"""Pure adversarial gate for the exact native CI stereo content oracle.

The independent ffmpeg decoder runs only in the real Host E2E. This suite
tests the bounded decoded PCM evaluator without a browser or native process.
"""
import copy
import hashlib
import io
import os
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import patch
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from native_mlt_sequence_e2e import inspect_decoded_ci_stereo, retain_native_master_source
from native_mlt_sequence_e2e import digest as native_file_digest
import native_mlt_public_artifact as pub


def original_pcm() -> bytes:
    return b"".join(
        struct.pack(
            "<hh",
            ((index % 120) - 60) * 250,
            ((index % 80) - 40) * 260,
        )
        for index in range(52_800)
    )


class NativeAvStereoContentTests(unittest.TestCase):
    def test_distinct_measured_source_content_passes(self):
        result = inspect_decoded_ci_stereo(original_pcm())
        self.assertEqual(result["decoded_master_pcm_frames"], 52_800)
        self.assertEqual(
            result["decoded_stereo_content"],
            "distinct_source_400hz_left_600hz_right",
        )
        self.assertGreater(result["left_400_amplitude"], 0.08)
        self.assertGreater(result["right_600_amplitude"], 0.06)

    def test_no_sound_and_partial_frames_are_rejected(self):
        original = original_pcm()
        for altered in [
            bytes(len(original)),
            original[:-1],
            original[:12_000],
            original + bytes(4 * 1_500),
        ]:
            with self.subTest(size=len(altered)):
                with self.assertRaises(AssertionError):
                    inspect_decoded_ci_stereo(altered)

    def test_swapped_and_duplicated_stereo_are_rejected(self):
        original = original_pcm()
        swap = b"".join(
            struct.pack("<hh", right, left)
            for left, right in struct.iter_unpack("<hh", original)
        )
        duplicated_left = b"".join(
            struct.pack("<hh", left, left)
            for left, _ in struct.iter_unpack("<hh", original)
        )
        duplicated_right = b"".join(
            struct.pack("<hh", right, right)
            for _, right in struct.iter_unpack("<hh", original)
        )
        for altered in (swap, duplicated_left, duplicated_right):
            with self.subTest(variant=altered[:8]):
                with self.assertRaises(AssertionError):
                    inspect_decoded_ci_stereo(altered)


SOURCE_SHA = "66aeaa3aa6fff53834ab4003671ce79068bcbd6f631609f0f988b3ac9744ebe9"


def synthetic_voice_wav() -> bytes:
    # Independent standard-library WAV writer, fed by the existing PCM oracle.
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48_000)
        wav.writeframes(original_pcm())
    return output.getvalue()


def native_source_receipt(mode: str = "final-mp4") -> dict:
    return {
        "frame_count": 33,
        "evidence_scope": (pub.CAPTIONED_SCOPE if mode == "captioned-mp4"
                           else pub.FINAL_SCOPE),
        "staged_audio": {
            "relative_path": "mw-ci-source-" + SOURCE_SHA[:16] + ".wav",
            "sha256": SOURCE_SHA, "sample_rate": 48000, "channels": 2,
        },
        "measured_voice": {
            "audio_sha256": SOURCE_SHA, "audio_pcm_frames": 52800,
            "audio_size_bytes": 211244, "audio_sample_rate": 48000,
            "audio_channels": 2, "total_frames": 33, "fps_num": 30, "fps_den": 1,
            "caption_cue_count": 2 if mode == "captioned-mp4" else None,
        },
    }


class NativeSourceWavCustodyTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="mw-source-custody-")
        self.addCleanup(self.root.cleanup)
        self.native_temp = tempfile.TemporaryDirectory(prefix="native-", dir=self.root.name)
        self.addCleanup(self.native_temp.cleanup)
        self.native = pathlib.Path(self.native_temp.name) / "output"
        self.native.mkdir()
        self.evidence = pathlib.Path(self.root.name) / "retained"
        self.evidence.mkdir()
        self.receipt = native_source_receipt()
        self.source = self.native / self.receipt["staged_audio"]["relative_path"]
        self.data = synthetic_voice_wav()
        self.source.write_bytes(self.data)

    def retain(self, receipt=None):
        return retain_native_master_source(
            self.native, self.evidence,
            self.receipt if receipt is None else receipt, "final-mp4",
        )

    def test_staged_source_survives_the_native_temporary_directory_cleanup(self):
        self.assertEqual(len(self.data), 211244)
        self.assertEqual(hashlib.sha256(self.data).hexdigest(), SOURCE_SHA)
        proof = self.retain()
        self.native_temp.cleanup()
        self.assertFalse(self.source.exists())
        retained = self.evidence / "native-master-source.wav"
        self.assertEqual(retained.read_bytes(), self.data)
        self.assertEqual(proof["sha256"], SOURCE_SHA)
        self.assertEqual(proof["pcm_frames"], 52800)
        self.assertEqual(proof["source_kind"], "measured-voice")
        self.assertTrue(proof["synthetic_fixture_verified"])
        self.assertEqual(proof["full_master_reference_readback"], "NOT_RUN")
        self.assertNotIn(self.root.name, repr(proof))

    def test_foreign_staged_paths_fail_before_any_source_open(self):
        for name in ("../private.wav", "/private.wav", "C:\\private.wav",
                     "nested/source.wav", "other-source.wav"):
            with self.subTest(name=name):
                receipt = copy.deepcopy(self.receipt)
                receipt["staged_audio"]["relative_path"] = name
                with patch("native_mlt_sequence_e2e.os.open") as opened:
                    with self.assertRaisesRegex(
                        AssertionError, "native_master_source_retention_refused",
                    ):
                        self.retain(receipt)
                    opened.assert_not_called()
                self.assertFalse((self.evidence / "native-master-source.wav").exists())

    def test_staged_source_and_measured_voice_bindings_are_required(self):
        changes = (
            ("staged_audio", "sha256", "f" * 64),
            ("staged_audio", "sample_rate", 44100),
            ("staged_audio", "channels", 1),
            ("measured_voice", "audio_sha256", "e" * 64),
            ("measured_voice", "audio_pcm_frames", 96000),
            ("measured_voice", "audio_size_bytes", 384044),
            ("measured_voice", "total_frames", 60),
            ("measured_voice", "fps_num", 60),
        )
        for section, key, value in changes:
            with self.subTest(section=section, key=key):
                receipt = copy.deepcopy(self.receipt)
                receipt[section][key] = value
                with self.assertRaisesRegex(
                    AssertionError, "native_master_source_retention_refused",
                ):
                    self.retain(receipt)
                self.assertFalse((self.evidence / "native-master-source.wav").exists())

    def test_changed_truncated_or_extended_source_bytes_are_refused(self):
        changed_header = bytearray(self.data)
        struct.pack_into("<I", changed_header, 24, 44100)
        for data in (self.data[:-4], self.data + b"\0\0\0\0",
                     bytes(changed_header), self.data[:-4] + b"\0\0\0\0"):
            with self.subTest(size=len(data)):
                self.source.write_bytes(data)
                with self.assertRaisesRegex(
                    AssertionError, "native_master_source_retention_refused",
                ):
                    self.retain()
                self.assertFalse((self.evidence / "native-master-source.wav").exists())

    def test_existing_retained_evidence_is_never_overwritten(self):
        retained = self.evidence / "native-master-source.wav"
        retained.write_bytes(b"existing-review-draft")
        with self.assertRaisesRegex(AssertionError, "native_master_source_retention_refused"):
            self.retain()
        self.assertEqual(retained.read_bytes(), b"existing-review-draft")

    def test_real_symlink_is_not_an_admitted_staged_source(self):
        target = self.native / "actual-fixture.wav"
        self.source.replace(target)
        try:
            self.source.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("This host cannot create the real source symlink fixture")
        with self.assertRaisesRegex(AssertionError, "native_master_source_retention_refused"):
            self.retain()
        self.assertFalse((self.evidence / "native-master-source.wav").exists())

    def test_source_replacement_during_retention_is_detected(self):
        original_fsync = os.fsync

        def replace_source_after_copy(fd):
            original_fsync(fd)
            replacement = self.native / "replacement.wav"
            replacement.write_bytes(self.data[:-4] + b"\0\0\0\0")
            replacement.replace(self.source)

        with patch("native_mlt_sequence_e2e.os.fsync",
                   side_effect=replace_source_after_copy):
            with self.assertRaisesRegex(
                AssertionError, "native_master_source_retention_refused",
            ):
                self.retain()

    def test_per_mode_clock_does_not_admit_unimplemented_consumers(self):
        self.assertEqual(pub.mode_contract("final-mp4").pcm_frames, 52800)
        self.assertEqual(pub.mode_contract("captioned-mp4").video_frames, 33)
        proposed_clock = pub.MltModeContract(60, (32, 28))
        self.assertEqual(proposed_clock.pcm_frames, 96000)
        self.assertEqual(proposed_clock.wav_bytes, 384044)
        self.assertEqual(pub.MODES, {"pcm", "video-only", "final-mp4", "captioned-mp4"})
        for mode in ("original-mix-mp4", "original-sfx-mp4"):
            with self.subTest(mode=mode), self.assertRaises(pub.PublicationError):
                pub.mode_contract(mode)
        with self.assertRaises(ValueError):
            pub.MltModeContract(1, (1,), fps_num=30000, fps_den=1001)
        with self.assertRaises(AssertionError):
            inspect_decoded_ci_stereo(original_pcm(), mode="original-mix-mp4")


class _GrowingHashReader(io.BytesIO):
    """Never EOF, with an independent guard so a broken hash loop cannot hang."""

    def __init__(self):
        super().__init__()
        self.read_requests = []
        self.bytes_returned = 0
        self.iteration_guard_triggered = False

    def read(self, size=-1):
        self.read_requests.append(size)
        if len(self.read_requests) > 16:
            self.iteration_guard_triggered = True
            raise AssertionError("growing-reader guard reached; hashing did not stop")
        if size <= 0:
            raise AssertionError("a positive read size is required")
        chunk = b"x" * min(size, 3)
        self.bytes_returned += len(chunk)
        return chunk


class NativeFileHashReadBoundsTests(unittest.TestCase):
    @staticmethod
    def _path_with_stream(stream, initial_size):
        path = mock.Mock(spec=pathlib.Path)
        path.stat.return_value = mock.Mock(
            st_dev=1, st_ino=2, st_size=initial_size, st_mtime_ns=3,
        )
        path.open.return_value = stream
        return path

    def test_growing_file_rejected_after_initial_size_plus_one_bytes(self):
        initial_size = 7
        stream = _GrowingHashReader()
        path = self._path_with_stream(stream, initial_size)
        with self.assertRaisesRegex(ValueError, "^native_mlt_file_changed_during_hash$"):
            native_file_digest(path)
        self.assertEqual(stream.bytes_returned, initial_size + 1)
        self.assertFalse(stream.iteration_guard_triggered)
        self.assertTrue(stream.closed)
        self.assertEqual(path.stat.call_count, 1)

    def test_eof_must_match_initial_size(self):
        data = b"short"
        stream = io.BytesIO(data)
        path = self._path_with_stream(stream, len(data) + 1)
        with self.assertRaisesRegex(ValueError, "^native_mlt_file_changed_during_hash$"):
            native_file_digest(path)
        self.assertTrue(stream.closed)
        self.assertEqual(path.stat.call_count, 1)

    def test_nonempty_short_reads_can_reach_the_exact_initial_size(self):
        class PartialReader(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 3))

        data = b"stable fragmented input"
        stream = PartialReader(data)
        path = self._path_with_stream(stream, len(data))
        self.assertEqual(native_file_digest(path), hashlib.sha256(data).hexdigest())
        self.assertTrue(stream.closed)

    def test_stable_physical_files_including_empty_retain_their_digest(self):
        with tempfile.TemporaryDirectory(prefix="native-file-reader-hash-") as directory:
            path = pathlib.Path(directory) / "media.bin"
            for size in (0, 1, 1024 * 1024, 1024 * 1024 + 7):
                with self.subTest(size=size):
                    data = b"x" * size
                    path.write_bytes(data)
                    self.assertEqual(native_file_digest(path), hashlib.sha256(data).hexdigest())

    def test_all_final_identity_fields_are_checked(self):
        data = b"stable source"
        fields = {"st_dev": 1, "st_ino": 2, "st_size": len(data), "st_mtime_ns": 3}
        for changed_field in fields:
            with self.subTest(field=changed_field):
                path = self._path_with_stream(io.BytesIO(data), len(data))
                after = dict(fields)
                after[changed_field] += 1
                path.stat.side_effect = [path.stat.return_value, mock.Mock(**after)]
                with self.assertRaisesRegex(ValueError,
                                            "^native_mlt_file_changed_during_hash$"):
                    native_file_digest(path)

    def test_negative_initial_size_is_rejected_before_open(self):
        path = self._path_with_stream(io.BytesIO(b"x"), -1)
        with self.assertRaisesRegex(ValueError, "^native_mlt_file_size_invalid$"):
            native_file_digest(path)
        path.open.assert_not_called()
        path.open.return_value.close()


if __name__ == "__main__":
    unittest.main()
