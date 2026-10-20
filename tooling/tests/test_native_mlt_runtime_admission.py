"""Synthetic fail-closed preflight tests; no installed library is ever touched."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_runtime_admission as gate


def source_paths() -> tuple[Path, Path] | None:
    original = os.environ.get("MOTIONWRIGHT_MLT_V722_ORIGINAL")
    repaired = os.environ.get("MOTIONWRIGHT_MLT_V722_REPAIRED")
    if not original or not repaired:
        return None
    return Path(original), Path(repaired)


class NativeModuleAdmissionTests(unittest.TestCase):
    def real_originals(self) -> tuple[Path, Path]:
        paths = source_paths()
        if paths is None:
            self.skipTest("source-locked original+repaired source paths not supplied")
        return paths

    @staticmethod
    def compiler(temp: Path, *, bad_source: bool = False,
                 duplicate: bool = False) -> tuple[Path, str, Path, str]:
        module = temp / "libmltavformat.so"
        binary = b"\x7fELF" + bytes(range(256)) * 8
        module.write_bytes(binary)
        module_sha = hashlib.sha256(binary).hexdigest()
        receipt = {
            "schema": "motionwright.native.mlt.repaired-only-compiler-receipt/1",
            "original_source_sha256": gate.ORIGINAL_SHA,
            "repaired_source_sha256": "ff" * 32 if bad_source else gate.REPAIRED_SHA,
            "compiled_source_sha256": gate.REPAIRED_SHA,
            "instrumented_combined_source_compiled": False,
            "compiled_consumer_object_sha256": "ab" * 32,
            "compiled_consumer_object_linked": True,
            "linker_consumer_object_sha256": "ab" * 32,
            "linker_command_sha256": "cd" * 32,
            "linker_command": {
                "original_link_command_sha256": "cd" * 32,
                "repaired_consumer_object_in_link": True,
            },
            "compiler_result": "PASS",
            "source_sanitizers_result": "PASS",
            "source_frame_oracle_result": "PASS",
            "module_installed": False,
            "source_head": "aa" * 20,
            "run_id": "39352693034",
            "run_attempt": "1",
            "compiled_module_sha256": module_sha,
            "compiled_module_bytes": len(binary),
            "MLT_version": "7.22.0",
            "source_constructor_ASAN_UBSAN": "PASS",
            "complete_MLT_consumer_compiler": "PASS",
            "actual_native_effect_performed": False,
        }
        output = temp / "compiled-source-proof.json"
        data = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
        if duplicate:
            data = data.replace(b'"MLT_version":"7.22.0",',
                                b'"MLT_version":"7.22.0","MLT_version":"7.22.0",')
        output.write_bytes(data)
        return module, module_sha, output, hashlib.sha256(data).hexdigest()

    def test_known_original_source_sha_is_fixed_not_discovered_dynamically(self):
        self.assertEqual(gate.ORIGINAL_SHA,
            "c1c8850ff049a8775bb3d24c64be9b9365659773ce019902585c056f5be2f910")
        self.assertEqual(gate.REPAIRED_SHA,
            "246e84b02d25d0fe5ea08d59d5abb03c6dfd42d5d15dabeb464c8051f8ed8de0")

    def test_simulated_windows_machine_can_never_admit_module_on_either_runner(self):
        # This control is deliberately portable: run the Windows platform
        # negative on Linux CI too, without changing any real runtime file.
        # Strict G1 may now require zero skips across both hosts.
        with mock.patch.object(gate.sys, "platform", "win32"):
            with self.assertRaisesRegex(gate.AdmissionRejected, "disposable_GHA"):
                gate.disposable_linux_runner()
            with self.assertRaisesRegex(gate.AdmissionRejected, "disposable_GHA"):
                gate.install({}, Path("/tmp/fake.so"), Path("/tmp/fake-owner"))

    def test_existing_unsafe_runtime_overrides_are_explicitly_forbidden(self):
        self.assertIn("LD_LIBRARY_PATH", gate.FORBIDDEN_ENV)
        self.assertIn("MLT_REPOSITORY", gate.FORBIDDEN_ENV)
        self.assertIn("LD_PRELOAD", gate.FORBIDDEN_ENV)

    def test_authentic_source_and_mock_compiler_receipt_are_structurally_bound(self):
        original, repaired = self.real_originals()
        with tempfile.TemporaryDirectory() as temp:
            module, module_sha, proof, proof_sha = self.compiler(Path(temp))
            result = gate.source_compiler_closure(original, repaired, module,
                                                  module_sha, proof, proof_sha)
            self.assertEqual(result["original_MLT_source_sha256"], gate.ORIGINAL_SHA)
            self.assertEqual(result["repaired_MLT_source_sha256"], gate.REPAIRED_SHA)
            self.assertEqual(result["compiled_module_sha256"], module_sha)

    def test_wrong_original_or_repaired_source_refuses_candidate(self):
        original, repaired = self.real_originals()
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            module, modsha, proof, recsha = self.compiler(work)
            old = work / "wrong_original.c"
            old.write_bytes(original.read_bytes()[:-1] + b"x")
            with self.assertRaisesRegex(gate.AdmissionRejected, "original_MLT"):
                gate.source_compiler_closure(old, repaired, module, modsha, proof, recsha)
            wrong = work / "wrong_patch.c"
            wrong.write_bytes(repaired.read_bytes()[:-1] + b"x")
            with self.assertRaisesRegex(gate.AdmissionRejected, "repaired_MLT"):
                gate.source_compiler_closure(original, wrong, module, modsha, proof, recsha)

    def test_modified_compiler_binary_rejected_even_with_valid_sources(self):
        original, repaired = self.real_originals()
        with tempfile.TemporaryDirectory() as temp:
            module, modsha, proof, recsha = self.compiler(Path(temp))
            module.write_bytes(module.read_bytes() + b"tampered")
            with self.assertRaisesRegex(gate.AdmissionRejected, "exact_expected_ELF"):
                gate.source_compiler_closure(original, repaired, module, modsha, proof, recsha)

    def test_wrong_compiler_provenance_rejected_even_if_module_sha_matches(self):
        original, repaired = self.real_originals()
        with tempfile.TemporaryDirectory() as temp:
            module, modsha, proof, recsha = self.compiler(Path(temp), bad_source=True)
            with self.assertRaisesRegex(gate.AdmissionRejected, "receipt_does_not_bind"):
                gate.source_compiler_closure(original, repaired, module, modsha, proof, recsha)

    def test_duplicate_compiler_receipt_json_fields_rejected(self):
        original, repaired = self.real_originals()
        with tempfile.TemporaryDirectory() as temp:
            module, modsha, proof, recsha = self.compiler(Path(temp), duplicate=True)
            with self.assertRaisesRegex(gate.AdmissionRejected, "duplicate_key"):
                gate.source_compiler_closure(original, repaired, module, modsha, proof, recsha)

    def test_modified_receipt_digest_rejected_without_native_execution(self):
        original, repaired = self.real_originals()
        with tempfile.TemporaryDirectory() as temp:
            module, modsha, proof, recsha = self.compiler(Path(temp))
            proof.write_bytes(proof.read_bytes() + b" ")
            with self.assertRaisesRegex(gate.AdmissionRejected, "receipt_bytes"):
                gate.source_compiler_closure(original, repaired, module, modsha, proof, recsha)

    def test_corrupt_elf_candidate_and_path_escape_rejected(self):
        original, repaired = self.real_originals()
        with tempfile.TemporaryDirectory() as temp:
            module, modsha, proof, recsha = self.compiler(Path(temp))
            module.write_bytes(b"not an ELF")
            with self.assertRaisesRegex(gate.AdmissionRejected, "exact_expected_ELF"):
                gate.source_compiler_closure(original, repaired, module,
                                              hashlib.sha256(b"not an ELF").hexdigest(),
                                              proof, recsha)
            with self.assertRaisesRegex(gate.AdmissionRejected, "absolute_regular"):
                gate.checked_bytes(Path("relative.so"), gate.MAX_ELF_BYTES)

    def test_installed_module_path_is_constant_and_not_user_selected(self):
        self.assertEqual(gate.INSTALLED.as_posix(),
                         "/usr/lib/x86_64-linux-gnu/mlt-7/libmltavformat.so")

    def test_nonexistent_source_cannot_be_read_or_written(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.c"
            with self.assertRaisesRegex(gate.AdmissionRejected, "absolute_regular"):
                gate.checked_bytes(missing, gate.MAX_SOURCE_BYTES)
            self.assertFalse(missing.exists())

    def test_repaired_module_install_requires_linux_uid0_and_ephemeral_allowance(self):
        # In all environments, absence of the explicit owner authorization
        # must fail before preflight can touch an installed shared library.
        with mock.patch.dict(os.environ, {"MOTIONWRIGHT_APPROVED_MLT_G2": "0"}):
            with self.assertRaisesRegex(gate.AdmissionRejected, "disposable_GHA"):
                gate.disposable_linux_runner()
        self.assertFalse(gate.INSTALLED.as_posix().startswith("/tmp/"))


if __name__ == "__main__":
    unittest.main()
