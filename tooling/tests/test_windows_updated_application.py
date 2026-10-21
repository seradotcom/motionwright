"""Synthetic contract controls; no installer, installed app or native UI runs."""
from __future__ import annotations

import copy
import hashlib
import io
from contextlib import ExitStack, closing, contextmanager, redirect_stdout
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
import windows_updated_application as oracle
import nsis_owned_files as owned

UUID = "11111111-1111-4111-8111-111111111111"
GENERATION = "22222222-2222-4222-8222-222222222222"
OPERATION = "33333333-3333-4333-8333-333333333333"
INTERRUPTION = "44444444-4444-4444-8444-444444444444"
RECOVERY = "55555555-5555-4555-8555-555555555555"
BRANCH = "66666666-6666-4666-8666-666666666666"
RUN = {"repository": "example/project", "run_id": 12, "run_attempt": 1}
PROCESS = {"pid": 123, "creation_filetime": 456, "window_handle": 789}


def document():
    return {"schema_version": 2, "id": UUID, "generation": GENERATION, "revision": 0,
            "title": "Preserved project", "updated_at": "2026-01-01T00:00:00Z",
            "active_branch": BRANCH, "branches": [{"id": BRANCH, "head_revision": 0}],
            "scenes": [{"id": "scene", "name": "Preserved scene"}], "assets": []}


def values():
    return {"operation_id": OPERATION, "project_id": UUID, "generation": GENERATION,
            "revision": 0, "old_title": "Preserved project", "new_title": "Updated project"}


def snapshots():
    before = {"storage_schema": 2, "document": document(), "events": [], "receipts": []}
    after = copy.deepcopy(before)
    after["storage_schema"] = 3
    after["document"].update(revision=1, title="Updated project", updated_at="2026-01-02T00:00:00Z")
    after["document"]["branches"][0]["head_revision"] = 1
    after["events"] = [[1, "change", json.dumps({"type": "rename_project", "title": "Updated project"}), "now"]]
    reply = {"project": after["document"], "previous_revision": 0, "request_id": "original-request",
             "replayed": False}
    after["receipts"] = [["original-request", "d" * 64, json.dumps(reply), "now"]]
    return before, after


def ui_record(mode="edit"):
    return {"schema": oracle.UI_SCHEMA, "operation_id": OPERATION, "mode": mode,
            "state": "ACK" if mode == "edit" else "OBSERVED", "phase": "observed",
            "project_id": UUID, "revision": 1, "title": "Updated project",
            "ack_marker": "The original save reply and saved revision r1 agree." if mode == "edit" else None,
            "save_attempts": 1 if mode == "edit" else 0,
            "selection_confirmed": True, "future_ack_absent": True, "process": PROCESS}


def encode(value):
    return json.dumps(value).encode()


def make_db(path, snap):
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(
            "CREATE TABLE projects(id TEXT PRIMARY KEY,generation TEXT,revision INTEGER,title TEXT,document_json TEXT);"
            "CREATE TABLE events(project_id TEXT,revision INTEGER,kind TEXT,payload_json TEXT,created_at TEXT);"
            "CREATE TABLE receipts(project_id TEXT,request_id TEXT,request_sha256 TEXT,result_json TEXT,created_at TEXT);"
        )
        replace_db(db, snap)


def replace_db(db, snap):
    p = snap["document"]
    db.execute("PRAGMA user_version=" + str(snap["storage_schema"]))
    db.execute("DELETE FROM projects")
    db.execute("INSERT INTO projects VALUES(?,?,?,?,?)",
               (p["id"], p["generation"], p["revision"], p["title"], json.dumps(p)))
    for name in ("events", "receipts"):
        db.execute("DELETE FROM " + name)
        for row in snap[name]:
            db.execute("INSERT INTO " + name + " VALUES(?,?,?,?,?)", [p["id"], *row])



class SyntheticLifetimeBoundary:
    """Fake OS boundaries while the actual lifetime/oracle code runs."""

    def __init__(self, root, fault=None, on_dispatch=None):
        self.fault = fault
        self.on_dispatch = on_dispatch
        self.system_root = root / "synthetic-system"
        powershell = self.system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
        powershell.parent.mkdir(parents=True)
        powershell.write_bytes(b"synthetic controller marker; never executable")
        self.environment = {"SystemRoot": str(self.system_root)}
        self.process = mock.Mock(pid=PROCESS["pid"], returncode=None)
        self.process.poll.side_effect = self.poll
        self.process.kill.side_effect = self.kill
        self.process.wait.side_effect = lambda timeout=None: self.process.returncode
        self.gracefully_closed = False
        self.ack_records_written = 0
        self.application_launch = None
        self.controller_dispatch = None

    def poll(self):
        if self.fault == "cleanup-poll" and self.gracefully_closed:
            raise OSError("synthetic original-handle final observation failure")
        return self.process.returncode

    def kill(self):
        self.process.returncode = -9

    def close(self, window, process):
        if window != PROCESS["window_handle"] or process is not self.process:
            raise AssertionError("cleanup must retain the original process and window")
        self.gracefully_closed = True
        self.process.returncode = 0

    def dispatch(self, command, **kwargs):
        if self.on_dispatch is not None:
            self.on_dispatch()
        if self.fault == "opaque-oserror":
            raise OSError("synthetic opaque controller boundary failure")
        if self.fault == "timeout-empty":
            raise oracle.subprocess.TimeoutExpired(command, 90)
        mode = command[command.index("-Mode") + 1]
        record = ui_record(mode)
        code = 0
        if self.fault == "original-not-sent":
            record.update(state="NOT_SENT", phase="PRECHECK_REFUSED", save_attempts=0,
                          selection_confirmed=False, future_ack_absent=False,
                          revision=0, title="Preserved project", ack_marker=None)
            code = 75
        kwargs["stdout"].write(encode(record))
        kwargs["stdout"].flush()
        self.ack_records_written += int(record["state"] == "ACK")
        if self.fault == "timeout-ack":
            raise oracle.subprocess.TimeoutExpired(command, 90)
        return SimpleNamespace(returncode=code)

    @contextmanager
    def active(self):
        real_open, real_read, real_exists = Path.open, Path.read_bytes, Path.exists
        real_retain, real_ref = oracle.retain_observation, oracle.life.retained_ref
        fault = self.fault

        class FailingClose:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self.stream.__enter__()

            def __exit__(self, *args):
                self.stream.__exit__(*args)
                raise OSError("synthetic controller stream-close failure")

        def open_file(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            if fault == "pre-dispatch-open" and path.name == "controller.stdout.log" and mode == "xb":
                raise PermissionError("synthetic pre-dispatch output refusal")
            stream = real_open(path, *args, **kwargs)
            if fault == "stream-close" and path.name == "controller.stderr.log" and mode == "xb":
                return FailingClose(stream)
            return stream

        def read_file(path):
            if fault == "stdout-read" and path.name == "controller.stdout.log":
                raise PermissionError("synthetic original stdout unavailable")
            return real_read(path)

        def exists(path):
            if fault == "log-exists" and path.name == "controller.stdout.log":
                raise OSError("synthetic final log observation failure")
            return real_exists(path)

        def retain(output, name, original):
            if fault == "ui-publication" and name.endswith("/ui-original.json"):
                def failed_publisher(*_):
                    raise OSError("synthetic ACK publication failure")
                return real_retain(output, name, original, failed_publisher)
            return real_retain(output, name, original)

        def reference(output, path):
            if fault == "log-reference" and path.name == "controller.stdout.log":
                raise OSError("synthetic retained-log publication failure")
            return real_ref(output, path)

        with ExitStack() as stack:
            self.application_launch = stack.enter_context(mock.patch.object(
                oracle.subprocess, "Popen", return_value=self.process))
            self.controller_dispatch = stack.enter_context(mock.patch.object(
                oracle.subprocess, "run", side_effect=self.dispatch))
            stack.enter_context(mock.patch.object(oracle, "process_identity", return_value={
                "pid": PROCESS["pid"], "creation_filetime": PROCESS["creation_filetime"]}))
            stack.enter_context(mock.patch.object(oracle.cold, "windows_motionwright_window",
                                                 return_value=PROCESS["window_handle"]))
            stack.enter_context(mock.patch.object(oracle.cold, "graceful_close", side_effect=self.close))
            stack.enter_context(mock.patch.object(Path, "open", open_file))
            stack.enter_context(mock.patch.object(Path, "read_bytes", read_file))
            stack.enter_context(mock.patch.object(Path, "exists", exists))
            stack.enter_context(mock.patch.object(oracle, "retain_observation", side_effect=retain))
            stack.enter_context(mock.patch.object(oracle.life, "retained_ref", side_effect=reference))
            yield self


class NativeLifetimeBoundaryContracts(unittest.TestCase):
    def exercise(self, fault=None, invalid_main=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "output"
            output.mkdir()
            executable = root / "application.exe"
            executable.write_bytes(b"synthetic installed bytes; never executable")
            expected = oracle.life.file_identity(executable)
            if invalid_main:
                expected["sha256"] = "0" * 64
            boundary = SyntheticLifetimeBoundary(root, fault)
            escaped = None
            result = None
            with boundary.active():
                try:
                    result = oracle.run_lifetime(output, "first", "edit", values(),
                                                 executable, expected, boundary.environment)
                except Exception as error:
                    escaped = type(error).__name__ + ": " + str(error)
            saved = output / "first/ui-original.json"
            original = json.loads(saved.read_text()) if saved.is_file() else None
            if escaped is not None:
                self.fail("run_lifetime lost its local outcome: " + escaped
                          + "; retained original=" + json.dumps(original))
            return SimpleNamespace(result=result, original=original,
                                   launches=boundary.application_launch.call_count,
                                   dispatches=boundary.controller_dispatch.call_count,
                                   ack_records=boundary.ack_records_written)

    def assert_incomplete(self, outcome, state, launches=1, dispatches=1):
        self.assertEqual(outcome.result["ui"]["state"], state)
        self.assertEqual(outcome.launches, launches)
        self.assertEqual(outcome.dispatches, dispatches)
        with self.assertRaises(oracle.OracleError):
            oracle.require_lifetime(outcome.result, "edit")

    def test_preflight_and_precontroller_failures_never_dispatch(self):
        self.assert_incomplete(self.exercise(invalid_main=True), "NOT_STARTED", 0, 0)
        self.assert_incomplete(self.exercise("pre-dispatch-open"), "NOT_STARTED", 1, 0)

    def test_validated_zero_save_original_is_not_sent(self):
        outcome = self.exercise("original-not-sent")
        self.assert_incomplete(outcome, "NOT_SENT")
        self.assertEqual(outcome.result["ui"]["record"]["save_attempts"], 0)
        self.assertEqual(outcome.ack_records, 0)

    def test_stdout_read_failure_after_dispatch_is_unknown(self):
        outcome = self.exercise("stdout-read")
        self.assert_incomplete(outcome, "UNKNOWN")
        self.assertEqual(outcome.ack_records, 1)
        self.assertIsNone(outcome.result["ui"].get("record"))

    def test_controller_stream_close_failure_is_unknown(self):
        outcome = self.exercise("stream-close")
        self.assert_incomplete(outcome, "UNKNOWN")
        self.assertEqual(outcome.ack_records, 1)

    def test_opaque_controller_oserror_is_unknown(self):
        outcome = self.exercise("opaque-oserror")
        self.assert_incomplete(outcome, "UNKNOWN")
        self.assertIsNone(outcome.result["ui"].get("record"))
        self.assertNotEqual(outcome.result["ui"].get("reason"), "CONTROLLER_NOT_STARTED")

    def test_ack_survives_final_original_process_poll_failure(self):
        outcome = self.exercise("cleanup-poll")
        self.assert_incomplete(outcome, "ACK")
        self.assertEqual(outcome.original["state"], "ACK")
        self.assertTrue(outcome.result["custody_errors"])

    def test_ack_survives_custody_failures_in_direct_lifetime(self):
        for fault in ("ui-publication", "log-reference", "log-exists"):
            with self.subTest(fault=fault):
                outcome = self.exercise(fault)
                self.assert_incomplete(outcome, "ACK")
                self.assertTrue(outcome.result["custody_errors"])
                self.assertEqual(outcome.ack_records, 1)

    def test_complete_original_ack_survives_timeout_without_completion(self):
        for exit_code in (None, 0):
            with self.subTest(exit_code=exit_code):
                observed = oracle.classify_ui(encode(ui_record()), exit_code, True,
                                              "edit", values(), PROCESS)
                self.assertEqual(observed["state"], "ACK")
                self.assertEqual(observed["record"], ui_record())
                self.assertFalse(observed["controller_complete"])
        outcome = self.exercise("timeout-ack")
        self.assert_incomplete(outcome, "ACK")
        self.assertEqual(outcome.original["state"], "ACK")
        self.assertFalse(outcome.result["ui"]["controller_complete"])


class NativeUiResultContracts(unittest.TestCase):
    def classify(self, record, code=0, timeout=False, mode="edit"):
        return oracle.classify_ui(encode(record), code, timeout, mode, values(), PROCESS)

    def test_original_reply_marker_and_current_revision_are_required(self):
        self.assertEqual(self.classify(ui_record())["state"], "ACK")
        for change in ({"ack_marker": None}, {"revision": 0}, {"title": "Different"},
                       {"selection_confirmed": False}, {"future_ack_absent": False}):
            with self.subTest(change=change):
                self.assertEqual(self.classify(ui_record() | change)["state"], "UNKNOWN")

    def test_foreign_uuid_same_title_revision_and_foreign_process_are_not_ack(self):
        for change in ({"project_id": GENERATION}, {"process": PROCESS | {"pid": 999}},
                       {"operation_id": RECOVERY}, {"mode": "observe"}):
            self.assertEqual(self.classify(ui_record() | change)["state"], "UNKNOWN")

    def test_invoke_only_or_unconfirmed_result_cannot_become_ack(self):
        for marker in ("Invoke returned", "Saved values match", "Unconfirmed brief submission"):
            self.assertEqual(self.classify(ui_record() | {"ack_marker": marker})["state"], "UNKNOWN")

    def test_timeout_does_not_admit_even_a_partial_ack_record(self):
        raw = encode(ui_record())[:-1]
        result = oracle.classify_ui(raw, None, True, "edit", values(), PROCESS)
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertIsNone(result["record"])
        self.assertFalse(result["controller_complete"])

    def test_missing_duplicate_or_nonfinite_original_json_is_unknown(self):
        for raw in (b"", b'{"schema":1,"schema":2}', b'{"value":NaN}', b"partial"):
            self.assertEqual(oracle.classify_ui(raw, 0, False, "edit", values(), PROCESS)["state"], "UNKNOWN")

    def test_known_ack_survives_late_controller_failure_but_is_incomplete(self):
        result = self.classify(ui_record(), code=75)
        self.assertEqual(result["state"], "ACK")
        self.assertFalse(result["controller_complete"])

    def test_not_sent_requires_zero_save_invocations(self):
        record = ui_record() | {"state": "NOT_SENT", "save_attempts": 0}
        self.assertEqual(self.classify(record, code=75)["state"], "NOT_SENT")
        self.assertEqual(self.classify(record | {"save_attempts": 1}, code=75)["state"], "UNKNOWN")

    def test_read_only_reopen_never_accepts_a_save_or_ack(self):
        record = ui_record("observe")
        self.assertEqual(self.classify(record, mode="observe")["state"], "OBSERVED")
        for change in ({"save_attempts": 1}, {"state": "ACK"}, {"ack_marker": "old ACK"},
                       {"revision": 2}, {"selection_confirmed": False}):
            self.assertEqual(self.classify(record | change, mode="observe")["state"], "UNKNOWN")

    def test_duplicate_save_count_is_rejected(self):
        for count in (True, 2, -1, "1"):
            self.assertEqual(self.classify(ui_record() | {"save_attempts": count})["state"], "UNKNOWN")

    def test_ack_and_publication_failure_remain_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            def fail(*_):
                raise OSError("synthetic custody failure")
            result = oracle.retain_observation(Path(temp), "result.json", {"state": "ACK"}, fail)
        self.assertEqual(result["original"], {"state": "ACK"})
        self.assertEqual(result["custody"], "INCOMPLETE")
        self.assertTrue(result["errors"])

    def test_lifetime_requires_known_graceful_terminal_without_custody_loss(self):
        valid = {"mode": "edit", "process": PROCESS,
                 "ui": {"state": "ACK", "controller_complete": True},
                 "terminal": oracle.life.observed_terminal(0),
                 "graceful_close": True, "forced_stop": False, "custody_errors": []}
        oracle.require_lifetime(valid, "edit")
        for change in ({"ui": {"state": "UNKNOWN", "controller_complete": False}},
                       {"terminal": oracle.life.observed_terminal(None)},
                       {"forced_stop": True}, {"custody_errors": ["missing"]},
                       {"graceful_close": False}):
            with self.assertRaises(oracle.OracleError):
                oracle.require_lifetime(valid | change, "edit")


class SemanticProjectContracts(unittest.TestCase):
    def test_one_rename_and_read_only_reopen_are_observed(self):
        before, after = snapshots()
        self.assertTrue(oracle.verify_edit(before, after, copy.deepcopy(after), values())["one_edit"])

    def test_foreign_generation_stale_revision_and_wrong_title_fail(self):
        before, after = snapshots()
        for change in ({"id": GENERATION}, {"generation": UUID}, {"revision": 2}, {"title": "Other"}):
            bad = copy.deepcopy(after)
            bad["document"].update(change)
            with self.assertRaises(oracle.OracleError):
                oracle.verify_edit(before, bad, bad, values())

    def test_old_content_collection_changes_are_not_a_rename(self):
        before, after = snapshots()
        bad = copy.deepcopy(after)
        bad["document"]["scenes"][0]["name"] = "Unrelated changed content"
        with self.assertRaisesRegex(oracle.OracleError, "unrelated_project_content"):
            oracle.verify_edit(before, bad, bad, values())

    def test_second_lifetime_cannot_add_another_revision_or_receipt(self):
        before, after = snapshots()
        reopened = copy.deepcopy(after)
        reopened["document"]["revision"] = 2
        with self.assertRaisesRegex(oracle.OracleError, "read_only_reopen"):
            oracle.verify_edit(before, after, reopened, values())

    def test_event_without_exact_rename_and_original_receipt_fails(self):
        before, after = snapshots()
        for field in ("events", "receipts"):
            bad = copy.deepcopy(after)
            bad[field] = []
            with self.assertRaises(oracle.OracleError):
                oracle.verify_edit(before, bad, bad, values())

    def test_schema_migration_is_separate_from_storage_and_revision(self):
        before, after = snapshots()
        before["document"]["schema_version"] = 1
        after["document"]["new_default"] = []
        reply = json.loads(after["receipts"][-1][2])
        reply["project"] = after["document"]
        after["receipts"][-1][2] = json.dumps(reply)
        self.assertTrue(oracle.verify_edit(before, after, after, values())["one_edit"])
        bad = copy.deepcopy(after)
        bad["storage_schema"] = 2
        with self.assertRaisesRegex(oracle.OracleError, "schema_not_current"):
            oracle.verify_edit(before, bad, bad, values())

    def test_current_schema_cannot_gain_an_unrelated_field(self):
        before, after = snapshots()
        after["document"]["unexpected"] = "unrelated"
        with self.assertRaisesRegex(oracle.OracleError, "project_fields_lost"):
            oracle.verify_edit(before, after, after, values())

    def test_old_receipts_are_preserved_and_new_reply_is_not_replayed(self):
        before, after = snapshots()
        old = ["previous", "e" * 64, "{}", "before"]
        before["receipts"].append(old)
        after["receipts"].insert(0, old)
        oracle.verify_edit(before, after, after, values())
        bad = copy.deepcopy(after)
        reply = json.loads(bad["receipts"][-1][2])
        reply["replayed"] = True
        bad["receipts"][-1][2] = json.dumps(reply)
        with self.assertRaises(oracle.OracleError):
            oracle.verify_edit(before, bad, bad, values())


    def test_rename_invalidates_only_the_original_active_branch_resolution(self):
        before, after = snapshots()
        review = {"id": "review", "anchor": {"branch_id": BRANCH, "revision": 0},
                  "status": "resolved", "body": "Preserve the authored review",
                  "resolution": "Reviewed before edit", "resolved_at": "before"}
        before["document"]["reviews"] = [review]
        after["document"]["reviews"] = [review | {"status": "needs_recheck",
                                                   "resolution": None, "resolved_at": None}]
        reply = json.loads(after["receipts"][-1][2])
        reply["project"] = after["document"]
        after["receipts"][-1][2] = json.dumps(reply)
        oracle.verify_edit(before, after, after, values())
        bad = copy.deepcopy(after)
        bad["document"]["reviews"][0]["status"] = "resolved"
        with self.assertRaisesRegex(oracle.OracleError, "review_resolution_not_invalidated"):
            oracle.verify_edit(before, bad, bad, values())
        bad = copy.deepcopy(after)
        bad["document"]["reviews"][0]["body"] = "Unrelated replacement"
        with self.assertRaisesRegex(oracle.OracleError, "unrelated_project_content_changed"):
            oracle.verify_edit(before, bad, bad, values())


class FamilyCustodyContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.live = self.root / "live"
        self.live.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        self.db = self.live / oracle.FAMILY[0]
        _, self.snapshot = snapshots()
        make_db(self.db, self.snapshot)

    def test_capture_keeps_original_family_and_reads_only_inspection_copy(self):
        original = oracle.life.file_identity(self.db)
        value = oracle.capture_family(self.output, "after", self.db, UUID)
        self.assertEqual(value["semantic"], self.snapshot)
        self.assertEqual(oracle.life.file_identity(self.db), original)
        self.assertIsNone(value["sqlite_family"][oracle.FAMILY[1]])
        self.assertIsNone(value["sqlite_family"][oracle.FAMILY[2]])

    def test_wal_commit_is_included_and_omitting_it_cannot_pass(self):
        writer = sqlite3.connect(self.db)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE projects SET title=?, document_json=? WHERE id=?",
                       ("WAL title", json.dumps(self.snapshot["document"] | {"title": "WAL title"}), UUID))
        writer.commit()
        value = oracle.capture_family(self.output, "wal", self.db, UUID)
        self.assertEqual(value["semantic"]["document"]["title"], "WAL title")
        self.assertIsNotNone(value["sqlite_family"][oracle.FAMILY[1]])
        self.assertIsNotNone(value["sqlite_family"][oracle.FAMILY[2]])
        omitted = dict(value["sqlite_family"])
        omitted[oracle.FAMILY[1]] = None
        stale = oracle.inspect_retained_family(self.output, omitted, self.output / "omitted", UUID, (3,))
        self.assertNotEqual(stale["document"]["title"], "WAL title")

    def test_missing_family_and_live_sidecar_substitution_refuse(self):
        value = oracle.capture_family(self.output, "after", self.db, UUID)
        oracle.verify_live_family(self.db, value["sqlite_family"], self.output)
        (self.live / oracle.FAMILY[1]).write_bytes(b"foreign WAL")
        with self.assertRaisesRegex(oracle.OracleError, "unexpected_sqlite_sidecar"):
            oracle.verify_live_family(self.db, value["sqlite_family"], self.output)

    def test_captured_original_mutation_and_orphan_sidecar_are_rejected(self):
        value = oracle.capture_family(self.output, "after", self.db, UUID)
        ref = value["sqlite_family"][oracle.FAMILY[0]]
        (self.output / ref["path"]).write_bytes(b"tampered")
        with self.assertRaises(oracle.life.LifecycleError):
            oracle.inspect_retained_family(self.output, value["sqlite_family"],
                                           self.output / "again", UUID)
        family = {name: None for name in oracle.FAMILY}
        family[oracle.FAMILY[1]] = ref
        with self.assertRaisesRegex(oracle.OracleError, "database_absent"):
            oracle.inspect_retained_family(self.output, family, self.output / "orphan", UUID)

    def test_future_storage_schema_and_inconsistent_columns_fail(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(oracle.OracleError, "sqlite_schema_unsupported"):
            oracle.inspect_project(self.db, UUID)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("PRAGMA user_version=3")
            db.execute("UPDATE projects SET title='foreign'")
        with self.assertRaisesRegex(oracle.OracleError, "project_columns_disagree"):
            oracle.inspect_project(self.db, UUID)

    def test_exclusive_capture_never_overwrites_prior_attempt(self):
        oracle.capture_family(self.output, "after", self.db, UUID)
        with self.assertRaises(FileExistsError):
            oracle.capture_family(self.output, "after", self.db, UUID)


class OriginalAdmissionContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.install = self.root / "install"
        self.install.mkdir()
        self.live = self.root / "live"
        self.live.mkdir()
        self.database = self.live / oracle.FAMILY[0]
        before, _ = snapshots()
        make_db(self.database, before)
        self.exe = self.install / "motionwright-desktop.exe"
        self.exe.write_bytes(b"MZcurrent synthetic nonexecutable")
        self.refs = {}
        def ref(name, content):
            path = self.evidence / name
            path.write_bytes(content if isinstance(content, bytes) else encode(content))
            return oracle.life.retained_ref(self.evidence, path)
        self.ref = ref
        self.current = ref("current-main.bin", self.exe.read_bytes())
        self.normal = ref("normal-setup.bin", b"MZnormal synthetic nonexecutable")
        self.optin = ref("opt-in-setup.bin", b"MZopt-in synthetic nonexecutable")
        old = ref("old-main.bin", b"MZpredecessor synthetic nonexecutable")
        uninstaller = ref("old-uninstaller.bin", b"MZuninstaller synthetic nonexecutable")
        family = {name: None for name in oracle.FAMILY}
        family[oracle.FAMILY[0]] = ref("original.sqlite3", self.database.read_bytes())
        registry = {"32": {"Software\\Example": None}, "64": {"Software\\Example": None}}
        def snap(operation, position, main):
            return {"schema": "motionwright.windows-lifecycle-snapshot/1", "operation_id": operation,
                    "position": position, "install_root": str(self.install), "database": str(self.database),
                    "installed": {self.exe.name: main, "uninstall.exe": uninstaller},
                    "registry": registry, "sqlite_family": family, "project": None}
        profile = {"schema": "motionwright.nsis-predecessor/1",
                   "target": {"version": "0.1.1", "main_sha256": self.current["sha256"]},
                   "files": [{"relative": self.exe.name, **oracle.life.ref_identity(old)},
                             {"relative": "uninstall.exe", **oracle.life.ref_identity(uninstaller)}]}
        prof = ref("profile.json", profile)
        context = {"id": "{" + INTERRUPTION.upper() + "}", "pid": "51", "creation_filetime": "100",
                   "product_sha": "a" * 40, "gate_sha": "b" * 40,
                   "setup_sha256": self.optin["sha256"], "predecessor_profile_sha256": prof["sha256"],
                   "candidate_sha256": self.current["sha256"], "candidate_size": str(self.current["bytes"]),
                   "run_id": "12", "run_attempt": "1"}
        recovery = {"schema": "motionwright.windows-lifecycle-terminal/1", "phase": "upgrade",
                    "context": {"operation_id": RECOVERY, "process": {"pid": 52, "creation_filetime": 101},
                                "executable": oracle.life.ref_identity(self.normal), "source_sha": "a" * 40,
                                "run": RUN}, "original_terminal": oracle.life.observed_terminal(0)}
        source = {"product": {"sha": "a" * 40, "tree": "c" * 40}, "gate": {"sha": "b" * 40}}
        compile_receipt = {"schema": "motionwright.nsis-interruption-compile/1",
                           "compile": "TWO_VARIANTS_COMPILED", "product_sha": "a" * 40,
                           "gate_sha": "b" * 40, "source_before": source, "source_after": source,
                           "real_application": oracle.life.ref_identity(self.current),
                           "variants": [{"variant": variant, "result": "COMPILE_ONLY",
                                         "setup": oracle.life.ref_identity(setup),
                                         "main_capture": oracle.life.ref_identity(self.current)}
                                        for variant, setup in (("normal", self.normal), ("opt-in", self.optin))]}
        self.value = {"schema": oracle.INPUT_SCHEMA, **values(), "run": RUN,
                      "product_sha": "a" * 40, "gate_sha": "b" * 40, "version": "0.1.1",
                      "registry_keys": ["Software\\Example"], "install_root": str(self.install),
                      "database": str(self.database), "executable": str(self.exe),
                      "refs": {"compile_summary": ref("compile.json", compile_receipt),
                               "normal_setup": self.normal, "opt_in_setup": self.optin,
                               "current_main": self.current, "predecessor_profile": prof,
                               "checkpoint": ref("checkpoint.bin", b"synthetic checkpoint reader input"),
                               "interruption_context": ref("context.json", context),
                               "interruption_terminal": ref("interrupted.json", {"state": "TERMINATED"}),
                               "recovery_terminal": ref("recovery.json", recovery),
                               "before_snapshot": ref("before.json", snap(INTERRUPTION, "before", old)),
                               "recovered_snapshot": ref("after.json", snap(RECOVERY, "after", self.current))}}
        self.gate = SimpleNamespace(parse_checkpoint=mock.Mock(return_value={}),
                                    validate_live_context=mock.Mock(), validate_termination=mock.Mock())

    def replace_original(self, role, mutate):
        original = oracle.read_json(self.evidence, self.value["refs"][role])
        mutate(original)
        self.value["refs"][role] = self.ref(role + "-changed.json", original)

    def test_complete_original_bindings_delegate_to_canonical_nsis_reader(self):
        result = oracle.admit_originals(self.evidence, self.value, self.gate)
        self.assertEqual(result["recovery_id"], RECOVERY)
        self.gate.validate_termination.assert_called_once()
        self.assertEqual(result["main"], oracle.life.ref_identity(self.current))

    def test_recovery_unknown_and_error_cannot_start_application(self):
        for exit_code in (None, 2, 3):
            with self.subTest(exit_code=exit_code):
                original = oracle.read_json(self.evidence, self.value["refs"]["recovery_terminal"])
                original["original_terminal"] = oracle.life.observed_terminal(exit_code)
                candidate = copy.deepcopy(self.value)
                candidate["refs"]["recovery_terminal"] = self.ref("recovery-" + str(exit_code) + ".json", original)
                with mock.patch.object(oracle.subprocess, "Popen") as launch:
                    with self.assertRaises(oracle.OracleError):
                        oracle.admit_originals(self.evidence, candidate, self.gate)
                    launch.assert_not_called()

    def test_wrong_compiled_source_or_changed_compile_observation_fails(self):
        self.replace_original("compile_summary", lambda x: x.update(product_sha="c" * 40))
        with self.assertRaisesRegex(oracle.OracleError, "current_compile_missing"):
            oracle.admit_originals(self.evidence, self.value, self.gate)

    def test_recovery_old_setup_or_reused_process_fails(self):
        self.replace_original("recovery_terminal", lambda x: x["context"].update(
            process={"pid": 51, "creation_filetime": 100}))
        with self.assertRaisesRegex(oracle.OracleError, "reuses_interrupted_process"):
            oracle.admit_originals(self.evidence, self.value, self.gate)

    def test_installer_must_preserve_the_complete_family(self):
        self.replace_original("recovered_snapshot", lambda x: x["sqlite_family"].update(
            {oracle.FAMILY[1]: self.current}))
        with self.assertRaisesRegex(oracle.OracleError, "installer_changed_project_family"):
            oracle.admit_originals(self.evidence, self.value, self.gate)

    def test_missing_original_or_self_asserted_pass_does_not_admit(self):
        self.replace_original("recovery_terminal", lambda x: x.clear())
        with self.assertRaises(oracle.OracleError):
            oracle.admit_originals(self.evidence, self.value, self.gate)

    def test_foreign_profile_digest_and_unreviewed_input_fields_fail(self):
        self.replace_original("interruption_context", lambda x: x.update(
            predecessor_profile_sha256="e" * 64))
        with self.assertRaisesRegex(oracle.OracleError, "interruption_payload"):
            oracle.admit_originals(self.evidence, self.value, self.gate)
        with self.assertRaisesRegex(oracle.OracleError, "input_fields"):
            oracle.input_shape(self.value | {"assume_accepted": True})

    def test_input_path_alias_and_sendkeys_metacharacters_fail_before_effects(self):
        for value in (self.value | {"new_title": "bad+keys"}, self.value | {"new_title": " Updated "},
                      self.value | {"database": str(self.live / "../live/motionwright.sqlite3")}):
            with self.assertRaises((oracle.OracleError, ValueError)):
                oracle.input_shape(value)

    def test_operation_identity_cannot_reuse_interruption_or_recovery(self):
        for operation in (INTERRUPTION, RECOVERY):
            with self.assertRaisesRegex(oracle.OracleError, "operation_identity_reused"):
                oracle.admit_originals(self.evidence, self.value | {"operation_id": operation}, self.gate)


    def run_synthetic_observation(self, first_change=None):
        """Mock native boundaries; use real retained SQLite and original refs."""
        product, gate_repo = self.root / "product", self.root / "gate"
        product.mkdir()
        gate_repo.mkdir()
        source = lambda _repo, sha, _paths: {"sha": sha, "tree": "c" * 40, "files": {}}
        self.gate.source_observation = source
        self.gate.GATE_PATHS = ("tooling/canonical-gate.py",)
        self.gate.PRODUCT_PATHS = ("installer.nsi",)
        input_ref = self.ref("input.json", self.value)
        output = self.root / "observation"
        args = SimpleNamespace(output=output, evidence_root=self.evidence,
                               product_repo=product, gate_repo=gate_repo,
                               input=self.evidence / input_ref["path"],
                               input_sha256=input_ref["sha256"],
                               product_source="a" * 40, gate_source="b" * 40,
                               oracle_source="f" * 40)
        environment = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows",
                       "GITHUB_REPOSITORY": RUN["repository"], "GITHUB_RUN_ID": "12",
                       "GITHUB_RUN_ATTEMPT": "1"}
        _, after = snapshots()
        def lifetime(_out, _label, mode, *_args):
            result = {"mode": mode, "process": {"pid": 80 if mode == "edit" else 81,
                                               "creation_filetime": 1000 if mode == "edit" else 1001},
                      "ui": {"state": "ACK" if mode == "edit" else "OBSERVED",
                             "controller_complete": True},
                      "terminal": oracle.life.observed_terminal(0), "graceful_close": True,
                      "forced_stop": False, "custody_errors": []}
            if mode == "edit":
                with closing(sqlite3.connect(self.database)) as db, db:
                    replace_db(db, after)
                if first_change:
                    result.update(copy.deepcopy(first_change))
            return result
        with (mock.patch.object(oracle, "os", SimpleNamespace(name="nt", environ=environment)),
              mock.patch.object(oracle, "load_canonical_gate", return_value=self.gate),
              mock.patch.object(oracle.life, "read_pe_versions", return_value={}),
              mock.patch.object(oracle.life, "require_pe_version"),
              mock.patch.object(oracle, "emit_predecessor_include", return_value={"enabled": True}),
              mock.patch.object(oracle, "run_lifetime", side_effect=lifetime) as lifetimes,
              mock.patch.object(oracle.subprocess, "Popen") as forbidden_native,
              redirect_stdout(io.StringIO())):
            result = oracle.observe(args)
            forbidden_native.assert_not_called()
        return result, json.loads((output / "receipt.json").read_text()), lifetimes.call_args_list

    def test_unknown_save_with_matching_sql_never_reopens_or_replays(self):
        code, receipt, calls = self.run_synthetic_observation(
            {"ui": {"state": "UNKNOWN", "controller_complete": False}})
        self.assertEqual(code, 1)
        self.assertEqual(receipt["application_result"], "UNKNOWN")
        self.assertEqual(receipt["after_edit"]["semantic"]["document"]["title"], "Updated project")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("after_reopen", receipt)
        self.assertFalse(receipt["human_acceptance"])

    def test_known_ack_with_lost_custody_is_retained_but_cannot_reopen(self):
        code, receipt, calls = self.run_synthetic_observation({"custody_errors": ["synthetic"]})
        self.assertEqual(code, 1)
        self.assertEqual(receipt["application_result"], "ACK")
        self.assertEqual(len(calls), 1)
        self.assertEqual(receipt["status"], "INCOMPLETE")

    def test_complete_synthetic_observation_requires_two_distinct_lifetimes(self):
        code, receipt, calls = self.run_synthetic_observation()
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "SOURCE_BOUND_UPDATED_APP_OBSERVATIONS")
        self.assertEqual([call.args[2] for call in calls], ["edit", "observe"])
        self.assertTrue(receipt["invariants"]["read_only_reopen"])
        self.assertFalse(receipt["release_authorized"])
        self.assertEqual(receipt["installer_execution"], "NOT_RUN")




    def run_direct_synthetic_observation(self, fault):
        """Run the real lifetime; only OS boundaries and prior admission are fake."""
        product, gate_repo = self.root / "product", self.root / "gate"
        product.mkdir()
        gate_repo.mkdir()
        self.gate.source_observation = lambda _repo, sha, _paths: {
            "sha": sha, "tree": "c" * 40, "files": {}}
        self.gate.GATE_PATHS = ("tooling/canonical-gate.py",)
        self.gate.PRODUCT_PATHS = ("installer.nsi",)
        input_ref = self.ref("direct-input.json", self.value)
        output = self.root / "direct-observation"
        args = SimpleNamespace(output=output, evidence_root=self.evidence,
                               product_repo=product, gate_repo=gate_repo,
                               input=self.evidence / input_ref["path"],
                               input_sha256=input_ref["sha256"],
                               product_source="a" * 40, gate_source="b" * 40,
                               oracle_source="f" * 40)
        environment = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows",
                       "GITHUB_REPOSITORY": RUN["repository"], "GITHUB_RUN_ID": "12",
                       "GITHUB_RUN_ATTEMPT": "1"}
        _, after = snapshots()

        def saved_effect():
            with closing(sqlite3.connect(self.database)) as db, db:
                replace_db(db, after)

        boundary = SyntheticLifetimeBoundary(self.root, fault, saved_effect)
        original_lifetime = oracle.run_lifetime
        with (mock.patch.object(oracle, "os", SimpleNamespace(name="nt", environ=environment)),
              mock.patch.object(oracle, "load_canonical_gate", return_value=self.gate),
              mock.patch.object(oracle.life, "read_pe_versions", return_value={}),
              mock.patch.object(oracle.life, "require_pe_version"),
              mock.patch.object(oracle, "emit_predecessor_include", return_value={"enabled": True}),
              mock.patch.object(oracle.cold, "native_windows_launch_environment",
                                return_value=environment | boundary.environment),
              boundary.active(),
              mock.patch.object(oracle, "run_lifetime", wraps=original_lifetime) as lifetimes,
              redirect_stdout(io.StringIO())):
            result = oracle.observe(args)
        return (result, json.loads((output / "receipt.json").read_text()),
                lifetimes.call_args_list, boundary)

    def test_direct_unknown_controller_with_matching_sql_does_not_reopen(self):
        code, receipt, calls, boundary = self.run_direct_synthetic_observation("timeout-empty")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["application_result"], "UNKNOWN")
        self.assertEqual(receipt["after_edit"]["semantic"]["document"]["title"], "Updated project")
        self.assertEqual(receipt["after_edit"]["semantic"]["document"]["revision"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[2], "edit")
        self.assertEqual(boundary.application_launch.call_count, 1)
        self.assertEqual(boundary.controller_dispatch.call_count, 1)
        self.assertNotIn("after_reopen", receipt)
        self.assertFalse(receipt["human_acceptance"])
        self.assertFalse(receipt["release_authorized"])

    def test_direct_ack_cleanup_failure_preserves_result_and_blocks_reopen(self):
        code, receipt, calls, boundary = self.run_direct_synthetic_observation("cleanup-poll")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["application_result"], "ACK")
        self.assertEqual(receipt["status"], "INCOMPLETE")
        self.assertTrue(receipt["lifetimes"][0]["custody_errors"])
        self.assertEqual(receipt["after_edit"]["semantic"]["document"]["revision"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[2], "edit")
        self.assertEqual(boundary.application_launch.call_count, 1)
        self.assertEqual(boundary.controller_dispatch.call_count, 1)
        self.assertNotIn("after_reopen", receipt)
        self.assertFalse(receipt["human_acceptance"])
        self.assertFalse(receipt["release_authorized"])



class ProducerAdmissionContracts(unittest.TestCase):
    """Real profile/retained-file helpers; no native process or UI is executed."""

    def setUp(self):
        OriginalAdmissionContracts.setUp(self)
        self.profile_calls = 0
        previous_setup = self.ref("previous-setup.bin", b"synthetic retained previous package")
        previous_receipt = self.ref("previous-capture.json", {"fixture": "synthetic prior capture"})
        old = oracle.life.retained_ref(self.evidence, self.evidence / "old-main.bin")
        uninstaller = oracle.life.retained_ref(self.evidence, self.evidence / "old-uninstaller.bin")
        self.profile = {
            "schema": "motionwright.nsis-predecessor/1",
            "identity": {"application": "Motionwright",
                         "bundle_id": "com.seradotcom.motionwright", "arch": "x64"},
            "version": "0.1.0", "source_sha": "d" * 40, "source_tree": "e" * 40,
            "target": {"version": "0.1.1", "main_sha256": self.current["sha256"]},
            "package": previous_setup, "capture_receipt": previous_receipt,
            "files": [
                {"relative": self.exe.name, "source": old["path"], "kind": "main",
                 **oracle.life.ref_identity(old)},
                {"relative": "uninstall.exe", "source": uninstaller["path"], "kind": "uninstaller",
                 **oracle.life.ref_identity(uninstaller)},
            ],
        }
        self.value["refs"]["predecessor_profile"] = self.ref("complete-profile.json", self.profile)
        context = oracle.read_json(self.evidence, self.value["refs"]["interruption_context"])
        context["predecessor_profile_sha256"] = self.value["refs"]["predecessor_profile"]["sha256"]
        self.value["refs"]["interruption_context"] = self.ref("complete-context.json", context)

    def real_profile_admission(self, profile=None, expected_pin=None):
        self.profile_calls += 1
        profile = copy.deepcopy(self.profile if profile is None else profile)
        reference = self.ref("profile-helper-" + str(self.profile_calls) + ".json", profile)
        output = self.root / ("profile-helper-" + str(self.profile_calls))
        output.mkdir()
        result = oracle.emit_predecessor_include(
            output / "predecessor.nsh", application="Motionwright",
            bundle_id="com.seradotcom.motionwright", arch="x64",
            target_version="0.1.1", target_main_sha256=self.current["sha256"],
            environment={
                "MW_NSIS_PREDECESSOR_MANIFEST": str(self.evidence / reference["path"]),
                "MW_NSIS_PREDECESSOR_MANIFEST_SHA256": expected_pin or reference["sha256"],
            },
        )
        self.assertEqual(result, {
            "enabled": True, "files": 2, "manifest_sha256": reference["sha256"],
            "package_sha256": profile["package"]["sha256"],
        })
        self.assertEqual(
            hashlib.sha256((output / "mw-predecessor-manifest.capture.json").read_bytes()).hexdigest(),
            reference["sha256"],
        )
        return result

    def change_snapshot(self, role, suffix, mutate):
        value = oracle.read_json(self.evidence, self.value["refs"][role])
        mutate(value)
        self.value["refs"][role] = self.ref(role + "-" + suffix + ".json", value)

    def add_foreign(self):
        reference = self.ref("foreign-original.txt", b"Synthetic user data retained without ownership.\n")
        for role in ("before_snapshot", "recovered_snapshot"):
            self.change_snapshot(role, "foreign",
                                 lambda value: value["installed"].update({"user-created-foreign.txt": reference}))
        return reference

    def test_real_x64_profile_reaches_one_application_boundary(self):
        product, gate_repo = self.root / "product", self.root / "gate"
        product.mkdir()
        gate_repo.mkdir()
        self.gate.source_observation = lambda _repo, sha, _paths: {
            "sha": sha, "tree": "c" * 40, "files": {}}
        self.gate.GATE_PATHS = ("tooling/canonical-gate.py",)
        self.gate.PRODUCT_PATHS = ("installer.nsi",)
        reference = self.ref("real-profile-observer-input.json", self.value)
        output = self.root / "real-profile-observation"
        args = SimpleNamespace(
            output=output, evidence_root=self.evidence, product_repo=product, gate_repo=gate_repo,
            input=self.evidence / reference["path"], input_sha256=reference["sha256"],
            product_source="a" * 40, gate_source="b" * 40, oracle_source="f" * 40,
        )
        environment = {
            "GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows",
            "GITHUB_REPOSITORY": RUN["repository"], "GITHUB_RUN_ID": "12",
            "GITHUB_RUN_ATTEMPT": "1",
        }
        real_helper = oracle.emit_predecessor_include
        # Prior checkpoint/PE admission is synthetic. Profile admission itself
        # executes the original helper and captures its real input family.
        # Stop at the first application boundary, without manufacturing an ACK.
        with (mock.patch.object(oracle, "os", SimpleNamespace(name="nt", environ=environment)),
              mock.patch.object(oracle, "load_canonical_gate", return_value=self.gate),
              mock.patch.object(oracle.life, "read_pe_versions", return_value={}),
              mock.patch.object(oracle.life, "require_pe_version"),
              mock.patch.object(oracle, "emit_predecessor_include", wraps=real_helper) as profile_call,
              mock.patch.object(oracle.cold, "native_windows_launch_environment", return_value=environment),
              mock.patch.object(oracle, "run_lifetime",
                                side_effect=RuntimeError("synthetic application boundary")) as lifetime,
              mock.patch.object(oracle.subprocess, "Popen") as native,
              redirect_stdout(io.StringIO())):
            code = oracle.observe(args)
            native.assert_not_called()
        profile_call.assert_called_once()
        lifetime.assert_called_once()
        self.assertEqual(lifetime.call_args.args[1:3], ("first", "edit"))
        self.assertEqual(code, 1)
        receipt = json.loads((output / "receipt.json").read_bytes())
        self.assertEqual(receipt["status"], "INCOMPLETE")
        self.assertEqual(receipt["application_result"], "NOT_STARTED")
        self.assertEqual(receipt["lifetimes"], [])
        self.assertEqual(receipt["diagnostic"], "RuntimeError")
        admitted = json.loads((output / "admitted-input.json").read_bytes())
        self.assertEqual(admitted["profile"]["enabled"], True)
        self.assertEqual(admitted["profile"]["files"], 2)
        self.assertEqual(admitted["profile"]["manifest_sha256"],
                         self.value["refs"]["predecessor_profile"]["sha256"])
        self.assertFalse(admitted["human_acceptance"])

    def test_complete_owned_and_foreign_originals_are_admitted(self):
        foreign = self.add_foreign()
        self.real_profile_admission()
        originals = {role: (self.evidence / self.value["refs"][role]["path"]).read_bytes()
                     for role in ("before_snapshot", "recovered_snapshot")}
        try:
            admitted = oracle.admit_originals(self.evidence, self.value, self.gate)
        except oracle.OracleError as error:
            self.fail("Complete producer snapshots with preserved foreign bytes were refused: " + str(error))
        for key, role in (("before", "before_snapshot"), ("recovered", "recovered_snapshot")):
            self.assertEqual(len(admitted[key]["installed"]), 3)
            self.assertEqual(oracle.life.file_map(admitted[key])["user-created-foreign.txt"],
                             oracle.life.ref_identity(foreign))
            self.assertEqual((self.evidence / self.value["refs"][role]["path"]).read_bytes(),
                             originals[role])
        self.assertNotIn("user-created-foreign.txt", {row["relative"] for row in self.profile["files"]})

    def test_modified_owned_original_is_refused(self):
        self.real_profile_admission()
        changed = self.ref("changed-owned.bin", b"synthetic modified predecessor main")
        self.change_snapshot("before_snapshot", "owned-changed",
                             lambda value: value["installed"].update({self.exe.name: changed}))
        with self.assertRaisesRegex(oracle.OracleError, "predecessor_inventory_not_original_snapshot"):
            oracle.admit_originals(self.evidence, self.value, self.gate)

    def test_missing_owned_original_is_refused(self):
        self.real_profile_admission()
        self.change_snapshot("before_snapshot", "owned-missing",
                             lambda value: value["installed"].pop("uninstall.exe"))
        with self.assertRaisesRegex(oracle.OracleError, "predecessor_inventory_not_original_snapshot"):
            oracle.admit_originals(self.evidence, self.value, self.gate)

    def test_modified_foreign_original_is_refused(self):
        self.add_foreign()
        self.real_profile_admission()
        changed = self.ref("changed-foreign.txt", b"synthetic changed user data")
        self.change_snapshot("recovered_snapshot", "foreign-changed",
                             lambda value: value["installed"].update({"user-created-foreign.txt": changed}))
        with self.assertRaises(oracle.OracleError):
            oracle.admit_originals(self.evidence, self.value, self.gate)

    def test_missing_foreign_original_is_refused(self):
        self.add_foreign()
        self.real_profile_admission()
        self.change_snapshot("recovered_snapshot", "foreign-missing",
                             lambda value: value["installed"].pop("user-created-foreign.txt"))
        with self.assertRaises(oracle.OracleError):
            oracle.admit_originals(self.evidence, self.value, self.gate)

    def test_original_profile_helper_rejects_invalid_authority_and_paths(self):
        for fault in ("architecture", "pin", "duplicate", "case-alias", "source-escape", "missing-sibling"):
            with self.subTest(fault=fault):
                profile = copy.deepcopy(self.profile)
                pin = None
                if fault == "architecture":
                    profile["identity"]["arch"] = "x86_64"
                elif fault == "pin":
                    pin = "0" * 64
                elif fault == "duplicate":
                    profile["files"].append(copy.deepcopy(profile["files"][0]))
                elif fault == "case-alias":
                    duplicate = copy.deepcopy(profile["files"][0])
                    duplicate["relative"] = duplicate["relative"].upper()
                    profile["files"].append(duplicate)
                elif fault == "source-escape":
                    profile["files"][0]["source"] = "../old-main.bin"
                else:
                    profile["package"]["path"] = "missing-previous-package.bin"
                with self.assertRaises((owned.CaptureError, FileNotFoundError)):
                    self.real_profile_admission(profile, pin)

    def test_original_snapshot_helper_rejects_case_aliases(self):
        self.real_profile_admission()
        for role in ("before_snapshot", "recovered_snapshot"):
            with self.subTest(role=role):
                original = self.value["refs"][role]
                self.change_snapshot(role, "case-alias", lambda value: value["installed"].update({
                    self.exe.name.upper(): value["installed"][self.exe.name]}))
                try:
                    with self.assertRaisesRegex(oracle.life.LifecycleError, "snapshot_inventory_alias"):
                        oracle.admit_originals(self.evidence, self.value, self.gate)
                finally:
                    self.value["refs"][role] = original


if __name__ == "__main__":
    unittest.main()
