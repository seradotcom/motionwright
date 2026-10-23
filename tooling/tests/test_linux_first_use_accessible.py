"""Synthetic selection/latch regressions. These are NOT Linux GUI acceptance."""
from __future__ import annotations

from pathlib import Path
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_first_use_accessible as probe
import storage_schema_contract as storage

BINDING = {"window": "0x10", "pid": 99, "start_time": "123456"}


class Node:
    def __init__(self, role, name, *, states=probe.USABLE, interfaces=(), pid=99):
        self.role, self.name, self.pid = role, name, pid
        self.states, self.interfaces = set(states), set(interfaces)
        self.children = []
        self.text = ""


class Journal:
    """An exclusive durable-latch model, not a filesystem durability proof."""
    def __init__(self):
        self.entries = {}
        self.order = []
        self.fail = None

    def put(self, name, value):
        if name in self.entries:
            raise FileExistsError(name)
        if name == self.fail:
            raise OSError("synthetic journal failure")
        self.entries[name] = value
        self.order.append(name)


class Bridge:
    def __init__(self, journal):
        self.journal = journal
        self.app = Node("application", "Motionwright")
        self.dialog = Node("dialog", "Project library")
        self.entry = Node("entry", "Project title",
                          states=probe.USABLE | {"editable"},
                          interfaces=("EditableText", "Text"))
        self.button = Node("button", "Create project", interfaces=("Action",),
                           states=probe.VISIBLE)
        self.app.children = [self.dialog]
        self.dialog.children = [self.entry, self.button]
        self.apps = [self.app]
        self.edits, self.clicks = 0, 0
        self.names = ["click"]
        self.set_result, self.action_result = True, True
        self.set_error = self.action_error = None
        self.update_text = self.enable_button = True

    def applications(self):
        return self.apps

    def pid(self, node):
        return node.pid

    def owner_pid(self, node):
        return node.pid

    def children(self, node):
        return node.children

    def view(self, node):
        return probe.View(node, node.role, node.name,
                          frozenset(node.states), frozenset(node.interfaces))

    def read_text(self, node):
        return node.text

    def set_text(self, node, value):
        assert "edit-intent" in self.journal.entries
        self.edits += 1
        if self.update_text:
            node.text = value
        if self.enable_button:
            self.button.states |= probe.USABLE
        if self.set_error:
            raise self.set_error
        return self.set_result

    def actions(self, node):
        return self.names

    def invoke(self, node, index):
        assert "create-intent" in self.journal.entries
        assert self.entry.text == probe.EXPECTED_INITIAL_TITLE
        assert probe.USABLE <= node.states
        assert self.names[index] in ("click", "press")
        self.clicks += 1
        if self.action_error:
            raise self.action_error
        return self.action_result


class SelectionAndAttemptTests(unittest.TestCase):
    def setUp(self):
        self.journal = Journal()
        self.bridge = Bridge(self.journal)
        self.identity = dict(BINDING)
        self.attempt = probe.Attempt(
            self.bridge, lambda: dict(self.identity), self.journal, dict(BINDING))

    def rejected_without_effect(self, code, exception=probe.ProbeError):
        with self.assertRaisesRegex(exception, code):
            self.attempt.step()
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (0, 0))

    def edit_once(self):
        self.assertFalse(self.attempt.step())
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (1, 0))

    def test_exact_target_one_edit_one_action_and_no_commit_claim(self):
        self.edit_once()
        self.assertTrue(self.attempt.step())
        for _ in range(10):
            self.assertTrue(self.attempt.step())
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (1, 1))
        self.assertEqual(self.journal.order, [
            "worker-start", "edit-intent", "edit-return",
            "create-intent", "create-return",
        ])
        report = self.attempt.report("ACTION_RETURNED_TRUE")
        self.assertEqual(report["status"], "ACTION_ACCEPTED")
        self.assertEqual(report["project_commit"], "NOT_VERIFIED_BY_ACTION")

    def test_same_title_other_application_has_no_authority(self):
        self.bridge.app.pid = 100
        self.rejected_without_effect("MISSING_APPLICATION", probe.NotReady)

    def test_duplicate_applications_never_select_first(self):
        self.bridge.apps.append(Node("application", "Motionwright"))
        self.rejected_without_effect("AMBIGUOUS_APPLICATION")

    def test_duplicate_dialogs_never_select_first(self):
        self.bridge.app.children.append(Node("dialog", "Project library"))
        self.rejected_without_effect("AMBIGUOUS_DIALOG")

    def test_duplicate_inputs_never_select_first(self):
        self.bridge.dialog.children.append(Node("entry", "Project title"))
        self.rejected_without_effect("AMBIGUOUS_TITLE")

    def test_duplicate_buttons_never_select_enabled_one(self):
        self.bridge.dialog.children.append(Node("button", "Create project"))
        self.rejected_without_effect("AMBIGUOUS_CREATE")

    def test_wrong_label_cannot_be_used_as_input(self):
        self.bridge.entry.name = "Project title for export"
        self.rejected_without_effect("MISSING_TITLE", probe.NotReady)

    def test_hidden_input_cannot_be_used(self):
        self.bridge.entry.states.remove("showing")
        self.rejected_without_effect("MISSING_TITLE", probe.NotReady)

    def test_control_from_another_application_is_blocked(self):
        self.bridge.entry.pid = 100
        self.rejected_without_effect("CONTROL_APPLICATION_PID_MISMATCH")

    def test_input_must_be_editable_and_have_both_interfaces(self):
        self.bridge.entry.states.remove("editable")
        self.rejected_without_effect("TITLE_NOT_EDITABLE", probe.NotReady)
        self.bridge.entry.states.add("editable")
        self.bridge.entry.interfaces.remove("EditableText")
        self.rejected_without_effect("TITLE_INTERFACES_UNAVAILABLE")

    def test_existing_input_text_is_not_overwritten(self):
        self.bridge.entry.text = "owner data"
        self.rejected_without_effect("INITIAL_TITLE_NOT_EMPTY")

    def test_successful_setter_without_observed_text_never_creates(self):
        self.bridge.update_text = False
        self.edit_once()
        for _ in range(3):
            with self.assertRaisesRegex(probe.NotReady, "TITLE_NOT_VERIFIED"):
                self.attempt.step()
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (1, 0))
        self.assertNotIn("create-intent", self.journal.entries)
        self.assertEqual(self.attempt.report("DEADLINE")["status"], "UNKNOWN")

    def test_disabled_create_waits_for_real_state_without_reediting(self):
        self.bridge.enable_button = False
        self.edit_once()
        with self.assertRaisesRegex(probe.NotReady, "CREATE_NOT_ENABLED"):
            self.attempt.step()
        self.assertEqual(self.bridge.clicks, 0)
        self.bridge.button.states |= probe.USABLE
        self.assertTrue(self.attempt.step())
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (1, 1))

    def test_pid_reuse_before_edit_is_rejected(self):
        self.identity["start_time"] = "123457"
        self.rejected_without_effect("WINDOW_PROCESS_IDENTITY_CHANGED")

    def test_identity_is_rechecked_after_selection_before_edit(self):
        sequence = iter([dict(BINDING), {**BINDING, "start_time": "new"}])
        self.attempt.identity = lambda: next(sequence)
        self.rejected_without_effect("WINDOW_PROCESS_IDENTITY_CHANGED")

    def test_pid_reuse_after_edit_never_clicks(self):
        self.edit_once()
        self.identity["start_time"] = "123457"
        with self.assertRaisesRegex(probe.ProbeError, "WINDOW_PROCESS_IDENTITY_CHANGED"):
            self.attempt.step()
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (1, 0))

    def test_replaced_input_with_same_label_and_text_cannot_inherit_intent(self):
        self.edit_once()
        old = self.bridge.entry
        new = Node(old.role, old.name, states=old.states, interfaces=old.interfaces)
        new.text = old.text
        self.bridge.dialog.children[0] = new
        with self.assertRaisesRegex(probe.ProbeError, "CONTROLS_REPLACED_AFTER_EDIT"):
            self.attempt.step()
        self.assertEqual(self.bridge.clicks, 0)

    def test_ambiguous_activation_action_is_refused(self):
        self.edit_once()
        self.bridge.names = ["click", "press"]
        with self.assertRaisesRegex(probe.ProbeError, "CREATE_ACTION_NOT_UNIQUE"):
            self.attempt.step()
        self.assertEqual(self.bridge.clicks, 0)

    def test_unrecognized_button_action_is_refused(self):
        self.edit_once()
        self.bridge.names = ["toggle"]
        with self.assertRaisesRegex(probe.ProbeError, "CREATE_ACTION_NOT_UNIQUE"):
            self.attempt.step()
        self.assertEqual(self.bridge.clicks, 0)

    def test_setter_timeout_cannot_be_retried(self):
        self.bridge.set_error = TimeoutError("synthetic lost reply")
        with self.assertRaises(TimeoutError):
            self.attempt.step()
        with self.assertRaisesRegex(probe.ProbeError, "MUTATION_ALREADY_ATTEMPTED"):
            self.attempt.step()
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (1, 0))
        self.assertEqual(self.attempt.report("TIMEOUT")["status"], "UNKNOWN")

    def test_action_timeout_cannot_be_retried(self):
        self.edit_once()
        self.bridge.action_error = TimeoutError("synthetic lost reply")
        with self.assertRaises(TimeoutError):
            self.attempt.step()
        with self.assertRaisesRegex(probe.ProbeError, "MUTATION_ALREADY_ATTEMPTED"):
            self.attempt.step()
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (1, 1))
        self.assertEqual(self.attempt.report("TIMEOUT")["phase"], "CREATE_UNKNOWN")

    def test_false_setter_is_not_no_effect_permission_to_retry(self):
        self.bridge.set_result = False
        with self.assertRaisesRegex(probe.ProbeError, "EDIT_RETURNED_FALSE_UNCONFIRMED"):
            self.attempt.step()
        with self.assertRaisesRegex(probe.ProbeError, "MUTATION_ALREADY_ATTEMPTED"):
            self.attempt.step()
        self.assertEqual(self.bridge.edits, 1)
        self.assertEqual(self.bridge.clicks, 0)

    def test_false_action_is_unconfirmed_and_never_retried(self):
        self.edit_once()
        self.bridge.action_result = False
        with self.assertRaisesRegex(probe.ProbeError, "CREATE_RETURNED_FALSE_UNCONFIRMED"):
            self.attempt.step()
        with self.assertRaisesRegex(probe.ProbeError, "MUTATION_ALREADY_ATTEMPTED"):
            self.attempt.step()
        self.assertEqual(self.bridge.clicks, 1)
        self.assertEqual(self.attempt.report("RETURN_FALSE")["status"], "UNKNOWN")

    def test_edit_journal_failure_prevents_effect(self):
        self.journal.fail = "edit-intent"
        with self.assertRaises(OSError):
            self.attempt.step()
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (0, 0))

    def test_create_journal_failure_prevents_button_effect(self):
        self.edit_once()
        self.journal.fail = "create-intent"
        with self.assertRaises(OSError):
            self.attempt.step()
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (1, 0))

    def test_restart_on_same_journal_is_refused_even_with_new_binding(self):
        self.edit_once()
        with self.assertRaises(FileExistsError):
            probe.Attempt(self.bridge, lambda: BINDING, self.journal,
                          {**BINDING, "start_time": "different"})
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (1, 0))

    def test_expired_deadline_cannot_send_edit(self):
        self.attempt.deadline = 10
        with patch.object(probe.time, "monotonic", return_value=11):
            self.rejected_without_effect("DEADLINE")

    def test_expired_deadline_after_edit_cannot_send_create(self):
        self.edit_once()
        self.attempt.deadline = 10
        with patch.object(probe.time, "monotonic", return_value=11):
            with self.assertRaisesRegex(probe.ProbeError, "DEADLINE"):
                self.attempt.step()
        self.assertEqual((self.bridge.edits, self.bridge.clicks), (1, 0))

    def test_tree_limit_prevents_unbounded_cycle_or_partial_selection(self):
        self.bridge.dialog.children.append(self.bridge.dialog)
        self.rejected_without_effect("ACCESSIBLE_TREE_LIMIT")

    def test_tree_read_error_does_not_hide_other_matches(self):
        original = self.bridge.children
        def children(node):
            if node is self.bridge.button:
                raise RuntimeError("synthetic unavailable subtree")
            return original(node)
        self.bridge.children = children
        self.rejected_without_effect("synthetic unavailable subtree", RuntimeError)


class X11IdentityTests(unittest.TestCase):
    def identity(self, window_text, prop_text):
        outputs = [SimpleNamespace(returncode=0, stdout=window_text),
                   SimpleNamespace(returncode=0, stdout=prop_text)]
        stat_text = "99 (name with ) brackets) " + " ".join(
            ["S"] + ["0"] * 18 + ["123456"] + ["0"] * 4)
        with patch.object(probe.subprocess, "run", side_effect=outputs), \
             patch.object(probe.Path, "stat", return_value=SimpleNamespace(st_uid=7)), \
             patch.object(probe.Path, "read_text", return_value=stat_text), \
             patch.object(probe.os, "getuid", return_value=7, create=True):
            return probe.x11_identity()

    def test_pid_and_start_time_are_both_bound(self):
        result = self.identity('  0x10 "Motionwright": ("a" "b")\n',
                               "_NET_WM_PID(CARDINAL) = 99\n")
        self.assertEqual(result, BINDING)

    def test_duplicate_window_titles_are_ambiguous(self):
        with self.assertRaisesRegex(probe.ProbeError, "AMBIGUOUS_X11_WINDOW"):
            self.identity('0x10 "Motionwright":\n0x11 "Motionwright":\n', "")

    def test_pid_absence_or_zero_is_not_title_authority(self):
        for prop in ("_NET_WM_PID: not found\n", "_NET_WM_PID(CARDINAL) = 0\n"):
            with self.subTest(prop=prop):
                with self.assertRaisesRegex(probe.NotReady, "X11_PID_UNAVAILABLE"):
                    self.identity('0x10 "Motionwright":\n', prop)


class ColdOracleIntegrationContractTests(unittest.TestCase):
    def test_reuses_same_data_and_real_oracles_with_no_second_interaction(self):
        with tempfile.TemporaryDirectory() as root:
            scratch = Path(root) / "fresh"
            app_run = Path(root) / "AppRun"
            calls = []
            def fake_probe(app, data, **kwargs):
                self.assertEqual(app, app_run)
                self.assertEqual(data, scratch.resolve())
                self.assertIs(kwargs["ready_check"], probe._bootstrap_committed)
                if not calls:
                    callback = kwargs["window_interaction"]
                    self.assertTrue(callback(data))
                else:
                    self.assertNotIn("window_interaction", kwargs)
                calls.append(kwargs)
                (data / "app-run.log").write_text("synthetic launch\n", encoding="utf-8")
                return {"status": "WINDOW_OBSERVED"}
            with patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true", "DISPLAY": ":synthetic",
                "DBUS_SESSION_BUS_ADDRESS": "synthetic", "RUNNER_TEMP": root,
            }), patch.object(probe.sys, "platform", "linux"), \
                 patch.object(probe, "run_probe", side_effect=fake_probe), \
                 patch.object(probe, "_empty_library_ready", return_value=True), \
                 patch.object(probe, "x11_identity", return_value=dict(BINDING)), \
                 patch.object(probe, "invoke_worker", return_value={"status": "ACTION_ACCEPTED"}) as action, \
                 patch.object(probe, "read_snapshot", side_effect=["first", "second"]) as reads, \
                 patch.object(probe, "attest_persistence", return_value={"synthetic": True}) as attest:
                result = probe.probe_accessible_cold_relaunch(
                    app_run=app_run, scratch=scratch, source_sha="a" * 40,
                    semwright_sha="b" * 40, seconds=24)
            self.assertEqual(action.call_count, 1)
            self.assertEqual(action.call_args.kwargs["binding"], BINDING)
            self.assertEqual([c.args[0] for c in reads.call_args_list],
                             [scratch.resolve() / "data"] * 2)
            attest.assert_called_once_with("first", "second")
            self.assertEqual(result["launch_count"], 2)
            self.assertEqual(result["claims"]["human_install_acceptance"], "NOT_RUN")
            self.assertTrue((scratch / "app-run-first.log").is_file())
            self.assertTrue((scratch / "app-run-second.log").is_file())

    def test_worker_timeout_stops_without_relaunch_or_fresh_retry(self):
        with tempfile.TemporaryDirectory() as root:
            scratch = Path(root) / "fresh"
            calls = []
            def fake_probe(app, data, **kwargs):
                calls.append(kwargs)
                return kwargs["window_interaction"](data)
            with patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true", "DISPLAY": ":synthetic",
                "DBUS_SESSION_BUS_ADDRESS": "synthetic", "RUNNER_TEMP": root,
            }), patch.object(probe.sys, "platform", "linux"), \
                 patch.object(probe, "run_probe", side_effect=fake_probe), \
                 patch.object(probe, "_empty_library_ready", return_value=True), \
                 patch.object(probe, "x11_identity", return_value=dict(BINDING)), \
                 patch.object(probe, "invoke_worker", side_effect=probe.ProbeError(
                     "ACCESSIBLE_WORKER_TIMEOUT_UNKNOWN")) as action, \
                 patch.object(probe, "read_snapshot") as reads:
                arguments = dict(app_run=Path(root) / "AppRun", scratch=scratch,
                                 source_sha="a" * 40, semwright_sha="b" * 40)
                with self.assertRaisesRegex(probe.ProbeError, "TIMEOUT_UNKNOWN"):
                    probe.probe_accessible_cold_relaunch(**arguments)
                with self.assertRaisesRegex(probe.ColdStartError, "must be new"):
                    probe.probe_accessible_cold_relaunch(**arguments)
            self.assertEqual(len(calls), 1)
            action.assert_called_once()
            reads.assert_not_called()

    def test_existing_scratch_is_preserved_without_launch(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "original"
            path.mkdir()
            receipt = path / "evidence.original"
            receipt.write_bytes(b"unchanged original")
            with patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true", "DISPLAY": ":synthetic",
                "DBUS_SESSION_BUS_ADDRESS": "synthetic", "RUNNER_TEMP": root,
            }), patch.object(probe.sys, "platform", "linux"), \
                 patch.object(probe, "run_probe") as launch:
                with self.assertRaisesRegex(probe.ColdStartError, "must be new"):
                    probe.probe_accessible_cold_relaunch(
                        app_run=Path(root) / "AppRun", scratch=path,
                        source_sha="a" * 40, semwright_sha="b" * 40)
            launch.assert_not_called()
            self.assertEqual(receipt.read_bytes(), b"unchanged original")


@unittest.skipUnless(os.name == "posix", "Linux dirfd journal; mocks are not durability proof")
class JournalFilesystemTests(unittest.TestCase):
    def test_exclusive_entries_keep_original_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            journal = probe.DiskJournal(root)
            try:
                journal.put("create-intent", {"intent": 1})
                original = (Path(root) / "create-intent.json").read_bytes()
                with self.assertRaises(FileExistsError):
                    journal.put("create-intent", {"intent": 2})
                self.assertEqual(journal.read("create-intent"), {"intent": 1})
                self.assertEqual((Path(root) / "create-intent.json").read_bytes(), original)
            finally:
                journal.close()



class SourceCohortIntegrationTests(unittest.TestCase):
    """Synthetic SQLite source-cohort coverage; no installed GUI is launched."""

    @staticmethod
    def cohort():
        raw = b"pub const STORAGE_SCHEMA_VERSION: u32 = 4;\n"
        identity = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        observation = {"sha": "a" * 40, "tree": "b" * 40, "files": {
            storage.SOURCE_PATH: {
                "git_bytes": len(raw), "git_sha256": identity["sha256"],
                "checkout": identity, "checkout_encoding": "EXACT_GIT_BYTES",
            },
        }}
        return storage.admit_source(observation, raw)

    @staticmethod
    def fixture_database(scratch):
        data = scratch / "data"
        data.mkdir(parents=True)
        database = data / "motionwright.sqlite3"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.executescript("""
                PRAGMA user_version=4;
                CREATE TABLE projects(
                    id TEXT, generation TEXT, revision INTEGER,
                    title TEXT, document_json TEXT);
                CREATE TABLE events(event_id INTEGER);
                CREATE TABLE production_receipts(receipt_id TEXT);
                CREATE TABLE receipts(
                    project_id TEXT, request_id TEXT, request_sha256 TEXT,
                    result_json TEXT, created_at TEXT,
                    apply_binding_version INTEGER NOT NULL DEFAULT 0,
                    apply_binding_json TEXT);
            """)
        return database

    @staticmethod
    def commit_fixture_project(database):
        document = {
            "schema_version": 2,
            "id": "018f0000-0000-7000-8000-000000000001",
            "generation": "018f0000-0000-7000-8000-000000000002",
            "revision": 0, "title": probe.EXPECTED_INITIAL_TITLE,
        }
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (
                document["id"], document["generation"], document["revision"],
                document["title"], json.dumps(document),
            ))

    def test_shared_oracle_requires_explicit_current_source_cohort(self):
        with tempfile.TemporaryDirectory() as root:
            scratch = Path(root) / "original"
            database = self.fixture_database(scratch)
            cohort = self.cohort()
            self.assertFalse(probe._empty_library_ready(scratch))
            self.assertTrue(probe._empty_library_ready(scratch, storage_cohort=cohort))
            self.commit_fixture_project(database)
            self.assertFalse(probe._bootstrap_committed(scratch))
            self.assertTrue(probe._bootstrap_committed(scratch, storage_cohort=cohort))
            with self.assertRaisesRegex(probe.ColdStartError, "supported native schema"):
                probe.read_snapshot(scratch / "data")
            self.assertEqual(probe.read_snapshot(
                scratch / "data", storage_cohort=cohort).schema_version, 4)

    def test_explicit_cohort_reaches_readiness_and_both_real_sqlite_snapshots(self):
        with tempfile.TemporaryDirectory() as root:
            scratch = Path(root) / "fresh"
            cohort = self.cohort()
            calls = []
            def fake_probe(app, data, **kwargs):
                self.assertEqual(data, scratch.resolve())
                if not calls:
                    database = self.fixture_database(data)
                    self.assertFalse(kwargs["ready_check"](data))
                    self.assertTrue(kwargs["window_interaction"](data))
                    self.commit_fixture_project(database)
                else:
                    self.assertNotIn("window_interaction", kwargs)
                self.assertTrue(kwargs["ready_check"](data))
                calls.append(kwargs)
                (data / "app-run.log").write_text("synthetic launch\n", encoding="utf-8")
                return {"status": "WINDOW_OBSERVED"}
            with patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true", "DISPLAY": ":synthetic",
                "DBUS_SESSION_BUS_ADDRESS": "synthetic", "RUNNER_TEMP": root,
            }), patch.object(probe.sys, "platform", "linux"), \
                 patch.object(probe, "run_probe", side_effect=fake_probe), \
                 patch.object(probe, "x11_identity", return_value=dict(BINDING)), \
                 patch.object(probe, "invoke_worker", return_value={
                     "status": "ACTION_ACCEPTED"}) as action:
                result = probe.probe_accessible_cold_relaunch(
                    app_run=Path(root) / "AppRun", scratch=scratch,
                    source_sha="a" * 40, semwright_sha="c" * 40,
                    storage_cohort=cohort,
                )
            self.assertEqual(len(calls), 2)
            action.assert_called_once()
            self.assertEqual(result["schema"], "motionwright.ci.installed-cold-relaunch/2")
            self.assertEqual(result["storage_cohort"], cohort)
            self.assertEqual(result["persistence"]["schema_version"], 4)
            self.assertEqual(result["persistence"]["status"], "PASS")
            self.assertEqual(result["claims"]["human_install_acceptance"], "NOT_RUN")

    def test_foreign_source_cohort_is_rejected_before_launch_or_scratch(self):
        with tempfile.TemporaryDirectory() as root:
            scratch = Path(root) / "not-created"
            with patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true", "DISPLAY": ":synthetic",
                "DBUS_SESSION_BUS_ADDRESS": "synthetic", "RUNNER_TEMP": root,
            }), patch.object(probe.sys, "platform", "linux"), \
                 patch.object(probe, "run_probe") as launch:
                with self.assertRaisesRegex(
                        storage.StorageContractError, "storage_cohort_source_mismatch"):
                    probe.probe_accessible_cold_relaunch(
                        app_run=Path(root) / "AppRun", scratch=scratch,
                        source_sha="d" * 40, semwright_sha="c" * 40,
                        storage_cohort=self.cohort(),
                    )
            launch.assert_not_called()
            self.assertFalse(scratch.exists())

    def test_cli_admits_source_before_probe_and_rechecks_before_publication(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            output = root / "result.json"
            source = root / "source"
            cohort = self.cohort()
            events = []
            def admit(repo, sha):
                self.assertEqual((repo, sha), (source, "a" * 40))
                events.append("admit")
                self.assertFalse(output.exists())
                return cohort
            def probe_call(**kwargs):
                events.append("probe")
                self.assertEqual(kwargs["storage_cohort"], cohort)
                return {"schema": "synthetic-unit-only", "status": "fixture"}
            argv = ["probe", "--scratch", str(root / "scratch"),
                    "--app-run", str(root / "AppRun"), "--source-sha", "a" * 40,
                    "--semwright-sha", "c" * 40, "--output", str(output),
                    "--storage-source-repo", str(source)]
            with patch.object(sys, "argv", argv), \
                 patch.object(storage, "admit_checkout", side_effect=admit), \
                 patch.object(probe, "probe_accessible_cold_relaunch", side_effect=probe_call):
                probe.main()
            self.assertEqual(events, ["admit", "probe", "admit"])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "fixture")

    def test_cli_source_drift_retains_originals_and_does_not_publish(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            output = root / "result.json"
            original = root / "original.evidence"
            original.write_bytes(b"unchanged original attempt")
            cohort = self.cohort()
            changed = {**cohort, "source_tree": "e" * 40}
            argv = ["probe", "--scratch", str(root / "scratch"),
                    "--app-run", str(root / "AppRun"), "--source-sha", "a" * 40,
                    "--semwright-sha", "c" * 40, "--output", str(output),
                    "--storage-source-repo", str(root / "source")]
            with patch.object(sys, "argv", argv), \
                 patch.object(storage, "admit_checkout", side_effect=[cohort, changed]), \
                 patch.object(probe, "probe_accessible_cold_relaunch",
                              return_value={"status": "synthetic-unit-only"}):
                with self.assertRaisesRegex(probe.ColdStartError, "Storage source changed"):
                    probe.main()
            self.assertFalse(output.exists())
            self.assertEqual(original.read_bytes(), b"unchanged original attempt")


if __name__ == "__main__":
    unittest.main()
