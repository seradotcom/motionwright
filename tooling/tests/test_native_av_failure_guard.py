"""v0.1 synthetic *negative* tests for native renderer failure redaction.

These tests use private-looking fabricated text. They are not actual media
or a reproduced provider failure and cannot promote release acceptance.
"""
from __future__ import annotations

import json
import hashlib
import os
import errno
import io
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_av_failure_guard import (
    NativeCommandFailure, classify_native_command_failure,
)
import native_av_master_e2e


PRIVATE = (
    "job_ref=mc1:original-private-provider:render-123 "
    "ticket=super-secret-credential /home/owner/private/video.mp4"
)


def failure_bytes() -> bytes:
    return json.dumps({
        "native_render_e2e": "FAIL",
        "error": {
            "code": "Unavailable",
            "outcome_known": False,
            "message": "Native render failed. " + PRIVATE,
        },
        "receipts": [
            {
                "command": "driver.motion-canvas.render.start",
                "stage": "completed",
                "payload": {"result": {"data": {"job_ref": PRIVATE}}},
            },
            {
                "command": "driver.motion-canvas.render.status",
                "stage": "outcome_unknown",
                "payload": {"error": {
                    "code": "Timeout",
                    "message": "Command timed out: " + PRIVATE,
                }},
            },
            {
                "command": "driver.motion-canvas.render.status",
                "stage": "failed_known",
                "payload": {"error": {
                    "code": "Unavailable",
                    "message": "Provider capability generation is inactive. " + PRIVATE,
                }},
            },
        ],
        "opaque_provider_session": PRIVATE,
        "user_output_paths": ["/home/owner/private/video.mp4"],
    }).encode()


class NativeAVFailureRedactionTests(unittest.TestCase):
    def assert_closed(self, report: dict) -> None:
        exposed = json.dumps(report, sort_keys=True)
        for private in (PRIVATE, "super-secret", "owner/private",
                        "render-123", "original-private-provider"):
            self.assertNotIn(private, exposed)
        self.assertEqual(report["result"], "FAIL")
        self.assertIs(report["terminal_native_result_verified"], False)
        self.assertIs(report["decoded_media_verified"], False)
        self.assertIs(report["automatically_restarted_mutating_render"], False)
        self.assertIs(report["raw_cli_output_in_diagnostic"], False)
        self.assertIs(report["raw_argv_in_diagnostic"], False)
        self.assertIs(report["owner_connection_disclosed"], False)
        self.assertIs(report["native_job_references_disclosed"], False)
        self.assertIs(report["requires_original_job_owner_reconciliation"], True)

    def test_real_failure_shape_classified_but_private_journal_never_exposed(self) -> None:
        report = classify_native_command_failure(
            stdout=b"", stderr=failure_bytes(), exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["reason"], "provider_generation_inactive")
        self.assertEqual(report["source_outcome"], "UNKNOWN")
        self.assertEqual(report["reported_error_code"], "Unavailable")
        self.assertEqual(report["original_render_start_receipts_observed"], 1)
        self.assertIs(report["read_only_status_timeout_observed"], True)
        self.assertIs(report["read_only_status_unavailable_observed"], True)
        self.assertIs(report["provider_generation_inactive_observed"], True)

    def test_command_failure_exception_never_prints_private_output_or_argv(self) -> None:
        command = ["/home/owner/private/render", PRIVATE]
        with patch.object(
            native_av_master_e2e.subprocess, "run",
            return_value=SimpleNamespace(
                returncode=1, stdout=b"", stderr=failure_bytes(),
            ),
        ):
            with self.assertRaises(NativeCommandFailure) as caught:
                native_av_master_e2e.run_json(
                    command, env={"SUPER_SECRET": PRIVATE})
        self.assert_closed(caught.exception.diagnostic)
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.assertNotIn("owner/private", str(caught.exception))
        self.assertNotIn("render-123", str(caught.exception))
        self.assertEqual(caught.exception.diagnostic["source_outcome"], "UNKNOWN")

    def test_timeout_never_prints_argv_or_declares_mutating_retry(self) -> None:
        with patch.object(
            native_av_master_e2e.subprocess, "run",
            side_effect=subprocess.TimeoutExpired([PRIVATE], 3),
        ):
            with self.assertRaises(NativeCommandFailure) as caught:
                native_av_master_e2e.run_json([PRIVATE], env={})
        self.assert_closed(caught.exception.diagnostic)
        self.assertEqual(caught.exception.diagnostic["reason"], "command_deadline")
        self.assertEqual(caught.exception.diagnostic["source_outcome"], "UNKNOWN")
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_malformed_native_json_fails_closed(self) -> None:
        report = classify_native_command_failure(
            stdout=b"", stderr=b"{ private paths not JSON " + PRIVATE.encode(),
            exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["reason"], "malformed_native_failure_response")

    def test_oversized_native_failure_is_never_truncated_and_forwarded(self) -> None:
        report = classify_native_command_failure(
            stdout=b"", stderr=PRIVATE.encode() * 10000, exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["reason"], "oversized_native_failure_response")

    def test_unknown_error_code_not_promoted_or_reproduced(self) -> None:
        raw = json.dumps({
            "error": {
                "code": PRIVATE,
                "outcome_known": False,
                "message": PRIVATE,
            },
            "receipts": [],
        }).encode()
        report = classify_native_command_failure(
            stdout=b"", stderr=raw, exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["reported_error_code"], "OtherOrMissing")
        self.assertEqual(report["source_outcome"], "UNKNOWN")

    def test_status_failure_without_proof_stays_unverified(self) -> None:
        raw = json.dumps({
            "error": {"code": "Unavailable", "outcome_known": True,
                      "message": PRIVATE},
            "receipts": [{"command": "driver.motion-canvas.render.status",
                          "stage": "failed_known",
                          "payload": {"error": {"code": "Unavailable",
                                               "message": PRIVATE}}}],
        }).encode()
        report = classify_native_command_failure(
            stdout=b"", stderr=raw, exit_code=1)
        self.assert_closed(report)
        self.assertEqual(report["source_outcome"], "FAILED_NOT_MEDIA_VERIFIED")
        self.assertFalse(report["provider_generation_inactive_observed"])

    def test_successful_json_command_unchanged(self) -> None:
        with patch.object(native_av_master_e2e.subprocess, "run",
                          return_value=SimpleNamespace(
                              returncode=0, stdout=b'{"ok":true}', stderr=b"",
                          )):
            self.assertEqual(
                native_av_master_e2e.run_json(["safe-cli"], env={}),
                {"ok": True},
            )

    def test_bad_success_json_never_echoes_private_output(self) -> None:
        with patch.object(native_av_master_e2e.subprocess, "run",
                          return_value=SimpleNamespace(
                              returncode=0, stdout=PRIVATE.encode(),
                              stderr=b"",
                          )):
            with self.assertRaises(NativeCommandFailure) as caught:
                native_av_master_e2e.run_json([PRIVATE], env={})
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.assert_closed(caught.exception.diagnostic)




class NativeObservationFinalizationTests(unittest.TestCase):
    @contextmanager
    def broker_fixture(self):
        """Exercise the real wrapper cleanup with synthetic local stand-ins only."""
        with tempfile.TemporaryDirectory(prefix="native-av-observer-contract-") as raw:
            root = Path(raw)
            bins = root / "bins"
            bins.mkdir()
            for name in (
                "semwright", "semwrightd", "semwright-motion-canvas-driver",
                "semwright-mlt-video-driver", "semwright-mlt-runtime-runner",
                "node", "melt", "ffmpeg", "ffprobe",
            ):
                (bins / name).write_bytes(b"synthetic observer contract; not executable")
            evidence = root / "evidence"
            log = io.BytesIO()
            daemon = Mock()
            daemon.poll.return_value = None
            daemon.wait.return_value = 0
            daemon.pid = 12345
            sampler = Mock()
            sampler.stop.return_value = {}
            native = Mock(return_value={"width": 1920, "height": 1080, "resource": {}})
            original_open = Path.open

            def open_file(path, *args, **kwargs):
                if path == evidence / "semwrightd.log":
                    return log
                return original_open(path, *args, **kwargs)

            with ExitStack() as stack:
                module = native_av_master_e2e
                for name, value in {
                    "EVIDENCE": evidence, "SW_BINS": bins, "RUNTIME": root,
                    "FIXTURE": "baseline", "EXPECTED_SIZE": (1920, 1080),
                    "IS_HERO": False, "IS_STAGE": False, "IS_CAMERA": False,
                    "IS_MEASURED": False, "IS_PROCEDURAL": False,
                    "IS_COMPOSITION": False,
                }.items():
                    stack.enter_context(patch.object(module, name, value))
                stack.enter_context(patch.object(module, "exact_inputs"))
                stack.enter_context(patch.object(module, "semwright_version", return_value="0.0.0"))
                stack.enter_context(patch.object(module, "run_json", native))
                stack.enter_context(patch.object(module.subprocess, "Popen", return_value=daemon))
                stack.enter_context(patch.object(module, "ProviderLifecycleSampler", return_value=sampler))
                stack.enter_context(patch.object(module.shutil, "which", side_effect=lambda name: str(bins / name)))
                stack.enter_context(patch.object(Path, "open", autospec=True, side_effect=open_file))
                stack.enter_context(patch.dict(module.os.environ, {"MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY": "0"}))
                yield SimpleNamespace(
                    root=root, evidence=evidence, daemon=daemon, sampler=sampler,
                    log=log, native=native, stack=stack,
                )
            log.close()

    def test_primary_unknown_survives_lifecycle_stop_failure(self):
        primary = NativeCommandFailure({
            "reason": "SYNTHETIC_TIMEOUT", "source_outcome": "UNKNOWN",
        })
        with self.broker_fixture() as fixture:
            fixture.sampler.stop.side_effect = RuntimeError("synthetic observer failure")
            fixture.stack.enter_context(
                patch.object(native_av_master_e2e, "wait_for_socket", side_effect=primary)
            )
            with self.assertRaises(BaseException) as caught:
                native_av_master_e2e.main()
            self.assertIs(caught.exception, primary)
            self.assertEqual(caught.exception.diagnostic["source_outcome"], "UNKNOWN")
            fixture.sampler.stop.assert_called_once_with()
            fixture.daemon.terminate.assert_called_once_with()
            fixture.daemon.wait.assert_called_once_with(timeout=10)
            self.assertTrue(fixture.log.closed)
            self.assertEqual(fixture.native.call_count, 1)
            self.assertEqual(fixture.native.call_args.args[0][1], "seed")

    def test_observer_start_failure_cleans_broker_once(self):
        primary = RuntimeError("synthetic observer start failure")
        with self.broker_fixture() as fixture:
            fixture.sampler.start.side_effect = primary
            with self.assertRaises(BaseException) as caught:
                native_av_master_e2e.main()
            self.assertIs(caught.exception, primary)
            fixture.daemon.terminate.assert_called_once_with()
            fixture.daemon.wait.assert_called_once_with(timeout=10)
            self.assertTrue(fixture.log.closed)
            self.assertEqual(fixture.native.call_count, 1)
            self.assertEqual(fixture.native.call_args.args[0][1], "seed")




    def test_primary_unknown_survives_broker_cleanup_failure(self):
        primary = NativeCommandFailure({"reason": "SYNTHETIC_TIMEOUT", "source_outcome": "UNKNOWN"})
        with self.broker_fixture() as fixture:
            fixture.daemon.terminate.side_effect = OSError("synthetic cleanup failure")
            fixture.stack.enter_context(patch.object(native_av_master_e2e, "wait_for_socket", side_effect=primary))
            with self.assertRaises(BaseException) as caught:
                native_av_master_e2e.main()
            self.assertIs(caught.exception, primary)
            fixture.sampler.stop.assert_called_once_with()
            fixture.daemon.terminate.assert_called_once_with()
            self.assertTrue(fixture.log.closed)

    def test_each_finalizer_and_cleanup_is_attempted_once_after_observer_failure(self):
        order = []

        def fail():
            order.append("first")
            raise RuntimeError(PRIVATE)

        with patch("builtins.print") as output:
            with self.assertRaises(native_av_master_e2e.NativeObservationFailure) as caught:
                native_av_master_e2e.finalize_native_observation(
                    primary_error=None,
                    finalizers=[("provider_lifecycle", fail), ("provider_correlation", lambda: order.append("second"))],
                    cleanup=lambda: order.append("cleanup"),
                )
        self.assertEqual(order, ["first", "second", "cleanup"])
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.assertNotIn(PRIVATE, repr(output.call_args_list))

    def test_cleanup_failure_without_primary_cannot_report_success(self):
        def fail():
            raise OSError(PRIVATE)

        with self.assertRaises(native_av_master_e2e.NativeObservationFailure):
            native_av_master_e2e.finalize_native_observation(
                primary_error=None, finalizers=[], cleanup=fail,
            )

    def test_successful_finalization_preserves_order_without_native_commands(self):
        order = []
        with patch.object(native_av_master_e2e, "run_json") as native:
            failures = native_av_master_e2e.finalize_native_observation(
                primary_error=None,
                finalizers=[
                    ("provider_lifecycle", lambda: order.append("lifecycle")),
                    ("provider_correlation", lambda: order.append("correlation")),
                ],
                cleanup=lambda: order.append("cleanup"),
            )
        self.assertEqual(order, ["lifecycle", "correlation", "cleanup"])
        self.assertEqual(failures, [])
        native.assert_not_called()

    def test_failure_diagnostic_writes_cannot_replace_primary_unknown(self):
        primary = NativeCommandFailure({"reason": "SYNTHETIC_TIMEOUT", "source_outcome": "UNKNOWN"})
        with patch.object(native_av_master_e2e, "write_private_json", side_effect=OSError(PRIVATE)):
            with patch.object(native_av_master_e2e, "preserve_native_failure_evidence", side_effect=RuntimeError(PRIVATE)):
                with patch.object(native_av_master_e2e, "run_json") as native:
                    with patch("builtins.print") as output:
                        try:
                            raise primary
                        except NativeCommandFailure as error:
                            native_av_master_e2e.retain_render_failure(error, Path("synthetic-output"))
                            observed = error
        self.assertIs(observed, primary)
        self.assertEqual(observed.diagnostic["source_outcome"], "UNKNOWN")
        native.assert_not_called()
        self.assertNotIn(PRIVATE, repr(output.call_args_list))

    def test_timeout_custody_does_not_issue_another_render(self):
        primary = subprocess.TimeoutExpired(["synthetic-command"], 1)
        with patch.object(native_av_master_e2e, "preserve_native_failure_evidence") as capture:
            with patch.object(native_av_master_e2e, "run_json") as native:
                native_av_master_e2e.retain_render_failure(primary, Path("synthetic-output"))
        capture.assert_called_once_with(Path("synthetic-output"), native_outcome="UNKNOWN")
        native.assert_not_called()


    def test_workspace_cleanup_failure_preserves_primary_unknown(self):
        primary = NativeCommandFailure({"reason": "SYNTHETIC_TIMEOUT", "source_outcome": "UNKNOWN"})
        temporary = Mock()
        temporary.name = "synthetic-workspace"
        temporary.cleanup.side_effect = OSError(PRIVATE)
        with patch.object(native_av_master_e2e.tempfile, "TemporaryDirectory", return_value=temporary):
            with self.assertRaises(BaseException) as caught:
                with native_av_master_e2e._native_workspace():
                    raise primary
        self.assertIs(caught.exception, primary)
        temporary.cleanup.assert_called_once_with()

    def test_workspace_cleanup_failure_without_primary_is_visible(self):
        temporary = Mock()
        temporary.name = "synthetic-workspace"
        temporary.cleanup.side_effect = OSError(PRIVATE)
        with patch.object(native_av_master_e2e.tempfile, "TemporaryDirectory", return_value=temporary):
            with self.assertRaises(native_av_master_e2e.NativeObservationFailure):
                with native_av_master_e2e._native_workspace():
                    pass
        temporary.cleanup.assert_called_once_with()

    def test_broken_secondary_log_sink_preserves_original_error(self):
        primary = NativeCommandFailure({"reason": "SYNTHETIC_TIMEOUT", "source_outcome": "UNKNOWN"})

        def fail():
            raise RuntimeError(PRIVATE)

        cleanup = Mock()
        with patch("builtins.print", side_effect=BrokenPipeError):
            result = native_av_master_e2e.finalize_native_observation(
                primary_error=primary, finalizers=[("provider_lifecycle", fail)], cleanup=cleanup,
            )
        self.assertEqual(result[0]["status"], "NOT_CAPTURED")
        self.assertEqual(primary.diagnostic["source_outcome"], "UNKNOWN")
        cleanup.assert_called_once_with()

    def test_broken_custody_log_sink_cannot_replace_native_error(self):
        primary = NativeCommandFailure({"reason": "SYNTHETIC_TIMEOUT", "source_outcome": "UNKNOWN"})
        with patch.object(native_av_master_e2e, "write_private_json", side_effect=OSError(PRIVATE)):
            with patch.object(native_av_master_e2e, "preserve_native_failure_evidence", side_effect=OSError(PRIVATE)):
                with patch("builtins.print", side_effect=BrokenPipeError):
                    native_av_master_e2e.retain_render_failure(primary, Path("synthetic-output"))
        self.assertEqual(primary.diagnostic["source_outcome"], "UNKNOWN")

    def test_broker_creation_failure_closes_log_without_starting_observers(self):
        primary = OSError("synthetic Broker creation failure")
        with self.broker_fixture() as fixture:
            fixture.stack.enter_context(patch.object(native_av_master_e2e.subprocess, "Popen", side_effect=primary))
            with self.assertRaises(BaseException) as caught:
                native_av_master_e2e.main()
            self.assertIs(caught.exception, primary)
            fixture.sampler.start.assert_not_called()
            fixture.daemon.terminate.assert_not_called()
            self.assertTrue(fixture.log.closed)


class NativeFailureFileCustodyTests(unittest.TestCase):
    @contextmanager
    def file_fixture(self):
        with tempfile.TemporaryDirectory(prefix="native-av-file-custody-") as raw:
            root = Path(raw)
            source_root = root / "output"
            source_root.mkdir()
            evidence = root / "evidence"
            evidence.mkdir()
            with patch.object(native_av_master_e2e, "EVIDENCE", evidence):
                yield SimpleNamespace(root=root, output=source_root, evidence=evidence)

    def copy(self, fixture, source, *, limit=1024):
        return native_av_master_e2e.copy_native_failure_file(
            fixture.output, source, fixture.evidence / "copy.bin", byte_limit=limit,
        )

    def test_bounded_copy_is_byte_exact_and_unverified(self):
        with self.file_fixture() as fixture:
            source = fixture.output / "native-observations.ndjson"
            data = b'{"synthetic":true,"outcome":"UNKNOWN"}\n'
            source.write_bytes(data)
            result = self.copy(fixture, source)
            self.assertEqual(result["capture_status"], "CAPTURED_UNVERIFIED")
            self.assertEqual(result["source_identity"], result["source_identity_after"])
            self.assertEqual(result["source_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(result["copy_sha256"], result["source_sha256"])
            self.assertEqual((fixture.evidence / "copy.bin").read_bytes(), data)

    def test_missing_empty_and_oversized_files_have_explicit_omissions(self):
        with self.file_fixture() as fixture:
            source = fixture.output / "file"
            self.assertEqual(self.copy(fixture, source)["omission_reason"], "MISSING")
            source.write_bytes(b"")
            self.assertEqual(self.copy(fixture, source)["omission_reason"], "EMPTY_OR_SIZE_LIMIT")
            source.write_bytes(b"12345")
            self.assertEqual(self.copy(fixture, source, limit=4)["omission_reason"], "EMPTY_OR_SIZE_LIMIT")
            self.assertFalse((fixture.evidence / "copy.bin").exists())

    def test_outside_and_parent_traversal_are_rejected_before_read(self):
        with self.file_fixture() as fixture:
            outside = fixture.root / "private.bin"
            outside.write_bytes(b"synthetic private bytes")
            for source in (outside, fixture.output / ".." / "private.bin"):
                with self.subTest(path=source.name):
                    result = self.copy(fixture, source)
                    self.assertEqual(result["capture_status"], "NOT_CAPTURED")
                    self.assertEqual(result["omission_reason"], "OUTSIDE_ROOT")
            self.assertFalse((fixture.evidence / "copy.bin").exists())

    def test_existing_destination_is_never_overwritten(self):
        with self.file_fixture() as fixture:
            source = fixture.output / "file"
            source.write_bytes(b"new")
            destination = fixture.evidence / "copy.bin"
            destination.write_bytes(b"original")
            result = self.copy(fixture, source)
            self.assertEqual(result["omission_reason"], "DESTINATION_EXISTS")
            self.assertEqual(destination.read_bytes(), b"original")

    def test_real_hardlink_is_not_admitted_as_owned_regular_source(self):
        with self.file_fixture() as fixture:
            original = fixture.root / "original"
            original.write_bytes(b"synthetic linked bytes")
            source = fixture.output / "linked"
            try:
                os.link(original, source)
            except OSError as error:
                if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP) or getattr(error, "winerror", None) == 1:
                    self.skipTest("filesystem does not support hardlinks")
                raise
            result = self.copy(fixture, source)
            self.assertEqual(result["omission_reason"], "SYMLINK_REPARSE_OR_HARDLINK")
            self.assertFalse((fixture.evidence / "copy.bin").exists())

    def test_real_symlink_is_rejected_where_available(self):
        with self.file_fixture() as fixture:
            outside = fixture.root / "outside"
            outside.write_bytes(b"synthetic external bytes")
            source = fixture.output / "linked"
            try:
                source.symlink_to(outside)
            except OSError as error:
                if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP) or getattr(error, "winerror", None) == 1314:
                    self.skipTest("filesystem or Windows privilege does not permit a symlink fixture")
                raise
            result = self.copy(fixture, source)
            self.assertEqual(result["omission_reason"], "SYMLINK_REPARSE_OR_HARDLINK")
            self.assertFalse((fixture.evidence / "copy.bin").exists())

    def test_replacement_between_path_check_and_open_is_rejected(self):
        with self.file_fixture() as fixture:
            source = fixture.output / "file"
            source.write_bytes(b"original")
            original_open = native_av_master_e2e.os.open
            replaced = False

            def replace_then_open(path, flags, *args, **kwargs):
                nonlocal replaced
                if Path(path) == source and not replaced:
                    replaced = True
                    source.rename(fixture.output / "original-preserved")
                    source.write_bytes(b"replaced")
                return original_open(path, flags, *args, **kwargs)

            with patch.object(native_av_master_e2e.os, "open", side_effect=replace_then_open):
                result = self.copy(fixture, source)
            self.assertTrue(replaced)
            self.assertEqual(result["omission_reason"], "SOURCE_CHANGED")
            self.assertEqual((fixture.output / "original-preserved").read_bytes(), b"original")
            self.assertFalse((fixture.evidence / "copy.bin").exists())

    def test_source_mutation_during_copy_invalidates_and_removes_only_created_copy(self):
        with self.file_fixture() as fixture:
            source = fixture.output / "file"
            source.write_bytes(b"original")
            original_sync = native_av_master_e2e.os.fsync

            def mutate_after_write(fd):
                source.write_bytes(b"changed original content and length")
                return original_sync(fd)

            with patch.object(native_av_master_e2e.os, "fsync", side_effect=mutate_after_write):
                result = self.copy(fixture, source)
            self.assertEqual(result["omission_reason"], "SOURCE_CHANGED")
            self.assertFalse((fixture.evidence / "copy.bin").exists())

    def test_baseline_custody_retains_available_originals_without_media_claim(self):
        with self.file_fixture() as fixture:
            job = fixture.output / "job"
            job.mkdir()
            for name in ("native-observations.ndjson", "native-observations-receipt.json", "artifact-manifest.json"):
                (job / name).write_bytes(b'{"synthetic":true}\n')
            frames = job / "frames"
            frames.mkdir()
            (frames / "000001.png").write_bytes(b"synthetic bytes; no PNG decoding")
            with patch.object(native_av_master_e2e, "FIXTURE", "baseline"):
                receipt = native_av_master_e2e.preserve_native_failure_evidence(fixture.output, native_outcome="UNKNOWN")
            self.assertEqual(receipt["native_outcome"], "UNKNOWN")
            self.assertEqual(receipt["retention_scope"], "RUNNER_LOCAL_ONLY")
            self.assertEqual(receipt["driver_host_exit_evidence"], "NOT_OBSERVED")
            self.assertFalse(receipt["media_decoded"])
            self.assertFalse(receipt["terminal_native_result_verified"])
            self.assertFalse(receipt["original_render_retry_permitted"])
            self.assertEqual(len(receipt["files"]), 4)
            self.assertTrue(all(item["capture_status"] == "CAPTURED_UNVERIFIED" for item in receipt["files"]))
            self.assertEqual((fixture.evidence / "UNVERIFIED-native-frame-000000.png").read_bytes(), b"synthetic bytes; no PNG decoding")
            stored = json.loads((fixture.evidence / "native-failure-files.json").read_text())
            self.assertEqual(stored, receipt)

    def test_empty_native_output_is_not_promoted_to_captured_evidence(self):
        with self.file_fixture() as fixture:
            receipt = native_av_master_e2e.preserve_native_failure_evidence(fixture.output, native_outcome="UNKNOWN")
            self.assertEqual(receipt["capture_status"], "NOT_CAPTURED")
            self.assertEqual(len(receipt["files"]), 4)
            self.assertTrue(all(item["omission_reason"] == "NOT_FOUND" for item in receipt["files"]))

    def test_depth_limit_is_explicit_and_does_not_capture_deeper_files(self):
        with self.file_fixture() as fixture:
            deep = fixture.output
            for index in range(8):
                deep = deep / str(index)
                deep.mkdir()
            (deep / "native-observations.ndjson").write_bytes(b"synthetic deeper data")
            receipt = native_av_master_e2e.preserve_native_failure_evidence(fixture.output, native_outcome="UNKNOWN")
            self.assertIn("DIRECTORY_DEPTH_LIMIT", receipt["discovery_omissions"])
            self.assertEqual(receipt["capture_status"], "NOT_CAPTURED")




    def test_corrupted_destination_bytes_are_rejected(self):
        with self.file_fixture() as fixture:
            source = fixture.output / "file"
            source.write_bytes(b"original source bytes")
            original_sync = native_av_master_e2e.os.fsync

            def corrupt_destination(fd):
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, b"corrupted")
                return original_sync(fd)

            with patch.object(native_av_master_e2e.os, "fsync", side_effect=corrupt_destination):
                result = self.copy(fixture, source)
            self.assertEqual(result["omission_reason"], "DESTINATION_CHANGED")
            self.assertFalse((fixture.evidence / "copy.bin").exists())
            self.assertEqual(source.read_bytes(), b"original source bytes")

    def test_raced_destination_replacement_is_not_deleted(self):
        with self.file_fixture() as fixture:
            source = fixture.output / "file"
            source.write_bytes(b"original source bytes")
            destination = fixture.evidence / "copy.bin"
            identity = native_av_master_e2e._contained_file_identity
            replaced = False

            def replace_destination(root, path):
                nonlocal replaced
                if path == destination and not replaced:
                    replaced = True
                    destination.rename(fixture.evidence / "original-copy-preserved")
                    destination.write_bytes(b"external replacement")
                return identity(root, path)

            with patch.object(native_av_master_e2e, "_contained_file_identity", side_effect=replace_destination):
                result = self.copy(fixture, source)
            self.assertTrue(replaced)
            self.assertEqual(result["omission_reason"], "DESTINATION_CHANGED")
            self.assertEqual(destination.read_bytes(), b"external replacement")
            self.assertEqual((fixture.evidence / "original-copy-preserved").read_bytes(), b"original source bytes")


if __name__ == "__main__":
    unittest.main()
