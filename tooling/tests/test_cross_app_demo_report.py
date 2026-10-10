"""Safety checks for the offline, evidence-bound cross-app demo report."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cross_app_demo_report import make_report


class CrossAppDemoReportTests(unittest.TestCase):
    def base(self, root: Path):
        result = {
            "native_cross_app_recovery_e2e": "PASS",
            "source_motionwright": "a" * 40,
            "source_semwright": "b" * 40,
            "blender_glb_sha256": "c" * 64,
            "motion_canvas_manifest_sha256": "d" * 64,
            "motion_canvas_frame_count": 60,
        }
        stop = {"blender": "executed"}
        resume = {
            "recovery": {"blender": "reused", "motion_canvas": "executed"},
            "blender_reexecution_count": 0,
        }
        repeat = {
            "recovery": {"blender": "reused", "motion_canvas": "reused"},
            "blender_reexecution_count": 0,
        }
        for filename, payload in [
            ("result.json", result), ("stop.json", stop),
            ("resume.json", resume), ("repeat.json", repeat),
        ]:
            (root / filename).write_text(json.dumps(payload))
        for filename in [
            "reused-blender.glb", "artifact-manifest.json",
            "first-frame.png", "middle-frame.png", "last-frame.png",
        ]:
            (root / filename).write_bytes(b"fixture - NOT native execution")
        return result, stop, resume, repeat

    def test_report_has_explicit_failure_boundary_and_local_native_links(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.base(root)
            report = make_report(root)
            body = report.read_text()
            self.assertIn("not</strong> a real Motion Canvas renderer crash", body)
            self.assertIn('href="reused-blender.glb"', body)
            self.assertIn('src="middle-frame.png"', body)
            self.assertIn("a" * 40, body)

    def test_unverified_recovery_state_is_never_presented_as_pass(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.base(root)
            p = root / "repeat.json"
            repeat = json.loads(p.read_text())
            repeat["recovery"]["blender"] = "executed"
            p.write_text(json.dumps(repeat))
            with self.assertRaises(AssertionError):
                make_report(root)
            self.assertFalse((root / "recovery-demo.html").exists())

    def test_missing_native_artifact_prevents_report(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.base(root)
            (root / "middle-frame.png").unlink()
            with self.assertRaises(AssertionError):
                make_report(root)
            self.assertFalse((root / "recovery-demo.html").exists())


if __name__ == "__main__":
    unittest.main()
