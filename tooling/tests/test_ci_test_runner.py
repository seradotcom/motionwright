"""Real subprocess regressions for the unittest execution boundary."""
import contextlib
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
                         ["discovery:test_*.py", "dotted"])
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


class SummaryBoundaryTests(unittest.TestCase):
    def test_multiple_or_fabricated_summaries_are_rejected(self):
        for text in ("Ran 0 tests in 0.1s\nOK\n", "Ran 1 test in 0.1s\nOK\nOK\n",
                     "Ran 1 test in 0.1s\nOK (failures=1)\n", "Ran 1 test in 0.1s\nOK (custom=1)\n",
                     "Ran 1 test in 0.1s\nFAILED\n", "Ran 1 test in 0.1s\nOK (skipped=0, skipped=0)\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                runner.parse_unittest_summary(text)


if __name__ == "__main__":
    unittest.main()
