#!/usr/bin/env python3
"""v0.1: publish only verified MLT CI media, not Broker or project receipts.

All source observations remain confined to the disposable native CI runner.
This pure file admission stage NEVER contacts Semwright or dispatches a job.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys
import uuid

from native_mlt_mix_readback import ContentLimits

from cross_app_public_artifact import (
    PublicationError, read_bounded, require, strict_object,
)

SCHEMA = "motionwright.v01-native-mlt-public-evidence/1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
MODES = {"pcm", "video-only", "final-mp4", "captioned-mp4", "original-mix-mp4"}
VALID_STATUS = {"success", "failure", "cancelled", "skipped"}
NO_CLEANUP = "not_requested_requires_foreground_broker_consent"
SCOPES = {
    "pcm": "actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master",
    "video-only": "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
    "final-mp4": "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
    "captioned-mp4": "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
    "original-mix-mp4": "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
}
FINAL_SCOPE = "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved"
CAPTIONED_SCOPE = "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved"
LIMIT = 256_000_000
READBACK_DOCUMENT_LIMIT = 4 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
ORIGINAL_MIX_SCOPE = (
    "actual-native-canonical-original-mix-h264-aac-source-bound-not-mastered-or-human-approved"
)
ORIGINAL_MIX_SOURCE_SCOPE = (
    "verified-owner-original-mix-source-only-not-canonical-delivery-or-native-master"
)
ORIGINAL_MIX_RECEIPT = "native-multisegment-original-mix-master-evidence.json"
ORIGINAL_MIX_READBACK = "native-original-mix-readback.json"
ORIGINAL_MIX_PROVENANCE = "native-original-mix-provenance.json"
ORIGINAL_MIX_FIXTURE = "motionwright.native-mlt-ci.canonical-nonperiodic-original-mix/1"
# Literal identities from Rust calibration at source commit
# 276e85dfe12f96a8980f582bbcd20e746be57b0a, using the example binary with SHA-256
# 153d7ea41eeb575cd18d02aea5d927a56135654b756d012acbfbb903917fb2ff.
# These identify source bytes; calibration does not prove a native master readback.
# Never reconstruct either reference from AAC or a separate Python mixer.
ORIGINAL_MIX_WAV_SHA256 = "1de9a5fc0c57e8502e2eaac58246498c7b2be509027a4fac5e5056271fc86868"
ORIGINAL_MIX_PCM_SHA256 = "21c9fb918f852410aa8205f6697fe801f34e30a3c5a82e1799ecbe9dcd694729"
ORIGINAL_MIX_READER_SHA256 = (
    "5c66976b10357c7e97294e62bccb6844fad4277344ccb7c948e421f2fc267abc"
)
ORIGINAL_MIX_STEMS = {
    "measured_voice": (
        "92600baa4f59af065cf2d4132cd84943313e6c842392c226efe8eac00e176c49",
        "741d45438a68fa44d875398653c588f02a3a2674638070b1dc0149795a37f0a8",
    ),
    "original_music": (
        "708d8953769f4a92228df3417487e04344c6a3a3a07f89e4bc36e3f9b1031ad1",
        "3ea758e4872cd184d0f3ffb60e535e4f72d1f416867a396682bdf6a0de4c7e26",
    ),
    "original_sfx": (
        "efe99c94f674aa38fb094c9d37ba00ba2baca0c5b87cdc14449d79dbbca74a11",
        "b9c647103eee2fd4201da8be2da5d5b7bbe4ec2b77aa030d225e1f5ded1c4026",
    ),
}


SOURCE_WAV_NAME = "native-master-source.wav"
SOURCE_WAV_SCHEMA = "motionwright.synthetic-native-source-wav/1"
LEGACY_VOICE_FIXTURE = "motionwright.native-mlt-ci.stereo-400-600/1"
LEGACY_VOICE_WAV_SHA256 = (
    "66aeaa3aa6fff53834ab4003671ce79068bcbd6f631609f0f988b3ac9744ebe9"
)


ORIGINAL_MIX_SOURCE_PATHS = (
    "SOURCE_LOCK.json", "Cargo.toml", "Cargo.lock",
    "crates/native/examples/native-mlt-sequence-e2e.rs",
    "crates/native/src/original_mix.rs", "crates/native/src/production.rs",
    "crates/native/src/production/original_mix_av.rs",
    "crates/native/src/production/mlt_multisegment_av.rs",
    "tooling/native_mlt_sequence_e2e.py", "tooling/native_mlt_public_artifact.py",
    "tooling/native_mlt_mix_readback.py", "tooling/tests/test_native_mlt_mix_readback.py",
    ".github/workflows/native-mlt-sequence-e2e.yml",
)
ORIGINAL_MIX_TOOL_NAMES = (
    "motionwright_example", "semwright_cli", "semwright_daemon", "semwright_sandbox",
    "motion_canvas_driver", "mlt_driver", "mlt_runner",
    "melt", "ffmpeg", "ffprobe", "bwrap", "node",
)
ORIGINAL_MIX_VERSION_NAMES = ("ffmpeg", "ffprobe", "bwrap", "node")
ORIGINAL_MIX_READER_TEST_SHA256 = (
    "b954488db49aebf0dd8dc58ae590e050a4721336d24e1bb4a89c203e55a2e419"
)
ORIGINAL_MIX_AAC_TEST_IDS = tuple(
    "test_native_mlt_mix_readback.RealAacCalibrationTests." + name for name in (
        "test_real_aac_positive_retains_the_measured_comparison_limits",
        "test_real_audible_suffix_is_not_reclassified_as_codec_padding",
        "test_real_aac_cannot_hide_a_loss_of_the_last_100_samples",
        "test_real_periodic_audio_cannot_prove_zero_delay_or_hide_96_sample_shift",
    )
)


@dataclass(frozen=True)
class MltModeContract:
    video_frames: int
    source_segment_frames: tuple[int, ...]
    width: int = 1280
    height: int = 720
    scene_count: int = 33
    deliverable_count: int = 3
    fps_num: int = 30
    fps_den: int = 1
    sample_rate: int = 48000
    channels: int = 2
    source_fixture_id: str | None = None
    source_wav_sha256: str | None = None

    def __post_init__(self) -> None:
        values = (self.video_frames, self.width, self.height, self.fps_num,
                  self.fps_den, self.sample_rate, self.channels,
                  self.scene_count, self.deliverable_count)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("invalid_native_fixture_clock")
        if (not self.source_segment_frames
            or any(type(value) is not int or value <= 0
                   for value in self.source_segment_frames)
            or sum(self.source_segment_frames) != self.video_frames):
            raise ValueError("invalid_native_fixture_segment_clock")
        if (self.sample_rate, self.channels) != (48000, 2):
            raise ValueError("unsupported_native_fixture_pcm_profile")
        if (self.video_frames * self.sample_rate * self.fps_den) % self.fps_num:
            raise ValueError("fractional_native_fixture_sample_clock")
        if (self.source_fixture_id is None) != (self.source_wav_sha256 is None):
            raise ValueError("incomplete_native_fixture_identity")

    @property
    def pcm_frames(self) -> int:
        return self.video_frames * self.sample_rate * self.fps_den // self.fps_num

    @property
    def wav_bytes(self) -> int:
        return 44 + self.pcm_frames * self.channels * 2


# Each active consumer has its own clock and admitted Rust source identity.
MODE_CONTRACTS = {
    "pcm": MltModeContract(33, (32, 1)),
    "video-only": MltModeContract(33, (32, 1)),
    "original-mix-mp4": MltModeContract(
        60, (32, 28), source_fixture_id=ORIGINAL_MIX_FIXTURE,
        source_wav_sha256=ORIGINAL_MIX_WAV_SHA256,
    ),
    "final-mp4": MltModeContract(
        33, (32, 1), source_fixture_id=LEGACY_VOICE_FIXTURE,
        source_wav_sha256=LEGACY_VOICE_WAV_SHA256,
    ),
    "captioned-mp4": MltModeContract(
        33, (32, 1), source_fixture_id=LEGACY_VOICE_FIXTURE,
        source_wav_sha256=LEGACY_VOICE_WAV_SHA256,
    ),
}
assert set(MODE_CONTRACTS) == MODES


def mode_contract(mode: str) -> MltModeContract:
    require(type(mode) is str and mode in MODES, "invalid_mlt_mode")
    return MODE_CONTRACTS[mode]


def expected_source_proof(mode: str) -> dict:
    contract = mode_contract(mode)
    if mode == "original-mix-mp4":
        require(contract.source_fixture_id == ORIGINAL_MIX_FIXTURE
                and contract.source_wav_sha256 == ORIGINAL_MIX_WAV_SHA256
                and bool(SHA256.fullmatch(ORIGINAL_MIX_WAV_SHA256))
                and bool(SHA256.fullmatch(ORIGINAL_MIX_PCM_SHA256))
                and ORIGINAL_MIX_WAV_SHA256 not in (
                    LEGACY_VOICE_WAV_SHA256, ORIGINAL_MIX_STEMS["measured_voice"][0]
                )
                and ORIGINAL_MIX_PCM_SHA256 != ORIGINAL_MIX_STEMS["measured_voice"][1],
                "native_mlt_source_fixture_not_admitted")
    else:
        require(contract.source_fixture_id == LEGACY_VOICE_FIXTURE
                and contract.source_wav_sha256 == LEGACY_VOICE_WAV_SHA256,
                "native_mlt_source_fixture_not_admitted")
    return {
        "schema": SOURCE_WAV_SCHEMA,
        "mode": mode,
        "fixture_id": contract.source_fixture_id,
        "name": SOURCE_WAV_NAME,
        "sha256": contract.source_wav_sha256,
        "bytes": contract.wav_bytes,
        "source_kind": "original-mix" if mode == "original-mix-mp4" else "measured-voice",
        "video_frames": contract.video_frames,
        "fps_num": contract.fps_num,
        "fps_den": contract.fps_den,
        "pcm_frames": contract.pcm_frames,
        "sample_rate": contract.sample_rate,
        "channels": contract.channels,
        "bits_per_sample": 16,
        "synthetic_fixture_verified": True,
        "full_master_reference_readback": "NOT_RUN",
    }


def synthetic_source_proof(data: bytes, mode: str) -> dict:
    proof = expected_source_proof(mode)
    pcm_bytes = proof["pcm_frames"] * proof["channels"] * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + pcm_bytes, b"WAVE",
        b"fmt ", 16, 1, proof["channels"], proof["sample_rate"],
        proof["sample_rate"] * proof["channels"] * 2,
        proof["channels"] * 2, 16, b"data", pcm_bytes,
    )
    require(len(data) == proof["bytes"] and data[:44] == header
            and file_sha(data) == proof["sha256"],
            "native_mlt_synthetic_source_identity_mismatch")
    if mode == "original-mix-mp4":
        require(file_sha(data[44:]) == ORIGINAL_MIX_PCM_SHA256,
                "native_mlt_original_pcm_identity_mismatch")
    return proof


def verify_staged_source_receipt(receipt: dict, mode: str,
                                 project: dict | None = None) -> str:
    if mode == "original-mix-mp4":
        return verify_original_mix_source_receipt(receipt, project)
    proof = expected_source_proof(mode)
    require(isinstance(receipt, dict), "native_mlt_source_wav_receipt_mismatch")
    staged = receipt.get("staged_audio")
    voice = receipt.get("measured_voice")
    relative = "mw-ci-source-" + proof["sha256"][:16] + ".wav"
    require(
        isinstance(staged, dict)
        and set(staged) == {"relative_path", "sha256", "sample_rate", "channels"}
        and staged.get("relative_path") == relative
        and staged.get("sha256") == proof["sha256"]
        and type(staged.get("sample_rate")) is int
        and staged["sample_rate"] == proof["sample_rate"]
        and type(staged.get("channels")) is int
        and staged["channels"] == proof["channels"]
        and type(receipt.get("frame_count")) is int
        and receipt["frame_count"] == proof["video_frames"]
        and receipt.get("evidence_scope")
            == (CAPTIONED_SCOPE if mode == "captioned-mp4" else FINAL_SCOPE)
        and isinstance(voice, dict)
        and voice.get("audio_sha256") == proof["sha256"]
        and type(voice.get("audio_size_bytes")) is int
        and voice["audio_size_bytes"] == proof["bytes"]
        and type(voice.get("audio_sample_rate")) is int
        and voice["audio_sample_rate"] == proof["sample_rate"]
        and type(voice.get("audio_channels")) is int
        and voice["audio_channels"] == proof["channels"]
        and type(voice.get("total_frames")) is int
        and voice["total_frames"] == proof["video_frames"]
        and type(voice.get("fps_num")) is int
        and voice["fps_num"] == proof["fps_num"]
        and type(voice.get("fps_den")) is int
        and voice["fps_den"] == proof["fps_den"]
        and type(voice.get("audio_pcm_frames")) is int
        and voice["audio_pcm_frames"] == proof["pcm_frames"]
        and voice.get("caption_cue_count")
            == (2 if mode == "captioned-mp4" else None),
        "native_mlt_source_wav_receipt_mismatch",
    )
    return relative


def source_file_token(metadata: os.stat_result) -> tuple:
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns,
            getattr(metadata, "st_file_attributes", 0))


def verify_source_unchanged(path: Path, token: tuple) -> None:
    try:
        current = source_file_token(path.lstat())
    except OSError:
        raise PublicationError("native_mlt_source_wav_changed") from None
    require(current == token, "native_mlt_source_wav_changed")


def read_synthetic_source_wav(path: Path, mode: str) -> tuple[bytes, dict, tuple]:
    contract = mode_contract(mode)
    expected_source_proof(mode)
    try:
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode)
                and not (getattr(before, "st_file_attributes", 0) & 0x400),
                "native_mlt_source_wav_missing_or_linked")
        require(before.st_size == contract.wav_bytes,
                "native_mlt_synthetic_source_identity_mismatch")
        token = source_file_token(before)
        flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(os.open(path, flags), "rb") as stream:
            require(source_file_token(os.fstat(stream.fileno())) == token,
                    "native_mlt_source_wav_changed")
            data = stream.read(contract.wav_bytes + 1)
            require(source_file_token(os.fstat(stream.fileno())) == token,
                    "native_mlt_source_wav_changed")
        verify_source_unchanged(path, token)
    except OSError:
        raise PublicationError("native_mlt_source_wav_unreadable") from None
    return data, synthetic_source_proof(data, mode), token


def file_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_stable_native_file(path: Path, limit: int) -> tuple[bytes, tuple]:
    """Finite, no-follow read for the fifth mode's files and retained documents."""
    try:
        metadata = path.lstat()
        require(stat.S_ISREG(metadata.st_mode)
                and not (getattr(metadata, "st_file_attributes", 0) & 0x400),
                "native_mix_file_missing_or_linked")
        require(type(limit) is int and 0 < metadata.st_size <= limit,
                "native_mix_file_budget")
        token = source_file_token(metadata)
        flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(os.open(path, flags), "rb") as stream:
            require(source_file_token(os.fstat(stream.fileno())) == token,
                    "native_mix_file_changed")
            data = stream.read(metadata.st_size + 1)
            require(len(data) == metadata.st_size
                    and source_file_token(os.fstat(stream.fileno())) == token,
                    "native_mix_file_changed")
        verify_source_unchanged(path, token)
    except OSError:
        raise PublicationError("native_mix_file_unreadable") from None
    return data, token


def same_json_value(observed: object, expected: object) -> bool:
    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            same_json_value(observed[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            same_json_value(left, right) for left, right in zip(observed, expected)
        )
    return observed == expected


def exact_values(observed: object, expected: dict, code: str, *,
                 closed: bool = False) -> None:
    require(isinstance(observed, dict)
            and (not closed or set(observed) == set(expected))
            and all(key in observed and same_json_value(observed[key], value)
                    for key, value in expected.items()), code)


def canonical_fixture_ids(project: dict | None) -> tuple[str, str, int, str]:
    code = "native_mlt_original_mix_project_mismatch"
    require(isinstance(project, dict)
            and type(project.get("revision")) is int and project["revision"] >= 0
            and type(project.get("resource")) is str and bool(project["resource"]), code)
    compact = []
    for key in ("project_id", "generation", "master_profile_id"):
        value = project.get(key)
        require(type(value) is str, code)
        try:
            identity = uuid.UUID(value)
        except (ValueError, AttributeError):
            raise PublicationError(code) from None
        require(str(identity) == value and identity.int != 0, code)
        compact.append(identity.hex)
    return compact[0], compact[1], project["revision"], compact[2]


def original_mix_source_name(project: dict, plan_sha256: str) -> str:
    project_id, generation, revision, profile = canonical_fixture_ids(project)
    require(type(plan_sha256) is str and bool(SHA256.fullmatch(plan_sha256)),
            "native_mlt_original_mix_plan_mismatch")
    expected_source_proof("original-mix-mp4")
    return (f"mw-original-mix-{project_id}-{generation}-r{revision}-p{profile}-"
            f"{plan_sha256[:16]}-{ORIGINAL_MIX_WAV_SHA256[:16]}.wav")


def original_mix_master_name(project: dict) -> str:
    project_id, _, revision, _ = canonical_fixture_ids(project)
    token = file_sha(b"real-mlt-canonical-original-mix-60")[:16]
    return f"mw-{project_id}-r{revision}-{token}-multisegment-master.mp4"


def verify_original_mix_source_receipt(receipt: dict, project: dict | None) -> str:
    code = "native_mlt_original_mix_receipt_mismatch"
    proof = expected_source_proof("original-mix-mp4")
    canonical_fixture_ids(project)
    identity = {
        "project_resource": project["resource"], "generation": project["generation"],
        "revision": project["revision"], "deliverable_id": project["master_profile_id"],
        "frame_count": proof["video_frames"],
    }
    exact_values(receipt, {**identity, "evidence_scope": ORIGINAL_MIX_SCOPE}, code)
    source = receipt.get("original_mix")
    exact_values(source, {**identity, "fps_num": 30, "fps_den": 1,
                         "staged_wav_sha256": ORIGINAL_MIX_WAV_SHA256,
                         "staged_wav_bytes": proof["bytes"],
                         "evidence_scope": ORIGINAL_MIX_SOURCE_SCOPE}, code)
    mix = source.get("original_mix")
    exact_values(mix, {
        "frames": proof["pcm_frames"], "sample_rate": 48000, "channels": 2,
        "rendered_pcm_sha256": ORIGINAL_MIX_PCM_SHA256,
        "measured_music_duck_windows": 2,
        "owner_release_approved": False, "listening_intelligibility_approved": False,
        "independently_verified_lufs": False, "independently_verified_true_peak": False,
    }, code)
    relative = original_mix_source_name(project, mix.get("source_plan_sha256"))
    exact_values(receipt.get("staged_audio"), {
        "relative_path": relative, "sha256": ORIGINAL_MIX_WAV_SHA256,
        "sample_rate": 48000, "channels": 2,
    }, code, closed=True)
    for name, (wav_sha, pcm_sha) in ORIGINAL_MIX_STEMS.items():
        exact_values(source.get(name), {
            "source_asset_sha256": wav_sha, "decoded_pcm_sha256": pcm_sha,
            "sample_frames": proof["pcm_frames"],
        }, code)
    measured_voice = receipt.get("measured_voice")
    exact_values(measured_voice, {
        "audio_sha256": ORIGINAL_MIX_STEMS["measured_voice"][0],
        "audio_size_bytes": proof["bytes"], "audio_sample_rate": 48000,
        "audio_channels": 2, "audio_pcm_frames": proof["pcm_frames"],
        "total_frames": proof["video_frames"], "fps_num": 30, "fps_den": 1,
    }, code)
    # Rust omits Option::None for this uncaptioned source profile. An explicit
    # null or count is not the canonical serialized form admitted here.
    require("caption_cue_count" not in measured_voice, code)
    exact_values(receipt.get("canonical_dependency"), {
        "project_generation": project["generation"], "project_revision": project["revision"],
        "profile_id": project["master_profile_id"],
    }, code)
    exact_values(receipt.get("master"), {"audio_sha256": ORIGINAL_MIX_WAV_SHA256}, code)
    require(type(receipt.get("canonical_film_options_sha256")) is str
            and bool(SHA256.fullmatch(receipt["canonical_film_options_sha256"])), code)
    motion = receipt.get("canonical_motion_source")
    visual = receipt.get("video")
    exact_values(visual, identity, code)
    exact_values(visual.get("source"), {
        **{key: value for key, value in identity.items() if key != "frame_count"},
        "total_frames": 60, "fps_num": 30, "fps_den": 1,
    }, code)
    segments = visual["source"].get("verified_video_segments")
    require(isinstance(motion, list) and len(motion) == 2
            and isinstance(segments, list) and len(segments) == 2, code)
    seen_rows, seen_jobs, seen_segments = set(), set(), set()
    for index, (item, segment) in enumerate(zip(motion, segments)):
        exact_values(segment, {"frame_count": (32, 28)[index],
                               "output_start_frame": (0, 32)[index]}, code)
        for field in ("segment_id", "native_render_job_ref"):
            require(type(segment.get(field)) is str and 0 < len(segment[field]) <= 256
                    and all(ord(char) >= 32 for char in segment[field]), code)
        exact_values(item, {"segment_id": segment["segment_id"],
                            "native_job_ref": segment["native_render_job_ref"]}, code)
        require(item["segment_id"] not in seen_segments
                and item["native_job_ref"] not in seen_jobs, code)
        seen_segments.add(item["segment_id"])
        seen_jobs.add(item["native_job_ref"])
        for field in ("film_sha256", "plan_ref", "native_fingerprint", "connection_identity"):
            require(type(item.get(field)) is str and bool(SHA256.fullmatch(item[field])), code)
        require(type(item.get("provider_generation")) is int
                and item["provider_generation"] >= 0
                and isinstance(item.get("completed_receipt_ids"), list)
                and len(item["completed_receipt_ids"]) == 5, code)
        row_ids = []
        for value in item["completed_receipt_ids"]:
            require(type(value) is str, code)
            try:
                row = uuid.UUID(value)
            except ValueError:
                raise PublicationError(code) from None
            require(str(row) == value and row.int != 0 and value not in seen_rows, code)
            seen_rows.add(value)
            row_ids.append(row.int)
        # The Rust producer binds these positions to plan/apply/start/result/verify
        # in its original journal. Preserve its strict order and distinct identities.
        require(all(left < right for left, right in zip(row_ids, row_ids[1:])), code)
    return relative


def require_inventory(root: Path, result: dict) -> None:
    inventory_bytes = read_bounded(root, "native-mlt-lifetime-inventory.json", 128_000)
    inventory = strict_object(inventory_bytes)
    project = result.get("project")
    require(
        isinstance(project, dict)
        and inventory.get("project_id") == project.get("project_id")
        and inventory.get("generation") == project.get("generation")
        and inventory.get("revision") == project.get("revision"),
        "inventory_project_scope_mismatch",
    )
    require(file_sha(inventory_bytes) == result.get("native_mlt_lifetime_inventory_sha256"),
            "inventory_digest_mismatch")
    current = inventory.get("confirmed_native_creations_current_revision")
    require(
        inventory.get("schema") == "motionwright.v01-mlt-cleanup-inventory/1"
        and type(current) is int and 1 <= current <= 4
        and current == result.get("native_mlt_lifetime_confirmed_current_creations")
        and type(inventory.get("source_receipts_scanned")) is int
        and inventory["source_receipts_scanned"] == result.get("native_mlt_lifetime_receipts_scanned")
        and inventory.get("complete_source_receipt_snapshot") is True
        and inventory.get("unconfirmed_or_invalid_creation_requests") == 0
        and inventory.get("observed_close_request_identities") == 0
        and result.get("native_mlt_lifetime_close_requests_observed") == 0
        and inventory.get("disposition") == "project_lifetime_unverified_foreground_consent_required"
        and inventory.get("native_provider_liveness_verified") is False
        and inventory.get("foreground_cleanup_consent_obtained") is False
        and inventory.get("verified_native_project_cleanup") is False
        and inventory.get("automatic_destructive_cleanup_allowed") is False,
        "mlt_cleanup_authority_unproven",
    )


def original_mix_readback_projection(proof: dict, reference_sha: str,
                                     master_sha: str) -> dict:
    """Admit the existing reader's bounded result, without decoding or repairing."""
    code = "native_mlt_original_mix_readback_invalid"
    require(set(proof) == {
        "schema", "technical_content_readback", "reference_wav_sha256",
        "final_mp4_sha256", "clock", "content", "listening_approval",
        "rights_approval", "editable_source_reopen_verified", "independent_loudness_verified",
    }, code)
    exact_values(proof, {
        "schema": "motionwright.native-original-mix-readback/1",
        "technical_content_readback": "PASS", "reference_wav_sha256": reference_sha,
        "final_mp4_sha256": master_sha, "listening_approval": False,
        "rights_approval": False, "editable_source_reopen_verified": False,
        "independent_loudness_verified": False,
    }, code)
    samples = mode_contract("original-mix-mp4").pcm_frames
    limits = vars(ContentLimits())

    def integer(value, minimum, maximum):
        require(type(value) is int and minimum <= value <= maximum, code)
        return value

    def number(value, minimum, maximum):
        require(type(value) in (int, float) and math.isfinite(value)
                and minimum <= value <= maximum, code)
        return value

    clock = proof["clock"]
    fixed_clock = {
        "video_frames": 60, "video_packets": 60, "audio_duration_samples": samples,
        "first_video_pts": 0, "first_audio_pts": 0, "video_fps": "30",
        "audio_timebase": "1/48000", "exact_contiguous_frame_timestamps": True,
        "audio_packet_presentation_end_samples": samples,
        "packet_priming_and_presentation_checked": True,
    }
    dynamic_clock = {
        "audio_frames", "audio_packets", "decoded_audio_samples_from_frames",
        "priming_skip_samples", "discard_padding_samples",
        "final_audio_packet_duration_samples", "declared_decoder_suffix_budget_samples",
    }
    exact_values(clock, fixed_clock, code)
    require(set(clock) == set(fixed_clock) | dynamic_clock, code)
    packets = integer(clock["audio_packets"], 1, samples + 4096)
    integer(clock["audio_frames"], 1, packets)
    integer(clock["priming_skip_samples"], 0, 4096)
    integer(clock["discard_padding_samples"], 0, 1024)
    integer(clock["final_audio_packet_duration_samples"], 1, 1024)
    suffix_budget = integer(clock["declared_decoder_suffix_budget_samples"], 0, 1024)
    decoded = integer(clock["decoded_audio_samples_from_frames"],
                      samples, samples + suffix_budget)
    content = proof["content"]
    exact_values(content, {
        "reference_sample_frames": samples, "decoded_sample_frames": decoded,
        "decoded_access_unit_suffix_samples": decoded - samples,
        "reference_pcm_sha256": ORIGINAL_MIX_PCM_SHA256,
        "logical_interval": [0, samples], "all_logical_samples_compared": True,
        "limits": limits,
    }, code)
    require(set(content) == {
        "reference_sample_frames", "decoded_sample_frames",
        "decoded_access_unit_suffix_samples", "reference_pcm_sha256",
        "decoded_pcm_sha256", "logical_interval", "all_logical_samples_compared",
        "windows", "short_energy_windows", "alignment", "limits",
    } and type(content.get("decoded_pcm_sha256")) is str
        and bool(SHA256.fullmatch(content["decoded_pcm_sha256"])), code)
    windows = content["windows"]
    block = limits["block_samples"]
    expected_windows = [
        (channel, max(0, min(samples, start + block) - block)
         if start + block >= samples else start, min(samples, start + block))
        for channel in (0, 1) for start in range(0, samples, block)
    ]
    require(isinstance(windows, list) and len(windows) == len(expected_windows), code)
    correlations, gains, errors, silent_peaks = [], [], [], []
    for item, (channel, start, end) in zip(windows, expected_windows):
        exact_values(item, {"channel": channel, "start_sample": start,
                            "end_exclusive": end}, code)
        require(type(item.get("silent_reference")) is bool, code)
        metric_keys = ({"observed_peak"} if item["silent_reference"] else
                       {"correlation", "gain", "relative_error"})
        require(set(item) == {"channel", "start_sample", "end_exclusive",
                              "silent_reference"} | metric_keys, code)
        if item["silent_reference"]:
            silent_peaks.append(integer(item["observed_peak"], 0, limits["maximum_silent_sample"]))
        else:
            correlations.append(number(item["correlation"], limits["minimum_correlation"], 1.000000001))
            gains.append(number(item["gain"], limits["minimum_gain"], limits["maximum_gain"]))
            errors.append(number(item["relative_error"], 0, limits["maximum_relative_error"]))
    require(bool(correlations), code)
    short = content["short_energy_windows"]
    size, hop = limits["short_window_samples"], limits["short_window_samples"] // 2
    maximum_short = 2 * ((samples - size) // hop + 2)
    require(isinstance(short, list) and 0 < len(short) <= maximum_short, code)
    short_ids, short_gains = [], []
    for item in short:
        require(isinstance(item, dict) and set(item) == {
            "channel", "start_sample", "end_exclusive", "reference_rms", "gain",
        }, code)
        channel = integer(item["channel"], 0, 1)
        start = integer(item["start_sample"], 0, samples - size)
        require((start % hop == 0 or start == samples - size)
                and type(item["end_exclusive"]) is int
                and item["end_exclusive"] == start + size, code)
        number(item["reference_rms"], limits["minimum_short_window_rms"], 32768)
        short_gains.append(number(item["gain"], limits["minimum_short_window_gain"],
                                  limits["maximum_short_window_gain"]))
        short_ids.append((channel, start))
    require(short_ids == sorted(set(short_ids))
            and {item[0] for item in short_ids} == {0, 1}, code)
    alignment = content["alignment"]
    require(isinstance(alignment, list) and len(alignment) == 2, code)
    for channel, item in enumerate(alignment):
        expected = {
            "channel": channel, "best_lag_samples": 0,
            "searched_lag_samples": limits["diagnostic_lag_samples"],
            "fingerprint_positions": 512,
            "search_interval_samples": [-limits["diagnostic_lag_samples"],
                                        limits["diagnostic_lag_samples"]],
            "absolute_delay_absence_proven": False, "aligned_or_shifted_output": False,
        }
        exact_values(item, expected, code)
        require(set(item) == set(expected) | {"zero_lag_margin", "reference_zero_lag_margin"}, code)
        for key in ("zero_lag_margin", "reference_zero_lag_margin"):
            number(item[key], limits["minimum_lag_margin"], 2.000000001)
    # Only typed fields admitted above are projected; no provider/path text is copied.
    return {
        "schema": "motionwright.native-original-mix-readback-projection/1",
        "technical_content_readback": "PASS",
        "reference_wav_sha256": reference_sha, "reference_pcm_sha256": ORIGINAL_MIX_PCM_SHA256,
        "final_mp4_sha256": master_sha, "decoded_pcm_sha256": content["decoded_pcm_sha256"],
        "clock": clock, "logical_interval": [0, samples],
        "all_logical_samples_compared": True, "limits": limits,
        "decoded_access_unit_suffix_samples": decoded - samples,
        "window_count": len(windows), "minimum_window_correlation": min(correlations),
        "minimum_window_gain": min(gains), "maximum_window_gain": max(gains),
        "maximum_window_relative_error": max(errors),
        "silent_window_count": len(silent_peaks),
        "maximum_silent_window_sample": max(silent_peaks, default=0),
        "short_energy_window_count": len(short),
        "minimum_short_window_gain": min(short_gains),
        "maximum_short_window_gain": max(short_gains),
        "alignment": alignment, "listening_approval": False, "rights_approval": False,
        "editable_source_reopen_verified": False, "independent_loudness_verified": False,
        "publisher_decoded_media": False,
    }


def original_mix_provenance_projection(document: dict, source_sha: str,
                                       semwright_sha: str) -> dict:
    code = "native_mlt_original_mix_provenance_invalid"
    require(isinstance(document, dict) and set(document) == {
        "schema", "run_id", "run_attempt", "source_before", "source_after", "same_inputs",
    }, code)
    exact_values(document, {"schema": "motionwright.native-original-mix-provenance/1",
                            "same_inputs": True}, code)
    for key in ("run_id", "run_attempt"):
        require(type(document[key]) is str and document[key].isdigit()
                and 0 < int(document[key]) < 10**20, code)
    before, after = document["source_before"], document["source_after"]
    require(isinstance(before, dict) and same_json_value(before, after)
            and set(before) == {
                "motionwright_sha", "motionwright_tree", "semwright_sha", "semwright_tree",
                "semwright_declared_version", "tracked_worktrees_clean", "source_files", "tools",
                "tool_versions", "native_runtime_versions_sha256", "real_aac_test_gate",
            }, code)
    exact_values(before, {"motionwright_sha": source_sha, "semwright_sha": semwright_sha,
                          "tracked_worktrees_clean": True}, code)
    for key in ("motionwright_tree", "semwright_tree"):
        require(type(before[key]) is str and bool(SHA40.fullmatch(before[key])), code)
    require(type(before["semwright_declared_version"]) is str
            and 0 < len(before["semwright_declared_version"]) <= 64, code)
    sources = before["source_files"]
    tools = before["tools"]
    versions = before["tool_versions"]
    require(isinstance(sources, dict) and set(sources) == set(ORIGINAL_MIX_SOURCE_PATHS)
            and isinstance(tools, dict) and set(tools) == set(ORIGINAL_MIX_TOOL_NAMES)
            and isinstance(versions, dict) and set(versions) == set(ORIGINAL_MIX_VERSION_NAMES), code)
    for item in sources.values():
        require(isinstance(item, dict) and set(item) == {"bytes", "sha256"}
                and type(item["bytes"]) is int and 0 < item["bytes"] <= READBACK_DOCUMENT_LIMIT
                and type(item["sha256"]) is str and bool(SHA256.fullmatch(item["sha256"])), code)
    require(sources["tooling/native_mlt_mix_readback.py"]["sha256"]
            == ORIGINAL_MIX_READER_SHA256
            and sources["tooling/tests/test_native_mlt_mix_readback.py"]["sha256"]
            == ORIGINAL_MIX_READER_TEST_SHA256, code)
    exact_values(before["real_aac_test_gate"], {
        "schema": "motionwright.native-original-mix-real-aac-tests/1",
        "motionwright_sha": source_sha, "reader_sha256": ORIGINAL_MIX_READER_SHA256,
        "reader_tests_sha256": ORIGINAL_MIX_READER_TEST_SHA256,
        "test_ids": sorted(ORIGINAL_MIX_AAC_TEST_IDS),
        "tests_run": 4, "skipped": 0, "failures": 0, "errors": 0,
    }, code, closed=True)
    for item in tools.values():
        require(isinstance(item, dict) and set(item) == {"path", "bytes", "sha256"}
                and type(item["path"]) is str and Path(item["path"]).is_absolute()
                and type(item["bytes"]) is int and 0 < item["bytes"] <= 512 * 1024 * 1024
                and type(item["sha256"]) is str and bool(SHA256.fullmatch(item["sha256"])), code)
    require(all(type(value) is str and 0 < len(value) <= 65536 for value in versions.values())
            and type(before["native_runtime_versions_sha256"]) is str
            and bool(SHA256.fullmatch(before["native_runtime_versions_sha256"])), code)
    return {
        "motionwright_sha": source_sha, "motionwright_tree": before["motionwright_tree"],
        "semwright_sha": semwright_sha, "semwright_tree": before["semwright_tree"],
        "run_id": document["run_id"], "run_attempt": document["run_attempt"],
        "reader_sha256": ORIGINAL_MIX_READER_SHA256, "source_files": sources,
        "tool_sha256": {name: item["sha256"] for name, item in tools.items()},
        "tool_version_sha256": {name: file_sha(value.encode("utf-8"))
                               for name, value in versions.items()},
        "native_runtime_versions_sha256": before["native_runtime_versions_sha256"],
        "same_inputs_before_and_after": True,
        "real_aac_test_gate": before["real_aac_test_gate"],
    }


def verify_original_mix_master(root: Path, result: dict, source_sha: str,
                                semwright_sha: str) -> tuple[dict[str, bytes], dict]:
    code = "native_mlt_original_mix_master_invalid"
    master, _ = read_stable_native_file(root / "native-multisegment-master.mp4", LIMIT)
    require(len(master) >= 12 and master[4:8] == b"ftyp"
            and file_sha(master) == result.get("final_master_sha256")
            and type(result.get("final_master_bytes")) is int
            and len(master) == result["final_master_bytes"], code)
    exact_values(result, {
        "final_master_frames": 60, "final_master_video_codec": "h264",
        "final_master_audio_codec": "aac", "final_master_audio_rate": 48000,
        "final_master_audio_channels": 2, "original_mix_pcm_frames": 96000,
        "final_evidence_scope": ORIGINAL_MIX_SCOPE,
        "canonical_original_mix_requested": True,
    }, code)
    source, custody, _ = read_synthetic_source_wav(root / SOURCE_WAV_NAME, "original-mix-mp4")
    exact_values(result.get("retained_source_audio"), custody,
                 "native_mlt_retained_source_metadata_mismatch", closed=True)
    native_blob, _ = read_stable_native_file(root / ORIGINAL_MIX_RECEIPT, READBACK_DOCUMENT_LIMIT)
    native = strict_object(native_blob)
    exact_values(result, {"original_mix_native_receipt_sha256": file_sha(native_blob)}, code)
    verify_original_mix_source_receipt(native, result.get("project"))
    exact_values(native.get("master", {}).get("artifact"), {
        "root": "output", "path": original_mix_master_name(result["project"]),
        "sha256": file_sha(master), "bytes": len(master),
    }, code)
    exact_values(native["master"], {"video_sha256": result.get("video_sha256")}, code)
    exact_values(native.get("video"), {
        "frame_count": 60, "artifact_sha256": result.get("video_sha256"),
    }, code)
    exact_values(result, {
        "canonical_mix_source_plan_sha256": native["original_mix"]["original_mix"]["source_plan_sha256"],
        "canonical_mix_decoded_source_pcm_sha256": ORIGINAL_MIX_PCM_SHA256,
        "canonical_dependency": native["canonical_dependency"],
    }, code)
    readback_blob, _ = read_stable_native_file(root / ORIGINAL_MIX_READBACK, READBACK_DOCUMENT_LIMIT)
    provenance_blob, _ = read_stable_native_file(root / ORIGINAL_MIX_PROVENANCE, READBACK_DOCUMENT_LIMIT)
    exact_values(result, {
        "original_mix_readback_sha256": file_sha(readback_blob),
        "original_mix_provenance_sha256": file_sha(provenance_blob),
    }, code)
    projection = original_mix_readback_projection(
        strict_object(readback_blob), file_sha(source), file_sha(master)
    )
    provenance = original_mix_provenance_projection(strict_object(provenance_blob), source_sha, semwright_sha)
    for name, field in (
        ("motion_canvas_driver", "motion_canvas_driver_sha256"),
        ("mlt_driver", "mlt_driver_sha256"), ("mlt_runner", "mlt_runner_sha256"),
        ("melt", "melt_sha256"), ("ffmpeg", "ffmpeg_sha256"), ("ffprobe", "ffprobe_sha256"),
    ):
        exact_values(result, {field: provenance["tool_sha256"][name]}, code)
    projection.update({
        "readback_document_sha256": file_sha(readback_blob),
        "provenance_document_sha256": file_sha(provenance_blob), "source": provenance,
    })
    return {SOURCE_WAV_NAME: source, "native-multisegment-master.mp4": master}, projection


def verify_private_source(root: Path, mode: str, source_sha: str,
                          semwright_sha: str, *,
                          projections: dict | None = None) -> dict[str, bytes]:
    require(root.is_dir() and not root.is_symlink(),
            "missing_or_linked_private_evidence_root")
    result = strict_object(read_bounded(root, "result.json", 128_000))
    contract = mode_contract(mode)
    video_only = mode != "pcm"
    require(
        result.get("native_mlt_multisegment_e2e") == "PASS"
        and result.get("motionwright_sha") == source_sha
        and result.get("semwright_sha") == semwright_sha
        and result.get("mode") == mode
        and type(result.get("assembled_video_frames")) is int
        and result["assembled_video_frames"] == contract.video_frames
        and result.get("rendered_source_frames") == list(contract.source_segment_frames)
        and result.get("assembled_video_codec") == "ffv1"
        and result.get("assembled_video_has_audio") is (not video_only)
        and result.get("transport_audio_codec") == (None if video_only else "pcm_s16le")
        and result.get("native_render_profile") == (
            "lossless-video-only" if video_only else "lossless"
        )
        and result.get("provider_project_cleanup") == NO_CLEANUP
        and result.get("native_mlt_cleanup_verified") is False
        and result.get("evidence_scope") == SCOPES[mode],
        "native_mlt_source_not_verified",
    )
    timeline = strict_object(read_bounded(
        root, "native-multisegment-timeline-evidence.json", 256_000
    ))
    require(
        timeline.get("evidence_scope") == SCOPES[mode]
        and timeline.get("provider_project_cleanup") == NO_CLEANUP
        and type(timeline.get("frame_count")) is int
        and timeline["frame_count"] == contract.video_frames
        and timeline.get("transport_pcm_audio") is (not video_only)
        and timeline.get("native_render_profile") == (
            "lossless-video-only" if video_only else "lossless"
        )
        and (not video_only
             or timeline.get("native_frame_count_observed") == contract.video_frames),
        "native_mlt_timeline_evidence_invalid",
    )
    require_inventory(root, result)

    name = ("native-two-segment-video-only.mkv"
            if video_only else "native-two-segment-lossless.mkv")
    media = read_bounded(root, name, LIMIT)
    require(media.startswith(b"\x1a\x45\xdf\xa3"), "invalid_native_mlt_container")
    require(file_sha(media) == result.get("video_sha256")
            and len(media) == result.get("video_bytes"),
            "native_mlt_media_digest_mismatch")
    admitted = {name: media}
    if mode in ("final-mp4", "captioned-mp4"):
        master = read_bounded(root, "native-multisegment-master.mp4", LIMIT)
        require(len(master) >= 12 and master[4:8] == b"ftyp",
                "invalid_native_mlt_master")
        require(file_sha(master) == result.get("final_master_sha256")
                and len(master) == result.get("final_master_bytes")
                and type(result.get("final_master_frames")) is int
                and result["final_master_frames"] == contract.video_frames
                and result.get("final_master_video_codec") == "h264"
                and result.get("final_master_audio_codec") == "aac"
                and result.get("final_master_audio_rate") == 48000
                and result.get("final_master_audio_channels") == 2
                and result.get("decoded_stereo_content")
                == "distinct_source_400hz_left_600hz_right"
                and type(result.get("measured_voice_pcm_frames")) is int
                and result["measured_voice_pcm_frames"] == contract.pcm_frames
                and result.get("final_evidence_scope")
                    == (CAPTIONED_SCOPE if mode == "captioned-mp4" else FINAL_SCOPE),
                "native_mlt_master_evidence_invalid")
        stereo = strict_object(read_bounded(
            root, "decoded-stereo-content-proof.json", 128_000
        ))
        require(
            stereo.get("decoded_stereo_content") == result.get("decoded_stereo_content")
            and type(stereo.get("decoded_master_pcm_frames")) is int
            and stereo["decoded_master_pcm_frames"]
            == result.get("decoded_master_pcm_frames")
            and type(result.get("decoded_master_pcm_frames")) is int
            and contract.pcm_frames <= result["decoded_master_pcm_frames"]
                <= contract.pcm_frames + 1024,
            "native_mlt_stereo_evidence_invalid",
        )
        if mode == "captioned-mp4":
            # Original native PNG and FFV1/H264 bytes were inspected on the
            # disposable runner. Admit only closed numerical evidence from
            # the independent decoder, never transcript/media/session data.
            bounded = strict_object(read_bounded(
                root, "captioned-master-pixel-evidence.json", 32_768
            ))
            masks = bounded.get("active_caption_frames")
            counts = result.get("native_caption_mask_pixels")
            require(
                bounded.get("schema") == "motionwright-v05-native-captioned-master-pixels/1"
                and bounded.get("captioned_master_decoded_pixels") == "PASS"
                and bounded.get("motionwright_source_sha") == source_sha
                and bounded.get("semwright_source_sha") == semwright_sha
                and bounded.get("original_native_ffv1_frame_indices") == [0, 14, 15, 31, 32]
                and bounded.get("decoded_h264_frame_indices") == [0, 15, 32]
                and bounded.get("native_cue_count") == 2
                and bounded.get("original_ffv1_sha256") == result["video_sha256"]
                and bounded.get("actual_master_mp4_sha256") == result["final_master_sha256"]
                and bounded.get("human_readability") == "NOT_RUN"
                and bounded.get("commercial_release_authorized") is False
                and bounded.get("original_owner_media_or_transcript_published") is False
                and isinstance(masks, dict) and set(masks) == {"15", "32"}
                and isinstance(counts, dict) and set(counts) == {"15", "32"}
                and all(
                    isinstance(masks[key], dict)
                    and type(masks[key].get("native_caption_mask_pixels")) is int
                    and 350 <= masks[key]["native_caption_mask_pixels"] <= 100_000
                    and masks[key]["native_caption_mask_pixels"] == counts[key]
                    for key in ("15", "32")
                )
                and result.get("captioned_master_decoded_pixels") == "PASS"
                and result.get("captioned_source_cue_count") == 2
                and result.get("native_captioned_human_legibility") == "NOT_RUN",
                "native_captioned_master_decoded_pixels_not_verified",
            )
        source, proof, _ = read_synthetic_source_wav(root / SOURCE_WAV_NAME, mode)
        observed_source = result.get("retained_source_audio")
        require(
            isinstance(observed_source, dict) and set(observed_source) == set(proof)
            and all(type(observed_source[key]) is type(value)
                    and observed_source[key] == value for key, value in proof.items()),
            "native_mlt_retained_source_metadata_mismatch",
        )
        native_receipt = strict_object(read_bounded(
            root, "native-multisegment-av-master-evidence.json", 512_000,
        ))
        verify_staged_source_receipt(native_receipt, mode)
        admitted[SOURCE_WAV_NAME] = source
        admitted["native-multisegment-master.mp4"] = master
    if mode == "original-mix-mp4":
        mixed_media, projection = verify_original_mix_master(root, result, source_sha, semwright_sha)
        admitted.update(mixed_media)
        if projections is not None:
            projections["original_mix_readback"] = projection
    return admitted


def publish_mlt(root: Path, output: Path, mode: str, source_sha: str,
                semwright_sha: str, job_status: str) -> dict:
    require(mode in MODES, "invalid_mlt_mode")
    require(job_status in VALID_STATUS, "invalid_ci_job_status")
    require(bool(SHA40.fullmatch(source_sha))
            and bool(SHA40.fullmatch(semwright_sha)), "invalid_source_pin")
    require(not output.exists(), "public_artifact_already_exists")
    src = root.resolve()
    dst = output.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "public_root_overlaps_private_source")
    projections: dict = {}
    files = (verify_private_source(root, mode, source_sha, semwright_sha, projections=projections)
             if job_status == "success" else {})
    entries = [
        {"name": name, "bytes": len(data), "sha256": file_sha(data)}
        for name, data in sorted(files.items())
    ]
    receipt = {
        "schema": SCHEMA,
        "motionwright_source_sha": source_sha,
        "semwright_source_pin": semwright_sha,
        "mode": mode,
        "ci_native_mlt_evidence": "PASS" if job_status == "success" else "NOT_VERIFIED",
        "native_video_frames": mode_contract(mode).video_frames if job_status == "success" else None,
        "retained_source_audio": (
            synthetic_source_proof(files[SOURCE_WAV_NAME], mode)
            if SOURCE_WAV_NAME in files else None
        ),
        "native_captioned_h264_decoded_pixels":
            "PASS" if job_status == "success" and mode == "captioned-mp4" else "NOT_VERIFIED",
        "human_caption_readability": "NOT_RUN",
        "final_master_human_approved": False,
        "verified_native_project_cleanup": False,
        "foreground_cleanup_consent_obtained": False,
        "raw_provider_and_daemon_logs_uploaded": False,
        "original_native_project_receipts_uploaded": False,
        "native_mutations_replayed_by_publisher": False,
        "installed_owner_acceptance": "NOT_RUN",
        "v01_release_authorized": False,
        "included_files": entries,
        **projections,
    }
    require(len(json.dumps(receipt, allow_nan=False).encode("utf-8")) <= READBACK_DOCUMENT_LIMIT,
            "native_mlt_public_document_budget")
    output.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        with (output / name).open("xb") as stream:
            stream.write(data)
    with (output / "public-evidence.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args(argv)
    try:
        lock = strict_object((Path(__file__).resolve().parents[1] /
                              "SOURCE_LOCK.json").read_bytes())
        pin = lock["dependencies"]["semwright"]["revision"]
        receipt = publish_mlt(args.evidence_root, args.output_root,
                              args.mode, args.source_sha, pin, args.job_status)
    except (PublicationError, OSError, KeyError, TypeError) as exc:
        code = exc.code if isinstance(exc, PublicationError) else "public_artifact_refused"
        print("native-mlt-public-artifact=refused reason=" + code, file=sys.stderr)
        return 2
    print("native-mlt-public-artifact=" + receipt["ci_native_mlt_evidence"]
          + " files=" + str(len(receipt["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
