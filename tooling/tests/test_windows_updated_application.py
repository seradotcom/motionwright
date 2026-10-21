"""Synthetic contract controls; no installer, installed app or native UI runs."""
from __future__ import annotations

import copy
import hashlib
import io
from contextlib import closing, redirect_stdout
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
        self.assertEqual(self.classify(ui_record(), timeout=True)["state"], "UNKNOWN")

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



if __name__ == "__main__":
    unittest.main()
