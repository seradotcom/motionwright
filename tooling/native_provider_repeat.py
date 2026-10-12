#!/usr/bin/env python3
"""v0.1 CI owner-render recurrence: independent repeated FULL native E2E receipts.

Read-only evidence reducer. This code does not call the renderer, retry native
mutations, infer verified installed-owner acceptance or claim provider repair.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

SHA = re.compile(r"[a-f0-9]{40}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
MAX_BYTES = 2_000_000


class RepeatEvidenceError(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RepeatEvidenceError(reason)


def _unique_keys(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        require(key not in result, "duplicate_json_field")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    require(not path.is_symlink() and path.is_file(), "missing_or_unsafe_receipt")
    require(path.stat().st_size <= MAX_BYTES, "oversized_receipt")
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    except (UnicodeError, OSError, ValueError, TypeError) as error:
        if isinstance(error, RepeatEvidenceError):
            raise
        raise RepeatEvidenceError("invalid_receipt_json") from error
    require(isinstance(data, dict), "receipt_not_object")
    return data


def sha256(path: Path) -> str:
    require(path.is_file() and not path.is_symlink(), "unsafe_evidence_file")
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(part)
    return result.hexdigest()


REQUIRED_RECEIPTS = (
    "result.json",
    "motionwright-direct-parity-film.json",
    "artifact-manifest.json",
    "native-provider-lifecycle.json",
    "native-direct-mediated-parity.json",
    "native-render-stage-cost.json",
    "frame-first.png",
    "frame-middle.png",
    "frame-last.png",
)


def verify_single_run(folder: Path, source: str, semwright: str) -> dict:
    require(folder.is_dir() and not folder.is_symlink(), "missing_or_unsafe_run")
    for name in REQUIRED_RECEIPTS:
        require((folder / name).is_file() and not (folder / name).is_symlink(),
                "incomplete_native_evidence")
    result = read_json(folder / "result.json")
    parity = read_json(folder / "native-direct-mediated-parity.json")
    stages = read_json(folder / "native-render-stage-cost.json")
    lifecycle = read_json(folder / "native-provider-lifecycle.json")
    require(
        result.get("native_render_e2e") == "PASS"
        and result.get("motionwright_sha") == source
        and result.get("semwright_sha") == semwright
        and result.get("frame_count") == 60
        and result.get("direct_mediated_native_frame_parity") == "PASS"
        and result.get("linear_position_motion_sampled") is True
        and result.get("linear_opacity_motion_sampled") is True,
        "native_run_not_source_verified",
    )
    film_sha = result.get("direct_mediated_same_film_sha256")
    require(isinstance(film_sha, str) and bool(DIGEST.fullmatch(film_sha)),
            "source_film_digest_invalid")
    require(sha256(folder / "motionwright-direct-parity-film.json") == film_sha,
            "source_film_bytes_mismatch")
    require(
        parity.get("schema") == "motionwright-native-direct-mediated-parity/1"
        and parity.get("motionwright_sha") == source
        and parity.get("semwright_sha") == semwright
        and parity.get("source_film_sha256") == film_sha
        and parity.get("direct_job_distinct_from_mediated") is True
        and parity.get("human_visual_acceptance") == "NOT_RUN"
        and parity.get("arbitrary_renderer_property_parity") == "NOT_PROVEN",
        "native_parity_source_identity_inconsistent",
    )
    pixels = parity.get("pixels")
    require(isinstance(pixels, dict)
            and pixels.get("all_frames_decoded_rgba_pixel_identical") is True
            and pixels.get("frame_count") == 60
            and pixels.get("raw_rgba_pixel_format") == "rgba_1920x1080"
            and pixels.get("checked_frames") == [0, 15, 30, 59],
            "native_full_frame_parity_not_proven")
    pixel_sha = pixels.get("ordered_rgba_frame_digests_sha256")
    require(isinstance(pixel_sha, str) and bool(DIGEST.fullmatch(pixel_sha)),
            "decoded_rgba_digest_invalid")
    require(
        stages.get("schema") == "motionwright.native-render-stage-cost/1"
        and stages.get("source_sha") == source
        and stages.get("semwright_sha") == semwright
        and stages.get("canonical_film_sha256") == film_sha
        and stages.get("verified_frames_per_leg") == 60
        and stages.get("verified_rgba_frames_across_both_legs") == 120
        and stages.get("ordered_rgba_frame_digests_sha256") == pixel_sha
        and isinstance(stages.get("comparison"), dict)
        and stages["comparison"].get("saved_native_renderer_frames") is None
        and stages["comparison"].get("cache_savings") is None,
        "stage_timing_claims_not_source_bound",
    )
    require(
        lifecycle.get("schema") == "motionwright-ci-provider-lifecycle/1"
        and lifecycle.get("authority") == "passive-ci-process-status-not-driver-evidence"
        and isinstance(lifecycle.get("sample_count"), int)
        and lifecycle["sample_count"] >= 1
        and lifecycle.get("daemon_exit_before_owner_cleanup") is None
        and lifecycle.get("cgroup_oom_kills_delta") in (None, 0),
        "native_provider_lifecycle_unconfirmed",
    )
    project = result.get("project")
    require(isinstance(project, dict)
            and isinstance(project.get("project_id"), str)
            and isinstance(project.get("generation"), str)
            and isinstance(project.get("revision"), int),
            "project_identity_invalid")
    for candidate in ("driver_sha256", "node_sha256", "artifact_manifest_sha256"):
        require(isinstance(result.get(candidate), str)
                and bool(DIGEST.fullmatch(result[candidate])),
                "native_digest_invalid")
    require(sha256(folder / "artifact-manifest.json")
            == result["artifact_manifest_sha256"], "native_manifest_bytes_mismatch")
    review = result.get("review_frames")
    require(isinstance(review, dict), "native_review_frames_missing")
    for filename, key in (
        ("frame-first.png", "first_sha256"),
        ("frame-middle.png", "middle_sha256"),
        ("frame-last.png", "last_sha256"),
    ):
        require(review.get(key) == sha256(folder / filename),
                "native_review_frame_bytes_mismatch")
    return {
        "source_film_sha256": film_sha,
        "decoded_rgba_sha256": pixel_sha,
        "project_id": project["project_id"],
        "generation": project["generation"],
        "revision": project["revision"],
        "driver_sha256": result["driver_sha256"],
        "node_sha256": result["node_sha256"],
        "manifest_sha256": result["artifact_manifest_sha256"],
        "source_receipt_sha256": sha256(folder / "result.json"),
        "lifecycle_receipt_sha256": sha256(folder / "native-provider-lifecycle.json"),
        "native_frames_per_renderer": 60,
        "native_full_render_pair_verified": True,
    }


def compare_runs(first: Path, second: Path, source: str, semwright: str) -> dict:
    require(bool(SHA.fullmatch(source)) and bool(SHA.fullmatch(semwright)),
            "invalid_source_lock")
    # Two independently provisioned owner sessions and providers, not a
    # hidden render retry in one Broker session.
    require(first.resolve() != second.resolve(), "repeat_source_reused")
    one = verify_single_run(first, source, semwright)
    two = verify_single_run(second, source, semwright)
    require(one["project_id"] != two["project_id"]
            and one["generation"] != two["generation"],
            "owner_sessions_not_independent")
    require(one["driver_sha256"] == two["driver_sha256"]
            and one["node_sha256"] == two["node_sha256"],
            "provider_source_changed_between_runs")
    same_film = one["source_film_sha256"] == two["source_film_sha256"]
    same_pixels = one["decoded_rgba_sha256"] == two["decoded_rgba_sha256"]
    if same_film:
        require(same_pixels, "identical_film_different_decoded_rgba")
    return {
        "schema": "motionwright.v01-independent-provider-repeat/1",
        "source_sha": source,
        "semwright_pin": semwright,
        "status": "TWO_INDEPENDENT_NATIVE_E2E_RUNS_VERIFIED",
        "first": one,
        "second": two,
        "fresh_owner_project_and_generation_each_run": True,
        "same_pinned_native_provider_bytes": True,
        "same_exact_film_source_digest": same_film,
        "decoded_rgba_equal": same_pixels,
        "cross_instance_rgba_claim": (
            "SAME_FILM_PIXEL_IDENTICAL" if same_film and same_pixels
            else "DIFFERENT_SOURCE_FILM_INSTANCES_NOT_COMPARABLE"
        ),
        "native_frames_in_each_full_renderer_leg": 60,
        "automatic_retry_after_unknown_observed": "NOT_EXERCISED",
        "same_job_reconciliation_from_missing_provider": "NOT_PROVEN",
        "intermittent_driver_host_loss_fixed": "NOT_PROVEN",
        "real_owner_installed_acceptance": "NOT_RUN",
        "independent_product_acceptance": "NOT_RUN",
        "motionwright_v01_release_ready": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--semwright-pin", required=True)
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare_runs(args.first, args.second, args.source_sha, args.semwright_pin)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (RepeatEvidenceError, OSError) as error:
        # Constant diagnostic codes, not private absolute path/CLI stderr.
        code = str(error) if isinstance(error, RepeatEvidenceError) else "output_unavailable"
        print("native-v01-repeat-evidence=failed code=" + code)
        return 2
    print("native-v01-repeat-evidence=" + result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
