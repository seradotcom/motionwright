#!/usr/bin/env python3
"""Host verification of four new physical phases in one retained runtime.

The two historical Native references and their failed outer are preserved; this
reader never recomputes them or asserts runtime equivalence. No Store is opened,
no source change is applied, and no video is decoded or rendered on this host.
"""
from __future__ import annotations
import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import zipfile
from cryptography.hazmat.primitives import serialization
import ci_private_evidence as engine
from native_manim_camera_evidence import read, read_object, require, sha, object_bytes
from native_manim_editable_cycle import EVIDENCE_NAME, binding, stamp
from native_manim_recovery_cycle import (PHASES, SHORT_PHASES, checked_reference_readback,
                                         verify_phase as verify_recovery_phase)
from native_manim_recovery_gate import check_source_gate
from native_manim_resume_cycle import checked_readback, checked_recovery
from native_manim_resume_host import bounded, get, private_root, save
from native_manim_resume_lineage import (Snapshot, RECIPIENT_SHA, MAX_CAPSULE, MAX_ARTIFACT,
    OLD_CIPHERS, identity, same, context, completed_run, outer_original, checked_custody, phase_source)
from native_manim_resume_pixels import four_phase_pixels
from native_manim_resume_transfer import (INPUTS, JOB, ORIGIN, RECOVERY_SCENARIO, REPOSITORY,
    MANIFEST, SOURCE_RECOVERY, timestamp, validate_release, validate_recipient, manifest)

NEW_FILES = {name + ".mwseal" for name in PHASES} | {"resume-outer.mwseal", "resume-outer.public.json"}

def intake(args):
    current = context(args.source_sha, args.run_id)
    private_root(args.private)
    require(shutil.disk_usage(args.private).free >= 1024 * 1024 * 1024, "resume_lineage_disk_reserve")
    root = args.private / ("recovery-runtime-lineage-" + current["run_id"] + "-" + current["source_sha"][:12])
    root.mkdir(mode=0o700, exist_ok=False)
    save(root, "intake-intent.original.json", {"context": current, "one_artifact_only": True,
         "private_key_accessed": False, "native_effect_invocations": 0, "automatic_retry": False})
    prefix = "/repos/" + REPOSITORY
    run_raw = get(root, "completed-run", args.gh, prefix + "/actions/runs/" + current["run_id"])
    completed_run(engine.strict(run_raw), current)
    jobs_raw = get(root, "completed-jobs", args.gh,
                   prefix + "/actions/runs/" + current["run_id"] + "/jobs?filter=all&per_page=100")
    jobs = engine.strict(jobs_raw)
    require(type(jobs.get("total_count")) is int and jobs["total_count"] <= 100, "resume_lineage_job_budget")
    selected = [row for row in jobs.get("jobs", []) if row.get("name") == JOB]
    require(len(selected) == 1 and selected[0].get("status") == "completed"
            and selected[0].get("conclusion") == "success", "resume_lineage_completed_resume_job")
    artifacts_raw = get(root, "completed-artifacts", args.gh,
                        prefix + "/actions/runs/" + current["run_id"] + "/artifacts?per_page=100")
    artifacts = engine.strict(artifacts_raw)
    require(type(artifacts.get("total_count")) is int and artifacts["total_count"] <= 100,
            "resume_lineage_artifact_roster_budget")
    name = "motionwright-manim-recovery-originals-" + current["source_sha"] + "-" + current["run_id"] + "-a1"
    selected = [row for row in artifacts.get("artifacts", []) if row.get("name") == name]
    require(len(selected) == 1, "resume_lineage_unique_ciphertext_artifact")
    row = selected[0]
    require(type(row.get("id")) is int and row["id"] > 0 and row.get("expired") is False
            and type(row.get("size_in_bytes")) is int and 0 < row["size_in_bytes"] <= MAX_ARTIFACT
            and re.fullmatch(r"sha256:[0-9a-f]{64}", row.get("digest", ""))
            and row.get("workflow_run", {}).get("id") == int(current["run_id"])
            and row.get("workflow_run", {}).get("head_sha") == current["source_sha"],
            "resume_lineage_artifact_api_identity")
    archive_raw = get(root, "originals-artifact", args.gh,
                      prefix + "/actions/artifacts/" + str(row["id"]) + "/zip", limit=MAX_ARTIFACT, seconds=90)
    require(len(archive_raw) == row["size_in_bytes"] and sha(archive_raw) == row["digest"][7:],
            "resume_lineage_download_original_digest")
    destination = root / "ciphertexts"
    destination.mkdir(mode=0o700)
    retained = {}
    with zipfile.ZipFile(io.BytesIO(archive_raw)) as archive:
        members = archive.infolist()
        require(len(members) == len(NEW_FILES) and {member.filename for member in members} == NEW_FILES
                and not archive.comment and sum(member.file_size for member in members) <= MAX_ARTIFACT,
                "resume_lineage_closed_ciphertext_archive")
        for member in members:
            require(not member.is_dir() and not member.flag_bits & 1
                    and not stat.S_ISLNK(member.external_attr >> 16)
                    and 0 < member.file_size <= (65536 if member.filename.endswith(".json") else MAX_CAPSULE),
                    "resume_lineage_original_archive_entry")
            raw = archive.read(member)
            require(len(raw) == member.file_size, "resume_lineage_original_member_size")
            engine.new_bytes(destination / member.filename, raw)
            require(read(destination / member.filename, MAX_CAPSULE) == raw, "resume_lineage_physical_cipher_readback")
            retained[member.filename] = identity(raw)
    receipt = {"schema": "motionwright.manim.recovery-runtime.lineage-intake/1", "context": current,
               "status": "AUTHENTICATED_COMPLETED_RUN_CIPHERTEXTS_RETAINED",
               "artifact_id": row["id"], "artifact": identity(archive_raw), "files": retained,
               "completed_run_sha256": sha(run_raw), "completed_jobs_sha256": sha(jobs_raw),
               "artifact_list_sha256": sha(artifacts_raw), "private_key_accessed": False,
               "native_effect_invocations": 0, "automatic_retry": False}
    save(root, "intake-result.original.json", receipt)
    print(json.dumps({"status": receipt["status"], "root": str(root), "context": current,
                      "artifact_id": row["id"], "files": retained}))


def checked_reuse_originals(reuse, originals):
    for label in ("inspect-python", "install-wheels", "observe-native"):
        for kind in ("intent", "spawn", "terminal", "stdout", "stderr"):
            raw = originals["runtime-reuse-" + label + "-" + kind]
            expected = reuse["commands"][label][kind]
            require(identity(raw) == {key: expected[key] for key in ("bytes", "sha256")}
                    and (kind in ("stdout", "stderr") or raw.decode("utf-8") == expected["original_utf8"]),
                    "recovery_lineage_reuse_command_original_changed")
    for label, key in (("runtime-reuse-native-observation", "native_observation_original"),
                       ("runtime-reuse-inspect-python-stdout", "python_inspection_original"),
                       ("runtime-reuse-download-terminal", "download_terminal")):
        raw, expected = originals[label], reuse[key]
        require(identity(raw) == {name: expected[name] for name in ("bytes", "sha256")}
                and raw.decode("utf-8") == expected["original_utf8"],
                "recovery_lineage_reuse_current_observation_changed")
    require(identity(originals["runtime-installed-files-observed"]) == reuse["installed_manifest"],
            "recovery_lineage_reuse_current_installed_roster_changed")
    intent = object_bytes(originals["runtime-reuse-download-intent"])
    spawn = object_bytes(originals["runtime-reuse-download-spawn"])
    terminal = object_bytes(originals["runtime-reuse-download-terminal"])
    artifact = reuse["origin_artifact"]
    command = intent.get("command", [])
    require(type(command) is list and len(command) == 7 and type(command[0]) is str
            and PurePosixPath(command[0]).is_absolute()
            and command[1:] == ["api", "--hostname", "github.com", "--method", "GET",
                "/repos/" + REPOSITORY + "/actions/artifacts/" + str(artifact["artifact_id"]) + "/zip"]
            and intent.get("attempts_authorized") == 1 and intent.get("automatic_retry") is False
            and intent.get("maximum_stdout_bytes") == artifact["artifact_zip_bytes"]
            and type(spawn.get("pid")) is int and spawn["pid"] > 0
            and spawn.get("pid") == terminal.get("pid") and spawn.get("automatic_retry") is False,
            "recovery_lineage_reuse_original_get_identity")
    stderr = terminal["streams"]["stderr"]
    require(identity(originals["runtime-reuse-download-stderr"])
            == {"bytes": stderr["written_bytes"], "sha256": stderr["sha256"]},
            "recovery_lineage_reuse_download_stderr_changed")


def new_outer(snapshot, public, root, current):
    require(public.get("schema") == "motionwright.manim.recovery-runtime.outer-custody-public/1"
            and public.get("context") == current and public.get("origin_context") == ORIGIN
            and public.get("selected_originals_complete") is True and public.get("failed_labels") == []
            and public.get("automatic_retry") is False, "resume_lineage_new_outer_complete")
    originals = {}
    for label in public["original_files"]:
        require(re.fullmatch(r"[a-z][a-z-]*", label), "resume_lineage_original_label")
        originals[label] = outer_original(snapshot, public, label)
    origin = object_bytes(originals["outer-origin"])
    intent = object_bytes(originals["wrapper-intent"])
    requested = object_bytes(originals["wrapper-requested"])
    start = object_bytes(originals["wrapper-start"])
    terminal = object_bytes(originals["wrapper-terminal"])
    from native_manim_editable_outer import OuterDeadline
    clock = OuterDeadline(origin, current, origin.get("boot_id"),
                          origin_sha256=sha(originals["outer-origin"]), root=root)
    require(all(value.get("context") == current and value.get("origin") == clock.binding()
                and value.get("automatic_retry") is False for value in (intent, start, terminal)),
            "resume_lineage_one_outer_origin")
    require(intent.get("label") == terminal.get("label") == "native-wrapper"
            and intent.get("schema") == "motionwright.manim.editable.outer-launch-intent/1"
            and start.get("schema") == "motionwright.manim.editable.outer-launch-start/1"
            and terminal.get("schema") == "motionwright.manim.editable.outer-launch-terminal/1"
            and intent.get("execution") == "NOT_STARTED" and intent.get("launch_requested") is False
            and requested.get("intent_sha256") == start.get("intent_sha256")
                == terminal.get("intent_sha256") == sha(originals["wrapper-intent"])
            and requested.get("automatic_retry") is False
            and type(start.get("pid")) is int and start["pid"] > 0 and terminal.get("pid") == start["pid"]
            and terminal.get("status") == "EXITED" and terminal.get("process_exit_known") is True
            and type(terminal.get("original_exit")) is int and terminal["original_exit"] == 0
            and terminal.get("termination_requested") is False and terminal.get("launch_requested") is True
            and terminal.get("original_error") is None and terminal.get("secondary_errors") == []
            and terminal.get("native_result_claim") == "NOT_ASSESSED"
            and terminal.get("started_monotonic_ns") == start.get("started_monotonic_ns")
            and origin["origin_monotonic_ns"] <= requested["requested_monotonic_ns"]
                <= start["started_monotonic_ns"] <= terminal["finished_monotonic_ns"] <= origin["work_deadline_ns"],
            "resume_lineage_original_wrapper_zero_exit")
    for stream in ("stdout", "stderr"):
        require(terminal.get("streams", {}).get(stream) == identity(originals["wrapper-" + stream]),
                "resume_lineage_original_wrapper_stream")
    resource = object_bytes(snapshot.raw("outer-resources/native-wrapper/terminal.json"))
    before, after, entry = (resource.get(name, {}) for name in
                            ("resource_before", "resource_after", "original_cgroup_entry"))
    require(resource.get("status") == "EXITED_WITHIN_LIMITS" and resource.get("context") == current
            and resource.get("origin") == clock.binding() and resource.get("guard_pid") == start["pid"]
            and resource.get("original_child_exit_known") is True
            and type(resource.get("original_child_exit")) is int and resource["original_child_exit"] == 0
            and resource.get("memory_limit_bytes") == 1536 * 1024 * 1024
            and resource.get("physical_peak_budget_bytes") == 1536 * 1024 * 1024
            and resource.get("cgroup_max_bytes") == 1472 * 1024 * 1024
            and resource.get("cgroup_high_bytes") == 1408 * 1024 * 1024
            and resource.get("physical_reserve_bytes") == 512 * 1024 * 1024
            and resource.get("minimum_physical_available_bytes", 0) >= 512 * 1024 * 1024
            and resource.get("cgroup_quiescent") is True and resource.get("first_error") is None,
            "resume_lineage_native_cgroup_original_terminal")
    require(type(after.get("peak_bytes")) is int and 0 <= after["peak_bytes"] <= 1536 * 1024 * 1024
            and after.get("limit_bytes") == before.get("limit_bytes") == 1472 * 1024 * 1024
            and after.get("high_bytes") == before.get("high_bytes") == 1408 * 1024 * 1024
            and all(type(after.get(name)) is int and type(before.get(name)) is int
                    and after[name] == before[name] >= 0 for name in ("oom", "oom_kill"))
            and entry.get("schema") == "motionwright.manim.resume.original-cgroup-entry/1"
            and entry.get("context") == current and entry.get("origin") == clock.binding()
            and entry.get("label") == "native-wrapper" and entry.get("guard_pid") == start["pid"]
            and entry.get("controller_pid") == resource.get("child_pid")
            and type(entry.get("pid")) is int and entry["pid"] > 0
            and type(entry.get("uid")) is int and entry["uid"] > 0
            and type(entry.get("gid")) is int and entry["gid"] > 0
            and entry.get("privileges_dropped_before_exec") is True
            and entry.get("cgroup_self_membership_observed") is True,
            "resume_lineage_original_memory_and_nonroot_entry")
    for label in ("resume-source-gate", "resume-inputs", "resume-recipient"):
        snapshot.copy_root(label, root / label)
    gate, gate_sha = check_source_gate(current, root / "resume-source-gate")
    require(sha(originals["runtime-inventory"]) == gate["runtime"]["runtime_inventory_sha256"]
            and sha(originals["runtime-freeze-receipt"]) == sha(read(root / "resume-source-gate/runtime-receipt.json"))
            and sha(originals["runtime-installed-files"]) == sha(read(root / "resume-source-gate/runtime-installed-files.json"))
            and sha(originals["runtime-wheel-lock"]) == sha(read(root / "resume-source-gate/runtime-wheel-lock.json"))
            and sha(originals["runtime-artifact"]) == sha(read(root / "resume-source-gate/runtime-artifact.original.json"))
            and sha(originals["runtime-reuse"]) == sha(read(root / "resume-source-gate/runtime-reuse.json")),
            "recovery_lineage_runtime_metadata_originals")
    checked_reuse_originals(object_bytes(originals["runtime-reuse"]), originals)
    resume = object_bytes(originals["resume-result"])
    require(resume.get("schema") == "motionwright.manim.recovery-runtime.cycle-observation/1"
            and resume.get("status") == "NEW_REFERENCE_TWO_PLUS_EDITED_TWO_REQUIRES_INDEPENDENT_CUSTODY"
            and resume.get("context") == current and resume.get("origin_context") == ORIGIN
            and resume.get("source_recovery") == SOURCE_RECOVERY and resume.get("native_effect_invocations") == 4
            and resume.get("additional_reference_renders") == 2 and resume.get("runtime_binding") == gate["runtime"]
            and resume.get("project_changes_applied") == 0 and resume.get("original_edit_report_recreated") is False
            and resume.get("old_result_replaced") is False and resume.get("equivalence_to_old_runtime") is False
            and resume.get("final_F_association") == "PENDING" and resume.get("formal_G2_granted") is False
            and resume.get("automatic_retry") is False, "recovery_lineage_new_four_only")
    return originals, resume, gate, gate_sha


def phase(snapshot, name, short, root, current, custody_raw, gate=None):
    snapshot.copy_root("evidence", root)
    engine.new_bytes(root / "editable-custody.json", custody_raw)
    observation = read_object(root / "result.json")
    native = read_object(root / EVIDENCE_NAME)
    require(observation.get("phase") == name and observation.get("context") == current
            and observation.get("binding") == binding(native) and native.get("native_manim_e2e") == "PASS"
            and native.get("custody_error") is None and native.get("receipt_read_error") is None,
            "resume_lineage_real_native_source_binding")
    verify_recovery_phase({
        "current": current, "evidence": root, "phase_observation": observation,
        "native_path": root / EVIDENCE_NAME, "sealed_originals": snapshot.path}, current)
    terminal = read_object(root / "original-terminal.json")
    require(terminal.get("schema") == "motionwright.manim.editable.original-terminal/1"
            and terminal.get("native_example_invoked") is True and same(terminal.get("native_result"), native)
            and terminal.get("native_error") is None and terminal.get("later_error") is None
            and terminal.get("secondary_errors") == [] and terminal.get("automatic_retry") is False
            and type(terminal.get("original_process_observation", {}).get("process_returncode")) is int
            and terminal["original_process_observation"]["process_returncode"] == 0,
            "resume_lineage_original_native_zero_exit")
    source_sha = INPUTS["original-source.mwedit.zip"][1] if PHASES.index(name) < 2 else SOURCE_RECOVERY["export_sha256"]
    source = phase_source(root, native, observation, source_sha, name.startswith("reopen-"))
    runtime_raw = read(root / "runtime-inventory.json")
    inventory = object_bytes(runtime_raw)
    require(inventory.get("schema") == "motionwright.manim.runtime-inventory/2"
            and inventory.get("context") == current
            and sha(runtime_raw) == gate["runtime"]["runtime_inventory_sha256"]
            and observation.get("runtime_binding") == gate["runtime"]
            and read_object(root / "frozen-runtime-binding.json") == gate["runtime"],
            "recovery_lineage_one_new_retained_runtime")
    identities = read_object(root / "native-input-identities.json")
    launch = read_object(root / "original-host-launch.json")
    require(observation.get("launch") == launch and launch.get("fresh_process_launched_by_this_wrapper") is True
            and launch.get("runtime_inventory_sha256") == sha(runtime_raw)
            and identities.get("runtime_inventory_sha256") == sha(runtime_raw)
            and identities.get("motionwright_sha") == current["source_sha"]
            and identities.get("semwright_sha") == current["semwright_sha"]
            and type(identities.get("runner_uid")) is int and identities["runner_uid"] > 0,
            "resume_lineage_original_host_and_runtime")
    if gate is not None:
        require(identities.get("binaries", {}).get("native_example") == gate["compiled_binary_after"]["sha256"],
                "resume_lineage_tested_linux_example")
    admission = read_object(root / "native-video-admission.json")
    report = read_object(root / "native-video-originals/decoded-video.json")
    returned = native["result"]["result"]
    result = returned["result"]
    artifact = result["data"]["artifact"]
    receipt = returned["receipt"]
    provenance = result["execution"]["provenance"]
    require(returned.get("replayed") is False and result.get("provenance_checked") is True
            and receipt.get("command") == "driver.manim-community.render.result" and receipt.get("stage") == "completed"
            and all(receipt.get(key) == native.get(key) for key in ("project_id", "generation", "revision"))
            and receipt.get("payload", {}).get("result") == result
            and native.get("original_result_receipt") == receipt
            and provenance.get("provider") == "driver:manim-community" and provenance.get("source") == "driver"
            and result["execution"].get("backend") == "driver:manim-community"
            and result["data"].get("state") == "succeeded"
            and observation.get("native_request_id") == native.get("request_id")
            and observation.get("native_job_ref") == admission.get("job_ref") == native["result"]["job_ref"],
            "resume_lineage_original_completed_provider_receipt")
    relative = artifact["relative_path"]
    require(re.fullmatch(r"videos/mw_[0-9a-f]{32}/360p12/mw_[0-9a-f]{32}\.mp4", relative),
            "resume_lineage_original_media_path")
    media = snapshot.raw("owner/" + short + "/output/" + relative, MAX_CAPSULE)
    require(identity(media) == {key: artifact[key] for key in ("bytes", "sha256")}
            and artifact["sha256"] == admission.get("artifact_sha256") == report.get("artifact_sha256")
            and admission.get("status") == "PASS"
            and admission.get("authority") == "original_driver_rust_parser_and_receipt"
            and admission.get("report_sha256") == sha(read(root / "native-video-originals/decoded-video.json"))
            and admission.get("plan_sha256") == native["result"]["plan_sha256"]
            and admission.get("native_metadata_frame_count") == admission.get("frame_count") == 24,
            "resume_lineage_original_media_and_decoder_admission")
    engine.new_bytes(root / "recovered-original.mp4", media)
    return {"phase": name, "context": current, "result_sha256": sha(read(root / "result.json")),
            "ciphertext_sha256": snapshot.expected["sha256"], "media_sha256": sha(media),
            "bundle_sha256": source_sha, "native": native, "source": source,
            "observation": observation, "root": root}



def verify(args):
    os.environ["MANIM_NATIVE_SCENARIO"] = RECOVERY_SCENARIO
    private_root(args.private)
    root = args.intake
    require(root.parent == args.private and root.resolve(strict=True) == root,
            "recovery_lineage_existing_private_intake")
    receipt = read_object(root / "intake-result.original.json")
    current = receipt["context"]
    require(receipt.get("schema") == "motionwright.manim.recovery-runtime.lineage-intake/1"
            and current == context(current["source_sha"], current["run_id"])
            and receipt.get("status") == "AUTHENTICATED_COMPLETED_RUN_CIPHERTEXTS_RETAINED"
            and set(receipt.get("files", {})) == NEW_FILES, "recovery_lineage_exact_new_four_intake")
    completed_raw = bounded(root / "completed-run.stdout.original", 512 * 1024)
    require(sha(completed_raw) == receipt["completed_run_sha256"], "recovery_lineage_original_run_receipt")
    completed_run(engine.strict(completed_raw), current)
    archive_raw = bounded(root / "originals-artifact.stdout.original", MAX_ARTIFACT)
    require(identity(archive_raw) == receipt["artifact"], "recovery_lineage_download_original_preserved")
    del archive_raw
    for name, expected in receipt["files"].items():
        require(identity(read(root / "ciphertexts" / name, MAX_CAPSULE)) == expected,
                "recovery_lineage_new_cipher_changed")
    # Only the bytes of the old ciphertexts are checked. Their old images and
    # native observations are neither redecoded nor accepted as the new baseline.
    for name, (size, digest) in OLD_CIPHERS.items():
        require(identity(read(args.old / name, MAX_CAPSULE)) == {"bytes": size, "sha256": digest},
                "recovery_lineage_historical_cipher_preserved")
    old_public = read_object(args.old / "cycle-outer.public.json")
    require(old_public.get("context") == ORIGIN and old_public.get("selected_originals_complete") is False
            and old_public.get("ciphertext_sha256") == OLD_CIPHERS["cycle-outer.mwseal"][1],
            "recovery_lineage_historical_incomplete_outer_not_promoted")
    for name, (size, digest) in INPUTS.items():
        require(identity(read(args.private / name, size)) == {"bytes": size, "sha256": digest},
                "recovery_lineage_five_source_originals_unchanged")
    destination = root / "verified-originals"
    destination.mkdir(mode=0o700, exist_ok=False)
    save(destination, "verification-intent.original.json", {"context": current, "origin": ORIGIN,
        "selected_scenario": RECOVERY_SCENARIO, "historical_native_media_read": False,
        "native_effect_invocations": 0, "source_mutations": 0, "automatic_retry": False})
    key_raw = read(args.key, 8192)
    key = serialization.load_pem_private_key(key_raw, password=None)
    key_raw = b""
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    require(sha(der) == RECIPIENT_SHA, "recovery_lineage_original_host_key_identity")
    public = read_object(root / "ciphertexts/resume-outer.public.json")
    expected = receipt["files"]["resume-outer.mwseal"]
    require(public.get("ciphertext_sha256") == expected["sha256"]
            and public.get("ciphertext_bytes") == expected["bytes"], "recovery_lineage_outer_cipher_binding")
    outer_snapshot = Snapshot(root / "ciphertexts/resume-outer.mwseal", expected, current, key, complete=True)
    outer_root = destination / "outer"
    outer_root.mkdir(mode=0o700)
    originals, cycle, gate, gate_sha = new_outer(outer_snapshot, public, outer_root, current)
    handoff = read_object(args.handoff / "handoff-result.original.json")
    reviewed_raw = read(args.handoff / "publish-plan.json")
    reviewed = object_bytes(reviewed_raw)
    intake_value = read_object(outer_root / "resume-inputs/intake-result.original.json")
    public_key = read_object(outer_root / "resume-recipient/recipient.json")
    release = read_object(outer_root / "resume-inputs/release-readback.original.json")
    validate_recipient(public_key, current, public_key["actor_id"], gate_sha,
                       clock=timestamp(public_key["issued_at"]))
    require(handoff.get("context") == current and handoff.get("status") == "ENCRYPTED_DRAFT_ASSET_RETAINED"
            and handoff.get("release_published") is False and handoff.get("api_asset_id_digest_readback") is True
            and handoff.get("plan_sha256") == sha(reviewed_raw)
            and reviewed.get("scenario") == RECOVERY_SCENARIO and reviewed.get("authorized_new_native_effects") == 4
            and reviewed.get("additional_reference_renders") == 2
            and reviewed.get("equivalence_to_old_runtime") is False
            and handoff.get("asset") == intake_value.get("asset") == validate_release(release, public_key, current)
            and public_key.get("source_gate_sha256") == gate_sha
            and intake_value.get("historical_private_key_received") is False
            and intake_value.get("project_changes_applied") == 0 and intake_value.get("native_effect_invocations") == 0,
            "recovery_lineage_reviewed_four_phase_draft_handoff")
    manifest_raw = read(outer_root / "resume-inputs/originals" / MANIFEST, 16384)
    require(manifest_raw == engine.json_bytes(manifest(current, public_key["recipient"]["sha256"]))
            and sha(manifest_raw) == public_key["input_contract_sha256"]
                == intake_value["manifest_sha256"] == intake_value["input_contract_sha256"],
            "recovery_lineage_five_originals_manifest_scope")
    for name, (size, digest) in INPUTS.items():
        require(identity(read(outer_root / "resume-inputs/originals" / name, size)) ==
                {"bytes": size, "sha256": digest}, "recovery_lineage_transferred_original_source")
    outer_snapshot.close()
    phases = []
    source_root = destination / "source-recovery"
    source_root.mkdir(mode=0o700)
    for name, short in zip(PHASES, SHORT_PHASES):
        expected = receipt["files"][name + ".mwseal"]
        custody_raw = originals[name + "-custody"]
        checked_custody(object_bytes(custody_raw), current, expected)
        snapshot = Snapshot(root / "ciphertexts" / (name + ".mwseal"), expected, current, key, complete=True)
        phases.append(phase(snapshot, name, short, destination / name, current, custody_raw, gate))
        for input_name, (size, digest) in INPUTS.items():
            require(identity(snapshot.raw("owner/source-originals/" + input_name, size)) ==
                    {"bytes": size, "sha256": digest}, "recovery_lineage_owner_source_original_changed")
        if name == PHASES[-1]:
            for source_name in ("committed-source-recovery.json", "committed-source-recovery.current.mwedit.zip",
                                "source-preparation.json"):
                engine.new_bytes(source_root / source_name, snapshot.raw("owner/source-recovery/" + source_name))
        snapshot.close()
    key = None
    recovered_path = source_root / "committed-source-recovery.json"
    recovered_export = recovered_path.with_suffix(".current.mwedit.zip")
    recovered = read_object(recovered_path)
    edited_binding = checked_recovery(recovered, recovered_path, recovered_export)
    preparation = read_object(source_root / "source-preparation.json")
    require(preparation.get("schema") == "motionwright.manim.recovery-runtime.source-preparation/1"
            and preparation.get("context") == current and preparation.get("origin_context") == ORIGIN
            and preparation.get("recovery_receipt_sha256") == sha(read(recovered_path))
            and preparation.get("baseline_copy_initial_sha256") == INPUTS["reopen-original-reference.sqlite3"][1]
            and preparation.get("input_sha256") == recovered["input_sha256"]
            and preparation.get("project_changes_applied") == 0
            and preparation.get("native_effect_invocations") == 0
            and preparation.get("equivalence_to_old_runtime") is False
            and preparation.get("original_edit_report_recreated") is False,
            "recovery_lineage_distinct_rehydrated_sources_without_apply")
    require(recovered["original_stamp"] == stamp(phases[0]["observation"]["binding"])
            == stamp(phases[1]["observation"]["binding"])
            and all(row["observation"]["binding"] == edited_binding for row in phases[2:])
            and preparation["reference_binding"] == phases[0]["observation"]["binding"]
            and preparation["edited_binding"] == edited_binding,
            "recovery_lineage_saved_before_after_three_verified_commits")
    for index, row in enumerate(phases):
        native_context = {"native": row["native"], "native_path": row["root"] / EVIDENCE_NAME,
                          "source": row["source"], "prepared": {"binding": row["observation"]["binding"]}}
        name = "reference-source-readback.json" if index < 2 else "recovered-source-readback.json"
        raw = read(row["root"] / name)
        require(sha(raw) == row["observation"]["source_readback_sha256"],
                "recovery_lineage_original_source_readback_hash")
        if index < 2:
            checked_reference_readback(object_bytes(raw), native_context,
                {"phase": row["phase"], "history": phases[:index]})
        else:
            checked_readback(object_bytes(raw), recovered_path, recovered, native_context, row["phase"])
    require(len({row["native"]["request_id"] for row in phases}) == 4
            and len({row["native"]["result"]["job_ref"] for row in phases}) == 4
            and len({row["observation"]["launch"]["session_sha256"] for row in phases}) == 4,
            "recovery_lineage_four_distinct_real_jobs_and_sessions")
    require(cycle["phase_results"] == [{key: row[key] for key in ("phase", "result_sha256", "ciphertext_sha256")}
                                        for row in phases], "recovery_lineage_four_result_bindings")
    pixels = four_phase_pixels([row["root"] for row in phases], recovered_path)
    runner_pixels_raw = read(phases[-1]["root"] / "recovery-runtime-pixels.json")
    runner_pixels = object_bytes(runner_pixels_raw)
    require(sha(runner_pixels_raw) == cycle.get("pixel_cycle_sha256")
            and {key: value for key, value in runner_pixels.items() if key != "png_decoder"}
                == {key: value for key, value in pixels.items() if key != "png_decoder"},
            "recovery_lineage_independent_96_png_observation_equals_original")
    pixel_sha = save(destination, "all-96-pngs.original.json", pixels)
    result = {"schema": "motionwright.manim.recovery-runtime.physical-lineage/1",
              "status": "VERIFIED_DEVELOPMENT_NEW_REFERENCE_TWO_PLUS_EDITED_TWO",
              "context": current, "origin_context": ORIGIN, "source_recovery": SOURCE_RECOVERY,
              "source_gate_sha256": gate_sha, "runtime_binding": gate["runtime"],
              "phases": [{key: row[key] for key in ("phase", "context", "result_sha256", "ciphertext_sha256",
                                                     "media_sha256", "bundle_sha256")} for row in phases],
              "actual_rust_focals_passed": 10, "new_native_phases_observed": 4,
              "additional_reference_renders": 2, "all_96_retained_pngs_verified": True,
              "both_original_bundle_reopens_verified": True,
              "original_pairs_equal_and_saved_text_change_observed": True,
              "original_font_bytes_license_and_origin_verified": True, "pixel_report_sha256": pixel_sha,
              "historical_native_media_read": False, "old_failed_outer_promoted": False,
              "equivalence_to_old_runtime": False, "old_result_replaced": False,
              "original_edit_report_recreated": False, "source_mutations": 0,
              "native_effect_invocations": 0, "native_video_decode_invocations": 0,
              "F_selected": False, "formal_G2_granted": False, "human_acceptance": False,
              "release_authorized": False, "automatic_retry": False}
    save(destination, "physical-lineage.original.json", result)
    print(json.dumps(result, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    incoming = sub.add_parser("intake")
    incoming.add_argument("--source-sha", required=True)
    incoming.add_argument("--run-id", required=True)
    incoming.add_argument("--gh", type=Path, default=Path(r"C:\Program Files\GitHub CLI\gh.exe"))
    incoming.add_argument("--private", type=Path, required=True)
    check = sub.add_parser("verify")
    for name in ("private", "intake", "old", "key", "handoff"):
        check.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.environ["MANIM_NATIVE_SCENARIO"] = RECOVERY_SCENARIO
    (intake if args.command == "intake" else verify)(args)


if __name__ == "__main__":
    main()
