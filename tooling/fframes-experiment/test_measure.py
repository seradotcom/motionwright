#!/usr/bin/env python3
"""Protocol tests; synthetic captures and inert children are not renderer G2."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import wave
import zlib

import measure as m


def png_bytes(width=2, height=1, *, level=6):
    rgba = bytes((10, 20, 30, 255, 40, 50, 60, 255)) * (width * height // 2)
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))
    pixels = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(pixels, level)) + chunk(b"IEND", b""))
    return raw, rgba


def capture_completed(capture, stdout=b"", stderr=b"", *, input_bytes=None, exit_code=0):
    capture.update({"state": "TERMINAL_OBSERVED", "stdout": stdout, "stderr": stderr,
                    "stdin_bytes": 0 if input_bytes is None else len(input_bytes),
                    "stdin_sha256": None if input_bytes is None else m.sha(input_bytes),
                    "terminal": {"exit_code": exit_code, "elapsed_seconds": 0.01,
                                 "stdout_bytes": len(stdout), "stderr_bytes": len(stderr),
                                 "stderr_sha256": m.sha(stderr), "whole_process_completed": True,
                                 "timed_out": False, "output_budget_exceeded": False,
                                 "pipes_closed": True, "stdin_complete": True, "pipe_errors": []}})


def framehash_bytes(*, frames=30, width=m.WIDTH, height=m.HEIGHT):
    head = ("#format: frame checksums\n#version: 2\n#hash: SHA256\n#software: fixture\n"
            f"#tb 0: 1/30\n#media_type 0: video\n#codec_id 0: rawvideo\n"
            f"#dimensions 0: {width}x{height}\n#sar 0: 1/1\n")
    return (head + "".join(f"0, {i}, {i}, 1, {width * height * 4}, "
                           + hashlib.sha256(str(i).encode()).hexdigest() + "\n"
                           for i in range(frames))).encode()


def synthetic_pcm():
    bed_values = [(1000 if i % 2 == 0 else -2000) / 32768 for i in range(m.AUDIO_FRAMES * 2)]
    cue_values = [(3000 if i % 2 == 0 else 4000) / 32768 for i in range(m.AUDIO_RATE * 2)]
    expected = [v * 0.5 for v in bed_values]
    for i, value in enumerate(cue_values):
        expected[48000 + i] += value * 0.25
    def pack(values):
        return struct.pack("<" + str(len(values)) + "f", *values)
    return pack(bed_values), pack(cue_values), pack(expected)


class FframesProtocol(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mw-fframes-protocol-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def test_public_git_sha_and_zero_automatic_release_admission(self):
        self.assertEqual(len(m.PIN), 40)
        self.assertTrue(all(char in "0123456789abcdef" for char in m.PIN))
        self.assertEqual(m.REPO, "https://github.com/dmtrKovalenko/fframes.git")
        self.assertEqual(m.LICENSE, "MIT")
        self.assertNotEqual(m.OUT, m.CHECKOUT)

    def test_actual_binary_png_validation(self):
        path = self.root / "test.png"
        raw, _ = png_bytes()
        path.write_bytes(raw)
        inspected = m.png_metadata(path)
        self.assertEqual((inspected["width"], inspected["height"]), (2, 1))
        self.assertEqual(inspected["sha256"], m.sha(raw))
        self.assertTrue(inspected["transport_crc_checked"])
        self.assertNotIn("decoded_by_ffprobe", inspected)

    def test_unbounded_source_preview_is_rejected(self):
        path = self.root / "invalid.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        with self.assertRaises(AssertionError):
            m.png_metadata(path)

    def test_png_crc_truncation_and_trailing_bytes_are_rejected(self):
        raw, _ = png_bytes()
        path = self.root / "invalid.png"
        corrupt = bytearray(raw)
        corrupt[-5] ^= 1
        for variant in (raw[:24], raw[:-1], bytes(corrupt), raw + b"trailing"):
            with self.subTest(variant=m.sha(variant)):
                path.write_bytes(variant)
                with self.assertRaises((AssertionError, ValueError, RuntimeError)):
                    m.png_metadata(path)

    def test_run_retains_binary_originals_and_argv_before_return(self):
        metrics = {}
        stdout, stderr = b"\x00original\xff", b"private stderr\x00"
        argv = ["inert-fixture", "argument with spaces"]
        def fake(args, cap, timeout, *, input_bytes, capture):
            self.assertEqual(args, argv)
            request = json.loads((self.root / "processes/one/argv.json").read_bytes())
            self.assertEqual(request["argv"], argv)
            self.assertEqual((self.root / "processes/one/stdin.bin").read_bytes(), b"input")
            capture_completed(capture, stdout, stderr, input_bytes=input_bytes)
            return stdout, {}
        with mock.patch.object(m, "run_bounded", side_effect=fake) as called:
            completed = m.run(argv, 1, "one", metrics, evidence_root=self.root, input_bytes=b"input")
        called.assert_called_once()
        self.assertEqual(completed.stdout, stdout)
        self.assertEqual((self.root / "processes/one/stdout.bin").read_bytes(), stdout)
        self.assertEqual((self.root / "processes/one/stderr.bin").read_bytes(), stderr)
        self.assertTrue(metrics["one"]["originals_retained"])

    def test_duplicate_process_label_is_rejected_before_second_dispatch(self):
        def fake(args, cap, timeout, *, input_bytes, capture):
            capture_completed(capture)
            return b"", {}
        metrics = {}
        with mock.patch.object(m, "run_bounded", side_effect=fake) as called:
            m.run(["inert-fixture"], 1, "once", metrics, evidence_root=self.root)
            with self.assertRaisesRegex(AssertionError, "already_used"):
                m.run(["inert-fixture"], 1, "once", metrics, evidence_root=self.root)
        called.assert_called_once()

    def test_primary_process_error_survives_failed_custody_without_second_dispatch(self):
        original = OSError("original supervisor error")
        def fake(args, cap, timeout, *, input_bytes, capture):
            capture_completed(capture, b"original prefix", b"original error", exit_code=-9)
            capture["terminal"]["timed_out"] = True
            raise original
        writer = m.new_bytes
        def fail_capture(path, raw):
            if path.name == "capture.json":
                raise OSError("secondary custody error")
            return writer(path, raw)
        with mock.patch.object(m, "run_bounded", side_effect=fake) as called:
            with mock.patch.object(m, "new_bytes", side_effect=fail_capture):
                with self.assertRaises(OSError) as caught:
                    m.run(["inert-fixture"], 1, "failed", {}, evidence_root=self.root)
        self.assertIs(caught.exception, original)
        called.assert_called_once()
        self.assertEqual((self.root / "processes/failed/stdout.bin").read_bytes(), b"original prefix")

    def test_successful_process_with_failed_custody_is_not_success(self):
        failure = OSError("capture storage failure")
        def fake(args, cap, timeout, *, input_bytes, capture):
            capture_completed(capture, b"success")
            return b"success", {}
        writer = m.new_bytes
        def fail_capture(path, raw):
            if path.name == "capture.json":
                raise failure
            return writer(path, raw)
        metrics = {}
        with mock.patch.object(m, "run_bounded", side_effect=fake) as called:
            with mock.patch.object(m, "new_bytes", side_effect=fail_capture):
                with self.assertRaises(OSError) as caught:
                    m.run(["inert-fixture"], 1, "storage", metrics, evidence_root=self.root)
        called.assert_called_once()
        self.assertIs(caught.exception, failure)
        self.assertFalse(metrics["storage"]["originals_retained"])

    def test_reserved_capture_does_not_invent_terminal_or_eof(self):
        original = OSError("spawn failed")
        def fake(args, cap, timeout, *, input_bytes, capture):
            capture.update({"state": "RESERVED"})
            raise original
        with mock.patch.object(m, "run_bounded", side_effect=fake) as called:
            with self.assertRaises(OSError) as caught:
                m.run(["inert-fixture"], 1, "reserved", {}, evidence_root=self.root)
        self.assertIs(caught.exception, original)
        called.assert_called_once()
        retained = json.loads((self.root / "processes/reserved/capture.json").read_bytes())
        self.assertEqual(retained["state"], "RESERVED")
        self.assertNotIn("terminal", retained)
        self.assertFalse((self.root / "processes/reserved/stdout.bin").exists())

    def test_incomplete_capture_is_retained_but_cannot_be_promoted(self):
        def fake(args, cap, timeout, *, input_bytes, capture):
            capture_completed(capture, b"valid-prefix")
            capture["terminal"]["pipes_closed"] = False
            capture["terminal"]["whole_process_completed"] = False
            return b"valid-prefix", {}
        with mock.patch.object(m, "run_bounded", side_effect=fake) as called:
            with self.assertRaisesRegex(AssertionError, "capture_incomplete"):
                m.run(["inert-fixture"], 1, "partial", {}, evidence_root=self.root)
        called.assert_called_once()
        self.assertEqual((self.root / "processes/partial/stdout.bin").read_bytes(), b"valid-prefix")

    def test_real_inert_child_streams_and_nonzero_terminal_are_retained(self):
        argv = [sys.executable, "-c",
                "import os;os.write(1,b'original\\x00stdout');os.write(2,b'original stderr');raise SystemExit(7)"]
        with self.assertRaises(ValueError):
            m.run(argv, 15, "inert_nonzero", {}, evidence_root=self.root)
        folder = self.root / "processes/inert_nonzero"
        self.assertEqual((folder / "stdout.bin").read_bytes(), b"original\x00stdout")
        self.assertEqual((folder / "stderr.bin").read_bytes(), b"original stderr")
        capture = json.loads((folder / "capture.json").read_bytes())
        self.assertEqual(capture["terminal"]["exit_code"], 7)
        self.assertTrue(capture["terminal"]["whole_process_completed"])
        self.assertIn("raised_error", capture)

    def test_different_png_encodings_compare_actual_rgba(self):
        first, rgba = png_bytes(level=0)
        second, _ = png_bytes(level=9)
        self.assertNotEqual(first, second)
        def fake(args, cap, timeout, *, input_bytes, capture):
            capture_completed(capture, rgba, input_bytes=input_bytes)
            return rgba, {}
        paths = [self.root / "a.png", self.root / "b.png"]
        for path, raw in zip(paths, (first, second)):
            path.write_bytes(raw)
        with mock.patch.object(m, "run_bounded", side_effect=fake) as called:
            a = m.decode_png(paths[0], Path("/inert/ffmpeg"), "decode_a", {}, evidence_root=self.root)
            b = m.decode_png(paths[1], Path("/inert/ffmpeg"), "decode_b", {}, evidence_root=self.root)
        self.assertEqual(called.call_count, 2)
        self.assertNotEqual(a["sha256"], b["sha256"])
        self.assertEqual(a["rgba_sha256"], b["rgba_sha256"])
        self.assertEqual(a["rgba_sha256"], m.sha(rgba))

    def test_png_decode_rejects_short_rgba_even_with_complete_process(self):
        path = self.root / "a.png"
        raw, rgba = png_bytes()
        path.write_bytes(raw)
        def fake(args, cap, timeout, *, input_bytes, capture):
            capture_completed(capture, rgba[:-1], input_bytes=input_bytes)
            return rgba[:-1], {}
        with mock.patch.object(m, "run_bounded", side_effect=fake):
            with self.assertRaisesRegex(AssertionError, "single_complete_rgba"):
                m.decode_png(path, Path("/inert/ffmpeg"), "short", {}, evidence_root=self.root)

    def test_framehash_requires_all_decoded_frames_and_original_times(self):
        result = m.parse_framehash(framehash_bytes(), m.WIDTH, m.HEIGHT, 30)
        self.assertEqual(result["frame_count"], 30)
        self.assertEqual(result["frames"][-1]["pts"], 29)
        self.assertEqual(result["pixel_format"], "rgba")

    def test_framehash_rejects_missing_extra_reordered_or_wrong_size_frames(self):
        full = framehash_bytes()
        variants = [framehash_bytes(frames=29), framehash_bytes(frames=31),
                    full.replace(b"0, 1, 1, 1,", b"0, 2, 2, 1,"),
                    full.replace(str(m.WIDTH * m.HEIGHT * 4).encode(), b"1", 1)]
        for raw in variants:
            with self.subTest(raw=m.sha(raw)):
                with self.assertRaises(AssertionError):
                    m.parse_framehash(raw, m.WIDTH, m.HEIGHT, 30)

    def test_framehash_metadata_alone_and_changed_clock_are_not_decode(self):
        full = framehash_bytes()
        for raw in (full[:full.index(b"0, 0,")], full.replace(b"#tb 0: 1/30", b"#tb 0: 1/60"),
                    b'{"streams":[{"codec_name":"h264","nb_frames":"30"}]}'):
            with self.assertRaises((AssertionError, ValueError)):
                m.parse_framehash(raw, m.WIDTH, m.HEIGHT, 30)

    def test_synthetic_audio_originals_are_explicit_stereo_wav_inputs(self):
        specification = m.create_audio_fixture(self.root / "inputs")
        self.assertEqual(sorted(p.name for p in (self.root / "inputs").iterdir()), ["bed.wav", "cue.wav"])
        self.assertFalse(specification["audio_acceptance"])
        self.assertTrue((self.root / "fixture.json").is_file())
        for name, frames in (("bed.wav", 96000), ("cue.wav", 48000)):
            raw = (self.root / "inputs" / name).read_bytes()
            self.assertEqual(specification["originals"][name]["sha256"], m.sha(raw))
            with wave.open(io.BytesIO(raw), "rb") as stream:
                self.assertEqual((stream.getnchannels(), stream.getframerate(), stream.getnframes()),
                                 (2, 48000, frames))

    def test_audio_comparison_observes_only_the_synthetic_mix_scope(self):
        bed, cue, expected = synthetic_pcm()
        result = m.compare_audio_pcm(bed, cue, expected)
        self.assertEqual(result["sample_frames"], 96000)
        self.assertEqual(result["max_absolute_error"], 0.0)
        self.assertFalse(result["human_audio_acceptance"])

    def test_audio_comparison_rejects_missing_gain_delay_stereo_and_truncation(self):
        bed, cue, expected = synthetic_pcm()
        swapped = b"".join(expected[i + 4:i + 8] + expected[i:i + 4]
                            for i in range(0, len(expected), 8))
        shifted = b"\0" * 8 + expected[:-8]
        for variant in (bed, shifted, swapped, expected[:-8], b"\0" * len(expected)):
            with self.subTest(raw=m.sha(variant)):
                with self.assertRaises(AssertionError):
                    m.compare_audio_pcm(bed, cue, variant)

    def test_audio_comparison_rejects_nonfinite_pcm(self):
        bed, cue, expected = synthetic_pcm()
        with self.assertRaisesRegex(AssertionError, "audio_finite"):
            m.compare_audio_pcm(bed, cue, struct.pack("<f", float("nan")) + expected[4:])

    def _media_original_readers(self, path):
        return (
            ("png_metadata", lambda: m.png_metadata(path)),
            ("png_decode", lambda: m.decode_png(path, Path("/inert/ffmpeg"),
                                                "alias_png", {}, evidence_root=self.root)),
            ("video_decode", lambda: m.decode_video(path, Path("/inert/ffmpeg"),
                                                    Path("/inert/ffprobe"), {}, evidence_root=self.root)),
            ("pcm_decode", lambda: m.decode_pcm(path, Path("/inert/ffmpeg"),
                                                "alias_pcm", m.AUDIO_FRAMES, {}, evidence_root=self.root)),
        )

    def test_media_alias_is_rejected_before_decoder_with_portable_path_mock(self):
        # Portable contract test only: Path.resolve simulates the alias. This
        # does not claim a Windows symlink or native Linux decoder was exercised.
        target, alias = self.root / "target.bin", self.root / "alias.bin"
        target.write_bytes(b"regular original")
        actual_resolve = Path.resolve
        def resolved(path, *args, **kwargs):
            return target if path == alias else actual_resolve(path, *args, **kwargs)
        with mock.patch.object(Path, "resolve", autospec=True, side_effect=resolved):
            with mock.patch.object(m.sys, "platform", "linux"):
                for name, invoke in self._media_original_readers(alias):
                    with self.subTest(caller=name):
                        with (mock.patch.object(m, "run") as decoder,
                              mock.patch.object(m, "run_bounded") as supervisor,
                              mock.patch.object(m.subprocess, "Popen") as process):
                            with self.assertRaisesRegex(AssertionError, "original_path"):
                                invoke()
                        decoder.assert_not_called()
                        supervisor.assert_not_called()
                        process.assert_not_called()
        self.assertFalse((self.root / "processes").exists())

    @unittest.skipUnless(sys.platform == "linux", "real Linux media symlink refusal")
    def test_real_linux_media_alias_is_rejected_before_decoder(self):
        target, alias = self.root / "target.bin", self.root / "alias.bin"
        target.write_bytes(b"regular original")
        alias.symlink_to(target)
        self.assertTrue(alias.is_symlink())
        for name, invoke in self._media_original_readers(alias):
            with self.subTest(caller=name):
                with (mock.patch.object(m, "run") as decoder,
                      mock.patch.object(m, "run_bounded") as supervisor,
                      mock.patch.object(m.subprocess, "Popen") as process):
                    with self.assertRaisesRegex(AssertionError, "original_path"):
                        invoke()
                decoder.assert_not_called()
                supervisor.assert_not_called()
                process.assert_not_called()
        self.assertFalse((self.root / "processes").exists())

    @unittest.skipUnless(sys.platform == "linux", "retained Linux descriptor profile")
    def test_original_replacement_is_detected_after_retained_descriptor_use(self):
        original, replacement = self.root / "native.bin", self.root / "replacement.bin"
        original.write_bytes(b"original")
        replacement.write_bytes(b"replaced")
        with self.assertRaisesRegex(AssertionError, "original_after_changed"):
            with m.original_file(original, 128) as (raw, _fd):
                self.assertEqual(raw, b"original")
                replacement.replace(original)

    def test_source_manifest_keeps_sdk_and_upstream_pins_distinct(self):
        sdk = "a" * 40
        manifest = (f'[workspace.dependencies]\nsemwright-types = {{git="https://github.com/seradotcom/semwright", rev="{sdk}"}}\n').encode()
        self.assertEqual(m.declared_sdk_pin(manifest), sdk)
        self.assertNotEqual(m.declared_sdk_pin(manifest), m.PIN)
        mixed = manifest + (f'semwright-driver-sdk = {{git="https://github.com/seradotcom/semwright", rev="{"b" * 40}"}}\n').encode()
        with self.assertRaisesRegex(AssertionError, "mixed_declared"):
            m.declared_sdk_pin(mixed)


class _FailingDiagnosticStream:
    def __init__(self, failure):
        self.failure = failure
        self.attempts = 0

    def write(self, _text):
        self.attempts += 1
        raise self.failure


class FinalDiagnosticContracts(unittest.TestCase):
    def test_stdout_failure_keeps_identical_primary_and_records_secondary(self):
        # Only the stdout sink is inert; the real print and secondary helper run.
        for primary, failure in (
            (RuntimeError("original process failure"), BrokenPipeError("closed output")),
            (KeyboardInterrupt("original cancellation"), OSError("output unavailable")),
        ):
            with self.subTest(primary=type(primary).__name__, diagnostic=type(failure).__name__):
                marker = object()
                primary.original_marker = marker
                primary.add_note("previous_original_note")
                stream = _FailingDiagnosticStream(failure)
                with mock.patch.object(m.sys, "stdout", stream):
                    with self.assertRaises(type(primary)) as caught:
                        m._finish_diagnostic({"status": "EXPERIMENTAL_UPSTREAM_SOURCE_BLOCKED"}, primary)
                self.assertIs(caught.exception, primary)
                self.assertIs(caught.exception.original_marker, marker)
                self.assertEqual(primary.__notes__, [
                    "previous_original_note",
                    "secondary_summary_not_written:" + type(failure).__name__,
                ])
                self.assertEqual(stream.attempts, 1)

    def test_stdout_failure_without_primary_propagates_identical_diagnostic(self):
        for failure in (BrokenPipeError("closed output"), OSError("output unavailable")):
            with self.subTest(diagnostic=type(failure).__name__):
                stream = _FailingDiagnosticStream(failure)
                with mock.patch.object(m.sys, "stdout", stream):
                    with self.assertRaises(type(failure)) as caught:
                        m._finish_diagnostic({"status": "EXPERIMENTAL_UPSTREAM_SOURCE_RENDER_PASS"}, None)
                self.assertIs(caught.exception, failure)
                self.assertEqual(stream.attempts, 1)

    def test_secondary_note_failure_cannot_replace_original_primary(self):
        class NoteFailure(RuntimeError):
            def add_note(self, _note):
                raise OSError("secondary note failed")

        primary = NoteFailure("original process failure")
        stream = _FailingDiagnosticStream(BrokenPipeError("closed output"))
        with mock.patch.object(m.sys, "stdout", stream):
            with self.assertRaises(NoteFailure) as caught:
                m._finish_diagnostic({"status": "EXPERIMENTAL_UPSTREAM_SOURCE_BLOCKED"}, primary)
        self.assertIs(caught.exception, primary)
        self.assertEqual(stream.attempts, 1)

    def test_success_summary_preserves_existing_public_contract(self):
        stream = io.StringIO()
        evidence = {"status": "EXPERIMENTAL_UPSTREAM_SOURCE_RENDER_PASS",
                    "private_original": object()}
        with mock.patch.object(m.sys, "stdout", stream):
            self.assertIsNone(m._finish_diagnostic(evidence, None))
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {
            "fframes_upstream": evidence["status"], "source_sha": m.PIN,
            "release_admitted": False, "originals_upload_confirmed": False,
        })

    def test_success_summary_rethrows_the_original_primary(self):
        primary = RuntimeError("private original failure")
        primary.add_note("previous_original_note")
        stream = io.StringIO()
        with mock.patch.object(m.sys, "stdout", stream):
            with self.assertRaises(RuntimeError) as caught:
                m._finish_diagnostic({"status": "EXPERIMENTAL_UPSTREAM_SOURCE_BLOCKED"}, primary)
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.__notes__, ["previous_original_note"])
        self.assertNotIn("private original failure", stream.getvalue())
        self.assertEqual(json.loads(stream.getvalue()), {
            "fframes_upstream": "EXPERIMENTAL_UPSTREAM_SOURCE_BLOCKED", "source_sha": m.PIN,
            "release_admitted": False, "originals_upload_confirmed": False,
        })


if __name__ == "__main__":
    unittest.main()
