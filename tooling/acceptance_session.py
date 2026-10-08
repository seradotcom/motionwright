#!/usr/bin/env python3
"""Private, source-bound intake for Motionwright's 60 independent acceptance cases.

This tool does not contain private acceptance scenario text. It NEVER promotes
an implementation CI result to PASS, edits the public acceptance ledger, or
authenticates the claimed reviewer identity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "docs" / "acceptance" / "acceptance-status.json"
LOCK = ROOT / "SOURCE_LOCK.json"
EXPECTED_IDS = [f"ACC-{i:03d}" for i in range(1, 61)]
STATES = {"NOT_RUN", "PASS", "FAIL", "BLOCKED"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024 * 1024
SCHEMA = 1


class IntakeError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def canonical_ids() -> list[str]:
    doc = json.loads(CANONICAL.read_text(encoding="utf-8"))
    ids = [case.get("id") for case in doc.get("cases", [])]
    if ids != EXPECTED_IDS:
        raise IntakeError("Public acceptance ID list differs from the 60 canonical private-spec identifiers")
    return ids


def locked_semwright_revision() -> str:
    revision = json.loads(LOCK.read_text(encoding="utf-8"))["dependencies"]["semwright"]["revision"]
    if not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
        raise IntakeError("Semwright lock lacks an exact 40-character Git revision")
    return revision


def checked_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    revision = result.stdout.strip()
    if not SHA_RE.fullmatch(revision):
        raise IntakeError("Current Motionwright HEAD is not a concrete Git revision")
    return revision


def create_session(*, source_sha: str, executed_by: str = "") -> dict:
    if not SHA_RE.fullmatch(source_sha):
        raise IntakeError("source_sha must be the exact 40-character tested Motionwright commit")
    return {
        "schema_version": SCHEMA,
        "source": {
            "repository": "seradotcom/motionwright",
            "motionwright_sha": source_sha,
            "semwright_sha": locked_semwright_revision(),
            "private_spec_version": "0.1.0",
            "private_spec_text_embedded": False,
        },
        "session": {
            "id": str(uuid.uuid4()),
            "created_at_utc": utc_now(),
            "executed_by": executed_by.strip(),
            "notes": "",
        },
        "cases": [
            {
                "id": case_id,
                "status": "NOT_RUN",
                "executed_by": "",
                "reviewed_by": "",
                "independent": False,
                "executed_at_utc": None,
                "reviewed_at_utc": None,
                "notes": "",
                "evidence": [],
            }
            for case_id in canonical_ids()
        ],
    }


def validated_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or len(relative) > 1024:
        raise IntakeError("Evidence path must be a bounded relative POSIX path")
    normalized = PurePosixPath(relative)
    if normalized.is_absolute() or any(part in ("", ".", "..") for part in relative.split("/")):
        raise IntakeError("Evidence paths must not be absolute or contain parent/dot components")
    if "\\" in relative or normalized.parts[0].startswith("~"):
        raise IntakeError("Evidence paths must not use backslashes or home expansion")
    current = root
    for part in normalized.parts:
        current = current / part
        if current.is_symlink():
            raise IntakeError(f"Evidence path traverses a symlink: {relative}")
    resolved_root = root.resolve(strict=True)
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise IntakeError(f"Evidence path is not a regular file within its root: {relative}")
    return resolved


def fingerprint(root: Path, relative: str) -> dict:
    path = validated_path(root, relative)
    size = path.stat().st_size
    if not 0 < size <= MAX_EVIDENCE_BYTES:
        raise IntakeError("Evidence file is missing, empty or outside the 2 GiB individual budget")
    digest = sha256()
    actual = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            actual += len(chunk)
            if actual > MAX_EVIDENCE_BYTES:
                raise IntakeError("Evidence size changed beyond the 2 GiB budget during hashing")
            digest.update(chunk)
    if actual != size:
        raise IntakeError("Evidence size changed during hashing")
    return {"path": relative, "sha256": digest.hexdigest(), "size_bytes": actual}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IntakeError(message)


def validate_session(doc: object, *, evidence_root: Path) -> dict[str, int]:
    _require(isinstance(doc, dict), "Session must be a JSON object")
    _require(doc.get("schema_version") == SCHEMA, "Unknown acceptance intake schema")
    source = doc.get("source")
    _require(isinstance(source, dict), "Missing source provenance")
    _require(source.get("repository") == "seradotcom/motionwright", "Unexpected source repository")
    _require(isinstance(source.get("motionwright_sha"), str) and
             SHA_RE.fullmatch(source["motionwright_sha"]) is not None,
             "Unpinned Motionwright source SHA")
    _require(source.get("semwright_sha") == locked_semwright_revision(),
             "Semwright source SHA does not match SOURCE_LOCK.json")
    _require(source.get("private_spec_version") == "0.1.0" and
             source.get("private_spec_text_embedded") is False,
             "Private acceptance specification must not be embedded in public review evidence")
    session = doc.get("session")
    _require(isinstance(session, dict), "Missing session metadata")
    _require(valid_timestamp(session.get("created_at_utc")), "Session creation time must include UTC offset")
    _require(isinstance(session.get("id"), str), "Session ID is missing")
    try:
        uuid.UUID(session["id"])
    except (ValueError, TypeError):
        raise IntakeError("Session ID is not a valid UUID") from None
    cases = doc.get("cases")
    _require(isinstance(cases, list) and len(cases) == 60
             and all(isinstance(case, dict) for case in cases)
             and [c["id"] for c in cases] == canonical_ids(),
             "Session must contain each of the exact 60 canonical acceptance IDs, once and in order")
    root = evidence_root.resolve(strict=True)
    _require(root.is_dir(), "Evidence root must be a real directory")
    counts: Counter[str] = Counter()
    for case in cases:
        case_id = case["id"]
        state = case.get("status")
        _require(state in STATES, f"{case_id}: unknown acceptance status")
        counts[state] += 1
        items = case.get("evidence")
        _require(isinstance(items, list) and len(items) <= 32,
                 f"{case_id}: evidence must be a list with no more than 32 files")
        _require(isinstance(case.get("notes"), str), f"{case_id}: missing notes text")
        if state == "NOT_RUN":
            _require(not items and not case.get("independent") and not case.get("reviewed_by"),
                     f"{case_id}: NOT_RUN must not contain pass/review claims or evidence")
            continue
        executor = case.get("executed_by")
        _require(isinstance(executor, str) and bool(executor.strip()),
                 f"{case_id}: executed test must name its operator")
        _require(valid_timestamp(case.get("executed_at_utc")),
                 f"{case_id}: execution time must include timezone")
        if state == "BLOCKED":
            _require(bool(case["notes"].strip()), f"{case_id}: blocking reason is required")
        else:
            _require(bool(items), f"{case_id}: PASS/FAIL requires at least one actual evidence file")
        if state == "PASS":
            _require(bool(case["notes"].strip()), f"{case_id}: PASS requires review rationale")
            reviewer = case.get("reviewed_by")
            _require(case.get("independent") is True and
                     isinstance(reviewer, str) and bool(reviewer.strip()) and
                     reviewer.strip().casefold() != executor.strip().casefold(),
                     f"{case_id}: PASS requires an independently identified reviewer different from the executor")
            _require(valid_timestamp(case.get("reviewed_at_utc")),
                     f"{case_id}: independent PASS review time is missing")
            executed_at = datetime.fromisoformat(case["executed_at_utc"].replace("Z", "+00:00"))
            reviewed_at = datetime.fromisoformat(case["reviewed_at_utc"].replace("Z", "+00:00"))
            _require(reviewed_at >= executed_at,
                     f"{case_id}: an independent review cannot precede test execution")
        for item in items:
            _require(isinstance(item, dict), f"{case_id}: invalid evidence record")
            path = item.get("path")
            digest = item.get("sha256")
            size = item.get("size_bytes")
            _require(isinstance(digest, str) and DIGEST_RE.fullmatch(digest) is not None and
                     isinstance(size, int) and not isinstance(size, bool),
                     f"{case_id}: evidence requires exact digest and integer size")
            actual = fingerprint(root, path)
            _require(actual["sha256"] == digest and actual["size_bytes"] == size,
                     f"{case_id}: evidence has changed since it was recorded: {path}")
    return {status: counts[status] for status in ("NOT_RUN", "PASS", "FAIL", "BLOCKED")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    init = subs.add_parser("init", help="Create private NOT_RUN intake, never a PASS")
    init.add_argument("--output", required=True, type=Path)
    init.add_argument("--source-sha", default=None)
    init.add_argument("--executed-by", default="")
    verify = subs.add_parser("verify", help="Verify source, independent claims and evidence bytes")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--evidence-root", required=True, type=Path)
    fp = subs.add_parser("fingerprint", help="Prepare one bounded evidence descriptor")
    fp.add_argument("--evidence-root", required=True, type=Path)
    fp.add_argument("--path", required=True)
    args = parser.parse_args()
    try:
        if args.action == "init":
            # Prevent accidentally committing private review sessions to a
            # public repository. Refuse replacing an existing session.
            # Resolve the *parent* to guard public-checkout paths hidden by
            # symlink aliases; the final file must not exist yet.
            output = args.output.parent.resolve(strict=True) / args.output.name
            if output.is_relative_to(ROOT.resolve()):
                raise IntakeError("Review intake must be created outside the public source checkout")
            data = create_session(
                source_sha=args.source_sha or checked_head(),
                executed_by=args.executed_by,
            )
            with output.open("x", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
            print(f"acceptance-intake=initialized cases=60 not_run=60 file={output}")
        elif args.action == "verify":
            doc = json.loads(args.manifest.read_text(encoding="utf-8"))
            totals = validate_session(doc, evidence_root=args.evidence_root)
            print("acceptance-intake=verified " + " ".join(
                f"{key.lower()}={count}" for key, count in totals.items()
            ))
        else:
            print(json.dumps(fingerprint(args.evidence_root, args.path), sort_keys=True))
    except (IntakeError, OSError, ValueError) as exc:
        print(f"acceptance-intake: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
