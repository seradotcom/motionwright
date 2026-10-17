"""Adversarial v0.1 MLT public artifact admission tests. No native execution."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_public_artifact as pub  # noqa: E402

MW = "a" * 40
SW = "b" * 40
SOURCE_SHA = "66aeaa3aa6fff53834ab4003671ce79068bcbd6f631609f0f988b3ac9744ebe9"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def synthetic_voice_wav() -> bytes:
    # The publisher never regenerates media; this independent test oracle does.
    pcm = b"".join(
        struct.pack("<hh", ((i % 120) - 60) * 250, ((i % 80) - 40) * 260)
        for i in range(52800)
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(pcm)
    return output.getvalue()


def unit_unmixed_wav() -> bytes:
    """Unmixed zero PCM for admission plumbing tests, never a calibration oracle."""
    pcm = bytes(96000 * 4)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE",
        b"fmt ", 16, 1, 2, 48000, 192000, 4, 16, b"data", len(pcm),
    )
    return header + pcm


@contextmanager
def unit_original_mix_contract():
    # The real fifth-mode positive is the unmocked native CI run. This explicitly
    # scoped unit double tests routing, custody and projection without a mixer.
    data = unit_unmixed_wav()
    wav_sha, pcm_sha = digest(data), digest(data[44:])
    contract = pub.MltModeContract(
        60, (32, 28), source_fixture_id=pub.ORIGINAL_MIX_FIXTURE,
        source_wav_sha256=wav_sha,
    )
    with patch.object(pub, "ORIGINAL_MIX_WAV_SHA256", wav_sha), \
         patch.object(pub, "ORIGINAL_MIX_PCM_SHA256", pcm_sha), \
         patch.dict(pub.MODE_CONTRACTS, {"original-mix-mp4": contract}):
        yield data


def unit_original_mix_project() -> dict:
    return {
        "project_id": "00000000-0000-0000-0000-000000000011",
        "generation": "00000000-0000-0000-0000-000000000012", "revision": 7,
        "master_profile_id": "00000000-0000-0000-0000-000000000013",
        "resource": "motionwright:project:00000000-0000-0000-0000-000000000011",
    }


def unit_original_mix_receipt(project: dict, master_sha: str, master_bytes: int,
                              video_sha: str) -> dict:
    source = {
        "project_resource": project["resource"], "generation": project["generation"],
        "revision": project["revision"], "deliverable_id": project["master_profile_id"],
        "frame_count": 60, "fps_num": 30, "fps_den": 1,
        "staged_wav_sha256": pub.ORIGINAL_MIX_WAV_SHA256, "staged_wav_bytes": 384044,
        "semantic_sfx": None, "evidence_scope": pub.ORIGINAL_MIX_SOURCE_SCOPE,
        "original_mix": {
            "frames": 96000, "sample_rate": 48000, "channels": 2,
            "source_plan_sha256": "f" * 64, "rendered_pcm_sha256": pub.ORIGINAL_MIX_PCM_SHA256,
            "measured_music_duck_windows": 2, "owner_release_approved": False,
            "listening_intelligibility_approved": False, "independently_verified_lufs": False,
            "independently_verified_true_peak": False,
        },
    }
    for name, (wav_sha, pcm_sha) in pub.ORIGINAL_MIX_STEMS.items():
        source[name] = {"asset_id": "00000000-0000-0000-0000-000000000099",
                        "source_asset_sha256": wav_sha, "decoded_pcm_sha256": pcm_sha,
                        "sample_frames": 96000}
    return {
        "project_resource": project["resource"], "generation": project["generation"],
        "revision": project["revision"], "deliverable_id": project["master_profile_id"],
        "frame_count": 60, "evidence_scope": pub.ORIGINAL_MIX_SCOPE, "original_mix": source,
        "staged_audio": {
            "relative_path": pub.original_mix_source_name(project, "f" * 64),
            "sha256": pub.ORIGINAL_MIX_WAV_SHA256, "sample_rate": 48000, "channels": 2,
        },
        "measured_voice": {
            "audio_sha256": pub.ORIGINAL_MIX_STEMS["measured_voice"][0],
            "audio_pcm_frames": 96000, "audio_size_bytes": 384044,
            "audio_sample_rate": 48000, "audio_channels": 2, "total_frames": 60,
            "fps_num": 30, "fps_den": 1,
        },
        "canonical_dependency": {
            "project_generation": project["generation"], "project_revision": project["revision"],
            "profile_id": project["master_profile_id"],
        },
        "canonical_film_options_sha256": "1" * 64,
        "canonical_motion_source": [
            {"segment_id": f"segment-{index}", "film_sha256": "4" * 64, "plan_ref": "5" * 64,
             "native_fingerprint": "6" * 64, "connection_identity": "7" * 64,
             "provider_generation": 1, "completed_receipt_ids": [
                f"00000000-0000-0000-0000-0000000000{i + shift:02d}" for i in range(5)
             ], "native_job_ref": f"PRIVATE-NATIVE-ORIGINAL-JOB-{index}"}
            for index, shift in enumerate((20, 30))
        ],
        "video": {
            "project_resource": project["resource"], "generation": project["generation"],
            "revision": project["revision"], "deliverable_id": project["master_profile_id"],
            "frame_count": 60, "artifact_sha256": video_sha,
            "source": {
                "project_resource": project["resource"], "generation": project["generation"],
                "revision": project["revision"], "deliverable_id": project["master_profile_id"],
                "total_frames": 60, "fps_num": 30, "fps_den": 1,
                "verified_video_segments": [
                    {"segment_id": f"segment-{index}", "frame_count": frames,
                     "output_start_frame": start,
                     "native_render_job_ref": f"PRIVATE-NATIVE-ORIGINAL-JOB-{index}"}
                    for index, (frames, start) in enumerate(((32, 0), (28, 32)))
                ],
            },
        },
        "master": {
            "audio_sha256": pub.ORIGINAL_MIX_WAV_SHA256, "video_sha256": video_sha,
            "artifact": {"root": "output", "path": pub.original_mix_master_name(project),
                         "sha256": master_sha, "bytes": master_bytes},
        },
        "private_provider_payload": "PRIVATE-ORIGINAL-MIX-RECEIPT",
    }


def unit_original_mix_readback(master_sha: str) -> dict:
    limits = vars(pub.ContentLimits())
    windows = []
    for channel in (0, 1):
        for start in range(0, 96000, limits["block_samples"]):
            end = min(96000, start + limits["block_samples"])
            actual_start = max(0, end - limits["block_samples"]) if end == 96000 else start
            windows.append({
                "channel": channel, "start_sample": actual_start, "end_exclusive": end,
                "silent_reference": False, "correlation": 1.0, "gain": 1.0, "relative_error": 0.0,
            })
    return {
        "schema": "motionwright.native-original-mix-readback/1",
        "technical_content_readback": "PASS", "reference_wav_sha256": pub.ORIGINAL_MIX_WAV_SHA256,
        "final_mp4_sha256": master_sha, "listening_approval": False, "rights_approval": False,
        "editable_source_reopen_verified": False, "independent_loudness_verified": False,
        "clock": {
            "video_frames": 60, "video_packets": 60, "audio_duration_samples": 96000,
            "first_video_pts": 0, "first_audio_pts": 0, "video_fps": "30", "audio_timebase": "1/48000",
            "exact_contiguous_frame_timestamps": True, "audio_packet_presentation_end_samples": 96000,
            "packet_priming_and_presentation_checked": True, "audio_frames": 94, "audio_packets": 95,
            "decoded_audio_samples_from_frames": 96256, "priming_skip_samples": 1024,
            "discard_padding_samples": 0, "final_audio_packet_duration_samples": 768,
            "declared_decoder_suffix_budget_samples": 256,
        },
        "content": {
            "reference_sample_frames": 96000, "decoded_sample_frames": 96256,
            "decoded_access_unit_suffix_samples": 256, "reference_pcm_sha256": pub.ORIGINAL_MIX_PCM_SHA256,
            "decoded_pcm_sha256": "c" * 64, "logical_interval": [0, 96000],
            "all_logical_samples_compared": True, "limits": limits, "windows": windows,
            "short_energy_windows": [
                {"channel": channel, "start_sample": 0, "end_exclusive": 64,
                 "reference_rms": 1000.0, "gain": 1.0} for channel in (0, 1)
            ],
            "alignment": [
                {"channel": channel, "best_lag_samples": 0, "searched_lag_samples": 1600,
                 "fingerprint_positions": 512, "search_interval_samples": [-1600, 1600],
                 "zero_lag_margin": 0.001, "reference_zero_lag_margin": 0.001,
                 "absolute_delay_absence_proven": False, "aligned_or_shifted_output": False}
                for channel in (0, 1)
            ],
        },
    }


def unit_original_mix_provenance(root: Path) -> dict:
    sources = {name: {"bytes": 1, "sha256": "d" * 64} for name in pub.ORIGINAL_MIX_SOURCE_PATHS}
    sources["tooling/native_mlt_mix_readback.py"]["sha256"] = pub.ORIGINAL_MIX_READER_SHA256
    sources["tooling/tests/test_native_mlt_mix_readback.py"]["sha256"] = pub.ORIGINAL_MIX_READER_TEST_SHA256
    snapshot = {
        "motionwright_sha": MW, "motionwright_tree": "1" * 40,
        "semwright_sha": SW, "semwright_tree": "2" * 40,
        "semwright_declared_version": "0.0.0", "tracked_worktrees_clean": True,
        "source_files": sources,
        "tools": {name: {"path": str((root / name).resolve()), "bytes": 1, "sha256": "e" * 64}
                  for name in pub.ORIGINAL_MIX_TOOL_NAMES},
        "tool_versions": {name: "PRIVATE-VERSION-SOURCE" for name in pub.ORIGINAL_MIX_VERSION_NAMES},
        "native_runtime_versions_sha256": "3" * 64,
        "real_aac_test_gate": {
            "schema": "motionwright.native-original-mix-real-aac-tests/1", "motionwright_sha": MW,
            "reader_sha256": pub.ORIGINAL_MIX_READER_SHA256,
            "reader_tests_sha256": pub.ORIGINAL_MIX_READER_TEST_SHA256,
            "test_ids": sorted(pub.ORIGINAL_MIX_AAC_TEST_IDS),
            "tests_run": 4, "skipped": 0, "failures": 0, "errors": 0,
        },
    }
    return {"schema": "motionwright.native-original-mix-provenance/1",
            "run_id": "123", "run_attempt": "1", "same_inputs": True,
            "source_before": snapshot, "source_after": copy.deepcopy(snapshot)}


class MltPublicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mw-mlt-ci-public-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.root.mkdir()
        self.out = Path(self.temp.name) / "public"
        self.mode = "pcm"
        self.result = {}

    def fixture(self, mode: str = "pcm") -> None:
        self.mode = mode
        contract = pub.mode_contract(mode)
        video_only = mode != "pcm"
        mkv = ("native-two-segment-video-only.mkv" if video_only
               else "native-two-segment-lossless.mkv")
        payload = b"\x1a\x45\xdf\xa3" + b"first-party-multisegment-source-" + mode.encode()
        (self.root / mkv).write_bytes(payload)
        timeline = {
            "frame_count": contract.video_frames,
            "native_frame_count_observed": contract.video_frames if video_only else None,
            "transport_pcm_audio": not video_only,
            "native_render_profile": "lossless-video-only" if video_only else "lossless",
            "provider_project_cleanup": pub.NO_CLEANUP,
            "evidence_scope": pub.SCOPES[mode],
            "native_job_ref": "PRIVATE-NATIVE-JOB-EXCLUDED",
        }
        (self.root / "native-multisegment-timeline-evidence.json").write_text(
            json.dumps(timeline), encoding="utf-8")
        inventory = {
            "schema": "motionwright.v01-mlt-cleanup-inventory/1",
            "source_receipts_scanned": 4,
            "confirmed_native_creations_current_revision": 1,
            "complete_source_receipt_snapshot": True,
            "unconfirmed_or_invalid_creation_requests": 0,
            "observed_close_request_identities": 0,
            "disposition": "project_lifetime_unverified_foreground_consent_required",
            "native_provider_liveness_verified": False,
            "foreground_cleanup_consent_obtained": False,
            "verified_native_project_cleanup": False,
            "automatic_destructive_cleanup_allowed": False,
            "project_id": "PRIVATE-PROJECT-ID-EXCLUDED",
            "generation": "PRIVATE-GENERATION-ID-EXCLUDED",
            "revision": 0,
        }
        inventory_blob = json.dumps(inventory).encode()
        (self.root / "native-mlt-lifetime-inventory.json").write_bytes(inventory_blob)
        self.result = {
            "native_mlt_multisegment_e2e": "PASS",
            "motionwright_sha": MW,
            "semwright_sha": SW,
            "mode": mode,
            "assembled_video_frames": contract.video_frames,
            "rendered_source_frames": list(contract.source_segment_frames),
            "assembled_video_codec": "ffv1",
            "assembled_video_has_audio": not video_only,
            "transport_audio_codec": None if video_only else "pcm_s16le",
            "native_render_profile": "lossless-video-only" if video_only else "lossless",
            "provider_project_cleanup": pub.NO_CLEANUP,
            "native_mlt_cleanup_verified": False,
            "evidence_scope": pub.SCOPES[mode],
            "video_sha256": digest(payload),
            "video_bytes": len(payload),
            "native_mlt_lifetime_inventory_sha256": digest(inventory_blob),
            "native_mlt_lifetime_confirmed_current_creations": 1,
            "native_mlt_lifetime_receipts_scanned": 4,
            "native_mlt_lifetime_close_requests_observed": 0,
            "project": {
                "project_id": "PRIVATE-PROJECT-ID-EXCLUDED",
                "generation": "PRIVATE-GENERATION-ID-EXCLUDED",
                "revision": 0,
            },
        }
        if mode in ("final-mp4", "captioned-mp4"):
            mp4 = b"\x00\x00\x00\x18ftypisom" + b"source-bound-av-master"
            (self.root / "native-multisegment-master.mp4").write_bytes(mp4)
            (self.root / "decoded-stereo-content-proof.json").write_text(
                json.dumps({
                    "private_source": "PRIVATE-NATIVE-PCM-EXCLUDED",
                    "frame_count": 33,
                    "decoded_stereo_content": "distinct_source_400hz_left_600hz_right",
                    "decoded_master_pcm_frames": 53248,
                }),
                encoding="utf-8",
            )
            self.result.update({
                "final_master_sha256": digest(mp4),
                "final_master_bytes": len(mp4),
                "final_master_frames": 33,
                "final_master_video_codec": "h264",
                "final_master_audio_codec": "aac",
                "final_master_audio_rate": 48000,
                "final_master_audio_channels": 2,
                "decoded_stereo_content": "distinct_source_400hz_left_600hz_right",
                "measured_voice_pcm_frames": 52800,
                "decoded_master_pcm_frames": 53248,
                "final_evidence_scope": (
                    pub.CAPTIONED_SCOPE if mode == "captioned-mp4" else pub.FINAL_SCOPE
                ),
            })
            source = synthetic_voice_wav()
            self.assertEqual(digest(source), SOURCE_SHA)
            (self.root / "native-master-source.wav").write_bytes(source)
            self.result["retained_source_audio"] = {
                "schema": "motionwright.synthetic-native-source-wav/1",
                "mode": mode,
                "fixture_id": "motionwright.native-mlt-ci.stereo-400-600/1",
                "name": "native-master-source.wav",
                "sha256": SOURCE_SHA, "bytes": 211244,
                "source_kind": "measured-voice", "video_frames": 33,
                "fps_num": 30, "fps_den": 1, "pcm_frames": 52800,
                "sample_rate": 48000, "channels": 2, "bits_per_sample": 16,
                "synthetic_fixture_verified": True,
                "full_master_reference_readback": "NOT_RUN",
            }
            receipt = {
                "frame_count": 33, "evidence_scope": self.result["final_evidence_scope"],
                "staged_audio": {
                    "relative_path": "mw-ci-source-" + SOURCE_SHA[:16] + ".wav",
                    "sha256": SOURCE_SHA, "sample_rate": 48000, "channels": 2,
                },
                "measured_voice": {
                    "audio_sha256": SOURCE_SHA, "audio_pcm_frames": 52800,
                    "audio_size_bytes": 211244, "audio_sample_rate": 48000,
                    "audio_channels": 2, "total_frames": 33, "fps_num": 30, "fps_den": 1,
                    "caption_cue_count": 2 if mode == "captioned-mp4" else None,
                },
                "private_provider_payload": "PRIVATE-SOURCE-RECEIPT-EXCLUDED",
            }
            (self.root / "native-multisegment-av-master-evidence.json").write_text(
                json.dumps(receipt), encoding="utf-8",
            )
        if mode == "original-mix-mp4":
            # This path is used only inside unit_original_mix_contract().
            # It is deliberately not media generated by the native provider.
            project = unit_original_mix_project()
            self.result["project"] = project
            inventory.update(project_id=project["project_id"], generation=project["generation"],
                             revision=project["revision"])
            inventory_blob = json.dumps(inventory).encode()
            (self.root / "native-mlt-lifetime-inventory.json").write_bytes(inventory_blob)
            self.result["native_mlt_lifetime_inventory_sha256"] = digest(inventory_blob)
            mp4 = b"\x00\x00\x00\x18ftypisom" + b"unit-original-mix-routing"
            (self.root / "native-multisegment-master.mp4").write_bytes(mp4)
            source = unit_unmixed_wav()
            (self.root / pub.SOURCE_WAV_NAME).write_bytes(source)
            native = unit_original_mix_receipt(project, digest(mp4), len(mp4), self.result["video_sha256"])
            (self.root / pub.ORIGINAL_MIX_RECEIPT).write_text(json.dumps(native), encoding="utf-8")
            readback_blob = json.dumps(unit_original_mix_readback(digest(mp4))).encode()
            provenance_blob = json.dumps(unit_original_mix_provenance(self.root)).encode()
            (self.root / pub.ORIGINAL_MIX_READBACK).write_bytes(readback_blob)
            (self.root / pub.ORIGINAL_MIX_PROVENANCE).write_bytes(provenance_blob)
            self.result.update({
                "final_master_sha256": digest(mp4), "final_master_bytes": len(mp4),
                "final_master_frames": 60, "final_master_video_codec": "h264",
                "final_master_audio_codec": "aac", "final_master_audio_rate": 48000,
                "final_master_audio_channels": 2, "original_mix_pcm_frames": 96000,
                "final_evidence_scope": pub.ORIGINAL_MIX_SCOPE, "canonical_original_mix_requested": True,
                "retained_source_audio": pub.synthetic_source_proof(source, mode),
                "canonical_mix_source_plan_sha256": "f" * 64,
                "canonical_mix_decoded_source_pcm_sha256": pub.ORIGINAL_MIX_PCM_SHA256,
                "canonical_dependency": native["canonical_dependency"],
                "original_mix_native_receipt_sha256": digest((self.root / pub.ORIGINAL_MIX_RECEIPT).read_bytes()),
                "original_mix_readback_sha256": digest(readback_blob),
                "original_mix_provenance_sha256": digest(provenance_blob),
                **{field: "e" * 64 for field in (
                    "motion_canvas_driver_sha256", "mlt_driver_sha256", "mlt_runner_sha256",
                    "melt_sha256", "ffmpeg_sha256", "ffprobe_sha256",
                )},
            })
        if mode == "captioned-mp4":
            self.result.update({
                "captioned_master_decoded_pixels": "PASS",
                "captioned_source_cue_count": 2,
                "native_caption_mask_pixels": {"15": 999, "32": 800},
                "native_captioned_human_legibility": "NOT_RUN",
            })
            (self.root / "captioned-master-pixel-evidence.json").write_text(
                json.dumps({
                    "schema": "motionwright-v05-native-captioned-master-pixels/1",
                    "captioned_master_decoded_pixels": "PASS",
                    "motionwright_source_sha": MW,
                    "semwright_source_sha": SW,
                    "original_native_ffv1_frame_indices": [0, 14, 15, 31, 32],
                    "decoded_h264_frame_indices": [0, 15, 32],
                    "native_cue_count": 2,
                    "original_ffv1_sha256": self.result["video_sha256"],
                    "actual_master_mp4_sha256": self.result["final_master_sha256"],
                    "human_readability": "NOT_RUN",
                    "commercial_release_authorized": False,
                    "original_owner_media_or_transcript_published": False,
                    "active_caption_frames": {
                        "15": {"native_caption_mask_pixels": 999},
                        "32": {"native_caption_mask_pixels": 800},
                    },
                    "original_source_owner_path": "PRIVATE-NATIVE-SOURCE-EXCLUDED",
                }),
                encoding="utf-8",
            )
        self.store()
        for private in ("semwrightd.log", "run.log", "owner-session.json",
                        "native-provider-lifecycle.json"):
            (self.root / private).write_text(
                "secret owner token=/private/session/path", encoding="utf-8",
            )

    def store(self):
        (self.root / "result.json").write_text(
            json.dumps(self.result), encoding="utf-8"
        )

    def refused(self, code):
        with self.assertRaises(pub.PublicationError) as raised:
            pub.publish_mlt(self.root, self.out, self.mode, MW, SW, "success")
        self.assertEqual(raised.exception.code, code)
        self.assertFalse(self.out.exists())

    def test_four_modes_have_exact_media_allowlist_and_hashes(self):
        for mode in ("pcm", "video-only", "final-mp4", "captioned-mp4"):
            with self.subTest(mode=mode):
                self.fixture(mode)
                out = self.out.with_name("published-" + mode)
                receipt = pub.publish_mlt(self.root, out, mode, MW, SW, "success")
                self.assertEqual(receipt["ci_native_mlt_evidence"], "PASS")
                self.assertEqual(receipt["native_video_frames"], 33)
                self.assertFalse(receipt["v01_release_authorized"])
                self.assertFalse(receipt["verified_native_project_cleanup"])
                self.assertFalse(receipt["foreground_cleanup_consent_obtained"])
                self.assertFalse(receipt["raw_provider_and_daemon_logs_uploaded"])
                self.assertFalse(receipt["original_native_project_receipts_uploaded"])
                expected = {"public-evidence.json", (
                    "native-two-segment-lossless.mkv" if mode == "pcm"
                    else "native-two-segment-video-only.mkv"
                )}
                if mode in ("final-mp4", "captioned-mp4"):
                    expected.add("native-multisegment-master.mp4")
                    expected.add("native-master-source.wav")
                    retained = receipt["retained_source_audio"]
                    self.assertEqual(retained["sha256"], SOURCE_SHA)
                    self.assertEqual(retained["bytes"], 211244)
                    self.assertEqual(retained["full_master_reference_readback"], "NOT_RUN")
                else:
                    self.assertIsNone(receipt["retained_source_audio"])
                self.assertEqual(
                    receipt["native_captioned_h264_decoded_pixels"],
                    "PASS" if mode == "captioned-mp4" else "NOT_VERIFIED",
                )
                self.assertEqual(receipt["human_caption_readability"], "NOT_RUN")
                self.assertEqual({p.name for p in out.iterdir()}, expected)
                for item in receipt["included_files"]:
                    data = (out / item["name"]).read_bytes()
                    self.assertEqual(digest(data), item["sha256"])
                    self.assertEqual(len(data), item["bytes"])
                self.assertNotIn("PRIVATE-",
                                 (out / "public-evidence.json").read_text())

    def test_fail_cancel_and_skip_are_receipt_only_even_without_root(self):
        for mode in pub.MODES:
            for status in ("failure", "cancelled", "skipped"):
                with self.subTest(mode=mode, status=status):
                    out = self.out.with_name("no-media-" + mode + "-" + status)
                    receipt = pub.publish_mlt(
                        self.root / "never-created", out, mode, MW, SW, status
                    )
                    self.assertEqual(receipt["ci_native_mlt_evidence"], "NOT_VERIFIED")
                    self.assertIsNone(receipt["native_video_frames"])
                    self.assertIsNone(receipt["retained_source_audio"])
                    self.assertEqual([p.name for p in out.iterdir()],
                                     ["public-evidence.json"])

    def test_foreign_mode_or_source_is_rejected(self):
        self.fixture("pcm")
        self.result["mode"] = "video-only"
        self.store()
        self.refused("native_mlt_source_not_verified")
        self.result["mode"] = "pcm"
        self.result["semwright_sha"] = "f" * 40
        self.store()
        self.refused("native_mlt_source_not_verified")

    def test_invalid_status_and_source_pin_are_rejected_before_output(self):
        with self.assertRaises(pub.PublicationError):
            pub.publish_mlt(self.root, self.out, "pcm", "notsha", SW, "failure")
        with self.assertRaises(pub.PublicationError):
            pub.publish_mlt(self.root, self.out, "pcm", MW, SW, "pending")
        self.assertFalse(self.out.exists())

    def test_corrupted_lossless_mkv_is_rejected(self):
        self.fixture("pcm")
        (self.root / "native-two-segment-lossless.mkv").write_bytes(
            b"not-a-valid-ebml-file"
        )
        self.refused("invalid_native_mlt_container")

    def test_edited_media_with_valid_header_cannot_pass_sha(self):
        self.fixture("video-only")
        path = self.root / "native-two-segment-video-only.mkv"
        path.write_bytes(b"\x1a\x45\xdf\xa3" + b"forged media")
        self.refused("native_mlt_media_digest_mismatch")

    def test_invalid_source_receipt_and_final_audio_are_rejected(self):
        self.fixture("final-mp4")
        self.result["decoded_stereo_content"] = "unconfirmed"
        self.store()
        self.refused("native_mlt_master_evidence_invalid")

    def test_cancelled_original_provider_cleanup_cannot_be_forged(self):
        self.fixture("pcm")
        inventory_path = self.root / "native-mlt-lifetime-inventory.json"
        inventory = json.loads(inventory_path.read_text())
        inventory["verified_native_project_cleanup"] = True
        blob = json.dumps(inventory).encode()
        inventory_path.write_bytes(blob)
        self.result["native_mlt_lifetime_inventory_sha256"] = digest(blob)
        self.store()
        self.refused("mlt_cleanup_authority_unproven")

    def test_mismatched_inventory_project_scope_fails_even_with_matching_sha(self):
        self.fixture("pcm")
        path = self.root / "native-mlt-lifetime-inventory.json"
        inventory = json.loads(path.read_text())
        inventory["generation"] = "OTHER-GENERATION"
        content = json.dumps(inventory).encode()
        path.write_bytes(content)
        self.result["native_mlt_lifetime_inventory_sha256"] = digest(content)
        self.store()
        self.refused("inventory_project_scope_mismatch")

    def test_captioned_mp4_rejects_forged_or_missing_native_decoded_pixels(self):
        self.fixture("captioned-mp4")
        manifest = self.root / "captioned-master-pixel-evidence.json"
        original = json.loads(manifest.read_text())
        for changed in [
            {"captioned_master_decoded_pixels": "NOT_VERIFIED"},
            {"motionwright_source_sha": "f" * 40},
            {"actual_master_mp4_sha256": "b" * 64},
            {"human_readability": "PASS"},
            {"native_cue_count": 0},
        ]:
            with self.subTest(changed=changed):
                manifest.write_text(json.dumps({**original, **changed}))
                self.refused("native_captioned_master_decoded_pixels_not_verified")
        manifest.write_text(json.dumps(original))
        self.result["native_caption_mask_pixels"]["15"] = 3
        self.store()
        self.refused("native_captioned_master_decoded_pixels_not_verified")
        self.result["native_caption_mask_pixels"]["15"] = 999
        self.store()
        manifest.unlink()
        with self.assertRaises(pub.PublicationError):
            pub.publish_mlt(self.root, self.out, self.mode, MW, SW, "success")
        self.assertFalse(self.out.exists())

    def test_final_decoded_audio_proof_mismatch_rejected(self):
        self.fixture("final-mp4")
        path = self.root / "decoded-stereo-content-proof.json"
        stereo = json.loads(path.read_text())
        stereo["decoded_master_pcm_frames"] = 0
        path.write_text(json.dumps(stereo))
        self.refused("native_mlt_stereo_evidence_invalid")

    def test_malformed_or_duplicate_source_json_fails_closed(self):
        self.fixture("pcm")
        (self.root / "result.json").write_bytes(
            b'{"native_mlt_multisegment_e2e":"PASS","native_mlt_multisegment_e2e":"FAIL"}'
        )
        self.refused("duplicate_evidence_json_key")

    def test_missing_or_linked_media_is_not_uploaded(self):
        self.fixture("pcm")
        original = Path.is_symlink

        def synthetic_link(path: Path):
            return path == self.root / "native-two-segment-lossless.mkv" or original(path)

        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=synthetic_link):
            self.refused("missing_or_linked_public_source")

    def test_output_overlap_and_duplicate_upload_are_refused(self):
        self.fixture("pcm")
        with self.assertRaises(pub.PublicationError):
            pub.publish_mlt(self.root, self.root / "public", "pcm", MW, SW, "failure")
        pub.publish_mlt(self.root, self.out, "pcm", MW, SW, "success")
        with self.assertRaises(pub.PublicationError):
            pub.publish_mlt(self.root, self.out, "pcm", MW, SW, "success")


    def test_changed_wav_is_not_public_even_if_every_json_hash_is_rewritten(self):
        self.fixture("final-mp4")
        source = self.root / "native-master-source.wav"
        altered = source.read_bytes()[:-4] + b"\0\0\0\0"
        source.write_bytes(altered)
        forged_sha = digest(altered)
        self.result["retained_source_audio"]["sha256"] = forged_sha
        self.store()
        path = self.root / "native-multisegment-av-master-evidence.json"
        receipt = json.loads(path.read_text())
        receipt["staged_audio"]["sha256"] = forged_sha
        receipt["staged_audio"]["relative_path"] = "mw-ci-source-" + forged_sha[:16] + ".wav"
        receipt["measured_voice"]["audio_sha256"] = forged_sha
        path.write_text(json.dumps(receipt))
        self.refused("native_mlt_synthetic_source_identity_mismatch")

    def test_retained_source_metadata_is_closed_typed_and_clock_bound(self):
        mutations = (
            ("pcm_frames", 96000), ("pcm_frames", 52800.0),
            ("synthetic_fixture_verified", 1), ("mode", "original-mix-mp4"),
            ("full_master_reference_readback", "PASS"),
            ("private_path", "/tmp/private/session"),
        )
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                self.fixture("final-mp4")
                self.result["retained_source_audio"][key] = value
                self.store()
                self.refused("native_mlt_retained_source_metadata_mismatch")

    def test_retained_source_requires_the_native_staged_receipt(self):
        self.fixture("final-mp4")
        path = self.root / "native-multisegment-av-master-evidence.json"
        original = json.loads(path.read_text())
        mutations = (
            ("staged_audio", "relative_path", "../private.wav"),
            ("staged_audio", "sha256", "f" * 64),
            ("staged_audio", "sample_rate", 44100),
            ("staged_audio", "channels", 1),
            ("measured_voice", "audio_sha256", "e" * 64),
            ("measured_voice", "audio_pcm_frames", 96000),
            ("measured_voice", "audio_size_bytes", 384044),
            ("measured_voice", "total_frames", 60),
            ("measured_voice", "fps_num", 60),
        )
        for section, key, value in mutations:
            with self.subTest(section=section, key=key):
                receipt = copy.deepcopy(original)
                receipt[section][key] = value
                path.write_text(json.dumps(receipt))
                self.refused("native_mlt_source_wav_receipt_mismatch")
        for key, value in (("frame_count", 60), ("evidence_scope", "unverified-mix")):
            with self.subTest(key=key):
                receipt = copy.deepcopy(original)
                receipt[key] = value
                path.write_text(json.dumps(receipt))
                self.refused("native_mlt_source_wav_receipt_mismatch")
        path.unlink()
        with self.assertRaises(pub.PublicationError):
            pub.publish_mlt(self.root, self.out, self.mode, MW, SW, "success")
        self.assertFalse(self.out.exists())

    def test_missing_retained_source_wav_prevents_publication(self):
        self.fixture("final-mp4")
        (self.root / "native-master-source.wav").unlink()
        self.refused("native_mlt_source_wav_unreadable")

    def test_real_retained_source_symlink_prevents_publication(self):
        self.fixture("final-mp4")
        source = self.root / "native-master-source.wav"
        target = self.root / "not-an-admitted-public-filename.wav"
        source.replace(target)
        try:
            source.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("This host cannot create the real source symlink fixture")
        self.refused("native_mlt_source_wav_missing_or_linked")

    def test_unimplemented_audio_modes_are_not_admitted_by_failed_job_status(self):
        for mode in ("original-sfx-mp4",):
            with self.subTest(mode=mode):
                with self.assertRaises(pub.PublicationError) as raised:
                    pub.publish_mlt(self.root, self.out, mode, MW, SW, "failure")
                self.assertEqual(raised.exception.code, "invalid_mlt_mode")
                self.assertFalse(self.out.exists())

    def test_workflow_uploads_only_staged_public_artifact(self):
        workflow = (Path(__file__).resolve().parents[2]
                    / ".github/workflows/native-mlt-sequence-e2e.yml")
        source = workflow.read_text(encoding="utf-8")
        self.assertIn("path: verification/native-mlt-sequence-public", source)
        self.assertNotIn("path: verification/native-mlt-sequence-e2e\n", source)
        self.assertIn("test_native_mlt_public_artifact.py", source)
        self.assertIn("'tooling/tests/test_native_mlt_ci_audio.py'", source)
        self.assertIn("read_synthetic_source_wav(source_wav, mode)", source)
        self.assertIn("NATIVE_CI_JOB_STATUS: ${{ job.status }}", source)

    def test_original_mix_projection_is_closed_and_separate_from_source_custody(self):
        with unit_original_mix_contract():
            self.fixture("original-mix-mp4")
            source = json.loads((self.root / pub.ORIGINAL_MIX_RECEIPT).read_text())
            self.assertNotIn("caption_cue_count", source["measured_voice"])
            receipt = pub.publish_mlt(self.root, self.out, self.mode, MW, SW, "success")
            self.assertEqual({path.name for path in self.out.iterdir()}, {
                "native-two-segment-video-only.mkv", "native-multisegment-master.mp4",
                "native-master-source.wav", "public-evidence.json",
            })
            self.assertEqual(receipt["native_video_frames"], 60)
            self.assertEqual(receipt["retained_source_audio"]["source_kind"], "original-mix")
            self.assertEqual(receipt["retained_source_audio"]["pcm_frames"], 96000)
            self.assertEqual(receipt["retained_source_audio"]["full_master_reference_readback"], "NOT_RUN")
            proof = receipt["original_mix_readback"]
            self.assertEqual(proof["reference_wav_sha256"], digest(unit_unmixed_wav()))
            self.assertEqual(proof["final_mp4_sha256"], self.result["final_master_sha256"])
            self.assertEqual(proof["technical_content_readback"], "PASS")
            self.assertEqual(proof["window_count"], 94)
            self.assertEqual(proof["decoded_access_unit_suffix_samples"], 256)
            self.assertEqual(proof["clock"]["declared_decoder_suffix_budget_samples"], 256)
            self.assertEqual(proof["limits"], vars(pub.ContentLimits()))
            self.assertEqual(proof["source"]["real_aac_test_gate"]["tests_run"], 4)
            self.assertEqual(proof["source"]["real_aac_test_gate"]["skipped"], 0)
            self.assertFalse(proof["publisher_decoded_media"])
            self.assertTrue(all(item["absolute_delay_absence_proven"] is False
                                and item["aligned_or_shifted_output"] is False
                                for item in proof["alignment"]))
            text = (self.out / "public-evidence.json").read_text()
            self.assertNotIn("PRIVATE-", text)
            self.assertNotIn(str(self.root), text)
            for field in ("listening_approval", "rights_approval",
                          "editable_source_reopen_verified", "independent_loudness_verified"):
                self.assertIs(proof[field], False)

    def test_original_mix_measured_voice_rejects_any_present_caption_count(self):
        with unit_original_mix_contract():
            self.fixture("original-mix-mp4")
            path = self.root / pub.ORIGINAL_MIX_RECEIPT
            original = json.loads(path.read_text())
            self.assertNotIn("caption_cue_count", original["measured_voice"])
            output = self.out
            for index, value in enumerate((None, 0, 2, False, "0", "2")):
                with self.subTest(type=type(value).__name__, value=value):
                    altered = copy.deepcopy(original)
                    altered["measured_voice"]["caption_cue_count"] = value
                    path.write_text(json.dumps(altered))
                    self.result["original_mix_native_receipt_sha256"] = digest(path.read_bytes())
                    self.store()
                    # Separate outputs keep an unexpectedly admitted negative
                    # from making later cases fail only because output exists.
                    self.out = output.with_name(f"caption-count-{index}")
                    self.refused("native_mlt_original_mix_receipt_mismatch")

    def test_original_mix_measured_voice_requires_every_other_field(self):
        with unit_original_mix_contract():
            self.fixture("original-mix-mp4")
            path = self.root / pub.ORIGINAL_MIX_RECEIPT
            original = json.loads(path.read_text())
            self.assertNotIn("caption_cue_count", original["measured_voice"])
            self.assertEqual(
                pub.verify_original_mix_source_receipt(original, self.result["project"]),
                original["staged_audio"]["relative_path"],
            )
            wrong_values = {
                "audio_sha256": "0" * 64, "audio_size_bytes": 384045,
                "audio_pcm_frames": 95999, "audio_sample_rate": 44100,
                "audio_channels": 1, "total_frames": 59, "fps_num": 60, "fps_den": True,
            }
            output = self.out
            for field, wrong in wrong_values.items():
                for operation in ("missing", "corrupted"):
                    with self.subTest(field=field, operation=operation):
                        altered = copy.deepcopy(original)
                        if operation == "missing":
                            del altered["measured_voice"][field]
                        else:
                            altered["measured_voice"][field] = wrong
                        path.write_text(json.dumps(altered))
                        self.result["original_mix_native_receipt_sha256"] = digest(path.read_bytes())
                        self.store()
                        self.out = output.with_name(f"voice-field-{field}-{operation}")
                        self.refused("native_mlt_original_mix_receipt_mismatch")

    def test_real_calibrated_identity_rejects_the_unmixed_unit_double_and_voice(self):
        self.assertNotEqual(pub.ORIGINAL_MIX_WAV_SHA256, digest(unit_unmixed_wav()))
        self.assertNotEqual(pub.ORIGINAL_MIX_WAV_SHA256, SOURCE_SHA)
        self.assertNotEqual(pub.ORIGINAL_MIX_WAV_SHA256, pub.ORIGINAL_MIX_STEMS["measured_voice"][0])
        for data in (unit_unmixed_wav(), synthetic_voice_wav()):
            with self.subTest(size=len(data)), self.assertRaises(pub.PublicationError):
                pub.synthetic_source_proof(data, "original-mix-mp4")

    def test_original_mix_rejects_a_copied_legacy_clock(self):
        with unit_original_mix_contract():
            self.fixture("original-mix-mp4")
            self.result["assembled_video_frames"] = 33
            self.result["rendered_source_frames"] = [32, 1]
            self.store()
            self.refused("native_mlt_source_not_verified")

    def test_original_mix_source_and_native_authority_bindings_are_not_interchangeable(self):
        with unit_original_mix_contract():
            self.fixture("original-mix-mp4")
            path = self.root / pub.ORIGINAL_MIX_RECEIPT
            original = json.loads(path.read_text())
            changes = (
                (("staged_audio", "relative_path"), "../private.wav"),
                (("staged_audio", "sha256"), pub.ORIGINAL_MIX_STEMS["measured_voice"][0]),
                (("measured_voice", "audio_sha256"), pub.ORIGINAL_MIX_WAV_SHA256),
                (("original_mix", "staged_wav_sha256"), SOURCE_SHA),
                (("original_mix", "fps_num"), 60),
                (("original_mix", "fps_den"), True),
                (("original_mix", "measured_voice", "source_asset_sha256"), pub.ORIGINAL_MIX_WAV_SHA256),
                (("canonical_dependency", "project_revision"), 8),
                (("canonical_dependency", "project_generation"), "00000000-0000-0000-0000-000000000014"),
                (("master", "audio_sha256"), pub.ORIGINAL_MIX_STEMS["measured_voice"][0]),
                (("original_mix", "original_mix", "rendered_pcm_sha256"), "8" * 64),
                (("original_mix", "original_mix", "independently_verified_true_peak"), True),
            )
            for pointer, value in changes:
                with self.subTest(pointer=pointer):
                    altered = copy.deepcopy(original)
                    parent = altered
                    for key in pointer[:-1]:
                        parent = parent[key]
                    parent[pointer[-1]] = value
                    path.write_text(json.dumps(altered))
                    self.result["original_mix_native_receipt_sha256"] = digest(path.read_bytes())
                    self.store()
                    self.refused("native_mlt_original_mix_receipt_mismatch")
            for mutation in ("missing", "reversed", "duplicate", "cross-segment", "job"):
                with self.subTest(mutation=mutation):
                    altered = copy.deepcopy(original)
                    source = altered["canonical_motion_source"]
                    if mutation == "missing":
                        source[0]["completed_receipt_ids"].pop()
                    elif mutation == "reversed":
                        source[0]["completed_receipt_ids"].reverse()
                    elif mutation == "duplicate":
                        source[1]["completed_receipt_ids"] = source[0]["completed_receipt_ids"].copy()
                    elif mutation == "cross-segment":
                        source.reverse()
                    else:
                        source[0]["native_job_ref"] = source[1]["native_job_ref"]
                    path.write_text(json.dumps(altered))
                    self.result["original_mix_native_receipt_sha256"] = digest(path.read_bytes())
                    self.store()
                    self.refused("native_mlt_original_mix_receipt_mismatch")

    def test_original_mix_readback_refuses_rewritten_hashes_limits_and_tail_claims(self):
        with unit_original_mix_contract():
            self.fixture("original-mix-mp4")
            path = self.root / pub.ORIGINAL_MIX_READBACK
            original = json.loads(path.read_text())
            changes = (
                (("reference_wav_sha256",), SOURCE_SHA),
                (("final_mp4_sha256",), "8" * 64),
                (("content", "reference_pcm_sha256"), "9" * 64),
                (("content", "all_logical_samples_compared"), False),
                (("content", "limits", "minimum_correlation"), 0.96),
                (("content", "limits", "short_window_samples"), 128),
                (("content", "limits", "block_samples"), 2048.0),
                (("content", "limits", "minimum_short_window_rms"), 128),
                (("clock", "packet_priming_and_presentation_checked"), False),
                (("clock", "audio_packet_presentation_end_samples"), 95900),
                (("content", "decoded_access_unit_suffix_samples"), 100),
                (("content", "alignment", 0, "best_lag_samples"), 96),
                (("content", "alignment", 0, "best_lag_samples"), False),
                (("content", "alignment", 0, "absolute_delay_absence_proven"), True),
                (("content", "windows", 93, "end_exclusive"), 95900),
                (("content", "windows", 0, "relative_error"), 0.26),
                (("content", "windows", 0, "gain"), float("nan")),
                (("content", "short_energy_windows", 1, "gain"), 0.49),
                (("content", "logical_interval"), [False, 96000]),
                (("listening_approval",), True),
                (("rights_approval",), True),
                (("private_path",), "PRIVATE-EXCLUDED"),
            )
            for pointer, value in changes:
                with self.subTest(pointer=pointer):
                    altered = copy.deepcopy(original)
                    parent = altered
                    for key in pointer[:-1]:
                        parent = parent[key]
                    parent[pointer[-1]] = value
                    blob = json.dumps(altered).encode()
                    path.write_bytes(blob)
                    self.result["original_mix_readback_sha256"] = digest(blob)
                    self.store()
                    self.refused("native_mlt_original_mix_readback_invalid")

    def test_original_mix_needs_same_checkout_tools_reader_and_four_unskipped_aac_tests(self):
        with unit_original_mix_contract():
            self.fixture("original-mix-mp4")
            path = self.root / pub.ORIGINAL_MIX_PROVENANCE
            original = json.loads(path.read_text())
            changes = (
                (("motionwright_sha",), "c" * 40),
                (("source_files", "tooling/native_mlt_mix_readback.py", "sha256"), "7" * 64),
                (("real_aac_test_gate", "tests_run"), 3),
                (("real_aac_test_gate", "skipped"), 1),
                (("real_aac_test_gate", "failures"), 1),
                (("real_aac_test_gate", "test_ids"), list(pub.ORIGINAL_MIX_AAC_TEST_IDS[:3])),
                (("tracked_worktrees_clean",), False),
            )
            for pointer, value in changes:
                with self.subTest(pointer=pointer):
                    altered = copy.deepcopy(original)
                    for label in ("source_before", "source_after"):
                        parent = altered[label]
                        for key in pointer[:-1]:
                            parent = parent[key]
                        parent[pointer[-1]] = value
                    blob = json.dumps(altered).encode()
                    path.write_bytes(blob)
                    self.result["original_mix_provenance_sha256"] = digest(blob)
                    self.store()
                    self.refused("native_mlt_original_mix_provenance_invalid")
            altered = copy.deepcopy(original)
            altered["source_after"]["tools"]["ffmpeg"]["sha256"] = "8" * 64
            blob = json.dumps(altered).encode()
            path.write_bytes(blob)
            self.result["original_mix_provenance_sha256"] = digest(blob)
            self.store()
            self.refused("native_mlt_original_mix_provenance_invalid")
            path.write_text(json.dumps(original))
            self.result["original_mix_provenance_sha256"] = digest(path.read_bytes())
            self.result["ffmpeg_sha256"] = "8" * 64
            self.store()
            self.refused("native_mlt_original_mix_master_invalid")

    def test_original_mix_missing_or_oversized_readback_never_becomes_public(self):
        with unit_original_mix_contract():
            for missing in (pub.ORIGINAL_MIX_READBACK, pub.ORIGINAL_MIX_PROVENANCE,
                            pub.ORIGINAL_MIX_RECEIPT):
                with self.subTest(missing=missing):
                    self.fixture("original-mix-mp4")
                    (self.root / missing).unlink()
                    with self.assertRaises(pub.PublicationError):
                        pub.publish_mlt(self.root, self.out, self.mode, MW, SW, "success")
                    self.assertFalse(self.out.exists())
            self.fixture("original-mix-mp4")
            (self.root / pub.ORIGINAL_MIX_READBACK).write_bytes(
                b" " * (pub.READBACK_DOCUMENT_LIMIT + 1)
            )
            self.refused("native_mix_file_budget")

    def test_original_mix_failures_publish_no_media_even_when_custody_exists(self):
        with unit_original_mix_contract():
            self.fixture("original-mix-mp4")
            for status in ("failure", "cancelled", "skipped"):
                with self.subTest(status=status):
                    output = self.out.with_name("mixed-" + status)
                    receipt = pub.publish_mlt(self.root, output, self.mode, MW, SW, status)
                    self.assertEqual({path.name for path in output.iterdir()}, {"public-evidence.json"})
                    self.assertEqual(receipt["ci_native_mlt_evidence"], "NOT_VERIFIED")
                    self.assertNotIn("original_mix_readback", receipt)
                    self.assertIsNone(receipt["retained_source_audio"])
                    self.assertEqual((self.root / pub.SOURCE_WAV_NAME).read_bytes(), unit_unmixed_wav())

    def test_original_mix_changed_source_cannot_pass_by_rewriting_every_claim(self):
        with unit_original_mix_contract():
            self.fixture("original-mix-mp4")
            altered = unit_unmixed_wav()[:-4] + b"\x01\x00\x00\x00"
            (self.root / pub.SOURCE_WAV_NAME).write_bytes(altered)
            self.result["retained_source_audio"]["sha256"] = digest(altered)
            self.store()
            self.refused("native_mlt_synthetic_source_identity_mismatch")

    def test_fifth_mode_document_read_stops_at_initial_size_plus_one_on_real_growth(self):
        target = self.root / "growing.json"
        target.write_bytes(b"0123456789")
        requests = []
        original_fdopen = pub.os.fdopen

        class GrowingRead:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def fileno(self):
                return self.stream.fileno()
            def read(self, count):
                requests.append(count)
                with target.open("ab") as writer:
                    writer.write(b"X" * 4096)
                return self.stream.read(count)

        with patch.object(pub.os, "fdopen",
                          side_effect=lambda fd, mode: GrowingRead(original_fdopen(fd, mode))):
            with self.assertRaises(pub.PublicationError) as raised:
                pub.read_stable_native_file(target, 65536)
        self.assertEqual(raised.exception.code, "native_mix_file_changed")
        self.assertEqual(requests, [11])



if __name__ == "__main__":
    unittest.main()
