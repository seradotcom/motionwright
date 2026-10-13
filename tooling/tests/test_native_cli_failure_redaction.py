"""v0.1 secret-safe CI native wrappers; synthetic tests, not real media proof."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_av_failure_guard import NativeCommandFailure, run_sanitized_json

# AV is included to prevent a second, divergent native failure parser.
MODULE_NAMES = (
    "native_av_master_e2e",
    "native_render_e2e",
    "native_mlt_sequence_e2e",
    "native_host_shared_state",
)
SECRET = "job_ref=mc1:super-secret-private-job /home/owner/media/private.mp4 session=not-public"


def invoke(module: str) -> dict:
    return importlib.import_module(module).run_json(
        ["/private/semwright-binary", SECRET], env={"PRIVATE_SESSION": SECRET},
        timeout=2,
    )


class NativeCliErrorSanitization(unittest.TestCase):
    def assert_closed(self, raised: NativeCommandFailure) -> None:
        diagnostic = raised.diagnostic
        serialized = json.dumps(diagnostic, sort_keys=True)
        message = str(raised)
        for forbidden in (
            SECRET, "super-secret-private-job", "owner/media", "session=not-public",
            "/private/semwright-binary",
        ):
            self.assertNotIn(forbidden, serialized)
            self.assertNotIn(forbidden, message)
        self.assertFalse(diagnostic["terminal_native_result_verified"])
        self.assertFalse(diagnostic["decoded_media_verified"])
        self.assertFalse(diagnostic["automatically_restarted_mutating_render"])
        self.assertTrue(diagnostic["requires_original_job_owner_reconciliation"])

    def test_every_native_wrapper_rejects_private_cli_failure_without_retry(self) -> None:
        private_error = json.dumps({
            "error": {"code": "Unavailable", "outcome_known": False,
                      "message": "Provider capability generation is inactive " + SECRET},
            "receipts": [{"command": "driver.motion-canvas.render.status",
                          "stage": "outcome_unknown", "payload": {
                              "error": {"code": "Timeout", "message": SECRET},
                              "job_ref": SECRET,
                          }}],
            "connection": SECRET,
        }).encode()
        for module in MODULE_NAMES:
            with self.subTest(module=module):
                with patch("native_av_failure_guard.subprocess.run",
                           return_value=SimpleNamespace(returncode=1,
                                                        stdout=b"", stderr=private_error)) as spy:
                    with self.assertRaises(NativeCommandFailure) as ctx:
                        invoke(module)
                self.assertEqual(spy.call_count, 1)
                self.assert_closed(ctx.exception)
                self.assertEqual(ctx.exception.diagnostic["reason"],
                                 "provider_generation_inactive")
                self.assertEqual(ctx.exception.diagnostic["source_outcome"], "UNKNOWN")

    def test_every_native_wrapper_rejects_malformed_success_without_stdout_leak(self) -> None:
        for module in MODULE_NAMES:
            with self.subTest(module=module):
                with patch("native_av_failure_guard.subprocess.run",
                           return_value=SimpleNamespace(returncode=0,
                                                        stdout=SECRET.encode(), stderr=b"")) as spy:
                    with self.assertRaises(NativeCommandFailure) as ctx:
                        invoke(module)
                self.assertEqual(spy.call_count, 1)
                self.assert_closed(ctx.exception)

    def test_every_native_wrapper_preserves_valid_success(self) -> None:
        for module in MODULE_NAMES:
            with self.subTest(module=module):
                response = {"ok": True, "value": {"frame_count": 60}}
                with patch("native_av_failure_guard.subprocess.run",
                           return_value=SimpleNamespace(
                               returncode=0, stdout=json.dumps(response).encode(), stderr=b"")) as spy:
                    self.assertEqual(invoke(module), response)
                self.assertEqual(spy.call_count, 1)

    def test_deadline_cannot_echo_private_argv_or_issue_duplicate_render(self) -> None:
        for module in MODULE_NAMES:
            with self.subTest(module=module):
                with patch("native_av_failure_guard.subprocess.run",
                           side_effect=subprocess.TimeoutExpired([SECRET], 2)) as spy:
                    with self.assertRaises(NativeCommandFailure) as ctx:
                        invoke(module)
                self.assertEqual(spy.call_count, 1)
                self.assert_closed(ctx.exception)
                self.assertEqual(ctx.exception.diagnostic["source_outcome"], "UNKNOWN")

    def test_spawn_error_is_sanitized_and_no_fallback_command(self) -> None:
        with patch("native_av_failure_guard.subprocess.run",
                   side_effect=OSError(SECRET)) as spy:
            with self.assertRaises(NativeCommandFailure) as ctx:
                run_sanitized_json([SECRET], env={}, timeout=2)
        self.assertEqual(spy.call_count, 1)
        self.assert_closed(ctx.exception)
        self.assertEqual(ctx.exception.diagnostic["reason"], "empty_native_response")

    def test_json_array_does_not_become_successful_native_execution_envelope(self) -> None:
        with patch("native_av_failure_guard.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout=b'["fake-result"]',
                                                stderr=b"")):
            with self.assertRaises(NativeCommandFailure) as ctx:
                run_sanitized_json([SECRET], env={}, timeout=2)
        self.assert_closed(ctx.exception)


class MixedNativeResponseStreamTests(unittest.TestCase):
    def test_typed_stdout_error_survives_noisy_secret_bearing_stderr(self) -> None:
        typed = json.dumps({
            "error": {
                "code": "Unavailable", "outcome_known": False,
                "message": "Provider capability generation is inactive " + SECRET,
            },
            "receipts": [{
                "command": "driver.motion-canvas.render.start",
                "stage": "completed", "payload": {},
            }],
        }).encode()
        with patch("native_av_failure_guard.subprocess.run",
                   return_value=SimpleNamespace(returncode=1, stdout=typed,
                                                stderr=b"runtime warning: " + SECRET.encode())) as spy:
            with self.assertRaises(NativeCommandFailure) as captured:
                run_sanitized_json([SECRET], env={"PRIVATE": SECRET})
        report = captured.exception.diagnostic
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(report["reason"], "provider_generation_inactive")
        self.assertEqual(report["source_outcome"], "UNKNOWN")
        self.assertEqual(report["reported_error_code"], "Unavailable")
        self.assertEqual(report["original_render_start_receipts_observed"], 1)
        self.assertNotIn(SECRET, json.dumps(report) + str(captured.exception))

    def test_typed_stderr_used_when_stdout_is_unstructured(self) -> None:
        typed = json.dumps({
            "error": {"code": "Timeout", "outcome_known": False,
                      "message": SECRET},
            "receipts": [],
        }).encode()
        with patch("native_av_failure_guard.subprocess.run",
                   return_value=SimpleNamespace(returncode=1,
                                                stdout=b"stray native logging " + SECRET.encode(),
                                                stderr=typed)):
            with self.assertRaises(NativeCommandFailure) as captured:
                run_sanitized_json([SECRET], env={})
        report = captured.exception.diagnostic
        self.assertEqual(report["reported_error_code"], "Timeout")
        self.assertEqual(report["reason"], "native_status_unconfirmed")
        self.assertEqual(report["source_outcome"], "UNKNOWN")
        self.assertNotIn(SECRET, json.dumps(report) + str(captured.exception))

    def test_conflicting_bounded_typed_streams_cannot_upgrade_unknown_to_known(self) -> None:
        stdout = json.dumps({
            "error": {"code": "PermissionDenied", "outcome_known": True,
                      "message": SECRET},
            "receipts": [],
        }).encode()
        stderr = json.dumps({
            "error": {"code": "Unavailable", "outcome_known": False,
                      "message": "Provider capability generation is inactive " + SECRET},
            "receipts": [{
                "command": "driver.motion-canvas.render.start",
                "stage": "completed", "payload": {},
            }],
        }).encode()
        with patch("native_av_failure_guard.subprocess.run",
                   return_value=SimpleNamespace(returncode=1,
                                                stdout=stdout, stderr=stderr)):
            with self.assertRaises(NativeCommandFailure) as captured:
                run_sanitized_json([SECRET], env={})
        report = captured.exception.diagnostic
        self.assertEqual(report["reported_error_code"], "PermissionDenied")
        self.assertEqual(report["source_outcome"], "UNKNOWN")
        self.assertEqual(report["reason"], "provider_generation_inactive")
        self.assertEqual(report["original_render_start_receipts_observed"], 1)
        self.assertNotIn(SECRET, json.dumps(report) + str(captured.exception))

    def test_oversized_secondary_stream_cannot_be_published_or_ignored(self) -> None:
        typed = json.dumps({
            "error": {"code": "Unavailable", "outcome_known": False},
            "receipts": [],
        }).encode()
        noisy = SECRET.encode() * 10000
        with patch("native_av_failure_guard.subprocess.run",
                   return_value=SimpleNamespace(returncode=1,
                                                stdout=typed, stderr=noisy)):
            with self.assertRaises(NativeCommandFailure) as captured:
                run_sanitized_json([SECRET], env={})
        report = captured.exception.diagnostic
        self.assertEqual(report["reason"], "oversized_native_failure_response")
        self.assertFalse(report["terminal_native_result_verified"])
        self.assertNotIn(SECRET, json.dumps(report) + str(captured.exception))

    def test_nonzero_with_success_stdout_can_never_be_reported_as_success(self) -> None:
        stdout = b'{"ok":true,"data":{"artifact":{"verified":true}}}'
        with patch("native_av_failure_guard.subprocess.run",
                   return_value=SimpleNamespace(returncode=1, stdout=stdout, stderr=b"")):
            with self.assertRaises(NativeCommandFailure) as captured:
                run_sanitized_json([SECRET], env={})
        report = captured.exception.diagnostic
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["reason"], "typed_error_missing")
        self.assertFalse(report["decoded_media_verified"])


if __name__ == "__main__":
    unittest.main()
