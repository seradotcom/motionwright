import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("creative_status_policy",ROOT/"tooling/creative_status_policy.py")
policy=importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

class CreativeStatusPolicyTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((ROOT/"docs/creative-production/requirements-status.json").read_text())

    def test_real_ledger_is_complete_in_identity_but_not_a_release_claim(self):
        policy.validate(self.data)
        self.assertEqual(len(self.data["requirements"]),64)
        self.assertFalse(self.data["v05_release_ready"])

    def test_renamed_or_duplicate_requirement_does_not_reset_history(self):
        for value in ["MW05-E00-99",self.data["requirements"][1]["id"]]:
            changed=copy.deepcopy(self.data);changed["requirements"][0]["id"]=value
            with self.assertRaises(ValueError):policy.validate(changed)

    def test_automatic_acceptance_and_release_promotion_are_rejected(self):
        changed=copy.deepcopy(self.data);changed["v05_release_ready"]=True
        with self.assertRaises(ValueError):policy.validate(changed)
        changed=copy.deepcopy(self.data);changed["requirements"][0]["complete_requirement_acceptance"]="PASS"
        with self.assertRaises(ValueError):policy.validate(changed)

    def test_missing_or_escaping_source_evidence_is_rejected(self):
        for value in ["crates/nonexistent-v05-capability.rs","../private-path"]:
            changed=copy.deepcopy(self.data);changed["requirements"][0]["source_paths"]=[value]
            with self.assertRaises(ValueError):policy.validate(changed)

if __name__=="__main__":unittest.main()
