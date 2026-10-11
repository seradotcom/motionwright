#!/usr/bin/env python3
"""Independent, CI-only decoded-media acceptance for the pinned 2s native AV master.

A native receipt and an MP4 SHA-256 are necessary but insufficient: a video can
contain 60 copies of a blank frame and an AAC stream can be silent or swapped.
This probe uses the already-required ffmpeg/ffprobe tools *only inside CI*, never
as a Motionwright application/runtime media backend or a source of authority.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

FRAME_COUNT = 60
FPS_NUM = 30
FPS_DEN = 1
WIDTH = 1920
HEIGHT = 1080
AUDIO_RATE = 48_000
EXPECTED_SECONDS = 2.0
MAX_MP4_BYTES = 512 * 1024 * 1024
MAX_DECODED_PCM_BYTES = 2 * 1024 * 1024  # up to 6s stereo s16le + bounded AAC tail
FRAME_WIDTH = 160
FRAME_HEIGHT = 90
FRAMES_TO_COMPARE = (0, 30)
LEFT_TONE_HZ = 440
RIGHT_TONE_HZ = 660
VIDEO_DIFF_THRESHOLD = 17
MIN_CHANGED_CHANNEL_BYTES = 85
# Closed CI fixtures only: baseline 2s or one of the 6s ProductHero profiles.
ALLOWED_PROFILES = {(60, 1920, 1080), (180, 1920, 1080), (180, 1080, 1920), (180, 1080, 1080)}


class MediaProbeError(ValueError):
    """The decoded native master failed a required media-content invariant."""


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise MediaProbeError(message)


def _run_decoder(argv: list[str], *, timeout: int = 70, max_bytes: int) -> bytes:
    """Decode a fixed CI command, keeping even malformed output off heap."""
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                argv,
                stdout=output,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
            if result.returncode != 0:
                raise MediaProbeError(
                    "Native AV decoder failed: " +
                    result.stderr[:1024].decode("utf-8", errors="replace")
                )
            output.seek(0, 2)
            count = output.tell()
            _require(0 < count <= max_bytes,
                     "Native AV decoder returned empty or unbounded bytes")
            output.seek(0)
            data = output.read(max_bytes + 1)
            _require(len(data) == count, "Native AV decoder data changed during bounded read")
            return data
    except (subprocess.TimeoutExpired, OSError) as error:
        raise MediaProbeError("Native AV decoder timed out or was unavailable") from error


def _rate(value: object) -> tuple[int, int]:
    _require(isinstance(value, str) and "/" in value, "AV stream lacks a rational video frame rate")
    numerator, denominator = value.split("/", 1)
    try:
        n, den = int(numerator), int(denominator)
    except ValueError as error:
        raise MediaProbeError("AV video frame rate is malformed") from error
    _require(n > 0 and den > 0, "AV video frame rate is not positive")
    return n, den


def inspect_streams(probed: dict, *, expected_frames: int = FRAME_COUNT,
                    expected_size: tuple[int, int] = (WIDTH, HEIGHT)) -> dict:
    """Validate the exact bounded native AV profile, never guess dimensions."""
    _require((expected_frames, *expected_size) in ALLOWED_PROFILES,
             "Native AV probe profile is not one of the pinned CI fixtures")
    expected_seconds = expected_frames / FPS_NUM
    _require(isinstance(probed, dict), "FFprobe result must be JSON")
    streams = probed.get("streams")
    _require(isinstance(streams, list), "FFprobe did not report streams")
    video = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    _require(len(video) == 1 and len(audio) == 1, "Native MP4 must contain exactly one video and audio stream")
    v, a = video[0], audio[0]
    _require(v.get("codec_name") == "h264", "Native AV video is not decoded as H.264")
    _require((v.get("width"), v.get("height")) == expected_size,
             f"Decoded master dimensions differ from the {expected_size[0]}x{expected_size[1]} profile")
    _require(_rate(v.get("avg_frame_rate")) == (FPS_NUM, FPS_DEN), "Decoded master frame rate differs from the native profile")
    observed_frames = str(v.get("nb_read_frames", ""))
    _require(observed_frames.isdecimal() and int(observed_frames) == expected_frames,
             f"Decoded MP4 video does not contain exactly {expected_frames} frames")
    _require(a.get("codec_name") == "aac", "Native AV audio is not decoded as AAC")
    _require(str(a.get("sample_rate")) == str(AUDIO_RATE), "Decoded AAC sample rate is not 48000 Hz")
    _require(str(a.get("channels", "")) == "2", "Decoded AAC is not stereo")
    try:
        duration = float(probed["format"]["duration"])
    except (ValueError, TypeError, KeyError) as error:
        raise MediaProbeError("Native MP4 has no measured duration") from error
    _require(math.isfinite(duration) and abs(duration - expected_seconds) <= 0.09,
             f"Native MP4 duration is not consistent with its {expected_frames}-frame editorial cut")
    return {
        "container_seconds": duration,
        "video_codec": "h264",
        "video_frames_decoded": expected_frames,
        "video_width": expected_size[0],
        "video_height": expected_size[1],
        "video_fps": "30/1",
        "audio_codec": "aac",
        "audio_sample_rate": AUDIO_RATE,
        "audio_channels": 2,
    }


def _harmonic_amplitude(samples: list[int], frequency: int) -> float:
    """Phase-insensitive DFT at exact fixture tone, independent of AAC delay."""
    begin = int(AUDIO_RATE * 0.25)
    end = min(len(samples), int(AUDIO_RATE * 1.75))
    _require(end - begin >= AUDIO_RATE, "Decoded audio lacks one full second of stable content")
    cosine = 0.0
    sine = 0.0
    count = 0
    for position in range(begin, end, 8):
        value = samples[position] / 32768.0
        angle = (2 * math.pi * frequency / AUDIO_RATE) * position
        cosine += value * math.cos(angle)
        sine += value * math.sin(angle)
        count += 1
    return 2.0 * math.hypot(sine, cosine) / count


def inspect_decoded_audio(pcm: bytes, *, expected_seconds: float = EXPECTED_SECONDS) -> dict:
    """Prove actual L440Hz/R660Hz content and exact bounded source duration."""
    _require(expected_seconds in (2.0, 6.0), "Audio probe supports only pinned 2s or 6s fixtures")
    _require(len(pcm) % 4 == 0, "Decoded stereo s16le PCM has a partial sample")
    frames = len(pcm) // 4
    expected_samples = int(expected_seconds * AUDIO_RATE)
    _require(expected_samples - 2_400 <= frames <= expected_samples + 4_800,
             "Decoded 48k AAC duration differs materially from the voice source")
    import struct

    left: list[int] = []
    right: list[int] = []
    for l, r in struct.iter_unpack("<hh", pcm):
        left.append(l)
        right.append(r)
    left_440 = _harmonic_amplitude(left, LEFT_TONE_HZ)
    left_660 = _harmonic_amplitude(left, RIGHT_TONE_HZ)
    right_440 = _harmonic_amplitude(right, LEFT_TONE_HZ)
    right_660 = _harmonic_amplitude(right, RIGHT_TONE_HZ)

    _require(left_440 >= 0.075, "Decoded AAC left channel lacks the source 440 Hz tone")
    _require(right_660 >= 0.065, "Decoded AAC right channel lacks the source 660 Hz tone")
    _require(left_440 >= 5 * max(left_660, 1e-7),
             "Decoded AAC left channel lost its 440 Hz identity or was mixed/swapped")
    _require(right_660 >= 5 * max(right_440, 1e-7),
             "Decoded AAC right channel lost its 660 Hz identity or was mixed/swapped")
    return {
        "decoded_pcm_frames": frames,
        "decoded_seconds": frames / AUDIO_RATE,
        "left_440_amplitude": round(left_440, 6),
        "left_660_leakage": round(left_660, 6),
        "right_440_leakage": round(right_440, 6),
        "right_660_amplitude": round(right_660, 6),
        "stereo_channel_identity": "confirmed_distinct_source_tones",
    }


def inspect_decoded_video(pixels: bytes, *, expect_movement: bool = True) -> dict:
    """Distinguish moving/still *actual* MP4 frames; no fallback synthetic pixels.

    The static test is opt-in and requires nonblank content plus a stable
    between-frame decode. Existing moving-film contracts remain unchanged.
    """
    size = FRAME_WIDTH * FRAME_HEIGHT * 3
    _require(len(pixels) == 2 * size, "Decoded MP4 does not expose the requested frames 0 and 30")
    first, middle = pixels[:size], pixels[size:]
    changed = sum(abs(a - b) >= VIDEO_DIFF_THRESHOLD for a, b in zip(first, middle))
    if expect_movement:
        _require(changed >= MIN_CHANGED_CHANNEL_BYTES,
                 "Decoded native master has no meaningful pixel movement between frame 0 and frame 30")
    else:
        _require(changed < MIN_CHANGED_CHANNEL_BYTES,
                 "Declared static procedural film unexpectedly changes between frames")
        # Independently checked at native/source-object positions by
        # procedural_native_evidence; this is only a nonblank decoder check.
        _require(max(first) - min(first) >= 45,
                 "Static decoded native master is visually blank/monochromatic")
    return {
        "compared_frames": list(FRAMES_TO_COMPARE),
        "decoded_sample_width": FRAME_WIDTH,
        "decoded_sample_height": FRAME_HEIGHT,
        "changed_channel_bytes": changed,
        "first_frame_rgb_sha256": hashlib.sha256(first).hexdigest(),
        "middle_frame_rgb_sha256": hashlib.sha256(middle).hexdigest(),
        "video_movement_decoded": expect_movement,
        "video_stable_nonblank": not expect_movement,
    }


def write_two_channel_tone_wav(destination: Path, *, seconds: float = EXPECTED_SECONDS) -> None:
    """Create deterministic, distinct-channel 2s/6s stereo fixture (not sound design)."""
    _require(seconds in (2.0, 6.0), "Only the pinned native AV test durations are supported")
    import struct

    sample_count = int(seconds * AUDIO_RATE)
    frames = bytearray()
    for index in range(sample_count):
        taper = min(1.0, index / 960, (sample_count - 1 - index) / 960)
        left = round(8_800 * taper * math.sin(2 * math.pi * LEFT_TONE_HZ * index / AUDIO_RATE))
        right = round(6_400 * taper * math.sin(2 * math.pi * RIGHT_TONE_HZ * index / AUDIO_RATE))
        frames.extend(struct.pack("<hh", left, right))
    with wave.open(str(destination), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(AUDIO_RATE)
        wav.writeframes(frames)
    verify_generated_tone_wav(destination, seconds=seconds)


def hash_file(path: Path) -> str:
    """Hash exact file bytes in bounded chunks without buffering whole masters."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_generated_tone_wav(source: Path, *, seconds: float = EXPECTED_SECONDS) -> None:
    """Fail if the authored test WAV is silent, mono, or has a wrong duration."""
    _require(seconds in (2.0, 6.0), "Unsupported pinned native WAV duration")
    with wave.open(str(source), "rb") as audio:
        _require(audio.getnchannels() == 2 and audio.getsampwidth() == 2
                 and audio.getframerate() == AUDIO_RATE
                 and audio.getnframes() == int(AUDIO_RATE * seconds),
                 f"Generated source WAV is not exact {seconds:g}s/48k/stereo/s16le")
        observed = inspect_decoded_audio(audio.readframes(audio.getnframes()), expected_seconds=seconds)
    _require(observed["stereo_channel_identity"] == "confirmed_distinct_source_tones",
             "Generated source WAV has wrong stereo tone mapping")


def probe_native_mp4(master: Path, source_wav: Path, ffmpeg: Path, ffprobe: Path,
                     *, expected_frames: int = FRAME_COUNT,
                     expected_size: tuple[int, int] = (WIDTH, HEIGHT),
                     expect_movement: bool = True) -> dict:
    _require((expected_frames, *expected_size) in ALLOWED_PROFILES,
             "Native AV probe profile is outside the fixed CI fixtures")
    seconds = expected_frames / FPS_NUM
    _require(master.is_file() and not master.is_symlink(), "Native master is not a regular MP4 file")
    _require(1024 < master.stat().st_size <= MAX_MP4_BYTES, "Native master is missing or exceeds the bounded media budget")
    verify_generated_tone_wav(source_wav, seconds=seconds)

    metadata = _run_decoder([
        str(ffprobe), "-v", "error", "-count_frames", "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,sample_rate,channels:format=duration",
        "-of", "json", str(master),
    ], max_bytes=128 * 1024)
    try:
        json_data = json.loads(metadata)
    except json.JSONDecodeError as error:
        raise MediaProbeError("FFprobe did not return structured video/audio metadata") from error
    stream_report = inspect_streams(json_data, expected_frames=expected_frames,
                                    expected_size=expected_size)
    pcm = _run_decoder([
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(master), "-map", "0:a:0", "-ac", "2", "-ar", str(AUDIO_RATE),
        "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
    ], max_bytes=MAX_DECODED_PCM_BYTES)
    audio_report = inspect_decoded_audio(pcm, expected_seconds=seconds)
    pixels = _run_decoder([
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(master), "-map", "0:v:0",
        "-vf", r"select=eq(n\,0)+eq(n\,30),scale=160:90:flags=bicubic,format=rgb24",
        "-vsync", "0", "-f", "rawvideo", "pipe:1",
    ], max_bytes=2 * FRAME_WIDTH * FRAME_HEIGHT * 3)
    video_report = inspect_decoded_video(pixels, expect_movement=expect_movement)
    return {
        "evidence_scope": (
            "actual_decoded_h264_aac_two_second_fixture_only" if expected_frames == 60
            else "actual_decoded_h264_aac_six_second_hero_fixture" if expect_movement
            else "actual_decoded_h264_aac_six_second_static_procedural_fixture"
        ),
        "source_wav_sha256": hash_file(source_wav),
        "master_sha256": hash_file(master),
        **stream_report,
        **audio_report,
        **video_report,
    }


def main() -> int:
    if len(sys.argv) != 5:
        print("usage: native_av_media_probe.py MASTER.mp4 SOURCE.wav FFMPEG FfPROBE", file=sys.stderr)
        return 2
    try:
        result = probe_native_mp4(*map(Path, sys.argv[1:]))
    except (MediaProbeError, OSError) as error:
        print(f"native-av-decoded-probe: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
