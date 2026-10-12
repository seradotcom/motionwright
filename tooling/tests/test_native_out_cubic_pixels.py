"""Synthetic adversarial *validator* tests; production uses actual FFmpeg PNGs."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_render_e2e import (
    verify_source_cubic_frames, mean_byte_distance, source_bound_cubic_roi,
)


def data(value: int) -> bytes:
    return bytes([value])*900


def position() -> tuple[bytes, bytes, bytes, bytes]:
    return (data(200),data(193),data(25),data(20))


def opacity() -> tuple[bytes, bytes, bytes]:
    return (data(12),data(177),data(200))


class CubicPixelValidatorTests(unittest.TestCase):
    def test_real_native_pixel_gate_accepts_only_a_correct_original_trajectory(self):
        proof=verify_source_cubic_frames(position(),opacity())
        self.assertTrue(proof["native_original_pixels_verified"])
        self.assertEqual(proof["interpolation"],"ease_out_cubic")
        self.assertEqual(proof["native_motion_easing"],"out_cubic")
        self.assertFalse(proof["pixel_difference_interpreted_as_visual_approval"])
        self.assertEqual(proof["position_sampled_frames"],[0,2,8,30])
        self.assertEqual(proof["opacity_sampled_frames"],[0,15,30])

    def test_linear_xy_midpoint_is_rejected_as_wrong_native_curve(self):
        original=list(position())
        original[2]=data(140)  # position ROI not yet cleared by quarter frame
        with self.assertRaisesRegex(AssertionError,"position ROI"):
            verify_source_cubic_frames(tuple(original),opacity())

    def test_static_xy_does_not_count_as_motion(self):
        with self.assertRaisesRegex(AssertionError,"position ROI"):
            verify_source_cubic_frames(tuple([data(50)]*4),opacity())

    def test_instantaneous_position_jump_is_not_cubic_out(self):
        original=list(position())
        original[1]=data(20)  # jumped at frame 2
        with self.assertRaisesRegex(AssertionError,"position ROI"):
            verify_source_cubic_frames(tuple(original),opacity())

    def test_linear_opacity_halfway_is_rejected(self):
        with self.assertRaisesRegex(AssertionError,"opacity ROI"):
            verify_source_cubic_frames(position(),(data(12),data(98),data(200)))

    def test_static_opacity_fade_is_rejected(self):
        with self.assertRaisesRegex(AssertionError,"opacity ROI"):
            verify_source_cubic_frames(position(),(data(12),data(12),data(12)))

    def test_instantaneous_opacity_jump_cannot_count_as_easing(self):
        with self.assertRaisesRegex(AssertionError,"opacity ROI"):
            verify_source_cubic_frames(position(),(data(12),data(200),data(200)))

    def test_mismatched_rgb24_byte_lengths_fail_before_ratio(self):
        with self.assertRaisesRegex(AssertionError,"byte dimensions"):
            verify_source_cubic_frames(
                (data(200),data(193),data(25),b"short"),opacity())

    def test_unapproved_roi_names_cannot_select_arbitrary_pixels(self):
        with self.assertRaisesRegex(AssertionError,"unrecognized"):
            source_bound_cubic_roi(Path("nonexistent.png"),kind="secrets")

    def test_mean_distance_uses_original_byte_values_without_correction(self):
        self.assertEqual(mean_byte_distance(data(0),data(255)),255)
        self.assertEqual(mean_byte_distance(data(128),data(128)),0)


if __name__=="__main__":
    unittest.main()
