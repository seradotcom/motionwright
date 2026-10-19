#!/usr/bin/env python3
"""Independent decoded-frame check for one source-bound Manim camera master.

This reader never starts a native effect, imports executable project code or
grants provider authority. The caller MUST already have a trusted camera plan
SHA and the original verified artifact SHA from a separate receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

WIDTH = 640
HEIGHT = 360
FPS = 30
FRAMES = 120
READBACK_SAMPLES = (0, 60, 119)
FRAME_BYTES = WIDTH * HEIGHT * 3
MAX_MP4_BYTES = 512 * 1024 * 1024


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def checked_input(path: Path, budget: int) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise AssertionError("source must be an absolute ordinary file")
    if not 0 < path.stat().st_size <= budget:
        raise AssertionError("source file outside the bounded byte budget")
    return path


def verified_plan(path: Path, expected_sha: str) -> dict:
    checked_input(path, 4 * 1024 * 1024)
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
        raise AssertionError("trusted plan SHA-256 must be lowercase hexadecimal")
    if digest(path) != expected_sha:
        raise AssertionError("compiled source plan SHA-256 mismatch")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (
        plan.get("schema_version") != 4
        or plan.get("clock", {}).get("frame_count") != FRAMES
        or plan.get("clock", {}).get("frame_rate") != {"num": str(FPS), "den": "1"}
        or plan.get("render_profile") != {
            "width": WIDTH, "height": HEIGHT, "frame_rate": FPS
        }
        or plan.get("reframe", {}).get("component_version") != 2
        or plan.get("reframe", {}).get("duration_frames") != FRAMES
    ):
        raise AssertionError("source plan is not the admitted 120-frame camera contract")
    source = plan["reframe"]
    if (
        source.get("start_center_x") != 960.0
        or source.get("start_center_y") != 540.0
        or source.get("end_center_x") != 1000.0
        or source.get("end_center_y") != 544.0
        or plan.get("camera", {}).get("center_x") != 1000.0
        or plan.get("camera", {}).get("center_y") != 544.0
    ):
        raise AssertionError("camera endpoints do not match the authored physical test")
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise AssertionError("original authored camera objects are missing")
    if len({node.get("node_id") for node in nodes}) != len(nodes):
        raise AssertionError("camera source identities are not distinct")
    # 120 frames with two objects breach the original 256 KiB camera getter
    # envelope. The single authored rectangle still proves the visible move.
    if len(nodes) != 1:
        raise AssertionError("120-frame source must have one original editable object")
    if nodes[0].get("source_kind") != "rectangle":
        raise AssertionError("source object is not the admitted rectangle")
    for node in nodes:
        if (
            not all(isinstance(node.get(k), (float, int)) for k in
                    ("x_px", "y_px", "width_px", "height_px"))
            or not all(math.isfinite(node[k]) for k in
                       ("x_px", "y_px", "width_px", "height_px"))
            or re.fullmatch(r"#[0-9A-Fa-f]{6}", node.get("fill_hex", "")) is None
        ):
            raise AssertionError("camera source geometry or color is unsupported")
    return plan


def frame_center(plan: dict, node: dict, frame: int) -> tuple[float, float]:
    if frame < 0 or frame >= FRAMES:
        raise AssertionError("requested frame is outside the original source")
    camera = plan["reframe"]
    t = frame / camera["duration_frames"]
    center_x = camera["start_center_x"] + (
        camera["end_center_x"] - camera["start_center_x"]
    ) * t
    center_y = camera["start_center_y"] + (
        camera["end_center_y"] - camera["start_center_y"]
    ) * t
    return (
        (node["x_px"] + node["width_px"] / 2 + 960.0 - center_x) / 3,
        (node["y_px"] + node["height_px"] / 2 + 540.0 - center_y) / 3,
    )


def color_center(rgb: bytes, node: dict, estimate: tuple[float, float]) -> tuple[float, float, int]:
    if len(rgb) != FRAME_BYTES:
        raise AssertionError("decoded RGB frame has wrong length")
    target = tuple(bytes.fromhex(node["fill_hex"][1:]))
    x0 = max(0, math.floor(estimate[0] - node["width_px"] / 6 - 24))
    x1 = min(WIDTH, math.ceil(estimate[0] + node["width_px"] / 6 + 24))
    y0 = max(0, math.floor(estimate[1] - node["height_px"] / 6 - 24))
    y1 = min(HEIGHT, math.ceil(estimate[1] + node["height_px"] / 6 + 24))
    xs = ys = count = 0
    pixels = memoryview(rgb)
    for y in range(y0, y1):
        for x in range(x0, x1):
            offset = (y * WIDTH + x) * 3
            if all(abs(pixels[offset + i] - target[i]) <= 38 for i in range(3)):
                xs += x
                ys += y
                count += 1
    if count < 100:
        raise AssertionError("native frame is missing the authored color object")
    return xs / count, ys / count, count


def assert_centroids(plan: dict, frames: dict[int, bytes]) -> list[dict]:
    if sorted(frames) != list(READBACK_SAMPLES):
        raise AssertionError("independent frame samples are missing")
    observations = []
    for node in plan["nodes"]:
        positions = []
        for frame in READBACK_SAMPLES:
            expected = frame_center(plan, node, frame)
            actual_x, actual_y, pixels = color_center(frames[frame], node, expected)
            if abs(actual_x - expected[0]) > 3.0 or abs(actual_y - expected[1]) > 3.0:
                raise AssertionError("native object center diverges from saved camera trajectory")
            positions.append((actual_x, actual_y))
            observations.append({
                "node_id": node["node_id"],
                "frame": frame,
                "expected_center": [round(a, 5) for a in expected],
                "observed_center": [round(actual_x, 5), round(actual_y, 5)],
                "matched_color_pixels": pixels,
            })
        if not (positions[0][0] - positions[1][0] > 4
                and positions[1][0] - positions[2][0] > 4):
            raise AssertionError("decoded frames do not show the original moving-camera trajectory")
    return observations



def assert_two_axis_motion(plan: dict, rows: list[dict]) -> dict:
    """Verify independent X and Y movement from all 120 decoded centroids.

    A single final frame can be inside the existing 3-pixel positional bound
    even if the subtler Y camera axis is frozen. Linear regression over all
    original frames detects that false positive without moving the bound.
    """
    if len(plan["nodes"]) != 1 or len(rows) != FRAMES:
        raise AssertionError("all-frame camera movement requires one saved source")
    if [row.get("frame") for row in rows] != list(range(FRAMES)):
        raise AssertionError("decoded camera frames are missing, repeated or out of order")
    node_id = plan["nodes"][0]["node_id"]
    if any(row.get("node_id") != node_id for row in rows):
        raise AssertionError("camera source identity changed across decoded frames")
    mid = (FRAMES - 1) / 2
    denominator = sum((frame - mid) ** 2 for frame in range(FRAMES))
    source = plan["reframe"]
    expected = (
        -(source["end_center_x"] - source["start_center_x"])
        / (3 * source["duration_frames"]),
        -(source["end_center_y"] - source["start_center_y"])
        / (3 * source["duration_frames"]),
    )
    observed = []
    max_absolute_error = []
    for axis in (0, 1):
        points = []
        errors = []
        for frame, row in enumerate(rows):
            point = row["observed_center"][axis]
            expected_point = row["expected_center"][axis]
            if not all(isinstance(value, (int, float)) and math.isfinite(value)
                       for value in (point, expected_point)):
                raise AssertionError("native centroid includes invalid or nonfinite data")
            errors.append(abs(point - expected_point))
            points.append(point)
        if max(errors) > 3.0:
            raise AssertionError("native camera object violates original 3px position bound")
        mean = sum(points) / FRAMES
        slope = sum((frame - mid) * (point - mean)
                    for frame, point in enumerate(points)) / denominator
        # The original Y move is only 4 source pixels (~1.33 output pixels);
        # use an absolute slope tolerance that admits RGB quantization yet
        # rejects no movement, reversed motion, and wrong-axis trajectories.
        allowed = max(0.001, abs(expected[axis]) * 0.30)
        if (abs(expected[axis]) < 0.005 or
                slope * expected[axis] <= 0 or
                abs(slope - expected[axis]) > allowed):
            raise AssertionError(
                ("x", "y")[axis] + "_camera_motion_not_decoded_from_source"
            )
        observed.append(slope)
        max_absolute_error.append(max(errors))
    return {
        "frame_count": FRAMES,
        "observed_center_slope_px_per_frame": [round(x, 9) for x in observed],
        "expected_center_slope_px_per_frame": [round(x, 9) for x in expected],
        "max_original_centroid_error_px": [round(x, 6) for x in max_absolute_error],
        "both_axes_moving_camera_verified_from_decoded_centroids": True,
        "full_pixel_equality_claim": False,
    }


def assert_full_decoded_camera(plan: dict, rgb: bytes) -> dict:
    """Observe every frame of bounded decoded RGB24 without rerendering."""
    if len(rgb) != FRAMES * FRAME_BYTES:
        raise AssertionError("decoded original RGB source does not contain 120 full frames")
    memory = memoryview(rgb)
    node = plan["nodes"][0]
    rows = []
    for frame in range(FRAMES):
        start = frame * FRAME_BYTES
        expected = frame_center(plan, node, frame)
        x, y, count = color_center(memory[start:start + FRAME_BYTES], node, expected)
        rows.append({
            "frame": frame,
            "node_id": node["node_id"],
            "expected_center": [expected[0], expected[1]],
            "observed_center": [x, y],
            "matched_color_pixels": count,
        })
    return assert_two_axis_motion(plan, rows)


def probe_media(media: Path, ffprobe: str) -> dict:
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_streams", "-show_frames", "-show_format", "-of", "json", str(media),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    parsed = json.loads(proc.stdout)
    video = [x for x in parsed["streams"] if x["codec_type"] == "video"]
    if len(video) != 1:
        raise AssertionError("expected one physical video stream")
    stream = video[0]
    if (
        stream.get("codec_name") != "h264"
        or stream.get("width") != WIDTH
        or stream.get("height") != HEIGHT
        or stream.get("avg_frame_rate") != f"{FPS}/1"
    ):
        raise AssertionError("decoded video profile is not the agreed H264 camera profile")
    sampled = [x for x in parsed["frames"] if x.get("media_type") == "video"]
    if len(sampled) != FRAMES:
        raise AssertionError(f"original video decodes {len(sampled)} rather than {FRAMES} frames")
    for n, frame in enumerate(sampled):
        pts = frame.get("best_effort_timestamp_time")
        if pts is None or abs(float(pts) - n / FPS) > 0.0005:
            raise AssertionError(f"frame {n} presentation timestamp deviates from exact 30fps")
    if abs(float(parsed["format"]["duration"]) - 4.0) > 0.02:
        raise AssertionError("original master duration is not four seconds")
    # The existing ffprobe request selects only video. Query the full source
    # independently to reject an unexpected voice/audio contribution.
    streams = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "json", str(media)],
        capture_output=True, timeout=30, check=True,
    )
    if any(row.get("codec_type") != "video"
           for row in json.loads(streams.stdout)["streams"]):
        raise AssertionError("Manim scene-video contribution unexpectedly carries audio")
    return {
        "codec": stream["codec_name"],
        "size": [stream["width"], stream["height"]],
        "frame_count": len(sampled),
        "frame_rate": stream["avg_frame_rate"],
        "duration_seconds": float(parsed["format"]["duration"]),
        "first_frame_pts": float(sampled[0]["best_effort_timestamp_time"]),
        "last_frame_pts": float(sampled[-1]["best_effort_timestamp_time"]),
    }


def decoded_rgb120(media: Path, ffmpeg: str) -> bytes:
    proc = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-i", str(media), "-an",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-fps_mode", "passthrough", "pipe:1"],
        capture_output=True, timeout=120, check=True,
    )
    if len(proc.stdout) != FRAMES * FRAME_BYTES:
        raise AssertionError("raw decoded frame byte count deviates from original camera clock")
    return proc.stdout


def verify(plan_file: Path, plan_sha: str, media: Path, media_sha: str,
           ffprobe: str, ffmpeg: str) -> dict:
    plan = verified_plan(plan_file, plan_sha)
    checked_input(media, MAX_MP4_BYTES)
    if re.fullmatch(r"[0-9a-f]{64}", media_sha) is None or digest(media) != media_sha:
        raise AssertionError("native video digest differs from the original trusted receipt")
    observation = probe_media(media, ffprobe)
    rgb = decoded_rgb120(media, ffmpeg)
    view = memoryview(rgb)
    sample_frames = {
        frame: view[frame * FRAME_BYTES:(frame + 1) * FRAME_BYTES]
        for frame in READBACK_SAMPLES
    }
    movement = assert_centroids(plan, sample_frames)
    temporal_motion = assert_full_decoded_camera(plan, rgb)
    return {
        "schema": "motionwright.manim.camera.decoded-readback/1",
        "compiled_plan_sha256": plan_sha,
        "trusted_media_sha256": media_sha,
        "binding": plan["binding"],
        "video": observation,
        "native_frame_movement_observations": movement,
        "all_120_decoded_frame_motion": temporal_motion,
        "decoded_rgb_sha256": hashlib.sha256(rgb).hexdigest(),
        "source_bound_video_observation": "PASS",
        "native_driver_authority_verified_by_this_reader": False,
        "full_editable_reopen_verified_by_this_reader": False,
        "voice_or_caption_mixing_verified": False,
        "human_acceptance": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--media-sha256", required=True)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if (not args.receipt.is_absolute() or args.receipt.exists()
            or not args.receipt.parent.is_dir()):
        raise SystemExit("receipt must be a new absolute file in an existing directory")
    result = verify(
        args.plan, args.plan_sha256, args.media, args.media_sha256,
        args.ffprobe, args.ffmpeg,
    )
    with args.receipt.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    main()
