"""Adversarial proof that native render phase output never leaks private text."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semantic_sfx_native_stage import STAGES, safe_stage


class ClosedNativeStageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.file = Path(self.directory.name) / "phase.json"

    def tearDown(self):
        self.directory.cleanup()

    def write(self, stage="entered"):
        self.file.write_text(json.dumps({
            "schema": "motionwright.sfx-ci-native-stage/1",
            "stage": stage,
            "final_av_master_verified": False,
            "native_job_outcome_established": False,
            "automatically_retried": False,
        }), encoding="utf-8")

    def test_each_allowlisted_stage_remains_a_nonresult(self):
        for stage in sorted(STAGES):
            with self.subTest(stage=stage):
                self.write(stage)
                self.assertEqual(safe_stage(self.file), stage)

    def test_unknown_or_malformed_or_private_stage_fails_closed(self):
        self.write("C:/Owner/Secrets/owner-session")
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")
        self.file.write_text('{"stage":"entered","stage":"unknown"}', encoding="utf-8")
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")
        self.file.write_text("garbage", encoding="utf-8")
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")

    def test_non_authoritative_success_flag_is_rejected(self):
        self.write()
        raw = json.loads(self.file.read_text())
        raw["native_job_outcome_established"] = True
        self.file.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")
        raw["native_job_outcome_established"] = False
        raw["private_user_session"] = "untrusted"
        self.file.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")

    def test_missing_or_excessive_file_does_not_publish_raw_data(self):
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")
        self.file.write_bytes(b"x" * 1100)
        self.assertEqual(safe_stage(self.file), "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
