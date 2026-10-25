"""Pure vector-contract and mocked native ownership tests; no native rendering.

No test loads Pango, Cairo, Manim or fonts. These checks do not grant admission,
prove glyph support, or substitute for the real sealed-runtime frame probe.
"""
from __future__ import annotations

import copy
import ctypes as C
import hashlib
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manim_graph_vector_text as vector

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="640px" height="360px" '
       b'viewBox="0 0 640 360" version="1.1"><g><path d="M 1 1 L 2 1 L 2 2 Z" '
       b'style="fill:rgb(100%,100%,100%);stroke:none"/></g></svg>')
FONT = "/mock-runtime-fonts/a.woff2"
ALLOWED = {FONT: {"relative_path": "a.woff2", "sha256": "ab" * 32, "size_bytes": 12}}


def observed():
    return {
        "unknown_glyph_count": 0,
        "runs": [{"offset": 0, "length": 1, "num_chars": 1, "family": "Instrument Sans",
                  "file": FONT, "face_index": 0, "weight": 400, "style": 0}],
        "ink_pango_units": [0, 0, 10240, 20480],
        "logical_pango_units": [0, 0, 12288, 24576],
        "pango_version": "MOCK", "fontconfig_version": 0,
    }


def signature():
    return {"text": b"A", "unknown": 0, "ink": [0, 0, 10240, 20480],
            "logical": [0, 0, 12288, 24576], "baseline": 18432}


class SinkAndSvgContractTests(unittest.TestCase):
    def test_exact_sink_bound_succeeds_then_failure_preserves_partial_bytes(self):
        sink = vector.BoundedSvgSink(4)
        self.assertEqual(sink.append(b"1234"), 0)
        self.assertEqual(sink.append(b"5"), 11)
        self.assertEqual(sink.append(b""), 11)
        self.assertEqual(bytes(sink.buffer), b"1234")

    def test_callback_copies_observed_bytes_within_bound(self):
        sink = vector.BoundedSvgSink(4)
        buffer = C.create_string_buffer(b"abcd")
        self.assertEqual(sink.callback(None, C.addressof(buffer), 4), 0)
        self.assertEqual(bytes(sink.buffer), b"abcd")

    def test_callback_rejects_oversize_before_reading_pointer(self):
        sink = vector.BoundedSvgSink(4)
        with mock.patch.object(C, "string_at", side_effect=AssertionError("must not read")):
            self.assertEqual(sink.callback(None, None, 5), 11)
        self.assertTrue(sink.failed)

    def test_mock_vector_svg_has_explicit_pixel_viewport(self):
        self.assertEqual(vector.validate_vector_svg(SVG, 640, 360), 1)

    def test_viewport_scaling_or_translation_is_rejected(self):
        for old, new in [(b'640px', b'480pt'), (b'0 0 640 360', b'1 0 640 360')]:
            with self.subTest(new=new), self.assertRaisesRegex(vector.Rejected, "viewport"):
                vector.validate_vector_svg(SVG.replace(old, new), 640, 360)

    def test_raster_text_use_and_executable_nodes_are_rejected(self):
        for tag in ["image", "text", "use", "script", "foreignObject", "foreign:node"]:
            raw = SVG.replace(b'<g>', f'<g><{tag}/>'.encode())
            with self.subTest(tag=tag), self.assertRaises(vector.Rejected):
                vector.validate_vector_svg(raw, 640, 360)

    def test_external_reference_and_event_attributes_are_rejected(self):
        for attribute in [b'href="https://example.test/a"', b'onload="bad"',
                          b'fill="url(file:/tmp/x)"', b'style="fill:url(data:fake)"']:
            raw = SVG.replace(b'<path ', b'<path ' + attribute + b" ")
            with self.subTest(attribute=attribute), self.assertRaises(vector.Rejected):
                vector.validate_vector_svg(raw, 640, 360)

    def test_xml_declarations_and_empty_outlines_are_rejected(self):
        for raw in [b"<!DOCTYPE svg>" + SVG, SVG.replace(b'M 1 1 L 2 1 L 2 2 Z', b''),
                    SVG.replace(b'<path ', b'<defs ').replace(b'/></g>', b'/></g>')]:
            with self.subTest(raw=raw), self.assertRaises(vector.Rejected):
                vector.validate_vector_svg(raw, 640, 360)

    def test_source_bound_is_checked_before_xml_parsing(self):
        with mock.patch.object(vector.ET, "fromstring", side_effect=AssertionError("must not parse")):
            with self.assertRaisesRegex(vector.Rejected, "output_budget"):
                vector.validate_vector_svg(b"x" * (vector.MAX_LABEL_SVG_BYTES + 1), 640, 360)

    def test_authored_box_fit_is_inclusive_and_does_not_mutate(self):
        report = {"logical_bounds_px": [1.0, 2.0, 11.0, 22.0],
                  "outline_bounds_px": [1.0, 2.0, 11.0, 22.0]}
        before = copy.deepcopy(report)
        vector.require_authored_box_fit(report, {"x": 1, "y": 2, "width": 10, "height": 20})
        self.assertEqual(report, before)

    def test_even_small_overflow_is_rejected_without_fit_or_clipping(self):
        for field, index, value in [("logical_bounds_px", 0, 0.999999),
                                    ("outline_bounds_px", 3, 22.000001)]:
            report = {"logical_bounds_px": [1, 2, 11, 22], "outline_bounds_px": [1, 2, 11, 22]}
            report[field][index] = value
            with self.subTest(field=field), self.assertRaisesRegex(vector.Rejected, "overflow"):
                vector.require_authored_box_fit(report, {"x": 1, "y": 2, "width": 10, "height": 20})


class MockSameLayoutTests(unittest.TestCase):
    def invoke(self, observation=None, dimensions=(640, 360)):
        return vector.vectorize_observed_layout(
            self.native, self.layout, observation or observed(), ALLOWED,
            "Instrument Sans", b"A", dimensions, [100.0, 200.0], "#ffffff")

    def setUp(self):
        self.native, self.layout = object(), object()

    def test_mock_same_live_layout_is_used_and_raw_svg_digest_preserved(self):
        cairo = mock.Mock()
        cairo.outlines.return_value = (SVG, [100.0, 200.0, 110.0, 220.0], "MOCK")
        with mock.patch.object(vector, "_native_layout_signature", side_effect=[signature(), signature()]), \
                mock.patch.object(vector, "NativeCairoSvg", return_value=cairo) as constructor:
            raw, report = self.invoke()
        constructor.assert_called_once_with(self.native)
        self.assertIs(cairo.outlines.call_args.args[0], self.layout)
        self.assertEqual(raw, SVG)
        self.assertEqual(report["svg_sha256"], hashlib.sha256(SVG).hexdigest())
        self.assertEqual(report["logical_bounds_px"], [100.0, 200.0, 112.0, 224.0])
        self.assertEqual(report["baseline_px"], 218.0)
        self.assertFalse(report["manim_import_verified"])
        self.assertFalse(report["profile_admitted"])

    def test_changed_layout_before_vectorization_stops_before_cairo(self):
        changed = signature()
        changed["text"] = b"B"
        with mock.patch.object(vector, "_native_layout_signature", return_value=changed), \
                mock.patch.object(vector, "NativeCairoSvg") as constructor:
            with self.assertRaisesRegex(vector.Rejected, "changed_before"):
                self.invoke()
        constructor.assert_not_called()

    def test_changed_layout_during_vectorization_is_rejected(self):
        changed = signature()
        changed["baseline"] += 1
        cairo = mock.Mock()
        cairo.outlines.return_value = (SVG, [100, 200, 110, 220], "MOCK")
        with mock.patch.object(vector, "_native_layout_signature", side_effect=[signature(), changed]), \
                mock.patch.object(vector, "NativeCairoSvg", return_value=cairo):
            with self.assertRaisesRegex(vector.Rejected, "changed_during"):
                self.invoke()

    def test_font_substitution_is_rejected_before_native_draw(self):
        observation = observed()
        observation["runs"][0]["file"] = "/system/fallback.ttf"
        with mock.patch.object(vector, "NativeCairoSvg") as constructor:
            with self.assertRaisesRegex(vector.Rejected, "file_substitution"):
                self.invoke(observation)
        constructor.assert_not_called()

    def test_nonintegral_or_unbounded_document_stops_before_native_draw(self):
        for size in [(640.0, 360), (640, 0), (10000, 10000), (True, 360)]:
            with self.subTest(size=size), mock.patch.object(vector, "NativeCairoSvg") as constructor:
                with self.assertRaisesRegex(vector.Rejected, "document_size"):
                    self.invoke(dimensions=size)
                constructor.assert_not_called()


def fake_cairo(events, surface_error=False, context_error=False, raw=SVG):
    """A mock ABI object; no native function or font is loaded."""
    native = object.__new__(vector.NativeCairoSvg)
    callback = {}

    def create_surface(writer, _closure, _width, _height):
        callback["writer"] = writer
        events.append("surface")
        return 101

    def finish_surface(_surface):
        events.append("finish")
        buffer = C.create_string_buffer(raw)
        callback["writer"](None, C.addressof(buffer), len(raw))

    def path_extents(_context, *pointers):
        for pointer, value in zip(pointers, [1.0, 2.0, 3.0, 4.0]):
            C.cast(pointer, C.POINTER(C.c_double))[0] = value

    native.callback_type = lambda function: function
    native.create_surface = create_surface
    native.surface_unit = lambda *_: None
    native.surface_version = lambda *_: None
    native.surface_status = lambda *_: 1 if surface_error else 0
    native.create_context = lambda *_: 102
    native.context_status = lambda *_: 1 if context_error else 0
    native.destroy_context = lambda *_: events.append("destroy_context")
    native.finish_surface = finish_surface
    native.destroy_surface = lambda *_: events.append("destroy_surface")
    native.source_rgb = lambda *_: None
    native.move_to = lambda *_: None
    native.layout_path = lambda *_: events.append("layout_path")
    native.path_extents = path_extents
    native.fill = lambda *_: events.append("fill")
    native.version_string = lambda: b"MOCK"
    return native


class MockCairoOwnershipTests(unittest.TestCase):
    def test_success_destroys_context_before_finish_then_surface(self):
        events = []
        native = fake_cairo(events)
        raw, bounds, _ = native.outlines(777, 640, 360, [0, 0], [1, 1, 1])
        self.assertEqual(raw, SVG)
        self.assertEqual(bounds, [1, 2, 3, 4])
        self.assertEqual(events, ["surface", "layout_path", "fill", "destroy_context",
                                  "finish", "destroy_surface"])

    def test_surface_failure_still_destroys_only_surface(self):
        events = []
        with self.assertRaisesRegex(vector.Rejected, "surface_failed"):
            fake_cairo(events, surface_error=True).outlines(777, 640, 360, [0, 0], [1, 1, 1])
        self.assertEqual(events, ["surface", "destroy_surface"])

    def test_context_failure_destroys_both_without_path_or_finish(self):
        events = []
        with self.assertRaisesRegex(vector.Rejected, "context_failed"):
            fake_cairo(events, context_error=True).outlines(777, 640, 360, [0, 0], [1, 1, 1])
        self.assertEqual(events, ["surface", "destroy_context", "destroy_surface"])

    def test_output_limit_failure_preserves_cleanup(self):
        events = []
        native = fake_cairo(events, raw=b"x" * (vector.MAX_LABEL_SVG_BYTES + 1))
        with self.assertRaisesRegex(vector.Rejected, "output_budget"):
            native.outlines(777, 640, 360, [0, 0], [1, 1, 1])
        self.assertEqual(events[-3:], ["destroy_context", "finish", "destroy_surface"])


def fake_pango(events):
    from manim_graph_font_probe import NativePango
    native = object.__new__(NativePango)
    native.config_new = lambda: 101
    native.config_free = lambda value: events.append(("config_free", value))
    native.add_font = lambda *_: 1
    native.map_new = lambda *_: 102
    native.map_config = lambda *_: None
    native.context_new = lambda *_: 103
    native.layout_new = lambda *_: 104
    native.unref = lambda value: events.append(("unref", value))
    native.description_new = lambda: 105
    native.description_free = lambda value: events.append(("description_free", value))
    for name in ("description_family", "description_weight", "description_style",
                 "description_size", "description_variations", "layout_description",
                 "layout_width", "layout_ellipsize", "layout_paragraph",
                 "attributes_insert", "layout_attributes", "layout_text"):
        setattr(native, name, lambda *_: None)
    native.attributes_new = lambda: 106
    native.attributes_free = lambda value: events.append(("attributes_free", value))
    native.attribute_fallback = lambda *_: 108
    native.layout_get_text = lambda *_: b"A"
    native.unknown_glyphs = lambda *_: 0
    native.layout_extents = lambda *_: None
    native.iterator_new = lambda *_: 107
    native.iterator_free = lambda value: events.append(("iterator_free", value))
    native.iterator_run = lambda *_: None
    native.iterator_next = lambda *_: 0
    native.pango_version_string = lambda: b"MOCK"
    native.fc_version = lambda: 0
    return native


class MockPangoLayoutLifetimeTests(unittest.TestCase):
    def test_consumer_runs_before_layout_map_and_config_are_freed(self):
        events = []
        native = fake_pango(events)

        def consume(layout, observation):
            self.assertEqual(layout, 104)
            self.assertEqual(events, [])
            self.assertEqual(observation["pango_version"], "MOCK")
            events.append(("consume", layout))

        native.observe(["/mock/a.woff2"], "Instrument Sans", b"A", 20.0, True,
                       consume_layout=consume)
        self.assertEqual(events[0], ("consume", 104))
        self.assertLess(events.index(("unref", 104)), events.index(("unref", 102)))
        self.assertEqual(events[-1], ("config_free", 101))

    def test_consumer_failure_propagates_after_native_lifetimes_are_released(self):
        events = []
        native = fake_pango(events)

        def consume(_layout, _observation):
            self.assertEqual(events, [])
            raise vector.Rejected("mock_vector_failure")

        with self.assertRaisesRegex(vector.Rejected, "mock_vector_failure"):
            native.observe(["/mock/a.woff2"], "Instrument Sans", b"A", 20.0, True,
                           consume_layout=consume)
        self.assertIn(("unref", 104), events)
        self.assertEqual(events[-1], ("config_free", 101))


if __name__ == "__main__":
    unittest.main()
