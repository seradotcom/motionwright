#!/usr/bin/env python3
"""Validate expansion traceability without manufacturing requirement acceptance."""
from __future__ import annotations
import json
from pathlib import Path
import sys

try:
    from tooling.acceptance_policy import (ACC_PATH, REQ_PATH, exact_rows, load,
                                          need, source_file, validate as validate_original)
except ModuleNotFoundError:
    from acceptance_policy import (ACC_PATH, REQ_PATH, exact_rows, load,
                                   need, source_file, validate as validate_original)

ROOT = Path(__file__).resolve().parents[1]


def validate(data: object, root: Path = ROOT) -> None:
    need(isinstance(data, dict), "creative_ledger_object_required")
    need(type(data.get("schema_version")) is int and data["schema_version"] == 1
         and data.get("scope") == "creative-v05-expansion-only", "creative_schema_invalid")
    need(data.get("v05_release_ready") is False, "creative_release_promotion_refused")
    required = {f"MW05-E{epic:02d}-{item:02d}" for epic in range(16) for item in range(1, 5)}
    rows = exact_rows(data.get("requirements"), required, "creative_ids_invalid")
    allowed = {"bounded_implementation", "partial", "inherited_not_reassessed", "not_implemented"}
    for row in rows:
        status = row.get("implementation")
        need(isinstance(status, str) and status in allowed
             and row.get("complete_requirement_acceptance") == "NOT_COMPLETED", "creative_status_invalid")
        for key in ("implemented_scope", "remaining_scope"):
            value = row.get(key)
            need(isinstance(value, str) and bool(value.strip()), "creative_scope_required")
        paths = row.get("source_paths")
        need(isinstance(paths, list), "creative_source_paths_invalid")
        need(bool(paths) == (status != "not_implemented"), "creative_evidence_claim_mismatch")
        for path in paths:
            source_file(root, path)
    need(data.get("original_requirements_ledger") == REQ_PATH
         and data.get("original_acceptance_ledger") == ACC_PATH, "original_ledger_redirect_refused")
    validate_original(load(source_file(root, REQ_PATH)), load(source_file(root, ACC_PATH)), root)


def main() -> int:
    try:
        data = load(source_file(ROOT, "docs/creative-production/requirements-status.json"))
        validate(data)
    except (ValueError, OSError):
        print("creative-status=invalid traceability-validation-failed", file=sys.stderr)
        return 2
    counts = {status: sum(row["implementation"] == status for row in data["requirements"])
              for status in sorted({row["implementation"] for row in data["requirements"]})}
    print("creative-status=valid", json.dumps(counts, sort_keys=True), "release=false human-acceptance=not-completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
