"""Portable negative controls for the frozen SDK G2 gate; no SDK process starts."""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tooling import ci_sdk_daemon_stdin_g2 as gate


def environment():
    source = "a" * 40
    return {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": source, "GITHUB_WORKFLOW_SHA": source, "MW_DAEMON_GATE_SHA": source,
            "MW_DAEMON_SDK_SHA": gate.SDK_SHA, "MW_DAEMON_SCOPE": gate.SCOPE,
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
            "GITHUB_JOB": "linux-real-host-g2", "GITHUB_REPOSITORY": "seradotcom/motionwright",
            "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/" + gate.WORKFLOW + "@refs/heads/review"}


def identity(name="semwrightd"):
    target = Path(tempfile.gettempdir()).absolute() / "owned-daemon-target"
    return {"absolute_path": str(target / "debug" / name), "path": "debug/" + name,
            "bytes": 123, "sha256": "b" * 64, "identity": [1, 2, 123, 4, 1]}


def roster_bytes(names):
    return ("".join(name + ": test\n" for name in names) + f"\n{len(names)} tests, 0 benchmarks\n").encode()


def rust_result(name, total):
    return (f"running 1 test\ntest {name} ... ok\n\n"
            f"test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; {total - 1} filtered out; finished in 0.01s\n").encode()


def observation(name, binary, context):
    return {"observation": "DIRECT_PROCESS_EXIT_OBSERVED", "log_custody": "VERIFIED_SNAPSHOT",
            "owned_group_after_wait": "ABSENT", "argv": gate.case_argv(binary, name),
            "context": context, "exit_code": 0}


def cargo_raw(specs, repo, target, *, testing=False):
    rows = []
    for selected in specs:
        features = (["private-host-stdin-custody"] if selected["name"] == "semwrightd"
                    else ["test-tools"] if selected["crate"] == "driver-host" else [])
        rows.append({"reason": "compiler-artifact", "manifest_path": str(repo / "crates" / selected["crate"] / "Cargo.toml"),
                     "target": {"name": selected["name"], "kind": ["bin"], "src_path": str(repo / selected["source"])},
                     "executable": str(target / "debug" / selected["name"]), "profile": {"test": testing}, "features": features})
    rows.append({"reason": "build-finished", "success": True})
    return rows


def lines(rows):
    return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()


class RequestAndSourceControls(unittest.TestCase):
    def test_loader_audit_override_is_rejected_before_any_directory_or_command(self):
        with mock.patch.object(gate.observer, "build_environment") as build, \
                mock.patch.object(Path, "mkdir") as mkdir:
            with self.assertRaisesRegex(gate.custody.VerificationError, "LD_AUDIT"):
                gate.isolated_environment({"LD_AUDIT": "untrusted.so"}, Path("target"), Path("tmp"), Path("run"))
            build.assert_not_called()
            mkdir.assert_not_called()

    def test_only_manual_g2_provisions_exact_reviewed_dependencies(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / gate.WORKFLOW).read_text(encoding="utf-8")
        source, physical = workflow.split("\n  linux-real-host-g2:\n", 1)
        name = "      - name: Provision the four reviewed Linux dependencies with originals\n"
        self.assertNotIn(name, source)
        self.assertEqual(physical.count(name), 1)
        self.assertIn("if: github.event_name == 'workflow_dispatch' && inputs.verification_scope == 'linux-real-host-g2'", physical)
        self.assertIn("needs: source-controls", physical)
        block = physical.split(name, 1)[1].split("      - uses:", 1)[0]
        inline = block.split("          python -I -B - <<'PY'\n", 1)[1].rsplit("          PY\n", 1)[0]
        code = "\n".join(line[10:] for line in inline.splitlines())
        tree = ast.parse(code)
        assignments = {n.targets[0].id: n.value for n in tree.body if isinstance(n, ast.Assign)
                       and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
        self.assertEqual(ast.literal_eval(assignments["packages"]),
                         ("libpipewire-0.3-dev", "pkg-config", "clang", "bubblewrap"))
        commands = assignments["commands"]
        self.assertIsInstance(commands, ast.Tuple)
        labels = [ast.literal_eval(item.elts[0]) for item in commands.elts]
        self.assertEqual(labels, ["apt-update", "apt-install", "package-versions", "pipewire-version", "clang-version", "bubblewrap-version"])
        install = commands.elts[1].elts[1]
        constants = [item.value for item in install.elts if isinstance(item, ast.Constant)]
        self.assertIn("--no-install-recommends", constants)
        self.assertEqual(constants[:2], ["sudo", "apt-get"])
        self.assertIn("gate.observer.command", code)
        self.assertIn("gate.sdk_gate.verify_commands", code)
        self.assertIn("UNKNOWN_NO_RETRY", code)
        self.assertIn("version >= (0, 3, 65)", code)
        self.assertNotIn("sysctl", code)
        self.assertNotIn("apparmor", code.lower())
        self.assertNotIn("${{ runner.temp }}", workflow)

    def test_source_push_never_authorizes_real_cases(self):
        env = {**environment(), "GITHUB_EVENT_NAME": "push", "MW_DAEMON_SCOPE": gate.SOURCE_SCOPE,
               "GITHUB_RUN_ATTEMPT": "2"}
        self.assertEqual(gate.admit_request(env)["sdk_source_sha"], None)
        with self.assertRaises(gate.custody.VerificationError):
            gate.execution_context(env, "linux")
        env["MW_DAEMON_SCOPE"] = gate.SCOPE
        with self.assertRaisesRegex(gate.custody.VerificationError, "never automatic"):
            gate.admit_request(env)

    def test_exact_manual_first_attempt_has_separate_source_identities(self):
        self.assertEqual((gate.SDK_SHA, gate.SDK_TREE, gate.SDK_PARENT),
                         ("8e8a2f4d91d0d573dc31be439844b99e3ec1d48a",
                          "2994fc0ccef7c6a9762bcb1181ae95b322d8db4b",
                          "4dee8069fecf6a31cfc5e0c4bb8c3c5a9ce11950"))
        workflow = (Path(__file__).resolve().parents[2] / gate.WORKFLOW).read_text(encoding="utf-8")
        self.assertIn("        default: 8e8a2f4d91d0d573dc31be439844b99e3ec1d48a\n", workflow)
        context = gate.execution_context(environment(), "linux")
        self.assertEqual(context["sdk_source_sha"], gate.SDK_SHA)
        self.assertEqual(context["sdk_source_tree"], gate.SDK_TREE)
        self.assertNotEqual(context["sdk_source_sha"], context["GITHUB_SHA"])
        self.assertEqual(context["GITHUB_RUN_ATTEMPT"], "1")

    def test_request_rejects_other_sources_or_attempts(self):
        mutations = {"MW_DAEMON_GATE_SHA": "b" * 40, "GITHUB_WORKFLOW_SHA": "b" * 40,
                     "MW_DAEMON_SDK_SHA": gate.SDK_PARENT, "GITHUB_RUN_ATTEMPT": "2",
                     "MW_DAEMON_SCOPE": "all", "GITHUB_SHA": "main", "GITHUB_EVENT_NAME": "pull_request"}
        for key, value in mutations.items():
            with self.subTest(key=key), self.assertRaises(gate.custody.VerificationError):
                gate.admit_request({**environment(), key: value})

    def test_real_context_rejects_foreign_runner_job_and_workflow(self):
        mutations = {"RUNNER_OS": "Windows", "RUNNER_ARCH": "ARM64", "GITHUB_ACTIONS": "false",
                     "GITHUB_JOB": "source-controls", "GITHUB_RUN_ID": "0",
                     "GITHUB_WORKFLOW_REF": "another/repository/workflow.yml@main",
                     "GITHUB_REPOSITORY": "another/repository"}
        for key, value in mutations.items():
            with self.subTest(key=key), self.assertRaises(gate.custody.VerificationError):
                gate.execution_context({**environment(), key: value}, "linux")
        with self.assertRaises(gate.custody.VerificationError):
            gate.execution_context(environment(), "win32")

    def inventory(self):
        return {*gate.SDK_PINS, "Cargo.toml", "rust-toolchain.toml", "crates/daemon/Cargo.toml",
                "crates/driver-host/Cargo.toml", "crates/plugin-host/Cargo.toml", "crates/daemon/src/semwrightd.rs",
                "crates/driver-host/src/bin/tool_fixture.rs", "crates/plugin-host/src/semwright-sandbox.rs",
                ".cargo/config.toml", "crates/foundation/build.rs", "fixtures/actual-data.json"}

    def test_inventory_keeps_all_build_inputs_and_non_rust_data(self):
        entries = sorted(self.inventory())
        with mock.patch.object(gate.custody, "git", return_value=("\0".join(entries) + "\0").encode()):
            self.assertEqual(gate.input_paths(Path("sdk")), tuple(entries))

    def test_inventory_rejects_omission_duplicate_and_unsafe_path(self):
        entries = sorted(self.inventory())
        for changed in (entries + [entries[0]], [p for p in entries if p != "Cargo.lock"],
                        entries + ["../foreign.rs"], []):
            with self.subTest(changed=changed[-1:] if changed else []), mock.patch.object(gate.custody, "git", return_value=("\0".join(changed) + "\0").encode()):
                with self.assertRaises(gate.custody.VerificationError):
                    gate.input_paths(Path("sdk"))

    def test_sdk_lock_tree_and_changed_input_pins_are_not_reassigned(self):
        before = {"parents": [gate.SDK_PARENT], "files": {p: {"sha256": h} for p, h in gate.SDK_PINS.items()}}
        for change in (None, "parent", "lock", "bridge"):
            snapshot = deepcopy(before)
            if change == "parent": snapshot["parents"] = ["c" * 40]
            if change == "lock": snapshot["files"]["Cargo.lock"]["sha256"] = "0" * 64
            if change == "bridge": snapshot["files"]["crates/daemon/src/host_stdin_custody_bridge_tests.rs"]["sha256"] = "0" * 64
            with self.subTest(change=change), mock.patch.object(gate.custody, "source_snapshot", return_value=snapshot) as check:
                if change is None:
                    self.assertEqual(gate.source_snapshot(Path("sdk"), ("all",)), before)
                else:
                    with self.assertRaises(gate.custody.VerificationError): gate.source_snapshot(Path("sdk"), ("all",))
                check.assert_called_once_with(Path("sdk"), gate.SDK_SHA, ("all",), gate.SDK_TREE)

    def test_exact_preparer_is_reused_without_source_edit(self):
        root = Path(__file__).resolve().parents[2]
        raw = (root / gate.PREPARER).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), gate.GATE_PINS[gate.PREPARER])
        self.assertEqual(len(raw), 3106)


class CompilerAndRosterControls(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.gettempdir()).absolute() / "sdk-source"
        self.target = Path(tempfile.gettempdir()).absolute() / "target-source"

    def test_producer_plan_builds_three_auxiliaries_and_one_test_binary_once(self):
        plan = gate.producer_plan("cargo-exact")
        self.assertEqual([row[0] for row in plan], ["driver-fixtures", "sandbox", "daemon-tests"])
        self.assertEqual([len(row[2]) for row in plan], [2, 1, 1])
        self.assertEqual([row[3] for row in plan], [False, False, True])
        self.assertTrue(all("--locked" in row[1] and "--message-format=json" in row[1] for row in plan))
        self.assertIn("--no-run", plan[2][1])
        self.assertFalse(any("--ignored" in row[1] for row in plan))

    def test_cargo_json_binds_each_selected_executable_to_its_source(self):
        for specs, testing in ((gate.DRIVER_SPECS, False), (gate.SANDBOX_SPECS, False), (gate.TEST_SPECS, True)):
            raw = lines(cargo_raw(specs, self.repo, self.target, testing=testing))
            found = gate.compiled_paths(raw, 0, self.repo, self.target, specs, testing)
            self.assertEqual(set(found), {s["name"] for s in specs})
            self.assertTrue(all(Path(path).is_relative_to(self.target) for path in found.values()))

    def test_cargo_json_rejects_missing_duplicate_foreign_or_incomplete_artifacts(self):
        rows = cargo_raw(gate.DRIVER_SPECS, self.repo, self.target)
        mutations = []
        mutations.append(rows[1:])
        mutations.append(rows[:1] + rows)
        for key, value in (("executable", str(self.repo / "foreign")), ("manifest_path", str(self.repo / "other/Cargo.toml")),
                           ("features", []), ("profile", {"test": True})):
            changed = deepcopy(rows); changed[0][key] = value; mutations.append(changed)
        changed = deepcopy(rows); changed[-1]["success"] = False; mutations.append(changed)
        for changed in mutations:
            with self.subTest(changed=changed[0]), self.assertRaises(gate.custody.VerificationError):
                gate.compiled_paths(lines(changed), 0, self.repo, self.target, gate.DRIVER_SPECS, False)
        for code in (True, 101):
            with self.subTest(code=code), self.assertRaises(gate.custody.VerificationError):
                gate.compiled_paths(lines(rows), code, self.repo, self.target, gate.DRIVER_SPECS, False)

    def test_daemon_private_feature_cannot_be_omitted(self):
        rows = cargo_raw(gate.TEST_SPECS, self.repo, self.target, testing=True)
        rows[0]["features"] = []
        with self.assertRaisesRegex(gate.custody.VerificationError, "feature absent"):
            gate.compiled_paths(lines(rows), 0, self.repo, self.target, gate.TEST_SPECS, True)

    def test_real_enumeration_distinguishes_four_selected_from_other_tests(self):
        listed = gate.compiled_roster(roster_bytes((*gate.CASES, "other::test")), 0)
        self.assertEqual(listed["selected"], gate.CASES)
        self.assertEqual(listed["not_selected"], ("other::test",))
        self.assertEqual(listed["not_selected_credit"], "NONE")

    def test_roster_rejects_omission_duplicate_extra_and_invented_summary(self):
        values = (roster_bytes(gate.CASES[:-1]), roster_bytes((*gate.CASES, gate.CASES[0])),
                  roster_bytes((*gate.CASES, gate.CASE_PREFIX + "foreign")),
                  roster_bytes(gate.CASES).replace(b"4 tests", b"999 tests"),
                  roster_bytes(gate.CASES) + b"0 tests, 0 benchmarks\n")
        for raw in values:
            with self.subTest(raw=raw[-80:]), self.assertRaises(gate.custody.VerificationError):
                gate.compiled_roster(raw, 0)
        with self.assertRaises(gate.custody.VerificationError): gate.compiled_roster(roster_bytes(gate.CASES), True)

    def test_original_terse_listing_cannot_omit_the_summary(self):
        # Original Linux libtest --list --format terse output: no footer.
        raw = (
            b'host_stdin_custody::bridge_tests::linux_genuine_capture_late_reserved_permit_is_counted_not_false_eof: test\n'
            b'host_stdin_custody::bridge_tests::linux_genuine_capture_stop_during_consumer_terminal_wait_stays_incomplete: test\n'
            b'host_stdin_custody::bridge_tests::linux_real_host_metadata_collision_preserves_partial_bytes_and_native_job: test\n'
            b'host_stdin_custody::bridge_tests::linux_real_host_nonempty_capture_drains_to_bound_original_and_terminal: test\n'
            b'host_stdin_custody::tests::custody_directory_is_denied_to_overlapping_filesystem_grants: test\n'
            b'host_stdin_custody::tests::default_off_and_partial_options_never_enable_custody: test\n'
            b'host_stdin_custody::tests::original_pipe_terminals_are_distinct_and_never_imply_consumption: test\n'
            b'host_stdin_custody::tests::private_directory_refuses_reuse_links_and_nonprivate_parent: test\n'
            b'host_stdin_custody::tests::quota_and_encoded_metadata_reject_before_reserving_output: test\n'
            b'host_stdin_custody::tests::retained_directory_never_follows_a_replacement_path: test\n'
            b'host_stdin_custody::tests::retained_manifest_and_drop_do_not_fabricate_native_terminals: test\n'
            b'host_stdin_custody::tests::selection_requires_exact_existing_unique_provider_and_v7_tool: test\n'
            b'host_stdin_custody::tests::setup_sanitization_preserves_known_and_unknown_without_private_details: test\n'
            b'stdin_custody_cli_tests::diagnostic_binary_requires_explicit_cli_selection: test\n'
            b'stdin_custody_cli_tests::ordinary_cli_does_not_enable_custody: test\n'
        )
        self.assertEqual(len(raw), 1436)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "a640af1efee6b60e6f5d49976fdf287d65010b8a81e08145a4f0e756d0db1577")
        names = gate.observer.selected_roster(raw, 0, {"prefixes": (gate.CASE_PREFIX,)}, gate.CASES, "repair")
        self.assertEqual(len(names["all"]), 15)
        self.assertEqual(names["selected"], gate.CASES)
        self.assertEqual(len(names["not_selected"]), 11)
        self.assertEqual(names["not_selected_credit"], "NONE")
        with self.assertRaisesRegex(gate.custody.VerificationError,
                                    "enumeration summary does not match its own IDs"):
            gate.compiled_roster(raw, 0)

    def test_real_listing_call_requests_pretty_and_keeps_observation_contract(self):
        # Actual Rust 1.98.1 --list outputs from one inert compiled fixture.
        # Only the two enumeration statements run here, with process/log seams.
        listings = {"terse": b'list_only_fixture: test\n', "pretty": b'list_only_fixture: test\n\n1 test, 0 benchmarks\n'}
        self.assertEqual(hashlib.sha256(listings["terse"]).hexdigest(),
                         "5c81185603de099f5390153fe094b172620c9c84ca159b83c5ff444290503d10")
        self.assertEqual(hashlib.sha256(listings["pretty"]).hexdigest(),
                         "62356216f8ecf2ca59c9e7fd557adff9e16260c47fa48930b55fb7273bd9cd41")
        module = ast.parse(Path(gate.__file__).read_bytes())
        function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "run")
        body = next(node for node in function.body if isinstance(node, ast.Try)).body
        indices = [index for index, node in enumerate(body)
                   if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                   and isinstance(node.value.func, ast.Name) and node.value.func.id == "barrier"
                   and len(node.value.args) == 7 and isinstance(node.value.args[4], ast.Constant)
                   and node.value.args[4].value == "compiled-roster"]
        self.assertEqual(len(indices), 1)
        index = indices[0]
        self.assertIsInstance(body[index + 1], ast.Assign)
        self.assertEqual(body[index + 1].value.func.id, "compiled_roster")
        command = mock.Mock(side_effect=lambda argv, *_args: {"argv": argv, "exit_code": 0})
        read = mock.Mock(side_effect=lambda _logs, row, _stream: listings[row["argv"][-1]])
        binary, env, logs, context = identity(), {"scope": "list-only-fixture"}, Path("logs"), {"source": "fixture"}
        namespace = {"__builtins__": {}, "barrier": command, "binary": binary, "sdk": self.repo,
                     "env": env, "logs": logs, "context": context,
                     "observer": SimpleNamespace(original_stream=read), "compiled_roster": gate.compiled_roster}
        statements = ast.Module(body=deepcopy(body[index:index + 2]), type_ignores=[])
        with mock.patch.object(gate, "CASE_PREFIX", "list_only_fixture"), \
                mock.patch.object(gate, "CASES", ("list_only_fixture",)):
            exec(compile(statements, "actual-compiled-roster-statements", "exec"), namespace)
        command.assert_called_once_with([binary["absolute_path"], "--list", "--format", "pretty"],
                                        self.repo, env, logs, "compiled-roster", 45, context)
        read.assert_called_once_with(logs, namespace["row"], "stdout")
        self.assertEqual(namespace["listed"]["all"], ("list_only_fixture",))
        self.assertEqual(namespace["listed"]["selected"], ("list_only_fixture",))
        self.assertEqual(namespace["listed"]["not_selected"], ())
        self.assertEqual(namespace["listed"]["not_selected_credit"], "NONE")

    def test_producer_barrier_forbids_build_after_first_observation(self):
        delegate = mock.Mock()
        barrier = gate.sdk_gate.ProducerBarrier(delegate, "cargo-exact")
        with self.assertRaises(gate.custody.VerificationError): barrier(["testbinary", "--list"])
        barrier(["cargo-exact", "test", "--no-run"])
        barrier.begin_sealing()
        with self.assertRaises(gate.custody.VerificationError): barrier(["anything"])
        barrier.begin_observation()
        barrier(["testbinary", "--list"])
        with self.assertRaises(gate.custody.VerificationError): barrier(["cargo-exact", "build"])
        self.assertEqual(delegate.call_count, 2)


class CapabilityAndResultControls(unittest.TestCase):
    def setUp(self):
        self.binary = identity()
        self.listed = gate.compiled_roster(roster_bytes((*gate.CASES, "other::test")), 0)
        self.name = gate.CASES[0]
        self.context = {"sdk_source_sha": gate.SDK_SHA, "case_name": self.name}
        self.row = observation(self.name, self.binary, self.context)

    def test_case_admits_only_one_observed_unignored_terminal(self):
        result = gate.admit_case(self.name, rust_result(self.name, 5), self.row, self.binary,
                                 self.binary, self.listed, self.context)
        self.assertEqual(result["status"], "PASS_EXACT_REAL_HOST_CASE")
        self.assertEqual(result["child_consumption"], "NOT_OBSERVED")
        self.assertEqual(result["native_render"], "NOT_INFERRED")
        self.assertIn("--ignored", self.row["argv"])

    def test_case_rejects_omission_duplicates_wrong_counts_and_panics(self):
        raw = rust_result(self.name, 5)
        variants = (raw.replace(b" ... ok", b" ... ignored"), raw + raw,
                    raw.replace(self.name.encode(), gate.CASES[1].encode()),
                    raw.replace(b"1 passed", b"0 passed"), raw.replace(b"4 filtered out", b"0 filtered out"),
                    raw + b"thread 'test' panicked at source.rs:1:2:\n")
        for value in variants:
            with self.subTest(value=value[-80:]), self.assertRaises(gate.custody.VerificationError):
                gate.admit_case(self.name, value, self.row, self.binary, self.binary, self.listed, self.context)

    def test_case_rejects_unknown_nonzero_changed_binary_or_foreign_context(self):
        mutations = (("observation", "UNKNOWN_NO_RETRY"), ("exit_code", 101), ("exit_code", False),
                     ("owned_group_after_wait", "PRESENT"), ("log_custody", "INCOMPLETE"),
                     ("argv", self.row["argv"][:-1]), ("context", {"sdk_source_sha": gate.SDK_PARENT}))
        for key, value in mutations:
            with self.subTest(key=key), self.assertRaises(gate.custody.VerificationError):
                gate.admit_case(self.name, rust_result(self.name, 5), {**self.row, key: value},
                                self.binary, self.binary, self.listed, self.context)
        with self.assertRaises(gate.custody.VerificationError):
            gate.admit_case(self.name, rust_result(self.name, 5), self.row, self.binary,
                            {**self.binary, "sha256": "c" * 64}, self.listed, self.context)

    def test_landlock_probe_requires_v3_and_never_loads_native_code_in_this_control(self):
        for value in (-1, 0, 1, 2, 3, 6):
            syscall = mock.Mock(return_value=value)
            with self.subTest(value=value), mock.patch.object(gate.sys, "platform", "linux"), \
                    mock.patch.object(gate.platform, "machine", return_value="x86_64"), \
                    mock.patch.object(gate.ctypes, "CDLL", return_value=SimpleNamespace(syscall=syscall)), \
                    mock.patch.object(gate.ctypes, "get_errno", return_value=0):
                if value < 3:
                    with self.assertRaises(gate.CapabilityUnavailable): gate.landlock_abi()
                else:
                    self.assertEqual(gate.landlock_abi(), value)
                self.assertEqual(syscall.call_count, 1)

    def test_actual_helper_probe_uses_readonly_roots_and_private_write_mounts(self):
        argv = gate.sandbox_probe_argv("bwrap", "sealed-helper", "sealed-true", ("/usr", "/lib", "/lib64", "/etc"))
        self.assertEqual(argv[-5:], ["/tmp", "--", "/plugin/helper", "--", "/plugin/bin"])
        for name in ("/tmp", "/dev/shm"):
            self.assertIn(["--tmpfs", name], [argv[i:i + 2] for i in range(len(argv) - 1)])
        self.assertIn(["--ro-bind", "sealed-true", "/plugin/bin"], [argv[i:i + 3] for i in range(len(argv) - 2)])
        self.assertNotIn("--bind", argv)
        self.assertFalse(any("sysctl" in word or "sudo" in word or "disable" in word for word in argv))
        with self.assertRaises(gate.custody.VerificationError):
            gate.sandbox_probe_argv("bwrap", "helper", "true", ("/usr", "/lib", "/etc", "/home"))

    def test_capability_denial_is_not_pass_and_unknown_remains_unknown(self):
        gate.admit_capability(self.row)
        for code in (125, 1, -9, False, None):
            with self.subTest(code=code), self.assertRaises(gate.CapabilityUnavailable):
                gate.admit_capability({**self.row, "exit_code": code})
        unknown = {**self.row, "observation": "UNKNOWN_PROCESS_NO_RETRY"}
        with self.assertRaises(gate.observer.UnknownExecution) as result:
            gate.admit_capability(unknown)
        self.assertIs(result.exception.command, unknown)

    def test_resource_absence_is_not_run_not_omitted_success(self):
        good = gate.check_budget(b"MemAvailable: 3145728 kB\n", 20 * 1024**3)
        self.assertEqual(good["cargo_jobs"], 2)
        for mem, disk in ((b"MemAvailable: 1 kB\n", 20 * 1024**3), (b"MemAvailable: 3145728 kB\n", 1)):
            with self.subTest(mem=mem, disk=disk), self.assertRaises(gate.CapabilityUnavailable):
                gate.check_budget(mem, disk)

    def test_fixture_paths_and_hashes_are_exact_not_environment_overrides(self):
        root = Path(tempfile.gettempdir()).absolute() / "owned-fixture"
        binaries = {name: identity(name) for name in ("semwright-driver-fixture", "semwright-tool-fixture", "semwright-sandbox")}
        value = {gate.FIXTURE_VARIABLES[0]: str(root / "config.toml"),
                 gate.FIXTURE_VARIABLES[1]: binaries["semwright-sandbox"]["absolute_path"],
                 "driver_sha256": "b" * 64, "tool_sha256": "b" * 64,
                 "manifest_sha256": "c" * 64, "config_sha256": "d" * 64}
        self.assertEqual(gate.fixture_payload(json.dumps(value).encode(), root, binaries), value)
        mutations = ((gate.FIXTURE_VARIABLES[0], str(root.parent / "foreign.toml")),
                     (gate.FIXTURE_VARIABLES[1], "other-helper"), ("tool_sha256", "0" * 64), ("driver_sha256", None),
                     ("SEMWRIGHT_NVIDIA_GPU", "1"))
        for key, replacement in mutations:
            with self.subTest(key=key), self.assertRaises(gate.custody.VerificationError):
                gate.fixture_payload(json.dumps({**value, key: replacement}).encode(), root, binaries)

    def test_final_roster_cannot_hide_a_missing_duplicated_or_failed_case(self):
        results = [{"name": name, "status": "PASS_EXACT_REAL_HOST_CASE"} for name in gate.CASES]
        self.assertEqual(gate.final_counts(results, self.listed)["passed"], 4)
        for changed in (results[:-1], results[:-1] + results[:1],
                        [{**results[0], "status": "SKIPPED"}, *results[1:]]):
            with self.subTest(changed=changed), self.assertRaises(gate.custody.VerificationError):
                gate.final_counts(changed, self.listed)


class NoReplayControls(unittest.TestCase):
    def harness(self, command):
        binary = identity()
        fixture = {"scope": "portable-control"}
        report = {"cases": [], "fixture": fixture}
        listed = gate.compiled_roster(roster_bytes(gate.CASES), 0)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(gate.sdk_gate, "verify_binaries"))
        stack.enter_context(mock.patch.object(gate, "verify_fixture", return_value=fixture))
        stack.enter_context(mock.patch.object(gate.custody, "fingerprint", return_value={k: v for k, v in binary.items() if k != "absolute_path"}))
        stack.enter_context(mock.patch.object(gate.observer, "original_stream", side_effect=lambda folder, row, stream:
                                          rust_result(row["argv"][2], 4) if stream == "stdout" else b""))
        args = (report, binary, {"semwrightd": binary}, listed, Path("sdk"), Path("target"), {}, Path("logs"),
                Path("evidence"), Path("fixture"), b"{}", {"sdk_source_sha": gate.SDK_SHA}, command)
        return report, args

    def test_four_cases_execute_once_after_roster_and_never_rebuild(self):
        def run(argv, _sdk, _env, _logs, _label, _timeout, context):
            return observation(argv[2], identity(), context)
        command = mock.Mock(side_effect=run)
        report, args = self.harness(command)
        gate.observe_cases(*args)
        self.assertEqual([row["name"] for row in report["cases"]], list(gate.CASES))
        self.assertEqual(command.call_count, 4)
        self.assertEqual([call.args[0][2] for call in command.call_args_list], list(gate.CASES))
        self.assertTrue(all(call.args[0][0] == identity()["absolute_path"] for call in command.call_args_list))

    def test_unknown_first_observation_stops_without_another_case_or_replay(self):
        unknown = {"observation": "UNKNOWN_TIMEOUT_NO_RETRY", "pid": 12345}
        command = mock.Mock(side_effect=gate.observer.UnknownExecution("fixture UNKNOWN", unknown))
        report, args = self.harness(command)
        with self.assertRaises(gate.observer.UnknownExecution) as result:
            gate.observe_cases(*args)
        self.assertIs(result.exception.command, unknown)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(report["cases"], [])
        self.assertEqual(report["currently_selected_case"], gate.CASES[0])

    def test_nonzero_second_case_preserves_first_and_does_not_start_third(self):
        def run(argv, _sdk, _env, _logs, _label, _timeout, context):
            row = observation(argv[2], identity(), context)
            if argv[2] == gate.CASES[1]: row["exit_code"] = 101
            return row
        command = mock.Mock(side_effect=run)
        report, args = self.harness(command)
        with self.assertRaises(gate.custody.VerificationError): gate.observe_cases(*args)
        self.assertEqual(command.call_count, 2)
        self.assertEqual([row["name"] for row in report["cases"]], [gate.CASES[0]])

    def test_source_postcheck_cannot_promote_unknown_or_mutated_source(self):
        before = {"identity": "original"}
        for status in ("UNKNOWN_NO_RETRY", "PASS_EXACT_FOUR_REAL_HOST_G2_CASES"):
            report = {"status": status, "source_before": before}
            with mock.patch.object(gate, "source_snapshot", side_effect=OSError("unreadable original")):
                gate.postcheck_sources(report, Path("gate"), Path("sdk"), ())
            self.assertEqual(report["status"], "UNKNOWN_NO_RETRY" if status.startswith("UNKNOWN") else "FAIL_OR_INCOMPLETE")
            self.assertIn("source_postcheck_errors", report)
            self.assertEqual(report["source_before"], before)


if __name__ == "__main__":
    unittest.main()
