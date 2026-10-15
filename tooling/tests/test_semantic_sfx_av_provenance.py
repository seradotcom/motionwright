"""Adversarial source provenance tests for SFX -> native AV evidence."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semantic_sfx_av_evidence import (
    inspect_original_sources,
    MediaProbeError,
    SOURCE_FRAMES,
    MASTER_FRAMES,
)
from semantic_sfx_av_public_artifact import curate
from test_semantic_sfx_av_evidence import tone
from semantic_sfx_av_evidence import original_native_mix


def original_wav(path, values):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"".join(struct.pack("<hh", left, right)
                                 for left, right in values))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OriginalSfxSourceAdmission(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        a, b = tone(440, 660), tone(740, 980)
        mix = original_native_mix(a, b)
        original_wav(self.root / "original-focus-sfx.wav", a)
        original_wav(self.root / "original-confirm-sfx.wav", b)
        original_wav(self.root / "semantic-sfx-master.wav", mix)
        self.receipt = {
            "schema": "motionwright.semantic-sfx-av-prepared/1",
            "frames": 60,
            "sample_count": 96000,
            "fps": "30/1",
            "sample_rate": 48000,
            "channels": 2,
            "revision": 8,
            "first_source_sha256": sha(self.root / "original-focus-sfx.wav"),
            "second_source_sha256": sha(self.root / "original-confirm-sfx.wav"),
            "audio_sha256": sha(self.root / "semantic-sfx-master.wav"),
            "sample_peak_pcm16": max(max(abs(x), abs(y)) for x, y in mix),
            "scheduled_sfx": [
                {"absolute_start_frame": 6, "absolute_end_frame": 30,
                 "first_sample": 9600, "sample_count": 38400},
                {"absolute_start_frame": 20, "absolute_end_frame": 44,
                 "first_sample": 32000, "sample_count": 38400},
            ],
        }

    def tearDown(self):
        self.folder.cleanup()

    def test_native_mix_equals_the_two_original_imported_wavs(self):
        report = inspect_original_sources(self.root, self.receipt)
        self.assertEqual(report["max_sample_difference_pcm16"], 0)
        self.assertEqual(report["real_imported_original_stems"], 2)
        self.assertEqual(report["exact_sfx_sample_count"], MASTER_FRAMES)

    def test_tampered_source_or_schedule_never_becomes_verified(self):
        for field, value in (
            ("first_source_sha256", "0" * 64),
            ("second_source_sha256", "0" * 64),
            ("audio_sha256", "0" * 64),
            ("frames", 59),
            ("sample_rate", 44100),
            ("revision", 0),
        ):
            with self.subTest(field=field):
                candidate = dict(self.receipt)
                candidate[field] = value
                with self.assertRaises(MediaProbeError):
                    inspect_original_sources(self.root, candidate)
        moved = json.loads(json.dumps(self.receipt))
        moved["scheduled_sfx"][1]["first_sample"] = 31000
        with self.assertRaises(MediaProbeError):
            inspect_original_sources(self.root, moved)

    def test_curated_artifact_cannot_claim_pass_without_master(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "public"
            result = curate(self.root, destination, "a" * 40, True)
            self.assertEqual(result["technical_result"], "NOT_VERIFIED")
            self.assertEqual(result["native_sdk_mux"], "NOT_VERIFIED")
            self.assertFalse((destination / "motionwright-master.mp4").exists())

    def test_failed_native_job_never_publishes_candidate_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "public"
            result = curate(self.root, destination, "a" * 40, False)
            self.assertEqual(result["technical_result"], "NOT_VERIFIED")
            self.assertFalse(list(destination.glob("*.wav")))
