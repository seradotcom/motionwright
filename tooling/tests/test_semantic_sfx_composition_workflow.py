"""Source-only verification of separate pinned 60f vs 540f SFX CI lanes."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
OLD=ROOT/".github/workflows/semantic-sfx-native-av.yml"
MULTI=ROOT/".github/workflows/semantic-sfx-multiscene-native-av.yml"
RUNNER=ROOT/"tooling/native_av_master_e2e.py"


class NativeSfxMultisceneContracts(unittest.TestCase):
    def test_new_540f_job_reuses_exact_pinned_semwright_runtime(self):
        old=OLD.read_text(encoding="utf8")
        new=MULTI.read_text(encoding="utf8")
        for required in (
            "repository: seradotcom/semwright",
            "PLAYWRIGHT_BROWSERS_PATH: '0'",
            "SOURCE_LOCK.json",
            "python3 tooling/source_policy.py",
            "cargo fmt --all -- --check",
            "semwright-mlt-video-driver",
            "semwright-motion-canvas-driver",
            "semwright-mlt-runtime-runner",
            "semantic-sfx-av-prepare",
            "MOTIONWRIGHT_E2E_FIXTURE:",
            "actual", # disabled if not in original workflow
        ):
            if required=="actual":continue
            with self.subTest(contract=required):
                self.assertIn(required,old)
                self.assertIn(required,new)
        self.assertIn("MOTIONWRIGHT_E2E_FIXTURE: semantic-sfx-composition-av",new)
        self.assertIn("MOTIONWRIGHT_E2E_FIXTURE: semantic-sfx-av",old)
        self.assertNotIn("semantic-sfx-composition-av",old)
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH: /home/runner",new)

    def test_typed_sfx_source_project_dispatched_through_existing_owner_mlt(self):
        source=RUNNER.read_text(encoding="utf8")
        for required in (
            'IS_SFX_COMPOSITION = FIXTURE == "semantic-sfx-composition-av"',
            'COMPOSITION_SEED = IS_COMPOSITION or IS_SFX_COMPOSITION',
            'inspect_native_multiscene_sfx_av(',
            '"seed-composition"',
            '"semantic-sfx-multiscene-av-e2e"',
            '"semantic-sfx-master.wav"',
        ):
            with self.subTest(required=required):
                self.assertIn(required,source)
        self.assertNotIn("ffmpeg -i",source)
        self.assertIn('"driver.mlt-video.av.mux"',source)

    def test_no_success_artifacts_accepted_without_true_540f_native_verification(self):
        source=MULTI.read_text(encoding="utf8")
        for required in (
            'assert run["fixture"]=="semantic-sfx-composition-av"',
            'assert run["frame_count"]==proof["video_frames_decoded"]==540',
            'assert proof["expected_onset_samples"]==[9600,608000]',
            'assert proof["middle_scene_silence_sample_count"]==288000',
            'assert proof["max_absolute_onset_error_samples"]<=1024',
            'assert proof["human_creative_approval"]=="REQUIRED"',
            'tooling/semantic_sfx_av_public_artifact.py',
        ):
            self.assertIn(required,source)
        self.assertNotIn("--job-status success",source)
        self.assertIn("--job-status \"$NATIVE_CI_JOB_STATUS\"",source)
        self.assertIn("if: always()",source)


if __name__=="__main__":
    unittest.main()
