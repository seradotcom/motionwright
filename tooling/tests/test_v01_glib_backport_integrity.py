"""Synthetic source-only v0.1 glib 0.18.5 backport admission tests."""
from __future__ import annotations

import importlib.util
import io
from hashlib import sha256
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "v01_glib_backport_integrity.py"
SPEC = importlib.util.spec_from_file_location("v01_glib_backport_integrity", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

ORIGINAL = (
    b"fn impl_get(&self, i: usize) {\n"
    + b"let p: *mut libc::c_char = std::ptr::null_mut();\n"
    + b"ffi::g_variant_get_child(...,\n                &p,\n);\n}"
)


class BackportAdmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-v01-glib-fixture-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "glib-0.18.5"
        self.root.mkdir()
        (self.root / "src").mkdir()
        self.upstream = {
            "src/variant_iter.rs": ORIGINAL,
            "Cargo.toml": b"[package]\nname = 'glib'\nversion = '0.18.5'\n",
            "LICENSE": b"synthetic fixture not an actual license",
        }
        for name, value in self.upstream.items():
            data = mod.checked_backport(value) if name == mod.AFFECTED else value
            (self.root / name).write_bytes(data)

    def test_only_exact_mutability_fix_is_accepted(self):
        result = mod.verify_vendor(self.upstream, self.root)
        self.assertEqual(result["patched_file"], "src/variant_iter.rs")
        self.assertTrue(result["only_two_mutability_edits"])
        self.assertFalse(result["upstream_rustsec_advisory_closed"])
        self.assertFalse(result["linux_binary_linkage_verified"])
        self.assertFalse(result["motionwright_v01_release_authorized"])

    def test_missing_original_out_parameter_is_refused(self):
        with self.assertRaisesRegex(mod.BackportError, "declaration_changed"):
            mod.checked_backport(ORIGINAL.replace(mod.OLD_DECL, b"let p = ptr();"))

    def test_missing_original_ffi_pointer_argument_is_refused(self):
        with self.assertRaisesRegex(mod.BackportError, "call_changed"):
            mod.checked_backport(ORIGINAL.replace(mod.OLD_CALL, b"\n &p,\n"))

    def test_already_patched_source_requires_new_review(self):
        patched = mod.checked_backport(ORIGINAL)
        with self.assertRaises(mod.BackportError):
            mod.checked_backport(patched)

    def test_added_vendor_file_is_refused(self):
        (self.root / "unexpected.rs").write_bytes(b"unexpected")
        with self.assertRaisesRegex(mod.BackportError, "extra_file"):
            mod.verify_vendor(self.upstream, self.root)

    def test_missing_vendor_file_is_refused(self):
        (self.root / "LICENSE").unlink()
        with self.assertRaisesRegex(mod.BackportError, "vendored_files_differ"):
            mod.verify_vendor(self.upstream, self.root)

    def test_non_patch_modification_is_refused(self):
        (self.root / "Cargo.toml").write_bytes(b"tampered manifest")
        with self.assertRaisesRegex(mod.BackportError, "non_backport_modification"):
            mod.verify_vendor(self.upstream, self.root)

    def test_disposable_candidate_generation_is_exact(self):
        target = self.root.with_name("fresh-disposable")
        mod.materialize_candidate(self.upstream, target)
        result = mod.verify_vendor(self.upstream, target)
        self.assertTrue(result["only_two_mutability_edits"])
        self.assertFalse(result["linux_binary_linkage_verified"])

    def test_candidate_generation_cannot_overwrite_existing_source(self):
        with self.assertRaisesRegex(mod.BackportError, "already_exists"):
            mod.materialize_candidate(self.upstream, self.root)

    def test_wrong_crate_hash_fails_before_parsing(self):
        crate = self.root / "untrusted.crate"
        crate.write_bytes(b"not an upstream crate")
        with self.assertRaisesRegex(mod.BackportError, "upstream_crate_checksum_mismatch"):
            mod.read_upstream(crate)

    def test_archive_parent_traversal_is_rejected(self):
        crate = self.root / "escape.crate"
        with tarfile.open(crate, "w:gz") as tar:
            payload = b"unsafe"
            member = tarfile.TarInfo("glib-0.18.5/../../escape")
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
        with patch.object(mod, "UPSTREAM_SHA256", sha256(crate.read_bytes()).hexdigest()):
            with self.assertRaisesRegex(mod.BackportError, "unsafe_archive_member_path"):
                mod.read_upstream(crate)


if __name__ == "__main__":
    unittest.main()
