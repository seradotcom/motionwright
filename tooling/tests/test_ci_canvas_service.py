"""Negative controls for the mandatory real-service browser selection."""
import copy
import json
import io
import importlib.util
import re
from pathlib import Path
import unittest
from unittest import mock
import tempfile

PATH = Path(__file__).resolve().parents[1] / "ci_canvas_service.py"
SPEC = importlib.util.spec_from_file_location("ci_canvas_service", PATH)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def synthetic_process_fields(env=None):
    """An archive/parser fixture, never evidence that a real process executed."""
    return {"process_observation_schema": "motionwright.studio-command-process-observation/2",
            "process_boundary": "OWNED_POSIX_SESSION_GROUP", "pid": 123,
            "owned_group_after_wait": "ABSENT", "escaped_descendants": "NOT_OBSERVED",
            "selected_environment": {key: (env or {}).get(key) for key in (
                "CARGO_TARGET_DIR", "PLAYWRIGHT_JSON_OUTPUT_FILE",
                "MOTIONWRIGHT_SERVICE_TEST_BIN", "MOTIONWRIGHT_SERVICE_TEST_TMPDIR")}}


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
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS), 184)
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
             ('projection-navigation', 'project-projection-service.spec.ts'),
             ('production-design', 'production-design-service.spec.ts'),
             ('creative-workbench', 'creative-workbench-service.spec.ts'),
             ('branch-authoring', 'branch-authoring-service.spec.ts'),
             ('claim-source', 'claim-source-service.spec.ts'),
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
            'review': 1020,
            'inspector': 900,
            'profile-metadata': 1500,
            'profile-authority': 900,
            'projection-navigation': 1260,
            'production-design': 1140,
            'creative-workbench': 1260,
            'branch-authoring': 2220,
            'claim-source': 1860,
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
            outcome = mock.Mock(pid=123, wait=mock.Mock(return_value=7))
            with mock.patch.object(gate.subprocess, "Popen", return_value=outcome) as child, \
                    mock.patch.object(gate, "owned_process_group_state", return_value="ABSENT"):
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
            outcome = mock.Mock(pid=123, wait=mock.Mock(side_effect=original))
            with mock.patch.object(gate.subprocess, "Popen", return_value=outcome) as child, \
                    mock.patch.object(gate, "stop_observed_process", return_value=[]) as cleanup:
                result = gate.execute(["unused-synthetic-command"], output, {}, output, "timeout", 1)
            self.assertEqual(child.call_count, 1)
            cleanup.assert_called_once_with(outcome, gate.os.name == "posix")
            self.assertIsNone(result["exit_code"])
            self.assertEqual(result["observation"], "UNKNOWN_TIMEOUT_NO_RETRY")
            self.assertEqual(result["diagnostic_custody"], "UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED")
            self.assertEqual(result["timeout_seconds"], 1)
            self.assertNotIn("verified", result)
            self.assertTrue((output / "timeout.command.json").is_file())



class StudioProcessCustodyTests(unittest.TestCase):
    """Controlled process and filesystem faults, without running product commands."""

    def test_group_present_or_direct_only_never_grants_closed_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = mock.Mock(pid=123, wait=mock.Mock(return_value=0))
            with mock.patch.object(gate.subprocess, "Popen", return_value=process) as spawn, \
                    mock.patch.object(gate, "owned_process_group_state", return_value="PRESENT"), \
                    mock.patch.object(gate, "stop_observed_process", return_value=[]) as cleanup:
                command = gate.execute(["synthetic-unused"], root, {}, root, "group", 1)
            spawn.assert_called_once()
            self.assertEqual(command["exit_code"], 0)
            self.assertEqual(command["escaped_descendants"], "NOT_OBSERVED")
            if gate.os.name == "posix":
                self.assertEqual(command["observation"], "UNKNOWN_PROCESS_NO_RETRY")
                self.assertEqual(command["uncertainty_reason"], "OWNED_GROUP_PRESENT_AFTER_DIRECT_EXIT")
                cleanup.assert_called_once_with(process, True)
                with self.assertRaises(gate.UnknownObservation):
                    gate.require_observed_command(command)
            else:
                cleanup.assert_not_called()
                self.assertEqual(command["process_boundary"], "DIRECT_PROCESS_ONLY")
                with self.assertRaises(ValueError):
                    gate.require_observed_command(command)

    def test_post_launch_wait_error_retains_unknown_and_handle_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = mock.Mock(pid=123, wait=mock.Mock(side_effect=OSError("controlled wait fault")))
            with mock.patch.object(gate.subprocess, "Popen", return_value=process) as spawn, \
                    mock.patch.object(gate, "stop_observed_process", return_value=["controlled cleanup failure"]):
                command = gate.execute(["synthetic-unused"], root, {}, root, "wait", 1)
            spawn.assert_called_once()
            self.assertEqual(command["pid"], 123)
            self.assertEqual(command["observation"], "UNKNOWN_PROCESS_NO_RETRY")
            self.assertEqual(command["uncertainty_reason"], "POST_LAUNCH_OBSERVATION_ERROR")
            self.assertIn("controlled cleanup failure", command["cleanup_errors"])
            self.assertEqual(gate.json.loads((root / "wait.command.json").read_bytes()), command)

    def test_spawn_handle_unknown_survives_stream_close_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_open = Path.open

            class BrokenClose:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self.stream.__enter__()
                def __exit__(self, *args):
                    self.stream.__exit__(*args)
                    raise OSError("controlled close failure")

            def opened(path, *args, **kwargs):
                stream = original_open(path, *args, **kwargs)
                return BrokenClose(stream) if path.name == "spawn.stdout.log" and args == ("xb",) else stream

            with mock.patch.object(gate.subprocess, "Popen", side_effect=OSError("controlled handle uncertainty")) as spawn, \
                    mock.patch.object(Path, "open", new=opened), \
                    mock.patch.object(gate, "stop_observed_process") as cleanup:
                command = gate.execute(["synthetic-unused"], root, {}, root, "spawn", 1)
            spawn.assert_called_once()
            cleanup.assert_not_called()
            self.assertIsNone(command["pid"])
            self.assertEqual(command["observation"], "UNKNOWN_PROCESS_NO_RETRY")
            self.assertEqual(command["uncertainty_reason"], "SPAWN_HANDLE_NOT_OBSERVED")
            self.assertIn("controlled close failure", command["secondary_observation_errors"][0])
            self.assertEqual(command["diagnostic_custody"], "UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED")

    def test_stream_fingerprint_failure_retains_observed_exit_as_unknown_custody(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = mock.Mock(pid=123, wait=mock.Mock(return_value=7))
            with mock.patch.object(gate.subprocess, "Popen", return_value=process), \
                    mock.patch.object(gate, "owned_process_group_state", return_value="ABSENT"), \
                    mock.patch.object(gate, "archive_file_digest", side_effect=OSError("controlled changing stream")):
                command = gate.execute(["synthetic-unused"], root, {}, root, "stream", 1)
            self.assertEqual(command["exit_code"], 7)
            self.assertEqual(command["observation"], "UNKNOWN_CUSTODY_NO_RETRY")
            self.assertIn("controlled changing stream", command["stdout"]["custody_error"])
            with self.assertRaises(gate.UnknownObservation):
                gate.require_observed_command(command)

    def test_receipt_persistence_fault_keeps_initial_unknown_and_does_not_rewrite_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def broken_write(path, value):
                path.write_bytes(b'{"partial":')
                raise OSError("controlled receipt interruption")

            with mock.patch.object(gate.subprocess, "Popen", side_effect=OSError("controlled handle uncertainty")) as spawn, \
                    mock.patch.object(gate, "write_new", side_effect=broken_write) as persist, \
                    mock.patch.object(gate, "stop_observed_process") as cleanup, \
                    self.assertRaises(gate.UnknownObservation) as caught:
                gate.execute(["synthetic-unused"], root, {}, root, "receipt", 1)
            spawn.assert_called_once()
            persist.assert_called_once()
            cleanup.assert_not_called()
            command = caught.exception.command
            self.assertEqual(command["observation"], "UNKNOWN_PROCESS_NO_RETRY")
            self.assertEqual(command["uncertainty_reason"], "SPAWN_HANDLE_NOT_OBSERVED")
            self.assertIn("controlled receipt interruption", command["receipt_error"])
            self.assertEqual((root / "receipt.command.json").read_bytes(), b'{"partial":')

    def test_reader_requires_owned_boundary_schema_pid_and_absence(self):
        valid = {**synthetic_process_fields(), "exit_code": 0, "diagnostic_custody": "DIRECT_PROCESS_EXIT_OBSERVED"}
        gate.require_observed_command(valid)
        for change in ({"process_boundary": "DIRECT_PROCESS_ONLY"}, {"pid": True}, {"pid": 0},
                       {"owned_group_after_wait": "PRESENT"}, {"owned_group_after_wait": "NOT_OBSERVED"},
                       {"escaped_descendants": "ABSENT"}, {"process_observation_schema": "legacy"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                gate.require_observed_command({**valid, **change})

    def test_service_consumer_stops_on_build_or_selection_unknown_without_postqueries(self):
        for phase in ("build", "ink"):
            for receipt_error in (False, True):
                with self.subTest(phase=phase, receipt_error=receipt_error), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    repo, output, target = root / "g", root / "v/e", root / "v/t"
                    cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
                    cli.parent.mkdir(parents=True)
                    cli.write_bytes(b"synthetic CLI; never executed")
                    for name in ("cargo", "node"):
                        (repo / name).write_bytes(b"synthetic tool; never executed")
                    source = {"checkout": "a" * 40, "files": {"synthetic": "only"}}
                    calls = []

                    def execute(argv, cwd, env, destination, label, timeout):
                        calls.append(label)
                        command = {"argv": argv, "cwd": str(cwd), "exit_code": 0,
                                   **synthetic_process_fields(env),
                                   "diagnostic_custody": "DIRECT_PROCESS_EXIT_OBSERVED"}
                        self.assertEqual(env.get("CARGO_TARGET_DIR"), str(target))
                        if label == "build":
                            self.assertNotIn("PLAYWRIGHT_JSON_OUTPUT_FILE", env)
                            self.assertNotIn("MOTIONWRIGHT_SERVICE_TEST_BIN", env)
                            self.assertNotIn("MOTIONWRIGHT_SERVICE_TEST_TMPDIR", env)
                        if label == phase:
                            command.update(observation="UNKNOWN_PROCESS_NO_RETRY", uncertainty_reason="controlled uncertainty",
                                           diagnostic_custody="UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED")
                            if receipt_error:
                                raise gate.UnknownObservation("controlled receipt uncertainty", command)
                        else:
                            binary = target / "debug/examples/studio_review_session"
                            if gate.os.name == "nt": binary = binary.with_suffix(".exe")
                            binary.parent.mkdir(parents=True)
                            binary.write_bytes(b"synthetic fixture; never product execution")
                        return command

                    argv = ["ci_canvas_service.py", "--repo", str(repo), "--output", str(output),
                            "--target", str(target), "--selection", "ink"]
                    with mock.patch("sys.argv", argv), mock.patch("sys.stdout", new=io.StringIO()), \
                            mock.patch.object(gate, "source_observation", return_value=source) as source_query, \
                            mock.patch.object(gate.shutil, "which", side_effect=lambda name: str(repo / name)), \
                            mock.patch.object(gate, "execute", side_effect=execute), \
                            mock.patch.dict(gate.os.environ, {key: "forbidden inherited authority" for key in gate.PROCESS_ENVIRONMENT_KEYS}):
                        code = gate.main()
                    receipt = gate.json.loads((output / "receipt.json").read_bytes())
                    self.assertEqual(code, 1)
                    self.assertEqual(receipt["status"], "UNKNOWN", receipt)
                    self.assertEqual(receipt["terminal_custody"], "UNSEALED_NO_FURTHER_COMMANDS")
                    self.assertEqual(calls, ["build"] if phase == "build" else ["build", "ink"])
                    self.assertEqual(source_query.call_count, 1 if phase == "build" else 2)
                    self.assertNotIn("source_after", receipt)
                    self.assertFalse((output / "ink.json").exists())


class SelectedServiceScopeTests(unittest.TestCase):
    def test_narrative_scope_preserves_full_default_and_declares_every_omission(self):
        plan = gate.selection_plan("narrative")
        self.assertEqual(plan["required_tests"], 8)
        self.assertEqual(plan["full_default_tests"], 184)
        self.assertEqual(plan["scope"], "EXPLICIT_SELECTED_SERVICE_CASES")
        self.assertEqual(len(plan["required"]), 1)
        self.assertEqual(plan["required"][0]["file"], "narrative-service.spec.ts")
        self.assertEqual(plan["required"][0]["titles"], list(gate.NARRATIVE_TITLES))
        self.assertEqual(sum(len(row["titles"]) for row in plan["not_selected"]), 176)
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
            ("review", "review-service.spec.ts", 7),
        ])
        self.assertEqual(plan["required_tests"], 39)
        self.assertEqual(plan["full_default_tests"], 184)
        self.assertEqual(sum(len(x["titles"]) for x in plan["not_selected"]), 145)
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
                                 184 - len(titles))
                verified = gate.validate_report(selection_report(filename, titles), filename, titles)
                self.assertEqual(verified["passed"], len(titles))
                cases.extend((filename, title) for title in verified["titles"])
                per_case = 90 if label == "delivery-recipe" else 120
                self.assertGreater(gate.SELECTION_TIMEOUTS[label], len(titles) * per_case + 120)
        self.assertEqual(len(cases), 39)
        self.assertEqual(len(set(cases)), 39)

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
        self.assertEqual(plan["required_tests"], 184)
        self.assertEqual(plan["not_selected"], [])
        cases = [(x["file"], title) for x in plan["required"] for title in x["titles"]]
        self.assertEqual(len(cases), 184)
        self.assertEqual(len(set(cases)), 184)
        self.assertEqual(gate.selected_selections("all"), gate.SELECTIONS)



class CompleteAuthoringScopeTests(unittest.TestCase):
    def test_complete_authoring_requires_all_eight_files_and_sixty_four_cases(self):
        plan = gate.selection_plan("complete-authoring")
        self.assertEqual([(x["label"], x["file"], len(x["titles"])) for x in plan["required"]], [
            ("beat-draft", "scene-beat-draft-service.spec.ts", 11),
            ("beat-time", "scene-beat-time-service.spec.ts", 4),
            ("delivery-recipe", "delivery-recipe.spec.ts", 12),
            ("recipe-authority", "delivery-recipe-authority-service.spec.ts", 5),
            ("review", "review-service.spec.ts", 7),
            ("inspector", "scene-inspector-service.spec.ts", 6),
            ("profile-metadata", "delivery-profile-preservation.spec.ts", 14),
            ("profile-authority", "delivery-profile-authority-service.spec.ts", 6),
        ])
        self.assertEqual(plan["required_tests"], 65)
        self.assertEqual(plan["full_default_tests"], 184)
        self.assertEqual(sum(len(x["titles"]) for x in plan["not_selected"]), 119)
        self.assertEqual({x["label"] for x in plan["not_selected"]},
                         {row[0] for row in gate.SELECTIONS[:10] + gate.SELECTIONS[18:]})
        self.assertTrue(all(x["status"] == "NOT_SELECTED" and x["technical_credit"] == "NONE"
                            for x in plan["not_selected"]))
        self.assertFalse(plan["formal_acceptance"])
        self.assertEqual(gate.selection_plan("authoring-drafts")["required_tests"], 39)
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS[:15]), 104)

    def test_inspector_and_profile_results_require_exact_files_names_and_one_original_result(self):
        observed = []
        for label, filename, titles in gate.SELECTIONS[15:18]:
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
        for label, filename, titles in gate.SELECTIONS[15:18]:
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



class ProjectionNavigationScopeTests(unittest.TestCase):
    REQUIRED_SOURCE_PATHS = (
        'apps/studio/index.html',
        'apps/studio/vite.config.ts',
        'apps/studio/tsconfig.json',
        'apps/studio/tsconfig.app.json',
        'apps/studio/src/main.tsx',
        'apps/studio/src/styles.css',
        'apps/studio/src/fixture.ts',
        'apps/studio/src/ProjectLibraryDialog.tsx',
        'apps/studio/src/projectLibrary.ts',
        'apps/studio/src/project-library.css',
        'apps/studio/src/ProjectProjectionWorkspace.tsx',
        'apps/studio/src/projectProjection.ts',
        'apps/studio/src/projectProjection.test.ts',
        'apps/studio/src/projectMutation.test.ts',
        'apps/studio/tests/project-projection-service.spec.ts',
        'apps/studio/tests/project-projection.spec.ts',
        'apps/studio/tests/fixtures/project-projection.html',
        'apps/studio/tests/fixtures/project-projection.tsx',
        'apps/desktop/src-tauri/src/effect_grants.rs',
        'apps/desktop/src-tauri/src/project_library.rs',
        'apps/desktop/src-tauri/src/project_projection.rs',
        'apps/desktop/src-tauri/src/caption_export.rs',
        'apps/desktop/src-tauri/src/file_import.rs',
        'crates/domain/src/lib.rs',
        'crates/domain/src/canvas.rs',
        'crates/domain/src/history.rs',
        'crates/domain/src/creative.rs',
        'crates/service/src/lib.rs',
        'crates/service/src/project_creation.rs',
        'crates/service/src/project_projection.rs',
        'crates/storage/src/lib.rs',
        'crates/storage/src/project_creation.rs',
        'crates/storage/src/project_projection.rs',
        'apps/studio/src/App.tsx',
        'apps/studio/src/api.ts',
        'apps/studio/src/types.ts',
        'apps/studio/src/projectMutation.ts',
        'apps/studio/tests/support/scopedChangeService.ts',
        'crates/service/examples/studio_review_session.rs',
        'Cargo.lock',
        'SOURCE_LOCK.json',
        'apps/studio/package-lock.json',
        'apps/studio/package.json',
    )

    def selection(self):
        chosen = gate.selected_selections("projection-navigation")
        self.assertEqual(len(chosen), 1)
        return chosen[0]

    def test_projection_scope_uses_the_six_real_source_cases_and_declares_every_omission(self):
        label, filename, titles = self.selection()
        source = (PATH.parents[1] / "apps/studio/tests" / filename).read_text(encoding="utf-8")
        declared = tuple(re.findall(r'test\("([^"]+)",', source))
        self.assertEqual(len(declared), 6)
        self.assertEqual(len(set(declared)), 6)
        self.assertEqual(titles, declared)
        self.assertEqual(filename, "project-projection-service.spec.ts")
        plan = gate.selection_plan(label)
        self.assertEqual(plan["required_tests"], 6)
        self.assertEqual(plan["full_default_tests"], 184)
        self.assertEqual(plan["required"], [{"label": label, "file": filename, "titles": list(declared)}])
        omitted = [(row["file"], title) for row in plan["not_selected"] for title in row["titles"]]
        self.assertEqual(len(omitted), 178)
        self.assertEqual(len(set(omitted)), 178)
        self.assertTrue(all(row["status"] == "NOT_SELECTED" and row["technical_credit"] == "NONE"
                            for row in plan["not_selected"]))
        self.assertFalse(plan["formal_acceptance"])
        verified = gate.validate_report(selection_report(filename, declared), filename, titles)
        self.assertEqual(verified, {"passed": 6, "skipped": 0, "retries": 0, "titles": list(declared)})
        self.assertEqual(gate.selection_plan("complete-authoring")["required_tests"], 65)
        self.assertEqual(gate.selection_plan("authoring-drafts")["required_tests"], 39)

    def test_projection_success_totals_cannot_hide_missing_extra_duplicate_or_foreign_cases(self):
        _, filename, titles = self.selection()
        for mutation in ("missing", "extra", "duplicate", "foreign-title", "component-file"):
            with self.subTest(mutation=mutation):
                value = selection_report(filename, titles)
                rows = value["suites"][0]["suites"][0]["specs"]
                if mutation == "missing":
                    rows.pop()
                elif mutation == "extra":
                    extra = copy.deepcopy(rows[0])
                    extra["title"] = "undeclared projection case"
                    rows.append(extra)
                elif mutation == "duplicate":
                    rows[-1] = copy.deepcopy(rows[0])
                elif mutation == "foreign-title":
                    rows[-1]["title"] = gate.INK_TITLE
                else:
                    rows[-1]["file"] = "project-projection.spec.ts"
                with self.assertRaises(ValueError):
                    gate.validate_report(value, filename, titles)

    def test_projection_discovery_skips_retries_and_nonoriginal_results_never_pass(self):
        _, filename, titles = self.selection()
        for mutation in ("discovery", "skip", "retry", "second-result", "missing-result",
                         "expected-failure", "error", "failed", "foreign-browser",
                         "missing-counter", "zero", "flaky", "global-error"):
            with self.subTest(mutation=mutation):
                value = selection_report(filename, titles)
                case = value["suites"][0]["suites"][0]["specs"][-1]["tests"][0]
                if mutation == "discovery":
                    for row in value["suites"][0]["suites"][0]["specs"]:
                        row["tests"][0].update(expectedStatus="skipped", status="skipped", results=[])
                    value["stats"].update(expected=0, skipped=6)
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
                elif mutation == "error":
                    case["results"][0]["errors"] = [{"message": "actual projection service failure"}]
                elif mutation == "failed":
                    case["results"][0]["status"] = "failed"
                elif mutation == "foreign-browser":
                    case["projectId"] = "firefox"
                elif mutation == "missing-counter":
                    del value["stats"]["skipped"]
                elif mutation == "zero":
                    value["stats"]["expected"] = 0
                elif mutation == "flaky":
                    value["stats"]["flaky"] = 1
                else:
                    value["errors"] = [{"message": "source-built service unavailable"}]
                with self.assertRaises(ValueError):
                    gate.validate_report(value, filename, titles)

    def test_projection_deadline_covers_actual_case_bounds_and_workflow_remains_manual(self):
        label, filename, titles = self.selection()
        source = (PATH.parents[1] / "apps/studio/tests" / filename).read_text(encoding="utf-8")
        declared = re.findall(r"test\.setTimeout\(([0-9_]+)\)", source)
        self.assertEqual(len(declared), 1)
        per_case_ms = int(declared[0].replace("_", ""))
        self.assertEqual(per_case_ms, 180_000)
        self.assertGreater(gate.SELECTION_TIMEOUTS[label], len(titles) * per_case_ms / 1000 + 120)
        workflow = (PATH.parents[1] / ".github/workflows/studio-service-verification.yml").read_text(encoding="utf-8")
        declared_options = re.findall(r"options: \[([^\]]+)\]", workflow)
        self.assertEqual(len(declared_options), 1)
        choices = [item.strip() for item in declared_options[0].split(",")]
        self.assertEqual(len(choices), len(set(choices)))
        self.assertEqual(set(choices), {gate.STUDIO68_SCOPE, gate.PARTIAL_ORACLE_SCOPE, "all", *gate.SELECTION_GROUPS, *(row[0] for row in gate.SELECTIONS), *gate.BROWSER_SCOPES})
        service_job = workflow.split("  real-service:\n", 1)[1].split("  studio-service-68:\n", 1)[0]
        conditions = re.findall(r"^    if: (.+)$", service_job, re.MULTILINE)
        self.assertEqual(conditions, ["github.event_name == 'workflow_dispatch' && inputs.selection != 'studio-service-68'"])
        self.assertIn("--selection \"$REQUESTED_SELECTION\"", service_job)

    def source_fixture(self, repo):
        initializing = [True]

        def content(name):
            return ("committed source: " + name + "\n").encode("utf-8")

        def git(arguments, *, cwd, timeout):
            self.assertEqual(Path(cwd), repo)
            self.assertEqual(timeout, 30)
            if arguments == ["git", "rev-parse", "HEAD"]:
                return b"a" * 40
            if arguments == ["git", "rev-parse", "HEAD^{tree}"]:
                return b"b" * 40
            if arguments == ["git", "diff", "--name-only", "HEAD", "--"]:
                # Model a clean index even when physical bytes are hidden by
                # assume-unchanged or skip-worktree flags.
                return b""
            self.assertEqual(arguments[:2], ["git", "show"])
            head, separator, name = arguments[2].partition(":")
            self.assertEqual((head, separator), ("a" * 40, ":"))
            value = content(name)
            if initializing[0]:
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
            return value

        with mock.patch.object(gate.subprocess, "check_output", side_effect=git), \
                mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
            before = gate.source_observation(repo)
        initializing[0] = False
        for name in self.REQUIRED_SOURCE_PATHS:
            path = repo / name
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content(name))
        return git, before

    def test_projection_physical_changes_fail_even_when_git_reports_a_clean_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            git, before = self.source_fixture(repo)
            with mock.patch.object(gate.subprocess, "check_output", side_effect=git), \
                    mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
                self.assertEqual(gate.source_observation(repo), before)
                for name in self.REQUIRED_SOURCE_PATHS:
                    with self.subTest(source=name):
                        path = repo / name
                        original = path.read_bytes()
                        try:
                            path.write_bytes(original + b"hidden physical change")
                            with self.assertRaisesRegex(ValueError, "source differs from Git"):
                                gate.source_observation(repo)
                        finally:
                            path.write_bytes(original)

    def test_required_projection_source_absence_cannot_be_omitted_from_custody(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            git, before = self.source_fixture(repo)
            with mock.patch.object(gate.subprocess, "check_output", side_effect=git), \
                    mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
                self.assertEqual(gate.source_observation(repo), before)
                for name in self.REQUIRED_SOURCE_PATHS:
                    with self.subTest(source=name):
                        path = repo / name
                        original = path.read_bytes()
                        try:
                            path.unlink()
                            with self.assertRaisesRegex(ValueError, "regular file required"):
                                gate.source_observation(repo)
                        finally:
                            path.write_bytes(original)


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
        repo, target = root.parent / "g", root / "t"
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_bytes(b"synthetic CLI identity, never executed\n")
        for name in ("cargo", "node"):
            (repo / name).write_bytes(b"synthetic tool identity, never executed\n")
        cargo, node = (gate.observed_tool(repo / name) for name in ("cargo", "node"))
        binary_path = target / "debug/examples/studio_review_session"
        binary_path.parent.mkdir(parents=True)
        binary_path.write_bytes(b"synthetic bytes, never executed\n")
        commands = {}
        for label in ("build", "ink"):
            for stream in ("stdout", "stderr"):
                (output / (label + "." + stream + ".log")).write_bytes(b"synthetic command stream\n")
            argv = ([cargo["path"], "build", "--locked", "-p", "motionwright-service",
                     "--example", "studio_review_session"] if label == "build" else
                    gate.service_command(node["path"], cli, output, gate.selected_selections("ink")[0]))
            env = (gate.service_environment_binding(target) if label == "build" else
                   gate.service_environment_binding(target, output, label, binary_path))
            command = {"argv": argv, "cwd": str(repo if label == "build" else repo / "apps/studio"),
                       **synthetic_process_fields(env),
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
                   "cargo": cargo, "node": node, "target_directory": str(target),
                   "playwright_cli": gate.digest(cli),
                   "service_binary": {"path": str(binary_path), **gate.digest(binary)},
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

    def test_unknown_original_is_quarantined_before_inventory_source_commands_or_copy(self):
        for mutation in ("receipt", "command"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root, _, _ = self.fixture(directory)
                path = root / "e/receipt.json"
                receipt = gate.json.loads(path.read_bytes())
                command = {"observation": "UNKNOWN_PROCESS_NO_RETRY", "pid": 123,
                           "diagnostic_custody": "UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED"}
                if mutation == "receipt":
                    receipt.update(status="UNKNOWN", unsealed_command=command)
                else:
                    receipt["selections"][0]["command"] = command
                path.write_text(gate.json.dumps(receipt))
                with mock.patch.object(gate, "archive_inventory", side_effect=OSError("changing original streams")) as inventory, \
                        mock.patch.object(gate, "source_observation") as source, \
                        mock.patch.object(gate, "write_closed_tar") as copy_tar:
                    result = gate.retain_closed_archive(root.parent / "g", root)
                self.assertEqual(result["status"], "UNKNOWN", result)
                self.assertEqual(result["unsealed_command"], command)
                self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")
                inventory.assert_not_called()
                source.assert_not_called()
                copy_tar.assert_not_called()
                self.assertFalse((root / "retained/closed-evidence.tar").exists())

    def test_coherent_original_command_mutations_cannot_change_service_invocation_authority(self):
        changes = ("build-argv", "build-cwd", "build-executable", "build-target", "build-inherited-report",
                   "case-file", "case-cwd", "case-executable", "case-binary", "case-store", "case-report",
                   "case-target", "extra-environment", "missing-environment", "target-root", "binary-path",
                   "cargo-bytes", "node-bytes", "cli-bytes")
        for mutation in changes:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root, source, _ = self.fixture(directory)
                output, repo = root / "e", root.parent / "g"
                receipt = gate.json.loads((output / "receipt.json").read_bytes())
                build, case = receipt["build"], receipt["selections"][0]["command"]
                if mutation == "build-argv": build["argv"].append("--release")
                elif mutation == "build-cwd": build["cwd"] = str(root)
                elif mutation == "build-executable": build["argv"][0] = str(repo / "other-cargo")
                elif mutation == "build-target": build["selected_environment"]["CARGO_TARGET_DIR"] = str(root / "other-target")
                elif mutation == "build-inherited-report": build["selected_environment"]["PLAYWRIGHT_JSON_OUTPUT_FILE"] = "inherited"
                elif mutation == "case-file": case["argv"][3] = "tests/other-service.spec.ts"
                elif mutation == "case-cwd": case["cwd"] = str(repo)
                elif mutation == "case-executable": case["argv"][0] = str(repo / "other-node")
                elif mutation == "case-binary": case["selected_environment"]["MOTIONWRIGHT_SERVICE_TEST_BIN"] = str(root / "other-binary")
                elif mutation == "case-store": case["selected_environment"]["MOTIONWRIGHT_SERVICE_TEST_TMPDIR"] = str(root / "other-stores")
                elif mutation == "case-report": case["selected_environment"]["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / "other.json")
                elif mutation == "case-target": case["selected_environment"]["CARGO_TARGET_DIR"] = str(root / "other-target")
                elif mutation == "extra-environment": case["selected_environment"]["UNDECLARED_AUTHORITY"] = "injected"
                elif mutation == "missing-environment": case.pop("selected_environment")
                elif mutation == "target-root": receipt["target_directory"] = str(root / "target")
                elif mutation == "binary-path": receipt["service_binary"]["path"] = str(root / "wrong-binary")
                elif mutation == "cargo-bytes": (repo / "cargo").write_bytes(b"changed synthetic cargo")
                elif mutation == "node-bytes": (repo / "node").write_bytes(b"changed synthetic node")
                elif mutation == "cli-bytes": (repo / "apps/studio/node_modules/@playwright/test/cli.js").write_bytes(b"changed synthetic CLI")
                # Both retained originals agree: rejection must be invocation authority,
                # not a receipt-versus-command.json byte mismatch.
                (output / "build.command.json").write_text(gate.json.dumps(build))
                (output / "ink.command.json").write_text(gate.json.dumps(case))
                (output / "receipt.json").write_text(gate.json.dumps(receipt))
                result = self.retained(root, source)
                self.assertEqual(result["status"], "FAIL", result)
                self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")
                self.assertFalse((root / "retained/closed-evidence.tar").exists())

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
                        exit_code=None, observation="TIMED_OUT_NO_RETRY",
                        diagnostic_custody="UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED"))
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
                self.assertEqual(result["status"], "UNKNOWN" if mutation == "timeout" else "FAIL")
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
            name = "e/" + "long-" * 24 + "Ã¡.json"
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


class BrowserContractSelectionTests(unittest.TestCase):
    """Synthetic process and archive controls; no browser, Node or service runs."""

    def discovery_document(self, scope="browser-contracts"):
        selected, excluded = gate.browser_discovery_inventory(scope)
        suites = {}
        for filename, title in sorted(selected | excluded):
            path = gate.browser_full_title_path(filename, title)[1:-1]
            current = suites
            for part in path:
                current = current.setdefault(part, {"specs": [], "children": {}})["children"]
            current = suites
            node = None
            for part in path:
                node = current[part]
                current = node["children"]
            node["specs"].append({"file": filename, "title": title, "ok": True, "tags": [],
                                 "tests": [{"projectId": "chromium", "projectName": "chromium",
                                            "expectedStatus": "skipped" if (filename, title) in excluded else "passed",
                                            "status": "skipped", "results": []}]})

        def materialize(items):
            return [{"title": name, "specs": row["specs"], "suites": materialize(row["children"])}
                    for name, row in items.items()]

        return {"errors": [], "stats": {"expected": 0, "skipped": len(selected | excluded), "unexpected": 0, "flaky": 0},
                "suites": materialize(suites)}

    def executed_document(self, filename, titles, scope="browser-contracts"):
        value = self.discovery_document(scope)

        def selected(suites):
            result = []
            for suite in suites:
                specs = [spec for spec in suite["specs"] if spec["file"] == filename and spec["title"] in titles]
                children = selected(suite["suites"])
                for spec in specs:
                    case = spec["tests"][0]
                    case.update(expectedStatus="passed", status="expected",
                                results=[{"status": "passed", "retry": 0, "errors": []}])
                if specs or children:
                    result.append({"title": suite["title"], "specs": specs, "suites": children})
            return result

        value["suites"] = selected(value["suites"])
        value["stats"] = {"expected": len(titles), "skipped": 0, "unexpected": 0, "flaky": 0}
        return value

    def fixture(self, directory, mutation=None, scope="browser-contracts"):
        repo, root = Path(directory) / "g", Path(directory) / "v"
        repo.mkdir()
        tools = root / "tools"
        tools.mkdir(parents=True)
        for name in ("node", "npm", "python"):
            (tools / (name + ".txt")).write_bytes(b"synthetic identity, never executed\n")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_bytes(b"synthetic CLI, never executed\n")
        node = repo / "synthetic-node"
        node.write_bytes(b"synthetic Node identity, never executed\n")
        output, target = root / "e", root / "t"
        source = {"checkout": "a" * 40, "tree": "b" * 40, "files": {"synthetic": "only"}}
        calls = []

        def which(name):
            self.assertEqual(name, "node", "browser mode must not resolve Cargo")
            return str(node)

        def execute(argv, cwd, env, destination, label, timeout):
            self.assertEqual(destination, output)
            self.assertEqual(cwd, repo / "apps/studio")
            self.assertNotIn("MOTIONWRIGHT_SERVICE_TEST_BIN", env)
            self.assertNotIn("MOTIONWRIGHT_SERVICE_TEST_TMPDIR", env)
            self.assertFalse(target.exists())
            calls.append((label, argv, timeout))
            if label == "browser-discovery":
                document = self.discovery_document(scope)
                if mutation == "discovery-mismatch":
                    document["suites"][0]["specs"][0]["title"] = "unknown injected case"
            else:
                row = next(row for row in gate.browser_selections(scope) if row[0] == label)
                document = self.executed_document(row[1], row[2], scope)
            gate.write_new(Path(env["PLAYWRIGHT_JSON_OUTPUT_FILE"]), document)
            for stream in ("stdout", "stderr"):
                (output / (label + "." + stream + ".log")).write_bytes(b"synthetic original stream\n")
            timed_out = mutation == "timeout" and label != "browser-discovery"
            command = {"argv": argv, "cwd": str(cwd), "exit_code": None if timed_out else 0,
                       **synthetic_process_fields(env),
                       "diagnostic_custody": ("UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED"
                                               if timed_out else "DIRECT_PROCESS_EXIT_OBSERVED"),
                       "stdout": gate.digest(output / (label + ".stdout.log")),
                       "stderr": gate.digest(output / (label + ".stderr.log"))}
            if timed_out:
                command.update(observation="TIMED_OUT_NO_RETRY", timeout_seconds=timeout)
            gate.write_new(output / (label + ".command.json"), command)
            return command

        with mock.patch.object(gate, "source_observation", return_value=source) as source_query, \
                mock.patch.object(gate.shutil, "which", side_effect=which), \
                mock.patch.object(gate, "execute", side_effect=execute), \
                mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false",
                                                "MOTIONWRIGHT_SERVICE_TEST_BIN": "forbidden-inherited-binary",
                                                "MOTIONWRIGHT_SERVICE_TEST_TMPDIR": "forbidden-inherited-store"}), \
                mock.patch("sys.stdout", new=io.StringIO()):
            code = gate.run_browser_selection(repo, output, target, scope)
        if mutation == "timeout":
            self.assertEqual(source_query.call_count, 1, "UNKNOWN must prevent source_after subprocesses")
        receipt = gate.json.loads((output / "receipt.json").read_bytes())
        return repo, root, source, receipt, calls, code

    def retain(self, repo, root, source):
        with mock.patch.object(gate, "source_observation", return_value=source), \
                mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
            return gate.retain_closed_archive(repo, root)

    def test_browser_scope_is_closed_and_historical_service_groups_are_unchanged(self):
        rows = gate.selected_selections("browser-contracts")
        self.assertEqual(len(rows), 33)
        self.assertEqual(sum(len(row[2]) for row in rows), 206)
        self.assertEqual(sum(len(row[2]) for row in gate.selected_selections("all")), 184)
        self.assertEqual(gate.selection_plan("complete-authoring")["required_tests"], 65)
        self.assertEqual(gate.selection_plan("authoring-drafts")["required_tests"], 39)
        plan = gate.selection_plan("browser-contracts")
        self.assertEqual(plan["scope"], "EXPLICIT_SYNTHETIC_BROWSER_CONTRACTS")
        self.assertEqual(sum(len(row["titles"]) for row in plan["not_selected"]), 184 + 29 + 60)
        self.assertEqual(len(plan["excluded_service_cases"]), 4)
        self.assertTrue(all(row["technical_credit"] == "NONE"
                            for row in plan["not_selected"] + plan["excluded_service_cases"]))
        for choice in ("browser", "browser-contracts,all", "browser-contracts ", "", None):
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                gate.selected_selections(choice)
        with mock.patch.object(gate, "BROWSER_SELECTIONS", gate.BROWSER_SELECTIONS[:-1]):
            with self.assertRaises(ValueError):
                gate.selected_selections("browser-contracts")

    def test_browser_full_title_filter_excludes_four_real_service_cases(self):
        document = self.discovery_document()
        self.assertEqual(gate.validate_browser_discovery(document),
                         {"status": "LISTED_NOT_EXECUTED", "listed": 210, "selected": 206,
                          "excluded_service": 4, "execution_results": 0})
        selected, excluded = gate.browser_discovery_inventory()
        for row in gate.BROWSER_SELECTIONS:
            argv = gate.browser_command("node", Path("cli"), Path("output"), row)
            pattern = re.compile(argv[argv.index("--grep") + 1])
            for title in row[2]:
                self.assertRegex(" ".join(gate.browser_full_title_path(row[1], title)), pattern)
            for pair in excluded:
                self.assertIsNone(pattern.search(" ".join(gate.browser_full_title_path(*pair))))
        nested_title = "a successful create preserves later selection and text"
        self.assertEqual(gate.browser_full_title_path("project-mutation.spec.ts", nested_title),
                         ("chromium", "project-mutation.spec.ts",
                          "Canvas interaction during delayed creation \u2014 synthetic UI contract", nested_title))
        for mutation in ("omission", "duplicate", "wrong-describe", "executed", "skip-selected"):
            changed = copy.deepcopy(document)
            suite = changed["suites"][0]
            spec = suite["specs"][0]
            if mutation == "omission":
                suite["specs"].pop()
            elif mutation == "duplicate":
                suite["specs"].append(copy.deepcopy(spec))
            elif mutation == "wrong-describe":
                suite["title"] = "wrong suite"
            elif mutation == "executed":
                spec["tests"][0]["results"] = [{"status": "passed", "retry": 0}]
            else:
                spec["tests"][0]["expectedStatus"] = "skipped"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_browser_discovery(changed)

    def test_browser_execution_never_builds_or_inherits_real_service_authority(self):
        with tempfile.TemporaryDirectory(prefix="browser-control-") as directory:
            repo, root, source, receipt, calls, code = self.fixture(directory)
            self.assertEqual(code, 0, receipt)
            self.assertEqual(len(calls), 34)
            self.assertEqual(calls[0][0], "browser-discovery")
            self.assertIn("--list", calls[0][1])
            self.assertFalse((root / "t").exists())
            self.assertFalse(any(path.name.endswith("-stores") for path in (root / "e").iterdir()))
            self.assertFalse({"build", "service_binary", "retained_service_binary"}.intersection(receipt))
            self.assertEqual(receipt["service_runtime"], "NOT_RUN")
            self.assertEqual(receipt["discovery"]["status"], "LISTED_NOT_EXECUTED")
        with tempfile.TemporaryDirectory(prefix="browser-control-") as directory:
            _, _, _, receipt, calls, code = self.fixture(directory, "discovery-mismatch")
            self.assertEqual(code, 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(receipt["selections"], [])

    def test_browser_uses_original_report_validator_and_timeout_certainty(self):
        with tempfile.TemporaryDirectory(prefix="browser-control-") as directory, \
                mock.patch.object(gate, "validate_report", wraps=gate.validate_report) as validate:
            _, _, _, receipt, _, code = self.fixture(directory)
            self.assertEqual(code, 0, receipt)
            self.assertEqual(validate.call_count, 33)
        filename, titles = gate.BROWSER_SELECTIONS[0][1:]
        for mutation in ("skip", "retry", "duplicate", "missing"):
            changed = selection_report(filename, titles)
            rows = changed["suites"][0]["suites"][0]["specs"]
            if mutation == "skip":
                changed["stats"]["skipped"] = 1
            elif mutation == "retry":
                rows[0]["tests"][0]["results"][0]["retry"] = 1
            elif mutation == "duplicate":
                rows.append(copy.deepcopy(rows[0]))
            else:
                rows.pop()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_report(changed, filename, titles)
        with tempfile.TemporaryDirectory(prefix="browser-control-") as directory:
            _, _, _, receipt, calls, code = self.fixture(directory, "timeout")
            self.assertEqual(code, 1)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(receipt["selections"]), 1)
            self.assertIsNone(receipt["selections"][0]["command"]["exit_code"])
            self.assertEqual(receipt["selections"][0]["command"]["observation"], "TIMED_OUT_NO_RETRY")
            self.assertEqual(receipt["status"], "UNKNOWN")
            self.assertNotIn("source_after", receipt)
            self.assertNotIn("verified", receipt["selections"][0])

    def test_browser_source_observation_adds_only_declared_inventory(self):
        with tempfile.TemporaryDirectory(prefix="browser-source-control-") as directory:
            repo = Path(directory)
            initializing = [True]

            def git(argv, *, cwd, timeout):
                self.assertEqual(Path(cwd), repo)
                self.assertEqual(timeout, 30)
                if argv == ["git", "rev-parse", "HEAD"]:
                    return b"a" * 40
                if argv == ["git", "rev-parse", "HEAD^{tree}"]:
                    return b"b" * 40
                if argv == ["git", "diff", "--name-only", "HEAD", "--"]:
                    return b""
                self.assertEqual(argv[:2], ["git", "show"])
                name = argv[2].split(":", 1)[1]
                raw = ("source " + name + "\n").encode()
                if initializing[0]:
                    path = repo / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                return raw

            with mock.patch.object(gate.subprocess, "check_output", side_effect=git), \
                    mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
                old = gate.source_observation(repo)
                extended = gate.source_observation(repo, "browser-contracts")
                initializing[0] = False
                self.assertEqual(gate.source_observation(repo), old)
                self.assertEqual(set(extended["files"]) - set(old["files"]),
                                 set(gate.BROWSER_SOURCE_PATHS) - set(old["files"]))
                for name in ("apps/studio/tests/support/syntheticProjectReads.ts",
                             "apps/studio/src/SceneBeatEditor.tsx", "apps/studio/tests/project-mutation.spec.ts"):
                    path = repo / name
                    raw = path.read_bytes()
                    try:
                        path.write_bytes(raw + b"hidden physical change")
                        with self.subTest(path=name), self.assertRaisesRegex(ValueError, "source differs from Git"):
                            gate.source_observation(repo, "browser-contracts")
                    finally:
                        path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    gate.source_observation(repo, "unknown")

    def test_browser_archive_closes_original_source_commands_reports_without_service_credit(self):
        with tempfile.TemporaryDirectory(prefix="browser-archive-control-") as directory:
            repo, root, source, receipt, _, code = self.fixture(directory)
            self.assertEqual(code, 0, receipt)
            result = self.retain(repo, root, source)
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["bindings"]["captures"], [])
            self.assertEqual(result["bindings"]["service_runtime"], "NOT_RUN")
            self.assertEqual(result["bindings"]["discovery"]["status"], "LISTED_NOT_EXECUTED")
            with gate.tarfile.open(root / "retained/closed-evidence.tar") as archive:
                self.assertIn("e/browser-discovery.json", archive.getnames())
                self.assertIn("e/browser-discovery.command.json", archive.getnames())
                self.assertFalse(any("executed-studio-service" in name for name in archive.getnames()))

    def test_browser_archive_rejects_service_claims_and_malformed_or_unsealed_commands(self):
        for mutation in ("service-claim", "store", "argv", "unknown", "discovery-executed", "case-retry"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="browser-archive-control-") as directory:
                repo, root, source, receipt, _, code = self.fixture(directory)
                self.assertEqual(code, 0)
                output = root / "e"
                first = receipt["selections"][0]
                if mutation == "service-claim":
                    receipt["service_binary"] = {"path": "forged", "sha256": "0" * 64}
                elif mutation == "store":
                    (output / "forged-stores").mkdir()
                elif mutation in ("argv", "unknown"):
                    command = first["command"]
                    if mutation == "argv":
                        command["argv"] = ["unrelated-command"]
                    else:
                        command.update(exit_code=None, observation="TIMED_OUT_NO_RETRY",
                                       diagnostic_custody="UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED")
                    (output / (first["label"] + ".command.json")).write_text(gate.json.dumps(command))
                elif mutation == "discovery-executed":
                    path = output / "browser-discovery.json"
                    document = gate.json.loads(path.read_bytes())
                    document["suites"][0]["specs"][0]["tests"][0]["results"] = [{"status": "passed", "retry": 0}]
                    path.write_text(gate.json.dumps(document))
                    receipt["discovery"]["original_report"] = gate.digest(path)
                else:
                    path = output / (first["label"] + ".json")
                    document = gate.json.loads(path.read_bytes())
                    document["suites"][0]["specs"][0]["tests"][0]["results"][0]["retry"] = 1
                    path.write_text(gate.json.dumps(document))
                    first["original_report"] = gate.digest(path)
                (output / "receipt.json").write_text(gate.json.dumps(receipt))
                result = self.retain(repo, root, source)
                self.assertEqual(result["status"], "UNKNOWN" if mutation == "unknown" else "FAIL", result)
                self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")

    def test_browser_workflow_is_manual_closed_and_within_declared_deadlines(self):
        workflow = (PATH.parents[1] / ".github/workflows/studio-service-verification.yml").read_text(encoding="utf-8")
        choices = [item.strip() for item in re.findall(r"options: \[([^\]]+)\]", workflow)[0].split(",")]
        self.assertEqual(choices.count("browser-contracts"), 1)
        self.assertIn("default: authoring-drafts", workflow)
        self.assertIn("branches: ['verify/studio-service-*']", workflow)
        job = workflow.split("  real-service:\n", 1)[1].split("  studio-service-68:\n", 1)[0]
        self.assertEqual(re.findall(r"^    if: (.+)$", job, re.MULTILINE),
                         ["github.event_name == 'workflow_dispatch' && inputs.selection != 'studio-service-68'"])
        self.assertGreaterEqual(job.count("!contains(fromJSON('[\"browser-contracts\",\"creative-production-browser\",\"review-browser\",\"workflow-preview-browser\"]'), matrix.selection)"), 2)
        self.assertIn("matrix.selection == 'browser-contracts' && 240", job)
        self.assertIn('tooling/ci_canvas_service.py archive --repo . --evidence ../v', job)
        self.assertIn("id: archive\n        if: success()", job)
        self.assertIn('steps.archive.outcome != \'success\'', job)
        self.assertEqual(sum(gate.BROWSER_SELECTION_TIMEOUTS[row[0]] for row in gate.browser_selections("browser-contracts")), 12090)
        self.assertGreater(240 * 60, sum(gate.BROWSER_SELECTION_TIMEOUTS[row[0]] for row in gate.browser_selections("browser-contracts")) + 180 + 1200)
        for label, filename, titles in gate.BROWSER_SELECTIONS:
            source = (PATH.parents[1] / "apps/studio/tests" / filename).read_text(encoding="utf-8")
            if filename == "project-mutation.spec.ts":
                source = source.split('test.describe("real service imports and portable restore"', 1)[0]
            self.assertNotRegex(source, r"test(?:Info)?\.setTimeout|test\.slow|describe\.configure")
            self.assertGreater(gate.BROWSER_SELECTION_TIMEOUTS[label], 120 + 30 * len(titles))


# Independent expected strings copied from the reviewed source-bound inventories.
EXPECTED_CREATIVE_SERVICE = (('production-design',
  'production-design-service.spec.ts',
  ('Production plan raw drafts, claim references and content-bound approval survive actual App '
   'navigation and SQLite restart',
   'Production Hero updates retain editable node identities and a human override; detach preserves '
   'objects across SQLite restart',
   'a held original Production component receipt preserves later typing across actual App remount and a '
   'separate save',
   'a held original Production plan receipt preserves later typing across actual App remount and a '
   'separate save',
   'an unknown Production plan committed result survives reload without replay and needs a fresh '
   'history review',
   'an unknown Production plan matching-peer result survives reload without replay and needs a fresh '
   'history review',
   'Production data recovery failure sends no real service grant or change; explicit healthy retry '
   'persists the retained draft',
   'Production pointer recovery failure sends no real service grant or change; explicit healthy retry '
   'persists the retained draft')),
 ('creative-workbench',
  'creative-workbench-service.spec.ts',
  ('stage actual App updates preserve stable editable objects and a human override; detach survives '
   'SQLite restart',
   'procedural actual App updates preserve stable editable objects and a human override; detach '
   'survives SQLite restart',
   'Measured actual source bytes, versioned table and Canvas binding persist through App editing and '
   'SQLite restart',
   'Measured original binding receipt preserves later raw selection and precision across actual App '
   'remount',
   'stage an actual committed response lost in transport remains UNKNOWN through reload and requires '
   'fresh history review',
   'procedural an actual committed response lost in transport remains UNKNOWN through reload and '
   'requires fresh history review',
   'measured an actual committed response lost in transport remains UNKNOWN through reload and requires '
   'fresh history review',
   'creative workbench data backup failure sends no real grant or change; explicit healthy retry saves '
   'retained input',
   'creative workbench pointer backup failure sends no real grant or change; explicit healthy retry '
   'saves retained input')))
EXPECTED_CREATIVE_BROWSER = (('browser-production-design',
  'production-design-preservation.spec.ts',
  ('Production component raw input survives tool navigation, scene changes, remount and reload without '
   'a save',
   'Production plan preserves exact lists, reviewer, note, shot choices and clock through navigation '
   'and reload',
   'Production component drafts cannot cross id and restore the separate author on return',
   'Production component drafts cannot cross generation and restore the separate author on return',
   'Production component drafts cannot cross active_branch and restore the separate author on return',
   'Production plan drafts cannot cross id and restore the separate author on return',
   'Production plan drafts cannot cross generation and restore the separate author on return',
   'Production plan drafts cannot cross active_branch and restore the separate author on return',
   'a deferred Production concept digest cannot overwrite or send later author input',
   'a Production digest completing after owner does not dispatch an obsolete approval',
   'a Production digest completing after revision does not dispatch an obsolete approval',
   'a Production digest completing after unmount does not dispatch an obsolete approval',
   'an original Production component ACK retains later input and permits its separate second save',
   'an original Production plan ACK retains later input and permits its separate second save',
   'observed matching Production component values do not acknowledge an unknown original result',
   'observed matching Production plan values do not acknowledge an unknown original result',
   'silent Production data recovery failure blocks actual Project dispatch and preserves raw input',
   'silent Production pointer recovery failure blocks actual Project dispatch and preserves raw input',
   'a silent Production copy removal reports unconfirmed discard and keeps the original author bytes '
   'visible',
   'an unreadable own Production copy stays warned while typing; an explicit new copy preserves both '
   'buffers',
   'retrying a readable Production recovery retains its UNKNOWN alongside newer author input')),
 ('browser-creative-workbench',
  'creative-workbench-preservation.spec.ts',
  ('stage raw author input survives navigation, scene return, remount and reload without Project writes',
   'stage author input is isolated by id and restores on return',
   'stage author input is isolated by generation and restores on return',
   'stage author input is isolated by active_branch and restores on return',
   'stage incomplete numeric input remains exact and cannot dispatch a Project mutation',
   'stage a reply without the submitted operation cannot clear UNKNOWN',
   'stage silent recovery data loss blocks dispatch and preserves author input',
   'stage matching observed props and a lost original reply remain UNKNOWN after reload',
   'procedural raw author input survives navigation, scene return, remount and reload without Project '
   'writes',
   'procedural author input is isolated by id and restores on return',
   'procedural author input is isolated by generation and restores on return',
   'procedural author input is isolated by active_branch and restores on return',
   'procedural incomplete numeric input remains exact and cannot dispatch a Project mutation',
   'procedural a reply without the submitted operation cannot clear UNKNOWN',
   'procedural silent recovery data loss blocks dispatch and preserves author input',
   'procedural matching observed props and a lost original reply remain UNKNOWN after reload',
   'measured raw author input survives navigation, scene return, remount and reload without Project '
   'writes',
   'measured author input is isolated by id and restores on return',
   'measured author input is isolated by generation and restores on return',
   'measured author input is isolated by active_branch and restores on return',
   'measured incomplete numeric input remains exact and cannot dispatch a Project mutation',
   'measured a reply without the submitted operation cannot clear UNKNOWN',
   'measured silent recovery data loss blocks dispatch and preserves author input',
   'measured matching observed props and a lost original reply remain UNKNOWN after reload',
   'Measured table selection preserves each dataset and binding buffer without writes',
   'Measured binding original ACK preserves later Canvas selection and raw precision',
   'Measured pending file typing cannot publish rows, proof or an obsolete error',
   'Measured pending file asset cannot publish rows, proof or an obsolete error',
   'Measured pending file owner cannot publish rows, proof or an obsolete error',
   'Measured pending file unmount cannot publish rows, proof or an obsolete error',
   'Measured pending file late-error cannot publish rows, proof or an obsolete error',
   'Measured completed byte proof tolerates label edits but is never recovered as submit authority',
   'Measured pending file cannot resurrect a buffer after an explicit saved-value choice',
   'Measured pending file cannot replace an explicitly restored copy with the same edit sequence',
   'Measured new-copy action cannot bypass a repaired UNKNOWN after missing reload',
   'Measured new-copy action cannot bypass a repaired UNKNOWN after corrupt reload',
   'ProductStage existing Hero eligibility blocks dispatch while separate scene drafts and valid '
   'authoring remain intact')))
EXPECTED_CREATIVE_UNITS = (('src/productionDesignDraft.test.ts',
  (('Production author input and original response authority',
    'scopes component and plan input by project, generation, branch and component scene'),
   ('Production author input and original response authority',
    'retains exact plan text, empty lines, reviewer, source references and clock without cleaning '
    'author input'),
   ('Production author input and original response authority',
    'an observed identical hero value at r+1 never consumes its pending request'),
   ('Production author input and original response authority',
    'an observed identical plan value at r+1 never consumes its pending request'),
   ('Production author input and original response authority',
    'the original hero reply preserves later raw input including a return to the old value'),
   ('Production author input and original response authority',
    'the original plan reply preserves later raw input including a return to the old value'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong id without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong generation without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong active_branch without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong revision without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong value without consuming raw input'),
   ('Production author input and original response authority',
    'treats an unrelated observed revision as a reviewable conflict rather than silently rebasing '
    'authored intent'),
   ('Production author input and original response authority',
    'invalidates a prepared digest when input, owner, revision, pending attempt, or source target '
    'changes'),
   ('Production author input and original response authority',
    'cleans only a plan submission and binds approval to that exact content while retaining author '
    'bytes'),
   ('Production author input and original response authority',
    'keeps editable scene objects on detach and rejects a receipt that changes them'),
   ('Production author input and original response authority',
    'will not dispatch plan or component submissions through content locks'),
   ('Production durable custody',
    'recovers NOT_SENT conservatively as UNKNOWN and exports complete raw input without write '
    'authority'),
   ('Production durable custody',
    'rejects foreign scope, writer and authority fields without altering any stored bytes'),
   ('Production durable custody',
    'requires confirmed data storage before treating a Production backup as durable'),
   ('Production durable custody',
    'requires confirmed pointer storage before treating a Production backup as durable'),
   ('Production durable custody',
    'rechecks data custody after budget enumeration before replacing any author bytes'),
   ('Production durable custody',
    'rechecks pointer custody after budget enumeration before replacing any author bytes'),
   ('Production durable custody',
    'rereads the data after pointer publication and never adopts the interleaved replacement'),
   ('Production durable custody', 'retains corrupt copies and refuses to mutate a clean checkpoint'),
   ('Production durable custody',
    "refuses a full recovery budget without deleting older copies or the live author's input"),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'reads two current snapshots and complete history; matching values remain UNKNOWN until explicit '
    'separate intent'),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'rejects truncated history and a project that changes during inspection'),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'retains the old UNKNOWN through a verified two-phase new writer before releasing a separate '
    'intent'),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'a failed second fork phase leaves a recoverable UNKNOWN and the earlier author bytes intact'))),
 ('src/creativeWorkbenchDraft.test.ts',
  (('creative workbench raw input and original response authority',
    'keeps raw integer grammar distinct from numeric coercion'),
   ('creative workbench raw input and original response authority',
    'scopes all workbenches by project, generation, branch, scene and editor kind'),
   ('creative workbench raw input and original response authority',
    'stage observed matching values never acknowledge an original pending attempt'),
   ('creative workbench raw input and original response authority',
    'procedural observed matching values never acknowledge an original pending attempt'),
   ('creative workbench raw input and original response authority',
    'measured observed matching values never acknowledge an original pending attempt'),
   ('creative workbench raw input and original response authority',
    'stage the original ACK preserves later raw input and independent table buffers'),
   ('creative workbench raw input and original response authority',
    'procedural the original ACK preserves later raw input and independent table buffers'),
   ('creative workbench raw input and original response authority',
    'measured the original ACK preserves later raw input and independent table buffers'),
   ('creative workbench raw input and original response authority',
    'stage raw copy roundtrip does not carry execution grants or byte verification'),
   ('creative workbench raw input and original response authority',
    'procedural raw copy roundtrip does not carry execution grants or byte verification'),
   ('creative workbench raw input and original response authority',
    'measured raw copy roundtrip does not carry execution grants or byte verification'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong id without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong generation without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong active_branch without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong revision without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong value without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'stage detach requires the original receipt to preserve every editable node'),
   ('creative workbench raw input and original response authority',
    'procedural detach requires the original receipt to preserve every editable node'),
   ('creative workbench raw input and original response authority',
    'an external revision needs an explicit base choice and never clears pending intent'),
   ('creative workbench raw input and original response authority',
    'invalidates asynchronous preparation when owner, revision, input or attempt changes'),
   ('creative workbench raw input and original response authority',
    'Measured keeps exact independent table, node, precision and axis buffers when selecting another '
    'table'),
   ('creative workbench raw input and original response authority',
    'Measured ACK for one table cannot mark a different hidden author buffer as saved'),
   ('creative workbench raw input and original response authority',
    'Measured reviewed new alias cannot overwrite a separately authored buffer at its saved table ID'),
   ('creative workbench raw input and original response authority',
    'Measured exact byte proof is ephemeral, tolerates label changes and rejects another source or '
    'recovered authority'),
   ('creative workbench raw input and original response authority',
    'Measured binding ACK checks the original table, datum, view and projected Canvas value'),
   ('creative workbench verified custody and uncertain outcome review',
    'requires verified data readback before a recovery write succeeds'),
   ('creative workbench verified custody and uncertain outcome review',
    'requires verified pointer readback before a recovery write succeeds'),
   ('creative workbench verified custody and uncertain outcome review',
    'rechecks data custody after the recovery budget scan'),
   ('creative workbench verified custody and uncertain outcome review',
    'rechecks pointer custody after the recovery budget scan'),
   ('creative workbench verified custody and uncertain outcome review',
    'rereads data after pointer publication and rejects an interleaved replacement'),
   ('creative workbench verified custody and uncertain outcome review',
    'preserves corrupt bytes, rejects foreign scope and refuses clean checkpoint replacement'),
   ('creative workbench verified custody and uncertain outcome review',
    'a full recovery budget blocks saving without deleting any previous author bytes'),
   ('creative workbench verified custody and uncertain outcome review',
    'recovering a NOT_SENT checkpoint conservatively retains UNKNOWN and rejects extra authority '
    'fields'),
   ('creative workbench verified custody and uncertain outcome review',
    'requires fresh read-history-read and retains matching values as comparison rather than ACK'),
   ('creative workbench verified custody and uncertain outcome review',
    'rejects incomplete history and a project changed during outcome inspection'),
   ('creative workbench verified custody and uncertain outcome review',
    'a separate reviewed intent verifies both records and preserves the exact earlier UNKNOWN'),
   ('ProductStage existing project eligibility before mutation dispatch',
    'rejects an existing Hero collision before arming an attempt and retains the exact author input'),
   ('ProductStage existing project eligibility before mutation dispatch',
    'rejects creation at the existing sixteen-Stage limit while a bound Stage can still update'))))
EXPECTED_CREATIVE_SOURCES = ('apps/studio/src/App.tsx',
 'apps/studio/src/CompositionStudy.tsx',
 'apps/studio/src/MeasuredDataWorkbench.tsx',
 'apps/studio/src/ProceduralFieldWorkbench.tsx',
 'apps/studio/src/ProductStageWorkbench.tsx',
 'apps/studio/src/ProductionDesignWorkspace.tsx',
 'apps/studio/src/api.ts',
 'apps/studio/src/brandGovernance.ts',
 'apps/studio/src/cameraReframe.ts',
 'apps/studio/src/canvasInheritedInk.ts',
 'apps/studio/src/captureSource.ts',
 'apps/studio/src/componentDistillation.ts',
 'apps/studio/src/creativeDoctor.ts',
 'apps/studio/src/creativeEvaluator.ts',
 'apps/studio/src/creativeProduction.ts',
 'apps/studio/src/creativeUndo.ts',
 'apps/studio/src/creativeWorkbenchDraft.test.ts',
 'apps/studio/src/creativeWorkbenchDraft.ts',
 'apps/studio/src/deliveryRecipe.ts',
 'apps/studio/src/deliveryRecipeDraft.ts',
 'apps/studio/src/fileImport.ts',
 'apps/studio/src/fixture.ts',
 'apps/studio/src/generationJournalApi.ts',
 'apps/studio/src/identityOrigin.ts',
 'apps/studio/src/measuredData.ts',
 'apps/studio/src/musicClock.ts',
 'apps/studio/src/narrationReviewApi.ts',
 'apps/studio/src/proceduralField.ts',
 'apps/studio/src/productStage.ts',
 'apps/studio/src/productionDesignDraft.test.ts',
 'apps/studio/src/productionDesignDraft.ts',
 'apps/studio/src/projectLibrary.ts',
 'apps/studio/src/projectMutation.ts',
 'apps/studio/src/semanticSfx.ts',
 'apps/studio/src/stageCapture.ts',
 'apps/studio/src/types.ts',
 'apps/studio/src/useCreativeWorkbenchDraft.tsx',
 'apps/studio/tests/creative-workbench-preservation.spec.ts',
 'apps/studio/tests/creative-workbench-service.spec.ts',
 'apps/studio/tests/fixtures/creative-workbench-authority.html',
 'apps/studio/tests/fixtures/creative-workbench-authority.tsx',
 'apps/studio/tests/fixtures/production-design-authority.html',
 'apps/studio/tests/fixtures/production-design-authority.tsx',
 'apps/studio/tests/production-design-preservation.spec.ts',
 'apps/studio/tests/production-design-service.spec.ts',
 'apps/studio/tests/support/scopedChangeService.ts')
HISTORICAL_SERVICE_ROSTER_SHA256 = '45e92a6df5f4b1e470954a487ba57580cc63376883eb2e867bb0d287d4df4822'
HISTORICAL_BROWSER_ROSTER_SHA256 = 'a59c5db069b10483e1b2ee34671941ae9fcc45c5399f048bff00e29bbbd4c275'


class CreativeProductionScopeTests(unittest.TestCase):
    """Closed catalogues and original custody controls; no product runtime executes."""

    def test_exact_service_browser_and_unit_title_rosters_are_independently_pinned(self):
        self.assertEqual(gate.CREATIVE_PRODUCTION_SELECTIONS, EXPECTED_CREATIVE_SERVICE)
        self.assertEqual(gate.CREATIVE_PRODUCTION_BROWSER_SELECTIONS, EXPECTED_CREATIVE_BROWSER)
        self.assertEqual(gate.CREATIVE_PRODUCTION_UNITS, EXPECTED_CREATIVE_UNITS)
        self.assertEqual([len(row[2]) for row in EXPECTED_CREATIVE_SERVICE], [8, 9])
        self.assertEqual([len(row[2]) for row in EXPECTED_CREATIVE_BROWSER], [21, 37])
        self.assertEqual([len(row[1]) for row in EXPECTED_CREATIVE_UNITS], [29, 38])

    def test_historical_titles_and_manual_all_disjoint_partition_are_preserved(self):
        digest = lambda rows: gate.hashlib.sha256(gate.json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest(gate.HISTORICAL_PROJECT_AUTHORING_SELECTIONS), HISTORICAL_SERVICE_ROSTER_SHA256)
        self.assertEqual(sum(len(row[2]) for row in gate.HISTORICAL_PROJECT_AUTHORING_SELECTIONS), 135)
        self.assertEqual(digest(gate.HISTORICAL_BROWSER_SELECTIONS), HISTORICAL_BROWSER_ROSTER_SHA256)
        old = gate.selected_selections("project-authoring")
        added = gate.selected_selections("creative-production")
        self.assertEqual((len(old), sum(len(row[2]) for row in old)), (19, 136))
        self.assertEqual((len(added), sum(len(row[2]) for row in added)), (2, 17))
        branch_claim = gate.selected_selections("branch-claim")
        self.assertEqual(old + added + branch_claim, gate.selected_selections("all"))
        self.assertFalse({row[0] for row in old + added}.intersection(row[0] for row in branch_claim))
        self.assertFalse({row[0] for row in old}.intersection(row[0] for row in added))
        for selection, count in (("project-authoring", 136), ("creative-production", 17), ("branch-claim", 31), ("all", 184)):
            plan = gate.selection_plan(selection)
            self.assertEqual(plan["required_tests"], count)
            self.assertEqual(plan["full_default_tests"], 184)
            self.assertFalse(plan["formal_acceptance"])
        self.assertEqual(sum(len(row[2]) for row in gate.HISTORICAL_BROWSER_SELECTIONS), 148)
        self.assertEqual(len(gate.browser_selections()), 33)
        self.assertEqual(sum(len(row[2]) for row in gate.browser_selections()), 206)

    def test_new_browser_scope_has_exact_58_and_declares_all_unselected_scopes_without_credit(self):
        self.assertEqual(gate.browser_selections("creative-production-browser"), EXPECTED_CREATIVE_BROWSER)
        plan = gate.selection_plan("creative-production-browser")
        self.assertEqual(plan["required_tests"], 58)
        self.assertEqual(plan["discovery_tests"], 58)
        self.assertEqual(plan["excluded_service_cases"], [])
        self.assertEqual(sum(len(row["titles"]) for row in plan["not_selected"]), 184 + 148 + 29 + 60)
        self.assertTrue(all(row["status"] == "NOT_SELECTED" and row["technical_credit"] == "NONE"
                            for row in plan["not_selected"]))
        for selection in ("creative-production-browser ", "browser-creative-production", "creative-production-browser,all"):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                gate.selected_selections(selection)

    def test_catalogue_and_named_scope_count_preserving_substitutions_are_rejected(self):
        for attribute in ("SELECTIONS", "BROWSER_SELECTIONS"):
            original = getattr(gate, attribute)
            changed = list(original)
            label, filename, titles = changed[-1]
            changed[-1] = (label, filename, (*titles[:-1], "same count, unreviewed identity"))
            with mock.patch.object(gate, attribute, tuple(changed)), self.assertRaises(ValueError):
                gate.selected_selections("all" if attribute == "SELECTIONS" else "creative-production-browser")
        for scope in ("project-authoring", "creative-production"):
            group = copy.deepcopy(gate.SELECTION_GROUPS[scope])
            for mutation in ("duplicate", "foreign", "missing", "count"):
                bad = copy.deepcopy(group)
                if mutation == "duplicate": bad["labels"] = (*bad["labels"][:-1], bad["labels"][0])
                elif mutation == "foreign": bad["labels"] = (*bad["labels"][:-1], "unreviewed")
                elif mutation == "missing": bad["labels"] = bad["labels"][:-1]
                else: bad["required_tests"] -= 1
                with self.subTest(scope=scope, mutation=mutation), mock.patch.dict(gate.SELECTION_GROUPS, {scope: bad}), self.assertRaises(ValueError):
                    gate.selected_selections(scope)
        with mock.patch.dict(gate.BROWSER_SCOPES, {"creative-production-browser": ("browser-production-design", "browser-editor")}), self.assertRaises(ValueError):
            gate.browser_selections("creative-production-browser")

    def test_new_service_files_require_every_exact_ordinary_original_result(self):
        for label, filename, titles in EXPECTED_CREATIVE_SERVICE:
            value = selection_report(filename, titles)
            self.assertEqual(gate.validate_report(value, filename, titles)["passed"], len(titles))
            for mutation in ("missing", "duplicate", "foreign", "file", "skip", "retry", "repeat", "expected-fail", "global-error", "wrong-browser"):
                changed = copy.deepcopy(value)
                rows = changed["suites"][0]["suites"][0]["specs"]
                case = rows[0]["tests"][0]
                if mutation == "missing": rows.pop()
                elif mutation == "duplicate": rows[-1] = copy.deepcopy(rows[0])
                elif mutation == "foreign": rows[-1]["title"] = "undeclared service case"
                elif mutation == "file": rows[-1]["file"] = "production-design-preservation.spec.ts"
                elif mutation == "skip": case["expectedStatus"] = "skipped"
                elif mutation == "retry": case["results"][0]["retry"] = 1
                elif mutation == "repeat": case["results"].append(copy.deepcopy(case["results"][0]))
                elif mutation == "expected-fail": case["expectedStatus"] = "failed"
                elif mutation == "global-error": changed["errors"] = [{"message": "service failed"}]
                else: case["projectId"] = "firefox"
                with self.subTest(label=label, mutation=mutation), self.assertRaises(ValueError):
                    gate.validate_report(changed, filename, titles)

    def test_new_browser_discovery_rejects_old_file_injection_duplicates_and_fake_execution(self):
        helper = BrowserContractSelectionTests()
        scope = "creative-production-browser"
        value = helper.discovery_document(scope)
        self.assertEqual(gate.validate_browser_discovery(value, scope),
                         {"status": "LISTED_NOT_EXECUTED", "listed": 58, "selected": 58,
                          "excluded_service": 0, "execution_results": 0})
        for mutation in ("missing", "duplicate", "old-file", "tag", "browser", "executed", "skip", "global-error"):
            changed = copy.deepcopy(value)
            rows = changed["suites"][0]["specs"]
            case = rows[0]["tests"][0]
            if mutation == "missing": rows.pop()
            elif mutation == "duplicate": rows[-1] = copy.deepcopy(rows[0])
            elif mutation == "old-file": rows[0]["file"] = "project-mutation.spec.ts"
            elif mutation == "tag": rows[0]["tags"] = ["@unreviewed"]
            elif mutation == "browser": case["projectName"] = "firefox"
            elif mutation == "executed": case["results"] = [{"status": "passed", "retry": 0}]
            elif mutation == "skip": case["expectedStatus"] = "skipped"
            else: changed["errors"] = [{"message": "discovery failed"}]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_browser_discovery(changed, scope)
        with self.assertRaises(ValueError):
            gate.validate_browser_discovery(helper.discovery_document(), scope)

    def test_new_browser_execution_requires_exact_identity_tags_browser_and_one_attempt(self):
        helper = BrowserContractSelectionTests()
        for label, filename, titles in EXPECTED_CREATIVE_BROWSER:
            value = helper.executed_document(filename, titles)
            self.assertEqual(gate.validate_browser_execution(value, filename, titles)["passed"], len(titles))
            argv = gate.browser_command("node", Path("cli"), Path("output"),
                                        (label, filename, titles), scope="creative-production-browser")
            pattern = re.compile(argv[argv.index("--grep") + 1])
            for title in titles:
                self.assertTrue(pattern.fullmatch(" ".join(gate.browser_full_title_path(filename, title))))
            self.assertIn("--workers=1", argv)
            self.assertIn("--retries=0", argv)
            for mutation in ("foreign", "duplicate", "tag", "browser", "retry", "repeat", "skip", "failure"):
                changed = copy.deepcopy(value)
                rows = changed["suites"][0]["specs"]
                case = rows[0]["tests"][0]
                if mutation == "foreign": rows[-1]["title"] = "another unchanged total"
                elif mutation == "duplicate": rows[-1] = copy.deepcopy(rows[0])
                elif mutation == "tag": rows[0]["tags"] = ["@wrong"]
                elif mutation == "browser": case["projectName"] = "firefox"
                elif mutation == "retry": case["results"][0]["retry"] = 1
                elif mutation == "repeat": case["results"].append(copy.deepcopy(case["results"][0]))
                elif mutation == "skip": case["expectedStatus"] = "skipped"
                else: case["results"][0]["status"] = "failed"
                with self.subTest(label=label, mutation=mutation), self.assertRaises(ValueError):
                    gate.validate_browser_execution(changed, filename, titles)

    def test_new_browser_executes_only_two_files_and_never_inherits_service_authority(self):
        helper = BrowserContractSelectionTests()
        with tempfile.TemporaryDirectory() as directory:
            repo, root, source, receipt, calls, code = helper.fixture(directory, scope="creative-production-browser")
            self.assertEqual(code, 0, receipt)
            self.assertEqual([call[0] for call in calls], ["browser-discovery", "browser-production-design", "browser-creative-workbench"])
            self.assertEqual(receipt["selection_plan"]["requested"], "creative-production-browser")
            self.assertEqual(receipt["discovery"]["verified"]["listed"], 58)
            self.assertFalse((root / "t").exists())
            retained = helper.retain(repo, root, source)
            self.assertEqual(retained["status"], "PASS", retained)
            self.assertEqual(retained["bindings"]["captures"], [])
            self.assertEqual(retained["bindings"]["service_runtime"], "NOT_RUN")
            with gate.tarfile.open(root / "retained/closed-evidence.tar") as archive:
                self.assertIn("e/browser-discovery.json", archive.getnames())

    def test_new_browser_archive_rejects_cross_scope_reports_sources_commands_and_service_claims(self):
        helper = BrowserContractSelectionTests()
        for mutation in ("plan", "discovery-argv", "discovery-report", "source", "store", "binary", "target", "retry"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                repo, root, source, receipt, _, code = helper.fixture(directory, scope="creative-production-browser")
                self.assertEqual(code, 0)
                output = root / "e"
                if mutation == "plan": receipt["selection_plan"] = gate.selection_plan("browser-contracts")
                elif mutation == "source": receipt["source_after"] = {**source, "checkout": "c" * 40}
                elif mutation == "store": (output / "forbidden-stores").mkdir()
                elif mutation == "binary": receipt["service_binary"] = {"path": "forged"}
                elif mutation == "target": receipt["service_target_absent"] = False
                elif mutation == "discovery-argv":
                    command = receipt["discovery"]["command"]
                    command["argv"] = gate.browser_command(receipt["node"]["path"],
                                                          Path(receipt["playwright_cli"]["path"]), output)
                    (output / "browser-discovery.command.json").write_text(gate.json.dumps(command))
                elif mutation == "discovery-report":
                    path = output / "browser-discovery.json"
                    path.write_text(gate.json.dumps(helper.discovery_document()))
                    receipt["discovery"]["original_report"] = gate.digest(path)
                else:
                    first = receipt["selections"][0]
                    path = output / (first["label"] + ".json")
                    value = gate.json.loads(path.read_bytes())
                    value["suites"][0]["specs"][0]["tests"][0]["results"][0]["retry"] = 1
                    path.write_text(gate.json.dumps(value)); first["original_report"] = gate.digest(path)
                (output / "receipt.json").write_text(gate.json.dumps(receipt))
                result = helper.retain(repo, root, source)
                self.assertEqual(result["status"], "FAIL", result)
                self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")

    def test_new_sources_are_required_even_when_index_claims_clean(self):
        fixture = ProjectionNavigationScopeTests()
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git, before = fixture.source_fixture(repo)
            self.assertTrue(set(EXPECTED_CREATIVE_SOURCES).issubset(before["files"]))
            with mock.patch.object(gate.subprocess, "check_output", side_effect=git), mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
                for name in EXPECTED_CREATIVE_SOURCES:
                    path = repo / name
                    original = path.read_bytes()
                    try:
                        path.write_bytes(original + b"hidden physical change")
                        with self.subTest(path=name), self.assertRaisesRegex(ValueError, "source differs from Git"):
                            gate.source_observation(repo)
                        path.unlink()
                        with self.subTest(missing=name), self.assertRaises(ValueError):
                            gate.source_observation(repo)
                    finally:
                        path.write_bytes(original)
                original_stat = gate.archive_stat
                def denied_parent(path):
                    if path == repo / "apps/studio/src":
                        raise ValueError("source parent alias rejected")
                    return original_stat(path)
                with mock.patch.object(gate, "archive_stat", side_effect=denied_parent), self.assertRaisesRegex(ValueError, "source parent alias"):
                    gate.source_observation(repo)

    def test_new_deadlines_preserve_per_case_bounds_and_all_is_two_same_source_jobs(self):
        self.assertEqual({label: gate.SELECTION_TIMEOUTS[label] for label, _, _ in EXPECTED_CREATIVE_SERVICE},
                         {"production-design": 1140, "creative-workbench": 1260})
        for label, _, titles in EXPECTED_CREATIVE_SERVICE:
            self.assertEqual(gate.SELECTION_TIMEOUTS[label], len(titles) * 120 + 180)
        self.assertEqual({label: gate.BROWSER_SELECTION_TIMEOUTS[label] for label, _, _ in EXPECTED_CREATIVE_BROWSER},
                         {"browser-production-design": 810, "browser-creative-workbench": 1260})
        for label, _, titles in EXPECTED_CREATIVE_BROWSER:
            self.assertGreater(gate.BROWSER_SELECTION_TIMEOUTS[label], len(titles) * 30 + 120)
        workflow = (PATH.parents[1] / ".github/workflows/studio-service-verification.yml").read_text()
        service = workflow.split("  real-service:\n", 1)[1].split("  studio-service-68:\n", 1)[0]
        self.assertIn("fail-fast: false", service)
        self.assertIn("inputs.selection == 'all' && '[\"project-authoring\",\"creative-production\",\"branch-claim\"]'", service)
        self.assertIn('REQUESTED_SELECTION: ${{ matrix.selection }}', service)
        self.assertIn('studio-service-${{ matrix.selection }}-${{ github.sha }}-', service)
        self.assertIn('studio-service-unsealed-${{ matrix.selection }}-${{ github.sha }}-', service)
        self.assertEqual(workflow.split('  studio-service-68:\n', 1)[0].count('ref: ${{ github.sha }}'), 3)
        unit = workflow.split("  creative-unit-build:\n", 1)[1].split("  real-service:\n", 1)[0]
        self.assertNotIn("matrix:", unit)
        self.assertEqual(workflow.count('ci_canvas_service.py unit-build --repo . --output ../v/unit-build'), 1)
        self.assertIn("github.event_name == 'workflow_dispatch'", unit)
        self.assertIn("needs: g0", unit)
        self.assertIn("studio-${{ inputs.selection == 'branch-claim' && 'branch-claim' || inputs.selection == 'workflow-preview-browser' && 'workflow-preview' || inputs.selection == 'review' && 'review' || 'creative' }}-unit-build-${{ github.sha }}-", unit)
        self.assertNotIn('npm test', unit)
        self.assertGreater(360 * 60, 1200 + sum(gate.SELECTION_TIMEOUTS[row[0]] for row in gate.PROJECT_AUTHORING_SELECTIONS) + 1200)
        self.assertGreater(90 * 60, 1200 + 1140 + 1260 + 1200)
        self.assertGreater(75 * 60, 180 + 810 + 1260 + 1200)


class CreativeProductionStoreTests(unittest.TestCase):
    """Retain synthetic fixtures to check every new case/store binding independently."""

    def fixture(self, directory, selection="creative-production"):
        self.assertIn(selection, ("creative-production", "review", "workflow-preview", "branch-claim"))
        rows = (EXPECTED_CREATIVE_SERVICE if selection == "creative-production"
                else gate.BRANCH_CLAIM_SELECTIONS if selection == "branch-claim"
                else (("review", "review-service.spec.ts", gate.REVIEW_TITLES),))
        old = ClosedStudioArchiveTests()
        root, source, base_store = old.fixture(directory)
        output = root / "e"
        receipt = gate.json.loads((output / "receipt.json").read_bytes())
        receipt["selection_plan"] = gate.selection_plan(selection)
        receipt["selections"] = []
        first_manifest = gate.json.loads((base_store.parent / "manifest.json").read_bytes())
        for label, filename, titles in rows:
            original = selection_report(filename, titles)
            specs = original["suites"][0]["suites"][0]["specs"]
            for index, spec in enumerate(specs):
                folder = output / (label + "-test-results") / ("case-" + str(index))
                closed = folder / "closed-store"
                gate.shutil.copytree(base_store, closed / "store")
                manifest = copy.deepcopy(first_manifest)
                manifest["snapshotDirectory"] = str(closed / "store")
                (closed / "store" / manifest["databaseRelativePath"]).rename(closed / "store/studio.sqlite3")
                for entry in manifest["entries"]:
                    if entry["path"] == manifest["databaseRelativePath"]:
                        entry["path"] = "studio.sqlite3"
                manifest["databaseRelativePath"] = "studio.sqlite3"
                manifest["sourceDirectory"] = str(folder / "service")
                participants = 2 if label == "production-design" and index in (4, 5) else 1
                manifest["lifecycle"] = [
                    {"databasePath": str(folder / "service/studio.sqlite3"), "taints": [],
                     "exits": [{"processId": 1000 + index * 10 + participant, "code": 0, "signal": None}]}
                    for participant in range(participants)]
                if label in ("production-design", "review") and index == 0:
                    # More than one retained exit is allowed for a declared helper.
                    # These are synthetic records, not a claim of an OS restart.
                    manifest["lifecycle"][0]["exits"].append({"processId": 1001, "code": 0, "signal": None})
                aggregate = gate.hashlib.sha256(gate.json.dumps(
                    manifest["entries"], separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                for key in ("sourceBeforeSha256", "sourceAfterSha256", "snapshotSha256"):
                    manifest[key] = aggregate
                # Preserve the helper's original entry-key order in its aggregate.
                (closed / "manifest.json").write_text(gate.json.dumps(manifest))
                oracle = {"schemaVersion": 1, "journalComplete": True, "journalAfterRevisionExclusive": 0,
                          "journal": [], "project": {"id": "synthetic", "revision": index}}
                gate.write_new(closed / "oracle.json", oracle)
                outcome = {"schemaVersion": 1, "errors": [], "capture": manifest, "oracle": oracle,
                           "preparation": [{"index": 0, "status": "fulfilled"}],
                           "closures": [{"status": "fulfilled", **row} for row in manifest["lifecycle"]]}
                gate.write_new(folder / "closed-store-outcomes.json", outcome)
                evidence = {"primaryFailure": None, "failures": [], "canonicalOracle": oracle, "final": oracle["project"],
                            "services": [{"binaryPath": receipt["service_binary"]["path"],
                                          "databasePath": row["databasePath"], "processExits": row["exits"],
                                          "lifecycleTaints": row["taints"]} for row in manifest["lifecycle"]]}
                if label in ("review", "branch-authoring", "claim-source"):
                    evidence["service"] = evidence.pop("services")[0]
                name = "branch-authoring-evidence" if label == "branch-authoring" else label + "-service-evidence"
                gate.write_new(folder / (name + ".json"), evidence)
                spec["tests"][0]["results"][0]["attachments"] = [
                    {"name": item, "contentType": "application/json", "path": str(folder / (item + ".json"))}
                    for item in ("closed-store-outcomes", name)]
            gate.write_new(output / (label + ".json"), original)
            for stream in ("stdout", "stderr"):
                (output / (label + "." + stream + ".log")).write_bytes(b"synthetic fixture; never product execution\n")
            repo, target = root.parent / "g", root / "t"
            cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
            env = gate.service_environment_binding(target, output, label, Path(receipt["service_binary"]["path"]))
            command = {"argv": gate.service_command(receipt["node"]["path"], cli, output, (label, filename, titles)),
                       "cwd": str(repo / "apps/studio"), "exit_code": 0,
                       **synthetic_process_fields(env),
                       "diagnostic_custody": "DIRECT_PROCESS_EXIT_OBSERVED",
                       "stdout": gate.digest(output / (label + ".stdout.log")),
                       "stderr": gate.digest(output / (label + ".stderr.log"))}
            gate.write_new(output / (label + ".command.json"), command)
            receipt["selections"].append({"label": label, "expected_tests": len(titles), "status": "PASS",
                                          "command": command, "original_report": gate.digest(output / (label + ".json")),
                                          "verified": gate.validate_report(original, filename, titles)})
        (output / "receipt.json").write_text(gate.json.dumps(receipt))
        return root, source

    def reporter_fixture(self, directory, selection="creative-production"):
        root, source = self.fixture(directory, selection=selection)
        receipt_path = root / "e/receipt.json"
        receipt = gate.json.loads(receipt_path.read_bytes())
        ordinal = 0
        for observed in receipt["selections"]:
            report_path = root / "e" / (observed["label"] + ".json")
            document = gate.json.loads(report_path.read_bytes())
            for case in document["suites"][0]["suites"][0]["specs"]:
                for attachment in case["tests"][0]["results"][0]["attachments"]:
                    original = Path(attachment["path"])
                    ordinal += 1
                    # Synthetic reporter-shaped names, not an assertion about its hash algorithm.
                    copied = original.parent / "attachments" / (attachment["name"] + f"-{ordinal:040x}.json")
                    copied.parent.mkdir(exist_ok=True)
                    copied.write_bytes(original.read_bytes())
                    attachment["path"] = str(copied)
            report_path.write_text(gate.json.dumps(document))
            observed["original_report"] = gate.digest(report_path)
        receipt_path.write_text(gate.json.dumps(receipt))
        return root, source

    def test_reporter_copies_bind_to_all_seven_review_original_stores(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source = self.reporter_fixture(directory, selection="review")
            result = ClosedStudioArchiveTests().retained(root, source)
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["custody"], "VERIFIED_ORIGINAL_TREE_COPY")
            cases = result["bindings"]["review_cases"]
            self.assertEqual(len(cases), 7)
            self.assertEqual(len({row["manifest"] for row in cases}), 7)
            self.assertEqual(sum(row["services"] for row in cases), 7)
            self.assertNotIn("creative_production_cases", result["bindings"])
            self.assertEqual(
                [(row["label"], row["file"], row["title"]) for row in cases],
                [("review", "review-service.spec.ts", title) for title in gate.REVIEW_TITLES],
            )
            for row in cases:
                folder = row["manifest"].rsplit("/closed-store/", 1)[0]
                self.assertEqual(row["outcomes"], folder + "/closed-store-outcomes.json")
                self.assertEqual(row["original_evidence"], folder + "/review-service-evidence.json")
                self.assertEqual(
                    row["database_path"],
                    str(Path(row["source_directory"]) / "studio.sqlite3"),
                )
            self.assertTrue((root / "retained/closed-evidence.tar").is_file())

    def test_reporter_copies_bind_to_all_seventeen_original_stores(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source = self.reporter_fixture(directory)
            result = ClosedStudioArchiveTests().retained(root, source)
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["custody"], "VERIFIED_ORIGINAL_TREE_COPY")
            cases = result["bindings"]["creative_production_cases"]
            self.assertEqual(len(cases), 17)
            self.assertEqual(len({row["manifest"] for row in cases}), 17)
            self.assertEqual(sum(row["services"] for row in cases), 19)
            for row in cases:
                folder = row["manifest"].rsplit("/closed-store/", 1)[0]
                self.assertEqual(row["outcomes"], folder + "/closed-store-outcomes.json")
                self.assertEqual(row["original_evidence"], folder + "/" + row["label"] + "-service-evidence.json")
            self.assertTrue((root / "retained/closed-evidence.tar").is_file())

    def test_reporter_copy_tampering_and_missing_or_foreign_originals_cannot_close(self):
        mutations = ("copy-bytes", "original-bytes", "equal-length-copy", "missing-copy", "missing-original",
                     "malformed-name", "escape", "duplicate", "cross-case", "store-divergence")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root, source = self.reporter_fixture(directory)
                report_path = root / "e/production-design.json"
                document = gate.json.loads(report_path.read_bytes())
                cases = document["suites"][0]["suites"][0]["specs"]
                attachments = cases[0]["tests"][0]["results"][0]["attachments"]
                copied = Path(attachments[0]["path"])
                original = copied.parent.parent / "closed-store-outcomes.json"
                if mutation == "copy-bytes":
                    copied.write_bytes(copied.read_bytes() + b"\n")
                elif mutation == "original-bytes":
                    original.write_bytes(original.read_bytes() + b"\n")
                elif mutation == "equal-length-copy":
                    raw = copied.read_bytes()
                    changed = raw.replace(b": ", b":\t", 1)
                    self.assertNotEqual(raw, changed)
                    self.assertEqual(len(raw), len(changed))
                    self.assertEqual(gate.json.loads(raw), gate.json.loads(changed))
                    copied.write_bytes(changed)
                elif mutation == "missing-copy":
                    copied.unlink()
                elif mutation == "missing-original":
                    original.unlink()
                elif mutation == "malformed-name":
                    renamed = copied.with_name("closed-store-outcomes-not-40-hex.json")
                    copied.rename(renamed)
                    attachments[0]["path"] = str(renamed)
                elif mutation == "escape":
                    attachments[0]["path"] = str(root.parent / "outside.json")
                elif mutation == "duplicate":
                    attachments.append(copy.deepcopy(attachments[0]))
                elif mutation == "cross-case":
                    attachments[1]["path"] = cases[1]["tests"][0]["results"][0]["attachments"][1]["path"]
                else:
                    value = gate.json.loads(original.read_bytes())
                    value["capture"]["snapshotSha256"] = "0" * 64
                    original.write_text(gate.json.dumps(value))
                    copied.write_bytes(original.read_bytes())
                # Preserve the report binding so the intended attachment/store guard is tested.
                report_path.write_text(gate.json.dumps(document))
                receipt_path = root / "e/receipt.json"
                receipt = gate.json.loads(receipt_path.read_bytes())
                for selection in receipt["selections"]:
                    if selection["label"] == "production-design":
                        selection["original_report"] = gate.digest(report_path)
                receipt_path.write_text(gate.json.dumps(receipt))
                result = ClosedStudioArchiveTests().retained(root, source)
                self.assertEqual(result["status"], "FAIL", result)
                self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")
                self.assertFalse((root / "retained/closed-evidence.tar").exists())

    def test_reporter_copy_or_original_replacement_after_inventory_cannot_close(self):
        for member in ("copy", "original"):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as directory:
                root, source = self.reporter_fixture(directory)
                document = gate.json.loads((root / "e/production-design.json").read_bytes())
                case = document["suites"][0]["suites"][0]["specs"][0]
                copied = Path(case["tests"][0]["results"][0]["attachments"][0]["path"])
                target = copied if member == "copy" else copied.parent.parent / "closed-store-outcomes.json"
                relative = target.relative_to(root).as_posix()
                read_original = gate.archive_json
                replaced = []

                def replace_before_parse(observed_root, inventory, name):
                    if name == relative and not replaced:
                        replacement = target.with_name(target.name + ".replacement")
                        replacement.write_bytes(target.read_bytes())
                        target.unlink()
                        replacement.rename(target)
                        replaced.append(name)
                    return read_original(observed_root, inventory, name)

                with mock.patch.object(gate, "archive_json", side_effect=replace_before_parse):
                    result = ClosedStudioArchiveTests().retained(root, source)
                self.assertEqual(replaced, [relative])
                self.assertEqual(result["status"], "FAIL", result)
                self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")
                self.assertEqual(result["error"], "original JSON replaced before parse")
                self.assertFalse((root / "retained/closed-evidence.tar").exists())

    def test_all_seventeen_cases_require_distinct_original_store_and_oracle_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source = self.fixture(directory)
            result = ClosedStudioArchiveTests().retained(root, source)
            self.assertEqual(result["status"], "PASS", result)
            cases = result["bindings"]["creative_production_cases"]
            self.assertEqual(len(cases), 17)
            self.assertEqual(len({row["manifest"] for row in cases}), 17)
            self.assertEqual(sum(row["services"] for row in cases), 19)
            self.assertTrue(all(row["database_path"] == str(Path(row["source_directory"]) / "studio.sqlite3")
                                for row in cases))
            self.assertEqual([(row["file"], row["title"]) for row in cases],
                             [(filename, title) for _, filename, titles in EXPECTED_CREATIVE_SERVICE for title in titles])

    def test_foreign_service_binary_database_participants_and_lifecycle_cannot_close(self):
        mutations = ("missing-services", "binary", "database", "process-exits", "taints", "source-directory",
                     "missing-source-directory", "missing-peer-coherent", "extra-participant-coherent", "peer-order")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root, source = self.fixture(directory)
                index = 4 if mutation in ("missing-peer-coherent", "peer-order") else 0
                folder = root / "e/production-design-test-results" / ("case-" + str(index))
                evidence_path, manifest_path = folder / "production-design-service-evidence.json", folder / "closed-store/manifest.json"
                outcome_path = folder / "closed-store-outcomes.json"
                evidence = gate.json.loads(evidence_path.read_bytes())
                manifest = gate.json.loads(manifest_path.read_bytes())
                outcome = gate.json.loads(outcome_path.read_bytes())
                if mutation == "missing-services": evidence.pop("services")
                elif mutation == "binary": evidence["services"][0]["binaryPath"] = str(root / "foreign-service")
                elif mutation == "database": evidence["services"][0]["databasePath"] = str(folder / "foreign.sqlite3")
                elif mutation == "process-exits": evidence["services"][0]["processExits"][0]["processId"] += 1
                elif mutation == "taints": evidence["services"][0]["lifecycleTaints"] = ["UNKNOWN"]
                elif mutation == "source-directory": manifest["sourceDirectory"] = str(root / "e/production-design-stores")
                elif mutation == "missing-source-directory": manifest.pop("sourceDirectory")
                elif mutation == "missing-peer-coherent":
                    evidence["services"].pop()
                    manifest["lifecycle"].pop()
                    outcome["closures"].pop()
                elif mutation == "extra-participant-coherent":
                    evidence["services"].append(copy.deepcopy(evidence["services"][0]))
                    manifest["lifecycle"].append(copy.deepcopy(manifest["lifecycle"][0]))
                    outcome["closures"].append(copy.deepcopy(outcome["closures"][0]))
                else:
                    evidence["services"].reverse()
                # Preserve existing capture/outcome consistency to expose the
                # missing link to invocation and participant authority itself.
                outcome["capture"] = manifest
                evidence_path.write_text(gate.json.dumps(evidence))
                manifest_path.write_text(gate.json.dumps(manifest))
                outcome_path.write_text(gate.json.dumps(outcome))
                result = ClosedStudioArchiveTests().retained(root, source)
                self.assertEqual(result["status"], "FAIL", result)
                self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")
                self.assertFalse((root / "retained/closed-evidence.tar").exists())

    def test_missing_shared_foreign_uncertain_or_failed_case_evidence_is_rejected(self):
        for mutation in ("missing-attachment", "duplicate-attachment", "foreign-attachment", "shared-capture", "manifest",
                         "oracle", "capture", "closure", "taint", "preparation", "primary-failure", "final-project"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root, source = self.fixture(directory)
                output = root / "e"
                report_path = output / "production-design.json"
                report_value = gate.json.loads(report_path.read_bytes())
                cases = report_value["suites"][0]["suites"][0]["specs"]
                result = cases[0]["tests"][0]["results"][0]
                folder = output / "production-design-test-results/case-0"
                if mutation == "missing-attachment": result["attachments"].pop()
                elif mutation == "duplicate-attachment": result["attachments"].append(copy.deepcopy(result["attachments"][0]))
                elif mutation == "foreign-attachment": result["attachments"][0]["path"] = str(root.parent / "outside.json")
                elif mutation == "shared-capture": cases[1]["tests"][0]["results"][0]["attachments"] = copy.deepcopy(result["attachments"])
                elif mutation == "manifest": (folder / "closed-store/manifest.json").unlink()
                elif mutation == "oracle": (folder / "closed-store/oracle.json").write_text('{}')
                elif mutation in ("capture", "closure", "taint", "preparation"):
                    path = folder / "closed-store-outcomes.json"
                    value = gate.json.loads(path.read_bytes())
                    if mutation == "capture": value["capture"]["snapshotSha256"] = "0" * 64
                    elif mutation == "closure": value["closures"][0]["status"] = "rejected"
                    elif mutation == "taint": value["closures"][0]["taints"] = ["UNKNOWN"]
                    else: value["preparation"][0]["status"] = "rejected"
                    path.write_text(gate.json.dumps(value))
                else:
                    path = folder / "production-design-service-evidence.json"
                    value = gate.json.loads(path.read_bytes())
                    if mutation == "primary-failure": value["primaryFailure"] = {"message": "original failure"}
                    else: value["final"]["revision"] = -1
                    path.write_text(gate.json.dumps(value))
                report_path.write_text(gate.json.dumps(report_value))
                receipt_path = output / "receipt.json"
                receipt = gate.json.loads(receipt_path.read_bytes())
                receipt["selections"][0]["original_report"] = gate.digest(report_path)
                receipt_path.write_text(gate.json.dumps(receipt))
                result = ClosedStudioArchiveTests().retained(root, source)
                self.assertEqual(result["status"], "FAIL", result)
                self.assertEqual(result["custody"], "UNSEALED_DIAGNOSTICS")


class CreativeProductionUnitTests(unittest.TestCase):
    """Original reporter and process-invocation contracts, using synthetic files only."""

    def document(self, studio, selection="creative-production"):
        self.assertIn(selection, ("creative-production", "review", "workflow-preview"))
        rows, count = {"creative-production": (EXPECTED_CREATIVE_UNITS, 67),
                       "review": (gate.REVIEW_UNITS, 72),
                       "workflow-preview": (gate.WORKFLOW_PREVIEW_UNITS, 79)}[selection]
        return {"success": True, "numTotalTests": count, "numPassedTests": count,
                "numFailedTests": 0, "numPendingTests": 0, "numTodoTests": 0,
                "numFailedTestSuites": 0, "numPendingTestSuites": 0,
                "testResults": [
                    {"name": str(studio / filename), "status": "passed", "message": "",
                     "assertionResults": [{"ancestorTitles": [parent], "title": title, "fullName": parent + " " + title,
                                           "status": "passed", "failureMessages": []} for parent, title in titles]}
                    for filename, titles in rows]}

    def test_selected_unit_report_requires_both_files_and_exact_67_original_assertions(self):
        studio = Path("synthetic/studio")
        result = gate.validate_creative_production_units(self.document(studio), studio)
        self.assertEqual((result["passed"], result["files"], result["skipped"], result["retries"]), (67, 2, 0, 0))
        self.assertFalse(result["formal_acceptance"])
        self.assertEqual(result["evidence_scope"], "UNIT_ASSERTIONS_ONLY")

    def test_unit_counts_cannot_hide_missing_foreign_skipped_retried_or_failed_assertions(self):
        studio = Path("synthetic/studio")
        for mutation in ("missing-file", "duplicate-file", "foreign-file", "missing-title", "duplicate-title", "foreign-title",
                         "wrong-hierarchy", "wrong-full-name", "pending", "todo", "failed", "retry", "message", "global", "zero", "suite-failure"):
            value = self.document(studio)
            suites = value["testResults"]
            rows = suites[0]["assertionResults"]
            first = rows[0]
            if mutation == "missing-file": suites.pop()
            elif mutation == "duplicate-file": suites[-1] = copy.deepcopy(suites[0])
            elif mutation == "foreign-file": suites[0]["name"] = str(studio / "src/unreviewed.test.ts")
            elif mutation == "missing-title": rows.pop()
            elif mutation == "duplicate-title": rows[-1] = copy.deepcopy(rows[0])
            elif mutation == "foreign-title": first["title"] = "same total, foreign title"
            elif mutation == "wrong-hierarchy": first["ancestorTitles"] = ["unreviewed"]
            elif mutation == "wrong-full-name": first["fullName"] += " changed"
            elif mutation in ("pending", "todo", "failed"): first["status"] = mutation
            elif mutation == "retry": first["retryCount"] = 1
            elif mutation == "message": first["failureMessages"] = ["original error"]
            elif mutation == "global": value["unhandledErrors"] = ["global error"]
            elif mutation == "zero": value["numTotalTests"] = 0
            else: suites[0]["status"] = "failed"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_creative_production_units(value, studio)

    def fixture(self, directory, mutation=None, selection="creative-production"):
        files = [name for name, _ in (
            EXPECTED_CREATIVE_UNITS if selection == "creative-production" else gate.REVIEW_UNITS)]
        repo = Path(directory) / "g"
        studio = repo / "apps/studio"
        studio.mkdir(parents=True)
        output = Path(directory) / "e"
        cli = studio / "node_modules/vitest/vitest.mjs"
        cli.parent.mkdir(parents=True); cli.write_bytes(b"synthetic CLI, not executed")
        probe = repo / "tooling/vite_editor_security_probe.mjs"
        probe.parent.mkdir(parents=True); probe.write_bytes(b"synthetic probe, not executed")
        node, npm = repo / "node", repo / "npm"
        node.write_bytes(b"synthetic node"); npm.write_bytes(b"synthetic npm")
        source = {"checkout": "a" * 40, "tree": "b" * 40, "files": {"synthetic": "only"}}
        calls = []
        if mutation == "stale-build": (studio / "dist").mkdir()
        def which(name):
            self.assertIn(name, ("node", "npm"))
            return str(repo / name)
        def execute(argv, cwd, env, destination, label, timeout):
            self.assertEqual(cwd, studio)
            self.assertNotIn("MOTIONWRIGHT_SERVICE_TEST_BIN", env)
            self.assertNotIn("MOTIONWRIGHT_SERVICE_TEST_TMPDIR", env)
            self.assertEqual(destination, output)
            calls.append((label, argv, timeout))
            if label == "vitest":
                self.assertEqual(argv[2:3 + len(files)], ["run", *files])
                self.assertIn("--retry=0", argv)
                self.assertNotIn("src", argv)
                document = self.document(studio, selection)
                if mutation == "vitest-report": document["testResults"][0]["assertionResults"].pop()
                gate.write_new(output / "vitest.json", document)
            if label == "build":
                dist = studio / "dist"
                dist.mkdir(); (dist / "index.html").write_bytes(b"synthetic build")
                (dist / "main.js").write_bytes(b"synthetic bundle")
                if mutation == "changed-tool": node.write_bytes(b"changed synthetic tool")
            command = {"argv": argv, "cwd": str(cwd), "exit_code": 0,
                       **synthetic_process_fields(env),
                       "diagnostic_custody": "DIRECT_PROCESS_EXIT_OBSERVED"}
            if mutation == "probe-fail" and label == "security-probe": command["exit_code"] = 1
            if mutation == "timeout" and label == "vitest":
                command.update(exit_code=None, observation="TIMED_OUT_NO_RETRY",
                               diagnostic_custody="UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED")
            for stream in ("stdout", "stderr"):
                path = output / (label + "." + stream + ".log")
                path.write_bytes(b"synthetic original stream\n"); command[stream] = gate.digest(path)
            gate.write_new(output / (label + ".command.json"), command)
            return command
        after = {**source, "checkout": "c" * 40} if mutation == "changed-source" else source
        with mock.patch.object(gate, "source_observation", side_effect=[source, after]) as source_query, \
                mock.patch.object(gate.shutil, "which", side_effect=which), \
                mock.patch.object(gate, "execute", side_effect=execute), \
                mock.patch.dict(gate.os.environ, {"MOTIONWRIGHT_SERVICE_TEST_BIN": "forbidden", "MOTIONWRIGHT_SERVICE_TEST_TMPDIR": "forbidden"}), \
                mock.patch("sys.stdout", new=io.StringIO()):
            code = gate.run_creative_production_units(repo, output, selection)
        if mutation == "timeout":
            self.assertEqual(source_query.call_count, 1, "UNKNOWN must prevent source_after subprocesses")
        return gate.json.loads((output / "receipt.json").read_bytes()), calls, code, output

    def test_one_selected_unit_build_run_preserves_original_report_and_actual_build_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt, calls, code, output = self.fixture(directory)
            self.assertEqual(code, 0, receipt)
            self.assertEqual([row[0] for row in calls], ["security-probe", "vitest", "build"])
            self.assertEqual(receipt["source_before"], receipt["source_after"])
            self.assertEqual(receipt["vitest_original"], gate.digest(output / "vitest.json"))
            self.assertEqual(receipt["vitest"]["passed"], 67)
            self.assertEqual(receipt["production_build"]["files"]["main.js"], gate.digest(output / "production-build/main.js"))
            self.assertEqual(receipt["service_runtime"], "NOT_RUN")
            self.assertEqual(receipt["browser_runtime"], "NOT_RUN")
            self.assertFalse(receipt["formal_acceptance"])

    def test_unit_build_stops_on_probe_failure_invalid_original_or_unknown_command(self):
        for mutation, expected in (("probe-fail", ["security-probe"]),
                                   ("vitest-report", ["security-probe", "vitest"]),
                                   ("timeout", ["security-probe", "vitest"]), ("stale-build", [])):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                receipt, calls, code, _ = self.fixture(directory, mutation)
                self.assertEqual(code, 1)
                self.assertEqual([row[0] for row in calls], expected)
                self.assertEqual(receipt["status"], "UNKNOWN" if mutation == "timeout" else "FAIL")
                if mutation == "timeout":
                    self.assertNotIn("source_after", receipt)
                    self.assertEqual(receipt["terminal_custody"], "UNSEALED_NO_FURTHER_COMMANDS")
                self.assertNotIn("production_build", receipt)

    def test_changed_source_or_executable_cannot_seal_unit_build_success(self):
        for mutation in ("changed-source", "changed-tool"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                receipt, calls, code, _ = self.fixture(directory, mutation)
                self.assertEqual(code, 1)
                self.assertEqual(len(calls), 3)
                self.assertNotIn("production_build", receipt)



class ReviewScopeTests(unittest.TestCase):
    """Exact Review scopes and synthetic custody controls, without product execution."""

    def test_review_rosters_are_pinned_and_historical_scopes_remain_separate(self):
        digest = lambda rows: gate.hashlib.sha256(gate.json.dumps(
            rows, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(digest(gate.REVIEW_UNITS),
                         "058f9321286e82c1ba818df050a2a02d019647bcc8a0da1513e6add2dc5e4b36")
        self.assertEqual(digest(gate.REVIEW_BROWSER_SELECTIONS),
                         "14b1893b6ed67f47bfbecc18ed150a1afc14827495bea3e236818ab12f156fa7")
        self.assertEqual(digest(gate.selected_selections("review")),
                         "ef5acb89447654f1a4b3ca6d353a5c00b480b7d48b3750a7a2074c9e8b1b5ecb")
        self.assertEqual(sum(len(row[1]) for row in gate.REVIEW_UNITS), 72)
        self.assertEqual(gate.selection_plan("review-browser")["required_tests"], 29)
        self.assertEqual(gate.selection_plan("review")["required_tests"], 7)
        self.assertEqual(sum(len(row[2]) for row in gate.HISTORICAL_PROJECT_AUTHORING_SELECTIONS), 135)
        self.assertEqual(gate.selection_plan("project-authoring")["required_tests"], 136)
        self.assertEqual(gate.selection_plan("all")["required_tests"], 184)
        self.assertEqual(sum(len(row[1]) for row in gate.CREATIVE_PRODUCTION_UNITS), 67)
        self.assertEqual(gate.selection_plan("creative-production-browser")["required_tests"], 58)
        self.assertEqual(gate.selection_plan("creative-production")["required_tests"], 17)
        plan = gate.selection_plan("review-browser")
        self.assertEqual(plan["discovery_tests"], 29)
        self.assertEqual(sum(len(row["titles"]) for row in plan["not_selected"]), 184 + 206 + 60)
        self.assertEqual(plan["excluded_service_cases"], [])
        self.assertTrue(all(row["technical_credit"] == "NONE" for row in plan["not_selected"]))
        with self.assertRaises(ValueError):
            gate.selected_unit_inventory("review,creative-production")

    def test_review_browser_discovery_and_execution_require_exact_original_cases(self):
        fixture = BrowserContractSelectionTests()
        scope = "review-browser"
        document = fixture.discovery_document(scope)
        self.assertEqual(gate.validate_browser_discovery(document, scope)["listed"], 29)
        for mutation in ("missing", "duplicate", "foreign", "file", "executed", "skip"):
            value = copy.deepcopy(document)
            rows = value["suites"][0]["specs"]
            case = rows[0]["tests"][0]
            if mutation == "missing": rows.pop()
            elif mutation == "duplicate": rows[-1] = copy.deepcopy(rows[0])
            elif mutation == "foreign": rows[0]["title"] = "unreviewed same-count case"
            elif mutation == "file": rows[0]["file"] = "review-service.spec.ts"
            elif mutation == "executed": case["results"] = [{"status": "passed", "retry": 0}]
            else: case["expectedStatus"] = "skipped"
            with self.subTest(discovery=mutation), self.assertRaises(ValueError):
                gate.validate_browser_discovery(value, scope)
        _, filename, titles = gate.REVIEW_BROWSER_SELECTIONS[0]
        original = fixture.executed_document(filename, titles, scope)
        self.assertEqual(gate.validate_browser_execution(original, filename, titles)["passed"], 29)
        for mutation in ("foreign", "duplicate", "tag", "browser", "retry", "repeat", "skip", "failure"):
            value = copy.deepcopy(original)
            rows = value["suites"][0]["specs"]
            case = rows[0]["tests"][0]
            if mutation == "foreign": rows[-1]["title"] = "unreviewed same-count case"
            elif mutation == "duplicate": rows[-1] = copy.deepcopy(rows[0])
            elif mutation == "tag": rows[0]["tags"] = ["@foreign"]
            elif mutation == "browser": case["projectName"] = "firefox"
            elif mutation == "retry": case["results"][0]["retry"] = 1
            elif mutation == "repeat": case["results"].append(copy.deepcopy(case["results"][0]))
            elif mutation == "skip": case["expectedStatus"] = "skipped"
            else: case["results"][0]["status"] = "failed"
            with self.subTest(execution=mutation), self.assertRaises(ValueError):
                gate.validate_browser_execution(value, filename, titles)

    def test_review_browser_uses_existing_observer_and_unknown_stops_further_commands(self):
        fixture = BrowserContractSelectionTests()
        for mutation in (None, "timeout", "discovery-mismatch"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                repo, root, source, receipt, calls, code = fixture.fixture(
                    directory, mutation, scope="review-browser")
                self.assertFalse((root / "t").exists())
                if mutation is None:
                    self.assertEqual(code, 0, receipt)
                    self.assertEqual([row[0] for row in calls], ["browser-discovery", "browser-review"])
                    self.assertEqual(receipt["discovery"]["verified"]["listed"], 29)
                    retained = fixture.retain(repo, root, source)
                    self.assertEqual(retained["status"], "PASS", retained)
                    self.assertEqual(retained["bindings"]["captures"], [])
                    self.assertEqual(retained["bindings"]["service_runtime"], "NOT_RUN")
                else:
                    self.assertEqual(code, 1)
                    self.assertEqual(len(calls), 2 if mutation == "timeout" else 1)
                    if mutation == "timeout":
                        self.assertEqual(receipt["status"], "UNKNOWN")
                        self.assertNotIn("source_after", receipt)
                        self.assertEqual(receipt["terminal_custody"], "UNSEALED_NO_FURTHER_COMMANDS")

    def test_review_unit_report_requires_exact_72_original_assertions(self):
        fixture = CreativeProductionUnitTests()
        studio = Path("synthetic/studio")
        original = fixture.document(studio, "review")
        result = gate.validate_creative_production_units(original, studio, "review")
        self.assertEqual((result["passed"], result["files"], result["skipped"], result["retries"]), (72, 1, 0, 0))
        self.assertFalse(result["formal_acceptance"])
        for mutation in ("missing-file", "duplicate-file", "foreign-file", "missing-title",
                         "duplicate-title", "foreign-title", "hierarchy", "full-name",
                         "pending", "retry", "error", "global", "zero"):
            value = copy.deepcopy(original)
            suites = value["testResults"]
            rows = suites[0]["assertionResults"]
            first = rows[0]
            if mutation == "missing-file": suites.clear()
            elif mutation == "duplicate-file": suites.append(copy.deepcopy(suites[0]))
            elif mutation == "foreign-file": suites[0]["name"] = str(studio / "src/foreign.test.ts")
            elif mutation == "missing-title": rows.pop()
            elif mutation == "duplicate-title": rows[-1] = copy.deepcopy(first)
            elif mutation == "foreign-title": first["title"] = "unreviewed same-count assertion"
            elif mutation == "hierarchy": first["ancestorTitles"] = ["foreign"]
            elif mutation == "full-name": first["fullName"] += " changed"
            elif mutation == "pending": first["status"] = "pending"
            elif mutation == "retry": first["retryCount"] = 1
            elif mutation == "error": first["failureMessages"] = ["original failure"]
            elif mutation == "global": value["unhandledErrors"] = ["global failure"]
            else: value["numTotalTests"] = 0
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_creative_production_units(value, studio, "review")

    def test_review_unit_build_preserves_source_tools_originals_and_unknown_boundary(self):
        fixture = CreativeProductionUnitTests()
        for mutation in (None, "probe-fail", "vitest-report", "timeout",
                         "stale-build", "changed-source", "changed-tool"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                receipt, calls, code, output = fixture.fixture(directory, mutation, selection="review")
                if mutation is None:
                    self.assertEqual(code, 0, receipt)
                    self.assertEqual([row[0] for row in calls], ["security-probe", "vitest", "build"])
                    self.assertEqual(receipt["requested_selection"], "review")
                    self.assertEqual(receipt["vitest"]["passed"], 72)
                    self.assertEqual(receipt["vitest"]["files"], 1)
                    self.assertEqual(receipt["vitest_original"], gate.digest(output / "vitest.json"))
                    self.assertEqual(receipt["source_before"], receipt["source_after"])
                    self.assertEqual(receipt["production_build"]["files"]["main.js"],
                                     gate.digest(output / "production-build/main.js"))
                else:
                    self.assertEqual(code, 1)
                    self.assertNotIn("production_build", receipt)
                    if mutation == "timeout":
                        self.assertEqual(receipt["status"], "UNKNOWN")
                        self.assertEqual([row[0] for row in calls], ["security-probe", "vitest"])
                        self.assertNotIn("source_after", receipt)
                        self.assertEqual(receipt["terminal_custody"], "UNSEALED_NO_FURTHER_COMMANDS")
                self.assertEqual(receipt["service_runtime"], "NOT_RUN")
                self.assertEqual(receipt["browser_runtime"], "NOT_RUN")
                self.assertFalse(receipt["formal_acceptance"])

    def test_review_service_archive_binds_all_seven_cases_and_singular_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source = CreativeProductionStoreTests().fixture(directory, selection="review")
            result = ClosedStudioArchiveTests().retained(root, source)
            self.assertEqual(result["status"], "PASS", result)
            rows = result["bindings"]["review_cases"]
            self.assertEqual(len(rows), 7)
            self.assertEqual(len({row["manifest"] for row in rows}), 7)
            self.assertEqual(sum(row["services"] for row in rows), 7)
            self.assertNotIn("creative_production_cases", result["bindings"])
            self.assertEqual([row["title"] for row in rows], list(gate.REVIEW_TITLES))
            self.assertTrue(all(row["database_path"] == str(Path(row["source_directory"]) / "studio.sqlite3")
                                for row in rows))

    def test_review_service_archive_rejects_missing_shared_or_foreign_original_authority(self):
        mutations = ("attachment", "duplicate-attachment", "shared-store", "missing-service",
                     "plural-service", "binary", "database", "exits", "taints",
                     "final", "failure", "closure", "oracle", "source-directory")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root, source = CreativeProductionStoreTests().fixture(directory, selection="review")
                output = root / "e"
                report_path = output / "review.json"
                report_value = gate.json.loads(report_path.read_bytes())
                cases = report_value["suites"][0]["suites"][0]["specs"]
                first_result = cases[0]["tests"][0]["results"][0]
                folder = output / "review-test-results/case-0"
                evidence_path = folder / "review-service-evidence.json"
                outcome_path = folder / "closed-store-outcomes.json"
                manifest_path = folder / "closed-store/manifest.json"
                evidence = gate.json.loads(evidence_path.read_bytes())
                outcome = gate.json.loads(outcome_path.read_bytes())
                manifest = gate.json.loads(manifest_path.read_bytes())
                if mutation == "attachment": first_result["attachments"].pop()
                elif mutation == "duplicate-attachment":
                    first_result["attachments"].append(copy.deepcopy(first_result["attachments"][0]))
                elif mutation == "shared-store":
                    cases[1]["tests"][0]["results"][0]["attachments"] = copy.deepcopy(first_result["attachments"])
                elif mutation == "missing-service": evidence.pop("service")
                elif mutation == "plural-service": evidence["services"] = [copy.deepcopy(evidence["service"])]
                elif mutation == "binary": evidence["service"]["binaryPath"] = str(root / "foreign-binary")
                elif mutation == "database": evidence["service"]["databasePath"] = str(root / "foreign.sqlite3")
                elif mutation == "exits": evidence["service"]["processExits"][0]["processId"] += 1
                elif mutation == "taints": evidence["service"]["lifecycleTaints"] = ["UNKNOWN"]
                elif mutation == "final": evidence["final"]["revision"] = -1
                elif mutation == "failure": evidence["primaryFailure"] = {"message": "original failure"}
                elif mutation == "closure": outcome["closures"][0]["status"] = "rejected"
                elif mutation == "oracle": (folder / "closed-store/oracle.json").write_text("{}")
                else:
                    manifest["sourceDirectory"] = str(output / "review-stores")
                    outcome["capture"] = manifest
                evidence_path.write_text(gate.json.dumps(evidence))
                outcome_path.write_text(gate.json.dumps(outcome))
                manifest_path.write_text(gate.json.dumps(manifest))
                report_path.write_text(gate.json.dumps(report_value))
                receipt_path = output / "receipt.json"
                receipt = gate.json.loads(receipt_path.read_bytes())
                receipt["selections"][0]["original_report"] = gate.digest(report_path)
                receipt_path.write_text(gate.json.dumps(receipt))
                retained = ClosedStudioArchiveTests().retained(root, source)
                self.assertEqual(retained["status"], "FAIL", retained)
                self.assertEqual(retained["custody"], "UNSEALED_DIAGNOSTICS")
                self.assertFalse((root / "retained/closed-evidence.tar").exists())

    def test_review_workflow_is_source_bound_and_preserves_existing_execution_budgets(self):
        workflow = (PATH.parents[1] / ".github/workflows/studio-service-verification.yml").read_text()
        self.assertEqual(workflow.split('  studio-service-68:\n', 1)[0].count("ref: ${{ github.sha }}"), 3)
        self.assertIn("default: authoring-drafts", workflow)
        self.assertIn("branches: ['verify/studio-service-*']", workflow)
        self.assertIn("""inputs.selection == 'review' && '["review-browser","review"]'""", workflow)
        self.assertIn("""inputs.selection == 'all' && '["project-authoring","creative-production","branch-claim"]'""", workflow)
        unit = workflow.split("  creative-unit-build:\n", 1)[1].split("  real-service:\n", 1)[0]
        self.assertIn("needs: g0", unit)
        self.assertIn("github.event_name == 'workflow_dispatch'", unit)
        self.assertIn('REQUESTED_UNIT_SELECTION:', unit)
        self.assertIn('--selection "$REQUESTED_UNIT_SELECTION"', unit)
        self.assertIn("retention-days: 14", unit)
        self.assertEqual(gate.SELECTION_TIMEOUTS["review"], 7 * 120 + 120 + 60)
        self.assertEqual(gate.BROWSER_SELECTION_TIMEOUTS["browser-review"], 29 * 30 + 180)
        self.assertEqual(gate.SELECTION_TIMEOUTS["creative-workbench"], 1260)
        self.assertEqual(gate.BROWSER_SELECTION_TIMEOUTS["browser-creative-workbench"], 1260)
        self.assertEqual(sum(gate.BROWSER_SELECTION_TIMEOUTS[row[0]]
                             for row in gate.browser_selections("browser-contracts")), 12090)
        self.assertEqual(gate.selection_plan("browser-contracts")["discovery_tests"], 210)
        self.assertGreater(60 * 60, 1200 + gate.SELECTION_TIMEOUTS["review"] + 1200)
        self.assertGreater(60 * 60, 180 + gate.BROWSER_SELECTION_TIMEOUTS["browser-review"] + 1200)

    def test_review_source_observation_covers_product_tests_and_original_authority_helper(self):
        required = {
            "apps/studio/src/reviewDraft.ts",
            "apps/studio/src/reviewDraft.test.ts",
            "apps/studio/src/ReviewWorkspace.tsx",
            "apps/studio/src/CreativeAlternativesPanel.tsx",
            "apps/studio/tests/review-service.spec.ts",
            "apps/studio/tests/review-preservation.spec.ts",
            "apps/studio/tests/support/scopedChangeService.ts",
        }
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            initializing = [True]
            def git(argv, *, cwd, timeout):
                self.assertEqual(Path(cwd), repo)
                self.assertEqual(timeout, 30)
                if argv == ["git", "rev-parse", "HEAD"]: return b"a" * 40
                if argv == ["git", "rev-parse", "HEAD^{tree}"]: return b"b" * 40
                if argv == ["git", "diff", "--name-only", "HEAD", "--"]: return b""
                self.assertEqual(argv[:2], ["git", "show"])
                name = argv[2].split(":", 1)[1]
                raw = ("source " + name + "\n").encode()
                if initializing[0]:
                    path = repo / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                return raw
            with mock.patch.object(gate.subprocess, "check_output", side_effect=git), \
                    mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
                observed = gate.source_observation(repo, "review-browser")
                self.assertTrue(required.issubset(observed["files"]))
                initializing[0] = False
                for name in required:
                    path = repo / name
                    raw = path.read_bytes()
                    try:
                        path.write_bytes(raw + b"unobserved physical change")
                        with self.subTest(path=name), self.assertRaises(ValueError):
                            gate.source_observation(repo, "review-browser")
                    finally:
                        path.write_bytes(raw)



class WorkflowPreviewScopeTests(unittest.TestCase):
    def test_recovery_rosters_are_closed_and_historical_scopes_unchanged(self):
        units = gate.selected_unit_inventory("workflow-preview")
        self.assertEqual([len(titles) for _, titles in units], [60, 15, 4])
        browser = gate.browser_selections("workflow-preview-browser")
        self.assertEqual([(name, len(titles)) for _, name, titles in browser], [
            ("program-av-monitor.spec.ts", 2), ("workflow-action-recovery.spec.ts", 33),
            ("preview-recovery.spec.ts", 27)])
        self.assertEqual(browser[0], next(row for row in gate.BROWSER_SELECTIONS
                                         if row[1] == "program-av-monitor.spec.ts"))
        self.assertEqual(sum(len(titles) for _, _, titles in gate.browser_selections("browser-contracts")), 206)
        self.assertEqual(sum(len(titles) for _, titles in gate.selected_unit_inventory("creative-production")), 67)
        self.assertEqual(sum(len(titles) for _, titles in gate.selected_unit_inventory("review")), 72)

    def test_recovery_unit_report_rejects_omitted_foreign_skipped_duplicate_cases(self):
        studio = Path("/repo/apps/studio")
        document = CreativeProductionUnitTests().document(studio, "workflow-preview")
        self.assertEqual(gate.validate_creative_production_units(document, studio, "workflow-preview")["passed"], 79)
        for mutation in ("missing", "foreign", "skip", "duplicate", "summary", "file"):
            changed = copy.deepcopy(document)
            rows = changed["testResults"][0]["assertionResults"]
            if mutation == "missing": rows.pop()
            elif mutation == "foreign": rows[0]["title"] = "foreign"
            elif mutation == "skip": rows[0]["status"] = "pending"
            elif mutation == "duplicate": rows[-1] = copy.deepcopy(rows[0])
            elif mutation == "summary": changed["numPassedTests"] = 78
            else: changed["testResults"][0]["name"] = str(studio / "foreign.test.ts")
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_creative_production_units(changed, studio, "workflow-preview")

    def test_recovery_browser_discovery_and_originals_require_exact_cases(self):
        fixture = BrowserContractSelectionTests()
        scope = "workflow-preview-browser"
        gate.validate_browser_discovery(fixture.discovery_document(scope), scope)
        for label, filename, titles in gate.browser_selections(scope):
            document = fixture.executed_document(filename, titles, scope)
            self.assertEqual(gate.validate_browser_execution(document, filename, titles)["passed"], len(titles))
            command = gate.browser_command("node", Path("cli"), Path("out"), (label, filename, titles), scope=scope)
            self.assertIn("--retries=0", command)
            self.assertIn("--workers=1", command)
            grep = re.compile(command[command.index("--grep") + 1])
            self.assertTrue(all(grep.fullmatch(" ".join(gate.browser_full_title_path(filename, title)))
                                for title in titles))

    def test_recovery_browser_rejects_retry_skip_failure_and_foreign_identity(self):
        fixture = BrowserContractSelectionTests()
        def specs(suites):
            return [spec for suite in suites for spec in [*suite.get("specs", []), *specs(suite.get("suites", []))]]
        for _, filename, titles in gate.browser_selections("workflow-preview-browser"):
            original = fixture.executed_document(filename, titles, "workflow-preview-browser")
            for mutation in ("retry", "skip", "failure", "foreign", "tag", "duplicate-result", "browser"):
                changed = copy.deepcopy(original)
                row = specs(changed["suites"])[0]
                case = row["tests"][0]
                if mutation == "retry": case["results"][0]["retry"] = 1
                elif mutation == "skip": case["expectedStatus"] = "skipped"
                elif mutation == "failure": case["results"][0]["status"] = "failed"
                elif mutation == "foreign": row["title"] = "foreign"
                elif mutation == "tag": row["tags"] = ["@foreign"]
                elif mutation == "duplicate-result": case["results"].append(copy.deepcopy(case["results"][0]))
                else: case["projectName"] = "firefox"
                with self.subTest(file=filename, mutation=mutation), self.assertRaises(ValueError):
                    gate.validate_browser_execution(changed, filename, titles)

    def test_recovery_source_scope_includes_shared_custody_and_actual_test_sources(self):
        paths = set(gate.WORKFLOW_PREVIEW_SOURCE_PATHS)
        self.assertTrue({"apps/desktop/src-tauri/src/workflow_native.rs", "apps/desktop/src-tauri/src/main.rs",
                         "apps/desktop/src-tauri/src/effect_grants.rs", "crates/native/src/production.rs",
                         "crates/native/tests/desktop_workflow_route.rs", "apps/studio/src/previewRecovery.ts",
                         "apps/studio/src/workflowActionDraft.ts", "apps/studio/src/WorkflowWorkspace.tsx",
                         "apps/studio/src/App.tsx", "apps/studio/tests/preview-recovery.spec.ts",
                         "apps/studio/tests/workflow-action-recovery.spec.ts"}.issubset(paths))
        for name in paths:
            self.assertTrue((PATH.parents[1] / name).is_file(), name)

    @staticmethod
    def route_log():
        return "".join(f"test {name} ... ok\n" for name in gate.WORKFLOW_ROUTE_CASES) + (
            "test result: ok. 20 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.1s\n")

    def test_native_closed_roster_includes_all_nine_routes_and_eleven_grants(self):
        self.assertEqual(len(gate.WORKFLOW_ROUTE_CASES), 20)
        self.assertEqual(sum(name.startswith("effect_grants::") for name in gate.WORKFLOW_ROUTE_CASES), 11)
        self.assertEqual(sum(name.startswith("routed::") for name in gate.WORKFLOW_ROUTE_CASES), 8)
        result = gate.validate_workflow_route_output(0, self.route_log())
        self.assertEqual(result["passed"], 20)
        self.assertFalse(result["formal_acceptance"])
        self.assertEqual(result["native_application_execution"], "NOT_RUN")

    def test_native_original_rejects_missing_foreign_duplicate_and_ignored_case(self):
        original = self.route_log()
        first, second = gate.WORKFLOW_ROUTE_CASES[:2]
        variants = [original.replace(f"test {first} ... ok\n", ""),
                    original.replace(f"test {first} ... ok", "test foreign ... ok"),
                    original.replace(f"test {first} ... ok", f"test {second} ... ok"),
                    original.replace(f"test {first} ... ok", f"test {first} ... ignored"),
                    original.replace("0 filtered out", "1 filtered out"),
                    original.replace("20 passed", "19 passed"),
                    original.replace("0 failed", "1 failed")]
        for value in variants:
            with self.assertRaises(ValueError): gate.validate_workflow_route_output(0, value)

    def test_native_original_rejects_exit_failure_missing_or_multiple_summaries(self):
        original = self.route_log()
        for code, value in [(1, original), (False, original), (0, original.split("test result:")[0]),
                            (0, original + original.splitlines()[-1] + "\n")]:
            with self.assertRaises(ValueError): gate.validate_workflow_route_output(code, value)

    def test_native_historical_nine_only_is_not_whole_target_execution(self):
        names = [name for name in gate.WORKFLOW_ROUTE_CASES if not name.startswith("effect_grants::")]
        value = "".join(f"test {name} ... ok\n" for name in names) + (
            "test result: ok. 9 passed; 0 failed; 0 ignored; 0 measured; 11 filtered out;\n")
        with self.assertRaises(ValueError): gate.validate_workflow_route_output(0, value)

    def test_native_unknown_stops_without_further_command_or_source_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            tool = root / "rustup"
            tool.write_bytes(b"controlled test executable identity")
            output = root / "out"
            command = {"exit_code": None, "observation": "UNKNOWN_TIMEOUT_NO_RETRY",
                       "process_observation_schema": gate.PROCESS_OBSERVATION_SCHEMA,
                       "process_boundary": "OWNED_POSIX_SESSION_GROUP", "pid": 111}
            with mock.patch.object(gate.sys, "platform", "linux"), \
                 mock.patch.object(gate, "source_observation", return_value={"source": "fixture"}) as source, \
                 mock.patch.object(gate.shutil, "which", return_value=str(tool)), \
                 mock.patch.object(gate, "execute", return_value=command) as execute:
                self.assertEqual(gate.run_workflow_route(repo, output), 1)
            self.assertEqual(source.call_count, 1)
            self.assertEqual(execute.call_count, 1)
            receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "UNKNOWN")
            self.assertNotIn("source_after", receipt)
            self.assertNotIn("route_contracts", receipt)
            self.assertFalse(receipt["formal_acceptance"])

    def test_new_workflow_uses_existing_observer_and_never_claims_tauri_launch(self):
        workflow = (PATH.parents[1] / ".github/workflows/studio-service-verification.yml").read_text(encoding="utf-8")
        self.assertIn('ci_canvas_service.py workflow-route --repo . --output ../v/native-route', workflow)
        self.assertIn("inputs.selection == 'workflow-preview-browser'", workflow)
        self.assertIn("include-hidden-files: true", workflow)
        self.assertIn("if-no-files-found: warn", workflow)
        self.assertNotIn("--ignore", workflow)



class BranchClaimScopeTests(unittest.TestCase):
    """Synthetic verifier controls; no generated report is product execution."""

    def test_exact_service_and_unit_rosters_preserve_every_historical_group(self):
        digest = lambda value: gate.hashlib.sha256(gate.json.dumps(
            value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest(gate.BRANCH_CLAIM_SELECTIONS), '9ff817b358e4ea886f67314b57e16a271a3192b1d292624bb07076bce39ad94c')
        self.assertEqual(digest(gate.BRANCH_CLAIM_UNITS), '3b3cb7ad95ac6808b6049249b7aaed115f5966fdd193f45cccf1fd69f8a353e0')
        self.assertEqual(digest(gate.CLAIM_SOURCE_RUST_CASES), '135431fa4213c8648db78e198b93f674a111d0b32dce2fda148c8f19c1c9b174')
        self.assertEqual([len(row[2]) for row in gate.BRANCH_CLAIM_SELECTIONS], [17, 14])
        self.assertEqual([len(row[1]) for row in gate.selected_unit_inventory("branch-claim")], [65, 69])
        self.assertEqual(gate.selection_plan("all")["required_tests"], 184)
        self.assertEqual(gate.selection_plan("project-authoring")["required_tests"], 136)
        self.assertEqual(gate.selection_plan("creative-production")["required_tests"], 17)
        self.assertEqual(gate.selection_plan("review")["required_tests"], 7)
        plan = gate.selection_plan("branch-claim")
        self.assertEqual(plan["required_tests"], 31)
        self.assertEqual(sum(len(row["titles"]) for row in plan["not_selected"]), 153)
        self.assertTrue(all(row["status"] == "NOT_SELECTED" and row["technical_credit"] == "NONE"
                            for row in plan["not_selected"]))
        for selection in ("branch", "branch-claim ", "branch-claim,review", "claim-source-browser"):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                gate.selected_selections(selection)

    def test_every_new_original_service_identity_is_required(self):
        for _, filename, titles in gate.BRANCH_CLAIM_SELECTIONS:
            original = selection_report(filename, titles)
            self.assertEqual(gate.validate_report(original, filename, titles)["passed"], len(titles))
            for mutation in ("missing", "duplicate", "foreign", "skip", "retry", "failure", "repeat"):
                value = copy.deepcopy(original)
                rows = value["suites"][0]["suites"][0]["specs"]
                case = rows[0]["tests"][0]
                if mutation == "missing": rows.pop()
                elif mutation == "duplicate": rows[-1] = copy.deepcopy(rows[0])
                elif mutation == "foreign": rows[-1]["title"] = "same-count substituted case"
                elif mutation == "skip": case["expectedStatus"] = "skipped"
                elif mutation == "retry": case["results"][0]["retry"] = 1
                elif mutation == "failure": case["results"][0]["status"] = "failed"
                else: case["results"].append(copy.deepcopy(case["results"][0]))
                with self.subTest(file=filename, mutation=mutation), self.assertRaises(ValueError):
                    gate.validate_report(value, filename, titles)

    def unit_document(self, studio):
        return {"success": True, "numTotalTests": 134, "numPassedTests": 134,
                "numFailedTests": 0, "numPendingTests": 0, "numTodoTests": 0,
                "numFailedTestSuites": 0, "numPendingTestSuites": 0,
                "testResults": [{"name": str(studio / filename), "status": "passed", "message": "",
                    "assertionResults": [{"ancestorTitles": [parent], "title": title,
                        "fullName": parent + " " + title, "status": "passed", "failureMessages": []}
                        for parent, title in titles]} for filename, titles in gate.BRANCH_CLAIM_UNITS]}

    def test_units_require_both_files_and_all_original_assertions(self):
        studio = Path("synthetic-studio")
        original = self.unit_document(studio)
        result = gate.validate_creative_production_units(original, studio, "branch-claim")
        self.assertEqual((result["passed"], result["files"], result["skipped"], result["retries"]), (134, 2, 0, 0))
        for mutation in ("missing-file", "missing-case", "foreign", "duplicate", "skip", "retry", "interrupted"):
            value = copy.deepcopy(original)
            assertions = value["testResults"][0]["assertionResults"]
            if mutation == "missing-file": value["testResults"].pop()
            elif mutation == "missing-case": assertions.pop()
            elif mutation == "foreign": assertions[-1]["title"] = "unreviewed assertion"
            elif mutation == "duplicate": assertions[-1] = copy.deepcopy(assertions[0])
            elif mutation == "skip": assertions[-1]["status"] = "pending"
            elif mutation == "retry": assertions[-1]["retryCount"] = 1
            else: value["wasInterrupted"] = True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_creative_production_units(value, studio, "branch-claim")

    def test_new_source_bytes_are_required_even_when_the_index_claims_clean(self):
        required = set(gate.BRANCH_CLAIM_SOURCE_PATHS)
        self.assertEqual(len(required), 22)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            initializing = [True]
            def git(argv, *, cwd, timeout):
                self.assertEqual(Path(cwd), repo)
                self.assertEqual(timeout, 30)
                if argv == ["git", "rev-parse", "HEAD"]: return b"a" * 40
                if argv == ["git", "rev-parse", "HEAD^{tree}"]: return b"b" * 40
                if argv == ["git", "diff", "--name-only", "HEAD", "--"]: return b""
                self.assertEqual(argv[:2], ["git", "show"])
                name = argv[2].split(":", 1)[1]
                raw = ("synthetic source " + name + "\n").encode()
                if initializing[0]:
                    path = repo / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                return raw
            with mock.patch.object(gate.subprocess, "check_output", side_effect=git), \
                 mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
                observed = gate.source_observation(repo)
                self.assertTrue(required.issubset(observed["files"]))
                initializing[0] = False
                for name in sorted(required):
                    path = repo / name
                    raw = path.read_bytes()
                    try:
                        path.write_bytes(raw + b"physical mutation")
                        with self.subTest(path=name), self.assertRaises(ValueError):
                            gate.source_observation(repo)
                    finally:
                        path.write_bytes(raw)

    def test_all_31_cases_require_distinct_closed_stores_and_singular_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source = CreativeProductionStoreTests().reporter_fixture(directory, "branch-claim")
            result = ClosedStudioArchiveTests().retained(root, source)
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["custody"], "VERIFIED_ORIGINAL_TREE_COPY")
            cases = result["bindings"]["branch_claim_cases"]
            self.assertEqual(len(cases), 31)
            self.assertEqual(len({row["manifest"] for row in cases}), 31)
            self.assertEqual(sum(row["services"] for row in cases), 31)
            self.assertEqual([(row["label"], row["file"], row["title"]) for row in cases],
                [(label, filename, title) for label, filename, titles in gate.BRANCH_CLAIM_SELECTIONS for title in titles])
            self.assertNotIn("creative_production_cases", result["bindings"])
            self.assertNotIn("review_cases", result["bindings"])
            self.assertTrue((root / "retained/closed-evidence.tar").is_file())

    def test_forged_missing_failed_and_foreign_new_store_authority_cannot_close(self):
        for label in ("branch-authoring", "claim-source"):
            for mutation in ("primary-failure", "final-oracle", "foreign-binary", "foreign-database", "missing-service", "extra-service"):
                with self.subTest(label=label, mutation=mutation), tempfile.TemporaryDirectory() as directory:
                    root, source = CreativeProductionStoreTests().fixture(directory, "branch-claim")
                    name = "branch-authoring-evidence" if label == "branch-authoring" else "claim-source-service-evidence"
                    path = root / "e" / (label + "-test-results") / "case-0" / (name + ".json")
                    value = gate.json.loads(path.read_bytes())
                    if mutation == "primary-failure": value["primaryFailure"] = "original assertion failed"
                    elif mutation == "final-oracle": value["final"] = {"foreign": True}
                    elif mutation == "foreign-binary": value["service"]["binaryPath"] += ".other"
                    elif mutation == "foreign-database": value["service"]["databasePath"] += ".other"
                    elif mutation == "missing-service": value.pop("service")
                    else: value["services"] = [value["service"]]
                    path.write_text(gate.json.dumps(value), encoding="utf-8")
                    result = ClosedStudioArchiveTests().retained(root, source)
                    self.assertEqual(result["status"], "FAIL", result)
                    self.assertFalse((root / "retained/closed-evidence.tar").exists())

    def test_two_new_cases_cannot_share_one_original_closed_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source = CreativeProductionStoreTests().fixture(directory, "branch-claim")
            path = root / "e/branch-authoring.json"
            value = gate.json.loads(path.read_bytes())
            rows = value["suites"][0]["suites"][0]["specs"]
            rows[1]["tests"][0]["results"][0]["attachments"] = copy.deepcopy(rows[0]["tests"][0]["results"][0]["attachments"])
            path.write_text(gate.json.dumps(value), encoding="utf-8")
            receipt_path = root / "e/receipt.json"
            receipt = gate.json.loads(receipt_path.read_bytes())
            receipt["selections"][0]["original_report"] = gate.digest(path)
            receipt_path.write_text(gate.json.dumps(receipt), encoding="utf-8")
            result = ClosedStudioArchiveTests().retained(root, source)
            self.assertEqual(result["status"], "FAIL", result)
            self.assertFalse((root / "retained/closed-evidence.tar").exists())

    def test_original_unknown_helper_exit_never_becomes_closed_new_case_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source = CreativeProductionStoreTests().fixture(directory, "branch-claim")
            path = root / "e/claim-source-test-results/case-0/closed-store-outcomes.json"
            value = gate.json.loads(path.read_bytes())
            value["closures"][0]["status"] = "rejected"
            value["closures"][0]["error"] = "original helper outcome unavailable"
            path.write_text(gate.json.dumps(value), encoding="utf-8")
            result = ClosedStudioArchiveTests().retained(root, source)
            self.assertEqual(result["status"], "FAIL", result)
            self.assertFalse((root / "retained/closed-evidence.tar").exists())

    def rust_log(self, package):
        cases = gate.CLAIM_SOURCE_RUST_CASES[package]
        return "\n".join("test " + name + " ... ok" for name in cases) + (
            f"\ntest result: ok. {len(cases)} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n")

    def test_all_fifteen_rust_assertions_are_required_by_exact_identity(self):
        for package, count in (("domain", 7), ("service", 8)):
            original = self.rust_log(package)
            self.assertEqual(gate.validate_claim_source_output(0, original, package)["passed"], count)
            lines = original.splitlines()
            for mutation in ("missing", "duplicate", "foreign", "ignored", "failed", "summary", "empty", "exit"):
                changed = list(lines)
                code = 0
                if mutation == "missing": changed.pop(0)
                elif mutation == "duplicate": changed[1] = changed[0]
                elif mutation == "foreign": changed[0] = "test foreign_same_count ... ok"
                elif mutation == "ignored": changed[0] = changed[0].removesuffix("ok") + "ignored"
                elif mutation == "failed": changed[0] = changed[0].removesuffix("ok") + "FAILED"
                elif mutation == "summary": changed.append(changed[-1])
                elif mutation == "empty": changed = []
                else: code = 101
                with self.subTest(package=package, mutation=mutation), self.assertRaises(ValueError):
                    gate.validate_claim_source_output(code, "\n".join(changed), package)
        with self.assertRaises(ValueError): gate.validate_claim_source_output(True, self.rust_log("domain"), "domain")
        with self.assertRaises(ValueError): gate.validate_claim_source_output(0, "", "native")

    def test_claim_unknown_stops_without_any_further_command_or_source_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            tool = root / "rustup"
            tool.write_bytes(b"synthetic executable identity")
            output = root / "out"
            command = {"exit_code": None, "observation": "UNKNOWN_TIMEOUT_NO_RETRY",
                       "process_observation_schema": gate.PROCESS_OBSERVATION_SCHEMA,
                       "process_boundary": "OWNED_POSIX_SESSION_GROUP", "pid": 111}
            with mock.patch.object(gate.sys, "platform", "linux"), \
                 mock.patch.object(gate, "source_observation", return_value={"source": "fixture"}) as source, \
                 mock.patch.object(gate.shutil, "which", return_value=str(tool)), \
                 mock.patch.object(gate, "execute", return_value=command) as execute:
                self.assertEqual(gate.run_claim_source_contracts(repo, output), 1)
            self.assertEqual(source.call_count, 1)
            self.assertEqual(execute.call_count, 1)
            receipt = gate.json.loads((output / "receipt.json").read_bytes())
            self.assertEqual(receipt["status"], "UNKNOWN")
            self.assertNotIn("source_after", receipt)
            self.assertNotIn("domain", receipt)
            self.assertNotIn("service", receipt)
            self.assertFalse(receipt["formal_acceptance"])

    def test_claim_runner_uses_existing_observer_and_closed_targets(self):
        for failing in (None, "domain", "service", "native-consumer-check"):
            with self.subTest(failing=failing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo = root / "repo"
                repo.mkdir()
                tool = root / "rustup"
                tool.write_bytes(b"synthetic executable identity")
                output = root / "out"
                calls = []
                def execute(argv, cwd, env, destination, label, timeout):
                    calls.append((label, argv))
                    content = self.rust_log(label) if label in ("domain", "service") else "synthetic identity or compilation\n"
                    (destination / (label + ".stdout.log")).write_text(content, encoding="utf-8")
                    (destination / (label + ".stderr.log")).write_bytes(b"")
                    return {"argv": argv, "cwd": str(cwd), "exit_code": 101 if label == failing else 0,
                            **synthetic_process_fields(env), "diagnostic_custody": "DIRECT_PROCESS_EXIT_OBSERVED",
                            "stdout": gate.digest(destination / (label + ".stdout.log")),
                            "stderr": gate.digest(destination / (label + ".stderr.log"))}
                with mock.patch.object(gate.sys, "platform", "linux"), \
                     mock.patch.object(gate, "source_observation", return_value={"source": "fixture"}), \
                     mock.patch.object(gate.shutil, "which", return_value=str(tool)), \
                     mock.patch.object(gate, "execute", side_effect=execute):
                    result = gate.run_claim_source_contracts(repo, output)
                receipt = gate.json.loads((output / "receipt.json").read_bytes())
                if failing is None:
                    self.assertEqual(result, 0, receipt)
                    self.assertEqual([name for name, _ in calls], ["rustc-identity", "cargo-identity", "domain", "service", "native-consumer-check"])
                    self.assertEqual(receipt["domain"]["passed"] + receipt["service"]["passed"], 15)
                    self.assertEqual(receipt["native_consumer"], "COMPILED_ONLY_NOT_EXECUTED")
                    self.assertEqual(receipt["source_before"], receipt["source_after"])
                else:
                    self.assertEqual(result, 1)
                    self.assertEqual(calls[-1][0], failing)
                    self.assertNotIn("native_consumer", receipt)
                    self.assertNotIn("source_after", receipt)
                self.assertEqual(receipt["native_render"], "NOT_RUN")
                self.assertEqual(receipt["installed_tauri"], "NOT_RUN")
                self.assertFalse(receipt["formal_acceptance"])

    def test_new_workflow_keeps_all_three_service_groups_and_complete_budgets(self):
        workflow = (PATH.parents[1] / ".github/workflows/studio-service-verification.yml").read_text(encoding="utf-8")
        self.assertIn("inputs.selection == 'all' && '[\"project-authoring\",\"creative-production\",\"branch-claim\"]'", workflow)
        self.assertIn("matrix.selection == 'branch-claim' && 120", workflow)
        self.assertIn("inputs.selection == 'branch-claim' && 105", workflow)
        self.assertIn('ci_canvas_service.py claim-source-contracts --repo . --output ../v/claim-contracts', workflow)
        self.assertIn('claim-contracts-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}', workflow)
        self.assertEqual(workflow.split('  studio-service-68:\n', 1)[0].count('ref: ${{ github.sha }}'), 3)
        self.assertGreater(120 * 60, 1200 + sum(gate.SELECTION_TIMEOUTS[row[0]] for row in gate.BRANCH_CLAIM_SELECTIONS) + 1200)
        for label, _, titles in gate.BRANCH_CLAIM_SELECTIONS:
            self.assertEqual(gate.SELECTION_TIMEOUTS[label], len(titles) * 120 + 180)
        unit = workflow.split("  creative-unit-build:\n", 1)[1].split("  real-service:\n", 1)[0]
        self.assertIn("needs: g0", unit)
        self.assertIn("github.event_name == 'workflow_dispatch'", unit)
        self.assertNotIn("npm test", unit)



def studio68_environment_fixture():
    return {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "seradotcom/motionwright", "GITHUB_JOB": gate.STUDIO68_SCOPE,
            "GITHUB_SHA": "1" * 40, "GITHUB_WORKFLOW_SHA": "1" * 40,
            "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/" + gate.STUDIO68_WORKFLOW + "@refs/heads/verify/studio-service-fixture",
            "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "1", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64"}


def studio68_report_fixture(*, executed=False):
    value = {"errors": [], "config": {"workers": 1, "shard": None, "globalSetup": None,
        "globalTeardown": None, "fullyParallel": False, "forbidOnly": True,
        "grep": {}, "grepInvert": None,
        "projects": [{"id": "chromium", "name": "chromium", "retries": 0, "repeatEach": 1}]},
        "stats": {"expected": 68 if executed else 0, "unexpected": 0, "flaky": 0,
                  "skipped": 0 if executed else 68}, "suites": [{"specs": []}]}
    for file, title, line, column, identifier in gate.STUDIO68_CASES:
        value["suites"][0]["specs"].append({"file": file, "title": title, "line": line,
            "column": column, "id": identifier, "ok": True,
            "tests": [{"projectId": "chromium", "projectName": "chromium", "expectedStatus": "passed",
                       "timeout": 120000, "annotations": [], "status": "expected" if executed else "skipped",
                       "results": [{"status": "passed", "retry": 0, "errors": []}] if executed else []}]})
    return value


def studio68_command_fixture():
    return {**synthetic_process_fields(), "diagnostic_custody": "DIRECT_PROCESS_EXIT_OBSERVED", "exit_code": 0}


class Studio68SourceContracts(unittest.TestCase):
    """Source controls; these synthetic records never establish a service run."""

    def test_gate_context_is_preserved_separately_from_exact_product_F(self):
        env = studio68_environment_fixture()
        actual = gate.studio68_context(env, gate.STUDIO68_SOURCE, platform="linux")
        self.assertEqual(actual["gate_source_sha"], env["GITHUB_SHA"])
        self.assertEqual(actual["executed_product_source_sha"], gate.STUDIO68_SOURCE)
        self.assertEqual(actual["github"]["GITHUB_SHA"], "1" * 40)
        self.assertEqual(env, studio68_environment_fixture())

    def test_foreign_product_context_attempt_or_job_is_rejected_before_commands(self):
        env = studio68_environment_fixture()
        bad = ({"GITHUB_SHA": gate.STUDIO68_SOURCE, "GITHUB_WORKFLOW_SHA": gate.STUDIO68_SOURCE},
               {"GITHUB_RUN_ATTEMPT": "2"}, {"GITHUB_JOB": "real-service"},
               {"GITHUB_WORKFLOW_SHA": "2" * 40}, {"GITHUB_EVENT_NAME": "push"},
               {"RUNNER_ARCH": "ARM64"}, {"GITHUB_WORKFLOW_REF": "foreign/workflow@main"})
        with mock.patch.object(gate, "execute") as launch:
            for change in bad:
                with self.subTest(change=change), self.assertRaises(ValueError):
                    gate.studio68_context({**env, **change}, gate.STUDIO68_SOURCE, platform="linux")
            with self.assertRaises(ValueError):
                gate.studio68_context(env, "2" * 40, platform="linux")
            launch.assert_not_called()

    def test_environment_rejects_execution_overrides_without_stripping_GitHub_identity(self):
        env = studio68_environment_fixture()
        for key in ("LD_AUDIT", "LD_LIBRARY_PATH", "LD_PRELOAD", "GIT_DIR", "GIT_INDEX_FILE", "NODE_OPTIONS",
                    "RUSTC_WRAPPER", "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER", "MOTIONWRIGHT_SERVICE_TEST_BIN",
                    "CARGO_ALIAS_CLIPPY"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "override"):
                gate.studio68_environment({**env, key: "foreign"}, Path("/owned"))
        actual = gate.studio68_environment(env, Path("/owned"))
        self.assertEqual(actual["GITHUB_SHA"], env["GITHUB_SHA"])
        self.assertEqual(actual["CARGO_HOME"], str(Path("/owned/cargo")))
        self.assertEqual(env, studio68_environment_fixture())

    def test_fixed_clock_and_phase_never_renew_deadline_or_accept_future_origin(self):
        now = [2_000_000_000]
        with mock.patch("time.monotonic_ns", side_effect=lambda: now[0]):
            with self.assertRaises(ValueError):
                gate.Studio68Clock(True)
            with self.assertRaises(ValueError):
                gate.Studio68Clock(now[0] + 1)
            clock = gate.Studio68Clock(now[0])
            phase = clock.phase(120)
            original = clock.deadline_ns
            now[0] += 100_000_000_000
            self.assertEqual(phase.remaining(120), 20)
            now[0] += 20_000_000_000
            with self.assertRaisesRegex(ValueError, "phase deadline"):
                phase.remaining(120)
            self.assertEqual(clock.deadline_ns, original)
            now[0] = original
            with self.assertRaisesRegex(ValueError, "original deadline"):
                clock.remaining(1)

    def test_caps_preserve_every_original_case_and_leave_an_explicit_custody_reserve(self):
        self.assertEqual(len(gate.STUDIO68_CASES), 68)
        self.assertEqual(68 * 120, 8160)
        self.assertEqual(gate.STUDIO68_PHASE_SECONDS["execution"], 8460)
        self.assertEqual(gate.STUDIO68_PHASE_SECONDS["readback"], 5400)
        self.assertEqual(sum(gate.STUDIO68_PHASE_SECONDS.values()), 18660)
        self.assertEqual(gate.STUDIO68_SECONDS, 19800)
        self.assertEqual(gate.STUDIO68_SECONDS - sum(gate.STUDIO68_PHASE_SECONDS.values()), 1140)
        self.assertEqual(gate.STUDIO68_PREPARATION_SECONDS, 300)
        self.assertLess(gate.STUDIO68_PREPARATION_SECONDS + gate.STUDIO68_CLEANUP_RESERVE, 1140)

    def test_work_cannot_consume_the_retention_reserve_or_restart_its_original_clock(self):
        now = [2_000_000_000]
        with mock.patch("time.monotonic_ns", side_effect=lambda: now[0]):
            clock = gate.Studio68Clock(now[0])
            deadline = clock.deadline_ns
            now[0] = deadline - 511_000_000_000
            self.assertEqual(clock.remaining(600), 1)
            now[0] += 1_000_000_000
            with self.assertRaisesRegex(ValueError, "original deadline"):
                clock.remaining(1)
            phase = clock.phase(480, retention=True)
            self.assertEqual(phase.remaining(480), 480)
            self.assertEqual(clock.deadline_ns, deadline)
            with self.assertRaisesRegex(ValueError, "single terminal"):
                clock.phase(480, retention=True)
            with self.assertRaisesRegex(ValueError, "single terminal"):
                clock.phase(120)

    def test_original_open_requires_nonblocking_before_any_possible_fifo_read(self):
        class BeforeRead(Exception):
            pass
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "original"
            path.write_bytes(b"owned")
            with mock.patch.object(gate.os, "O_NONBLOCK", 2048, create=True), \
                 mock.patch.object(gate.os, "open", side_effect=BeforeRead) as opened, \
                 mock.patch.object(gate.os, "fdopen") as reader:
                with self.assertRaises(BeforeRead):
                    gate.studio68_original(path, 128)
                self.assertEqual(opened.call_count, 1)
                self.assertEqual(opened.call_args.args[1] & 2048, 2048)
                reader.assert_not_called()

    def test_unknown_command_stops_before_original_reads_and_cannot_be_replayed(self):
        unknown = {"observation": "UNKNOWN_TIMEOUT_NO_RETRY", "exit_code": None}
        clock = mock.Mock()
        clock.remaining.return_value = 5
        records = []
        with mock.patch.object(gate, "execute", return_value=unknown) as launch, \
             mock.patch.object(gate, "studio68_original") as read:
            with self.assertRaises(gate.UnknownObservation):
                gate.studio68_command(["tool"], Path("/p"), {}, Path("/e"), "case", 5, clock, records)
            with self.assertRaisesRegex(ValueError, "already attempted"):
                gate.studio68_command(["tool"], Path("/p"), {}, Path("/e"), "case", 5, clock, records)
            self.assertEqual(launch.call_count, 1)
            read.assert_not_called()
        self.assertEqual(records[0]["status"], "UNKNOWN_NO_RETRY")

    def test_report_success_cannot_replace_nonzero_original_exit(self):
        clock = mock.Mock()
        clock.remaining.return_value = 5
        command = {**studio68_command_fixture(), "exit_code": 9}
        with mock.patch.object(gate, "execute", return_value=command), \
             mock.patch.object(gate, "studio68_original") as read:
            with self.assertRaisesRegex(ValueError, "command failed"):
                gate.studio68_command(["tool"], Path("/p"), {}, Path("/e"), "case", 5, clock, [])
            read.assert_not_called()

    def test_inner_product_unknown_with_original_exit_one_stops_before_format_and_build(self):
        import hashlib
        raw_runner = (PATH.parent / "ci_test_runner.py").read_bytes()
        self.assertEqual(hashlib.sha256(raw_runner).hexdigest(), gate.STUDIO68_RUNNER_SHA256)
        report = {"schema": "motionwright.unittest-execution/1", "status": "UNKNOWN", "exit_code": 1,
                  "steps": [{"entry_point": "discovery:test_ci_canvas_service.py", "status": "FAIL", "exit_code": 1},
                            {"entry_point": "dotted:tooling.tests.test_ci_canvas_service", "status": "UNKNOWN", "exit_code": 124}]}
        raw_report = json.dumps(report).encode()
        command = {**studio68_command_fixture(), "exit_code": 1}
        labels = []
        def observed(argv, cwd, environment, output, label, cap, clock, commands, **options):
            labels.append(label)
            if label == "product-contracts":
                self.assertIs(options["defer_exit_status"], True)
                return command, {"stdout": b"", "stderr": b"original failed runner"}
            self.assertIn(label, ["product-" + name[:-3] for name in gate.STUDIO68_POLICIES])
            return studio68_command_fixture(), {"stdout": b"", "stderr": b""}
        def original(path, limit):
            path = Path(path)
            if path.name == "ci_test_runner.py": value = raw_runner
            elif path.name == "receipt.json": value = raw_report
            elif path.name == "observed-python": value = b"original python"
            else: self.fail("No source/build may follow inner uncertainty: " + str(path))
            return value, {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        with mock.patch.object(gate, "studio68_command", side_effect=observed), \
             mock.patch.object(gate, "studio68_original", side_effect=original), \
             mock.patch.object(gate, "studio68_snapshot") as snapshot:
            with self.assertRaises(gate.UnknownObservation) as caught:
                gate.studio68_product_g0(Path("/product"), {}, {}, {"python": "/observed-python"}, {},
                                         Path("/evidence"), mock.Mock(), [], mock.Mock())
            snapshot.assert_not_called()
        self.assertEqual(labels[-1], "product-contracts")
        self.assertEqual(caught.exception.command["outer_command"]["exit_code"], 1)
        self.assertEqual(caught.exception.command["inner_receipt"]["sha256"], hashlib.sha256(raw_report).hexdigest())
        self.assertIn("runner_status_UNKNOWN", caught.exception.command["uncertainty"])

    def test_readback_uncertain_transport_is_not_downgraded_by_observed_reader_exit(self):
        identity = {"bytes": 1, "sha256": "a" * 64}
        for close in (None, {"returnCode": None, "taints": [], "errors": []},
                      {"returnCode": 0, "taints": ["request_timeout"], "errors": []},
                      {"returnCode": 0, "taints": [], "errors": ["capture thread did not finish"]}):
            receipt = {"status": "FAILED", "cases": [{"case": "observed-case", "readback": {
                "transportAttempted": True, "transportExecuted": True, "close": close}}]}
            command = {**studio68_command_fixture(), "exit_code": 1}
            with self.subTest(close=close), self.assertRaises(gate.UnknownObservation) as caught:
                gate.studio68_inner_terminal(command, receipt, identity, "readback")
            self.assertEqual(caught.exception.command["outer_command"]["exit_code"], 1)
        with self.assertRaisesRegex(ValueError, "nonzero original exit"):
            gate.studio68_inner_terminal(command, {"status": "PASS", "steps": []}, identity, "product-g0")

    def test_git_snapshot_checks_working_bytes_and_rejects_extra_checkout_inputs(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "owned.txt"
            raw = b"original source\n"
            path.write_bytes(raw)
            oid = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            extra = [b""]
            def git(repo, *args):
                self.assertEqual(repo, root)
                if args == ("rev-parse", "HEAD"):
                    return b"a" * 40 + b"\n"
                if args == ("rev-parse", "HEAD^{tree}"):
                    return b"b" * 40 + b"\n"
                if args[0] == "ls-tree":
                    return ("100644 blob " + oid + "\towned.txt\0").encode()
                if args[0] == "ls-files":
                    return extra[0]
                if args[0] == "diff":
                    return b""
                self.fail(args)
            observed = gate.studio68_snapshot(root, "a" * 40, git, expected_tree="b" * 40)
            self.assertEqual(observed["files"]["owned.txt"]["sha256"], hashlib.sha256(raw).hexdigest())
            path.write_bytes(b"changed\n")
            with self.assertRaisesRegex(ValueError, "Git blob"):
                gate.studio68_snapshot(root, "a" * 40, git)
            path.write_bytes(raw)
            extra[0] = b"foreign.py\0"
            with self.assertRaisesRegex(ValueError, "unbound checkout"):
                gate.studio68_snapshot(root, "a" * 40, git, allow_node_modules=True)
            extra[0] = b"apps/studio/node_modules/package/index.js\0"
            self.assertEqual(gate.studio68_snapshot(root, "a" * 40, git, allow_node_modules=True), observed)

    def test_product_pin_guard_does_not_accept_only_a_claimed_F_SHA(self):
        snapshot = {"sha": gate.STUDIO68_SOURCE, "tree": gate.STUDIO68_TREE,
                    "files": {k: {"sha256": v} for k, v in gate.STUDIO68_PRODUCT_PINS.items()}}
        self.assertEqual(gate.studio68_product_identity(snapshot)["sha"], gate.STUDIO68_SOURCE)
        for key in ("Cargo.lock", "apps/studio/tests/review-service.spec.ts"):
            changed = copy.deepcopy(snapshot)
            changed["files"][key]["sha256"] = "0" * 64
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "pin"):
                gate.studio68_product_identity(changed)

    def test_exact_68_roster_is_list_only_until_each_original_result_exists(self):
        selected = gate.studio68_roster(studio68_report_fixture())
        complete = gate.studio68_roster(studio68_report_fixture(executed=True), executed=True)
        self.assertEqual(selected["ids"], complete["ids"])
        self.assertEqual(selected["mode"], "enumeration")
        self.assertEqual(complete["physical_service_evidence"], "REQUIRES_CLOSED_STORE_READBACK")
        counts = {p: sum(x[0] == p for x in gate.STUDIO68_CASES) for p in gate.STUDIO68_FILES}
        self.assertEqual(counts, {"branch-authoring-service.spec.ts": 18, "brand-authoring-service.spec.ts": 8,
            "component-distillation-service.spec.ts": 12, "review-service.spec.ts": 11,
            "production-design-service.spec.ts": 19})

    def test_missing_duplicate_foreign_case_id_title_file_and_location_are_rejected(self):
        for mutation in ("missing", "duplicate", "id", "title", "file", "line", "boolean-line"):
            doc = studio68_report_fixture()
            rows = doc["suites"][0]["specs"]
            if mutation == "missing":
                rows.pop()
            elif mutation == "duplicate":
                rows[-1] = copy.deepcopy(rows[0])
            elif mutation == "boolean-line":
                rows[0]["line"] = True
            else:
                rows[0][mutation] = 999 if mutation == "line" else "foreign"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.studio68_roster(doc)

    def test_skipped_retried_expected_failure_and_reduced_timeout_never_pass(self):
        for mutation in ("skip", "retry", "failed", "timeout", "error", "duplicate-result", "bool-count"):
            doc = studio68_report_fixture(executed=True)
            case = doc["suites"][0]["specs"][0]["tests"][0]
            if mutation == "skip":
                case["expectedStatus"] = "skipped"
            elif mutation == "retry":
                case["results"][0]["retry"] = 1
            elif mutation == "failed":
                case["results"][0]["status"] = "failed"
            elif mutation == "timeout":
                case["timeout"] = 60000
            elif mutation == "error":
                case["results"][0]["errors"] = [{"message": "actual failure"}]
            elif mutation == "duplicate-result":
                case["results"].append(copy.deepcopy(case["results"][0]))
            else:
                doc["stats"]["flaky"] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.studio68_roster(doc, executed=True)

    def test_changed_project_shard_workers_and_filter_fail_before_execution(self):
        for mutation in ("workers", "bool-workers", "shard", "grep", "retry", "repeat", "second-project"):
            doc = studio68_report_fixture()
            config = doc["config"]
            if mutation == "workers": config["workers"] = 2
            elif mutation == "bool-workers": config["workers"] = True
            elif mutation == "shard": config["shard"] = {"current": 1, "total": 2}
            elif mutation == "grep": config["grep"] = {"source": "one case"}
            elif mutation == "retry": config["projects"][0]["retries"] = 1
            elif mutation == "repeat": config["projects"][0]["repeatEach"] = 2
            else: config["projects"].append(copy.deepcopy(config["projects"][0]))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.studio68_roster(doc)

    def test_playwright_list_and_execution_use_the_same_five_files_without_timeout_override(self):
        actual = gate.studio68_playwright_argv("node", Path("/cli.js"), Path("/results"))
        listing = gate.studio68_playwright_argv("node", Path("/cli.js"), Path("/results"), listing=True)
        self.assertEqual(listing, actual + ["--list"])
        self.assertEqual(actual[3:8], ["tests/" + x for x in gate.STUDIO68_FILES])
        for option in ("--workers=1", "--retries=0", "--repeat-each=1", "--forbid-only"):
            self.assertEqual(actual.count(option), 1)
        self.assertFalse(any(x.startswith(("--grep", "--timeout", "--shard")) for x in actual))

    def g0_fixture(self):
        import ast
        import hashlib
        raw = (PATH.parent / "ci_test_runner.py").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), gate.STUDIO68_RUNNER_SHA256)
        function = next(x for x in ast.parse(raw).body if isinstance(x, ast.FunctionDef) and x.name == "parse_unittest_summary")
        namespace = {"re": re}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "verified_common_parser", "exec"), namespace)
        ids = ["tooling.tests.test_ci_canvas_service.Fixture.test_one", "tooling.tests.test_ci_canvas_service.Fixture.test_two"]
        context = gate.studio68_context(studio68_environment_fixture(), gate.STUDIO68_SOURCE, platform="linux")
        expected = {"definition": "a" * 64, "ids": ids, "python": "/observed/python", "python_sha256": "c" * 64,
                    "locks": {p: "b" * 64 for p in ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")}}
        receipt = {"schema": "motionwright.unittest-execution/1", "executed_source_sha": gate.STUDIO68_SOURCE,
            "tree_sha": gate.STUDIO68_TREE, "runner_sha256": gate.STUDIO68_RUNNER_SHA256,
            "require_complete": True, "exit_code": 0, "status": "PASS", "python_executable_sha256": "c" * 64,
            "test_definition_sha256": {"tooling/tests/test_ci_canvas_service.py": expected["definition"]},
            "source_locks": expected["locks"], "github": {k: context["github"][k] for k in
                ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")}, "steps": []}
        logs = {}
        for index, label in enumerate(("discovery:test_ci_canvas_service.py", "dotted:tooling.tests.test_ci_canvas_service")):
            stderr = ("test_one (" + ids[0] + ") ... ok\n" + "test_two (" + ids[1] + ") ... ok\n"
                      + "\n----------------------------------------------------------------------\nRan 2 tests in 0.1s\n\nOK\n").encode()
            suffix = (["discover", "-s", "tooling/tests", "-p", "test_ci_canvas_service.py", "-v"]
                      if index == 0 else ["tooling.tests.test_ci_canvas_service", "-v"])
            step = {"entry_point": label, "status": "PASS", "exit_code": 0,
                    "command": [expected["python"], "-B", "-E", "-s", "-m", "unittest", *suffix],
                    "counts": namespace["parse_unittest_summary"](stderr.decode())}
            for stream, value in (("stdout", b""), ("stderr", stderr)):
                name = "receipt-" + str(index).zfill(2) + "-" + stream + ".log"
                logs[name] = value
                step[stream] = {"file": name, "sha256": hashlib.sha256(value).hexdigest()}
            receipt["steps"].append(step)
        return receipt, expected, context, logs, namespace["parse_unittest_summary"]

    def test_product_g0_admits_actual_F_receipt_with_G_provenance_and_observed_child_commands(self):
        receipt, expected, context, logs, parser = self.g0_fixture()
        result = gate.studio68_g0_receipt(receipt, expected, context, logs.__getitem__, parser)
        self.assertEqual(result["status"], "PASS_PRODUCT_CONTRACTS_ONLY")
        self.assertEqual(result["methods_per_entry"], 2)
        self.assertEqual(result["executed_product_source_sha"], gate.STUDIO68_SOURCE)

    def test_gate_G0_cannot_substitute_F_or_relabel_its_real_GitHub_context(self):
        for mutation in ("source", "tree", "github", "definition", "locks", "entrypoint", "boolean-count", "unknown",
                         "python-hash", "python-argv", "command-filter"):
            receipt, expected, context, logs, parser = self.g0_fixture()
            if mutation == "source": receipt["executed_source_sha"] = context["gate_source_sha"]
            elif mutation == "tree": receipt["tree_sha"] = "e" * 40
            elif mutation == "github": receipt["github"]["GITHUB_SHA"] = gate.STUDIO68_SOURCE
            elif mutation == "definition": receipt["test_definition_sha256"] = {}
            elif mutation == "locks": receipt["source_locks"] = {}
            elif mutation == "entrypoint": receipt["steps"].pop()
            elif mutation == "boolean-count": receipt["steps"][0]["counts"]["errors"] = False
            elif mutation == "unknown": receipt["steps"][0]["status"] = "UNKNOWN"
            elif mutation == "python-hash": receipt["python_executable_sha256"] = "d" * 64
            elif mutation == "python-argv": receipt["steps"][0]["command"][0] = "/foreign/python"
            else: receipt["steps"][0]["command"] += ["-k", "one"]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.studio68_g0_receipt(receipt, expected, context, logs.__getitem__, parser)

    def test_forged_G0_log_hash_footer_or_repeated_original_ID_is_rejected(self):
        import hashlib
        for mutation in ("bytes", "footer", "duplicate-id", "skipped", "failed", "description", "missing-terminal"):
            receipt, expected, context, logs, parser = self.g0_fixture()
            name = receipt["steps"][0]["stderr"]["file"]
            if mutation == "bytes": logs[name] += b"changed\n"
            elif mutation == "footer":
                logs[name] = logs[name].replace(b"Ran 2 tests", b"Ran 1 test")
                receipt["steps"][0]["stderr"]["sha256"] = hashlib.sha256(logs[name]).hexdigest()
            elif mutation == "duplicate-id":
                logs[name] = logs[name].replace(expected["ids"][1].encode(), expected["ids"][0].encode())
                receipt["steps"][0]["stderr"]["sha256"] = hashlib.sha256(logs[name]).hexdigest()
            else:
                replacement = {"skipped": b" ... skipped 'forged'", "failed": b" ... FAIL",
                               "description": b"\nUnbound synthetic description ... ok", "missing-terminal": b""}[mutation]
                logs[name] = logs[name].replace(b" ... ok", replacement, 1)
                receipt["steps"][0]["stderr"]["sha256"] = hashlib.sha256(logs[name]).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.studio68_g0_receipt(receipt, expected, context, logs.__getitem__, parser)

    def test_build_uses_single_offline_locked_example_with_real_Cargo_JSON_selector(self):
        args = gate.studio68_build_argv("/cargo")
        self.assertEqual(args, ["/cargo", "build", "--locked", "--offline", "--jobs", "1", "-p",
            "motionwright-service", "--example", "studio_review_session", "--message-format=json"])
        self.assertEqual(gate.studio68_binary_spec(), ({"package": "motionwright-service", "crate": "service",
            "name": "studio_review_session", "kind": "example", "source": "crates/service/examples/studio_review_session.rs"},))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "target").mkdir()
            (root / "evidence").mkdir()
            original = root / "target/observed-artifact"
            original.write_bytes(b"actual fixture binary bytes")
            identity = gate.studio68_original(original, 1024)[1]
            sdk = mock.Mock()
            sdk.select_binaries.return_value = {"studio_review_session": {"absolute_path": str(original), **identity}}
            result = gate.studio68_seal_binary(b"cargo-original", 0, root / "product", root, sdk)
            sdk.select_binaries.assert_called_once_with(b"cargo-original", 0, root / "product", root / "target",
                                                        gate.studio68_binary_spec(), testing=False)
            self.assertEqual(Path(result["retained"]["path"]).read_bytes(), original.read_bytes())
            self.assertNotIn("target/debug", result["retained"]["path"])
            sdk.verify_binaries.assert_called_once()

    def test_actual_product_policy_failure_stops_main_before_any_Cargo_producer(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            product, repository, root = parent / "product", parent / "gate", parent / "evidence-root"
            product.mkdir()
            (repository / "tooling").mkdir(parents=True)
            helper = repository / "tooling/ci_canvas_service.py"
            helper.write_bytes(b"source fixture")
            env = {**studio68_environment_fixture(), "RUNNER_TEMP": str(parent)}
            gate_snapshot = {"sha": env["GITHUB_SHA"], "tree": "2" * 40,
                "files": {k: {"sha256": v} for k, v in gate.STUDIO68_READER_PINS.items()}}
            product_snapshot = {"sha": gate.STUDIO68_SOURCE, "tree": gate.STUDIO68_TREE,
                "files": {k: {"sha256": v} for k, v in gate.STUDIO68_PRODUCT_PINS.items()}}
            tools = {k: str(parent / k) for k in ("cargo", "rustc", "rustdoc", "rustfmt", "cc", "ar", "node")}
            sdk = mock.Mock()
            with mock.patch.dict(gate.os.environ, env, clear=True), mock.patch.object(gate.sys, "platform", "linux"), \
                 mock.patch.object(gate, "__file__", str(helper)), \
                 mock.patch.object(gate, "studio68_load_helpers", return_value=sdk), \
                 mock.patch.object(gate, "studio68_snapshot", side_effect=[gate_snapshot, product_snapshot]), \
                 mock.patch.object(gate, "studio68_tools", return_value=(tools, {})), \
                 mock.patch.object(gate, "studio68_product_g0", side_effect=ValueError("actual F source lock is stale")), \
                 mock.patch.object(gate, "execute") as launch:
                code = gate.studio68_main(["--gate-repo", str(repository), "--product-repo", str(product),
                    "--product-source", gate.STUDIO68_SOURCE, "--root", str(root)])
                self.assertEqual(code, 1)
                launch.assert_not_called()
                sdk.ProducerBarrier.assert_not_called()
            receipt = json.loads((root / "evidence/STUDIO68_RESULT.json").read_bytes())
            self.assertIn("source lock is stale", receipt["error"])
            self.assertEqual(receipt["commands"], [])
            self.assertEqual(receipt["service_cases"], "NOT_RUN")

    def test_NPM_package_identity_does_not_promote_generated_Vite_cache_to_input(self):
        sdk = mock.Mock()
        package = {"sha256": "a" * 64, "bytes": 10}
        sdk.dependency_inventory.return_value = {"vite/dist/index.js": package,
            ".vite/deps/_metadata.json": {"sha256": "b" * 64}, ".vite-temp/generated.mjs": {"sha256": "c" * 64}}
        result = gate.studio68_npm_inventory(Path("/owned/node_modules"), sdk)
        self.assertEqual(result["files"], {"vite/dist/index.js": package})
        sdk.dependency_inventory.return_value["vite/dist/index.js"] = {"sha256": "d" * 64, "bytes": 10}
        self.assertNotEqual(gate.studio68_npm_inventory(Path("/owned/node_modules"), sdk), result)

    def test_changed_compiled_binary_is_rejected_before_retained_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            original = root / "observed-artifact"
            original.write_bytes(b"changed")
            sdk = mock.Mock()
            sdk.select_binaries.return_value = {"studio_review_session": {"absolute_path": str(original), "bytes": 7, "sha256": "0" * 64}}
            with self.assertRaisesRegex(ValueError, "changed before retention"):
                gate.studio68_seal_binary(b"cargo-original", 0, root / "product", root, sdk)
            self.assertFalse((root / "evidence/tools/studio_review_session").exists())

    def readback_fixture(self):
        binary = {"path": "/owned/service", "sha256": "a" * 64}
        report = {"status": "READBACK_VERIFIED", "errors": [], "expectedCases": 68,
            "backendSourceShaAssertedByCaller": gate.STUDIO68_SOURCE, "playwrightExitCodeReportedByCaller": 0,
            "transportAttempted": True, "transportExecuted": True,
            "binary": binary, "receipt": {"status": "WRITTEN"},
            "observationJournal": {"status": "CLOSED", "recordsAttempted": 206, "recordsConfirmed": 206}, "cases": []}
        ids = [r[4] for r in gate.STUDIO68_CASES]
        for index, identifier in enumerate(ids):
            report["cases"].append({"case": "owned-case-" + str(index), "captureStatus": "VERIFIED", "errors": [],
                "readback": {"status": "VERIFIED", "errors": [], "transportAttempted": True, "transportExecuted": True,
                             "processId": index + 100, "rpcCount": 5, "primaryFailure": None,
                             "projectEqual": True, "journalEqual": True, "receipt": {"status": "WRITTEN"},
                             "close": {"clean": True, "errors": [], "taints": [], "returnCode": 0,
                                       "processId": index + 100, "rpcCount": 5}},
                "playwrightIdentity": {"projectId": "chromium", "playwrightId": identifier}})
        return report, binary, ids

    def test_closed_readback_requires_same_68_IDs_and_original_reader_exit(self):
        report, binary, ids = self.readback_fixture()
        result = gate.studio68_readback_admission(report, studio68_command_fixture(), binary, ids)
        self.assertEqual(result["cases"], 68)
        self.assertEqual(result["human_acceptance"], "NOT_GRANTED")
        for command in ({**studio68_command_fixture(), "exit_code": 7},
                        {**studio68_command_fixture(), "observation": "UNKNOWN_TIMEOUT_NO_RETRY"}):
            with self.subTest(command=command), self.assertRaises(ValueError):
                gate.studio68_readback_admission(report, command, binary, ids)

    def test_missing_duplicate_store_pending_journal_foreign_binary_or_F_does_not_close(self):
        for mutation in ("missing", "duplicate", "id", "capture", "readback", "journal", "binary", "source", "boolean-exit"):
            report, binary, ids = self.readback_fixture()
            report = copy.deepcopy(report)
            if mutation == "missing": report["cases"].pop()
            elif mutation == "duplicate": report["cases"][-1] = copy.deepcopy(report["cases"][0])
            elif mutation == "id": report["cases"][0]["playwrightIdentity"]["playwrightId"] = "foreign"
            elif mutation == "capture": report["cases"][0]["captureStatus"] = "FAILED"
            elif mutation == "readback": report["cases"][0]["readback"]["status"] = "UNKNOWN"
            elif mutation == "journal": report["observationJournal"]["recordsConfirmed"] = 139
            elif mutation == "binary": report["binary"]["sha256"] = "b" * 64
            elif mutation == "source": report["backendSourceShaAssertedByCaller"] = "b" * 40
            else: report["playwrightExitCodeReportedByCaller"] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.studio68_readback_admission(report, studio68_command_fixture(), binary, ids)

    def test_reader_invocation_preserves_owner_bytes_and_binds_three_original_digests(self):
        root = Path("/owned")
        original = {k: {"sha256": c * 64} for k, c in (("execution", "a"), ("list", "b"), ("provenance", "c"))}
        args = gate.studio68_reader_argv("python", Path("/reader.py"), root, original, {"sha256": "d" * 64})
        for option, value in (("--expected-cases", "68"), ("--backend-source-sha", gate.STUDIO68_SOURCE),
                              ("--expected-playwright-sha256", "a" * 64), ("--expected-roster-sha256", "b" * 64),
                              ("--expected-provenance-sha256", "c" * 64), ("--expected-binary-sha256", "d" * 64)):
            self.assertEqual(args[args.index(option) + 1], value)
        self.assertIn("--execute-readback", args)
        self.assertEqual([item for item in args if item.endswith("-reviewed")], ["--provenance-reviewed"])
        for name, expected in gate.STUDIO68_READER_PINS.items():
            self.assertEqual(gate.digest(PATH.parents[1] / name)["sha256"], expected)

    def test_R3_original_journal_binds_each_terminal_and_preserves_sequence(self):
        report, _, _ = self.readback_fixture()
        rows = [{"schema": "motionwright.readback-observation/1", "kind": "run_before_transport", "case": None}]
        for case in report["cases"]:
            observation = copy.deepcopy(case["readback"])
            observation["receipt"]["status"] = "PENDING"
            for kind in ("launch_intent", "transport_observed", "case_closed"):
                rows.append({"schema": "motionwright.readback-observation/1", "kind": kind,
                             "case": case["case"], "observation": copy.deepcopy(observation)})
        rows.append({"schema": "motionwright.readback-observation/1", "kind": "run_before_receipt", "case": None,
                     "observation": {"cases": report["cases"]}})
        for index, row in enumerate(rows, 1): row["sequence"] = index
        def encoded(value): return b"".join((json.dumps(x) + "\n").encode() for x in value)
        raw = encoded(rows)
        self.assertEqual(gate.studio68_readback_journal(raw, report)["records"], 206)
        for mutation in ("missing", "sequence", "case", "terminal", "truncated"):
            changed = copy.deepcopy(rows)
            if mutation == "missing": changed.pop(3)
            elif mutation == "sequence": changed[3]["sequence"] = True
            elif mutation == "case": changed[3]["case"] = "foreign"
            elif mutation == "terminal": changed[3]["observation"]["close"]["returnCode"] = 3
            altered = encoded(changed)
            if mutation == "truncated": altered = altered[:-1]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.studio68_readback_journal(altered, report)

    def test_new_manual_profile_keeps_old_defaults_matrix_and_distinct_G_F_checkouts(self):
        workflow = (PATH.parents[1] / gate.STUDIO68_WORKFLOW).read_text(encoding="utf-8")
        legacy, selected = workflow.split("  studio-service-68:\n", 1)
        self.assertIn("default: authoring-drafts", legacy)
        self.assertIn("inputs.selection == 'all' && '[\"project-authoring\",\"creative-production\",\"branch-claim\"]'", legacy)
        self.assertIn("inputs.selection != 'studio-service-68'", legacy)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.selection == 'studio-service-68'", selected)
        self.assertIn("needs: g0", selected)
        self.assertEqual(selected.count("ref: ${{ github.sha }}"), 1)
        self.assertEqual(selected.count("ref: '" + gate.STUDIO68_SOURCE + "'"), 1)
        self.assertIn("timeout-minutes: 360", selected)
        self.assertIn("timeout-minutes: 335", selected)
        self.assertIn("if: always()", selected)
        self.assertIn("env -u LD_PRELOAD -u LD_LIBRARY_PATH -u LD_AUDIT", selected)
        self.assertNotIn("GITHUB_SHA:", selected)
        self.assertNotIn("strategy:", selected)
        self.assertIn("--test-directory tooling/service_readback --pattern test_readback_r3.py", legacy)
        self.assertIn("--mode discovery", legacy)


@unittest.skipUnless(gate.sys.platform == "linux" and hasattr(gate.os, "mkfifo"), "requires a real Linux FIFO")
class Studio68LinuxOriginals(unittest.TestCase):
    def test_regular_original_replaced_by_fifo_is_rejected_before_a_blocking_read(self):
        import time
        started = time.monotonic()
        actual_open = gate.os.open
        opened = []
        with tempfile.TemporaryDirectory() as temporary:
            original = Path(temporary).resolve() / "owned-original"
            original.write_bytes(b"retained original")
            def replace_before_open(path, flags, *args, **kwargs):
                self.assertEqual(Path(path), original)
                # The preimage is stopped before its unsafe syscall; no writer
                # or subprocess is needed to demonstrate the real FIFO race.
                self.assertTrue(flags & gate.os.O_NONBLOCK)
                original.unlink()
                gate.os.mkfifo(original)
                fd = actual_open(path, flags, *args, **kwargs)
                opened.append(fd)
                return fd
            with mock.patch.object(gate.os, "open", side_effect=replace_before_open):
                with self.assertRaisesRegex(ValueError, "changed before open"):
                    gate.studio68_original(original, 1024)
            self.assertEqual(len(opened), 1)
            with self.assertRaises(OSError):
                gate.os.fstat(opened[0])
        self.assertLess(time.monotonic() - started, 2)

class PartialCriterionServiceSelectionTests(unittest.TestCase):
    """Synthetic selector/custody controls; no Node, Rust or service is executed."""

    @staticmethod
    def document(filename, titles, *, listing=False):
        specs = []
        for title in titles:
            specs.append({"title": title, "file": filename, "tags": [], "ok": True,
                          "tests": [{"projectId": "chromium", "projectName": "chromium",
                                     "expectedStatus": "passed", "status": "expected",
                                     "results": [] if listing else [{"status": "passed", "retry": 0,
                                                                      "errors": [], "attachments": []}]}]})
        return {"errors": [], "stats": {"expected": 0 if listing else len(titles),
                                        "skipped": len(titles) if listing else 0,
                                        "unexpected": 0, "flaky": 0},
                "suites": [{"title": filename, "specs": specs, "suites": []}]}

    def test_closed_six_preserves_historical_all_and_studio68_scope(self):
        rows = gate.selected_selections("criterion-partials")
        self.assertEqual([(label, name, len(titles)) for label, name, titles in rows], [
            ("criterion-graph", "semantic-graph-service.spec.ts", 1),
            ("criterion-visual", "visual-language-draft-service.spec.ts", 4),
            ("criterion-caption", "caption-continuity-service.spec.ts", 1)])
        self.assertEqual(sum(len(titles) for _, _, titles in rows), 6)
        self.assertEqual(gate.selected_selections("all"), gate.SELECTIONS)
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS), 184)
        plan = gate.selection_plan("criterion-partials")
        self.assertEqual(plan["required_tests"], 6)
        self.assertEqual(sum(len(row["titles"]) for row in plan["not_selected"]), 184)
        self.assertTrue(plan["discovery_required_before_execution"])
        self.assertEqual(plan["execution_platform"], "Linux")
        self.assertFalse(plan["formal_acceptance"])
        self.assertEqual(plan["criterion_acceptance"], "NOT_ASSESSED")
        self.assertEqual(gate.PARTIAL_ORACLE_TIMEOUTS, {
            "criterion-graph": 300, "criterion-visual": 600, "criterion-caption": 300})

    def test_catalogue_missing_duplicate_and_renamed_title_are_rejected(self):
        rows = gate.PARTIAL_ORACLE_SELECTIONS
        changes = [rows[:-1], (rows[0], rows[0], rows[2]),
                   (rows[0], (rows[1][0], rows[1][1], (*rows[1][2][:-1], "another title")), rows[2])]
        for changed in changes:
            with self.subTest(changed=changed), mock.patch.object(gate, "PARTIAL_ORACLE_SELECTIONS", changed):
                with self.assertRaises(ValueError):
                    gate.selected_selections("criterion-partials")

    def test_exact_full_identity_grep_lists_and_executes_the_same_six_without_retry(self):
        for selection in gate.partial_oracle_selections():
            label, filename, titles = selection
            argv = gate.service_command("node", Path("cli.js"), Path("out"), selection)
            listed = gate.service_command("node", Path("cli.js"), Path("out"), selection, listing=True)
            self.assertEqual(listed, [*argv, "--list"])
            for flag in ("--project=chromium", "--workers=1", "--retries=0", "--repeat-each=1", "--forbid-only"):
                self.assertIn(flag, argv)
            pattern = argv[argv.index("--grep") + 1]
            for title in titles:
                actual = " ".join(("chromium", filename, title))
                self.assertIsNotNone(re.fullmatch(pattern, actual))
                for altered in ("prefix " + actual, actual + " suffix", actual.replace("chromium", "firefox", 1),
                                actual.replace(filename, "foreign.spec.ts", 1)):
                    self.assertIsNone(re.fullmatch(pattern, altered))
            with self.assertRaises(ValueError):
                gate.service_command("node", Path("cli.js"), Path("out"), (label, filename, ("foreign",)))
            with self.assertRaises(ValueError):
                gate.service_command("node", Path("cli.js"), Path("out"), selection, listing=1)

    def test_discovery_is_closed_unexecuted_and_rejects_skips_results_foreign_or_missing_ids(self):
        for _, filename, titles in gate.partial_oracle_selections():
            doc = self.document(filename, titles, listing=True)
            self.assertEqual(gate.partial_oracle_discovery(doc, filename, titles)["status"], "LISTED_NOT_EXECUTED")
            self.assertEqual(gate.partial_oracle_discovery(doc, filename, titles)["execution_results"], 0)
            for mutation in ("missing", "duplicate", "foreign", "skip", "executed", "wrong-project", "boolean-count"):
                with self.subTest(file=filename, mutation=mutation):
                    bad = copy.deepcopy(doc)
                    specs = bad["suites"][0]["specs"]
                    case = specs[0]["tests"][0]
                    if mutation == "missing": specs.pop()
                    elif mutation == "duplicate": specs.append(copy.deepcopy(specs[0]))
                    elif mutation == "foreign": specs[0]["title"] = "foreign"
                    elif mutation == "skip": case["expectedStatus"] = "skipped"
                    elif mutation == "executed": case["results"] = [{"status": "passed", "retry": 0, "errors": []}]
                    elif mutation == "wrong-project": case["projectName"] = "firefox"
                    else: bad["stats"]["expected"] = False
                    with self.assertRaises(ValueError):
                        gate.partial_oracle_discovery(bad, filename, titles)

    def test_execution_requires_every_original_identity_with_zero_skips_or_retries(self):
        for _, filename, titles in gate.partial_oracle_selections():
            good = self.document(filename, titles)
            self.assertEqual(gate.validate_browser_execution(good, filename, titles)["passed"], len(titles))
            for mutation in ("missing", "duplicate", "retry", "skip", "ancestor", "project", "result-error"):
                with self.subTest(file=filename, mutation=mutation):
                    bad = copy.deepcopy(good)
                    specs = bad["suites"][0]["specs"]
                    case = specs[0]["tests"][0]
                    if mutation == "missing": specs.pop()
                    elif mutation == "duplicate": specs.append(copy.deepcopy(specs[0]))
                    elif mutation == "retry": case["results"][0]["retry"] = 1
                    elif mutation == "skip": case["expectedStatus"] = "skipped"
                    elif mutation == "ancestor": bad["suites"][0]["title"] = "foreign describe"
                    elif mutation == "project": case["projectName"] = "foreign"
                    else: case["results"][0]["errors"] = [{"message": "original failure"}]
                    with self.assertRaises(ValueError):
                        gate.validate_browser_execution(bad, filename, titles)

    def test_source_observation_adds_exact_three_specs_and_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory(prefix="partial-source-control-") as directory:
            repo = Path(directory)
            initializing = [True]

            def git(argv, *, cwd, timeout):
                self.assertEqual(Path(cwd), repo)
                self.assertEqual(timeout, 30)
                if argv == ["git", "rev-parse", "HEAD"]: return b"a" * 40
                if argv == ["git", "rev-parse", "HEAD^{tree}"]: return b"b" * 40
                if argv == ["git", "diff", "--name-only", "HEAD", "--"]: return b""
                self.assertEqual(argv[:2], ["git", "show"])
                name = argv[2].split(":", 1)[1]
                raw = ("synthetic frozen source: " + name + "\n").encode()
                if initializing[0]:
                    path = repo / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                return raw

            with mock.patch.object(gate.subprocess, "check_output", side_effect=git), \
                    mock.patch.dict(gate.os.environ, {"GITHUB_ACTIONS": "false"}):
                old = gate.source_observation(repo)
                current = gate.source_observation(repo, "criterion-partials")
                initializing[0] = False
                expected = {"apps/studio/tests/" + row[1] for row in gate.partial_oracle_selections()}
                expected |= set(gate.PARTIAL_ORACLE_VISUAL_SOURCE_PATHS)
                self.assertEqual(set(current["files"]) - set(old["files"]), expected - set(old["files"]))
                self.assertTrue(expected <= set(current["files"]))
                for name in sorted(expected):
                    path = repo / name
                    original = path.read_bytes()
                    try:
                        path.write_bytes(original + b"changed")
                        with self.subTest(path=name), self.assertRaisesRegex(ValueError, "source differs from Git"):
                            gate.source_observation(repo, "criterion-partials")
                    finally:
                        path.write_bytes(original)

    def run_fixture(self, directory, *, unknown_label=None, bad_list=False):
        root = Path(directory)
        repo, output, target = root / "g", root / "v/e", root / "v/t"
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_bytes(b"synthetic CLI; never executed")
        for name in ("cargo", "node"):
            (repo / name).write_bytes(b"synthetic tool; never executed")
        source = {"checkout": "a" * 40, "tree": "b" * 40, "files": {"synthetic": "only"}}
        calls = []
        rows = {row[0]: row for row in gate.partial_oracle_selections()}

        def execute(argv, cwd, env, destination, label, timeout):
            calls.append(label)
            command = {"argv": argv, "cwd": str(cwd), "exit_code": 0,
                       **synthetic_process_fields(env), "diagnostic_custody": "DIRECT_PROCESS_EXIT_OBSERVED"}
            if label == unknown_label:
                command.update(observation="UNKNOWN_PROCESS_NO_RETRY", exit_code=None,
                               diagnostic_custody="UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED")
                return command
            if label == "build":
                binary = target / "debug/examples/studio_review_session"
                if gate.os.name == "nt": binary = binary.with_suffix(".exe")
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b"synthetic executable fixture; never executed")
                self.assertEqual(timeout, 1200)
            else:
                listing = label.endswith("-discovery")
                selected = label.removesuffix("-discovery")
                _, filename, titles = rows[selected]
                self.assertEqual(timeout, 60 if listing else gate.PARTIAL_ORACLE_TIMEOUTS[selected])
                self.assertEqual("--list" in argv, listing)
                value = self.document(filename, titles, listing=listing)
                if bad_list and listing:
                    value["suites"][0]["specs"].pop()
                Path(env["PLAYWRIGHT_JSON_OUTPUT_FILE"]).write_text(json.dumps(value), encoding="utf-8")
            return command

        argv = ["ci_canvas_service.py", "--repo", str(repo), "--output", str(output), "--target", str(target),
                "--selection", "criterion-partials"]
        with mock.patch("sys.argv", argv), mock.patch("sys.stdout", new=io.StringIO()), \
                mock.patch.object(gate.sys, "platform", "linux"), \
                mock.patch.object(gate, "source_observation", return_value=source) as query, \
                mock.patch.object(gate.shutil, "which", side_effect=lambda name: str(repo / name)), \
                mock.patch.object(gate, "execute", side_effect=execute):
            code = gate.main()
        return code, json.loads((output / "receipt.json").read_bytes()), calls, query.call_count

    def test_all_three_current_lists_precede_every_test_body_on_the_real_main_path(self):
        with tempfile.TemporaryDirectory(prefix="partial-main-control-") as directory:
            code, receipt, calls, source_queries = self.run_fixture(directory)
        labels = [row[0] for row in gate.partial_oracle_selections()]
        self.assertEqual(calls, ["build", *(label + "-discovery" for label in labels), *labels])
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(source_queries, 4)
        self.assertEqual(sum(row["expected_tests"] for row in receipt["selections"]), 6)
        self.assertEqual([row["verified"]["execution_results"] for row in receipt["partial_discovery"]], [0, 0, 0])

    def test_unknown_or_changed_list_stops_before_test_bodies_and_unknown_postqueries(self):
        labels = [row[0] + "-discovery" for row in gate.partial_oracle_selections()]
        for index, label in enumerate(labels):
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix="partial-unknown-control-") as directory:
                code, receipt, calls, source_queries = self.run_fixture(directory, unknown_label=label)
            self.assertEqual(code, 1)
            self.assertEqual(receipt["status"], "UNKNOWN")
            self.assertEqual(calls, ["build", *labels[:index + 1]])
            self.assertEqual(source_queries, 2)
            self.assertNotIn("source_after", receipt)
            self.assertEqual(receipt["selections"], [])
        with tempfile.TemporaryDirectory(prefix="partial-bad-list-control-") as directory:
            code, receipt, calls, _ = self.run_fixture(directory, bad_list=True)
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(calls, ["build", labels[0]])
        self.assertEqual(receipt["selections"], [])

    def store_fixture(self, directory):
        return partial_case_store_fixture(self, directory)

    def test_six_case_attachments_require_seven_distinct_original_closed_stores(self):
        with tempfile.TemporaryDirectory(prefix="partial-store-control-") as directory:
            root, inventory, values, selections, captures = self.store_fixture(directory)
            with mock.patch.object(gate, "archive_json", side_effect=lambda _root, _inventory, name: values[name]):
                actual = gate.partial_oracle_store_bindings(root, inventory, selections, captures, **partial_fixture_bindings(root))
                self.assertEqual(len(actual), 6)
                self.assertEqual(sum(len(row["manifests"]) for row in actual), 7)
                self.assertTrue(all(row["criterion_acceptance"] == "NOT_ASSESSED" for row in actual))
            for mutation in ("missing", "shared", "outcome", "taint", "incomplete-journal", "missing-attachment"):
                with self.subTest(mutation=mutation):
                    changed = copy.deepcopy(values)
                    changed_captures = copy.deepcopy(captures)
                    first = captures[0]["manifest"]
                    prefix = first.removesuffix("/manifest.json")
                    if mutation == "missing": changed_captures.pop(0)
                    elif mutation == "shared":
                        visual = changed["e/criterion-visual.json"]["suites"][0]["specs"]
                        visual[1]["tests"][0]["results"][0]["attachments"] = copy.deepcopy(
                            visual[0]["tests"][0]["results"][0]["attachments"])
                    elif mutation == "outcome": changed[prefix + "-outcomes.json"]["errors"] = ["original failure"]
                    elif mutation == "taint": changed[prefix + "-outcomes.json"]["closures"][0]["taints"] = ["uncertain"]
                    elif mutation == "incomplete-journal": changed[prefix + "/oracle.json"]["journalComplete"] = False
                    else: changed["e/criterion-graph.json"]["suites"][0]["specs"][0]["tests"][0]["results"][0]["attachments"] = []
                    with mock.patch.object(gate, "archive_json", side_effect=lambda _r, _i, name: changed[name]):
                        with self.assertRaises(ValueError):
                            gate.partial_oracle_store_bindings(root, inventory, selections, changed_captures, **partial_fixture_bindings(root))

    def test_unknown_discovery_is_quarantined_before_archive_or_source_access(self):
        with tempfile.TemporaryDirectory(prefix="partial-quarantine-control-") as directory:
            root, _, _ = ClosedStudioArchiveTests().fixture(directory)
            path = root / "e/receipt.json"
            value = json.loads(path.read_bytes())
            uncertain = {"observation": "UNKNOWN_PROCESS_NO_RETRY", "pid": 123,
                         "diagnostic_custody": "UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED"}
            value["partial_discovery"] = [{"label": "criterion-graph", "command": uncertain}]
            path.write_text(json.dumps(value), encoding="utf-8")
            with mock.patch.object(gate, "archive_inventory") as inventory, \
                    mock.patch.object(gate, "source_observation") as source, \
                    mock.patch.object(gate, "write_closed_tar") as copy_tar:
                actual = gate.retain_closed_archive(root.parent / "g", root)
            self.assertEqual(actual["status"], "UNKNOWN")
            self.assertEqual(actual["unsealed_command"], uncertain)
            inventory.assert_not_called()
            source.assert_not_called()
            copy_tar.assert_not_called()

    def test_archive_order_requires_last_list_closed_before_first_execution(self):
        lists = [{"command": {"finished_utc": "2026-10-26T12:00:0" + str(index) + "+00:00"}}
                 for index in (1, 2, 3)]
        runs = [{"command": {"started_utc": "2026-10-26T12:00:0" + str(index) + "+00:00"}}
                for index in (3, 4, 5)]
        gate.partial_oracle_list_order(lists, runs)
        for mutation in ("last-list-late", "first-test-early", "missing-list", "missing-execution", "naive", "missing-time"):
            with self.subTest(mutation=mutation):
                left, right = copy.deepcopy(lists), copy.deepcopy(runs)
                if mutation == "last-list-late": left[-1]["command"]["finished_utc"] = "2026-10-26T12:00:04+00:00"
                elif mutation == "first-test-early": right[0]["command"]["started_utc"] = "2026-10-26T12:00:02+00:00"
                elif mutation == "missing-list": left.pop()
                elif mutation == "missing-execution": right.pop()
                elif mutation == "naive": left[-1]["command"]["finished_utc"] = "2026-10-26T12:00:03"
                else: right[0]["command"].clear()
                with self.assertRaises(ValueError):
                    gate.partial_oracle_list_order(left, right)

    def test_workflow_routes_only_the_new_choice_through_the_existing_manual_g0_service_job(self):
        path = Path(__file__).resolve().parents[2] / ".github/workflows/studio-service-verification.yml"
        raw = path.read_bytes()
        added = b"options: [studio-service-68, criterion-partials, creative-production,"
        old = b"options: [studio-service-68, creative-production,"
        self.assertEqual(raw.count(added), 1)
        reverted = raw.replace(added, old, 1)
        self.assertEqual(gate.hashlib.sha256(reverted).hexdigest(),
                         "9962a222faa52eeacb24f886b4e8fe3240ad08e0830252f809a54284015a914b")
        self.assertIn(b"default: authoring-drafts", raw)
        self.assertIn(b"if: github.event_name == 'workflow_dispatch' && inputs.selection != 'studio-service-68'\n    needs: g0", raw)
        self.assertIn(b"REQUESTED_SELECTION: ${{ matrix.selection }}", raw)
        self.assertIn(b'--target ../v/t --output ../v/e --selection "$REQUESTED_SELECTION"', raw)
        self.assertIn(b"runs-on: ubuntu-24.04", raw)
        self.assertEqual(raw.count(b"criterion-partials"), 1)
        for changed in (reverted + b" ", reverted.replace(b"default: authoring-drafts", b"default: all", 1),
                        reverted.replace(b"needs: g0", b"needs: []", 1)):
            self.assertNotEqual(gate.hashlib.sha256(changed).hexdigest(),
                                "9962a222faa52eeacb24f886b4e8fe3240ad08e0830252f809a54284015a914b")



def partial_fixture_bindings(root):
    """Inert parser identities; no service executable or product result."""
    files = {name: {"bytes": 1, "sha256": gate.hashlib.sha256(name.encode()).hexdigest()}
             for name in (*gate.PARTIAL_ORACLE_VISUAL_SOURCE_PATHS,
                          "apps/studio/tests/visual-language-draft-service.spec.ts")}
    return {"binary": {"path": str(root / "t/debug/examples/studio_review_session"),
                        "bytes": 19, "sha256": "b" * 64}, "source": {"files": files}}


def partial_case_store_fixture(test, directory):
    """Synthetic documents use the existing TS schemas and independent JSON copies."""
    root = Path(directory)
    values, entries, captures, selections = {}, {}, [], []
    context = partial_fixture_bindings(root)
    stamp = "2026-10-26T12:00:00.001Z"

    def oracle(project, kinds=None, observed=stamp):
        kinds = kinds or ["synthetic_fixture_change"] * project["revision"]
        return {"schemaVersion": 1, "observedAt": observed, "project": copy.deepcopy(project),
                "journal": [{"revision": index, "change": {"type": kind}, "created_at": stamp}
                            for index, kind in enumerate(kinds, 1)],
                "journalAfterRevisionExclusive": 0, "journalComplete": True}

    def retain_store(case_root, name, source_name, database_name, project_oracle, count=1, preparation=0, restarts=1):
        prefix = case_root + "/" + name
        directory = root / case_root / source_name
        lifecycle = [{"databasePath": str(directory / database_name),
                      "exits": [{"processId": 2300 + index * 10 + lifetime, "code": 0,
                                 "signal": None, "observedAt": stamp} for lifetime in range(restarts)],
                      "taints": []} for index in range(count)]
        manifest = {"schemaVersion": 1, "capturedAt": stamp, "sourceDirectory": str(directory),
                    "snapshotDirectory": str(root / prefix / "store"), "databaseRelativePath": database_name,
                    "lifecycle": lifecycle, "entries": [{"path": "", "kind": "directory"}],
                    "sourceBeforeSha256": "a" * 64, "sourceAfterSha256": "a" * 64,
                    "snapshotSha256": "a" * 64,
                    "evidenceScope": "synthetic parser fixture; no SQLite or product execution"}
        outcome = {"schemaVersion": 1, "errors": [],
                   "preparation": [{"index": index, "status": "fulfilled"} for index in range(preparation)],
                   "closures": [{"status": "fulfilled", **copy.deepcopy(row)} for row in lifecycle],
                   "capture": copy.deepcopy(manifest), "oracle": copy.deepcopy(project_oracle)}
        values[prefix + "/manifest.json"] = copy.deepcopy(manifest)
        values[prefix + "/oracle.json"] = copy.deepcopy(project_oracle)
        values[prefix + "-outcomes.json"] = copy.deepcopy(outcome)
        captures.append({"manifest": prefix + "/manifest.json"})
        return manifest, outcome

    index = 0
    for label, filename, titles in gate.partial_oracle_selections():
        document = test.document(filename, titles)
        for spec in document["suites"][0]["specs"]:
            index += 1
            title = spec["title"]
            case_root = "e/" + label + "-test-results/case-" + str(index)
            project = {"schema_version": 1, "id": "fixture-project-" + str(index),
                       "generation": "fixture-generation-" + str(index), "active_branch": "fixture-main",
                       "revision": 3, "audio": {"transcript": []}, "deliverables": [], "scenes": []}
            if label == "criterion-graph":
                attachment = "graph-real-source-review-reopen"
                project["revision"] = 7
                graph = {"id": "fixture-graph", "scene_id": "fixture-scene",
                         "content": {"kind": "causal_diagram", "vertices": [{"id": "v1", "label": "Revised declared intervention"}], "edges": []},
                         "authorship": {"source_snapshot": {"project_id": project["id"],
                            "generation": project["generation"], "branch_id": project["active_branch"], "revision": 6}}}
                review = {"id": "fixture-review", "status": "needs_recheck",
                          "anchor": {"resource": "graph:fixture-graph", "branch_id": "fixture-main", "revision": 4}}
                project.update(production_design={"semantic_graphs": [copy.deepcopy(graph)]}, reviews=[copy.deepcopy(review)])
                events = ["upsert_semantic_graph", "add_review", "resolve_review", "upsert_semantic_graph"]
                observed = oracle(project, ["synthetic_fixture_change"] * 3 + events)
                retain_store(case_root, "closed-graph-store", "store", "semantic-graph.sqlite3", observed,
                             preparation=1, restarts=2)
                original = {"scope": "Actual App + StudioService + SQLite only", "initialRevision": 3,
                            "finalRevision": 7, "graph": graph, "review": review, "events": events,
                            "excludes": ["native rendered output", "provider parity", "installed app", "human craft approval"]}
            elif label == "criterion-visual":
                attachment = "visual-language-real-service-evidence"
                count = 2 if title == titles[-1] else 1
                project["revision"] = 13 if count == 2 else 3
                project["visual_language"] = {"name": "fixture-visual-" + str(index)}
                observed = oracle(project)
                initial = copy.deepcopy(project)
                initial["revision"] = 2
                initial_oracle = oracle(initial)
                final_checkpoint = copy.deepcopy(observed)
                final_checkpoint["observedAt"] = "2026-10-26T12:00:00.002Z"
                manifest, outcome = retain_store(case_root, "closed-store", "service", "studio.sqlite3", observed,
                                                count=count, restarts=2 if title == titles[1] else 1)
                ui = [{"path": name, "sha256": context["source"]["files"][name]["sha256"]}
                      for name in gate.PARTIAL_ORACLE_VISUAL_SOURCE_PATHS]
                original = {"schema": "motionwright.visual-language-service-evidence/1",
                            "fixture": "synthetic_authored_content", "test": title,
                            "case_result": "completed", "failure": None, "cleanupErrors": [],
                            "evidence_scope": "real_service_sqlite_cas_and_browser_visual_input_continuity",
                            "excludes": ["installed_tauri", "native_realization", "font_rights_acceptance", "human_acceptance"],
                            "ui_sources": {"start": copy.deepcopy(ui), "end": copy.deepcopy(ui), "unchanged_during_case": True},
                            "test_source_sha256": context["source"]["files"]["apps/studio/tests/visual-language-draft-service.spec.ts"]["sha256"],
                            "inspected_source_sha256": ui[0]["sha256"], "runner_sha256": context["binary"]["sha256"],
                            "initialOracle": initial_oracle,
                            "checkpoints": [{"label": "initial complete oracle before interaction", "oracle": copy.deepcopy(initial_oracle)},
                                            {"label": "observed final state before every process closes", "oracle": final_checkpoint}],
                            "finalization": outcome, "archive": {"synthetic": "not used as store admission"},
                            "services": [{"databasePath": row["databasePath"], "processExits": copy.deepcopy(row["exits"]),
                                          "lifecycleTaints": [], "stderr": "", "transcript": []} for row in manifest["lifecycle"]]}
            else:
                attachment = "caption-continuity-evidence"
                project["revision"] = 14
                project["audio"] = {"transcript": [{"id": "fixture-segment", "text": "fixture-original"}]}
                final = copy.deepcopy(project)
                final["generation"] = "fixture-imported-generation"
                final["revision"] = 15
                final["audio"]["transcript"][0]["text"] = "fixture-edited-after-import"
                imported = copy.deepcopy(project)
                imported["generation"] = final["generation"]
                stores, services = [], []
                for store_index, (source_name, current) in enumerate((("original", project), ("reimported", final))):
                    observed = oracle(current)
                    manifest, outcome = retain_store(case_root, "closed-store-" + str(store_index), source_name,
                                                     "studio.sqlite3", observed, restarts=2 if store_index == 0 else 1)
                    earlier = copy.deepcopy(observed)
                    earlier["observedAt"] = "2026-10-26T12:00:00.000Z"
                    stores.append({"oracles": [earlier], "finalization": outcome})
                    services.append({"binary": context["binary"]["path"], "database": manifest["lifecycle"][0]["databasePath"],
                                     "exits": copy.deepcopy(manifest["lifecycle"][0]["exits"]), "taints": [], "stderr": "", "transcript": []})
                original = {"schema": "motionwright.caption-continuity-test/1",
                            "scope": "real_studio_service_sqlite_cas_and_shared_desktop_caption_export",
                            "excludes": ["installed_tauri", "native_burn_in_pixels", "human_acceptance"],
                            "status": "passed", "errors": [], "stores": stores, "services": services,
                            "observations": {"source": {"project_id": project["id"], "generation": project["generation"], "revision": 14},
                                             "original": project, "imported": imported, "final": final}}
            name = case_root + "/" + attachment + ".json"
            values[name] = original
            entries[name] = {"kind": "file", "bytes": 7, "sha256": "f" * 64}
            spec["tests"][0]["results"][0]["attachments"] = [
                {"name": attachment, "contentType": "application/json", "path": str(root / name)}]
        values["e/" + label + ".json"] = document
        selections.append({"label": label})
    return root, {"entries": entries}, values, selections, captures


class PartialOriginalContentJoinTests(unittest.TestCase):
    """Real JSON reads of inert original-shaped files, never a service execution."""

    def fixture(self, directory):
        return partial_case_store_fixture(PartialCriterionServiceSelectionTests(), directory)

    def original_name(self, values, label, case_index=0):
        prefix = "e/" + label + "-test-results/"
        names = [name for name in values if name.startswith(prefix) and name.count("/") == 3
                 and not name.endswith("-outcomes.json")]
        return sorted(names)[case_index]

    def read_retained(self, root, values, selections, captures, context=None):
        inventory = {"entries": {}, "identities": {}}
        for name, value in values.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
            path.write_bytes(raw)
            inventory["entries"][name] = {"kind": "file", "bytes": len(raw), "sha256": gate.hashlib.sha256(raw).hexdigest()}
            inventory["identities"][name] = gate.archive_identity(path.stat())
        return gate.partial_oracle_store_bindings(root, inventory, selections, captures,
                                                 **(context or partial_fixture_bindings(root)))

    def assert_rejected(self, mutation, message, context_mutation=None):
        with tempfile.TemporaryDirectory(prefix="partial-original-causal-") as directory:
            root, _, values, selections, captures = self.fixture(directory)
            context = partial_fixture_bindings(root)
            mutation(values, captures)
            if context_mutation:
                context_mutation(context)
            with self.assertRaisesRegex(ValueError, message):
                self.read_retained(root, values, selections, captures, context)

    def test_original_schemas_join_six_cases_seven_stores_with_distinct_observation_times(self):
        with tempfile.TemporaryDirectory(prefix="partial-original-positive-") as directory:
            root, _, values, selections, captures = self.fixture(directory)
            result = self.read_retained(root, values, selections, captures)
            self.assertEqual(len(result), 6)
            self.assertEqual(sum(len(row["manifests"]) for row in result), 7)
            self.assertTrue(all(row["criterion_acceptance"] == "NOT_ASSESSED" for row in result))
            self.assertTrue(all(row["original_content_join"] == "EXPLICIT_CURRENT_SPEC_PROJECT_JOURNAL_LIFECYCLE" for row in result))
            visual = values[self.original_name(values, "criterion-visual")]
            self.assertNotEqual(visual["checkpoints"][-1]["oracle"]["observedAt"], visual["finalization"]["oracle"]["observedAt"])
            caption = values[self.original_name(values, "criterion-caption")]
            self.assertNotEqual(caption["stores"][0]["oracles"][0]["observedAt"], caption["stores"][0]["finalization"]["oracle"]["observedAt"])

    def test_null_or_scalar_originals_reject_after_every_retained_hash_is_recomputed(self):
        for label in ("criterion-graph", "criterion-visual", "criterion-caption"):
            for malformed in (None, [], "original", True, 1):
                with self.subTest(label=label, value=malformed):
                    self.assert_rejected(lambda values, _captures: values.__setitem__(
                        self.original_name(values, label), malformed), "partial case original must be a JSON object")

    def test_wrong_families_and_cross_case_original_swaps_reject_with_consistent_stores(self):
        for label in ("criterion-graph", "criterion-visual", "criterion-caption"):
            with self.subTest(label=label):
                other = "criterion-caption" if label != "criterion-caption" else "criterion-visual"
                self.assert_rejected(lambda values, _captures: values.__setitem__(self.original_name(values, label),
                    copy.deepcopy(values[self.original_name(values, other)])), "partial (Graph|Visual|Caption) original family")
        def swap_visual(values, _captures):
            first, second = (self.original_name(values, "criterion-visual", index) for index in (0, 1))
            title = values[first]["test"]
            values[first] = copy.deepcopy(values[second])
            values[first]["test"] = title
        self.assert_rejected(swap_visual, "partial Visual finalization differs")

    def test_graph_revisions_and_journal_segment_are_bound_to_original_events(self):
        for field, value in (("initialRevision", 2), ("initialRevision", True), ("finalRevision", 8),
                             ("events", ["upsert_semantic_graph"] * 4)):
            with self.subTest(field=field, value=value):
                self.assert_rejected(lambda values, _captures: values[self.original_name(values, "criterion-graph")].__setitem__(field, value),
                                     "partial Graph (revision|events)")
        def changed_journal(values, captures):
            prefix = captures[0]["manifest"].removesuffix("/manifest.json")
            values[prefix + "/oracle.json"]["journal"][-1]["change"]["type"] = "rename_project"
            values[prefix + "-outcomes.json"]["oracle"] = copy.deepcopy(values[prefix + "/oracle.json"])
        self.assert_rejected(changed_journal, "partial Graph events")

    def test_graph_unique_saved_objects_and_project_anchor_cannot_be_relabelled(self):
        for field in ("graph", "review"):
            with self.subTest(field=field):
                self.assert_rejected(lambda values, _captures: values[self.original_name(values, "criterion-graph")][field].__setitem__("id", "foreign"),
                                     "partial Graph or review differs")
        for mutation in ("duplicate-graph", "duplicate-review", "project", "generation", "branch", "anchor", "status"):
            def change(values, captures):
                name = self.original_name(values, "criterion-graph")
                prefix = captures[0]["manifest"].removesuffix("/manifest.json")
                project = values[prefix + "/oracle.json"]["project"]
                if mutation == "duplicate-graph": project["production_design"]["semantic_graphs"].append(copy.deepcopy(project["production_design"]["semantic_graphs"][0]))
                elif mutation == "duplicate-review": project["reviews"].append(copy.deepcopy(project["reviews"][0]))
                elif mutation in ("project", "generation", "branch"):
                    project[{"project": "id", "generation": "generation", "branch": "active_branch"}[mutation]] = "foreign"
                elif mutation == "anchor":
                    project["reviews"][0]["anchor"]["resource"] = "graph:foreign"
                    values[name]["review"] = copy.deepcopy(project["reviews"][0])
                else:
                    project["reviews"][0]["status"] = "resolved"
                    values[name]["review"] = copy.deepcopy(project["reviews"][0])
                values[prefix + "-outcomes.json"]["oracle"] = copy.deepcopy(values[prefix + "/oracle.json"])
            with self.subTest(mutation=mutation):
                self.assert_rejected(change, "partial Graph (or review|Project or anchored review)")

    def test_visual_status_title_failure_cleanup_and_source_originals_are_required(self):
        for field, value in (("case_result", "failed"), ("test", "foreign case"), ("failure", "failed"),
                             ("cleanupErrors", ["close failed"]), ("schema", "motionwright.caption-continuity-test/1")):
            with self.subTest(field=field):
                self.assert_rejected(lambda values, _captures: values[self.original_name(values, "criterion-visual")].__setitem__(field, value),
                                     "partial Visual original family")
        for mutation in ("flag", "changed", "both-changed", "missing-path", "test-source", "inspected-source"):
            def change(values, _captures):
                value = values[self.original_name(values, "criterion-visual")]
                if mutation == "flag": value["ui_sources"]["unchanged_during_case"] = False
                elif mutation in ("changed", "both-changed"):
                    value["ui_sources"]["end"][0]["sha256"] = "c" * 64
                    if mutation == "both-changed": value["ui_sources"]["start"][0]["sha256"] = "c" * 64
                elif mutation == "missing-path":
                    value["ui_sources"]["start"].pop(); value["ui_sources"]["end"].pop()
                else: value["test_source_sha256" if mutation == "test-source" else "inspected_source_sha256"] = "c" * 64
            with self.subTest(mutation=mutation):
                self.assert_rejected(change, "partial Visual original sources differ")

    def test_visual_runner_and_independently_observed_ui_source_bindings_cannot_be_substituted(self):
        self.assert_rejected(lambda values, _captures: values[self.original_name(values, "criterion-visual")].__setitem__("runner_sha256", "c" * 64),
                             "partial Visual original runner differs")
        self.assert_rejected(lambda _values, _captures: None, "partial Visual original runner differs",
                             lambda context: context["binary"].__setitem__("sha256", "c" * 64))
        self.assert_rejected(lambda _values, _captures: None, "partial Visual original sources differ",
                             lambda context: context["source"]["files"][gate.PARTIAL_ORACLE_VISUAL_SOURCE_PATHS[0]].__setitem__("sha256", "c" * 64))
        self.assert_rejected(lambda _values, _captures: None, "partial Visual observed UI source missing",
                             lambda context: context["source"]["files"].pop(gate.PARTIAL_ORACLE_VISUAL_SOURCE_PATHS[-1]))

    def test_visual_final_checkpoint_project_journal_and_exact_finalization_are_required(self):
        for mutation in ("finalization", "final-project", "final-journal", "initial-project", "last-label", "empty"):
            def change(values, _captures):
                value = values[self.original_name(values, "criterion-visual")]
                if mutation == "finalization": value["finalization"]["errors"] = ["failed"]
                elif mutation == "final-project": value["checkpoints"][-1]["oracle"]["project"]["id"] = "foreign"
                elif mutation == "final-journal": value["checkpoints"][-1]["oracle"]["journal"][-1]["change"]["type"] = "foreign"
                elif mutation == "initial-project":
                    value["initialOracle"]["project"]["generation"] = "foreign"
                    value["checkpoints"][0]["oracle"] = copy.deepcopy(value["initialOracle"])
                elif mutation == "last-label": value["checkpoints"][-1]["label"] = "unrelated observation"
                else: value["checkpoints"] = []
            with self.subTest(mutation=mutation):
                self.assert_rejected(change, "partial Visual (finalization|final checkpoint|checkpoint)")

    def test_visual_peer_count_database_exits_and_taints_match_the_original_lifecycle(self):
        for mutation in ("peer-count", "database", "exits", "taints"):
            def change(values, _captures):
                value = values[self.original_name(values, "criterion-visual", 3)]
                if mutation == "peer-count": value["services"].pop()
                elif mutation == "database": value["services"][0]["databasePath"] = "foreign.sqlite3"
                elif mutation == "exits": value["services"][0]["processExits"][0]["code"] = 1
                else: value["services"][0]["lifecycleTaints"] = ["uncertain"]
            with self.subTest(mutation=mutation):
                self.assert_rejected(change, "partial Visual services differ")

    def test_caption_status_two_stores_order_and_finalizations_cannot_be_fabricated(self):
        for mutation in ("status", "errors", "one-store", "three-stores", "swap", "finalization", "empty-oracles"):
            def change(values, _captures):
                value = values[self.original_name(values, "criterion-caption")]
                if mutation == "status": value["status"] = "failed"
                elif mutation == "errors": value["errors"] = ["failure"]
                elif mutation == "one-store": value["stores"].pop()
                elif mutation == "three-stores": value["stores"].append(copy.deepcopy(value["stores"][0]))
                elif mutation == "swap": value["stores"].reverse()
                elif mutation == "finalization": value["stores"][0]["finalization"]["oracle"]["project"]["id"] = "foreign"
                else: value["stores"][0]["oracles"] = []
            with self.subTest(mutation=mutation):
                self.assert_rejected(change, "partial Caption (original family|two-store|store or finalization)")

    def test_caption_original_final_imported_projects_and_revisions_are_bound(self):
        for mutation in ("original", "final", "swap", "imported-id", "imported-generation", "imported-revision", "descriptor", "same-generation"):
            def change(values, captures):
                value = values[self.original_name(values, "criterion-caption")]
                observations = value["observations"]
                if mutation in ("original", "final"): observations[mutation]["id"] = "foreign"
                elif mutation == "swap": observations["original"], observations["final"] = observations["final"], observations["original"]
                elif mutation.startswith("imported-"):
                    key = mutation.removeprefix("imported-")
                    observations["imported"][key] = True if key == "revision" else "foreign"
                elif mutation == "descriptor": observations["source"]["generation"] = "foreign"
                else:
                    old = observations["original"]["generation"]
                    prefix = captures[-1]["manifest"].removesuffix("/manifest.json")
                    values[prefix + "/oracle.json"]["project"]["generation"] = old
                    values[prefix + "-outcomes.json"]["oracle"] = copy.deepcopy(values[prefix + "/oracle.json"])
                    value["stores"][1]["oracles"][0]["project"]["generation"] = old
                    value["stores"][1]["finalization"] = copy.deepcopy(values[prefix + "-outcomes.json"])
                    observations["final"]["generation"] = old
                    observations["imported"]["generation"] = old
            with self.subTest(mutation=mutation):
                self.assert_rejected(change, "partial Caption (observations|original/imported Project)")

    def test_caption_binary_database_exits_and_taints_are_original_and_ordered(self):
        for key, value in (("binary", "foreign-binary"), ("database", "foreign.sqlite3"), ("exits", []), ("taints", ["uncertain"])):
            for index in (0, 1):
                with self.subTest(key=key, index=index):
                    self.assert_rejected(lambda values, _captures: values[self.original_name(values, "criterion-caption")]["services"][index].__setitem__(key, value),
                                         "partial Caption service binary or lifecycle differs")

    def test_consistent_foreign_store_paths_and_incomplete_oracles_reject_before_join(self):
        for mutation in ("source", "database", "snapshot", "preparation", "journal-gap", "revision-bool", "lifecycle", "duplicate-manifest"):
            def change(values, captures):
                prefix = captures[0]["manifest"].removesuffix("/manifest.json")
                manifest, outcome = values[prefix + "/manifest.json"], values[prefix + "-outcomes.json"]
                if mutation in ("source", "database", "snapshot"):
                    manifest[{"source": "sourceDirectory", "database": "databaseRelativePath", "snapshot": "snapshotDirectory"}[mutation]] = "foreign"
                    outcome["capture"] = copy.deepcopy(manifest)
                elif mutation == "preparation": outcome["preparation"] = []
                elif mutation in ("journal-gap", "revision-bool"):
                    observed = values[prefix + "/oracle.json"]
                    if mutation == "journal-gap": observed["journal"][0]["revision"] = 2
                    else: observed["project"]["revision"] = True
                    outcome["oracle"] = copy.deepcopy(observed)
                elif mutation == "lifecycle":
                    manifest["lifecycle"][0]["exits"] = []
                    outcome["capture"] = copy.deepcopy(manifest)
                    outcome["closures"][0]["exits"] = []
                else: captures.append(copy.deepcopy(captures[0]))
            with self.subTest(mutation=mutation):
                self.assert_rejected(change, "partial (captured source|preparation|stored|original lifecycle|captured store inventory)")

    def test_archive_passes_the_retained_binary_and_observed_sources_to_the_explicit_join(self):
        import ast
        tree = ast.parse(PATH.read_text(encoding="utf-8"))
        method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "archive_closed_bindings")
        calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "partial_oracle_store_bindings"]
        self.assertEqual(len(calls), 1)
        self.assertEqual([ast.unparse(value) for value in calls[0].args],
                         ["root", "inventory", "selections", "captures", "receipt['service_binary']", "source"])
        self.assertFalse(calls[0].keywords)
        expected = ("VisualLanguageEditor.tsx", "visualLanguageDraft.ts", "CanvasWorkspace.tsx", "App.tsx",
                    "CompositionStudy.tsx", "ExpandedStudy.tsx", "canvasInheritedInk.ts", "CameraReframeWorkbench.tsx",
                    "CreativePatchWorkbench.tsx", "ProceduralFieldWorkbench.tsx", "ProductStageWorkbench.tsx", "ProductionDesignWorkspace.tsx")
        self.assertEqual(gate.PARTIAL_ORACLE_VISUAL_SOURCE_PATHS, tuple("apps/studio/src/" + name for name in expected))
        self.assertEqual(len(gate.PARTIAL_ORACLE_SOURCE_PATHS), 15)
        self.assertEqual(set(gate.PARTIAL_ORACLE_SOURCE_PATHS),
                         set(gate.PARTIAL_ORACLE_VISUAL_SOURCE_PATHS) | {"apps/studio/tests/" + row[1] for row in gate.partial_oracle_selections()})



class Studio68PartialStateTests(unittest.TestCase):
    """Synthetic orchestration only; no SDK, Node, service or browser process."""

    def main_fixture(self, failure=None):
        import contextlib
        import types
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            product, repository, root = parent / "product", parent / "gate", parent / "evidence-root"
            studio = product / "apps/studio"
            (studio / "tests").mkdir(parents=True)
            # The real main reads these original lock bytes before the explicit
            # dependency-closure seam; no Cargo or dependency is executed here.
            (product / "Cargo.lock").write_bytes(b"# Inert source fixture\nversion = 4\n")
            cli = studio / "node_modules/@playwright/test/cli.js"
            cli.parent.mkdir(parents=True)
            cli.write_bytes(b"fixture CLI; never executed")
            (studio / "playwright.config.ts").write_bytes(b"fixture config")
            helper = repository / "tooling/ci_canvas_service.py"
            helper.parent.mkdir(parents=True)
            helper.write_bytes(b"fixture helper identity")
            reader = repository / "tooling/service_readback/service_archive_readback_r3.py"
            reader.parent.mkdir()
            reader.write_bytes(b"fixture independent reader; never executed")
            pins = {reader.relative_to(repository).as_posix(): gate.digest(reader)["sha256"]}
            env = {**studio68_environment_fixture(), "RUNNER_TEMP": str(parent)}
            gate_before = {"sha": env["GITHUB_SHA"], "tree": "2" * 40,
                           "files": {key: {"sha256": value} for key, value in pins.items()}}
            product_before = {"sha": gate.STUDIO68_SOURCE, "tree": gate.STUDIO68_TREE,
                              "files": {key: {"sha256": value} for key, value in gate.STUDIO68_PRODUCT_PINS.items()}}
            tools = {key: str(parent / key) for key in
                     ("cargo", "rustc", "rustdoc", "rustfmt", "cc", "ar", "node", "npm", "python")}
            closure = {"sources": {"sdk": {"snapshot": {"tree": "3" * 40}}}}
            calls, intents = [], []

            class FixtureBarrier:
                def __init__(self, invoke, cargo):
                    self.invoke = invoke

                def __call__(self, *args):
                    return self.invoke(*args)

                def begin_sealing(self):
                    pass

                def begin_observation(self):
                    pass

            sdk = mock.Mock()
            sdk.ProducerBarrier.side_effect = FixtureBarrier
            binary = {}

            def seal(_stdout, exit_code, _product, selected_root, _sdk):
                self.assertEqual(exit_code, 0)
                path = selected_root / "evidence/executed-service"
                path.write_bytes(b"inert selected service binary")
                binary.update(retained={"path": str(path), **gate.digest(path)}, producer_original={"fixture": True})
                return binary

            def snapshot(path, _sha, _git, **_kwargs):
                return copy.deepcopy(gate_before if path == repository else product_before)

            def fake_execute(argv, cwd, command_env, output, label, timeout):
                calls.append(label)
                self.assertIn(type(timeout), (int, float))
                self.assertGreater(timeout, 0)
                if label == "playwright-execution":
                    intent_path = output / "playwright-execution-attempt.json"
                    self.assertTrue(intent_path.is_file(), "selection intent must precede the child call")
                    intent = json.loads(intent_path.read_bytes())
                    self.assertEqual(intent["argv"], argv)
                    self.assertEqual(intent["cwd"], str(studio))
                    self.assertEqual(intent["selected_cases"], 68)
                    self.assertEqual(intent["selected_ids"], [row[4] for row in gate.STUDIO68_CASES])
                    self.assertFalse(intent["retry_authorized"])
                    intents.append(intent)
                stdout = b"{}" if label == "resolved-dependencies" else b"synthetic retained original\n"
                stderr = b""
                result = {**synthetic_process_fields(command_env), "pid": 321,
                          "argv": list(argv), "cwd": str(cwd), "started_utc": "2026-10-26T12:00:00+00:00",
                          "finished_utc": "2026-10-26T12:00:01+00:00", "exit_code": 0,
                          "diagnostic_custody": "DIRECT_PROCESS_EXIT_OBSERVED", "cleanup_errors": []}
                timeout_here = (failure == "playwright-timeout" and label == "playwright-execution"
                                or failure == "readback-timeout" and label == "independent-readback")
                if timeout_here:
                    result.update(exit_code=None, observation="UNKNOWN_TIMEOUT_NO_RETRY",
                                  diagnostic_custody="UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED")
                elif label in ("playwright-list", "playwright-execution"):
                    document = studio68_report_fixture(executed=label == "playwright-execution")
                    document["config"].update(argv=list(argv), rootDir=str(studio / "tests"),
                                              configFile=str(studio / "playwright.config.ts"))
                    if failure == "invalid-execution-report" and label == "playwright-execution":
                        document["stats"]["expected"] = 67
                    gate.write_new(Path(command_env["PLAYWRIGHT_JSON_OUTPUT_FILE"]), document)
                elif label == "independent-readback":
                    target = output / "readback"
                    target.mkdir()
                    sealed = target / "sealed-playwright-results.zip"
                    sealed.write_bytes(b"synthetic archive for final-state wiring")
                    archive = {"archive": str(sealed), "archiveSha256": gate.digest(sealed)["sha256"],
                               "treeSha256": "4" * 64}
                    gate.write_new(target / "readback-report.json", {"status": "READBACK_VERIFIED",
                        "cases": [], "archive": archive})
                    (target / "execution-observations.jsonl").write_bytes(b"{}\n")
                    gate.write_new(target / "seal-manifest.json", {
                        "schema": "motionwright.playwright-results-seal/1", **archive,
                        "sourceDirectory": str(output / "results")})
                    if failure == "readback-nonzero":
                        result["exit_code"] = 23
                for name, raw in (("stdout", stdout), ("stderr", stderr)):
                    path = output / (label + "." + name + ".log")
                    path.write_bytes(raw)
                    result[name] = gate.digest(path)
                gate.write_new(output / (label + ".command.json"), result)
                return result

            actual_original = gate.studio68_original
            def retained_original(path, cap):
                raw, identity = actual_original(path, cap)
                if failure == "intent-readback" and path.name == "playwright-execution-attempt.json":
                    return b"{}", identity
                return raw, identity

            actual_read_text = Path.read_text
            def read_text(path, *args, **kwargs):
                if path.as_posix() == "/proc/meminfo":
                    return "MemAvailable:       8388608 kB\n"
                return actual_read_text(path, *args, **kwargs)

            with contextlib.ExitStack() as stack:
                for patch in (
                    mock.patch.dict(gate.os.environ, env, clear=True),
                    mock.patch.object(gate.sys, "platform", "linux"),
                    mock.patch.object(gate, "__file__", str(helper)),
                    mock.patch.object(gate, "STUDIO68_READER_PINS", pins),
                    mock.patch.object(gate, "studio68_load_helpers", return_value=sdk),
                    mock.patch.object(gate, "studio68_snapshot", side_effect=snapshot),
                    mock.patch.object(gate, "studio68_tools", return_value=(tools, {})),
                    mock.patch.object(gate, "studio68_product_g0", return_value={"fixture": "SOURCE_ONLY"}),
                    mock.patch.object(gate.shutil, "disk_usage", return_value=types.SimpleNamespace(free=24 * 1024**3)),
                    mock.patch.object(Path, "read_text", autospec=True, side_effect=read_text),
                    mock.patch.object(gate, "studio68_dependency_closure", return_value=closure),
                    mock.patch.object(gate, "studio68_npm_inventory", return_value={"files": {}}),
                    mock.patch.object(gate, "studio68_seal_binary", side_effect=seal),
                    mock.patch.object(gate, "studio68_verify_dependencies"),
                    mock.patch.object(gate, "studio68_verify_tools"),
                    mock.patch.object(gate, "studio68_readback_admission", return_value={"fixture": "READBACK_WIRING"}),
                    mock.patch.object(gate, "studio68_readback_journal", return_value={"records": 206}),
                    mock.patch.object(gate, "studio68_original", side_effect=retained_original),
                    mock.patch.object(gate, "execute", side_effect=fake_execute),
                ):
                    stack.enter_context(patch)
                code = gate.studio68_main(["--gate-repo", str(repository), "--product-repo", str(product),
                    "--product-source", gate.STUDIO68_SOURCE, "--root", str(root)])
            receipt = json.loads((root / "evidence/STUDIO68_RESULT.json").read_bytes())
            # A setup refusal cannot satisfy a later failure/timeout oracle.
            self.assertIn("playwright-list", calls, receipt)
            self.assertIn("list", receipt, receipt)
            return code, receipt, calls, intents

    def test_original_success_separates_intent_process_report_and_closed_readback_counts(self):
        code, receipt, calls, intents = self.main_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "PASS_STUDIO68_REAL_SERVICE_CLOSED_READBACK")
        self.assertEqual(receipt["service_cases"], 68)
        state = receipt["service_case_observation"]
        self.assertTrue(state["launch_attempted"])
        self.assertTrue(state["process_start_observed"])
        self.assertEqual(state["process_identity"]["pid"], 321)
        self.assertEqual(state["playwright_validated_cases"], 68)
        self.assertEqual(state["closed_readback_validated_cases"], 68)
        self.assertEqual(len(intents), 1)
        self.assertEqual(calls.count("playwright-execution"), 1)

    def test_post_playwright_readback_failure_retains_attempt_without_final_case_credit(self):
        code, receipt, calls, intents = self.main_fixture("readback-nonzero")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "FAIL_OR_PREPARATION_REJECTED")
        self.assertEqual(receipt["service_cases"], "PLAYWRIGHT_VERIFIED_READBACK_PENDING")
        state = receipt["service_case_observation"]
        self.assertTrue(state["launch_attempted"])
        self.assertTrue(state["process_start_observed"])
        self.assertEqual(state["playwright_validated_cases"], 68)
        self.assertEqual(state["closed_readback_validated_cases"], 0)
        self.assertEqual(state["command_observation"]["exit_code"], 0)
        self.assertEqual(receipt["commands"][-1]["exit_code"], 23)
        self.assertEqual(len(intents), 1)
        self.assertEqual(calls.count("playwright-execution"), 1)

    def test_post_playwright_readback_timeout_keeps_unknown_and_original_execution_facts(self):
        code, receipt, calls, _intents = self.main_fixture("readback-timeout")
        self.assertEqual(code, 124)
        self.assertEqual(receipt["status"], "UNKNOWN_NO_RETRY")
        self.assertFalse(receipt["retry_authorized"])
        self.assertEqual(receipt["service_cases"], "PLAYWRIGHT_VERIFIED_READBACK_PENDING")
        state = receipt["service_case_observation"]
        self.assertTrue(state["process_start_observed"])
        self.assertEqual(state["playwright_validated_cases"], 68)
        self.assertEqual(state["closed_readback_validated_cases"], 0)
        self.assertEqual(state["command_observation"]["exit_code"], 0)
        self.assertEqual(calls[-1], "independent-readback")
        self.assertEqual(calls.count("playwright-execution"), 1)

    def test_playwright_timeout_retains_selected_attempt_pid_and_zero_validated_counts(self):
        code, receipt, calls, intents = self.main_fixture("playwright-timeout")
        self.assertEqual(code, 124)
        self.assertEqual(receipt["status"], "UNKNOWN_NO_RETRY")
        self.assertEqual(receipt["service_cases"], "ATTEMPTED_NOT_VALIDATED")
        state = receipt["service_case_observation"]
        self.assertTrue(state["launch_attempted"])
        self.assertTrue(state["process_start_observed"])
        self.assertEqual(state["individual_case_start"], "NOT_OBSERVED")
        self.assertEqual((state["playwright_validated_cases"], state["closed_readback_validated_cases"]), (0, 0))
        self.assertEqual(state["command_observation"]["observation"], "UNKNOWN_TIMEOUT_NO_RETRY")
        self.assertIsNone(state["command_observation"]["exit_code"])
        self.assertEqual(calls[-1], "playwright-execution")
        self.assertEqual(len(intents), 1)

    def test_invalid_playwright_report_cannot_erase_known_attempt_or_validate_the_roster(self):
        code, receipt, calls, _intents = self.main_fixture("invalid-execution-report")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["service_cases"], "ATTEMPTED_NOT_VALIDATED")
        state = receipt["service_case_observation"]
        self.assertTrue(state["launch_attempted"])
        self.assertTrue(state["process_start_observed"])
        self.assertEqual(state["command_observation"]["exit_code"], 0)
        self.assertEqual((state["playwright_validated_cases"], state["closed_readback_validated_cases"]), (0, 0))
        self.assertNotIn("independent-readback", calls)

    def test_failed_intent_readback_stops_before_execution_without_inventing_a_process(self):
        code, receipt, calls, intents = self.main_fixture("intent-readback")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["error"], "ValueError: Studio68 pre-dispatch selection intent changed")
        self.assertEqual(calls[-1], "playwright-list")
        self.assertEqual(receipt["service_cases"], "NOT_RUN")
        state = receipt["service_case_observation"]
        self.assertFalse(state["launch_attempted"])
        self.assertFalse(state["process_start_observed"])
        self.assertIsNone(state["process_identity"])
        self.assertEqual((state["playwright_validated_cases"], state["closed_readback_validated_cases"]), (0, 0))
        self.assertNotIn("playwright-execution", calls)
        self.assertEqual(intents, [])



if __name__ == "__main__":
    unittest.main()
