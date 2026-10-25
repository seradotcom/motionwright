"""Text-only regressions for notices copied into candidate resources.

No executable, installer or native library is executed. Directory-link tests
use a real NTFS junction on Windows when symlink creation is unavailable.
The platform smoke tests separately inspect inert binary-format fixtures.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "candidate_smoke_notices", ROOT / "tooling/candidate_smoke.py",
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class CandidateLicenseNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="candidate-notices-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.binary = self.folder / "extracted/usr/bin/product-placeholder"
        # A path is enough: this suite never writes or runs a program file.
        self.resource_root = mod.notice_resource_directory(self.binary, "deb")
        self.desktop = json.loads(
            (ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_bytes()
        )
        for source, target in mod.NOTICE_FILES:
            path = self.resource_root / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / source).read_bytes())

    def verify(self):
        return mod.verify_license_notices(
            installed_binary=self.binary, kind="deb", desktop=self.desktop,
        )

    def test_original_source_bytes_are_present_and_each_digest_matches(self):
        receipt = self.verify()
        self.assertEqual(receipt["status"], "DECLARED_NOTICES_PRESENT_BYTE_EXACT")
        self.assertEqual(len(receipt["files"]), 4)
        for record in receipt["files"]:
            original = (ROOT / record["source_relative_path"]).read_bytes()
            self.assertEqual(record["packaged_bytes"], len(original))
            self.assertEqual(record["packaged_sha256"],
                             hashlib.sha256(original).hexdigest())
            self.assertEqual(record["source_sha256"], record["packaged_sha256"])

    def test_each_required_file_is_independently_mandatory(self):
        for source, target in mod.NOTICE_FILES:
            with self.subTest(source=source):
                path = self.resource_root / target
                original = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(mod.CandidateError, "missing_or_unreadable"):
                    self.verify()
                path.write_bytes(original)

    def test_same_size_changed_glib_copyright_is_rejected(self):
        path = self.resource_root / "licenses/glib-0.18.5/COPYRIGHT"
        original = path.read_bytes()
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        with self.assertRaisesRegex(mod.CandidateError, "bytes_changed"):
            self.verify()

    def test_wrong_filename_does_not_substitute_for_product_license(self):
        path = self.resource_root / "licenses/motionwright/LICENSE"
        path.rename(path.with_name("LICENSE-copy"))
        with self.assertRaisesRegex(mod.CandidateError, "missing_or_unreadable"):
            self.verify()

    def test_source_file_alone_does_not_prove_a_packaged_copy(self):
        for _, target in mod.NOTICE_FILES:
            (self.resource_root / target).unlink()
        self.assertTrue((ROOT / "LICENSE").is_file())
        with self.assertRaisesRegex(mod.CandidateError, "missing_or_unreadable"):
            self.verify()

    def test_resource_map_must_bind_original_sources_to_declared_locations(self):
        mapping = self.desktop["bundle"]["resources"]
        mapping["../../../vendor/glib-0.18.5/LICENSE"] = "licenses/wrong/LICENSE"
        with self.assertRaisesRegex(mod.CandidateError, "resource_mapping_invalid"):
            self.verify()

    def test_empty_notice_is_rejected(self):
        path = self.resource_root / "licenses/THIRD_PARTY_NOTICES.md"
        path.write_bytes(b"")
        with self.assertRaisesRegex(mod.CandidateError, "size_invalid"):
            self.verify()

    def test_oversized_notice_is_rejected(self):
        path = self.resource_root / "licenses/THIRD_PARTY_NOTICES.md"
        path.write_bytes(b"x" * (mod.MAX_NOTICE_BYTES + 1))
        with self.assertRaisesRegex(mod.CandidateError, "size_invalid"):
            self.verify()

    def test_directory_cannot_stand_in_for_a_notice(self):
        path = self.resource_root / "licenses/THIRD_PARTY_NOTICES.md"
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(mod.CandidateError, "not_regular"):
            self.verify()

    def test_source_line_endings_are_not_silently_rewritten(self):
        original = b"copyright\r\npermission\r\n"
        self.assertEqual(mod.compare_notice_bytes(original, original)["packaged_bytes"],
                         len(original))
        with self.assertRaisesRegex(mod.CandidateError, "bytes_changed"):
            mod.compare_notice_bytes(original, original.replace(b"\r\n", b"\n"))

    def test_resource_layout_uses_product_name_not_binary_filename(self):
        binary = self.folder / "root/usr/bin/product-placeholder"
        expected = self.folder / "root/usr/lib/Motionwright"
        self.assertEqual(mod.notice_resource_directory(binary, "deb"), expected)
        self.assertEqual(mod.notice_resource_directory(binary, "appimage"), expected)
        windows_path = self.folder / "installed/product-placeholder"
        self.assertEqual(mod.notice_resource_directory(windows_path, "nsis"),
                         windows_path.parent)
        mac_path = self.folder / "Product.app/Contents/MacOS/product-placeholder"
        self.assertEqual(mod.notice_resource_directory(mac_path, "dmg"),
                         mac_path.parent.parent / "Resources")

    def test_flat_linux_or_macos_paths_are_not_installed_resource_layouts(self):
        for kind in ("deb", "appimage", "dmg"):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(mod.CandidateError, "layout_invalid"):
                    mod.notice_resource_directory(self.folder / "placeholder", kind)

    def test_complete_transitive_inventory_is_not_claimed(self):
        result = self.verify()
        self.assertEqual(result["complete_dependency_inventory"], "NOT_VERIFIED")
        self.assertNotIn("license_compliance", result)
        self.assertNotIn("signing", result)
        self.assertNotIn("published", result)


    def make_directory_link(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            if os.name != "nt" or getattr(error, "winerror", None) != 1314:
                raise
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        info = link.lstat()
        self.assertTrue(stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        self.assertEqual(link.resolve(strict=True), target.resolve(strict=True))

    def test_package_locator_alias_accepts_exact_notices(self):
        package = self.binary.parents[2]
        alias = self.folder / "package-alias"
        self.make_directory_link(alias, package)
        result = mod.verify_license_notices(
            installed_binary=alias / self.binary.relative_to(package),
            kind="deb", desktop=self.desktop,
        )
        self.assertEqual(result["status"], "DECLARED_NOTICES_PRESENT_BYTE_EXACT")

    def test_source_locator_alias_accepts_exact_notices(self):
        alias = self.folder / "source-alias"
        self.make_directory_link(alias, ROOT)
        result = mod.verify_license_notices(
            installed_binary=self.binary, kind="deb", desktop=self.desktop, root=alias,
        )
        self.assertEqual(len(result["files"]), 4)

    def test_internal_resource_directory_link_is_refused(self):
        original = self.resource_root / "licenses"
        moved = self.folder / "outside-notices"
        original.rename(moved)
        self.make_directory_link(original, moved)
        with self.assertRaisesRegex(mod.CandidateError, "link_refused"):
            self.verify()

    def test_linux_usr_component_cannot_redirect_outside_package(self):
        original = self.binary.parents[1]
        moved = self.folder / "outside-usr"
        original.rename(moved)
        self.make_directory_link(original, moved)
        with self.assertRaisesRegex(mod.CandidateError, "link_refused"):
            self.verify()

    def test_mac_resources_component_cannot_redirect_outside_app(self):
        binary = self.folder / "Product.app/Contents/MacOS/product-placeholder"
        resource = mod.notice_resource_directory(binary, "dmg")
        resource.mkdir(parents=True)
        for source, target in mod.NOTICE_FILES:
            destination = resource / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / source).read_bytes())
        moved = self.folder / "outside-mac-resources"
        resource.rename(moved)
        self.make_directory_link(resource, moved)
        with self.assertRaisesRegex(mod.CandidateError, "link_refused"):
            mod.verify_license_notices(
                installed_binary=binary, kind="dmg", desktop=self.desktop,
            )

    def test_notice_file_link_is_refused(self):
        path = self.resource_root / "licenses/glib-0.18.5/COPYRIGHT"
        target = self.folder / "outside-copyright"
        path.rename(target)
        try:
            path.symlink_to(target)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows token cannot create file symlinks")
            raise
        with self.assertRaisesRegex(mod.CandidateError, "link_refused"):
            self.verify()

    def test_relative_notice_path_cannot_escape_anchor(self):
        anchor = self.resource_root.resolve(strict=True)
        for relative in (Path("../outside"), self.folder.resolve(strict=True)):
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(mod.CandidateError, "relative_path_invalid"):
                    mod._notice_bytes(anchor, relative)

    def test_all_package_layouts_inspect_the_declared_bytes(self):
        layouts = {
            "deb": "all-deb/usr/bin/product",
            "appimage": "all-appimage/usr/bin/product",
            "nsis": "all-nsis/product.exe",
            "dmg": "All.app/Contents/MacOS/product",
        }
        for kind, relative in layouts.items():
            with self.subTest(kind=kind):
                binary = self.folder / relative
                directory = mod.notice_resource_directory(binary, kind)
                for source, target in mod.NOTICE_FILES:
                    destination = directory / target
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes((ROOT / source).read_bytes())
                result = mod.verify_license_notices(
                    installed_binary=binary, kind=kind, desktop=self.desktop,
                )
                self.assertEqual(len(result["files"]), 4)

if __name__ == "__main__":
    unittest.main()
