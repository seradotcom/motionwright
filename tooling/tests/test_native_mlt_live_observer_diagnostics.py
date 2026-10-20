"""Offline regressions for private diagnostics around the one original observer.

All process inventories, native attestations and native callbacks are doubles.
No renderer, subprocess, privilege change or external service is used.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_g2_observation as g2
from native_av_failure_guard import NativeCommandFailure, classify_native_command_failure


class DiagnosticReadTests(unittest.TestCase):
    def test_raw_read_retains_binary_bytes_and_sha(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "raw"
            raw = bytes(range(256)) + b"\r\n\x1a"
            path.write_bytes(raw)
            value = g2.diagnostic_read(path, 1024)
            self.assertEqual(base64.b64decode(value["raw"]), raw)
            self.assertEqual(value["retained_sha256"], g2.sha(raw))
            self.assertEqual(value["retained_bytes"], len(raw))
            self.assertTrue(value["complete_read"])

    def test_oversized_raw_read_is_a_labelled_prefix_not_a_full_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "raw"
            path.write_bytes(b"a" * 100)
            value = g2.diagnostic_read(path, 7)
            self.assertEqual(base64.b64decode(value["raw"]), b"a" * 7)
            self.assertEqual(value["retained_sha256"], g2.sha(b"a" * 7))
            self.assertEqual(value["retained_bytes"], 7)
            self.assertFalse(value["complete_read"])

    def test_absent_raw_file_retains_the_original_errno(self):
        with tempfile.TemporaryDirectory() as temporary:
            value = g2.diagnostic_read(Path(temporary) / "absent", 7)
            self.assertFalse(value["complete_read"])
            self.assertEqual(value["error"]["exception_type"], "FileNotFoundError")
            self.assertEqual(value["error"]["errno"], 2)
            self.assertNotIn("raw", value)

    def test_exception_message_has_a_byte_bound_and_keeps_type_errno(self):
        value = g2.bounded_error(PermissionError(13, "\u00e9" * 5000))
        self.assertEqual(value["exception_type"], "PermissionError")
        self.assertEqual(value["errno"], 13)
        self.assertTrue(value["message_truncated"])
        self.assertLessEqual(len(value["message"].encode()), 4098)

    def test_path_and_root_ownership_are_observations_not_identity_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            value = g2.diagnostic_stat(root)
            info = root.stat()
            self.assertEqual(value["path"], str(root))
            self.assertEqual((value["uid"], value["gid"], value["device"], value["inode"]),
                             (info.st_uid, info.st_gid, info.st_dev, info.st_ino))


class LiveScanDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        self.master = self.output / "master.mp4"
        self.master.write_bytes(b"test-double-result")
        self.melt = self.root / "melt-7"
        self.melt.write_bytes(b"test-double-executable")
        self.uid = self.root.stat().st_uid
        self.executables = {}
        self.add_process(20, 1, 100, "semwrightd", str(self.root / "semwrightd"))
        self.add_process(41, 20, 200, "melt-7", str(self.melt))
        self.patches = [
            mock.patch.object(g2, "PROC_ROOT", self.proc),
            mock.patch.object(g2, "host_tool_contract", return_value={"test_double": True}),
            mock.patch.object(g2, "MELT", self.melt),
            mock.patch.object(g2.os, "getuid", return_value=self.uid, create=True),
            mock.patch.object(g2.os, "geteuid", return_value=self.uid, create=True),
            mock.patch.object(g2.os, "readlink", side_effect=self.readlink),
            mock.patch.object(g2.loaded, "verified_melt_cmdline",
                              return_value={"test_double": True}),
            mock.patch.object(g2.loaded, "inspect_live_original",
                              return_value={"test_double": True, "native_effect": False}),
        ]
        self.active = [patch.start() for patch in self.patches]
        for patch in self.patches:
            self.addCleanup(patch.stop)
        self.attest = self.active[-1]
        self.observer = g2.LiveObserver(20, self.output, "a" * 64)

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temporary.cleanup()

    def set_start(self, pid, parent, start):
        fields = ["S", str(parent)] + ["0"] * 17 + [str(start)]
        (self.proc / str(pid) / "stat").write_bytes(
            (str(pid) + " (melt-7) " + " ".join(fields)).encode())

    def add_process(self, pid, parent, start, name, executable, output=None):
        path = self.proc / str(pid)
        path.mkdir()
        (path / "root").mkdir()
        self.executables[pid] = executable
        self.set_start(pid, parent, start)
        (path / "comm").write_bytes(name.encode() + b"\n")
        (path / "status").write_bytes(("Name:\t" + name + "\nUid:\t1001\t1001\t1001\t1001\n").encode())
        (path / "uid_map").write_bytes(b"0 1001 1\n")
        (path / "gid_map").write_bytes(b"0 1001 1\n")
        (path / "maps").write_bytes(b"test-double-maps-not-attestation\n")
        (path / "environ").write_bytes(b"LANG=C\0")
        selected = self.master if output is None else output
        (path / "cmdline").write_bytes(
            ("melt-7\0timeline.mlt\0-consumer\0avformat:" + str(selected) + "\0").encode())

    def readlink(self, path):
        path = Path(path)
        if path.name == "exe" and path.parent.name.isdecimal():
            value = self.executables.get(int(path.parent.name), FileNotFoundError(2, "test-double-process-absent"))
            if isinstance(value, BaseException):
                raise value
            return value
        if path.name in {"root", "cwd"}:
            return str(self.root)
        if path.parent.name == "ns":
            return path.name + ":[test-double]"
        raise OSError(2, "test-double-link-absent")

    def stages(self):
        return {row["stage"]: row for row in self.observer.diagnostic()["events"]}

    def test_witness_stays_strict_and_is_not_issued_by_the_diagnostic(self):
        self.observer.scan()
        value = self.observer.diagnostic()
        self.assertEqual(value["candidates"], [[41, 200]])
        self.assertEqual(value["receipts"][0]["receipt"]["output_path"], str(self.master))
        self.assertFalse(value["module_attestation_inferred"])
        self.assertFalse(value["native_operation_retried"])
        self.assertEqual(self.observer.require_one(self.master)["pid"], 41)
        self.attest.assert_called_once_with(41, self.melt, "a" * 64, g2.admission.INSTALLED,
                                           g2.REPAIRED_MODULE_SHA256, str(self.master))
        raw = self.stages()["module_witness_retained"]["process"]["raw_reads"]
        self.assertEqual(base64.b64decode(raw["maps"]["raw"]), b"test-double-maps-not-attestation\n")
        self.assertEqual(base64.b64decode(raw["environ"]["raw"]), b"LANG=C\0")

    def test_unreadable_melt_executable_retains_permission_error_without_attestation(self):
        self.executables[41] = PermissionError(13, "original exe denied")
        self.observer.scan()
        value = self.stages()["executable_unreadable"]
        self.assertEqual(value["error"]["errno"], 13)
        self.assertIn("original exe denied", value["error"]["message"])
        self.assertEqual(value["process"]["proc"]["path"], str(self.proc / "41"))
        self.assertIn("uid", value["process"]["root"])
        self.attest.assert_not_called()
        with self.assertRaisesRegex(g2.G2Unverified, "not_uniquely_witnessed"):
            self.observer.require_one(self.master)

    def test_different_melt_path_is_recorded_but_not_accepted_by_name(self):
        self.executables[41] = "/sandbox/tools/melt-7"
        self.observer.scan()
        row = self.stages()["executable_mismatch"]
        self.assertEqual(row["detail"]["observed_executable"], "/sandbox/tools/melt-7")
        self.assertEqual(row["detail"]["expected_executable"], str(self.melt))
        self.assertEqual(self.observer.candidates, set())
        self.attest.assert_not_called()

    def test_uid_rejection_retains_observer_and_proc_root_uids(self):
        with mock.patch.object(g2.os, "getuid", return_value=self.uid + 1001, create=True):
            self.observer.scan()
        row = self.stages()["uid_mismatch"]
        self.assertEqual(row["detail"]["required_uid"], self.uid + 1001)
        self.assertEqual(row["process"]["proc"]["uid"], self.uid)
        self.assertEqual(self.observer.diagnostic()["scope"]["proc_root"]["path"], str(self.proc))
        self.attest.assert_not_called()

    def test_foreign_parent_chain_is_not_a_witness(self):
        self.set_start(41, 1, 200)
        self.observer.scan()
        self.assertIn("not_daemon_descendant", self.stages())
        self.assertFalse(self.observer.candidates)
        self.attest.assert_not_called()

    def test_original_cmdline_outside_owner_root_is_retained_but_rejected(self):
        outside = self.root / "foreign.mp4"
        raw = ("melt-7\0timeline.mlt\0-consumer\0avformat:" + str(outside) + "\0").encode()
        (self.proc / "41" / "cmdline").write_bytes(raw)
        self.observer.scan()
        row = self.stages()["consumer_unselected"]
        self.assertEqual(base64.b64decode(row["process"]["raw_reads"]["cmdline"]["raw"]), raw)
        self.assertFalse(self.observer.candidates)
        self.attest.assert_not_called()

    def test_exact_loaded_module_rejection_is_preserved_with_candidate_identity(self):
        self.attest.side_effect = g2.loaded.LoadedModuleUnverified(
            "mapped_native_MLT_inode_or_device_changed")
        self.observer.scan()
        row = self.stages()["loaded_module_attestation"]
        self.assertEqual(row["error"]["message"], "mapped_native_MLT_inode_or_device_changed")
        self.assertEqual(row["error"]["exception_type"], "LoadedModuleUnverified")
        self.assertEqual(self.observer.diagnostic()["candidates"], [[41, 200]])
        self.assertEqual(self.observer.receipts, {})
        with self.assertRaises(g2.G2Unverified):
            self.observer.require_one(self.master)

    def test_repeated_reads_are_counted_without_repeating_native_effects_or_unbounded_samples(self):
        self.attest.side_effect = g2.loaded.LoadedModuleUnverified("module not mapped yet")
        self.observer.scan()
        self.observer.scan()
        row = self.stages()["loaded_module_attestation"]
        self.assertEqual(row["observations"], 2)
        self.assertEqual((row["first_scan"], row["last_scan"]), (1, 2))
        self.assertEqual(self.observer.diagnostic()["filter_counts"]["loaded_module_attestation_error"], 2)
        self.assertEqual(self.attest.call_count, 2)
        self.assertFalse(self.observer.receipts)

    def test_successful_same_pid_attestation_is_retained_once(self):
        self.observer.scan()
        self.observer.scan()
        self.attest.assert_called_once()
        self.assertEqual(self.observer.diagnostic()["filter_counts"]["witness_already_retained"], 1)

    def test_pid_generation_change_after_attestation_remains_fatal(self):
        def changed(*_args):
            self.set_start(41, 20, 201)
            return {"test_double": True}
        self.attest.side_effect = changed
        self.observer.scan()
        self.assertTrue(self.observer.fatal)
        self.assertEqual(self.stages()["loaded_module_attestation"]["error"]["message"],
                         "g2_native_process_identity_changed")
        self.assertFalse(self.observer.receipts)

    def test_two_original_candidates_remain_ambiguous_with_both_outputs_retained(self):
        second = self.output / "second.mp4"
        self.add_process(42, 20, 300, "melt-7", str(self.melt), second)
        self.observer.scan()
        self.assertTrue(self.observer.fatal)
        self.assertEqual(self.observer.diagnostic()["candidates"], [[41, 200], [42, 300]])
        self.assertEqual({row["output"] for row in self.observer.diagnostic()["outputs"]},
                         {str(self.master), str(second)})
        with self.assertRaises(g2.G2Unverified):
            self.observer.require_one(self.master)

    def test_preexisting_consumer_stops_before_watcher_or_original_dispatch(self):
        with mock.patch.object(g2.threading, "Thread") as thread:
            with self.assertRaisesRegex(g2.G2Unverified, "preexists_original_assemble"):
                self.observer.start()
            thread.assert_not_called()
        self.assertEqual(self.stages()["observer_start"]["error"]["message"],
                         "g2_consumer_preexists_original_assemble")

    def test_watch_exception_is_recorded_and_never_retried(self):
        class ThreadDouble:
            def __init__(self, target, **_kwargs):
                self.target = target
            def start(self):
                self.target()
            def is_alive(self):
                return False
        with mock.patch.object(self.observer, "scan",
                               side_effect=[None, RuntimeError("original scan exception")]) as scan, \
             mock.patch.object(g2.threading, "Thread", ThreadDouble):
            self.observer.start()
        self.assertTrue(self.observer.fatal)
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(self.stages()["observer_watch"]["error"]["message"],
                         "original scan exception")

    def test_event_and_byte_budgets_mark_dropped_evidence(self):
        with mock.patch.object(g2, "MAX_OBSERVER_EVENTS", 1):
            self.observer.record("first", error=ValueError("a"))
            self.observer.record("second", error=ValueError("b"))
        self.assertEqual(len(self.observer.diagnostic()["events"]), 1)
        self.assertEqual(self.observer.diagnostic()["dropped_events"], 1)
        self.assertFalse(self.observer.diagnostic()["bounded_journal_complete"])
        with mock.patch.object(g2, "MAX_OBSERVER_DIAGNOSTIC_BYTES", 1):
            self.observer.record("third", error=ValueError("c"))
        self.assertEqual(len(self.observer.diagnostic()["events"]), 1)
        self.assertEqual(self.observer.diagnostic()["dropped_events"], 2)

    def test_a_live_watcher_snapshot_is_explicitly_incomplete_and_detached(self):
        self.observer.thread = mock.Mock()
        self.observer.thread.is_alive.return_value = True
        first = self.observer.diagnostic()
        self.assertFalse(first["bounded_journal_complete"])
        self.assertTrue(first["observer_thread_alive_at_snapshot"])
        self.observer.record("later")
        self.assertEqual(first["events"], [])
        self.assertEqual(len(self.observer.diagnostic()["events"]), 1)

    def test_proc_inventory_and_raw_samples_are_not_claimed_atomic_or_complete(self):
        self.observer.scan()
        value = self.observer.diagnostic()
        self.assertEqual(value["first_scan_pids"], [20, 41])
        self.assertEqual(value["last_scan_pids"], [20, 41])
        self.assertFalse(value["all_proc_reads_claimed"])
        row = self.stages()["module_witness_retained"]
        self.assertEqual(row["process"]["sample_scope"],
                         "separate-read-only-observations-not-attestation")


class ObserverDouble:
    def __init__(self, *, start_error=None, stop_error=None, diagnostic_error=None,
                 complete=True):
        self.start_error = start_error
        self.stop_error = stop_error
        self.diagnostic_error = diagnostic_error
        self.complete = complete
        self.outputs = {}
        self.stopped = False
        self.snapshots = 0
    def start(self):
        if self.start_error:
            raise self.start_error
    def stop(self):
        self.stopped = True
        if self.stop_error:
            raise self.stop_error
    def diagnostic(self):
        self.snapshots += 1
        if self.diagnostic_error:
            raise self.diagnostic_error
        return {"schema": "test-double", "bounded_journal_complete": self.complete,
                "stop_observed": self.stopped, "private_path": "owner-private-not-public"}
    def retain_intermediate(self, _evidence):
        pass
    def close(self):
        pass
    def require_one(self, _master):
        raise g2.G2Unverified("g2_observed_consumer_is_not_native_result_artifact")


class DiagnosticTerminalCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / "output"
        self.output.mkdir()
        self.private = self.root / "private"
        self.private.mkdir()
        self.master = self.output / "master.mp4"
        self.raw = b"test-double-native-output"
        self.master.write_bytes(self.raw)
        self.result = {"native_mlt_multisegment_e2e": "PASS",
                       "master_artifact_path": "master.mp4",
                       "master_artifact_sha256": g2.sha(self.raw),
                       "master_artifact_bytes": len(self.raw)}
        self.session = g2.G2Session({"known_melt_sha256": "a"*64}, self.private, b"", {})
    def tearDown(self):
        self.temporary.cleanup()
    def unknown(self):
        return NativeCommandFailure(classify_native_command_failure(
            stdout=b"", stderr=b"", exit_code=None, deadline_exceeded=True))
    def execute(self, observer, callback):
        with mock.patch.object(g2, "LiveObserver", return_value=observer):
            return self.session.run_original(callback, 100, self.output)

    def test_success_captures_diagnostic_after_stop_before_return_and_only_once(self):
        observer = ObserverDouble()
        callback = mock.Mock(return_value=self.result)
        with self.session:
            self.assertIs(self.execute(observer, callback), self.result)
            value = json.loads((self.private / "observer-diagnostic.json").read_bytes())
            self.assertTrue(value["stop_observed"])
            self.assertEqual(self.session.observer_diagnostic_sha256,
                             g2.sha((self.private / "observer-diagnostic.json").read_bytes()))
        self.assertEqual(observer.snapshots, 1)
        callback.assert_called_once_with()

    def test_unknown_keeps_primary_error_and_diagnostic_without_replay(self):
        observer = ObserverDouble()
        primary = self.unknown()
        callback = mock.Mock(side_effect=primary)
        with self.assertRaises(NativeCommandFailure) as raised:
            with self.session:
                self.execute(observer, callback)
        self.assertIs(raised.exception, primary)
        self.assertEqual(observer.snapshots, 1)
        self.assertTrue((self.private / "observer-diagnostic.json").is_file())
        final = json.loads((self.private / "final-custody.json").read_bytes())
        self.assertEqual(final["native_terminal"], "ERROR_OR_UNKNOWN")
        self.assertFalse(final["source_content_and_live_module_verified"])
        callback.assert_called_once_with()

    def test_preflight_failure_retains_diagnostic_without_calling_native(self):
        failure = PermissionError(13, "original start")
        observer = ObserverDouble(start_error=failure)
        callback = mock.Mock(return_value=self.result)
        with self.assertRaises(PermissionError) as raised:
            with self.session:
                self.execute(observer, callback)
        self.assertIs(raised.exception, failure)
        callback.assert_not_called()
        final = json.loads((self.private / "final-custody.json").read_bytes())
        self.assertEqual(final["native_terminal"], "NOT_STARTED")
        self.assertEqual(final["observer_error_details"]["observer_start"]["errno"], 13)
        self.assertEqual(observer.snapshots, 1)

    def test_failed_stop_preserves_primary_error_and_incomplete_observer_state(self):
        primary = self.unknown()
        observer = ObserverDouble(stop_error=TimeoutError("original stop timeout"), complete=False)
        callback = mock.Mock(side_effect=primary)
        with self.assertRaises(NativeCommandFailure) as raised:
            with self.session:
                self.execute(observer, callback)
        self.assertIs(raised.exception, primary)
        final = json.loads((self.private / "final-custody.json").read_bytes())
        self.assertIn("observer_diagnostic_incomplete", final["secondary_failures"])
        self.assertEqual(final["observer_error_details"]["observer_stop_after_native_error"]["message"],
                         "original stop timeout")
        callback.assert_called_once_with()

    def test_diagnostic_exception_never_substitutes_for_native_unknown(self):
        primary = self.unknown()
        observer = ObserverDouble(diagnostic_error=OSError("original diagnostic disk"))
        callback = mock.Mock(side_effect=primary)
        with self.assertRaises(NativeCommandFailure) as raised:
            with self.session:
                self.execute(observer, callback)
        self.assertIs(raised.exception, primary)
        self.assertIn("observer_diagnostic_custody", self.session.secondary_failures)
        self.assertEqual(observer.snapshots, 1)
        callback.assert_called_once_with()

    def test_existing_diagnostic_is_not_overwritten_or_retried_after_write_failure(self):
        path = self.private / "observer-diagnostic.json"
        path.write_bytes(b"existing original")
        observer = ObserverDouble()
        callback = mock.Mock(return_value=self.result)
        with self.session:
            self.execute(observer, callback)
        self.assertEqual(path.read_bytes(), b"existing original")
        self.assertIsNone(self.session.observer_diagnostic_sha256)
        self.assertEqual(observer.snapshots, 1)
        self.assertIn("observer_diagnostic_custody", self.session.secondary_failures)
        callback.assert_called_once_with()

    def test_require_one_original_error_keeps_exact_message_result_and_diagnostic_binding(self):
        observer = ObserverDouble()
        callback = mock.Mock(return_value=self.result)
        with self.session:
            self.execute(observer, callback)
            self.session.retain_master(self.master, g2.sha(self.raw), len(self.raw))
        error = json.loads((self.private / "original-live-module-error.json").read_bytes())
        self.assertEqual(error["error"]["message"],
                         "g2_observed_consumer_is_not_native_result_artifact")
        self.assertEqual(error["master_sha256"], g2.sha(self.raw))
        self.assertEqual(error["observer_diagnostic_sha256"], self.session.observer_diagnostic_sha256)
        self.assertIs(self.session.native_result, self.result)
        self.assertEqual((self.private / "original-native-master.mp4").read_bytes(), self.raw)
        final = json.loads((self.private / "final-custody.json").read_bytes())
        self.assertEqual(final["observer_error_details"]["original_live_module_witness"], error["error"])
        self.assertFalse(final["source_content_and_live_module_verified"])
        callback.assert_called_once_with()

    def test_error_custody_failure_is_secondary_and_does_not_remove_native_result(self):
        observer = ObserverDouble()
        self.execute(observer, mock.Mock(return_value=self.result))
        (self.private / "original-live-module-error.json").write_bytes(b"first original")
        self.session.retain_master(self.master, g2.sha(self.raw), len(self.raw))
        self.assertEqual((self.private / "original-live-module-error.json").read_bytes(), b"first original")
        self.assertIs(self.session.native_result, self.result)
        self.assertIn("live_module_error_custody", self.session.secondary_failures)

    def test_private_diagnostics_are_not_added_to_the_closed_public_projection(self):
        import native_mlt_g2_custody as custody
        observer = ObserverDouble()
        with self.session:
            self.execute(observer, mock.Mock(return_value=self.result))
        final = json.loads((self.private / "final-custody.json").read_bytes())
        final["observer_error_details"]["private_path"] = {"message": "owner-private-not-public"}
        public = custody.projection({}, final, 1, "failure", b"test-double-seal", {"sha256": "a"*64})
        encoded = json.dumps(public)
        self.assertNotIn("owner-private-not-public", encoded)
        self.assertNotIn("observer_error_details", public)
        self.assertNotIn("observer_diagnostic_sha256", public)
        self.assertFalse(public["source_content_and_live_module_verified"])


if __name__ == "__main__":
    unittest.main()
