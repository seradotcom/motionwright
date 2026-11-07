#!/usr/bin/env python3
"""Host-only admission of the genuine old two plus new two Manim phases.

Intake downloads one exact completed-run ciphertext artifact. Verification
reads authenticated capsules and retained PNGs; it never executes the renderer,
opens a Store, replays an edit, or recreates the absent historical edit report.
The old failed outer remains failed and formal F/G2 acceptance remains pending.
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
import tarfile
import zipfile

from cryptography.hazmat.primitives import serialization
import ci_private_evidence as engine
from native_manim_camera_evidence import object_bytes, read, read_object, require, sha
from native_manim_editable_cycle import (
    BINDING_KEYS, EVIDENCE_NAME, FONT_SHA, LICENSE_SHA, ORIGIN_SHA, PHASES,
    binding, checked_native_link, stamp, verify_phase as verify_original_phase,
)
from native_manim_resume_cycle import checked_readback, checked_recovery, verify_phase as verify_resumed_phase
from native_manim_resume_focals import check_source_gate
from native_manim_resume_host import bounded, get, private_root, save
from native_manim_resume_pixels import four_phase_pixels
from native_manim_resume_runtime import compare as compare_runtime, check as check_runtime
from native_manim_resume_transfer import (
    INPUTS, JOB, ORIGIN, REF, REPOSITORY, SDK, SOURCE_RECOVERY, validate_release,
)

RECIPIENT_SHA = "afbb08aa54131350d14601ccbb111f25e1eec829789818bc70e1e96accb3e4aa"
OLD_CIPHERS = {
    "original.mwseal": (29942813, "a766ffbc06d8b6abb2c86b6fde08318ef26302ae4490bc8709d142986f0c01f9"),
    "reopen-original.mwseal": (32738333, "aceef1c18997de00d9e30c64c74bd149bcec81f5c9da67f9ac9cc0322439618a"),
    "edited.mwseal": (33690653, "fba3cebdb1137b9b38fd710bbaa3354ba8def3f0d980d635f61d2dd4cdb1cae3"),
    "cycle-outer.mwseal": (554011, "952fc6e8d699911927542e019b63e0546cbc48bd8e4a88bdcc43027389fd4ff7"),
}
NEW_FILES = {"edited.mwseal", "reopen-edited.mwseal", "resume-outer.mwseal", "resume-outer.public.json"}
MAX_CAPSULE = 128 * 1024 * 1024
MAX_ARTIFACT = 256 * 1024 * 1024


def same(left, right):
    return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == \
           json.dumps(right, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(raw):
    return {"bytes": len(raw), "sha256": sha(raw)}


def context(source, run_id):
    value = {"source_sha": source, "semwright_sha": SDK, "run_id": run_id, "run_attempt": "1"}
    engine.validate_context(value)
    require(value != ORIGIN and value["source_sha"] != ORIGIN["source_sha"], "resume_lineage_new_run_required")
    return value


def completed_run(value, current):
    require(type(value.get("id")) is int and value["id"] == int(current["run_id"])
            and value.get("head_sha") == current["source_sha"] and value.get("run_attempt") == 1
            and value.get("head_branch") == REF.removeprefix("refs/heads/")
            and value.get("event") == "workflow_dispatch" and value.get("status") == "completed"
            and value.get("conclusion") == "success" and value.get("path") == ".github/workflows/native-manim-e2e.yml"
            and value.get("repository", {}).get("full_name") == REPOSITORY,
            "resume_lineage_completed_original_run_required")


def intake(args):
    current = context(args.source_sha, args.run_id)
    private_root(args.private)
    require(shutil.disk_usage(args.private).free >= 1024 * 1024 * 1024, "resume_lineage_disk_reserve")
    root = args.private / ("resume-lineage-" + current["run_id"] + "-" + current["source_sha"][:12])
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
    name = "motionwright-manim-resume-originals-" + current["source_sha"] + "-" + current["run_id"] + "-a1"
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
    receipt = {"schema": "motionwright.manim.resume.lineage-intake/1", "context": current,
               "status": "AUTHENTICATED_COMPLETED_RUN_CIPHERTEXTS_RETAINED",
               "artifact_id": row["id"], "artifact": identity(archive_raw), "files": retained,
               "completed_run_sha256": sha(run_raw), "completed_jobs_sha256": sha(jobs_raw),
               "artifact_list_sha256": sha(artifacts_raw), "private_key_accessed": False,
               "native_effect_invocations": 0, "automatic_retry": False}
    save(root, "intake-result.original.json", receipt)
    print(json.dumps({"status": receipt["status"], "root": str(root), "context": current,
                      "artifact_id": row["id"], "files": retained}))


class Snapshot:
    """One bounded authenticated capsule; selected files are read by manifest."""
    def __init__(self, path, expected, current, key, *, complete):
        self.path, self.expected = Path(path), expected
        raw = read(self.path, MAX_CAPSULE)
        require(identity(raw) == expected, "resume_lineage_cipher_identity")
        offset = len(engine.MAGIC)
        require(raw.startswith(engine.MAGIC), "resume_lineage_cipher_magic")
        size = int.from_bytes(raw[offset:offset + 4], "big")
        require(0 < size <= 8192, "resume_lineage_header_bound")
        header = engine.strict(raw[offset + 4:offset + 4 + size])
        require(header.get("context") == current and header.get("recipient_spki_sha256") == RECIPIENT_SHA
                and type(header.get("plaintext_bytes")) is int and 0 < header["plaintext_bytes"] <= MAX_CAPSULE,
                "resume_lineage_authenticated_context")
        plaintext, observed = engine.decrypt_snapshot(raw, key)
        require(observed == current, "resume_lineage_decrypted_context")
        self.stream = io.BytesIO(plaintext)
        self.archive = tarfile.open(fileobj=self.stream, mode="r:")
        members = self.archive.getmembers()
        self.members = {row.name: row for row in members}
        require(len(members) <= engine.MAX_FILES + 1 and len(self.members) == len(members),
                "resume_lineage_closed_snapshot_names")
        require(all(row.isreg() and not row.linkname and not row.sparse
                    and row.pax_headers in ({}, {"path": row.name})
                    and 0 <= row.size <= MAX_CAPSULE and row.name == str(PurePosixPath(row.name))
                    and not row.name.startswith("/") and ".." not in PurePosixPath(row.name).parts
                    and "\\" not in row.name and ":" not in row.name for row in members),
                "resume_lineage_snapshot_regular_members_only")
        manifest = self.members["snapshot-manifest.json"]
        require(manifest.size <= 2 * 1024 * 1024, "resume_lineage_manifest_budget")
        self.manifest_raw = self.archive.extractfile(manifest).read(2 * 1024 * 1024 + 1)
        self.manifest = object_bytes(self.manifest_raw)
        require(self.manifest.get("schema") == "motionwright.ci.private-snapshot/1"
                and self.manifest.get("scope") == "selected-original-files-at-read-time"
                and self.manifest.get("complete_owner_state_claimed") is False
                and (self.manifest.get("selected_originals_complete") is True if complete
                     else self.manifest.get("selected_originals_complete") is False),
                "resume_lineage_snapshot_scope")

    def raw(self, name, limit=4 * 1024 * 1024):
        member = self.members[name]
        require(member.size <= limit, "resume_lineage_selected_member_budget")
        root = self.manifest["roots"][name.split("/", 1)[0]]
        require(root.get("status") == "CAPTURED", "resume_lineage_selected_root_not_complete")
        rows = [row for row in root["files"] if row.get("archive_path") == name]
        require(len(rows) == 1 and rows[0].get("status") is None and "observation" in rows[0],
                "resume_lineage_selected_observation_missing")
        observed = rows[0]["observation"]
        raw = self.archive.extractfile(member).read(limit + 1)
        require(len(raw) == member.size == observed.get("size") and sha(raw) == observed.get("sha256")
                and observed.get("before") == observed.get("after") == observed.get("namespace_after"),
                "resume_lineage_original_member_changed")
        return raw

    def copy_root(self, label, destination):
        require(self.manifest["roots"][label].get("status") == "CAPTURED", "resume_lineage_captured_root_required")
        destination.mkdir(mode=0o700, exist_ok=False)
        total = 0
        for name in sorted(name for name in self.members if name.startswith(label + "/")):
            raw = self.raw(name, MAX_CAPSULE)
            total += len(raw)
            require(total <= MAX_CAPSULE, "resume_lineage_selected_root_byte_budget")
            target = destination / name.removeprefix(label + "/")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            engine.new_bytes(target, raw)

    def close(self):
        self.archive.close()
        self.stream.close()
        require(identity(read(self.path, MAX_CAPSULE)) == self.expected, "resume_lineage_cipher_changed_after_read")


def checked_custody(value, current, expected):
    require(value.get("schema") == "motionwright.manim.editable.sealed-custody/1"
            and value.get("status") == "PASS" and value.get("context") == current
            and value.get("recipient_sha256") == RECIPIENT_SHA
            and value.get("selected_originals_complete") is True
            and value.get("native_result_retained") is True
            and type(value.get("secondary_error_count")) is int and value["secondary_error_count"] == 0
            and value.get("ciphertext_bytes") == expected["bytes"]
            and value.get("ciphertext_sha256") == expected["sha256"], "resume_lineage_complete_original_phase_custody")


def outer_original(snapshot, public, label):
    raw = snapshot.raw(label + "/original")
    require(public.get("original_files", {}).get(label) == identity(raw), "resume_lineage_outer_original_digest")
    return raw


def new_outer(snapshot, public, root, current):
    require(public.get("schema") == "motionwright.manim.editable.resume-outer-custody-public/1"
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
            and resource.get("physical_reserve_bytes") == 512 * 1024 * 1024
            and resource.get("minimum_physical_available_bytes", 0) >= 512 * 1024 * 1024
            and resource.get("cgroup_quiescent") is True and resource.get("first_error") is None,
            "resume_lineage_native_cgroup_original_terminal")
    require(type(after.get("peak_bytes")) is int and 0 <= after["peak_bytes"] <= 1536 * 1024 * 1024
            and after.get("limit_bytes") == before.get("limit_bytes") == 1536 * 1024 * 1024
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
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    for label, name in (("runtime-inventory", "runtime-inventory.json"), ("runtime-match", "resume-runtime-match.json")):
        engine.new_bytes(runtime / name, originals[label])
    require(check_runtime(current, runtime) == gate["runtime"], "resume_lineage_original_runtime_gate")
    resume = object_bytes(originals["resume-result"])
    require(resume.get("schema") == "motionwright.manim.editable.resumed-two-phase-observation/1"
            and resume.get("status") == "TWO_NATIVE_PHASES_RECOVERED_LINEAGE_PENDING"
            and resume.get("context") == current and resume.get("origin_context") == ORIGIN
            and resume.get("source_recovery") == SOURCE_RECOVERY and resume.get("native_effect_invocations") == 2
            and resume.get("project_changes_applied") == 0 and resume.get("original_edit_report_recreated") is False
            and resume.get("old_two_phase_lineage_verified") is False
            and resume.get("four_phase_acceptance") == "PENDING" and resume.get("formal_G2_granted") is False
            and resume.get("automatic_retry") is False, "resume_lineage_new_two_only")
    return originals, resume, gate, gate_sha


def phase_source(root, native, observation, expected_sha, reopened):
    source = read_object(root / "editable-source-retention.json")
    descriptor = source["descriptor"]
    bundle = read(root / "editable-source-originals/source.mwedit.zip", 4 * 1024 * 1024)
    require(sha(bundle) == descriptor.get("sha256") == expected_sha
            and len(bundle) == descriptor.get("bytes") and source.get("source_program_equals_actual_native_input") is True
            and source.get("original_font_bytes_retained") is True and source.get("font_license_sha256") == LICENSE_SHA
            and source.get("font_origin_sha256") == ORIGIN_SHA
            and source.get("plan", {}).get("binding") == observation["binding"], "resume_lineage_saved_source_bytes")
    entries = {}
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        members = archive.infolist()
        require(len(members) == 7 and len({row.filename for row in members}) == 7 and not archive.comment,
                "resume_lineage_source_package_roster")
        for member in members:
            require(member.compress_type == zipfile.ZIP_STORED and not member.flag_bits & 1
                    and 0 < member.file_size <= 4 * 1024 * 1024, "resume_lineage_source_package_entry")
            entries[member.filename] = archive.read(member)
    require(set(entries) == {"plan.json", "scene.py", "fonts.conf", "render-config.json", "assets.lock.json",
                            "manifest.json", f"fonts/{FONT_SHA}.ttf"}, "resume_lineage_source_closed_entries")
    require(source.get("entries") == [{"path": name, **identity(raw)} for name, raw in sorted(entries.items())],
            "resume_lineage_source_entry_originals")
    for name, raw in entries.items():
        require(read(root / "editable-source-originals" / name, 4 * 1024 * 1024) == raw,
                "resume_lineage_package_retained_member_changed")
    require(object_bytes(entries["plan.json"]) == source["plan"]
            and sha(entries["plan.json"]) == native["result"]["plan_sha256"] == descriptor["closure"]["plan_sha256"]
            and sha(entries["scene.py"]) == descriptor["closure"]["source_sha256"]
            and read(root / "native-video-originals/source.py") == entries["scene.py"]
            and sha(entries[f"fonts/{FONT_SHA}.ttf"]) == FONT_SHA
            and sha(read(root / "editable-source-originals/Montserrat-OFL.txt")) == LICENSE_SHA
            and sha(read(root / "editable-source-originals/Montserrat-source.json")) == ORIGIN_SHA,
            "resume_lineage_plan_program_font_closure")
    verified = native["result"]["artifact"]["editable_source"]
    actual = native["result"]["result"]["result"]["data"]["artifact"]
    getters_raw = read(root / "editable-source-originals/native-objects.json")
    require(verified.get("source") == descriptor == actual.get("editable_source")
            and verified.get("native_getters") == source["getters"] == actual.get("editable_getters")
            and source["getters"].get("sha256") == sha(getters_raw)
            and source["getters"].get("bytes") == len(getters_raw)
            and verified.get("native_object_getters_verified") is True
            and verified.get("source_bundle_inspected") is True
            and verified.get("observed_object_count") == 3 and verified.get("observed_font_count") == 1,
            "resume_lineage_official_native_object_getters")
    getters = object_bytes(getters_raw)
    require(getters.get("source_file_sha256") == sha(entries["scene.py"])
            and getters.get("fontconfig_sha256") == sha(entries["fonts.conf"]), "resume_lineage_getter_program_binding")
    if reopened:
        requested = observation["source_bundle_input"]
        require(type(requested) is dict and requested.get("sha256") == expected_sha
                and requested.get("bytes") == len(bundle) and native.get("requested_original_source_bundle") == requested
                and actual.get("editable_input_source") == native["result"]["artifact"].get("editable_input_source")
                and actual.get("editable_input_source", {}).get("input") == requested,
                "resume_lineage_physical_source_bundle_reopened")
    else:
        require(observation.get("source_bundle_input") is None and native.get("requested_original_source_bundle") is None
                and actual.get("editable_input_source") is None, "resume_lineage_generated_source_input_absent")
    return source


def phase(snapshot, name, short, root, current, custody_raw, gate=None):
    snapshot.copy_root("evidence", root)
    engine.new_bytes(root / "editable-custody.json", custody_raw)
    observation = read_object(root / "result.json")
    native = read_object(root / EVIDENCE_NAME)
    require(observation.get("phase") == name and observation.get("context") == current
            and observation.get("binding") == binding(native) and native.get("native_manim_e2e") == "PASS"
            and native.get("custody_error") is None and native.get("receipt_read_error") is None,
            "resume_lineage_real_native_source_binding")
    (verify_original_phase if current == ORIGIN else verify_resumed_phase)({
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
    source_sha = INPUTS["original-source.mwedit.zip"][1] if current == ORIGIN else SOURCE_RECOVERY["export_sha256"]
    source = phase_source(root, native, observation, source_sha, name.startswith("reopen-"))
    runtime_raw = read(root / "runtime-inventory.json")
    require(compare_runtime(object_bytes(runtime_raw), current)["status"] == "SAME_ORIGINAL_RUNTIME",
            "resume_lineage_identical_measured_runtime")
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
    private_root(args.private)
    root = args.intake
    require(root.parent == args.private and root.resolve(strict=True) == root, "resume_lineage_existing_private_intake")
    receipt = read_object(root / "intake-result.original.json")
    current = receipt["context"]
    require(current == context(current["source_sha"], current["run_id"])
            and receipt.get("status") == "AUTHENTICATED_COMPLETED_RUN_CIPHERTEXTS_RETAINED"
            and set(receipt.get("files", {})) == NEW_FILES, "resume_lineage_original_intake_scope")
    completed_raw = bounded(root / "completed-run.stdout.original", 512 * 1024)
    require(sha(completed_raw) == receipt["completed_run_sha256"], "resume_lineage_original_run_receipt")
    completed_run(engine.strict(completed_raw), current)
    archive_raw = bounded(root / "originals-artifact.stdout.original", MAX_ARTIFACT)
    require(identity(archive_raw) == receipt["artifact"], "resume_lineage_original_download_preserved")
    del archive_raw
    for name, expected in receipt["files"].items():
        require(identity(read(root / "ciphertexts" / name, MAX_CAPSULE)) == expected, "resume_lineage_new_cipher_changed")
    for name, (size, digest) in OLD_CIPHERS.items():
        require(identity(read(args.old / name, MAX_CAPSULE)) == {"bytes": size, "sha256": digest},
                "resume_lineage_old_cipher_changed")
    for name, (size, digest) in INPUTS.items():
        require(identity(read(args.private / name, 4 * 1024 * 1024)) == {"bytes": size, "sha256": digest},
                "resume_lineage_five_source_originals_changed")
    destination = root / "verified-originals"
    destination.mkdir(mode=0o700, exist_ok=False)
    save(destination, "verification-intent.original.json", {"context": current, "origin": ORIGIN,
         "old_failed_outer_will_not_be_promoted": True, "native_effect_invocations": 0,
         "source_mutations": 0, "automatic_retry": False})
    key_raw = read(args.key, 8192)
    key = serialization.load_pem_private_key(key_raw, password=None)
    key_raw = b""
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    require(sha(der) == RECIPIENT_SHA, "resume_lineage_historical_host_key_identity")
    old_public = read_object(args.old / "cycle-outer.public.json")
    old_expected = dict(zip(("bytes", "sha256"), OLD_CIPHERS["cycle-outer.mwseal"]))
    require(old_public.get("ciphertext_sha256") == old_expected["sha256"]
            and old_public.get("ciphertext_bytes") == old_expected["bytes"]
            and old_public.get("context") == ORIGIN and old_public.get("selected_originals_complete") is False,
            "resume_lineage_old_failed_outer_retained")
    old = Snapshot(args.old / "cycle-outer.mwseal", old_expected, ORIGIN, key, complete=False)
    old_custodies = {name: outer_original(old, old_public, name + "-custody") for name in PHASES[:2]}
    old_failed_raw = outer_original(old, old_public, "edited-custody")
    old_terminal_raw = outer_original(old, old_public, "wrapper-terminal")
    old_failure = object_bytes(old_failed_raw)
    old_terminal = object_bytes(old_terminal_raw)
    require(old_failure.get("status") != "PASS" and old_failure.get("native_result_retained") is False
            and old_failure.get("ciphertext_sha256") == OLD_CIPHERS["edited.mwseal"][1]
            and old_terminal.get("automatic_retry") is False
            and old_terminal.get("process_exit_known") is True
            and type(old_terminal.get("original_exit")) is int and old_terminal["original_exit"] != 0,
            "resume_lineage_historical_failure_cannot_become_PASS")
    old.close()
    new_public = read_object(root / "ciphertexts/resume-outer.public.json")
    expected = receipt["files"]["resume-outer.mwseal"]
    require(new_public.get("ciphertext_sha256") == expected["sha256"]
            and new_public.get("ciphertext_bytes") == expected["bytes"], "resume_lineage_new_outer_cipher_binding")
    new = Snapshot(root / "ciphertexts/resume-outer.mwseal", expected, current, key, complete=True)
    outer_root = destination / "new-outer"
    outer_root.mkdir(mode=0o700)
    originals, resumed, gate, gate_sha = new_outer(new, new_public, outer_root, current)
    handoff = read_object(args.handoff / "handoff-result.original.json")
    intake_value = read_object(outer_root / "resume-inputs/intake-result.original.json")
    public_key = read_object(outer_root / "resume-recipient/recipient.json")
    release = read_object(outer_root / "resume-inputs/release-readback.original.json")
    require(handoff.get("context") == current and handoff.get("status") == "ENCRYPTED_DRAFT_ASSET_RETAINED"
            and handoff.get("release_published") is False and handoff.get("api_asset_id_digest_readback") is True
            and handoff.get("asset") == intake_value.get("asset") == validate_release(release, public_key, current)
            and public_key.get("source_gate_sha256") == gate_sha
            and intake_value.get("historical_private_key_received") is False,
            "resume_lineage_original_draft_asset_and_source_gate")
    for name, (size, digest) in INPUTS.items():
        require(identity(read(outer_root / "resume-inputs/originals" / name, 4 * 1024 * 1024)) ==
                {"bytes": size, "sha256": digest}, "resume_lineage_transferred_original_source")
    new.close()
    phases = []
    for name, short in zip(PHASES, ("e1", "e2", "r3", "r4")):
        is_old = name in PHASES[:2]
        selected_context = ORIGIN if is_old else current
        expected = dict(zip(("bytes", "sha256"), OLD_CIPHERS[name + ".mwseal"])) if is_old else receipt["files"][name + ".mwseal"]
        cipher = (args.old if is_old else root / "ciphertexts") / (name + ".mwseal")
        custody = old_custodies[name] if is_old else originals[name + "-custody"]
        checked_custody(object_bytes(custody), selected_context, expected)
        snapshot = Snapshot(cipher, expected, selected_context, key, complete=True)
        phases.append(phase(snapshot, name, short, destination / name, selected_context, custody,
                            gate=None if is_old else gate))
        snapshot.close()
    key = None
    recovered_path = phases[2]["root"] / "committed-source-recovery.json"
    recovered_export = recovered_path.with_suffix(".current.mwedit.zip")
    recovered = read_object(recovered_path)
    selected = checked_recovery(recovered, recovered_path, recovered_export)
    require(recovered.get("original_stamp") == stamp(phases[0]["observation"]["binding"])
            == stamp(phases[1]["observation"]["binding"])
            and all(phases[index]["observation"]["binding"] == selected for index in (2, 3)),
            "resume_lineage_original_to_three_committed_changes")
    for row in phases[2:]:
        native_context = {"native": row["native"], "native_path": row["root"] / EVIDENCE_NAME,
                          "source": row["source"], "prepared": {"binding": selected}}
        checked_readback(read_object(row["root"] / "recovered-source-readback.json"), recovered_path,
                         recovered, native_context, row["phase"])
    require(len({row["native"]["request_id"] for row in phases}) == 4
            and len({row["native"]["result"]["job_ref"] for row in phases}) == 4
            and len({row["observation"]["launch"]["session_sha256"] for row in phases}) == 4,
            "resume_lineage_four_distinct_original_requests_jobs_sessions")
    require(resumed["phase_results"] == [{key: row[key] for key in ("phase", "result_sha256", "ciphertext_sha256")}
                                          for row in phases[2:]], "resume_lineage_new_two_result_binding")
    pixels = four_phase_pixels([row["root"] for row in phases], recovered_path)
    pixel_sha = save(destination, "all-96-pngs.original.json", pixels)
    result = {"schema": "motionwright.manim.editable.resumed-physical-lineage/1",
              "status": "VERIFIED_DEVELOPMENT_OLD_TWO_NEW_TWO_SOURCE_CYCLE", "origin_context": ORIGIN,
              "resumed_context": current, "source_recovery": SOURCE_RECOVERY, "source_gate_sha256": gate_sha,
              "phases": [{key: row[key] for key in ("phase", "context", "result_sha256", "ciphertext_sha256",
                                                     "media_sha256", "bundle_sha256")} for row in phases],
              "actual_rust_focals_passed": 8, "all_96_retained_pngs_verified": True,
              "original_pairs_equal_and_saved_text_change_observed": True,
              "original_font_bytes_license_and_origin_verified": True,
              "pixel_report_sha256": pixel_sha, "old_failed_outer_promoted": False,
              "historical_failed_edit_custody_sha256": sha(old_failed_raw),
              "historical_failed_wrapper_terminal_sha256": sha(old_terminal_raw),
              "historical_wrapper_exit": old_terminal["original_exit"],
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
    (intake if args.command == "intake" else verify)(args)


if __name__ == "__main__":
    main()
