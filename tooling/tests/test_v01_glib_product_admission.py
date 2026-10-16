"""v0.1 source-bound exact glib backport admission; no product release claims."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
from v01_glib_product_admission import (  # noqa: E402
    ProductBackportError, admit_patched_glib,
)


class GlibPatchedProductSourceTests(unittest.TestCase):
    def test_current_repository_uses_only_exact_two_line_backport(self):
        result = admit_patched_glib(ROOT)
        self.assertTrue(result["local_cargo_lock_selected"])
        self.assertTrue(result["all_other_files_identical_to_upstream"])
        self.assertEqual(result["upstream_file_count"], 121)
        self.assertTrue(result["upstream_advisory_open"])
        self.assertFalse(result["native_linux_binary_linkage_verified"])
        self.assertFalse(result["release_authorized"])

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-v01-glib-product-custody-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ("Cargo.toml", "Cargo.lock"):
            shutil.copy2(ROOT / name, self.root / name)
        dest = self.root / "tooling" / "upstream-crates"
        dest.mkdir(parents=True)
        shutil.copy2(ROOT / "tooling/upstream-crates/glib-0.18.5.crate",
                     dest / "glib-0.18.5.crate")
        shutil.copytree(ROOT / "vendor/glib-0.18.5",
                        self.root / "vendor/glib-0.18.5")

    def rejected(self, reason):
        with self.assertRaisesRegex(ProductBackportError, reason):
            admit_patched_glib(self.root)

    def test_clone_is_source_verified(self):
        self.assertTrue(admit_patched_glib(self.root)["all_other_files_identical_to_upstream"])

    def test_revert_glib_to_registry_is_refused_even_same_version(self):
        path = self.root / "Cargo.lock"
        data = path.read_text(encoding="utf-8")
        fragment = 'name = "glib"\nversion = "0.18.5"\n'
        replacement = (fragment
                       + 'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
                       + 'checksum = "233daaf6e83ae6a12a52055f568f9d7cf4671dabb78ff9560ab6da230ce00ee5"\n')
        self.assertEqual(data.count(fragment), 1)
        path.write_text(data.replace(fragment, replacement, 1), encoding="utf-8")
        self.rejected("glib_lock_did_not_select_local_patch")

    def test_changed_cargo_patch_path_is_refused(self):
        path = self.root / "Cargo.toml"
        data = path.read_text(encoding="utf-8")
        path.write_text(data.replace(
            'glib = { path = "vendor/glib-0.18.5" }',
            'glib = { path = "vendor/unchecked-glib" }',
        ), encoding="utf-8")
        self.rejected("unexpected_cargo_source_override")

    def test_extra_vendor_source_is_refused(self):
        (self.root / "vendor/glib-0.18.5/src/unreviewed.rs").write_bytes(b"")
        self.rejected("glib_upstream_or_patch_source_not_verified")

    def test_ffi_fix_reverted_is_refused(self):
        path = self.root / "vendor/glib-0.18.5/src/variant_iter.rs"
        data = path.read_bytes()
        old = b"                &mut p,\n"
        self.assertEqual(data.count(old), 1)
        path.write_bytes(data.replace(old, b"                &p,\n"))
        self.rejected("glib_upstream_or_patch_source_not_verified")

    def test_orig_archive_mutation_is_refused(self):
        path = self.root / "tooling/upstream-crates/glib-0.18.5.crate"
        with path.open("ab") as output:
            output.write(b"unreviewed byte")
        self.rejected("glib_upstream_or_patch_source_not_verified")

    def test_not_a_product_remediation_verdict(self):
        data = admit_patched_glib(self.root)
        self.assertEqual(data["source_mode"], "local-path-exact-two-line-upstream-backport")
        self.assertFalse(data["real_linux_installed_gui_verified"])
        self.assertTrue(data["upstream_advisory_open"])


if __name__ == "__main__":
    unittest.main()
