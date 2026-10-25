"""Mock-only Manim import tests. No Manim, Cairo, Pango or font is loaded.

The mock exercises the actual Graph wrapper's parameters, pivot, custody and
rejection paths. A real displaced-SVG/baseline probe in the sealed Linux runtime
is a separate mandatory G1 gate; these tests cannot satisfy it.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from manim_graph_font_probe import Rejected

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="640px" height="360px" '
       b'viewBox="0 0 640 360" version="1.1"><path '
       b'd="M 47.5 31.25 L 60 31.25 L 60 56.25 L 47.5 56.25 Z" '
       b'style="fill:rgb(100%,100%,100%);stroke:none"/></svg>')
SOURCE_NAME = "mw_" + "a" * 32 + ".py"


class MockSvgMobject:
    behavior = "normal"
    created = 0

    def __init__(self, **kwargs):
        type(self).created += 1
        self.parent_parameters = kwargs
        self.points = [[47.5, 31.25, 0.0], [60.0, 31.25, 0.0],
                       [60.0, 56.25, 0.0], [47.5, 56.25, 0.0]]
        if self.behavior == "missing_flip":
            return
        self.flip((0, 1, 0) if self.behavior == "wrong_axis" else (1, 0, 0))
        if self.behavior == "double_flip":
            self.flip((1, 0, 0))
        if self.behavior == "nonplanar":
            self.points[0][2] = 1.0
        if self.behavior == "empty":
            self.points = []

    def apply_matrix(self, matrix, *, about_point):
        if tuple(about_point) != (0, 0, 0):
            raise AssertionError("mock observed an implicit non-origin pivot")
        self.points = [[sum(matrix[row][column] * point[column] for column in range(3))
                        for row in range(3)] for point in self.points]
        return self

    def flip(self, axis, *, about_point=None, about_edge=None):
        self.delegated_flip = (axis, about_point, about_edge)
        return self

    def shift(self, vector):
        self.points = [[value + offset for value, offset in zip(point, vector)]
                       for point in self.points]
        return self

    def get_all_points(self):
        return self.points


def import_with_mock_manim():
    fake = types.ModuleType("manim")
    fake.__version__ = "0.21.0"
    fake.ORIGIN = (0, 0, 0)
    fake.RIGHT = (1, 0, 0)
    fake.UP = (0, 1, 0)
    fake.SVGMobject = MockSvgMobject
    spec = importlib.util.spec_from_file_location(
        "motionwright_graph_svg_mock_under_test", HERE / "manim_graph_svg_import.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"manim": fake}):
        spec.loader.exec_module(module)
    return module


IMPORT = import_with_mock_manim()


def vector_report():
    return {"svg_sha256": hashlib.sha256(SVG).hexdigest(), "svg_bytes": len(SVG)}


class MockOriginImportTests(unittest.TestCase):
    def setUp(self):
        IMPORT.GraphOutlineMobject.behavior = "normal"
        IMPORT.GraphOutlineMobject.created = 0

    def test_wrapper_passes_no_automatic_scale_center_or_cache(self):
        item = IMPORT.GraphOutlineMobject(Path("mock-only.svg"), name="mock")
        self.assertEqual(item.parent_parameters, {
            "file_name": Path("mock-only.svg"), "should_center": False,
            "height": None, "width": None, "use_svg_cache": False,
            "stroke_width": 0, "name": "mock",
        })
        self.assertEqual(item.points, [[47.5, -31.25, 0], [60, -31.25, 0],
                                       [60, -56.25, 0], [47.5, -56.25, 0]])
        self.assertFalse(item._graph_importing_svg)

    def test_wrong_version_stops_before_parent_constructor(self):
        with mock.patch.object(IMPORT, "MANIM_VERSION", "0.22.0"):
            with self.assertRaisesRegex(Rejected, "version_mismatch"):
                IMPORT.GraphOutlineMobject(Path("mock-only.svg"), name="mock")
        self.assertEqual(IMPORT.GraphOutlineMobject.created, 0)

    def test_changed_flip_contract_is_rejected(self):
        for behavior in ("missing_flip", "wrong_axis", "double_flip"):
            IMPORT.GraphOutlineMobject.behavior = behavior
            with self.subTest(behavior=behavior), self.assertRaisesRegex(Rejected, "flip"):
                IMPORT.GraphOutlineMobject(Path("mock-only.svg"), name="mock")

    def test_flip_after_import_retains_parent_behavior(self):
        item = IMPORT.GraphOutlineMobject(Path("mock-only.svg"), name="mock")
        item.flip((0, 1, 0), about_point=(1, 2, 3))
        self.assertEqual(item.delegated_flip, ((0, 1, 0), (1, 2, 3), None))

    def test_camera_mapping_rejects_fractional_pixels_center_rate_and_scale(self):
        camera = types.SimpleNamespace(
            pixel_width=640, pixel_height=360, frame_width=640.0,
            frame_height=360.0, frame_rate=30.0, frame_center=[0.0, 0.0, 0.0],
            cairo_line_width_multiple=1.0)
        self.assertEqual(IMPORT.require_pixel_camera(camera, 640, 360, 30)["frame_width"], 640)
        for field, wrong in [("pixel_width", 640.5), ("frame_width", 14.2222),
                             ("frame_center", [1, 0, 0]), ("frame_rate", 29.97),
                             ("cairo_line_width_multiple", 0.01)]:
            old = getattr(camera, field)
            setattr(camera, field, wrong)
            with self.subTest(field=field), self.assertRaisesRegex(Rejected, "mapping_mismatch"):
                IMPORT.require_pixel_camera(camera, 640, 360, 30)
            setattr(camera, field, old)


class MockSvgCustodyTests(unittest.TestCase):
    def setUp(self):
        IMPORT.GraphOutlineMobject.behavior = "normal"
        IMPORT.GraphOutlineMobject.created = 0
        # Keep fixtures next to the reviewed source, never in the system
        # temporary root.
        self.temporary = tempfile.TemporaryDirectory(prefix="graph-svg-mock-", dir=HERE)
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def test_directory_creation_is_exclusive_and_does_not_reuse_unknown_attempt(self):
        source = self.root / SOURCE_NAME
        source.write_bytes(b"# controlled mock source\n")
        with mock.patch.object(IMPORT.Path, "cwd", return_value=self.root):
            directory = IMPORT.create_label_directory(source)
            with self.assertRaises(FileExistsError):
                IMPORT.create_label_directory(source)
        self.assertTrue(directory.is_dir())

    def test_fixed_editable_source_name_keeps_exclusive_attempt_custody(self):
        source = self.root / "scene.py"
        source.write_bytes(b"# controlled source package, not native execution\n")
        with mock.patch.object(IMPORT.Path, "cwd", return_value=self.root):
            directory = IMPORT.create_label_directory(source)
            self.assertEqual(directory.name, "scene.graph-labels")
            with self.assertRaises(FileExistsError):
                IMPORT.create_label_directory(source)
        self.assertTrue(directory.is_dir())
        self.assertTrue(source.is_file())

    def test_non_generated_source_name_is_rejected_before_creating_directory(self):
        source = self.root / "user.py"
        source.write_bytes(b"# controlled mock source\n")
        with mock.patch.object(IMPORT.Path, "cwd", return_value=self.root):
            with self.assertRaisesRegex(Rejected, "generated_job_name"):
                IMPORT.create_label_directory(source)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["user.py"])

    def test_mock_import_preserves_bytes_and_exact_viewport_translation(self):
        item, result = IMPORT.import_vector_label(self.root, 0, SVG, vector_report(), (640, 360))
        expected = [[-272.5, 148.75, 0], [-260, 148.75, 0],
                    [-260, 123.75, 0], [-272.5, 123.75, 0]]
        self.assertEqual(item.points, expected)
        self.assertEqual((self.root / "label_0000.svg").read_bytes(), SVG)
        digest = hashlib.sha256(b"".join(struct.pack("<ddd", *point) for point in expected)).hexdigest()
        self.assertEqual(result["points_sha256"], digest)
        self.assertFalse(result["control_bounds_are_glyph_ink_bounds"])
        self.assertFalse(result["manim_frame_verified"])
        self.assertFalse(result["native_reopen_verified"])
        self.assertFalse(result["profile_admitted"])

    def test_digest_mismatch_rejects_before_any_file_or_manim_construction(self):
        report = vector_report()
        report["svg_sha256"] = "00" * 32
        with self.assertRaisesRegex(Rejected, "result_binding"):
            IMPORT.import_vector_label(self.root, 0, SVG, report, (640, 360))
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(IMPORT.GraphOutlineMobject.created, 0)

    def test_reused_label_attempt_does_not_run_manim_again(self):
        IMPORT.import_vector_label(self.root, 0, SVG, vector_report(), (640, 360))
        with self.assertRaisesRegex(Rejected, "already_exists"):
            IMPORT.import_vector_label(self.root, 0, SVG, vector_report(), (640, 360))
        self.assertEqual(IMPORT.GraphOutlineMobject.created, 1)
        self.assertEqual((self.root / "label_0000.svg").read_bytes(), SVG)

    def test_preexisting_parser_temp_is_preserved_and_rejected(self):
        partial = self.root / "label_0000_.svg"
        partial.write_bytes(b"partial")
        with self.assertRaisesRegex(Rejected, "already_exists"):
            IMPORT.import_vector_label(self.root, 0, SVG, vector_report(), (640, 360))
        self.assertEqual(partial.read_bytes(), b"partial")
        self.assertEqual(IMPORT.GraphOutlineMobject.created, 0)

    def test_nonplanar_or_empty_mock_output_fails_but_keeps_source_for_custody(self):
        for ordinal, behavior in enumerate(("nonplanar", "empty")):
            IMPORT.GraphOutlineMobject.behavior = behavior
            with self.subTest(behavior=behavior), self.assertRaisesRegex(Rejected, "point"):
                IMPORT.import_vector_label(self.root, ordinal, SVG, vector_report(), (640, 360))
            self.assertEqual((self.root / f"label_{ordinal:04d}.svg").read_bytes(), SVG)


if __name__ == "__main__":
    unittest.main()
