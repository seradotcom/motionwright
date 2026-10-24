"""Gate controls only; these fixtures do not claim installed Linux acceptance."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_linux_first_use as gate


SHA = "a" * 40
TREE = "b" * 40
SDK = "c" * 40
GITHUB = {"GITHUB_SHA": SHA, "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/package-candidate.yml@refs/heads/test",
          "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}


def state():
    identity = {"bytes": 1, "sha256": "d" * 64}
    return {"source": {"sha": SHA, "tree": TREE, "files": {
        p: {"checkout": dict(identity)} for p in gate.PATHS}},
        "storage_cohort": {"storage_schema": 4}}


def verbose(module, cases=None, suffix="ok", repeated_method=True):
    rows = []
    for case in sorted(gate.EXPECTED_CASES if cases is None else cases):
        cls, method = case.split(".")
        target = module + "." + cls + ("." + method if repeated_method else "")
        rows.append(f"{method} ({target}) ... {suffix}")
    return ("\n".join(rows) + "\n\n----------------------------------------------------------------------\n"
            "Ran 56 tests in 0.010s\n\nOK\n").encode()


def execution(folder):
    record = {"schema": "motionwright.unittest-execution/1", "status": "PASS", "exit_code": 0,
              "require_complete": True, "executed_source_sha": SHA, "tree_sha": TREE,
              "platform": "Linux-fixture", "github": {**GITHUB, "GITHUB_JOB": gate.SOURCE_JOB},
              "runner_sha256": "d" * 64, "python_executable_sha256": "e" * 64,
              "test_definition_sha256": {gate.TEST_PATH: "d" * 64},
              "source_locks": {p: "d" * 64 for p in gate.LOCKS}, "steps": []}
    for index, label in enumerate(("discovery:" + gate.PATTERN, "dotted:" + gate.TEST_MODULE)):
        module = gate.PATTERN[:-3] if index == 0 else gate.TEST_MODULE
        raw = verbose(module)
        tail = (["discover", "-s", "tooling/tests", "-p", gate.PATTERN, "-v"] if index == 0
                else [gate.TEST_MODULE, "-v"])
        step = {"entry_point": label, "status": "PASS", "exit_code": 0,
                "command": ["/usr/bin/python3", "-B", "-E", "-s", "-m", "unittest", *tail],
                "counts": gate.runner.parse_unittest_summary(raw.decode())}
        for stream, data in (("stdout", b""), ("stderr", raw)):
            name = f"execution-{index:02d}-{stream}.log"
            (folder / name).write_bytes(data)
            step[stream] = {"file": name, "sha256": hashlib.sha256(data).hexdigest()}
        record["steps"].append(step)
    return record


def window():
    return {"schema": "motionwright.ci.installed-cold-relaunch/2", "motionwright_sha": SHA,
            "semwright_sha": SDK, "storage_cohort": {"storage_schema": 4},
            "status": "WINDOW_OBSERVED", "window_title": "Motionwright",
            "scope": "extracted_unsigned_linux_appimage_rootless_xvfb", "launch_count": 2,
            "first_launch": {"status": "WINDOW_OBSERVED"}, "reopened_launch": {"status": "WINDOW_OBSERVED"},
            "first_use_action": {"schema": "motionwright.ci.accessible-action/1",
                "status": "ACTION_ACCEPTED", "phase": "ACTION_ACCEPTED",
                "edit_intended": True, "create_intended": True,
                "project_commit": "NOT_VERIFIED_BY_ACTION",
                "binding": {"window": "0x10", "pid": 100, "start_time": "1234"}},
            "claims": dict(gate.CLAIMS), "persistence": {
                "status": "PASS", "schema_version": 4, "native_receipts": 0,
                "one_project_after_both_launches": True, "same_project_id": True,
                "same_generation": True, "same_revision": True, "same_domain_document_sha256": True,
                "same_event_count": True, "sqlite_integrity_checked_twice": True,
                "project_identity_sha256": "f" * 64, "generation_sha256": "f" * 64,
                "document_sha256": "f" * 64}}


def first_use_g0_verbose(prefix="", cases=None, repeated_method=True, descriptions=False):
    selected = sorted(gate.FIRST_USE_G0_CASES if cases is None else cases)
    rows = []
    for index, case in enumerate(selected):
        method = case.rsplit(".", 1)[-1]
        name = prefix + (case if repeated_method else case.rsplit(".", 1)[0])
        if descriptions and index % 5 == 0:
            rows.append(f"{method} ({name})\nSynthetic short description. ... ok")
        else:
            rows.append(f"{method} ({name}) ... ok")
    return ("\n".join(rows) + "\n\n----------------------------------------------------------------------\n"
            f"Ran {len(selected)} tests in 0.010s\n\nOK\n").encode()


def g0_execution(folder):
    record = execution(folder)
    record["test_definition_sha256"] = {"tooling/tests/" + p: "d" * 64 for p in gate.G0_PATTERNS}
    record["steps"] = []
    choices = [("discovery:" + p, ["discover", "-s", "tooling/tests", "-p", p, "-v"]) for p in gate.G0_PATTERNS]
    choices += [("dotted:tooling.tests." + p[:-3], ["tooling.tests." + p[:-3], "-v"]) for p in sorted(gate.G0_PATTERNS)]
    for index, (label, tail) in enumerate(choices):
        raw = (first_use_g0_verbose("tooling.tests." if label.startswith("dotted:") else "")
               if "test_runtime_first_use_audit" in label
               else b"Ran 2 tests in 0.001s\n\nOK\n")
        step = {"entry_point": label, "status": "PASS", "exit_code": 0,
                "command": ["/usr/bin/python3", "-B", "-E", "-s", "-m", "unittest", *tail],
                "counts": gate.runner.parse_unittest_summary(raw.decode())}
        for stream, data in (("stdout", b""), ("stderr", raw)):
            name = f"g0-{index:02d}-{stream}.log"
            (folder / name).write_bytes(data)
            step[stream] = {"file": name, "sha256": hashlib.sha256(data).hexdigest()}
        record["steps"].append(step)
    return record


class ClosedSourceResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record = execution(self.root)

    def admit(self):
        return gate.admit_execution(self.root, self.record, state(), GITHUB)

    def test_two_original_entrypoints_admit_exact_112_cases(self):
        result = self.admit()
        self.assertEqual([len(v) for v in result.values()], [56, 56])
        self.assertIn("JournalFilesystemTests.test_exclusive_entries_keep_original_bytes", next(iter(result.values())))

    def test_both_supported_verbose_id_formats_are_nominal(self):
        for repeated in (False, True):
            with self.subTest(repeated=repeated):
                self.assertEqual(len(gate.nominal_results(verbose(gate.TEST_MODULE, repeated_method=repeated), gate.TEST_MODULE)), 56)

    def test_unchanged_count_cannot_hide_duplicate_and_omitted_case(self):
        cases = sorted(gate.EXPECTED_CASES)
        cases[-1] = cases[0]
        with self.assertRaisesRegex(gate.GateError, "missing_extra_or_duplicate"):
            gate.nominal_results(verbose(gate.TEST_MODULE, cases=cases), gate.TEST_MODULE)

    def test_unchanged_count_cannot_substitute_foreign_case(self):
        cases = sorted(gate.EXPECTED_CASES)
        cases[-1] = "UnknownTests.test_not_reviewed"
        with self.assertRaisesRegex(gate.GateError, "missing_extra_or_duplicate"):
            gate.nominal_results(verbose(gate.TEST_MODULE, cases=cases), gate.TEST_MODULE)

    def test_zero_or_missing_nominal_results_are_refused(self):
        legacy_classes = (
            "SelectionAndAttemptTests.", "X11IdentityTests.",
            "ColdOracleIntegrationContractTests.", "JournalFilesystemTests.",
            "SourceCohortIntegrationTests.",
        )
        legacy_cases = sorted(case for case in gate.EXPECTED_CASES
                              if case.startswith(legacy_classes))
        self.assertEqual(len(legacy_cases), 42)
        legacy_claiming_56 = verbose(gate.TEST_MODULE, cases=legacy_cases)
        legacy_claiming_42 = legacy_claiming_56.replace(b"Ran 56 tests", b"Ran 42 tests", 1)
        controls = (
            ("summary_without_nominals", b"Ran 56 tests in 0s\nOK\n"),
            ("empty_nominals", verbose(gate.TEST_MODULE, cases=[])),
            ("legacy_42_with_original_summary", legacy_claiming_42),
            ("legacy_42_with_forged_56_summary", legacy_claiming_56),
        )
        for label, raw in controls:
            with self.subTest(control=label), self.assertRaises(gate.GateError):
                gate.nominal_results(raw, gate.TEST_MODULE)

    def test_skip_expected_failure_and_failure_are_not_pass(self):
        for result in ("skipped 'Linux only'", "expected failure", "FAIL", "ERROR"):
            with self.subTest(result=result), self.assertRaisesRegex(gate.GateError, "not_ok"):
                gate.nominal_results(verbose(gate.TEST_MODULE, suffix=result), gate.TEST_MODULE)
        step = self.record["steps"][0]
        path = self.root / step["stderr"]["file"]
        raw = path.read_bytes()
        method = "test_exclusive_entries_keep_original_bytes"
        module = gate.PATTERN[:-3]
        original_line = f"{method} ({module}.JournalFilesystemTests.{method}) ... ok\n".encode()
        skipped_line = (f"{method} ({module}.JournalFilesystemTests.{method}) "
                        "... skipped 'Linux dirfd fixture unavailable'\n").encode()
        self.assertEqual(raw.count(original_line), 1)
        self.assertEqual(raw.count(b"\n\nOK\n"), 1)
        raw = raw.replace(original_line, skipped_line, 1)
        raw = raw.replace(b"\n\nOK\n", b"\n\nOK (skipped=1)\n", 1)
        path.write_bytes(raw)
        step["stderr"]["sha256"] = hashlib.sha256(raw).hexdigest()
        step["counts"] = gate.runner.parse_unittest_summary(raw.decode())
        self.assertEqual(step["counts"]["tests_run"], 56)
        self.assertEqual(step["counts"]["passed"], 55)
        self.assertEqual(step["counts"]["skipped"], 1)
        with self.assertRaisesRegex(gate.GateError, "source_counts_incomplete_or_changed"):
            self.admit()

    def test_foreign_test_module_is_refused(self):
        with self.assertRaisesRegex(gate.GateError, "foreign_test_module"):
            gate.nominal_results(verbose("other.tests"), gate.TEST_MODULE)

    def test_changed_original_stream_is_refused(self):
        (self.root / self.record["steps"][0]["stderr"]["file"]).write_bytes(b"changed")
        with self.assertRaisesRegex(gate.GateError, "original_log_hash_mismatch"):
            self.admit()

    def test_matching_summary_cannot_hide_changed_case_names(self):
        step = self.record["steps"][0]
        path = self.root / step["stderr"]["file"]
        raw = path.read_bytes().replace(b"test_same_title_other_application_has_no_authority",
                                       b"test_unreviewed_title")
        path.write_bytes(raw)
        step["stderr"]["sha256"] = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(gate.GateError, "missing_extra_or_duplicate"):
            self.admit()

    def test_foreign_sha_tree_definition_and_locks_refused(self):
        changes = {"executed_source_sha": "f" * 40, "tree_sha": "f" * 40,
                   "test_definition_sha256": {}, "runner_sha256": "f" * 64, "source_locks": {}}
        for key, value in changes.items():
            with self.subTest(key=key), patch.dict(self.record, {key: value}), self.assertRaises(gate.GateError):
                self.admit()

    def test_foreign_run_attempt_job_and_platform_refused(self):
        for key, value in (("GITHUB_SHA", "f" * 40), ("GITHUB_RUN_ID", "124"),
                           ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_JOB", "other")):
            with self.subTest(key=key), patch.dict(self.record["github"], {key: value}), self.assertRaises(gate.GateError):
                self.admit()
        with patch.dict(self.record, {"platform": "Windows-fixture"}), self.assertRaises(gate.GateError):
            self.admit()

    def test_summary_omission_or_corruption_is_refused(self):
        changes = (("tests_run", 55), ("tests_run", 42), ("skipped", 1),
                   ("passed", 55), ("passed", 42), ("errors", 1), ("errors", False))
        for key, value in changes:
            with self.subTest(key=key, value=value), patch.dict(
                self.record["steps"][0]["counts"], {key: value}
            ), self.assertRaises(gate.GateError):
                self.admit()

    def test_nonterminal_incomplete_source_and_false_exit_are_refused(self):
        for change in ({"status": "UNKNOWN"}, {"status": "PASS_WITH_OMISSIONS"},
                       {"require_complete": False}, {"exit_code": False},
                       {"source_postcheck_error": "changed"}):
            with self.subTest(change=change), patch.dict(self.record, change), self.assertRaises(gate.GateError):
                self.admit()
        source_state = state()
        g0 = g0_execution(self.root)
        source_gate = {
            "schema": gate.SOURCE_SCHEMA, "status": "PASS", "invocations": 112,
            "system_python": "/usr/bin/python3", "github": copy.deepcopy(GITHUB),
            "claims": copy.deepcopy(gate.CLAIMS),
            "source_before": source_state, "source_after": source_state,
            "test_ids": gate.admit_execution(self.root, self.record, source_state, GITHUB),
            "g0_entry_points": gate.admit_g0(self.root, g0, source_state, GITHUB),
        }
        intent = {"schema": gate.SOURCE_SCHEMA, "status": "STARTED",
                  "github": copy.deepcopy(GITHUB), "before": source_state}
        for name, value in (("execution.json", self.record), ("g0.json", g0),
                            ("intent.json", intent), ("source-gate.json", source_gate)):
            gate.publish(self.root / name, value)
        admitted = gate.admit_source_gate(self.root, source_state, GITHUB)
        self.assertIn("source-gate.json", admitted)
        stale = copy.deepcopy(source_gate)
        stale["invocations"] = 84
        (self.root / "source-gate.json").write_bytes(
            (json.dumps(stale, indent=2, sort_keys=True) + "\n").encode())
        with self.assertRaisesRegex(gate.GateError, "source_gate_not_admitted_for_this_run"):
            gate.admit_source_gate(self.root, source_state, GITHUB)

    def test_one_entrypoint_swapped_entrypoints_or_wrong_python_refused(self):
        original = copy.deepcopy(self.record["steps"])
        for steps in (original[:1], list(reversed(original))):
            with patch.dict(self.record, {"steps": steps}), self.assertRaises(gate.GateError):
                self.admit()
        self.record["steps"][0]["command"][0] = "/opt/python/bin/python3"
        with self.assertRaisesRegex(gate.GateError, "command_changed"):
            self.admit()

    def test_log_traversal_absolute_and_symlink_refused(self):
        ref = self.record["steps"][0]["stderr"]
        for path in ("../stderr.log", "/tmp/stderr.log"):
            with patch.dict(ref, {"file": path}), self.assertRaises(gate.GateError):
                self.admit()
        link = self.root / "linked.log"
        link.symlink_to(self.root / ref["file"])
        with patch.dict(ref, {"file": link.name}), self.assertRaises(ValueError):
            self.admit()

    def test_all_nine_g0_families_require_both_original_entrypoints(self):
        g0 = g0_execution(self.root)
        admitted = gate.admit_g0(self.root, g0, state(), GITHUB)
        self.assertEqual(len(admitted), 18)
        self.assertEqual(set(admitted.values()), {2, 52})
        self.assertEqual(admitted["discovery:test_runtime_first_use_audit.py"], 52)
        self.assertEqual(admitted["dotted:tooling.tests.test_runtime_first_use_audit"], 52)
        g0["steps"].pop()
        with self.assertRaisesRegex(gate.GateError, "all_g0_entry_points"):
            gate.admit_g0(self.root, g0, state(), GITHUB)

    def test_g0_cannot_borrow_source_definition_run_or_step(self):
        g0 = g0_execution(self.root)
        for change in ({"tree_sha": "f" * 40}, {"test_definition_sha256": {}}, {"source_locks": {}}):
            with self.subTest(change=change), patch.dict(g0, change), self.assertRaises(gate.GateError):
                gate.admit_g0(self.root, g0, state(), GITHUB)
        g0["steps"][0]["entry_point"] = "dotted:unreviewed"
        with self.assertRaisesRegex(gate.GateError, "entrypoint"):
            gate.admit_g0(self.root, g0, state(), GITHUB)

    def test_g0_original_skip_cannot_be_presented_as_success(self):
        g0 = g0_execution(self.root)
        step = g0["steps"][0]
        raw = b"Ran 2 tests in 0.001s\n\nOK (skipped=1)\n"
        (self.root / step["stderr"]["file"]).write_bytes(raw)
        step["stderr"]["sha256"] = hashlib.sha256(raw).hexdigest()
        step["counts"] = gate.runner.parse_unittest_summary(raw.decode())
        with self.assertRaisesRegex(gate.GateError, "g0_counts"):
            gate.admit_g0(self.root, g0, state(), GITHUB)


class InstalledReceiptTests(unittest.TestCase):
    def setUp(self):
        self.receipt = window()

    def admit(self):
        return gate.validate_window(self.receipt, SHA, SDK, {"storage_schema": 4})

    def test_only_two_windows_action_ack_and_persisted_source_cohort_admit(self):
        self.assertEqual(self.admit(), self.receipt)

    def test_source_sdk_or_storage_change_is_refused(self):
        for key, value in (("motionwright_sha", "f" * 40), ("semwright_sha", "f" * 40),
                           ("storage_cohort", {"storage_schema": 3})):
            with self.subTest(key=key), patch.dict(self.receipt, {key: value}), self.assertRaises(gate.GateError):
                self.admit()

    def test_missing_second_window_and_unconfirmed_first_action_refused(self):
        with patch.dict(self.receipt, {"launch_count": 1}), self.assertRaises(gate.GateError):
            self.admit()
        with patch.dict(self.receipt["reopened_launch"], {"status": "UNKNOWN"}), self.assertRaises(gate.GateError):
            self.admit()
        with patch.dict(self.receipt["first_use_action"], {"status": "UNKNOWN"}), self.assertRaises(gate.GateError):
            self.admit()

    def test_action_ack_cannot_claim_database_commit(self):
        with patch.dict(self.receipt["first_use_action"], {"project_commit": "PASS"}), self.assertRaises(gate.GateError):
            self.admit()

    def test_action_requires_both_intents_and_real_process_binding(self):
        for change in ({"edit_intended": False}, {"create_intended": False},
                       {"binding": {"window": "0x1", "pid": True, "start_time": "1"}}):
            with self.subTest(change=change), patch.dict(self.receipt["first_use_action"], change), self.assertRaises(gate.GateError):
                self.admit()

    def test_each_persistence_invariant_and_identity_required(self):
        for key in ("one_project_after_both_launches", "same_project_id", "same_generation", "same_revision",
                    "same_domain_document_sha256", "same_event_count", "sqlite_integrity_checked_twice"):
            with self.subTest(key=key), patch.dict(self.receipt["persistence"], {key: False}), self.assertRaises(gate.GateError):
                self.admit()
        with patch.dict(self.receipt["persistence"], {"document_sha256": "unknown"}), self.assertRaises(gate.GateError):
            self.admit()

    def test_human_native_or_signed_claim_promotion_is_refused(self):
        for key in gate.CLAIMS:
            with self.subTest(key=key), patch.dict(self.receipt["claims"], {key: "PASS"}), self.assertRaises(gate.GateError):
                self.admit()


class EffectBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.namespace = self.root / ("linux-first-use-123-1-" + SHA)

    def test_create_once_record_does_not_replace_original(self):
        target = self.root / "intent.json"
        gate.publish(target, {"status": "INTENDED"})
        raw = target.read_bytes()
        with self.assertRaises(FileExistsError):
            gate.publish(target, {"status": "NEW_ATTEMPT"})
        self.assertEqual(target.read_bytes(), raw)

    def test_duplicate_json_keys_are_not_last_value_wins(self):
        target = self.root / "receipt.json"
        target.write_text('{"status":"UNKNOWN","status":"PASS"}')
        with self.assertRaisesRegex(gate.GateError, "duplicate_json_key"):
            gate.load_json(target)

    def test_fresh_namespace_refuses_existing_and_outside_runner_temp(self):
        with patch.dict(os.environ, {"RUNNER_TEMP": str(self.root)}):
            self.assertEqual(gate.fresh_directory(self.namespace, self.repo), self.namespace)
            with self.assertRaises(FileExistsError):
                gate.fresh_directory(self.namespace, self.repo)
            with self.assertRaises(gate.GateError):
                gate.fresh_directory(self.root, self.repo)
            with self.assertRaises(gate.GateError):
                gate.fresh_directory(self.repo / "evidence", self.repo)

    def test_source_or_shared_cohort_drift_refuses_publication(self):
        for after in ({**state(), "storage_cohort": {"storage_schema": 3}},
                      {**state(), "source": {"sha": "f" * 40}}):
            with self.subTest(after=after), self.assertRaisesRegex(gate.GateError, "source_or_storage_cohort_changed"):
                gate.require_same(state(), after)

    def test_attempt_two_and_nondispatch_stop_before_reservation(self):
        env = {**GITHUB, "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch"}
        with patch.dict(os.environ, env), patch.object(gate.sys, "platform", "linux"), \
                patch.object(gate.sys, "executable", "/usr/bin/python3"):
            self.assertEqual(gate.environment(installed=True), GITHUB)
            with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}), self.assertRaisesRegex(gate.GateError, "attempt_one"):
                gate.environment(installed=True)
            with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}), self.assertRaisesRegex(gate.GateError, "explicit_dispatch"):
                gate.environment(installed=True)

    def test_nonlinux_host_is_not_installed_linux_evidence(self):
        with patch.object(gate.sys, "platform", "win32"), self.assertRaisesRegex(gate.GateError, "disposable_linux"):
            gate.environment(installed=True)

    def test_reservation_is_written_after_admission_and_cannot_replay(self):
        with patch.dict(os.environ, {"RUNNER_TEMP": str(self.root)}), \
                patch.object(gate, "environment", return_value=GITHUB), \
                patch.object(gate, "observe", return_value=state()), \
                patch.object(gate, "admit_source_gate", return_value={"fixture": "source originals"}):
            self.assertEqual(gate.reserve(self.repo, SHA, self.namespace, self.repo), 0)
            first = (self.namespace / "reservation.json").read_bytes()
            with self.assertRaises(FileExistsError):
                gate.reserve(self.repo, SHA, self.namespace, self.repo)
            self.assertEqual((self.namespace / "reservation.json").read_bytes(), first)

    def test_failed_source_admission_creates_no_install_namespace(self):
        with patch.dict(os.environ, {"RUNNER_TEMP": str(self.root)}), \
                patch.object(gate, "environment", return_value=GITHUB), \
                patch.object(gate, "observe", return_value=state()), \
                patch.object(gate, "admit_source_gate", side_effect=gate.GateError("source failed")):
            with self.assertRaisesRegex(gate.GateError, "source failed"):
                gate.reserve(self.repo, SHA, self.namespace, self.repo)
        self.assertFalse(self.namespace.exists())

    def test_other_namespace_cannot_bypass_a_prior_run_intent(self):
        with patch.dict(os.environ, {"RUNNER_TEMP": str(self.root)}), \
                patch.object(gate, "environment", return_value=GITHUB), \
                patch.object(gate, "observe") as source:
            with self.assertRaisesRegex(gate.GateError, "namespace_must_match_run"):
                gate.reserve(self.repo, SHA, self.root / "different", self.repo)
        source.assert_not_called()

    def test_timeout_or_nonzero_keeps_intent_and_never_invokes_again(self):
        for outcome in (subprocess.TimeoutExpired(["fixture"], 110),
                        subprocess.CompletedProcess(["fixture"], 2)):
            with self.subTest(outcome=type(outcome).__name__), tempfile.TemporaryDirectory() as temporary:
                temp = Path(temporary)
                repo = temp / "source"
                repo.mkdir()
                (repo / "SOURCE_LOCK.json").write_text(json.dumps({"dependencies": {"semwright": {"revision": SDK}}}))
                image = repo / "fixture.AppImage"
                candidate = repo / "candidate.json"
                image.write_bytes(b"synthetic image")
                candidate.write_bytes(b"synthetic receipt")
                namespace = temp / ("linux-first-use-123-1-" + SHA)
                env = {"RUNNER_TEMP": str(temp), "DISPLAY": ":fixture", "DBUS_SESSION_BUS_ADDRESS": "fixture"}
                with patch.dict(os.environ, env), patch.object(gate, "environment", return_value=GITHUB), \
                        patch.object(gate, "observe", return_value=state()), \
                        patch.object(gate, "admit_source_gate", return_value={"fixture": "source originals"}):
                    gate.reserve(repo, SHA, namespace, repo)
                    app = namespace / "AppRun"
                    binary = namespace / "motionwright"
                    app.write_bytes(b"synthetic launcher")
                    binary.write_bytes(b"synthetic binary")
                    with patch.object(gate.subprocess, "run", side_effect=outcome if isinstance(outcome, Exception) else None,
                                      return_value=outcome) as process:
                        self.assertEqual(gate.installed(repo, SHA, namespace, app, binary, image, candidate), 1)
                        process.assert_called_once()
                        intent = (namespace / "launch-intent.json").read_bytes()
                        terminal = gate.load_json(namespace / "installed-gate.json")
                        self.assertEqual(terminal["status"], "UNKNOWN")
                        self.assertEqual(terminal["retry"], "FORBIDDEN")
                        with self.assertRaises(FileExistsError):
                            gate.installed(repo, SHA, namespace, app, binary, image, candidate)
                        process.assert_called_once()
                        self.assertEqual((namespace / "launch-intent.json").read_bytes(), intent)



class WorkflowContextTests(unittest.TestCase):
    def test_runner_temp_is_bound_only_in_the_executing_step(self):
        workflow = (Path(__file__).resolve().parents[2] /
                    ".github/workflows/package-candidate.yml").read_text(encoding="utf-8")
        source = workflow.split("\n  linux-first-use-source:\n", 1)[1].split(
            "\n  linux-first-use-installed:\n", 1)[0]
        job_context, steps = source.split("    steps:\n", 1)
        self.assertNotRegex(job_context, r"\$\{\{\s*runner(?:\.|\[)")
        target = steps.split(
            "      - name: Exact Linux source roster in discovery and dotted module modes\n", 1)[1].split(
            "      - name:", 1)[0]
        self.assertIn(
            "        env:\n          MW_CI_JOB_TEST_TMP: ${{ runner.temp }}\n",
            target)
        self.assertIn("tooling/ci_linux_first_use.py source", target)
        self.assertEqual(source.count("MW_CI_JOB_TEST_TMP:"), 1)


class FirstUseG0ClosureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.g0 = g0_execution(self.root)

    def admit(self, source_state=None):
        return gate.admit_g0(self.root, self.g0, state() if source_state is None else source_state, GITHUB)

    def replace_stderr(self, index, raw):
        step = self.g0["steps"][index]
        path = self.root / step["stderr"]["file"]
        path.write_bytes(raw)
        step["stderr"]["sha256"] = hashlib.sha256(raw).hexdigest()
        step["counts"] = gate.runner.parse_unittest_summary(raw.decode())

    def first_use_index(self, mode):
        label = ("discovery:test_runtime_first_use_audit.py" if mode == "discovery"
                 else "dotted:tooling.tests.test_runtime_first_use_audit")
        return next(i for i, step in enumerate(self.g0["steps"]) if step["entry_point"] == label)

    def test_first_use_has_exact_fifty_two_names_in_both_entrypoints(self):
        result = self.admit()
        self.assertEqual(result["discovery:test_runtime_first_use_audit.py"], 52)
        self.assertEqual(result["dotted:tooling.tests.test_runtime_first_use_audit"], 52)
        for mode, prefix in (("discovery", ""), ("dotted", "tooling.tests.")):
            for repeated in (False, True):
                raw = first_use_g0_verbose(prefix, repeated_method=repeated, descriptions=True)
                self.assertEqual(gate.first_use_nominal_results(raw, mode), sorted(gate.FIRST_USE_G0_CASES))

    def test_dotted_forty_three_cannot_borrow_discovery_atomic_nine(self):
        base_cases = [case for case in gate.FIRST_USE_G0_CASES
                      if case.startswith("test_runtime_first_use_audit.")]
        self.assertEqual(len(base_cases), 43)
        for forged_count in (False, True):
            with self.subTest(forged_count=forged_count):
                self.g0 = g0_execution(self.root)
                raw = first_use_g0_verbose("tooling.tests.", base_cases)
                if forged_count:
                    raw = raw.replace(b"Ran 43 tests", b"Ran 52 tests", 1)
                self.replace_stderr(self.first_use_index("dotted"), raw)
                with self.assertRaises(gate.GateError):
                    self.admit()

    def test_rehashed_missing_duplicate_foreign_or_skipped_atomic_cannot_pass(self):
        cases = sorted(gate.FIRST_USE_G0_CASES)
        atomic = next(case for case in cases if case.startswith("test_runtime_first_use_atomic_receipt."))
        other = next(case for case in cases if case != atomic)
        controls = (
            [case for case in cases if case != atomic],
            [other if case == atomic else case for case in cases],
            ["test_runtime_first_use_atomic_receipt.AtomicSupportReceiptTests.test_foreign"
             if case == atomic else case for case in cases],
        )
        for selected in controls:
            with self.subTest(selected=len(selected)):
                self.g0 = g0_execution(self.root)
                raw = first_use_g0_verbose(cases=selected)
                self.replace_stderr(self.first_use_index("discovery"), raw)
                with self.assertRaises(gate.GateError):
                    self.admit()
        self.g0 = g0_execution(self.root)
        raw = first_use_g0_verbose()
        method = atomic.rsplit(".", 1)[-1]
        old = f"{method} ({atomic}) ... ok\n".encode()
        self.assertEqual(raw.count(old), 1)
        raw = raw.replace(old, f"{method} ({atomic}) ... skipped 'unavailable'\n".encode(), 1)
        raw = raw.replace(b"\n\nOK\n", b"\n\nOK (skipped=1)\n", 1)
        self.replace_stderr(self.first_use_index("discovery"), raw)
        with self.assertRaises(gate.GateError):
            self.admit()

    def test_atomic_and_runtime_dependency_inputs_cannot_be_omitted(self):
        for missing in ("tooling/tests/test_runtime_first_use_atomic_receipt.py",
                        "tooling/runtime_first_use_audit.py"):
            with self.subTest(missing=missing):
                original = state()
                original["source"]["files"].pop(missing)
                with self.assertRaisesRegex(gate.GateError, "g0_source_dependency_closure"):
                    self.admit(original)

    def test_hidden_atomic_source_change_refuses_full_gate_without_rewriting_originals(self):
        source_state = state()
        altered = copy.deepcopy(source_state)
        altered["source"]["files"]["tooling/tests/test_runtime_first_use_atomic_receipt.py"]["checkout"]["sha256"] = "f" * 64
        execution_record = execution(self.root)
        gate_record = {
            "schema": gate.SOURCE_SCHEMA, "status": "PASS", "invocations": 112,
            "system_python": "/usr/bin/python3", "github": copy.deepcopy(GITHUB),
            "claims": copy.deepcopy(gate.CLAIMS), "source_before": source_state,
            "source_after": altered,
            "test_ids": gate.admit_execution(self.root, execution_record, source_state, GITHUB),
            "g0_entry_points": self.admit(source_state),
        }
        intent = {"schema": gate.SOURCE_SCHEMA, "status": "STARTED",
                  "github": copy.deepcopy(GITHUB), "before": source_state}
        for name, value in (("execution.json", execution_record), ("g0.json", self.g0),
                            ("intent.json", intent), ("source-gate.json", gate_record)):
            (self.root / name).write_bytes((json.dumps(value) + "\n").encode())
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        with self.assertRaisesRegex(gate.GateError, "source_or_storage_cohort_changed"):
            gate.admit_source_gate(self.root, source_state, GITHUB)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_missing_or_orphan_terminals_and_wrong_mode_are_not_nominal_success(self):
        raw = first_use_g0_verbose(descriptions=True)
        controls = (
            raw.replace(b"Synthetic short description. ... ok", b"Synthetic short description. ... FAIL", 1),
            raw.replace(b"Synthetic short description. ... ok\n", b"", 1),
            b"ok\n" + raw,
            first_use_g0_verbose("tooling.tests."),
        )
        for control in controls:
            with self.subTest(control=control[:80]), self.assertRaises(gate.GateError):
                gate.first_use_nominal_results(control, "discovery")


if __name__ == "__main__":
    unittest.main()
