#!/usr/bin/env python3
"""Independent real PNG + H264 frame evidence for original generic ProductStage.

This verifier never generates a positive native render. The source of frames
is the pinned Semwright Motion Canvas Driver Host; decoded frames are obtained
independently from the resulting real H264/AAC MP4. Negative unit-test images
are not admitted to the original-render success path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageOps


ROLES = (
    "frame", "screen", "chrome", "chrome_indicator",
    "screen_heading", "screen_detail", "card", "card_highlight",
    "action_button", "action_text", "focus_marker", "focus_caption",
    "screen_caption", "disclosure",
)
DISCLOSURE = "ORIGINAL UI GRAPHIC / NOT A PRODUCT CAPTURE"
IMAGE_SIZE = (1920, 1080)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def node_by_role(nodes: list[dict]) -> dict[str, dict]:
    return {n["name"].removeprefix("ProductStage / "): n for n in nodes}


def spot(rect: dict, x: float, y: float) -> tuple[int, int]:
    return (round(rect["x"] + rect["width"] * x),
            round(rect["y"] + rect["height"] * y))


def delta(first: tuple[int, int, int], second: tuple[int, int, int]) -> int:
    return max(abs(a - b) for a, b in zip(first, second))


def text_sample(img: Image.Image, rect: dict, reference: tuple[int, int, int]) -> int:
    left, top = int(rect["x"]), int(rect["y"])
    right = min(img.width, int(rect["x"] + rect["width"]))
    bottom = min(img.height, int(rect["y"] + rect["height"]))
    return sum(
        delta(img.getpixel((x, y)), reference) > 72
        for y in range(max(0, top), max(top, bottom), 3)
        for x in range(max(0, left), max(left, right), 4)
    )


def inspect_stage_frame(path: Path, source_nodes: list[dict], *,
                        expect_settled: bool) -> dict:
    roles = node_by_role(source_nodes)
    require(set(roles) == set(ROLES), "Stage render source role set changed")
    with Image.open(path) as source:
        img = source.convert("RGB")
        require(img.size == IMAGE_SIZE, "Original stage pixels have the wrong native dimensions")
        bg = img.getpixel((8, 8))
        frame_rgb = img.getpixel(spot(roles["frame"], .025, .025))
        require(delta(frame_rgb, bg) > 25,
                "Native generic stage frame is missing from its authored bounding box")
        screen_rgb = img.getpixel(spot(roles["screen"], .55, .38))
        require(delta(screen_rgb, bg) > 15,
                "Native screen surface missing from source-anchored position")
        card_rgb = img.getpixel(spot(roles["card"], .75, .52))
        require(delta(card_rgb, screen_rgb) > 15,
                "Native source semantic card is blank or misplaced")

        heading_samples = text_sample(img, roles["screen_heading"], screen_rgb)
        # The disclosure is outside the screen and must remain visible on
        # the editor's original-graphic frame, even before focus motion.
        disclosure_samples = text_sample(img, roles["disclosure"], bg)
        require(heading_samples > 20, "Native original UI heading is not visible as real glyph pixels")
        require(disclosure_samples > 25,
                "Native source has no visible NOT A PRODUCT CAPTURE disclosure pixels")

        focus_rgb = img.getpixel(spot(roles["focus_marker"], .5, .5))
        highlight_rgb = img.getpixel(spot(roles["card_highlight"], .5, .5))
        if expect_settled:
            require(delta(focus_rgb, card_rgb) > 65,
                    "Native screen-relative focus marker is missing after authored entrance")
            require(delta(highlight_rgb, card_rgb) > 55,
                    "Native focus highlight missing after original animation")
        else:
            require(delta(highlight_rgb, card_rgb) < 48,
                    "Focus emphasis started fully illuminated when it should enter over real frames")
        return {
            "sha256": digest(path),
            "frame_color_difference": delta(frame_rgb, bg),
            "screen_color_difference": delta(screen_rgb, bg),
            "card_color_difference": delta(card_rgb, screen_rgb),
            "heading_nonblank_samples": heading_samples,
            "disclosure_nonblank_samples": disclosure_samples,
            "focus_rgb": focus_rgb,
            "highlight_rgb": highlight_rgb,
        }


def inspect_native_stage(frames: list[Path], folder: Path, seeded: dict,
                         *, source_sha: str, semwright_sha: str) -> dict:
    require(len(frames) == 180, "ProductStage source-pinned render is not exactly 180 frames")
    require(seeded["fixture"] == "product-stage-original/1",
            "Cannot use original-graphic inspector for unknown fixture")
    source_path = folder / "editable-project.json"
    layout_path = folder / "expected-stage-layout.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    require(source["generation"] == layout["generation"] == seeded["generation"]
            and source["revision"] == layout["revision"] == seeded["revision"],
            "Native ProductStage evidence does not match immutable original source revision")
    require(seeded["original_graphic"] is True
            and seeded["real_product_capture"] is False
            and seeded["rights_verification"] == "author_declaration_not_audited",
            "Stage technical fixture misrepresented an original study as an authenticated capture")
    stages = source["production_design"]["stages"]
    require(len(stages) == 1 and len(source["scenes"]) == 1,
            "ProductStage source has ambiguous ownership")
    stage = stages[0]
    nodes = source["scenes"][0]["nodes"]
    require(len(nodes) == len(ROLES) and stage["baseline"] == layout["nodes"],
            "The original stage source does not match the persisted deterministic layout")
    roles = node_by_role(nodes)
    require(set(roles) == set(ROLES), "ProductStage declared role set mismatch")
    require(stage["config"]["frame"] ==
            ("browser" if seeded["stage_frame"] == "browser" else "generic_handset"),
            "Native stage silhouette diverged from the requested anonymous frame")
    require(stage["config"]["animate_focus"] is True,
            "The original semantic stage needs actual editable entrance motion")
    require(roles["disclosure"]["text"] == DISCLOSURE,
            "Original ProductStage disclosure was missing/overridden in project")
    require(roles["screen_heading"]["text"] == "Clear original workflow",
            "The native stage did not preserve the authorized replacement copy")
    require(roles["focus_marker"]["id"] == seeded["focus_id"]
            and roles["screen"]["id"] == seeded["screen_id"],
            "ProductStage fixed screen/focus identity was not preserved")
    require(not source["assets"],
            "Unadmitted third-party artwork or binary source entered the original stage")

    proof = []
    for index in (0, 30, 90, 179):
        proof.append({
            "frame_index": index,
            **inspect_stage_frame(frames[index], nodes, expect_settled=index >= 30),
        })
    sheet = Image.new("RGB", (960, 540))
    for i, frame_index in enumerate((0, 30, 90, 179)):
        with Image.open(frames[frame_index]) as frame:
            tile = ImageOps.fit(frame.convert("RGB"), (480, 270))
            sheet.paste(tile, ((i % 2) * 480, (i // 2) * 270))
    sheet_path = folder / "stage-contact-sheet.png"
    sheet.save(sheet_path, optimize=True)

    report = {
        "schema": "motionwright.original-product-stage-native-pixels/1",
        "technical_sampling": "PASS",
        "creative_approval": "required",
        "rights": "original_user_declaration_not_independently_verified",
        "authentic_capture": False,
        "frame": seeded["stage_frame"],
        "motionwright_sha": source_sha,
        "semwright_sha": semwright_sha,
        "generation": source["generation"],
        "revision": source["revision"],
        "layout_sha256": digest(layout_path),
        "project_sha256": digest(source_path),
        "contact_sheet": {"path": sheet_path.name, "sha256": digest(sheet_path)},
        "source_samples": proof,
    }
    (folder / "stage-native-inspection.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def inspect_stage_mp4(master: Path, folder: Path, seeded: dict, ffmpeg: Path) -> dict:
    source = json.loads((folder / "editable-project.json").read_text(encoding="utf-8"))
    nodes = source["scenes"][0]["nodes"]
    with tempfile.TemporaryDirectory(prefix="stage-h264-evidence-") as temp:
        stage = Path(temp)
        command = [
            str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(master), "-map", "0:v:0",
            "-vf", r"select=eq(n\,0)+eq(n\,120)", "-vsync", "0",
            "-frames:v", "2", str(stage / "sample-%02d.png"),
        ]
        observed = subprocess.run(command, capture_output=True, timeout=120, check=False)
        require(observed.returncode == 0, "Cannot decode native ProductStage H264 MP4")
        files = sorted(stage.glob("sample-*.png"))
        require(len(files) == 2, "Independent ProductStage decoder did not produce two samples")
        samples = [
            inspect_stage_frame(files[0], nodes, expect_settled=False),
            inspect_stage_frame(files[1], nodes, expect_settled=True),
        ]
    report_file=folder/"stage-native-inspection.json"
    report=json.loads(report_file.read_text(encoding="utf-8"))
    require(seeded["generation"] == report["generation"], "Decoded MP4 not bound to ProductStage source")
    report["decoded_mp4"] = {
        "master_sha256": digest(master),
        "sample_frame_indices": [0, 120],
        "samples": samples,
        "technical_sampling": "PASS",
        "capture_truth": "original_graphic_not_authenticated_capture",
    }
    report_file.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
