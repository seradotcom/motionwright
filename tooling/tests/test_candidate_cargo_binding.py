"""Join the candidate's recorded Cargo identity to extracted executable bytes.

The package bytes and executable headers in this suite are explicit unit fixtures.
No installer, native executable or Cargo build is run by these tests.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("candidate_smoke_binding", ROOT / "tooling/candidate_smoke.py")
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


class CandidateCargoBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="candidate-cargo-binding-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.binary = self.folder / "installed/Motionwright.exe"
        self.binary.parent.mkdir()
        original = bytearray(512)
        original[:2] = b"MZ"
        struct.pack_into("<I", original, 0x3c, 0x80)
        original[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", original, 0x84, 0x8664)
        self.original = bytes(original)
        self.binary.write_bytes(self.original)
        for source, target in mod.NOTICE_FILES:
            path = self.binary.parent / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / source).read_bytes())
        artifact = {"path": "x86_64-pc-windows-msvc/release/motionwright-desktop.exe",
                    "bytes": len(self.original), "sha256": digest(self.original)}
        public_id = "cargo:path:apps/desktop/src-tauri/Cargo.toml#motionwright-desktop@0.1.0"
        self.cargo_notice = (ROOT / "LICENSE").read_bytes()
        self.cargo_notice_sha = digest(self.cargo_notice)
        self.index = {
            "schema": "motionwright.cargo-build-notices/1",
            "source": "1"*40, "source_tree": "2"*40, "target": "x86_64-pc-windows-msvc",
            "root_package": "motionwright-desktop",
            "components": [{"component_id": public_id, "name": "motionwright-desktop",
                            "notices": [{"sha256": self.cargo_notice_sha}]}],
            "artifact_identities": [artifact],
            "artifact_identities_sha256": digest(encoded([artifact])),
            "root_binary_observations": [{
                "package_id": public_id, "package": "motionwright-desktop",
                "target": "x86_64-pc-windows-msvc", "name": "motionwright-desktop",
                "unit_id": "3"*64, **artifact,
            }],
            "coverage": {"full_package_inventory": "NOT_VERIFIED"},
        }
        self.index_path = self.binary.parent / "licenses/cargo/index.json"
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.cargo_text_path = self.index_path.parent / "texts" / (self.cargo_notice_sha + ".txt")
        self.cargo_text_path.parent.mkdir()
        self.cargo_text_path.write_bytes(self.cargo_notice)
        self.index_path.write_bytes(encoded(self.index))
        self.installer = self.folder / "Motionwright_0.1.0_x64-setup.exe"
        self.installer.write_bytes(b"INERT UNIT PACKAGE; NEVER EXECUTED")
        config = json.loads((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_bytes())
        lock = json.loads((ROOT / "SOURCE_LOCK.json").read_bytes())
        self.receipt_data = {
            "schema_version": 1,
            "candidate": {
                "application": "Motionwright", "version": config["version"],
                "source_revision": "1"*40,
                "semwright_revision": lock["dependencies"]["semwright"]["revision"],
                "platform": "windows", "arch": "x86_64",
            },
            "artifacts": [{
                "filename": self.installer.name, "bundle_kind": "nsis",
                "bytes": self.installer.stat().st_size, "sha256": digest(self.installer.read_bytes()),
                "cargo_build": {"index_sha256": digest(encoded(self.index)),
                                "source_tree": "2"*40, "target": "x86_64-pc-windows-msvc",
                                "root_package": "motionwright-desktop", "binary_target": "motionwright-desktop"},
            }],
            "claims": {"published": False, "human_install_acceptance": "NOT_RUN",
                       "distribution_signature": "NOT_CLAIMED", "notarization": "NOT_CLAIMED"},
        }
        self.receipt = self.folder / "receipt.json"
        self.save_receipt()

    def save_receipt(self):
        self.receipt.write_bytes(encoded(self.receipt_data))

    def inspect(self):
        return mod.build_receipt(candidate_receipt=self.receipt, artifact=self.installer,
                                 installed_binary=self.binary, kind="nsis",
                                 method="nsis_silent_current_user")

    def test_same_size_valid_header_binary_substitution_is_rejected(self):
        self.assertEqual(self.inspect()["automated_inspection"]["status"], "PASS")
        changed = bytearray(self.original); changed[400] = 1
        self.binary.write_bytes(changed)
        with self.assertRaisesRegex(mod.CandidateError, "installed_binary_differs_from_cargo_capture"):
            self.inspect()

    def test_changed_index_cannot_supply_its_own_trusted_digest(self):
        self.index_path.write_bytes(encoded(self.index) + b"\n")
        with self.assertRaisesRegex(mod.CandidateError, "cargo_index_digest_mismatch"):
            self.inspect()

    def test_declared_capture_cannot_be_omitted_from_installed_resources(self):
        self.index_path.unlink()
        with self.assertRaisesRegex(mod.CandidateError, "candidate_cargo_capture_missing_or_invalid"):
            self.inspect()

    def test_digest_matching_other_source_index_cannot_transfer_to_candidate(self):
        self.index["source"] = "4"*40
        raw = encoded(self.index); self.index_path.write_bytes(raw)
        self.receipt_data["artifacts"][0]["cargo_build"]["index_sha256"] = digest(raw)
        self.save_receipt()
        with self.assertRaisesRegex(mod.CandidateError, "cargo_index_candidate_scope_mismatch"):
            self.inspect()

    def test_matching_capture_preserves_existing_unsigned_inspection_limits(self):
        result = self.inspect()
        self.assertEqual(result["automated_inspection"]["status"], "PASS")
        self.assertEqual(result["automated_inspection"]["binary"]["binary_sha256"], digest(self.original))
        self.assertEqual(result["automated_inspection"]["license_notices"]["status"],
                         "DECLARED_NOTICES_PRESENT_BYTE_EXACT")
        self.assertEqual(result["claims"]["human_install_acceptance"], "NOT_RUN")
        self.assertEqual(result["claims"]["signing"], "NOT_CLAIMED")
        self.assertFalse(result["claims"]["release_published"])



    def test_binary_changed_between_inspections_is_rejected(self):
        original_identity = mod.binary_identity
        changed = bytearray(self.original); changed[400] = 1
        self.binary.write_bytes(changed)

        def inspect_then_restore(*args, **kwargs):
            result = original_identity(*args, **kwargs)
            self.binary.write_bytes(self.original)
            return result

        with mock.patch.object(mod, "binary_identity", side_effect=inspect_then_restore):
            with self.assertRaisesRegex(mod.CandidateError, "candidate_binary_changed_between_inspections"):
                result = self.inspect()
                print(json.dumps({"contradictory_receipt_observed": True,
                                  "header_inspection_sha256": result["automated_inspection"]["binary"]["binary_sha256"],
                                  "cargo_binding_sha256": result["automated_inspection"]["cargo_build_binding"]["sha256"]}))

    def test_cargo_texts_close_only_the_declared_cargo_subtree(self):
        (self.binary.parent / "licenses/unrelated-resource.txt").write_bytes(b"outside cargo")
        result = self.inspect()
        binding = result["automated_inspection"]["cargo_build_binding"]
        self.assertEqual(binding["status"], "CAPTURED_ROOT_BINARY_BYTE_EXACT")
        notices = binding["notice_resources"]
        self.assertEqual(notices["status"], "DECLARED_CARGO_TEXTS_PRESENT_BYTE_EXACT")
        self.assertEqual(notices["scope"], "CARGO_NOTICE_SUBTREE_ONLY")
        self.assertTrue(notices["subtree_closed"])
        self.assertEqual(notices["unique_texts"], 1)
        self.assertEqual(notices["total_bytes"], len(self.cargo_notice))
        self.assertEqual(notices["full_package_inventory"], "NOT_VERIFIED")
        self.assertEqual(result["claims"]["human_install_acceptance"], "NOT_RUN")
        self.assertEqual(result["claims"]["signing"], "NOT_CLAIMED")
        self.assertFalse(result["claims"]["release_published"])

    def test_missing_cargo_text_is_rejected_with_the_same_sealed_index(self):
        self.assertEqual(self.inspect()["automated_inspection"]["status"], "PASS")
        self.cargo_text_path.unlink()
        with self.assertRaisesRegex(mod.CandidateError, "candidate_cargo_notice_resource_missing"):
            self.inspect()

    def test_same_size_cargo_text_substitution_is_rejected(self):
        self.assertEqual(self.inspect()["automated_inspection"]["status"], "PASS")
        changed = bytearray(self.cargo_notice)
        changed[0] ^= 1
        self.cargo_text_path.write_bytes(changed)
        self.assertEqual(self.cargo_text_path.stat().st_size, len(self.cargo_notice))
        with self.assertRaisesRegex(mod.CandidateError, "candidate_cargo_notice_digest_mismatch"):
            self.inspect()

    def test_unreferenced_content_addressed_cargo_text_is_rejected(self):
        self.assertEqual(self.inspect()["automated_inspection"]["status"], "PASS")
        extra = b"An unreferenced notice is still extra.\n"
        (self.cargo_text_path.parent / (digest(extra) + ".txt")).write_bytes(extra)
        with self.assertRaisesRegex(mod.CandidateError, "candidate_cargo_notice_tree_extra"):
            self.inspect()

    def test_extra_empty_cargo_directories_are_rejected(self):
        for extra in (self.index_path.parent / "unexpected", self.cargo_text_path.parent / "nested"):
            with self.subTest(location=extra.name):
                extra.mkdir()
                try:
                    with self.assertRaisesRegex(mod.CandidateError, "candidate_cargo_notice_tree_extra"):
                        self.inspect()
                finally:
                    extra.rmdir()

    def test_cargo_text_symlink_is_rejected_even_for_identical_bytes(self):
        target = self.folder / "notice-link-target.txt"
        target.write_bytes(self.cargo_notice)
        self.cargo_text_path.unlink()
        try:
            self.cargo_text_path.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            self.cargo_text_path.write_bytes(self.cargo_notice)
            self.skipTest("Physical symlink creation unavailable: " + type(error).__name__)
        with self.assertRaisesRegex(mod.CandidateError, "^redirected_path$"):
            self.inspect()


if __name__ == "__main__":
    unittest.main(verbosity=2)
