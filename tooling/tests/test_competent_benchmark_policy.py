"""Adversarial comparisons using entirely synthetic, non-product fixtures.

No benchmark suite executing these tests may claim measured creative quality
or source/runner authority from the synthetic controls.
"""
from __future__ import annotations
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import competent_benchmark_policy as gate  # noqa: E402


class CompetentBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "schema": gate.CORPUS_SCHEMA,
            "operation": "APPEND_ONLY",
            "append_contract": {"materialization_status": "PREPARED_NOT_WRITTEN"},
            "scope": {"requirement_id": "MW05-E09-04",
                      "acceptance_id": "A05-E09-04"},
            "catalogue_source": {"original_fixture_count": 12,
                                 "sha256": "a" * 64},
            "shared_protocol": {
                "ten_changes": [
                    {"id":key, "original_text": "A substantive revision exercise."}
                    for key in gate.CHANGES
                ],
                "application": {"budget_status": "DEFINE_AND_FREEZE_BEFORE_COMPARISON"},
            },
            "legacy_manifests": [{}, {}, {}],
            "manifests": [
                {"manifest_id": "MW05-E09-04:" + id,
                 "source_sha256": "a" * 64,
                 "original_record": {"id": id, "family": {
                     "P": "PRODUCT_FILM", "D": "REAL_APP_DEMO",
                     "T": "TECHNICAL_EXPLAINER"}[id[0]],
                     "brief": "Render an editable, authorized original."},
                 "results": None,
                 "execution_status": "NOT_RUN"}
                for id in gate.IDS
            ],
        }
        self.pretrusted = gate.digest(gate.canon(self.original))
        self.temp = tempfile.TemporaryDirectory(prefix="mw-comparison-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original_file = self.root / "real-synthetic-artifact.bin"
        self.original_file.write_bytes(b"synthetic-original-stub-not-product-evidence\n")
        self.artifact = {"path": self.original_file.name,
                         "bytes": self.original_file.stat().st_size,
                         "sha256": gate.digest(self.original_file.read_bytes())}

    def submitted(self):
        corpus = gate.source_corpus(self.original)
        tasks = []
        for code in gate.IDS:
            runs = []
            for j, role in enumerate(gate.ROLES):
                attempts = [{
                    "id": role + ":run1",
                    "started_at_utc": "2026-10-22T01:00:00+00:00",
                    "finished_at_utc": "2026-10-22T01:01:00+00:00",
                    "status": "pass",
                    "stdout": copy.deepcopy(self.artifact),
                    "stderr": copy.deepcopy(self.artifact),
                    "terminal": copy.deepcopy(self.artifact),
                    "editable_source": copy.deepcopy(self.artifact),
                    "master": copy.deepcopy(self.artifact),
                    "revisions": [
                        {"id": key, "status": "pass",
                         "receipt": copy.deepcopy(self.artifact)}
                        for key in gate.CHANGES
                    ],
                    "metrics": {
                        "wall_seconds": 60,
                        "known_cost_usd": 0,
                        "human_active_seconds": 10,
                        "changes_rebuilt": 0,
                        "changes_lost": 0,
                    },
                }]
                runs.append({
                    "role": role,
                    "engine": "same-native-engine" if j < 2 else "capable-alternative",
                    "engine_version": "engine-1",
                    "model_configuration": "documented-fixture",
                    "operator_experience": "competent-operator-not-authenticated",
                    "instructions_original_sha256": self.artifact["sha256"],
                    "instructions_artifact": copy.deepcopy(self.artifact),
                    "configuration_artifact": copy.deepcopy(self.artifact),
                    "available_features": ["edit", "render", "save-source"],
                    "attempts": attempts,
                })
            tasks.append({
                "id": code,
                "original_record_sha256": corpus["tasks"][code]["original_sha256"],
                "change_ids": list(gate.CHANGES),
                "generative": False,
                "necessary_features": ["edit", "render"],
                "budget": {
                    "max_wall_seconds": 2400,
                    "max_known_cost_usd": 10,
                    "max_human_active_seconds": 1500,
                },
                "source_rights": {
                    "status": "authorized",
                    "receipt": copy.deepcopy(self.artifact),
                },
                "input_assets": [copy.deepcopy(self.artifact)],
                "arms": runs,
            })
        return {
            "schema": gate.RESULT_SCHEMA,
            "original_manifest_sha256": self.pretrusted,
            "candidate_source_sha": "a" * 40,
            "source_lock_sha256": "c" * 64,
            "preregistered_at_utc": "2026-10-20T01:00:00+00:00",
            "tasks": tasks,
        }

    def examine(self, submitted=None):
        return gate.examine(
            self.original, self.pretrusted, submitted, self.root)

    def fail(self, proposal, matches):
        with self.assertRaisesRegex(gate.ComparisonError, matches):
            self.examine(proposal)

    def test_original_twelve_three_families_and_ten_revisions_not_executed(self):
        result = self.examine()
        self.assertEqual(result["total_original_tasks"], 12)
        self.assertEqual(set(result["original_task_families"].values()), {4})
        self.assertEqual(result["revisions_per_task"], 10)
        self.assertEqual(result["actual_arms_observed"], 0)
        self.assertEqual(result["status"], "BLOCKED_ORIGINAL_TWELVE_NOT_EXECUTED")
        self.assertEqual(len(result["task_execution"]), 12)
        self.assertFalse(result["release_authorized"])

    def test_complete_synthetic_data_is_never_formal_or_human_acceptance(self):
        reported = self.examine(self.submitted())
        self.assertEqual(
            reported["status"],
            "STRUCTURALLY_COMPLETE_FOR_INDEPENDENT_COMPARISON_REVIEW")
        self.assertEqual(reported["actual_arms_observed"], 36)
        self.assertEqual(reported["new_product_contract_PASS"], 0)
        self.assertFalse(reported["user_creative_quality_independently_reviewed"])
        self.assertFalse(reported["comparator_competence_independently_reviewed"])
        self.assertFalse(reported["release_authorized"])
        self.assertEqual(reported["source_run_execution_authentication"],
                         "NOT_PERFORMED")

    def test_missing_a_frozen_task_does_not_disappear_from_denominator(self):
        proposal = self.submitted()
        proposal["tasks"].pop()
        self.fail(proposal, "all twelve original tasks")

    def test_reorder_frozen_tasks_is_rejected(self):
        proposal = self.submitted()
        proposal["tasks"][0], proposal["tasks"][1] = (
            proposal["tasks"][1], proposal["tasks"][0])
        self.fail(proposal, "task order or ID differs")

    def test_alias_to_new_benchmark_corpus_is_denied(self):
        proposal = self.submitted()
        proposal["original_manifest_sha256"] = "f" * 64
        self.fail(proposal, "substitute a different original")

    def test_original_change_omission_denied(self):
        proposal = self.submitted()
        proposal["tasks"][0]["change_ids"].pop()
        self.fail(proposal, "all ten original changes")

    def test_same_native_engine_baseline_mandatory(self):
        proposal = self.submitted()
        proposal["tasks"][0]["arms"][1]["engine_version"] = "different-native"
        self.fail(proposal, "share the exact native engine/version")

    def test_competitor_missing_a_required_feature_not_fair(self):
        proposal = self.submitted()
        proposal["tasks"][0]["arms"][2]["available_features"] = ["edit"]
        self.fail(proposal, "incapable comparator")

    def test_a_deliberately_weak_baseline_is_not_equivalent(self):
        proposal = self.submitted()
        proposal["tasks"][0]["arms"][0]["available_features"] = ["render"]
        self.fail(proposal, "incapable comparator")

    def test_missing_credentialed_input_rights_rejected(self):
        proposal = self.submitted()
        proposal["tasks"][0]["source_rights"]["status"] = "assumed"
        self.fail(proposal, "rights not positively admitted")

    def test_physical_original_artifact_tampering_detected(self):
        proposal = self.submitted()
        self.original_file.write_bytes(b"tampered source material\n")
        self.fail(proposal, "original artifact (type/bytes mismatch|bytes changed)")

    def test_runner_failure_must_stay_in_comparison(self):
        proposal = self.submitted()
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        attempt["status"] = "fail"
        attempt.pop("editable_source")
        attempt.pop("master")
        attempt["failure_or_unknown_reason"] = "original executable returned nonzero"
        result = self.examine(proposal)
        self.assertEqual(result["task_execution"][0]["attempts"][0]["status"],
                         "fail")
        self.assertEqual(result["actual_arms_observed"], 36)

    def test_unknown_run_cannot_be_promoted_without_causal_reason(self):
        proposal = self.submitted()
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        attempt["status"] = "unknown"
        self.fail(proposal, "lacks original causal limitation")

    def test_over_budget_pass_cannot_be_hidden(self):
        proposal = self.submitted()
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        attempt["metrics"]["wall_seconds"] = 2500
        self.fail(proposal, "over-budget attempt cannot")

    def test_over_budget_failure_is_retained_as_unfavorable_result(self):
        proposal = self.submitted()
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        attempt["metrics"]["wall_seconds"] = 3000
        attempt["status"] = "fail"
        attempt["failure_or_unknown_reason"] = "budget exhaustion"
        result = self.examine(proposal)
        self.assertTrue(result["task_execution"][0]["attempts"][0]["over_budget"])
        self.assertFalse(result["release_authorized"])

    def test_future_budget_preregistration_not_backdated(self):
        proposal = self.submitted()
        proposal["preregistered_at_utc"] = "2026-10-25T01:00:00+00:00"
        self.fail(proposal, "not frozen before execution")

    def test_missing_iteration_original_stream_cannot_pass(self):
        proposal = self.submitted()
        proposal["tasks"][0]["arms"][2]["attempts"][0]["revisions"].pop()
        self.fail(proposal, "all ten original revisions")

    def test_duplicate_attempt_identity_refused(self):
        proposal = self.submitted()
        dup = copy.deepcopy(proposal["tasks"][0]["arms"][0]["attempts"][0])
        proposal["tasks"][0]["arms"][0]["attempts"].append(dup)
        self.fail(proposal, "duplicate ID")

    def test_generation_requiring_repetition_cannot_be_sample_of_one(self):
        proposal = self.submitted()
        proposal["tasks"][0]["generative"] = True
        self.fail(proposal, "needs multiple original runs")

    def test_multiple_original_generations_with_dispersion_still_not_acceptance(self):
        proposal = self.submitted()
        proposal["tasks"][0]["generative"] = True
        for arm in proposal["tasks"][0]["arms"]:
            repeat = copy.deepcopy(arm["attempts"][0])
            repeat["id"] = arm["role"] + ":run2"
            repeat["metrics"]["wall_seconds"] = 72
            arm["attempts"].append(repeat)
        result = self.examine(proposal)
        self.assertEqual(result["actual_arms_observed"], 36)
        self.assertEqual(result["actual_attempts_observed"], 39)
        self.assertEqual(result["new_product_contract_PASS"], 0)

    def test_no_implicit_rebase_of_original_task_to_new_brief(self):
        proposal = self.submitted()
        proposal["tasks"][0]["original_record_sha256"] = "0" * 64
        self.fail(proposal, "task source brief changed")

    def test_duplicate_original_fixture_rejected(self):
        self.original["manifests"][0] = copy.deepcopy(
            self.original["manifests"][1])
        with self.assertRaises(gate.ComparisonError):
            self.examine()

    def test_source_rights_issuer_and_human_not_authenticated(self):
        result = self.examine(self.submitted())
        self.assertEqual(result["source_rights_issuer_authentication"],
                         "NOT_PERFORMED")
        self.assertNotIn("product_acceptance_READY", result)


    def test_original_comparator_prompt_is_physically_required(self):
        proposal = self.submitted()
        proposal["tasks"][0]["arms"][0].pop("instructions_artifact")
        self.fail(proposal, "original artifact path/bytes/hash identity required")

    def test_comparator_prompt_digest_must_match_retained_bytes(self):
        proposal = self.submitted()
        proposal["tasks"][0]["arms"][0]["instructions_original_sha256"] = "a" * 64
        self.fail(proposal, "not the actual retained prompt")

    def test_configuration_bytes_must_be_present_not_just_version_label(self):
        proposal = self.submitted()
        proposal["tasks"][0]["arms"][2].pop("configuration_artifact")
        self.fail(proposal, "original artifact path/bytes/hash identity required")

    def test_original_run_failure_can_preserve_nine_unattempted_revisions(self):
        proposal = self.submitted()
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        attempt["status"] = "fail"
        attempt["failure_or_unknown_reason"] = "actual renderer failed before revision2"
        for revision in attempt["revisions"][1:]:
            revision["status"] = "not_run"
            revision["reason"] = "earlier effect failed; no automatic retry"
            revision.pop("receipt")
        result = self.examine(proposal)
        self.assertEqual(result["unattempted_original_revisions"], 9)
        self.assertEqual(result["actual_arms_observed"], 36)
        self.assertEqual(result["status"],
                         "STRUCTURAL_DATA_WITH_INCOMPLETE_REVISIONS_OR_UNKNOWN_COST")
        self.assertFalse(result["release_authorized"])

    def test_unattempted_revision_cannot_have_fictional_receipt(self):
        proposal = self.submitted()
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        attempt["status"] = "fail"
        attempt["failure_or_unknown_reason"] = "partial"
        attempt["revisions"][-1]["status"] = "not_run"
        attempt["revisions"][-1]["reason"] = "prior error"
        self.fail(proposal, "no fictional receipt")

    def test_success_cannot_hide_failed_ninth_revision(self):
        proposal = self.submitted()
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        attempt["revisions"][-2]["status"] = "fail"
        self.fail(proposal, "cannot hide failed or missing revisions")

    def test_unknown_cost_is_null_not_automatically_zero(self):
        proposal = self.submitted()
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        attempt["metrics"]["known_cost_usd"] = None
        result = self.examine(proposal)
        self.assertEqual(result["unknown_cost_attempts"], 1)
        self.assertIsNone(result["task_execution"][0]["attempts"][0]["cost_usd"])
        self.assertEqual(result["status"],
                         "STRUCTURAL_DATA_WITH_INCOMPLETE_REVISIONS_OR_UNKNOWN_COST")

    def test_original_budget_cannot_be_nan_or_infinite(self):
        proposal = self.submitted()
        proposal["tasks"][0]["budget"]["max_known_cost_usd"] = float("inf")
        self.fail(proposal, "must be finite positive")


    def empty_original(self):
        path = self.root / "present-empty-original.log"
        path.write_bytes(b"")
        return {"path": path.name, "bytes": 0, "sha256": gate.digest(b"")}

    def test_present_empty_stdout_or_stderr_remains_valid_original(self):
        for channel in ("stdout", "stderr"):
            with self.subTest(channel=channel):
                proposal = self.submitted()
                proposal["tasks"][0]["arms"][0]["attempts"][0][channel] = self.empty_original()
                result = self.examine(proposal)
                self.assertEqual(result["actual_arms_observed"], 36)
                self.assertEqual(result["actual_original_streams_checked"], 72)

    def test_present_empty_both_streams_are_checked_without_acceptance(self):
        proposal = self.submitted()
        empty = self.empty_original()
        proposal["tasks"][0]["arms"][0]["attempts"][0].update(stdout=empty, stderr=empty)
        result = self.examine(proposal)
        self.assertEqual(result["actual_attempts_observed"], 36)
        self.assertFalse(result["release_authorized"])
        self.assertEqual(result["new_product_contract_PASS"], 0)

    def test_absent_empty_original_stream_is_not_validated(self):
        proposal = self.submitted()
        empty = self.empty_original()
        (self.root / empty["path"]).unlink()
        proposal["tasks"][0]["arms"][0]["attempts"][0]["stdout"] = empty
        self.fail(proposal, "unavailable original benchmark artifact")

    def test_empty_original_stream_requires_actual_empty_digest(self):
        proposal = self.submitted()
        empty = self.empty_original()
        empty["sha256"] = "0" * 64
        proposal["tasks"][0]["arms"][0]["attempts"][0]["stderr"] = empty
        self.fail(proposal, "physical benchmark original artifact bytes changed")

    def test_nonstream_artifacts_still_require_nonempty_original_bytes(self):
        for field in ("terminal", "editable_source", "master"):
            with self.subTest(field=field):
                proposal = self.submitted()
                proposal["tasks"][0]["arms"][0]["attempts"][0][field] = self.empty_original()
                self.fail(proposal, "original artifact type/bytes mismatch")
        proposal = self.submitted()
        proposal["tasks"][0]["source_rights"]["receipt"] = self.empty_original()
        self.fail(proposal, "original artifact type/bytes mismatch")

    def test_distinct_arm_attempt_and_reference_counts_are_separate(self):
        proposal = self.submitted()
        proposal["tasks"][0]["generative"] = True
        for arm in proposal["tasks"][0]["arms"]:
            extra = copy.deepcopy(arm["attempts"][0])
            extra["id"] += ":repeat"
            arm["attempts"].append(extra)
        result = self.examine(proposal)
        self.assertEqual(result["actual_arms_observed"], 36)
        self.assertEqual(result["actual_attempts_observed"], 39)
        self.assertEqual(result["actual_original_streams_checked"], 78)
        # 24 input/rights + 72 instruction/config + 39 * 15 attempt references.
        self.assertEqual(result["actual_artifact_references_checked"], 681)
        self.assertIn("not unique files", result["artifact_reference_count_semantics"])

    def test_artifact_reference_count_includes_inputs_and_rights(self):
        result = self.examine(self.submitted())
        self.assertEqual(result["actual_arms_observed"], 36)
        self.assertEqual(result["actual_attempts_observed"], 36)
        self.assertEqual(result["actual_original_streams_checked"], 72)
        self.assertEqual(result["actual_artifact_references_checked"], 636)

    def test_source_lock_requires_lowercase_sha256_hex(self):
        for value in ("G" * 64, "g" * 64, "A" * 64, "0" * 63, " " * 64):
            with self.subTest(value=value):
                proposal = self.submitted()
                proposal["source_lock_sha256"] = value
                self.fail(proposal, "actual product source lock pin missing")

    def test_every_attempt_container_is_checked_before_field_access(self):
        for value in (None, [], "private/path", 0, True):
            with self.subTest(value=value):
                proposal = self.submitted()
                proposal["tasks"][0]["arms"][0]["attempts"] = [value]
                self.fail(proposal, "actual attempt must be an object")

    def test_malformed_attempt_cli_is_sanitized_and_writes_no_result(self):
        proposal = self.submitted()
        proposal["tasks"][0]["arms"][0]["attempts"] = [None]
        original = self.root / "private-original.json"
        submitted = self.root / "private-submission.json"
        result = self.root / "must-not-be-created.json"
        original.write_bytes(gate.canon(self.original))
        submitted.write_bytes(gate.canon(proposal))
        stderr, stdout = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            code = gate.main(["--original", str(original),
                              "--original-sha256", self.pretrusted,
                              "--submitted", str(submitted),
                              "--evidence-root", str(self.root), "--output", str(result)])
        self.assertEqual(code, 2)
        self.assertIn("code=ComparisonError", stderr.getvalue())
        self.assertNotIn(str(self.root), stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertFalse(result.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
