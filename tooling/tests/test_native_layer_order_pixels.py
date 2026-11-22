"""Adversarial source-only controls for original Native z-order pixel inspection.
This synthetic data never counts as a Native Host effect or human acceptance.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_render_e2e import verify_native_layer_original_pixels


def reference_pixels(front=True, back=True, *, back_over_front=False) -> bytes:
    image = bytearray(bytes((12, 18, 24)) * (240 * 145))
    def paint(x0, y0, width, height, color):
        for y in range(y0, y0 + height):
            offset = (y * 240 + x0) * 3
            image[offset:offset + width * 3] = bytes(color) * width
    if back:
        paint(0, 0, 240, 145, (37, 105, 184))
    if front:
        paint(65, 30, 155, 95, (224, 169, 64))
    if back_over_front:
        paint(0, 0, 240, 145, (37, 105, 184))
    return bytes(image)


class OriginalNativeLayerOrderPixels(unittest.TestCase):
    def test_source_promoted_front_layer_and_back_visible_without_fabricated_effect(self):
        frame = reference_pixels()
        result = verify_native_layer_original_pixels((frame, frame, frame))
        self.assertEqual(result["status"], "SOURCE_BOUND_PROMOTED_LAYER_VISIBLE")
        self.assertEqual(result["sampled_frames"], [0, 30, 59])
        self.assertFalse(result["native_effect_invoked_by_validator"])
        self.assertFalse(result["human_visual_acceptance"])

    def test_rejects_wrong_z_order_or_missing_source_subjects(self):
        for pixels in (reference_pixels(back_over_front=True),
                       reference_pixels(front=False),
                       reference_pixels(back=False)):
            with self.subTest(pixels=pixels[:3]), self.assertRaisesRegex(AssertionError, "missing or Z order"):
                verify_native_layer_original_pixels((pixels, pixels, pixels))

    def test_rejects_transient_wrong_order_and_incomplete_original_bytes(self):
        frame = reference_pixels()
        changed = bytearray(frame)
        changed[(12 * 240 + 100) * 3] ^= 1  # Alter an unsampled interior pixel only.
        with self.assertRaisesRegex(AssertionError, "Static source Z order"):
            verify_native_layer_original_pixels((frame, frame, bytes(changed)))
        with self.assertRaisesRegex(AssertionError, "byte dimensions"):
            verify_native_layer_original_pixels((frame, frame[:-3], frame))
        with self.assertRaisesRegex(AssertionError, "byte dimensions"):
            verify_native_layer_original_pixels((frame, frame))


if __name__ == "__main__":
    unittest.main()
