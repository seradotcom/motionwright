"""Synthetic, bounded negative contracts for passive v0.1 CI forensics.

Synthetic process numbers are not root-cause proof and not installed-owner evidence.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_provider_loss_correlation import (
    CorrelationEvidenceError, inspect_provider_lifetime,
)


PRIVATE = "mc1:private-native-job:secret-ticket=/owner/media/project.mp4"


def sample(elapsed: int, roles: list[str], observed: bool = True) -> dict:
    return {
        "elapsed_ms": elapsed, "root_observed": observed, "truncated": False,
        "cgroup": {"current_bytes": 10000, "oom_kill": None,
                   "sensitive_owner_path": PRIVATE},
        "processes": [
            {"role": role, "pid": 600 + n, "ppid": 700 + n,
             "cwd": PRIVATE, "commandline": PRIVATE}
            for n, role in enumerate(roles)
        ],
    }


def baseline() -> dict:
    frames = [
        sample(0, ["semwrightd"]),
        sample(2000, ["semwrightd", "bwrap", "bwrap", "bwrap", "node"]),
        sample(4000, ["semwrightd", "bwrap", "node"]),
    ]
    return {
        "schema": "motionwright-ci-provider-lifecycle/1",
        "authority": "passive-ci-process-status-not-driver-evidence",
        "sample_count": len(frames),
        "observed_roles": ["semwrightd", "bwrap", "node"],
        "samples": frames,
        "daemon_exit_before_owner_cleanup": None,
        "cgroup_oom_kills_delta": None,
        "secret_broker_data": PRIVATE,
    }


def failed_status() -> dict:
    return {
        "schema": "motionwright-ci-render-status-reconciliation/1",
        "read_only": True,
        "render_outcome": "unconfirmed",
        "timeout_then_provider_unavailable_observed": True,
        "same_acknowledged_job_status_chain_verified": True,
        "render_start_dispatch_count": 1,
        "status_events": [{"message": PRIVATE}],
    }


class ProviderLossCorrelationTests(unittest.TestCase):
    def assert_no_private_or_false_release(self, verdict: dict) -> None:
        encoded = json.dumps(verdict, sort_keys=True)
        for part in (PRIVATE, "secret-ticket", "/owner/media", "private-native-job",
                     "cwd", "ppid", "pid"):
            if part in ("pid", "ppid"):
                self.assertNotIn('"' + part + '"', encoded)
            else:
                self.assertNotIn(part, encoded)
        self.assertFalse(verdict["oom_cause_established"])
        self.assertFalse(verdict["provider_generation_exit_cause_established"])
        self.assertFalse(verdict["specific_provider_process_exit_verified"])
        self.assertFalse(verdict["native_job_terminal_result_verified_by_this_report"])
        self.assertFalse(verdict["automatic_native_mutation_retry"])
        self.assertEqual(verdict["installed_owner_acceptance"], "NOT_RUN")
        self.assertFalse(verdict["v01_release_ready"])

    def test_realistic_bwrap_drop_with_live_daemon_is_only_correlation(self) -> None:
        result = inspect_provider_lifetime(baseline(), failed_status())
        self.assert_no_private_or_false_release(result)
        self.assertTrue(result["render_outcome_unknown_after_status_loss"])
        self.assertTrue(result["daemon_observed_in_all_samples"])
        self.assertFalse(result["daemon_exit_before_owner_cleanup_observed"])
        self.assertEqual(result["bwrap_process_count_peak"], 3)
        self.assertEqual(result["bwrap_process_count_final"], 1)
        self.assertEqual(result["bwrap_count_decreases_observed"], 1)
        self.assertEqual(result["samples_bounded"], 3)
        self.assertTrue(result["operator_owner_reconciliation_required_if_unknown"])

    def test_no_correlated_failure_never_infers_interruption_from_process_churn(self) -> None:
        result = inspect_provider_lifetime(baseline(), None)
        self.assert_no_private_or_false_release(result)
        self.assertFalse(result["render_outcome_unknown_after_status_loss"])
        self.assertFalse(result["operator_owner_reconciliation_required_if_unknown"])
        self.assertEqual(result["bwrap_count_decreases_observed"], 1)

    def test_terminal_result_receipt_does_not_reinterpret_prior_timeout_as_unknown(self) -> None:
        response = failed_status()
        response["render_outcome"] = "confirmed_result_receipt"
        result = inspect_provider_lifetime(baseline(), response)
        self.assert_no_private_or_false_release(result)
        self.assertFalse(result["render_outcome_unknown_after_status_loss"])

    def test_cross_job_or_missing_scope_does_not_infer_generation_loss(self) -> None:
        for value in (False, None, "true"):
            response = failed_status()
            if value is None:
                response.pop("same_acknowledged_job_status_chain_verified")
            else:
                response["same_acknowledged_job_status_chain_verified"] = value
            result = inspect_provider_lifetime(baseline(), response)
            self.assertFalse(result["render_outcome_unknown_after_status_loss"])
            self.assertFalse(result["read_only_timeout_then_unavailable_observed"])
            self.assertFalse(result["operator_owner_reconciliation_required_if_unknown"])
            self.assert_no_private_or_false_release(result)

    def test_missing_root_is_not_silently_reported_as_healthy(self) -> None:
        report = baseline()
        report["samples"][2]["root_observed"] = False
        report["daemon_exit_before_owner_cleanup"] = 137
        result = inspect_provider_lifetime(report, failed_status())
        self.assertFalse(result["daemon_observed_in_all_samples"])
        self.assertTrue(result["daemon_exit_before_owner_cleanup_observed"])
        self.assertEqual(result["samples_with_missing_daemon"], 1)
        self.assert_no_private_or_false_release(result)

    def test_oom_kill_is_numeric_but_not_claimed_as_root_cause(self) -> None:
        report = baseline()
        report["cgroup_oom_kills_delta"] = 1
        result = inspect_provider_lifetime(report, failed_status())
        self.assertEqual(result["oom_kills_delta_observed"], 1)
        self.assertFalse(result["oom_cause_established"])

    def test_unrecognized_process_role_with_embedded_secret_rejected(self) -> None:
        report = baseline()
        report["samples"][1]["processes"][0]["role"] = PRIVATE
        with self.assertRaisesRegex(CorrelationEvidenceError, "unexpected_native_process_role"):
            inspect_provider_lifetime(report, failed_status())

    def test_unordered_or_inconsistent_samples_fail_closed(self) -> None:
        report = baseline()
        report["samples"][1]["elapsed_ms"] = 6000
        with self.assertRaisesRegex(CorrelationEvidenceError, "invalid_sample_order"):
            inspect_provider_lifetime(report, failed_status())
        report = baseline()
        report["sample_count"] = 101
        with self.assertRaisesRegex(CorrelationEvidenceError, "inconsistent_sample_count"):
            inspect_provider_lifetime(report, failed_status())

    def test_oversized_process_sample_cannot_bypass_bound(self) -> None:
        report = baseline()
        report["samples"][1]["processes"] *= 25
        with self.assertRaisesRegex(CorrelationEvidenceError, "unbounded_process_sample"):
            inspect_provider_lifetime(report, failed_status())

    def test_unknown_schema_or_forged_status_evidence_rejected(self) -> None:
        report = baseline()
        report["authority"] = "native-driver-proved-generation-recovery"
        with self.assertRaisesRegex(CorrelationEvidenceError, "invalid_source_schema"):
            inspect_provider_lifetime(report, failed_status())
        response = failed_status()
        response["schema"] = "synthetic-forged-status"
        with self.assertRaisesRegex(CorrelationEvidenceError, "invalid_status_schema"):
            inspect_provider_lifetime(baseline(), response)

    def test_truncated_tree_is_explicit_and_does_not_prove_liveness(self) -> None:
        report = baseline()
        report["samples"][1]["truncated"] = True
        result = inspect_provider_lifetime(report, failed_status())
        self.assertEqual(result["samples_with_truncated_process_tree"], 1)
        self.assert_no_private_or_false_release(result)


if __name__ == "__main__":
    unittest.main()
