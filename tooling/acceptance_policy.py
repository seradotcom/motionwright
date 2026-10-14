#!/usr/bin/env python3
"""Validate public traceability metadata, never authenticate human acceptance.

Private recordings and review identities stay outside this repository. A public
PASS may reference only a curated, case-scoped JSON receipt with matching bytes;
its substantive review and publication still require a maintainer.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
REQ_PATH = "docs/acceptance/requirements-status.json"
ACC_PATH = "docs/acceptance/acceptance-status.json"
AREAS = (
    "FOUND", "PROJECT", "NARR", "AUDIO", "UX", "TIME", "CANVAS",
    "CONCEPT", "STYLE", "CHANGE", "SDK", "MOTION", "MLT", "BLENDER",
    "MANIM", "GRAPH", "EFFECT", "JOBS", "MODELS", "WORKFLOW",
    "VARIANT", "SEC", "DELIVER", "PERF", "PLATFORM", "EXT",
)
REQUIREMENT_IDS = {f"CR-{area}-{i:02d}" for area in AREAS for i in range(1, 9)}
ACCEPTANCE_IDS = {f"ACC-{i:03d}" for i in range(1, 61)}
IMPLEMENTATION = {"EVIDENCE_PRESENT", "PARTIAL_OR_GAP", "BLOCKED_UPSTREAM",
                  "OPTIONAL_UNCLAIMED", "VERIFIED"}
VERIFICATION = {"CI_PARTIAL", "NOT_RUN", "BLOCKED_UPSTREAM", "VERIFIED"}
ACCEPTANCE = {"NOT_RUN", "PASS", "FAIL", "BLOCKED"}
MAX_JSON_BYTES = 3_000_000
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class PolicyError(ValueError):
    pass


def need(condition: bool, code: str) -> None:
    if not condition:
        raise PolicyError(code)

def source_file(root: Path, pointer: object) -> Path:
    """Require a canonical in-repo regular file, without links or reparse points."""
    need(isinstance(pointer, str) and 0 < len(pointer) <= 1024, "evidence_path_invalid")
    need(not any(ord(c) < 32 or ord(c) == 127 for c in pointer)
         and "\\" not in pointer and ":" not in pointer, "evidence_path_invalid")
    relative = PurePosixPath(pointer)
    need(not relative.is_absolute() and all(p not in {"", ".", ".."}
         for p in pointer.split("/")), "evidence_path_invalid")
    current = root
    try:
        for part in relative.parts:
            current = current / part
            info = current.lstat()
            need(not stat.S_ISLNK(info.st_mode) and not bool(
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ), "evidence_link_refused")
        need(stat.S_ISREG(info.st_mode), "evidence_not_regular_file")
        need(current.resolve().is_relative_to(root.resolve()), "evidence_path_escape")
    except (OSError, RuntimeError) as exc:
        raise PolicyError("evidence_file_unavailable") from exc
    return current


def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        need(key not in result, "duplicate_json_field")
        result[key] = value
    return result


def reject_nonfinite(_value: str) -> None:
    raise PolicyError("nonfinite_json_value")


def load(path: Path) -> dict:
    try:
        need(0 < path.stat().st_size <= MAX_JSON_BYTES, "json_size_invalid")
        raw = path.read_bytes()
        need(0 < len(raw) <= MAX_JSON_BYTES, "json_size_invalid")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicate_keys,
                          parse_constant=reject_nonfinite)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PolicyError("json_invalid") from exc
    need(isinstance(data, dict), "json_object_required")
    return data


def exact_rows(rows: object, ids: set[str], code: str) -> list[dict]:
    need(isinstance(rows, list) and len(rows) == len(ids), code)
    need(all(isinstance(row, dict) for row in rows), code)
    received = [row.get("id") for row in rows]
    need(all(isinstance(value, str) for value in received), code)
    need(len(set(received)) == len(ids) and set(received) == ids, code)
    return rows


def status_in(value: object, allowed: set[str]) -> bool:
    return isinstance(value, str) and value in allowed

def validate(requirements: object, acceptance: object, root: Path = ROOT) -> dict:
    for document in (requirements, acceptance):
        need(isinstance(document, dict), "ledger_object_required")
        need(type(document.get("schema_version")) is int
             and document["schema_version"] == 1
             and document.get("source_spec_version") == "0.1.0"
             and document.get("source_text_published") is False, "ledger_schema_invalid")
    rows = exact_rows(requirements.get("requirements"), REQUIREMENT_IDS, "requirements_ids_invalid")
    cases = exact_rows(acceptance.get("cases"), ACCEPTANCE_IDS, "acceptance_ids_invalid")
    for row in rows:
        need(row.get("area") == row["id"].split("-")[1], "requirement_area_invalid")
        impl, ver = row.get("implementation_assessment"), row.get("verification_status")
        need(status_in(impl, IMPLEMENTATION) and status_in(ver, VERIFICATION), "requirement_status_invalid")
        evidence = row.get("evidence")
        need(isinstance(evidence, list), "requirement_evidence_invalid")
        if impl in {"EVIDENCE_PRESENT", "VERIFIED"} or ver == "VERIFIED":
            need(bool(evidence), "claimed_requirement_needs_evidence")
        for pointer in evidence:
            source_file(root, pointer)
    seen = set()
    for case in cases:
        status, independent, evidence = case.get("status"), case.get("independent"), case.get("evidence")
        need(status_in(status, ACCEPTANCE), "acceptance_status_invalid")
        need(type(independent) is bool, "independent_must_be_boolean")
        need(isinstance(evidence, list), "acceptance_evidence_invalid")
        if status == "NOT_RUN":
            need(not independent and not evidence, "not_run_has_evidence_or_review")
        if status == "PASS":
            need(independent is True and bool(evidence), "pass_needs_review_and_evidence")
        if status == "FAIL":
            need(bool(evidence), "fail_needs_evidence")
        for descriptor in evidence:
            need(isinstance(descriptor, dict)
                 and set(descriptor) == {"path", "sha256", "size_bytes"}, "receipt_descriptor_invalid")
            pointer, digest, size = descriptor["path"], descriptor["sha256"], descriptor["size_bytes"]
            path = source_file(root, pointer)
            need(pointer.startswith(f"docs/acceptance/evidence/{case['id']}/")
                 and path.suffix == ".json", "receipt_case_scope_invalid")
            need(pointer not in seen, "duplicate_acceptance_evidence")
            seen.add(pointer)
            need(isinstance(digest, str) and DIGEST.fullmatch(digest) is not None,
                 "receipt_digest_invalid")
            need(type(size) is int and 0 < size <= MAX_JSON_BYTES, "receipt_size_invalid")
            need(path.stat().st_size == size, "receipt_size_mismatch")
            raw = path.read_bytes()
            need(len(raw) == size and sha256(raw).hexdigest() == digest, "receipt_digest_mismatch")
            load(path)
    return {"requirements": len(rows), "acceptance_cases": len(cases),
            "product_pass": sum(case["status"] == "PASS" for case in cases)}


def main() -> int:
    try:
        result = validate(load(source_file(ROOT, REQ_PATH)), load(source_file(ROOT, ACC_PATH)))
    except (PolicyError, OSError) as exc:
        code = str(exc) if isinstance(exc, PolicyError) else "evidence_file_unavailable"
        print("acceptance-policy=failed code=" + code, file=sys.stderr)
        return 2
    print("acceptance-policy=ok " + " ".join(f"{key}={value}" for key, value in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
