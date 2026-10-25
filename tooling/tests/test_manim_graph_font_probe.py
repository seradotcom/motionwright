"""Pure rejection/ownership tests. Every Pango observation here is a MOCK.

These tests never load native libraries and do not prove font support, glyph
coverage, layout fidelity, sandbox support, a Manim frame, or profile admission.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import manim_graph_font_probe as probe


MOCK_FILE = "/mock-font-mount/instrument-sans/files/mock.woff2"
MOCK_DESCRIPTOR = {
    "relative_path": "instrument-sans/files/mock.woff2",
    "sha256": "ab" * 32,
    "size_bytes": 123,
}
ALLOWED = {MOCK_FILE: MOCK_DESCRIPTOR}


def mock_run(offset=0, length=1, characters=1):
    return {
        "offset": offset, "length": length, "num_chars": characters,
        "family": "Instrument Sans", "file": MOCK_FILE,
        "face_index": 0, "weight": 400, "style": 0,
    }


def mock_observation():
    return {
        "unknown_glyph_count": 0,
        "runs": [mock_run(0), mock_run(2)],
    }


class MockGetterValidationTests(unittest.TestCase):
    def validate(self, value, raw=b"A\nB"):
        return probe.validate_observation(value, ALLOWED, "Instrument Sans", raw)

    def test_mock_valid_runs_preserve_explicit_newline_and_exact_resource(self):
        rows = self.validate(mock_observation())
        self.assertEqual([row["offset"] for row in rows], [0, 2])
        self.assertTrue(all(row["font_sha256"] == "ab" * 32 for row in rows))
        self.assertNotIn("profile_admitted", rows[0])
        self.assertNotIn("file", rows[0])

    def test_mock_utf8_run_counts_characters_and_bytes_separately(self):
        value = {"unknown_glyph_count": 0, "runs": [mock_run(0, 2, 1)]}
        rows = self.validate(value, "\u00e9".encode("utf-8"))
        self.assertEqual(rows[0]["length"], 2)
        self.assertEqual(rows[0]["num_chars"], 1)

    def test_mock_unknown_glyph_is_rejected(self):
        value = mock_observation()
        value["unknown_glyph_count"] = 1
        with self.assertRaisesRegex(probe.Rejected, "native_unknown_glyphs"):
            self.validate(value)

    def test_mock_family_substitution_is_rejected(self):
        value = mock_observation()
        value["runs"][0]["family"] = "Instrument Sans Variable"
        with self.assertRaisesRegex(probe.Rejected, "native_font_family_substitution"):
            self.validate(value)

    def test_mock_system_font_file_is_rejected(self):
        value = mock_observation()
        value["runs"][0]["file"] = "/usr/share/fonts/system.ttf"
        with self.assertRaisesRegex(probe.Rejected, "native_font_file_substitution"):
            self.validate(value)

    def test_mock_face_weight_and_style_substitution_are_rejected(self):
        for field, replacement in (("weight", 700), ("style", 2), ("face_index", 65536)):
            value = mock_observation()
            value["runs"][0][field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(probe.Rejected, "native_font_face_substitution"):
                self.validate(value)

    def test_mock_missing_text_is_rejected(self):
        value = mock_observation()
        value["runs"].pop()
        with self.assertRaisesRegex(probe.Rejected, "native_text_not_fully_itemized"):
            self.validate(value)

    def test_mock_overlapping_runs_are_rejected(self):
        value = mock_observation()
        value["runs"].append(copy.deepcopy(value["runs"][0]))
        with self.assertRaisesRegex(probe.Rejected, "native_overlapping_runs"):
            self.validate(value)

    def test_mock_out_of_bounds_run_is_rejected(self):
        value = mock_observation()
        value["runs"][0]["length"] = 4
        with self.assertRaisesRegex(probe.Rejected, "native_run_range"):
            self.validate(value)

    def test_mock_character_count_cannot_replace_glyph_coverage(self):
        value = mock_observation()
        value["runs"][0]["num_chars"] = 2
        with self.assertRaisesRegex(probe.Rejected, "native_run_character_count"):
            self.validate(value)

    def test_mock_empty_run_inventory_is_rejected(self):
        with self.assertRaisesRegex(probe.Rejected, "native_run_count"):
            self.validate({"unknown_glyph_count": 0, "runs": []})

    def test_mock_invalid_utf8_boundary_is_rejected(self):
        value = {"unknown_glyph_count": 0, "runs": [mock_run(1)]}
        with self.assertRaises(UnicodeDecodeError):
            self.validate(value, "\u00e9".encode("utf-8"))


class ClosedRuntimeBoundaryTests(unittest.TestCase):
    def test_non_linux_rejects_without_loading_a_library(self):
        with mock.patch.object(probe.sys, "platform", "win32"):
            with mock.patch.object(probe.C, "CDLL") as loader:
                with self.assertRaisesRegex(probe.Rejected, "font_probe_requires_linux_runtime"):
                    probe.NativePango()
                loader.assert_not_called()

    def test_missing_runtime_api_is_rejected_without_fallback(self):
        with mock.patch.object(probe.sys, "platform", "linux"):
            with mock.patch.object(probe.C, "CDLL", side_effect=OSError("mock unavailable")) as loader:
                with self.assertRaisesRegex(probe.Rejected, "native_font_api_unavailable"):
                    probe.NativePango()
                self.assertEqual(loader.call_count, 1)

    def test_invalid_source_text_and_size_are_rejected_before_native_loading(self):
        for text, size in (("", 20), ("A\0B", 20), ("A\tB", 20),
                           ("A" * (probe.MAX_TEXT_BYTES + 1), 20),
                           ("A", float("nan")), ("A", True), ("A", 3.99), ("A", 513)):
            with self.subTest(size=size, text_size=len(text)):
                with mock.patch.object(probe, "NativePango") as native:
                    with self.assertRaises(probe.Rejected):
                        probe.probe(HERE, HERE, {}, "Instrument Sans Variable", text, size)
                    native.assert_not_called()

    def test_mock_failed_font_open_releases_the_config_once(self):
        native = object.__new__(probe.NativePango)
        calls = []
        native.config_new = lambda: 101
        native.config_free = lambda pointer: calls.append(("free_config", pointer))
        native.add_font = lambda _config, _path: 0
        with self.assertRaisesRegex(probe.Rejected, "native_woff2_open_failed"):
            native.observe(["/mock-font"], "Instrument Sans", b"A", 20, True)
        self.assertEqual(calls, [("free_config", 101)])

    def test_mock_failed_context_releases_map_before_config(self):
        native = object.__new__(probe.NativePango)
        calls = []
        native.config_new = lambda: 101
        native.config_free = lambda pointer: calls.append(("free_config", pointer))
        native.add_font = lambda _config, _path: 1
        native.map_new = lambda _kind: 202
        native.unref = lambda pointer: calls.append(("unref", pointer))
        native.map_config = lambda _map, _config: None
        native.context_new = lambda _map: 0
        with self.assertRaisesRegex(probe.Rejected, "native_pango_context_failed"):
            native.observe(["/mock-font"], "Instrument Sans", b"A", 20, True)
        self.assertEqual(calls, [("unref", 202), ("free_config", 101)])


if __name__ == "__main__":
    unittest.main()
