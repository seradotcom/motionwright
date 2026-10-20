"""Synthetic admission controls; these never build or run the Rust product."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from tooling import ci_bundle_export_authority as gate


def environment(platform="Windows"):
    sha = "a" * 40
    return {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": sha, "GITHUB_WORKFLOW_SHA": sha, "RUNNER_OS": platform,
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "bundle-export-authority", "GITHUB_REPOSITORY": "test/repository",
            "GITHUB_WORKFLOW_REF": "test/repository/" + gate.WORKFLOW + "@refs/heads/fixture"}


class EvidenceContracts(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="be-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.repo, self.target = self.root / "s", self.root / "t"
        self.repo.mkdir()
        self.target.mkdir()
        self.path = self.target / "test-binary"
        self.path.write_bytes(b"synthetic binary identity, never executed")
        self.binary = {"absolute_path": str(self.path), **gate.fingerprint(self.path, self.target)}
        self.context = gate.execution_context(environment(), gate.SOURCE_SHA, "win32")
        self.names = (*gate.CASES["Windows"], "unselected::test")

    def message(self):
        return {"reason": "compiler-artifact", "manifest_path": str(self.repo / "crates/storage/Cargo.toml"),
                "target": {"name": "motionwright_storage", "kind": ["lib"],
                           "src_path": str(self.repo / "crates/storage/src/lib.rs")},
                "profile": {"test": True}, "executable": str(self.path)}

    def build_stream(self, messages=None):
        messages = [self.message()] if messages is None else messages
        return b"\n".join(json.dumps(x).encode() for x in
                          [*messages, {"reason": "build-finished", "success": True}])

    def command(self, name):
        return {"argv": [str(self.path), "--exact", name, "--nocapture", "--test-threads=1"],
                "cwd": str(self.repo), "exit_code": 0, "context": self.context,
                "observation": "DIRECT_PROCESS_EXIT_OBSERVED", "log_custody": "VERIFIED_SNAPSHOT"}

    def output(self, name):
        return (f"running 1 test\ntest {name} ... ok\n\ntest result: ok. 1 passed; 0 failed; "
                f"0 ignored; 0 measured; {len(self.names)-1} filtered out; finished in 0.00s\n").encode()

    def row(self, name=None):
        name = self.names[0] if name is None else name
        return gate.admit_case(name, self.output(name), self.command(name), self.binary,
                               self.binary, self.names, self.context)

    def snapshots(self):
        return {"product": {"sha": gate.SOURCE_SHA, "tree": gate.SOURCE_TREE,
                            "checkout": str(self.repo), "files": {p: {"sha256": h} for p, h in gate.REVIEWED.items()}},
                "gate": {"sha": self.context["GITHUB_SHA"], "files": dict.fromkeys(gate.GATE_FILES, {})}}

    def test_product_and_workflow_sources_are_distinct_and_explicit(self):
        self.assertEqual(self.context["product_source_sha"], gate.SOURCE_SHA)
        self.assertNotEqual(self.context["product_source_sha"], self.context["GITHUB_SHA"])

    def test_foreign_product_or_abbreviated_source_rejected(self):
        for sha in ("b" * 40, gate.SOURCE_SHA[:12]):
            with self.subTest(sha=sha), self.assertRaisesRegex(gate.VerificationError, "unreviewed product"):
                gate.execution_context(environment(), sha, "win32")

    def test_linux_push_and_foreign_os_cannot_claim_this_G2(self):
        for runtime, change in (("linux", {}), ("win32", {"GITHUB_EVENT_NAME": "push"}),
                                ("darwin", {}), ("win32", {"GITHUB_ACTIONS": "false"})):
            with self.subTest(runtime=runtime, change=change), self.assertRaises(gate.VerificationError):
                gate.execution_context(environment() | change, gate.SOURCE_SHA, runtime)

    def test_missing_attempt_or_wrong_workflow_job_rejected(self):
        for change in ({"GITHUB_RUN_ATTEMPT": ""}, {"GITHUB_RUN_ID": "0"},
                       {"GITHUB_WORKFLOW_SHA": "b" * 40}, {"GITHUB_JOB": "other"},
                       {"GITHUB_WORKFLOW_REF": "test/repository/other.yml@refs/heads/fixture"}):
            with self.subTest(change=change), self.assertRaises(gate.VerificationError):
                gate.execution_context(environment() | change, gate.SOURCE_SHA, "win32")

    def test_binary_is_selected_from_its_actual_compiler_record(self):
        self.assertEqual(gate.select_binary(self.build_stream(), 0, self.repo, self.target), self.binary)

    def test_failed_compilation_cannot_admit_a_stale_binary(self):
        with self.assertRaisesRegex(gate.VerificationError, "compilation"):
            gate.select_binary(self.build_stream(), 1, self.repo, self.target)

    def test_missing_duplicate_or_non_test_binary_is_rejected(self):
        altered = self.message()
        altered["profile"]["test"] = False
        for messages in ([], [self.message(), self.message()], [altered]):
            with self.subTest(messages=messages), self.assertRaisesRegex(gate.VerificationError, "ambiguous"):
                gate.select_binary(self.build_stream(messages), 0, self.repo, self.target)

    def test_absent_or_unsuccessful_compile_completion_is_rejected(self):
        for raw in (json.dumps(self.message()).encode(),
                    self.build_stream().replace(b'"success": true', b'"success": false')):
            with self.subTest(raw=raw), self.assertRaisesRegex(gate.VerificationError, "completion"):
                gate.select_binary(raw, 0, self.repo, self.target)

    def test_foreign_manifest_or_source_target_is_rejected(self):
        for key in ("manifest_path", "src_path"):
            message = self.message()
            (message if key == "manifest_path" else message["target"])[key] = str(self.root / "foreign")
            with self.subTest(key=key), self.assertRaisesRegex(gate.VerificationError, "another source"):
                gate.select_binary(self.build_stream([message]), 0, self.repo, self.target)

    def test_binary_outside_owned_target_is_rejected(self):
        message = self.message()
        message["executable"] = str(self.root / "outside")
        with self.assertRaisesRegex(gate.VerificationError, "escaped"):
            gate.select_binary(self.build_stream([message]), 0, self.repo, self.target)

    def test_exact_windows_six_and_macos_five_rosters(self):
        for platform, count in (("Windows", 6), ("macOS", 5)):
            raw = "".join(x + ": test\n" for x in gate.CASES[platform]).encode()
            self.assertEqual(len(gate.selected_roster(raw, 0, platform)), count)

    def test_missing_duplicate_or_extra_bundle_cases_rejected(self):
        for names in (self.names[1:], (*self.names, self.names[0]), (*self.names, gate.PREFIX + "new_case")):
            with self.subTest(names=names), self.assertRaises(gate.VerificationError):
                gate.selected_roster("".join(x + ": test\n" for x in names).encode(), 0, "Windows")

    def test_one_exact_success_is_admitted(self):
        self.assertEqual(self.row()["status"], "PASS")

    def test_zero_ignored_duplicate_or_wrong_case_cannot_pass(self):
        name = self.names[0]
        raw = self.output(name)
        variants = (raw.replace(b"1 passed", b"0 passed"),
                    raw.replace(b" ... ok", b" ... ignored"), raw + raw,
                    raw.replace(name.encode(), b"other::case"),
                    raw.replace(b"6 filtered out", b"7 filtered out"))
        for changed in variants:
            with self.subTest(raw=changed), self.assertRaisesRegex(gate.VerificationError, "omitted"):
                gate.admit_case(name, changed, self.command(name), self.binary, self.binary, self.names, self.context)

    def test_nonzero_exit_and_UNKNOWN_are_not_retried_or_admitted(self):
        name = self.names[0]
        for change in ({"exit_code": 1}, {"exit_code": None, "observation": "UNKNOWN_TIMEOUT_NO_RETRY"}):
            with self.subTest(change=change), self.assertRaisesRegex(gate.VerificationError, "no retry"):
                gate.admit_case(name, self.output(name), self.command(name) | change,
                                self.binary, self.binary, self.names, self.context)

    def test_changed_binary_wrong_command_or_other_attempt_rejected(self):
        name = self.names[0]
        command = self.command(name)
        for altered, after in ((command, self.binary | {"sha256": "f" * 64}),
                               (command | {"argv": [str(self.path), name]}, self.binary),
                               (command | {"context": self.context | {"GITHUB_RUN_ATTEMPT": "2"}}, self.binary)):
            with self.subTest(command=altered), self.assertRaises(gate.VerificationError):
                gate.admit_case(name, self.output(name), altered, self.binary, after, self.names, self.context)

    def test_nul_case_records_preflight_scope(self):
        row = self.row(gate.CASES["Windows"][-1])
        self.assertIn("OS move not invoked", row["evidence_kind"])

    def test_complete_receipt_keeps_fixture_and_formal_limits(self):
        before = self.snapshots()
        result = gate.finish(before, copy.deepcopy(before), self.binary,
                             [self.row(name) for name in gate.CASES["Windows"]], self.names, self.context)
        self.assertEqual(result["passed"], 6)
        for flag in ("fixtures_retained", "formal_acceptance", "human_acceptance", "release_authorized"):
            self.assertFalse(result[flag])

    def test_receipt_cannot_combine_missing_duplicate_or_foreign_cases(self):
        before = self.snapshots()
        rows = [self.row(name) for name in gate.CASES["Windows"]]
        foreign = copy.deepcopy(rows)
        foreign[0]["context"]["GITHUB_RUN_ATTEMPT"] = "2"
        for altered in (rows[:-1], [rows[0], *rows[:-1]], list(reversed(rows)), foreign):
            with self.subTest(rows=altered), self.assertRaises(gate.VerificationError):
                gate.finish(before, before, self.binary, altered, self.names, self.context)

    def test_receipt_rechecks_source_and_recorded_exit(self):
        before, after = self.snapshots(), self.snapshots()
        after["product"]["sha"] = "c" * 40
        rows = [self.row(name) for name in gate.CASES["Windows"]]
        with self.assertRaisesRegex(gate.VerificationError, "source inputs"):
            gate.finish(before, after, self.binary, rows, self.names, self.context)
        rows[0]["command"]["exit_code"] = 1
        with self.assertRaisesRegex(gate.VerificationError, "unsuccessful"):
            gate.finish(before, before, self.binary, rows, self.names, self.context)

    def test_foreign_reviewed_hash_and_missing_log_custody_rejected(self):
        before = self.snapshots()
        before["product"]["files"]["Cargo.lock"]["sha256"] = "0" * 64
        rows = [self.row(name) for name in gate.CASES["Windows"]]
        with self.assertRaisesRegex(gate.VerificationError, "product evidence"):
            gate.finish(before, before, self.binary, rows, self.names, self.context)
        name = self.names[0]
        with self.assertRaisesRegex(gate.VerificationError, "custody incomplete"):
            gate.admit_case(name, self.output(name), self.command(name) | {"log_custody": "INCOMPLETE"},
                            self.binary, self.binary, self.names, self.context)

    def test_timeout_with_unstable_log_keeps_UNKNOWN_and_one_invocation(self):
        output = self.root / "e"
        output.mkdir()
        with mock.patch.object(gate.subprocess, "run", side_effect=subprocess.TimeoutExpired(["fixture"], 1)) as run, \
                mock.patch.object(gate, "fingerprint", side_effect=gate.VerificationError("log changed")):
            command = gate.execute(["fixture"], self.repo, {}, output, "timeout", 1, self.context)
        run.assert_called_once()
        self.assertEqual(command["observation"], "UNKNOWN_TIMEOUT_NO_RETRY")
        self.assertEqual(command["log_custody"], "INCOMPLETE")
        self.assertEqual(json.loads((output / "timeout.command.json").read_text())["observation"],
                         "UNKNOWN_TIMEOUT_NO_RETRY")

    def test_launch_failure_is_not_reported_as_an_executed_unknown_case(self):
        output = self.root / "e"
        output.mkdir()
        with mock.patch.object(gate.subprocess, "run", side_effect=FileNotFoundError("fixture absent")) as run:
            command = gate.execute(["absent"], self.repo, {}, output, "launch", 1, self.context)
        run.assert_called_once()
        self.assertEqual(command["observation"], "NOT_STARTED_LAUNCH_ERROR")

    def test_original_log_replacement_is_detected_before_admission(self):
        output = self.root / "e"
        output.mkdir()
        log = output / "original.log"
        log.write_bytes(b"original bytes")
        command = {"log_custody": "VERIFIED_SNAPSHOT", "stdout": gate.fingerprint(log, output)}
        self.assertEqual(gate.read_original(output, command, "stdout"), b"original bytes")
        log.write_bytes(b"replacement")
        with self.assertRaisesRegex(gate.VerificationError, "changed before"):
            gate.read_original(output, command, "stdout")

    def test_empty_logs_are_valid_but_reparse_paths_are_not(self):
        empty = self.target / "empty.log"
        empty.touch()
        self.assertEqual(gate.fingerprint(empty, self.target)["bytes"], 0)
        original = Path.lstat
        def reparse(path):
            info = original(path)
            return mock.Mock(st_mode=info.st_mode, st_file_attributes=0x400) if path == empty else info
        with mock.patch.object(Path, "lstat", reparse), self.assertRaisesRegex(gate.VerificationError, "linked"):
            gate.fingerprint(empty, self.target)

    def test_receipt_publication_never_overwrites_an_original(self):
        path = self.root / "receipt.json"
        gate.write_new(path, {"attempt": 1})
        with self.assertRaises(FileExistsError):
            gate.write_new(path, {"attempt": 2})
        self.assertEqual(json.loads(path.read_text())["attempt"], 1)


class SourceContracts(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="bg-")
        self.root = Path(self.scratch.name).resolve()
        self.repo = self.root / "s"
        self.repo.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
                        GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.git("init", "--quiet")
        (self.repo / "source.rs").write_text("fn source() {}\n", encoding="utf-8", newline="\n")
        self.git("add", "source.rs")
        self.git("commit", "--quiet", "-m", "Source fixture")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.tree = self.git("rev-parse", "HEAD^{tree}").strip()

    def tearDown(self):
        def retry(function, path, _error):
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            function(path)
        shutil.rmtree(self.root, onerror=retry)
        self.scratch.cleanup()

    def git(self, *args):
        result = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(self.repo), *args],
                                env=self.env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout.decode()

    def test_exact_git_blob_commit_and_tree_are_observed(self):
        observed = gate.source_snapshot(self.repo, self.sha, ("source.rs",), self.tree)
        self.assertEqual(observed["sha"], self.sha)
        self.assertEqual(observed["files"]["source.rs"]["sha256"],
                         hashlib.sha256((self.repo / "source.rs").read_bytes()).hexdigest())

    def test_foreign_head_tree_and_untracked_input_rejected(self):
        with self.assertRaisesRegex(gate.VerificationError, "checkout SHA"):
            gate.source_snapshot(self.repo, "a" * 40, ("source.rs",))
        with self.assertRaisesRegex(gate.VerificationError, "tree"):
            gate.source_snapshot(self.repo, self.sha, ("source.rs",), "a" * 40)
        (self.repo / "unbound").write_bytes(b"untracked input")
        with self.assertRaisesRegex(gate.VerificationError, "unbound"):
            gate.source_snapshot(self.repo, self.sha, ("source.rs",))

    def test_git_clean_CRLF_still_fails_exact_byte_binding(self):
        self.git("config", "--local", "core.autocrlf", "true")
        (self.repo / "source.rs").unlink()
        self.git("-c", "core.autocrlf=true", "checkout-index", "--force", "--index", "--", "source.rs")
        self.assertEqual(gate.git(self.repo, "status", "--porcelain"), b"")
        with self.assertRaisesRegex(gate.VerificationError, "bytes differ"):
            gate.source_snapshot(self.repo, self.sha, ("source.rs",))


if __name__ == "__main__":
    unittest.main()
