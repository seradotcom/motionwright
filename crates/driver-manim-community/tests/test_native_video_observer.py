"""Data-path tests; mocks exercise bounds, custody and exact observation semantics.

These tests do not claim that PyAV, Manim, a native Host or MLT was executed.
"""
from contextlib import nullcontext
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from fractions import Fraction
from unittest import mock
import zlib

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "native_video_observer.py"
SPEC = importlib.util.spec_from_file_location("native_video_observer", MODULE_PATH)
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


class Alpha:
    def __init__(self, packed):
        self.values = packed[3::4]

    def min(self):
        return min(self.values)

    def max(self):
        return max(self.values)


class Array:
    dtype = "uint8"

    def __init__(self, width=16, height=16, alpha=255):
        self.shape = (height, width, 4)
        self.packed = bytes((17, 68, 221, alpha)) * width * height

    def __getitem__(self, key):
        assert key == (slice(None), slice(None), 3)
        return Alpha(self.packed)

    def tobytes(self, order):
        assert order == "C"
        return self.packed


class Frame:
    def __init__(self, index, *, color=1, alpha=255, width=16, height=16):
        self.width, self.height = width, height
        self.format = types.SimpleNamespace(name="yuv420p")
        self.time_base = Fraction(1, 90_000)
        self.pts = index * 3000
        self.colorspace = color
        self.color_range = 1
        self.array = Array(width, height, alpha)
        self.conversion = None
        self.interlaced_frame = False

    def to_ndarray(self, **kwargs):
        self.conversion = kwargs
        return self.array


class Container:
    def __init__(self, frames, audio=0, on_decode=None, color=1):
        self.frames = frames
        self.on_decode = on_decode
        self.context = types.SimpleNamespace(
            width=16, height=16, name="h264",
            format=types.SimpleNamespace(name="yuv420p"),
            colorspace=color, color_range=1, color_primaries=color, color_trc=color,
            sample_aspect_ratio=Fraction(1, 1),
        )
        self.streams = [
            types.SimpleNamespace(
                type="video", codec_context=self.context, average_rate=Fraction(30, 1)
            )
        ] + [types.SimpleNamespace(type="audio") for _ in range(audio)]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def decode(self, stream):
        assert stream is self.streams[0]
        if self.on_decode:
            self.on_decode()
        yield from self.frames


def av_module(container):
    value = types.SimpleNamespace(
        __version__="15.0.0", library_versions={"libavcodec": (61, 19, 100)}
    )
    def open_video(handle, mode):
        assert mode == "r"
        assert handle.read(1) == b"v"
        handle.seek(0)
        return container
    value.open = open_video
    return value


def decode_png(png):
    """Independent minimal PNG reader: validate every chunk CRC, then inflate."""
    assert png[:8] == bytes((137, 80, 78, 71, 13, 10, 26, 10))
    offset, image_data, dimensions = 8, bytearray(), None
    kinds = []
    while offset < len(png):
        size = struct.unpack(">I", png[offset:offset + 4])[0]
        kind = png[offset + 4:offset + 8]
        data = png[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", png[offset + 8 + size:offset + 12 + size])[0]
        assert crc == zlib.crc32(kind + data) & 0xFFFFFFFF
        kinds.append(kind)
        if kind == b"IHDR":
            dimensions = struct.unpack(">IIBBBBB", data)
        if kind == b"IDAT":
            image_data.extend(data)
        offset += size + 12
    assert offset == len(png) and kinds == [b"IHDR", b"IDAT", b"IEND"]
    width, height, bits, color, compression, filtering, interlace = dimensions
    assert (bits, color, compression, filtering, interlace) == (8, 6, 0, 0, 0)
    rows = zlib.decompress(image_data)
    stride = width * 4 + 1
    assert len(rows) == height * stride
    assert all(rows[start] == 0 for start in range(0, len(rows), stride))
    return width, height, b"".join(
        rows[start + 1:start + stride] for start in range(0, len(rows), stride)
    )


class ObserverTests(unittest.TestCase):
    def setUp(self):
        # The caller selects an isolated temporary root, including on Windows.
        self.temp = tempfile.TemporaryDirectory(prefix="native-observer-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.video = self.root / "original.mp4"
        self.source = self.root / "original.py"
        self.report = self.root / "original.mp4.decoded-video.json"
        self.video.write_bytes(b"video-bytes-original")
        self.source.write_bytes(b"# original generated source\n")
        self.frames = [Frame(index) for index in range(3)]

    def run_observation(self, container=None):
        container = container or Container(self.frames)
        with mock.patch.dict(sys.modules, {"av": av_module(container)}):
            receipt = observer.run(self.video, self.source, self.report)
        return receipt, json.loads(self.report.read_bytes())

    def assert_failed(self, category, container=None, **limits):
        with (mock.patch.multiple(observer, **limits) if limits else nullcontext()):
            with mock.patch.dict(sys.modules, {"av": av_module(container or Container(self.frames))}):
                with self.assertRaisesRegex(observer.ObservationError, category):
                    observer.run(self.video, self.source, self.report)
        self.assertFalse(self.report.exists(), "failure must not publish a complete report")

    def test_complete_range_retains_exact_rgba_with_lossless_png(self):
        receipt, report = self.run_observation()
        self.assertEqual(receipt["sha256"], hashlib.sha256(self.report.read_bytes()).hexdigest())
        self.assertEqual(report["artifact_sha256"], hashlib.sha256(self.video.read_bytes()).hexdigest())
        self.assertEqual(report["source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual((report["first_frame"], report["end_frame_exclusive"]), (0, 3))
        self.assertEqual(report["frame_count"], len(self.frames))
        digest = hashlib.sha256(b"motionwright.native-rgba-sequence/1\0")
        retained = 0
        for index, record in enumerate(report["frames"]):
            self.assertEqual(Fraction(record["pts_num"], record["pts_den"]), Fraction(index, 30))
            path = Path(str(self.report) + ".frames") / record["file"]
            raw = path.read_bytes()
            width, height, packed = decode_png(raw)
            self.assertEqual((width, height, packed), (16, 16, self.frames[index].array.packed))
            self.assertEqual(record["png_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(record["png_bytes"], len(raw))
            self.assertEqual(record["rgba_sha256"], hashlib.sha256(packed).hexdigest())
            self.assertEqual(record["presentation"], {
                "scope": "pyav-codec-context-per-frame-and-frame-interlace-v1",
                "sample_aspect_num": 1, "sample_aspect_den": 1, "interlaced_frame": False,
            })
            digest.update(struct.pack(">Q", index))
            digest.update(hashlib.sha256(packed).digest())
            retained += len(raw)
        self.assertEqual(report["rgba_sequence_sha256"], digest.hexdigest())
        self.assertEqual(report["retained_png_bytes"], retained)
        self.assertTrue(report["all_pixels_opaque"])
        self.assertTrue(all(f.conversion == {"format": "rgba", "src_colorspace": 1, "dst_colorspace": 5, "src_color_range": 1, "dst_color_range": 2} for f in self.frames))

    def test_unknown_presentation_is_retained_without_profile_defaults(self):
        for frame in self.frames:
            del frame.interlaced_frame
        container = Container(self.frames)
        container.context.sample_aspect_ratio = None
        _, result = self.run_observation(container)
        for frame in result["frames"]:
            self.assertIsNone(frame["presentation"]["sample_aspect_num"])
            self.assertIsNone(frame["presentation"]["sample_aspect_den"])
            self.assertIsNone(frame["presentation"]["interlaced_frame"])

    def test_actual_interlace_and_codec_context_aspect_are_not_hidden(self):
        self.frames[1].interlaced_frame = True
        container = Container(self.frames)
        container.context.sample_aspect_ratio = Fraction(4, 3)
        _, result = self.run_observation(container)
        self.assertTrue(result["frames"][1]["presentation"]["interlaced_frame"])
        self.assertFalse(result["frames"][0]["presentation"]["interlaced_frame"])
        for frame in result["frames"]:
            self.assertEqual(frame["presentation"]["sample_aspect_num"], 4)
            self.assertEqual(frame["presentation"]["sample_aspect_den"], 3)

    def test_aspect_is_observed_on_each_decoded_frame(self):
        container = Container(self.frames)
        def decode(stream):
            self.assertIs(stream, container.streams[0])
            for index, frame in enumerate(self.frames):
                container.context.sample_aspect_ratio = Fraction(4, 3) if index == 1 else Fraction(1, 1)
                yield frame
        container.decode = decode
        _, result = self.run_observation(container)
        self.assertEqual([f["presentation"]["sample_aspect_num"] for f in result["frames"]], [1, 4, 1])

    def test_unknown_color_is_never_filled_from_a_requested_profile(self):
        self.frames = [Frame(i, color=None) for i in range(3)]
        _, result = self.run_observation(Container(self.frames, color=None))
        self.assertIsNone(result["stream_color"]["matrix"])
        self.assertIsNone(result["stream_color"]["primaries"])
        self.assertIsNone(result["stream_color"]["transfer"])
        self.assertIsNone(result["frame_color"]["matrix"])
        self.assertTrue(all(f.conversion == {"format": "rgba", "src_color_range": 1, "dst_color_range": 2} for f in self.frames))

    def test_observed_full_range_is_supplied_to_rgba_conversion(self):
        for frame in self.frames:
            frame.color_range = 2
        container = Container(self.frames)
        container.context.color_range = 2
        _, result = self.run_observation(container)
        self.assertEqual(result["stream_color"]["range"], 2)
        self.assertEqual(result["frame_color"]["range"], 2)
        self.assertTrue(all(f.conversion["src_color_range"] == 2 for f in self.frames))
        self.assertTrue(all(f.conversion["dst_color_range"] == 2 for f in self.frames))
        self.assertTrue(all(f.conversion["src_colorspace"] != f.conversion["dst_colorspace"] for f in self.frames))

    def test_all_pixels_and_audio_presence_remain_observed(self):
        self.frames[1].array = Array(alpha=19)
        _, result = self.run_observation(Container(self.frames, audio=2))
        self.assertEqual(result["audio_streams"], 2)
        self.assertEqual((result["alpha_min"], result["alpha_max"]), (19, 255))
        self.assertFalse(result["all_pixels_opaque"])

    def test_nonuniform_pts_are_rejected_without_report(self):
        self.frames[1].pts += 1
        self.assert_failed("nonuniform_pts")

    def test_missing_middle_frame_cannot_be_hidden_by_duration(self):
        self.assert_failed("nonuniform_pts", Container([self.frames[0], self.frames[2]]))

    def test_changing_geometry_is_rejected(self):
        self.frames[1].width = 17
        self.assert_failed("changing_geometry")

    def test_changing_format_is_rejected(self):
        self.frames[1].format.name = "yuv444p"
        self.assert_failed("changing_pixel_format")

    def test_changing_color_is_rejected(self):
        self.frames[1].colorspace = 6
        self.assert_failed("changing_color")

    def test_empty_video_is_not_zero_duration_success(self):
        self.assert_failed("empty_video", Container([]))

    def test_frame_count_budget_is_exact(self):
        self.assert_failed("frame_budget", MAX_FRAMES=2)

    def test_png_aggregate_budget_is_exact(self):
        self.assert_failed("png_aggregate_budget", MAX_PNG_TOTAL_BYTES=1)

    def test_report_budget_never_truncates_observations(self):
        self.assert_failed("report_budget", MAX_REPORT_BYTES=1)

    def test_video_changed_after_open_is_not_original_identity(self):
        def mutate():
            self.video.write_bytes(b"video-bytes-mutated!")
        self.assert_failed("artifact_changed_after_decode", Container(self.frames, on_decode=mutate))

    def test_source_changed_during_decode_is_not_original_identity(self):
        def mutate():
            self.source.write_bytes(b"# changed generated source\n")
        self.assert_failed("source_changed", Container(self.frames, on_decode=mutate))

    def test_report_and_frame_collision_preserve_original_bytes(self):
        _, before = self.run_observation()
        original = self.report.read_bytes()
        png = Path(str(self.report) + ".frames") / "000000.png"
        image_before = png.read_bytes()
        with mock.patch.dict(sys.modules, {"av": av_module(Container(self.frames))}):
            with self.assertRaises(FileExistsError):
                observer.run(self.video, self.source, self.report)
        self.assertEqual(original, self.report.read_bytes())
        self.assertEqual(image_before, png.read_bytes())
        self.assertEqual(before["frame_count"], 3)

    def test_foreign_stream_is_not_silently_dropped(self):
        c = Container(self.frames)
        c.streams.append(types.SimpleNamespace(type="subtitle"))
        self.assert_failed("unsupported_stream", c)

    def test_missing_pts_is_rejected(self):
        self.frames[1].pts = None
        self.assert_failed("missing_pts")

    def test_partial_failure_retains_prior_frames_but_no_complete_report(self):
        self.frames[2].pts = 100
        self.assert_failed("nonuniform_pts")
        directory = Path(str(self.report) + ".frames")
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ["000000.png", "000001.png"])


class NativeMetadataTests(unittest.TestCase):
    """Synthetic data and bounded logging tests, not a native execution."""
    HEADER = (
        "n:   0 pts:      0 pts_time:999 duration:512 duration_time:999 "
        "fmt:yuv420p cl:left sar:0/1 s:16x16 i:P iskey:1 type:I"
    )
    COLOR = "color_range:tv color_space:bt709 color_primaries:bt709 color_trc:bt709"

    def parse(self, text=None, **kwargs):
        values = dict(index=0, pts=0, time_base=Fraction(1, 15360),
                      width=16, height=16, yuv_sha256="f" * 64)
        values.update(kwargs)
        return observer.native_frame_metadata(
            text if text is not None else self.HEADER + "\n" + self.COLOR, **values)

    @staticmethod
    def record(message):
        return logging.LogRecord("libav." + observer.NATIVE_LOG_NAME,
                                 logging.INFO, "", 0, message, (), None)

    def test_native_integer_clock_and_color_do_not_use_float_time_or_stream(self):
        result = self.parse()
        self.assertEqual(result["scope"], observer.NATIVE_METADATA_SCOPE)
        self.assertEqual(result["pts"], 0)
        self.assertEqual(result["duration"], 512)
        self.assertEqual(Fraction(result["duration"] * result["time_base_num"],
                                  result["time_base_den"]), Fraction(1, 30))
        self.assertEqual(result["color"],
                         dict(matrix=1, range=1, primaries=1, transfer=1))
        self.assertNotIn("sample_aspect_num", result)

    def test_native_unknown_fields_stay_unknown_without_codec_context_fill(self):
        result = self.parse(self.HEADER + "\n" + self.COLOR.replace("color_trc:bt709", "color_trc:unknown"))
        self.assertIsNone(result["color"]["transfer"])

    def test_native_fragment_stream_accepts_sei_under_bound_without_retaining_list(self):
        handler = observer.BoundedNativeLogHandler()
        handler.emit(self.record(self.HEADER))
        for _ in range(1000):
            handler.emit(self.record("ab"))
        for part in self.COLOR.split():
            handler.emit(self.record(part))
        result = self.parse(handler.text())
        self.assertEqual(result["color"]["primaries"], 1)
        self.assertEqual(handler.fragments, 1005)
        self.assertLessEqual(len(handler.data), observer.MAX_NATIVE_LOG_BYTES)

    def test_native_byte_budget_fails_closed_before_capture_can_grow(self):
        handler = observer.BoundedNativeLogHandler()
        handler.emit(self.record("x" * (observer.MAX_NATIVE_LOG_BYTES - 1)))
        self.assertEqual(len(handler.data), observer.MAX_NATIVE_LOG_BYTES)
        handler.emit(self.record("x"))
        for _ in range(1000):
            handler.emit(self.record("discard"))
        self.assertTrue(handler.overflow)
        self.assertEqual(len(handler.data), observer.MAX_NATIVE_LOG_BYTES)
        with self.assertRaisesRegex(observer.ObservationError, "native_metadata_log_budget"):
            handler.text()

    def test_native_fragment_budget_is_independent_of_message_byte_size(self):
        handler = observer.BoundedNativeLogHandler()
        for _ in range(observer.MAX_NATIVE_LOG_FRAGMENTS):
            handler.emit(self.record(""))
        self.assertFalse(handler.overflow)
        handler.emit(self.record(""))
        self.assertTrue(handler.overflow)
        self.assertEqual(handler.fragments, observer.MAX_NATIVE_LOG_FRAGMENTS + 1)
        self.assertEqual(len(handler.data), observer.MAX_NATIVE_LOG_FRAGMENTS)
        handler.reset()
        self.assertEqual(handler.text(), "")
        self.assertFalse(handler.overflow)

    def test_native_malformed_and_duplicate_observations_are_refused(self):
        for bad in [
            self.HEADER,
            self.HEADER + "\n" + self.COLOR + "\n" + self.COLOR,
            self.HEADER + "\n" + self.HEADER + "\n" + self.COLOR,
            self.HEADER.replace("pts:      0", "pts:      NOPTS") + "\n" + self.COLOR,
            self.HEADER.replace("duration:512", "duration:-1") + "\n" + self.COLOR,
            self.HEADER.replace("s:16x16", "s:32x16") + "\n" + self.COLOR,
            self.HEADER.replace("fmt:yuv420p", "fmt:yuv444p") + "\n" + self.COLOR,
        ]:
            with self.subTest(text=bad):
                with self.assertRaises(observer.ObservationError):
                    self.parse(bad)
        for values in [dict(index=1), dict(pts=1), dict(time_base=Fraction(1, 1_000_000_001))]:
            with self.assertRaises(observer.ObservationError):
                self.parse(**values)

    def test_native_zero_duration_is_retained_as_observed_for_admission_to_refuse(self):
        result = self.parse(self.HEADER.replace("duration:512", "duration:0") + "\n" + self.COLOR)
        self.assertEqual(result["duration"], 0)

    def test_native_logger_and_python_threshold_restore_even_on_decode_error(self):
        state = {"level": None, "skip": True, "libav": None}
        api = types.SimpleNamespace(
            INFO=32, get_level=lambda: state["level"],
            set_level=lambda x: state.__setitem__("level", x),
            get_skip_repeated=lambda: state["skip"],
            set_skip_repeated=lambda x: state.__setitem__("skip", x),
            set_libav_level=lambda x: state.__setitem__("libav", x),
        )
        av = types.SimpleNamespace(__version__="15.0.0", logging=api,
                                   filter=types.SimpleNamespace(filters_available={"buffer", "showinfo", "buffersink"}))
        logger = logging.getLogger("libav." + observer.NATIVE_LOG_NAME)
        before = (logger.handlers[:], logger.level, logger.propagate, logger.disabled)
        with self.assertRaisesRegex(RuntimeError, "decode failed"):
            with observer.NativeFrameMetadataReader(av) as reader:
                self.assertEqual(logger.handlers, [reader.handler])
                self.assertFalse(logger.propagate)
                self.assertFalse(logger.disabled)
                self.assertEqual(state["level"], 32)
                self.assertFalse(state["skip"])
                raise RuntimeError("decode failed")
        self.assertEqual((logger.handlers, logger.level, logger.propagate, logger.disabled), before)
        self.assertIsNone(state["level"])
        self.assertTrue(state["skip"])
        # The public API cannot read the previous C threshold. Policy remains
        # explicit for the isolated process; this is not a fake restoration.
        self.assertEqual(state["libav"], 32)

    def test_native_unavailable_reader_does_not_fill_missing_api_values(self):
        av = types.SimpleNamespace(__version__="15.0.0")
        frame = Frame(0)
        with observer.NativeFrameMetadataReader(av) as reader:
            self.assertIsNone(reader.observe(frame, 0))
            self.assertIsNone(observer.color_metadata(frame)["primaries"])
            self.assertIsNone(observer.color_metadata(frame)["transfer"])
            reader.finish()

    def test_native_yuv_hash_covers_every_visible_plane_but_not_line_padding(self):
        class Plane(bytearray):
            def __init__(self, columns, rows, fill):
                super().__init__(bytes([fill] * columns + [99, 99]) * rows)
                self.line_size = columns + 2
        frame = types.SimpleNamespace(
            width=16, height=16, format=types.SimpleNamespace(name="yuv420p"),
            planes=[Plane(16, 16, 1), Plane(8, 8, 2), Plane(8, 8, 3)],
        )
        expected = hashlib.sha256(bytes([1]) * 256 + bytes([2]) * 64 + bytes([3]) * 64).hexdigest()
        self.assertEqual(observer.yuv420_sha256(frame), expected)
        frame.planes[0][16] = 77
        self.assertEqual(observer.yuv420_sha256(frame), expected)
        frame.planes[2][-3] = 4
        self.assertNotEqual(observer.yuv420_sha256(frame), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
