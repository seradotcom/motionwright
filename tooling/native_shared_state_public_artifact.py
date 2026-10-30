#!/usr/bin/env python3
"""Release only verified, redacted v0.1 shared-state CI facts.

Never upload owner sessions, raw Native SDK envelopes, CLI calls, daemon logs,
provider paths or project IDs from the disposable Broker/Driver Host.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

from cross_app_public_artifact import PublicationError, read_bounded, require, strict_object

SCHEMA = "motionwright.v01-native-shared-state-public/1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_CHECKS = frozenset({
    "ui_seed_visible_through_host",
    "sdk_write_visible_to_ui_service",
    "ui_write_invalidates_stale_sdk_ref",
    "fresh_sdk_observes_ui_write",
    "forged_actor_field_rejected",
    "cross_project_ref_rejected",
    "two_sessions_share_one_base",
    "two_sessions_preview_through_real_host",
    "preview_preserves_store_identity_rows_receipts_and_events",
    "preview_payload_resource_override_error_retained",
    "preview_refs_continue_into_real_cas",
    "stale_preview_error_retained",
    "concurrent_cas_has_exactly_one_winner",
    "concurrent_loser_is_stale",
    "final_sdk_write_visible_to_ui_service",
})
EXPECTED_COMMANDS = {
    "driver.motionwright.observe": 6,
    "driver.motionwright.project.rename": 7,
    "driver.motionwright.project.preview": 4,
}


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def parse_private_call_list(blob: bytes) -> list[dict]:
    def no_duplicate(pairs: list[tuple[str, object]]) -> dict:
        obj: dict = {}
        for key, value in pairs:
            require(key not in obj, "duplicate_private_call_key")
            obj[key] = value
        return obj

    try:
        result = json.loads(blob.decode("utf-8"), object_pairs_hook=no_duplicate)
    except (ValueError, UnicodeError) as error:
        if isinstance(error, PublicationError):
            raise
        raise PublicationError("invalid_private_call_receipts") from error
    require(isinstance(result, list) and len(result) == 17,
            "incomplete_private_call_receipts")
    command_counts: Counter[str] = Counter()
    succeeded = 0
    failed = 0
    for entry in result:
        require(isinstance(entry, dict)
                and set(entry) == {"args", "command", "envelope", "exit_code", "session"}
                and isinstance(entry["args"], dict)
                and isinstance(entry["command"], str)
                and isinstance(entry["session"], str)
                and bool(entry["session"])
                and type(entry["exit_code"]) is int
                and isinstance(entry["envelope"], dict)
                and type(entry["envelope"].get("ok")) is bool,
                "invalid_private_call_receipt_shape")
        okay = entry["envelope"]["ok"]
        require((entry["exit_code"] == 0) is okay,
                "contradictory_private_native_call_status")
        if not okay:
            error = entry["envelope"].get("error")
            require(isinstance(error, dict) and error.get("outcome_known") is True,
                    "private_native_call_known_denial_unverified")
        command_counts[entry["command"]] += 1
        succeeded += int(okay)
        failed += int(not okay)
    require(dict(command_counts) == EXPECTED_COMMANDS and succeeded == 10 and failed == 7,
            "private_native_call_sequence_unverified")
    return result


def verify_private_shared_state(root: Path, source: str, pin: str) -> dict:
    require(root.is_dir() and not root.is_symlink(),
            "missing_or_linked_private_evidence_root")
    private_result = read_bounded(root, "result.json", 128_000)
    result = strict_object(private_result)
    require(
        set(result) == {"checks", "motionwright_sha", "project",
                        "provider_sha256", "semwright_sha"}
        and result["motionwright_sha"] == source
        and result["semwright_sha"] == pin
        and isinstance(result["provider_sha256"], str)
        and bool(SHA256.fullmatch(result["provider_sha256"])),
        "native_shared_state_source_not_verified",
    )
    checks = result["checks"]
    require(isinstance(checks, dict) and set(checks) == EXPECTED_CHECKS
            and all(value is True for value in checks.values()),
            "native_shared_state_checks_not_verified")
    project = result["project"]
    require(isinstance(project, dict)
            and set(project) == {"id", "resource", "generation", "final_revision", "final_title"}
            and all(isinstance(project[k], str) and bool(project[k])
                    for k in ("id", "resource", "generation"))
            and project["final_title"] == "SDK final shared state"
            and isinstance(project["final_revision"], (int, str))
            and str(project["final_revision"]).isdecimal()
            and int(project["final_revision"]) > 0,
            "native_shared_state_final_project_unverified")
    private_calls = read_bounded(root, "calls.json", 512_000)
    parse_private_call_list(private_calls)
    return {
        "private_result_sha256": digest(private_result),
        "private_calls_sha256": digest(private_calls),
        "checks_verified": len(checks),
        "native_calls_verified": 17,
    }


def publish(root: Path, out: Path, source: str, pin: str, status: str) -> dict:
    require(bool(SHA40.fullmatch(source)) and bool(SHA40.fullmatch(pin)),
            "source_identity_invalid")
    require(status in {"success", "failure", "cancelled", "skipped"},
            "ci_job_status_invalid")
    require(not out.exists(), "public_artifact_already_exists")
    src = root.resolve()
    dst = out.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "public_root_overlaps_private_evidence")
    verified = (
        verify_private_shared_state(root, source, pin)
        if status == "success" else None
    )
    receipt = {
        "schema": SCHEMA,
        "motionwright_source_sha": source,
        "semwright_source_pin": pin,
        "native_shared_state_ci": "PASS" if verified is not None else "NOT_VERIFIED",
        "verified_checks": sorted(EXPECTED_CHECKS) if verified is not None else [],
        "verified_native_invocations": verified["native_calls_verified"] if verified else None,
        "source_result_digest_sha256": verified["private_result_sha256"] if verified else None,
        "source_calls_digest_sha256": verified["private_calls_sha256"] if verified else None,
        "raw_native_cli_calls_uploaded": False,
        "owner_session_or_project_refs_uploaded": False,
        "raw_daemon_or_provider_logs_uploaded": False,
        "native_effect_replayed_by_publisher": False,
        "installed_owner_acceptance": "NOT_RUN",
        "v01_release_authorized": False,
    }
    out.mkdir(parents=True, exist_ok=False)
    with (out / "public-evidence.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args(argv)
    try:
        lock = strict_object(
            (Path(__file__).resolve().parents[1] / "SOURCE_LOCK.json").read_bytes()
        )
        pin = lock["dependencies"]["semwright"]["revision"]
        result = publish(args.evidence_root, args.output_root,
                         args.source_sha, pin, args.job_status)
    except (PublicationError, OSError, TypeError, KeyError, ValueError) as error:
        reason = error.code if isinstance(error, PublicationError) else "native_shared_public_refused"
        print("native-shared-public=refused reason=" + reason, file=sys.stderr)
        return 2
    print("native-shared-public=" + result["native_shared_state_ci"]
          + " files=1 checks=" + str(len(result["verified_checks"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
