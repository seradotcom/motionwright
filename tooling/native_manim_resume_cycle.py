#!/usr/bin/env python3
"""Run only edited and reopen-edited from authenticated committed-source copies.

This is a separate two-phase continuation. It never calls source-cycle-edit and
does not stand in for the historical four-phase verifier. The independent host
must still join the two old originals and the two new originals, including all
96 decoded frames, before accepting the full physical lineage.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal

from native_manim_camera_evidence import private_bytes, private_json, read, read_object, require, sha
from native_manim_editable_cycle import (
    EVIDENCE_NAME, FONT_SHA, LICENSE_SHA, ORIGIN_SHA, binding, checked_native_link,
    checked_seed, retained_source, stamp, verify_phase as verify_original_phase_custody,
)
from native_manim_resume_transfer import (
    INPUTS, MANIFEST, ORIGIN, SOURCE_RECOVERY, hosted_context, manifest, paths,
)

PHASES = ("edited", "reopen-edited")
SHORT_PHASES = ("r3", "r4")


def owned_root(current):
    # Keep Broker UNIX socket paths short and make a killed wrapper's owner
    # discoverable for the outer's one failure snapshot, without a directory scan.
    return Path(os.environ["RUNNER_TEMP"]).resolve(strict=True) / ("mw-mr-" + current["run_id"])


def checked_recovery(value, report, export):
    require(value.get("schema") == "motionwright.manim.editable.committed-source-recovery/1"
            and value.get("status") == "SOURCE_COMMITTED_RECOVERED"
            and value.get("scope") == "SOURCE_ONLY_NO_NATIVE_AUTHORITY"
            and sha(read(report)) == SOURCE_RECOVERY["report_sha256"]
            and read_object(report) == value,
            "resume_original_committed_source_receipt")
    require(all(value.get(key) is True for key in (
        "source_assets_original_bytes_verified", "prior_events_and_native_receipts_unchanged",
        "fresh_source_export_file_readback", "typed_commit_lookup_performed"))
        and all(value.get(key) is False for key in (
            "original_edit_report_recreated", "original_cas_identity_claimed", "source_mutation_performed",
            "provider_dispatch_performed", "native_execution_performed", "native_bundle_reopen_verified",
            "inherited_native_results_revalidated", "human_acceptance", "automatic_retry"))
        and type(value.get("project_changes_applied")) is int and value["project_changes_applied"] == 0
        and value.get("working_cas_provenance") == "CAS_REHYDRATED_NEW_IDENTITY"
        and value.get("working_cas_blobs_published") == 4
        and len(value.get("committed_bindings", [])) == 3,
        "resume_recovery_is_lookup_and_new_cas_only")
    expected_inputs = dict(zip(("before_database", "current_database", "original_bundle", "edited_bundle", "intent"),
                              (INPUTS[name][1] for name in INPUTS)))
    require(value.get("input_sha256") == expected_inputs,
            "resume_source_recovery_uses_five_originals")
    raw = read(export, 4 * 1024 * 1024)
    require(len(raw) == value.get("current_export_bytes") == SOURCE_RECOVERY["export_bytes"]
            and sha(raw) == value.get("current_export_sha256") == SOURCE_RECOVERY["export_sha256"],
            "resume_saved_export_equals_host_recovered_original")
    selected = binding(value.get("prepared_plan", {}).get("binding", {}))
    require(value.get("current_stamp") == stamp(selected)
            and value.get("original_stamp", {}).get("revision") + 3 == selected["revision"],
            "resume_three_committed_changes_only")
    return selected


def admitted_originals(current, *, frozen=False):
    from native_manim_resume_transfer import RECOVERY_SCENARIO, RESUME_SCENARIO, selected_scenario
    require(selected_scenario() == (RECOVERY_SCENARIO if frozen else RESUME_SCENARIO),
            "resume_input_runtime_mode_must_be_explicit")
    if frozen:
        from native_manim_recovery_gate import check_source_gate, check_runtime
    else:
        from native_manim_resume_runtime import check as check_runtime
        from native_manim_resume_focals import check_source_gate
    gate, _ = check_source_gate(current)
    require(check_runtime(current) == gate["runtime"], "resume_input_runtime_identity_changed")
    scope = paths(current)["inputs"]
    intake = read_object(scope / "intake-result.original.json")
    require(intake.get("schema") == "motionwright.manim.resume.input-intake/1"
            and intake.get("status") == "SOURCE_INPUTS_ONLY" and intake.get("context") == current
            and intake.get("origin") == ORIGIN and intake.get("original_file_count") == 5
            and intake.get("project_changes_applied") == 0 and intake.get("native_effect_invocations") == 0
            and intake.get("historical_private_key_received") is False
            and intake.get("automatic_retry") is False, "resume_original_input_intake_required")
    root = scope / "originals"
    require({item.name for item in root.iterdir()} == set(INPUTS) | {MANIFEST},
            "resume_original_input_namespace")
    from ci_private_evidence import json_bytes
    manifest_raw = read(root / MANIFEST, 16384)
    require(manifest_raw == json_bytes(manifest(current, intake["recipient_sha256"]))
            and sha(manifest_raw) == intake.get("manifest_sha256") == intake.get("input_contract_sha256"),
            "resume_input_manifest_changed")
    files = {}
    for name, (size, digest) in INPUTS.items():
        raw = read(root / name, size)
        require(len(raw) == size and sha(raw) == digest, "resume_input_original_changed:" + name)
        files[name] = raw
    return files, manifest_raw, intake


def new_phase_inputs(context, evidence, database, output_root, env, *, capture, source_binary):
    phase, history = context["phase"], context["history"]
    require(phase in PHASES and len(history) == PHASES.index(phase)
            and context["resume_only"] is True, "resume_exact_two_phase_order")
    descriptor, input_path = None, None
    if not history:
        files, manifest_raw, intake = admitted_originals(context["current"])
        owner = database.parent.parent
        references, before_root = owner / "resume-source-originals", owner / "resume-before"
        references.mkdir(mode=0o700)
        before_root.mkdir(mode=0o700)
        for name, raw in files.items():
            private_bytes(references / name, raw)
        private_bytes(references / MANIFEST, manifest_raw)
        private_json(evidence / "source-input-intake.original.json", intake)
        before_database = before_root / "motionwright.sqlite3"
        private_bytes(before_database, files["reopen-original-reference.sqlite3"])
        private_bytes(database, files["edited-original.sqlite3"])
        report = evidence / "committed-source-recovery.json"
        export = report.with_suffix(".current.mwedit.zip")
        recovered = capture([
            str(source_binary), "source-cycle-recover-edit-default-cas", str(before_database),
            str(database), str(references / "original-source.mwedit.zip"),
            str(references / "edited-source.mwedit.zip"),
            str(references / "source-edit-intent.original.json"), str(report),
        ], env=env, evidence=evidence, label="committed-source-recovery", timeout=120)
        selected = checked_recovery(recovered, report, export)
        # These are the original public fixture sidecars already embedded in the
        # pinned Rust executable, copied by digest; no additional private input.
        fixture = Path(__file__).resolve().parents[1] / "crates/manim-profile/tests/fixtures"
        for suffix, expected in (("Montserrat-OFL.txt", LICENSE_SHA), ("Montserrat-source.json", ORIGIN_SHA)):
            raw = read(fixture / suffix, 64 * 1024)
            require(sha(raw) == expected, "resume_original_font_license_or_origin_changed")
            private_bytes(database.with_suffix("." + suffix), raw)
        seed = {"schema": "motionwright.manim.editable.recovered-source-selection/1", **selected,
                "resource": stamp(selected)["resource"], "primitive_count": 3, "frame_count": 24,
                "font_sha256": FONT_SHA, "font_license_sha256": LICENSE_SHA,
                "native_execution_performed": False, "project_changes_applied": 0,
                "recovery_receipt_sha256": sha(read(report))}
    else:
        previous = history[0]
        require(database == previous["database"], "resume_same_recovered_store_required")
        report = previous["prepared"]["recovery_path"]
        before_database = previous["prepared"]["before_database"]
        references = previous["prepared"]["references"]
        export = report.with_suffix(".current.mwedit.zip")
        checked_recovery(read_object(report), report, export)
        seed = previous["seed"]
        descriptor = {key: previous["source"]["descriptor"][key] for key in ("sha256", "bytes")}
        descriptor.update(root="output", relative_path="source-input.mwedit.zip")
        raw = read(previous["source"]["bundle_path"], 4 * 1024 * 1024)
        require(sha(raw) == descriptor["sha256"] == SOURCE_RECOVERY["export_sha256"]
                and len(raw) == descriptor["bytes"] == SOURCE_RECOVERY["export_bytes"],
                "resume_reopen_uses_first_native_original_export")
        private_bytes(output_root / descriptor["relative_path"], raw)
        require(read(output_root / descriptor["relative_path"], 4 * 1024 * 1024) == raw,
                "resume_original_bundle_mount_copy")
        input_path = evidence / "original-source-input.json"
        private_json(input_path, descriptor)
    for name, (size, digest) in INPUTS.items():
        require(sha(read(references / name, size)) == digest, "resume_references_changed")
    private_json(evidence / "source-seed.json", seed)
    return {"binding": checked_seed(seed), "seed": seed, "edit_path": None,
            "input_path": input_path, "input_descriptor": descriptor,
            "is_reopen": phase == "reopen-edited", "recovery_path": report,
            "before_database": before_database, "references": references}


def checked_readback(value, report, recovered, current, phase):
    require(value.get("schema") == "motionwright.manim.editable.recovered-source-readback/1"
            and value.get("status") == "SOURCE_READBACK_REQUIRES_NATIVE_CUSTODY"
            and value.get("canonical_stamp") == stamp(current["prepared"]["binding"])
            and value.get("recovery_receipt_sha256") == sha(read(report))
            and value.get("committed_bindings") == recovered["committed_bindings"]
            and value.get("additional_native_receipts") == (2 if phase == "edited" else 4)
            and value.get("source_sha256") == SOURCE_RECOVERY["export_sha256"]
            and value.get("source_closure") == current["source"]["descriptor"]["closure"],
            "resume_fresh_readback_source_and_original_receipts")
    require(all(value.get(key) is True for key in (
        "typed_commit_lookup_performed", "fresh_sqlite_readback", "source_assets_original_bytes_verified",
        "saved_export_matches_native_carrier", "original_native_receipts_unchanged",
        "production_receipts_unchanged_during_readback", "project_events_unchanged"))
        and all(value.get(key) is False for key in (
            "original_edit_report_recreated", "native_execution_performed", "provider_dispatch_performed",
            "native_bundle_reopen_verified", "human_acceptance", "automatic_retry"))
        and type(value.get("project_changes_applied")) is int and value["project_changes_applied"] == 0,
        "resume_readback_scope_is_observation_only")
    checked_native_link(value.get("current_native"), current)


def finish_phase(context, prepared, native, evidence, output_root, work_root, database,
                 env, *, capture, source_binary, python, runtime, tooling, launch):
    require(read_object(evidence / EVIDENCE_NAME) == native, "resume_original_native_stdout")
    source = retained_source(native, prepared, evidence, output_root, work_root, database)
    recovered = read_object(prepared["recovery_path"])
    require(source["plan"] == recovered["prepared_plan"]
            and source["descriptor"]["sha256"] == SOURCE_RECOVERY["export_sha256"],
            "resume_native_carrier_is_saved_committed_source")
    current = {"native": native, "native_path": evidence / EVIDENCE_NAME,
               "source": source, "prepared": prepared, "evidence": evidence}
    readback_path = evidence / "recovered-source-readback.json"
    value = capture([
        str(source_binary), "source-cycle-readback-recovered", str(prepared["before_database"]),
        str(database), str(prepared["recovery_path"]), str(current["native_path"]),
        str(output_root), str(readback_path),
    ], env=env, evidence=evidence, label="recovered-source-readback", timeout=120)
    require(read_object(readback_path) == value, "resume_readback_original_stdout")
    checked_readback(value, prepared["recovery_path"], recovered, current, context["phase"])
    pair = None
    if context["phase"] == "reopen-edited":
        previous = context["history"][0]
        verify_phase(previous, context["current"])
        pair_path = evidence / "resumed-edited-pixels.json"
        pixel_env = dict(env, PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                         PYTHONPATH=str(runtime / "site-packages") + os.pathsep + str(tooling))
        pair = capture([str(python), "-B", str(tooling / "native_manim_resume_pixels.py"),
                        "edited-pair", str(previous["evidence"]), str(evidence),
                        str(prepared["recovery_path"]), str(pair_path)],
                       env=pixel_env, evidence=evidence, label="resumed-edited-pixels", timeout=120)
        require(read_object(pair_path) == pair and pair.get("status") == "EDITED_PAIR_REQUIRES_CUSTODY"
                and pair.get("frame_count_per_phase") == 24
                and pair.get("all_edited_reopen_frames_equal") is True
                and pair.get("old_two_phase_lineage_verified") is False,
                "resume_original_edited_pair_pixels_incomplete")
    observation = {
        "schema": "motionwright.manim.editable.resumed-native-phase/1",
        "status": "PASS_REQUIRES_SEALED_CUSTODY", "phase": context["phase"],
        "context": context["current"], "origin_context": ORIGIN, "binding": prepared["binding"],
        "native_evidence_sha256": sha(read(evidence / EVIDENCE_NAME)),
        "source_retention_sha256": sha(read(evidence / "editable-source-retention.json")),
        "native_video_admission_sha256": sha(read(evidence / "native-video-admission.json")),
        "recovery_receipt_sha256": sha(read(prepared["recovery_path"])),
        "recovered_readback_sha256": sha(read(readback_path)),
        "native_request_id": native["request_id"], "native_job_ref": native["result"]["job_ref"],
        "source_bundle_input": prepared["input_descriptor"], "launch": launch,
        "native_effect_invocations": 1, "native_result_original_retained": True,
        "source_edit_performed": False, "project_changes_applied": 0,
        "original_edit_report_recreated": False, "original_committed_changes_recovered": 3,
        "resumed_pixel_pair_sha256": sha(read(evidence / "resumed-edited-pixels.json")) if pair else None,
        "old_two_phase_lineage_verified": False, "human_acceptance": False, "automatic_retry": False,
    }
    private_json(evidence / "result.json", observation)
    return {"source": source, "phase_observation": observation, "pixel_report": pair}


def verify_phase(phase, current):
    custody = verify_original_phase_custody(phase, current)
    value = phase["phase_observation"]
    require(value.get("schema") == "motionwright.manim.editable.resumed-native-phase/1"
            and value.get("origin_context") == ORIGIN and value.get("phase") in PHASES
            and value.get("source_edit_performed") is False
            and value.get("original_edit_report_recreated") is False
            and value.get("old_two_phase_lineage_verified") is False
            and value.get("project_changes_applied") == 0
            and value.get("recovery_receipt_sha256") == SOURCE_RECOVERY["report_sha256"]
            and value.get("recovered_readback_sha256") == sha(read(
                phase["evidence"] / "recovered-source-readback.json")),
            "resume_phase_cannot_promote_old_lineage")
    return custody


def run_resume(args, *, run_phase, validate_phase):
    from native_manim_editable_outer import OuterDeadline
    deadline = args.outer_deadline
    current = hosted_context()
    require(isinstance(deadline, OuterDeadline) and deadline.context == current,
            "resume_original_outer_deadline")
    deadline.admit_work()
    require("GH_TOKEN" not in os.environ and "GITHUB_TOKEN" not in os.environ,
            "resume_native_owner_must_not_inherit_transfer_credentials")
    root, sealed = args.evidence_root, args.sealed_cycle_root
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(root == temporary / ("manim-resume-private-" + current["source_sha"])
            and sealed == temporary / ("manim-resume-sealed-" + current["source_sha"]),
            "resume_closed_evidence_scopes")
    recipient, observed = validate_phase(argparse.Namespace(evidence_root=root, sealed_originals=sealed))
    require(observed == current, "resume_native_context_changed")
    admitted_originals(current)
    root.mkdir(mode=0o700)
    sealed.mkdir(mode=0o700)
    owner = owned_root(current)
    owner.mkdir(mode=0o700, exist_ok=False)
    private_json(root / "resume-intent.json", {
        "schema": "motionwright.manim.editable.resume-intent/1", "context": current,
        "origin_context": ORIGIN, "phases": list(PHASES), "native_effect_invocations": 2,
        "source_recovery": SOURCE_RECOVERY, "outer_origin": deadline.binding(),
        "project_changes_authorized": 0, "original_edit_report_recreated": False,
        "old_two_phase_lineage_verified": False, "next_phase_after_failure": False,
        "automatic_retry": False})
    history = []
    for phase, short in zip(PHASES, SHORT_PHASES):
        deadline.admit_work()
        selected = argparse.Namespace(scenario="editable-source-resume", evidence_root=root / phase,
                                      sealed_originals=sealed / (phase + ".mwseal"))
        checked_recipient, checked_current = validate_phase(selected)
        require(checked_recipient == recipient and checked_current == current, "resume_phase_preflight")
        context = {"phase": phase, "short_phase": short, "history": history, "resume_only": True,
                   "outer_deadline": deadline, "current": current,
                   "owner": history[0]["owner"] if history else owner,
                   "database": history[0]["database"] if history else None}
        observed = run_phase(selected, resume_cycle=context)
        deadline.admit_work()
        observed["sealed_custody"] = verify_phase(observed, current)
        require(observed["phase_observation"]["phase"] == phase, "resume_phase_identity")
        if history:
            previous = history[0]
            require(observed["owner"] == previous["owner"] and observed["database"] == previous["database"]
                    and observed["native"]["request_id"] != previous["native"]["request_id"]
                    and observed["native"]["result"]["job_ref"] != previous["native"]["result"]["job_ref"]
                    and observed["launch"]["session_sha256"] != previous["launch"]["session_sha256"],
                    "resume_original_store_and_fresh_host_session")
        history.append(observed)
    require(history[-1]["pixel_report"] is not None, "resume_edited_pair_pixels_required")
    result = {"schema": "motionwright.manim.editable.resumed-two-phase-observation/1",
              "status": "TWO_NATIVE_PHASES_RECOVERED_LINEAGE_PENDING", "context": current,
              "origin_context": ORIGIN, "native_effect_invocations": 2,
              "phase_results": [{"phase": row["phase_observation"]["phase"],
                                  "result_sha256": sha(read(row["evidence"] / "result.json")),
                                  "ciphertext_sha256": sha(read(row["sealed_originals"], 256 * 1024 * 1024))}
                                 for row in history],
              "source_recovery": SOURCE_RECOVERY, "same_recovered_store": True,
              "fresh_process_and_session_per_phase": True,
              "edited_bundle_byte_preserving_native_reopen": True,
              "all_24_edited_reopen_frames_equal": True,
              "pixel_pair_sha256": sha(read(history[-1]["evidence"] / "resumed-edited-pixels.json")),
              "project_changes_applied": 0, "original_edit_report_recreated": False,
              "old_two_phase_lineage_verified": False, "four_phase_acceptance": "PENDING",
              "formal_G2_granted": False, "human_acceptance": False, "automatic_retry": False}
    private_json(root / "resume-result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--sealed-cycle-root", type=Path, required=True)
    args = parser.parse_args()
    import native_manim_e2e as native
    from native_manim_editable_outer import load_deadline
    deadline = native._EDITABLE_OUTER_DEADLINE = load_deadline()
    args.outer_deadline = deadline
    signal.signal(signal.SIGTERM, native.editable_cancelled)
    signal.signal(signal.SIGINT, native.editable_cancelled)
    try:
        native._EDITABLE_STAGE = "resume-exact-inputs-before-phase-custody"
        hosted_context()
        deadline.admit_work()
        native.exact_inputs()
        native._EDITABLE_STAGE = "explicit-two-phase-resume"
        result = run_resume(args, run_phase=native._run_single, validate_phase=native.editable_options)
        print(json.dumps({"status": result["status"], "context": result["context"],
                          "native_effect_invocations": 2, "four_phase_acceptance": "PENDING"}))
    except BaseException as error:
        native._retain_editable_error(error)
        raise


if __name__ == "__main__":
    main()
