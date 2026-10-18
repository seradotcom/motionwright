"""Red/green fixture-specific stereo sound tail conformance, no decoder execution."""
from __future__ import annotations
import io
import pathlib
import struct
import sys
import unittest
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from native_mlt_ci_pcm_envelope import (
    SOURCE_FRAMES, SOURCE_PCM_BYTES, inspect_fixed_ci_stereo_envelope,
)
from native_mlt_sequence_e2e import inspect_decoded_ci_stereo


def original_pcm() -> bytes:
    return b"".join(
        struct.pack(
            "<hh", ((frame % 120) - 60) * 250, ((frame % 80) - 40) * 260,
        ) for frame in range(SOURCE_FRAMES)
    )


def source_wave(raw: bytes, channels: int = 2) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(raw)
    return stream.getvalue()


def zero_right(raw: bytes, start: int, stop: int) -> bytes:
    altered = bytearray(raw)
    for frame in range(start, stop):
        struct.pack_into("<h", altered, frame * 4 + 2, 0)
    return bytes(altered)


class CiStereoFullSourceEnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = original_pcm()
        cls.wav = source_wave(cls.raw)
        assert len(cls.raw) == SOURCE_PCM_BYTES

    def test_entire_clean_source_passes_without_native_acceptance(self):
        result = inspect_fixed_ci_stereo_envelope(self.wav, self.raw)
        self.assertEqual(result["decoded_frames"], SOURCE_FRAMES)
        self.assertEqual(result["source_windows_checked_per_channel"], 825)
        self.assertEqual(result["minimum_ratio_by_channel"], [1.0, 1.0])
        self.assertEqual(result["maximum_ratio_by_channel"], [1.0, 1.0])
        self.assertFalse(result["media_packet_priming_and_skip_verified"])
        self.assertFalse(result["native_provider_receipt_verified"])
        self.assertFalse(result["human_listening_approved"])

    def test_old_oracle_false_green_for_muted_right_tail_and_448_padding(self):
        # Physical original AAC evidence has last nonzero right frame 52543,
        # 704 decoded silent right frames and 448 decoded frames beyond source.
        # This generated fixture mirrors the failure *shape*, not real media.
        truncated = zero_right(self.raw, SOURCE_FRAMES - 256, SOURCE_FRAMES)
        # Never call the source generator/render: suffix only extends synthetic
        # PCM after the source clock to show the old padding admission defect.
        padded = truncated + b"".join(
            struct.pack("<hh", ((i % 120) - 60) * 250, 0)
            for i in range(SOURCE_FRAMES, SOURCE_FRAMES + 448)
        )
        legacy = inspect_decoded_ci_stereo(padded)
        self.assertEqual(legacy["decoded_master_pcm_frames"], 53248)
        self.assertEqual(
            legacy["decoded_stereo_content"], "distinct_source_400hz_left_600hz_right"
        )
        with self.assertRaisesRegex(
            AssertionError, r"ci_source_channel_envelope_lost:right:52544:52608"
        ):
            inspect_fixed_ci_stereo_envelope(self.wav, padded)

    def test_just_last_64_right_samples_cannot_be_missed(self):
        short_tail = zero_right(self.raw, SOURCE_FRAMES - 64, SOURCE_FRAMES)
        inspect_decoded_ci_stereo(short_tail)  # Old gate incorrectly passes.
        with self.assertRaisesRegex(AssertionError, "right:52736:52800"):
            inspect_fixed_ci_stereo_envelope(self.wav, short_tail)

    def test_middle_silent_source_window_is_not_hidden_by_global_frequency(self):
        middle = zero_right(self.raw, 30_016, 30_080)
        inspect_decoded_ci_stereo(middle)
        with self.assertRaisesRegex(AssertionError, "right:30016:30080"):
            inspect_fixed_ci_stereo_envelope(self.wav, middle)

    def test_no_right_channel_at_all_is_rejected(self):
        with self.assertRaises(AssertionError):
            inspect_fixed_ci_stereo_envelope(
                self.wav, zero_right(self.raw, 0, SOURCE_FRAMES)
            )

    def test_extra_aac_padding_is_not_claimed_as_verified_packet_timing(self):
        # A known waveform followed by 448 encoded tail samples can pass only
        # SOURCE ENVELOPE, not native AAC priming/discard/final PTS admission.
        extra = self.raw + bytes(448 * 4)
        seen = inspect_fixed_ci_stereo_envelope(self.wav, extra)
        self.assertEqual(seen["unexplained_aac_extra_frames"], 448)
        self.assertFalse(seen["media_packet_priming_and_skip_verified"])

    def test_rehashed_or_altered_wav_source_does_not_become_authority(self):
        changed = bytearray(self.raw)
        changed[-1] ^= 1
        with self.assertRaisesRegex(AssertionError, "declared source fixture"):
            inspect_fixed_ci_stereo_envelope(source_wave(bytes(changed)), self.raw)

    def test_mono_wrong_clock_and_truncated_payload_are_rejected(self):
        with self.assertRaisesRegex(AssertionError, "media profile changed"):
            inspect_fixed_ci_stereo_envelope(source_wave(self.raw, channels=1), self.raw)
        self.assertRaises(
            AssertionError, inspect_fixed_ci_stereo_envelope, self.wav, self.raw[:-1]
        )
        with self.assertRaisesRegex(AssertionError, "frame count"):
            inspect_fixed_ci_stereo_envelope(
                self.wav, self.raw + bytes(1500 * 4)
            )

    def test_swapped_channels_do_not_pass_existing_frequency_gate(self):
        swap = b"".join(
            struct.pack("<hh", r, l)
            for l, r in struct.iter_unpack("<hh", self.raw)
        )
        with self.assertRaisesRegex(AssertionError, "lost distinct authored stereo"):
            inspect_fixed_ci_stereo_envelope(self.wav, swap)


if __name__ == "__main__":
    unittest.main()
