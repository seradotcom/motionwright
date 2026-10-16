"""FFV1-to-H.264 caption pixel tests: no synthetic bytes imply real media approval."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_mlt_caption_pixels import (
    _caption_mask, CaptionedMasterPixelsNotVerified, prove_native_captioned_master,
)

WIDTH = 50
HEIGHT = 20
ROI = (10, 5, 40, 18)


def samples():
    previous = bytearray(b"\xff" * (WIDTH * HEIGHT * 3))
    active = previous[:]
    master = bytearray(b"\xfd" * (WIDTH * HEIGHT * 3))
    for y in range(8, 16):
        for x in range(16, 28):
            start = (y * WIDTH + x) * 3
            active[start:start + 3] = b"\x00" * 3
            master[start:start + 3] = b"\x04" * 3
    return bytes(previous), bytes(active), bytes(master)


class NativeMltCaptionMaskTests(unittest.TestCase):
    def test_real_source_difference_and_lossy_master_preservation(self):
        previous, active, master = samples()
        observed = _caption_mask(
            previous, active, master,
            width=WIDTH, height=HEIGHT, roi=ROI, minimum=60,
        )
        self.assertEqual(observed["native_caption_mask_pixels"], 96)
        self.assertLess(observed["masked_lossy_master_error_mean"], 5)
        self.assertGreater(observed["masked_inactive_reference_error_mean"], 200)

    def test_final_master_without_actual_glyphs_fails(self):
        previous, active, _ = samples()
        with self.assertRaises(CaptionedMasterPixelsNotVerified):
            _caption_mask(
                previous, active, previous,
                width=WIDTH, height=HEIGHT, roi=ROI, minimum=60,
            )

    def test_native_source_that_never_drew_caption_fails(self):
        previous, _, master = samples()
        with self.assertRaises(CaptionedMasterPixelsNotVerified):
            _caption_mask(
                previous, previous, master,
                width=WIDTH, height=HEIGHT, roi=ROI, minimum=60,
            )

    def test_degenerate_incomplete_and_wrong_region_fail(self):
        previous, active, master = samples()
        for a, b, c, roi in [
            (previous[:-1], active, master, ROI),
            (previous, active, master[:-1], ROI),
            (previous, active, master, (0, 0, WIDTH + 1, HEIGHT)),
            (previous, active, master, (0, 0, 0, HEIGHT)),
        ]:
            with self.subTest(roi=roi, count=len(a)):
                with self.assertRaises(CaptionedMasterPixelsNotVerified):
                    _caption_mask(a, b, c, width=WIDTH, height=HEIGHT, roi=roi, minimum=60)

    def test_missing_original_and_untrusted_source_identity_cannot_pass(self):
        with self.assertRaises(CaptionedMasterPixelsNotVerified):
            prove_native_captioned_master(
                Path("not-real.mkv"), Path("not-real.mp4"), Path("ffmpeg"),
                source_sha="unverified", semwright_sha="b" * 40,
                selected_cues=2,
            )
        with self.assertRaises(CaptionedMasterPixelsNotVerified):
            prove_native_captioned_master(
                Path("not-real.mkv"), Path("not-real.mp4"), Path("ffmpeg"),
                source_sha="a" * 40, semwright_sha="b" * 40,
                selected_cues=0,
            )


if __name__ == "__main__":
    unittest.main()
