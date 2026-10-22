"""Closed camera120 evidence roles and projections for native custody /2.

No provider commands, SQLite reads, recursive directory discovery, encryption,
upload or acceptance logic lives here. Original diagnostic bytes remain local.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile
import base64

import native_evidence_projection as selected
from native_evidence_projection import Array, Field, Literal, Nullable, Number, Object, String

SCHEMA = "motionwright.manim.camera.sealed-custody/2"
MANIFEST_NAME = "camera-selected-manifest.json"
SOURCE_PATHS = (
    "SOURCE_LOCK.json",
    ".github/workflows/native-manim-e2e.yml",
    "tooling/ci_private_evidence.py",
    "tooling/native_evidence_projection.py",
    "tooling/native_manim_evidence_selection.py",
    "tooling/native_manim_e2e.py",
    "tooling/native_manim_camera_evidence.py",
    "tooling/native_manim_camera_cycle.py",
    "tooling/native_manim_camera_pixels.py",
    "tooling/native_scene_public_artifact.py",
    "crates/native/examples/native-manim-camera-seed.rs",
    "crates/native/examples/native-manim-e2e.rs",
    "crates/native/examples/native-manim-camera-e2e.rs",
    "crates/native/src/production.rs",
    "crates/native/src/manim_camera_projection.rs",
    "crates/native/src/lib.rs",
    "crates/manim-profile/src/lib.rs",
    "crates/manim-profile/src/planar_camera.rs",
    "crates/driver-manim-community/src/main.rs",
    "crates/driver-manim-community/src/runtime_runner.rs",
    "crates/storage/src/production.rs",
    "crates/storage/src/lib.rs",
    "crates/service/src/lib.rs",
)
PRODUCER_PATHS = (
    "tooling/native_manim_e2e.py",
    "tooling/native_manim_camera_evidence.py",
    "tooling/native_manim_evidence_selection.py",
    "crates/native/examples/native-manim-camera-seed.rs",
    "crates/native/examples/native-manim-e2e.rs",
    "crates/native/examples/native-manim-camera-e2e.rs",
)


def obj(**fields):
    return Object({key: value if isinstance(value, Field) else Field(value)
                   for key, value in fields.items()})


def opt(value):
    return Field(value, required=False)


def optional_object(rule):
    return Object({key: Field(field.rule, required=False)
                   for key, field in rule.fields.items()})


SHA = String(64, r"[0-9a-f]{64}")
GIT = String(40, r"[0-9a-f]{40}")
UUID = String(36, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
UINT = Number(0, 2**63-1, integer=True)
FINITE = Number(-1e12, 1e12)
FALSE, TRUE = Literal(False), Literal(True)
REQUEST = String(160, r"[A-Za-z0-9_.:-]+")
RESOURCE = String(44, r"project:[0-9a-f-]{36}")
HEX_COLOR = String(7, r"#[0-9A-Fa-f]{6}")
BINDING = obj(project_id=UUID, generation=UUID, revision=UINT,
              scene_id=UUID, deliverable_id=UUID)
STAMP = obj(resource=RESOURCE, generation=UUID, revision=UINT)
RATIONAL = obj(num=String(32, r"-?[0-9]+"), den=String(32, r"[1-9][0-9]*"))
PROFILE = obj(width=Literal(640), height=Literal(360), frame_rate=Literal(30))
CLOCK = obj(timeline_start=RATIONAL, duration=RATIONAL, frame_rate=RATIONAL,
            project_start_frame=UINT, project_end_frame_exclusive=UINT,
            local_start_frame=Literal(0), frame_count=Literal(120))
CAMERA = obj(center_x=FINITE, center_y=FINITE, zoom=FINITE,
             rotation_deg=FINITE, safe_margin=Number(0, 1))
CAMERA_MOVE = obj(id=UUID, scene_id=UUID, component_version=Literal(2),
    start_center_x=FINITE, start_center_y=FINITE, end_center_x=FINITE,
    end_center_y=FINITE, duration_frames=Literal(120))
SHAPE = obj(node_id=UUID, display_name=String(256), source_kind=Literal("rectangle"),
    canonical_ordinal=UINT, z_index=Number(-(2**31), 2**31-1, integer=True),
    x_px=FINITE, y_px=FINITE, width_px=Number(0, 1920), height_px=Number(0, 1080),
    fill_hex=HEX_COLOR)
PLAN = obj(schema_version=Literal(4), profile_id=Literal("mw-manim-planar-camera-v1"),
    output_role=Literal("scene_video_contribution_without_audio_or_captions"),
    binding=BINDING, clock=CLOCK, render_profile=PROFILE,
    canvas=obj(width=Literal(1920), height=Literal(1080),
               coordinate_space=Literal("project_pixels")),
    background=obj(kind=Literal("solid"), color=HEX_COLOR, opacity=Literal(1)),
    camera=CAMERA, reframe=CAMERA_MOVE, nodes=Array(SHAPE, 1, 1))
CLOSURE_ENTRY = obj(path=String(160, r"[a-zA-Z0-9_.+/-]+"), bytes=UINT, sha256=SHA)
CLOSURE = obj(plan_schema_version=Literal(4),
    compiler_contract_id=Literal("motionwright.manim.rust.planar_camera.v1.0"),
    plan_sha256=SHA, source_sha256=SHA, config_sha256=SHA,
    assets_lock_sha256=SHA, manifest_sha256=SHA, entries=Array(CLOSURE_ENTRY, 32, 1))
CARRIER = obj(relative_path=String(64, r"mw_[0-9a-f]{32}\.mwcam\.zip"),
    sha256=SHA, bytes=Number(1, 2*1024*1024, integer=True),
    media_type=Literal("application/vnd.motionwright.manim-planar-camera-source+zip"),
    closure=CLOSURE)
# `verified_path` is deliberately absent: routing is not a custody capability.
RECEIPT = obj(id=UUID, project_id=UUID, generation=UUID, revision=UINT,
    request_id=REQUEST, request_sha256=SHA,
    command=String(96, values=("driver.manim-community.camera.render.start",
        "driver.manim-community.render.status", "driver.manim-community.render.result",
        "driver.manim-community.render.cancel")),
    stage=String(32, values=("dispatching", "completed", "failed_known", "outcome_unknown")),
    created_at=String(64, r"[0-9TZt:z+ .-]+"))
ERROR = obj(code=String(64, r"[A-Za-z][A-Za-z0-9_]{0,63}"), outcome_known=bool)
ARTIFACT = obj(relative_path=String(128, r"videos/mw_[0-9a-f]{32}/360p30/mw_[0-9a-f]{32}\.mp4"),
    sha256=SHA, bytes=Number(1, 128*1024*1024, integer=True),
    camera_getters_sha256=SHA, camera_source=opt(CARRIER))
NATIVE_RESULT = obj(renderer=Literal("manim-community"),
    native_driver=Literal("driver:manim-community"),
    runtime_contract=Literal("Manim Community 0.21.0"), project_revision=UINT,
    scene_id=UUID, job_ref=String(38, r"manim-[0-9a-f]{32}"), plan_sha256=SHA,
    profile=PROFILE, artifact=ARTIFACT)
NATIVE = obj(project_id=UUID, resource=RESOURCE, generation=UUID, revision=UINT,
    scene_id=UUID, deliverable_id=UUID, request_id=REQUEST,
    native_manim_camera_dispatch=String(16, values=("PASS", "FAIL", "UNKNOWN", "UNVERIFIED")),
    recent_receipts=Nullable(Array(RECEIPT, 256)), receipt_read_error=Nullable(ERROR),
    receipt_window_limit=Literal(256), decoded_frames_verified=FALSE,
    motion_acceptance=Literal("NOT_ASSESSED"),
    original_start_receipt=opt(RECEIPT), original_result_receipt=opt(RECEIPT),
    result=opt(NATIVE_RESULT), error=opt(ERROR),
    failure_stage=opt(String(48, values=("receipt_read", "native_dispatch", "database_reopen",
        "project_reopen", "start_receipt_reopen", "result_receipt_reopen", "receipt_identity",
        "post_dispatch_verification", "evidence_write"))))
NATIVE_LINK = obj(native_evidence_sha256=SHA, request_id=REQUEST, camera_source=CARRIER,
                  original_start_receipt=RECEIPT, original_result_receipt=RECEIPT)
COMPILER = obj(compiler_id=Literal("motionwright.manim.rust.planar_camera.v1.0"),
    plan_sha256=SHA, source_sha256=SHA, native_execution_performed=FALSE,
    runtime_sample_pts_verified=FALSE)
PREPARED = obj(source=PLAN, source_plan_sha256=SHA, source_plan_bytes=UINT,
    native_execution_performed=FALSE, permissions_verified=FALSE,
    native_source_compiler_registered=FALSE, imported_source_authority_used=FALSE,
    losses=Array(String(192), 32))
PROGRAM = obj(prepared=PREPARED, trusted_compiler=COMPILER,
    original_source_snapshot_checked=TRUE, program_semantically_bound_to_source=TRUE,
    native_execution_performed=FALSE, native_source_compiler_registered=FALSE,
    native_permissions_verified=FALSE)
SEED = obj(schema=Literal("motionwright.native.manim-camera-seed/1"),
    project_id=UUID, generation=UUID, revision=UINT, scene_id=UUID,
    deliverable_id=UUID, reframe_id=UUID, source_project_sha256=SHA,
    prepared_program=PROGRAM, frame_count=Literal(120), frame_rate_num=Literal(30),
    frame_rate_den=Literal(1), source_objects=Array(UUID, 1, 1),
    fresh_sqlite_readback=TRUE, native_dispatch_performed=FALSE, decoded_media_verified=FALSE,
    rights=Literal("first-party synthetic shapes; no external media"))
INSPECTION = obj(source_asset_id=UUID, source_sha256=SHA,
    origin_binding=BINDING, current_binding=BINDING,
    original_closure=CLOSURE, current_closure=CLOSURE, current_plan=PLAN,
    revision_only_rebound=bool, editable_parameters=Array(String(96), 32),
    native_execution_performed=FALSE, imported_source_authority_used=FALSE)
PREVIEW = obj(expected=STAMP, original_source_asset_id=UUID, edited_source_asset_id=UUID,
    original_source_sha256=SHA, edited_source_sha256=SHA,
    current_plan_sha256=SHA, proposed_plan_sha256=SHA,
    native_execution_performed=FALSE, imported_source_authority_used=FALSE)
# Compiled code, duplicate plan bytes, preview Change and original receipt payloads
# remain in exact local originals. Plan/program/capsule and SQLite have own roles.
CYCLE_COMMON = dict(schema=Literal("motionwright.manim.camera.source-cycle/1"),
    project_id=UUID, scene_id=UUID, deliverable_id=UUID, canonical_stamp=STAMP,
    source_project_sha256=SHA, original_source_asset_id=UUID, edited_source_asset_id=UUID,
    original_source_sha256=SHA, edited_source_sha256=SHA, current_export_bundle_sha256=SHA,
    current_source_inspection=INSPECTION, fresh_sqlite_readback=TRUE,
    original_native_receipts_unchanged=TRUE, production_receipt_window_unchanged=TRUE,
    production_receipt_window_limit=Literal(256), native_execution_performed=FALSE,
    native_compiler_registered=FALSE, provider_dispatch_performed=FALSE,
    new_render_job_created=FALSE, imported_source_authority_used=FALSE,
    fresh_registered_source_reopen_verified=FALSE,
    native_video_frame_content_verified=FALSE, human_acceptance=FALSE)
EDIT = obj(**CYCLE_COMMON, phase=Literal("source-cycle-edit"),
    original_stamp=STAMP, attached_stamp=STAMP, original_native=NATIVE_LINK,
    original_source_inspection=INSPECTION, preview=PREVIEW, prepared_program=PROGRAM,
    source_changed_by_example=TRUE, import_staging_files_removed=TRUE)
REOPEN = obj(**CYCLE_COMMON, phase=Literal("source-cycle-readback"),
    edit_receipt_sha256=SHA, edited_native=NATIVE_LINK,
    saved_export_matches_native_carrier=TRUE, source_changed_by_example=FALSE)
EDIT_INTENT = obj(schema=Literal("motionwright.manim.camera.source-cycle-intent/1"),
    request_id=REQUEST, project_id=UUID, canonical_stamp=STAMP, original_native=NATIVE_LINK,
    original_source_sha256=SHA, edited_source_sha256=SHA,
    automatic_retry=FALSE, provider_dispatch_performed=FALSE)
POINT2 = Array(FINITE, 2, 2)
POINT3 = Array(FINITE, 3, 3)
GETTER_OBJECT = obj(node_id=UUID, kind=Literal("rectangle"), name=String(256),
    world_center=POINT3, width=Number(0, 1e6), height=Number(0, 1e6),
    z_index=Number(-(2**31), 2**31-1, integer=True), fill_hex=HEX_COLOR,
    fill_opacity=Number(0, 1), stroke_width=Number(0, 1e6), stroke_opacity=Number(0, 1))
GETTERS = obj(schema_version=Literal(1), profile_id=Literal("mw-manim-planar-camera-v1"),
    scene_class=Literal("MotionwrightScene"), plan_sha256=SHA, binding=BINDING,
    sample_count=Literal(120), callback_alphas=Array(Number(0, 1), 120, 120),
    frames=Array(obj(index=Number(0, 119, integer=True),
        camera=obj(frame_center=POINT3, frame_width=Number(0, 1e6),
            frame_height=Number(0, 1e6), pixel_width=Literal(640), pixel_height=Literal(360),
            frame_rate=Literal(30)), objects=Array(GETTER_OBJECT, 1, 1)), 120, 120),
    decoded_video_verified=FALSE, native_grants_verified_by_this_script=FALSE)
VIDEO = obj(codec=Literal("h264"), size=Array(Number(1, 4096, integer=True), 2, 2),
    frame_count=Literal(120), frame_rate=Literal("30/1"), duration_seconds=Number(3.98, 4.02),
    first_frame_pts=Number(-0.0005, 0.0005), last_frame_pts=Number(3.966, 3.967))
MOVEMENT = obj(node_id=UUID, frame=Number(0, 119, integer=True),
    expected_center=POINT2, observed_center=POINT2, matched_color_pixels=Number(100, 640*360, integer=True))
DECODED = obj(schema=Literal("motionwright.manim.camera.decoded-readback/1"),
    compiled_plan_sha256=SHA, trusted_media_sha256=SHA, binding=BINDING, video=VIDEO,
    native_frame_movement_observations=Array(MOVEMENT, 3, 3),
    all_120_decoded_frame_motion=obj(frame_count=Literal(120),
        observed_center_slope_px_per_frame=POINT2, expected_center_slope_px_per_frame=POINT2,
        max_original_centroid_error_px=Array(Number(0, 3), 2, 2),
        both_axes_moving_camera_verified_from_decoded_centroids=TRUE, full_pixel_equality_claim=FALSE),
    decoded_rgb_sha256=SHA, source_bound_video_observation=Literal("PASS"),
    native_driver_authority_verified_by_this_reader=FALSE,
    full_editable_reopen_verified_by_this_reader=FALSE, voice_or_caption_mixing_verified=FALSE,
    human_acceptance=FALSE)
NEGATIVE = obj(schema=Literal("motionwright.manim.camera.original-media-negative/1"),
    status=Literal("EXPECTED_GEOMETRIC_REJECTION"), original_media_sha256=SHA,
    edited_plan_sha256=SHA, original_native_evidence_sha256=SHA, decoded_rgb_sha256=SHA,
    decoded_frame_count=Literal(120), video=VIDEO,
    rejection=Literal("native object center diverges from saved camera trajectory"),
    metadata_and_original_digest_verified_before_geometry=TRUE, synthetic_media_used=FALSE,
    provider_dispatch_performed=FALSE, automatic_retry=FALSE, human_acceptance=FALSE)
OBSERVATION = obj(schema=Literal("motionwright.manim.camera.source-cycle-observation/1"),
    source_cycle_observation=Literal("PASS_REQUIRES_SEALED_CUSTODY"),
    motionwright_sha=GIT, semwright_sha=GIT, original_request_id=REQUEST, edited_request_id=REQUEST,
    original_native_evidence_sha256=SHA, edited_native_evidence_sha256=SHA,
    edit_receipt_sha256=SHA, fresh_source_readback_sha256=SHA, original_media_negative_sha256=SHA,
    original_plan_sha256=SHA, edited_plan_sha256=SHA, saved_camera_source_cycle_verified=TRUE,
    same_sqlite_store_reopened_in_fresh_process=TRUE, current_export_equals_second_native_carrier=TRUE,
    original_native_receipts_unchanged=TRUE, native_effect_invocations=Literal(2),
    fresh_requests_and_distinct_once_scopes=TRUE, source_export_grants_native_authority=FALSE,
    motion_canvas_equivalence_verified=FALSE, voice_or_caption_mixing_verified=FALSE,
    human_acceptance=FALSE, automatic_retry=FALSE)
CONTEXT = obj(source_sha=GIT, semwright_sha=GIT,
    run_id=String(20, r"[1-9][0-9]{0,19}"), run_attempt=Literal("1"))
INVOCATION = obj(context=CONTEXT, native_example_invocations=Literal(1), automatic_retry=FALSE)
PROCESS = obj(pid=Number(1, 2**31-1, integer=True),
    process_returncode=Nullable(Number(-255, 255, integer=True)),
    native_outcome=String(32, values=("NOT_INTERPRETED", "UNKNOWN")), automatic_retry=FALSE,
    timeout_seconds=opt(Number(0, 3600)))
ERROR_TYPE = String(64, values=("CameraEvidenceError", "SelectionError", "TimeoutExpired",
    "CalledProcessError", "AssertionError", "ValueError", "TypeError", "RuntimeError",
    "OSError", "PermissionError", "FileNotFoundError", "FileExistsError", "BrokenPipeError",
    "JSONDecodeError", "UnicodeDecodeError", "KeyboardInterrupt", "SystemExit"))
ERROR_CLASS = obj(type=ERROR_TYPE)
SECONDARY = obj(stage=String(64, values=("terminal-custody", "sealed-custody",
    "native-client-timeout-cleanup", "native-process-stream-custody",
    "native-process-stream-close", "broker-cleanup")), type=ERROR_TYPE, automatic_retry=FALSE)
TERMINAL = obj(schema=Literal("motionwright.manim.camera.original-terminal/1"),
    native_example_invoked=bool, original_process_observation=Nullable(PROCESS),
    native_result=Nullable(optional_object(NATIVE)), native_error=Nullable(ERROR_CLASS),
    later_error=Nullable(ERROR_CLASS), secondary_errors=Array(SECONDARY, 32),
    automatic_retry=FALSE, source_or_human_acceptance=FALSE)
INPUTS = obj(motionwright_sha=GIT, semwright_sha=GIT, runner_uid=Number(1, 2**32-1, integer=True),
    manim_version=String(96), source_lock_sha256=SHA,
    binaries=obj(source_seed=SHA, native_example=SHA, source_cycle_example=opt(SHA),
        driver=SHA, runner=SHA, broker=SHA, client=SHA, sandbox=SHA,
        python=SHA, ffmpeg=SHA, ffprobe=SHA),
    compiler_bytes_exported_before_native_dispatch=TRUE,
    independent_vendor_binary_authentication=FALSE)
RESULT = obj(schema=Literal("motionwright.manim.camera.native-e2e/1"),
    native_manim_camera_e2e=Literal("PASS"), motionwright_sha=GIT, semwright_sha=GIT,
    semwright_version=Literal("1.0.0"), manim_version=Literal("0.21.0"), project=BINDING,
    source_project_sha256=SHA, plan_sha256=SHA, program_sha256=SHA,
    original_getters_sha256=SHA, native_evidence_sha256=SHA, decoded_readback_sha256=SHA,
    mp4=obj(relative_path=String(128, r"videos/mw_[0-9a-f]{32}/360p30/mw_[0-9a-f]{32}\.mp4"), sha256=SHA,
        bytes=Number(1, 128*1024*1024, integer=True), width=Literal(640), height=Literal(360),
        frame_count=Literal(120), frame_rate_num=Literal(30), frame_rate_den=Literal(1)),
    native_effect_invocations=Literal(1), sealed_custody_required=TRUE,
    native_editable_reopen_verified=FALSE, full_pixel_equality_verified=FALSE, human_acceptance=FALSE)
POLICIES = {
    "manim-camera-plan-v2": PLAN, "manim-camera-native-v2": NATIVE,
    "manim-camera-seed-v2": SEED, "manim-camera-getters-v2": GETTERS,
    "manim-camera-decoded-v2": DECODED, "manim-camera-result-v2": RESULT,
    "manim-camera-terminal-v2": TERMINAL, "manim-camera-invocation-v2": INVOCATION,
    "manim-camera-inputs-v2": INPUTS, "manim-camera-stamp-v2": STAMP,
    "manim-camera-edit-v2": EDIT, "manim-camera-reopen-v2": REOPEN,
    "manim-camera-edit-intent-v2": EDIT_INTENT,
    "manim-camera-negative-v2": NEGATIVE, "manim-camera-cycle-observation-v2": OBSERVATION,
}


# Each row is code-owned. Callers cannot supply their own filenames or policies.
# Source JSON is a projection because generated Python and duplicate compiler
# plan bytes are already retained in their own exact original roles.
COMMON_ROWS = (
    ("evidence", "result.json", "native-result", "readback", "json-original", "manim-camera-result-v2"),
    ("evidence", "source-seed.json", "source-seed", "readback", "json-projection", "manim-camera-seed-v2"),
    ("evidence", "compiled-camera-plan.json", "compiled-plan", "readback", "json-original", "manim-camera-plan-v2"),
    ("evidence", "compiled-camera-program.py", "compiled-program", "readback", "original", None),
    ("evidence", "motionwright-manim-camera-evidence.json", "native-receipts", "readback", "json-projection", "manim-camera-native-v2"),
    ("evidence", "original-camera-getters.json", "native-getters", "readback", "json-original", "manim-camera-getters-v2"),
    ("evidence", "original-render-program.py", "executed-program", "readback", "original", None),
    ("evidence", "decoded-camera-readback.json", "decoded-readback", "readback", "json-original", "manim-camera-decoded-v2"),
    ("evidence", "motionwright-manim-community.mp4", "native-media", "readback", "original", None),
    ("evidence", "native-invocation-intent.json", "native-attempt-intent", "attempt-custody", "json-original", "manim-camera-invocation-v2"),
    ("evidence", "original-terminal.json", "native-terminal", "attempt-custody", "json-projection", "manim-camera-terminal-v2"),
    ("evidence", "native-input-identities.json", "native-input-identities", "origin", "json-original", "manim-camera-inputs-v2"),
    ("owner", "motionwright-data/motionwright.sqlite3", "sqlite-main", "persistence", "original", None),
)
EDIT_ROWS = (
    ("evidence", "source-cycle-original-stamp.json", "original-source-stamp", "readback", "json-original", "manim-camera-stamp-v2"),
    ("evidence", "source-cycle-edit.intent.json", "source-edit-intent", "attempt-custody", "json-projection", "manim-camera-edit-intent-v2"),
    ("evidence", "source-cycle-edit.json", "source-cycle-edit", "readback", "json-projection", "manim-camera-edit-v2"),
    ("evidence", "source-cycle-edited-stamp.json", "edited-source-stamp", "readback", "json-original", "manim-camera-stamp-v2"),
    ("evidence", "source-cycle-readback.json", "source-cycle-readback", "readback", "json-projection", "manim-camera-reopen-v2"),
    ("evidence", "original-media-edited-geometry-negative.json", "original-media-negative", "readback", "json-original", "manim-camera-negative-v2"),
    ("evidence", "source-cycle-observation.json", "source-cycle-observation", "readback", "json-original", "manim-camera-cycle-observation-v2"),
)
DATABASE_AUXILIARIES = (
    ("owner", "motionwright-data/motionwright.sqlite3-wal", "sqlite-wal", "persistence", "original", None),
    ("owner", "motionwright-data/motionwright.sqlite3-shm", "sqlite-shm", "persistence", "original", None),
)


def scope(current, scenario, fixture):
    selected.project(current, CONTEXT, exact=True)
    selected.require(scenario in ("camera120", "camera120-source-cycle"), "manim-selection-scenario")
    selected.require(fixture in ("original", "edited1040")
        and (fixture == "original" or scenario == "camera120-source-cycle"), "manim-selection-fixture")
    context = {**current, "scenario": scenario}
    selected.context(context)
    return context, "original" if fixture == "original" else "edited"


def fixed_rows(fixture):
    selected.require(fixture in ("original", "edited1040"), "manim-selection-fixture")
    rows = list(COMMON_ROWS)
    if fixture == "edited1040":
        rows = [(*row[:5], "manim-camera-edit-v2") if row[2] == "source-seed" else row
                for row in rows]
        rows.extend(EDIT_ROWS)
    return tuple(rows)


def role_contract(current, scenario, fixture):
    _, phase = scope(current, scenario, fixture)
    roles = {(phase, row[3], row[2]): (1, 1) for row in fixed_rows(fixture)}
    roles[(phase, "readback", "native-editable-source")] = (1, 1)
    for row in DATABASE_AUXILIARIES:
        roles[(phase, row[3], row[2])] = (0, 1)
    if fixture == "edited1040":
        roles[(phase, "persistence", "original-source-cas")] = (1, 1)
        roles[(phase, "persistence", "edited-source-cas")] = (1, 1)
    return roles


def source_bindings(source_root, source_sha):
    """Only fixed Git paths; check physical bytes before any native attempt."""
    selected.project(source_sha, GIT)
    bindings = []
    for path in SOURCE_PATHS:
        result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c",
            "core.untrackedCache=false", "show", source_sha + ":" + path],
            cwd=source_root, capture_output=True, timeout=30)
        selected.require(result.returncode == 0, "manim-producer-source-missing")
        bindings.append({"path": path, "sha256": selected.sha(result.stdout)})
    selected.verify_source_bindings(str(source_root), source_sha, bindings)
    return bindings


def exclusions(fixture):
    rows = []
    for prefix in ("", "edited1040/"):
        for path in ("runtime", "config", "home", "state", "bin"):
            rows.append({"root": "owner", "relative_path": prefix + path,
                         "reason": "outside-reviewed-scope"})
    for path in ("observer-diagnostic.json", "source-seed.stdout.log", "source-seed.stderr.log",
                 "native-example.stdout.log", "native-example.stderr.log"):
        rows.append({"root": "evidence", "relative_path": path,
                     "reason": "outside-reviewed-scope"})
    return rows


def member(row, phase, identity=None):
    root, path, role, layer, kind, policy = row
    return {"root": root, "relative_path": path, "role": role, "layer": layer,
            "phase": phase, "kind": kind, "required": True,
            "expected_sha256": identity[0] if identity else None,
            "bytes": identity[1] if identity else None, "policy": policy}


def observe(handles, root, path, maximum=selected.MAX_FILE):
    """Read exactly one selected safe path; never recurse or report raw errors."""
    try:
        raw, _ = handles.read(root, selected.member_path(path), maximum)
        return raw, "PRESENT"
    except FileNotFoundError:
        return None, "ABSENT"
    except (OSError, selected.SelectionError, ValueError):
        return None, "UNOBSERVED"


def closed_json(handles, path, policy):
    raw, _ = observe(handles, "evidence", path, selected.MAX_JSON)
    if raw is None:
        return None
    try:
        return selected.project(selected.strict(raw), policy)
    except (selected.SelectionError, TypeError, ValueError):
        return None


def carrier_row(native, fixture):
    prefix = "output/" if fixture == "original" else "edited1040/output/"
    missing = ("owner", prefix + "required-native-source.unobserved", "native-editable-source",
               "readback", "original", None)
    if not isinstance(native, dict):
        return missing, None
    try:
        value = native["result"]["artifact"]["camera_source"]
        selected.project(value, CARRIER, exact=True)
        token = native["result"]["job_ref"][6:]
        selected.require(value["relative_path"] == "mw_" + token + ".mwcam.zip",
                         "manim-source-carrier-job")
        return (("owner", prefix + value["relative_path"], "native-editable-source",
                "readback", "original", None), (value["sha256"], value["bytes"]))
    except (KeyError, TypeError, selected.SelectionError):
        return missing, None


def cas_rows(edit):
    result = []
    for prefix in ("original", "edited"):
        digest = edit.get(prefix + "_source_sha256") if isinstance(edit, dict) else None
        valid = type(digest) is str and selected.SHA.fullmatch(digest) is not None
        path = ("motionwright-data/blobs/sha256/" + digest[:2] + "/" + digest
                if valid else "motionwright-data/required-" + prefix + "-source-cas.unobserved")
        result.append((("owner", path, prefix + "-source-cas", "persistence", "original", None),
                       digest if valid else None))
    return result


def terminal_complete(value):
    """A captured payload is not itself a successful native terminal result."""
    if not isinstance(value, dict):
        return False
    process, native = value.get("original_process_observation"), value.get("native_result")
    return (value.get("native_example_invoked") is True
        and value.get("native_error") is None and value.get("later_error") is None
        and value.get("secondary_errors") == [] and value.get("automatic_retry") is False
        and value.get("source_or_human_acceptance") is False
        and isinstance(process, dict) and process.get("process_returncode") == 0
        and type(process.get("process_returncode")) is int
        and process.get("native_outcome") == "NOT_INTERPRETED"
        and process.get("automatic_retry") is False and "timeout_seconds" not in process
        and isinstance(native, dict) and native.get("native_manim_camera_dispatch") == "PASS"
        and native.get("receipt_read_error") is None and native.get("error") is None
        and native.get("custody_error") is None)


class CameraSelection:
    """One immutable source preflight followed by one selected capture."""
    def __init__(self, owner, evidence, source_root, current, scenario, fixture):
        self.context, self.phase = scope(current, scenario, fixture)
        self.fixture, self.scenario = fixture, scenario
        self.roots = {"owner": str(owner), "evidence": str(evidence), "source": str(source_root)}
        self.bindings = source_bindings(source_root, current["source_sha"])
        self.contract = role_contract(current, scenario, fixture)
        self.captured = False

    def capture(self):
        selected.require(not self.captured, "manim-selected-capture-already-attempted")
        # Set the guard before opening originals. A failed capture is not replayed.
        self.captured = True
        rows, guards = [], []
        handles = selected.RootHandles(self.roots)
        try:
            native = closed_json(handles, "motionwright-manim-camera-evidence.json", NATIVE)
            edit = closed_json(handles, "source-cycle-edit.json", EDIT) if self.fixture == "edited1040" else None
            if self.fixture == "edited1040" and edit is None:
                edit = closed_json(handles, "source-cycle-edit.intent.json", EDIT_INTENT)
            for row in fixed_rows(self.fixture):
                raw, _ = observe(handles, row[0], row[1],
                    selected.MAX_JSON if row[4] != "original" else selected.MAX_FILE)
                rows.append(member(row, self.phase,
                    (selected.sha(raw), len(raw)) if raw is not None else None))
            row, expected = carrier_row(native, self.fixture)
            rows.append(member(row, self.phase, expected))
            if self.fixture == "edited1040":
                for row, digest in cas_rows(edit):
                    # Content address is supplied by the admitted source receipt;
                    # byte length is observed only for this exact selected CAS path.
                    raw, _ = observe(handles, row[0], row[1]) if digest else (None, "UNOBSERVED")
                    identity = (digest, len(raw)) if raw is not None else None
                    rows.append(member(row, self.phase, identity))
            for row in DATABASE_AUXILIARIES:
                raw, status = observe(handles, row[0], row[1])
                if status == "ABSENT":
                    guards.append({"root": row[0], "relative_path": row[1], "phase": self.phase,
                                   "layer": row[3], "role": row[2]})
                else:
                    rows.append(member(row, self.phase,
                        (selected.sha(raw), len(raw)) if raw is not None else None))
        finally:
            handles.close()
        spec = {"schema": selected.SPEC_SCHEMA, "context": self.context,
            "roots": self.roots, "members": rows, "absence_guards": guards,
            "exclusions": exclusions(self.fixture), "source_bindings": self.bindings,
            "origin": {"kind": "generated-first-party-fixture", "fixture_id": "manim-" + self.scenario,
                "producer_paths": list(PRODUCER_PATHS), "private_data": True,
                "upload_authorization": "NOT_GRANTED_BY_COLLECTOR"}}
        tar, manifest = selected.capture_selected_originals(spec,
            policies=POLICIES, role_contract=self.contract)
        if manifest["required_layers_complete"]:
            selected.inspect_selected_tar(tar, expected_context=self.context,
                role_contract=self.contract, policies=POLICIES)
        return tar, manifest



def expected_from_environment(source_sha, semwright_sha):
    """Run identity comes from the admitted caller, never from its own receipt."""
    current = {"source_sha": source_sha, "semwright_sha": semwright_sha,
               "run_id": os.environ.get("GITHUB_RUN_ID", ""),
               "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "")}
    selected.project(current, CONTEXT, exact=True)
    return current


def _layout(manifest, current, scenario, fixture, bindings, native, edit):
    context, phase = scope(current, scenario, fixture)
    contract = role_contract(current, scenario, fixture)
    selected.validate_selected_manifest(manifest, expected_context=context, role_contract=contract)
    selected.require(manifest.get("source_bindings") == bindings, "manim-selected-source-bindings")
    selected.require([row["path"] for row in bindings] == list(SOURCE_PATHS),
                     "manim-selected-source-paths")
    selected.require(manifest.get("origin") == {
        "kind": "generated-first-party-fixture", "fixture_id": "manim-" + scenario,
        "producer_paths": list(PRODUCER_PATHS), "private_data": True,
        "upload_authorization": "NOT_GRANTED_BY_COLLECTOR"}, "manim-selected-origin")
    rows = {row["role"]: row for row in manifest["members"]}
    selected.require(len(rows) == len(manifest["members"]), "manim-selected-duplicate-role")
    expected_rows = {row[2]: row for row in fixed_rows(fixture)}
    source_row, source_identity = carrier_row(native, fixture)
    selected.require(source_identity is not None, "manim-selected-source-carrier-missing")
    expected_rows[source_row[2]] = source_row
    if fixture == "edited1040":
        for row, digest in cas_rows(edit):
            selected.require(digest is not None, "manim-selected-cas-identity-missing")
            expected_rows[row[2]] = row
    for row in DATABASE_AUXILIARIES:
        if row[2] in rows:
            expected_rows[row[2]] = row
    selected.require(set(rows) == set(expected_rows), "manim-selected-row-set")
    for role, definition in expected_rows.items():
        row = rows[role]
        selected.require(tuple(row[k] for k in ("root", "relative_path", "role", "layer", "kind", "policy"))
                         == definition and row["phase"] == phase and row["required"] is True
                         and row["state"] == "INCLUDED", "manim-selected-row-contract")
    selected.require((rows["native-editable-source"]["expected_sha256"],
                      rows["native-editable-source"]["bytes"]) == source_identity,
                     "manim-selected-carrier-identity")
    if fixture == "edited1040":
        for prefix in ("original", "edited"):
            selected.require(rows[prefix + "-source-cas"]["expected_sha256"] ==
                             edit[prefix + "_source_sha256"], "manim-selected-cas-digest")
    guards = manifest.get("absence_guards", [])
    expected_guards = [{"root": row[0], "relative_path": row[1], "phase": phase,
                        "layer": row[3], "role": row[2]}
                       for row in DATABASE_AUXILIARIES if row[2] not in rows]
    # The generic helper adds only closed observation fields to absent guards.
    selected.require(len(guards) == len(expected_guards), "manim-sqlite-absence-guard-count")
    selected.require([{k: row.get(k) for k in ("root", "relative_path", "phase", "layer", "role")}
                       for row in guards] == expected_guards, "manim-sqlite-absence-guard-identity")
    return rows


def _value(raw, row, context):
    """Validate a retained original or a recomputed projection, no raw diagnostics."""
    if row["kind"] == "original":
        return raw
    parsed = selected.strict(raw)
    if row["kind"] == "json-projection":
        selected.require(parsed.get("schema") == selected.PROJECTION_SCHEMA
            and parsed.get("policy") == row["policy"] and parsed.get("context") == context
            and parsed.get("phase") == row["phase"] and parsed.get("role") == row["role"]
            and parsed.get("source_sha256") == row["expected_sha256"]
            and parsed.get("source_bytes") == row["bytes"]
            and parsed.get("original_bytes_retained_locally") is True
            and parsed.get("full_original_json_claimed") is False, "manim-projection-origin")
        parsed = parsed["value"]
    selected.project(parsed, POLICIES[row["policy"]], exact=True)
    return parsed


def _program_capsule(raw, carrier, plan_raw, program_raw):
    selected.require(selected.sha(raw) == carrier["sha256"] and len(raw) == carrier["bytes"],
                     "manim-editable-capsule-digest")
    expected = {row["path"]: row for row in carrier["closure"]["entries"]}
    allowed = {"assets.lock.json", "manifest.json", "plan.json", "render-config.json", "scene.py"}
    selected.require(set(expected) == allowed and len(expected) == len(carrier["closure"]["entries"]),
                     "manim-editable-capsule-closure")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        selected.require(len(infos) == 5 and {x.filename for x in infos} == allowed
                         and not archive.comment, "manim-editable-capsule-files")
        total = 0
        for info in infos:
            selected.require(not info.is_dir() and info.compress_type == zipfile.ZIP_STORED
                and not info.flag_bits & 1 and not info.comment
                and (info.external_attr >> 16) & 0o170000 != 0o120000
                and 0 < info.file_size <= 256*1024, "manim-editable-capsule-member")
            total += info.file_size
            selected.require(total <= 2*1024*1024, "manim-editable-capsule-budget")
            data = archive.read(info)
            row = expected[info.filename]
            selected.require(len(data) == row["bytes"] and selected.sha(data) == row["sha256"],
                             "manim-editable-capsule-member-digest")
            if info.filename == "plan.json":
                selected.require(data == plan_raw, "manim-editable-capsule-plan")
            elif info.filename == "scene.py":
                selected.require(data == program_raw, "manim-editable-capsule-program")


def validate_readback_values(values, rows, current, fixture):
    """Cross-bind closed evidence; no pixel decoding or acceptance promotion."""
    native = values["native-receipts"]
    result = values["native-result"]
    plan = values["compiled-plan"]
    terminal = values["native-terminal"]
    selected.require(terminal_complete(terminal), "manim-selected-terminal-not-successful")
    selected.require(terminal["native_result"] == native, "manim-selected-terminal-result")
    selected.require(result["motionwright_sha"] == current["source_sha"]
        and result["semwright_sha"] == current["semwright_sha"], "manim-selected-result-source")
    binding = plan["binding"]
    selected.require(result["project"] == binding
        and all(native[k] == binding[k] for k in binding), "manim-selected-native-binding")
    request = "camera-" + current["run_id"] + "-" + current["run_attempt"] + "-" + current["source_sha"][:12]
    if fixture == "edited1040":
        request += "-edited1040"
    selected.require(native["request_id"] == request, "manim-selected-request-scope")
    for key, suffix, command in (
        ("original_start_receipt", ":start", "driver.manim-community.camera.render.start"),
        ("original_result_receipt", ":result", "driver.manim-community.render.result"),
    ):
        receipt = native[key]
        selected.require(receipt["request_id"] == request + suffix and receipt["command"] == command
            and receipt["stage"] == "completed" and all(receipt[k] == binding[k]
                for k in ("project_id", "generation", "revision")), "manim-selected-receipt-binding")
    inputs = values["native-input-identities"]
    selected.require(inputs["motionwright_sha"] == current["source_sha"]
        and inputs["semwright_sha"] == current["semwright_sha"], "manim-selected-input-source")
    selected.require(values["native-attempt-intent"]["context"] == current, "manim-selected-invocation-scope")
    for result_field, role in (
        ("plan_sha256", "compiled-plan"), ("program_sha256", "compiled-program"),
        ("original_getters_sha256", "native-getters"),
        ("native_evidence_sha256", "native-receipts"), ("decoded_readback_sha256", "decoded-readback"),
    ):
        selected.require(result[result_field] == rows[role]["expected_sha256"], "manim-selected-readback-digest")
    selected.require(rows["compiled-program"]["expected_sha256"] == rows["executed-program"]["expected_sha256"],
                     "manim-selected-executed-program")
    artifact = native["result"]["artifact"]
    selected.require(result["mp4"]["sha256"] == artifact["sha256"] == rows["native-media"]["expected_sha256"]
        and result["mp4"]["bytes"] == artifact["bytes"] == rows["native-media"]["bytes"],
                     "manim-selected-original-media")
    carrier = artifact["camera_source"]
    selected.require(carrier["closure"]["plan_sha256"] == result["plan_sha256"]
        and carrier["closure"]["source_sha256"] == result["program_sha256"],
                     "manim-selected-native-source-closure")
    selected.require(values["native-getters"]["binding"] == binding
        and values["native-getters"]["plan_sha256"] == result["plan_sha256"]
        and values["decoded-readback"]["binding"] == binding
        and values["decoded-readback"]["compiled_plan_sha256"] == result["plan_sha256"]
        and values["decoded-readback"]["trusted_media_sha256"] == artifact["sha256"],
                     "manim-selected-getter-decoded-binding")
    seed = values["source-seed"]
    selected.require(seed["prepared_program"]["prepared"]["source"] == plan
        and seed["prepared_program"]["trusted_compiler"]["plan_sha256"] == result["plan_sha256"]
        and seed["prepared_program"]["trusted_compiler"]["source_sha256"] == result["program_sha256"],
                     "manim-selected-seed-program-binding")
    end = 1000.0 if fixture == "original" else 1040.0
    selected.require(plan["reframe"]["start_center_x"] == 960.0
        and plan["reframe"]["start_center_y"] == 540.0
        and plan["reframe"]["end_center_x"] == plan["camera"]["center_x"] == end
        and plan["reframe"]["end_center_y"] == plan["camera"]["center_y"] == 544.0,
                     "manim-selected-fixture-endpoints")
    if fixture == "edited1040":
        edit, reopen = values["source-cycle-edit"], values["source-cycle-readback"]
        selected.require(seed == edit, "manim-selected-edited-seed")
        selected.require(reopen["edit_receipt_sha256"] == rows["source-cycle-edit"]["expected_sha256"]
            and reopen["current_source_inspection"] == edit["current_source_inspection"]
            and reopen["edited_native"]["camera_source"] == carrier
            and reopen["edited_native"]["native_evidence_sha256"] == rows["native-receipts"]["expected_sha256"],
                         "manim-selected-fresh-source-readback")
        for prefix in ("original", "edited"):
            selected.require(reopen[prefix + "_source_sha256"] == edit[prefix + "_source_sha256"]
                == values["source-edit-intent"][prefix + "_source_sha256"]
                == rows[prefix + "-source-cas"]["expected_sha256"], "manim-selected-cas-history")
        selected.require(edit["current_export_bundle_sha256"] == reopen["current_export_bundle_sha256"]
                         == carrier["sha256"], "manim-selected-edited-source-carrier")
        observation = values["source-cycle-observation"]
        for field, role in (("edited_native_evidence_sha256", "native-receipts"),
            ("edit_receipt_sha256", "source-cycle-edit"), ("fresh_source_readback_sha256", "source-cycle-readback"),
            ("original_media_negative_sha256", "original-media-negative")):
            selected.require(observation[field] == rows[role]["expected_sha256"],
                             "manim-selected-cycle-observation-digest")
        selected.require(observation["edited_request_id"] == request
            and observation["original_request_id"] != request
            and observation["motionwright_sha"] == current["source_sha"]
            and observation["semwright_sha"] == current["semwright_sha"], "manim-selected-cycle-observation-scope")
    return True


def inspect_camera_tar(raw, *, current, scenario, fixture, bindings):
    """Offline /2 intake, no extraction, database opening or native effect."""
    context, _ = scope(current, scenario, fixture)
    manifest = selected.inspect_selected_tar(raw, expected_context=context,
        role_contract=role_contract(current, scenario, fixture), policies=POLICIES)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        payloads = {row["role"]: archive.extractfile(row["member"]).read()
                    for row in manifest["members"] if row["state"] == "INCLUDED"}
    by_role = {row["role"]: row for row in manifest["members"]}
    values = {role: _value(data, by_role[role], context) for role, data in payloads.items()}
    rows = _layout(manifest, current, scenario, fixture, bindings,
                   values["native-receipts"], values.get("source-cycle-edit"))
    validate_readback_values(values, rows, current, fixture)
    _program_capsule(payloads["native-editable-source"],
        values["native-receipts"]["result"]["artifact"]["camera_source"],
        payloads["compiled-plan"], payloads["compiled-program"])
    return manifest


def _retained_json(raw, row, context):
    value = selected.project(selected.strict(raw), POLICIES[row["policy"]],
                             exact=row["kind"] == "json-original")
    if row["kind"] == "json-original":
        return raw, value
    retained = selected.canonical({"schema": selected.PROJECTION_SCHEMA, "policy": row["policy"],
        "source_sha256": selected.sha(raw), "source_bytes": len(raw), "context": context,
        "phase": row["phase"], "role": row["role"], "original_bytes_retained_locally": True,
        "full_original_json_claimed": False, "value": value})
    return retained, value


def verify_publication_selection(evidence, receipt, *, source_root, current, scenario, fixture):
    """Recheck local selected readbacks and manifest; never reopen original SQLite."""
    context, phase = scope(current, scenario, fixture)
    selected.require(receipt.get("schema") == SCHEMA and receipt.get("status") == "PASS"
        and receipt.get("context") == current and receipt.get("scenario") == scenario
        and receipt.get("phase") == phase and receipt.get("required_layers_complete") is True
        and receipt.get("native_terminal_complete") is True
        and receipt.get("native_result_retained") is True and receipt.get("secondary_error_count") == 0
        and receipt.get("original_owner_tree_removed") is False
        and receipt.get("atomic_sqlite_snapshot_claimed") is False
        and receipt.get("producer_authenticated_by_encryption") is False,
                     "manim-selected-custody-incomplete")
    bindings = source_bindings(source_root, current["source_sha"])
    handles = selected.RootHandles({"source": str(source_root), "evidence": str(evidence)})
    try:
        raw, status = observe(handles, "evidence", MANIFEST_NAME, selected.MAX_JSON)
        selected.require(status == "PRESENT" and selected.sha(raw) == receipt.get("selected_manifest_sha256")
            and len(raw) == receipt.get("selected_manifest_bytes"), "manim-selected-manifest-identity")
        manifest = selected.strict(raw)
        native = closed_json(handles, "motionwright-manim-camera-evidence.json", NATIVE)
        edit = closed_json(handles, "source-cycle-edit.json", EDIT) if fixture == "edited1040" else None
        rows = _layout(manifest, current, scenario, fixture, bindings, native, edit)
        values = {}
        for definition in fixed_rows(fixture):
            root, path, role, _, kind, _ = definition
            if root != "evidence":
                continue
            raw, status = observe(handles, root, path,
                selected.MAX_JSON if kind != "original" else selected.MAX_FILE)
            row = rows[role]
            selected.require(status == "PRESENT" and selected.sha(raw) == row["expected_sha256"]
                and len(raw) == row["bytes"], "manim-selected-local-readback-changed")
            if kind == "original":
                retained, value = raw, raw
            else:
                retained, value = _retained_json(raw, row, context)
            selected.require(selected.sha(retained) == row["retained_sha256"]
                and len(retained) == row["retained_bytes"], "manim-selected-projection-changed")
            values[role] = value
        validate_readback_values(values, rows, current, fixture)
    finally:
        handles.close()
    return manifest


def verify_envelope_binding(envelope, receipt, current):
    """Parse existing seal header identity only; cryptographic recovery is offline."""
    magic = b"MWCISEAL1\n"
    selected.require(type(envelope) is bytes and envelope.startswith(magic)
        and len(envelope) > len(magic) + 4, "manim-selected-envelope-magic")
    width = int.from_bytes(envelope[len(magic):len(magic)+4], "big")
    selected.require(0 < width <= 8192 and len(envelope) > len(magic) + 4 + width + 16,
                     "manim-selected-envelope-header-size")
    header = selected.strict(envelope[len(magic)+4:len(magic)+4+width])
    selected.require(set(header) == {"schema", "algorithm", "recipient_spki_sha256", "context",
        "nonce", "wrapped_key", "plaintext_sha256", "plaintext_bytes"}
        and header["schema"] == "motionwright.ci.private-evidence-seal/1"
        and header["algorithm"] == "RSA-OAEP-SHA256+AES-256-GCM"
        and header["context"] == current
        and header["recipient_spki_sha256"] == receipt["recipient_sha256"]
        and header["plaintext_sha256"] == receipt["selected_tar_sha256"]
        and header["plaintext_bytes"] == receipt["selected_tar_bytes"],
                     "manim-selected-envelope-origin")
    try:
        selected.require(len(base64.b64decode(header["nonce"], validate=True)) == 12
            and len(base64.b64decode(header["wrapped_key"], validate=True)) == 384,
                         "manim-selected-envelope-key-shape")
    except (ValueError, TypeError) as error:
        raise selected.SelectionError("manim-selected-envelope-key-shape") from error
