"""Exercise retained source evidence against a real Windows directory junction."""
from pathlib import Path
import errno
import os
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_source_lock_admission import SourceLockAdmissionFixture


class SourceLockAdmissionNtfsTests(SourceLockAdmissionFixture, unittest.TestCase):
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
