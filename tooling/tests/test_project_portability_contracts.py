"""Closed CI rosters must execute every applicable import case, including NTFS."""
import ast
from pathlib import Path
import re
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]


def workflow_functions():
    text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
    start = text.index("          import hashlib, json, os, re, subprocess, sys, time\n")
    end = text.index("      - name: Retain source binding and selective test evidence", start)
    tree = ast.parse(textwrap.dedent(text[start:end]))
    selected = {"storage_import_cases", "portability_test_options", "verify_test_output"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in selected]
    if {node.name for node in functions} != selected or len(functions) != len(selected):
        raise AssertionError("closed portability functions missing or duplicated")
    scope = {"re": re}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "portability-contracts", "exec"), scope)
    return scope


def passing_log(names):
    return "".join(f"test {name} ... ok\n" for name in sorted(names)) + (
        f"test result: ok. {len(names)} passed; 0 failed; 0 ignored; 0 measured; 180 filtered out;\n"
    )


class PortabilityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions = workflow_functions()

    def test_linux_and_macos_include_all_eighteen_cases(self):
        linux = self.functions["storage_import_cases"]("Linux")
        self.assertEqual(len(linux), 18)
        self.assertEqual(linux, self.functions["storage_import_cases"]("macOS"))
        self.assertEqual(sum("fifo_" in name for name in linux), 1)
        self.assertEqual(sum("symlink" in name for name in linux), 2)

    def test_windows_has_seventeen_actual_cases_not_fifteen_plus_ignored(self):
        windows = self.functions["storage_import_cases"]("Windows")
        self.assertEqual(len(windows), 17)
        self.assertEqual(sum("symlink" in name for name in windows), 2)
        self.assertFalse(any("fifo_" in name for name in windows))
        self.assertEqual(self.functions["portability_test_options"]("full", "storage", "Windows"),
                         ["--test-threads=1", "--include-ignored"])

    def test_unknown_platform_is_rejected(self):
        with self.assertRaises(ValueError):
            self.functions["storage_import_cases"]("unrecognized")

    def test_unrelated_suites_do_not_include_ignored(self):
        fn = self.functions["portability_test_options"]
        self.assertEqual(fn("full", "service", "Windows"), ["--test-threads=1"])
        self.assertEqual(fn("full", "storage", "Linux"), ["--test-threads=1"])
        self.assertEqual(fn("otio_export", "desktop-otio-export", "Windows"), ["--nocapture"])

    def test_exact_current_rosters_are_admitted(self):
        for platform in ("Linux", "macOS", "Windows"):
            with self.subTest(platform=platform):
                names = self.functions["storage_import_cases"](platform)
                self.assertTrue(self.functions["verify_test_output"](0, passing_log(names), names)[0])

    def test_historical_six_case_roster_cannot_accept_current_result(self):
        names = self.functions["storage_import_cases"]("Linux")
        historical = {name for name in names if "::tests::" in name or "::staging_tests::" in name}
        self.assertEqual(len(historical), 6)
        self.assertFalse(self.functions["verify_test_output"](0, passing_log(names), historical)[0])

    def test_two_ignored_windows_symlinks_do_not_count_as_pass(self):
        names = self.functions["storage_import_cases"]("Windows")
        text = passing_log(names)
        for name in names:
            if "symlink" in name:
                text = text.replace(f"test {name} ... ok", f"test {name} ... ignored, privilege required")
        text = text.replace("17 passed; 0 failed; 0 ignored", "15 passed; 0 failed; 2 ignored")
        self.assertFalse(self.functions["verify_test_output"](0, text, names)[0])

    def test_missing_duplicate_or_additional_case_is_rejected(self):
        names = self.functions["storage_import_cases"]("Linux")
        first, second = sorted(names)[:2]
        text = passing_log(names)
        changes = (text.replace(f"test {first} ... ok\n", ""),
                   text.replace(f"test {first} ... ok", f"test {second} ... ok"),
                   "test unowned ... ok\n" + text)
        for changed in changes:
            self.assertFalse(self.functions["verify_test_output"](0, changed, names)[0])

    def test_nonzero_exit_or_missing_or_duplicate_summary_is_rejected(self):
        names = self.functions["storage_import_cases"]("Linux")
        text = passing_log(names)
        for code, changed in ((1, text), (0, text.split("test result:")[0]),
                              (0, text + text.splitlines()[-1] + "\n")):
            self.assertFalse(self.functions["verify_test_output"](code, changed, names)[0])

    def test_failed_status_or_contradictory_summary_is_rejected(self):
        names = self.functions["storage_import_cases"]("Linux")
        text = passing_log(names)
        first = sorted(names)[0]
        for changed in (text.replace(f"test {first} ... ok", f"test {first} ... FAILED"),
                        text.replace("18 passed; 0 failed", "17 passed; 1 failed"),
                        text.replace("test result: ok", "test result: FAILED")):
            self.assertFalse(self.functions["verify_test_output"](0, changed, names)[0])


if __name__ == "__main__":
    unittest.main()
