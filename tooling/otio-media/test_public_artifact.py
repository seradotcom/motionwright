"""Adversarial synthetic OTIO artifact publication. No external NLE or render."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import public_artifact as pub

SHA = "a" * 40
MEDIA = b"\x00\x00\x00\x18ftypisom" + b"original-owned-synthetic-ci-H264"
HASH = hashlib.sha256(MEDIA).hexdigest()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def j(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True) + "\n").encode("utf-8")


class PublicOtioTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-otio-public-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.private = self.base / "private"
        self.bundle = self.private / "portable"
        self.media = self.bundle / "media"
        self.media.mkdir(parents=True)
        self.public = self.base / "public"

        self.project = {
            "title": "Original source media editorial exchange",
            "scenes": [{"name": n} for n in (
                "Opening source", "Detail source", "Abstract branded CTA")],
            "assets": [{"content_sha256": HASH}],
        }
        self.project_bytes = j(self.project)
        (self.bundle / "source-project.json").write_bytes(self.project_bytes)
        self.manifest = {
            "project_sha256": digest(self.project_bytes),
            "source_media_count": 1,
            "nle_import_verified": False,
            "owner_review_declared": "CI original fixture owner",
            "property_fidelity": [{"property": n} for n in range(12)],
        }
        self.save_manifest()
        self.timeline = {
            "OTIO_SCHEMA": "Timeline.1",
            "metadata": {"motionwright": {
                "permission_to_publish": False,
                "native_editable_reimport_supported": False,
                "verified_media_clip_count": 2,
                "source_project_sha256": digest(self.project_bytes),
                "property_fidelity": [{"property": n} for n in range(12)],
            }},
            "tracks": {"children": [{"children": [
                {"OTIO_SCHEMA": "Clip.1",
                 "media_reference": {"OTIO_SCHEMA": "ExternalReference.1",
                                     "target_url": "media/" + HASH + ".mp4"}},
                {"OTIO_SCHEMA": "Clip.1",
                 "media_reference": {"OTIO_SCHEMA": "ExternalReference.1",
                                     "target_url": "media/" + HASH + ".mp4"}},
                {"OTIO_SCHEMA": "Clip.1",
                 "media_reference": {"OTIO_SCHEMA": "MissingReference.1"}},
            ]}]},
        }
        self.save_timeline()
        (self.media / (HASH + ".mp4")).write_bytes(MEDIA)
        self.result = {
            "schema": "motionwright.otio-real-media-native-e2e/1",
            "motionwright_sha": SHA, "nle_receiver": "opentimelineio_python_schema",
            "real_video_stream_decode_and_rational_time": "PASS",
            "source_references_located": 2, "MissingReference_preserved": 1,
            "bounded_cut_import_preview": "PASS_NOT_APPLIED",
            "audio_and_native_effects": "NOT_ROUNDTRIPPED",
            "external_nle_editor": "NOT_RUN",
            "human_creative_approval": "NOT_REVIEWED",
            "native_editing_changes_committed": False,
            "release": "BLOCKED_REVIEW",
            "media_sha256": HASH,
            "source_project_sha256": digest(self.project_bytes),
        }
        self.save_result()
        (self.private / "owner-secrets.json").write_text('{"job_ref":"SECRET"}')

    def save_manifest(self):
        (self.bundle / "bridge-manifest.json").write_bytes(j(self.manifest))

    def save_timeline(self):
        (self.bundle / "timeline.otio").write_bytes(j(self.timeline))

    def save_result(self):
        (self.private / "result.json").write_bytes(j(self.result))

    def refuse(self, reason: str):
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.private, self.public, SHA, "success")
        self.assertEqual(caught.exception.code, reason)
        self.assertFalse(self.public.exists())

    def test_pass_exports_only_original_synthetic_media_and_otio(self):
        report = pub.publish(self.private, self.public, SHA, "success")
        expected = {"timeline.otio", "public-evidence.json",
                    "media/" + HASH + ".mp4"}
        found = {str(p.relative_to(self.public)).replace("\\", "/")
                 for p in self.public.rglob("*") if p.is_file()}
        self.assertEqual(found, expected)
        self.assertEqual(report["ci_evidence"], "PASS")
        self.assertFalse(report["real_human_rights_authenticated"])
        self.assertFalse(report["native_edits_committed"])
        self.assertFalse(report["v05_release_ready"])
        self.assertEqual(report["human_acceptance"], "NOT_RUN")
        for f in report["included_files"]:
            self.assertEqual(digest((self.public / f["name"]).read_bytes()),
                             f["sha256"])
        self.assertNotIn("SECRET", (self.public / "public-evidence.json").read_text())

    def test_unknown_or_failed_run_never_publishes_media(self):
        for status in ("failure", "skipped", "cancelled"):
            output = self.base / status
            report = pub.publish(self.private, output, SHA, status)
            self.assertEqual(report["ci_evidence"], "NOT_VERIFIED")
            self.assertEqual([p.name for p in output.iterdir()],
                             ["public-evidence.json"])

    def test_tampered_media_refused(self):
        (self.media / (HASH + ".mp4")).write_bytes(MEDIA + b"changed")
        self.refuse("synthetic_media_digest_mismatch")

    def test_forged_real_editor_execution_refused(self):
        self.result["external_nle_editor"] = "DaVinci Resolve"
        self.save_result()
        self.refuse("unverified_synthetic_otio_source")

    def test_missing_source_reference_cannot_be_invented(self):
        self.timeline["tracks"]["children"][0]["children"][2][
            "media_reference"] = {"OTIO_SCHEMA": "ExternalReference.1",
                                  "target_url": "media/" + HASH + ".mp4"}
        self.save_timeline()
        self.refuse("unsafe_synthetic_otio_media_reference")

    def test_native_property_losses_or_rights_cannot_be_silently_removed(self):
        self.timeline["metadata"]["motionwright"]["property_fidelity"] = []
        self.save_timeline()
        self.refuse("unsafe_synthetic_otio_timeline")
        self.timeline["metadata"]["motionwright"]["property_fidelity"] = [
            {"property": n} for n in range(12)]
        self.timeline["metadata"]["motionwright"]["permission_to_publish"] = True
        self.save_timeline()
        self.refuse("unsafe_synthetic_otio_timeline")

    def test_private_owner_token_cannot_be_embedded_in_public_timeline(self):
        self.timeline["name"] = "session_ticket"
        self.save_timeline()
        self.refuse("unsafe_synthetic_otio_media_reference")

    def test_duplicate_result_json_and_existing_public_target_are_refused(self):
        (self.private / "result.json").write_text(
            '{"motionwright_sha":"'+SHA+'","motionwright_sha":"'+SHA+'"}')
        self.refuse("duplicate_evidence_json_key")
        self.public.mkdir()
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.private, self.public, SHA, "success")
        self.assertEqual(caught.exception.code, "existing_public_destination")


if __name__ == "__main__":
    unittest.main()
