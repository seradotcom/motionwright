#!/usr/bin/env python3
"""Retained-evidence gate for the first native Kdenlive cut receiver.

The existing bridge owns Project/rights/OTIO interpretation. This module adds
whole decoded-frame and clock checks, plus custody checks on the observed
save/close/reopen/edit sequence. It never starts Kdenlive, changes RAW, creates a
Host execution receipt, commits a Project, or retries an UNKNOWN action.

Preparation consumes ONE retained canonical Rust fixture and actual media.
The runtime/preset/clock lock must be fixed before A6 opens the native editor.
Synthetic inspector fixtures are tests of this reader, not NLE executions.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import time

import bridge

SCHEMA = "motionwright.kdenlive-receiver-gate/1"
LOCK_SCHEMA = "motionwright.kdenlive-receiver-runtime-lock/1"
OBS_SCHEMA = "motionwright.kdenlive-receiver-observations/1"
KDENLIVE_COMMIT = "12a22024760d41ba233cdcb6f5b73aa20d859bed"
MAX_FILE = 256 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
WIDTH, HEIGHT, FPS, SOURCE_FRAMES, EDITED_FRAMES = 320, 180, 30, 90, 75
REQUIRED_RUNTIME_FILES = {
    "kdenlive", "otio", "qt", "mlt_renderer", "ffmpeg", "ffprobe",
    "provider_closure", "decoder_closure", "versions", "preset_definition",
    "preset_parameters", "ui_controller",
}
STAGES = (
    "import", "save_received", "close_received", "reopen_received",
    "export_received", "render_received", "native_tail_edit", "save_edited",
    "close_edited", "reopen_edited", "export_edited", "render_edited",
)
MISSING_STAGES = ("import_missing", "save_missing", "export_missing")
NATIVE_OUTPUTS = {
    "received_project": "native/received.kdenlive",
    "edited_project": "native/tail-edited.kdenlive",
    "received_raw": "raw/received.otio",
    "edited_raw": "raw/tail-edited.otio",
    "missing_project": "missing-native/received.kdenlive",
    "missing_raw": "missing-raw/received.otio",
}


class GateRefused(ValueError):
    """A fixed qualification failure, never an authorization decision."""


def require(value: bool, code: str) -> None:
    if not value:
        raise GateRefused(code)


def exact(value: object, keys: set[str], code: str) -> dict:
    require(isinstance(value, dict) and set(value) == keys, code)
    return value


def integer(value: object, code: str) -> int:
    require(type(value) is int or isinstance(value, str), code)
    if isinstance(value, str):
        require(re.fullmatch(r"-?(0|[1-9][0-9]{0,17})", value) is not None, code)
    result = int(value)
    require(abs(result) <= 10**15, code)
    return result


def ratio(value: object, code: str) -> Fraction:
    require(isinstance(value, str) and len(value) <= 40, code)
    require(re.fullmatch(r"[0-9]{1,15}/[1-9][0-9]{0,14}", value) is not None, code)
    result = Fraction(value)
    require(result > 0, code)
    return result


def relative_name(name: object) -> str:
    require(isinstance(name, str) and 1 <= len(name) <= 250, "relative_path_required")
    require("\\" not in name and ":" not in name and "\x00" not in name,
            "relative_path_required")
    require(all(part not in ("", ".", "..") for part in name.split("/")),
            "relative_path_required")
    require(not PurePosixPath(name).is_absolute(), "relative_path_required")
    return name


def file_record(path: Path, budget: int = MAX_FILE) -> dict:
    resolved = bridge._nle_resolve_plain_path(path.absolute(), "unsafe_evidence_file")
    digest, size = bridge.fingerprint(resolved, budget)
    return {"sha256": digest, "bytes": size}


def bound_file(root: Path, row: object, budget: int = MAX_FILE) -> Path:
    row = exact(row, {"path", "sha256", "bytes"}, "invalid_file_binding")
    name = relative_name(row["path"])
    require(isinstance(row["sha256"], str) and bridge.HEX.fullmatch(row["sha256"]) is not None,
            "invalid_file_binding")
    require(type(row["bytes"]) is int and 0 < row["bytes"] <= budget,
            "invalid_file_binding")
    path = root.joinpath(*PurePosixPath(name).parts)
    require(file_record(path, budget) == {k: row[k] for k in ("sha256", "bytes")},
            "evidence_bytes_changed")
    return path


def json_file(root: Path, row: dict) -> dict:
    return load_json(bound_file(root, row, MAX_JSON))


def load_json(path: Path) -> dict:
    before = file_record(path, MAX_JSON)
    with path.open("rb") as stream:
        data = stream.read(MAX_JSON + 1)
    require(0 < len(data) <= MAX_JSON and hashlib.sha256(data).hexdigest() == before["sha256"]
            and file_record(path, MAX_JSON) == before, "json_changed_during_read")
    return bridge.strict_json(data, "receiver evidence")


def write_new(path: Path, data: bytes) -> None:
    require(not path.exists() and path.parent.is_dir(), "new_output_required")
    with path.open("xb") as stream:
        stream.write(data)


def encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def validate_clock_profile(value: object) -> dict:
    value = exact(value, {"mode", "container_tick"}, "invalid_clock_profile")
    if value["mode"] == "exact":
        require(value["container_tick"] is None, "invalid_clock_profile")
    else:
        require(value["mode"] == "nearest_tick", "invalid_clock_profile")
        require(ratio(value["container_tick"], "invalid_container_tick") <= Fraction(1, 1000),
                "container_tick_too_coarse")
    return value


def validate_runtime(lock: object) -> dict:
    lock = exact(lock, {"schema", "kdenlive_commit", "kdenlive_version", "files",
                        "preset", "source_profile", "clock", "observer",
                        "controlled_roots"}, "invalid_runtime_lock")
    require(lock["schema"] == LOCK_SCHEMA and lock["kdenlive_commit"] == KDENLIVE_COMMIT
            and lock["kdenlive_version"] == "25.04.3", "unadmitted_native_source")
    files = exact(lock["files"], REQUIRED_RUNTIME_FILES, "incomplete_runtime_lock")
    # These are operator-observed binaries/manifests, not client-selected Host tools.
    for row in files.values():
        exact(row, {"path", "sha256", "bytes"}, "invalid_runtime_file")
        require(isinstance(row["path"], str) and Path(row["path"]).is_absolute(),
                "runtime_file_must_be_absolute")
        require(file_record(Path(row["path"])) == {k: row[k] for k in ("sha256", "bytes")},
                "runtime_bytes_changed")
    observer = exact(lock["observer"], {"controller", "session_identity"}, "invalid_observer_lock")
    require(all(isinstance(v, str) and 1 <= len(v) <= 200 for v in observer.values()),
            "invalid_observer_lock")
    roots = lock["controlled_roots"]
    require(isinstance(roots, list) and 1 <= len(roots) <= 8, "isolated_roots_required")
    for root in roots:
        require(isinstance(root, str) and Path(root).is_absolute(), "isolated_roots_required")
        bridge._nle_resolve_plain_path(Path(root), "unsafe_isolated_root", directory=True)
    validate_clock_profile(lock["clock"])
    for key in ("preset", "source_profile"):
        fields = {"codec", "demuxer", "pix_fmt", "color_range", "color_space",
                  "color_transfer", "color_primaries"}
        if key == "preset":
            fields |= {"name", "extension"}
        row = exact(lock[key], fields, "invalid_media_profile")
        require(all(isinstance(v, str) and 1 <= len(v) <= 200 for v in row.values()),
                "invalid_media_profile")
        require(row["demuxer"] in {"mov", "matroska"}, "unsupported_demuxer")
    require(lock["source_profile"]["codec"] == "h264"
            and lock["source_profile"]["demuxer"] == "mov", "canonical_h264_source_required")
    require(lock["preset"]["codec"] in {"ffv1", "rawvideo", "png", "huffyuv", "utvideo"},
            "lossless_verification_profile_required")
    require(lock["preset"]["extension"] in {".mkv", ".mov"}, "unsupported_render_container")
    # The bridge resolves ffprobe by PATH. Bind that resolution to the SAME lock.
    selected = shutil.which("ffprobe")
    require(selected is not None and Path(selected).resolve() == Path(files["ffprobe"]["path"]).resolve(),
            "bridge_decoder_differs_from_lock")
    return lock


def check_timestamp(value: int, base: Fraction, expected: Fraction, clock: dict) -> None:
    if clock["mode"] == "exact":
        require(value * base == expected, "timestamp_not_exact")
        return
    tick = ratio(clock["container_tick"], "invalid_container_tick")
    require(base == tick, "container_tick_differs_from_lock")
    require(abs(value * base - expected) <= tick / 2, "timestamp_outside_declared_quantization")


def validate_probe(streams: dict, frames: dict, packets: dict, count: int,
                   profile: dict, clock: dict) -> dict:
    validate_clock_profile(clock)
    require(type(count) is int and count in {SOURCE_FRAMES, EDITED_FRAMES}, "invalid_frame_count")
    values = streams.get("streams")
    require(isinstance(values, list) and len(values) == 1 and isinstance(values[0], dict),
            "exactly_one_video_no_audio_required")
    video = values[0]
    require(video.get("codec_type") == "video", "exactly_one_video_no_audio_required")
    require(video.get("width") == WIDTH and video.get("height") == HEIGHT
            and video.get("sample_aspect_ratio") == "1:1"
            and video.get("field_order") == "progressive", "video_geometry_or_interlace_changed")
    require(ratio(video.get("r_frame_rate"), "invalid_frame_rate") == FPS
            and ratio(video.get("avg_frame_rate"), "invalid_frame_rate") == FPS,
            "video_frame_rate_changed")
    require(video.get("codec_name") == profile["codec"], "video_codec_changed")
    for key in ("pix_fmt", "color_range", "color_space", "color_transfer", "color_primaries"):
        # Missing color signaling is a declaration of unknown, not guessed BT.709.
        require(video.get(key, "unknown") == profile[key], "video_pixel_or_color_profile_changed")
    base = ratio(video.get("time_base"), "invalid_video_time_base")
    index = integer(video.get("index"), "invalid_video_stream_index")
    observed = frames.get("frames")
    require(isinstance(observed, list) and len(observed) == count, "decoded_frame_count_changed")
    timestamps = []
    for at, row in enumerate(observed):
        require(isinstance(row, dict) and row.get("media_type") == "video"
                and row.get("stream_index") == index, "decoded_frame_stream_changed")
        require(row.get("width") == WIDTH and row.get("height") == HEIGHT
                and row.get("sample_aspect_ratio") == "1:1"
                and row.get("interlaced_frame") == 0
                and row.get("pix_fmt") == profile["pix_fmt"], "decoded_frame_profile_changed")
        pts = integer(row.get("pts"), "frame_pts_missing")
        require(integer(row.get("best_effort_timestamp"), "frame_pts_missing") == pts,
                "decoder_repaired_timestamp")
        check_timestamp(pts, base, Fraction(at, FPS), clock)
        require(not timestamps or pts > timestamps[-1], "frame_pts_not_increasing")
        timestamps.append(pts)
    rows = packets.get("packets")
    require(isinstance(rows, list) and len(rows) == count, "packet_count_changed")
    packet_times = []
    for row in rows:
        require(isinstance(row, dict) and row.get("stream_index") == index, "packet_stream_changed")
        pts = integer(row.get("pts"), "packet_pts_missing")
        integer(row.get("dts"), "packet_dts_missing")
        duration = integer(row.get("duration"), "packet_duration_missing")
        require(duration > 0, "packet_duration_missing")
        check_timestamp(duration, base, Fraction(1, FPS), clock)
        packet_times.append((pts, duration))
    ordered = sorted(packet_times)
    require([v[0] for v in ordered] == timestamps, "packet_and_decoded_frame_clock_disagree")
    check_timestamp(ordered[-1][0] + ordered[-1][1], base, Fraction(count, FPS), clock)
    return {"frames": count, "width": WIDTH, "height": HEIGHT, "fps": "30/1",
            "time_base": str(base), "input_pts": timestamps, "packet_pts_duration": ordered,
            "clock_profile": clock, "all_frames_and_packets_checked": True,
            "audio_streams": 0, "metadata_count_is_decode_proof": False}


def parse_framehash(payload: bytes, count: int) -> list[str]:
    require(0 < len(payload) <= MAX_JSON, "framehash_byte_budget")
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeError:
        raise GateRefused("invalid_framehash") from None
    require(sum(line.strip() == "#hash: SHA256" for line in lines) == 1,
            "wrong_framehash_algorithm")
    hashes = []
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        fields = [part.strip() for part in line.split(",")]
        require(len(fields) == 6, "invalid_framehash_row")
        stream, dts, pts, duration, size = [integer(x, "invalid_framehash_row") for x in fields[:5]]
        require(stream == 0 and dts == pts == len(hashes) and duration == 1,
                "framehash_output_clock_changed")
        require(size == WIDTH * HEIGHT * 3 and bridge.HEX.fullmatch(fields[5]) is not None,
                "framehash_representation_changed")
        hashes.append(fields[5])
    require(len(hashes) == count, "incomplete_decoded_framehash")
    return hashes


def compare_frames(source: list[str], final: list[str], count: int) -> dict:
    require(isinstance(source, list) and isinstance(final, list)
            and type(count) is int and count in {SOURCE_FRAMES, EDITED_FRAMES} and len(source) == SOURCE_FRAMES
            and len(final) == count, "frame_sequence_length_changed")
    require(all(isinstance(h, str) and bridge.HEX.fullmatch(h) is not None for h in source + final),
            "invalid_frame_digest")
    require(len(set(source)) == SOURCE_FRAMES, "source_timing_identity_ambiguous")
    require(final == source[:count], "decoded_source_frame_sequence_changed")
    return {"representation": "rgb24", "source_frames": SOURCE_FRAMES,
            "matched_frames": count, "source_interval": [0, count],
            "every_frame_digest_equal": True, "first_last_only": False,
            "perceptual_or_lossy_tolerance_applied": False}


def inspect_video(path: Path, count: int, profile: dict, clock: dict,
                  lock: dict, output: Path) -> dict:
    require(not output.exists() and output.parent.is_dir(), "new_readback_output_required")
    before = file_record(path)
    deadline = time.monotonic() + 60
    ffprobe, ffmpeg = (lock["files"][name]["path"] for name in ("ffprobe", "ffmpeg"))
    common = [ffprobe, "-v", "error", "-protocol_whitelist", "file", "-threads", "1",
              "-max_alloc", str(MAX_FILE), "-f", profile["demuxer"]]
    captures = {}
    for key, options in (
        ("streams", ["-show_streams", "-show_format"]),
        ("frames", ["-err_detect", "explode", "-show_frames"]),
        ("packets", ["-show_packets"]),
    ):
        captures[key] = bridge._probe_output(common + options + ["-of", "json", str(path)], deadline)
    args = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-xerror",
            "-err_detect", "explode", "-protocol_whitelist", "file,pipe", "-threads", "1",
            "-filter_threads", "1", "-f", profile["demuxer"], "-i", str(path),
            "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", "format=rgb24",
            "-fps_mode", "passthrough", "-c:v", "rawvideo", "-f", "framehash",
            "-hash", "sha256", "pipe:1"]
    captures["framehash"] = bridge._probe_output(args, deadline)
    require(file_record(path) == before, "media_changed_during_decode")
    output.mkdir()
    for name, data in captures.items():
        write_new(output / (name + (".txt" if name == "framehash" else ".json")), data)
    proof = validate_probe(*(bridge.strict_json(captures[k], k) for k in
                             ("streams", "frames", "packets")), count, profile, clock)
    proof["frame_hashes"] = parse_framehash(captures["framehash"], count)
    proof["media"] = before
    proof["decoder_arguments"] = args
    proof["readback_files"] = {p.name: file_record(p, MAX_JSON) for p in output.iterdir()}
    return proof


def validate_fixture(fixture: dict, source_digest: str) -> list[str]:
    exact(fixture, {"schema", "project", "original_otio", "owner_media_asset_id",
                    "expected_media_sha256", "editorial_source_provenance", "creative_approval"},
          "invalid_canonical_fixture")
    require(fixture["schema"] == "motionwright.real-otio-fixture/1"
            and fixture["expected_media_sha256"] == source_digest, "fixture_media_mismatch")
    project = fixture["project"]
    require(isinstance(project, dict) and isinstance(project.get("scenes"), list)
            and len(project["scenes"]) == 3, "three_original_scenes_required")
    ids = []
    for scene in project["scenes"]:
        require(isinstance(scene, dict) and isinstance(scene.get("id"), str)
                and bridge.UUID.fullmatch(scene["id"]) is not None
                and bridge.rational(scene.get("duration"), "scene") == 1,
                "canonical_scene_clock_changed")
        ids.append(scene["id"])
    require(len(set(ids)) == 3, "duplicate_scene_identity")
    bridge.validate_existing_otio(project, fixture["original_otio"])
    matches = [a for a in project.get("assets", []) if a.get("id") == fixture["owner_media_asset_id"]]
    require(len(matches) == 1 and matches[0].get("content_sha256") == source_digest
            and matches[0].get("media_type") == "video/mp4", "canonical_asset_identity_changed")
    return ids


def prepare_case(fixture_path: Path, fixture_sha: str, media: Path, runtime_path: Path,
                 runtime_sha: str, output: Path, rights: dict, owner_review: dict) -> dict:
    """Create owner capsules once; this does not launch any native application."""
    require(file_record(fixture_path, MAX_JSON)["sha256"] == fixture_sha,
            "fixture_execution_output_changed")
    require(file_record(runtime_path, MAX_JSON)["sha256"] == runtime_sha, "runtime_lock_changed")
    lock = validate_runtime(load_json(runtime_path))
    require(not output.exists() and output.parent.is_dir(), "new_case_required")
    parent = output.parent.resolve(strict=True)
    require(any(parent == Path(p).resolve() or parent.is_relative_to(Path(p).resolve())
                for p in lock["controlled_roots"]), "case_outside_controlled_roots")
    source = file_record(media)
    fixture = load_json(fixture_path)
    ids = validate_fixture(fixture, source["sha256"])
    exact(rights, {"owner", "license", "authorized", "redistributable"}, "invalid_owner_rights")
    require(rights["authorized"] is True and rights["redistributable"] is True,
            "source_rights_not_declared")
    exact(owner_review, {"reviewer", "synthetic_source_allowed"}, "invalid_owner_review")
    output.mkdir()
    owner = output / "owner"
    owner.mkdir()
    for dest, data in (("fixture.json", fixture_path.read_bytes()), ("source.mp4", media.read_bytes()),
                       ("runtime-lock.json", runtime_path.read_bytes()),
                       ("source-project.json", encoded(fixture["project"])),
                       ("original.otio", encoded(fixture["original_otio"]))):
        write_new(owner / dest, data)
    source_readback = inspect_video(owner / "source.mp4", SOURCE_FRAMES, lock["source_profile"],
                                    {"mode": "exact", "container_tick": None}, lock,
                                    output / "source-readback")
    compare_frames(source_readback["frame_hashes"], source_readback["frame_hashes"], SOURCE_FRAMES)
    results = {}
    for kind, size in (("complete", 3), ("missing", 2)):
        request = {"schema": bridge.SCHEMA, "project": "source-project.json", "otio": "original.otio",
                   "bindings": [{"scene_id": ids[at], "asset_id": fixture["owner_media_asset_id"],
                                 "file": "source.mp4", "sha256": source["sha256"], "rights": rights,
                                 "source_start_frame": at * 30} for at in range(size)],
                   "owner_review": owner_review}
        results[kind] = bridge.export_portable(owner, request, output / kind)
    immutable = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            immutable.append({"path": path.relative_to(output).as_posix(), **file_record(path)})
    plan = {"schema": SCHEMA, "runtime_lock_sha256": runtime_sha, "fixture_sha256": fixture_sha,
            "source_sha256": source["sha256"], "scene_ids": ids, "immutable_files": immutable,
            "source_frame_hashes": source_readback["frame_hashes"], "capsule_results": results,
            "outputs": {**NATIVE_OUTPUTS,
                        "received_render": "renders/received" + lock["preset"]["extension"],
                        "edited_render": "renders/tail-edited" + lock["preset"]["extension"]},
            "native_runs_started": 0, "runtime_lock_is_product_authority": False}
    require(file_record(fixture_path, MAX_JSON)["sha256"] == fixture_sha
            and file_record(media) == source
            and file_record(runtime_path, MAX_JSON)["sha256"] == runtime_sha,
            "owner_input_changed_during_preparation")
    write_new(output / "receiver-plan.json", encoded(plan))
    return plan


def validate_observations(root: Path, observations: dict, plan: dict, lock: dict) -> dict:
    exact(observations, {"schema", "plan_sha256", "runtime_lock_sha256", "observer", "actions"},
          "invalid_observation_bundle")
    require(observations["schema"] == OBS_SCHEMA
            and observations["plan_sha256"] == file_record(root / "receiver-plan.json", MAX_JSON)["sha256"]
            and observations["runtime_lock_sha256"] == plan["runtime_lock_sha256"]
            and observations["observer"] == lock["observer"], "observation_source_binding_changed")
    actions = observations["actions"]
    require(isinstance(actions, list) and len(actions) == len(STAGES) + len(MISSING_STAGES),
            "native_action_chain_incomplete")
    require([x.get("stage") for x in actions if isinstance(x, dict)] == list(STAGES + MISSING_STAGES),
            "native_action_order_changed")
    by_stage = {}
    previous_time = -1
    for action in actions:
        exact(action, {"stage", "case", "process_identity", "monotonic_ns", "outcome",
                       "inputs", "outputs", "evidence", "details"}, "invalid_native_action")
        require(action["outcome"] == "KNOWN_COMPLETE", "original_action_requires_reconciliation")
        expected_case = "missing" if action["stage"] in MISSING_STAGES else "complete"
        require(action["case"] == expected_case, "native_cases_were_combined")
        process = exact(action["process_identity"], {"pid", "start_token"}, "process_identity_missing")
        require(type(process["pid"]) is int and process["pid"] > 0
                and isinstance(process["start_token"], str) and 1 <= len(process["start_token"]) <= 200,
                "process_identity_missing")
        stamp = action["monotonic_ns"]
        require(type(stamp) is int and 0 <= stamp <= 2**63 - 1, "invalid_observation_clock")
        require(stamp > previous_time, "native_observation_clock_not_increasing")
        previous_time = stamp
        for key in ("inputs", "outputs", "evidence"):
            rows = action[key]
            require(isinstance(rows, list) and len(rows) <= 16 and (key != "evidence" or rows),
                    "physical_action_evidence_missing")
            for row in rows:
                bound_file(root, row)
        require(isinstance(action["details"], dict), "invalid_action_details")
        by_stage[action["stage"]] = action
    def has(stage: str, role: str, file: str) -> None:
        require(any(row["path"] == file for row in by_stage[stage][role]),
                "native_action_file_join_missing")
    def same(*stages: str) -> None:
        require(all(by_stage[s]["process_identity"] == by_stage[stages[0]]["process_identity"]
                    for s in stages), "native_session_continuity_changed")
    same("import", "save_received", "close_received")
    same("reopen_received", "export_received", "native_tail_edit", "save_edited", "close_edited")
    same("reopen_edited", "export_edited")
    same(*MISSING_STAGES)
    app_instances = [by_stage[s]["process_identity"] for s in
                     ("import", "reopen_received", "reopen_edited", "import_missing")]
    require(len({(x["pid"], x["start_token"]) for x in app_instances}) == 4,
            "reopen_requires_a_new_process")
    outputs = plan["outputs"]
    has("import", "inputs", "complete/timeline.otio")
    has("import_missing", "inputs", "missing/timeline.otio")
    for adjective in ("received", "edited"):
        project, raw, render = (outputs[adjective + "_" + suffix] for suffix in ("project", "raw", "render"))
        has("save_" + adjective, "outputs", project)
        has("reopen_" + adjective, "inputs", project)
        has("export_" + adjective, "inputs", project)
        has("export_" + adjective, "outputs", raw)
        has("render_" + adjective, "inputs", project)
        has("render_" + adjective, "outputs", render)
        require(by_stage["render_" + adjective]["details"] == {
            "preset_parameters_sha256": lock["files"]["preset_parameters"]["sha256"],
            "render_async": False, "exit_code": 0, "native_jobs_created": 1},
            "native_render_not_bound_to_observed_job")
    has("save_missing", "outputs", outputs["missing_project"])
    has("export_missing", "inputs", outputs["missing_project"])
    has("export_missing", "outputs", outputs["missing_raw"])
    require(by_stage["native_tail_edit"]["details"] == {
        "scene_ordinal": 2, "selected_clips": 1, "m_pos_frames": 60,
        "m_cropStart_frames": 60, "m_dur_frames": 15, "external_xml_edit": False},
        "native_tail_edit_profile_changed")
    for stage in ("close_received", "close_edited"):
        require(by_stage[stage]["details"] == {"process_exited": True, "exit_code": 0},
                "native_close_not_observed")
    require(file_record(root / outputs["received_project"])["sha256"]
            != file_record(root / outputs["edited_project"])["sha256"],
            "native_edited_source_not_distinct")
    return {"all_stages_bound_to_physical_files": True, "distinct_app_instances": 4,
            "reported_save_close_reopen_edit_chain_consistent": True,
            "observation_authentication": "REQUIRES_A6_OBSERVER_ADMISSION",
            "native_execution_authenticated_by_this_json": False}


def missing_result(preview: dict) -> dict:
    if preview.get("status") == "REJECTED_LOSS" and preview.get("rejection_code") == \
            "original_missing_reference_not_receivable" and preview.get("native_change_proposals") == []:
        return {"status": "SOURCE_BOUND_MISSING_LOSS_VERIFIED", "native_acceptance": False}
    return {"status": "INCONCLUSIVE_MISSING", "observed_bridge_status": preview.get("status"),
            "observed_rejection_code": preview.get("rejection_code"), "native_acceptance": False}


def verify_case(root: Path, plan_sha: str, observation_row: dict, output: Path) -> dict:
    require(file_record(root / "receiver-plan.json", MAX_JSON)["sha256"] == plan_sha,
            "pre_run_plan_changed")
    plan = load_json(root / "receiver-plan.json")
    exact(plan, {"schema", "runtime_lock_sha256", "fixture_sha256", "source_sha256", "scene_ids",
                 "immutable_files", "source_frame_hashes", "capsule_results", "outputs",
                 "native_runs_started", "runtime_lock_is_product_authority"}, "invalid_pre_run_plan")
    require(plan["schema"] == SCHEMA and plan["native_runs_started"] == 0
            and plan["runtime_lock_is_product_authority"] is False, "invalid_plan_authority")
    require(isinstance(plan["scene_ids"], list) and len(plan["scene_ids"]) == 3
            and all(isinstance(x, str) and bridge.UUID.fullmatch(x) is not None for x in plan["scene_ids"])
            and len(set(plan["scene_ids"])) == 3, "invalid_plan_scene_identity")
    exact(plan["outputs"], set(NATIVE_OUTPUTS) | {"received_render", "edited_render"},
          "native_output_roles_changed")
    expected_names = {**NATIVE_OUTPUTS, "received_render": plan["outputs"].get("received_render"),
                      "edited_render": plan["outputs"].get("edited_render")}
    require(plan["outputs"] == expected_names, "native_output_roles_changed")
    immutable = plan["immutable_files"]
    require(isinstance(immutable, list) and 1 <= len(immutable) <= 128
            and len({x.get("path") for x in immutable if isinstance(x, dict)}) == len(immutable),
            "invalid_immutable_inventory")
    for row in immutable:
        bound_file(root, row)
    runtime = root / "owner/runtime-lock.json"
    require(file_record(runtime, MAX_JSON)["sha256"] == plan["runtime_lock_sha256"], "runtime_lock_changed")
    lock = validate_runtime(load_json(runtime))
    require(plan["outputs"]["received_render"] == "renders/received" + lock["preset"]["extension"]
            and plan["outputs"]["edited_render"] == "renders/tail-edited" + lock["preset"]["extension"],
            "native_output_profile_changed")
    observations = json_file(root, observation_row)
    chain = validate_observations(root, observations, plan, lock)
    require(not output.exists() and output.parent.resolve() == root.resolve(), "new_case_readback_required")
    output.mkdir()
    source = inspect_video(root / "owner/source.mp4", SOURCE_FRAMES, lock["source_profile"],
                           {"mode": "exact", "container_tick": None}, lock, output / "source")
    require(source["media"]["sha256"] == plan["source_sha256"]
            and source["frame_hashes"] == plan["source_frame_hashes"], "source_preflight_changed")
    media = {}
    for adjective, count in (("received", SOURCE_FRAMES), ("edited", EDITED_FRAMES)):
        value = inspect_video(root / plan["outputs"][adjective + "_render"], count,
                              lock["preset"], lock["clock"], lock, output / adjective)
        value["source_comparison"] = compare_frames(source["frame_hashes"], value["frame_hashes"], count)
        media[adjective] = value
    previews = {}
    for kind, raw_dir, initial, last in (
        ("complete", "raw", "received.otio", "tail-edited.otio"),
        ("missing", "missing-raw", "received.otio", "received.otio"),
    ):
        bundle, raw_root = root / kind, root / raw_dir
        request = {"schema": bridge.KDENLIVE_REQUEST_SCHEMA,
                   "capsule_manifest_sha256": file_record(bundle / "bridge-manifest.json", MAX_JSON)["sha256"],
                   "received_otio": initial, "received_sha256": file_record(raw_root / initial, MAX_JSON)["sha256"],
                   "edited_otio": last, "edited_sha256": file_record(raw_root / last, MAX_JSON)["sha256"]}
        previews[kind] = bridge.preview_kdenlive_cut(
            bundle, raw_root, request, root / "owner/source-project.json",
            authorized_media_roots=((bundle / "media").resolve(),))
    expected = [{"type": "set_scene_duration", "scene_id": plan["scene_ids"][2],
                 "duration": {"num": "1", "den": "2"}}]
    preview = previews["complete"]
    require(preview.get("status") == "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED"
            and preview.get("native_change_proposals") == expected, "bounded_return_cut_changed")
    for key, expected_flag in (("nle_receiver_acceptance", "NOT_VERIFIED"),
                               ("native_project_reopened", "NOT_VERIFIED"),
                               ("native_final_frames_verified", False), ("edit_imported", False),
                               ("publication_approved", False)):
        require(preview.get(key) == expected_flag, "bridge_authority_was_promoted")
    for row in immutable:
        bound_file(root, row)
    bound_file(root, observation_row, MAX_JSON)
    validate_runtime(lock)
    report = {"schema": SCHEMA, "status": "FRAME_AND_SOURCE_BUNDLE_VERIFIED_OBSERVER_ADMISSION_REQUIRED",
              "plan_sha256": plan_sha, "observation_sha256": observation_row["sha256"],
              "source": source, "media": media, "observation_custody": chain,
              "bridge_previews": previews, "missing_case": missing_result(previews["missing"]),
              "native_execution_authenticated": False, "host_receipt_created": False,
              "canonical_project_changed": False, "native_project_portability_verified": False,
              "source_audio_or_native_scene_semantics_roundtripped": False,
              "publication_approved": False, "human_acceptance": False,
              "loss_report": preview["loss_report"]}
    write_new(output / "receiver-readback.json", encoded(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    prep = sub.add_parser("prepare")
    for name in ("fixture", "media", "runtime", "output", "rights", "owner-review"):
        prep.add_argument("--" + name, type=Path, required=True)
    prep.add_argument("--fixture-sha256", required=True)
    prep.add_argument("--runtime-sha256", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--case", type=Path, required=True)
    verify.add_argument("--plan-sha256", required=True)
    verify.add_argument("--observation-binding", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == "prepare":
            result = prepare_case(args.fixture, args.fixture_sha256, args.media,
                                  args.runtime, args.runtime_sha256, args.output,
                                  load_json(args.rights), load_json(args.owner_review))
        else:
            result = verify_case(args.case, args.plan_sha256,
                                 load_json(args.observation_binding),
                                 args.output)
        print(json.dumps(result, sort_keys=True))
    except (GateRefused, bridge.BridgeRefused, OSError, ValueError) as error:
        print(json.dumps({"schema": SCHEMA, "status": "NOT_VERIFIED",
                          "reason": str(error) if isinstance(error, GateRefused) else "bounded_receiver_readback_failed",
                          "native_execution_authenticated": False, "retry_native_action": False}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
