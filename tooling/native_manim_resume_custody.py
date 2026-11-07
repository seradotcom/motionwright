#!/usr/bin/env python3
"""Original outer custody for the explicit two-phase Manim continuation."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal

import ci_private_evidence as engine
import native_manim_editable_outer as outer
from native_manim_camera_evidence import read, read_object, require, sha
from native_manim_resume_transfer import ORIGIN, SOURCE_RECOVERY, hosted_context, paths


def scopes(current):
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    suffix = current["source_sha"]
    return (temporary / ("manim-resume-private-" + suffix),
            temporary / ("manim-resume-sealed-" + suffix),
            temporary / ("manim-resume-public-" + suffix))


def wrapper_argv(current):
    root, sealed, _ = scopes(current)
    temporary = root.parent
    repo = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    command = [str(temporary / "manim-custody-venv/bin/python"), "-B",
            str(repo / "tooling/native_manim_resume_cycle.py"),
            "--evidence-root", str(root), "--sealed-cycle-root", str(sealed)]
    from native_manim_resume_resources import expanded_argv
    return expanded_argv("native-wrapper", command)


def outer_inputs(current):
    root, _, _ = scopes(current)
    control = outer.control_root(root.parent, current)
    launch = control / "work/native-wrapper"
    runtime = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) / "verification/native-manim-e2e"
    provision = root.parent / "manim-runtime-provision"
    return {"resume-intent": root / "resume-intent.json", "resume-result": root / "resume-result.json",
            "edited-custody": root / "edited/editable-custody.json",
            "reopen-edited-custody": root / "reopen-edited/editable-custody.json",
            "wrapper-stdout": root.parent / "manim-editable-cycle-wrapper.log",
            "wrapper-stderr": root.parent / "manim-editable-cycle-wrapper.stderr.log",
            "outer-origin": control / "origin.json", "wrapper-intent": launch / "intent.json",
            "resource-origin": control / "resource-origin.json",
            "runtime-inventory": runtime / "runtime-inventory.json",
            "runtime-match": runtime / "resume-runtime-match.json",
            "runtime-pip-install": provision / "pip-install.json",
            "runtime-install-stdout": provision / "install.stdout.log",
            "runtime-install-stderr": provision / "install.stderr.log",
            "wrapper-requested": launch / "launch-requested.json", "wrapper-start": launch / "start.json",
            "wrapper-terminal": launch / "terminal.json"}


def verify_launcher(current):
    inputs = outer_inputs(current)
    origin_raw = read(inputs["outer-origin"], 65536)
    origin = json.loads(origin_raw)
    require(origin.get("context") == current, "resume_launcher_source_changed")
    root = outer.control_root(inputs["outer-origin"].parent.parent, current)
    deadline = outer.OuterDeadline(origin, current, origin.get("boot_id"),
                                   origin_sha256=sha(origin_raw), root=root)
    intent_raw = read(inputs["wrapper-intent"], 65536)
    intent, requested, start, terminal = (read_object(inputs[name]) for name in (
        "wrapper-intent", "wrapper-requested", "wrapper-start", "wrapper-terminal"))
    require(intent.get("schema") == "motionwright.manim.editable.outer-launch-intent/1"
            and intent.get("label") == "native-wrapper" and intent.get("context") == current
            and intent.get("origin") == deadline.binding() and intent.get("automatic_retry") is False
            and intent.get("execution") == "NOT_STARTED" and intent.get("launch_requested") is False
            and intent.get("argv") == wrapper_argv(current)
            and intent.get("cwd") == str(Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)),
            "resume_original_launcher_intent")
    require(requested.get("intent_sha256") == sha(intent_raw) and requested.get("automatic_retry") is False
            and type(requested.get("requested_monotonic_ns")) is int,
            "resume_original_launcher_request")
    require(start.get("schema") == "motionwright.manim.editable.outer-launch-start/1"
            and start.get("context") == current and start.get("origin") == deadline.binding()
            and start.get("intent_sha256") == sha(intent_raw)
            and type(start.get("pid")) is int and start["pid"] > 0
            and type(start.get("started_monotonic_ns")) is int
            and requested["requested_monotonic_ns"] <= start["started_monotonic_ns"],
            "resume_original_launcher_start")
    require(terminal.get("schema") == "motionwright.manim.editable.outer-launch-terminal/1"
            and terminal.get("context") == current and terminal.get("origin") == deadline.binding()
            and terminal.get("intent_sha256") == sha(intent_raw) and terminal.get("pid") == start["pid"]
            and terminal.get("status") == "EXITED" and terminal.get("process_exit_known") is True
            and type(terminal.get("original_exit")) is int and terminal["original_exit"] == 0
            and terminal.get("termination_requested") is False and terminal.get("launch_requested") is True
            and terminal.get("original_error") is None and terminal.get("secondary_errors") == []
            and terminal.get("native_result_claim") == "NOT_ASSESSED" and terminal.get("automatic_retry") is False
            and terminal.get("started_monotonic_ns") == start["started_monotonic_ns"]
            and type(terminal.get("finished_monotonic_ns")) is int
            and origin["origin_monotonic_ns"] <= start["started_monotonic_ns"] <= terminal["finished_monotonic_ns"]
                <= origin["work_deadline_ns"], "resume_launcher_not_original_known_success")
    for name in ("stdout", "stderr"):
        raw = read(inputs["wrapper-" + name], outer.STREAM_LIMIT)
        require(terminal.get("streams", {}).get(name) == {"bytes": len(raw), "sha256": sha(raw)},
                "resume_original_launcher_stream_changed")
    require(read_object(inputs["resume-intent"]).get("outer_origin") == deadline.binding(),
            "resume_same_outer_clock_required")
    return terminal


def output_recipient():
    recipient = {"spki_der_base64": os.environ["PRIVATE_EVIDENCE_RECIPIENT_SPKI"],
                 "sha256": os.environ["PRIVATE_EVIDENCE_RECIPIENT_SHA256"]}
    engine.public_key(recipient)
    return recipient


def seal_outer(current):
    root, sealed, _ = scopes(current)
    recipient = output_recipient()
    sealed.mkdir(mode=0o700, exist_ok=True)
    stage = root.parent / ("manim-resume-outer-" + current["source_sha"])
    stage.mkdir(mode=0o700, exist_ok=False)
    roots, selected, failed = {}, {}, []
    for label, source in outer_inputs(current).items():
        folder = roots[label] = stage / label
        try:
            raw = read(source, 4 * 1024 * 1024)
            folder.mkdir(mode=0o700)
            engine.new_bytes(folder / "original", raw)
            selected[label] = {"bytes": len(raw), "sha256": sha(raw)}
        except Exception:
            failed.append(label)
            if folder.exists() or folder.is_symlink():
                roots[label + "-partial"] = folder
            roots[label] = stage / (label + "-unavailable")
    try:
        verify_launcher(current)
    except Exception:
        failed.append("wrapper-terminal-validation")
    transfer = paths(current)
    roots.update({"outer-workflow": outer.control_root(root.parent, current) / "work",
                  "outer-resources": outer.control_root(root.parent, current) / "resources",
                  "resume-inputs": transfer["inputs"], "resume-recipient": transfer["public"],
                  "resume-source-gate": root.parent / "manim-resume-source-gate"})
    from native_manim_resume_cycle import owned_root
    if failed and owned_root(current).exists():
        # A cgroup OOM may kill the inner Python custodian. Retain its known
        # owner exactly once in this outer failure envelope, without retry.
        roots["failed-native-owner"] = owned_root(current)
    require(all(path != transfer["key"] and path not in transfer["key"].parents
                and transfer["key"] not in path.parents for path in roots.values()),
            "resume_ephemeral_private_key_must_never_be_selected")
    plaintext, complete = engine.gather_snapshot(roots, preserve_partial=True)
    envelope = engine.encrypt_snapshot(plaintext, recipient, current)
    engine.new_bytes(sealed / "resume-outer.mwseal", envelope)
    value = {"schema": "motionwright.manim.editable.resume-outer-custody-public/1",
             "context": current, "origin_context": ORIGIN, "recipient_sha256": recipient["sha256"],
             "ciphertext_sha256": sha(envelope), "ciphertext_bytes": len(envelope),
             "selected_originals_complete": complete and not failed and len(selected) == len(outer_inputs(current)),
             "original_files": selected, "failed_labels": failed,
             "native_effect_invocations": 0, "automatic_retry": False}
    engine.new_json(sealed / "resume-outer.public.json", value)
    return value


def verify_outer(current):
    _, sealed, _ = scopes(current)
    value = read_object(sealed / "resume-outer.public.json")
    require(set(value) == {"schema", "context", "origin_context", "recipient_sha256", "ciphertext_sha256",
            "ciphertext_bytes", "selected_originals_complete", "original_files", "failed_labels",
            "native_effect_invocations", "automatic_retry"}
            and value["schema"] == "motionwright.manim.editable.resume-outer-custody-public/1"
            and value["context"] == current and value["origin_context"] == ORIGIN
            and value["recipient_sha256"] == output_recipient()["sha256"]
            and value["selected_originals_complete"] is True and value["failed_labels"] == []
            and type(value["native_effect_invocations"]) is int and value["native_effect_invocations"] == 0
            and value["automatic_retry"] is False, "resume_complete_outer_originals_required")
    inputs = outer_inputs(current)
    require(type(value["original_files"]) is dict and set(value["original_files"]) == set(inputs),
            "resume_outer_closed_selection")
    for label, source in inputs.items():
        raw = read(source, 4 * 1024 * 1024)
        require(value["original_files"][label] == {"bytes": len(raw), "sha256": sha(raw)},
                "resume_outer_original_changed")
    verify_launcher(current)
    ciphertext = read(sealed / "resume-outer.mwseal", 64 * 1024 * 1024)
    require(ciphertext.startswith(engine.MAGIC) and len(ciphertext) == value["ciphertext_bytes"]
            and sha(ciphertext) == value["ciphertext_sha256"], "resume_outer_ciphertext_changed")
    return value


def verify_resume(current):
    from native_manim_resume_cycle import PHASES, admitted_originals, verify_phase
    from native_manim_resume_focals import check_source_gate
    from native_manim_resume_runtime import check as check_runtime
    root, sealed, public = scopes(current)
    gate, gate_sha = check_source_gate(current)
    require(gate["runtime"] == check_runtime(current), "resume_runtime_unchanged_through_custody")
    admitted_originals(current)
    outer_receipt = verify_outer(current)
    phases = []
    for name in PHASES:
        evidence = root / name
        observation = read_object(evidence / "result.json")
        require(observation.get("phase") == name and observation.get("context") == current,
                "resume_verified_phase_order_context")
        verify_phase({"current": current, "evidence": evidence, "phase_observation": observation,
                      "native_path": evidence / "motionwright-manim-evidence.json",
                      "sealed_originals": sealed / (name + ".mwseal")}, current)
        identities = read_object(evidence / "native-input-identities.json")
        require(identities.get("motionwright_sha") == current["source_sha"]
                and identities.get("semwright_sha") == current["semwright_sha"]
                and identities.get("binaries", {}).get("native_example") == gate["compiled_binary_after"]["sha256"],
                "resume_native_used_the_tested_compiled_source")
        phases.append({"phase": name, "result_sha256": sha(read(evidence / "result.json")),
                       "ciphertext_sha256": sha(read(sealed / (name + ".mwseal"), 256 * 1024 * 1024))})
    result = read_object(root / "resume-result.json")
    require(result.get("schema") == "motionwright.manim.editable.resumed-two-phase-observation/1"
            and result.get("status") == "TWO_NATIVE_PHASES_RECOVERED_LINEAGE_PENDING"
            and result.get("context") == current and result.get("origin_context") == ORIGIN
            and result.get("phase_results") == phases and result.get("native_effect_invocations") == 2
            and result.get("source_recovery") == SOURCE_RECOVERY
            and result.get("old_two_phase_lineage_verified") is False
            and result.get("four_phase_acceptance") == "PENDING" and result.get("formal_G2_granted") is False
            and result.get("project_changes_applied") == 0 and result.get("original_edit_report_recreated") is False
            and result.get("automatic_retry") is False,
            "resume_cannot_replace_original_four_phase_admission")
    public.mkdir(mode=0o700, exist_ok=False)
    summary = {"schema": "motionwright.manim.editable.resume-public-summary/1",
               "status": result["status"], "context": current, "origin_context": ORIGIN,
               "native_effect_invocations": 2, "project_changes_applied": 0,
               "source_gate_sha256": gate_sha, "actual_rust_focals_passed": 8,
               "resume_result_sha256": sha(read(root / "resume-result.json")),
               "outer_ciphertext_sha256": outer_receipt["ciphertext_sha256"],
               "phase_results": phases, "old_two_phase_lineage_verified": False,
               "four_phase_acceptance": "PENDING", "formal_G2_granted": False,
               "human_acceptance": False, "automatic_retry": False}
    engine.new_json(public / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("launch", "seal", "verify"))
    args = parser.parse_args()
    current = hosted_context()
    deadline = outer.load_deadline()
    if args.command == "launch":
        deadline.admit_work()
        from native_manim_resume_resources import admitted_group
        admitted_group(deadline)
        result = outer.run_command(deadline, "native-wrapper", wrapper_argv(current),
                                   Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True))
    elif args.command == "seal":
        deadline.remaining("seal")
        result = seal_outer(current)
    else:
        deadline.remaining("seal")
        result = verify_resume(current)
    print(json.dumps({"status": result.get("status", "ORIGINALS_RETAINED"), "automatic_retry": False}))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, outer.workflow_cancelled)
    signal.signal(signal.SIGINT, outer.workflow_cancelled)
    main()
