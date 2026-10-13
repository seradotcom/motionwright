#!/usr/bin/env python3
"""Independent original-camera technical pixel proof, not optical/3D evidence.

Uses actual pinned Semwright Motion Canvas PNGs and separately decoded H.264
frames, never synthetic stand-ins for a passing renderer or human review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageOps

WIDTH, HEIGHT = 1920, 1080


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def color_delta(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return max(abs(int(x) - int(y)) for x, y in zip(a, b))


def probe_pan(source: Path, target: Path, shift_x: int, shift_y: int,
              *, codec_tolerance: int) -> dict:
    with Image.open(source) as original, Image.open(target) as terminal:
        first = original.convert("RGB")
        last = terminal.convert("RGB")
        require(first.size == (WIDTH, HEIGHT) and last.size == (WIDTH, HEIGHT),
                "Camera master image dimensions changed")
        background = last.getpixel((8, 8))
        total, aligned, wrong = 0, 0, 0
        # Only inspect visible textured/edge pixels. Flat background or
        # broad solid regions alone cannot prove a source camera moved.
        for y in range(215, 875, 3):
            for x in range(310, 1590, 3):
                px, py = x + shift_x, y + shift_y
                if px + 4 >= WIDTH or py + 4 >= HEIGHT:
                    continue
                start = first.getpixel((px, py))
                edge = color_delta(start, first.getpixel((px + 4, py)))
                if color_delta(start, background) < 20 or edge < 13:
                    continue
                actual = last.getpixel((x, y))
                total += 1
                aligned += color_delta(start, actual) <= codec_tolerance
                wrong += color_delta(first.getpixel((x, y)), actual) <= codec_tolerance
        require(total >= 140, "Original stage lacks enough visible detailed pixels to prove a camera pan")
        similarity = aligned / total
        unmoved = wrong / total
        require(similarity >= (0.70 if codec_tolerance > 5 else 0.85),
                "Native camera did not realize the authored full-frame source displacement")
        require(similarity > unmoved + (0.04 if codec_tolerance > 5 else 0.09),
                "Native pixels do not distinguish camera translation from a static composition")
        return {
            "source_sha256": sha256(source), "target_sha256": sha256(target),
            "shift_x_px": shift_x, "shift_y_px": shift_y,
            "tested_edge_samples": total,
            "authored_motion_alignment": round(similarity, 5),
            "static_alignment": round(unmoved, 5),
        }


def inspect_native_camera_frames(
    frames: list[Path], evidence: Path, seeded: dict, *, source_sha: str,
    semwright_sha: str,
) -> dict:
    require(len(frames) == 180 and seeded.get("fixture") == "camera-reframe/1",
            "Original planar camera fixture is not the authorized 180-frame sequence")
    project_file = evidence / "editable-project.json"
    source = json.loads(project_file.read_text(encoding="utf-8"))
    require(source["revision"] == seeded["revision"] and
            source["generation"] == seeded["generation"],
            "Camera source revision differs from the rendered fixture")
    moves = source["production_design"]["camera_reframes"]
    stages = source["production_design"]["stages"]
    require(len(moves) == len(stages) == 1 and len(source["scenes"]) == 1,
            "Camera source has ambiguous scene ownership")
    move = moves[0]
    scene = source["scenes"][0]
    require(move["scene_id"] == scene["id"]
            and move["id"] == seeded["camera_reframe_id"]
            and move["start_center_x"] == 960 and move["end_center_x"] == 978
            and move["start_center_y"] == move["end_center_y"] == 540
            and move["duration_frames"] == 90,
            "Camera source moved away from the bounded native pan acceptance fixture")
    require(scene["camera"]["center_x"] == 978
            and len(scene["nodes"]) == 14
            and not any(node["keyframes"] for node in scene["nodes"]),
            "Camera source changed the original screen content or silently combined object motion")
    require(stages[0]["config"]["animate_focus"] is False
            and seeded["real_product_capture"] is False,
            "Camera source may not substitute a fake device recording or an object motion source")
    delta_x = int(move["end_center_x"] - move["start_center_x"])
    delta_y = int(move["end_center_y"] - move["start_center_y"])
    original = probe_pan(frames[0], frames[120], delta_x, delta_y, codec_tolerance=3)
    midpoint = probe_pan(frames[45], frames[120], delta_x // 2, delta_y // 2,
                         codec_tolerance=5)
    sheet = Image.new("RGB", (960, 540))
    for i, index in enumerate((0, 45, 120, 179)):
        with Image.open(frames[index]) as src:
            sheet.paste(ImageOps.fit(src.convert("RGB"), (480, 270)),
                        ((i % 2) * 480, (i // 2) * 270))
    sheet_path = evidence / "camera-contact-sheet.png"
    sheet.save(sheet_path, optimize=True)
    report = {
        "schema": "motionwright.original-planar-camera-pixel-proof/1",
        "technical_sampling": "PASS",
        "creative_approval": "required",
        "optical_orbit_dolly": "not_implemented",
        "source_revision": source["revision"],
        "project_id": source["id"],
        "source_sha256": sha256(project_file),
        "motionwright_sha": source_sha,
        "semwright_sha": semwright_sha,
        "camera_instance": move["id"],
        "native_original_to_terminal": original,
        "native_midpoint_to_terminal": midpoint,
        "contact_sheet": {"path": sheet_path.name, "sha256": sha256(sheet_path)},
    }
    (evidence / "camera-native-inspection.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def inspect_camera_master(master: Path, evidence: Path, ffmpeg: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="camera-mp4-independent-") as temporary:
        tmp = Path(temporary)
        command = [
            str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(master), "-map", "0:v:0",
            "-vf", r"select=eq(n\,0)+eq(n\,120)", "-vsync", "0",
            "-frames:v", "2", str(tmp / "sample-%02d.png"),
        ]
        completed = subprocess.run(command, capture_output=True, timeout=120, check=False)
        require(completed.returncode == 0, "Independent decoding of native H.264 master failed")
        samples = sorted(tmp.glob("sample-*.png"))
        require(len(samples) == 2, "Decoded native camera master lacks both source poses")
        decoded = probe_pan(samples[0], samples[1], 18, 0, codec_tolerance=29)
    proof_file = evidence / "camera-native-inspection.json"
    proof = json.loads(proof_file.read_text(encoding="utf-8"))
    proof["decoded_mp4"] = {
        "mp4_sha256": sha256(master),
        "frames": [0, 120],
        "technical_sampling": "PASS",
        "source_aligned_translation": decoded,
    }
    proof_file.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return proof
