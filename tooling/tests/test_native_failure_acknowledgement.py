"""v0.1 provider-loss evidence regression: no false job certainty or retries.

Fixtures are exclusively synthetic native JSON; no live Broker, render.start,
owner job/session, user data, or human acceptance is exercised.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_av_failure_guard import classify_native_command_failure


PRIVATE = "owner-secret-native-request-render-9a0"


def start(request_id=None, *, revision=1, stage="completed", marker=""):
    row = {
        "command": "driver.motion-canvas.render.start",
        "stage": stage,
        "payload": {"result": {"data": {"opaque": "not for CI " + PRIVATE}}},
        "id": "append-record-" + marker,
        "generation": "synthetic-generation",
        "revision": revision,
        "project_id": "synthetic-project",
    }
    if request_id is not None:
        row["request_id"] = request_id
    return row


def envelope(*receipts, error=None):
    data = {
        "native_render_e2e": "FAIL",
        "error": error or {
            "code": "PermissionDenied", "outcome_known": True,
            "message": "Known terminal result of wrapper/status, NOT of started render " + PRIVATE,
        },
        "receipts": list(receipts),
        "untrusted_native_refs": PRIVATE,
    }
    return json.dumps(data).encode()


def classified(stdout=b"", stderr=b"", *, exit_code=1):
    return classify_native_command_failure(
        stdout=stdout, stderr=stderr, exit_code=exit_code,
    )


class AcknowledgedNativeOutcomeTests(unittest.TestCase):
    def check_closed(self, report):
        payload = json.dumps(report)
        self.assertNotIn(PRIVATE, payload)
        self.assertNotIn("append-record-", payload)
        self.assertEqual(report["result"], "FAIL")
        self.assertFalse(report["terminal_native_result_verified"])
        self.assertFalse(report["decoded_media_verified"])
        self.assertFalse(report["automatically_restarted_mutating_render"])
        self.assertTrue(report["requires_original_job_owner_reconciliation"])

    def test_known_wrapper_failure_does_not_prove_started_render_failed(self):
        report = classified(stdout=envelope(start("started-original")))
        self.check_closed(report)
        self.assertEqual(report["source_outcome"], "UNKNOWN")
        self.assertEqual(report["reason"], "acknowledged_render_terminal_unverified")
        self.assertEqual(report["reported_error_code"], "PermissionDenied")
        self.assertEqual(report["original_render_start_receipts_observed"], 1)
        self.assertFalse(report["original_render_start_identity_ambiguous"])

    def test_duplicate_completed_stage_records_count_as_one_acknowledged_request(self):
        report = classified(stderr=envelope(
            start(PRIVATE, marker="a"),
            start(PRIVATE, marker="b"),
            start(PRIVATE, marker="c"),
        ))
        self.check_closed(report)
        self.assertEqual(report["original_render_start_receipts_observed"], 1)
        self.assertFalse(report["original_render_start_identity_ambiguous"])
        self.assertEqual(report["source_outcome"], "UNKNOWN")

    def test_separately_identified_mutations_are_not_collapsed(self):
        report = classified(stderr=envelope(
            start(PRIVATE, marker="a"),
            start("other-distinct-request", marker="b"),
        ))
        self.check_closed(report)
        self.assertEqual(report["original_render_start_receipts_observed"], 2)
        self.assertFalse(report["original_render_start_identity_ambiguous"])

    def test_same_request_id_with_different_creative_revision_is_separate(self):
        report = classified(stderr=envelope(
            start(PRIVATE, revision=1, marker="a"),
            start(PRIVATE, revision=2, marker="b"),
        ))
        self.check_closed(report)
        self.assertEqual(report["original_render_start_receipts_observed"], 2)

    def test_anonymous_repeated_completed_start_is_only_one_ambiguous_bucket(self):
        report = classified(stderr=envelope(
            start(marker="a"), start(marker="b"), start(marker="c"),
        ))
        self.check_closed(report)
        self.assertEqual(report["original_render_start_receipts_observed"], 1)
        self.assertTrue(report["original_render_start_identity_ambiguous"])
        self.assertEqual(report["source_outcome"], "UNKNOWN")

    def test_anonymous_stage_may_duplicate_known_request_no_extra_job_claim(self):
        report = classified(stdout=envelope(
            start(PRIVATE, marker="known"),
            start(marker="unidentified-may-be-the-same"),
        ))
        self.check_closed(report)
        self.assertEqual(report["original_render_start_receipts_observed"], 1)
        self.assertTrue(report["original_render_start_identity_ambiguous"])
        self.assertEqual(report["source_outcome"], "UNKNOWN")

    def test_multiple_stdout_and_stderr_copies_not_summed(self):
        row = envelope(start(PRIVATE), start("new-other"))
        report = classified(stdout=row, stderr=row)
        self.check_closed(report)
        self.assertEqual(report["original_render_start_receipts_observed"], 2)

    def test_noncompleted_start_is_not_a_proven_ack(self):
        report = classified(stdout=envelope(start(PRIVATE, stage="failed_known")))
        self.check_closed(report)
        self.assertEqual(report["source_outcome"], "FAILED_NOT_MEDIA_VERIFIED")
        self.assertEqual(report["original_render_start_receipts_observed"], 0)
        self.assertFalse(report["original_render_start_identity_ambiguous"])

    def test_other_provider_or_status_receipt_cannot_be_a_start(self):
        other = start(PRIVATE)
        other["command"] = "driver.mlt-video.render.start"
        status = start(PRIVATE)
        status["command"] = "driver.motion-canvas.render.status"
        report = classified(stderr=envelope(other, status))
        self.check_closed(report)
        self.assertEqual(report["original_render_start_receipts_observed"], 0)

    def test_original_unknown_dominates_conflicting_known_second_stream(self):
        known = envelope(start(PRIVATE))
        uncertain = envelope(error={
            "code": "Timeout", "outcome_known": False, "message": PRIVATE,
        })
        report = classified(stdout=known, stderr=uncertain)
        self.check_closed(report)
        self.assertEqual(report["source_outcome"], "UNKNOWN")
        self.assertEqual(report["reason"], "native_status_unconfirmed")
        self.assertEqual(report["original_render_start_receipts_observed"], 1)

    def test_bounded_malformed_failing_stream_stays_not_verified_without_raw_data(self):
        report = classified(stdout=b"{ not-a-valid-native-response " + PRIVATE.encode())
        self.check_closed(report)
        self.assertEqual(report["reason"], "malformed_native_failure_response")
        self.assertEqual(report["original_render_start_receipts_observed"], 0)
        self.assertEqual(report["source_outcome"], "NOT_VERIFIED")

    def test_typed_failure_with_empty_bounded_journal_has_no_verified_start(self):
        report = classified(stdout=envelope())
        self.check_closed(report)
        self.assertEqual(report["source_outcome"], "FAILED_NOT_MEDIA_VERIFIED")
        self.assertEqual(report["original_render_start_receipts_observed"], 0)
        self.assertFalse(report["original_render_start_identity_ambiguous"])


    def test_cli_sigkill_is_a_safe_category_not_oom_proof(self):
        report = classified(
            stdout=b"truncated CLI failure text " + PRIVATE.encode(),
            exit_code=-9,
        )
        self.check_closed(report)
        self.assertEqual(report["cli_exit_category"], "signal")
        self.assertEqual(report["cli_signal_observed"], "SIGKILL")
        self.assertFalse(report["cli_signal_proves_oom_or_provider_loss"])
        self.assertEqual(report["source_outcome"], "UNKNOWN")
        self.assertEqual(report["reason"], "malformed_native_failure_response")

    def test_cli_sigsegv_cannot_upgrade_typed_wrapper_known_to_render_known(self):
        report = classified(
            stderr=envelope(), exit_code=-11,
        )
        self.check_closed(report)
        self.assertEqual(report["cli_exit_category"], "signal")
        self.assertEqual(report["cli_signal_observed"], "SIGSEGV")
        self.assertFalse(report["cli_signal_proves_oom_or_provider_loss"])
        self.assertEqual(report["source_outcome"], "UNKNOWN")
        self.assertEqual(report["reason"], "cli_exit_without_original_terminal")

    def test_spawn_without_exit_status_is_unobserved_and_unknown(self):
        report = classified(exit_code=None)
        self.check_closed(report)
        self.assertEqual(report["cli_exit_category"], "not_observed")
        self.assertEqual(report["cli_signal_observed"], "NONE")
        self.assertEqual(report["source_outcome"], "UNKNOWN")

    def test_plain_nonzero_exit_does_not_imply_a_provider_signal(self):
        report = classified(stderr=envelope(), exit_code=1)
        self.check_closed(report)
        self.assertEqual(report["cli_exit_category"], "nonzero_exit")
        self.assertEqual(report["cli_signal_observed"], "NONE")
        self.assertEqual(report["source_outcome"], "FAILED_NOT_MEDIA_VERIFIED")

    def test_zero_exit_with_malformed_success_is_not_signal_or_success(self):
        report = classified(stdout=b"private invalid CLI output " + PRIVATE.encode(),
                            exit_code=0)
        self.check_closed(report)
        self.assertEqual(report["cli_exit_category"], "zero_exit_invalid_protocol")
        self.assertEqual(report["source_outcome"], "NOT_VERIFIED")
        self.assertEqual(report["cli_signal_observed"], "NONE")

    def test_deadline_without_observed_exit_is_distinct_from_signal(self):
        report = classify_native_command_failure(
            stdout=b"", stderr=b"", exit_code=None, deadline_exceeded=True,
        )
        self.check_closed(report)
        self.assertEqual(report["cli_exit_category"], "deadline")
        self.assertEqual(report["cli_signal_observed"], "NONE")
        self.assertEqual(report["source_outcome"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
