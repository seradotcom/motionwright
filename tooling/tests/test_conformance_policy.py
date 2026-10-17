import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import conformance_policy as policy


def digest(value):
    return hashlib.sha256(value).hexdigest()


class ConformancePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "SOURCE_LOCK.json").write_bytes(b'{"pin":"synthetic"}\n')
        (self.root / "oracle.py").write_bytes(b"assert 2 + 2 == 4\n")
        (self.root / "product.py").write_bytes(b"value = 4\n")
        (self.root / "result.json").write_bytes(b'{"observed":4}\n')
        self.source = {"motionwright_sha": "1" * 40, "digest_kind": "git-text-lf",
                       "source_lock_sha256": digest((self.root / "SOURCE_LOCK.json").read_bytes())}
        self.ids = ["CR-FOUND-01#/statement", "CR-FOUND-01#/acceptance_criterion"]
        self.index = {
            "schema": "motionwright.technical-evidence/1",
            "source": self.source,
            "criteria": {identity: digest(identity.encode()) for identity in self.ids},
            "contracts": {"CR-FOUND-01": {"source_record_sha256": "a" * 64, "scope": "required",
                                         "human_review_required": False, "human_criterion_ids": [],
                                         "not_applicable_reason_codes": []}},
            "human_reviews": [],
            "execution_profiles": {"synthetic-math-oracle": {"kind": "unit"}},
            "records": [{
                "id": "source-execution",
                "source": self.source,
                "criterion_ids": self.ids,
                "complete_criterion_ids": self.ids,
                "source_inputs": [{"path": "product.py", "sha256": digest((self.root / "product.py").read_bytes())}],
                "assertions": ["Synthetic oracle executed and returned the expected value"],
                "execution": {"kind": "unit", "profile": "synthetic-math-oracle", "status": "pass", "passed": 1, "failed": 0, "skipped": 0},
                "definitions": [{"path": "oracle.py", "sha256": digest((self.root / "oracle.py").read_bytes())}],
                "artifacts": [{"path": "result.json", "sha256": digest((self.root / "result.json").read_bytes())}],
            }],
        }
        self.populate_sufficiency()
        self.assessment = {
            "schema_version": 1,
            "source": self.source,
            "implementation": {"status": "implemented"},
            "integration": {"status": "main", "source_sha": self.source["motionwright_sha"]},
            "technical_verification": {"status": "pass", "execution_kinds": ["unit"],
                                       "evidence_ids": ["source-execution"]},
            "criteria": [{"id": identity, "source_clause_sha256": self.index["criteria"][identity],
                          "status": "pass", "evidence_ids": ["source-execution"], "missing_assertions": []}
                         for identity in self.ids],
            "review": {"method": "automated", "reviewer_kind": "automated_technical"},
            "contract_acceptance": {"status": "open", "pending_criteria": [],
                                    "human_review_required": False, "human_review_status": "not_required"},
        }

    def populate_sufficiency(self):
        self.index["verification_profiles"] = {
            identity: {"obligations": {"observed-result": {"execution_profiles": [self.index["records"][0]["execution"]["profile"]]}}}
            for identity in self.ids
        }
        self.index["sufficiency_reviews"] = [{
            "id": "fixture-review-" + str(number),
            "schema_version": 1, "source": self.source, "criterion_id": identity,
            "source_clause_sha256": self.index["criteria"][identity],
            "oracle_profile_sha256": policy.canonical_digest(self.index["verification_profiles"][identity]),
            "evidence_record_sha256": {"source-execution": policy.canonical_digest(self.index["records"][0])},
            "reviewer": {"kind": "automated_technical", "reference": "synthetic-validator-fixture",
                         "identity_authentication": "not_asserted"},
            "reviewed_at_utc": "2026-10-17T00:00:00+00:00",
            "obligations": [{"id": "observed-result", "status": "pass", "evidence_ids": ["source-execution"],
                             "basis": "Synthetic fixture: retained value and exact oracle definition."}],
            "decision": "sufficient", "limitations": ["Validator fixture, not an actual product review."],
        } for number, identity in enumerate(self.ids)]

    def load_index(self):
        path = self.root / "index.json"
        path.write_text(json.dumps(self.index), encoding="utf-8")
        return policy.load_evidence_index(self.root, {"path": "index.json", "sha256": digest(path.read_bytes()), "digest_kind": "git-text-lf"})

    def validate(self):
        index, records = self.load_index()
        policy.validate_assessment(self.assessment, "CR-FOUND-01", index, records)

    def test_complete_technical_review_does_not_require_human_or_grant_acceptance(self):
        self.validate()
        self.assertEqual(self.assessment["contract_acceptance"]["status"], "open")
        self.assessment["contract_acceptance"]["status"] = "accepted"
        self.validate()

    def test_missing_negative_criterion_cannot_promote_remaining_positive_criterion(self):
        self.assessment["criteria"].pop()
        with self.assertRaisesRegex(ValueError, "dropped"):
            self.validate()

    def test_criterion_text_identity_cannot_be_rewritten(self):
        self.assessment["criteria"][0]["source_clause_sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.validate()

    def test_matching_filename_does_not_cover_another_criterion(self):
        self.index["records"][0]["criterion_ids"] = [self.ids[0]]
        self.index["records"][0]["complete_criterion_ids"] = [self.ids[0]]
        self.populate_sufficiency()
        self.index["sufficiency_reviews"] = self.index["sufficiency_reviews"][:1]
        with self.assertRaisesRegex(ValueError, "does not cover"):
            self.validate()

    def test_failed_skipped_and_zero_assertion_records_cannot_be_pass(self):
        for changed in ({"failed": 1}, {"skipped": 1}, {"passed": 0}, {"passed": True}):
            with self.subTest(changed=changed):
                original = copy.deepcopy(self.index)
                self.index["records"][0]["execution"].update(changed)
                with self.assertRaises(ValueError):
                    self.validate()
                self.index = original

    def test_old_source_cannot_be_relabelled_as_new_execution(self):
        self.index["records"][0]["source"] = {**self.source, "motionwright_sha": "2" * 40}
        self.index["sufficiency_reviews"] = []
        with self.assertRaisesRegex(ValueError, "historical"):
            self.validate()

    def test_git_text_crlf_checkout_keeps_source_identity(self):
        for name in ("SOURCE_LOCK.json", "oracle.py", "product.py"):
            path = self.root / name
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.validate()

    def test_runtime_artifact_bytes_are_not_normalized_as_source(self):
        path = self.root / "result.json"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(ValueError, "artifact bytes"):
            self.validate()

    def test_artifact_mutation_is_detected(self):
        (self.root / "result.json").write_text('{"observed":5}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "artifact bytes"):
            self.validate()

    def test_oracle_and_lock_changes_require_reassessment(self):
        for filename in ("oracle.py", "SOURCE_LOCK.json"):
            with self.subTest(filename=filename):
                path = self.root / filename
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                with self.assertRaisesRegex(ValueError, "stale"):
                    self.validate()
                path.write_bytes(original)

    def test_branch_execution_does_not_close_main_implementation(self):
        self.assessment["integration"]["status"] = "branch"
        with self.assertRaisesRegex(ValueError, "integrated"):
            self.validate()

    def test_pending_assertions_prevent_contract_acceptance(self):
        self.assessment["criteria"][1].update(status="partial", missing_assertions=["Recovery remains unobserved"])
        self.assessment["technical_verification"]["status"] = "partial"
        self.assessment["contract_acceptance"].update(status="accepted", pending_criteria=[self.ids[1]])
        with self.assertRaisesRegex(ValueError, "complete technical"):
            self.validate()

    def test_omitting_pending_criteria_does_not_hide_a_blocker(self):
        self.assessment["criteria"][1].update(status="not_run", missing_assertions=["Recovery remains unobserved"],
                                              evidence_ids=[])
        self.assessment["technical_verification"]["status"] = "partial"
        with self.assertRaisesRegex(ValueError, "omits pending"):
            self.validate()

    def test_required_human_review_cannot_be_created_by_automation(self):
        self.index["contracts"]["CR-FOUND-01"].update(human_review_required=True, human_criterion_ids=self.ids)
        self.assessment["contract_acceptance"].update(status="accepted", human_review_required=True,
                                                     human_review_status="not_run")
        with self.assertRaisesRegex(ValueError, "actual human"):
            self.validate()
        self.assessment["contract_acceptance"]["human_review_status"] = "reviewed"
        with self.assertRaisesRegex(ValueError, "cannot certify human"):
            self.validate()

    def test_unknown_or_repeated_evidence_and_criteria_are_refused(self):
        self.assessment["criteria"].append(copy.deepcopy(self.assessment["criteria"][0]))
        with self.assertRaises(ValueError):
            self.validate()

    def test_unsafe_paths_and_directories_are_not_evidence(self):
        for pointer in ("../result.json", "C:/result.json", "C:result.json", "result.json:stream", "."):
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                policy.public_file(self.root, pointer)

    def test_duplicate_json_keys_do_not_override_previous_verdicts(self):
        path = self.root / "duplicate.json"
        path.write_text('{"status":"fail","status":"pass"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            policy.load_json(path)

    def test_stale_index_descriptor_is_detected_before_claims_are_read(self):
        path = self.root / "index.json"
        path.write_text(json.dumps(self.index), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "index bytes"):
            policy.load_evidence_index(self.root, {"path": "index.json", "sha256": "0" * 64, "digest_kind": "git-text-lf"})


    def test_execution_kind_cannot_be_promoted_or_omitted(self):
        for kinds in ([], ["native"], ["unit", "installed"]):
            with self.subTest(kinds=kinds):
                self.assessment["technical_verification"]["execution_kinds"] = kinds
                with self.assertRaisesRegex(ValueError, "execution kinds"):
                    self.validate()

    def test_canonical_human_obligation_cannot_be_waived(self):
        self.index["contracts"]["CR-FOUND-01"].update(human_review_required=True, human_criterion_ids=self.ids)
        self.assessment["contract_acceptance"]["status"] = "accepted"
        with self.assertRaisesRegex(ValueError, "canonical human"):
            self.validate()

    def test_pending_identifiers_must_belong_to_the_contract(self):
        self.assessment["contract_acceptance"]["pending_criteria"] = ["CR-OTHER-99#/statement"]
        with self.assertRaisesRegex(ValueError, "canonical row"):
            self.validate()

    def test_required_cannot_be_waived_but_unselected_optional_can(self):
        self.assessment["contract_acceptance"].update(status="not_applicable", reason="Profile not selected",
                                                     reason_code="optional_profile_not_selected")
        with self.assertRaisesRegex(ValueError, "eligible canonical"):
            self.validate()
        self.index["contracts"]["CR-FOUND-01"].update(scope="optional", not_applicable_reason_codes=["optional_profile_not_selected"])
        self.validate()

    def test_schema_boolean_is_not_a_version(self):
        self.assessment["schema_version"] = True
        with self.assertRaisesRegex(ValueError, "schema"):
            self.validate()

    def test_manual_method_cannot_hide_automated_reviewer(self):
        self.assessment["review"]["method"] = "manual"
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            self.validate()

    def test_product_change_invalidates_unchanged_oracle_execution(self):
        (self.root / "product.py").write_bytes(b"value = 5\n")
        with self.assertRaisesRegex(ValueError, "product source input"):
            self.validate()

    def test_remote_url_without_retained_bytes_is_not_execution_evidence(self):
        self.index["records"][0]["artifacts"] = [{"url": "https://github.com/seradotcom/motionwright/actions/runs/999999999999",
                                                 "sha256": "0" * 64}]
        with self.assertRaisesRegex(ValueError, "retained artifact bytes"):
            self.validate()

    def test_partial_evidence_cannot_be_promoted_to_complete_criterion(self):
        self.index["records"][0]["complete_criterion_ids"] = []
        self.index["sufficiency_reviews"] = []
        with self.assertRaisesRegex(ValueError, "complete criterion"):
            self.validate()
        for row in self.assessment["criteria"]:
            row.update(status="partial", missing_assertions=["Complete runtime remains unobserved"])
        self.assessment["technical_verification"]["status"] = "partial"
        self.assessment["contract_acceptance"]["pending_criteria"] = self.ids
        self.validate()

    def test_human_review_metadata_needs_retained_source_and_artifact_bound_record(self):
        # Synthetic validator fixture only; this test is not a human acceptance session.
        self.index["contracts"]["CR-FOUND-01"].update(human_review_required=True, human_criterion_ids=self.ids)
        self.assessment["review"].update(method="manual", reviewer_kind="human", human_review_id="fixture-review")
        self.assessment["contract_acceptance"].update(status="accepted", human_review_required=True, human_review_status="reviewed")
        with self.assertRaisesRegex(ValueError, "retained report"):
            self.validate()
        proof = self.root / "synthetic-review.json"
        proof.write_bytes(b'{"synthetic_validator_fixture":true}\n')
        self.index["human_reviews"] = [{
            "id": "fixture-review", "source": self.source, "status": "completed", "reviewer_kind": "human",
            "reviewer_reference_sha256": "b" * 64, "criterion_ids": self.ids,
            "observed_artifact_sha256": [self.index["records"][0]["artifacts"][0]["sha256"]],
            "record": {"path": proof.name, "sha256": digest(proof.read_bytes())},
        }]
        self.validate()
        self.index["human_reviews"][0]["source"] = {**self.source, "motionwright_sha": "2" * 40}
        with self.assertRaisesRegex(ValueError, "source-bound"):
            self.validate()


    def test_record_cannot_award_its_own_full_criterion_coverage(self):
        self.index["sufficiency_reviews"] = []
        with self.assertRaisesRegex(ValueError, "separate sufficiency"):
            self.validate()

    def test_native_admission_does_not_establish_different_native_surface(self):
        self.index["execution_profiles"].update({"native-input-decode": {"kind": "native"}, "native-receiver-relink": {"kind": "native"}})
        self.index["records"][0]["execution"].update(kind="native", profile="native-input-decode")
        self.assessment["technical_verification"]["execution_kinds"] = ["native"]
        self.populate_sufficiency()
        for profile in self.index["verification_profiles"].values():
            profile["obligations"]["observed-result"]["execution_profiles"] = ["native-receiver-relink"]
        for review in self.index["sufficiency_reviews"]:
            review["oracle_profile_sha256"] = policy.canonical_digest(self.index["verification_profiles"][review["criterion_id"]])
        with self.assertRaisesRegex(ValueError, "execution surface"):
            self.validate()

    def test_complete_record_mutation_invalidates_review(self):
        self.index["records"][0]["assertions"].append("An extra unreviewed assertion")
        with self.assertRaisesRegex(ValueError, "changed after sufficiency"):
            self.validate()

    def test_changed_verification_profile_requires_new_review(self):
        self.index["execution_profiles"]["installed-other-environment"] = {"kind": "installed"}
        self.index["verification_profiles"][self.ids[0]]["obligations"]["observed-result"]["execution_profiles"] = ["installed-other-environment"]
        with self.assertRaisesRegex(ValueError, "stale verification profile"):
            self.validate()

    def test_sufficiency_cannot_omit_or_pass_missing_obligations(self):
        self.index["sufficiency_reviews"][0]["obligations"] = []
        with self.assertRaisesRegex(ValueError, "dropped or added"):
            self.validate()
        self.populate_sufficiency()
        self.index["sufficiency_reviews"][0]["obligations"][0]["status"] = "missing"
        with self.assertRaisesRegex(ValueError, "missing obligations"):
            self.validate()

    def test_objective_native_evidence_and_technical_review_can_complete_without_human(self):
        self.index["execution_profiles"]["native-observed-result"] = {"kind": "native"}
        self.index["records"][0]["execution"].update(kind="native", profile="native-observed-result")
        self.assessment["technical_verification"]["execution_kinds"] = ["native"]
        self.populate_sufficiency()
        self.validate()
        self.assertFalse(self.assessment["contract_acceptance"]["human_review_required"])


    def test_native_profile_label_cannot_promote_unit_execution(self):
        self.index["execution_profiles"]["native-observed-result"] = {"kind": "native"}
        self.index["records"][0]["execution"]["profile"] = "native-observed-result"
        self.populate_sufficiency()
        with self.assertRaisesRegex(ValueError, "profile and kind"):
            self.validate()


    def test_changed_source_can_keep_historical_result_without_completing_current_criterion(self):
        original_record = copy.deepcopy(self.index["records"][0])
        original_reviews = copy.deepcopy(self.index["sufficiency_reviews"])
        (self.root / "product.py").write_bytes(b"value = 5\n")
        for criterion in self.assessment["criteria"]:
            criterion.update(status="stale", missing_assertions=["Changed input needs reassessment"])
        self.assessment["technical_verification"]["status"] = "stale"
        self.assessment["contract_acceptance"]["pending_criteria"] = self.ids
        index, records = self.load_index()
        self.assertIn("source-execution", index["_stale_evidence_records"])
        self.assertEqual(records["source-execution"], original_record)
        self.assertEqual(index["sufficiency_reviews"], original_reviews)
        self.assertEqual(set(index["_stale_sufficiency_reviews"]),
                         {review["id"] for review in original_reviews})
        policy.validate_assessment(self.assessment, "CR-FOUND-01", index, records)

    def test_duplicate_canonical_obligation_cannot_drop_one_surface(self):
        path = self.root / "duplicate-profile.json"
        path.write_text('{"obligations":{"output":{"execution_profiles":["input-decode"]},"output":{"execution_profiles":["receiver-relink"]}}}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            policy.load_json(path)


    def _synthetic_human_review_for(self, observed_hash):
        # Validator fixture only: no participant or product acceptance is created.
        self.index["contracts"]["CR-FOUND-01"].update(
            human_review_required=True, human_criterion_ids=self.ids)
        self.assessment["review"].update(
            method="manual", reviewer_kind="human", human_review_id="fixture-review")
        self.assessment["contract_acceptance"].update(
            status="accepted", human_review_required=True,
            human_review_status="reviewed")
        proof = self.root / "synthetic-review.json"
        proof.write_bytes(b'{"synthetic_validator_fixture":true}\n')
        self.index["human_reviews"] = [{
            "id": "fixture-review", "source": self.source,
            "status": "completed", "reviewer_kind": "human",
            "reviewer_reference_sha256": "b" * 64,
            "criterion_ids": self.ids,
            "observed_artifact_sha256": [observed_hash],
            "record": {"path": proof.name, "sha256": digest(proof.read_bytes())},
        }]

    def test_human_observation_cannot_use_only_a_historical_artifact(self):
        current = self.index["records"][0]
        self._synthetic_human_review_for(current["artifacts"][0]["sha256"])
        self.validate()
        historical = copy.deepcopy(current)
        historical["id"] = "historical-execution"
        historical["source"] = {**self.source, "motionwright_sha": "2" * 40}
        output = self.root / "historical-result.json"
        output.write_bytes(b'{"observed":3,"synthetic_validator_fixture":true}\n')
        historical["artifacts"] = [{"path": output.name, "sha256": digest(output.read_bytes())}]
        self.index["records"].append(historical)
        self.index["human_reviews"][0]["observed_artifact_sha256"] = [
            historical["artifacts"][0]["sha256"]]
        with self.assertRaises(ValueError):
            self.validate()

    def test_human_observed_artifacts_must_cover_every_reviewed_criterion(self):
        current = self.index["records"][0]
        self._synthetic_human_review_for(current["artifacts"][0]["sha256"])
        self.validate()
        narrow = copy.deepcopy(current)
        narrow["id"] = "statement-only-execution"
        narrow["criterion_ids"] = [self.ids[0]]
        narrow["complete_criterion_ids"] = [self.ids[0]]
        output = self.root / "statement-only-result.json"
        output.write_bytes(b'{"statement_only":true,"synthetic_validator_fixture":true}\n')
        narrow["artifacts"] = [{"path": output.name, "sha256": digest(output.read_bytes())}]
        self.index["records"].append(narrow)
        self.index["human_reviews"][0]["observed_artifact_sha256"] = [
            narrow["artifacts"][0]["sha256"]]
        with self.assertRaises(ValueError):
            self.validate()

    def test_current_artifact_can_share_its_hash_with_a_historical_producer(self):
        current = self.index["records"][0]
        historical = copy.deepcopy(current)
        historical["id"] = "historical-identical-output"
        historical["source"] = {**self.source, "motionwright_sha": "2" * 40}
        self.index["records"].insert(0, historical)
        self._synthetic_human_review_for(current["artifacts"][0]["sha256"])
        self.validate()
        self.index["records"].reverse()
        self.validate()

    def test_removed_input_or_oracle_preserves_history_but_cannot_complete(self):
        # Synthetic validator fixture only; no product execution or acceptance.
        original = copy.deepcopy(self.index["records"][0])
        original_reviews = copy.deepcopy(self.index["sufficiency_reviews"])
        for criterion in self.assessment["criteria"]:
            criterion.update(status="stale", missing_assertions=["Removed source needs reassessment"])
        self.assessment["technical_verification"]["status"] = "stale"
        self.assessment["contract_acceptance"]["pending_criteria"] = list(self.ids)
        for name in ("product.py", "oracle.py"):
            with self.subTest(removed=name):
                path = self.root / name
                previous_bytes = path.read_bytes()
                path.unlink()
                try:
                    index, records = self.load_index()
                    self.assertIn("source-execution", index["_stale_evidence_records"])
                    self.assertEqual(records["source-execution"], original)
                    self.assertEqual(index["sufficiency_reviews"], original_reviews)
                    self.assertEqual(set(index["_stale_sufficiency_reviews"]),
                                     {review["id"] for review in original_reviews})
                    policy.validate_assessment(self.assessment, "CR-FOUND-01", index, records)
                    promoted = copy.deepcopy(self.assessment)
                    for criterion in promoted["criteria"]:
                        criterion.update(status="pass", missing_assertions=[])
                    promoted["technical_verification"]["status"] = "pass"
                    promoted["contract_acceptance"]["pending_criteria"] = []
                    with self.assertRaisesRegex(ValueError, "stale|reassess"):
                        policy.validate_assessment(promoted, "CR-FOUND-01", index, records)
                finally:
                    path.write_bytes(previous_bytes)

    def test_human_observation_of_uncited_current_record_cannot_close_cited_criteria(self):
        current = self.index["records"][0]
        other = copy.deepcopy(current)
        other["id"] = "uncited-current-execution"
        output = self.root / "uncited-current.json"
        output.write_bytes(b'{"synthetic_validator_fixture":"uncited-current"}\n')
        other["artifacts"] = [{"path": output.name, "sha256": digest(output.read_bytes())}]
        self.index["records"].append(other)
        self._synthetic_human_review_for(other["artifacts"][0]["sha256"])
        with self.assertRaisesRegex(ValueError, "current cited evidence"):
            self.validate()

    def test_human_observation_can_cover_distinct_current_records_per_criterion(self):
        first = self.index["records"][0]
        second = copy.deepcopy(first)
        first.update(criterion_ids=[self.ids[0]], complete_criterion_ids=[self.ids[0]])
        second.update(id="second-current-execution", criterion_ids=[self.ids[1]],
                      complete_criterion_ids=[self.ids[1]])
        output = self.root / "second-current.json"
        output.write_bytes(b'{"synthetic_validator_fixture":"second-current"}\n')
        second["artifacts"] = [{"path": output.name, "sha256": digest(output.read_bytes())}]
        self.index["records"].append(second)
        self.populate_sufficiency()
        review = self.index["sufficiency_reviews"][1]
        review["evidence_record_sha256"] = {second["id"]: policy.canonical_digest(second)}
        review["obligations"][0]["evidence_ids"] = [second["id"]]
        self.assessment["criteria"][1]["evidence_ids"] = [second["id"]]
        self.assessment["technical_verification"]["evidence_ids"].append(second["id"])
        self._synthetic_human_review_for(first["artifacts"][0]["sha256"])
        self.index["human_reviews"][0]["observed_artifact_sha256"].append(second["artifacts"][0]["sha256"])
        self.validate()
        self.index["human_reviews"][0]["observed_artifact_sha256"].pop()
        with self.assertRaisesRegex(ValueError, "current cited evidence"):
            self.validate()

    def test_historical_sufficiency_review_keeps_its_original_source_and_records(self):
        original_records = copy.deepcopy(self.index["records"])
        original_reviews = copy.deepcopy(self.index["sufficiency_reviews"])
        current = {**self.source, "motionwright_sha": "2" * 40}
        self.index["source"] = current
        self.assessment["source"] = copy.deepcopy(current)
        self.assessment["integration"]["source_sha"] = current["motionwright_sha"]
        for criterion in self.assessment["criteria"]:
            criterion.update(status="stale", missing_assertions=["Historical execution needs reassessment"])
        self.assessment["technical_verification"]["status"] = "stale"
        self.assessment["contract_acceptance"]["pending_criteria"] = list(self.ids)
        index, records = self.load_index()
        self.assertEqual(index["records"], original_records)
        self.assertEqual(index["sufficiency_reviews"], original_reviews)
        self.assertEqual(set(index["_stale_sufficiency_reviews"]),
                         {review["id"] for review in original_reviews})
        policy.validate_assessment(self.assessment, "CR-FOUND-01", index, records)
        promoted = copy.deepcopy(self.assessment)
        for criterion in promoted["criteria"]:
            criterion.update(status="pass", missing_assertions=[])
        promoted["technical_verification"]["status"] = "pass"
        promoted["contract_acceptance"]["pending_criteria"] = []
        with self.assertRaisesRegex(ValueError, "historical"):
            policy.validate_assessment(promoted, "CR-FOUND-01", index, records)

    def test_missing_source_option_does_not_admit_unsafe_paths_or_existing_directories(self):
        self.assertIsNone(policy.public_file(self.root, "removed-parent/product.py", allow_missing=True))
        with self.assertRaisesRegex(ValueError, "missing repository evidence"):
            policy.public_file(self.root, "removed-parent/product.py")
        (self.root / "directory").mkdir()
        for pointer in ("directory", ".", "../missing.py", "C:/missing.py",
                        "C:missing.py", "missing.py:stream", "product.py/child"):
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                policy.public_file(self.root, pointer, allow_missing=True)

    def test_inspection_and_read_errors_are_not_reclassified_as_missing_source(self):
        original_lstat = Path.lstat
        original_read = Path.read_bytes
        for name in ("product.py", "oracle.py"):
            target = self.root / name
            for failure in (PermissionError("synthetic inspection denial"),
                            OSError("synthetic inspection failure")):
                def failed_lstat(path, *args, **kwargs):
                    if path == target:
                        raise failure
                    return original_lstat(path, *args, **kwargs)
                with self.subTest(name=name, failure=type(failure).__name__), \
                     patch.object(Path, "lstat", failed_lstat), \
                     self.assertRaisesRegex(ValueError, "could not be inspected"):
                    self.load_index()
            def failed_read(path, *args, **kwargs):
                if path == target:
                    raise PermissionError("synthetic read denial")
                return original_read(path, *args, **kwargs)
            with self.subTest(name=name, stage="read"), \
                 patch.object(Path, "read_bytes", failed_read), \
                 self.assertRaises(PermissionError):
                self.load_index()

    def test_missing_child_of_linked_or_reparse_ancestor_is_not_stale(self):
        # Deterministic classifier faults; this does not claim a physical NTFS test.
        ancestor = self.root / "linked-parent"
        ancestor.mkdir()
        original_link = Path.is_symlink
        original_lstat = Path.lstat
        def marked_link(path):
            return path == ancestor or original_link(path)
        with patch.object(Path, "is_symlink", marked_link), \
             self.assertRaisesRegex(ValueError, "linked evidence"):
            policy.public_file(self.root, "linked-parent/missing.py", allow_missing=True)
        class ReparseInfo:
            st_mode = 0o040755
            st_file_attributes = 0x400
        def marked_reparse(path, *args, **kwargs):
            return ReparseInfo() if path == ancestor else original_lstat(path, *args, **kwargs)
        with patch.object(Path, "lstat", marked_reparse), \
             self.assertRaisesRegex(ValueError, "linked evidence"):
            policy.public_file(self.root, "linked-parent/missing.py", allow_missing=True)

    def test_absent_retained_artifact_lock_and_index_remain_hard_failures(self):
        for name in ("result.json", "SOURCE_LOCK.json"):
            path = self.root / name
            data = path.read_bytes()
            path.unlink()
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.load_index()
            finally:
                path.write_bytes(data)
        self.load_index()
        path = self.root / "index.json"
        descriptor = {"path": path.name, "sha256": digest(path.read_bytes()), "digest_kind": "git-text-lf"}
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing repository evidence"):
            policy.load_evidence_index(self.root, descriptor)


if __name__ == "__main__":
    unittest.main()
