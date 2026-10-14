#!/usr/bin/env python3
"""Publish only source-verified v0.1 Motion Canvas CI frames and hashes.

The native job, provider lifetime, Broker session and original source receipts
remain on the ephemeral runner, never in the public artifact.
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

SCHEMA = "motionwright.v01-native-render-public-evidence/1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
VALID_STATUSES = {"success", "failure", "cancelled", "skipped"}
INDICES = (0, 15, 30, 59)
FILES = {
    "artifact-manifest.json": 4_000_000,
    "frame-first.png": 16_000_000,
    "frame-middle.png": 16_000_000,
    "frame-last.png": 16_000_000,
    **{
        f"{leg}-native-frame-{frame:06}.png": 16_000_000
        for leg in ("direct", "mediated") for frame in INDICES
    },
}


def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def checked_source(root: Path, source: str, sdk: str) -> tuple[dict[str, bytes], str]:
    require(root.is_dir() and not root.is_symlink(),
            "missing_or_linked_private_evidence_root")
    result = strict_object(read_bounded(root, "result.json", 128_000))
    parity = strict_object(read_bounded(root, "native-direct-mediated-parity.json", 128_000))
    film_sha = result.get("direct_mediated_same_film_sha256")
    require(
        result.get("native_render_e2e") == "PASS"
        and result.get("motionwright_sha") == source
        and result.get("semwright_sha") == sdk
        and type(result.get("frame_count")) is int
        and result["frame_count"] == 60
        and result.get("linear_position_motion_sampled") is True
        and result.get("linear_opacity_motion_sampled") is True
        and result.get("direct_mediated_native_frame_parity") == "PASS"
        and isinstance(film_sha, str) and bool(SHA256.fullmatch(film_sha)),
        "native_render_source_not_verified",
    )
    pixels = parity.get("pixels")
    project = result.get("project")
    require(
        parity.get("schema") == "motionwright-native-direct-mediated-parity/1"
        and parity.get("motionwright_sha") == source
        and parity.get("semwright_sha") == sdk
        and parity.get("source_film_sha256") == film_sha
        and isinstance(project, dict)
        and parity.get("project_generation") == project.get("generation")
        and parity.get("project_revision") == project.get("revision")
        and parity.get("direct_job_distinct_from_mediated") is True
        and type(parity.get("direct_native_validation_checks")) is int
        and parity["direct_native_validation_checks"] > 0
        and parity.get("human_visual_acceptance") == "NOT_RUN"
        and parity.get("arbitrary_renderer_property_parity") == "NOT_PROVEN"
        and isinstance(pixels, dict)
        and pixels.get("frame_count") == 60
        and pixels.get("raw_rgba_pixel_format") == "rgba_1920x1080"
        and pixels.get("all_frames_decoded_rgba_pixel_identical") is True
        and pixels.get("checked_frames") == list(INDICES)
        and isinstance(pixels.get("ordered_rgba_frame_digests_sha256"), str)
        and bool(SHA256.fullmatch(pixels["ordered_rgba_frame_digests_sha256"])),
        "native_render_parity_not_verified",
    )
    manifest = parity.get("native_manifest_sha256")
    require(isinstance(manifest, dict)
            and manifest.get("mediated") == result.get("artifact_manifest_sha256")
            and isinstance(manifest.get("direct"), str)
            and bool(SHA256.fullmatch(manifest["direct"])),
            "native_render_manifest_identity_mismatch")
    blobs = {name: read_bounded(root, name, limit) for name, limit in FILES.items()}
    require(
        sha256(blobs["artifact-manifest.json"]) == result["artifact_manifest_sha256"],
        "native_render_manifest_digest_mismatch",
    )
    review = result.get("review_frames")
    require(
        isinstance(review, dict)
        and review.get("first_sha256") == sha256(blobs["frame-first.png"])
        and review.get("middle_sha256") == sha256(blobs["frame-middle.png"])
        and review.get("last_sha256") == sha256(blobs["frame-last.png"]),
        "native_render_review_hash_mismatch",
    )
    # PNG file hashes cannot prove visual equality: the native E2E separately
    # decoded RGBA for all 60 frames. Do not claim PNG container identity.
    require(type(pixels.get("png_container_byte_identical")) is bool
            and type(pixels.get("png_container_mismatch_count")) is int
            and 0 <= pixels["png_container_mismatch_count"] <= 60,
            "native_render_parity_not_verified")
    return blobs, pixels["ordered_rgba_frame_digests_sha256"]


def publish(root: Path, out: Path, source: str, sdk: str, ci_status: str) -> dict:
    require(bool(SHA40.fullmatch(source)) and bool(SHA40.fullmatch(sdk)),
            "source_identity_invalid")
    require(ci_status in VALID_STATUSES, "ci_status_invalid")
    require(not out.exists(), "public_artifact_already_exists")
    src = root.resolve()
    dest = out.resolve()
    require(src != dest and src not in dest.parents and dest not in src.parents,
            "public_root_overlaps_private_source")
    blobs, rgba_sha = (checked_source(root, source, sdk)
                       if ci_status == "success" else ({}, None))
    receipt = {
        "schema": SCHEMA,
        "motionwright_source_sha": source,
        "semwright_source_pin": sdk,
        "ci_native_motion_canvas_evidence": "PASS" if ci_status == "success" else "NOT_VERIFIED",
        "native_frames_per_leg": 60 if ci_status == "success" else None,
        "native_rgba_direct_mediated_parity": (True if ci_status == "success" else None),
        "native_ordered_rgba_digest_sha256": rgba_sha,
        "png_container_binary_equality_claimed": False,
        "original_owner_receipts_uploaded": False,
        "raw_daemon_provider_logs_uploaded": False,
        "owner_session_material_uploaded": False,
        "native_mutation_replayed_by_publisher": False,
        "intermittent_provider_generation_issue_fixed": False,
        "installed_owner_acceptance": "NOT_RUN",
        "v01_release_authorized": False,
        "included_files": [
            {"name": name, "bytes": len(data), "sha256": sha256(data)}
            for name, data in sorted(blobs.items())
        ],
    }
    out.mkdir(parents=True, exist_ok=False)
    for name, data in blobs.items():
        with (out / name).open("xb") as handle:
            handle.write(data)
    with (out / "public-evidence.json").open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, sort_keys=True, indent=2)
        handle.write("\n")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args(argv)
    try:
        lock = strict_object((Path(__file__).resolve().parents[1] /
                              "SOURCE_LOCK.json").read_bytes())
        sdk = lock["dependencies"]["semwright"]["revision"]
        receipt = publish(args.evidence_root, args.output_root,
                          args.source_sha, sdk, args.job_status)
    except (PublicationError, OSError, KeyError, TypeError) as exc:
        reason = exc.code if isinstance(exc, PublicationError) else "public_artifact_refused"
        print("native-render-public-artifact=refused reason=" + reason, file=sys.stderr)
        return 2
    print("native-render-public-artifact=" + receipt["ci_native_motion_canvas_evidence"]
          + " files=" + str(len(receipt["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
