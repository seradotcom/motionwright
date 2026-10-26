#!/usr/bin/env python3
"""Evaluate one pinned upstream on disposable CI; never adopt a renderer.

The untouched hello-world binary supplies the visual/cold/warm baseline. A
separately named synthetic example exercises that same upstream's AudioMap and
audio-render CLI. Raw process records and original media stay in the private
evidence root; public_artifact seals that root with the existing CI custodian.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import tomllib
import traceback
import wave

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tooling") not in sys.path:
    sys.path.insert(0, str(ROOT / "tooling"))
from manim_native_readback import run_bounded
from native_motion_canvas_roundtrip import validate_png_crc_bytes

PIN = "e2b552892c0373a5c8dc79de9280a3289670f27b"
REPO = "https://github.com/dmtrKovalenko/fframes.git"
APP = "hello_world_example_bin"
PKG = "hello-world-example"
AUDIO_APP = "motionwright_audio_probe"
OUT = ROOT / "verification/fframes-upstream-experiment"
CHECKOUT = ROOT / "verification/fframes-upstream-source"
EXPERIMENT_SCHEMA = "motionwright.fframes-experimental-runtime/2"
LICENSE = "MIT"
MAX_STDOUT = 32 * 1024
MAX_STDERR = 65536  # The unchanged run_bounded helper's actual limit.
MAX_PNG = 16 * 1024 * 1024
MAX_MOVIE = 32 * 1024 * 1024
MAX_AUDIO = 2 * 1024 * 1024
WIDTH, HEIGHT, FPS, VIDEO_FRAMES = 480, 270, 30, 30
AUDIO_RATE, AUDIO_CHANNELS, AUDIO_FRAMES = 48000, 2, 96000
AUDIO_TOLERANCE = 2 / 32768
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
LABEL = re.compile(r"[a-z][a-z0-9_]{0,80}\Z")
BASELINE_PHASES = (
    "cold_release_build", "warm_release_build",
    "cold_headless_native_preview", "warm_headless_native_preview",
    "cold_bounded_video_render",
)
OBSERVED_PHASES = BASELINE_PHASES + (
    "cold_png_rgba_decode", "warm_png_rgba_decode", "video_metadata",
    "video_full_framehash", "audio_probe_build", "native_audio_mix_render",
    "audio_mix_metadata", "audio_mix_pcm_decode", "audio_bed_pcm_decode",
    "audio_cue_pcm_decode",
)

# This is an experimental caller of the pinned library, not upstream source,
# a Motionwright renderer adapter, a media import, or an authority issuer.
AUDIO_PROBE_RS = r'''use clap::Args;
use fframes::{AudioMap, AudioMixOptions, AudioTimestamp::Second, AudioTrack,
    FFramesContext, Frame, RenderOptions, Svgr, Video, cli};
use std::{path::PathBuf, process::ExitCode};

#[derive(Debug, Args)]
struct ProbeArgs {
    #[arg(long, global = true)]
    assets: PathBuf,
}

#[derive(Debug)]
struct SyntheticAudioProbe;

impl Video for SyntheticAudioProbe {
    const FPS: usize = 30;
    const WIDTH: usize = 320;
    const HEIGHT: usize = 180;

    fn duration(&self) -> fframes::Duration<'_> {
        fframes::Duration::Seconds(2.0)
    }

    fn audio(&self) -> AudioMap<'_> {
        AudioMap::from([
            AudioTrack::new("bed.wav", Second(0.0)..Second(2.0)).volume(0.5),
            AudioTrack::new("cue.wav", Second(0.5)..Second(1.5)).volume(0.25),
        ])
    }

    fn render_frame<'a>(&'a self, _frame: Frame, _ctx: &FFramesContext<'a, '_>) -> Svgr<'a> {
        fframes::svgr!(
            <svg xmlns="http://www.w3.org/2000/svg" width="320" height="180">
                <rect width="320" height="180" fill="#173047" />
            </svg>
        )
    }
}

fn main() -> ExitCode {
    let args = cli::parse::<ProbeArgs>();
    let folder = fframes::MediaDirectory::read_folder(&args.app.assets)
        .expect("private synthetic audio directory");
    let media = folder.process_media_source().expect("synthetic WAV inputs");
    let mut options = RenderOptions::default();
    options.media = Some(&media);
    options.load_system_fonts = false;
    options.audio_encoder_options.sample_rate = 48000;
    options.audio_mix = AudioMixOptions {
        master_gain_db: 0.0, limiter: None, declick: false,
    };
    cli::new(&SyntheticAudioProbe, options).args(args).run()
}
'''


def expect(condition: bool, why: str) -> None:
    if not condition:
        raise AssertionError(why)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode()


def secondary(primary: BaseException, category: str) -> None:
    # A failed diagnostic must not replace the original process exception.
    try:
        primary.add_note(category)
    except BaseException:
        pass


def new_bytes(path: Path, data: bytes) -> None:
    expect(type(data) is bytes, "private_output_bytes")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def identity(info) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def original_file(path: Path, limit: int):
    """Retain one regular descriptor and compare its identity before/after use."""
    path = Path(path)
    expect(type(limit) is int and limit > 0, "original_byte_limit")
    expect(path.is_absolute() and path.resolve(strict=True) == path
           and not any(p.is_symlink() for p in path.parents), "original_path")
    before = path.lstat()
    expect(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
           and 0 <= before.st_size <= limit, "original_kind_or_size")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if os.name == "posix":
        flags |= os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    primary = None
    stream = None
    try:
        expect(identity(os.fstat(fd)) == identity(before), "original_open_changed")
        stream = os.fdopen(fd, "rb", closefd=False)
        raw = stream.read(limit + 1)
        expect(len(raw) == before.st_size and len(raw) <= limit, "original_read_size")
        expect(identity(os.fstat(fd)) == identity(before), "original_read_changed")
        yield raw, fd
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            expect(identity(os.fstat(fd)) == identity(before)
                   and identity(path.lstat()) == identity(before), "original_after_changed")
        except BaseException:
            if primary is None:
                raise
            secondary(primary, "secondary_original_readback_failed")
        finally:
            try:
                if stream is not None:
                    stream.close()
                os.close(fd)
            except BaseException:
                if primary is None:
                    raise
                secondary(primary, "secondary_original_close_failed")


def read_original(path: Path, limit: int) -> bytes:
    with original_file(path, limit) as (raw, _fd):
        return raw


def sha_file(path: Path) -> str:
    # Executables are bounded, hashed originals; they are not copied to upload.
    digest = hashlib.sha256()
    path = path.resolve(strict=True)
    before = path.lstat()
    expect(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= 768 * 1024 * 1024,
           "tool_binary_size")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if os.name == "posix":
        flags |= os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as stream:
        expect(identity(os.fstat(stream.fileno())) == identity(before), "tool_open_changed")
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        expect(identity(os.fstat(stream.fileno())) == identity(before)
               and identity(path.stat()) == identity(before), "tool_binary_changed")
    return digest.hexdigest()


def complete_capture(capture: dict) -> bool:
    terminal = capture.get("terminal")
    return (capture.get("state") == "TERMINAL_OBSERVED"
            and type(terminal) is dict and type(terminal.get("exit_code")) is int
            and terminal["exit_code"] == 0
            and terminal.get("whole_process_completed") is True
            and terminal.get("pipes_closed") is True
            and terminal.get("timed_out") is False
            and terminal.get("output_budget_exceeded") is False
            and terminal.get("stdin_complete") is True
            and terminal.get("pipe_errors") == [])


def run(args: list[str], limit: int, label: str, metrics: dict, *,
        evidence_root: Path = OUT, max_stdout: int = MAX_STDOUT,
        input_bytes: bytes | None = None,
        file_input: dict | None = None) -> subprocess.CompletedProcess[bytes]:
    """Use the existing bounded supervisor once and retain before interpreting."""
    expect(LABEL.fullmatch(label) is not None and label not in metrics,
           "process_label_already_used")
    expect(type(args) is list and args and all(type(a) is str for a in args), "process_argv")
    expect(type(max_stdout) is int and 0 < max_stdout <= 8 * 1024 * 1024,
           "process_stdout_limit")
    process_root = evidence_root / "processes"
    process_root.mkdir(mode=0o700, exist_ok=True)
    folder = process_root / label
    folder.mkdir(mode=0o700, exist_ok=False)
    request = {"argv": args, "cwd": os.getcwd(), "timeout_seconds": limit,
               "stdout_limit_bytes": max_stdout, "stderr_limit_bytes": MAX_STDERR,
               "cleanup_allowance_seconds": 10, "automatic_retry": False,
               "stdin_bytes": None if input_bytes is None else len(input_bytes),
               "stdin_sha256": None if input_bytes is None else sha(input_bytes),
               "file_input": file_input}
    request_raw = json_bytes(request)
    new_bytes(folder / "argv.json", request_raw)
    if input_bytes is not None:
        new_bytes(folder / "stdin.bin", input_bytes)
    capture: dict = {}
    metrics[label] = {"capture": "processes/" + label,
                      "argv_sha256": sha(request_raw), "originals_retained": False}
    primary = None
    output = None
    try:
        output, _summary = run_bounded(args, max_stdout, limit,
                                      input_bytes=input_bytes, capture=capture)
    except BaseException as error:
        primary = error
    try:
        # Missing fields remain missing. RESERVED/NOT_STARTED are never EOF or exit.
        original = {key: value for key, value in capture.items()
                    if key not in {"stdout", "stderr"}}
        if primary is not None:
            original["raised_error"] = {"type": type(primary).__name__,
                                        "message": str(primary), "automatic_retry": False}
        for name in ("stdout", "stderr"):
            if name in capture:
                expect(type(capture[name]) is bytes, "process_capture_stream_type")
                new_bytes(folder / (name + ".bin"), capture[name])
                original[name + "_sha256"] = sha(capture[name])
                original[name + "_retained_bytes"] = len(capture[name])
        capture_raw = json_bytes(original)
        new_bytes(folder / "capture.json", capture_raw)
        metrics[label].update({"capture_sha256": sha(capture_raw),
                               "originals_retained": True})
        terminal = capture.get("terminal")
        if type(terminal) is dict:
            metrics[label].update({key: terminal[key] for key in
                                   ("elapsed_seconds", "exit_code") if key in terminal})
    except BaseException as custody_error:
        if primary is None:
            raise
        secondary(primary, "secondary_process_custody_failed:" + type(custody_error).__name__)
    if primary is not None:
        raise primary
    expect(complete_capture(capture), "process_capture_incomplete")
    expect(type(output) is bytes and output == capture["stdout"], "process_stdout_binding")
    return subprocess.CompletedProcess(args, 0, output, capture["stderr"])


def strict_object(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            expect(key not in result, "duplicate_evidence_key")
            result[key] = value
        return result
    obj = json.loads(raw, object_pairs_hook=unique,
                     parse_constant=lambda _: (_ for _ in ()).throw(AssertionError("json_constant")))
    expect(type(obj) is dict, "evidence_object")
    return obj


def png_metadata(path: Path) -> dict:
    raw = read_original(path, MAX_PNG)
    expect(len(raw) >= 33 and raw[:8] == b"\x89PNG\r\n\x1a\n", "png_header")
    width, height = struct.unpack_from(">II", raw, 16)
    expect(0 < width <= 4096 and 0 < height <= 4096
           and width * height <= 1920 * 1080, "png_dimensions")
    metadata = validate_png_crc_bytes(raw, width, height, max_bytes=MAX_PNG)
    expect(metadata["animated"] is False, "animated_preview_not_single_frame")
    return {"width": width, "height": height, "bytes": len(raw),
            "sha256": sha(raw), "transport_crc_checked": True}


def decode_png(path: Path, ffmpeg: Path, label: str, metrics: dict, *,
               evidence_root: Path = OUT) -> dict:
    metadata = png_metadata(path)
    raw = read_original(path, MAX_PNG)
    expect(sha(raw) == metadata["sha256"], "png_changed_before_decode")
    size = metadata["width"] * metadata["height"] * 4
    output = run([str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                  "-noautorotate", "-f", "image2pipe", "-c:v", "png", "-i", "pipe:0",
                  "-map", "0:v:0", "-an", "-sn", "-dn", "-pix_fmt", "rgba",
                  "-c:v", "rawvideo", "-f", "rawvideo", "pipe:1"],
                 30, label, metrics, evidence_root=evidence_root,
                 max_stdout=size + 1, input_bytes=raw)
    expect(output.stderr == b"" and len(output.stdout) == size, "single_complete_rgba_frame")
    return {**metadata, "rgba_sha256": sha(output.stdout), "rgba_bytes": size,
            "pixel_format": "rgba", "decode_capture": label,
            "encoded_path": path.relative_to(evidence_root).as_posix()}


def parse_framehash(raw: bytes, width: int, height: int, frames: int) -> dict:
    expect(0 < len(raw) <= MAX_STDOUT, "framehash_byte_limit")
    headers, records = {}, []
    for line in raw.decode("ascii").splitlines():
        if line.startswith("#"):
            if ":" in line:
                key, value = line[1:].split(":", 1)
                key, value = key.strip(), value.strip()
                expect(key not in headers, "framehash_duplicate_header")
                headers[key] = value
            continue
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        expect(len(parts) == 6 and HEX64.fullmatch(parts[-1]) is not None,
               "framehash_record_shape")
        values = [int(part) for part in parts[:5]]
        index = len(records)
        expect(values == [0, index, index, 1, width * height * 4],
               "framehash_frame_time_or_size")
        expect(index < frames, "framehash_extra_frame")
        records.append({"index": index, "pts": index, "duration": 1,
                        "rgba_bytes": values[4], "sha256": parts[-1]})
    expect(headers.get("format") == "frame checksums" and headers.get("version") == "2"
           and headers.get("hash") == "SHA256" and headers.get("tb 0") == "1/30"
           and headers.get("media_type 0") == "video"
           and headers.get("codec_id 0") == "rawvideo"
           and headers.get("dimensions 0") == f"{width}x{height}"
           and len(records) == frames, "framehash_complete_video")
    return {"frame_count": len(records), "time_base": "1/30", "pixel_format": "rgba",
            "width": width, "height": height, "frames": records,
            "framehash_stdout_sha256": sha(raw)}


def decode_video(path: Path, ffmpeg: Path, ffprobe: Path, metrics: dict, *,
                 evidence_root: Path = OUT) -> dict:
    # The decoder reopens the retained descriptor via procfs, not a mutable
    # pathname. No FD is passed to an SDK or represented as a permission.
    expect(sys.platform == "linux", "video_descriptor_decode_linux_profile")
    with original_file(path, MAX_MOVIE) as (raw, fd):
        descriptor_path = f"/proc/{os.getpid()}/fd/{fd}"
        witness = {"kind": "retained_regular_descriptor", "descriptor_path": descriptor_path,
                   "relative_path": path.relative_to(evidence_root).as_posix(),
                   "bytes": len(raw), "sha256": sha(raw)}
        metadata_output = run([str(ffprobe), "-v", "error", "-show_entries",
                               "stream=index,codec_type,codec_name,width,height,r_frame_rate,nb_frames",
                               "-show_entries", "format=duration", "-of", "json", descriptor_path],
                              30, "video_metadata", metrics, evidence_root=evidence_root,
                              file_input=witness)
        metadata = strict_object(metadata_output.stdout)
        expect(type(metadata.get("streams")) is list and len(metadata["streams"]) == 1,
               "baseline_video_stream_roster")
        video = metadata["streams"][0]
        expect(video.get("codec_type") == "video" and video.get("codec_name") == "h264"
               and (video.get("width"), video.get("height")) == (WIDTH, HEIGHT)
               and video.get("r_frame_rate") == "30/1"
               and video.get("nb_frames") == str(VIDEO_FRAMES), "baseline_video_metadata")
        duration = float(metadata["format"]["duration"])
        expect(math.isfinite(duration) and abs(duration - 1.0) <= 1 / FPS,
               "baseline_video_duration")
        decoded = run([str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                       "-noautorotate", "-i", descriptor_path, "-map", "0:v:0", "-an", "-sn", "-dn",
                       "-vsync", "0", "-pix_fmt", "rgba", "-c:v", "rawvideo",
                       "-f", "framehash", "-hash", "sha256", "pipe:1"],
                      60, "video_full_framehash", metrics, evidence_root=evidence_root,
                      file_input=witness)
        expect(decoded.stderr == b"", "video_decoder_reported_error")
        decoded_frames = parse_framehash(decoded.stdout, WIDTH, HEIGHT, VIDEO_FRAMES)
        return {"encoded_path": path.relative_to(evidence_root).as_posix(),
                "bytes": len(raw), "sha256": sha(raw), "codec": "h264", "fps": "30/1",
                "width": WIDTH, "height": HEIGHT, "duration_seconds": duration,
                "metadata_capture": "video_metadata", "decode_capture": "video_full_framehash",
                "decode": decoded_frames, "retained_descriptor_checked_before_after": True}


def _pcm_wave(frames: int, left_frequency: int, right_frequency: int) -> bytes:
    output = io.BytesIO()
    samples = bytearray()
    for index in range(frames):
        fade = min(1.0, index / 128, (frames - 1 - index) / 128)
        left = round(5000 * fade * math.sin(2 * math.pi * left_frequency * index / AUDIO_RATE))
        right = round(3500 * fade * math.sin(2 * math.pi * right_frequency * index / AUDIO_RATE))
        samples.extend(struct.pack("<hh", left, right))
    with wave.open(output, "wb") as stream:
        stream.setnchannels(AUDIO_CHANNELS)
        stream.setsampwidth(2)
        stream.setframerate(AUDIO_RATE)
        stream.writeframes(bytes(samples))
    return output.getvalue()


def create_audio_fixture(folder: Path) -> dict:
    folder.mkdir(mode=0o700, exist_ok=False)
    sources = {"bed.wav": _pcm_wave(AUDIO_FRAMES, 440, 660),
               "cue.wav": _pcm_wave(AUDIO_RATE, 330, 550)}
    for name, raw in sources.items():
        new_bytes(folder / name, raw)
    specification = {
        "schema": "motionwright.fframes-synthetic-audio-fixture/1",
        "scope": "synthetic_stereo_gain_and_timeline_mix_only",
        "sample_rate": AUDIO_RATE, "channels": AUDIO_CHANNELS, "output_frames": AUDIO_FRAMES,
        "limiter": False, "declick": False, "audio_acceptance": False,
        "tracks": [{"file": "bed.wav", "start_frame": 0, "end_frame": AUDIO_FRAMES, "gain": 0.5},
                   {"file": "cue.wav", "start_frame": 24000, "end_frame": 72000, "gain": 0.25}],
        "originals": {name: {"bytes": len(raw), "sha256": sha(raw)} for name, raw in sources.items()},
    }
    # Only WAV inputs enter MediaDirectory; the specification is a sibling.
    new_bytes(folder.parent / "fixture.json", json_bytes(specification))
    return specification


def decode_pcm(path: Path, ffmpeg: Path, label: str, frames: int, metrics: dict, *,
               evidence_root: Path = OUT) -> bytes:
    raw = read_original(path, MAX_AUDIO)
    size = frames * AUDIO_CHANNELS * 4
    decoded = run([str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                   "-f", "wav", "-i", "pipe:0", "-map", "0:a:0", "-vn", "-sn", "-dn",
                   "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"],
                  30, label, metrics, evidence_root=evidence_root,
                  max_stdout=size + 1, input_bytes=raw)
    expect(decoded.stderr == b"" and len(decoded.stdout) == size, "complete_stereo_pcm_frame_count")
    expect(all(math.isfinite(v[0]) for v in struct.iter_unpack("<f", decoded.stdout)),
           "nonfinite_decoded_pcm")
    return decoded.stdout


def compare_audio_pcm(bed: bytes, cue: bytes, actual: bytes) -> dict:
    expect(len(bed) == AUDIO_FRAMES * 8 and len(cue) == AUDIO_RATE * 8
           and len(actual) == AUDIO_FRAMES * 8, "audio_reference_shape")
    a = [value[0] for value in struct.iter_unpack("<f", bed)]
    b = [value[0] for value in struct.iter_unpack("<f", cue)]
    c = [value[0] for value in struct.iter_unpack("<f", actual)]
    expect(all(math.isfinite(v) for values in (a, b, c) for v in values), "audio_finite")
    expected = [v * 0.5 for v in a]
    for i, value in enumerate(b):
        expected[24000 * 2 + i] += value * 0.25
    errors = [abs(found - wanted) for found, wanted in zip(c, expected)]
    max_error = max(errors)
    expect(max_error <= AUDIO_TOLERANCE, "native_audio_gain_timeline_or_channel_mismatch")
    expect(max(abs(v) for v in c) > 0.01 and max(abs(c[i] - c[i + 1])
           for i in range(0, len(c), 2)) > 0.01, "native_audio_missing_or_collapsed_stereo")
    return {"scope": "synthetic_stereo_gain_and_timeline_mix_only", "sample_rate": AUDIO_RATE,
            "channels": AUDIO_CHANNELS, "sample_frames": AUDIO_FRAMES,
            "decoded_pcm_sha256": sha(actual), "decoded_pcm_bytes": len(actual),
            "max_absolute_error": max_error,
            "rms_error": math.sqrt(sum(v * v for v in errors) / len(errors)),
            "absolute_error_limit": AUDIO_TOLERANCE, "human_audio_acceptance": False}


def declared_sdk_pin(raw: bytes) -> str:
    manifest = tomllib.loads(raw.decode("utf8"))
    dependencies = manifest["workspace"]["dependencies"]
    selected = [value for name, value in dependencies.items() if name.startswith("semwright-")]
    expect(selected and all(type(value) is dict
           and value.get("git") == "https://github.com/seradotcom/semwright"
           and HEX40.fullmatch(str(value.get("rev", ""))) is not None for value in selected),
           "declared_semwright_source_contract")
    pins = {value["rev"] for value in selected}
    expect(len(pins) == 1, "mixed_declared_semwright_pins")
    return pins.pop()


def measure_audio(checkout: Path, ffmpeg: Path, ffprobe: Path, metrics: dict, *,
                  evidence_root: Path = OUT) -> dict:
    audio_root = evidence_root / "audio"
    audio_root.mkdir(mode=0o700, exist_ok=False)
    specification = create_audio_fixture(audio_root / "inputs")
    source = AUDIO_PROBE_RS.encode()
    new_bytes(audio_root / "probe.rs", source)
    example_folder = checkout / "examples/hello-world/examples"
    example_folder.mkdir(exist_ok=True)
    expect(example_folder.resolve(strict=True) == example_folder
           and not any(path.is_symlink() for path in example_folder.parents),
           "synthetic_example_parent")
    example_path = example_folder / (AUDIO_APP + ".rs")
    new_bytes(example_path, source)
    run(["cargo", "build", "--locked", "--quiet", "--release", "--manifest-path",
         str(checkout / "Cargo.toml"), "--target-dir", str(checkout / "target"),
         "-p", PKG, "--example", AUDIO_APP], 180, "audio_probe_build", metrics,
        evidence_root=evidence_root)
    binary = checkout / "target/release/examples" / AUDIO_APP
    binary_sha = sha_file(binary)
    mixed = audio_root / "native-mix.wav"
    rendered = run([str(binary), "--json", "--assets", str(audio_root / "inputs"),
                    "audio", "render", "0s..2s", "--float", "-o", str(mixed)],
                   120, "native_audio_mix_render", metrics, evidence_root=evidence_root)
    result = strict_object(rendered.stdout)
    expect(result.get("output") == str(mixed) and result.get("missing_files") == []
           and result.get("start_seconds") == 0.0 and result.get("end_seconds") == 2.0,
           "native_audio_cli_result")
    raw = read_original(mixed, MAX_AUDIO)
    metadata = strict_object(run([str(ffprobe), "-v", "error", "-show_entries",
                                  "stream=codec_name,codec_type,sample_rate,channels,duration",
                                  "-of", "json", str(mixed)], 30, "audio_mix_metadata", metrics,
                                 evidence_root=evidence_root).stdout)
    expect(type(metadata.get("streams")) is list and len(metadata["streams"]) == 1,
           "native_audio_stream_roster")
    stream = metadata["streams"][0]
    expect(stream.get("codec_type") == "audio" and stream.get("codec_name") == "pcm_f32le"
           and stream.get("sample_rate") == str(AUDIO_RATE)
           and stream.get("channels") == AUDIO_CHANNELS
           and abs(float(stream.get("duration", 0)) - 2.0) <= 1 / AUDIO_RATE,
           "native_audio_metadata")
    actual = decode_pcm(mixed, ffmpeg, "audio_mix_pcm_decode", AUDIO_FRAMES, metrics,
                        evidence_root=evidence_root)
    expect(sha(read_original(mixed, MAX_AUDIO)) == sha(raw), "native_audio_original_changed")
    bed = decode_pcm(audio_root / "inputs/bed.wav", ffmpeg, "audio_bed_pcm_decode", AUDIO_FRAMES,
                     metrics, evidence_root=evidence_root)
    cue = decode_pcm(audio_root / "inputs/cue.wav", ffmpeg, "audio_cue_pcm_decode", AUDIO_RATE,
                     metrics, evidence_root=evidence_root)
    comparison = compare_audio_pcm(bed, cue, actual)
    expect(sha_file(binary) == binary_sha, "audio_probe_binary_changed")
    expect(read_original(example_path, 32 * 1024) == source, "audio_probe_source_changed")
    for name, witness in specification["originals"].items():
        original = read_original(audio_root / "inputs" / name, MAX_AUDIO)
        expect(len(original) == witness["bytes"] and sha(original) == witness["sha256"],
               "synthetic_audio_input_changed")
    return {**comparison, "status": "OBSERVED_SYNTHETIC_STEREO_MIX",
            "original_wav_sha256": sha(raw), "original_wav_bytes": len(raw),
            "encoded_path": "audio/native-mix.wav", "fixture_path": "audio/fixture.json",
            "fixture_sha256": sha(json_bytes(specification)), "probe_source_path": "audio/probe.rs",
            "probe_source_sha256": sha(source), "binary_sha256": binary_sha,
            "render_capture": "native_audio_mix_render", "decode_capture": "audio_mix_pcm_decode",
            "baseline_hello_world_modified": False, "baseline_build_timings_reused": False}


def _finish_diagnostic(evidence: dict, primary: BaseException | None) -> None:
    """Publish the existing summary once, preserving the original failure."""
    try:
        print(json.dumps({"fframes_upstream": evidence["status"], "source_sha": PIN,
                          "release_admitted": False, "originals_upload_confirmed": False}))
    except BaseException as error:
        if primary is None:
            raise
        secondary(primary, "secondary_summary_not_written:" + type(error).__name__)
    if primary is not None:
        raise primary


def main() -> None:
    expect(os.getenv("GITHUB_ACTIONS") == "true", "disposable_ci_runner_required")
    expect(sys.platform == "linux", "disposable_ubuntu_profile_required")
    expect(os.getenv("CARGO_INCREMENTAL") == "0"
           and os.getenv("CARGO_PROFILE_RELEASE_DEBUG") == "0", "benchmark_build_environment")
    expect(not OUT.exists() and not CHECKOUT.exists(), "existing_experiment_destination")
    OUT.mkdir(mode=0o700, parents=True)
    # One fixed process cwd. run_bounded is reused without wrappers or API changes.
    os.chdir(OUT)
    metrics: dict = {}
    evidence = {
        "schema": EXPERIMENT_SCHEMA, "status": "STARTED", "runtime": "disposable_ubuntu_24_04",
        "fframes_repository": REPO, "fframes_exact_sha": PIN, "fframes_license": LICENSE,
        "rust_svg_experimental": True, "source_code_admitted_to_motionwright": False,
        "semwright_driver_host_executed": False, "owner_install_permission": False,
        "backend_release_admitted": False, "artistic_quality_reviewed": False,
        "hyperframes_same_fixture_comparison": "NOT_RUN", "interactive_gpu_preview": "NOT_RUN_HEADLESS_CI",
        "audio_score_composition": "NOT_OBSERVED", "compile_time_svg_correctness":
            "NOT_EQUIVALENT_TO_MOTIONWRIGHT_SOURCE",
        "cold_build_measured": False, "warm_build_measured": False,
        "frame_preview_measured": False, "full_video_render_measured": False,
        "private_originals_upload_confirmed": False, "measurements": metrics,
    }
    primary = None
    try:
        source_sha = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                         15, "motionwright_source_head", metrics).stdout.decode("ascii").strip()
        expect(HEX40.fullmatch(source_sha) is not None
               and source_sha == os.getenv("GITHUB_SHA"), "motionwright_source_sha")
        evidence["motionwright_sha"] = source_sha
        manifest = run(["git", "-C", str(ROOT), "show", "HEAD:Cargo.toml"],
                       15, "motionwright_source_manifest", metrics).stdout
        new_bytes(OUT / "motionwright-Cargo.toml", manifest)
        evidence["declared_semwright_sha"] = declared_sdk_pin(manifest)
        evidence["source_manifest_sha256"] = sha(manifest)
        for executable in ("cargo", "rustc", "ffmpeg", "ffprobe"):
            expect(shutil.which(executable) is not None, "required_tool_missing:" + executable)
        ffmpeg = Path(shutil.which("ffmpeg")).resolve(strict=True)
        ffprobe = Path(shutil.which("ffprobe")).resolve(strict=True)
        evidence["independent_tools"] = {"ffmpeg_sha256": sha_file(ffmpeg),
                                          "ffprobe_sha256": sha_file(ffprobe)}
        run(["rustc", "--version"], 15, "rustc_version", metrics)
        run([str(ffmpeg), "-version"], 15, "ffmpeg_version", metrics)
        run([str(ffprobe), "-version"], 15, "ffprobe_version", metrics)
        run(["uname", "-m"], 15, "architecture", metrics)
        run(["git", "clone", "--quiet", "--filter=blob:none", REPO, str(CHECKOUT)],
            120, "source_checkout", metrics)
        run(["git", "-C", str(CHECKOUT), "checkout", "--detach", "--quiet", PIN],
            45, "exact_source_checkout", metrics)
        actual = run(["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"],
                     15, "upstream_source_head", metrics).stdout.decode("ascii").strip()
        expect(actual == PIN, "upstream_source_pin")
        # Match the baseline's original Cargo/media working directory. This is
        # a single owned cwd transition, not a per-command launcher or shell.
        os.chdir(CHECKOUT)
        run(["git", "-C", str(CHECKOUT), "diff", "--exit-code", "HEAD", "--"],
            15, "upstream_tracked_before", metrics)
        build = ["cargo", "build", "--locked", "--quiet", "--release", "--manifest-path",
                 str(CHECKOUT / "Cargo.toml"), "--target-dir", str(CHECKOUT / "target"),
                 "-p", PKG, "--bin", APP]
        expect(not (CHECKOUT / "target").exists(), "cold_build_target_already_present")
        run(build, 1350, "cold_release_build", metrics)
        evidence["cold_build_measured"] = True
        binary = CHECKOUT / "target/release" / APP
        binary_sha = sha_file(binary)
        evidence["binary_bytes"] = binary.stat().st_size
        evidence["binary_sha256"] = binary_sha
        run(build, 90, "warm_release_build", metrics)
        expect(sha_file(binary) == binary_sha, "baseline_binary_changed_during_warm_build")
        evidence["warm_build_measured"] = True
        for index in range(2):
            help_output = run([str(binary), "--help"], 15, f"cli_startup_{index}", metrics)
            expect(b"render" in help_output.stdout, "upstream_cli_command_set")
        previews = []
        for phase, folder_name, decode_label in (
            ("cold_headless_native_preview", "frame-preview", "cold_png_rgba_decode"),
            ("warm_headless_native_preview", "frame-preview-repeat", "warm_png_rgba_decode"),
        ):
            folder = OUT / folder_name
            folder.mkdir()
            run([str(binary), "frame", "1s", "-o", str(folder), "--scale", "0.25"],
                180, phase, metrics)
            paths = sorted(folder.glob("*.png"))
            expect(len(paths) == 1, "single_native_preview_roster")
            preview = decode_png(paths[0], ffmpeg, decode_label, metrics)
            expect((preview["width"], preview["height"]) == (WIDTH, HEIGHT), "baseline_preview_size")
            previews.append(preview)
        evidence["preview_png"], evidence["repeated_preview_png"] = previews
        evidence["repeated_encoded_png_identical"] = previews[0]["sha256"] == previews[1]["sha256"]
        evidence["repeated_decoded_rgba_identical"] = previews[0]["rgba_sha256"] == previews[1]["rgba_sha256"]
        expect(evidence["repeated_decoded_rgba_identical"], "repeated_native_pixels_differ")
        evidence["frame_preview_measured"] = True
        movie = OUT / "upstream-source-preview.mp4"
        run([str(binary), "render", "0s..1s", "--draft", "--scale", "0.25", "-o", str(movie)],
            360, "cold_bounded_video_render", metrics)
        evidence["video"] = decode_video(movie, ffmpeg, ffprobe, metrics)
        evidence["full_video_render_measured"] = True
        evidence["audio"] = measure_audio(CHECKOUT, ffmpeg, ffprobe, metrics)
        evidence["audio_score_composition"] = "OBSERVED_SYNTHETIC_STEREO_MIX"
        run(["git", "-C", str(CHECKOUT), "diff", "--exit-code", "HEAD", "--"],
            15, "upstream_tracked_after", metrics)
        expect(sha_file(binary) == binary_sha, "baseline_binary_changed")
        expect(sha_file(ffmpeg) == evidence["independent_tools"]["ffmpeg_sha256"]
               and sha_file(ffprobe) == evidence["independent_tools"]["ffprobe_sha256"],
               "independent_decoder_binary_changed")
        evidence["status"] = "EXPERIMENTAL_UPSTREAM_SOURCE_RENDER_PASS"
    except BaseException as error:
        primary = error
        evidence["status"] = "EXPERIMENTAL_UPSTREAM_SOURCE_BLOCKED"
        evidence["failure_type"] = type(error).__name__
        evidence["failure_summary"] = str(error)
        try:
            new_bytes(OUT / "failure.txt", traceback.format_exc().encode("utf8"))
        except BaseException:
            secondary(error, "secondary_failure_record_not_retained")
    finally:
        evidence["completed_utc"] = datetime.now(timezone.utc).isoformat()
        try:
            new_bytes(OUT / "result.json", json_bytes(evidence))
        except BaseException:
            if primary is None:
                raise
            secondary(primary, "secondary_result_record_not_retained")
        _finish_diagnostic(evidence, primary)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # The exact exception/captures stay private. Do not echo decoder stderr
        # through an unhandled traceback into public CI logs.
        print("fframes-experiment=NOT_VERIFIED", file=sys.stderr)
        raise SystemExit(2)
