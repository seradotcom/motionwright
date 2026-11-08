#!/usr/bin/env python3
"""Read retained PNGs for the explicit two-phase Manim continuation.

The edited-pair mode is the runner's two-phase check. The four-phase mode reads
all 96 old/new PNG originals on the host; its result still requires independent
artifact, context and sealed-custody lineage admission. No video is redecoded.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys

from native_manim_camera_evidence import private_json, read, read_object, require, sha
from native_manim_editable_pixels import COUNT, PHASES, changed_text, load_phase, rgba, text_roi
from native_manim_resume_transfer import SOURCE_RECOVERY


def recovery(path):
    raw = read(path)
    value = read_object(path)
    require(sha(raw) == SOURCE_RECOVERY["report_sha256"]
            and value.get("schema") == "motionwright.manim.editable.committed-source-recovery/1"
            and value.get("status") == "SOURCE_COMMITTED_RECOVERED"
            and value.get("scope") == "SOURCE_ONLY_NO_NATIVE_AUTHORITY"
            and value.get("original_edit_report_recreated") is False
            and value.get("project_changes_applied") == 0
            and value.get("current_export_sha256") == SOURCE_RECOVERY["export_sha256"],
            "resume_pixel_original_source_recovery")
    return raw, value


def decoder():
    import PIL
    from PIL import Image, _imaging
    files = [Path(Image.__file__).resolve(), Path(_imaging.__file__).resolve()]
    return {"implementation": "Pillow", "version": PIL.__version__,
            "files": [{"name": path.name, "bytes": path.stat().st_size,
                       "sha256": sha(read(path, 32 * 1024 * 1024))} for path in files]}


def phase_bindings(phases, names):
    return {name: {"report_sha256": sha(row[3]), "artifact_sha256": row[2]["artifact_sha256"],
                   "source_sha256": row[2]["source_sha256"], "job_ref": row[2]["job_ref"]}
            for name, row in zip(names, phases)}


def unchanged_reports(roots, phases):
    for root, row in zip(roots, phases):
        require(read(root / "native-video-originals/decoded-video.json") == row[3],
                "resume_original_video_report_changed")


def edited_pair(roots, recovery_path):
    require(len(roots) == len(set(roots)) == 2, "resume_two_distinct_native_phases")
    recovered_raw, recovered = recovery(recovery_path)
    text_roi(recovered["prepared_plan"])
    phases = [load_phase(root) for root in roots]
    frames = []
    for i in range(COUNT):
        pixels = [rgba(frame_root / f"{i:06}.png", report["frames"][i])
                  for report, frame_root, _, _ in phases]
        require(pixels[0] == pixels[1], "resume_edited_reopen_pixel_difference")
        frames.append({"index": i, "rgba_sha256": sha(pixels[0]), "all_rgba_equal": True})
    unchanged_reports(roots, phases)
    return {"schema": "motionwright.manim.editable.resumed-edited-pixel-pair/1",
            "status": "EDITED_PAIR_REQUIRES_CUSTODY", "frame_count_per_phase": COUNT,
            "recovery_receipt_sha256": sha(recovered_raw), "all_edited_reopen_frames_equal": True,
            "phases": phase_bindings(phases, PHASES[2:]), "frames": frames,
            "png_decoder": decoder(), "old_two_phase_lineage_verified": False,
            "saved_text_change_against_original_verified": False,
            "native_video_decode_invocations": 0, "render_invocations": 0,
            "source_export_grants_authority": False, "human_acceptance": False}


def four_phase_pixels(roots, recovery_path):
    require(len(roots) == len(set(roots)) == 4, "resume_four_distinct_old_new_phases")
    recovered_raw, recovered = recovery(recovery_path)
    roi = text_roi(recovered["prepared_plan"])
    phases = [load_phase(root) for root in roots]
    comparisons, negative = [], None
    for i in range(COUNT):
        pixels = [rgba(frame_root / f"{i:06}.png", report["frames"][i])
                  for report, frame_root, _, _ in phases]
        require(pixels[0] == pixels[1], "resume_original_reopen_pixel_difference")
        require(pixels[2] == pixels[3], "resume_edited_reopen_pixel_difference")
        if i == 0:
            try:
                changed_text(pixels[0], pixels[0], roi)
            except ValueError as error:
                require(str(error) == "editable_text_change_unobserved", "resume_wrong_negative_rejection")
                negative = {"status": "EXPECTED_PIXEL_REJECTION", "rejection": str(error),
                            "original_png_sha256": phases[0][0]["frames"][0]["png_sha256"],
                            "original_rgba_sha256": sha(pixels[0]), "synthetic_pixels_used": False}
            else:
                raise ValueError("resume_original_pixels_satisfied_edited_text")
        comparisons.append({"index": i, **changed_text(pixels[0], pixels[2], roi),
                            "original_reopen_all_rgba_equal": True, "edited_reopen_all_rgba_equal": True,
                            "original_rgba_sha256": sha(pixels[0]), "edited_rgba_sha256": sha(pixels[2])})
    unchanged_reports(roots, phases)
    return {"schema": "motionwright.manim.editable.resumed-original-pixel-cycle/1",
            "status": "ALL_96_PNGS_VERIFIED_REQUIRES_LINEAGE_CUSTODY",
            "recovery_receipt_sha256": sha(recovered_raw), "frame_count_per_phase": COUNT,
            "original_reopen_all_frames_equal": True, "edited_reopen_all_frames_equal": True,
            "saved_text_change_observed_in_all_frames": True,
            "text_region_with_codec_margin": list(roi),
            "maximum_outside_difference_policy": "outside <= text // 20 + 96",
            "original_media_as_edited_negative": negative,
            "phases": phase_bindings(phases, PHASES), "frames": comparisons,
            "png_decoder": decoder(), "native_video_decode_invocations": 0, "render_invocations": 0,
            "source_export_grants_authority": False, "original_edit_report_recreated": False,
            "old_new_sealed_lineage_verified_by_pixels_alone": False,
            "human_acceptance": False}


def main():
    args = sys.argv[1:]
    require(args and args[0] in ("edited-pair", "four-phase"), "resume_pixels_explicit_mode")
    count = 2 if args[0] == "edited-pair" else 4
    require(len(args) == count + 3, "resume_pixels_cli_contract")
    roots = [Path(raw) for raw in args[1:1 + count]]
    recovery_path, output = map(Path, args[-2:])
    require(output.is_absolute() and output.parent.is_dir() and not output.exists()
            and not output.is_symlink(), "resume_pixels_new_original_report")
    result = (edited_pair if count == 2 else four_phase_pixels)(roots, recovery_path)
    private_json(output, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
