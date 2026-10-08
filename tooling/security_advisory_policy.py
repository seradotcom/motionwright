#!/usr/bin/env python3
"""Fail closed around Motionwright's reviewed RustSec warning set.

This does not mark known advisories as fixed. It makes the current exceptions
explicit, lock-version bound, and invalid as soon as dependency or audit output
changes.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tomllib
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "docs/security/known-rustsec-advisories.json"
LOCK_PATH = ROOT / "Cargo.lock"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def package_versions(lock: dict[str, Any]) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    for package in lock.get("package", []):
        name = package.get("name")
        version = package.get("version")
        if isinstance(name, str) and isinstance(version, str):
            versions.setdefault(name, set()).add(version)
    return versions


def first_party_files() -> list[pathlib.Path]:
    roots = [ROOT / "crates", ROOT / "apps"]
    files: list[pathlib.Path] = []
    for base in roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {"target", "node_modules", "dist", "test-results"} for part in path.parts):
                continue
            if path.suffix in {".rs", ".ts", ".tsx", ".js", ".jsx"}:
                files.append(path)
    return files


def item_advisory_id(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    advisory = item.get("advisory")
    if isinstance(advisory, dict) and isinstance(advisory.get("id"), str):
        return advisory["id"]
    for key in ("id", "advisory_id", "advisory-id"):
        value = item.get(key)
        if isinstance(value, str) and value.startswith("RUSTSEC-"):
            return value
    return None


def collect_warning_ids(audit: dict[str, Any]) -> dict[str, str]:
    warnings = audit.get("warnings", {})
    if warnings in ({}, None):
        return {}
    if not isinstance(warnings, dict):
        fail("cargo-audit JSON warnings field is not an object")

    found: dict[str, str] = {}
    for category, entries in warnings.items():
        if entries is None:
            continue
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            fail(f"cargo-audit warning category {category!r} is not a list")
        for item in entries:
            advisory_id = item_advisory_id(item)
            if advisory_id is None:
                fail(f"cargo-audit warning in {category!r} has no RustSec advisory id")
            if advisory_id in found:
                fail(f"cargo-audit advisory {advisory_id} appeared more than once")
            found[advisory_id] = str(category)
    return found


def ensure_zero_vulnerabilities(audit: dict[str, Any]) -> None:
    vulnerabilities = audit.get("vulnerabilities", {})
    if vulnerabilities in ({}, None):
        return
    if not isinstance(vulnerabilities, dict):
        fail("cargo-audit vulnerabilities field is not an object")
    count = vulnerabilities.get("count", 0)
    entries = vulnerabilities.get("list", [])
    found = vulnerabilities.get("found", False)
    if count or entries or found:
        ids: list[str] = []
        if isinstance(entries, list):
            ids = [value for item in entries if (value := item_advisory_id(item))]
        suffix = f": {', '.join(ids)}" if ids else ""
        fail(f"cargo-audit reported security vulnerabilities{suffix}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-json", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()

    policy = json.loads(POLICY_PATH.read_text())
    if policy.get("schema") != "motionwright.security-known-advisories.v1":
        fail("known advisory policy schema is unsupported")

    advisories = policy.get("advisories")
    if not isinstance(advisories, list) or not advisories:
        fail("known advisory policy must contain at least one reviewed advisory")

    lock = tomllib.loads(LOCK_PATH.read_text())
    locked = package_versions(lock)
    expected: dict[str, dict[str, Any]] = {}
    for advisory in advisories:
        advisory_id = advisory.get("id")
        package = advisory.get("package")
        version = advisory.get("locked_version")
        category = advisory.get("class")
        status = advisory.get("status")
        if not all(isinstance(value, str) and value for value in (advisory_id, package, version, category, status)):
            fail("known advisory entry is incomplete")
        if advisory_id in expected:
            fail(f"duplicate known advisory {advisory_id}")
        if not status.startswith("OPEN_"):
            fail(f"known advisory {advisory_id} must remain explicitly OPEN")
        if locked.get(package) != {version}:
            fail(
                f"{advisory_id} review is stale: expected {package} {version}, "
                f"lock has {sorted(locked.get(package, set()))}"
            )
        context = advisory.get("required_lock_context", {})
        if not isinstance(context, dict) or not context:
            fail(f"{advisory_id} must bind its reviewed dependency context")
        for context_package, context_version in context.items():
            if locked.get(context_package) != {context_version}:
                fail(
                    f"{advisory_id} dependency context drifted: expected "
                    f"{context_package} {context_version}, lock has "
                    f"{sorted(locked.get(context_package, set()))}"
                )
        expected[advisory_id] = advisory

    source_files = first_party_files()
    for advisory_id, advisory in expected.items():
        for token in advisory.get("forbidden_first_party_tokens", []):
            if not isinstance(token, str) or not token:
                fail(f"{advisory_id} has an invalid forbidden token")
            offenders = []
            for path in source_files:
                try:
                    text = path.read_text()
                except (UnicodeDecodeError, OSError):
                    continue
                if token in text:
                    offenders.append(path.relative_to(ROOT).as_posix())
            if offenders:
                fail(
                    f"{advisory_id} affected API token {token!r} entered first-party code: "
                    + ", ".join(offenders)
                )

    actual_warnings: dict[str, str] | None = None
    if args.audit_json is not None:
        audit = json.loads(args.audit_json.read_text())
        ensure_zero_vulnerabilities(audit)
        actual_warnings = collect_warning_ids(audit)
        expected_ids = set(expected)
        actual_ids = set(actual_warnings)
        if actual_ids != expected_ids:
            missing = sorted(expected_ids - actual_ids)
            unexpected = sorted(actual_ids - expected_ids)
            fail(
                "cargo-audit warning set changed; "
                f"missing={missing} unexpected={unexpected}. A new security review is required."
            )
        for advisory_id, category in actual_warnings.items():
            expected_category = expected[advisory_id]["class"]
            if category != expected_category:
                fail(
                    f"{advisory_id} class changed from {expected_category!r} "
                    f"to {category!r}; a new security review is required"
                )

    report = {
        "schema": "motionwright.security-advisory-evidence.v1",
        "source_sha": os.environ.get("GITHUB_SHA", "local-unattributed"),
        "policy": POLICY_PATH.relative_to(ROOT).as_posix(),
        "audit_checked": actual_warnings is not None,
        "vulnerabilities_allowed": 0,
        "known_warnings": [
            {
                "id": advisory_id,
                "class": advisory["class"],
                "package": advisory["package"],
                "locked_version": advisory["locked_version"],
                "status": advisory["status"],
            }
            for advisory_id, advisory in sorted(expected.items())
        ],
        "actual_warning_ids": sorted(actual_warnings) if actual_warnings is not None else None,
        "statement": (
            "Known warnings remain open; this evidence only proves the exact reviewed "
            "warning set and dependency context did not drift."
        ),
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        "security-advisory-policy=ok "
        f"known={len(expected)} audit_checked={str(actual_warnings is not None).lower()}"
    )


if __name__ == "__main__":
    main()
