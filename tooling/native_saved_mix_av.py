"""Closed two-second SavedMix source/consumer oracle; no runtime media backend.

All stems are labelled synthetic tones. The PCM equation is checked independently
from the Rust factory; decoded AAC must retain every bus and the saved ducking.
No speech, source rights, Tauri invocation, mastering targets or release grant.
"""
from __future__ import annotations

import array
import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import sys
import wave

from native_av_media_probe import (
    MediaProbeError, inspect_streams, inspect_decoded_video,
)
from native_original_mix_custody import OriginalPcmCustody

RATE = 48_000
FRAMES = 96_000
WAV_BYTES = 44 + FRAMES * 4
BUSES = ("voice", "music", "sfx")
FREQUENCIES = {"voice": (440, 660), "music": (250, 370), "sfx": (910, 1230)}
LEVELS = {"voice": 6_000, "music": 8_000, "sfx": 4_000}
WINDOWS = {"voice": (24_000, 48_000), "music": (0, FRAMES), "sfx": (60_000, 84_000)}
SCOPE = "actual_decoded_h264_aac_two_second_saved_mix_fixture_only"
STEREO = "confirmed_saved_mix_three_bus_stereo"
PUBLIC_MEDIA = {f"saved-mix-{bus}.wav": WAV_BYTES for bus in BUSES}
PUBLIC_MEDIA.update({"saved-mix.wav": WAV_BYTES, "saved-mix-source-proof.json": 64 * 1024})


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise MediaProbeError(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_new(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def write_stems(root: Path) -> None:
    """Generate only three original CI tone WAVs; no mixed audio is generated here."""
    for bus in BUSES:
        start, end = WINDOWS[bus]
        pcm = bytearray()
        for frame in range(FRAMES):
            fade = max(0.0, min(1.0, (frame - start) / 960, (end - 1 - frame) / 960))
            for frequency in FREQUENCIES[bus]:
                value = round(LEVELS[bus] * fade * math.sin(math.tau * frequency * frame / RATE))
                pcm.extend(struct.pack("<h", value))
        blob = io.BytesIO()
        with wave.open(blob, "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(pcm)
        write_new(root / f"saved-mix-{bus}.wav", blob.getvalue())


def wav_pcm(raw: bytes) -> bytes:
    require(len(raw) == WAV_BYTES, "SavedMix WAV byte count differs from the closed source")
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            require((wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes())
                    == (2, 2, RATE, FRAMES), "SavedMix WAV format or clock changed")
            pcm = wav.readframes(FRAMES + 1)
    except (wave.Error, EOFError) as error:
        raise MediaProbeError("SavedMix WAV cannot be decoded") from error
    require(len(pcm) == FRAMES * 4, "SavedMix original PCM is incomplete")
    return pcm


def samples(raw: bytes) -> array.array:
    out = array.array("h")
    out.frombytes(raw)
    if sys.byteorder != "little":
        out.byteswap()
    return out


def duck_gain(frame: int) -> float:
    """Independent piecewise equation for the one authored fixture interval."""
    low = 10.0 ** (-14.0 / 20.0)
    if frame < 24_000:
        return 1.0
    if frame < 26_400:
        return 1.0 - (1.0 - low) * (frame - 24_000) / 2_400
    if frame < 48_000:
        return low
    if frame < 52_800:
        return low + (1.0 - low) * (frame - 48_000) / 4_800
    return 1.0


def amplitude(pcm: bytes, channel: int, frequency: int, begin: int, end: int) -> float:
    data = samples(pcm)
    require(len(data) >= end * 2, "SavedMix measured window is absent")
    # Fixed source-clock windows, not a searched AAC alignment or inferred offset.
    values = range(begin, end, 4)
    real = sum(data[2 * i + channel] * math.cos(math.tau * frequency * i / RATE) for i in values)
    imag = sum(data[2 * i + channel] * math.sin(math.tau * frequency * i / RATE) for i in values)
    return 2.0 * math.hypot(real, imag) / len(values) / 32768.0


def inspect_pcm_files(files: dict[str, bytes]) -> dict:
    inputs = {bus: wav_pcm(files[f"saved-mix-{bus}.wav"]) for bus in BUSES}
    mixed = wav_pcm(files["saved-mix.wav"])
    require(len({sha(raw) for raw in inputs.values()}) == 3,
            "SavedMix sources are not three distinct original buses")
    values = {bus: samples(pcm) for bus, pcm in inputs.items()}
    actual = samples(mixed)
    music_gain, sfx_gain = 10.0 ** (-12.0 / 20.0), 10.0 ** (-8.0 / 20.0)
    largest = 0
    for frame in range(FRAMES):
        gain = duck_gain(frame)
        for channel in range(2):
            index = 2 * frame + channel
            expected = (values["voice"][index] + values["music"][index] * music_gain * gain
                        + values["sfx"][index] * sfx_gain)
            # Exact inverse PCM16 scale; half ties follow Rust round-away-from-zero.
            quantized = math.floor(expected + 0.5) if expected >= 0 else math.ceil(expected - 0.5)
            largest = max(largest, abs(actual[index] - quantized))
    require(largest <= 1, "SavedMix actual WAV differs from the independent three-bus equation")
    # Reject a self-consistent but silent/mislabelled fixture before AAC is inspected.
    for bus, window in (("voice", (31_200, 40_800)), ("music", (9_600, 19_200)),
                        ("sfx", (64_800, 74_400))):
        for channel in range(2):
            own = amplitude(inputs[bus], channel, FREQUENCIES[bus][channel], *window)
            cross = amplitude(inputs[bus], channel, FREQUENCIES[bus][1 - channel], *window)
            require(own > LEVELS[bus] / 32768 * 0.9 and cross < own * 0.1,
                    "SavedMix original bus lost its distinct stereo fixture identity")
    return {
        "source_wav_sha256": {bus: sha(files[f"saved-mix-{bus}.wav"]) for bus in BUSES},
        "source_pcm_sha256": {bus: sha(inputs[bus]) for bus in BUSES},
        "mixed_wav_sha256": sha(files["saved-mix.wav"]),
        "mixed_pcm_sha256": sha(mixed), "compared_channel_samples": FRAMES * 2,
        "maximum_equation_error_lsb": largest,
    }


def inspect_aac(pcm: bytes, source_mix: bytes) -> dict:
    require(len(pcm) % 4 == 0 and FRAMES - 2_400 <= len(pcm) // 4 <= FRAMES + 4_800,
            "SavedMix AAC duration differs from the original two-second cut")
    source = wav_pcm(source_mix)
    measured = {}
    windows = {"voice": (31_200, 40_800), "music": (9_600, 19_200), "sfx": (64_800, 74_400)}
    for bus, window in windows.items():
        for channel in range(2):
            frequency = FREQUENCIES[bus][channel]
            expected = amplitude(source, channel, frequency, *window)
            observed = amplitude(pcm, channel, frequency, *window)
            wrong = amplitude(pcm, channel, FREQUENCIES[bus][1 - channel], *window)
            require(expected > 0.005 and 0.70 * expected <= observed <= 1.30 * expected,
                    "SavedMix AAC omitted or replaced a persisted source bus")
            require(wrong < observed * 0.20, "SavedMix AAC swapped or collapsed stereo channels")
            measured[f"{bus}_{channel}"] = round(observed / expected, 6)
    ratios = []
    for channel in range(2):
        frequency = FREQUENCIES["music"][channel]
        baseline = amplitude(pcm, channel, frequency, 9_600, 19_200)
        ducked = amplitude(pcm, channel, frequency, 31_200, 40_800)
        restored = amplitude(pcm, channel, frequency, 54_000, 58_800)
        require(0.12 <= ducked / baseline <= 0.30 and 0.70 <= restored / baseline <= 1.30,
                "SavedMix AAC does not retain the saved duck and release")
        ratios.append(round(ducked / baseline, 6))
    return {
        "decoded_pcm_frames": len(pcm) // 4, "decoded_seconds": len(pcm) / 4 / RATE,
        "stereo_channel_identity": STEREO, "three_buses_decoded": True,
        "bus_amplitude_ratios": measured, "music_duck_ratios": ratios,
        "aac_sample_exact_sync_claimed": False,
    }


def bounded_file(path: Path, limit: int) -> bytes:
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit,
            "SavedMix retained input is missing, linked or oversized")
    raw = path.read_bytes()
    require(len(raw) <= limit, "SavedMix retained input grew during read")
    return raw


def inspect_native_source(root: Path, files: dict[str, bytes], pcm_proof: dict) -> dict:
    original = json.loads(bounded_file(root / "saved-mix-source.private.json", 512 * 1024))
    readback = json.loads(bounded_file(root / "saved-mix-readback.json", 64 * 1024))
    require(original.get("schema") == "motionwright.saved-mix-prepared-source/1"
            and original.get("wav_sha256") == pcm_proof["mixed_wav_sha256"],
            "SavedMix Native factory receipt is not bound to the retained WAV")
    plan = original["plan"]
    for bus, key in (("voice", "measured_voice"), ("music", "original_music"), ("sfx", "original_sfx")):
        record = plan[key]
        require(record["source_asset_sha256"] == pcm_proof["source_wav_sha256"][bus]
                and record["decoded_pcm_sha256"] == pcm_proof["source_pcm_sha256"][bus]
                and record["sample_frames"] == FRAMES,
                "SavedMix CAS source receipt differs from a retained original bus")
    for key, expected in {"voice_gain_db": 0.0, "music_gain_db": -12.0, "sfx_gain_db": -8.0,
                          "duck_attenuation_db": 14.0, "duck_attack_samples": 2400,
                          "duck_release_samples": 4800, "sample_frames": FRAMES}.items():
        require(plan.get(key) == expected, "SavedMix stored recipe changed")
    require(plan.get("voice_windows") == [{"start_sample": 24000, "end_exclusive": 48000}]
            and original.get("automatic_retry") is False,
            "SavedMix original interval or uncertainty policy changed")
    require(readback.get("schema") == "motionwright.saved-mix-independent-readback/1"
            and readback.get("reader_authority") == "StudioServiceReader"
            and readback.get("verified_original_assets") == 3
            and readback.get("verified_original_bytes") == 3 * WAV_BYTES,
            "SavedMix independent physical Store readback is absent")
    for key in ("project_unchanged", "history_unchanged", "same_physical_database", "writer_handoff_verified"):
        require(readback.get(key) is True, "SavedMix independent readback did not verify its source")
    for key in ("automatic_retry", "human_approval", "release_authorized"):
        require(readback.get(key) is False, "SavedMix readback cannot grant approval or retry")
    return {
        "schema": "motionwright.saved-mix-public-source/1", "fixture": "saved-mix",
        "pcm": pcm_proof, "source_plan_sha256": original["evidence"]["source_plan_sha256"],
        "audio_inputs_sha256": original["audio_inputs_sha256"],
        "source_witness_sha256": readback["source_witness_sha256"],
        "same_physical_store_readback": True, "project_and_history_unchanged": True,
        "producer_returned_before_readback": True,
        "tauri_owner_invocation": "NOT_EXERCISED", "source_classification": "SYNTHETIC_TONES_NOT_SPEECH",
        "human_approval": False, "release_authorized": False, "automatic_retry": False,
    }


def probe_saved_mix(root: Path, master: Path, ffmpeg: Path, ffprobe: Path, *, source_sha: str, sdk: str) -> dict:
    files = {name: bounded_file(root / name, limit)
             for name, limit in PUBLIC_MEDIA.items() if name.endswith(".wav")}
    pcm_proof = inspect_pcm_files(files)
    public_source = inspect_native_source(root, files, pcm_proof)
    retained_master = root / "motionwright-master.mp4"
    master_bytes = bounded_file(retained_master, 4 * 1024 * 1024)
    require(sha(master_bytes) == sha(bounded_file(master, 4 * 1024 * 1024)),
            "SavedMix retained master differs from the Native output")
    capture = OriginalPcmCustody(root, Path(__file__).resolve().parents[1], {
        "scope": SCOPE, "source_sha": source_sha, "semwright_sha": sdk,
    })
    original_master = capture.observe(retained_master)
    for name in files:
        capture.observe(root / name)
    probe = capture.run([
        str(ffprobe), "-v", "error", "-count_frames", "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,sample_rate,channels:format=duration",
        "-of", "json", str(retained_master),
    ], "saved-mix-ffprobe", inputs=[original_master])
    streams = inspect_streams(json.loads(probe["stdout"]))
    decoded = capture.run([
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(retained_master),
        "-map", "0:a:0", "-ac", "2", "-ar", str(RATE), "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
    ], "saved-mix-aac-decode", inputs=[original_master])
    audio = inspect_aac(decoded["stdout"], files["saved-mix.wav"])
    frames = capture.run([
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(retained_master),
        "-map", "0:v:0", "-vf",
        r"select=eq(n\,0)+eq(n\,30),scale=160:90:flags=bicubic,format=rgb24",
        "-vsync", "0", "-f", "rawvideo", "pipe:1",
    ], "saved-mix-frame-decode", inputs=[original_master])
    video = inspect_decoded_video(frames["stdout"])
    for identity in list(capture.originals.values()):
        require(capture.observe(root / identity["path"]) == identity,
                "SavedMix observed decoder original changed")
    write_new(root / "saved-mix-decoder-custody.private.json", json.dumps({
        "schema": "motionwright.saved-mix-decoder-originals/1",
        "originals": list(capture.originals.values()), "commands": capture.commands,
        "automatic_retry": False,
    }, sort_keys=True).encode())
    write_new(root / "saved-mix-source-proof.json", json.dumps(public_source, sort_keys=True).encode())
    return {
        "evidence_scope": SCOPE, "master_sha256": sha(master_bytes),
        "source_wav_sha256": pcm_proof["mixed_wav_sha256"],
        "saved_mix_source_proof_sha256": sha((root / "saved-mix-source-proof.json").read_bytes()),
        **streams, **audio, **video,
    }
