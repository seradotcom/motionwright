#!/usr/bin/env python3
"""v0.1 private intake manifest and NTFS alias adversarial regressions.

All identities, records, and evidence bytes here are synthetic fixtures.
These tests cannot mark one of the 60 private human acceptance cases PASS.
"""
from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "acceptance_session.py"
SPEC = importlib.util.spec_from_file_location("acceptance_session", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

SOURCE_SHA = "a" * 40


class ManifestAndAliasTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mw01-manifest-alias-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "ACC-001").mkdir()
        (self.root / "ACC-002").mkdir()
        self.evidence = self.root / "ACC-001" / "frame.bin"
        self.evidence.write_bytes(b"synthetic-fixture-not-product-evidence")
        self.doc = mod.create_session(source_sha=SOURCE_SHA)
        self.manifest = self.root / "session.json"
        self.manifest.write_text(json.dumps(self.doc), encoding="utf-8")

    def _realistic_pass(self):
        case = self.doc["cases"][0]
        case.update({
            "status": "PASS",
            "executed_by": "fixture-operator",
            "reviewed_by": "fixture-reviewer",
            "independent": True,
            "executed_at_utc": "2026-10-14T09:00:00Z",
            "reviewed_at_utc": "2026-10-14T10:00:00Z",
            "notes": "synthetic suite only: no actual case execution",
            "evidence": [mod.fingerprint(self.root, "ACC-001/frame.bin")],
        })
        self.manifest.write_text(json.dumps(self.doc), encoding="utf-8")

    def test_regular_manifest_has_unchanged_public_shape(self):
        loaded = mod.load_private_manifest(self.manifest)
        self.assertEqual(loaded, self.doc)
        with patch.object(mod, "checked_intake_source", return_value=SOURCE_SHA):
            counts = mod.verify_private_session(self.manifest, evidence_root=self.root)
        self.assertEqual(counts["NOT_RUN"], 60)
        self.assertEqual(counts["PASS"], 0)

    def test_manifest_hardlinks_are_rejected(self):
        alias = self.root / "linked-session.json"
        try:
            os.link(self.manifest, alias)
        except (OSError, NotImplementedError):
            self.skipTest("Filesystem cannot create hardlinks")
        with self.assertRaisesRegex(mod.IntakeError, "single-linked"):
            mod.load_private_manifest(self.manifest)

    def test_manifest_symlink_is_rejected(self):
        alias = self.root / "alias.json"
        try:
            alias.symlink_to(self.manifest)
        except OSError:
            self.skipTest("Symlink creation is restricted on this host")
        with self.assertRaisesRegex(mod.IntakeError, "symlink or reparse"):
            mod.load_private_manifest(alias)

    def test_manifest_descriptor_swap_during_open_is_refused(self):
        actual_fstat = os.fstat
        def changed(fd):
            info = actual_fstat(fd)
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1,
                st_size=info.st_size, st_mtime_ns=info.st_mtime_ns,
                st_nlink=info.st_nlink, st_mode=info.st_mode)
        with patch.object(mod.os, "fstat", side_effect=changed):
            with self.assertRaisesRegex(mod.IntakeError, "identity changed before reading"):
                mod.load_private_manifest(self.manifest)

    def test_manifest_reparse_attribute_is_refused_even_without_symlink_mode(self):
        real_lstat = Path.lstat
        target = self.manifest
        def forged(path, *args, **kwargs):
            result = real_lstat(path, *args, **kwargs)
            if Path(path) == target:
                return SimpleNamespace(st_mode=result.st_mode,
                    st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            return result
        with patch.object(mod.Path, "lstat", autospec=True, side_effect=forged):
            with self.assertRaisesRegex(mod.IntakeError, "symlink or reparse"):
                mod.load_private_manifest(self.manifest)

    def test_evidence_reparse_directory_is_refused_without_symlink_mode(self):
        target = self.root / "ACC-001"
        real_lstat = Path.lstat
        def forged(path, *args, **kwargs):
            result = real_lstat(path, *args, **kwargs)
            if Path(path) == target:
                return SimpleNamespace(st_mode=result.st_mode,
                    st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            return result
        with patch.object(mod.Path, "lstat", autospec=True, side_effect=forged):
            with self.assertRaisesRegex(mod.IntakeError, "symlink or reparse"):
                mod.fingerprint(self.root, "ACC-001/frame.bin")

    def test_ntfs_stream_and_drive_syntax_are_not_portable_evidence_paths(self):
        for relative in ("ACC-001/frame.bin:hidden", "C:relative.bin", "ACC-001/C:drive"):
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(mod.IntakeError, "stream or drive"):
                    mod.fingerprint(self.root, relative)

    @unittest.skipUnless(sys.platform == "win32", "Real NTFS junction is Windows-only")
    def test_real_windows_junction_cannot_smuggle_other_case_evidence(self):
        real = self.root / "ACC-002" / "frame.bin"
        real.write_bytes(b"other-case-synthetic-fixture")
        alias = self.root / "ACC-001" / "alias"
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(real.parent)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode:
            self.skipTest("Creating Windows directory junction is restricted")
        # Crucially, the canonical target is still under evidence_root, so
        # the previous root-only containment check admitted this alias.
        self.assertTrue((alias / "frame.bin").exists())
        with self.assertRaisesRegex(mod.IntakeError, "symlink or reparse"):
            mod.fingerprint(self.root, "ACC-001/alias/frame.bin")
        # Windows rmdir removes junction only; never traverse/delete target.
        self.addCleanup(lambda: subprocess.run(
            ["cmd", "/c", "rmdir", str(alias)], capture_output=True, text=True))
        self.assertTrue(real.exists())

    @unittest.skipUnless(sys.platform == "win32", "Real NTFS junction is Windows-only")
    def test_real_junction_ancestor_of_private_root_or_manifest_is_refused(self):
        alias = self.root.parent / (self.root.name + "-alias-parent")
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(self.root)],
                              capture_output=True, text=True)
        if proc.returncode:
            self.skipTest("Creating Windows directory junction is restricted")
        self.addCleanup(lambda: subprocess.run(
            ["cmd", "/c", "rmdir", str(alias)], capture_output=True, text=True))
        # The final ACC-001 directory and session.json are ordinary files:
        # the prior implementation checked those but missed their linked ancestor.
        self.assertTrue((alias / "ACC-001" / "frame.bin").is_file())
        with self.assertRaisesRegex(mod.IntakeError, "symlink or reparse"):
            mod.fingerprint(alias / "ACC-001", "frame.bin")
        with self.assertRaisesRegex(mod.IntakeError, "symlink or reparse"):
            mod.load_private_manifest(alias / "session.json")
        self.assertTrue(self.evidence.exists())

    def test_private_root_parent_reparse_attribute_is_rejected(self):
        parent = self.root.parent
        actual_lstat = Path.lstat
        def modified(path, *args, **kwargs):
            info = actual_lstat(path, *args, **kwargs)
            if Path(path) == parent:
                return SimpleNamespace(st_mode=info.st_mode,
                    st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            return info
        with patch.object(mod.Path, "lstat", autospec=True, side_effect=modified):
            with self.assertRaisesRegex(mod.IntakeError, "symlink or reparse"):
                mod.fingerprint(self.root, "ACC-001/frame.bin")

    def test_manifest_same_bytes_inode_replacement_after_evidence_is_refused(self):
        self._realistic_pass()
        actual_fp = mod.fingerprint
        replaced = False
        def replace_after_read(root, relative):
            nonlocal replaced
            descriptor = actual_fp(root, relative)
            if not replaced:
                replaced = True
                other = self.root / "replacement.json"
                other.write_bytes(self.manifest.read_bytes())
                os.replace(other, self.manifest)
            return descriptor
        with patch.object(mod, "checked_intake_source", return_value=SOURCE_SHA), \
             patch.object(mod, "fingerprint", side_effect=replace_after_read):
            with self.assertRaisesRegex(mod.IntakeError, "manifest changed during"):
                mod.verify_private_session(self.manifest, evidence_root=self.root)
        self.assertTrue(replaced)

    def test_manifest_same_size_in_place_change_with_restored_mtime_is_refused(self):
        self._realistic_pass()
        actual_fp = mod.fingerprint
        changed = False
        def change_after_read(root, relative):
            nonlocal changed
            descriptor = actual_fp(root, relative)
            if not changed:
                changed = True
                old = self.manifest.stat()
                raw = self.manifest.read_bytes()
                # Same size and restored mtime, but a different source claim.
                modified = raw.replace(SOURCE_SHA.encode(), ("b" * 40).encode(), 1)
                self.assertEqual(len(modified), len(raw))
                self.assertNotEqual(modified, raw)
                self.manifest.write_bytes(modified)
                os.utime(self.manifest, ns=(old.st_atime_ns, old.st_mtime_ns))
            return descriptor
        with patch.object(mod, "checked_intake_source", return_value=SOURCE_SHA), \
             patch.object(mod, "fingerprint", side_effect=change_after_read):
            with self.assertRaisesRegex(mod.IntakeError, "manifest changed during"):
                mod.verify_private_session(self.manifest, evidence_root=self.root)
        self.assertTrue(changed)

    def test_original_manifest_and_evidence_remain_private_after_verification(self):
        self._realistic_pass()
        with patch.object(mod, "checked_intake_source", return_value=SOURCE_SHA):
            counts = mod.verify_private_session(self.manifest, evidence_root=self.root)
        self.assertEqual(counts["PASS"], 1)  # SYNTHETIC ONLY, never public ledger
        self.assertNotIn(str(self.root), str(counts))
        self.assertFalse((mod.ROOT / "docs/acceptance" / "session.json").exists())


if __name__ == "__main__":
    unittest.main()
