#!/usr/bin/env python3
"""Coordinate two existing camera producers around a persisted, typed source edit.

No Broker, provider implementation, new authority, retry or synthetic media is
introduced here. Each producer owns its own once-only custody scope.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def run_source_cycle(args, *, run_phase, validate_phase, publish_phase):
    """Fail closed between phases; an exception never starts a later phase."""
    names = ("evidence_root", "sealed_originals", "edited_evidence_root", "sealed_edited")
    paths = [getattr(args, name, None) for name in names]
    _require(all(isinstance(path, Path) for path in paths), "camera_cycle_paths_required")
    _require(len(set(paths)) == 4, "camera_cycle_paths_must_be_distinct")
    for left in paths:
        for right in paths:
            if left != right:
                _require(left not in right.parents, "camera_cycle_paths_must_not_overlap")
    original_args = argparse.Namespace(
        scenario="camera120", evidence_root=args.evidence_root,
        sealed_originals=args.sealed_originals, source_cycle_enabled=True,
    )
    edited_args = argparse.Namespace(
        scenario="camera120", evidence_root=args.edited_evidence_root,
        sealed_originals=args.sealed_edited, source_cycle_enabled=True,
    )
    # Check both new scopes before the first effect, without creating either.
    first_recipient, first_context = validate_phase(original_args)
    second_recipient, second_context = validate_phase(edited_args)
    _require(first_recipient == second_recipient and first_context == second_context,
             "camera_cycle_context_changed_before_dispatch")
    original = run_phase(original_args)
    publish_phase(original["evidence"], first_context["source_sha"],
                  first_context["semwright_sha"], original["sealed_originals"],
                  scenario="camera120-source-cycle", current=first_context)
    edited = run_phase(edited_args, source_cycle=original)
    publish_phase(edited["evidence"], first_context["source_sha"],
                  first_context["semwright_sha"], edited["sealed_originals"],
                  fixture="edited1040", scenario="camera120-source-cycle", current=first_context)
    from native_manim_camera_evidence import read_object
    _require(read_object(edited["evidence"] / "source-cycle-observation.json")
             == edited["cycle_observation"], "camera_cycle_durable_observation_changed")
    _require(edited["cycle_observation"]["source_cycle_observation"] == "PASS_REQUIRES_SEALED_CUSTODY",
             "camera_cycle_observation_missing")
    return edited["cycle_observation"]


def reject_original_media(prepared, original, evidence, ffprobe, ffmpeg):
    """A real original MP4 must reach the edited geometric comparator and fail."""
    from native_manim_camera_evidence import MEDIA_NAME, private_json, read, sha
    from native_manim_camera_pixels import (
        FRAME_BYTES, FRAMES, READBACK_SAMPLES, assert_centroids, checked_input,
        decoded_rgb120, digest, probe_media, verified_plan,
    )
    plan = verified_plan(prepared["plan_path"], prepared["plan_sha256"],
                         fixture="edited1040")
    media = original["evidence"] / MEDIA_NAME
    expected = original["native"]["result"]["artifact"]["sha256"]
    checked_input(media, 128 * 1024 * 1024)
    _require(digest(media) == expected, "camera_cycle_original_media_changed")
    video = probe_media(media, ffprobe)
    rgb = decoded_rgb120(media, ffmpeg)
    view = memoryview(rgb)
    samples = {
        frame: view[frame * FRAME_BYTES:(frame + 1) * FRAME_BYTES]
        for frame in READBACK_SAMPLES
    }
    expected_error = "native object center diverges from saved camera trajectory"
    try:
        assert_centroids(plan, samples)
    except AssertionError as error:
        _require(str(error) == expected_error,
                 "camera_cycle_negative_failed_before_expected_geometry")
    else:
        raise ValueError("camera_cycle_original_media_satisfied_edited_geometry")
    _require(digest(media) == expected, "camera_cycle_original_media_changed_after_decode")
    observation = {
        "schema": "motionwright.manim.camera.original-media-negative/1",
        "status": "EXPECTED_GEOMETRIC_REJECTION",
        "original_media_sha256": expected,
        "edited_plan_sha256": prepared["plan_sha256"],
        "original_native_evidence_sha256": sha(read(original["native_path"])),
        "decoded_rgb_sha256": hashlib.sha256(rgb).hexdigest(),
        "decoded_frame_count": FRAMES,
        "video": video,
        "rejection": expected_error,
        "metadata_and_original_digest_verified_before_geometry": True,
        "synthetic_media_used": False,
        "provider_dispatch_performed": False,
        "automatic_retry": False,
        "human_acceptance": False,
    }
    private_json(evidence / "original-media-edited-geometry-negative.json", observation)
    return observation


def _verify_native_link(link, native, native_path):
    """Join a Rust source observation to the normalized, retained native result."""
    from native_manim_camera_evidence import read, read_object, sha
    _require(type(link) is dict and set(link) == {
        "native_evidence_sha256", "request_id", "camera_source",
        "original_start_receipt", "original_result_receipt",
    }, "camera_cycle_native_link_shape")
    _require(read_object(native_path) == native
             and link["native_evidence_sha256"] == sha(read(native_path))
             and read_object(native_path.parent / "result.json")["native_evidence_sha256"]
                 == link["native_evidence_sha256"],
             "camera_cycle_original_native_bytes_changed")
    _require(link["request_id"] == native["request_id"]
             and link["original_start_receipt"] == native["original_start_receipt"]
             and link["original_result_receipt"] == native["original_result_receipt"]
             and link["camera_source"] == native["result"]["artifact"]["camera_source"],
             "camera_cycle_native_link_identity")
    source = link["camera_source"]
    _require(native.get("custody_error") is None and native.get("receipt_read_error") is None
             and native["result"]["artifact"].get("camera_source_custody_error") is None
             and type(source) is dict
             and source.get("media_type")
                 == "application/vnd.motionwright.manim-planar-camera-source+zip"
             and native["result"]["plan_sha256"] == source["closure"]["plan_sha256"],
             "camera_cycle_native_carrier_custody")
    return source


def _checked_edit_contract(value, original):
    from native_manim_camera_evidence import identity, seed_binding
    binding = seed_binding(value, fixture="edited1040")
    before = original["prepared"]["binding"]
    expected = {key: before[key] for key in ("generation", "revision")}
    expected["resource"] = "project:" + before["project_id"]
    _require(value.get("original_stamp") == expected
             and all(binding[key] == before[key]
                     for key in ("project_id", "generation", "scene_id", "deliverable_id")),
             "camera_cycle_edit_original_binding")
    attached = value.get("attached_stamp", {})
    _require(attached.get("resource") == expected["resource"]
             and attached.get("generation") == expected["generation"]
             and type(attached.get("revision")) is int
             and attached["revision"] > expected["revision"]
             and binding["revision"] == attached["revision"] + 1,
             "camera_cycle_edit_stamp_order")
    for key in ("original_source_asset_id", "edited_source_asset_id"):
        identity(value.get(key))
    _require(value["original_source_asset_id"] != value["edited_source_asset_id"],
             "camera_cycle_source_assets_not_distinct")
    for key in ("original_source_sha256", "edited_source_sha256",
                "current_export_bundle_sha256"):
        raw = value.get(key)
        _require(type(raw) is str and len(raw) == 64
                 and all(c in "0123456789abcdef" for c in raw),
                 "camera_cycle_source_digest")
    source = _verify_native_link(value.get("original_native"), original["native"],
                                 original["native_path"])
    _require(value["original_source_sha256"] == source["sha256"],
             "camera_cycle_original_source_changed")
    inspected = value.get("current_source_inspection", {})
    plan = value.get("prepared_program", {}).get("prepared", {}).get("source")
    compiler = value.get("prepared_program", {}).get("trusted_compiler", {})
    _require(inspected.get("source_asset_id") == value["edited_source_asset_id"]
             and inspected.get("source_sha256") == value["edited_source_sha256"]
             and inspected.get("origin_binding") == before
             and inspected.get("current_binding") == binding
             and inspected.get("current_plan") == plan
             and type(plan) is dict and plan.get("binding") == binding
             and inspected.get("current_closure", {}).get("plan_sha256") == compiler.get("plan_sha256")
             and inspected.get("current_closure", {}).get("source_sha256") == compiler.get("source_sha256")
             and inspected.get("native_execution_performed") is False
             and inspected.get("imported_source_authority_used") is False,
             "camera_cycle_edit_source_inspection")
    preview = value.get("preview", {})
    _require(preview.get("expected") == attached
             and all(preview.get(key) == value[key] for key in (
                 "original_source_asset_id", "edited_source_asset_id",
                 "original_source_sha256", "edited_source_sha256",
             ))
             and preview.get("native_execution_performed") is False
             and preview.get("imported_source_authority_used") is False,
             "camera_cycle_edit_preview_identity")
    _require(all(value.get(key) is True for key in (
        "original_native_receipts_unchanged", "production_receipt_window_unchanged",
    )) and value.get("production_receipt_window_limit") == 256,
        "camera_cycle_edit_receipt_window")
    return binding


def prepare_edited_seed(original, evidence, database, env, *, source_binary, capture):
    """Invoke the existing source-only Rust editor against the same saved store."""
    from native_manim_camera_evidence import private_json, read_object
    _require(database == original["database"], "camera_cycle_database_changed")
    binding = original["prepared"]["binding"]
    stamp = {key: binding[key] for key in ("generation", "revision")}
    stamp["resource"] = "project:" + binding["project_id"]
    stamp_path = evidence / "source-cycle-original-stamp.json"
    private_json(stamp_path, stamp)
    receipt_path = evidence / "source-cycle-edit.json"
    request = "edit-" + original["current"]["run_id"] + "-" + original["current"]["source_sha"][:12]
    value = capture([
        str(source_binary), "source-cycle-edit", str(database), str(stamp_path),
        binding["project_id"], binding["scene_id"], binding["deliverable_id"],
        str(receipt_path), str(original["native_path"]), str(original["output_root"]),
        request,
    ], env=env, evidence=evidence, label="source-cycle-edit", timeout=120)
    _require(read_object(receipt_path) == value, "camera_cycle_edit_stdout_differs")
    _checked_edit_contract(value, original)
    return value


def verify_edited_source_readback(original, prepared, native, evidence, database,
                                 output_root, env, *, source_binary, capture):
    """Observe persisted CAS and both exact native receipts in a fresh process."""
    from native_manim_camera_evidence import EVIDENCE_NAME, private_json, read, read_object, sha
    _require(database == original["database"], "camera_cycle_database_changed")
    edit_path = evidence / "source-cycle-edit.json"
    edit = read_object(edit_path)
    binding = prepared["binding"]
    _require(_checked_edit_contract(edit, original) == binding,
             "camera_cycle_prepared_edit_binding")
    stamp = {key: binding[key] for key in ("generation", "revision")}
    stamp["resource"] = "project:" + binding["project_id"]
    stamp_path = evidence / "source-cycle-edited-stamp.json"
    private_json(stamp_path, stamp)
    receipt_path = evidence / "source-cycle-readback.json"
    value = capture([
        str(source_binary), "source-cycle-readback", str(database), str(stamp_path),
        binding["project_id"], binding["scene_id"], binding["deliverable_id"],
        str(receipt_path), str(edit_path), str(evidence / EVIDENCE_NAME), str(output_root),
    ], env=env, evidence=evidence, label="source-cycle-readback", timeout=120)
    _require(read_object(receipt_path) == value, "camera_cycle_readback_stdout_differs")
    _require(value.get("schema") == "motionwright.manim.camera.source-cycle/1"
             and value.get("phase") == "source-cycle-readback"
             and value.get("canonical_stamp") == stamp
             and all(value.get(key) == binding[key]
                     for key in ("project_id", "scene_id", "deliverable_id")),
             "camera_cycle_readback_binding")
    _require(all(value.get(key) is True for key in (
        "fresh_sqlite_readback", "original_native_receipts_unchanged",
        "production_receipt_window_unchanged", "saved_export_matches_native_carrier",
    )) and value.get("production_receipt_window_limit") == 256,
        "camera_cycle_readback_incomplete")
    _require(all(value.get(key) is False for key in (
        "source_changed_by_example", "native_execution_performed",
        "native_compiler_registered", "provider_dispatch_performed",
        "new_render_job_created", "imported_source_authority_used",
        "fresh_registered_source_reopen_verified",
        "native_video_frame_content_verified", "human_acceptance",
    )), "camera_cycle_readback_authority_changed")
    preserved = ("original_source_asset_id", "edited_source_asset_id",
                 "original_source_sha256", "edited_source_sha256",
                 "source_project_sha256", "current_export_bundle_sha256")
    _require(all(value.get(key) == edit[key] for key in preserved)
             and value.get("edit_receipt_sha256") == sha(read(edit_path)),
             "camera_cycle_source_history_changed")
    original_request = original["native"]["request_id"]
    edited_request = native["request_id"]
    _require(original_request != edited_request, "camera_cycle_native_request_reused")
    carrier = _verify_native_link(value.get("edited_native"), native, evidence / EVIDENCE_NAME)
    inspected = value.get("current_source_inspection", {})
    _require(inspected == edit["current_source_inspection"]
             and inspected.get("current_binding") == binding
             and inspected.get("current_plan") == prepared["plan"]
             and inspected.get("current_closure") == carrier.get("closure")
             and carrier.get("sha256") == value.get("current_export_bundle_sha256")
             and carrier.get("closure", {}).get("plan_sha256") == prepared["plan_sha256"]
             and carrier.get("closure", {}).get("source_sha256") == prepared["program_sha256"],
             "camera_cycle_edited_carrier_differs_from_source")
    negative_path = evidence / "original-media-edited-geometry-negative.json"
    negative = read_object(negative_path)
    _require(negative.get("status") == "EXPECTED_GEOMETRIC_REJECTION"
             and negative.get("edited_plan_sha256") == prepared["plan_sha256"]
             and negative.get("original_media_sha256")
                 == original["native"]["result"]["artifact"]["sha256"],
             "camera_cycle_negative_identity")
    observation = {
        "schema": "motionwright.manim.camera.source-cycle-observation/1",
        "source_cycle_observation": "PASS_REQUIRES_SEALED_CUSTODY",
        "motionwright_sha": original["current"]["source_sha"],
        "semwright_sha": original["current"]["semwright_sha"],
        "original_request_id": original_request,
        "edited_request_id": edited_request,
        "original_native_evidence_sha256": sha(read(original["native_path"])),
        "edited_native_evidence_sha256": sha(read(evidence / EVIDENCE_NAME)),
        "edit_receipt_sha256": sha(read(edit_path)),
        "fresh_source_readback_sha256": sha(read(receipt_path)),
        "original_media_negative_sha256": sha(read(negative_path)),
        "original_plan_sha256": original["prepared"]["plan_sha256"],
        "edited_plan_sha256": prepared["plan_sha256"],
        "saved_camera_source_cycle_verified": True,
        "same_sqlite_store_reopened_in_fresh_process": True,
        "current_export_equals_second_native_carrier": True,
        "original_native_receipts_unchanged": True,
        "native_effect_invocations": 2,
        "fresh_requests_and_distinct_once_scopes": True,
        "source_export_grants_native_authority": False,
        "motion_canvas_equivalence_verified": False,
        "voice_or_caption_mixing_verified": False,
        "human_acceptance": False,
        "automatic_retry": False,
    }
    private_json(evidence / "source-cycle-observation.json", observation)
    return observation
