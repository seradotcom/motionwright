"""Synthetic source-bound three-clip Kdenlive G2 input tests.

No Kdenlive process, H264 encoding, Semwright Broker or native rendering
is executed here. A separate authorized app run owns exact process custody.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from kdenlive_complete_g2_fixture import (
    KdenliveCompleteFixtureRefused, EXPECTED_SOURCE_INTERVALS,
    admitted_firstparty_bindings, require_full_original_manifest,
    require_ephemeral_approved_source, firstparty_media_command,
    firstparty_audio_probe_command, require_firstparty_audio_probe,
    selected_audio_source_mode,
)

SOURCE_SHA = "ab" * 32
ASSET = "8ae8e638-a16d-4d28-a92c-765999999999"


def original_three_scene_project() -> dict:
    return {
        "id": "8ae8e638-a16d-4d28-a92c-765900000001",
        "scenes": [
            {
                "id": f"8ae8e638-a16d-4d28-a92c-{n:012d}",
                "duration": {"num": "1", "den": "1"},
            }
            for n in (10, 11, 12)
        ],
        "assets": [{
            "id": ASSET, "media_type": "video/mp4",
            "content_sha256": SOURCE_SHA,
        }],
    }


def complete_manifest(project: dict, rows: list[dict]) -> dict:
    return {
        "schema": "motionwright.otio-portable-media-bridge/1",
        "output_profile": "portable-media-cut-v2",
        "nle_import_verified": False, "external_program_executed": True,
        "publishing_authorized": False, "missing_scene_ids": [],
        "scene_order": [scene["id"] for scene in project["scenes"]],
        "bound_media": [
            {
                "scene_id": item["scene_id"],
                "source_sha256": SOURCE_SHA,
                "source_fps": 30,
                "source_start_frame": item["source_start_frame"],
                "duration_frames": 30,
            }
            for item in rows
        ],
    }


class KdenliveFullyBoundOriginalSourceTests(unittest.TestCase):
    def source(self) -> tuple[dict, list[dict]]:
        project = original_three_scene_project()
        rows = admitted_firstparty_bindings(
            project, ASSET, SOURCE_SHA, frames=90, fps=30,
        )
        return project, rows

    def test_original_three_scene_video_provenance_covers_all_source_intervals(self):
        project, rows = self.source()
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [row["source_start_frame"] for row in rows], [0, 30, 60]
        )
        self.assertEqual(
            [row["scene_id"] for row in rows],
            [scene["id"] for scene in project["scenes"]],
        )
        self.assertEqual(
            [row["rights"]["authorized"] for row in rows], [True] * 3
        )
        report = require_full_original_manifest(
            complete_manifest(project, rows), project, SOURCE_SHA,
        )
        self.assertEqual(report["fully_bound_scene_count"], 3)
        self.assertEqual(report["missing_reference_count"], 0)
        self.assertFalse(report["actual_Kdenlive_native_editor_import_or_reopen"])
        self.assertFalse(report["release_authorized"])

    def test_original_missing_reference_negative_is_not_a_full_NLE_import(self):
        project, rows = self.source()
        bad = complete_manifest(project, rows)
        bad["missing_scene_ids"] = [project["scenes"][2]["id"]]
        with self.assertRaisesRegex(
            KdenliveCompleteFixtureRefused, "three_bound",
        ):
            require_full_original_manifest(bad, project, SOURCE_SHA)

    def test_two_bound_clips_with_missing_original_third_cannot_pass(self):
        project, rows = self.source()
        bad = complete_manifest(project, rows[:2])
        with self.assertRaisesRegex(KdenliveCompleteFixtureRefused, "three_bound"):
            require_full_original_manifest(bad, project, SOURCE_SHA)

    def test_three_video_clips_must_have_distinct_original_source_inpoints(self):
        project, rows = self.source()
        bad = complete_manifest(project, rows)
        bad["bound_media"][2]["source_start_frame"] = 30
        with self.assertRaisesRegex(
            KdenliveCompleteFixtureRefused, "inpoint_mapping_not_exact",
        ):
            require_full_original_manifest(bad, project, SOURCE_SHA)

    def test_edited_clip_rate_or_duration_cannot_substitute_original(self):
        project, rows = self.source()
        for label, field, value in [
            ("fps", "source_fps", 24),
            ("duration", "duration_frames", 31),
        ]:
            with self.subTest(label=label):
                bad = complete_manifest(project, rows)
                bad["bound_media"][1][field] = value
                with self.assertRaisesRegex(
                    KdenliveCompleteFixtureRefused, "inpoint_mapping_not_exact",
                ):
                    require_full_original_manifest(bad, project, SOURCE_SHA)

    def test_falsely_authorized_source_digest_or_duplicate_scene_rejected(self):
        project, rows = self.source()
        bad = copy.deepcopy(project)
        bad["assets"][0]["content_sha256"] = "cd" * 32
        with self.assertRaisesRegex(
            KdenliveCompleteFixtureRefused, "owner_asset_does_not_match",
        ):
            admitted_firstparty_bindings(
                bad, ASSET, SOURCE_SHA, frames=90, fps=30,
            )
        bad = copy.deepcopy(project)
        bad["scenes"][2]["id"] = bad["scenes"][0]["id"]
        with self.assertRaisesRegex(
            KdenliveCompleteFixtureRefused, "scene_IDs_not_unique",
        ):
            admitted_firstparty_bindings(
                bad, ASSET, SOURCE_SHA, frames=90, fps=30,
            )

    def test_original_source_video_shorter_than_ninety_frames_is_rejected(self):
        project, rows = self.source()
        self.assertEqual(sum(interval[1] for interval in EXPECTED_SOURCE_INTERVALS), 90)
        with self.assertRaisesRegex(
            KdenliveCompleteFixtureRefused, "90frame_source",
        ):
            admitted_firstparty_bindings(
                project, ASSET, SOURCE_SHA, frames=89, fps=30,
            )
        with self.assertRaisesRegex(
            KdenliveCompleteFixtureRefused, "90frame_source",
        ):
            admitted_firstparty_bindings(
                project, ASSET, SOURCE_SHA, frames=90, fps=24,
            )

    def test_native_rights_or_human_approval_never_inferred_from_owner_fixture(self):
        project, rows = self.source()
        manifest = complete_manifest(project, rows)
        self.assertIs(manifest["external_program_executed"], True)
        report = require_full_original_manifest(manifest, project, SOURCE_SHA)
        self.assertFalse(report["human_or_rights_independent_acceptance"])
        self.assertFalse(report["actual_Kdenlive_native_editor_import_or_reopen"])
        self.assertFalse(report["release_authorized"])
        stale_effect = copy.deepcopy(manifest)
        stale_effect["external_program_executed"] = False
        with self.assertRaisesRegex(KdenliveCompleteFixtureRefused, "three_bound"):
            require_full_original_manifest(stale_effect, project, SOURCE_SHA)

    def test_disposable_linux_G2_gate_rejects_simulated_windows_on_all_hosts(self):
        with mock.patch.object(sys, "platform", "win32"):
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaisesRegex(
                    KdenliveCompleteFixtureRefused, "disposable_Linux_CI",
                ):
                    require_ephemeral_approved_source(Path(root) / "new")

    def test_fixture_requires_explicit_source_selection_even_on_Linux(self):
        with mock.patch.dict(
            "os.environ", {"MOTIONWRIGHT_APPROVED_KDENLIVE_G2_SOURCE": "0"}
        ):
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaisesRegex(
                    KdenliveCompleteFixtureRefused, "disposable_Linux_CI",
                ):
                    require_ephemeral_approved_source(Path(root) / "new")


    def test_default_audio_source_mode_preserves_silent_original_and_rejects_unknown(self):
        with mock.patch.dict("os.environ", {"MOTIONWRIGHT_KDENLIVE_G2_AUDIO_SOURCE": "0"}):
            self.assertIs(selected_audio_source_mode(), False)
        with mock.patch.dict("os.environ", {"MOTIONWRIGHT_KDENLIVE_G2_AUDIO_SOURCE": "1"}):
            self.assertIs(selected_audio_source_mode(), True)
        for value in ("TRUE", "tone", "", "2"):
            with self.subTest(value=value), mock.patch.dict(
                    "os.environ", {"MOTIONWRIGHT_KDENLIVE_G2_AUDIO_SOURCE": value}):
                with self.assertRaisesRegex(KdenliveCompleteFixtureRefused, "mode_invalid"):
                    selected_audio_source_mode()

    def test_existing_silent_fixture_ffmpeg_argv_remains_video_only(self):
        target = Path.cwd() / "original.mp4"
        argv = firstparty_media_command(target)
        self.assertIn("-an", argv)
        self.assertNotIn("-c:a", argv)
        self.assertEqual(argv.count("-i"), 1)
        self.assertEqual(argv[argv.index("-frames:v") + 1], "90")
        self.assertEqual(argv[-1], str(target))
        self.assertEqual(argv, [
            "ffmpeg", "-nostdin", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=3",
            "-frames:v", "90", "-an", "-c:v", "libx264",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(target)])

    def test_selected_audio_fixture_has_one_explicit_tone_and_aac_track(self):
        target = Path.cwd() / "tone.mp4"
        argv = firstparty_media_command(target, audio_source=True)
        self.assertNotIn("-an", argv)
        self.assertEqual(argv.count("-i"), 2)
        self.assertEqual(argv[argv.index("-map") + 1], "0:v:0")
        self.assertEqual(argv[argv.index("-map", argv.index("-map") + 1) + 1], "1:a:0")
        self.assertEqual(argv[argv.index("-c:a") + 1], "aac")
        self.assertEqual(argv[argv.index("-ar") + 1], "48000")
        self.assertEqual(argv[argv.index("-ac") + 1], "1")
        self.assertTrue(any("frequency=997:sample_rate=48000:duration=3" in a for a in argv))
        self.assertEqual(firstparty_audio_probe_command(target)[-1], str(target))
        with self.assertRaisesRegex(KdenliveCompleteFixtureRefused, "command_invalid"):
            firstparty_media_command(target, audio_source=1)

    def test_audio_probe_requires_one_real_aac_track_without_claiming_fidelity(self):
        stream = {"codec_name": "aac", "codec_type": "audio",
                  "sample_rate": "48000", "channels": 1}
        good = json.dumps({"streams": [stream]}).encode()
        result = require_firstparty_audio_probe(good)
        self.assertEqual(result["status"], "SOURCE_AUDIO_ONLY")
        self.assertIs(result["native_audio_fidelity_accepted"], False)
        self.assertIs(result["release_authorized"], False)
        failures = [
            b'{"streams":[]}',
            json.dumps({"streams": [stream, stream]}).encode(),
            json.dumps({"streams": [{**stream, "codec_name": "opus"}]}).encode(),
            json.dumps({"streams": [{**stream, "sample_rate": "44100"}]}).encode(),
            json.dumps({"streams": [{**stream, "channels": True}]}).encode(),
            b'{"streams":[{"codec_name":"aac","codec_name":"aac","codec_type":"audio","sample_rate":"48000","channels":1}]}',
        ]
        for value in failures:
            with self.subTest(payload=value), self.assertRaises(KdenliveCompleteFixtureRefused):
                require_firstparty_audio_probe(value)

if __name__ == "__main__":
    unittest.main()
