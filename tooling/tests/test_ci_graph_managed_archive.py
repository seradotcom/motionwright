"""Inert SQLite/CAS archive-shape controls; never a Native or acceptance receipt."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "ci_canvas_service.py"
SPEC = importlib.util.spec_from_file_location("graph_ci_managed_archive", SOURCE)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def managed_entries():
    original = Path(tempfile.gettempdir()).resolve() / "inert-graph-archive" / "store"
    root_id = "11111111-2222-4333-8444-555555555555"
    control = ".motionwright-cas-" + root_id
    anchor = {"format_version": 1, "root_id": root_id,
              "canonical_root": str(original / "blobs"), "control": str(original / control),
              "directory_id": "inert-directory", "control_id": "inert-control", "lock_id": "inert-lock"}
    directory = lambda name: {"path": name, "kind": "directory"}
    def file(name, size):
        return {"path": name, "kind": "file", "sizeBytes": size,
                "sha256": hashlib.sha256(name.encode()).hexdigest()}
    entries = [directory(""), file("motionwright.sqlite3", 4096), directory("blobs"),
               file("blobs/.motionwright-cas-root-v1.json", 512),
               directory(control), directory(control + "/locks"),
               file(control + "/root.lock", 0), file(control + "/catalog.json", 128)]
    return original, anchor, entries


def with_saved_blob():
    original, anchor, entries = managed_entries()
    name = "ab" + "0" * 62
    entries.extend([{"path": "blobs/sha256", "kind": "directory"},
                    {"path": "blobs/sha256/ab", "kind": "directory"},
                    {"path": "blobs/sha256/ab/" + name, "kind": "file", "sizeBytes": 151,
                     "sha256": hashlib.sha256(b"inert-source-vector").hexdigest()}])
    return original, anchor, entries


class GraphManagedStoreArchiveTests(unittest.TestCase):
    def verify(self, triple):
        original, anchor, entries = triple
        return gate.graph_verify_managed_store_entries(entries, anchor, original)

    def test_real_managed_layout_contract_cannot_fit_the_legacy_five_member_limit(self):
        triple = managed_entries()
        self.assertEqual(len(triple[2]), 8)
        result = self.verify(triple)
        self.assertEqual(result, {"sqlite_files": 1, "sqlite_bytes": 4096,
                                  "cas_files": 3, "cas_bytes": 640})

    def test_saved_blob_adds_cas_files_and_directories_without_using_sqlite_slots(self):
        triple = with_saved_blob()
        self.assertEqual(len(triple[2]), 11)
        result = self.verify(triple)
        self.assertEqual((result["sqlite_files"], result["cas_files"]), (1, 4))
        self.assertEqual(result["cas_bytes"], 791)

    def test_full_sqlite_and_cas_byte_slices_remain_separate(self):
        original, anchor, entries = with_saved_blob()
        entries[1]["sizeBytes"] = 4 * gate.GRAPH_BYTES
        for suffix in ("-wal", "-shm", "-journal"):
            entries.append({**entries[1], "path": "motionwright.sqlite3" + suffix})
        entries[-4]["sizeBytes"] = 4 * gate.GRAPH_BYTES - 640
        result = self.verify((original, anchor, entries))
        self.assertEqual(result["sqlite_files"], 4)
        self.assertEqual(result["sqlite_bytes"], 16 * gate.GRAPH_BYTES)
        self.assertEqual(result["cas_bytes"], 4 * gate.GRAPH_BYTES)
        for name, index in (("sqlite", 1), ("cas", 10)):
            changed = copy.deepcopy(entries)
            changed[index]["sizeBytes"] += 1
            with self.subTest(component=name), self.assertRaises(ValueError):
                self.verify((original, anchor, changed))

    def test_cas_file_and_complete_tree_member_limits_are_independent(self):
        original, anchor, entries = managed_entries()
        control = ".motionwright-cas-" + anchor["root_id"]
        for n in range(13):
            entries.append({"path": control + "/locks/" + f"{n:08x}-0000-0000-0000-000000000000.lock",
                            "kind": "file", "sizeBytes": 0, "sha256": "0" * 64})
        self.assertEqual(self.verify((original, anchor, entries))["cas_files"], 16)
        extra = {"path": control + "/locks/ffffffff-0000-0000-0000-000000000000.lock",
                 "kind": "file", "sizeBytes": 0, "sha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "CAS footprint"):
            self.verify((original, anchor, entries + [extra]))
        original, anchor, entries = managed_entries()
        entries.append({"path": "blobs/sha256", "kind": "directory"})
        entries.extend({"path": f"blobs/sha256/{n:02x}", "kind": "directory"} for n in range(33))
        self.assertEqual(len(entries), 42)
        with self.assertRaisesRegex(ValueError, "complete tree bound"):
            self.verify((original, anchor, entries))

    def test_unknown_traversing_duplicate_and_mismatched_blob_paths_are_denied(self):
        for name in ("foreign.txt", "../escape", "blobs//extra", "blobs\\extra",
                     "/absolute", "blobs/.staging/unadmitted", "motionwright.sqlite3",
                     "blobs/sha256/ab/" + "cd" + "0" * 62):
            original, anchor, entries = with_saved_blob()
            entries.append({"path": name, "kind": "file", "sizeBytes": 1, "sha256": "0" * 64})
            with self.subTest(path=name), self.assertRaises(ValueError):
                self.verify((original, anchor, entries))

    def test_anchor_preserves_the_original_store_paths_and_identity_fields(self):
        for mutation in ("canonical_root", "control", "root_id", "format_version",
                         "directory_id", "control_id", "lock_id", "extra"):
            original, anchor, entries = managed_entries()
            if mutation == "extra": anchor["invented"] = True
            elif mutation == "format_version": anchor[mutation] = True
            else: anchor[mutation] = ""
            with self.subTest(field=mutation), self.assertRaises(ValueError):
                self.verify((original, anchor, entries))

    def test_missing_managed_roots_wrong_kinds_and_invalid_file_identity_are_denied(self):
        original, anchor, entries = managed_entries()
        for index in range(len(entries)):
            changed = entries[:index] + entries[index + 1:]
            with self.subTest(missing=entries[index]["path"]), self.assertRaises(ValueError):
                self.verify((original, anchor, changed))
        for field, value in (("kind", "symlink"), ("sizeBytes", True), ("sizeBytes", -1),
                             ("sizeBytes", 1.5), ("sha256", "changed")):
            changed = copy.deepcopy(entries)
            changed[1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.verify((original, anchor, changed))


if __name__ == "__main__":
    unittest.main()
