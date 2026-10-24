"""Original 33-frame AAC source-tail tests, no native dispatch or private media.

Generates exactly the historical original PCM16 WAV bytes from public Rust
native-mlt-sequence-e2e::write_synthetic_stereo_wav. The WAV SHA must match
GH39054798740's original. All tests run cross-platform, with zero skips.

These synthetic controls are NOT a new native media PASS. Protected original
RED PCM and independent direct FFmpeg control are exercised separately via
the A4 read-only private custody harness.
"""
from __future__ import annotations

import hashlib
import io
import json
import struct
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_stereo_tail_g2 as oracle


def identical_first_party_wav() -> bytes:
    """Exact Rust 48k PCM16 33-frame sawtooth source; no pasted private file."""
    pcm = bytearray()
    for frame in range(oracle.ORIGINAL_PCM_FRAMES):
        left = ((frame % 120) - 60) * 250
        right = ((frame % 80) - 40) * 260
        pcm.extend(struct.pack("<hh", left, right))
    result = io.BytesIO()
    with wave.open(result, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(48_000)
        out.writeframes(pcm)
    return result.getvalue()


def original_source() -> tuple[bytes, bytes]:
    wave_bytes = identical_first_party_wav()
    with wave.open(io.BytesIO(wave_bytes), "rb") as source:
        pcm = source.readframes(source.getnframes())
    return wave_bytes, pcm


def synthetic_decoded(*, drop_right_from: int | None = None,
                      swapped: bool = False, mute_left: bool = False,
                      short: bool = False) -> tuple[bytes, bytes]:
    wav, original = original_source()
    values = list(struct.iter_unpack("<hh", original))
    out = bytearray()
    for frame, (left, right) in enumerate(values):
        if drop_right_from is not None and frame >= drop_right_from:
            right = 0
        if swapped:
            left, right = right, left
        if mute_left:
            left = 0
        out.extend(struct.pack("<hh", left, right))
    out.extend(bytes(448 * 4))
    if short:
        out = out[:-4000]
    return wav, bytes(out)


def physical_streams() -> dict:
    return {
        "streams": [
            {"codec_type": "video", "codec_name": "h264",
             "width": 1280, "height": 720, "r_frame_rate": "30/1",
             "nb_read_frames": "33"},
            {"codec_type": "audio", "codec_name": "aac",
             "sample_rate": "48000", "channels": 2},
        ],
    }


class OriginalMltAacTailOracleContracts(unittest.TestCase):
    def test_public_firstparty_rust_fixture_reconstructs_original_wav_byte_exactly(self):
        wav, pcm = original_source()
        self.assertEqual(len(wav), 211244)
        self.assertEqual(len(pcm), 52800 * 4)
        self.assertEqual(hashlib.sha256(wav).hexdigest(),
                         oracle.ORIGINAL_SOURCE_SHA256)
        frames = oracle.source_frames(wav)
        self.assertEqual(frames[0], (-15000, -10400))
        self.assertEqual(frames[-1], (14750, 10140))
        self.assertEqual(len(frames), 52800)

    def test_exact_source_stereo_content_plus_aac_padding_passes_content_only(self):
        wav, decoded = synthetic_decoded()
        report = oracle.analyze_original_AAC(wav, decoded)
        self.assertEqual(report["independent_source_content_verdict"], "PASS_CONTENT")
        self.assertEqual(report["decoded_AAC_extra_padding_frames"], 448)
        self.assertTrue(report["AAC_padding_not_counted_as_authored_source"])
        self.assertFalse(report["original_Broker_provider_loaded_module_verified_here"])
        self.assertFalse(report["human_listening_acceptance"])

    def test_known_historical_red_mlt_pcm_zeroes_last_256_right(self):
        wav, decoded = synthetic_decoded(drop_right_from=52544)
        report = oracle.analyze_original_AAC(wav, decoded)
        self.assertEqual(report["independent_source_content_verdict"], "RED")
        self.assertIn("right_terminal256_source_PCM_lost_or_misaligned",
                      report["reasons"])
        self.assertIn("right_terminal_256_original_PCM_muted", report["reasons"])
        window = report["observed_PCM_windows"]["authored_terminal_256"]
        self.assertGreaterEqual(window["right"]["source_nonzero_frames"], 250)
        self.assertEqual(window["right"]["decoded_nonzero_frames"], 0)
        self.assertEqual(window["right"]["energy_ratio"], 0)

    def test_late_right_mute_is_rejected_even_if_early_stereo_passed(self):
        wav, decoded = synthetic_decoded(drop_right_from=52352)
        result = oracle.analyze_original_AAC(wav, decoded)
        self.assertEqual(result["independent_source_content_verdict"], "RED")
        self.assertIn("right_pre_tail_source_PCM_lost_or_misaligned", result["reasons"])
        self.assertNotIn("original_right_content_not_retained", result["reasons"])

    def test_swapped_channels_cannot_fake_right_tail_amplitude(self):
        wav, decoded = synthetic_decoded(swapped=True)
        result = oracle.analyze_original_AAC(wav, decoded)
        self.assertEqual(result["independent_source_content_verdict"], "RED")
        self.assertIn("original_right_content_not_retained", result["reasons"])

    def test_muted_left_is_not_a_stereo_content_pass(self):
        wav, decoded = synthetic_decoded(mute_left=True)
        result = oracle.analyze_original_AAC(wav, decoded)
        self.assertEqual(result["independent_source_content_verdict"], "RED")
        self.assertIn("original_left_content_not_retained", result["reasons"])

    def test_only_last_64_right_source_samples_lost_is_still_red(self):
        wav, decoded = synthetic_decoded(drop_right_from=52_736)
        report = oracle.analyze_original_AAC(wav, decoded)
        self.assertEqual(report["independent_source_content_verdict"], "RED")
        self.assertIn("right_terminal64_source_PCM_lost_or_misaligned",
                      report["reasons"])
        self.assertGreater(
            report["observed_PCM_windows"]["authored_terminal_256"]["right"]["energy_ratio"],
            0.55,
            "last-64 gate must detect loss not caught by coarse 256-frame energy",
        )

    def test_g2_cli_observes_source_bound_whole_pcm_without_native_dispatch(self):
        wav, decoded = synthetic_decoded()
        fake_mp4 = b"source-bound-original-owner-media-not-a-real-mp4"
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            src = root / "original.wav"
            media = root / "original.mp4"
            src.write_bytes(wav)
            media.write_bytes(fake_mp4)
            profile = json.dumps(physical_streams()).encode("utf-8")
            responses = [
                subprocess.CompletedProcess(["ffprobe"], 0, stdout=profile, stderr=b""),
                subprocess.CompletedProcess(["ffmpeg"], 0, stdout=decoded, stderr=b""),
            ]
            with mock.patch.object(oracle.subprocess, "run",
                                   side_effect=responses) as process:
                report = oracle.observe_original_master(
                    src, oracle.ORIGINAL_SOURCE_SHA256, media,
                    hashlib.sha256(fake_mp4).hexdigest(), "ffmpeg", "ffprobe",
                )
            self.assertEqual(report["independent_source_content_verdict"],
                             "PASS_CONTENT")
            self.assertEqual(process.call_count, 2)
            commands = [call.args[0] for call in process.call_args_list]
            self.assertEqual(commands[0][0], "ffprobe")
            self.assertIn("-count_frames", commands[0])
            self.assertEqual(commands[1][0], "ffmpeg")
            self.assertIn("0:a:0", commands[1])
            self.assertEqual(commands[1][-2:], ["s16le", "pipe:1"])
            self.assertFalse(report["original_master_provider_mutation_issued"])

    def test_unbound_master_sha_rejected_before_ffmpeg_is_called(self):
        wav, _ = synthetic_decoded()
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            src = root / "original.wav"
            media = root / "original.mp4"
            src.write_bytes(wav)
            media.write_bytes(b"not-the-reported-owner-media")
            with mock.patch.object(oracle.subprocess, "run") as decoder:
                with self.assertRaisesRegex(oracle.SourceMediaUnverified,
                                            "SHA256_changed"):
                    oracle.observe_original_master(
                        src, oracle.ORIGINAL_SOURCE_SHA256,
                        media, "ab" * 32, "ffmpeg", "ffprobe",
                    )
                decoder.assert_not_called()

    def test_short_decoded_master_not_relabelled_aac_padding(self):
        wav, decoded = synthetic_decoded(short=True)
        with self.assertRaisesRegex(oracle.SourceMediaUnverified, "clock_invalid"):
            oracle.analyze_original_AAC(wav, decoded)

    def test_beyond_single_aac_unit_does_not_pass_even_with_good_tail(self):
        wav, decoded = synthetic_decoded()
        with self.assertRaisesRegex(oracle.SourceMediaUnverified, "clock_invalid"):
            oracle.analyze_original_AAC(wav, decoded + bytes(4096))

    def test_mutated_firstparty_source_wav_cannot_become_reference(self):
        wav, decoded = synthetic_decoded()
        altered = bytearray(wav)
        altered[-1] ^= 1
        with self.assertRaisesRegex(oracle.SourceMediaUnverified, "SHA_or_bytes"):
            oracle.analyze_original_AAC(bytes(altered), decoded)

    def test_original_aac_whole_mp4_codec_inventory_is_not_only_headers(self):
        actual = oracle.verify_mp4_profile(physical_streams())
        self.assertEqual(actual["real_video_frames"], 33)
        self.assertEqual(actual["audio_channels"], 2)

    def test_wrong_native_mp4_frame_count_or_audio_format_is_rejected(self):
        for target, field, value in (
            ("video", "nb_read_frames", "32"),
            ("audio", "channels", 1),
            ("audio", "codec_name", "mp3"),
            ("video", "r_frame_rate", "30000/1001"),
        ):
            streams = physical_streams()
            selected = 0 if target == "video" else 1
            streams["streams"][selected][field] = value
            with self.subTest(target=target, field=field):
                with self.assertRaises(oracle.SourceMediaUnverified):
                    oracle.verify_mp4_profile(streams)

    def test_immutable_source_expected_sha_and_plain_file_path(self):
        wav, _ = synthetic_decoded()
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "firstparty-original.wav"
            path.write_bytes(wav)
            self.assertEqual(
                oracle.checked(path, oracle.ORIGINAL_SOURCE_SHA256,
                               oracle.ORIGINAL_SOURCE_WAV_BYTES),
                wav,
            )
            path.write_bytes(wav + b"\0")
            with self.assertRaises(oracle.SourceMediaUnverified):
                oracle.checked(path, oracle.ORIGINAL_SOURCE_SHA256,
                               oracle.ORIGINAL_SOURCE_WAV_BYTES)


if __name__ == "__main__":
    unittest.main()
