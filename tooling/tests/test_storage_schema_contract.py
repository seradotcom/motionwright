"""Pure source/cohort controls; no application or native process is launched."""
from __future__ import annotations

import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage_schema_contract as storage


def observed_source(version=4, *, source="a" * 40, tree="b" * 40, raw=None, crlf=False):
    blob = (f"pub const STORAGE_SCHEMA_VERSION: u32 = {version};\n".encode()
            if raw is None else raw)
    checkout = blob.replace(b"\n", b"\r\n") if crlf else blob
    identity = lambda value: {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    original = identity(blob)
    observed = {"sha": source, "tree": tree, "files": {storage.SOURCE_PATH: {
        "git_bytes": original["bytes"], "git_sha256": original["sha256"],
        "checkout": identity(checkout),
        "checkout_encoding": "EXACT_LF_TO_CRLF" if crlf else "EXACT_GIT_BYTES"}}}
    return observed, checkout


def cohort(version=4, **kwargs):
    return storage.admit_source(*observed_source(version, **kwargs))


def receipt_columns(version=4):
    result = [(0, "project_id", "TEXT", 1, None, 1, 0),
              (1, "request_id", "TEXT", 1, None, 2, 0),
              (2, "request_sha256", "TEXT", 1, None, 0, 0),
              (3, "result_json", "TEXT", 1, None, 0, 0),
              (4, "created_at", "TEXT", 1, None, 0, 0)]
    if version in (4, 5, 6):
        result.extend([(5, "apply_binding_version", "INTEGER", 1, "0", 0, 0),
                       (6, "apply_binding_json", "TEXT", 0, None, 0, 0)])
    return result


class StorageCohortControls(unittest.TestCase):
    def test_each_supported_source_has_a_separate_storage_and_document_contract(self):
        for version in (2, 3, 4, 5, 6):
            with self.subTest(version=version):
                value = cohort(version)
                self.assertEqual(value["storage_schema"], version)
                self.assertEqual(value["document_schema_after_save"], 2)
                self.assertEqual(storage.validate_cohort(value), value)
        self.assertEqual(cohort(4)["receipt_family"], "v1-with-legacy-zero-null")
        self.assertEqual(cohort(5)["receipt_family"], "v1-with-legacy-zero-null")

    def test_absence_retains_historical_defaults_and_cannot_mean_current(self):
        self.assertEqual(storage.expected_schema(None), 3)
        self.assertEqual(storage.expected_schema(None, historical_default=2), 2)
        for value in (4, 5, True, "4", "5", None):
            with self.subTest(default=value), self.assertRaises(storage.StorageContractError):
                storage.expected_schema(None, historical_default=value)

    def test_source_and_tree_are_explicit_and_cross_cohort_records_fail(self):
        value = cohort(4)
        for kwargs in ({"source_sha": "c" * 40}, {"source_tree": "c" * 40}):
            with self.subTest(kwargs=kwargs), self.assertRaises(storage.StorageContractError):
                storage.validate_cohort(value, **kwargs)
        for field, replacement in (("storage_schema", True), ("storage_schema", 7),
                                   ("receipt_family", "historical-v0"),
                                   ("document_schema_after_save", 4)):
            wrong = copy.deepcopy(value)
            wrong[field] = replacement
            with self.subTest(field=field, value=replacement), self.assertRaises(storage.StorageContractError):
                storage.validate_cohort(wrong)

    def test_original_source_bytes_are_bound_before_the_declaration_is_used(self):
        observation, raw = observed_source(4)
        with self.assertRaisesRegex(storage.StorageContractError, "storage_original_bytes_changed"):
            storage.admit_source(observation, raw.replace(b"4", b"3"))
        wrong = copy.deepcopy(observation)
        wrong["files"] = {}
        with self.assertRaises(storage.StorageContractError):
            storage.admit_source(wrong, raw)

    def test_missing_duplicate_future_and_nonliteral_declarations_fail_closed(self):
        for raw in (b"// no declaration\n",
                    b"pub const STORAGE_SCHEMA_VERSION: u32 = 4;\n" * 2,
                    b"pub const STORAGE_SCHEMA_VERSION: u32 = 7;\n",
                    b"pub const STORAGE_SCHEMA_VERSION: u32 = 2 + 2;\n",
                    b"#[cfg(unix)]\npub const STORAGE_SCHEMA_VERSION: u32 = 4;\n",
                    b"#[cfg(\nunix\n)]\npub const STORAGE_SCHEMA_VERSION: u32 = 4;\n"):
            with self.subTest(raw=raw), self.assertRaises(storage.StorageContractError):
                storage.admit_source(*observed_source(raw=raw))

    def test_comments_do_not_become_a_storage_declaration(self):
        for raw in (b"// pub const STORAGE_SCHEMA_VERSION: u32 = 4;\n",
                    b"/*\npub const STORAGE_SCHEMA_VERSION: u32 = 4;\n*/\n",
                    b"/* /* nested */\npub const STORAGE_SCHEMA_VERSION: u32 = 4;\n*/"):
            with self.subTest(raw=raw), self.assertRaises(storage.StorageContractError):
                storage.admit_source(*observed_source(raw=raw))

    def test_source_bounds_utf8_and_nul_are_closed(self):
        for raw in (b"", b"\xff", b"\0", b"x" * (storage.MAX_SOURCE_BYTES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(storage.StorageContractError):
                storage.admit_source(*observed_source(raw=raw))

    def test_crlf_is_kept_as_distinct_physical_custody(self):
        value = cohort(4, crlf=True)
        item = value["source_input"]
        self.assertNotEqual(item["git"], item["checkout"])
        self.assertEqual(item["checkout_encoding"], "EXACT_LF_TO_CRLF")
        changed = copy.deepcopy(value)
        changed["source_input"]["checkout_encoding"] = "EXACT_GIT_BYTES"
        with self.assertRaises(storage.StorageContractError):
            storage.validate_cohort(changed)

    def test_cohort_fields_are_exact_and_returned_records_are_independent(self):
        value = cohort(4)
        other = storage.validate_cohort(value)
        other["source_input"]["git"]["sha256"] = "c" * 64
        self.assertNotEqual(value, other)
        for change in ("extra", "missing"):
            wrong = copy.deepcopy(value)
            if change == "extra":
                wrong["claim"] = "PASS"
            else:
                wrong.pop("source_input")
            with self.assertRaises(storage.StorageContractError):
                storage.validate_cohort(wrong)

    def test_source_input_path_encoding_and_identity_are_closed(self):
        for key, replacement in (("path", "../lib.rs"), ("checkout_encoding", "NORMALIZED"),
                                 ("git", {"bytes": True, "sha256": "c" * 64})):
            value = cohort(4)
            value["source_input"][key] = replacement
            with self.subTest(key=key), self.assertRaises(storage.StorageContractError):
                storage.validate_cohort(value)

    def test_source_observer_is_reused_and_postread_change_cannot_be_admitted(self):
        import source_checkout_observation as observer
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            original, raw = observed_source(4)
            path = root / storage.SOURCE_PATH
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            changed = copy.deepcopy(original)
            changed["tree"] = "c" * 40
            with mock.patch.object(observer, "source_observation", side_effect=[original, changed]) as read:
                with self.assertRaisesRegex(storage.StorageContractError, "storage_source_changed"):
                    storage.admit_checkout(root, "a" * 40)
                self.assertEqual(read.call_args_list,
                    [mock.call(root, "a" * 40, (storage.SOURCE_PATH,))] * 2)

    def test_exact_source_observation_is_retained_without_touching_source(self):
        import source_checkout_observation as observer
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            original, raw = observed_source(4)
            path = root / storage.SOURCE_PATH
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            with mock.patch.object(observer, "source_observation", return_value=original):
                value = storage.admit_checkout(root, "a" * 40)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(value, cohort(4))

    def test_layout_distinguishes_historical_rows_from_reserved_binding_columns(self):
        for version in (2, 3):
            storage.validate_receipt_columns(receipt_columns(version), cohort(version))
            with self.assertRaises(storage.StorageContractError):
                storage.validate_receipt_columns(receipt_columns(4), cohort(version))
        for version in (4, 5):
            storage.validate_receipt_columns(receipt_columns(version), cohort(version))
            with self.assertRaises(storage.StorageContractError):
                storage.validate_receipt_columns(receipt_columns(3), cohort(version))

    def test_binding_family_rejects_alias_hidden_column_type_and_default_changes(self):
        for index, field, value in ((5, 1, "APPLY_BINDING_VERSION"), (5, 2, "TEXT"),
                                    (5, 3, 0), (5, 4, "1"), (5, 6, 2),
                                    (6, 1, "APPLY_BINDING_JSON"), (6, 2, "BLOB"),
                                    (6, 3, 1), (6, 6, 3)):
            columns = [list(x) for x in receipt_columns()]
            columns[index][field] = value
            for version in (4, 5):
                with self.subTest(version=version, index=index, field=field), self.assertRaises(storage.StorageContractError):
                    storage.validate_receipt_columns(columns, cohort(version))

    def test_missing_duplicate_or_malformed_column_observations_fail(self):
        for columns in ([], [("bad",)], receipt_columns() * 2):
            with self.subTest(columns=columns), self.assertRaises(storage.StorageContractError):
                storage.validate_receipt_columns(columns, cohort(4))

    def test_document_two_is_not_replaced_by_the_storage_version(self):
        for version in (4, 5):
            storage.validate_document({"schema_version": 2}, cohort(version))
        storage.validate_document({"schema_version": 1}, cohort(2))
        for document in ({}, {"schema_version": 4}, {"schema_version": 5}, {"schema_version": True},
                         {"schema_version": "2"}):
            for version in (4, 5):
                with self.subTest(version=version, document=document), self.assertRaises(storage.StorageContractError):
                    storage.validate_document(document, cohort(version))



class StorageSqliteBindingShapeControls(unittest.TestCase):
    """SQLite metadata from the receipt declarations used by storage schema four."""

    def columns(self, *, payload_default="", primary_key="project_id,request_id"):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory(prefix="mw-binding-column-shape-") as temporary:
            database = Path(temporary) / "receipt.sqlite3"
            sql = f"""CREATE TABLE receipts(
                project_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                apply_binding_version INTEGER NOT NULL DEFAULT 0
                    CHECK (apply_binding_version IN (0, 1)),
                apply_binding_json TEXT{payload_default},
                PRIMARY KEY({primary_key}),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )"""
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(sql)
            original = database.read_bytes()
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
                rows = connection.execute("PRAGMA table_xinfo(receipts)").fetchall()
            self.assertEqual(database.read_bytes(), original)
            return rows

    def test_actual_sqlite_binding_shape_has_no_payload_default_or_binding_key(self):
        rows = self.columns()
        marker, payload = rows[-2:]
        self.assertEqual(marker[4:7], ("0", 0, 0))
        self.assertEqual(payload[4:7], (None, 0, 0))
        storage.validate_receipt_columns(rows, cohort(4))

    def test_payload_defaults_including_explicit_null_are_not_the_source_declaration(self):
        for default in (" DEFAULT 'forged'", " DEFAULT NULL"):
            with self.subTest(default=default):
                rows = self.columns(payload_default=default)
                self.assertIsNotNone(rows[-1][4])
                with self.assertRaisesRegex(storage.StorageContractError, "storage_binding_columns_differ"):
                    storage.validate_receipt_columns(rows, cohort(4))

    def test_binding_columns_cannot_replace_the_project_request_primary_key(self):
        for key in ("apply_binding_version", "apply_binding_json"):
            with self.subTest(key=key):
                rows = self.columns(primary_key=key)
                observed = next(row for row in rows if row[1] == key)
                self.assertEqual(observed[5], 1)
                with self.assertRaisesRegex(storage.StorageContractError, "storage_binding_columns_differ"):
                    storage.validate_receipt_columns(rows, cohort(4))


class StorageCasSchemaFiveControls(unittest.TestCase):
    """Real SQLite files, read-only consumers; no Store, GUI or native jobs.

    The fixture independently spells out cas_retention::migrate_schema's SQL,
    rather than building a database from the validator's expected objects.
    """

    identity_sql = """CREATE TABLE cas_store_identity (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        store_id TEXT NOT NULL UNIQUE
    )"""
    commits_sql = """CREATE TABLE cas_import_commits (
        operation_id TEXT PRIMARY KEY NOT NULL,
        root_id TEXT NOT NULL,
        store_id TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        project_id TEXT NOT NULL,
        generation TEXT NOT NULL
    )"""
    store_id = "019a1700-0000-7000-8000-000000000001"

    def database(self, *, version=5, identity_sql=None, commits_sql=None,
                 include_cas=True, after=None):
        temporary = tempfile.TemporaryDirectory(
            prefix="mw-cas-schema-contract-", dir=Path(__file__).resolve().parents[2])
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "state.sqlite3"
        bindings = (""", apply_binding_version INTEGER NOT NULL DEFAULT 0
            CHECK(apply_binding_version IN (0,1)), apply_binding_json TEXT"""
            if version >= 4 else "")
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.executescript(f"""
                PRAGMA user_version={version};
                CREATE TABLE projects(id TEXT PRIMARY KEY);
                CREATE TABLE receipts(
                    project_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL, result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL{bindings},
                    PRIMARY KEY(project_id,request_id)
                );
            """)
            if include_cas:
                connection.execute(self.identity_sql if identity_sql is None else identity_sql)
                connection.execute(self.commits_sql if commits_sql is None else commits_sql)
                connection.execute("INSERT INTO cas_store_identity VALUES(1,?)", (self.store_id,))
            if after is not None:
                after(connection)
        return database

    def inspect(self, database, *, version=5, error=None):
        original = database.read_bytes()
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            if error is None:
                storage.validate_schema_layout(connection, cohort(version))
            else:
                with self.assertRaisesRegex(storage.StorageContractError, error):
                    storage.validate_schema_layout(connection, cohort(version))
        self.assertEqual(database.read_bytes(), original)

    def test_schema_five_admission_binds_exact_source_and_retains_document_two(self):
        for crlf in (False, True):
            with self.subTest(crlf=crlf):
                observation, raw = observed_source(5, crlf=crlf)
                admitted = storage.admit_source(observation, raw)
                self.assertEqual(admitted["storage_schema"], 5)
                self.assertEqual(admitted["document_schema_after_save"], 2)
                self.assertEqual(admitted["receipt_family"], "v1-with-legacy-zero-null")
                with self.assertRaisesRegex(storage.StorageContractError, "original_bytes_changed"):
                    storage.admit_source(observation, raw.replace(b"5", b"4"))

    def test_exact_schema_five_is_read_without_writing_or_migrating(self):
        self.inspect(self.database())

    def test_actual_historical_cohorts_remain_read_only_and_separate(self):
        for version in (2, 3, 4):
            with self.subTest(version=version):
                self.inspect(self.database(version=version, include_cas=False), version=version)

    def test_user_version_never_substitutes_for_source_admission(self):
        self.inspect(self.database(), version=4, error="storage_schema_version_mismatch")
        self.assertEqual(storage.expected_schema(None), 3)

    def test_schema_five_without_cas_objects_is_not_schema_four(self):
        self.inspect(self.database(include_cas=False), error="storage_cas_objects_differ")

    def test_historical_cohorts_reject_future_reserved_objects(self):
        for version in (2, 3, 4):
            with self.subTest(version=version):
                self.inspect(self.database(version=version), version=version,
                             error="historical_storage_has_reserved_cas_objects")

    def test_missing_table_and_reserved_view_cannot_pass_the_layout(self):
        for sql in ("DROP TABLE cas_import_commits",
                    "DROP TABLE cas_store_identity",
                    "DROP TABLE cas_import_commits; CREATE VIEW cas_import_commits AS SELECT 1"):
            with self.subTest(sql=sql):
                database = self.database(after=lambda connection: connection.executescript(sql))
                self.inspect(database, error="storage_cas_objects_differ")

    def test_primary_key_unique_and_singleton_check_are_required(self):
        alternatives = (
            {"identity_sql": self.identity_sql.replace(" CHECK(singleton=1)", "")},
            {"identity_sql": self.identity_sql.replace("CHECK(singleton=1)",
                                                       "CHECK(singleton IN (1,2))")},
            {"identity_sql": self.identity_sql.replace(" NOT NULL UNIQUE", " NOT NULL")},
            {"commits_sql": self.commits_sql.replace("PRIMARY KEY NOT NULL", "UNIQUE NOT NULL")},
        )
        for changes in alternatives:
            with self.subTest(changes=changes):
                self.inspect(self.database(**changes), error="storage_cas_(objects_differ|ddl_differs)")

    def test_type_default_collation_and_extra_columns_are_not_source_layout(self):
        alternatives = (
            {"commits_sql": self.commits_sql.replace("root_id TEXT", "root_id BLOB")},
            {"commits_sql": self.commits_sql.replace("generation TEXT NOT NULL",
                                                     "generation TEXT NOT NULL DEFAULT ''")},
            {"commits_sql": self.commits_sql.replace("generation TEXT NOT NULL",
                                                     "generation TEXT NOT NULL, extra TEXT")},
            {"identity_sql": self.identity_sql.replace("store_id TEXT", "store_id TEXT COLLATE NOCASE")},
        )
        for changes in alternatives:
            with self.subTest(changes=changes):
                self.inspect(self.database(**changes), error="storage_cas_ddl_differs")

    def test_commit_markers_must_not_cascade_with_projects(self):
        sql = self.commits_sql.replace("project_id TEXT NOT NULL",
            "project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE")
        self.inspect(self.database(commits_sql=sql), error="storage_cas_ddl_differs")

    def test_triggers_and_additional_indexes_are_not_admitted(self):
        for sql in (
            "CREATE TRIGGER erase_import_marker AFTER INSERT ON cas_import_commits "
            "BEGIN DELETE FROM cas_import_commits WHERE operation_id=NEW.operation_id; END",
            "CREATE TRIGGER erase_identity AFTER INSERT ON cas_store_identity "
            "BEGIN DELETE FROM cas_store_identity; END",
            "CREATE INDEX unexpected_cas_index ON cas_import_commits(project_id)",
        ):
            with self.subTest(sql=sql):
                database = self.database(after=lambda connection: connection.executescript(sql))
                self.inspect(database, error="storage_cas_objects_differ")

    def test_store_identity_must_be_present_singular_and_a_uuid(self):
        alternatives = (
            ("DELETE FROM cas_store_identity", "storage_cas_identity_not_singular"),
            ("UPDATE cas_store_identity SET store_id='not-a-uuid'", "storage_cas_identity_malformed"),
            ("PRAGMA ignore_check_constraints=ON; INSERT INTO cas_store_identity "
             "VALUES(2,'019a1700-0000-7000-8000-000000000002')", "storage_cas_identity_not_singular"),
        )
        for sql, error in alternatives:
            with self.subTest(sql=sql):
                database = self.database(after=lambda connection: connection.executescript(sql))
                self.inspect(database, error=error)

    def test_committed_import_marker_may_outlive_its_project(self):
        def insert_marker(connection):
            connection.execute("PRAGMA foreign_keys=ON")
            deleted_project = "019a1700-0000-7000-8000-000000000005"
            connection.execute("INSERT INTO projects VALUES(?)", (deleted_project,))
            connection.execute("INSERT INTO cas_import_commits VALUES(?,?,?,?,?,?)", (
                "019a1700-0000-7000-8000-000000000002",
                "019a1700-0000-7000-8000-000000000003", self.store_id, "a" * 64,
                deleted_project, "019a1700-0000-7000-8000-000000000004"))
            connection.execute("DELETE FROM projects")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cas_import_commits").fetchone(), (1,))
        self.inspect(self.database(after=insert_marker))


def add_schema_five_cas(connection):
    """Independent schema-5 fixture shared by installed-reader policy tests."""
    connection.execute(StorageCasSchemaFiveControls.identity_sql)
    connection.execute(StorageCasSchemaFiveControls.commits_sql)
    connection.execute("INSERT INTO cas_store_identity VALUES(1,?)",
                       (StorageCasSchemaFiveControls.store_id,))
    connection.execute("PRAGMA user_version=5")


# Independent exact-production SQL fixture, not validator constants.
SCHEMA_SIX_V5_SQL = '\n            CREATE TABLE IF NOT EXISTS projects(\n              id TEXT PRIMARY KEY,\n              generation TEXT NOT NULL,\n              revision INTEGER NOT NULL CHECK(revision >= 0),\n              title TEXT NOT NULL,\n              document_json TEXT NOT NULL,\n              updated_at TEXT NOT NULL\n            );\n\n            CREATE TABLE IF NOT EXISTS receipts(\n              project_id TEXT NOT NULL,\n              request_id TEXT NOT NULL,\n              request_sha256 TEXT NOT NULL,\n              result_json TEXT NOT NULL,\n              created_at TEXT NOT NULL,\n              PRIMARY KEY(project_id, request_id),\n              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE\n            );\n\n            CREATE TABLE IF NOT EXISTS events(\n              event_id INTEGER PRIMARY KEY AUTOINCREMENT,\n              project_id TEXT NOT NULL,\n              revision INTEGER NOT NULL,\n              kind TEXT NOT NULL,\n              payload_json TEXT NOT NULL,\n              created_at TEXT NOT NULL,\n              UNIQUE(project_id, revision),\n              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE\n            );\n\n            CREATE TABLE IF NOT EXISTS production_receipts(\n              receipt_id TEXT PRIMARY KEY,\n              project_id TEXT NOT NULL,\n              generation TEXT NOT NULL,\n              revision INTEGER NOT NULL CHECK(revision >= 0),\n              request_id TEXT NOT NULL,\n              request_sha256 TEXT NOT NULL,\n              command TEXT NOT NULL,\n              stage TEXT NOT NULL,\n              payload_json TEXT NOT NULL,\n              created_at TEXT NOT NULL,\n              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE\n            );\n\n            -- Derived proxy metadata is disposable, and never grants render authority.\n            CREATE TABLE IF NOT EXISTS render_window_cache_entries(\n              project_id TEXT NOT NULL,\n              generation TEXT NOT NULL,\n              revision INTEGER NOT NULL CHECK(revision >= 0),\n              deliverable_id TEXT NOT NULL,\n              source_start INTEGER NOT NULL CHECK(source_start >= 0),\n              source_end INTEGER NOT NULL CHECK(source_end > source_start),\n              fingerprint_sha256 TEXT NOT NULL,\n              PRIMARY KEY(project_id, generation, revision, deliverable_id,\n                          source_start, source_end, fingerprint_sha256),\n              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE\n            );\n            CREATE INDEX IF NOT EXISTS render_window_cache_by_owner\n              ON render_window_cache_entries(project_id, generation, deliverable_id);\n\n            -- A commit atomically revokes cache registrations. Recoverable\n            -- eviction records retain only derived-artifact keys, never source.\n            CREATE TABLE IF NOT EXISTS render_window_cache_evictions(\n              project_id TEXT NOT NULL,\n              generation TEXT NOT NULL,\n              revision INTEGER NOT NULL CHECK(revision >= 0),\n              deliverable_id TEXT NOT NULL,\n              source_start INTEGER NOT NULL CHECK(source_start >= 0),\n              source_end INTEGER NOT NULL CHECK(source_end > source_start),\n              fingerprint_sha256 TEXT NOT NULL,\n              PRIMARY KEY(project_id, generation, revision, deliverable_id,\n                          source_start, source_end, fingerprint_sha256),\n              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE\n            );\n            CREATE TRIGGER IF NOT EXISTS render_window_cache_invalidate_revision\n            AFTER UPDATE OF generation, revision ON projects\n            WHEN OLD.generation<>NEW.generation OR OLD.revision<>NEW.revision\n            BEGIN\n              INSERT OR IGNORE INTO render_window_cache_evictions(\n                project_id,generation,revision,deliverable_id,\n                source_start,source_end,fingerprint_sha256)\n                SELECT project_id,generation,revision,deliverable_id,\n                       source_start,source_end,fingerprint_sha256\n                FROM render_window_cache_entries\n                WHERE project_id=NEW.id\n                  AND (generation<>NEW.generation OR revision<>NEW.revision);\n              DELETE FROM render_window_cache_entries\n              WHERE project_id=NEW.id\n                AND (generation<>NEW.generation OR revision<>NEW.revision);\n            END;\n\n            CREATE INDEX IF NOT EXISTS events_by_project_revision\n              ON events(project_id, revision);\n            CREATE INDEX IF NOT EXISTS production_receipts_by_project\n              ON production_receipts(project_id, receipt_id DESC);\n            CREATE INDEX IF NOT EXISTS production_receipts_by_request\n              ON production_receipts(project_id, request_id, receipt_id DESC);\n\nCREATE TABLE production_receipt_order(\n           sequence INTEGER PRIMARY KEY AUTOINCREMENT,\n           receipt_id TEXT NOT NULL UNIQUE,\n           project_id TEXT NOT NULL,\n           FOREIGN KEY(receipt_id) REFERENCES production_receipts(receipt_id)\n             ON UPDATE CASCADE ON DELETE CASCADE,\n           FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE\n         );\n\nCREATE INDEX production_receipt_order_by_project\n           ON production_receipt_order(project_id, sequence DESC);\n\nCREATE TRIGGER production_receipt_order_append\n         AFTER INSERT ON production_receipts\n         BEGIN\n           INSERT INTO production_receipt_order(receipt_id,project_id)\n             VALUES(NEW.receipt_id,NEW.project_id);\n         END;\n\nALTER TABLE receipts ADD COLUMN apply_binding_version INTEGER NOT NULL DEFAULT 0\n         CHECK (apply_binding_version IN (0, 1));\n\nALTER TABLE receipts ADD COLUMN apply_binding_json TEXT;\n\nCREATE TABLE cas_store_identity (\n            singleton INTEGER PRIMARY KEY CHECK(singleton=1),\n            store_id TEXT NOT NULL UNIQUE\n        );\n        CREATE TABLE cas_import_commits (\n            operation_id TEXT PRIMARY KEY NOT NULL,\n            root_id TEXT NOT NULL,\n            store_id TEXT NOT NULL,\n            request_sha256 TEXT NOT NULL,\n            project_id TEXT NOT NULL,\n            generation TEXT NOT NULL\n        );\n'
SCHEMA_SIX_MIGRATION_SQL = "CREATE TABLE production_operation_roots(\n project_id TEXT NOT NULL,\n root_id TEXT NOT NULL,\n operation_key TEXT NOT NULL,\n generation TEXT NOT NULL,\n revision INTEGER NOT NULL CHECK(revision >= 0),\n limit_attempts INTEGER NOT NULL CHECK(limit_attempts > 0),\n limit_model_calls INTEGER NOT NULL CHECK(limit_model_calls >= 0),\n limit_render_frames INTEGER NOT NULL CHECK(limit_render_frames >= 0),\n limit_output_bytes INTEGER NOT NULL CHECK(limit_output_bytes >= 0),\n PRIMARY KEY(project_id,root_id),\n UNIQUE(project_id,operation_key),\n FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE RESTRICT\n)\n;\nCREATE TABLE production_operation_debits(\n project_id TEXT NOT NULL,\n request_id TEXT NOT NULL,\n root_id TEXT NOT NULL,\n receipt_id TEXT NOT NULL UNIQUE,\n generation TEXT NOT NULL,\n revision INTEGER NOT NULL CHECK(revision >= 0),\n request_sha256 TEXT NOT NULL,\n command TEXT NOT NULL,\n attempts INTEGER NOT NULL CHECK(attempts = 1),\n model_calls INTEGER NOT NULL CHECK(model_calls >= 0),\n render_frames INTEGER NOT NULL CHECK(render_frames >= 0),\n output_bytes INTEGER NOT NULL CHECK(output_bytes >= 0),\n PRIMARY KEY(project_id,request_id),\n FOREIGN KEY(project_id,root_id) REFERENCES production_operation_roots(project_id,root_id)\n   ON DELETE RESTRICT\n)\n;\nCREATE TRIGGER production_operation_dispatch_gate\nBEFORE INSERT ON production_receipts\nWHEN EXISTS(SELECT 1 FROM production_operation_roots WHERE project_id=NEW.project_id)\n AND (NEW.stage='dispatching'\n      OR NOT EXISTS(\n        SELECT 1 FROM production_receipts AS prior\n        WHERE prior.project_id=NEW.project_id AND prior.request_id=NEW.request_id\n      ))\nBEGIN\n SELECT RAISE(ABORT,'budgeted dispatch requires matching retained debit')\n WHERE NOT EXISTS(\n  SELECT 1 FROM production_operation_debits AS d\n  WHERE d.project_id=NEW.project_id AND d.request_id=NEW.request_id\n   AND d.receipt_id=NEW.receipt_id AND d.generation=NEW.generation\n   AND d.revision=NEW.revision AND d.command=NEW.command\n   AND d.request_sha256=NEW.request_sha256\n );\nEND\n;\nCREATE TRIGGER production_operation_root_immutable\nBEFORE UPDATE ON production_operation_roots\nBEGIN SELECT RAISE(ABORT,'operation roots are immutable'); END\n;\nCREATE TRIGGER production_operation_debit_immutable\nBEFORE UPDATE ON production_operation_debits\nBEGIN SELECT RAISE(ABORT,'operation debits are immutable'); END\n;\nCREATE TRIGGER production_operation_root_nodelete\nBEFORE DELETE ON production_operation_roots\nBEGIN SELECT RAISE(ABORT,'operation roots cannot be discarded'); END\n;\nCREATE TRIGGER production_operation_debit_nodelete\nBEFORE DELETE ON production_operation_debits\nBEGIN SELECT RAISE(ABORT,'operation debits cannot be discarded'); END\n;\nCREATE TRIGGER production_operation_debit_budget_gate\nBEFORE INSERT ON production_operation_debits\nBEGIN\n SELECT RAISE(ABORT,'unknown or stale root')\n WHERE NOT EXISTS(\n  SELECT 1 FROM production_operation_roots AS r\n  WHERE r.project_id=NEW.project_id AND r.root_id=NEW.root_id\n   AND r.generation=NEW.generation AND r.revision=NEW.revision\n );\n SELECT RAISE(ABORT,'root operation budget exhausted')\n WHERE EXISTS(\n   SELECT 1 FROM production_operation_roots AS r\n   WHERE r.project_id=NEW.project_id AND r.root_id=NEW.root_id\n     AND (\n       (SELECT COALESCE(SUM(d.attempts),0) FROM production_operation_debits AS d\n        WHERE d.project_id=NEW.project_id AND d.root_id=NEW.root_id)\n            > r.limit_attempts-NEW.attempts\n       OR (SELECT COALESCE(SUM(d.model_calls),0) FROM production_operation_debits AS d\n           WHERE d.project_id=NEW.project_id AND d.root_id=NEW.root_id)\n            > r.limit_model_calls-NEW.model_calls\n       OR (SELECT COALESCE(SUM(d.render_frames),0) FROM production_operation_debits AS d\n           WHERE d.project_id=NEW.project_id AND d.root_id=NEW.root_id)\n            > r.limit_render_frames-NEW.render_frames\n       OR (SELECT COALESCE(SUM(d.output_bytes),0) FROM production_operation_debits AS d\n           WHERE d.project_id=NEW.project_id AND d.root_id=NEW.root_id)\n            > r.limit_output_bytes-NEW.output_bytes\n     )\n );\nEND\n;\nCREATE TRIGGER mw_writer_v6_projects_insert BEFORE INSERT ON projects BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_projects_update BEFORE UPDATE ON projects BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_projects_delete BEFORE DELETE ON projects BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_receipts_insert BEFORE INSERT ON receipts BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_receipts_update BEFORE UPDATE ON receipts BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_receipts_delete BEFORE DELETE ON receipts BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_events_insert BEFORE INSERT ON events BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_events_update BEFORE UPDATE ON events BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_events_delete BEFORE DELETE ON events BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_production_receipts_insert BEFORE INSERT ON production_receipts BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_production_receipts_update BEFORE UPDATE ON production_receipts BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_production_receipts_delete BEFORE DELETE ON production_receipts BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_production_receipt_order_insert BEFORE INSERT ON production_receipt_order BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_production_receipt_order_update BEFORE UPDATE ON production_receipt_order BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_production_receipt_order_delete BEFORE DELETE ON production_receipt_order BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_render_window_cache_entries_insert BEFORE INSERT ON render_window_cache_entries BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_render_window_cache_entries_update BEFORE UPDATE ON render_window_cache_entries BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_render_window_cache_entries_delete BEFORE DELETE ON render_window_cache_entries BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_render_window_cache_evictions_insert BEFORE INSERT ON render_window_cache_evictions BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_render_window_cache_evictions_update BEFORE UPDATE ON render_window_cache_evictions BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_render_window_cache_evictions_delete BEFORE DELETE ON render_window_cache_evictions BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_cas_store_identity_insert BEFORE INSERT ON cas_store_identity BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_cas_store_identity_update BEFORE UPDATE ON cas_store_identity BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_cas_store_identity_delete BEFORE DELETE ON cas_store_identity BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_cas_import_commits_insert BEFORE INSERT ON cas_import_commits BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_cas_import_commits_update BEFORE UPDATE ON cas_import_commits BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END\n;\nCREATE TRIGGER mw_writer_v6_cas_import_commits_delete BEFORE DELETE ON cas_import_commits BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;\n         END;\n"



class StorageSchemaSixControls(unittest.TestCase):
    """Actual SQLite layout controls, not Rust or installed-application evidence."""

    def database(self, *, version=6, mutate=None):
        temporary = tempfile.TemporaryDirectory(prefix="mw-schema-six-", dir=Path(__file__).resolve().parents[2])
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "state.sqlite3"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript(SCHEMA_SIX_V5_SQL)
            connection.execute("INSERT INTO cas_store_identity VALUES(1,?)",
                               (StorageCasSchemaFiveControls.store_id,))
            connection.executescript(SCHEMA_SIX_MIGRATION_SQL)
            connection.execute("PRAGMA user_version=" + str(version))
            if mutate is not None:
                mutate(connection)
        return path

    def inspect(self, path, *, version=6, error=None):
        original = path.read_bytes()
        selected = cohort(version)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            if error is None:
                storage.validate_schema_layout(connection, selected)
            else:
                with self.assertRaisesRegex(storage.StorageContractError, error):
                    storage.validate_schema_layout(connection, selected)
        self.assertEqual(path.read_bytes(), original)

    def test_six_is_explicit_and_does_not_change_document_or_receipt_representation(self):
        selected = cohort(6)
        self.assertEqual(selected["storage_schema"], 6)
        self.assertEqual(selected["document_schema_after_save"], 2)
        self.assertEqual(selected["receipt_family"], "v1-with-legacy-zero-null")
        self.assertEqual(storage.expected_schema(None), 3)
        storage.validate_receipt_columns(receipt_columns(6), selected)

    def test_exact_full_six_layout_is_read_only_without_writer_function(self):
        self.inspect(self.database())

    def test_five_cannot_be_relabelled_six_without_budget_and_fences(self):
        temporary = tempfile.TemporaryDirectory(prefix="mw-five-not-six-", dir=Path(__file__).resolve().parents[2])
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "state.sqlite3"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript(SCHEMA_SIX_V5_SQL)
            connection.execute("INSERT INTO cas_store_identity VALUES(1,?)",
                               (StorageCasSchemaFiveControls.store_id,))
            connection.execute("PRAGMA user_version=6")
        self.inspect(path, error="storage_schema6_(budget|writer)_objects_differ")

    def test_six_cannot_be_relabelled_historical_five(self):
        self.inspect(self.database(version=5), version=5,
                     error="historical_storage_has_reserved_budget_objects")

    def test_every_missing_writer_fence_is_rejected(self):
        for table in ("projects", "receipts", "events", "production_receipts",
                      "production_receipt_order", "render_window_cache_entries",
                      "render_window_cache_evictions", "cas_store_identity", "cas_import_commits"):
            for event in ("insert", "update", "delete"):
                name = "mw_writer_v6_" + table + "_" + event
                with self.subTest(name=name):
                    self.inspect(self.database(mutate=lambda c, name=name: c.execute("DROP TRIGGER " + name)),
                                 error="storage_schema6_writer_objects_differ")

    def test_null_unsafe_or_omitted_guard_predicates_are_not_canonical(self):
        def change(connection, replacement):
            name = "mw_writer_v6_projects_update"
            sql = connection.execute("SELECT sql FROM sqlite_schema WHERE name=?", (name,)).fetchone()[0]
            connection.execute("DROP TRIGGER " + name)
            connection.execute(sql.replace("IS NOT 6", replacement))
        for replacement in ("<> 6", "IS NOT 5", "IS NOT NULL"):
            with self.subTest(replacement=replacement):
                self.inspect(self.database(mutate=lambda c, value=replacement: change(c, value)),
                             error="storage_schema6_writer_ddl_differs")

    def test_wrong_table_or_extra_named_writer_fence_is_rejected(self):
        def wrong_table(connection):
            sql = connection.execute("SELECT sql FROM sqlite_schema WHERE name='mw_writer_v6_projects_update'").fetchone()[0]
            connection.execute("DROP TRIGGER mw_writer_v6_projects_update")
            connection.execute(sql.replace("ON projects", "ON receipts"))
        self.inspect(self.database(mutate=wrong_table), error="storage_schema6_writer_objects_differ")
        self.inspect(self.database(mutate=lambda c: c.execute(
            "CREATE TRIGGER mw_writer_v6_extra BEFORE UPDATE ON projects BEGIN SELECT 1; END")),
            error="storage_schema6_writer_objects_differ")

    def test_all_budget_objects_are_required(self):
        for name in ("production_operation_roots", "production_operation_debits",
                     "production_operation_dispatch_gate", "production_operation_root_immutable",
                     "production_operation_debit_immutable", "production_operation_root_nodelete",
                     "production_operation_debit_nodelete", "production_operation_debit_budget_gate"):
            def drop(connection, name=name):
                kind = connection.execute("SELECT type FROM sqlite_schema WHERE name=?", (name,)).fetchone()[0]
                connection.execute("DROP " + kind + " " + name)
            with self.subTest(name=name):
                self.inspect(self.database(mutate=drop), error="storage_schema6_budget_objects_differ")

    def test_budget_predicate_and_quoted_literal_semantics_are_preserved(self):
        def changed(connection, before, after):
            name = "production_operation_dispatch_gate"
            sql = connection.execute("SELECT sql FROM sqlite_schema WHERE name=?", (name,)).fetchone()[0]
            connection.execute("DROP TRIGGER " + name)
            connection.execute(sql.replace(before, after))
        for before, after in (("NEW.stage='dispatching'", "NEW.stage='DISPATCHING'"),
                              ("d.request_sha256=NEW.request_sha256", "1=1")):
            with self.subTest(after=after):
                self.inspect(self.database(mutate=lambda c, a=before, b=after: changed(c, a, b)),
                             error="storage_schema6_budget_ddl_differs")

    def test_extra_budget_or_cas_objects_do_not_hide_behind_correct_counts(self):
        for sql, error in (
            ("CREATE INDEX unexpected_budget_index ON production_operation_roots(generation)",
             "storage_schema6_budget_objects_differ"),
            ("CREATE TABLE production_operation_extra(id TEXT)", "storage_schema6_budget_objects_differ"),
            ("CREATE TRIGGER unexpected_cas BEFORE UPDATE ON cas_store_identity BEGIN SELECT 1; END",
             "storage_cas_objects_differ"),
        ):
            with self.subTest(sql=sql):
                self.inspect(self.database(mutate=lambda c, sql=sql: c.execute(sql)), error=error)

    def test_six_still_requires_cas_singleton_identity_and_exact_receipt_shape(self):
        def identity(connection):
            connection.create_function("motionwright_writer_epoch_guard_v6", 0, lambda: 6)
            connection.execute("DELETE FROM cas_store_identity")
        self.inspect(self.database(mutate=identity), error="storage_cas_identity_not_singular")
        self.inspect(self.database(mutate=lambda c: c.execute("ALTER TABLE receipts DROP COLUMN apply_binding_json")),
                     error="storage_binding_columns_missing")

    def test_missing_module_provenance_fails_even_with_valid_lib_declaration(self):
        selected = cohort(6)
        observed, raw = observed_source(6)
        with self.assertRaisesRegex(storage.StorageContractError, "storage_schema6_source_not_observed"):
            storage.validate_schema_six_source(selected, observed, b"budget")

    def test_module_provenance_binds_same_source_tree_git_and_physical_bytes(self):
        raw = b"reviewed budget source\n"
        observed, ignored = observed_source(6)
        original = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        observed["files"]["crates/storage/src/root_operation_budget.rs"] = {
            "git_bytes": original["bytes"], "git_sha256": original["sha256"],
            "checkout": original, "checkout_encoding": "EXACT_GIT_BYTES"}
        with mock.patch.object(storage, "BUDGET_SOURCE_GIT_BYTES", len(raw)), \
             mock.patch.object(storage, "BUDGET_SOURCE_GIT_SHA256", original["sha256"]):
            storage.validate_schema_six_source(cohort(6), observed, raw)
            with self.assertRaisesRegex(storage.StorageContractError, "storage_schema6_source_original_changed"):
                storage.validate_schema_six_source(cohort(6), observed, raw + b"changed")
            for key in ("sha", "tree"):
                wrong = copy.deepcopy(observed)
                wrong[key] = "c" * 40
                with self.subTest(key=key), self.assertRaisesRegex(
                        storage.StorageContractError, "storage_schema6_source_identity_mismatch"):
                    storage.validate_schema_six_source(cohort(6), wrong, raw)

    def test_unknown_version_seven_still_fails_source_admission(self):
        with self.assertRaisesRegex(storage.StorageContractError, "storage_cohort_version_unsupported"):
            cohort(7)


if __name__ == "__main__":
    unittest.main()
