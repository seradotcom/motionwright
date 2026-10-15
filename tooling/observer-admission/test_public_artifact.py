"""Strict public CI boundary; synthetic signing keys never leave the runner."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from public_artifact import publish, PublicationRefused
import test_observer

SHA = "a" * 40


class SignedPublicFactsTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="mw-observer-public-")
        self.addCleanup(t.cleanup)
        self.base = Path(t.name)
        self.private = self.base / "ci"
        self.private.mkdir()
        self.public = self.base / "public"
        self.owner = test_observer.SignedVerifierTests(
            methodName="test_separate_owner_and_observer_signatures_plus_actual_png_decode")
        self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)
        result = self.owner.check()
        assert result["gate_eligible"] and result["actual_source_png_decode"] == "VERIFIED"
        self.original_png = self.private / "native-source-frame-000030.png"
        shutil.copyfile(self.owner.image, self.original_png)
        self.result = result
        self.result.update({
            "motionwright_source_sha": SHA,
            "ci_test_classification":
                "EPHEMERAL_SYNTHETIC_KEYS_NO_REAL_OWNER_AUTHENTICATION",
            "gate_eligible": False,
            "status": "CI_TEST_ONLY_NOT_AN_OWNER_PRODUCTION_ADMISSION",
        })
        self.owner.policy["owner_generation"] = 8
        self.owner.policy["revoked_ids"] = [self.owner.admission["admission_id"]]
        self.owner.write_all()
        assert not self.owner.check()["gate_eligible"]
        self.revocation = {
            "schema": "motionwright.observation-ci-revocation/1",
            "motionwright_source_sha": SHA,
            "original_png_sha256":
                hashlib.sha256(self.original_png.read_bytes()).hexdigest(),
            "signed_owner_policy_revoked_current_admission": "VERIFIED",
            "current_gate_eligible": False,
            "historical_observation_bytes_preserved": True,
            "artifact_contains_private_keys": False,
            "trusted_key_provisioning": "EPHEMERAL_CI_ONLY",
            "real_production_host_admission": "NOT_RUN",
        }
        self.save()

    def save(self):
        (self.private / "source-observation.json").write_text(
            json.dumps(self.result, sort_keys=True))
        (self.private / "owner-revocation-experiment.json").write_text(
            json.dumps(self.revocation, sort_keys=True))

    def refuse(self, code: str):
        with self.assertRaises(PublicationRefused) as caught:
            publish(self.private, self.public, SHA, "success")
        self.assertIn(code, str(caught.exception))
        self.assertFalse(self.public.exists())

    def test_success_is_manifest_only_no_private_signers(self):
        public = publish(self.private, self.public, SHA, "success")
        self.assertEqual(public["technical_ci_evidence"], "PASS")
        self.assertEqual([p.name for p in self.public.iterdir()],
                         ["public-evidence.json"])
        self.assertTrue(public["revocation_blocks_current_admission"])
        self.assertFalse(public["original_png_uploaded"])
        self.assertFalse(public["signing_keys_uploaded"])
        self.assertFalse(public["production_verifier_admitted"])
        self.assertFalse(public["publication_authorized"])
        self.assertEqual(public["human_acceptance"], "NOT_RUN")
        data = (self.public / "public-evidence.json").read_text()
        self.assertNotIn("verifier_public_key_b64", data)
        self.assertNotIn("signature_b64", data)
        self.assertNotIn("source_frame_path", data)

    def test_unknown_native_outcome_cannot_publish_stale_verified_facts(self):
        for status in ("failure", "skipped", "cancelled"):
            dest = self.base / ("result-" + status)
            output = publish(self.private, dest, SHA, status)
            self.assertEqual(output["technical_ci_evidence"], "NOT_VERIFIED")
            self.assertIsNone(output["synthetic_frame_sha256"])
            self.assertIsNone(output["synthetic_decoded_pixels"])
            self.assertFalse(output["separate_ephemeral_keys_checked"])
            self.assertEqual([p.name for p in dest.iterdir()],
                             ["public-evidence.json"])

    def test_other_git_commit_cannot_reuse_signed_source(self):
        self.result["motionwright_source_sha"] = "b" * 40
        self.save()
        self.refuse("unverified_or_foreign_signed_ci_observation")

    def test_changed_original_frame_even_same_dimensions_is_not_pass(self):
        from PIL import Image
        Image.new("RGBA", (320, 180), (255, 24, 24, 255)).save(self.original_png)
        self.refuse("unverified_or_foreign_signed_ci_observation")

    def test_revocation_absent_or_claimed_valid_is_not_pass(self):
        self.revocation["current_gate_eligible"] = True
        self.save()
        self.refuse("unverified_or_foreign_signed_ci_observation")

    def test_producer_cannot_claim_user_creative_approval(self):
        self.result["creative_quality_approved"] = True
        self.save()
        self.refuse("unverified_or_foreign_signed_ci_observation")

    def test_ambiguous_nested_ci_observation_json_rejected(self):
        p = self.private / "source-observation.json"
        raw = p.read_text()
        assert '"width": 320' in raw
        p.write_text(raw.replace('"width": 320',
                                 '"width": 320, "width": 999', 1))
        self.refuse("ambiguous_or_invalid_observation_json")

    def test_existing_output_and_source_overlap_refused(self):
        self.public.mkdir()
        with self.assertRaisesRegex(PublicationRefused,
                                    "public_target_already_exists"):
            publish(self.private, self.public, SHA, "success")
        with self.assertRaisesRegex(PublicationRefused,
                                    "private_public_directory_overlap"):
            publish(self.private, self.private / "nested-public", SHA, "success")


if __name__ == "__main__":
    unittest.main()
