"""Adversarial tests for v0.1 native Blender and Manim public scene evidence."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_scene_public_artifact as pub

SHA = "a" * 40
PIN = "b" * 40
SECRET = "OWNER_PRIVATE_SESSION_SECRET"

def hash_bytes(value):
    return hashlib.sha256(value).hexdigest()

class NativeSceneEvidenceTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base = Path(t.name)
        self.private = self.base / "private"
        self.private.mkdir()
        self.out = self.base / "public"

    def fixture(self, scene):
        self.scene = scene
        config = pub.SCENES[scene]
        if scene == "blender":
            self.media = b"glTF" + struct.pack("<II", 2, 32) + b"blob" * 5
            asset = {"sha256": hash_bytes(self.media), "bytes": len(self.media),
                     "meshes": 2, "materials": 2}
        else:
            self.media = b"\x00\x00\x00\x18ftyp" + b"isom" + b"\x00" * 16
            asset = {"sha256": hash_bytes(self.media), "bytes": len(self.media),
                     "width": 640, "height": 360,
                     "relative_path": "videos/owned/360p12/owned.mp4"}
        project = {"project_id": SECRET, "generation": SECRET,
                   "revision": 9, "scene_id": SECRET}
        self.result = {f"native_{scene}_e2e": "PASS",
                       "motionwright_sha": SHA, "semwright_sha": PIN,
                       "semwright_version": "1.0.0",
                       "capabilities_checked": sorted(config["commands"]),
                       "project": project, config["asset_key"]: asset}
        self.receipt = {f"native_{scene}_e2e": "PASS", **project,
                        "receipt_count": config["required_count"],
                        "completed_commands": sorted(config["commands"]),
                        "result": {"native_driver": "driver:blender" if scene == "blender"
                                   else "driver:manim-community",
                                   "artifact": {"sha256": asset["sha256"],
                                                "bytes": asset["bytes"]},
                                   "private_job": SECRET}}
        self.write()
        (self.private / "semwrightd.log").write_text(SECRET)

    def write(self):
        c = pub.SCENES[self.scene]
        (self.private / "result.json").write_text(json.dumps(self.result))
        (self.private / c["evidence"]).write_text(json.dumps(self.receipt))
        (self.private / c["media"]).write_bytes(self.media)

    def reject(self, code):
        with self.assertRaises(pub.PublicationError) as cm:
            pub.publish(self.private, self.out, SHA, PIN, self.scene, "success")
        self.assertEqual(cm.exception.code, code)
        self.assertFalse(self.out.exists())

    def test_verified_media_only_no_original_owner_receipts(self):
        for scene in pub.SCENES:
            self.fixture(scene)
            target = self.base / ("published-" + scene)
            result = pub.publish(self.private, target, SHA, PIN, scene, "success")
            self.assertEqual({f.name for f in target.iterdir()},
                             {"public-evidence.json", pub.SCENES[scene]["media"]})
            self.assertEqual(result["included_files"][0]["sha256"], hash_bytes(self.media))
            self.assertEqual(result["included_files"][0]["bytes"], len(self.media))
            txt = (target / "public-evidence.json").read_text()
            self.assertNotIn(SECRET, txt)
            self.assertFalse(result["v01_release_authorized"])
            self.assertFalse(result["raw_daemon_or_sandbox_logs_uploaded"])
            self.assertFalse(result["native_effects_replayed_by_publisher"])
            self.assertEqual(result["installed_owner_acceptance"], "NOT_RUN")

    def test_failure_never_copies_broker_material(self):
        for scene in pub.SCENES:
            self.fixture(scene)
            for status in ("failure", "cancelled", "skipped"):
                dest = self.base / ("status-" + scene + status)
                x = pub.publish(self.private, dest, SHA, PIN, scene, status)
                self.assertEqual(x["native_scene_ci_evidence"], "NOT_VERIFIED")
                self.assertEqual(x["included_files"], [])
                self.assertEqual([f.name for f in dest.iterdir()], ["public-evidence.json"])

    def test_source_and_provenance_mismatch_rejected(self):
        self.fixture("blender")
        self.result["semwright_sha"] = "c" * 40
        self.write()
        self.reject("native_scene_receipts_not_verified")
        self.result["semwright_sha"] = PIN
        self.receipt["generation"] = "FOREIGN"
        self.write()
        self.reject("native_scene_receipts_not_verified")

    def test_wrong_digest_or_swapped_media_rejected(self):
        for scene in pub.SCENES:
            self.fixture(scene)
            (self.private / pub.SCENES[scene]["media"]).write_bytes(self.media + b"changed")
            self.reject("native_scene_artifact_integrity_mismatch")

    def test_forged_receipt_digest_refused(self):
        self.fixture("manim")
        self.receipt["result"]["artifact"]["sha256"] = "d" * 64
        self.write()
        self.reject("native_scene_artifact_identity_mismatch")

    def test_missing_native_private_receipts_refused(self):
        self.fixture("blender")
        (self.private / pub.SCENES["blender"]["evidence"]).unlink()
        self.reject("missing_or_linked_public_source")

    def test_duplicate_authority_fields_refused(self):
        self.fixture("blender")
        (self.private / "result.json").write_text(
            '{"motionwright_sha":"first","motionwright_sha":"second"}'
        )
        self.reject("duplicate_evidence_json_key")

    def test_linked_original_native_media_refused(self):
        self.fixture("manim")
        target = self.private / pub.SCENES["manim"]["media"]
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=lambda p: p == target or original(p)):
            self.reject("missing_or_linked_public_source")

    def test_forged_native_success_claim_refused(self):
        self.fixture("blender")
        self.result["native_blender_e2e"] = "FAIL"
        self.write()
        self.reject("native_scene_receipts_not_verified")

    def test_unsupported_mode_invalid_status_and_overlap_refused(self):
        self.fixture("blender")
        for scene, status in (("other", "success"), ("blender", "pending")):
            with self.assertRaises(pub.PublicationError):
                pub.publish(self.private, self.out, SHA, PIN, scene, status)
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.private, self.private / "out",
                        SHA, PIN, "blender", "failure")

    def test_workflows_upload_only_curated_assets(self):
        root = Path(__file__).resolve().parents[2]
        for scene in pub.SCENES:
            yml = (root / ".github/workflows" / ("native-" + scene + "-e2e.yml")).read_text()
            self.assertIn("path: verification/native-" + scene + "-public", yml)
            self.assertNotIn("path: verification/native-" + scene + "-e2e\n", yml)
            self.assertIn("test_native_scene_public_artifact.py", yml)

if __name__ == "__main__":
    unittest.main()
