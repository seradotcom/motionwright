"""Negative-control checks for source-bound Manim decoded-media inspection."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_manim_camera_pixels as inspect


def source() -> dict:
    return {
        "schema_version": 4,
        "clock": {"frame_count": 120, "frame_rate": {"num": "30", "den": "1"}},
        "render_profile": {"width": 640, "height": 360, "frame_rate": 30},
        "reframe": {
            "component_version": 2, "duration_frames": 120,
            "start_center_x": 960.0, "start_center_y": 540.0,
            "end_center_x": 1000.0, "end_center_y": 544.0,
        },
        "camera": {"center_x": 1000.0, "center_y": 544.0},
        "nodes": [
            {
                "source_kind": "rectangle", "node_id": "rect-uuid",
                "x_px": 400.0, "y_px": 400.0,
                "width_px": 220.0, "height_px": 120.0,
                "fill_hex": "#86C8D1",
            },
        ],
        "binding": {"project_id": "uuid", "revision": 9},
    }


def color_frames(plan: dict, still: bool = False, horizontal_only: bool = False) -> dict[int, bytes]:
    frames = {}
    for frame in inspect.READBACK_SAMPLES:
        data = bytearray(inspect.FRAME_BYTES)
        for node in plan["nodes"]:
            cx, cy = inspect.frame_center(plan, node, 0 if still else frame)
            if horizontal_only:
                cy = inspect.frame_center(plan, node, 0)[1]
            rgb = bytes.fromhex(node["fill_hex"][1:])
            # A filled rectangle and square interior yield the same stable
            # source-color centroid as the physical large Manim shapes.
            half_w = int(node["width_px"] / 6)
            half_h = int(node["height_px"] / 6)
            for y in range(round(cy) - half_h, round(cy) + half_h):
                for x in range(round(cx) - half_w, round(cx) + half_w):
                    offset = (y * inspect.WIDTH + x) * 3
                    data[offset:offset + 3] = rgb
        frames[frame] = bytes(data)
    return frames


class CameraPixelReaderTests(unittest.TestCase):
    def test_source_frame_positions_are_exactly_bound_to_camera_linear_move(self):
        plan = source()
        first = inspect.frame_center(plan, plan["nodes"][0], 0)
        middle = inspect.frame_center(plan, plan["nodes"][0], 60)
        last = inspect.frame_center(plan, plan["nodes"][0], 119)
        self.assertAlmostEqual(first[0] - middle[0], 20.0 / 3)
        self.assertAlmostEqual(first[0] - last[0], (40 * 119 / 120) / 3)
        self.assertAlmostEqual(first[1] - last[1], (4 * 119 / 120) / 3)

    def test_independent_decoded_color_motion_positive_control(self):
        observations = inspect.assert_centroids(source(), color_frames(source()))
        self.assertEqual(len(observations), 3)
        self.assertEqual({row["frame"] for row in observations}, {0, 60, 119})
        self.assertTrue(all(row["matched_color_pixels"] > 100 for row in observations))

    def test_horizontal_only_motion_is_not_a_valid_vertical_camera_move(self):
        plan = source()
        # X changes as authored while the Y center is held at frame zero.
        # The existing broad per-frame center tolerance must not mask this.
        with self.assertRaisesRegex(AssertionError, "vertical camera movement"):
            inspect.assert_centroids(plan, color_frames(plan, horizontal_only=True))

    def test_static_frames_with_valid_timestamps_do_not_pass_motion(self):
        plan = source()
        with self.assertRaisesRegex(AssertionError, "diverges"):
            inspect.assert_centroids(plan, color_frames(plan, still=True))

    def test_black_frames_are_not_equivalent_to_saved_source_colors(self):
        plan = source()
        black = bytes(inspect.FRAME_BYTES)
        with self.assertRaisesRegex(AssertionError, "missing"):
            inspect.assert_centroids(plan, {i: black for i in inspect.READBACK_SAMPLES})

    def test_missing_physical_sample_is_rejected(self):
        frames = color_frames(source())
        frames.pop(119)
        with self.assertRaisesRegex(AssertionError, "missing"):
            inspect.assert_centroids(source(), frames)

    def test_corrupt_decoded_byte_count_rejected_before_content_claim(self):
        plan = source()
        with self.assertRaisesRegex(AssertionError, "length"):
            inspect.color_center(b"\x00" * 9, plan["nodes"][0], (166, 153))

    def test_hash_bound_source_input_and_byte_mutation_negative(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "canonical-camera.json"
            raw = (json.dumps(source(), indent=2) + "\n").encode()
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            self.assertEqual(inspect.verified_plan(path, digest), source())
            path.write_bytes(raw + b"\n")
            with self.assertRaisesRegex(AssertionError, "SHA-256 mismatch"):
                inspect.verified_plan(path, digest)

    def test_numeric_rational_is_rejected_even_with_matching_source_digest(self):
        # The native compiler emits canonical rational fields as decimal strings.
        # Do not silently coerce another representation into an accepted source.
        with tempfile.TemporaryDirectory() as folder:
            plan = source()
            plan["clock"]["frame_rate"] = {"num": 30, "den": 1}
            raw = json.dumps(plan).encode("utf-8")
            path = Path(folder) / "wrong-rational-shape.json"
            path.write_bytes(raw)
            with self.assertRaisesRegex(AssertionError, "120-frame camera contract"):
                inspect.verified_plan(path, hashlib.sha256(raw).hexdigest())

    def test_capitalized_rectangle_enum_is_rejected_even_with_matching_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = source()
            plan["nodes"][0]["source_kind"] = "Rectangle"
            raw = json.dumps(plan).encode("utf-8")
            path = Path(folder) / "wrong-enum-shape.json"
            path.write_bytes(raw)
            with self.assertRaisesRegex(AssertionError, "admitted rectangle"):
                inspect.verified_plan(path, hashlib.sha256(raw).hexdigest())

    def test_nonadmitted_camera_endpoints_rejected_even_with_a_matching_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = source()
            plan["reframe"]["end_center_x"] = 1040.0
            raw = json.dumps(plan).encode()
            path = Path(folder) / "nonadmitted.json"
            path.write_bytes(raw)
            with self.assertRaisesRegex(AssertionError, "camera endpoints"):
                inspect.verified_plan(path, hashlib.sha256(raw).hexdigest())

    def test_second_object_exceeds_the_120_frame_getter_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = source()
            second = plan["nodes"][0].copy()
            second["node_id"] = "second-uuid"
            second["x_px"] = 900.0
            plan["nodes"].append(second)
            raw = json.dumps(plan).encode()
            path = Path(folder) / "oversubscribed.json"
            path.write_bytes(raw)
            with self.assertRaisesRegex(AssertionError, "one original editable"):
                inspect.verified_plan(path, hashlib.sha256(raw).hexdigest())

    def test_truncated_or_unbound_identity_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = source()
            plan["nodes"].append(plan["nodes"][0].copy())
            raw = json.dumps(plan).encode()
            path = Path(folder) / "duplicate.json"
            path.write_bytes(raw)
            with self.assertRaisesRegex(AssertionError, "identities"):
                inspect.verified_plan(path, hashlib.sha256(raw).hexdigest())

    def test_symlink_plan_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "saved.json"
            raw = json.dumps(source()).encode()
            target.write_bytes(raw)
            alias = root / "alias.json"
            try:
                alias.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("host does not allow symlink creation")
            with self.assertRaisesRegex(AssertionError, "ordinary"):
                inspect.verified_plan(alias, hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    unittest.main()
