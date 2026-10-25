#!/usr/bin/env python3
"""Deterministic, source-only Motionwright v0.1.0 release blocker ledger.

This is *not* a release authorizer. It cannot authenticate external reviewers,
installed-device trials, source provenance, signatures or Broker health.
An automatic CI PASS for this script means the BLOCKED verdict is truthful.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
KNOWN_AREAS = (
    "FOUND", "PROJECT", "NARR", "AUDIO", "UX", "TIME", "CANVAS",
    "CONCEPT", "STYLE", "CHANGE", "SDK", "MOTION", "MLT", "BLENDER",
    "MANIM", "GRAPH", "EFFECT", "JOBS", "MODELS", "WORKFLOW",
    "VARIANT", "SEC", "DELIVER", "PERF", "PLATFORM", "EXT",
)
REQUIREMENT_IDS = {
    f"CR-{area}-{index:02d}"
    for area in KNOWN_AREAS for index in range(1, 9)
}
ACCEPTANCE_IDS = {f"ACC-{index:03d}" for index in range(1, 61)}
IMPLEMENTATIONS = {
    "EVIDENCE_PRESENT", "PARTIAL_OR_GAP", "BLOCKED_UPSTREAM",
    "OPTIONAL_UNCLAIMED", "VERIFIED",
}
VERIFICATIONS = {"CI_PARTIAL", "NOT_RUN", "BLOCKED_UPSTREAM", "VERIFIED"}
ACCEPTANCE = {"NOT_RUN", "PASS", "FAIL", "BLOCKED"}
# Only tracked source metadata used for a v0.1 readiness determination.
# Deliberately exclude output receipts, caches and private acceptance assets.
SOURCE_METADATA_PATHS = (
    "Cargo.toml",
    "apps/desktop/src-tauri/tauri.conf.json",
    "SOURCE_LOCK.json",
    "docs/acceptance/requirements-status.json",
    "docs/acceptance/acceptance-status.json",
    "docs/security/known-rustsec-advisories.json",
)


class ReadinessError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def need(condition: bool, code: str) -> None:
    if not condition:
        raise ReadinessError(code)


def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        need(key not in result, "duplicate_json_field")
        result[key] = value
    return result


def _git_read(root: Path, code: str, *args: str) -> str:
    """Run a bounded, read-only Git query; never expose Git stderr or owner paths."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadinessError(code) from exc
    need(result.returncode == 0, code)
    return result.stdout.strip()


def verify_local_checkout(root: Path, expected_sha: str) -> None:
    """Bind a *local* source report to HEAD and unchanged tracked inputs.

    This is not a remote provenance attestation, CI-run signature or release
    authorization. Call both before and after computing a report.
    """
    need(bool(SHA40.fullmatch(expected_sha)), "source_sha_invalid")
    top = _git_read(root, "source_checkout_unavailable", "rev-parse", "--show-toplevel")
    try:
        is_root = Path(top).resolve() == root.resolve()
    except (OSError, ValueError):
        is_root = False
    need(is_root, "source_checkout_root_mismatch")
    head = _git_read(root, "source_checkout_unavailable", "rev-parse",
                     "--verify", "HEAD")
    need(head == expected_sha, "source_checkout_sha_mismatch")
    _git_read(root, "source_metadata_not_tracked", "ls-files",
              "--error-unmatch", "--", *SOURCE_METADATA_PATHS)
    # Git tracks a symlink's TARGET STRING, not the bytes the reporter would
    # read by following it. A clean checkout can therefore still read external
    # mutable content through a committed link or a link in a parent directory.
    # Verify every component, including intermediate directories, on both
    # sides of source parsing. Do not trust an untracked symlink target.
    for relative in SOURCE_METADATA_PATHS:
        path = root
        for part in Path(relative).parts:
            path = path / part
            try:
                # NTFS junctions are reparse points but are not necessarily
                # classified as symbolic links. st_file_attributes is
                # available on Windows even before Python 3.12.
                try:
                    attributes = getattr(path.lstat(), "st_file_attributes", 0)
                except FileNotFoundError:
                    attributes = 0
                link_free = (
                    not path.is_symlink()
                    and not bool(
                        attributes
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    )
                )
            except OSError:
                link_free = False
            need(link_free, "source_metadata_link_refused")
        need(path.is_file(), "source_metadata_not_regular_file")
    _git_read(root, "source_metadata_dirty", "diff", "--quiet",
              "HEAD", "--")


# Bound authentic, append-only predecessor snapshots in the two public ledgers
# without lifting the original ceiling for unrelated readiness source metadata.
MAX_READINESS_JSON_BYTES = 3_000_000
MAX_CONFORMANCE_LEDGER_BYTES = 16_000_000


def source_json(
    path: Path, code: str, *, max_bytes: int = MAX_READINESS_JSON_BYTES
) -> dict:
    try:
        # Every expected source file is first-party checked-in metadata.
        # Cap parser input size to avoid accepting arbitrary CI evidence blobs.
        need(path.stat().st_size <= max_bytes, code)
        data = json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=no_duplicate_keys)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, ReadinessError):
            raise
        raise ReadinessError(code) from exc
    need(isinstance(data, dict), code)
    return data


def source_toml(path: Path, code: str) -> dict:
    try:
        need(path.stat().st_size <= 128_000, code)
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, ReadinessError):
            raise
        raise ReadinessError(code) from exc
    need(isinstance(document, dict), code)
    return document


def exact_ids(entries: object, ids: set[str], code: str) -> list[dict]:
    need(isinstance(entries, list) and len(entries) == len(ids), code)
    need(all(isinstance(x, dict) for x in entries), code)
    received = [x.get("id") for x in entries]
    need(all(isinstance(value, str) for value in received), code)
    need(len(set(received)) == len(ids) and set(received) == ids, code)
    return entries


def summarize(root: Path, tested_source_sha: str) -> dict:
    need(bool(SHA40.fullmatch(tested_source_sha)), "source_sha_invalid")
    cargo = source_toml(root / "Cargo.toml", "workspace_invalid")
    desktop = source_json(root / "apps/desktop/src-tauri/tauri.conf.json",
                          "desktop_invalid")
    lock = source_json(root / "SOURCE_LOCK.json", "source_lock_invalid")
    package = cargo.get("workspace", {}).get("package", {})
    need(isinstance(package, dict), "workspace_invalid")
    need(package.get("version") == desktop.get("version") == "0.1.0",
         "version_mismatch")
    need(package.get("license") == "AGPL-3.0-or-later"
         and package.get("repository") == "https://github.com/seradotcom/motionwright",
         "workspace_identity_invalid")
    need(desktop.get("identifier") == "com.seradotcom.motionwright",
         "desktop_invalid")
    native_lock = lock.get("dependencies", {}).get("semwright")
    need(isinstance(native_lock, dict), "source_lock_invalid")
    pin = native_lock.get("revision")
    need(isinstance(pin, str) and SHA40.fullmatch(pin) is not None
         and native_lock.get("version") == "1.0.0",
         "source_lock_invalid")
    sdk = cargo.get("workspace", {}).get("dependencies", {}).get("semwright-native-sdk")
    need(isinstance(sdk, dict) and sdk.get("rev") == pin
         and sdk.get("git") == native_lock.get("repository")
         and sdk.get("package") == "semwright-native-sdk",
         "native_sdk_pin_mismatch")

    requirements = source_json(
        root / "docs/acceptance/requirements-status.json",
        "requirements_invalid", max_bytes=MAX_CONFORMANCE_LEDGER_BYTES,
    )
    acceptance = source_json(
        root / "docs/acceptance/acceptance-status.json",
        "acceptance_invalid", max_bytes=MAX_CONFORMANCE_LEDGER_BYTES,
    )
    required = exact_ids(requirements.get("requirements"), REQUIREMENT_IDS,
                         "requirements_invalid")
    cases = exact_ids(acceptance.get("cases"), ACCEPTANCE_IDS,
                      "acceptance_invalid")

    for item in required:
        need(item.get("implementation_assessment") in IMPLEMENTATIONS
             and item.get("verification_status") in VERIFICATIONS,
             "requirement_status_invalid")
        evidence = item.get("evidence")
        need(isinstance(evidence, list)
             and all(isinstance(ref, str) and ref for ref in evidence),
             "requirement_evidence_invalid")
        if (item["implementation_assessment"] == "VERIFIED"
                or item["verification_status"] == "VERIFIED"):
            need(bool(evidence), "unsupported_requirement_verified")
    for case in cases:
        need(case.get("status") in ACCEPTANCE, "acceptance_status_invalid")
        evidence = case.get("evidence")
        need(isinstance(evidence, list), "acceptance_evidence_invalid")
        if case["status"] == "PASS":
            # Canonical acceptance evidence is a roster of public source
            # pointers. This aggregate report does not authenticate the review.
            need(case.get("independent") is True and bool(evidence)
                 and all(isinstance(ref, str) and ref.strip() for ref in evidence)
                 and len(evidence) == len(set(evidence)),
                 "unreviewed_acceptance_pass")
        else:
            need(not case.get("independent", False)
                 or case["status"] in {"FAIL", "BLOCKED"},
                 "acceptance_status_invalid")
    implementation = dict(sorted(Counter(
        item["implementation_assessment"] for item in required
    ).items()))
    verification = dict(sorted(Counter(
        item["verification_status"] for item in required
    ).items()))
    case_counts = dict(sorted(Counter(x["status"] for x in cases).items()))
    fully_verified = sum(
        item["implementation_assessment"] == "VERIFIED"
        and item["verification_status"] == "VERIFIED"
        for item in required
    )

    security = source_json(root / "docs/security/known-rustsec-advisories.json",
                           "security_policy_invalid")
    need(security.get("schema") == "motionwright.security-known-advisories.v1",
         "security_policy_invalid")
    alerts = security.get("advisories")
    need(isinstance(alerts, list), "security_policy_invalid")
    ids = []
    for alert in alerts:
        need(isinstance(alert, dict) and isinstance(alert.get("id"), str)
             and bool(re.fullmatch(r"RUSTSEC-\d{4}-\d{4}", alert["id"]))
             and isinstance(alert.get("status"), str)
             and alert["status"].startswith("OPEN_"),
             "security_policy_invalid")
        ids.append(alert["id"])
    need(len(ids) == len(set(ids)), "security_policy_invalid")

    blockers = []
    if fully_verified != len(required):
        blockers.append("requirements_not_independently_verified")
    if case_counts.get("PASS", 0) != len(cases):
        blockers.append("private_acceptance_not_complete")
    if alerts:
        blockers.append("reviewed_upstream_security_warnings_open")
    # These require actual external owner/reviewer evidence and are *never*
    # promoted by inspecting a build tree or green GitHub Actions.
    blockers.extend([
        "owner_runtime_first_render_not_independently_accepted",
        "physical_device_distribution_signing_and_install_not_attested",
        "release_authorization_requires_independent_manual_review",
    ])
    return {
        "schema": "motionwright.v01-release-readiness/1",
        "source_sha": tested_source_sha,
        "semwright_native_sdk_source_pin": pin,
        "version": "0.1.0",
        "license": "AGPL-3.0-or-later",
        "status": "BLOCKED_PENDING_EXTERNAL_EVIDENCE",
        "technical_candidate_ci": "SEPARATE_SHA_BOUND_RESULTS",
        "requirements": {
            "total": len(required), "fully_verified": fully_verified,
            "implementation_counts": implementation,
            "verification_counts": verification,
        },
        "private_acceptance": {
            "total": len(cases), "status_counts": case_counts,
            "independent_evidence_validated_by_this_report": False,
        },
        "reviewed_dependency_warnings": sorted(ids),
        "blockers": blockers,
        "claims": {
            "stable_release_authorized": False,
            "automatic_publish": False,
            "human_acceptance": "NOT_AUTOMATICALLY_ASSESSED",
            "native_first_render_on_owner_device": "NOT_ATTESTED",
            "signing_and_notarization": "NOT_ATTESTED",
        },
    }


def write_receipt(output: Path, payload: dict) -> None:
    """Never erase a previously issued source-bound readiness receipt."""
    need(payload.get("schema") == "motionwright.v01-release-readiness/1",
         "report_invalid")
    try:
        with output.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        raise ReadinessError("receipt_write_refused") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--verify-checkout", action="store_true",
                        help="require exact local Git HEAD and unchanged tracked source inputs")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expect-blocked", action="store_true",
                        help="CI guard: an unexpected automatic release verdict is a failure")
    args = parser.parse_args(argv)
    try:
        if args.verify_checkout:
            verify_local_checkout(ROOT, args.source_sha)
        report = summarize(ROOT, args.source_sha)
        if args.verify_checkout:
            # Recheck after parsing to reject concurrent checkout/source changes.
            verify_local_checkout(ROOT, args.source_sha)
        report["checkout_binding"] = {
            "local_git_head_and_tracked_inputs_checked": args.verify_checkout,
            "remote_commit_attestation": False,
            "installed_binary_attestation": False,
        }
        need(not args.expect_blocked or
             report["status"] == "BLOCKED_PENDING_EXTERNAL_EVIDENCE",
             "unexpected_release_authorization")
        if args.output:
            write_receipt(args.output, report)
    except ReadinessError as exc:
        # No raw source JSON, file paths, reviewer names or exceptions in logs.
        print("v01-readiness-policy=failed code=" + exc.code, file=sys.stderr)
        return 2
    print("v01-release-readiness=" + report["status"]
          + " requirements_verified=" + str(report["requirements"]["fully_verified"])
          + "/208 product_cases_pass=" +
          str(report["private_acceptance"]["status_counts"].get("PASS", 0))
          + "/60", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
