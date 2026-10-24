"""Synthetic receipt controls; no product test or installed acceptance is claimed."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from tooling.ci_suites import nsis_source_selection as gate

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

class DefinitionAndSelectionTests(unittest.TestCase):
    def test_exact_definition_and_closed_platform_counts(self):
        gate.validate_definition(b"class Cases:\n def test_one(self): pass\n def test_two(self): pass\n",
                                 ("Cases.test_one", "Cases.test_two"))
        for mode in ("discovery", "dotted"):
            linux = gate.selection_contract("test_nsis_owned_files", mode, "linux")
            windows = gate.selection_contract("test_nsis_owned_files", mode, "win32")
            self.assertEqual((linux["required_total"], linux["required_selected"]), (33, 31))
            self.assertEqual((windows["required_total"], windows["required_selected"]), (33, 33))
            prefix = ("" if mode == "discovery" else "tooling.tests.") + "test_nsis_owned_files."
            self.assertEqual({item["id"] for item in linux["not_applicable"]}, {
                prefix + "PublicationIntegrityTests.test_windows_guards_block_writes_and_capture_deletion_through_publication",
                prefix + "PublicationIntegrityTests.test_windows_preexisting_capture_writer_is_rejected_before_publication"})
            self.assertEqual(windows["not_applicable"], [])
            for platform in ("linux", "win32"):
                lock = gate.selection_contract("test_source_lock_admission", mode, platform)
                self.assertEqual((lock["required_total"], lock["required_selected"]), (45, 45))
                self.assertEqual(lock["not_applicable"], [])

    def test_same_count_wrong_name_duplicate_shadow_and_dynamic_definitions_rejected(self):
        expected = ("Cases.test_one", "Cases.test_two")
        valid = "class Cases:\n def test_one(self): pass\n def test_two(self): pass\n"
        variants = [
            valid.replace("test_two", "test_foreign"),
            valid + " def test_one(self): pass\n",
            "class Cases:\n def test_one(self): pass\nclass Cases:\n def test_two(self): pass\n",
            valid + "def load_tests(loader, tests, pattern): return []\n",
            valid.replace(" def test_two(self): pass\n", " def helper(self):\n  def test_two(self): pass\n"),
            valid.replace(" def test_two(self): pass\n", ""),
            valid + " def test_extra(self): pass\n",
        ]
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                gate.validate_definition(raw.encode(), expected)

    def test_unknown_module_mode_or_platform_rejected(self):
        for args in (("foreign", "discovery", "linux"), ("test_nsis_owned_files", "both", "linux"),
                     ("test_source_lock_admission", "dotted", "darwin")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                gate.selection_contract(*args)

    def test_dotted_import_failure_never_falls_back_to_discovery(self):
        loader = mock.Mock()
        loader.errors = ["synthetic original import failure"]
        loader.loadTestsFromName.return_value = unittest.TestSuite()
        with mock.patch.object(gate, "ROOT", Path.cwd().resolve()), \
             mock.patch.object(gate, "source_snapshot", return_value={}), \
             mock.patch.object(gate, "selected_files", return_value=[Path("original.py")]), \
             mock.patch.object(gate, "read_original", return_value=b""), \
             mock.patch.object(gate, "validate_definition"), \
             mock.patch.object(gate.unittest, "TestLoader", return_value=loader):
            with self.assertRaises(ValueError):
                gate.select_original_suite("test_nsis_owned_files", "tooling.ci_suites.nsis_source_suite")
        loader.loadTestsFromName.assert_called_once_with("tooling.tests.test_nsis_owned_files")
        loader.discover.assert_not_called()

    def test_selection_record_must_be_unique_valid_utf8_json_object(self):
        self.assertEqual(gate.parse_selection((gate.PREFIX + '{"ok":true}\n').encode()), {"ok": True})
        for raw in (b"", (gate.PREFIX + "{}\n" + gate.PREFIX + "{}\n").encode(),
                    (gate.PREFIX + '{"x":1,"x":2}\n').encode(),
                    (gate.PREFIX + "[]\n").encode(), (gate.PREFIX + "{broken\n").encode(), b"\xff"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                gate.parse_selection(raw)

class OriginalReceiptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-nsis-gate-", dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT"))
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        self.report = self.base / "receipt.json"
        files = {}
        for relative in gate.SOURCE_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = ("synthetic source " + relative + "\n").encode()
            path.write_bytes(raw)
            files[relative] = {"bytes": len(raw), "sha256": digest(raw)}
        self.before = {"executed_source_sha": "a" * 40, "tree_sha": "b" * 40, "files": files}
        self.after = copy.deepcopy(self.before)
        self.make("linux")

    def make(self, platform):
        self.platform = platform
        self.github = dict(zip(gate.GITHUB_KEYS, ["a" * 40, "fixture/workflow@ref", "1001", "1", "source-fixture"]))
        self.receipt = {
            "schema": "motionwright.unittest-execution/1", "executed_source_sha": "a" * 40,
            "tree_sha": "b" * 40, "require_complete": True, "status": "PASS", "exit_code": 0,
            "github": copy.deepcopy(self.github), "platform": "Linux-fixture" if platform == "linux" else "Windows-fixture",
            "runner_sha256": self.before["files"]["tooling/ci_test_runner.py"]["sha256"],
            "test_definition_sha256": {p: self.before["files"][p]["sha256"] for p in (
                "tooling/ci_suites/nsis_source_suite.py", "tooling/ci_suites/source_lock_suite.py")},
            "source_locks": {p: self.before["files"][p]["sha256"] for p in (
                "Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")},
            "steps": [],
        }
        for mode in ("discovery", "dotted"):
            for module in ("test_nsis_owned_files", "test_source_lock_admission"):
                adapter = gate.ROSTERS[module][2]
                contract = gate.selection_contract(module, mode, platform)
                count = contract["required_selected"]
                label = "discovery:" + adapter + ".py" if mode == "discovery" else "dotted:tooling.ci_suites." + adapter
                tail = ["discover", "-s", "tooling/ci_suites", "-p", adapter + ".py", "-v"] if mode == "discovery" else ["tooling.ci_suites." + adapter, "-v"]
                step = {"entry_point": label, "command": [sys.executable, "-B", "-E", "-s", "-m", "unittest", *tail],
                        "status": "PASS", "exit_code": 0,
                        "counts": {"terminal_status": "OK", "tests_run": count, "passed": count,
                                   **{k: 0 for k in gate.ZERO_COUNTS}}}
                self.receipt["steps"].append(step)
                index = len(self.receipt["steps"]) - 1
                record = {**contract, "discovered_ids": contract["expected_ids"], "source": copy.deepcopy(self.before)}
                self.log(index, "stdout", (gate.PREFIX + json.dumps(record) + "\n").encode())
                stderr = "".join(f"{name.rsplit('.', 1)[1]} ({name}) ... ok\n" for name in contract["selected_ids"])
                self.log(index, "stderr", (stderr + f"\nRan {count} tests in 0.01s\n\nOK\n").encode())

    def log(self, index, stream, raw):
        path = self.report.with_name(f"{self.report.stem}-{index:02d}-{stream}.log")
        path.write_bytes(raw)
        self.receipt["steps"][index][stream] = {"file": path.name, "sha256": digest(raw)}

    def read_log(self, index, stream):
        return self.report.with_name(f"{self.report.stem}-{index:02d}-{stream}.log").read_bytes()

    def admit(self):
        self.report.write_text(json.dumps(self.receipt), encoding="utf-8")
        return gate.validate_receipt(self.root, self.report, self.before, self.after, self.platform, self.github)

    def refused(self):
        with self.assertRaises(ValueError):
            self.admit()

    def test_exact_originals_admitted_for_both_platforms_without_inventing_linux_credit(self):
        for platform, invocations, unique in (("linux", 152, 76), ("win32", 156, 78)):
            self.make(platform)
            value = self.admit()
            self.assertEqual((value["status"], value["invocations"], value["unique_applicable_controls"]), ("PASS", invocations, unique))
            self.assertIs(value["release_authorized"], False)
            self.assertEqual(value["installer_execution"], "NOT_RUN")

    def test_wrong_executed_name_with_identical_count_and_rehashed_log_rejected(self):
        raw = self.read_log(0, "stderr")
        first = gate.selection_contract("test_nsis_owned_files", "discovery", "linux")["selected_ids"][0]
        self.log(0, "stderr", raw.replace(first.encode(), (first + "_foreign").encode(), 1))
        self.refused()

    def test_missing_or_duplicate_entrypoint_rejected(self):
        saved = copy.deepcopy(self.receipt)
        self.receipt["steps"].pop()
        self.refused()
        self.receipt = saved
        self.receipt["steps"][3] = copy.deepcopy(self.receipt["steps"][2])
        self.refused()

    def test_skip_unknown_not_run_and_nonzero_cannot_be_promoted(self):
        saved = copy.deepcopy(self.receipt)
        for status in ("UNKNOWN", "NOT_RUN", "PASS_WITH_OMISSIONS", "FAIL"):
            self.receipt = copy.deepcopy(saved)
            self.receipt["steps"][0]["status"] = status
            self.refused()
        self.receipt = copy.deepcopy(saved)
        self.receipt["steps"][0]["counts"]["skipped"] = 1
        self.receipt["steps"][0]["counts"]["passed"] -= 1
        self.refused()
        self.receipt = copy.deepcopy(saved)
        self.receipt["steps"][0]["exit_code"] = 101
        self.refused()

    def test_boolean_exit_or_count_is_not_a_numeric_success(self):
        saved = copy.deepcopy(self.receipt)
        for container, field in (("receipt", "exit_code"), ("step", "exit_code"), ("counts", "failures")):
            self.receipt = copy.deepcopy(saved)
            target = self.receipt if container == "receipt" else self.receipt["steps"][0] if container == "step" else self.receipt["steps"][0]["counts"]
            target[field] = False
            self.refused()

    def test_foreign_sha_tree_run_or_platform_rejected(self):
        saved = copy.deepcopy(self.receipt)
        for field, value in (("executed_source_sha", "c" * 40), ("tree_sha", "d" * 40), ("platform", "Windows-fixture")):
            self.receipt = copy.deepcopy(saved)
            self.receipt[field] = value
            self.refused()
        self.receipt = saved
        self.receipt["github"]["GITHUB_RUN_ATTEMPT"] = "2"
        self.refused()

    def test_changed_source_or_missing_dependency_rejected(self):
        self.after["files"]["Cargo.lock"]["sha256"] = "f" * 64
        self.refused()
        self.after = copy.deepcopy(self.before)
        self.before["files"].pop("Cargo.lock")
        self.after = copy.deepcopy(self.before)
        self.refused()

    def test_runner_definition_lock_or_command_substitution_rejected(self):
        saved = copy.deepcopy(self.receipt)
        for field in ("runner_sha256", "test_definition_sha256", "source_locks"):
            self.receipt = copy.deepcopy(saved)
            self.receipt[field] = "0" * 64 if field == "runner_sha256" else {}
            self.refused()
        self.receipt = saved
        self.receipt["steps"][2]["command"][-2] = "tooling.tests.unreviewed"
        self.refused()

    def test_corrupt_or_redirected_original_log_rejected(self):
        saved = copy.deepcopy(self.receipt)
        self.receipt["steps"][0]["stdout"]["sha256"] = "0" * 64
        self.refused()
        self.receipt = saved
        self.receipt["steps"][0]["stderr"]["file"] = "../foreign.log"
        self.refused()

    def test_rehashed_selection_source_and_duplicate_records_rejected(self):
        raw = self.read_log(0, "stdout")
        record = gate.parse_selection(raw)
        record["source"]["executed_source_sha"] = "d" * 40
        self.log(0, "stdout", (gate.PREFIX + json.dumps(record) + "\n").encode())
        self.refused()
        self.log(0, "stdout", raw + raw)
        self.refused()

    def test_rehashed_missing_or_second_footer_rejected(self):
        raw = self.read_log(0, "stderr")
        self.log(0, "stderr", raw.split(b"\nRan ", 1)[0])
        self.refused()
        self.log(0, "stderr", raw + b"\nRan 31 tests in 0.01s\n\nOK\n")
        self.refused()

    def test_admission_does_not_rewrite_original_receipts_or_logs(self):
        self.admit()
        before = {p.name: p.read_bytes() for p in self.base.iterdir() if p.is_file()}
        gate.validate_receipt(self.root, self.report, self.before, self.after, self.platform, self.github)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.base.iterdir() if p.is_file()})


    def test_expected_interleaved_diagnostic_keeps_both_original_entrypoint_identities(self):
        suffix = "DistributionOwnedFilesTests.test_admitted_main_capture_mutation_cannot_publish_an_include"
        method = suffix.rsplit(".", 1)[1]
        for platform in ("linux", "win32"):
            self.make(platform)
            for index, prefix in ((0, "test_nsis_owned_files."), (2, "tooling.tests.test_nsis_owned_files.")):
                raw = self.read_log(index, "stderr")
                original = f"{method} ({prefix}{suffix}) ... ok\n".encode()
                retained = (f"{method} ({prefix}{suffix}) ... "
                            "nsis_owned_files: capture changed during capture\nok\n").encode()
                self.assertEqual(raw.count(original), 1)
                self.log(index, "stderr", raw.replace(original, retained, 1))
            admitted = self.admit()
            self.assertEqual(admitted["invocations"], 152 if platform == "linux" else 156)

    def test_interleaved_diagnostic_cannot_supply_a_missing_foreign_or_failed_result(self):
        suffix = "DistributionOwnedFilesTests.test_admitted_main_capture_mutation_cannot_publish_an_include"
        method = suffix.rsplit(".", 1)[1]
        identity = "test_nsis_owned_files." + suffix
        original = f"{method} ({identity}) ... ok\n".encode()
        header = f"{method} ({identity}) ... expected diagnostic\n".encode()
        controls = (
            header,
            header + b"FAIL\n",
            header + b"ERROR\n",
            header + b"skipped 'unavailable'\n",
            header + b"ok\nok\n",
            header + original,
            header + b"ok\n" + original,
            header.replace(identity.encode(), (identity + "_foreign").encode()) + b"ok\n",
            header.replace(method.encode(), b"test_foreign", 1) + b"ok\n",
            b"ok\n" + original,
        )
        for control in controls:
            with self.subTest(control=control):
                self.make("linux")
                raw = self.read_log(0, "stderr")
                self.assertEqual(raw.count(original), 1)
                self.log(0, "stderr", raw.replace(original, control, 1))
                self.refused()

    def test_real_unittest_stderr_interleaving_is_parsed_without_reexecuting_product(self):
        import contextlib
        import io
        method = "test_admitted_main_capture_mutation_cannot_publish_an_include"
        for module in ("test_nsis_owned_files", "tooling.tests.test_nsis_owned_files"):
            stream = io.StringIO()
            def action(case):
                print("nsis_owned_files: capture changed during capture", file=sys.stderr)
            case_type = type("DistributionOwnedFilesTests", (unittest.TestCase,),
                             {"__module__": module, method: action})
            with contextlib.redirect_stderr(stream):
                result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
                    unittest.TestSuite([case_type(method)]))
            self.assertTrue(result.wasSuccessful())
            self.assertEqual(result.testsRun, 1)
            self.assertEqual(gate.executed_test_ids(stream.getvalue()),
                             [module + ".DistributionOwnedFilesTests." + method])

if __name__ == "__main__":
    unittest.main(verbosity=2)
