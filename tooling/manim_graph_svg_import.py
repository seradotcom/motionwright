"""Origin-preserving SVG import inside the existing Manim 0.21 Cairo process.

The input is a Cairo outline result from the live, verified Pango layout. Graph
JSON never accepts an SVG path, Python program, library path or source filename.
Generated SVG and partial Manim parser files stay in the existing owned work
mount for the job custodian; no cleanup/retry is performed here.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import struct

from manim import ORIGIN, RIGHT, UP, SVGMobject
from manim import __version__ as MANIM_VERSION

from manim_bundled_font_intake import read_owned_file
from manim_graph_font_probe import require
from manim_graph_vector_text import MAX_LABEL_SVG_BYTES, validate_vector_svg

MAX_LABEL_POINTS = 131072
MAX_LABELS = 256


class GraphOutlineMobject(SVGMobject):
    """Keep authored SVG coordinates; reuse Manim's parser and vector objects."""

    def __init__(self, source, *, name):
        require(MANIM_VERSION == "0.21.0", "graph_manim_version_mismatch")
        self._graph_importing_svg = True
        self._graph_origin_flip_count = 0
        try:
            super().__init__(
                file_name=source, should_center=False, height=None, width=None,
                use_svg_cache=False, stroke_width=0, name=name,
            )
        finally:
            self._graph_importing_svg = False
        require(self._graph_origin_flip_count == 1, "graph_svg_import_flip_contract_changed")

    def flip(self, axis=UP, *, about_point=None, about_edge=None):
        if self._graph_importing_svg:
            require(tuple(float(value) for value in axis) == tuple(float(value) for value in RIGHT)
                    and about_point is None and about_edge is None,
                    "graph_svg_import_flip_contract_changed")
            self._graph_origin_flip_count += 1
            require(self._graph_origin_flip_count == 1, "graph_svg_import_flip_repeated")
            # SVGMobject 0.21 calls flip(RIGHT), whose default pivot is its own
            # center. Replacing only that implicit pivot preserves SVG origin.
            # Exact reflection avoids the spurious Z from sin(pi).
            return self.apply_matrix(
                [[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]],
                about_point=ORIGIN,
            )
        return super().flip(axis, about_point=about_point, about_edge=about_edge)


def create_label_directory(generated_source):
    source = Path(generated_source)
    require(source.is_absolute() and source.parent == Path.cwd(),
            "graph_source_not_in_current_work_directory")
    # The fixed editable bundle entry is scene.py; the qualified Driver keeps
    # using mw_<uuid>.py. This does not admit another JSON path or imported code.
    require(source.name == "scene.py"
            or re.fullmatch(r"mw_[0-9a-f]{32}\.py", source.name),
            "graph_source_not_generated_job_name")
    metadata = source.lstat()
    require(source.is_file() and not source.is_symlink()
            and metadata.st_nlink == 1, "graph_source_not_owned_regular_file")
    target = source.with_suffix(".graph-labels")
    # Exclusive creation means an interrupted/unknown attempt is never reused.
    target.mkdir(mode=0o700)
    return target


def import_vector_label(directory, ordinal, raw_svg, vector_report, document_size):
    require(type(ordinal) is int and 0 <= ordinal < MAX_LABELS, "graph_label_ordinal")
    width, height = document_size
    validate_vector_svg(raw_svg, width, height)
    require(hashlib.sha256(raw_svg).hexdigest() == vector_report["svg_sha256"]
            and len(raw_svg) == vector_report["svg_bytes"], "graph_svg_result_binding")
    directory = Path(directory)
    require(directory.is_absolute() and directory.is_dir() and not directory.is_symlink(),
            "graph_label_directory_not_owned")
    path = directory / f"label_{ordinal:04d}.svg"
    modified = path.with_name(path.stem + "_" + path.suffix)
    require(not path.exists() and not modified.exists(), "graph_label_attempt_already_exists")
    with path.open("xb") as target:
        target.write(raw_svg)
        target.flush()
        os.fsync(target.fileno())
    item = GraphOutlineMobject(path, name=f"mw_graph_label_{ordinal:04d}")
    # Only this explicit viewport-origin translation follows the Y reflection.
    # One Manim unit is one authored pixel; the caller verifies the camera.
    item.shift([-width / 2.0, height / 2.0, 0.0])
    require(read_owned_file(directory, path, MAX_LABEL_SVG_BYTES) == raw_svg,
            "graph_svg_changed_during_manim_import")
    require(not modified.exists(), "graph_svg_parser_temp_not_released")
    raw_points = item.get_all_points()
    require(0 < len(raw_points) <= MAX_LABEL_POINTS, "graph_svg_point_budget")
    digest = hashlib.sha256()
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    for row in raw_points:
        require(len(row) == 3, "graph_svg_point_shape")
        point = tuple(float(value) for value in row)
        require(all(math.isfinite(value) for value in point), "graph_svg_point_nonfinite")
        require(point[2] == 0.0, "graph_svg_point_not_planar")
        digest.update(struct.pack("<ddd", *point))
        for axis in range(3):
            minimum[axis] = min(minimum[axis], point[axis])
            maximum[axis] = max(maximum[axis], point[axis])
    imported = {
        "schema": "motionwright.manim.graph-svg-import-observation/1",
        "manim_version": MANIM_VERSION,
        "svg_sha256": vector_report["svg_sha256"],
        "svg_bytes": len(raw_svg), "private_svg_name": path.name,
        "origin_reflection": [1, -1, 1],
        "viewport_translation": [-width / 2.0, height / 2.0, 0.0],
        "scale": 1, "automatic_centering": False, "automatic_fit": False,
        "point_count": len(raw_points), "point_encoding": "xyz-f64le",
        "points_sha256": digest.hexdigest(),
        "control_point_min": minimum, "control_point_max": maximum,
        "control_bounds_are_glyph_ink_bounds": False,
        "manim_frame_verified": False, "native_reopen_verified": False,
        "profile_admitted": False, "human_acceptance": False,
    }
    return item, imported


def require_pixel_camera(camera, width, height, fps):
    require(camera.pixel_width == width and camera.pixel_height == height,
            "graph_native_camera_pixel_mapping_mismatch")
    values = {
        "pixel_width": int(camera.pixel_width),
        "pixel_height": int(camera.pixel_height),
        "frame_width": float(camera.frame_width),
        "frame_height": float(camera.frame_height),
        "frame_rate": float(camera.frame_rate),
        "frame_center": [float(value) for value in camera.frame_center],
        "cairo_line_width_multiple": float(camera.cairo_line_width_multiple),
    }
    require(values == {
        "pixel_width": width, "pixel_height": height,
        "frame_width": float(width), "frame_height": float(height),
        "frame_rate": float(fps), "frame_center": [0.0, 0.0, 0.0],
        "cairo_line_width_multiple": 1.0,
    }, "graph_native_camera_pixel_mapping_mismatch")
    return values
