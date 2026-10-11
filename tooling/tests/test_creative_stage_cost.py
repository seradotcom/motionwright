"""Adversarial tests; all measurements here are intentionally synthetic fixtures."""
from __future__ import annotations

import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import creative_stage_cost as stage  # noqa: E402

SHA = "a" * 40
STAGE_NAMES = tuple(name for _, keys in stage.STAGES for name in keys)


def benchmark() -> dict:
    rows = []
    for brief in sorted(stage.BRIEFS):
        for size, count in stage.SIZES.items():
            rows.append({
                "brief": brief, "size": size, "scene_count": count,
                "seed_micros": 300 + count, "revision_start": 5,
                "revision_end": 15, "revision_delta": 10,
                "revision_ten_micros": 130, "max_commit_micros": 10,
                "reopen_micros": 12, "event_count": 15,
                "database_bytes": 1024 * count,
                "operations": [
                    {"name": name, "micros": index+1}
                    for index,name in enumerate(STAGE_NAMES)
                ],
                "cache": {
                    "scope": "project:test",
                    "kind": "preview-index",
                    "fingerprint_sha256": "b" * 64,
                    "stored_bytes": count * 40,
                    "validated_bytes": count * 40,
                    "store_micros": 7,
                    "validation_micros": 4,
                    "avoided_work_units": count,
                },
            })
    return {
        "schema": stage.SOURCE_SCHEMA,
        "source_sha": SHA,
        "generated_by": "motionwright-service/examples/acceptance_bench",
        "latency_budget_ms": 2000,
        "briefs": 3,
        "datasets": 9,
        "results": rows,
        "cache_evidence": {
            "implemented": True,
            "lookup_attempts": 9,
            "hits": 9,
            "avoided_work_count": sum(x["scene_count"] for x in rows),
            "work_unit": "scene preview-index regeneration units",
            "validated_bytes": sum(x["cache"]["validated_bytes"] for x in rows),
        },
        "human_feedback": "NOT_RUN: there were no recruited humans",
        "comparative_claims": "NONE: no competing editor was benchmarked",
    }


class StageCostSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-stage-cost-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = benchmark()

    def fail_code(self, code: str):
        with self.assertRaises(stage.EvidenceInvalid) as e:
            stage.build_stage_report(self.report, SHA)
        self.assertEqual(e.exception.code, code)

    def test_exact_source_nine_projects_and_stage_totals(self):
        result = stage.build_stage_report(self.report, SHA)
        self.assertEqual(result["schema"], stage.SCHEMA)
        self.assertEqual(result["source_sha"], SHA)
        self.assertEqual(result["status"], "BOUNDED_EDIT_COST_OBSERVED")
        self.assertEqual(result["dataset_count"], 9)
        self.assertEqual(result["stage_measurements"]["concept_and_intent"]
                         ["total_commit_micros"], 9 * (1+2+3))
        self.assertEqual(result["stage_measurements"]["shot_timing_and_review"]
                         ["total_commit_micros"], 9 * (4+5+6+7))
        self.assertEqual(result["stage_measurements"]["source_safety_and_revision"]
                         ["total_commit_micros"], 9 * (8+9+10))
        self.assertTrue(all(r["total_measured_commit_micros"] == 55
                            for r in result["per_dataset"]))

    def test_savings_and_native_approval_never_inferred(self):
        result = stage.build_stage_report(self.report, SHA)
        cache = result["cache_evidence"]
        self.assertEqual(cache["avoided_work_units"], 3*(8+32+96))
        self.assertIsNone(cache["wallclock_render_time_saved_micros"])
        self.assertIsNone(cache["actual_renderer_frames_avoided"])
        self.assertIsNone(cache["cash_savings"])
        self.assertEqual(result["real_native_renderer_evaluated"], "NOT_RUN")
        self.assertEqual(result["human_interaction_evaluated"], "NOT_RUN")
        self.assertFalse(result["claims"]["release_ready"])
        self.assertFalse(result["claims"]["v05_requirement_complete"])

    def test_order_is_deterministic_under_source_row_shuffle(self):
        expected = stage.build_stage_report(self.report, SHA)
        self.report["results"].reverse()
        self.assertEqual(stage.build_stage_report(self.report, SHA), expected)

    def test_report_preserves_only_known_brief_labels_and_no_local_paths(self):
        value = json.dumps(stage.build_stage_report(self.report, SHA))
        self.assertNotIn(str(self.root), value)
        self.assertNotIn("session_file", value)
        self.assertNotIn("connection_file", value)
        self.assertNotIn("owner_token", value)
        self.assertEqual(set(x["brief"] for x in stage.build_stage_report(
            self.report, SHA)["per_dataset"]), stage.BRIEFS)

    def test_mismatched_sha_rejected(self):
        self.report["source_sha"] = "b"*40
        self.fail_code("source_sha_mismatch")

    def test_non_commit_sha_rejected(self):
        with self.assertRaises(stage.EvidenceInvalid) as e:
            stage.build_stage_report(self.report, "main")
        self.assertEqual(e.exception.code, "expected_source_sha_invalid")

    def test_forged_generator_rejected(self):
        self.report["generated_by"] = "unverified-other-generator"
        self.fail_code("untrusted_generator")

    def test_wrong_source_schema_rejected(self):
        self.report["schema"] = "motionwright.fake/1"
        self.fail_code("source_schema_invalid")

    def test_missing_dataset_does_not_silently_sample_less(self):
        self.report["results"].pop()
        self.fail_code("dataset_contract_invalid")

    def test_duplicate_dataset_rejected(self):
        self.report["results"][0] = deepcopy(self.report["results"][1])
        self.fail_code("duplicate_dataset")

    def test_unrecognized_brief_rejected(self):
        self.report["results"][0]["brief"] = "other"
        self.fail_code("dataset_identity_invalid")

    def test_falsified_scene_count_rejected(self):
        self.report["results"][0]["scene_count"] = 200
        self.fail_code("dataset_shape_invalid")

    def test_unreproduced_revision_ten_rejected(self):
        self.report["results"][0]["revision_end"] = 14
        self.fail_code("revision_ten_not_reproduced")

    def test_missing_operation_rejected(self):
        self.report["results"][0]["operations"].pop()
        self.fail_code("operation_count_invalid")

    def test_renamed_operation_rejected_instead_of_guessing_stage(self):
        self.report["results"][0]["operations"][0]["name"] = "render"
        self.fail_code("operation_grammar_invalid")

    def test_swapped_operation_order_rejected(self):
        ops=self.report["results"][0]["operations"]
        ops[0],ops[1]=ops[1],ops[0]
        self.fail_code("operation_grammar_invalid")

    def test_negative_duration_rejected(self):
        self.report["results"][0]["operations"][3]["micros"] = -2
        self.fail_code("operation_measurement_invalid")

    def test_boolean_duration_rejected(self):
        self.report["results"][0]["operations"][3]["micros"] = True
        self.fail_code("operation_measurement_invalid")

    def test_max_commit_claim_must_match_operations(self):
        self.report["results"][0]["max_commit_micros"] = 9
        self.fail_code("max_commit_mismatch")

    def test_total_commit_exceeding_measured_revision_walltime_rejected(self):
        self.report["results"][0]["revision_ten_micros"] = 12
        self.fail_code("revision_stage_walltime_conflict")

    def test_cache_bytes_must_have_been_validated(self):
        self.report["results"][0]["cache"]["validated_bytes"] = 0
        self.fail_code("cache_validation_inconsistent")

    def test_forged_avoided_work_units_rejected(self):
        self.report["results"][0]["cache"]["avoided_work_units"] = 90000
        self.fail_code("cache_validation_inconsistent")

    def test_wrong_cache_digest_rejected(self):
        self.report["results"][0]["cache"]["fingerprint_sha256"] = "X"*64
        self.fail_code("cache_identity_invalid")


    def test_wrong_cache_kind_cannot_claim_renderer_savings(self):
        self.report["results"][0]["cache"]["kind"] = "render-cache"
        self.fail_code("cache_identity_invalid")

    def test_forged_cache_aggregate_rejected(self):
        self.report["cache_evidence"]["hits"] = 10
        self.fail_code("cache_aggregate_invalid")

    def test_false_human_feedback_rejected(self):
        self.report["human_feedback"] = "PASS: happy users"
        self.fail_code("human_evidence_not_independent")

    def test_false_competitor_superiority_rejected(self):
        self.report["comparative_claims"] = "BETTER THAN BLENDER"
        self.fail_code("unverified_comparison")

    def test_excessive_duration_rejected(self):
        self.report["results"][0]["operations"][0]["micros"] = 10**20
        self.fail_code("operation_measurement_invalid")

    def test_duplicate_source_json_field_rejected(self):
        path = self.root / "duplicate.json"
        path.write_text(
            '{"schema":"motionwright.performance.v1","schema":"different"}',
            encoding="utf8")
        with self.assertRaises(stage.EvidenceInvalid) as e:
            stage.read_source(path)
        self.assertEqual(e.exception.code, "duplicate_json_key")

    def test_oversized_input_rejected_before_json_parsing(self):
        path = self.root / "oversized.json"
        path.write_bytes(b"X" * (stage.MAX_SOURCE_BYTES + 1))
        with self.assertRaises(stage.EvidenceInvalid) as e:
            stage.read_source(path)
        self.assertEqual(e.exception.code, "input_size_invalid")

    def test_missing_input_fails_closed(self):
        with self.assertRaises(stage.EvidenceInvalid) as e:
            stage.read_source(self.root / "missing.json")
        self.assertEqual(e.exception.code, "input_size_invalid")

    def test_report_receipt_new_file_only(self):
        dest = self.root / "stage-cost.json"
        result = stage.build_stage_report(self.report, SHA)
        stage.write_new_report(dest, result)
        original = dest.read_bytes()
        with self.assertRaises(stage.EvidenceInvalid) as e:
            stage.write_new_report(dest, {"schema":stage.SCHEMA,"fake":"replace"})
        self.assertEqual(e.exception.code, "receipt_not_written")
        self.assertEqual(dest.read_bytes(), original)

    def test_cli_keeps_all_source_bytes_and_does_not_create_default_artifacts(self):
        path = self.root / "evidence.json"
        path.write_text(json.dumps(self.report),encoding="utf8")
        before = path.read_bytes()
        existed = set(self.root.iterdir())
        result = io.StringIO()
        with contextlib.redirect_stdout(result):
            code = stage.main(["--input",str(path),"--source-sha",SHA])
        self.assertEqual(code, 0)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(set(self.root.iterdir()), existed)
        self.assertEqual(json.loads(result.getvalue())["source_sha"],SHA)

    def test_cli_explicit_output_is_valid_json_and_cannot_be_reused(self):
        path = self.root / "evidence.json"
        path.write_text(json.dumps(self.report),encoding="utf8")
        out = self.root / "new-report.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = stage.main(["--input",str(path),"--source-sha",SHA,
                               "--output",str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.read_text())["status"],
                         "BOUNDED_EDIT_COST_OBSERVED")
        before = out.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            code = stage.main(["--input",str(path),"--source-sha",SHA,
                               "--output",str(out)])
        self.assertEqual(code, 2)
        self.assertEqual(out.read_bytes(), before)

    def test_bad_input_never_leaks_local_paths_to_stderr(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = stage.main(["--input",str(self.root / "private-session.json"),
                               "--source-sha",SHA])
        self.assertEqual(code,2)
        self.assertIn("input_size_invalid",errors.getvalue())
        self.assertNotIn(str(self.root),errors.getvalue())


if __name__ == "__main__":
    unittest.main()
