"""Synthetic subprocess tests of the shared read-only decoder supervisor.

These checks exercise transport and retention, not native Blender acceptance.
"""
from __future__ import annotations

from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manim_native_readback import run_bounded


class BoundedDecoderTransportTests(unittest.TestCase):
    def python(self, source):
        return [sys.executable, "-c", source]

    def test_existing_callers_without_new_kwargs_keep_return_shape_and_closed_stdin(self):
        data, terminal = run_bounded(
            self.python("import sys; assert sys.stdin.buffer.read() == b''; sys.stdout.buffer.write(b'legacy')"),
            64)
        self.assertEqual(data, b"legacy")
        self.assertEqual(set(terminal), {"exit_code", "elapsed_seconds", "stdout_bytes",
                                        "stderr_bytes", "stderr_sha256", "whole_process_completed"})
        self.assertEqual(terminal["exit_code"], 0)
        self.assertTrue(terminal["whole_process_completed"])

    def test_input_bytes_are_delivered_and_terminal_retained(self):
        captured = {}
        payload = b"synthetic png transport\0" * 4096
        data, terminal = run_bounded(
            self.python("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
            len(payload), input_bytes=payload, capture=captured)
        self.assertEqual(data, payload)
        self.assertEqual(captured["stdout"], payload)
        self.assertEqual(captured["stderr"], b"")
        self.assertTrue(captured["terminal"]["stdin_complete"])
        self.assertEqual(captured["terminal"]["exit_code"], terminal["exit_code"])

    def test_blocked_stdin_is_inside_absolute_deadline(self):
        captured = {}
        start = time.monotonic()
        with self.assertRaisesRegex(Exception, "readonly_decoder_timeout"):
            run_bounded(self.python("import time; time.sleep(30)"), 64, 0.25,
                        input_bytes=b"x" * (2 * 1024 * 1024), capture=captured)
        self.assertLess(time.monotonic() - start, 12)
        self.assertTrue(captured["terminal"]["timed_out"])
        self.assertFalse(captured["terminal"]["stdin_complete"])
        self.assertIsNotNone(captured["terminal"]["exit_code"])

    def test_stdout_overflow_is_bounded_before_rejection(self):
        captured = {}
        with self.assertRaisesRegex(Exception, "readonly_decoder_output_budget"):
            run_bounded(self.python("import sys; sys.stdout.buffer.write(b'x' * 200000)"),
                        128, 10, capture=captured)
        self.assertEqual(len(captured["stdout"]), 129)
        self.assertTrue(captured["terminal"]["output_budget_exceeded"])

    def test_stderr_overflow_retains_at_most_limit_plus_one(self):
        captured = {}
        with self.assertRaisesRegex(Exception, "readonly_decoder_output_budget"):
            run_bounded(self.python("import sys; sys.stderr.buffer.write(b'x' * 200000)"),
                        128, 10, capture=captured)
        self.assertEqual(len(captured["stderr"]), 65537)
        self.assertTrue(captured["terminal"]["output_budget_exceeded"])

    def test_nonzero_keeps_original_streams_and_exit_status(self):
        captured = {}
        with self.assertRaisesRegex(Exception, "readonly_decoder_failed:7"):
            run_bounded(self.python(
                "import sys; sys.stdout.buffer.write(b'partial'); "
                "sys.stderr.buffer.write(b'synthetic failure'); sys.exit(7)"),
                128, 10, capture=captured)
        self.assertEqual(captured["stdout"], b"partial")
        self.assertEqual(captured["stderr"], b"synthetic failure")
        self.assertEqual(captured["terminal"]["exit_code"], 7)

    def test_spawn_error_is_not_replaced_by_capture(self):
        captured = {}
        original = OSError("synthetic startup failure")
        with mock.patch("manim_native_readback.subprocess.Popen", side_effect=original):
            with self.assertRaises(OSError) as observed:
                run_bounded(["synthetic-unused-command"], 128, capture=captured)
        self.assertIs(observed.exception, original)
        self.assertEqual(captured["state"], "NOT_STARTED")
        self.assertEqual(captured["stdout"], b"")
        self.assertFalse(captured["terminal"]["whole_process_completed"])

    def test_invalid_input_or_reused_capture_fails_before_process(self):
        with mock.patch("manim_native_readback.subprocess.Popen") as start:
            for value in (b"", bytearray(b"mutable"), b"x" * (32 * 1024 * 1024 + 1)):
                with self.subTest(kind=type(value).__name__, size=len(value)):
                    with self.assertRaises(Exception):
                        run_bounded(["unused"], 64, input_bytes=value)
            with self.assertRaisesRegex(Exception, "capture_must_be_new"):
                run_bounded(["unused"], 64, capture={"prior": True})
            start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
