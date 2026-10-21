#!/usr/bin/env python3
"""Independent, read-only decoded-content gate for one saved 180-frame Film.

The caller first authenticates the original source project, first-party voice
WAV, both native Host result receipts and the H264/AAC MP4 through independent
SHA-256 values. This inspection does NOT dispatch or approve a native job,
reconcile author rights, replace a source renderer or approve perceptual quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

from native_pcm_source_fidelity import inspect_source_pcm16_stereo_content

FRAME_COUNT = 180
FRAME_RATE = 30
WIDTH = 1920
HEIGHT = 1080
PCM_RATE = 48_000
PCM_FRAMES = 288_000
FRAME_BYTES = WIDTH * HEIGHT * 3
AUDIO_BYTES_MAX = (PCM_FRAMES + 1024) * 4
MAX_SOURCE_BYTES = 12 * 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_MP4_BYTES = 256 * 1024 * 1024
SELECTED = (0, 5, 6, 26, 27, 32, 33, 71, 72, 89, 90, 179)
HEX = re.compile(r"[0-9a-f]{64}\Z")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


class OriginalMediaMismatch(ValueError):
    """The physical original does not satisfy its authenticated saved source."""


def require(test: object, why: str) -> None:
    if not test:
        raise OriginalMediaMismatch(why)


def digest(path: Path, maximum: int, expected: str) -> str:
    require(isinstance(expected, str) and HEX.fullmatch(expected) is not None,
            "original_sha256_missing")
    require(path.is_absolute() and not path.is_symlink() and path.is_file(),
            "original_media_not_absolute_regular")
    for parent in path.parents:
        require(not parent.is_symlink(), "original_media_ancestor_is_symlink")
    before = path.stat()
    require(0 < before.st_size <= maximum, "original_media_byte_budget")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    after = path.stat()
    require((before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns),
            "original_media_changed_during_read")
    result = sha.hexdigest()
    require(result == expected, "original_media_sha256_changed")
    return result


def load_json(path: Path, expected: str, limit: int = MAX_RECEIPT_BYTES) -> dict:
    digest(path, limit, expected)

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
        out = {}
        for key, value in pairs:
            require(key not in out, "original_receipt_duplicate_json_key")
            out[key] = value
        return out

    data = json.loads(path.read_bytes(), object_pairs_hook=unique_pairs,
                      parse_constant=lambda _x: require(False, "original_json_nonfinite"))
    require(type(data) is dict, "original_json_object_required")
    return data


def source_identity(source: dict, motion: dict, master: dict, wave_sha: str,
                    mp4_sha: str) -> dict:
    pid = source.get("id")
    generation = source.get("generation")
    revision = source.get("revision")
    require(type(pid) is str and UUID.fullmatch(pid) is not None
            and type(generation) is str and UUID.fullmatch(generation) is not None
            and type(revision) is int and revision >= 1,
            "invalid_canonical_source_project_identity")
    resource = f"project:{pid}"
    for receipt in (motion, master):
        require(receipt.get("project_resource") == resource
                and receipt.get("generation") == generation
                and receipt.get("revision") == revision,
                "media_receipt_does_not_bind_saved_project_revision")
    profiles = source.get("deliverables")
    scenes = source.get("scenes")
    require(type(profiles) is list and len(profiles) >= 1
            and all(type(profile) is dict for profile in profiles),
            "original_source_deliverable_inventory_invalid")
    profile_ids = [profile.get("id") for profile in profiles]
    require(all(type(profile_id) is str and UUID.fullmatch(profile_id) is not None
                for profile_id in profile_ids)
            and len(set(profile_ids)) == len(profile_ids),
            "original_source_deliverable_ids_invalid_or_duplicated")
    require(type(scenes) is list and len(scenes) == 2
            and [len(scene.get("nodes", [])) for scene in scenes] == [3, 1],
            "180frame_original_source_scene_graph_changed")
    profile = profiles[0]
    ratio = lambda num, den: {"num": str(num), "den": str(den)}
    require(profile.get("width") == WIDTH
            and profile.get("height") == HEIGHT
            and profile.get("frame_rate") == ratio(FRAME_RATE, 1)
            and profile.get("captions") is True
            and profile.get("burn_in_captions") is True,
            "saved_source_caption_output_profile_changed")
    require(
        [(scene.get("start"), scene.get("duration")) for scene in scenes] == [
            (ratio(0, 1), ratio(3, 1)), (ratio(3, 1), ratio(3, 1))
        ],
        "saved_two_scene_original_rational_clock_changed"
    )
    profile_id = profile.get("id")
    require(profile_id == motion.get("deliverable_id")
            and profile_id == master.get("deliverable_id")
            and UUID.fullmatch(profile_id or "") is not None,
            "original_deliverable_identity_changed")
    require(motion.get("frame_rate") == {"num": FRAME_RATE, "den": 1}
            and master.get("frame_rate") == {"num": FRAME_RATE, "den": 1}
            and master.get("frame_count") == FRAME_COUNT,
            "native_source_clock_or_fps_changed")
    segments = motion.get("segments")
    require(type(segments) is list and len(segments) == 1
            and segments[0].get("frame_count") == FRAME_COUNT
            and segments[0].get("scene_ids") == [scene.get("id") for scene in scenes]
            and segments[0].get("verification", {}).get("report", {}).get("support_level") == "native"
            and master.get("motion_segment_id") == segments[0].get("segment_id"),
            "original_motion_film_sequence_not_natively_verified")
    original_audio = master.get("source_audio")
    voices = source.get("audio", {}).get("voice_tracks")
    require(type(voices) is list and len(voices) == 1
            and voices[0].get("source_sha256") == wave_sha
            and voices[0].get("sample_rate_hz") == PCM_RATE
            and voices[0].get("channels") == 2
            and voices[0].get("measured_duration") == ratio(6, 1)
            and profile.get("voice_track_id") == voices[0].get("id")
            and type(original_audio) is dict
            and original_audio.get("sha256") == wave_sha
            and original_audio.get("sample_rate") == PCM_RATE
            and original_audio.get("channels") == 2,
            "original_selected_stereo_voice_digest_or_profile_changed")
    transcript = source.get("audio", {}).get("transcript")
    require(type(transcript) is list and len(transcript) == 2,
            "original_two_captions_not_saved_in_authorial_source")
    expected_cues = [
        (ratio(1, 5), ratio(9, 10)),
        (ratio(11, 10), ratio(12, 5)),
    ]
    for cue, (expected_start, expected_end) in zip(transcript, expected_cues):
        require(type(cue) is dict
                and cue.get("voice_track_id") == voices[0].get("id")
                and cue.get("start") == expected_start
                and cue.get("end") == expected_end
                and cue.get("alignment", {}).get("kind") == "manual"
                and type(cue.get("text")) is str
                and 1 <= len(cue["text"]) <= 160,
                "saved_manual_native_caption_cues_not_original")
    recipes = source.get("production_design", {}).get("delivery_recipes")
    require(type(recipes) is dict and set(recipes) == {profile_id},
            "saved_original_delivery_recipe_missing_or_duplicated")
    intents = recipes[profile_id].get("scene_intents")
    require(type(intents) is list and intents == [
        {"scene_id": scenes[0].get("id"), "role": "evidence",
         "archetype": "evidence_frame"},
        {"scene_id": scenes[1].get("id"), "role": "evidence",
         "archetype": "statement"},
    ], "saved_original_two_scene_narrative_intents_changed")
    encoded = master.get("master")
    require(type(encoded) is dict
            and encoded.get("artifact", {}).get("sha256") == mp4_sha
            and encoded.get("artifact", {}).get("root") == "output"
            and encoded.get("profile") == "h264-aac-mp4",
            "native_master_artifact_not_owned_or_hash_bound")
    source_node_ids = {node.get("id") for scene in scenes for node in scene["nodes"]}
    require(len(source_node_ids) == 4 and all(UUID.fullmatch(v or "")
            for v in source_node_ids),
            "original_source_node_ids_not_preserved")
    return {
        "project_id": pid, "generation": generation, "revision": revision,
        "deliverable_id": profile_id, "scene_ids": [s["id"] for s in scenes],
        "node_ids": sorted(source_node_ids),
        "native_motion_segment_id": segments[0]["segment_id"],
    }


def inspect_video_probe(raw: dict) -> dict:
    streams = raw.get("streams")
    frames = raw.get("frames")
    require(type(streams) is list and len(streams) == 2
            and type(frames) is list, "physical_master_stream_inventory_invalid")
    videos = [row for row in streams if row.get("codec_type") == "video"]
    audios = [row for row in streams if row.get("codec_type") == "audio"]
    require(len(videos) == len(audios) == 1, "physical_master_missing_video_or_audio")
    video, audio = videos[0], audios[0]
    require(video.get("codec_name") == "h264" and video.get("width") == WIDTH
            and video.get("height") == HEIGHT
            and video.get("avg_frame_rate") == "30/1"
            and audio.get("codec_name") == "aac"
            and audio.get("sample_rate") == str(PCM_RATE)
            and audio.get("channels") == 2,
            "physical_master_codec_or_geometry_invalid")
    decoded = [frame for frame in frames if frame.get("media_type") == "video"]
    require(len(decoded) == FRAME_COUNT,
            "original_native_master_lost_or_duplicated_video_frames")
    for index, frame in enumerate(decoded):
        pts = frame.get("best_effort_timestamp_time")
        require(type(pts) is str and abs(float(pts) - index / FRAME_RATE) < 0.0005,
                "original_native_master_frame_pts_not_exact_30fps")
    return {
        "video_codec": "h264", "audio_codec": "aac",
        "video_frames_decoded": len(decoded),
        "frame_rate": FRAME_RATE,
        "pixel_size": [WIDTH, HEIGHT],
        "first_pts": float(decoded[0]["best_effort_timestamp_time"]),
        "last_pts": float(decoded[-1]["best_effort_timestamp_time"]),
    }


def difference_count(a: bytes, b: bytes, width: int, height: int,
                     *, bottom_only: bool, channel_threshold: int = 24) -> int:
    require(len(a) == len(b) == width * height * 3,
            "sampled_native_frame_byte_count_wrong")
    start = int(height * 0.54) * width * 3 if bottom_only else 0
    delta = 0
    for offset in range(start, len(a), 3):
        if (abs(a[offset] - b[offset])
                + abs(a[offset + 1] - b[offset + 1])
                + abs(a[offset + 2] - b[offset + 2])) > channel_threshold * 3:
            delta += 1
    return delta


def inspect_selected_pixels(frames: dict[int, bytes],
                            width: int = WIDTH, height: int = HEIGHT) -> dict:
    require(set(frames) == set(SELECTED), "native_sampled_frame_indices_not_closed")
    require(all(len(frame) == width * height * 3 for frame in frames.values()),
            "native_sampled_rgb_geometry_changed")
    caption_changes = {}
    for earlier, later in ((5, 6), (26, 27), (32, 33), (71, 72)):
        changed = difference_count(frames[earlier], frames[later], width, height,
                                   bottom_only=True)
        min_pixels = max(3, int(width * height * 0.00015))
        require(changed >= min_pixels,
                f"saved_caption_boundary_missing_from_native_pixels:{earlier}_{later}")
        caption_changes[f"{earlier}_to_{later}"] = changed
    cut = difference_count(frames[89], frames[90], width, height,
                           bottom_only=False)
    require(cut >= max(3, int(width * height * 0.003)),
            "two_authored_scenes_are_same_native_pixels_at_frame90")
    unique = {hashlib.sha256(frame).hexdigest() for frame in frames.values()}
    # Two static source scenes plus two different caption strings need only
    # four distinct authored images; never invent entrance effects for a fifth.
    require(len(unique) >= 4, "native_video_frames_are_static_or_forged")
    return {
        "observed_caption_transition_changed_pixels": caption_changes,
        "scene_cut_89_to_90_changed_pixels": cut,
        "selected_native_frame_rgbs_sha256": {
            str(frame): hashlib.sha256(rgb).hexdigest()
            for frame, rgb in frames.items()
        },
        "actual_selected_frame_count": len(frames),
        "native_rgbs_selected_only": True,
        "all_180_frame_pixels_compared": False,
    }


def ffprobe_media(media: Path, executable: str) -> dict:
    proc = subprocess.run(
        [executable, "-v", "error", "-show_streams",
         "-select_streams", "v:0", "-show_frames", "-of", "json", str(media)],
        capture_output=True, timeout=90, check=True,
    )
    require(len(proc.stdout) <= 4 * 1024 * 1024,
            "original_ffprobe_receipt_unbounded")
    partial = json.loads(proc.stdout)
    # A second independent full inventory prevents a fake video-only master.
    all_streams = subprocess.run(
        [executable, "-v", "error", "-show_streams", "-of", "json", str(media)],
        capture_output=True, timeout=60, check=True,
    )
    partial["streams"] = json.loads(all_streams.stdout)["streams"]
    return inspect_video_probe(partial)


def decode_selected_video(media: Path, executable: str) -> dict[int, bytes]:
    selector = "+".join(f"eq(n\\,{n})" for n in SELECTED)
    proc = subprocess.run(
        [executable, "-nostdin", "-v", "error", "-i", str(media), "-an",
         "-vf", "select=" + selector, "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True, timeout=150, check=True,
    )
    require(len(proc.stdout) == len(SELECTED) * FRAME_BYTES,
            "selected_decoded_native_frame_bytes_mismatch")
    return {
        frame: proc.stdout[index * FRAME_BYTES:(index + 1) * FRAME_BYTES]
        for index, frame in enumerate(SELECTED)
    }


def decode_original_audio(media: Path, executable: str, *, capture=None) -> bytes:
    try:
        proc = subprocess.run(
            [executable, "-nostdin", "-v", "error", "-i", str(media), "-map", "0:a:0",
             "-acodec", "pcm_s16le", "-ac", "2", "-ar", str(PCM_RATE),
             "-f", "s16le", "pipe:1"],
            capture_output=True, timeout=120, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        if capture is not None:
            try:
                capture({"stdout": error.stdout or b"", "stderr": error.stderr or b"",
                         "exit_code": getattr(error, "returncode", None),
                         "deadline_exceeded": isinstance(error, subprocess.TimeoutExpired),
                         "automatic_retry": False})
            except BaseException:
                error.private_capture_failed = True
        raise
    if capture is not None:
        capture({"stdout": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode,
                 "deadline_exceeded": False, "automatic_retry": False})
    require(PCM_FRAMES * 4 <= len(proc.stdout) <= AUDIO_BYTES_MAX
            and len(proc.stdout) % 4 == 0, "full_original_aac_pcm_sample_budget_changed")
    return proc.stdout


def verified_source_readback(project_path: Path, project_hash: str,
                             wav_path: Path, wav_hash: str,
                             motion_path: Path, motion_hash: str,
                             master_receipt_path: Path, master_receipt_hash: str,
                             master_path: Path, master_hash: str,
                             ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                             *, pcm_capture=None) -> dict:
    project = load_json(project_path, project_hash, MAX_SOURCE_BYTES)
    motion = load_json(motion_path, motion_hash)
    master = load_json(master_receipt_path, master_receipt_hash)
    digest(wav_path, MAX_SOURCE_BYTES, wav_hash)
    digest(master_path, MAX_MP4_BYTES, master_hash)
    identity = source_identity(project, motion, master, wav_hash, master_hash)
    probe = ffprobe_media(master_path, ffprobe)
    pixels = inspect_selected_pixels(decode_selected_video(master_path, ffmpeg))
    pcm = (decode_original_audio(master_path, ffmpeg) if pcm_capture is None else
           decode_original_audio(master_path, ffmpeg, capture=pcm_capture))
    audio_content = inspect_source_pcm16_stereo_content(
        wav_path.read_bytes(), pcm,
        original_wav_sha256=wav_hash,
        decoded_pcm_sha256=hashlib.sha256(pcm).hexdigest(),
    )
    require(audio_content["source_stereo_frames"] == PCM_FRAMES
            and all(audio_content["terminal_source_window_informative_by_channel"]),
            "original_tail_audio_not_observed_in_both_channels")
    return {
        "schema": "motionwright.native.film180.decoded-media/1",
        "identity": identity,
        "physical_video": probe,
        "native_pixel_boundaries": pixels,
        "source_bound_PCM": audio_content,
        "decoded_PCM_sha256": hashlib.sha256(pcm).hexdigest(),
        "master_sha256": master_hash,
        "source_WAV_sha256": wav_hash,
        "native_original_right_channel_tail_observed": True,
        "lossy_audio_quality_human_approved": False,
        "saved_source_rights_human_accepted": False,
        "original_source_reopened_in_external_NLE": False,
        "privileged_Broker_grant_verified_here": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["project", "wav", "motion", "master_receipt", "master"]:
        parser.add_argument("--" + name.replace("_", "-"), required=True, type=Path)
        parser.add_argument("--" + name.replace("_", "-") + "-sha256", required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    require(args.receipt.is_absolute() and not args.receipt.exists()
            and not args.receipt.is_symlink()
            and args.receipt.parent.is_dir(),
            "decoded_original_receipt_destination_must_be_fresh")
    result = verified_source_readback(
        args.project, args.project_sha256, args.wav, args.wav_sha256,
        args.motion, args.motion_sha256, args.master_receipt,
        args.master_receipt_sha256, args.master, args.master_sha256,
        args.ffmpeg, args.ffprobe,
    )
    with args.receipt.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")


if __name__ == "__main__":
    main()
