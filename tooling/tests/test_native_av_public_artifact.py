"""v0.1 native AV public artifact privacy and provenance regressions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_av_public_artifact as pub  # noqa: E402

SOURCE = "a" * 40
PIN = "b" * 40
AUDIO = "c" * 64


class NativeAVPublicArtifactTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory(prefix="mw-av-public-v01-")
        self.addCleanup(root.cleanup)
        self.private = Path(root.name) / "private"
        self.private.mkdir()
        self.public = Path(root.name) / "public"

        for name in pub.ALLOWED_MEDIA:
            if name.endswith(".png"):
                blob = b"\x89PNG\r\n\x1a\n" + b"verified fixture pixels"
            elif name.endswith(".mp4"):
                blob = b"\x00\x00\x00\x18ftypisom" + b"verified fixture master"
            elif name == "artifact-manifest.json":
                blob = b'{"schema":"safe-native-media-manifest","frames":60}'
            else:
                continue
            (self.private / name).write_bytes(blob)
        media_sha = hashlib.sha256(
            (self.private / "motionwright-master.mp4").read_bytes()
        ).hexdigest()
        manifest_sha = hashlib.sha256(
            (self.private / "artifact-manifest.json").read_bytes()
        ).hexdigest()
        proof = {
            "evidence_scope": "actual_decoded_h264_aac_two_second_fixture_only",
            "master_sha256": media_sha,
            "source_wav_sha256": AUDIO,
            "video_frames_decoded": 60,
            "video_movement_decoded": True,
        }
        (self.private / "decoded-media-proof.json").write_text(
            json.dumps(proof), encoding="utf-8"
        )
        self.result = {
            "native_av_master_e2e": "PASS",
            "motionwright_sha": SOURCE,
            "semwright_sha": PIN,
            "frame_count": 60,
            "audio_sha256": AUDIO,
            "artifact_manifest_sha256": manifest_sha,
            "master_sha256": media_sha,
            "artifact_directory": "/home/runner/private/user/video",
        }
        self.store_result()
        for name in ("semwrightd.log", "motionwright-render-evidence.json",
                     "provider-status-reconciliation.json",
                     "native-provider-lifecycle.json", "run.log"):
            (self.private / name).write_text(
                'session_file=/private/owner/SECRET_NATIVE_JOB',
                encoding="utf-8",
            )

    def store_result(self):
        (self.private / "result.json").write_text(
            json.dumps(self.result), encoding="utf-8"
        )

    def refused(self, code):
        with self.assertRaises(pub.PublicationError) as err:
            pub.publish_av(self.private, self.public, SOURCE, PIN, "success")
        self.assertEqual(err.exception.code, code)
        self.assertFalse(self.public.exists())

    def test_source_bound_success_contains_only_media_and_public_receipt(self):
        proof = pub.publish_av(self.private, self.public, SOURCE, PIN, "success")
        self.assertEqual(proof["ci_native_av_evidence"], "PASS")
        self.assertEqual(proof["native_video_frames"], 60)
        self.assertEqual(proof["installed_owner_acceptance"], "NOT_RUN")
        self.assertFalse(proof["v01_release_authorized"])
        self.assertFalse(proof["raw_daemon_or_provider_logs_uploaded"])
        self.assertFalse(proof["original_native_job_receipts_uploaded"])
        self.assertEqual(
            {p.name for p in self.public.iterdir()},
            set(pub.ALLOWED_MEDIA) | {"public-evidence.json"}
        )
        for file in proof["included_files"]:
            actual = (self.public / file["name"]).read_bytes()
            self.assertEqual(hashlib.sha256(actual).hexdigest(), file["sha256"])
            self.assertEqual(len(actual), file["bytes"])
        self.assertNotIn("SECRET_NATIVE_JOB",
                         (self.public / "public-evidence.json").read_text())

    def test_failed_or_cancelled_job_cannot_upload_private_receipts(self):
        for status in ("failure", "cancelled"):
            with self.subTest(status=status):
                folder = self.public.with_name("public-" + status)
                report = pub.publish_av(self.private, folder, SOURCE, PIN, status)
                self.assertEqual(report["ci_native_av_evidence"], "NOT_VERIFIED")
                self.assertIsNone(report["native_video_frames"])
                self.assertEqual([x.name for x in folder.iterdir()],
                                 ["public-evidence.json"])

    def test_changed_master_digest_rejected(self):
        (self.private / "motionwright-master.mp4").write_bytes(
            b"\x00\x00\x00\x18ftypisom" + b"DIFFERENT CONTENT"
        )
        self.refused("native_av_media_digest_mismatch")

    def test_missing_decoded_proof_rejected(self):
        (self.private / "decoded-media-proof.json").unlink()
        self.refused("missing_or_linked_public_source")

    def test_decoded_video_proof_cannot_forge_frames(self):
        proof = json.loads(
            (self.private / "decoded-media-proof.json").read_text()
        )
        proof["video_frames_decoded"] = 0
        (self.private / "decoded-media-proof.json").write_text(
            json.dumps(proof), encoding="utf-8"
        )
        self.refused("native_av_decoded_media_mismatch")

    def test_private_decoded_media_extra_field_refused(self):
        proof_path = self.private / "decoded-media-proof.json"
        proof = json.loads(proof_path.read_text())
        proof["job_ref"] = "secret-original-native-job"
        proof_path.write_text(json.dumps(proof), encoding="utf-8")
        self.refused("private_metadata_in_public_source")

    def test_invalid_native_mp4_container_rejected(self):
        (self.private / "motionwright-master.mp4").write_bytes(
            b"definitely-not-a-real-mp4-container"
        )
        self.refused("invalid_native_mp4")

    def test_bad_ci_source_identity_rejected(self):
        self.result["motionwright_sha"] = "d" * 40
        self.store_result()
        self.refused("native_av_not_verified")

    def test_refuses_private_source_manifest(self):
        (self.private / "artifact-manifest.json").write_text(
            '{"job_ref":"private-opaque-job"}', encoding="utf-8"
        )
        self.refused("private_metadata_in_public_source")

    def test_workflow_never_uploads_full_private_directory(self):
        file = Path(__file__).resolve().parents[2] / ".github/workflows/native-av-master-e2e.yml"
        workflow = file.read_text(encoding="utf-8")
        self.assertIn("path: verification/native-av-master-public", workflow)
        self.assertNotIn("path: verification/native-av-master-e2e\n", workflow)
        self.assertIn("test_native_av_public_artifact.py", workflow)

    def test_reuse_of_publication_root_fails_closed(self):
        pub.publish_av(self.private, self.public, SOURCE, PIN, "failure")
        with self.assertRaises(pub.PublicationError):
            pub.publish_av(self.private, self.public, SOURCE, PIN, "success")


if __name__ == "__main__":
    unittest.main()
