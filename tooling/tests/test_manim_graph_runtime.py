"""Explicit mock regressions for the fixed Graph runtime, never a native gate."""
from __future__ import annotations

from contextlib import ExitStack
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "tooling" / "manim_graph_runtime.py"


class Rejected(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


class Color:
    def __init__(self, value):
        self.value = value

    def to_hex(self):
        return self.value


class Object:
    def __init__(self, *vertices, name="object", **kwargs):
        self.name, self.z_index = name, 0
        self.points = [list(row) for row in vertices] or [[0.0, 0.0, 0.0]]
        self.fill, self.stroke, self.width = 0.0, 0.0, 0.0
        self.fill_color, self.stroke_color = Color("#000000"), Color("#000000")
        self.children = []
        self.joint_type = kwargs.get("joint_type")
        self.cap_style = kwargs.get("cap_style")

    def set_z_index(self, value):
        self.z_index = value
        return self

    def set_points_as_corners(self, value):
        self.points = value
        return self

    def move_to(self, value):
        self.points = [list(value)]
        return self

    def set_stroke(self, color=None, width=None, opacity=None):
        if color is not None:
            self.stroke_color = Color(color)
        if width is not None:
            self.width = width
        if opacity is not None:
            self.stroke = opacity
        for child in self.children:
            child.set_stroke(color, width, opacity)
        return self

    def set_fill(self, color=None, opacity=None):
        if color is not None:
            self.fill_color = Color(color)
        if opacity is not None:
            self.fill = opacity
        for child in self.children:
            child.set_fill(color, opacity)
        return self

    def get_all_points(self):
        return self.points + [point for child in self.children for point in child.get_all_points()]

    def family_members_with_points(self):
        return ([self] if self.points else []) + [
            item for child in self.children for item in child.family_members_with_points()
        ]

    def get_fill_opacity(self):
        return self.fill

    def get_stroke_opacity(self):
        return self.stroke

    def get_stroke_width(self):
        return self.width

    def get_fill_color(self):
        return self.fill_color

    def get_stroke_color(self):
        return self.stroke_color


class Group(Object):
    def __init__(self, *objects, name="group"):
        super().__init__(name=name)
        self.points, self.children = [], list(objects)


class Scene:
    def __init__(self, count=6, fail_at=None, mutate=None, duplicate_zero=True):
        self.camera = SimpleNamespace(
            frame_width=14.222, frame_height=8.0, frame_center=[1, 2, 3],
            pixel_width=640, pixel_height=360, frame_rate=10,
            cairo_line_width_multiple=0.01, background_opacity=1.0,
            background_image=None, background_color=Color("#000000"),
        )
        self.count, self.fail_at, self.mutate = count, fail_at, mutate
        self.duplicate_zero, self.objects, self.plays = duplicate_zero, [], 0

    def add(self, item):
        self.objects.append(item)

    def play(self, animation):
        self.plays += 1
        if self.duplicate_zero:
            animation.update(animation.item, 0.0)
        for index in range(self.count):
            if self.fail_at == index:
                raise RuntimeError("controlled mock frame failure")
            if self.mutate is not None:
                self.mutate(index, self)
            animation.update(animation.item, index / (self.count - 0.5))
        animation.update(animation.item, 1.0)


def pixel_camera(camera, width, height, fps):
    result = {
        "pixel_width": camera.pixel_width, "pixel_height": camera.pixel_height,
        "frame_width": camera.frame_width, "frame_height": camera.frame_height,
        "frame_rate": camera.frame_rate, "frame_center": list(camera.frame_center),
        "cairo_line_width_multiple": camera.cairo_line_width_multiple,
    }
    require(result == {
        "pixel_width": width, "pixel_height": height, "frame_width": float(width),
        "frame_height": float(height), "frame_rate": fps, "frame_center": [0.0, 0.0, 0.0],
        "cairo_line_width_multiple": 1.0,
    }, "controlled camera getter rejection")
    return result


def create_labels(source):
    target = Path(source).with_suffix(".graph-labels")
    target.mkdir()
    return target


def import_label(directory, ordinal, raw, report, viewport):
    with (directory / f"label_{ordinal:04d}.svg").open("xb") as target:
        target.write(raw)
    return Object(name=f"label-{ordinal}"), {"point_count": 1, "mock_only": True}


def layout(native, selected, family, editorial, text, size, box, viewport, color, resource):
    raw = None if not text.strip(" ") else b"<svg/>"
    return raw, {"mock_only": True, "text": text}, [] if raw is None else [raw]


def load_runtime():
    manim = ModuleType("manim")
    for name in ("Circle", "Polygon", "RoundedRectangle", "VMobject"):
        setattr(manim, name, Object)
    manim.ManimColor, manim.VGroup = Color, Group
    manim.linear = lambda value: value
    manim.UpdateFromAlphaFunc = lambda item, update, **kw: SimpleNamespace(item=item, update=update, **kw)
    constants = ModuleType("manim.constants")
    constants.CapStyleType = SimpleNamespace(BUTT=SimpleNamespace(name="BUTT"))
    constants.LineJointType = SimpleNamespace(MITER=SimpleNamespace(name="MITER"))
    intake = ModuleType("manim_bundled_font_intake")
    intake.MAX_MEMBER = 256 * 1024
    intake.read_owned_file = lambda root, path, limit: Path(path).read_bytes()
    probe = ModuleType("manim_graph_font_probe")
    probe.Rejected, probe.require = Rejected, require
    svg = ModuleType("manim_graph_svg_import")
    svg.create_label_directory, svg.import_vector_label = create_labels, import_label
    svg.require_pixel_camera = pixel_camera
    text = ModuleType("manim_graph_text_layout")
    text.CONTRACT_SHA256 = "f0c7b5a58efed42a32461df4a8cb92bc4cf187f644adad2b76423496dc0b258a"
    text.NativeGraphPango, text.layout_text_block = object, layout
    modules = {
        "manim": manim, "manim.constants": constants,
        "manim_bundled_font_intake": intake, "manim_graph_font_probe": probe,
        "manim_graph_svg_import": svg, "manim_graph_text_layout": text,
    }
    spec = importlib.util.spec_from_file_location("_graph_runtime_mock_test", RUNTIME)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


runtime = load_runtime()


def payload():
    box = {"x": 20.0, "y": 20.0, "width": 80.0, "height": 40.0}
    appearance = {"start_frame": 0, "duration_frames": 1}
    return {
        "schema": "motionwright.manim.graph-native-payload/1",
        "profile_id": "mw-manim-semantic-graph-v1",
        "contract_sha256": runtime.CONTRACT_SHA256,
        "render_profile": {"width": 640, "height": 360, "frame_rate": 10},
        "clock": {"scene_frame_count": 6, "project_start_frame": 100,
                  "graph_start_frame": 1, "graph_frame_count": 4},
        "style": {"font_size": 16.0, "text_color": "#ffffff", "edge_color": "#112233",
                  "edge_width": 2.0, "vertex_fill": "#334455", "accent_color": "#ff0000",
                  "background": "#222222"},
        "typography": {"editorial_family": "Instrument Sans Variable",
                       "native_family": "Instrument Sans"},
        "font_lock": {}, "motion": "full", "scene_matte": "#000000",
        "plan_sha256": "a" * 64, "execution_snapshot": {},
        "graph_id": "graph", "scene_id": "scene",
        "vertices": [{"id": "vertex", "shape": "rectangle", "authored_box": box,
                      "corner_radius": 1.6, "appearance": appearance}],
        "edges": [{"id": "edge", "points": [{"x": 100.0, "y": 40.0}, {"x": 180.0, "y": 40.0}],
                   "arrowhead": [{"x": 182.0, "y": 40.0}, {"x": 162.0, "y": 50.0},
                                 {"x": 162.0, "y": 30.0}],
                   "appearance": {"start_frame": 1, "duration_frames": 1}}],
        "events": [{"id": "event", "edge_id": "edge", "start_frame": 2, "end_frame_exclusive": 3}],
        "text_blocks": [
            {"ordinal": 0, "resource": "edge", "owner": {"kind": "edge", "id": "edge"},
             "text": "edge", "authored_box": box, "active_start_frame": 0,
             "active_end_frame_exclusive": 4},
            {"ordinal": 1, "resource": "vertex", "owner": {"kind": "vertex", "id": "vertex"},
             "text": "vertex", "authored_box": box, "active_start_frame": 0,
             "active_end_frame_exclusive": 4},
            {"ordinal": 2, "resource": "disclosure", "owner": {"kind": "disclosure"},
             "text": "disclosure", "authored_box": box, "active_start_frame": 0,
             "active_end_frame_exclusive": 4},
        ],
    }


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        # All temporary test bytes stay below the repository-owned test cwd.
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / ("mw_" + "a" * 32 + ".py")
        self.source.write_bytes(b"# controlled mock source\n")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(runtime, "verify_fonts", return_value=({}, [])))
        self.unchanged = self.stack.enter_context(mock.patch.object(runtime, "verify_fonts_unchanged"))

    def render(self, scene=None, data=None):
        scene = scene or Scene()
        runtime.render_graph(scene, data or payload(), self.source, self.root, [])
        return scene

    def frames(self):
        return [json.loads(line) for line in self.source.with_suffix(".graph.frames.jsonl").read_bytes().splitlines()]

    def test_budget_exact_boundary_is_allowed_and_next_byte_rejected(self):
        budget = runtime.DerivedBudget(3)
        budget.reserve(3)
        with self.assertRaises(Rejected):
            budget.reserve(1)
        self.assertEqual(budget.used, 3)

    def test_budget_rejects_boolean_and_negative_reservations(self):
        for value in (True, -1, 1.5):
            with self.subTest(value=value), self.assertRaises(Rejected):
                runtime.DerivedBudget(3).reserve(value)

    def test_existing_evidence_is_never_overwritten(self):
        target = self.root / "existing.json"
        target.write_bytes(b"first")
        with self.assertRaises(FileExistsError):
            runtime.write_new(target, b"second", runtime.DerivedBudget())
        self.assertEqual(target.read_bytes(), b"first")

    def test_partial_writes_are_completed_without_duplication(self):
        chunks = []
        sink = SimpleNamespace(write=lambda view: (chunks.append(bytes(view[:2])), min(2, len(view)))[1])
        runtime.write_all(sink, b"abcdefg")
        self.assertEqual(b"".join(chunks), b"abcdefg")

    def test_stalled_write_is_rejected(self):
        with self.assertRaises(Rejected):
            runtime.write_all(SimpleNamespace(write=lambda view: 0), b"payload")

    def test_graph_clock_is_scene_relative_and_half_open(self):
        self.assertEqual([runtime.graph_local_frame(i, 2, 3) for i in range(7)],
                         [None, None, 0, 1, 2, None, None])

    def test_full_motion_has_exact_discrete_appearance(self):
        appearance = {"start_frame": 2, "duration_frames": 4}
        self.assertEqual([runtime.appearance_opacity(appearance, i, "full") for i in range(8)],
                         [0, 0, 0, 0.25, 0.5, 0.75, 1, 1])

    def test_off_motion_preserves_start_boundary_without_fade(self):
        appearance = {"start_frame": 2, "duration_frames": 4}
        self.assertEqual([runtime.appearance_opacity(appearance, i, "off") for i in range(4)], [0, 0, 1, 1])
        self.assertEqual(runtime.appearance_opacity(appearance, None, "off"), 0)

    def test_nonpositive_appearance_duration_rejected(self):
        with self.assertRaises(Rejected):
            runtime.appearance_opacity({"start_frame": 0, "duration_frames": 0}, 0, "full")

    def test_callback_grid_and_terminal_are_distinct(self):
        self.assertEqual([runtime.callback_index(i / 5.5, 6) for i in range(6)], list(range(6)))
        self.assertIsNone(runtime.callback_index(1, 6))

    def test_off_grid_and_nonfinite_callback_rejected(self):
        for alpha in (-0.1, 0.123, 1.1, math.nan, math.inf, True):
            with self.subTest(alpha=alpha), self.assertRaises(Rejected):
                runtime.callback_index(alpha, 6)

    def test_point_observation_rejects_nonplanar_or_nonfinite_coordinates(self):
        for point in ([0, 0, 1], [math.nan, 0, 0]):
            with self.subTest(point=point), self.assertRaises(Rejected):
                runtime.point_observation(Object(point))

    def test_point_observation_is_actual_binary_coordinate_digest(self):
        observed = runtime.point_observation(Object([1.0, 2.0, 0.0]))
        self.assertEqual(observed["point_count"], 1)
        self.assertEqual(observed["control_point_min"], [1.0, 2.0, 0.0])
        self.assertFalse(observed["control_bounds_are_glyph_ink_bounds"])

    def test_changed_native_family_is_rejected(self):
        item = Object()
        item.set_fill("#112233", 0.5)
        with self.assertRaises(Rejected):
            runtime.family_paint_observation(item, "fill", 0.5, "#112233", 0, 2)

    def test_all_native_paint_members_are_checked(self):
        first, second = Object(), Object()
        group = Group(first, second).set_fill("#112233", 0.5)
        second.fill = 0.25
        with self.assertRaises(Rejected):
            runtime.family_paint_observation(group, "fill", 0.5, "#112233", 0, 2)

    def test_stroke_width_mismatch_is_rejected(self):
        item = Object().set_stroke("#112233", width=0.02, opacity=1)
        with self.assertRaises(Rejected):
            runtime.family_paint_observation(item, "stroke", 1, "#112233", 2, 1)

    def test_full_mock_run_observes_half_open_frames_and_terminal(self):
        self.render()
        rows = self.frames()
        self.assertEqual([row["scene_frame"] for row in rows[:-1]], list(range(6)))
        self.assertEqual([row["project_frame"] for row in rows[:-1]], list(range(100, 106)))
        self.assertEqual([row["graph_local_frame"] for row in rows[:-1]], [None, 0, 1, 2, 3, None])
        self.assertEqual(rows[-1]["record_type"], "observed_terminal")
        self.assertTrue(rows[-1]["static_geometry_reobserved_unchanged"])
        self.assertEqual(self.unchanged.call_count, 2)

    def test_layer_order_matches_edges_vertices_disclosure_then_event_overlay(self):
        self.render()
        header = json.loads(self.source.with_suffix(".graph.text.json").read_bytes())
        self.assertEqual([row["name"] for row in header["objects"]],
                         ["mw_graph_edge_edge", "mw_graph_arrow_edge", "label-0",
                          "mw_graph_vertex_vertex", "label-1", "label-2", "mw_graph_event_event"])
        self.assertEqual([row["z_index"] for row in header["objects"]], list(range(7)))

    def test_event_overlay_uses_accent_double_width_and_own_half_open_opacity(self):
        self.render()
        header = json.loads(self.source.with_suffix(".graph.text.json").read_bytes())
        overlay = header["objects"][-1]
        self.assertEqual((overlay["paint_color"], overlay["stroke_width"]), ("#ff0000", 4.0))
        self.assertEqual([row["paint_opacity"][-1] for row in self.frames()[:-1]], [0, 0, 0, 1, 0, 0])

    def test_exact_native_camera_stroke_factor_is_observed(self):
        scene = self.render()
        self.assertEqual(scene.camera.cairo_line_width_multiple, 1.0)
        self.assertEqual(self.frames()[0]["camera"]["cairo_line_width_multiple"], 1.0)

    def test_wrong_pixel_resolution_is_rejected_before_layout_and_play(self):
        scene = Scene()
        scene.camera.pixel_width = 320
        with mock.patch.object(runtime, "layout_text_block") as mocked, self.assertRaises(Rejected):
            self.render(scene)
        mocked.assert_not_called()
        self.assertEqual(scene.plays, 0)

    def test_text_evidence_is_retained_individually_with_original_byte_digests(self):
        self.render()
        header = json.loads(self.source.with_suffix(".graph.text.json").read_bytes())
        for descriptor in header["text_blocks"]:
            raw = (self.source.with_suffix(".graph-labels") / descriptor["file_name"]).read_bytes()
            self.assertEqual(descriptor["size_bytes"], len(raw))
            self.assertEqual(descriptor["sha256"], runtime.hashlib.sha256(raw).hexdigest())
            self.assertTrue(json.loads(raw)["layout"]["mock_only"])

    def test_blank_text_keeps_block_without_importing_an_outline(self):
        data = payload()
        data["text_blocks"][0]["text"] = "   "
        with mock.patch.object(runtime, "import_vector_label", wraps=import_label) as imported:
            self.render(data=data)
        self.assertEqual(imported.call_count, 2)
        header = json.loads(self.source.with_suffix(".graph.text.json").read_bytes())
        first = self.source.with_suffix(".graph-labels") / header["text_blocks"][0]["file_name"]
        self.assertIsNone(json.loads(first.read_bytes())["manim_import"])

    def test_native_failure_preserves_partial_frames_without_terminal_or_retry(self):
        scene = Scene(fail_at=3)
        with self.assertRaisesRegex(RuntimeError, "controlled mock frame failure"):
            self.render(scene)
        self.assertEqual(scene.plays, 1)
        self.assertEqual([row["scene_frame"] for row in self.frames()], [0, 1, 2])
        self.assertTrue(self.source.with_suffix(".graph.text.json").is_file())
        self.assertTrue(self.source.with_suffix(".graph-labels").is_dir())

    def test_reentering_same_source_never_reuses_partial_attempt(self):
        scene = Scene(fail_at=1)
        with self.assertRaises(RuntimeError):
            self.render(scene)
        original = self.source.with_suffix(".graph.frames.jsonl").read_bytes()
        second = Scene()
        with self.assertRaises(FileExistsError):
            self.render(second)
        self.assertEqual(second.plays, 0)
        self.assertEqual(self.source.with_suffix(".graph.frames.jsonl").read_bytes(), original)

    def test_incremental_label_point_budget_stops_before_following_block(self):
        with mock.patch.object(runtime, "MAX_TOTAL_POINTS", 1), \
             mock.patch.object(runtime, "layout_text_block", wraps=layout) as observed:
            with self.assertRaises(Rejected):
                self.render()
        self.assertEqual(observed.call_count, 2)

    def test_aggregate_svg_budget_stops_before_creating_first_block_files(self):
        original = runtime.DerivedBudget
        with mock.patch.object(runtime, "DerivedBudget", side_effect=lambda: original(11)):
            with self.assertRaises(Rejected):
                self.render()
        self.assertEqual(list(self.source.with_suffix(".graph-labels").iterdir()), [])

    def test_geometry_mutation_during_animation_is_observed_and_rejected(self):
        def mutate(index, scene):
            if index == 2:
                scene.objects[0].children[0].points[0][0] += 1
        with self.assertRaisesRegex(Rejected, "graph_native_geometry_changed_during_render"):
            self.render(Scene(mutate=mutate))
        self.assertTrue(all(row["record_type"] == "frame" for row in self.frames()))

    def test_header_never_declares_runtime_admission_or_reopen(self):
        self.render()
        header = json.loads(self.source.with_suffix(".graph.text.json").read_bytes())
        for key in ("decoded_video_verified", "native_reopen_verified", "profile_admitted", "human_acceptance"):
            self.assertIs(header[key], False)

    def test_native_float_subclass_callback_is_admitted_without_accepting_bool(self):
        class NativeLikeFloat(float):
            pass
        self.assertEqual(runtime.callback_index(NativeLikeFloat(2 / 5.5), 6), 2)
        with self.assertRaises(Rejected):
            runtime.callback_index(True, 6)

    def test_single_frame_has_one_observation_and_a_separate_terminal_pose(self):
        data = payload()
        data["clock"].update(scene_frame_count=1, graph_start_frame=0, graph_frame_count=1)
        data["motion"] = "off"
        data["edges"], data["events"] = [], []
        data["text_blocks"] = [data["text_blocks"][1]]
        data["text_blocks"][0]["active_end_frame_exclusive"] = 1
        self.render(Scene(count=1), data)
        rows = self.frames()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["scene_frame"], 0)
        self.assertEqual(rows[0]["callback_alpha"], 0.0)
        self.assertEqual(rows[1]["record_type"], "observed_terminal")
        self.assertEqual(rows[1]["sample_count"], 1)


    def test_changed_native_join_is_rejected_with_partial_custody(self):
        def mutate(index, scene):
            if index == 2:
                scene.objects[0].children[0].joint_type = SimpleNamespace(name="ROUND")
        scene = Scene(mutate=mutate)
        with self.assertRaisesRegex(Rejected, "graph_native_stroke_join_or_cap_changed"):
            self.render(scene)
        self.assertEqual(scene.plays, 1)
        self.assertEqual([row["scene_frame"] for row in self.frames()], [0, 1])
        self.assertTrue(self.source.with_suffix(".graph.text.json").is_file())

    def test_changed_native_cap_is_rejected_with_partial_custody(self):
        def mutate(index, scene):
            if index == 2:
                scene.objects[0].children[0].cap_style = SimpleNamespace(name="ROUND")
        scene = Scene(mutate=mutate)
        with self.assertRaisesRegex(Rejected, "graph_native_stroke_join_or_cap_changed"):
            self.render(scene)
        self.assertEqual(scene.plays, 1)
        self.assertEqual([row["scene_frame"] for row in self.frames()], [0, 1])
        self.assertTrue(self.source.with_suffix(".graph.text.json").is_file())



if __name__ == "__main__":
    unittest.main()
