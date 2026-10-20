#!/usr/bin/env python3
"""Independent whole-PCM AAC source-tail oracle for the original 33-frame MLT.

Reuses the *real*, SHA-locked first-party 48kHz/PCM16/stereo source from
original GitHub run 39054798740. The original WAV has distinct authored left
400Hz / right 600Hz signals through frame 52800. The old Semwright MLT master
silences the right channel from frame 52544; the independent direct-FFmpeg
AAC positive control preserves it. Both have 53248 decoded PCM sample frames.

This script never invokes the Semwright Broker, creates native media or
retries UNKNOWN operations. On a new A6 G2, the operator supplies a fresh
source-bound MP4 digest and separately authenticated provider receipts.
RMS/correlation observe content in the *authored* frames [0,52800), never
promote AAC encoder padding [52800,decoded_frames) as preserved source.

The thresholds are conservative classification gates calibrated on frozen
negative and independent positive originals. They are not human listening
approval, exact PCM reconstruction or a substitute for loaded-module proof.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import wave

ORIGINAL_SOURCE_SHA256 = "66aeaa3aa6fff53834ab4003671ce79068bcbd6f631609f0f988b3ac9744ebe9"
ORIGINAL_SOURCE_WAV_BYTES = 211244
ORIGINAL_PCM_FRAMES = 52_800
SOURCE_RATE = 48_000
SOURCE_CHANNELS = 2
VIDEO_FRAMES = 33
MAX_MP4_BYTES = 64 * 1024 * 1024
MAX_PCM_FRAMES = ORIGINAL_PCM_FRAMES + 1024
EARLY = (4_800, 9_600)
PRE_TAIL = (52_352, 52_416)
TERMINAL = (52_544, 52_800)
END64 = (52_736, 52_800)
SHA256_LENGTH = 64


class SourceMediaUnverified(ValueError):
    """Original source or decoded whole media cannot be trusted."""


def require(ok: object, reason: str) -> None:
    if not ok:
        raise SourceMediaUnverified(reason)


def checksum(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def checked(path: Path, expected: str, budget: int) -> bytes:
    require(path.is_absolute() and path.is_file() and not path.is_symlink()
            and not any(parent.is_symlink() for parent in path.parents),
            "original_media_not_absolute_regular")
    require(isinstance(expected, str) and len(expected) == SHA256_LENGTH
            and all(letter in "0123456789abcdef" for letter in expected),
            "original_media_sha256_not_frozen")
    first = path.stat()
    require(0 < first.st_size <= budget, "original_media_exceeds_budget")
    raw = path.read_bytes()
    last = path.stat()
    require((first.st_ino, first.st_size, first.st_mtime_ns)
            == (last.st_ino, last.st_size, last.st_mtime_ns)
            and len(raw) == first.st_size,
            "original_media_changed_during_read")
    require(checksum(raw) == expected, "original_media_SHA256_changed")
    return raw


def source_frames(source_wav: bytes) -> list[tuple[int, int]]:
    require(checksum(source_wav) == ORIGINAL_SOURCE_SHA256
            and len(source_wav) == ORIGINAL_SOURCE_WAV_BYTES,
            "original_stereo_fixture_SHA_or_bytes_changed")
    try:
        with wave.open(io.BytesIO(source_wav), "rb") as wav:
            require(wav.getcomptype() == "NONE"
                    and wav.getnchannels() == SOURCE_CHANNELS
                    and wav.getsampwidth() == 2
                    and wav.getframerate() == SOURCE_RATE
                    and wav.getnframes() == ORIGINAL_PCM_FRAMES,
                    "original_stereo_WAV_source_clock_or_format_changed")
            pcm = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as error:
        raise SourceMediaUnverified("original_PCM16_WAV_not_readable") from error
    require(len(pcm) == ORIGINAL_PCM_FRAMES * 4,
            "original_stereo_WAV_source_PCM_changed")
    return list(struct.iter_unpack("<hh", pcm))


def decoded_frames(pcm: bytes) -> list[tuple[int, int]]:
    require(len(pcm) % 4 == 0
            and ORIGINAL_PCM_FRAMES * 4 <= len(pcm) <= MAX_PCM_FRAMES * 4,
            "whole_decoded_AAC_sample_clock_invalid")
    return list(struct.iter_unpack("<hh", pcm))


def rms(frames: list[tuple[int, int]], channel: int,
        start: int, end: int) -> float:
    require(0 <= start < end <= len(frames),
            "PCM_reference_window_outside_decoded_source")
    return math.sqrt(
        sum(float(frames[i][channel]) ** 2 for i in range(start, end))
        / (end - start)
    )


def signal_correlation(original: list[tuple[int, int]],
                       decoded: list[tuple[int, int]], channel: int,
                       start: int, end: int) -> dict:
    """Measure actual source waveform shape allowing at most +/-12 AAC samples.

    The same bounded window is compared against *original* PCM16 source;
    a second unrelated 600Hz signal or swapped L/R content cannot pass merely
    because energy is non-zero.
    """
    x = [original[i][channel] for i in range(start, end)]
    mean = sum(x) / len(x)
    variance = sum((v - mean) ** 2 for v in x)
    require(variance > 0, "source_PCM_tail_is_not_informative")
    best = (-1.0, 0)
    for lag in range(-12, 13):
        y = [decoded[i + lag][channel] for i in range(start, end)]
        ym = sum(y) / len(y)
        power = sum((v - ym) ** 2 for v in y)
        correlation = (
            sum((a - mean) * (b - ym) for a, b in zip(x, y))
            / math.sqrt(variance * power)
            if power > 0 else 0.0
        )
        if correlation > best[0]:
            best = (correlation, lag)
    return {"max_correlation": round(best[0], 6),
            "source_relative_lag": best[1]}


def window(original: list[tuple[int, int]],
           decoded: list[tuple[int, int]], start: int, end: int) -> dict:
    data = {}
    for channel, name in ((0, "left"), (1, "right")):
        a = rms(original, channel, start, end)
        b = rms(decoded, channel, start, end)
        require(a > 100.0, "original_source_PCM_window_silent_or_malformed")
        shape = signal_correlation(original, decoded, channel, start, end)
        data[name] = {
            "source_rms": round(a, 4),
            "decoded_rms": round(b, 4),
            "energy_ratio": round(b / a, 6),
            "source_nonzero_frames": sum(original[i][channel] != 0
                                         for i in range(start, end)),
            "decoded_nonzero_frames": sum(decoded[i][channel] != 0
                                          for i in range(start, end)),
            **shape,
        }
    return {
        "start_source_frame": start,
        "end_source_frame_exclusive": end,
        "left": data["left"], "right": data["right"],
    }


def analyze_original_AAC(source_wav: bytes, decoded_pcm: bytes) -> dict:
    original = source_frames(source_wav)
    decoded = decoded_frames(decoded_pcm)
    windows = {
        "earlier_original_content": window(original, decoded, *EARLY),
        "before_last_AAC_block": window(original, decoded, *PRE_TAIL),
        "authored_terminal_256": window(original, decoded, *TERMINAL),
        "authored_last_64": window(original, decoded, *END64),
    }
    early = windows["earlier_original_content"]
    last = windows["authored_terminal_256"]
    last64 = windows["authored_last_64"]
    before = windows["before_last_AAC_block"]
    problems = []
    for channel in ("left", "right"):
        if (early[channel]["energy_ratio"] < 0.55
                or early[channel]["max_correlation"] < 0.70):
            problems.append("original_" + channel + "_content_not_retained")
    # Informative original source must exist *before* AAC encoder padding.
    require(last["right"]["source_nonzero_frames"] >= 200
            and last64["right"]["source_nonzero_frames"] >= 45
            and last["left"]["source_nonzero_frames"] >= 200,
            "original_stereo_source_terminal_content_not_informative")
    for key, sample in (
        ("right_pre_tail", before["right"]),
        ("right_terminal256", last["right"]),
        ("right_terminal64", last64["right"]),
        ("left_terminal256", last["left"]),
    ):
        if (sample["energy_ratio"] < 0.55
                or sample["max_correlation"] < 0.70):
            problems.append(key + "_source_PCM_lost_or_misaligned")
    if last["right"]["decoded_nonzero_frames"] < 160:
        problems.append("right_terminal_256_original_PCM_muted")
    return {
        "schema": "motionwright.native.mlt-original-stereo-tail-content/1",
        "original_33_video_frames": VIDEO_FRAMES,
        "original_source_pcm_frames": ORIGINAL_PCM_FRAMES,
        "original_source_pcm_rate": SOURCE_RATE,
        "decoded_whole_PCM_frames": len(decoded),
        "decoded_AAC_extra_padding_frames": len(decoded) - ORIGINAL_PCM_FRAMES,
        "decoded_whole_pcm_sha256": checksum(decoded_pcm),
        "frozen_original_source_wav_sha256": ORIGINAL_SOURCE_SHA256,
        "observed_PCM_windows": windows,
        "independent_source_content_verdict": "RED" if problems else "PASS_CONTENT",
        "reasons": problems,
        "AAC_padding_not_counted_as_authored_source": True,
        "source_PCM_equality_claim": False,
        "full_53k_PCM_decoded_and_hashed": True,
        "original_Broker_provider_loaded_module_verified_here": False,
        "human_listening_acceptance": False,
        "release_authorized": False,
    }


def verify_mp4_profile(probe: dict) -> dict:
    streams = probe.get("streams")
    require(isinstance(streams, list) and len(streams) == 2,
            "real_MP4_has_wrong_stream_inventory")
    video = [item for item in streams if item.get("codec_type") == "video"]
    audio = [item for item in streams if item.get("codec_type") == "audio"]
    require(len(video) == len(audio) == 1,
            "real_MP4_does_not_have_one_H264_and_one_AAC")
    v, a = video[0], audio[0]
    require(v.get("codec_name") == "h264"
            and v.get("width") == 1280 and v.get("height") == 720
            and v.get("r_frame_rate") == "30/1"
            and v.get("nb_read_frames") == str(VIDEO_FRAMES),
            "real_MP4_video_clock_or_codec_invalid")
    require(a.get("codec_name") == "aac"
            and a.get("sample_rate") == str(SOURCE_RATE)
            and a.get("channels") == SOURCE_CHANNELS,
            "real_MP4_AAC_source_stereo_stream_not_present")
    return {
        "video_codec": "h264", "audio_codec": "aac",
        "real_video_frames": VIDEO_FRAMES,
        "audio_rate": SOURCE_RATE, "audio_channels": SOURCE_CHANNELS,
    }


def observe_original_master(source_wav_path: Path, original_wav_sha: str,
                            master_mp4: Path, master_mp4_sha: str,
                            ffmpeg: str, ffprobe: str) -> dict:
    require(original_wav_sha == ORIGINAL_SOURCE_SHA256,
            "original_stereo_source_must_be_the_frozen_first_party_fixture")
    source = checked(source_wav_path, original_wav_sha, ORIGINAL_SOURCE_WAV_BYTES)
    checked(master_mp4, master_mp4_sha, MAX_MP4_BYTES)
    probed = subprocess.run(
        [ffprobe, "-v", "error", "-count_frames",
         "-show_entries",
         "stream=codec_name,codec_type,width,height,r_frame_rate,nb_read_frames,sample_rate,channels",
         "-of", "json", str(master_mp4)],
        capture_output=True, check=True, timeout=45,
    )
    require(len(probed.stdout) <= 64 * 1024,
            "real_MP4_ffprobe_receipt_exceeded_budget")
    video = verify_mp4_profile(json.loads(probed.stdout))
    decoded = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-i", str(master_mp4),
         "-map", "0:a:0", "-acodec", "pcm_s16le",
         "-ac", "2", "-ar", str(SOURCE_RATE), "-f", "s16le", "pipe:1"],
        capture_output=True, check=True, timeout=90,
    )
    require(len(decoded.stdout) <= MAX_PCM_FRAMES * 4,
            "whole_original_AAC_decoder_exceeded_bounded_PCM_source")
    require(checksum(source) == original_wav_sha
            and checked(master_mp4, master_mp4_sha, MAX_MP4_BYTES) is not None,
            "real_original_media_changed_during_full_decode")
    report = analyze_original_AAC(source, decoded.stdout)
    report["verified_original_video_stream"] = video
    report["new_master_mp4_sha256"] = master_mp4_sha
    report["decode_performed_via_original_ffmpeg"] = True
    report["original_master_provider_mutation_issued"] = False
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--original-wav", type=Path, required=True)
    p.add_argument("--original-wav-sha256", required=True)
    p.add_argument("--master", type=Path, required=True)
    p.add_argument("--master-sha256", required=True)
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--ffprobe", default="ffprobe")
    p.add_argument("--receipt", type=Path, required=True)
    args = p.parse_args()
    require(args.receipt.is_absolute() and not args.receipt.is_symlink()
            and not args.receipt.exists() and args.receipt.parent.is_dir(),
            "whole_AAC_tail_receipt_must_be_new_and_private")
    result = observe_original_master(
        args.original_wav, args.original_wav_sha256,
        args.master, args.master_sha256, args.ffmpeg, args.ffprobe,
    )
    with args.receipt.open("x", encoding="utf-8") as output:
        json.dump(result, output, sort_keys=True, indent=2)
        output.write("\n")
    print(json.dumps({
        "schema": result["schema"], "verdict": result["independent_source_content_verdict"],
        "reasons": result["reasons"],
        "whole_source_pcm_sha256": result["decoded_whole_pcm_sha256"],
        "human_acceptance": False,
    }, sort_keys=True))
    if result["independent_source_content_verdict"] != "PASS_CONTENT":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
