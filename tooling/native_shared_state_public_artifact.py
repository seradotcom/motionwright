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
import math
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
    "driver.motionwright.observe": 12,
    "driver.motionwright.project.rename": 8,
    "driver.motionwright.project.preview": 7,
}
EXPECTED_ERRORS = {
    ("driver.motionwright.observe", "PolicyDenied"): 1,
    ("driver.motionwright.project.rename", "StaleReference"): 3,
    ("driver.motionwright.project.rename", "InvalidArgument"): 1,
    ("driver.motionwright.project.preview", "InvalidArgument"): 1,
    ("driver.motionwright.project.preview", "StaleReference"): 1,
}
CASE_COMMANDS = {
    "positive": {"observe": 6, "project.rename": 3, "project.preview": 3},
    "stale-ui": {"observe": 1, "project.rename": 1},
    "stale-preview": {"observe": 1, "project.preview": 2},
    "stale-cas": {"observe": 2, "project.rename": 2},
    "cas-race": {"observe": 2, "project.preview": 2, "project.rename": 2},
}
CORE_ERRORS = {
    ("driver.motionwright.observe", "PolicyDenied"): 1,
    ("driver.motionwright.project.rename", "InvalidArgument"): 1,
    ("driver.motionwright.project.preview", "InvalidArgument"): 1,
}
TERMINAL_TITLES = {
    "stale-ui": {"must not commit from stale ref"},
    "stale-preview": {"stale preview must be rejected"},
    "stale-cas": {"old default-session ref must stay stale"},
    "cas-race": {"Human contender", "Agent contender"},
}
NATIVE_CALLS = 27
RECEIPT = re.compile(r"[0-9a-f]{32}\Z")


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def parse_private_json(blob: bytes):
    def no_duplicate(pairs: list[tuple[str, object]]) -> dict:
        obj: dict = {}
        for key, value in pairs:
            require(key not in obj, "duplicate_private_call_key")
            obj[key] = value
        return obj

    def reject_nonfinite(_value: str):
        raise ValueError("nonfinite private call JSON")

    def finite_float(raw: str) -> float:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("unrepresentable private call JSON number")
        return value

    try:
        return json.loads(blob.decode("utf-8"), object_pairs_hook=no_duplicate,
                          parse_constant=reject_nonfinite, parse_float=finite_float)
    except (ValueError, UnicodeError) as error:
        if isinstance(error, PublicationError):
            raise
        raise PublicationError("invalid_private_call_receipts") from error


def parse_private_call_list(blob: bytes) -> list[dict]:
    result = parse_private_json(blob)
    require(isinstance(result, list) and len(result) == NATIVE_CALLS,
            "incomplete_private_call_receipts")
    command_counts: Counter[str] = Counter()
    error_counts: Counter[tuple[str, str]] = Counter()
    succeeded = 0
    failed = 0
    for entry in result:
        require(isinstance(entry, dict)
                and set(entry) == {"args", "command", "envelope", "exit_code", "session",
                                   "case", "owner", "receipt"}
                and isinstance(entry["args"], dict)
                and isinstance(entry["command"], str)
                and isinstance(entry["session"], str)
                and bool(entry["session"])
                and isinstance(entry["case"], str) and entry["case"] in CASE_COMMANDS
                and isinstance(entry["owner"], str) and bool(entry["owner"])
                and isinstance(entry["receipt"], str) and bool(RECEIPT.fullmatch(entry["receipt"]))
                and type(entry["exit_code"]) is int
                and isinstance(entry["envelope"], dict)
                and type(entry["envelope"].get("ok")) is bool,
                "invalid_private_call_receipt_shape")
        okay = entry["envelope"]["ok"]
        require((entry["exit_code"] == 0) is okay,
                "contradictory_private_native_call_status")
        if not okay:
            error = entry["envelope"].get("error")
            known = entry["case"] == "positive"
            require(isinstance(error, dict) and error.get("outcome_known") is known,
                    "private_native_call_known_denial_unverified" if known
                    else "private_native_terminal_unknown_unverified")
            require(isinstance(error.get("code"), str),
                    "invalid_private_native_error")
            error_counts[(entry["command"], error["code"])] += 1
        command_counts[entry["command"]] += 1
        succeeded += int(okay)
        failed += int(not okay)
    require(dict(command_counts) == EXPECTED_COMMANDS and succeeded == 20 and failed == 7,
            "private_native_call_sequence_unverified")
    require(dict(error_counts) == EXPECTED_ERRORS,
            "private_native_error_sequence_unverified")
    require(len({entry["receipt"] for entry in result}) == NATIVE_CALLS,
            "duplicate_private_native_receipt")
    require([entry["case"] for entry in result] == [
        case for case, commands in CASE_COMMANDS.items() for _ in range(sum(commands.values()))
    ], "private_native_case_sequence_unverified")
    for case, commands in CASE_COMMANDS.items():
        rows = [entry for entry in result if entry["case"] == case]
        require(dict(Counter(entry["command"] for entry in rows)) == {
            "driver.motionwright." + command: count for command, count in commands.items()
        }, "private_native_case_command_roster_unverified")
        failures = [entry for entry in rows if not entry["envelope"]["ok"]]
        if case == "positive":
            require(dict(Counter((entry["command"], entry["envelope"]["error"]["code"])
                                 for entry in failures)) == CORE_ERRORS,
                    "private_native_core_denial_roster_unverified")
            continue
        terminal = rows[-(2 if case == "cas-race" else 1):]
        require(len(failures) == 1 and failures[0] in terminal
                and failures[0]["envelope"]["error"]["code"] == "StaleReference",
                "private_native_terminal_position_unverified")
        titles = []
        for entry in terminal:
            args = entry["args"]
            preview = case == "stale-preview"
            payload = args.get("edit") if preview else args
            require(entry["command"] == "driver.motionwright.project." + ("preview" if preview else "rename")
                    and isinstance(args.get("ref"), str) and bool(args["ref"])
                    and set(args) == ({"ref", "edit"} if preview else {"ref", "title"})
                    and isinstance(payload, dict) and isinstance(payload.get("title"), str)
                    and (not preview or (set(payload) == {"operation", "title"}
                                         and payload["operation"] == "project.rename")),
                    "private_native_terminal_request_unverified")
            titles.append(payload["title"])
        require(set(titles) == TERMINAL_TITLES[case], "private_native_terminal_request_unverified")
        if case == "cas-race":
            require(len({entry["session"] for entry in terminal}) == 2,
                    "private_native_race_sessions_unverified")
    return result


def same_private_value(left, right) -> bool:
    # Preserve full typed values, including bool versus int and every Error field.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def private_stream(root: Path, name: str) -> bytes:
    path = root / name
    require(path.is_file() and not path.is_symlink(), "missing_or_linked_public_source")
    size = path.stat().st_size
    require(size <= 256_000, "oversized_public_source")
    raw = path.read_bytes()
    require(len(raw) == size, "source_changed_during_publication")
    return raw


def verify_case_originals(root: Path, cases: dict, calls: list[dict], provider: str) -> None:
    require(isinstance(cases, dict) and set(cases) == set(CASE_COMMANDS),
            "private_native_case_roster_unverified")
    owners, databases, identities, receipts = set(), set(), set(), set()
    connections, bindings = {}, set()
    for case in CASE_COMMANDS:
        row = cases[case]
        require(isinstance(row, dict)
                and set(row) == {"owner", "database", "provider_sha256", "native_calls", "terminal"}
                and isinstance(row["owner"], str), "private_native_case_identity_unverified")
        owner = Path(row["owner"])
        database = row["database"]
        require(owner.is_absolute() and str(owner) == row["owner"] and ".." not in owner.parts
                and all(owner != previous and owner not in previous.parents and previous not in owner.parents
                        for previous in owners)
                and isinstance(database, dict) and set(database) == {"path", "device", "inode"}
                and isinstance(database["path"], str)
                and type(database["device"]) is int and database["device"] >= 0
                and type(database["inode"]) is int and database["inode"] > 0,
                "private_native_case_identity_unverified")
        path = Path(database["path"])
        identity = (database["device"], database["inode"])
        require(path.is_absolute() and str(path) == database["path"] and ".." not in path.parts
                and path == owner / "motionwright-data" / "motionwright.sqlite3"
                and path not in databases and identity not in identities
                and row["provider_sha256"] == provider,
                "private_native_case_identity_unverified")
        owners.add(owner)
        databases.add(path)
        identities.add(identity)
        rows = [entry for entry in calls if entry["case"] == case]
        require(type(row["native_calls"]) is int and row["native_calls"] == len(rows)
                and all(entry["owner"] == row["owner"] for entry in rows),
                "private_native_case_identity_unverified")
        terminal = None if case == "positive" else {
            "case": case, "evaluation": "EXPECTED_TERMINAL_UNKNOWN", "outcome_known": False,
            "scope_terminal": True, "effects_absent_asserted": False,
        }
        require(same_private_value(row["terminal"], terminal), "private_native_terminal_evaluation_unverified")
        case_root = root / case
        require(case_root.is_dir() and not case_root.is_symlink()
                and case_root.resolve() == root.resolve() / case,
                "missing_or_linked_private_case_root")
        transport = parse_private_json(read_bounded(case_root, "transport-calls.original.json", 512_000))
        require(isinstance(transport, list), "private_native_transport_unverified")
        by_receipt, mounts = {}, {}
        case_connection = None
        for entry in transport:
            require(isinstance(entry, dict) and set(entry) == {
                "command", "args", "session", "receipt", "automatic_retry", "exit_code", "envelope"}
                and isinstance(entry["command"], str)
                and entry["command"] in {"project.create", "providers.mount.observe", "providers.execute_bound"}
                and isinstance(entry["args"], dict) and isinstance(entry["session"], str) and bool(entry["session"])
                and isinstance(entry["receipt"], str) and bool(RECEIPT.fullmatch(entry["receipt"]))
                and entry["receipt"] not in receipts and entry["automatic_retry"] is False
                and type(entry["exit_code"]) is int and isinstance(entry["envelope"], dict)
                and type(entry["envelope"].get("ok")) is bool
                and ((entry["exit_code"] == 0) is entry["envelope"]["ok"]),
                "private_native_transport_unverified")
            receipt = entry["receipt"]
            receipts.add(receipt)
            by_receipt[receipt] = entry
            original = parse_private_json(private_stream(case_root, receipt + ".stdout.original"))
            private_stream(case_root, receipt + ".stderr.original")
            require(same_private_value(original, entry["envelope"]), "private_native_original_mismatch")
            if entry["command"] != "providers.execute_bound":
                require(entry["envelope"]["ok"] is True, "private_native_setup_outcome_unverified")
            if entry["command"] == "providers.mount.observe":
                observed = entry["envelope"].get("data")
                require(same_private_value(entry["args"], {"provider": "driver:motionwright", "root": "motionwright-data"})
                        and isinstance(observed, dict) and observed.get("provider") == "driver:motionwright"
                        and same_private_value(observed.get("mount"), {
                            "root": "motionwright-data", "path": str(owner / "motionwright-data"),
                            "read": True, "write": True, "execute": False})
                        and isinstance(observed.get("binding_ref"), str) and bool(observed["binding_ref"])
                        and isinstance(observed.get("connection"), str) and bool(observed["connection"])
                        and type(observed.get("provider_generation")) is int and observed["provider_generation"] >= 0,
                        "private_native_mount_unverified")
                connection = (observed["connection"], observed["provider_generation"])
                # A Broker provider lease is shared by sessions in this owner;
                # each session receives its own mount reference. Neither can
                # be borrowed from a different declared terminal owner.
                require(connections.get(observed["connection"], case) == case
                        and (case_connection is None or case_connection == connection)
                        and observed["binding_ref"] not in bindings
                        and entry["session"] not in mounts,
                        "private_native_owner_binding_crossed")
                connections[observed["connection"]] = case
                case_connection = connection
                bindings.add(observed["binding_ref"])
                mounts[entry["session"]] = observed["binding_ref"]
            elif entry["command"] == "providers.execute_bound":
                binding = mounts.get(entry["session"])
                require(binding is not None and entry["args"].get("binding_ref") == binding,
                        "private_native_binding_unverified")
                if entry["envelope"]["ok"]:
                    output = entry["envelope"].get("data")
                    require(isinstance(output, dict) and output.get("binding_ref") == binding,
                            "private_native_binding_unverified")
        native = [entry for entry in transport if entry["command"] == "providers.execute_bound"]
        require({entry["receipt"] for entry in native} == {entry["receipt"] for entry in rows},
                "private_native_transport_roster_unverified")
        for entry in rows:
            original = by_receipt[entry["receipt"]]
            args = original["args"]
            require(set(args) == {"binding_ref", "request"}
                    and isinstance(args["binding_ref"], str) and bool(args["binding_ref"])
                    and same_private_value(args["request"], {
                        "command": entry["command"], "args": entry["args"], "dry_run": False})
                    and original["session"] == entry["session"] and original["exit_code"] == entry["exit_code"]
                    and same_private_value(original["envelope"], entry["envelope"]),
                    "private_native_original_mismatch")
        if case != "positive":
            count = 2 if case == "cas-race" else 1
            require({entry["receipt"] for entry in transport[-count:]} == {
                entry["receipt"] for entry in rows[-count:]
            }, "private_native_terminal_transport_tail_unverified")


def verify_private_shared_state(root: Path, source: str, pin: str) -> dict:
    require(root.is_dir() and not root.is_symlink(),
            "missing_or_linked_private_evidence_root")
    private_result = read_bounded(root, "result.json", 128_000)
    result = strict_object(private_result)
    require(
        set(result) == {"checks", "motionwright_sha", "project",
                        "provider_sha256", "semwright_sha", "cases"}
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
    calls = parse_private_call_list(private_calls)
    verify_case_originals(root, result["cases"], calls, result["provider_sha256"])
    return {
        "private_result_sha256": digest(private_result),
        "private_calls_sha256": digest(private_calls),
        "checks_verified": len(checks),
        "native_calls_verified": NATIVE_CALLS,
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
        "verified_known_core_denials": 3 if verified is not None else 0,
        "verified_terminal_driver_cases": 4 if verified is not None else 0,
        "terminal_driver_outcomes_known": False if verified is not None else None,
        "effects_absent_asserted": False,
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
