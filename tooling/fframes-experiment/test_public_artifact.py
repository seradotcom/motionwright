"""Bounded original upstream measurements never imply renderer admission."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import public_artifact as pub

MW = "a" * 40


class PublicFframesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mw-fframes-facts-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.private = self.root / "private"
        self.private.mkdir()
        self.public = self.root / "public"
        self.report = {
            "schema": "motionwright.fframes-experimental-runtime/1",
            "status": "EXPERIMENTAL_UPSTREAM_SOURCE_RENDER_PASS",
            "motionwright_sha": MW, "fframes_exact_sha": pub.PIN,
            "fframes_license": "MIT",
            "fframes_repository": "https://github.com/dmtrKovalenko/fframes.git",
            "runtime": "disposable_ubuntu_24_04",
            "source_code_admitted_to_motionwright": False,
            "semwright_driver_host_executed": False,
            "owner_install_permission": False,
            "backend_release_admitted": False,
            "artistic_quality_reviewed": False,
            "hyperframes_same_fixture_comparison": "NOT_RUN",
            "interactive_gpu_preview": "NOT_RUN_HEADLESS_CI",
            "audio_score_composition": "NOT_RUN_NO_AUDIO_IN_HELLO_FIXTURE",
            "cold_build_measured": True, "warm_build_measured": True,
            "frame_preview_measured": True, "full_video_render_measured": True,
            "measurements": {phase: {"exit_code": 0, "elapsed_seconds": 2.1}
                             for phase in pub.PHASES},
            "preview_png": {"decoded_by_ffprobe": True, "width": 320,
                            "height": 180, "sha256": "a" * 64},
            "video": {"bytes": 4096, "sha256": "b" * 64,
                      "codec": "h264", "width": 320, "height": 180,
                      "duration_seconds": "1.00"},
            "failure_summary": "private source workspace path must not publish",
        }
        self.save()

    def save(self):
        (self.private / "result.json").write_text(json.dumps(self.report))

    def refuse(self, reason):
        with self.assertRaises(pub.Refused) as caught:
            pub.publish(self.private, self.public, MW, "success")
        self.assertIn(reason, str(caught.exception))
        self.assertFalse(self.public.exists())

    def test_synthetic_upstream_metrics_are_public_as_facts_not_adoption(self):
        report = pub.publish(self.private, self.public, MW, "success")
        self.assertEqual(report["ci_evidence"], "PASS")
        self.assertFalse(report["renderer_adoption_authorized"])
        self.assertFalse(report["v05_release_ready"])
        self.assertEqual(report["audio_fidelity_assessed"], "NOT_RUN")
        self.assertEqual(report["human_creative_acceptance"], "NOT_RUN")
        self.assertEqual(set(report["actual_measured_seconds"]), set(pub.PHASES))
        self.assertEqual([p.name for p in self.public.iterdir()],
                         ["public-evidence.json"])
        manifest = (self.public / "public-evidence.json").read_text()
        self.assertNotIn("private source workspace", manifest)
        self.assertNotIn("failure_summary", manifest)

    def test_failure_status_does_not_publish_stale_pass(self):
        report = pub.publish(self.private, self.public, MW, "failure")
        self.assertEqual(report["ci_evidence"], "NOT_VERIFIED")
        self.assertEqual(report["actual_measured_seconds"], {})
        self.assertEqual(report["source_preview"], {})
        self.assertEqual(report["source_one_second_video"], {})

    def test_foreign_commit_is_not_allowed_to_claim_pass(self):
        self.report["motionwright_sha"] = "c" * 40
        self.save()
        self.refuse("unverified_or_admitted")

    def test_renderer_adoption_cannot_be_spoofed(self):
        self.report["backend_release_admitted"] = True
        self.save()
        self.refuse("unverified_or_admitted")

    def test_missing_timing_is_not_equivalent_to_source_measurement(self):
        del self.report["measurements"]["warm_release_build"]
        self.save()
        self.refuse("invalid_source_timing")

    def test_invalid_native_preview_and_video_hash_refused(self):
        self.report["preview_png"]["decoded_by_ffprobe"] = False
        self.save()
        self.refuse("unverified_fframes_decoded_media")

    def test_duplicate_or_ambiguous_evidence_fields_rejected(self):
        (self.private / "result.json").write_text(
            '{"status":"PASS","status":"EXPERIMENTAL_UPSTREAM_SOURCE_RENDER_PASS"}')
        self.refuse("duplicate_upstream_evidence_key")

    def test_existing_target_and_private_overlap_denied(self):
        self.public.mkdir()
        with self.assertRaisesRegex(pub.Refused, "existing_public_destination"):
            pub.publish(self.private, self.public, MW, "failure")
        with self.assertRaisesRegex(pub.Refused, "overlapping_private_public_roots"):
            pub.publish(self.private, self.private / "out", MW, "failure")


if __name__ == "__main__":
    unittest.main()
