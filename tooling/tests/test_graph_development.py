"""Inert report/selector boundaries; no Host, fake Ready or native execution."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("graph_development_under_test", ROOT / "tooling/graph_development.py")
development = importlib.util.module_from_spec(spec)
spec.loader.exec_module(development)


def report(*, listing=False):
    return {
        "errors": [],
        "config": {"workers": 1, "forbidOnly": True, "maxFailures": 1,
                   "projects": [{"id": "chromium", "name": "chromium", "repeatEach": 1, "retries": 0}]},
        "stats": {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0},
        "suites": [{"suites": [], "specs": [{
            "title": development.TITLE, "file": "tooling/graph_dev_first_save.spec.ts", "ok": True,
            "tests": [{"projectId": "chromium", "status": "expected", "expectedStatus": "passed",
                       "results": [] if listing else [{"status": "passed", "retry": 0, "errors": []}]}],
        }]}],
    }


class DevelopmentOriginalReportTests(unittest.TestCase):
    def test_discovery_and_success_are_distinct_and_neither_grants_formal_credit(self):
        self.assertEqual(development.selected_report(report(listing=True), listing=True),
                         {"tests": 1, "retries": 0, "listing_only": True, "formal_acceptance": False})
        self.assertFalse(development.selected_report(report(), listing=False)["formal_acceptance"])

    def test_discovery_cannot_be_reported_as_save(self):
        with self.assertRaisesRegex(ValueError, "DEV_REPORT_NOT_ONE_ACKNOWLEDGED_PASS"):
            development.selected_report(report(listing=True), listing=False)

    def test_failure_unknown_retry_and_skipped_cannot_be_success(self):
        for status in ("failed", "timedOut", "interrupted", "skipped"):
            with self.subTest(status=status):
                value = report()
                value["suites"][0]["specs"][0]["tests"][0]["results"][0]["status"] = status
                with self.assertRaisesRegex(ValueError, "DEV_REPORT_NOT_ONE_ACKNOWLEDGED_PASS"):
                    development.selected_report(value, listing=False)
        value = report()
        value["suites"][0]["specs"][0]["tests"][0]["results"][0]["retry"] = 1
        with self.assertRaisesRegex(ValueError, "DEV_REPORT_NOT_ONE_ACKNOWLEDGED_PASS"):
            development.selected_report(value, listing=False)

    def test_foreign_case_duplicate_result_and_report_error_are_rejected(self):
        for kind in ("foreign", "duplicate", "error"):
            value = report()
            spec = value["suites"][0]["specs"][0]
            if kind == "foreign":
                spec["file"] = "semantic-graph-service.spec.ts"
            elif kind == "duplicate":
                spec["tests"][0]["results"].append(copy.deepcopy(spec["tests"][0]["results"][0]))
            else:
                value["errors"] = [{"message": "original process not closed"}]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                development.selected_report(value, listing=False)

    def test_parallel_or_retry_config_denied(self):
        for property_name, value in (("workers", 2), ("forbidOnly", False), ("maxFailures", 2)):
            current = report()
            current["config"][property_name] = value
            with self.subTest(property_name=property_name), self.assertRaisesRegex(ValueError, "EXECUTION_POLICY"):
                development.selected_report(current, listing=False)
        current = report()
        current["config"]["projects"][0]["retries"] = 1
        with self.assertRaisesRegex(ValueError, "EXECUTION_POLICY"):
            development.selected_report(current, listing=False)


if __name__ == "__main__":
    unittest.main()
