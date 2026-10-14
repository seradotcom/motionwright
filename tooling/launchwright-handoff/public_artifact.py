#!/usr/bin/env python3
"""Publish only a bounded synthetic handoff proof, not the owner source ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from verify_handoff import verify_zip, HandoffVerificationError

SCHEMA = "motionwright.v05-launchwright-public-facts/1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
ZIP_NAME = "motionwright-to-launchwright-SYNTHETIC-NOT_PUBLISHED.zip"
MAX_ZIP = 32_000_000


class PublicRefused(ValueError):
    pass


def require(value: bool, reason: str):
    if not value:
        raise PublicRefused(reason)


def strict_json(path: Path, max_bytes: int) -> dict:
    require(path.is_file() and not path.is_symlink(), "missing_or_linked_status")
    require(0 < path.stat().st_size <= max_bytes, "unbounded_status")
    def no_duplicates(pairs):
        result = {}
        for k, v in pairs:
            require(k not in result, "ambiguous_status_keys")
            result[k] = v
        return result
    try:
        result = json.loads(path.read_bytes(), object_pairs_hook=no_duplicates)
    except (ValueError, UnicodeError, TypeError) as error:
        if isinstance(error, PublicRefused):
            raise
        raise PublicRefused("invalid_status_json") from error
    require(type(result) is dict, "invalid_status_record")
    return result


def digest_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            result.update(block)
    return result.hexdigest()


def publish(private: Path, public: Path, sha: str, status: str) -> dict:
    require(bool(SHA.fullmatch(sha)), "invalid_source_sha")
    require(status in {"success", "failure", "cancelled", "skipped"},
            "invalid_ci_status")
    require(not public.exists() and not public.is_symlink(), "existing_output")
    source, dest = private.resolve(), public.resolve()
    require(source != dest and source not in dest.parents and dest not in source.parents,
            "overlapping_source_and_public")
    result = {
        "schema": SCHEMA, "motionwright_source_sha": sha,
        "technical_handoff": "NOT_VERIFIED",
        "synthetic_fixture_only": True,
        "source_project_uploaded": False, "archive_uploaded": False,
        "owner_claims_or_rights_uploaded": False,
        "launchwright_import": "NOT_RUN",
        "publication_authorized": False, "native_mutation_retried": False,
        "human_source_rights_authenticated": False,
        "human_acceptance": "NOT_RUN",
        "v05_release_ready": False,
        "handoff_archive_sha256": None, "handoff_archive_bytes": None,
    }
    if status == "success":
        require(private.is_dir() and not private.is_symlink(), "invalid_private_source")
        trace = strict_json(private / "status.json", 16_000)
        archive = private / ZIP_NAME
        require(archive.is_file() and not archive.is_symlink(), "missing_private_archive")
        size = archive.stat().st_size
        require(0 < size <= MAX_ZIP, "archive_exceeds_synthetic_budget")
        expected = trace.get("archive_sha256")
        require(trace.get("status") == "SYNTHETIC_HANDOFF_PROTOCOL_ONLY"
                and trace.get("publication_approved") is False
                and trace.get("creative_approval") == "OWNER_RECORDED_NOT_AUTHENTICATED"
                and type(trace.get("archive_bytes")) is int
                and trace["archive_bytes"] == size
                and isinstance(expected, str) and bool(DIGEST.fullmatch(expected))
                and digest_file(archive) == expected, "unverified_synthetic_handoff")
        try:
            verified = verify_zip(archive)
        except (HandoffVerificationError, OSError, ValueError) as error:
            raise PublicRefused("private_handoff_verification_failed") from error
        require(verified.get("byte_identity") == "PASS"
                and verified.get("launchwright_import") == "NOT_RUN"
                and verified.get("publication_approved") is False,
                "invalid_receiver_result")
        result["technical_handoff"] = "PASS"
        result["handoff_archive_sha256"] = expected
        result["handoff_archive_bytes"] = size
    public.mkdir(parents=True, exist_ok=False)
    with (public / "public-evidence.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--public-root", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args()
    try:
        result = publish(args.evidence_root, args.public_root,
                         args.source_sha, args.job_status)
    except (PublicRefused, OSError, TypeError, ValueError) as error:
        reason = str(error) if isinstance(error, PublicRefused) else "public_handoff_refused"
        print("handoff-public=refused reason=" + reason, file=sys.stderr)
        return 1
    print("handoff-public=" + result["technical_handoff"] + " owner-private-archive=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
