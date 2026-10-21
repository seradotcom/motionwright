"""Keep compilation evidence separate from required native execution."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Import the module so unittest does not rediscover its TestCase class here.
from tooling.tests import test_conformance_policy as fixtures


class ExecutionSurfaceBoundaryTests(unittest.TestCase):
    def test_compilation_receipt_cannot_satisfy_native_execution(self):
        fixture = fixtures.ConformancePolicyTests(
            "test_complete_technical_review_does_not_require_human_or_grant_acceptance"
        )
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        policy = fixtures.policy
        fixture.index["execution_profiles"].update({
            "locked-consumer-compilation": {"kind": "integration"},
            "native-host-execution": {"kind": "native"},
        })
        fixture.index["records"][0]["execution"].update(
            kind="integration", profile="locked-consumer-compilation"
        )
        fixture.assessment["technical_verification"]["execution_kinds"] = [
            "integration"
        ]
        fixture.populate_sufficiency()
        fixture.validate()
        self.assertEqual(fixture.assessment["contract_acceptance"]["status"], "open")
        record_digest = policy.canonical_digest(fixture.index["records"][0])
        for profile in fixture.index["verification_profiles"].values():
            profile["obligations"]["observed-result"]["execution_profiles"] = [
                "native-host-execution"
            ]
        for review in fixture.index["sufficiency_reviews"]:
            review["oracle_profile_sha256"] = policy.canonical_digest(
                fixture.index["verification_profiles"][review["criterion_id"]]
            )
            self.assertEqual(
                review["evidence_record_sha256"]["source-execution"], record_digest
            )
        self.assertEqual(
            policy.canonical_digest(fixture.index["records"][0]), record_digest
        )
        with self.assertRaisesRegex(ValueError, "execution surface"):
            fixture.validate()
        self.assertEqual(fixture.assessment["contract_acceptance"]["status"], "open")


if __name__ == "__main__":
    unittest.main()
