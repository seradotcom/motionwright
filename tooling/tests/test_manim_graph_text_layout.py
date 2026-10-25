"""Pure contract regressions with explicit Pango/Cairo doubles; no native proof."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import manim_graph_text_layout as LAYOUT
from manim_graph_font_probe import Rejected

BOX = {"x": 10.0, "y": 20.0, "width": 100.0, "height": 96.0}
FONT = "/controlled-readonly-mock/font.woff2"
ALLOWED = {FONT: {"relative_path": "font.woff2", "sha256": "ab" * 32}}
FAMILY = "Instrument Sans"


def svg(path="M 10 20 L 20 20 L 20 30 Z", extra=""):
    return (f'<svg xmlns="{LAYOUT.SVG_NS}" width="640px" height="360px" '
            f'viewBox="0 0 640 360"><g>{extra}<path d="{path}"/></g></svg>').encode()


class MockPango:
    def __init__(self):
        self.live = False
        self.texts = []
        self.changes = {}
        self.observe_mode = "normal"
        self.line_count_value = 1
        self.wrapped = False
        self.ellipsized = False
        self.current = None
        self.callback_count = 0

    def require_live(self, layout):
        if not self.live or layout != "controlled-live-layout":
            raise AssertionError("Mock getter used outside live layout lifetime")

    def line_count(self, layout):
        self.require_live(layout)
        return self.line_count_value

    def is_wrapped(self, layout):
        self.require_live(layout)
        return self.wrapped

    def is_ellipsized(self, layout):
        self.require_live(layout)
        return self.ellipsized

    def signature(self, layout):
        self.require_live(layout)
        return copy.deepcopy(self.current)

    def observe(self, paths, family, text, font_size, variable, *, consume_layout):
        self.texts.append(text)
        assert paths == [FONT] and family == FAMILY and font_size == 20 and variable
        advance = len(text.decode()) * 10
        visible = bool(text.strip())
        self.current = {
            "text": text, "unknown": 0,
            "logical": [0, 0, advance * 1024, 1000 * 1024],
            "ink": [0, 3 * 1024, advance * 1024, 10 * 1024] if visible else [0, 0, 0, 0],
            "baseline": 15 * 1024,
        }
        self.current.update(copy.deepcopy(self.changes.get("signature", {})))
        runs = [{
            "offset": 0, "length": len(text), "num_chars": len(text.decode()),
            "family": FAMILY, "file": FONT, "face_index": 0,
            "weight": 400, "style": 0,
        }] if text else []
        if runs:
            runs[0].update(self.changes.get("run", {}))
        observation = {
            "pango_version": "MOCK-NOT-NATIVE", "fontconfig_version": 0,
            "unknown_glyph_count": self.current["unknown"], "runs": runs,
            "ink_pango_units": list(self.current["ink"]),
            "logical_pango_units": list(self.current["logical"]),
        }
        self.live = True
        try:
            if self.observe_mode != "skip":
                self.callback_count += 1
                consume_layout("controlled-live-layout", observation)
            if self.observe_mode == "double":
                self.callback_count += 1
                consume_layout("controlled-live-layout", observation)
        finally:
            self.live = False
        return observation


class NativeLayoutMockTests(unittest.TestCase):
    def setUp(self):
        self.native = MockPango()
        self.vector_calls = []
        self.outline_override = None
        self.signature_patch = mock.patch.object(
            LAYOUT, "_native_layout_signature",
            side_effect=lambda native, layout: native.signature(layout))
        self.signature_patch.start()
        self.addCleanup(self.signature_patch.stop)
        self.vector_patch = mock.patch.object(
            LAYOUT, "vectorize_observed_layout", side_effect=self.vectorize)
        self.vector_patch.start()
        self.addCleanup(self.vector_patch.stop)

    def vectorize(self, native, layout, observation, allowed, family, text,
                  document_size, origin, color):
        native.require_live(layout)
        self.vector_calls.append((text, tuple(origin), color))
        ink = native.current["ink"]
        bounds = [origin[0] + ink[0] / 1024, origin[1] + ink[1] / 1024,
                  origin[0] + (ink[0] + ink[2]) / 1024,
                  origin[1] + (ink[1] + ink[3]) / 1024]
        if self.outline_override is not None:
            bounds = self.outline_override
        raw = svg(f"M {bounds[0]} {bounds[1]} L {bounds[2]} {bounds[1]} L {bounds[2]} {bounds[3]} Z")
        return raw, {
            "svg_sha256": hashlib.sha256(raw).hexdigest(), "svg_bytes": len(raw),
            "baseline_px": origin[1] + native.current["baseline"] / 1024,
            "outline_bounds_px": bounds,
        }

    def run_block(self, text, *, box=None):
        return LAYOUT.layout_text_block(
            self.native, ALLOWED, FAMILY, "Instrument Sans Variable", text,
            20, box or BOX, (640, 360), "#123456", "graph-edge:mock:mock")

    def test_literal_spaces_empty_lines_and_final_lf_have_fixed_baselines(self):
        raw, report, originals = self.run_block(" A \n\nB\n")
        self.assertEqual(self.native.texts, [b" A ", b"", b"B", b""])
        self.assertEqual([row["text"] for row in report["lines"]], [" A ", "", "B", ""])
        self.assertEqual([row["placement"]["baseline_px"] for row in report["lines"]],
                         [39.0, 63.0, 87.0, 111.0])
        self.assertEqual(report["literal_line_count"], 4)
        for row in report["lines"]:
            self.assertEqual(row["native_layout"]["unknown_glyph_count"], 0)
            self.assertEqual(row["native_layout"]["line_count"], 1)
            self.assertIs(row["native_layout"]["is_wrapped"], False)
            self.assertIs(row["native_layout"]["is_ellipsized"], False)
            self.assertEqual(len(row["native_layout"]["logical_pango_units"]), 4)
        self.assertEqual(len(originals), 2)
        self.assertEqual(len(self.vector_calls), 2)
        self.assertIsNotNone(raw)

    def test_pango_logical_height_is_not_substituted_for_a3_line_height(self):
        _raw, report, _originals = self.run_block("A")
        self.assertEqual(report["lines"][0]["placement"]["line_height_px"], 24.0)
        self.assertTrue(report["lines"][0]["has_ink"])

    def test_whitespace_advance_is_kept_without_inventing_an_outline(self):
        raw, report, originals = self.run_block("   ")
        self.assertIsNone(raw)
        self.assertEqual(originals, [])
        self.assertEqual(self.vector_calls, [])
        self.assertEqual(report["lines"][0]["placement"]["advance_px"], 30.0)
        self.assertEqual(report["lines"][0]["placement"]["line_start_x_px"], 45.0)
        self.assertEqual(report["lines"][0]["runs"][0]["length"], 3)
        self.assertFalse(report["lines"][0]["has_ink"])

    def test_empty_line_occupies_the_authored_line_box_without_fake_glyphs(self):
        raw, report, originals = self.run_block("")
        self.assertIsNone(raw)
        self.assertEqual(report["literal_line_count"], 1)
        self.assertEqual(report["lines"][0]["placement"]["baseline_px"], 75.0)
        self.assertEqual(report["lines"][0]["runs"], [])
        self.assertEqual(originals, [])

    def test_aggregate_height_overflow_rejects_before_native_observation(self):
        with self.assertRaisesRegex(Rejected, "line_height_overflow"):
            self.run_block("A\nB\nC\nD\nE")
        self.assertEqual(self.native.texts, [])
        self.assertEqual(self.vector_calls, [])

    def test_advance_overflow_rejects_without_scaling_or_clipping(self):
        with self.assertRaisesRegex(Rejected, r"graph-edge:mock:mock:line:0:.*advance_overflow"):
            self.run_block("12345678901")
        self.assertEqual(self.vector_calls, [])

    def test_ink_overflow_is_rejected_even_when_advance_fits(self):
        self.native.changes = {"signature": {"ink": [-100 * 1024, 3 * 1024, 120 * 1024, 10 * 1024]}}
        with self.assertRaisesRegex(Rejected, "native_ink_overflow"):
            self.run_block("A")
        self.assertEqual(self.vector_calls, [])

    def test_native_cairo_outline_overflow_is_checked_after_pango_ink(self):
        self.outline_override = [-1.0, 50.0, 20.0, 60.0]
        with self.assertRaisesRegex(Rejected, "native_outline_overflow"):
            self.run_block("A")
        self.assertEqual(len(self.vector_calls), 1)

    def test_unknown_glyph_count_rejects_before_vectorization(self):
        self.native.changes = {"signature": {"unknown": 1}}
        with self.assertRaisesRegex(Rejected, "live_layout_changed"):
            self.run_block("A")
        self.assertEqual(self.vector_calls, [])

    def test_homonymous_system_font_is_rejected_by_actual_run_path(self):
        self.native.changes = {"run": {"file": "/system/not-the-bundled-font.woff2"}}
        with self.assertRaisesRegex(Rejected, "font_file_substitution"):
            self.run_block("A")
        self.assertEqual(self.vector_calls, [])

    def test_synthesized_weight_is_rejected(self):
        self.native.changes = {"run": {"weight": 700}}
        with self.assertRaisesRegex(Rejected, "font_face_substitution"):
            self.run_block("A")

    def test_native_wrapping_and_ellipsis_are_explicit_failures(self):
        for attribute in ("wrapped", "ellipsized"):
            with self.subTest(attribute=attribute):
                setattr(self.native, attribute, True)
                with self.assertRaisesRegex(Rejected, "implicit_line_change"):
                    self.run_block("A")
                setattr(self.native, attribute, False)
        self.assertEqual(self.vector_calls, [])

    def test_other_unicode_line_separator_cannot_silently_add_native_lines(self):
        self.native.line_count_value = 2
        with self.assertRaisesRegex(Rejected, "implicit_line_change"):
            self.run_block("A\u2028B")
        self.assertEqual(self.native.texts, ["A\u2028B".encode()])

    def test_cr_and_tab_are_rejected_without_normalization(self):
        for text in ("A\r\nB", "A\tB"):
            with self.subTest(text=text), self.assertRaisesRegex(Rejected, "control_character"):
                self.run_block(text)
        self.assertEqual(self.native.texts, [])

    def test_missing_live_callback_cannot_be_replaced_by_a_report(self):
        self.native.observe_mode = "skip"
        with self.assertRaisesRegex(Rejected, "not_consumed"):
            self.run_block("A")

    def test_repeated_live_callback_fails_without_second_vectorization(self):
        self.native.observe_mode = "double"
        with self.assertRaisesRegex(Rejected, "consumed_twice"):
            self.run_block("A")
        self.assertEqual(len(self.vector_calls), 1)
        self.assertFalse(self.native.live)

    def test_native_layout_cleanup_runs_when_cairo_or_fit_rejects(self):
        with mock.patch.object(LAYOUT, "vectorize_observed_layout",
                               side_effect=Rejected("controlled_vector_failure")):
            with self.assertRaisesRegex(Rejected, "controlled_vector_failure"):
                self.run_block("A")
        self.assertFalse(self.native.live)

    def test_original_line_svg_sum_is_bounded_before_next_line_or_composite(self):
        raw_constant = svg()
        def constant_svg(*args):
            _raw, report = self.vectorize(*args)
            report["svg_sha256"] = hashlib.sha256(raw_constant).hexdigest()
            report["svg_bytes"] = len(raw_constant)
            return raw_constant, report
        with mock.patch.object(LAYOUT, "vectorize_observed_layout", side_effect=constant_svg):
            with mock.patch.object(LAYOUT, "MAX_LABEL_SVG_BYTES", len(raw_constant) + 1):
                with self.assertRaisesRegex(Rejected, "original_line_svg_budget"):
                    self.run_block("A\nB\nC")
        self.assertEqual(self.native.texts, [b"A", b"B"])
        self.assertEqual(len(self.vector_calls), 2)
        self.assertFalse(self.native.live)

    def test_report_has_literal_digest_and_no_frame_reopen_or_admission_credit(self):
        raw, report, originals = self.run_block(" A ")
        self.assertEqual(report["text_sha256"], hashlib.sha256(b" A ").hexdigest())
        self.assertEqual(report["svg_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report["original_line_svgs"][0]["sha256"], hashlib.sha256(originals[0]).hexdigest())
        for key in ("automatic_wrap", "automatic_scale", "clipping", "fallback_allowed",
                    "manim_frame_verified", "native_reopen_verified", "profile_admitted", "human_acceptance"):
            self.assertIs(report[key], False)
        self.assertFalse(self.native.live)


class PlacementAndCompositionTests(unittest.TestCase):
    def test_advance_centering_does_not_center_ink_or_replace_baseline(self):
        value = LAYOUT.line_placement(BOX, 20, 1, 0, [2 * 1024, -900 * 1024, 40 * 1024, 1000 * 1024], 15 * 1024)
        self.assertEqual(value["line_start_x_px"], 40.0)
        self.assertEqual(value["layout_origin_px"], [38.0, 60.0])
        self.assertEqual(value["baseline_px"], 75.0)

    def test_border_fit_is_inclusive_and_no_tolerance_extends_authored_box(self):
        LAYOUT._inside([10.0, 20.0, 110.0, 116.0], BOX, "overflow")
        with self.assertRaisesRegex(Rejected, "overflow"):
            LAYOUT._inside([10.0, 20.0, 110.0000000001, 116.0], BOX, "overflow")

    def test_combining_lines_renames_ids_and_local_refs_without_geometry_change(self):
        extra = '<defs><clipPath id="clip1"><path d="M 0 0 L 1 0 L 1 1 Z"/></clipPath></defs><g clip-path="url(#clip1)"><path d="M 4 5 L 6 5 L 6 7 Z"/></g>'
        original = svg(extra=extra)
        combined = LAYOUT.combine_outline_svgs([original, original], 640, 360)
        root = ET.fromstring(combined)
        ids = [item.get("id") for item in root.iter() if item.get("id")]
        refs = [item.get("clip-path") for item in root.iter() if item.get("clip-path")]
        self.assertEqual(ids, ["line_0000_clip1", "line_0001_clip1"])
        self.assertEqual(refs, ["url(#line_0000_clip1)", "url(#line_0001_clip1)"])
        self.assertEqual([item.get("d") for item in root.iter() if item.tag.endswith("}path")].count("M 4 5 L 6 5 L 6 7 Z"), 2)
        self.assertFalse(any("transform" in item.attrib for item in root.iter()))

    def test_duplicate_native_ids_and_unresolved_refs_are_rejected(self):
        for extra, reason in [
            ('<g id="x"/><g id="x"/>', "duplicate_id"),
            ('<g clip-path="url(#missing)"/>', "missing_local_target"),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(Rejected, reason):
                LAYOUT.combine_outline_svgs([svg(extra=extra)], 640, 360)

    def test_raster_external_or_font_text_cannot_enter_composite(self):
        for extra in ('<image/>', '<text>A</text>', '<g style="url(https://invalid.example/x)"/>'):
            with self.subTest(extra=extra), self.assertRaises(Rejected):
                LAYOUT.combine_outline_svgs([svg(extra=extra)], 640, 360)

    def test_composite_bound_is_checked_without_truncating_originals(self):
        original = svg()
        digest = hashlib.sha256(original).hexdigest()
        with mock.patch.object(LAYOUT, "MAX_LABEL_SVG_BYTES", 10):
            with self.assertRaisesRegex(Rejected, "composite_svg_budget"):
                LAYOUT.combine_outline_svgs([original], 640, 360)
        self.assertEqual(hashlib.sha256(original).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
