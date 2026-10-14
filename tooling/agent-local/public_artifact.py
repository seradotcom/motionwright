#!/usr/bin/env python3
"""Source-bound public *facts*, never the owner-local two-client event journal.

The native SDK actors and SQLite database execute only on the disposable CI
runner. This step cannot open a host or commit any project change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SCHEMA = "motionwright.v05-local-clients-public-facts/1"
SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")
EXPECTED_SCOPES = ["summary", "timeline", "history"]
MAX_REPORT = 192_000


class Refused(ValueError):
    """A fail-closed public evidence admission failure."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise Refused(code)


def strict_object(data: bytes) -> dict:
    def unique(pairs: list[tuple[str, object]]) -> dict:
        output = {}
        for key, value in pairs:
            require(key not in output, "duplicate_owner_report_key")
            output[key] = value
        return output
    try:
        decoded = json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError, TypeError) as error:
        if isinstance(error, Refused):
            raise
        raise Refused("invalid_owner_report_json") from error
    require(type(decoded) is dict, "non_object_owner_report")
    return decoded


def publish(private: Path, public: Path, source_sha: str, job_status: str) -> dict:
    require(bool(SOURCE_SHA.fullmatch(source_sha)), "invalid_ci_source_sha")
    require(job_status in {"success", "failure", "cancelled", "skipped"},
            "invalid_ci_job_status")
    require(not public.exists() and not public.is_symlink(),
            "existing_public_destination")
    src, dst = private.resolve(), public.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "private_public_path_overlap")

    extra: dict = {}
    if job_status == "success":
        file = private / "result.json"
        require(private.is_dir() and not private.is_symlink()
                and file.is_file() and not file.is_symlink()
                and 0 < file.stat().st_size <= MAX_REPORT,
                "missing_or_unbounded_local_client_source")
        source_bytes = file.read_bytes()
        report = strict_object(source_bytes)
        sdk_count = report.get("native_sdk_catalog_operations")
        events = report.get("events")
        require(
            report.get("schema") == "motionwright.v05-local-agent-two-process/1"
            and report.get("status") == "PASS_OWNER_LOCAL_CAS_AND_NATIVE_SDK_READ"
            and report.get("same_sha") == source_sha
            and report.get("same_sqlite_project") is True
            and report.get("independent_subprocess_per_request") is True
            and report.get("shared_chat") is False
            and type(sdk_count) is int and 50 <= sdk_count <= 512
            and report.get("sdk_observation_scopes") == EXPECTED_SCOPES
            and report.get("owner_local_cas_revisions") == 3
            and report.get("stale_revision_rejected") is True
            and report.get("stale_page_cursor_rejected") is True
            and report.get("unknown_operation_rejected") is True
            and report.get("cross_generation_rejected") is True
            and report.get("read_only_preview") == "PASS"
            and report.get("canonical_database_backend") == "existing_studio_service"
            and report.get("host_authenticated_agent_write") == "NOT_TESTED_OR_GRANTED"
            and report.get("remote_listener") == "NONE"
            and report.get("creative_approval") == "NOT_RUN"
            and type(events) is list and 18 <= len(events) <= 128
            and all(type(e) is dict and e.get("actor") in ("owner", "agent_a", "agent_b")
                    and e.get("action") in ("init", "discover", "observe", "preview", "commit")
                    for e in events)
            and any(e.get("actor") == "agent_a" for e in events)
            and any(e.get("actor") == "agent_b" for e in events),
            "unverified_or_unauthorized_two_client_claim",
        )
        extra = {
            "registered_native_sdk_operations": sdk_count,
            "independent_os_invocations": len(events),
            "owner_local_committed_revisions": 3,
            "private_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        }

    manifest = {
        "schema": SCHEMA,
        "motionwright_source_sha": source_sha,
        "ci_evidence": "PASS" if job_status == "success" else "NOT_VERIFIED",
        **extra,
        "owner_project_data_uploaded": False,
        "owner_database_or_sqlite_uploaded": False,
        "owner_request_payloads_uploaded": False,
        "raw_process_stdout_stderr_uploaded": False,
        "native_mutation_retried_by_publication": False,
        "local_os_owner_opt_in_required": True,
        "host_authenticated_remote_write": "NOT_TESTED_OR_GRANTED",
        "teams_or_rbac_authorized": False,
        "shared_chat_authority": False,
        "human_creative_acceptance": "NOT_RUN",
        "v05_release_ready": False,
    }
    public.mkdir(parents=True, exist_ok=False)
    with (public / "public-evidence.json").open("x", encoding="utf-8") as output:
        json.dump(manifest, output, sort_keys=True, indent=2)
        output.write("\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args()
    try:
        receipt = publish(args.private_root, args.public_root,
                          args.source_sha, args.job_status)
    except (Refused, OSError, TypeError, ValueError) as error:
        code = str(error) if isinstance(error, Refused) else "public_local_client_refused"
        print("local-client-public=refused reason=" + code, file=sys.stderr)
        return 1
    print("local-client-public=" + receipt["ci_evidence"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
