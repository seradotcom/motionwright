"""CI-only safe visibility for the original semantic SFX native render.

The original native job's outcome is NEVER inferred from progress stages.
Only a fixed enum is emitted into public CI logs; never private owner paths,
stdout, stderr, session identifiers, render refs or input payloads.
"""
from __future__ import annotations
import json
from pathlib import Path

SCHEMA = "motionwright.sfx-ci-native-stage/1"
STAGES = frozenset({
    "entered",
    "owner_project_loaded",
    "fixture_checked",
    "owner_connection_ready",
    "native_request_entered",
    "native_request_returned",
    "native_request_error",
    "native_master_error",
    "native_result_checked",
    "motion_evidence_written",
})
KEYS = frozenset({
    "schema", "stage", "final_av_master_verified",
    "native_job_outcome_established", "automatically_retried",
})


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    fields: dict = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("repeated stage diagnostic key")
        fields[key] = value
    return fields


def safe_stage(path: Path) -> str:
    """One static token; failed or ambiguous stage remains NOT_VERIFIED."""
    try:
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 1024:
            return "NOT_VERIFIED"
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)
        if (
            not isinstance(value, dict) or set(value) != KEYS
            or value["schema"] != SCHEMA or value["stage"] not in STAGES
            or value["final_av_master_verified"] is not False
            or value["native_job_outcome_established"] is not False
            or value["automatically_retried"] is not False
        ):
            return "NOT_VERIFIED"
        return value["stage"]
    except (OSError, ValueError, TypeError, UnicodeError, KeyError):
        return "NOT_VERIFIED"


NATIVE_ERROR_SCHEMA = "motionwright.sfx-ci-native-error-category/1"
NATIVE_ERROR_PHASES = frozenset({"native_request_error", "native_master_error"})
NATIVE_ERROR_CODES = frozenset({
    "BackendFailed", "InvalidArgument", "Unsupported", "StaleReference",
    "Unavailable", "Timeout", "PermissionDenied", "Conflict", "Cancelled",
    "Internal", "ResourceExhausted", "OtherOrMissing",
})
NATIVE_REASON_CLASSES = frozenset({
    "provider_generation", "revision_mismatch", "film_contract",
    "composition_plan", "composition_apply", "render_start",
    "render_failed", "render_status", "render_result", "verification",
    "native_mux", "unclassified",
})
NATIVE_RENDER_FAILURE_CLASSES = frozenset({
    'arguments', 'font_evidence', 'project_stage', 'vite_build',
    'frame_export', 'browser_launch', 'page_load', 'render_wait',
    'render_wait_timeout', 'renderer_state_frame_clock', 'renderer_state_authoring_protocol', 'renderer_log_authoring_protocol',
    'renderer_state_playback_protocol', 'renderer_log_playback_protocol', 'renderer_log_exporter_missing', 'renderer_log_async_property',
    'renderer_state_webgl_unavailable', 'renderer_log_webgl_unavailable', 'renderer_state_model_invariant', 'renderer_log_model_invariant',
    'renderer_state_authoring_model', 'renderer_log_authoring_model', 'renderer_state_invalid_scene', 'renderer_log_invalid_scene',
    'renderer_state_range_error', 'renderer_log_range_error', 'renderer_state_type_error', 'renderer_log_type_error',
    'renderer_state_semwright_native', 'renderer_state_semwright_exporter', 'renderer_state_motion_core', 'renderer_state_motion_2d',
    'renderer_state_before_first_frame', 'renderer_state_after_first_frame', 'renderer_state_error', 'renderer_log_error',
    'render_result_aborted', 'render_result_error', 'render_result_unknown', 'render_nonzero',
    'runtime_module_load', 'runtime_syntax', 'runtime_permission', 'runtime_oom',
    'runtime_killed', 'runtime_cpu_limit', 'runtime_file_size_limit', 'runtime_signal',
    'observation', 'finalize', 'startup',
    'unclassified', 'not_applicable',
})

NATIVE_RECEIPT_COUNTS = frozenset({
    "plan_completed", "apply_completed", "render_start_completed",
    "render_start_dispatching", "render_status_seen", "render_result_seen",
    "verify_completed", "mux_seen",
})
NATIVE_ERROR_KEYS = frozenset({
    "schema", "phase", "sdk_code", "reason_class", "render_failure_class",
    "sdk_outcome_known",
    "native_media_verified", "automatically_retried",
}) | NATIVE_RECEIPT_COUNTS


def safe_native_error_category(path: Path) -> dict | None:
    """Return ONLY a fixed typed result or None. Never return source text."""
    try:
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 2048:
            return None
        item = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicates,
        )
        if (
            not isinstance(item, dict)
            or set(item) != NATIVE_ERROR_KEYS
            or item["schema"] != NATIVE_ERROR_SCHEMA
            or item["phase"] not in NATIVE_ERROR_PHASES
            or item["sdk_code"] not in NATIVE_ERROR_CODES
            or item["reason_class"] not in NATIVE_REASON_CLASSES
            or item["render_failure_class"] not in NATIVE_RENDER_FAILURE_CLASSES
            or (item["reason_class"] != "render_failed"
                and item["render_failure_class"] != "not_applicable")
            or (item["reason_class"] == "render_failed"
                and item["render_failure_class"] == "not_applicable")
            or type(item["sdk_outcome_known"]) is not bool
            or item["native_media_verified"] is not False
            or item["automatically_retried"] is not False
            or any(type(item[key]) is not int or not 0 <= item[key] <= 128
                   for key in NATIVE_RECEIPT_COUNTS)
            or sum(item[key] for key in NATIVE_RECEIPT_COUNTS) > 128
        ):
            return None
        return item
    except (OSError, ValueError, TypeError, UnicodeError, KeyError):
        return None
