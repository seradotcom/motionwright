"""Actual bounded Python children exercise custody; no Native render is claimed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
from native_original_mix_custody import OriginalCommandError, OriginalPcmCustody


class OriginalCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="original-pcm-custody-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.capture = OriginalPcmCustody(
            self.directory, ROOT, {"scope": "REAL_PYTHON_CHILD_NOT_NATIVE_RENDER"}
        )

    def child(self, code, label, *args, timeout=5, inputs=()):
        return self.capture.run([sys.executable, "-B", "-E", "-s", "-c", code, *map(str, args)],
                                label, timeout, inputs)

    def metadata(self, label):
        return json.loads((self.directory / "commands" / label /
                           (label + ".command.json")).read_bytes())

    def test_binary_and_empty_streams_are_original_before_interpretation(self):
        result = self.child(
            "import os;os.write(1,bytes([0,255,13,10]));os.write(2,bytes([128,0,10]))",
            "binary",
        )
        self.assertEqual(result["stdout"], bytes([0, 255, 13, 10]))
        self.assertEqual(result["stderr"], bytes([128, 0, 10]))
        record = self.metadata("binary")
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(record["observation"], "DIRECT_PROCESS_EXIT_OBSERVED")
        self.assertEqual(record["stdout"]["sha256"], hashlib.sha256(result["stdout"]).hexdigest())
        self.assertEqual(record["stderr"]["sha256"], hashlib.sha256(result["stderr"]).hexdigest())
        empty = self.child("pass", "empty")
        self.assertEqual((empty["stdout"], empty["stderr"]), (b"", b""))
        for stream in ("stdout", "stderr"):
            self.assertEqual(self.metadata("empty")[stream]["bytes"], 0)

    def test_input_bytes_are_retained_exclusively_and_bound_to_the_actual_child(self):
        raw = bytes([0, 255, 128, 13, 10]) * 123
        original = self.capture.retain("en-voice.s16le", raw)
        path = self.directory / original["path"]
        result = self.child(
            "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
            "input", path, inputs=[original],
        )
        self.assertEqual(result["stdout"], raw)
        self.assertEqual(self.metadata("input")["context"]["original_inputs"], [original])
        with self.assertRaises(FileExistsError):
            self.capture.retain("en-voice.s16le", b"replacement")
        self.assertEqual(path.read_bytes(), raw)

    def test_prior_command_collision_refuses_before_launch_without_overwrite(self):
        output = self.directory / "commands" / "collision"
        output.mkdir()
        original = output / "collision.command.json"
        original.write_bytes(b"prior original")
        marker = self.directory / "child-started"
        with self.assertRaises(FileExistsError):
            self.child("import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(b'started')",
                       "collision", marker)
        self.assertFalse(marker.exists())
        self.assertEqual(original.read_bytes(), b"prior original")
        with self.assertRaises(ValueError):
            self.child("pass", "after-collision")
        self.assertFalse((self.directory / "commands/after-collision").exists())

    def test_nonzero_return_retains_the_observed_exit_and_both_streams(self):
        with self.assertRaises(OriginalCommandError) as raised:
            self.child("import os,sys;os.write(1,b'partial');os.write(2,b'original failure');sys.exit(7)",
                       "failed")
        self.assertEqual(raised.exception.original_exit_code, 7)
        self.assertEqual(raised.exception.command, self.metadata("failed"))
        self.assertEqual((self.directory / "commands/failed/failed.stdout.log").read_bytes(),
                         b"partial")
        self.assertEqual((self.directory / "commands/failed/failed.stderr.log").read_bytes(),
                         b"original failure")
        self.assertNotIn("failed", self.capture.commands)

    def test_timeout_preserves_partial_bytes_and_remains_unknown_without_retry(self):
        with self.assertRaises(OriginalCommandError) as raised:
            self.child("import os,time;os.write(1,b'started');os.write(2,b'waiting');time.sleep(30)",
                       "timeout", timeout=2)
        error = raised.exception
        self.assertIsNone(error.original_exit_code)
        self.assertEqual(error.command["observation"], "UNKNOWN_TIMEOUT_NO_RETRY")
        self.assertEqual((self.directory / "commands/timeout/timeout.stdout.log").read_bytes(),
                         b"started")
        self.assertEqual((self.directory / "commands/timeout/timeout.stderr.log").read_bytes(),
                         b"waiting")
        self.assertNotIn("timeout", self.capture.commands)
        with self.assertRaises(ValueError):
            self.child("raise SystemExit(0)", "forbidden-followup")
        self.assertFalse((self.directory / "commands/forbidden-followup").exists())

    def test_metadata_failure_cannot_erase_exit_seven_or_admit_exit_zero(self):
        for code in (7, 0):
            with self.subTest(exit_code=code):
                directory = self.directory / ("metadata-" + str(code))
                directory.mkdir()
                capture = OriginalPcmCustody(directory, ROOT, {"scope": "PYTHON_CHILD"})
                target = directory / "commands/captured/captured.command.json"
                child = (
                    "import pathlib,sys;"
                    "pathlib.Path(sys.argv[1]).write_bytes(b'child occupied metadata');"
                    "sys.stderr.buffer.write(b'original stderr');"
                    "sys.exit(int(sys.argv[2]))"
                )
                with self.assertRaises(OriginalCommandError) as raised:
                    capture.run([sys.executable, "-B", "-E", "-s", "-c", child,
                                 str(target), str(code)], "captured", 5)
                error = raised.exception
                self.assertEqual(error.original_exit_code, code)
                self.assertEqual(error.command["observation"], "UNKNOWN_CUSTODY_NO_RETRY")
                self.assertEqual(error.command["uncertainty_reason"],
                                 "COMMAND_RECEIPT_PERSISTENCE_FAILED")
                self.assertEqual(target.read_bytes(), b"child occupied metadata")
                derived = json.loads((target.parent / "failure-observation.json").read_bytes())
                self.assertEqual(derived["command"]["exit_code"], code)
                self.assertEqual((target.parent / "captured.stderr.log").read_bytes(),
                                 b"original stderr")
                self.assertEqual(capture.commands, {})

    def test_real_input_mutation_after_success_refuses_custody(self):
        original = self.capture.retain("en-source.s16le", b"source")
        source = self.directory / original["path"]
        with self.assertRaises(OriginalCommandError) as raised:
            self.child("import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(b'changed')",
                       "mutation", source, inputs=[original])
        self.assertEqual(raised.exception.original_exit_code, 0)
        self.assertIsNotNone(raised.exception.secondary)
        self.assertNotIn("mutation", self.capture.commands)

    def test_late_original_stream_change_cannot_enter_final_inventory(self):
        self.child("import os;os.write(1,b'original')", "late-change")
        path = self.directory / "commands/late-change/late-change.stdout.log"
        path.write_bytes(b"replacement")
        with self.assertRaisesRegex(ValueError, "changed after first observation"):
            self.capture.inventory()


if __name__ == "__main__":
    unittest.main()
