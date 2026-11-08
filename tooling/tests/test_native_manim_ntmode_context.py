"""Exact historical NT-mode admission; source-only and no artifact GET/Native."""
from __future__ import annotations

from argparse import Namespace
import hashlib
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_manim_frozen_runtime_host as host

HISTORICAL = {
    "source_sha": "54c74f5366513a72f6f22d4f6641091362534a09",
    "semwright_sha": "acc0a089e2f0e3e9936632fb2a4aa0c2fab56199",
    "run_id": "42361964881",
    "run_attempt": "1",
}
RECEIPT_SHA = "d54d3cc4e49fb0cda44d7da0106ad02532e06263b1e7a0f7083e18cafe8fe6dc"


def historical_args(**overrides):
    values = {
        "source_sha": HISTORICAL["source_sha"],
        "run_id": HISTORICAL["run_id"],
        "admission": "runtime-metadata",
        "attempt_tag": "causalntmode01",
        "source_gate_sha256": None,
        "runtime_receipt_sha256": RECEIPT_SHA,
    }
    values.update(overrides)
    return Namespace(**values)


class HistoricalNtModeContextTests(unittest.TestCase):
    def test_fixed_selection_preserves_actual_original_sdk_and_receipt_bytes(self):
        self.assertNotEqual(host.SDK, HISTORICAL["semwright_sha"])
        self.assertEqual(host.NTMODE_CONTEXT, HISTORICAL)
        self.assertEqual(host.selected_context(historical_args()), HISTORICAL)
        path = Path(__file__).resolve().parents[1] / (
            "requirements/manim-frozen-runtime-42361964881-receipt.json"
        )
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), RECEIPT_SHA)
        self.assertEqual(host.engine.strict(raw)["context"], HISTORICAL)

    def test_current_sdk_is_never_silently_relabelled_as_historical(self):
        modern = historical_args(
            source_sha="a" * 40, run_id="18", attempt_tag=None,
            admission="source-gate", runtime_receipt_sha256=None,
        )
        self.assertEqual(host.selected_context(modern), {
            "source_sha": "a" * 40, "semwright_sha": host.SDK,
            "run_id": "18", "run_attempt": "1",
        })
        # Even an old source spelling is not a historical selector without
        # the exact fixed tag and admission/receipt.
        plain = historical_args(attempt_tag=None)
        self.assertEqual(host.selected_context(plain)["semwright_sha"], host.SDK)

    def test_historical_exception_denies_foreign_source_run_mode_and_receipt(self):
        failures = (
            {"source_sha": "0" * 40},
            {"run_id": "42361964882"},
            {"admission": "source-gate"},
            {"source_gate_sha256": "a" * 64},
            {"runtime_receipt_sha256": "b" * 64},
            {"attempt_tag": "causalntmode01-other"},
        )
        for changed in failures:
            with self.subTest(changed=changed), self.assertRaises(host.Rejected):
                host.selected_context(historical_args(**changed))

    def test_historical_guard_reaches_original_namespace_only_for_exact_context(self):
        admission = {
            "mode": "runtime-metadata",
            "runtime_receipt_sha256": RECEIPT_SHA,
            "runtime": {"retained_artifact": {"artifact_id": 13570292418}},
        }
        sentinel = RuntimeError("namespace-probe-without-I-O")
        with mock.patch.object(host, "namespace", side_effect=sentinel) as proof:
            with self.assertRaisesRegex(RuntimeError, "namespace-probe-without-I-O"):
                host.checked_ntmode_continuation(
                    Path("E:/historical-no-read"), dict(HISTORICAL), admission, {}, None
                )
            self.assertEqual(proof.call_count, 1)
        variants = (
            ({**HISTORICAL, "semwright_sha": host.SDK}, admission),
            ({**HISTORICAL, "source_sha": "f" * 40}, admission),
            (dict(HISTORICAL), {**admission, "mode": "source-gate"}),
            (dict(HISTORICAL), {**admission, "runtime_receipt_sha256": "0" * 64}),
        )
        with mock.patch.object(host, "namespace") as protected:
            for context, record in variants:
                with self.subTest(context=context, record=record):
                    with self.assertRaises(host.Rejected):
                        host.checked_ntmode_continuation(
                            Path("E:/historical-no-read"), context, record, {}, None
                        )
            protected.assert_not_called()

    def test_unauthorized_historical_tag_refuses_before_private_access_or_get(self):
        args = historical_args(source_sha="0" * 40)
        args.private = Path("E:/not-observed")
        with mock.patch.dict(host.os.environ):
            with mock.patch.object(host, "namespace") as namespace, \
                 mock.patch.object(host, "HostResources") as resources, \
                 mock.patch.object(host, "checked_admission") as admission, \
                 mock.patch.object(host, "download_once") as download:
                with self.assertRaises(host.Rejected):
                    host.run(args)
        namespace.assert_not_called()
        resources.assert_not_called()
        admission.assert_not_called()
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
