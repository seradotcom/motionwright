"""Regression tests for unsigned, portable media content integrity receipts."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid

SCRIPT = Path(__file__).resolve().parents[1] / "verify_delivered_media.py"
SPEC = importlib.util.spec_from_file_location("verify_delivered_media", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class DeliveredMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.master = self.root / "screen-capture.mp4"
        self.bytes = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00one verified MP4 body fixture"
        self.master.write_bytes(self.bytes)
        self.proof = self.root / "screen-capture.mp4.motionwright-integrity.json"
        self.original = {
            "schema": mod.SCHEMA,
            "scope": mod.SCOPE,
            "media": {
                "filename": self.master.name,
                "size_bytes": len(self.bytes),
                "sha256": hashlib.sha256(self.bytes).hexdigest(),
            },
            "origin": {
                "project_id": str(uuid.uuid4()),
                "generation": str(uuid.uuid4()),
                "revision": 9,
                "deliverable_id": str(uuid.uuid4()),
                "native_profile": "h264-aac-mp4",
                "frame_count": 60,
                "frame_rate": {"num": 30, "den": 1},
                "semwright_native_sdk_revision": "8fa191250ae68274182570c65f067f7a60f85625",
            },
        }
        self.write()

    def write(self):
        self.proof.write_text(json.dumps(self.original, indent=2) + "\n", encoding="utf-8")

    def test_valid_sha_matches_exact_bytes_without_authenticating_creator(self):
        result = mod.verify_delivery(self.proof)
        self.assertEqual(result["sha256"], hashlib.sha256(self.bytes).hexdigest())
        self.assertEqual(result["source_revision"], 9)
        self.assertEqual(result["status"], "sha256-content-verified")
        self.assertFalse(result["signed_authenticity"])
        self.assertFalse(result["human_acceptance"])

    def test_external_trusted_hash_can_anchor_consistency(self):
        good = hashlib.sha256(self.bytes).hexdigest()
        self.assertTrue(mod.verify_delivery(self.proof, good)["trusted_anchor_matched"])
        with self.assertRaisesRegex(mod.IntegrityError, "Trusted SHA-256 anchor"):
            mod.verify_delivery(self.proof, "f" * 64)
        with self.assertRaisesRegex(mod.IntegrityError, "Trusted SHA-256 anchor"):
            mod.verify_delivery(self.proof, "INVALID")

    def test_tampered_video_hash_fails_closed(self):
        self.master.write_bytes(self.bytes[:-1] + b"x")
        with self.assertRaisesRegex(mod.IntegrityError, "SHA-256 differs"):
            mod.verify_delivery(self.proof)

    def test_video_byte_count_change_fails_before_hashing(self):
        self.master.write_bytes(self.bytes + b"added")
        with self.assertRaisesRegex(mod.IntegrityError, "byte count"):
            mod.verify_delivery(self.proof)

    def test_missing_media_is_not_self_certifying(self):
        self.master.unlink()
        with self.assertRaisesRegex(mod.IntegrityError, "does not exist"):
            mod.verify_delivery(self.proof)

    def test_media_symlink_is_never_followed(self):
        original = self.root / "outside.mp4"
        original.write_bytes(self.bytes)
        self.master.unlink()
        self.master.symlink_to(original)
        with self.assertRaisesRegex(mod.IntegrityError, "symlink"):
            mod.verify_delivery(self.proof)

    def test_receipt_symlink_is_never_followed(self):
        outside = self.root / "other.json"
        outside.write_bytes(self.proof.read_bytes())
        self.proof.unlink()
        self.proof.symlink_to(outside)
        with self.assertRaisesRegex(mod.IntegrityError, "symlink"):
            mod.verify_delivery(self.proof)

    def test_manifest_media_filename_cannot_escape_directory(self):
        for forged in ("../../user.mp4", "/tmp/private.mp4", "sub\\asset.mp4", "bad\nname.mp4"):
            with self.subTest(forged=forged):
                self.original["media"]["filename"] = forged
                self.write()
                with self.assertRaisesRegex(mod.IntegrityError, "basename"):
                    mod.verify_delivery(self.proof)

    def test_invalid_or_extra_schema_fields_fail_closed(self):
        for key, value in (("scope", "SIGNED"), ("schema", "older"), ("new", 1)):
            with self.subTest(field=key):
                saved = copy.deepcopy(self.original)
                self.original[key] = value
                self.write()
                with self.assertRaises(mod.IntegrityError):
                    mod.verify_delivery(self.proof)
                self.original = saved

    def test_source_values_must_be_canonical(self):
        problems = [
            ("frame_count", 0),
            ("revision", -1),
            ("native_profile", "hevc"),
            ("generation", "not-a-uuid"),
            ("semwright_native_sdk_revision", "branch-main"),
            ("frame_rate", {"num": 0, "den": 1}),
        ]
        for field, value in problems:
            with self.subTest(field=field):
                saved = copy.deepcopy(self.original)
                self.original["origin"][field] = value
                self.write()
                with self.assertRaises(mod.IntegrityError):
                    mod.verify_delivery(self.proof)
                self.original = saved

    def test_fake_media_hash_or_size_is_rejected(self):
        for field, value in (("sha256", "wrong"), ("size_bytes", -1), ("size_bytes", True)):
            with self.subTest(field=field):
                saved = copy.deepcopy(self.original)
                self.original["media"][field] = value
                self.write()
                with self.assertRaises(mod.IntegrityError):
                    mod.verify_delivery(self.proof)
                self.original = saved

    def test_explicit_parent_traversal_is_rejected_even_when_target_exists(self):
        nested = self.root / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(mod.IntegrityError, "parent traversal"):
            mod.verify_delivery(nested / ".." / self.proof.name)

    def test_media_header_is_checked_not_codec_authenticity(self):
        bad = b"\x00\x00\x00\x18bad!isom\x00\x00\x00\x00body"
        self.master.write_bytes(bad)
        self.original["media"]["size_bytes"] = len(bad)
        self.original["media"]["sha256"] = hashlib.sha256(bad).hexdigest()
        self.write()
        with self.assertRaisesRegex(mod.IntegrityError, "ftyp"):
            mod.verify_delivery(self.proof)

    def test_oversize_manifest_is_rejected_without_parsing(self):
        self.proof.write_bytes(b"x" * (mod.MAX_DESCRIPTOR_BYTES + 1))
        with self.assertRaisesRegex(mod.IntegrityError, "larger than 12 KiB"):
            mod.verify_delivery(self.proof)


if __name__ == "__main__":
    unittest.main()
