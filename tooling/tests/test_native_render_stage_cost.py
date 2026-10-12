"""Native stage cost receipt: only measured facts, never replay or retry."""
from __future__ import annotations

from copy import deepcopy
import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_render_stage_cost as stages  # noqa: E402

SOURCE = "a" * 40
SEMWRIGHT = "b" * 40
FILM = "c" * 64


def measured() -> dict:
    return {
        "source_sha": SOURCE,
        "semwright_sha": SEMWRIGHT,
        "film_sha256": FILM,
        "mediated_job": "opaque:acknowledged-mediation-job",
        "direct_is_separate_job": True,
        "mediated_micros": 1_000_000,
        "manifest_review_micros": 17_000,
        "direct_micros": 1_200_000,
        "parity_micros": 55_000,
        "direct_stage_micros": {
            "plan": 50_000,
            "apply": 70_000,
            "start": 11_000,
            "terminal_wait": 1_000_000,
            "result": 20_000,
            "verify": 45_000,
        },
        "frame_comparison": {
            "frame_count": 60,
            "all_frames_decoded_rgba_pixel_identical": True,
            "png_container_mismatch_count": 60,
            "png_container_byte_identical": False,
            "ordered_rgba_frame_digests_sha256": "d" * 64,
        },
    }


class NativeStageCostTests(unittest.TestCase):
    def test_original_same_source_report_does_not_claim_a_speedup(self):
        report = stages.assemble_native_stage_report(**measured())
        self.assertEqual(report["schema"], "motionwright.native-render-stage-cost/1")
        self.assertEqual(report["verified_frames_per_leg"], 60)
        self.assertEqual(report["verified_rgba_frames_across_both_legs"], 120)
        self.assertEqual(report["canonical_film_sha256"], FILM)
        self.assertEqual(report["observations_micros"]["mediated_native_render"], 1_000_000)
        self.assertEqual(report["direct_broker_substage_micros"]["terminal_wait"], 1_000_000)
        compare = report["comparison"]
        self.assertTrue(compare["source_films_exactly_identical"])
        self.assertTrue(compare["independent_native_jobs"])
        self.assertTrue(compare["decoded_rgba_all_frames_exactly_equal"])
        self.assertEqual(compare["cross_run_wallclock_speedup_claim"],
                         "NOT_COMPARABLE_SEQUENTIAL_RUNS")
        self.assertIsNone(compare["cache_savings"])
        self.assertIsNone(compare["saved_native_renderer_frames"])
        self.assertEqual(compare["actual_human_creative_approval"], "NOT_RUN")
        self.assertFalse(report["release_ready"])
        self.assertFalse(report["v05_requirement_complete"])

    def test_exact_stage_identity_and_units_are_preserved(self):
        report=stages.assemble_native_stage_report(**measured())
        self.assertEqual(list(report["direct_broker_substage_micros"]),list(stages.DIRECT_STAGES))
        self.assertEqual(report["direct_broker_substage_micros"]["apply"], 70_000)
        self.assertEqual(report["direct_broker_substage_micros"]["verify"], 45_000)
        self.assertEqual(report["observations_micros"]["decoded_rgba_parity_verification"],55_000)
        self.assertEqual(report["semwright_sha"], SEMWRIGHT)
        self.assertEqual(report["source_sha"], SOURCE)

    def test_monotonic_elapsed_microseconds_ceil_sub_microsecond(self):
        self.assertEqual(stages.elapsed_us(2_000,2_000),0)
        self.assertEqual(stages.elapsed_us(2_000,2_001),1)
        self.assertEqual(stages.elapsed_us(2_000,3_000),1)
        self.assertEqual(stages.elapsed_us(2_000,3_001),2)

    def test_nonmonotonic_or_noninteger_clock_rejected(self):
        for start,end in [(2,1),(-1,100),(1,True),(None,5),(4,1.1)]:
            with self.assertRaises(stages.NativeStageError):
                stages.elapsed_us(start,end)

    def test_unbounded_wallclock_rejected(self):
        with self.assertRaisesRegex(stages.NativeStageError,"unbounded_stage_duration"):
            stages.elapsed_us(0,(stages.MAX_STAGE_US+1)*1000)

    def test_excessive_manifest_or_decode_time_rejected(self):
        for key in ["mediated_micros","manifest_review_micros","direct_micros","parity_micros"]:
            values=measured()
            values[key] = stages.MAX_STAGE_US+1
            with self.subTest(key=key), self.assertRaisesRegex(
                stages.NativeStageError,"native_phase_measurement_invalid"):
                stages.assemble_native_stage_report(**values)

    def test_zero_time_and_zero_job_identifier_fail_consistently(self):
        values=measured()
        values["mediated_job"]=""
        with self.assertRaisesRegex(stages.NativeStageError,"mediated_job_missing"):
            stages.assemble_native_stage_report(**values)

    def test_unauthorized_job_reuse_is_not_independent_proof(self):
        values=measured()
        values["direct_is_separate_job"]=False
        with self.assertRaisesRegex(stages.NativeStageError,"native_job_identity_not_independent"):
            stages.assemble_native_stage_report(**values)

    def test_missing_subphase_rejected(self):
        values=measured()
        del values["direct_stage_micros"]["verify"]
        with self.assertRaisesRegex(stages.NativeStageError,"direct_phase_measurement_invalid"):
            stages.assemble_native_stage_report(**values)

    def test_unexpected_subphase_rejected(self):
        values=measured()
        values["direct_stage_micros"]["cache_saved_frames"]=1_000_000
        with self.assertRaisesRegex(stages.NativeStageError,"direct_phase_measurement_invalid"):
            stages.assemble_native_stage_report(**values)

    def test_invented_negative_or_boolean_phase_is_not_a_measurement(self):
        for measured_value in [-3,True,1.5,None]:
            values=measured()
            values["direct_stage_micros"]["plan"]=measured_value
            with self.subTest(measured_value=measured_value), self.assertRaisesRegex(
                stages.NativeStageError,"direct_phase_measurement_invalid"):
                stages.assemble_native_stage_report(**values)

    def test_direct_subphases_exceeding_total_fail_closed(self):
        values=measured()
        values["direct_micros"]=1_000
        with self.assertRaisesRegex(stages.NativeStageError,"direct_phase_measurement_exceeds_total"):
            stages.assemble_native_stage_report(**values)

    def test_unmatched_semwright_source_pin_rejected(self):
        values=measured()
        values["semwright_sha"]="B"*40
        with self.assertRaisesRegex(stages.NativeStageError,"semwright_sha_invalid"):
            stages.assemble_native_stage_report(**values)

    def test_malformed_source_sha_rejected(self):
        values=measured()
        values["source_sha"]="main"
        with self.assertRaisesRegex(stages.NativeStageError,"source_sha_invalid"):
            stages.assemble_native_stage_report(**values)

    def test_film_identity_requires_sha256(self):
        values=measured()
        values["film_sha256"]="film"
        with self.assertRaisesRegex(stages.NativeStageError,"film_identity_invalid"):
            stages.assemble_native_stage_report(**values)

    def test_partial_native_frame_manifest_rejected(self):
        values=measured()
        values["frame_comparison"]["frame_count"]=59
        with self.assertRaisesRegex(stages.NativeStageError,"frame_parity_unverified"):
            stages.assemble_native_stage_report(**values)

    def test_different_rgba_pixels_not_valid_evidence(self):
        values=measured()
        values["frame_comparison"]["all_frames_decoded_rgba_pixel_identical"]=False
        with self.assertRaisesRegex(stages.NativeStageError,"frame_parity_unverified"):
            stages.assemble_native_stage_report(**values)

    def test_png_metadata_mismatch_does_not_falsify_rgba_equality(self):
        values=measured()
        values["frame_comparison"]["png_container_mismatch_count"]=60
        self.assertEqual(stages.assemble_native_stage_report(**values)["status"],
                         "INDEPENDENT_NATIVE_RENDER_PHASES_MEASURED")
        values["frame_comparison"]["png_container_mismatch_count"]=61
        with self.assertRaisesRegex(stages.NativeStageError,"frame_parity_unverified"):
            stages.assemble_native_stage_report(**values)

    def test_unverified_frame_digest_rejected(self):
        values=measured()
        values["frame_comparison"]["ordered_rgba_frame_digests_sha256"]="Z"*64
        with self.assertRaisesRegex(stages.NativeStageError,"frame_digest_unverified"):
            stages.assemble_native_stage_report(**values)

    def test_report_is_pure_redacted_deterministic_json(self):
        values=measured()
        prior=deepcopy(values)
        x=stages.assemble_native_stage_report(**values)
        y=stages.assemble_native_stage_report(**values)
        self.assertEqual(x,y)
        self.assertEqual(values,prior)
        serialized=json.dumps(x)
        for secret in ["session_file","socket","auth_token","connection_file","output_root",
                       "native_cli_secret","/home/private-owner/","C:\\Users\\private-owner"]:
            self.assertNotIn(secret,serialized)
        self.assertIn("NOT_RUN",serialized)

    def test_boolean_frame_count_is_not_an_integer(self):
        values=measured()
        values["frame_comparison"]["frame_count"]=True
        with self.assertRaisesRegex(stages.NativeStageError,"frame_parity_unverified"):
            stages.assemble_native_stage_report(**values)


if __name__=="__main__":
    unittest.main()
