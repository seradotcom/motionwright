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
