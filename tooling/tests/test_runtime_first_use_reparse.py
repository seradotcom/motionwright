"""v0.1 standalone junction/reparse acceptance tests, with no owner data."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime_first_use_audit as audit


class ReparseRejectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-safe-path-fixture-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.folder = self.root / "trusted"
        self.folder.mkdir()
        self.file = self.folder / "session.json"
        self.file.write_text('{"dummy":true}', encoding="utf8")

    @staticmethod
    def reparse_at(target: Path):
        original = Path.lstat

        def forged_lstat(path: Path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == target:
                return SimpleNamespace(
                    st_mode=info.st_mode,
                    st_file_attributes=0x400,
                )
            return info

        return patch.object(Path, "lstat", autospec=True,
                            side_effect=forged_lstat)

    def test_valid_plain_file_and_directory_still_allowed(self):
        self.assertEqual(audit.safe_path(str(self.file),
                                         kind="regular",
                                         code="session_invalid"), self.file)
        self.assertEqual(audit.safe_path(str(self.folder),
                                         kind="directory",
                                         code="output_root_invalid"), self.folder)

    def test_windows_reparse_leaf_cannot_be_a_connection_file(self):
        with self.reparse_at(self.file):
            with self.assertRaises(audit.AuditError) as caught:
                audit.safe_path(str(self.file), kind="regular",
                                code="connection_invalid")
        self.assertEqual(caught.exception.code, "connection_invalid")

    def test_windows_reparse_parent_blocks_private_session(self):
        with self.reparse_at(self.folder):
            with self.assertRaises(audit.AuditError) as caught:
                audit.safe_path(str(self.file), kind="regular",
                                code="session_invalid")
        self.assertEqual(caught.exception.code, "session_invalid")

    def test_windows_reparse_output_root_cannot_be_used(self):
        with self.reparse_at(self.folder):
            with self.assertRaises(audit.AuditError) as caught:
                audit.safe_path(str(self.folder), kind="directory",
                                code="output_root_invalid")
        self.assertEqual(caught.exception.code, "output_root_invalid")

    def test_windows_reparse_blocks_receipt_destination_parent(self):
        with self.reparse_at(self.folder):
            with self.assertRaises(audit.AuditError) as caught:
                audit.write_new_receipt(self.folder / "receipt.json",
                                        {"status": "not_authorized"})
        self.assertEqual(caught.exception.code, "receipt_destination_invalid")
        self.assertFalse((self.folder / "receipt.json").exists())

    def test_python312_junction_probe_is_also_fail_closed(self):
        original = getattr(Path, "is_junction", None)
        def forged_junction(path):
            return path == self.folder or (
                bool(original(path)) if callable(original) else False
            )
        with patch.object(Path, "is_junction", forged_junction, create=True):
            with self.assertRaises(audit.AuditError) as caught:
                audit.safe_path(str(self.file), kind="regular",
                                code="session_invalid")
        self.assertEqual(caught.exception.code, "session_invalid")

    @unittest.skipUnless(sys.platform == "win32", "NTFS junction requires Windows")
    def test_actual_unprivileged_windows_junction_is_refused(self):
        # Confined to a new temporary directory: never links or reads owner data.
        alias = self.root / "synthetic-junction"
        try:
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J",
                 str(alias), str(self.folder)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("Junction creation unavailable in this test environment")
        if result.returncode != 0:
            self.skipTest("Unprivileged junction creation unavailable")
        try:
            self.assertTrue((alias / "session.json").exists())
            with self.assertRaises(audit.AuditError) as caught:
                audit.safe_path(str(alias / "session.json"),
                                kind="regular", code="session_invalid")
            self.assertEqual(caught.exception.code, "session_invalid")
        finally:
            # rmdir removes only our junction, never its temporary target.
            alias.rmdir()

    def test_no_internal_path_exported_on_refused_reparse(self):
        with self.reparse_at(self.file):
            with self.assertRaises(audit.AuditError) as caught:
                audit.safe_path(str(self.file), kind="regular",
                                code="session_invalid")
        self.assertEqual(str(caught.exception), "session_invalid")
        self.assertNotIn(str(self.root), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
