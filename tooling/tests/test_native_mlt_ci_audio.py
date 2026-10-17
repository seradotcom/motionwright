"""Pure adversarial gate for the exact native CI stereo content oracle.

The independent ffmpeg decoder runs only in the real Host E2E. This suite
tests the bounded decoded PCM evaluator without a browser or native process.
"""
import copy
import hashlib
import io
import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from native_mlt_sequence_e2e import (
    inspect_decoded_ci_stereo, retain_native_master_source, retain_partial_original_mix_source,
    retain_original_mix_master, inspect_retained_original_mix,
)
from native_mlt_mix_readback import ContentLimits, MediaClock, ReadbackError
from test_native_mlt_public_artifact import (
    unit_original_mix_contract, unit_original_mix_project,
    unit_original_mix_receipt, unit_original_mix_readback,
)
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

    def test_per_mode_clocks_keep_the_legacy_content_oracle_separate(self):
        self.assertEqual(pub.mode_contract("final-mp4").pcm_frames, 52800)
        self.assertEqual(pub.mode_contract("captioned-mp4").video_frames, 33)
        proposed_clock = pub.MltModeContract(60, (32, 28))
        self.assertEqual(proposed_clock.pcm_frames, 96000)
        self.assertEqual(proposed_clock.wav_bytes, 384044)
        self.assertEqual(pub.MODES, {"pcm", "video-only", "final-mp4", "captioned-mp4", "original-mix-mp4"})
        self.assertEqual(pub.mode_contract("original-mix-mp4").pcm_frames, 96000)
        self.assertEqual(pub.mode_contract("original-mix-mp4").source_segment_frames, (32, 28))
        for mode in ("original-sfx-mp4",):
            with self.subTest(mode=mode), self.assertRaises(pub.PublicationError):
                pub.mode_contract(mode)
        with self.assertRaises(ValueError):
            pub.MltModeContract(1, (1,), fps_num=30000, fps_den=1001)
        with self.assertRaises(AssertionError):
            inspect_decoded_ci_stereo(original_pcm(), mode="original-mix-mp4")


class OriginalMixCustodyAndReaderRoutingTests(unittest.TestCase):
    def setUp(self):
        self.data = self.enterContext(unit_original_mix_contract())
        self.root = self.enterContext(tempfile.TemporaryDirectory(prefix="mw-original-mix-routing-"))
        self.native_temp = tempfile.TemporaryDirectory(prefix="native-", dir=self.root)
        self.addCleanup(self.native_temp.cleanup)
        self.native = pathlib.Path(self.native_temp.name) / "output"
        self.native.mkdir()
        self.evidence = pathlib.Path(self.root) / "retained"
        self.evidence.mkdir()
        self.project = unit_original_mix_project()
        self.mp4 = b"\x00\x00\x00\x18ftypisom" + b"unit-original-mix-routing"
        self.master_sha = hashlib.sha256(self.mp4).hexdigest()
        self.receipt = unit_original_mix_receipt(self.project, self.master_sha, len(self.mp4), "9" * 64)
        self.source = self.native / self.receipt["staged_audio"]["relative_path"]
        self.source.write_bytes(self.data)
        self.master = self.native / pub.original_mix_master_name(self.project)
        self.master.write_bytes(self.mp4)
        self.result = {"master_artifact_path": self.master.name, "master_artifact_sha256": self.master_sha,
                       "master_artifact_bytes": len(self.mp4)}

    def retain(self):
        return retain_native_master_source(
            self.native, self.evidence, self.receipt, "original-mix-mp4", self.project
        )

    def retain_both(self):
        source = self.retain()
        master = retain_original_mix_master(
            self.native, self.evidence, self.project, self.receipt, self.result
        )
        return source, master

    def test_original_mix_and_mp4_are_retained_before_the_one_reader_call(self):
        custody, retained_master = self.retain_both()
        self.native_temp.cleanup()
        self.assertFalse(self.source.exists())
        self.assertEqual((self.evidence / pub.SOURCE_WAV_NAME).read_bytes(), self.data)
        self.assertEqual(retained_master.read_bytes(), self.mp4)
        self.assertEqual(custody["source_kind"], "original-mix")
        self.assertEqual(custody["bytes"], 384044)
        self.assertEqual(custody["full_master_reference_readback"], "NOT_RUN")
        with patch("native_mlt_sequence_e2e.inspect_master",
                   return_value=unit_original_mix_readback(self.master_sha)) as inspected, \
             patch("native_mlt_sequence_e2e.inspect_native_master_decoded_content") as legacy:
            proof, proof_sha = inspect_retained_original_mix(
                self.evidence, self.master_sha, pathlib.Path("ffmpeg"), pathlib.Path("ffprobe")
            )
        legacy.assert_not_called()
        inspected.assert_called_once_with(
            self.evidence / pub.SOURCE_WAV_NAME, retained_master,
            reference_sha256=pub.ORIGINAL_MIX_WAV_SHA256, master_sha256=self.master_sha,
            clock=MediaClock(60, 1280, 720, 30, 1),
            ffmpeg="ffmpeg", ffprobe="ffprobe", limits=ContentLimits(),
        )
        self.assertEqual(proof["technical_content_readback"], "PASS")
        self.assertEqual(proof_sha, hashlib.sha256((self.evidence / pub.ORIGINAL_MIX_READBACK).read_bytes()).hexdigest())

    def test_reader_failure_retains_original_bytes_without_a_success_document(self):
        self.retain_both()
        captured = io.StringIO()
        run = {"GITHUB_RUN_ID": "39054798740", "GITHUB_RUN_ATTEMPT": "1"}
        with patch.dict(os.environ, run), \
             patch("native_mlt_sequence_e2e.GITHUB_SHA", "a" * 40), \
             patch("native_mlt_sequence_e2e.PIN", "b" * 40), \
             patch("native_mlt_sequence_e2e.sys.stderr", captured), \
             patch("native_mlt_sequence_e2e.inspect_master",
                   side_effect=ReadbackError("local_audio_content_correlation")) as inspected:
            with self.assertRaisesRegex(AssertionError, "native_original_mix_readback_failed"):
                inspect_retained_original_mix(
                    self.evidence, self.master_sha, pathlib.Path("ffmpeg"), pathlib.Path("ffprobe")
                )
            public = pathlib.Path(self.root) / "public-failure"
            published = pub.publish_mlt(
                self.evidence, public, "original-mix-mp4", "a" * 40, "b" * 40, "failure"
            )
        inspected.assert_called_once()
        self.native_temp.cleanup()
        self.assertEqual((self.evidence / pub.SOURCE_WAV_NAME).read_bytes(), self.data)
        self.assertEqual((self.evidence / "native-multisegment-master.mp4").read_bytes(), self.mp4)
        self.assertFalse((self.evidence / pub.ORIGINAL_MIX_READBACK).exists())
        diagnostic = json.loads((self.evidence / pub.ORIGINAL_MIX_FAILURE_FILE).read_text())
        self.assertEqual(diagnostic["technical_content_readback"], "FAIL")
        self.assertEqual(diagnostic["reason"], "local_audio_content_correlation")
        self.assertNotIn(self.root, repr(diagnostic))
        logged = json.loads(captured.getvalue())
        self.assertEqual(logged["reason"], diagnostic["reason"])
        self.assertEqual(logged["input_stability_after_failure"], "NOT_VERIFIED")
        self.assertEqual(published["ci_native_mlt_evidence"], "NOT_VERIFIED")
        projection = published["original_mix_readback_failure"]
        self.assertEqual(projection["reason"], logged["reason"])
        self.assertEqual(projection["final_mp4_sha256_before_call"], self.master_sha)
        self.assertEqual(projection["failure_document_sha256"], hashlib.sha256(
            (self.evidence / pub.ORIGINAL_MIX_FAILURE_FILE).read_bytes()
        ).hexdigest())
        self.assertEqual({path.name for path in public.iterdir()}, {"public-evidence.json"})
        self.assertEqual(published["included_files"], [])
        self.assertIsNone(published["retained_source_audio"])
        self.assertNotIn("original_mix_readback", published)

    def test_private_diagnostic_write_refusal_keeps_the_primary_error_and_safe_log(self):
        self.retain_both()
        retained = self.evidence / pub.ORIGINAL_MIX_FAILURE_FILE
        previous = b"existing-private-draft-must-remain-exact"
        retained.write_bytes(previous)
        captured = io.StringIO()
        with patch("native_mlt_sequence_e2e.sys.stderr", captured), \
             patch("native_mlt_sequence_e2e.inspect_master",
                   side_effect=ReadbackError("reference_timing_signature_ambiguous")) as inspected:
            with self.assertRaisesRegex(AssertionError, "native_original_mix_readback_failed"):
                inspect_retained_original_mix(
                    self.evidence, self.master_sha, pathlib.Path("ffmpeg"), pathlib.Path("ffprobe")
                )
        inspected.assert_called_once()
        self.assertEqual(retained.read_bytes(), previous)
        self.assertEqual(json.loads(captured.getvalue())["reason"],
                         "reference_timing_signature_ambiguous")
        self.assertFalse((self.evidence / pub.ORIGINAL_MIX_READBACK).exists())
        self.assertEqual((self.evidence / pub.SOURCE_WAV_NAME).read_bytes(), self.data)
        self.assertEqual((self.evidence / "native-multisegment-master.mp4").read_bytes(), self.mp4)

    def test_unknown_reader_reason_is_redacted_in_both_retained_document_and_log(self):
        self.retain_both()
        captured = io.StringIO()
        private = "customer_secret_password"
        with patch("native_mlt_sequence_e2e.sys.stderr", captured), \
             patch("native_mlt_sequence_e2e.inspect_master", side_effect=ReadbackError(private)):
            with self.assertRaisesRegex(AssertionError, "native_original_mix_readback_failed"):
                inspect_retained_original_mix(
                    self.evidence, self.master_sha, pathlib.Path("ffmpeg"), pathlib.Path("ffprobe")
                )
        document = (self.evidence / pub.ORIGINAL_MIX_FAILURE_FILE).read_text()
        self.assertNotIn(private, document + captured.getvalue())
        for value in (json.loads(document), json.loads(captured.getvalue())):
            self.assertEqual(value["reason"], "reader_refused")
            self.assertIs(value["reason_identified"], False)
            self.assertEqual(value["complete_master_readback"], "NOT_VERIFIED")

    def test_stderr_refusal_does_not_replace_the_primary_reader_error(self):
        self.retain_both()
        with patch("native_mlt_sequence_e2e.sys.stderr") as stderr, \
             patch("native_mlt_sequence_e2e.inspect_master",
                   side_effect=ReadbackError("local_audio_gain")):
            stderr.write.side_effect = OSError("private stderr unavailable")
            with self.assertRaisesRegex(AssertionError, "native_original_mix_readback_failed"):
                inspect_retained_original_mix(
                    self.evidence, self.master_sha, pathlib.Path("ffmpeg"), pathlib.Path("ffprobe")
                )
        document = json.loads((self.evidence / pub.ORIGINAL_MIX_FAILURE_FILE).read_text())
        self.assertEqual(document["reason"], "local_audio_gain")
        self.assertFalse((self.evidence / pub.ORIGINAL_MIX_READBACK).exists())

    def test_changed_retained_source_or_master_during_readback_cannot_leave_pass(self):
        self.retain_both()
        source = self.evidence / pub.SOURCE_WAV_NAME
        master = self.evidence / "native-multisegment-master.mp4"
        for target, original in ((source, self.data), (master, self.mp4)):
            with self.subTest(target=target.name):
                source.write_bytes(self.data)
                master.write_bytes(self.mp4)

                def replace_during_read(*args, **kwargs):
                    target.write_bytes(original + b"changed")
                    return unit_original_mix_readback(self.master_sha)

                with patch("native_mlt_sequence_e2e.inspect_master", side_effect=replace_during_read):
                    with self.assertRaises(pub.PublicationError):
                        inspect_retained_original_mix(
                            self.evidence, self.master_sha, pathlib.Path("ffmpeg"), pathlib.Path("ffprobe")
                        )
                self.assertFalse((self.evidence / pub.ORIGINAL_MIX_READBACK).exists())

    def test_partial_custody_uses_only_the_existing_recognized_fixture_and_no_native_retry(self):
        with patch("native_mlt_sequence_e2e.run_json") as native, \
             patch("native_mlt_sequence_e2e.inspect_master") as inspected:
            with self.assertRaisesRegex(RuntimeError, "native_outcome_unknown"):
                try:
                    raise RuntimeError("native_outcome_unknown")
                finally:
                    retain_partial_original_mix_source(self.native, self.evidence, self.project)
        native.assert_not_called()
        inspected.assert_not_called()
        self.native_temp.cleanup()
        self.assertEqual((self.evidence / pub.SOURCE_WAV_NAME).read_bytes(), self.data)
        outcome = json.loads((self.evidence / "native-original-mix-partial-custody.json").read_text())
        self.assertEqual(outcome["source_status"], "RETAINED")
        self.assertEqual(outcome["scope"], "SOURCE_ONLY")
        self.assertEqual(outcome["master_readback"], "NOT_RUN")
        self.assertFalse((self.evidence / "native-multisegment-master.mp4").exists())
        self.assertFalse((self.evidence / pub.ORIGINAL_MIX_READBACK).exists())
        self.assertFalse((self.evidence / pub.ORIGINAL_MIX_RECEIPT).exists())

    def test_absent_partial_source_stays_absent_without_reconstruction(self):
        self.source.unlink()
        with patch("native_mlt_sequence_e2e.run_json") as native, \
             patch("native_mlt_sequence_e2e.inspect_master") as inspected:
            retain_partial_original_mix_source(self.native, self.evidence, self.project)
        native.assert_not_called()
        inspected.assert_not_called()
        self.assertFalse((self.evidence / pub.SOURCE_WAV_NAME).exists())
        outcome = json.loads((self.evidence / "native-original-mix-partial-custody.json").read_text())
        self.assertEqual(outcome["source_status"], "NOT_OBSERVED")
        self.assertNotIn("retained_source_audio", outcome)

    def test_custody_failure_never_masks_the_native_unknown_outcome(self):
        with patch("native_mlt_sequence_e2e.retain_admitted_source_file",
                   side_effect=OSError("private-path-unavailable")):
            with self.assertRaisesRegex(RuntimeError, "native_outcome_unknown"):
                try:
                    raise RuntimeError("native_outcome_unknown")
                finally:
                    retain_partial_original_mix_source(self.native, self.evidence, self.project)
        outcome = json.loads((self.evidence / "native-original-mix-partial-custody.json").read_text())
        self.assertEqual(outcome["source_status"], "REFUSED")
        self.assertNotIn("private-path-unavailable", repr(outcome))
        self.assertFalse((self.evidence / pub.SOURCE_WAV_NAME).exists())

    def test_other_project_or_nonfixture_partial_source_is_never_retained(self):
        self.source.unlink()
        other = {**self.project, "revision": self.project["revision"] + 1}
        foreign = self.native / pub.original_mix_source_name(other, "f" * 64)
        foreign.write_bytes(self.data)
        with patch("native_mlt_sequence_e2e.read_synthetic_source_wav") as opened:
            retain_partial_original_mix_source(self.native, self.evidence, self.project)
        opened.assert_not_called()
        self.assertFalse((self.evidence / pub.SOURCE_WAV_NAME).exists())
        (self.evidence / "native-original-mix-partial-custody.json").unlink()
        self.source.write_bytes(self.data[:-4] + b"\x01\x00\x00\x00")
        retain_partial_original_mix_source(self.native, self.evidence, self.project)
        outcome = json.loads((self.evidence / "native-original-mix-partial-custody.json").read_text())
        self.assertEqual(outcome["source_status"], "REFUSED")
        self.assertFalse((self.evidence / pub.SOURCE_WAV_NAME).exists())

    def test_existing_source_master_and_readback_drafts_are_never_overwritten(self):
        source = self.evidence / pub.SOURCE_WAV_NAME
        source.write_bytes(b"existing-source-draft")
        with self.assertRaises(AssertionError):
            self.retain()
        self.assertEqual(source.read_bytes(), b"existing-source-draft")
        source.unlink()
        self.retain()
        master = self.evidence / "native-multisegment-master.mp4"
        master.write_bytes(b"existing-master-draft")
        with self.assertRaises(FileExistsError):
            retain_original_mix_master(self.native, self.evidence, self.project, self.receipt, self.result)
        self.assertEqual(master.read_bytes(), b"existing-master-draft")
        master.unlink()
        retain_original_mix_master(self.native, self.evidence, self.project, self.receipt, self.result)
        proof_file = self.evidence / pub.ORIGINAL_MIX_READBACK
        proof_file.write_bytes(b"existing-readback-draft")
        with patch("native_mlt_sequence_e2e.inspect_master",
                   return_value=unit_original_mix_readback(self.master_sha)):
            with self.assertRaises(FileExistsError):
                inspect_retained_original_mix(
                    self.evidence, self.master_sha, pathlib.Path("ffmpeg"), pathlib.Path("ffprobe")
                )
        self.assertEqual(proof_file.read_bytes(), b"existing-readback-draft")

    def test_foreign_master_path_is_refused_before_the_file_is_opened(self):
        for path in ("../private.mp4", "/private.mp4", "C:\\private.mp4", "other-master.mp4"):
            with self.subTest(path=path):
                altered = {**self.result, "master_artifact_path": path}
                with patch("native_mlt_sequence_e2e.os.open") as opened:
                    with self.assertRaises(pub.PublicationError):
                        retain_original_mix_master(
                            self.native, self.evidence, self.project, self.receipt, altered
                        )
                opened.assert_not_called()
                self.assertFalse((self.evidence / "native-multisegment-master.mp4").exists())



if __name__ == "__main__":
    unittest.main()
