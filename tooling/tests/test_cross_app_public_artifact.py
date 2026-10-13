"""Adversarial v0.1 public CI package tests; no provider or render invoked."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cross_app_public_artifact as pub  # noqa: E402

MW = "a" * 40
SW = "b" * 40


class CrossAppPublicArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-public-v01-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.root.mkdir()
        self.output = Path(self.temp.name) / "public"
        fixture = {
            "native_cross_app_recovery_e2e": "PASS",
            "source_motionwright": MW, "source_semwright": SW,
            "motion_canvas_frame_count": 60,
            "native_motion_canvas_successful_attempts": 1,
            "blender_native_driver_reexecutions": 0,
        }
        (self.root / "result.json").write_text(json.dumps(fixture), encoding="utf-8")
        for name in pub.COPY:
            if name.endswith(".png"):
                value = pub.PNG_MAGIC + b"sample pixels"
            elif name.endswith(".glb"):
                value = b"glTF" + b"source-native-mesh"
            elif name.endswith(".json"):
                value = b'{"schema":"safe-source-bound-fixture/1","count":60}'
            else:
                value = b"<!doctype html><title>Native cross app demo</title>"
            (self.root / name).write_bytes(value)
        fixture["blender_glb_sha256"] = hashlib.sha256(
            (self.root / "reused-blender.glb").read_bytes()
        ).hexdigest()
        fixture["motion_canvas_manifest_sha256"] = hashlib.sha256(
            (self.root / "artifact-manifest.json").read_bytes()
        ).hexdigest()
        (self.root / "result.json").write_text(
            json.dumps(fixture), encoding="utf-8"
        )
        # These must never be distributed, even when a CI run succeeds.
        secret = "/private/owner/session-ticket-DO-NOT-PUBLISH"
        for name in ("semwrightd.log", "run.log", "resume.json",
                     "repeat.json", "stop.json", "failure.json"):
            (self.root / name).write_text(
                '{"owner_path":"' + secret + '"}', encoding="utf-8"
            )

    def refused(self, code):
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.root, self.output, MW, SW, "success")
        self.assertEqual(caught.exception.code, code)
        self.assertFalse(self.output.exists())

    def test_success_is_exact_allowlist_and_private_sources_are_not_published(self):
        manifest = pub.publish(self.root, self.output, MW, SW, "success")
        self.assertEqual(manifest["ci_evidence"], "PASS")
        self.assertEqual(manifest["motionwright_source_sha"], MW)
        self.assertEqual(manifest["semwright_source_pin"], SW)
        self.assertFalse(manifest["raw_daemon_log_uploaded"])
        self.assertFalse(manifest["original_native_receipts_uploaded"])
        self.assertFalse(manifest["native_mutation_replayed_by_publisher"])
        self.assertFalse(manifest["v01_release_authorized"])
        self.assertEqual(manifest["installed_owner_acceptance"], "NOT_RUN")
        self.assertEqual(
            {p.name for p in self.output.iterdir()},
            set(pub.COPY) | {"public-evidence.json"},
        )
        for item in manifest["included_files"]:
            content = (self.output / item["name"]).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), item["sha256"])
            self.assertEqual(len(content), item["bytes"])
        public_text = (self.output / "public-evidence.json").read_text()
        self.assertNotIn("DO-NOT-PUBLISH", public_text)
        self.assertNotIn("/private/", public_text)

    def test_failed_job_can_publish_only_not_verified_receipt(self):
        receipt = pub.publish(self.root, self.output, MW, SW, "failure")
        self.assertEqual(receipt["ci_evidence"], "NOT_VERIFIED")
        self.assertEqual(receipt["included_files"], [])
        self.assertEqual([p.name for p in self.output.iterdir()],
                         ["public-evidence.json"])
        self.assertNotIn("owner_path",
                         (self.output / "public-evidence.json").read_text())

    def test_cancelled_job_never_reuses_existing_success_evidence(self):
        receipt = pub.publish(self.root, self.output, MW, SW, "cancelled")
        self.assertEqual(receipt["ci_evidence"], "NOT_VERIFIED")
        self.assertEqual(len(list(self.output.iterdir())), 1)

    def test_mismatched_native_manifest_digest_is_rejected(self):
        (self.root / "artifact-manifest.json").write_text(
            '{"schema":"safe-source-bound-fixture/1","count":61}',
            encoding="utf-8",
        )
        self.refused("native_source_hash_mismatch")

    def test_publication_inside_private_root_is_refused(self):
        child = self.root / "public"
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.root, child, MW, SW, "failure")
        self.assertEqual(caught.exception.code, "public_root_overlaps_private_source")
        self.assertFalse(child.exists())

    def test_private_manifest_is_rejected_before_output(self):
        (self.root / "artifact-manifest.json").write_text(
            '{"job_ref":"secret-provider-value"}', encoding="utf-8"
        )
        self.refused("private_metadata_in_public_source")

    def test_private_html_path_is_rejected(self):
        (self.root / "recovery-demo.html").write_text(
            '<a href="/home/runner/owner/secret.html">Open</a>', encoding="utf-8"
        )
        self.refused("private_metadata_in_public_source")

    def test_corrupt_media_magic_is_rejected(self):
        (self.root / "first-frame.png").write_bytes(b"private-not-a-png")
        self.refused("invalid_public_png")

    def test_duplicate_json_keys_do_not_silently_replace_authority(self):
        (self.root / "artifact-manifest.json").write_text(
            '{"schema":"safe","schema":"forged"}', encoding="utf-8"
        )
        self.refused("duplicate_evidence_json_key")

    def test_foreign_pin_or_source_rejected(self):
        self.refused_with_wrong_source()

    def refused_with_wrong_source(self):
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.root, self.output, "c" * 40, SW, "success")
        self.assertEqual(caught.exception.code, "unverified_public_success")
        self.assertFalse(self.output.exists())

    def test_unsupported_status_and_second_publication_fail_closed(self):
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.root, self.output, MW, SW, "running")
        self.assertEqual(caught.exception.code, "ci_result_invalid")
        pub.publish(self.root, self.output, MW, SW, "failure")
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.root, self.output, MW, SW, "success")
        self.assertEqual(caught.exception.code, "public_artifact_already_exists")

    def test_missing_or_linked_media_rejected(self):
        (self.root / "last-frame.png").unlink()
        self.refused("missing_or_linked_public_source")

    def test_linked_native_media_is_never_published(self):
        actual = Path.is_symlink

        def fake_link(path: Path):
            return path == self.root / "middle-frame.png" or actual(path)

        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=fake_link):
            self.refused("missing_or_linked_public_source")

    def test_workflow_uploads_only_staged_public_directory(self):
        workflow = (Path(__file__).resolve().parents[2]
                    / ".github/workflows/native-cross-app-recovery.yml")
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("path: verification/native-cross-app-public", text)
        self.assertNotIn("path: verification/native-cross-app-recovery\n", text)
        self.assertIn("test_cross_app_public_artifact.py", text)
        self.assertIn("NATIVE_CI_JOB_STATUS: ${{ job.status }}", text)

    def test_source_and_output_must_not_overlap(self):
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.root, self.root, MW, SW, "failure")
        self.assertFalse((self.root / "public-evidence.json").exists())


if __name__ == "__main__":
    unittest.main()
