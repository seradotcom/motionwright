"""Adversarial proof that native render phase output never leaks private text."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semantic_sfx_native_stage import STAGES, safe_stage


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


if __name__ == "__main__":
    unittest.main()
