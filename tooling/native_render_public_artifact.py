#!/usr/bin/env python3
"""Publish only source-verified v0.1 Motion Canvas CI frames and hashes.

The native job, provider lifetime, Broker session and original source receipts
remain on the ephemeral runner, never in the public artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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


# A negative CI artifact can report a closed technical observation only.
# Raw provider/job/session receipts, original error messages and paths are
# NEVER imported into the public bundle. This is not a provider root cause.
FAILURE_REASONS = frozenset({
    "command_deadline", "native_command_failed", "empty_native_response",
    "oversized_native_failure_response", "typed_error_missing",
    "unexpected_native_failure_shape", "malformed_native_failure_response",
    "native_journal_missing_or_unbounded", "provider_generation_inactive",
    "native_status_unconfirmed", "acknowledged_render_terminal_unverified",
    "cli_exit_without_original_terminal",
})
FAILURE_OUTCOMES = frozenset({
    "UNKNOWN", "NOT_VERIFIED", "FAILED_NOT_MEDIA_VERIFIED",
})
CLI_EXIT_CATEGORIES = frozenset({
    "deadline", "signal", "not_observed", "nonzero_exit",
    "zero_exit_invalid_protocol",
})
FAILURE_SCHEMA = "motionwright.v01-native-command-failure/1"


def closed_failure_observation(root: Path, source: str, sdk: str) -> dict:
    """Only fixed, source-bound enums from a privately retained CI failure."""
    result: dict = {
        "observation": "NOT_RECORDED",
        "safe_reason": "NOT_AVAILABLE",
        "source_outcome": "NOT_VERIFIED",
        "acknowledged_start_observed": False,
        "provider_generation_inactive_observed": False,
        "cli_exit_category": "NOT_AVAILABLE",
        "terminal_native_result_verified": False,
        "provider_root_cause_verified": False,
        "original_native_job_retried": False,
    }
    name = "native-render-failure-diagnostic.json"
    path = root / name
    if not path.exists() and not path.is_symlink():
        return result
    # A symlinked, oversized, malformed, foreign-source or private-looking
    # diagnostic is not an excuse to export raw content or claim success.
    result["observation"] = "UNTRUSTED_PRIVATE_DIAGNOSTIC"
    try:
        data = strict_object(read_bounded(root, name, 32_768))
    except (PublicationError, OSError, ValueError, TypeError):
        return result
    if not isinstance(data, dict) or not (
        data.get("schema") == FAILURE_SCHEMA
        and data.get("result") == "FAIL"
        and data.get("motionwright_source_sha") == source
        and data.get("semwright_source_pin") == sdk
        and isinstance(data.get("reason"), str)
        and data["reason"] in FAILURE_REASONS
        and isinstance(data.get("source_outcome"), str)
        and data["source_outcome"] in FAILURE_OUTCOMES
        and data.get("terminal_native_result_verified") is False
        and data.get("decoded_media_verified") is False
        and data.get("automatically_restarted_mutating_render") is False
        and type(data.get("original_render_start_receipts_observed")) is int
        and 0 <= data["original_render_start_receipts_observed"] <= 4096
        and type(data.get("provider_generation_inactive_observed")) is bool
    ):
        return result
    count = data["original_render_start_receipts_observed"]
    # An enclosing CLI error (even a known one) does not prove the terminal
    # result of an earlier ACKNOWLEDGED mutating render.
    outcome = "UNKNOWN" if count else data["source_outcome"]
    category = data.get("cli_exit_category")
    return {
        **result,
        "observation": "SOURCE_BOUND_CI_FAILURE_CATEGORY",
        "safe_reason": data["reason"],
        "source_outcome": outcome,
        "acknowledged_start_observed": count > 0,
        "provider_generation_inactive_observed":
            data["provider_generation_inactive_observed"],
        "cli_exit_category":
            category if isinstance(category, str) and category in CLI_EXIT_CATEGORIES
            else "NOT_AVAILABLE",
    }


def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()



def verify_selected_original_source(result: dict, parity: dict) -> dict[str, bool]:
    """Admit source-only Clip/Layer pixels and original Store reopen claims.

    A public artifact never includes the original Studio receipt, Store or
    owner payload; these exact fields are producer-bound through result/parity.
    """
    clip_mode = os.environ.get("MOTIONWRIGHT_NATIVE_CLIP_E2E", "0")
    layer_mode = os.environ.get("MOTIONWRIGHT_NATIVE_LAYER_ORDER_E2E", "0")
    require(clip_mode in {"0", "1"} and layer_mode in {"0", "1"}
            and not (clip_mode == "1" and layer_mode == "1"),
            "native_selected_source_invalid")
    clip_selected = clip_mode == "1"
    layer_selected = layer_mode == "1"
    clip = result.get("native_clip_group_original")
    layer = result.get("native_layer_order_original")
    project = result.get("project")
    compared = parity.get("compared_properties")
    require(isinstance(project, dict), "native_selected_project_missing")
    digest = lambda v: isinstance(v, str) and SHA256.fullmatch(v) is not None

    if clip_selected:
        frames = clip.get("frames") if isinstance(clip, dict) else None
        require(
            isinstance(clip, dict)
            and clip.get("schema") == "motionwright.native-clip-group-original-pixels/1"
            and clip.get("status") == "ORIGINAL_NATIVE_RECTANGULAR_CLIP_PIXELS_PASS"
            and clip.get("parent_right_px") == 1280
            and clip.get("original_child_right_px") == 1460
            and clip.get("source_fill") == "#E0A940"
            and clip.get("source_replaced_with_overlay") is False
            and clip.get("native_effect_invoked_by_validator") is False
            and clip.get("human_visual_acceptance") is False
            and digest(project.get("clip_ui_sha256"))
            and digest(project.get("clip_source_sha256"))
            and clip.get("source_receipt_sha256") == project["clip_source_sha256"]
            and digest(clip.get("independent_store_readback_sha256"))
            and isinstance(frames, list) and len(frames) == 3
            and all(
                isinstance(frame, dict) and frame.get("frame") == index
                and digest(frame.get("source_roi_sha256"))
                and frame.get("inside_rgb") == [224, 169, 64]
                and isinstance(frame.get("outside_rgb"), list)
                and len(frame["outside_rgb"]) == 3
                and all(type(channel) is int and 0 <= channel <= 255
                        for channel in frame["outside_rgb"])
                and sum(abs(channel - expected) for channel, expected
                        in zip(frame["outside_rgb"], (224, 169, 64))) >= 45
                for index, frame in zip((0, 30, 59), frames)
            )
            and isinstance(compared, list)
            and "source_bound_rectangular_clip_group" in compared,
            "native_clip_original_not_verified",
        )
    else:
        require(clip is None, "native_clip_selection_mismatch")

    if layer_selected:
        require(
            isinstance(layer, dict)
            and layer.get("schema") == "motionwright.native-layer-original-pixels/1"
            and layer.get("status") == "SOURCE_BOUND_PROMOTED_LAYER_VISIBLE"
            and layer.get("sampled_frames") == [0, 30, 59]
            and layer.get("exact_static_region") is True
            and layer.get("overlap_front_rgb") == [224, 169, 64]
            and layer.get("background_visible_rgb") == [37, 105, 184]
            and layer.get("native_effect_invoked_by_validator") is False
            and layer.get("human_visual_acceptance") is False
            and digest(project.get("layer_order_ui_sha256"))
            and digest(project.get("layer_order_source_sha256"))
            and layer.get("source_receipt_sha256") == project["layer_order_source_sha256"]
            and digest(layer.get("independent_store_reader_sha256"))
            and isinstance(compared, list)
            and "source_bound_native_layer_order" in compared,
            "native_layer_original_not_verified",
        )
    else:
        require(layer is None, "native_layer_selection_mismatch")
    return {"clip": clip_selected, "layer_order": layer_selected}


def checked_source(root: Path, source: str, sdk: str) -> tuple[dict[str, bytes], str, dict[str, bool]]:
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
    source_selected = verify_selected_original_source(result, parity)
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
    return blobs, pixels["ordered_rgba_frame_digests_sha256"], source_selected


def publish(root: Path, out: Path, source: str, sdk: str, ci_status: str) -> dict:
    require(bool(SHA40.fullmatch(source)) and bool(SHA40.fullmatch(sdk)),
            "source_identity_invalid")
    require(ci_status in VALID_STATUSES, "ci_status_invalid")
    require(not out.exists(), "public_artifact_already_exists")
    require(root.is_dir() and not root.is_symlink(),
            "missing_or_linked_private_evidence_root")
    src = root.resolve()
    dest = out.resolve()
    require(src != dest and src not in dest.parents and dest not in src.parents,
            "public_root_overlaps_private_source")
    blobs, rgba_sha, selected = (checked_source(root, source, sdk)
                                 if ci_status == "success"
                                 else ({}, None, {"clip": False, "layer_order": False}))
    failure_summary = (closed_failure_observation(root, source, sdk)
                       if ci_status != "success" else None)
    receipt = {
        "schema": SCHEMA,
        "motionwright_source_sha": source,
        "semwright_source_pin": sdk,
        "ci_native_motion_canvas_evidence": "PASS" if ci_status == "success" else "NOT_VERIFIED",
        "ci_failure_observation": failure_summary,
        "native_frames_per_leg": 60 if ci_status == "success" else None,
        "native_rgba_direct_mediated_parity": (True if ci_status == "success" else None),
        "native_ordered_rgba_digest_sha256": rgba_sha,
        "selected_original_clip_pixels_and_store_verified": selected["clip"],
        "selected_original_layer_pixels_and_store_verified": selected["layer_order"],
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
