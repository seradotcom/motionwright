"""Source-only controls for SDK selection, authority, originals and ordering."""
from __future__ import annotations

import copy
import ast
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

from tooling import ci_sdk_foundation_candidate as gate


class SDKFoundationCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sdk-foundation-controls-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "sdk"; self.repo.mkdir()
        self.logs = self.root / "logs"; self.logs.mkdir()
        self.target = self.root / "target"; self.target.mkdir()
        self.context = {"mode": "windows-g1", "github": {}, "executed_source_sha": gate.FIXED["sha"],
                        "executed_tree_sha": gate.FIXED["tree"]}
        self.binary_path = self.target / "binary"; self.binary_path.write_bytes(b"sealed binary")
        self.binary = {"absolute_path": str(self.binary_path), **gate.custody.fingerprint(self.binary_path, self.target)}

    def local_authority(self):
        return {"kind": "local-lease", "mode": "windows-g1", "source_sha": gate.FIXED["sha"],
                "review_sha256": "a" * 64, "max_total_seconds": 600, "binding": {"lease_sha256": "b" * 64}}

    def remote_environment(self):
        return {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
                "GITHUB_EVENT_NAME": "workflow_dispatch", "SDK_FOUNDATION_SCOPE": gate.SCOPES[1],
                "GITHUB_JOB": "sdk-foundation-linux-two", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "123",
                "GITHUB_SHA": "c" * 40, "GITHUB_WORKFLOW_SHA": "c" * 40,
                "GITHUB_REPOSITORY": "example/repo",
                "GITHUB_WORKFLOW_REF": "example/repo/" + gate.WORKFLOW + "@refs/heads/review"}

    def lease(self):
        return {"schema": "motionwright.sdk-foundation-slot/1", "issuer": "release-integrator", "holder": "ci-verification",
                "status": "EXCLUSIVE_GRANTED", "mode": "windows-g1",
                "source_shas": sorted([gate.FIXED["sha"], *(p["sha"] for p in gate.CAUSALS)]),
                "run_root": str(self.root / "run"), "not_before_unix_ns": 100,
                "expires_unix_ns": 700_000_000_000, "max_total_seconds": 600, "event_id": "owned-slot-1",
                "process_boundary": "DIRECT_PROCESS_ONLY", "continuous_resource_watchdog": False}

    def g0_snapshot(self):
        names = ("scripts/verify-oss-hygiene.py", "scripts/verify-source.py", "scripts/sync-contracts.py",
                 "Cargo.toml", "Cargo.lock", "rust-toolchain.toml")
        return {"sha": gate.FIXED["sha"], "tree": gate.FIXED["tree"], "files": {n: {} for n in names}}

    def g0_tools(self):
        return {n: {"absolute_path": str(self.root / n)} for n in ("python", "cargo-fmt", "cargo")}

    def g0_fixture(self, failure=None, unknown=None, source_report=None):
        calls = []
        report = {"source_validation": "PASS", "rust_compiled": False, "counts": {"json": 3}}
        if source_report is not None:
            report = source_report
        def call(argv, cwd, env, output, label, timeout, context):
            calls.append((label, argv, context))
            if label == unknown:
                raise gate.observer.UnknownExecution("original uncertainty", {"label": label, "observation": "UNKNOWN_TIMEOUT_NO_RETRY"})
            return {"exit_code": 1 if label == failure else 0, "label": label, "argv": argv, "context": context}
        def stream(output, row, channel):
            if channel == "stderr":
                return b"original stderr\n"
            if row["label"] == "g0-source":
                return ("source synchronization checked\n" + json.dumps(report, indent=2) + "\n").replace("\n", "\r\n").encode()
            return b"original stdout\n"
        return calls, call, stream

    def group_fixture(self):
        names = tuple("operation_permits::tests::case_" + s for s in "abc")
        group = gate.GROUPS[0]
        listed = gate.group_roster((*names, "elsewhere::other"), group)
        argv = gate.group_argv(self.binary, group)
        row = {"observation": "DIRECT_PROCESS_EXIT_OBSERVED", "log_custody": "VERIFIED_SNAPSHOT",
               "exit_code": 0, "argv": argv, "context": self.context}
        raw = ("running 3 tests\n" + "".join("test " + n + " ... ok\n" for n in names)
               + "test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 1 filtered out; finished in 0.01s\n").encode()
        return raw, row, listed, argv

    def admit(self, raw=None, row=None, after=None):
        original, observed, listed, argv = self.group_fixture()
        return gate.admit_group(original if raw is None else raw, observed if row is None else row,
                                self.binary, self.binary if after is None else after, listed, argv, self.context)

    def test_empty_authority_stops_before_paths_or_product_commands(self):
        # Exercise empty and populated catalogues explicitly. A future reviewed
        # production entry must not permit an unknown ID or a foreign source.
        self.assertEqual(hashlib.sha256(json.dumps(gate.CAUSALS, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest(), "39f7a1fdefdd08f0751751f804ca7a83a5421adf728266fec95303acb06c209f")
        self.assertTrue(all(case["pins"]["docs/commands.md"] ==
            "23b03db7d5e68d7340247b00b710d644e533b83a5bb9ac55d43f853c39aecf12" for case in gate.CAUSALS))
        self.assertEqual(gate.FIXED["sha"], "dac1f5886b97efb4cd7a9351e817f4b7321b79c9")
        self.assertEqual(gate.FIXED["tree"], "564852515f37f2ba7fad537a26c8085db338e3e3")
        self.assertEqual(gate.FIXED["listing"], "7ec70ebd978bfdaa3a98772aa35140fba04e3fc0451ef9b34271c0944cc7ee15")
        self.assertEqual(hashlib.sha256(json.dumps(gate.FIXED["pins"], sort_keys=True,
            separators=(",", ":")).encode()).hexdigest(), "291667adc19acb1c18ebdda51f336374d84d631af7182a7ee67c1bda8ff8c124")
        for catalogue in ({}, {"reviewed": self.local_authority()}):
            with self.subTest(authority_entries=len(catalogue)), \
                    patch.object(gate, "REVIEWED_AUTHORITIES", catalogue), \
                    patch.object(gate.observer, "command") as command, \
                    patch.object(gate, "full_snapshot") as reader, \
                    self.assertRaisesRegex(ValueError, "authority"):
                gate.run("windows-g1", gate.FIXED["sha"], "self-issued", self.repo, {}, self.root / "new", environment={})
            command.assert_not_called()
            reader.assert_not_called()
            self.assertFalse((self.root / "new").exists())
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"reviewed": self.local_authority()}), \
                patch.object(gate.observer, "command") as command, \
                patch.object(gate, "full_snapshot") as reader, \
                self.assertRaisesRegex(ValueError, "unknown source profile"):
            gate.run("windows-g1", "c00a811a5d47b2f1505f2815d0d8b2eb68fb8ab2", "reviewed", self.repo, {}, self.root / "new", environment={})
        command.assert_not_called()
        reader.assert_not_called()
        self.assertFalse((self.root / "new").exists())

    def test_local_context_is_truthful_and_requires_the_reviewed_mode(self):
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"slot": self.local_authority()}):
            result = gate.request_context({}, "windows-g1", gate.FIXED["sha"], "slot", "win32")
            self.assertEqual(result["execution_kind"], "LOCAL_WINDOWS")
            self.assertFalse(any(result["github"].values()))
            self.assertEqual(result["escaped_descendants"], "NOT_OBSERVED")
            for env, platform in (({"GITHUB_ACTIONS": "true"}, "win32"), ({"GITHUB_SHA": "a" * 40}, "win32"), ({}, "linux")):
                with self.subTest(env=env, platform=platform), self.assertRaises(ValueError):
                    gate.request_context(env, "windows-g1", gate.FIXED["sha"], "slot", platform)

    def test_remote_original_attempt_and_exact_context_are_required(self):
        env = self.remote_environment()
        auth = {**self.local_authority(), "kind": "github-dispatch", "mode": "linux-g2",
                "binding": {"repository": env["GITHUB_REPOSITORY"], "workflow_ref": env["GITHUB_WORKFLOW_REF"]}}
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": auth}):
            self.assertEqual(gate.request_context(env, "linux-g2", gate.FIXED["sha"], "remote", "linux")["github"],
                             {k: env[k] for k in gate.request_context(env, "linux-g2", gate.FIXED["sha"], "remote", "linux")["github"]})
            for name, value in (("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_EVENT_NAME", "push"), ("GITHUB_JOB", "other"),
                                ("GITHUB_WORKFLOW_SHA", "d" * 40), ("GITHUB_WORKFLOW_REF", "other"), ("RUNNER_OS", "Windows")):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    gate.request_context({**env, name: value}, "linux-g2", gate.FIXED["sha"], "remote", "linux")

    def test_typed_pinned_lease_accepts_only_the_exact_scope_and_window(self):
        value = self.lease()
        self.assertEqual(gate.validate_lease(value, "a" * 64, "a" * 64, self.root / "run", 1000, 600), value)
        for key, changed in (("issuer", "ci-verification"), ("holder", "release-integrator"), ("source_shas", [gate.FIXED["sha"]]),
                             ("expires_unix_ns", 100), ("not_before_unix_ns", 2000), ("max_total_seconds", True),
                             ("run_root", str(self.root / "other")), ("continuous_resource_watchdog", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.validate_lease({**value, key: changed}, "a" * 64, "a" * 64, self.root / "run", 1000, 600)
        with self.assertRaisesRegex(ValueError, "reviewed authority"):
            gate.validate_lease(value, "a" * 64, "b" * 64, self.root / "run", 1000, 600)

    def test_original_budget_caps_each_call_without_reopening_time(self):
        now = [0]
        budget = gate.Budget(60, lambda: now[0])
        now[0] = 50_000_000_000
        command = Mock(return_value={"exit_code": 0})
        budget.call(command, ["tool"], self.repo, {}, self.logs, "one", 900, {})
        self.assertEqual(command.call_args.args[5], 10.0)
        now[0] = 60_000_000_000
        with self.assertRaisesRegex(ValueError, "global budget"):
            budget.call(command, ["tool"], self.repo, {}, self.logs, "two", 900, {})
        self.assertEqual(command.call_count, 1)
        for seconds in (True, float("inf"), 0, 7201):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                gate.Budget(seconds)

    def test_inherited_compiler_formatter_loader_product_and_git_overrides_reject(self):
        for key in ("RUSTFLAGS", "RUSTC_WRAPPER", "RUSTFMT", "RUSTC", "RUSTDOC", "CARGO", "LD_AUDIT",
                    "SEMWRIGHT_TEST_OWNER_PERMIT_DRIVER", "SEMWRIGHT_BROKER", "PYTHONPATH", "GIT_DIR",
                    "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_NAMESPACE", "CARGO_TARGET_X_LINKER", "NODE_OPTIONS"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "inherited"):
                gate.clean_environment({key: "injected"}, self.root, self.target, self.root / "tmp")
        env = gate.clean_environment({"GITHUB_SHA": "a" * 40}, self.root, self.target, self.root / "tmp")
        self.assertEqual(env["GITHUB_SHA"], "a" * 40)
        self.assertEqual(env["CARGO_BUILD_JOBS"], "1")
        self.assertEqual(env["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(env["CARGO_HOME"], str(self.root / "cargo-home"))

    def test_sdk_g0_is_focal_uses_sdk_paths_and_retains_actual_context(self):
        calls, call, stream = self.g0_fixture()
        with patch.object(gate, "source_unchanged", return_value="EXACT_SDK_SOURCE_UNCHANGED"), \
             patch.object(gate, "verify_tools"), patch.object(gate.observer, "original_stream", side_effect=stream):
            result = gate.sdk_g0(self.repo, self.g0_snapshot(), self.g0_tools(), {}, self.logs, self.context, call)
        self.assertEqual([r[0] for r in calls], ["g0-prerequisites", "g0-hygiene", "g0-source", "g0-format"])
        for _, argv, _ in calls[:3]:
            self.assertEqual(argv[:5], [str(self.root / "python"), "-I", "-B", "-X", "utf8"])
        self.assertIn(str(self.repo / "scripts/verify-source.py"), calls[2][1])
        self.assertEqual(calls[-1][1], [str(self.root / "cargo-fmt"), "fmt", "--all", "--", "--check"])
        self.assertEqual(result["status"], "PASS_G0_SDK_FOCAL")
        self.assertFalse(result["rust_compiled"])
        self.assertEqual(set(result["remaining_sdk_ci"].values()), {"NOT_RUN"})
        self.assertEqual(json.loads((self.logs / "sdk-g0.json").read_text())["executed_source_sha"], gate.FIXED["sha"])

    def test_real_source_failure_stops_format_and_preserves_the_incomplete_stage(self):
        calls, call, stream = self.g0_fixture(failure="g0-source")
        stage = {}
        with patch.object(gate, "source_unchanged"), patch.object(gate, "verify_tools"), \
             patch.object(gate.observer, "original_stream", side_effect=stream), self.assertRaisesRegex(ValueError, "g0-source"):
            gate.sdk_g0(self.repo, self.g0_snapshot(), self.g0_tools(), {}, self.logs, self.context, call, stage)
        self.assertEqual([r[0] for r in calls], ["g0-prerequisites", "g0-hygiene", "g0-source"])
        self.assertEqual(stage["status"], "NOT_COMPLETED")
        self.assertEqual(stage["steps"][-1]["exit_code"], 1)

    def test_unknown_sdk_g0_is_not_retried_or_promoted(self):
        calls, call, stream = self.g0_fixture(unknown="g0-source")
        with patch.object(gate, "source_unchanged"), patch.object(gate, "verify_tools"), \
             patch.object(gate.observer, "original_stream", side_effect=stream), self.assertRaises(gate.observer.UnknownExecution) as caught:
            gate.sdk_g0(self.repo, self.g0_snapshot(), self.g0_tools(), {}, self.logs, self.context, call)
        self.assertEqual(caught.exception.command["label"], "g0-source")
        self.assertEqual(sum(label == "g0-source" for label, *_ in calls), 1)
        self.assertNotIn("g0-format", [x[0] for x in calls])

    def test_foreign_sdk_g0_context_and_noninteger_report_counts_reject(self):
        calls, call, stream = self.g0_fixture(source_report={"source_validation": "PASS", "rust_compiled": False, "counts": {"json": True}})
        with self.assertRaisesRegex(ValueError, "another source"):
            gate.sdk_g0(self.repo, self.g0_snapshot(), self.g0_tools(), {}, self.logs,
                        {**self.context, "executed_source_sha": "e" * 40}, call)
        self.assertFalse(calls)
        with patch.object(gate, "source_unchanged"), patch.object(gate, "verify_tools"), \
             patch.object(gate.observer, "original_stream", side_effect=stream), self.assertRaisesRegex(ValueError, "source report"):
            gate.sdk_g0(self.repo, self.g0_snapshot(), self.g0_tools(), {}, self.logs, self.context, call)

    def test_changed_sdk_source_or_formatter_prevents_a_g0_pass(self):
        calls, call, stream = self.g0_fixture()
        with patch.object(gate, "source_unchanged", side_effect=[None, ValueError("source changed")]), \
             patch.object(gate, "verify_tools"), patch.object(gate.observer, "original_stream", side_effect=stream), \
             self.assertRaisesRegex(ValueError, "source changed"):
            gate.sdk_g0(self.repo, self.g0_snapshot(), self.g0_tools(), {}, self.logs, self.context, call)
        tools = {"rustfmt": {"absolute_path": str(self.binary_path), "fingerprint": gate.custody.fingerprint(self.binary_path, self.binary_path.parent)}}
        self.binary_path.write_bytes(b"modified formatter")
        with self.assertRaisesRegex(ValueError, "tool changed"):
            gate.verify_tools(tools)

    def test_full_snapshot_includes_sdk_docs_scripts_and_schemas_not_motionwright_roots(self):
        names = ["docs/commands.md", "scripts/verify-source.py", "schemas/commands.json", "Cargo.lock"]
        raw = b"".join(("100644 blob " + "a" * 40 + "\t" + name + "\0").encode() for name in names)
        profile = {"sha": "a" * 40, "tree": "b" * 40, "pins": {"Cargo.lock": "c" * 64}}
        observed = {"files": {"Cargo.lock": {"sha256": "c" * 64}}}
        with patch.object(gate.custody, "git", return_value=raw), patch.object(gate.custody, "source_snapshot", return_value=observed) as snapshot:
            self.assertEqual(gate.full_snapshot(self.repo, profile), observed)
        self.assertEqual(snapshot.call_args.args[2], tuple(sorted(names)))
        with patch.object(gate.custody, "git", return_value=raw.replace(b"100644", b"120000", 1)), self.assertRaisesRegex(ValueError, "regular blob"):
            gate.full_snapshot(self.repo, profile)

    def test_source_postcheck_detects_changed_bytes_even_with_clean_git_claim(self):
        p = self.repo / "source.rs"; p.write_bytes(b"old")
        snapshot = {"sha": "a" * 40, "files": {"source.rs": gate.custody.fingerprint(p, self.repo)}}
        p.write_bytes(b"new")
        with patch.object(gate.custody, "git", side_effect=[b"a" * 40 + b"\n", b""]), self.assertRaisesRegex(ValueError, "input changed"):
            gate.source_unchanged(self.repo, snapshot)

    def test_seven_producers_keep_nine_configurations_and_both_daemon_features(self):
        plan = gate.producer_plan("windows-g1", "cargo-exact")
        self.assertEqual(len(plan), 7)
        self.assertEqual(sum(len(row[2]) for row in plan), 9)
        self.assertTrue(all("--locked" in row[1] and "--no-run" in row[1] for row in plan))
        private, plain = plan[-2:]
        self.assertIn("private-host-stdin-custody", private[1])
        self.assertIn("--no-default-features", plain[1])
        self.assertNotEqual(private[0], plain[0])
        self.assertEqual(gate.validate_catalogue()["windows_positive_observations"], 134)

    def test_all_five_sdk_g0_checks_precede_the_first_producer(self):
        states = [{"id": "fixed"}, *({"id": p["id"], "causal": p} for p in gate.CAUSALS)]
        for state in states:
            state.update(repo=self.repo, source={}, env={}, logs=self.logs, context={})
        trace = []
        def g0(*args):
            trace.append("g0")
        def producer(*args):
            trace.append("producer")
            raise ValueError("stop at first producer")
        report = {"sdk_g0": {}}
        with patch.object(gate, "sdk_g0", side_effect=g0), patch.object(gate, "prepare_role", side_effect=producer), \
             self.assertRaisesRegex(ValueError, "first producer"):
            gate.execute_selection(states, "windows-g1", {"cargo": {"absolute_path": "cargo"}}, {}, self.root,
                                   Mock(), Mock(), report)
        self.assertEqual(trace, ["g0"] * 5 + ["producer"])

    def test_any_sdk_g0_failure_prevents_every_producer(self):
        states = [{"id": str(i), "repo": self.repo, "source": {}, "env": {}, "logs": self.logs, "context": {}} for i in range(5)]
        with patch.object(gate, "sdk_g0", side_effect=[None, None, ValueError("source verification failed")]), \
             patch.object(gate, "prepare_role") as prepare, self.assertRaisesRegex(ValueError, "source verification"):
            gate.execute_selection(states, "windows-g1", {}, {}, self.root, Mock(), Mock(), {"sdk_g0": {}})
        prepare.assert_not_called()

    def test_unknown_first_producer_stops_later_producers_and_observations(self):
        state = {"repo": self.repo, "target": self.target, "env": {}, "logs": self.logs, "context": self.context}
        unknown = gate.observer.UnknownExecution("original timeout", {"observation": "UNKNOWN_TIMEOUT_NO_RETRY"})
        command = Mock(side_effect=unknown)
        with self.assertRaises(gate.observer.UnknownExecution) as caught:
            gate.prepare_role(state, "windows-g1", "cargo", command)
        self.assertIs(caught.exception, unknown)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[5], 900)

    def test_filter_groups_require_observing_phase_even_without_exact_flag(self):
        command = Mock()
        barrier = gate.producer.ProducerBarrier(command, "cargo")
        with self.assertRaisesRegex(ValueError, "after sealing"):
            gate.observe_fixed_groups({}, barrier, [])
        command.assert_not_called()

    def test_compiled_roster_rejects_duplicates_foreign_output_and_missing_namespace(self):
        raw = b"namespace::one: test\nnamespace::two: test\n"
        self.assertEqual(gate.parse_roster(raw, 0), ("namespace::one", "namespace::two"))
        for invalid in (raw + raw, raw + b"error: not a list\n", b"", raw.replace(b": test", b": benchmark")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                gate.parse_roster(invalid, 0)
        group = gate.GROUPS[10]
        names = tuple("effects_readback::target_linux_preparation_tests::case_" + s for s in "abc")
        self.assertEqual(gate.group_roster(names, group)["selected"], names)
        with self.assertRaisesRegex(ValueError, "namespace"):
            gate.group_roster(tuple("alien::target_linux_preparation_tests::case_" + s for s in "abc"), group)

    def test_group_admits_exact_named_roster_and_preserves_binary_identity(self):
        result = self.admit()
        self.assertEqual(len(result["ids"]), 3)
        self.assertEqual(result["binary_sha256"], self.binary["sha256"])
        self.assertEqual(result["status"], "PASS_EXACT_COMPILED_GROUP")

    def test_counts_cannot_hide_changed_omitted_duplicate_or_extra_ids(self):
        raw, *_ = self.group_fixture()
        for changed in (raw.replace(b"case_b", b"case_z"), raw.replace(b"test operation_permits::tests::case_b ... ok\n", b""),
                        raw.replace(b"case_b", b"case_a"), raw + b"test alien::extra ... ok\n"):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "named case"):
                self.admit(raw=changed)

    def test_group_rejects_ignored_wrong_filtered_panic_and_noninteger_exit(self):
        raw, row, *_ = self.group_fixture()
        for changed in (raw.replace(b"case_a ... ok", b"case_a ... ignored"), raw.replace(b"1 filtered", b"0 filtered"),
                        raw + b"\nthread 'unjoined-child' panicked at child.rs:10:4:\nactual child failure\n"):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.admit(raw=changed)
        with self.assertRaises(ValueError):
            self.admit(row={**row, "exit_code": False})
        with self.assertRaisesRegex(ValueError, "binary changed"):
            self.admit(after={**self.binary, "sha256": "f" * 64})

    def test_compiler_json_requires_real_target_source_and_requested_features(self):
        spec = gate.TARGETS["daemon"]
        self.assertEqual(spec["source"], "crates/daemon/src/semwrightd.rs")
        artifact = {"reason": "compiler-artifact", "manifest_path": str(self.repo / "crates/daemon/Cargo.toml"),
                    "profile": {"test": True}, "executable": str(self.binary_path), "features": ["private-host-stdin-custody"],
                    "target": {"name": spec["name"], "kind": ["bin"], "src_path": str(self.repo / "crates/daemon/src/semwrightd.rs")}}
        def raw(row):
            return (json.dumps(row) + "\n" + json.dumps({"reason": "build-finished", "success": True}) + "\n").encode()
        argv = ["cargo", "test", "--features", "private-host-stdin-custody"]
        self.assertEqual(gate.compiled_paths(raw(artifact), 0, self.repo, self.target, (spec,), True, argv), {spec["name"]: str(self.binary_path)})
        for changed in ({**artifact, "features": []},
                        {**artifact, "target": {**artifact["target"], "src_path": str(self.repo / "foreign.rs")}},
                        {**artifact, "target": {**artifact["target"], "src_path": str(self.repo / "crates/daemon/src/main.rs")}}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                gate.compiled_paths(raw(changed), 0, self.repo, self.target, (spec,), True, argv)
        with self.assertRaisesRegex(ValueError, "no-default"):
            gate.compiled_paths(raw(artifact), 0, self.repo, self.target, (spec,), True, ["cargo", "test", "--no-default-features"])
        with self.assertRaisesRegex(ValueError, "not causal RED"):
            gate.compiled_paths(raw(artifact), 101, self.repo, self.target, (spec,), True, argv)

    def test_causal_result_requires_named_failure_at_frozen_site_and_original_cause(self):
        causal = gate.CAUSALS[2]
        name, failure = causal["test"], causal["failure"]
        pair = {"id": causal["id"], "baseline": {"targets": {"unit": [name]}}, "repair": {"targets": {}}, "failures": {name: failure}}
        row = {"observation": "DIRECT_PROCESS_EXIT_OBSERVED", "log_custody": "VERIFIED_SNAPSHOT", "exit_code": 101,
               "argv": gate.observer.case_argv(self.binary, pair, "baseline", name), "context": self.context}
        raw = ("test " + name + " ... FAILED\nthread '" + name + "' panicked at " + failure["source"] + ":717:9:\n"
               + failure["markers"][0] + "\ntest result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out;\n").encode()
        def admit(data, command=row):
            return gate.observer.admit_case(pair, "baseline", name, data, command, self.binary, self.binary,
                                            {"all": (name,), "selected": (name,)}, self.context)
        self.assertEqual(admit(raw)["status"], "EXPECTED_CAUSAL_RED")
        for changed in (raw.replace(b":717:9", b":719:9"), raw.replace(failure["markers"][0].encode(), b"unrelated assertion"),
                        raw.replace(("thread '" + name + "'").encode(), b"thread 'other'")):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                admit(changed)
        with self.assertRaises(ValueError):
            admit(b"compiler failed", {**row, "exit_code": 1})

    def test_linux_selection_has_three_producers_and_two_exact_ignored_methods(self):
        plan = gate.producer_plan("linux-g2", "cargo")
        self.assertEqual(len(plan), 3)
        self.assertEqual([row[3] for row in plan], [False, False, True])
        self.assertEqual(len(gate.G2_CASES), 2)
        self.assertTrue(all(n.startswith("owner_operation_process_tests::") for n in gate.G2_CASES))
        self.assertNotIn("private-host-stdin-custody", json.dumps(plan))

    def test_original_resource_thresholds_and_compiler_slot_are_not_counts_only(self):
        for initial, limits in ((True, (4, 8, 18)), (False, (4, 0, 18))):
            values = dict(zip(("physical", "virtual", "disk"),
                              (minimum * 1024**3 for minimum in limits)))
            values["compilers"] = []
            with self.subTest(initial=initial, boundary="exact"):
                result = gate.resource_values(values, "windows-g1", initial)
                self.assertEqual(result["minimum_gib"],
                                 dict(zip(("physical", "virtual", "disk"), limits)))
                self.assertFalse(result["continuous_monitoring"])
                self.assertEqual(result["cargo_jobs"], 1)
            for key, value in (("physical", values["physical"] - 1),
                               ("virtual", values["virtual"] - 1),
                               ("disk", values["disk"] - 1),
                               ("compilers", [{"Id": 42}]), ("physical", True)):
                with self.subTest(initial=initial, key=key), self.assertRaises(ValueError):
                    gate.resource_values({**values, key: value}, "windows-g1", initial)
        linux = gate.resource_values(
            {"physical": 2 * 1024**3, "virtual": 0, "disk": 12 * 1024**3,
             "compilers": []}, "linux-g2", True)
        self.assertEqual(linux["minimum_gib"], {"physical": 2, "virtual": 0, "disk": 12})
        self.assertFalse(linux["continuous_monitoring"])
        self.assertEqual(linux["cargo_jobs"], 1)

    def test_missing_retained_fixture_root_never_receives_original_credit(self):
        temp = self.root / "owned-temp"; temp.mkdir()
        (self.logs / "linux-native-0.stderr.log").write_bytes(("RETAINED_OWNER_PERMIT_FIXTURE_ROOT=" + str(temp / "missing") + "\n").encode())
        with self.assertRaises(FileNotFoundError):
            gate.retained_failure_roots({"logs": self.logs, "temporary": temp}, self.root)

    def test_existing_failed_fixture_bytes_are_retained_without_wire_or_success_credit(self):
        temp = self.root / "owned-temp"; temp.mkdir()
        fixture = temp / "fixture"; fixture.mkdir(); (fixture / "original.json").write_bytes(b'{"actual":true}\n')
        (self.logs / "linux-native-0.stderr.log").write_bytes(("RETAINED_OWNER_PERMIT_FIXTURE_ROOT=" + str(fixture) + "\n").encode())
        result = gate.retained_failure_roots({"logs": self.logs, "temporary": temp}, self.root)
        self.assertEqual(result[0]["status"], "FILES_RETAINED")
        self.assertFalse(result[0]["raw_wire_capture"])
        retained = self.root / result[0]["files"]["original.json"]["path"]
        self.assertEqual(retained.read_bytes(), b'{"actual":true}\n')


    def gate_receipt_fixture(self):
        folder = self.root / "gate-originals"; folder.mkdir()
        path = folder / "gate-unittest.json"
        files = {p: {"sha256": "a" * 64} for p in (
            "tooling/ci_test_runner.py", *("tooling/tests/" + p for p in gate.GATE_PATTERNS),
            "Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")}
        snapshot = {"sha": "b" * 40, "tree": "c" * 40, "files": files}
        python = {"absolute_path": str(self.root / "python"), "fingerprint": {"sha256": "d" * 64}}
        entries = []
        for pattern in gate.GATE_PATTERNS:
            entries.append(("discovery:" + pattern, pattern[:-3], [python["absolute_path"], "-B", "-E", "-s", "-m", "unittest", "discover", "-s", "tooling/tests", "-p", pattern, "-v"]))
        for pattern in sorted(gate.GATE_PATTERNS):
            module = "tooling.tests." + pattern[:-3]
            entries.append(("dotted:" + module, pattern[:-3], [python["absolute_path"], "-B", "-E", "-s", "-m", "unittest", module, "-v"]))
        steps = []
        for index, (label, module, argv) in enumerate(entries):
            stderr = ("test_one (" + module + ".Fixture.test_one) ... ok\n"
                      "test_two (" + module + ".Fixture.test_two) ... ok\n\nRan 2 tests in 0.001s\n\nOK\n").encode()
            step = {"entry_point": label, "command": argv, "status": "PASS", "exit_code": 0,
                    "counts": gate.fast_runner.parse_unittest_summary(stderr.decode())}
            for stream, raw in (("stdout", b""), ("stderr", stderr)):
                name = path.stem + "-" + str(index).zfill(2) + "-" + stream + ".log"
                (folder / name).write_bytes(raw)
                step[stream] = {"file": name, "sha256": hashlib.sha256(raw).hexdigest()}
            steps.append(step)
        receipt = {"schema": "motionwright.unittest-execution/1", "executed_source_sha": snapshot["sha"],
                   "tree_sha": snapshot["tree"], "status": "PASS", "exit_code": 0, "require_complete": True,
                   "runner_sha256": files["tooling/ci_test_runner.py"]["sha256"],
                   "python_executable_sha256": python["fingerprint"]["sha256"],
                   "test_definition_sha256": {"tooling/tests/" + p: files["tooling/tests/" + p]["sha256"] for p in gate.GATE_PATTERNS},
                   "source_locks": {p: files[p]["sha256"] for p in ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")},
                   "github": {k: "" for k in ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")},
                   "steps": steps}
        path.write_text(json.dumps(receipt))
        return path, snapshot, python, receipt

    def test_gate_controls_admit_real_runner_filename_contract_and_both_rosters(self):
        path, snapshot, python, _ = self.gate_receipt_fixture()
        result = gate.admit_gate_controls(path, snapshot, python, self.context)
        self.assertEqual(result["status"], "PASS_EXACT_GATE_CONTRACTS")
        self.assertEqual(result["source_sha"], snapshot["sha"])
        self.assertEqual(len(result["rosters"]), 6)
        self.assertTrue(all(p["path"].startswith("gate-unittest") for p in result["originals"]))

    def test_gate_controls_reject_foreign_source_omissions_and_boolean_counts(self):
        path, snapshot, python, receipt = self.gate_receipt_fixture()
        mutations = []
        foreign = copy.deepcopy(receipt); foreign["executed_source_sha"] = gate.FIXED["sha"]; mutations.append(foreign)
        omission = copy.deepcopy(receipt); omission["steps"].pop(); mutations.append(omission)
        boolean = copy.deepcopy(receipt); boolean["steps"][0]["counts"]["failures"] = False; mutations.append(boolean)
        skipped = copy.deepcopy(receipt); skipped["steps"][0]["status"] = "PASS_WITH_OMISSIONS"; mutations.append(skipped)
        for changed in mutations:
            path.write_text(json.dumps(changed))
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                gate.admit_gate_controls(path, snapshot, python, self.context)

    def test_gate_controls_reject_duplicate_ids_even_with_matching_log_hash(self):
        path, snapshot, python, receipt = self.gate_receipt_fixture()
        stream = receipt["steps"][0]["stderr"]
        original = path.parent / stream["file"]
        raw = original.read_bytes().replace(b"test_two", b"test_one")
        original.write_bytes(raw); stream["sha256"] = hashlib.sha256(raw).hexdigest()
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "roster"):
            gate.admit_gate_controls(path, snapshot, python, self.context)

    def test_local_gate_nonzero_then_unknown_receipt_stays_unknown(self):
        path, snapshot, python, receipt = self.gate_receipt_fixture()
        receipt.update(status="UNKNOWN", exit_code=1)
        path.write_text(json.dumps(receipt))
        calls = []
        def call(argv, cwd, env, output, label, timeout, context):
            calls.append(label)
            return {"exit_code": 1 if label == "gate-contracts" else 0, "argv": argv, "context": context}
        with self.assertRaises(gate.observer.UnknownExecution) as caught:
            gate.local_gate_g0(self.repo, snapshot, {"python": python}, {}, path.parent, self.context, call)
        self.assertEqual(caught.exception.command["runner_receipt"]["status"], "UNKNOWN")
        self.assertEqual(calls, ["gate-" + p for p in gate.GATE_POLICIES] + ["gate-contracts"])

    def test_nonzero_local_gate_policy_cannot_borrow_passing_contracts(self):
        path, snapshot, python, _ = self.gate_receipt_fixture()
        def call(argv, cwd, env, output, label, timeout, context):
            return {"exit_code": 1 if label == "gate-source_policy" else 0, "argv": argv, "context": context}
        with self.assertRaisesRegex(ValueError, "gate policy failed"):
            gate.local_gate_g0(self.repo, snapshot, {"python": python}, {}, path.parent, self.context, call)

    def test_local_gate_g0_positive_remains_separate_from_sdk_credit(self):
        path, snapshot, python, _ = self.gate_receipt_fixture()
        now = [0]
        budget = gate.Budget(500, lambda: now[0])
        original_deadline = budget.deadline
        requested, observed = [], []
        elapsed = {"gate-source_policy": 40, "gate-public_surface_policy": 20,
                   "gate-acceptance_policy": 400, "gate-creative_status_policy": 30,
                   "gate-contracts": 1}
        def delegate(argv, cwd, env, output, label, timeout, context):
            observed.append((label, timeout))
            now[0] += elapsed[label] * 1_000_000_000
            return {"exit_code": 0, "argv": argv, "context": context}
        def call(argv, cwd, env, output, label, timeout, context):
            requested.append((label, timeout))
            return budget.call(delegate, argv, cwd, env, output, label, timeout, context)
        with patch.object(gate, "source_unchanged"), patch.object(gate, "verify_tools"):
            result = gate.local_gate_g0(self.repo, snapshot, {"python": python}, {}, path.parent, self.context, call)
        labels = ["gate-source_policy", "gate-public_surface_policy", "gate-acceptance_policy",
                  "gate-creative_status_policy", "gate-contracts"]
        self.assertEqual(requested, list(zip(labels, (480, 480, 480, 480, 300))))
        self.assertEqual(observed, list(zip(labels, (480.0, 460.0, 440.0, 40.0, 10.0))))
        self.assertEqual(budget.deadline, original_deadline)
        self.assertEqual(budget.remaining(480), 9.0)
        self.assertEqual(result["status"], "PASS_EXACT_LOCAL_GATE_G0")
        self.assertEqual(result["executed_source_sha"], snapshot["sha"])
        self.assertEqual(result["sdk_g0_credit"], "NONE")

    def test_noncanonical_output_parent_is_rejected_before_creation(self):
        run_root = self.root / "future-output"
        self.assertEqual(gate.canonical_run_root(run_root), run_root)
        with patch.object(Path, "resolve", return_value=self.root / "foreign"), self.assertRaisesRegex(ValueError, "parent"):
            gate.canonical_run_root(run_root)
        self.assertFalse(run_root.exists())


def workflow_jobs(text):
    body = text.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(r"(?m)^  ([a-z0-9-]+):\n", body))
    return {m.group(1): body[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(body)]
            for i, m in enumerate(matches)}


def source_condition(expression, values):
    """Evaluate only the string comparisons/booleans in these source guards."""
    text = re.sub(r"(?:github|inputs)\.[a-z_]+", lambda m: repr(values.get(m.group(0), "")), expression)
    parsed = ast.parse(text.replace("&&", "and").replace("||", "or"), mode="eval")
    def value(node):
        if isinstance(node, ast.Constant) and type(node.value) is str:
            return node.value
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            parts = [value(n) for n in node.values]
            return all(parts) if isinstance(node.op, ast.And) else any(parts)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            equal = value(node.left) == value(node.comparators[0])
            return equal if isinstance(node.ops[0], ast.Eq) else not equal
        raise ValueError("unexpected guard expression; this is not a general Actions interpreter")
    return value(parsed.body)


LEGACY_BODY_DIGESTS = {'source-boundary': 'e50122f664f404d040457df904767fa36c56c5b7936bbd12b9b9f0dd4a35c998',
 'dependency-advisories': '89509a72b361c9a7b07a63e2551e933900a0790dddab0473430c325bbf61e1fa',
 'controlled-imports': '4ad8a401a889fb257cf1702cfd231c1200922a0a9e3b1b6c7e7d0b5b58e28330',
 'desktop-boundary': '6c6ad78ca1ee49c88a8c252eb91752eebdb5378a34bbdff9435eb5660db10a23',
 'dependency-review': '586f5822bcef9121fa9e2189c564a5143ff80fa6692ae2b83136d489dbc26e15',
 'v01-private-ntfs-evidence': 'c9eb6762cd1d8cdd670de3eb9cd3a3c0dade27280a1001a7e29482f82f8c7481',
 'source-admission-linux': '2da53b82a219e8a2bb570af09be0bd2f2db3c305f9d5a153e859d4df85d70b9b',
 'source-admission-ntfs': 'f86cd2dba566800b75ba3df7360829792b04ed0a65744d90972e87a4552837a7',
 'foundation-causal-g0': '4e15925fe7cf094fb730ddacfa2a7c91b7c1c72518f1f52ff4476bb6ff0c8148',
 'foundation-causal': 'fd062fd535bb1add7499c0612bb2a0ed732fb69a8033def780ae61f0efef5ade'}


class SDKFoundationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text()
        self.jobs = workflow_jobs(self.text)

    def condition(self, job, event, scope="full", review=True):
        branch = gate.REVIEW_BRANCH if review else "main"
        values = {"github.event_name": event, "inputs.verification_scope": scope,
                  "github.ref": "refs/heads/" + branch if event != "pull_request" else "refs/pull/1/merge",
                  "github.head_ref": branch if event == "pull_request" else ""}
        expression = re.search(r"(?m)^    if: (.+)$", self.jobs[job]).group(1)
        return source_condition(expression, values)

    def test_legacy_job_bodies_remain_byte_exact_after_only_review_guards(self):
        self.assertEqual(set(self.jobs), set(LEGACY_BODY_DIGESTS) | {"sdk-foundation-g0", "sdk-foundation-linux-two", "sdk-foundation-windows-g1"})
        for name, digest in LEGACY_BODY_DIGESTS.items():
            body = self.jobs[name]
            if name in {"source-boundary", "dependency-review"}:
                body = re.sub(r"(?m)^    if: .+\n", "", body, count=1)
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256(body.rstrip().encode()).hexdigest(), digest)

    def test_review_push_and_pull_request_select_only_new_source_job(self):
        for event in ("push", "pull_request"):
            with self.subTest(event=event):
                self.assertTrue(self.condition("sdk-foundation-g0", event))
                self.assertFalse(self.condition("source-boundary", event))
                self.assertFalse(self.condition("sdk-foundation-linux-two", event))
                self.assertFalse(self.condition("sdk-foundation-windows-g1", event))
                self.assertFalse(self.condition("dependency-review", event))

    def test_manual_full_and_legacy_foundation_preserve_behavior_on_review_branch(self):
        for review in (False, True):
            with self.subTest(review=review):
                self.assertTrue(self.condition("source-boundary", "workflow_dispatch", "full", review))
                self.assertFalse(self.condition("sdk-foundation-g0", "workflow_dispatch", "full", review))
                self.assertFalse(self.condition("source-boundary", "workflow_dispatch", "foundation-causal", review))
                self.assertTrue(self.condition("foundation-causal-g0", "workflow_dispatch", "foundation-causal", review))
                self.assertTrue(self.condition("foundation-causal", "workflow_dispatch", "foundation-causal", review))
                self.assertFalse(self.condition("sdk-foundation-g0", "workflow_dispatch", "foundation-causal", review))

    def test_new_manual_scope_does_not_fall_through_legacy_or_other_native_jobs(self):
        for scope in gate.SCOPES:
            with self.subTest(scope=scope):
                self.assertFalse(self.condition("source-boundary", "workflow_dispatch", scope))
                self.assertFalse(self.condition("foundation-causal", "workflow_dispatch", scope))
                self.assertFalse(self.condition("source-admission-linux", "workflow_dispatch", scope))
                self.assertFalse(self.condition("source-admission-ntfs", "workflow_dispatch", scope))
                self.assertTrue(self.condition("sdk-foundation-g0", "workflow_dispatch", scope))
                self.assertEqual(self.condition("sdk-foundation-linux-two", "workflow_dispatch", scope), scope == gate.SCOPES[1])
                self.assertEqual(self.condition("sdk-foundation-windows-g1", "workflow_dispatch", scope), scope == gate.HOSTED_SCOPE)
        self.assertEqual(re.findall(r"(?m)^    needs: (.+)$", self.jobs["sdk-foundation-linux-two"]), ["sdk-foundation-g0"])
        self.assertIn("        default: full\n", self.text)

    def test_remote_admission_precedes_source_checkout_and_no_windows_workflow_is_added(self):
        job = self.jobs["sdk-foundation-linux-two"]
        self.assertLess(job.index("--admit-only"), job.index("repository: seradotcom/semwright"))
        self.assertLess(job.index("--admit-only"), job.index("apt-get"))
        self.assertIn("/usr/bin/python3 -I -B gate/tooling/ci_sdk_foundation_candidate.py", job)
        self.assertIn("python3-jsonschema", job)
        self.assertIn("python3-yaml", job)
        self.assertIn('node-version: "22"', job)
        self.assertNotIn("sysctl", job)
        self.assertNotIn("apparmor", job)
        self.assertNotIn("windows-g1", job)
        self.assertIn("--expected-source \"$GITHUB_SHA\"", self.jobs["sdk-foundation-g0"])


HOSTED_UNCHANGED_KERNEL_SOURCE = {'validate_lease': 'ec98cda700bea93615e487f78488f99f3e1d920f1264e4233748544c34a07e63', 'full_snapshot': 'a222f3969e0405a5415a202bad1d6a11d942a4a73df8a5230d3fce301cc11c3b', 'source_unchanged': 'aa21fcf4d6b4d01b09eaaa692047d77d9738571148a3060482ef7e7377c4b825', 'sdk_g0': 'f635fab2284ae19a245723bfe4b4bfbb4c6370ba8d46f710a7013b60d939d5c1', 'producer_plan': '1729aa1650c9199ee7534cbc5e620accc69f9ddde4327497b2aa5dabca9d82ba', 'compiled_paths': 'ca2c07c5129ba6e724d54a31de601a7c21f533344b57bdde3ed5c3eef6469c61', 'prepare_role': '1e0984647890fdd351cf753585481baddef14c0d73084f0eab61ef851fe4018a', 'parse_roster': 'b08a60c15d800045cdd425eb4ca963e087d0a3c8217adfb054d29a714dd11f76', 'group_roster': '48d53599850b54ffc021eeb67374b718c8e982b340a4405bf67aa0ebca175c62', 'group_argv': '0df762a22414ab76d881a8ab4332460f41fa2bce0b2f4893299a294a3de34b25', 'admit_group': '77545de430e06e4ac7bf4df12c1ee73a325f7819022a5e937c627b34252aff2f', 'current_binary': 'c8853e30869663f936291c92a8802ce931f6a0323f758f8a36d43415420a84ea', 'list_role': 'dbb614d0bb25b3de3b9196196d263fea3f471d69b9f2c425331b8a561102b4a7', 'observe_causal': '92268fb5202bc5a142869fdc744f1aab8142a4b6359a88bd247863ce37e7aeec', 'observe_fixed_groups': '565239a9e4114c64bde21d75228d3843d397e8e5dedb32f4420d555bc25a2956', 'admit_gate_controls': 'd7fb8fdb0e755857045fb1385818910b20824ffb51511d9723829009b74c51a7', 'local_gate_g0': '1ea0d631217a6246651e85f8225f0a5da28632d1a728c5cb3929d979a19c2c57', 'execute_selection': 'a0637485f7bd2af50ddf8848de012a7dae9ef94105aab2c7f1d8b8f05dd5d0b9'}


class SDKFoundationHostedTests(unittest.TestCase):
    """Synthetic admission pairs; these controls never grant execution authority."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sdk-hosted-contracts-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "gate"; self.repo.mkdir()
        self.env = {
            "GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows", "RUNNER_ARCH": "X64",
            "RUNNER_ENVIRONMENT": "github-hosted", "IMAGEOS": gate.HOSTED_IMAGE_OS,
            "IMAGEVERSION": "20260101.1", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "SDK_FOUNDATION_SCOPE": gate.HOSTED_SCOPE, "GITHUB_JOB": gate.HOSTED_SCOPE,
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "12345",
            "GITHUB_SHA": "c" * 40, "GITHUB_WORKFLOW_SHA": "c" * 40,
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_WORKFLOW_REF": "example/repo/" + gate.WORKFLOW + "@refs/heads/review",
            "GITHUB_WORKSPACE": str(self.root),
        }
        sources = sorted([gate.FIXED["sha"], *(p["sha"] for p in gate.causal_profiles(gate.HOSTED_MODE))])
        self.auth = {
            "kind": "github-dispatch", "mode": gate.HOSTED_MODE, "source_sha": gate.FIXED["sha"],
            "review_sha256": "a" * 64, "max_total_seconds": 600,
            "binding": {
                "repository": self.env["GITHUB_REPOSITORY"], "workflow_ref": self.env["GITHUB_WORKFLOW_REF"],
                "gate_base_sha": "b" * 40, "runner_label": gate.HOSTED_RUNNER,
                "runner_image_os": gate.HOSTED_IMAGE_OS,
                "private_transport": {
                    "review_sha256": "d" * 64, "source_shas": sources,
                    "incoming": "OWNER_COMPOSED_FIVE_CHECKOUTS",
                    "outgoing": "OWNER_COMPOSED_PRIVATE_ORIGINALS_CUSTODY",
                    "private_sources_published": False,
                },
                "cold_growth": {
                    "basis_sha256": "e" * 64, "source_shas": sources, "compiled_configurations": 13,
                    "bytes": {name: 1024**2 for name in (
                        "source_transport", "source_checkouts", "toolchain", "cargo_downloads", "targets",
                        "sealed_binary_originals", "temporary_and_logs", "safety")},
                },
            },
        }

    def context(self, environment=None, authority=None, platform="win32"):
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": self.auth if authority is None else authority}):
            return gate.request_context(self.env if environment is None else environment,
                                        gate.HOSTED_MODE, gate.FIXED["sha"], "remote", platform)

    def test_hosted_context_accepts_windows_snapshot_keys_without_case_aliases(self):
        observed = self.context(dict(self.env))
        self.assertEqual(observed["image_os"], self.env["IMAGEOS"])
        self.assertEqual(observed["image_version"], self.env["IMAGEVERSION"])
        for canonical, other_case in (("IMAGEOS", "ImageOS"), ("IMAGEVERSION", "ImageVersion")):
            changed = dict(self.env)
            changed[other_case] = changed.pop(canonical)
            with self.subTest(key=canonical), self.assertRaises(ValueError):
                self.context(changed)

    def gate_fixture(self):
        context = self.context()
        old = b"from types import MappingProxyType\nREVIEWED_AUTHORITIES = MappingProxyType({})\ndef observed_code():\n    return 1\n"
        new = old.replace(b"MappingProxyType({})", ("MappingProxyType(" + repr({"remote": self.auth}) + ")").encode())
        path = self.repo / gate.HELPER; path.parent.mkdir(); path.write_bytes(new)
        base, actual = "b" * 40, "c" * 40
        before = ("100644 blob " + "1" * 40 + "\t" + gate.HELPER + "\0"
                  "100644 blob " + "3" * 40 + "\tunchanged.py\0").encode()
        after = before.replace(b"1" * 40, b"2" * 40)
        responses = {
            ("rev-list", "--parents", "-n", "1", "HEAD"): (actual + " " + base + "\n").encode(),
            ("ls-tree", "-r", "-z", base): before,
            ("ls-tree", "-r", "-z", actual): after,
            ("show", base + ":" + gate.HELPER): old,
            ("show", actual + ":" + gate.HELPER): new,
        }
        return context, path, responses

    def verify_gate(self, context, responses):
        def query(repo, *argv):
            self.assertEqual(repo, self.repo)
            return responses[argv]
        with patch.object(gate.custody, "git", side_effect=query):
            return gate.verify_hosted_gate(self.repo, context, self.auth, gate.Budget(60))

    def test_hosted_original_identity_remains_distinct_from_local_and_linux(self):
        value = self.context()
        self.assertEqual(value["execution_kind"], "GITHUB_WINDOWS")
        self.assertEqual(value["mode"], gate.HOSTED_MODE)
        self.assertEqual(value["github"]["GITHUB_SHA"], "c" * 40)
        self.assertEqual(value["github"]["GITHUB_WORKFLOW_SHA"], "c" * 40)
        self.assertEqual(value["runner_label"], "windows-2025")
        self.assertEqual(value["image_version"], "20260101.1")
        self.assertEqual(value["process_boundary"], "DIRECT_PROCESS_ONLY")
        self.assertFalse(value["continuous_resource_watchdog"])
        self.assertEqual(value["escaped_descendants"], "NOT_OBSERVED")
        expected = {
            "pending-original-red": "5f6f79ae1e31e035f294b46ef958883c1fd75c92",
            "effects-target-original-red": "859f0f9b9e1f461a977efcbd32c70a5a6a056968",
            "receiver-close-latch-red": "0ae99dfe35f91aef3cfe301fbafb6085c474c357",
            "refresh-remove-red": "cad4c0e2ef921edc398cf9cf3ac60cc8d53e7a5e",
        }
        hosted = gate.causal_profiles(gate.HOSTED_MODE)
        self.assertEqual({p["id"]: p["sha"] for p in hosted}, expected)
        self.assertIs(gate.causal_profiles("windows-g1"), gate.CAUSALS)
        self.assertEqual(gate.causal_profiles("linux-g2"), ())
        for local, public in zip(gate.CAUSALS, hosted):
            self.assertNotEqual(local["sha"], public["sha"])
            self.assertEqual({k: v for k, v in local.items() if k != "sha"},
                             {k: v for k, v in public.items() if k != "sha"})
        with self.assertRaisesRegex(ValueError, "unknown causal source"):
            gate.causal_profiles("same-tree-any-sha")

    def test_hosted_rejects_foreign_run_job_attempt_image_and_workflow(self):
        changes = (("GITHUB_ACTIONS", "false"), ("GITHUB_EVENT_NAME", "push"),
                   ("RUNNER_OS", "Linux"), ("RUNNER_ARCH", "ARM64"),
                   ("RUNNER_ENVIRONMENT", "self-hosted"), ("IMAGEOS", "win22"), ("IMAGEVERSION", ""),
                   ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_ATTEMPT", "2"),
                   ("GITHUB_JOB", "sdk-foundation-linux-two"), ("SDK_FOUNDATION_SCOPE", gate.SCOPES[1]),
                   ("GITHUB_SHA", "d" * 40), ("GITHUB_WORKFLOW_SHA", "d" * 40),
                   ("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_WORKFLOW_REF", "foreign"))
        for key, value in changes:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.context({**self.env, key: value})
        with self.assertRaises(ValueError):
            self.context(platform="linux")

    def test_local_mode_still_refuses_actions_instead_of_stripping_identity(self):
        local = {"kind": "local-lease", "mode": "windows-g1", "source_sha": gate.FIXED["sha"],
                 "review_sha256": "a" * 64, "max_total_seconds": 600,
                 "binding": {"lease_sha256": "b" * 64}}
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"local": local}):
            for environment in (self.env, {"GITHUB_RUN_ID": "12345"}, {"GITHUB_SHA": "c" * 40}):
                with self.subTest(environment=environment), self.assertRaisesRegex(ValueError, "impersonate"):
                    gate.request_context(environment, "windows-g1", gate.FIXED["sha"], "local", "win32")
        with self.assertRaisesRegex(ValueError, "authority mode"):
            self.context(authority={**self.auth, "kind": "local-lease"})
        original_case = SDKFoundationCandidateTests()
        original_case.root = self.root
        lease = original_case.lease()
        self.assertEqual(gate.validate_lease(lease, "b" * 64, "b" * 64,
                                            self.root / "run", 200, 600), lease)
        lease["source_shas"] = self.auth["binding"]["private_transport"]["source_shas"]
        with self.assertRaisesRegex(ValueError, "lease scope differs"):
            gate.validate_lease(lease, "b" * 64, "b" * 64, self.root / "run", 200, 600)

    def test_hosted_unknown_or_foreign_authority_stops_before_path_or_command(self):
        stale = copy.deepcopy(self.auth)
        stale_sources = sorted([gate.FIXED["sha"], *(p["sha"] for p in gate.CAUSALS)])
        stale["binding"]["private_transport"]["source_shas"] = stale_sources
        stale["binding"]["cold_growth"]["source_shas"] = stale_sources
        for catalogue, source in (({}, gate.FIXED["sha"]), ({"remote": self.auth}, "f" * 40),
                                  ({"remote": stale}, gate.FIXED["sha"])):
            with self.subTest(source=source), patch.object(gate, "REVIEWED_AUTHORITIES", catalogue), \
                    patch.object(gate.observer, "command") as command, \
                    patch.object(gate, "full_snapshot") as reader, \
                    patch.object(gate, "require_hosted_transport_composition") as transport, \
                    self.assertRaises(ValueError):
                gate.run(gate.HOSTED_MODE, source, "remote", self.repo, {}, self.root / "new", environment=self.env)
            command.assert_not_called(); reader.assert_not_called(); transport.assert_not_called()
            self.assertFalse((self.root / "new").exists())

    def test_hosted_private_transport_covers_exact_five_sources_both_directions(self):
        self.assertEqual(gate.hosted_binding(self.auth["binding"]), self.auth["binding"])
        for key, value in (("incoming", "PUBLIC_GIT_PUSH"), ("outgoing", "PUBLIC_ACTIONS_ARTIFACT"),
                           ("private_sources_published", True), ("source_shas", [gate.FIXED["sha"]]),
                           ("review_sha256", "self-approved")):
            changed = copy.deepcopy(self.auth)
            changed["binding"]["private_transport"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "private incoming"):
                self.context(authority=changed)
        selected = gate.causal_profiles(gate.HOSTED_MODE)
        for index, old in enumerate(gate.CAUSALS):
            for foreign_sha in (old["sha"], "f" * 40, selected[(index + 1) % 4]["sha"]):
                sources = [gate.FIXED["sha"], *(p["sha"] for p in selected)]
                sources[index + 1] = foreign_sha
                changed = copy.deepcopy(self.auth)
                changed["binding"]["private_transport"]["source_shas"] = sorted(sources)
                changed["binding"]["cold_growth"]["source_shas"] = sorted(sources)
                with self.subTest(role=old["id"], sha=foreign_sha), \
                        self.assertRaisesRegex(ValueError, "private incoming"):
                    self.context(authority=changed)

    def test_hosted_growth_requires_reviewed_typed_source_bound_components(self):
        variants = []
        for key, value in (("compiled_configurations", True), ("compiled_configurations", 12),
                           ("source_shas", [gate.FIXED["sha"]]), ("basis_sha256", "missing")):
            changed = copy.deepcopy(self.auth); changed["binding"]["cold_growth"][key] = value
            variants.append(changed)
        for value in (0, -1, True, 1.5, 257 * 1024**3):
            changed = copy.deepcopy(self.auth); changed["binding"]["cold_growth"]["bytes"]["targets"] = value
            variants.append(changed)
        changed = copy.deepcopy(self.auth); del changed["binding"]["cold_growth"]["bytes"]["sealed_binary_originals"]
        variants.append(changed)
        for changed in variants:
            with self.subTest(binding=changed["binding"]["cold_growth"]), self.assertRaisesRegex(ValueError, "cold growth"):
                self.context(authority=changed)

    def test_hosted_transport_cannot_be_activated_by_registry_or_boolean(self):
        context = self.context()
        for changed in (context, {**context, "transport_ready": True, "execute": True}):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "BLOCKED_PRIVATE_TRANSPORT_UNCOMPOSED"):
                gate.require_hosted_transport_composition(changed)
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": self.auth}), \
                patch.object(gate.sys, "platform", "win32"), \
                patch.object(gate.observer, "command") as command, \
                patch.object(gate, "full_snapshot") as reader, \
                self.assertRaisesRegex(ValueError, "BLOCKED_PRIVATE_TRANSPORT_UNCOMPOSED"):
            gate.run(gate.HOSTED_MODE, gate.FIXED["sha"], "remote", self.repo, {}, self.root / "new",
                     lease=self.root / "self-issued.json", environment=self.env)
        command.assert_not_called(); reader.assert_not_called()
        self.assertFalse((self.root / "new").exists())

    def test_hosted_paths_use_actual_workspace_without_an_E_drive_alias(self):
        sdk = self.root / "sdk"; sdk.mkdir()
        baselines = {}
        for profile in gate.CAUSALS:
            path = self.root / profile["id"]; path.mkdir(); baselines[profile["id"]] = path
        with patch.dict(gate.os.environ, {"GITHUB_WORKSPACE": str(self.root)}):
            gate.owned_paths(self.repo, sdk, baselines, self.root / "new", gate.HOSTED_MODE)
        with patch.dict(gate.os.environ, {"GITHUB_WORKSPACE": str(self.repo)}):
            with self.assertRaisesRegex(ValueError, "escaped the owned workspace"):
                gate.owned_paths(self.repo, sdk, baselines, self.root / "new", gate.HOSTED_MODE)
        self.assertFalse((self.root / "new").exists())

    def test_hosted_growth_keeps_original_floor_and_does_not_reset_origin(self):
        context = self.context(); total = sum(context["cold_growth"]["bytes"].values())
        floor = 18 * 1024**3; original = floor + total
        first = gate.hosted_growth_observation({"disk": original}, context)
        self.assertEqual(first["initial_free_bytes"], original)
        final = gate.hosted_growth_observation({"disk": floor}, context, original)
        self.assertEqual(final["observed_volume_delta_bytes"], total)
        self.assertFalse(final["exclusive_allocation_attribution"])
        self.assertFalse(final["continuous_resource_watchdog"])
        for disk, initial in ((original - 1, None), (floor - 1, original), (original, original - 1)):
            with self.subTest(disk=disk, initial=initial), self.assertRaises(ValueError):
                gate.hosted_growth_observation({"disk": disk}, context, initial)

    def hosted_tool_fixture(self, compiler_target="x86_64-w64-mingw32"):
        paths = {}
        for family, names in (("rust", ("rustup", "cargo", "rustc", "rustdoc", "cargo-fmt", "rustfmt")),
                              ("base", ("python", "git", "node", "bash")),
                              ("gnu", ("gcc", "g++", "ar", "as", "ld"))):
            directory = self.root / family; directory.mkdir()
            for name in names:
                path = directory / name; path.write_bytes(("observed-fixture-" + name).encode()); paths[name] = path
        calls = []
        def call(argv, cwd, env, output, label, timeout, context):
            calls.append((list(argv), dict(env), label))
            return {"exit_code": 0, "label": label}
        def stream(output, row, channel):
            label = row["label"]
            if label.startswith("tool-path-"):
                return (str(paths[label.removeprefix("tool-path-")]) + "\n").encode()
            if label.startswith("tool-target-"):
                return (compiler_target + "\n").encode()
            name = label.removeprefix("tool-version-")
            if name == "rustc":
                return b"rustc 1.98.1\nrelease: 1.98.1\nhost: x86_64-pc-windows-gnu\n"
            return (("v22.23.4" if name == "node" else name + " observed-version") + "\n").encode()
        return paths, calls, call, stream

    def test_hosted_toolchain_observes_GNU_target_and_binds_own_linker_without_identity_stripping(self):
        paths, calls, call, stream = self.hosted_tool_fixture()
        with patch.object(gate.sys, "executable", str(paths["python"])), \
                patch.object(gate.shutil, "which", side_effect=lambda name, **kw: str(paths[name])), \
                patch.object(gate.observer, "original_stream", side_effect=stream):
            tools, env = gate.observe_tools(self.repo, self.env, self.root, self.context(), call)
        self.assertEqual(tools["gcc"]["target"], "x86_64-w64-mingw32")
        self.assertEqual(tools["g++"]["target"], "x86_64-w64-mingw32")
        self.assertEqual(env["CC"], str(paths["gcc"]))
        self.assertEqual(env["CXX"], str(paths["g++"]))
        self.assertEqual(env["CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER"], str(paths["gcc"]))
        self.assertEqual(env["GITHUB_SHA"], self.env["GITHUB_SHA"])
        self.assertEqual(env["GITHUB_RUN_ID"], self.env["GITHUB_RUN_ID"])
        self.assertEqual(env["GITHUB_ACTIONS"], "true")
        resolutions = [argv for argv, _, label in calls if label.startswith("tool-path-")]
        self.assertEqual(len(resolutions), 5)
        self.assertTrue(all(argv[3] == gate.HOSTED_RUST for argv in resolutions))

    def test_hosted_toolchain_rejects_incompatible_compiler_before_any_Cargo_producer(self):
        paths, calls, call, stream = self.hosted_tool_fixture("i686-w64-mingw32")
        with patch.object(gate.sys, "executable", str(paths["python"])), \
                patch.object(gate.shutil, "which", side_effect=lambda name, **kw: str(paths[name])), \
                patch.object(gate.observer, "original_stream", side_effect=stream), \
                self.assertRaisesRegex(ValueError, "tool target is incompatible"):
            gate.observe_tools(self.repo, self.env, self.root, self.context(), call)
        self.assertFalse(any("test" in argv or "build" in argv for argv, _, _ in calls))

    def test_hosted_retains_the_same_resource_boundaries_and_compiler_exclusion(self):
        for initial, minima in ((True, (4, 8, 18)), (False, (4, 0, 18))):
            value = dict(zip(("physical", "virtual", "disk"), (n * 1024**3 for n in minima)))
            value["compilers"] = []
            self.assertEqual(gate.resource_values(value, gate.HOSTED_MODE, initial),
                             gate.resource_values(value, "windows-g1", initial))
            for name, minimum in zip(("physical", "virtual", "disk"), minima):
                if minimum:
                    with self.subTest(name=name, initial=initial), self.assertRaises(ValueError):
                        gate.resource_values({**value, name: minimum * 1024**3 - 1}, gate.HOSTED_MODE, initial)
            with self.assertRaisesRegex(ValueError, "compiler"):
                gate.resource_values({**value, "compilers": [{"Id": 123, "ProcessName": "rustc"}]}, gate.HOSTED_MODE, initial)

    def test_hosted_gate_accepts_only_literal_authority_child_and_reports_actual_SHA(self):
        context, _, responses = self.gate_fixture()
        result = self.verify_gate(context, responses)
        self.assertEqual(result["reviewed_gate_base_sha"], "b" * 40)
        self.assertEqual(result["executed_gate_sha"], "c" * 40)
        self.assertEqual(result["actual_workflow_sha"], "c" * 40)
        self.assertFalse(result["source_result_transferred"])

    def test_hosted_gate_rejects_foreign_parent_merge_and_changed_other_blob(self):
        context, _, original = self.gate_fixture()
        key = ("rev-list", "--parents", "-n", "1", "HEAD")
        for lineage in (("c" * 40 + " " + "d" * 40 + "\n").encode(),
                        ("c" * 40 + " " + "b" * 40 + " " + "d" * 40 + "\n").encode()):
            with self.subTest(lineage=lineage), self.assertRaisesRegex(ValueError, "ordinary child"):
                self.verify_gate(context, {**original, key: lineage})
        key = ("ls-tree", "-r", "-z", "c" * 40)
        for data in (original[key].replace(b"3" * 40, b"4" * 40),
                     original[key].replace(b"100644 blob " + b"2" * 40, b"100755 blob " + b"2" * 40)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.verify_gate(context, {**original, key: data})

    def test_hosted_gate_rejects_code_change_or_unrelated_registry_entry(self):
        context, path, original = self.gate_fixture()
        key = ("show", "c" * 40 + ":" + gate.HELPER)
        variants = [original[key].replace(b"return 1", b"return 2"),
                    original[key].replace(repr({"remote": self.auth}).encode(),
                                          repr({"remote": self.auth, "foreign": self.auth}).encode())]
        for data in variants:
            path.write_bytes(data)
            with self.subTest(data=data[-60:]), self.assertRaisesRegex(ValueError, "code or contains unrelated"):
                self.verify_gate(context, {**original, key: data})

    def test_hosted_gate_rejects_working_helper_different_from_its_Git_blob(self):
        context, path, responses = self.gate_fixture()
        path.write_bytes(path.read_bytes() + b"# local edit\n")
        with self.assertRaisesRegex(ValueError, "executed gate helper"):
            self.verify_gate(context, responses)

    def test_hosted_registry_ast_rejects_dynamic_duplicate_or_missing_assignment(self):
        for raw in (b"x=1\n", b"REVIEWED_AUTHORITIES = MappingProxyType(dict())\n",
                    b"REVIEWED_AUTHORITIES = MappingProxyType({'x':1,'x':2})\n",
                    b"REVIEWED_AUTHORITIES = MappingProxyType({})\nREVIEWED_AUTHORITIES = MappingProxyType({})\n"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                gate.catalogue_syntax(raw)

    def test_hosted_gate_query_keeps_existing_finite_bound_and_original_budget(self):
        context = self.context(); now = [0]; budget = gate.Budget(60, lambda: now[0])
        now[0] = 16_000_000_000
        with patch.object(gate.custody, "git") as query, self.assertRaisesRegex(ValueError, "insufficient original budget"):
            gate.verify_hosted_gate(self.repo, context, self.auth, budget)
        query.assert_not_called()
        self.assertEqual(budget.deadline, 60_000_000_000)

    def test_selection_G0_causal_and_original_admission_kernels_remain_reviewed(self):
        text = Path(gate.__file__).read_bytes().decode("utf-8")
        def source_digests(value):
            functions = {node.name: node for node in ast.parse(value).body if isinstance(node, ast.FunctionDef)}
            return {name: hashlib.sha256(ast.get_source_segment(value, functions[name]).encode("utf-8")).hexdigest()
                    for name in HOSTED_UNCHANGED_KERNEL_SOURCE}
        actual = source_digests(text)
        for name, expected in HOSTED_UNCHANGED_KERNEL_SOURCE.items():
            with self.subTest(name=name):
                self.assertEqual(actual[name], expected)
        # The historical source bytes stay authoritative across Python AST versions.
        # A changed executable statement still fails the same source comparison.
        changed = text.replace('stage["status"] = "PASS_G0_SDK_FOCAL"',
                               'stage["status"] = "UNREVIEWED_G0_PROMOTION"', 1)
        self.assertNotEqual(changed, text)
        with self.assertRaises(AssertionError):
            self.assertEqual(source_digests(changed), HOSTED_UNCHANGED_KERNEL_SOURCE)
        self.assertEqual(len(gate.CAUSALS), 4)
        self.assertEqual(len(gate.GROUPS), 13)
        self.assertEqual(sum(group[4] for group in gate.GROUPS), 134)
        self.assertTrue(gate.windows_selection(gate.HOSTED_MODE))
        self.assertFalse(gate.windows_selection("linux-g2"))
        self.assertEqual(gate.HOSTED_RUST, "1.98.1-x86_64-pc-windows-gnu")


class SDKFoundationHostedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text()
        self.jobs = workflow_jobs(self.text)

    def test_manual_hosted_job_needs_exact_source_G0_and_has_no_product_or_private_upload(self):
        job = self.jobs[gate.HOSTED_SCOPE]
        self.assertIn("if: github.event_name == 'workflow_dispatch' && inputs.verification_scope == 'sdk-foundation-windows-g1'", job)
        self.assertEqual(re.findall(r"(?m)^    needs: (.+)$", job), ["sdk-foundation-g0"])
        self.assertIn("runs-on: windows-2025", job)
        self.assertIn("timeout-minutes: 135", job)
        self.assertIn("--mode windows-hosted-g1", job)
        self.assertIn("--admit-only", job)
        self.assertNotIn("--execute", job)
        self.assertNotIn("--lease", job)
        self.assertNotIn("repository: seradotcom/semwright", job)
        self.assertNotIn("rust-toolchain@", job)
        self.assertNotIn("cargo test", job)
        self.assertNotIn("foundation-execution/evidence", job)
        self.assertIn("path: verification/sdk-foundation-hosted-admission", job)
        self.assertIn("$actualExit = $LASTEXITCODE", job)
        self.assertIn("if ($actualExit -ne 0)", job)
        self.assertNotIn("Remove-Item Env:GITHUB", job)
        self.assertNotIn("subst ", job)

    def test_third_scope_normalization_preserves_old_hash_and_rejects_one_foreign_byte(self):
        from tooling.tests import test_ci_foundation_causal as legacy
        case = legacy.FoundationCausalTests("test_existing_automatic_workflow_and_default_jobs_are_preserved")
        method = case.test_existing_automatic_workflow_and_default_jobs_are_preserved
        # Exercise the actual legacy normalizer, not a new duplicate of it.
        with patch.object(Path, "read_text", return_value=self.text):
            method()
        changed = self.text.replace("name: Security and Delivery", "name: Security and DeliveryX", 1)
        self.assertNotEqual(changed, self.text)
        with patch.object(Path, "read_text", return_value=changed), self.assertRaises(AssertionError):
            method()
        changed = self.text.replace("          - sdk-foundation-windows-g1\n", "          - unrelated-hosted-scope\n", 1)
        with patch.object(Path, "read_text", return_value=changed), self.assertRaises(AssertionError):
            method()



class SDKFoundationHostedCapacityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hosted-capacity-controls-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "gate"; self.repo.mkdir()
        (self.repo / "tooling").mkdir()
        self.temp = self.root / "temporary"; self.temp.mkdir()
        self.profile = self.root / "profile"; self.profile.mkdir()
        self.output_parent = self.root / "verification/sdk-foundation-hosted-admission"
        self.output_parent.mkdir(parents=True)
        self.env = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows", "RUNNER_ARCH": "X64",
                    "RUNNER_ENVIRONMENT": "github-hosted", "IMAGEOS": "win25-vs2026", "IMAGEVERSION": "20261001.1",
                    "GITHUB_EVENT_NAME": "workflow_dispatch", "SDK_FOUNDATION_SCOPE": gate.HOSTED_SCOPE,
                    "GITHUB_JOB": gate.HOSTED_SCOPE, "GITHUB_SHA": "c" * 40, "GITHUB_WORKFLOW_SHA": "c" * 40,
                    "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REPOSITORY": "example/repo",
                    "GITHUB_WORKFLOW_REF": "example/repo/" + gate.WORKFLOW + "@refs/heads/review",
                    "GITHUB_WORKSPACE": str(self.root), "RUNNER_TEMP": str(self.temp), "USERPROFILE": str(self.profile)}
        with patch.object(gate.sys, "platform", "win32"):
            self.context = gate.capacity_context(self.env)

    def memory(self):
        return {"physical": 4 * 1024**3, "virtual": 8 * 1024**3,
                "physical_total": 16 * 1024**3, "virtual_total": 32 * 1024**3, "compilers": []}

    def volume(self):
        return {"mount_path": "D:\\", "volume_guid_path": "\\\\?\\volume{11111111-1111-1111-1111-111111111111}\\",
                "caller_available_bytes": 18 * 1024**3, "caller_total_bytes": 64 * 1024**3,
                "volume_free_bytes": 20 * 1024**3,
                "api": "GetVolumePathNameW/GetVolumeNameForVolumeMountPointW/GetDiskFreeSpaceExW"}

    def value(self):
        paths = gate.capacity_paths(self.env, self.repo)
        return {"schema": gate.CAPACITY_SCHEMA, "context": self.context,
                "source": {"sha": "c" * 40}, "source_postcheck": "EXACT_GATE_SOURCE_UNCHANGED",
                "paths": {name: {"requested_path": str(path), "path_exists": True, "ancestor_only": False,
                                  "before": self.volume(), "after": self.volume(),
                                  "footprint": {"complete": True}} for name, path in paths.items()},
                "growth_reserve": {"status": "UNADMITTED_NO_COMPARABLE_BUILD_BASIS", "bytes": None}}

    def test_capacity_context_does_not_turn_an_empty_catalogue_into_sdk_authority(self):
        with patch.object(gate.sys, "platform", "win32"), patch.object(gate, "REVIEWED_AUTHORITIES", {}):
            observed = gate.capacity_context(self.env)
            self.assertEqual(observed["github"]["GITHUB_SHA"], "c" * 40)
            self.assertFalse(observed["sdk_execution_authorized"])
            self.assertEqual(observed["sdk_execution"], "NOT_RUN")
            for key, value in (("GITHUB_ACTIONS", "false"), ("GITHUB_EVENT_NAME", "push"),
                               ("RUNNER_ENVIRONMENT", "self-hosted"), ("IMAGEOS", "win22"),
                               ("GITHUB_JOB", "foreign"), ("GITHUB_WORKFLOW_SHA", "d" * 40),
                               ("GITHUB_RUN_ID", "0"), ("IMAGEVERSION", "")):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    gate.capacity_context({**self.env, key: value})
            with self.assertRaisesRegex(ValueError, "BLOCKED_PRIVATE_TRANSPORT"):
                gate.require_hosted_transport_composition({"mode": gate.HOSTED_MODE})

    def test_capacity_accepts_windows_snapshot_keys_without_case_aliases(self):
        with patch.object(gate.sys, "platform", "win32"):
            observed = gate.capacity_context(dict(self.env))
            self.assertEqual(observed["image_os"], self.env["IMAGEOS"])
            self.assertEqual(observed["image_version"], self.env["IMAGEVERSION"])
            self.assertFalse(observed["sdk_execution_authorized"])
            for canonical, other_case in (("IMAGEOS", "ImageOS"), ("IMAGEVERSION", "ImageVersion")):
                changed = dict(self.env)
                changed[other_case] = changed.pop(canonical)
                with self.subTest(key=canonical), self.assertRaises(ValueError):
                    gate.capacity_context(changed)

    def test_capacity_cli_refuses_execution_inputs_before_any_observer(self):
        for suffix in (("--execute",), ("--admit-only",), ("--authority", "reviewed"),
                       ("--sdk", "some-source"), ("--baseline", "fixed=source"),
                       ("--lease", "lease.json"), ("--run-root", "run"),
                       ("--source-sha", "a" * 40), ("--capacity-worker",)):
            with self.subTest(suffix=suffix), patch.object(gate, "observe_hosted_capacity") as called, \
                 patch.object(gate, "run") as product:
                with self.assertRaises(ValueError):
                    gate.main(["--mode", gate.HOSTED_MODE, "--observe-hosted-capacity", *suffix])
                called.assert_not_called(); product.assert_not_called()
        with patch.object(gate, "observe_hosted_capacity", return_value=0) as called:
            self.assertEqual(gate.main(["--mode", gate.HOSTED_MODE, "--observe-hosted-capacity"]), 0)
            called.assert_called_once_with()

    def test_capacity_paths_use_real_temp_and_absent_target_ancestor_without_mkdir(self):
        paths = gate.capacity_paths(self.env, self.repo)
        self.assertEqual(tuple(paths), gate.CAPACITY_ROLES)
        self.assertEqual(paths["runner_temp"], self.temp)
        self.assertEqual(paths["targets"], self.root / "verification/sdk-foundation-execution/targets")
        before = set(self.root.rglob("*"))
        actual = gate.capacity_anchor(paths["targets"])
        self.assertFalse(actual["path_exists"])
        self.assertTrue(actual["ancestor_only"])
        self.assertEqual(actual["physical_anchor"], str(self.root / "verification"))
        self.assertEqual(before, set(self.root.rglob("*")))
        linked_target = self.root / "file"; linked_target.write_bytes(b"not a directory")
        with self.assertRaisesRegex(ValueError, "not a directory"):
            gate.capacity_anchor(linked_target)

    def test_volume_api_preserves_real_guid_and_full_width_available_bytes(self):
        calls = []
        class Function:
            def __init__(self, callback): self.callback = callback
            def __call__(self, *args): return self.callback(*args)
        def mount(path, output, length):
            calls.append(("mount", path)); output.value = "D:\\mounted\\"; return 1
        def guid(path, output, length):
            calls.append(("guid", path.value)); output.value = self.volume()["volume_guid_path"].replace("volume{", "Volume{"); return 1
        def space(path, available, total, free):
            calls.append(("space", path))
            for pointer, value in ((available, 19 * 1024**3), (total, 80 * 1024**3), (free, 21 * 1024**3)):
                gate.ctypes.cast(pointer, gate.ctypes.POINTER(gate.ctypes.c_uint64)).contents.value = value
            return 1
        kernel = Mock(GetVolumePathNameW=Function(mount), GetVolumeNameForVolumeMountPointW=Function(guid),
                      GetDiskFreeSpaceExW=Function(space))
        with patch.object(gate.sys, "platform", "win32"), \
             patch.object(gate.ctypes, "WinDLL", return_value=kernel, create=True), \
             patch.object(gate.ctypes, "get_last_error", return_value=0, create=True):
            observed = gate.capacity_volume("D:\\mounted\\actual")
            self.assertEqual(observed["caller_available_bytes"], 19 * 1024**3)
            self.assertEqual(observed["volume_guid_path"], self.volume()["volume_guid_path"])
            self.assertEqual(calls, [("mount", "D:\\mounted\\actual"), ("guid", "D:\\mounted\\"),
                                     ("space", "D:\\mounted\\actual")])
            kernel.GetVolumePathNameW = Function(lambda *args: 0)
            calls.clear()
            with self.assertRaisesRegex(ValueError, "GetVolumePathNameW failed"):
                gate.capacity_volume("D:\\mounted\\actual")
            self.assertEqual(calls, [])

    def test_insufficient_or_shared_volume_capacity_is_preserved_without_admission(self):
        result = gate.admit_capacity(self.memory(), self.value(), self.context)
        self.assertTrue(result["initial_4_8_18_floor_satisfied"])
        self.assertFalse(result["growth_reserve_admitted"])
        self.assertFalse(result["sdk_execution_authorized"])
        self.assertEqual(result["growth_reserve"]["bytes"], None)
        self.assertNotIn("aggregate_free_bytes", result)
        for role in ("runner_temp", "targets", "cargo_home"):
            changed = self.value(); changed["paths"][role]["after"]["caller_available_bytes"] -= 1
            observed = gate.admit_capacity(self.memory(), changed, self.context)
            self.assertFalse(observed["initial_4_8_18_floor_satisfied"])
            self.assertEqual(observed["paths"][role]["after"]["caller_available_bytes"], 18 * 1024**3 - 1)
        for key in ("physical", "virtual"):
            memory = self.memory(); memory[key] -= 1
            self.assertFalse(gate.admit_capacity(memory, self.value(), self.context)["initial_4_8_18_floor_satisfied"])
        occupied = self.memory(); occupied["compilers"] = [{"Id": 99, "ProcessName": "rustc"}]
        self.assertFalse(gate.admit_capacity(occupied, self.value(), self.context)["initial_4_8_18_floor_satisfied"])

    def test_capacity_rejects_boolean_memory_foreign_source_volume_or_path_roster(self):
        memory = self.memory(); memory["physical"] = True
        with self.assertRaises(ValueError): gate.admit_capacity(memory, self.value(), self.context)
        for mutation in (lambda value: value["source"].update(sha="f" * 40),
                         lambda value: value["paths"].pop("targets"),
                         lambda value: value["paths"]["targets"]["after"].update(volume_guid_path="D:"),
                         lambda value: value["paths"]["targets"]["after"].update(caller_available_bytes=True)):
            value = self.value(); mutation(value)
            with self.assertRaises(ValueError): gate.admit_capacity(self.memory(), value, self.context)
        value = self.value(); value["paths"]["targets"]["footprint"]["complete"] = False
        self.assertEqual(gate.admit_capacity(self.memory(), value, self.context)["status"],
                         "CAPACITY_PARTIALLY_OBSERVED_NOT_ADMISSION")

    def test_footprint_real_metadata_and_absence_do_not_claim_allocated_or_cold_bytes(self):
        first = self.temp / "one"; first.write_bytes(b"abc")
        (self.temp / "sub").mkdir(); (self.temp / "sub/two").write_bytes(b"de")
        result = gate.capacity_footprint(self.temp, gate.time.monotonic_ns() + 10_000_000_000)
        self.assertTrue(result["complete"])
        self.assertEqual((result["entries"], result["files"], result["logical_bytes"]), (3, 2, 5))
        self.assertIsNone(result["allocated_bytes"])
        self.assertFalse(result["content_hashes_measured"])
        first.write_bytes(b"changed")
        changed = gate.capacity_footprint(self.temp, gate.time.monotonic_ns() + 10_000_000_000)
        self.assertNotEqual(result["metadata_sha256"], changed["metadata_sha256"])
        absent = gate.capacity_footprint(self.temp / "absent", gate.time.monotonic_ns() + 10_000_000_000)
        self.assertEqual(absent["state"], "ABSENT_NOT_A_MEASURED_CACHE")
        self.assertIsNone(absent["logical_bytes"])
        self.assertIsNone(absent["metadata_sha256"])

    def test_footprint_limits_preserve_partial_metadata_without_retry(self):
        for index in range(3): (self.temp / str(index)).write_bytes(b"xx")
        with patch.object(gate, "CAPACITY_MAX_ENTRIES", 2):
            result = gate.capacity_footprint(self.temp, gate.time.monotonic_ns() + 10_000_000_000)
            self.assertFalse(result["complete"])
            self.assertIn("namespace", result["error"])
            self.assertEqual(len(result["metadata_sha256"]), 64)
        with patch.object(gate.os, "scandir") as scan:
            result = gate.capacity_footprint(self.temp, 1)
            scan.assert_not_called()
            self.assertFalse(result["complete"])
            self.assertIn("time budget", result["error"])
        with patch.object(gate, "CAPACITY_MAX_LOGICAL_BYTES", 1):
            result = gate.capacity_footprint(self.temp, gate.time.monotonic_ns() + 10_000_000_000)
            self.assertFalse(result["complete"])
            self.assertIn("byte budget", result["error"])
            self.assertEqual(result["logical_bytes"], 2)

    def test_capacity_worker_binds_source_and_rejects_changed_volume_before_publication(self):
        snapshot = {"sha": "c" * 40, "files": {path: {"sha256": digest} for path, digest in gate.GATE_PINS.items()}}
        changed = self.volume(); changed["volume_guid_path"] = "\\\\?\\volume{22222222-2222-2222-2222-222222222222}\\"
        with patch.object(gate, "__file__", str(self.repo / gate.HELPER)), \
             patch.object(gate.sys, "platform", "win32"), \
             patch.object(gate.custody, "source_snapshot", return_value=snapshot) as source, \
             patch.object(gate, "source_unchanged") as post, \
             patch.object(gate, "capacity_volume", side_effect=[self.volume(), changed]) as volume, \
             patch.object(gate, "capacity_footprint", return_value={"complete": True}):
            with self.assertRaisesRegex(ValueError, "volume identity changed"):
                gate.capacity_worker(self.env, gate.time.monotonic_ns() + 30_000_000_000)
            source.assert_called_once_with(self.repo, "c" * 40, gate.GATE_FILES)
            self.assertEqual(volume.call_count, 2)
            post.assert_not_called()
        with patch.object(gate.sys, "platform", "win32"), patch.object(gate.custody, "source_snapshot") as source:
            with self.assertRaises(ValueError): gate.capacity_worker(self.env, 1)
            source.assert_not_called()

    def test_capacity_unknown_retains_original_command_and_never_runs_second_query_or_sdk(self):
        tool = self.root / "powershell.exe"; tool.write_bytes(b"fixture tool")
        original = {"label": "memory-and-compilers", "observation": "UNKNOWN_TIMEOUT_NO_RETRY", "exit_code": None}
        with patch.object(gate, "__file__", str(self.repo / gate.HELPER)), \
             patch.object(gate.sys, "platform", "win32"), patch.object(gate.shutil, "which", return_value=str(tool)), \
             patch.object(gate.observer, "command", side_effect=gate.observer.UnknownExecution("original", original)) as called, \
             patch.object(gate, "execute_selection") as sdk, patch.object(gate, "authority") as admission, patch("builtins.print"):
            self.assertEqual(gate.observe_hosted_capacity(self.env), 124)
            self.assertEqual(called.call_count, 1)
            sdk.assert_not_called(); admission.assert_not_called()
        receipt = json.loads((self.output_parent / "capacity/receipt.json").read_text())
        self.assertEqual(receipt["status"], "UNKNOWN_CAPACITY_NO_RETRY")
        self.assertEqual(receipt["uncertain_command"], original)
        self.assertFalse(receipt["process_tree_closed"])
        self.assertFalse(receipt["sdk_execution_authorized"])

    def test_capacity_success_keeps_original_deadline_and_observed_arguments(self):
        tool = self.root / "powershell.exe"; tool.write_bytes(b"fixture tool")
        value = self.value()
        calls = []
        def observed(argv, cwd, env, output, label, timeout, context, observations):
            calls.append((argv, timeout, label, context))
            return {"exit_code": 0, "label": label}
        def stream(output, row, which):
            return json.dumps(self.memory() if row["label"] == "memory-and-compilers" else value).encode()
        with patch.object(gate, "__file__", str(self.repo / gate.HELPER)), \
             patch.object(gate.sys, "platform", "win32"), patch.object(gate.shutil, "which", return_value=str(tool)), \
             patch.object(gate.observer, "command", side_effect=observed), \
             patch.object(gate.observer, "original_stream", side_effect=stream), \
             patch.object(gate.producer, "verify_commands") as readback, patch.object(gate, "execute_selection") as sdk, patch("builtins.print"):
            self.assertEqual(gate.observe_hosted_capacity(self.env), 0)
            readback.assert_called_once(); sdk.assert_not_called()
        receipt = json.loads((self.output_parent / "capacity/receipt.json").read_text())
        self.assertEqual([call[2] for call in calls], ["memory-and-compilers", "volumes-and-footprints"])
        self.assertEqual([call[1] for call in calls], [45, 60])
        self.assertEqual(calls[1][0][-2:], ["--capacity-deadline-ns", str(receipt["original_deadline_monotonic_ns"])])
        self.assertIn("Get-Process -ErrorAction Stop", calls[0][0][-1])
        self.assertEqual(receipt["status"], "CAPACITY_OBSERVED_NOT_ADMISSION")
        self.assertFalse(receipt["growth_reserve_admitted"])

    def test_capacity_workflow_observes_before_closed_admission_and_retains_originals(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text()
        text = restore_transport_workflow(text)
        job = workflow_jobs(text)[gate.HOSTED_SCOPE]
        capacity = job.index("      - name: Observe standard hosted capacity without SDK execution\n")
        admission = job.index("      - name: Admit exact hosted gate and reject uncomposed private transport\n")
        self.assertLess(capacity, admission)
        block = job[capacity:admission]
        self.assertIn("timeout-minutes: 3", block)
        self.assertIn("--mode windows-hosted-g1 --observe-hosted-capacity", block)
        self.assertIn("$actualExit = $LASTEXITCODE", block)
        self.assertIn("if ($actualExit -ne 0)", block)
        self.assertNotIn("--authority", block)
        self.assertNotIn("--execute", job)
        self.assertIn("if: always()", job)
        self.assertIn("path: verification/sdk-foundation-hosted-admission", job)
        checkout_config = ('        env:\n          GIT_CONFIG_COUNT: "1"\n'
                           '          GIT_CONFIG_KEY_0: core.autocrlf\n          GIT_CONFIG_VALUE_0: "false"\n')
        self.assertEqual(job.count(checkout_config), 1)
        restored = text.replace(block, "", 1).replace(checkout_config, "", 1)
        self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(),
                         "2acac408724007c100f99506d1e24a73cbab55cc84ef427957bbb8271c741086")

    def test_capacity_observed_windows2025_image_is_exact_and_old_alias_rejected(self):
        self.assertEqual(gate.HOSTED_IMAGE_OS, "win25-vs2026")
        with patch.object(gate.sys, "platform", "win32"):
            context = gate.capacity_context(self.env)
            self.assertEqual(context["image_os"], "win25-vs2026")
            self.assertFalse(context["sdk_execution_authorized"])
            for image in ("win25", "win22", "win25-vs2026-other", "", "win25-vs2026\n"):
                with self.subTest(image=image), self.assertRaisesRegex(ValueError, "actual standard hosted job"):
                    gate.capacity_context({**self.env, "IMAGEOS": image})

    def test_capacity_rejection_reports_only_bounded_public_provenance_keys(self):
        environment = {**self.env, "IMAGEOS": "other" + "x" * 500,
                       "SECRET_UNRELATED": "NEVER-PRINT-THIS-SECRET"}
        with patch.object(gate.sys, "platform", "win32"):
            with self.assertRaises(ValueError) as caught:
                gate.capacity_context(environment)
        message = str(caught.exception)
        self.assertNotIn("SECRET_UNRELATED", message)
        self.assertNotIn("NEVER-PRINT-THIS-SECRET", message)
        data = json.loads(message.split(": ", 1)[1])
        self.assertEqual(set(data["observed"]), set(data["expected"]))
        self.assertEqual(len(data["observed"]["IMAGEOS"]), 128)
        self.assertEqual(data["expected"]["IMAGEOS"], "win25-vs2026")

    def test_hosted_checkout_preserves_exact_git_bytes_before_capacity_snapshot(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text()
        job = workflow_jobs(text)[gate.HOSTED_SCOPE]
        checkout = job.index("      - uses: actions/checkout@")
        setup = job.index("      - uses: actions/setup-python@")
        block = job[checkout:setup]
        self.assertIn('GIT_CONFIG_COUNT: "1"', block)
        self.assertIn("GIT_CONFIG_KEY_0: core.autocrlf", block)
        self.assertIn('GIT_CONFIG_VALUE_0: "false"', block)
        self.assertNotIn("ImageOS:", block)
        self.assertNotIn("GITHUB_SHA:", block)
        self.assertNotIn("RUNNER_ENVIRONMENT:", block)
        self.assertNotIn("git reset", job)



HOSTED_TRANSPORT_WORKFLOW_BLOCK_SHA256 = 'fe5800bff0afa1e711a235dbbf528733308ca68335b0498ea91e209e81b06ca4'
HOSTED_TRANSPORT_INPUT_FIXTURE = '      sdk_foundation_transport:\n        description: Closed public transport review and recipient input (not authority)\n        required: false\n        type: string\n        default: ""\n'
HOSTED_PUBLIC_RECIPIENT_FIXTURE = b'{"sha256":"1ca6d673bd8c5ea4d0378310b25c254452c19504d7e784ab94b8a8d2d1115921","spki_der_base64":"MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAuBV28ghryANKaknGk4Hupo7Cri2hdksBVE02t4BFn/87PGxvTEs+t6ZIsSUcP7LE8OXQK6EuobNPwSCM5l92TZLKtX7IgtCxRntdBjlsk0eGZHKa87kdakq9WiSRaNzLkUYuBYjvPlqBkhfPQW3ijlc37hdLCc574VDHAq/GCmLuJWJz7YfOi325CRkiPcIdFiZ0QMNnONZjXZagWNPyps0GPXOjrvqMeQ4DV2i3gTn8lUn99IpLQvncRmvyPTG//nId+G0B9rqmuD2bG9pQs1L2irS81Zb38w9CnvK8F+3XVXBRh23FgwC/e51iukw70CDEbNSF4kylSbOJlO0vlY0zLiR+R0eUIQQl3M7o7kLN4Lc4E6jKUWprZREaIr8/jcC/g6e49X+n7OjdHcECv1xJ8BeGu9MpFu45uyyXaD+G16JpyNN9ez0omv/6FZsB3MfnJ88ehOkH0I0ohDb/yKNm4CgSInGU0BJiS95XK4ar58vAzVHEsUJqj91pE8tFAgMBAAE="}\n'


def restore_transport_workflow(text):
    """Reverse only these named transport additions, preserving the historical oracle."""
    if text.count(HOSTED_TRANSPORT_INPUT_FIXTURE) != 1:
        raise AssertionError("closed transport input is missing, duplicated or changed")
    text = text.replace(HOSTED_TRANSPORT_INPUT_FIXTURE, "", 1)
    prefix, job = text.split("\n  sdk-foundation-windows-g1:\n", 1)
    begin = job.index("      # BEGIN Foundation ciphertext transport preparation\n")
    end_marker = "      # END Foundation ciphertext transport preparation\n"
    end = job.index(end_marker, begin) + len(end_marker)
    block = job[begin:end]
    if hashlib.sha256(block.encode()).hexdigest() != HOSTED_TRANSPORT_WORKFLOW_BLOCK_SHA256:
        raise AssertionError("known ciphertext controller/upload addition differs")
    old_comment = ("      # No private source is fetched or published by this proposal. The exact\n"
                   "      # input transport and private output custody must be composed into a new\n"
                   "      # reviewed gate base before the controller's explicit blocker is replaced.\n"
                   "      # Do not insert a boolean bypass, map E:, or clear actual Actions identity.\n")
    job = job[:begin] + old_comment + job[end:]
    replacements = (
        ("    timeout-minutes: 135\n    permissions:\n      contents: read\n      actions: read\n", "    timeout-minutes: 135\n"),
        ("      SDK_FOUNDATION_TRANSPORT: ${{ inputs.sdk_foundation_transport }}\n", ""),
        ("      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262\n        timeout-minutes: 2\n",
         "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262\n"),
        ("      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065\n        timeout-minutes: 3\n",
         "      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065\n"),
        ("      - name: Admit exact hosted gate and bound public transport input\n        id: foundation_admission\n        timeout-minutes: 2\n        shell: pwsh\n",
         "      - name: Admit exact hosted gate and reject uncomposed private transport\n        shell: pwsh\n"),
        ("      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02\n        if: always()\n        timeout-minutes: 1\n",
         "      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02\n        if: always()\n"),
    )
    for current, prior in replacements:
        if job.count(current) != 1:
            raise AssertionError("explicit hosted transport delta is missing, duplicated or changed")
        job = job.replace(current, prior, 1)
    result = prefix + "\n  sdk-foundation-windows-g1:\n" + job
    if hashlib.sha256(result.encode()).hexdigest() != "6e72f2bb01e79fc180b1f1eb9c34165585237ec3dc28815f0a719f6907a5bed4":
        raise AssertionError("transport changed another historical workflow byte")
    return result


class SDKFoundationPrivateTransportTests(unittest.TestCase):
    """Source controls; the crypto module is a call-recording fixture, not a new cipher."""
    def setUp(self):
        import io
        import tarfile
        self.io, self.tarfile = io, tarfile
        temporary = tempfile.TemporaryDirectory(prefix="foundation-private-transport-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.evidence = self.root / "evidence"; self.evidence.mkdir()
        self.private = self.root / "private"; self.private.mkdir()
        self.outgoing = self.root / "outgoing"; self.outgoing.mkdir()
        source_case = SDKFoundationHostedTests()
        source_case.setUp(); self.addCleanup(source_case.doCleanups)
        source_case.auth["max_total_seconds"] = 7200
        self.auth, self.context = source_case.auth, source_case.context()
        self.crypto = Mock()
        self.crypto.public_key.return_value = "synthetic-call-only-public-key"
        self.crypto.gather_snapshot.side_effect = self.fixture_snapshot
        self.crypto.encrypt_snapshot.side_effect = lambda raw, recipient, aad: b"SYNTHETIC-CIPHER:" + hashlib.sha256(raw).digest()
        self.crypto.new_bytes.side_effect = self.write_bytes
        self.crypto.new_json.side_effect = lambda path, value: self.write_bytes(path, gate.transport_json(value))
        self.recipient = {"sha256": gate.HOSTED_TRANSPORT_SPKI_SHA256, "spki_der_base64": "SOURCE-CONTROL-NOT-A-REAL-KEY"}
        self.started = {"schema": gate.SCHEMA, "context": self.context,
                        "original_started_monotonic_ns": 1000000000,
                        "original_deadline_monotonic_ns": 7201000000000, "budget_seconds": 7200}

    def write_bytes(self, path, data):
        with path.open("xb") as stream: stream.write(data)
        self.assertEqual(path.read_bytes(), data)

    def fixture_snapshot(self, roots, *, preserve_partial):
        self.assertTrue(preserve_partial)
        self.assertEqual(set(roots), {"files"})
        rows = [("files/" + path.relative_to(roots["files"]).as_posix(), path.read_bytes())
                for path in sorted(roots["files"].rglob("*")) if path.is_file()]
        metadata = {"schema": "motionwright.ci.private-snapshot/1", "selected_originals_complete": True,
                    "complete_owner_state_claimed": False, "roots": {}}
        rows.append(("snapshot-manifest.json", gate.transport_json(metadata)))
        stream = self.io.BytesIO()
        with self.tarfile.open(fileobj=stream, mode="w") as archive:
            for name, data in rows:
                member = self.tarfile.TarInfo(name); member.size = len(data)
                archive.addfile(member, self.io.BytesIO(data))
        return stream.getvalue(), True

    def existing_snapshot_functions(self):
        """Load only the exact regular-file/snapshot AST; no cryptography backend."""
        root = Path(gate.__file__).resolve().parents[1]
        scope = {"Path": Path, "os": gate.os, "stat": gate.stat, "io": self.io,
                 "tarfile": self.tarfile, "re": re, "json": json, "hashlib": hashlib,
                 "require": gate.require, "digest": lambda raw: hashlib.sha256(raw).hexdigest(),
                 "MAX_FILE": 256 * 1024**2, "MAX_TOTAL": 512 * 1024**2, "MAX_FILES": 4096}
        selections = (("tooling/mlt_official_runtime_intake.py", ("capture_regular",)),
                      ("tooling/ci_private_evidence.py", ("json_bytes", "metadata_identity", "snapshot_member", "regular", "gather_snapshot")))
        for path, names in selections:
            raw = (root / path).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), gate.HOSTED_TRANSPORT_PINS[path])
            tree = ast.parse(raw)
            nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
            self.assertEqual({node.name for node in nodes}, set(names))
            exec(compile(ast.Module(body=nodes, type_ignores=[]), str(root / path), "exec"), scope)
        return scope["gather_snapshot"]

    def row(self, path="started.json", data=b"retained original", role="run-started"):
        return {"path": path, "role": role, "state": "PRESENT", "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def seal_fixture(self):
        data = b"retained original"
        (self.evidence / "started.json").write_bytes(data)
        rows = [self.row(data=data)]
        return gate.transport_seal_parts(self.evidence, self.private, self.outgoing, rows,
            self.context, self.recipient, self.crypto, lambda: 1)

    def descriptor(self, number=101):
        return {"artifact_id": number, "run_id": 12345, "run_attempt": 1,
                "source_sha": "c" * 40, "zip_bytes": 100, "zip_sha256": format(number, "064x")}

    def uploaded(self, sealed):
        return {row["id"]: {"descriptor": self.descriptor(101 + i), "member": row["id"] + ".bin",
                "ciphertext_bytes": row["ciphertext_bytes"], "ciphertext_sha256": row["ciphertext_sha256"]}
                for i, row in enumerate(sealed["parts"])}

    def test_private_transport_recipe_keeps_exact_five_sources_thirteen_configs_and_no_authority(self):
        value = gate.hosted_transport_recipe()
        self.assertEqual(len(value["sources"]), 5)
        self.assertEqual({row["sha"] for row in value["sources"]},
                         {gate.FIXED["sha"], *gate.HOSTED_CAUSAL_SHAS.values()})
        self.assertEqual(len(value["compiled_configurations"]), 13)
        self.assertEqual(sum(row["role"] == "fixed" for row in value["compiled_configurations"]), 9)
        self.assertEqual(value["recipient_public_sha256"], "c3204ef97fb30495e1a40f373fe75da47eb0e0cb155fbe3539e4cdf0b6b3811a")
        self.assertEqual(value["recipient_spki_sha256"], "1ca6d673bd8c5ea4d0378310b25c254452c19504d7e784ab94b8a8d2d1115921")
        self.assertFalse(value["sdk_execution_authorized"])
        self.assertFalse(value["upload_confirmed"])
        self.assertEqual(value["whole_build_growth"], "UNADMITTED_NO_COMPARABLE_BASIS")
        self.assertEqual(gate.HOSTED_TRANSPORT_CIPHER_MAX, 256 * 1024**2)
        self.assertEqual(gate.HOSTED_TRANSPORT_TOTAL_MAX, 512 * 1024**2)

    def test_private_transport_no_empty_or_populated_catalogue_can_replace_technical_bootstrap(self):
        with patch.object(gate, "REVIEWED_AUTHORITIES", {}), patch.object(gate.Path, "resolve") as resolve:
            with self.assertRaisesRegex(ValueError, "authority is not reviewed"):
                gate.transport_entry("data", "remote", {})
            resolve.assert_not_called()
        with patch.object(gate, "request_context", return_value=self.context), \
                patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": self.auth}), \
                patch.object(gate.Path, "resolve") as resolve:
            with self.assertRaisesRegex(ValueError, "BLOCKED_PRIVATE_TRANSPORT_UNCOMPOSED"):
                gate.run(gate.HOSTED_MODE, gate.FIXED["sha"], "remote", self.root, {}, self.root / "run", environment={})
            resolve.assert_not_called()

    def test_private_transport_review_is_exact_and_bound_to_fixed_recipient_recipe_and_growth(self):
        value = {"schema": gate.HOSTED_TRANSPORT_REVIEW_SCHEMA, "purpose": gate.HOSTED_FULL_PURPOSE,
                 "gate_base_sha": self.auth["binding"]["gate_base_sha"],
                 "recipe_sha256": hashlib.sha256(gate.transport_json(gate.hosted_transport_recipe())).hexdigest(),
                 "recipient_public_sha256": gate.HOSTED_TRANSPORT_RECIPIENT_SHA256,
                 "recipient_spki_sha256": gate.HOSTED_TRANSPORT_SPKI_SHA256,
                 "cold_growth_basis_sha256": self.auth["binding"]["cold_growth"]["basis_sha256"]}
        raw = gate.transport_json(value); digest = hashlib.sha256(raw).hexdigest()
        self.auth["binding"]["private_transport"]["review_sha256"] = digest
        context = {**self.context, "private_transport_review_sha256": digest}
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": self.auth}):
            self.assertEqual(gate.transport_review(raw, context), value)
            for changed in (raw + b"\n", gate.transport_json({**value, "recipient_spki_sha256": "f" * 64}),
                            gate.transport_json({**value, "cold_growth_basis_sha256": "f" * 64})):
                with self.subTest(raw=changed[:40]), self.assertRaises(ValueError):
                    gate.transport_review(changed, context)
            with self.assertRaises(ValueError): gate.transport_review(raw, {**context, "product_source_sha": "f" * 40})

    def test_private_transport_rows_reject_aliases_types_oversize_and_unselected_plaintext(self):
        row = self.row()
        self.assertEqual(gate.transport_rows([row]), [row])
        for changed in ({**row, "bytes": True}, {**row, "bytes": gate.HOSTED_TRANSPORT_PAYLOAD_MAX + 1},
                        {**row, "path": "../started.json"}, {**row, "path": "target/binary"},
                        {**row, "path": "lease.json"}, {**row, "path": "controller/../started.json"},
                        {**row, "role": "compiled-binary"}, {**row, "state": "UNKNOWN"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError): gate.transport_rows([changed])
        with self.assertRaises(ValueError): gate.transport_rows([row, row])
        missing = {**row, "state": "MISSING", "bytes": None, "sha256": None}
        self.assertEqual(gate.transport_rows([missing]), [missing])

    def test_private_transport_partition_has_fifteen_bound_parts_without_fragmenting_binaries(self):
        binaries = [{"path": "binaries/" + format(i, "064x"), "role": "compiled-binary", "state": "PRESENT",
                     "bytes": 5, "sha256": format(i, "064x")} for i in range(1, 14)]
        ordinary = [self.row("commands/case-" + str(i) + ".stdout.log", b"1234", "command-original") for i in range(2)]
        with patch.object(gate, "HOSTED_TRANSPORT_PAYLOAD_MAX", 5), patch.object(gate, "HOSTED_TRANSPORT_TOTAL_MAX", 100):
            plan = gate.transport_partition_plan(binaries + ordinary)
            self.assertEqual(len(plan["parts"]), 15)
            self.assertTrue(all(len(part["paths"]) == 1 for part in plan["parts"]))
            self.assertEqual(len({path for part in plan["parts"] for path in part["paths"]}), 15)
            self.assertEqual(gate.HOSTED_TRANSPORT_DATA_PARTS + 1, 16)
            self.assertEqual(gate.transport_unpack_roster(gate.transport_pack_roster(plan)), plan)
            with self.assertRaisesRegex(ValueError, "data partition budget exhausted"):
                gate.transport_partition_plan(binaries + ordinary + [self.row("commands/extra.stdout.log", b"1234", "command-original")])
            extra = {**binaries[-1], "path": "binaries/" + "f" * 64, "sha256": "f" * 64}
            with self.assertRaises(ValueError): gate.transport_partition_plan(binaries + [extra])
        # The old payload-only split put all1735 small blobs into one manifest.
        # Exercise the real pinned gather_snapshot and its three physical maps.
        gather = self.existing_snapshot_functions()
        bulk = self.root / "bulk"; originals = bulk / "originals"; originals.mkdir(parents=True)
        source_rows = []
        for number in range(1735):
            data = ("source-fixture-" + str(number)).encode()
            digest = hashlib.sha256(data).hexdigest()
            (originals / digest).write_bytes(data)
            source_rows.append(self.row("sources/" + digest, data, "source-original"))
        raw, complete = gather({"files": bulk}, preserve_partial=True)
        self.assertTrue(complete)
        with self.assertRaisesRegex(ValueError, "actual snapshot manifest"):
            gate.transport_snapshot_manifest(raw, gate.HOSTED_TRANSPORT_INDEX_MAX)
        self.crypto.gather_snapshot.side_effect = lambda *_args, **_kwargs: (raw, True)
        with self.assertRaisesRegex(ValueError, "actual snapshot manifest"):
            self.seal_fixture()
        self.crypto.encrypt_snapshot.assert_not_called()
        plan = gate.transport_partition_plan(source_rows)
        self.assertGreater(len(plan["parts"]), 1)
        self.assertLessEqual(len(plan["parts"]), 15)
        self.assertEqual(sum(len(part["paths"]) for part in plan["parts"]), 1735)
        for part in plan["parts"]:
            target = self.root / ("bounded-" + part["id"]); staged = target / "originals"; staged.mkdir(parents=True)
            for path in part["paths"]:
                (originals / path.split("/", 1)[1]).rename(staged / path.split("/", 1)[1])
                gate.transport_manifest_path(staged / path.split("/", 1)[1])
                gate.transport_manifest_identity((staged / path.split("/", 1)[1]).lstat())
            bounded, complete = gather({"files": target}, preserve_partial=True)
            self.assertTrue(complete)
            self.assertLessEqual(part["snapshot_manifest_bytes_bound"], 512 * 1024)
            self.assertTrue(gate.transport_snapshot_manifest(bounded, part["snapshot_manifest_bytes_bound"])["selected_originals_complete"])
        with self.assertRaises(ValueError): gate.transport_manifest_path("x" * 321)
        with self.assertRaises(ValueError): gate.transport_manifest_path("\\" * 161)

    def test_private_transport_packed_roster_is_lossless_and_rejects_foreign_assignments(self):
        plan = gate.transport_partition_plan([self.row(), {**self.row("controller/stderr.original", b"", "controller-original")}])
        packed = gate.transport_pack_roster(plan)
        self.assertEqual(gate.transport_unpack_roster(packed), plan)
        for mutation in (packed + [packed[0]], [[True, *packed[0][1:]], *packed[1:]],
                         [[*packed[0][:-1], 16], *packed[1:]], [[*packed[0][:-1], 0], *packed[1:]], list(reversed(packed))):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): gate.transport_unpack_roster(mutation)
        # Out-of-range uploader slots fail before gate queries, crypto or writes.
        for slot in ("00", "16", "17", True):
            originals = [(b"review", {}), (b"recipient", {}), (gate.transport_json(self.started), {})]
            with self.subTest(slot=slot), patch.object(gate, "request_context", return_value=self.context), \
                    patch.object(gate, "hosted_private_root", return_value=self.private), \
                    patch.object(gate, "transport_original", side_effect=originals), \
                    patch.object(gate, "transport_review"), patch.object(gate, "transport_recipient", return_value=self.recipient), \
                    patch.object(gate, "verify_hosted_gate") as verify, patch.object(gate, "transport_primitives") as primitives, \
                    patch.object(gate.custody, "write_new") as write:
                with self.assertRaisesRegex(ValueError, "unknown ciphertext upload slot"):
                    gate.transport_entry("upload-data-check", "remote", {"FOUNDATION_UPLOAD_SLOT": slot})
                verify.assert_not_called(); primitives.assert_not_called(); write.assert_not_called()

    def test_private_transport_reserve_is_inside_original_H_and_cannot_start_late_or_renew(self):
        self.assertEqual(sum(n for _, n in gate.HOSTED_TRANSPORT_PHASES), 1800)
        latest = self.started["original_deadline_monotonic_ns"] - 1800 * 10**9
        self.assertEqual(gate.transport_clock(self.started, self.context, "seal-data", latest)["remaining_reserved_seconds"], 1800)
        with self.assertRaises(ValueError): gate.transport_clock(self.started, self.context, "seal-data", latest + 1)
        for changed in ({**self.started, "original_started_monotonic_ns": True},
                        {**self.started, "original_deadline_monotonic_ns": self.started["original_deadline_monotonic_ns"] + 1},
                        {**self.started, "budget_seconds": 7201}):
            with self.subTest(changed=changed), self.assertRaises(ValueError): gate.transport_clock(changed, self.context, "seal-data", latest)
        clock = Mock(return_value=latest)
        remaining = gate.transport_phase_remaining(self.started, "seal-data", clock)
        self.assertEqual(remaining(300), 300)
        clock.return_value += 300 * 10**9
        with self.assertRaises(ValueError): remaining(1)
        entry = gate.transport_clock(self.started, self.context, "data-uploads", latest + 300 * 10**9)
        later = entry["observed_monotonic_ns"] + 60 * 10**9
        self.assertEqual(gate.transport_resume_phase(self.started, self.context, "data-uploads", entry, later), entry)
        self.assertEqual(gate.transport_phase_remaining(self.started, "data-uploads", lambda: later,
                         phase_started_ns=entry["observed_monotonic_ns"])(1080), 1020)
        with self.assertRaises(ValueError):
            gate.transport_resume_phase(self.started, self.context, "data-uploads", entry,
                                        entry["phase_deadline_monotonic_ns"])
        with self.assertRaises(ValueError):
            gate.transport_resume_phase(self.started, self.context, "data-uploads",
                                        {**entry, "phase_deadline_monotonic_ns": entry["phase_deadline_monotonic_ns"] + 1}, later)

    def test_private_transport_inventory_preserves_missing_role_guards_and_rejects_unselected_files(self):
        (self.evidence / "started.json").write_bytes(b"started")
        rows = gate.transport_inventory(self.evidence)
        self.assertEqual(len(rows), 15)
        self.assertEqual(sum(row["state"] == "MISSING" for row in rows), 14)
        (self.evidence / "private-lease.json").write_bytes(b"not selected")
        with self.assertRaisesRegex(ValueError, "unselected evidence file"):
            gate.transport_inventory(self.evidence)

    def test_private_transport_seals_once_with_existing_primitives_and_only_ciphertext_outgoing(self):
        before = b"retained original"
        plan, sealed = self.seal_fixture()
        self.assertEqual((self.evidence / "started.json").read_bytes(), before)
        self.assertEqual(self.crypto.gather_snapshot.call_count, 1)
        self.assertEqual(self.crypto.encrypt_snapshot.call_count, 1)
        self.assertEqual(self.crypto.encrypt_snapshot.call_args.args[2], gate.transport_aad(self.context))
        self.assertEqual(set(gate.transport_aad(self.context)), {"source_sha", "semwright_sha", "run_id", "run_attempt"})
        self.assertEqual([path.name for path in self.outgoing.iterdir()], ["part-01.bin"])
        self.assertFalse(sealed["foundation_result_admitted"])
        self.assertFalse(sealed["upload_confirmed"])
        self.assertEqual(plan["parts"][0]["paths"], ["started.json"])

    def test_private_transport_capture_error_retains_original_terminal_without_second_snapshot(self):
        (self.evidence / "started.json").write_bytes(b"retained original")
        self.crypto.gather_snapshot.side_effect = OSError("owned original capture failed")
        with self.assertRaises(OSError):
            gate.transport_seal_parts(self.evidence, self.private, self.outgoing, [self.row()],
                self.context, self.recipient, self.crypto, lambda: 1)
        self.crypto.gather_snapshot.assert_called_once()
        self.crypto.encrypt_snapshot.assert_not_called()
        original = json.loads((self.private / "parts.original.json").read_bytes())
        self.assertEqual(original["status"], "UNKNOWN_SEAL_CUSTODY_NO_RETRY")
        self.assertTrue(original["originals_retained_in_place"])
        self.assertFalse(original["foundation_result_admitted"])

    def test_private_transport_index_binds_every_artifact_and_excludes_its_own_descriptor(self):
        plan, sealed = self.seal_fixture()
        uploaded = self.uploaded(sealed)
        index = gate.transport_index(plan, sealed, uploaded, self.context)
        rebound, records = gate.transport_index_records(index)
        self.assertEqual(rebound, plan)
        self.assertEqual(records[0]["descriptor"], self.descriptor())
        self.assertEqual(index["index_own_descriptor"], "EXTERNAL_ONLY")
        self.assertNotIn("index_sha256", index)
        self.assertFalse(index["source_result_admitted"])
        for changed in ({}, {**uploaded, "part-16": uploaded["part-01"]}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                gate.transport_index(plan, sealed, changed, self.context)
        changed = copy.deepcopy(uploaded); changed["part-01"]["ciphertext_sha256"] = "f" * 64
        with self.assertRaises(ValueError): gate.transport_index(plan, sealed, changed, self.context)
        changed = copy.deepcopy(index); changed["data_parts"][0]["copy_failed_rows"] = [True]
        with self.assertRaises(ValueError): gate.transport_index_records(changed)

    def test_private_transport_partial_snapshot_cannot_become_a_complete_roster_by_sealing(self):
        original_snapshot = self.crypto.gather_snapshot.side_effect
        def partial(*args, **kwargs):
            raw, _ = original_snapshot(*args, **kwargs)
            stream = self.io.BytesIO()
            with self.tarfile.open(fileobj=self.io.BytesIO(raw), mode="r:") as old, \
                    self.tarfile.open(fileobj=stream, mode="w") as new:
                for member in old.getmembers():
                    data = old.extractfile(member).read()
                    if member.name == "snapshot-manifest.json":
                        value = json.loads(data); value["selected_originals_complete"] = False
                        data = gate.transport_json(value); member.size = len(data)
                    new.addfile(member, self.io.BytesIO(data))
            return stream.getvalue(), False
        self.crypto.gather_snapshot.side_effect = partial
        plan, sealed = self.seal_fixture()
        self.assertEqual(sealed["status"], "SEALED_INCOMPLETE_ORIGINALS_NO_RESULT_ADMISSION")
        index = gate.transport_index(plan, sealed, self.uploaded(sealed), self.context)
        self.assertFalse(index["data_parts"][0]["snapshot_complete"])
        self.assertFalse(index["source_result_admitted"])

    def test_private_transport_zip_readback_requires_actual_run_job_and_exact_cipher_member(self):
        import zipfile
        stream = self.io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive: archive.writestr("part-01.bin", b"cipher fixture")
        raw = stream.getvalue()
        descriptor = {**self.descriptor(), "zip_bytes": len(raw), "zip_sha256": hashlib.sha256(raw).hexdigest()}
        run = {"id": 12345, "run_attempt": 1, "head_sha": "c" * 40, "repository": {"full_name": "example/repo"},
               "path": gate.WORKFLOW, "event": "workflow_dispatch"}
        job = {"id": 201, "run_id": 12345, "head_sha": "c" * 40, "name": gate.HOSTED_SCOPE}
        artifact = {"id": 101, "expired": False, "size_in_bytes": len(raw), "digest": "sha256:" + descriptor["zip_sha256"],
                    "workflow_run": {"id": 12345, "head_sha": "c" * 40}}
        value = gate.transport_artifact_readback(descriptor, self.context, run, job, artifact, raw)
        self.assertEqual(value["ciphertext"], b"cipher fixture")
        self.assertFalse(value["producer_result_admitted"])
        for other_run, other_job, other_artifact, other_raw in (
                ({**run, "run_attempt": 2}, job, artifact, raw), (run, {**job, "name": "other"}, artifact, raw),
                (run, job, {**artifact, "expired": True}, raw), (run, job, artifact, raw + b"changed")):
            with self.subTest(run=other_run), self.assertRaises(ValueError):
                gate.transport_artifact_readback(descriptor, self.context, other_run, other_job, other_artifact, other_raw)
        with self.assertRaises(ValueError): gate.transport_descriptor({**descriptor, "artifact_id": True}, self.context)

    def test_private_transport_recovery_rechecks_tar_hashes_and_rejects_omitted_complete_member(self):
        plan, sealed = self.seal_fixture()
        directory = self.private / "part-01/payload"
        raw, _ = self.fixture_snapshot({"files": directory}, preserve_partial=True)
        record = sealed["parts"][0]
        record["plaintext_sha256"] = hashlib.sha256(raw).hexdigest(); record["plaintext_bytes"] = len(raw)
        result = gate.transport_snapshot_readback(raw, record, plan)
        self.assertEqual(result["originals"], {"started.json": b"retained original"})
        self.assertFalse(result["foundation_result_admitted"])
        with self.assertRaises(ValueError): gate.transport_snapshot_readback(raw + b"x", record, plan)
        altered = copy.deepcopy(record); altered["staged"][0]["sha256"] = "f" * 64
        with self.assertRaises(ValueError): gate.transport_snapshot_readback(raw, altered, plan)

    def test_private_transport_reader_cannot_attribute_unknown_or_foreign_terminal_to_foundation(self):
        minimal = {"started.json": gate.transport_json(self.started),
                   "receipt.json": gate.transport_json({"schema": gate.SCHEMA,
                    "context": {**self.context, "gate_source_sha": "c" * 40}, "status": "UNKNOWN_NO_RETRY"})}
        with self.assertRaisesRegex(ValueError, "Foundation terminal is incomplete"):
            gate.transport_foundation_readback(minimal, self.context, {})
        with self.assertRaises(ValueError):
            gate.transport_foundation_readback({}, self.context, {})

    def test_private_transport_reused_data_artifact_or_wrong_recipient_is_rejected(self):
        (self.evidence / "started.json").write_bytes(b"retained original")
        binary = b"one synthetic compiled executable"; digest = hashlib.sha256(binary).hexdigest()
        (self.evidence / "binaries").mkdir(); (self.evidence / "binaries" / digest).write_bytes(binary)
        another = b"second intact executable"; second_digest = hashlib.sha256(another).hexdigest()
        (self.evidence / "binaries" / second_digest).write_bytes(another)
        rows = [self.row(), self.row("binaries/" + digest, binary, "compiled-binary"),
                self.row("binaries/" + second_digest, another, "compiled-binary")]
        plan, sealed = gate.transport_seal_parts(self.evidence, self.private, self.outgoing, rows,
            self.context, self.recipient, self.crypto, lambda: 1)
        uploaded = self.uploaded(sealed)
        uploaded["part-02"]["descriptor"]["artifact_id"] = uploaded["part-01"]["descriptor"]["artifact_id"]
        with self.assertRaisesRegex(ValueError, "reused"):
            gate.transport_index(plan, sealed, uploaded, self.context)
        primitive = Mock(MAGIC=b"MWCISEAL1\n")
        primitive.strict.side_effect = gate.custody.strict_json
        primitive.json_bytes.side_effect = gate.transport_json
        header = gate.transport_json({"context": gate.transport_aad(self.context),
                                      "recipient_spki_sha256": "f" * 64})
        raw = primitive.MAGIC + len(header).to_bytes(4, "big") + header + b"x" * 32
        with self.assertRaisesRegex(ValueError, "exact recipient"):
            gate.transport_cipher_header(raw, self.context, primitive)
        self.crypto.decrypt_snapshot.assert_not_called()

    def test_private_transport_workflow_uses_only_the_existing_bounded_ciphertext_upload_surface(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text()
        job = workflow_jobs(text)[gate.HOSTED_SCOPE]
        self.assertIn("timeout-minutes: 135", job)
        begin = job.index("      # BEGIN Foundation ciphertext transport preparation\n")
        end_marker = "      # END Foundation ciphertext transport preparation\n"
        end = job.index(end_marker, begin) + len(end_marker)
        block = job[begin:end]
        self.assertEqual(hashlib.sha256(block.encode()).hexdigest(), HOSTED_TRANSPORT_WORKFLOW_BLOCK_SHA256)
        self.assertEqual(block.count("uses: actions/upload-artifact@" + gate.HOSTED_TRANSPORT_UPLOAD_ACTION), 16)
        self.assertEqual(block.count("timeout-minutes: 1\n"), 32)
        for number in range(1, 16):
            name = str(number).zfill(2)
            self.assertEqual(block.count("id: foundation_upload_" + name + "\n"), 1)
            self.assertEqual(block.count("id: foundation_guard_" + name + "\n"), 1)
            self.assertIn("steps.foundation_guard_" + name + ".outputs.ready == 'true'", block)
            self.assertIn("/outgoing/part-" + name + ".bin\n", block)
            self.assertIn("FOUNDATION_ARTIFACT_" + name + ": ${{ steps.foundation_upload_" + name + ".outputs.artifact-id }}", block)
        self.assertLess(block.index("id: foundation_upload_15"), block.index("id: foundation_index\n"))
        self.assertLess(block.index("id: foundation_index\n"), block.index("id: foundation_index_upload\n"))
        self.assertNotIn("--execute", block)
        paths = re.findall(r"(?m)^          path: (.+)$", block)
        self.assertEqual(len(paths), 16)
        self.assertNotIn("foundation_upload_16", block)
        self.assertNotIn("foundation_upload_17", block)
        self.assertTrue(all("/outgoing/" in path and path.endswith(".bin") for path in paths))
        self.assertNotIn("private_key", block)
        self.assertIn("--hosted-controller", block)
        self.assertNotIn("--transport-action dependencies", block)
        self.assertEqual(gate.transport_dependency_argv(Path("python"), Path("gate"))[-5:],
                         ["--only-binary=:all:", "--require-hashes", "--no-deps", "-r", str(Path("gate") / gate.HOSTED_TRANSPORT_REQUIREMENTS)])
        self.assertTrue(all("steps.foundation_admission.outcome == 'success'" in line
                            or "steps.foundation_guard_" in line or "steps.foundation_index_guard.outputs.ready" in line
                            for line in block.splitlines() if "if: " in line))
        # Checkout2 + Python3 + capacity3 + admission2 + original H120 + public upload1.
        self.assertLessEqual(2 + 3 + 3 + 2 + 120 + 1, 135)
        self.assertEqual(sum(cap for _, cap in gate.HOSTED_TRANSPORT_PHASES), 1800)

    def test_private_transport_requirements_are_exact_windows_wheels_and_dependencies_unchanged(self):
        root = Path(gate.__file__).resolve().parents[1]
        raw = (root / gate.HOSTED_TRANSPORT_REQUIREMENTS).read_bytes()
        self.assertEqual(len(raw), 933)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "82b7c9a4741d26411cdd6a0732cebeffd081cfad5f51ef4a75585aa7d38f162f")
        self.assertEqual([line.split()[0] for line in raw.decode().splitlines() if line and not line.startswith("#")],
                         ["cryptography==50.0.2", "cffi==2.1.1", "pycparser==2.23", "jsonschema==4.26.0", "PyYAML==6.0.3",
                          "attrs==26.1.0", "jsonschema-specifications==2025.9.1", "referencing==0.37.0", "rpds-py==2026.9.1"])
        self.assertEqual(len(gate.HOSTED_PYTHON_DEPENDENCIES), 9)
        tools = {"node": {"absolute_path": "C:/image-resolved-node/node.exe", "version": "v22.23.4\n"},
                 **{name: {"absolute_path": "C:/mingw64/bin/" + name + ".exe"} for name in ("gcc", "g++", "ar", "as", "ld")}}
        observed = gate.hosted_image_tool_roots(tools)
        self.assertFalse(observed["node_installation_path_inferred_from_MSI"])
        for name, field, value in (("node", "absolute_path", "D:/unselected/node.exe"),
                                   ("node", "version", "v24.22.1\n"), ("gcc", "absolute_path", "C:/mingw32/bin/gcc.exe")):
            changed = copy.deepcopy(tools); changed[name][field] = value
            with self.subTest(name=name), self.assertRaises(ValueError): gate.hosted_image_tool_roots(changed)
        self.assertEqual(gate.HOSTED_TRANSPORT_PINS["tooling/ci_private_evidence.py"],
                         "2671eea354f672353a6b4ea1ac9c43de4b682e96f4dcc9146104a5f8d20a8eee")
        self.assertEqual(gate.HOSTED_TRANSPORT_PINS["tooling/mlt_official_runtime_intake.py"],
                         "a5c8843db38e48060f34e73afdcaf59341cac0ee88ddd6e13cbf85dadbfb770f")



class SDKFoundationHostedCompositionTests(unittest.TestCase):
    """Actual composition functions with explicit command/kernel fixtures; no SDK launch."""
    def setUp(self):
        existing = SDKFoundationHostedTests(); existing.setUp(); self.addCleanup(existing.doCleanups)
        self.root, self.repo, self.env, self.auth = existing.root, existing.repo, existing.env, existing.auth
        self.auth["max_total_seconds"] = 7200
        (self.root / "verification").mkdir()
        self.context = existing.context()

    def qualification(self):
        value = copy.deepcopy(self.auth)
        value["purpose"] = gate.HOSTED_QUALIFICATION_PURPOSE
        value["binding"]["private_transport"].update(source_shas=[gate.FIXED["sha"]], incoming="OWNER_COMPOSED_FIXED_CHECKOUT")
        value["binding"]["cold_growth"].update(source_shas=[gate.FIXED["sha"]], compiled_configurations=1,
                                               bytes=dict(gate.HOSTED_QUALIFICATION_CEILINGS))
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": value}):
            context = gate.request_context(self.env, gate.HOSTED_MODE, gate.FIXED["sha"], "remote", "win32")
        return value, context

    def public_input(self, auth, context):
        import base64
        value = {"schema": gate.HOSTED_TRANSPORT_REVIEW_SCHEMA, "purpose": gate.hosted_purpose(context),
                 "gate_base_sha": auth["binding"]["gate_base_sha"],
                 "recipe_sha256": hashlib.sha256(gate.transport_json(gate.hosted_transport_recipe(gate.hosted_purpose(context)))).hexdigest(),
                 "recipient_public_sha256": gate.HOSTED_TRANSPORT_RECIPIENT_SHA256,
                 "recipient_spki_sha256": gate.HOSTED_TRANSPORT_SPKI_SHA256,
                 "cold_growth_basis_sha256": auth["binding"]["cold_growth"]["basis_sha256"]}
        review = gate.transport_json(value)
        digest = hashlib.sha256(review).hexdigest()
        auth["binding"]["private_transport"]["review_sha256"] = digest
        context["private_transport_review_sha256"] = digest
        recipient = HOSTED_PUBLIC_RECIPIENT_FIXTURE
        raw = gate.transport_json({"schema": gate.HOSTED_INPUT_SCHEMA,
            "review_base64": base64.b64encode(review).decode(), "recipient_base64": base64.b64encode(recipient).decode()})
        return raw, review, recipient

    def test_public_input_binds_exact_R_recipient_and_purpose_without_self_authority(self):
        auth, context = self.qualification()
        raw, review, recipient = self.public_input(auth, context)
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": auth}):
            parsed = gate.hosted_transport_input(raw, context)
            self.assertEqual(parsed["originals"], {"review": review, "recipient": recipient})
            self.assertEqual(parsed["input_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertFalse(parsed["human_acknowledgement"])
            changed = json.loads(raw); changed["lease"] = {"self_issued": True}
            for bad in (gate.transport_json(changed), raw + b"x", b"{}", b"x" * 8193):
                with self.subTest(raw=bad[:32]), self.assertRaises(ValueError): gate.hosted_transport_input(bad, context)
            changed = json.loads(raw); changed["recipient_base64"] += "\n"
            with self.assertRaises(ValueError): gate.hosted_transport_input(gate.transport_json(changed), context)
            with self.assertRaises(ValueError): gate.hosted_transport_input(raw, {**context, "purpose": gate.HOSTED_FULL_PURPOSE})
        with patch.object(gate, "REVIEWED_AUTHORITIES", {}), patch.object(gate, "hosted_private_root") as paths:
            with self.assertRaises(ValueError): gate.hosted_transport_input(raw, context)
            paths.assert_not_called()

    def test_qualification_authority_is_exact_one_source_and_chosen_ceiling_not_full_growth(self):
        auth, context = self.qualification()
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": auth}):
            self.assertEqual(gate.authority("remote", gate.HOSTED_MODE, gate.FIXED["sha"]), auth)
        self.assertEqual(gate.hosted_profiles(context["purpose"]), [("fixed", gate.FIXED)])
        self.assertEqual(sum(gate.HOSTED_QUALIFICATION_CEILINGS.values()), 68987912192)
        self.assertEqual(gate.HOSTED_QUALIFICATION_FREE, 88315265024)
        self.assertEqual(gate.HOSTED_QUALIFICATION_CUSTODY_BYTES, 4 * 1024**3)
        value = gate.hosted_transport_recipe(context["purpose"])
        self.assertEqual(len(value["sources"]), 1); self.assertEqual(len(value["compiled_configurations"]), 1)
        self.assertFalse(value["chosen_ceilings_are_measured_growth"])
        self.assertFalse(value["test_or_list_execution_selected"])
        for key, bad in (("purpose", "arbitrary-subset"), ("max_total_seconds", 7199)):
            changed = copy.deepcopy(auth); changed[key] = bad
            with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": changed}), self.assertRaises(ValueError):
                gate.authority("remote", gate.HOSTED_MODE, gate.FIXED["sha"])
        for mutation in ("count", "ceiling", "source"):
            changed = copy.deepcopy(auth)
            growth = changed["binding"]["cold_growth"]
            if mutation == "count": growth["compiled_configurations"] = True
            elif mutation == "ceiling": growth["bytes"]["targets"] += 1
            else: growth["source_shas"] = ["f" * 40]
            with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": changed}), self.assertRaises(ValueError):
                gate.authority("remote", gate.HOSTED_MODE, gate.FIXED["sha"])

    def test_hosted_work_budget_inherits_t0_and_protects_custody_before_all_producers(self):
        first = 1_000_000_000
        original = gate.hosted_clock(self.context, first, first)
        now = Mock(return_value=first + 5399 * 10**9)
        budget = gate.HostedWorkBudget(original, self.context, now)
        self.assertEqual(budget.started, first)
        self.assertEqual(budget.original_deadline, first + 7200 * 10**9)
        self.assertEqual(budget.remaining(900), 1)
        now.return_value += 10**9
        with self.assertRaises(ValueError): budget.remaining(1)
        with self.assertRaises(ValueError): gate.HostedWorkBudget(original, self.context, now)
        for key in ("original_started_monotonic_ns", "work_deadline_monotonic_ns", "custody_reserved_seconds"):
            changed = dict(original); changed[key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.HostedWorkBudget(changed, self.context, lambda: first)

    def test_original_hosted_clock_is_created_before_private_preparation_and_one_worker(self):
        # Closed source order, paired with the executable clock and incoming
        # controls below. No controller is launched by this source assertion.
        text = Path(gate.__file__).read_text()
        tree = ast.parse(text)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "hosted_controller")
        source = ast.get_source_segment(text, node)
        self.assertLess(source.index('original = Budget('), source.index('root.mkdir('))
        self.assertLess(source.index('HostedWorkBudget(clock, context)'), source.index('hosted_fetch_sources('))
        self.assertLess(source.index('hosted_prepare_custody_python('), source.index('hosted_fetch_sources('))
        self.assertLess(source.index('hosted_load_bootstrap('), source.index('"--hosted-worker"'))
        self.assertEqual(source.count('"--hosted-worker"'), 1)
        self.assertIn('child_environment.pop(name, None)', source)
        self.assertIn('"GH_TOKEN", "GITHUB_TOKEN"', source)
        calls = []
        preparation = self.root / "python-preparation"; preparation.mkdir()
        python = preparation / "toolchain/custody-python/Scripts/python.exe"
        def call(argv, cwd, environment, logs, label, timeout, context):
            calls.append((argv, label, timeout))
            if label == "custody-python":
                self.assertNotIn("--system-site-packages", argv)
                python.parent.mkdir(parents=True); python.write_bytes(b"nonexecuted owned interpreter fixture")
            return {"exit_code": 0, "label": label}
        def capability(*_args):
            return gate.transport_json({"status": "EXISTING_CUSTODY_IMPORTS_AND_RECIPIENT_OBSERVED",
                "context": self.context, "source_pins": dict(gate.HOSTED_TRANSPORT_PINS),
                "recipient_sha256": gate.HOSTED_TRANSPORT_RECIPIENT_SHA256, "private_key_loaded": False,
                "python": gate.custody.fingerprint(python, preparation)})
        with patch.object(gate.observer, "original_stream", side_effect=capability):
            selected, _, _, _ = gate.hosted_prepare_custody_python(preparation, Path(gate.__file__).resolve().parents[1],
                self.context, gate.Budget(600), call, {})
        self.assertEqual(selected, python)
        self.assertEqual([(label, cap) for _, label, cap in calls],
                         [("custody-python", 120), ("transport-dependencies", 120), ("custody-capability", 45)])
        self.assertEqual(calls[1][0], gate.transport_dependency_argv(python, Path(gate.__file__).resolve().parents[1]))
        self.assertIn('python_executable=python', source)

    def test_incoming_sources_are_once_only_fixed_git_commands_and_keep_original_budget(self):
        auth, context = self.qualification()
        root = gate.hosted_private_root(self.env, context); root.mkdir()
        (root / "bootstrap").mkdir(); (root / "bootstrap/commands").mkdir(); (root / "checkouts").mkdir()
        executable = self.root / "git-fixture"; executable.write_bytes(b"explicit intercepted Git tool")
        calls = []
        def call(argv, cwd, env, output, label, timeout, frame):
            calls.append((argv, label, timeout, frame))
            if label.endswith("-init"): Path(argv[-1]).mkdir()
            return {"exit_code": 0}
        def snapshot(checkout, profile):
            return {"sha": profile["sha"], "tree": profile["tree"], "checkout": str(checkout), "files": {}}
        budget = gate.Budget(600)
        with patch.object(gate.shutil, "which", return_value=str(executable)), \
                patch.object(gate, "full_snapshot", side_effect=snapshot):
            rows = gate.hosted_fetch_sources(root, self.repo, context, budget, call, {})
            self.assertEqual(set(rows), {"fixed"})
            self.assertEqual([c[1] for c in calls], ["incoming-fixed-init", "incoming-fixed-fetch", "incoming-fixed-checkout"])
            self.assertEqual([c[2] for c in calls], [45, 180, 45])
            self.assertIn(gate.HOSTED_PUBLIC_SOURCE_REPOSITORY, calls[1][0])
            self.assertEqual(calls[1][0][-1], gate.FIXED["sha"])
            self.assertIn("core.autocrlf=false", calls[2][0])
            with self.assertRaisesRegex(ValueError, "no fetch retry"):
                gate.hosted_fetch_sources(root, self.repo, context, budget, call, {})
            self.assertEqual(len(calls), 3)
        self.assertEqual(len(gate.hosted_source_plan(root)), 5)
        self.assertEqual(len(gate.hosted_source_plan(root, gate.HOSTED_QUALIFICATION_PURPOSE)), 1)

    def test_incoming_unknown_stops_without_checkout_snapshot_or_retry(self):
        auth, context = self.qualification()
        root = self.root / "unknown-incoming"; root.mkdir(); (root / "bootstrap").mkdir(); (root / "checkouts").mkdir()
        executable = self.root / "git-fixture"; executable.write_bytes(b"intercepted")
        uncertain = {"observation": "UNKNOWN_TIMEOUT_NO_RETRY"}
        call = Mock(side_effect=gate.observer.UnknownExecution("uncertain", uncertain))
        with patch.object(gate.shutil, "which", return_value=str(executable)), patch.object(gate, "full_snapshot") as source:
            with self.assertRaises(gate.observer.UnknownExecution):
                gate.hosted_fetch_sources(root, self.repo, context, gate.Budget(600), call, {})
            call.assert_called_once(); source.assert_not_called()

    def qualification_fixture(self):
        auth, context = self.qualification()
        logs = self.root / "qualification-logs"; logs.mkdir()
        target = self.root / "qualification-target"; target.mkdir()
        binary_path = target / "compiled-fixture"; binary_path.write_bytes(b"not executable SDK")
        binary = {"absolute_path": str(binary_path), **gate.custody.fingerprint(binary_path, target)}
        state = {"id": "fixed", "causal": None, "repo": self.repo, "source": {}, "env": {},
                 "logs": logs, "context": context, "target": target}
        report = {"context": context, "sdk_g0": {}, "binaries": {}, "rosters": {}, "groups": [], "causals": []}
        return state, report, binary

    def test_qualification_real_barrier_runs_G0_then_one_compile_then_seal_without_observation(self):
        state, report, binary = self.qualification_fixture()
        order = []
        def call(argv, cwd, env, logs, label, timeout, frame):
            order.append(("compile", argv, timeout)); return {"exit_code": 0, "argv": argv}
        def seal(prepared, repo, target, logs, evidence, barrier):
            self.assertEqual(barrier.phase, "SEALING"); order.append(("seal",))
            return {"driver-sdk": {gate.TARGETS["driver-sdk"]["name"]: binary}}
        with patch.object(gate, "sdk_g0", side_effect=lambda *args: order.append(("sdk-g0",))), \
                patch.object(gate.observer, "original_stream", return_value=b"intercepted Cargo JSON"), \
                patch.object(gate, "compiled_paths"), patch.object(gate, "source_unchanged"), patch.object(gate, "verify_tools"), \
                patch.object(gate.producer, "seal_compiled_role", side_effect=seal), \
                patch.object(gate.producer, "verify_binaries"), patch.object(gate, "list_role") as listed:
            gate.execute_qualification([state], {"cargo": {"absolute_path": "observed-cargo"}}, self.root,
                                       gate.Budget(600), call, report)
            listed.assert_not_called()
        self.assertEqual([row[0] for row in order], ["sdk-g0", "compile", "seal"])
        self.assertEqual(order[1][1], ["observed-cargo", "test", "--locked", "-p", "semwright-driver-sdk", "--lib", "--no-run", "--message-format=json"])
        self.assertEqual(order[1][2], 900)
        self.assertEqual(report["producer_barrier"], "ONE_SELECTED_PRODUCER_SEALED_NO_OBSERVATION")
        record = json.loads((state["logs"] / "compiled-rosters.json").read_bytes())
        self.assertFalse(record["listed"]); self.assertEqual(record["test_methods_executed"], 0)
        self.assertEqual(report["qualification"]["full_thirteen_configuration_growth"], "NOT_ADMITTED")

    def test_qualification_G0_failure_and_UNKNOWN_compile_prevent_seal_and_methods(self):
        state, report, _ = self.qualification_fixture()
        call = Mock()
        with patch.object(gate, "sdk_g0", side_effect=ValueError("actual source G0 fails")), \
                patch.object(gate.producer, "seal_compiled_role") as seal:
            with self.assertRaises(ValueError):
                gate.execute_qualification([state], {"cargo": {"absolute_path": "cargo"}}, self.root, gate.Budget(600), call, report)
            call.assert_not_called(); seal.assert_not_called()
        uncertain = {"observation": "UNKNOWN_TIMEOUT_NO_RETRY", "exit_code": None}
        call = Mock(side_effect=gate.observer.UnknownExecution("original", uncertain))
        with patch.object(gate, "sdk_g0"), patch.object(gate.producer, "seal_compiled_role") as seal, patch.object(gate, "list_role") as listed:
            with self.assertRaises(gate.observer.UnknownExecution):
                gate.execute_qualification([state], {"cargo": {"absolute_path": "cargo"}}, self.root, gate.Budget(600), call, report)
            call.assert_called_once(); seal.assert_not_called(); listed.assert_not_called()
        self.assertEqual(report["groups"], []); self.assertEqual(report["causals"], [])

    def test_component_measurement_uses_disjoint_paths_real_volume_and_reserved_ceilings(self):
        _, context = self.qualification()
        original_clock = gate.hosted_clock(context, gate.time.monotonic_ns())
        def admit(value, phase="initial", origin=None):
            return gate.qualification_measurement(value, context, expected_phase=phase,
                                                  original_clock=original_clock, origin=origin)
        root = self.root / "owned"; root.mkdir()
        image = self.root / "image"; image.mkdir()
        paths = {"checkouts/fixed/Cargo.toml": "source_checkouts", "toolchain/rustup/tool": "toolchain",
                 "execution/cargo-home/download": "cargo_downloads", "execution/targets/fixed/binary": "targets",
                 "execution/evidence/binaries/retained": "sealed_binary_originals",
                 "execution/evidence/sources/retained": "source_transport", "bootstrap/commands/log": "temporary_and_logs"}
        for name, component in paths.items():
            file = root / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(b"abc")
            self.assertEqual(gate.hosted_component_name(name), component)
        def volume(anchor):
            work = str(anchor) == str(root)
            return {"mount_path": "D:\\" if work else "C:\\", "volume_guid_path": "work" if work else "image",
                    "caller_available_bytes": 100 * 1024**3 if work else 30 * 1024**3}
        with patch.object(gate, "capacity_volume", side_effect=volume):
            value = gate.hosted_component_snapshot(root, context, original_clock["work_deadline_monotonic_ns"],
                                                  phase="initial", environment={"USERPROFILE": str(image)})
        self.assertEqual(value["component_logical_bytes"], {key: 3 for key in paths.values()})
        self.assertEqual(admit(value), value)
        self.assertEqual(value["custody_bytes_reserved"], 4 * 1024**3)
        self.assertFalse(value["component_physical_quotas"]); self.assertFalse(value["continuous_peak_observed"])
        self.assertFalse(value["full_foundation_growth_admitted"])
        changed = copy.deepcopy(value); changed["component_logical_bytes"]["temporary_and_logs"] = 2 * 1024**3 + 1
        with self.assertRaises(ValueError): admit(changed)
        changed = copy.deepcopy(value); changed["component_file_counts"]["targets"] = True
        with self.assertRaises(ValueError): admit(changed)
        changed = copy.deepcopy(value); changed["work_volume"]["caller_available_bytes"] = 88315265023
        with self.assertRaises(ValueError): admit(changed)
        changed = copy.deepcopy(value); changed["image_volume"]["caller_available_bytes"] = 18 * 1024**3 - 1
        with self.assertRaises(ValueError): admit(changed)
        relabelled = copy.deepcopy(value)
        relabelled["phase"] = "work"
        relabelled["work_volume"]["caller_available_bytes"] = 30 * 1024**3
        with self.assertRaisesRegex(ValueError, "relabelled"):
            admit(relabelled)
        work = {**copy.deepcopy(value), "phase": "work", "observed_monotonic_ns": value["observed_monotonic_ns"] + 1}
        self.assertEqual(admit(work, "work", value), work)
        for sample, origin in ((work, relabelled),
                               ({**work, "original_deadline_monotonic_ns": work["original_deadline_monotonic_ns"] + 1}, value),
                               ({**work, "observed_shared_volume_delta_bytes": 1}, value)):
            with self.subTest(sample=sample["phase"]), self.assertRaises(ValueError):
                admit(sample, "work", origin)
        custody = {**work, "phase": "custody", "original_deadline_monotonic_ns": original_clock["original_deadline_monotonic_ns"]}
        self.assertEqual(admit(custody, "custody", value), custody)
        with self.assertRaises(ValueError): admit(work, "custody", value)

    def test_qualification_intake_cannot_promote_partial_or_foreign_resource_measurement(self):
        _, context = self.qualification()
        original_clock = gate.hosted_clock(context, gate.time.monotonic_ns())
        for value in ({}, {"schema": "motionwright.sdk-foundation-owned-footprint/1", "context": context,
                           "full_foundation_growth_admitted": True}):
            with self.assertRaises(ValueError): gate.qualification_measurement(
                value, context, expected_phase="initial", original_clock=original_clock)
        with self.assertRaises(ValueError): gate.transport_partition_plan(
            [{"path": gate.CAUSALS[0]["id"] + "/sdk-g0.json", "role": "sdk-g0", "state": "MISSING",
              "bytes": None, "sha256": None}], gate.HOSTED_QUALIFICATION_PURPOSE)

    def test_bootstrap_digest_is_checked_before_any_source_or_tool_readback(self):
        root = gate.hosted_private_root(self.env, self.context); root.mkdir(); (root / "bootstrap").mkdir()
        (root / "bootstrap/technical-ready.original.json").write_bytes(b"{}\n")
        with patch.object(gate, "source_unchanged") as source, patch.object(gate, "verify_tools") as tools, \
                patch.object(gate, "verify_hosted_gate") as verify:
            with self.assertRaisesRegex(ValueError, "bootstrap original differs"):
                gate.hosted_load_bootstrap(root, "f" * 64, self.context, self.env)
            source.assert_not_called(); tools.assert_not_called(); verify.assert_not_called()

    def bootstrap_fixture(self):
        auth, context = self.qualification()
        public, review, recipient = self.public_input(auth, context)
        root = gate.hosted_private_root(self.env, context); root.mkdir()
        logs = root / "bootstrap/commands"; logs.mkdir(parents=True)
        (root / "checkouts").mkdir()
        clock = gate.hosted_clock(context, gate.time.monotonic_ns())
        for name, raw in (("input.original.json", public), ("review.original.json", review),
                          ("recipient-public.original.json", recipient), ("clock.original.json", gate.transport_json(clock))):
            (root / name).write_bytes(raw)
        sources = {}
        for role, profile, checkout in gate.hosted_source_plan(root, context["purpose"]):
            checkout.mkdir()
            raw = gate.transport_json({"sha": profile["sha"], "tree": profile["tree"], "checkout": str(checkout), "files": {}})
            (root / "bootstrap" / (role + "-source.json")).write_bytes(raw)
            sources[role] = {"checkout": str(checkout), "sha": profile["sha"], "tree": profile["tree"],
                            "listing_sha256": profile["listing"], "snapshot_bytes": len(raw),
                            "snapshot_sha256": hashlib.sha256(raw).hexdigest()}
        python = root / "toolchain/custody-python/Scripts/python.exe"
        python.parent.mkdir(parents=True); python.write_bytes(b"explicit nonexecutable interpreter fixture")
        tools = {}
        for name in ("cargo", "rustc", "rustdoc", "cargo-fmt", "rustfmt", "rustup", "python", "git", "node", "bash", "gcc", "g++", "ar", "as", "ld"):
            tools[name] = {"absolute_path": str(python), "fingerprint": gate.custody.fingerprint(python, python.parent),
                           "version": "explicit intercepted version"}
        def command(label, raw=b"explicit command stdout"):
            out, err = logs / (label + ".stdout.log"), logs / (label + ".stderr.log")
            out.write_bytes(raw); err.write_bytes(b"")
            row = {"context": context, "argv": ["explicit-intercepted-tool", label], "exit_code": 0,
                   "observation": "DIRECT_PROCESS_EXIT_OBSERVED", "log_custody": "VERIFIED_SNAPSHOT",
                   "stdout": gate.custody.fingerprint(out, logs), "stderr": gate.custody.fingerprint(err, logs)}
            (logs / (label + ".command.json")).write_bytes(gate.transport_json(row))
            return row
        jobs = gate.transport_json({"total_count": 1, "jobs": [{"id": 201, "run_id": 12345, "head_sha": "c" * 40,
              "name": "sdk-foundation-g0", "status": "completed", "conclusion": "success"}]})
        api = command("gate-source-job", jobs)
        prerequisites = {"status": "OBSERVED_EXISTING_PREREQUISITES", "tools": tools,
            "image_tools": {"fixture": "explicit image tool location seam"},
            "worker_python": gate.custody.fingerprint(python, root), "worker_python_path": str(python)}
        for key, label in (("python_command", "sdk-prerequisites"), ("dependency_command", "transport-dependencies"),
                           ("custody_capability_command", "custody-capability"), ("toolchain_command", "owned-toolchain")):
            prerequisites[key] = command(label)
        admission = {"status": "EXPLICIT_KERNEL_AND_GIT_SEAM_FIXTURE"}
        ack = {"schema": gate.HOSTED_BOOTSTRAP_SCHEMA, "kind": "TECHNICAL_INPUT_AND_CUSTODY_READY",
               "context": context, "clock": clock, "input_sha256": hashlib.sha256(public).hexdigest(),
               "review_sha256": hashlib.sha256(review).hexdigest(), "recipient_sha256": gate.HOSTED_TRANSPORT_RECIPIENT_SHA256,
               "gate_admission": admission, "gate_g0": {"conclusion": "success", "source_sha": "c" * 40,
                   "job_id": 201, "command": api, "api_original_sha256": hashlib.sha256(jobs).hexdigest()},
               "sources": sources, "crypto_sources": dict(gate.HOSTED_TRANSPORT_PINS), "prerequisites": prerequisites,
               "human_acknowledgement": False, "lease_authority": False, "sdk_started": False, "automatic_retry": False}
        raw = gate.transport_json(ack)
        path = root / "bootstrap/technical-ready.original.json"; path.write_bytes(raw)
        return auth, context, root, path, ack, admission

    def test_technical_bootstrap_reads_actual_bound_sources_streams_and_same_clock_before_worker(self):
        auth, context, root, path, ack, admission = self.bootstrap_fixture()
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": auth}), \
                patch.object(gate, "verify_hosted_gate", return_value=admission), patch.object(gate, "source_unchanged") as sources, \
                patch.object(gate, "hosted_image_tool_roots", return_value=ack["prerequisites"]["image_tools"]):
            value = gate.hosted_load_bootstrap(root, expected, context, self.env)
            self.assertEqual(value["sdk"], root / "checkouts/fixed")
            self.assertEqual(value["baselines"], {})
            self.assertEqual(value["budget"].started, ack["clock"]["original_started_monotonic_ns"])
            self.assertEqual(value["budget"].original_deadline, ack["clock"]["original_deadline_monotonic_ns"])
            sources.assert_called_once()
            self.assertFalse(value["ack"]["human_acknowledgement"])
            self.assertFalse(value["ack"]["lease_authority"])
            self.assertFalse(value["ack"]["sdk_started"])
            # A successful receipt does not authorize a new row or another SDK source.
            with patch.object(gate, "REVIEWED_AUTHORITIES", {}), self.assertRaises(ValueError):
                gate.hosted_load_bootstrap(root, expected, context, self.env)

    def test_technical_bootstrap_rejects_consistent_foreign_source_unknown_stream_and_renewed_clock(self):
        auth, context, root, path, original, admission = self.bootstrap_fixture()
        with patch.object(gate, "REVIEWED_AUTHORITIES", {"remote": auth}), \
                patch.object(gate, "verify_hosted_gate", return_value=admission), patch.object(gate, "source_unchanged"), \
                patch.object(gate, "hosted_image_tool_roots", return_value=original["prerequisites"]["image_tools"]):
            for mutation in ("source", "clock", "human"):
                changed = copy.deepcopy(original)
                if mutation == "source": changed["sources"]["fixed"]["sha"] = "f" * 40
                elif mutation == "clock": changed["clock"]["work_deadline_monotonic_ns"] += 10**9
                else: changed["human_acknowledgement"] = True
                raw = gate.transport_json(changed); path.write_bytes(raw)
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    gate.hosted_load_bootstrap(root, hashlib.sha256(raw).hexdigest(), context, self.env)
            changed = copy.deepcopy(original)
            changed["prerequisites"]["python_command"]["observation"] = "UNKNOWN_TIMEOUT_NO_RETRY"
            row = changed["prerequisites"]["python_command"]
            (root / "bootstrap/commands/sdk-prerequisites.command.json").write_bytes(gate.transport_json(row))
            raw = gate.transport_json(changed); path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "uncertain"):
                gate.hosted_load_bootstrap(root, hashlib.sha256(raw).hexdigest(), context, self.env)

if __name__ == "__main__":
    unittest.main()
