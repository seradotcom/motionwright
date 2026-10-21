"""Pure source/cohort controls; no application or native process is launched."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
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
    if version == 4:
        result.extend([(5, "apply_binding_version", "INTEGER", 1, "0", 0, 0),
                       (6, "apply_binding_json", "TEXT", 0, None, 0, 0)])
    return result


class StorageCohortControls(unittest.TestCase):
    def test_each_supported_source_has_a_separate_storage_and_document_contract(self):
        for version in (2, 3, 4):
            with self.subTest(version=version):
                value = cohort(version)
                self.assertEqual(value["storage_schema"], version)
                self.assertEqual(value["document_schema_after_save"], 2)
                self.assertEqual(storage.validate_cohort(value), value)
        self.assertEqual(cohort(4)["receipt_family"], "v1-with-legacy-zero-null")

    def test_absence_retains_historical_defaults_and_cannot_mean_current_four(self):
        self.assertEqual(storage.expected_schema(None), 3)
        self.assertEqual(storage.expected_schema(None, historical_default=2), 2)
        for value in (4, True, "4", None):
            with self.subTest(default=value), self.assertRaises(storage.StorageContractError):
                storage.expected_schema(None, historical_default=value)

    def test_source_and_tree_are_explicit_and_cross_cohort_records_fail(self):
        value = cohort(4)
        for kwargs in ({"source_sha": "c" * 40}, {"source_tree": "c" * 40}):
            with self.subTest(kwargs=kwargs), self.assertRaises(storage.StorageContractError):
                storage.validate_cohort(value, **kwargs)
        for field, replacement in (("storage_schema", True), ("storage_schema", 5),
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
                    b"pub const STORAGE_SCHEMA_VERSION: u32 = 5;\n",
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
        import windows_nsis_interruption as observer
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
        import windows_nsis_interruption as observer
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
        storage.validate_receipt_columns(receipt_columns(4), cohort(4))
        with self.assertRaises(storage.StorageContractError):
            storage.validate_receipt_columns(receipt_columns(3), cohort(4))

    def test_schema_four_rejects_alias_hidden_column_type_and_default_changes(self):
        for index, field, value in ((5, 1, "APPLY_BINDING_VERSION"), (5, 2, "TEXT"),
                                    (5, 3, 0), (5, 4, "1"), (5, 6, 2),
                                    (6, 1, "APPLY_BINDING_JSON"), (6, 2, "BLOB"),
                                    (6, 3, 1), (6, 6, 3)):
            columns = [list(x) for x in receipt_columns()]
            columns[index][field] = value
            with self.subTest(index=index, field=field), self.assertRaises(storage.StorageContractError):
                storage.validate_receipt_columns(columns, cohort(4))

    def test_missing_duplicate_or_malformed_column_observations_fail(self):
        for columns in ([], [("bad",)], receipt_columns() * 2):
            with self.subTest(columns=columns), self.assertRaises(storage.StorageContractError):
                storage.validate_receipt_columns(columns, cohort(4))

    def test_document_two_is_not_replaced_by_the_storage_version(self):
        storage.validate_document({"schema_version": 2}, cohort(4))
        storage.validate_document({"schema_version": 1}, cohort(2))
        for document in ({}, {"schema_version": 4}, {"schema_version": True},
                         {"schema_version": "2"}):
            with self.subTest(document=document), self.assertRaises(storage.StorageContractError):
                storage.validate_document(document, cohort(4))



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


if __name__ == "__main__":
    unittest.main()
