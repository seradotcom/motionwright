"""Source and OS descriptor regressions; native callbacks and module attestations are doubles."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_g2_observation as g2
from native_av_failure_guard import NativeCommandFailure, classify_native_command_failure
from native_mlt_sequence_e2e import inspect_decoded_ci_stereo


def source_wave() -> tuple[bytes, bytes]:
    pcm = b"".join(struct.pack("<hh", ((i % 120) - 60) * 250, ((i % 80) - 40) * 260)
                   for i in range(52_800))
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(48_000)
        writer.writeframes(pcm)
    packed = output.getvalue()
    assert hashlib.sha256(packed).hexdigest() == g2.REFERENCE_WAV_SHA256
    return packed, pcm


class ObserverDouble:
    def __init__(self, *, start_error=None, stop_error=None):
        self.started = False
        self.stopped = False
        self.start_error = start_error
        self.stop_error = stop_error
        self.outputs = {}

    def start(self):
        if self.start_error:
            raise self.start_error
        self.started = True

    def stop(self):
        self.stopped = True
        if self.stop_error:
            raise self.stop_error

    def close(self):
        pass

    def require_one(self, master):
        raise g2.G2Unverified("test original live witness absent")


class NativeTerminalCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.evidence = self.root / "private"
        self.evidence.mkdir()
        self.output = self.root / "owner"
        self.output.mkdir()
        self.wav, self.pcm = source_wave()
        self.session = g2.G2Session(
            {"known_melt_sha256": "a" * 64}, self.evidence, self.wav,
            {"test_double": True, "native_effect_executed": False},
        )
        self.master = self.output / "master.mp4"
        # This is a custody test double, never a decoded or accepted MP4.
        self.master.write_bytes(b"test-double-native-result-bytes")
        self.result = {
            "native_mlt_multisegment_e2e": "PASS",
            "master_artifact_path": "master.mp4",
            "master_artifact_sha256": g2.sha(self.master.read_bytes()),
            "master_artifact_bytes": self.master.stat().st_size,
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_observer_starts_before_the_single_callback_and_preserves_exact_result(self):
        observer = ObserverDouble()
        calls = []
        def original():
            self.assertTrue(observer.started)
            self.assertFalse(observer.stopped)
            calls.append("one")
            return self.result
        with mock.patch.object(g2, "LiveObserver", return_value=observer), self.session:
            returned = self.session.run_original(original, 100, self.output)
            self.assertIs(returned, self.result)
            self.assertIs(self.session.native_result, self.result)
            self.assertEqual((self.evidence / "original-native-master.mp4").read_bytes(),
                             self.master.read_bytes())
        self.assertEqual(calls, ["one"])
        self.assertTrue(observer.stopped)
        receipt = json.loads((self.evidence / "final-custody.json").read_bytes())
        self.assertEqual(receipt["native_result"], self.result)
        self.assertFalse(receipt["source_content_and_live_module_verified"])

    def test_second_invocation_is_refused_even_after_a_successful_callback(self):
        callback = mock.Mock(return_value=self.result)
        with mock.patch.object(g2, "LiveObserver", return_value=ObserverDouble()):
            self.session.run_original(callback, 100, self.output)
            with self.assertRaisesRegex(g2.G2Unverified, "only_be_invoked_once"):
                self.session.run_original(callback, 100, self.output)
        callback.assert_called_once_with()

    def test_observer_start_failure_does_not_invoke_native_callback(self):
        callback = mock.Mock(return_value=self.result)
        observer = ObserverDouble(start_error=OSError("test observer unavailable"))
        with mock.patch.object(g2, "LiveObserver", return_value=observer):
            with self.assertRaises(OSError):
                self.session.run_original(callback, 100, self.output)
        callback.assert_not_called()
        self.assertIsNone(self.session.native_result)

    def test_unknown_error_survives_stop_custody_and_stderr_failures_without_replay(self):
        failure = NativeCommandFailure(classify_native_command_failure(
            stdout=b"", stderr=b"", exit_code=None, deadline_exceeded=True,
        ))
        callback = mock.Mock(side_effect=failure)
        observer = ObserverDouble(stop_error=OSError("secondary stop"))
        with mock.patch.object(g2, "LiveObserver", return_value=observer), \
             mock.patch.object(g2, "new_json", side_effect=OSError("secondary disk")), \
             mock.patch.object(g2.os, "write", side_effect=OSError("secondary stderr")):
            with self.assertRaises(NativeCommandFailure) as raised:
                with self.session:
                    self.session.run_original(callback, 100, self.output)
        self.assertIs(raised.exception, failure)
        self.assertIs(self.session.native_error, failure)
        callback.assert_called_once_with()
        self.assertIsNone(self.session.native_result)

    def test_native_error_survives_a_later_cleanup_exception(self):
        original_error = NativeCommandFailure(classify_native_command_failure(
            stdout=b"", stderr=b"", exit_code=None, deadline_exceeded=True,
        ))
        callback = mock.Mock(side_effect=original_error)
        with mock.patch.object(g2, "LiveObserver", return_value=ObserverDouble()):
            with self.assertRaises(AssertionError) as raised:
                with self.session:
                    try:
                        self.session.run_original(callback, 100, self.output)
                    finally:
                        raise OSError("secondary cleanup")
        self.assertIs(raised.exception, original_error)
        receipt = json.loads((self.evidence / "final-custody.json").read_bytes())
        self.assertEqual(receipt["terminal_scope"], "canonical_assemble_command")
        self.assertEqual(receipt["canonical_failure_diagnostic"], original_error.diagnostic)
        self.assertEqual(receipt["canonical_failure_diagnostic"]["source_outcome"], "UNKNOWN")
        callback.assert_called_once_with()

    def test_success_result_remains_attached_when_later_observation_and_custody_fail(self):
        later = OSError("post-native reopen failed")
        callback = mock.Mock(return_value=self.result)
        with mock.patch.object(g2, "LiveObserver", return_value=ObserverDouble()), \
             mock.patch.object(g2, "new_json", side_effect=OSError("custody unavailable")), \
             mock.patch.object(g2.os, "write", side_effect=OSError("stderr unavailable")):
            with self.assertRaises(OSError) as raised:
                with self.session:
                    self.session.run_original(callback, 100, self.output)
                    raise later
        self.assertIs(raised.exception, later)
        self.assertIs(raised.exception.motionwright_native_result, self.result)
        self.assertIs(self.session.native_result, self.result)
        self.assertEqual((self.evidence / "original-native-master.mp4").read_bytes(),
                         self.master.read_bytes())
        callback.assert_called_once_with()

    def test_final_custody_failure_cannot_become_overall_success(self):
        callback = mock.Mock(return_value=self.result)
        with mock.patch.object(g2, "LiveObserver", return_value=ObserverDouble()):
            self.session.run_original(callback, 100, self.output)
        with mock.patch.object(g2, "new_json", side_effect=OSError("final custody failure")):
            with self.assertRaisesRegex(g2.G2Unverified, "final_custody_failed") as raised:
                with self.session:
                    pass
        self.assertIs(raised.exception.native_result, self.result)
        callback.assert_called_once_with()

    def test_stale_foreign_result_path_cannot_copy_an_outside_file(self):
        outside = self.root / "foreign.mp4"
        outside.write_bytes(b"foreign")
        result = {**self.result, "master_artifact_path": "../foreign.mp4"}
        callback = mock.Mock(return_value=result)
        with mock.patch.object(g2, "LiveObserver", return_value=ObserverDouble()):
            returned = self.session.run_original(callback, 100, self.output)
        self.assertIs(returned, result)
        self.assertIs(self.session.native_result, result)
        self.assertFalse((self.evidence / "original-native-master.mp4").exists())
        self.assertIn("reported_native_master_custody", self.session.secondary_failures)
        self.assertEqual(outside.read_bytes(), b"foreign")

    def test_missing_live_witness_does_not_discard_the_unique_original_pcm(self):
        callback = mock.Mock(return_value=self.result)
        with mock.patch.object(g2, "LiveObserver", return_value=ObserverDouble()):
            self.session.run_original(callback, 100, self.output)
        self.session.retain_master(self.master, self.result["master_artifact_sha256"],
                                   self.result["master_artifact_bytes"])
        proof = self.session.inspect_pcm(self.pcm)
        self.assertTrue(all(row["state"] == "PASS" for row in proof["oracles"].values()))
        self.assertFalse(proof["physical_AV_master_acceptance"])
        self.assertFalse(proof["packet_PTS_and_AAC_priming_verified"])
        self.assertEqual((self.evidence / "original-master-decoded.s16le").read_bytes(), self.pcm)
        with self.assertRaisesRegex(g2.G2Unverified, "evidence_chain_incomplete"):
            self.session.complete()
        callback.assert_called_once_with()

    def test_known_right_tail_loss_remains_red_despite_legacy_midstream_pass(self):
        damaged = bytearray(self.pcm)
        for index in range(52_544, 52_800):
            struct.pack_into("<h", damaged, index * 4 + 2, 0)
        damaged = bytes(damaged)
        self.assertEqual(inspect_decoded_ci_stereo(damaged)["decoded_stereo_content"],
                         "distinct_source_400hz_left_600hz_right")
        self.session.native_result = self.result
        self.session.master_sha = self.result["master_artifact_sha256"]
        with self.assertRaisesRegex(g2.G2Unverified, "full_source_content_failed"):
            self.session.inspect_pcm(damaged)
        proof = json.loads((self.evidence / "source-content.json").read_bytes())
        self.assertEqual({name: value["state"] for name, value in proof["oracles"].items()},
                         {"full_source_content": "FAIL", "fixed_fixture_envelope": "FAIL"})
        self.assertEqual((self.evidence / "original-master-decoded.s16le").read_bytes(), damaged)
        self.assertIs(self.session.native_result, self.result)
        self.assertFalse(proof["human_acceptance"])

    def test_packet_document_is_retained_without_inventing_pts_approval(self):
        raw = json.dumps({"streams": [{"codec_name": "aac"}],
                          "packets": [{"pts": -1024, "duration": 1024}]}).encode()
        self.session.retain_packet_document(raw)
        self.assertEqual((self.evidence / "original-master-packets.json").read_bytes(), raw)
        self.assertFalse(self.session.packet_observation["packet_PTS_and_AAC_priming_verified"])

    def test_exclusive_custody_preserves_an_existing_file(self):
        path = self.evidence / "prior.json"
        path.write_bytes(b"preserved")
        with self.assertRaises(FileExistsError):
            g2.new_json(path, {"untrusted": "replacement"})
        self.assertEqual(path.read_bytes(), b"preserved")


class ObserverIdentityTests(unittest.TestCase):
    def test_daemon_pid_reuse_cannot_supply_ancestry(self):
        with mock.patch.object(g2, "process_identity",
                               side_effect=lambda pid: {41: (20, 9), 20: (1, 101)}[pid]):
            self.assertFalse(g2.descendant_of(41, (20, 100)))
            self.assertTrue(g2.descendant_of(41, (20, 101)))

    def test_foreign_and_cyclic_parent_chains_do_not_belong_to_owner(self):
        with mock.patch.object(g2, "process_identity",
                               side_effect=lambda pid: {41: (42, 10), 42: (41, 11)}[pid]):
            self.assertFalse(g2.descendant_of(41, (20, 100)))
        with mock.patch.object(g2, "process_identity", return_value=(1, 10)):
            self.assertFalse(g2.descendant_of(41, (20, 100)))

    def test_consumer_selection_is_scoped_and_delegates_exact_argv_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "master.mp4"
            payload = ("/usr/bin/melt\0timeline.mlt\0-consumer\0avformat:" + str(output) + "\0").encode()
            with mock.patch.object(g2.loaded, "verified_melt_cmdline") as validate:
                self.assertEqual(g2.selected_consumer(payload, root), output)
                validate.assert_called_once_with(payload, str(output))
            outside = ("/usr/bin/melt\0timeline.mlt\0-consumer\0avformat:"
                       + str(root.parent / "foreign.mp4") + "\0").encode()
            self.assertIsNone(g2.selected_consumer(outside, root))

    def test_absent_ambiguous_or_foreign_output_receipt_never_becomes_native_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            master = Path(temporary).resolve() / "master.mp4"
            master.write_bytes(b"test double")
            observer = object.__new__(g2.LiveObserver)
            observer.fatal = False
            observer.output_root = master.parent
            observer.consumer_files = {}
            observer.candidates = set()
            observer.receipts = {}
            with self.assertRaises(g2.G2Unverified):
                observer.require_one(master)
            observer.candidates = {(41, 10), (42, 11)}
            observer.receipts = {(41, 10): {"output_path": str(master)}}
            with self.assertRaises(g2.G2Unverified):
                observer.require_one(master)
            observer.candidates = {(41, 10)}
            observer.receipts = {(41, 10): {"output_path": str(master.parent / "other.mp4")}}
            with self.assertRaisesRegex(g2.G2Unverified, "not_native_result_artifact"):
                observer.require_one(master)

    def test_changed_original_archive_is_rejected_before_any_member_is_admitted(self):
        with self.assertRaisesRegex(g2.G2Unverified, "artifact_zip_changed"):
            g2.reference_source(b"another archive with the same claimed filename")


class InputEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = "c" * 40
        self.inputs = {key: str(self.root / key) for key in g2.INPUT_KEYS}
        self.inputs.update({
            "schema": g2.SCHEMA, "motionwright_sha": self.source,
            "semwright_sha": g2.SEMWRIGHT_PIN, "run_id": "123456", "run_attempt": "1",
            "known_original_module_sha256": "1" * 64, "known_melt_sha256": "2" * 64,
            "official_runtime_receipt_sha256": "3" * 64,
            "compiled_module_sha256": g2.REPAIRED_MODULE_SHA256,
            "compiler_receipt_sha256": g2.COMPILER_RECEIPT_SHA256,
            "installation_receipt_sha256": "4" * 64,
        })
        self.path = self.root / "inputs.json"
        self.env = {
            "GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
        }

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, *, mode="final-mp4"):
        self.path.write_text(json.dumps(self.inputs), encoding="utf-8")
        with mock.patch.object(g2.admission, "disposable_linux_runner", return_value=self.root), \
             mock.patch.dict(g2.os.environ, self.env):
            return g2.G2Session.prepare(self.path, source_sha=self.source,
                                        semwright_sha=g2.SEMWRIGHT_PIN, mode=mode)

    def test_nonfinal_mode_cannot_enter_the_g2_path(self):
        with self.assertRaisesRegex(g2.G2Unverified, "single_final_mp4"):
            self.prepare(mode="captioned-mp4")

    def test_rerun_attempt_cannot_be_used_as_an_automatic_replay(self):
        self.inputs["run_attempt"] = "2"
        self.env["GITHUB_RUN_ATTEMPT"] = "2"
        with self.assertRaisesRegex(g2.G2Unverified, "execution_binding"):
            self.prepare()

    def test_pull_request_cannot_enable_this_one_shot_native_envelope(self):
        self.env["GITHUB_EVENT_NAME"] = "pull_request"
        with self.assertRaisesRegex(g2.G2Unverified, "execution_binding"):
            self.prepare()

    def test_missing_and_unexpected_input_fields_are_refused(self):
        del self.inputs["known_original_module_sha256"]
        self.inputs["verified"] = True
        with self.assertRaisesRegex(g2.G2Unverified, "inputs_schema"):
            self.prepare()

    def test_official_receipt_label_cannot_replace_authenticated_bytes(self):
        official = Path(self.inputs["official_runtime_receipt_path"])
        official.write_text(json.dumps({"schema": g2.OFFICIAL_SCHEMA, "verified": True}))
        with mock.patch.object(g2.admission, "source_compiler_closure") as closure:
            with self.assertRaisesRegex(g2.G2Unverified, "official_runtime_receipt_changed"):
                self.prepare()
            closure.assert_not_called()

    def test_official_digest_with_different_binary_bindings_is_still_refused(self):
        raw = json.dumps({"schema": g2.OFFICIAL_SCHEMA, "runtime_bindings": {
            "original_module": {"installed_path": str(g2.admission.INSTALLED), "sha256": "0" * 64},
            "melt": {"installed_path": str(g2.MELT), "sha256": self.inputs["known_melt_sha256"]},
        }}).encode()
        Path(self.inputs["official_runtime_receipt_path"]).write_bytes(raw)
        self.inputs["official_runtime_receipt_sha256"] = g2.sha(raw)
        with mock.patch.object(g2.admission, "source_compiler_closure") as closure:
            with self.assertRaisesRegex(g2.G2Unverified, "official_runtime_binary_bindings"):
                self.prepare()
            closure.assert_not_called()


class OriginalConsumerFileTests(unittest.TestCase):
    def test_private_consumer_selection_requires_the_pinned_layout(self):
        root = Path("/owner-output")
        output = Path("/tmp/semwright-video-" + "a" * 32 + "/partial.mp4")
        def argv(path):
            return ("/usr/bin/melt\0timeline.mlt\0-consumer\0avformat:" + str(path) + "\0").encode()
        with mock.patch.object(g2.loaded, "verified_melt_cmdline") as validate:
            self.assertEqual(g2.selected_consumer(argv(output), root), output)
            validate.assert_called_once_with(argv(output), str(output))
        for path in (
            "/tmp/unrelated/partial.mp4",
            "/tmp/semwright-video-" + "a" * 31 + "/partial.mp4",
            "/tmp/semwright-video-" + "A" * 32 + "/partial.mp4",
            "/tmp/semwright-video-" + "a" * 32 + "/child/partial.mp4",
            "/tmp/semwright-video-" + "a" * 32 + "/partial.wav",
        ):
            with self.subTest(path=path):
                self.assertIsNone(g2.selected_consumer(argv(path), root))

    @unittest.skipUnless(sys.platform == "linux", "requires the real Linux procfs FD bridge")
    def test_live_fd_survives_publication_unlink_and_path_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            stage = root / "partial.mp4"
            master = root / "published.mp4"
            original = b"original source-only custody fixture"
            stage.write_bytes(original)
            with stage.open("rb") as native_fd:
                self.assertGreaterEqual(native_fd.fileno(), 0)
                ticks = g2.process_identity(os.getpid())[1]
                held = g2.OriginalConsumerFile.capture(os.getpid(), ticks, stage)
                try:
                    master.write_bytes(stage.read_bytes())
                    stage.unlink()
                    stage.write_bytes(b"foreign replacement must never be reopened")
                    binding = held.verify(g2.sha(master.read_bytes()), master.stat().st_size)
                    self.assertEqual(binding["inode"], os.fstat(native_fd.fileno()).st_ino)
                    self.assertNotEqual(binding["inode"], stage.stat().st_ino)
                    self.assertFalse(binding["staging_path_reopened"])
                    self.assertEqual(binding["sha256"], g2.sha(original))
                finally:
                    held.close()

    def test_final_publication_must_equal_the_retained_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary) / "partial.mp4"
            stage.write_bytes(b"original")
            descriptor = os.open(stage, os.O_RDONLY)
            held = g2.OriginalConsumerFile(descriptor, os.fstat(descriptor), 41, 10)
            try:
                with self.assertRaisesRegex(g2.G2Unverified, "differs_from_published"):
                    held.verify(g2.sha(b"replaced"), len(b"replaced"))
            finally:
                held.close()

            evidence = Path(temporary) / "evidence"
            evidence.mkdir()
            original = stage.read_bytes()
            expected = g2.sha(original)
            (evidence / "original-native-master.mp4").write_bytes(original)
            session = g2.G2Session({}, evidence, b"", {"test_double": True})
            session.native_result = {
                "test_double": True,
                "master_artifact_sha256": expected,
                "master_artifact_bytes": len(original),
            }
            session.master_sha = expected
            session.observer = mock.Mock()
            session.observer.require_one.return_value = {
                "publication_binding": {"sha256": g2.sha(b"replaced"), "bytes": len(original)},
            }
            session.retain_master(stage, expected, len(original))
            self.assertIsNone(session.live_proof)
            self.assertIn("original_live_module_witness", session.secondary_failures)
            self.assertFalse((evidence / "original-live-module.json").exists())

    def test_descriptor_mutation_during_final_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary) / "partial.mp4"
            original = b"original"
            stage.write_bytes(original)
            descriptor = os.open(stage, os.O_RDONLY)
            held = g2.OriginalConsumerFile(descriptor, os.fstat(descriptor), 41, 10)
            real_read = os.read
            changed = False
            def mutate(fd, limit):
                nonlocal changed
                block = real_read(fd, limit)
                if not changed:
                    changed = True
                    with stage.open("ab") as writer:
                        writer.write(b"changed")
                return block
            try:
                with mock.patch.object(g2.os, "read", side_effect=mutate):
                    with self.assertRaisesRegex(g2.G2Unverified, "changed_during_read"):
                        held.verify(g2.sha(original), len(original))
            finally:
                held.close()

    def test_pid_reuse_during_capture_closes_every_retained_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            directory = proc / "41" / "fd"
            directory.mkdir(parents=True)
            entry = directory / "7"
            entry.write_bytes(b"original")
            output = Path("/tmp/semwright-video-" + "a" * 32 + "/partial.mp4")
            opened = []
            real_open = os.open
            def track_open(path, flags):
                descriptor = real_open(path, flags)
                opened.append(descriptor)
                return descriptor
            with mock.patch.object(g2, "Path", return_value=proc), \
                 mock.patch.object(g2, "process_identity", side_effect=[(20, 10), (20, 11)]), \
                 mock.patch.object(g2.os, "readlink", return_value=str(output)), \
                 mock.patch.object(g2.os, "getuid", return_value=entry.stat().st_uid, create=True), \
                 mock.patch.object(g2.os, "O_CLOEXEC", getattr(os, "O_CLOEXEC", 0), create=True), \
                 mock.patch.object(g2.os, "O_NONBLOCK", getattr(os, "O_NONBLOCK", 0), create=True), \
                 mock.patch.object(g2.os, "open", side_effect=track_open):
                with self.assertRaisesRegex(g2.G2Unverified, "process_identity_changed"):
                    g2.OriginalConsumerFile.capture(41, 10, output)
            self.assertEqual(len(opened), 1)
            with self.assertRaises(OSError):
                os.fstat(opened[0])

    def test_private_staging_is_joined_to_owned_publication_by_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            stage = root / "partial.mp4"
            master = root / "owner" / "master.mp4"
            master.parent.mkdir()
            stage.write_bytes(b"source-only publication fixture")
            master.write_bytes(stage.read_bytes())
            descriptor = os.open(stage, os.O_RDONLY)
            held = g2.OriginalConsumerFile(descriptor, os.fstat(descriptor), 41, 10)
            observer = object.__new__(g2.LiveObserver)
            observer.fatal = False
            observer.output_root = master.parent
            observer.candidates = {(41, 10)}
            observer.receipts = {(41, 10): {
                "output_path": "/tmp/semwright-video-" + "a" * 32 + "/partial.mp4",
                "live_module": {"test_double": True, "native_effect_executed": False},
            }}
            observer.consumer_files = {(41, 10): held}
            try:
                proof = observer.require_one(master)
                self.assertEqual(proof["publication_binding"]["sha256"], g2.sha(master.read_bytes()))
                self.assertEqual(proof["published_output_path"], str(master))
                foreign = root / "foreign.mp4"
                foreign.write_bytes(master.read_bytes())
                with self.assertRaisesRegex(g2.G2Unverified, "not_native_result_artifact"):
                    observer.require_one(foreign)
            finally:
                held.close()


if __name__ == "__main__":
    unittest.main()
