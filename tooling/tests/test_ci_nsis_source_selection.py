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


class DistributionSelectionTests(unittest.TestCase):
    def test_distribution_nominal_roster_and_only_fifo_platform_exclusion(self):
        expected = {
            "test_real_cli_is_deterministic_read_only_and_independent_of_cwd",
            "test_invalid_source_refuses_before_opening_payload",
            "test_cli_argument_errors_are_static_and_help_uses_stderr",
            "test_zero_directory_and_bounded_size_refuse_before_open",
            "test_real_leaf_and_parent_symlinks_are_rejected",
            "test_fifo_substitution_after_lstat_refuses_without_blocking",
            "test_cargo_mutation_during_read_produces_no_stdout_record",
            "test_real_payload_growth_is_detected_or_prevented_by_windows",
            "test_real_payload_truncation_is_detected_or_prevented_by_windows",
            "test_same_size_overwrite_with_restored_mtime_is_detected_or_prevented",
            "test_real_same_bytes_path_replacement_is_detected_or_prevented",
            "test_read_and_final_change_time_errors_close_fd_and_emit_no_json",
            "test_interrupt_and_programming_error_remain_visible_and_close_fd",
            "test_emitted_bytes_interoperate_with_explicit_synthetic_permission",
            "test_observation_does_not_override_negative_or_missing_admission",
        }
        self.assertEqual(set(gate.DISTRIBUTION_IDS), {"DistributionBuildRecordTests." + n for n in expected})
        for mode in ("discovery", "dotted"):
            for platform, count in (("linux", 15), ("win32", 14)):
                value = gate.selection_contract("test_distribution_build_record", mode, platform)
                self.assertEqual((value["required_total"], value["required_selected"]), (15, count))
                self.assertEqual(len(value["expected_ids"]), 15)
                self.assertTrue(any(n.endswith(".test_real_leaf_and_parent_symlinks_are_rejected")
                                    for n in value["selected_ids"]))
                omitted = value["not_applicable"]
                self.assertEqual(len(omitted), 0 if platform == "linux" else 1)
                if omitted:
                    self.assertTrue(omitted[0]["id"].endswith(".test_fifo_substitution_after_lstat_refuses_without_blocking"))
                    self.assertEqual(omitted[0]["status"], "NOT_APPLICABLE")
        self.assertEqual(set(gate.ROSTERS), {"test_nsis_owned_files", "test_source_lock_admission"})
        self.assertEqual(len(gate.scope_identities("nsis_source")), 4)
        self.assertEqual(len(gate.scope_identities("distribution_source")), 2)

    def test_distribution_dependency_closure_is_complete_and_scope_specific(self):
        expected = {
            ".github/workflows/package-candidate.yml", "tooling/ci_test_runner.py",
            "tooling/ci_suites/__init__.py", "tooling/ci_suites/nsis_source_selection.py",
            "tooling/ci_suites/distribution_source_suite.py", "tooling/tests/test_ci_nsis_source_selection.py",
            "tooling/distribution_build_record.py", "tooling/tests/test_distribution_build_record.py",
            "tooling/nsis_owned_files.py", "tooling/source_lock_admission.py",
            "tooling/tests/test_source_lock_admission.py", "tooling/public_surface_policy.py",
            "tooling/tests/test_public_surface_policy.py", "Cargo.lock", "SOURCE_LOCK.json",
            "apps/studio/package-lock.json",
        }
        self.assertEqual(set(gate.scope_source_paths("distribution_source")), expected)
        self.assertEqual(len(gate.scope_source_paths("distribution_source")), 16)
        self.assertEqual(gate.scope_source_paths("nsis_source"), gate.SOURCE_PATHS)
        self.assertNotIn("test_distribution_build_record", gate.scope_rosters("nsis_source"))
        for value in ("all", "", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gate.scope_source_paths(value)

    def test_distribution_adapter_loads_only_original_producer_with_full_runtime_roster(self):
        import contextlib
        import io
        for mode in ("discovery", "dotted"):
            module = ("" if mode == "discovery" else "tooling.tests.") + "test_distribution_build_record"
            methods = {name.split(".", 1)[1]: lambda case: None for name in gate.DISTRIBUTION_IDS}
            case_type = type("DistributionBuildRecordTests", (unittest.TestCase,),
                             {"__module__": module, **methods})
            loader = mock.Mock(errors=[])
            suite = unittest.TestSuite(case_type(name) for name in methods)
            loader.discover.return_value = suite
            loader.loadTestsFromName.return_value = suite
            before = {"executed_source_sha": "a" * 40}
            adapter = ("" if mode == "discovery" else "tooling.ci_suites.") + "distribution_source_suite"
            with mock.patch.object(gate, "ROOT", Path.cwd().resolve()), \
                 mock.patch.object(gate.sys, "platform", "win32"), \
                 mock.patch.object(gate, "source_snapshot", return_value=before) as snapshot, \
                 mock.patch.object(gate, "selected_files", return_value=[Path("producer.py")]) as files, \
                 mock.patch.object(gate, "read_original", return_value=b""), \
                 mock.patch.object(gate, "validate_definition") as definition, \
                 mock.patch.object(gate.unittest, "TestLoader", return_value=loader), \
                 contextlib.redirect_stdout(io.StringIO()) as stream:
                selected = gate.select_original_suite("test_distribution_build_record", adapter)
            self.assertEqual(selected.countTestCases(), 14)
            self.assertEqual(files.call_count, 1)
            self.assertEqual(files.call_args.args[2], ["test_distribution_build_record.py"])
            definition.assert_called_once_with(b"", gate.DISTRIBUTION_IDS)
            self.assertEqual(snapshot.call_count, 2)
            self.assertTrue(all(call.args[-1] == "distribution_source" for call in snapshot.call_args_list))
            record = gate.parse_selection(stream.getvalue().encode())
            self.assertEqual(len(record["discovered_ids"]), 15)
            self.assertEqual(len(record["selected_ids"]), 14)
            if mode == "dotted":
                loader.loadTestsFromName.assert_called_once_with("tooling.tests.test_distribution_build_record")
                loader.discover.assert_not_called()
            else:
                loader.discover.assert_called_once()
                loader.loadTestsFromName.assert_not_called()

    def test_source_snapshot_retains_physical_crlf_bytes_and_rejects_missing_tracked_dependency(self):
        source = {"executed_source_sha": "a" * 40, "tree_sha": "b" * 40}
        closure = gate.scope_source_paths("distribution_source")
        for raw in (b"source\n", b"source\r\n"):
            with mock.patch.object(gate, "source_state", return_value=source), \
                 mock.patch.object(gate, "git", return_value="\n".join(closure)), \
                 mock.patch.object(gate, "read_original", return_value=raw):
                observed = gate.source_snapshot(Path.cwd(), "a" * 40, "distribution_source")
            self.assertEqual(observed["files"][closure[0]], {"bytes": len(raw), "sha256": digest(raw)})
        with mock.patch.object(gate, "source_state", return_value=source), \
             mock.patch.object(gate, "git", return_value="\n".join(closure[:-1])), \
             mock.patch.object(gate, "read_original") as read:
            with self.assertRaises(ValueError):
                gate.source_snapshot(Path.cwd(), "a" * 40, "distribution_source")
        read.assert_not_called()


class DistributionReceiptTests(unittest.TestCase):
    log = OriginalReceiptTests.log
    read_log = OriginalReceiptTests.read_log

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-distribution-gate-", dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT"))
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        self.report = self.base / "receipt.json"
        files = {}
        for relative in gate.DISTRIBUTION_SOURCE_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = ("synthetic distribution source " + relative + "\n").encode()
            path.write_bytes(raw)
            files[relative] = {"bytes": len(raw), "sha256": digest(raw)}
        self.before = {"executed_source_sha": "a" * 40, "tree_sha": "b" * 40, "files": files}
        self.after = copy.deepcopy(self.before)
        self.make("linux")

    def make(self, platform):
        self.platform = platform
        self.github = dict(zip(gate.GITHUB_KEYS, ["a" * 40, "fixture/workflow@ref", "2001", "1", "distribution-fixture"]))
        adapter = "distribution_source_suite"
        adapter_path = "tooling/ci_suites/" + adapter + ".py"
        self.receipt = {
            "schema": "motionwright.unittest-execution/1", "executed_source_sha": "a" * 40,
            "tree_sha": "b" * 40, "require_complete": True, "status": "PASS", "exit_code": 0,
            "github": copy.deepcopy(self.github), "platform": "Linux-fixture" if platform == "linux" else "Windows-fixture",
            "runner_sha256": self.before["files"]["tooling/ci_test_runner.py"]["sha256"],
            "test_definition_sha256": {adapter_path: self.before["files"][adapter_path]["sha256"]},
            "source_locks": {p: self.before["files"][p]["sha256"] for p in (
                "Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")},
            "steps": [],
        }
        for mode in ("discovery", "dotted"):
            contract = gate.selection_contract("test_distribution_build_record", mode, platform)
            count = contract["required_selected"]
            label = "discovery:" + adapter + ".py" if mode == "discovery" else "dotted:tooling.ci_suites." + adapter
            tail = ["discover", "-s", "tooling/ci_suites", "-p", adapter + ".py", "-v"] if mode == "discovery" else ["tooling.ci_suites." + adapter, "-v"]
            step = {"entry_point": label, "command": [sys.executable, "-B", "-E", "-s", "-m", "unittest", *tail],
                    "status": "PASS", "exit_code": 0, "counts": {
                        "terminal_status": "OK", "tests_run": count, "passed": count,
                        **{k: 0 for k in gate.ZERO_COUNTS}}}
            self.receipt["steps"].append(step)
            index = len(self.receipt["steps"]) - 1
            record = {**contract, "discovered_ids": contract["expected_ids"], "source": copy.deepcopy(self.before)}
            self.log(index, "stdout", (gate.PREFIX + json.dumps(record) + "\n").encode())
            stderr = "".join(f"{name.rsplit('.', 1)[1]} ({name}) ... ok\n" for name in contract["selected_ids"])
            self.log(index, "stderr", (stderr + f"\nRan {count} tests in 0.01s\n\nOK\n").encode())

    def admit(self, scope="distribution_source"):
        self.report.write_text(json.dumps(self.receipt), encoding="utf-8")
        return gate.validate_receipt(self.root, self.report, self.before, self.after,
                                     self.platform, self.github, scope)

    def refused(self):
        with self.assertRaises(ValueError):
            self.admit()

    def test_distribution_receipts_bind_both_entrypoints_without_authorizing_distribution(self):
        for platform, invocations, unique in (("linux", 30, 15), ("win32", 28, 14)):
            self.make(platform)
            result = self.admit()
            self.assertEqual((result["invocations"], result["unique_applicable_controls"]), (invocations, unique))
            self.assertEqual(result["schema"], "motionwright.distribution-source-admission/1")
            self.assertEqual(result["distribution_authorization"], "NOT_GRANTED")
            self.assertEqual(result["payload_build_causality"], "NOT_PROVEN")
            self.assertEqual(result["installer_execution"], "NOT_RUN")
            self.assertEqual(result["historical_nsis_source_suites"], "NOT_SELECTED")
            self.assertEqual(result["historical_nsis_success_credit"], 0)
            self.assertIs(result["release_authorized"], False)
            originals = {p.name: p.read_bytes() for p in self.base.iterdir() if p.is_file()}
            self.admit()
            self.assertEqual(originals, {p.name: p.read_bytes() for p in self.base.iterdir() if p.is_file()})

    def test_windows_symlink_skip_cannot_be_counted_as_pass_or_not_applicable(self):
        self.make("win32")
        raw = self.read_log(0, "stderr")
        contract = gate.selection_contract("test_distribution_build_record", "discovery", "win32")
        identity = next(n for n in contract["selected_ids"] if n.endswith(".test_real_leaf_and_parent_symlinks_are_rejected"))
        line = f"{identity.rsplit('.', 1)[1]} ({identity}) ... ok\n".encode()
        self.log(0, "stderr", raw.replace(line, line.replace(b"ok\n", b"skipped 'unavailable'\n")))
        self.refused()
        self.make("win32")
        record = gate.parse_selection(self.read_log(0, "stdout"))
        record["selected_ids"].remove(identity)
        record["not_applicable"].append({"id": identity, "status": "NOT_APPLICABLE", "reason": "unavailable"})
        record["required_selected"] = 13
        self.log(0, "stdout", (gate.PREFIX + json.dumps(record) + "\n").encode())
        self.refused()

    def test_linux_fifo_omission_or_foreign_same_count_identity_is_rejected(self):
        contract = gate.selection_contract("test_distribution_build_record", "discovery", "linux")
        identity = next(n for n in contract["selected_ids"] if n.endswith(".test_fifo_substitution_after_lstat_refuses_without_blocking"))
        for replacement in (identity + "_foreign", contract["selected_ids"][0]):
            self.make("linux")
            raw = self.read_log(0, "stderr")
            self.log(0, "stderr", raw.replace(identity.encode(), replacement.encode()))
            self.refused()
        self.make("linux")
        raw = self.read_log(0, "stderr")
        line = f"{identity.rsplit('.', 1)[1]} ({identity}) ... ok\n".encode()
        self.log(0, "stderr", raw.replace(line, b""))
        self.refused()

    def test_distribution_bool_skipped_unknown_and_extra_legacy_entrypoint_refused(self):
        saved = copy.deepcopy(self.receipt)
        for target, field, value in (("receipt", "exit_code", False), ("step", "exit_code", False),
                                     ("counts", "tests_run", True), ("counts", "skipped", False),
                                     ("counts", "skipped", 1), ("step", "status", "UNKNOWN"),
                                     ("receipt", "status", "PASS_WITH_OMISSIONS")):
            self.receipt = copy.deepcopy(saved)
            section = self.receipt if target == "receipt" else self.receipt["steps"][0] if target == "step" else self.receipt["steps"][0]["counts"]
            section[field] = value
            self.refused()
        self.receipt = copy.deepcopy(saved)
        self.receipt["steps"].append({"entry_point": "discovery:nsis_source_suite.py"})
        self.refused()
        self.receipt = saved
        self.receipt["steps"].pop()
        self.refused()

    def test_distribution_source_run_attempt_scope_and_physical_identity_substitution_refused(self):
        saved = copy.deepcopy(self.receipt)
        for key, value in (("executed_source_sha", "f" * 40), ("tree_sha", "f" * 40)):
            self.receipt = copy.deepcopy(saved)
            self.receipt[key] = value
            self.refused()
        self.receipt = copy.deepcopy(saved)
        self.receipt["github"]["GITHUB_RUN_ATTEMPT"] = "2"
        self.refused()
        self.receipt = copy.deepcopy(saved)
        self.receipt["github"]["GITHUB_RUN_ID"] = "2002"
        self.refused()
        self.receipt = saved
        with self.assertRaises(ValueError):
            self.admit("nsis_source")
        original = copy.deepcopy(self.before)
        for relative in ("tooling/distribution_build_record.py", "tooling/tests/test_source_lock_admission.py"):
            self.before = copy.deepcopy(original)
            self.after = copy.deepcopy(original)
            self.after["files"][relative]["sha256"] = "f" * 64
            self.refused()
        self.before = copy.deepcopy(original)
        self.before["files"]["Cargo.lock"]["bytes"] = False
        self.after = copy.deepcopy(self.before)
        self.refused()

    def test_distribution_interleaved_original_diagnostic_retained_and_hash_corruption_rejected(self):
        for platform in ("linux", "win32"):
            self.make(platform)
            for index in (0, 1):
                raw = self.read_log(index, "stderr")
                self.log(index, "stderr", raw.replace(b" ... ok\n", b" ... expected producer refusal\nok\n", 1))
            result = self.admit()
            self.assertEqual(result["unique_applicable_controls"], 15 if platform == "linux" else 14)
            self.receipt["steps"][0]["stderr"]["sha256"] = "0" * 64
            self.refused()


class DistributionGateExecutionTests(unittest.TestCase):
    def test_scope_runner_uses_only_selected_adapter_and_unknown_is_terminal(self):
        import contextlib
        import io
        temp = tempfile.TemporaryDirectory(prefix="mw-distribution-exec-", dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT"))
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        root = base / "source"
        root.mkdir()
        before = {"executed_source_sha": "a" * 40, "tree_sha": "b" * 40, "files": {}}
        for scope, patterns in (("distribution_source", ["distribution_source_suite.py"]),
                                ("nsis_source", ["nsis_source_suite.py", "source_lock_suite.py"])):
            with mock.patch.dict(os.environ, {}, clear=False), \
                 mock.patch.object(gate, "source_snapshot", return_value=before) as snapshot, \
                 mock.patch.object(gate, "run_suite", return_value=(124, {"status": "UNKNOWN"})) as runner, \
                 mock.patch.object(gate, "validate_receipt") as admit, \
                 contextlib.redirect_stdout(io.StringIO()):
                output = base / scope
                code = gate.run_source_gate(root, output, "a" * 40, scope)
            self.assertEqual(code, 124)
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(runner.call_args.args[1:4], ("tooling/ci_suites", patterns, "both"))
            self.assertIs(runner.call_args.args[6], True)
            self.assertEqual(snapshot.call_count, 1)
            admit.assert_not_called()
            self.assertTrue((output / "source-before.json").is_file())
            self.assertFalse((output / "source-after.json").exists())
            self.assertFalse((output / "readback.json").exists())

    def test_distribution_bool_runner_exit_and_changed_source_never_create_pass_readback(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-distribution-exit-", dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT"))
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        root = base / "source"
        root.mkdir()
        before = {"executed_source_sha": "a" * 40, "tree_sha": "b" * 40, "files": {}}
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(gate, "source_snapshot", return_value=before) as snapshot, \
             mock.patch.object(gate, "run_suite", return_value=(False, {"status": "PASS"})), \
             mock.patch.object(gate, "validate_receipt") as admit:
            with self.assertRaises(ValueError):
                gate.run_source_gate(root, base / "boolean", "a" * 40, "distribution_source")
        self.assertEqual(snapshot.call_count, 1)
        admit.assert_not_called()
        self.assertFalse((base / "boolean/readback.json").exists())
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(gate, "source_snapshot", side_effect=[before, ValueError("source changed")]), \
             mock.patch.object(gate, "run_suite", return_value=(0, {"status": "PASS"})), \
             mock.patch.object(gate, "validate_receipt") as admit:
            with self.assertRaises(ValueError):
                gate.run_source_gate(root, base / "changed", "a" * 40, "distribution_source")
        admit.assert_not_called()
        self.assertFalse((base / "changed/readback.json").exists())


class DistributionWorkflowRoutingTests(unittest.TestCase):
    def assert_source_and_installed_routes(self, text):
        import re
        jobs_text = text.split("\njobs:\n", 1)[1]
        matches = list(re.finditer(r"(?m)^  ([a-z][a-z-]*):\n", jobs_text))
        jobs = {match.group(1): jobs_text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(jobs_text)]
                for index, match in enumerate(matches)}
        self.assertEqual(set(jobs), {"receipt-policy", "nsis-source-windows", "linux", "windows-contracts",
                                     "windows", "windows-update", "macos", "attest-candidates",
                                     "linux-first-use-source", "linux-first-use-installed"})
        def enabled(body, event, scope, ref="refs/heads/main"):
            found = re.search(r"(?m)^    if: (.+)$", body)
            if found is None:
                return True
            expr = found[1]
            if expr == ">-":
                # The actual Windows job has a folded YAML condition. Bind its
                # successful prerequisites here; the separate tests enforce them.
                continuation = body[found.end():].splitlines()
                folded = []
                for line in continuation:
                    if line and not line.startswith("      "):
                        break
                    if line.strip():
                        folded.append(line.strip())
                expr = " ".join(folded)
                self.assertTrue(expr.startswith("${{ ") and expr.endswith(" }}"))
                expr = expr[4:-3]
                self.assertIn("!cancelled()", expr)
                expr = expr.replace("!cancelled()", "True")
                for dependency in ("receipt-policy", "windows-contracts", "nsis-source-windows"):
                    expected = "needs['" + dependency + "'].result"
                    self.assertIn(expected, expr)
                    expr = expr.replace(expected, "'success'")
            expr = expr.replace("github.event_name", repr(event)).replace("inputs.verification_scope", repr(scope))
            expr = expr.replace("github.ref", repr(ref)).replace("&&", " and ").replace("||", " or ")
            try:
                return bool(eval(expr, {"__builtins__": {}}, {}))
            except (SyntaxError, NameError, TypeError) as error:
                raise AssertionError("Workflow conditional has unreviewed syntax") from error
        two = {"receipt-policy", "nsis-source-windows"}
        for scope, extra in (("distribution_source", set()), ("nsis_source", set()),
                             ("nsis_compile", {"windows-update"}), ("nsis_lifecycle", {"windows-update"}),
                             ("windows_contracts", {"windows-contracts"}),
                             ("windows_installed", {"windows-contracts", "windows"}),
                             ("all", {"linux", "windows-contracts", "windows", "macos", "attest-candidates"})):
            self.assertEqual({name for name, body in jobs.items() if enabled(body, "workflow_dispatch", scope)}, two | extra, scope)
        first_use = {"receipt-policy", "linux-first-use-source"}
        for scope, extra in (("linux_first_use_source", set()),
                             ("linux_first_use", {"linux-first-use-installed"})):
            self.assertEqual({name for name, body in jobs.items() if enabled(body, "workflow_dispatch", scope)}, first_use | extra, scope)
        manual_first_use = {"linux-first-use-source", "linux-first-use-installed"}
        self.assertEqual({name for name, body in jobs.items() if enabled(body, "push", "")},
                         set(jobs) - {"windows-update"} - manual_first_use)
        self.assertEqual({name for name, body in jobs.items() if enabled(body, "pull_request", "")},
                         set(jobs) - {"windows-update", "attest-candidates"} - manual_first_use)
        self.assertIn("          - distribution_source\n", text)
        for scope in ("linux_first_use_source", "linux_first_use"):
            self.assertIn("          - " + scope + "\n", text)
        for job in two:
            body = jobs[job]
            self.assertIn("SOURCE_CONTRACT_SCOPE:", body)
            self.assertIn("&& 'distribution_source' || 'nsis_source'", body)
            self.assertIn("-m unittest tooling.tests.test_ci_nsis_source_selection -v", body)
            self.assertIn("--scope ", body)
            self.assertIn("if: github.event_name != 'workflow_dispatch' || inputs.verification_scope != 'distribution_source'", body)
            self.assertIn("if: always() && (github.event_name != 'workflow_dispatch' || inputs.verification_scope != 'distribution_source')", body)
            self.assertIn("&& 'error' || 'warn'", body)
        for policy in ("source_policy", "acceptance_policy", "creative_status_policy", "public_surface_policy"):
            self.assertIn("python3 tooling/" + policy + ".py", jobs["receipt-policy"])
        self.assertIn("tooling/tests/test_package_receipt.py", jobs["receipt-policy"])
        self.assertIn("if ($LASTEXITCODE -ne 0)", jobs["nsis-source-windows"])
        source_job = jobs["linux-first-use-source"]
        installed_job = jobs["linux-first-use-installed"]
        self.assertIn("    needs: receipt-policy\n", source_job)
        self.assertIn("tooling/ci_linux_first_use.py source", source_job)
        for forbidden in ("tauri build", "npm ci", "xvfb-run", "ci_linux_first_use.py installed"):
            self.assertNotIn(forbidden, source_job)
        self.assertIn("    needs: [receipt-policy, linux-first-use-source]\n", installed_job)
        self.assertEqual(installed_job.count("tooling/ci_linux_first_use.py installed"), 1)
        self.assertLess(installed_job.index("tooling/ci_linux_first_use.py reserve"),
                        installed_job.index("tauri build --bundles deb,appimage"))

    def test_only_existing_two_jobs_select_distribution_and_all_historical_scopes_remain(self):
        text = (gate.ROOT / ".github/workflows/package-candidate.yml").read_text(encoding="utf-8")
        self.assert_source_and_installed_routes(text)

    def test_nsis_update_render_preserves_all_product_license_resources(self):
        import json
        root = gate.ROOT
        resources = json.loads((root / "apps/desktop/src-tauri/tauri.conf.json").read_text(
            encoding="utf-8"))["bundle"]["resources"]
        expected = {
            "bundle-licenses/LICENSE": "licenses/motionwright/LICENSE",
            "bundle-licenses/THIRD_PARTY_NOTICES.md": "licenses/THIRD_PARTY_NOTICES.md",
            "bundle-licenses/glib-0.18.5/LICENSE": "licenses/glib-0.18.5/LICENSE",
            "bundle-licenses/glib-0.18.5/COPYRIGHT": "licenses/glib-0.18.5/COPYRIGHT",
        }
        self.assertEqual(resources, expected)
        self.assertTrue(all((root / "apps/desktop/src-tauri" / key).is_file()
                            for key in resources))
        workflow = (root / ".github/workflows/package-candidate.yml").read_text(
            encoding="utf-8")
        self.assertEqual(workflow.count("expected_configured_resources = {"), 1)
        self.assertEqual(workflow.count(
            'config["bundle"].get("resources") == expected_configured_resources'), 1)
        self.assertEqual(workflow.count(
            'resource_inputs = dict(expected_configured_resources)'), 1)
        self.assertEqual(workflow.count(
            'expected_fixture[installed] = {"kind": "resource", **identity(source_file)}'), 1)
        self.assertIn("the_render_fixture_must_preserve_exact_configured_application_payloads",
                      workflow)

    def test_nsis_lifecycle_passes_private_source_bound_main_admission_to_bundle(self):
        source = (gate.ROOT / ".github/workflows/package-candidate.yml").read_text(encoding="utf-8")
        stage = source.split("- name: Admit exact retained predecessor bytes", 1)[1]
        stage = stage.split("- name: Compile the actual NSIS template", 1)[0]
        for literal in (
            "from distribution_build_record import distribution_build_record",
            "from source_lock_admission import verify_distribution_admission",
            'distribution_build_record(ROOT, arch="x64", payload=main)',
            'distribution_build["payload"] == main_before',
            "write_new_manifest(ROOT, ROOT / admission_relative, disposition)",
            "record_path=admission_relative,",
            "record_sha256=distribution_pin) == main_before",
            'environment.write("MW_NSIS_DISTRIBUTION_ADMISSION="',
            'environment.write("MW_NSIS_DISTRIBUTION_ADMISSION_SHA256="',
            "GITHUB_RUN_ATTEMPT", "Independent G2/G3 acceptance remains NOT_RUN.",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, stage)
        self.assertLess(stage.index("write_new_manifest(ROOT, ROOT / admission_relative, disposition)"),
                        stage.index('environment.write("MW_NSIS_DISTRIBUTION_ADMISSION="'))

    def test_nsis_historical_predecessor_requires_exact_old_source_and_binary_only(self):
        workflow = (gate.ROOT / ".github/workflows/package-candidate.yml").read_text(encoding="utf-8")
        stage = workflow.split("- name: Observe one distinct-version fixture install", 1)[1]
        stage = stage.split("- name: Retain original lifecycle observations", 1)[0]
        self.assertNotIn("candidate_smoke.build_receipt(", stage)
        for token in (
            "candidate_smoke.verify_installer(", "candidate_smoke.binary_identity(",
            "candidate_smoke.verify_cargo_build_binding(",
            "historical_predecessor_notice_exception_not_applicable",
            "679cc48baf7c6f9bb4a0b803508c9b2a9891d944",
            "4c1f8f492a505340ec3ca8e521254ff753a01875c92052a48c0dc42ad1314de8",
            "HISTORICAL_PREDECESSOR_BINARY_ONLY",
            "NOT_VERIFIED_HISTORICAL_UNDECLARED",
            "historical_predecessor_binary_or_receipt_changed",
            'smoke_ref = publish("previous-installed-smoke.json", smoke)',
        ):
            with self.subTest(token=token):
                self.assertIn(token, stage)

    def test_linux_source_scope_cannot_activate_installed_or_legacy_package_effects(self):
        text = (gate.ROOT / ".github/workflows/package-candidate.yml").read_text(encoding="utf-8")
        changes = (
            ("    if: github.event_name == 'workflow_dispatch' && inputs.verification_scope == 'linux_first_use'\n",
             "    if: github.event_name == 'workflow_dispatch' && inputs.verification_scope == 'linux_first_use_source'\n"),
            ("    if: github.event_name == 'workflow_dispatch' && (inputs.verification_scope == 'linux_first_use_source' || inputs.verification_scope == 'linux_first_use')\n",
             "    if: github.event_name != 'workflow_dispatch' || (inputs.verification_scope == 'linux_first_use_source' || inputs.verification_scope == 'linux_first_use')\n"),
            ("    needs: [receipt-policy, linux-first-use-source]\n", "    needs: receipt-policy\n"),
            (" && inputs.verification_scope != 'linux_first_use_source'", ""),
        )
        for original, replacement in changes:
            with self.subTest(original=original):
                self.assertIn(original, text)
                with self.assertRaises(AssertionError):
                    self.assert_source_and_installed_routes(text.replace(original, replacement, 1))


class PackageSchemaFiveReceiptReadbackTests(unittest.TestCase):
    """Execute only the workflow's receipt readers against synthetic originals."""

    def setUp(self):
        import ast
        import re
        import textwrap
        temp = tempfile.TemporaryDirectory(prefix="mw-schema5-readback-", dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT"))
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.counter = 0
        self.workflow = (gate.ROOT / ".github/workflows/package-candidate.yml").read_text(encoding="utf-8")
        sections = self.workflow.split('      - name: Require sixty-seven lifecycle contracts through both existing runner entrypoints\n')[1:]
        bodies = []
        for section in sections:
            section = section.split('      - name: Retain exact lifecycle contract receipts\n', 1)[0]
            found = re.search(
                r"(          receipt = json.loads\(report.read_bytes\(\)\)\n.*?)(?=\n          (?:PY|'@ \| python -))",
                section, re.S)
            self.assertIsNotNone(found)
            bodies.append(found[1])
        self.assertEqual(len(bodies), 2)
        self.lifecycle = [compile(textwrap.dedent(body), "<workflow-lifecycle-readback>", "exec") for body in bodies]
        windows = self.workflow.split('          if sys.platform != "win32":\n', 1)[1].split("\n          '@", 1)[0]
        self.windows = compile(textwrap.dedent('          if sys.platform != "win32":\n' + windows),
                               "<workflow-windows-readback>", "exec")
        self.definitions = {}
        for name, expected in (("test_windows_installer_lifecycle.py", 67), ("test_windows_cold_start.py", 52)):
            raw = (gate.ROOT / "tooling/tests" / name).read_bytes()
            tree = ast.parse(raw)
            methods = [cls.name + "." + method.name for cls in tree.body if isinstance(cls, ast.ClassDef)
                       for method in cls.body if isinstance(method, ast.FunctionDef) and method.name.startswith("test_")]
            self.assertEqual(len(methods), expected)
            self.assertEqual(len(set(methods)), expected)
            self.definitions[name] = (raw, sorted(methods))

    def fixture(self, kind):
        self.counter += 1
        root = self.base / str(self.counter)
        root.mkdir()
        output = root / "output"
        output.mkdir()
        name = "test_windows_installer_lifecycle.py" if kind == "lifecycle" else "test_windows_cold_start.py"
        raw, methods = self.definitions[name]
        relative = "tooling/tests/" + name
        paths = (relative, "tooling/windows_cold_start.py", "tooling/windows_first_project_ui.ps1",
                 "tooling/storage_schema_contract.py", "tooling/tests/test_storage_schema_contract.py",
                 "tooling/source_checkout_observation.py", "tooling/ci_test_runner.py",
                 ".github/workflows/package-candidate.yml")
        for path in paths:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw if path == relative else ("SYNTHETIC SOURCE " + path).encode())
        github = {"GITHUB_SHA": "a" * 40, "GITHUB_WORKFLOW_REF": "synthetic/fixture@refs/heads/control",
                  "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "synthetic-reader-control"}
        receipt = {"schema": "motionwright.unittest-execution/1", "status": "PASS", "exit_code": 0,
                   "require_complete": True, "executed_source_sha": github["GITHUB_SHA"], "tree_sha": "b" * 40,
                   "github": github, "test_definition_sha256": {relative: digest(raw)},
                   "runner_sha256": digest((root / "tooling/ci_test_runner.py").read_bytes()), "steps": []}
        report = output / "receipt.json"
        for index, mode in enumerate(("discovery", "dotted")):
            module = ("" if index == 0 else "tooling.tests.") + name[:-3]
            stderr = "".join(f"{item.rsplit('.', 1)[1]} ({module}.{item}) ... ok\n" for item in methods).encode()
            stderr += f"\nRan {len(methods)} tests in 0.001s\n\nOK\n".encode()
            counts = {"terminal_status": "OK", "tests_run": len(methods), "passed": len(methods),
                      "failures": 0, "errors": 0, "skipped": 0, "expected_failures": 0, "unexpected_successes": 0}
            step = {"entry_point": "discovery:" + name if index == 0 else "dotted:" + module,
                    "status": "PASS", "exit_code": 0, "counts": counts}
            for stream, data in (("stdout", b""), ("stderr", stderr)):
                target = output / f"receipt-{index:02d}-{stream}.log"
                target.write_bytes(data)
                step[stream] = {"file": target.name, "sha256": digest(data)}
            receipt["steps"].append(step)
        return root, output, report, receipt, github

    def run_reader(self, fixture, kind, code=None):
        import ast
        import contextlib
        from datetime import datetime, timezone
        import io
        import re
        from types import SimpleNamespace
        root, output, report, receipt, github = fixture
        report.write_text(json.dumps(receipt), encoding="utf-8")
        result = []
        def require(value, reason):
            if not value:
                raise ValueError(reason)
        def record(root_arg, path, document):
            self.assertEqual(root_arg, root)
            self.assertEqual(path, output / "readback.json")
            result.append(document)
        before = {"tree": "b" * 40, "checkout": "a" * 40}
        scope = {"ast": ast, "hashlib": hashlib, "json": json, "os": os, "Path": Path, "re": re,
                 "sys": SimpleNamespace(platform="win32" if kind == "windows" else "linux"),
                 "ROOT": root, "SOURCE": "a" * 40, "before": before, "output": output, "report": report,
                 "root": root, "head": "a" * 40, "tree": "b" * 40,
                 "require": require, "source_observation": lambda *_: before,
                 "write_new_manifest": record, "datetime": datetime, "timezone": timezone}
        with mock.patch.dict(os.environ, github), contextlib.redirect_stdout(io.StringIO()):
            exec(code if code is not None else self.windows, scope)
        if kind == "windows":
            result.append(json.loads((output / "contract-readback.json").read_bytes()))
        self.assertEqual(len(result), 1)
        return result[0]

    def mutate_log(self, fixture, mutation):
        _, output, _, receipt, _ = fixture
        step = receipt["steps"][0]
        path = output / step["stderr"]["file"]
        rows = path.read_text().splitlines(keepends=True)
        mutation(rows)
        raw = "".join(rows).encode()
        path.write_bytes(raw)
        step["stderr"]["sha256"] = digest(raw)

    def test_lifecycle_readers_require_all_sixty_seven_named_controls_in_both_modes(self):
        for code in self.lifecycle:
            result = self.run_reader(self.fixture("lifecycle"), "lifecycle", code)
            self.assertEqual((result["unique_controls"], result["entrypoints"], result["skipped"]), (67, 2, 0))
            self.assertEqual(result["installer_execution"], "NOT_RUN")

    def test_lifecycle_readers_reject_legacy_counts_boolean_exits_omissions_and_foreign_source(self):
        mutations = (
            lambda r: r.update(schema="foreign"),
            lambda r: r.update(exit_code=False),
            lambda r: r.update(executed_source_sha="c" * 40),
            lambda r: r["steps"][0].update(exit_code=False),
            lambda r: r["steps"][0]["counts"].update(tests_run=60, passed=60),
            lambda r: r["steps"][0]["counts"].update(passed=62, skipped=1),
        )
        for code in self.lifecycle:
            for mutation in mutations:
                fixture = self.fixture("lifecycle")
                mutation(fixture[3])
                with self.assertRaises(ValueError):
                    self.run_reader(fixture, "lifecycle", code)

    def test_lifecycle_readers_reject_missing_duplicate_and_foreign_ids_with_rehashed_streams(self):
        for code in self.lifecycle:
            for mutation in (lambda rows: rows.pop(0), lambda rows: rows.__setitem__(0, rows[1]),
                             lambda rows: rows.__setitem__(0, rows[0].replace("test_", "test_foreign_"))):
                fixture = self.fixture("lifecycle")
                self.mutate_log(fixture, mutation)
                with self.assertRaises(ValueError):
                    self.run_reader(fixture, "lifecycle", code)

    def test_windows_reader_requires_all_fifty_two_named_controls_in_both_modes(self):
        result = self.run_reader(self.fixture("windows"), "windows")
        self.assertEqual((result["unique_source_controls"], result["entrypoints"], result["skipped"]), (52, 2, 0))
        self.assertIs(result["installed_application_execution"], False)

    def test_windows_reader_rejects_legacy_counts_omissions_boolean_exit_and_foreign_source(self):
        for mutation in (lambda r: r.update(schema="foreign"),
                         lambda r: r.update(exit_code=False),
                         lambda r: r.update(executed_source_sha="c" * 40),
                         lambda r: r["steps"][0]["counts"].update(tests_run=43, passed=43),
                         lambda r: r["steps"][0]["counts"].update(passed=47, skipped=1),
                         lambda r: r["steps"][0].update(exit_code=False)):
            fixture = self.fixture("windows")
            mutation(fixture[3])
            with self.assertRaises(SystemExit):
                self.run_reader(fixture, "windows")

    def test_windows_reader_rejects_missing_duplicate_and_foreign_ids_with_rehashed_streams(self):
        for mutation in (lambda rows: rows.pop(0), lambda rows: rows.__setitem__(0, rows[1]),
                         lambda rows: rows.__setitem__(0, rows[0].replace("test_", "test_foreign_"))):
            fixture = self.fixture("windows")
            self.mutate_log(fixture, mutation)
            with self.assertRaises(SystemExit):
                self.run_reader(fixture, "windows")


if __name__ == "__main__":
    unittest.main(verbosity=2)
