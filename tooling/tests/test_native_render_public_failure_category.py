"""Synthetic v0.1 public native-render negative-evidence safety tests.

Only fabricated private-looking payloads are used: no renderer, owner runtime,
Semwright connection, native mutation or acceptance pass is created here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_render_public_artifact as pub

SOURCE = "a" * 40
SDK = "b" * 40
SECRET = "private-native-ref-owner-token-never-upload"


class ClosedNativeFailurePublicationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mw-v01-failure-public-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "original-private-evidence"
        self.root.mkdir()
        self.out = Path(tmp.name) / "public-artifact"

    def diagnostic(self, **updates):
        data = {
            "schema": pub.FAILURE_SCHEMA,
            "result": "FAIL",
            "reason": "native_status_unconfirmed",
            "source_outcome": "UNKNOWN",
            "reported_error_code": "Timeout",
            "original_render_start_receipts_observed": 1,
            "terminal_native_result_verified": False,
            "decoded_media_verified": False,
            "automatically_restarted_mutating_render": False,
            "provider_generation_inactive_observed": False,
            "motionwright_source_sha": SOURCE,
            "semwright_source_pin": SDK,
            "cli_exit_category": "nonzero_exit",
            "opaque_original_job_ref": SECRET,
            "private_filesystem_path": "/private/owner/" + SECRET,
            "raw_stderr": SECRET,
            "receipts": [{"request_id": SECRET, "job_ref": SECRET}],
        }
        data.update(updates)
        (self.root / "native-render-failure-diagnostic.json").write_text(
            json.dumps(data), encoding="utf-8",
        )
        return data

    def publish_failed(self, status="failure"):
        result = pub.publish(self.root, self.out, SOURCE, SDK, status)
        self.assertEqual(result["ci_native_motion_canvas_evidence"], "NOT_VERIFIED")
        self.assertEqual(result["included_files"], [])
        self.assertFalse(result["v01_release_authorized"])
        self.assertFalse(result["native_mutation_replayed_by_publisher"])
        self.assertEqual({p.name for p in self.out.iterdir()},
                         {"public-evidence.json"})
        public = (self.out / "public-evidence.json").read_text()
        self.assertNotIn(SECRET, public)
        self.assertNotIn("job_ref", public)
        self.assertNotIn("raw_stderr", public)
        self.assertNotIn("/private/owner", public)
        return result["ci_failure_observation"]

    def test_normal_failure_absent_diagnostic_is_never_claimed_positive(self):
        obs = self.publish_failed()
        self.assertEqual(obs["observation"], "NOT_RECORDED")
        self.assertEqual(obs["safe_reason"], "NOT_AVAILABLE")
        self.assertEqual(obs["source_outcome"], "NOT_VERIFIED")
        self.assertFalse(obs["terminal_native_result_verified"])

    def test_allowlisted_typed_reason_excludes_all_private_fields(self):
        self.diagnostic()
        obs = self.publish_failed()
        self.assertEqual(obs["observation"], "SOURCE_BOUND_CI_FAILURE_CATEGORY")
        self.assertEqual(obs["safe_reason"], "native_status_unconfirmed")
        self.assertEqual(obs["source_outcome"], "UNKNOWN")
        self.assertEqual(obs["cli_exit_category"], "nonzero_exit")
        self.assertTrue(obs["acknowledged_start_observed"])
        self.assertFalse(obs["provider_root_cause_verified"])
        self.assertFalse(obs["original_native_job_retried"])
        self.assertEqual(len(obs), 9)

    def test_inactive_generation_is_observation_not_causation(self):
        self.diagnostic(
            reason="provider_generation_inactive",
            provider_generation_inactive_observed=True,
            cli_exit_category="signal",
        )
        obs = self.publish_failed()
        self.assertEqual(obs["safe_reason"], "provider_generation_inactive")
        self.assertTrue(obs["provider_generation_inactive_observed"])
        self.assertFalse(obs["provider_root_cause_verified"])

    def test_known_error_after_acknowledged_start_never_proves_terminal(self):
        self.diagnostic(
            source_outcome="FAILED_NOT_MEDIA_VERIFIED",
            reason="native_command_failed",
        )
        obs = self.publish_failed()
        self.assertEqual(obs["source_outcome"], "UNKNOWN")
        self.assertTrue(obs["acknowledged_start_observed"])

    def test_unacknowledged_known_enclosing_command_is_not_media_pass(self):
        self.diagnostic(
            original_render_start_receipts_observed=0,
            source_outcome="FAILED_NOT_MEDIA_VERIFIED",
        )
        obs = self.publish_failed()
        self.assertEqual(obs["source_outcome"], "FAILED_NOT_MEDIA_VERIFIED")
        self.assertFalse(obs["terminal_native_result_verified"])

    def test_forced_ci_success_claim_in_diagnostic_refused(self):
        self.diagnostic(terminal_native_result_verified=True)
        obs = self.publish_failed()
        self.assertEqual(obs["observation"], "UNTRUSTED_PRIVATE_DIAGNOSTIC")
        self.assertEqual(obs["safe_reason"], "NOT_AVAILABLE")

    def test_duplicate_native_mutation_retry_flag_refused(self):
        self.diagnostic(automatically_restarted_mutating_render=True)
        obs = self.publish_failed()
        self.assertEqual(obs["observation"], "UNTRUSTED_PRIVATE_DIAGNOSTIC")

    def test_foreign_source_or_semwright_pin_cannot_be_transported(self):
        for change in ({"motionwright_source_sha": "c"*40},
                       {"semwright_source_pin": "c"*40}):
            with self.subTest(change=change):
                self.diagnostic(**change)
                obs = self.publish_failed()
                self.assertEqual(obs["observation"], "UNTRUSTED_PRIVATE_DIAGNOSTIC")
                self.out.rmdir() if not any(self.out.iterdir()) else None
                (self.out / "public-evidence.json").unlink()
                self.out.rmdir()

    def test_bogus_unhashable_reason_fails_closed_without_exception(self):
        self.diagnostic(reason=[SECRET])
        obs = self.publish_failed()
        self.assertEqual(obs["observation"], "UNTRUSTED_PRIVATE_DIAGNOSTIC")

    def test_unrecognized_cli_exit_is_safely_omitted(self):
        self.diagnostic(cli_exit_category=SECRET)
        obs = self.publish_failed()
        self.assertEqual(obs["observation"], "SOURCE_BOUND_CI_FAILURE_CATEGORY")
        self.assertEqual(obs["cli_exit_category"], "NOT_AVAILABLE")

    def test_oversized_private_failure_never_copied_or_inspected(self):
        self.diagnostic(raw_stderr=SECRET * 10000)
        obs = self.publish_failed()
        self.assertEqual(obs["observation"], "UNTRUSTED_PRIVATE_DIAGNOSTIC")

    def test_malformed_or_duplicate_json_authority_rejected(self):
        path = self.root / "native-render-failure-diagnostic.json"
        path.write_text('{"reason":"trusted","reason":"'+SECRET+'"}')
        obs = self.publish_failed()
        self.assertEqual(obs["observation"], "UNTRUSTED_PRIVATE_DIAGNOSTIC")

    def test_cancelled_and_skipped_cannot_admit_private_media(self):
        for status in ("cancelled", "skipped"):
            with self.subTest(status=status):
                self.diagnostic()
                target = self.out.with_name(self.out.name + status)
                report = pub.publish(self.root, target, SOURCE, SDK, status)
                self.assertEqual(report["included_files"], [])
                self.assertEqual(report["ci_failure_observation"]["source_outcome"], "UNKNOWN")
                self.assertEqual({p.name for p in target.iterdir()},
                                 {"public-evidence.json"})

    def test_runner_writes_only_private_source_bound_failure_descriptor(self):
        base = Path(__file__).resolve().parents[2]
        src = (base / "tooling/native_render_e2e.py").read_text(encoding="utf8")
        workflow = (base / ".github/workflows/native-render-e2e.yml").read_text(
            encoding="utf8"
        )
        self.assertIn('EVIDENCE / "native-render-failure-diagnostic.json"', src)
        self.assertIn('"motionwright_source_sha": GITHUB_SHA', src)
        self.assertIn('"semwright_source_pin": PIN', src)
        self.assertIn("path: verification/native-render-public", workflow)
        self.assertNotIn("path: verification/native-render-e2e\n", workflow)

    def test_missing_private_dir_fails_closed(self):
        self.root.rmdir()
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.root, self.out, SOURCE, SDK, "failure")
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
