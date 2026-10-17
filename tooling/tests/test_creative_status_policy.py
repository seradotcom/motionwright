import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("creative_status_policy",ROOT/"tooling/creative_status_policy.py")
policy=importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
import conformance_policy as conformance

EXPECTED_STATUSES = {
    "bounded_implementation", "partial", "inherited_not_reassessed",
    "not_implemented", "implemented",
}

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


    def test_status_semantics_cannot_omit_add_or_relabel_an_implementation_state(self):
        self.assertEqual(set(self.data["status_semantics"]), EXPECTED_STATUSES)
        changed = copy.deepcopy(self.data)
        changed["status_semantics"].pop("implemented")
        with self.assertRaisesRegex(ValueError, "all five implementation states"):
            policy.validate(changed)
        changed = copy.deepcopy(self.data)
        changed["status_semantics"]["accepted_release"] = "Synthetic invalid status"
        with self.assertRaisesRegex(ValueError, "all five implementation states"):
            policy.validate(changed)
        changed = copy.deepcopy(self.data)
        changed["status_semantics"]["implemented"] = "Human acceptance and release are complete"
        with self.assertRaisesRegex(ValueError, "all five implementation states"):
            policy.validate(changed)


class CreativeStatusCliTests(unittest.TestCase):
    """Synthetic validator inputs only; no product completion or human session."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="creative-policy-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tooling").mkdir()
        for name in ("creative_status_policy.py", "conformance_policy.py"):
            (self.root / "tooling" / name).write_bytes((ROOT / "tooling" / name).read_bytes())
        self.data = conformance.load_json(ROOT / "docs/creative-production/requirements-status.json")
        descriptor = self.data["technical_evidence_index"]
        self.index = conformance.load_json(ROOT / descriptor["path"])
        self.catalog = copy.deepcopy({
            key: self.index[key] for key in (
                "contracts", "criteria", "criterion_aliases", "catalog_counts",
                "granularity", "normative_edges", "normative_edges_sha256",
            )
        })
        self.identities = [(row["id"], row["source_record_sha256"], row["acceptance_case_ids"])
                           for row in self.data["requirements"]]
        for key in ("original_requirements_ledger", "original_acceptance_ledger"):
            relative = self.data[key]
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            original = (ROOT / relative).read_bytes()
            target.write_bytes(original)
            self.assertEqual(target.read_bytes(), original)
        (self.root / "SOURCE_LOCK.json").write_bytes((ROOT / "SOURCE_LOCK.json").read_bytes())
        (self.root / "fixture-product.py").write_bytes(b"synthetic_validator_input = 4\n")
        (self.root / "fixture-oracle.py").write_bytes(b"# Validator fixture, no product execution.\nassert 2 + 2 == 4\n")
        (self.root / "fixture-result.json").write_bytes(b'{"synthetic_validator_fixture":true,"observed":4}\n')
        self.source = {
            "motionwright_sha": "1" * 40, "digest_kind": "git-text-lf",
            "source_lock_sha256": conformance.source_digest(self.root / "SOURCE_LOCK.json"),
        }
        self.index["source"] = copy.deepcopy(self.source)
        self.index["records"] = []
        self.index["human_reviews"] = []
        self.index["sufficiency_reviews"] = []
        self.index["verification_profiles"] = {}
        self.index["execution_profiles"]["validator-unit-fixture"] = {"kind": "unit"}
        self.index["synthetic_validator_fixture"] = True
        self.data["current_assessment_source"] = copy.deepcopy(self.source)
        for row in self.data["requirements"]:
            row.update(implementation="not_implemented", complete_requirement_acceptance="NOT_COMPLETED",
                       implemented_scope="Synthetic validator fixture; no product implementation is claimed.",
                       remaining_scope="Synthetic validator observation remains pending.", source_paths=[])
            assessment = row["technical_assessment"]
            assessment["source"] = copy.deepcopy(self.source)
            assessment["implementation"]["status"] = "absent"
            assessment["integration"].update(status="main", source_sha=self.source["motionwright_sha"])
            assessment["technical_verification"].update(status="not_run", execution_kinds=[], evidence_ids=[])
            assessment["review"].update(method="automated", reviewer_kind="automated_technical")
            assessment["review"].pop("human_review_id", None)
            contract = self.index["contracts"][row["id"]]
            for criterion in assessment["criteria"]:
                criterion.update(status="not_run", evidence_ids=[],
                                 missing_assertions=["Synthetic validator observation remains pending."])
            assessment["contract_acceptance"].update(
                status="open", pending_criteria=[item["id"] for item in assessment["criteria"]],
                human_review_required=contract["human_review_required"],
                human_review_status="not_run" if contract["human_review_required"] else "not_required",
            )
        self.target = next(row for row in self.data["requirements"] if row["id"] == "MW05-E01-04")
        contract = self.index["contracts"][self.target["id"]]
        self.assertIs(contract["human_review_required"], False)
        self.assertEqual(contract["human_criterion_ids"], [])
        assessment = self.target["technical_assessment"]
        identities = [item["id"] for item in assessment["criteria"]]
        record = {
            "id": "synthetic-validator-execution", "source": copy.deepcopy(self.source),
            "criterion_ids": identities, "complete_criterion_ids": identities,
            "source_inputs": [{"path": "fixture-product.py",
                               "sha256": conformance.source_digest(self.root / "fixture-product.py")}],
            "definitions": [{"path": "fixture-oracle.py",
                             "sha256": conformance.source_digest(self.root / "fixture-oracle.py")}],
            "artifacts": [{"path": "fixture-result.json",
                           "sha256": hashlib.sha256((self.root / "fixture-result.json").read_bytes()).hexdigest()}],
            "execution": {"kind": "unit", "profile": "validator-unit-fixture", "status": "pass",
                          "passed": 1, "failed": 0, "skipped": 0},
            "assertions": ["Synthetic policy fixture only; no actual product result or human approval."],
        }
        self.index["records"] = [record]
        for number, identity in enumerate(identities):
            profile = {"obligations": {"synthetic-validator-observation": {
                "execution_profiles": ["validator-unit-fixture"]}}}
            self.index["verification_profiles"][identity] = profile
            self.index["sufficiency_reviews"].append({
                "id": "synthetic-validator-sufficiency-" + str(number), "schema_version": 1,
                "source": copy.deepcopy(self.source), "criterion_id": identity,
                "source_clause_sha256": self.index["criteria"][identity],
                "oracle_profile_sha256": conformance.canonical_digest(profile),
                "evidence_record_sha256": {record["id"]: conformance.canonical_digest(record)},
                "reviewer": {"kind": "automated_technical", "reference": "synthetic-validator-fixture",
                             "identity_authentication": "not_asserted"},
                "reviewed_at_utc": "2026-10-17T00:00:00+00:00",
                "obligations": [{"id": "synthetic-validator-observation", "status": "pass",
                                 "evidence_ids": [record["id"]],
                                 "basis": "Synthetic CLI validator fixture, not a product acceptance result."}],
                "decision": "sufficient", "limitations": ["Validator fixture only."],
            })
        self.target.update(implementation="implemented", complete_requirement_acceptance="COMPLETED",
                           implemented_scope="Complete technical evidence in a synthetic validator fixture.",
                           remaining_scope="", source_paths=["fixture-product.py"])
        assessment["implementation"]["status"] = "implemented"
        assessment["technical_verification"].update(status="pass", execution_kinds=["unit"],
                                                   evidence_ids=[record["id"]])
        for criterion in assessment["criteria"]:
            criterion.update(status="pass", evidence_ids=[record["id"]], missing_assertions=[])
        assessment["contract_acceptance"].update(
            status="accepted", pending_criteria=[], human_review_required=False,
            human_review_status="not_required",
        )
        self.assertEqual({key: self.index[key] for key in self.catalog}, self.catalog)
        self.assertEqual([(row["id"], row["source_record_sha256"], row["acceptance_case_ids"])
                          for row in self.data["requirements"]], self.identities)

    def write_fixture(self):
        index_path = self.root / self.data["technical_evidence_index"]["path"]
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_bytes((json.dumps(self.index, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        self.data["technical_evidence_index"]["sha256"] = conformance.source_digest(index_path)
        ledger = self.root / "docs/creative-production/requirements-status.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_bytes((json.dumps(self.data, sort_keys=True, indent=2) + "\n").encode("utf-8"))

    def run_cli(self):
        self.write_fixture()
        return subprocess.run(
            [sys.executable, str(self.root / "tooling/creative_status_policy.py")],
            cwd=self.root, capture_output=True, encoding="utf-8", timeout=30, check=False,
        )

    def test_objective_complete_fixture_cli_counts_all_64_without_granting_release(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        prefix = "creative-status=valid "
        self.assertTrue(result.stdout.startswith(prefix), result.stdout)
        counts_text, accepted = result.stdout[len(prefix):].strip().split(" release=false contractually_accepted=")
        counts = json.loads(counts_text)
        self.assertEqual(set(counts), EXPECTED_STATUSES)
        self.assertEqual(sum(counts.values()), 64)
        self.assertEqual(counts["implemented"], 1)
        self.assertEqual(counts["not_implemented"], 63)
        self.assertEqual(accepted, "1")
        self.assertFalse(self.data["v05_release_ready"])
        self.assertIs(self.target["technical_assessment"]["contract_acceptance"]["human_review_required"], False)
        self.assertEqual(self.index["human_reviews"], [])

    def test_cli_refuses_completion_without_its_sufficiency_review(self):
        self.index["sufficiency_reviews"] = []
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("separate sufficiency review", result.stderr)
        self.assertNotIn("creative-status=valid", result.stdout)

    def test_cli_refuses_completion_with_a_pending_criterion(self):
        assessment = self.target["technical_assessment"]
        pending = assessment["criteria"][-1]
        pending.update(status="not_run", missing_assertions=["Synthetic pending criterion"])
        assessment["technical_verification"]["status"] = "partial"
        assessment["contract_acceptance"]["pending_criteria"] = [pending["id"]]
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("complete technical evidence", result.stderr)
        self.assertNotIn("creative-status=valid", result.stdout)

    def test_cli_refuses_release_true_even_with_complete_objective_evidence(self):
        self.data["v05_release_ready"] = True
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot declare a v0.5 release", result.stderr)
        self.assertNotIn("creative-status=valid", result.stdout)

if __name__=="__main__":unittest.main()
