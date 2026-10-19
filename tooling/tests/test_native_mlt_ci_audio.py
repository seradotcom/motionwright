"""Adversarial CI stereo and retained-readback adapter controls.

Pure PCM cases and mocked decoder observations exercise the gates and custody.
One real FFmpeg control encodes only this synthetic WAV; it is not an MLT
provider run, native acceptance, packet/priming proof or a human verdict.
"""
import copy
import hashlib
import io
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from native_mlt_sequence_e2e import (
    AudioReadbackLimitExceeded, inspect_decoded_ci_stereo,
    inspect_native_master_decoded_content, retain_native_master_source,
    run_bounded_audio_readback,
)
import native_mlt_public_artifact as pub
import native_mlt_sequence_e2e as wrapper


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


class NativeRetainedAudioReadbackTests(unittest.TestCase):
    """Decoder mocks test adapter wiring, never actual native media production."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="mw-audio-readback-")
        self.addCleanup(self.root.cleanup)
        self.root_path = pathlib.Path(self.root.name)
        self.evidence = self.root_path / "retained"
        self.evidence.mkdir()
        self.source = self.evidence / "native-master-source.wav"
        self.source_data = synthetic_voice_wav()
        self.source.write_bytes(self.source_data)
        self.master = self.evidence / "native-multisegment-master.mp4"
        self.master_data = b"synthetic-decoder-adapter-input-not-an-mp4"
        self.master.write_bytes(self.master_data)
        self.ffmpeg = self.root_path / "ffmpeg-test-executable"
        self.ffprobe = self.root_path / "ffprobe-test-executable"
        self.ffmpeg.write_bytes(b"synthetic-ffmpeg-identity")
        self.ffprobe.write_bytes(b"synthetic-ffprobe-identity")
        self.commands = []

    @staticmethod
    def clock():
        return {
            "streams": [{"codec_type": "audio", "codec_name": "aac",
                         "sample_rate": "48000", "channels": 2,
                         "time_base": "1/48000"}],
            "packets_and_frames": [
                {"type": "packet", "pts": -1024, "duration": 1024},
                {"type": "frame", "best_effort_timestamp": 0, "nb_samples": 1024},
            ],
        }

    def inspect(self, pcm, *, clock=None, decoder_exit=0, after_decode=None):
        clock = self.clock() if clock is None else clock
        clock_bytes = clock if isinstance(clock, bytes) else json.dumps(clock).encode()

        def decoder(command, *, env, stdout, stderr, timeout, check,
                    stdout_limit, stderr_limit):
            self.commands.append(command)
            self.assertFalse(check)
            self.assertLessEqual(stdout_limit, 2_000_000)
            self.assertEqual(stderr_limit, 65_536)
            if command[0] == str(self.ffprobe):
                stdout.write(clock_bytes)
                return subprocess.CompletedProcess(command, 0)
            self.assertEqual(command[0], str(self.ffmpeg))
            stdout.write(pcm)
            stderr.write(b"synthetic-decoder-diagnostic\n")
            if after_decode:
                after_decode(command, timeout)
            return subprocess.CompletedProcess(command, decoder_exit)

        with patch("native_mlt_sequence_e2e.run_bounded_audio_readback", side_effect=decoder):
            return inspect_native_master_decoded_content(
                self.master, self.ffmpeg, dict(os.environ), source_wav=self.source,
                ffprobe=self.ffprobe, evidence_root=self.evidence,
            )

    def retained_failure(self, pcm=None):
        if pcm is None:
            self.assertFalse((self.evidence / "native-master-decoded.s16le").exists())
        else:
            self.assertEqual((self.evidence / "native-master-decoded.s16le").read_bytes(), pcm)
        clock = self.evidence / "native-master-audio-clock.json"
        self.assertTrue(clock.is_file())
        observation_path = self.evidence / "native-master-audio-observation.json"
        observation = json.loads(observation_path.read_bytes())
        failure = json.loads((self.evidence / "native-master-audio-failure.json").read_bytes())
        self.assertEqual(failure["status"], "FAIL")
        self.assertFalse(failure["native_job_retried"])
        self.assertEqual(failure["observation_sha256"],
                         hashlib.sha256(observation_path.read_bytes()).hexdigest())
        if pcm is None:
            self.assertNotIn("pcm", observation["outputs"])
            self.assertEqual(len(self.commands), 1)
        else:
            self.assertEqual(observation["outputs"]["pcm"]["sha256"],
                             hashlib.sha256(pcm).hexdigest())
        self.assertEqual(observation["outputs"]["clock"]["sha256"],
                         hashlib.sha256(clock.read_bytes()).hexdigest())
        self.assertFalse(observation["native_execution_performed"])
        self.assertFalse(observation["media_packet_priming_and_skip_verified"])
        return observation, failure

    def test_full_source_hook_retains_originals_and_never_normalizes_audio(self):
        pcm = original_pcm() + bytes(448 * 4)
        result = self.inspect(pcm)
        envelope = result["full_source_pcm_envelope"]
        self.assertEqual(envelope["source_windows_checked_per_channel"], 825)
        self.assertEqual(envelope["source_frames"], 52800)
        self.assertEqual(envelope["decoded_frames"], 53248)
        self.assertEqual(envelope["unexplained_aac_extra_frames"], 448)
        self.assertFalse(result["media_packet_priming_and_skip_verified"])
        self.assertFalse(envelope["native_provider_receipt_verified"])
        self.assertFalse(envelope["human_listening_approved"])
        self.assertEqual(self.source.read_bytes(), self.source_data)
        self.assertEqual(self.master.read_bytes(), self.master_data)
        self.assertEqual((self.evidence / "native-master-decoded.s16le").read_bytes(), pcm)
        self.assertEqual(len(self.commands), 2)
        for forbidden in ("-ac", "-ar", "-af", "-filter:a", "-filter_complex", "-t", "-ss"):
            self.assertNotIn(forbidden, self.commands[1])
        self.assertIn("-show_packets", self.commands[0])
        self.assertIn("-show_frames", self.commands[0])
        self.assertFalse((self.evidence / "native-master-audio-failure.json").exists())

    def test_known_right_tail_blind_spot_now_fails_with_complete_originals(self):
        # Synthetic reproduction of the defect geometry, not the original
        # historical AAC bytes: 256 authored right samples + 448 extra frames.
        pcm = b"".join(struct.pack(
            "<hh", ((index % 120) - 60) * 250,
            ((index % 80) - 40) * 260 if index < 52544 else 0,
        ) for index in range(53248))
        inspect_decoded_ci_stereo(pcm)  # The unchanged old gate was insufficient.
        with self.assertRaisesRegex(AssertionError,
                                    "ci_source_channel_envelope_lost:right:52544:52608"):
            self.inspect(pcm)
        self.retained_failure(pcm)
        self.assertEqual(self.source.read_bytes(), self.source_data)
        self.assertEqual(self.master.read_bytes(), self.master_data)

    def test_partial_stereo_sample_is_retained_and_rejected(self):
        pcm = (original_pcm() + bytes(448 * 4))[:-1]
        with self.assertRaisesRegex(AssertionError, "complete stereo s16"):
            self.inspect(pcm)
        self.retained_failure(pcm)

    def test_swapped_channels_still_fail_the_original_midstream_gate(self):
        pcm = b"".join(struct.pack("<hh", right, left)
                       for left, right in struct.iter_unpack("<hh", original_pcm()))
        with self.assertRaisesRegex(AssertionError, "lost distinct authored stereo"):
            self.inspect(pcm)
        self.retained_failure(pcm)

    def test_nonzero_decoder_exit_keeps_stdout_stderr_and_clock(self):
        pcm = original_pcm()[:40]
        with self.assertRaisesRegex(AssertionError, "probe or decoder failed"):
            self.inspect(pcm, decoder_exit=19)
        observation, _ = self.retained_failure(pcm)
        self.assertEqual(observation["exit_codes"], {"clock": 0, "pcm": 19})
        self.assertEqual((self.evidence / "native-master-decode.stderr.log").read_bytes(),
                         b"synthetic-decoder-diagnostic\n")
        self.assertEqual(len(self.commands), 2)

    def test_clock_channel_mismatch_is_not_hidden_by_resampling(self):
        clock = self.clock()
        clock["streams"][0]["channels"] = 1
        pcm = original_pcm()
        with self.assertRaisesRegex(AssertionError, "admitted stereo profile"):
            self.inspect(pcm, clock=clock)
        self.retained_failure()

    def test_malformed_packet_frame_row_is_retained_and_rejected(self):
        clock = self.clock()
        clock["packets_and_frames"].append(None)
        pcm = original_pcm()
        with self.assertRaisesRegex(AssertionError, "clock is malformed"):
            self.inspect(pcm, clock=clock)
        self.retained_failure()

    def test_malformed_json_clock_is_retained_without_becoming_success(self):
        pcm = original_pcm()
        with self.assertRaisesRegex(AssertionError, "native_audio_custody_refused"):
            self.inspect(pcm, clock=b"not a JSON object")
        self.retained_failure()
        self.assertEqual((self.evidence / "native-master-audio-clock.json").read_bytes(),
                         b"not a JSON object")

    def test_existing_observation_is_not_overwritten_or_reexecuted(self):
        existing = self.evidence / "native-master-decoded.s16le"
        existing.write_bytes(b"previous-original-observation")
        with self.assertRaisesRegex(AssertionError, "must not be overwritten"):
            self.inspect(original_pcm())
        self.assertEqual(existing.read_bytes(), b"previous-original-observation")
        self.assertEqual(self.commands, [])

    def test_source_replacement_during_decode_is_detected(self):
        def replace_source(command, timeout):
            replacement = self.root_path / "replacement.wav"
            replacement.write_bytes(self.source_data)
            replacement.replace(self.source)
        pcm = original_pcm()
        with self.assertRaisesRegex(AssertionError, "native_audio_custody_refused"):
            self.inspect(pcm, after_decode=replace_source)
        self.retained_failure(pcm)

    def test_master_replacement_during_decode_is_detected(self):
        def replace_master(command, timeout):
            replacement = self.root_path / "replacement.mp4"
            replacement.write_bytes(self.master_data)
            replacement.replace(self.master)
        pcm = original_pcm()
        with self.assertRaisesRegex(AssertionError, "master changed"):
            self.inspect(pcm, after_decode=replace_master)
        self.retained_failure(pcm)

    def test_timeout_keeps_partial_observations_without_retry(self):
        def time_out(command, timeout):
            raise subprocess.TimeoutExpired(command, timeout)
        pcm = original_pcm()[:40]
        with self.assertRaisesRegex(AssertionError, "interrupted; no retry"):
            self.inspect(pcm, after_decode=time_out)
        observation, _ = self.retained_failure(pcm)
        self.assertEqual(observation["interrupted"],
                         {"stage": "pcm", "error_type": "TimeoutExpired"})
        self.assertEqual(observation["exit_codes"], {"clock": 0})
        self.assertEqual(len(self.commands), 2)

    def test_budget_truncation_cannot_pass_even_when_retained_pcm_is_valid(self):
        from native_mlt_ci_pcm_envelope import inspect_fixed_ci_stereo_envelope
        pcm = original_pcm() + bytes(1024 * 4)
        inspect_fixed_ci_stereo_envelope(self.source_data, pcm)

        def capped_prefix(command, timeout):
            raise AudioReadbackLimitExceeded(["stdout"], -9)

        with self.assertRaisesRegex(AssertionError, "interrupted; no retry"):
            self.inspect(pcm, after_decode=capped_prefix)
        observation, _ = self.retained_failure(pcm)
        self.assertFalse(observation["capture_complete"])
        self.assertEqual(observation["interrupted"]["truncated_streams"], ["stdout"])
        self.assertEqual(observation["outputs"]["pcm"]["bytes"],
                         observation["capture_limits_bytes"]["pcm"])


class BoundedReadbackCaptureTests(unittest.TestCase):
    """Real harmless child processes test pipe budgets and exit propagation."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="mw-bounded-readback-")
        self.addCleanup(self.root.cleanup)
        self.stdout_path = pathlib.Path(self.root.name) / "stdout.bin"
        self.stderr_path = pathlib.Path(self.root.name) / "stderr.bin"

    def run_child(self, program, *, stdout_limit=1024, stderr_limit=1024, timeout=3):
        with self.stdout_path.open("xb") as output, self.stderr_path.open("xb") as errors:
            return run_bounded_audio_readback(
                [sys.executable, "-B", "-E", "-s", "-c", program],
                env=dict(os.environ), stdout=output, stderr=errors, timeout=timeout,
                stdout_limit=stdout_limit, stderr_limit=stderr_limit,
            )

    def test_exact_budget_preserves_all_binary_bytes_and_success(self):
        result = self.run_child("import os; os.write(1, b'a\\x00' * 512); os.write(2, b'warn')")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.stdout_path.read_bytes(), b"a\x00" * 512)
        self.assertEqual(self.stderr_path.read_bytes(), b"warn")

    def test_stdout_flood_is_capped_and_cannot_be_success(self):
        with self.assertRaises(AudioReadbackLimitExceeded) as caught:
            self.run_child("import os, time; os.write(1, b'x' * 65536); time.sleep(10)")
        self.assertIn("stdout", caught.exception.streams)
        self.assertEqual(self.stdout_path.read_bytes(), b"x" * 1024)
        self.assertLessEqual(self.stderr_path.stat().st_size, 1024)
        self.assertNotEqual(caught.exception.returncode, 0)

    def test_stderr_flood_is_capped_independently(self):
        with self.assertRaises(AudioReadbackLimitExceeded) as caught:
            self.run_child("import os, time; os.write(1, b'ok'); os.write(2, b'e' * 65536); time.sleep(10)")
        self.assertIn("stderr", caught.exception.streams)
        self.assertEqual(self.stdout_path.read_bytes(), b"ok")
        self.assertEqual(self.stderr_path.read_bytes(), b"e" * 1024)

    def test_deadline_keeps_partial_bytes_and_never_returns_success(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_child("import os, time; os.write(1, b'partial'); os.write(2, b'diagnostic'); time.sleep(10)",
                           timeout=0.5)
        self.assertEqual(self.stdout_path.read_bytes(), b"partial")
        self.assertEqual(self.stderr_path.read_bytes(), b"diagnostic")

    def test_real_nonzero_exit_is_propagated_with_original_bytes(self):
        result = self.run_child("import os; os.write(1, b'partial'); os.write(2, b'failure'); raise SystemExit(23)")
        self.assertEqual(result.returncode, 23)
        self.assertEqual(self.stdout_path.read_bytes(), b"partial")
        self.assertEqual(self.stderr_path.read_bytes(), b"failure")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "The real synthetic AAC readback control requires FFmpeg and FFprobe")
class RealSyntheticAacReadbackControl(unittest.TestCase):
    def test_real_decoder_retains_full_pcm_and_original_clock(self):
        # Only a reader control with synthetic input; no MLT, Host or producer.
        with tempfile.TemporaryDirectory(prefix="mw-synthetic-aac-reader-") as directory:
            evidence = pathlib.Path(directory)
            source = evidence / "native-master-source.wav"
            source.write_bytes(synthetic_voice_wav())
            master = evidence / "synthetic-aac-reader.mp4"
            ffmpeg = pathlib.Path(shutil.which("ffmpeg"))
            ffprobe = pathlib.Path(shutil.which("ffprobe"))
            subprocess.run([
                str(ffmpeg), "-nostdin", "-v", "error", "-i", str(source),
                "-c:a", "aac", "-b:a", "192k", str(master),
            ], check=True, capture_output=True, timeout=30)
            result = inspect_native_master_decoded_content(
                master, ffmpeg, dict(os.environ), source_wav=source,
                ffprobe=ffprobe, evidence_root=evidence,
            )
            self.assertEqual(result["full_source_pcm_envelope"]
                             ["source_windows_checked_per_channel"], 825)
            self.assertEqual(result["full_source_pcm_envelope"]["source_frames"], 52800)
            self.assertFalse(result["media_packet_priming_and_skip_verified"])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), SOURCE_SHA)
            clock = json.loads((evidence / "native-master-audio-clock.json").read_bytes())
            self.assertEqual(clock["streams"][0]["channels"], 2)
            self.assertEqual(clock["streams"][0]["sample_rate"], "48000")
            self.assertGreaterEqual((evidence / "native-master-decoded.s16le").stat().st_size,
                                    52800 * 4)


class NativeTechnicalCustodyTests(unittest.TestCase):
    """Synthetic byte custody only; these tests never invoke media tools or MLT."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mw-technical-custody-")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.evidence = self.root / "evidence"
        self.owner = self.root / "owner-private"
        self.evidence.mkdir()
        (self.owner / "runtime").mkdir(parents=True)
        self.ticket = b"private-session-token-0123456789"
        (self.owner / "runtime" / "motionwright.session").write_bytes(
            json.dumps({"session_token": self.ticket.decode()}).encode()
        )
        self.staged, self.published = self.root / "staged", self.root / "published"
        self.context = {"source": "a" * 40, "run_id": "12345", "run_attempt": "1"}
        self.originals = {
            "native-master-source.wav": synthetic_voice_wav(),
            "native-multisegment-master.mp4": b"\x00\x00\x00\x18ftypisomSYNTHETIC-CUSTODY-FIXTURE",
            "native-master-decoded.s16le": b"\x00\x00\x01\x00" * 8,
            "native-master-audio-clock.json": b'{"streams": [], "frames": []}\n',
            "native-master-audio-clock.stderr.log": b"",
            "native-master-decode.stderr.log": b"original decoder failure\n",
            "native-master-audio-failure.json": b'{"status":"FAIL","native_job_retried":false}\n',
        }
        for name, data in self.originals.items():
            (self.evidence / name).write_bytes(data)

    def stage(self):
        return wrapper.stage_technical_custody(
            self.evidence, self.staged, self.owner, "final-mp4", self.context, True,
        )

    def publish(self, context=None):
        return wrapper.verify_technical_custody(
            self.staged, self.published, "final-mp4", self.context if context is None else context, "failure",
        )

    def test_failure_preserves_exact_originals_without_audio_or_native_pass(self):
        receipt = self.stage()
        self.assertTrue(receipt["wrapper_exception_active"])
        self.assertTrue(receipt["complete_for_existing_allowlisted_files"])
        self.assertFalse(receipt["every_allowlisted_file_present"])
        self.assertEqual(receipt["native_verdict"], "NOT_GRANTED_BY_CUSTODY")
        self.assertFalse(receipt["audio_fidelity_verified_by_custody"])
        self.assertFalse(receipt["formal_acceptance"])
        self.assertTrue(self.publish())
        for name, data in self.originals.items():
            self.assertEqual((self.published / name).read_bytes(), data)
            self.assertEqual((self.evidence / name).read_bytes(), data)
        status = json.loads((self.published / "custody-status.json").read_bytes())
        self.assertEqual(status["native_step_outcome"], "failure")
        self.assertEqual(status["product_verdict"], "NOT_GRANTED_BY_CUSTODY")

    def test_ticket_in_media_is_excluded_without_redaction_or_false_completeness(self):
        name = "native-multisegment-master.mp4"
        original = self.originals[name] + self.ticket
        (self.evidence / name).write_bytes(original)
        receipt = self.stage()
        self.assertFalse(receipt["complete_for_existing_allowlisted_files"])
        self.assertEqual(receipt["excluded_files"], [{"name": name,
            "reason": "technical_custody_private_material_excluded_no_redaction"}])
        self.assertEqual((self.evidence / name).read_bytes(), original)
        self.assertEqual((self.root / "staged-quarantine" / name).read_bytes(), original)
        self.assertFalse((self.staged / name).exists())
        self.assertFalse(self.publish())
        self.assertFalse((self.published / name).exists())
        for path in self.published.iterdir():
            self.assertNotIn(self.ticket, path.read_bytes())

    def test_owner_path_in_log_is_excluded_but_original_remains(self):
        name = "native-master-decode.stderr.log"
        original = b"error at " + str(self.owner).encode() + b"/config/item\n"
        (self.evidence / name).write_bytes(original)
        receipt = self.stage()
        self.assertEqual(receipt["excluded_files"][0]["name"], name)
        self.assertFalse((self.staged / name).exists())
        self.assertEqual((self.evidence / name).read_bytes(), original)

    def test_extra_owner_config_and_provider_logs_are_never_copied(self):
        names = ("owner.toml", "motionwright.session", "semwrightd.log", "run.log", "result.json")
        for name in names:
            (self.evidence / name).write_bytes(self.ticket)
        self.stage()
        self.assertTrue(self.publish())
        for root in (self.staged, self.root / "staged-quarantine", self.published):
            self.assertTrue(all(not (root / name).exists() for name in names))

    def test_same_length_change_after_scan_cannot_reach_publication(self):
        name = "native-multisegment-master.mp4"
        original_scan = wrapper.technical_scan
        def substitute(path, limit, patterns, expected):
            original_scan(path, limit, patterns, expected)
            if path.name == name:
                path.write_bytes(b"X" * expected["bytes"])
        with patch.object(wrapper, "technical_scan", side_effect=substitute):
            receipt = self.stage()
        self.assertFalse(receipt["complete_for_existing_allowlisted_files"])
        self.assertFalse((self.staged / name).exists())
        self.assertEqual((self.evidence / name).read_bytes(), self.originals[name])
        self.assertFalse(self.publish())
        self.assertFalse((self.published / name).exists())

    def test_change_after_staging_is_rejected_before_upload_copy(self):
        self.stage()
        name = "native-multisegment-master.mp4"
        (self.staged / name).write_bytes(self.originals[name] + self.ticket)
        self.assertFalse(self.publish())
        self.assertFalse((self.published / name).exists())
        self.assertEqual((self.evidence / name).read_bytes(), self.originals[name])

    def test_non_synthetic_wav_is_retained_privately_but_not_admitted(self):
        name = "native-master-source.wav"
        altered = bytearray(self.originals[name]); altered[-1] ^= 1
        (self.evidence / name).write_bytes(altered)
        receipt = self.stage()
        self.assertFalse(receipt["complete_for_existing_allowlisted_files"])
        self.assertFalse((self.staged / name).exists())
        self.assertEqual((self.evidence / name).read_bytes(), altered)

    def test_file_budget_is_checked_before_any_copy(self):
        limits = wrapper.technical_allowlist("final-mp4")
        name = "native-multisegment-master.mp4"
        limits[name] = 8
        with patch.object(wrapper, "technical_allowlist", return_value=limits):
            receipt = self.stage()
        self.assertFalse(receipt["complete_for_existing_allowlisted_files"])
        self.assertFalse((self.root / "staged-quarantine" / name).exists())
        self.assertFalse((self.staged / name).exists())

    def test_growth_during_retained_copy_is_bounded_and_rejected(self):
        source, destination = self.root / "source.mp4", self.root / "retained.mp4"
        source.write_bytes(b"12345678")
        expected = wrapper.technical_record(source, 8)
        original_fdopen = wrapper.os.fdopen
        read_opens, requested = 0, []

        class GrowingReader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self, limit):
                requested.append(limit)
                with source.open("ab") as writer:
                    writer.write(b"X" * 64)
                return self.stream.read(limit)

        def observed_fdopen(descriptor, mode, *args, **kwargs):
            nonlocal read_opens
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            if mode == "rb":
                read_opens += 1
                if read_opens == 2:  # After the initial source identity check.
                    return GrowingReader(stream)
            return stream

        with patch.object(wrapper.os, "fdopen", side_effect=observed_fdopen):
            with self.assertRaises(pub.PublicationError) as rejected:
                wrapper.technical_copy(source, destination, 8, expected)
        self.assertEqual(rejected.exception.code, "technical_custody_source_changed")
        self.assertEqual(requested, [9])
        self.assertEqual(source.stat().st_size, 72)
        self.assertEqual(destination.read_bytes(), b"")

    def test_outcome_write_error_preserves_original_failure_and_incomplete_custody(self):
        name = "native-assembly-outcome.json"
        original = b'{"state":"an earlier outcome is not this attempt"}\n'
        (self.evidence / name).write_bytes(original)
        native_failure = RuntimeError("original native failure")
        try:
            try:
                raise native_failure
            except RuntimeError as original_error:
                with patch.object(wrapper, "write_new_private_json", side_effect=OSError("disk full")):
                    recorded = wrapper.record_assembly_failure(self.evidence, self.context, original_error)
                raise
        except RuntimeError as preserved:
            self.assertIs(preserved, native_failure)
        self.assertFalse(recorded)
        receipt = wrapper.stage_technical_custody(
            self.evidence, self.staged, self.owner, "final-mp4", self.context, True,
            assembly_journal_complete=recorded,
        )
        self.assertFalse(receipt["assembly_outcome_journal_complete"])
        self.assertFalse(receipt["complete_for_existing_allowlisted_files"])
        self.assertEqual(receipt["excluded_files"], [{"name": name,
            "reason": "assembly_outcome_not_written_by_this_attempt"}])
        self.assertEqual((self.evidence / name).read_bytes(), original)
        self.assertFalse((self.staged / name).exists())
        self.assertFalse(self.publish())
        self.assertFalse((self.published / name).exists())

    def test_existing_stage_or_publication_is_never_replaced(self):
        self.stage()
        before = (self.staged / "technical-custody.json").read_bytes()
        with self.assertRaises(pub.PublicationError):
            self.stage()
        self.assertEqual((self.staged / "technical-custody.json").read_bytes(), before)
        self.assertTrue(self.publish())
        published = (self.published / "custody-status.json").read_bytes()
        with self.assertRaises(pub.PublicationError):
            self.publish()
        self.assertEqual((self.published / "custody-status.json").read_bytes(), published)

    def test_wrong_source_or_attempt_binding_refuses_all_raw_uploads(self):
        self.stage()
        changed = dict(self.context, run_attempt="2")
        self.assertFalse(self.publish(changed))
        self.assertEqual({path.name for path in self.published.iterdir()}, {"custody-status.json"})

    def test_no_stage_after_a_skipped_native_step_does_not_invent_evidence(self):
        complete = wrapper.verify_technical_custody(
            self.staged, self.published, "final-mp4", self.context, "skipped",
        )
        self.assertTrue(complete)  # Status recording succeeded; no media claim.
        report = json.loads((self.published / "custody-status.json").read_bytes())
        self.assertEqual(report["state"], "NO_STAGED_ORIGINALS")
        self.assertFalse(report["native_execution_inferred"])
        self.assertEqual({path.name for path in self.published.iterdir()}, {"custody-status.json"})

    def test_missing_ticket_blocks_payloads_and_does_not_restart_owner(self):
        (self.owner / "runtime" / "motionwright.session").unlink()
        with patch.object(wrapper.subprocess, "Popen", side_effect=AssertionError("no process allowed")) as process:
            receipt = self.stage()
        process.assert_not_called()
        self.assertFalse(receipt["original_ticket_checked_in_memory"])
        self.assertEqual(receipt["included_files"], [])
        self.assertFalse(self.publish())

    def test_attempt_two_is_rejected_before_git_or_any_native_effect(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ATTEMPT": "2"}), \
                patch.object(wrapper, "GITHUB_SHA", "a" * 40), \
                patch.object(wrapper.subprocess, "check_output") as git:
            with self.assertRaisesRegex(AssertionError, "never replay"):
                wrapper.exact_inputs()
        git.assert_not_called()

    def test_symlink_in_allowlisted_media_is_not_followed(self):
        name = "native-multisegment-master.mp4"
        path = self.evidence / name
        path.unlink()
        private = self.owner / "secret-media"
        private.write_bytes(self.ticket)
        try:
            path.symlink_to(private)
        except OSError:
            self.skipTest("Creating a real symlink requires filesystem support and permission")
        receipt = self.stage()
        self.assertFalse(receipt["complete_for_existing_allowlisted_files"])
        self.assertFalse((self.staged / name).exists())
        self.assertEqual(private.read_bytes(), self.ticket)


if __name__ == "__main__":
    unittest.main()
