"""Inert, no-execution DT_NEEDED, SONAME, RPATH and graph candidate tests."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_elf_dynamic_needed as needed

ROOT_VADDR = 0x400000
LOAD_OFF = 64 + 2 * needed.PHDR.size


def fixture(*, dependencies=("libexample.so.1",),
            soname="libours.so", runpath="$ORIGIN",
            rpath=None, include_null=True):
    names = [*dependencies]
    if soname is not None:
        names.append(soname)
    if runpath is not None:
        names.append(runpath)
    if rpath is not None:
        names.append(rpath)
    table = bytearray(b"\x00")
    offsets = {}
    for name in names:
        offsets.setdefault(name, len(table))
        table.extend(name.encode("utf-8") + b"\x00")
    count = 2 + len(dependencies) + (
        (soname is not None) + (runpath is not None) + (rpath is not None))
    count += int(include_null)
    dyn_size = count * needed.DYN.size
    str_off = LOAD_OFF + dyn_size
    rows = [(needed.DT_STRTAB, ROOT_VADDR + str_off),
            (needed.DT_STRSZ, len(table))]
    rows += [(needed.DT_NEEDED, offsets[n]) for n in dependencies]
    if soname is not None:
        rows.append((needed.DT_SONAME, offsets[soname]))
    if runpath is not None:
        rows.append((needed.DT_RUNPATH, offsets[runpath]))
    if rpath is not None:
        rows.append((needed.DT_RPATH, offsets[rpath]))
    if include_null:
        rows.append((needed.DT_NULL, 0))
    full = bytearray(str_off + len(table))
    full[0:4] = b"\x7fELF"
    full[4:7] = bytes((2, 1, 1))
    struct.pack_into("<HHI", full, 16, 3, 62, 1)
    struct.pack_into("<Q", full, 32, 64)
    struct.pack_into("<HHH", full, 52, 64, 56, 2)
    struct.pack_into("<IIQQQQQQ", full, 64,
                     needed.PT_LOAD, 5, 0, ROOT_VADDR, ROOT_VADDR,
                     len(full), len(full), 4096)
    struct.pack_into("<IIQQQQQQ", full, 64 + needed.PHDR.size,
                     needed.PT_DYNAMIC, 4, LOAD_OFF,
                     ROOT_VADDR + LOAD_OFF, 0, dyn_size, dyn_size, 8)
    for i, (tag, value) in enumerate(rows):
        needed.DYN.pack_into(full, LOAD_OFF + i * needed.DYN.size,
                             tag, value)
    full[str_off:] = table
    return bytes(full)


def descriptor(raw, path="usr/lib/libours.so"):
    return {"path": path, "kind": "file", "native_format": "ELF",
            **needed.digest(raw)}


class DynamicNeededSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(
            prefix="mw-elf-dt-needed-", dir=os.environ.get("TEMP"))
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(strict=True)
        self.raw = fixture()
        self.path = self.root / "usr/lib/libours.so"
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(self.raw)
        self.original = {
            "context": {
                "kind": "appimage", "platform": "linux", "arch": "x86_64",
            },
            "custody": {"status": "DIRECT_CAPTURED_EXTRACTION"},
            "roots": [{"role": "payload",
                       "members": [descriptor(self.raw)]}],
        }

    def scan(self, **kwargs):
        with mock.patch.object(needed.inventory, "validate", return_value=None):
            return needed.scan_original(self.root, self.original, **kwargs)

    def test_valid_elf_needs_soname_and_runpath(self):
        value = needed.dynamic_needed(self.raw)
        self.assertEqual(value["needed"], ["libexample.so.1"])
        self.assertEqual(value["soname"], "libours.so")
        self.assertEqual(value["runpath"], "$ORIGIN")
        self.assertIsNone(value["rpath"])

    def test_zero_dynamic_is_not_false_closure(self):
        raw = bytearray(self.raw)
        struct.pack_into("<I", raw, 64 + needed.PHDR.size, 4)
        found = needed.dynamic_needed(bytes(raw))
        self.assertEqual(found["status"], "NO_PT_DYNAMIC")
        self.assertEqual(found["needed"], [])

    def test_non_elf_never_accepted_as_elf(self):
        with self.assertRaisesRegex(ValueError, "not_elf"):
            needed.dynamic_needed(b"fake ELF notice")

    def test_unsupported_arch_is_rejected(self):
        raw = bytearray(self.raw)
        struct.pack_into("<H", raw, 18, 3)
        with self.assertRaisesRegex(ValueError, "unsupported_header"):
            needed.dynamic_needed(bytes(raw))

    def test_invalid_program_header_location_fails(self):
        raw = bytearray(self.raw)
        struct.pack_into("<Q", raw, 32, len(raw) + 4)
        with self.assertRaisesRegex(ValueError, "unsupported_header"):
            needed.dynamic_needed(bytes(raw))

    def test_truncated_dynamic_segment_fails(self):
        raw = bytearray(self.raw)
        struct.pack_into("<Q", raw, 64 + needed.PHDR.size + 32,
                         len(raw) + 10)
        with self.assertRaisesRegex(ValueError, "segment_out_of_bounds"):
            needed.dynamic_needed(bytes(raw))

    def test_dynamic_must_have_null_terminator(self):
        with self.assertRaisesRegex(ValueError, "without_null"):
            needed.dynamic_needed(fixture(include_null=False))

    def test_duplicate_needed_name_is_not_silently_deduped(self):
        with self.assertRaisesRegex(ValueError, "duplicate_dependency"):
            needed.dynamic_needed(fixture(dependencies=("libsame.so",
                                                        "libsame.so")))

    def test_slash_in_requested_soname_not_a_resolved_library(self):
        with self.assertRaisesRegex(ValueError, "unsafe_soname"):
            needed.dynamic_needed(fixture(dependencies=("../escape.so",)))

    def test_rpath_and_runpath_are_labels_not_executed(self):
        row = needed.dynamic_needed(fixture(
            dependencies=("liba.so",), rpath="/usr/lib",
            runpath="$ORIGIN:$ORIGIN/private"))
        self.assertEqual(row["rpath"], "/usr/lib")
        self.assertEqual(row["runpath"], "$ORIGIN:$ORIGIN/private")

    def test_multiple_soname_tags_refused(self):
        raw = bytearray(fixture(dependencies=(),
                                soname="libself.so", runpath=None))
        dyn = LOAD_OFF
        soname_row = dyn + 2 * needed.DYN.size
        null_row = dyn + 3 * needed.DYN.size
        original_offset = needed.DYN.unpack_from(raw, soname_row)[1]
        needed.DYN.pack_into(raw, null_row, needed.DT_SONAME,
                             original_offset)
        with self.assertRaisesRegex(ValueError, "duplicate_single_tag"):
            needed.dynamic_needed(bytes(raw))

    def test_strtab_address_outside_load_fails(self):
        raw = bytearray(self.raw)
        needed.DYN.pack_into(raw, LOAD_OFF, needed.DT_STRTAB,
                             0x90000000)
        with self.assertRaisesRegex(ValueError, "unmapped"):
            needed.dynamic_needed(bytes(raw))

    def test_strtab_byte_limit_is_strict(self):
        raw = bytearray(self.raw)
        needed.DYN.pack_into(raw, LOAD_OFF + needed.DYN.size,
                             needed.DT_STRSZ, needed.MAX_STRTAB + 1)
        with self.assertRaisesRegex(ValueError, "string_table_missing"):
            needed.dynamic_needed(bytes(raw))

    def test_same_run_live_member_hash_verified_before_parsing(self):
        result = self.scan()
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["total_bytes"], len(self.raw))
        self.assertEqual(result["entries"][0]["dynamic"]["soname"],
                         "libours.so")
        self.assertEqual(result["entries"][0]["identity"]["sha256"],
                         hashlib.sha256(self.raw).hexdigest())

    def test_same_length_changed_member_fails(self):
        changed = bytearray(self.raw)
        changed[-2] ^= 1
        self.path.write_bytes(changed)
        with self.assertRaisesRegex(ValueError,
                                     "live_payload_different_from_original"):
            self.scan()

    def test_missing_native_member_fails(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.scan()

    def test_roster_duplicate_filename_is_not_graph_credit(self):
        self.original["roots"][0]["members"].append(
            descriptor(self.raw))
        with self.assertRaisesRegex(ValueError,
                                     "duplicate_or_invalid_path"):
            self.scan()

    def test_total_budget_is_enforced_before_read(self):
        with self.assertRaisesRegex(ValueError, "total_budget"):
            self.scan(max_bytes=len(self.raw) - 1)

    def test_unique_and_ambiguous_soname_edges_are_scoped(self):
        payload = self.scan()
        item = payload["entries"][0]
        item["dynamic"]["needed"] = ["libours.so", "missing.so"]
        matches = needed.candidate_edges(payload)
        self.assertEqual(matches["counts"],
                         {"ONE_BYTE_IDENTITY_CANDIDATE": 1,
                          "NOT_OBSERVED_IN_BUNDLE": 1})
        self.assertFalse(matches["assertions"][
            "loader_search_path_or_namespace_checked"])
        self.assertEqual(matches["assertions"]["G3"], "STOP")
        second = {
            "member": "usr/lib/other/libours.so",
            "identity": {"sha256": "f" * 64, "bytes": 100},
            "dynamic": {"soname": "libours.so", "needed": [],
                        "runpath": None, "rpath": None},
        }
        payload["entries"].append(second)
        ambiguity = needed.candidate_edges(payload)
        self.assertEqual(ambiguity["counts"]["AMBIGUOUS_DIFFERENT_BYTES"], 1)

    def test_no_exec_and_no_license_authority_in_source(self):
        self.assertEqual(needed.SCHEMA,
                         "motionwright.linux-appimage-elf-needed-survey/1")
        record = needed.candidate_edges(self.scan())
        self.assertFalse(record["assertions"][
            "dynamic_dlopen_and_plugins_observed"])
        self.assertEqual(record["assertions"]["native_graph_closure"],
                         "NOT_VERIFIED")
        self.assertEqual(record["assertions"]["G2"], "NOT_GRANTED")


if __name__ == "__main__":
    unittest.main()
