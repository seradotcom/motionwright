"""Adversarial proof that native render phase output never leaks private text."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semantic_sfx_native_stage import (STAGES, safe_stage, safe_native_error_category,
                                        NATIVE_RENDER_FAILURE_CLASSES)


class ClosedNativeStageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.file = Path(self.directory.name) / "phase.json"

    def tearDown(self):
        self.directory.cleanup()

    def write(self, stage="entered"):
        self.file.write_text(json.dumps({
            "schema": "motionwright.sfx-ci-native-stage/1",
            "stage": stage,
            "final_av_master_verified": False,
            "native_job_outcome_established": False,
            "automatically_retried": False,
        }), encoding="utf-8")

    def test_each_allowlisted_stage_remains_a_nonresult(self):
        for stage in sorted(STAGES):
            with self.subTest(stage=stage):
                self.write(stage)
                self.assertEqual(safe_stage(self.file), stage)

    def test_unknown_or_malformed_or_private_stage_fails_closed(self):
        self.write("C:/Owner/Secrets/owner-session")
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")
        self.file.write_text('{"stage":"entered","stage":"unknown"}', encoding="utf-8")
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")
        self.file.write_text("garbage", encoding="utf-8")
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")

    def test_non_authoritative_success_flag_is_rejected(self):
        self.write()
        raw = json.loads(self.file.read_text())
        raw["native_job_outcome_established"] = True
        self.file.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")
        raw["native_job_outcome_established"] = False
        raw["private_user_session"] = "untrusted"
        self.file.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")

    def test_missing_or_excessive_file_does_not_publish_raw_data(self):
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")
        self.file.write_bytes(b"x" * 1100)
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")



class ClosedNativeErrorEnvelopeTests(unittest.TestCase):
    """A Rust error plus arbitrary private stderr must never enter CI logs."""

    def test_bounded_stdout_error_survives_unstructured_rust_stderr(self):
        from native_av_failure_guard import classify_native_command_failure
        envelope = json.dumps({
            "error": {"code": "Unavailable", "outcome_known": False},
            "receipts": [],
            "native_media_verified": False,
            "automatic_mutating_retry": False,
        }).encode("utf-8")
        sensitive = b"Error: owner/session/private-broker-id"
        proof = classify_native_command_failure(
            stdout=envelope, stderr=sensitive, exit_code=1,
        )
        self.assertEqual(proof["reported_error_code"], "Unavailable")
        self.assertEqual(proof["source_outcome"], "UNKNOWN")
        self.assertFalse(proof["decoded_media_verified"])
        self.assertFalse(proof["raw_cli_output_in_diagnostic"])
        self.assertFalse(proof["automatically_restarted_mutating_render"])
        self.assertNotIn(b"private-broker-id".decode(), json.dumps(proof))

    def test_authoritative_failure_code_cannot_be_reclassified_as_success(self):
        from native_av_failure_guard import classify_native_command_failure
        for known in (True, False):
            with self.subTest(known=known):
                raw = json.dumps({
                    "error": {"code": "Conflict", "outcome_known": known},
                    "receipts": [],
                }).encode("utf-8")
                proof = classify_native_command_failure(
                    stdout=raw, stderr=b"", exit_code=1,
                )
                self.assertNotEqual(proof["source_outcome"], "SUCCESS")
                self.assertFalse(proof["terminal_native_result_verified"])
                self.assertFalse(proof["automatically_restarted_mutating_render"])

    def test_closed_receipts_count_start_without_exposing_job_identifiers(self):
        from native_av_failure_guard import classify_native_command_failure
        safe_error = {
            "error": {"code": "Conflict", "outcome_known": False},
            "receipts": [
                {"command": "driver.motion-canvas.render.start",
                 "stage": "completed", "payload": {}},
                {"command": "driver.motion-canvas.render.status",
                 "stage": "outcome_unknown", "payload": {}},
            ],
            "native_media_verified": False,
            "automatic_mutating_retry": False,
        }
        proof = classify_native_command_failure(
            stdout=json.dumps(safe_error).encode("utf-8"),
            stderr=b"Error: local owner file /secret/path and job ref",
            exit_code=1,
        )
        self.assertEqual(proof["original_render_start_receipts_observed"], 1)
        self.assertEqual(proof["source_outcome"], "UNKNOWN")
        self.assertFalse(proof["terminal_native_result_verified"])
        self.assertFalse(proof["native_job_references_disclosed"])
        self.assertNotIn("/secret/path", json.dumps(proof))



class ClosedSdkReasonCategories(unittest.TestCase):
    """No source-derived secret or successful media claim can leak."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "error.json"
        self.evidence = {
            "schema": "motionwright.sfx-ci-native-error-category/1",
            "phase": "native_request_error",
            "sdk_code": "BackendFailed",
            "reason_class": "composition_plan",
            "render_failure_class": "not_applicable",
            "sdk_outcome_known": True,
            "plan_completed": 0,
            "apply_completed": 0,
            "render_start_completed": 0,
            "render_start_dispatching": 0,
            "render_status_seen": 0,
            "render_result_seen": 0,
            "verify_completed": 0,
            "mux_seen": 0,
            "native_media_verified": False,
            "automatically_retried": False,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, value=None):
        self.path.write_text(json.dumps(
            self.evidence if value is None else value
        ), encoding="utf-8")

    def test_exact_closed_sdk_error_without_any_success_claim(self):
        self.write()
        safe = safe_native_error_category(self.path)
        self.assertIsNotNone(safe)
        self.assertEqual(safe["sdk_code"], "BackendFailed")
        self.assertEqual(safe["reason_class"], "composition_plan")
        self.assertTrue(safe["sdk_outcome_known"])
        self.assertFalse(safe["native_media_verified"])
        self.assertFalse(safe["automatically_retried"])

    def test_plausible_started_native_render_still_not_verified(self):
        data = {**self.evidence, "render_start_completed": 1,
                "sdk_outcome_known": False, "phase": "native_master_error"}
        self.write(data)
        safe = safe_native_error_category(self.path)
        self.assertIsNotNone(safe)
        self.assertEqual(safe["render_start_completed"], 1)
        self.assertFalse(safe["native_media_verified"])

    def test_private_or_unknown_error_codes_fail_closed(self):
        for key, value in (
            ("sdk_code", "owner/secret/private"),
            ("reason_class", "private provider error payload"),
            ("render_failure_class", "C:/owner/private"),
            ("phase", "some arbitrary phase"),
            ("native_media_verified", True),
            ("automatically_retried", True),
            ("render_start_completed", -1),
            ("render_start_completed", 129),
            ("sdk_outcome_known", 1),
        ):
            with self.subTest(key=key, value=value):
                self.write({**self.evidence, key: value})
                self.assertIsNone(safe_native_error_category(self.path))

    def test_render_failure_class_is_allowlisted_and_bound_to_failed_render(self):
        known = {**self.evidence, "reason_class": "render_failed",
                 "render_failure_class": "renderer_state_model_invariant",
                 "render_start_completed": 1, "render_status_seen": 6}
        self.write(known)
        read = safe_native_error_category(self.path)
        self.assertIsNotNone(read)
        self.assertEqual(read["render_failure_class"],
                         "renderer_state_model_invariant")
        self.assertFalse(read["native_media_verified"])
        for invalid in (
            {**known, "render_failure_class": "arbitrary provider stack"},
            {**known, "reason_class": "composition_plan"},
            {**self.evidence, "render_failure_class": "vite_build"},
        ):
            with self.subTest(invalid=invalid):
                self.write(invalid)
                self.assertIsNone(safe_native_error_category(self.path))

    def test_closed_renderer_categories_match_native_source_contract(self):
        # Producer and diagnostic validator must never drift silently.
        import re
        source = (Path(__file__).resolve().parents[2]
                  / "crates/native/src/production.rs").read_text(encoding="utf-8")
        body = source.split("fn motion_failure_class(data: &Value)", 1)[1]
        body = body.split("let reported =", 1)[0]
        supported = frozenset(re.findall(r'"([a-z0-9_]+)"', body))
        self.assertEqual(
            supported | {"unclassified", "not_applicable"},
            NATIVE_RENDER_FAILURE_CLASSES,
        )

    def test_duplicate_or_missing_or_oversized_fields_fail_closed(self):
        self.path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
        self.assertIsNone(safe_native_error_category(self.path))
        self.write({key:value for key,value in self.evidence.items()
                    if key!="reason_class"})
        self.assertIsNone(safe_native_error_category(self.path))
        self.path.write_bytes(b"x"*2100)
        self.assertIsNone(safe_native_error_category(self.path))


if __name__ == "__main__":
    unittest.main()
