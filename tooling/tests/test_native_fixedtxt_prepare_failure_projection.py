"""Redaction-only G2 inner-prepare failure observation, without Native effects."""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import native_fixedtxt_prepare_failure_projection as projection

SOURCE = (pathlib.Path(__file__).resolve().parents[1] /
          "native_mlt_g2_custody.py").read_bytes()
PHASES = projection.literal_frozenset(SOURCE, "_PREPARE_PHASES")
CODES = projection.literal_frozenset(SOURCE, "_PREPARE_SAFE_CODES")


def safe_line(phase="runtime_artifact", kind="CustodyRejected",
              code="artifact_API_failed_no_retry", **changes):
    record = {
        "phase": phase, "error_class": kind, "error_code": code,
        "automatic_retry": False, "originals_disclosed": False,
    }
    record.update(changes)
    return (b"MLT G2 custody gate failed; no automatic retry. " +
            json.dumps(record, separators=(",", ":")).encode() + b"\n")


class OriginalPrepareFailureProjectionTests(unittest.TestCase):
    def test_original_literals_exactly_source_bound(self):
        self.assertGreaterEqual(len(PHASES), 10)
        self.assertGreaterEqual(len(CODES), 70)
        self.assertIn("compiler_source_reconstruction", PHASES)
        self.assertIn("artifact_API_failed_no_retry", CODES)
        self.assertIn("source-preflight-commit", CODES)

    def test_valid_selected_final_line_emits_only_safe_fields(self):
        raw = b"Private context preceding the original terminal.\n" + safe_line()
        result = projection.safe_record(raw, PHASES, CODES)
        self.assertEqual(result, {
            "status": "SOURCE_BOUND_SAFE_PREPARE_FAILURE",
            "phase": "runtime_artifact", "error_class": "CustodyRejected",
            "error_code": "artifact_API_failed_no_retry",
            "native_outcome": "NOT_INFERRED",
            "automatic_retry": False, "originals_disclosed": False,
        })
        self.assertNotIn("Private context", json.dumps(result))

    def test_compiler_original_source_code_is_allowed(self):
        row = projection.safe_record(
            safe_line(phase="compiler_source_closure",
                      code="compiler_receipt_bytes_not_authenticated"),
            PHASES, CODES)
        self.assertEqual(row["phase"], "compiler_source_closure")
        self.assertEqual(row["error_code"],
                         "compiler_receipt_bytes_not_authenticated")

    def test_arbitrary_secret_canary_never_accepted_as_code(self):
        private = "MOTIONWRIGHT_PRIVATE_KEY_CANARY_PEM"
        with self.assertRaisesRegex(ValueError,
                                    "safe_projection_enum_or_contract_invalid"):
            projection.safe_record(safe_line(code=private), PHASES, CODES)

    def test_never_accept_synthetic_or_unknown_phase(self):
        with self.assertRaisesRegex(ValueError,
                                    "safe_projection_enum_or_contract_invalid"):
            projection.safe_record(
                safe_line(phase="private/attacker/path"), PHASES, CODES)

    def test_forged_class_and_extra_json_field_rejected(self):
        for raw in (safe_line(kind="SECRET_CANARY"),
                    safe_line(private_key="SENSITIVE")):
            with self.subTest(raw=raw[:60]), self.assertRaisesRegex(
                    ValueError, "safe_projection_enum_or_contract_invalid"):
                projection.safe_record(raw, PHASES, CODES)

    def test_boolean_contract_and_retry_are_never_bypassed(self):
        for raw in (safe_line(automatic_retry=True),
                    safe_line(originals_disclosed=True)):
            with self.subTest(raw=raw[:60]), self.assertRaisesRegex(
                    ValueError, "safe_projection_enum_or_contract_invalid"):
                projection.safe_record(raw, PHASES, CODES)

    def test_error_code_withheld_can_be_emitted_without_raw_payload(self):
        row = projection.safe_record(
            b"SECRET-RAW-REASON\n" + safe_line(code="WITHHELD"),
            PHASES, CODES)
        self.assertEqual(row["error_code"], "WITHHELD")
        self.assertNotIn("SECRET", json.dumps(row))

    def test_duplicate_marker_and_marker_not_at_end_rejected(self):
        for raw in (
            safe_line() + safe_line(),
            safe_line() + b"last private line\n",
        ):
            with self.subTest(length=len(raw)), self.assertRaisesRegex(
                    ValueError, "no_unique_final_safe_diagnostic"):
                projection.safe_record(raw, PHASES, CODES)

    def test_json_duplicate_keys_rejected(self):
        raw = (b"MLT G2 custody gate failed; no automatic retry. "
               b'{"phase":"runtime_artifact","phase":"compiler_members",'
               b'"error_class":"CustodyRejected","error_code":"WITHHELD",'
               b'"automatic_retry":false,"originals_disclosed":false}\n')
        with self.assertRaisesRegex(ValueError,
                                    "duplicate_safe_projection_field"):
            projection.safe_record(raw, PHASES, CODES)

    def test_truncated_or_multiline_payload_is_not_a_diagnostic(self):
        for raw in (safe_line()[:-1], b"",
                    b"MLT G2 custody gate failed; no automatic retry. {}\r\n",
                    b"MLT G2 custody gate failed; no automatic retry. {}\n"):
            with self.subTest(raw=raw[-20:]), self.assertRaises(ValueError):
                projection.safe_record(raw, PHASES, CODES)

    def test_source_enum_loader_does_not_execute_python(self):
        malicious = (
            b"_PREPARE_PHASES = frozenset({__import__('os').getcwd()})\n")
        with self.assertRaisesRegex(ValueError,
                                    "source_enum_values_invalid"):
            projection.literal_frozenset(malicious, "_PREPARE_PHASES")

    def test_original_reader_does_not_print_secrets_from_raw_log(self):
        # The parser returns only one enum-bound object; it never logs raw bytes.
        canary = "SECRET-CANARY-IN-ORIGINAL-STDERR"
        row = projection.safe_record(
            (canary + "\n").encode() + safe_line(), PHASES, CODES)
        self.assertNotIn(canary, json.dumps(row, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
