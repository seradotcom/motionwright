#!/usr/bin/env python3
"""Four real Native phases over retained before/after Stores in one new runtime.

The historical three edits are observed, never applied again. Two new reference
renders replace an unavailable runtime comparison, not the old result. Every
Native attempt owns its original session, outputs, readback and sealed custody.
"""
from __future__ import annotations
import argparse
import io
import json
import os
from pathlib import Path
import signal
import zipfile

from native_manim_camera_evidence import object_bytes, private_bytes, private_json, read, read_object, require, sha
from native_manim_editable_cycle import (
    EVIDENCE_NAME, FONT_SHA, LICENSE_SHA, ORIGIN_SHA, binding, checked_native_link,
    checked_seed, retained_source, stamp, verify_phase as verify_custody,
)
from native_manim_resume_cycle import admitted_originals, checked_readback, checked_recovery, checked_receipt_classification
from native_manim_resume_transfer import (
    INPUTS, MANIFEST, ORIGIN, RECOVERY_SCENARIO, SOURCE_RECOVERY, hosted_context, selected_scenario,
)
import native_manim_retained_reference as retained_reference

PHASES = ("original-reference", "reopen-original-reference", "edited", "reopen-edited")
SHORT_PHASES = ("n1", "n2", "n3", "n4")


def owned_root(current):
    return Path(os.environ["RUNNER_TEMP"]).resolve(strict=True) / ("mw-mc-" + current["run_id"])


def scopes(current):
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    return (temporary / ("manim-recovery-private-" + current["source_sha"]),
            temporary / ("manim-recovery-sealed-" + current["source_sha"]),
            temporary / ("manim-recovery-public-" + current["source_sha"]))


def original_plan(raw):
    require(len(raw) == INPUTS["original-source.mwedit.zip"][0]
            and sha(raw) == INPUTS["original-source.mwedit.zip"][1], "recovery_runtime_original_source_bytes")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        require(len(members) == 7 and len({row.filename for row in members}) == 7,
                "recovery_runtime_original_source_roster")
        member = archive.getinfo("plan.json")
        require(member.compress_type == zipfile.ZIP_STORED and not member.flag_bits & 1
                and 0 < member.file_size <= 256 * 1024, "recovery_runtime_original_plan_budget")
        plan = object_bytes(archive.read(member))
    require(plan.get("profile_id") == "mw-manim-editable-text-v3"
            and plan.get("clock", {}).get("frame_count") == 24
            and plan.get("render_profile") == {"width": 640, "height": 360, "frame_rate": 12},
            "recovery_runtime_original_profile")
    return plan


def prepare_sources(current, owner, *, capture, source_binary, retained=None):
    if retained is not None:
        retained_reference.check_runtime_input(
            read_object(retained["originals"] / "evidence/runtime-inventory.json"),
            read_object(Path(__file__).resolve().parents[1] / "verification/native-manim-e2e/runtime-inventory.json"),
            current)
    files, manifest_raw, intake = admitted_originals(current, frozen=True)
    roots = {name: owner / name for name in ("source-originals", "reference-data", "motionwright-data",
                                             "readback-baseline", "source-recovery")}
    if retained is not None:
        roots["reference-recovery-input"] = owner / "reference-recovery-input"
    for path in roots.values():
        path.mkdir(mode=0o700, exist_ok=False)
    originals = roots["source-originals"]
    for name, raw in files.items():
        private_bytes(originals / name, raw)
    private_bytes(originals / MANIFEST, manifest_raw)
    private_json(roots["source-recovery"] / "input-intake.original.json", intake)
    reference_db = roots["reference-recovery-input" if retained is not None else "reference-data"] / "motionwright.sqlite3"
    current_db = roots["motionwright-data"] / "motionwright.sqlite3"
    baseline_db = roots["readback-baseline"] / "motionwright.sqlite3"
    for target in (reference_db, baseline_db):
        private_bytes(target, files["reopen-original-reference.sqlite3"])
    private_bytes(current_db, files["edited-original.sqlite3"])
    report = roots["source-recovery"] / "committed-source-recovery.json"
    recovered = capture([
        str(source_binary), "source-cycle-recover-edit-default-cas", str(reference_db), str(current_db),
        str(originals / "original-source.mwedit.zip"), str(originals / "edited-source.mwedit.zip"),
        str(originals / "source-edit-intent.original.json"), str(report),
    ], env=dict(os.environ), evidence=roots["source-recovery"], label="committed-source-recovery",
       timeout=120, record_native=False)
    selected = checked_recovery(recovered, report, report.with_suffix(".current.mwedit.zip"))
    before_plan = original_plan(files["original-source.mwedit.zip"])
    require(stamp(binding(before_plan["binding"])) == recovered["original_stamp"]
            and selected == recovered["prepared_plan"]["binding"], "recovery_runtime_saved_pair_binding")
    fixture = Path(__file__).resolve().parents[1] / "crates/manim-profile/tests/fixtures"
    for suffix, expected in (("Montserrat-OFL.txt", LICENSE_SHA), ("Montserrat-source.json", ORIGIN_SHA)):
        raw = read(fixture / suffix, 64 * 1024)
        require(sha(raw) == expected, "recovery_runtime_original_license_and_origin")
        for database in (reference_db, current_db):
            private_bytes(database.with_suffix("." + suffix), raw)
    result = {"schema": "motionwright.manim.recovery-runtime.source-preparation/1", "context": current,
              "origin_context": ORIGIN, "input_sha256": recovered["input_sha256"],
              "recovery_receipt_sha256": sha(read(report)),
              "reference_binding": before_plan["binding"], "edited_binding": selected,
              "baseline_copy_initial_sha256": sha(read(baseline_db, 16 * 1024 * 1024)),
              "working_cas_provenance": "CAS_REHYDRATED_NEW_IDENTITY", "project_changes_applied": 0,
              "original_edit_report_recreated": False, "native_effect_invocations": 0,
              "equivalence_to_old_runtime": False, "automatic_retry": False}
    require(result["baseline_copy_initial_sha256"] == INPUTS["reopen-original-reference.sqlite3"][1],
            "recovery_runtime_baseline_is_original_copy")
    private_json(roots["source-recovery"] / "source-preparation.json", result)
    state = {"reference_database": reference_db, "database": current_db, "before_database": baseline_db,
            "references": originals, "recovery_path": report, "original_plan": before_plan,
            "prepared_plan": recovered["prepared_plan"], "preparation": result}
    if retained is not None:
        reference_db = roots["reference-data"] / "motionwright.sqlite3"
        private_bytes(reference_db, read(retained["database_original"], 4 * 1024 * 1024))
        state["reference_database"] = reference_db
        for suffix, expected in (("Montserrat-OFL.txt", LICENSE_SHA), ("Montserrat-source.json", ORIGIN_SHA)):
            raw = read(fixture / suffix, 64 * 1024)
            require(sha(raw) == expected, "retained_reference_original_font_attribution")
            private_bytes(reference_db.with_suffix("." + suffix), raw)
        original = retained_reference.phase_original(retained, state)
        rehydration_path = retained["derived"] / "reference-rehydration.json"
        rehydration = capture([str(source_binary), "source-cycle-rehydrate-reference",
            str(baseline_db), str(reference_db), str(retained["source_bundle"]), str(rehydration_path)],
            env=dict(os.environ), evidence=retained["derived"], label="retained-reference-rehydration",
            timeout=120, record_native=False)
        require(read_object(rehydration_path) == rehydration
                and rehydration.get("schema") == "motionwright.manim.editable.retained-reference-rehydration/1"
                and rehydration.get("status") == "REFERENCE_CAS_REHYDRATED_SOURCE_ONLY"
                and rehydration.get("canonical_stamp") == stamp(original["prepared"]["binding"])
                and rehydration.get("input_sha256") == {
                    "baseline_database": INPUTS["reopen-original-reference.sqlite3"][1],
                    "reference_database": retained["manifest"]["files"][retained_reference.DATABASE]["sha256"],
                    "original_bundle": INPUTS["original-source.mwedit.zip"][1]}
                and rehydration.get("source_sha256") == INPUTS["original-source.mwedit.zip"][1]
                and rehydration.get("source_bytes") == INPUTS["original-source.mwedit.zip"][0]
                and rehydration.get("source_closure") == original["source"]["descriptor"]["closure"]
                and rehydration.get("baseline_production_receipt_count") == 20
                and rehydration.get("retained_production_receipt_count") == 30
                and rehydration.get("working_cas_provenance") == "CAS_REHYDRATED_NEW_IDENTITY"
                and rehydration.get("new_cas_capture_count") == 1
                and rehydration.get("project_changes_applied") == 0,
                "retained_reference_new_cas_binding")
        require(all(rehydration.get(key) is True for key in (
                    "project_events_and_all_production_receipts_unchanged",
                    "source_assets_original_bytes_verified", "saved_source_bundle_unchanged"))
                and all(rehydration.get(key) is False for key in (
                    "original_cas_identity_claimed", "source_edit_performed", "native_execution_performed",
                    "provider_dispatch_performed", "original_edit_report_recreated",
                    "native_bundle_reopen_verified", "human_acceptance", "automatic_retry")),
                "retained_reference_rehydration_is_source_only")
        readback_path = retained["derived"] / "reference-source-readback.json"
        readback = capture([str(source_binary), "source-cycle-readback-reference", str(baseline_db),
            str(reference_db), str(retained["source_bundle"]), str(original["native_path"]),
            str(original["output_root"]), str(readback_path)], env=dict(os.environ),
            evidence=retained["derived"], label="retained-reference-readback", timeout=120, record_native=False)
        require(read_object(readback_path) == readback, "retained_reference_new_readback_stdout")
        checked_reference_readback(readback, original, {"phase": "original-reference", "history": []})
        original["phase_observation"] = retained_reference.record_source_readback(
            retained, readback, readback_path, rehydration_path, current)
        original["sealed_custody"] = retained_reference.verify_phase(original, current)
        state["retained_reference"] = original
    return state


def current_owner_has_phase(context):
    """A retained reference supplies media history, never this owner's binaries."""
    history, phase = context.get("history"), context.get("phase")
    owner, current = context.get("owner"), context.get("current")
    require(context.get("recovery_runtime_only") is True and type(history) is list
            and phase in PHASES and isinstance(owner, Path) and type(current) is dict
            and owner.is_dir() and not owner.is_symlink() and owner.resolve(strict=True) == owner,
            "recovery_runtime_owned_binary_history_context")
    index = PHASES.index(phase)
    first = 1 if retained_reference.selected() else 0
    require(index >= first and len(history) == index and context.get("short_phase") == SHORT_PHASES[index],
            "recovery_runtime_owned_binary_phase_order")
    if first:
        retained = history[0]
        require(type(retained) is dict and retained.get("retained_reference") is True
                and retained.get("current") == retained_reference.ORIGIN,
                "recovery_runtime_owned_binary_retained_origin")
    for position, previous in enumerate(history[first:], first):
        require(type(previous) is dict and previous.get("retained_reference") is not True
                and previous.get("current") == current and previous.get("owner") == owner
                and previous.get("phase_observation", {}).get("phase") == PHASES[position],
                "recovery_runtime_owned_binary_history_prefix")
    return len(history) > first


def new_phase_inputs(context, evidence, database, output_root, env, *, capture, source_binary):
    index = PHASES.index(context["phase"])
    require(context.get("recovery_runtime_only") is True and len(context["history"]) == index,
            "recovery_runtime_exact_four_phase_order")
    state = context["source_state"]
    expected_db = state["reference_database"] if index < 2 else state["database"]
    require(database == expected_db and context["database"] == expected_db,
            "recovery_runtime_separate_before_after_stores")
    recovered = read_object(state["recovery_path"])
    checked_recovery(recovered, state["recovery_path"], state["recovery_path"].with_suffix(".current.mwedit.zip"))
    plan = state["original_plan"] if index < 2 else state["prepared_plan"]
    selected = binding(plan["binding"])
    seed = {"schema": "motionwright.manim.recovery-runtime.saved-source-selection/1", **selected,
            "resource": stamp(selected)["resource"], "primitive_count": 3, "frame_count": 24,
            "font_sha256": FONT_SHA, "font_license_sha256": LICENSE_SHA,
            "native_execution_performed": False, "project_changes_applied": 0,
            "recovery_receipt_sha256": sha(read(state["recovery_path"]))}
    descriptor, input_path = None, None
    is_reopen = index in (1, 3)
    if is_reopen:
        previous = context["history"][index - 1]
        descriptor = {key: previous["source"]["descriptor"][key] for key in ("sha256", "bytes")}
        descriptor.update(root="output", relative_path="source-input.mwedit.zip")
        expected = INPUTS["original-source.mwedit.zip"] if index == 1 else (
            SOURCE_RECOVERY["export_bytes"], SOURCE_RECOVERY["export_sha256"])
        raw = read(previous["source"]["bundle_path"], 4 * 1024 * 1024)
        require((len(raw), sha(raw)) == expected
                and descriptor["bytes"] == len(raw) and descriptor["sha256"] == sha(raw),
                "recovery_runtime_reopen_uses_immediately_preceding_native_export")
        private_bytes(output_root / descriptor["relative_path"], raw)
        require(read(output_root / descriptor["relative_path"], 4 * 1024 * 1024) == raw,
                "recovery_runtime_physical_input_copy")
        input_path = evidence / "original-source-input.json"
        private_json(input_path, descriptor)
    for name, (size, digest) in INPUTS.items():
        require(sha(read(state["references"] / name, size)) == digest,
                "recovery_runtime_original_input_changed")
    private_json(evidence / "source-seed.json", seed)
    private_json(evidence / "source-preparation.json", state["preparation"])
    return {**state, "binding": checked_seed(seed), "seed": seed, "edit_path": None,
            "input_path": input_path, "input_descriptor": descriptor, "is_reopen": is_reopen}


def checked_reference_readback(value, current, context):
    phase_index = PHASES.index(context["phase"])
    require(phase_index in (0, 1)
            and value.get("schema") == "motionwright.manim.editable.retained-reference-readback/1"
            and value.get("status") == "REFERENCE_READBACK_REQUIRES_NATIVE_CUSTODY"
            and value.get("canonical_stamp") == stamp(current["prepared"]["binding"])
            and value.get("source_sha256") == INPUTS["original-source.mwedit.zip"][1]
            and value.get("source_bytes") == INPUTS["original-source.mwedit.zip"][0]
            and value.get("source_closure") == current["source"]["descriptor"]["closure"]
            and value.get("additional_native_receipts") == 2 * (phase_index + 1),
            "recovery_runtime_original_reference_readback")
    checked_receipt_classification(value, 2 * (phase_index + 1))
    require(all(value.get(key) is True for key in ("fresh_sqlite_readback", "saved_export_matches_native_carrier",
            "original_project_and_events_unchanged", "original_native_receipts_unchanged",
            "production_receipts_unchanged_during_readback"))
            and all(value.get(key) is False for key in ("source_mutation_performed", "native_execution_performed",
            "provider_dispatch_performed", "native_bundle_reopen_verified", "original_edit_report_recreated",
            "equivalence_to_old_runtime_claimed", "human_acceptance", "automatic_retry"))
            and value.get("project_changes_applied") == 0,
            "recovery_runtime_reference_is_observation_only")
    expected = []
    for phase in [*context["history"][:phase_index], current]:
        expected.extend(phase["native"][key] for key in ("original_start_receipt", "original_result_receipt"))
    canonical = lambda rows: sorted(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows)
    require(type(value.get("added_native_receipts")) is list
            and canonical(value["added_native_receipts"]) == canonical(expected),
            "recovery_runtime_only_the_new_reference_receipt_pairs")
    checked_native_link(value.get("current_native"), current)


def finish_phase(context, prepared, native, evidence, output_root, work_root, database,
                 env, *, capture, source_binary, python, runtime, tooling, launch):
    from native_manim_recovery_gate import check_runtime
    index = PHASES.index(context["phase"])
    require(read_object(evidence / EVIDENCE_NAME) == native, "recovery_runtime_original_native_stdout")
    source = retained_source(native, prepared, evidence, output_root, work_root, database)
    expected_plan = prepared["original_plan"] if index < 2 else prepared["prepared_plan"]
    expected_sha = INPUTS["original-source.mwedit.zip"][1] if index < 2 else SOURCE_RECOVERY["export_sha256"]
    require(source["plan"] == expected_plan and source["descriptor"]["sha256"] == expected_sha,
            "recovery_runtime_native_source_is_retained_saved_state")
    current = {"native": native, "native_path": evidence / EVIDENCE_NAME,
               "source": source, "prepared": prepared, "evidence": evidence}
    readback_path = evidence / ("reference-source-readback.json" if index < 2 else "recovered-source-readback.json")
    command = ([str(source_binary), "source-cycle-readback-reference", str(prepared["before_database"]),
                str(database), str(prepared["references"] / "original-source.mwedit.zip")]
               if index < 2 else [str(source_binary), "source-cycle-readback-recovered",
                str(prepared["before_database"]), str(database), str(prepared["recovery_path"])])
    value = capture([*command, str(current["native_path"]), str(output_root), str(readback_path)],
                    env=env, evidence=evidence, label="retained-source-readback", timeout=120)
    require(read_object(readback_path) == value, "recovery_runtime_readback_original_stdout")
    if index < 2:
        checked_reference_readback(value, current, context)
    else:
        checked_readback(value, prepared["recovery_path"], read_object(prepared["recovery_path"]),
                         current, context["phase"])
    runtime_after = check_runtime(context["current"])
    require(runtime_after == context["runtime_binding"], "recovery_runtime_changed_during_native_phase")
    private_json(evidence / "frozen-runtime-binding.json", runtime_after)
    pixel_report = None
    if index == 3:
        for previous in context["history"]:
            verify_phase(previous, context["current"])
        pixel_path = evidence / "recovery-runtime-pixels.json"
        pixel_env = dict(env, PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                         PYTHONPATH=str(runtime / "site-packages") + os.pathsep + str(tooling))
        pixel_report = capture([str(python), "-B", str(tooling / "native_manim_resume_pixels.py"),
            "four-phase", *(str(row["evidence"]) for row in context["history"]), str(evidence),
            str(prepared["recovery_path"]), str(pixel_path)], env=pixel_env,
            evidence=evidence, label="recovery-runtime-pixels", timeout=120)
        require(read_object(pixel_path) == pixel_report
                and pixel_report.get("status") == "ALL_96_PNGS_VERIFIED_REQUIRES_LINEAGE_CUSTODY"
                and pixel_report.get("original_reopen_all_frames_equal") is True
                and pixel_report.get("edited_reopen_all_frames_equal") is True
                and pixel_report.get("saved_text_change_observed_in_all_frames") is True,
                "recovery_runtime_all_four_physical_pixel_phases_required")
    observation = {"schema": "motionwright.manim.recovery-runtime.native-phase/1",
        "status": "PASS_REQUIRES_SEALED_CUSTODY", "phase": context["phase"], "context": context["current"],
        "origin_context": ORIGIN, "binding": prepared["binding"],
        "native_evidence_sha256": sha(read(evidence / EVIDENCE_NAME)),
        "source_retention_sha256": sha(read(evidence / "editable-source-retention.json")),
        "native_video_admission_sha256": sha(read(evidence / "native-video-admission.json")),
        "recovery_receipt_sha256": sha(read(prepared["recovery_path"])),
        "source_readback_sha256": sha(read(readback_path)), "runtime_binding": runtime_after,
        "native_request_id": native["request_id"], "native_job_ref": native["result"]["job_ref"],
        "source_bundle_input": prepared["input_descriptor"], "launch": launch,
        "native_effect_invocations": 1, "native_result_original_retained": True,
        "source_edit_performed": False, "project_changes_applied": 0,
        "original_edit_report_recreated": False, "original_committed_changes_recovered": 3,
        "new_reference_render": index < 2, "equivalence_to_old_runtime": False,
        "pixel_cycle_sha256": sha(read(evidence / "recovery-runtime-pixels.json")) if pixel_report else None,
        "human_acceptance": False, "automatic_retry": False}
    private_json(evidence / "result.json", observation)
    return {"source": source, "phase_observation": observation, "pixel_report": pixel_report}


def verify_phase(phase, current):
    if phase.get("retained_reference") is True:
        return retained_reference.verify_phase(phase, current)
    custody = verify_custody(phase, current)
    value = phase["phase_observation"]
    require(value.get("schema") == "motionwright.manim.recovery-runtime.native-phase/1"
            and value.get("phase") in PHASES and value.get("origin_context") == ORIGIN
            and value.get("source_edit_performed") is False and value.get("project_changes_applied") == 0
            and value.get("original_edit_report_recreated") is False
            and value.get("recovery_receipt_sha256") == SOURCE_RECOVERY["report_sha256"]
            and value.get("new_reference_render") is (PHASES.index(value["phase"]) < 2)
            and value.get("equivalence_to_old_runtime") is False,
            "recovery_runtime_phase_scope")
    return custody


def run_cycle(args, *, run_phase, validate_phase, capture, source_binary):
    from native_manim_editable_outer import OuterDeadline
    from native_manim_recovery_gate import check_runtime, check_source_gate
    current, deadline = hosted_context(), args.outer_deadline
    require(selected_scenario() == RECOVERY_SCENARIO and isinstance(deadline, OuterDeadline)
            and deadline.context == current and "GH_TOKEN" not in os.environ and "GITHUB_TOKEN" not in os.environ,
            "recovery_runtime_explicit_scoped_native_owner")
    deadline.admit_work()
    root, sealed, _ = scopes(current)
    require(args.evidence_root == root and args.sealed_cycle_root == sealed, "recovery_runtime_closed_scopes")
    recipient, observed = validate_phase(argparse.Namespace(evidence_root=root, sealed_originals=sealed))
    require(observed == current, "recovery_runtime_native_context")
    gate, _ = check_source_gate(current)
    runtime_binding = check_runtime(current)
    require(gate["runtime"] == runtime_binding, "recovery_runtime_gate_binding")
    files, _, _ = admitted_originals(current, frozen=True)
    retained_mode = retained_reference.selected()
    root.mkdir(mode=0o700)
    sealed.mkdir(mode=0o700)
    owner = owned_root(current)
    owner.mkdir(mode=0o700)
    private_json(root / "cycle-intent.json", {"schema": "motionwright.manim.recovery-runtime.cycle-intent/1",
        "context": current, "origin_context": ORIGIN,
        "phases": list(PHASES[1:] if retained_mode else PHASES), "native_effect_invocations": 3 if retained_mode else 4,
        "additional_reference_renders": 1 if retained_mode else 2, "source_recovery": SOURCE_RECOVERY,
        **({"retained_reference": retained_reference.contract()} if retained_mode else {}),
        "runtime_binding": runtime_binding, "outer_origin": deadline.binding(),
        "project_changes_authorized": 0, "equivalence_to_old_runtime": False,
        "original_edit_report_recreated": False, "next_phase_after_failure": False, "automatic_retry": False})
    retained = (retained_reference.materialize(files[retained_reference.CAPSULE], owner, current)
                if retained_mode else None)
    state = prepare_sources(current, owner, capture=capture, source_binary=source_binary, retained=retained)
    history = [state["retained_reference"]] if retained_mode else []
    remaining = list(enumerate(zip(PHASES, SHORT_PHASES)))[1 if retained_mode else 0:]
    for index, (phase, short) in remaining:
        deadline.admit_work()
        require(check_runtime(current) == runtime_binding, "recovery_runtime_changed_before_native_phase")
        selected = argparse.Namespace(scenario=RECOVERY_SCENARIO, evidence_root=root / phase,
                                      sealed_originals=sealed / (phase + ".mwseal"))
        require(validate_phase(selected) == (recipient, current), "recovery_runtime_phase_preflight")
        context = {"phase": phase, "short_phase": short, "history": history, "recovery_runtime_only": True,
                   "outer_deadline": deadline, "current": current, "owner": owner, "source_state": state,
                   "runtime_binding": runtime_binding,
                   "database": state["reference_database"] if index < 2 else state["database"]}
        observed = run_phase(selected, recovery_cycle=context)
        deadline.admit_work()
        observed["sealed_custody"] = verify_phase(observed, current)
        require(observed["owner"] == owner and observed["database"] == context["database"],
                "recovery_runtime_same_owned_saved_pair")
        require(all(observed["native"]["request_id"] != row["native"]["request_id"]
                    and observed["native"]["result"]["job_ref"] != row["native"]["result"]["job_ref"]
                    and observed["launch"]["session_sha256"] != row["launch"]["session_sha256"]
                    for row in history), "recovery_runtime_unique_real_jobs_and_sessions")
        history.append(observed)
    require(history[-1]["pixel_report"] is not None and check_runtime(current) == runtime_binding,
            "recovery_runtime_complete_four_phase_comparison")
    new_phases = history[1:] if retained_mode else history
    result = {"schema": "motionwright.manim.recovery-runtime.cycle-observation/1",
        "status": "NEW_REFERENCE_TWO_PLUS_EDITED_TWO_REQUIRES_INDEPENDENT_CUSTODY", "context": current,
        "origin_context": ORIGIN, "native_effect_invocations": 3 if retained_mode else 4,
        "additional_reference_renders": 1 if retained_mode else 2,
        "source_recovery": SOURCE_RECOVERY, "runtime_binding": runtime_binding,
        "phase_results": [{"phase": row["phase_observation"]["phase"],
                           "result_sha256": sha(read(row["evidence"] / "result.json")),
                           "ciphertext_sha256": sha(read(row["sealed_originals"], 256 * 1024 * 1024))} for row in new_phases],
        "fresh_process_and_session_per_phase": True, "same_saved_store_within_each_pair": True,
        "both_original_bundle_byte_preserving_native_reopens": True,
        "all_96_decoded_frames_checked": True, "saved_text_change_observed_in_all_frames": True,
        "pixel_cycle_sha256": sha(read(history[-1]["evidence"] / "recovery-runtime-pixels.json")),
        "project_changes_applied": 0, "original_edit_report_recreated": False,
        "old_result_replaced": False, "equivalence_to_old_runtime": False,
        "formal_G2_granted": False, "final_F_association": "PENDING", "human_acceptance": False,
        "automatic_retry": False}
    if retained_mode:
        result.update(schema="motionwright.manim.recovery-runtime.retained-reference-cycle/1",
            status="THREE_NEW_PHASES_COMPLETE_REFERENCE_RUNTIME_POSTCHECK_NOT_RETAINED",
            retained_reference=retained_reference.contract(),
            retained_reference_observation_sha256=sha(read(retained["derived"] / "observation.json")),
            reference_phase_complete=False, complete_cycle_verification=False,
            reference_post_native_runtime_observation="NOT_RETAINED")
    private_json(root / "cycle-result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--sealed-cycle-root", type=Path, required=True)
    args = parser.parse_args()
    import native_manim_e2e as native
    from native_manim_editable_outer import load_deadline
    args.outer_deadline = native._EDITABLE_OUTER_DEADLINE = load_deadline()
    signal.signal(signal.SIGTERM, native.editable_cancelled)
    signal.signal(signal.SIGINT, native.editable_cancelled)
    try:
        native._EDITABLE_STAGE = "recovery-runtime-original-inputs"
        hosted_context()
        native.exact_inputs()
        result = run_cycle(args, run_phase=native._run_single, validate_phase=native.editable_options,
                           capture=native.captured_json, source_binary=native.MW_EXAMPLE)
        print(json.dumps({"status": result["status"], "context": result["context"],
                          "native_effect_invocations": result["native_effect_invocations"]}))
    except BaseException as error:
        native._retain_editable_error(error)
        raise


if __name__ == "__main__":
    main()
