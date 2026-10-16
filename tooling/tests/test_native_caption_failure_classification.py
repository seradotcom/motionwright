"""Closed, source-safe caption error staging never certifies a native render."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_av_failure_guard import classify_native_command_failure

PRIVATE = "C:/users/private/owner/job_ref=SECRET/voice.wav"


class CaptionFailureReportTests(unittest.TestCase):
    def sample(self, stage, native_class, unknown=False, gate="unclassified"):
        return classify_native_command_failure(
            stdout=json.dumps({
                "error": {
                    "code": "Internal", "outcome_known": not unknown,
                    "message": PRIVATE,
                },
                "caption_native_pixels": "NOT_VERIFIED",
                "caption_stage": stage,
                "caption_native_class": native_class,
                "caption_verification_gate": gate,
                "retry_authorized": False,
            }).encode(), stderr=PRIVATE.encode(), exit_code=1,
        )

    def test_exact_allowlisted_native_class_is_available_without_private_data(self):
        result = self.sample("render_terminal", "observation")
        self.assertEqual(result["reported_caption_stage"], "render_terminal")
        self.assertEqual(result["reported_caption_native_class"], "observation")
        self.assertEqual(result["reported_caption_verification_gate"], "unclassified")
        self.assertEqual(result["source_outcome"], "FAILED_NOT_MEDIA_VERIFIED")
        self.assertFalse(result["terminal_native_result_verified"])
        self.assertNotIn(PRIVATE, json.dumps(result, sort_keys=True))
        self.assertFalse(result["automatically_restarted_mutating_render"])

    def test_untrusted_stage_or_render_class_is_never_echoed(self):
        for stage, native_class in [
            (PRIVATE, "observation"), ("render_terminal", PRIVATE),
            ("production_ref", "observation"),
            ("render_terminal", "new_unknown_renderer_version"),
        ]:
            with self.subTest(stage=stage, native_class=native_class):
                result = self.sample(stage, native_class, unknown=True)
                self.assertEqual(result["source_outcome"], "UNKNOWN")
                self.assertNotIn(PRIVATE, json.dumps(result, sort_keys=True))
                self.assertIn("unclassified", {
                    result["reported_caption_stage"],
                    result["reported_caption_native_class"],
                })

    def test_closed_semwright_caption_check_names_never_leak_raw_provider_receipts(self):
        for gate in ("caption_timing", "resolved_cues", "frame_coverage",
                     "other_rule", "unresolved_findings", "checks_missing",
                     "not_native"):
            with self.subTest(gate=gate):
                result = self.sample("native_verification", "unclassified", gate=gate)
                self.assertEqual(result["reported_caption_verification_gate"], gate)
                self.assertFalse(result["terminal_native_result_verified"])
                self.assertNotIn(PRIVATE, json.dumps(result))
        for unsafe in (PRIVATE, "caption_timing extra", "../../private", None, 42):
            with self.subTest(gate=unsafe):
                result = self.sample("native_verification", "unclassified", gate=unsafe)
                self.assertEqual(result["reported_caption_verification_gate"], "unclassified")
                self.assertNotIn(PRIVATE, json.dumps(result))

    def test_missing_optional_caption_fields_leave_non_caption_v01_unchanged(self):
        result = classify_native_command_failure(
            stdout=json.dumps({
                "error": {"code": "Unavailable", "outcome_known": False, "message": PRIVATE}
            }).encode(), stderr=b"", exit_code=1,
        )
        self.assertNotIn("reported_caption_stage", result)
        self.assertNotIn("reported_caption_native_class", result)
        self.assertNotIn("reported_caption_verification_gate", result)
        self.assertEqual(result["source_outcome"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
