#!/usr/bin/env python3
"""Validate canonical identities and recorded acceptance evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tooling") not in sys.path:
    sys.path.insert(0, str(ROOT / "tooling"))
from conformance_policy import load_json, public_file, validate_ledger_assessments

REQ = ROOT / "docs/acceptance/requirements-status.json"
ACC = ROOT / "docs/acceptance/acceptance-status.json"
IMPLEMENTATION = {"EVIDENCE_PRESENT", "PARTIAL_OR_GAP", "BLOCKED_UPSTREAM", "OPTIONAL_UNCLAIMED", "VERIFIED"}
VERIFICATION = {"CI_PARTIAL", "NOT_RUN", "BLOCKED_UPSTREAM", "VERIFIED"}
ACCEPTANCE = {"NOT_RUN", "PASS", "FAIL", "BLOCKED"}
EXPECTED_REQ = {
    f"CR-{area}-{n:02d}"
    for area in ("FOUND", "PROJECT", "NARR", "AUDIO", "UX", "TIME", "CANVAS",
                 "CONCEPT", "STYLE", "CHANGE", "SDK", "MOTION", "MLT", "BLENDER",
                 "MANIM", "GRAPH", "EFFECT", "JOBS", "MODELS", "WORKFLOW", "VARIANT",
                 "SEC", "DELIVER", "PERF", "PLATFORM", "EXT")
    for n in range(1, 9)
}
EXPECTED_ACC = {f"ACC-{n:03d}" for n in range(1, 61)}
EXPECTED_A05 = {f"A05-E{epic:02d}-{n:02d}" for epic in range(16) for n in range(1, 5)}


def exact_rows(rows, expected, label):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(label + " must be a list of records")
    ids = [row.get("id") for row in rows]
    if any(not isinstance(identity, str) for identity in ids) or len(ids) != len(expected) or set(ids) != expected:
        raise ValueError(label + " must preserve all canonical IDs exactly once")


def evidence_pointers(row, root):
    evidence = row.get("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError(row["id"] + " evidence must be a list")
    if any(not isinstance(pointer, str) for pointer in evidence) or len(evidence) != len(set(evidence)):
        raise ValueError(row["id"] + " evidence pointers must be distinct strings")
    for pointer in evidence:
        public_file(root, pointer)
    return evidence


def validate(requirements, acceptance, root=ROOT):
    if not isinstance(requirements, dict) or not isinstance(acceptance, dict):
        raise ValueError("ledgers must be objects")
    items = requirements.get("requirements")
    exact_rows(items, EXPECTED_REQ, "requirement ledger")
    validate_ledger_assessments(requirements, items, root)
    for item in items:
        impl = item.get("implementation_assessment")
        ver = item.get("verification_status")
        if not isinstance(impl, str) or impl not in IMPLEMENTATION:
            raise ValueError(item["id"] + " has an invalid implementation status")
        if not isinstance(ver, str) or ver not in VERIFICATION:
            raise ValueError(item["id"] + " has an invalid verification status")
        evidence = evidence_pointers(item, root)
        if impl == "VERIFIED" or ver == "VERIFIED":
            review = item.get("technical_assessment", {})
            if not evidence or review.get("technical_verification", {}).get("status") != "pass":
                raise ValueError(item["id"] + " VERIFIED needs complete source-scoped technical assessment")
    cases = acceptance.get("cases")
    exact_rows(cases, EXPECTED_ACC, "acceptance ledger")
    expansions = acceptance.get("expansion_cases", [])
    if "expansion_cases" in acceptance:
        exact_rows(expansions, EXPECTED_A05, "expansion acceptance ledger")
        if acceptance.get("conformance_schema_version") != 1:
            raise ValueError("expansion cases require explicit conformance schema")
    elif acceptance.get("conformance_schema_version") == 1:
        raise ValueError("conformance schema must preserve all 64 expansion acceptance IDs")
    all_cases = cases + expansions
    validate_ledger_assessments(acceptance, all_cases, root)
    for case in all_cases:
        status = case.get("status")
        if not isinstance(status, str) or status not in ACCEPTANCE:
            raise ValueError(case["id"] + " has an invalid acceptance status")
        independent = case.get("independent")
        if type(independent) is not bool:
            raise ValueError(case["id"] + " independent must be a JSON boolean")
        evidence = evidence_pointers(case, root)
        if status == "PASS":
            assessment = case.get("technical_assessment", {})
            if independent is not True or not evidence:
                raise ValueError(case["id"] + " PASS needs independent review and real evidence")
            if assessment.get("contract_acceptance", {}).get("status") != "accepted":
                raise ValueError(case["id"] + " PASS needs complete contractual assessment")
    return len(items), len(cases), len(expansions), sum(case["status"] == "PASS" for case in all_cases)


def main():
    try:
        counts = validate(load_json(REQ), load_json(ACC))
    except (OSError, ValueError, TypeError) as error:
        raise SystemExit("acceptance-policy: " + str(error)) from error
    print(f"acceptance-policy=ok requirements={counts[0]} acceptance_cases={counts[1]} "
          f"expansion_cases={counts[2]} product_pass={counts[3]}")


if __name__ == "__main__":
    main()
