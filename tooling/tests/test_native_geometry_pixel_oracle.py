"""Pure adversarial PNG-oracle data; no Native renderer or Store effect."""
from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_geometry_pixel_oracle as oracle


def modeled_frame(frame: int, *, easing="cubic", profile="out-cubic", width_delta=0, rotate_offset=0) -> bytes:
    source = oracle.expected_geometry(frame, profile)
    progress = source["progress"] if easing == "cubic" else frame / 30.0
    cx = 820 + 120 * progress
    cy = 405 + 75 * progress
    width = 240 + 160 * progress + width_delta
    height = 90 + 90 * progress
    angle = math.radians(-18 + 18 * progress + rotate_offset)
    cos, sin = math.cos(angle), math.sin(angle)
    x0, y0, rw, rh = oracle.REGION
    pixels = bytearray(bytes((12, 18, 24)) * (rw * rh))
    dx = (width * abs(cos) + height * abs(sin)) / 2 + 3
    dy = (width * abs(sin) + height * abs(cos)) / 2 + 3
    for y in range(max(0, math.floor(cy - dy) - y0), min(rh, math.ceil(cy + dy) - y0)):
        for x in range(max(0, math.floor(cx - dx) - x0), min(rw, math.ceil(cx + dx) - x0)):
            u = (x + x0 + 0.5 - cx) * cos + (y + y0 + 0.5 - cy) * sin
            v = -(x + x0 + 0.5 - cx) * sin + (y + y0 + 0.5 - cy) * cos
            if abs(u) <= width / 2 and abs(v) <= height / 2:
                index = (y * rw + x) * 3
                pixels[index:index + 3] = bytes(oracle.RGB)
    return bytes(pixels)


class GeometryPixelOracleTests(unittest.TestCase):
    def test_authored_linear_clock_with_out_cubic_geometry_is_admitted(self):
        for frame in oracle.FRAMES:
            with self.subTest(frame=frame):
                report = oracle.measure_rgb(modeled_frame(frame), frame)
                self.assertEqual(report["frame"], frame)
                self.assertLess(abs(report["rotation_deg"] -
                                    oracle.expected_geometry(frame)["rotation_deg"]), 1.5)

    def test_linear_or_static_midpoint_cannot_pass_as_cubic(self):
        for kind in ("linear", "frozen"):
            with self.subTest(kind=kind), self.assertRaises(oracle.GeometryRefused):
                frame = modeled_frame(15, easing=kind) if kind == "linear" else modeled_frame(0)
                oracle.measure_rgb(frame, 15)

    def test_shape_width_change_cannot_be_hidden_by_correct_center(self):
        with self.assertRaisesRegex(oracle.GeometryRefused, "DIMENSIONS"):
            oracle.measure_rgb(modeled_frame(15, width_delta=-40), 15)

    def test_wrong_rotation_and_unreadable_frames_are_rejected(self):
        with self.assertRaisesRegex(oracle.GeometryRefused, "ROTATION"):
            oracle.measure_rgb(modeled_frame(0, rotate_offset=14), 0)
        with self.assertRaisesRegex(oracle.GeometryRefused, "DIMENSIONS"):
            oracle.measure_rgb(b"no original RGB24 bytes", 0)

    def test_source_clock_is_not_approximated(self):
        with self.assertRaisesRegex(oracle.GeometryRefused, "UNAUTHORIZED_FRAME"):
            oracle.expected_geometry(14)


    def test_held_geometry_stays_initial_through_frame_29_and_jumps_at_30(self):
        for frame in oracle.HOLD_FRAMES:
            with self.subTest(frame=frame):
                report = oracle.measure_rgb(modeled_frame(frame, profile="hold"), frame, "hold")
                self.assertEqual(report["expected"]["progress"], 0 if frame < 30 else 1)

    def test_held_geometry_refuses_interpolation_and_late_or_early_jumps(self):
        for observed, frame in ((modeled_frame(29, profile="hold", easing="linear"), 29),
                                (modeled_frame(30, profile="hold"), 29),
                                (modeled_frame(0, profile="hold"), 30)):
            with self.subTest(frame=frame), self.assertRaises(oracle.GeometryRefused):
                oracle.measure_rgb(observed, frame, "hold")

    def test_complete_hold_intervals_reject_one_changed_original_or_static_output(self):
        rows = [{"index": index, "sha256": ("a" if index < 30 else "b") * 64} for index in range(60)]
        self.assertEqual(oracle.held_frame_identity(rows)["verified_original_frames"], 60)
        for index in (1, 28, 29, 30, 31, 59):
            bad = [dict(row) for row in rows]
            bad[index]["sha256"] = "c" * 64
            with self.subTest(index=index), self.assertRaises(oracle.GeometryRefused):
                oracle.held_frame_identity(bad)
        with self.assertRaisesRegex(oracle.GeometryRefused, "NO_DESTINATION"):
            oracle.held_frame_identity([{"index": i, "sha256": "a" * 64} for i in range(60)])
        with self.assertRaisesRegex(oracle.GeometryRefused, "ROSTER"):
            oracle.held_frame_identity(rows[:-1])

    def test_geometry_profile_cannot_be_replaced_with_another_easing(self):
        with self.assertRaisesRegex(oracle.GeometryRefused, "UNKNOWN_PROFILE"):
            oracle.expected_geometry(29, "linear")
        with self.assertRaisesRegex(oracle.GeometryRefused, "UNAUTHORIZED_FRAME"):
            oracle.expected_geometry(15, "hold")


if __name__ == "__main__":
    unittest.main()