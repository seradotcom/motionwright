"""Adversarial publisher tests: no owner DB, native execution or network."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import public_artifact as pub

MW_SHA = "a" * 40


class TwoClientPublicTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mw-two-client-facts-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.private = self.root / "owner-private"
        self.private.mkdir()
        self.public = self.root / "public"
        self.report = {
            "schema": "motionwright.v05-local-agent-two-process/1",
            "status": "PASS_OWNER_LOCAL_CAS_AND_NATIVE_SDK_READ",
            "same_sha": MW_SHA, "same_sqlite_project": True,
            "independent_subprocess_per_request": True,
            "shared_chat": False,
            "native_sdk_catalog_operations": 74,
            "sdk_observation_scopes": ["summary", "timeline", "history"],
            "owner_local_cas_revisions": 3,
            "stale_revision_rejected": True,
            "stale_page_cursor_rejected": True,
            "unknown_operation_rejected": True,
            "cross_generation_rejected": True,
            "read_only_preview": "PASS",
            "canonical_database_backend": "existing_studio_service",
            "host_authenticated_agent_write": "NOT_TESTED_OR_GRANTED",
            "remote_listener": "NONE",
            "creative_approval": "NOT_RUN",
            "events": [
                {"actor": "agent_a" if n % 2 == 0 else "agent_b",
                 "action": "observe" if n % 3 else "discover",
                 "status": "PASS", "revision": n // 6}
                for n in range(24)
            ],
            "private_owner_db": "C:/Owner/Secret/production.sqlite3",
            "owner_note": "DO-NOT-PUBLISH-AUTHORIZATION-TOKEN",
        }
        self.write()

    def write(self):
        (self.private / "result.json").write_text(json.dumps(self.report))

    def deny(self, code: str):
        with self.assertRaises(pub.Refused) as caught:
            pub.publish(self.private, self.public, MW_SHA, "success")
        self.assertEqual(str(caught.exception), code)
        self.assertFalse(self.public.exists())

    def test_only_bounded_source_bound_facts_are_public(self):
        receipt = pub.publish(self.private, self.public, MW_SHA, "success")
        self.assertEqual(receipt["ci_evidence"], "PASS")
        self.assertEqual(receipt["registered_native_sdk_operations"], 74)
        self.assertEqual(receipt["independent_os_invocations"], 24)
        self.assertEqual(receipt["owner_local_committed_revisions"], 3)
        self.assertEqual([x.name for x in self.public.iterdir()],
                         ["public-evidence.json"])
        self.assertFalse(receipt["owner_database_or_sqlite_uploaded"])
        self.assertFalse(receipt["native_mutation_retried_by_publication"])
        self.assertEqual(receipt["host_authenticated_remote_write"],
                         "NOT_TESTED_OR_GRANTED")
        self.assertFalse(receipt["v05_release_ready"])
        text = (self.public / "public-evidence.json").read_text()
        self.assertNotIn("DO-NOT-PUBLISH", text)
        self.assertNotIn("production.sqlite3", text)
        self.assertNotIn("events", text)

    def test_failure_and_cancel_never_reuse_old_pass(self):
        for state in ("failure", "cancelled", "skipped"):
            directory = self.root / state
            receipt = pub.publish(self.private, directory, MW_SHA, state)
            self.assertEqual(receipt["ci_evidence"], "NOT_VERIFIED")
            self.assertNotIn("registered_native_sdk_operations", receipt)
            self.assertEqual([x.name for x in directory.iterdir()],
                             ["public-evidence.json"])

    def test_other_commit_cannot_attest_prior_source(self):
        self.report["same_sha"] = "b" * 40
        self.write()
        self.deny("unverified_or_unauthorized_two_client_claim")

    def test_no_remote_host_write_may_be_claimed(self):
        self.report["host_authenticated_agent_write"] = "VERIFIED"
        self.write()
        self.deny("unverified_or_unauthorized_two_client_claim")

    def test_no_forged_cas_revision_count(self):
        self.report["owner_local_cas_revisions"] = 4
        self.write()
        self.deny("unverified_or_unauthorized_two_client_claim")

    def test_preview_must_remain_read_only(self):
        self.report["read_only_preview"] = "COMMITTED"
        self.write()
        self.deny("unverified_or_unauthorized_two_client_claim")

    def test_unknown_actor_or_operation_denied(self):
        self.report["events"][0]["actor"] = "other-remote-agent"
        self.write()
        self.deny("unverified_or_unauthorized_two_client_claim")

    def test_source_count_is_bounded_and_typed(self):
        self.report["native_sdk_catalog_operations"] = True
        self.write()
        self.deny("unverified_or_unauthorized_two_client_claim")

    def test_duplicate_source_status_keys_refused(self):
        (self.private / "result.json").write_text(
            '{"same_sha":"' + MW_SHA + '","same_sha":"' + MW_SHA + '"}')
        self.deny("duplicate_owner_report_key")

    def test_existing_target_and_private_overlap_refused(self):
        self.public.mkdir()
        with self.assertRaisesRegex(pub.Refused, "existing_public_destination"):
            pub.publish(self.private, self.public, MW_SHA, "success")
        with self.assertRaisesRegex(pub.Refused, "private_public_path_overlap"):
            pub.publish(self.private, self.private / "output", MW_SHA, "failure")


if __name__ == "__main__":
    unittest.main()
