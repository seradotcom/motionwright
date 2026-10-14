"""v0.1 safe, loss-aware diagnostic for a failed canonical native AV command.

Do not forward raw CLI stdout/stderr, argv, session ticket, native job_ref,
provider private path, or the application production journal to CI logs.
This helper NEVER converts an uncertain started native render into success.
"""
from __future__ import annotations

import json
import math
import subprocess
from typing import Any

SCHEMA = "motionwright.v01-native-command-failure/1"
MAX_INPUT_BYTES = 256 * 1024
MAX_SUCCESS_BYTES = 1024 * 1024
MAX_RECEIPTS = 4096
CODES = frozenset((
    "Unavailable", "Timeout", "ConsentRequired", "Conflict",
    "StaleReference", "ResourceExhausted", "NotFound", "Cancelled",
    "InvalidArguments", "Internal", "PermissionDenied", "Unknown",
))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("ambiguous native JSON object")
        result[name] = value
    return result


def _reject_nonfinite(_value: str) -> Any:
    raise ValueError("nonfinite native JSON constant")


def _finite_float(raw: str) -> float:
    result = float(raw)
    if not math.isfinite(result):
        raise ValueError("unrepresentable native JSON number")
    return result


def _strict_native_json(raw: bytes) -> Any:
    return json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_nonfinite,
        parse_float=_finite_float,
    )


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


def run_sanitized_json(
    argv: list[str], *, env: dict[str, str], timeout: int = 30,
) -> dict[str, Any]:
    """Canonical subprocess JSON contract, no private stderr/argv exceptions.

    Used by CI-only native render wrappers. Always refuse a failed or malformed
    command; never issue an automatic second native operation.
    """
    try:
        result = subprocess.run(
            argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise NativeCommandFailure(classify_native_command_failure(
            stdout=b"", stderr=b"", exit_code=None, deadline_exceeded=True,
        )) from None
    except OSError:
        raise NativeCommandFailure(classify_native_command_failure(
            stdout=b"", stderr=b"", exit_code=None,
        )) from None

    if result.returncode != 0:
        raise NativeCommandFailure(classify_native_command_failure(
            stdout=result.stdout, stderr=result.stderr,
            exit_code=result.returncode,
        )) from None
    # Exit code 0 is not a trustworthy completion receipt by itself.
    # Cap bytes and reject duplicate keys, NaN/Infinity or overflowed floats
    # *before* any caller can interpret a native success/result status.
    oversized = isinstance(result.stdout, bytes) and len(result.stdout) > MAX_SUCCESS_BYTES
    try:
        parsed = _strict_native_json(result.stdout) if not oversized else None
    except (ValueError, UnicodeError, TypeError):
        parsed = None
    if not isinstance(parsed, dict):
        diagnostic = classify_native_command_failure(
            stdout=b"", stderr=b"invalid-native-response",
            exit_code=result.returncode,
        )
        diagnostic["reason"] = (
            "oversized_native_success_response" if oversized
            else "malformed_native_success_response"
        )
        # The process may have performed the original native mutation.
        # No new grant, retry or result interpretation is authorized.
        diagnostic["source_outcome"] = "UNKNOWN"
        raise NativeCommandFailure(diagnostic) from None
    return parsed


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

    # A native executable may write a canonical typed JSON failure to
    # stdout while its stderr contains unrelated diagnostic noise. Inspect
    # both bounded streams in-memory; never log either raw channel.
    raw_streams = (stdout.strip(), stderr.strip())
    if not any(raw_streams):
        report["reason"] = "empty_native_response"
        return report
    if any(len(raw) > MAX_INPUT_BYTES for raw in raw_streams):
        report["reason"] = "oversized_native_failure_response"
        return report

    typed: list[dict[str, Any]] = []
    untyped_object = False
    unexpected_shape = False
    for raw in raw_streams:
        if not raw:
            continue
        try:
            candidate = _strict_native_json(raw)
        except (UnicodeError, ValueError, TypeError):
            continue
        if isinstance(candidate, dict):
            if isinstance(candidate.get("error"), dict):
                typed.append(candidate)
            else:
                untyped_object = True
        else:
            unexpected_shape = True
    if not typed:
        report["reason"] = (
            "typed_error_missing" if untyped_object
            else "unexpected_native_failure_shape" if unexpected_shape
            else "malformed_native_failure_response"
        )
        return report

    # The canonical application JSON stdout wins when both streams contain
    # typed errors. Later stderr errors can only make outcome *less* certain.
    parsed = typed[0]
    error = parsed["error"]
    code = _known_code(error.get("code"))
    report["reported_error_code"] = code
    if error.get("outcome_known") is False:
        report["source_outcome"] = "UNKNOWN"
    elif error.get("outcome_known") is True:
        report["source_outcome"] = "FAILED_NOT_MEDIA_VERIFIED"

    any_typed_timeout = False
    has_bounded_journal = False
    for envelope in typed:
        observed_error = envelope["error"]
        if observed_error.get("outcome_known") is False:
            # A secondary typed stream may only weaken certainty, never
            # turn the already-started native operation into known failure.
            report["source_outcome"] = "UNKNOWN"
        if observed_error.get("code") == "Timeout":
            any_typed_timeout = True
        if _matches_generation_loss(observed_error.get("message")):
            report["provider_generation_inactive_observed"] = True

        receipts = envelope.get("receipts")
        if not isinstance(receipts, list) or len(receipts) > MAX_RECEIPTS:
            continue
        has_bounded_journal = True
        starts_in_envelope = 0
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
                starts_in_envelope += 1
            if command == "driver.motion-canvas.render.status":
                if receipt_error.get("code") == "Timeout":
                    report["read_only_status_timeout_observed"] = True
                if receipt_error.get("code") == "Unavailable":
                    report["read_only_status_unavailable_observed"] = True
                if _matches_generation_loss(receipt_error.get("message")):
                    report["provider_generation_inactive_observed"] = True
        # Different streams can describe the same durable request history.
        # Never double count original native mutation acknowledgements.
        report["original_render_start_receipts_observed"] = max(
            report["original_render_start_receipts_observed"], starts_in_envelope
        )
    if not has_bounded_journal:
        report["reason"] = "native_journal_missing_or_unbounded"

    if report["provider_generation_inactive_observed"]:
        report["reason"] = "provider_generation_inactive"
        # Provider resource loss following timeout does not prove what
        # occurred in the ORIGINAL mutating render.
        report["source_outcome"] = "UNKNOWN"
    elif any_typed_timeout or (
        report["read_only_status_timeout_observed"]
        and report["read_only_status_unavailable_observed"]
    ):
        report["reason"] = "native_status_unconfirmed"
        report["source_outcome"] = "UNKNOWN"
    return report
