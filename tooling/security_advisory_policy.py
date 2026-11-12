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
from glib_owner_chain import OwnerChainError, verify_glib_owner_chain, lock_reverse_edges

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


def verify_rustix_0486_patched(lock: dict[str, Any]) -> None:
    """Reject an affected rustix source instead of waiving a new RustSec warning.

    This lock/source check is not proof that unsafe APIs are unreachable, nor
    a substitute for the authoritative hosted cargo-audit database.
    """
    selected = [item for item in lock.get("package", [])
                if item.get("name") == "rustix"]
    if (len(selected) != 1
            or selected[0].get("version") != "1.1.6"
            or selected[0].get("source") != "registry+https://github.com/rust-lang/crates.io-index"
            or selected[0].get("checksum") !=
            "e5e76742e5294153d4dde1207dbec5bdd81eef6fabdc0d2118babadabfd858aa"):
        fail("RUSTSEC-2026-0486: reviewed patched rustix 1.1.6 source is missing or changed")



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


def verify_ttf_parser_review(lock: dict[str, Any], advisory: dict[str, Any]) -> dict[str, Any]:
    """Track an OPEN unmaintained parser only on exact bytes and reviewed owners."""
    if (advisory.get("id") != "RUSTSEC-2026-0192"
            or advisory.get("package") != "ttf-parser"
            or advisory.get("locked_version") != "0.25.1"
            or advisory.get("class") != "unmaintained"
            or advisory.get("status") != "OPEN_UPSTREAM_UNMAINTAINED"):
        fail("ttf-parser new maintenance-risk policy changed without review")
    matching = [p for p in lock["package"] if p.get("name") == "ttf-parser"]
    if (len(matching) != 1
            or matching[0].get("version") != "0.25.1"
            or matching[0].get("source") != "registry+https://github.com/rust-lang/crates.io-index"
            or matching[0].get("checksum") != advisory.get("registry_checksum")
            or advisory.get("registry_checksum") !=
            "d2df906b07856748fa3f6e0ad0cbaa047052d4a7dd609e231c4f72cee8c36f31"):
        fail("ttf-parser exact registry source or checksum changed; review required")
    driver = [p for p in lock["package"] if p.get("name") == "semwright-driver-motion-canvas"]
    if (len(driver) != 1
            or driver[0].get("version") != "1.0.0"
            or driver[0].get("source") != advisory.get("reviewed_motion_canvas_lock_source")
            or advisory.get("reviewed_motion_canvas_lock_source") !=
            "git+https://github.com/seradotcom/semwright?rev=723cb4c9956af28a605571e3fb5935bcc00a0231#723cb4c9956af28a605571e3fb5935bcc00a0231"):
        fail("ttf-parser reviewed Semwright driver source changed; review required")
    try:
        reverse = lock_reverse_edges(lock)
    except OwnerChainError as error:
        fail("ttf-parser dependency graph is not uniquely resolved: " + str(error))
    source = ("ttf-parser", "0.25.1")
    if source not in reverse:
        fail("ttf-parser reviewed package missing")
    direct = sorted(f"{name}@{version}" for name, version in reverse[source])
    expected_direct = [
        "motionwright-manim-profile@0.1.0",
        "semwright-driver-motion-canvas@1.0.0",
    ]
    if direct != expected_direct or advisory.get("reviewed_direct_owners") != expected_direct:
        fail("ttf-parser direct owners changed; review required")
    closure, stack = {source}, [source]
    while stack:
        for parent in reverse[stack.pop()]:
            if parent not in closure:
                closure.add(parent)
                stack.append(parent)
    actual = sorted(f"{name}@{version}" for name, version in closure if (name, version) != source)
    expected = [
        "motionwright-desktop@0.1.0",
        "motionwright-manim-community-driver@0.1.0",
        "motionwright-manim-profile@0.1.0",
        "motionwright-native@0.1.0",
        "motionwright-service@0.1.0",
        "semwright-driver-motion-canvas@1.0.0",
    ]
    if actual != expected or advisory.get("reviewed_transitive_owners") != expected:
        fail("ttf-parser transitive owners changed; review required")
    return {
        "advisory_id": "RUSTSEC-2026-0192",
        "status": "OPEN_UPSTREAM_UNMAINTAINED",
        "source": matching[0]["source"],
        "locked_version": "0.25.1",
        "direct_owners": direct,
        "transitive_owners": actual,
        "maintenance_risk_closed": False,
        "native_runtime_or_distribution_certified": False,
    }


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
    verify_rustix_0486_patched(lock)
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

    glib = expected.get("RUSTSEC-2024-0429")
    if not isinstance(glib, dict):
        fail("reviewed glib advisory policy is missing")
    try:
        glib_owner_chain = verify_glib_owner_chain(lock, glib)
    except OwnerChainError as error:
        fail("RUSTSEC-2024-0429 owner chain requires independent review: " + str(error))

    ttf_parser_review = verify_ttf_parser_review(lock, expected.get("RUSTSEC-2026-0192", {}))

    actual_warnings: dict[str, str] | None = None
    if args.audit_json is not None:
        audit = json.loads(args.audit_json.read_text())
        ensure_zero_vulnerabilities(audit)
        actual_warnings = collect_warning_ids(audit)
        expected_ids = set(expected)
        actual_ids = set(actual_warnings)
        # cargo-audit audits registry package identities. The exact reviewed
        # local-path glib backport is ABSENT from its warning set even though
        # its upstream RUSTSEC advisory remains OPEN. This is an explicit
        # coverage gap, not an ignore/waiver and not evidence of remediation.
        # verify_glib_owner_chain() above already fails closed unless Cargo
        # selects the one audited local path and EVERY upstream source byte
        # is compared with the original registry tarball.
        local_path_advisory = "RUSTSEC-2024-0429"
        if glib_owner_chain.get("reviewed_backport_source_verified") is not True:
            fail("local glib source audit gap has no independently verified backport")
        required_ids = expected_ids - {local_path_advisory}
        missing = sorted(required_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        if missing or unexpected:
            fail(
                "cargo-audit warning set changed outside reviewed local path; "
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
        "glib_owner_chain": glib_owner_chain,
        "ttf_parser_unmaintained_source": ttf_parser_review,
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
        "open_advisories_absent_from_registry_audit": (
            sorted(set(expected) - set(actual_warnings))
            if actual_warnings is not None else None
        ),
        "local_path_crate_audit_coverage_complete": False,
        "upstream_glib_advisory_resolved": False,
        "statement": (
            "Known advisories remain OPEN. cargo-audit cannot establish that the "
            "locally patched glib 0.18.5 source is safe: the registry advisory may "
            "disappear from its report for this path dependency. Exact upstream "
            "source custody is independently verified, but installed product "
            "remediation and human acceptance are NOT proven."
        ),
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        "security-advisory-policy=ok "
        f"known_open={len(expected)} audit_checked={str(actual_warnings is not None).lower()}"
        + (" path_glib_audit_absence="
           + str("RUSTSEC-2024-0429" not in actual_warnings).lower()
           + " glib_remediation=NOT_PROVEN"
           if actual_warnings is not None else "")
    )


if __name__ == "__main__":
    main()
