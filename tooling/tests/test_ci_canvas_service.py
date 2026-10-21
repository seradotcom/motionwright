"""Negative controls for the mandatory real-service browser selection."""
import copy
import io
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
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS[:9]), 57)
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS[:10]), 65)
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS), 129)
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
             ("gesture", "canvas-gesture-service.spec.ts"),
             ("narrative", "narrative-service.spec.ts"),
             ('beat-draft', 'scene-beat-draft-service.spec.ts'),
             ('beat-time', 'scene-beat-time-service.spec.ts'),
             ('delivery-recipe', 'delivery-recipe.spec.ts'),
             ('recipe-authority', 'delivery-recipe-authority-service.spec.ts'),
             ('review', 'review-service.spec.ts'),
             ('inspector', 'scene-inspector-service.spec.ts'),
             ('profile-metadata', 'delivery-profile-preservation.spec.ts'),
             ('profile-authority', 'delivery-profile-authority-service.spec.ts'),
             ],
        )

    def test_all_57_exact_names_have_one_original_result(self):
        observed = []
        for label, filename, titles in gate.SELECTIONS[:9]:
            with self.subTest(selection=label):
                verified = gate.validate_report(selection_report(filename, titles), filename, titles)
                self.assertEqual(verified, {"passed": len(titles), "skipped": 0,
                                            "retries": 0, "titles": list(titles)})
                observed.extend((filename, title) for title in verified["titles"])
        self.assertEqual(len(observed), 57)
        self.assertEqual(len(set(observed)), 57)

    def test_camera_and_gesture_counts_cannot_substitute_named_inventory(self):
        for label, filename, titles in (row for row in gate.SELECTIONS if row[0] in {"camera", "gesture"}):
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
        for label, filename, titles in (row for row in gate.SELECTIONS if row[0] in {"camera", "gesture"}):
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
            "camera": 1200, "gesture": 900, "narrative": 1200,
            'beat-draft': 1500,
            'beat-time': 660,
            'delivery-recipe': 1260,
            'recipe-authority': 780,
            'review': 900,
            'inspector': 900,
            'profile-metadata': 1500,
            'profile-authority': 900,
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



class SelectedServiceScopeTests(unittest.TestCase):
    def test_narrative_scope_preserves_full_default_and_declares_every_omission(self):
        plan = gate.selection_plan("narrative")
        self.assertEqual(plan["required_tests"], 8)
        self.assertEqual(plan["full_default_tests"], 129)
        self.assertEqual(plan["scope"], "EXPLICIT_SELECTED_SERVICE_CASES")
        self.assertEqual(len(plan["required"]), 1)
        self.assertEqual(plan["required"][0]["file"], "narrative-service.spec.ts")
        self.assertEqual(plan["required"][0]["titles"], list(gate.NARRATIVE_TITLES))
        self.assertEqual(sum(len(row["titles"]) for row in plan["not_selected"]), 121)
        self.assertEqual({row["label"] for row in plan["not_selected"]},
                         {row[0] for row in gate.SELECTIONS if row[0] != "narrative"})
        self.assertTrue(all(row["status"] == "NOT_SELECTED" and row["technical_credit"] == "NONE"
                            for row in plan["not_selected"]))
        self.assertFalse(plan["formal_acceptance"])
        self.assertEqual(gate.selection_plan("all")["not_selected"], [])
        self.assertEqual(gate.selected_selections("all"), gate.SELECTIONS)

    def test_unknown_empty_combined_and_nonstring_selection_are_rejected(self):
        for selection in ("", "Narrative", "narrative,canvas", "all ", "foreign", None, ["narrative"]):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                gate.selected_selections(selection)

    def test_narrative_requires_each_of_eight_original_results(self):
        titles = gate.NARRATIVE_TITLES
        self.assertEqual(len(titles), 8)
        self.assertEqual(len(set(titles)), 8)
        value = selection_report("narrative-service.spec.ts", titles)
        observed = gate.validate_report(value, "narrative-service.spec.ts", titles)
        self.assertEqual(observed["passed"], 8)
        self.assertEqual(observed["titles"], list(titles))
        self.assertGreater(gate.SELECTION_TIMEOUTS["narrative"], 8 * 120 + 120)

    def test_narrative_count_cannot_hide_missing_duplicate_foreign_or_mock_results(self):
        for mutation in ("missing", "duplicate", "foreign-title", "foreign-file"):
            with self.subTest(mutation=mutation):
                value = selection_report("narrative-service.spec.ts", gate.NARRATIVE_TITLES)
                rows = value["suites"][0]["suites"][0]["specs"]
                if mutation == "missing":
                    rows.pop()
                elif mutation == "duplicate":
                    rows[-1] = copy.deepcopy(rows[0])
                elif mutation == "foreign-title":
                    rows[-1]["title"] = gate.INK_TITLE
                else:
                    rows[-1]["file"] = "narrative-preservation.spec.ts"
                with self.assertRaises(ValueError):
                    gate.validate_report(value, "narrative-service.spec.ts", gate.NARRATIVE_TITLES)

    def test_narrative_skip_retry_or_second_result_cannot_satisfy_scope(self):
        for mutation in ("skip", "retry", "second-result"):
            with self.subTest(mutation=mutation):
                value = selection_report("narrative-service.spec.ts", gate.NARRATIVE_TITLES)
                test = value["suites"][0]["suites"][0]["specs"][-1]["tests"][0]
                if mutation == "skip":
                    test["expectedStatus"] = "skipped"
                    test["results"][0]["status"] = "skipped"
                elif mutation == "retry":
                    test["results"][0]["retry"] = 1
                else:
                    test["results"].append(copy.deepcopy(test["results"][0]))
                with self.assertRaises(ValueError):
                    gate.validate_report(value, "narrative-service.spec.ts", gate.NARRATIVE_TITLES)



class AuthoringServiceScopeTests(unittest.TestCase):
    def test_closed_authoring_scope_requires_all_five_files_and_thirty_eight_cases(self):
        plan = gate.selection_plan("authoring-drafts")
        self.assertEqual([(x["label"], x["file"], len(x["titles"])) for x in plan["required"]], [
            ("beat-draft", "scene-beat-draft-service.spec.ts", 11),
            ("beat-time", "scene-beat-time-service.spec.ts", 4),
            ("delivery-recipe", "delivery-recipe.spec.ts", 12),
            ("recipe-authority", "delivery-recipe-authority-service.spec.ts", 5),
            ("review", "review-service.spec.ts", 6),
        ])
        self.assertEqual(plan["required_tests"], 38)
        self.assertEqual(plan["full_default_tests"], 129)
        self.assertEqual(sum(len(x["titles"]) for x in plan["not_selected"]), 91)
        self.assertEqual({x["label"] for x in plan["not_selected"]},
                         {row[0] for row in gate.SELECTIONS[:10] + gate.SELECTIONS[15:]})
        self.assertTrue(all(x["status"] == "NOT_SELECTED" and x["technical_credit"] == "NONE"
                            for x in plan["not_selected"]))
        self.assertFalse(plan["formal_acceptance"])

    def test_each_authoring_file_accepts_only_its_exact_original_inventory(self):
        cases = []
        for label, filename, titles in gate.selected_selections("authoring-drafts"):
            with self.subTest(selection=label):
                plan = gate.selection_plan(label)
                self.assertEqual(plan["required_tests"], len(titles))
                self.assertEqual(sum(len(x["titles"]) for x in plan["not_selected"]),
                                 129 - len(titles))
                verified = gate.validate_report(selection_report(filename, titles), filename, titles)
                self.assertEqual(verified["passed"], len(titles))
                cases.extend((filename, title) for title in verified["titles"])
                per_case = 90 if label == "delivery-recipe" else 120
                self.assertGreater(gate.SELECTION_TIMEOUTS[label], len(titles) * per_case + 120)
        self.assertEqual(len(cases), 38)
        self.assertEqual(len(set(cases)), 38)

    def test_authoring_totals_cannot_hide_missing_duplicate_foreign_or_mock_results(self):
        for label, filename, titles in gate.selected_selections("authoring-drafts"):
            for mutation in ("missing", "duplicate", "foreign-title", "component-file"):
                with self.subTest(selection=label, mutation=mutation):
                    value = selection_report(filename, titles)
                    rows = value["suites"][0]["suites"][0]["specs"]
                    if mutation == "missing":
                        rows.pop()
                    elif mutation == "duplicate":
                        rows[-1] = copy.deepcopy(rows[0])
                    elif mutation == "foreign-title":
                        rows[-1]["title"] = gate.NARRATIVE_TITLES[0]
                    else:
                        rows[-1]["file"] = "review-preservation.spec.ts"
                    with self.assertRaises(ValueError):
                        gate.validate_report(value, filename, titles)

    def test_authoring_skips_retries_missing_and_repeated_results_cannot_pass(self):
        for label, filename, titles in gate.selected_selections("authoring-drafts"):
            for mutation in ("skip", "retry", "second-result", "missing-result", "expected-failure"):
                with self.subTest(selection=label, mutation=mutation):
                    value = selection_report(filename, titles)
                    case = value["suites"][0]["suites"][0]["specs"][-1]["tests"][0]
                    if mutation == "skip":
                        case["expectedStatus"] = "skipped"
                        case["results"][0]["status"] = "skipped"
                    elif mutation == "retry":
                        case["results"][0]["retry"] = 1
                    elif mutation == "second-result":
                        case["results"].append(copy.deepcopy(case["results"][0]))
                    elif mutation == "missing-result":
                        case["results"].clear()
                    else:
                        case["expectedStatus"] = "failed"
                    with self.assertRaises(ValueError):
                        gate.validate_report(value, filename, titles)

    def test_group_inventory_cannot_silently_omit_repeat_or_include_unknown_files(self):
        original = gate.SELECTION_GROUPS["authoring-drafts"]
        for mutation in ("omit", "duplicate", "unknown", "count", "nonstring", "empty"):
            with self.subTest(mutation=mutation):
                group = copy.deepcopy(original)
                labels = list(group["labels"])
                if mutation == "omit":
                    labels.pop()
                elif mutation == "duplicate":
                    labels[-1] = labels[0]
                elif mutation == "unknown":
                    labels[-1] = "unreviewed"
                elif mutation == "count":
                    group["required_tests"] = 37
                elif mutation == "nonstring":
                    labels[-1] = None
                else:
                    labels.clear()
                group["labels"] = tuple(labels)
                with mock.patch.dict(gate.SELECTION_GROUPS, {"authoring-drafts": group}):
                    with self.assertRaises(ValueError):
                        gate.selected_selections("authoring-drafts")
        for selection in ("authoring", "authoring-drafts,review", "authoring-drafts ", ["authoring-drafts"]):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                gate.selected_selections(selection)

    def test_full_default_keeps_old_sixty_five_and_new_thirty_eight_distinct_cases(self):
        plan = gate.selection_plan("all")
        self.assertEqual(plan["required_tests"], 129)
        self.assertEqual(plan["not_selected"], [])
        cases = [(x["file"], title) for x in plan["required"] for title in x["titles"]]
        self.assertEqual(len(cases), 129)
        self.assertEqual(len(set(cases)), 129)
        self.assertEqual(gate.selected_selections("all"), gate.SELECTIONS)



class CompleteAuthoringScopeTests(unittest.TestCase):
    def test_complete_authoring_requires_all_eight_files_and_sixty_four_cases(self):
        plan = gate.selection_plan("complete-authoring")
        self.assertEqual([(x["label"], x["file"], len(x["titles"])) for x in plan["required"]], [
            ("beat-draft", "scene-beat-draft-service.spec.ts", 11),
            ("beat-time", "scene-beat-time-service.spec.ts", 4),
            ("delivery-recipe", "delivery-recipe.spec.ts", 12),
            ("recipe-authority", "delivery-recipe-authority-service.spec.ts", 5),
            ("review", "review-service.spec.ts", 6),
            ("inspector", "scene-inspector-service.spec.ts", 6),
            ("profile-metadata", "delivery-profile-preservation.spec.ts", 14),
            ("profile-authority", "delivery-profile-authority-service.spec.ts", 6),
        ])
        self.assertEqual(plan["required_tests"], 64)
        self.assertEqual(plan["full_default_tests"], 129)
        self.assertEqual(sum(len(x["titles"]) for x in plan["not_selected"]), 65)
        self.assertEqual({x["label"] for x in plan["not_selected"]},
                         {row[0] for row in gate.SELECTIONS[:10]})
        self.assertTrue(all(x["status"] == "NOT_SELECTED" and x["technical_credit"] == "NONE"
                            for x in plan["not_selected"]))
        self.assertFalse(plan["formal_acceptance"])
        self.assertEqual(gate.selection_plan("authoring-drafts")["required_tests"], 38)
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS[:15]), 103)

    def test_inspector_and_profile_results_require_exact_files_names_and_one_original_result(self):
        observed = []
        for label, filename, titles in gate.SELECTIONS[15:]:
            with self.subTest(selection=label):
                result = gate.validate_report(selection_report(filename, titles), filename, titles)
                self.assertEqual(result["titles"], list(titles))
                self.assertEqual(result["passed"], len(titles))
                observed.extend((filename, title) for title in result["titles"])
                self.assertEqual(gate.selection_plan(label)["required_tests"], len(titles))
                case_bound = 90 if label == "profile-metadata" else 120
                self.assertGreater(gate.SELECTION_TIMEOUTS[label], len(titles) * case_bound + 120)
            for mutation in ("missing", "duplicate", "foreign-title", "component-file", "skip",
                             "retry", "second-result", "missing-result", "expected-failure", "error"):
                with self.subTest(selection=label, mutation=mutation):
                    value = selection_report(filename, titles)
                    rows = value["suites"][0]["suites"][0]["specs"]
                    case = rows[-1]["tests"][0]
                    if mutation == "missing":
                        rows.pop()
                    elif mutation == "duplicate":
                        rows[-1] = copy.deepcopy(rows[0])
                    elif mutation == "foreign-title":
                        rows[-1]["title"] = gate.INK_TITLE
                    elif mutation == "component-file":
                        rows[-1]["file"] = "delivery-profile-authority.spec.ts"
                    elif mutation == "skip":
                        case["expectedStatus"] = "skipped"
                        case["results"][0]["status"] = "skipped"
                    elif mutation == "retry":
                        case["results"][0]["retry"] = 1
                    elif mutation == "second-result":
                        case["results"].append(copy.deepcopy(case["results"][0]))
                    elif mutation == "missing-result":
                        case["results"].clear()
                    elif mutation == "expected-failure":
                        case["expectedStatus"] = "failed"
                    else:
                        case["results"][0]["errors"] = [{"message": "source service failure"}]
                    with self.assertRaises(ValueError):
                        gate.validate_report(value, filename, titles)
        self.assertEqual(len(observed), 26)
        self.assertEqual(len(set(observed)), 26)

    def test_complete_group_cannot_drop_repeat_or_replace_a_required_file(self):
        original = gate.SELECTION_GROUPS["complete-authoring"]
        for mutation in ("omit", "duplicate", "unknown", "count", "nonstring", "empty"):
            with self.subTest(mutation=mutation):
                group = copy.deepcopy(original)
                labels = list(group["labels"])
                if mutation == "omit":
                    labels.pop()
                elif mutation == "duplicate":
                    labels[-1] = labels[0]
                elif mutation == "unknown":
                    labels[-1] = "unreviewed"
                elif mutation == "count":
                    group["required_tests"] = 63
                elif mutation == "nonstring":
                    labels[-1] = None
                else:
                    labels.clear()
                group["labels"] = tuple(labels)
                with mock.patch.dict(gate.SELECTION_GROUPS, {"complete-authoring": group}):
                    with self.assertRaises(ValueError):
                        gate.selected_selections("complete-authoring")
        for invalid in ("complete-authoring ", "Complete-authoring",
                        "complete-authoring,canvas", ["complete-authoring"]):
            with self.subTest(selection=invalid), self.assertRaises(ValueError):
                gate.selected_selections(invalid)

    def test_complete_roster_never_converts_unexecuted_or_wrong_browser_results_into_credit(self):
        for label, filename, titles in gate.SELECTIONS[15:]:
            for mutation in ("missing-counter", "zero", "flaky", "foreign-browser", "global-error"):
                with self.subTest(selection=label, mutation=mutation):
                    value = selection_report(filename, titles)
                    case = value["suites"][0]["suites"][0]["specs"][-1]["tests"][0]
                    if mutation == "missing-counter":
                        del value["stats"]["skipped"]
                    elif mutation == "zero":
                        value["stats"]["expected"] = 0
                    elif mutation == "flaky":
                        value["stats"]["flaky"] = 1
                    elif mutation == "foreign-browser":
                        case["projectId"] = "firefox"
                    else:
                        value["errors"] = [{"message": "source service unavailable"}]
                    with self.assertRaises(ValueError):
                        gate.validate_report(value, filename, titles)


class ClosedStudioArchiveTests(unittest.TestCase):
    """Small synthetic files exercise retention only; no service or application runs."""

    def fixture(self, directory):
        root = Path(directory) / "v"
        output, tools = root / "e", root / "tools"
        output.mkdir(parents=True)
        tools.mkdir()
        for name in ("rustc", "cargo", "node", "npm", "python"):
            (tools / (name + ".txt")).write_text("synthetic tool identity\n")
        source = {"checkout": "a" * 40, "tree": "b" * 40, "files": {"synthetic": "only"}}
        gate.write_new(output / "source-before.json", source)
        commands = {}
        for label in ("build", "ink"):
            for stream in ("stdout", "stderr"):
                (output / (label + "." + stream + ".log")).write_bytes(b"synthetic command stream\n")
            command = {"argv": ["synthetic-not-executed"], "cwd": str(root),
                       "exit_code": 0, "diagnostic_custody": "DIRECT_PROCESS_EXIT_OBSERVED",
                       "stdout": gate.digest(output / (label + ".stdout.log")),
                       "stderr": gate.digest(output / (label + ".stderr.log"))}
            gate.write_new(output / (label + ".command.json"), command)
            commands[label] = command
        gate.write_new(output / "ink.json", report())
        binary = output / "executed-studio-service"
        binary.write_bytes(b"synthetic bytes, never executed\n")
        receipt = {"status": "PASS", "source_before": source, "source_after": source,
                   "build": commands["build"], "selection_plan": gate.selection_plan("ink"),
                   "service_binary": {"path": "synthetic", **gate.digest(binary)},
                   "retained_service_binary": {"path": binary.name, **gate.digest(binary)},
                   "selections": [{"label": "ink", "status": "PASS", "expected_tests": 1,
                                   "command": commands["ink"], "original_report": gate.digest(output / "ink.json"),
                                   "verified": gate.validate_report(report(), "visual-language-draft-service.spec.ts",
                                                                    (gate.INK_TITLE,))}]}
        gate.write_new(output / "receipt.json", receipt)
        store = output / "case" / "closed-store" / "store"
        (store / "blobs" / ".staging").mkdir(parents=True)
        database = store / "synthetic.sqlite3"
        database.write_bytes(b"synthetic database byte fixture\n")
        entries = [{"path": "", "kind": "directory"}, {"path": "blobs", "kind": "directory"},
                   {"path": "blobs/.staging", "kind": "directory"},
                   {"path": database.name, "kind": "file", "sizeBytes": database.stat().st_size,
                    "sha256": gate.digest(database)["sha256"]}]
        aggregate = gate.hashlib.sha256(gate.json.dumps(
            entries, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        manifest = {"schemaVersion": 1, "entries": entries, "snapshotDirectory": str(store),
                    "databaseRelativePath": database.name, "sourceBeforeSha256": aggregate,
                    "sourceAfterSha256": aggregate, "snapshotSha256": aggregate,
                    "lifecycle": [{"exits": [{"processId": 1, "code": 0, "signal": None}], "taints": []}],
                    "evidenceScope": "synthetic retention control; not a SQLite execution"}
        (store.parent / "manifest.json").write_text(gate.json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return root, source, store

    def retained(self, root, source):
        with mock.patch.object(gate, "source_observation", return_value=source):
            with mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
                return gate.retain_closed_archive(root.parent / "g", root)

    def mutate_json(self, path, change):
        value = gate.json.loads(path.read_bytes())
        change(value)
        path.write_text(gate.json.dumps(value))

    def forged_tar(self, root, path, inventory, mutation):
        items = list(inventory["entries"].items())
        if mutation == "duplicate":
            items.append(items[0])
        if mutation == "extra":
            items.append(("e/unexpected", {"kind": "file", "mode": 0o644, "bytes": 0}))
        changed = False
        with gate.tarfile.open(path, "w", format=gate.tarfile.PAX_FORMAT) as archive:
            for name, item in items:
                if mutation == "omitted-directory" and name.endswith("/.staging"):
                    continue
                info = gate.tarfile.TarInfo("../escape" if mutation == "unsafe-name" and not changed else name)
                info.mode = item["mode"]
                info.type = gate.tarfile.DIRTYPE if item["kind"] == "directory" else gate.tarfile.REGTYPE
                data = b"" if item["kind"] == "directory" or name == "e/unexpected" else (root / name).read_bytes()
                if not changed and mutation in ("symlink", "hardlink", "fifo"):
                    info.type = {"symlink": gate.tarfile.SYMTYPE, "hardlink": gate.tarfile.LNKTYPE,
                                 "fifo": gate.tarfile.FIFOTYPE}[mutation]
                    info.linkname = "e/elsewhere" if mutation != "fifo" else ""
                if not changed and mutation == "mode":
                    info.mode ^= 1
                if item["kind"] == "file" and data and mutation in ("changed-bytes", "wrong-size") and not changed:
                    data = bytes([data[0] ^ 1]) + data[1:] if mutation == "changed-bytes" else data + b"x"
                    changed = True
                elif mutation not in ("changed-bytes", "wrong-size"):
                    changed = True
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data) if data else None)

    def test_closed_tar_preserves_original_empty_staging_and_all_bindings(self):
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, source, store = self.fixture(directory)
            before = gate.archive_inventory(root)
            result = self.retained(root, source)
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["custody"], "VERIFIED_ORIGINAL_TREE_COPY")
            self.assertFalse(result["formal_acceptance"])
            self.assertEqual(len(result["bindings"]["captures"]), 1)
            self.assertEqual(gate.archive_inventory(root), before)
            with gate.tarfile.open(root / "retained/closed-evidence.tar") as archive:
                self.assertTrue(archive.getmember("e/case/closed-store/store/blobs/.staging").isdir())
            self.assertTrue((store / "blobs/.staging").is_dir())

    def test_omitted_empty_directory_reproduces_structural_failure(self):
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, _, _ = self.fixture(directory)
            inventory = gate.archive_inventory(root)
            path = root / "omitted.tar"
            self.forged_tar(root, path, inventory, "omitted-directory")
            with self.assertRaisesRegex(ValueError, "complete member structure"):
                gate.verify_closed_tar(path, inventory)
            self.assertEqual(gate.archive_inventory(root), inventory)

    def test_archive_cannot_substitute_changed_duplicate_extra_or_wrong_size_members(self):
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, _, _ = self.fixture(directory)
            inventory = gate.archive_inventory(root)
            for mutation in ("changed-bytes", "duplicate", "extra", "wrong-size", "mode"):
                with self.subTest(mutation=mutation):
                    path = root / (mutation + ".tar")
                    self.forged_tar(root, path, inventory, mutation)
                    with self.assertRaises(ValueError):
                        gate.verify_closed_tar(path, inventory)

    def test_unsafe_tar_names_links_and_nonregular_members_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, _, _ = self.fixture(directory)
            inventory = gate.archive_inventory(root)
            for mutation in ("unsafe-name", "symlink", "hardlink", "fifo"):
                with self.subTest(mutation=mutation):
                    path = root / (mutation + ".tar")
                    self.forged_tar(root, path, inventory, mutation)
                    with self.assertRaises(ValueError):
                        gate.verify_closed_tar(path, inventory)
            for name in ("/e/x", "e/../x", "e//x", "e/./x", "e\\x", "e/a:stream",
                         "tools/NUL.txt", "e/trailing.", "e/trailing ", "e/control\nx", "other/x"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    gate.archive_member_name(name)

    def test_member_file_total_and_path_bounds_are_enforced(self):
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, _, _ = self.fixture(directory)
            for key, limit in (("members", 2), ("file_bytes", 1), ("total_bytes", 1), ("path_bytes", 2)):
                with self.subTest(bound=key), mock.patch.dict(gate.ARCHIVE_LIMITS, {key: limit}):
                    with self.assertRaises(ValueError):
                        gate.archive_inventory(root)

    def test_extended_metadata_trailing_payload_and_truncation_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, _, _ = self.fixture(directory)
            inventory = gate.archive_inventory(root)
            for mutation in ("pax", "trailing", "truncated"):
                with self.subTest(mutation=mutation):
                    path = root / (mutation + ".tar")
                    if mutation == "pax":
                        with gate.tarfile.open(path, "w") as archive:
                            info = gate.tarfile.TarInfo("metadata")
                            info.type, info.size = gate.tarfile.XHDTYPE, 5000
                            archive.addfile(info, io.BytesIO(b"x" * 5000))
                    else:
                        gate.write_closed_tar(root, path, inventory)
                        raw = path.read_bytes()
                        path.write_bytes(raw + b"extra" if mutation == "trailing" else raw[:len(raw) // 2])
                    with self.assertRaises((ValueError, gate.tarfile.TarError)):
                        gate.verify_closed_tar(path, inventory)

    def test_source_links_reparse_points_and_special_files_are_rejected(self):
        for mode, attributes in ((gate.stat.S_IFLNK, 0), (gate.stat.S_IFDIR, 0x400),
                                 (gate.stat.S_IFIFO, 0), (gate.stat.S_IFREG, 0)):
            with self.subTest(mode=mode, attributes=attributes):
                value = mock.Mock(st_mode=mode, st_file_attributes=attributes, st_nlink=2)
                with mock.patch.object(Path, "lstat", return_value=value), self.assertRaises(ValueError):
                    gate.archive_stat(Path("synthetic"))
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, _, store = self.fixture(directory)
            gate.os.link(store / "synthetic.sqlite3", root / "e" / "hard-linked-copy")
            with self.assertRaisesRegex(ValueError, "hard-linked"):
                gate.archive_inventory(root)

    def test_copy_time_input_mutation_preserves_failure_and_original_diagnostic(self):
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, source, store = self.fixture(directory)
            original = gate.write_closed_tar
            def changing(*args):
                original(*args)
                (store / "synthetic.sqlite3").write_bytes(b"changed after copy")
            with mock.patch.object(gate, "write_closed_tar", side_effect=changing):
                result = self.retained(root, source)
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")
            self.assertIn("changed during retention", result["error"])
            self.assertNotIn("archive", result)
            self.assertEqual(gate.json.loads((root / "retained/archive-receipt.json").read_bytes()), result)

    def test_source_mutation_after_copy_cannot_be_sealed(self):
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, source, _ = self.fixture(directory)
            with mock.patch.object(gate, "source_observation", side_effect=[source, {**source, "tree": "c" * 40}]):
                with mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
                    result = gate.retain_closed_archive(root.parent / "g", root)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("source changed", result["error"])
            self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")

    def test_unknown_command_or_helper_lifecycle_never_produces_sealed_custody(self):
        for mutation in ("timeout", "taint", "missing-exits", "nonzero-exit"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
                root, source, store = self.fixture(directory)
                if mutation == "timeout":
                    self.mutate_json(root / "e/receipt.json", lambda r: r["build"].update(
                        exit_code=None, diagnostic_custody="UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED"))
                else:
                    def change(manifest):
                        if mutation == "taint":
                            manifest["lifecycle"][0]["taints"] = ["synthetic unobserved child"]
                        elif mutation == "missing-exits":
                            manifest["lifecycle"][0]["exits"] = []
                        else:
                            manifest["lifecycle"][0]["exits"][0]["code"] = 7
                    self.mutate_json(store.parent / "manifest.json", change)
                result = self.retained(root, source)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")
                self.assertFalse((root / "retained/closed-evidence.tar").exists())

    def test_receipt_manifest_source_and_executable_bindings_cannot_be_substituted(self):
        for mutation in ("source", "report", "binary", "aggregate", "snapshot", "missing-tool", "missing-staging"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
                root, source, store = self.fixture(directory)
                if mutation == "source":
                    self.mutate_json(root / "e/source-before.json", lambda r: r.update(tree="c" * 40))
                elif mutation == "report":
                    self.mutate_json(root / "e/ink.json", lambda r: r["stats"].update(expected=0))
                elif mutation == "binary":
                    (root / "e/executed-studio-service").write_bytes(b"substituted bytes")
                elif mutation == "aggregate":
                    self.mutate_json(store.parent / "manifest.json", lambda r: r.update(snapshotSha256="0" * 64))
                elif mutation == "snapshot":
                    self.mutate_json(store.parent / "manifest.json", lambda r: r.update(snapshotDirectory=str(root)))
                elif mutation == "missing-tool":
                    (root / "tools/rustc.txt").unlink()
                else:
                    (store / "blobs/.staging").rmdir()
                result = self.retained(root, source)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")

    def test_long_unicode_names_retain_exact_streamed_member_bytes(self):
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, source, _ = self.fixture(directory)
            name = "e/" + "long-" * 24 + "á.json"
            (root / name).write_bytes(b"synthetic unicode-path payload")
            result = self.retained(root, source)
            self.assertEqual(result["status"], "PASS", result)
            with gate.tarfile.open(root / "retained/closed-evidence.tar") as archive:
                self.assertEqual(archive.extractfile(name).read(), b"synthetic unicode-path payload")

    def test_existing_retention_is_preserved_and_host_archive_command_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="studio-archive-control-") as directory:
            root, source, _ = self.fixture(directory)
            result = self.retained(root, source)
            self.assertEqual(result["status"], "PASS")
            original = (root / "retained/archive-receipt.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "fresh archive"):
                self.retained(root, source)
            self.assertEqual((root / "retained/archive-receipt.json").read_bytes(), original)
            with mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}), self.assertRaises(ValueError):
                gate.archive_main(["--repo", str(root.parent / "g"), "--evidence", str(root)])


if __name__ == "__main__":
    unittest.main()
