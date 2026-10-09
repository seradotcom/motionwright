#!/usr/bin/env python3
"""CI-only inspection of actual native frames; never labels creative quality PASS.

The source frame list comes from the verified renderer artifact, not a DOM mock.
Outputs remain bound to project generation/revision and both immutable source SHAs.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_native_frames(frames: list[Path], destination: Path, size: tuple[int, int], *,
                          project: dict, source_sha: str, provider_sha: str) -> dict:
    from PIL import Image, ImageChops, ImageDraw, ImageStat
    if len(frames) != 180 or len(source_sha) != 40 or len(provider_sha) != 40:
        raise AssertionError("native hero inspection requires its exact six-second acceptance fixture")
    sample_indices = [0, 5, 12, 20, 30, 44, 60, 179]
    samples = []
    images = []
    for index in sample_indices:
        path = frames[index]
        if not path.is_file() or path.is_symlink():
            raise AssertionError("native frame is absent or an unexpected symlink")
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != size:
                raise AssertionError(f"native frame {index} has unexpected size or encoding")
            images.append(image.convert("RGB"))
        output = destination / f"native-frame-{index:06d}.png"
        shutil.copyfile(path, output)
        samples.append({"frame": index, "time": {"num": str(index), "den": "30"},
                        "path": output.name, "sha256": sha256(output), "width": size[0], "height": size[1]})
    first_to_final = ImageChops.difference(images[0], images[-1])
    changed_mean = sum(ImageStat.Stat(first_to_final).mean) / 3
    if changed_mean <= 1.0:
        raise AssertionError("native entrance did not visibly change from its initial frame")
    settled_drift = ImageChops.difference(images[-2], images[-1]).getbbox()
    if settled_drift is not None:
        raise AssertionError("settled composition changed after all authored entrance intervals ended")
    sample_change = [ImageChops.difference(a, b).getbbox() is not None for a, b in zip(images, images[1:])]
    if not all(sample_change[:5]):
        raise AssertionError("native entrance lost a sampled temporal state")

    thumb_width = 384
    thumb_height = round(thumb_width * size[1] / size[0])
    gutter, label_height = 14, 34
    rows, cols = 2, 4
    header = 90
    sheet = Image.new("RGB", (cols*(thumb_width+gutter)+gutter, header+rows*(thumb_height+label_height+gutter)+gutter), "#151A20")
    draw = ImageDraw.Draw(sheet)
    draw.text((gutter,18), "PRODUCTHEROREVEAL / NATIVE FRAME CONTACT SHEET", fill="#F2F4F3")
    draw.text((gutter,40), f"{size[0]}x{size[1]} / 30 fps / revision {project['revision']} / Motionwright {source_sha[:12]}", fill="#A5C8DF")
    draw.text((gutter,61), "Technical samples only. Human creative approval and glyph review are still required.", fill="#A5C8DF")
    for i, image in enumerate(images):
        x = gutter+(i%cols)*(thumb_width+gutter)
        y = header+(i//cols)*(thumb_height+label_height+gutter)
        sheet.paste(image.resize((thumb_width,thumb_height),Image.Resampling.LANCZOS),(x,y))
        draw.text((x,y+thumb_height+10),f"frame {sample_indices[i]:03d} / {sample_indices[i]/30:.3f} s",fill="#F2F4F3")
    sheet.save(destination/"native-contact-sheet.png", optimize=True)
    onion = Image.blend(Image.blend(images[2],images[3],.5), images[4],1/3)
    onion.save(destination/"native-onion-012-020-030.png", optimize=True)
    difference = ImageChops.difference(images[2],images[3])
    difference.save(destination/"native-diff-012-020.png", optimize=True)
    report = {
        "schema":"motionwright.native-creative-frame-inspection/1",
        "project_id":project["project_id"],"generation":project["generation"],"revision":project["revision"],
        "motionwright_sha":source_sha,"provider":"semwright.motion-canvas","provider_sha":provider_sha,
        "method":"decoded-native-png-difference-and-temporal-sampling/1","units":"decoded RGB channel values [0,255]",
        "samples":samples,"first_to_final_mean_channel_difference":changed_mean,"settled_drift":False,
        "technical_sampling":"PASS","creative_approval":"required","pixel_equivalence_with_editorial_preview":"not_claimed",
        "limitations":["This detects frozen or drifting fixtures, not aesthetic quality.",
                       "No OCR or independently measured glyph layout is inferred from these samples.",
                       "The silent audio fixture tests transport only, not sound choreography."],
        "contact_sheet":{"path":"native-contact-sheet.png","sha256":sha256(destination/"native-contact-sheet.png")},
        "onion":{"path":"native-onion-012-020-030.png","sha256":sha256(destination/"native-onion-012-020-030.png")},
        "difference":{"path":"native-diff-012-020.png","sha256":sha256(destination/"native-diff-012-020.png")},
        "source_project":{"path":"editable-project.json","sha256":sha256(destination/"editable-project.json")},
    }
    (destination/"creative-frame-inspection.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return report
