"""Offline, effect-free conformance controls for one opt-in first-party AAC track."""
import copy
import unittest
from kdenlive_firstparty_audio_track import (
    AUDIO_SCHEMA, AudioTrackRejected, attach_firstparty_audio,
    require_original_audio_track, source_profile,
)

SHA = "1"*64
SCENES = [f"00000000-0000-4000-8000-{i:012x}" for i in (10,11,12)]
ASSET = "00000000-0000-4000-8000-000000000050"
PROBE = {
    "schema": "motionwright.native.kdenlive-firstparty-audio-source/1",
    "status": "SOURCE_AUDIO_ONLY", "codec": "aac",
    "sample_rate": 48000, "channels": 1,
    "synthesized_tone_hz": 997,
    "native_audio_fidelity_accepted": False,
    "human_acceptance": False, "release_authorized": False,
}


def time(start, count):
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": {"OTIO_SCHEMA": "RationalTime.1",
                       "value": float(start), "rate": 30.0},
        "duration": {"OTIO_SCHEMA": "RationalTime.1",
                     "value": float(count), "rate": 30.0},
    }


def video_source():
    bindings = []
    clips = []
    for index, scene in enumerate(SCENES):
        binding = {
            "scene_id": scene, "asset_id": ASSET, "sha256": SHA,
            "source_start_frame": index * 30,
        }
        bindings.append(binding)
        clips.append({
            "OTIO_SCHEMA": "Clip.1", "name": f"Scene {index}",
            "enabled": True, "effects": [], "markers": [],
            "metadata": {"motionwright": {
                "scene_id": scene, "asset_id": ASSET,
                "media_sha256": SHA, "source_start_frame": index * 30,
                "media_rate": 30,
            }},
            "source_range": time(index * 30, 30),
            "media_reference": {
                "OTIO_SCHEMA": "ExternalReference.1",
                "target_url": f"media/{SHA}.mp4",
                "available_range": time(0,90),
                "metadata": {"motionwright": {
                    "asset_id": ASSET, "asset_sha256": SHA,
                    "owner_rights_attested": True,
                }},
            },
        })
    timeline = {
        "OTIO_SCHEMA": "Timeline.1", "name": "Original source",
        "tracks": {"OTIO_SCHEMA": "Stack.1", "children": [{
            "OTIO_SCHEMA": "Track.1", "kind": "Video",
            "children": clips, "name": "V1"
        }]},
    }
    return timeline, bindings


class AudioTrackSourceContractTests(unittest.TestCase):
    def test_opt_in_timeline_adds_exact_three_source_bound_audio_clips(self):
        timeline, bindings=video_source()
        original = copy.deepcopy(timeline["tracks"]["children"][0])
        descriptor = attach_firstparty_audio(timeline, bindings, PROBE)
        self.assertTrue(require_original_audio_track(timeline, descriptor, bindings))
        self.assertEqual(timeline["tracks"]["children"][0], original)
        self.assertEqual(len(timeline["tracks"]["children"]), 2)
        self.assertEqual(timeline["tracks"]["children"][1]["kind"], "Audio")
        self.assertEqual(descriptor["schema"], AUDIO_SCHEMA)
        self.assertEqual(descriptor["audio_clip_count"], 3)
        self.assertEqual(descriptor["scene_ids"], SCENES)
        self.assertIs(descriptor["actual_output_audio_pcm_verified"], False)
        self.assertIs(descriptor["native_audio_fidelity_accepted"], False)
        self.assertIs(descriptor["release_authorized"], False)

    def test_source_profile_rejects_any_forged_or_nonexact_codec_measurement(self):
        for edit in [
            {"codec": "opus"}, {"sample_rate": 44100}, {"channels": 2},
            {"synthesized_tone_hz": 998}, {"status": "NATIVE_VERIFIED"},
            {"native_audio_fidelity_accepted": True},
            {"release_authorized": True}, {"human_acceptance": True},
            {"unknown_field": "yes"},
        ]:
            with self.subTest(edit=edit):
                with self.assertRaises(AudioTrackRejected):
                    source_profile({**PROBE, **edit})

    def test_rejects_duplicate_sources_or_unbound_scenes(self):
        for mutate in [
            lambda x,b: b[1].update(scene_id=b[0]["scene_id"]),
            lambda x,b: b[2].update(sha256="2"*64),
            lambda x,b: b[1].update(asset_id="x"),
            lambda x,b: b[2].update(source_start_frame=29),
            lambda x,b: x["tracks"]["children"][0]["children"][1]["metadata"]["motionwright"].update(media_sha256="2"*64),
            lambda x,b: x["tracks"]["children"][0]["children"][2]["source_range"]["duration"].update(value=31.0),
            lambda x,b: x["tracks"]["children"][0]["children"][0]["media_reference"].update(target_url="forged.mp4"),
            lambda x,b: x["tracks"]["children"][0]["children"][1].update(enabled=False),
        ]:
            timeline, bindings=video_source()
            mutate(timeline,bindings)
            with self.subTest(mutate=mutate):
                with self.assertRaises(AudioTrackRejected):
                    attach_firstparty_audio(timeline,bindings,PROBE)

    def test_existing_second_track_never_silently_duplicated(self):
        timeline,bindings=video_source()
        attach_firstparty_audio(timeline,bindings,PROBE)
        with self.assertRaisesRegex(AudioTrackRejected, "one_video_track"):
            attach_firstparty_audio(timeline,bindings,PROBE)

    def test_audio_track_mutations_are_rejected_in_original(self):
        mutations = [
            lambda a:a["children"][1]["source_range"]["duration"].update(value=29.0),
            lambda a:a["children"][0]["media_reference"].update(target_url="fake.mp4"),
            lambda a:a["children"][1]["metadata"]["motionwright"].update(scene_id=SCENES[2]),
            lambda a:a.update(enabled=False),
            lambda a:a.update(kind="Video"),
            lambda a:a["children"][1].update(effects=[{"fake":1}]),
            lambda a:a["children"].pop(),
        ]
        for mutate in mutations:
            timeline,bindings=video_source()
            descriptor=attach_firstparty_audio(timeline,bindings,PROBE)
            mutate(timeline["tracks"]["children"][1])
            with self.subTest(mutate=mutate):
                with self.assertRaises(AudioTrackRejected):
                    require_original_audio_track(timeline,descriptor,bindings)

    def test_descriptor_forgery_never_grants_native_acceptance(self):
        mutations = [
            {"native_otio_audio_track_verified":True},
            {"actual_output_audio_pcm_verified":True},
            {"audio_clip_count":4}, {"source_sha256":"3"*64},
            {"scene_ids":[SCENES[2], SCENES[0], SCENES[1]]},
        ]
        for mutate in mutations:
            timeline,bindings=video_source()
            descriptor=attach_firstparty_audio(timeline,bindings,PROBE)
            with self.subTest(mutate=mutate):
                with self.assertRaises(AudioTrackRejected):
                    require_original_audio_track(timeline,{**descriptor,**mutate},bindings)

    def test_video_original_does_not_gain_audio_by_default(self):
        timeline,bindings=video_source()
        self.assertEqual(len(timeline["tracks"]["children"]),1)
        with self.assertRaises(AudioTrackRejected):
            require_original_audio_track(timeline,{**source_profile(PROBE),
                "source_sha256":SHA,"scene_ids":SCENES,
                "audio_track_index":1,"audio_clip_count":3,"video_clip_count":3,
                "source_fps":30,"native_otio_audio_track_verified":False,
                "actual_output_audio_pcm_verified":False},bindings)


    def test_coordinated_audio_and_video_mutation_cannot_replace_original_bindings(self):
        for field in ("url", "range", "availability", "asset", "scene"):
            timeline, bindings = video_source()
            descriptor = attach_firstparty_audio(timeline, bindings, PROBE)
            for track in timeline["tracks"]["children"]:
                clip = track["children"][1]
                if field == "url":
                    clip["media_reference"]["target_url"] = "forged.mp4"
                elif field == "range":
                    clip["source_range"]["duration"]["value"] = 29.0
                elif field == "availability":
                    clip["media_reference"]["available_range"]["duration"]["value"] = 120.0
                elif field == "asset":
                    clip["media_reference"]["metadata"]["motionwright"]["asset_id"] = SCENES[0]
                else:
                    clip["metadata"]["motionwright"]["scene_id"] = SCENES[2]
            with self.subTest(field=field), self.assertRaises(AudioTrackRejected):
                require_original_audio_track(timeline, descriptor, bindings)

    def test_every_descriptor_profile_and_acceptance_field_is_closed(self):
        edits = [
            {"release_authorized": True}, {"human_acceptance": True},
            {"native_audio_fidelity_accepted": True}, {"source_codec": "opus"},
            {"sample_rate": 44100}, {"channels": True}, {"tone_hz": 998},
            {"audio_track_index": True}, {"video_clip_count": 3.0},
            {"source_fps": 30.0}, {"unexpected": None},
            {"scene_ids": [[], SCENES[1], SCENES[2]]},
        ]
        for edit in edits:
            timeline, bindings = video_source()
            descriptor = attach_firstparty_audio(timeline, bindings, PROBE)
            with self.subTest(edit=edit), self.assertRaises(AudioTrackRejected):
                require_original_audio_track(timeline, {**descriptor, **edit}, bindings)

    def test_measurement_json_types_cannot_impersonate_numbers_or_false(self):
        for field, value in [
            ("channels", True), ("sample_rate", 48000.0),
            ("synthesized_tone_hz", 997.0), ("human_acceptance", 0),
            ("release_authorized", 0), ("native_audio_fidelity_accepted", 0),
        ]:
            with self.subTest(field=field), self.assertRaises(AudioTrackRejected):
                source_profile({**PROBE, field: value})

    def test_malformed_metadata_uses_the_closed_rejection_type(self):
        for value in (None, [], "not metadata"):
            timeline, bindings = video_source()
            descriptor = attach_firstparty_audio(timeline, bindings, PROBE)
            timeline["tracks"]["children"][0]["children"][0]["metadata"] = value
            with self.subTest(value=value), self.assertRaises(AudioTrackRejected):
                require_original_audio_track(timeline, descriptor, bindings)

    def test_failed_audio_admission_preserves_the_entire_source_timeline(self):
        for bad_measurement in ({**PROBE, "human_acceptance": True}, {}):
            timeline, bindings = video_source()
            original = copy.deepcopy(timeline)
            with self.assertRaises(AudioTrackRejected):
                attach_firstparty_audio(timeline, bindings, bad_measurement)
            self.assertEqual(timeline, original)
        timeline, bindings = video_source()
        bindings[2]["source_start_frame"] = 0
        original = copy.deepcopy(timeline)
        with self.assertRaises(AudioTrackRejected):
            attach_firstparty_audio(timeline, bindings, PROBE)
        self.assertEqual(timeline, original)


if __name__ == "__main__":
    unittest.main()
