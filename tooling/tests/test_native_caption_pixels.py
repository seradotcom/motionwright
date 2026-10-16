"""Adversarial manifest/pixel reader tests. No simulated pixels count as proof."""
from __future__ import annotations
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_caption_pixels import _load_frame, compare_caption_overlay, NativeCaptionPixelsError


class NativeCaptionManifestAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "frames").mkdir()
        blob = b"NOT-A-REAL-PNG"
        self.frame = self.root / "frames" / "000022.png"
        self.frame.write_bytes(blob)
        rows = [{
            "index": i,
            "file": f"frames/{i:06}.png",
            "sha256": hashlib.sha256(blob).hexdigest(),
            "bytes": len(blob),
        } for i in range(60)]
        self.manifest = self.root / "artifact-manifest.json"
        self.data = {"plan": {"frame_count": 60}, "frames": rows}
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.data), encoding="utf-8")

    def test_correct_digest_and_index_admit_only_original_named_bytes(self):
        self.assertEqual(_load_frame(self.root, 22), self.frame)

    def test_rejects_non_allowlisted_frame_numbers(self):
        with self.assertRaises(NativeCaptionPixelsError):
            _load_frame(self.root, 15)

    def test_rejects_forged_frame_index_or_path(self):
        self.data["frames"][22]["index"] = 21
        self.write_manifest()
        with self.assertRaises(NativeCaptionPixelsError):
            _load_frame(self.root, 22)
        self.data["frames"][22]["index"] = 22
        self.data["frames"][22]["file"] = "../private.png"
        self.write_manifest()
        with self.assertRaises(NativeCaptionPixelsError):
            _load_frame(self.root, 22)

    def test_rejects_mutated_bytes_even_when_frame_name_matches(self):
        self.frame.write_bytes(b"NOT-A-REAL-PNG-modified")
        with self.assertRaises(NativeCaptionPixelsError):
            _load_frame(self.root, 22)

    def test_rejects_truncated_or_unbounded_manifest(self):
        self.data["frames"] = self.data["frames"][:-1]
        self.write_manifest()
        with self.assertRaises(NativeCaptionPixelsError):
            _load_frame(self.root, 22)
        self.data["frames"].append(self.data["frames"][-1])
        self.data["plan"]["frame_count"] = 10**20
        self.write_manifest()
        with self.assertRaises(NativeCaptionPixelsError):
            _load_frame(self.root, 22)

    def test_rejects_unknown_fixture_cardinality_before_decoding(self):
        with self.assertRaises(NativeCaptionPixelsError):
            compare_caption_overlay(self.root, self.root, 1)

    def test_rejects_manifest_symlinks_when_supported(self):
        self.manifest.unlink()
        other = self.root / "other.json"
        other.write_text("{}", encoding="utf-8")
        try:
            self.manifest.symlink_to(other)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(NativeCaptionPixelsError):
            _load_frame(self.root, 22)


if __name__ == "__main__":
    unittest.main()
