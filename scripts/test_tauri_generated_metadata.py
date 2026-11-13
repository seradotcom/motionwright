"""Independent generated-permission custody contracts; no Cargo or installers."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import shutil
import unittest
from unittest import mock

import cargo_license_sources as collector
import tauri_generated_permissions as permissions
import test_cargo_license_sources as source_tests
from test_cargo_license_sources import NOTICE, REGISTRY, sha

BUILD_PATH = "apps/desktop/src-tauri/build.rs"
BUILD = b"""fn main() {
    let attributes = tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["hello_world", "save_project"])
    );
    tauri_build::try_build(attributes).expect("build failed");
}
"""


@unittest.skipUnless(shutil.which("git"), "real Git fixture capability is unavailable")
class TauriGeneratedMetadataTests(unittest.TestCase):
    setUp = source_tests.CargoLicenseSourcesTests.setUp
    codes = source_tests.CargoLicenseSourcesTests.codes

    def commit_paths(self, *names):
        self.fixture.git(self.fixture.workspace, "add", "--", *names)
        self.fixture.git(self.fixture.workspace, "commit", "--quiet", "-m", "bound fixture change")

    def head(self):
        return self.fixture.git(self.fixture.workspace, "rev-parse", "HEAD").decode().strip()

    def source_descriptor(self, raw=BUILD):
        return {"path": BUILD_PATH, "bytes": len(raw), "sha256": sha(raw),
                "git_blob": self.fixture.git(self.fixture.workspace, "rev-parse",
                                            "HEAD:" + BUILD_PATH).decode().strip()}

    def generated_fixture(self, *, linux=False):
        desktop = self.fixture.workspace_member(
            "motionwright-desktop", relative=Path("apps/desktop/src-tauri"))
        for name, version, checksum in permissions.GENERATOR_PACKAGES:
            self.fixture.packages.append({"name": name, "version": version,
                                          "checksum": checksum, "source": REGISTRY})
        (self.fixture.workspace / BUILD_PATH).write_bytes(BUILD)
        self.fixture.bind_lock()
        schemas = {}
        names = collector.LINUX_TAURI_SCHEMAS if linux else collector.WINDOWS_TAURI_SCHEMAS
        for name in names:
            path = self.fixture.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"{}")
            schemas[name] = {"bytes": 2, "sha256": sha(b"{}")}
        generated = {}
        for name, raw in permissions.derive_command_permissions(BUILD).items():
            path = self.fixture.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            generated[name] = {"bytes": len(raw), "sha256": sha(raw)}
        profile = {"schema": ("motionwright.linux-cargo-generated-metadata/1" if linux
                              else "motionwright.windows-cargo-generated-metadata/2"),
                   "source": self.head(), "target": ("x86_64-unknown-linux-gnu" if linux
                                                   else "x86_64-pc-windows-msvc"),
                   "cargo_git_markers": {}, "tauri_schemas": schemas,
                   "tauri_command_permissions": {
                       "command_source": self.source_descriptor(), "files": generated}}
        return desktop, profile

    def collect(self, component, profile):
        return self.fixture.collect([component], generated_metadata=profile)

    def assert_rejected(self, component, profile, code):
        result = self.collect(component, profile)
        self.assertIn(code, self.codes(result), result["issues"])
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        return result

    def test_linux_profile_preserves_notices_and_requires_source_bound_permissions(self):
        component, profile = self.generated_fixture(linux=True)
        result = self.collect(component, profile)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["texts"], {sha(NOTICE): NOTICE})
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        excluded = result["components"][0]["source_authentication"]["excluded_generated_metadata"]
        self.assertEqual({row["path"] for row in excluded},
                         set(collector.LINUX_TAURI_SCHEMAS) |
                         set(profile["tauri_command_permissions"]["files"]))
        missing = copy.deepcopy(profile)
        del missing["tauri_command_permissions"]
        self.assert_rejected(component, missing, "generated_metadata_profile_invalid")

    def test_linux_profile_rejects_crossed_target_schema_and_rosters(self):
        component, profile = self.generated_fixture(linux=True)
        for label in ("windows_target", "windows_profile", "unsupported_target",
                      "non_string_schema", "missing_schema", "windows_schema", "extra_field"):
            with self.subTest(label):
                changed = copy.deepcopy(profile)
                if label == "windows_target":
                    changed["target"] = "x86_64-pc-windows-msvc"
                elif label == "windows_profile":
                    changed["schema"] = "motionwright.windows-cargo-generated-metadata/2"
                elif label == "unsupported_target":
                    changed["target"] = "aarch64-unknown-linux-gnu"
                elif label == "non_string_schema":
                    changed["schema"] = {}
                elif label == "missing_schema":
                    changed["tauri_schemas"].pop(collector.LINUX_TAURI_SCHEMAS[0])
                elif label == "windows_schema":
                    row = changed["tauri_schemas"].pop(collector.LINUX_TAURI_SCHEMAS[-1])
                    changed["tauri_schemas"][collector.WINDOWS_TAURI_SCHEMAS[-1]] = row
                else:
                    changed["unreviewed"] = True
                self.assert_rejected(component, changed, "generated_metadata_profile_invalid")

    def test_linux_desktop_schema_must_equal_target_even_with_updated_descriptor(self):
        component, profile = self.generated_fixture(linux=True)
        name = "apps/desktop/src-tauri/gen/schemas/desktop-schema.json"
        raw = b'{"different":true}'
        (self.fixture.workspace / name).write_bytes(raw)
        profile["tauri_schemas"][name] = {"bytes": len(raw), "sha256": sha(raw)}
        self.assert_rejected(component, profile, "generated_tauri_schema_copy_mismatch")

    def test_linux_extra_ignored_file_never_becomes_generated_source(self):
        component, profile = self.generated_fixture(linux=True)
        ignored = "apps/desktop/src-tauri/unreviewed.bin"
        ignore_file = "apps/desktop/src-tauri/.gitignore"
        (self.fixture.workspace / ignore_file).write_bytes(b"unreviewed.bin\n")
        self.commit_paths(ignore_file)
        profile["source"] = self.head()
        path = self.fixture.workspace / ignored
        path.write_bytes(b"unreviewed source addition")
        self.assertEqual(self.fixture.git(
            self.fixture.workspace, "check-ignore", "--", ignored).decode().strip(), ignored)
        self.assert_rejected(component, profile, "git_worktree_members_mismatch")
        self.assertEqual(path.read_bytes(), b"unreviewed source addition")

    def test_linux_permission_forgery_is_not_admitted_by_its_own_digest(self):
        component, profile = self.generated_fixture(linux=True)
        name = next(iter(profile["tauri_command_permissions"]["files"]))
        raw = (self.fixture.workspace / name).read_bytes().replace(b"hello_world", b"other_world")
        (self.fixture.workspace / name).write_bytes(raw)
        profile["tauri_command_permissions"]["files"][name] = {"bytes": len(raw), "sha256": sha(raw)}
        self.assert_rejected(component, profile, "generated_tauri_permission_bytes_invalid")

    def test_linux_generator_lock_remains_exact(self):
        component, profile = self.generated_fixture(linux=True)
        self.fixture.lock_bytes = self.fixture.lock_bytes.replace(
            permissions.GENERATOR_PACKAGES[0][2].encode(), b"0" * 64, 1)
        (self.fixture.workspace / "Cargo.lock").write_bytes(self.fixture.lock_bytes)
        self.commit_paths("Cargo.lock")
        profile["source"] = self.head()
        self.assert_rejected(component, profile, "generated_tauri_generator_lock_invalid")

    def test_v2_preserves_original_notices_and_excludes_exact_permission_bytes(self):
        component, profile = self.generated_fixture()
        original = copy.deepcopy(profile)
        before = {name: (self.fixture.workspace / name).read_bytes()
                  for name in profile["tauri_command_permissions"]["files"]}
        result = self.collect(component, profile)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["texts"], {sha(NOTICE): NOTICE})
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        record = result["components"][0]
        auth = record["source_authentication"]
        excluded = {row["path"]: row for row in auth["excluded_generated_metadata"]}
        self.assertEqual(len(excluded), 6)
        for name, raw in before.items():
            self.assertEqual(excluded[name]["kind"], "TAURI_GENERATED_COMMAND_PERMISSION")
            self.assertEqual(excluded[name]["sha256"], sha(raw))
            self.assertEqual((self.fixture.workspace / name).read_bytes(), raw)
        generated = set(before)
        self.assertFalse(generated & {row["source_path"] for row in auth["files"]})
        self.assertFalse(generated & {row["source_path"] for row in record["notices"]})
        self.assertEqual(profile, original)

    def test_no_profile_and_v1_still_reject_permission_files(self):
        component, profile = self.generated_fixture()
        self.assertIn("git_worktree_members_mismatch",
                      self.codes(self.fixture.collect([component])))
        old = copy.deepcopy(profile)
        old["schema"] = "motionwright.windows-cargo-generated-metadata/1"
        del old["tauri_command_permissions"]
        self.assert_rejected(component, old, "git_worktree_members_mismatch")
        wrong = {**profile, "schema": old["schema"]}
        self.assert_rejected(component, wrong, "generated_metadata_profile_invalid")

    def test_source_descriptor_is_exact_and_cannot_claim_another_path_or_blob(self):
        component, profile = self.generated_fixture()
        for field, value in (("path", "apps/desktop/src-tauri/other.rs"),
                             ("sha256", "0" * 64), ("git_blob", "0" * 40),
                             ("bytes", True)):
            with self.subTest(field=field):
                changed = copy.deepcopy(profile)
                changed["tauri_command_permissions"]["command_source"][field] = value
                self.assert_rejected(component, changed, "generated_tauri_command_source_invalid")

    def test_changed_tracked_source_cannot_be_admitted_by_new_profile_hash(self):
        component, profile = self.generated_fixture()
        changed = BUILD.replace(b"fn main() {", b"fn main() { unexpected();")
        (self.fixture.workspace / BUILD_PATH).write_bytes(changed)
        self.commit_paths(BUILD_PATH)
        profile["source"] = self.head()
        profile["tauri_command_permissions"]["command_source"] = self.source_descriptor(changed)
        result = self.collect(component, profile)
        self.assertTrue(any(code.startswith("tauri_permissions_") for code in self.codes(result)),
                        result["issues"])
        self.assertNotEqual(result["issues"], [])

    def test_untracked_build_source_cannot_supply_the_command_roster(self):
        component, profile = self.generated_fixture()
        self.fixture.git(self.fixture.workspace, "rm", "--cached", "--", BUILD_PATH)
        self.fixture.git(self.fixture.workspace, "commit", "--quiet", "-m", "remove source fixture")
        profile["source"] = self.head()
        self.assert_rejected(component, profile, "git_required_file_untracked")

    def test_lock_must_bind_exact_generator_identity(self):
        component, profile = self.generated_fixture()
        original = self.fixture.lock_bytes
        replacements = (
            (permissions.GENERATOR_PACKAGES[0][2].encode(), b"0" * 64),
            (b'version = "2.7.1"', b'version = "2.7.2"'),
            (REGISTRY.encode(), b"registry+https://example.invalid/registry"),
        )
        for old, new in replacements:
            with self.subTest(replacement=old):
                self.fixture.lock_bytes = original.replace(old, new, 1)
                (self.fixture.workspace / "Cargo.lock").write_bytes(self.fixture.lock_bytes)
                self.commit_paths("Cargo.lock")
                profile["source"] = self.head()
                self.assert_rejected(component, profile, "generated_tauri_generator_lock_invalid")

    def test_an_extra_generator_version_is_not_silently_selected(self):
        component, profile = self.generated_fixture()
        self.fixture.lock_bytes += (
            b'\n[[package]]\nname = "tauri-utils"\nversion = "0.0.1"\n'
            + ('source = "' + REGISTRY + '"\n').encode()
            + b'checksum = "' + b"1" * 64 + b'"\n')
        (self.fixture.workspace / "Cargo.lock").write_bytes(self.fixture.lock_bytes)
        self.commit_paths("Cargo.lock")
        profile["source"] = self.head()
        self.assert_rejected(component, profile, "generated_tauri_generator_lock_invalid")

    def test_profile_hash_cannot_launder_forged_permission_contents(self):
        component, profile = self.generated_fixture()
        name = next(iter(profile["tauri_command_permissions"]["files"]))
        raw = (self.fixture.workspace / name).read_bytes().replace(b"hello_world", b"other_world")
        (self.fixture.workspace / name).write_bytes(raw)
        profile["tauri_command_permissions"]["files"][name] = {"bytes": len(raw), "sha256": sha(raw)}
        self.assert_rejected(component, profile, "generated_tauri_permission_bytes_invalid")

    def test_permission_descriptor_and_roster_are_closed(self):
        component, profile = self.generated_fixture()
        name = next(iter(profile["tauri_command_permissions"]["files"]))
        for mutation in ("missing", "alias", "extra", "bool", "extra_descriptor"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(profile)
                files = changed["tauri_command_permissions"]["files"]
                if mutation == "missing":
                    del files[name]
                elif mutation == "alias":
                    files[name.upper()] = files.pop(name)
                elif mutation == "extra":
                    files[name + ".extra"] = files[name]
                elif mutation == "bool":
                    files[name]["bytes"] = True
                else:
                    files[name]["observed"] = True
                result = self.collect(component, changed)
                self.assertTrue(self.codes(result) & {
                    "generated_tauri_permission_roster_invalid",
                    "generated_metadata_descriptor_invalid"}, result["issues"])

    def test_physical_namespace_rejects_extra_and_missing_entries(self):
        component, profile = self.generated_fixture()
        directory = self.fixture.workspace / permissions.GENERATED_ROOT
        extra = directory / "extra-empty-directory"
        extra.mkdir()
        self.assert_rejected(component, profile, "generated_tauri_permission_roster_invalid")
        extra.rmdir()
        path = self.fixture.workspace / next(iter(profile["tauri_command_permissions"]["files"]))
        raw = path.read_bytes()
        path.unlink()
        self.assert_rejected(component, profile, "generated_tauri_permission_roster_invalid")
        path.mkdir()
        result = self.collect(component, profile)
        self.assertNotEqual(result["issues"], [])
        path.rmdir()
        path.write_bytes(raw)

    def test_generated_permission_cannot_replace_tracked_source(self):
        component, profile = self.generated_fixture()
        name = next(iter(profile["tauri_command_permissions"]["files"]))
        self.commit_paths(name)
        profile["source"] = self.head()
        self.assert_rejected(component, profile, "generated_tauri_permission_tracked")

    def test_permissions_do_not_admit_unrelated_ignored_desktop_source(self):
        component, profile = self.generated_fixture()
        name = "apps/desktop/src-tauri/foreign.rs"
        (self.fixture.workspace / ".git/info/exclude").write_text(name + "\n", encoding="utf-8")
        (self.fixture.workspace / name).write_bytes(b"untracked source\n")
        self.assert_rejected(component, profile, "git_worktree_members_mismatch")

    def test_post_collection_revalidation_detects_a_changed_permission(self):
        component, profile = self.generated_fixture()
        name = next(iter(profile["tauri_command_permissions"]["files"]))
        path = self.fixture.workspace / name
        original_collect = collector._collect_one
        def change_after_collection(*args, **kwargs):
            result = original_collect(*args, **kwargs)
            path.write_bytes(path.read_bytes() + b"\n")
            return result
        with mock.patch.object(collector, "_collect_one", side_effect=change_after_collection):
            self.assert_rejected(component, profile, "generated_metadata_bytes_changed")


if __name__ == "__main__":
    unittest.main()
