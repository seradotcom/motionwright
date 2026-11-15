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
TOOLING = ROOT / "tooling"
# Isolated Python runtimes may still point to an older worktree. Load all
# security owner-chain policies from the exact source being attested.
sys.path.insert(0, str(TOOLING))
from glib_owner_chain import OwnerChainError, verify_glib_owner_chain, lock_reverse_edges
for _name in ("glib_owner_chain", "v01_glib_product_admission",
              "v01_tauri_ipc_product_admission"):
    _module = sys.modules.get(_name)
    _origin = getattr(_module, "__file__", None)
    if not isinstance(_origin, str) or pathlib.Path(_origin).resolve(strict=True).parent != TOOLING:
        print("ERROR: security advisory guard imported from a different checkout", file=sys.stderr)
        raise SystemExit(1)

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


def verified_font_dependency_edges(lock: dict[str, Any]):
    """Reject unresolved or source-qualified drift before using the owner graph."""
    packages = lock.get("package")
    if not isinstance(packages, list) or not packages:
        fail("Fontations package roster is missing")
    by_name: dict[str, list[dict[str, Any]]] = {}
    for package in packages:
        if (not isinstance(package, dict)
                or not isinstance(package.get("name"), str) or not package["name"]
                or not isinstance(package.get("version"), str) or not package["version"]
                or ("source" in package and (not isinstance(package["source"], str)
                                              or not package["source"]))):
            fail("Fontations package identity is malformed")
        by_name.setdefault(package["name"], []).append(package)
    for package in packages:
        dependencies = package.get("dependencies", [])
        if (not isinstance(dependencies, list)
                or any(not isinstance(value, str) for value in dependencies)
                or len(set(dependencies)) != len(dependencies)):
            fail("Fontations dependency roster is malformed")
        for dependency in dependencies:
            parts = dependency.split(" ")
            if not 1 <= len(parts) <= 3 or any(not part for part in parts):
                fail("Fontations dependency reference is malformed: " + dependency)
            selected = by_name.get(parts[0], [])
            if len(parts) >= 2:
                selected = [p for p in selected if p["version"] == parts[1]]
            if len(parts) == 3:
                selected = [p for p in selected
                            if isinstance(p.get("source"), str)
                            and parts[2] == "(" + p["source"] + ")"]
            if len(selected) != 1:
                fail("Fontations dependency reference is not exactly resolved: " + dependency)
    try:
        return lock_reverse_edges(lock)
    except OwnerChainError as error:
        fail("Fontations dependency graph is not uniquely resolved: " + str(error))


def verify_ttf_parser_review(lock: dict[str, Any], advisory: dict[str, Any]) -> dict[str, Any]:
    """Verify source removal in both owners; installed-product risk stays OPEN."""
    if (advisory.get("id") != "RUSTSEC-2026-0192"
            or advisory.get("package") != "ttf-parser"
            or advisory.get("locked_version") != "0.25.1"
            or advisory.get("class") != "unmaintained"
            or advisory.get("status") != "OPEN_SOURCE_REMOVED_RUNTIME_UNVERIFIED"
            or advisory.get("registry_checksum") !=
            "d2df906b07856748fa3f6e0ad0cbaa047052d4a7dd609e231c4f72cee8c36f31"
            or advisory.get("forbidden_first_party_tokens") != ["ttf_parser", "ttf-parser"]):
        fail("ttf-parser source-removal review changed without review")
    if any(p.get("name") == "ttf-parser" for p in lock["package"]):
        fail("ttf-parser re-entered the reviewed source lock; source removal is not established")
    replacement = {
        "name": "read-fonts",
        "version": "0.47.0",
        "source": "registry+https://github.com/rust-lang/crates.io-index",
        "checksum": "a39d2f8f681030a7bcbbcf3e8568775ac2393afd51401a2e2133ee89e3953897",
    }
    if advisory.get("source_replacement") != replacement:
        fail("Fontations replacement identity changed; review required")
    matching = [p for p in lock["package"] if p.get("name") == replacement["name"]]
    if len(matching) != 1 or any(matching[0].get(k) != v for k, v in replacement.items()):
        fail("Fontations exact registry source, version or checksum changed; review required")
    driver = [p for p in lock["package"] if p.get("name") == "semwright-driver-motion-canvas"]
    reviewed_sdk = (
        "git+https://github.com/seradotcom/semwright?rev=e60e789035b1afe02df127e510b6be533371f567"
        "#e60e789035b1afe02df127e510b6be533371f567"
    )
    if (len(driver) != 1
            or driver[0].get("version") != "1.0.0"
            or driver[0].get("source") != reviewed_sdk
            or "checksum" in driver[0]
            or advisory.get("reviewed_motion_canvas_lock_source") != reviewed_sdk):
        fail("Fontations reviewed Semwright driver source changed; review required")
    local_owner = [p for p in lock["package"] if p.get("name") == "motionwright-manim-profile"]
    if (len(local_owner) != 1 or local_owner[0].get("version") != "0.1.0"
            or "source" in local_owner[0] or "checksum" in local_owner[0]):
        fail("Fontations Manim owner is not the exact local source package")
    reviewed_sources = {
        "crates/manim-profile/Cargo.toml":
            "c85a49e6a1979a2ed07ff0f746bbc4d3d75a9acf15917ef9fad1b263fbbe19ca",
        "crates/manim-profile/src/editable_scene.rs":
            "44e0bb4b39381ecb743657ff5cbba78b4ac91ab8956fe78e2cc128a7007583cf",
        "crates/manim-profile/src/font_tables.rs":
            "ad85a729848f1033c3c6be91f61cec4e63423e7b04dc632ff29fcf22312527af",
    }
    if advisory.get("reviewed_first_party_sources") != reviewed_sources:
        fail("Fontations reviewed first-party source bindings changed")
    import hashlib
    for name, digest in reviewed_sources.items():
        try:
            actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        except OSError:
            fail("Fontations reviewed first-party source is unavailable: " + name)
        if actual != digest:
            fail("Fontations first-party admission source changed; review required: " + name)
    reverse = verified_font_dependency_edges(lock)
    source = ("read-fonts", "0.47.0")
    if source not in reverse:
        fail("Fontations reviewed package missing")
    direct = sorted(f"{name}@{version}" for name, version in reverse[source])
    expected_direct = [
        "motionwright-manim-profile@0.1.0",
        "semwright-driver-motion-canvas@1.0.0",
    ]
    if direct != expected_direct or advisory.get("reviewed_direct_owners") != expected_direct:
        fail("Fontations direct owners changed; review required")
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
        fail("Fontations transitive owners changed; review required")
    return {
        "advisory_id": "RUSTSEC-2026-0192",
        "status": "OPEN_SOURCE_REMOVED_RUNTIME_UNVERIFIED",
        "removed_package": "ttf-parser",
        "removed_locked_version": "0.25.1",
        "source_dependency_absent": True,
        "replacement": replacement,
        "replacement_source_verified": True,
        "reviewed_first_party_sources": reviewed_sources,
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
        source_removed = (advisory_id == "RUSTSEC-2026-0192"
                          and status == "OPEN_SOURCE_REMOVED_RUNTIME_UNVERIFIED")
        if source_removed:
            # The historical version remains in the policy as the retired review
            # identity. Its replacement and both owners are checked below.
            if package in locked:
                fail(f"{advisory_id}: removed parser is present in the source lock")
        elif locked.get(package) != {version}:
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
        # Source removal is a different reason for absence from the local-path
        # glib audit coverage gap. It is allowed only after the exact replacement
        # and both source owners above were checked against exact source and lock bytes.
        removed_parser = "RUSTSEC-2026-0192"
        if (ttf_parser_review.get("source_dependency_absent") is not True
                or ttf_parser_review.get("replacement_source_verified") is not True):
            fail("ttf-parser audit absence has no verified source replacement")
        if removed_parser in actual_ids:
            fail("RUSTSEC-2026-0192 warning reappeared after verified source removal")
        required_ids = expected_ids - {local_path_advisory, removed_parser}
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
                "source_dependency_present": advisory_id != "RUSTSEC-2026-0192",
            }
            for advisory_id, advisory in sorted(expected.items())
        ],
        "actual_warning_ids": sorted(actual_warnings) if actual_warnings is not None else None,
        "open_advisories_absent_from_registry_audit": (
            sorted(set(expected) - set(actual_warnings))
            if actual_warnings is not None else None
        ),
        "verified_source_removed_advisories": ["RUSTSEC-2026-0192"],
        "registry_audit_coverage_gaps": ["RUSTSEC-2024-0429"],
        "local_path_crate_audit_coverage_complete": False,
        "upstream_glib_advisory_resolved": False,
        "statement": (
            "Known advisories remain OPEN for installed-product review. The source "
            "lock removes ttf-parser and binds Fontations in both prior owners; "
            "installed binary absence and runtime acceptance are not proven. "
            "cargo-audit cannot establish that the "
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
