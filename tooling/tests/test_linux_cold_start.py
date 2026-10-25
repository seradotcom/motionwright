"""Adversarial read-only cold/relaunch probe policy tests (no heavy GUI)."""
from __future__ import annotations

import hashlib
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
sys.path.insert(0, str(TOOLING / "tests"))
from test_storage_schema_contract import add_schema_five_cas, cohort as storage_cohort_fixture
SPEC = importlib.util.spec_from_file_location(
    "linux_cold_start", TOOLING / "linux_cold_start.py"
)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)



def schema_four_store(root):
    database = seeded_store(root)
    with closing(sqlite3.connect(database)) as db, db:
        document = json.loads(db.execute("SELECT document_json FROM projects").fetchone()[0])
        document["schema_version"] = 2
        db.execute("UPDATE projects SET document_json=?", (json.dumps(document),))
        db.executescript("""
          PRAGMA user_version=4;
          CREATE TABLE receipts(project_id TEXT,request_id TEXT,request_sha256 TEXT,
            result_json TEXT,created_at TEXT,
            apply_binding_version INTEGER NOT NULL DEFAULT 0 CHECK(apply_binding_version IN (0,1)),
            apply_binding_json TEXT);
        """)
    return database

def schema_five_store(root):
    database = schema_four_store(root)
    with closing(sqlite3.connect(database)) as connection, connection:
        add_schema_five_cas(connection)
    return database

def seeded_store(root: Path, *, name: str = "motionwright ci first use") -> Path:
    data = root / "data" / "com.seradotcom.motionwright"
    data.mkdir(parents=True, exist_ok=True)
    database = data / "motionwright.sqlite3"
    project = str(uuid.uuid4())
    generation = str(uuid.uuid4())
    revision = 0
    document = json.dumps({
        "id": project,
        "generation": generation,
        "revision": revision,
        "title": name,
        "scenes": [],
    }, separators=(",", ":"))
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executescript("""
          PRAGMA user_version=3;
          CREATE TABLE projects(
            id TEXT PRIMARY KEY, generation TEXT NOT NULL,
            revision INTEGER NOT NULL, title TEXT NOT NULL,
            document_json TEXT NOT NULL
          );
          CREATE TABLE events(event_id INTEGER PRIMARY KEY);
          CREATE TABLE production_receipts(receipt_id TEXT PRIMARY KEY);
        """)
        conn.execute(
            "INSERT INTO projects(id,generation,revision,title,document_json) VALUES(?,?,?,?,?)",
            (project, generation, revision, name, document),
        )
        conn.commit()
    return database


class InstalledColdStartEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.scratch = self.work / "isolated"

    def test_read_only_snapshot_from_real_sqlite_tracks_original_domain_document(self):
        db = seeded_store(self.scratch)
        before = db.read_bytes()
        snapshot = probe.read_snapshot(self.scratch / "data")
        self.assertEqual(snapshot.schema_version, 3)
        self.assertEqual(snapshot.revision, 0)
        self.assertEqual(snapshot.title, "motionwright ci first use")
        self.assertEqual(len(snapshot.document_sha256), 64)
        self.assertEqual(db.read_bytes(), before, "read-only inspector must not rewrite SQLite")
        self.assertEqual(probe.attest_persistence(snapshot, snapshot)["status"], "PASS")

    def test_empty_installed_store_is_ready_for_one_real_ui_action(self):
        self.scratch.mkdir()
        self.assertFalse(probe._empty_library_ready(self.scratch))
        database = seeded_store(self.scratch)
        self.assertFalse(probe._empty_library_ready(self.scratch))
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM projects")
        self.assertTrue(probe._empty_library_ready(self.scratch))
        self.assertFalse(probe._bootstrap_committed(self.scratch))
        for version in (2, 4, 5):
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("PRAGMA user_version=" + str(version))
            with self.subTest(schema=version):
                self.assertFalse(probe._empty_library_ready(self.scratch))

    def test_readiness_requires_complete_committed_project_after_x11_window(self):
        self.scratch.mkdir()
        self.assertFalse(probe._bootstrap_committed(self.scratch))
        database = seeded_store(self.scratch)
        self.assertTrue(probe._bootstrap_committed(self.scratch))
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("DELETE FROM projects")
        self.assertFalse(probe._bootstrap_committed(self.scratch))

    def test_second_launch_on_identical_persisted_project_is_accepted(self):
        calls = []
        def fake_native_gui(app_run, scratch, *, seconds, ready_check, window_interaction=None):
            calls.append((app_run, scratch, seconds))
            scratch.mkdir(exist_ok=True)
            if len(calls) == 1:
                self.assertIsNotNone(window_interaction)
                seeded_store(scratch)
            else:
                self.assertIsNone(window_interaction)
            self.assertTrue(ready_check(scratch), "window alone is not persistence proof")
            return {
                "status": "WINDOW_OBSERVED",
                "window_title": "Motionwright",
                "elapsed_seconds": 0.5,
            }
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(
            probe, "run_probe", side_effect=fake_native_gui,
        ):
            result = probe.probe_cold_relaunch(
                app_run=self.work / "AppRun", scratch=self.scratch,
                source_sha="a" * 40, semwright_sha="b" * 40, seconds=6,
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], calls[1][1], "relaunch must reuse exact same XDG root")
        self.assertTrue(result["persistence"]["same_domain_document_sha256"])
        self.assertEqual(result["launch_count"], 2)
        self.assertEqual(result["claims"]["human_install_acceptance"], "NOT_RUN")
        self.assertEqual(result["claims"]["native_renderer_execution"], "NOT_RUN")
        self.assertNotIn("id", result["persistence"], "public report only retains identity hash")

    def test_relaunch_with_replaced_domain_project_cannot_claim_persistence(self):
        snapshot = probe.ProjectSnapshot(
            id=str(uuid.uuid4()), generation=str(uuid.uuid4()),
            revision=0, title="motionwright ci first use",
            document_sha256="a" * 64, schema_version=3, event_count=0,
        )
        for update in [
            {"id": str(uuid.uuid4())},
            {"generation": str(uuid.uuid4())},
            {"revision": 1},
            {"document_sha256": "b" * 64},
            {"event_count": 1},
        ]:
            newer = probe.ProjectSnapshot(**{**snapshot.__dict__, **update})
            with self.subTest(update=update), self.assertRaisesRegex(
                probe.ColdStartError, "replaced or mutated"
            ):
                probe.attest_persistence(snapshot, newer)

    def test_unsafe_or_missing_database_rejected_before_inspection(self):
        data = self.scratch / "data"
        data.mkdir(parents=True)
        with self.assertRaisesRegex(probe.ColdStartError, "exactly one"):
            probe.read_snapshot(data)
        outside = self.work / "outside.sqlite3"
        outside.write_bytes(b"SQLite?")
        (data / "motionwright.sqlite3").symlink_to(outside)
        with self.assertRaisesRegex(probe.ColdStartError, "symlink"):
            probe.read_snapshot(data)

    def test_nested_directory_symlink_is_never_followed(self):
        data = self.scratch / "data"
        data.mkdir(parents=True)
        (data / "external").symlink_to(self.work, target_is_directory=True)
        with self.assertRaisesRegex(probe.ColdStartError, "symlink"):
            probe.read_snapshot(data)

    def test_tampered_scope_and_multiple_projects_rejected(self):
        database = seeded_store(self.scratch)
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute(
                "UPDATE projects SET document_json=?",
                (json.dumps({"id": "foreign", "generation": "foreign",
                             "title": "motionwright ci first use", "revision": 0}),),
            )
        with self.assertRaisesRegex(probe.ColdStartError, "disagree"):
            probe.read_snapshot(self.scratch / "data")

        # An extra project is not a normal fresh-install default.
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute(
                "INSERT INTO projects VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), str(uuid.uuid4()), 0,
                 "Another project", "{}"),
            )
        with self.assertRaisesRegex(probe.ColdStartError, "exactly one project"):
            probe.read_snapshot(self.scratch / "data")

    def test_schema_mismatch_or_corrupt_file_cannot_become_pass(self):
        database = seeded_store(self.scratch)
        for version in (0, 1, 2, 4, 5):
            with closing(sqlite3.connect(database)) as conn, conn:
                conn.execute("PRAGMA user_version=" + str(version))
            with self.subTest(schema=version), self.assertRaisesRegex(
                probe.ColdStartError, "supported native schema",
            ):
                probe.read_snapshot(self.scratch / "data")
        database.write_bytes(b"not a real sqlite database")
        with self.assertRaisesRegex(probe.ColdStartError, "read-only"):
            probe.read_snapshot(self.scratch / "data")

    def test_offline_first_use_must_not_create_native_render_receipt(self):
        database = seeded_store(self.scratch)
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("INSERT INTO production_receipts VALUES('fabricated-receipt')")
        with self.assertRaisesRegex(probe.ColdStartError, "native render receipts"):
            probe.read_snapshot(self.scratch / "data")

    def test_environment_or_preexisting_scratch_prevents_autolaunch(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), patch.object(
            probe, "run_probe"
        ) as launcher:
            with self.assertRaisesRegex(probe.ColdStartError, "disposable CI only"):
                probe.probe_cold_relaunch(
                    app_run=self.work / "AppRun", scratch=self.scratch,
                    source_sha="a" * 40, semwright_sha="b" * 40,
                )
            launcher.assert_not_called()
        self.scratch.mkdir()
        (self.scratch / "leftover").write_text("old state")
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(
            probe, "run_probe"
        ) as launcher:
            with self.assertRaisesRegex(probe.ColdStartError, "empty"):
                probe.probe_cold_relaunch(
                    app_run=self.work / "AppRun", scratch=self.scratch,
                    source_sha="a" * 40, semwright_sha="b" * 40,
                )
            launcher.assert_not_called()

    def test_project_document_source_digest_is_bound_to_actual_sqlite_bytes(self):
        seeded_store(self.scratch)
        snapshot = probe.read_snapshot(self.scratch / "data")
        db = self.scratch / "data" / "com.seradotcom.motionwright" / "motionwright.sqlite3"
        with closing(sqlite3.connect(db)) as conn, conn:
            value = conn.execute("SELECT document_json FROM projects").fetchone()[0]
        self.assertEqual(
            snapshot.document_sha256,
            hashlib.sha256(value.encode()).hexdigest(),
        )


    def test_explicit_source_four_reads_the_exact_document_without_rewriting_sqlite(self):
        database = schema_four_store(self.scratch)
        original = database.read_bytes()
        selected = storage_cohort_fixture(4)
        snapshot = probe.read_snapshot(self.scratch / "data", storage_cohort=selected)
        self.assertEqual(snapshot.schema_version, 4)
        self.assertEqual(database.read_bytes(), original)
        self.assertEqual(probe.attest_persistence(snapshot, snapshot)["schema_version"], 4)

    def test_source_four_never_replaces_the_historical_default_three(self):
        schema_four_store(self.scratch)
        with self.assertRaises(probe.ColdStartError):
            probe.read_snapshot(self.scratch / "data")
        with self.assertRaises(probe.ColdStartError):
            probe.read_snapshot(self.scratch / "data", storage_cohort=storage_cohort_fixture(3))

    def test_empty_four_requires_its_source_and_does_not_create_on_a_mismatch(self):
        database = schema_four_store(self.scratch)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("DELETE FROM projects")
        selected = storage_cohort_fixture(4)
        self.assertTrue(probe._empty_library_ready(self.scratch, storage_cohort=selected))
        self.assertFalse(probe._empty_library_ready(self.scratch))
        with patch.object(probe, "_send_native_x11_create") as create:
            self.assertFalse(probe._first_project_window_interaction(
                self.scratch, storage_cohort=storage_cohort_fixture(3)))
            create.assert_not_called()
            self.assertTrue(probe._first_project_window_interaction(
                self.scratch, storage_cohort=selected))
            self.assertEqual(create.call_count, 1)

    def test_four_rejects_document_four_and_missing_binding_columns(self):
        database = schema_four_store(self.scratch)
        selected = storage_cohort_fixture(4)
        with closing(sqlite3.connect(database)) as db, db:
            row = json.loads(db.execute("SELECT document_json FROM projects").fetchone()[0])
            row["schema_version"] = 4
            db.execute("UPDATE projects SET document_json=?", (json.dumps(row),))
        with self.assertRaises(probe.ColdStartError):
            probe.read_snapshot(self.scratch / "data", storage_cohort=selected)
        with closing(sqlite3.connect(database)) as db, db:
            row["schema_version"] = 2
            db.execute("UPDATE projects SET document_json=?", (json.dumps(row),))
            db.execute("ALTER TABLE receipts DROP COLUMN apply_binding_json")
        with self.assertRaises(probe.ColdStartError):
            probe.read_snapshot(self.scratch / "data", storage_cohort=selected)

    def test_foreign_source_cohort_is_rejected_before_a_gui_probe(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(probe, "run_probe") as gui:
            with self.assertRaises(probe.storage.StorageContractError):
                probe.probe_cold_relaunch(
                    app_run=self.work / "AppRun", scratch=self.scratch,
                    source_sha="b" * 40, semwright_sha="c" * 40,
                    storage_cohort=storage_cohort_fixture(4))
            gui.assert_not_called()

    def test_two_lifetimes_receive_the_same_source_cohort_and_new_receipt_version(self):
        selected = storage_cohort_fixture(4)
        calls = []
        def gui(app_run, scratch, **kwargs):
            if not calls:
                schema_four_store(scratch)
            calls.append(kwargs)
            self.assertTrue(kwargs["ready_check"](scratch))
            return {"status": "WINDOW_OBSERVED"}
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(probe, "run_probe", side_effect=gui):
            result = probe.probe_cold_relaunch(
                app_run=self.work / "AppRun", scratch=self.scratch,
                source_sha="a" * 40, semwright_sha="c" * 40, storage_cohort=selected)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["schema"], "motionwright.ci.installed-cold-relaunch/2")
        self.assertEqual(result["storage_cohort"], selected)
        self.assertEqual(result["persistence"]["schema_version"], 4)
        self.assertNotIn("window_interaction", calls[1])


    def test_explicit_five_reads_cas_and_document_without_changing_sqlite(self):
        database = schema_five_store(self.scratch)
        original = database.read_bytes()
        selected = storage_cohort_fixture(5)
        observed = probe.read_snapshot(self.scratch / "data", storage_cohort=selected)
        self.assertEqual(observed.schema_version, 5)
        self.assertEqual(probe.attest_persistence(observed, observed)["schema_version"], 5)
        with self.assertRaises(probe.ColdStartError):
            probe.read_snapshot(self.scratch / "data")
        with self.assertRaises(probe.ColdStartError):
            probe.read_snapshot(self.scratch / "data", storage_cohort=storage_cohort_fixture(4))
        self.assertEqual(database.read_bytes(), original)

    def test_five_requires_cas_in_both_snapshot_and_preaction_readiness(self):
        database = schema_five_store(self.scratch)
        selected = storage_cohort_fixture(5)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM projects")
        self.assertTrue(probe._empty_library_ready(self.scratch, storage_cohort=selected))
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DROP TABLE cas_import_commits")
        original = database.read_bytes()
        self.assertFalse(probe._empty_library_ready(self.scratch, storage_cohort=selected))
        with patch.object(probe, "_send_native_x11_create") as create:
            self.assertFalse(probe._first_project_window_interaction(
                self.scratch, storage_cohort=selected))
            create.assert_not_called()
        with self.assertRaisesRegex(probe.ColdStartError, "storage_cas_objects_differ"):
            probe.read_snapshot(self.scratch / "data", storage_cohort=selected)
        self.assertEqual(database.read_bytes(), original)

    def test_five_relaunch_threads_one_source_cohort_to_both_real_sqlite_reads(self):
        selected = storage_cohort_fixture(5)
        calls = []
        def gui(app_run, scratch, **kwargs):
            if not calls:
                schema_five_store(scratch)
            calls.append(kwargs)
            self.assertTrue(kwargs["ready_check"](scratch))
            return {"status": "WINDOW_OBSERVED"}
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(probe, "run_probe", side_effect=gui):
            result = probe.probe_cold_relaunch(
                app_run=self.work / "AppRun", scratch=self.scratch,
                source_sha="a" * 40, semwright_sha="c" * 40, storage_cohort=selected)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["storage_cohort"], selected)
        self.assertEqual(result["persistence"]["schema_version"], 5)
        self.assertEqual(result["claims"]["native_renderer_execution"], "NOT_RUN")
        self.assertNotIn("window_interaction", calls[1])


if __name__ == "__main__":
    unittest.main()
