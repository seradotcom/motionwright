"""Source-only custody and tamper tests; no Tauri compilation or execution."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import v01_tauri_ipc_product_admission as admission

REPOSITORY = Path(__file__).resolve().parents[2]
BASE_MANIFEST = """[workspace]
members = []
[patch.crates-io]
glib = { path = "vendor/glib-0.18.5" }
tauri = { path = "vendor/tauri-2.12.1" }
"""
BASE_LOCK = """version = 4
[[package]]
name = "tauri"
version = "2.12.1"
"""
BASE_DESKTOP = """[package]
name = "motionwright-desktop"
version = "0.1.0"
[dependencies]
tauri = { version = "=2.12.1", features = [] }
"""


class TauriProductAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.archive = (REPOSITORY / admission.UPSTREAM).read_bytes()
        if hashlib.sha256(cls.archive).hexdigest() != admission.UPSTREAM_SHA256:
            raise AssertionError("Test input is not the retained official Tauri crate")
        vendor = REPOSITORY / admission.VENDOR
        cls.source = {p.relative_to(vendor).as_posix(): p.read_bytes()
                      for p in vendor.rglob("*") if p.is_file()}
        if len(cls.source) != 142:
            raise AssertionError("Test input is not the complete reviewed vendor tree")
        for name, record in admission.PATCHED_FILES.items():
            if hashlib.sha256(cls.source[name]).hexdigest() != record["after_sha256"]:
                raise AssertionError("Test input is not the reviewed three-file patch")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mw-tauri-source-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.write("Cargo.toml", BASE_MANIFEST.encode())
        self.write("Cargo.lock", BASE_LOCK.encode())
        self.write("apps/desktop/src-tauri/Cargo.toml", BASE_DESKTOP.encode())
        self.write(admission.UPSTREAM, self.archive)
        for name, data in self.source.items():
            self.write(f"{admission.VENDOR}/{name}", data)

    def write(self, relative: str, data: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def refused(self, reason: str | None = None) -> None:
        with self.assertRaises(admission.TauriSourceAdmissionError) as caught:
            admission.admit_patched_tauri(self.root)
        if reason is not None:
            self.assertEqual(str(caught.exception), reason)

    def link(self, target: Path, destination: Path, *, directory: bool = False) -> None:
        try:
            destination.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"This runner cannot create the symlink negative: {exc}")

    def test_complete_official_source_and_both_patches_are_admitted(self) -> None:
        result = admission.admit_patched_tauri(self.root)
        self.assertEqual(result["upstream_file_count"], 142)
        self.assertEqual(set(result["patched_files"]), {
            "src/webview/mod.rs", "src/ipc/protocol.rs", "src/ipc/channel.rs",
        })
        self.assertTrue(result["all_other_files_identical_to_upstream"])
        for field in ("target_compilation_verified", "runtime_diagnostic_sink_closure_verified",
                      "hostile_input_confidentiality_verified", "release_authorized"):
            self.assertIs(result[field], False)

    def test_archive_hash_rejects_same_size_bit_flip(self) -> None:
        changed = bytes([self.archive[0] ^ 1]) + self.archive[1:]
        self.write(admission.UPSTREAM, changed)
        self.refused("tauri_upstream_crate_hash_changed")

    def test_archive_missing_rejected(self) -> None:
        (self.root / admission.UPSTREAM).unlink()
        self.refused()

    def test_archive_symlink_rejected_even_when_bytes_are_authentic(self) -> None:
        path = self.root / admission.UPSTREAM
        path.unlink()
        original = self.write("authentic.crate", self.archive)
        self.link(original, path)
        self.refused("source_path_is_link_or_reparse_point")

    def test_archive_parent_symlink_rejected(self) -> None:
        path = self.root / "tooling/upstream-crates"
        target = self.root / "retained-archive"
        path.rename(target)
        self.link(target, path, directory=True)
        self.refused("source_path_is_link_or_reparse_point")

    def test_archive_size_budget_rejected_before_reading(self) -> None:
        self.write(admission.UPSTREAM, self.archive + b"x")
        self.refused("source_file_budget_exceeded")

    def test_vendor_extra_file_rejected(self) -> None:
        self.write(f"{admission.VENDOR}/extra.rs", b"unreviewed")
        self.refused("tauri_vendor_inventory_changed")

    def test_vendor_extra_empty_directory_rejected(self) -> None:
        (self.root / admission.VENDOR / "unreviewed-empty").mkdir()
        self.refused("tauri_vendor_inventory_changed")

    def test_vendor_missing_file_rejected(self) -> None:
        (self.root / admission.VENDOR / "LICENSE-MIT").unlink()
        self.refused("tauri_vendor_inventory_changed")

    def test_vendor_file_replaced_by_directory_rejected(self) -> None:
        path = self.root / admission.VENDOR / "README.md"
        path.unlink()
        path.mkdir()
        self.refused("tauri_vendor_inventory_changed")

    def test_vendor_directory_replaced_by_regular_file_rejected(self) -> None:
        path = self.root / admission.VENDOR / "src/ipc"
        shutil.rmtree(path)
        path.write_bytes(b"not a directory")
        self.refused("vendor_entry_not_regular")

    def test_vendor_root_symlink_rejected(self) -> None:
        path = self.root / admission.VENDOR
        target = self.root / "retained-vendor"
        path.rename(target)
        self.link(target, path, directory=True)
        self.refused("source_path_is_link_or_reparse_point")

    def test_vendor_parent_symlink_rejected(self) -> None:
        path = self.root / "vendor"
        target = self.root / "retained-vendor-parent"
        path.rename(target)
        self.link(target, path, directory=True)
        self.refused("source_path_is_link_or_reparse_point")

    def test_vendor_file_symlink_rejected_even_with_expected_bytes(self) -> None:
        path = self.root / admission.VENDOR / "README.md"
        expected = self.write("authentic-readme", path.read_bytes())
        path.unlink()
        self.link(expected, path)
        self.refused("vendor_entry_is_link_or_reparse_point")

    def test_vendor_broken_symlink_rejected(self) -> None:
        path = self.root / admission.VENDOR / "README.md"
        path.unlink()
        self.link(self.root / "does-not-exist", path)
        self.refused("vendor_entry_is_link_or_reparse_point")

    def test_vendor_directory_symlink_rejected(self) -> None:
        path = self.root / admission.VENDOR / "src/ipc"
        target = self.root / "retained-ipc"
        path.rename(target)
        self.link(target, path, directory=True)
        self.refused("vendor_entry_is_link_or_reparse_point")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "Runner has no FIFO creation API")
    def test_vendor_special_file_rejected(self) -> None:
        path = self.root / admission.VENDOR / "README.md"
        path.unlink()
        os.mkfifo(path)
        self.refused("vendor_entry_not_regular")

    def test_unchanged_license_same_length_tamper_rejected(self) -> None:
        original = self.source["LICENSE-MIT"]
        self.write(f"{admission.VENDOR}/LICENSE-MIT", bytes([original[0] ^ 1]) + original[1:])
        self.refused("tauri_unchanged_source_file_changed")

    def test_unchanged_rust_source_tamper_rejected(self) -> None:
        name = "src/ipc/mod.rs"
        self.write(f"{admission.VENDOR}/{name}", self.source[name] + b"\n// changed\n")
        self.refused("tauri_unchanged_source_file_changed")

    def test_each_patch_postimage_is_required_exactly(self) -> None:
        for name in admission.PATCHED_FILES:
            with self.subTest(name=name):
                original = self.source[name]
                self.write(f"{admission.VENDOR}/{name}", bytes([original[0] ^ 1]) + original[1:])
                self.refused("tauri_patch_postimage_changed")
                self.write(f"{admission.VENDOR}/{name}", original)

    def test_channel_patch_cannot_be_omitted(self) -> None:
        with tarfile.open(fileobj=io.BytesIO(self.archive), mode="r:gz") as archive:
            with archive.extractfile("tauri-2.12.1/src/ipc/channel.rs") as stream:
                self.write(f"{admission.VENDOR}/src/ipc/channel.rs", stream.read())
        self.refused("tauri_patch_postimage_changed")

    def test_cargo_override_must_be_the_reviewed_relative_path(self) -> None:
        self.write("Cargo.toml", BASE_MANIFEST.replace("vendor/tauri-2.12.1", "../other-tauri").encode())
        self.refused("unexpected_cargo_source_override")

    def test_cargo_unknown_crates_io_override_rejected(self) -> None:
        self.write("Cargo.toml", (BASE_MANIFEST + 'serde = { path = "vendor/serde" }\n').encode())
        self.refused("unexpected_cargo_source_override")

    def test_cargo_unknown_registry_override_rejected(self) -> None:
        self.write("Cargo.toml", (BASE_MANIFEST + '[patch.other]\nserde = { path = "vendor/serde" }\n').encode())
        self.refused("unexpected_cargo_source_override")

    def test_cargo_replace_cannot_override_selection(self) -> None:
        self.write("Cargo.toml", (BASE_MANIFEST + '[replace]\n"tauri:2.12.1" = { path = "elsewhere" }\n').encode())
        self.refused("unexpected_cargo_source_override")

    def test_glib_override_must_remain_exact(self) -> None:
        self.write("Cargo.toml", BASE_MANIFEST.replace("vendor/glib-0.18.5", "vendor/glib-other").encode())
        self.refused("unexpected_cargo_source_override")

    def test_lock_rejects_registry_source(self) -> None:
        self.write("Cargo.lock", (BASE_LOCK + 'source = "registry+https://github.com/rust-lang/crates.io-index"\n').encode())
        self.refused("tauri_lock_did_not_select_local_patch")

    def test_lock_rejects_checksum_without_source(self) -> None:
        self.write("Cargo.lock", (BASE_LOCK + f'checksum = "{admission.UPSTREAM_SHA256}"\n').encode())
        self.refused("tauri_lock_did_not_select_local_patch")

    def test_lock_rejects_missing_tauri(self) -> None:
        self.write("Cargo.lock", BASE_LOCK.replace('name = "tauri"', 'name = "another-crate"').encode())
        self.refused("tauri_locked_version_or_identity_changed")

    def test_lock_rejects_second_tauri_version(self) -> None:
        self.write("Cargo.lock", (BASE_LOCK + '[[package]]\nname = "tauri"\nversion = "2.11.0"\n').encode())
        self.refused("tauri_locked_version_or_identity_changed")

    def test_lock_rejects_duplicate_same_version(self) -> None:
        self.write("Cargo.lock", (BASE_LOCK + '[[package]]\nname = "tauri"\nversion = "2.12.1"\n').encode())
        self.refused("tauri_locked_version_or_identity_changed")

    def test_lock_rejects_wrong_version(self) -> None:
        self.write("Cargo.lock", BASE_LOCK.replace('version = "2.12.1"', 'version = "2.12.2"').encode())
        self.refused("tauri_locked_version_or_identity_changed")

    def test_desktop_rejects_semver_range(self) -> None:
        self.write("apps/desktop/src-tauri/Cargo.toml", BASE_DESKTOP.replace('"=2.12.1"', '"2.12.1"').encode())
        self.refused("desktop_tauri_dependency_not_exact_registry_pin")

    def test_desktop_malformed_dependency_table_rejected_with_admission_error(self) -> None:
        self.write("apps/desktop/src-tauri/Cargo.toml", b'dependencies = "invalid"\n')
        self.refused("desktop_tauri_dependency_not_exact_registry_pin")

    def test_desktop_rejects_direct_path_or_git_or_alias(self) -> None:
        for field in ('path = "elsewhere"', 'git = "https://example.invalid/tauri"', 'package = "other"'):
            with self.subTest(field=field):
                value = BASE_DESKTOP.replace('features = []', f'features = [], {field}')
                self.write("apps/desktop/src-tauri/Cargo.toml", value.encode())
                self.refused("desktop_tauri_dependency_not_exact_registry_pin")

    def test_cargo_manifest_symlink_rejected(self) -> None:
        path = self.root / "Cargo.toml"
        target = self.write("manifest-original", path.read_bytes())
        path.unlink()
        self.link(target, path)
        self.refused("source_path_is_link_or_reparse_point")

    def test_repository_root_symlink_rejected(self) -> None:
        alias = Path(self.temp.name) / "alias"
        self.link(self.root, alias, directory=True)
        self.root = alias
        self.refused("repository_root_not_regular")

    def test_invalid_cargo_toml_rejected(self) -> None:
        self.write("Cargo.toml", b"[unfinished")
        self.refused("tauri_source_admission_input_invalid")


class ArchiveInventoryTests(unittest.TestCase):
    """Exercise the parser directly; these synthetic archives are never admitted."""

    def archive(self, entries: list[tuple[str, bytes, bytes]]) -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.USTAR_FORMAT) as archive:
            for name, content, kind in entries:
                member = tarfile.TarInfo(name)
                member.type = kind
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = "../../outside"
                member.size = len(content) if kind == tarfile.REGTYPE else 0
                archive.addfile(member, io.BytesIO(content) if member.size else None)
        return output.getvalue()

    def test_unsafe_and_wrong_root_paths_rejected(self) -> None:
        for name in ("/tauri-2.12.1/a", "tauri-2.12.1/../a", "tauri-2.12.1/a/./b",
                     "tauri-2.12.1/a//b", "tauri-2.12.1/a\\b", "tauri-2.12.1/C:evil",
                     "tauri-2.12.1/nul.txt", "tauri-2.12.1/trailing.", "tauri-2.12.2/a"):
            with self.subTest(name=name), self.assertRaises(admission.TauriSourceAdmissionError):
                admission._archive_inventory(self.archive([(name, b"x", tarfile.REGTYPE)]))

    def test_links_and_special_members_rejected(self) -> None:
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind), self.assertRaises(admission.TauriSourceAdmissionError):
                admission._archive_inventory(self.archive([("tauri-2.12.1/a", b"", kind)]))

    def test_duplicate_and_case_aliased_entries_rejected(self) -> None:
        for second in ("a", "A"):
            with self.subTest(second=second), self.assertRaises(admission.TauriSourceAdmissionError):
                admission._archive_inventory(self.archive([
                    ("tauri-2.12.1/a", b"first", tarfile.REGTYPE),
                    (f"tauri-2.12.1/{second}", b"second", tarfile.REGTYPE),
                ]))

    def test_file_directory_path_collision_rejected(self) -> None:
        with self.assertRaisesRegex(admission.TauriSourceAdmissionError, "file_directory_collision"):
            admission._archive_inventory(self.archive([
                ("tauri-2.12.1/a", b"first", tarfile.REGTYPE),
                ("tauri-2.12.1/a/b", b"second", tarfile.REGTYPE),
            ]))

    def test_archive_member_count_is_bounded(self) -> None:
        entries = [(f"tauri-2.12.1/{number}", b"x", tarfile.REGTYPE) for number in range(143)]
        with self.assertRaisesRegex(admission.TauriSourceAdmissionError, "budget_exceeded"):
            admission._archive_inventory(self.archive(entries))


if __name__ == "__main__":
    unittest.main()
