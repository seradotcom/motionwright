#!/usr/bin/env python3
"""Adversarial unit tests for CI's independent decoded-native-media acceptance."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import wave


PATH = Path(__file__).resolve().parents[1] / "native_av_media_probe.py"
SPEC = importlib.util.spec_from_file_location("native_av_media_probe", PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def real_metadata() -> dict:
    return {
        "format": {"duration": "2.024"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30/1",
                "nb_read_frames": "60",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
    }


class NativeMediaProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.voice = Path(cls.temp.name) / "source-two-tones.wav"
        probe.write_two_channel_tone_wav(cls.voice)
        with wave.open(str(cls.voice), "rb") as wav:
            cls.pcm = wav.readframes(wav.getnframes())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_deterministic_source_contains_distinct_non_silent_channels(self):
        report = probe.inspect_decoded_audio(self.pcm)
        self.assertEqual(report["stereo_channel_identity"], "confirmed_distinct_source_tones")
        self.assertGreater(report["left_440_amplitude"], report["right_440_leakage"] * 5)
        self.assertGreater(report["right_660_amplitude"], report["left_660_leakage"] * 5)
        with wave.open(str(self.voice), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getnframes()),
                             (48_000, 2, 96_000))

    def test_six_second_hero_stereo_source_and_portrait_profile(self):
        six_seconds = Path(self.temp.name) / "hero-six-seconds.wav"
        probe.write_two_channel_tone_wav(six_seconds, seconds=6.0)
        with wave.open(str(six_seconds), "rb") as wav:
            self.assertEqual(wav.getnframes(), 288_000)
            measured = probe.inspect_decoded_audio(
                wav.readframes(wav.getnframes()), expected_seconds=6.0,
            )
        self.assertEqual(measured["stereo_channel_identity"], "confirmed_distinct_source_tones")
        metadata = real_metadata()
        metadata["format"]["duration"] = "6.024"
        metadata["streams"][0].update(
            width=1080, height=1920, nb_read_frames="180",
        )
        reviewed = probe.inspect_streams(
            metadata, expected_frames=180, expected_size=(1080, 1920),
        )
        self.assertEqual((reviewed["video_width"], reviewed["video_height"]), (1080, 1920))
        self.assertEqual(reviewed["video_frames_decoded"], 180)
        metadata["streams"][0]["nb_read_frames"] = "179"
        with self.assertRaisesRegex(probe.MediaProbeError, "exactly 180"):
            probe.inspect_streams(metadata, expected_frames=180, expected_size=(1080, 1920))
        with self.assertRaisesRegex(probe.MediaProbeError, "pinned CI fixtures"):
            probe.inspect_streams(metadata, expected_frames=179, expected_size=(1080, 1920))
        with self.assertRaisesRegex(probe.MediaProbeError, "duration"):
            probe.inspect_decoded_audio(self.pcm, expected_seconds=6.0)

    def test_swapped_channels_are_rejected(self):
        swapped = b"".join(struct.pack("<hh", r, l)
                           for l, r in struct.iter_unpack("<hh", self.pcm))
        with self.assertRaisesRegex(probe.MediaProbeError, "left channel lacks"):
            probe.inspect_decoded_audio(swapped)

    def test_identical_left_right_channels_cannot_pass_as_stereo(self):
        same = b"".join(struct.pack("<hh", l, l)
                        for l, _ in struct.iter_unpack("<hh", self.pcm))
        with self.assertRaisesRegex(probe.MediaProbeError, "right channel"):
            probe.inspect_decoded_audio(same)

    def test_silent_or_short_master_is_rejected(self):
        with self.assertRaisesRegex(probe.MediaProbeError, "lacks"):
            probe.inspect_decoded_audio(bytes(96_000 * 4))
        with self.assertRaisesRegex(probe.MediaProbeError, "duration"):
            probe.inspect_decoded_audio(self.pcm[:48_000 * 4])

    def test_misaligned_pcm_is_rejected(self):
        with self.assertRaisesRegex(probe.MediaProbeError, "partial sample"):
            probe.inspect_decoded_audio(self.pcm + b"1")

    def test_metadata_asserts_codec_frame_rate_duration_and_stereo(self):
        good = probe.inspect_streams(real_metadata())
        self.assertEqual((good["video_frames_decoded"], good["audio_sample_rate"]),
                         (60, 48_000))
        for field, value, expected in [
            ("codec_name", "hevc", "H.264"),
            ("width", 1280, "dimensions"),
            ("avg_frame_rate", "30000/1001", "frame rate"),
            ("nb_read_frames", "59", "exactly 60"),
        ]:
            invalid = real_metadata()
            invalid["streams"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(probe.MediaProbeError, expected):
                probe.inspect_streams(invalid)

    def test_audio_metadata_prevents_false_pcm_or_mono_claims(self):
        for field, value in [
            ("codec_name", "pcm_s16le"),
            ("sample_rate", "44100"),
            ("channels", 1),
        ]:
            invalid = real_metadata()
            invalid["streams"][1][field] = value
            with self.subTest(field=field), self.assertRaises(probe.MediaProbeError):
                probe.inspect_streams(invalid)

    def test_duration_and_extra_streams_are_rejected(self):
        invalid = real_metadata()
        invalid["format"]["duration"] = "2.45"
        with self.assertRaisesRegex(probe.MediaProbeError, "duration"):
            probe.inspect_streams(invalid)
        invalid = real_metadata()
        invalid["streams"].append(deepcopy(invalid["streams"][1]))
        with self.assertRaisesRegex(probe.MediaProbeError, "exactly one"):
            probe.inspect_streams(invalid)

    def test_moving_decoded_frame_pair_is_visible(self):
        size = probe.FRAME_WIDTH * probe.FRAME_HEIGHT * 3
        first = bytearray([15] * size)
        middle = bytearray(first)
        for position in range(0, 180):
            middle[1_000 + position] = 240
        report = probe.inspect_decoded_video(bytes(first + middle))
        self.assertTrue(report["video_movement_decoded"])
        self.assertGreaterEqual(report["changed_channel_bytes"], 85)
        self.assertNotEqual(report["first_frame_rgb_sha256"], report["middle_frame_rgb_sha256"])

    def test_explicit_static_graphic_requires_nonblank_stable_decoded_frames(self):
        size = probe.FRAME_WIDTH * probe.FRAME_HEIGHT * 3
        visible = bytearray([15] * size)
        for position in range(900, 2400):
            visible[position] = 200
        report = probe.inspect_decoded_video(bytes(visible + visible), expect_movement=False)
        self.assertFalse(report["video_movement_decoded"])
        self.assertTrue(report["video_stable_nonblank"])
        with self.assertRaisesRegex(probe.MediaProbeError, "monochromatic"):
            probe.inspect_decoded_video(bytes([15] * (2 * size)), expect_movement=False)
        changed = bytearray(visible)
        for position in range(5_000, 6_000):
            changed[position] = 240
        with self.assertRaisesRegex(probe.MediaProbeError, "unexpectedly changes"):
            probe.inspect_decoded_video(bytes(visible + changed), expect_movement=False)

    def test_static_or_corrupt_decoded_video_is_rejected(self):
        size = probe.FRAME_WIDTH * probe.FRAME_HEIGHT * 3
        static = bytes([15] * (2 * size))
        with self.assertRaisesRegex(probe.MediaProbeError, "no meaningful pixel movement"):
            probe.inspect_decoded_video(static)
        with self.assertRaisesRegex(probe.MediaProbeError, "requested frames"):
            probe.inspect_decoded_video(static[:-1])


if __name__ == "__main__":
    unittest.main()
