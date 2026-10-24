# Trusted compiler template. Project strings are JSON data, never Python source.
# APIs pinned to Manim Community 0.21.0 / ManimPango 0.6.1 (Linux Cairo).
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import manim
import manimpango
import numpy as np
from manim import Scene, Text, VGroup, Rectangle, Circle, Line, config
from manim.mobject.text.text_mobject import TEXT_MOB_SCALE_FACTOR, TEXT2SVG_ADJUSTMENT_FACTOR, START_X, START_Y

PLAN = json.loads(__PLAN_JSON_LITERAL__)
WAIT_HEX = __WAIT_HEX_LITERAL__
FONTCONFIG_SHA = __FONTCONFIG_SHA_LITERAL__
if (config.pixel_width, config.pixel_height, config.frame_rate) != (PLAN["render_profile"]["width"], PLAN["render_profile"]["height"], PLAN["render_profile"]["frame_rate"]):
    raise ValueError("editable_runtime_output_profile_mismatch")
# Configure before Scene creates Camera: its Cairo cache and allowable norm
# must use this aspect too. Camera parses this color into a native ManimColor.
config.frame_height = 8.0
config.frame_width = 8.0 * config.pixel_width / config.pixel_height
config.background_color = PLAN["background"]["color"]
config.background_opacity = 1.0


def _digest(path, maximum):
    if path.is_symlink() or not path.is_file():
        raise ValueError("editable_native_file_not_regular")
    before = path.stat()
    if not 0 < before.st_size <= maximum:
        raise ValueError("editable_native_file_budget")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or len(data) != before.st_size:
        raise ValueError("editable_native_file_changed")
    return hashlib.sha256(data).hexdigest(), len(data)


def _vector(value):
    return [float(item) for item in value]


def _native_points(mobject, center, x_axis, y_axis):
    points = mobject.get_all_points()
    if len(points) == 0:
        raise ValueError("editable_text_has_no_native_points")
    relative = points - center
    xs, ys, zs = relative @ x_axis, relative @ y_axis, relative[:, 2]
    low = np.array([float(xs.min()), float(ys.min()), float(zs.min())])
    high = np.array([float(xs.max()), float(ys.max()), float(zs.max())])
    return (low + high) / 2, high - low


class MotionwrightPlainText(Text):
    """The pinned Text native type with explicit Pango no-wrap layout.

    Text0.21 internally gives Pango the output pixel width, which can silently
    wrap a long line. This fixed backend uses the existing Manim/Pango methods
    with pango_width=-1 and a bounded SVG surface; no project code is invoked.
    """
    def _text2svg(self, color):
        directory = config.get_dir("text_dir")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (self._text2hash(color) + ".svg")
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("editable_text_svg_not_regular")
            return str(path.resolve())
        return manimpango.text2svg(self._text2settings(color),
            self._font_size / TEXT2SVG_ADJUSTMENT_FACTOR,
            self.line_spacing / TEXT2SVG_ADJUSTMENT_FACTOR,
            self.disable_ligatures, str(path.resolve()), START_X, START_Y,
            32768, 32768, self.text, pango_width=-1)


class MotionwrightScene(Scene):
    def construct(self):
        if sys.platform != "linux" or manim.__version__ != "0.21.0" or manimpango.__version__ != "0.6.1":
            raise ValueError("editable_runtime_identity_unsupported")
        if TEXT_MOB_SCALE_FACTOR != 0.05 or TEXT2SVG_ADJUSTMENT_FACTOR != 4.8:
            raise ValueError("editable_font_conversion_contract_changed")
        source_path = Path(__file__).resolve()
        assets = source_path.with_suffix(".assets")
        if assets.is_symlink() or not assets.is_dir() or assets.parent != source_path.parent:
            raise ValueError("editable_font_assets_missing")
        fontconfig_path = assets / "fonts.conf"
        if os.environ.get("FONTCONFIG_FILE") != str(fontconfig_path) or os.environ.get("FONTCONFIG_PATH") != str(assets):
            raise ValueError("editable_fontconfig_not_job_isolated")
        config_sha, _ = _digest(fontconfig_path, 4096)
        if config_sha != FONTCONFIG_SHA:
            raise ValueError("editable_fontconfig_changed")
        expected_files = {"fonts.conf"} | {font["sha256"] + "." + font["format"] for font in PLAN["fonts"]}
        actual_files = {path.name for path in assets.iterdir() if path.name != "cache"}
        if actual_files != expected_files or ((assets / "cache").exists() and ((assets / "cache").is_symlink() or not (assets / "cache").is_dir())):
            raise ValueError("editable_font_inventory_changed")
        # Each fresh native process reads the bytes, registers only that private
        # font file, and asks the native Pango font map for its actual family list.
        native_fonts = []
        font_by_id = {}
        for font in PLAN["fonts"]:
            path = assets / (font["sha256"] + "." + font["format"])
            sha, size = _digest(path, 16 * 1024 * 1024)
            if sha != font["sha256"]:
                raise ValueError("editable_font_digest_changed")
            registered = bool(manimpango.register_font(str(path)))
            if not registered:
                raise ValueError("editable_font_registration_failed")
            family_present = font["family"] in manimpango.list_fonts()
            if not family_present:
                raise ValueError("editable_native_font_family_absent")
            native_fonts.append({"sha256": sha, "bytes": size, "registered": registered, "native_family_present": family_present})
            font_by_id[font["asset_id"]] = font
        Text.font_list.cache_clear()
        output_width = self.camera.pixel_width
        output_height = self.camera.pixel_height
        self.camera.frame_center = np.zeros(3)
        # A per-job text directory prevents a prior SVG cache from substituting
        # outlines when a different font file keeps the same family name.
        config.text_dir = str(assets / "cache" / "text")
        pixels_per_world = output_height / self.camera.frame_height
        if abs(output_width / self.camera.frame_width - pixels_per_world) > 1e-9:
            raise ValueError("editable_camera_pixel_aspect_incoherent")
        sx, sy = output_width / PLAN["canvas"]["width"], output_height / PLAN["canvas"]["height"]
        if PLAN["framing"] == "crop":
            sx = sy = object_scale = max(sx, sy)
            ox = (output_width - PLAN["canvas"]["width"] * object_scale) / 2.0
            oy = (output_height - PLAN["canvas"]["height"] * object_scale) / 2.0
        else:
            object_scale, ox, oy = min(sx, sy), 0.0, 0.0
        world_per_canonical_px = object_scale / pixels_per_world
        records = []
        for node in sorted(PLAN["nodes"], key=lambda row: (row["z_index"], row["canonical_ordinal"])):
            width, height = node["width_px"] * world_per_canonical_px, node["height_px"] * world_per_canonical_px
            frame = Rectangle(width=width, height=height, stroke_width=0, stroke_opacity=0, fill_opacity=0)
            x_marker = Line(np.zeros(3), np.array([1.0, 0.0, 0.0]), stroke_width=0, stroke_opacity=0)
            y_marker = Line(np.zeros(3), np.array([0.0, 1.0, 0.0]), stroke_width=0, stroke_opacity=0)
            text_lines = []
            initial_sizes = []
            if node["kind"] == "text":
                typography = node["typography"]
                font = font_by_id[typography["font_asset_id"]]
                lines = typography["text"].split("\n")
                # Text._text2svg divides by4.8, Pango maps pt to96/72px,
                # SVGMobject scales by0.05: native em=font_size/72 world units.
                native_size = typography["font_size_px"] * world_per_canonical_px * 72.0
                for index, line in enumerate(lines):
                    text = MotionwrightPlainText(line, font=font["family"], font_size=native_size,
                                weight="BOLD" if typography["weight"] == 700 else "NORMAL",
                                slant="NORMAL", color=node["fill_hex"], fill_opacity=node["opacity"],
                                stroke_width=0, stroke_opacity=0, should_center=True,
                                disable_ligatures=False, use_svg_cache=False, warn_missing_font=True)
                    text.move_to([0.0, ((len(lines) - 1) / 2 - index) * typography["font_size_px"] * typography["line_height"] * world_per_canonical_px, 0.0])
                    initial_sizes.append(float(text.font_size))
                    text_lines.append(text)
                visible = VGroup(*text_lines)
                if visible.width > width + 1e-7 or visible.height > height + 1e-7:
                    raise ValueError("editable_native_text_layout_overflow")
            elif node["kind"] == "circle":
                visible = Circle(radius=width / 2.0, color=node["fill_hex"], fill_opacity=node["opacity"], stroke_width=0, stroke_opacity=0)
            else:
                visible = Rectangle(width=width, height=height, color=node["fill_hex"], fill_opacity=node["opacity"], stroke_width=0, stroke_opacity=0)
            group = VGroup(frame, x_marker, y_marker, visible)
            group.name = "mw_" + node["node_id"].replace("-", "")
            group.set_z_index(node["z_index"], family=True)
            group.rotate(-math.radians(node["rotation_deg"]), about_point=np.zeros(3))
            target = np.array([((node["x_px"] + node["width_px"] / 2.0) * sx + ox - output_width / 2.0) / pixels_per_world,
                               (output_height / 2.0 - (node["y_px"] + node["height_px"] / 2.0) * sy - oy) / pixels_per_world, 0.0])
            group.shift(target)
            self.add(group)
            records.append((group, frame, x_marker, y_marker, visible, text_lines, initial_sizes))
        native_objects = []
        for group, frame, x_marker, y_marker, visible, text_lines, initial_sizes in records:
            center = frame.get_center()
            x_axis = x_marker.get_end() - x_marker.get_start()
            y_axis = y_marker.get_end() - y_marker.get_start()
            native_lines = []
            for line, initial_size in zip(text_lines, initial_sizes):
                local_center, local_size = _native_points(line, center, x_axis, y_axis)
                native_lines.append({"primitive": type(line).__name__, "original_text": line.original_text,
                    "family": line.font, "weight": line.weight, "font_size_before_transform": initial_size,
                    "local_center": _vector(local_center), "local_width": float(local_size[0]), "local_height": float(local_size[1]),
                    "glyph_count": len(line.submobjects), "fill_hex": line.get_fill_color().to_hex(), "fill_opacity": float(line.get_fill_opacity())})
            style_object = text_lines[0] if text_lines else visible
            native_objects.append({"name": group.name, "primitive": type(visible).__name__, "center": _vector(center),
                "layout_corners": [_vector(value) for value in frame.get_vertices()], "x_axis": _vector(x_axis), "y_axis": _vector(y_axis),
                "z_index": float(group.z_index), "fill_hex": style_object.get_fill_color().to_hex(), "fill_opacity": float(style_object.get_fill_opacity()),
                "stroke_width": float(style_object.get_stroke_width()), "stroke_opacity": float(style_object.get_stroke_opacity()), "lines": native_lines})
        source_sha, _ = _digest(source_path, 256 * 1024)
        readback = {"schema_version": 1, "profile_id": "mw-manim-editable-text-v3", "scene_class": self.__class__.__name__,
            "source_file_sha256": source_sha, "fontconfig_sha256": config_sha,
            "camera": {"pixel_width": self.camera.pixel_width, "pixel_height": self.camera.pixel_height,
                "frame_rate": float(self.camera.frame_rate), "frame_width": float(self.camera.frame_width), "frame_height": float(self.camera.frame_height),
                "frame_center": _vector(self.camera.frame_center), "background_hex": self.camera.background_color.to_hex(), "background_opacity": float(self.camera.background_opacity)},
            "fonts": native_fonts, "objects": native_objects}
        with source_path.with_suffix(".objects.json").open("x", encoding="utf-8") as record:
            record.write(json.dumps(readback, sort_keys=True, indent=2, allow_nan=False) + "\n")
        realized_wait = float.fromhex(WAIT_HEX)
        if int(realized_wait / (1.0 / self.camera.frame_rate)) != PLAN["clock"]["frame_count"]:
            raise ValueError("editable_frame_count_realization_mismatch")
        self.wait(realized_wait, frozen_frame=True)
