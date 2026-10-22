"""Negative controls for the mandatory real-service browser selection."""
import copy
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
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS), 152)
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
            'projection-navigation': 1260,
            'production-design': 1140,
            'creative-workbench': 1260,
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
        self.assertEqual(plan["full_default_tests"], 152)
        self.assertEqual(plan["scope"], "EXPLICIT_SELECTED_SERVICE_CASES")
        self.assertEqual(len(plan["required"]), 1)
        self.assertEqual(plan["required"][0]["file"], "narrative-service.spec.ts")
        self.assertEqual(plan["required"][0]["titles"], list(gate.NARRATIVE_TITLES))
        self.assertEqual(sum(len(row["titles"]) for row in plan["not_selected"]), 144)
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
        self.assertEqual(plan["full_default_tests"], 152)
        self.assertEqual(sum(len(x["titles"]) for x in plan["not_selected"]), 114)
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
                                 152 - len(titles))
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
        self.assertEqual(plan["required_tests"], 152)
        self.assertEqual(plan["not_selected"], [])
        cases = [(x["file"], title) for x in plan["required"] for title in x["titles"]]
        self.assertEqual(len(cases), 152)
        self.assertEqual(len(set(cases)), 152)
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
        self.assertEqual(plan["full_default_tests"], 152)
        self.assertEqual(sum(len(x["titles"]) for x in plan["not_selected"]), 88)
        self.assertEqual({x["label"] for x in plan["not_selected"]},
                         {row[0] for row in gate.SELECTIONS[:10] + gate.SELECTIONS[18:]})
        self.assertTrue(all(x["status"] == "NOT_SELECTED" and x["technical_credit"] == "NONE"
                            for x in plan["not_selected"]))
        self.assertFalse(plan["formal_acceptance"])
        self.assertEqual(gate.selection_plan("authoring-drafts")["required_tests"], 38)
        self.assertEqual(sum(len(titles) for _, _, titles in gate.SELECTIONS[:15]), 103)

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
        self.assertEqual(plan["full_default_tests"], 152)
        self.assertEqual(plan["required"], [{"label": label, "file": filename, "titles": list(declared)}])
        omitted = [(row["file"], title) for row in plan["not_selected"] for title in row["titles"]]
        self.assertEqual(len(omitted), 146)
        self.assertEqual(len(set(omitted)), 146)
        self.assertTrue(all(row["status"] == "NOT_SELECTED" and row["technical_credit"] == "NONE"
                            for row in plan["not_selected"]))
        self.assertFalse(plan["formal_acceptance"])
        verified = gate.validate_report(selection_report(filename, declared), filename, titles)
        self.assertEqual(verified, {"passed": 6, "skipped": 0, "retries": 0, "titles": list(declared)})
        self.assertEqual(gate.selection_plan("complete-authoring")["required_tests"], 64)
        self.assertEqual(gate.selection_plan("authoring-drafts")["required_tests"], 38)

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
        self.assertEqual(set(choices), {"all", *gate.SELECTION_GROUPS, *(row[0] for row in gate.SELECTIONS), *gate.BROWSER_SCOPES})
        service_job = workflow.split("  real-service:\n", 1)[1]
        conditions = re.findall(r"^    if: (.+)$", service_job, re.MULTILINE)
        self.assertEqual(conditions, ["github.event_name == 'workflow_dispatch'"])
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

    def executed_document(self, filename, titles):
        value = self.discovery_document()

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
                row = next(row for row in gate.BROWSER_SELECTIONS if row[0] == label)
                document = self.executed_document(row[1], row[2])
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
        self.assertEqual(sum(len(row[2]) for row in rows), 205)
        self.assertEqual(sum(len(row[2]) for row in gate.selected_selections("all")), 152)
        self.assertEqual(gate.selection_plan("complete-authoring")["required_tests"], 64)
        self.assertEqual(gate.selection_plan("authoring-drafts")["required_tests"], 38)
        plan = gate.selection_plan("browser-contracts")
        self.assertEqual(plan["scope"], "EXPLICIT_SYNTHETIC_BROWSER_CONTRACTS")
        self.assertEqual(sum(len(row["titles"]) for row in plan["not_selected"]), 152)
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
                         {"status": "LISTED_NOT_EXECUTED", "listed": 209, "selected": 205,
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
        job = workflow.split("  real-service:\n", 1)[1]
        self.assertEqual(re.findall(r"^    if: (.+)$", job, re.MULTILINE),
                         ["github.event_name == 'workflow_dispatch'"])
        self.assertGreaterEqual(job.count("!contains(fromJSON('[\"browser-contracts\",\"creative-production-browser\"]'), matrix.selection)"), 2)
        self.assertIn("matrix.selection == 'browser-contracts' && 240", job)
        self.assertIn('tooling/ci_canvas_service.py archive --repo . --evidence ../v', job)
        self.assertIn("id: archive\n        if: success()", job)
        self.assertIn('steps.archive.outcome != \'success\'', job)
        self.assertEqual(sum(gate.BROWSER_SELECTION_TIMEOUTS.values()), 12090)
        self.assertGreater(240 * 60, sum(gate.BROWSER_SELECTION_TIMEOUTS.values()) + 180 + 1200)
        for _, filename, titles in gate.BROWSER_SELECTIONS:
            source = (PATH.parents[1] / "apps/studio/tests" / filename).read_text(encoding="utf-8")
            if filename == "project-mutation.spec.ts":
                source = source.split('test.describe("real service imports and portable restore"', 1)[0]
            self.assertNotRegex(source, r"test(?:Info)?\.setTimeout|test\.slow|describe\.configure")
            self.assertGreater(180 + 30 * len(titles), 120 + 30 * len(titles))


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
   'Measured new-copy action cannot bypass a repaired UNKNOWN after corrupt reload')))
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
    'a separate reviewed intent verifies both records and preserves the exact earlier UNKNOWN'))))
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
        self.assertEqual([len(row[2]) for row in EXPECTED_CREATIVE_BROWSER], [21, 36])
        self.assertEqual([len(row[1]) for row in EXPECTED_CREATIVE_UNITS], [29, 36])

    def test_historical_titles_and_manual_all_disjoint_partition_are_preserved(self):
        digest = lambda rows: gate.hashlib.sha256(gate.json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest(gate.PROJECT_AUTHORING_SELECTIONS), HISTORICAL_SERVICE_ROSTER_SHA256)
        self.assertEqual(digest(gate.HISTORICAL_BROWSER_SELECTIONS), HISTORICAL_BROWSER_ROSTER_SHA256)
        old = gate.selected_selections("project-authoring")
        added = gate.selected_selections("creative-production")
        self.assertEqual((len(old), sum(len(row[2]) for row in old)), (19, 135))
        self.assertEqual((len(added), sum(len(row[2]) for row in added)), (2, 17))
        self.assertEqual(old + added, gate.selected_selections("all"))
        self.assertFalse({row[0] for row in old}.intersection(row[0] for row in added))
        for selection, count in (("project-authoring", 135), ("creative-production", 17), ("all", 152)):
            plan = gate.selection_plan(selection)
            self.assertEqual(plan["required_tests"], count)
            self.assertEqual(plan["full_default_tests"], 152)
            self.assertFalse(plan["formal_acceptance"])
        self.assertEqual(sum(len(row[2]) for row in gate.HISTORICAL_BROWSER_SELECTIONS), 148)
        self.assertEqual(len(gate.browser_selections()), 33)
        self.assertEqual(sum(len(row[2]) for row in gate.browser_selections()), 205)

    def test_new_browser_scope_has_exact_57_and_declares_all_unselected_scopes_without_credit(self):
        self.assertEqual(gate.browser_selections("creative-production-browser"), EXPECTED_CREATIVE_BROWSER)
        plan = gate.selection_plan("creative-production-browser")
        self.assertEqual(plan["required_tests"], 57)
        self.assertEqual(plan["discovery_tests"], 57)
        self.assertEqual(plan["excluded_service_cases"], [])
        self.assertEqual(sum(len(row["titles"]) for row in plan["not_selected"]), 152 + 148)
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
                         {"status": "LISTED_NOT_EXECUTED", "listed": 57, "selected": 57,
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
            self.assertEqual(receipt["discovery"]["verified"]["listed"], 57)
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
        for label, _, titles in EXPECTED_CREATIVE_BROWSER:
            self.assertEqual(gate.BROWSER_SELECTION_TIMEOUTS[label], len(titles) * 30 + 180)
        workflow = (PATH.parents[1] / ".github/workflows/studio-service-verification.yml").read_text()
        service = workflow.split("  real-service:\n", 1)[1]
        self.assertIn("fail-fast: false", service)
        self.assertIn("inputs.selection == 'all' && '[\"project-authoring\",\"creative-production\"]'", service)
        self.assertIn('REQUESTED_SELECTION: ${{ matrix.selection }}', service)
        self.assertIn('studio-service-${{ matrix.selection }}-${{ github.sha }}-', service)
        self.assertIn('studio-service-unsealed-${{ matrix.selection }}-${{ github.sha }}-', service)
        self.assertEqual(workflow.count('ref: ${{ github.sha }}'), 3)
        unit = workflow.split("  creative-unit-build:\n", 1)[1].split("  real-service:\n", 1)[0]
        self.assertNotIn("matrix:", unit)
        self.assertEqual(workflow.count('ci_canvas_service.py unit-build --repo . --output ../v/unit-build'), 1)
        self.assertIn("github.event_name == 'workflow_dispatch'", unit)
        self.assertIn("needs: g0", unit)
        self.assertIn('studio-creative-unit-build-${{ github.sha }}-', unit)
        self.assertNotIn('npm test', unit)
        self.assertGreater(360 * 60, 1200 + sum(gate.SELECTION_TIMEOUTS[row[0]] for row in gate.PROJECT_AUTHORING_SELECTIONS) + 1200)
        self.assertGreater(90 * 60, 1200 + 1140 + 1260 + 1200)
        self.assertGreater(75 * 60, 180 + 810 + 1260 + 1200)


class CreativeProductionStoreTests(unittest.TestCase):
    """Retain synthetic fixtures to check every new case/store binding independently."""

    def fixture(self, directory):
        old = ClosedStudioArchiveTests()
        root, source, base_store = old.fixture(directory)
        output = root / "e"
        receipt = gate.json.loads((output / "receipt.json").read_bytes())
        receipt["selection_plan"] = gate.selection_plan("creative-production")
        receipt["selections"] = []
        first_manifest = gate.json.loads((base_store.parent / "manifest.json").read_bytes())
        for label, filename, titles in EXPECTED_CREATIVE_SERVICE:
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
                if label == "production-design" and index == 0:
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
                name = label + "-service-evidence"
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

    def document(self, studio):
        return {"success": True, "numTotalTests": 65, "numPassedTests": 65,
                "numFailedTests": 0, "numPendingTests": 0, "numTodoTests": 0,
                "numFailedTestSuites": 0, "numPendingTestSuites": 0,
                "testResults": [
                    {"name": str(studio / filename), "status": "passed", "message": "",
                     "assertionResults": [{"ancestorTitles": [parent], "title": title, "fullName": parent + " " + title,
                                           "status": "passed", "failureMessages": []} for parent, title in titles]}
                    for filename, titles in EXPECTED_CREATIVE_UNITS]}

    def test_selected_unit_report_requires_both_files_and_exact_65_original_assertions(self):
        studio = Path("synthetic/studio")
        result = gate.validate_creative_production_units(self.document(studio), studio)
        self.assertEqual((result["passed"], result["files"], result["skipped"], result["retries"]), (65, 2, 0, 0))
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

    def fixture(self, directory, mutation=None):
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
                self.assertEqual(argv[2:5], ["run", "src/productionDesignDraft.test.ts", "src/creativeWorkbenchDraft.test.ts"])
                self.assertIn("--retry=0", argv)
                self.assertNotIn("src", argv)
                document = self.document(studio)
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
            code = gate.run_creative_production_units(repo, output)
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
            self.assertEqual(receipt["vitest"]["passed"], 65)
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


if __name__ == "__main__":
    unittest.main()
