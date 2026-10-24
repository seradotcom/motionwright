"""Complete observation of one original native video inside the sealed Manim runner.

This module does not render, mix, change color tags, create a project, or authorize
a provider call. It reads the exact output through an already-open regular file.
Unknown color metadata remains unknown. The hybrid admission layer must reject
unsupported color/alpha/audio states instead of filling them from authoring data.
"""
from fractions import Fraction
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import struct
import sys
import zlib

SCHEMA = "motionwright.native-video-observation/1"
MAX_VIDEO_BYTES = 512 * 1024 * 1024
MAX_SOURCE_BYTES = 256 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_FRAMES = 36_000
MAX_PIXELS = 33_177_600
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_PNG_TOTAL_BYTES = 1024 * 1024 * 1024


class ObservationError(ValueError):
    pass


def require(condition, category):
    if not condition:
        raise ObservationError(category)


def file_identity(handle, maximum):
    before = os.fstat(handle.fileno())
    require(stat.S_ISREG(before.st_mode), "not_regular")
    require(0 < before.st_size <= maximum, "byte_budget")
    handle.seek(0)
    digest = hashlib.sha256()
    count = 0
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        count += len(chunk)
        require(count <= maximum, "byte_budget")
        digest.update(chunk)
    after = os.fstat(handle.fileno())
    require(count == before.st_size == after.st_size, "size_changed")
    require(
        (before.st_dev, before.st_ino, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_mtime_ns),
        "file_changed",
    )
    handle.seek(0)
    return {"bytes": count, "sha256": digest.hexdigest()}


def integer_metadata(value):
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 <= number <= 255 else None


def color_metadata(value):
    return {
        "matrix": integer_metadata(getattr(value, "colorspace", None)),
        "range": integer_metadata(getattr(value, "color_range", None)),
        "primaries": integer_metadata(getattr(value, "color_primaries", None)),
        "transfer": integer_metadata(getattr(value, "color_trc", None)),
    }


def rational(value, category):
    require(value is not None, category)
    try:
        rate = Fraction(value)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as error:
        raise ObservationError(category) from error
    require(0 < rate.numerator <= 240_000 and 0 < rate.denominator <= 100_000, category)
    return rate


def presentation_metadata(context, frame):
    # PyAV15 exposes SAR on the codec context, not on VideoFrame. Read that
    # actual decoder context for every yielded frame; keep absent values null.
    # This is not an av_guess_sample_aspect_ratio or an expected-profile value.
    aspect = getattr(context, "sample_aspect_ratio", None)
    try:
        value = Fraction(aspect) if aspect is not None else None
        if value is None or not (0 < value.numerator <= 100_000 and 0 < value.denominator <= 100_000):
            value = None
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        value = None
    interlaced = getattr(frame, "interlaced_frame", None)
    return {
        "scope": "pyav-codec-context-per-frame-and-frame-interlace-v1",
        "sample_aspect_num": value.numerator if value is not None else None,
        "sample_aspect_den": value.denominator if value is not None else None,
        "interlaced_frame": interlaced if isinstance(interlaced, bool) else None,
    }



NATIVE_METADATA_SCOPE = "ffmpeg-showinfo-avframe-color-clock-yuv-passthrough-v1"
NATIVE_LOG_NAME = "mw_native_frame_metadata"
MAX_NATIVE_LOG_BYTES = 64 * 1024
MAX_NATIVE_LOG_FRAGMENTS = 16_384
NATIVE_HEADER = re.compile(
    r"(?:^|\n)n:\s*(\d+)\s+pts:\s*(-?\d+|NOPTS)\s+pts_time:\S+\s+"
    r"duration:\s*(-?\d+)\s+duration_time:\S+\s+fmt:(\S+)\s+cl:(\S+)\s+"
    r"sar:(-?\d+)/(\d+)\s+s:(\d+)x(\d+)\s+i:([PTB])\s+iskey:([01])\s+type:(\S+)"
)
NATIVE_COLOR = re.compile(
    r"(?:^|\n)color_range:(\S+)\s+color_space:(\S+)\s+"
    r"color_primaries:(\S+)\s+color_trc:(\S+)"
)


class BoundedNativeLogHandler(logging.Handler):
    """PyAV15's public logging path, with a bound before retaining messages.

    PyAV logging.pyx sends native fragments to standard logging when no Capture
    is active. Its C callback limits each fragment to 1023 bytes. This handler
    additionally bounds total bytes and fragment count per push/pull, drops all
    further text on overflow and rejects after returning from the C callback.
    It never raises from emit: PyAV would catch/discard that exception.
    """
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.reset()

    def reset(self):
        self.data = bytearray()
        self.fragments = 0
        self.overflow = False

    def emit(self, record):
        if self.overflow:
            return
        self.fragments += 1
        if (self.fragments > MAX_NATIVE_LOG_FRAGMENTS
                or not isinstance(record.msg, str) or record.args
                or len(record.msg) > MAX_NATIVE_LOG_BYTES):
            self.overflow = True
            return
        message = record.msg.encode("utf-8", errors="replace")
        # PyAV strips each fragment. A separator preserves the whitespace that
        # showinfo's independently logged fields need; no field is synthesized.
        if len(self.data) + len(message) + 1 > MAX_NATIVE_LOG_BYTES:
            self.overflow = True
            return
        self.data.extend(message)
        self.data.append(10)

    def text(self):
        require(not self.overflow, "native_metadata_log_budget")
        return self.data.decode("utf-8")


def yuv420_sha256(frame):
    """Hash all visible Y/U/V bytes, excluding decoder line padding."""
    require(frame.format.name == "yuv420p", "native_metadata_pixel_format")
    require(frame.width % 2 == frame.height % 2 == 0, "native_metadata_geometry")
    require(len(frame.planes) == 3, "native_metadata_plane_count")
    digest = hashlib.sha256()
    for plane, columns, rows in zip(
            frame.planes,
            (frame.width, frame.width // 2, frame.width // 2),
            (frame.height, frame.height // 2, frame.height // 2)):
        view = memoryview(plane)
        stride = int(plane.line_size)
        require(stride >= columns and view.nbytes >= stride * rows,
                "native_metadata_plane_bounds")
        for row in range(rows):
            digest.update(view[row * stride:row * stride + columns])
    return digest.hexdigest()


def native_frame_metadata(text, index, pts, time_base, width, height, yuv_sha256):
    """Read bounded native integer/color fields; pts_time is never an oracle."""
    require(len(text.encode("utf-8")) <= MAX_NATIVE_LOG_BYTES, "native_metadata_log_budget")
    headers, colors = list(NATIVE_HEADER.finditer(text)), list(NATIVE_COLOR.finditer(text))
    require(len(headers) == len(colors) == 1 and colors[0].start() > headers[0].start(),
            "native_metadata_incomplete_or_duplicate")
    h, c = headers[0], colors[0]
    require(all(len(h[i]) <= 20 for i in (1, 2, 3, 6, 7, 8, 9)),
            "native_metadata_integer_budget")
    require(h[2] != "NOPTS", "native_metadata_missing_pts")
    native_index, native_pts, duration = int(h[1]), int(h[2]), int(h[3])
    require(native_index == index and native_pts == pts, "native_metadata_ordinal_or_pts")
    require(0 <= native_pts <= 2**63 - 1 and 0 <= duration <= 2**63 - 1,
            "native_metadata_clock_bounds")
    require((int(h[8]), int(h[9])) == (width, height), "native_metadata_geometry")
    require(h[4] == "yuv420p", "native_metadata_pixel_format")
    tb = Fraction(time_base)
    require(0 < tb.numerator <= 1_000_000_000 and 0 < tb.denominator <= 1_000_000_000,
            "native_metadata_time_base")
    # These are values logged from C AVFrame fields, not codec-context defaults.
    # Unknown or unsupported names remain unknown and cannot acquire admission.
    color = {
        "range": {"tv": 1, "pc": 2}.get(c[1]),
        "matrix": {"bt709": 1}.get(c[2]),
        "primaries": {"bt709": 1}.get(c[3]),
        "transfer": {"bt709": 1}.get(c[4]),
    }
    require(isinstance(yuv_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", yuv_sha256),
            "native_metadata_pixel_hash")
    return {
        "scope": NATIVE_METADATA_SCOPE,
        "index": native_index,
        "pts": native_pts,
        "time_base_num": tb.numerator,
        "time_base_den": tb.denominator,
        "duration": duration,
        "color": color,
        "yuv_sha256": yuv_sha256,
    }


class NativeFrameMetadataReader:
    """Bounded metadata passthrough inside the original observer process.

    No Capture, subprocess, ctypes, encoder, source edit or codec-context fill.
    FFmpeg's buffersrc can fill missing matrix/range, so graph entry requires
    those fields already observed. FFmpeg also copies filter-link SAR to frames;
    showinfo SAR is therefore deliberately excluded from this observation.
    Existing presentation_metadata observes codec SAR and frame interlacing.
    """
    def __init__(self, av_module):
        self.av = av_module
        filters = getattr(getattr(av_module, "filter", None), "filters_available", ())
        log = getattr(av_module, "logging", None)
        self.available = (
            getattr(av_module, "__version__", None) == "15.0.0"
            and all(name in filters for name in ("buffer", "showinfo", "buffersink"))
            and log is not None
            and all(callable(getattr(log, name, None)) for name in (
                "get_level", "set_level", "set_libav_level",
                "get_skip_repeated", "set_skip_repeated"))
        )
        self.graph = None
        self.enabled = None
        self.next_index = 0
        self.handler = BoundedNativeLogHandler()
        self.logger = None
        self.last_log_stats = None

    def __enter__(self):
        if not self.available:
            return self
        self.logger = logging.getLogger("libav." + NATIVE_LOG_NAME)
        self.saved_logger = (
            self.logger.handlers[:], self.logger.level,
            self.logger.propagate, self.logger.disabled,
        )
        self.old_level = self.av.logging.get_level()
        self.old_skip = self.av.logging.get_skip_repeated()
        try:
            self.logger.handlers = [self.handler]
            self.logger.setLevel(logging.DEBUG)
            self.logger.propagate = False
            self.logger.disabled = False
            self.av.logging.set_level(self.av.logging.INFO)
            # This original observer is an isolated process. The libav C level
            # has no public getter; INFO is explicit process policy, not a claim
            # that an unknown prior C-level value can be read or restored.
            self.av.logging.set_libav_level(self.av.logging.INFO)
            self.av.logging.set_skip_repeated(False)
        except BaseException:
            self.close()
            raise
        return self

    def observe(self, frame, index):
        require(index == self.next_index, "native_metadata_sequence")
        self.next_index += 1
        eligible = (
            self.available and frame.format.name == "yuv420p"
            and frame.width % 2 == frame.height % 2 == 0
            and color_metadata(frame)["matrix"] == 1
            and color_metadata(frame)["range"] == 1
        )
        if self.enabled is None:
            self.enabled = eligible
        if not self.enabled:
            return None
        require(eligible, "native_metadata_changing_source")
        pts, tb = frame.pts, Fraction(frame.time_base)
        original_color = color_metadata(frame)
        original_interlaced = getattr(frame, "interlaced_frame", None)
        before = yuv420_sha256(frame)
        if self.graph is None:
            self.graph = self.av.filter.Graph()
            source = self.graph.add(
                "buffer", name="mw_native_metadata_source",
                video_size=f"{frame.width}x{frame.height}", pix_fmt=str(int(frame.format)),
                time_base=str(tb), pixel_aspect="0/1",
                colorspace=str(frame.colorspace), range=str(frame.color_range),
            )
            node = self.graph.add("showinfo", "checksum=0", name=NATIVE_LOG_NAME)
            sink = self.graph.add("buffersink", name="mw_native_metadata_sink")
            self.graph.link_nodes(source, node, sink)
            self.graph.configure()
        self.handler.reset()
        self.graph.push(frame)
        passed = self.graph.pull()
        text = self.handler.text()
        self.last_log_stats = {
            "fragments": self.handler.fragments,
            "stored_bytes_including_separators": len(self.handler.data),
            "overflow": self.handler.overflow,
        }
        observed = native_frame_metadata(text, index, pts, tb, frame.width, frame.height, before)
        require(passed.pts == pts and Fraction(passed.time_base) == tb,
                "native_metadata_clock_changed")
        require(color_metadata(passed) == original_color,
                "native_metadata_api_color_changed")
        require(getattr(passed, "interlaced_frame", None) == original_interlaced,
                "native_metadata_interlace_changed")
        require(yuv420_sha256(passed) == before and yuv420_sha256(frame) == before,
                "native_metadata_pixels_changed")
        return observed

    def finish(self):
        if self.graph is None:
            return
        self.handler.reset()
        self.graph.push(None)
        try:
            self.graph.pull()
        except self.av.error.EOFError:
            pass
        else:
            raise ObservationError("native_metadata_unexpected_buffered_frame")
        self.handler.text()

    def close(self):
        if self.logger is not None:
            handlers, level, propagate, disabled = self.saved_logger
            self.logger.handlers = handlers
            self.logger.setLevel(level)
            self.logger.propagate, self.logger.disabled = propagate, disabled
            self.logger = None
            try:
                self.av.logging.set_skip_repeated(self.old_skip)
            finally:
                self.av.logging.set_level(self.old_level)
        self.handler.close()

    def __exit__(self, *_):
        self.close()
        return False


def png_rgba(width, height, pixels):
    require(len(pixels) == width * height * 4, "rgba_bytes")
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(
            ">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    compress = zlib.compressobj(level=6)
    compressed = bytearray()
    stride = width * 4
    for start in range(0, len(pixels), stride):
        compressed.extend(compress.compress(bytes([0]) + pixels[start:start + stride]))
    compressed.extend(compress.flush())
    png = (
        bytes([137, 80, 78, 71, 13, 10, 26, 10])
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", bytes(compressed)) + chunk(b"IEND", b"")
    )
    require(8 < len(png) <= MAX_PNG_BYTES, "png_byte_budget")
    return png


def retain_frame(directory, index, width, height, packed):
    png = png_rgba(width, height, packed)
    name = f"{index:06}.png"
    with (directory / name).open("xb") as out:
        out.write(png)
        out.flush()
        os.fsync(out.fileno())
    return {"file": name, "png_sha256": hashlib.sha256(png).hexdigest(), "png_bytes": len(png)}


def decode_video(handle, av_module, frame_directory):
    """Observe actual decoded frames; no expected width/rate/count is accepted."""
    identity = file_identity(handle, MAX_VIDEO_BYTES)
    frame_records = []
    aggregate = hashlib.sha256(b"motionwright.native-rgba-sequence/1\0")
    opaque = True
    retained_bytes = 0
    alpha_min, alpha_max = 255, 0
    frame_color = None
    with NativeFrameMetadataReader(av_module) as native_reader, av_module.open(handle, mode="r") as container:
        streams = list(container.streams)
        require(0 < len(streams) <= 16, "stream_budget")
        video = [stream for stream in streams if stream.type == "video"]
        audio = [stream for stream in streams if stream.type == "audio"]
        require(len(video) == 1, "video_stream_count")
        require(all(stream.type in ("video", "audio") for stream in streams), "unsupported_stream")
        stream = video[0]
        context = stream.codec_context
        width, height = int(context.width), int(context.height)
        require(16 <= width <= 7680 and 16 <= height <= 4320, "geometry")
        require(width * height <= MAX_PIXELS, "pixel_budget")
        rate = rational(stream.average_rate, "frame_rate")
        require(rate <= 240, "frame_rate")
        context_color = color_metadata(context)
        pixel_format = context.format.name if context.format is not None else None
        require(isinstance(pixel_format, str) and 0 < len(pixel_format) <= 32, "pixel_format")
        codec = context.name
        require(isinstance(codec, str) and 0 < len(codec) <= 32, "codec")
        for index, frame in enumerate(container.decode(stream)):
            require(index < MAX_FRAMES, "frame_budget")
            require(frame.width == width and frame.height == height, "changing_geometry")
            require(frame.format.name == pixel_format, "changing_pixel_format")
            time_base = rational(frame.time_base, "time_base")
            require(isinstance(frame.pts, int), "missing_pts")
            pts = Fraction(frame.pts) * time_base
            require(pts == Fraction(index, 1) / rate, "nonuniform_pts")
            current_color = color_metadata(frame)
            if frame_color is None:
                frame_color = current_color
            else:
                require(current_color == frame_color, "changing_color")
            native_metadata = native_reader.observe(frame, index)
            # This is the decoder's measured RGBA output, never an assertion that
            # absent matrix/transfer tags mean Rec.709. Admission checks tags.
            conversion = {}
            if current_color["matrix"] in (1, 4, 5, 6, 7):
                conversion["src_colorspace"] = current_color["matrix"]
                # swscale's RGB destination uses its default coefficient table.
                # An explicit destination avoids PyAV15's same-matrix shortcut
                # for the admitted BT.709 input; no source tag is invented.
                conversion["dst_colorspace"] = 5
            if current_color["range"] in (1, 2):
                conversion["src_color_range"] = current_color["range"]
                conversion["dst_color_range"] = 2
            rgba = frame.to_ndarray(format="rgba", **conversion)
            require(
                rgba.shape == (height, width, 4)
                and str(rgba.dtype) == "uint8",
                "rgba_shape",
            )
            low, high = int(rgba[:, :, 3].min()), int(rgba[:, :, 3].max())
            alpha_min, alpha_max = min(alpha_min, low), max(alpha_max, high)
            opaque = opaque and low == high == 255
            packed = rgba.tobytes(order="C")
            require(len(packed) == width * height * 4, "rgba_bytes")
            retained = retain_frame(frame_directory, index, width, height, packed)
            retained_bytes += retained["png_bytes"]
            require(retained_bytes <= MAX_PNG_TOTAL_BYTES, "png_aggregate_budget")
            frame_digest = hashlib.sha256(packed).digest()
            aggregate.update(struct.pack(">Q", index))
            aggregate.update(frame_digest)
            frame_records.append(
                {
                    "index": index,
                    "pts_num": pts.numerator,
                    "pts_den": pts.denominator,
                    "rgba_sha256": frame_digest.hex(),
                    "presentation": presentation_metadata(context, frame),
                    "native_metadata": native_metadata,
                    **retained,
                }
            )
        native_reader.finish()
        require(bool(frame_records), "empty_video")
        # Do not replace counted frames with container.nb_frames or duration.
        result = {
            "schema": SCHEMA,
            "artifact_sha256": identity["sha256"],
            "artifact_bytes": identity["bytes"],
            "decoder": {
                "name": "PyAV",
                "version": str(av_module.__version__),
                "libraries": {
                    key: list(value)
                    for key, value in sorted(av_module.library_versions.items())
                },
            },
            "video_streams": len(video),
            "audio_streams": len(audio),
            "width": width,
            "height": height,
            "fps_num": rate.numerator,
            "fps_den": rate.denominator,
            "codec": codec,
            "pixel_format": pixel_format,
            "stream_color": context_color,
            "frame_color": frame_color,
            "frame_count": len(frame_records),
            "first_frame": 0,
            "end_frame_exclusive": len(frame_records),
            "rgba_bytes_per_frame": width * height * 4,
            "retained_png_bytes": retained_bytes,
            "rgba_sequence_sha256": aggregate.hexdigest(),
            "alpha_min": alpha_min,
            "alpha_max": alpha_max,
            "all_pixels_opaque": opaque,
            "rgba_conversion": "pyav-observed-matrix-range-or-unverified-default-to-rgba8",
            "frames": frame_records,
        }
    require(file_identity(handle, MAX_VIDEO_BYTES) == identity, "artifact_changed_after_decode")
    return result


def read_regular(path, maximum):
    path = Path(path)
    require(path.is_absolute(), "absolute_path_required")
    require(not path.is_symlink(), "symlink")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    # Unbuffered FileIO prevents a post-decode hash from reusing stale read-ahead.
    handle = os.fdopen(os.open(path, flags), "rb", buffering=0)
    try:
        file_identity(handle, maximum)
    except BaseException:
        handle.close()
        raise
    return handle


def run(video_path, source_path, report_path):
    """Paths are exact original-job paths selected by the sealed Rust runner."""
    import av

    frame_directory = Path(str(report_path) + ".frames")
    frame_directory.mkdir(exist_ok=False)
    with read_regular(source_path, MAX_SOURCE_BYTES) as source:
        source_identity = file_identity(source, MAX_SOURCE_BYTES)
        with read_regular(video_path, MAX_VIDEO_BYTES) as video:
            observation = decode_video(video, av, frame_directory)
        require(file_identity(source, MAX_SOURCE_BYTES) == source_identity, "source_changed")
    observation["source_sha256"] = source_identity["sha256"]
    encoded = (json.dumps(observation, sort_keys=True, separators=(",", ":")) + "\n").encode()
    require(len(encoded) <= MAX_REPORT_BYTES, "report_budget")
    with Path(report_path).open("xb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    return {"schema": SCHEMA, "sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded)}


if __name__ == "__main__":
    require(len(sys.argv) == 4, "exact_arguments_required")
    try:
        print(json.dumps(run(*sys.argv[1:]), sort_keys=True))
    except BaseException as error:
        # No raw decoder diagnostics, local paths, or argv cross this boundary.
        category = str(error) if isinstance(error, ObservationError) else "decoder_unavailable"
        print(json.dumps({"schema": SCHEMA, "status": "unavailable", "category": category}))
        raise SystemExit(2)
