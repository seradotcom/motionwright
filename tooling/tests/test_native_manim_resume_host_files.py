"""Real file and Windows sharing checks; no Native effects or remote mutations."""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_private_evidence as engine
from mlt_official_runtime_intake import IntakeError
import native_manim_resume_host as host
from nsis_owned_files import CaptureError

REJECTIONS = (engine.CustodyRejected, IntakeError, CaptureError)


class OriginalHostFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_modified_binary_original_is_read_exactly(self):
        target = self.root / "archive.original"
        target.write_bytes(b"first")
        original = bytes(range(256)) * 17
        with target.open("wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        # Make modification time distinct from creation without changing bytes.
        # On Windows 3.13, path and descriptor ctime also name different clocks.
        os.utime(target, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        self.assertEqual(host.bounded(target, len(original)), original)
        self.assertEqual(target.read_bytes(), original)
        # The original handle must have closed after the read.
        target.rename(self.root / "closed.original")

    def test_empty_original_and_oversize_rejection_preserve_bytes(self):
        empty = self.root / "empty.original"
        empty.write_bytes(b"")
        self.assertEqual(host.bounded(empty, 0), b"")
        target = self.root / "bounded.original"
        target.write_bytes(b"retained original")
        with self.assertRaises(REJECTIONS):
            host.bounded(target, target.stat().st_size - 1)
        self.assertEqual(target.read_bytes(), b"retained original")
        target.rename(self.root / "rejected.original")

    def test_directory_is_never_admitted_as_an_original(self):
        with self.assertRaises(REJECTIONS):
            host.bounded(self.root, 4096)

    def test_existing_writer_obeys_platform_sharing_without_mutation(self):
        target = self.root / "open-writer.original"
        original = b"existing writer must retain its bytes"
        target.write_bytes(original)
        with target.open("r+b") as writer:
            if os.name == "nt":
                with self.assertRaisesRegex(CaptureError, "cannot be held"):
                    host.bounded(target, len(original))
            else:
                # POSIX retains its existing descriptor checks, without
                # claiming Windows mandatory write/deletion exclusion.
                self.assertEqual(host.bounded(target, len(original)), original)
            self.assertEqual(writer.read(), original)
        self.assertEqual(host.bounded(target, len(original)), original)


if __name__ == "__main__":
    unittest.main()
