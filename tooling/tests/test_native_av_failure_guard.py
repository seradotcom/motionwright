"""v0.1 synthetic *negative* tests for native renderer failure redaction.

These tests use private-looking fabricated text. They are not actual media
or a reproduced provider failure and cannot promote release acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_av_failure_guard import (
    NativeCommandFailure, classify_native_command_failure,
)
import native_av_master_e2e


PRIVATE = (
    "job_ref=mc1:original-private-provider:render-123 "
    "ticket=super-secret-credential /home/owner/private/video.mp4"
)


def failure_bytes() -> bytes:
    return json.dumps({
        "native_render_e2e": "FAIL",
        "error": {
            "code": "Unavailable",
            "outcome_known": False,
            "message": "Native render failed. " + PRIVATE,
        },
        "receipts": [
            {
                "command": "driver.motion-canvas.render.start",
                "stage": "completed",
                "payload": {"result": {"data": {"job_ref": PRIVATE}}},
            },
            {
                "command": "driver.motion-canvas.render.status",
                "stage": "outcome_unknown",
                "payload": {"error": {
                    "code": "Timeout",
                    "message": "Command timed out: " + PRIVATE,
                }},
            },
            {
                "command": "driver.motion-canvas.render.status",
                "stage": "failed_known",
                "payload": {"error": {
                    "code": "Unavailable",
                    "message": "Provider capability generation is inactive. " + PRIVATE,
                }},
            },
        ],
        "opaque_provider_session": PRIVATE,
        "user_output_paths": ["/home/owner/private/video.mp4"],
    }).encode()


class NativeAVFailureRedactionTests(unittest.TestCase):
    def assert_closed(self, report: dict) -> None:
        exposed = json.dumps(report, sort_keys=True)
        for private in (PRIVATE, "super-secret", "owner/private",
                        "render-123", "original-private-provider"):
            self.assertNotIn(private, exposed)
        self.assertEqual(report["result"], "FAIL")
        self.assertIs(report["terminal_native_result_verified"], False)
        self.assertIs(report["decoded_media_verified"], False)
        self.assertIs(report["automatically_restarted_mutating_render"], False)
        self.assertIs(report["raw_cli_output_in_diagnostic"], False)
        self.assertIs(report["raw_argv_in_diagnostic"], False)
        self.assertIs(report["owner_connection_disclosed"], False)
        self.assertIs(report["native_job_references_disclosed"], False)
        self.assertIs(report["requires_original_job_owner_reconciliation"], True)

    def test_real_failure_shape_classified_but_private_journal_never_exposed(self) -> None:
        report = classify_native_command_failure(
            stdout=b"", stderr=failure_bytes(), exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["reason"], "provider_generation_inactive")
        self.assertEqual(report["source_outcome"], "UNKNOWN")
        self.assertEqual(report["reported_error_code"], "Unavailable")
        self.assertEqual(report["original_render_start_receipts_observed"], 1)
        self.assertIs(report["read_only_status_timeout_observed"], True)
        self.assertIs(report["read_only_status_unavailable_observed"], True)
        self.assertIs(report["provider_generation_inactive_observed"], True)

    def test_command_failure_exception_never_prints_private_output_or_argv(self) -> None:
        command = ["/home/owner/private/render", PRIVATE]
        with patch.object(
            native_av_master_e2e.subprocess, "run",
            return_value=SimpleNamespace(
                returncode=1, stdout=b"", stderr=failure_bytes(),
            ),
        ):
            with self.assertRaises(NativeCommandFailure) as caught:
                native_av_master_e2e.run_json(
                    command, env={"SUPER_SECRET": PRIVATE})
        self.assert_closed(caught.exception.diagnostic)
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.assertNotIn("owner/private", str(caught.exception))
        self.assertNotIn("render-123", str(caught.exception))
        self.assertEqual(caught.exception.diagnostic["source_outcome"], "UNKNOWN")

    def test_timeout_never_prints_argv_or_declares_mutating_retry(self) -> None:
        with patch.object(
            native_av_master_e2e.subprocess, "run",
            side_effect=subprocess.TimeoutExpired([PRIVATE], 3),
        ):
            with self.assertRaises(NativeCommandFailure) as caught:
                native_av_master_e2e.run_json([PRIVATE], env={})
        self.assert_closed(caught.exception.diagnostic)
        self.assertEqual(caught.exception.diagnostic["reason"], "command_deadline")
        self.assertEqual(caught.exception.diagnostic["source_outcome"], "UNKNOWN")
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_malformed_native_json_fails_closed(self) -> None:
        report = classify_native_command_failure(
            stdout=b"", stderr=b"{ private paths not JSON " + PRIVATE.encode(),
            exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["reason"], "malformed_native_failure_response")

    def test_oversized_native_failure_is_never_truncated_and_forwarded(self) -> None:
        report = classify_native_command_failure(
            stdout=b"", stderr=PRIVATE.encode() * 10000, exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["reason"], "oversized_native_failure_response")

    def test_unknown_error_code_not_promoted_or_reproduced(self) -> None:
        raw = json.dumps({
            "error": {
                "code": PRIVATE,
                "outcome_known": False,
                "message": PRIVATE,
            },
            "receipts": [],
        }).encode()
        report = classify_native_command_failure(
            stdout=b"", stderr=raw, exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["reported_error_code"], "OtherOrMissing")
        self.assertEqual(report["source_outcome"], "UNKNOWN")

    def test_status_failure_without_proof_stays_unverified(self) -> None:
        raw = json.dumps({
            "error": {"code": "Unavailable", "outcome_known": True,
                      "message": PRIVATE},
            "receipts": [{"command": "driver.motion-canvas.render.status",
                          "stage": "failed_known",
                          "payload": {"error": {"code": "Unavailable",
                                               "message": PRIVATE}}}],
        }).encode()
        report = classify_native_command_failure(
            stdout=b"", stderr=raw, exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["source_outcome"], "FAILED_NOT_MEDIA_VERIFIED")
        self.assertFalse(report["provider_generation_inactive_observed"])


    def test_backend_failure_code_preserves_explicit_outcome_with_stderr_noise(self) -> None:
        for known, outcome in ((False, "UNKNOWN"), (True, "FAILED_NOT_MEDIA_VERIFIED")):
            with self.subTest(outcome_known=known):
                raw = json.dumps({
                    "error": {"code": "BackendFailed", "outcome_known": known,
                              "message": PRIVATE},
                    "receipts": [],
                }).encode()
                report = classify_native_command_failure(
                    stdout=raw, stderr=("Error: " + PRIVATE).encode(), exit_code=1)
                self.assert_closed(report)
                self.assertEqual(report["reported_error_code"], "BackendFailed")
                self.assertEqual(report["source_outcome"], outcome)
                self.assertEqual(report["original_render_start_receipts_observed"], 0)

    def test_pinned_protocol_error_categories_remain_closed(self) -> None:
        # These spellings belong to the pinned Semwright ErrorCode wire enum.
        codes = (
            "Unsupported", "Unavailable", "PermissionDenied",
            "ConsentRequired", "PolicyDenied", "NotFound",
            "AmbiguousTarget", "StaleReference", "BackendFailed",
            "Timeout", "InvalidArgument", "PluginProtocolError",
            "SandboxDenied", "Conflict", "Cancelled",
            "Internal", "ProtocolMismatch", "ResourceExhausted",
        )
        for code in codes:
            with self.subTest(code=code):
                raw = json.dumps({
                    "error": {"code": code, "outcome_known": False, "message": PRIVATE},
                    "receipts": [],
                }).encode()
                report = classify_native_command_failure(
                    stdout=raw, stderr=b"", exit_code=1)
                self.assert_closed(report)
                self.assertEqual(report["reported_error_code"], code)
                self.assertEqual(report["source_outcome"], "UNKNOWN")
        for code in ("InvalidArguments", "Unknown", "backendfailed", PRIVATE, None, 17):
            with self.subTest(non_protocol_code=code):
                raw = json.dumps({
                    "error": {"code": code, "outcome_known": False, "message": PRIVATE},
                    "receipts": [],
                }).encode()
                report = classify_native_command_failure(
                    stdout=raw, stderr=b"", exit_code=1)
                self.assert_closed(report)
                self.assertEqual(report["reported_error_code"], "OtherOrMissing")
                self.assertEqual(report["source_outcome"], "UNKNOWN")

    def test_json_embedded_in_rust_diagnostics_does_not_become_authority(self) -> None:
        raw = json.dumps({
            "error": {"code": "BackendFailed", "outcome_known": True, "message": PRIVATE},
            "receipts": [],
        }).encode()
        report = classify_native_command_failure(
            stdout=b"", stderr=raw + b"\nError: " + PRIVATE.encode(), exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["reason"], "malformed_native_failure_response")
        self.assertEqual(report["reported_error_code"], "OtherOrMissing")
        self.assertEqual(report["source_outcome"], "NOT_VERIFIED")

    def test_typed_stdout_never_overrides_uncertain_secondary_error(self) -> None:
        def channel(known: bool) -> bytes:
            return json.dumps({
                "error": {"code": "BackendFailed", "outcome_known": known,
                          "message": PRIVATE},
                "receipts": [],
            }).encode()
        report = classify_native_command_failure(
            stdout=channel(True), stderr=channel(False), exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["reported_error_code"], "BackendFailed")
        self.assertEqual(report["source_outcome"], "UNKNOWN")

    def test_successful_json_command_unchanged(self) -> None:
        with patch.object(native_av_master_e2e.subprocess, "run",
                          return_value=SimpleNamespace(
                              returncode=0, stdout=b'{"ok":true}', stderr=b"",
                          )):
            self.assertEqual(
                native_av_master_e2e.run_json(["safe-cli"], env={}),
                {"ok": True},
            )

    def test_bad_success_json_never_echoes_private_output(self) -> None:
        with patch.object(native_av_master_e2e.subprocess, "run",
                          return_value=SimpleNamespace(
                              returncode=0, stdout=PRIVATE.encode(),
                              stderr=b"",
                          )):
            with self.assertRaises(NativeCommandFailure) as caught:
                native_av_master_e2e.run_json([PRIVATE], env={})
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.assert_closed(caught.exception.diagnostic)


if __name__ == "__main__":
    unittest.main()
