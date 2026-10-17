import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import acceptance_policy as policy


class AcceptancePolicyTests(unittest.TestCase):
    def setUp(self):
        self.requirements = json.loads((ROOT / "docs/acceptance/requirements-status.json").read_text(encoding="utf-8"))
        self.acceptance = json.loads((ROOT / "docs/acceptance/acceptance-status.json").read_text(encoding="utf-8"))

    def test_current_ledgers_keep_legacy_identity_and_do_not_manufacture_acceptance(self):
        counts = policy.validate(self.requirements, self.acceptance)
        self.assertEqual(counts[:2], (208, 60))
        recorded = self.acceptance["cases"] + self.acceptance.get("expansion_cases", [])
        self.assertEqual(counts[3], sum(row["status"] == "PASS" for row in recorded))

    def test_pass_with_nonexistent_or_unsafe_evidence_is_rejected(self):
        for pointer in ("does-not-exist.json", "../private-evidence", "docs"):
            with self.subTest(pointer=pointer):
                changed = copy.deepcopy(self.acceptance)
                changed["cases"][0].update(status="PASS", independent=True, evidence=[pointer])
                with self.assertRaises(ValueError):
                    policy.validate(self.requirements, changed)

    def test_truthy_strings_and_numbers_are_not_independent_review(self):
        for independent in ("false", "true", 1, 0, [], {}):
            with self.subTest(independent=independent):
                changed = copy.deepcopy(self.acceptance)
                changed["cases"][0].update(status="PASS", independent=independent, evidence=["README.md"])
                with self.assertRaises(ValueError):
                    policy.validate(self.requirements, changed)

    def test_readme_presence_does_not_establish_verified_implementation(self):
        changed = copy.deepcopy(self.requirements)
        changed["requirements"][0].update(implementation_assessment="VERIFIED",
                                          verification_status="VERIFIED", evidence=["README.md"])
        changed["requirements"][0].pop("technical_assessment", None)
        with self.assertRaises(ValueError):
            policy.validate(changed, self.acceptance)

    def test_a_boolean_and_existing_file_do_not_establish_contract_acceptance(self):
        changed = copy.deepcopy(self.acceptance)
        changed["cases"][0].update(status="PASS", independent=True, evidence=["README.md"])
        changed["cases"][0].pop("technical_assessment", None)
        with self.assertRaises(ValueError):
            policy.validate(self.requirements, changed)

    def test_expansion_case_identity_cannot_be_dropped_duplicated_or_renamed(self):
        complete = [{"id": identity, "status": "NOT_RUN", "independent": False, "evidence": []}
                    for identity in sorted(policy.EXPECTED_A05)]
        for rows in (complete[:-1], complete[:-1] + [copy.deepcopy(complete[0])],
                     complete[:-1] + [{"id": "A05-E99-99"}]):
            with self.subTest(count=len(rows)), self.assertRaises(ValueError):
                policy.exact_rows(rows, policy.EXPECTED_A05, "expansion")

    def test_legacy_row_cardinality_and_history_remain_mandatory(self):
        changed = copy.deepcopy(self.requirements)
        changed["requirements"][0]["id"] = changed["requirements"][1]["id"]
        with self.assertRaises(ValueError):
            policy.validate(changed, self.acceptance)


    def test_source_record_identity_cannot_be_rewritten(self):
        changed = copy.deepcopy(self.requirements)
        changed["requirements"][0]["source_record_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "preserved source record"):
            policy.validate(changed, self.acceptance)

    def test_canonical_case_link_cannot_be_silently_dropped(self):
        changed = copy.deepcopy(self.requirements)
        changed["requirements"][0]["acceptance_case_ids"] = ["ACC-060"]
        with self.assertRaisesRegex(ValueError, "normative links"):
            policy.validate(changed, self.acceptance)


if __name__ == "__main__":
    unittest.main()
