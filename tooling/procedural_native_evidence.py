#!/usr/bin/env python3
"""Technical review of real Semwright-rendered procedural frames.

Checks authored object identities and multiple actual PNG frames. This is not
human creative approval and not evidence for hypothetical external renderers.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageOps


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def visible_centers(path: Path, nodes: list[dict], expected_size: tuple[int, int]) -> dict:
    with Image.open(path) as raw:
        rgb = raw.convert("RGB")
        if rgb.size != expected_size:
            raise AssertionError(f"Native procedural frame has wrong size {rgb.size}")
        background = rgb.getpixel((3, 3))
        visibility: list[bool] = []
        for node in nodes:
            x = round(node["x"] + node["width"] / 2)
            y = round(node["y"] + node["height"] / 2)
            if not (3 < x < expected_size[0] - 3 and 3 < y < expected_size[1] - 3):
                raise AssertionError("Procedural object center is outside native frame")
            pixel = rgb.getpixel((x, y))
            visibility.append(max(abs(a - b) for a, b in zip(pixel, background)) >= 35)
        return {
            "frame_sha256": file_sha(path),
            "background_rgb": background,
            "visible_count": sum(visibility),
            "visible_indices": [index for index, yes in enumerate(visibility) if yes],
        }


def verify_temporal_samples(first: dict, middle: dict, last: dict, *,
                            count: int, animated: bool) -> None:
    if last["visible_count"] < count - 1:
        raise AssertionError("Final procedural native frame misses authored objects")
    if middle["visible_count"] < count - 1:
        raise AssertionError("Native procedural study does not settle before mid-master")
    if animated:
        if first["visible_count"] > middle["visible_count"] - 5:
            raise AssertionError("Procedural entrance did not create measurable item visibility progression")
    elif first["visible_count"] < count - 1:
        raise AssertionError("Declared static procedural frame unexpectedly starts invisible")
    if not animated and set(first["visible_indices"]) != set(last["visible_indices"]):
        raise AssertionError("Static procedural native content changed over time")


def inspect_procedural_frames(
    frames: list[Path], evidence: Path, seed: dict, *,
    source_sha: str, provider_sha: str
) -> dict:
    if len(frames) != 180:
        raise AssertionError("Procedural study must contain exactly 180 native frames")
    source = json.loads((evidence / "editable-project.json").read_text(encoding="utf-8"))
    if source["revision"] != seed["revision"] or source["generation"] != seed["generation"]:
        raise AssertionError("Native procedural frames are not attached to current authored revision")
    if len(source["production_design"]["procedural_fields"]) != 1:
        raise AssertionError("Project lacks a single stored procedural generator")
    field = source["production_design"]["procedural_fields"][0]
    nodes = source["scenes"][0]["nodes"]
    if field["config"] != seed["procedural_config"]:
        raise AssertionError("Procedural source config drifted from seeding receipt")
    if field["generator_version"] != 1 or nodes != field["baseline"]:
        raise AssertionError("Procedural source objects no longer equal their generated baseline")
    if len(nodes) != 12 or [node["name"] for node in nodes] != [
        f"ProceduralField / item {i:03}" for i in range(12)
    ]:
        raise AssertionError("Procedural project has missing/renamed authored objects")
    if len({node["id"] for node in nodes}) != 12:
        raise AssertionError("Procedural IDs are not unique")
    animated = seed["fixture"] == "procedural-field-motion/1"
    if animated != bool(field["config"]["reveal_step_frames"]):
        raise AssertionError("Native fixture animation declaration is inconsistent")
    size = (seed["width"], seed["height"])
    selected = (frames[0], frames[30], frames[90], frames[179])
    metrics = [visible_centers(frame, nodes, size) for frame in selected]
    verify_temporal_samples(metrics[0], metrics[2], metrics[3],
                            count=len(nodes), animated=animated)

    # Contact sheet holds only four thumbnails; no fabricated editorial overlays
    # and no persistence of 180 full-resolution PNGs in CI artifacts.
    sheet = Image.new("RGB", (960, 540))
    for index, frame in enumerate(selected):
        with Image.open(frame) as source_frame:
            thumb = ImageOps.fit(source_frame.convert("RGB"), (480, 270))
            sheet.paste(thumb, ((index % 2) * 480, (index // 2) * 270))
    sheet_path = evidence / "procedural-contact-sheet.png"
    sheet.save(sheet_path, optimize=True)
    report = {
        "schema": "motionwright.procedural-native-pixel-evidence/1",
        "technical_sampling": "PASS",
        "creative_approval": "required",
        "kind": seed["fixture"],
        "source_sha": source_sha,
        "semwright_sha": provider_sha,
        "generation": seed["generation"],
        "revision": seed["revision"],
        "scene_id": seed["scene_id"],
        "source_project_sha256": file_sha(evidence / "editable-project.json"),
        "contact_sheet": {"path": sheet_path.name, "sha256": file_sha(sheet_path)},
        "native_samples": [
            {"frame_index": at, **data}
            for at, data in zip((0, 30, 90, 179), metrics)
        ],
    }
    (evidence / "procedural-native-inspection.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def inspect_procedural_mp4(
    master: Path, evidence: Path, seed: dict, ffmpeg: Path
) -> dict:
    """Independently decode the delivered MP4, not just native PNG sources."""
    project = json.loads((evidence / "editable-project.json").read_text(encoding="utf-8"))
    nodes = project["scenes"][0]["nodes"]
    with tempfile.TemporaryDirectory(prefix="procedural-decoded-") as temp:
        root = Path(temp)
        command = [
            str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(master), "-map", "0:v:0",
            "-vf", r"select=eq(n\,0)+eq(n\,120)",
            "-vsync", "0", "-frames:v", "2",
            str(root / "decoded-%02d.png"),
        ]
        result = subprocess.run(command, capture_output=True, check=False, timeout=120)
        if result.returncode != 0:
            raise AssertionError("Cannot independently decode procedural MP4 sample frames")
        images = sorted(root.glob("decoded-*.png"))
        if len(images) != 2:
            raise AssertionError("Decoded MP4 did not contain both requested procedural frames")
        dimensions = (seed["width"], seed["height"])
        first = visible_centers(images[0], nodes, dimensions)
        later = visible_centers(images[1], nodes, dimensions)
    is_animated = seed["fixture"] == "procedural-field-motion/1"
    if later["visible_count"] < len(nodes) - 1:
        raise AssertionError("Decoded MP4 lost procedural final visible objects")
    if is_animated:
        if first["visible_count"] >= later["visible_count"] - 5:
            raise AssertionError("Decoded MP4 lost seeded procedural entrance progression")
    elif first["visible_count"] < len(nodes) - 1:
        raise AssertionError("Decoded MP4 lost static procedural objects")
    report_file = evidence / "procedural-native-inspection.json"
    report = json.loads(report_file.read_text(encoding="utf-8"))
    report["decoded_master"] = {
        "mp4_sha256": file_sha(master),
        "first_decoded_visibility": first,
        "later_decoded_visibility": later,
        "video_frames_sampled": [0, 120],
        "technical_sampling": "PASS",
        "creative_approval": "required",
    }
    report_file.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
