"""Closed native Manim writer proposal; activation requires reviewed runtime schema 2.

Uses the public CairoRenderer/SceneFileWriter hook, not Manim's private jobs.
No MP4 is read or transcoded. Original native RGBA is transformed before encoding.
"""
from __future__ import annotations

import ast
from fractions import Fraction
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import sys

SOURCE_POLICY = "srgb-rgba8-opaque-v1"
COLOR_RECIPE = "srgb-to-rec709-full12-v2"
AV_VERSION = "15.0.0"
MAX_FRAMES = 36_000
MAX_VIDEO_BYTES = 512 * 1024 * 1024
# This recipe uses filters supplied by the pinned PyAV15 wheel. The 12-bit
# full-range intermediate preserves precision before the actual transfer change.
SCALE = (
    "in_color_matrix=bt709:out_color_matrix=bt709:"
    "in_range=pc:out_range=pc:flags=accurate_rnd+full_chroma_int"
)
INTERMEDIATE_FORMAT = "pix_fmts=yuv444p12le"
COLORSPACE = (
    "ispace=bt709:iprimaries=bt709:itrc=srgb:irange=pc:"
    "space=bt709:primaries=bt709:trc=bt709:range=tv:"
    "format=yuv420p:fast=0:dither=none"
)
COLOR_FILTER = ("scale=" + SCALE + ",format=" + INTERMEDIATE_FORMAT
                + ",colorspace=" + COLORSPACE)
REQUIRED_FILTERS = frozenset(("buffer", "scale", "format", "colorspace", "buffersink"))


class ColorProfileError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise ColorProfileError(code)


def closed_profile(width, height, fps, source_policy):
    require(source_policy == SOURCE_POLICY, "unsupported_source_color_policy")
    require(type(width) is int and type(height) is int and type(fps) is int,
            "integer_profile")
    require(0 < width <= 4096 and 0 < height <= 4096
            and width * height <= 8_847_360, "geometry")
    require(width % 2 == 0 and height % 2 == 0, "yuv420_even_geometry")
    require(1 <= fps <= 120, "integer_fps")


def source_camera(source_bytes):
    """The existing profile validator remains authoritative for complete source."""
    require(0 < len(source_bytes) <= 2 * 1024 * 1024, "source_byte_budget")
    tree = ast.parse(source_bytes)
    scenes = [node for node in tree.body
              if isinstance(node, ast.ClassDef) and node.name == "MotionwrightScene"]
    require(len(scenes) == 1 and len(scenes[0].bases) == 1, "scene_class")
    base = scenes[0].bases[0]
    require(isinstance(base, ast.Name)
            and base.id in ("Scene", "MovingCameraScene"), "scene_camera_profile")
    return base.id


def repeat_count(value, written):
    require(type(value) is int and 1 <= value <= MAX_FRAMES, "repeat_count")
    require(written + value <= MAX_FRAMES, "frame_budget")
    return value


def rgba_shape(frame, width, height):
    require(str(getattr(frame, "dtype", "")) == "uint8"
            and getattr(frame, "shape", None) == (height, width, 4),
            "native_rgba8_shape")
    # NumPy is supplied by the already required Manim runtime.
    require(bool((frame[:, :, 3] == 255).all()), "nonopaque_native_frame")


def make_graph(av, width, height, fps, available_filters):
    # Admit actual filter capabilities before any scene or output file exists.
    # A configuration error propagates; there is no tagging or alternate-filter
    # fallback. Native output still requires a separate complete decoder report.
    require(REQUIRED_FILTERS.issubset(available_filters),
            "native_color_filters_unavailable")
    graph = av.filter.Graph()
    nodes = [
        graph.add_buffer(width=width, height=height, format="rgba",
                         time_base=Fraction(1, fps)),
        graph.add("scale", SCALE),
        graph.add("format", INTERMEDIATE_FORMAT),
        graph.add("colorspace", COLORSPACE),
        graph.add("buffersink"),
    ]
    graph.link_nodes(*nodes)
    graph.configure()
    return graph


def writer_type(av, SceneFileWriter, width, height, fps, source_sha256,
                source_path, artifact_path, graph):
    class Rec709Writer(SceneFileWriter):
        def __init__(self, renderer, scene_name, **kwargs):
            self._container = None
            self._stream = None
            self._file = None
            self._finished = False
            self._failed = False
            self._written = 0
            self._packets = 0
            self._rgba_roster = hashlib.sha256(b"motionwright.native-rgba-sequence/1\0")
            super().__init__(renderer, scene_name, **kwargs)
            require(Path(self.movie_file_path).resolve() == artifact_path,
                    "native_output_path_differs")

        def open_partial_movie_stream(self, file_path=None):
            require(not self._finished and not self._failed, "writer_closed")
            if self._container is None:
                # Base writer owns output directories; this file is always fresh.
                self._file = artifact_path.open("xb")
                self._container = av.open(self._file, mode="w", format="mp4")
                self._stream = self._container.add_stream("libx264",
                                                         rate=Fraction(fps, 1))
                self._stream.width = width
                self._stream.height = height
                self._stream.pix_fmt = "yuv420p"
                context = self._stream.codec_context
                # The admitted native camera is a square-pixel RGBA lattice.
                # Declare its geometry before encoding; independent readback
                # must still observe SAR1:1 from the encoded video.
                context.sample_aspect_ratio = Fraction(1, 1)
                context.thread_count = 2
                context.options = {"crf": "23"}
                # These labels describe the actual transformed pixels, never a shortcut.
                context.color_primaries = 1
                context.color_trc = 1
                context.colorspace = 1
                context.color_range = 1
                context.time_base = Fraction(1, fps)

        def close_partial_movie_stream(self):
            # One encoder spans native animation boundaries; no lossy join/reencode.
            require(self._container is not None, "partial_without_encoder")

        def write_frame(self, frame_or_renderer, num_frames=1):
            require(not self._failed and not self._finished, "writer_closed")
            count = repeat_count(num_frames, self._written)
            rgba_shape(frame_or_renderer, width, height)
            self.open_partial_movie_stream()
            digest = hashlib.sha256(frame_or_renderer.tobytes(order="C")).digest()
            for _ in range(count):
                frame = av.VideoFrame.from_ndarray(frame_or_renderer, format="rgba")
                frame.pts = self._written
                frame.time_base = Fraction(1, fps)
                # Native opaque RGBA8 is full-range RGB. The graph performs the
                # sRGB transfer conversion; these input labels do not replace it.
                frame.color_range = 2
                frame.colorspace = 0
                graph.push(frame)
                transformed = graph.pull()
                require(transformed.width == width and transformed.height == height
                        and transformed.format.name == "yuv420p",
                        "transformed_frame_format")
                require(transformed.pts is not None
                        and transformed.pts * transformed.time_base
                        == Fraction(self._written, fps), "transformed_frame_clock")
                require(transformed.colorspace == 1 and transformed.color_range == 1,
                        "transformed_matrix_range")
                # Native output is already YUV420p; PyAV's implicit reformatter
                # receives identical dimensions/format and is therefore bypassed.
                for packet in self._stream.encode(transformed):
                    self._container.mux(packet)
                    self._packets += 1
                self._rgba_roster.update(self._written.to_bytes(8, "big"))
                self._rgba_roster.update(digest)
                self._written += 1
                self.frame_count = self._written
                require(os.fstat(self._file.fileno()).st_size <= MAX_VIDEO_BYTES,
                        "video_byte_budget")

        def finish(self):
            require(not self._finished and not self._failed, "writer_closed")
            require(self._container is not None and self._written > 0, "no_frames")
            require(not self.includes_sound and not self.subcaptions,
                    "native_audio_or_subcaptions_not_in_closed_profile")
            graph.push(None)
            try:
                graph.pull()
            except (av.error.EOFError, av.error.BlockingIOError):
                pass
            else:
                raise ColorProfileError("unexpected_buffered_color_frame")
            for packet in self._stream.encode(None):
                self._container.mux(packet)
                self._packets += 1
            self._container.close()
            self._container = None
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
            self._file = None
            require(hashlib.sha256(source_path.read_bytes()).hexdigest()
                    == source_sha256, "source_changed_during_native_render")
            size = artifact_path.stat().st_size
            require(0 < size <= MAX_VIDEO_BYTES, "video_byte_budget")
            report = {
                "schema": "motionwright.native-manim-color-transform/1",
                "source_sha256": source_sha256,
                "source_policy": SOURCE_POLICY, "recipe": COLOR_RECIPE,
                "filter": COLOR_FILTER,
                "artifact_sha256": hash_file(artifact_path),
                "artifact_bytes": size,
                "native_input_frame_count": self._written,
                "native_input_rgba_roster_sha256": self._rgba_roster.hexdigest(),
                "native_input_rgba_roster_algorithm":
                    "domain-nul+index-u64be+rgba-sha256-bytes",
                "muxed_packet_count": self._packets,
                "width": width, "height": height, "fps_num": fps, "fps_den": 1,
                "encoder": {"name": "PyAV", "version": av.__version__,
                            "libraries": {k: list(v) for k, v in av.library_versions.items()}},
                "pixel_geometry_policy": {"source": "native-square-pixel-camera",
                                          "sample_aspect_ratio_num": 1,
                                          "sample_aspect_ratio_den": 1},
                "output_color": {"matrix": 1, "range": 1, "primaries": 1, "transfer": 1},
                "scope": "producer transform receipt; separate full decoder evidence required",
            }
            report_path = artifact_path.with_suffix(".mp4.color-transform.json")
            payload = (json.dumps(report, sort_keys=True, separators=(",", ":"))
                       + "\n").encode()
            require(len(payload) <= 64 * 1024, "color_receipt_budget")
            with report_path.open("xb") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            self._finished = True

        def abort_encode_jobs(self, reraise_encoder_failures=False):
            self._failed = True
            # Keep the attempted output; a failure is not a fresh attempt or retry.
            if self._container is not None:
                try:
                    self._container.close()
                finally:
                    self._container = None
            if self._file is not None:
                self._file.close()
                self._file = None

    return Rec709Writer


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv):
    require(len(argv) == 7, "fixed_color_launcher_arguments")
    source_path, output_root, output_name, width, height, fps, policy = argv
    width, height, fps = int(width), int(height), int(fps)
    closed_profile(width, height, fps, policy)
    source_path = Path(source_path)
    output_root = Path(output_root)
    require(source_path.is_absolute() and source_path.is_file()
            and not source_path.is_symlink(), "source_path")
    require(output_root.is_absolute() and output_root.is_dir()
            and not output_root.is_symlink(), "output_root")
    require(output_name.startswith("mw_") and output_name.isascii()
            and all(c.isalnum() or c == "_" for c in output_name), "output_name")
    source_bytes = source_path.read_bytes()
    camera_profile = source_camera(source_bytes)
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    artifact_path = (output_root / "videos" / source_path.stem
                     / f"{height}p{fps}" / (output_name + ".mp4")).resolve()
    require(artifact_path.is_relative_to(output_root.resolve()), "output_escape")
    require(not artifact_path.exists()
            and not artifact_path.with_suffix(".mp4.color-transform.json").exists(),
            "fresh_output_required")

    import av
    from av.filter.filter import filters_available
    import numpy as np
    import manim
    from manim import Camera, MovingCamera, Scene, MovingCameraScene, tempconfig
    from manim.renderer.cairo_renderer import CairoRenderer
    from manim.scene.scene_file_writer import SceneFileWriter

    require(manim.__version__ == "0.21.0" and av.__version__ == AV_VERSION,
            "pinned_native_runtime_version")
    graph = make_graph(av, width, height, fps, filters_available)
    writer = writer_type(av, SceneFileWriter, width, height, fps, source_digest,
                         source_path, artifact_path, graph)
    random.seed(0)
    np.random.seed(0)
    config = {
        "renderer": "cairo", "format": "mp4", "media_dir": str(output_root),
        "input_file": str(source_path), "output_file": output_name,
        "pixel_width": width, "pixel_height": height, "frame_rate": fps,
        "disable_caching": True, "write_to_movie": True, "save_last_frame": False,
        "save_sections": False, "transparent": False, "dry_run": False,
        "preview": False, "show_in_file_browser": False, "write_all": False,
        "verbosity": "WARNING", "progress_bar": "none",
    }
    with tempconfig(config):
        spec = importlib.util.spec_from_file_location("motionwright_native_scene", source_path)
        require(spec is not None and spec.loader is not None, "source_loader")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scene_class = getattr(module, "MotionwrightScene", None)
        expected_base = MovingCameraScene if camera_profile == "MovingCameraScene" else Scene
        require(isinstance(scene_class, type) and scene_class.__bases__ == (expected_base,),
                "scene_base_changed")
        camera = MovingCamera if camera_profile == "MovingCameraScene" else Camera
        renderer = CairoRenderer(file_writer_class=writer, camera_class=camera)
        scene_class(renderer=renderer).render()


if __name__ == "__main__":
    main(sys.argv[1:])
