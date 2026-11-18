"""Inert ELF build-id parser and declared installed library custody regressions."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_elf_build_id as elf


def fixture(build_id=b"\x13" * 20, *, wrong_machine=False, truncate=False,
            note_name=b"GNU\x00", section_kind=4, duplicate=False):
    hdr = bytearray(64)
    hdr[:4] = b"\x7fELF"
    hdr[4:7] = bytes((2, 1, 1))
    struct.pack_into("<HHI", hdr, 16, 3, 3 if wrong_machine else 62, 1)
    struct.pack_into("<Q", hdr, 32, 64)
    struct.pack_into("<HH", hdr, 52, 64, 56)
    struct.pack_into("<H", hdr, 56, 2 if duplicate else 1)
    desc = build_id
    name = note_name
    note = struct.pack("<III", len(name), len(desc), 3)
    note += name + b"\x00" * ((-len(name)) % 4)
    note += desc + b"\x00" * ((-len(desc)) % 4)
    note_at = 64 + 56 * (2 if duplicate else 1)
    phdr = struct.pack("<IIQQQQQQ", section_kind, 4, note_at, 0, 0,
                       len(note), len(note), 4)
    out = bytes(hdr) + phdr + (phdr if duplicate else b"") + note
    return out[:-1] if truncate else out


class ElfBuildIdTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="mw-elf-identity-", dir=os.environ.get("TEMP"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(strict=True)
        self.src = "/usr/lib/x86_64-linux-gnu/libexample.so.1"
        self.destination = self.root / self.src.lstrip("/")
        self.destination.parent.mkdir(parents=True)
        self.raw = fixture()
        self.destination.write_bytes(self.raw)

    def test_single_elf_ceiling_covers_original_webkit_size_without_unbounded_reads(self):
        self.assertEqual(elf.MAX_BINARY, 128 * 1024 * 1024)
        self.assertGreater(elf.MAX_BINARY, 95_202_521)
        self.assertLessEqual(elf.MAX_BINARY, 128 * 1024 * 1024)

    def test_gnu_build_id_for_genuine_elf_note(self):
        self.assertEqual(elf.gnu_build_id(fixture()), "13" * 20)

    def test_non_elf_does_not_synthesize_note(self):
        self.assertIsNone(elf.gnu_build_id(b"not an ELF\x00" * 10))

    def test_non_x86_64_note_never_matches(self):
        self.assertIsNone(elf.gnu_build_id(fixture(wrong_machine=True)))

    def test_wrong_pt_type_does_not_supply_build_id(self):
        self.assertIsNone(elf.gnu_build_id(fixture(section_kind=1)))

    def test_truncated_elf_segment_refused(self):
        with self.assertRaisesRegex(ValueError, "elf_note_bounds"):
            elf.gnu_build_id(fixture(truncate=True))

    def test_ambiguous_build_id_refused(self):
        with self.assertRaisesRegex(ValueError, "elf_ambiguous_build_id"):
            elf.gnu_build_id(fixture(duplicate=True))

    def test_gnu_name_is_required_exactly(self):
        self.assertIsNone(elf.gnu_build_id(fixture(note_name=b"FAKE")))

    def test_short_build_id_refused(self):
        with self.assertRaisesRegex(ValueError, "elf_gnu_build_id_length"):
            elf.gnu_build_id(fixture(build_id=b"\x41" * 8))

    def test_stable_installed_regular_library_binds_full_bytes(self):
        actual = elf.observed_library(self.root, self.src)
        self.assertEqual(actual["sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(actual["bytes"], len(self.raw))
        self.assertEqual(actual["gnu_build_id"], "13" * 20)
        self.assertEqual(actual["declared_path"], self.src)
        self.assertFalse(actual["alias_symlink"])

    def test_identical_note_does_not_imply_identical_bytes(self):
        modified = self.raw + b"\x01\x01\x01"
        self.assertEqual(elf.gnu_build_id(modified), "13" * 20)
        self.assertNotEqual(hashlib.sha256(modified).hexdigest(),
                            hashlib.sha256(self.raw).hexdigest())

    def test_missing_source_is_not_positive_evidence(self):
        self.destination.unlink()
        self.assertIsNone(elf.observed_library(self.root, self.src))

    def test_maximum_byte_budget_prevents_read(self):
        self.assertIsNone(elf.observed_library(self.root, self.src, limit=100))

    def test_redirected_host_library_link_must_stay_inside_root(self):
        self.destination.unlink()
        external = self.root.parent / ("escape-" + self.root.name + ".so")
        external.write_bytes(self.raw)
        self.addCleanup(lambda: external.unlink(missing_ok=True))
        try:
            self.destination.symlink_to(external)
        except OSError as exc:
            self.skipTest("symlink creation permission unavailable: " + str(exc))
        with self.assertRaises(ValueError):
            elf.observed_library(self.root, self.src)

    def test_valid_relative_symlink_keeps_original_alias_and_target(self):
        self.destination.rename(self.destination.with_name("libexample.so.1.real"))
        try:
            self.destination.symlink_to("libexample.so.1.real")
        except OSError as exc:
            self.skipTest("symlink creation permission unavailable: " + str(exc))
        observed = elf.observed_library(self.root, self.src)
        self.assertTrue(observed["alias_symlink"])
        self.assertEqual(observed["gnu_build_id"], "13" * 20)

    def test_parent_directory_symlink_escape_refused(self):
        other = self.root / "escaped"
        other.mkdir()
        parent = self.root / "usr/lib"
        try:
            parent.rename(self.root / "usr/lib-real")
            parent.symlink_to(other, target_is_directory=True)
        except OSError as exc:
            self.skipTest("symlink creation permission unavailable: " + str(exc))
        self.assertIsNone(elf.observed_library(self.root, self.src))


if __name__ == "__main__":
    unittest.main()
