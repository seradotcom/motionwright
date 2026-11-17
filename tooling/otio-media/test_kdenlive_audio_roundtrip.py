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




class SavedAudioCompanionReadbackTests(unittest.TestCase):
    """Synthetic native-XML controls, not editor/process or audio-fidelity evidence."""
    setUp = SourceAudioRoundtripTests.setUp
    export = SourceAudioRoundtripTests.export

    def xml(self, *, trimmed=False):
        import xml.etree.ElementTree as ET
        root = ET.Element("mlt", {"producer": "main_bin"})
        ET.SubElement(root, "profile", {"width": "320", "height": "180",
                                       "frame_rate_num": "30", "frame_rate_den": "1"})
        def prop(node, name, value):
            ET.SubElement(node, "property", {"name": name}).text = value
        for index, row in enumerate(self.manifest["bound_media"]):
            chain = ET.SubElement(root, "chain", {"id": f"source{index}"})
            prop(chain, "resource", row["path"])
            prop(chain, "mlt_service", "avformat-novalidate")
        black = ET.SubElement(root, "producer", {"id": "black", "in": "0", "out": "9090"})
        for key, value in {
            "length": "2147483647", "eof": "continue", "resource": "black",
            "aspect_ratio": "1", "mlt_service": "color", "kdenlive:playlistid": "black_track",
            "mlt_image_format": "rgba", "set.test_audio": "0",
        }.items():
            prop(black, key, value)
        total = 75 if trimmed else 90
        for kind, hide in (("audio", "video"), ("video", "audio")):
            playlist = ET.SubElement(root, "playlist", {"id": kind + "_clips"})
            for index, row in enumerate(self.manifest["bound_media"]):
                start = row["source_start_frame"]
                duration = 15 if trimmed and index == 2 else 30
                ET.SubElement(playlist, "entry", {
                    "producer": f"source{index}", "in": str(start), "out": str(start+duration-1)})
            ET.SubElement(root, "playlist", {"id": kind + "_empty"})
            tractor = ET.SubElement(root, "tractor", {
                "id": kind, "in": "0", "out": str(total-1)})
            if kind == "audio":
                prop(tractor, "kdenlive:audio_track", "1")
            for suffix in ("_clips", "_empty"):
                ET.SubElement(tractor, "track", {"producer": kind+suffix, "hide": hide})
        wrapper = ET.SubElement(root, "tractor", {"id": "wrapper", "in": "0", "out": str(total-1)})
        for producer in ("black", "audio", "video"):
            ET.SubElement(wrapper, "track", {"producer": producer})
        ET.SubElement(root, "playlist", {"id": "main_bin"})
        master = ET.SubElement(root, "tractor", {"id": "master", "in": "0", "out": str(total-1)})
        prop(master, "kdenlive:projectTractor", "1")
        ET.SubElement(master, "track", {"producer": "wrapper", "in": "0", "out": str(total-1)})
        return ET.tostring(root)

    def readback(self, raw, name="audio-native.kdenlive"):
        import kdenlive_readback
        path = self.case.out / name
        path.write_bytes(raw)
        manifest_raw = (self.case.out / "bridge-manifest.json").read_bytes()
        try:
            return kdenlive_readback.inspect_saved_kdenlive(
                path, hashlib.sha256(raw).hexdigest(), self.case.out,
                hashlib.sha256(manifest_raw).hexdigest())
        finally:
            self.assertEqual(path.read_bytes(), raw)

    def rejected_variant(self, raw, mutate, reason):
        import xml.etree.ElementTree as ET
        import kdenlive_readback
        tree = ET.fromstring(raw)
        mutate(tree)
        with self.assertRaisesRegex(kdenlive_readback.KdenliveReadbackRejected, reason):
            self.readback(ET.tostring(tree))

    def test_saved_three_track_wrapper_binds_audio_and_video_through_reopen_trim(self):
        import kdenlive_readback
        self.export()
        original = self.readback(self.xml())
        edited = self.readback(self.xml(trimmed=True), "audio-edited.kdenlive")
        self.assertEqual(original["observed_bound_audio_clip_count"], 3)
        self.assertTrue(original["native_audio_source_intervals_match_video"])
        self.assertFalse(original["audio_fidelity_accepted"])
        comparison = kdenlive_readback.compare_two_native_custody_snapshots(original, edited)
        self.assertEqual(len(comparison["bounded_native_end_trims_observed"]), 1)
        self.assertEqual(comparison["bounded_native_end_trims_observed"][0]["reopened_duration_frames"], 15)

    def single_source_xml(self, *, trimmed=False, split=False):
        import xml.etree.ElementTree as ET
        tree = ET.fromstring(self.xml(trimmed=trimmed))
        for chain in tree.findall("chain")[1:]:
            tree.remove(chain)
        for entry in tree.findall(".//playlist/entry"):
            entry.set("producer", "source0")
        if split:
            clone = copy.deepcopy(tree.find("./chain[@id='source0']"))
            clone.set("id", "video_source")
            for key, value in (("set.test_audio", "1"), ("set.test_image", "0")):
                ET.SubElement(clone, "property", {"name": key}).text = value
            tree.insert(2, clone)
            for entry in tree.findall(".//playlist[@id='video_clips']/entry"):
                entry.set("producer", "video_source")
        return ET.tostring(tree)

    def test_realistic_same_source_chain_split_preserves_paired_trim_identity(self):
        import kdenlive_readback
        self.export()
        original = self.readback(self.single_source_xml())
        edited = self.readback(self.single_source_xml(trimmed=True, split=True),
                               "split-edited.kdenlive")
        self.assertEqual(original["distinct_native_MLT_ID_count"], 11)
        self.assertEqual(edited["distinct_native_MLT_ID_count"], 12)
        self.assertEqual(edited["bound_audio_video_source_components"],
                         {"audio": ["source0"], "video": ["video_source"]})
        result = kdenlive_readback.compare_two_native_custody_snapshots(original, edited)
        self.assertTrue(result["observed_same_source_audio_video_component_split"])
        self.assertEqual(result["bounded_native_end_trims_observed"], [{
            "scene_id": original_fixture.ID(12),
            "initial_duration_frames": 30, "reopened_duration_frames": 15,
        }])
        self.assertFalse(result["external_editor_or_human_acceptance"])
        self.assertFalse(edited["audio_fidelity_accepted"])

    def test_audio_split_does_not_explain_an_unaccounted_extra_component(self):
        import xml.etree.ElementTree as ET
        import kdenlive_readback
        self.export()
        original = self.readback(self.single_source_xml())
        for split in (False, True):
            with self.subTest(source_split=split):
                tree = ET.fromstring(self.single_source_xml(trimmed=True, split=split))
                ET.SubElement(tree, "playlist", {"id": "unrelated_extra_component"})
                edited = self.readback(ET.tostring(tree), "extra-component.kdenlive")
                with self.assertRaisesRegex(kdenlive_readback.KdenliveReadbackRejected,
                                            "unaccounted_MLT_component_count_change"):
                    kdenlive_readback.compare_two_native_custody_snapshots(original, edited)

    def test_audio_split_does_not_accept_changed_project_geometry(self):
        import xml.etree.ElementTree as ET
        import kdenlive_readback
        self.export()
        original = self.readback(self.single_source_xml())
        tree = ET.fromstring(self.single_source_xml(trimmed=True, split=True))
        tree.find("profile").set("width", "640")
        edited = self.readback(ET.tostring(tree), "changed-geometry.kdenlive")
        with self.assertRaisesRegex(kdenlive_readback.KdenliveReadbackRejected,
                                    "lost_source_or_frame_clock"):
            kdenlive_readback.compare_two_native_custody_snapshots(original, edited)

    def test_split_video_chain_still_requires_the_original_media_bytes(self):
        self.export()
        (self.case.out / "foreign.mp4").write_bytes(b"foreign non-original source")
        def substitute(tree):
            tree.find("./chain[@id='video_source']/property[@name='resource']").text = "foreign.mp4"
        self.rejected_variant(self.single_source_xml(trimmed=True, split=True),
                              substitute, "playback_media_not_immutable_original_SHA")

    def test_audio_companion_cannot_be_admitted_by_a_video_only_capsule(self):
        import kdenlive_readback
        self.export(audio=False)
        with self.assertRaisesRegex(kdenlive_readback.KdenliveReadbackRejected, "unbound_visible_track"):
            self.readback(self.xml())

    def test_selected_audio_companion_cannot_disappear_from_saved_project(self):
        self.export()
        def remove_audio(tree):
            wrapper = tree.find(".//tractor[@id='wrapper']")
            wrapper.remove(wrapper.findall("track")[1])
        self.rejected_variant(self.xml(), remove_audio, "selected_audio_companion_missing")

    def test_native_audio_clip_sha_inpoint_duration_or_count_cannot_drift(self):
        self.export()
        for label, update in (
            ("source", {"producer": "black"}),
            ("inpoint", {"in": "1"}),
            ("duration", {"out": "28"}),
        ):
            with self.subTest(label=label):
                def mutate(tree, update=update):
                    tree.find(".//playlist[@id='audio_clips']/entry").attrib.update(update)
                reason = ("multiple_or_reused_black_background" if label == "source"
                          else "audio_video_source_or_interval")
                self.rejected_variant(self.xml(), mutate, reason)
        def remove_clip(tree):
            playlist = tree.find(".//playlist[@id='audio_clips']")
            playlist.remove(playlist.findall("entry")[-1])
        self.rejected_variant(self.xml(), remove_clip, "audio_companion_clip_or_lane_count")
        def false_originalurl(tree):
            import xml.etree.ElementTree as ET
            source = tree.find(".//chain[@id='source0']")
            resource = source.find("./property[@name='resource']")
            ET.SubElement(source, "property", {"name": "kdenlive:originalurl"}).text = resource.text
            resource.text = "black"
            source.find("./property[@name='mlt_service']").text = "color"
        self.rejected_variant(self.xml(), false_originalurl, "audio_profile_media_service_or_resource_changed")

    def test_extra_visible_track_and_extra_audio_lane_are_rejected(self):
        import xml.etree.ElementTree as ET
        self.export()
        def extra_video(tree):
            ET.SubElement(tree.find(".//tractor[@id='wrapper']"), "track", {"producer": "video"})
        self.rejected_variant(self.xml(), extra_video, "unbound_visible_track")
        def extra_audio_lane(tree):
            ET.SubElement(tree.find(".//tractor[@id='audio']"), "track",
                          {"producer": "audio_empty", "hide": "video"})
        self.rejected_variant(self.xml(), extra_audio_lane, "audio_companion_two_lane_profile")
        def duplicate_companion(tree):
            wrapper = tree.find(".//tractor[@id='wrapper']")
            wrapper.findall("track")[0].set("producer", "audio")
        self.rejected_variant(self.xml(), duplicate_companion, "audio_wrapper_shape_not_admitted")
        def foreign_hidden_first(tree):
            wrapper = tree.find(".//tractor[@id='wrapper']")
            wrapper.findall("track")[0].set("producer", "source0")
            wrapper.findall("track")[0].set("hide", "video")
        self.rejected_variant(self.xml(), foreign_hidden_first, "audio_wrapper_shape_not_admitted")
        def foreign_master_audio(tree):
            ET.SubElement(tree.find(".//tractor[@id='master']"), "track",
                          {"producer": "source0", "hide": "video"})
        self.rejected_variant(self.xml(), foreign_master_audio, "audio_profile_master_wrapper_not_unique")

    def test_audio_visibility_gap_and_container_interval_controls_are_rejected(self):
        import xml.etree.ElementTree as ET
        self.export()
        def visible_audio(tree):
            tree.find(".//tractor[@id='audio']/track").set("hide", "audio")
        self.rejected_variant(self.xml(), visible_audio, "audio_companion_visibility_or_playlist")
        def sounding_video_lane(tree):
            tree.find(".//tractor[@id='video']/track").attrib.pop("hide")
        self.rejected_variant(self.xml(), sounding_video_lane, "audio_profile_video_lanes_not_silent")
        def foreign_video_audio(tree):
            ET.SubElement(tree.find(".//tractor[@id='video']"), "track",
                          {"producer": "source0", "hide": "video"})
        self.rejected_variant(self.xml(), foreign_video_audio, "audio_profile_video_lanes_not_silent")
        def gap(tree):
            ET.SubElement(tree.find(".//playlist[@id='audio_clips']"), "blank", {"length": "1"})
        self.rejected_variant(self.xml(), gap, "audio_companion_visibility_or_playlist")
        def shifted_container(tree):
            tree.find(".//tractor[@id='audio']").set("in", "1")
        self.rejected_variant(self.xml(), shifted_container, "container_interval_not_full_editorial_span")
        def property_override(tree):
            ET.SubElement(tree.find(".//tractor[@id='audio']"), "property", {"name": "out"}).text = "88"
        self.rejected_variant(self.xml(), property_override, "playback_property_override")

    def test_audio_descriptor_and_original_timeline_cannot_be_coordinately_forged(self):
        import kdenlive_readback
        self.export()
        forged = copy.deepcopy(self.manifest["firstparty_audio_track"])
        forged["actual_output_audio_pcm_verified"] = True
        self.timeline["metadata"]["motionwright"]["firstparty_audio_track"] = forged
        raw = json.dumps(self.timeline).encode()
        (self.case.out / "timeline.otio").write_bytes(raw)
        self.manifest["otio_sha256"] = hashlib.sha256(raw).hexdigest()
        self.manifest["firstparty_audio_track"] = forged
        (self.case.out / "bridge-manifest.json").write_bytes(json.dumps(self.manifest).encode())
        with self.assertRaisesRegex(kdenlive_readback.KdenliveReadbackRejected, "firstparty_audio_original_binding_changed"):
            self.readback(self.xml())

    def test_changed_original_audio_timeline_fails_before_native_graph_admission(self):
        import kdenlive_readback
        self.export()
        path = self.case.out / "timeline.otio"
        path.write_bytes(path.read_bytes()+b" ")
        with self.assertRaisesRegex(kdenlive_readback.KdenliveReadbackRejected, "content_changed_from_expected_SHA"):
            self.readback(self.xml())

if __name__ == "__main__":
    unittest.main()
