#!/usr/bin/env python3
"""Positive and causal rejection controls for the frozen SDK source observer."""
from __future__ import annotations
import ast
import json
import io
from contextlib import redirect_stdout
from unittest import mock
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
import ci_sdk_native_source as gate


class SourceGateTests(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get("MW_SDK_G0_TEMP", tempfile.gettempdir())) / "sdk-source-controls"
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="control-", dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "source"
        self.target = self.root / "target"
        self.repo.mkdir(); self.target.mkdir()
        self.binary_path = self.target / "selected-test"
        self.binary_path.write_bytes(b"synthetic identity fixture; never executed\n")
        self.binary = {"absolute_path": str(self.binary_path),
                       **gate.custody.fingerprint(self.binary_path, self.target)}
        self.env = {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "MW_SDK_SCOPE": "sdk_native_source", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
            "GITHUB_REPOSITORY": "seradotcom/motionwright", "GITHUB_JOB": "sdk-native-source",
            "GITHUB_SHA": "1" * 40, "GITHUB_WORKFLOW_SHA": "1" * 40,
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "123",
            "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/" + gate.WORKFLOW + "@refs/heads/example",
        }

    def cargo(self, selected=None, testing=True):
        selected = selected or gate.MLT_TESTS[0]
        artifact = {
            "reason": "compiler-artifact",
            "manifest_path": str(self.repo / ("crates/" + selected["crate"] + "/Cargo.toml")),
            "target": {"name": selected["name"], "kind": [selected["kind"]],
                       "src_path": str(self.repo / selected["source"])},
            "profile": {"test": testing}, "features": ["test-tools"],
            "executable": str(self.binary_path),
        }
        return [artifact, {"reason": "build-finished", "success": True}]

    @staticmethod
    def raw(rows):
        return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()

    def selected_binary(self, rows, code=0):
        return gate.select_binaries(self.raw(rows), code, self.repo, self.target,
                                    gate.MLT_TESTS[:1], testing=True)

    def case(self, name, role):
        pair = gate.causal_pair()
        listed = {"all": tuple(gate.CAUSAL_CASES), "selected": tuple(gate.CAUSAL_CASES)}
        failed = role == "baseline" and name in pair["failures"]
        context = {"product_source_sha": gate.RED if role == "baseline" else gate.GREEN, "role": role}
        row = {"argv": gate.observer.case_argv(self.binary, pair, role, name), "context": context,
               "observation": "DIRECT_PROCESS_EXIT_OBSERVED", "log_custody": "VERIFIED_SNAPSHOT",
               "exit_code": 101 if failed else 0}
        state = "FAILED" if failed else "ok"
        text = f"running 1 test\ntest {name} ... {state}\n"
        if failed:
            site = pair["failures"][name]
            text += (f"\nthread '{name}' panicked at {site['source']}:{site['line']}:9:\n"
                     + "\n".join(site["markers"]) + "\n")
        text += (f"test result: {state}. {0 if failed else 1} passed; {1 if failed else 0} failed; "
                 f"0 ignored; 0 measured; {len(listed['all'])-1} filtered out; finished in 0.01s\n")
        return pair, listed, context, row, text.encode()

    def admit(self, name, role, *, edit=None):
        pair, listed, context, row, raw = self.case(name, role)
        if edit:
            raw, row = edit(raw, row)
        return gate.observer.admit_case(pair, role, name, raw, row, self.binary,
                                        self.binary, listed, context)

    def test_manual_linux_context_is_bound_to_gate_source(self):
        value = gate.execution_context(self.env, "linux")
        self.assertEqual(value["GITHUB_SHA"], "1" * 40)
        self.assertNotIn(value["GITHUB_SHA"], (gate.RED, gate.GREEN))

    def test_wrong_platform_event_repository_job_and_replay_are_rejected(self):
        for key, value in (("RUNNER_OS", "Windows"), ("RUNNER_ARCH", "ARM64"),
                           ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_RUN_ATTEMPT", "2"),
                           ("GITHUB_REPOSITORY", "example/other"), ("GITHUB_JOB", "native-render"),
                           ("MW_SDK_SCOPE", "physical"), ("GITHUB_WORKFLOW_SHA", "2" * 40)):
            with self.subTest(key=key), self.assertRaises(gate.custody.VerificationError):
                gate.execution_context({**self.env, key: value}, "linux")

    def test_gate_checkout_cannot_be_the_sdk_product_commit(self):
        with self.assertRaises(gate.custody.VerificationError):
            gate.execution_context({**self.env, "GITHUB_SHA": gate.GREEN,
                                    "GITHUB_WORKFLOW_SHA": gate.GREEN}, "linux")

    def test_runtime_and_test_overrides_are_rejected(self):
        for key in ("SEMWRIGHT_TEST_MLT_VIDEO", "NODE_OPTIONS", "NODE_PATH",
                    "NPM_CONFIG_USERCONFIG", "TS_NODE_PROJECT"):
            with self.subTest(key=key), self.assertRaises(gate.custody.VerificationError):
                gate.execution_context({**self.env, key: "fixture"}, "linux")

    def test_source_pair_keeps_24_paths_and_exact_three_repairs(self):
        self.assertEqual(set(gate.FROZEN[gate.RED]), set(gate.FROZEN[gate.GREEN]))
        self.assertEqual(len(gate.FROZEN[gate.GREEN]), 24)
        different = {p for p in gate.FROZEN[gate.RED]
                     if gate.FROZEN[gate.RED][p] != gate.FROZEN[gate.GREEN][p]}
        self.assertEqual(different, set(gate.REPAIR_PATHS))
        self.assertEqual(len(gate.MANDATORY_MODULES), 3)

    def test_successful_cargo_artifact_is_bound_to_exact_manifest_source_and_binary(self):
        result = self.selected_binary(self.cargo())
        self.assertEqual(result["semwright_mlt_video"], self.binary)

    def test_compiler_exit_cannot_establish_causal_failure(self):
        for code in (101, 1, -9, None, True):
            with self.subTest(code=code), self.assertRaises(gate.custody.VerificationError):
                self.selected_binary(self.cargo(), code)

    def test_missing_or_failed_build_completion_is_rejected(self):
        for rows in (self.cargo()[:1], [self.cargo()[0], {"reason": "build-finished", "success": False}],
                     [*self.cargo(), {"reason": "build-finished", "success": True}]):
            with self.subTest(rows=rows), self.assertRaises(gate.custody.VerificationError):
                self.selected_binary(rows)

    def test_compiler_error_diagnostic_cannot_be_hidden_by_success_footer(self):
        rows = self.cargo()
        rows.insert(0, {"reason": "compiler-message", "message": {"level": "error"}})
        with self.assertRaises(gate.custody.VerificationError):
            self.selected_binary(rows)

    def test_foreign_manifest_and_source_are_rejected(self):
        for field in ("manifest_path", "src_path"):
            rows = self.cargo()
            if field == "manifest_path":
                rows[0][field] = str(self.root / "elsewhere/Cargo.toml")
            else:
                rows[0]["target"][field] = str(self.repo / "crates/driver-mlt-video/src/other.rs")
            with self.subTest(field=field), self.assertRaises(gate.custody.VerificationError):
                self.selected_binary(rows)

    def test_duplicate_or_missing_binary_is_rejected(self):
        for rows in ([self.cargo()[0], *self.cargo()], self.cargo()[1:]):
            with self.subTest(rows=rows), self.assertRaises(gate.custody.VerificationError):
                self.selected_binary(rows)

    def test_test_tools_feature_and_test_profile_are_required(self):
        for change in ("features", "profile"):
            rows = self.cargo()
            if change == "features":
                rows[0]["features"] = []
            else:
                rows[0]["profile"]["test"] = False
            with self.subTest(change=change), self.assertRaises(gate.custody.VerificationError):
                self.selected_binary(rows)

    def test_binary_outside_owned_target_is_rejected(self):
        outside = self.root / "outside"
        outside.write_bytes(b"not selected")
        rows = self.cargo(); rows[0]["executable"] = str(outside)
        with self.assertRaises(gate.custody.VerificationError):
            self.selected_binary(rows)

    def test_compiled_binary_replacement_before_execution_is_rejected(self):
        gate.verify_binaries({"selected": self.binary}, self.target)
        self.binary_path.write_bytes(b"synthetic identity fixture; now changed\n")
        with self.assertRaises(gate.custody.VerificationError):
            gate.verify_binaries({"selected": self.binary}, self.target)

    def producer_fixture(self, *, unknown=None, corrupt=None, code=0):
        originals, calls = {}, []
        specs = {
            "test-tools-build": ((gate.MLT_TESTS[1:], False),),
            "mlt-test-compile": ((gate.MLT_TESTS, True),),
            "mlt-integration-compile": ((gate.MLT_INTEGRATIONS, True), (gate.MLT_TESTS[1:], False)),
            "video-domain-compile": ((gate.VIDEO_TESTS, True),),
            "motion-canvas-compile": ((gate.MC_TESTS, True),),
            "materialize-compile": (((gate.MATERIALIZE,), False),),
        }
        def command(argv, work, env, logs, label, timeout, ctx):
            calls.append(label)
            if label == unknown:
                raise gate.observer.UnknownExecution("synthetic unresolved compiler", {"argv": argv})
            rows = []
            for selected_specs, testing in specs.get(label, ()):
                if ctx.get("role") == "baseline" and label == "mlt-test-compile":
                    selected_specs = gate.MLT_TESTS[:1]
                for selected in selected_specs:
                    path = self.target / (("test-" if testing else "producer-") + selected["name"])
                    path.write_bytes(("unexecuted producer fixture " + label + " " + selected["name"]).encode())
                    row = self.cargo(selected, testing=testing)[0]
                    row["executable"] = str(path)
                    rows.append(row)
            if corrupt and rows:
                corrupt(rows)
            originals[label] = self.raw([*rows, {"reason": "build-finished", "success": code == 0}])
            return {"exit_code": code, "label": label}
        return command, originals, calls

    def test_producer_barrier_rejects_lists_and_cases_before_all_producers_close(self):
        delegate = mock.Mock()
        barrier = gate.ProducerBarrier(delegate, "bound-cargo")
        for argv in (["harness", "--list"], ["harness", "--exact", "selected"],
                     ["node", "--test", "tests/selected.test.mjs"]):
            with self.subTest(argv=argv), self.assertRaisesRegex(gate.custody.VerificationError,
                                                                 "observation before producer barrier"):
                barrier(argv)
        delegate.assert_not_called()

    def test_producer_barrier_forbids_late_compilation_and_resealing(self):
        delegate = mock.Mock()
        barrier = gate.ProducerBarrier(delegate, "bound-cargo")
        barrier(["bound-cargo", "test", "--test", "conformance", "--no-run"])
        barrier.begin_sealing()
        with self.assertRaisesRegex(gate.custody.VerificationError, "during producer sealing"):
            barrier(["harness", "--list"])
        barrier.begin_observation()
        barrier(["harness", "--exact", "selected"])
        for argv in (["bound-cargo", "build"], ["bound-cargo", "clippy"]):
            with self.subTest(argv=argv), self.assertRaisesRegex(gate.custody.VerificationError,
                                                                 "producer attempted after sealing"):
                barrier(argv)
        with self.assertRaisesRegex(gate.custody.VerificationError, "cannot be resealed"):
            barrier.begin_sealing()
        self.assertEqual(delegate.call_count, 2)

    def test_complete_producer_barrier_seals_after_materialize_and_clippy(self):
        command, originals, calls = self.producer_fixture()
        barrier = gate.ProducerBarrier(command, "bound-cargo")
        expected_steps = ["test-tools-build", "mlt-test-compile", "mlt-integration-compile",
                          "video-domain-compile", "motion-canvas-compile", "materialize-compile", "strict-clippy"]
        with mock.patch.object(gate.observer, "original_stream",
                               side_effect=lambda logs, row, channel: originals[row["label"]]):
            with mock.patch.object(gate.custody, "fingerprint", wraps=gate.custody.fingerprint) as fingerprint:
                prepared = gate.prepare_compiled_role("repair", "bound-cargo", self.repo, {}, self.root,
                                                      {"role": "repair"}, self.target, barrier)
                fingerprint.assert_not_called()
            self.assertEqual(calls, expected_steps)
            self.assertEqual(prepared["clippy"], "PASS")
            self.assertEqual(prepared["collections"]["test-tools"]["label"], "mlt-integration-compile")
            barrier.begin_sealing()
            evidence = self.root / "evidence"; evidence.mkdir()
            sealed = gate.seal_compiled_role(prepared, self.repo, self.target, self.root, evidence, barrier)
        self.assertEqual(set(sealed), {"test-tools", "mlt-tests", "mlt-integrations",
                                      "video-domain", "motion-canvas", "materializer"})
        self.assertEqual(calls, expected_steps)
        for binary in sealed["test-tools"].values():
            self.assertIn(b"mlt-integration-compile", Path(binary["absolute_path"]).read_bytes())
        for collection in sealed.values():
            gate.verify_binaries(collection, self.target)
        barrier.begin_observation()
        binary = next(iter(sealed["test-tools"].values()))
        path = Path(binary["absolute_path"])
        replacement = self.target / "identical-replacement"
        replacement.write_bytes(path.read_bytes()); os.replace(replacement, path)
        with self.assertRaisesRegex(gate.custody.VerificationError, "compiled original binary changed"):
            gate.verify_binaries(sealed["test-tools"], self.target)
        with self.assertRaisesRegex(gate.custody.VerificationError, "cannot be resealed"):
            gate.seal_compiled_role(prepared, self.repo, self.target, self.root, evidence, barrier)

    def test_unknown_producer_stops_without_retry_or_sealing(self):
        command, originals, calls = self.producer_fixture(unknown="mlt-integration-compile")
        barrier = gate.ProducerBarrier(command, "bound-cargo")
        with mock.patch.object(gate.observer, "original_stream",
                               side_effect=lambda logs, row, channel: originals[row["label"]]):
            with mock.patch.object(gate.custody, "fingerprint") as fingerprint:
                with self.assertRaises(gate.observer.UnknownExecution):
                    gate.prepare_compiled_role("repair", "bound-cargo", self.repo, {}, self.root,
                                                {"role": "repair"}, self.target, barrier)
                fingerprint.assert_not_called()
        self.assertEqual(calls, ["test-tools-build", "mlt-test-compile", "mlt-integration-compile"])
        self.assertEqual(barrier.phase, "PREPARING")

    def test_foreign_producer_metadata_stops_before_sealing(self):
        def corrupt(rows):
            rows[0]["target"]["src_path"] = str(self.repo / "foreign.rs")
        command, originals, calls = self.producer_fixture(corrupt=corrupt)
        with mock.patch.object(gate.observer, "original_stream",
                               side_effect=lambda logs, row, channel: originals[row["label"]]):
            with self.assertRaisesRegex(gate.custody.VerificationError, "belongs to another source"):
                gate.prepare_compiled_role("repair", "bound-cargo", self.repo, {}, self.root,
                                            {"role": "repair"}, self.target, command)
        self.assertEqual(calls, ["test-tools-build"])

    def test_failed_compiler_is_not_a_causal_red_or_a_sealed_closure(self):
        command, originals, calls = self.producer_fixture(code=101)
        with mock.patch.object(gate.observer, "original_stream",
                               side_effect=lambda logs, row, channel: originals[row["label"]]):
            with self.assertRaisesRegex(gate.custody.VerificationError, "compiler failure is not causal RED"):
                gate.prepare_compiled_role("repair", "bound-cargo", self.repo, {}, self.root,
                                            {"role": "repair"}, self.target, command)
        self.assertEqual(calls, ["test-tools-build"])

    def test_diagnostic_compilation_never_prepares_repair_supplements(self):
        command, originals, calls = self.producer_fixture()
        with mock.patch.object(gate.observer, "original_stream",
                               side_effect=lambda logs, row, channel: originals[row["label"]]):
            prepared = gate.prepare_compiled_role("baseline", "bound-cargo", self.repo, {}, self.root,
                                                  {"role": "baseline"}, self.target, command)
        self.assertEqual(gate.execution_roles(gate.DIAGNOSTIC_SCOPE), (("baseline", gate.RED),))
        self.assertEqual(calls, ["test-tools-build", "mlt-test-compile"])
        self.assertEqual(set(prepared["collections"]), {"test-tools", "mlt-tests"})
        self.assertEqual(prepared["clippy"], "NOT_SELECTED")

    def test_changed_source_after_all_producers_prevents_sealing_and_observation(self):
        source_reads = {}
        def snapshot(work, source, paths, tree=None):
            if work == self.repo:
                return {"files": {name: {"sha256": digest} for name, digest in gate.GATE_PINS.items()}}
            count = source_reads.get(str(work), 0) + 1
            source_reads[str(work)] = count
            return {"sha": source, "tree": tree, "files": {}, "generation": count}
        def prepare(role, *args):
            return {"collections": {}, "steps": ["synthetic-final-producer"],
                    "clippy": "PASS" if role == "repair" else "NOT_SELECTED"}
        with mock.patch.dict(os.environ, self.env, clear=True), \
             mock.patch.object(gate.sys, "platform", "linux"), \
             mock.patch.object(gate.observer, "__file__", str(self.repo / "tooling/ci_foundation_causal.py")), \
             mock.patch.object(gate.custody, "__file__", str(self.repo / "tooling/ci_storage_apply_recovery.py")), \
             mock.patch.object(gate.custody, "source_snapshot", side_effect=snapshot), \
             mock.patch.object(gate.observer, "retain_source", return_value={}), \
             mock.patch.object(gate.observer, "tools_identity", return_value={"cargo": {"absolute_path": "bound-cargo"}}), \
             mock.patch.object(gate.observer, "command", return_value={"exit_code": 0}) as commands, \
             mock.patch.object(gate, "verify_pair", return_value={}), \
             mock.patch.object(gate, "input_paths", return_value=[]), \
             mock.patch.object(gate, "prepare_compiled_role", side_effect=prepare) as producers, \
             mock.patch.object(gate, "prepare_runtime", return_value={}), \
             mock.patch.object(gate, "seal_compiled_role") as sealing:
            report = gate.run(self.repo, self.root / "sdk", self.root / "output")
        self.assertEqual(producers.call_count, 2)
        sealing.assert_not_called()
        self.assertEqual(report["status"], "FAIL_OR_INCOMPLETE")
        self.assertIn("source changed before producer sealing", report["error"])
        self.assertEqual(len(report["roles"]["baseline"]["results"]), 0)
        self.assertEqual(len(report["roles"]["repair"]["results"]), 0)
        self.assertTrue(all(call.args[0][0] == "git" for call in commands.call_args_list))

    def test_run_prepares_and_seals_both_roles_before_the_first_list(self):
        events = []
        def snapshot(work, source, paths, tree=None):
            if work == self.repo:
                return {"files": {name: {"sha256": digest} for name, digest in gate.GATE_PINS.items()}}
            return {"sha": source, "tree": tree, "files": {}}
        def prepare(role, *args):
            events.append("prepare-" + role)
            return {"role": role, "collections": {}, "steps": ["synthetic-final-producer"],
                    "clippy": "PASS" if role == "repair" else "NOT_SELECTED"}
        def runtime(*args):
            events.append("prepare-runtime")
            return {}
        def seal(prepared, work, target, logs, evidence, barrier):
            self.assertEqual(barrier.phase, "SEALING")
            events.append("seal-" + prepared["role"])
            path = target / "unexecuted-harness"
            path.write_bytes(b"synthetic ordering fixture; never executed")
            binary = {"absolute_path": str(path), **gate.custody.fingerprint(path, target)}
            sealed = {"test-tools": {"fixture": binary}, "mlt-tests": {"semwright_mlt_video": binary},
                      "materializer": {"materialize": binary}}
            prepared["sealed"] = sealed
            return sealed
        def command(argv, *args):
            if "--list" in argv:
                events.append("first-list")
                raise gate.custody.VerificationError("synthetic stop before execution")
            self.assertEqual(argv[0], "git")
            return {"exit_code": 0}
        with mock.patch.dict(os.environ, self.env, clear=True), \
             mock.patch.object(gate.sys, "platform", "linux"), \
             mock.patch.object(gate.observer, "__file__", str(self.repo / "tooling/ci_foundation_causal.py")), \
             mock.patch.object(gate.custody, "__file__", str(self.repo / "tooling/ci_storage_apply_recovery.py")), \
             mock.patch.object(gate.custody, "source_snapshot", side_effect=snapshot), \
             mock.patch.object(gate.observer, "retain_source", return_value={}), \
             mock.patch.object(gate.observer, "tools_identity", return_value={"cargo": {"absolute_path": "bound-cargo"}}), \
             mock.patch.object(gate.observer, "command", side_effect=command), \
             mock.patch.object(gate, "verify_pair", return_value={}), \
             mock.patch.object(gate, "input_paths", return_value=[]), \
             mock.patch.object(gate, "prepare_compiled_role", side_effect=prepare), \
             mock.patch.object(gate, "prepare_runtime", side_effect=runtime), \
             mock.patch.object(gate, "seal_compiled_role", side_effect=seal):
            report = gate.run(self.repo, self.root / "sdk", self.root / "ordered-output")
        self.assertEqual(events, ["prepare-baseline", "prepare-repair", "prepare-runtime",
                                  "seal-baseline", "seal-repair", "first-list"])
        self.assertEqual(report["status"], "FAIL_OR_INCOMPLETE")
        self.assertIn("synthetic stop before execution", report["error"])
        self.assertEqual(report["producer_barrier"]["status"], "SEALED_BEFORE_FIRST_OBSERVATION")

    def test_cargo_duplicate_json_key_is_rejected(self):
        with self.assertRaises(gate.custody.VerificationError):
            gate.cargo_rows(b'{"reason":"build-finished","success":true,"success":false}\n', 0)

    def test_causal_roster_is_exact_and_controls_are_not_dropped(self):
        pair = gate.causal_pair()
        self.assertEqual(len(pair["failures"]), 6)
        self.assertEqual(len(gate.CAUSAL_CASES) - len(pair["failures"]), 5)
        raw = "".join(n + ": test\n" for n in gate.CAUSAL_CASES).encode()
        selected = gate.roster(raw, 0, gate.MLT_TESTS[0], gate.CAUSAL_CASES)
        self.assertEqual(selected["all"], tuple(gate.CAUSAL_CASES))

    def test_missing_mandatory_or_duplicate_enumeration_is_rejected(self):
        for names in (gate.CAUSAL_CASES[:-1], [*gate.CAUSAL_CASES, gate.CAUSAL_CASES[0]]):
            raw = "".join(n + ": test\n" for n in names).encode()
            with self.subTest(names=names), self.assertRaises(gate.custody.VerificationError):
                gate.roster(raw, 0, gate.MLT_TESTS[0], gate.CAUSAL_CASES)

    def test_empty_host_cannot_pass_as_zero_tests(self):
        with self.assertRaises(gate.custody.VerificationError):
            gate.roster(b"0 tests, 0 benchmarks\n", 0, gate.MLT_TESTS[1], gate.HOST_CASES)
        self.assertEqual(gate.roster(b"0 tests, 0 benchmarks\n", 0,
                                    gate.MLT_TESTS[-1])["all"], ())

    def test_all_six_causal_failures_and_five_controls_use_existing_observer(self):
        for name in gate.CAUSAL_CASES:
            with self.subTest(name=name):
                result = self.admit(name, "baseline")
                expected = "EXPECTED_CAUSAL_RED" if name in gate.FAILURE_SITES else "PASS"
                self.assertEqual(result["status"], expected)

    def test_all_eleven_green_cases_must_pass(self):
        for name in gate.CAUSAL_CASES:
            with self.subTest(name=name):
                self.assertEqual(self.admit(name, "repair")["status"], "PASS")

    def test_wrong_assertion_site_and_unrelated_panic_do_not_establish_red(self):
        name = next(iter(gate.FAILURE_SITES))
        for edit in (
            lambda raw, row: (raw.replace(b":1320:", b":1319:"), row),
            lambda raw, row: (raw.replace(b"source rejected=", b"unrelated="), row),
            lambda raw, row: (raw.replace(name.encode() + b"' panicked", b"other' panicked"), row),
        ):
            with self.subTest(edit=edit), self.assertRaises(gate.custody.VerificationError):
                self.admit(name, "baseline", edit=edit)

    def test_signal_timeout_ignored_and_duplicate_case_are_rejected(self):
        name = next(iter(gate.FAILURE_SITES))
        for edit in (
            lambda raw, row: (raw, {**row, "exit_code": -9}),
            lambda raw, row: (raw, {**row, "observation": "UNKNOWN_TIMEOUT_NO_RETRY"}),
            lambda raw, row: (raw.replace(b"0 ignored", b"1 ignored"), row),
            lambda raw, row: (raw + f"test {name} ... FAILED\n".encode(), row),
        ):
            with self.subTest(edit=edit), self.assertRaises(gate.custody.VerificationError):
                self.admit(name, "baseline", edit=edit)

    def test_source_drift_or_missing_case_prevents_role_completion(self):
        pair = gate.causal_pair()
        good = {"source_before": {"sha": gate.RED}, "source_after": {"sha": gate.RED},
                "results": [self.admit(n, "baseline") for n in gate.CAUSAL_CASES],
                "clippy": "NOT_SELECTED"}
        self.assertEqual(gate.observer.role_complete(pair, "baseline", good)["cases"], 11)
        for bad in ({**good, "source_after": {"sha": gate.GREEN}},
                    {**good, "results": good["results"][:-1]}):
            with self.assertRaises(gate.custody.VerificationError):
                gate.observer.role_complete(pair, "baseline", bad)

    @staticmethod
    def tap(names):
        count = len(names)
        return (f"TAP version 13\n" + "".join(f"# Subtest: {n}\nok {i+1} - {n}\n" for i, n in enumerate(names))
                + f"1..{count}\n# tests {count}\n# suites 0\n# pass {count}\n# fail 0\n"
                "# cancelled 0\n# skipped 0\n# todo 0\n# duration_ms 1\n").encode()

    def test_node_closed_names_and_summary_are_required(self):
        names = ("one source control", "another control")
        result = gate.node_tap(self.tap(names), 0, names, self.repo / "tests/control.test.mjs")
        self.assertEqual(result["cases"], 2)

    def test_node_skip_failure_duplicate_and_truncated_summary_are_rejected(self):
        names = ("source control",)
        raw = self.tap(names)
        for bad, code in ((raw.replace(b"# skipped 0", b"# skipped 1"), 0),
                          (raw.replace(b"ok 1 -", b"not ok 1 -"), 0),
                          (raw + b"ok 1 - source control\n", 0),
                          (raw.replace(b"# todo 0\n", b""), 0), (raw, 1)):
            with self.subTest(raw=bad, code=code), self.assertRaises(gate.custody.VerificationError):
                gate.node_tap(bad, code, names, self.repo / "tests/control.test.mjs")

    def test_node_file_level_mock_is_not_credited_as_individual_browser_cases(self):
        path = self.repo / "tests/mock.test.mjs"
        value = gate.node_tap(self.tap(("tests/mock.test.mjs",)), 0, ("<file>",), path)
        self.assertEqual(value["cases"], 1)
        with self.assertRaises(gate.custody.VerificationError):
            gate.node_tap(self.tap(("tests/other.test.mjs",)), 0, ("<file>",), path)

    def test_working_copy_matches_original_bytes_and_rejects_late_change(self):
        file = self.repo / "bound.txt"; file.write_bytes(b"same original")
        original = gate.custody.fingerprint(file, self.repo)
        snapshot = {"files": {"bound.txt": original}}
        destination = self.root / "copy"
        copied = gate.copy_source_tree(self.repo, snapshot, destination)
        self.assertEqual(copied["bound.txt"]["sha256"], original["sha256"])
        (destination / "bound.txt").write_bytes(b"changed later")
        with self.assertRaises(gate.custody.VerificationError):
            gate.copied_sources(destination, snapshot)

    def test_preexisting_working_copy_is_not_overwritten(self):
        destination = self.root / "copy"; destination.mkdir()
        sentinel = destination / "original"; sentinel.write_bytes(b"keep")
        with self.assertRaises(gate.custody.VerificationError):
            gate.copy_source_tree(self.repo, {"files": {}}, destination)
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_dependency_identity_detects_same_size_late_replacement(self):
        root = self.root / "dependencies"; root.mkdir()
        item = root / "module.js"; item.write_bytes(b"aaaa")
        before = gate.dependency_inventory(root)
        item.write_bytes(b"bbbb")
        after = gate.dependency_inventory(root)
        self.assertNotEqual(before["module.js"]["sha256"], after["module.js"]["sha256"])

    def test_runtime_selection_retains_all_five_files_and_twenty_node_cases(self):
        self.assertEqual(len(gate.NODE_CASES), 5)
        self.assertEqual(sum(len(names) for names in gate.NODE_CASES.values()), 20)
        self.assertEqual(len(gate.HOST_CASES), 14)
        self.assertEqual(len(gate.CANVAS_CASES), 2)
        self.assertEqual(gate.FIXTURES, ("hello-text", "semantic-complete"))

    @staticmethod
    def workflow_condition(expression, context):
        """Interpret the small boolean expression grammar used by these two jobs."""
        tree = ast.parse(expression.replace("&&", "and").replace("||", "or"), mode="eval")
        def value(node):
            if isinstance(node, ast.Expression):
                return value(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
                return node.value
            if isinstance(node, ast.Name) and node.id in ("github", "inputs"):
                return context[node.id]
            if isinstance(node, ast.Attribute):
                return value(node.value)[node.attr]
            if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
                observed = [bool(value(x)) for x in node.values]
                return all(observed) if isinstance(node.op, ast.And) else any(observed)
            if (isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1
                    and isinstance(node.ops[0], ast.Eq)):
                return value(node.left) == value(node.comparators[0])
            raise AssertionError("unexpected workflow condition construct")
        return bool(value(tree))

    def test_workflow_automatic_controls_and_manual_sdk_execution_remain_separate(self):
        workflow = (TOOLING.parent / gate.WORKFLOW).read_text()
        header = workflow.split("\npermissions:", 1)[0]
        expected_paths = {gate.WORKFLOW, gate.HELPER, gate.TEST,
                          "tooling/contracts/sdk-successor/*.json"}
        for event in ("pull_request", "push"):
            section = re.search(r"(?ms)^  " + event + r":\n(.*?)(?=^  [a-z_]+:|\Z)", header)
            self.assertIsNotNone(section)
            self.assertIn("    branches: [main]", section.group(1))
            paths = re.findall(r"(?m)^      - (.+)$", section.group(1))
            self.assertEqual(len(paths), 4)
            self.assertEqual(set(paths), expected_paths)
        conditions = {}
        for job in ("sdk-native-source-g0", "sdk-native-source"):
            section = workflow.split("\n  " + job + ":\n", 1)[1].split("\n  sdk-", 1)[0]
            conditions[job] = re.search(r"(?m)^    if: (.+)$", section).group(1)
        for event, ref, scope, expected in (
            ("pull_request", "refs/pull/12/merge", "", (True, False)),
            ("pull_request", "refs/pull/12/merge", "sdk_native_source", (True, False)),
            ("push", "refs/heads/main", "", (True, False)),
            ("push", "refs/heads/feature", "", (False, False)),
            ("workflow_dispatch", "refs/heads/main", "sdk_native_source", (True, True)),
            ("workflow_dispatch", "refs/heads/main", "sdk_native_baseline_diagnostic", (True, True)),
            ("workflow_dispatch", "refs/heads/main", "sdk_native_successor_source", (True, True)),
            ("workflow_dispatch", "refs/heads/main", "physical", (False, False)),
            ("workflow_dispatch", "refs/heads/main", "", (False, False)),
            ("schedule", "refs/heads/main", "sdk_native_source", (False, False)),
        ):
            with self.subTest(event=event, ref=ref, scope=scope):
                context = {"github": {"event_name": event, "ref": ref},
                           "inputs": {"verification_scope": scope}}
                observed = tuple(self.workflow_condition(condition, context)
                                 for condition in conditions.values())
                self.assertEqual(observed, expected)
        exact_head = "github.event.pull_request.head.sha || github.sha"
        self.assertEqual(workflow.count(exact_head), 2)
        self.assertIn("MW_G0_SOURCE_SHA: ${{ " + exact_head + " }}", workflow)
        self.assertIn("ref: ${{ " + exact_head + " }}", workflow)
        self.assertIn('--expected-source "$MW_G0_SOURCE_SHA"', workflow)
        self.assertIn("--require-complete", workflow)
        self.assertIn("node-version: '22.22.0'", workflow)
        self.assertIn(gate.GREEN, workflow)
        self.assertIn("/evidence/", workflow)
        self.assertNotIn("--ignored", workflow)
        self.assertNotIn("playwright install", workflow)
        self.assertNotIn("cargo run", workflow)

    def test_runner_temporary_directory_is_evaluated_only_at_step_scope(self):
        workflow = (TOOLING.parent / gate.WORKFLOW).read_text()

        def check_document(document):
            # GitHub resolves workflow/job env before a runner exists.
            # Step env is evaluated with runner context available.
            lines = document.splitlines()
            scopes = []
            for index, line in enumerate(lines):
                if line not in ("env:", "    env:"):
                    continue
                indent = len(line) - len(line.lstrip())
                content = []
                for child in lines[index + 1:]:
                    if child.strip() and len(child) - len(child.lstrip()) <= indent:
                        break
                    content.append(child)
                scopes.append("\n".join(content))
            for scope in scopes:
                self.assertNotRegex(scope, r"\$\{\{[^}]*\brunner(?:\.|\[)")

        check_document(workflow)
        binding = "          MW_SDK_G0_TEMP: ${{ runner.temp }}/sdk-native-source-controls"
        self.assertEqual(workflow.count(binding), 1)
        self.assertIn(
            "      - name: Verify observer controls through both entrypoints\n"
            "        env:\n" + binding + "\n", workflow)
        rejected = workflow.replace(
            "    env:\n", "    env:\n      MW_SDK_G0_TEMP: ${{ runner.temp }}/sdk-native-source-controls\n", 1)
        with self.assertRaises(AssertionError):
            check_document(rejected)

    def test_selected_mlt_integrations_preserve_all_52_cases_and_captions(self):
        counts = {item["name"]: len(item["closed_roster"]) for item in gate.MLT_INTEGRATIONS}
        self.assertEqual(counts, {"conformance": 17, "domain_contract": 4,
                                 "process": 22, "runtime_runner": 9})
        self.assertEqual(sum(counts.values()), 52)
        self.assertIn("native_caption_glyph_observer_is_read_only_and_closed_world",
                      gate.INTEGRATION_CASES["conformance"])
        self.assertIn("runtime_runner_renders_one_curated_profile_inside_scratch",
                      gate.INTEGRATION_CASES["runtime_runner"])
        self.assertEqual(len(gate.HOST_CASES), 14)
        argv = gate.integration_compile_argv("bound-cargo")
        names = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "--test"]
        self.assertEqual(names, list(gate.INTEGRATION_CASES))
        self.assertIn("--no-run", argv)
        self.assertIn("--message-format=json", argv)
        self.assertNotIn("--tests", argv)
        self.assertNotIn("--all-targets", argv)
        self.assertNotIn("--ignored", argv)
        self.assertEqual({item["source"] for item in gate.MLT_INTEGRATIONS}
                         | {"crates/driver-mlt-video/tests/common/mod.rs"},
                         set(gate.INTEGRATION_SOURCE_PINS))

    def test_integration_enumeration_rejects_a_missing_or_unselected_case(self):
        for selected in gate.MLT_INTEGRATIONS:
            expected = tuple(sorted(selected["closed_roster"]))
            raw = "".join(name + ": test\n" for name in expected).encode()
            with self.subTest(target=selected["name"]):
                result = gate.roster(raw, 0, selected)
                self.assertEqual(result["all"], expected)
            for changed in (expected[:-1], (*expected, "foreign_live_case"), ()):
                raw = "".join(name + ": test\n" for name in changed).encode()
                with self.subTest(target=selected["name"], changed=changed):
                    with self.assertRaises(gate.custody.VerificationError):
                        gate.roster(raw, 0, selected)

    def test_integration_compiler_bindings_require_every_exact_test_target(self):
        rows = []
        for selected in gate.MLT_INTEGRATIONS:
            binary = self.target / ("integration-" + selected["name"])
            binary.write_bytes(("unexecuted identity fixture " + selected["name"]).encode())
            row = self.cargo(selected)[0]
            row["executable"] = str(binary)
            rows.append(row)
        footer = {"reason": "build-finished", "success": True}
        bound = gate.select_binaries(self.raw([*rows, footer]), 0, self.repo, self.target,
                                     gate.MLT_INTEGRATIONS, testing=True)
        self.assertEqual(set(bound), set(gate.INTEGRATION_CASES))
        bad_kind = json.loads(json.dumps(rows))
        bad_kind[0]["target"]["kind"] = ["bin"]
        extra_live = json.loads(json.dumps(rows[0]))
        extra_live["target"]["name"] = "native_live"
        extra_live["target"]["src_path"] = str(self.repo / "crates/driver-mlt-video/tests/native_live.rs")
        for changed in (rows[:-1], bad_kind, [*rows, extra_live]):
            with self.subTest(changed=changed), self.assertRaises(gate.custody.VerificationError):
                gate.select_binaries(self.raw([*changed, footer]), 0, self.repo, self.target,
                                     gate.MLT_INTEGRATIONS, testing=True)

    def diagnostic_fixture(self, name, *, passed=None):
        pair, listed, context, row, raw = self.case(name, "baseline")
        if passed is not None and passed:
            _, _, _, pass_row, raw = self.case(name, "repair")
            row = {**pass_row, "context": context}
        return pair, listed, context, row, raw

    def diagnostic(self, name, *, passed=None):
        pair, listed, context, row, raw = self.diagnostic_fixture(name, passed=passed)
        return gate.diagnostic_observation(pair, name, raw, row, self.binary,
                                           self.binary, listed, context)

    def diagnostic_result(self):
        return {
            "source_before": {"sha": gate.RED}, "source_after": {"sha": gate.RED},
            "results": [self.diagnostic(name, passed=True) for name in gate.CAUSAL_CASES],
            "clippy": "NOT_SELECTED", "supplemental": {},
        }

    def test_diagnostic_scope_is_explicit_baseline_only_and_default_g1_is_unchanged(self):
        self.assertEqual(gate.execution_roles(gate.DIAGNOSTIC_SCOPE), (("baseline", gate.RED),))
        self.assertEqual(gate.execution_roles(gate.SOURCE_SCOPE),
                         (("baseline", gate.RED), ("repair", gate.GREEN)))
        environment = {**self.env, "MW_SDK_SCOPE": gate.DIAGNOSTIC_SCOPE}
        gate.execution_context(environment, "linux", scope=gate.DIAGNOSTIC_SCOPE)
        with self.assertRaises(gate.custody.VerificationError):
            gate.execution_context(environment, "linux")
        with self.assertRaises(gate.custody.VerificationError):
            gate.execution_context(self.env, "linux", scope=gate.DIAGNOSTIC_SCOPE)
        with self.assertRaises(gate.custody.VerificationError):
            gate.execution_roles("physical")
        self.assertEqual(len(gate.causal_pair()["failures"]), 6)

    def test_diagnostic_unexpected_pass_is_observed_without_rewriting_g1(self):
        name = "color::png_completion_tests::png_completion_rejects_corrupt_iend_crc"
        result = self.diagnostic(name, passed=True)
        self.assertEqual(result["outcome"], "OBSERVED_LIBTEST_PASS")
        self.assertEqual(result["status"], "DIAGNOSTIC_OBSERVATION_NOT_ACCEPTANCE")
        self.assertEqual(result["frozen_expected_exit"], 101)
        self.assertFalse(result["frozen_g1_comparison"]["matched"])
        self.assertFalse(result["g1_acceptance"])
        pair, listed, context, row, raw = self.diagnostic_fixture(name, passed=True)
        with self.assertRaises(gate.custody.VerificationError):
            gate.observer.admit_case(pair, "baseline", name, raw, row,
                                     self.binary, self.binary, listed, context)

    def test_diagnostic_different_assertion_retains_the_actual_marker_and_frozen_mismatch(self):
        name = gate.CAUSAL_CASES[8]
        pair, listed, context, row, raw = self.diagnostic_fixture(name)
        expected = pair["failures"][name]
        raw = raw.replace((":" + str(expected["line"]) + ":").encode(), b":2605:")
        raw = raw.replace(expected["markers"][0].encode(), b"shared deadline must expire")
        result = gate.diagnostic_observation(pair, name, raw, row, self.binary,
                                              self.binary, listed, context)
        self.assertEqual(result["outcome"], "OBSERVED_LIBTEST_FAILURE")
        self.assertFalse(result["frozen_g1_comparison"]["matched"])
        self.assertIn(":2605:", result["panics"][0][1])
        self.assertFalse(result["frozen_marker_observations"][0]["present"])
        self.assertEqual(result["combined_original_stream_sha256"],
                         gate.hashlib.sha256(raw).hexdigest())

    def test_diagnostic_does_not_promote_signal_skip_duplicate_or_missing_footer(self):
        name = gate.CAUSAL_CASES[0]
        pair, listed, context, row, raw = self.diagnostic_fixture(name)
        for changed, command in (
            (raw, {**row, "exit_code": -9}),
            (raw.replace(b"0 ignored", b"1 ignored"), row),
            (raw + f"test {name} ... FAILED\n".encode(), row),
            (raw.split(b"test result:")[0], row),
        ):
            with self.subTest(exit=command["exit_code"], raw=changed):
                observed = gate.diagnostic_observation(pair, name, changed, command,
                                                       self.binary, self.binary, listed, context)
                self.assertEqual(observed["outcome"], "NONCONFORMING_TEST_RESULT")
                result = self.diagnostic_result()
                result["results"][0] = observed
                with self.assertRaises(gate.custody.VerificationError):
                    gate.diagnostic_complete(result)

    def test_diagnostic_rejects_unknown_custody_source_command_binary_or_roster(self):
        name = gate.CAUSAL_CASES[0]
        pair, listed, context, row, raw = self.diagnostic_fixture(name)
        variants = (
            ({**row, "observation": "UNKNOWN_TIMEOUT_NO_RETRY"}, self.binary, listed, context),
            ({**row, "log_custody": "UNSEALED"}, self.binary, listed, context),
            ({**row, "argv": [*row["argv"], "--ignored"]}, self.binary, listed, context),
            (row, {**self.binary, "sha256": "0" * 64}, listed, context),
            (row, self.binary, {**listed, "selected": gate.CAUSAL_CASES[:-1]}, context),
            (row, self.binary, listed, {**context, "product_source_sha": gate.GREEN}),
        )
        for command, after, roster, selected_context in variants:
            with self.subTest(command=command), self.assertRaises(gate.custody.VerificationError):
                gate.diagnostic_observation(pair, name, raw, command, self.binary,
                                             after, roster, selected_context)

    def test_diagnostic_driver_observes_all_eleven_once_despite_frozen_g1_mismatches(self):
        pair, listed, context, _, _ = self.diagnostic_fixture(gate.CAUSAL_CASES[0])
        results, seen, originals = [], [], {}
        def command(argv, work, env, logs, label, timeout, ctx):
            name = argv[2]
            seen.append((name, label))
            _, _, _, row, raw = self.diagnostic_fixture(name, passed=True)
            originals[name] = raw
            return row
        def stream(logs, row, channel):
            return originals[row["argv"][2]] if channel == "stdout" else b""
        with mock.patch.object(gate.observer, "original_stream", side_effect=stream):
            gate.diagnostic_cases(self.binary, listed, pair, self.repo, self.target, {},
                                  self.root, context, results, {"fixture": self.binary}, command)
        self.assertEqual(seen, [(name, "diagnostic-" + str(i))
                                for i, name in enumerate(gate.CAUSAL_CASES)])
        self.assertEqual(len(results), 11)
        self.assertEqual(sum(not row["frozen_g1_comparison"]["matched"] for row in results), 6)
        with self.assertRaises(gate.custody.VerificationError):
            gate.diagnostic_cases(self.binary, listed, pair, self.repo, self.target, {},
                                  self.root, context, results, {"fixture": self.binary}, command)
        self.assertEqual(len(seen), 11)

    def test_diagnostic_driver_stops_on_unknown_without_replaying_or_continuing(self):
        pair, listed, context, _, _ = self.diagnostic_fixture(gate.CAUSAL_CASES[0])
        results, seen, originals = [], [], {}
        def command(argv, work, env, logs, label, timeout, ctx):
            name = argv[2]; seen.append(name)
            if len(seen) == 3:
                raise gate.observer.UnknownExecution("synthetic unknown; not a real SDK process")
            _, _, _, row, raw = self.diagnostic_fixture(name, passed=True)
            originals[name] = raw
            return row
        def stream(logs, row, channel):
            return originals[row["argv"][2]] if channel == "stdout" else b""
        with mock.patch.object(gate.observer, "original_stream", side_effect=stream):
            with self.assertRaises(gate.observer.UnknownExecution):
                gate.diagnostic_cases(self.binary, listed, pair, self.repo, self.target, {},
                                      self.root, context, results, {"fixture": self.binary}, command)
        self.assertEqual(seen, list(gate.CAUSAL_CASES[:3]))
        self.assertEqual([row["name"] for row in results], list(gate.CAUSAL_CASES[:2]))

    def test_diagnostic_completion_preserves_source_and_forbids_other_suites(self):
        good = self.diagnostic_result()
        summary = gate.diagnostic_complete(good)
        self.assertEqual(summary["status"], "DIAGNOSTIC_NOT_ACCEPTANCE")
        self.assertEqual(summary["cases"], 11)
        self.assertEqual(summary["green"], "NOT_RUN")
        self.assertFalse(summary["g1_acceptance"])
        for bad in (
            {**good, "source_after": {"sha": gate.GREEN}},
            {**good, "results": good["results"][:-1]},
            {**good, "results": [*good["results"][:-1], good["results"][0]]},
            {**good, "clippy": "PASS"},
            {**good, "supplemental": {"mlt-integrations": {}}},
            {**good, "runtime": {}},
        ):
            with self.subTest(result=bad), self.assertRaises(gate.custody.VerificationError):
                gate.diagnostic_complete(bad)

    def test_diagnostic_workflow_is_manual_and_node_execution_stays_source_only(self):
        workflow = (TOOLING.parent / gate.WORKFLOW).read_text()
        self.assertIn("options: [sdk_native_source, sdk_native_baseline_diagnostic, sdk_native_successor_source]", workflow)
        self.assertIn("default: sdk_native_source", workflow)
        self.assertIn("MW_SDK_SCOPE: "+"$"+"{{ inputs.verification_scope }}", workflow)
        self.assertIn('--scope "$MW_SDK_SCOPE"', workflow)
        self.assertIn("uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020\n"
                      "        if: inputs.verification_scope == 'sdk_native_source'\n", workflow)
        job = workflow.split("\n  sdk-native-source:\n", 1)[1]
        condition = re.search(r"(?m)^    if: (.+)$", job).group(1)
        for event in ("pull_request", "push", "schedule"):
            context = {"github": {"event_name": event, "ref": "refs/heads/main"},
                       "inputs": {"verification_scope": gate.DIAGNOSTIC_SCOPE}}
            self.assertFalse(self.workflow_condition(condition, context))

    def test_diagnostic_cli_success_cannot_be_a_g1_acceptance_status(self):
        args = ["--gate", str(self.repo), "--sdk", str(self.root / "sdk"),
                "--output", str(self.root / "output"), "--scope", gate.DIAGNOSTIC_SCOPE]
        for status, code in (("DIAGNOSTIC_NOT_ACCEPTANCE", 0), ("PASS_SOURCE_G1", 2),
                             ("UNKNOWN_NO_RETRY", 2), ("FAIL_OR_INCOMPLETE", 2)):
            with self.subTest(status=status), mock.patch.object(gate, "run", return_value={"status": status}) as run:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(gate.main(args), code)
                self.assertEqual(run.call_args.kwargs, {"scope": gate.DIAGNOSTIC_SCOPE})


    def observed_assertion_fixture(self, name, source, line, message, *, column=9):
        pair, listed, context, row, _ = self.case(name, "baseline")
        row = {**row, "exit_code": 101}
        raw = (f"running 1 test\ntest {name} ... FAILED\n\n"
               f"thread '{name}' panicked at {source}:{line}:{column}:\n{message}\n\n"
               f"test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; "
               f"{len(listed['all'])-1} filtered out; finished in 0.01s\n").encode()
        return pair, listed, context, row, raw

    def test_missing_iend_remains_an_existing_baseline_pass_control(self):
        name = "color::png_completion_tests::png_completion_rejects_missing_iend"
        self.assertIn(name, gate.CAUSAL_CASES)
        self.assertNotIn(name, gate.causal_pair()["failures"])
        self.assertEqual(self.admit(name, "baseline")["status"], "PASS")
        self.assertEqual(self.admit(name, "repair")["status"], "PASS")
        observed = self.diagnostic(name, passed=True)
        self.assertEqual(observed["frozen_expected_exit"], 0)
        self.assertTrue(observed["frozen_g1_comparison"]["matched"])
        self.assertFalse(observed["g1_acceptance"])

    def test_missing_iend_failure_cannot_be_credited_as_a_new_causal_red(self):
        name = "color::png_completion_tests::png_completion_rejects_missing_iend"
        pair, listed, context, row, raw = self.observed_assertion_fixture(
            name, "crates/driver-mlt-video/src/color.rs", 1320,
            "source rejected=false, decoded rejected=false")
        with self.assertRaises(gate.custody.VerificationError):
            gate.observer.admit_case(pair, "baseline", name, raw, row,
                                     self.binary, self.binary, listed, context)

    def test_shared_deadline_requires_the_observed_probe_completion_assertion(self):
        name = "runtime::native_closure_tests::output_probe_cannot_complete_past_the_shared_deadline"
        pair, listed, context, row, raw = self.observed_assertion_fixture(
            name, "crates/driver-mlt-video/src/runtime.rs", 2610,
            "the media probe completed after the shared deadline")
        self.assertEqual(pair["failures"][name]["line"], 2610)
        result = gate.observer.admit_case(pair, "baseline", name, raw, row,
                                          self.binary, self.binary, listed, context)
        self.assertEqual(result["status"], "EXPECTED_CAUSAL_RED")
        observed = gate.diagnostic_observation(pair, name, raw, row, self.binary,
                                                self.binary, listed, context)
        self.assertTrue(observed["frozen_g1_comparison"]["matched"])
        self.assertTrue(all(item["present"] for item in observed["frozen_marker_observations"]))
        self.assertFalse(observed["g1_acceptance"])

    def test_shared_deadline_rejects_the_old_site_marker_or_an_unrelated_probe_failure(self):
        name = "runtime::native_closure_tests::output_probe_cannot_complete_past_the_shared_deadline"
        for line, column, message in (
            (2605, 9, "shared deadline must expire"),
            (2605, 9, "the media probe completed after the shared deadline"),
            (2610, 9, "shared deadline must expire"),
            (2610, 9, "unrelated probe failed before completion"),
            (2610, 8, "the media probe completed after the shared deadline"),
        ):
            with self.subTest(line=line, column=column, message=message):
                pair, listed, context, row, raw = self.observed_assertion_fixture(
                    name, "crates/driver-mlt-video/src/runtime.rs", line, message, column=column)
                with self.assertRaises(gate.custody.VerificationError):
                    gate.observer.admit_case(pair, "baseline", name, raw, row,
                                             self.binary, self.binary, listed, context)


    def test_empty_terse_helpers_keep_zero_credit_and_verbose_support(self):
        empty_specs = [selected for selected in gate.MLT_TESTS if selected["allow_empty"]]
        self.assertTrue(empty_specs)
        expected = {"all": (), "selected": (), "not_selected": (),
                    "not_selected_credit": "NONE"}
        for index, selected in enumerate(empty_specs):
            for raw in (b"", b"0 tests, 0 benchmarks\n"):
                with self.subTest(spec=index, raw=raw):
                    self.assertEqual(gate.roster(raw, 0, selected), expected)

    def test_empty_terse_ordinary_target_is_rejected(self):
        selected = gate.MLT_TESTS[1]
        self.assertFalse(selected["allow_empty"])
        with self.assertRaisesRegex(gate.custody.VerificationError,
                                    "selected target has no enumerated tests"):
            gate.roster(b"", 0, selected)

    def test_allow_empty_cannot_bypass_required_cases(self):
        selected = gate.MLT_TESTS[-1]
        self.assertTrue(selected["allow_empty"])
        self.assertTrue(gate.HOST_CASES)
        for raw in (b"", b"0 tests, 0 benchmarks\n"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(gate.custody.VerificationError,
                                            "selected target has no enumerated tests"):
                    gate.roster(raw, 0, selected, gate.HOST_CASES)

    def test_allow_empty_cannot_bypass_nonempty_closed_roster(self):
        selected = {**gate.MLT_TESTS[-1], "closed_roster": ("required_case",)}
        self.assertTrue(selected["allow_empty"])
        for raw in (b"", b"0 tests, 0 benchmarks\n"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(gate.custody.VerificationError,
                                            "selected target has no enumerated tests"):
                    gate.roster(raw, 0, selected)

    def test_empty_terse_nonzero_exit_is_rejected(self):
        selected = gate.MLT_TESTS[-1]
        self.assertTrue(selected["allow_empty"])
        for code in (1, 2):
            with self.subTest(code=code):
                with self.assertRaises(gate.custody.VerificationError):
                    gate.roster(b"", code, selected)

    def test_empty_terse_does_not_admit_whitespace_or_malformed_output(self):
        selected = gate.MLT_TESTS[-1]
        self.assertTrue(selected["allow_empty"])
        for raw in (b" ", b"\t", b"\n", b"\r\n", b"\x00",
                    b"unexpected output\n", b"0 tests, 1 benchmark\n",
                    b"0 tests, 0 benchmarks\nunexpected output\n"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(gate.custody.VerificationError,
                                            "selected target has no enumerated tests"):
                    gate.roster(raw, 0, selected)



class SuccessorSourceTests(unittest.TestCase):
    """Synthetic source contracts only; these fixtures are not an SDK execution."""

    setUp = SourceGateTests.setUp
    cargo = SourceGateTests.cargo
    raw = staticmethod(SourceGateTests.raw)

    @staticmethod
    def manifest_bytes(value):
        import hashlib
        raw = json.dumps(value, separators=(",", ":")).encode()
        return raw, hashlib.sha256(raw).hexdigest()

    def manifest(self):
        value = {
            "schema": gate.SUCCESSOR_SCHEMA, "sdk_repository": "seradotcom/semwright",
            "sdk_sha": gate.SUCCESSOR_SHA, "sdk_tree": gate.SUCCESSOR_TREE,
            "gate_verifier_pins": {name: "a" * 64 for name in gate.GATE_FILES},
            "sdk_source_pins": {"synthetic.txt": "b" * 64},
            "rust_toolchain": "1.98.1", "node_version": "24.0.0",
            "rust_rosters": {gate.successor_target_key(s): ([] if s["allow_empty"] else ["fixture_case"])
                             for s in gate.successor_specs()},
            "node_cases": {"fixture.test.mjs": ["<file>"]},
            "catalog_capabilities": 1, "fixture_count": 1, "fixtures": list(gate.FIXTURES),
        }
        value["rust_rosters"][gate.successor_target_key(gate.FIXEDTXT_V4)] = list(gate.FIXEDTXT_V4_CASES)
        value["sdk_source_pins"] = {name: "b" * 64 for name in gate.successor_required_pins(value)}
        return value

    def admit_shape(self, value):
        return gate.successor_manifest_shape(*self.manifest_bytes(value))

    def repository_fixture(self):
        import hashlib
        value = self.manifest()
        gate_data = {p: ("synthetic gate fixture " + p).encode() for p in gate.GATE_FILES}
        sdk_data = {p: ("synthetic SDK fixture " + p).encode() for p in value["sdk_source_pins"]}
        sdk_data[gate.RUNTIME + "/package.json"] = json.dumps({"engines": {"node": "24.0.0"}}).encode()
        sdk_data[gate.CATALOG_FILES[0]] = b'[{"descriptor":{"name":"synthetic"}}]'
        sdk_data[gate.CATALOG_FILES[-1]] = json.dumps({"synthetic.json": "a" * 64}).encode()
        value["gate_verifier_pins"] = {p: hashlib.sha256(b).hexdigest() for p, b in gate_data.items()}
        value["sdk_source_pins"] = {p: hashlib.sha256(b).hexdigest() for p, b in sdk_data.items()}
        path = gate.MANIFEST_PREFIX + "synthetic-source-control.json"
        raw, digest = self.manifest_bytes(value)
        target = self.repo / path; target.parent.mkdir(parents=True); target.write_bytes(raw)
        gate_data[path] = raw
        sdk = self.root / "sdk"
        context = {"GITHUB_SHA": "1" * 40}
        def git(repo, *args):
            if repo == self.repo:
                self.assertEqual(args[0], "show")
                sha, name = args[1].split(":", 1)
                self.assertEqual(sha, context["GITHUB_SHA"])
                return gate_data[name]
            self.assertEqual(repo, sdk)
            if args[0] == "rev-parse":
                self.assertEqual(args[1], gate.SUCCESSOR_SHA + "^{tree}")
                return (gate.SUCCESSOR_TREE + "\n").encode()
            if args[0] == "ls-tree":
                self.assertEqual(args[-1], gate.SUCCESSOR_SHA)
                return b"\0".join(p.encode() for p in sdk_data) + b"\0"
            self.assertEqual(args[0], "show")
            sha, name = args[1].split(":", 1)
            self.assertEqual(sha, gate.SUCCESSOR_SHA)
            return sdk_data[name]
        return value, sdk, path, digest, context, git, gate_data, sdk_data

    def test_successor_shape_accepts_only_its_source_contract_and_no_execution_credit(self):
        admitted = self.admit_shape(self.manifest())
        self.assertEqual(admitted["sdk_sha"], gate.SUCCESSOR_SHA)
        self.assertNotIn("PASS", json.dumps(admitted))
        self.assertNotIn(gate.SUCCESSOR_SHA, (gate.BASE, gate.RED, gate.GREEN))

    def test_successor_shape_rejects_historical_or_other_sha_and_wrong_tree(self):
        for sha in (gate.BASE, gate.RED, gate.GREEN,
                    "3384f785f42f4c51e06bdf9c39e23c7c5ed7227a",
                    "12084b1316b4d931aaf3dd917aa2a0f4f8c2303a", "f" * 40):
            with self.subTest(sha=sha), self.assertRaises(gate.custody.VerificationError):
                self.admit_shape({**self.manifest(), "sdk_sha": sha})
        with self.assertRaises(gate.custody.VerificationError):
            self.admit_shape({**self.manifest(), "sdk_tree": "f" * 40})

    def test_successor_shape_rejects_unknown_fields_and_injected_acceptance(self):
        for key in ("status", "human_acceptance", "reused_pass", "commands", "environment"):
            with self.subTest(key=key), self.assertRaises(gate.custody.VerificationError):
                self.admit_shape({**self.manifest(), key: True})

    def test_successor_shape_rejects_missing_keys_digest_and_duplicate_json(self):
        value = self.manifest(); del value["rust_rosters"]
        with self.assertRaises(gate.custody.VerificationError): self.admit_shape(value)
        raw, digest = self.manifest_bytes(self.manifest())
        with self.assertRaises(gate.custody.VerificationError):
            gate.successor_manifest_shape(raw, "0" * 64)
        import hashlib
        duplicate = raw[:-1] + b',"sdk_sha":"' + gate.SUCCESSOR_SHA.encode() + b'"}'
        with self.assertRaises(gate.custody.VerificationError):
            gate.successor_manifest_shape(duplicate, hashlib.sha256(duplicate).hexdigest())

    def test_successor_shape_rejects_zero_boolean_duplicate_and_omitted_rosters(self):
        selected = next(s for s in gate.successor_specs() if not s["allow_empty"])
        key = gate.successor_target_key(selected)
        for names in ([], [True], ["fixture_case", "fixture_case"], ["../test"]):
            value = self.manifest(); value["rust_rosters"][key] = names
            with self.subTest(names=names), self.assertRaises(gate.custody.VerificationError):
                self.admit_shape(value)
        value = self.manifest(); del value["rust_rosters"][key]
        with self.assertRaises(gate.custody.VerificationError): self.admit_shape(value)

    def test_successor_shape_rejects_unsafe_pins_unknown_verifier_and_version_range(self):
        for path in ("../Cargo.lock", "/Cargo.lock", "C:\\Cargo.lock", "tooling//x", "tooling/./x"):
            value = self.manifest(); value["sdk_source_pins"][path] = "a" * 64
            with self.subTest(path=path), self.assertRaises(gate.custody.VerificationError):
                self.admit_shape(value)
        value = self.manifest(); value["gate_verifier_pins"]["unknown.py"] = "a" * 64
        with self.assertRaises(gate.custody.VerificationError): self.admit_shape(value)
        for version in ("24", ">=24", "24.0.0\nfield=value", True):
            with self.subTest(version=version), self.assertRaises(gate.custody.VerificationError):
                self.admit_shape({**self.manifest(), "node_version": version})

    def test_successor_shape_rejects_node_empty_duplicate_and_inventory_bool(self):
        for cases in ({}, {"fixture.test.mjs": []}, {"fixture.test.mjs": ["x", "x"]},
                      {"fixture.test.mjs": ["<file>", "x"]}, {"../fixture.test.mjs": ["x"]}):
            with self.subTest(cases=cases), self.assertRaises(gate.custody.VerificationError):
                self.admit_shape({**self.manifest(), "node_cases": cases})
        for count in (0, -1, True, 1.0):
            with self.subTest(count=count), self.assertRaises(gate.custody.VerificationError):
                self.admit_shape({**self.manifest(), "fixture_count": count})

    def test_successor_manifest_is_bound_to_tracked_bytes_and_both_exact_sources(self):
        value, sdk, path, digest, context, git, _, _ = self.repository_fixture()
        with mock.patch.object(gate.custody, "git", side_effect=git):
            admitted, receipt = gate.admit_successor_manifest(self.repo, sdk, path, digest, context)
        self.assertEqual(admitted, value)
        self.assertEqual(receipt["gate_source_sha"], context["GITHUB_SHA"])
        self.assertEqual(receipt["sdk_source_sha"], gate.SUCCESSOR_SHA)
        self.assertEqual(receipt["admission"], "SOURCE_CONTRACT_ONLY_NOT_EXECUTION")

    def test_successor_manifest_rejects_untracked_or_unsafe_manifest_location(self):
        for path in (None, "contract.json", "tooling/contracts/sdk-successor/../x.json",
                     "tooling/contracts/sdk-successor/nested/x.json", "C:\\manifest.json"):
            with self.subTest(path=path), mock.patch.object(gate.custody, "git") as git:
                with self.assertRaises(gate.custody.VerificationError):
                    gate.admit_successor_manifest(self.repo, self.root, path, "a" * 64,
                                                  {"GITHUB_SHA": "1" * 40})
                git.assert_not_called()

    def test_successor_manifest_rejects_changed_working_bytes_and_wrong_tree(self):
        value, sdk, path, digest, context, git, _, _ = self.repository_fixture()
        original = (self.repo / path).read_bytes()
        (self.repo / path).write_bytes(original + b" ")
        with mock.patch.object(gate.custody, "git", side_effect=git):
            with self.assertRaisesRegex(gate.custody.VerificationError, "working manifest differs"):
                gate.admit_successor_manifest(self.repo, sdk, path, digest, context)
        (self.repo / path).write_bytes(original)
        def changed(repo, *args):
            return b"f" * 40 if args[0] == "rev-parse" else git(repo, *args)
        with mock.patch.object(gate.custody, "git", side_effect=changed):
            with self.assertRaisesRegex(gate.custody.VerificationError, "Git tree differs"):
                gate.admit_successor_manifest(self.repo, sdk, path, digest, context)

    def test_successor_manifest_rejects_modified_sdk_and_gate_pin(self):
        value, sdk, path, digest, context, git, gate_data, sdk_data = self.repository_fixture()
        for entries, key in ((sdk_data, "Cargo.lock"), (gate_data, gate.HELPER)):
            original = entries[key]; entries[key] = original + b"changed"
            with self.subTest(path=key), mock.patch.object(gate.custody, "git", side_effect=git):
                with self.assertRaisesRegex(gate.custody.VerificationError, "exact source pin differs"):
                    gate.admit_successor_manifest(self.repo, sdk, path, digest, context)
            entries[key] = original

    def test_successor_manifest_rejects_omitted_node_test_before_compilation(self):
        _, sdk, path, digest, context, git, _, sdk_data = self.repository_fixture()
        sdk_data[gate.RUNTIME + "/tests/omitted.test.mjs"] = b"synthetic unlisted test"
        with mock.patch.object(gate.custody, "git", side_effect=git):
            with self.assertRaisesRegex(gate.custody.VerificationError, "Node test files were omitted"):
                gate.admit_successor_manifest(self.repo, sdk, path, digest, context)

    def test_successor_required_pins_include_runtime_authoring_fonts_and_coverage(self):
        pins = gate.successor_required_pins(self.manifest())
        expected = {
            ".gitattributes", gate.RUNTIME + "/render.mjs", gate.RUNTIME + "/probe.mjs",
            gate.RUNTIME + "/tools/semantic-coverage.mjs", gate.RUNTIME + "/stub-editor/main.js",
            "integrations/composition/motion/native.ts",
            "crates/driver-motion-canvas/src/authoring.rs",
            "crates/driver-motion-canvas/src/compiler.rs",
            "crates/driver-motion-canvas/src/fonts.rs",
            "crates/driver-motion-canvas/src/renderer.rs",
            "docs/motion-canvas/API_COVERAGE.json",
            "docs/motion-canvas/CORE_API_COVERAGE.json",
            "docs/motion-canvas/AUX_API_COVERAGE.json",
        }
        self.assertEqual(set(gate.SUCCESSOR_RUNTIME_INPUTS), expected)
        self.assertTrue(expected <= pins)

    def test_successor_rejects_missing_runtime_pin_before_compilation(self):
        value, sdk, path, _, context, git, gate_data, _ = self.repository_fixture()
        del value["sdk_source_pins"]["integrations/composition/motion/native.ts"]
        raw, digest = self.manifest_bytes(value)
        gate_data[path] = raw
        (self.repo / path).write_bytes(raw)
        with mock.patch.object(gate.custody, "git", side_effect=git), \
             mock.patch.object(gate, "prepare_runtime") as prepare:
            with self.assertRaisesRegex(gate.custody.VerificationError, "SDK pin closure"):
                gate.admit_successor_manifest(self.repo, sdk, path, digest, context)
        prepare.assert_not_called()

    def test_successor_rejects_runtime_bytes_changed_under_self_consistent_test_roster(self):
        _, sdk, path, digest, context, git, _, sdk_data = self.repository_fixture()
        sdk_data[gate.RUNTIME + "/render.mjs"] += b"changed source fixture"
        with mock.patch.object(gate.custody, "git", side_effect=git):
            with self.assertRaisesRegex(gate.custody.VerificationError, "exact source pin differs"):
                gate.admit_successor_manifest(self.repo, sdk, path, digest, context)

    def test_successor_roster_is_exact_and_never_inherits_historical_cases(self):
        selected = gate.MLT_TESTS[0]; value = self.manifest()
        good = b"fixture_case: test\n"
        result = gate.successor_roster(good, 0, selected, value)
        self.assertEqual(result["all"], ("fixture_case",))
        for raw in (good + b"unplanned_case: test\n", b"other_case: test\n", good + good, b""):
            with self.subTest(raw=raw), self.assertRaises(gate.custody.VerificationError):
                gate.successor_roster(raw, 0, selected, value)

    def test_successor_scope_keeps_historical_execution_roles_unchanged(self):
        self.assertEqual(gate.execution_roles(gate.SUCCESSOR_SCOPE), (("successor", gate.SUCCESSOR_SHA),))
        self.assertEqual(gate.execution_roles(gate.SOURCE_SCOPE), (("baseline", gate.RED), ("repair", gate.GREEN)))
        self.assertEqual(gate.execution_roles(gate.DIAGNOSTIC_SCOPE), (("baseline", gate.RED),))

    def test_successor_missing_manifest_fails_without_producer_or_historical_pair(self):
        env = {**self.env, "MW_SDK_SCOPE": gate.SUCCESSOR_SCOPE}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(gate.sys, "platform", "linux"), \
             mock.patch.object(gate.observer, "__file__", str(self.repo / "tooling/ci_foundation_causal.py")), \
             mock.patch.object(gate.custody, "__file__", str(self.repo / "tooling/ci_storage_apply_recovery.py")), \
             mock.patch.object(gate.observer, "command") as commands, \
             mock.patch.object(gate, "verify_pair") as historical:
            report = gate.run(self.repo, self.root / "sdk", self.root / "successor-missing", scope=gate.SUCCESSOR_SCOPE)
        self.assertEqual(report["status"], "FAIL_OR_INCOMPLETE")
        self.assertFalse(report["historical_evidence_reused"])
        self.assertEqual(report["roles"], {})
        commands.assert_not_called(); historical.assert_not_called()

    def test_successor_unknown_is_retained_without_retry_or_pass(self):
        env = {**self.env, "MW_SDK_SCOPE": gate.SUCCESSOR_SCOPE}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(gate.sys, "platform", "linux"), \
             mock.patch.object(gate.observer, "__file__", str(self.repo / "tooling/ci_foundation_causal.py")), \
             mock.patch.object(gate.custody, "__file__", str(self.repo / "tooling/ci_storage_apply_recovery.py")), \
             mock.patch.object(gate, "admit_successor_manifest",
                               side_effect=gate.observer.UnknownExecution("synthetic uncertainty")) as admission, \
             mock.patch.object(gate.observer, "command") as commands:
            report = gate.run(self.repo, self.root / "sdk", self.root / "successor-unknown", scope=gate.SUCCESSOR_SCOPE)
        self.assertEqual(report["status"], "UNKNOWN_NO_RETRY")
        self.assertEqual(admission.call_count, 1); commands.assert_not_called()
        self.assertEqual(report["new_native_operations"], 0)

    def test_successor_cli_does_not_accept_historical_or_admission_only_status(self):
        args = ["--gate", str(self.repo), "--sdk", str(self.root / "sdk"),
                "--output", str(self.root / "output"), "--scope", gate.SUCCESSOR_SCOPE,
                "--successor-manifest", gate.MANIFEST_PREFIX + "reviewed.json",
                "--successor-manifest-sha256", "a" * 64]
        for status, code in (("PASS_SUCCESSOR_SOURCE_G1", 0), ("PASS_SOURCE_G1", 2),
                             ("SOURCE_CONTRACT_ONLY_NOT_EXECUTION", 2), ("UNKNOWN_NO_RETRY", 2)):
            with self.subTest(status=status), mock.patch.object(gate, "run", return_value={"status": status}):
                with redirect_stdout(io.StringIO()): self.assertEqual(gate.main(args), code)

    def test_historical_scope_rejects_successor_manifest_inputs(self):
        with self.assertRaisesRegex(gate.custody.VerificationError, "cannot alter a historical"):
            gate.run(self.repo, self.root, self.root / "unused", scope=gate.SOURCE_SCOPE,
                     successor_manifest=gate.MANIFEST_PREFIX + "reviewed.json")

    def test_successor_workflow_is_manual_and_freezes_source_without_shell_interpolation(self):
        workflow = (TOOLING.parent / gate.WORKFLOW).read_text()
        self.assertIn(gate.SUCCESSOR_SHA, workflow)
        self.assertIn("default: sdk_native_source", workflow)
        self.assertIn("MW_SDK_SUCCESSOR_MANIFEST: ${{ inputs.successor_manifest_path }}", workflow)
        self.assertIn('"${successor_args[@]}"', workflow)
        job = workflow.split("\n  sdk-native-source:\n", 1)[1]
        condition = re.search(r"(?m)^    if: (.+)$", job).group(1)
        for event in ("pull_request", "push", "schedule"):
            context = {"github": {"event_name": event, "ref": "refs/heads/main"},
                       "inputs": {"verification_scope": gate.SUCCESSOR_SCOPE}}
            self.assertFalse(SourceGateTests.workflow_condition(condition, context))

    def orchestrated_successor(self, *, unknown=False, source_drift=False):
        """Run the real coordinator against explicit fake producers and original streams."""
        manifest = self.manifest(); events = []; source_reads = 0; paths_to_names = {}; streams = {}
        env = {**self.env, "MW_SDK_SCOPE": gate.SUCCESSOR_SCOPE}
        output = self.root / "synthetic-successor-coordinator"
        def snapshot(work, source, paths, tree=None):
            nonlocal source_reads
            if work == self.repo:
                return {"sha": source, "files": {p: {"sha256": h} for p, h in gate.GATE_PINS.items()}}
            self.assertEqual(source, gate.SUCCESSOR_SHA); self.assertEqual(tree, gate.SUCCESSOR_TREE)
            source_reads += 1
            return {"sha": source, "tree": tree, "files": {},
                    "fixture_generation": 2 if source_drift and source_reads == 3 else 1}
        def prepare(role, cargo, work, environment, logs, ctx, target, command):
            self.assertEqual(role, "repair"); self.assertEqual(ctx["product_source_sha"], gate.SUCCESSOR_SHA)
            self.assertEqual(command.phase, "PREPARING"); events.append("prepare-current")
            return {"steps": ["synthetic-compilation"], "clippy": "PASS", "collections": {
                name: {} for name in ("test-tools", "mlt-tests", "mlt-integrations", "video-domain",
                                      "motion-canvas", "materializer", "fixedtxt-v4")}}
        def runtime(*args, **kwargs):
            self.assertEqual(kwargs["node_version"], manifest["node_version"])
            events.append("prepare-node"); return {}
        def seal(prepared, work, target, logs, evidence, barrier):
            self.assertEqual(barrier.phase, "SEALING"); events.append("seal-current")
            result = {}
            for collection, specs in (("test-tools", gate.MLT_TESTS[1:]), ("mlt-tests", gate.MLT_TESTS),
                                      ("mlt-integrations", gate.MLT_INTEGRATIONS),
                                      ("video-domain", gate.VIDEO_TESTS), ("motion-canvas", gate.MC_TESTS),
                                      ("materializer", (gate.MATERIALIZE,)),
                                      ("fixedtxt-v4", (gate.FIXEDTXT_V4,))):
                result[collection] = {}
                for selected in specs:
                    path = target / (collection + "-" + selected["name"])
                    path.write_bytes(b"synthetic unused binary fixture")
                    result[collection][selected["name"]] = {"absolute_path": str(path),
                                                           **gate.custody.fingerprint(path, target)}
                    paths_to_names[str(path)] = manifest["rust_rosters"].get(gate.successor_target_key(selected), [])
            prepared["sealed"] = result
            return result
        def command(argv, work, environment, logs, label, timeout, ctx, observations):
            self.assertEqual(ctx["product_source_sha"], gate.SUCCESSOR_SHA)
            if argv[0] == "git":
                self.assertEqual(argv[-1], gate.SUCCESSOR_SHA); events.append("checkout-current")
            elif "--list" in argv:
                self.assertIn("seal-current", events); events.append("list-current")
                if unknown: raise gate.observer.UnknownExecution("synthetic list uncertainty")
                streams[label] = "".join(n + ": test\n" for n in paths_to_names[argv[0]]).encode()
            else:
                self.assertIn("--exact", argv); events.append("case-current")
                name = argv[2]
                streams[label] = (f"running 1 test\ntest {name} ... ok\n"
                                  "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; "
                                  f"{len(paths_to_names[argv[0]]) - 1} filtered out; finished in 0.01s\n").encode()
            return {"exit_code": 0, "argv": argv, "context": ctx, "label": label,
                    "observation": "DIRECT_PROCESS_EXIT_OBSERVED", "log_custody": "VERIFIED_SNAPSHOT"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(gate.sys, "platform", "linux"), \
             mock.patch.object(gate.observer, "__file__", str(self.repo / "tooling/ci_foundation_causal.py")), \
             mock.patch.object(gate.custody, "__file__", str(self.repo / "tooling/ci_storage_apply_recovery.py")), \
             mock.patch.object(gate, "admit_successor_manifest", return_value=(manifest, {"synthetic": True})), \
             mock.patch.object(gate.custody, "source_snapshot", side_effect=snapshot), \
             mock.patch.object(gate.observer, "retain_source", return_value={}), \
             mock.patch.object(gate.observer, "tools_identity", return_value={"cargo": self.binary}), \
             mock.patch.object(gate.observer, "command", side_effect=command), \
             mock.patch.object(gate.observer, "original_stream",
                               side_effect=lambda logs, row, channel: streams.get(row["label"], b"")
                               if channel == "stdout" else b""), \
             mock.patch.object(gate, "input_paths", return_value=[]), \
             mock.patch.object(gate, "prepare_successor_compiled_role", side_effect=prepare), \
             mock.patch.object(gate, "prepare_runtime", side_effect=runtime), \
             mock.patch.object(gate, "seal_compiled_role", side_effect=seal), \
             mock.patch.object(gate, "run_runtime", return_value={"status": "SYNTHETIC_CONTROL_ONLY"}), \
             mock.patch.object(gate, "verify_commands"), \
             mock.patch.object(gate, "verify_pair") as old_pair:
            report = gate.run(self.repo, self.root / "sdk", output, scope=gate.SUCCESSOR_SCOPE,
                              successor_manifest=gate.MANIFEST_PREFIX + "synthetic.json",
                              successor_manifest_sha256="a" * 64)
        old_pair.assert_not_called()
        return report, events


    def test_fixedtxt_v4_is_only_in_the_current_successor_target_selection(self):
        self.assertEqual(gate.FIXEDTXT_V4["kind"], "example")
        self.assertEqual(gate.FIXEDTXT_V4["source"],
                         "crates/driver-motion-canvas/examples/fixed_text_compile_v4.rs")
        self.assertEqual(len(gate.FIXEDTXT_V4_CASES), 6)
        self.assertEqual(len(set(gate.FIXEDTXT_V4_CASES)), 6)
        self.assertEqual(len(gate.successor_specs()), 15)
        self.assertNotIn(gate.FIXEDTXT_V4, (*gate.MC_TESTS, *gate.MLT_TESTS, *gate.VIDEO_TESTS))
        self.assertIn(gate.FIXEDTXT_V4["source"], gate.successor_required_pins(self.manifest()))

    def test_fixedtxt_v4_manifest_rejects_missing_relabelled_and_historical_cases(self):
        key = gate.successor_target_key(gate.FIXEDTXT_V4)
        for names in ([], list(gate.FIXEDTXT_V4_CASES[:-1]), ["tests::old_v3_case"],
                      [*gate.FIXEDTXT_V4_CASES, "tests::invented_case"],
                      [*gate.FIXEDTXT_V4_CASES[:-1], gate.FIXEDTXT_V4_CASES[0]]):
            value = self.manifest(); value["rust_rosters"][key] = names
            with self.subTest(names=names), self.assertRaises(gate.custody.VerificationError):
                self.admit_shape(value)

    def test_fixedtxt_v4_example_binary_requires_test_kind_profile_and_exact_source(self):
        selected = gate.FIXEDTXT_V4
        rows = self.cargo(selected)
        accepted = gate.select_binaries(self.raw(rows), 0, self.repo, self.target,
                                        (selected,), testing=True)
        self.assertEqual(set(accepted), {selected["name"]})
        for change in ("kind", "profile", "source", "exit"):
            rows = self.cargo(selected)
            if change == "kind": rows[0]["target"]["kind"] = ["bin"]
            if change == "profile": rows[0]["profile"]["test"] = False
            if change == "source": rows[0]["target"]["src_path"] = str(self.repo / "old_v3.rs")
            with self.subTest(change=change), self.assertRaises(gate.custody.VerificationError):
                gate.select_binaries(self.raw(rows), 101 if change == "exit" else 0,
                                     self.repo, self.target, (selected,), testing=True)

    def test_fixedtxt_v4_compiled_list_requires_all_six_and_rejects_omission(self):
        manifest = self.manifest()
        def listing(names):
            return "".join(n + ": test\n" for n in names).encode()
        actual = gate.successor_roster(listing(gate.FIXEDTXT_V4_CASES), 0,
                                      gate.FIXEDTXT_V4, manifest)
        self.assertEqual(set(actual["all"]), set(gate.FIXEDTXT_V4_CASES))
        for names in (gate.FIXEDTXT_V4_CASES[:-1], (),
                      (*gate.FIXEDTXT_V4_CASES, "tests::foreign_case")):
            with self.subTest(names=names), self.assertRaises(gate.custody.VerificationError):
                gate.successor_roster(listing(names), 0, gate.FIXEDTXT_V4, manifest)

    def fixedtxt_producer_fixture(self, unknown=False):
        original, streams, calls = SourceGateTests.producer_fixture(self)
        def command(argv, work, env, logs, label, timeout, ctx):
            if label != "fixedtxt-v4-test-compile":
                return original(argv, work, env, logs, label, timeout, ctx)
            calls.append(label)
            self.assertEqual(argv, ["bound-cargo", "test", "--locked", "-p", gate.MC,
                                    "--example", "fixed_text_compile_v4", "--no-run",
                                    "--message-format=json"])
            if unknown:
                raise gate.observer.UnknownExecution("synthetic V4 compiler uncertainty", {"argv": argv})
            streams[label] = self.raw(self.cargo(gate.FIXEDTXT_V4))
            return {"exit_code": 0, "label": label}
        return command, streams, calls

    def test_fixedtxt_v4_producer_closes_before_sealing_and_rejects_late_compilation(self):
        command, streams, calls = self.fixedtxt_producer_fixture()
        barrier = gate.ProducerBarrier(command, "bound-cargo")
        with mock.patch.object(gate.observer, "original_stream",
                               side_effect=lambda logs, row, channel: streams[row["label"]]):
            prepared = gate.prepare_successor_compiled_role("repair", "bound-cargo", self.repo,
                {}, self.root, {"role": "repair", "product_source_sha": gate.SUCCESSOR_SHA},
                self.target, barrier)
            self.assertEqual(calls[-2:], ["strict-clippy", "fixedtxt-v4-test-compile"])
            self.assertIn("fixedtxt-v4", prepared["collections"])
            barrier.begin_sealing()
            evidence = self.root / "v4-evidence"; evidence.mkdir()
            sealed = gate.seal_compiled_role(prepared, self.repo, self.target, self.root, evidence, barrier)
        self.assertEqual(set(sealed["fixedtxt-v4"]), {"fixed_text_compile_v4"})
        barrier.begin_observation()
        with self.assertRaises(gate.custody.VerificationError):
            barrier(["bound-cargo", "test", "--example", "fixed_text_compile_v4"])

    def test_fixedtxt_v4_unknown_producer_is_not_retried_or_sealed(self):
        command, streams, calls = self.fixedtxt_producer_fixture(unknown=True)
        barrier = gate.ProducerBarrier(command, "bound-cargo")
        with mock.patch.object(gate.observer, "original_stream",
                               side_effect=lambda logs, row, channel: streams[row["label"]]), \
             mock.patch.object(gate.custody, "fingerprint") as fingerprint:
            with self.assertRaises(gate.observer.UnknownExecution):
                gate.prepare_successor_compiled_role("repair", "bound-cargo", self.repo, {},
                    self.root, {"role": "repair", "product_source_sha": gate.SUCCESSOR_SHA},
                    self.target, barrier)
            fingerprint.assert_not_called()
        self.assertEqual(calls.count("fixedtxt-v4-test-compile"), 1)
        self.assertEqual(barrier.phase, "PREPARING")

    def test_successor_coordinator_closes_producers_before_every_current_case(self):
        report, events = self.orchestrated_successor()
        self.assertEqual(report["status"], "PASS_SUCCESSOR_SOURCE_G1", report.get("error"))
        self.assertEqual(events[:4], ["checkout-current", "prepare-current", "prepare-node", "seal-current"])
        self.assertEqual(report["executed_source_sha"], gate.SUCCESSOR_SHA)
        self.assertEqual(report["historical_pair"], "NOT_RUN")
        self.assertEqual(report["baseline"], "NOT_RUN")
        self.assertFalse(report["full_sdk_certification"])
        self.assertFalse(report["historical_evidence_reused"])
        self.assertEqual(report["passed_cases"], sum(len(x) for x in self.manifest()["rust_rosters"].values()))

    def test_successor_coordinator_unknown_stops_after_original_first_observation(self):
        report, events = self.orchestrated_successor(unknown=True)
        self.assertEqual(report["status"], "UNKNOWN_NO_RETRY")
        self.assertEqual(events.count("list-current"), 1)
        self.assertNotIn("case-current", events)
        self.assertEqual(report["roles"]["successor"]["results"], [])

    def test_successor_coordinator_late_source_change_prevents_pass(self):
        report, events = self.orchestrated_successor(source_drift=True)
        self.assertEqual(report["status"], "FAIL_OR_INCOMPLETE")
        self.assertIn("source changed during observation", report["error"])
        self.assertIn("case-current", events)
        self.assertNotIn("passed_cases", report)


    def causal_successor(self, *, omit_producer=False, drift_group=None,
                         late_compile=False, unknown_producer=False):
        """Exercise the current producer and coordinator with synthetic command originals."""
        manifest = self.manifest(); events = []; paths_to_names = {}; streams = {}
        sealed_groups = {}
        env = {**self.env, "MW_SDK_SCOPE": gate.SUCCESSOR_SCOPE}
        self.coordinator_runs = getattr(self, "coordinator_runs", 0) + 1
        output = self.root / ("synthetic-successor-coordinator-" + str(self.coordinator_runs))
        def snapshot(work, source, paths, tree=None):
            if work == self.repo:
                return {"sha": source, "files": {p: {"sha256": h} for p, h in gate.GATE_PINS.items()}}
            self.assertEqual(source, gate.SUCCESSOR_SHA); self.assertEqual(tree, gate.SUCCESSOR_TREE)
            return {"sha": source, "tree": tree, "files": {},
                    "fixture_generation": 1}
        def prepare(role, cargo, work, environment, logs, ctx, target, command):
            self.assertEqual(role, "repair"); self.assertEqual(ctx["product_source_sha"], gate.SUCCESSOR_SHA)
            self.assertEqual(command.phase, "PREPARING"); events.append("prepare-current")
            return {"steps": ["synthetic-compilation"], "clippy": "PASS", "collections": {
                name: {} for name in ("test-tools", "mlt-tests", "mlt-integrations", "video-domain",
                                      "motion-canvas", "materializer")}}
        original_prepare_successor = gate.prepare_successor_compiled_role
        def prepare_current(*args):
            if omit_producer:
                return prepare(*args)
            return original_prepare_successor(*args)
        def runtime(*args, **kwargs):
            self.assertEqual(kwargs["node_version"], manifest["node_version"])
            events.append("prepare-node"); return {}
        def seal(prepared, work, target, logs, evidence, barrier):
            self.assertEqual(barrier.phase, "SEALING"); events.append("seal-current")
            result = {}
            for collection, specs in (("test-tools", gate.MLT_TESTS[1:]), ("mlt-tests", gate.MLT_TESTS),
                                      ("mlt-integrations", gate.MLT_INTEGRATIONS),
                                      ("video-domain", gate.VIDEO_TESTS), ("motion-canvas", gate.MC_TESTS),
                                      ("materializer", (gate.MATERIALIZE,)),
                                      ("fixedtxt-v4", (gate.FIXEDTXT_V4,))):
                self.assertIn(collection, prepared["collections"])
                result[collection] = {}
                for selected in specs:
                    if collection == "fixedtxt-v4":
                        producer = prepared["collections"][collection]
                        result[collection] = gate.select_binaries(
                            streams[producer["label"]], producer["command"]["exit_code"],
                            work, target, specs, testing=True)
                        path = Path(result[collection][selected["name"]]["absolute_path"])
                    else:
                        path = target / (collection + "-" + selected["name"])
                        path.write_bytes(b"synthetic unused binary fixture")
                        result[collection][selected["name"]] = {"absolute_path": str(path),
                                                               **gate.custody.fingerprint(path, target)}
                    paths_to_names[str(path)] = manifest["rust_rosters"].get(gate.successor_target_key(selected), [])
            sealed_groups.update(result)
            prepared["sealed"] = result
            return result
        def command(argv, work, environment, logs, label, timeout, ctx, observations):
            self.assertEqual(ctx["product_source_sha"], gate.SUCCESSOR_SHA)
            if argv[0] == "git":
                self.assertEqual(argv[-1], gate.SUCCESSOR_SHA); events.append("checkout-current")
            elif label == "fixedtxt-v4-test-compile":
                self.assertNotIn("seal-current", events); events.append("compile-fixed-text-v4")
                self.assertEqual(argv[1:], ["test", "--locked", "-p", "semwright-driver-motion-canvas",
                                           "--example", "fixed_text_compile_v4", "--no-run",
                                           "--message-format=json"])
                if unknown_producer:
                    raise gate.observer.UnknownExecution("synthetic V4 compiler uncertainty", {"argv": argv})
                path = Path(environment["CARGO_TARGET_DIR"]) / "compiled-fixed-text-v4"
                path.write_bytes(b"synthetic V4 compiler output; never executed")
                artifact = {
                    "reason": "compiler-artifact",
                    "manifest_path": str(work / "crates/driver-motion-canvas/Cargo.toml"),
                    "target": {"name": "fixed_text_compile_v4", "kind": ["example"],
                               "src_path": str(work / "crates/driver-motion-canvas/examples/fixed_text_compile_v4.rs")},
                    "profile": {"test": True}, "features": [], "executable": str(path),
                }
                streams[label] = SourceGateTests.raw([artifact, {"reason": "build-finished", "success": True}])
            elif "--list" in argv:
                self.assertIn("seal-current", events); events.append("list-current")

                names = paths_to_names[argv[0]]
                if Path(argv[0]).name == "compiled-fixed-text-v4":
                    events.append("list-fixed-text-v4")
                streams[label] = "".join(n + ": test\n" for n in names).encode()
                if drift_group and events.count("list-current") == 1:
                    changed = Path(next(iter(sealed_groups[drift_group].values()))["absolute_path"])
                    changed.write_bytes(changed.read_bytes() + b"changed after sealing")
            else:
                self.assertIn("--exact", argv); events.append("case-current")
                name = argv[2]
                if Path(argv[0]).name == "compiled-fixed-text-v4": events.append("case-fixed-text-v4:" + name)
                streams[label] = (f"running 1 test\ntest {name} ... ok\n"
                                  "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; "
                                  f"{len(paths_to_names[argv[0]]) - 1} filtered out; finished in 0.01s\n").encode()
            return {"exit_code": 0, "argv": argv, "context": ctx, "label": label,
                    "observation": "DIRECT_PROCESS_EXIT_OBSERVED", "log_custody": "VERIFIED_SNAPSHOT"}
        def observed_runtime(runtime, before, output, evidence, logs, environment, ctx, target,
                             command, require_command, binary, **kwargs):
            if late_compile:
                events.append("late-producer-attempt")
                command([self.binary["absolute_path"], "test", "--example", "fixed_text_compile_v4",
                         "--no-run"], self.repo, environment, logs, "fixedtxt-v4-test-compile", 1800, ctx)
            return {"status": "SYNTHETIC_CONTROL_ONLY"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(gate.sys, "platform", "linux"), \
             mock.patch.object(gate.observer, "__file__", str(self.repo / "tooling/ci_foundation_causal.py")), \
             mock.patch.object(gate.custody, "__file__", str(self.repo / "tooling/ci_storage_apply_recovery.py")), \
             mock.patch.object(gate, "admit_successor_manifest", return_value=(manifest, {"synthetic": True})), \
             mock.patch.object(gate.custody, "source_snapshot", side_effect=snapshot), \
             mock.patch.object(gate.observer, "retain_source", return_value={}), \
             mock.patch.object(gate.observer, "tools_identity", return_value={"cargo": self.binary}), \
             mock.patch.object(gate.observer, "command", side_effect=command), \
             mock.patch.object(gate.observer, "original_stream",
                               side_effect=lambda logs, row, channel: streams.get(row["label"], b"")
                               if channel == "stdout" else b""), \
             mock.patch.object(gate, "input_paths", return_value=[]), \
             mock.patch.object(gate, "prepare_compiled_role", side_effect=prepare), \
             mock.patch.object(gate, "prepare_successor_compiled_role", side_effect=prepare_current), \
             mock.patch.object(gate, "prepare_runtime", side_effect=runtime), \
             mock.patch.object(gate, "seal_compiled_role", side_effect=seal), \
             mock.patch.object(gate, "run_runtime", side_effect=observed_runtime), \
             mock.patch.object(gate, "verify_commands"), \
             mock.patch.object(gate, "verify_pair") as old_pair:
            report = gate.run(self.repo, self.root / "sdk", output, scope=gate.SUCCESSOR_SCOPE,
                              successor_manifest=gate.MANIFEST_PREFIX + "synthetic.json",
                              successor_manifest_sha256="a" * 64)
        old_pair.assert_not_called()
        return report, events

    def test_s3_coordinator_executes_each_fixed_text_case_once_after_its_producer(self):
        report, events = self.causal_successor()
        self.assertEqual(report["status"], "PASS_SUCCESSOR_SOURCE_G1", report.get("error"))
        self.assertEqual(events.count("compile-fixed-text-v4"), 1)
        self.assertEqual(events.count("list-fixed-text-v4"), 1)
        self.assertLess(events.index("compile-fixed-text-v4"), events.index("seal-current"))
        self.assertLess(events.index("seal-current"), events.index("list-current"))
        seen = [e.removeprefix("case-fixed-text-v4:") for e in events if e.startswith("case-fixed-text-v4:")]
        self.assertCountEqual(seen, gate.FIXEDTXT_V4_CASES)
        self.assertEqual(len(seen), 6)
        results = [r for r in report["roles"]["successor"]["results"] if r["target"] == gate.successor_target_key(gate.FIXEDTXT_V4)]
        self.assertEqual(len(results), 6)

    def test_s3_omitted_example_producer_prevents_sealing_and_all_observation(self):
        report, events = self.causal_successor(omit_producer=True)
        self.assertEqual(report["status"], "FAIL_OR_INCOMPLETE")
        self.assertIn("successor compiler target closure differs", report["error"])
        for event in ("compile-fixed-text-v4", "prepare-node", "seal-current", "list-current", "case-current"):
            self.assertNotIn(event, events)
        self.assertNotIn("passed_cases", report)

    def test_s3_coordinator_refuses_a_late_example_compiler_before_delegate_execution(self):
        report, events = self.causal_successor(late_compile=True)
        self.assertEqual(report["status"], "FAIL_OR_INCOMPLETE")
        self.assertIn("producer attempted after sealing", report["error"])
        self.assertIn("late-producer-attempt", events)
        self.assertEqual(events.count("compile-fixed-text-v4"), 1)
        self.assertNotIn("passed_cases", report)

    def test_s3_every_sealed_binary_group_remains_guarded_before_the_first_case(self):
        for collection in ("test-tools", "mlt-tests", "mlt-integrations", "video-domain",
                           "motion-canvas", "materializer", "fixedtxt-v4"):
            with self.subTest(collection=collection):
                report, events = self.causal_successor(drift_group=collection)
                self.assertEqual(report["status"], "FAIL_OR_INCOMPLETE")
                self.assertIn("compiled original binary changed", report["error"])
                self.assertEqual(events.count("list-current"), 1)
                self.assertNotIn("case-current", events)
                self.assertNotIn("passed_cases", report)

    def test_s3_unknown_example_producer_stops_without_retry_or_sealing(self):
        report, events = self.causal_successor(unknown_producer=True)
        self.assertEqual(report["status"], "UNKNOWN_NO_RETRY")
        self.assertEqual(events.count("compile-fixed-text-v4"), 1)
        for event in ("prepare-node", "seal-current", "list-current", "case-current"):
            self.assertNotIn(event, events)
        self.assertIn("fixed_text_compile_v4", report["uncertain_command"]["argv"])
        self.assertNotIn("passed_cases", report)


if __name__ == "__main__":
    unittest.main()
