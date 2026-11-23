"""Small synthetic adversarial controls, never evidence of a Native render."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_composed_clip_pixels import WIDTH, HEIGHT, LEFT, TOP, FILL, verify_composed_clip_frames

BACKGROUND = (12, 18, 24)


def fixture(*, clip=True, rotation=True, resize=True, move=True, fade=True, curve="cubic"):
    result = []
    for frame in (0, 15, 30):
        t = frame / 30
        p = 1 - (1 - t) ** 3 if curve == "cubic" else t
        x, y = (1200 - 100 * p, 410 + 20 * p) if move else (1200, 410)
        width, height = (280 + 80 * p, 160 + 60 * p) if resize else (280, 160)
        angle = math.radians(-18 * (1 - p) if rotation else 0)
        cosine, sine = math.cos(angle), math.sin(angle)
        cx, cy = x + width / 2, y + height / 2
        alpha = 1 - t if fade else 1
        fill = bytes(round(a * alpha + b * (1 - alpha)) for a, b in zip(FILL, BACKGROUND))
        pixels = bytearray(bytes(BACKGROUND) * WIDTH * HEIGHT)
        for py in range(HEIGHT):
            for px in range(WIDTH):
                world_x, world_y = LEFT + px, TOP + py
                dx, dy = world_x - cx, world_y - cy
                lx = dx * cosine + dy * sine + width / 2
                ly = -dx * sine + dy * cosine + height / 2
                if 0 <= lx <= width and 0 <= ly <= height and (not clip or 640 <= world_x <= 1280 and 360 <= world_y <= 720):
                    offset = (py * WIDTH + px) * 3
                    pixels[offset:offset + 3] = fill
        result.append(bytes(pixels))
    return tuple(result)


class ComposedClipWitnesses(unittest.TestCase):
    def test_complete_composition_and_each_missing_property(self):
        proof = verify_composed_clip_frames(fixture())
        self.assertEqual(proof["status"], "ORIGINAL_NATIVE_COMPOSED_CLIP_POSE_PIXELS_PASS")
        self.assertFalse(proof["native_effect_invoked_by_validator"])
        for change in ({"clip": False}, {"rotation": False}, {"resize": False},
                       {"move": False}, {"fade": False}, {"curve": "linear"}):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                verify_composed_clip_frames(fixture(**change))

    def test_missing_or_substituted_frames_are_refused(self):
        original = fixture()
        for wrong in (original[:2], (original[0], original[1][:-3], original[2]),
                      (original[2], original[2], original[2]), (original[0],) * 3):
            with self.assertRaises(AssertionError):
                verify_composed_clip_frames(wrong)


if __name__ == "__main__":
    unittest.main()
