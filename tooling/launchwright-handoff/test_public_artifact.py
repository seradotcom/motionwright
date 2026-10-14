"""No provider, Launchwright account, network or release: public facts only."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest

from export_handoff import package
import test_handoff
from public_artifact import publish, PublicRefused, ZIP_NAME

SHA = "a" * 40


class HandoffPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mw-handoff-publisher-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.private = self.root / "owner-evidence"
        self.private.mkdir()
        self.public = self.root / "public"
        self.owner = test_handoff.HandoffContractTests()
        self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)
        self.status = package(self.owner.request, self.owner.root,
                              self.private / ZIP_NAME)
        self.status['motionwright_source_sha'] = SHA
        (self.private / "status.json").write_text(json.dumps(self.status))
        (self.private / "private-owner-tickets.json").write_text(
            '{"session_ticket":"SECRET-NOT-PUBLISHED"}')

    def test_verified_synthetic_archive_stays_private(self):
        result = publish(self.private, self.public, SHA, "success")
        self.assertEqual(result["technical_handoff"], "PASS")
        self.assertFalse(result["archive_uploaded"])
        self.assertFalse(result["publication_authorized"])
        self.assertFalse(result["v05_release_ready"])
        self.assertEqual([p.name for p in self.public.iterdir()],
                         ["public-evidence.json"])
        published = (self.public / "public-evidence.json").read_text()
        self.assertNotIn("SECRET-NOT-PUBLISHED", published)
        self.assertNotIn("session_ticket", published)
        self.assertNotIn(self.owner.project.name, published)
        self.assertEqual(result["handoff_archive_sha256"],
                         self.status["archive_sha256"])

    def test_failed_unknown_and_skipped_have_no_verified_artifacts(self):
        for status in ("failure", "cancelled", "skipped"):
            target = self.root / status
            output = publish(self.private, target, SHA, status)
            self.assertEqual(output["technical_handoff"], "NOT_VERIFIED")
            self.assertIsNone(output["handoff_archive_sha256"])
            self.assertEqual([p.name for p in target.iterdir()],
                             ["public-evidence.json"])

    def test_tampered_archive_cannot_be_certified(self):
        with (self.private / ZIP_NAME).open("ab") as f:
            f.write(b"changed")
        with self.assertRaisesRegex(PublicRefused, "unverified_synthetic_handoff"):
            publish(self.private, self.public, SHA, "success")
        self.assertFalse(self.public.exists())

    def test_foreign_commit_cannot_reuse_verified_synthetic_handoff(self):
        self.status["motionwright_source_sha"] = "b" * 40
        (self.private / "status.json").write_text(json.dumps(self.status))
        with self.assertRaisesRegex(PublicRefused, "unverified_synthetic_handoff"):
            publish(self.private, self.public, SHA, "success")
        self.assertFalse(self.public.exists())

    def test_forged_publication_status_rejected(self):
        self.status["publication_approved"] = True
        (self.private / "status.json").write_text(json.dumps(self.status))
        with self.assertRaisesRegex(PublicRefused, "unverified_synthetic_handoff"):
            publish(self.private, self.public, SHA, "success")

    def test_duplicate_status_json_and_existing_output_rejected(self):
        (self.private / "status.json").write_text('{"status":"a","status":"b"}')
        with self.assertRaisesRegex(PublicRefused, "ambiguous_status_keys"):
            publish(self.private, self.public, SHA, "success")
        self.public.mkdir()
        with self.assertRaisesRegex(PublicRefused, "existing_output"):
            publish(self.private, self.public, SHA, "success")

    def test_nonfinite_status_json_is_rejected(self):
        (self.private / "status.json").write_text('{"status":"synthetic","extra":NaN}')
        with self.assertRaisesRegex(PublicRefused, "non_finite_status"):
            publish(self.private, self.public, SHA, "success")
        self.assertFalse(self.public.exists())

    def test_private_root_overlap_is_never_published(self):
        with self.assertRaisesRegex(PublicRefused, "overlapping_source_and_public"):
            publish(self.private, self.private / "untrusted", SHA, "failure")


if __name__ == "__main__":
    unittest.main()
