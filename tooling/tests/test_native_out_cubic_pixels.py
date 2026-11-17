"""Adversarial validator data only; production inspects actual original Native PNGs."""
from __future__ import annotations

from functools import lru_cache
import math
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_render_e2e import (
    verify_source_cubic_frames, mean_byte_distance, source_bound_cubic_roi,
    CUBIC_POSITION_REGION, CUBIC_POSITION_FRAMES, CUBIC_POSITION_RGB,
)


def data(value: int) -> bytes:
    return bytes([value]) * 900


def opacity() -> tuple[bytes, bytes, bytes]:
    return data(12), data(177), data(200)


def rectangle(left, top, *, width=140, height=90, fill=CUBIC_POSITION_RGB, hollow=False):
    x, y, crop_width, crop_height = CUBIC_POSITION_REGION
    result = bytearray(bytes((12, 18, 24)) * crop_width * crop_height)
    first_x, end_x = math.ceil(left), math.floor(left + width)
    first_y, end_y = math.ceil(top), math.floor(top + height)
    for row in range(first_y, end_y):
        if not y <= row < y + crop_height:
            raise AssertionError("validator data left the admitted crop")
        if hollow and first_y + 1 < row < end_y - 2:
            continue
        begin = ((row - y) * crop_width + first_x - x) * 3
        result[begin:begin+(end_x-first_x)*3] = bytes(fill) * (end_x-first_x)
    return bytes(result)


@lru_cache(maxsize=16)
def position(x_curve="cubic", y_curve="cubic", end_x=1510, end_y=750, width=140, height=90):
    rows = []
    for frame in CUBIC_POSITION_FRAMES:
        t = frame / 30.0
        cubic = 1 - (1 - t) ** 3
        progress = {"cubic": cubic, "linear": t, "frozen": 0.0,
                    "jump": float(frame > 0)}
        rows.append(rectangle(1410 + (end_x-1410)*progress[x_curve],
                              620 + (end_y-620)*progress[y_curve],width=width,height=height))
    return tuple(rows)


class CubicPixelValidatorTests(unittest.TestCase):
    def test_real_native_pixel_gate_requires_both_original_axes_and_retains_fade(self):
        proof = verify_source_cubic_frames(position(), opacity())
        self.assertTrue(proof["native_original_pixels_verified"])
        self.assertEqual(proof["interpolation"], "ease_out_cubic")
        self.assertEqual(proof["native_motion_easing"], "out_cubic")
        self.assertTrue(proof["position_x_verified"])
        self.assertTrue(proof["position_y_verified"])
        self.assertFalse(proof["pixel_difference_interpreted_as_visual_approval"])
        self.assertEqual(proof["position_sampled_frames"], [0,2,8,30])
        self.assertEqual(proof["opacity_sampled_frames"], [0,15,30])
        self.assertEqual(proof["position_authored_start"], [1410,620])
        self.assertEqual(proof["position_authored_end"], [1510,750])
        self.assertLessEqual(max(error for row in proof["position_geometry"]
                                 for error in row["axis_error_px"]),1.5)

    def test_either_individually_frozen_axis_is_rejected(self):
        for axis, samples in (("x",position(x_curve="frozen")),("y",position(y_curve="frozen"))):
            with self.subTest(axis=axis), self.assertRaisesRegex(AssertionError,axis+" axis"):
                verify_source_cubic_frames(samples,opacity())

    def test_either_individually_linear_axis_is_rejected(self):
        for axis, samples in (("x",position(x_curve="linear")),("y",position(y_curve="linear"))):
            with self.subTest(axis=axis), self.assertRaisesRegex(AssertionError,axis+" axis"):
                verify_source_cubic_frames(samples,opacity())

    def test_instantaneous_jump_on_either_axis_is_rejected(self):
        for axis, samples in (("x",position(x_curve="jump")),("y",position(y_curve="jump"))):
            with self.subTest(axis=axis), self.assertRaisesRegex(AssertionError,axis+" axis"):
                verify_source_cubic_frames(samples,opacity())

    def test_swapped_axis_displacements_do_not_count_as_xy_motion(self):
        with self.assertRaisesRegex(AssertionError,"x axis"):
            verify_source_cubic_frames(position(end_x=1540,end_y=720),opacity())

    def test_incorrect_terminal_endpoint_is_rejected_on_each_axis(self):
        for samples in (position(end_x=1520),position(end_y=760)):
            with self.assertRaisesRegex(AssertionError,"axis"):
                verify_source_cubic_frames(samples,opacity())

    def test_wrong_shape_cannot_gain_credit_from_an_approximate_centroid(self):
        for samples in (position(width=120),position(height=70)):
            with self.assertRaisesRegex(AssertionError,"solid fill area"):
                verify_source_cubic_frames(samples,opacity())
        same_center = tuple(rectangle(1390,630,width=180,height=70) for _ in CUBIC_POSITION_FRAMES)
        with self.assertRaisesRegex(AssertionError,"bounds"):
            verify_source_cubic_frames(same_center,opacity())
        hollow = tuple(rectangle(1410,620,hollow=True) for _ in CUBIC_POSITION_FRAMES)
        with self.assertRaisesRegex(AssertionError,"solid fill area"):
            verify_source_cubic_frames(hollow,opacity())

    def test_missing_or_wrong_color_cannot_be_interpreted_as_source_motion(self):
        wrong = tuple(rectangle(1410,620,fill=(250,198,119)) for _ in CUBIC_POSITION_FRAMES)
        with self.assertRaisesRegex(AssertionError,"solid fill area"):
            verify_source_cubic_frames(wrong,opacity())

    def test_linear_static_or_instantaneous_opacity_is_still_rejected(self):
        for values in ((12,98,200),(12,12,12),(12,200,200)):
            with self.subTest(values=values), self.assertRaisesRegex(AssertionError,"opacity ROI"):
                verify_source_cubic_frames(position(),tuple(data(value) for value in values))

    def test_mismatched_rgb24_dimensions_and_frame_roster_fail(self):
        samples = list(position())
        samples[2] = b"short"
        with self.assertRaisesRegex(AssertionError,"byte dimensions"):
            verify_source_cubic_frames(tuple(samples),opacity())
        with self.assertRaisesRegex(AssertionError,"frame roster"):
            verify_source_cubic_frames(position()[:3],opacity())

    def test_unapproved_roi_names_cannot_select_arbitrary_pixels(self):
        with self.assertRaisesRegex(AssertionError,"unrecognized"):
            source_bound_cubic_roi(Path("nonexistent.png"),kind="secrets")

    def test_mean_distance_uses_original_byte_values_without_correction(self):
        self.assertEqual(mean_byte_distance(data(0),data(255)),255)
        self.assertEqual(mean_byte_distance(data(128),data(128)),0)


if __name__ == "__main__":
    unittest.main()
