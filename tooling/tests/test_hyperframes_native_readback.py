"""Focused source/decoder-contract tests; synthetic buffers are not native PASS."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "hyperframes_native_readback.py"
SPEC = importlib.util.spec_from_file_location("hyperframes_native_readback", SOURCE)
reader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reader
SPEC.loader.exec_module(reader)


def clock():
    return reader.MediaClock(64, 64, 2, 30, 1)


def stream():
    return {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 64, "height": 64,
            "pix_fmt": "yuv420p", "r_frame_rate": "30/1", "avg_frame_rate": "30/1",
            "time_base": "1/15360", "duration_ts": 1024, "nb_frames": "2",
            "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709"}


def frames():
    return {"frames": [{"media_type": "video", "stream_index": 0, "pts": index * 512, "pkt_duration": 512} for index in range(2)]}


def medium(first=(32, 96, 160, 255), last=(224, 64, 32, 255)):
    media_clock = clock()
    rgba = bytes(first) * (64 * 64) + bytes(last) * (64 * 64)
    digest = hashlib.sha256(rgba).hexdigest()
    observation = reader.FileObservation(5, "a" * 64, (1, 2, 5, 3, 4))
    hashes = tuple(hashlib.sha256(rgba[index * media_clock.frame_bytes:(index + 1) * media_clock.frame_bytes]).hexdigest() for index in range(2))
    return reader.DecodedMedium(observation, media_clock, rgba, hashes)


def probes():
    return [reader.PixelProbe(0, 30, 30, (32, 96, 160, 255)), reader.PixelProbe(1, 30, 30, (224, 64, 32, 255))]


class ClockTests(unittest.TestCase):
    def test_nominal_exact_clock_and_both_frames_are_observed(self):
        result = reader.validate_stream_probe({"streams": [stream()]}, clock())
        self.assertEqual(reader.validate_frame_probe(frames(), result, clock()), frames()["frames"])

    def test_boolean_rate_and_frame_count_are_not_numbers(self):
        for field in ("width", "height", "frame_count", "fps_num", "fps_den"):
            value = replace(clock(), **{field: True})
            with self.assertRaises(reader.ReadbackError):
                value.validate()

    def test_extra_audio_stream_is_rejected_before_decode(self):
        with self.assertRaisesRegex(reader.ReadbackError, "ONE_VIDEO_STREAM_REQUIRED"):
            reader.validate_stream_probe({"streams": [stream(), {"codec_type": "audio"}]}, clock())

    def test_geometry_rate_duration_hdr_and_header_count_are_independent(self):
        for field, bad in (("width", 128), ("r_frame_rate", "24/1"), ("avg_frame_rate", "25/1"),
                           ("duration_ts", 1023), ("nb_frames", "1"), ("color_transfer", "smpte2084"),
                           ("pix_fmt", "yuv420p10le")):
            with self.subTest(field=field), self.assertRaises(reader.ReadbackError):
                reader.validate_stream_probe({"streams": [{**stream(), field: bad}]}, clock())

    def test_empty_missing_extra_and_reordered_frames_are_rejected(self):
        variants = [{"frames": []}, {"frames": frames()["frames"][:1]},
                    {"frames": frames()["frames"] + frames()["frames"][:1]},
                    {"frames": list(reversed(frames()["frames"]))}]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(reader.ReadbackError):
                reader.validate_frame_probe(value, stream(), clock())

    def test_best_effort_pts_alone_cannot_repair_a_missing_source_timestamp(self):
        value = frames()
        value["frames"][0]["best_effort_timestamp"] = value["frames"][0].pop("pts")
        with self.assertRaises(reader.ReadbackError):
            reader.validate_frame_probe(value, stream(), clock())

    def test_shift_gap_foreign_stream_and_wrong_frame_duration_are_rejected(self):
        for field, bad in (("pts", 1), ("pts", 513), ("stream_index", 1), ("pkt_duration", 511), ("pts", True)):
            value = frames()
            value["frames"][1][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(reader.ReadbackError):
                reader.validate_frame_probe(value, stream(), clock())

    def test_fractional_fps_is_checked_without_float_rounding(self):
        rate = reader.MediaClock(64, 64, 2, 30000, 1001)
        info = {**stream(), "r_frame_rate": "30000/1001", "avg_frame_rate": "30000/1001", "time_base": "1/30000", "duration_ts": 2002}
        timeline = {"frames": [{"media_type": "video", "stream_index": 0, "pts": n * 1001, "pkt_duration": 1001} for n in range(2)]}
        reader.validate_stream_probe({"streams": [info]}, rate)
        reader.validate_frame_probe(timeline, info, rate)
        timeline["frames"][1]["pts"] = 1000
        with self.assertRaises(reader.ReadbackError):
            reader.validate_frame_probe(timeline, info, rate)

    def test_work_is_rejected_from_geometry_before_any_frame_producer(self):
        with self.assertRaisesRegex(reader.ReadbackError, "PIXEL_BUDGET"):
            reader.MediaClock(1920, 1080, 60, 30, 1).validate()
        with self.assertRaisesRegex(reader.ReadbackError, "DURATION_BOUND"):
            reader.MediaClock(64, 64, 180, 24, 1).validate()


class RasterTests(unittest.TestCase):
    def test_equal_whole_raster_with_two_temporal_probes_is_narrowly_classified(self):
        report = reader.compare_media(medium(), medium(), probes())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["frame_sha256"]), 2)
        self.assertTrue(report["all_frames_decoded_and_compared"])
        for key in ("glyph_legibility_verified", "editable_roundtrip_verified", "original_receipts_verified", "export_authorized", "human_approval"):
            self.assertIs(report[key], False)

    def test_unprobed_pixel_difference_still_rejects_whole_medium(self):
        left = medium()
        altered = bytearray(left.rgba)
        altered[-8] ^= 1
        right = replace(left, rgba=bytes(altered))
        with self.assertRaisesRegex(reader.ReadbackError, "WHOLE_RGBA_MISMATCH"):
            reader.compare_media(left, right, probes())

    def test_same_wrong_pixels_in_two_renders_do_not_pass_the_intent_probes(self):
        wrong = medium(first=(0, 0, 0, 255), last=(0, 0, 0, 255))
        with self.assertRaisesRegex(reader.ReadbackError, "INTENT_PIXEL_SAMPLE_MISMATCH"):
            reader.compare_media(wrong, wrong, probes())

    def test_dropped_trailing_frame_cannot_be_hidden_by_equal_hash_arrays(self):
        left = medium()
        short = replace(left, rgba=left.rgba[:-left.clock.frame_bytes])
        with self.assertRaisesRegex(reader.ReadbackError, "COMPARISON_DECODE_LENGTH"):
            reader.compare_media(short, short, probes())

    def test_reordered_frame_bytes_fail_temporal_intent(self):
        wrong = medium(first=(224, 64, 32, 255), last=(32, 96, 160, 255))
        with self.assertRaises(reader.ReadbackError):
            reader.compare_media(wrong, wrong, probes())

    def test_probe_tolerance_is_explicit_and_cannot_be_relaxed_beyond_profile(self):
        approximate = medium(first=(33, 95, 161, 255))
        admitted = [replace(probe, maximum_channel_error=1) for probe in probes()]
        reader.check_pixels(approximate, admitted)
        with self.assertRaises(reader.ReadbackError):
            reader.check_pixels(approximate, probes())
        with self.assertRaises(reader.ReadbackError):
            reader.check_pixels(approximate, [replace(probe, maximum_channel_error=25) for probe in probes()])

    def test_single_frame_probes_cannot_claim_temporal_observation(self):
        with self.assertRaisesRegex(reader.ReadbackError, "TEMPORAL_PIXEL_PROBES_REQUIRED"):
            reader.check_pixels(medium(), [probes()[0], replace(probes()[0], x=31)])


class CustodyTests(unittest.TestCase):
    def test_source_export_checks_physical_bytes_without_inventing_editability(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.json"
            exported = Path(directory) / "exported.json"
            data = b'{"native-source":"fixture"}\n'
            original.write_bytes(data)
            exported.write_bytes(data)
            report = reader.inspect_source_reopen(original, exported, source_sha256=hashlib.sha256(data).hexdigest())
            self.assertTrue(report["preserved_source_bytes_equal"])
            self.assertFalse(report["source_grammar_readmitted"])
            self.assertFalse(report["editable_roundtrip_verified"])
            exported.write_bytes(data + b" ")
            with self.assertRaises(reader.ReadbackError):
                reader.inspect_source_reopen(original, exported, source_sha256=hashlib.sha256(data).hexdigest())

    def test_wrong_hash_directory_and_size_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "source"
            target.write_bytes(b"abcd")
            with self.assertRaises(reader.ReadbackError):
                reader.checked_file(target, "0" * 64)
            with self.assertRaises(reader.ReadbackError):
                reader.checked_file(target, hashlib.sha256(b"abcd").hexdigest(), 3)
            with self.assertRaises(reader.ReadbackError):
                reader.checked_file(Path(directory), "0" * 64)

    def test_controlled_growing_reader_is_stopped_after_initial_size_plus_one(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "growing"
            target.write_bytes(b"abcd")
            original_open = Path.open
            state = {"read": 0, "calls": 0}

            class Growing:
                def __init__(self, real): self.real = real
                def __enter__(self): return self
                def __exit__(self, *args): self.real.close()
                def fileno(self): return self.real.fileno()
                def read(self, count):
                    state["calls"] += 1
                    if state["calls"] > 3:
                        raise AssertionError("independent finite iteration guard")
                    state["read"] += count
                    return b"x" * count

            def controlled_open(path, *args, **kwargs):
                handle = original_open(path, *args, **kwargs)
                return Growing(handle) if path == target else handle

            with patch.object(Path, "open", controlled_open), self.assertRaisesRegex(reader.ReadbackError, "FILE_GREW"):
                reader.checked_file(target, "0" * 64)
            self.assertEqual(state["read"], 5)
            self.assertEqual(state["calls"], 1)

    @unittest.skipIf(sys.platform == "win32", "Windows symlink fixture needs an existing grant")
    def test_symlink_ancestor_and_hard_link_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "source"
            target.write_bytes(b"data")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(reader.ReadbackError):
                reader.checked_file(link, hashlib.sha256(b"data").hexdigest())
            link.unlink()
            link.hardlink_to(target)
            with self.assertRaises(reader.ReadbackError):
                reader.checked_file(link, hashlib.sha256(b"data").hexdigest())


class DecoderPipeTests(unittest.TestCase):
    def test_successful_readonly_process_is_not_itself_a_native_render_pass(self):
        command = [sys.executable, "-B", "-c", "import sys;sys.stdout.buffer.write(b'abcd')"]
        self.assertEqual(reader._run_bounded(command, stdout_limit=4, timeout=5), b"abcd")

    def test_output_bound_is_enforced_while_reading(self):
        command = [sys.executable, "-B", "-c", "import sys;sys.stdout.buffer.write(b'x'*100000)"]
        with self.assertRaisesRegex(reader.ReadbackError, "DECODER_OUTPUT_BOUND"):
            reader._run_bounded(command, stdout_limit=4, timeout=5)

    def test_exit_zero_with_decoder_error_is_rejected(self):
        command = [sys.executable, "-B", "-c", "import sys;sys.stdout.buffer.write(b'ab');sys.stderr.write('corrupt')"]
        with self.assertRaisesRegex(reader.ReadbackError, "DECODER_REJECTED_OR_CORRUPT"):
            reader._run_bounded(command, stdout_limit=4, timeout=5)


class ExampleProbeContractTests(unittest.TestCase):
    """Exercise CLI admission with the exact JSON literals in the Rust example.

    Rust fixture execution remains a separate gate. This test stops at the
    decoder boundary; no fake successful readback or native job is produced.
    """

    def example_probes(self, variable):
        example = SOURCE.parents[1] / "crates/native/examples/native-hyperframes-e2e.rs"
        text = example.read_text(encoding="utf-8")
        match = re.search(r"let " + re.escape(variable) + r" = json!\((\[.*?\])\);", text, re.DOTALL)
        self.assertIsNotNone(match, "the committed example must retain its explicit probe fixture")
        return json.loads(match.group(1))

    def invoke_cli(self, values, directory):
        root = Path(directory).resolve()
        fixture = root / "probes.json"
        payload = json.dumps(values, separators=(",", ":")).encode()
        fixture.write_bytes(payload)
        argv = [str(SOURCE), "--direct", str(root / "direct.mp4"), "--mediated", str(root / "mediated.mp4"),
                "--direct-sha256", "a" * 64, "--mediated-sha256", "b" * 64,
                "--ffmpeg", str(root / "ffmpeg"), "--ffprobe", str(root / "ffprobe"),
                "--ffmpeg-sha256", "c" * 64, "--ffprobe-sha256", "d" * 64,
                "--width", "640", "--height", "360", "--frames", "60", "--fps-num", "30", "--fps-den", "1",
                "--pixel-probes", str(fixture), "--pixel-probes-sha256", hashlib.sha256(payload).hexdigest(),
                "--report", str(root / "report.json")]
        with patch.object(sys, "argv", argv), patch.object(sys, "stdout", io.StringIO()):
            return reader._main()

    def assert_example_admitted_before_decoder(self, variable):
        values = self.example_probes(variable)

        class ReachedDecoder(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(reader, "inspect_pair", side_effect=ReachedDecoder("source probes admitted")) as boundary, \
                    patch.object(reader.subprocess, "Popen", side_effect=AssertionError("a decoder must not run")):
                with self.assertRaisesRegex(ReachedDecoder, "source probes admitted"):
                    self.invoke_cli(values, directory)
            boundary.assert_called_once()
            selected = boundary.call_args.kwargs["probes"]
            self.assertEqual(len(selected), len(values))
            self.assertTrue(all(probe.maximum_channel_error == 12 for probe in selected))
            for probe in selected:
                probe.validate(boundary.call_args.kwargs["clock"])
            self.assertFalse((Path(directory) / "report.json").exists())

    def test_original_example_probes_reach_the_decoder_boundary(self):
        self.assert_example_admitted_before_decoder("probes")

    def test_edited_example_probes_reach_the_decoder_boundary(self):
        self.assert_example_admitted_before_decoder("edited_probes")

    def test_legacy_tolerance_key_remains_rejected_before_decoder(self):
        values = self.example_probes("probes")
        for value in values:
            value.pop("maximum_channel_error", None)
            value["tolerance"] = 12
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(reader, "inspect_pair", side_effect=AssertionError("legacy schema must not decode")) as boundary:
                self.assertEqual(self.invoke_cli(values, directory), 1)
            boundary.assert_not_called()
            self.assertFalse((Path(directory) / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
