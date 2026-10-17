#!/usr/bin/env python3
"""Independent bounded PCM/AAC readback against an exact original mix WAV.

This reader does not render, normalize, trim, pad, align or repair media. Its
numeric thresholds describe a reproducible technical comparison profile;
they do not grant listening approval, rights, mastering or editability.
Use the actual source WAV and final MP4 retained by the native producer.
"""
from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import subprocess
import sys
import threading
import wave
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

SAMPLE_RATE = 48_000
CHANNELS = 2
MAX_SAMPLES = SAMPLE_RATE * 60
MAX_MEDIA_BYTES = 512 * 1024 * 1024


class ReadbackError(ValueError):
    """A fixed diagnostic safe to retain without decoder output or paths."""


def require(value: bool, reason: str) -> None:
    if not value:
        raise ReadbackError(reason)


@dataclass(frozen=True)
class ContentLimits:
    minimum_correlation: float = 0.97
    maximum_relative_error: float = 0.25
    minimum_gain: float = 0.80
    maximum_gain: float = 1.20
    maximum_silent_sample: int = 128
    minimum_lag_margin: float = 0.00001
    block_samples: int = 2048
    diagnostic_lag_samples: int = 16

    def validate(self) -> None:
        require(0 < self.minimum_correlation <= 1, "invalid_correlation_limit")
        require(0 < self.maximum_relative_error <= 1, "invalid_error_limit")
        require(0 < self.minimum_gain <= 1 <= self.maximum_gain <= 2,
                "invalid_gain_limits")
        require(0 <= self.maximum_silent_sample <= 32767, "invalid_silence_limit")
        require(0 < self.minimum_lag_margin < 1, "invalid_lag_margin")
        require(64 <= self.block_samples <= 8192, "invalid_block_size")
        require(1 <= self.diagnostic_lag_samples <= 64, "invalid_lag_budget")


@dataclass(frozen=True)
class MediaClock:
    frames: int
    width: int
    height: int
    fps_num: int = 30
    fps_den: int = 1

    @property
    def samples(self) -> int:
        require(all(type(v) is int and v > 0 for v in (
            self.frames, self.width, self.height, self.fps_num, self.fps_den
        )), "invalid_media_clock")
        require(self.width <= 4096 and self.height <= 4096, "geometry_budget")
        count = Fraction(self.frames * SAMPLE_RATE * self.fps_den, self.fps_num)
        require(count.denominator == 1, "fractional_audio_sample_clock")
        require(1024 <= count.numerator <= MAX_SAMPLES, "sample_budget")
        return count.numerator


def _integer(value: object, reason: str) -> int:
    require(not isinstance(value, bool), reason)
    if isinstance(value, int):
        return value
    require(isinstance(value, str), reason)
    try:
        result = int(value)
    except ValueError:
        raise ReadbackError(reason) from None
    require(str(result) == value, reason)
    return result


def _fraction(value: object, reason: str) -> Fraction:
    require(isinstance(value, str), reason)
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError):
        raise ReadbackError(reason) from None
    return result


def _checked_file(path: Path, expected_sha256: str) -> tuple[str, tuple]:
    require(len(expected_sha256) == 64
            and all(c in "0123456789abcdef" for c in expected_sha256),
            "invalid_expected_digest")
    require(path.is_file() and not path.is_symlink(), "regular_media_file_required")
    before = path.stat()
    require(0 < before.st_size <= MAX_MEDIA_BYTES, "media_byte_budget")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    token = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after = path.stat()
    require(token == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            "media_changed_during_hash")
    require(digest.hexdigest() == expected_sha256, "source_or_master_digest_mismatch")
    return digest.hexdigest(), token


def _unchanged(path: Path, token: tuple) -> None:
    require(path.is_file() and not path.is_symlink(), "media_changed_during_readback")
    current = path.stat()
    require(token == (current.st_dev, current.st_ino, current.st_size,
                      current.st_mtime_ns), "media_changed_during_readback")


def bounded_decoder(argv: list[str], maximum_stdout: int,
                    timeout: float = 45) -> bytes:
    """Cap both streams while reading; timeout and overflow kill and reap."""
    require(0 < maximum_stdout <= MAX_MEDIA_BYTES, "decoder_output_budget")
    buffers = [bytearray(), bytearray()]
    overflows = [False, False]
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as process:
        def collect(index: int, stream, limit: int) -> None:
            try:
                while True:
                    block = stream.read(65536)
                    if not block:
                        break
                    if len(buffers[index]) + len(block) > limit:
                        overflows[index] = True
                        process.kill()
                        break
                    buffers[index].extend(block)
            finally:
                stream.close()

        threads = [
            threading.Thread(target=collect, args=(0, process.stdout, maximum_stdout),
                             daemon=True),
            threading.Thread(target=collect, args=(1, process.stderr, 65536),
                             daemon=True),
        ]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
        require(not timed_out, "decoder_timeout")
        require(not any(thread.is_alive() for thread in threads), "decoder_stream_not_closed")
        require(not any(overflows), "decoder_output_limit_exceeded")
        require(process.returncode == 0, "decoder_failed")
        require(not buffers[1].strip(), "decoder_reported_media_errors")
    return bytes(buffers[0])


def read_reference_wav(path: Path, expected_samples: int) -> bytes:
    try:
        with wave.open(str(path), "rb") as audio:
            require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                     audio.getcomptype()) == (2, 2, SAMPLE_RATE, "NONE"),
                    "reference_must_be_original_pcm16_stereo_48khz")
            require(audio.getnframes() == expected_samples, "reference_sample_clock_mismatch")
            data = audio.readframes(expected_samples + 1)
    except (wave.Error, EOFError):
        raise ReadbackError("reference_wav_invalid") from None
    require(len(data) == expected_samples * 4, "reference_wav_physically_truncated")
    return data


def _channels(pcm: bytes) -> tuple[array.array, array.array]:
    require(len(pcm) % 4 == 0, "partial_stereo_sample")
    values = array.array("h")
    values.frombytes(pcm)
    if sys.byteorder != "little":
        values.byteswap()
    return values[::2], values[1::2]


def _metrics(reference, observed) -> dict:
    reference_energy = sum(float(a) * a for a in reference)
    observed_energy = sum(float(b) * b for b in observed)
    cross = sum(float(a) * b for a, b in zip(reference, observed, strict=True))
    error = sum(float(a - b) ** 2 for a, b in zip(reference, observed, strict=True))
    if reference_energy == 0:
        return {"silent_reference": True,
                "observed_peak": max((abs(b) for b in observed), default=0)}
    return {
        "silent_reference": False,
        "correlation": cross / math.sqrt(reference_energy * observed_energy)
                       if observed_energy else 0.0,
        "gain": math.sqrt(observed_energy / reference_energy),
        "relative_error": math.sqrt(error / reference_energy),
    }


def _lag_diagnostic(reference, observed, limits: ContentLimits) -> dict:
    radius = limits.diagnostic_lag_samples
    step = max(1, (len(reference) - 2 * radius) // 4096)
    positions = range(radius, len(reference) - radius, step)
    values = [reference[i] for i in positions]
    scores = {}
    for lag in range(-radius, radius + 1):
        shifted = [observed[i + lag] for i in positions]
        metric = _metrics(values, shifted)
        require(not metric["silent_reference"], "reference_has_no_timing_signature")
        scores[lag] = metric["correlation"]
    best = max(scores, key=scores.get)
    margin = scores[0] - max(value for lag, value in scores.items() if lag != 0)
    require(best == 0 and margin >= limits.minimum_lag_margin,
            "audio_alignment_not_uniquely_at_zero")
    return {"best_lag_samples": best, "zero_lag_margin": margin,
            "searched_lag_samples": radius, "aligned_or_shifted_output": False}


def compare_pcm(reference: bytes, observed: bytes,
                limits: ContentLimits = ContentLimits()) -> dict:
    """Measure every logical sample at its declared position, including tail.

    Extra decoder samples are reported as an AAC access-unit suffix. Missing
    logical samples fail. Diagnostic correlation never shifts the comparison.
    """
    limits.validate()
    require(len(reference) % 4 == 0 and len(observed) % 4 == 0,
            "partial_stereo_sample")
    samples = len(reference) // 4
    require(1024 <= samples <= MAX_SAMPLES, "reference_sample_budget")
    physical = len(observed) // 4
    require(samples <= physical <= samples + 1024, "decoded_content_sample_count")
    refs, outs = _channels(reference), _channels(observed)
    records = []
    alignment = []
    for channel in range(2):
        for start in range(0, samples, limits.block_samples):
            end = min(samples, start + limits.block_samples)
            # A final small remainder is tested in a full preceding window;
            # no tail samples are dropped and every logical sample is covered.
            window_start = max(0, end - limits.block_samples) if end == samples else start
            metric = _metrics(refs[channel][window_start:end], outs[channel][window_start:end])
            if metric["silent_reference"]:
                require(metric["observed_peak"] <= limits.maximum_silent_sample,
                        "unexpected_audio_in_silent_window")
            else:
                require(metric["correlation"] >= limits.minimum_correlation,
                        "local_audio_content_correlation")
                require(limits.minimum_gain <= metric["gain"] <= limits.maximum_gain,
                        "local_audio_gain")
                require(metric["relative_error"] <= limits.maximum_relative_error,
                        "local_audio_content_error")
            records.append({"channel": channel, "start_sample": window_start,
                            "end_exclusive": end, **metric})
        alignment.append({"channel": channel,
                          **_lag_diagnostic(refs[channel], outs[channel], limits)})
    return {
        "reference_sample_frames": samples, "decoded_sample_frames": physical,
        "decoded_access_unit_suffix_samples": physical - samples,
        "reference_pcm_sha256": hashlib.sha256(reference).hexdigest(),
        "decoded_pcm_sha256": hashlib.sha256(observed).hexdigest(),
        "logical_interval": [0, samples], "all_logical_samples_compared": True,
        "windows": records, "alignment": alignment, "limits": vars(limits),
    }


def validate_clock(probe: dict, clock: MediaClock) -> dict:
    samples = clock.samples
    streams = probe.get("streams", [])
    require(isinstance(streams, list) and len(streams) == 2, "exact_av_streams_required")
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    require(len(videos) == len(audios) == 1, "exact_av_streams_required")
    video, audio = videos[0], audios[0]
    fps = Fraction(clock.fps_num, clock.fps_den)
    require(video.get("codec_name") == "h264"
            and (video.get("width"), video.get("height")) == (clock.width, clock.height)
            and _fraction(video.get("r_frame_rate"), "invalid_video_rate") == fps,
            "video_profile_mismatch")
    require(audio.get("codec_name") == "aac"
            and _integer(audio.get("sample_rate"), "invalid_sample_rate") == SAMPLE_RATE
            and audio.get("channels") == 2, "audio_profile_mismatch")
    video_base = _fraction(video.get("time_base"), "invalid_video_timebase")
    audio_base = _fraction(audio.get("time_base"), "invalid_audio_timebase")
    require(video_base > 0 and audio_base == Fraction(1, SAMPLE_RATE), "media_timebase_mismatch")
    require(_integer(video.get("start_pts"), "missing_video_start") == 0
            and _integer(audio.get("start_pts"), "missing_audio_start") == 0,
            "nonzero_av_start_timestamp")
    frames = probe.get("frames", [])
    require(isinstance(frames, list), "missing_decoded_frames")
    vf = [frame for frame in frames if frame.get("media_type") == "video"]
    af = [frame for frame in frames if frame.get("media_type") == "audio"]
    require(len(vf) == clock.frames
            and _integer(video.get("nb_read_frames"), "missing_video_frame_count") == clock.frames,
            "physical_video_frame_count")
    for index, frame in enumerate(vf):
        pts = _integer(frame.get("best_effort_timestamp", frame.get("pts")), "video_frame_without_pts")
        require(pts * video_base == Fraction(index, 1) / fps, "video_timestamp_discontinuity")
    cursor = 0
    for frame in af:
        require(frame.get("stream_index") == audio.get("index"), "audio_stream_identity")
        pts = _integer(frame.get("pts"), "audio_frame_without_pts")
        count = _integer(frame.get("nb_samples"), "audio_frame_without_sample_count")
        require(pts == cursor and 0 < count <= 1024, "audio_timestamp_discontinuity")
        cursor += count
    duration = _integer(audio.get("duration_ts"), "missing_audio_duration")
    require(samples <= duration <= samples + 1024, "audio_duration_clock_mismatch")
    require(samples <= cursor <= samples + 1024, "decoded_audio_frame_clock_mismatch")
    return {"video_frames": len(vf), "audio_frames": len(af),
            "decoded_audio_samples_from_frames": cursor,
            "audio_duration_samples": duration, "first_video_pts": 0, "first_audio_pts": 0,
            "video_fps": str(fps), "audio_timebase": str(audio_base),
            "exact_contiguous_frame_timestamps": True}


def inspect_master(reference: Path, master: Path, *, reference_sha256: str,
                   master_sha256: str, clock: MediaClock, ffmpeg: Path,
                   ffprobe: Path, limits: ContentLimits = ContentLimits()) -> dict:
    samples = clock.samples
    _, reference_token = _checked_file(reference, reference_sha256)
    _, master_token = _checked_file(master, master_sha256)
    original_pcm = read_reference_wav(reference, samples)
    entries = (
        "stream=index,codec_type,codec_name,width,height,r_frame_rate,time_base,"
        "start_pts,duration_ts,sample_rate,channels,nb_read_frames:"
        "frame=media_type,stream_index,pts,best_effort_timestamp,nb_samples"
    )
    raw_probe = bounded_decoder([
        str(ffprobe), "-v", "error", "-count_frames", "-show_streams", "-show_frames",
        "-show_entries", entries, "-of", "json", str(master)
    ], 4 * 1024 * 1024)
    try:
        probe = json.loads(raw_probe)
    except (ValueError, UnicodeDecodeError):
        raise ReadbackError("invalid_decoder_json") from None
    require(isinstance(probe, dict), "invalid_decoder_json")
    timing = validate_clock(probe, clock)
    # No -ar, -ac, trim, padding, normalization or alignment filter: the
    # original codec/rate/channels were checked before decoding this stream.
    decoded_pcm = bounded_decoder([
        str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-i", str(master),
        "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", "-f", "s16le", "-"
    ], (samples + 1024) * 4)
    require(len(decoded_pcm) // 4 == timing["decoded_audio_samples_from_frames"],
            "physical_pcm_and_frame_count_disagree")
    content = compare_pcm(original_pcm, decoded_pcm, limits)
    _unchanged(reference, reference_token)
    _unchanged(master, master_token)
    return {
        "schema": "motionwright.native-original-mix-readback/1",
        "technical_content_readback": "PASS",
        "reference_wav_sha256": reference_sha256, "final_mp4_sha256": master_sha256,
        "clock": timing, "content": content,
        "listening_approval": False, "rights_approval": False,
        "editable_source_reopen_verified": False, "independent_loudness_verified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-wav", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--reference-sha256", required=True)
    parser.add_argument("--master-sha256", required=True)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps-num", type=int, default=30)
    parser.add_argument("--fps-den", type=int, default=1)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inspect_master(
            args.reference_wav, args.master, reference_sha256=args.reference_sha256,
            master_sha256=args.master_sha256,
            clock=MediaClock(args.frames, args.width, args.height, args.fps_num, args.fps_den),
            ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
        )
    except (ReadbackError, OSError):
        print(json.dumps({"technical_content_readback": "FAIL",
                          "diagnostic": "original_mix_media_readback_rejected"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
