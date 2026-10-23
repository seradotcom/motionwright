#!/usr/bin/env python3
"""Positive and causal rejection controls for the frozen SDK source observer."""
from __future__ import annotations
import ast
import json
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

    def test_cargo_duplicate_json_key_is_rejected(self):
        with self.assertRaises(gate.custody.VerificationError):
            gate.cargo_rows(b'{"reason":"build-finished","success":true,"success":false}\n', 0)

    def test_causal_roster_is_exact_and_controls_are_not_dropped(self):
        pair = gate.causal_pair()
        self.assertEqual(len(pair["failures"]), 7)
        self.assertEqual(len(gate.CAUSAL_CASES) - len(pair["failures"]), 4)
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

    def test_all_seven_causal_failures_and_four_controls_use_existing_observer(self):
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
        expected_paths = {gate.WORKFLOW, gate.HELPER, gate.TEST}
        for event in ("pull_request", "push"):
            section = re.search(r"(?ms)^  " + event + r":\n(.*?)(?=^  [a-z_]+:|\Z)", header)
            self.assertIsNotNone(section)
            self.assertIn("    branches: [main]", section.group(1))
            paths = re.findall(r"(?m)^      - (.+)$", section.group(1))
            self.assertEqual(len(paths), 3)
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


if __name__ == "__main__":
    unittest.main()
