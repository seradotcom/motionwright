#!/usr/bin/env python3
"""Publish only source-bound synthetic observation facts, no owner/observer keys.

The signed observation is checked separately by verify_observer.py in the
disposable runner. This boundary never executes source or publishes a frame.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from verify_observer import (AdmissionRejected, png_dimensions, strict_json)

SCHEMA = "motionwright.v05-independent-observer-public/1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")


class PublicationRefused(ValueError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise PublicationRefused(reason)


def bounded_json(folder: Path, name: str) -> dict:
    f = folder / name
    require(f.is_file() and not f.is_symlink() and
            0 < f.stat().st_size <= 48_000, "untrusted_or_unbounded_ci_result")
    try:
        result = strict_json(f.read_bytes(), name)
    except AdmissionRejected as error:
        raise PublicationRefused("ambiguous_or_invalid_observation_json") from error
    require(type(result) is dict, "observation_not_an_object")
    return result


def publish(private: Path, public: Path, sha: str, status: str) -> dict:
    require(bool(SHA40.fullmatch(sha)), "invalid_motionwright_source_sha")
    require(status in {"success", "failure", "cancelled", "skipped"},
            "invalid_ci_status")
    require(not public.exists() and not public.is_symlink(),
            "public_target_already_exists")
    source, dest = private.resolve(), public.resolve()
    require(source != dest and source not in dest.parents and
            dest not in source.parents, "private_public_directory_overlap")
    png_sha = None
    measured = None
    if status == "success":
        require(private.is_dir() and not private.is_symlink(),
                "missing_private_ci_evidence")
        observation = bounded_json(private, "source-observation.json")
        revocation = bounded_json(private, "owner-revocation-experiment.json")
        frame = private / "native-source-frame-000030.png"
        require(frame.is_file() and not frame.is_symlink() and
                0 < frame.stat().st_size <= 32 * 1024 * 1024,
                "missing_or_unbounded_ci_png")
        try:
            dims = png_dimensions(frame)
        except (AdmissionRejected, OSError) as error:
            raise PublicationRefused("unverified_ci_png") from error
        require(dims == (320, 180), "unexpected_ci_fixture_dimensions")
        png_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
        measured = {"width": dims[0], "height": dims[1]}
        require(
            observation.get("schema") ==
                "motionwright.observation-admission-report/1"
            and observation.get("status") ==
                "CI_TEST_ONLY_NOT_AN_OWNER_PRODUCTION_ADMISSION"
            and observation.get("ci_test_classification") ==
                "EPHEMERAL_SYNTHETIC_KEYS_NO_REAL_OWNER_AUTHENTICATION"
            and observation.get("motionwright_source_sha") == sha
            and observation.get("source_frame_sha256") == png_sha
            and type(observation.get("measured_pixels")) is dict
            and observation["measured_pixels"] == measured
            and observation.get("owner_signed_admission") == "VERIFIED"
            and observation.get("independent_verifier_signed_observation") ==
                "VERIFIED"
            and observation.get("actual_source_png_decode") == "VERIFIED"
            and observation.get("verifier_source_code_executed") is False
            and observation.get("producer_execution_granted") is False
            and observation.get("publication_approved") is False
            and observation.get("creative_quality_approved") is False
            and observation.get("gate_eligible") is False
            and revocation.get("schema") ==
                "motionwright.observation-ci-revocation/1"
            and revocation.get("motionwright_source_sha") == sha
            and revocation.get("original_png_sha256") == png_sha
            and revocation.get("signed_owner_policy_revoked_current_admission") ==
                "VERIFIED"
            and revocation.get("current_gate_eligible") is False
            and revocation.get("historical_observation_bytes_preserved") is True
            and revocation.get("artifact_contains_private_keys") is False
            and revocation.get("trusted_key_provisioning") == "EPHEMERAL_CI_ONLY"
            and revocation.get("real_production_host_admission") == "NOT_RUN",
            "unverified_or_foreign_signed_ci_observation",
        )
        require(bool(SHA64.fullmatch(png_sha)), "invalid_ci_source_digest")
    facts = {
        "schema": SCHEMA, "motionwright_source_sha": sha,
        "technical_ci_evidence": "PASS" if status == "success" else "NOT_VERIFIED",
        "synthetic_frame_sha256": png_sha,
        "synthetic_decoded_pixels": measured,
        "separate_ephemeral_keys_checked": status == "success",
        "revocation_blocks_current_admission": status == "success",
        "original_png_uploaded": False, "signing_keys_uploaded": False,
        "owner_policy_or_signed_observation_uploaded": False,
        "source_verifier_executed_by_publisher": False,
        "production_verifier_admitted": False, "publication_authorized": False,
        "real_owner_rights_authenticated": False,
        "creative_quality_human_approved": False,
        "human_acceptance": "NOT_RUN", "v05_release_ready": False,
    }
    public.mkdir(parents=True, exist_ok=False)
    with (public / "public-evidence.json").open("x", encoding="utf-8") as f:
        json.dump(facts, f, indent=2, sort_keys=True)
        f.write("\n")
    return facts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--public-root", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args()
    try:
        data = publish(args.evidence_root, args.public_root,
                       args.source_sha, args.job_status)
    except (PublicationRefused, OSError, ValueError, TypeError) as error:
        print("observer-public=refused reason=" +
              (str(error) if isinstance(error, PublicationRefused)
               else "public_observer_refused"), file=sys.stderr)
        return 1
    print("observer-public=" + data["technical_ci_evidence"] + " public=manifest-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
