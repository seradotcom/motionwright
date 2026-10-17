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
import os
import signal
import subprocess
import sys
import time
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
    diagnostic_lag_samples: int = 1600
    short_window_samples: int = 64
    minimum_short_window_rms: float = 128.0
    minimum_short_window_gain: float = 0.5
    maximum_short_window_gain: float = 2.5

    def validate(self) -> None:
        require(0 < self.minimum_correlation <= 1, "invalid_correlation_limit")
        require(0 < self.maximum_relative_error <= 1, "invalid_error_limit")
        require(0 < self.minimum_gain <= 1 <= self.maximum_gain <= 2,
                "invalid_gain_limits")
        require(0 <= self.maximum_silent_sample <= 32767, "invalid_silence_limit")
        require(0 < self.minimum_lag_margin < 1, "invalid_lag_margin")
        require(64 <= self.block_samples <= 8192, "invalid_block_size")
        require(type(self.diagnostic_lag_samples) is int
                and 1 <= self.diagnostic_lag_samples <= 4096, "invalid_lag_budget")
        require(type(self.short_window_samples) is int
                and 16 <= self.short_window_samples <= 256
                and self.short_window_samples % 2 == 0, "invalid_short_window_size")
        require(0 < self.minimum_short_window_rms <= 32767, "invalid_short_window_rms")
        require(0 < self.minimum_short_window_gain <= 1
                <= self.maximum_short_window_gain <= 4, "invalid_short_window_gain")


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


_DECODER_REAP_SECONDS = 1.0
_DECODER_POLL_SECONDS = 0.005
_WINDOWS_DECODER_LAUNCHER = (
    "import subprocess,sys\n"
    "if sys.stdin.buffer.read(1) != b'1': sys.exit(125)\n"
    "sys.exit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL))\n"
)


class _WindowsDecoderJob:
    """A private kill-on-close job; no breakaway or privilege changes."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", ctypes.c_ulonglong * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int,
                                         ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "PeekNamedPipe": ([wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                               ctypes.c_void_p], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        require(bool(self.handle), "decoder_job_creation_failed")
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise ReadbackError("decoder_job_configuration_failed")

    def assign(self, pid: int) -> None:
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE, for the child we just created.
        handle = self.kernel.OpenProcess(0x0101, False, pid)
        require(bool(handle), "decoder_job_process_access_failed")
        try:
            require(bool(self.kernel.AssignProcessToJobObject(self.handle, handle)),
                    "decoder_job_assignment_failed")
        finally:
            self.kernel.CloseHandle(handle)

    def read(self, stream, maximum: int) -> bytes | None:
        import msvcrt
        from ctypes import wintypes

        available = wintypes.DWORD()
        handle = msvcrt.get_osfhandle(stream.fileno())
        if not self.kernel.PeekNamedPipe(
            handle, None, 0, None, self.ctypes.byref(available), None
        ):
            error = self.ctypes.get_last_error()
            if error in (109, 232):  # ERROR_BROKEN_PIPE / ERROR_NO_DATA
                return b""
            raise ReadbackError("decoder_pipe_probe_failed")
        if not available.value:
            return None
        # This is the only reader of a byte-mode anonymous pipe. Reading at
        # most its available bytes cannot wait for a later write or pipe EOF.
        return os.read(stream.fileno(), min(maximum, available.value))

    def terminate(self) -> None:
        require(bool(self.kernel.TerminateJobObject(self.handle, 1)),
                "decoder_job_termination_failed")

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, None
            require(bool(self.kernel.CloseHandle(handle)), "decoder_job_close_failed")


def bounded_decoder(argv: list[str], maximum_stdout: int,
                    timeout: float = 45) -> bytes:
    """Bound pipe reads and leader lifetime; always terminate our process group/job.

    A single monotonic deadline covers reading and process completion. Cleanup
    gets at most one additional second to reap the direct child. OS process
    creation itself cannot be interrupted portably. No reader threads or
    buffered-pipe context manager may wait indefinitely during cleanup.
    """
    require(type(maximum_stdout) is int and 0 < maximum_stdout <= MAX_MEDIA_BYTES,
            "decoder_output_budget")
    require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
            "decoder_timeout_budget")
    deadline = time.monotonic() + timeout
    buffers = [bytearray(), bytearray()]
    limits = [maximum_stdout, 65536]
    process = None
    job = None
    try:
        if os.name == "nt":
            job = _WindowsDecoderJob()
            # The trusted launcher cannot create the decoder until its job
            # assignment succeeds. This closes the Popen/Assign spawn race.
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", "-c", _WINDOWS_DECODER_LAUNCHER,
                 *argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0,
            )
            job.assign(process.pid)
            require(time.monotonic() < deadline, "decoder_timeout")
            process.stdin.write(b"1")
            process.stdin.close()
        else:
            process = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0, start_new_session=True,
            )
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
        pending = {0: process.stdout, 1: process.stderr}
        while pending or process.poll() is None:
            require(time.monotonic() < deadline, "decoder_timeout")
            made_progress = False
            for index, stream in list(pending.items()):
                maximum = min(65536, limits[index] - len(buffers[index]) + 1)
                if job is not None:
                    block = job.read(stream, maximum)
                else:
                    try:
                        block = os.read(stream.fileno(), maximum)
                    except BlockingIOError:
                        block = None
                if block is None:
                    continue
                made_progress = True
                if not block:
                    pending.pop(index)
                    stream.close()
                    continue
                require(len(buffers[index]) + len(block) <= limits[index],
                        "decoder_output_limit_exceeded")
                buffers[index].extend(block)
            if not made_progress:
                time.sleep(min(_DECODER_POLL_SECONDS,
                               max(0, deadline - time.monotonic())))
        require(process.returncode == 0, "decoder_failed")
        require(not buffers[1].strip(), "decoder_reported_media_errors")
        return bytes(buffers[0])
    finally:
        # Even a successful direct child must not leave detached-from-parent
        # descendants running inside the process group/job that we own.
        try:
            if job is not None:
                job.terminate()
            elif process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            try:
                if process is not None:
                    # Also handles a Windows launcher whose job assignment
                    # failed; it has not received permission to spawn a child.
                    process.kill()
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
                    try:
                        process.wait(timeout=_DECODER_REAP_SECONDS)
                    except subprocess.TimeoutExpired:
                        raise ReadbackError("decoder_reap_timeout") from None
            finally:
                if job is not None:
                    job.close()


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
    radius = min(limits.diagnostic_lag_samples, (len(reference) - 1) // 4)
    count = min(512, len(reference) - 2 * radius)
    positions = [radius + at * (len(reference) - 2 * radius - 1) // (count - 1)
                 for at in range(count)]
    values = [reference[index] for index in positions]
    reference_energy = sum(value * value for value in values)
    require(reference_energy > 0, "reference_has_no_timing_signature")

    def correlation(signal_values, lag):
        selected = [signal_values[index + lag] for index in positions]
        energy = sum(value * value for value in selected)
        return sum(a * b for a, b in zip(values, selected, strict=True)) \
            / math.sqrt(reference_energy * energy) if energy else 0.0

    # Codec noise must not turn an intrinsically periodic reference into a
    # claimed time signature. First test identifiability on the source itself.
    reference_margin = 1.0 - max(correlation(reference, lag)
                                 for lag in range(-radius, radius + 1) if lag)
    require(reference_margin >= limits.minimum_lag_margin,
            "reference_timing_signature_ambiguous")
    scores = {lag: correlation(observed, lag) for lag in range(-radius, radius + 1)}
    best = max(scores, key=scores.get)
    margin = scores[0] - max(value for lag, value in scores.items() if lag)
    require(best == 0 and margin >= limits.minimum_lag_margin,
            "audio_alignment_not_uniquely_at_zero")
    return {"best_lag_samples": best, "zero_lag_margin": margin,
            "reference_zero_lag_margin": reference_margin,
            "searched_lag_samples": radius, "fingerprint_positions": count,
            "search_interval_samples": [-radius, radius],
            "absolute_delay_absence_proven": False,
            "aligned_or_shifted_output": False}


def compare_pcm(reference: bytes, observed: bytes,
                limits: ContentLimits = ContentLimits(), *,
                declared_suffix_samples: int = 0) -> dict:
    """Compare every logical sample, retaining the complete decoded bytes/hash.

    Only inspect_master may classify extra decoded samples as packet-declared
    padding: it derives declared_suffix_samples from validated frame/packet
    clocks. Direct PCM comparison admits no undeclared suffix. Diagnostic
    correlation does not shift, trim, normalize or repair the compared media.
    """
    limits.validate()
    require(len(reference) % 4 == 0 and len(observed) % 4 == 0,
            "partial_stereo_sample")
    samples = len(reference) // 4
    require(1024 <= samples <= MAX_SAMPLES, "reference_sample_budget")
    require(type(declared_suffix_samples) is int and 0 <= declared_suffix_samples <= 1024,
            "invalid_declared_decoder_suffix")
    physical = len(observed) // 4
    require(physical == samples + declared_suffix_samples, "undeclared_decoder_suffix_or_loss")
    refs, outs = _channels(reference), _channels(observed)
    records, short_records, alignment = [], [], []
    for channel in range(2):
        for start in range(0, samples, limits.block_samples):
            end = min(samples, start + limits.block_samples)
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
        size, hop = limits.short_window_samples, limits.short_window_samples // 2
        starts = list(range(0, samples - size + 1, hop))
        if starts[-1] != samples - size:
            starts.append(samples - size)
        for start in starts:
            end = start + size
            energy = sum(value * value for value in refs[channel][start:end])
            observed_energy = sum(value * value for value in outs[channel][start:end])
            reference_rms = math.sqrt(energy / size)
            if reference_rms >= limits.minimum_short_window_rms:
                gain = math.sqrt(observed_energy / energy)
                require(limits.minimum_short_window_gain <= gain <= limits.maximum_short_window_gain,
                        "short_window_audio_gain")
                short_records.append({"channel": channel, "start_sample": start,
                                      "end_exclusive": end, "reference_rms": reference_rms,
                                      "gain": gain})
        alignment.append({"channel": channel,
                          **_lag_diagnostic(refs[channel], outs[channel], limits)})
    return {
        "reference_sample_frames": samples, "decoded_sample_frames": physical,
        "decoded_access_unit_suffix_samples": physical - samples,
        "reference_pcm_sha256": hashlib.sha256(reference).hexdigest(),
        "decoded_pcm_sha256": hashlib.sha256(observed).hexdigest(),
        "logical_interval": [0, samples], "all_logical_samples_compared": True,
        "windows": records, "short_energy_windows": short_records,
        "alignment": alignment, "limits": vars(limits),
    }


def _packet_clock(probe: dict, video: dict, audio: dict, clock: MediaClock) -> tuple[dict, list]:
    samples = clock.samples
    packets = probe.get("packets")
    require(isinstance(packets, list) and bool(packets)
            and all(isinstance(packet, dict) for packet in packets), "missing_media_packets")
    require(all(packet.get("stream_index") in (video["index"], audio["index"])
                for packet in packets), "packet_stream_identity")
    vp = [packet for packet in packets if packet["stream_index"] == video["index"]]
    ap = [packet for packet in packets if packet["stream_index"] == audio["index"]]
    fps, video_base = Fraction(clock.fps_num, clock.fps_den), Fraction(video["time_base"])
    require(len(vp) == clock.frames and bool(ap), "physical_packet_count")
    video_pts = sorted(_integer(packet.get("pts"), "video_packet_without_pts") for packet in vp)
    require(all(pts * video_base == Fraction(index, 1) / fps
                for index, pts in enumerate(video_pts)), "video_packet_clock_mismatch")
    require(all(_integer(packet.get("duration"), "video_packet_without_duration") * video_base
                == 1 / fps for packet in vp), "video_packet_duration_mismatch")
    cursor = None
    initial_skip = final_discard = final_duration = final_pts = 0
    access_units = []
    for index, packet in enumerate(ap):
        pts = _integer(packet.get("pts"), "audio_packet_without_pts")
        duration = _integer(packet.get("duration"), "audio_packet_without_duration")
        require(_integer(packet.get("dts"), "audio_packet_without_dts") == pts,
                "audio_packet_decode_timestamp")
        require(0 < duration <= 1024 and (index == len(ap) - 1 or duration == 1024),
                "audio_packet_duration_mismatch")
        require(cursor is None or pts == cursor, "audio_packet_timestamp_discontinuity")
        side = packet.get("side_data_list", [])
        require(isinstance(side, list) and all(isinstance(item, dict) for item in side),
                "invalid_audio_packet_side_data")
        skips = [item for item in side if item.get("side_data_type") == "Skip Samples"]
        require(len(skips) <= 1, "duplicate_audio_skip_metadata")
        skip = _integer(skips[0].get("skip_samples", 0), "invalid_audio_skip_samples") if skips else 0
        discard = _integer(skips[0].get("discard_padding", 0), "invalid_audio_discard_padding") \
            if skips else 0
        require(0 <= skip <= 4096 and 0 <= discard <= 1024, "audio_skip_discard_budget")
        require(not skip or index == 0, "noninitial_audio_skip_samples")
        require(not discard or index == len(ap) - 1, "nonterminal_audio_discard_padding")
        if index == 0:
            initial_skip = skip
            require(pts + skip == 0, "undeclared_or_incoherent_audio_priming")
        cursor = pts + duration
        final_pts, final_duration, final_discard = pts, duration, discard
        if pts + 1024 > 0:
            access_units.append((max(0, pts), pts + 1024))
    # Packet duration may already describe the presented portion. A declared
    # discard therefore constrains the nominal LC access unit, rather than
    # being blindly subtracted a second time from a clipped packet duration.
    if final_discard:
        require(final_duration in (1024, 1024 - final_discard),
                "audio_packet_discard_duration_mismatch")
        presentation_end = final_pts + 1024 - final_discard
    else:
        presentation_end = cursor
    require(presentation_end == samples and final_pts < samples,
            "audio_packet_presentation_end_mismatch")
    suffix_budget = final_pts + 1024 - samples
    require(0 <= suffix_budget <= 1024, "audio_access_unit_suffix_budget")
    return {"video_packets": len(vp), "audio_packets": len(ap),
            "priming_skip_samples": initial_skip, "discard_padding_samples": final_discard,
            "final_audio_packet_duration_samples": final_duration,
            "audio_packet_presentation_end_samples": presentation_end,
            "declared_decoder_suffix_budget_samples": suffix_budget,
            "packet_priming_and_presentation_checked": True}, access_units


def validate_clock(probe: dict, clock: MediaClock) -> dict:
    samples = clock.samples
    streams = probe.get("streams", [])
    require(isinstance(streams, list) and len(streams) == 2
            and all(isinstance(stream, dict) for stream in streams), "exact_av_streams_required")
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    require(len(videos) == len(audios) == 1, "exact_av_streams_required")
    video, audio = videos[0], audios[0]
    video_index = _integer(video.get("index"), "missing_video_stream_index")
    audio_index = _integer(audio.get("index"), "missing_audio_stream_index")
    require(video_index >= 0 and audio_index >= 0 and video_index != audio_index,
            "media_stream_identity")
    fps = Fraction(clock.fps_num, clock.fps_den)
    require(video.get("codec_name") == "h264"
            and (video.get("width"), video.get("height")) == (clock.width, clock.height)
            and _fraction(video.get("r_frame_rate"), "invalid_video_rate") == fps,
            "video_profile_mismatch")
    require(audio.get("codec_name") == "aac" and audio.get("profile") == "LC"
            and _integer(audio.get("sample_rate"), "invalid_sample_rate") == SAMPLE_RATE
            and audio.get("channels") == 2, "audio_profile_mismatch")
    video_base = _fraction(video.get("time_base"), "invalid_video_timebase")
    audio_base = _fraction(audio.get("time_base"), "invalid_audio_timebase")
    require(video_base > 0 and audio_base == Fraction(1, SAMPLE_RATE), "media_timebase_mismatch")
    require(_integer(video.get("start_pts"), "missing_video_start") == 0
            and _integer(audio.get("start_pts"), "missing_audio_start") == 0,
            "nonzero_av_start_timestamp")
    require(_integer(video.get("duration_ts"), "missing_video_duration") * video_base
            == Fraction(clock.frames, 1) / fps, "video_duration_clock_mismatch")
    duration = _integer(audio.get("duration_ts"), "missing_audio_duration")
    require(duration == samples, "audio_duration_clock_mismatch")
    frames = probe.get("frames", [])
    require(isinstance(frames, list) and all(isinstance(frame, dict) for frame in frames),
            "missing_decoded_frames")
    require(all(frame.get("media_type") in ("video", "audio") for frame in frames),
            "unexpected_decoded_frame")
    vf = [frame for frame in frames if frame.get("media_type") == "video"]
    af = [frame for frame in frames if frame.get("media_type") == "audio"]
    require(len(vf) == clock.frames
            and _integer(video.get("nb_read_frames"), "missing_video_frame_count") == clock.frames,
            "physical_video_frame_count")
    for index, frame in enumerate(vf):
        require(frame.get("stream_index") == video["index"], "video_stream_identity")
        pts = _integer(frame.get("pts"), "video_frame_without_pts")
        require(pts * video_base == Fraction(index, 1) / fps, "video_timestamp_discontinuity")
        require(_integer(frame.get("best_effort_timestamp", pts), "invalid_video_best_effort_pts")
                == pts, "video_timestamp_discontinuity")
    packet_clock, access_units = _packet_clock(probe, video, audio, clock)
    require(len(af) == len(access_units), "physical_audio_frame_count")
    cursor = 0
    for index, (frame, (unit_start, unit_end)) in enumerate(zip(af, access_units, strict=True)):
        require(frame.get("stream_index") == audio["index"], "audio_stream_identity")
        pts = _integer(frame.get("pts"), "audio_frame_without_pts")
        count = _integer(frame.get("nb_samples"), "audio_frame_without_sample_count")
        require(pts == cursor and 0 < count <= 1024, "audio_timestamp_discontinuity")
        require(pts == unit_start, "audio_frame_and_packet_pts_disagree")
        permitted_ends = {unit_end}
        if index == len(af) - 1:
            permitted_ends.add(samples)
        require(pts + count in permitted_ends, "audio_frame_and_packet_samples_disagree")
        cursor += count
    require(samples <= cursor <= samples + packet_clock["declared_decoder_suffix_budget_samples"],
            "decoded_audio_frame_clock_mismatch")
    require(bool(af) and len(af) <= packet_clock["audio_packets"], "physical_audio_frame_count")
    return {"video_frames": len(vf), "audio_frames": len(af),
            "decoded_audio_samples_from_frames": cursor,
            "audio_duration_samples": duration, "first_video_pts": 0, "first_audio_pts": 0,
            "video_fps": str(fps), "audio_timebase": str(audio_base),
            "exact_contiguous_frame_timestamps": True, **packet_clock}


def inspect_master(reference: Path, master: Path, *, reference_sha256: str,
                   master_sha256: str, clock: MediaClock, ffmpeg: Path,
                   ffprobe: Path, limits: ContentLimits = ContentLimits()) -> dict:
    samples = clock.samples
    _, reference_token = _checked_file(reference, reference_sha256)
    _, master_token = _checked_file(master, master_sha256)
    original_pcm = read_reference_wav(reference, samples)
    entries = (
        "stream=index,codec_type,codec_name,profile,width,height,r_frame_rate,time_base,"
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
    raw_packets = bounded_decoder([
        str(ffprobe), "-v", "error", "-show_packets", "-show_entries",
        "packet=stream_index,pts,dts,duration:packet_side_data=side_data_type,skip_samples,discard_padding",
        "-of", "json", str(master)
    ], 4 * 1024 * 1024)
    try:
        packets = json.loads(raw_packets)
    except (ValueError, UnicodeDecodeError):
        raise ReadbackError("invalid_packet_json") from None
    require(isinstance(packets, dict), "invalid_packet_json")
    probe["packets"] = packets.get("packets")
    timing = validate_clock(probe, clock)
    # No -ar, -ac, trim, padding, normalization or alignment filter: the
    # original codec/rate/channels were checked before decoding this stream.
    decoded_pcm = bounded_decoder([
        str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-i", str(master),
        "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", "-f", "s16le", "-"
    ], (samples + timing["declared_decoder_suffix_budget_samples"]) * 4)
    require(len(decoded_pcm) // 4 == timing["decoded_audio_samples_from_frames"],
            "physical_pcm_and_frame_count_disagree")
    content = compare_pcm(original_pcm, decoded_pcm, limits,
                          declared_suffix_samples=timing["decoded_audio_samples_from_frames"] - samples)
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
