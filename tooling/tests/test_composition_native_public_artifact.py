"""No native provider or render; strict public evidence allowlist tests."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import composition_native_public_artifact as pub  # noqa: E402

MW, SW = "a" * 40, "b" * 40
SHEET = b"\x89PNG\r\n\x1a\n" + b"only-original-synthetic-test-pixels"
VIDEO = b"\x00\x00\x00\x18ftypisom" + b"synthetic-h264-fixture"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class JointMasterPublicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mw-joint-public-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "private"
        self.folder = self.root / MW / "composition-stack"
        self.folder.mkdir(parents=True)
        self.output = Path(self.tmp.name) / "public"
        project = {"scenes": [{}, {}, {}]}
        project_bytes = json.dumps(project).encode()
        self.folder.joinpath("editable-project.json").write_bytes(project_bytes)
        review = {
            "technical_sampling": "PASS",
            "source_truth": "first-party-original-and-synthetic-only",
            "source_project_sha256": sha(project_bytes),
            "frame_indices": [140, 270, 480],
            "contact_sheet": {"path": "composition-native-contact-sheet.png",
                              "sha256": sha(SHEET)},
            "decoded_master": {"technical_sampling": "PASS",
                               "frame_indices": [140, 270, 480],
                               "mp4_sha256": sha(VIDEO)},
        }
        review_bytes = json.dumps(review).encode()
        self.folder.joinpath("composition-native-inspection.json").write_bytes(review_bytes)
        self.result = {
            "native_av_master_e2e": "PASS",
            "motionwright_sha": MW, "semwright_sha": SW,
            "fixture": "composition-stack", "frame_count": 540,
            "decoded_video_frames": 540, "master_sha256": sha(VIDEO),
            "joint_native_pixel_evidence_sha256": sha(review_bytes),
            "project": {"fixture": "composition-stack/1",
                        "original_ui_not_real_capture": True,
                        "data_truth": "synthetic_fixture_not_real_world_metric"},
        }
        self.write_result()
        self.folder.joinpath("composition-native-contact-sheet.png").write_bytes(SHEET)
        self.folder.joinpath("motionwright-master.mp4").write_bytes(VIDEO)
        self.folder.joinpath("semwrightd.log").write_text("PRIVATE OWNER REF DO NOT SHARE")
        self.folder.joinpath("owner-receipts.json").write_text('{"job_ref":"SECRET"}')

    def write_result(self):
        self.folder.joinpath("result.json").write_text(json.dumps(self.result))

    def refused(self, code):
        with self.assertRaises(pub.PublicationError) as caught:
            pub.publish(self.root, self.output, MW, SW, "success")
        self.assertEqual(caught.exception.code, code)
        self.assertFalse(self.output.exists())

    def test_only_verified_synthetic_sheet_and_master_are_public(self):
        receipt = pub.publish(self.root, self.output, MW, SW, "success")
        self.assertEqual(receipt["ci_evidence"], "PASS")
        self.assertEqual(set(p.name for p in self.output.iterdir()),
                         {"composition-native-contact-sheet.png",
                          "motionwright-master.mp4", "public-evidence.json"})
        self.assertFalse(receipt["native_mutation_replayed_by_publisher"])
        self.assertFalse(receipt["raw_native_receipts_uploaded"])
        self.assertEqual(receipt["human_creative_review"], "NOT_RUN")
        self.assertFalse(receipt["v05_release_ready"])
        for item in receipt["included_files"]:
            self.assertEqual(sha((self.output / item["name"]).read_bytes()),
                             item["sha256"])
        self.assertNotIn("SECRET", (self.output / "public-evidence.json").read_text())

    def test_failed_or_unknown_ci_has_no_video(self):
        for status in ("failure", "cancelled", "skipped"):
            target = self.output.parent / ("public-" + status)
            receipt = pub.publish(self.root, target, MW, SW, status)
            self.assertEqual(receipt["ci_evidence"], "NOT_VERIFIED")
            self.assertEqual([p.name for p in target.iterdir()], ["public-evidence.json"])

    def test_tampered_master_digest_is_denied(self):
        self.folder.joinpath("motionwright-master.mp4").write_bytes(VIDEO + b"changed")
        self.refused("joint_media_digest_mismatch")

    def test_unauthorized_real_world_claim_refused(self):
        self.result["project"]["original_ui_not_real_capture"] = False
        self.write_result()
        self.refused("unverified_joint_native_source")

    def test_unverified_frame_count_cannot_appear_passed(self):
        self.result["decoded_video_frames"] = 539
        self.write_result()
        self.refused("unverified_joint_native_source")

    def test_duplicate_result_fields_fail_closed(self):
        self.folder.joinpath("result.json").write_text('{"fixture":"composition-stack",'
                                                      '"fixture":"different"}')
        self.refused("duplicate_evidence_json_key")

    def test_existing_target_and_overlap_refused(self):
        self.output.mkdir()
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.publish(self.root, self.output, MW, SW, "success")
        self.assertEqual(ctx.exception.code, "public_artifact_already_exists")
        nested = self.folder / "public"
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.publish(self.root, nested, MW, SW, "success")
        self.assertEqual(ctx.exception.code, "public_and_private_roots_overlap")


if __name__ == "__main__":
    unittest.main()
