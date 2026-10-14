"""Adversarial native v0.1 CI public artifact tests; no renderer executed."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_render_public_artifact as pub  # noqa: E402

SOURCE = "a" * 40
SDK = "b" * 40
FILM = "c" * 64
RGBA = "d" * 64
DIRECT = "e" * 64


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


class NativeRenderPublicArtifactTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-v01-render-public-")
        self.addCleanup(temp.cleanup)
        self.private = Path(temp.name) / "private"
        self.private.mkdir()
        self.public = Path(temp.name) / "public"
        for name in pub.FILES:
            if name.endswith(".png"):
                blob = b"\x89PNG\r\n\x1a\n" + name.encode()
            else:
                blob = b'{"schema":"source-bound-manifest","frames":60}'
            (self.private / name).write_bytes(blob)
        self.result = {
            "native_render_e2e": "PASS", "motionwright_sha": SOURCE,
            "semwright_sha": SDK, "frame_count": 60,
            "linear_position_motion_sampled": True,
            "linear_opacity_motion_sampled": True,
            "direct_mediated_native_frame_parity": "PASS",
            "direct_mediated_same_film_sha256": FILM,
            "artifact_manifest_sha256": sha(
                (self.private / "artifact-manifest.json").read_bytes()
            ),
            "project": {"generation": "secret-owner-generation", "revision": 17},
            "review_frames": {
                label + "_sha256": sha((self.private / filename).read_bytes())
                for label, filename in (
                    ("first", "frame-first.png"), ("middle", "frame-middle.png"),
                    ("last", "frame-last.png"),
                )
            },
            "artifact_directory": "/private/user/location/do-not-copy",
        }
        self.parity = {
            "schema": "motionwright-native-direct-mediated-parity/1",
            "motionwright_sha": SOURCE, "semwright_sha": SDK,
            "source_film_sha256": FILM,
            "project_generation": "secret-owner-generation",
            "project_revision": 17,
            "direct_job_distinct_from_mediated": True,
            "direct_native_validation_checks": 7,
            "human_visual_acceptance": "NOT_RUN",
            "arbitrary_renderer_property_parity": "NOT_PROVEN",
            "native_manifest_sha256": {
                "direct": DIRECT,
                "mediated": self.result["artifact_manifest_sha256"],
            },
            "pixels": {
                "frame_count": 60, "raw_rgba_pixel_format": "rgba_1920x1080",
                "all_frames_decoded_rgba_pixel_identical": True,
                "checked_frames": [0, 15, 30, 59],
                "ordered_rgba_frame_digests_sha256": RGBA,
                "png_container_byte_identical": False,
                "png_container_mismatch_count": 60,
            },
        }
        self.store()
        for name in ("semwrightd.log", "provider-status-reconciliation.json",
                     "motionwright-render-evidence.json",
                     "native-provider-lifecycle.json", "motionwright-direct-parity-film.json",
                     "native-render-stage-cost.json"):
            (self.private / name).write_text(
                "job_ref=/private/owner/NEVER_PUBLISH_SESSION",
                encoding="utf-8",
            )

    def store(self):
        (self.private / "result.json").write_text(json.dumps(self.result))
        (self.private / "native-direct-mediated-parity.json").write_text(
            json.dumps(self.parity)
        )

    def refused(self, reason):
        with self.assertRaises(pub.PublicationError) as failure:
            pub.publish(self.private, self.public, SOURCE, SDK, "success")
        self.assertEqual(failure.exception.code, reason)
        self.assertFalse(self.public.exists())

    def test_success_exact_native_frames_digest_and_limits(self):
        report = pub.publish(self.private, self.public, SOURCE, SDK, "success")
        self.assertEqual(report["ci_native_motion_canvas_evidence"], "PASS")
        self.assertEqual(report["native_frames_per_leg"], 60)
        self.assertTrue(report["native_rgba_direct_mediated_parity"])
        self.assertFalse(report["png_container_binary_equality_claimed"])
        self.assertFalse(report["intermittent_provider_generation_issue_fixed"])
        self.assertFalse(report["v01_release_authorized"])
        self.assertEqual(report["installed_owner_acceptance"], "NOT_RUN")
        self.assertEqual({p.name for p in self.public.iterdir()},
                         set(pub.FILES) | {"public-evidence.json"})
        for entry in report["included_files"]:
            content = (self.public / entry["name"]).read_bytes()
            self.assertEqual(content, (self.private / entry["name"]).read_bytes())
            self.assertEqual(entry["sha256"], sha(content))
            self.assertEqual(entry["bytes"], len(content))
        self.assertNotIn("NEVER_PUBLISH_SESSION",
                         (self.public / "public-evidence.json").read_text())

    def test_failed_cancelled_or_skipped_never_admits_native_media(self):
        for status in ("failure", "cancelled", "skipped"):
            with self.subTest(status=status):
                target = self.public.with_name("public-" + status)
                result = pub.publish(self.private, target, SOURCE, SDK, status)
                self.assertEqual(result["ci_native_motion_canvas_evidence"], "NOT_VERIFIED")
                self.assertEqual(result["included_files"], [])
                self.assertIsNone(result["native_rgba_direct_mediated_parity"])
                self.assertEqual([f.name for f in target.iterdir()], ["public-evidence.json"])

    def test_foreign_motionwright_source_or_sdk_pin_rejected(self):
        self.result["motionwright_sha"] = "0" * 40
        self.store()
        self.refused("native_render_source_not_verified")
        self.result["motionwright_sha"] = SOURCE
        self.result["semwright_sha"] = "0" * 40
        self.store()
        self.refused("native_render_source_not_verified")

    def test_foreign_owner_generation_cannot_be_same_film_parity(self):
        self.parity["project_generation"] = "other-owner"
        self.store()
        self.refused("native_render_parity_not_verified")

    def test_unverified_rgba_or_forged_human_pass_refused(self):
        self.parity["pixels"]["all_frames_decoded_rgba_pixel_identical"] = False
        self.store()
        self.refused("native_render_parity_not_verified")
        self.parity["pixels"]["all_frames_decoded_rgba_pixel_identical"] = True
        self.parity["human_visual_acceptance"] = "PASS"
        self.store()
        self.refused("native_render_parity_not_verified")

    def test_foreign_mediated_manifest_identity_refused(self):
        self.parity["native_manifest_sha256"]["mediated"] = "f" * 64
        self.store()
        self.refused("native_render_manifest_identity_mismatch")

    def test_manifest_content_swap_rejected_by_sha256(self):
        (self.private / "artifact-manifest.json").write_bytes(
            b'{"schema":"source-bound-manifest","frames":30}'
        )
        self.refused("native_render_manifest_digest_mismatch")

    def test_private_manifest_not_exported(self):
        (self.private / "artifact-manifest.json").write_bytes(
            b'{"native_job_ref":"/private/owner/credential"}'
        )
        self.refused("private_metadata_in_public_source")

    def test_modified_preview_after_proof_rejected(self):
        (self.private / "frame-middle.png").write_bytes(
            b"\x89PNG\r\n\x1a\nchanged-pixels"
        )
        self.refused("native_render_review_hash_mismatch")

    def test_linked_native_frame_refused(self):
        target = self.private / "direct-native-frame-000030.png"
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=lambda path: path == target or original(path)):
            self.refused("missing_or_linked_public_source")

    def test_duplicate_authority_json_key_fails_closed(self):
        (self.private / "native-direct-mediated-parity.json").write_text(
            '{"schema":"trusted","schema":"forged"}'
        )
        self.refused("duplicate_evidence_json_key")

    def test_invalid_state_existing_dest_or_private_output_path_refused(self):
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.private, self.public, SOURCE, SDK, "pending")
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.private, self.private / "public", SOURCE, SDK, "failure")
        pub.publish(self.private, self.public, SOURCE, SDK, "failure")
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.private, self.public, SOURCE, SDK, "success")

    def test_workflow_upload_is_allowlisted_not_private_directory(self):
        path = (Path(__file__).resolve().parents[2] /
                ".github/workflows/native-render-e2e.yml")
        yml = path.read_text(encoding="utf-8")
        self.assertIn("path: verification/native-render-public", yml)
        self.assertNotIn("path: verification/native-render-e2e\n", yml)
        self.assertIn("test_native_render_public_artifact.py", yml)


if __name__ == "__main__":
    unittest.main()
