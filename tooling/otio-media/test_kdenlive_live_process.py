"""Fail-closed source and process identity controls for real Kdenlive G2.

These fixtures do not launch an editor or fabricate a native GUI receipt.
A selected Linux app test must supply two separate, actual running Kdenlive
processes and independently validate original/edited native OTIO media.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from kdenlive_live_process import (
    NativeKdenliveProcessRejected, check_commandline,
    compare_distinct_native_sessions, inspect_live_native_kdenlive,
    parse_native_kdenlive_version, process_starttime, read_exact_file,
)


class KdenliveNativeProcessControls(unittest.TestCase):
    def test_actual_kernel_stat_22nd_field_even_with_parentheses(self):
        pid = 4321
        line = (
            f"{pid} (kdenlive (original project)) S "
            + " ".join(str(n) for n in range(4, 26))
            + "\n"
        ).encode("ascii")
        self.assertEqual(process_starttime(line, pid), 22)

    def test_process_starttime_from_foreign_pid_cannot_be_reused(self):
        sample = b"4321 (kdenlive) S " + b" ".join(
            str(x).encode() for x in range(4, 26)
        )
        with self.assertRaisesRegex(NativeKdenliveProcessRejected, "original_pid"):
            process_starttime(sample, 1234)
        with self.assertRaisesRegex(NativeKdenliveProcessRejected, "pid_or_stat"):
            process_starttime(sample, 1)

    def test_invalid_or_non_kernel_process_birth_rejected(self):
        for raw in [b"", b"4321 (kdenlive) S 1", b"4321 () S " +
                    b" ".join(str(x).encode() for x in range(4, 26))]:
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(NativeKdenliveProcessRejected):
                    process_starttime(raw, 4321)

    def test_reopen_commandline_requires_exact_saved_native_path(self):
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / "source.kdenlive"
            argv = (
                b"/usr/bin/kdenlive\0--new-instance\0"
                + str(native).encode() + b"\0"
            )
            result = check_commandline(
                argv, Path("/usr/bin/kdenlive"), "reopen", native,
            )
            self.assertTrue(result["saved_project_was_an_exact_launch_argument"])
            self.assertEqual(result["native_process_phase"], "reopen")

    def test_reopen_rejects_foreign_native_project_or_basename_only(self):
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / "original.kdenlive"
            for argv in [
                b"/usr/bin/kdenlive\0different.kdenlive\0",
                b"/usr/bin/kdenlive\0original.kdenlive\0",
                b"/usr/bin/kdenlive\0/foreign/original.kdenlive\0",
                b"/usr/bin/kdenlive\0",
            ]:
                with self.subTest(argv=argv):
                    with self.assertRaisesRegex(
                        NativeKdenliveProcessRejected, "saved_project",
                    ):
                        check_commandline(
                            argv, Path("/usr/bin/kdenlive"), "reopen", native,
                        )

    def test_import_phase_never_claims_otio_import_from_process_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / "original.kdenlive"
            result = check_commandline(
                b"/usr/bin/kdenlive\0--new-instance\0",
                Path("/usr/bin/kdenlive"), "import", native,
            )
            self.assertFalse(result["saved_project_was_an_exact_launch_argument"])
            self.assertEqual(result["bounded_arg_count"], 2)

    def test_malformed_argv_and_foreign_binary_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / "source.kdenlive"
            for raw, exe in [
                (b"", Path("/usr/bin/kdenlive")),
                (b"not terminated", Path("/usr/bin/kdenlive")),
                (b"/usr/bin/not-an-editor\0", Path("/usr/bin/kdenlive")),
                (b"/usr/bin/kdenlive\0", Path("/usr/bin/python")),
            ]:
                with self.subTest(raw=raw):
                    with self.assertRaises(NativeKdenliveProcessRejected):
                        check_commandline(raw, exe, "import", native)

    def test_source_file_rehash_detects_bit_modified_original(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "scene.otio"
            raw = b'{"OTIO_SCHEMA":"Timeline.1"}\n'
            original.write_bytes(raw)
            sha = hashlib.sha256(raw).hexdigest()
            first = read_exact_file(original, sha, 1024)
            self.assertEqual(first["sha256"], sha)
            original.write_bytes(raw + b"changed")
            with self.assertRaisesRegex(
                NativeKdenliveProcessRejected, "source_changed"
            ):
                read_exact_file(original, sha, 1024)

    def test_oversized_or_symlinked_original_path_never_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "source.otio"
            original.write_bytes(b"untrusted data")
            sha = hashlib.sha256(original.read_bytes()).hexdigest()
            with self.assertRaisesRegex(
                NativeKdenliveProcessRejected, "byte_budget"
            ):
                read_exact_file(original, sha, 3)
            actual = Path.is_symlink
            with mock.patch.object(
                Path, "is_symlink",
                lambda path: path == original or actual(path),
            ):
                with self.assertRaisesRegex(
                    NativeKdenliveProcessRejected, "source_path"
                ):
                    read_exact_file(original, sha, 1024)

    @staticmethod
    def stage_receipt(*, phase: str, pid: int, start: int) -> dict:
        return {
            "schema": "motionwright.native.kdenlive-live-process/1",
            "phase": phase,
            "pid": pid,
            "kernel_starttime_ticks": start,
            "actual_executable_sha256": "ab" * 32,
            "original_Kdenlive_application_version": "25.4.3",
            "source_original_OTIO_sha256": "cd" * 32,
            "saved_native_project_path": "/ci/imported-original.kdenlive",
            "saved_native_project_sha256": "ef" * 32,
            "reopen_input_project_path": (
                "/ci/imported-original.kdenlive" if phase == "reopen" else None
            ),
            "reopen_input_project_sha256": "ef" * 32 if phase == "reopen" else None,
            "process_arguments": {
                "saved_project_was_an_exact_launch_argument": phase == "reopen",
                "native_project_path_checked": "/ci/imported-original.kdenlive",
            },
            "live_application_binary_and_pid_verified": True,
        }

    def test_distinct_native_import_and_reopen_sessions_are_source_bound(self):
        original = self.stage_receipt(phase="import", pid=12, start=1234)
        reopened = self.stage_receipt(phase="reopen", pid=23, start=1450)
        value = compare_distinct_native_sessions(original, reopened)
        self.assertTrue(value["two_distinct_live_native_process_snapshots"])
        self.assertTrue(value["reopened_process_received_original_saved_path"])
        self.assertFalse(value["native_GUI_OTIO_import_or_export_verified_here"])
        self.assertFalse(value["human_acceptance"])

    def test_reopen_input_path_must_match_first_saved_project_even_with_same_hash(self):
        original = self.stage_receipt(phase="import", pid=12, start=1234)
        reopened = self.stage_receipt(phase="reopen", pid=23, start=1450)
        foreign_path = "/ci/different-project.kdenlive"
        reopened["reopen_input_project_path"] = foreign_path
        reopened["process_arguments"]["native_project_path_checked"] = foreign_path
        self.assertEqual(
            original["saved_native_project_sha256"],
            reopened["reopen_input_project_sha256"],
        )
        with self.assertRaisesRegex(
            NativeKdenliveProcessRejected, "reopen_input_path"
        ):
            compare_distinct_native_sessions(original, reopened)

    def test_reopen_input_hash_must_match_first_saved_project_at_same_path(self):
        original = self.stage_receipt(phase="import", pid=12, start=1234)
        reopened = self.stage_receipt(phase="reopen", pid=23, start=1450)
        reopened["reopen_input_project_sha256"] = "12" * 32
        self.assertEqual(
            original["saved_native_project_path"],
            reopened["reopen_input_project_path"],
        )
        # The observed output hash is still equal; only the input was replaced.
        self.assertEqual(
            original["saved_native_project_sha256"],
            reopened["saved_native_project_sha256"],
        )
        with self.assertRaisesRegex(
            NativeKdenliveProcessRejected, "reopen_input_SHA"
        ):
            compare_distinct_native_sessions(original, reopened)

    def test_reopen_binds_original_input_separately_from_edited_output(self):
        original = self.stage_receipt(phase="import", pid=12, start=1234)
        reopened = self.stage_receipt(phase="reopen", pid=23, start=1450)
        reopened["saved_native_project_path"] = "/ci/edited-output.kdenlive"
        reopened["saved_native_project_sha256"] = "12" * 32
        value = compare_distinct_native_sessions(original, reopened)
        self.assertTrue(value["reopened_process_received_original_saved_path"])
        self.assertEqual(
            value["reopen_input_project_path"],
            original["saved_native_project_path"],
        )
        self.assertEqual(
            value["reopen_input_project_sha256"],
            original["saved_native_project_sha256"],
        )
        self.assertEqual(
            value["reopened_native_project_path"],
            reopened["saved_native_project_path"],
        )
        self.assertNotEqual(
            value["initial_native_project_sha256"],
            value["reopened_native_project_sha256"],
        )
        self.assertFalse(value["native_GUI_OTIO_import_or_export_verified_here"])
        self.assertFalse(value["human_acceptance"])

    def test_incomplete_older_receipts_cannot_claim_original_project_reopen(self):
        for section, missing in (
            ("first", "saved_native_project_path"),
            ("reopened", "saved_native_project_path"),
            ("reopened", "reopen_input_project_path"),
            ("reopened", "reopen_input_project_sha256"),
            ("argv", "native_project_path_checked"),
        ):
            with self.subTest(section=section, missing=missing):
                original = self.stage_receipt(phase="import", pid=12, start=1234)
                reopened = self.stage_receipt(phase="reopen", pid=23, start=1450)
                target = (original if section == "first" else
                          reopened["process_arguments"] if section == "argv" else
                          reopened)
                target.pop(missing)
                with self.assertRaises(NativeKdenliveProcessRejected):
                    compare_distinct_native_sessions(original, reopened)

    def test_same_native_process_cannot_impersonate_application_reopen(self):
        original = self.stage_receipt(phase="import", pid=12, start=1234)
        reopened = self.stage_receipt(phase="reopen", pid=12, start=1234)
        with self.assertRaisesRegex(
            NativeKdenliveProcessRejected, "newer_distinct_process"
        ):
            compare_distinct_native_sessions(original, reopened)

    def test_older_reopen_or_untrusted_source_does_not_pass(self):
        original = self.stage_receipt(phase="import", pid=12, start=1234)
        reopened = self.stage_receipt(phase="reopen", pid=23, start=1100)
        with self.assertRaisesRegex(
            NativeKdenliveProcessRejected, "newer_distinct_process"
        ):
            compare_distinct_native_sessions(original, reopened)
        reopened["kernel_starttime_ticks"] = 1400
        reopened["source_original_OTIO_sha256"] = "11" * 32
        with self.assertRaisesRegex(
            NativeKdenliveProcessRejected, "source"
        ):
            compare_distinct_native_sessions(original, reopened)

    def test_Kdenlive_native_OTIO_export_fix_minimum_version(self):
        accepted = [
            (b"kdenlive 25.04.3\n", "25.4.3"),
            (b"Kdenlive version 25.08.1\n", "25.8.1"),
            (b"kdenlive v26.04.0\n", "26.4.0"),
        ]
        for raw, expected in accepted:
            with self.subTest(raw=raw):
                self.assertEqual(parse_native_kdenlive_version(raw), expected)
        for raw in [b"kdenlive 25.04.0\n", b"kdenlive 25.04.2\n",
                    b"kdenlive 24.12.9\n"]:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(
                    NativeKdenliveProcessRejected, "predates_track_order_fix"
                ):
                    parse_native_kdenlive_version(raw)

    def test_native_OTIO_export_version_must_be_identifiable(self):
        for raw in [b"", b"Qt 6.8.3", b"unknown-program 25.04.3",
                    b"kdenlive testing unstable", b"\xff\xfe"]:
            with self.subTest(raw=raw):
                with self.assertRaises(NativeKdenliveProcessRejected):
                    parse_native_kdenlive_version(raw)

    def test_a_different_Kdenlive_version_between_sessions_cannot_approve_reopen(self):
        first = self.stage_receipt(phase="import", pid=12, start=1234)
        later = self.stage_receipt(phase="reopen", pid=23, start=1300)
        later["original_Kdenlive_application_version"] = "26.4.0"
        with self.assertRaisesRegex(
            NativeKdenliveProcessRejected, "original_executable_or_source"
        ):
            compare_distinct_native_sessions(first, later)


    def test_edited_project_reopen_requires_explicit_reopen_to_reopen_phase_contract(self):
        edited = self.stage_receipt(phase="reopen", pid=23, start=1450)
        edited["saved_native_project_path"] = "/ci/edited-output.kdenlive"
        edited["saved_native_project_sha256"] = "12" * 32
        final = self.stage_receipt(phase="reopen", pid=34, start=1600)
        final.update(saved_native_project_path="/ci/edited-output.kdenlive",
                     saved_native_project_sha256="12" * 32,
                     reopen_input_project_path="/ci/edited-output.kdenlive",
                     reopen_input_project_sha256="12" * 32)
        final["process_arguments"]["native_project_path_checked"] = (
            "/ci/edited-output.kdenlive")
        with self.assertRaises(NativeKdenliveProcessRejected):
            compare_distinct_native_sessions(edited, final)
        result = compare_distinct_native_sessions(
            edited, final, expected_first_phase="reopen")
        self.assertEqual(result["reopen_input_project_path"],
                         edited["saved_native_project_path"])
        self.assertEqual(result["reopen_input_project_sha256"], "12" * 32)
        self.assertEqual(edited["phase"], "reopen")
        self.assertEqual(final["phase"], "reopen")
        self.assertFalse(result["human_acceptance"])

    def test_reopen_chain_rejects_unsupported_or_mismatched_requested_phases(self):
        edited = self.stage_receipt(phase="reopen", pid=23, start=1450)
        final = self.stage_receipt(phase="reopen", pid=34, start=1600)
        for expected in ("import", "edited", "", None):
            with self.subTest(expected=expected):
                with self.assertRaises(NativeKdenliveProcessRejected):
                    compare_distinct_native_sessions(
                        edited, final, expected_first_phase=expected)
        edited["phase"] = "import"
        with self.assertRaises(NativeKdenliveProcessRejected):
            compare_distinct_native_sessions(edited, final, expected_first_phase="reopen")

    def test_explicit_reopen_chain_preserves_path_hash_and_process_identity_checks(self):
        for field, invalid in (
            ("reopen_input_project_path", "/ci/unrelated.kdenlive"),
            ("reopen_input_project_sha256", "12" * 32),
            ("kernel_starttime_ticks", 1400),
            ("source_original_OTIO_sha256", "12" * 32),
            ("actual_executable_sha256", "12" * 32),
        ):
            with self.subTest(field=field):
                edited = self.stage_receipt(phase="reopen", pid=23, start=1450)
                final = self.stage_receipt(phase="reopen", pid=34, start=1600)
                final[field] = invalid
                with self.assertRaises(NativeKdenliveProcessRejected):
                    compare_distinct_native_sessions(
                        edited, final, expected_first_phase="reopen")
        edited = self.stage_receipt(phase="reopen", pid=23, start=1450)
        final = self.stage_receipt(phase="reopen", pid=23, start=1450)
        with self.assertRaises(NativeKdenliveProcessRejected):
            compare_distinct_native_sessions(edited, final, expected_first_phase="reopen")

    def test_physical_G2_never_runs_on_windows_or_without_approval(self):
        with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(
            os.environ, {"MOTIONWRIGHT_APPROVED_KDENLIVE_G2_SOURCE": "0"}
        ):
            with self.assertRaisesRegex(
                NativeKdenliveProcessRejected, "selected_Linux_GHA"
            ):
                inspect_live_native_kdenlive(
                    3, Path("/usr/bin/kdenlive"), "ab" * 32,
                    Path("/tmp/source.otio"), "cd" * 32,
                    Path("/tmp/source.kdenlive"), "ef" * 32,
                    phase="reopen",
                )


if __name__ == "__main__":
    unittest.main()
