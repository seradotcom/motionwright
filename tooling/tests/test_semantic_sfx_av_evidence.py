"""Independent source-bound SFX timing checks with original numeric PCM."""
import math
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semantic_sfx_av_evidence import original_native_mix, inspect_decoded_sfx_audio, SOURCE_FRAMES


def tone(left, right):
    return [
        (round(12000 * math.sin(2 * math.pi * left * i / 48000)),
         round(12000 * math.sin(2 * math.pi * right * i / 48000)))
        for i in range(SOURCE_FRAMES)
    ]


class OriginalSfxTimingTests(unittest.TestCase):
    def test_original_source_alignment(self):
        mixed = original_native_mix(tone(440, 660), tone(740, 980))
        pcm = b"".join(struct.pack("<hh", l, r) for l, r in mixed)
        report = inspect_decoded_sfx_audio(pcm)
        self.assertEqual(report["expected_first_onset_sample"], 9600)
        self.assertLessEqual(report["absolute_first_onset_error_samples"], 1024)

    def test_silence_is_not_sound_evidence(self):
        with self.assertRaises(ValueError):
            inspect_decoded_sfx_audio(bytes(96000 * 4))

class RejectMismatchedSfxEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mixed = original_native_mix(tone(440, 660), tone(740, 980))

    def test_reversed_stereo_does_not_pass_audio_identity(self):
        flipped = b"".join(struct.pack("<hh", r, l) for l, r in self.mixed)
        with self.assertRaises(ValueError):
            inspect_decoded_sfx_audio(flipped)

    def test_delayed_audio_does_not_pass_onset_contract(self):
        samples = b"".join(struct.pack("<hh", l, r) for l, r in self.mixed)
        shift_bytes = 16000 * 4
        delayed = bytes(shift_bytes) + samples[:-shift_bytes]
        with self.assertRaises(ValueError):
            inspect_decoded_sfx_audio(delayed)

    def test_mix_is_silent_before_first_cue_and_after_release(self):
        self.assertTrue(all(l == 0 and r == 0 for l, r in self.mixed[:9600]))
        self.assertTrue(all(l == 0 and r == 0 for l, r in self.mixed[70400:]))
