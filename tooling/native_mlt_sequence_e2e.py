#!/usr/bin/env python3
"""Exact-SHA source-bound two-segment Motion Canvas / MLT FFV1 video E2E.

CI only. Provisions a 33-scene application-owned project and pinned Semwright
Broker/Driver Host, renders 32+1 real Motion Canvas segments, and assembles
FFV1/PCM Matroska lossless intermediary through semantic MLT, not an AAC master.
"""
from __future__ import annotations

import hashlib
import datetime
import json
import math
import re
import struct
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from native_av_failure_guard import run_sanitized_json
from native_mlt_caption_pixels import prove_native_captioned_master
from native_mlt_public_artifact import (
    PublicationError, SOURCE_WAV_NAME, mode_contract, read_synthetic_source_wav,
    read_bounded, source_file_token, strict_object, verify_source_unchanged,
    verify_staged_source_receipt,
)

from mlt_failure_diagnostic import safe_observation_diagnostic

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = json.loads((ROOT / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
PIN = SOURCE_LOCK["dependencies"]["semwright"]["revision"]
SEMWRIGHT = Path(os.environ.get("SEMWRIGHT_CHECKOUT", ROOT / "_semwright")).resolve()
SW_BINS = SEMWRIGHT / "target" / "debug"
MW_BIN = ROOT / "target" / "debug" / "examples" / "native-mlt-sequence-e2e"
RUNTIME = SEMWRIGHT / "integrations" / "motion-canvas" / "runtime"
GITHUB_SHA = os.environ.get("GITHUB_SHA", "unknown")
EVIDENCE = ROOT / "verification" / "native-mlt-sequence-e2e" / GITHUB_SHA
MODE = os.environ.get("MOTIONWRIGHT_MLT_MODE", "pcm")
if MODE not in {"pcm", "video-only", "final-mp4", "captioned-mp4"}:
    raise SystemExit("Only closed native MLT smoke modes are accepted")
CONTRACT = mode_contract(MODE)
VIDEO_ONLY = MODE in {"video-only", "final-mp4", "captioned-mp4"}
FINAL_MASTER = MODE in {"final-mp4", "captioned-mp4"}
CAPTIONED_MASTER = MODE == "captioned-mp4"
PROFILE = "lossless-video-only" if VIDEO_ONLY else "lossless"
EXPECTED_SCOPE = (
    "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master"
    if VIDEO_ONLY else "actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master"
)
VIDEO_FILE = "native-two-segment-video-only.mkv" if VIDEO_ONLY else "native-two-segment-lossless.mkv"
TECHNICAL = ROOT / "verification" / "native-mlt-sequence-technical" / GITHUB_SHA


def inspect_decoded_ci_stereo(pcm: bytes, *, mode: str = "final-mp4") -> dict:
    """Independent decoded-content evidence for the actual 33-frame CI
    source. The 48 kHz stereo WAV authored by the Rust E2E contains distinct
    400 Hz left and 600 Hz right sawtooth fundamentals. AAC stream headers,
    MP4 byte hashes or a nonempty decoded WAV alone do NOT prove the actual
    speaker content was retained and not swapped, duplicated or muted.
    """
    if mode not in {"final-mp4", "captioned-mp4"}:
        raise AssertionError("Legacy stereo oracle admits only its measured-voice fixture")
    contract = mode_contract(mode)
    if len(pcm) % 4:
        raise AssertionError("Decoded H.264/AAC master has partial stereo PCM sample")
    frames = len(pcm) // 4
    if not contract.pcm_frames <= frames <= contract.pcm_frames + 1024:
        raise AssertionError("Decoded AAC samples exceed one bounded access unit")
    start = 4_800
    end = min(48_000, frames - 2_048)
    if end - start < 24_000:
        raise AssertionError("Decoded AAC lacks a stable measured content window")

    def fundamental(channel: int, frequency: int) -> float:
        cosine = sine = 0.0
        count = 0
        for frame in range(start, end, 8):
            sample = struct.unpack_from("<hh", pcm, frame * 4)[channel] / 32_768.0
            phase = 2.0 * math.pi * frequency * frame / 48_000
            cosine += sample * math.cos(phase)
            sine += sample * math.sin(phase)
            count += 1
        return 2.0 * math.hypot(cosine, sine) / count

    left_400 = fundamental(0, 400)
    left_600 = fundamental(0, 600)
    right_400 = fundamental(1, 400)
    right_600 = fundamental(1, 600)
    if (left_400 < 0.08 or right_600 < 0.06
        or left_400 < 4 * max(left_600, 1e-7)
        or right_600 < 4 * max(right_400, 1e-7)):
        raise AssertionError("Actual decoded AAC lost distinct authored stereo CI signals")
    return {
        "decoded_master_pcm_frames": frames,
        "decoded_stereo_content": "distinct_source_400hz_left_600hz_right",
        "left_400_amplitude": round(left_400, 5),
        "right_600_amplitude": round(right_600, 5),
    }


def new_private_binary(path: Path):
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
    return os.fdopen(os.open(path, flags, 0o600), "wb")


def write_new_private_json(path: Path, value: object) -> None:
    with new_private_binary(path) as output:
        output.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
        output.flush()
        os.fsync(output.fileno())



class AudioReadbackLimitExceeded(OSError):
    def __init__(self, streams: list[str], returncode: int):
        self.streams = sorted(streams)
        self.returncode = returncode
        super().__init__("Independent audio readback exceeded its capture budget")


def run_bounded_audio_readback(argv: list[str], *, env: dict, stdout, stderr,
                               timeout: float, stdout_limit: int,
                               stderr_limit: int, check: bool = False):
    """Capture exact output up to explicit budgets; any excess is a failure.

    This function starts only the independent read-only decoder/probe supplied
    by the wrapper. A capped prefix is never a successful media observation.
    Two pipe readers bound memory and disk on Windows as well as POSIX.
    """
    if (check or not math.isfinite(timeout) or timeout <= 0
        or any(type(value) is not int or value <= 0
               for value in (stdout_limit, stderr_limit))):
        raise ValueError("Independent audio readback requires positive capture budgets")
    process = subprocess.Popen(
        argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, bufsize=0,
    )
    exceeded = []
    failures = []

    def stop():
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def capture(name, stream, output, maximum):
        count = 0
        try:
            while True:
                # Read one byte beyond the remaining budget only to detect
                # truncation; that byte is never written to the evidence file.
                data = stream.read(min(16384, maximum - count + 1))
                if not data:
                    break
                kept = min(len(data), maximum - count)
                output.write(data[:kept])
                count += kept
                if kept != len(data):
                    exceeded.append(name)
                    stop()
                    break
        except (OSError, ValueError) as error:
            failures.append(type(error).__name__)
            stop()
        finally:
            stream.close()

    threads = [threading.Thread(target=capture, args=values, daemon=True)
               for values in (("stdout", process.stdout, stdout, stdout_limit),
                              ("stderr", process.stderr, stderr, stderr_limit))]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop()
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop()
        cleanup_deadline = time.monotonic() + 5
        for thread in threads:
            thread.join(max(0, cleanup_deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            timed_out = True
            stop()
    if timed_out:
        raise subprocess.TimeoutExpired(argv, timeout)
    if exceeded:
        raise AudioReadbackLimitExceeded(exceeded, process.returncode)
    if failures:
        raise OSError("Independent audio pipe capture failed")
    return subprocess.CompletedProcess(argv, process.returncode)


def verify_original_aac_clock(clock_bytes: bytes, contract) -> None:
    clock = strict_object(clock_bytes)
    streams = clock.get("streams")
    if (not isinstance(streams, list) or len(streams) != 1
        or not isinstance(streams[0], dict)
        or streams[0].get("codec_type") != "audio"
        or streams[0].get("codec_name") != "aac"
        or streams[0].get("sample_rate") != str(contract.sample_rate)
        or type(streams[0].get("channels")) is not int
        or streams[0]["channels"] != contract.channels):
        raise AssertionError("Original AAC clock does not have the admitted stereo profile")
    rows = clock.get("packets_and_frames", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise AssertionError("Native audio packet/frame clock is malformed")
    packets = clock.get("packets", [row for row in rows if row.get("type") == "packet"])
    frames = clock.get("frames", [row for row in rows if row.get("type") == "frame"])
    if (not isinstance(packets, list) or not packets
        or not isinstance(frames, list) or not frames
        or any(not isinstance(row, dict) for row in (*packets, *frames))):
        raise AssertionError("Native audio packet/frame clock was not retained")


def inspect_native_master_decoded_content(master: Path, ffmpeg: Path, env: dict,
                                          *, source_wav: Path, ffprobe: Path,
                                          evidence_root: Path,
                                          mode: str = "final-mp4") -> dict:
    """Retain source-bound full PCM and clock before evaluating either channel.

    This independent readback never runs a producer. Raw observations remain
    private; the public artifact allowlist does not include these files.
    Packet/priming data is preserved, not promoted to a timing acceptance.
    """
    if mode not in {"final-mp4", "captioned-mp4"}:
        raise AssertionError("Legacy stereo decoder admits only its measured-voice fixture")
    contract = mode_contract(mode)
    if not evidence_root.is_dir() or evidence_root.is_symlink():
        raise AssertionError("Native audio evidence root must be an existing real directory")
    outputs = {
        "clock": evidence_root / "native-master-audio-clock.json",
        "clock_stderr": evidence_root / "native-master-audio-clock.stderr.log",
        "pcm": evidence_root / "native-master-decoded.s16le",
        "decode_stderr": evidence_root / "native-master-decode.stderr.log",
    }
    observation_path = evidence_root / "native-master-audio-observation.json"
    failure_path = evidence_root / "native-master-audio-failure.json"
    if any(path.exists() or path.is_symlink() for path in
           (*outputs.values(), observation_path, failure_path)):
        raise AssertionError("Existing native audio observations must not be overwritten")
    source_bytes, source_proof, source_token = read_synthetic_source_wav(source_wav, mode)
    before = master.lstat()
    if (not stat.S_ISREG(before.st_mode)
        or getattr(before, "st_file_attributes", 0) & 0x400
        or not 0 < before.st_size <= 256_000_000):
        raise AssertionError("Native AAC master is not a bounded regular file")
    master_token = source_file_token(before)
    master_sha256 = digest(master)
    tool_identity = {"ffmpeg_sha256": digest(ffmpeg), "ffprobe_sha256": digest(ffprobe)}
    commands = (
        ("clock", "clock_stderr", 30,
         [str(ffprobe), "-v", "error", "-select_streams", "a",
          "-show_streams", "-show_frames", "-show_packets", "-of", "json", str(master)]),
        ("pcm", "decode_stderr", 45,
         [str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
          "-i", str(master), "-map", "0:a:0", "-acodec", "pcm_s16le",
          "-f", "s16le", "-"]),
    )
    limits = {"clock": 2_000_000, "pcm": (contract.pcm_frames + 1024) * 4,
              "clock_stderr": 65_536, "decode_stderr": 65_536}
    exit_codes = {}
    interrupted = None
    clock_error = None
    clock_bytes = None
    for name, stderr_name, timeout, command in commands:
        try:
            with new_private_binary(outputs[name]) as output, \
                    new_private_binary(outputs[stderr_name]) as errors:
                try:
                    result = run_bounded_audio_readback(
                        command, env=env, stdout=output, stderr=errors,
                        timeout=timeout, check=False,
                        stdout_limit=limits[name], stderr_limit=limits[stderr_name],
                    )
                finally:
                    output.flush()
                    errors.flush()
                    os.fsync(output.fileno())
                    os.fsync(errors.fileno())
            exit_codes[name] = result.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            interrupted = {"stage": name, "error_type": type(error).__name__}
            if isinstance(error, AudioReadbackLimitExceeded):
                interrupted["truncated_streams"] = error.streams
                exit_codes[name] = error.returncode
            break
        if result.returncode != 0:
            break
        if name == "clock":
            try:
                clock_bytes = read_bounded(evidence_root, outputs[name].name, limits[name])
                verify_original_aac_clock(clock_bytes, contract)
            except (AssertionError, PublicationError, OSError) as error:
                clock_error = (str(error) if isinstance(error, AssertionError)
                               else "native_audio_custody_refused")
                break  # Refuse the original profile before starting a decoder.
    observed = {
        name: {"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path)}
        for name, path in outputs.items() if path.is_file()
    }
    observation = {
        "schema": "motionwright.native-mlt-ci.audio-observation/1",
        "motionwright_sha": GITHUB_SHA, "semwright_sha": PIN, "mode": mode,
        "github": {name: os.environ.get(name, "") for name in (
            "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_WORKFLOW_REF",
        )},
        "reader_sha256": digest(Path(__file__)),
        "envelope_sha256": digest(Path(__file__).with_name("native_mlt_ci_pcm_envelope.py")),
        "source_wav": source_proof, "master_sha256": master_sha256,
        "master_bytes": before.st_size, "tools": tool_identity,
        "outputs": observed, "exit_codes": exit_codes, "interrupted": interrupted,
        "capture_limits_bytes": limits, "clock_error": clock_error,
        "capture_complete": (interrupted is None and clock_error is None
                             and exit_codes == {"clock": 0, "pcm": 0}),
        "inspection": "CAPTURED_NOT_YET_VALIDATED",
        "native_execution_performed": False,
        "media_packet_priming_and_skip_verified": False,
        "native_provider_receipt_verified": False,
    }
    # Preserve the original observations before a bad clock, short decode or
    # missing final channel energy can fail the gate. Never retry a producer.
    write_new_private_json(observation_path, observation)
    try:
        if clock_error is not None:
            raise AssertionError(clock_error)
        if interrupted is not None:
            raise AssertionError("Independent native audio readback was interrupted; no retry")
        if exit_codes != {"clock": 0, "pcm": 0}:
            raise AssertionError("Independent native audio probe or decoder failed")
        size = observed["pcm"]["bytes"]
        maximum_pcm = (contract.pcm_frames + 1024) * 4
        if not contract.pcm_frames * 4 <= size <= maximum_pcm:
            raise AssertionError("Decoded source speaker PCM exceeded the AV clock budget")
        pcm = read_bounded(evidence_root, outputs["pcm"].name, maximum_pcm)
        if (len(pcm) != size or hashlib.sha256(pcm).hexdigest() != observed["pcm"]["sha256"]
            or hashlib.sha256(clock_bytes).hexdigest() != observed["clock"]["sha256"]):
            raise AssertionError("Decoded AV speaker bytes changed during bounded read")
        verify_source_unchanged(source_wav, source_token)
        checked_source, checked_proof, _ = read_synthetic_source_wav(source_wav, mode)
        if checked_source != source_bytes or checked_proof != source_proof:
            raise AssertionError("Original source WAV changed during native audio readback")
        if source_file_token(master.lstat()) != master_token or digest(master) != master_sha256:
            raise AssertionError("Original AAC master changed during independent decode")
        # The separate pure envelope imports the existing midstream oracle.
        # Defer this import until both modules are initialized; retain both gates.
        from native_mlt_ci_pcm_envelope import inspect_fixed_ci_stereo_envelope
        envelope = inspect_fixed_ci_stereo_envelope(source_bytes, pcm)
    except (AssertionError, PublicationError, OSError) as error:
        reason = str(error) if isinstance(error, AssertionError) else "native_audio_custody_refused"
        write_new_private_json(failure_path, {
            "schema": "motionwright.native-mlt-ci.audio-failure/1",
            "observation_sha256": digest(observation_path), "reason": reason,
            "status": "FAIL", "native_job_retried": False,
        })
        raise AssertionError(reason) from None
    return {
        **envelope["existing_midstream_stereo_observation"],
        "full_source_pcm_envelope": envelope,
        "audio_observation_sha256": digest(observation_path),
        "decoded_master_full_pcm_sha256": observed["pcm"]["sha256"],
        "audio_clock_sha256": observed["clock"]["sha256"],
        "media_packet_priming_and_skip_verified": False,
    }


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def retain_native_master_source(output_root: Path, evidence_root: Path,
                                receipt: dict, mode: str) -> dict:
    """Retain the admitted staged bytes; never regenerate or replace a source."""
    try:
        if (not output_root.is_dir() or output_root.is_symlink()
            or not evidence_root.is_dir() or evidence_root.is_symlink()):
            raise PublicationError("native_mlt_source_wav_root_invalid")
        native_root = output_root.resolve()
        retained_root = evidence_root.resolve()
        if (native_root == retained_root
            or native_root in retained_root.parents
            or retained_root in native_root.parents):
            raise PublicationError("native_mlt_source_wav_roots_overlap")
        # This admits the closed CI basename and source identity before any
        # source file is opened. The private native receipt is never copied.
        relative = verify_staged_source_receipt(receipt, mode)
        source = native_root / relative
        data, proof, token = read_synthetic_source_wav(source, mode)
        destination = retained_root / SOURCE_WAV_NAME
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_BINARY", 0))
        with os.fdopen(os.open(destination, flags, 0o600), "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        copied, copied_proof, _ = read_synthetic_source_wav(destination, mode)
        if copied != data or copied_proof != proof:
            raise PublicationError("native_mlt_source_wav_copy_mismatch")
        verify_source_unchanged(source, token)
        return proof
    except (PublicationError, OSError):
        raise AssertionError("native_master_source_retention_refused") from None


def run_json(argv: list[str], *, env: dict[str, str], timeout: int = 30) -> dict:
    return run_sanitized_json(argv, env=env, timeout=timeout)


def technical_attempt_guard() -> None:
    if (os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
        or not re.fullmatch(r"[0-9a-f]{40}", GITHUB_SHA)):
        raise AssertionError("Native MLT requires an original Actions attempt; reconcile prior outcomes, never replay")


def technical_context() -> dict:
    names = ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
             "GITHUB_JOB", "GITHUB_WORKFLOW_REF", "GITHUB_WORKFLOW_SHA")
    return {"motionwright_sha": GITHUB_SHA, "semwright_sha": PIN, "mode": MODE,
            "github": {name: os.environ.get(name, "") for name in names},
            "wrapper_sha256": digest(Path(__file__))}


def record_assembly_failure(evidence: Path, context: dict, error: BaseException) -> bool:
    """A custody write must not replace the exception from the native request."""
    try:
        write_new_private_json(evidence / "native-assembly-outcome.json", {
            "schema": "motionwright.native-mlt-ci.assembly-outcome/1", "context": context,
            "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "state": "FAILED_OR_UNKNOWN_REQUIRES_ORIGINAL_OWNER_RECONCILIATION",
            "exception_type": type(error).__name__, "native_request_retried": False,
        })
        return True
    except OSError:
        return False


def technical_allowlist(mode: str) -> dict[str, int]:
    contract = mode_contract(mode)
    files = {
        "native-assembly-intent.json": 16_384,
        "native-assembly-outcome.json": 16_384,
        "safe-observation-failure.json": 32_768,
        ("native-two-segment-lossless.mkv" if mode == "pcm"
         else "native-two-segment-video-only.mkv"): 256_000_000,
    }
    if mode in {"final-mp4", "captioned-mp4"}:
        files.update({
            SOURCE_WAV_NAME: contract.wav_bytes,
            "native-multisegment-master.mp4": 256_000_000,
            "native-master-audio-clock.json": 2_000_000,
            "native-master-audio-clock.stderr.log": 65_536,
            "native-master-decoded.s16le": (contract.pcm_frames + 1024) * 4,
            "native-master-decode.stderr.log": 65_536,
            "native-master-audio-observation.json": 65_536,
            "native-master-audio-failure.json": 16_384,
            "decoded-stereo-content-proof.json": 256_000,
        })
    return files


def technical_plain(path: Path, *, new: bool = False) -> Path:
    path = Path(os.path.abspath(path))
    for parent in (*path.parents, *(() if new else (path,))):
        metadata = parent.lstat()
        if (stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & 0x400):
            raise PublicationError("technical_custody_link_refused")
    if new and (path.exists() or path.is_symlink()):
        raise PublicationError("technical_custody_existing_output")
    return path


def technical_record(path: Path, limit: int) -> dict:
    path = technical_plain(path)
    before = path.lstat()
    token = source_file_token(before)
    if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
        raise PublicationError("technical_custody_size_or_type_refused")
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    count, measured = 0, hashlib.sha256()
    with os.fdopen(os.open(path, flags), "rb") as source:
        if source_file_token(os.fstat(source.fileno())) != token:
            raise PublicationError("technical_custody_source_changed")
        while block := source.read(min(65_536, before.st_size - count + 1)):
            count += len(block)
            if count > before.st_size:
                raise PublicationError("technical_custody_source_changed")
            measured.update(block)
        if source_file_token(os.fstat(source.fileno())) != token:
            raise PublicationError("technical_custody_source_changed")
    if count != before.st_size or source_file_token(path.lstat()) != token:
        raise PublicationError("technical_custody_source_changed")
    return {"name": path.name, "bytes": count, "sha256": measured.hexdigest(), "token": list(token)}


def technical_small(path: Path, limit: int) -> bytes:
    before = technical_record(path, limit)
    with path.open("rb") as source:
        data = source.read(min(limit + 1, before["bytes"] + 1))
    if (len(data) != before["bytes"] or hashlib.sha256(data).hexdigest() != before["sha256"]
        or technical_record(path, limit) != before):
        raise PublicationError("technical_custody_source_changed")
    return data


def technical_copy(source: Path, destination: Path, limit: int, expected: dict) -> dict:
    before = technical_record(source, limit)
    if before != expected:
        raise PublicationError("technical_custody_source_changed")
    technical_plain(destination, new=True)
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    count, measured = 0, hashlib.sha256()
    with os.fdopen(os.open(source, flags), "rb") as original, new_private_binary(destination) as retained:
        if list(source_file_token(os.fstat(original.fileno()))) != before["token"]:
            raise PublicationError("technical_custody_source_changed")
        while block := original.read(min(65_536, before["bytes"] - count + 1)):
            count += len(block)
            if count > before["bytes"]:
                raise PublicationError("technical_custody_source_changed")
            measured.update(block)
            retained.write(block)
        retained.flush()
        os.fsync(retained.fileno())
        if list(source_file_token(os.fstat(original.fileno()))) != before["token"]:
            raise PublicationError("technical_custody_source_changed")
    if (count != before["bytes"] or measured.hexdigest() != before["sha256"]
        or technical_record(source, limit) != before):
        raise PublicationError("technical_custody_source_changed")
    copied = technical_record(destination, limit)
    if any(copied[key] != before[key] for key in ("bytes", "sha256")):
        raise PublicationError("technical_custody_copy_changed")
    return copied


def technical_private_patterns(owner_root: Path) -> tuple[list[bytes], bool]:
    # Values never leave memory. The owner tree is not enumerated or copied.
    patterns = {str(owner_root).encode(), b"motionwright.session", b"owner.toml",
                b"session_file", b"session-file", b"session_ticket", b"session-ticket",
                b"owner-connection", b"bearer ", b"-----begin private key-----"}
    session = owner_root / "runtime" / "motionwright.session"
    if not session.exists() and not session.is_symlink():
        return sorted(patterns), False
    raw = technical_small(session, 65_536)
    if not raw.strip():
        raise PublicationError("technical_custody_ticket_unreadable")
    patterns.update((raw, raw.strip()))
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        value = None
        patterns.update(line.strip() for line in raw.splitlines() if len(line.strip()) >= 16)
    def visit(node):
        if type(node) is dict:
            for child in node.values():
                visit(child)
        elif type(node) is list:
            for child in node:
                visit(child)
        elif type(node) is str and len(node.encode()) >= 8:
            patterns.add(node.encode())
            patterns.add(json.dumps(node, ensure_ascii=True)[1:-1].encode())
    visit(value)
    return sorted(pattern.lower() for pattern in patterns if pattern), True


def technical_scan(path: Path, limit: int, patterns: list[bytes], expected: dict) -> None:
    if technical_record(path, limit) != expected:
        raise PublicationError("technical_custody_scan_source_changed")
    overlap = max((len(item) for item in patterns), default=1) - 1
    previous, count, measured = b"", 0, hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(min(65_536, expected["bytes"] - count + 1)):
            count += len(block)
            if count > expected["bytes"]:
                raise PublicationError("technical_custody_scan_source_changed")
            measured.update(block)
            combined = (previous + block).lower()
            if any(pattern in combined for pattern in patterns):
                raise PublicationError("technical_custody_private_material_excluded_no_redaction")
            previous = combined[-overlap:] if overlap else b""
    if (count != expected["bytes"] or measured.hexdigest() != expected["sha256"]
        or technical_record(path, limit) != expected):
        raise PublicationError("technical_custody_scan_source_changed")


def stage_technical_custody(evidence: Path, output: Path, owner_root: Path,
                            mode: str, context: dict, wrapper_failed: bool,
                            assembly_journal_complete: bool = True) -> dict:
    """Copy closed synthetic observations; retain original failures without granting PASS."""
    evidence = technical_plain(evidence)
    technical_plain(output.parent)
    output = technical_plain(output, new=True)
    quarantine = technical_plain(output.with_name(output.name + "-quarantine"), new=True)
    if (evidence == output or evidence in output.parents or output in evidence.parents
        or owner_root == output or owner_root in output.parents or output in owner_root.parents):
        raise PublicationError("technical_custody_roots_overlap")
    output.mkdir(mode=0o700)
    quarantine.mkdir(mode=0o700)
    write_new_private_json(output / "custody-start.json", {
        "schema": "motionwright.native-mlt-ci.technical-custody-start/1", "context": context,
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "state": "CUSTODY_PENDING", "product_verdict": "NOT_GRANTED_BY_CUSTODY",
    })
    allowed = technical_allowlist(mode)
    entries, absent, exclusions = [], [], []
    total = 0
    try:
        patterns, ticket_observed = technical_private_patterns(owner_root)
    except (OSError, PublicationError, RecursionError):
        patterns, ticket_observed = [], False
    for name, limit in sorted(allowed.items()):
        source, candidate, destination = evidence / name, quarantine / name, output / name
        if not source.exists() and not source.is_symlink():
            absent.append(name)
            continue
        if name == "native-assembly-outcome.json" and not assembly_journal_complete:
            exclusions.append({"name": name, "reason": "assembly_outcome_not_written_by_this_attempt"})
            continue
        try:
            original = technical_record(source, limit)
            total += original["bytes"]
            if total > 520_000_000:
                raise PublicationError("technical_custody_total_budget_exceeded")
            if not ticket_observed:
                raise PublicationError("technical_custody_original_ticket_scan_unavailable")
            if name == SOURCE_WAV_NAME:
                read_synthetic_source_wav(source, mode)
            scanned = technical_copy(source, candidate, limit, original)
            technical_scan(candidate, limit, patterns, scanned)
            published = technical_copy(candidate, destination, limit, scanned)
            if technical_record(source, limit) != original:
                raise PublicationError("technical_custody_source_changed")
            entries.append({"name": name, "bytes": published["bytes"], "sha256": published["sha256"],
                            "original_token": original["token"], "scanned_copy_sha256": scanned["sha256"]})
        except (OSError, PublicationError) as error:
            if destination.exists() or destination.is_symlink():
                destination.unlink()
            exclusions.append({"name": name, "reason": error.code if isinstance(error, PublicationError)
                               else "technical_custody_io_failure"})
    complete = bool(entries) and not exclusions and assembly_journal_complete
    receipt = {
        "schema": "motionwright.native-mlt-ci.technical-custody/1", "context": context,
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": mode, "wrapper_exception_active": wrapper_failed,
        "state": "ORIGINAL_BYTES_RETAINED" if complete else "INCOMPLETE_CUSTODY",
        "included_files": entries, "absent_allowlisted_files": absent, "excluded_files": exclusions,
        "complete_for_existing_allowlisted_files": complete,
        "assembly_outcome_journal_complete": assembly_journal_complete,
        "every_allowlisted_file_present": not absent and not exclusions,
        "original_ticket_checked_in_memory": ticket_observed,
        "owner_tree_copied": False, "originals_redacted": False,
        "native_mutations_replayed": False, "native_verdict": "NOT_GRANTED_BY_CUSTODY",
        "packet_timing_verified": False, "audio_fidelity_verified_by_custody": False,
        "human_acceptance": False, "formal_acceptance": False, "release_authorized": False,
    }
    write_new_private_json(output / "technical-custody.json", receipt)
    return receipt


def verify_technical_custody(staged: Path, output: Path, mode: str,
                             context: dict, native_step: str) -> bool:
    """Publish only checked copies from the closed stage; never upload that stage."""
    if native_step not in {"success", "failure", "cancelled", "skipped", ""}:
        raise PublicationError("technical_custody_invalid_step_outcome")
    output.parent.mkdir(parents=True, exist_ok=True)
    technical_plain(output.parent)
    output = technical_plain(output, new=True)
    if staged == output or staged in output.parents or output in staged.parents:
        raise PublicationError("technical_custody_roots_overlap")
    output.mkdir(mode=0o700)
    if not staged.exists() and not staged.is_symlink():
        write_new_private_json(output / "custody-status.json", {
            "schema": "motionwright.native-mlt-ci.technical-custody-status/1", "context": context,
            "native_step_outcome": native_step, "state": "NO_STAGED_ORIGINALS",
            "native_execution_inferred": False, "product_verdict": "NOT_GRANTED_BY_CUSTODY",
            "formal_acceptance": False, "release_authorized": False,
        })
        return native_step in {"skipped", ""}
    allowed = technical_allowlist(mode)
    copied, refused = [], []
    manifest_sha = None
    complete = False
    try:
        technical_plain(staged)
        manifest_path = staged / "technical-custody.json"
        manifest_record = technical_record(manifest_path, 65_536)
        report = strict_object(technical_small(manifest_path, 65_536))
        keys = {
            "schema", "context", "utc", "mode", "wrapper_exception_active", "state",
            "included_files", "absent_allowlisted_files", "excluded_files",
            "complete_for_existing_allowlisted_files", "every_allowlisted_file_present",
            "assembly_outcome_journal_complete",
            "original_ticket_checked_in_memory", "owner_tree_copied", "originals_redacted",
            "native_mutations_replayed", "native_verdict", "packet_timing_verified",
            "audio_fidelity_verified_by_custody", "human_acceptance", "formal_acceptance", "release_authorized",
        }
        false_fields = ("owner_tree_copied", "originals_redacted", "native_mutations_replayed",
                        "packet_timing_verified", "audio_fidelity_verified_by_custody",
                        "human_acceptance", "formal_acceptance", "release_authorized")
        if (set(report) != keys or report["schema"] != "motionwright.native-mlt-ci.technical-custody/1"
            or report["context"] != context or report["mode"] != mode
            or report["native_verdict"] != "NOT_GRANTED_BY_CUSTODY"
            or any(report[name] is not False for name in false_fields)
            or type(report["wrapper_exception_active"]) is not bool
            or not re.fullmatch(r"[0-9T:+.\-Z]{20,40}", report["utc"])
            or report["state"] not in {"ORIGINAL_BYTES_RETAINED", "INCOMPLETE_CUSTODY"}
            or any(type(report[name]) is not bool for name in (
                "complete_for_existing_allowlisted_files", "every_allowlisted_file_present",
                "original_ticket_checked_in_memory", "assembly_outcome_journal_complete"))
            or any(type(report[name]) is not list for name in (
                "included_files", "absent_allowlisted_files", "excluded_files"))):
            raise PublicationError("technical_custody_manifest_binding_refused")
        seen = set()
        for entry in report["included_files"]:
            if (type(entry) is not dict or set(entry) != {
                "name", "bytes", "sha256", "original_token", "scanned_copy_sha256"}
                or entry["name"] not in allowed or entry["name"] in seen
                or type(entry["bytes"]) is not int or not 0 <= entry["bytes"] <= allowed[entry["name"]]
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or entry["scanned_copy_sha256"] != entry["sha256"]
                or type(entry["original_token"]) is not list or len(entry["original_token"]) != 7
                or any(type(number) is not int for number in entry["original_token"])):
                raise PublicationError("technical_custody_manifest_allowlist_mismatch")
            seen.add(entry["name"])
        absent = report["absent_allowlisted_files"]
        if len(set(absent)) != len(absent) or any(name not in allowed or name in seen for name in absent):
            raise PublicationError("technical_custody_manifest_allowlist_mismatch")
        seen.update(absent)
        for entry in report["excluded_files"]:
            if (type(entry) is not dict or set(entry) != {"name", "reason"}
                or entry["name"] not in allowed or entry["name"] in seen
                or not re.fullmatch(r"[a-z_]{1,100}", entry["reason"])):
                raise PublicationError("technical_custody_manifest_allowlist_mismatch")
            seen.add(entry["name"])
        expected_complete = (bool(report["included_files"]) and not report["excluded_files"]
                             and report["assembly_outcome_journal_complete"])
        if (seen != set(allowed) or report["complete_for_existing_allowlisted_files"] is not expected_complete
            or report["every_allowlisted_file_present"] is not (not absent and not report["excluded_files"])
            or report["state"] != ("ORIGINAL_BYTES_RETAINED" if expected_complete else "INCOMPLETE_CUSTODY")
            or (report["included_files"] and not report["original_ticket_checked_in_memory"])):
            raise PublicationError("technical_custody_manifest_completeness_mismatch")
        for entry in report["included_files"]:
            name, limit = entry["name"], allowed[entry["name"]]
            source, destination = staged / name, output / name
            try:
                original = technical_record(source, limit)
                if any(original[key] != entry[key] for key in ("bytes", "sha256")):
                    raise PublicationError("technical_custody_published_copy_changed")
                retained = technical_copy(source, destination, limit, original)
                copied.append({"name": name, "bytes": retained["bytes"], "sha256": retained["sha256"]})
            except (OSError, PublicationError):
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
                refused.append({"name": name, "reason": "technical_custody_published_copy_changed"})
        # This raw manifest is copied only after its closed fields have been
        # validated and the exact original bytes remain unchanged.
        if technical_record(manifest_path, 65_536) != manifest_record:
            raise PublicationError("technical_custody_manifest_changed")
        technical_copy(manifest_path, output / "technical-custody.json", 65_536, manifest_record)
        manifest_sha = manifest_record["sha256"]
        complete = expected_complete and not refused
    except (OSError, PublicationError, KeyError, TypeError, ValueError) as error:
        path = output / "technical-custody.json"
        if path.exists() or path.is_symlink():
            path.unlink()
        refused.append({"name": "technical-custody.json", "reason": error.code if isinstance(error, PublicationError)
                        else "technical_custody_manifest_refused"})
    write_new_private_json(output / "custody-status.json", {
        "schema": "motionwright.native-mlt-ci.technical-custody-status/1", "context": context,
        "native_step_outcome": native_step, "state": "VERIFIED_ORIGINAL_BYTE_CUSTODY" if complete else "INCOMPLETE_CUSTODY",
        "source_manifest_sha256": manifest_sha, "included_files": copied, "refused_files": refused,
        "complete_for_existing_allowlisted_files": complete, "product_verdict": "NOT_GRANTED_BY_CUSTODY",
        "formal_acceptance": False, "release_authorized": False,
    })
    return complete


def exact_inputs() -> None:
    technical_attempt_guard()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("native render E2E is GitHub-Actions-only")
    actual = subprocess.check_output(
        ["git", "--no-replace-objects", "-C", str(ROOT), "rev-parse", "HEAD"], text=True,
    ).strip()
    if actual != GITHUB_SHA:
        raise SystemExit("Motionwright checkout differs from the original attempt source")
    observed = subprocess.check_output(
        ["git", "--no-replace-objects", "-C", str(SEMWRIGHT), "rev-parse", "HEAD"], text=True
    ).strip()
    if observed != PIN:
        raise SystemExit(f"Semwright checkout mismatch: expected {PIN}, got {observed}")
    for path in [
        SW_BINS / "semwright",
        SW_BINS / "semwrightd",
        SW_BINS / "semwright-sandbox",
        SW_BINS / "semwright-motion-canvas-driver",
        SW_BINS / "semwright-mlt-video-driver",
        SW_BINS / "semwright-mlt-runtime-runner",
        MW_BIN,
        RUNTIME / "package-lock.json",
    ]:
        if not path.exists():
            raise SystemExit(f"missing exact acceptance input: {path}")
    if not shutil.which("bwrap"):
        raise SystemExit("bubblewrap is required; no unsandboxed fallback is accepted")


def semwright_version() -> str:
    with (SEMWRIGHT / "Cargo.toml").open("rb") as stream:
        return tomllib.load(stream)["workspace"]["package"]["version"]


def wait_for_socket(process: subprocess.Popen[bytes], socket: Path, log: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(log.read_text(encoding="utf-8", errors="replace"))
        if socket.is_socket():
            return
        time.sleep(0.05)
    raise AssertionError("semwrightd did not expose the owner socket")


def main() -> None:
    exact_inputs()
    EVIDENCE.mkdir(parents=True, exist_ok=False)
    TECHNICAL.parent.mkdir(parents=True, exist_ok=True)
    custody_context = technical_context()
    assembly_journal_complete = False

    with tempfile.TemporaryDirectory(prefix="motionwright-native-av-") as raw:
        root = Path(raw).resolve()
        root.chmod(0o700)
        paths: dict[str, Path] = {}
        for name in [
            "runtime",
            "state",
            "home",
            "config",
            "bin",
            "project",
            "media",
            "output",
            "scratch",
            "motionwright-data",
        ]:
            path = root / name
            path.mkdir(mode=0o700)
            paths[name] = path

        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}
        }
        env.update(
            HOME=str(paths["home"]),
            XDG_RUNTIME_DIR=str(paths["runtime"]),
            XDG_STATE_HOME=str(paths["state"]),
            RUST_BACKTRACE="1",
            SEMWRIGHT_TEST_DRIVER_SANDBOX="1",
            SEMWRIGHT_TEST_SANDBOX_HELPER=str(SW_BINS / "semwright-sandbox"),
        )

        database = paths["motionwright-data"] / "motionwright.sqlite3"
        seeded = run_json([str(MW_BIN), "seed", str(database)], env=env)
        if ((seeded["width"], seeded["height"]) != (CONTRACT.width, CONTRACT.height)
            or (seeded["fps_num"], seeded["fps_den"]) != (CONTRACT.fps_num, CONTRACT.fps_den)
            or seeded["scene_count"] != CONTRACT.scene_count
            or seeded["deliverable_count"] != CONTRACT.deliverable_count):
            raise AssertionError(f"unexpected real default profiles or source scenes: {seeded}")

        driver = paths["bin"] / "semwright-motion-canvas-driver"
        node_tool = paths["bin"] / "semwright-motion-node"
        mlt_driver = paths["bin"] / "semwright-mlt-video-driver"
        mlt_runner = paths["bin"] / "semwright-mlt-runtime-runner"
        shutil.copyfile(SW_BINS / "semwright-motion-canvas-driver", driver)
        shutil.copyfile(Path(shutil.which("node") or ""), node_tool)
        shutil.copyfile(SW_BINS / "semwright-mlt-video-driver", mlt_driver)
        shutil.copyfile(SW_BINS / "semwright-mlt-runtime-runner", mlt_runner)
        for executable in [driver, node_tool, mlt_driver, mlt_runner]:
            executable.chmod(0o700)

        melt = Path(shutil.which("melt") or "").resolve()
        ffprobe = Path(shutil.which("ffprobe") or "").resolve()
        ffmpeg = Path(shutil.which("ffmpeg") or "").resolve()
        for executable in [melt, ffprobe, ffmpeg]:
            if not executable.is_file():
                raise SystemExit(f"missing pinned MLT runtime tool: {executable}")
        mlt_runtime = melt.parent.parent.resolve()

        manifest = {
            "manifest_version": 1,
            "protocol": 7,
            "id": "motion-canvas",
            "version": semwright_version(),
            "publisher": "semwright",
            "executable": str(driver),
            "sha256": digest(driver),
            "application": {
                "desktop_id": None,
                "process_names": ["node"],
                "supported_versions": ["Motion Canvas 3.17.2", "Node 22.22.0"],
            },
            "transport": "stdio_v1",
            "mounts": [
                {"root": "project", "read_only": False},
                {"root": "media", "read_only": True},
                {"root": "output", "read_only": False},
                {"root": "runtime", "read_only": True, "execute": True},
                {"root": "fontconfig", "read_only": True},
            ],
            "system_config": [],
            "secrets": [],
            "tools": [
                {
                    "root": "motion-node-tool",
                    "name": "motion-node",
                    "sha256": digest(node_tool),
                    "mounts": ["project", "output", "runtime", "fontconfig"],
                }
            ],
            "network": False,
            "loopback_port": None,
            "resources": {
                "open_files": 512,
                "processes": 256,
                "cpu_seconds": 300,
                "operation_cpu_seconds": 0,
                "address_space_bytes": 4_294_967_296,
                "file_size_bytes": 1_073_741_824,
            },
            "request_timeout_ms": 300_000,
            "interfaces": {
                "dynamic_capabilities": False,
                "cooperative_cancellation": True,
                "events": False,
                "health": True,
                "progress": True,
                "artifacts": True,
                "native_refs": False,
                "host_tools": True,
            },
        }
        manifest_path = paths["config"] / "motion-canvas-driver.json"
        write_private_json(manifest_path, manifest)

        mlt_manifest = {
            "manifest_version": 1,
            "protocol": 7,
            "id": "mlt-video",
            "version": semwright_version(),
            "publisher": "semwright",
            "executable": str(mlt_driver),
            "sha256": digest(mlt_driver),
            "application": {
                "desktop_id": None,
                "process_names": ["melt"],
                "supported_versions": [],
            },
            "transport": "stdio_v1",
            "mounts": [
                {"root": "project", "read_only": True},
                {"root": "media", "read_only": True},
                {"root": "output", "read_only": False},
                {"root": "mlt-runtime", "read_only": True, "execute": True},
                {"root": "scratch", "read_only": False},
            ],
            "system_config": [],
            "secrets": [],
            "tools": [
                {
                    "root": "mlt-runner-root",
                    "name": "mlt-runner",
                    "sha256": digest(mlt_runner),
                    "mounts": ["mlt-runtime", "scratch", "project", "media", "output"],
                    "dependencies": ["melt", "ffprobe", "ffmpeg"],
                },
                {
                    "root": "melt-root",
                    "name": "melt",
                    "sha256": digest(melt),
                    "mounts": [],
                    "dependencies": [],
                },
                {
                    "root": "ffprobe-root",
                    "name": "ffprobe",
                    "sha256": digest(ffprobe),
                    "mounts": [],
                    "dependencies": [],
                },
                {
                    "root": "ffmpeg-root",
                    "name": "ffmpeg",
                    "sha256": digest(ffmpeg),
                    "mounts": [],
                    "dependencies": [],
                },
            ],
            "network": False,
            "loopback_port": None,
            "resources": {
                "open_files": 512,
                "processes": 256,
                "cpu_seconds": 300,
                "operation_cpu_seconds": 0,
                "address_space_bytes": 4_294_967_296,
                "file_size_bytes": 1_073_741_824,
            },
            "request_timeout_ms": 300_000,
            "interfaces": {
                "dynamic_capabilities": False,
                "cooperative_cancellation": False,
                "events": False,
                "health": True,
                "progress": False,
                "artifacts": False,
                "native_refs": False,
                "host_tools": True,
            },
        }
        mlt_manifest_path = paths["config"] / "mlt-video-driver.json"
        write_private_json(mlt_manifest_path, mlt_manifest)

        grants = [
            ("project", paths["project"], True),
            ("media", paths["media"], False),
            ("output", paths["output"], True),
            ("runtime", RUNTIME, False),
            ("fontconfig", Path("/etc/fonts"), False),
            ("motion-node-tool", node_tool, False),
            ("mlt-runtime", mlt_runtime, False),
            ("scratch", paths["scratch"], True),
            ("mlt-runner-root", mlt_runner, False),
            ("melt-root", melt, False),
            ("ffprobe-root", ffprobe, False),
            ("ffmpeg-root", ffmpeg, False),
        ]
        config_lines = [
            "drivers = [" + ", ".join(
                json.dumps(str(path)) for path in [manifest_path, mlt_manifest_path]
            ) + "]",
            "driver_network = false",
            "",
            "[policy]",
            'profile = "observe"',
            'allow = ["driver:motion-canvas", "driver:mlt-video"]',
        ]
        for name, path, write in grants:
            config_lines.extend(
                [
                    "",
                    "[[policy.filesystem]]",
                    f"name = {json.dumps(name)}",
                    f"path = {json.dumps(str(path.resolve()))}",
                    "read = true",
                    f"write = {'true' if write else 'false'}",
                ]
            )
        config_path = paths["config"] / "owner.toml"
        config_path.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
        config_path.chmod(0o600)

        socket = paths["runtime"] / "broker.sock"
        session = paths["runtime"] / "motionwright.session"
        daemon_log = EVIDENCE / "semwrightd.log"
        log_stream = daemon_log.open("wb")
        daemon = subprocess.Popen(
            [
                str(SW_BINS / "semwrightd"),
                "--config",
                str(config_path),
                "--socket",
                str(socket),
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=log_stream,
            start_new_session=True,
        )

        try:
            wait_for_socket(daemon, socket, daemon_log)
            discovered = run_json(
                [
                    str(SW_BINS / "semwright"),
                    "--socket",
                    str(socket),
                    "--session-file",
                    str(session),
                    "--json",
                    "capabilities",
                    "search",
                    "motion-canvas",
                    "--provider",
                    "driver:motion-canvas",
                ],
                env=env,
            )
            capabilities = discovered.get("data", {}).get("capabilities", [])
            if not capabilities:
                raise AssertionError("pinned Motion Canvas provider exposed no capabilities")
            required_mlt = (
                "driver.mlt-video.frames.encode",
                "driver.mlt-video.project.create",
                "driver.mlt-video.sequence.create",
                "driver.mlt-video.sequence.list",
                "driver.mlt-video.track.create",
                "driver.mlt-video.track.list",
                "driver.mlt-video.asset.import",
                "driver.mlt-video.clip.insert",
                "driver.mlt-video.sequence.duration",
                "driver.mlt-video.render.plan",
                "driver.mlt-video.render.start",
                "driver.mlt-video.render.status",
                "driver.mlt-video.render.result",
                "driver.mlt-video.project.close",
            )
            for capability_name in required_mlt:
                described = run_json(
                    [
                        str(SW_BINS / "semwright"),
                        "--socket",
                        str(socket),
                        "--session-file",
                        str(session),
                        "--json",
                        "capabilities",
                        "describe",
                        capability_name,
                    ],
                    env=env,
                )
                payload = described.get("data", {})
                capability = payload.get("capability", {})
                provenance = payload.get("provenance", {})
                if capability.get("name") != capability_name:
                    raise AssertionError(
                        f"pinned MLT capability describe mismatch: {capability_name}: {capability}"
                    )
                if provenance.get("provider") != "driver:mlt-video":
                    raise AssertionError(
                        f"pinned MLT capability has unexpected provider: "
                        f"{capability_name}: {provenance}"
                    )
            if not session.is_file() or (session.stat().st_mode & 0o777) != 0o600:
                raise AssertionError("Semwright did not create a private persistent session ticket")

            connection_path = paths["config"] / "motionwright-connection.json"
            write_private_json(
                connection_path,
                {
                    "schema": "motionwright-semwright-connection/1",
                    "executable": str((SW_BINS / "semwright").resolve()),
                    "executable_sha256": digest(SW_BINS / "semwright"),
                    "socket": str(socket),
                    "session_file": str(session),
                    "output_root": str(paths["output"]),
                    "resource": seeded["resource"],
                },
            )

            write_new_private_json(EVIDENCE / "native-assembly-intent.json", {
                "schema": "motionwright.native-mlt-ci.assembly-intent/1", "context": custody_context,
                "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "state": "UNKNOWN_UNTIL_OUTCOME", "operation": "original_assemble_request",
                "native_request_retried": False,
            })
            try:
                result = run_json(
                    [
                        str(MW_BIN),
                        "assemble",
                        str(database),
                        str(connection_path),
                        str(EVIDENCE / "native-multisegment-timeline-evidence.json"),
                    ],
                    env=env,
                    timeout=1050,
                )
            except (AssertionError, subprocess.TimeoutExpired) as original_error:
                assembly_journal_complete = record_assembly_failure(EVIDENCE, custody_context, original_error)
                # The pinned renderer's raw failure receipt contains a local
                # stack and owner paths: NEVER print or upload that receipt.
                # Persist only a fixed vocabulary derived before the owner's
                # temporary, private output root is destroyed.
                safe = safe_observation_diagnostic(paths["output"])
                try:
                    write_private_json(EVIDENCE / "safe-observation-failure.json", safe)
                except OSError:
                    pass  # Never replace the original native failure.
                print(json.dumps(safe, sort_keys=True), file=sys.stderr)
                raise
            write_new_private_json(EVIDENCE / "native-assembly-outcome.json", {
                "schema": "motionwright.native-mlt-ci.assembly-outcome/1", "context": custody_context,
                "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "state": "ORIGINAL_JSON_RETURNED_NOT_YET_VALIDATED", "native_request_retried": False,
            })
            assembly_journal_complete = True
            if result.get("native_mlt_multisegment_e2e") != "PASS":
                raise AssertionError("Real source-bound MLT timeline was not successful: " + repr(result))

            # Inspect the actual source journal produced by Semwright-backed
            # native MLT project.create. This is not a mock allocation count
            # and never claims the provider project was actually closed.
            inventory_path = EVIDENCE / "native-mlt-lifetime-inventory.json"
            if (not inventory_path.is_file() or inventory_path.is_symlink()
                or not 1 <= inventory_path.stat().st_size <= 16_384):
                raise AssertionError("Native MLT lifetime source evidence was missing or oversized")
            inventory_bytes = inventory_path.read_bytes()
            if (result.get("native_mlt_lifetime_inventory_sha256")
                != hashlib.sha256(inventory_bytes).hexdigest()):
                raise AssertionError("Native MLT lifetime receipt bytes differ from Rust source")
            inventory = json.loads(inventory_bytes)
            expected_keys = {
                "schema", "project_id", "generation", "revision",
                "source_receipts_scanned", "confirmed_native_creations_current_revision",
                "confirmed_native_creations_historical",
                "unconfirmed_or_invalid_creation_requests",
                "observed_close_request_identities", "disposition",
                "complete_source_receipt_snapshot",
                "native_provider_liveness_verified",
                "foreground_cleanup_consent_obtained",
                "verified_native_project_cleanup",
                "automatic_destructive_cleanup_allowed",
            }
            if not isinstance(inventory, dict) or set(inventory) != expected_keys:
                raise AssertionError("Native MLT lifetime receipt schema exposes untrusted fields")
            current = inventory.get("confirmed_native_creations_current_revision")
            receipt_total = inventory.get("source_receipts_scanned")
            if (
                inventory["schema"] != "motionwright.v01-mlt-cleanup-inventory/1"
                or inventory["project_id"] != seeded["project_id"]
                or inventory["generation"] != seeded["generation"]
                or inventory["revision"] != seeded["revision"]
                or type(current) is not int or not 1 <= current <= 4
                or type(receipt_total) is not int or receipt_total < current
                or result.get("native_mlt_lifetime_confirmed_current_creations") != current
                or result.get("native_mlt_lifetime_receipts_scanned") != receipt_total
                or result.get("native_mlt_lifetime_close_requests_observed") != 0
                or result.get("native_mlt_cleanup_verified") is not False
                or inventory["unconfirmed_or_invalid_creation_requests"] != 0
                or inventory["observed_close_request_identities"] != 0
                or inventory["disposition"] != "project_lifetime_unverified_foreground_consent_required"
                or inventory["complete_source_receipt_snapshot"] is not True
                or any(inventory[key] is not False for key in (
                    "native_provider_liveness_verified",
                    "foreground_cleanup_consent_obtained",
                    "verified_native_project_cleanup",
                    "automatic_destructive_cleanup_allowed",
                ))
            ):
                raise AssertionError("Real MLT project creation provenance/lifetime is unconfirmed")
            if (result.get("frame_count") != CONTRACT.video_frames
                or result.get("segment_count") != len(CONTRACT.source_segment_frames)):
                raise AssertionError("Native timeline lost the 32+1 source partition")
            if result.get("evidence_scope") != EXPECTED_SCOPE or result.get("native_render_profile") != PROFILE:
                raise AssertionError("MLT result falsely claimed final H.264/AAC master")

            evidence_path = EVIDENCE / "native-multisegment-timeline-evidence.json"
            evidence = json.loads(evidence_path.read_text())
            if (evidence.get("provider_project_cleanup") != "not_requested_requires_foreground_broker_consent"
                or result.get("provider_project_cleanup") != "not_requested_requires_foreground_broker_consent"):
                raise AssertionError("Native MLT receipt did not disclose pending foreground-only cleanup")
            if evidence.get("evidence_scope") != EXPECTED_SCOPE or evidence.get("native_render_profile") != PROFILE:
                raise AssertionError("MLT output identity and exact closed profile diverged")
            if evidence.get("transport_pcm_audio") is VIDEO_ONLY:
                raise AssertionError("Native MLT claimed wrong audio presence")
            if VIDEO_ONLY and evidence.get("native_frame_count_observed") != CONTRACT.video_frames:
                raise AssertionError("New native video-only profile lacks actual decoded frame count")
            if evidence.get("source", {}).get("total_frames") != CONTRACT.video_frames:
                raise AssertionError("Real FFV1 intermediate frame total was altered")
            records = evidence.get("source", {}).get("verified_video_segments", [])
            if (len(records) != len(CONTRACT.source_segment_frames)
                or [item["frame_count"] for item in records] != list(CONTRACT.source_segment_frames)):
                raise AssertionError("Real native source FFV1s are not the exact two source segments")
            if [item["output_start_frame"] for item in records] != [
                sum(CONTRACT.source_segment_frames[:index])
                for index in range(len(CONTRACT.source_segment_frames))
            ]:
                raise AssertionError("Native output clip start frames were not preserved")
            for record in records:
                artifact = paths["output"] / record["artifact_path"]
                if (not artifact.is_file() or artifact.stat().st_size != record["artifact_bytes"]
                    or digest(artifact) != record["artifact_sha256"]):
                    raise AssertionError("Native source FFV1 bytes changed since Semwright verification")

            video_path = paths["output"] / result["artifact_path"]
            if not video_path.is_file() or video_path.stat().st_size != result["artifact_bytes"]:
                raise AssertionError("Native MLT timeline artifact does not exist with measured size")
            if digest(video_path) != result["artifact_sha256"]:
                raise AssertionError("Native MLT timeline SHA-256 mismatch")
            meta_result = subprocess.run(
                [
                    str(ffprobe), "-v", "error", "-count_frames",
                    "-show_entries", "stream=codec_name,codec_type,width,height,nb_read_frames,r_frame_rate",
                    "-of", "json", str(video_path),
                ],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=30, check=True,
            )
            streams = json.loads(meta_result.stdout)["streams"]
            videos = [item for item in streams if item.get("codec_type") == "video"]
            audios = [item for item in streams if item.get("codec_type") == "audio"]
            if len(videos) != 1 or len(audios) != (0 if VIDEO_ONLY else 1):
                raise AssertionError("MLT native render audio presence differs from exact closed profile")
            if not VIDEO_ONLY and audios[0].get("codec_name") != "pcm_s16le":
                raise AssertionError("Native MLT PCM transport audio differs")
            if result.get("transport_pcm_audio") is VIDEO_ONLY:
                raise AssertionError("MLT result contradicted source-bound profile")
            if evidence.get("native_frame_count_observed") not in (None, CONTRACT.video_frames):
                raise AssertionError("MLT native frame count observation contradicted source")
            video = videos[0]
            if (video.get("codec_name") != "ffv1"
                or (video.get("width"), video.get("height")) != (CONTRACT.width, CONTRACT.height)
                or video.get("r_frame_rate") != f"{CONTRACT.fps_num}/{CONTRACT.fps_den}"
                or video.get("nb_read_frames") != str(CONTRACT.video_frames)):
                raise AssertionError("Native actual MLT video codec/frame provenance failed: " + repr(video))

            shutil.copyfile(video_path, EVIDENCE / VIDEO_FILE)
            master_check = {}
            if FINAL_MASTER:
                av_receipt = json.loads(
                    (EVIDENCE / "native-multisegment-av-master-evidence.json").read_text()
                )
                master_scope = (
                    "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved"
                    if CAPTIONED_MASTER else
                    "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved"
                )
                if (av_receipt["frame_count"] != CONTRACT.video_frames
                    or av_receipt["measured_voice"]["audio_pcm_frames"] != CONTRACT.pcm_frames
                    or av_receipt["measured_voice"].get("caption_cue_count")
                       != (2 if CAPTIONED_MASTER else None)
                    or result.get("native_captioned_master_requested") is not CAPTIONED_MASTER
                    or result.get("source_bound_caption_cue_count")
                       != (2 if CAPTIONED_MASTER else None)
                    or av_receipt["evidence_scope"] != master_scope):
                    raise AssertionError("Native final AV master source/voice provenance differs")
                relative = result.get("master_artifact_path")
                if not isinstance(relative, str) or not relative.endswith(".mp4"):
                    raise AssertionError("Native master has no bounded MP4 output")
                master = paths["output"] / relative
                if not master.is_file():
                    raise AssertionError("Native final AV mux did not publish a real MP4")
                master_metadata = master.lstat()
                if (not stat.S_ISREG(master_metadata.st_mode)
                    or getattr(master_metadata, "st_file_attributes", 0) & 0x400
                    or not 0 < master_metadata.st_size <= 256_000_000):
                    raise AssertionError("Native AAC master is not a bounded regular file")
                if (master.stat().st_size != result.get("master_artifact_bytes")
                    or digest(master) != result.get("master_artifact_sha256")
                    or digest(master) != av_receipt["master"]["artifact"]["sha256"]):
                    raise AssertionError("Actual MP4 bytes do not match both native receipts")
                # Retain admitted originals before any decoded-content gate
                # can fail and the native temporary directory is removed.
                retained_source = retain_native_master_source(
                    paths["output"], EVIDENCE, av_receipt, MODE,
                )
                retained_master = EVIDENCE / "native-multisegment-master.mp4"
                original_master = technical_record(master, 256_000_000)
                if (original_master["bytes"] != result["master_artifact_bytes"]
                    or original_master["sha256"] != result["master_artifact_sha256"]
                    or original_master["sha256"] != av_receipt["master"]["artifact"]["sha256"]):
                    raise AssertionError("Original AAC master changed before bounded retention")
                technical_copy(master, retained_master, 256_000_000, original_master)
                if (retained_master.stat().st_size != result["master_artifact_bytes"]
                    or digest(retained_master) != result["master_artifact_sha256"]):
                    raise AssertionError("Retained original AAC master differs from native receipts")
                master = retained_master
                mux_probe = subprocess.run(
                    [
                        str(ffprobe), "-v", "error", "-count_frames",
                        "-show_entries",
                        "stream=codec_type,codec_name,nb_read_frames,width,height,r_frame_rate,sample_rate,channels",
                        "-of", "json", str(master),
                    ],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=45, check=True,
                )
                av_streams = json.loads(mux_probe.stdout)["streams"]
                if len(av_streams) != 2:
                    raise AssertionError("Final MP4 must have exactly H264 video and AAC stereo audio")
                v = [stream for stream in av_streams if stream.get("codec_type") == "video"]
                a = [stream for stream in av_streams if stream.get("codec_type") == "audio"]
                if (len(v) != 1 or len(a) != 1 or v[0].get("codec_name") != "h264"
                    or v[0].get("nb_read_frames") != str(CONTRACT.video_frames)
                    or (v[0].get("width"),v[0].get("height")) != (CONTRACT.width, CONTRACT.height)
                    or v[0].get("r_frame_rate") != f"{CONTRACT.fps_num}/{CONTRACT.fps_den}"
                    or a[0].get("codec_name") != "aac"
                    or a[0].get("sample_rate") != str(CONTRACT.sample_rate)
                    or a[0].get("channels") != CONTRACT.channels):
                    raise AssertionError("Independent MP4 decoder did not verify exact AV output")
                decoded = av_receipt["decoded_audio"]
                wav_path = paths["output"] / decoded["path"]
                if not wav_path.is_file() or digest(wav_path) != decoded["sha256"]:
                    raise AssertionError("MLT native decoded PCM audit is missing or changed")
                # The actual MP4, not the receipt or source WAV, must retain
                # the authored, distinct 400/600 Hz left/right test signals.
                decoded_content = inspect_native_master_decoded_content(
                    master, ffmpeg, env, source_wav=EVIDENCE / SOURCE_WAV_NAME,
                    ffprobe=ffprobe, evidence_root=EVIDENCE, mode=MODE,
                )
                write_private_json(EVIDENCE / "decoded-stereo-content-proof.json", decoded_content)
                master_check = {
                    "retained_source_audio": retained_source,
                    "final_master_frames":CONTRACT.video_frames,
                    "final_master_video_codec":"h264",
                    "final_master_audio_codec":"aac",
                    "final_master_audio_rate":CONTRACT.sample_rate,
                    "final_master_audio_channels":CONTRACT.channels,
                    "final_master_sha256":digest(master),
                    "final_master_bytes":master.stat().st_size,
                    "measured_voice_pcm_frames":CONTRACT.pcm_frames,
                    "decoded_audio_sha256":decoded["sha256"],
                    "final_evidence_scope":av_receipt["evidence_scope"],
                    **decoded_content,
                }
                if CAPTIONED_MASTER:
                    # Independent FFmpeg readback of native FFV1 and actual
                    # lossy H264. This comparison neither adds a glyph nor
                    # synthesizes a new render or MLT composition.
                    proof = prove_native_captioned_master(
                        video_path, master, ffmpeg,
                        source_sha=GITHUB_SHA,
                        semwright_sha=PIN,
                        selected_cues=av_receipt["measured_voice"]["caption_cue_count"],
                    )
                    if (proof["original_ffv1_sha256"] != digest(video_path)
                        or proof["actual_master_mp4_sha256"] != digest(master)
                        or proof["captioned_master_decoded_pixels"] != "PASS"
                        or proof["native_cue_count"] != 2
                        or proof["commercial_release_authorized"] is not False):
                        raise AssertionError(
                            "Original native caption glyphs did not survive the actual lossy MLT master"
                        )
                    write_private_json(
                        EVIDENCE / "captioned-master-pixel-evidence.json", proof,
                    )
                    master_check.update({
                        "captioned_master_decoded_pixels": "PASS",
                        "captioned_source_cue_count": 2,
                        "native_caption_mask_pixels": {
                            key: value["native_caption_mask_pixels"]
                            for key, value in proof["active_caption_frames"].items()
                        },
                        "native_captioned_human_legibility": "NOT_RUN",
                    })
            write_private_json(
                EVIDENCE / "result.json",
                {
                    "native_mlt_multisegment_e2e": "PASS",
                    "motionwright_sha": GITHUB_SHA,
                    "semwright_sha": PIN,
                    "semwright_version": semwright_version(),
                    "project": seeded,
                    "motion_canvas_driver_sha256": digest(driver),
                    "mlt_driver_sha256": digest(mlt_driver),
                    "mlt_runner_sha256": digest(mlt_runner),
                    "melt_sha256": digest(melt),
                    "ffprobe_sha256": digest(ffprobe),
                    "ffmpeg_sha256": digest(ffmpeg),
                    "rendered_source_frames": list(CONTRACT.source_segment_frames),
                    "assembled_video_frames": CONTRACT.video_frames,
                    "mode": MODE,
                    "evidence_scope": EXPECTED_SCOPE,
                    "native_render_profile": PROFILE,
                    "provider_project_cleanup": evidence["provider_project_cleanup"],
                    "native_mlt_lifetime_inventory_sha256": digest(inventory_path),
                    "native_mlt_lifetime_confirmed_current_creations": current,
                    "native_mlt_lifetime_receipts_scanned": receipt_total,
                    "native_mlt_lifetime_close_requests_observed": 0,
                    "native_mlt_cleanup_verified": False,
                    "assembled_video_codec": "ffv1",
                    "assembled_video_has_audio": not VIDEO_ONLY,
                    "transport_audio_codec": None if VIDEO_ONLY else "pcm_s16le",
                    "video_bytes": video_path.stat().st_size,
                    "video_sha256": digest(video_path),
                    **master_check,
                },
            )
        finally:
            wrapper_failed = sys.exc_info()[0] is not None
            try:
                if daemon.poll() is None:
                    pid = daemon.pid
                    daemon.terminate()
                    try:
                        daemon.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(pid, signal.SIGKILL)
                        daemon.wait(timeout=5)
            finally:
                log_stream.close()
                try:
                    custody = stage_technical_custody(
                        EVIDENCE, TECHNICAL, root, MODE, custody_context, wrapper_failed,
                        assembly_journal_complete,
                    )
                    if not custody["complete_for_existing_allowlisted_files"] and not wrapper_failed:
                        raise AssertionError("Native technical custody is incomplete; original observations are not a PASS")
                except (OSError, PublicationError):
                    # A custody error must not replace the original native or
                    # decoder failure. The later read-only status step fails too.
                    if not wrapper_failed:
                        raise AssertionError("Native technical custody could not be retained") from None
                    print("native-mlt-technical-custody=incomplete", file=sys.stderr)

    print(
        json.dumps(
            {
                "native_mlt_multisegment_e2e": "PASS",
                "motionwright_sha": GITHUB_SHA,
                "semwright_sha": PIN,
                "evidence": str(EVIDENCE.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--verify-technical-custody"]:
        try:
            complete = verify_technical_custody(
                TECHNICAL, ROOT / "verification" / "native-mlt-sequence-custody-upload" / GITHUB_SHA,
                MODE, technical_context(), os.environ.get("NATIVE_CI_STEP_OUTCOME", ""),
            )
        except (OSError, PublicationError, KeyError, TypeError):
            print("native-mlt-technical-custody=refused", file=sys.stderr)
            raise SystemExit(2) from None
        raise SystemExit(0 if complete else 1)
    if sys.argv[1:]:
        raise SystemExit("Unknown native MLT wrapper arguments")
    main()
