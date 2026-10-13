"""Fast, isolated SQLite contract test for revision-bound cache revocation.

This does not exercise Rust, native renderer output or filesystem deletion.
The Rust integration suite and source-pinned CI provide those separate gates.
"""
from __future__ import annotations

import pathlib
import sqlite3
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "crates/storage/src/lib.rs"


def storage_migration_sql() -> str:
    text = SOURCE.read_text(encoding="utf-8")
    marker = 'conn.execute_batch(\n            r#"'
    begin = text.index(marker) + len(marker)
    end = text.index('"#,\n        )?;', begin)
    return text[begin:end]


class RenderWindowSQLiteGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(storage_migration_sql())
        self.db.execute(
            "INSERT INTO projects(id,generation,revision,title,document_json,updated_at)"
            " VALUES('p','gen-a',1,'fixture','{}','now')"
        )
        self.db.execute(
            "INSERT INTO render_window_cache_entries(project_id,generation,revision,"
            "deliverable_id,source_start,source_end,fingerprint_sha256)"
            " VALUES('p','gen-a',1,'profile',0,60,?)",
            ("a" * 64,),
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def count(self, table: str) -> int:
        return self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_commit_revokes_and_records_recoverable_tombstone(self) -> None:
        self.db.execute("UPDATE projects SET revision=2 WHERE id='p'")
        self.db.commit()
        self.assertEqual(self.count("render_window_cache_entries"), 0)
        self.assertEqual(self.count("render_window_cache_evictions"), 1)
        tombstone = self.db.execute(
            "SELECT generation,revision,source_start,source_end "
            "FROM render_window_cache_evictions"
        ).fetchone()
        self.assertEqual(tombstone, ("gen-a", 1, 0, 60))

    def test_rollback_preserves_active_cache_and_records_nothing(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("UPDATE projects SET revision=2 WHERE id='p'")
        self.assertEqual(self.count("render_window_cache_entries"), 0)
        self.db.rollback()
        self.assertEqual(self.count("render_window_cache_entries"), 1)
        self.assertEqual(self.count("render_window_cache_evictions"), 0)

    def test_metadata_update_without_generation_or_revision_change_does_not_evict(self) -> None:
        self.db.execute("UPDATE projects SET title='renamed only' WHERE id='p'")
        self.db.commit()
        self.assertEqual(self.count("render_window_cache_entries"), 1)
        self.assertEqual(self.count("render_window_cache_evictions"), 0)

    def test_distinct_project_is_not_evicted_by_first_project_commit(self) -> None:
        self.db.execute(
            "INSERT INTO projects(id,generation,revision,title,document_json,updated_at)"
            " VALUES('second','gen-b',1,'fixture','{}','now')"
        )
        self.db.execute(
            "INSERT INTO render_window_cache_entries(project_id,generation,revision,"
            "deliverable_id,source_start,source_end,fingerprint_sha256)"
            " VALUES('second','gen-b',1,'profile',0,60,?)",
            ("b" * 64,),
        )
        self.db.commit()
        self.db.execute("UPDATE projects SET revision=2 WHERE id='p'")
        self.db.commit()
        rows = self.db.execute(
            "SELECT project_id FROM render_window_cache_entries"
        ).fetchall()
        self.assertEqual(rows, [("second",)])
        self.assertEqual(self.count("render_window_cache_evictions"), 1)


if __name__ == "__main__":
    unittest.main()
