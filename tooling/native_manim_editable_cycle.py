#!/usr/bin/env python3
"""Four original Host jobs over one persisted editable source lifecycle.

The existing native wrapper owns provisioning and dispatch. This coordinator
orders original / source reopen / typed edit / edited source reopen, and stops
permanently after any failed or unconfirmed phase.
"""
from __future__ import annotations
import argparse
import copy
import io
import json
import os
from pathlib import Path
import zipfile

from native_manim_camera_evidence import (
    BINDING_KEYS, JOB, SHA, identity, object_bytes, private_bytes, private_json,
    read, read_object, require, sha,
)

PHASES = ("original", "reopen-original", "edited", "reopen-edited")
SHORT_PHASES = ("e1", "e2", "e3", "e4")
EVIDENCE_NAME = "motionwright-manim-evidence.json"
FONT_SHA = "0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7"
LICENSE_SHA = "8b7141c03fa4f8d44e6345d5d4931709290f0f67875e452e95ac1fd3a027802e"
ORIGIN_SHA = "2656ac500e19cee1a35c1293985c33741c9f1e030b458bdff967a4a02d2f8ab0"
ORIGINAL_TEXT, EDITED_TEXT = "x² + y² = r²", "x² + y² = 2r²"


def binding(value):
    result = {key: value.get(key) for key in BINDING_KEYS}
    for key in ("project_id", "generation", "scene_id", "deliverable_id"):
        identity(result[key])
    require(type(result["revision"]) is int and result["revision"] >= 0,
            "editable_saved_revision")
    return result


def stamp(value):
    return {"resource": "project:" + value["project_id"],
            "generation": value["generation"], "revision": value["revision"]}


def checked_seed(value):
    result = binding(value)
    require(value.get("primitive_count") == 3 and value.get("frame_count") == 24
            and value.get("native_execution_performed") is False
            and value.get("font_sha256") == FONT_SHA
            and value.get("font_license_sha256") == LICENSE_SHA
            and value.get("resource") == stamp(result)["resource"],
            "editable_modern_seed_required")
    return result


def checked_native_link(link, original):
    native = original["native"]
    require(type(link) is dict and read_object(original["native_path"]) == native
            and link.get("native_evidence_sha256") == sha(read(original["native_path"]))
            and link.get("request_id") == native.get("request_id")
            and link.get("original_start_receipt") == native.get("original_start_receipt")
            and link.get("original_result_receipt") == native.get("original_result_receipt")
            and link.get("source") == original["source"]["descriptor"]
            and link.get("native_frame_content_verified") is False,
            "editable_source_original_native_link")


def checked_edit(value, original):
    before, after = original["prepared"]["binding"], binding(value)
    require(value.get("schema") == "motionwright.manim.editable.source-cycle/1"
            and value.get("phase") == "edit-import"
            and value.get("original_stamp") == stamp(before)
            and all(after[key] == before[key] for key in BINDING_KEYS if key != "revision"),
            "editable_source_edit_binding")
    attached = value.get("attached_stamp", {})
    require(attached.get("resource") == stamp(before)["resource"]
            and attached.get("generation") == before["generation"]
            and type(attached.get("revision")) is int
            and attached["revision"] == before["revision"] + 2
            and after["revision"] == attached["revision"] + 1,
            "editable_source_edit_revision_order")
    require(value.get("original_text") == ORIGINAL_TEXT
            and value.get("edited_text") == EDITED_TEXT
            and value.get("production_receipts_unchanged") is True
            and value.get("fresh_sqlite_readback") is True
            and value.get("provider_dispatch_performed") is False
            and value.get("native_bundle_reopen_verified") is False
            and value.get("human_acceptance") is False,
            "editable_source_edit_authority")
    checked_seed(value)
    checked_native_link(value.get("original_native"), original)
    for key in ("original_source_asset_id", "edited_source_asset_id", "edited_node_id"):
        identity(value.get(key))
    require(value["original_source_asset_id"] != value["edited_source_asset_id"]
            and value.get("original_source_sha256") == original["source"]["descriptor"]["sha256"],
            "editable_original_asset_bytes")
    plan = copy.deepcopy(original["source"]["plan"])
    plan["binding"] = after
    nodes = [node for node in plan["nodes"] if node["node_id"] == value["edited_node_id"]]
    require(len(nodes) == 1 and nodes[0].get("typography", {}).get("text") == ORIGINAL_TEXT,
            "editable_one_text_node")
    nodes[0]["typography"]["text"] = EDITED_TEXT
    require(plan == value.get("prepared_plan"), "editable_only_saved_text_changed")
    preview, inspected = value.get("preview", {}), value.get("current_source_inspection", {})
    require(preview.get("expected") == attached
            and all(preview.get(key) == value[key] for key in (
                "original_source_asset_id", "edited_source_asset_id",
                "original_source_sha256", "edited_source_sha256"))
            and preview.get("native_execution_performed") is False
            and preview.get("imported_source_authority_used") is False
            and inspected.get("source_asset_id") == value["edited_source_asset_id"]
            and inspected.get("source_sha256") == value["edited_source_sha256"]
            and inspected.get("origin_binding") == before
            and inspected.get("current_binding") == after
            and inspected.get("current_plan") == plan
            and inspected.get("native_execution_performed") is False,
            "editable_typed_preview_and_cas_binding")
    return after


def new_phase_inputs(context, evidence, database, output_root, env, *, capture, source_binary):
    phase, history = context["phase"], context["history"]
    require(phase in PHASES and len(history) == PHASES.index(phase),
            "editable_phase_order")
    edit_path, input_path, descriptor = None, None, None
    if phase == "original":
        seed = capture([str(source_binary), "seed", str(database)], env=env,
                       evidence=evidence, label="source-seed")
        checked_seed(seed)
    elif phase == "edited":
        original = history[0]
        require(database == original["database"], "editable_original_store_required")
        edit_path = evidence / "source-cycle-edit.json"
        request = "edit-" + context["current"]["run_id"] + "-" + context["current"]["source_sha"][:12]
        seed = capture([
            str(source_binary), "source-cycle-edit", str(database),
            str(original["native_path"]), str(original["output_root"]), str(edit_path), request,
        ], env=env, evidence=evidence, label="source-cycle-edit", timeout=120)
        require(read_object(edit_path) == seed, "editable_edit_original_stdout")
        checked_edit(seed, original)
    else:
        source_phase = history[0] if phase == "reopen-original" else history[2]
        require(database == source_phase["database"], "editable_reopen_original_store_required")
        seed = source_phase["seed"]
        edit_path = source_phase["prepared"]["edit_path"]
        descriptor = {key: source_phase["source"]["descriptor"][key]
                      for key in ("sha256", "bytes")}
        descriptor.update(root="output", relative_path="source-input.mwedit.zip")
        raw = read(source_phase["source"]["bundle_path"], 34 * 1024 * 1024)
        require(sha(raw) == descriptor["sha256"] and len(raw) == descriptor["bytes"],
                "editable_reopen_original_export_changed")
        private_bytes(output_root / descriptor["relative_path"], raw)
        require(read(output_root / descriptor["relative_path"], 34 * 1024 * 1024) == raw,
                "editable_reopen_original_mount_copy")
        input_path = evidence / "original-source-input.json"
        private_json(input_path, descriptor)
    private_json(evidence / "source-seed.json", seed)
    return {"binding": checked_seed(seed), "seed": seed, "edit_path": edit_path,
            "input_path": input_path, "input_descriptor": descriptor,
            "is_reopen": phase in ("reopen-original", "reopen-edited")}


def retained_source(native, prepared, evidence, output_root, work_root, database):
    require(native.get("native_manim_e2e") == "PASS"
            and native.get("custody_error") is None
            and native.get("receipt_read_error") is None
            and binding(native) == prepared["binding"], "editable_native_saved_binding")
    result = native["result"]
    match = JOB.fullmatch(result.get("job_ref", ""))
    require(match is not None, "editable_original_native_job")
    token = match[1]
    verified = result.get("artifact", {}).get("editable_source", {})
    actual = result.get("result", {}).get("result", {}).get("data", {}).get("artifact", {})
    require(verified.get("native_object_getters_verified") is True
            and verified.get("source_bundle_inspected") is True
            and verified.get("source_reopened") is False
            and verified.get("observed_object_count") == 3
            and verified.get("observed_font_count") == 1
            and verified.get("frame_count_required") == 24,
            "editable_original_source_admission")
    descriptor, getters = verified.get("source", {}), verified.get("native_getters", {})
    require(descriptor == actual.get("editable_source")
            and getters == actual.get("editable_getters")
            and descriptor.get("relative_path") == f"mw_{token}.mwedit.zip"
            and getters.get("relative_path") == f"mw_{token}.objects.json",
            "editable_original_job_sidecars")
    bundle_path = output_root / descriptor["relative_path"]
    raw = read(bundle_path, 34 * 1024 * 1024)
    getter_raw = read(output_root / getters["relative_path"])
    require(sha(raw) == descriptor.get("sha256") and len(raw) == descriptor.get("bytes")
            and sha(getter_raw) == getters.get("sha256") and len(getter_raw) == getters.get("bytes"),
            "editable_original_sidecar_digest")
    retained = evidence / "editable-source-originals"
    retained.mkdir(mode=0o700, exist_ok=False)
    private_bytes(retained / "source.mwedit.zip", raw)
    private_bytes(retained / "native-objects.json", getter_raw)
    # Bounded data retention. Rust's canonical inspector remains the package authority.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        require(6 <= len(infos) <= 8 and len({row.filename for row in infos}) == len(infos)
                and not archive.comment, "editable_source_zip_roster")
        entries = {}
        total = 0
        for row in infos:
            require(row.compress_type == zipfile.ZIP_STORED and not row.flag_bits & 1
                    and 0 < row.file_size <= 16 * 1024 * 1024,
                    "editable_source_zip_bounded_entry")
            total += row.file_size
            require(total <= 34 * 1024 * 1024, "editable_source_zip_budget")
            entries[row.filename] = archive.read(row)
    plan_raw = entries.get("plan.json")
    require(type(plan_raw) is bytes and len(plan_raw) <= 256 * 1024,
            "editable_original_plan_bytes")
    plan = object_bytes(plan_raw)
    require(plan.get("profile_id") == "mw-manim-editable-text-v3"
            and plan.get("binding") == prepared["binding"]
            and plan.get("clock", {}).get("frame_count") == 24
            and plan.get("render_profile") == {"width": 640, "height": 360, "frame_rate": 12}
            and sha(plan_raw) == result.get("plan_sha256")
            and descriptor.get("closure", {}).get("plan_sha256") == sha(plan_raw),
            "editable_original_plan_source_binding")
    require(len(plan.get("fonts", [])) == 1 and plan["fonts"][0].get("sha256") == FONT_SHA
            and plan["fonts"][0].get("family") == "Montserrat"
            and plan["fonts"][0].get("format") == "ttf", "editable_original_font_identity")
    expected = {"plan.json", "scene.py", "fonts.conf", "render-config.json",
                "assets.lock.json", "manifest.json", f"fonts/{FONT_SHA}.ttf"}
    require(set(entries) == expected, "editable_original_package_entries")
    program = read(work_root / f"mw_{token}.py", 256 * 1024)
    require(entries["scene.py"] == program
            and sha(program) == descriptor["closure"]["source_sha256"]
            and sha(entries[f"fonts/{FONT_SHA}.ttf"]) == FONT_SHA,
            "editable_actual_program_and_font_bytes")
    native_getters = object_bytes(getter_raw)
    require(native_getters.get("source_file_sha256") == sha(program)
            and native_getters.get("fontconfig_sha256") == sha(entries["fonts.conf"]),
            "editable_observed_program_config_binding")
    (retained / "fonts").mkdir(mode=0o700, exist_ok=False)
    for name in sorted(entries):
        private_bytes(retained / name, entries[name])
    license_raw = read(database.with_suffix(".Montserrat-OFL.txt"), 64 * 1024)
    origin_raw = read(database.with_suffix(".Montserrat-source.json"), 64 * 1024)
    require(sha(license_raw) == LICENSE_SHA and sha(origin_raw) == ORIGIN_SHA
            and native.get("font_fixture_license_sha256") == LICENSE_SHA,
            "editable_original_font_license")
    private_bytes(retained / "Montserrat-OFL.txt", license_raw)
    private_bytes(retained / "Montserrat-source.json", origin_raw)
    if prepared["is_reopen"]:
        requested = prepared["input_descriptor"]
        observed_input = result["artifact"].get("editable_input_source")
        require(native.get("requested_original_source_bundle") == requested
                and actual.get("editable_input_source") == observed_input
                and type(observed_input) is dict
                and observed_input.get("input") == requested
                and observed_input.get("source_sha256") == sha(program)
                and observed_input.get("fontconfig_sha256") == sha(entries["fonts.conf"])
                and observed_input.get("fonts") == [{
                    "sha256": FONT_SHA, "bytes": len(entries[f"fonts/{FONT_SHA}.ttf"])}]
                and descriptor["sha256"] == requested["sha256"]
                and descriptor["bytes"] == requested["bytes"]
                and read(output_root / requested["relative_path"], 34 * 1024 * 1024) == raw
                and not list(output_root.glob("mw_font_*")),
                "editable_original_bundle_used_without_cas_font_staging")
    else:
        require(native.get("requested_original_source_bundle") is None
                and actual.get("editable_input_source") is None,
                "editable_generated_source_input_absent")
    observation = {
        "schema": "motionwright.manim.editable.original-source-retention/1",
        "descriptor": descriptor, "getters": getters, "plan": plan,
        "entries": [{"path": name, "bytes": len(data), "sha256": sha(data)}
                    for name, data in sorted(entries.items())],
        "source_program_equals_actual_native_input": True,
        "original_font_bytes_retained": True, "font_license_sha256": LICENSE_SHA,
        "font_origin_sha256": ORIGIN_SHA, "input_source": prepared["input_descriptor"],
        "native_frame_content_verified": False, "human_acceptance": False,
    }
    private_json(evidence / "editable-source-retention.json", observation)
    return {**observation, "bundle_path": bundle_path}


def checked_readback(value, edit, current, original, edit_path):
    require(value.get("schema") == "motionwright.manim.editable.source-cycle/1"
            and value.get("phase") == "fresh-readback"
            and value.get("project_id") == current["prepared"]["binding"]["project_id"]
            and value.get("canonical_stamp") == stamp(current["prepared"]["binding"])
            and value.get("edit_receipt_sha256") == sha(read(edit_path))
            and value.get("source_sha256") == edit["current_export_sha256"]
            and value.get("source_closure") == current["source"]["descriptor"]["closure"],
            "editable_source_readback_identity")
    require(all(value.get(key) is True for key in (
        "source_assets_original_bytes_verified", "saved_export_matches_native_carrier",
        "fresh_sqlite_readback", "original_native_receipts_unchanged",
        "production_receipts_unchanged"))
        and all(value.get(key) is False for key in (
            "native_execution_performed", "provider_dispatch_performed",
            "native_bundle_reopen_verified", "human_acceptance", "automatic_retry")),
        "editable_source_readback_scope")
    checked_native_link(value.get("original_native"), original)
    checked_native_link(value.get("current_native"), current)


def finish_phase(context, prepared, native, evidence, output_root, work_root, database,
                 env, *, capture, source_binary, python, runtime, tooling, launch):
    require(read_object(evidence / EVIDENCE_NAME) == native,
            "editable_original_native_stdout_binding")
    source = retained_source(native, prepared, evidence, output_root, work_root, database)
    current = {"native": native, "native_path": evidence / EVIDENCE_NAME,
               "source": source, "prepared": prepared, "evidence": evidence}
    edit_path = prepared["edit_path"]
    if edit_path is not None:
        edit = read_object(edit_path)
        checked_edit(edit, context["history"][0])
        require(source["plan"] == edit["prepared_plan"]
                and source["descriptor"]["sha256"] == edit["current_export_sha256"],
                "editable_native_carrier_matches_saved_edited_export")
        report = evidence / "source-cycle-readback.json"
        value = capture([
            str(source_binary), "source-cycle-readback", str(database), str(edit_path),
            str(current["native_path"]), str(output_root), str(report),
        ], env=env, evidence=evidence, label="source-cycle-readback", timeout=120)
        require(read_object(report) == value, "editable_readback_original_stdout")
        checked_readback(value, edit, current, context["history"][0], edit_path)
    pixel_report = None
    if context["phase"] == "reopen-edited":
        for previous in context["history"]:
            verify_phase(previous, context["current"])
        pixel_path = evidence / "editable-pixel-cycle.json"
        pixel_env = dict(env, PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                         PYTHONPATH=str(runtime / "site-packages") + os.pathsep + str(tooling))
        pixel_report = capture([
            str(python), "-B", str(tooling / "native_manim_editable_pixels.py"),
            *(str(row["evidence"]) for row in context["history"]), str(evidence),
            str(edit_path), str(pixel_path),
        ], env=pixel_env, evidence=evidence, label="editable-pixels", timeout=120)
        require(read_object(pixel_path) == pixel_report
                and pixel_report.get("status") == "PASS_REQUIRES_SEALED_CUSTODY"
                and pixel_report.get("frame_count_per_phase") == 24
                and pixel_report.get("original_reopen_all_frames_equal") is True
                and pixel_report.get("edited_reopen_all_frames_equal") is True
                and pixel_report.get("saved_text_change_observed_in_all_frames") is True,
                "editable_original_pixel_cycle_incomplete")
    observation = {
        "schema": "motionwright.manim.editable.native-phase/1",
        "status": "PASS_REQUIRES_SEALED_CUSTODY", "phase": context["phase"],
        "context": context["current"], "binding": prepared["binding"],
        "native_evidence_sha256": sha(read(evidence / EVIDENCE_NAME)),
        "source_retention_sha256": sha(read(evidence / "editable-source-retention.json")),
        "native_video_admission_sha256": sha(read(evidence / "native-video-admission.json")),
        "native_request_id": native["request_id"], "native_job_ref": native["result"]["job_ref"],
        "source_bundle_input": prepared["input_descriptor"], "launch": launch,
        "native_effect_invocations": 1, "native_result_original_retained": True,
        "source_edit_performed": context["phase"] == "edited",
        "original_pixel_cycle_sha256": (sha(read(evidence / "editable-pixel-cycle.json"))
                                         if pixel_report is not None else None),
        "human_acceptance": False, "automatic_retry": False,
    }
    private_json(evidence / "result.json", observation)
    return {"source": source, "phase_observation": observation, "pixel_report": pixel_report}


def verify_phase(phase, current):
    require(phase["current"] == current, "editable_phase_context_changed")
    result = read_object(phase["evidence"] / "result.json")
    require(result == phase["phase_observation"]
            and result.get("status") == "PASS_REQUIRES_SEALED_CUSTODY"
            and result.get("native_effect_invocations") == 1
            and result.get("native_result_original_retained") is True
            and result.get("automatic_retry") is False
            and result.get("native_evidence_sha256") == sha(read(phase["native_path"]))
            and result.get("source_retention_sha256") == sha(read(
                phase["evidence"] / "editable-source-retention.json"))
            and result.get("native_video_admission_sha256") == sha(read(
                phase["evidence"] / "native-video-admission.json")),
            "editable_phase_original_result_changed")
    custody = read_object(phase["evidence"] / "editable-custody.json")
    require(custody.get("schema") == "motionwright.manim.editable.sealed-custody/1"
            and custody.get("status") == "PASS" and custody.get("context") == current
            and custody.get("selected_originals_complete") is True
            and custody.get("native_result_retained") is True
            and custody.get("secondary_error_count") == 0,
            "editable_phase_sealed_custody_incomplete")
    if "sealed_custody" in phase:
        require(custody == phase["sealed_custody"], "editable_phase_custody_identity_changed")
    cipher = read(phase["sealed_originals"], 256 * 1024 * 1024)
    require(cipher.startswith(b"MWCISEAL1\n") and len(cipher) == custody.get("ciphertext_bytes")
            and sha(cipher) == custody.get("ciphertext_sha256"),
            "editable_phase_original_ciphertext_changed")
    return custody


def run_source_cycle(args, *, run_phase, validate_phase):
    evidence_root, sealed_root = args.evidence_root, args.sealed_cycle_root
    require(isinstance(evidence_root, Path) and isinstance(sealed_root, Path)
            and evidence_root != sealed_root and evidence_root not in sealed_root.parents
            and sealed_root not in evidence_root.parents, "editable_cycle_private_roots")
    require(args.sealed_originals is None and args.edited_evidence_root is None
            and args.sealed_edited is None, "editable_cycle_exclusive_arguments")
    # Validate canonical new parent scopes, recipient and original attempt before mkdir.
    envelope_args = argparse.Namespace(
        scenario="editable-source-cycle", evidence_root=evidence_root,
        sealed_originals=sealed_root,
    )
    recipient, current = validate_phase(envelope_args)
    evidence_root.mkdir(mode=0o700, exist_ok=False)
    sealed_root.mkdir(mode=0o700, exist_ok=False)
    phase_args = [argparse.Namespace(
        scenario="editable-source-cycle", evidence_root=evidence_root / phase,
        sealed_originals=sealed_root / (phase + ".mwseal"),
    ) for phase in PHASES]
    for item in phase_args:
        observed_recipient, observed_current = validate_phase(item)
        require(observed_recipient == recipient and observed_current == current,
                "editable_cycle_preflight_context_changed")
    private_json(evidence_root / "cycle-intent.json", {
        "schema": "motionwright.manim.editable.cycle-intent/1",
        "context": current, "phases": list(PHASES), "native_effect_invocations": 4,
        "automatic_retry": False, "next_phase_after_failure": False,
    })
    history = []
    for phase, short, item in zip(PHASES, SHORT_PHASES, phase_args):
        context = {"phase": phase, "short_phase": short, "history": history,
                   "current": current,
                   "owner": history[0]["owner"] if history else None,
                   "database": history[0]["database"] if history else None}
        observed = run_phase(item, editable_cycle=context)
        observed["sealed_custody"] = verify_phase(observed, current)
        require(observed["phase_observation"]["phase"] == phase,
                "editable_cycle_phase_identity")
        if history:
            require(observed["owner"] == history[0]["owner"]
                    and observed["database"] == history[0]["database"],
                    "editable_cycle_persisted_store_changed")
        require(all(observed["native"]["request_id"] != row["native"]["request_id"]
                    and observed["native"]["result"]["job_ref"] != row["native"]["result"]["job_ref"]
                    and observed["launch"]["session_sha256"] != row["launch"]["session_sha256"]
                    for row in history), "editable_cycle_original_job_or_session_reused")
        history.append(observed)
    final = history[-1]["pixel_report"]
    require(type(final) is dict and final.get("status") == "PASS_REQUIRES_SEALED_CUSTODY",
            "editable_cycle_original_pixels_absent")
    result = {
        "schema": "motionwright.manim.editable.source-cycle-observation/1", "status": "PASS",
        "context": current, "native_effect_invocations": 4,
        "phase_results": [{"phase": row["phase_observation"]["phase"],
                           "result_sha256": sha(read(row["evidence"] / "result.json")),
                           "ciphertext_sha256": sha(read(row["sealed_originals"], 256 * 1024 * 1024))}
                          for row in history],
        "fresh_process_and_session_per_phase": True,
        "same_saved_store": True, "typed_edit_import_and_fresh_readback": True,
        "original_bundle_byte_preserving_native_reopen": True,
        "edited_bundle_byte_preserving_native_reopen": True,
        "all_24_decoded_frames_equal_for_both_reopens": True,
        "edited_text_observed_in_all_frames": True,
        "pixel_cycle_sha256": sha(read(history[-1]["evidence"] / "editable-pixel-cycle.json")),
        "license_and_origin_retained_alongside_original_font": True,
        "motion_canvas_equivalence_verified": False,
        "all_manim_features_accepted": False, "human_acceptance": False,
        "automatic_retry": False,
    }
    private_json(evidence_root / "cycle-result.json", result)
    return result


def _outer_inputs(root):
    return {
        "cycle-intent": root / "cycle-intent.json",
        "cycle-result": root / "cycle-result.json",
        **{phase + "-custody": root / phase / "editable-custody.json" for phase in PHASES},
        "wrapper-stdout": root.parent / "manim-editable-cycle-wrapper.log",
    }


def seal_outer_cycle(root, sealed, recipient, current):
    import ci_private_evidence as engine
    engine.public_key(recipient)
    engine.validate_context(current)
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(current["run_attempt"] == "1"
            and root == temporary / ("manim-editable-private-" + current["source_sha"])
            and sealed == temporary / ("manim-editable-sealed-" + current["source_sha"]),
            "editable_outer_original_scope")
    for path in (root, sealed):
        require(not path.is_symlink() and path.resolve() == path,
                "editable_outer_canonical_scope")
    sealed.mkdir(mode=0o700, exist_ok=True)
    cipher_path = sealed / "cycle-outer.mwseal"
    receipt_path = sealed / "cycle-outer.public.json"
    require(not any(p.exists() or p.is_symlink() for p in (cipher_path, receipt_path)),
            "editable_outer_no_overwrite")
    stage = temporary / ("manim-editable-outer-" + current["source_sha"])
    stage.mkdir(mode=0o700, exist_ok=False)
    roots, selected, failed = {}, {}, []
    for label, source in _outer_inputs(root).items():
        folder = roots[label] = stage / label
        try:
            raw = engine.regular(source, 4 * 1024 * 1024)[0]
            folder.mkdir(mode=0o700, exist_ok=False)
            engine.new_bytes(folder / "original", raw)
            selected[label] = {"bytes": len(raw), "sha256": sha(raw)}
        except Exception:
            failed.append(label)
            if folder.exists() or folder.is_symlink():
                roots[label + "-partial"] = folder
            roots[label] = stage / (label + "-unavailable")
    snapshot, complete = engine.gather_snapshot(roots, preserve_partial=True)
    ciphertext = engine.encrypt_snapshot(snapshot, recipient, current)
    engine.new_bytes(cipher_path, ciphertext)
    receipt = {
        "schema": "motionwright.manim.editable.outer-custody-public/1",
        "context": current, "recipient_sha256": recipient["sha256"],
        "ciphertext_sha256": sha(ciphertext), "ciphertext_bytes": len(ciphertext),
        "selected_originals_complete": complete and not failed and len(selected) == len(roots),
        "original_files": selected, "failed_labels": failed,
        "native_effect_invocations": 0, "automatic_retry": False,
    }
    engine.new_json(receipt_path, receipt)
    return receipt


def verified_outer_cycle(root, sealed, current, recipient_sha256):
    value = read_object(sealed / "cycle-outer.public.json")
    require(set(value) == {
        "schema", "context", "recipient_sha256", "ciphertext_sha256", "ciphertext_bytes",
        "selected_originals_complete", "original_files", "failed_labels",
        "native_effect_invocations", "automatic_retry",
    }, "editable_outer_public_fields")
    require(value["schema"] == "motionwright.manim.editable.outer-custody-public/1"
            and value["context"] == current and value["recipient_sha256"] == recipient_sha256
            and value["selected_originals_complete"] is True and value["failed_labels"] == []
            and type(value["native_effect_invocations"]) is int
            and value["native_effect_invocations"] == 0 and value["automatic_retry"] is False,
            "editable_outer_complete_original_context")
    inputs = _outer_inputs(root)
    require(type(value["original_files"]) is dict and set(value["original_files"]) == set(inputs),
            "editable_outer_closed_selection")
    for label, source in inputs.items():
        raw = read(source, 4 * 1024 * 1024)
        row = value["original_files"][label]
        require(type(row) is dict and type(row.get("bytes")) is int
                and row == {"bytes": len(raw), "sha256": sha(raw)},
                "editable_outer_original_changed")
    ciphertext = read(sealed / "cycle-outer.mwseal", 64 * 1024 * 1024)
    require(ciphertext.startswith(b"MWCISEAL1\n")
            and type(value["ciphertext_bytes"]) is int
            and len(ciphertext) == value["ciphertext_bytes"]
            and sha(ciphertext) == value["ciphertext_sha256"],
            "editable_outer_ciphertext_changed")
    return value
