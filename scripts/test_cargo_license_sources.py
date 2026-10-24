"""Source and license collection fixtures; not installed-package acceptance.

These tests create only private TemporaryDirectory trees. Git fixture setup
mutates those disposable repositories; the collector itself uses read-only
plumbing. No test executes Cargo, downloaded sources, or package installers.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock

import cargo_license_sources as collector

REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
INDEX = "index.crates.io-fixture"
NOTICE = b"Original fixture notice retained byte-for-byte.\n"
SOURCE = b"pub fn fixture_value() -> u8 { 7 }\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def package_manifest(name: str, version: str = "1.2.3",
                     license_expression: str | None = "MIT OR Apache-2.0",
                     license_file: str | None = None) -> bytes:
    lines = ["[package]", f"name = {toml_string(name)}", f"version = {toml_string(version)}"]
    if license_expression is not None:
        lines.append(f"license = {toml_string(license_expression)}")
    if license_file is not None:
        lines.append(f"license-file = {toml_string(license_file)}")
    return ("\n".join(lines) + "\n").encode()


def crate_bytes(prefix: str, files: dict[str, bytes],
                extra: list[tuple[tarfile.TarInfo, bytes | None]] | None = None) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for relative, data in files.items():
            info = tarfile.TarInfo(f"{prefix}/{relative}")
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
        for info, data in extra or []:
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return gzip.compress(raw.getvalue(), mtime=0)


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.workspace = root / "workspace"
        self.cargo = root / "cargo"
        self.workspace.mkdir()
        self.cargo.mkdir()
        self.packages: list[dict[str, str | None]] = []
        self.lock_bytes = b""
        self.git(self.workspace, "init", "--quiet")
        self.git(self.workspace, "config", "core.autocrlf", "false")
        (self.workspace / "Cargo.toml").write_text("[workspace]\nmembers = []\n", encoding="utf-8")
        self.bind_lock()

    @staticmethod
    def git(cwd: Path, *arguments: str) -> bytes:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_TERMINAL_PROMPT": "0"})
        command = ["git", "-c", "user.name=Collector Fixture",
                   "-c", "user.email=collector-fixture@example.invalid",
                   "-c", "core.autocrlf=false", *arguments]
        result = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return result.stdout

    def commit(self) -> None:
        self.git(self.workspace, "add", "--all")
        self.git(self.workspace, "commit", "--quiet", "--allow-empty", "-m", "fixture source")

    def bind_lock(self) -> None:
        lines = ["version = 4", ""]
        for package in self.packages:
            lines += ["[[package]]", f'name = {toml_string(str(package["name"]))}',
                      f'version = {toml_string(str(package["version"]))}']
            for field in ("source", "checksum"):
                if package.get(field) is not None:
                    lines.append(f"{field} = {toml_string(str(package[field]))}")
            lines.append("")
        self.lock_bytes = ("\n".join(lines) + "\n").encode()
        (self.workspace / "Cargo.lock").write_bytes(self.lock_bytes)
        self.commit()

    def registry(self, name: str = "fixture_crate", *,
                 license_expression: str | None = "MIT OR Apache-2.0",
                 license_file: str | None = None, notices: dict[str, bytes] | None = None) -> dict:
        version = "1.2.3"
        files = {"Cargo.toml": package_manifest(name, version, license_expression, license_file),
                 "src/lib.rs": SOURCE}
        files.update({"LICENSE": NOTICE} if notices is None else notices)
        prefix = f"{name}-{version}"
        source = self.cargo / "registry" / "src" / INDEX / prefix
        for relative, data in files.items():
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        archive = self.cargo / "registry" / "cache" / INDEX / f"{prefix}.crate"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(crate_bytes(prefix, files))
        self.packages.append({"name": name, "version": version, "source": REGISTRY,
                              "checksum": sha(archive.read_bytes())})
        self.bind_lock()
        return {"id": f"registry+fixture#{name}@{version}", "name": name, "version": version,
                "source": REGISTRY, "manifest_path": str(source / "Cargo.toml"),
                "license": license_expression, "license_file": license_file,
                "roles": ["target-runtime", "host-build"]}

    def repin_archive(self, component: dict, data: bytes) -> None:
        prefix = f'{component["name"]}-{component["version"]}'
        archive = self.cargo / "registry" / "cache" / INDEX / f"{prefix}.crate"
        archive.write_bytes(data)
        for package in self.packages:
            if package["name"] == component["name"]:
                package["checksum"] = sha(data)
        self.bind_lock()

    def workspace_member(self, name: str = "member", *, inherited: bool = False,
                         vendor: bool = False) -> dict:
        relative = Path("vendor") / name if vendor else Path(name)
        package = self.workspace / relative
        (package / "src").mkdir(parents=True)
        if inherited:
            (self.workspace / "Cargo.toml").write_text(
                '[workspace]\nmembers = ["member"]\n[workspace.package]\n'
                'version = "1.2.3"\nlicense = "MIT OR Apache-2.0"\nlicense-file = "LICENSE"\n',
                encoding="utf-8")
            manifest = (f'[package]\nname = "{name}"\nversion.workspace = true\n'
                        'license.workspace = true\nlicense-file.workspace = true\n').encode()
            (self.workspace / "LICENSE").write_bytes(NOTICE)
            license_file = "../LICENSE"
        else:
            manifest = package_manifest(name)
            (package / "LICENSE").write_bytes(NOTICE)
            license_file = None
        (package / "Cargo.toml").write_bytes(manifest)
        (package / "src" / "lib.rs").write_bytes(SOURCE)
        self.packages.append({"name": name, "version": "1.2.3", "source": None})
        self.bind_lock()
        return {"id": f"path+fixture#{name}@1.2.3", "name": name, "version": "1.2.3",
                "source": None, "manifest_path": str(package / "Cargo.toml"),
                "license": "MIT OR Apache-2.0", "license_file": license_file,
                "roles": ["target-runtime"]}

    def git_package(self) -> dict:
        name = "git_fixture"
        checkout = self.cargo / "git" / "checkouts" / "fixture-origin" / "fixture-checkout"
        (checkout / "src").mkdir(parents=True)
        self.git(checkout, "init", "--quiet")
        self.git(checkout, "config", "core.autocrlf", "false")
        (checkout / "Cargo.toml").write_bytes(package_manifest(name))
        (checkout / "LICENSE").write_bytes(NOTICE)
        (checkout / "src" / "lib.rs").write_bytes(SOURCE)
        self.git(checkout, "add", "--all")
        self.git(checkout, "commit", "--quiet", "-m", "exact fixture revision")
        revision = self.git(checkout, "rev-parse", "HEAD").decode().strip()
        source = f"git+https://example.invalid/fixture?rev={revision}#{revision}"
        self.packages.append({"name": name, "version": "1.2.3", "source": source})
        self.bind_lock()
        return {"id": f"{source}#git_fixture@1.2.3", "name": name, "version": "1.2.3",
                "source": source, "manifest_path": str(checkout / "Cargo.toml"),
                "license": "MIT OR Apache-2.0", "license_file": None,
                "roles": ["host-proc-macro"]}

    def collect(self, components: list[dict], **overrides) -> dict:
        options = {"workspace_root": self.workspace, "cargo_home": self.cargo,
                   "lock_bytes": self.lock_bytes}
        options.update(overrides)
        return collector.collect_cargo_licenses(components, **options)


@unittest.skipUnless(shutil.which("git"), "real Git fixture capability is unavailable")
class CargoLicenseSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mw-cargo-license-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(Path(self.temp.name))

    def codes(self, result: dict) -> set[str]:
        return {issue["code"] for issue in result["issues"]}

    def assert_verified(self, result: dict, count: int = 1) -> None:
        self.assertEqual(result["schema"], "motionwright.cargo-license-sources/1")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        self.assertEqual(len(result["components"]), count)
        for component in result["components"]:
            self.assertEqual(component["source_authentication"]["status"], "VERIFIED")
            self.assertTrue(component["notices"])
            for entry in component["notices"]:
                self.assertFalse(Path(entry["source_path"]).is_absolute())
                self.assertNotIn("\\", entry["source_path"])
                self.assertEqual(sha(result["texts"][entry["sha256"]]), entry["sha256"])
                self.assertEqual(len(result["texts"][entry["sha256"]]), entry["bytes"])

    def assert_blocked(self, result: dict, code: str) -> None:
        self.assertIn(code, self.codes(result))
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")

    def test_registry_original_archive_authenticates_all_source_files_and_preserves_expression_roles(self):
        component = self.fixture.registry()
        result = self.fixture.collect([component])
        self.assert_verified(result)
        record = result["components"][0]
        self.assertEqual(record["license_expression"], "MIT OR Apache-2.0")
        self.assertEqual(record["roles"], ["target-runtime", "host-build"])
        files = record["source_authentication"]["files"]
        self.assertTrue(any(item["source_path"].endswith("/src/lib.rs")
                            and item["sha256"] == sha(SOURCE) for item in files))
        self.assertEqual(result["texts"][sha(NOTICE)], NOTICE)

    def test_matching_texts_are_deduplicated_without_merging_component_identity_or_roles(self):
        left = self.fixture.registry("left")
        right = self.fixture.registry("right")
        right["roles"] = ["host-proc-macro"]
        result = self.fixture.collect([left, right])
        self.assert_verified(result, 2)
        self.assertEqual(len(result["texts"]), 1)
        self.assertNotEqual(result["components"][0]["id"], result["components"][1]["id"])
        self.assertEqual(result["components"][1]["roles"], ["host-proc-macro"])

    def test_forged_cargo_checksum_json_does_not_authenticate_changed_compilable_source(self):
        component = self.fixture.registry()
        package = Path(component["manifest_path"]).parent
        changed = b"pub fn fixture_value() -> u8 { 99 }\n"
        (package / "src" / "lib.rs").write_bytes(changed)
        (package / ".cargo-checksum.json").write_text(
            json.dumps({"files": {"src/lib.rs": sha(changed)}, "package": None}),
            encoding="utf-8")
        result = self.fixture.collect([component])
        self.assert_blocked(result, "registry_source_bytes_mismatch")
        self.assertEqual(result["texts"], {})

    def test_original_crate_missing_is_not_replaced_by_local_checksum_metadata(self):
        component = self.fixture.registry()
        archive = self.fixture.cargo / "registry" / "cache" / INDEX / "fixture_crate-1.2.3.crate"
        archive.unlink()
        result = self.fixture.collect([component])
        self.assert_blocked(result, "source_file_missing")

    def test_original_crate_checksum_must_match_exact_tracked_lock_entry(self):
        component = self.fixture.registry()
        archive = self.fixture.cargo / "registry" / "cache" / INDEX / "fixture_crate-1.2.3.crate"
        data = bytearray(archive.read_bytes())
        data[-1] ^= 1
        archive.write_bytes(data)
        self.assert_blocked(self.fixture.collect([component]), "registry_archive_checksum_mismatch")

    def test_local_extra_or_missing_compilable_file_blocks_registry_authentication(self):
        component = self.fixture.registry()
        package = Path(component["manifest_path"]).parent
        extra = package / "injected.rs"
        extra.write_bytes(b"unexpected source")
        self.assert_blocked(self.fixture.collect([component]), "registry_source_members_mismatch")
        extra.unlink()
        (package / "src" / "lib.rs").unlink()
        self.assert_blocked(self.fixture.collect([component]), "registry_source_members_mismatch")

    def test_changed_license_cannot_be_laundered_by_unchanged_manifest_or_code(self):
        component = self.fixture.registry()
        (Path(component["manifest_path"]).parent / "LICENSE").write_bytes(b"replaced notice\n")
        result = self.fixture.collect([component])
        self.assert_blocked(result, "registry_source_bytes_mismatch")
        self.assertEqual(result["texts"], {})

    def test_archive_parent_traversal_is_rejected_without_extraction(self):
        component = self.fixture.registry()
        prefix = "fixture_crate-1.2.3"
        info = tarfile.TarInfo(prefix + "/../outside.rs")
        info.size = 4
        data = crate_bytes(prefix, {"Cargo.toml": package_manifest("fixture_crate"),
                                    "src/lib.rs": SOURCE, "LICENSE": NOTICE}, [(info, b"evil")])
        self.fixture.repin_archive(component, data)
        result = self.fixture.collect([component])
        self.assert_blocked(result, "unsafe_relative_path")
        self.assertFalse((self.fixture.root / "outside.rs").exists())

    def test_archive_symlink_member_is_rejected_even_when_checksum_is_locked(self):
        component = self.fixture.registry()
        prefix = "fixture_crate-1.2.3"
        info = tarfile.TarInfo(prefix + "/linked.rs")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../external.rs"
        data = crate_bytes(prefix, {"Cargo.toml": package_manifest("fixture_crate"),
                                    "src/lib.rs": SOURCE, "LICENSE": NOTICE}, [(info, None)])
        self.fixture.repin_archive(component, data)
        self.assert_blocked(self.fixture.collect([component]), "crate_member_type_rejected")

    def test_archive_case_collision_is_rejected_before_inspecting_checkout(self):
        component = self.fixture.registry()
        prefix = "fixture_crate-1.2.3"
        data = crate_bytes(prefix, {"Cargo.toml": package_manifest("fixture_crate"),
                                    "src/lib.rs": SOURCE, "LICENSE": NOTICE, "license": NOTICE})
        self.fixture.repin_archive(component, data)
        self.assert_blocked(self.fixture.collect([component]), "crate_member_duplicate")

    def test_archive_decompression_has_an_actual_byte_budget(self):
        component = self.fixture.registry()
        with mock.patch.object(collector, "MAX_UNPACKED_BYTES", 512):
            result = self.fixture.collect([component])
        self.assert_blocked(result, "crate_unpacked_budget_exceeded")

    def test_explicit_nonstandard_license_file_is_collected_without_inventing_text(self):
        component = self.fixture.registry(license_expression=None, license_file="legal/terms.txt",
                                          notices={"legal/terms.txt": NOTICE})
        result = self.fixture.collect([component])
        self.assert_verified(result)
        self.assertIsNone(result["components"][0]["license_expression"])
        self.assertTrue(result["components"][0]["notices"][0]["source_path"].endswith("/legal/terms.txt"))

    def test_missing_original_license_text_is_not_synthesized_from_spdx(self):
        component = self.fixture.registry(notices={})
        result = self.fixture.collect([component])
        self.assert_blocked(result, "license_text_missing")
        self.assertEqual(result["texts"], {})

    def test_malformed_spdx_expression_blocks_original_notice_admission(self):
        component = self.fixture.registry(license_expression="MIT OR")
        result = self.fixture.collect([component])
        self.assert_blocked(result, "license_expression_malformed")
        self.assertEqual(result["texts"], {})

    def test_notice_byte_budget_is_enforced_and_original_is_unchanged(self):
        component = self.fixture.registry(notices={"LICENSE": b"x" * 65})
        original = (Path(component["manifest_path"]).parent / "LICENSE").read_bytes()
        with mock.patch.object(collector, "MAX_NOTICE_BYTES", 64):
            result = self.fixture.collect([component])
        self.assert_blocked(result, "source_file_budget_exceeded")
        self.assertEqual((Path(component["manifest_path"]).parent / "LICENSE").read_bytes(), original)

    def test_registry_license_file_parent_traversal_is_not_workspace_inheritance(self):
        component = self.fixture.registry(license_file="../LICENSE")
        result = self.fixture.collect([component])
        self.assert_blocked(result, "license_file_outside_source")

    def test_workspace_inherited_license_file_uses_authenticated_workspace_declaration(self):
        component = self.fixture.workspace_member(inherited=True)
        result = self.fixture.collect([component])
        self.assert_verified(result)
        record = result["components"][0]
        self.assertEqual([item["source_path"] for item in record["notices"]], ["LICENSE"])
        self.assertTrue(any(item["source_path"] == "Cargo.toml"
                            for item in record["source_authentication"]["files"]))


    def test_native_metadata_separators_preserve_inherited_original_notice(self):
        for relative in (Path("crates/member"), Path("apps/desktop/src-tauri")):
            with self.subTest(depth=len(relative.parts)), tempfile.TemporaryDirectory(
                    prefix="mw-native-license-") as temp:
                fixture = Fixture(Path(temp))
                component = fixture.workspace_member(inherited=True)
                original = Path(component["manifest_path"]).parent
                package = fixture.workspace / relative
                package.parent.mkdir(parents=True)
                original.rename(package)
                workspace_manifest = fixture.workspace / "Cargo.toml"
                workspace_manifest.write_text(
                    workspace_manifest.read_text(encoding="utf-8").replace(
                        'members = ["member"]',
                        f"members = [{toml_string(relative.as_posix())}]"),
                    encoding="utf-8")
                component["manifest_path"] = str(package / "Cargo.toml")
                native_relative = os.path.join(*([".."] * len(relative.parts)), "LICENSE")
                component["license_file"] = native_relative
                fixture.commit()
                result = fixture.collect([component])
                self.assert_verified(result)
                self.assertEqual(result["texts"], {sha(NOTICE): NOTICE})
                self.assertEqual(result["components"][0]["notices"], [
                    {"source_path": "LICENSE", "sha256": sha(NOTICE), "bytes": len(NOTICE)}])
                self.assertEqual(component["license_file"], native_relative)
                self.assertEqual((fixture.workspace / "LICENSE").read_bytes(), NOTICE)

    def test_native_metadata_escape_or_same_bytes_substitution_is_rejected(self):
        component = self.fixture.workspace_member(inherited=True)
        (self.fixture.workspace / "OTHER").write_bytes(NOTICE)
        outside = self.fixture.root / "outside-license"
        outside.write_bytes(NOTICE)
        self.fixture.commit()
        cases = [
            ("escape", os.path.join("..", "..", "LICENSE"),
             {"license_file_outside_source", "license_file_path_invalid"}),
            ("substitution", os.path.join("..", "OTHER"),
             {"component_license_file_metadata_mismatch", "license_file_path_invalid"}),
            ("foreign_absolute", str(outside), {"source_outside_admitted_root"}),
            ("empty_component", ".." + os.sep * 2 + "LICENSE",
             {"unsafe_relative_path", "license_file_path_invalid"}),
        ]
        if os.name == "nt":
            cases.append(("drive_relative", "Q:LICENSE", {"license_file_path_invalid"}))
        for label, value, expected in cases:
            with self.subTest(case=label):
                result = self.fixture.collect([{**component, "license_file": value}])
                self.assertTrue(expected & self.codes(result), result["issues"])
                self.assertEqual(result["texts"], {})
                self.assertEqual(result["components"][0]["notices"], [])
                self.assertEqual(result["components"][0]["source_authentication"]["status"],
                                 "NOT_VERIFIED")

    def test_native_metadata_cannot_relax_malformed_manifest_declarations(self):
        for declaration, code in (("legal\\LICENSE", "license_file_path_invalid"),
                                  ("legal//LICENSE", "unsafe_relative_path")):
            with self.subTest(declaration=declaration), tempfile.TemporaryDirectory(
                    prefix="mw-declared-license-") as temp:
                fixture = Fixture(Path(temp))
                component = fixture.workspace_member(inherited=True)
                (fixture.workspace / "legal").mkdir()
                (fixture.workspace / "legal/LICENSE").write_bytes(NOTICE)
                manifest = fixture.workspace / "Cargo.toml"
                manifest.write_text(manifest.read_text(encoding="utf-8").replace(
                    'license-file = "LICENSE"',
                    "license-file = " + toml_string(declaration)), encoding="utf-8")
                component["license_file"] = os.path.join("..", "legal", "LICENSE")
                fixture.commit()
                result = fixture.collect([component])
                self.assert_blocked(result, code)
                self.assertEqual(result["texts"], {})
                self.assertEqual(result["components"][0]["notices"], [])

    def test_unproven_metadata_parent_path_cannot_claim_workspace_inheritance(self):
        component = self.fixture.workspace_member()
        component["license_file"] = "../LICENSE"
        result = self.fixture.collect([component])
        self.assert_blocked(result, "component_license_file_metadata_mismatch")

    def test_inherited_workspace_license_cannot_escape_the_admitted_root(self):
        component = self.fixture.workspace_member(inherited=True)
        manifest = self.fixture.workspace / "Cargo.toml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(
            'license-file = "LICENSE"', 'license-file = "../LICENSE"'), encoding="utf-8")
        self.fixture.commit()
        self.assert_blocked(self.fixture.collect([component]), "license_file_outside_source")

    def test_workspace_source_bytes_and_index_must_both_match_HEAD(self):
        component = self.fixture.workspace_member()
        path = Path(component["manifest_path"]).parent / "src" / "lib.rs"
        path.write_bytes(b"modified source\n")
        self.assert_blocked(self.fixture.collect([component]), "git_worktree_bytes_mismatch")
        self.fixture.git(self.fixture.workspace, "add", "--all")
        self.assert_blocked(self.fixture.collect([component]), "git_index_not_clean")

    def test_workspace_untracked_compilable_file_is_not_accepted_as_HEAD_source(self):
        component = self.fixture.workspace_member()
        (Path(component["manifest_path"]).parent / "extra.rs").write_bytes(b"untracked\n")
        self.assert_blocked(self.fixture.collect([component]), "git_worktree_members_mismatch")

    def test_git_dependency_requires_exact_full_locked_revision_and_clean_raw_tree(self):
        component = self.fixture.git_package()
        result = self.fixture.collect([component])
        self.assert_verified(result)
        self.assertEqual(result["components"][0]["source_authentication"]["kind"], "git_exact_revision")
        path = Path(component["manifest_path"]).parent / "src" / "lib.rs"
        path.write_bytes(b"dirty Git dependency\n")
        self.assert_blocked(self.fixture.collect([component]), "git_worktree_bytes_mismatch")

    def test_git_dependency_new_commit_cannot_satisfy_old_locked_revision(self):
        component = self.fixture.git_package()
        checkout = Path(component["manifest_path"]).parent
        (checkout / "src" / "lib.rs").write_bytes(b"new revision\n")
        self.fixture.git(checkout, "add", "--all")
        self.fixture.git(checkout, "commit", "--quiet", "-m", "different revision")
        self.assert_blocked(self.fixture.collect([component]), "git_source_revision_mismatch")

    def test_vendored_glib_is_explicitly_a_tracked_rust_wrapper_with_native_review_separate(self):
        component = self.fixture.workspace_member("glib", vendor=True)
        result = self.fixture.collect([component])
        self.assert_verified(result)
        record = result["components"][0]
        self.assertEqual(record["source_authentication"]["kind"], "vendored_rust_glib_wrapper")
        self.assertIn("not_native_GLib_C", record["source_authentication"]["scope"])
        self.assertEqual(result["native_content_reviews"][0]["code"], "native_content_review_required")
        self.assertEqual(result["texts"][sha(NOTICE)], NOTICE)

    def test_source_none_glib_outside_explicit_vendor_path_is_not_asserted_vendored(self):
        component = self.fixture.workspace_member("glib")
        self.assert_blocked(self.fixture.collect([component]), "vendored_glib_wrapper_path_unverified")

    def test_sqlite_wrapper_notices_survive_separate_pending_native_content_review(self):
        component = self.fixture.registry("libsqlite3-sys")
        result = self.fixture.collect([component])
        self.assert_verified(result)
        self.assertEqual(result["texts"][sha(NOTICE)], NOTICE)
        self.assertEqual([item["code"] for item in result["native_content_reviews"]],
                         ["native_content_review_required"])
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")

    def test_captured_lock_must_match_tracked_HEAD_not_just_parse(self):
        component = self.fixture.registry()
        changed = self.fixture.lock_bytes + b"# caller-only lock edit\n"
        self.assert_blocked(self.fixture.collect([component], lock_bytes=changed),
                            "lock_input_not_workspace_HEAD")

    def test_malformed_or_duplicate_lock_fails_closed_without_archives(self):
        component = self.fixture.registry()
        self.assert_blocked(self.fixture.collect([component], lock_bytes=b"not valid toml {{{"),
                            "cargo_lock_malformed")
        duplicate = (self.fixture.lock_bytes + b"[[package]]"
                     + self.fixture.lock_bytes.split(b"[[package]]", 1)[1])
        self.assert_blocked(self.fixture.collect([component], lock_bytes=duplicate),
                            "cargo_lock_package_duplicate")

    def test_inner_physical_symlink_is_rejected_when_host_supports_links(self):
        component = self.fixture.registry()
        package = Path(component["manifest_path"]).parent
        original = package / "LICENSE"
        external = self.fixture.root / "external-notice"
        external.write_bytes(NOTICE)
        original.unlink()
        try:
            original.symlink_to(external)
        except OSError as error:
            self.skipTest(f"physical symlink capability unavailable: {error.__class__.__name__}")
        self.assert_blocked(self.fixture.collect([component]), "source_link_rejected")

    def test_initial_cargo_root_alias_is_allowed_but_no_inner_links_are_introduced(self):
        component = self.fixture.registry()
        alias = self.fixture.root / "cargo-alias"
        try:
            alias.symlink_to(self.fixture.cargo, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"physical root alias capability unavailable: {error.__class__.__name__}")
        result = self.fixture.collect([component], cargo_home=alias)
        self.assert_verified(result)

    def test_no_git_source_authentication_is_inferred_when_git_is_unavailable(self):
        component = self.fixture.registry()
        with mock.patch.object(collector.subprocess, "Popen", side_effect=FileNotFoundError):
            result = self.fixture.collect([component])
        self.assert_blocked(result, "git_unavailable")
        self.assertEqual(result["texts"], {})


class BoundedReadTests(unittest.TestCase):
    def test_real_growth_after_metadata_is_rejected_at_limit_plus_one(self):
        with tempfile.TemporaryDirectory(prefix="mw-license-read-") as temp:
            directory = Path(temp).resolve()
            source = directory / "notice"
            source.write_bytes(b"1234")
            root = collector._Root.admit(directory, "fixture")
            original_read = os.read
            changed = False

            def grow_then_read(descriptor: int, count: int) -> bytes:
                nonlocal changed
                if not changed:
                    changed = True
                    with source.open("ab") as stream:
                        stream.write(b"5")
                        stream.flush()
                        os.fsync(stream.fileno())
                return original_read(descriptor, count)

            with mock.patch.object(collector.os, "read", side_effect=grow_then_read):
                with self.assertRaises(collector._Problem) as raised:
                    collector._read(root, ("notice",), 4, collector._Budget())
            self.assertEqual(raised.exception.code, "source_file_budget_exceeded")
            self.assertEqual(source.read_bytes(), b"12345")

    def test_windows_reparse_attribute_predicate_is_not_a_physical_junction_acceptance(self):
        class Metadata:
            st_mode = 0o100644
            st_file_attributes = 0x400
        self.assertTrue(collector._linked(Metadata()))

    def test_lexical_path_escape_and_windows_aliases_are_rejected(self):
        for value in ("../escape", "/absolute", "C:/escape", "dir\\file", "NUL.txt",
                      "dir/file.", "a//b", "a/./b", "a/../b"):
            with self.subTest(value=value):
                with self.assertRaises(collector._Problem):
                    collector._parts(value)


@unittest.skipUnless(shutil.which("git"), "real Git fixture capability is unavailable")
class CargoLicenseDeltaTests(unittest.TestCase):
    setUp = CargoLicenseSourcesTests.setUp
    codes = CargoLicenseSourcesTests.codes
    assert_verified = CargoLicenseSourcesTests.assert_verified
    assert_blocked = CargoLicenseSourcesTests.assert_blocked

    def test_crlf_checkout_preserves_physical_notice_with_real_local_or_global_policy(self):
        for policy in ("local", "global"):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory(prefix="mw-crlf-") as temp:
                fixture = Fixture(Path(temp))
                component = fixture.workspace_member()
                package = Path(component["manifest_path"]).parent
                overrides = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                             "GIT_CONFIG_COUNT": "0", "GIT_CONFIG_PARAMETERS": ""}
                if policy == "local":
                    fixture.git(fixture.workspace, "config", "core.autocrlf", "true")
                else:
                    fixture.git(fixture.workspace, "config", "--unset", "core.autocrlf")
                    config = fixture.root / "fixture.gitconfig"
                    config.write_bytes(b"[core]\n    autocrlf = true\n")
                    overrides["GIT_CONFIG_GLOBAL"] = str(config)
                for relative in ("Cargo.toml", "LICENSE", "src/lib.rs"):
                    path = package / relative
                    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
                physical = NOTICE.replace(b"\n", b"\r\n")
                with mock.patch.dict(os.environ, overrides):
                    result = fixture.collect([component])
                self.assert_verified(result)
                self.assertEqual(result["texts"][sha(physical)], physical)
                self.assertEqual((package / "LICENSE").read_bytes(), physical)
                files = result["components"][0]["source_authentication"]["files"]
                notice = next(item for item in files if item["source_path"] == "member/LICENSE")
                self.assertEqual(notice["git_line_endings"], "lf_blob_to_crlf_worktree")
                expected_oid = fixture.git(fixture.workspace, "rev-parse", "HEAD:member/LICENSE").decode().strip()
                self.assertEqual(notice["git_blob"], expected_oid)

    def test_crlf_normalization_does_not_accept_mixed_endings_or_changed_content(self):
        component = self.fixture.workspace_member()
        path = Path(component["manifest_path"]).parent / "src/lib.rs"
        canonical = SOURCE + b"// second line\n"
        path.write_bytes(canonical)
        self.fixture.commit()
        self.fixture.git(self.fixture.workspace, "config", "core.autocrlf", "true")
        candidates = (
            canonical.replace(b"\n", b"\r\n", 1),
            canonical.replace(b"\n", b"\r\n") + b"changed",
            canonical.replace(b"\n", b"\r\n") + b"\r",
        )
        for data in candidates:
            with self.subTest(physical=data):
                path.write_bytes(data)
                self.assert_blocked(self.fixture.collect([component]), "git_worktree_bytes_mismatch")
                self.assertEqual(path.read_bytes(), data)

    def test_crlf_policy_never_overrides_false_or_prohibited_attributes(self):
        cases = ("autocrlf=false", "-text", "eol=lf", "filter=fixture",
                 "ident", "working-tree-encoding=UTF-8", "crlf=input")
        for case in cases:
            with self.subTest(policy=case), tempfile.TemporaryDirectory(prefix="mw-crlf-deny-") as temp:
                fixture = Fixture(Path(temp))
                component = fixture.workspace_member()
                package = Path(component["manifest_path"]).parent
                if case != "autocrlf=false":
                    (package / ".gitattributes").write_bytes(f"src/lib.rs {case}\n".encode())
                    fixture.commit()
                    fixture.git(fixture.workspace, "config", "core.autocrlf", "true")
                path = package / "src" / "lib.rs"
                physical = SOURCE.replace(b"\n", b"\r\n")
                path.write_bytes(physical)
                result = fixture.collect([component])
                self.assertTrue(self.codes(result) & {
                    "git_worktree_bytes_mismatch", "git_crlf_policy_unverified"
                }, result["issues"])
                self.assertEqual(result["texts"], {})
                self.assertEqual(path.read_bytes(), physical)

    def test_nonzero_payload_in_second_gzip_member_cannot_hide_after_tar_end(self):
        component = self.fixture.registry()
        archive = (self.fixture.cargo / "registry" / "cache" / INDEX
                   / f'{component["name"]}-{component["version"]}.crate')
        self.fixture.repin_archive(component, archive.read_bytes() + gzip.compress(b"hidden payload"))
        self.assert_blocked(self.fixture.collect([component]), "crate_trailing_payload_rejected")

class GitAndGzipBoundaryTests(unittest.TestCase):
    def test_git_completed_process_with_retained_output_pipe_returns_timeout(self):
        read_fd, write_fd = os.pipe()
        ready, done, released = threading.Event(), threading.Event(), threading.Event()
        release_lock = threading.Lock()
        observed = {}
        started = time.monotonic()
        scheduled_release = started + 0.75

        def release_writer(reason):
            with release_lock:
                if not released.is_set():
                    observed["release_reason"] = reason
                    observed["released_at"] = time.monotonic()
                    os.close(write_fd)
                    released.set()

        class Process:
            stdout = os.fdopen(read_fd, "rb")
            stderr = io.BytesIO()
            returncode = 0
            def wait(self, timeout=None):
                observed["entered_at"] = time.monotonic()
                ready.set()
                return 0
            def kill(self):
                pass

        def invoke():
            try:
                observed["value"] = collector._git(Path.cwd(), "fixture")
            except BaseException as error:
                observed["error"] = error
            finally:
                observed["returned_at"] = time.monotonic()
                observed["returned_before_release"] = not released.is_set()
                done.set()

        timer = threading.Timer(max(0.0, scheduled_release - time.monotonic()),
                                release_writer, args=("fixed_timer",))
        timer.daemon = True
        with mock.patch.object(collector.subprocess, "Popen", return_value=Process()), \
                mock.patch.object(collector, "GIT_TIMEOUT_SECONDS", 0.1):
            caller = threading.Thread(target=invoke, daemon=True)
            timer.start()
            caller.start()
            try:
                completed = done.wait(1.4)
            finally:
                release_writer("bounded_cleanup")
                timer.cancel()
                caller.join(timeout=0.25)
                timer.join(timeout=0.05)
        report = {
            "configured_git_budget_seconds": 0.1,
            "scheduled_release_delay_seconds": 0.75,
            "collector_elapsed_seconds": observed.get("returned_at", started) - started,
            "git_wait_elapsed_seconds": (
                observed.get("returned_at", started) - observed.get("entered_at", started)),
            "release_delay_seconds": observed["released_at"] - started,
            "release_reason": observed["release_reason"],
            "returned_before_release": observed.get("returned_before_release"),
            "error_type": type(observed.get("error")).__name__,
            "error_code": getattr(observed.get("error"), "code", None),
            "controller_completed": completed,
        }
        print("MW_CARGO_RETAINED_PIPE_ORACLE " + json.dumps(report, sort_keys=True), flush=True)
        self.assertTrue(ready.is_set(), "SETUP_FAILURE: Git wait was never reached")
        self.assertLess(observed["entered_at"], scheduled_release - 0.3,
                        "SETUP_FAILURE: scheduler did not establish the retained-pipe window")
        self.assertTrue(completed, "SETUP_FAILURE: bounded controller had to release the fixture")
        self.assertFalse(caller.is_alive(), "SETUP_FAILURE: bounded fixture cleanup did not complete")
        self.assertTrue(observed["returned_before_release"],
                        "collector waited for the fixed writer release beyond its own deadline")
        self.assertIsInstance(observed.get("error"), collector._Problem)
        self.assertEqual(observed["error"].code, "git_timeout")

    def test_git_timeout_never_uses_unbounded_post_kill_wait(self):
        timeouts = []
        class Process:
            stdout = io.BytesIO()
            stderr = io.BytesIO()
            returncode = None
            def wait(self, timeout=None):
                timeouts.append(timeout)
                if len(timeouts) == 1:
                    raise subprocess.TimeoutExpired("fixture", timeout)
                return -9
            def kill(self):
                pass
        with mock.patch.object(collector.subprocess, "Popen", return_value=Process()):
            with self.assertRaises(collector._Problem) as raised:
                collector._git(Path.cwd(), "fixture")
        self.assertEqual(raised.exception.code, "git_timeout")
        self.assertEqual(len(timeouts), 2)
        self.assertTrue(all(timeout is not None and 0 <= timeout <= 20 for timeout in timeouts))

    def test_invalid_deflate_is_a_structured_archive_issue(self):
        invalid = bytes.fromhex("1f8b0800000000000003") + b"\x07" + b"\0" * 8
        caught = None
        try:
            collector._decompress(invalid, collector._Budget())
        except Exception as error:
            caught = error
        self.assertIsInstance(caught, collector._Problem)
        self.assertEqual(caught.code, "crate_archive_malformed")

    def test_gzip_exact_budget_and_concatenated_limit_plus_one(self):
        first = gzip.compress(b"1234")
        with mock.patch.object(collector, "MAX_UNPACKED_BYTES", 4):
            self.assertEqual(collector._decompress(first, collector._Budget()), b"1234")
            with self.assertRaises(collector._Problem) as raised:
                collector._decompress(first + gzip.compress(b"5"), collector._Budget())
        self.assertEqual(raised.exception.code, "crate_unpacked_budget_exceeded")

if __name__ == "__main__":
    unittest.main()
