"""Vector outlines from the same checked, live Pango layout used by Graph.

This module belongs inside the existing trusted Manim program/runtime. It does
not start a process, load an imported scene, admit a profile, or alter Fontconfig
globally. Its callers own the existing runtime/font mounts and keep their CAS
leases until the Host is terminal/reaped and all byte consumers have finished.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import math
import re
import xml.etree.ElementTree as ET

from manim_graph_font_probe import (
    PANGO_SCALE, Rectangle, Rejected, _bind, require, validate_observation,
)

MAX_LABEL_SVG_BYTES = 512 * 1024
MAX_SVG_ELEMENTS = 4096
SVG_NS = "http://www.w3.org/2000/svg"
CAIRO_STATUS_WRITE_ERROR = 11
CAIRO_SVG_UNIT_PX = 3
CAIRO_SVG_VERSION_1_1 = 0


def _finite(values, reason):
    require(all(type(value) in (int, float) and math.isfinite(value)
                for value in values), reason)


def _native_layout_signature(native, layout):
    ink, logical = Rectangle(), Rectangle()
    native.layout_extents(layout, C.byref(ink), C.byref(logical))
    baseline = _bind(native.pango, "pango_layout_get_baseline", C.c_int, C.c_void_p)
    return {
        "text": native.layout_get_text(layout),
        "unknown": native.unknown_glyphs(layout),
        "ink": [ink.x, ink.y, ink.width, ink.height],
        "logical": [logical.x, logical.y, logical.width, logical.height],
        "baseline": baseline(layout),
    }


class BoundedSvgSink:
    """Cairo stream callback owner; no exception crosses the native ABI."""

    def __init__(self, maximum=MAX_LABEL_SVG_BYTES):
        require(type(maximum) is int and 0 < maximum <= MAX_LABEL_SVG_BYTES,
                "graph_svg_output_limit")
        self.maximum = maximum
        self.buffer = bytearray()
        self.failed = False

    def append(self, fragment):
        if self.failed or len(fragment) > self.maximum - len(self.buffer):
            self.failed = True
            return CAIRO_STATUS_WRITE_ERROR
        self.buffer.extend(fragment)
        return 0

    def callback(self, _closure, data, length):
        try:
            if length > self.maximum - len(self.buffer):
                self.failed = True
                return CAIRO_STATUS_WRITE_ERROR
            return self.append(C.string_at(data, length))
        except Exception:
            self.failed = True
            return CAIRO_STATUS_WRITE_ERROR


class NativeCairoSvg:
    """Fixed public Cairo/PangoCairo ABI; no user-controlled library path."""

    def __init__(self, pango_native):
        try:
            self.library = C.CDLL("libcairo.so.2")
            pointer, double, integer = C.c_void_p, C.c_double, C.c_int
            self.callback_type = C.CFUNCTYPE(integer, pointer, pointer, C.c_uint)
            self.create_surface = _bind(
                self.library, "cairo_svg_surface_create_for_stream", pointer,
                self.callback_type, pointer, double, double)
            self.surface_unit = _bind(
                self.library, "cairo_svg_surface_set_document_unit", None, pointer, integer)
            self.surface_version = _bind(
                self.library, "cairo_svg_surface_restrict_to_version", None, pointer, integer)
            self.surface_status = _bind(self.library, "cairo_surface_status", integer, pointer)
            self.finish_surface = _bind(self.library, "cairo_surface_finish", None, pointer)
            self.destroy_surface = _bind(self.library, "cairo_surface_destroy", None, pointer)
            self.create_context = _bind(self.library, "cairo_create", pointer, pointer)
            self.context_status = _bind(self.library, "cairo_status", integer, pointer)
            self.destroy_context = _bind(self.library, "cairo_destroy", None, pointer)
            self.source_rgb = _bind(
                self.library, "cairo_set_source_rgb", None, pointer, double, double, double)
            self.move_to = _bind(self.library, "cairo_move_to", None, pointer, double, double)
            self.layout_path = _bind(
                pango_native.cairo, "pango_cairo_layout_path", None, pointer, pointer)
            self.path_extents = _bind(
                self.library, "cairo_path_extents", None, pointer,
                *([C.POINTER(double)] * 4))
            self.fill = _bind(self.library, "cairo_fill", None, pointer)
            self.version = _bind(self.library, "cairo_version", integer)
            self.version_string = _bind(self.library, "cairo_version_string", C.c_char_p)
        except (OSError, AttributeError) as error:
            raise Rejected("native_cairo_svg_api_unavailable") from error
        require(self.version() >= 11600, "cairo_1_16_required_for_explicit_svg_pixels")

    def outlines(self, layout, width, height, origin, rgb):
        sink = BoundedSvgSink()
        callback = self.callback_type(sink.callback)
        surface = self.create_surface(callback, None, width, height)
        require(bool(surface), "native_cairo_svg_surface_missing")
        try:
            require(self.surface_status(surface) == 0, "native_cairo_svg_surface_failed")
            self.surface_unit(surface, CAIRO_SVG_UNIT_PX)
            self.surface_version(surface, CAIRO_SVG_VERSION_1_1)
            context = self.create_context(surface)
            require(bool(context), "native_cairo_context_missing")
            try:
                require(self.context_status(context) == 0, "native_cairo_context_failed")
                self.source_rgb(context, *rgb)
                self.move_to(context, *origin)
                self.layout_path(context, layout)
                extents = [C.c_double() for _ in range(4)]
                self.path_extents(context, *(C.byref(value) for value in extents))
                bounds = [value.value for value in extents]
                _finite(bounds, "native_cairo_path_nonfinite")
                require(bounds[0] < bounds[2] and bounds[1] < bounds[3],
                        "native_cairo_glyph_outlines_empty")
                self.fill(context)
                require(self.context_status(context) == 0, "native_cairo_path_failed")
            finally:
                self.destroy_context(context)
            self.finish_surface(surface)
            require(not sink.failed, "graph_svg_output_budget")
            require(self.surface_status(surface) == 0, "native_cairo_svg_write_failed")
            raw = bytes(sink.buffer)
            require(raw, "native_cairo_svg_empty")
            return raw, bounds, self.version_string().decode("ascii", "strict")
        finally:
            # The callback and its buffer remain alive through surface destroy.
            self.destroy_surface(surface)


def validate_vector_svg(raw, width, height):
    """Reject raster/text/external resources before Manim parses derived SVG."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_LABEL_SVG_BYTES,
            "graph_svg_output_budget")
    require(b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper(),
            "graph_svg_external_declaration")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise Rejected("graph_svg_invalid_xml") from error
    require(root.tag == "{" + SVG_NS + "}svg", "graph_svg_root")
    require(root.get("width") == str(width) + "px"
            and root.get("height") == str(height) + "px"
            and root.get("viewBox") == f"0 0 {width} {height}",
            "graph_svg_viewport_changed")
    permitted = {"svg", "g", "path", "defs", "clipPath"}
    paths = 0
    elements = list(root.iter())
    require(len(elements) <= MAX_SVG_ELEMENTS, "graph_svg_element_budget")
    for element in elements:
        require(element.tag.startswith("{" + SVG_NS + "}"), "graph_svg_foreign_namespace")
        tag = element.tag.split("}", 1)[1]
        require(tag in permitted, "graph_svg_non_outline_element")
        for key, value in element.attrib.items():
            require("}" not in key and key.lower() not in ("href", "src")
                    and not key.lower().startswith("on"), "graph_svg_external_attribute")
            require(not re.search(r"(?:https?|file|data|javascript):", value, re.I),
                    "graph_svg_external_value")
            if "url(" in value.lower():
                require(re.fullmatch(r"url\(\s*#[A-Za-z_][A-Za-z0-9_.-]*\s*\)", value),
                        "graph_svg_external_reference")
        if tag == "path":
            require(bool(element.get("d", "").strip()), "graph_svg_empty_outline")
            paths += 1
    require(paths > 0, "graph_svg_no_outlines")
    return paths


def vectorize_observed_layout(native, layout, observation, allowed_files,
                               family, text_bytes, document_size, origin_px,
                               color_hex):
    """Consume the still-live layout, not a second lookup or a JSON assertion.

    origin_px is the top-left Pango layout origin chosen by the accepted Graph
    layout contract. This primitive does not choose wrapping, alignment, padding,
    clipping, font size or fit policy. The caller must check the returned logical
    and outline bounds against the authored label box before publishing a frame.
    """
    width, height = document_size
    require(type(width) is int and type(height) is int
            and 320 <= width <= 7680 and 240 <= height <= 4320
            and width * height <= 33177600, "graph_svg_document_size")
    require(len(origin_px) == 2, "graph_svg_origin_shape")
    _finite(origin_px, "graph_svg_origin_nonfinite")
    require(type(color_hex) is str and re.fullmatch(r"#[0-9A-Fa-f]{6}", color_hex),
            "graph_svg_color")
    runs = validate_observation(observation, allowed_files, family, text_bytes)
    before = _native_layout_signature(native, layout)
    require(before["text"] == text_bytes and before["unknown"] == 0
            and before["ink"] == observation["ink_pango_units"]
            and before["logical"] == observation["logical_pango_units"],
            "graph_layout_changed_before_vectorization")
    rgb = [int(color_hex[index:index + 2], 16) / 255.0 for index in (1, 3, 5)]
    raw, outline_bounds, cairo_version = NativeCairoSvg(native).outlines(
        layout, width, height, origin_px, rgb)
    after = _native_layout_signature(native, layout)
    require(before == after, "graph_layout_changed_during_vectorization")
    path_count = validate_vector_svg(raw, width, height)
    logical = before["logical"]
    logical_bounds = [
        origin_px[0] + logical[0] / PANGO_SCALE,
        origin_px[1] + logical[1] / PANGO_SCALE,
        origin_px[0] + (logical[0] + logical[2]) / PANGO_SCALE,
        origin_px[1] + (logical[1] + logical[3]) / PANGO_SCALE,
    ]
    report = {
        "schema": "motionwright.manim.graph-text-vector-observation/1",
        "status": "OBSERVED_PANGO_CAIRO_OUTLINES_ONLY",
        "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
        "text_bytes": len(text_bytes), "runs": runs,
        "unknown_glyph_count": 0, "fallback_allowed": False,
        "logical_bounds_px": logical_bounds,
        "outline_bounds_px": outline_bounds,
        "baseline_px": origin_px[1] + before["baseline"] / PANGO_SCALE,
        "layout_origin_px": list(origin_px),
        "units_per_pixel": PANGO_SCALE,
        "svg_sha256": hashlib.sha256(raw).hexdigest(),
        "svg_bytes": len(raw), "svg_outline_paths": path_count,
        "cairo_version": cairo_version, "pango_version": observation["pango_version"],
        "fontconfig_version": observation["fontconfig_version"],
        "manim_import_verified": False, "manim_frame_verified": False,
        "profile_admitted": False, "human_acceptance": False,
    }
    return raw, report


def require_authored_box_fit(report, box):
    """Exact authored box; do not shrink, center-to-fit, clip or truncate."""
    require(set(box) == {"x", "y", "width", "height"}, "graph_label_box_shape")
    _finite(box.values(), "graph_label_box_nonfinite")
    require(box["width"] > 0 and box["height"] > 0, "graph_label_box_extent")
    x0, y0 = box["x"], box["y"]
    x1, y1 = x0 + box["width"], y0 + box["height"]
    for name in ("logical_bounds_px", "outline_bounds_px"):
        bounds = report[name]
        require(len(bounds) == 4, "graph_label_bounds_shape")
        _finite(bounds, "graph_label_bounds_nonfinite")
        require(x0 <= bounds[0] <= bounds[2] <= x1
                and y0 <= bounds[1] <= bounds[3] <= y1,
                "graph_label_overflow")
