"""Closed static writer controls; only harmless Python children are executed."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_blender_e2e as wrapper


class StaticWriterBudgetTests(unittest.TestCase):
    def test_fifo_sidecar_rejects_without_blocking_or_replacing_primary_failure(self):
        with tempfile.TemporaryDirectory(prefix="static-fifo-original-") as directory:
            code = textwrap.dedent('''
                import json, os, stat, subprocess, sys
                from pathlib import Path
                from unittest.mock import patch
                sys.path.insert(0, sys.argv[2])
                import native_blender_e2e as owner
                root = Path(sys.argv[1])
                evidence = root / "evidence"
                evidence.mkdir()
                database = root / "motionwright.sqlite3"
                original = b"unopened synthetic original database\\x00"
                database.write_bytes(original)
                fifo = Path(str(database) + "-wal")
                os.mkfifo(fifo, 0o600)
                before = fifo.lstat()
                terminal = subprocess.CompletedProcess(["synthetic-native-result"], 17,
                            b"original terminal stdout", b"original terminal failure")
                with patch.object(owner, "EVIDENCE", evidence), \
                     patch.object(owner.subprocess, "run", return_value=terminal) as invocation:
                    try:
                        owner.run_json(["synthetic-native-result"], env={},
                                       static_capture=("realize", database, None))
                    except AssertionError as primary:
                        assert "command failed (17)" in str(primary)
                        assert isinstance(primary.native_custody_error, AssertionError)
                        assert "custody is incomplete" in str(primary.native_custody_error)
                    else:
                        raise AssertionError("native failure was replaced or lost")
                    assert invocation.call_count == 1
                receipt = json.loads((evidence / "static-realize-originals/receipt.json").read_bytes())
                assert receipt["custody_complete"] is False
                assert receipt["native_process_returncode"] == 17
                assert receipt["sqlite_reopen_or_native_retry_executed"] is False
                assert [row["name"] for row in receipt["custody_errors"]] == ["motionwright.sqlite3-wal"]
                assert not (evidence / "static-realize-originals/motionwright.sqlite3-wal").exists()
                assert database.read_bytes() == original
                assert (evidence / "static-realize-originals/motionwright.sqlite3").read_bytes() == original
                assert (evidence / "static-realize-originals/stderr.original").read_bytes() == terminal.stderr
                after = fifo.lstat()
                assert stat.S_ISFIFO(after.st_mode)
                assert (before.st_dev, before.st_ino, before.st_mode) == (after.st_dev, after.st_ino, after.st_mode)
                print(json.dumps({"fifo_rejected": True, "primary_error_preserved": True,
                                  "custody_complete": False, "native_retry_executed": False}))
            ''')
            try:
                result = subprocess.run([sys.executable, "-B", "-c", code, directory,
                                         str(Path(wrapper.__file__).parent)],
                                        capture_output=True, timeout=3, check=False)
            except subprocess.TimeoutExpired:
                self.fail("original custody blocked opening a FIFO; the bounded synthetic child was stopped")
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            self.assertEqual(json.loads(result.stdout), {"fifo_rejected": True,
                             "primary_error_preserved": True, "custody_complete": False,
                             "native_retry_executed": False})

    def test_json_budget_includes_escaping_and_newline_before_create_or_rewrite(self):
        with tempfile.TemporaryDirectory(prefix="static-json-budget-") as directory:
            root = Path(directory)
            value = {"escaped": "\u00e9\n"}
            expected = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
            path = root / "derived.json"
            with patch.object(wrapper, "STATIC_JSON_BYTES", len(expected) - 1):
                with self.assertRaisesRegex(ValueError, "byte budget"):
                    wrapper.write_static_json(path, value)
            self.assertFalse(path.exists())
            with patch.object(wrapper, "STATIC_JSON_BYTES", len(expected)):
                wrapper.write_static_json(path, value)
                self.assertEqual(path.read_bytes(), expected)
                with self.assertRaises(FileExistsError):
                    wrapper.write_static_json(path, value)
            self.assertEqual(path.read_bytes(), expected)

    def run_pipe(self, root, data, limit):
        log = root / "semwrightd.log"
        code = "import os,time; os.write(1," + repr(data) + "); time.sleep(" + (
            "10" if len(data) > limit else "0") + ")"
        process = subprocess.Popen([sys.executable, "-B", "-c", code],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        capture = None
        try:
            with log.open("xb") as stream, patch.object(wrapper, "STATIC_STREAM_BYTES", limit):
                capture = wrapper.start_static_daemon_log(process, stream)
                process.wait(timeout=5)
                if len(data) > limit:
                    with self.assertRaisesRegex(AssertionError, "UNKNOWN_NO_RETRY"):
                        wrapper.finish_static_daemon_log(capture, process, stream, log, root)
                else:
                    wrapper.finish_static_daemon_log(capture, process, stream, log, root)
            return json.loads((root / "semwrightd-capture.json").read_bytes())
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if capture is not None and capture["thread"].is_alive():
                capture["stop"].set()
                capture["thread"].join(timeout=2)
            if process.stdout is not None and not process.stdout.closed:
                process.stdout.close()

    def test_daemon_pipe_eof_retains_exact_bytes_and_does_not_claim_native_acceptance(self):
        with tempfile.TemporaryDirectory(prefix="static-log-eof-") as directory:
            root = Path(directory)
            original = b"synthetic exact broker output\x00\xff\n"
            receipt = self.run_pipe(root, original, len(original))
            self.assertEqual((root / "semwrightd.log").read_bytes(), original)
            self.assertTrue(receipt["capture_complete"])
            self.assertTrue(receipt["eof_observed"])
            self.assertEqual(receipt["retained_file_sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(receipt["native_operation_result"], "NO_NATIVE_ACCEPTANCE_FROM_LOG")
            self.assertFalse(receipt["whole_process_tree_completion_claimed"])

    def test_daemon_overflow_keeps_every_read_byte_including_sentinel_and_stops_owned_child(self):
        with tempfile.TemporaryDirectory(prefix="static-log-overflow-") as directory:
            root = Path(directory)
            original = b"x" * 1000
            receipt = self.run_pipe(root, original, 64)
            self.assertEqual((root / "semwrightd.log").read_bytes(), original[:65])
            self.assertEqual(receipt["observed_pipe_bytes"], 65)
            self.assertEqual(receipt["retained_file_bytes"], 65)
            self.assertTrue(receipt["overflow"])
            self.assertFalse(receipt["capture_complete"])
            self.assertFalse(receipt["eof_observed"])
            self.assertFalse(receipt["automatic_retry"])
            self.assertEqual(receipt["native_operation_result"], "UNKNOWN_NO_RETRY")
            self.assertIsNotNone(receipt["owned_daemon_exit_code"])

    def test_static_context_log_failure_stays_secondary_and_default_context_does_not_use_budget(self):
        class InertDaemon:
            pid = 424242
            returncode = None
            def poll(self):
                return self.returncode
            def wait(self, timeout):
                self.returncode = 0
                return 0

        for bounded in (False, True):
            with self.subTest(bounded_static=bounded), tempfile.TemporaryDirectory(prefix="static-context-budget-") as directory:
                root = Path(directory)
                tools = root / "tools"
                tools.mkdir()
                for name in ("semwright", "semwrightd", "semwright-sandbox", "semwright-blender-driver", "semwright-blender-session-runner", "blender"):
                    (tools / name).write_bytes(b"inert unit bytes; never executed")
                paths = {}
                for name in ("runtime", "state", "home", "config", "bin", "workspace", "scratch", "motionwright-data"):
                    paths[name] = root / name
                    paths[name].mkdir()
                evidence = root / "evidence"
                evidence.mkdir()
                original = RuntimeError("original owned-context startup error")
                daemon = InertDaemon()
                with patch.object(wrapper, "SW_BINS", tools), \
                     patch.object(wrapper, "BLENDER_BIN", tools / "blender"), \
                     patch.object(wrapper, "BLENDER_ROOT", tools), \
                     patch.object(wrapper, "static_original_budget") as clock, \
                     patch.object(wrapper, "semwright_version", return_value="synthetic"), \
                     patch.object(wrapper.subprocess, "Popen", return_value=daemon) as popen, \
                     patch.object(wrapper, "wait_for_socket", side_effect=original), \
                     patch.object(wrapper, "start_static_daemon_log", return_value={}) as start, \
                     patch.object(wrapper, "finish_static_daemon_log", side_effect=OSError("secondary log receipt failure")) as finish, \
                     patch.object(wrapper.os, "killpg"):
                    with self.assertRaises(RuntimeError) as raised:
                        with wrapper.blender_context(paths, {}, evidence, {"resource": "synthetic"}, bounded_static=bounded):
                            self.fail("no native context should be yielded")
                    self.assertIs(raised.exception, original)
                    if bounded:
                        clock.return_value.setup.assert_called_once_with(45)
                        start.assert_called_once()
                        finish.assert_called_once()
                        self.assertTrue(any("static-log:OSError" in row for row in original.native_context_cleanup))
                        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
                    else:
                        clock.assert_not_called()
                        start.assert_not_called()
                        finish.assert_not_called()
                        self.assertNotEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()
