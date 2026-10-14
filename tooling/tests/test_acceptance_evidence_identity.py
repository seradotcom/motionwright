"""v0.1 synthetic private evidence identity/path stability regressions.

No private acceptance specification, owner media, or human PASS verdicts are
stored, created, or evaluated in this test suite.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "acceptance_session.py"
SPEC = importlib.util.spec_from_file_location("acceptance_session", MODULE)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class EvidenceIdentityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="motionwright-v01-fixture-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "ACC-001").mkdir()
        self.file = self.root / "ACC-001" / "capture.bin"
        self.file.write_bytes(b"synthetic-private-evidence-fixture-only")

    def test_regular_file_keeps_public_descriptor_unchanged(self):
        actual = mod.fingerprint(self.root, "ACC-001/capture.bin")
        self.assertEqual(actual, {
            "path": "ACC-001/capture.bin",
            "sha256": hashlib.sha256(self.file.read_bytes()).hexdigest(),
            "size_bytes": self.file.stat().st_size,
        })
        self.assertNotIn(str(self.root), str(actual))
        self.assertNotIn("inode", str(actual).lower())

    def test_independent_copy_may_contain_equal_bytes(self):
        another = self.root / "ACC-001" / "copy.bin"
        another.write_bytes(self.file.read_bytes())
        self.assertEqual(mod.fingerprint(self.root, "ACC-001/capture.bin")["sha256"],
                         mod.fingerprint(self.root, "ACC-001/copy.bin")["sha256"])
        self.assertNotEqual(self.file.stat().st_ino, another.stat().st_ino)

    def test_hardlink_across_cases_is_rejected_for_both_names(self):
        (self.root / "ACC-002").mkdir()
        try:
            os.link(self.file, self.root / "ACC-002" / "capture.bin")
        except (NotImplementedError, PermissionError, OSError):
            self.skipTest("No hardlink support on this filesystem")
        for name in ("ACC-001/capture.bin", "ACC-002/capture.bin"):
            with self.subTest(path=name):
                with self.assertRaisesRegex(mod.IntakeError, "single-linked"):
                    mod.fingerprint(self.root, name)

    def test_synthetic_independent_review_cannot_admit_hardlinked_evidence(self):
        descriptor = mod.fingerprint(self.root, "ACC-001/capture.bin")
        session = mod.create_session(source_sha="a" * 40)
        first = session["cases"][0]
        first.update({
            "status": "PASS",
            "executed_by": "fixture-operator",
            "reviewed_by": "fixture-independent-reviewer",
            "independent": True,
            "executed_at_utc": "2026-10-14T10:00:00Z",
            "reviewed_at_utc": "2026-10-14T11:00:00Z",
            "notes": "synthetic test fixture only; no human acceptance",
            "evidence": [descriptor],
        })
        (self.root / "ACC-002").mkdir()
        try:
            os.link(self.file, self.root / "ACC-002" / "linked.bin")
        except (NotImplementedError, PermissionError, OSError):
            self.skipTest("No hardlink support on this filesystem")
        with self.assertRaisesRegex(mod.IntakeError, "single-linked"):
            mod.validate_session(session, evidence_root=self.root)

    def test_external_hardlink_is_also_rejected(self):
        foreign = self.root.parent / (self.root.name + "-hardlink.bin")
        self.addCleanup(lambda: foreign.unlink(missing_ok=True))
        try:
            os.link(self.file, foreign)
        except (NotImplementedError, PermissionError, OSError):
            self.skipTest("No hardlink support on this filesystem")
        with self.assertRaisesRegex(mod.IntakeError, "single-linked"):
            mod.fingerprint(self.root, "ACC-001/capture.bin")

    def test_missing_file_identity_fails_closed(self):
        real_stat = Path.stat
        original = self.file.stat()
        def forged_stat(path, *args, **kwargs):
            actual = real_stat(path, *args, **kwargs)
            if Path(path) == self.file:
                return SimpleNamespace(st_dev=actual.st_dev, st_ino=0,
                    st_size=actual.st_size, st_mtime_ns=actual.st_mtime_ns,
                    st_nlink=actual.st_nlink, st_mode=actual.st_mode)
            return actual
        with patch.object(mod.Path, "stat", autospec=True, side_effect=forged_stat):
            with self.assertRaisesRegex(mod.IntakeError, "single-linked"):
                mod.fingerprint(self.root, "ACC-001/capture.bin")
        self.assertGreater(original.st_ino, 0)

    def test_empty_file_not_evidence(self):
        self.file.write_bytes(b"")
        with self.assertRaisesRegex(mod.IntakeError, "empty"):
            mod.fingerprint(self.root, "ACC-001/capture.bin")

    def test_oversize_rejected_before_reading_file(self):
        with patch.object(mod, "MAX_EVIDENCE_BYTES", 2):
            with self.assertRaisesRegex(mod.IntakeError, "budget"):
                mod.fingerprint(self.root, "ACC-001/capture.bin")

    def test_fstat_change_during_hash_fails_closed(self):
        actual_fstat = os.fstat
        calls = 0
        def changed_fstat(fd):
            nonlocal calls
            calls += 1
            st = actual_fstat(fd)
            if calls == 2:
                return SimpleNamespace(st_dev=st.st_dev, st_ino=st.st_ino,
                    st_size=st.st_size, st_mtime_ns=st.st_mtime_ns + 1,
                    st_nlink=st.st_nlink)
            return st
        with patch.object(mod.os, "fstat", side_effect=changed_fstat):
            with self.assertRaisesRegex(mod.IntakeError, "changed while hashing"):
                mod.fingerprint(self.root, "ACC-001/capture.bin")
        self.assertEqual(calls, 2)

    def test_opened_descriptor_identity_must_match_prior_path(self):
        actual_fstat = os.fstat
        def forged_fstat(fd):
            st = actual_fstat(fd)
            return SimpleNamespace(st_dev=st.st_dev, st_ino=st.st_ino + 1,
                st_size=st.st_size, st_mtime_ns=st.st_mtime_ns,
                st_nlink=st.st_nlink, st_mode=st.st_mode)
        with patch.object(mod.os, "fstat", side_effect=forged_fstat):
            with self.assertRaisesRegex(mod.IntakeError, "changed before hashing"):
                mod.fingerprint(self.root, "ACC-001/capture.bin")

    def test_path_replacement_after_hash_fails_closed(self):
        actual_stat = Path.stat
        actual_fstat = os.fstat
        opened_checks = 0
        def count_fstat(fd):
            nonlocal opened_checks
            opened_checks += 1
            return actual_fstat(fd)
        def forged_stat(path, *args, **kwargs):
            st = actual_stat(path, *args, **kwargs)
            if Path(path) == self.file and opened_checks >= 2:
                return SimpleNamespace(st_dev=st.st_dev, st_ino=st.st_ino + 1,
                    st_size=st.st_size, st_mtime_ns=st.st_mtime_ns,
                    st_nlink=st.st_nlink, st_mode=st.st_mode)
            return st
        with patch.object(mod.os, "fstat", side_effect=count_fstat), \
             patch.object(mod.Path, "stat", autospec=True, side_effect=forged_stat):
            with self.assertRaisesRegex(mod.IntakeError, "changed after hashing"):
                mod.fingerprint(self.root, "ACC-001/capture.bin")
        self.assertEqual(opened_checks, 2)

    def test_os_error_becomes_safe_intake_failure(self):
        with patch.object(mod.os, "open", side_effect=PermissionError("private path never publish")):
            with self.assertRaisesRegex(mod.IntakeError, "^Evidence identity or bytes unavailable$") as err:
                mod.fingerprint(self.root, "ACC-001/capture.bin")
        self.assertNotIn("private path", str(err.exception))


if __name__ == "__main__":
    unittest.main()
