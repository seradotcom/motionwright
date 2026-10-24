#!/usr/bin/env python3
"""Read the original N3 PNGs; compare two independent source opens and one typed edit.

No renderer, encoder, Host, request, video decode, or writable runtime is created.
The native Rust parser owns video admission. Pillow only reads the retained PNGs.
"""
from __future__ import annotations
import hashlib
import io
import json
import math
from pathlib import Path
import sys

WIDTH, HEIGHT, COUNT = 640, 360, 24
ORIGINAL_TEXT = "x² + y² = r²"
EDITED_TEXT = "x² + y² = 2r²"
PHASES = ("original", "reopen-original", "edited", "reopen-edited")


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rgba(path, row):
    from PIL import Image
    from native_manim_camera_evidence import read
    raw = read(path, 32 * 1024 * 1024)
    require(len(raw) == row["png_bytes"] and sha(raw) == row["png_sha256"],
            "editable_png_original_digest")
    with Image.open(io.BytesIO(raw)) as image:
        require(image.format == "PNG" and image.size == (WIDTH, HEIGHT)
                and image.mode == "RGBA", "editable_png_original_format")
        image.load()
        pixels = image.tobytes("raw", "RGBA")
    require(len(pixels) == WIDTH * HEIGHT * 4
            and min(pixels[3::4]) == max(pixels[3::4]) == 255
            and sha(pixels) == row["rgba_sha256"], "editable_png_original_rgba")
    return pixels


def text_roi(plan):
    require(plan.get("render_profile") == {
        "width": WIDTH, "height": HEIGHT, "frame_rate": 12
    } and plan.get("canvas") == {
        "width": 1920, "height": 1080, "coordinate_space": "project_pixels"
    } and plan.get("framing") == "replan", "editable_pixel_fixture_geometry")
    nodes = [row for row in plan["nodes"] if row.get("typography") is not None]
    require(len(nodes) == 1 and nodes[0].get("rotation_deg") == 0
            and nodes[0].get("opacity") == 1, "editable_pixel_text_inventory")
    node = nodes[0]
    require(node["typography"]["text"] == EDITED_TEXT, "editable_pixel_edited_text")
    # Include codec ringing around the saved text layout, without admitting other nodes.
    x0 = max(0, math.floor(node["x_px"] / 3) - 24)
    y0 = max(0, math.floor(node["y_px"] / 3) - 24)
    x1 = min(WIDTH, math.ceil((node["x_px"] + node["width_px"]) / 3) + 24)
    y1 = min(HEIGHT, math.ceil((node["y_px"] + node["height_px"]) / 3) + 24)
    require(0 <= x0 < x1 <= WIDTH and 0 <= y0 < y1 <= HEIGHT,
            "editable_pixel_text_region")
    return (x0, y0, x1, y1)


def changed_text(original, edited, roi):
    require(len(original) == len(edited) == WIDTH * HEIGHT * 4,
            "editable_pixel_compare_size")
    require(original != edited, "editable_text_change_unobserved")
    x0, y0, x1, y1 = roi
    inside, outside, meaningful = 0, 0, 0
    for pixel in range(WIDTH * HEIGHT):
        offset = pixel * 4
        delta = sum(abs(original[offset + c] - edited[offset + c]) for c in range(3))
        x, y = pixel % WIDTH, pixel // WIDTH
        if x0 <= x < x1 and y0 <= y < y1:
            inside += delta
            meaningful += delta >= 12
        else:
            outside += delta
    require(meaningful >= 8 and inside >= 96, "editable_text_change_unobserved")
    require(outside <= inside // 20 + 96, "editable_change_outside_text_region")
    return {"changed_text_pixels": meaningful, "text_absolute_rgb_difference": inside,
            "outside_absolute_rgb_difference": outside}


def load_phase(root):
    from native_manim_camera_evidence import read, read_object
    raw = read(root / "native-video-originals/decoded-video.json")
    report = json.loads(raw)
    admission = read_object(root / "native-video-admission.json")
    require(admission.get("status") == "PASS"
            and admission.get("report_sha256") == sha(raw)
            and admission.get("frame_count") == COUNT
            and admission.get("native_metadata_frame_count") == COUNT
            and report.get("width") == WIDTH and report.get("height") == HEIGHT
            and report.get("fps_num") == 12 and report.get("fps_den") == 1
            and report.get("frame_count") == COUNT
            and len(report.get("frames", [])) == COUNT, "editable_original_admission")
    expected = {f"{i:06}.png" for i in range(COUNT)}
    frame_root = root / "native-video-originals/decoded-video.json.frames"
    require({path.name for path in frame_root.iterdir()} == expected,
            "editable_original_png_roster")
    for i, row in enumerate(report["frames"]):
        require(row.get("index") == i and row.get("file") == f"{i:06}.png",
                "editable_original_png_index")
    return report, frame_root, admission, raw


def compare(roots, edit_path):
    from native_manim_camera_evidence import read, read_object
    require(len(roots) == 4 and len(set(roots)) == 4, "editable_four_original_phases")
    edit_raw = read(edit_path)
    edit = read_object(edit_path)
    require(edit.get("schema") == "motionwright.manim.editable.source-cycle/1"
            and edit.get("phase") == "edit-import"
            and edit.get("original_text") == ORIGINAL_TEXT
            and edit.get("edited_text") == EDITED_TEXT,
            "editable_pixel_edit_identity")
    roi = text_roi(edit["prepared_plan"])
    phases = [load_phase(root) for root in roots]
    comparisons = []
    negative = None
    for i in range(COUNT):
        pixels = [rgba(frame_root / f"{i:06}.png", report["frames"][i])
                  for report, frame_root, _, _ in phases]
        require(pixels[0] == pixels[1], "editable_original_reopen_pixel_difference")
        require(pixels[2] == pixels[3], "editable_edited_reopen_pixel_difference")
        if i == 0:
            try:
                changed_text(pixels[0], pixels[0], roi)
            except ValueError as error:
                require(str(error) == "editable_text_change_unobserved",
                        "editable_original_negative_wrong_rejection")
                negative = {
                    "status": "EXPECTED_PIXEL_REJECTION",
                    "original_png_sha256": phases[0][0]["frames"][0]["png_sha256"],
                    "original_rgba_sha256": sha(pixels[0]),
                    "rejection": str(error), "synthetic_pixels_used": False,
                }
            else:
                raise ValueError("editable_original_pixels_satisfied_edited_text")
        change = changed_text(pixels[0], pixels[2], roi)
        comparisons.append({"index": i, **change,
                            "original_reopen_all_rgba_equal": True,
                            "edited_reopen_all_rgba_equal": True,
                            "original_rgba_sha256": sha(pixels[0]),
                            "edited_rgba_sha256": sha(pixels[2])})
    for root, (_, _, _, raw) in zip(roots, phases):
        require(read(root / "native-video-originals/decoded-video.json") == raw,
                "editable_original_report_changed")
    import PIL
    from PIL import Image, _imaging
    decoder_files = [Path(Image.__file__).resolve(), Path(_imaging.__file__).resolve()]
    return {
        "schema": "motionwright.manim.editable.original-pixel-cycle/1",
        "status": "PASS_REQUIRES_SEALED_CUSTODY",
        "edit_receipt_sha256": sha(edit_raw), "frame_count_per_phase": COUNT,
        "original_reopen_all_frames_equal": True,
        "edited_reopen_all_frames_equal": True,
        "saved_text_change_observed_in_all_frames": True,
        "text_region_with_codec_margin": list(roi),
        "maximum_outside_difference_policy": "outside <= text // 20 + 96",
        "original_media_as_edited_negative": negative,
        "phases": {name: {"report_sha256": sha(row[3]),
                         "artifact_sha256": row[2]["artifact_sha256"],
                         "source_sha256": row[2]["source_sha256"],
                         "job_ref": row[2]["job_ref"]}
                   for name, row in zip(PHASES, phases)},
        "frames": comparisons,
        "png_decoder": {"implementation": "Pillow", "version": PIL.__version__,
                        "files": [{"name": path.name, "bytes": path.stat().st_size,
                                   "sha256": sha(read(path, 32 * 1024 * 1024))}
                                  for path in decoder_files]},
        "native_video_decode_invocations": 0, "render_invocations": 0,
        "source_export_grants_authority": False,
        "motion_canvas_equivalence_verified": False, "human_acceptance": False,
    }


def main(argv=None):
    from native_manim_camera_evidence import private_json
    args = list(sys.argv[1:] if argv is None else argv)
    require(len(args) == 6, "editable_pixel_cli_contract")
    roots = [Path(value) for value in args[:4]]
    edit_path, report_path = map(Path, args[4:])
    require(report_path.is_absolute() and report_path.parent.is_dir()
            and not report_path.exists() and not report_path.is_symlink(),
            "editable_pixel_new_report")
    result = compare(roots, edit_path)
    private_json(report_path, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
