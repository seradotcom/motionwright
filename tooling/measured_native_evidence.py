#!/usr/bin/env python3
"""Independent inspection of exact-SHA native rendered synthetic measured data.

This is a technical oracle for source Canvas/Film projection, not OCR, source
truth, creative design approval or a new renderer. It inspects the actual
pinned Motion Canvas PNG and separately decoded final H.264 MP4 samples.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageOps


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_measured_frame(path: Path, *, x: int, y: int, width: int) -> dict:
    """Probe source-relative inside/outside fill with no invented graphic pixels."""
    with Image.open(path) as incoming:
        rgb = incoming.convert("RGB")
        if rgb.size != (1920, 1080):
            raise AssertionError("Measured data image has wrong native master dimensions")
        background = rgb.getpixel((8, 8))
        inside = rgb.getpixel((x + max(12, width // 4), y + 36))
        outside = rgb.getpixel((x + width + 100, y + 36))
        diff_inside = max(abs(a - b) for a, b in zip(inside, background))
        diff_outside = max(abs(a - b) for a, b in zip(outside, background))
        if diff_inside < 36:
            raise AssertionError("Native measured bar is not visible in its authored source region")
        if diff_outside >= 36:
            raise AssertionError("Native measured bar exceeds its declared 300px width")
        # Independent text raster occupancy gate, not glyph/OCR verification.
        sampled_pixels = []
        for py in range(245, 405, 8):
            for px in range(360, 1420, 8):
                pixel = rgb.getpixel((px, py))
                sampled_pixels.append(max(abs(a - b) for a, b in zip(pixel, background)))
        nonblank_text_samples = sum(diff >= 30 for diff in sampled_pixels)
        if nonblank_text_samples < 25:
            raise AssertionError("Native measured label region is blank or invisible")
        return {
            "native_png_sha256": sha256(path),
            "background_rgb": background,
            "bar_inside_rgb": inside,
            "bar_outside_rgb": outside,
            "bar_inside_delta": diff_inside,
            "bar_outside_delta": diff_outside,
            "label_nonblank_samples": nonblank_text_samples,
        }


def inspect_measured_native_frames(
    frames: list[Path],
    evidence: Path,
    seeded: dict,
    *,
    source_sha: str,
    semwright_sha: str,
) -> dict:
    if len(frames) != 180 or seeded["fixture"] != "measured-data/1":
        raise AssertionError("Measured-data native fixture is not 180 frames")
    source = json.loads((evidence / "editable-project.json").read_text(encoding="utf-8"))
    scene = source["scenes"][0]
    design = source["production_design"]
    if source["revision"] != seeded["revision"] or source["generation"] != seeded["generation"]:
        raise AssertionError("Measured native proof is not tied to the authored project revision")
    if len(scene["nodes"]) != 3 or len(design["measured_tables"]) != 1 or len(design["measured_bindings"]) != 2:
        raise AssertionError("Native measured project no longer has one table, two bindings and one motion tile")
    table = design["measured_tables"][0]
    if table["version"] != 2 or table["source_sha256"] != seeded["source_v2_sha256"]:
        raise AssertionError("Measured dataset is not bound to imported v2 source")
    if len(table["rows"]) != 1 or table["rows"][0] != {
        "key": "rate", "label": "Synthetic rate", "milli": 37500, "unit": "percent"
    }:
        raise AssertionError("Measured row content differs from source fixture")
    if design["last_measured_impact"]["scene_ids"] != [seeded["scene_id"]]:
        raise AssertionError("Source revision did not mark only measured scene as dirty")
    texts = [node for node in scene["nodes"] if node["kind"] == "text"]
    bars = [node for node in scene["nodes"] if node["name"] == "Explicit 0–100% synthetic bar"]
    if len(texts) != 1 or texts[0]["text"] != "Synthetic rate · 37.5%" or len(bars) != 1:
        raise AssertionError("Native source did not preserve explicit measured text and bar object")
    bar = bars[0]
    if bar["width"] != 300 or bar["height"] != 72 or (bar["x"], bar["y"]) != (340, 480):
        raise AssertionError("Native source bar lost exact independently computed axis geometry")
    samples = []
    for index in (0, 30, 90, 179):
        samples.append({
            "frame_index": index,
            **inspect_measured_frame(frames[index], x=340, y=480, width=300),
        })
    sheet = Image.new("RGB", (960, 540))
    for i, frame_index in enumerate((0, 30, 90, 179)):
        with Image.open(frames[frame_index]) as img:
            thumb = ImageOps.fit(img.convert("RGB"), (480, 270))
            sheet.paste(thumb, ((i % 2) * 480, (i // 2) * 270))
    sheet_file = evidence / "measured-contact-sheet.png"
    sheet.save(sheet_file, optimize=True)
    report = {
        "schema": "motionwright.measured-native-pixel-evidence/1",
        "technical_sampling": "PASS",
        "creative_approval": "required",
        "source_measurement_truth": "synthetic_fixture_not_real_observation",
        "motionwright_sha": source_sha,
        "semwright_sha": semwright_sha,
        "generation": seeded["generation"],
        "revision": seeded["revision"],
        "table_id": seeded["measured_table_id"],
        "source_project_sha256": sha256(evidence / "editable-project.json"),
        "contact_sheet": {"path": sheet_file.name, "sha256": sha256(sheet_file)},
        "native_samples": samples,
    }
    (evidence / "measured-native-inspection.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def inspect_measured_master(
    master: Path, evidence: Path, seeded: dict, ffmpeg: Path
) -> dict:
    with tempfile.TemporaryDirectory(prefix="measured-h264-samples-") as temp:
        prefix = Path(temp)
        command = [
            str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(master), "-map", "0:v:0",
            "-vf", r"select=eq(n\,0)+eq(n\,120)", "-vsync", "0",
            "-frames:v", "2", str(prefix / "frame-%02d.png"),
        ]
        process = subprocess.run(command, capture_output=True, timeout=120, check=False)
        if process.returncode != 0:
            raise AssertionError("Cannot independently decode source-bound measured MP4")
        image_files = sorted(prefix.glob("frame-*.png"))
        if len(image_files) != 2:
            raise AssertionError("Decoded measured master lacks both requested frame samples")
        decoded = [inspect_measured_frame(file, x=340, y=480, width=300) for file in image_files]
    report_file = evidence / "measured-native-inspection.json"
    report = json.loads(report_file.read_text(encoding="utf-8"))
    report["decoded_mp4"] = {
        "mp4_sha256": sha256(master),
        "sample_frames": [0, 120],
        "samples": decoded,
        "technical_sampling": "PASS",
        "source_truth": "synthetic_fixture_not_real_observation",
    }
    report_file.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
