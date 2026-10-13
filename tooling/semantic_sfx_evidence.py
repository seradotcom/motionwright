#!/usr/bin/env python3
"""Independent exact-source check of native PCM16 semantic SFX audio.

Does not create audio or claim MP4, legal rights, LUFS or human mix quality.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import wave

SCHEMA = "motionwright.semantic-sfx-native-technical-evidence/1"
FRAMES_PER_SECOND = 30
SAMPLES_PER_FRAME = 1600
STEREO_HZ = 48000


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pcm16(path: Path) -> tuple[int, bytes]:
    with wave.open(str(path), "rb") as input_file:
        if (input_file.getframerate(), input_file.getnchannels(),
                input_file.getsampwidth(), input_file.getcomptype()) != (
                    STEREO_HZ, 2, 2, "NONE"):
            raise AssertionError("Imported native original SFX audio is not stereo 48 kHz PCM16")
        frames = input_file.getnframes()
        audio = input_file.readframes(frames)
    if len(audio) != frames * 4:
        raise AssertionError("Native SFX source WAV has incomplete PCM")
    return frames, audio


def at(samples: bytes, frame: int, channel: int) -> int:
    return struct.unpack_from("<h", samples, (frame * 2 + channel) * 2)[0]


def expected_source_sample(cue: dict, stem: bytes, frame: int, channel: int) -> float:
    start = cue["offset_frames"] * SAMPLES_PER_FRAME
    count = cue["source_frames"] * SAMPLES_PER_FRAME
    offset = frame - start
    if offset < 0 or offset >= count:
        return 0.0
    attack = cue["attack_frames"] * SAMPLES_PER_FRAME
    release = cue["release_frames"] * SAMPLES_PER_FRAME
    entrance = 1.0 if not attack else min(1.0, offset / attack)
    tail = 1.0 if not release else min(1.0, (count - offset - 1) / release)
    gain = 10.0 ** (cue["gain_milli_db"] / 20000)
    return at(stem, offset, channel) * gain * min(entrance, tail)


def inspect(folder: Path, *, expected_source_sha: str | None = None) -> dict:
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    project = json.loads((folder / "editable-project.json").read_text(encoding="utf-8"))
    if result.get("schema") != SCHEMA:
        raise AssertionError("Unrecognized original native SFX evidence version")
    if not isinstance(result.get("motionwright_sha"), str) or len(result["motionwright_sha"]) != 40:
        raise AssertionError("Native SFX source revision missing")
    if expected_source_sha is not None and result.get("motionwright_sha") != expected_source_sha:
        raise AssertionError("Native SFX mix belongs to a different source revision")
    if result.get("master_scope") != "original WAV native PCM technical mix, no AV mux":
        raise AssertionError("SFX evidence cannot pretend to be a completed MP4")
    if result.get("creative_approval") != "required":
        raise AssertionError("SFX cannot claim artistic approval")
    if result.get("rights") != "synthetic owner-authored source, not external licensing review":
        raise AssertionError("Unknown audio source rights not independently reviewed")
    if result.get("frames") != 180 or result.get("sample_rate") != 48000:
        raise AssertionError("Native SFX mix must be the exact six-second pinned 30 fps fixture")
    wav_path = folder / "original-sfx-mix.wav"
    if sha(wav_path) != result["mixed_wav_sha256"]:
        raise AssertionError("Native SFX produced WAV differs from source-pinned receipt")
    total_frames, mix = pcm16(wav_path)
    if total_frames != result["frames"] * SAMPLES_PER_FRAME:
        raise AssertionError("Native audio does not have exact source frame/sample count")
    if result["project_id"] != project["id"] or (
            result["revision"] != project["revision"] or
            result["generation"] != project["generation"]):
        raise AssertionError("Source SFX mix does not belong to editable project CAS revision")
    cues = project["audio"]["sfx_cues"]
    if len(cues) != result["source_count"] or len(cues) != 2:
        raise AssertionError("Exactly two original imported semantic cues are expected")
    assets = {asset["id"]: asset for asset in project["assets"]}
    filenames = ["original-focus.wav", "original-confirmation.wav"]
    stems = {}
    for cue, filename, digest in zip(cues, filenames, result["source_shas"]):
        source_path = folder / filename
        if sha(source_path) != digest or digest != cue["source_sha256"]:
            raise AssertionError("SFX source WAV identity disagrees with immutable project")
        asset = assets[cue["source_asset_id"]]
        if asset["content_sha256"] != digest or asset["media_type"] != "audio/wav":
            raise AssertionError("SFX WAV project identity is not content-addressed")
        count, samples = pcm16(source_path)
        if count != cue["source_frames"] * SAMPLES_PER_FRAME:
            raise AssertionError("SFX source duration is not the original PCM file")
        if cue["original_source_attestation"].strip() == "":
            raise AssertionError("Missing source owner declaration")
        if cue["anchor"] != {"kind": "scene_start"}:
            raise AssertionError("Unapproved semantic anchor")
        stems[cue["id"]] = samples

    if at(mix, 0, 0) != 0 or at(mix, 5 * SAMPLES_PER_FRAME, 1) != 0:
        raise AssertionError("SFX has nonzero samples before the authored/attack frame")
    if at(mix, 50 * SAMPLES_PER_FRAME, 1) != 0:
        raise AssertionError("SFX tail illegally exceeds the source's end frame")
    sampled = []
    # Verify actual mixed PCM separately from the producer: three time regions,
    # both channels and attack/tail edges. No textual 'sound exists' proxy.
    for frame in [
        0, 4*SAMPLES_PER_FRAME, 5*SAMPLES_PER_FRAME, 5*SAMPLES_PER_FRAME+211,
        9*SAMPLES_PER_FRAME+17, 20*SAMPLES_PER_FRAME+11,
        24*SAMPLES_PER_FRAME+29, 34*SAMPLES_PER_FRAME+37,
        39*SAMPLES_PER_FRAME+43, 49*SAMPLES_PER_FRAME+47,
        50*SAMPLES_PER_FRAME, total_frames-1,
    ]:
        for channel in (0, 1):
            actual = at(mix, frame, channel)
            expected = sum(expected_source_sample(cue, stems[cue["id"]], frame, channel)
                           for cue in cues)
            if abs(actual - round(expected)) > 1:
                raise AssertionError(
                    f"Native mixed audio sample mismatch frame={frame} channel={channel}: "
                    f"{actual} != {expected}")
            sampled.append({"frame":frame, "channel":channel, "pcm16":actual})
    # Independently evaluate a genuinely mixed middle interval: it must not
    # be the same samples as either individual stem alone.
    frame = 27*SAMPLES_PER_FRAME+31
    actual = at(mix, frame, 0)
    separate = [
        round(expected_source_sample(cue, stems[cue["id"]], frame, 0))
        for cue in cues
    ]
    if not all(abs(actual-s) > 20 for s in separate):
        raise AssertionError("Source sounds did not audibly overlap in the actual PCM mix")
    peak = max(abs(x[0]) for x in struct.iter_unpack("<h", mix))
    if peak != result["sample_peak_pcm16"] or not (200 < peak < 20000):
        raise AssertionError("Native SFX peak measurement differs from decoded PCM")
    report = {
        "schema": "motionwright.semantic-sfx-independent-review/1",
        "technical_pcm": "PASS",
        "source_wav_shas": result["source_shas"],
        "mixed_wav_sha256": result["mixed_wav_sha256"],
        "total_sample_frames": total_frames,
        "sample_peak_pcm16": peak,
        "decoded_samples": sampled,
        "native_semwright_av_mux": "NOT_RUN",
        "legal_rights_certification": "NOT_PERFORMED",
        "human_creative_approval": "REQUIRED",
    }
    (folder / "independent-pcm-proof.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: semantic_sfx_evidence.py GENERATED_EVIDENCE_DIRECTORY [GIT_SHA]")
    print(json.dumps(inspect(Path(sys.argv[1]),
        expected_source_sha=sys.argv[2] if len(sys.argv) == 3 else None),
        indent=2, sort_keys=True))
