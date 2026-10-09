#!/usr/bin/env python3
"""Validate expansion traceability without manufacturing requirement acceptance."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def validate(data: dict, root: Path = ROOT) -> None:
    if data.get("schema_version") != 1 or data.get("scope") != "creative-v05-expansion-only":
        raise ValueError("unknown creative status schema or ownership scope")
    if data.get("v05_release_ready") is not False:
        raise ValueError("this partial expansion ledger cannot declare a v0.5 release")
    required = {f"MW05-E{epic:02d}-{item:02d}" for epic in range(16) for item in range(1,5)}
    rows = data.get("requirements", [])
    ids = [row.get("id") for row in rows]
    if len(ids) != 64 or set(ids) != required:
        raise ValueError("the 64 expansion identities must be preserved exactly once")
    allowed = {"bounded_implementation","partial","inherited_not_reassessed","not_implemented"}
    for row in rows:
        status = row.get("implementation")
        if status not in allowed or row.get("complete_requirement_acceptance") != "NOT_COMPLETED":
            raise ValueError("implementation scope is not product/human acceptance")
        if not row.get("implemented_scope", "").strip() or not row.get("remaining_scope", "").strip():
            raise ValueError("every row must explain its concrete scope and remaining work")
        paths = row.get("source_paths", [])
        if status != "not_implemented" and not paths:
            raise ValueError("claimed implementation or inherited behavior needs real source paths")
        if status == "not_implemented" and paths:
            raise ValueError("unimplemented work cannot cite source as implementation proof")
        for path in paths:
            relative = Path(path)
            if relative.is_absolute() or ".." in relative.parts or not (root / relative).is_file():
                raise ValueError(f"unknown or unsafe implementation evidence path: {path}")
    for key, name in [("original_requirements_ledger","requirements"),("original_acceptance_ledger","cases")]:
        path = root / data[key]
        original = json.loads(path.read_text(encoding="utf-8"))
        if not original.get(name):
            raise ValueError("the expansion cannot replace or empty the original requirement history")

if __name__ == "__main__":
    data = json.loads((ROOT / "docs/creative-production/requirements-status.json").read_text(encoding="utf-8"))
    validate(data)
    counts = {status:sum(row["implementation"]==status for row in data["requirements"]) for status in sorted(data["status_semantics"])}
    print("creative-status=valid", json.dumps(counts,sort_keys=True), "release=false human-acceptance=not-completed")
