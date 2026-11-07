"""Source controls only; these tests grant no Native or G2 credit."""
from __future__ import annotations
import base64
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_private_evidence as engine
import native_manim_retained_reference as retained
import native_manim_resume_transfer as transfer
from native_manim_resume_cycle import checked_receipt_classification
CURRENT = {"source_sha": "a" * 40, "semwright_sha": transfer.SDK, "run_id": "12345", "run_attempt": "1"}
SELECTED = {"MANIM_NATIVE_SCENARIO": transfer.RECOVERY_SCENARIO, retained.ENVIRONMENT: retained.CONTINUATION}


class RetainedReferenceContractTests(unittest.TestCase):
    def test_runtime_change_is_rejected_before_new_native_without_promoting_postcheck(self):
        original = {"schema": "motionwright.manim.runtime-inventory/2", "context": retained.ORIGIN,
                    "native_render_performed": False, "binaries": {"python": {"sha256": "a" * 64}}}
        current = {**original, "context": CURRENT}
        result = retained.check_runtime_input(original, current, CURRENT)
        self.assertEqual(result["post_native_runtime_observation"], "NOT_RETAINED")
        self.assertFalse(result["reference_phase_complete"])
        for altered in ({**current, "context": retained.ORIGIN},
                        {**current, "native_render_performed": True},
                        {**current, "binaries": {"python": {"sha256": "b" * 64}}}):
            with self.assertRaisesRegex(ValueError, "pre_native_runtime_input_changed"):
                retained.check_runtime_input(original, altered, CURRENT)

    def test_closed_selection_authorizes_only_the_three_unstarted_phases(self):
        with patch.dict(os.environ, SELECTED):
            three = transfer.manifest(CURRENT, "b" * 64)
            self.assertEqual(three["remaining_phases"], ["reopen-original-reference", "edited", "reopen-edited"])
            self.assertEqual(three["remaining_native_effect_invocations"], 3)
            self.assertEqual(three["additional_reference_renders"], 1)
            self.assertEqual(three["project_changes_authorized"], 0)
            self.assertEqual(three["retained_reference"], retained.contract())
            self.assertEqual(set(transfer.input_files()), set(transfer.INPUTS) | {retained.CAPSULE})
            self.assertEqual(transfer.input_files()[retained.CAPSULE], retained.CAPSULE_IDENTITY)
            self.assertLess(sum(size for size, _ in transfer.input_files().values()) + 128 * 1024, transfer.MAX_PLAINTEXT)
        with patch.dict(os.environ, {**SELECTED, retained.ENVIRONMENT: "none"}):
            four = transfer.manifest(CURRENT, "b" * 64)
            self.assertEqual(four["remaining_native_effect_invocations"], 4)
            self.assertNotIn("retained_reference", four)
            self.assertEqual(transfer.input_files(), transfer.INPUTS)
        for env in ({**SELECTED, retained.ENVIRONMENT: retained.CONTINUATION + "-retry"},
                    {**SELECTED, "MANIM_NATIVE_SCENARIO": transfer.RESUME_SCENARIO}):
            with self.subTest(env=env), patch.dict(os.environ, env), self.assertRaises(ValueError):
                transfer.manifest(CURRENT, "b" * 64)

    def test_pinned_selection_keeps_missing_postcheck_and_originals_separate(self):
        raw = retained.manifest_raw()
        self.assertEqual((len(raw), engine.sha(raw)), retained.MANIFEST_IDENTITY)
        original = json.loads(raw)
        self.assertEqual(original["origin_context"], retained.ORIGIN)
        self.assertEqual(original["original_readback_status"], "FAILED")
        self.assertEqual(original["post_native_frozen_runtime_observation"], "NOT_RETAINED")
        self.assertFalse(original["formal_G2_granted"])
        names = set(original["files"])
        self.assertEqual(len(names), 49)
        self.assertEqual(len([name for name in names if name.endswith(".png")]), 24)
        self.assertIn(retained.SOURCE_BUNDLE, names)
        self.assertIn(retained.DATABASE, names)
        self.assertFalse(any(name.endswith("/result.json") or "private-key" in name for name in names))
        for name in ("historical_phase_result_created", "formal_G2_granted", "automatic_retry"):
            self.assertFalse(retained.contract()[name])
        for bad in (b"", b"SYNTHETIC_UNIT_IS_NOT_AN_ORIGINAL", bytes(retained.CAPSULE_IDENTITY[0])):
            with self.assertRaisesRegex(ValueError, "retained_reference_capsule_identity"):
                retained.unpack(bad)

    def test_recipient_for_three_cannot_authorize_four_or_a_foreign_origin(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": engine.sha(der)}
        issued, expires = "2026-11-07T12:00:00+00:00", "2026-11-07T12:10:00+00:00"
        clock = datetime(2026, 11, 7, 12, 1, tzinfo=timezone.utc)
        with patch.dict(os.environ, SELECTED):
            three = transfer.recipient_record(recipient, CURRENT, "42", issued, expires, "c" * 64)
            self.assertEqual(three["native_effect_invocations"], 3)
            self.assertEqual(transfer.validate_recipient(three, CURRENT, "42", "c" * 64, clock=clock), recipient)
            for name, value in (("native_effect_invocations", 4), ("retained_reference", {})):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "recipient_exact_context"):
                    transfer.validate_recipient({**three, name: value}, CURRENT, "42", "c" * 64, clock=clock)
        with patch.dict(os.environ, {**SELECTED, retained.ENVIRONMENT: "none"}):
            with self.assertRaisesRegex(ValueError, "recipient_exact_context"):
                transfer.validate_recipient(three, CURRENT, "42", "c" * 64, clock=clock)

    def test_actual_lifecycle_shape_keeps_dispatch_and_status_records(self):
        # A public structural projection; this is no proof of Native provenance.
        fixture = json.loads((ROOT / "crates/native/tests/fixtures/manim-n1-receipt-lifecycle-42369864661.json").read_bytes())
        self.assertEqual(fixture["scope"], "STRUCTURAL_REGRESSION_FIXTURE_NOT_NATIVE_OR_G2_EVIDENCE")
        self.assertEqual(fixture["receipt_order"]["query"], "production_receipt_order.sequence DESC")
        self.assertEqual(fixture["prior"], fixture["current"][-20:])
        self.assertNotEqual(fixture["prior"], fixture["current"][:20])
        records = fixture["current"][:-20]
        endpoints = [row for row in records if row["stage"] == "completed" and row["request_id"].endswith((":start", ":result"))]
        endpoint_ids = {row["id"] for row in endpoints}
        operational = [row for row in records if row["id"] not in endpoint_ids]
        value = {"additional_native_receipts_scope": "completed_start_and_result_only",
                 "all_added_production_receipts_classified": True, "historical_production_receipts_unchanged": True,
                 "additional_native_receipts": 2, "added_native_receipts": endpoints,
                 "operational_native_receipts": operational, "added_production_receipts": records,
                 "additional_production_receipts": 10, "native_render_groups": [{}]}
        checked_receipt_classification(value, 2)
        for name, changed in (("additional_native_receipts", 10), ("additional_production_receipts", 2),
                ("operational_native_receipts", operational[:-1]), ("additional_native_receipts_scope", "all_rows"),
                ("historical_production_receipts_unchanged", False), ("all_added_production_receipts_classified", False),
                ("native_render_groups", [])):
            with self.subTest(name=name), self.assertRaises(ValueError):
                checked_receipt_classification({**value, name: changed}, 2)
        duplicate = copy.deepcopy(value)
        duplicate["added_production_receipts"][-1] = duplicate["added_production_receipts"][0]
        with self.assertRaises(ValueError):
            checked_receipt_classification(duplicate, 2)
        with self.assertRaises(ValueError):
            checked_receipt_classification(value, 4)


if __name__ == "__main__":
    unittest.main()
