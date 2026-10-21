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

from tooling import ci_storage_apply_recovery as gate


def environment(platform="Windows"):
    sha = "a" * 40
    return {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": sha, "GITHUB_WORKFLOW_SHA": sha, "RUNNER_OS": platform,
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "storage-apply-recovery", "GITHUB_REPOSITORY": "test/repository",
            "GITHUB_WORKFLOW_REF": "test/repository/" + gate.WORKFLOW + "@refs/heads/fixture"}


def create_phase_fixture(container, name):
    """Synthetic original-file protocol fixture; never creates a product database."""
    container.mkdir()
    checkpoint = gate.CHECKPOINTS[name]
    directory = container / checkpoint
    directory.mkdir()
    number = list(gate.CHECKPOINTS).index(name) + 1
    request_id = f"process-termination-00000000-0000-7000-8000-{number:012d}"
    case = {"database": str(container / "removed" / "database"),
            "manifest": str(container / "removed" / "manifest"), "blob": str(container / "removed" / "blob"),
            "original": {"id": "synthetic protocol only"}, "request_id": request_id}
    gate.write_new(directory / "case.json", case)
    snapshot = {"project": ["generation", "1", "synthetic title", "{}"], "events": [], "receipts": [],
                "registrations": [], "evictions": [], "manifest": None, "immutable_blob": [0, 255, 32]}
    for phase in gate.PHASES:
        gate.write_new(directory / (phase + ".json"), snapshot)
    for i, mode in enumerate(("writer", "observer", "observer"), 1):
        prefix = f"00000000-0000-7000-8000-{i:012d}"
        marker = ("APPLY_CHECKPOINT:" if mode == "writer" else "APPLY_OBSERVED:") + request_id + ":" + checkpoint
        (directory / (prefix + ".stdout")).write_bytes(("running 1 test\n" + marker + "\n").encode())
        (directory / (prefix + ".stderr")).write_bytes(b"")
        gate.write_new(directory / (prefix + ".process.json"), {
            "test_name": name.split("::", 1)[1], "checkpoint": checkpoint, "request_id": request_id,
            "mode": mode, "exit_code": 86 if mode == "writer" else 0, "exit_status": "synthetic exit",
            "success": mode == "observer", "timed_out": False, "error": None,
            "stdout": prefix + ".stdout", "stderr": prefix + ".stderr"})
    files = []
    for path in sorted(directory.iterdir()):
        data = path.read_bytes()
        files.append({"file": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    gate.write_new(directory / "custody.json", {"schema": "motionwright.apply-phase-custody/1",
        "test_name": name.split("::", 1)[1], "checkpoint": checkpoint, "status": "complete",
        "parent_unwinding": False, "errors": [], "case_identity": "case.json", "physical_power_loss": "NOT_RUN",
        "phases": [{"phase": x, "status": "captured", "reason": None} for x in gate.PHASES], "files": files})
    return directory


def mutate_json(path, update):
    value = json.loads(path.read_bytes())
    update(value)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8", newline="\n")


def reseal_member(directory, name):
    data = (directory / name).read_bytes()
    def update(custody):
        item = next(x for x in custody["files"] if x["file"] == name)
        item.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    mutate_json(directory / "custody.json", update)


class PhaseCustodyContracts(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="ap-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.name = gate.CASES["Windows"][0]
        self.counter = 0

    def fixture(self):
        self.counter += 1
        container = self.root / str(self.counter)
        directory = create_phase_fixture(container, self.name)
        return container, directory

    def test_complete_five_phases_and_three_child_originals_are_admitted(self):
        container, directory = self.fixture()
        result = gate.admit_phase_custody(container, self.name)
        self.assertEqual(result["status"], "COMPLETE_OBSERVED_PHASE_CUSTODY")
        self.assertEqual(len(result["files"]), 15)
        self.assertEqual(len(list(directory.iterdir())), 16)
        self.assertEqual([x["exit_code"] for x in result["processes"]], [86, 0, 0])
        self.assertFalse(result["raw_database_or_full_cas_retained"])

    def test_complete_receipt_does_not_reopen_deleted_case_paths(self):
        container, directory = self.fixture()
        case = json.loads((directory / "case.json").read_bytes())
        for field in ("database", "manifest", "blob"):
            self.assertFalse(Path(case[field]).exists())
        self.assertEqual(gate.admit_phase_custody(container, self.name)["phases"], list(gate.PHASES))

    def test_missing_original_manifest_or_phase_fails_closed(self):
        for member in ("custody.json", "case.json", "interrupted.json"):
            with self.subTest(member=member):
                container, directory = self.fixture()
                (directory / member).unlink()
                with self.assertRaises((OSError, gate.VerificationError)):
                    gate.admit_phase_custody(container, self.name)

    def test_mutated_original_bytes_cannot_match_the_manifest(self):
        container, directory = self.fixture()
        (directory / "before.json").write_bytes(b"{}")
        with self.assertRaisesRegex(gate.VerificationError, "hash or size"):
            gate.admit_phase_custody(container, self.name)

    def test_failed_unwinding_or_wrong_case_manifest_rejected(self):
        for change in ({"status": "incomplete"}, {"parent_unwinding": True}, {"errors": ["unavailable"]},
                       {"case_identity": None}, {"test_name": "other"}, {"checkpoint": "before_commit"}):
            with self.subTest(change=change):
                container, directory = self.fixture()
                mutate_json(directory / "custody.json", lambda x: x.update(change))
                with self.assertRaises(gate.VerificationError):
                    gate.admit_phase_custody(container, self.name)

    def test_duplicate_missing_reordered_or_unavailable_phase_rejected(self):
        for change in (lambda x: x["phases"].pop(), lambda x: x["phases"].append(x["phases"][0]),
                       lambda x: x["phases"].reverse(), lambda x: x["phases"][0].update(status="unavailable"),
                       lambda x: x["phases"][0].update(reason="invented")):
            container, directory = self.fixture()
            mutate_json(directory / "custody.json", change)
            with self.assertRaisesRegex(gate.VerificationError, "phase"):
                gate.admit_phase_custody(container, self.name)

    def test_duplicate_escaping_or_false_digest_entry_rejected(self):
        changes = (lambda x: x["files"].__setitem__(1, x["files"][0]),
                   lambda x: x["files"][0].update(file="../outside"),
                   lambda x: x["files"][0].update(file="C:\\foreign"),
                   lambda x: x["files"][0].update(sha256="0" * 64),
                   lambda x: x["files"][0].update(bytes=True),
                   lambda x: x["files"][0].update(unavailable="missing"))
        for change in changes:
            container, directory = self.fixture()
            mutate_json(directory / "custody.json", change)
            with self.assertRaises(gate.VerificationError):
                gate.admit_phase_custody(container, self.name)

    def test_unlisted_file_or_directory_is_rejected(self):
        for is_dir in (False, True):
            container, directory = self.fixture()
            (directory / "unexpected").mkdir() if is_dir else (directory / "unexpected").write_bytes(b"extra")
            with self.assertRaisesRegex(gate.VerificationError, "unlisted"):
                gate.admit_phase_custody(container, self.name)

    def test_wrong_child_request_checkpoint_or_mode_is_rejected(self):
        for change in ({"request_id": "process-termination-ffffffff-ffff-7fff-8fff-ffffffffffff"},
                       {"checkpoint": "after_commit"}, {"test_name": "other"}, {"mode": "foreign"}):
            container, directory = self.fixture()
            path = sorted(directory.glob("*.process.json"))[0]
            mutate_json(path, lambda x: x.update(change))
            reseal_member(directory, path.name)
            with self.assertRaisesRegex(gate.VerificationError, "child case"):
                gate.admit_phase_custody(container, self.name)

    def test_child_exit_type_timeout_and_success_are_exact(self):
        changes = ({"exit_code": 0}, {"exit_code": True}, {"exit_code": None}, {"success": True},
                   {"success": 0}, {"timed_out": True}, {"timed_out": 0}, {"error": "wait failed"})
        for change in changes:
            container, directory = self.fixture()
            path = sorted(directory.glob("*.process.json"))[0]
            mutate_json(path, lambda x: x.update(change))
            reseal_member(directory, path.name)
            with self.assertRaisesRegex(gate.VerificationError, "child exit"):
                gate.admit_phase_custody(container, self.name)

    def test_observer_false_zero_or_writer_success_is_rejected(self):
        container, directory = self.fixture()
        path = sorted(directory.glob("*.process.json"))[1]
        mutate_json(path, lambda x: x.update(exit_code=False))
        reseal_member(directory, path.name)
        with self.assertRaisesRegex(gate.VerificationError, "child exit"):
            gate.admit_phase_custody(container, self.name)

    def test_child_empty_stderr_is_retained_and_nonempty_is_rejected(self):
        container, directory = self.fixture()
        path = sorted(directory.glob("*.stderr"))[0]
        self.assertEqual(path.read_bytes(), b"")
        path.write_bytes(b"actual failure")
        reseal_member(directory, path.name)
        with self.assertRaisesRegex(gate.VerificationError, "stderr"):
            gate.admit_phase_custody(container, self.name)

    def test_matching_marker_must_be_present_once_in_original_stdout(self):
        for transform in (lambda x: b"", lambda x: x + x,
                          lambda x: x.replace(b"APPLY_CHECKPOINT:", b"APPLY_OBSERVED:"),
                          lambda x: x.replace(b"after_project_update", b"before_commit")):
            container, directory = self.fixture()
            path = sorted(directory.glob("*.stdout"))[0]
            path.write_bytes(transform(path.read_bytes()))
            reseal_member(directory, path.name)
            with self.assertRaisesRegex(gate.VerificationError, "marker"):
                gate.admit_phase_custody(container, self.name)

    def test_libtest_prefix_and_crlf_preserve_original_marker_bytes(self):
        container, directory = self.fixture()
        for path in directory.glob("*.stdout"):
            raw = path.read_bytes().replace(b"\nAPPLY_", ("\ntest " + self.name + " ... APPLY_").encode()).replace(b"\n", b"\r\n")
            path.write_bytes(raw)
            reseal_member(directory, path.name)
        self.assertEqual(len(gate.admit_phase_custody(container, self.name)["processes"]), 3)

    def test_duplicate_json_keys_and_nonfinite_numbers_fail_closed(self):
        for raw in (b'{"status":"incomplete","status":"complete"}', b'{"value":NaN}'):
            with self.assertRaises(gate.VerificationError):
                gate.strict_json(raw)

    def test_byte_arrays_are_exact_and_manifest_may_be_null(self):
        for change in ({"manifest": [True]}, {"immutable_blob": [256]}, {"immutable_blob": []},
                       {"manifest": "decoded bytes"}, {"project": ["only one"]}, {"events": [[False]]}):
            container, directory = self.fixture()
            mutate_json(directory / "before.json", lambda x: x.update(change))
            reseal_member(directory, "before.json")
            with self.assertRaises(gate.VerificationError):
                gate.admit_phase_custody(container, self.name)

    def test_sql_row_widths_match_the_frozen_snapshot_queries(self):
        for field, width in (("events", 4), ("receipts", 4), ("registrations", 6), ("evictions", 6)):
            with self.subTest(field=field, width=width):
                container, directory = self.fixture()
                mutate_json(directory / "before.json", lambda x: x.update({field: [["value"] * width]}))
                reseal_member(directory, "before.json")
                self.assertEqual(gate.admit_phase_custody(container, self.name)["status"],
                                 "COMPLETE_OBSERVED_PHASE_CUSTODY")
            for invalid_width in (0, width - 1, width + 1):
                with self.subTest(field=field, invalid_width=invalid_width):
                    container, directory = self.fixture()
                    mutate_json(directory / "before.json",
                                lambda x: x.update({field: [["value"] * invalid_width]}))
                    reseal_member(directory, "before.json")
                    with self.assertRaisesRegex(gate.VerificationError, "phase SQL"):
                        gate.admit_phase_custody(container, self.name)

    def test_foreign_child_stream_binding_is_rejected(self):
        container, directory = self.fixture()
        path = sorted(directory.glob("*.process.json"))[0]
        mutate_json(path, lambda x: x.update(stdout="before.json"))
        reseal_member(directory, path.name)
        with self.assertRaisesRegex(gate.VerificationError, "child streams"):
            gate.admit_phase_custody(container, self.name)


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
        row = gate.admit_case(name, self.output(name), self.command(name), self.binary,
                              self.binary, self.names, self.context)
        self.counter = getattr(self, "counter", 0) + 1
        container = self.root / ("phases-" + str(self.counter))
        create_phase_fixture(container, name)
        row["phase_custody"] = gate.admit_phase_custody(container, name)
        return row

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

    def test_exact_four_cases_on_each_selected_platform(self):
        for platform, count in (("Windows", 4), ("macOS", 4)):
            raw = "".join(x + ": test\n" for x in gate.CASES[platform]).encode()
            self.assertEqual(len(gate.selected_roster(raw, 0, platform)), count)

    def test_missing_duplicate_or_extra_apply_cases_rejected(self):
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
                    raw.replace(b"4 filtered out", b"5 filtered out"))
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

    def test_process_recovery_scope_does_not_claim_physical_database_retention(self):
        row = self.row()
        self.assertIn("process termination", row["evidence_kind"])
        self.assertFalse(row["phase_custody"]["raw_database_or_full_cas_retained"])

    def test_complete_receipt_keeps_fixture_and_formal_limits(self):
        before = self.snapshots()
        result = gate.finish(before, copy.deepcopy(before), self.binary,
                             [self.row(name) for name in gate.CASES["Windows"]], self.names, self.context)
        self.assertEqual(result["passed"], 4)
        for flag in ("raw_sqlite_snapshots_retained", "full_cas_archive_retained", "formal_acceptance", "human_acceptance", "release_authorized"):
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

    def test_parent_pass_alone_cannot_hide_missing_or_foreign_phase_custody(self):
        before = self.snapshots()
        for change in ({"status": "incomplete"}, {"test_name": "foreign"}, {"checkpoint": "foreign"}):
            rows = [self.row(name) for name in gate.CASES["Windows"]]
            rows[0]["phase_custody"].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(gate.VerificationError, "unsuccessful"):
                gate.finish(before, before, self.binary, rows, self.names, self.context)

    def test_phase_request_cannot_be_reused_by_another_case(self):
        before = self.snapshots()
        rows = [self.row(name) for name in gate.CASES["Windows"]]
        rows[1]["phase_custody"]["request_id"] = rows[0]["phase_custody"]["request_id"]
        with self.assertRaisesRegex(gate.VerificationError, "request reused"):
            gate.finish(before, before, self.binary, rows, self.names, self.context)

    def test_inherited_child_or_evidence_mode_is_rejected_before_build(self):
        for key in ("MOTIONWRIGHT_TEST_APPLY_CHILD", "MOTIONWRIGHT_TEST_APPLY_CASE",
                    "MOTIONWRIGHT_TEST_APPLY_CHECKPOINT", "MOTIONWRIGHT_TEST_APPLY_REQUEST",
                    "MOTIONWRIGHT_TEST_APPLY_SNAPSHOT", "MOTIONWRIGHT_TEST_APPLY_EVIDENCE_ROOT",
                    "RUSTC_WRAPPER", "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_RUSTFLAGS"):
            with self.subTest(key=key), self.assertRaisesRegex(gate.VerificationError, "inherited"):
                gate.isolated_build_environment({key: "foreign"})
        clean = gate.isolated_build_environment({"CARGO_HOME": "/isolated/cache"})
        self.assertNotIn(gate.EVIDENCE_ROOT, clean)
        self.assertEqual(clean["CARGO_INCREMENTAL"], "0")

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
