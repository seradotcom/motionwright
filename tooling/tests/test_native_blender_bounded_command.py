"""Read-only original command capture bounds; inert Python children only.

No Blender, Broker, Native Host, SDK, authorization, or renderer is executed.
The source-only journal writer is stubbed to avoid private-crypto dependencies.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tooling import native_blender_original_evidence as evidence


def retain_test_journal(path: Path, value: object) -> dict:
    body = (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode()
    path.write_bytes(body)
    return {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}


class BlenderCommandInFlightBounds(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="a4-blender-bounded-original-")
        self.addCleanup(self.workspace.cleanup)
        self.prefix = Path(self.workspace.name) / "original"
        self.record = {}
        patcher = mock.patch.object(evidence, "new_json", side_effect=retain_test_journal)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_inert(self, script: str, **kwargs):
        return evidence.captured_json(
            [sys.executable, "-I", "-c", script], env={},
            prefix=self.prefix, record=self.record, timeout=5, **kwargs,
        )

    def test_exact_small_original_json_and_stderr_survive(self):
        result = self.run_inert(
            "import sys;"
            "sys.stdout.buffer.write(b'{\\\"ok\\\":true}\\n');"
            "sys.stderr.buffer.write(b'original diagnostics\\n')"
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            self.prefix.with_suffix(".stdout").read_bytes(), b'{"ok":true}\n')
        self.assertEqual(
            self.prefix.with_suffix(".stderr").read_bytes(),
            b"original diagnostics\n")
        self.assertEqual(self.record["terminal"],
                         {"status": "PROCESS_EXIT", "returncode": 0})
        self.assertTrue(self.record["stream_custody"]["complete"])

    def test_runaway_stdout_is_bounded_on_disk_before_json_parse(self):
        with mock.patch.object(evidence, "MAX_JSON", 32):
            with self.assertRaisesRegex(
                    evidence.OriginalSceneUnverified,
                    "original_scene_command_stream_unbounded"):
                self.run_inert(
                    "import sys;sys.stdout.buffer.write(b'X'*33)")
        self.assertEqual(
            self.prefix.with_suffix(".stdout").read_bytes(), b"X" * 32)
        self.assertEqual(self.record["terminal"]["status"],
                         "PROCESS_STREAM_UNBOUNDED")
        self.assertEqual(
            self.record["stream_custody"]["stdout"]["retained_prefix_bytes"], 32)
        self.assertGreater(
            self.record["stream_custody"]["stdout"]["observed_bytes_at_least"], 32)
        self.assertFalse(self.record["stream_custody"]["complete"])

    def test_runaway_stderr_is_bounded_without_ack(self):
        with mock.patch.object(evidence, "MAX_STDERR", 32):
            with self.assertRaisesRegex(
                    evidence.OriginalSceneUnverified,
                    "original_scene_command_stream_unbounded"):
                self.run_inert(
                    "import sys;sys.stderr.buffer.write(b'Y'*33)")
        self.assertEqual(
            self.prefix.with_suffix(".stderr").read_bytes(), b"Y" * 32)
        self.assertEqual(self.record["terminal"]["status"],
                         "PROCESS_STREAM_UNBOUNDED")
        self.assertFalse(self.record["stream_custody"]["complete"])

    def test_delayed_original_stream_sync_does_not_false_reject_completed_child(self):
        # Both original pipes reached EOF, but flushing their exact retained
        # bytes to disk can outlast the direct child's exit by >1 second.
        # That is not an inherited live pipe writer or an UNKNOWN response.
        import threading
        import time
        real_sync = evidence.os.fsync
        def slow_sync(fd):
            if threading.current_thread() is not threading.main_thread():
                time.sleep(2)
            return real_sync(fd)
        with mock.patch.object(evidence.os, "fsync", side_effect=slow_sync):
            result = self.run_inert(
                "import sys;sys.stdout.buffer.write(b'{\\\"done\\\":true}\\n');"
                "sys.stderr.buffer.write(b'original-slow-sync')",
            )
        self.assertEqual(result, {"done": True})
        self.assertEqual(
            self.prefix.with_suffix(".stderr").read_bytes(),
            b"original-slow-sync")
        self.assertTrue(self.record["stream_custody"]["complete"])
        self.assertEqual(self.record["terminal"],
                         {"status": "PROCESS_EXIT", "returncode": 0})

    def test_inherited_original_writer_after_parent_exit_never_creates_ack(self):
        # The direct child reports exit zero, but a self-terminating Python
        # descendant holds its original pipes. No source status is allowed
        # until both stdout/stderr have actual EOF and complete custody.
        producer = (
            "import subprocess,sys;"
            "subprocess.Popen([sys.executable,'-I','-c',"
            "'import time;time.sleep(3)'],"
            "stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr);"
            "sys.stdout.buffer.write(b'{\\\"ok\\\":true}\\n');"
            "sys.stdout.buffer.flush()"
        )
        with self.assertRaisesRegex(
                evidence.OriginalSceneUnverified,
                "original_scene_command_stream_incomplete"):
            self.run_inert(producer)
        self.assertEqual(self.record["terminal"]["status"],
                         "PROCESS_STREAM_INCOMPLETE")
        self.assertFalse(self.record["stream_custody"]["complete"])
        self.assertFalse(self.record["terminal"]["outcome_known"])
        self.assertFalse(self.record["stream_custody"]["automatic_retry"])

    def test_nonzero_original_child_is_not_an_ack(self):
        with self.assertRaisesRegex(
                evidence.OriginalSceneUnverified,
                "original_scene_process_returned_nonzero"):
            self.run_inert(
                "import sys;sys.stderr.buffer.write(b'original-denial');"
                "sys.exit(7)")
        self.assertEqual(self.record["terminal"],
                         {"status": "PROCESS_EXIT", "returncode": 7})
        self.assertEqual(
            self.prefix.with_suffix(".stderr").read_bytes(), b"original-denial")

    def test_timeout_retains_only_the_original_bytes(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            evidence.captured_json(
                [sys.executable, "-I", "-c",
                 "import sys,time;sys.stdout.buffer.write(b'partial');"
                 "sys.stdout.buffer.flush();time.sleep(2)"],
                env={}, prefix=self.prefix, record=self.record, timeout=0.2,
            )
        self.assertEqual(self.record["terminal"]["status"], "PROCESS_TIMEOUT")
        self.assertFalse(self.record["terminal"]["outcome_known"])
        self.assertEqual(
            self.prefix.with_suffix(".stdout").read_bytes(), b"partial")

    def test_existing_original_file_prevents_child_invocation(self):
        source = self.prefix.with_suffix(".stdout")
        source.write_bytes(b"already-retained-original")
        with mock.patch.object(
                evidence, "_bounded_original_command",
                side_effect=AssertionError("forbidden second invocation")) as effect:
            with self.assertRaises(FileExistsError):
                evidence.captured_json(
                    ["forbidden-native-child"], env={},
                    prefix=self.prefix, record=self.record, timeout=5,
                )
        effect.assert_not_called()
        self.assertEqual(source.read_bytes(), b"already-retained-original")

    def test_namespace_inode_substitution_is_rejected_on_all_hosts(self):
        # Linux's shared source intake and the Windows ctime-aware reader
        # BOTH reject changed namespace identity. No platform skips in G0.
        from types import SimpleNamespace
        source = self.prefix.with_suffix(".stdout")
        with evidence._exclusive_original_binary(source) as output:
            output.write(b'{"ok":true}\n')
        real_lstat = Path.lstat
        def altered(path, *args, **kwargs):
            observed = real_lstat(path, *args, **kwargs)
            if path != source:
                return observed
            keys = ("st_dev", "st_ino", "st_size", "st_mode",
                    "st_uid", "st_gid", "st_mtime_ns", "st_ctime_ns",
                    "st_birthtime_ns")
            values = {key: getattr(observed, key, None) for key in keys}
            values["st_ino"] += 1
            return SimpleNamespace(**values)
        with mock.patch.object(Path, "lstat", new=altered):
            with self.assertRaisesRegex(
                    Exception,
                    "original_scene_command_stdout_source_changed|input changed during read"):
                evidence._read_stable_command_stdout(source, 32)


if __name__ == "__main__":
    unittest.main()
