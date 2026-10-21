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


class SyntheticObservedProcess:
    """Only a fixture for the canonical observer, never an executed test binary."""
    def __init__(self, code=0, error=None):
        self.pid = 123456789
        self.code, self.error, self.returncode = code, error, None

    def wait(self, timeout=None):
        if self.error is not None:
            raise self.error
        self.returncode = self.code
        return self.returncode

    def poll(self):
        return self.returncode


def synthetic_spawn_from_run(callback):
    def spawn(argv, **kwargs):
        try:
            result = callback(argv, **kwargs)
        except subprocess.TimeoutExpired as error:
            return SyntheticObservedProcess(error=error)
        return SyntheticObservedProcess(result.returncode)
    return spawn


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
        with mock.patch.object(gate.subprocess, "Popen", return_value=SyntheticObservedProcess(
                    error=subprocess.TimeoutExpired(["fixture"], 1))) as run, \
                mock.patch.object(gate, "stop_observed_process", return_value=[]), \
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
        with mock.patch.object(gate.subprocess, "Popen", side_effect=FileNotFoundError("fixture absent")) as run:
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



class Binding4Contracts(unittest.TestCase):
    """Synthetic protocol controls; no Rust executable or migration is run here."""

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="b4-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.repo, self.base, self.target = self.root / "s", self.root / "b", self.root / "t"
        for path in (self.repo, self.base, self.target):
            path.mkdir()
        env = environment("Linux") | {"GITHUB_JOB": "storage-apply-binding-core"}
        self.context = gate.binding4_context(env, gate.BINDING4_SOURCE_SHA, "binding4-core44", "linux")
        self.product_context = gate.binding4_command_context(self.context, "product")
        self.base_context = gate.binding4_command_context(self.context, "baseline")
        self.binaries = {}
        for name in ("motionwright_storage", *gate.BINDING4_INTEGRATIONS):
            path = self.target / name
            path.write_bytes(("synthetic identity only: " + name).encode())
            self.binaries[name] = {"absolute_path": str(path), **gate.fingerprint(path, self.target)}
        path = self.target / "baseline-only"
        path.write_bytes(b"synthetic baseline identity; never executed")
        self.base_binary = {"absolute_path": str(path), **gate.fingerprint(path, self.target)}
        self.lib_names = (*gate.BINDING4_UNIT_CASES, *gate.BINDING4_PHASE_CASES,
                          gate.BINDING4_DDL_CASE, "unselected::test")
        self.rosters = {"motionwright_storage": self.lib_names, **gate.BINDING4_INTEGRATIONS}

    def raw(self, group, enumerated=None):
        baseline = group == "baseline"
        binary = self.base_binary if baseline else next(iter(self.binaries.values()))
        target, wanted, _argv, _timeout = gate.binding4_group(group, binary)
        if enumerated is None:
            enumerated = (gate.BINDING4_BASELINE_CASE,) if baseline else self.rosters[target]
        state = "FAILED" if baseline else "ok"
        output = f"running {len(wanted)} tests\n" + "".join(f"test {name} ... {state}\n" for name in wanted)
        if baseline:
            output += ("thread '" + wanted[0] + "' panicked at crates/storage/tests/apply_receipt_binding.rs:90:5:\n"
                       "accepted corrupt known result: Ok(ApplyOutcome)\n")
        counts = (0, 1, 0, 0, 0) if baseline else (len(wanted), 0, 0, 0, len(enumerated) - len(wanted))
        output += (f"\ntest result: {state}. {counts[0]} passed; {counts[1]} failed; {counts[2]} ignored; "
                   f"{counts[3]} measured; {counts[4]} filtered out; finished in 0.00s\n")
        return output.encode()

    def admission(self, group):
        target, _wanted, _argv, _timeout = gate.binding4_group(group, self.base_binary)
        baseline = group == "baseline"
        binary = self.base_binary if baseline else self.binaries[target]
        context = self.base_context if baseline else self.product_context
        _target, _wanted, argv, _timeout = gate.binding4_group(group, binary)
        names = (gate.BINDING4_BASELINE_CASE,) if baseline else self.rosters[target]
        command = {"argv": argv, "cwd": str(self.base if baseline else self.repo),
                   "exit_code": 101 if baseline else 0, "context": context,
                   "observation": "DIRECT_PROCESS_EXIT_OBSERVED", "log_custody": "VERIFIED_SNAPSHOT"}
        return self.raw(group), command, binary, binary, names, context

    def row(self, group):
        row = gate.admit_binding4_group(group, *self.admission(group))
        if group.startswith("phase:"):
            container = self.root / ("phases-" + str(len(list(self.root.glob("phases-*")))))
            directory = create_phase_fixture(container, group[6:])
            for phase in gate.PHASES:
                mutate_json(directory / (phase + ".json"),
                            lambda x: x.update(receipts=[["request", "digest", "json", "created", "1", "binding"]]))
                reseal_member(directory, phase + ".json")
            row["phase_custody"] = gate.admit_phase_custody(container, group[6:], "binding4")
        return row

    def snapshot(self):
        return {"product": {"sha": gate.BINDING4_SOURCE_SHA, "tree": gate.BINDING4_SOURCE_TREE,
                            "checkout": str(self.repo), "files": {p: {"sha256": h} for p, h in gate.BINDING4_REVIEWED.items()}},
                "baseline": {"sha": gate.BASELINE_SOURCE_SHA, "tree": gate.BASELINE_SOURCE_TREE,
                             "checkout": str(self.base), "files": {p: {"sha256": h} for p, h in gate.BASELINE_REVIEWED.items()}},
                "gate": {"sha": self.context["GITHUB_SHA"], "files": dict.fromkeys(gate.GATE_FILES, {})}}

    def compiler_record(self, name, path=None, root=None):
        root = self.repo if root is None else root
        is_lib = name == "motionwright_storage"
        return {"reason": "compiler-artifact", "manifest_path": str(root / "crates/storage/Cargo.toml"),
                "target": {"name": name, "kind": ["lib"] if is_lib else ["test"],
                           "src_path": str(root / ("crates/storage/src/lib.rs" if is_lib
                                                 else "crates/storage/tests/" + name + ".rs"))},
                "profile": {"test": True},
                "executable": str(path) if path is not None else self.binaries[name]["absolute_path"]}

    def compiler_output(self, messages):
        return b"\n".join(json.dumps(row).encode() for row in
                          [*messages, {"reason": "build-finished", "success": True}])

    def test_mode_source_platform_selection_is_closed(self):
        self.assertEqual(gate.select_mode("binding4-core44", gate.BINDING4_SOURCE_SHA, "linux")["matrix"],
                         {"include": []})
        self.assertEqual(len(gate.select_mode("binding4-process", gate.BINDING4_SOURCE_SHA, "both")
                             ["matrix"]["include"]), 2)
        self.assertEqual(gate.select_mode("source", gate.SOURCE_SHA, "both")["tests_executed"], 0)
        for mode, source, platforms in (("other", gate.SOURCE_SHA, "both"),
                                       ("source", gate.BINDING4_SOURCE_SHA, "both"),
                                       ("historical5fa", gate.BINDING4_SOURCE_SHA, "windows"),
                                       ("historical5fa", gate.SOURCE_SHA, "linux"),
                                       ("binding4-core44", gate.SOURCE_SHA, "linux"),
                                       ("binding4-core44", gate.BINDING4_SOURCE_SHA[:12], "linux"),
                                       ("binding4-core44", gate.BINDING4_SOURCE_SHA, "both"),
                                       ("binding4-process", gate.BINDING4_SOURCE_SHA, "linux")):
            with self.subTest(mode=mode, platforms=platforms), self.assertRaises(gate.VerificationError):
                gate.select_mode(mode, source, platforms)

    def test_binding4_context_requires_dispatch_matching_os_and_logical_job(self):
        env = environment("Linux") | {"GITHUB_JOB": "storage-apply-binding-core"}
        for change in ({"GITHUB_EVENT_NAME": "push"}, {"RUNNER_OS": "Windows"},
                       {"GITHUB_JOB": "storage-apply-recovery"}, {"GITHUB_RUN_ATTEMPT": ""},
                       {"GITHUB_WORKFLOW_SHA": "f" * 40}):
            with self.subTest(change=change), self.assertRaises(gate.VerificationError):
                gate.binding4_context(env | change, gate.BINDING4_SOURCE_SHA, "binding4-core44", "linux")
        physical = gate.binding4_context(environment(), gate.BINDING4_SOURCE_SHA, "binding4-process", "win32")
        self.assertIsNone(physical["baseline_source_sha"])
        with self.assertRaises(gate.VerificationError):
            gate.binding4_command_context(physical, "baseline")

    def test_explicit_binding4_requires_six_strings_and_legacy_stays_four(self):
        sample = {"project": ["", "", "", ""], "events": [], "receipts": [[""] * 4],
                  "registrations": [], "evictions": [], "manifest": None, "immutable_blob": [0]}
        gate.phase_snapshot_shape(sample)
        with self.assertRaises(gate.VerificationError):
            gate.phase_snapshot_shape(sample, "binding4")
        sample["receipts"] = [[""] * 6]
        gate.phase_snapshot_shape(sample, "binding4")
        with self.assertRaises(gate.VerificationError):
            gate.phase_snapshot_shape(sample)
        for row in ([""] * 4, [""] * 5, [""] * 7, [""] * 5 + [None], [""] * 5 + [1], [""] * 5 + [False]):
            with self.subTest(row=row), self.assertRaises(gate.VerificationError):
                gate.phase_snapshot_shape(sample | {"receipts": [row]}, "binding4")
        with self.assertRaises(gate.VerificationError):
            gate.phase_snapshot_shape(sample | {"receipts": []}, "unknown")

    def test_binding4_phase_custody_keeps_closed_manifest_and_original_hashes(self):
        name = gate.BINDING4_PHASE_CASES[0]
        row = self.row("phase:" + name)
        self.assertEqual(len(row["phase_custody"]["files"]), 15)
        directory = self.root / "phases-0" / gate.CHECKPOINTS[name]
        (directory / "extra").write_bytes(b"unlisted")
        with self.assertRaisesRegex(gate.VerificationError, "unlisted"):
            gate.admit_phase_custody(directory.parent, name, "binding4")

    def test_reviewed_source_wrapper_requires_all_current_and_baseline_hashes(self):
        for role, key in (("product", "product"), ("baseline", "baseline")):
            expected = self.snapshot()[key]
            with mock.patch.object(gate, "source_snapshot", return_value=expected) as observe:
                self.assertEqual(gate.binding4_source_snapshot(self.repo, role), expected)
                self.assertEqual(observe.call_args.args[1], expected["sha"])
            changed = copy.deepcopy(expected)
            changed["files"]["Cargo.lock"]["sha256"] = "0" * 64
            with mock.patch.object(gate, "source_snapshot", return_value=changed), self.assertRaises(gate.VerificationError):
                gate.binding4_source_snapshot(self.repo, role)

    def test_four_compiler_targets_are_unique_real_records_inside_owned_target(self):
        messages = [self.compiler_record(name) for name in self.binaries]
        self.assertEqual(gate.select_binding4_binaries(self.compiler_output(messages), 0,
                         self.repo, self.target, "binding4-core44"), self.binaries)
        variants = [messages[:-1], [*messages, messages[0]]]
        for field, value in (("manifest_path", str(self.root / "foreign")), ("executable", str(self.root / "outside"))):
            changed = copy.deepcopy(messages)
            changed[-1][field] = value
            variants.append(changed)
        changed = copy.deepcopy(messages)
        changed[-1]["profile"]["test"] = False
        variants.append(changed)
        changed = copy.deepcopy(messages)
        changed[-1]["target"]["src_path"] = str(self.root / "foreign.rs")
        variants.append(changed)
        for variant in variants:
            with self.subTest(messages=variant), self.assertRaises((OSError, gate.VerificationError)):
                gate.select_binding4_binaries(self.compiler_output(variant), 0,
                                             self.repo, self.target, "binding4-core44")
        with self.assertRaises(gate.VerificationError):
            gate.select_binding4_binaries(self.compiler_output(messages), 101,
                                         self.repo, self.target, "binding4-core44")

    def test_core44_and_physical5_have_closed_actual_rosters(self):
        for target, names in self.rosters.items():
            raw = "".join(name + ": test\n" for name in names).encode()
            self.assertEqual(gate.binding4_roster(raw, 0, target), tuple(names))
            for changed in (names[1:], (*names, names[0]), (*names, (
                    "apply_crash_tests::unreviewed" if target == "motionwright_storage" else "unreviewed"))):
                with self.subTest(target=target, names=changed), self.assertRaises(gate.VerificationError):
                    gate.binding4_roster("".join(name + ": test\n" for name in changed).encode(), 0, target)
        self.assertEqual(sum(len(gate.binding4_group(x, self.base_binary)[1])
                             for x in gate.BINDING4_CORE_GROUPS), 44)
        self.assertEqual(sum(len(gate.binding4_group(x, self.base_binary)[1])
                             for x in gate.BINDING4_PROCESS_GROUPS), 5)

    def test_current_group_requires_every_result_once_and_exact_filtered_count(self):
        for group in gate.BINDING4_CORE_GROUPS:
            args = self.admission(group)
            self.assertEqual(gate.admit_binding4_group(group, *args)["status"], "PASS")
            filtered = len(args[4]) - len(gate.binding4_group(group, args[2])[1])
            for raw in (args[0] + args[0], args[0].replace(b" ... ok", b" ... ignored", 1),
                        args[0].replace(b"0 ignored", b"1 ignored"),
                        args[0].replace(f"{filtered} filtered out".encode(), f"{filtered + 1} filtered out".encode())):
                with self.subTest(group=group), self.assertRaises(gate.VerificationError):
                    gate.admit_binding4_group(group, raw, *args[1:])

    def test_original_baseline_failure_is_admitted_as_a_failure_not_green(self):
        row = self.row("baseline")
        self.assertEqual(row["status"], "EXPECTED_CAUSAL_FAILURE_OBSERVED")
        self.assertEqual(row["command"]["exit_code"], 101)
        self.assertEqual(row["cases"], [gate.BINDING4_BASELINE_CASE])

    def test_baseline_does_not_accept_compile_failure_wrong_cause_or_unknown(self):
        raw, command, binary, after, names, context = self.admission("baseline")
        for changed in (raw.replace(b"accepted corrupt known result: Ok(", b"different assertion: Err("),
                        raw.replace(b".rs:90:", b".rs:89:"), raw + raw,
                        raw.replace(b"1 failed", b"0 failed"), raw.replace(b" ... FAILED", b" ... ignored")):
            with self.subTest(raw=changed), self.assertRaises(gate.VerificationError):
                gate.admit_binding4_group("baseline", changed, command, binary, after, names, context)
        for change in ({"exit_code": 0}, {"exit_code": 1}, {"exit_code": 86},
                       {"exit_code": None, "observation": "UNKNOWN_TIMEOUT_NO_RETRY"},
                       {"argv": ["cargo", "test", "--no-run"]}, {"log_custody": "INCOMPLETE"}):
            with self.subTest(change=change), self.assertRaises(gate.VerificationError):
                gate.admit_binding4_group("baseline", raw, command | change, binary, after, names, context)

    def test_ddl_parent_has_normal_capture_and_no_retained_child_claim(self):
        args = self.admission("ddl")
        row = gate.admit_binding4_group("ddl", *args)
        self.assertNotIn("--nocapture", row["command"]["argv"])
        self.assertEqual(row["retained_child_originals"], 0)
        self.assertEqual(row["retained_phase_jsons"], 0)
        with self.assertRaises(gate.VerificationError):
            gate.admit_binding4_group("ddl", args[0],
                args[1] | {"argv": [*args[1]["argv"], "--nocapture"]}, *args[2:])

    def test_core44_final_receipt_keeps_four_phases_and_one_separate_ddl(self):
        before = self.snapshot()
        rows = [self.row(group) for group in gate.BINDING4_CORE_GROUPS]
        baseline = self.row("baseline")
        result = gate.finish_binding4(before, copy.deepcopy(before), self.binaries, rows,
                                      self.rosters, self.context, baseline, self.base_binary)
        self.assertEqual((result["passed"], result["phase_jsons_retained"],
                          result["child_process_originals_retained"], result["ddl_retained_child_originals"]),
                         (44, 20, 12, 0))
        for flag in ("sql_null_distinction_proven_by_phase_strings", "raw_sqlite_snapshots_retained",
                     "full_cas_archive_retained", "formal_acceptance", "human_acceptance", "release_authorized"):
            self.assertFalse(result[flag])

    def test_final_receipt_rejects_missing_foreign_or_fabricated_ddl_and_baseline(self):
        before = self.snapshot()
        rows = [self.row(group) for group in gate.BINDING4_CORE_GROUPS]
        baseline = self.row("baseline")
        variants = [rows[:-1], [rows[0], *rows[:-1]]]
        for change in ({"retained_child_originals": 2}, {"phase_custody": {"status": "complete"}}):
            changed = copy.deepcopy(rows)
            changed[-1].update(change)
            variants.append(changed)
        changed = copy.deepcopy(rows)
        changed[0]["command"]["context"]["GITHUB_RUN_ATTEMPT"] = "2"
        variants.append(changed)
        for changed in variants:
            with self.subTest(rows=changed), self.assertRaises(gate.VerificationError):
                gate.finish_binding4(before, before, self.binaries, changed,
                                     self.rosters, self.context, baseline, self.base_binary)
        for changed in (None, baseline | {"status": "PASS"}, baseline | {"binary_sha256": "f" * 64}):
            with self.subTest(baseline=changed), self.assertRaises(gate.VerificationError):
                gate.finish_binding4(before, before, self.binaries, rows,
                                     self.rosters, self.context, changed, self.base_binary)


    def test_process5_final_receipt_has_no_baseline_or_extra_binary(self):
        self.context = gate.binding4_context(environment(), gate.BINDING4_SOURCE_SHA, "binding4-process", "win32")
        self.product_context = gate.binding4_command_context(self.context, "product")
        before = self.snapshot()
        before.pop("baseline")
        rows = [self.row(group) for group in gate.BINDING4_PROCESS_GROUPS]
        binaries = {"motionwright_storage": self.binaries["motionwright_storage"]}
        rosters = {"motionwright_storage": self.lib_names}
        result = gate.finish_binding4(before, before, binaries, rows, rosters, self.context, None)
        self.assertEqual(result["passed"], 5)
        self.assertFalse(result["baseline_expected_failure_observed"])
        self.assertEqual(result["child_process_originals_retained"], 12)
        with self.assertRaises(gate.VerificationError):
            gate.finish_binding4(before, before, self.binaries, rows, self.rosters, self.context, None)

    def test_selected_baseline_timeout_is_unknown_and_no_current_build_is_dispatched(self):
        work = self.root / "runner"
        work.mkdir()
        g, s, b = work / "g", work / "s", work / "b"
        for path in (g, s, b):
            path.mkdir()
        output = work / "v" / "e"
        snapshots = {"product": {"checkout": str(s), "files": {}},
                     "baseline": {"checkout": str(b), "files": {}}}
        env = environment("Linux") | {"GITHUB_JOB": "storage-apply-binding-core",
                                     "GITHUB_WORKSPACE": str(work), "CARGO_TARGET_DIR": str(work / "v" / "t")}
        calls = []
        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[:2] in (["rustc", "-Vv"], ["cargo", "-V"]):
                return subprocess.CompletedProcess(argv, 0)
            if argv[0] == "cargo":
                path = work / "v" / "bt" / "baseline-test"
                path.write_bytes(b"synthetic binary, subprocess execution is mocked")
                record = self.compiler_record("apply_receipt_binding", path, b)
                kwargs["stdout"].write(self.compiler_output([record]))
                return subprocess.CompletedProcess(argv, 0)
            if "--list" in argv:
                kwargs["stdout"].write((gate.BINDING4_BASELINE_CASE + ": test\n").encode())
                return subprocess.CompletedProcess(argv, 0)
            raise subprocess.TimeoutExpired(argv, 120)
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(gate.sys, "platform", "linux"), \
                mock.patch.object(gate, "__file__", str(g / "tooling" / "ci_storage_apply_recovery.py")), \
                mock.patch.object(gate, "binding4_source_snapshot", side_effect=lambda _p, role: snapshots[role]), \
                mock.patch.object(gate, "source_snapshot", return_value={"files": {}}), \
                mock.patch.object(gate.subprocess, "Popen", side_effect=synthetic_spawn_from_run(fake_run)) as process, \
                mock.patch.object(gate, "owned_process_group_state", return_value="ABSENT"), \
                mock.patch.object(gate, "stop_observed_process", return_value=[]):
            self.assertEqual(gate.run_binding4(s, output, gate.BINDING4_SOURCE_SHA, "binding4-core44", b), 1)
        result = json.loads((output / "receipt.json").read_bytes())
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(process.call_count, 5)
        self.assertEqual(sum(x[0] == "cargo" and "--no-run" in x for x in calls), 1)
        self.assertEqual(result["results"], [])
        self.assertFalse(result["operations_retried"])
        self.assertEqual(result["commands"][-1]["observation"], "UNKNOWN_TIMEOUT_NO_RETRY")
        self.assertFalse((output / "compile.command.json").exists())


class CurrentProductContracts(unittest.TestCase):
    """Current-source admission controls; never execute a Rust product or native UI."""

    def setUp(self):
        self.fixture = Binding4Contracts("test_mode_source_platform_selection_is_closed")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.context = gate.binding4_context(
            environment(), gate.CURRENT_SOURCE_SHA, "current-process", "win32")
        self.fixture.product_context = gate.binding4_command_context(self.fixture.context, "product")

    def snapshot(self):
        result = self.fixture.snapshot()
        result.pop("baseline")
        result["product"].update(
            sha=gate.CURRENT_SOURCE_SHA, tree=gate.CURRENT_SOURCE_TREE,
            files={path: {"sha256": digest} for path, digest in gate.CURRENT_REVIEWED.items()})
        return result

    def finish(self, before, rows, context=None):
        fixture = self.fixture
        binaries = {"motionwright_storage": fixture.binaries["motionwright_storage"]}
        rosters = {"motionwright_storage": fixture.lib_names}
        return gate.finish_binding4(
            before, copy.deepcopy(before), binaries, rows, rosters,
            fixture.context if context is None else context, None)

    def test_current_identity_is_additive_and_historical_profiles_are_unchanged(self):
        self.assertEqual(gate.SOURCE_SHA, "5faedc090aa45b33833b229050c09bf424de938f")
        self.assertEqual(gate.BINDING4_SOURCE_SHA, "449de7a315a513468610d517e78f9ab87f76ae9d")
        self.assertEqual(gate.CURRENT_SOURCE_SHA, "be7d0377425f9eec201852450b5a7c53e4a1f403")
        self.assertEqual(gate.CURRENT_SOURCE_TREE, "5f9e92f6640a39c7adddb249501399cc97e958f8")
        self.assertEqual(gate.binding4_product_profile("binding4-process"),
                         (gate.BINDING4_SOURCE_SHA, gate.BINDING4_SOURCE_TREE, gate.BINDING4_REVIEWED))
        self.assertEqual(gate.binding4_product_profile("binding4-core44"),
                         gate.binding4_product_profile("binding4-process"))
        self.assertEqual(set(gate.CURRENT_REVIEWED), set(gate.BINDING4_REVIEWED))
        self.assertNotEqual(gate.CURRENT_REVIEWED["Cargo.lock"], gate.BINDING4_REVIEWED["Cargo.lock"])
        with self.assertRaises(TypeError):
            gate.CURRENT_REVIEWED["Cargo.lock"] = "0" * 64

    def test_current_source_selection_cannot_claim_or_select_execution(self):
        selection = gate.select_mode("current-source", gate.CURRENT_SOURCE_SHA, "both")
        self.assertEqual(selection["matrix"], {"include": []})
        self.assertEqual(selection["tests_executed"], 0)
        self.assertFalse(selection["formal_acceptance"])
        for source, platforms in ((gate.BINDING4_SOURCE_SHA, "both"), (gate.CURRENT_SOURCE_SHA, "windows"),
                                  (gate.CURRENT_SOURCE_SHA, "linux"), (gate.CURRENT_SOURCE_SHA[:12], "both")):
            with self.subTest(source=source, platforms=platforms), self.assertRaises(gate.VerificationError):
                gate.select_mode("current-source", source, platforms)

    def test_current_process_selection_is_only_exact_windows_and_macos_five(self):
        for platforms, expected in (("windows", ["windows"]), ("macos", ["macos"]),
                                    ("both", ["windows", "macos"])):
            selected = gate.select_mode("current-process", gate.CURRENT_SOURCE_SHA, platforms)
            self.assertEqual([row["platform"] for row in selected["matrix"]["include"]], expected)
            self.assertEqual(selected["tests_executed"], 0)
        for mode, source, platform in (
                ("current-process", gate.SOURCE_SHA, "windows"),
                ("current-process", gate.BINDING4_SOURCE_SHA, "macos"),
                ("current-process", gate.CURRENT_SOURCE_SHA[:12], "windows"),
                ("current-process", gate.CURRENT_SOURCE_SHA, "linux"),
                ("binding4-process", gate.CURRENT_SOURCE_SHA, "both"),
                ("binding4-core44", gate.CURRENT_SOURCE_SHA, "linux"),
                ("current-core44", gate.CURRENT_SOURCE_SHA, "linux")):
            with self.subTest(mode=mode, source=source, platform=platform), self.assertRaises(gate.VerificationError):
                gate.select_mode(mode, source, platform)
        self.assertEqual(sum(len(gate.binding4_group(group, self.fixture.base_binary)[1])
                             for group in gate.BINDING4_PROCESS_GROUPS), 5)

    def test_current_context_requires_explicit_dispatch_and_matching_job(self):
        for change in ({"GITHUB_EVENT_NAME": "push"}, {"RUNNER_OS": "macOS"},
                       {"GITHUB_JOB": "storage-apply-binding-core"}, {"GITHUB_RUN_ATTEMPT": ""},
                       {"GITHUB_WORKFLOW_SHA": "f" * 40}):
            with self.subTest(change=change), self.assertRaises(gate.VerificationError):
                gate.binding4_context(environment() | change, gate.CURRENT_SOURCE_SHA,
                                      "current-process", "win32")
        context = gate.binding4_context(environment("macOS"), gate.CURRENT_SOURCE_SHA,
                                        "current-process", "darwin")
        self.assertEqual(context["product_source_sha"], gate.CURRENT_SOURCE_SHA)
        self.assertIsNone(context["baseline_source_sha"])
        with self.assertRaises(gate.VerificationError):
            gate.binding4_context(environment("Linux"), gate.CURRENT_SOURCE_SHA,
                                  "current-process", "linux")

    def test_current_command_cannot_use_historical_source_or_baseline(self):
        context = self.fixture.context
        command = gate.binding4_command_context(context, "product")
        self.assertEqual(command["command_source_sha"], gate.CURRENT_SOURCE_SHA)
        for changed in (context | {"product_source_sha": gate.BINDING4_SOURCE_SHA},
                        context | {"mode": "binding4-process"}, context | {"cohort": "historical5fa"}):
            with self.subTest(context=changed), self.assertRaises(gate.VerificationError):
                gate.binding4_command_context(changed, "product")
        with self.assertRaises(gate.VerificationError):
            gate.binding4_command_context(context, "baseline")

    def test_current_snapshot_requires_exact_commit_tree_inventory_and_hashes(self):
        expected = self.snapshot()["product"]
        with mock.patch.object(gate, "source_snapshot", return_value=expected) as observe:
            self.assertEqual(gate.binding4_source_snapshot(
                self.fixture.repo, "product", mode="current-process"), expected)
            self.assertEqual(observe.call_args.args[1:],
                             (gate.CURRENT_SOURCE_SHA, tuple(gate.CURRENT_REVIEWED), gate.CURRENT_SOURCE_TREE))
        variants = [expected | {"sha": gate.BINDING4_SOURCE_SHA},
                    expected | {"tree": gate.BINDING4_SOURCE_TREE}]
        changed = copy.deepcopy(expected)
        changed["files"]["Cargo.lock"]["sha256"] = "0" * 64
        variants.append(changed)
        changed = copy.deepcopy(expected)
        changed["files"].pop("Cargo.lock")
        variants.append(changed)
        changed = copy.deepcopy(expected)
        changed["files"]["unreviewed"] = {"sha256": "0" * 64}
        variants.append(changed)
        for changed in variants:
            with self.subTest(snapshot=changed), mock.patch.object(
                    gate, "source_snapshot", return_value=changed), self.assertRaises(gate.VerificationError):
                gate.binding4_source_snapshot(self.fixture.repo, "product", mode="current-process")
        with self.assertRaises(gate.VerificationError):
            gate.binding4_source_snapshot(self.fixture.base, "baseline", mode="current-process")

    def test_current_compilation_admits_one_library_binary_and_no_core_integrations(self):
        fixture = self.fixture
        record = fixture.compiler_record("motionwright_storage")
        selected = gate.select_binding4_binaries(
            fixture.compiler_output([record]), 0, fixture.repo, fixture.target, "current-process")
        self.assertEqual(selected, {"motionwright_storage": fixture.binaries["motionwright_storage"]})
        for records in ([record, record], [fixture.compiler_record(name) for name in fixture.binaries]):
            with self.subTest(records=records), self.assertRaises(gate.VerificationError):
                gate.select_binding4_binaries(
                    fixture.compiler_output(records), 0, fixture.repo, fixture.target, "current-process")
        with self.assertRaises(gate.VerificationError):
            gate.select_binding4_binaries(fixture.compiler_output([record]), 0,
                                          fixture.repo, fixture.target, "current-process", baseline=True)

    def test_foreign_current_product_refuses_before_output_or_process_effects(self):
        fixture = self.fixture
        output = fixture.root / "must-not-exist"
        with mock.patch.dict(os.environ, environment(), clear=True), \
                mock.patch.object(gate.sys, "platform", "win32"), \
                mock.patch.object(gate, "execute") as execute, self.assertRaises(gate.VerificationError):
            gate.run_binding4(fixture.repo, output, gate.BINDING4_SOURCE_SHA, "current-process")
        execute.assert_not_called()
        self.assertFalse(output.exists())

    def test_current_group_rejects_old_source_unknown_and_missing_original_log(self):
        fixture = self.fixture
        group = "ddl"
        raw, command, binary, after, names, context = fixture.admission(group)
        self.assertEqual(gate.admit_binding4_group(group, raw, command, binary, after, names, context)["status"], "PASS")
        for change in ({"product_source_sha": gate.BINDING4_SOURCE_SHA},
                       {"command_source_sha": gate.BINDING4_SOURCE_SHA}, {"mode": "binding4-process"}):
            bad = context | change
            with self.subTest(context=bad), self.assertRaises(gate.VerificationError):
                gate.admit_binding4_group(group, raw, command | {"context": bad}, binary, after, names, bad)
        for change in ({"exit_code": None, "observation": "UNKNOWN_TIMEOUT_NO_RETRY"},
                       {"exit_code": 1}, {"log_custody": "INCOMPLETE"}):
            with self.subTest(command=change), self.assertRaises(gate.VerificationError):
                gate.admit_binding4_group(group, raw, command | change, binary, after, names, context)
        with self.assertRaises(gate.VerificationError):
            gate.admit_binding4_group("binding-unit7", *fixture.admission("binding-unit7"))

    def test_current_final_scope_requires_five_and_keeps_all_claim_limits(self):
        rows = [self.fixture.row(group) for group in gate.BINDING4_PROCESS_GROUPS]
        result = self.finish(self.snapshot(), rows)
        self.assertEqual((result["required"], result["executed"], result["passed"]), (5, 5, 5))
        self.assertEqual((result["phase_jsons_retained"], result["child_process_originals_retained"]), (20, 12))
        self.assertEqual(result["ddl_retained_child_originals"], 0)
        for flag in ("baseline_expected_failure_observed", "raw_sqlite_snapshots_retained",
                     "full_cas_archive_retained", "formal_acceptance", "human_acceptance",
                     "release_authorized", "other_source_or_platform_claims"):
            self.assertFalse(result[flag])

    def test_current_final_receipt_cannot_reuse_historical_context_or_product_evidence(self):
        rows = [self.fixture.row(group) for group in gate.BINDING4_PROCESS_GROUPS]
        before = self.snapshot()
        variants = []
        changed = copy.deepcopy(before)
        changed["product"]["sha"] = gate.BINDING4_SOURCE_SHA
        variants.append(changed)
        changed = copy.deepcopy(before)
        changed["product"]["tree"] = gate.BINDING4_SOURCE_TREE
        variants.append(changed)
        changed = copy.deepcopy(before)
        changed["product"]["files"]["Cargo.lock"]["sha256"] = gate.BINDING4_REVIEWED["Cargo.lock"]
        variants.append(changed)
        changed = copy.deepcopy(before)
        changed["baseline"] = self.fixture.snapshot()["baseline"]
        variants.append(changed)
        for changed in variants:
            with self.subTest(snapshot=changed), self.assertRaises(gate.VerificationError):
                self.finish(changed, rows)
        changed_rows = copy.deepcopy(rows)
        for row in changed_rows:
            historical = row["context"] | {"mode": "binding4-process",
                "product_source_sha": gate.BINDING4_SOURCE_SHA, "command_source_sha": gate.BINDING4_SOURCE_SHA}
            row["context"] = historical
            row["command"]["context"] = historical
        with self.assertRaises(gate.VerificationError):
            self.finish(before, changed_rows)

    def test_current_final_cannot_omit_custody_repeat_groups_or_upgrade_ddl_claims(self):
        rows = [self.fixture.row(group) for group in gate.BINDING4_PROCESS_GROUPS]
        variants = [rows[:-1], [rows[0], *rows[:-1]]]
        changed = copy.deepcopy(rows)
        changed[0].pop("phase_custody")
        variants.append(changed)
        changed = copy.deepcopy(rows)
        changed[-1]["retained_child_originals"] = 2
        variants.append(changed)
        for changed in variants:
            with self.subTest(rows=changed), self.assertRaises(gate.VerificationError):
                self.finish(self.snapshot(), changed)

    def test_current_workflow_keeps_historical_checkout_and_dispatch_only_processes(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text(encoding="utf-8")
        self.assertIn("options: [source, historical5fa, binding4-core44, binding4-process, current-source, current-process]", text)
        self.assertIn("ref: '449de7a315a513468610d517e78f9ab87f76ae9d'", text)
        self.assertIn("default: '5faedc090aa45b33833b229050c09bf424de938f'", text)
        self.assertIn("ref: " + "$" + "{{ needs.g0.outputs.source_sha }}", text)
        current_lines = [line.strip() for line in text.splitlines()
                         if line.strip().startswith("if:") and "current-process" in line]
        self.assertEqual(len(current_lines), 8)
        self.assertTrue(all("github.event_name == 'workflow_dispatch'" in line for line in current_lines))
        self.assertIn("and selected['mode'] not in {'source', 'current-source'}", text)
        self.assertNotIn("current-core44", text)

    def test_current_source_cli_cannot_launch_the_product_engine(self):
        import io
        argv = ["gate", "--repo", str(self.fixture.repo), "--output", str(self.fixture.root / "unused"),
                "--source-sha", gate.CURRENT_SOURCE_SHA, "--mode", "current-source"]
        with mock.patch.object(gate.sys, "argv", argv), mock.patch.object(gate.sys, "stderr", io.StringIO()), \
                mock.patch.object(gate, "run_binding4") as run, self.assertRaises(SystemExit) as error:
            gate.main()
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()

    def test_current_timeout_preserves_unknown_and_never_retries_or_starts_second_case(self):
        fixture = self.fixture
        work = fixture.root / "runner"
        work.mkdir()
        g, s = work / "g", work / "s"
        for path in (g, s):
            path.mkdir()
        output = work / "v" / "e"
        snapshots = {"product": {"checkout": str(s), "files": {}}}
        env = environment() | {"GITHUB_WORKSPACE": str(work), "CARGO_TARGET_DIR": str(work / "v" / "t")}
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[:2] in (["rustc", "-Vv"], ["cargo", "-V"]):
                return subprocess.CompletedProcess(argv, 0)
            if argv[0] == "cargo":
                path = work / "v" / "t" / "current-test.exe"
                path.write_bytes(b"synthetic binary, subprocess execution is mocked")
                kwargs["stdout"].write(fixture.compiler_output([fixture.compiler_record("motionwright_storage", path, s)]))
                return subprocess.CompletedProcess(argv, 0)
            if "--list" in argv:
                kwargs["stdout"].write("".join(name + ": test\n" for name in fixture.lib_names).encode())
                return subprocess.CompletedProcess(argv, 0)
            raise subprocess.TimeoutExpired(argv, 120)

        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(gate.sys, "platform", "win32"), \
                mock.patch.object(gate, "__file__", str(g / "tooling" / "ci_storage_apply_recovery.py")), \
                mock.patch.object(gate, "binding4_source_snapshot", side_effect=lambda _path, role, **_kw: snapshots[role]), \
                mock.patch.object(gate, "source_snapshot", return_value={"files": {}}), \
                mock.patch.object(gate.subprocess, "Popen", side_effect=synthetic_spawn_from_run(fake_run)) as process, \
                mock.patch.object(gate, "owned_process_group_state", return_value="ABSENT"), \
                mock.patch.object(gate, "stop_observed_process", return_value=[]):
            self.assertEqual(gate.run_binding4(s, output, gate.CURRENT_SOURCE_SHA, "current-process"), 1)
        receipt = json.loads((output / "receipt.json").read_bytes())
        self.assertEqual(receipt["product_source_sha"], gate.CURRENT_SOURCE_SHA)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(receipt["case_processes_started"], 1)
        self.assertEqual(process.call_count, 5)
        self.assertEqual(sum(argv[0] == "cargo" and "--no-run" in argv for argv in calls), 1)
        self.assertEqual(receipt["results"], [])
        self.assertFalse(receipt["operations_retried"])
        self.assertFalse((output / "case-02.command.json").exists())



class OwnedProcessObservationContracts(unittest.TestCase):
    """Source-level process controls; no Rust, installer or native application."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def observe(self, process, *, group="ABSENT", cleanup=None, owned=True):
        with mock.patch.object(gate.subprocess, "Popen", return_value=process) as spawn, \
                mock.patch.object(gate, "supports_owned_process_group", return_value=owned), \
                mock.patch.object(gate, "owned_process_group_state", return_value=group), \
                mock.patch.object(gate, "stop_observed_process", return_value=[] if cleanup is None else cleanup) as stop:
            row = gate.execute(["synthetic"], self.root, {}, self.root, "case", 17, {})
        self.assertEqual(spawn.call_count, 1)
        return row, spawn, stop

    def test_absent_owned_group_preserves_positive_direct_exit(self):
        row, spawn, cleanup = self.observe(SyntheticObservedProcess())
        self.assertEqual(row["observation"], "DIRECT_PROCESS_EXIT_OBSERVED")
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual(row["owned_group_after_wait"], "ABSENT")
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])
        self.assertEqual(row["escaped_descendants"], "NOT_OBSERVED")
        cleanup.assert_not_called()

    def test_live_owned_group_after_root_zero_keeps_unknown_and_cleanup(self):
        row, _, cleanup = self.observe(SyntheticObservedProcess(), group="PRESENT")
        self.assertTrue(gate.execution_unknown(row))
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual(row["uncertainty_reason"], "OWNED_GROUP_PRESENT_AFTER_DIRECT_EXIT")
        cleanup.assert_called_once()

    def test_direct_only_boundary_does_not_claim_descendant_observation(self):
        row, spawn, cleanup = self.observe(SyntheticObservedProcess(), owned=False)
        self.assertEqual(row["observation"], "DIRECT_PROCESS_EXIT_OBSERVED")
        self.assertEqual(row["process_boundary"], "DIRECT_PROCESS_ONLY")
        self.assertEqual(row["owned_group_after_wait"], "NOT_OBSERVED")
        self.assertEqual(row["escaped_descendants"], "NOT_OBSERVED")
        self.assertFalse(spawn.call_args.kwargs["start_new_session"])
        cleanup.assert_not_called()

    def test_post_launch_oserror_is_unknown_not_launch_failure(self):
        row, _, cleanup = self.observe(SyntheticObservedProcess(error=OSError("wait failed")))
        self.assertEqual(row["observation"], "UNKNOWN_PROCESS_NO_RETRY")
        self.assertEqual(row["pid"], 123456789)
        self.assertEqual(row["uncertainty_reason"], "POST_LAUNCH_OBSERVATION_ERROR")
        cleanup.assert_called_once()

    def test_wait_interrupt_and_secondary_cleanup_interrupt_keep_unknown(self):
        process = SyntheticObservedProcess(error=KeyboardInterrupt("first interruption"))
        with mock.patch.object(gate.subprocess, "Popen", return_value=process), \
                mock.patch.object(gate, "stop_observed_process", side_effect=KeyboardInterrupt("cleanup interruption")):
            row = gate.execute(["synthetic"], self.root, {}, self.root, "case", 17, {})
        self.assertTrue(gate.execution_unknown(row))
        self.assertIn("first interruption", row["error"])
        self.assertIn("cleanup interruption", row["cleanup_errors"][0])

    def test_group_probe_error_after_exit_is_unknown(self):
        process = SyntheticObservedProcess()
        with mock.patch.object(gate.subprocess, "Popen", return_value=process), \
                mock.patch.object(gate, "supports_owned_process_group", return_value=True), \
                mock.patch.object(gate, "owned_process_group_state", side_effect=PermissionError("group unavailable")), \
                mock.patch.object(gate, "stop_observed_process", return_value=[]):
            row = gate.execute(["synthetic"], self.root, {}, self.root, "case", 17, {})
        self.assertTrue(gate.execution_unknown(row))
        self.assertIn("group unavailable", row["error"])

    def test_partial_receipt_failure_carries_unknown_and_no_rewrite(self):
        def partial(path, value):
            Path(path).write_text("partial", encoding="utf-8")
            raise OSError("controlled receipt write failure")
        with mock.patch.object(gate.subprocess, "Popen", return_value=SyntheticObservedProcess()) as spawn, \
                mock.patch.object(gate, "owned_process_group_state", return_value="ABSENT"), \
                mock.patch.object(gate, "write_new", side_effect=partial) as write:
            with self.assertRaises(gate.UnknownObservation) as caught:
                gate.execute(["synthetic"], self.root, {}, self.root, "case", 17, {})
        self.assertEqual(caught.exception.command["observation"], "UNKNOWN_CUSTODY_NO_RETRY")
        self.assertEqual(caught.exception.command["pid"], 123456789)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual((self.root / "case.command.json").read_text(), "partial")

    def test_unknown_stream_failure_cannot_downgrade_observation(self):
        with mock.patch.object(gate.subprocess, "Popen", return_value=SyntheticObservedProcess(error=OSError("wait"))), \
                mock.patch.object(gate, "stop_observed_process", return_value=[]), \
                mock.patch.object(gate, "fingerprint", side_effect=gate.VerificationError("unstable stream")):
            row = gate.execute(["synthetic"], self.root, {}, self.root, "case", 17, {})
        self.assertTrue(gate.execution_unknown(row))
        self.assertEqual(row["log_custody"], "INCOMPLETE")
        self.assertEqual(json.loads((self.root / "case.command.json").read_bytes())["observation"],
                         "UNKNOWN_PROCESS_NO_RETRY")

    def test_ambiguous_spawn_and_stream_close_keep_unknown_without_guessing_a_handle(self):
        original_open = Path.open

        class FailingClose:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self.stream.__enter__()

            def __exit__(self, *error):
                self.stream.__exit__(*error)
                raise OSError("stderr finalization failed")

        def opened(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            return FailingClose(stream) if mode == "xb" and path.name.endswith(".stderr.log") else stream

        with mock.patch.object(Path, "open", opened), \
                mock.patch.object(gate.subprocess, "Popen", side_effect=OSError("spawn handle unavailable")) as spawn, \
                mock.patch.object(gate, "stop_observed_process") as stop:
            row = gate.execute(["synthetic"], self.root, {}, self.root, "case", 17, {"case": "ambiguous"})
        self.assertEqual(row["observation"], "UNKNOWN_PROCESS_NO_RETRY")
        self.assertEqual(row["uncertainty_reason"], "SPAWN_HANDLE_NOT_OBSERVED")
        self.assertEqual(row["error"], "OSError: spawn handle unavailable")
        self.assertEqual(row["secondary_observation_errors"], ["OSError: stderr finalization failed"])
        self.assertIsNone(row["pid"])
        self.assertEqual(row["context"], {"case": "ambiguous"})
        self.assertEqual(row["diagnostic_custody"], "UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED")
        self.assertEqual(json.loads((self.root / "case.command.json").read_bytes()), row)
        spawn.assert_called_once()
        stop.assert_not_called()

    def test_timeout_and_process_and_custody_unknown_share_stop_predicate(self):
        for value in ("UNKNOWN_TIMEOUT_NO_RETRY", "UNKNOWN_PROCESS_NO_RETRY", "UNKNOWN_CUSTODY_NO_RETRY"):
            self.assertTrue(gate.execution_unknown({"observation": value}))
        for value in ("DIRECT_PROCESS_EXIT_OBSERVED", "NOT_STARTED_LAUNCH_ERROR", ""):
            self.assertFalse(gate.execution_unknown({"observation": value}))


if __name__ == "__main__":
    unittest.main()
