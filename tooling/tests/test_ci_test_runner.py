"""Real subprocess regressions for the unittest execution boundary."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "ci_test_runner.py"
SPEC = importlib.util.spec_from_file_location("ci_test_runner", TOOL)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class ExecutionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-ci-boundary-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.root = self.directory / "source"
        self.root.mkdir()
        (self.root / "cases").mkdir()
        (self.root / "cases/__init__.py").write_text("", encoding="utf-8")
        self.git("init", "-q")

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-c", "core.hooksPath=" + str(self.directory / "no-hooks"),
             "-c", "user.name=Verification Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", *args], cwd=self.root, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    def source(self, name, body):
        (self.root / "cases" / name).write_text(body, encoding="utf-8")

    def commit(self):
        self.git("add", "--", "cases")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def run_suite(self, patterns=None, mode="both", complete=False, timeout=10):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run_suite(
                self.root, "cases", patterns or ["test_*.py"], mode,
                self.directory / "result.json", self.git("rev-parse", "HEAD"),
                complete, timeout,
            )

    def passing_test(self, name="test_pass.py"):
        self.source(name, "import unittest\nclass Cases(unittest.TestCase):\n"
                         "    def test_positive(self): self.assertEqual(2+2,4)\n")

    def test_success_retains_separate_entry_points_and_source_identity(self):
        self.passing_test()
        head = self.commit()
        code, report = self.run_suite()
        self.assertEqual(code, 0)
        self.assertEqual(report["executed_source_sha"], head)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual([s["counts"]["passed"] for s in report["steps"]], [1, 1])
        self.assertEqual([s["entry_point"] for s in report["steps"]],
                         ["discovery:test_*.py", "dotted:cases.test_pass"])
        for step in report["steps"]:
            self.assertEqual(step["stderr"]["sha256"],
                             runner.sha256(self.directory / step["stderr"]["file"]))

    def test_earlier_failure_cannot_be_masked_by_later_success(self):
        self.source("test_fail.py", "import unittest\nclass Cases(unittest.TestCase):\n"
                                   "    def test_negative(self): self.fail('historical failure')\n")
        self.passing_test()
        self.commit()
        code, report = self.run_suite(["test_fail.py", "test_pass.py"], mode="discovery")
        self.assertNotEqual(code, 0)
        self.assertEqual([s["exit_code"] for s in report["steps"]], [1, 0])
        self.assertEqual(report["status"], "FAIL")

    def test_multiple_subtest_failures_preserve_counts_without_inventing_successful_cases(self):
        self.source("test_subtests.py", "import unittest\nclass Cases(unittest.TestCase):\n"
                                       "    def test_subtests(self):\n"
                                       "        for value in range(3):\n"
                                       "            with self.subTest(value=value): self.fail('subtest failure')\n"
                                       "    def test_positive_one(self): self.assertEqual(2+2,4)\n"
                                       "    def test_positive_two(self): self.assertEqual(3+3,6)\n")
        self.commit()
        code, report = self.run_suite(complete=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "FAIL")
        for step in report["steps"]:
            self.assertEqual(step["status"], "FAIL")
            self.assertNotEqual(step["exit_code"], 0)
            self.assertNotIn("summary_error", step)
            self.assertEqual(step["counts"]["terminal_status"], "FAILED")
            self.assertEqual(step["counts"]["tests_run"], 3)
            self.assertEqual(step["counts"]["failures"], 3)
            self.assertEqual(step["counts"]["errors"], 0)
            self.assertIsNone(step["counts"]["passed"])
        retained = json.loads((self.directory / "result.json").read_text(encoding="utf-8"))
        self.assertTrue(all(step["counts"]["passed"] is None for step in retained["steps"]))

    def test_one_case_with_more_subtest_outcomes_than_tests_is_a_valid_failed_summary(self):
        self.source("test_subtests.py", "import unittest\nclass Cases(unittest.TestCase):\n"
                                       "    def test_subtests(self):\n"
                                       "        for value in range(3):\n"
                                       "            with self.subTest(value=value): self.fail('subtest failure')\n"
                                       "        with self.subTest(error=True): raise RuntimeError('subtest error')\n")
        self.commit()
        code, report = self.run_suite(mode="discovery", complete=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "FAIL")
        step = report["steps"][0]
        self.assertEqual(step["status"], "FAIL")
        self.assertNotIn("summary_error", step)
        self.assertEqual(step["counts"]["terminal_status"], "FAILED")
        self.assertEqual(step["counts"]["tests_run"], 1)
        self.assertEqual(step["counts"]["failures"], 3)
        self.assertEqual(step["counts"]["errors"], 1)
        self.assertIsNone(step["counts"]["passed"])

    def test_discovery_success_does_not_hide_broken_dotted_import(self):
        self.source("fixture_helper.py", "VALUE=42\n")
        self.source("test_import.py", "import unittest\nfrom fixture_helper import VALUE\n"
                                     "class Cases(unittest.TestCase):\n"
                                     "    def test_value(self): self.assertEqual(VALUE,42)\n")
        self.commit()
        code, report = self.run_suite()
        self.assertNotEqual(code, 0)
        self.assertEqual(report["steps"][0]["status"], "PASS")
        self.assertEqual(report["steps"][1]["status"], "FAIL")
        self.assertEqual(report["steps"][1]["counts"]["errors"], 1)

    def test_one_dotted_module_cannot_repair_another_modules_import_path(self):
        self.source("fixture_helper.py", "VALUE=42\n")
        self.source("test_a_path.py", "import sys,unittest\nfrom pathlib import Path\n"
                                     "sys.path.insert(0,str(Path(__file__).parent))\n"
                                     "class Cases(unittest.TestCase):\n"
                                     "    def test_positive(self): self.assertTrue(True)\n")
        self.source("test_b_import.py", "import unittest\nfrom fixture_helper import VALUE\n"
                                       "class Cases(unittest.TestCase):\n"
                                       "    def test_value(self): self.assertEqual(VALUE,42)\n")
        self.commit()
        code, report = self.run_suite()
        self.assertNotEqual(code, 0)
        self.assertEqual([s["entry_point"] for s in report["steps"]],
                         ["discovery:test_*.py", "dotted:cases.test_a_path", "dotted:cases.test_b_import"])
        self.assertEqual([s["status"] for s in report["steps"]], ["PASS", "PASS", "FAIL"])
        self.assertEqual(report["steps"][2]["counts"]["errors"], 1)
        self.assertEqual(report["status"], "FAIL")

    def test_original_crlf_and_non_utf8_stream_bytes_are_preserved(self):
        stdout_bytes = b"\xff\r\noriginal-stdout\r\n"
        stderr_bytes = b"\xfe\r\noriginal-stderr\r\n"
        self.source("test_bytes.py", "import sys,unittest\nclass Cases(unittest.TestCase):\n"
                                    "    def test_bytes(self):\n"
                                    f"        sys.stdout.buffer.write({stdout_bytes!r})\n"
                                    "        sys.stdout.buffer.flush()\n"
                                    f"        sys.stderr.buffer.write({stderr_bytes!r})\n"
                                    "        sys.stderr.buffer.flush()\n")
        self.commit()
        code, report = self.run_suite(complete=True)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")
        for step in report["steps"]:
            stdout = (self.directory / step["stdout"]["file"]).read_bytes()
            stderr = (self.directory / step["stderr"]["file"]).read_bytes()
            self.assertEqual(stdout, stdout_bytes)
            self.assertIn(stderr_bytes, stderr)
            self.assertEqual(step["stdout"]["sha256"], hashlib.sha256(stdout_bytes).hexdigest())
            self.assertEqual(step["stderr"]["sha256"], hashlib.sha256(stderr).hexdigest())

    def test_empty_test_file_is_not_pass(self):
        self.source("test_empty.py", "# no test cases\n")
        self.commit()
        code, report = self.run_suite(mode="discovery")
        self.assertNotEqual(code, 0)
        self.assertIn("zero tests", report["steps"][0]["summary_error"])

    def test_skips_remain_explicit_and_complete_gate_rejects_them(self):
        self.source("test_skip.py", "import unittest\nclass Cases(unittest.TestCase):\n"
                                   "    @unittest.skip('platform unavailable')\n"
                                   "    def test_skip(self): pass\n")
        self.commit()
        code, report = self.run_suite(mode="discovery", complete=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["steps"][0]["counts"]["skipped"], 1)
        self.assertEqual(report["steps"][0]["counts"]["passed"], 0)

    def test_permitted_skip_is_not_labeled_complete_pass(self):
        self.source("test_skip.py", "import unittest\nclass Cases(unittest.TestCase):\n"
                                   "    @unittest.skip('platform unavailable')\n"
                                   "    def test_skip(self): pass\n")
        self.commit()
        code, report = self.run_suite(mode="discovery")
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS_WITH_OMISSIONS")

    def test_expected_failure_is_not_complete_coverage(self):
        self.source("test_xfail.py", "import unittest\nclass Cases(unittest.TestCase):\n"
                                    "    @unittest.expectedFailure\n"
                                    "    def test_known_failure(self): self.fail('still broken')\n")
        self.commit()
        code, report = self.run_suite(mode="discovery", complete=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["steps"][0]["counts"]["expected_failures"], 1)

    def test_timeout_is_unknown_and_does_not_run_later_entry_point(self):
        self.source("test_timeout.py", "import unittest,time\nclass Cases(unittest.TestCase):\n"
                                      "    def test_wait(self): time.sleep(3)\n")
        self.passing_test()
        self.commit()
        code, report = self.run_suite(["test_timeout.py", "test_pass.py"],
                                     mode="discovery", timeout=0.1)
        self.assertEqual(code, 124)
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual(report["steps"][1]["status"], "NOT_RUN")

    def test_source_mutation_during_test_rejects_result(self):
        self.source("test_mutate.py", "import unittest\nfrom pathlib import Path\n"
                                     "class Cases(unittest.TestCase):\n"
                                     "    def test_mutate(self):\n"
                                     "        Path('cases/__init__.py').write_text('# changed')\n")
        self.commit()
        code, report = self.run_suite(mode="discovery")
        self.assertNotEqual(code, 0)
        self.assertIn("source_postcheck_error", report)

    def test_dirty_source_is_rejected_before_test(self):
        self.passing_test()
        self.commit()
        (self.root / "cases/__init__.py").write_text("# changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "uncommitted"):
            self.run_suite()
        self.assertFalse((self.directory / "result.json").exists())

    def test_existing_receipt_is_never_overwritten(self):
        self.passing_test()
        self.commit()
        self.run_suite()
        before = (self.directory / "result.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "not be overwritten"):
            self.run_suite()
        self.assertEqual(before, (self.directory / "result.json").read_bytes())

    def test_wrong_sha_is_rejected_before_tests(self):
        self.passing_test()
        self.commit()
        with self.assertRaisesRegex(ValueError, "source"):
            runner.run_suite(self.root, "cases", ["test_*.py"], "both",
                             self.directory / "result.json", "0" * 40, False, 10)

    def test_untracked_helper_cannot_repair_a_dotted_import(self):
        self.passing_test()
        self.commit()
        self.source("fixture_helper.py", "VALUE=42\n")
        with self.assertRaisesRegex(ValueError, "not bound"):
            self.run_suite()

    def test_ignored_helper_is_also_an_unbound_input(self):
        self.passing_test()
        (self.root / "cases/.gitignore").write_text("fixture_helper.py\n", encoding="utf-8")
        self.commit()
        self.source("fixture_helper.py", "VALUE=42\n")
        with self.assertRaisesRegex(ValueError, "not bound"):
            self.run_suite()

    def test_inherited_pythonpath_cannot_supply_a_missing_helper(self):
        self.source("test_import.py", "import unittest\nfrom outside_fixture import VALUE\n"
                                     "class Cases(unittest.TestCase):\n"
                                     "    def test_value(self): self.assertEqual(VALUE,42)\n")
        self.commit()
        external = self.directory / "external"
        external.mkdir()
        (external / "outside_fixture.py").write_text("VALUE=42\n", encoding="utf-8")
        with patch.dict(os.environ, {"PYTHONPATH": str(external)}):
            code, report = self.run_suite()
        self.assertNotEqual(code, 0)
        self.assertEqual([s["status"] for s in report["steps"]], ["FAIL", "FAIL"])

    def test_unmatched_pattern_and_path_escape_are_rejected(self):
        self.passing_test()
        self.commit()
        for folder, patterns in (("cases", ["test_missing.py"]),
                                 ("../elsewhere", ["test_*.py"]),
                                 ("cases", ["../test_*.py"])):
            with self.subTest(folder=folder, patterns=patterns), self.assertRaises(ValueError):
                runner.selected_files(self.root, folder, patterns)

    def test_cli_exit_survives_powershell_later_success(self):
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if shell is None:
            self.skipTest("PowerShell is unavailable on this platform")
        self.source("test_fail.py", "import unittest\nclass Cases(unittest.TestCase):\n"
                                   "    def test_negative(self): self.fail('must propagate')\n")
        self.passing_test()
        head = self.commit()
        def quote(text):
            return "'" + str(text).replace("'", "''") + "'"
        args = [sys.executable, str(TOOL), "--repo", str(self.root), "--test-directory", "cases",
                "--pattern", "test_fail.py", "--pattern", "test_pass.py", "--mode", "discovery",
                "--report", str(self.directory / "result.json"), "--expected-source", head]
        script = "& " + " ".join(quote(a) for a in args) + "\nexit $LASTEXITCODE\n"
        observed = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                                  capture_output=True, timeout=30,
                                  env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertNotEqual(observed.returncode, 0)
        report = json.loads((self.directory / "result.json").read_text(encoding="utf-8"))
        self.assertEqual([s["exit_code"] for s in report["steps"]], [1, 0])
        self.assertEqual(observed.returncode, report["exit_code"])


class BoundedInstalledCaptureTests(unittest.TestCase):
    """Inert child/writer controls with explicit Git-observation doubles."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-installed-capture-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.root = self.directory / "source"
        self.folder = self.root / "tooling/tests"
        self.folder.mkdir(parents=True)
        self.path = self.folder / "test_native_host_installed_shared_state.py"
        self.report = self.directory / "installed-controls.json"
        self.observations = []

    def observed_git(self, root, *args):
        self.assertEqual(root, self.root)
        self.observations.append(args)
        if args == ("rev-parse", "HEAD"): return "a" * 40
        if args == ("rev-parse", "HEAD^{tree}"): return "b" * 40
        if args[0] in {"status", "ls-files"}: return ""
        raise AssertionError("unexpected read-only Git observation")

    def run_bounded(self, **overrides):
        arguments = dict(bounded_installed_capture=True)
        arguments.update(overrides)
        with patch.object(runner, "git", side_effect=self.observed_git), contextlib.redirect_stdout(io.StringIO()):
            return runner.run_suite(self.root, "tooling/tests", [self.path.name], "discovery",
                                    self.report, "a" * 40, True, 5, **arguments)

    def reject_unsupported_platform(self):
        if sys.platform == "linux":
            return False
        with self.assertRaisesRegex(ValueError, "one complete source selection"), \
             patch.object(runner, "capture_installed_entry") as child:
            runner.run_suite(self.root, "tooling/tests", [self.path.name], "discovery",
                             self.report, "a" * 40, True, 5, bounded_installed_capture=True)
        child.assert_not_called()
        self.assertFalse(self.report.exists())
        return True

    def test_complete_real_child_keeps_non_utf8_bytes_and_can_write_a_four_mib_binary(self):
        if self.reject_unsupported_platform(): return
        import resource
        before = resource.getrlimit(resource.RLIMIT_FSIZE)
        binary = self.directory / "inert-build-output"
        self.path.write_text("import sys,unittest\nfrom pathlib import Path\nclass Cases(unittest.TestCase):\n"
            "    def test_original(self):\n"
            f"        Path({str(binary)!r}).write_bytes(b'b'*(4*1024*1024))\n"
            "        sys.stdout.buffer.write(b'\\xff\\r\\noriginal')\n")
        code, report = self.run_bounded()
        self.assertEqual((code, report["status"]), (0, "PASS"))
        step = report["steps"][0]
        self.assertEqual(step["bounded_capture"]["status"], "COMPLETE")
        self.assertEqual(step["counts"]["passed"], 1)
        self.assertEqual((self.directory / step["stdout"]["file"]).read_bytes(), b"\xff\r\noriginal")
        self.assertEqual(binary.stat().st_size, 4 * 1024 * 1024)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE), before)
        self.assertLessEqual(self.report.stat().st_size, runner.INSTALLED_RECEIPT_BYTES)
        self.assertEqual(self.observations.count(("ls-files", "--others", "--ignored", "--exclude-standard")), 2)

    def test_real_stream_overflow_stays_bounded_preserves_observed_bytes_and_is_unknown(self):
        if self.reject_unsupported_platform(): return
        for fd, name in ((1, "stdout"), (2, "stderr")):
            with self.subTest(stream=name):
                self.report = self.directory / (name + ".json")
                self.path.write_text("import os,unittest\nclass Cases(unittest.TestCase):\n"
                    "    def test_overflow(self):\n"
                    f"        os.write({fd}, b'x'*({runner.INSTALLED_STREAM_BYTES}+1))\n")
                code, report = self.run_bounded()
                self.assertNotEqual(code, 0)
                self.assertEqual(report["status"], "UNKNOWN")
                step = report["steps"][0]
                capture = step["bounded_capture"]
                self.assertTrue(capture["overflow"])
                self.assertFalse(capture["retention_exhausted"])
                self.assertEqual((self.directory / step[name]["file"]).stat().st_size, runner.INSTALLED_STREAM_BYTES)
                extra = self.directory / capture["overflow_originals"][name + "-overflow"]["file"]
                self.assertGreater(extra.stat().st_size, 0)
                self.assertLessEqual(extra.stat().st_size, runner.INSTALLED_OVERFLOW_BYTES)
                self.assertEqual(capture["observed_stream_bytes"][name],
                    capture["retained_stream_bytes"][name] + capture["retained_stream_bytes"][name + "-overflow"])

    def test_receipt_size_is_checked_before_open_and_the_opt_in_is_one_exact_complete_selection(self):
        if self.reject_unsupported_platform(): return
        self.path.write_text("import unittest\nclass Cases(unittest.TestCase):\n"
                             "    def test_original(self): self.assertEqual(2+2,4)\n")
        with patch.object(runner, "INSTALLED_RECEIPT_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "prior reservation"):
                self.run_bounded()
        self.assertFalse(self.report.exists())
        self.assertTrue((self.directory / "installed-controls-00-stderr.log").is_file())
        with patch.object(runner, "capture_installed_entry") as child:
            with self.assertRaisesRegex(ValueError, "one complete source selection"):
                runner.run_suite(self.root, "tooling/tests", [self.path.name], "both",
                    self.directory / "denied.json", "a" * 40, True, 5, bounded_installed_capture=True)
            child.assert_not_called()

    def test_original_ordinary_and_ignored_source_guards_still_deny_before_capture(self):
        self.path.write_text("# This explicitly inert source must never execute.\n")
        for ignored in (False, True):
            def observed(root, *args):
                if args[0] == "ls-files" and "--others" in args and ("--ignored" in args) == ignored:
                    return "target/generated" if ignored else "generated-original.json"
                return self.observed_git(root, *args)
            with self.subTest(ignored=ignored), patch.object(runner, "git", side_effect=observed), \
                 patch.object(runner, "capture_installed_entry") as child:
                with self.assertRaisesRegex(ValueError, "not bound to the source SHA"):
                    runner.run_suite(self.root, "tooling/tests", [self.path.name], "discovery",
                        self.report, "a" * 40, True, 5, bounded_installed_capture=sys.platform == "linux")
                child.assert_not_called()
                self.assertFalse(self.report.exists())


class SummaryBoundaryTests(unittest.TestCase):
    def test_failed_footer_counts_do_not_determine_successful_test_cases(self):
        summary = runner.parse_unittest_summary(
            "Ran 13 tests in 0.1s\nFAILED (failures=8, errors=4, skipped=1)\n")
        self.assertEqual(summary, {"terminal_status": "FAILED", "tests_run": 13,
                                   "passed": None, "failures": 8, "errors": 4, "skipped": 1,
                                   "expected_failures": 0, "unexpected_successes": 0})

    def test_windows_crlf_summary_is_accepted_as_a_derived_parsing_view(self):
        raw = b"Ran 1 test in 0.01s\r\n\r\nOK\r\n"
        summary = runner.parse_unittest_summary(raw.decode("utf-8"))
        self.assertEqual(summary["terminal_status"], "OK")
        self.assertEqual(summary["tests_run"], 1)
        self.assertEqual(summary["passed"], 1)

    def test_multiple_or_fabricated_summaries_are_rejected(self):
        for text in ("Ran 0 tests in 0.1s\nOK\n", "Ran 1 test in 0.1s\nOK\nOK\n",
                     "Ran 1 test in 0.1s\nOK (failures=1)\n", "Ran 1 test in 0.1s\nOK (custom=1)\n",
                     "Ran 1 test in 0.1s\nFAILED\n", "Ran 1 test in 0.1s\nOK (skipped=0, skipped=0)\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                runner.parse_unittest_summary(text)


if __name__ == "__main__":
    unittest.main()
