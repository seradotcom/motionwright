#!/usr/bin/env python3
"""Verify three original Semwright Motion Canvas PNG frames after Native Host exit.

Read-only. Source authoring, source-built ProductionCoordinator and independent
Store readback are separate original processes. No provider launch, image
generation, rights grant, effect replay or approval occurs in this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess

FRAMES = (0, 15, 30)
HOLD_FRAMES = (0, 29, 30)
PROFILES = {"out-cubic", "hold"}
RGB = (37, 212, 162)  # Exact authored source fill #25D4A2
REGION = (600, 160, 800, 600)
FRAME_BYTES = REGION[2] * REGION[3] * 3
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_SCHEMA = "motionwright.native-geometry-source/1"
READBACK_SCHEMA = "motionwright.native-geometry-independent-store-readback/1"


class GeometryRefused(ValueError):
    pass


def require(value: bool, code: str) -> None:
    if not value:
        raise GeometryRefused(code)


def raw_file(path: Path, maximum: int) -> bytes:
    require(path.is_absolute() and path.is_file() and not path.is_symlink()
            and all(not parent.is_symlink() for parent in path.parents)
            and 0 < path.stat().st_size <= maximum, "GEOMETRY_ORIGINAL_FILE_NOT_BOUND")
    return path.read_bytes()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_json(raw: bytes) -> dict:
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "GEOMETRY_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: require(False, "GEOMETRY_NONFINITE_JSON"))
    require(type(value) is dict, "GEOMETRY_JSON_OBJECT_REQUIRED")
    return value


def finite_number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def source_identity(source: dict, sha: str) -> dict:
    require(source.get("schema") == SOURCE_SCHEMA, "GEOMETRY_SOURCE_SCHEMA")
    identity = source.get("identity")
    project = source.get("project")
    require(type(identity) is dict and type(project) is dict
            and identity.get("schema") == SOURCE_SCHEMA
            and identity.get("frame_count") == 60 and identity.get("fps") == 30
            and identity.get("canvas_size") == [1920, 1080]
            and identity.get("fill") == "#25D4A2"
            and identity.get("easing") in {"ease_out_cubic", "hold"}
            and identity.get("native_execution") is False
            and identity.get("formal_G2") is False
            and identity.get("formal_G3") is False,
            "GEOMETRY_SOURCE_SELECTION_CHANGED")
    if identity["easing"] == "hold":
        require(type(identity.get("studio_authoring_sha256")) is str
                and SHA256.fullmatch(identity["studio_authoring_sha256"]),
                "GEOMETRY_HELD_STUDIO_SOURCE_MISSING")
    for field, project_field in (("project_id", "id"), ("generation", "generation"),
                                 ("revision", "revision")):
        require(identity.get(field) == project.get(project_field),
                "GEOMETRY_SOURCE_PROJECT_IDENTITY_CHANGED")
    scenes = project.get("scenes")
    profiles = project.get("deliverables")
    require(type(scenes) is list and len(scenes) == 1
            and type(profiles) is list and profiles
            and scenes[0].get("id") == identity.get("scene_id"),
            "GEOMETRY_SOURCE_SCENE_CHANGED")
    nodes = scenes[0].get("nodes")
    require(type(nodes) is list and len(nodes) == 1, "GEOMETRY_SOURCE_NODE_ROSTER")
    node = nodes[0]
    require(node.get("id") == identity.get("node_id")
            and node.get("name") == "Native geometry source-bound rectangle"
            and node.get("kind") == "rectangle"
            and node.get("parent_id") is None
            and node.get("coordinate_space") == "project_pixels"
            and node.get("style", {}).get("fill") == "#25D4A2"
            and node.get("opacity") == 1
            and node.get("rotation_deg") == 0,
            "GEOMETRY_SOURCE_RECTANGLE_CHANGED")
    base = {"x": 740, "y": 390, "width": 400, "height": 180}
    require(all(node.get(k) == v for k, v in base.items()),
            "GEOMETRY_SOURCE_BASE_TRANSFORM_CHANGED")
    keys = node.get("keyframes")
    require(type(keys) is list and len(keys) == 10, "GEOMETRY_SOURCE_KEY_ROSTER")
    expected = {
        "x": (700, 740), "y": (360, 390), "width": (240, 400),
        "height": (90, 180), "rotation_deg": (-18, 0),
    }
    observed = {}
    for key in keys:
        require(type(key) is dict and key.get("property") in expected
                and type(key.get("at")) is dict, "GEOMETRY_SOURCE_KEYS_CHANGED")
        at = (key["at"].get("num"), key["at"].get("den"))
        require(at in {("0", "1"), ("1", "1")}, "GEOMETRY_SOURCE_KEYS_CHANGED")
        selector = (key["property"], at[0])
        require(selector not in observed, "GEOMETRY_DUPLICATE_SOURCE_KEY")
        observed[selector] = (key.get("value"), key.get("interpolation"))
    require(len(observed) == 10, "GEOMETRY_SOURCE_KEYS_CHANGED")
    for name, (first, last) in expected.items():
        require(observed.get((name, "0")) == (first, "linear")
                and observed.get((name, "1")) == (last, identity["easing"]),
                "GEOMETRY_SOURCE_KEYS_CHANGED")
    return {
        "project_id": identity["project_id"],
        "generation": identity["generation"],
        "revision": identity["revision"],
        "node_id": identity["node_id"],
        "source_receipt_sha256": sha,
        "geometry_profile": "hold" if identity["easing"] == "hold" else "out-cubic",
    }


def expected_geometry(frame: int, profile: str = "out-cubic") -> dict:
    require(profile in PROFILES, "GEOMETRY_UNKNOWN_PROFILE")
    frames = HOLD_FRAMES if profile == "hold" else FRAMES
    require(frame in frames, "GEOMETRY_UNAUTHORIZED_FRAME")
    p = (0.0 if frame < 30 else 1.0) if profile == "hold" else 1.0 - (1.0 - frame / 30.0) ** 3
    width = 240.0 + 160.0 * p
    height = 90.0 + 90.0 * p
    rotation = -18.0 + 18.0 * p
    theta = math.radians(rotation)
    center = (820.0 + 120.0 * p, 405.0 + 75.0 * p)
    dx = (width * abs(math.cos(theta)) + height * abs(math.sin(theta))) / 2.0
    dy = (width * abs(math.sin(theta)) + height * abs(math.cos(theta))) / 2.0
    return {"frame": frame, "progress": p, "center": center,
            "width": width, "height": height, "rotation_deg": rotation,
            "bbox": (center[0] - dx, center[1] - dy,
                     center[0] + dx, center[1] + dy)}


def measure_rgb(pixels: bytes, frame: int, profile: str = "out-cubic") -> dict:
    x0, y0, rw, rh = REGION
    require(len(pixels) == FRAME_BYTES, "GEOMETRY_RGB24_FRAME_DIMENSIONS")
    count = sx = sy = sxx = syy = sxy = 0.0
    left, top, right, bottom = rw, rh, -1, -1
    for offset in range(0, len(pixels), 3):
        if any(abs(pixels[offset + index] - target) > 3
               for index, target in enumerate(RGB)):
            continue
        py, px = divmod(offset // 3, rw)
        x = x0 + px + 0.5
        y = y0 + py + 0.5
        count += 1
        sx += x
        sy += y
        sxx += x * x
        syy += y * y
        sxy += x * y
        left, top = min(left, px), min(top, py)
        right, bottom = max(right, px), max(bottom, py)
    require(count > 10000, "GEOMETRY_SOURCE_COLOR_MISSING")
    cx, cy = sx / count, sy / count
    vx = sxx / count - cx * cx
    vy = syy / count - cy * cy
    cov = sxy / count - cx * cy
    eigen = math.sqrt((vx - vy) ** 2 + 4 * cov * cov)
    measured_width = math.sqrt(max(0.0, 6 * (vx + vy + eigen)))
    measured_height = math.sqrt(max(0.0, 6 * (vx + vy - eigen)))
    observed_angle = math.degrees(0.5 * math.atan2(2 * cov, vx - vy))
    observed_bounds = (x0 + left, y0 + top, x0 + right + 1, y0 + bottom + 1)
    expected = expected_geometry(frame, profile)
    require(abs(cx - expected["center"][0]) <= 2.0
            and abs(cy - expected["center"][1]) <= 2.0,
            "GEOMETRY_NATIVE_POSITION_MISMATCH")
    require(abs(measured_width - expected["width"]) <= 3.6
            and abs(measured_height - expected["height"]) <= 3.6,
            "GEOMETRY_NATIVE_DIMENSIONS_MISMATCH")
    require(abs(observed_angle - expected["rotation_deg"]) <= 2.5,
            "GEOMETRY_NATIVE_ROTATION_MISMATCH")
    require(all(abs(a - b) <= 4.0 for a, b in zip(observed_bounds, expected["bbox"])),
            "GEOMETRY_NATIVE_BOUNDS_MISMATCH")
    area = expected["width"] * expected["height"]
    require(0.94 * area <= count <= 1.02 * area,
            "GEOMETRY_NATIVE_FILL_AREA_MISMATCH")
    return {
        "frame": frame, "center": [cx, cy], "width": measured_width,
        "height": measured_height, "rotation_deg": observed_angle,
        "solid_source_pixels": int(count), "bounding_edges": list(observed_bounds),
        "expected": expected,
    }


def output_directory(root: Path, relative: str) -> Path:
    require(type(relative) is str and 0 < len(relative) <= 200
            and "\\" not in relative and ":" not in relative
            and all(part not in {"", ".", ".."} for part in relative.split("/")),
            "GEOMETRY_UNSAFE_ARTIFACT_DIRECTORY")
    part = PurePosixPath(relative)
    require(not part.is_absolute(), "GEOMETRY_UNSAFE_ARTIFACT_DIRECTORY")
    output = root.joinpath(*part.parts)
    require(output.is_dir() and not output.is_symlink()
            and output.resolve(strict=True) == output,
            "GEOMETRY_ARTIFACT_DIRECTORY_NOT_ORIGINAL")
    return output


def decode_png(path: Path) -> bytes:
    require(path.is_file() and not path.is_symlink()
            and path.stat().st_size <= 16 * 1024 * 1024,
            "GEOMETRY_PNG_NOT_ORIGINAL_FILE")
    x, y, width, height = REGION
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
               "-i", str(path), "-vf", f"crop={width}:{height}:{x}:{y}",
               "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GeometryRefused("GEOMETRY_ORIGINAL_FFMPEG_UNAVAILABLE") from error
    require(result.returncode == 0 and len(result.stdout) == FRAME_BYTES,
            "GEOMETRY_ORIGINAL_PNG_RGB24_DECODE_FAILED")
    return result.stdout


def held_frame_identity(rows: list[dict]) -> dict:
    """A complete held interval has the same original lossless PNG bytes.

    The caller first hashes every bounded original against these manifest rows.
    Byte equality of complete PNGs implies equality of their decoded full frames.
    """
    require(type(rows) is list and len(rows) == 60
            and all(type(row) is dict and row.get("index") == index
                    and type(row.get("sha256")) is str and SHA256.fullmatch(row["sha256"])
                    for index, row in enumerate(rows)),
            "GEOMETRY_HOLD_ORIGINAL_ROSTER")
    first, destination = rows[0]["sha256"], rows[30]["sha256"]
    require(first != destination, "GEOMETRY_HOLD_NO_DESTINATION")
    require(all(row["sha256"] == first for row in rows[:30])
            and all(row["sha256"] == destination for row in rows[30:]),
            "GEOMETRY_HOLD_FRAME_NOT_HELD")
    return {"initial_interval": [0, 30], "destination_interval": [30, 60],
            "verified_original_frames": 60, "equality": "complete_original_png_bytes",
            "first_png_sha256": first, "destination_png_sha256": destination}


def inspect(source_path: Path, source_sha: str, result_path: Path,
            store_path: Path, output_root: Path) -> dict:
    require(type(source_sha) is str and SHA256.fullmatch(source_sha),
            "GEOMETRY_SOURCE_DIGEST_MISSING")
    source_data = raw_file(source_path, 4 * 1024 * 1024)
    require(digest(source_data) == source_sha, "GEOMETRY_SOURCE_DIGEST_CHANGED")
    source = source_identity(strict_json(source_data), source_sha)
    rendered = strict_json(raw_file(result_path, 4 * 1024 * 1024))
    readback = strict_json(raw_file(store_path, 4 * 1024 * 1024))
    require(rendered.get("native_render_e2e") == "PASS"
            and rendered.get("project_id") == source["project_id"]
            and rendered.get("generation") == source["generation"]
            and rendered.get("revision") == source["revision"]
            and rendered.get("frame_count") == 60
            and readback.get("schema") == READBACK_SCHEMA
            and readback.get("source_receipt_sha256") == source_sha
            and readback.get("reader_authority") == "StudioServiceReader"
            and readback.get("source_project_unchanged") is True
            and readback.get("source_history_unchanged") is True
            and readback.get("same_physical_database") is True,
            "GEOMETRY_RENDER_STORE_SOURCE_NOT_JOINED")
    artifact = rendered.get("artifact")
    require(type(artifact) is dict
            and type(artifact.get("directory")) is str,
            "GEOMETRY_RENDER_ARTIFACT_NOT_SOURCE_BOUND")
    claimed_manifest = artifact.get("manifest_sha256")
    require(claimed_manifest is None or
            (type(claimed_manifest) is str and SHA256.fullmatch(claimed_manifest)),
            "GEOMETRY_RENDER_MANIFEST_CLAIM_INVALID")
    artifact_root = output_directory(output_root, artifact["directory"])
    manifest_bytes = raw_file(artifact_root / "artifact-manifest.json", 4 * 1024 * 1024)
    observed_manifest = digest(manifest_bytes)
    require(claimed_manifest is None or observed_manifest == claimed_manifest,
            "GEOMETRY_MANIFEST_CHANGED")
    manifest = strict_json(manifest_bytes)
    plan = manifest.get("plan")
    rows = manifest.get("frames")
    require(type(plan) is dict and plan.get("frame_count") == 60
            and plan.get("first_frame") == 0 and plan.get("end_frame_exclusive") == 60
            and type(rows) is list and len(rows) == 60,
            "GEOMETRY_MANIFEST_FRAME_ROSTER_CHANGED")
    for index, row in enumerate(rows):
        expected = f"frames/{index:06d}.png"
        require(type(row) is dict and row.get("index") == index
                and row.get("file") == expected and SHA256.fullmatch(row.get("sha256", "")),
                "GEOMETRY_MANIFEST_ENTRY_CHANGED")
    profile = source["geometry_profile"]
    frames = HOLD_FRAMES if profile == "hold" else FRAMES
    held = None
    if profile == "hold":
        total = 0
        for row in rows:
            raw = raw_file(artifact_root / row["file"], 16 * 1024 * 1024)
            total += len(raw)
            require(total <= 256 * 1024 * 1024 and raw.startswith(b"\x89PNG\r\n\x1a\n")
                    and len(raw) == row.get("bytes") and digest(raw) == row["sha256"],
                    "GEOMETRY_HOLD_ORIGINAL_FRAME_CHANGED")
        held = held_frame_identity(rows)
    samples = []
    for frame in frames:
        file = artifact_root / "frames" / f"{frame:06d}.png"
        raw = raw_file(file, 16 * 1024 * 1024)
        row = rows[frame]
        require(len(raw) == row["bytes"] and digest(raw) == row["sha256"],
                "GEOMETRY_ORIGINAL_FRAME_DIGEST_CHANGED")
        observed = measure_rgb(decode_png(file), frame, profile)
        observed["original_png_sha256"] = row["sha256"]
        samples.append(observed)
    require(samples[0]["original_png_sha256"] != samples[-1]["original_png_sha256"],
            "GEOMETRY_NATIVE_RENDER_STATIC")
    return {
        "schema": "motionwright.native-geometry-pixels/1",
        "status": "SOURCE_BOUND_ORIGINAL_PNG_GEOMETRY_OBSERVED",
        "project": source, "artifact_manifest_sha256": observed_manifest,
        "original_frames": samples, "sampled_frame_roster": list(frames),
        "geometry_profile": profile, "held_frame_byte_identity": held,
        "read_only_store_source_bound": True, "automatic_retry": False,
        "installed_tauri": False, "human_visual_acceptance": False,
        "formal_G2": False, "formal_G3": False, "release_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-receipt", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--render-result", type=Path, required=True)
    parser.add_argument("--store-readback", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    require(args.report.is_absolute() and args.report.parent.is_dir()
            and not args.report.exists() and not args.report.is_symlink()
            and args.output_root.is_absolute() and args.output_root.is_dir(),
            "GEOMETRY_REPORT_OR_OUTPUT_ROOT_UNSAFE")
    report = inspect(args.source_receipt, args.source_sha256,
                     args.render_result, args.store_readback, args.output_root)
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(report, output, sort_keys=True, indent=2, allow_nan=False)
        output.write("\n")
        output.flush()
    print(json.dumps({"status": report["status"],
                      "frame_roster": report["sampled_frame_roster"],
                      "native_approval": False}, sort_keys=True))


if __name__ == "__main__":
    main()