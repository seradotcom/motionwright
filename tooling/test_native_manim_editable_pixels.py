"""G0 PNG/oracle tests; no Manim, video encoder or native provider is invoked."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import native_manim_editable_pixels as pixels


def solid():
    return bytes((21, 48, 71, 255)) * (pixels.WIDTH * pixels.HEIGHT)


def changed(raw, positions):
    result = bytearray(raw)
    for x, y in positions:
        offset = (y * pixels.WIDTH + x) * 4
        result[offset:offset + 3] = bytes((255, 255, 255))
    return bytes(result)


class PixelDifferenceTests(unittest.TestCase):
    def setUp(self):
        self.original = solid()
        self.roi = (180, 210, 410, 310)
        self.text_pixels = [(x, 250) for x in range(280, 296)]
        self.edited = changed(self.original, self.text_pixels)

    def test_actual_rgba_change_inside_text_is_measured(self):
        result = pixels.changed_text(self.original, self.edited, self.roi)
        self.assertEqual(result["changed_text_pixels"], 16)
        self.assertGreater(result["text_absolute_rgb_difference"], 96)
        self.assertEqual(result["outside_absolute_rgb_difference"], 0)

    def test_original_frame_cannot_satisfy_edited_text(self):
        with self.assertRaisesRegex(ValueError, "text_change_unobserved"):
            pixels.changed_text(self.original, self.original, self.roi)

    def test_change_to_another_object_is_not_accepted_as_a_text_edit(self):
        forged = changed(self.original, self.text_pixels + [(x, 30) for x in range(200)])
        with self.assertRaisesRegex(ValueError, "change_outside_text_region"):
            pixels.changed_text(self.original, forged, self.roi)

    def test_header_only_or_alpha_only_difference_does_not_fake_text_pixels(self):
        alpha = bytearray(self.original)
        alpha[3] = 0
        with self.assertRaisesRegex(ValueError, "text_change_unobserved"):
            pixels.changed_text(self.original, bytes(alpha), self.roi)

    def test_pixel_fixture_requires_actual_saved_text_and_fixed_geometry(self):
        plan = {
            "render_profile": {"width": 640, "height": 360, "frame_rate": 12},
            "canvas": {"width": 1920, "height": 1080, "coordinate_space": "project_pixels"},
            "framing": "replan",
            "nodes": [{"rotation_deg": 0, "opacity": 1, "x_px": 620, "y_px": 720,
                       "width_px": 520, "height_px": 120,
                       "typography": {"text": pixels.EDITED_TEXT}}],
        }
        roi = pixels.text_roi(plan)
        self.assertEqual(roi, (182, 216, 404, 304))
        wrong = copy.deepcopy(plan)
        wrong["nodes"][0]["typography"]["text"] = pixels.ORIGINAL_TEXT
        with self.assertRaisesRegex(ValueError, "edited_text"):
            pixels.text_roi(wrong)


class OriginalPngTests(unittest.TestCase):
    def setUp(self):
        # Pillow is already part of the pinned Manim runtime. Missing it is a
        # failed environment prerequisite, never a silently skipped test.
        from PIL import Image
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "000000.png"
        self.rgba = solid()
        Image.frombytes("RGBA", (pixels.WIDTH, pixels.HEIGHT), self.rgba).save(self.path)
        raw = self.path.read_bytes()
        self.row = {"png_bytes": len(raw), "png_sha256": hashlib.sha256(raw).hexdigest(),
                    "rgba_sha256": hashlib.sha256(self.rgba).hexdigest()}

    def tearDown(self):
        self.tmp.cleanup()

    def test_original_png_is_really_decoded_and_bound_to_rgba_bytes(self):
        self.assertEqual(pixels.rgba(self.path, self.row), self.rgba)

    def test_declared_original_rgba_cannot_admit_changed_png_content(self):
        from PIL import Image
        altered = changed(self.rgba, [(10, 10)])
        Image.frombytes("RGBA", (pixels.WIDTH, pixels.HEIGHT), altered).save(self.path)
        raw = self.path.read_bytes()
        row = {**self.row, "png_bytes": len(raw), "png_sha256": hashlib.sha256(raw).hexdigest()}
        with self.assertRaisesRegex(ValueError, "original_rgba"):
            pixels.rgba(self.path, row)

    def test_original_png_file_hash_cannot_be_replaced_by_report_metadata(self):
        self.path.write_bytes(self.path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "original_digest"):
            pixels.rgba(self.path, self.row)



class AllFramesTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.original = solid()
        self.edited = changed(self.original, [(x, 260) for x in range(280, 296)])
        files = []
        for name, rgba in (("original", self.original), ("edited", self.edited)):
            path = self.root / (name + ".png")
            Image.frombytes("RGBA", (pixels.WIDTH, pixels.HEIGHT), rgba).save(path)
            files.append((path.read_bytes(), hashlib.sha256(rgba).hexdigest()))
        self.roots = [self.root / phase for phase in pixels.PHASES]
        for index, root in enumerate(self.roots):
            frame_root = root / "native-video-originals/decoded-video.json.frames"
            frame_root.mkdir(parents=True)
            raw, rgba_sha = files[0 if index < 2 else 1]
            report = {"width": 640, "height": 360, "fps_num": 12, "fps_den": 1,
                      "frame_count": 24, "frames": []}
            for frame in range(24):
                name = f"{frame:06}.png"
                (frame_root / name).write_bytes(raw)
                report["frames"].append({
                    "index": frame, "file": name, "png_bytes": len(raw),
                    "png_sha256": hashlib.sha256(raw).hexdigest(), "rgba_sha256": rgba_sha,
                })
            self.write_report(root, report, index)
        self.edit = self.root / "edit.json"
        self.edit.write_text(json.dumps({
            "schema": "motionwright.manim.editable.source-cycle/1", "phase": "edit-import",
            "original_text": pixels.ORIGINAL_TEXT, "edited_text": pixels.EDITED_TEXT,
            "prepared_plan": {
                "render_profile": {"width": 640, "height": 360, "frame_rate": 12},
                "canvas": {"width": 1920, "height": 1080, "coordinate_space": "project_pixels"},
                "framing": "replan",
                "nodes": [{"rotation_deg": 0, "opacity": 1, "x_px": 620, "y_px": 720,
                           "width_px": 520, "height_px": 120,
                           "typography": {"text": pixels.EDITED_TEXT}}],
            },
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def write_report(self, root, report, index):
        raw = json.dumps(report, sort_keys=True).encode()
        (root / "native-video-originals/decoded-video.json").write_bytes(raw)
        (root / "native-video-admission.json").write_text(json.dumps({
            "status": "PASS", "report_sha256": hashlib.sha256(raw).hexdigest(),
            "frame_count": 24, "native_metadata_frame_count": 24,
            "artifact_sha256": str(index) * 64, "source_sha256": str(index + 4) * 64,
            "job_ref": "manim-" + f"{index + 1:032x}",
        }), encoding="utf-8")

    def test_all_24_frames_of_both_independent_opens_are_read(self):
        result = pixels.compare(self.roots, self.edit)
        self.assertEqual(len(result["frames"]), 24)
        self.assertEqual(result["frames"][-1]["index"], 23)
        self.assertTrue(result["original_reopen_all_frames_equal"])
        self.assertTrue(result["edited_reopen_all_frames_equal"])
        self.assertEqual(result["original_media_as_edited_negative"]["status"],
                         "EXPECTED_PIXEL_REJECTION")
        self.assertFalse(result["original_media_as_edited_negative"]["synthetic_pixels_used"])

    def test_a_changed_last_reopen_frame_fails_after_23_equal_frames(self):
        target = self.roots[3]
        report = json.loads((target / "native-video-originals/decoded-video.json").read_text())
        original = self.roots[0] / "native-video-originals/decoded-video.json.frames/000023.png"
        raw = original.read_bytes()
        (target / "native-video-originals/decoded-video.json.frames/000023.png").write_bytes(raw)
        report["frames"][23].update(
            png_bytes=len(raw), png_sha256=hashlib.sha256(raw).hexdigest(),
            rgba_sha256=hashlib.sha256(self.original).hexdigest(),
        )
        self.write_report(target, report, 3)
        with self.assertRaisesRegex(ValueError, "edited_reopen_pixel_difference"):
            pixels.compare(self.roots, self.edit)


if __name__ == "__main__":
    unittest.main()
