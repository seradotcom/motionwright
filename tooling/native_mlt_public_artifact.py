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
import os
from pathlib import Path
import re
import stat
import struct
import sys

from cross_app_public_artifact import (
    PublicationError, read_bounded, require, strict_object,
)

SCHEMA = "motionwright.v01-native-mlt-public-evidence/1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
# Execution and publication require an implemented native consumer.
MODES = {"pcm", "video-only", "final-mp4", "captioned-mp4", "hybrid-av"}
VALID_STATUS = {"success", "failure", "cancelled", "skipped"}
NO_CLEANUP = "not_requested_requires_foreground_broker_consent"
SCOPES = {
    "pcm": "actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master",
    "video-only": "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
    "final-mp4": "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
    "captioned-mp4": "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
}
FINAL_SCOPE = "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved"
CAPTIONED_SCOPE = "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved"
LIMIT = 256_000_000


SOURCE_WAV_NAME = "native-master-source.wav"
SOURCE_WAV_SCHEMA = "motionwright.synthetic-native-source-wav/1"
LEGACY_VOICE_FIXTURE = "motionwright.native-mlt-ci.stereo-400-600/1"
LEGACY_VOICE_WAV_SHA256 = (
    "66aeaa3aa6fff53834ab4003671ce79068bcbd6f631609f0f988b3ac9744ebe9"
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


# The hybrid consumer owns its original MC / Manim / MC source and budget.
# Its readback metadata does not grant runtime access or physical acceptance.
# A future 60-frame / 96000-sample OriginalMix also needs its own source identity.
MODE_CONTRACTS = {
    "hybrid-av": MltModeContract(180, (60,60,60), width=1920, height=1080, scene_count=3),
    "pcm": MltModeContract(33, (32, 1)),
    "video-only": MltModeContract(33, (32, 1)),
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
    """Return readback metadata; execution and publication are admitted by MODES."""
    require(type(mode) is str and mode in MODE_CONTRACTS, "invalid_mlt_mode")
    return MODE_CONTRACTS[mode]


def expected_source_proof(mode: str) -> dict:
    contract = mode_contract(mode)
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
        "source_kind": "measured-voice",
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
    return proof


def verify_staged_source_receipt(receipt: dict, mode: str) -> str:
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


def verify_private_source(root: Path, mode: str, source_sha: str,
                          semwright_sha: str) -> dict[str, bytes]:
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
    return admitted





HYBRID_PUBLIC_FIELDS = {
    "schema","motionwright_sha","semwright_sha","project_sha256","master_sha256","visual_sha256",
    "source_wave_sha256","source_audio_kind","width","height","fps_num","fps_den","frame_count","source_segment_frames",
    "source_renderers","original_PNG_frames_bound","original_PNG_decoder_invocations","exact_original_PNG_reuses",
    "native_cut_and_content_screen","source_audio_content_screen","source_vs_output_differences_retained",
    "visual_exact_clock_observed","master_exact_clock_observed","pixel_observation_sha256","audio_observation_sha256",
    "original_source_reopened_unchanged","live_MLT_module_observed","native_effect_retried",
    "packet_PTS_and_AAC_priming_verified","Host_receiver_closure_proved","physical_acceptance","human_approval",
    "media_publication_permission_inferred",
}


def validate_hybrid_public_source(source, *, source_sha, semwright_sha):
    require(type(source) is dict and set(source)==HYBRID_PUBLIC_FIELDS
            and source["schema"]=="motionwright.native.hybrid-public-source/1"
            and source["motionwright_sha"]==source_sha and source["semwright_sha"]==semwright_sha
            and source["source_audio_kind"]=="first_party_synthetic_Film180_source",
            "hybrid_public_source_identity")
    for key in ("project_sha256","master_sha256","visual_sha256","source_wave_sha256",
                "pixel_observation_sha256","audio_observation_sha256"):
        require(type(source[key]) is str and re.fullmatch(r"[0-9a-f]{64}",source[key]) is not None,
                "hybrid_public_source_digest")
    for key,value in {"width":1920,"height":1080,"fps_num":30,"fps_den":1,"frame_count":180,
                      "original_PNG_frames_bound":540}.items():
        require(type(source[key]) is int and source[key]==value,"hybrid_public_original_clock")
    require(source["source_segment_frames"]==[60,60,60]
            and source["source_renderers"]==["motion_canvas","manim_static","motion_canvas"]
            and type(source["original_PNG_decoder_invocations"]) is int
            and 1<=source["original_PNG_decoder_invocations"]<=540
            and type(source["exact_original_PNG_reuses"]) is int and source["exact_original_PNG_reuses"]>=0
            and source["original_PNG_decoder_invocations"]+source["exact_original_PNG_reuses"]==540,
            "hybrid_public_original_frame_coverage")
    require(source["native_cut_and_content_screen"]==source["source_audio_content_screen"]=="PASS"
            and all(source[k] is True for k in ("source_vs_output_differences_retained","original_source_reopened_unchanged","live_MLT_module_observed"))
            and all(type(source[k]) is bool for k in ("visual_exact_clock_observed","master_exact_clock_observed"))
            and all(source[k] is False for k in ("native_effect_retried","packet_PTS_and_AAC_priming_verified",
                "Host_receiver_closure_proved","physical_acceptance","human_approval","media_publication_permission_inferred")),
            "hybrid_public_scope_escalation")
    return source


def publish_hybrid_observation(root,output,source_sha,semwright_sha,job_status):
    """Publish only this closed summary. Original audio/media/errors stay private."""
    require("hybrid-av" in MODES, "invalid_mlt_mode")
    require(job_status in VALID_STATUS and bool(SHA40.fullmatch(source_sha))
            and bool(SHA40.fullmatch(semwright_sha)),"hybrid_public_context")
    require(not output.exists(),"public_artifact_already_exists")
    src,dst=root.resolve(),output.resolve()
    require(src!=dst and src not in dst.parents and dst not in src.parents,"public_root_overlaps_private_source")
    source=None
    if job_status=="success":
        source=validate_hybrid_public_source(strict_object(read_bounded(root,"hybrid-public-source.json",16384)),
                                            source_sha=source_sha,semwright_sha=semwright_sha)
        for name,key in (("hybrid-pixel-observation.json","pixel_observation_sha256"),
                         ("hybrid-audio-content.json","audio_observation_sha256")):
            require(file_sha(read_bounded(root,name,16*1024*1024))==source[key],"hybrid_public_private_observation_changed")
    receipt={"schema":"motionwright.native.hybrid-public-evidence/1","mode":"hybrid-av",
        "motionwright_source_sha":source_sha,"semwright_source_pin":semwright_sha,
        "ci_native_mlt_evidence":"READBACK_OBSERVED" if source is not None else "NOT_VERIFIED",
        "native_video_frames":180 if source is not None else None,
        "retained_source_audio":None,
        "source_audio_kind":None if source is None else source["source_audio_kind"],
        "real_voice_acceptance_claimed":False,
        "frame_count_scope":"three_original_60frame_native_contributions",
        "native_cut_and_content_screen":None if source is None else source["native_cut_and_content_screen"],
        "source_audio_content_screen":None if source is None else source["source_audio_content_screen"],
        "visual_exact_clock_observed":None if source is None else source["visual_exact_clock_observed"],
        "master_exact_clock_observed":None if source is None else source["master_exact_clock_observed"],
        "source_vs_output_differences_retained":source is not None,
        "packet_PTS_and_AAC_priming_verified":False,"Host_receiver_closure_proved":False,
        "physical_acceptance":False,"human_approval":False,"v01_release_authorized":False,
        "source_audio_or_media_uploaded":False,"private_errors_or_original_receipts_uploaded":False,
        "native_mutations_replayed_by_publisher":False,"included_files":[]}
    output.mkdir(parents=True,exist_ok=False)
    with (output/"public-evidence.json").open("x",encoding="utf-8") as stream:
        json.dump(receipt,stream,sort_keys=True,indent=2);stream.write("\n")
    return receipt


def publish_mlt(root: Path, output: Path, mode: str, source_sha: str,
                semwright_sha: str, job_status: str) -> dict:
    require(mode in MODES, "invalid_mlt_mode")
    if mode == "hybrid-av":
        return publish_hybrid_observation(root,output,source_sha,semwright_sha,job_status)
    require(job_status in VALID_STATUS, "invalid_ci_job_status")
    require(bool(SHA40.fullmatch(source_sha))
            and bool(SHA40.fullmatch(semwright_sha)), "invalid_source_pin")
    require(not output.exists(), "public_artifact_already_exists")
    src = root.resolve()
    dst = output.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "public_root_overlaps_private_source")
    files = (verify_private_source(root, mode, source_sha, semwright_sha)
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
    }
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
