#!/usr/bin/env python3
"""v0.1: publish only verified MLT CI media, not Broker or project receipts.

All source observations remain confined to the disposable native CI runner.
This pure file admission stage NEVER contacts Semwright or dispatches a job.
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

SCHEMA = "motionwright.v01-native-mlt-public-evidence/1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
MODES = {"pcm", "video-only", "final-mp4", "captioned-mp4"}
VALID_STATUS = {"success", "failure", "cancelled", "skipped"}
NO_CLEANUP = "not_requested_requires_foreground_broker_consent"
SCOPES = {
    "pcm": "actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master",
    "video-only": "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
    "final-mp4": "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
    "captioned-mp4": "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
}
FINAL_SCOPE = "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved"
CAPTIONED_SCOPE = "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved"
LIMIT = 256_000_000


def file_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_inventory(root: Path, result: dict) -> None:
    inventory_bytes = read_bounded(root, "native-mlt-lifetime-inventory.json", 128_000)
    inventory = strict_object(inventory_bytes)
    project = result.get("project")
    require(
        isinstance(project, dict)
        and inventory.get("project_id") == project.get("project_id")
        and inventory.get("generation") == project.get("generation")
        and inventory.get("revision") == project.get("revision"),
        "inventory_project_scope_mismatch",
    )
    require(file_sha(inventory_bytes) == result.get("native_mlt_lifetime_inventory_sha256"),
            "inventory_digest_mismatch")
    current = inventory.get("confirmed_native_creations_current_revision")
    require(
        inventory.get("schema") == "motionwright.v01-mlt-cleanup-inventory/1"
        and type(current) is int and 1 <= current <= 4
        and current == result.get("native_mlt_lifetime_confirmed_current_creations")
        and type(inventory.get("source_receipts_scanned")) is int
        and inventory["source_receipts_scanned"] == result.get("native_mlt_lifetime_receipts_scanned")
        and inventory.get("complete_source_receipt_snapshot") is True
        and inventory.get("unconfirmed_or_invalid_creation_requests") == 0
        and inventory.get("observed_close_request_identities") == 0
        and result.get("native_mlt_lifetime_close_requests_observed") == 0
        and inventory.get("disposition") == "project_lifetime_unverified_foreground_consent_required"
        and inventory.get("native_provider_liveness_verified") is False
        and inventory.get("foreground_cleanup_consent_obtained") is False
        and inventory.get("verified_native_project_cleanup") is False
        and inventory.get("automatic_destructive_cleanup_allowed") is False,
        "mlt_cleanup_authority_unproven",
    )


def verify_private_source(root: Path, mode: str, source_sha: str,
                          semwright_sha: str) -> dict[str, bytes]:
    require(root.is_dir() and not root.is_symlink(),
            "missing_or_linked_private_evidence_root")
    result = strict_object(read_bounded(root, "result.json", 128_000))
    video_only = mode != "pcm"
    require(
        result.get("native_mlt_multisegment_e2e") == "PASS"
        and result.get("motionwright_sha") == source_sha
        and result.get("semwright_sha") == semwright_sha
        and result.get("mode") == mode
        and type(result.get("assembled_video_frames")) is int
        and result["assembled_video_frames"] == 33
        and result.get("rendered_source_frames") == [32, 1]
        and result.get("assembled_video_codec") == "ffv1"
        and result.get("assembled_video_has_audio") is (not video_only)
        and result.get("transport_audio_codec") == (None if video_only else "pcm_s16le")
        and result.get("native_render_profile") == (
            "lossless-video-only" if video_only else "lossless"
        )
        and result.get("provider_project_cleanup") == NO_CLEANUP
        and result.get("native_mlt_cleanup_verified") is False
        and result.get("evidence_scope") == SCOPES[mode],
        "native_mlt_source_not_verified",
    )
    timeline = strict_object(read_bounded(
        root, "native-multisegment-timeline-evidence.json", 256_000
    ))
    require(
        timeline.get("evidence_scope") == SCOPES[mode]
        and timeline.get("provider_project_cleanup") == NO_CLEANUP
        and type(timeline.get("frame_count")) is int
        and timeline["frame_count"] == 33
        and timeline.get("transport_pcm_audio") is (not video_only)
        and timeline.get("native_render_profile") == (
            "lossless-video-only" if video_only else "lossless"
        )
        and (not video_only or timeline.get("native_frame_count_observed") == 33),
        "native_mlt_timeline_evidence_invalid",
    )
    require_inventory(root, result)

    name = ("native-two-segment-video-only.mkv"
            if video_only else "native-two-segment-lossless.mkv")
    media = read_bounded(root, name, LIMIT)
    require(media.startswith(b"\x1a\x45\xdf\xa3"), "invalid_native_mlt_container")
    require(file_sha(media) == result.get("video_sha256")
            and len(media) == result.get("video_bytes"),
            "native_mlt_media_digest_mismatch")
    admitted = {name: media}
    if mode in ("final-mp4", "captioned-mp4"):
        master = read_bounded(root, "native-multisegment-master.mp4", LIMIT)
        require(len(master) >= 12 and master[4:8] == b"ftyp",
                "invalid_native_mlt_master")
        require(file_sha(master) == result.get("final_master_sha256")
                and len(master) == result.get("final_master_bytes")
                and type(result.get("final_master_frames")) is int
                and result["final_master_frames"] == 33
                and result.get("final_master_video_codec") == "h264"
                and result.get("final_master_audio_codec") == "aac"
                and result.get("final_master_audio_rate") == 48000
                and result.get("final_master_audio_channels") == 2
                and result.get("decoded_stereo_content")
                == "distinct_source_400hz_left_600hz_right"
                and type(result.get("measured_voice_pcm_frames")) is int
                and result["measured_voice_pcm_frames"] == 52800
                and result.get("final_evidence_scope")
                    == (CAPTIONED_SCOPE if mode == "captioned-mp4" else FINAL_SCOPE),
                "native_mlt_master_evidence_invalid")
        stereo = strict_object(read_bounded(
            root, "decoded-stereo-content-proof.json", 128_000
        ))
        require(
            stereo.get("decoded_stereo_content") == result.get("decoded_stereo_content")
            and type(stereo.get("decoded_master_pcm_frames")) is int
            and stereo["decoded_master_pcm_frames"]
            == result.get("decoded_master_pcm_frames")
            and type(result.get("decoded_master_pcm_frames")) is int
            and 52_800 <= result["decoded_master_pcm_frames"] <= 53_824,
            "native_mlt_stereo_evidence_invalid",
        )
        if mode == "captioned-mp4":
            # Original native PNG and FFV1/H264 bytes were inspected on the
            # disposable runner. Admit only closed numerical evidence from
            # the independent decoder, never transcript/media/session data.
            bounded = strict_object(read_bounded(
                root, "captioned-master-pixel-evidence.json", 32_768
            ))
            masks = bounded.get("active_caption_frames")
            counts = result.get("native_caption_mask_pixels")
            require(
                bounded.get("schema") == "motionwright-v05-native-captioned-master-pixels/1"
                and bounded.get("captioned_master_decoded_pixels") == "PASS"
                and bounded.get("motionwright_source_sha") == source_sha
                and bounded.get("semwright_source_sha") == semwright_sha
                and bounded.get("original_native_ffv1_frame_indices") == [0, 14, 15, 31, 32]
                and bounded.get("decoded_h264_frame_indices") == [0, 15, 32]
                and bounded.get("native_cue_count") == 2
                and bounded.get("original_ffv1_sha256") == result["video_sha256"]
                and bounded.get("actual_master_mp4_sha256") == result["final_master_sha256"]
                and bounded.get("human_readability") == "NOT_RUN"
                and bounded.get("commercial_release_authorized") is False
                and bounded.get("original_owner_media_or_transcript_published") is False
                and isinstance(masks, dict) and set(masks) == {"15", "32"}
                and isinstance(counts, dict) and set(counts) == {"15", "32"}
                and all(
                    isinstance(masks[key], dict)
                    and type(masks[key].get("native_caption_mask_pixels")) is int
                    and 350 <= masks[key]["native_caption_mask_pixels"] <= 100_000
                    and masks[key]["native_caption_mask_pixels"] == counts[key]
                    for key in ("15", "32")
                )
                and result.get("captioned_master_decoded_pixels") == "PASS"
                and result.get("captioned_source_cue_count") == 2
                and result.get("native_captioned_human_legibility") == "NOT_RUN",
                "native_captioned_master_decoded_pixels_not_verified",
            )
        admitted["native-multisegment-master.mp4"] = master
    return admitted


def publish_mlt(root: Path, output: Path, mode: str, source_sha: str,
                semwright_sha: str, job_status: str) -> dict:
    require(mode in MODES, "invalid_mlt_mode")
    require(job_status in VALID_STATUS, "invalid_ci_job_status")
    require(bool(SHA40.fullmatch(source_sha))
            and bool(SHA40.fullmatch(semwright_sha)), "invalid_source_pin")
    require(not output.exists(), "public_artifact_already_exists")
    src = root.resolve()
    dst = output.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "public_root_overlaps_private_source")
    files = (verify_private_source(root, mode, source_sha, semwright_sha)
             if job_status == "success" else {})
    entries = [
        {"name": name, "bytes": len(data), "sha256": file_sha(data)}
        for name, data in sorted(files.items())
    ]
    receipt = {
        "schema": SCHEMA,
        "motionwright_source_sha": source_sha,
        "semwright_source_pin": semwright_sha,
        "mode": mode,
        "ci_native_mlt_evidence": "PASS" if job_status == "success" else "NOT_VERIFIED",
        "native_video_frames": 33 if job_status == "success" else None,
        "native_captioned_h264_decoded_pixels":
            "PASS" if job_status == "success" and mode == "captioned-mp4" else "NOT_VERIFIED",
        "human_caption_readability": "NOT_RUN",
        "final_master_human_approved": False,
        "verified_native_project_cleanup": False,
        "foreground_cleanup_consent_obtained": False,
        "raw_provider_and_daemon_logs_uploaded": False,
        "original_native_project_receipts_uploaded": False,
        "native_mutations_replayed_by_publisher": False,
        "installed_owner_acceptance": "NOT_RUN",
        "v01_release_authorized": False,
        "included_files": entries,
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        with (output / name).open("xb") as stream:
            stream.write(data)
    with (output / "public-evidence.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args(argv)
    try:
        lock = strict_object((Path(__file__).resolve().parents[1] /
                              "SOURCE_LOCK.json").read_bytes())
        pin = lock["dependencies"]["semwright"]["revision"]
        receipt = publish_mlt(args.evidence_root, args.output_root,
                              args.mode, args.source_sha, pin, args.job_status)
    except (PublicationError, OSError, KeyError, TypeError) as exc:
        code = exc.code if isinstance(exc, PublicationError) else "public_artifact_refused"
        print("native-mlt-public-artifact=refused reason=" + code, file=sys.stderr)
        return 2
    print("native-mlt-public-artifact=" + receipt["ci_native_mlt_evidence"]
          + " files=" + str(len(receipt["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
