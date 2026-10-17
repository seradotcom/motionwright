#!/usr/bin/env python3
"""Publish source-pinned fframes timing facts only, never arbitrary upstream logs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SCHEMA = "motionwright.v05-fframes-public-benchmark/1"
PIN = "e2b552892c0373a5c8dc79de9280a3289670f27b"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
PHASES = ("cli_startup_0", "cli_startup_1",
          "cold_release_build", "warm_release_build",
          "cold_headless_native_preview", "warm_headless_native_preview",
          "cold_bounded_video_render")


class Refused(ValueError):
    pass


def require(ok: bool, why: str) -> None:
    if not ok:
        raise Refused(why)


def strict_object(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_upstream_evidence_key")
            result[key] = value
        return result
    try:
        obj = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError, TypeError) as error:
        if isinstance(error, Refused):
            raise
        raise Refused("invalid_upstream_evidence") from error
    require(type(obj) is dict, "upstream_evidence_must_be_object")
    return obj


def publish(private: Path, public: Path, sha: str, status: str) -> dict:
    require(bool(HEX40.fullmatch(sha)), "invalid_motionwright_sha")
    require(status in {"success", "failure", "cancelled", "skipped"},
            "invalid_ci_status")
    require(not public.exists() and not public.is_symlink(),
            "existing_public_destination")
    src, dst = private.resolve(), public.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "overlapping_private_public_roots")
    times: dict[str, float] = {}
    preview = {}
    movie = {}
    if status == "success":
        path = private / "result.json"
        require(private.is_dir() and not private.is_symlink()
                and path.is_file() and not path.is_symlink()
                and 0 < path.stat().st_size <= 128_000,
                "missing_or_unbounded_source_result")
        result = strict_object(path.read_bytes())
        require(
            result.get("schema") == "motionwright.fframes-experimental-runtime/1"
            and result.get("status") == "EXPERIMENTAL_UPSTREAM_SOURCE_RENDER_PASS"
            and result.get("motionwright_sha") == sha
            and result.get("fframes_exact_sha") == PIN
            and result.get("fframes_license") == "MIT"
            and result.get("fframes_repository") ==
                "https://github.com/dmtrKovalenko/fframes.git"
            and result.get("runtime") == "disposable_ubuntu_24_04"
            and result.get("source_code_admitted_to_motionwright") is False
            and result.get("semwright_driver_host_executed") is False
            and result.get("owner_install_permission") is False
            and result.get("backend_release_admitted") is False
            and result.get("artistic_quality_reviewed") is False
            and result.get("hyperframes_same_fixture_comparison") == "NOT_RUN"
            and result.get("interactive_gpu_preview") == "NOT_RUN_HEADLESS_CI"
            and result.get("audio_score_composition") ==
                "NOT_RUN_NO_AUDIO_IN_HELLO_FIXTURE"
            and all(result.get(flag) is True for flag in (
                "cold_build_measured", "warm_build_measured",
                "frame_preview_measured", "full_video_render_measured"))
            and type(result.get("measurements")) is dict,
            "unverified_or_admitted_fframes_experiment",
        )
        for phase in PHASES:
            data = result["measurements"].get(phase, {})
            require(type(data) is dict and data.get("exit_code") == 0
                    and type(data.get("elapsed_seconds")) in (int, float)
                    and 0 <= data["elapsed_seconds"] <= 1500,
                    "invalid_source_timing")
            times[phase] = data["elapsed_seconds"]
        p = result.get("preview_png", {})
        m = result.get("video", {})
        require(type(p) is dict and type(m) is dict
                and p.get("decoded_by_ffprobe") is True
                and all(type(p.get(k)) is int and 1 <= p[k] <= 4096
                        for k in ("width", "height"))
                and bool(HEX64.fullmatch(str(p.get("sha256", ""))))
                and bool(HEX64.fullmatch(str(m.get("sha256", ""))))
                and type(m.get("bytes")) is int and 1024 <= m["bytes"] < 80_000_000
                and type(m.get("width")) is int and m["width"] > 0
                and type(m.get("height")) is int and m["height"] > 0
                and 0.5 <= float(m.get("duration_seconds", 0)) <= 2.0
                and m.get("codec") == "h264",
                "unverified_fframes_decoded_media")
        preview = {"sha256": p["sha256"], "width": p["width"],
                   "height": p["height"], "independent_decode": "PASS"}
        movie = {"sha256": m["sha256"], "bytes": m["bytes"],
                 "codec": str(m.get("codec", "unknown"))[:32]}
    receipt = {
        "schema": SCHEMA, "motionwright_sha": sha, "fframes_sha": PIN,
        "ci_evidence": "PASS" if status == "success" else "NOT_VERIFIED",
        "actual_measured_seconds": times, "source_preview": preview,
        "source_one_second_video": movie,
        "private_stdout_stderr_uploaded": False,
        "upstream_binary_uploaded": False, "source_code_admitted": False,
        "renderer_adoption_authorized": False, "same_brief_comparison": "NOT_RUN",
        "audio_fidelity_assessed": "NOT_RUN", "interactive_gpu_preview": "NOT_RUN",
        "human_creative_acceptance": "NOT_RUN", "v05_release_ready": False,
    }
    public.mkdir(parents=True, exist_ok=False)
    with (public / "public-evidence.json").open("x", encoding="utf-8") as out:
        json.dump(receipt, out, sort_keys=True, indent=2)
        out.write("\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args()
    try:
        value = publish(args.evidence_root, args.public_root,
                        args.source_sha, args.job_status)
    except (Refused, OSError, TypeError, ValueError) as error:
        print("fframes-public=refused " +
              (str(error) if isinstance(error, Refused) else "unsafe_evidence"),
              file=sys.stderr)
        return 1
    print("fframes-public=" + value["ci_evidence"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
