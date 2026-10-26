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
            "RUNNER_ENVIRONMENT": "github-hosted", "ImageOS": gate.HOSTED_IMAGE_OS,
            "ImageVersion": "20260101.1", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "SDK_FOUNDATION_SCOPE": gate.HOSTED_SCOPE, "GITHUB_JOB": gate.HOSTED_SCOPE,
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "12345",
            "GITHUB_SHA": "c" * 40, "GITHUB_WORKFLOW_SHA": "c" * 40,
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_WORKFLOW_REF": "example/repo/" + gate.WORKFLOW + "@refs/heads/review",
            "GITHUB_WORKSPACE": str(self.root),
        }
        sources = sorted([gate.FIXED["sha"], *(p["sha"] for p in gate.CAUSALS)])
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

    def test_hosted_rejects_foreign_run_job_attempt_image_and_workflow(self):
        changes = (("GITHUB_ACTIONS", "false"), ("GITHUB_EVENT_NAME", "push"),
                   ("RUNNER_OS", "Linux"), ("RUNNER_ARCH", "ARM64"),
                   ("RUNNER_ENVIRONMENT", "self-hosted"), ("ImageOS", "win22"), ("ImageVersion", ""),
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

    def test_hosted_unknown_or_foreign_authority_stops_before_path_or_command(self):
        for catalogue, source in (({}, gate.FIXED["sha"]), ({"remote": self.auth}, "f" * 40)):
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



if __name__ == "__main__":
    unittest.main()
