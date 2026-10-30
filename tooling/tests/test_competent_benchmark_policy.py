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

    def binding_fixture_json(self, name, value):
        """Explicit synthetic controls, never producer or human evidence."""
        path = self.root / name
        raw = gate.canon(value)
        path.write_bytes(raw)
        return {"path": name, "bytes": len(raw), "sha256": gate.digest(raw)}

    def bound_revision_fixture(self):
        proposal = self.submitted()
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        revision = attempt["revisions"][0]
        context = gate.revision_context(
            proposal, gate.IDS[0], gate.ROLES[0], attempt["id"], gate.CHANGES[0])
        operation = {"project_id": "00000000-0000-0000-0000-000000000001",
                     "generation": "00000000-0000-0000-0000-000000000002",
                     "revision": 7, "request_id": "synthetic-request",
                     "request_sha256": "7" * 64, "command": "synthetic.render"}
        receipt = {**operation, "id": "00000000-0000-0000-0000-000000000003",
                   "stage": "completed", "payload": {"synthetic_control_only": True},
                   "created_at": "2026-10-22T01:00:30+00:00"}
        revision["receipt"] = self.binding_fixture_json("synthetic-production.json", receipt)
        stamp = {k: operation[k] for k in ("project_id", "generation", "revision")}
        relation = {"schema": gate.REVISION_BINDING_SCHEMA, "context": context,
                    "operation": operation,
                    "production_receipt_sha256": revision["receipt"]["sha256"],
                    "status": "pass", "reason": None,
                    "editable_source": {"artifact": copy.deepcopy(self.artifact),
                                        "source_stamp": copy.deepcopy(stamp)},
                    "render": {"artifact": copy.deepcopy(self.artifact),
                               "source_stamp": copy.deepcopy(stamp),
                               "editable_source_sha256": self.artifact["sha256"]}}
        revision["binding"] = {
            "operation": copy.deepcopy(operation),
            "correspondence": self.binding_fixture_json("synthetic-correspondence.json", relation)}
        return proposal, revision, receipt, relation

    def lock_fixture(self, revision, relation):
        lock = {"id": "00000000-0000-0000-0000-000000000004",
                "resource": "synthetic-scene", "kind": "content",
                "note": "synthetic control; no real human lock asserted"}
        snap = {"schema": gate.LOCK_SNAPSHOT_SCHEMA, "context": relation["context"],
                "operation": relation["operation"], "lock": lock,
                "protected_values": {"text": "keep this original value"}}
        value = {"schema": gate.LOCK_OBSERVATION_SCHEMA, "context": relation["context"],
                 "operation": relation["operation"], "outcome": "preserved",
                 "reason": "synthetic scoped snapshot correspondence",
                 "lock": lock,
                 "before": self.binding_fixture_json("synthetic-before.json", snap),
                 "after": self.binding_fixture_json("synthetic-after.json", snap),
                 "rejection": None}
        revision["lock_observation"] = self.binding_fixture_json("synthetic-lock.json", value)
        return value, snap

    def review_fixture(self, proposal):
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        context = {k: proposal[k] for k in
                   ("original_manifest_sha256", "candidate_source_sha", "source_lock_sha256")}
        protocol = {"schema": gate.REVIEW_PROTOCOL_SCHEMA, "context": context,
                    "blind_required": True,
                    "rubric": [{"id": "declared-quality", "minimum": 0, "maximum": 5}],
                    "targets": [{"blind_id": "synthetic-blind-1", "task_id": gate.IDS[0],
                                 "arm_role": gate.ROLES[0], "attempt_id": attempt["id"],
                                 "artifact": copy.deepcopy(attempt["master"]),
                                 "operator_id": "synthetic-operator-not-authenticated"}]}
        pref = self.binding_fixture_json("synthetic-protocol.json", protocol)
        review = {"schema": gate.REVIEW_RECORD_SCHEMA, "context": context,
                  "protocol_sha256": pref["sha256"], "state": "completed",
                  "reviews": [{"reviewer_id": "synthetic-reviewer-not-authenticated",
                               "independent": True, "blind_order_attested": True,
                               "reviewed_at_utc": "2026-10-22T02:00:00+00:00",
                               "scores": {"synthetic-blind-1": {"declared-quality": 0}},
                               "limitations": ["synthetic unfavorable control; not actual review"]}],
                  "owner_decision": {"state": "pending", "receipt": None}}
        proposal["independent_review"] = {
            "protocol": pref, "record": self.binding_fixture_json("synthetic-review.json", review)}
        return protocol, review

    def test_n79_01_transplanted_correspondence_and_source_stamp_are_rejected(self):
        proposal, revision, receipt, relation = self.bound_revision_fixture()
        good = self.examine(proposal)["revision_reconciliation"][0]["editable_render"]
        self.assertEqual(good["state"], "BOUND_DECLARATIONS_AND_BYTES_CHECKED")
        # Same editable/render bytes deliberately remain valid for a no-op.
        self.assertEqual(good["pair"]["editable_source"], good["pair"]["render"])
        for field in ("context", "render"):
            with self.subTest(field=field):
                bad = copy.deepcopy(relation)
                if field == "context":
                    bad["context"]["change_id"] = gate.CHANGES[1]
                    error = "correspondence transplanted"
                else:
                    bad["render"]["source_stamp"]["revision"] += 1
                    error = "source stamp belongs to another revision"
                revision["binding"]["correspondence"] = self.binding_fixture_json(
                    "synthetic-correspondence.json", bad)
                self.fail(proposal, error)

    def test_n79_02_valid_receipt_cannot_establish_a_different_revision_pair(self):
        proposal, revision, receipt, relation = self.bound_revision_fixture()
        self.assertEqual(self.examine(proposal)["revision_reconciliation"][0]
                         ["editable_render"]["producer_authentication"], "NOT_PERFORMED")
        receipt["revision"] += 1
        revision["receipt"] = self.binding_fixture_json("synthetic-production.json", receipt)
        relation["production_receipt_sha256"] = revision["receipt"]["sha256"]
        revision["binding"]["correspondence"] = self.binding_fixture_json(
            "synthetic-correspondence.json", relation)
        self.fail(proposal, "another immutable request or source revision")

    def test_n79_03_zero_loss_cannot_promote_violation_refusal_or_unknown(self):
        proposal, revision, receipt, relation = self.bound_revision_fixture()
        observed, after = self.lock_fixture(revision, relation)
        first = self.examine(proposal)["revision_reconciliation"][0]["lock"]
        self.assertEqual(first["state"], "PRESERVED")
        after["protected_values"]["text"] = "changed protected original"
        observed["after"] = self.binding_fixture_json("synthetic-after.json", after)
        revision["lock_observation"] = self.binding_fixture_json("synthetic-lock.json", observed)
        self.fail(proposal, "contradicts retained protected fields")
        observed["outcome"] = "violation"
        revision["lock_observation"] = self.binding_fixture_json("synthetic-lock.json", observed)
        self.assertEqual(self.examine(proposal)["revision_reconciliation"][0]["lock"]["state"],
                         "VIOLATION")
        refusal = {"context": relation["context"], "operation": relation["operation"],
                   "lock": observed["lock"], "code": "SyntheticLocked",
                   "reason": "synthetic refusal, never actual execution"}
        observed.update(outcome="refused", after=None,
                        rejection=self.binding_fixture_json("synthetic-refusal.json", refusal))
        revision["lock_observation"] = self.binding_fixture_json("synthetic-lock.json", observed)
        self.assertEqual(self.examine(proposal)["revision_reconciliation"][0]["lock"]["state"],
                         "REFUSED")
        observed.update(outcome="unknown", lock=None, before=None, after=None, rejection=None)
        revision["lock_observation"] = self.binding_fixture_json("synthetic-lock.json", observed)
        result = self.examine(proposal)
        self.assertEqual(result["revision_reconciliation"][0]["lock"]["state"], "UNKNOWN")
        self.assertEqual(result["task_execution"][0]["attempts"][0]["changes_lost"], 0)
        self.assertFalse(result["release_authorized"])

    def test_n79_04_unattempted_revision_has_no_fictional_pair_or_lock(self):
        proposal = self.submitted()
        attempt = proposal["tasks"][0]["arms"][0]["attempts"][0]
        attempt.update(status="fail", failure_or_unknown_reason="original prior failure")
        revision = attempt["revisions"][0]
        revision.update(status="not_run", reason="no retry after earlier failure")
        revision.pop("receipt")
        self.assertEqual(self.examine(proposal)["revision_reconciliation"][0]
                         ["editable_render"]["state"], "NOT_RUN")
        revision["binding"] = {"operation": {}, "correspondence": copy.deepcopy(self.artifact)}
        self.fail(proposal, "fictional binding or observation")

    def test_n79_05_unrelated_targets_and_unblinded_review_are_rejected(self):
        proposal = self.submitted()
        protocol, review = self.review_fixture(proposal)
        first = self.examine(proposal)["independent_review_binding"]
        self.assertEqual(first["unreviewed_successful_artifact_targets"], 35)
        review["reviews"][0]["blind_order_attested"] = False
        proposal["independent_review"]["record"] = self.binding_fixture_json(
            "synthetic-review.json", review)
        self.fail(proposal, "unblinded declaration")
        review["reviews"][0]["blind_order_attested"] = True
        protocol["targets"][0]["artifact"] = self.binding_fixture_json(
            "synthetic-unrelated-render.json", {"synthetic": "different rendered artifact"})
        proposal["independent_review"]["protocol"] = self.binding_fixture_json(
            "synthetic-protocol.json", protocol)
        review["protocol_sha256"] = proposal["independent_review"]["protocol"]["sha256"]
        proposal["independent_review"]["record"] = self.binding_fixture_json(
            "synthetic-review.json", review)
        self.fail(proposal, "artifact belongs to another arm or attempt")

    def test_n79_06_external_decisions_and_unfavorable_scores_never_auto_accept(self):
        proposal = self.submitted()
        protocol, review = self.review_fixture(proposal)
        result = self.examine(proposal)
        bound = result["independent_review_binding"]
        self.assertEqual(bound["owner_decision"], "pending")
        self.assertEqual(bound["reviews"][0]["scores"]["synthetic-blind-1"]
                         ["declared-quality"], 0)
        self.assertEqual(bound["participant_and_owner_authentication"], "NOT_PERFORMED")
        decision = {"schema": gate.OWNER_DECISION_SCHEMA, "context": protocol["context"],
                    "protocol_sha256": review["protocol_sha256"],
                    "targets_sha256": gate.digest(gate.canon(protocol["targets"])),
                    "state": "declined", "owner_id": "synthetic-owner-not-authenticated",
                    "decided_at_utc": "2026-10-22T03:00:00+00:00",
                    "rationale": "synthetic unfavorable decision, not an actual owner vote"}
        review["owner_decision"] = {
            "state": "declined",
            "receipt": self.binding_fixture_json("synthetic-decision.json", decision)}
        proposal["independent_review"]["record"] = self.binding_fixture_json(
            "synthetic-review.json", review)
        result = self.examine(proposal)
        self.assertEqual(result["independent_review_binding"]["owner_decision"], "declined")
        self.assertFalse(result["independent_review_binding"]["creative_acceptance"])
        self.assertFalse(result["user_creative_quality_independently_reviewed"])
        self.assertFalse(result["release_authorized"])
        self.assertEqual(result["new_product_contract_PASS"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
