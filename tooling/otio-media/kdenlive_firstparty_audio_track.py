"""Closed, source-bound first-party Kdenlive AAC/997Hz OTIO audio track.

This opt-in profile carries the measured MP4 audio beside its video intervals.
It does not admit general voice/music cues or claim a native render, audio
fidelity, human acceptance or publication authority.
"""
from __future__ import annotations

import copy
import re

AUDIO_SCHEMA = "motionwright.otio.firstparty-aac-997hz-track/1"
PROBE_SCHEMA = "motionwright.native.kdenlive-firstparty-audio-source/1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE = {
    "schema": PROBE_SCHEMA, "status": "SOURCE_AUDIO_ONLY",
    "codec": "aac", "sample_rate": 48000, "channels": 1,
    "synthesized_tone_hz": 997,
    "native_audio_fidelity_accepted": False,
    "human_acceptance": False, "release_authorized": False,
}


class AudioTrackRejected(ValueError):
    pass


def require(ok: object, reason: str) -> None:
    if not ok:
        raise AudioTrackRejected(reason)


def same_json(left, right) -> bool:
    """Preserve JSON types: booleans and floats cannot impersonate integers."""
    if type(left) is not type(right):
        return False
    if type(right) is dict:
        return (set(left) == set(right)
                and all(same_json(left[key], value) for key, value in right.items()))
    if type(right) is list:
        return len(left) == len(right) and all(
            same_json(a, b) for a, b in zip(left, right))
    return left == right


def source_profile(measured: dict) -> dict:
    require(same_json(measured, _SOURCE),
            "firstparty_aac_source_measurement_not_exact")
    return {
        "schema": AUDIO_SCHEMA, "source_codec": "aac",
        "sample_rate": 48000, "channels": 1, "tone_hz": 997,
        "native_audio_fidelity_accepted": False, "human_acceptance": False,
        "release_authorized": False,
    }


def _time(value, start: int, duration: int, rate: int = 30) -> bool:
    return same_json(value, {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": {"OTIO_SCHEMA": "RationalTime.1",
                       "value": float(start), "rate": float(rate)},
        "duration": {"OTIO_SCHEMA": "RationalTime.1",
                     "value": float(duration), "rate": float(rate)},
    })


def _metadata(value: dict) -> dict:
    metadata = value.get("metadata")
    require(type(metadata) is dict
            and type(metadata.get("motionwright")) is dict,
            "audio_original_metadata_malformed")
    return metadata["motionwright"]


def _bound_video(timeline: dict, bindings: list[dict], *, allow_edit: bool = False) -> tuple[list[dict], str]:
    require(type(timeline) is dict and timeline.get("OTIO_SCHEMA") == "Timeline.1",
            "original_video_timeline_schema")
    stack = timeline.get("tracks")
    require(type(stack) is dict and stack.get("OTIO_SCHEMA") == "Stack.1"
            and type(stack.get("children")) is list
            and len(stack["children"]) == 1,
            "audio_requires_original_one_video_track")
    video = stack["children"][0]
    require(type(video) is dict and video.get("OTIO_SCHEMA") == "Track.1"
            and video.get("kind") == "Video"
            and video.get("enabled", True) is True
            and video.get("effects", []) == []
            and video.get("markers", []) == []
            and type(video.get("children")) is list
            and len(video["children"]) == 3
            and type(bindings) is list and len(bindings) == 3,
            "firstparty_audio_requires_three_original_video_clips")
    require(all(type(item) is dict and type(item.get("scene_id")) is str
                and type(item.get("source_start_frame")) is int
                and item["source_start_frame"] == 30 * index
                for index, item in enumerate(bindings)),
            "audio_original_binding_clock_invalid")
    by_scene = {item["scene_id"]: item for item in bindings}
    require(len(by_scene) == 3, "audio_original_binding_scenes_not_unique")
    pairs = list(zip(video["children"], bindings))
    if allow_edit:
        pairs = []
        for clip in video["children"]:
            require(type(clip) is dict, "audio_video_clip_malformed")
            item = by_scene.get(_metadata(clip).get("scene_id"))
            require(item is not None, "audio_edit_scene_not_original")
            pairs.append((clip, item))
    values, scene_ids = [], []
    source_sha = asset_id = None
    for index, (clip, item) in enumerate(pairs):
        require(type(clip) is dict and type(item) is dict
                and clip.get("OTIO_SCHEMA") == "Clip.1"
                and clip.get("enabled") is True
                and clip.get("effects", []) == []
                and clip.get("markers", []) == [],
                "audio_cannot_mirror_unsupported_video_clip")
        ref = clip.get("media_reference")
        require(type(ref) is dict and ref.get("OTIO_SCHEMA") == "ExternalReference.1",
                "audio_cannot_mirror_missing_media_reference")
        sha = item.get("sha256")
        require(type(sha) is str and _SHA.fullmatch(sha) is not None
                and ref.get("target_url") == f"media/{sha}.mp4",
                "audio_source_sha_not_exact")
        scene_id, current_asset = item.get("scene_id"), item.get("asset_id")
        meta, refmeta = _metadata(clip), _metadata(ref)
        require(type(scene_id) is str and 8 <= len(scene_id) <= 80
                and scene_id not in scene_ids
                and type(current_asset) is str and 8 <= len(current_asset) <= 80
                and meta.get("scene_id") == scene_id
                and meta.get("asset_id") == current_asset
                and meta.get("media_sha256") == sha
                and refmeta.get("asset_id") == current_asset
                and refmeta.get("asset_sha256") == sha
                and refmeta.get("owner_rights_attested") is True,
                "audio_scene_identity_not_original_bound")
        duration = 30
        if allow_edit:
            time_range = clip.get("source_range")
            stamp = time_range.get("duration") if type(time_range) is dict else None
            value = stamp.get("value") if type(stamp) is dict else None
            require(type(value) is float and 1 <= value <= 30
                    and value.is_integer(), "audio_edit_duration_not_bounded_end_trim")
            duration = int(value)
        require(_time(clip.get("source_range"), item["source_start_frame"], duration)
                and _time(ref.get("available_range"), 0, 90),
                "audio_source_frame_clock_not_three_intervals")
        require(source_sha is None or sha == source_sha,
                "audio_source_mixed_media_digest")
        require(asset_id is None or current_asset == asset_id,
                "audio_source_mixed_asset_identity")
        source_sha, asset_id = sha, current_asset
        scene_ids.append(scene_id)
        values.append(clip)
    return values, source_sha


def _audio_track(clips: list[dict], media_sha: str) -> dict:
    audio_clips = []
    for clip in clips:
        duplicate = copy.deepcopy(clip)
        duplicate["metadata"] = {"motionwright": {
            "scene_id": clip["metadata"]["motionwright"]["scene_id"],
            "audio_track_schema": AUDIO_SCHEMA,
            "source_sha256": media_sha,
            "native_audio_fidelity_accepted": False,
        }}
        audio_clips.append(duplicate)
    return {
        "OTIO_SCHEMA": "Track.1", "kind": "Audio",
        "name": "First-party AAC source audio", "children": audio_clips,
        "metadata": {}, "effects": [], "markers": [], "enabled": True,
    }


def _descriptor(bindings: list[dict], media_sha: str) -> dict:
    return {
        **source_profile(_SOURCE),
        "source_sha256": media_sha,
        "scene_ids": [item["scene_id"] for item in bindings],
        "audio_track_index": 1, "audio_clip_count": 3, "video_clip_count": 3,
        "source_fps": 30, "native_otio_audio_track_verified": False,
        "actual_output_audio_pcm_verified": False,
    }


def attach_firstparty_audio(timeline: dict, bindings: list[dict],
                            measured_audio: dict) -> dict:
    """Append only after the complete original profile and source are admitted."""
    source_profile(measured_audio)
    clips, media_sha = _bound_video(timeline, bindings)
    audio, descriptor = _audio_track(clips, media_sha), _descriptor(bindings, media_sha)
    timeline["tracks"]["children"].append(audio)
    return descriptor


def require_original_audio_track(timeline: dict, descriptor: dict,
                                 bindings: list[dict]) -> bool:
    """Compare BOTH original tracks against authoritative source/asset bindings."""
    return _require_audio_track(timeline, descriptor, bindings, allow_edit=False)


def require_edited_audio_track(timeline: dict, descriptor: dict,
                              bindings: list[dict]) -> bool:
    """Admit only shared scene reorder/end trims; keep the original descriptor."""
    return _require_audio_track(timeline, descriptor, bindings, allow_edit=True)


def _require_audio_track(timeline: dict, descriptor: dict,
                         bindings: list[dict], *, allow_edit: bool) -> bool:
    require(type(timeline) is dict and timeline.get("OTIO_SCHEMA") == "Timeline.1",
            "original_video_timeline_schema")
    stack = timeline.get("tracks")
    require(type(stack) is dict and stack.get("OTIO_SCHEMA") == "Stack.1"
            and type(stack.get("children")) is list
            and len(stack["children"]) == 2,
            "original_av_track_count_invalid")
    video, audio = stack["children"]
    original_view = {**timeline, "tracks": {**stack, "children": [video]}}
    clips, media_sha = _bound_video(original_view, bindings, allow_edit=allow_edit)
    require(same_json(descriptor, _descriptor(bindings, media_sha)),
            "audio_track_descriptor_not_exact")
    require(same_json(audio, _audio_track(clips, media_sha)),
            "original_av_audio_track_not_exact")
    return True
