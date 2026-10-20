"""Negative controls for the mandatory real-service browser selection."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import tempfile

PATH = Path(__file__).resolve().parents[1] / "ci_canvas_service.py"
SPEC = importlib.util.spec_from_file_location("ci_canvas_service", PATH)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def report():
    return {
        "errors": [], "stats": {"expected": 1, "skipped": 0, "unexpected": 0, "flaky": 0},
        "suites": [{"title": "outer", "suites": [{"specs": [{
            "title": gate.INK_TITLE, "file": "visual-language-draft-service.spec.ts", "ok": True,
            "tests": [{"projectId": "chromium", "expectedStatus": "passed", "status": "expected",
                       "results": [{"status": "passed", "retry": 0, "errors": []}]}],
        }]}]}],
    }


def selection_report(filename, titles):
    value = report()
    value["stats"]["expected"] = len(titles)
    rows = value["suites"][0]["suites"][0]["specs"]
    original = rows.pop()
    for title in titles:
        spec = copy.deepcopy(original)
        spec.update(file=filename, title=title)
        rows.append(spec)
    return value


class CanvasServiceReportTests(unittest.TestCase):
    def check(self, value):
        return gate.validate_report(value, "visual-language-draft-service.spec.ts", (gate.INK_TITLE,))

    def spec(self, value):
        return value["suites"][0]["suites"][0]["specs"][0]

    def test_original_pass_has_exact_inventory(self):
        observed = self.check(report())
        self.assertEqual(observed, {"passed": 1, "skipped": 0, "retries": 0, "titles": [gate.INK_TITLE]})
        self.assertEqual(len(gate.CANVAS_TITLES), 19)
        self.assertEqual(len(set(gate.CANVAS_TITLES)), 19)
        self.assertEqual(len(gate.PROGRAM_TITLES), 3)
        self.assertEqual(len(set(gate.PROGRAM_TITLES)), 3)
        self.assertEqual(len(gate.BRIEF_TITLES), 7)
        self.assertEqual(len(gate.BRIEF_RECOVERY_TITLES), 1)
        self.assertEqual(len(gate.BRIEF_UNCERTAINTY_TITLES), 6)
        self.assertEqual(len(gate.ACTIVE_VOICE_TITLES), 6)
        self.assertEqual(len(set(gate.ACTIVE_VOICE_TITLES)), 6)
        self.assertEqual(len(gate.CAMERA_TITLES), 7)
        self.assertEqual(len(set(gate.CAMERA_TITLES)), 7)
        self.assertEqual(len(gate.GESTURE_TITLES), 7)
        self.assertEqual(len(set(gate.GESTURE_TITLES)), 7)
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS[:7]), 43)
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS), 57)
        self.assertEqual(
            [(label, filename) for label, filename, _ in gate.SELECTIONS],
            [("canvas", "canvas-draft-service.spec.ts"),
             ("ink", "visual-language-draft-service.spec.ts"),
             ("program", "program-design-service.spec.ts"),
             ("brief", "brief-service.spec.ts"),
             ("brief-recovery", "brief-recovery-admission-service.spec.ts"),
             ("brief-uncertain", "brief-uncertain-outcome-service.spec.ts"),
             ("active-voice", "audio-active-voice-service.spec.ts"),
             ("camera", "camera-reframe-service.spec.ts"),
             ("gesture", "canvas-gesture-service.spec.ts")],
        )

    def test_all_57_exact_names_have_one_original_result(self):
        observed = []
        for label, filename, titles in gate.SELECTIONS:
            with self.subTest(selection=label):
                verified = gate.validate_report(selection_report(filename, titles), filename, titles)
                self.assertEqual(verified, {"passed": len(titles), "skipped": 0,
                                            "retries": 0, "titles": list(titles)})
                observed.extend((filename, title) for title in verified["titles"])
        self.assertEqual(len(observed), 57)
        self.assertEqual(len(set(observed)), 57)

    def test_camera_and_gesture_counts_cannot_substitute_named_inventory(self):
        for label, filename, titles in gate.SELECTIONS[-2:]:
            for mutation in ("missing", "duplicate", "foreign-title", "foreign-file"):
                with self.subTest(selection=label, mutation=mutation):
                    value = selection_report(filename, titles)
                    rows = value["suites"][0]["suites"][0]["specs"]
                    if mutation == "missing":
                        rows.pop()
                    elif mutation == "duplicate":
                        rows[-1] = copy.deepcopy(rows[0])
                    elif mutation == "foreign-title":
                        rows[-1]["title"] = gate.INK_TITLE
                    else:
                        rows[-1]["file"] = "mock.spec.ts"
                    with self.assertRaises(ValueError):
                        gate.validate_report(value, filename, titles)

    def test_new_selections_reject_skips_and_repeated_attempts(self):
        for label, filename, titles in gate.SELECTIONS[-2:]:
            for mutation in ("skip", "retry", "second-result"):
                with self.subTest(selection=label, mutation=mutation):
                    value = selection_report(filename, titles)
                    test = value["suites"][0]["suites"][0]["specs"][-1]["tests"][0]
                    if mutation == "skip":
                        test["expectedStatus"] = "skipped"
                        test["results"][0]["status"] = "skipped"
                    elif mutation == "retry":
                        test["results"][0]["retry"] = 1
                    else:
                        test["results"].append(copy.deepcopy(test["results"][0]))
                    with self.assertRaises(ValueError):
                        gate.validate_report(value, filename, titles)

    def test_selection_time_bounds_preserve_original_limits_and_new_case_budgets(self):
        self.assertEqual(gate.SELECTION_TIMEOUTS, {
            "canvas": 1800, "ink": 180, "program": 600, "brief": 900,
            "brief-recovery": 180, "brief-uncertain": 900, "active-voice": 900,
            "camera": 1200, "gesture": 900,
        })
        self.assertEqual(set(gate.SELECTION_TIMEOUTS), {label for label, _, _ in gate.SELECTIONS})
        self.assertGreater(gate.SELECTION_TIMEOUTS["camera"], 7 * 120 + 120)
        self.assertGreater(gate.SELECTION_TIMEOUTS["gesture"], 7 * 90 + 120)

    def test_skip_failure_and_flaky_counters_cannot_be_green(self):
        for key in ("skipped", "unexpected", "flaky"):
            with self.subTest(key=key):
                value = report()
                value["stats"][key] = 1
                with self.assertRaises(ValueError):
                    self.check(value)

    def test_missing_or_extra_cases_are_rejected_even_with_success_totals(self):
        for mutation in ("missing", "extra", "duplicate", "unknown"):
            with self.subTest(mutation=mutation):
                value = report()
                rows = value["suites"][0]["suites"][0]["specs"]
                if mutation == "missing":
                    rows.clear()
                elif mutation in ("extra", "duplicate"):
                    rows.append(copy.deepcopy(rows[0]))
                    if mutation == "extra":
                        rows[-1]["title"] = "unselected case"
                else:
                    rows[0]["title"] = "another title"
                with self.assertRaises(ValueError):
                    self.check(value)

    def test_expected_failure_and_other_browser_cannot_satisfy_selection(self):
        for field, bad in (("expectedStatus", "failed"), ("expectedStatus", "skipped"),
                           ("status", "unexpected"), ("projectId", "firefox")):
            with self.subTest(field=field, bad=bad):
                value = report()
                self.spec(value)["tests"][0][field] = bad
                with self.assertRaises(ValueError):
                    self.check(value)

    def test_retry_and_repeated_result_are_rejected(self):
        for mutation in ("retry", "repeated", "empty"):
            with self.subTest(mutation=mutation):
                value = report()
                results = self.spec(value)["tests"][0]["results"]
                if mutation == "retry":
                    results[0]["retry"] = 1
                elif mutation == "repeated":
                    results.append(copy.deepcopy(results[0]))
                else:
                    results.clear()
                with self.assertRaises(ValueError):
                    self.check(value)

    def test_global_or_original_result_error_is_rejected(self):
        for mutation in ("global", "error", "errors", "failed"):
            with self.subTest(mutation=mutation):
                value = report()
                result = self.spec(value)["tests"][0]["results"][0]
                if mutation == "global":
                    value["errors"] = [{"message": "browser failed"}]
                elif mutation == "failed":
                    result["status"] = "failed"
                else:
                    result[mutation] = {"message": "error"} if mutation == "error" else [{"message": "error"}]
                with self.assertRaises(ValueError):
                    self.check(value)

    def test_source_file_and_spec_success_are_required(self):
        for field, bad in (("file", "mock.spec.ts"), ("ok", False), ("ok", None)):
            with self.subTest(field=field):
                value = report()
                self.spec(value)[field] = bad
                with self.assertRaises(ValueError):
                    self.check(value)

    def test_missing_counters_and_zero_execution_are_rejected(self):
        for mutation in ("missing", "zero", "empty", "missing_errors", "multiple_projects"):
            with self.subTest(mutation=mutation):
                value = report()
                if mutation == "missing":
                    del value["stats"]["skipped"]
                elif mutation == "zero":
                    value["stats"]["expected"] = 0
                elif mutation == "empty":
                    value["suites"] = []
                elif mutation == "missing_errors":
                    del value["errors"]
                else:
                    self.spec(value)["tests"] *= 2
                with self.assertRaises(ValueError):
                    self.check(value)


    def test_nonzero_process_exit_is_retained_without_retry(self):
        with tempfile.TemporaryDirectory(prefix="canvas-report-control-") as directory:
            output = Path(directory)
            outcome = mock.Mock(returncode=7)
            with mock.patch.object(gate.subprocess, "run", return_value=outcome) as child:
                result = gate.execute(["unused-synthetic-command"], output, {}, output, "failed", 1)
            self.assertEqual(child.call_count, 1)
            self.assertEqual(result["exit_code"], 7)
            self.assertEqual(result["diagnostic_custody"], "DIRECT_PROCESS_EXIT_OBSERVED")
            self.assertNotIn("verified", result)
            self.assertTrue((output / "failed.command.json").is_file())

    def test_timeout_keeps_unsealed_snapshot_and_never_retries(self):
        with tempfile.TemporaryDirectory(prefix="canvas-report-control-") as directory:
            output = Path(directory)
            original = gate.subprocess.TimeoutExpired(["unused-synthetic-command"], 1)
            with mock.patch.object(gate.subprocess, "run", side_effect=original) as child:
                result = gate.execute(["unused-synthetic-command"], output, {}, output, "timeout", 1)
            self.assertEqual(child.call_count, 1)
            self.assertIsNone(result["exit_code"])
            self.assertEqual(result["observation"], "TIMED_OUT_NO_RETRY")
            self.assertEqual(result["diagnostic_custody"], "UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED")
            self.assertEqual(result["timeout_seconds"], 1)
            self.assertNotIn("verified", result)
            self.assertTrue((output / "timeout.command.json").is_file())



if __name__ == "__main__":
    unittest.main()
