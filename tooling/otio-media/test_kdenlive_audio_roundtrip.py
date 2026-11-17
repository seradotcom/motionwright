"""Effect-free AV source/receiver controls; these are not Native acceptance."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch
import zipfile

import bridge
import kdenlive_gui_actions as actions
import kdenlive_gui_e2e as runner
import test_bridge as original_fixture
import test_kdenlive_firstparty_audio_track as audio_fixture
import test_kdenlive_initial_save as save_fixture


def native_return(timeline, capsule):
    tracks = []
    for original in timeline["tracks"]["children"]:
        clips = []
        for source in original["children"]:
            reference = copy.deepcopy(source["media_reference"])
            reference["metadata"] = {}
            reference["target_url"] = (capsule / reference["target_url"]).as_uri()
            clips.append({
                "OTIO_SCHEMA": "Clip.2", "name": source["name"], "enabled": True,
                "source_range": copy.deepcopy(source["source_range"]),
                "media_references": {"DEFAULT_MEDIA": reference},
                "active_media_reference_key": "DEFAULT_MEDIA",
                "metadata": {}, "effects": [], "markers": [],
            })
        tracks.append({"OTIO_SCHEMA": "Track.1", "kind": original["kind"],
                       "children": clips, "metadata": {}, "effects": [], "markers": []})
    return {
        "OTIO_SCHEMA": "Timeline.1",
        "metadata": {"kdenlive": {"version": "26.08.3"}},
        "tracks": {"OTIO_SCHEMA": "Stack.1", "children": tracks},
    }


class SourceAudioRoundtripTests(unittest.TestCase):
    def setUp(self):
        # Existing source fixture explicitly uses synthetic bytes. Only the
        # decoder is substituted here; media paths and SHA checks are real.
        self.case = original_fixture.PortableOtioTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.request["bindings"].append({
            **self.case.request["bindings"][1],
            "scene_id": original_fixture.ID(12), "source_start_frame": 60,
        })
        probe = patch.object(bridge, "file_probe", return_value=original_fixture.VIDEO_INFO)
        probe.start()
        self.addCleanup(probe.stop)

    def export(self, *, audio=True):
        options = {"firstparty_audio": audio_fixture.PROBE} if audio else {}
        bridge.export_portable(self.case.src, self.case.request, self.case.out, **options)
        self.timeline = self.case.read()
        self.manifest = json.loads((self.case.out / "bridge-manifest.json").read_bytes())

    def preview(self, initial=None, edited=None):
        if initial is None:
            initial = native_return(self.timeline, self.case.out)
        if edited is None:
            edited = copy.deepcopy(initial)
            for track in edited["tracks"]["children"]:
                track["children"][2]["source_range"]["duration"]["value"] = 15.0
        raw = self.case.root / "native-raw"
        raw.mkdir()
        first, last = raw / "received.otio", raw / "edited.otio"
        first.write_bytes(json.dumps(initial).encode())
        last.write_bytes(json.dumps(edited).encode())
        originals = {path: path.read_bytes() for path in (
            first, last, self.case.out / "timeline.otio",
            self.case.out / "bridge-manifest.json", self.case.out / "source-project.json")}
        request = {
            "schema": bridge.KDENLIVE_REQUEST_SCHEMA,
            "capsule_manifest_sha256": hashlib.sha256(
                originals[self.case.out / "bridge-manifest.json"]).hexdigest(),
            "received_otio": first.name, "received_sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
            "edited_otio": last.name, "edited_sha256": hashlib.sha256(last.read_bytes()).hexdigest(),
        }
        result = bridge.preview_kdenlive_cut(
            self.case.out, raw, request, self.case.out / "source-project.json",
            authorized_media_roots=(self.case.out,))
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        return result

    def test_selected_source_audio_is_packaged_and_bound_by_the_original_manifest(self):
        self.export()
        self.assertEqual([t["kind"] for t in self.timeline["tracks"]["children"]], ["Video", "Audio"])
        descriptor = self.manifest["firstparty_audio_track"]
        self.assertEqual(descriptor, self.timeline["metadata"]["motionwright"]["firstparty_audio_track"])
        self.assertEqual(self.manifest["otio_sha256"],
                         hashlib.sha256((self.case.out / "timeline.otio").read_bytes()).hexdigest())
        with zipfile.ZipFile(self.case.out / "portable-cut.zip") as archive:
            self.assertEqual(archive.read("timeline.otio"), (self.case.out / "timeline.otio").read_bytes())
            self.assertEqual(archive.read("bridge-manifest.json"),
                             (self.case.out / "bridge-manifest.json").read_bytes())
        baseline = bridge.import_preview(self.case.out, "timeline.otio",
                                         self.case.out / "source-project.json")
        self.assertEqual(baseline["native_change_proposals"], [])
        self.assertIs(baseline["edit_imported"], False)
        self.assertIs(descriptor["actual_output_audio_pcm_verified"], False)
        self.assertIs(descriptor["release_authorized"], False)

    def test_paired_native_audio_trim_returns_one_scene_change_without_altering_originals(self):
        self.export()
        result = self.preview()
        self.assertEqual(result["status"], "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED")
        self.assertEqual(result["native_change_proposals"], [{
            "type": "set_scene_duration", "scene_id": original_fixture.ID(12),
            "duration": {"num": "1", "den": "2"},
        }])
        self.assertIs(result["native_final_frames_verified"], False)
        self.assertIs(result["publication_approved"], False)

    def test_native_audio_track_loss_is_rejected(self):
        self.export()
        initial = native_return(self.timeline, self.case.out)
        edited = copy.deepcopy(initial)
        edited["tracks"]["children"].pop()
        result = self.preview(initial, edited)
        self.assertEqual(result["status"], "REJECTED_LOSS")
        self.assertEqual(result["rejection_code"], "track_set_changed")
        self.assertEqual(result["native_change_proposals"], [])

    def test_video_only_native_trim_cannot_hide_untrimmed_audio(self):
        self.export()
        initial = native_return(self.timeline, self.case.out)
        edited = copy.deepcopy(initial)
        edited["tracks"]["children"][0]["children"][2]["source_range"]["duration"]["value"] = 15.0
        result = self.preview(initial, edited)
        self.assertEqual(result["status"], "REJECTED_LOSS")
        self.assertEqual(result["rejection_code"], "source_audio_intervals_changed")

    def test_native_audio_substitution_is_rejected_before_any_foreign_media_is_opened(self):
        self.export()
        initial = native_return(self.timeline, self.case.out)
        edited = copy.deepcopy(initial)
        audio = edited["tracks"]["children"][1]["children"][0]
        audio["media_references"]["DEFAULT_MEDIA"]["target_url"] = "https://invalid.example/audio.mp4"
        result = self.preview(initial, edited)
        self.assertEqual(result["status"], "REJECTED_LOSS")
        self.assertEqual(result["rejection_code"], "source_audio_intervals_changed")

    def test_legacy_video_source_does_not_implicitly_admit_an_extra_audio_track(self):
        self.export(audio=False)
        self.assertNotIn("firstparty_audio_track", self.manifest)
        initial = native_return(self.timeline, self.case.out)
        extra = copy.deepcopy(initial["tracks"]["children"][0])
        extra["kind"] = "Audio"
        initial["tracks"]["children"].append(extra)
        result = self.preview(initial, copy.deepcopy(initial))
        self.assertEqual(result["status"], "REJECTED_LOSS")
        self.assertEqual(result["rejection_code"], "track_set_changed")

    def test_invalid_audio_measurement_stops_before_the_existing_media_probe(self):
        with patch.object(bridge, "file_probe") as decode:
            with self.assertRaises(audio_fixture.AudioTrackRejected):
                bridge.export_portable(self.case.src, self.case.request, self.case.out,
                                       firstparty_audio={**audio_fixture.PROBE, "channels": True})
        decode.assert_not_called()
        self.assertFalse(self.case.out.exists())


    def test_legacy_companion_tracks_admit_only_common_reorder_and_end_trim(self):
        self.export()
        edited = copy.deepcopy(self.timeline)
        for track in edited["tracks"]["children"]:
            clips = track["children"]
            clips[2]["source_range"]["duration"]["value"] = 15.0
            track["children"] = [clips[1], clips[0], clips[2]]
        (self.case.out / "paired-edit.otio").write_bytes(json.dumps(edited).encode())
        result = bridge.import_preview(self.case.out, "paired-edit.otio",
                                       self.case.out / "source-project.json")
        self.assertEqual(result["native_change_proposals"], [
            {"type": "move_scene", "scene_id": original_fixture.ID(11), "to_index": 0},
            {"type": "set_scene_duration", "scene_id": original_fixture.ID(12),
             "duration": {"num": "1", "den": "2"}},
        ])
        self.assertEqual(self.case.read(), self.timeline)
        self.assertIs(result["edit_imported"], False)

    def test_legacy_companion_edit_cannot_hide_audio_drift_or_rewrite_the_original(self):
        self.export()
        edited = copy.deepcopy(self.timeline)
        edited["tracks"]["children"][0]["children"][2]["source_range"]["duration"]["value"] = 15.0
        path = self.case.out / "unpaired-edit.otio"
        path.write_bytes(json.dumps(edited).encode())
        with self.assertRaises(bridge.BridgeRefused):
            bridge.import_preview(self.case.out, path.name, self.case.out / "source-project.json")
        edited["tracks"]["children"][1]["children"][2]["source_range"]["duration"]["value"] = 15.0
        path.write_bytes(json.dumps(edited).encode())
        (self.case.out / "timeline.otio").write_bytes(json.dumps(edited).encode())
        with self.assertRaisesRegex(bridge.BridgeRefused, "Original audio timeline changed"):
            bridge.import_preview(self.case.out, path.name, self.case.out / "source-project.json")


class SourceAudioGuiBoundaryTests(unittest.TestCase):
    def test_audio_shortcut_is_limited_to_the_two_owned_route_steps(self):
        ui = actions.AtspiSession.__new__(actions.AtspiSession)
        ui.pid = 41
        events, commands = [], []
        ui.journal = actions.ActionJournal(events.append, lambda: None)

        def run(argv, **kwargs):
            commands.append(argv)
            stdout = "17" if argv[1] == "getwindowfocus" else "41"
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
        with patch.object(actions.subprocess, "run", side_effect=run):
            ui.key("edit.audio.select-audio-track1", 0x0031, audio_track=True)
        keys = [argv for argv in commands if argv[1] == "key"]
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0][-1], "alt+1")
        self.assertIn("--window", keys[0])
        with patch.object(actions.subprocess, "run") as native:
            with self.assertRaises(actions.GuiRejected):
                ui.key("unrelated.select-audio-track1", 0x0031, audio_track=True)
        native.assert_not_called()

    def test_source_audio_refresh_is_required_before_the_initial_model_barrier_and_save(self):
        first = SimpleNamespace(ui=Mock(), guard=Mock())
        records = SimpleNamespace(root=Path("owned"), document=Mock())
        with patch.object(runner, "digest", return_value="a" * 64), \
             patch.object(runner, "preview_kdenlive_cut", return_value=save_fixture.original_preview()):
            runner.save_initial_firstparty_project(
                first, Path("capsule"), records, Path("initial.kdenlive"),
                Path("received.otio"), "b" * 64, firstparty_audio=True)
        self.assertEqual(first.ui.refresh_third_clip_playlist.call_args_list,
                         [call(), call(track_kind="Audio")])
        first.ui.save_as.assert_called_once()

    def test_uncertain_audio_refresh_never_reaches_export_or_save(self):
        first = SimpleNamespace(ui=Mock(), guard=Mock())
        records = SimpleNamespace(root=Path("owned"), document=Mock())
        original = actions.GuiRejected("synthetic uncertain audio resize")
        first.ui.refresh_third_clip_playlist.side_effect = [None, original]
        with self.assertRaises(actions.GuiRejected) as caught:
            runner.save_initial_firstparty_project(
                first, Path("capsule"), records, Path("initial.kdenlive"),
                Path("received.otio"), "b" * 64, firstparty_audio=True)
        self.assertIs(caught.exception, original)
        first.ui.export_otio.assert_not_called()
        first.ui.save_as.assert_not_called()


if __name__ == "__main__":
    unittest.main()
