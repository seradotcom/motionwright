"""Strict source-only negative controls for a new MLT repaired-only G1 ELF.

Fixtures use synthetic ELF *headers* as structural negatives, never proof of a
real compiler. A6 must independently execute a compiler+linker in GH Actions,
preserve an original source-bound object, genuine ELF module and same-job oracle
before an authorized G2 module admission.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_repair_only_compiler_receipt as proof
import native_mlt_runtime_admission as admission

SOURCE_HEAD = "ab" * 20
RUN_ID = "39352693034"
RUN_ATTEMPT = "2"


def authentic_sources() -> tuple[Path, Path] | None:
    left = os.environ.get("MOTIONWRIGHT_MLT_V722_ORIGINAL")
    right = os.environ.get("MOTIONWRIGHT_MLT_V722_REPAIRED")
    return (Path(left), Path(right)) if left and right else None


class RepairOnlySourceReceiptTests(unittest.TestCase):
    def real_sources(self) -> tuple[Path, Path]:
        result = authentic_sources()
        if result is None:
            self.skipTest("authentic 7.22.0 source pair required for strict G1")
        return result

    def fixture(self, temp: Path, *, original_source: Path, repaired_source: Path) -> dict:
        compiled = temp / "consumer_avformat.c"
        compiled.write_bytes(repaired_source.read_bytes())
        object_file = temp / "consumer_avformat.c.o"
        object_file.write_bytes(b"\x7fELF" + b"test-structural-object" * 19)
        module = temp / "libmltavformat.so"
        module.write_bytes(b"\x7fELF" + b"test-structural-module" * 37)
        commands = temp / "compile_commands.json"
        arguments = [
            "/usr/bin/cc", "-fPIC", "-c", str(compiled), "-o", str(object_file),
        ]
        commands.write_text(json.dumps([{
            "directory": str(temp),
            "file": str(compiled),
            "arguments": arguments,
        }]), encoding="utf-8")
        linker = temp / "link.txt"
        linker.write_text(
            shlex.join(["/usr/bin/cc", "-shared", "-o", str(module),
                         str(object_file), "-lmlt"]), encoding="utf-8",
        )
        oracle = temp / "independent-G1-oracle.json"
        values = {
            "schema": "motionwright.native.mlt.repaired-only-G1-C-oracle/1",
            "source_head": SOURCE_HEAD,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "original_source_sha256": proof.ORIGINAL_SHA,
            "repaired_source_sha256": proof.REPAIRED_SHA,
            "compiled_consumer_object_sha256": proof.sha(object_file.read_bytes()),
            "compiled_module_sha256": proof.sha(module.read_bytes()),
            "source_constructor_ASAN_UBSAN": "PASS",
            "source_frame_oracle_result": "PASS",
            "legacy_U8P_zero_padding_negative": "CORRECTLY_REJECTED",
            "complete_MLT_consumer_compiler": "PASS",
            "actual_native_effect_performed": False,
        }
        oracle.write_text(json.dumps(values,sort_keys=True),encoding="utf-8")
        return {
            "original": original_source, "repaired": repaired_source,
            "compiled": compiled, "object": object_file, "module": module,
            "commands": commands, "link": linker, "oracle": oracle,
        }

    @staticmethod
    def seal(f: dict) -> dict:
        return proof.build_receipt(
            f["original"], f["repaired"], f["compiled"],
            f["object"], f["module"], f["commands"], f["link"], f["oracle"],
            SOURCE_HEAD, RUN_ID, RUN_ATTEMPT,
        )

    def test_original_unmodified_source_is_exact_and_not_instrumented(self):
        left, right = self.real_sources()
        self.assertEqual(proof.sha(left.read_bytes()), proof.ORIGINAL_SHA)
        self.assertEqual(proof.sha(right.read_bytes()), proof.REPAIRED_SHA)
        self.assertNotEqual(proof.REPAIRED_SHA, proof.COMBINED_DIAGNOSTIC_SHA)

    def test_source_object_compiler_and_module_receipt_bind_same_run(self):
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory), original_source=left, repaired_source=right)
            result = self.seal(f)
            self.assertEqual(result["compiled_source_sha256"], proof.REPAIRED_SHA)
            self.assertFalse(result["instrumented_combined_source_compiled"])
            self.assertEqual(result["compiled_module_sha256"],
                             proof.sha(f["module"].read_bytes()))
            self.assertTrue(result["compiled_consumer_object_linked"])
            self.assertEqual(result["linker_consumer_object_sha256"],
                             result["compiled_consumer_object_sha256"])
            self.assertEqual(result["linker_command_sha256"],
                             proof.sha(f["link"].read_bytes()))
            self.assertEqual(result["source_head"], SOURCE_HEAD)
            self.assertFalse(result["actual_native_effect_performed"])
            output = Path(directory) / "compiled-receipt.json"
            output.write_bytes((json.dumps(result) + "\n").encode())
            self.assertEqual(
                admission.source_compiler_closure(
                    left,right,f["module"],result["compiled_module_sha256"],
                    output,proof.sha(output.read_bytes())
                )["compiled_module_sha256"],
                result["compiled_module_sha256"],
            )

    def test_instrumented_combined_source_or_modified_repaired_C_rejected(self):
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory), original_source=left, repaired_source=right)
            f["compiled"].write_bytes(f["compiled"].read_bytes() + b"\n/* instrumentation */\n")
            with self.assertRaisesRegex(proof.CompilerReceiptRejected, "not_the_uninstrumented"):
                self.seal(f)

    def test_new_oracle_must_bind_exact_source_head_and_attempt(self):
        left, right = self.real_sources()
        for key, value in (
            ("source_head", "cd" * 20),
            ("run_attempt", "1"),
            ("legacy_U8P_zero_padding_negative", "SKIPPED"),
            ("actual_native_effect_performed", True),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                f = self.fixture(Path(directory), original_source=left, repaired_source=right)
                oracle = json.loads(f["oracle"].read_text(encoding="utf-8"))
                oracle[key] = value
                f["oracle"].write_text(json.dumps(oracle), encoding="utf-8")
                with self.assertRaisesRegex(proof.CompilerReceiptRejected,"G1_oracle_missing_or_red"):
                    self.seal(f)

    def test_repaired_object_tamper_is_rejected_against_original_oracle(self):
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory), original_source=left, repaired_source=right)
            f["object"].write_bytes(f["object"].read_bytes() + b"tamper")
            with self.assertRaisesRegex(proof.CompilerReceiptRejected, "G1_oracle_missing_or_red"):
                self.seal(f)

    def test_compiler_row_claiming_repaired_C_but_compiling_another_C_rejected(self):
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory), original_source=left, repaired_source=right)
            foreign = Path(directory) / "foreign-compiled-original.c"
            foreign.write_bytes(left.read_bytes())
            commands = json.loads(f["commands"].read_text(encoding="utf-8"))
            index = commands[0]["arguments"].index("-c")
            commands[0]["arguments"][index + 1] = str(foreign)
            f["commands"].write_text(json.dumps(commands), encoding="utf-8")
            with self.assertRaisesRegex(proof.CompilerReceiptRejected,
                                        "actual_compiler_C_argument_not"):
                self.seal(f)

    def test_unlinked_or_wrong_compiler_output_cannot_pass_command_binding(self):
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory), original_source=left, repaired_source=right)
            commands = json.loads(f["commands"].read_text(encoding="utf-8"))
            commands[0]["arguments"][-1] = str(f["module"])
            f["commands"].write_text(json.dumps(commands), encoding="utf-8")
            with self.assertRaisesRegex(proof.CompilerReceiptRejected,
                                        "consumer_object_is_not_the_source_command_target"):
                self.seal(f)

    def test_original_cmake_workdir_links_repaired_object_to_separate_out_module(self):
        """Actual CMake layout: link.txt executes from src/modules/avformat,
        while the ELF is in build/out/lib/mlt. Both arguments are relative.
        """
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as scratch:
            build_root = Path(scratch) / "mlt-only-new-build"
            cwd = build_root / "src" / "modules" / "avformat"
            cwd.mkdir(parents=True)
            f = self.fixture(cwd, original_source=left, repaired_source=right)
            object_file = cwd / "CMakeFiles" / "mltavformat.dir" / "consumer_avformat.c.o"
            object_file.parent.mkdir(parents=True)
            f["object"].rename(object_file)
            f["object"] = object_file
            destination = build_root / "out" / "lib" / "mlt"
            destination.mkdir(parents=True)
            module = destination / "libmltavformat.so"
            f["module"].rename(module)
            f["module"] = module
            commands = json.loads(f["commands"].read_text())
            commands[0]["arguments"][-1] = "CMakeFiles/mltavformat.dir/consumer_avformat.c.o"
            f["commands"].write_text(json.dumps(commands), encoding="utf-8")
            f["link"].write_text(shlex.join([
                "/usr/bin/cc", "-shared", "-o",
                "../../../out/lib/mlt/libmltavformat.so",
                "CMakeFiles/mltavformat.dir/consumer_avformat.c.o", "-lmlt",
            ]), encoding="utf-8")
            result = self.seal(f)
            self.assertEqual(result["compiler_command"]["compiler_workdir"], str(cwd))
            self.assertEqual(
                result["linker_command"]["original_CMake_link_workdir"], str(cwd)
            )
            self.assertEqual(result["linker_command"]["linked_module"], str(module))
            self.assertEqual(result["linker_command"]["linked_consumer_object"],
                             str(object_file))
            self.assertNotEqual(module.parent, cwd)

    def test_cmake_wrong_link_workdir_fails_instead_of_guessing_module_parent(self):
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as scratch:
            cwd = Path(scratch) / "build" / "src" / "modules" / "avformat"
            cwd.mkdir(parents=True)
            f = self.fixture(cwd, original_source=left, repaired_source=right)
            with self.assertRaises(proof.CompilerReceiptRejected):
                proof.linker_object_binding(
                    f["link"].read_bytes(), f["object"], f["module"],
                    cwd / "not-the-original-CMake-workdir",
                )

    def test_linking_a_foreign_object_rejects_module_provenance(self):
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory), original_source=left, repaired_source=right)
            foreign = Path(directory) / "foreign_unrepaired.c.o"
            foreign.write_bytes(b"\x7fELF" + b"foreign-source-object")
            f["link"].write_text(shlex.join([
                "/usr/bin/cc", "-shared", "-o", str(f["module"]),
                str(foreign), "-lmlt"
            ]), encoding="utf-8")
            with self.assertRaisesRegex(proof.CompilerReceiptRejected,
                                        "exact_repaired_consumer_object_not_linked_once"):
                self.seal(f)

    def test_linking_to_a_different_ELF_does_not_authorize_installed_module(self):
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory), original_source=left, repaired_source=right)
            unrelated = Path(directory) / "libother.so"
            unrelated.write_bytes(b"\x7fELF" + b"foreign-module")
            f["link"].write_text(shlex.join([
                "/usr/bin/cc", "-shared", "-o", str(unrelated),
                str(f["object"]), "-lmlt"
            ]), encoding="utf-8")
            with self.assertRaisesRegex(proof.CompilerReceiptRejected,
                                        "linked_MLT_module_not_the_admitted_candidate"):
                self.seal(f)

    def test_duplicate_build_command_rejected_not_interpreted_as_two_compilers(self):
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory), original_source=left, repaired_source=right)
            arr = json.loads(f["commands"].read_text())
            arr.append(copy.deepcopy(arr[0]))
            f["commands"].write_text(json.dumps(arr),encoding="utf-8")
            with self.assertRaisesRegex(proof.CompilerReceiptRejected, "one_and_only_one"):
                self.seal(f)

    def test_compiler_receipt_duplicate_json_field_not_accepted(self):
        left, right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory), original_source=left, repaired_source=right)
            data = f["oracle"].read_text(encoding="utf-8")
            data = data.replace('"source_head":',
                                '"source_head": "unexpected", "source_head":')
            f["oracle"].write_text(data,encoding="utf-8")
            with self.assertRaisesRegex(proof.CompilerReceiptRejected, "duplicate_key"):
                self.seal(f)

    def test_both_frozen_sources_must_be_regular_and_absolute(self):
        left,right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory), original_source=left,repaired_source=right)
            f["original"] = Path("relative-original.c")
            with self.assertRaisesRegex(proof.CompilerReceiptRejected,"absolute_regular"):
                self.seal(f)

    def test_same_build_oracle_cannot_bind_a_different_compiled_module(self):
        left,right = self.real_sources()
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory),original_source=left,repaired_source=right)
            f["module"].write_bytes(b"\x7fELF"+b"wrong-module-original")
            with self.assertRaisesRegex(proof.CompilerReceiptRejected, "G1_oracle_missing_or_red"):
                self.seal(f)

    def test_simulated_non_linux_cannot_be_a_fake_native_compiler_run(self):
        # Run this negative on both Windows and the actual Linux GHA runner:
        # no platform-dependent skipped test is allowed in strict G1.
        from unittest import mock
        with mock.patch.object(proof.sys, "platform", "win32"), mock.patch.object(
            sys, "argv", ["receipt", "--original-source", "/none",
                          "--repaired-source", "/none", "--compiled-source", "/none",
                          "--consumer-object", "/none", "--compiled-module", "/none",
                          "--compile-commands", "/none", "--link-command", "/none",
                          "--oracle-receipt", "/none",
                          "--receipt", "/none"],
        ):
            with self.assertRaisesRegex(proof.CompilerReceiptRejected,
                                        "GitHub_Linux_run"):
                proof.main()


if __name__ == "__main__":
    unittest.main()
