"""Fail-closed v0.5 artifact publishing, no native provider or render."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import craft_public_artifact as pub  # noqa: E402

MW = "a" * 40
SW = "b" * 40
PNG = pub.PNG_MAGIC + b"public synthetic pixels"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"public synthetic film"

def encoded(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True) + "\n").encode()

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CraftPublicArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mw-craft-public-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "private"
        self.root.mkdir()
        self.output = Path(self.tmp.name) / "public"
        for fixture, (family, layout, disclaimer) in pub.FAMILIES.items():
            path = self.root / MW / fixture
            path.mkdir(parents=True)
            project = encoded({"production_design": {
                "heroes": [{"config": {"layout": layout}}]}})
            source = encoded({"motionwright_sha": MW, "semwright_sha": SW,
                              "fixture": fixture, "native_av_master_e2e": "PASS",
                              "project": {"fixture": family}, "frame_count": 180,
                              "decoded_video_frames": 180, "master_sha256": sha(MP4)})
            inspection = encoded({
                "technical_sampling": "PASS", "layout_regression": "PASS",
                "creative_approval": "required",
                "source_project": {"sha256": sha(project)},
                "contact_sheet": {"path": "native-contact-sheet.png", "sha256": sha(PNG)},
            })
            (path / "editable-project.json").write_bytes(project)
            (path / "result.json").write_bytes(source)
            (path / "creative-frame-inspection.json").write_bytes(inspection)
            (path / "expected-component-layout.json").write_bytes(
                encoded({"nodes": [{}, {}, {}, {}, {}, {"text": disclaimer}]}))
            (path / "native-contact-sheet.png").write_bytes(PNG)
            (path / "motionwright-master.mp4").write_bytes(MP4)
            # Owner receipts must never enter an artifact even on success.
            (path / "semwrightd.log").write_text("secret owned socket")
            (path / "provider-receipt.json").write_text(
                '{"job_ref":"DO-NOT-PUBLISH","path":"/home/runner/private"}')

    def reject(self, code: str):
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.root, self.output, MW, SW, "success")
        self.assertEqual(caught.exception.code, code)
        self.assertFalse(self.output.exists())

    def test_publish_only_allowlisted_source_verified_media(self):
        manifest = pub.publish(self.root, self.output, MW, SW, "success")
        self.assertEqual(manifest["ci_evidence"], "PASS")
        self.assertEqual(len(manifest["included_files"]), 12)
        self.assertEqual(len(list(self.output.iterdir())), 13)
        self.assertFalse(manifest["raw_owner_receipts_uploaded"])
        self.assertFalse(manifest["raw_native_logs_uploaded"])
        self.assertFalse(manifest["native_render_started_by_publication"])
        self.assertFalse(manifest["v05_release_ready"])
        self.assertEqual(manifest["human_creative_approval"], "NOT_RUN")
        for item in manifest["included_files"]:
            data = (self.output / item["name"]).read_bytes()
            self.assertEqual(item["sha256"], sha(data))
            self.assertEqual(item["bytes"], len(data))
        contents = (self.output / "public-evidence.json").read_text()
        self.assertNotIn("DO-NOT-PUBLISH", contents)
        self.assertNotIn("owned socket", contents)
        self.assertNotIn("/home/runner", contents)

    def test_failed_ci_never_reuses_native_media_as_pass(self):
        data = pub.publish(self.root, self.output, MW, SW, "failure")
        self.assertEqual(data["ci_evidence"], "NOT_VERIFIED")
        self.assertEqual(data["included_files"], [])
        self.assertEqual([p.name for p in self.output.iterdir()], ["public-evidence.json"])

    def test_corrupt_native_master_rejected(self):
        (self.root / MW / "contrast-landscape" / "motionwright-master.mp4").write_bytes(
            b"\x00\x00\x00\x18ftypisom" + b"corrupt")
        self.reject("public_media_digest_mismatch")

    def test_source_notice_cannot_be_forged(self):
        path = self.root / MW / "quote-square" / "expected-component-layout.json"
        path.write_bytes(encoded({"nodes": [{}, {}, {}, {}, {}, {"text": "VERIFIED"}]}))
        self.reject("unverified_craft_public_source")

    def test_foreign_source_is_not_attested(self):
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.root, self.output, "c" * 40, SW, "success")
        self.assertEqual(caught.exception.code, "private_fixture_missing_or_linked")

    def test_existing_public_output_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.root, self.output, MW, SW, "success")
        self.assertEqual(caught.exception.code, "public_destination_already_exists")

    def test_duplicate_evidence_keys_rejected(self):
        (self.root / MW / "contrast-portrait" / "result.json").write_text(
            '{"native_av_master_e2e":"PASS","native_av_master_e2e":"FORGED"}')
        self.reject("duplicate_evidence_json_key")


if __name__ == "__main__":
    unittest.main()
