#!/usr/bin/env python3
"""Independent whole-medium HyperFrames readback, never a renderer or a grant.

The diagnostic CLI is an owner tool. Its paths/executables are not a WebView API.
Both MP4s are decoded completely without resampling, scaling or repairing their
clock. Equality proves the observed raster equivalence; selected pixel probes
and source-byte reopen checks have their own, narrower scopes. This module does
not infer glyph legibility, edit permissions, receipt authority or user approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Iterable

MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_CAPTURE_PIXELS = 64 * 1024 * 1024
MAX_PROBE_BYTES = 512 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_REPORT_BYTES = 256 * 1024
REPARSE_POINT = 0x400
SCHEMA = "motionwright.hyperframes.independent-rgba-readback/1"


class ReadbackError(ValueError):
    """A closed technical rejection. Messages intentionally contain no paths."""


def _require(value: bool, code: str) -> None:
    if not value:
        raise ReadbackError(code)


def _integer(value: object, low: int, high: int) -> int:
    _require(type(value) is int and low <= value <= high, "INTEGER_BOUND")
    return value


def _sha(value: str) -> str:
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "DIGEST_REQUIRED")
    return value


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class MediaClock:
    width: int
    height: int
    frame_count: int
    fps_num: int
    fps_den: int

    def validate(self) -> None:
        _integer(self.width, 64, 1920)
        _integer(self.height, 64, 1080)
        _integer(self.frame_count, 1, 180)
        _integer(self.fps_num, 1, 60000)
        _integer(self.fps_den, 1, 1001)
        _require(self.width % 2 == 0 and self.height % 2 == 0, "EVEN_GEOMETRY_REQUIRED")
        _require((self.fps_num, self.fps_den) in {(24, 1), (25, 1), (30, 1), (50, 1), (60, 1), (24000, 1001), (30000, 1001), (60000, 1001)}, "FPS_PROFILE")
        _require(self.frame_count * self.fps_den <= 6 * self.fps_num, "DURATION_BOUND")
        _require(self.width * self.height * self.frame_count <= MAX_CAPTURE_PIXELS, "PIXEL_BUDGET")

    @property
    def fps(self) -> Fraction:
        return Fraction(self.fps_num, self.fps_den)

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 4


@dataclass(frozen=True)
class FileObservation:
    bytes: int
    sha256: str
    identity: tuple[int, int, int, int, int]


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _no_links(path: Path) -> Path:
    _require(path.is_absolute() and ".." not in path.parts, "ABSOLUTE_NORMALIZED_PATH_REQUIRED")
    try:
        for item in reversed((path, *path.parents)):
            info = item.lstat()
            _require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & REPARSE_POINT, "LINK_OR_REPARSE_POINT")
        resolved = path.resolve(strict=True)
        _require(resolved == path, "PATH_RESOLUTION_CHANGED")
        return resolved
    except (OSError, RuntimeError) as error:
        raise ReadbackError("FILE_UNAVAILABLE") from error


def checked_file(path: Path, expected_sha256: str, cap: int = MAX_FILE_BYTES) -> FileObservation:
    """Read at most the initial size plus one byte; retain the final identity.

    This is a bounded observation, not a claim of atomicity over all path races.
    Backend mounts and original-receipt custody must be checked by the consumer.
    """
    _sha(expected_sha256)
    target = _no_links(Path(path))
    try:
        with target.open("rb") as handle:
            before = os.fstat(handle.fileno())
            _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= cap, "REGULAR_FILE_BYTE_BOUND")
            initial_size = before.st_size
            digest = hashlib.sha256()
            consumed = 0
            while True:
                chunk = handle.read(min(1024 * 1024, initial_size - consumed + 1))
                if not chunk:
                    break
                consumed += len(chunk)
                _require(consumed <= initial_size, "FILE_GREW")
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        named = target.lstat()
        _require(consumed == initial_size and _identity(before) == _identity(after) == _identity(named), "FILE_IDENTITY_CHANGED")
        _require(stat.S_ISREG(named.st_mode) and named.st_nlink == 1 and not getattr(named, "st_file_attributes", 0) & REPARSE_POINT, "FILE_KIND_CHANGED")
        actual = digest.hexdigest()
        _require(actual == expected_sha256, "FILE_SHA256_MISMATCH")
        return FileObservation(consumed, actual, _identity(named))
    except OSError as error:
        raise ReadbackError("FILE_READ_FAILED") from error


def _run_bounded(command: list[str], *, stdout_limit: int, timeout: float) -> bytes:
    """Supervise only this read-only decoder invocation, with finite pipe reads."""
    _require(0 < timeout <= 120 and 0 < stdout_limit <= 4 * MAX_CAPTURE_PIXELS + 1, "PROCESS_BOUND")
    streams: list[bytearray] = [bytearray(), bytearray()]
    errors: list[str] = []
    lock = threading.Lock()
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    except OSError as error:
        raise ReadbackError("DECODER_UNAVAILABLE") from error

    def read_pipe(index: int, pipe, limit: int) -> None:
        try:
            while True:
                remaining = limit - len(streams[index]) + 1
                chunk = pipe.read(min(64 * 1024, remaining))
                if not chunk:
                    return
                streams[index].extend(chunk)
                if len(streams[index]) > limit:
                    with lock:
                        errors.append("DECODER_OUTPUT_BOUND")
                    process.kill()
                    return
        except (OSError, ValueError):
            with lock:
                errors.append("DECODER_PIPE_FAILED")

    readers = [threading.Thread(target=read_pipe, args=(0, process.stdout, stdout_limit), daemon=True),
               threading.Thread(target=read_pipe, args=(1, process.stderr, MAX_STDERR_BYTES), daemon=True)]
    started = time.monotonic()
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=timeout)
        for reader in readers:
            reader.join(max(0.0, timeout - (time.monotonic() - started)))
        _require(not any(reader.is_alive() for reader in readers), "DECODER_PIPE_TIMEOUT")
        _require(not errors, errors[0] if errors else "DECODER_PIPE_FAILED")
        _require(process.returncode == 0 and not streams[1], "DECODER_REJECTED_OR_CORRUPT")
        return bytes(streams[0])
    except subprocess.TimeoutExpired as error:
        raise ReadbackError("DECODER_TIMEOUT") from error
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        for reader in readers:
            reader.join(1)


def _json_probe(command: list[str], limit: int = MAX_PROBE_BYTES) -> dict:
    raw = _run_bounded(command, stdout_limit=limit, timeout=30)
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ReadbackError("MALFORMED_PROBE_JSON") from error
    _require(type(parsed) is dict, "PROBE_OBJECT_REQUIRED")
    return parsed


def _fraction(value: object, *, positive: bool = True) -> Fraction:
    _require(type(value) is str and re.fullmatch(r"-?[0-9]+/[1-9][0-9]*", value) is not None and len(value) <= 48, "RATIONAL_PROBE_FIELD")
    parsed = Fraction(value)
    _require((parsed > 0) if positive else (parsed >= 0), "RATIONAL_PROBE_RANGE")
    return parsed


def _probe_int(value: object) -> int:
    if type(value) is int:
        return value
    _require(type(value) is str and len(value) <= 20 and re.fullmatch(r"-?(0|[1-9][0-9]*)", value) is not None, "INTEGER_PROBE_FIELD")
    return int(value)


def validate_stream_probe(probe: dict, clock: MediaClock) -> dict:
    clock.validate()
    streams = probe.get("streams")
    _require(type(streams) is list and len(streams) == 1 and type(streams[0]) is dict, "ONE_VIDEO_STREAM_REQUIRED")
    stream = streams[0]
    _require(stream.get("codec_type") == "video" and stream.get("codec_name") == "h264", "H264_VISUAL_PROFILE")
    _require(type(stream.get("width")) is int and stream["width"] == clock.width
             and type(stream.get("height")) is int and stream["height"] == clock.height, "GEOMETRY_MISMATCH")
    _require(stream.get("pix_fmt") in {"yuv420p", "yuvj420p"}, "SDR_PIXEL_FORMAT")
    _require(_fraction(stream.get("r_frame_rate")) == clock.fps and _fraction(stream.get("avg_frame_rate")) == clock.fps, "FPS_MISMATCH")
    timebase = _fraction(stream.get("time_base"))
    _require(timebase <= 1 and timebase.denominator <= 1_000_000_000, "TIMEBASE_BOUND")
    _require(_probe_int(stream.get("duration_ts")) * timebase == Fraction(clock.frame_count, 1) / clock.fps, "EXACT_DURATION_MISMATCH")
    if stream.get("nb_frames") not in (None, "N/A"):
        _require(_probe_int(stream["nb_frames"]) == clock.frame_count, "DECLARED_FRAME_COUNT_MISMATCH")
    for key in ("color_transfer", "color_primaries", "color_space"):
        _require(stream.get(key) not in {"smpte2084", "arib-std-b67", "bt2020", "bt2020nc", "bt2020c"}, "HDR_OUTSIDE_PROFILE")
    return stream


def validate_frame_probe(probe: dict, stream: dict, clock: MediaClock) -> list[dict]:
    frames = probe.get("frames")
    _require(type(frames) is list and len(frames) == clock.frame_count, "OBSERVED_FRAME_COUNT_MISMATCH")
    timebase = _fraction(stream.get("time_base"))
    index = _probe_int(stream.get("index"))
    for frame_index, frame in enumerate(frames):
        _require(type(frame) is dict and frame.get("media_type") == "video"
                 and _probe_int(frame.get("stream_index")) == index, "FOREIGN_FRAME_STREAM")
        # Best-effort timestamps alone could conceal synthesized/missing PTS.
        timestamp = _probe_int(frame.get("pts")) * timebase
        _require(timestamp == Fraction(frame_index, 1) / clock.fps, "FRAME_CLOCK_GAP_OFFSET_OR_REORDER")
        if "pkt_duration" in frame:
            _require(_probe_int(frame["pkt_duration"]) * timebase == 1 / clock.fps, "FRAME_DURATION_MISMATCH")
    return frames


@dataclass(frozen=True)
class DecodedMedium:
    observation: FileObservation
    clock: MediaClock
    rgba: bytes
    frame_sha256: tuple[str, ...]


def decode_medium(media: Path, expected_sha256: str, clock: MediaClock, *, ffmpeg: Path, ffprobe: Path,
                  ffmpeg_sha256: str, ffprobe_sha256: str) -> DecodedMedium:
    clock.validate()
    before = checked_file(media, expected_sha256)
    checked_file(ffmpeg, ffmpeg_sha256)
    checked_file(ffprobe, ffprobe_sha256)
    stream_probe = _json_probe([str(ffprobe), "-v", "error", "-show_streams", "-of", "json", str(media)])
    stream = validate_stream_probe(stream_probe, clock)
    frame_probe = _json_probe([str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_frames",
                              "-show_entries", "frame=media_type,stream_index,pts,pkt_duration", "-of", "json", str(media)])
    validate_frame_probe(frame_probe, stream, clock)
    expected_bytes = clock.frame_bytes * clock.frame_count
    rgba = _run_bounded([str(ffmpeg), "-hide_banner", "-v", "error", "-nostdin", "-xerror", "-err_detect", "explode",
                         "-threads", "1", "-i", str(media), "-map", "0:v:0", "-an", "-sn", "-dn", "-threads", "1",
                         "-fps_mode", "passthrough", "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1"],
                        stdout_limit=expected_bytes + 1, timeout=120)
    _require(len(rgba) == expected_bytes, "FULL_DECODE_INCOMPLETE_OR_EXCESS")
    _require(checked_file(media, expected_sha256) == before, "MEDIA_CHANGED_DURING_DECODE")
    checked_file(ffmpeg, ffmpeg_sha256)
    checked_file(ffprobe, ffprobe_sha256)
    frame_hashes = tuple(_digest(memoryview(rgba)[offset:offset + clock.frame_bytes])
                         for offset in range(0, len(rgba), clock.frame_bytes))
    return DecodedMedium(before, clock, rgba, frame_hashes)


@dataclass(frozen=True)
class PixelProbe:
    frame: int
    x: int
    y: int
    rgba: tuple[int, int, int, int]
    maximum_channel_error: int = 0

    def validate(self, clock: MediaClock) -> None:
        _integer(self.frame, 0, clock.frame_count - 1)
        _integer(self.x, 0, clock.width - 1)
        _integer(self.y, 0, clock.height - 1)
        _require(type(self.rgba) is tuple and len(self.rgba) == 4, "RGBA_PROBE_SHAPE")
        for value in self.rgba:
            _integer(value, 0, 255)
        _integer(self.maximum_channel_error, 0, 24)


def check_pixels(medium: DecodedMedium, probes: Iterable[PixelProbe]) -> list[dict]:
    values = list(probes)
    _require(2 <= len(values) <= 256, "PIXEL_PROBE_COUNT")
    _require(len({probe.frame for probe in values}) >= 2, "TEMPORAL_PIXEL_PROBES_REQUIRED")
    result = []
    for probe in values:
        probe.validate(medium.clock)
        offset = probe.frame * medium.clock.frame_bytes + 4 * (probe.y * medium.clock.width + probe.x)
        actual = tuple(medium.rgba[offset:offset + 4])
        error = max(abs(left - right) for left, right in zip(actual, probe.rgba))
        _require(error <= probe.maximum_channel_error, "INTENT_PIXEL_SAMPLE_MISMATCH")
        result.append({"frame": probe.frame, "x": probe.x, "y": probe.y, "actual_rgba": actual,
                       "expected_rgba": probe.rgba, "maximum_channel_error": probe.maximum_channel_error, "observed_channel_error": error})
    return result


def compare_media(direct: DecodedMedium, mediated: DecodedMedium, probes: Iterable[PixelProbe]) -> dict:
    _require(direct.clock == mediated.clock, "COMPARISON_CLOCK_MISMATCH")
    _require(len(direct.rgba) == len(mediated.rgba) == direct.clock.frame_bytes * direct.clock.frame_count, "COMPARISON_DECODE_LENGTH")
    _require(direct.frame_sha256 == mediated.frame_sha256 and direct.rgba == mediated.rgba, "WHOLE_RGBA_MISMATCH")
    direct_probes = check_pixels(direct, probes)
    mediated_probes = check_pixels(mediated, [PixelProbe(item["frame"], item["x"], item["y"], tuple(item["expected_rgba"]), item["maximum_channel_error"]) for item in direct_probes])
    return {"schema": SCHEMA, "status": "PASS", "clock": asdict(direct.clock),
            "direct_file_sha256": direct.observation.sha256, "mediated_file_sha256": mediated.observation.sha256,
            "direct_file_bytes": direct.observation.bytes, "mediated_file_bytes": mediated.observation.bytes,
            "decoded_rgba_sha256": _digest(direct.rgba), "frame_sha256": direct.frame_sha256,
            "all_frames_decoded_and_compared": True, "exact_pts_clock_verified": True,
            "selected_intent_pixels": {"direct": direct_probes, "mediated": mediated_probes},
            "evidence_scope": "EXACT_WHOLE_RGBA_EQUIVALENCE_AND_SELECTED_PIXEL_SAMPLES",
            "glyph_legibility_verified": False, "editable_roundtrip_verified": False,
            "original_receipts_verified": False, "export_authorized": False, "human_approval": False}


def inspect_pair(direct: Path, mediated: Path, *, direct_sha256: str, mediated_sha256: str, clock: MediaClock,
                 probes: Iterable[PixelProbe], ffmpeg: Path, ffprobe: Path, ffmpeg_sha256: str, ffprobe_sha256: str) -> dict:
    tools = {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "ffmpeg_sha256": ffmpeg_sha256, "ffprobe_sha256": ffprobe_sha256}
    left = decode_medium(direct, direct_sha256, clock, **tools)
    right = decode_medium(mediated, mediated_sha256, clock, **tools)
    report = compare_media(left, right, probes)
    report["decoder_pins"] = {"ffmpeg_sha256": ffmpeg_sha256, "ffprobe_sha256": ffprobe_sha256}
    return report


def inspect_source_reopen(original: Path, exported: Path, *, source_sha256: str) -> dict:
    left = checked_file(original, source_sha256, MAX_SOURCE_BYTES)
    right = checked_file(exported, source_sha256, MAX_SOURCE_BYTES)
    return {"source_sha256": source_sha256, "original_bytes": left.bytes, "reopened_bytes": right.bytes,
            "preserved_source_bytes_equal": True, "evidence_scope": "SOURCE_BYTE_PRESERVATION_ONLY",
            "source_grammar_readmitted": False, "edited_pixels_verified": False, "editable_roundtrip_verified": False}


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct", type=Path, required=True)
    parser.add_argument("--mediated", type=Path, required=True)
    parser.add_argument("--direct-sha256", required=True)
    parser.add_argument("--mediated-sha256", required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument("--ffmpeg-sha256", required=True)
    parser.add_argument("--ffprobe-sha256", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--fps-num", type=int, required=True)
    parser.add_argument("--fps-den", type=int, required=True)
    parser.add_argument("--pixel-probes", type=Path, required=True)
    parser.add_argument("--pixel-probes-sha256", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        checked_file(args.pixel_probes, args.pixel_probes_sha256, 64 * 1024)
        with args.pixel_probes.open("rb") as handle:
            data = handle.read(64 * 1024 + 1)
        _require(len(data) <= 64 * 1024 and _digest(data) == args.pixel_probes_sha256, "PIXEL_PROBES_CHANGED")
        values = json.loads(data)
        _require(type(values) is list and 2 <= len(values) <= 256, "PIXEL_PROBE_COUNT")
        probes = []
        for value in values:
            _require(type(value) is dict and set(value) == {"frame", "x", "y", "rgba", "maximum_channel_error"}, "CLOSED_PIXEL_PROBE")
            probes.append(PixelProbe(value["frame"], value["x"], value["y"], tuple(value["rgba"]), value["maximum_channel_error"]))
        clock = MediaClock(args.width, args.height, args.frames, args.fps_num, args.fps_den)
        report = inspect_pair(args.direct, args.mediated, direct_sha256=args.direct_sha256, mediated_sha256=args.mediated_sha256,
                              clock=clock, probes=probes, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
                              ffmpeg_sha256=args.ffmpeg_sha256, ffprobe_sha256=args.ffprobe_sha256)
        report["pixel_probe_fixture_sha256"] = args.pixel_probes_sha256
        encoded = (json.dumps(report, indent=2, allow_nan=False) + "\n").encode()
        _require(len(encoded) <= MAX_REPORT_BYTES, "REPORT_BYTE_BOUND")
        _no_links(args.report.parent)
        with args.report.open("xb") as handle:
            handle.write(encoded)
        print(json.dumps({"status": "PASS", "report_bytes": len(encoded), "report_sha256": _digest(encoded), "scope": report["evidence_scope"]}))
        return 0
    except (ReadbackError, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"status": "REJECTED", "scope": "NO_FIDELITY_OR_EXPORT_AUTHORITY"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
