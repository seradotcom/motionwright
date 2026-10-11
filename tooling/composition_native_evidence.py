#!/usr/bin/env python3
"""Independent real-image review of a bounded three-scene Semwright-native master.

All positive frames must originate from the pinned provider's rendered PNGs
or from independently decoded H264; this module does not synthesize proof.
The content is explicitly a first-party original/synthetic fixture, not
rights or creative approval for external customer material.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageOps

from procedural_native_evidence import visible_centers
from product_stage_native_evidence import (
    DISCLOSURE,
    inspect_stage_frame,
    node_by_role,
    text_sample,
)

SCHEMA = "motionwright.composition-native-evidence/1"
SIZE = (1920, 1080)
SEGMENT_SECONDS = 6
FPS = 30
FRAMES = 540
POSITIONS = (140, 270, 480)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise AssertionError(reason)


def _image_metric(path: Path, nodes: list[dict]) -> dict:
    require(len(nodes) == 2, "Original measured fixture must own two editable objects")
    label = next((node for node in nodes if node["kind"] == "text"), None)
    bar = next((node for node in nodes if node["kind"] == "rectangle"), None)
    require(label is not None and bar is not None, "Measured native scene lost the authored bar or label")
    require(label["text"] == "Synthetic rate · 37.5%", "Metric copy diverges from imported fixed-point source")
    require(abs(bar["width"] - 300.0) < 0.000001, "Explicit measured axis did not project its 37.5% value")
    with Image.open(path) as raw:
        image = raw.convert("RGB")
        require(image.size == SIZE, "Measured native frame viewport mismatch")
        bg = image.getpixel((8, 8))
        x = round(bar["x"] + bar["width"] * 0.5)
        y = round(bar["y"] + bar["height"] * 0.5)
        require(5 <= x < SIZE[0] - 5 and 5 <= y < SIZE[1] - 5,
                "Measured bar sample outside native frame")
        pixel = image.getpixel((x, y))
        contrast = max(abs(a - b) for a, b in zip(pixel, bg))
        glyphs = text_sample(image, label, bg)
    require(contrast > 45, "Native video failed to render the source-identified numeric bar")
    require(glyphs > 25, "Native video has no real glyphs for original measured value")
    return {
        "sha256": sha(path),
        "bar_center_rgb": pixel,
        "bar_contrast_from_background": contrast,
        "measured_label_glyph_samples": glyphs,
    }


def _source(folder: Path, seed: dict) -> list[dict]:
    require(seed["fixture"] == "composition-stack/1", "Unknown source family cannot enter native composition")
    require(seed["creative_approval"] == "required", "Synthetic evidence cannot self-certify creative approval")
    require(seed["original_ui_not_real_capture"] is True
            and seed["data_truth"] == "synthetic_fixture_not_real_world_metric",
            "Original/synthetic source was mislabeled as an actual customer application or measurement")
    require(seed["frame_count"] == FRAMES and (seed["width"], seed["height"]) == SIZE,
            "Source must declare the exact 18-second 1920x1080 master")
    project_path = folder / "editable-project.json"
    source = json.loads(project_path.read_text(encoding="utf-8"))
    require(source["generation"] == seed["generation"] and source["revision"] == seed["revision"],
            "Authored source project generation/revision disagrees with native master receipt")
    scenes = source["scenes"]
    design = source["production_design"]
    require(len(scenes) == len(seed["scene_ids"]) == 3
            and [scene["id"] for scene in scenes] == seed["scene_ids"],
            "Three original source scene identities/order are not preserved")
    require(len(design["stages"]) == 1 and len(design["procedural_fields"]) == 1
            and len(design["measured_tables"]) == len(design["measured_bindings"]) // 2 == 1,
            "Native project lost one of three source-owned composition contracts")
    require(all(scene["duration"] == {"num": "6", "den": "1"} for scene in scenes),
            "Native timeline does not contain three exact six-second source scenes")
    stage = design["stages"][0]
    field = design["procedural_fields"][0]
    table = design["measured_tables"][0]
    require(stage["id"] == seed["stage_id"] and stage["scene_id"] == scenes[0]["id"]
            and len(scenes[0]["nodes"]) == 14
            and stage["baseline"] == scenes[0]["nodes"],
            "Authored original Stage is not the first visible Film scene")
    require(field["id"] == seed["procedural_id"] and field["scene_id"] == scenes[1]["id"]
            and field["baseline"] == scenes[1]["nodes"] and len(scenes[1]["nodes"]) == 8
            and field["config"]["reveal_step_frames"] == 3,
            "Deterministic procedural scene has incorrect source identity or native keys")
    require(table["id"] == seed["measured_table_id"] and table["version"] == 1
            and table["source_sha256"] == seed["source_sha256"]
            and table["rows"] == [{"key": "rate", "label": "Synthetic rate",
                                    "milli": 37500, "unit": "percent"}],
            "Rendered metric is not bound to the original imported measured JSON")
    require(node_by_role(scenes[0]["nodes"])["disclosure"]["text"] == DISCLOSURE,
            "Native composition removed the original-graphic/not-a-capture disclosure")
    require((folder / "composition.motionwright").exists(),
            "Portable authored source bundle missing from original composition evidence")
    return scenes


def _sample(paths: list[Path], scenes: list[dict]) -> dict:
    require(len(paths) == 3 and all(path.is_file() for path in paths),
            "All three source/decoded scene image samples must exist")
    source_stages = scenes[0]["nodes"]
    procedural_nodes = scenes[1]["nodes"]
    stage = inspect_stage_frame(paths[0], source_stages, expect_settled=True)
    procedural = visible_centers(paths[1], procedural_nodes, SIZE)
    require(procedural["visible_count"] >= 7,
            "Procedural source objects disappeared from native scene at frame 270")
    measured = _image_metric(paths[2], scenes[2]["nodes"])
    require(len({stage["sha256"], procedural["frame_sha256"], measured["sha256"]}) == 3,
            "Rendered Stage/procedural/measured timeline stages have identical frame bytes")
    return {"stage": stage, "procedural": procedural, "measured": measured}


def inspect_composition_frames(frames: list[Path], folder: Path, seed: dict, *,
                               source_sha: str, provider_sha: str) -> dict:
    require(len(frames) == FRAMES, "Composition requires exactly 540 frames from real native provider")
    scenes = _source(folder, seed)
    selected = [frames[index] for index in POSITIONS]
    native = _sample(selected, scenes)
    sheet = Image.new("RGB", (1440, 270))
    for i, frame in enumerate(selected):
        with Image.open(frame) as image:
            thumbnail = ImageOps.fit(image.convert("RGB"), (480, 270))
            sheet.paste(thumbnail, (i * 480, 0))
    sheet_path = folder / "composition-native-contact-sheet.png"
    sheet.save(sheet_path, optimize=True)
    proof = {
        "schema": SCHEMA,
        "technical_sampling": "PASS",
        "creative_approval": "required",
        "source_sha": source_sha,
        "semwright_sha": provider_sha,
        "source_generation": seed["generation"],
        "source_revision": seed["revision"],
        "source_project_sha256": sha(folder / "editable-project.json"),
        "scene_order": seed["scene_ids"],
        "frame_indices": list(POSITIONS),
        "native": native,
        "contact_sheet": {"path": sheet_path.name, "sha256": sha(sheet_path)},
        "source_truth": "first-party-original-and-synthetic-only",
    }
    (folder / "composition-native-inspection.json").write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return proof


def inspect_composition_mp4(master: Path, folder: Path, seed: dict, ffmpeg: Path) -> dict:
    """Independently decode the actual delivered MP4: no reuse of native PNGs."""
    scenes = _source(folder, seed)
    with tempfile.TemporaryDirectory(prefix="composition-decoded-") as temp:
        target = Path(temp)
        chosen = "+".join("eq(n\\,"+str(index)+")" for index in POSITIONS)
        command = [
            str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(master), "-map", "0:v:0",
            "-vf", "select="+chosen, "-vsync", "0", "-frames:v", "3",
            str(target / "decoded-%02d.png"),
        ]
        result = subprocess.run(command, capture_output=True, check=False, timeout=180)
        require(result.returncode == 0, "Delivered H264 master cannot be independently frame-decoded")
        decoded = sorted(target.glob("decoded-*.png"))
        require(len(decoded) == 3, "Delivered MP4 has missing original Stage/procedural/metric scenes")
        pixels = _sample(decoded, scenes)
    report_path = folder / "composition-native-inspection.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["decoded_master"] = {
        "mp4_sha256": sha(master),
        "samples": pixels,
        "frame_indices": list(POSITIONS),
        "technical_sampling": "PASS",
        "creative_approval": "required",
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    return report
