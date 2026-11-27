"""Read the sample-exact clock of the retained installed AAC31 original."""
from __future__ import annotations

from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path

from linux_hybrid_original_receipt import Master, require
from manim_native_readback import run_bounded
from native_mlt_loaded_module import _verified_binary
from native_mp4_aac_presentation import inspect_mp4_aac_presentation
from native_pcm_source_fidelity import inspect_source_pcm16_stereo_content

SAMPLES = 49_600
FRAMES = 31
RATE = 48_000
MAX_TOOL_BYTES = 768 * 1024 * 1024


def checked_bytes(path: Path, expected_bytes: int, digest: str) -> bytes:
    require(path.is_absolute() and path == path.resolve(strict=True) and path.is_file(),
            "expected canonical AAC31 original file")
    with path.open("rb") as original:
        data = original.read(expected_bytes + 1)
    require(len(data) == expected_bytes and sha256(data).hexdigest() == digest,
            "AAC31 original bytes changed")
    return data


def observe_clock(master: Master, wave: Path, wave_sha: str, *,
                  ffprobe: Path, ffprobe_sha: str, ffmpeg: Path, ffmpeg_sha: str) -> dict:
    original = checked_bytes(master.path, master.size, master.sha256)
    wav = checked_bytes(wave, 44 + SAMPLES * 4, wave_sha)
    fields = ("stream=codec_name,codec_type,time_base,duration_ts,width,height"
              ":frame=best_effort_timestamp")
    with _verified_binary(ffprobe, ffprobe_sha, MAX_TOOL_BYTES):
        observed, _ = run_bounded(
            [str(ffprobe), "-v", "error", "-select_streams", "v:0",
             "-show_streams", "-show_frames", "-show_entries", fields, "-of", "json", "pipe:0"],
            128 * 1024, 30, input_bytes=original,
        )
    probe = json.loads(observed)
    [video] = probe["streams"]
    require((video["codec_name"], video["codec_type"], video["width"], video["height"])
            == ("h264", "video", 1920, 1080), "unexpected AAC31 video profile")
    timebase = Fraction(video["time_base"])
    pts = [int(frame["best_effort_timestamp"]) * timebase for frame in probe["frames"]]
    require(pts == [Fraction(index, 30) for index in range(FRAMES)]
            and int(video["duration_ts"]) * timebase == Fraction(FRAMES, 30),
            "AAC31 presentation timestamps differ from the authored cut")
    with _verified_binary(ffmpeg, ffmpeg_sha, MAX_TOOL_BYTES):
        pcm, _ = run_bounded(
            [str(ffmpeg), "-v", "error", "-nostdin", "-i", "pipe:0", "-map", "0:a:0",
             "-acodec", "pcm_s16le", "-ar", str(RATE), "-ac", "2", "-f", "s16le", "pipe:1"],
            (SAMPLES + 1024) * 4, 30, input_bytes=original,
        )
    require(len(pcm) % 4 == 0, "unexpected AAC31 decoded stereo layout")
    aac = inspect_mp4_aac_presentation(original, source_frames=SAMPLES, decoded_frames=len(pcm) // 4)
    content = inspect_source_pcm16_stereo_content(
        wav, pcm, original_wav_sha256=wave_sha, decoded_pcm_sha256=sha256(pcm).hexdigest(),
    )
    checked_bytes(master.path, master.size, master.sha256)
    checked_bytes(wave, len(wav), wave_sha)
    return {
        "original_sha256": master.sha256,
        "frame_count": len(pts),
        "frame_timestamps": [str(value) for value in pts],
        "aac_presentation": aac,
        "source_pcm_sha256": wave_sha,
        "decoded_pcm_sha256": sha256(pcm).hexdigest(),
        "source_pcm_minimum_correlation": content["minimum_source_window_correlation"],
        "ffprobe_sha256": ffprobe_sha,
        "ffmpeg_sha256": ffmpeg_sha,
    }
