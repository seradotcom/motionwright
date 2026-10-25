"""Adversarial source-only controls for the real-OS SQLite authority G2 runner."""
import unittest
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest import mock
from contextlib import ExitStack, redirect_stdout
import io

from tooling import ci_sqlite_os_path_g2 as gate


def successful(platform):
    names = gate.CASES[platform]
    body = "running " + str(len(names)) + " tests\n"
    body += "".join(f"test {case} ... ok\n" for case in names)
    body += (
        "\ntest result: ok. " + str(len(names)) +
        " passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n"
    )
    return body.encode()


class PhysicalSQLitePathG2Contracts(unittest.TestCase):
    def test_windows_three_positive(self):
        result = gate.verify_rust_result(successful("windows"), b"", "windows", 0)
        self.assertEqual(result["summary"], [3, 0, 0, 0, 0])
        self.assertCountEqual(result["observed"], gate.CASES["windows"])

    def test_macos_two_positive(self):
        result = gate.verify_rust_result(successful("macos"), b"", "macos", 0)
        self.assertEqual(result["summary"], [2, 0, 0, 0, 0])
        self.assertCountEqual(result["observed"], gate.CASES["macos"])

    def test_nonzero_process_exit_rejected(self):
        with self.assertRaisesRegex(gate.VerificationError, "exit"):
            gate.verify_rust_result(successful("windows"), b"", "windows", 1)

    def test_missing_selected_test_rejected(self):
        original = successful("windows")
        modified = original.replace(
            f"test {gate.CASES['windows'][0]} ... ok\n".encode(), b""
        )
        with self.assertRaisesRegex(gate.VerificationError, "number"):
            gate.verify_rust_result(modified, b"", "windows", 0)

    def test_duplicate_selected_test_rejected(self):
        original = successful("macos")
        line = f"test {gate.CASES['macos'][0]} ... ok\n".encode()
        with self.assertRaisesRegex(gate.VerificationError, "number"):
            gate.verify_rust_result(original.replace(line, line + line), b"", "macos", 0)

    def test_replaced_result_with_ignored_rejected(self):
        original = successful("windows")
        modified = original.replace(b" ... ok\n", b" ... ignored\n", 1)
        with self.assertRaisesRegex(gate.VerificationError, "omitted"):
            gate.verify_rust_result(modified, b"", "windows", 0)

    def test_wrong_test_substituted_rejected(self):
        original = successful("macos")
        modified = original.replace(gate.CASES["macos"][0].encode(), b"unreviewed_test")
        with self.assertRaisesRegex(gate.VerificationError, "omitted"):
            gate.verify_rust_result(modified, b"", "macos", 0)

    def test_terminal_ignored_count_rejected(self):
        original = successful("windows")
        modified = original.replace(
            b"3 passed; 0 failed; 0 ignored", b"2 passed; 0 failed; 1 ignored"
        )
        with self.assertRaisesRegex(gate.VerificationError, "summary"):
            gate.verify_rust_result(modified, b"", "windows", 0)

    def test_filtered_out_test_rejected(self):
        original = successful("macos")
        modified = original.replace(b"0 filtered out", b"1 filtered out")
        with self.assertRaisesRegex(gate.VerificationError, "summary"):
            gate.verify_rust_result(modified, b"", "macos", 0)

    def test_duplicate_terminal_status_rejected(self):
        original = successful("windows")
        tail = b"test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n"
        with self.assertRaisesRegex(gate.VerificationError, "ambiguous"):
            gate.verify_rust_result(original + tail, b"", "windows", 0)

    def test_no_summary_rejected(self):
        original = successful("macos")
        truncated = original.split(b"test result:")[0]
        with self.assertRaisesRegex(gate.VerificationError, "summary"):
            gate.verify_rust_result(truncated, b"", "macos", 0)

    def test_fake_success_without_real_os_rejected(self):
        with self.assertRaisesRegex(gate.VerificationError, "invalid OS scope"):
            gate.verify_rust_result(successful("windows"), b"", "linux", 0)


class ReviewedSQLitePathSourceContracts(unittest.TestCase):
    HEAD = "a" * 40
    BASELINE = "b" * 40
    TREE = "c" * 40

    def fixture(self, root):
        repo = Path(root) / "repo"
        repo.mkdir()
        source = Path(__file__).resolve().parents[2]
        blobs = {}
        for name in gate.REVIEWED_INPUTS:
            raw = (source / name).read_bytes()
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            blobs[name] = raw
        for name in gate.GATE_PATHS[:-1]:
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"Synthetic reviewed gate input\n")
            blobs[name] = target.read_bytes()
        manifest = {"schema": gate.REVIEWED_SCHEMA, "scope": "sqlite-os-path-g2",
                    "baseline_sha": self.BASELINE, "baseline_tree": self.TREE,
                    "review": {"status": "REVIEWED_SOURCE_BASELINE", "authority": "A1",
                               "reference": "SYNTHETIC_CONTROL_ONLY_NOT_AN_AUTHORIZATION"},
                    "product_pins": {name: gate.digest(blobs[name]) for name in gate.REVIEWED_INPUTS}}
        raw = (json.dumps(manifest) + "\n").encode()
        target = repo / gate.REVIEWED_MANIFEST
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        blobs[gate.REVIEWED_MANIFEST] = raw
        state = {"head": self.HEAD, "tree": self.TREE, "changed": list(gate.GATE_PATHS),
                 "dirty": b"", "missing_baseline": False}
        by_oid = {}
        for blob in blobs.values():
            oid = hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest()
            by_oid[oid] = blob
        def git(repo_arg, *args):
            self.assertEqual(repo_arg, repo)
            if args == ("rev-parse", "HEAD"):
                return (state["head"] + "\n").encode()
            if args == ("rev-parse", self.BASELINE + "^{tree}"):
                if state["missing_baseline"]:
                    raise gate.VerificationError("Git source lookup failed or oversized")
                return (state["tree"] + "\n").encode()
            if args == ("rev-parse", "HEAD^{tree}"):
                return ("d" * 40 + "\n").encode()
            if args == ("merge-base", "--is-ancestor", self.BASELINE, self.HEAD):
                return b""
            if args == ("diff", "--no-renames", "--name-only", "-z", self.BASELINE, self.HEAD):
                return b"".join(name.encode() + b"\0" for name in state["changed"])
            if args == ("status", "--porcelain", "--untracked-files=all"):
                return state["dirty"]
            if args == ("rev-list", "--parents", "-n", "1", "HEAD"):
                return (self.HEAD + "\n").encode()  # original shallow-checkout failure
            if args[0] == "show":
                source_sha, name = args[1].split(":", 1)
                self.assertIn(source_sha, (self.HEAD, self.BASELINE))
                return blobs[name]
            if args[:2] == ("ls-tree", "-z"):
                name = args[-1]
                blob = blobs[name]
                oid = hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest()
                return b"100644 blob " + oid.encode() + b"\t" + name.encode() + b"\0"
            if args[:2] == ("cat-file", "blob"):
                return by_oid[args[2]]
            raise AssertionError(args)
        return repo, manifest, raw, state, git

    def test_explicit_reviewed_baseline_accepts_only_gate_composition(self):
        with tempfile.TemporaryDirectory() as root:
            repo, _, raw, _, git = self.fixture(root)
            with mock.patch.object(gate, "git_query", side_effect=git):
                result = gate.reviewed_source_identity(repo, self.HEAD, gate.digest(raw))
            self.assertEqual(result["baseline_sha"], self.BASELINE)
            self.assertEqual(result["execution_credit"], "NONE")
            self.assertEqual(len(result["files"]), 16)

    def test_historical_shallow_ancestry_failure_is_not_relabelled(self):
        with tempfile.TemporaryDirectory() as root:
            repo, _, _, _, git = self.fixture(root)
            with mock.patch.object(gate, "git_query", side_effect=git):
                with self.assertRaisesRegex(gate.VerificationError, "ancestry"):
                    gate.source_identity(repo, self.HEAD)

    def test_missing_reviewed_baseline_fails_without_native_launch(self):
        with tempfile.TemporaryDirectory() as root:
            repo, _, raw, state, git = self.fixture(root)
            state["missing_baseline"] = True
            with mock.patch.object(gate, "git_query", side_effect=git), mock.patch.object(gate, "observe_rust") as launch:
                with self.assertRaisesRegex(gate.VerificationError, "Git source"):
                    gate.reviewed_source_identity(repo, self.HEAD, gate.digest(raw))
            launch.assert_not_called()

    def test_product_diff_dirty_wrong_head_tree_and_duplicate_paths_rejected(self):
        mutations = (("changed", ["crates/domain/src/lib.rs"]), ("dirty", b" M Cargo.lock\n"),
                     ("head", "e" * 40), ("tree", "e" * 40),
                     ("changed", [gate.WORKFLOW, gate.WORKFLOW]))
        for key, value in mutations:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as root:
                repo, _, raw, state, git = self.fixture(root)
                state[key] = value
                with mock.patch.object(gate, "git_query", side_effect=git):
                    with self.assertRaises(gate.VerificationError):
                        gate.reviewed_source_identity(repo, self.HEAD, gate.digest(raw))

    def test_working_manifest_and_product_bytes_cannot_differ_from_git(self):
        for name in (gate.REVIEWED_MANIFEST, "crates/storage/src/lib.rs"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                repo, _, raw, _, git = self.fixture(root)
                path = repo / name
                path.write_bytes(path.read_bytes() + b"changed")
                with mock.patch.object(gate, "git_query", side_effect=git):
                    with self.assertRaisesRegex(gate.VerificationError, "differs"):
                        gate.reviewed_source_identity(repo, self.HEAD, gate.digest(raw))

    def test_manifest_rejects_proposal_missing_extra_wrong_pins_and_definitions(self):
        with tempfile.TemporaryDirectory() as root:
            _, original, _, _, _ = self.fixture(root)
            mutations = []
            value = deepcopy(original); value["review"]["status"] = "PROPOSAL_REQUIRES_A1"; mutations.append(value)
            value = deepcopy(original); value["review"]["reference"] = None; mutations.append(value)
            value = deepcopy(original); value["product_pins"].pop("Cargo.lock"); mutations.append(value)
            value = deepcopy(original); value["product_pins"]["unreviewed.rs"] = "a" * 64; mutations.append(value)
            value = deepcopy(original); value["product_pins"][gate.TEST_PATH["windows"]] = "a" * 64; mutations.append(value)
            value = deepcopy(original); value["baseline_sha"] = "short"; mutations.append(value)
            value = deepcopy(original); value["allow_product_changes"] = True; mutations.append(value)
            for value in mutations:
                raw = json.dumps(value).encode()
                with self.subTest(value=value), self.assertRaises(gate.VerificationError):
                    gate.reviewed_manifest(raw, gate.digest(raw))

    def test_wrong_manifest_digest_and_duplicate_json_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            _, _, raw, _, _ = self.fixture(root)
            with self.assertRaisesRegex(gate.VerificationError, "digest"):
                gate.reviewed_manifest(raw, "f" * 64)
            duplicate = b'{"schema":1,"schema":2}'
            with self.assertRaisesRegex(gate.VerificationError, "duplicate"):
                gate.reviewed_manifest(duplicate, gate.digest(duplicate))

    def test_existing_real_five_tests_remain_fixed_and_platform_scoped(self):
        repo = Path(__file__).resolve().parents[2]
        for platform, name in gate.TEST_PATH.items():
            raw = (repo / name).read_bytes()
            self.assertEqual(gate.digest(raw), gate.SOURCES[name])
            self.assertIn(b'#![cfg(windows)]' if platform == "windows" else b'#![cfg(target_os = "macos")]', raw)
            self.assertEqual(raw.count(b'#[ignore = '), 3 if platform == "windows" else 0)
            for case in gate.CASES[platform]:
                self.assertIn(('fn ' + case + '()').encode(), raw)
        self.assertIn("--ignored", gate.rust_command("windows"))
        self.assertNotIn("--ignored", gate.rust_command("macos"))


class ObservedSQLitePathExecutionContracts(unittest.TestCase):
    HEAD = "a" * 40

    def env(self, root, platform="windows"):
        return {"RUNNER_TEMP": str(root), "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_SHA": self.HEAD, "GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_JOB": gate.JOB_NAMES[platform], "GITHUB_REPOSITORY": "seradotcom/motionwright"}

    def test_first_attempt_exact_job_scope_repository_required(self):
        with tempfile.TemporaryDirectory() as root:
            env = self.env(root)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(gate.run_context(self.HEAD, "windows", 1)["attempt"], "1")
            for key, value in (("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_JOB", gate.JOB_NAMES["macos"]),
                               ("GITHUB_SHA", "b" * 40), ("GITHUB_EVENT_NAME", "push"),
                               ("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_RUN_ID", "0")):
                with self.subTest(key=key), mock.patch.dict(os.environ, {**env, key: value}, clear=True):
                    with self.assertRaises(gate.VerificationError):
                        gate.run_context(self.HEAD, "windows", 1)

    def test_zero_header_bool_exit_invalid_utf8_and_extra_job_output_rejected(self):
        attempts = ((successful("windows").replace(b"running 3", b"running 0"), b"", 0),
                    (successful("windows"), b"", False),
                    (successful("windows") + b"\xff", b"", 0),
                    (successful("windows"), successful("macos"), 0))
        for stdout, stderr, code in attempts:
            with self.subTest(code=code, stderr=stderr):
                with self.assertRaises((gate.VerificationError, UnicodeError)):
                    gate.verify_rust_result(stdout, stderr, "windows", code)

    def test_timeout_keeps_original_streams_and_requests_only_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            process = mock.Mock(pid=321)
            process.wait.side_effect = subprocess.TimeoutExpired("cargo", 540)
            def launch(*args, **kwargs):
                kwargs["stdout"].write(b"partial original stdout\n")
                kwargs["stderr"].write(b"partial original stderr\n")
                self.assertTrue(kwargs.get("start_new_session") or kwargs.get("creationflags"))
                return process
            with mock.patch.object(gate.subprocess, "Popen", side_effect=launch) as start, \
                 mock.patch.object(gate, "stop_owned_process", return_value={"tree_closure": "NOT_PROVEN"}) as stop:
                result = gate.observe_rust(["cargo", "test"], folder, {}, folder / "stdout", folder / "stderr")
            self.assertEqual(start.call_count, 1)
            stop.assert_called_once_with(process, folder)
            self.assertEqual(result["state"], "UNKNOWN_TIMEOUT_NO_RETRY")
            self.assertIsNone(result["exit_code"])
            self.assertEqual((folder / "stdout").read_bytes(), b"partial original stdout\n")
            self.assertEqual((folder / "stderr").read_bytes(), b"partial original stderr\n")

    def test_windows_cleanup_uses_owned_tree_and_retains_original_cleanup_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            process = mock.Mock(pid=4321)
            process.wait.return_value = -9
            def cleanup(command, **kwargs):
                self.assertEqual(command, ["taskkill", "/PID", "4321", "/T", "/F"])
                kwargs["stdout"].write(b"original tree termination stdout\n")
                kwargs["stderr"].write(b"original tree termination stderr\n")
                return mock.Mock(returncode=0)
            with mock.patch.object(gate.os, "name", "nt"), \
                 mock.patch.object(gate.subprocess, "run", side_effect=cleanup) as terminate:
                result = gate.stop_owned_process(process, folder)
            self.assertEqual(terminate.call_count, 1)
            self.assertTrue(result["parent_reaped"])
            self.assertEqual(result["tree_closure"], "NOT_PROVEN")
            self.assertEqual((folder / "original-cleanup.stdout.log").read_bytes(), b"original tree termination stdout\n")
            self.assertEqual((folder / "original-cleanup.stderr.log").read_bytes(), b"original tree termination stderr\n")
            self.assertEqual(json.loads((folder / "cleanup.json").read_bytes())["exit_code"], 0)
            self.assertEqual(result["original_cleanup_streams"]["original-cleanup.stdout.log"]["sha256"],
                             gate.digest(b"original tree termination stdout\n"))

    def test_posix_cleanup_signals_owned_group_then_observes_absence(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            process = mock.Mock(pid=7654)
            process.wait.return_value = -9
            with mock.patch.object(gate.os, "name", "posix"), \
                 mock.patch.object(gate.signal, "SIGKILL", 9, create=True), \
                 mock.patch.object(gate.os, "killpg", side_effect=[None, ProcessLookupError()], create=True) as kill:
                result = gate.stop_owned_process(process, folder)
            self.assertEqual(kill.call_args_list, [mock.call(7654, 9), mock.call(7654, 0)])
            self.assertEqual(result["tree_closure"], "OWNED_PROCESS_GROUP_ABSENT")

    def test_uncertain_cleanup_keeps_unknown_tree_and_partial_cleanup_originals(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            process = mock.Mock(pid=4321)
            process.wait.side_effect = subprocess.TimeoutExpired("cargo", 5)
            def cleanup(command, **kwargs):
                kwargs["stdout"].write(b"partial cleanup\n")
                raise subprocess.TimeoutExpired(command, 15)
            with mock.patch.object(gate.os, "name", "nt"), \
                 mock.patch.object(gate.subprocess, "run", side_effect=cleanup):
                result = gate.stop_owned_process(process, folder)
            self.assertFalse(result["parent_reaped"])
            self.assertEqual(result["tree_closure"], "NOT_PROVEN")
            self.assertEqual((folder / "original-cleanup.stdout.log").read_bytes(), b"partial cleanup\n")
            self.assertIn("TimeoutExpired", json.loads((folder / "cleanup.json").read_bytes())["error"])

    def test_source_rejection_writes_receipt_without_launch(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); repo = base / "repo"; repo.mkdir(); output = base / "out"; output.mkdir()
            report = output / "receipt.json"
            with mock.patch.dict(os.environ, self.env(base), clear=True), \
                 mock.patch.object(gate, "check_environment", return_value={}), \
                 mock.patch.object(gate, "reviewed_source_identity", side_effect=gate.VerificationError("stale source")), \
                 mock.patch.object(gate, "observe_rust") as launch:
                with self.assertRaises(gate.VerificationError):
                    gate.run_selected(repo, self.HEAD, "windows", report, "f" * 64, 1)
            launch.assert_not_called()
            self.assertEqual(json.loads(report.read_bytes())["status"], "SOURCE_OR_ENVIRONMENT_REJECTED")
            self.assertTrue((output / "started.json").exists())

    def test_unknown_stops_source_readback_and_survives_later_custody_error(self):
        for custody_error in (False, True):
            with self.subTest(custody_error=custody_error), tempfile.TemporaryDirectory() as root:
                base = Path(root); repo = base / "repo"; repo.mkdir(); output = base / "out"; output.mkdir()
                report = output / "receipt.json"
                def observed(command, cwd, env, stdout, stderr):
                    stdout.write_bytes(b"partial stdout\n"); stderr.write_bytes(b"partial stderr\n")
                    if custody_error:
                        raise OSError("stream custody failed after launch")
                    return {"state": "UNKNOWN_TIMEOUT_NO_RETRY", "exit_code": None, "automatic_retry": False}
                with mock.patch.dict(os.environ, self.env(base), clear=True), \
                     mock.patch.object(gate, "check_environment", return_value={}), \
                     mock.patch.object(gate, "reviewed_source_identity", return_value={"tree_sha": "c" * 40}) as source, \
                     mock.patch.object(gate, "observe_rust", side_effect=observed) as launch, \
                     mock.patch.object(gate, "verify_rust_result") as verify:
                    with self.assertRaises(gate.VerificationError):
                        gate.run_selected(repo, self.HEAD, "windows", report, "f" * 64, 1)
                self.assertEqual(source.call_count, 1); self.assertEqual(launch.call_count, 1)
                verify.assert_not_called()
                self.assertEqual(json.loads(report.read_bytes())["status"], "UNKNOWN")
                self.assertEqual((output / "original-rust.stdout.log").read_bytes(), b"partial stdout\n")

    def test_success_requires_same_after_source_and_retains_exact_run_context(self):
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as root:
                base = Path(root); repo = base / "repo"; repo.mkdir(); output = base / "out"; output.mkdir()
                report = output / "receipt.json"
                def observed(command, cwd, env, stdout, stderr):
                    self.assertEqual(command, gate.rust_command("windows"))
                    self.assertEqual(env["RUSTUP_TOOLCHAIN"], "1.98.1")
                    stdout.write_bytes(successful("windows")); stderr.write_bytes(b"")
                    return {"state": "OBSERVED_EXIT", "exit_code": 0, "automatic_retry": False}
                before = {"source_sha": self.HEAD, "tree_sha": "c" * 40}
                after = {**before, "tree_sha": "d" * 40} if changed else before
                with mock.patch.dict(os.environ, self.env(base), clear=True), \
                     mock.patch.object(gate, "check_environment", return_value={}), \
                     mock.patch.object(gate, "reviewed_source_identity", side_effect=[before, after]), \
                     mock.patch.object(gate, "observe_rust", side_effect=observed):
                    if changed:
                        with self.assertRaises(gate.VerificationError):
                            gate.run_selected(repo, self.HEAD, "windows", report, "f" * 64, 1)
                    else:
                        gate.run_selected(repo, self.HEAD, "windows", report, "f" * 64, 1)
                receipt = json.loads(report.read_bytes())
                self.assertEqual(receipt["schema"], "motionwright.sqlite-real-os-path-authority/2")
                self.assertEqual(receipt["observed_context"]["GITHUB_SHA"], self.HEAD)
                self.assertEqual(receipt["status"], "FAIL_OR_OMISSION" if changed else "PASS_EXACT_REAL_OS_G2")
                self.assertEqual((receipt["source_sha"], receipt["run_id"], receipt["attempt"], receipt["job_id"]),
                                 (self.HEAD, "123456", "1", gate.JOB_NAMES["windows"]))


class WindowsCASSymlinkContracts(unittest.TestCase):
    """Synthetic source controls; these do not execute Rust or Windows fixtures."""
    HEAD = "a" * 40

    def compiled(self, total=180):
        names = [*gate.CAS_CASES, *("ordinary::case_" + str(i) for i in range(total - 4))]
        return "".join(name + ": test\n" for name in names).encode()

    def output(self, case, total=180):
        return ("running 1 test\ntest " + case + " ... ok\n\n"
                "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; "
                + str(total - 1) + " filtered out; finished in 0.01s\n").encode()

    def env(self, root):
        return {"RUNNER_TEMP": str(root), "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_SHA": self.HEAD, "GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_JOB": gate.JOB_NAMES[gate.CAS_SCOPE], "GITHUB_REPOSITORY": "seradotcom/motionwright"}

    def exercise(self, total=180, mutate=None, unknown_at=None, source_change_at=None,
                 capability_error=False, admission_error=False):
        with tempfile.TemporaryDirectory() as root_string:
            root = Path(root_string).resolve()
            repo, evidence = root / "repo", root / "evidence"
            repo.mkdir(); evidence.mkdir()
            report = evidence / "cas.json"
            before = {"source_sha": self.HEAD, "tree_sha": "b" * 40}
            commands = []
            source_calls = []
            def source(*args):
                source_calls.append(args)
                if admission_error:
                    raise gate.VerificationError("A1 reviewed baseline required; proposal is not admission")
                if len(source_calls) == source_change_at:
                    return {**before, "tree_sha": "c" * 40}
                return before
            def observed(command, cwd, env, stdout, stderr):
                index = len(commands)
                commands.append(command)
                self.assertEqual(cwd, repo)
                self.assertEqual(env["RUSTUP_TOOLCHAIN"], "1.98.1")
                raw = self.compiled(total) if index == 0 else self.output(gate.CAS_CASES[index - 1], total)
                if mutate:
                    raw = mutate(index, raw)
                stdout.write_bytes(raw); stderr.write_bytes(b"synthetic observer bytes\n")
                return {"state": "UNKNOWN_TIMEOUT_NO_RETRY" if index == unknown_at else "OBSERVED_EXIT",
                        "exit_code": None if index == unknown_at else 0, "automatic_retry": False}
            with ExitStack() as stack:
                stack.enter_context(mock.patch.dict(os.environ, self.env(root), clear=True))
                stack.enter_context(mock.patch.object(gate, "check_environment",
                    side_effect=gate.VerificationError("genuine Windows symlink capability unavailable") if capability_error else None,
                    return_value={"synthetic_only": True}))
                stack.enter_context(mock.patch.object(gate, "reviewed_source_identity", side_effect=source))
                stack.enter_context(mock.patch.object(gate, "observe_rust", side_effect=observed))
                stack.enter_context(redirect_stdout(io.StringIO()))
                try:
                    gate.run_windows_cas(repo, self.HEAD, report, "f" * 64, 1)
                except gate.VerificationError:
                    pass
            receipt = json.loads(report.read_bytes())
            saved = {path.relative_to(evidence).as_posix(): path.read_bytes()
                     for path in evidence.rglob("*") if path.is_file()}
            return receipt, commands, source_calls, saved

    def test_compiled_library_roster_drives_selection_without_fixed_180(self):
        for count in (4, 180, 181):
            with self.subTest(count=count):
                roster = gate.cas_compiled_roster(self.compiled(count), b"compiler messages\n", 0)
                self.assertEqual(roster["selected"], list(gate.CAS_CASES))
                self.assertEqual(roster["compiled_count"], count)
                self.assertEqual(len(roster["not_selected"]), count - 4)
                self.assertEqual(roster["not_selected_credit"], "NONE")
                with_total = self.compiled(count) + (str(count) + " tests, 0 benchmarks\n").encode()
                self.assertEqual(gate.cas_compiled_roster(with_total, b"", 0), roster)

    def test_compiled_roster_rejects_missing_duplicate_foreign_format_and_counts(self):
        raw = self.compiled()
        bad = (raw.replace((gate.CAS_CASES[0] + ": test\n").encode(), b""),
               raw + (gate.CAS_CASES[0] + ": test\n").encode(), b"",
               raw + b"foreign: benchmark\n", raw + b"179 tests, 0 benchmarks\n",
               raw + b"180 tests, 0 benchmarks\n180 tests, 0 benchmarks\n", raw + b"\xff")
        for changed in bad:
            with self.subTest(changed=changed[-80:]), self.assertRaises((gate.VerificationError, UnicodeDecodeError)):
                gate.cas_compiled_roster(changed, b"", 0)
        for code in (1, 101, False):
            with self.subTest(code=code), self.assertRaises(gate.VerificationError):
                gate.cas_compiled_roster(raw, b"", code)
        with self.assertRaisesRegex(gate.VerificationError, "stderr"):
            gate.cas_compiled_roster(raw, b"test fake ... ok\n", 0)

    def test_all_four_commands_require_exact_ignored_one_case_and_no_fallback(self):
        base = ["cargo", "test", "--locked", "-p", "motionwright-storage", "--lib"]
        self.assertEqual(gate.cas_command(), [*base, "--", "--list", "--format", "terse", "--color", "never"])
        for case in gate.CAS_CASES:
            self.assertEqual(gate.cas_command(case), [*base, case, "--", "--exact", "--ignored", "--nocapture",
                                                     "--test-threads=1", "--color", "never"])
        with self.assertRaises(gate.VerificationError):
            gate.cas_command("blob_hash_tests")

    def test_exact_case_nominal_validation_tracks_real_compiled_count(self):
        for count in (4, 180, 181):
            roster = gate.cas_compiled_roster(self.compiled(count), b"", 0)
            for case in gate.CAS_CASES:
                result = gate.verify_cas_case(self.output(case, count), b"", case, roster, 0)
                self.assertEqual(result["summary"], [1, 0, 0, 0, count - 1])
                self.assertEqual(result["filtered_out_credit"], "NONE")

    def test_exact_case_rejects_skip_omission_substitution_duplicate_and_stale_count(self):
        roster = gate.cas_compiled_roster(self.compiled(181), b"", 0)
        case = gate.CAS_CASES[0]
        raw = self.output(case, 181)
        bad = (raw.replace(b" ... ok", b" ... ignored, insufficient privilege"),
               raw.replace(case.encode(), b"other::test"), raw.replace(b"running 1 test", b"running 0 tests"),
               raw.replace(b"180 filtered", b"179 filtered"),
               raw + ("test " + case + " ... ok\n").encode(),
               raw + b"test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 180 filtered out;\n",
               raw.replace(b"0 ignored", b"1 ignored"), raw + b"test bogus malformed line\n")
        for changed in bad:
            with self.subTest(changed=changed[-100:]), self.assertRaises(gate.VerificationError):
                gate.verify_cas_case(changed, b"", case, roster, 0)
        for code in (101, False):
            with self.assertRaises(gate.VerificationError):
                gate.verify_cas_case(raw, b"", case, roster, code)

    def test_cas_is_distinct_from_old_platform_cases_and_job(self):
        self.assertEqual([len(gate.CASES[key]) for key in ("windows", "macos")], [3, 2])
        self.assertFalse(set(gate.CAS_CASES) & {name for cases in gate.CASES.values() for name in cases})
        with mock.patch.dict(os.environ, self.env(Path("/synthetic")), clear=True):
            self.assertEqual(gate.run_context(self.HEAD, gate.CAS_SCOPE, 1)["job_id"], "windows-cas-symlink-real-g2")
            os.environ["GITHUB_JOB"] = gate.JOB_NAMES["windows"]
            with self.assertRaisesRegex(gate.VerificationError, "wrong source job"):
                gate.run_context(self.HEAD, gate.CAS_SCOPE, 1)

    def test_proposal_with_all_twelve_pins_pending_is_unadmitted(self):
        path = Path(__file__).resolve().parents[2] / gate.REVIEWED_MANIFEST
        manifest = json.loads(path.read_bytes())
        self.assertEqual(set(manifest["product_pins"]), set(gate.REVIEWED_INPUTS))
        self.assertEqual(len(gate.REVIEWED_INPUTS), 12)
        # Construct a proposal rather than requiring the tracked manifest to stay
        # unadmitted forever after A1 fills and reviews the final source pins.
        manifest["review"] = {"status": "PROPOSAL_REQUIRES_A1", "authority": "A1", "reference": None}
        manifest["baseline_sha"] = manifest["baseline_tree"] = None
        manifest["product_pins"] = {name: None for name in gate.REVIEWED_INPUTS}
        raw = json.dumps(manifest).encode()
        with self.assertRaises(gate.VerificationError):
            gate.reviewed_manifest(raw, gate.digest(raw))

    def test_pinned_cas_definitions_cannot_be_replaced_in_reviewed_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = ReviewedSQLitePathSourceContracts()
            _, original, _, _, _ = fixture.fixture(root)
            for name in gate.CAS_TEST_SOURCES:
                modified = deepcopy(original)
                modified["product_pins"][name] = "f" * 64
                raw = json.dumps(modified).encode()
                with self.subTest(name=name), self.assertRaisesRegex(gate.VerificationError, "CAS test definitions"):
                    gate.reviewed_manifest(raw, gate.digest(raw))

    def test_real_coordinator_observes_list_and_four_cases_once_with_original_streams(self):
        receipt, commands, source_calls, saved = self.exercise(total=181)
        self.assertEqual(receipt["status"], "PASS_EXACT_REAL_WINDOWS_CAS_G2")
        self.assertEqual(commands, [gate.cas_command(), *(gate.cas_command(case) for case in gate.CAS_CASES)])
        self.assertEqual(len(source_calls), 6)
        self.assertEqual([row["case"] for row in receipt["cases_observed"]], list(gate.CAS_CASES))
        self.assertEqual(receipt["compiled_roster"]["compiled_count"], 181)
        self.assertEqual(receipt["executor_OS"], "windows")
        for index, step in enumerate(receipt["observations"]):
            self.assertEqual(gate.digest(saved[Path(step["stdout"]["file"]).as_posix()]), step["stdout"]["sha256"])
            self.assertIn("step-" + str(index) + "/observation.json", saved)
        self.assertEqual(receipt["real_NTFS_integration_tests"], "NOT_RUN")
        self.assertFalse(receipt["human_acceptance"])

    def test_capability_failure_and_unadmitted_source_launch_nothing(self):
        for flag in ("capability_error", "admission_error"):
            receipt, commands, _, _ = self.exercise(**{flag: True})
            self.assertEqual(receipt["status"], "SOURCE_OR_ENVIRONMENT_REJECTED")
            self.assertEqual(commands, [])
            self.assertEqual(receipt["execution"]["state"], "NOT_STARTED")
            self.assertFalse(receipt["real_Rust_SQLite"])

    def test_missing_compiled_case_stops_before_first_execution(self):
        def mutate(index, raw):
            return raw.replace((gate.CAS_CASES[1] + ": test\n").encode(), b"") if index == 0 else raw
        receipt, commands, _, saved = self.exercise(mutate=mutate)
        self.assertEqual(receipt["status"], "FAIL_OR_OMISSION")
        self.assertEqual(commands, [gate.cas_command()])
        self.assertEqual(receipt["cases_observed"], [])
        self.assertIn("step-0/original-rust.stdout.log", saved)

    def test_ignored_exact_case_stops_before_remaining_cases_without_fallback(self):
        def mutate(index, raw):
            return raw.replace(b" ... ok", b" ... ignored, no privilege") if index == 2 else raw
        receipt, commands, _, _ = self.exercise(mutate=mutate)
        self.assertEqual(receipt["status"], "FAIL_OR_OMISSION")
        self.assertEqual(len(commands), 3)
        self.assertEqual(len(receipt["cases_observed"]), 1)
        self.assertFalse(receipt["automatic_retry"])

    def test_unknown_list_or_case_preserves_partial_streams_and_stops_source_readback(self):
        for index in (0, 2):
            receipt, commands, source_calls, saved = self.exercise(unknown_at=index)
            self.assertEqual(receipt["status"], "UNKNOWN")
            self.assertEqual(len(commands), index + 1)
            self.assertEqual(len(source_calls), index + 1)
            self.assertIn("step-" + str(index) + "/original-rust.stdout.log", saved)
            self.assertNotIn("step-" + str(index) + "/source-after.json", saved)
            self.assertFalse(receipt["observations"][-1]["stdout"]["complete_terminal_stream"])

    def test_source_change_after_observation_stops_success_and_subsequent_cases(self):
        receipt, commands, _, _ = self.exercise(source_change_at=3)
        self.assertEqual(receipt["status"], "FAIL_OR_OMISSION")
        self.assertEqual(len(commands), 2)
        self.assertEqual(receipt["cases_observed"], [])
        self.assertIn("source changed", receipt["error"]["message"])

    def test_workflow_has_separate_manual_cas_scope_and_real_capability_failure(self):
        workflow = (Path(__file__).resolve().parents[2] / gate.WORKFLOW).read_text()
        self.assertIn("options: [windows, macos, both, windows-cas]", workflow)
        cas = workflow.split("  windows-cas-symlink-real-g2:\n", 1)[1]
        self.assertIn("inputs.os_scope == 'windows-cas'", cas)
        self.assertNotIn("inputs.os_scope == 'both'", cas)
        self.assertIn("New-Item -ItemType SymbolicLink", cas)
        self.assertIn("throw 'Genuine NTFS fixtures missing'", cas)
        self.assertIn("--platform windows-cas", cas)
        self.assertIn("if ($LASTEXITCODE -ne 0) { throw 'Actual Windows CAS symlink G2 failed' }", cas)
        self.assertIn("path: ${{ env.MW_SQLITE_EVIDENCE }}/", cas)


if __name__ == "__main__":
    unittest.main()
