"""Adversarial v0.1 MLT public artifact admission tests. No native execution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_public_artifact as pub  # noqa: E402

MW = "a" * 40
SW = "b" * 40


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        video_only = mode != "pcm"
        mkv = ("native-two-segment-video-only.mkv" if video_only
               else "native-two-segment-lossless.mkv")
        payload = b"\x1a\x45\xdf\xa3" + b"first-party-multisegment-source-" + mode.encode()
        (self.root / mkv).write_bytes(payload)
        timeline = {
            "frame_count": 33,
            "native_frame_count_observed": 33 if video_only else None,
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
            "assembled_video_frames": 33,
            "rendered_source_frames": [32, 1],
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

    def test_workflow_uploads_only_staged_public_artifact(self):
        workflow = (Path(__file__).resolve().parents[2]
                    / ".github/workflows/native-mlt-sequence-e2e.yml")
        source = workflow.read_text(encoding="utf-8")
        self.assertIn("path: verification/native-mlt-sequence-public", source)
        self.assertNotIn("path: verification/native-mlt-sequence-e2e\n", source)
        self.assertIn("test_native_mlt_public_artifact.py", source)
        self.assertIn("NATIVE_CI_JOB_STATUS: ${{ job.status }}", source)


if __name__ == "__main__":
    unittest.main()
