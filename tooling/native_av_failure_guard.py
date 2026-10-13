"""v0.1 safe, loss-aware diagnostic for a failed canonical native AV command.

Do not forward raw CLI stdout/stderr, argv, session ticket, native job_ref,
provider private path, or the application production journal to CI logs.
This helper NEVER converts an uncertain started native render into success.
"""
from __future__ import annotations

import json
from typing import Any

SCHEMA = "motionwright.v01-native-command-failure/1"
MAX_INPUT_BYTES = 256 * 1024
MAX_RECEIPTS = 4096
CODES = frozenset((
    "Unavailable", "Timeout", "ConsentRequired", "Conflict",
    "StaleReference", "ResourceExhausted", "NotFound", "Cancelled",
    "InvalidArguments", "Internal", "PermissionDenied", "Unknown",
))


class NativeCommandFailure(AssertionError):
    """Error with a closed, source-safe technical receipt, never raw output."""

    def __init__(self, diagnostic: dict[str, Any]) -> None:
        self.diagnostic = diagnostic
        super().__init__(
            "native AV command did not complete; safe_reason="
            + diagnostic["reason"]
            + " source_outcome=" + diagnostic["source_outcome"]
            + " (never automatically retry a mutating render)"
        )


def _known_code(value: Any) -> str:
    return value if isinstance(value, str) and value in CODES else "OtherOrMissing"


def _matches_generation_loss(value: Any) -> bool:
    """Interpret but never reproduce an untrusted provider message."""
    return isinstance(value, str) and "Provider capability generation is inactive" in value


def classify_native_command_failure(
    *, stdout: bytes, stderr: bytes, exit_code: int | None,
    deadline_exceeded: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "result": "FAIL",
        "reason": "command_deadline" if deadline_exceeded else "native_command_failed",
        "source_outcome": "UNKNOWN" if deadline_exceeded else "NOT_VERIFIED",
        "reported_error_code": "OtherOrMissing",
        "provider_generation_inactive_observed": False,
        "read_only_status_timeout_observed": False,
        "read_only_status_unavailable_observed": False,
        "original_render_start_receipts_observed": 0,
        "terminal_native_result_verified": False,
        "decoded_media_verified": False,
        "automatically_restarted_mutating_render": False,
        "raw_cli_output_in_diagnostic": False,
        "raw_argv_in_diagnostic": False,
        "owner_connection_disclosed": False,
        "native_job_references_disclosed": False,
        "requires_original_job_owner_reconciliation": True,
    }
    if deadline_exceeded:
        return report

    raw = stderr.strip() or stdout.strip()
    if not raw:
        report["reason"] = "empty_native_response"
        return report
    if len(raw) > MAX_INPUT_BYTES:
        report["reason"] = "oversized_native_failure_response"
        return report
    try:
        parsed = json.loads(raw)
    except (UnicodeError, ValueError, TypeError):
        report["reason"] = "malformed_native_failure_response"
        return report
    if not isinstance(parsed, dict):
        report["reason"] = "unexpected_native_failure_shape"
        return report

    error = parsed.get("error")
    if not isinstance(error, dict):
        report["reason"] = "typed_error_missing"
        return report
    code = _known_code(error.get("code"))
    report["reported_error_code"] = code
    if error.get("outcome_known") is False:
        report["source_outcome"] = "UNKNOWN"
    elif error.get("outcome_known") is True:
        report["source_outcome"] = "FAILED_NOT_MEDIA_VERIFIED"

    if _matches_generation_loss(error.get("message")):
        report["provider_generation_inactive_observed"] = True

    receipts = parsed.get("receipts")
    if isinstance(receipts, list) and len(receipts) <= MAX_RECEIPTS:
        for item in receipts:
            if not isinstance(item, dict):
                continue
            command = item.get("command")
            payload = item.get("payload")
            stage = item.get("stage")
            if not isinstance(payload, dict):
                continue
            observed = payload.get("error")
            receipt_error = observed if isinstance(observed, dict) else {}
            if command == "driver.motion-canvas.render.start" and stage == "completed":
                report["original_render_start_receipts_observed"] += 1
            if command == "driver.motion-canvas.render.status":
                if receipt_error.get("code") == "Timeout":
                    report["read_only_status_timeout_observed"] = True
                if receipt_error.get("code") == "Unavailable":
                    report["read_only_status_unavailable_observed"] = True
                if _matches_generation_loss(receipt_error.get("message")):
                    report["provider_generation_inactive_observed"] = True
    else:
        report["reason"] = "native_journal_missing_or_unbounded"

    if report["provider_generation_inactive_observed"]:
        report["reason"] = "provider_generation_inactive"
        # Provider resource loss following timeout does not prove what
        # occurred in the ORIGINAL mutating render.
        report["source_outcome"] = "UNKNOWN"
    elif code == "Timeout" or (
        report["read_only_status_timeout_observed"]
        and report["read_only_status_unavailable_observed"]
    ):
        report["reason"] = "native_status_unconfirmed"
        report["source_outcome"] = "UNKNOWN"
    return report
