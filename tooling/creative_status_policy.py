#!/usr/bin/env python3
"""Validate expansion traceability without manufacturing requirement acceptance."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tooling") not in sys.path:
    sys.path.insert(0, str(ROOT / "tooling"))
from conformance_policy import load_json, public_file, validate_ledger_assessments

STATUS_SEMANTICS = {
    "bounded_implementation": "The named bounded contract is implemented; this is not full product or human acceptance.",
    "implemented": "All canonical technical criteria have complete source-scoped evidence. Contractual acceptance and release authority remain separate.",
    "inherited_not_reassessed": "An older capability was preserved, not counted as new or fully accepted here.",
    "not_implemented": "No implementation of the new requirement is claimed.",
    "partial": "Some concrete implementation exists; the remaining requirement is explicit.",
}

def validate(data: dict, root: Path = ROOT) -> None:
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1 or data.get("scope") != "creative-v05-expansion-only":
        raise ValueError("unknown creative status schema or ownership scope")
    if data.get("v05_release_ready") is not False:
        raise ValueError("this expansion ledger alone cannot declare a v0.5 release")
    if data.get("status_semantics") != STATUS_SEMANTICS:
        raise ValueError("status semantics must preserve all five implementation states")
    required = {f"MW05-E{epic:02d}-{item:02d}" for epic in range(16) for item in range(1,5)}
    rows = data.get("requirements", [])
    ids = [row.get("id") for row in rows]
    if len(ids) != 64 or set(ids) != required:
        raise ValueError("the 64 expansion identities must be preserved exactly once")
    validate_ledger_assessments(data, rows, root)
    allowed = set(STATUS_SEMANTICS)
    for row in rows:
        status = row.get("implementation")
        completion = row.get("complete_requirement_acceptance")
        assessment = row.get("technical_assessment", {})
        if status not in allowed or completion not in {"NOT_COMPLETED", "COMPLETED"}:
            raise ValueError("implementation scope is not product/human acceptance")
        if status == "implemented" and assessment.get("technical_verification", {}).get("status") != "pass":
            raise ValueError("implemented needs complete source-scoped technical verification")
        if completion == "COMPLETED" and assessment.get("contract_acceptance", {}).get("status") != "accepted":
            raise ValueError("implementation scope is not product/human acceptance")
        if not row.get("implemented_scope", "").strip() or (completion == "NOT_COMPLETED" and not row.get("remaining_scope", "").strip()):
            raise ValueError("every row must explain its concrete scope and remaining work")
        paths = row.get("source_paths", [])
        if status != "not_implemented" and not paths:
            raise ValueError("claimed implementation or inherited behavior needs real source paths")
        if status == "not_implemented" and paths:
            raise ValueError("unimplemented work cannot cite source as implementation proof")
        for path in paths:
            try:
                public_file(root, path)
            except ValueError as error:
                raise ValueError(f"unknown or unsafe implementation evidence path: {path}") from error
    for key, name in [("original_requirements_ledger","requirements"),("original_acceptance_ledger","cases")]:
        path = public_file(root, data[key])
        original = load_json(path)
        if not original.get(name):
            raise ValueError("the expansion cannot replace or empty the original requirement history")

if __name__ == "__main__":
    data = load_json(ROOT / "docs/creative-production/requirements-status.json")
    validate(data)
    counts = {status:sum(row["implementation"]==status for row in data["requirements"]) for status in sorted(STATUS_SEMANTICS)}
    completed = sum(row["complete_requirement_acceptance"] == "COMPLETED" for row in data["requirements"])
    print("creative-status=valid", json.dumps(counts,sort_keys=True), f"release=false contractually_accepted={completed}")
