"""Negative regressions for full native master PCM and timestamp readback.

The generated signals exercise the reader only. They are not native render,
speech, licensed-media, listening or editable-project acceptance evidence.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
from native_mlt_mix_readback import (
    ContentLimits, MediaClock, ReadbackError, bounded_decoder, compare_pcm,
    read_reference_wav, validate_clock,
)

SAMPLES = 52_800


def fixture() -> tuple[bytes, dict[str, bytes]]:
    buses = {"voice": bytearray(), "music": bytearray(), "sfx": bytearray()}
    mixed = bytearray()
    for index in range(SAMPLES):
        values = []
        for channel in range(2):
            phase = 2 * math.pi * index / 48_000
            music = round(4200 * math.sin(phase * (317 + 142 * channel)
                                         + 0.000000025 * index * index))
            voice = round(6800 * math.sin(phase * (863 + 190 * channel))) \
                if 9601 <= index < 10801 or 24000 <= index < 33600 else 0
            sfx = round(7200 * math.sin(phase * (1361 + 302 * channel)
                                      + 0.00000009 * index * index)) \
                if 50_400 <= index < SAMPLES else 0
            for name, value in (("voice", voice), ("music", music), ("sfx", sfx)):
                buses[name].extend(struct.pack("<h", value))
            values.append(music + voice + sfx)
        mixed.extend(struct.pack("<hh", *values))
    return bytes(mixed), {name: bytes(data) for name, data in buses.items()}


def without_bus(reference: bytes, bus: bytes) -> bytes:
    original = struct.unpack("<" + "h" * (len(reference) // 2), reference)
    removed = struct.unpack("<" + "h" * (len(bus) // 2), bus)
    return struct.pack("<" + "h" * len(original),
                       *(a - b for a, b in zip(original, removed, strict=True)))


def valid_probe() -> dict:
    return {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1280,
             "height": 720, "r_frame_rate": "30/1", "time_base": "1/15360",
             "start_pts": 0, "duration_ts": 16_896, "nb_read_frames": "33"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
             "channels": 2, "time_base": "1/48000", "start_pts": 0,
             "duration_ts": SAMPLES},
        ],
        "frames": [
            {"media_type": "video", "stream_index": 0, "pts": index * 512,
             "best_effort_timestamp": index * 512}
            for index in range(33)
        ] + [
            {"media_type": "audio", "stream_index": 1, "pts": index * 1024,
             "nb_samples": 1024}
            for index in range(52)
        ],
    }


class FullContentReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference, cls.buses = fixture()

    def test_all_samples_and_tail_are_compared_without_shifting(self):
        result = compare_pcm(self.reference, self.reference)
        self.assertTrue(result["all_logical_samples_compared"])
        self.assertEqual(result["logical_interval"], [0, SAMPLES])
        self.assertEqual(result["decoded_access_unit_suffix_samples"], 0)
        self.assertTrue(all(item["best_lag_samples"] == 0 for item in result["alignment"]))
        for channel in range(2):
            windows = [item for item in result["windows"] if item["channel"] == channel]
            self.assertEqual(windows[0]["start_sample"], 0)
            self.assertEqual(windows[-1]["end_exclusive"], SAMPLES)
            self.assertTrue(all(a["end_exclusive"] >= b["start_sample"]
                                for a, b in zip(windows, windows[1:])))

    def test_codec_sized_suffix_is_disclosed_without_claiming_extra_content(self):
        result = compare_pcm(self.reference, self.reference + b"\0" * (448 * 4))
        self.assertEqual(result["decoded_sample_frames"], 53_248)
        self.assertEqual(result["decoded_access_unit_suffix_samples"], 448)
        self.assertEqual(result["logical_interval"], [0, SAMPLES])

    def test_central_tone_gate_misses_a_tail_that_the_full_reader_rejects(self):
        spec = importlib.util.spec_from_file_location(
            "native_sequence_tail_regression", ROOT / "tooling/native_mlt_sequence_e2e.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tones = bytearray()
        for sample in range(SAMPLES):
            tones.extend(struct.pack(
                "<hh", round(5000 * math.sin(2 * math.pi * 400 * sample / 48_000)),
                round(5000 * math.sin(2 * math.pi * 600 * sample / 48_000)),
            ))
        corrupted = bytes(tones[:48_000 * 4]) + bytes(4800 * 4)
        self.assertEqual(module.inspect_decoded_ci_stereo(corrupted)
                         ["decoded_master_pcm_frames"], SAMPLES)
        with self.assertRaises(ReadbackError):
            compare_pcm(bytes(tones), corrupted)

    def test_shifts_from_one_sample_to_one_video_frame_fail(self):
        for shift in (1, -1, 512, -512, 1024, 1600):
            with self.subTest(shift=shift):
                size = abs(shift) * 4
                shifted = (bytes(size) + self.reference[:-size]) if shift > 0 else \
                    (self.reference[size:] + bytes(size))
                with self.assertRaises(ReadbackError):
                    compare_pcm(self.reference, shifted)

    def test_each_missing_bus_fails(self):
        for name, bus in self.buses.items():
            with self.subTest(bus=name), self.assertRaises(ReadbackError):
                compare_pcm(self.reference, without_bus(self.reference, bus))

    def test_cut_and_last_100_milliseconds_cannot_disappear(self):
        for start in (48_000, 51_200):
            with self.subTest(start=start), self.assertRaises(ReadbackError):
                compare_pcm(self.reference, self.reference[:start * 4]
                            + bytes((SAMPLES - start) * 4))

    def test_channel_swap_and_duplication_fail(self):
        swapped, duplicated = bytearray(), bytearray()
        for left, right in struct.iter_unpack("<hh", self.reference):
            swapped.extend(struct.pack("<hh", right, left))
            duplicated.extend(struct.pack("<hh", left, left))
        for data in (swapped, duplicated):
            with self.assertRaises(ReadbackError):
                compare_pcm(self.reference, bytes(data))

    def test_wrong_gain_fails(self):
        wrong = b"".join(struct.pack("<h", max(-32768, min(32767, value[0] * 2)))
                         for value in struct.iter_unpack("<h", self.reference))
        with self.assertRaises(ReadbackError):
            compare_pcm(self.reference, wrong)

    def test_missing_or_partial_logical_sample_and_excess_padding_fail(self):
        for data in (self.reference[:-4], self.reference[:-1],
                     self.reference + bytes(1025 * 4)):
            with self.assertRaises(ReadbackError):
                compare_pcm(self.reference, data)

    def test_silence_cannot_prove_alignment(self):
        with self.assertRaisesRegex(ReadbackError, "timing_signature"):
            compare_pcm(bytes(SAMPLES * 4), bytes(SAMPLES * 4))

    def test_fractional_project_sample_clock_fails(self):
        with self.assertRaisesRegex(ReadbackError, "fractional_audio_sample_clock"):
            _ = MediaClock(1, 1280, 720, 59, 1).samples

    def test_invalid_limits_cannot_disable_the_reader(self):
        for limits in (ContentLimits(minimum_correlation=float("nan")),
                       ContentLimits(block_samples=0),
                       ContentLimits(maximum_relative_error=100)):
            with self.assertRaises(ReadbackError):
                compare_pcm(self.reference, self.reference, limits)


class TimestampAndDecoderTests(unittest.TestCase):
    def test_exact_frame_clock_exposes_aac_suffix(self):
        result = validate_clock(valid_probe(), MediaClock(33, 1280, 720))
        self.assertEqual(result["decoded_audio_samples_from_frames"], 53_248)
        self.assertEqual(result["audio_duration_samples"], SAMPLES)
        self.assertTrue(result["exact_contiguous_frame_timestamps"])

    def test_nonzero_or_discontinuous_pts_fail_with_unchanged_pcm(self):
        for kind in ("audio_start", "video_start", "audio_gap", "video_gap"):
            data = valid_probe()
            if kind == "audio_start":
                data["streams"][1]["start_pts"] = 1
            elif kind == "video_start":
                data["streams"][0]["start_pts"] = 512
            elif kind == "audio_gap":
                data["frames"][34]["pts"] += 1
            else:
                data["frames"][15]["best_effort_timestamp"] += 512
            with self.subTest(kind=kind), self.assertRaises(ReadbackError):
                validate_clock(data, MediaClock(33, 1280, 720))

    def test_no_channel_or_rate_normalization_hides_a_wrong_stream(self):
        for field, value in (("sample_rate", "44100"), ("channels", 1),
                             ("codec_name", "mp3"), ("duration_ts", SAMPLES - 1)):
            data = valid_probe()
            data["streams"][1][field] = value
            with self.subTest(field=field), self.assertRaises(ReadbackError):
                validate_clock(data, MediaClock(33, 1280, 720))

    def test_metadata_frame_total_cannot_hide_a_missing_decoded_frame(self):
        data = valid_probe()
        del data["frames"][32]
        with self.assertRaises(ReadbackError):
            validate_clock(data, MediaClock(33, 1280, 720))

    def test_decoder_stderr_is_rejected_even_with_exit_zero(self):
        with self.assertRaisesRegex(ReadbackError, "media_errors"):
            bounded_decoder([sys.executable, "-c",
                             "import sys;sys.stderr.write('corrupt media')"], 1024)

    def test_decoder_output_is_bounded_while_reading(self):
        with self.assertRaisesRegex(ReadbackError, "output_limit"):
            bounded_decoder([sys.executable, "-c", "print('x'*100000)"], 1024)

    def test_decoder_timeout_kills_and_reaps(self):
        with self.assertRaisesRegex(ReadbackError, "timeout"):
            bounded_decoder([sys.executable, "-c", "import time;time.sleep(30)"],
                            1024, timeout=0.1)

    def test_reference_wav_is_read_physically_without_resampling(self):
        with tempfile.TemporaryDirectory(prefix="native-mix-readback-") as directory:
            path = Path(directory) / "source.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setparams((2, 2, 48_000, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(2048 * 4))
            self.assertEqual(len(read_reference_wav(path, 2048)), 2048 * 4)
            path.write_bytes(path.read_bytes()[:-4])
            with self.assertRaisesRegex(ReadbackError, "physically_truncated"):
                read_reference_wav(path, 2048)


if __name__ == "__main__":
    unittest.main()
