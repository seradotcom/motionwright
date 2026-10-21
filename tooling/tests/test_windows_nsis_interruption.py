"""Synthetic reader/custody controls; no physical installation is performed."""
from __future__ import annotations
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "windows_nsis_interruption.py"
SPEC = importlib.util.spec_from_file_location("nsis_interruption_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
nsis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nsis)
OPERATION = "{12345678-1234-4234-8234-123456789ABC}"
STAGE = "{ABCDEF12-1234-4234-8234-123456789ABC}"


def checkpoint_text():
    return ("motionwright.nsis-checkpoint/1|id=" + OPERATION
            + "|pid=4321|creation_filetime=134000000000000001"
            + "|phase=main-withdrawn-before-publication|stage=mw-stage-" + STAGE
            + "\\payload.bin|candidate_sha256=" + "a" * 64 + "|candidate_size=4096\r\n")


def checkpoint():
    return nsis.parse_checkpoint(checkpoint_text().encode("utf-16-le"))


def live_context():
    parsed = checkpoint()
    return {"schema": "motionwright.nsis-live-context/1", "state": "LIVE",
            "original_handle_retained": True, "same_handle_identity_observed": True,
            "private_directory_admitted": True, "predecessor_profile_admitted": True,
            **{key: parsed[key] for key in ("id", "pid", "creation_filetime",
                                           "candidate_sha256", "candidate_size")},
            "setup_sha256": "b" * 64, "predecessor_profile_sha256": "c" * 64,
            "gate_sha": "1" * 40, "product_sha": "2" * 40,
            "run_id": "123456", "run_attempt": "2", "job": "compile", "launch_count": 1}


def terminal():
    context = live_context()
    return {"schema": "motionwright.nsis-terminal/1",
            **{key: context[key] for key in ("id", "pid", "creation_filetime", "setup_sha256",
                "predecessor_profile_sha256", "gate_sha", "product_sha", "run_id", "run_attempt", "job")},
            "state": "TERMINATED", "termination_attempts": 1, "same_original_handle": True,
            "terminate_returned_success": True, "wait_result": "WAIT_OBJECT_0",
            "exit_code": 99, "ambiguous": False}


class CheckpointContractTests(unittest.TestCase):
    def test_exact_original_bytes_and_decimal64_are_retained(self):
        raw = checkpoint_text().encode("utf-16-le")
        parsed = nsis.parse_checkpoint(raw)
        self.assertEqual(parsed["original_sha256"], nsis.digest(raw))
        self.assertEqual(parsed["original_bytes"], len(raw))
        self.assertEqual(parsed["creation_filetime"], "134000000000000001")
        self.assertIs(type(parsed["creation_filetime"]), str)
        self.assertNotEqual(OPERATION, STAGE)

    def test_encoding_bom_partial_and_trailing_bytes_refused(self):
        raw = checkpoint_text().encode("utf-16-le")
        for value in (b"", raw[:-1], raw + b"\x00\x00", b"\xff\xfe" + raw,
                      checkpoint_text().encode("utf-8"), checkpoint_text().encode("utf-16-be"),
                      b"\x00\xd8", raw * 4):
            with self.subTest(length=len(value)), self.assertRaises(nsis.ContractError):
                nsis.parse_checkpoint(value)

    def test_field_order_extras_and_phase_are_closed(self):
        original = checkpoint_text()
        for value in (
            original.replace("|pid=4321|creation_filetime=134000000000000001",
                             "|creation_filetime=134000000000000001|pid=4321"),
            original.replace("\r\n", "|extra=true\r\n"),
            original.replace("|pid=4321", "|PID=4321"),
            original.replace("main-withdrawn-before-publication", "after-publication"),
            original.replace("\r\n", "\n"), original + "\r\n",
            original.replace("|candidate_size=4096", "")):
            with self.subTest(value=value), self.assertRaises(nsis.ContractError):
                nsis.parse_checkpoint(value.encode("utf-16-le"))

    def test_guid_canonical_nonzero_and_stage_confinement(self):
        original = checkpoint_text()
        for value in (original.replace(OPERATION, OPERATION.lower()),
            original.replace(OPERATION, "{00000000-0000-0000-0000-000000000000}"),
            original.replace("mw-stage-", "..\\mw-stage-"),
            original.replace("\\payload.bin", "/payload.bin"),
            original.replace("\\payload.bin", "\\..\\payload.bin"),
            original.replace(STAGE, STAGE.lower())):
            with self.subTest(value=value), self.assertRaises(nsis.ContractError):
                nsis.parse_checkpoint(value.encode("utf-16-le"))

    def test_pid_filetime_and_size_exact_bounds(self):
        for field, value in (("pid=4321", "pid=0"), ("pid=4321", "pid=04321"),
                ("pid=4321", "pid=4294967296"),
                ("creation_filetime=134000000000000001", "creation_filetime=18446744073709551616"),
                ("creation_filetime=134000000000000001", "creation_filetime=1.34e17"),
                ("candidate_size=4096", "candidate_size=9223372036854775808"),
                ("candidate_size=4096", "candidate_size=0")):
            with self.subTest(value=value), self.assertRaises(nsis.ContractError):
                nsis.parse_checkpoint(checkpoint_text().replace(field, value).encode("utf-16-le"))

    def test_candidate_digest_is_canonical_and_complete(self):
        for value in ("A" * 64, "a" * 63, "g" * 64, "a" * 65):
            with self.subTest(value=value), self.assertRaises(nsis.ContractError):
                nsis.parse_checkpoint(checkpoint_text().replace("a" * 64, value).encode("utf-16-le"))

    def test_positive_live_reader_does_not_promote_physical_execution(self):
        result = nsis.validate_live_context(checkpoint(), live_context())
        self.assertEqual(result["result"], "STRUCTURALLY_ADMITTED")
        self.assertEqual(result["physical_execution"], "NOT_RUN")
        self.assertEqual(result["human_acceptance"], "NOT_RUN")

    def test_live_identity_and_guid_swaps_fail(self):
        for key, value in (("id", STAGE), ("pid", "4322"),
                ("creation_filetime", "134000000000000002"), ("candidate_sha256", "d" * 64),
                ("candidate_size", "4097"), ("creation_filetime", 134000000000000001.0)):
            changed = live_context()
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(nsis.ContractError):
                nsis.validate_live_context(checkpoint(), changed)

    def test_live_handle_authority_flags_required(self):
        for key in ("original_handle_retained", "same_handle_identity_observed",
                    "private_directory_admitted", "predecessor_profile_admitted"):
            for invalid in (False, None, "true", 1):
                changed = live_context()
                changed[key] = invalid
                with self.subTest(key=key, value=invalid), self.assertRaises(nsis.ContractError):
                    nsis.validate_live_context(checkpoint(), changed)

    def test_unknown_expired_or_relaunched_context_refused(self):
        for key, value in (("state", "UNKNOWN"), ("state", "EXITED"),
                           ("launch_count", 2), ("launch_count", True)):
            changed = live_context()
            changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(nsis.ContractError):
                nsis.validate_live_context(checkpoint(), changed)

    def test_missing_or_malformed_source_and_run_bindings_refused(self):
        for key, value in (("gate_sha", "1" * 39), ("product_sha", "main"),
                           ("setup_sha256", "b" * 63), ("predecessor_profile_sha256", ""),
                           ("run_id", "0"), ("run_attempt", 2), ("job", "job\nother")):
            changed = live_context()
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(nsis.ContractError):
                nsis.validate_live_context(checkpoint(), changed)

    def test_known_terminal_reader_cannot_authorize_recovery(self):
        result = nsis.validate_termination(checkpoint(), live_context(), terminal())
        self.assertEqual(result["recovery"], "NOT_AUTHORIZED_BY_READER")
        self.assertEqual(result["physical_execution"], "NOT_RUN")

    def test_exit4_unknown_ambiguous_and_missing_wait_are_rejected(self):
        for key, value in (("exit_code", 4), ("exit_code", None), ("exit_code", True),
                ("state", "UNKNOWN"), ("ambiguous", True),
                ("wait_result", "WAIT_TIMEOUT"), ("terminate_returned_success", False),
                ("same_original_handle", False), ("termination_attempts", 2),
                ("termination_attempts", True)):
            changed = terminal()
            changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(nsis.ContractError):
                nsis.validate_termination(checkpoint(), live_context(), changed)

    def test_terminal_source_run_handle_and_operation_swaps_rejected(self):
        for key, value in (("gate_sha", "9" * 40), ("product_sha", "8" * 40),
                ("run_id", "654321"), ("run_attempt", "3"), ("job", "other"),
                ("id", STAGE), ("pid", "7777"), ("creation_filetime", "134000000000000002"),
                ("setup_sha256", "d" * 64), ("predecessor_profile_sha256", "e" * 64)):
            changed = terminal()
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(nsis.ContractError):
                nsis.validate_termination(checkpoint(), live_context(), changed)

    def test_terminal_reader_revalidates_context(self):
        context = live_context()
        context["state"] = "UNKNOWN"
        with self.assertRaises(nsis.ContractError):
            nsis.validate_termination(checkpoint(), context, terminal())


class CompileCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-nsis-reader-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


    def compiled_fixture(self, label="normal", variant="normal",
                         alter_render=None, alter_include=None):
        # Honest synthetic reader inputs matching the producer schemas.
        # No executable generated by this fixture is physical PE evidence.
        output = self.root / label
        target = output / "target"
        original_render = target / "release/nsis/x64"
        original_render.mkdir(parents=True)
        main = target / "release/motionwright-desktop.exe"
        main.write_bytes(b"MZsynthetic-original-application")
        identity = nsis.regular_file(main)
        application = nsis.retain_built_application(main, output, identity)
        expected = {"PRODUCTNAME": "Motionwright", "BUNDLEID": "com.seradotcom.motionwright",
                    "ARCH": "x64", "INSTALLMODE": "currentUser", "VERSION": "0.1.1",
                    "MAINBINARYNAME": "motionwright-desktop", "MAINBINARYSRCPATH": str(main)}
        rendered = ("!define MWINST_CI_INTERRUPTION\n" if variant == "opt-in" else "")
        rendered += "Unicode true\n"
        rendered += "".join(f'!define {name} "{value}"\n' for name, value in expected.items())
        if alter_render:
            rendered = alter_render(rendered, expected)
        (original_render / "installer.nsi").write_bytes(rendered.encode("utf-8-sig"))
        capture = original_render / "mw-owned-MW_MAIN.bin"
        capture.write_bytes(main.read_bytes())
        include = (f'!define MW_MAIN_SOURCE "{capture}"\n'
                   f'!define MW_MAIN_SHA256 "{identity["sha256"]}"\n'
                   f'!define MW_MAIN_SIZE {identity["bytes"]}\n')
        folder = output / "variants" / variant
        if alter_include:
            include = alter_include(include, capture, folder, identity)
        (original_render / "mw-owned-MW_MAIN.nsh").write_bytes(include.encode("utf-8-sig"))
        bundle = target / "release/bundle/nsis"
        bundle.mkdir(parents=True)
        (bundle / "Motionwright_0.1.1_x64-setup.exe").write_bytes(b"MZsynthetic-setup")
        nsis.retain_variant(target, folder)
        return folder, identity, expected, application

    def test_source_template_prefix_is_only_opt_in_change(self):
        source = (b"; synthetic source\n!ifdef MWINST_CI_INTERRUPTION\n!endif\n"
                  b"Function MWINST_ArmInterruption\nFunctionEnd\n"
                  b"Function MWINST_InterruptMainAfterWithdrawal\nFunctionEnd\n")
        fixture = nsis.opt_in_template(source)
        self.assertEqual(fixture, nsis.OPT_IN_PREFIX + source)
        self.assertEqual(fixture[len(nsis.OPT_IN_PREFIX):], source)
        with self.assertRaises(nsis.ContractError):
            nsis.opt_in_template(fixture)
        with self.assertRaises(nsis.ContractError):
            nsis.opt_in_template(b"no checkpoint implementation\n")

    def test_create_new_preserves_existing_original(self):
        target = self.root / "receipt.json"
        nsis.write_new(target, b"original")
        with self.assertRaises(FileExistsError):
            nsis.write_new(target, b"replacement")
        self.assertEqual(target.read_bytes(), b"original")

    def test_regular_file_digest_and_directory_rejection(self):
        target = self.root / "payload.bin"
        target.write_bytes(b"synthetic payload")
        self.assertEqual(nsis.regular_file(target),
                         {"bytes": 17, "sha256": nsis.digest(b"synthetic payload")})
        with self.assertRaises(nsis.ContractError):
            nsis.regular_file(self.root)

    def test_partial_compile_outputs_are_retained_without_declaring_success(self):
        target = self.root / "target"
        render = target / "release/nsis/x64"
        render.mkdir(parents=True)
        (render / "partial.txt").write_bytes(b"partial compiler output")
        folder = self.root / "normal"
        nsis.retain_variant(target, folder)
        self.assertEqual((folder / "render/x64/partial.txt").read_bytes(), b"partial compiler output")
        self.assertFalse((target / "release/nsis").exists())
        with self.assertRaises(nsis.ContractError):
            nsis.inspect_variant(folder, "normal", {"bytes": 1, "sha256": "a" * 64}, {}, {})

    def test_retained_variant_destination_cannot_be_replaced(self):
        target = self.root / "target"
        source = target / "release/nsis"
        source.mkdir(parents=True)
        (source / "original.txt").write_bytes(b"first")
        folder = self.root / "normal"
        nsis.retain_variant(target, folder)
        source.mkdir(parents=True)
        (source / "original.txt").write_bytes(b"second")
        with self.assertRaises(nsis.ContractError):
            nsis.retain_variant(target, folder)
        self.assertEqual((folder / "render/original.txt").read_bytes(), b"first")
        self.assertEqual((source / "original.txt").read_bytes(), b"second")

    def test_compile_artifact_reader_requires_correct_define_and_payload(self):
        folder, main, expected, application = self.compiled_fixture()
        result = nsis.inspect_variant(folder, "normal", main, expected, application)
        self.assertEqual(result["result"], "COMPILE_ONLY")
        self.assertEqual(result["installer_execution"], "NOT_RUN")
        self.assertEqual(result["physical_pe_version"], "NOT_OBSERVED")
        with self.assertRaises(nsis.ContractError):
            nsis.inspect_variant(folder, "opt-in", main, expected, application)
        with self.assertRaises(nsis.ContractError):
            nsis.inspect_variant(folder, "normal", {"bytes": 1, "sha256": "a" * 64},
                                 expected, application)

    def test_opt_in_uses_original_capture_path_after_retention(self):
        folder, main, expected, application = self.compiled_fixture(variant="opt-in")
        result = nsis.inspect_variant(folder, "opt-in", main, expected, application)
        original = Path(expected["MAINBINARYSRCPATH"]).parent / "nsis/x64/mw-owned-MW_MAIN.bin"
        self.assertEqual(result["original_capture_path"], str(original))
        self.assertFalse(original.exists())
        self.assertTrue((folder / "render/x64/mw-owned-MW_MAIN.bin").is_file())
        self.assertEqual(result["main_capture"], application["identity"])

    def test_rendered_identity_swaps_missing_and_duplicate_defines_are_rejected(self):
        for index, name in enumerate(("PRODUCTNAME", "BUNDLEID", "ARCH", "INSTALLMODE",
                                      "VERSION", "MAINBINARYNAME")):
            def alter(text, expected, name=name):
                return text.replace(f'!define {name} "{expected[name]}"',
                                    f'!define {name} "wrong-value"')
            with self.subTest(name=name):
                args = self.compiled_fixture("wrong-identity-" + str(index), alter_render=alter)
                with self.assertRaisesRegex(nsis.ContractError, "rendered_identity_mismatch:" + name):
                    nsis.inspect_variant(args[0], "normal", *args[1:])
        for index, alter in enumerate((
            lambda text, _: text + '!define VERSION "0.1.1"\n',
            lambda text, _: text.replace('!define VERSION "0.1.1"\n', ""),
            lambda text, _: text + "!define VERSION invalid-unquoted\n")):
            with self.subTest(index=index):
                args = self.compiled_fixture("bad-define-" + str(index), alter_render=alter)
                with self.assertRaisesRegex(nsis.ContractError,
                                            "rendered_definition_missing_or_duplicated:VERSION"):
                    nsis.inspect_variant(args[0], "normal", *args[1:])

    def test_rendered_main_source_and_relocated_include_source_are_rejected(self):
        def wrong_main(text, expected):
            return text.replace(expected["MAINBINARYSRCPATH"],
                                str(Path(expected["MAINBINARYSRCPATH"]).with_name("other.exe")))
        args = self.compiled_fixture("wrong-main", alter_render=wrong_main)
        with self.assertRaisesRegex(nsis.ContractError, "rendered_main_source_mismatch"):
            nsis.inspect_variant(args[0], "normal", *args[1:])
        def relocated(text, capture, folder, _):
            return text.replace(str(capture), str(folder / "render/x64/mw-owned-MW_MAIN.bin"))
        args = self.compiled_fixture("relocated-include", alter_include=relocated)
        with self.assertRaisesRegex(nsis.ContractError, "capture_include_source_mismatch"):
            nsis.inspect_variant(args[0], "normal", *args[1:])

    def test_capture_include_unknown_extra_duplicate_and_payload_swaps_are_rejected(self):
        cases = (
            (lambda text, *_: "synthetic include", "capture_include_closed_schema"),
            (lambda text, *_: text + '!define UNKNOWN "value"\n', "capture_include_closed_schema"),
            (lambda text, *_: text + text.splitlines()[0] + "\n", "capture_include_closed_schema"),
            (lambda text, *_: "\n".join(reversed(text.splitlines())) + "\n",
             "capture_include_closed_schema"),
            (lambda text, _capture, _folder, identity:
                text.replace(identity["sha256"], "a" * 64), "capture_include_payload_mismatch"),
            (lambda text, _capture, _folder, identity:
                text.replace(f'MW_MAIN_SIZE {identity["bytes"]}', "MW_MAIN_SIZE 1"),
             "capture_include_payload_mismatch"),
        )
        for index, (alter, reason) in enumerate(cases):
            with self.subTest(index=index):
                args = self.compiled_fixture("bad-include-" + str(index), alter_include=alter)
                with self.assertRaisesRegex(nsis.ContractError, reason):
                    nsis.inspect_variant(args[0], "normal", *args[1:])

    def test_retained_application_mutation_cannot_be_admitted(self):
        args = self.compiled_fixture()
        Path(args[3]["retained_path"]).write_bytes(b"MZchanged")
        with self.assertRaisesRegex(nsis.ContractError, "retained_application_changed"):
            nsis.inspect_variant(args[0], "normal", *args[1:])

    def test_built_application_is_retained_before_any_bundle_and_is_exclusive(self):
        output = self.root / "output"
        main = self.root / "motionwright-desktop.exe"
        original = b"MZbuilt-before-bundle"
        main.write_bytes(original)
        identity = nsis.regular_file(main)
        record = nsis.retain_built_application(main, output, identity)
        self.assertEqual(Path(record["retained_path"]).read_bytes(), original)
        self.assertEqual(record["original_path"], str(main.absolute()))
        self.assertEqual(record["identity"], identity)
        self.assertEqual(record["physical_pe_version"], "NOT_OBSERVED")
        self.assertFalse((output / "variants").exists())
        with self.assertRaises(FileExistsError):
            nsis.retain_built_application(main, output, identity)
        self.assertEqual(Path(record["retained_path"]).read_bytes(), original)

    def test_archive_member_and_file_count_limits_precede_payload_hashing(self):
        render = self.root / "render"
        render.mkdir()
        (render / "installer.nsi").write_bytes(b"12345")
        with mock.patch.object(nsis, "MAX_RENDER_BYTES", 4):
            with mock.patch.object(nsis, "regular_file") as hashed:
                with self.assertRaisesRegex(nsis.ContractError, "artifact_member_size_bound"):
                    nsis.tree_manifest(render)
                hashed.assert_not_called()
        (render / "second.txt").write_bytes(b"1")
        with mock.patch.object(nsis, "MAX_DIAGNOSTIC_FILES", 1):
            with mock.patch.object(nsis, "regular_file") as hashed:
                with self.assertRaisesRegex(nsis.ContractError, "artifact_file_bound"):
                    nsis.tree_manifest(render)
                hashed.assert_not_called()

    def test_combined_render_bundle_byte_limit_precedes_hashing_or_move(self):
        target = self.root / "target"
        render = target / "release/nsis"
        bundle = target / "release/bundle/nsis"
        render.mkdir(parents=True)
        bundle.mkdir(parents=True)
        (render / "first.bin").write_bytes(b"12")
        (bundle / "second.bin").write_bytes(b"34")
        with mock.patch.object(nsis, "MAX_DIAGNOSTIC_BYTES", 3):
            with mock.patch.object(nsis, "regular_file") as hashed:
                with self.assertRaisesRegex(nsis.ContractError, "variant_total_size_bound"):
                    nsis.retain_variant(target, self.root / "archive")
                hashed.assert_not_called()
        self.assertTrue((render / "first.bin").is_file())
        self.assertTrue((bundle / "second.bin").is_file())

    def test_regular_file_bound_precedes_open_and_mz_reads_only_two_bytes(self):
        target = self.root / "large.bin"
        target.write_bytes(b"12345")
        with mock.patch.object(Path, "open", side_effect=AssertionError("must not open")):
            with self.assertRaisesRegex(nsis.ContractError, "file_size_bound"):
                nsis.regular_file(target, 4)
        calls = []
        class Reader(io.BytesIO):
            def read(self, size=-1):
                calls.append(size)
                return super().read(size)
        expected = {"bytes": 100, "sha256": "a" * 64}
        with mock.patch.object(nsis, "regular_file", return_value=expected):
            with mock.patch.object(Path, "open", return_value=Reader(b"MZ" + b"x" * 98)):
                self.assertEqual(nsis.pe_header_only(target, expected), expected)
        self.assertEqual(calls, [2])

    def test_diagnostic_budget_counts_archive_roots_and_excludes_build_caches(self):
        output = self.root / "output"
        for path, raw in (("inputs/original.bin", b"1"), ("target/release/deps/large.bin", b"12"),
                          ("npm-cache/large.bin", b"12"), ("node_modules/large.bin", b"12")):
            file = output / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(raw)
        with mock.patch.object(nsis, "MAX_PAYLOAD_BYTES", 1):
            result = nsis.diagnostic_budget(output)
        self.assertEqual(result["bytes"], 1)
        self.assertEqual(result["files"], 1)
        self.assertFalse(result["unknown_outputs_stable"])
        (output / "commands").mkdir()
        (output / "commands/log.bin").write_bytes(b"12")
        with mock.patch.object(nsis, "MAX_DIAGNOSTIC_BYTES", 2):
            with self.assertRaisesRegex(nsis.ContractError, "diagnostic_total_size_bound"):
                nsis.diagnostic_budget(output)

    def test_command_nonzero_exit_preserves_original_streams_and_stops(self):
        command = [sys.executable, "-B", "-c",
                   "import os,sys;os.write(1,b'original-out');os.write(2,b'original-err');sys.exit(7)"]
        with self.assertRaises(nsis.ContractError):
            nsis.command(self.root, "failure", command, self.root, dict(os.environ), 20)
        folder = self.root / "commands/failure"
        result = json.loads((folder / "terminal.json").read_bytes())
        self.assertEqual(result["state"], "ERROR")
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["attempt"], 1)
        self.assertEqual((folder / "stdout.bin").read_bytes(), b"original-out")
        self.assertEqual((folder / "stderr.bin").read_bytes(), b"original-err")
        with self.assertRaises(FileExistsError):
            nsis.command(self.root, "failure", command, self.root, dict(os.environ), 20)

    def test_oversized_stream_preserves_original_exit_and_terminal_receipt(self):
        command = [sys.executable, "-B", "-c", "import sys;print('original');sys.exit(7)"]
        with mock.patch.object(nsis, "MAX_PAYLOAD_BYTES", 1):
            with self.assertRaisesRegex(nsis.ContractError, "command_failed:oversized:ERROR"):
                nsis.command(self.root, "oversized", command, self.root, dict(os.environ), 20)
        record = json.loads((self.root / "commands/oversized/terminal.json").read_bytes())
        self.assertEqual(record["state"], "ERROR")
        self.assertEqual(record["exit_code"], 7)
        self.assertFalse(record["stream_custody_complete"])
        self.assertEqual(record["stdout.bin"]["state"], "RETAINED_NOT_ADMITTED")
        self.assertTrue((self.root / "commands/oversized/stdout.bin").is_file())

    def test_unknown_command_is_never_retried(self):
        with mock.patch.object(nsis.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(["synthetic-command"], 1)) as run:
            with self.assertRaises(nsis.ContractError):
                nsis.command(self.root, "unknown", ["synthetic-command"], self.root, dict(os.environ), 1)
        self.assertEqual(run.call_count, 1)
        result = json.loads((self.root / "commands/unknown/terminal.json").read_bytes())
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertIsNone(result["exit_code"])
        self.assertEqual(result["attempt"], 1)

    def test_source_head_and_dirty_worktree_rejected(self):
        with mock.patch.object(nsis, "git", return_value=b"2" * 40 + b"\n"):
            with self.assertRaisesRegex(nsis.ContractError, "source_head_mismatch"):
                nsis.source_observation(self.root, "1" * 40, ("a",))
        with mock.patch.object(nsis, "git", side_effect=[b"1" * 40 + b"\n", b" M a\n"]):
            with self.assertRaisesRegex(nsis.ContractError, "tracked_source_dirty"):
                nsis.source_observation(self.root, "1" * 40, ("a",))

    def test_source_observation_keeps_git_and_checkout_bytes_distinct(self):
        (self.root / "a").write_bytes(b"original\r\n")
        replies = [b"1" * 40 + b"\n", b"", b"a\n", b"original\n", b"2" * 40 + b"\n"]
        with mock.patch.object(nsis, "git", side_effect=replies):
            observed = nsis.source_observation(self.root, "1" * 40, ("a",))
        self.assertEqual(observed["sha"], "1" * 40)
        self.assertNotEqual(observed["files"]["a"]["git_sha256"],
                            observed["files"]["a"]["checkout"]["sha256"])
        self.assertEqual(observed["files"]["a"]["checkout_encoding"], "EXACT_LF_TO_CRLF")

    def test_source_byte_mutation_rejected_even_if_git_status_reports_clean(self):
        (self.root / "a").write_bytes(b"mutated\n")
        replies = [b"1" * 40 + b"\n", b"", b"a\n", b"original\n"]
        with mock.patch.object(nsis, "git", side_effect=replies):
            with self.assertRaisesRegex(nsis.ContractError, "checkout_bytes_not_git_or_exact_crlf"):
                nsis.source_observation(self.root, "1" * 40, ("a",))


if __name__ == "__main__":
    unittest.main()
