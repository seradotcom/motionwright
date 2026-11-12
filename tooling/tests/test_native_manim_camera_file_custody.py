"""Actual local file custody for camera/editable readers; no Native effects."""
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
import native_manim_camera_evidence as camera
from nsis_owned_files import CaptureError

REJECTIONS = (engine.CustodyRejected, IntakeError, CaptureError)


class CameraOriginalFileCustodyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()

    def original(self, raw):
        path = self.root / "retained.original"
        path.write_bytes(b"initial bytes")
        with path.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        return path

    def test_modified_original_is_byte_exact_and_releases_held_handle(self):
        raw = bytes(range(256)) * 17
        path = self.original(raw)
        self.assertEqual(camera.read(path, len(raw)), raw)
        self.assertEqual(path.read_bytes(), raw)
        path.rename(self.root / "closed.original")

    def test_budget_and_duplicate_json_rejections_preserve_original(self):
        raw = b'{"frame":1,"frame":2}\n'
        path = self.original(raw)
        with self.assertRaises(REJECTIONS):
            camera.read(path, len(raw) - 1)
        with self.assertRaisesRegex(camera.CameraEvidenceError, "camera_duplicate_json_key"):
            camera.read_object(path)
        self.assertEqual(path.read_bytes(), raw)
        path.rename(self.root / "rejected.original")

    @unittest.skipUnless(os.name == "nt", "Windows mandatory sharing contract")
    def test_camera_reader_rejects_existing_writer_without_mutating_original(self):
        raw = b"actual original with an open writer"
        path = self.original(raw)
        with path.open("r+b") as writer:
            with self.assertRaisesRegex(CaptureError, "cannot be held"):
                camera.read(path, len(raw))
            self.assertEqual(writer.read(), raw)
        self.assertEqual(camera.read(path, len(raw)), raw)


if __name__ == "__main__":
    unittest.main()
