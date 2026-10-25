"""Graph-v1 literal line placement in the existing native Manim process.

This consumes the same live private Pango layout that yields the font-file
getters and Cairo outlines. It does not wrap, fit, clip, install fonts, start
another process, or grant profile readiness. The caller retains canonical
Graph source and the existing runtime/font leases through terminal custody.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import math
import re
import xml.etree.ElementTree as ET

from manim_graph_font_probe import (
    MAX_TEXT_BYTES, NativePango, PANGO_SCALE, Rejected, _bind, require,
    validate_observation,
)
from manim_graph_vector_text import (
    MAX_LABEL_SVG_BYTES, SVG_NS, _native_layout_signature,
    validate_vector_svg, vectorize_observed_layout,
)

CONTRACT_SHA256 = "f0c7b5a58efed42a32461df4a8cb92bc4cf187f644adad2b76423496dc0b258a"


class NativeGraphPango(NativePango):
    def __init__(self):
        super().__init__()
        self.line_count = _bind(
            self.pango, "pango_layout_get_line_count", C.c_int, C.c_void_p)
        self.is_wrapped = _bind(
            self.pango, "pango_layout_is_wrapped", C.c_int, C.c_void_p)
        self.is_ellipsized = _bind(
            self.pango, "pango_layout_is_ellipsized", C.c_int, C.c_void_p)


def _finite(values, reason):
    require(all(type(value) in (int, float) and math.isfinite(value)
                for value in values), reason)


def _inside(bounds, box, reason):
    require(len(bounds) == 4, "graph_text_bounds_shape")
    _finite(bounds, "graph_text_bounds_nonfinite")
    require(box["x"] <= bounds[0] <= bounds[2] <= box["x"] + box["width"]
            and box["y"] <= bounds[1] <= bounds[3] <= box["y"] + box["height"],
            reason)


def line_placement(box, font_size, line_count, index, logical, baseline_units):
    """Use authored advance centering and fixed baseline, never ink centering."""
    require(set(box) == {"x", "y", "width", "height"}, "graph_text_box_shape")
    _finite(box.values(), "graph_text_box_nonfinite")
    require(box["width"] > 0 and box["height"] > 0, "graph_text_box_extent")
    require(type(font_size) in (int, float) and math.isfinite(font_size)
            and 4 <= font_size <= 512, "graph_text_font_size")
    require(type(line_count) is int and line_count > 0
            and type(index) is int and 0 <= index < line_count, "graph_text_line_index")
    require(len(logical) == 4 and all(type(value) is int for value in logical)
            and type(baseline_units) is int, "graph_text_native_metrics")
    line_height = 1.2 * font_size
    require(line_count * line_height <= box["height"], "graph_text_line_height_overflow")
    advance = logical[2] / PANGO_SCALE
    require(0 <= advance <= box["width"], "graph_text_advance_overflow")
    start_x = box["x"] + (box["width"] - advance) / 2
    baseline = (box["y"] + box["height"] / 2
                + (index - (line_count - 1) / 2) * line_height + 0.35 * font_size)
    origin = [start_x - logical[0] / PANGO_SCALE,
              baseline - baseline_units / PANGO_SCALE]
    _finite([advance, baseline, *origin], "graph_text_position_nonfinite")
    return {
        "line_start_x_px": start_x, "advance_px": advance,
        "baseline_px": baseline, "layout_origin_px": origin,
        "line_height_px": line_height,
    }


def combine_outline_svgs(lines, width, height):
    """Combine native line paths without changing coordinates or paint.

    Each line uses one live checked layout. IDs and local references are
    namespaced solely to prevent one native SVG's clip IDs shadowing another.
    Original line bytes and their hashes remain available to the custodian.
    """
    require(type(lines) is list and lines, "graph_text_svg_lines_empty")
    root = ET.Element("{" + SVG_NS + "}svg", {
        "version": "1.1", "width": f"{width}px", "height": f"{height}px",
        "viewBox": f"0 0 {width} {height}",
    })
    for number, raw in enumerate(lines):
        validate_vector_svg(raw, width, height)
        source = ET.fromstring(raw)
        identifiers = {}
        for element in source.iter():
            if "id" in element.attrib:
                old = element.attrib["id"]
                require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", old) is not None
                        and old not in identifiers, "graph_text_svg_duplicate_id")
                identifiers[old] = f"line_{number:04d}_{old}"
        for element in source.iter():
            for key, value in tuple(element.attrib.items()):
                if key == "id":
                    element.attrib[key] = identifiers[value]
                elif "url(" in value.lower():
                    match = re.fullmatch(r"url\(\s*#([A-Za-z_][A-Za-z0-9_.-]*)\s*\)", value)
                    require(match is not None and match.group(1) in identifiers,
                            "graph_text_svg_missing_local_target")
                    element.attrib[key] = "url(#" + identifiers[match.group(1)] + ")"
        # Root paint/style/transform attributes have the same group semantics;
        # width/height/viewBox have already been checked and are not reapplied.
        attributes = {key: value for key, value in source.attrib.items()
                      if key not in ("version", "width", "height", "viewBox")}
        group = ET.SubElement(root, "{" + SVG_NS + "}g", attributes)
        for child in list(source):
            group.append(child)
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    require(len(raw) <= MAX_LABEL_SVG_BYTES, "graph_text_composite_svg_budget")
    validate_vector_svg(raw, width, height)
    return raw


def layout_text_block(native, allowed_files, family, editorial_family, text,
                      font_size, box, document_size, color_hex, resource):
    """Return a derived vector block and observations; canonical text stays intact.

    native is the trusted in-process NativeGraphPango instance, not JSON or a
    caller-selected module. allowed_files comes from the fixed verified runtime
    font bundle, whose owner checks the bytes before and after the whole job.
    """
    require(type(text) is str, "graph_text_type")
    require(not any((ord(char) <= 31 and char != "\n") or ord(char) == 127
                    for char in text), "graph_text_control_character")
    encoded = text.encode("utf-8", "strict")
    require(len(encoded) <= MAX_TEXT_BYTES, "graph_text_budget")
    require(editorial_family in ("Instrument Sans Variable", "IBM Plex Mono"),
            "graph_text_editorial_family")
    expected_family = "Instrument Sans" if editorial_family == "Instrument Sans Variable" else "IBM Plex Mono"
    require(family == expected_family and allowed_files, "graph_text_font_binding")
    require(type(resource) is str and 0 < len(resource) <= 256, "graph_text_resource")
    width, height = document_size
    require(type(width) is int and type(height) is int
            and 320 <= width <= 7680 and 240 <= height <= 4320
            and width * height <= 33177600, "graph_text_document_size")
    require(type(color_hex) is str and re.fullmatch(r"#[0-9A-Fa-f]{6}", color_hex),
            "graph_text_color")
    lines = text.split("\n")
    # Reject aggregate overflow before any native call; empty and final LF
    # lines remain present in this count.
    line_placement(box, font_size, len(lines), 0, [0, 0, 0, 0], 0)
    reports, originals = [], []
    original_svg_bytes = 0
    for index, line in enumerate(lines):
        line_bytes = line.encode("utf-8", "strict")
        consumed = []

        def consume(layout, observation):
            nonlocal original_svg_bytes
            require(not consumed, "graph_text_layout_consumed_twice")
            consumed.append(True)
            native_line_count = native.line_count(layout)
            native_wrapped = native.is_wrapped(layout)
            native_ellipsized = native.is_ellipsized(layout)
            require(native_line_count == 1 and not native_wrapped
                    and not native_ellipsized, "graph_text_implicit_line_change")
            before = _native_layout_signature(native, layout)
            require(before["text"] == line_bytes and before["unknown"] == 0
                    and before["ink"] == observation["ink_pango_units"]
                    and before["logical"] == observation["logical_pango_units"],
                    "graph_text_live_layout_changed")
            require(len(before["ink"]) == 4
                    and all(type(value) is int for value in before["ink"]),
                    "graph_text_native_ink")
            placement = line_placement(
                box, font_size, len(lines), index, before["logical"], before["baseline"])
            if line_bytes:
                runs = validate_observation(observation, allowed_files, family, line_bytes)
            else:
                require(observation["unknown_glyph_count"] == 0 and not observation["runs"],
                        "graph_text_empty_line_glyphs")
                require(before["logical"][2] == 0, "graph_text_empty_line_advance")
                runs = []
            ink = before["ink"]
            require(ink[2] >= 0 and ink[3] >= 0, "graph_text_negative_ink")
            has_ink = ink[2] > 0 and ink[3] > 0
            if not has_ink:
                require(ink[2] == 0 and ink[3] == 0, "graph_text_degenerate_ink")
            origin = placement["layout_origin_px"]
            ink_bounds = [origin[0] + ink[0] / PANGO_SCALE,
                          origin[1] + ink[1] / PANGO_SCALE,
                          origin[0] + (ink[0] + ink[2]) / PANGO_SCALE,
                          origin[1] + (ink[1] + ink[3]) / PANGO_SCALE]
            vector = None
            if has_ink:
                _inside(ink_bounds, box, "graph_text_native_ink_overflow")
                raw, vector = vectorize_observed_layout(
                    native, layout, observation, allowed_files, family, line_bytes,
                    document_size, origin, color_hex)
                _inside(vector["outline_bounds_px"], box, "graph_text_native_outline_overflow")
                require(vector["baseline_px"] == placement["baseline_px"],
                        "graph_text_native_baseline_changed")
                require(len(raw) <= MAX_LABEL_SVG_BYTES - original_svg_bytes,
                        "graph_text_original_line_svg_budget")
                original_svg_bytes += len(raw)
                originals.append(raw)
            after = _native_layout_signature(native, layout)
            require(before == after, "graph_text_live_layout_changed")
            reports.append({
                "index": index, "text": line,
                "text_sha256": hashlib.sha256(line_bytes).hexdigest(),
                "text_bytes": len(line_bytes), "placement": placement,
                "native_ink_bounds_px": ink_bounds if has_ink else None,
                "has_ink": has_ink, "runs": runs, "vector": vector,
                "native_layout": {
                    "unknown_glyph_count": before["unknown"],
                    "ink_pango_units": before["ink"],
                    "logical_pango_units": before["logical"],
                    "baseline_pango_units": before["baseline"],
                    "line_count": native_line_count,
                    "is_wrapped": bool(native_wrapped),
                    "is_ellipsized": bool(native_ellipsized),
                },
                "pango_version": observation["pango_version"],
                "fontconfig_version": observation["fontconfig_version"],
            })

        try:
            native.observe(
                list(allowed_files), family, line_bytes, font_size,
                editorial_family == "Instrument Sans Variable", consume_layout=consume)
            require(len(consumed) == 1, "graph_text_live_layout_not_consumed")
        except Rejected as error:
            raise Rejected(f"{resource}:line:{index}:{error}") from error
    raw = combine_outline_svgs(originals, width, height) if originals else None
    return raw, {
        "schema": "motionwright.manim.graph-text-layout-observation/1",
        "status": "OBSERVED_NATIVE_TEXT_GEOMETRY_ONLY",
        "contract_sha256": CONTRACT_SHA256, "resource": resource,
        "text_sha256": hashlib.sha256(encoded).hexdigest(), "text_bytes": len(encoded),
        "literal_line_count": len(lines), "font_size_px": font_size,
        "editorial_family": editorial_family, "native_family": family, "weight": 400,
        "authored_box": dict(box), "lines": reports,
        "svg_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
        "svg_bytes": len(raw) if raw is not None else 0,
        "original_line_svgs": [{
            "sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value),
        } for value in originals],
        "automatic_wrap": False, "automatic_scale": False, "clipping": False,
        "fallback_allowed": False, "manim_frame_verified": False,
        "native_reopen_verified": False, "profile_admitted": False,
        "human_acceptance": False,
    }, originals
