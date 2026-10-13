#!/usr/bin/env python3
"""Publish only bounded, verified native AV evidence from disposable v0.1 CI.

The original Broker/Driver Host receipts and daemon/process diagnostics stay
inside the CI runner, never in the downloadable public artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from cross_app_public_artifact import (
    PublicationError, read_bounded, require, strict_object,
)

SCHEMA = "motionwright.v01-native-av-public-evidence/1"
SHA = re.compile(r"[a-f0-9]{40}\Z")
ALLOWED_MEDIA = {
    "motionwright-master.mp4": 256_000_000,
    "frame-first.png": 16_000_000,
    "frame-middle.png": 16_000_000,
    "frame-last.png": 16_000_000,
    "artifact-manifest.json": 4_000_000,
    "decoded-media-proof.json": 256_000,
}


def publish_av(root: Path, out: Path, source_sha: str,
               semwright_sha: str, job_status: str) -> dict:
    require(bool(SHA.fullmatch(source_sha)) and bool(SHA.fullmatch(semwright_sha)),
            "source_identity_invalid")
    require(job_status in {"success", "failure", "cancelled", "skipped"},
            "ci_status_invalid")
    require(not out.exists(), "public_artifact_already_exists")
    src = root.resolve()
    dst = out.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "public_root_overlaps_private_source")
    artifacts: dict[str, bytes] = {}
    if job_status == "success":
        require(root.is_dir() and not root.is_symlink(),
                "missing_or_linked_private_evidence_root")
        result = strict_object(read_bounded(root, "result.json", 128_000))
        require(result.get("native_av_master_e2e") == "PASS"
                and result.get("motionwright_sha") == source_sha
                and result.get("semwright_sha") == semwright_sha
                and type(result.get("frame_count")) is int
                and result["frame_count"] == 60,
                "native_av_not_verified")
        for name, limit in ALLOWED_MEDIA.items():
            artifacts[name] = read_bounded(root, name, limit)
        mp4 = artifacts["motionwright-master.mp4"]
        require(len(mp4) > 12 and mp4[4:8] == b"ftyp", "invalid_native_mp4")
        manifest_sha = hashlib.sha256(artifacts["artifact-manifest.json"]).hexdigest()
        master_sha = hashlib.sha256(mp4).hexdigest()
        require(manifest_sha == result.get("artifact_manifest_sha256")
                and master_sha == result.get("master_sha256"),
                "native_av_media_digest_mismatch")
        proof = strict_object(artifacts["decoded-media-proof.json"])
        require(proof.get("evidence_scope")
                == "actual_decoded_h264_aac_two_second_fixture_only"
                and proof.get("master_sha256") == master_sha
                and proof.get("source_wav_sha256") == result.get("audio_sha256")
                and type(proof.get("video_frames_decoded")) is int
                and proof["video_frames_decoded"] == 60
                and proof.get("video_movement_decoded") is True,
                "native_av_decoded_media_mismatch")

    files = [
        {"name": name, "bytes": len(content),
         "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in sorted(artifacts.items())
    ]
    receipt = {
        "schema": SCHEMA,
        "motionwright_source_sha": source_sha,
        "semwright_source_pin": semwright_sha,
        "ci_native_av_evidence": "PASS" if job_status == "success" else "NOT_VERIFIED",
        "native_video_frames": 60 if job_status == "success" else None,
        "included_files": files,
        "owner_connection_files_uploaded": False,
        "raw_daemon_or_provider_logs_uploaded": False,
        "original_native_job_receipts_uploaded": False,
        "native_mutations_replayed_by_publisher": False,
        "installed_owner_acceptance": "NOT_RUN",
        "v01_release_authorized": False,
    }
    out.mkdir(parents=True, exist_ok=False)
    for name, content in artifacts.items():
        with (out / name).open("xb") as stream:
            stream.write(content)
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
        lock = strict_object((Path(__file__).resolve().parents[1]
                              / "SOURCE_LOCK.json").read_bytes())
        pin = lock["dependencies"]["semwright"]["revision"]
        result = publish_av(args.evidence_root, args.output_root,
                            args.source_sha, pin, args.job_status)
    except (PublicationError, OSError, KeyError, TypeError) as exc:
        code = exc.code if isinstance(exc, PublicationError) else "public_artifact_refused"
        print("native-av-public-artifact=refused reason=" + code, file=sys.stderr)
        return 2
    print("native-av-public-artifact=" + result["ci_native_av_evidence"]
          + " files=" + str(len(result["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
