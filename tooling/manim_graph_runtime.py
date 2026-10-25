"""Fixed Graph-v1 realization inside the existing Manim 0.21 Host job.

No executor, scheduler, profile grant or Project writer is defined here. Only the
trusted Rust compiler may construct the payload and its fixed dependency pins.
All files created here stay under the job's existing owned work mount. Partial
or failed files are retained; the external custodian owns terminal cleanup.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import struct

from manim import (
    Circle, ManimColor, Polygon, RoundedRectangle, UpdateFromAlphaFunc,
    VGroup, VMobject, linear,
)
from manim.constants import CapStyleType, LineJointType

from manim_bundled_font_intake import MAX_MEMBER, read_owned_file
from manim_graph_font_probe import Rejected, require
from manim_graph_svg_import import (
    create_label_directory, import_vector_label, require_pixel_camera,
)
from manim_graph_text_layout import CONTRACT_SHA256, NativeGraphPango, layout_text_block

MAX_DERIVED_BYTES = 32 * 1024 * 1024
MAX_OBJECT_POINTS = 131072
MAX_TOTAL_POINTS = 1048576
MAX_LABELS = 225


class DerivedBudget:
    def __init__(self, maximum=MAX_DERIVED_BYTES):
        require(type(maximum) is int and 0 < maximum <= MAX_DERIVED_BYTES,
                "graph_derived_budget")
        self.maximum, self.used = maximum, 0

    def reserve(self, count):
        require(type(count) is int and 0 <= count <= self.maximum - self.used,
                "graph_derived_total_budget")
        self.used += count


def encoded_json(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def write_all(target, raw):
    view = memoryview(raw)
    while view:
        count = target.write(view)
        require(type(count) is int and 0 < count <= len(view), "graph_evidence_write_failed")
        view = view[count:]


def write_new(path, raw, budget):
    budget.reserve(len(raw))
    with Path(path).open("xb", buffering=0) as target:
        write_all(target, raw)
        os.fsync(target.fileno())


def graph_local_frame(scene_frame, first, count):
    return scene_frame - first if first <= scene_frame < first + count else None


def appearance_opacity(appearance, local, motion):
    if local is None or local < appearance["start_frame"]:
        return 0.0
    elapsed = local - appearance["start_frame"]
    duration = appearance["duration_frames"]
    require(type(duration) is int and duration > 0, "graph_appearance_duration")
    if motion == "off" or elapsed >= duration:
        return 1.0
    require(motion == "full", "graph_motion_profile")
    return elapsed / duration


def callback_index(alpha, count):
    require(isinstance(alpha, (int, float)) and not isinstance(alpha, bool)
            and math.isfinite(alpha) and 0 <= alpha <= 1,
            "graph_callback_alpha")
    if alpha == 1:
        return None
    coordinate = alpha * (count - 0.5)
    index = round(coordinate)
    require(abs(coordinate - index) <= 0.000001 and 0 <= index < count,
            "graph_callback_off_frame_grid")
    return index


def verify_fonts(runtime_root, lock, editorial_family):
    root = Path(runtime_root)
    require(root.is_absolute() and root.is_dir() and not root.is_symlink(),
            "graph_runtime_root")
    require(lock["contract_sha256"] == CONTRACT_SHA256, "graph_font_contract_mismatch")
    files, selected = [], {}
    for package in lock["packages"]:
        for item in [package["license"], *package["font_files"]]:
            relative = Path(item["relative_path"])
            require(not relative.is_absolute() and ".." not in relative.parts,
                    "graph_font_relative_path")
            path = root / "graph-profile-v1" / "fonts" / relative
            raw = read_owned_file(root, path, MAX_MEMBER)
            require(len(raw) == item["size_bytes"]
                    and hashlib.sha256(raw).hexdigest() == item["sha256"],
                    "graph_font_or_license_bytes_changed")
            canonical = str(path.resolve(strict=True))
            files.append({"path": canonical, **item})
            if package["editorial_family"] == editorial_family and item in package["font_files"]:
                require(canonical not in selected, "graph_font_duplicate")
                selected[canonical] = item
    require(selected, "graph_font_family_missing")
    return selected, files


def verify_fonts_unchanged(runtime_root, files):
    for item in files:
        raw = read_owned_file(runtime_root, item["path"], MAX_MEMBER)
        require(len(raw) == item["size_bytes"]
                and hashlib.sha256(raw).hexdigest() == item["sha256"],
                "graph_font_or_license_changed_during_render")


def point_observation(item):
    points = item.get_all_points()
    require(len(points) <= MAX_OBJECT_POINTS, "graph_native_object_point_budget")
    digest = hashlib.sha256()
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    for row in points:
        values = [float(value) for value in row]
        require(len(values) == 3 and all(math.isfinite(value) for value in values)
                and values[2] == 0, "graph_native_object_points")
        digest.update(struct.pack("<ddd", *values))
        for axis in range(3):
            minimum[axis] = min(minimum[axis], values[axis])
            maximum[axis] = max(maximum[axis], values[axis])
    return {
        "name": item.name, "native_class": type(item).__name__, "point_count": len(points),
        "point_encoding": "xyz-f64le", "points_sha256": digest.hexdigest(),
        "control_point_min": minimum if len(points) else None,
        "control_point_max": maximum if len(points) else None,
        "control_bounds_are_glyph_ink_bounds": False,
        "z_index": float(item.z_index),
    }


def family_paint_observation(item, mode, expected_opacity, expected_color, expected_width,
                             expected_members):
    members = item.family_members_with_points()
    require(len(members) == expected_members, "graph_native_family_changed")
    actual_value = None
    for member in members:
        fill = float(member.get_fill_opacity())
        stroke = float(member.get_stroke_opacity())
        width = float(member.get_stroke_width())
        require(all(math.isfinite(value) for value in (fill, stroke, width)),
                "graph_native_paint_nonfinite")
        if mode == "fill":
            actual, color = fill, member.get_fill_color().to_hex()
            require(stroke == 0 and width == 0, "graph_native_unexpected_outline")
        else:
            actual, color = stroke, member.get_stroke_color().to_hex()
            require(fill == 0 and width == expected_width, "graph_native_stroke_geometry")
            require(member.joint_type == LineJointType.MITER
                    and member.cap_style == CapStyleType.BUTT,
                    "graph_native_stroke_join_or_cap_changed")
        require(actual == expected_opacity and color.lower() == expected_color.lower(),
                "graph_native_paint_mismatch")
        actual_value = actual
    # Every member was read and checked above. Uniform actual opacity is encoded
    # once per object/frame; fixed family counts and colors live in the header.
    return actual_value


def render_graph(scene, payload, source_file, runtime_root, helper_observation):
    require(payload["schema"] == "motionwright.manim.graph-native-payload/1"
            and payload["profile_id"] == "mw-manim-semantic-graph-v1"
            and payload["contract_sha256"] == CONTRACT_SHA256, "graph_native_payload")
    profile, clock, style = payload["render_profile"], payload["clock"], payload["style"]
    width, height, fps = profile["width"], profile["height"], profile["frame_rate"]
    count = clock["scene_frame_count"]
    require(type(count) is int and 1 <= count <= 3600
            and len(payload["text_blocks"]) <= MAX_LABELS, "graph_native_budget")
    scene.camera.frame_width = float(width)
    scene.camera.frame_height = float(height)
    scene.camera.frame_center[:] = [0.0, 0.0, 0.0]
    scene.camera.cairo_line_width_multiple = 1.0
    scene.camera.background_opacity = 1.0
    camera = require_pixel_camera(scene.camera, width, height, fps)
    require(scene.camera.background_image is None, "graph_native_background_image")
    selected, font_files = verify_fonts(
        runtime_root, payload["font_lock"], payload["typography"]["editorial_family"])
    native = NativeGraphPango()
    directory = create_label_directory(source_file)
    budget, label_objects, text_rows = DerivedBudget(), {}, []
    label_point_count = 0
    for block in payload["text_blocks"]:
        raw, report, originals = layout_text_block(
            native, selected, payload["typography"]["native_family"],
            payload["typography"]["editorial_family"], block["text"],
            style["font_size"], block["authored_box"], (width, height),
            style["text_color"], block["resource"])
        # One checked composite per block; all original native line SVGs are
        # retained too. Check the complete SVG block before creating any file.
        block_bytes = sum(len(value) for value in originals) + (len(raw) if raw else 0)
        require(block_bytes <= budget.maximum - budget.used, "graph_derived_total_budget")
        for line_index, original in enumerate(originals):
            write_new(directory / f"source_{block['ordinal']:04d}_{line_index:04d}.svg",
                      original, budget)
        if raw is None:
            item, imported = VGroup(name=f"mw_graph_empty_label_{block['ordinal']:04d}"), None
        else:
            # import_vector_label itself writes the composite exclusively.
            budget.reserve(len(raw))
            item, imported = import_vector_label(
                directory, block["ordinal"], raw, report, (width, height))
        if imported is not None:
            label_point_count += imported["point_count"]
            require(label_point_count <= MAX_TOTAL_POINTS, "graph_native_total_point_budget")
        item.set_stroke(width=0, opacity=0)
        item.set_fill(style["text_color"], opacity=0)
        label_objects[block["ordinal"]] = item
        text_evidence = encoded_json({
            "ordinal": block["ordinal"], "owner": block["owner"],
            "layout": report, "manim_import": imported,
        })
        evidence_path = directory / f"text_block_{block['ordinal']:04d}.json"
        write_new(evidence_path, text_evidence, budget)
        text_rows.append({
            "ordinal": block["ordinal"], "owner": block["owner"],
            "file_name": evidence_path.name, "size_bytes": len(text_evidence),
            "sha256": hashlib.sha256(text_evidence).hexdigest(),
        })
    verify_fonts_unchanged(runtime_root, font_files)

    objects, edges, vertices, overlays, labels = [], {}, {}, [], []
    def add(item, mode, color, stroke_width=0):
        item.set_z_index(len(objects))
        objects.append((item, mode, color, stroke_width))
        return item
    def world(point):
        return [point["x"] - width / 2, height / 2 - point["y"], 0.0]
    def polyline(row, color, stroke_width, name):
        item = VMobject(name=name, joint_type=LineJointType.MITER, cap_style=CapStyleType.BUTT)
        item.set_points_as_corners([world(point) for point in row["points"]])
        item.set_fill(opacity=0)
        item.set_stroke(color, width=stroke_width, opacity=0)
        return add(item, "stroke", color, stroke_width)
    def attach_labels(kind, identity=None):
        for block in payload["text_blocks"]:
            owner = block["owner"]
            if owner["kind"] == kind and (identity is None or owner.get("id") == identity):
                item = add(label_objects[block["ordinal"]], "fill", style["text_color"])
                labels.append((block, item))

    for row in payload["edges"]:
        name = row["id"].replace("-", "")
        line = polyline(row, style["edge_color"], style["edge_width"], "mw_graph_edge_" + name)
        arrow = Polygon(*(world(point) for point in row["arrowhead"]), name="mw_graph_arrow_" + name)
        arrow.set_stroke(width=0, opacity=0)
        arrow.set_fill(style["edge_color"], opacity=0)
        add(arrow, "fill", style["edge_color"])
        edges[row["id"]] = (row, line, arrow)
        attach_labels("edge", row["id"])
    for row in payload["vertices"]:
        box = row["authored_box"]
        name = "mw_graph_vertex_" + row["id"].replace("-", "")
        if row["shape"] == "circle":
            item = Circle(radius=box["width"] / 2, name=name)
        else:
            require(row["shape"] == "rectangle", "graph_native_shape")
            item = RoundedRectangle(
                width=box["width"], height=box["height"],
                corner_radius=row["corner_radius"], name=name)
        item.move_to([box["x"] + box["width"] / 2 - width / 2,
                      height / 2 - box["y"] - box["height"] / 2, 0.0])
        item.set_stroke(width=0, opacity=0)
        item.set_fill(style["vertex_fill"], opacity=0)
        add(item, "fill", style["vertex_fill"])
        vertices[row["id"]] = (row, item)
        attach_labels("vertex", row["id"])
    attach_labels("disclosure")
    for event in payload["events"]:
        row = edges[event["edge_id"]][0]
        item = polyline(row, style["accent_color"], style["edge_width"] * 2,
                        "mw_graph_event_" + event["id"].replace("-", ""))
        overlays.append((event, item))
    moving = VGroup(*(item for item, _mode, _color, _width in objects))
    scene.add(moving)
    geometry = [point_observation(item) for item, _mode, _color, _width in objects]
    require(sum(row["point_count"] for row in geometry) <= MAX_TOTAL_POINTS,
            "graph_native_total_point_budget")
    member_counts = [len(item.family_members_with_points())
                     for item, _mode, _color, _width in objects]
    for geometry_row, members, (_item, mode, color, stroke_width) in zip(
            geometry, member_counts, objects, strict=True):
        geometry_row.update({
            "members_with_points": members, "paint_mode": mode,
            "paint_color": color, "stroke_width": stroke_width,
            "stroke_join": _item.joint_type.name if mode == "stroke" else None,
            "stroke_cap": _item.cap_style.name if mode == "stroke" else None,
        })
    source_path = Path(source_file)
    source_bytes = read_owned_file(source_path.parent, source_path, 256 * 1024)
    header = {
        "schema": "motionwright.manim.graph-native-observation/1",
        "profile_id": payload["profile_id"], "plan_sha256": payload["plan_sha256"],
        "generated_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "execution_snapshot": payload["execution_snapshot"],
        "graph_id": payload["graph_id"], "scene_id": payload["scene_id"],
        "camera": camera, "helper_modules": helper_observation,
        "font_files": [{key: value for key, value in row.items() if key != "path"} for row in font_files],
        "text_blocks": text_rows, "objects": geometry,
        "derived_byte_limit": MAX_DERIVED_BYTES,
        "one_svg_parser_temporary_byte_limit": 512 * 1024,
        "point_limit": MAX_TOTAL_POINTS,
        "decoded_video_verified": False, "native_reopen_verified": False,
        "profile_admitted": False, "human_acceptance": False,
    }
    write_new(source_path.with_suffix(".graph.text.json"), encoded_json(header), budget)
    first_observation, observed = None, 0
    frame_path = source_path.with_suffix(".graph.frames.jsonl")
    with frame_path.open("xb", buffering=0) as target:
        def apply_pose(index):
            local = graph_local_frame(index, clock["graph_start_frame"], clock["graph_frame_count"])
            background = style["background"] if local is not None else payload["scene_matte"]
            if scene.camera.background_color.to_hex().lower() != background.lower():
                scene.camera.background_color = ManimColor(background)
            expected = {}
            for row, line, arrow in edges.values():
                alpha = appearance_opacity(row["appearance"], local, payload["motion"])
                line.set_stroke(opacity=alpha)
                arrow.set_fill(opacity=alpha)
                expected[id(line)] = expected[id(arrow)] = alpha
            for row, item in vertices.values():
                alpha = appearance_opacity(row["appearance"], local, payload["motion"])
                item.set_fill(opacity=alpha)
                expected[id(item)] = alpha
            for block, item in labels:
                owner = block["owner"]
                if owner["kind"] == "edge":
                    appearance = edges[owner["id"]][0]["appearance"]
                elif owner["kind"] == "vertex":
                    appearance = vertices[owner["id"]][0]["appearance"]
                else:
                    appearance = None
                active = (local is not None
                          and block["active_start_frame"] <= local < block["active_end_frame_exclusive"])
                alpha = (appearance_opacity(appearance, local, payload["motion"])
                         if active and appearance is not None else 1.0 if active else 0.0)
                item.set_fill(opacity=alpha)
                expected[id(item)] = alpha
            for event, item in overlays:
                active = local is not None and event["start_frame"] <= local < event["end_frame_exclusive"]
                alpha = 1.0 if active else 0.0
                item.set_stroke(opacity=alpha)
                expected[id(item)] = alpha
            return local, background, expected

        def update(_group, alpha):
            nonlocal first_observation, observed
            index = callback_index(alpha, count)
            if index is None:
                apply_pose(count - 1)
                return
            local, background, expected = apply_pose(index)
            row = {
                "record_type": "frame", "scene_frame": index,
                "project_frame": clock["project_start_frame"] + index,
                "graph_local_frame": local, "callback_alpha": float(alpha),
                "camera": require_pixel_camera(scene.camera, width, height, fps),
                "background_hex": scene.camera.background_color.to_hex(),
                "background_opacity": float(scene.camera.background_opacity),
                "paint_opacity": [
                    family_paint_observation(
                        item, mode, expected[id(item)], color, stroke_width, members)
                    for (item, mode, color, stroke_width), members
                    in zip(objects, member_counts, strict=True)
                ],
            }
            require(row["background_hex"].lower() == background.lower()
                    and row["background_opacity"] == 1, "graph_native_background_mismatch")
            if observed == index:
                raw = encoded_json(row)
                budget.reserve(len(raw))
                write_all(target, raw)
                observed += 1
                if index == 0:
                    first_observation = row
            elif not (index == 0 and observed == 1 and row == first_observation):
                raise Rejected("graph_native_sample_sequence")

        # Scene 0.21 clamps its duration to 1/fps for count=1, without
        # changing animation.run_time. Its arange loop still samples only t=0.
        # For count>=2, t=i/fps gives alpha=i/(count-0.5); finish is not a frame.
        scene.play(UpdateFromAlphaFunc(
            moving, update, run_time=(count - 0.5) / fps, rate_func=linear))
        require(observed == count, "graph_native_samples_missing")
        verify_fonts_unchanged(runtime_root, font_files)
        final_geometry = [point_observation(item) for item, _mode, _color, _width in objects]
        for initial, final in zip(geometry, final_geometry, strict=True):
            require(all(final[key] == value for key, value in initial.items()
                        if key in final), "graph_native_geometry_changed_during_render")
        terminal = encoded_json({
            "record_type": "observed_terminal", "sample_count": observed,
            "static_geometry_reobserved_unchanged": True,
            "plan_sha256": payload["plan_sha256"], "terminal_editing_scene_frame": count - 1,
            "decoded_video_verified": False, "native_reopen_verified": False,
        })
        budget.reserve(len(terminal))
        write_all(target, terminal)
        os.fsync(target.fileno())
