"""Adversarial synthetic fixtures for the first editable scene technical proxy."""
from __future__ import annotations

import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parent))
from test_creative_stage_cost import benchmark, SHA  # noqa: E402
import first_scene_reuse as first  # noqa: E402
from creative_stage_cost import EvidenceInvalid  # noqa: E402


class FirstSceneSafetyTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="mw-first-scene-")
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.source=benchmark()
        for row in self.source["results"]:
            row.update(
                fresh_store_open_micros=120,
                fresh_project_create_micros=180,
                first_editable_scene_micros=50,
                first_scene_preserved_after_revision_ten=True,
            )
    def result(self)->dict:
        return first.first_scene_report(self.source,SHA)
    def rejects(self,code:str)->None:
        with self.assertRaises(EvidenceInvalid) as e:
            self.result()
        self.assertEqual(e.exception.code,code)

    def test_nine_measured_projects_and_original_source_sha(self):
        result=self.result()
        self.assertEqual(result["schema"],first.SCHEMA)
        self.assertEqual(result["source_sha"],SHA)
        self.assertEqual(result["projects"],9)
        self.assertEqual(len(result["per_dataset"]),9)
        self.assertEqual(result["recorded_revisions_per_project"],10)
        self.assertTrue(result["first_scene_identity_preserved_across_reopen"])

    def test_first_scene_clock_starts_after_project_creation(self):
        result=self.result()
        for item in result["per_dataset"]:
            self.assertEqual(item["new_store_open_micros"],120)
            self.assertEqual(item["create_project_micros"],180)
            self.assertEqual(item["first_persisted_editable_scene_after_new_project_micros"],50)
            self.assertEqual(item["source_edit_revision_count"],10)
        self.assertEqual(
            result["measurements"]["first_editable_scene_after_project_creation"]["median_micros"],
            50,
        )
        self.assertEqual(result["measurements"]["new_sqlite_store_open"]["median_micros"],120)

    def test_cache_reuse_is_not_a_video_render_savings_claim(self):
        result=self.result()
        cache=result["reuse"]
        self.assertEqual(cache["source_fingerprint_verified_preview_index_work_units"],408)
        self.assertIsNone(cache["native_frames_regenerated_or_avoided"])
        self.assertIsNone(cache["renderer_saved_wallclock_micros"])
        self.assertEqual(cache["audience_reuse_or_adoption"],"NOT_MEASURED")

    def test_user_value_and_installed_first_frame_are_not_fabricated(self):
        result=self.result()
        self.assertEqual(result["human_first_use"],"NOT_RUN")
        self.assertEqual(result["installed_desktop_first_frame"],"NOT_MEASURED")
        self.assertEqual(result["rendered_first_useful_video"],"NOT_MEASURED")
        self.assertEqual(result["end_user_first_useful_result"],"NOT_MEASURED")
        self.assertFalse(result["claims"]["user_value_demonstrated"])
        self.assertFalse(result["claims"]["release_ready"])
        self.assertFalse(result["claims"]["v05_requirement_complete"])

    def test_deterministic_order_regardless_of_benchmark_row_order(self):
        original=self.result()
        self.source["results"].reverse()
        self.assertEqual(original,self.result())

    def test_does_not_mutate_source_evidence_or_private_state(self):
        old=deepcopy(self.source)
        self.result()
        self.assertEqual(self.source,old)
        text=json.dumps(self.result())
        self.assertNotIn(str(self.root),text)
        self.assertNotIn("connection_file",text)
        self.assertNotIn("session_file",text)
        self.assertNotIn("effect_grant_token",text)

    def test_forged_unverified_first_scene_is_rejected(self):
        self.source["results"][0]["first_scene_preserved_after_revision_ten"]=False
        self.rejects("first_scene_persistence_not_verified")

    def test_missing_persistence_proof_is_not_assumed(self):
        del self.source["results"][0]["first_scene_preserved_after_revision_ten"]
        self.rejects("first_scene_persistence_not_verified")

    def test_first_scene_clock_cannot_follow_total_seed(self):
        self.source["results"][0]["first_editable_scene_micros"]=9999
        self.rejects("first_scene_after_seed_end")

    def test_missing_first_scene_measurement_rejected(self):
        del self.source["results"][0]["first_editable_scene_micros"]
        self.rejects("first_scene_measurement_invalid")

    def test_bad_clock_type_rejected(self):
        for value in (True,1.0,-1,None,"100"):
            with self.subTest(value=value):
                src=deepcopy(self.source)
                src["results"][0]["fresh_store_open_micros"]=value
                with self.assertRaises(EvidenceInvalid):
                    first.first_scene_report(src,SHA)

    def test_zero_duration_is_not_real_first_source_scene_evidence(self):
        self.source["results"][0]["first_editable_scene_micros"]=0
        self.rejects("first_scene_measurement_invalid")

    def test_missing_project_creation_cost_rejected(self):
        del self.source["results"][0]["fresh_project_create_micros"]
        self.rejects("first_scene_measurement_invalid")

    def test_falsified_source_sha_does_not_match_checkout(self):
        self.source["source_sha"]="b"*40
        self.rejects("source_sha_mismatch")

    def test_nonsha_ref_is_not_an_exact_source(self):
        with self.assertRaises(EvidenceInvalid) as e:
            first.first_scene_report(self.source,"main")
        self.assertEqual(e.exception.code,"expected_source_sha_invalid")

    def test_missing_or_duplicated_sml_dataset_rejected(self):
        self.source["results"].pop()
        self.rejects("dataset_contract_invalid")

    def test_missing_ten_revisions_fails_closed(self):
        self.source["results"][0]["revision_delta"]=9
        self.rejects("revision_ten_not_reproduced")

    def test_cache_fingerprint_verification_required(self):
        self.source["results"][0]["cache"]["validated_bytes"]=0
        self.rejects("cache_validation_inconsistent")

    def test_false_human_feedback_rejected(self):
        self.source["human_feedback"]="PASS"
        self.rejects("human_evidence_not_independent")

    def test_report_receipt_is_new_file_only(self):
        path=self.root/"first.json"
        first.write_receipt(path,self.result())
        existing=path.read_bytes()
        with self.assertRaises(EvidenceInvalid) as e:
            first.write_receipt(path,self.result())
        self.assertEqual(e.exception.code,"receipt_write_refused")
        self.assertEqual(path.read_bytes(),existing)

    def test_cli_explicit_source_report_does_not_overwrite_original(self):
        src=self.root/"source.json"
        src.write_text(json.dumps(self.source),encoding="utf8")
        out=self.root/"first-scene.json"
        baseline=src.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(first.main(["--input",str(src),"--source-sha",SHA,
                                         "--output",str(out)]),0)
        self.assertEqual(src.read_bytes(),baseline)
        self.assertEqual(json.loads(out.read_text())["schema"],first.SCHEMA)
        saved=out.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(first.main(["--input",str(src),"--source-sha",SHA,
                                         "--output",str(out)]),2)
        self.assertEqual(out.read_bytes(),saved)

    def test_cli_without_output_is_read_only(self):
        src=self.root/"source.json"
        src.write_text(json.dumps(self.source),encoding="utf8")
        before=set(self.root.iterdir())
        stdout=io.StringIO()
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(io.StringIO()):
            code=first.main(["--input",str(src),"--source-sha",SHA])
        self.assertEqual(code,0)
        self.assertEqual(set(self.root.iterdir()),before)
        self.assertEqual(json.loads(stdout.getvalue())["projects"],9)

    def test_cli_bad_input_redacts_private_filepath(self):
        stderr=io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code=first.main(["--input",str(self.root/"private.json"),
                             "--source-sha",SHA])
        self.assertEqual(code,2)
        self.assertNotIn(str(self.root),stderr.getvalue())
        self.assertIn("input_size_invalid",stderr.getvalue())

    def test_wrong_receipt_schema_cannot_be_written(self):
        with self.assertRaises(EvidenceInvalid):
            first.write_receipt(self.root/"bad.json",{"schema":"unknown"})

    def test_missing_native_renderer_data_remains_not_measured(self):
        result=self.result()
        self.assertEqual(result["rendered_first_useful_video"],"NOT_MEASURED")
        self.assertEqual(result["human_revisions_and_preference"],"NOT_RUN")
        self.assertIsNone(result["reuse"]["renderer_saved_wallclock_micros"])

    def test_each_source_scene_identity_survived_reopen(self):
        result=self.result()
        self.assertTrue(all(x["first_scene_survived_ten_revisions_and_sqlite_reopen"]
                            for x in result["per_dataset"]))
        self.assertEqual({x["size"] for x in result["per_dataset"]},{"S","M","L"})

    def test_stage_duration_over_limit_fails_closed(self):
        self.source["results"][0]["first_editable_scene_micros"]=10**20
        self.rejects("first_scene_measurement_invalid")


if __name__=="__main__":
    unittest.main()
