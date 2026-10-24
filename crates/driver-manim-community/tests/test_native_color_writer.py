"""G0 guards and standards reference vectors; no native runtime or pixel PASS."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
from fractions import Fraction
from types import SimpleNamespace

path = Path(__file__).resolve().parents[1] / "src" / "native_color_writer.py"
spec = importlib.util.spec_from_file_location("native_color_writer", path)
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)


class NativeColorGuards(unittest.TestCase):
    def test_closed_policy_accepts_srgb_and_refuses_unknown_p3(self):
        writer.closed_profile(1920, 1080, 30, writer.SOURCE_POLICY)
        for policy in ("display_p3", "", "rec709", None):
            with self.subTest(policy=policy), self.assertRaises(writer.ColorProfileError):
                writer.closed_profile(1920, 1080, 30, policy)

    def test_geometry_and_fractional_fps_do_not_round(self):
        for profile in ((1919, 1080, 30), (1920, 1079, 30),
                        (1920, 1080, 29.97), (0, 1080, 30),
                        (4096, 4096, 30), (1920, 1080, True)):
            with self.subTest(profile=profile), self.assertRaises(writer.ColorProfileError):
                writer.closed_profile(*profile, writer.SOURCE_POLICY)

    def test_frozen_frames_repeat_and_total_budget_are_exact(self):
        self.assertEqual(writer.repeat_count(180, 0), 180)
        self.assertEqual(writer.repeat_count(2, 35_998), 2)
        for count, written in ((0, 0), (-1, 0), (True, 0), (1.5, 0),
                               (2, 35_999), (36_001, 0)):
            with self.subTest(count=count, written=written), self.assertRaises(writer.ColorProfileError):
                writer.repeat_count(count, written)

    def test_static_and_moving_camera_profiles_remain_distinct(self):
        self.assertEqual(writer.source_camera(b"class MotionwrightScene(Scene): pass"), "Scene")
        self.assertEqual(writer.source_camera(b"class MotionwrightScene(MovingCameraScene): pass"),
                         "MovingCameraScene")

    def test_unknown_class_and_duplicate_scene_fail_closed(self):
        for source in (
            b"class MotionwrightScene(ThreeDScene): pass",
            b"class MotionwrightScene(other.Scene): pass",
            b"class MotionwrightScene(Scene, Other): pass",
            b"class Other(Scene): pass",
            b"class MotionwrightScene(Scene): pass\nclass MotionwrightScene(Scene): pass",
        ):
            with self.subTest(source=source), self.assertRaises(writer.ColorProfileError):
                writer.source_camera(source)

    def test_non_rgba8_does_not_enter_encoder(self):
        class Sample:
            dtype = "float64"
            shape = (1080, 1920, 4)
        with self.assertRaises(writer.ColorProfileError):
            writer.rgba_shape(Sample(), 1920, 1080)

    def test_analytic_midgray_distinguishes_transfer_from_tagging(self):
        # Independent reference equations from CSS Color4 and ITU-R BT.709-6.
        # This is NOT execution of native filters, PyAV, FFmpeg or the writer.
        srgb = 128 / 255
        linear = ((srgb + 0.055) / 1.055) ** 2.4
        rec709 = 1.099 * linear ** 0.45 - 0.099
        transformed_y = int(219 * rec709 + 16 + 0.5)
        tagged_only_y = int(219 * srgb + 16 + 0.5)
        self.assertEqual(transformed_y, 115)
        self.assertEqual(tagged_only_y, 126)
        self.assertGreater(tagged_only_y - transformed_y, 10)

    def test_writer_declares_square_pixel_context_before_encoding(self):
        # Exercise the real public-hook method with inert AV/Base collaborators.
        # This verifies producer configuration, never a native codec observation.
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            artifact = Path(directory).resolve() / "native.mp4"
            context = SimpleNamespace()
            stream = SimpleNamespace(codec_context=context)
            container = SimpleNamespace(add_stream=lambda *a, **kw: stream,
                                        close=lambda: None)
            av = SimpleNamespace(open=lambda *a, **kw: container)
            class Base:
                def __init__(self, renderer, name, **kwargs):
                    self.movie_file_path = str(artifact)
            cls = writer.writer_type(av, Base, 1920, 1080, 30, "a" * 64,
                                     Path(directory) / "scene.py", artifact, None)
            instance = cls(None, "MotionwrightScene")
            try:
                instance.open_partial_movie_stream()
                self.assertEqual(context.sample_aspect_ratio, Fraction(1, 1))
                self.assertEqual(context.time_base, Fraction(1, 30))
                self.assertEqual(stream.pix_fmt, "yuv420p")
            finally:
                instance.abort_encode_jobs()

    def test_recipe_names_do_not_claim_display_adaptation(self):
        self.assertEqual(writer.COLOR_RECIPE, "srgb-to-rec709-full12-v2")
        self.assertIn("itrc=srgb", writer.COLORSPACE)
        self.assertIn(":trc=bt709:", writer.COLORSPACE)
        self.assertIn("range=tv", writer.COLORSPACE)
        self.assertNotIn("zscale", writer.COLOR_FILTER)


    def test_pinned_wheel_without_zscale_uses_the_observed_full12_graph(self):
        # An inert graph records the real producer calls. It is not pixel evidence.
        calls = []
        class Graph:
            def add_buffer(self, **kwargs):
                calls.append(("buffer", kwargs))
                return "source"
            def add(self, name, *args):
                calls.append((name, args))
                return name
            def link_nodes(self, *nodes):
                calls.append(("links", nodes))
            def configure(self):
                calls.append(("configure",))
        av = SimpleNamespace(filter=SimpleNamespace(Graph=Graph))
        available = {"buffer", "scale", "format", "colorspace", "buffersink"}
        writer.make_graph(av, 1920, 1080, 30, available)
        self.assertEqual([c[0] for c in calls],
                         ["buffer", "scale", "format", "colorspace",
                          "buffersink", "links", "configure"])
        self.assertEqual(calls[0][1],
                         {"width": 1920, "height": 1080, "format": "rgba",
                          "time_base": Fraction(1, 30)})
        self.assertEqual(calls[1][1], (
            "in_color_matrix=bt709:out_color_matrix=bt709:"
            "in_range=pc:out_range=pc:flags=accurate_rnd+full_chroma_int",))
        self.assertEqual(calls[2][1], ("pix_fmts=yuv444p12le",))
        self.assertEqual(calls[3][1], (
            "ispace=bt709:iprimaries=bt709:itrc=srgb:irange=pc:"
            "space=bt709:primaries=bt709:trc=bt709:range=tv:"
            "format=yuv420p:fast=0:dither=none",))

    def test_missing_required_filter_refuses_before_graph_or_output(self):
        constructed = []
        av = SimpleNamespace(filter=SimpleNamespace(
            Graph=lambda: constructed.append(True)))
        for missing in writer.REQUIRED_FILTERS:
            with self.subTest(missing=missing):
                available = writer.REQUIRED_FILTERS - {missing}
                with self.assertRaisesRegex(writer.ColorProfileError,
                                            "native_color_filters_unavailable"):
                    writer.make_graph(av, 1920, 1080, 30, available)
        self.assertEqual(constructed, [])

    def test_graph_configuration_failure_has_no_fallback(self):
        created = []
        class Graph:
            def __init__(self):
                created.append(self)
            def add_buffer(self, **kwargs):
                return "source"
            def add(self, *args):
                return args[0]
            def link_nodes(self, *args):
                pass
            def configure(self):
                raise RuntimeError("native-filter-configuration-failed")
        av = SimpleNamespace(filter=SimpleNamespace(Graph=Graph))
        with self.assertRaisesRegex(RuntimeError, "native-filter-configuration-failed"):
            writer.make_graph(av, 1920, 1080, 30, writer.REQUIRED_FILTERS)
        self.assertEqual(len(created), 1)

    def test_native_rgba_is_identified_before_actual_transfer_graph(self):
        # Exercises write_frame, not a native filter/codec or a generated receipt.
        class Alpha:
            def __eq__(self, other):
                return self
            def all(self):
                return True
        class Rgba:
            dtype = "uint8"
            shape = (2, 2, 4)
            def __getitem__(self, key):
                return Alpha()
            def tobytes(self, order):
                return bytes((128, 128, 128, 255)) * 4
        frames = []
        class Graph:
            def push(self, frame):
                frames.append(frame)
            def pull(self):
                frame = frames[-1]
                return SimpleNamespace(
                    width=2, height=2, format=SimpleNamespace(name="yuv420p"),
                    pts=frame.pts, time_base=frame.time_base,
                    colorspace=1, color_range=1)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            artifact = Path(directory).resolve() / "native.mp4"
            context = SimpleNamespace()
            stream = SimpleNamespace(codec_context=context, encode=lambda frame: [])
            container = SimpleNamespace(add_stream=lambda *a, **kw: stream,
                                        close=lambda: None)
            av = SimpleNamespace(open=lambda *a, **kw: container,
                                 VideoFrame=SimpleNamespace(
                                     from_ndarray=lambda *a, **kw: SimpleNamespace()))
            class Base:
                def __init__(self, renderer, name, **kwargs):
                    self.movie_file_path = str(artifact)
            cls = writer.writer_type(av, Base, 2, 2, 30, "a" * 64,
                                     Path(directory) / "scene.py", artifact, Graph())
            instance = cls(None, "MotionwrightScene")
            try:
                instance.write_frame(Rgba(), num_frames=2)
                self.assertEqual(instance.frame_count, 2)
                self.assertEqual([(f.pts, f.time_base, f.color_range, f.colorspace)
                                  for f in frames],
                                 [(0, Fraction(1, 30), 2, 0),
                                  (1, Fraction(1, 30), 2, 0)])
            finally:
                instance.abort_encode_jobs()

if __name__ == "__main__":
    unittest.main()
