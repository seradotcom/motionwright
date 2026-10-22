"""Exercise retained source evidence against a real Windows directory junction."""
from pathlib import Path
import errno
import json
import os
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_source_lock_admission import SourceLockAdmissionFixture
from source_lock_admission import load_source_lock, verify_source_lock


class SourceLockAdmissionNtfsTests(SourceLockAdmissionFixture, unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows stat semantics required")
    def test_rewritten_source_lock_is_admitted_without_changing_its_bytes(self):
        path = self.root / "SOURCE_LOCK.json"
        raw = (json.dumps(self.lock) + "\n").encode("utf-8")
        path.write_bytes(raw)
        # Separate creation and change time on the actual Windows filesystem.
        time.sleep(0.02)
        path.write_bytes(raw)
        before = self.snapshot()
        loaded = load_source_lock(self.root)
        self.assertEqual(loaded, self.lock)
        self.assertIsNone(verify_source_lock(self.root, loaded))
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.name == "nt", "Windows junction semantics required")
    def test_junctioned_evidence_parent_is_rejected(self):
        import _winapi

        directory = self.evidence_path().parent
        retained = self.scratch / "junction-retained-evidence"
        directory.rename(retained)
        try:
            _winapi.CreateJunction(str(retained), str(directory))
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EPERM} or (
                getattr(error, "winerror", None) == 1314
            ):
                self.skipTest(
                    "junction creation denied: "
                    f"errno={error.errno}, winerror={getattr(error, 'winerror', None)}"
                )
            raise
        self.assert_rejected_without_changes()


if __name__ == "__main__":
    unittest.main()
