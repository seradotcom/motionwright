"""Closed Linux Pango font-coverage probe over the two original bundled archives.

No font installation, conversion, global Fontconfig change, subprocess, rendering,
profile admission or Graph write is performed. Run inside the existing admitted
runtime with exclusive custody of its read-only font mount. This observation
cannot replace the Manim frame/readback acceptance test.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import sys

from manim_bundled_font_intake import (
    MAX_ARCHIVE, MAX_MEMBER, PACKAGES, Rejected, inspect_archives,
    read_owned_file, require,
)

MAX_TEXT_BYTES = 16 * 1024
MAX_RUNS = 4096
PANGO_SCALE = 1024


class Analysis(C.Structure):
    _fields_ = [
        ("shape_engine", C.c_void_p), ("lang_engine", C.c_void_p),
        ("font", C.c_void_p),
        ("level", C.c_uint8), ("gravity", C.c_uint8),
        ("flags", C.c_uint8), ("script", C.c_uint8),
        ("language", C.c_void_p), ("extra_attrs", C.c_void_p),
    ]


class Item(C.Structure):
    _fields_ = [
        ("offset", C.c_int), ("length", C.c_int), ("num_chars", C.c_int),
        ("analysis", Analysis),
    ]


class GlyphItem(C.Structure):
    _fields_ = [
        ("item", C.POINTER(Item)), ("glyphs", C.c_void_p),
        ("y_offset", C.c_int), ("start_x_offset", C.c_int), ("end_x_offset", C.c_int),
    ]


class Rectangle(C.Structure):
    _fields_ = [(name, C.c_int) for name in ("x", "y", "width", "height")]


class Matrix(C.Structure):
    _fields_ = [(name, C.c_double) for name in ("xx", "xy", "yx", "yy")]


def _bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


class NativePango:
    """Public C ABI only; libraries are fixed runtime SONAMEs, never user paths."""

    def __init__(self):
        require(sys.platform.startswith("linux"), "font_probe_requires_linux_runtime")
        pointer, integer, string = C.c_void_p, C.c_int, C.c_char_p
        try:
            self.fc = C.CDLL("libfontconfig.so.1")
            self.pango = C.CDLL("libpango-1.0.so.0")
            self.cairo = C.CDLL("libpangocairo-1.0.so.0")
            self.pangofc = C.CDLL("libpangoft2-1.0.so.0")
            self.gobject = C.CDLL("libgobject-2.0.so.0")
            self.config_new = _bind(self.fc, "FcConfigCreate", pointer)
            self.config_free = _bind(self.fc, "FcConfigDestroy", None, pointer)
            self.add_font = _bind(self.fc, "FcConfigAppFontAddFile", integer, pointer, string)
            self.pattern_string = _bind(self.fc, "FcPatternGetString", integer,
                                        pointer, string, integer, C.POINTER(string))
            self.pattern_integer = _bind(self.fc, "FcPatternGetInteger", integer,
                                         pointer, string, integer, C.POINTER(integer))
            self.pattern_bool = _bind(self.fc, "FcPatternGetBool", integer,
                                      pointer, string, integer, C.POINTER(integer))
            self.pattern_matrix = _bind(self.fc, "FcPatternGetMatrix", integer,
                                        pointer, string, integer, C.POINTER(C.POINTER(Matrix)))
            self.fc_version = _bind(self.fc, "FcGetVersion", integer)
            self.map_new = _bind(self.cairo, "pango_cairo_font_map_new_for_font_type", pointer, integer)
            self.map_config = _bind(self.pangofc, "pango_fc_font_map_set_config", None, pointer, pointer)
            self.context_new = _bind(self.pango, "pango_font_map_create_context", pointer, pointer)
            self.layout_new = _bind(self.pango, "pango_layout_new", pointer, pointer)
            self.unref = _bind(self.gobject, "g_object_unref", None, pointer)
            self.description_new = _bind(self.pango, "pango_font_description_new", pointer)
            self.description_free = _bind(self.pango, "pango_font_description_free", None, pointer)
            self.description_family = _bind(self.pango, "pango_font_description_set_family", None, pointer, string)
            self.description_weight = _bind(self.pango, "pango_font_description_set_weight", None, pointer, integer)
            self.description_style = _bind(self.pango, "pango_font_description_set_style", None, pointer, integer)
            self.description_size = _bind(self.pango, "pango_font_description_set_absolute_size", None, pointer, C.c_double)
            self.description_variations = _bind(self.pango, "pango_font_description_set_variations", None, pointer, string)
            self.layout_description = _bind(self.pango, "pango_layout_set_font_description", None, pointer, pointer)
            self.layout_text = _bind(self.pango, "pango_layout_set_text", None, pointer, string, integer)
            self.layout_get_text = _bind(self.pango, "pango_layout_get_text", string, pointer)
            self.layout_width = _bind(self.pango, "pango_layout_set_width", None, pointer, integer)
            self.layout_ellipsize = _bind(self.pango, "pango_layout_set_ellipsize", None, pointer, integer)
            self.layout_paragraph = _bind(self.pango, "pango_layout_set_single_paragraph_mode", None, pointer, integer)
            self.attributes_new = _bind(self.pango, "pango_attr_list_new", pointer)
            self.attributes_free = _bind(self.pango, "pango_attr_list_unref", None, pointer)
            self.attribute_fallback = _bind(self.pango, "pango_attr_fallback_new", pointer, integer)
            self.attributes_insert = _bind(self.pango, "pango_attr_list_insert", None, pointer, pointer)
            self.layout_attributes = _bind(self.pango, "pango_layout_set_attributes", None, pointer, pointer)
            self.unknown_glyphs = _bind(self.pango, "pango_layout_get_unknown_glyphs_count", integer, pointer)
            self.layout_extents = _bind(self.pango, "pango_layout_get_extents", None,
                                        pointer, C.POINTER(Rectangle), C.POINTER(Rectangle))
            self.iterator_new = _bind(self.pango, "pango_layout_get_iter", pointer, pointer)
            self.iterator_free = _bind(self.pango, "pango_layout_iter_free", None, pointer)
            self.iterator_run = _bind(self.pango, "pango_layout_iter_get_run_readonly", C.POINTER(GlyphItem), pointer)
            self.iterator_next = _bind(self.pango, "pango_layout_iter_next_run", integer, pointer)
            self.font_pattern = _bind(self.pangofc, "pango_fc_font_get_pattern", pointer, pointer)
            self.font_describe = _bind(self.pango, "pango_font_describe", pointer, pointer)
            self.font_weight = _bind(self.pango, "pango_font_description_get_weight", integer, pointer)
            self.font_style = _bind(self.pango, "pango_font_description_get_style", integer, pointer)
            self.pango_version = _bind(self.pango, "pango_version", integer)
            self.pango_version_string = _bind(self.pango, "pango_version_string", string)
        except (OSError, AttributeError) as error:
            raise Rejected("native_font_api_unavailable") from error
        require(self.pango_version() >= 14800, "pango_1_48_required_for_font_pattern")

    def _string(self, pattern, key):
        value = C.c_char_p()
        require(self.pattern_string(pattern, key, 0, C.byref(value)) == 0 and value.value is not None,
                "native_font_pattern_string_missing")
        return value.value.decode("utf-8", "strict")

    def _run(self, run):
        require(bool(run.contents.item), "native_item_missing")
        item = run.contents.item.contents
        require(bool(item.analysis.font), "native_font_missing")
        pattern = self.font_pattern(item.analysis.font)
        require(bool(pattern), "native_font_pattern_missing")
        index = C.c_int()
        require(self.pattern_integer(pattern, b"index", 0, C.byref(index)) == 0, "native_font_index_missing")
        embolden = C.c_int()
        embolden_status = self.pattern_bool(pattern, b"embolden", 0, C.byref(embolden))
        require(embolden_status in (0, 1), "native_embolden_property_invalid")
        require(embolden_status != 0 or embolden.value == 0, "native_font_synthetic_bold")
        matrix = C.POINTER(Matrix)()
        matrix_status = self.pattern_matrix(pattern, b"matrix", 0, C.byref(matrix))
        require(matrix_status in (0, 1), "native_matrix_property_invalid")
        if matrix_status == 0:
            require(bool(matrix), "native_matrix_missing")
            require((matrix.contents.xx, matrix.contents.xy, matrix.contents.yx, matrix.contents.yy)
                    == (1.0, 0.0, 0.0, 1.0), "native_font_synthetic_transform")
        description = self.font_describe(item.analysis.font)
        require(bool(description), "native_font_description_missing")
        try:
            weight, style = self.font_weight(description), self.font_style(description)
        finally:
            self.description_free(description)
        return {
            "offset": item.offset, "length": item.length, "num_chars": item.num_chars,
            "family": self._string(pattern, b"family"),
            "file": self._string(pattern, b"file"), "face_index": index.value,
            "weight": weight, "style": style,
        }

    def observe(self, font_paths, family, text_bytes, font_size, variable, *, consume_layout=None):
        with ExitStack() as stack:
            config = self.config_new()
            require(bool(config), "native_font_config_failed")
            stack.callback(self.config_free, config)
            for path in font_paths:
                require(self.add_font(config, os.fsencode(path)) != 0, "native_woff2_open_failed")
            # CAIRO_FONT_TYPE_FT=1 explicitly selects the Fontconfig/FreeType map.
            font_map = self.map_new(1)
            require(bool(font_map), "native_freetype_map_unavailable")
            stack.callback(self.unref, font_map)
            self.map_config(font_map, config)
            context = self.context_new(font_map)
            require(bool(context), "native_pango_context_failed")
            stack.callback(self.unref, context)
            layout = self.layout_new(context)
            require(bool(layout), "native_pango_layout_failed")
            stack.callback(self.unref, layout)
            description = self.description_new()
            require(bool(description), "native_description_failed")
            stack.callback(self.description_free, description)
            self.description_family(description, family.encode("utf-8"))
            self.description_weight(description, 400)
            self.description_style(description, 0)
            self.description_size(description, font_size * PANGO_SCALE)
            if variable:
                self.description_variations(description, b"wght=400")
            self.layout_description(layout, description)
            self.layout_width(layout, -1)
            self.layout_ellipsize(layout, 0)
            self.layout_paragraph(layout, 0)
            attributes = self.attributes_new()
            require(bool(attributes), "native_attributes_failed")
            stack.callback(self.attributes_free, attributes)
            fallback = self.attribute_fallback(0)
            require(bool(fallback), "native_fallback_attribute_failed")
            self.attributes_insert(attributes, fallback)
            self.layout_attributes(layout, attributes)
            self.layout_text(layout, text_bytes, len(text_bytes))
            require(self.layout_get_text(layout) == text_bytes, "native_text_roundtrip_mismatch")
            unknown = self.unknown_glyphs(layout)
            ink, logical = Rectangle(), Rectangle()
            self.layout_extents(layout, C.byref(ink), C.byref(logical))
            iterator = self.iterator_new(layout)
            require(bool(iterator), "native_iterator_failed")
            stack.callback(self.iterator_free, iterator)
            runs = []
            while True:
                run = self.iterator_run(iterator)
                if run:
                    require(len(runs) < MAX_RUNS, "native_run_budget")
                    runs.append(self._run(run))
                if not self.iterator_next(iterator):
                    break
            observation = {
                "pango_version": self.pango_version_string().decode("ascii", "strict"),
                "fontconfig_version": self.fc_version(),
                "unknown_glyph_count": unknown, "runs": runs,
                "ink_pango_units": [ink.x, ink.y, ink.width, ink.height],
                "logical_pango_units": [logical.x, logical.y, logical.width, logical.height],
            }
            if consume_layout is not None:
                consume_layout(layout, observation)
            return observation


def validate_observation(observation, allowed_files, family, text_bytes):
    """Validate getters, not caller assertions. Pure tests use labelled mocks."""
    require(observation["unknown_glyph_count"] == 0, "native_unknown_glyphs")
    require(0 < len(observation["runs"]) <= MAX_RUNS, "native_run_count")
    covered = bytearray(len(text_bytes))
    result = []
    for run in observation["runs"]:
        require(run["family"] == family, "native_font_family_substitution")
        require(run["weight"] == 400 and run["style"] == 0 and run["face_index"] == 0,
                "native_font_face_substitution")
        descriptor = allowed_files.get(run["file"])
        require(descriptor is not None, "native_font_file_substitution")
        start, count = run["offset"], run["length"]
        require(type(start) is int and type(count) is int and count > 0
                and 0 <= start < start + count <= len(text_bytes), "native_run_range")
        fragment = text_bytes[start:start + count].decode("utf-8", "strict")
        require(run["num_chars"] == len(fragment), "native_run_character_count")
        require(not any(covered[start:start + count]), "native_overlapping_runs")
        covered[start:start + count] = b"\1" * count
        result.append({
            "offset": start, "length": count, "num_chars": run["num_chars"],
            "family": run["family"], "weight": run["weight"], "style": run["style"],
            "face_index": run["face_index"],
            "font_relative_path": descriptor["relative_path"],
            "font_sha256": descriptor["sha256"],
        })
    require(all(seen or byte == 10 for seen, byte in zip(covered, text_bytes)),
            "native_text_not_fully_itemized")
    return result


def probe(owner_root, font_root, intake, editorial_family, text, font_size):
    require(type(text) is str and text.strip(), "font_probe_text_empty")
    require(not any((ord(char) <= 31 and char != "\n") or ord(char) == 127 for char in text),
            "font_probe_text_control")
    text_bytes = text.encode("utf-8", "strict")
    require(0 < len(text_bytes) <= MAX_TEXT_BYTES, "font_probe_text_budget")
    require(type(font_size) in (int, float) and math.isfinite(font_size)
            and 4.0 <= font_size <= 512.0, "font_probe_size_outside_existing_text_bounds")
    owner_root, font_root = Path(owner_root), Path(font_root)
    require(owner_root.is_absolute() and font_root.is_absolute(), "absolute_font_roots_required")
    require(font_root.is_relative_to(owner_root), "font_root_outside_owner")
    specification = next((item for item in PACKAGES if item["editorial_family"] == editorial_family), None)
    require(specification is not None, "font_family_outside_closed_profile")
    package = next((item for item in intake["packages"] if item["package"] == specification["package"]), None)
    require(package is not None and package["version"] == specification["version"]
            and package["archive"]["integrity"] == specification["integrity"]
            and package["expected_native_family"] == specification["expected_native_family"],
            "font_intake_binding")
    files = package["font_files"]
    require(0 < len(files) <= 32, "font_resource_budget")
    allowed = {}
    for descriptor in files:
        relative = Path(descriptor["relative_path"])
        require(not relative.is_absolute() and ".." not in relative.parts, "font_relative_path")
        path = font_root / relative
        raw = read_owned_file(owner_root, path, MAX_MEMBER)
        require(len(raw) == descriptor["size_bytes"]
                and hashlib.sha256(raw).hexdigest() == descriptor["sha256"], "font_bytes_changed")
        canonical = str(path.resolve(strict=True))
        require(canonical not in allowed, "font_resource_duplicate")
        allowed[canonical] = descriptor
    # The CLI creates intake from the original archives just before this call.
    # No JSON flag or supplied path can manufacture a native observation.
    native = NativePango()
    observation = native.observe(
        list(allowed), specification["expected_native_family"], text_bytes, font_size,
        editorial_family == "Instrument Sans Variable",
    )
    runs = validate_observation(observation, allowed, specification["expected_native_family"], text_bytes)
    for path, descriptor in allowed.items():
        raw = read_owned_file(owner_root, path, MAX_MEMBER)
        require(len(raw) == descriptor["size_bytes"]
                and hashlib.sha256(raw).hexdigest() == descriptor["sha256"], "font_bytes_changed_after_layout")
    return {
        "schema": "motionwright.manim.bundled-font-pango-observation/1",
        "status": "OBSERVED_PANGO_FONT_COVERAGE_ONLY",
        "profile_id": "mw-manim-semantic-graph-v1",
        "editorial_family": editorial_family,
        "native_family": specification["expected_native_family"],
        "font_size_px": font_size, "weight": 400,
        "text_sha256": hashlib.sha256(text_bytes).hexdigest(), "text_bytes": len(text_bytes),
        "unknown_glyph_count": observation["unknown_glyph_count"],
        "fallback_allowed": False,
        "fontconfig_app_font_add_observed": [{
            "relative_path": descriptor["relative_path"], "sha256": descriptor["sha256"],
            "size_bytes": descriptor["size_bytes"],
        } for descriptor in allowed.values()],
        "runs": runs,
        "pango_version": observation["pango_version"],
        "fontconfig_version": observation["fontconfig_version"],
        "ink_pango_units": observation["ink_pango_units"],
        "logical_pango_units": observation["logical_pango_units"],
        "units_per_pixel": PANGO_SCALE,
        "layout_scope": "unwrapped_explicit_newlines_font_coverage_only",
        "box_fit_verified": False, "manim_frame_verified": False,
        "profile_admitted": False, "human_acceptance": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--instrument-archive", required=True)
    parser.add_argument("--mono-archive", required=True)
    parser.add_argument("--font-root", required=True)
    parser.add_argument("--family", required=True, choices=[item["editorial_family"] for item in PACKAGES])
    parser.add_argument("--text", required=True)
    parser.add_argument("--font-size", type=float, default=20.0)
    args = parser.parse_args()
    intake = inspect_archives(
        read_owned_file(args.root, args.lock, 2 * 1024 * 1024),
        {
            "instrument-sans": read_owned_file(args.root, args.instrument_archive, MAX_ARCHIVE),
            "ibm-plex-mono": read_owned_file(args.root, args.mono_archive, MAX_ARCHIVE),
        },
    )
    observation = probe(args.root, args.font_root, intake, args.family, args.text, args.font_size)
    encoded = (json.dumps(observation, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    require(len(encoded) <= 128 * 1024, "font_probe_output_budget")
    sys.stdout.buffer.write(encoded)


if __name__ == "__main__":
    main()
