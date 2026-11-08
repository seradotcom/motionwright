#!/usr/bin/env python3
"""Reuse one retained n1; it never grants another n1 Native invocation.

The original readback failed after a real render.  Its records and pixels stay
unchanged.  A later source readback is a new observation, and the missing
post-render frozen-runtime observation stays NOT_RETAINED.
"""
from __future__ import annotations
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile

from native_manim_camera_evidence import private_bytes, private_json, read, read_object, require, sha

ENVIRONMENT = "MW_MANIM_RECOVERY_CONTINUATION"
CONTINUATION = "retained-n1-42369864661"
CAPSULE = "retained-n1-42369864661.tar"
CAPSULE_IDENTITY = (1617920, "dbc6d91384b5722705857534172088262b36cb7803b2bd763a45811cc1dc5860")
MANIFEST = "retained-reference-manifest.json"
MANIFEST_IDENTITY = (8478, "4a7d9944acbdef31e5c3a715f8d080bc262858271214cc7fc86a665fb2b6429d")
ORIGIN = {"source_sha": "1891bc4f722a920fc5a49cd01d9bab98bb6b2520",
          "semwright_sha": "acc0a089e2f0e3e9936632fb2a4aa0c2fab56199",
          "run_id": "42369864661", "run_attempt": "1"}
PHASES = ("reopen-original-reference", "edited", "reopen-edited")
SOURCE_BUNDLE = "owner/n1/output/mw_01a1b5dfb8a777ac9bb988bc47f8f166.mwedit.zip"
DATABASE = "owner/reference-data/motionwright.sqlite3"
MAXIMUM = 2 * 1024 * 1024


def selection(value=None):
    value = os.environ.get(ENVIRONMENT, "none") if value is None else value
    require(value in ("none", CONTINUATION), "retained_reference_closed_selector")
    if value != "none":
        require(os.environ.get("MANIM_NATIVE_SCENARIO") == "recovery-runtime-cycle",
                "retained_reference_requires_frozen_runtime_cycle")
    return value


def selected():
    return selection() == CONTINUATION


def contract():
    return {"selector": CONTINUATION, "origin_context": ORIGIN,
            "capsule": {"name": CAPSULE, "bytes": CAPSULE_IDENTITY[0], "sha256": CAPSULE_IDENTITY[1]},
            "manifest": {"bytes": MANIFEST_IDENTITY[0], "sha256": MANIFEST_IDENTITY[1]},
            "retained_phase": "original-reference", "remaining_phases": list(PHASES),
            "new_native_effect_invocations": 3, "new_reference_renders": 1,
            "original_readback_status": "FAILED", "post_native_runtime_observation": "NOT_RETAINED",
            "historical_phase_result_created": False, "formal_G2_granted": False,
            "automatic_retry": False}


def manifest_raw():
    path = Path(__file__).resolve().parent / "requirements/manim-retained-reference-42369864661.json"
    raw = read(path, 16384)
    require((len(raw), sha(raw)) == MANIFEST_IDENTITY, "retained_reference_source_manifest_changed")
    return raw


def canonical_tar(files):
    require(0 < len(files) <= 64, "retained_reference_member_count")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, raw in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.mode, member.uid, member.gid, member.mtime = 0o600, 0, 0, 0
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    raw = output.getvalue()
    require(len(raw) <= MAXIMUM, "retained_reference_tar_budget")
    return raw


def unpack(raw):
    require(type(raw) is bytes and (len(raw), sha(raw)) == CAPSULE_IDENTITY,
            "retained_reference_capsule_identity")
    source_manifest = manifest_raw()
    specification = json.loads(source_manifest)
    require(specification.get("schema") == "motionwright.manim.retained-reference-originals/1"
            and specification.get("origin_context") == ORIGIN
            and specification.get("original_readback_status") == "FAILED"
            and specification.get("post_native_frozen_runtime_observation") == "NOT_RETAINED"
            and specification.get("formal_G2_granted") is False,
            "retained_reference_original_scope")
    identities = {**specification["files"], MANIFEST: {
        "bytes": MANIFEST_IDENTITY[0], "sha256": MANIFEST_IDENTITY[1]}}
    require(len(identities) == 50, "retained_reference_fixed_roster")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        rows = archive.getmembers()
        require(len(rows) == len(identities) and {row.name for row in rows} == set(identities),
                "retained_reference_canonical_roster")
        for row in rows:
            expected = identities[row.name]
            relative = PurePosixPath(row.name)
            require(not relative.is_absolute() and relative.as_posix() == row.name
                    and not {".", ".."} & set(relative.parts)
                    and row.isfile() and not row.pax_headers and not row.linkname
                    and row.mode == 0o600 and row.uid == row.gid == row.mtime == 0
                    and row.uname == row.gname == "" and row.size == expected["bytes"],
                    "retained_reference_regular_original")
            stream = archive.extractfile(row)
            require(stream is not None, "retained_reference_member_unreadable")
            value = stream.read(row.size + 1)
            require(len(value) == row.size and sha(value) == expected["sha256"],
                    "retained_reference_original_digest")
            files[row.name] = value
    require(files[MANIFEST] == source_manifest and canonical_tar(files) == raw,
            "retained_reference_alternate_tar_denied")
    return files, specification


def materialize(raw, owner, current):
    require(selected() and current["semwright_sha"] == ORIGIN["semwright_sha"]
            and current["run_id"] != ORIGIN["run_id"] and current["run_attempt"] == "1",
            "retained_reference_new_three_phase_context")
    files, specification = unpack(raw)
    root = owner / "retained-reference"
    originals, derived = root / "originals", root / "derived"
    root.mkdir(mode=0o700, exist_ok=False)
    originals.mkdir(mode=0o700, exist_ok=False)
    derived.mkdir(mode=0o700, exist_ok=False)
    for name, data in sorted(files.items()):
        path = originals.joinpath(*PurePosixPath(name).parts)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_bytes(path, data)
    result = {"root": root, "originals": originals, "derived": derived,
              "manifest": specification, "source_bundle": originals / SOURCE_BUNDLE,
              "database_original": originals / DATABASE, "current": current}
    verify_originals(result)
    return result


def locate(owner, current):
    require(selected(), "retained_reference_explicit_existing_namespace")
    root = owner / "retained-reference"
    originals = root / "originals"
    raw = read(originals / MANIFEST, 16384)
    require(raw == manifest_raw(), "retained_reference_existing_manifest")
    return {"root": root, "originals": originals, "derived": root / "derived",
            "manifest": json.loads(raw), "source_bundle": originals / SOURCE_BUNDLE,
            "database_original": originals / DATABASE, "current": current}


def check_runtime_input(original, current_inventory, current):
    require(original.get("context") == ORIGIN and current_inventory.get("context") == current
            and original.get("schema") == "motionwright.manim.runtime-inventory/2"
            and original.get("native_render_performed") is False
            and {key: value for key, value in original.items() if key != "context"}
                == {key: value for key, value in current_inventory.items() if key != "context"},
            "retained_reference_pre_native_runtime_input_changed")
    # Equality of retained inputs is not the missing old post-render check.
    return {"observation_scope": "ORIGINAL_PRE_NATIVE_INVENTORY_ONLY",
            "post_native_runtime_observation": "NOT_RETAINED", "reference_phase_complete": False}


def verify_originals(retained):
    root = retained["originals"]
    expected = {**retained["manifest"]["files"], MANIFEST: {
        "bytes": MANIFEST_IDENTITY[0], "sha256": MANIFEST_IDENTITY[1]}}
    observed, directories = set(), 0
    for current, children, filenames in os.walk(root, followlinks=False):
        directories += 1
        require(directories <= 32 and len(children) + len(filenames) <= 64,
                "retained_reference_namespace_budget")
        for name in children:
            require(not (Path(current) / name).is_symlink(), "retained_reference_directory_alias")
        for name in filenames:
            relative = (Path(current) / name).relative_to(root).as_posix()
            require(relative in expected and relative not in observed, "retained_reference_unselected_member")
            observed.add(relative)
    require(observed == set(expected), "retained_reference_originals_incomplete")
    for name, identity in expected.items():
        raw = read(root.joinpath(*PurePosixPath(name).parts), MAXIMUM)
        require({"bytes": len(raw), "sha256": sha(raw)} == identity,
                "retained_reference_originals_changed:" + name)
    custody = read_object(root / "origin/phase-custody.original.json")
    terminal = read_object(root / "origin/cgroup-terminal.original.json")
    entered = read_object(root / "origin/cgroup-entered.original.json")
    native = read_object(root / "evidence/native-example.process.json")
    broker = read_object(root / "evidence/broker.process.json")
    require(custody.get("context") == ORIGIN and custody.get("status") == "PASS"
            and custody.get("selected_originals_complete") is True
            and custody.get("native_result_retained") is True and custody.get("secondary_error_count") == 0
            and {"bytes": custody.get("ciphertext_bytes"), "sha256": custody.get("ciphertext_sha256")}
                == retained["manifest"]["origin_phase_ciphertext"], "retained_reference_original_custody")
    require(terminal.get("context") == entered.get("context") == ORIGIN
            and terminal.get("status") == "RESOURCE_OR_COMMAND_FAILED_NO_RETRY"
            and terminal.get("original_child_exit_known") is True and terminal.get("original_child_exit") == 1
            and terminal.get("cgroup_quiescent") is True
            and terminal.get("remaining_processes_before_owned_cleanup") == []
            and terminal.get("original_cgroup_entry") == entered
            and entered.get("cgroup_self_membership_observed") is True
            and entered.get("privileges_dropped_before_exec") is True and entered.get("uid") == 1001
            and terminal.get("resource_after", {}).get("oom") == 0
            and terminal.get("resource_after", {}).get("oom_kill") == 0
            and terminal["resource_after"]["peak_bytes"] <= terminal["physical_peak_budget_bytes"] == 1610612736,
            "retained_reference_original_owned_quiescence")
    require(native.get("process_exit_known") is True and native.get("process_returncode") == 0
            and native.get("original_error") is None and native.get("status") == "EXITED"
            and broker.get("process_exit_known") is True and broker.get("process_returncode") == 0
            and broker.get("status") == "UNKNOWN" and broker.get("native_outcome") == "UNKNOWN",
            "retained_reference_original_terminal_labels_preserved")
    return custody


def phase_original(retained, state):
    from native_manim_editable_cycle import binding
    originals = retained["originals"]
    evidence = originals / "evidence"
    native = read_object(evidence / "motionwright-manim-evidence.json")
    source = read_object(evidence / "editable-source-retention.json")
    launch = read_object(evidence / "original-host-launch.json")
    require(native.get("native_manim_e2e") == "PASS" and native.get("custody_error") is None
            and native.get("receipt_read_error") is None
            and binding(native) == binding(state["original_plan"]["binding"])
            and source.get("plan") == state["original_plan"]
            and source.get("source_program_equals_actual_native_input") is True
            and source.get("original_font_bytes_retained") is True,
            "retained_reference_original_native_source")
    source = {**source, "bundle_path": retained["source_bundle"]}
    return {"retained_reference": True, "retained": retained, "current": ORIGIN,
            "evidence": evidence, "native_path": evidence / "motionwright-manim-evidence.json",
            "native": native, "source": source, "launch": launch,
            "output_root": originals / "owner/n1/output", "prepared": {
                "binding": binding(state["original_plan"]["binding"])},
            "database": state["reference_database"]}


def record_source_readback(retained, value, path, rehydration_path, current):
    require(path.parent == retained["derived"] and read_object(path) == value,
            "retained_reference_new_readback_location")
    observation = {"schema": "motionwright.manim.retained-reference-observation/1",
        "status": "SOURCE_READBACK_RECOVERED_RUNTIME_POSTCHECK_NOT_RETAINED",
        "phase": "original-reference", "origin_context": ORIGIN, "observer_context": current,
        "reference_contract": contract(), "readback_sha256": sha(read(path)),
        "rehydration_sha256": sha(read(rehydration_path)),
        "old_readback_status": "FAILED", "old_broker_terminal_status": "UNKNOWN",
        "post_native_runtime_observation": "NOT_RETAINED", "project_changes_applied": 0,
        "new_native_effect_invocations": 0, "historical_phase_result_created": False,
        "formal_G2_granted": False, "automatic_retry": False}
    private_json(retained["derived"] / "observation.json", observation)
    return observation


def verify_phase(phase, current):
    retained = phase["retained"]
    custody = verify_originals(retained)
    observed = read_object(retained["derived"] / "observation.json")
    require(phase.get("retained_reference") is True and phase.get("current") == ORIGIN
            and observed == phase["phase_observation"]
            and observed.get("observer_context") == current and observed.get("origin_context") == ORIGIN
            and observed.get("reference_contract") == contract()
            and observed.get("status") == "SOURCE_READBACK_RECOVERED_RUNTIME_POSTCHECK_NOT_RETAINED"
            and observed.get("post_native_runtime_observation") == "NOT_RETAINED"
            and observed.get("old_broker_terminal_status") == "UNKNOWN"
            and observed.get("new_native_effect_invocations") == 0
            and observed.get("historical_phase_result_created") is False
            and observed.get("formal_G2_granted") is False,
            "retained_reference_observation_not_new_native_pass")
    require(sha(read(retained["derived"] / "reference-source-readback.json")) == observed["readback_sha256"]
            and sha(read(retained["derived"] / "reference-rehydration.json")) == observed["rehydration_sha256"],
            "retained_reference_new_source_observations_changed")
    return custody
