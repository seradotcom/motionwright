"""A4: actual Windows/Linux pipe EOF custody, without any Native effects.

One-run children are inert Python. All temporary files are directed to the
caller-provided E: test root on the authorized laptop.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tooling.ci_graph_dev_terminal_stdout import read_terminal_stdout
from tooling.ci_graph_dev_native_firstsave import FirstSaveFailure, NativeSession


class PortableStdoutCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT"),
            prefix="gn1-stdout-eof-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = self.root / "private"
        self.private.mkdir()

    def child(self, source, *, maximum=1024):
        child = subprocess.Popen(
            [sys.executable, "-B", "-S", "-c", source],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
        )
        self.assertEqual(child.wait(timeout=5), 0)
        captured = read_terminal_stdout(
            child.stdout, maximum=maximum, wait_seconds=1.0)
        child.stderr.close()
        return captured

    def new_session(self, source, *, read_only=False):
        session = NativeSession(
            Path(sys.executable), self.root / "inert.sqlite3",
            self.root / "inert-connection.json",
            self.private, os.environ.copy(), read_only=read_only)
        session.command = [
            sys.executable, "-B", "-S", "-c", source]
        return session

    def test_zero_stdout_from_exited_child_yields_real_eof_on_windows(self):
        result = self.child("pass")
        self.assertEqual(result["raw"], b"")
        self.assertTrue(result["eof"])
        self.assertTrue(result["reader_finished"])
        self.assertFalse(result["deadline_exceeded"])
        self.assertFalse(result["read_error"])

    def test_stdout_after_exited_child_is_read_without_windows_select(self):
        result = self.child("import os;os.write(1,b'after-reply\\n')")
        self.assertEqual(result["raw"], b"after-reply\n")
        self.assertTrue(result["eof"])
        self.assertFalse(result["over_cap"])

    def test_over_budget_trailing_stdout_retains_bounded_prefix(self):
        result = self.child("import os;os.write(1,b'X'*400)", maximum=64)
        self.assertGreater(len(result["raw"]), 64)
        self.assertLessEqual(len(result["raw"]), 65)
        self.assertTrue(result["over_cap"])
        self.assertFalse(result["eof"])

    def test_real_inert_native_session_closes_stdout_at_eof(self):
        session = self.new_session("pass", read_only=True)
        with session:
            pass
        terminal = json.loads(
            (self.private / "independent-readonly-session.terminal.original.json")
            .read_text(encoding="utf-8"))
        self.assertTrue(terminal["stdout_eof_observed"])
        self.assertTrue(terminal["original_reply_complete"])
        self.assertFalse(terminal["trailing_stdout_deadline_exceeded"])
        self.assertIsNone(terminal["trailing_stdout"])

    def test_real_inert_native_session_rejects_unexpected_stdout(self):
        session = self.new_session("import os;os.write(1,b'unexpected-extra')")
        with self.assertRaisesRegex(
                FirstSaveFailure, "GN1_FIRST_SAVE_SESSION_CLOSE_UNCERTAIN") as result:
            with session:
                pass
        self.assertTrue(result.exception.unknown)
        self.assertEqual(
            (self.private / "graph-native-session.trailing-stdout.original")
            .read_bytes(), b"unexpected-extra")
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json")
            .read_text(encoding="utf-8"))
        self.assertTrue(terminal["stdout_eof_observed"])
        self.assertFalse(terminal["original_reply_complete"])
        self.assertEqual(terminal["trailing_stdout"]["bytes"],
                         len(b"unexpected-extra"))


    def test_inherited_writer_cannot_claim_early_eof(self):
        reader_fd, writer_fd = os.pipe()
        pipe = os.fdopen(reader_fd, "rb", buffering=0)
        try:
            outcome = read_terminal_stdout(
                pipe, maximum=64, wait_seconds=0.05)
        finally:
            os.close(writer_fd)
        self.assertTrue(outcome["deadline_exceeded"])
        self.assertFalse(outcome["eof"])
        self.assertEqual(outcome["raw"], b"")

    def test_selected_ci_routes_the_new_portable_stdout_regressions(self):
        workflow = (Path(__file__).resolve().parents[2] /
                    ".github/workflows/graph-native-startup-dev.yml")
        text = workflow.read_text(encoding="utf-8")
        self.assertIn(
            "tooling.tests.test_ci_graph_dev_portable_terminal_stdout -q",
            text)
        self.assertIn("tooling.tests.test_ci_graph_dev_native_firstsave", text)


if __name__ == "__main__":
    unittest.main()
