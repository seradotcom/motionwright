#!/usr/bin/env python3
"""Independent real-media proof for owner-source-bound semantic SFX -> AAC.

Uses FFmpeg/FFprobe ONLY as CI acceptance decoders. The product renderer and
mux remain the pinned Semwright native Driver Host. This is synthetic original
technical audio, NOT mastered soundtrack, licensed media, or human approval.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any
import wave

from native_av_media_probe import (
    _run_decoder, inspect_streams, inspect_decoded_video, MediaProbeError,
    MAX_DECODED_PCM_BYTES, MAX_MP4_BYTES, FRAME_WIDTH, FRAME_HEIGHT, AUDIO_RATE,
)

SOURCE_FRAMES = 24 * 1600
MASTER_FRAMES = 60 * 1600
SAMPLE_RATE = 48_000
FPS = 30
SOURCE_A = "original-focus-sfx.wav"
SOURCE_B = "original-confirm-sfx.wav"
MIX = "semantic-sfx-master.wav"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MediaProbeError(message)


def sha(path: Path) -> str:
    require(path.is_file() and not path.is_symlink(), "SFX evidence source must be a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pcm(path: Path, *, frames: int) -> list[tuple[int, int]]:
    require(path.is_file() and not path.is_symlink(), "SFX WAV must be a regular file")
    require(44 < path.stat().st_size <= 2 * 1024 * 1024, "SFX original WAV exceeds bounded data")
    with wave.open(str(path), "rb") as audio:
        require(
            audio.getnchannels() == 2 and audio.getsampwidth() == 2
            and audio.getframerate() == SAMPLE_RATE
            and audio.getnframes() == frames and audio.getcomptype() == "NONE",
            "Semantic SFX WAV has wrong PCM16 stereo 48 kHz duration or codec",
        )
        body = audio.readframes(frames)
        require(len(body) == frames * 4, "SFX read was incomplete")
    return list(struct.iter_unpack("<hh", body))


def original_native_mix(
    source_a: list[tuple[int, int]], source_b: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    require(len(source_a) == len(source_b) == SOURCE_FRAMES, "Incorrect bounded original SFX source")
    out = [[0.0, 0.0] for _ in range(MASTER_FRAMES)]
    for source, start in ((source_a, 6 * 1600), (source_b, 20 * 1600)):
        # Authored gain_milli_db=-12000, attack_frames=3, release_frames=6.
        gain = 10.0 ** (-12000.0 / 20000.0)
        attack, release = 3 * 1600, 6 * 1600
        for i, sample in enumerate(source):
            envelope = min(1.0, i / attack, (SOURCE_FRAMES - i - 1) / release)
            out[start + i][0] += sample[0] * gain * envelope
            out[start + i][1] += sample[1] * gain * envelope
    require(all(max(abs(left), abs(right)) < 32767 for left, right in out),
            "Original SFX reference would clip")
    return [(int(round(l)), int(round(r))) for l, r in out]


def inspect_original_sources(folder: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    require(receipt.get("schema") == "motionwright.semantic-sfx-av-prepared/1",
            "SFX acceptance cannot use another preparation schema")
    require(receipt.get("frames") == 60 and receipt.get("sample_count") == 96000,
            "SFX preparation is not the fixed exact master")
    require(receipt.get("fps") == "30/1" and receipt.get("sample_rate") == 48000,
            "SFX preparation has an unsupported audio clock")
    require(receipt.get("channels") == 2 and receipt.get("revision", 0) > 0,
            "SFX preparation is not the persisted original stereo project")
    schedule = receipt.get("scheduled_sfx")
    require(isinstance(schedule, list) and len(schedule) == 2,
            "SFX preparation needs exactly two ordered cues")
    require(
        [(s.get("absolute_start_frame"), s.get("absolute_end_frame"),
          s.get("first_sample"), s.get("sample_count")) for s in schedule]
        == [(6, 30, 9600, 38400), (20, 44, 32000, 38400)],
        "SFX original frame/sample mapping changed",
    )
    a, b, mixed = folder / SOURCE_A, folder / SOURCE_B, folder / MIX
    for src, key in ((a, "first_source_sha256"), (b, "second_source_sha256"),
                     (mixed, "audio_sha256")):
        value = receipt.get(key)
        require(isinstance(value, str) and len(value) == 64 and sha(src) == value,
                "SFX original file does not match its reported immutable digest")
    left = pcm(a, frames=SOURCE_FRAMES)
    right = pcm(b, frames=SOURCE_FRAMES)
    output = pcm(mixed, frames=MASTER_FRAMES)
    expected = original_native_mix(left, right)
    # Rust f64::round uses ties away from zero, Python rounds ties to even.
    # A single PCM LSB is the maximum allowed implementation difference.
    worst = max(max(abs(x - u), abs(y - v)) for (x, y), (u, v) in zip(output, expected))
    require(worst <= 1, "Native semantic SFX PCM is not the sum of the actual imported stems")
    require(any(x != 0 or y != 0 for x, y in output[6000:72000]),
            "SFX master is silent despite source sample claims")
    require(all(x == 0 and y == 0 for x, y in output[:6*1600]),
            "Semantic SFX starts before its scene-linked offset")
    require(all(x == 0 and y == 0 for x, y in output[44*1600:]),
            "Semantic SFX continues after its authored tail")
    peak = max(max(abs(x), abs(y)) for x, y in output)
    require(peak == receipt.get("sample_peak_pcm16"),
            "SFX PCM peak differs from the native mixer report")
    return {
        "source_sha256":[sha(a),sha(b)],
        "mixed_sha256":sha(mixed),
        "measured_source_peak_pcm16":peak,
        "max_sample_difference_pcm16":worst,
        "exact_sfx_sample_count":MASTER_FRAMES,
        "real_imported_original_stems":2,
    }


def tone(series: list[int], hz: int, start: float, stop: float) -> float:
    begin, end = int(start * SAMPLE_RATE), int(stop * SAMPLE_RATE)
    require(len(series) >= end and end - begin >= 1000, "Incomplete AAC frequency window")
    real = imag = 0.0
    for i in range(begin, end, 4):
        v = float(series[i])
        angle = 2 * math.pi * hz * i / SAMPLE_RATE
        real += v * math.cos(angle)
        imag += v * math.sin(angle)
    return 2.0 * math.hypot(real, imag) / len(range(begin, end, 4))


def energy(samples: list[int], start: float, stop: float) -> float:
    a, b = int(start*SAMPLE_RATE), int(stop*SAMPLE_RATE)
    require(b <= len(samples), "AAC decoded stream too short for timing window")
    return math.sqrt(sum(s*s for s in samples[a:b:4]) / len(range(a, b, 4)))


def inspect_decoded_sfx_audio(pcm_data: bytes) -> dict[str, Any]:
    require(len(pcm_data) % 4 == 0, "AAC stereo sample block incomplete")
    n = len(pcm_data) // 4
    require(MASTER_FRAMES-2400 <= n <= MASTER_FRAMES+4800,
            "AAC duration not aligned to exact 60-frame film")
    source = list(struct.iter_unpack("<hh", pcm_data))
    left = [l for l, _ in source]
    right = [r for _, r in source]
    first = {
        "l440":tone(left,440,.41,.56), "l740":tone(left,740,.41,.56),
        "r660":tone(right,660,.41,.56), "r980":tone(right,980,.41,.56),
    }
    both = {
        "l440":tone(left,440,.75,.90), "l740":tone(left,740,.75,.90),
        "r660":tone(right,660,.75,.90), "r980":tone(right,980,.75,.90),
    }
    second = {
        "l440":tone(left,440,1.09,1.24), "l740":tone(left,740,1.09,1.24),
        "r660":tone(right,660,1.09,1.24), "r980":tone(right,980,1.09,1.24),
    }
    require(first["l440"] > 250 and first["r660"] > 250
            and first["l440"] > 5 * max(first["l740"], 1)
            and first["r660"] > 5 * max(first["r980"], 1),
            "Decoded AAC first original SFX absent, early, late or wrong channel")
    require(both["l440"] > 90 and both["l740"] > 90
            and both["r660"] > 90 and both["r980"] > 90,
            "Decoded AAC lost actual simultaneous SFX sources")
    require(second["l740"] > 180 and second["r980"] > 180
            and second["l740"] > 5 * max(second["l440"], 1)
            and second["r980"] > 5 * max(second["r660"], 1),
            "Decoded AAC second original SFX absent or delayed")
    mid = max(energy(left,.41,.56),energy(right,.41,.56))
    early = max(energy(left,.02,.14),energy(right,.02,.14))
    late = max(energy(left,1.68,1.85),energy(right,1.68,1.85))
    require(mid > 300 and early < .035 * mid and late < .035 * mid,
            "Decoded AAC has wrong source onset or retained an unapproved SFX tail")
    # Independently check the first meaningful sound against the 0.200s
    # nominal source cue; allow at most 1 AAC frame (1024 samples) jitter.
    onset = None
    for i in range(0, min(n, 16000), 240):
        if max(energy(left, i/SAMPLE_RATE, (i+240)/SAMPLE_RATE),
               energy(right, i/SAMPLE_RATE, (i+240)/SAMPLE_RATE)) > 80:
            onset = i
            break
    require(onset is not None and abs(onset - 6*1600) <= 1024,
            "Decoded AAC SFX onset moved beyond one 1024-sample packet")
    return {
        "decoded_pcm_frames":n,
        "stereo_channel_identity":"confirmed_scene_linked_440_660_and_740_980_hz",
        "measured_first_onset_sample":onset,
        "expected_first_onset_sample":9600,
        "absolute_first_onset_error_samples":abs(onset-9600),
        "semantic_sfx_windows_pcm16": {
            "first_only":{k:round(v,2) for k,v in first.items()},
            "overlap":{k:round(v,2) for k,v in both.items()},
            "second_only":{k:round(v,2) for k,v in second.items()},
            "silence_pre_rms":round(early,2),
            "silence_post_rms":round(late,2),
        },
    }


def inspect_native_sfx_av(
    master: Path, source_folder: Path, receipt: dict[str, Any],
    ffmpeg: Path, ffprobe: Path,
) -> dict[str, Any]:
    original = inspect_original_sources(source_folder, receipt)
    require(master.is_file() and not master.is_symlink()
            and 1024 < master.stat().st_size <= MAX_MP4_BYTES,
            "Native AV master is not a verified bounded file")
    meta = _run_decoder([
        str(ffprobe), "-v", "error", "-count_frames", "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,sample_rate,channels:format=duration",
        "-of", "json", str(master),
    ], max_bytes=128*1024)
    streams = inspect_streams(json.loads(meta),expected_frames=60,expected_size=(1920,1080))
    decoded = _run_decoder([
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(master), "-map", "0:a:0", "-ac", "2", "-ar", "48000",
        "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
    ], max_bytes=MAX_DECODED_PCM_BYTES)
    audio = inspect_decoded_sfx_audio(decoded)
    pixels = _run_decoder([
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(master), "-map", "0:v:0",
        "-vf", r"select=eq(n\,0)+eq(n\,30),scale=160:90:flags=bicubic,format=rgb24",
        "-vsync", "0", "-f", "rawvideo", "pipe:1",
    ], max_bytes=2*FRAME_WIDTH*FRAME_HEIGHT*3)
    video = inspect_decoded_video(pixels)
    return {
        "evidence_scope":"real_same_project_original_sfx_pcm_to_native_sewright_av_aac_and_decoded_mp4",
        "source_wav_sha256":original["mixed_sha256"],
        "master_sha256":sha(master),
        "technical_source_pcm":"PASS",
        "technical_h264_aac_mux":"PASS",
        "human_creative_approval":"REQUIRED",
        "rights_certification":"NOT_PERFORMED",
        "project_id":receipt["project_id"],
        "generation":receipt["generation"],
        "revision":receipt["revision"],
        **original, **streams, **audio, **video,
    }
