"""Synthetic adversarial controls only; never a provider render or G2 approval."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from native_render_e2e import verify_source_clip_frames

WIDTH=360
HEIGHT=220
FILL=(224,169,64)
BACK=(12,18,24)

def sample(*, clip=True, missing=False, color=FILL):
    pixels=bytearray(bytes(BACK)*WIDTH*HEIGHT)
    if not missing:
        last=180 if clip else WIDTH
        for y in range(HEIGHT):
            start=y*WIDTH*3
            pixels[start:start+last*3]=bytes(color)*last
    return bytes(pixels)

class SourceBoundClipPixelControls(unittest.TestCase):
    def test_exact_clip_pixels_preserve_inside_and_hide_child_outside(self):
        image=sample()
        proof=verify_source_clip_frames((image,image,image))
        self.assertEqual(proof["status"],"ORIGINAL_NATIVE_RECTANGULAR_CLIP_PIXELS_PASS")
        self.assertEqual([row["frame"] for row in proof["frames"]],[0,30,59])
        self.assertEqual(proof["parent_right_px"],1280)
        self.assertFalse(proof["native_effect_invoked_by_validator"])
        self.assertFalse(proof["human_visual_acceptance"])
    def test_unclipped_child_or_hidden_source_fails(self):
        for pixels, message in (
            (sample(clip=False),"failed to hide"),
            (sample(missing=True),"missing inside"),
            (sample(color=(224,64,169)),"missing inside"),
        ):
            with self.subTest(message=message),self.assertRaisesRegex(AssertionError,message):
                verify_source_clip_frames((pixels,pixels,pixels))
    def test_frame_drift_wrong_roster_and_short_rgb24_fail(self):
        image=sample()
        wrong=sample(clip=False)
        with self.assertRaisesRegex(AssertionError,"failed to hide"):
            verify_source_clip_frames((image,wrong,image))
        with self.assertRaisesRegex(AssertionError,"dimensions"):
            verify_source_clip_frames((image,image[:-3],image))
        with self.assertRaisesRegex(AssertionError,"dimensions"):
            verify_source_clip_frames((image,image))
        with self.assertRaisesRegex(AssertionError,"dimensions"):
            verify_source_clip_frames((b"",image,image))

if __name__=="__main__":
    unittest.main()
