"""Causal oracle regressions on explicit unencoded PCM fixtures; no Native credit."""
from __future__ import annotations

import io
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_saved_mix_av as saved


def fixture_files() -> dict[str, bytes]:
    with tempfile.TemporaryDirectory(prefix="mw-saved-mix-oracle-") as raw:
        root = Path(raw)
        saved.write_stems(root)
        return {path.name: path.read_bytes() for path in root.iterdir()}


def mix_fixture(files: dict[str, bytes], *, omit: str | None = None, unducked: bool = False) -> bytes:
    """Independent test carrier: 0.5s activity, 50ms attack and 100ms release."""
    buses = {bus: list(struct.iter_unpack("<hh", saved.wav_pcm(files[f"saved-mix-{bus}.wav"])))
             for bus in saved.BUSES}
    out = bytearray()
    floor = 10 ** (-14 / 20)
    for i in range(saved.FRAMES):
        envelope = (1 if i < 24000 or i >= 52800 or unducked
                    else 1 + (floor - 1) * (i - 24000) / 2400 if i < 26400
                    else floor if i < 48000
                    else floor + (1 - floor) * (i - 48000) / 4800)
        for channel in range(2):
            value = sum(buses[bus][i][channel] * gain for bus, gain in (
                ("voice", 1), ("music", 10 ** (-12 / 20) * envelope), ("sfx", 10 ** (-8 / 20))
            ) if bus != omit)
            quantized = int(math.copysign(math.floor(abs(value) + 0.5), value))
            out.extend(struct.pack("<h", quantized))
    blob = io.BytesIO()
    with wave.open(blob, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(saved.RATE)
        wav.writeframes(out)
    return blob.getvalue()


class SavedMixOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = fixture_files()
        cls.files["saved-mix.wav"] = mix_fixture(cls.files)

    def test_full_pcm_equation_covers_both_channels_and_all_original_buses(self):
        report = saved.inspect_pcm_files(self.files)
        self.assertEqual(report["compared_channel_samples"], 192000)
        self.assertLessEqual(report["maximum_equation_error_lsb"], 1)
        self.assertEqual(set(report["source_wav_sha256"]), {"voice", "music", "sfx"})
        # Unencoded PCM only establishes oracle behavior, never an AAC/render PASS.
        measured = saved.inspect_aac(saved.wav_pcm(self.files["saved-mix.wav"]), self.files["saved-mix.wav"])
        self.assertTrue(measured["three_buses_decoded"])
        self.assertFalse(measured["aac_sample_exact_sync_claimed"])

    def test_music_or_sfx_omission_fails_even_when_voice_remains_audible(self):
        for omitted in ("music", "sfx"):
            with self.subTest(omitted=omitted):
                corrupted = dict(self.files)
                corrupted["saved-mix.wav"] = mix_fixture(corrupted, omit=omitted)
                with self.assertRaises(saved.MediaProbeError):
                    saved.inspect_pcm_files(corrupted)
                with self.assertRaises(saved.MediaProbeError):
                    saved.inspect_aac(saved.wav_pcm(corrupted["saved-mix.wav"]), self.files["saved-mix.wav"])

    def test_missing_ducking_is_rejected_in_decoded_audio(self):
        unducked = mix_fixture(self.files, unducked=True)
        with self.assertRaisesRegex(saved.MediaProbeError, "saved duck"):
            saved.inspect_aac(saved.wav_pcm(unducked), self.files["saved-mix.wav"])

    def test_channel_swap_and_wrong_clock_fail(self):
        pcm = saved.wav_pcm(self.files["saved-mix.wav"])
        swapped = b"".join(struct.pack("<hh", right, left) for left, right in struct.iter_unpack("<hh", pcm))
        for invalid in (swapped, pcm[:4000]):
            with self.subTest(bytes=len(invalid)):
                with self.assertRaises(saved.MediaProbeError):
                    saved.inspect_aac(invalid, self.files["saved-mix.wav"])

    def test_relabelled_or_replaced_source_wav_cannot_pass_the_source_oracle(self):
        files = dict(self.files)
        files["saved-mix-sfx.wav"] = files["saved-mix-music.wav"]
        files["saved-mix.wav"] = mix_fixture(files)
        with self.assertRaises(saved.MediaProbeError):
            saved.inspect_pcm_files(files)

    def test_generator_never_overwrites_an_existing_original(self):
        with tempfile.TemporaryDirectory(prefix="mw-saved-mix-existing-") as raw:
            root = Path(raw)
            original = root / "saved-mix-voice.wav"
            original.write_bytes(b"prior original")
            with self.assertRaises(FileExistsError):
                saved.write_stems(root)
            self.assertEqual(original.read_bytes(), b"prior original")


if __name__ == "__main__":
    unittest.main()
