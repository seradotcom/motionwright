#!/usr/bin/env python3
"""Read-only native Motion Canvas editable-source and all-frame round-trip gate.

The input is an immutable observer-side witness, not a request to execute a
provider. This module checks actual files and decoded pixels. It does not
authenticate Host receipts, run native jobs, grant permission or approve rights.
A producer-independent A6 execution/Host-custody gate remains required.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import zlib
from typing import Any

from native_direct_mediated_parity import (
    DirectParityError, decoded_rgba_frame_hashes, native_png_hashes,
)

SCHEMA = "motionwright.motion-canvas-source-roundtrip/1"
STAGES = ("original", "reopened", "edited", "edited_reopened")
HEX = re.compile(r"[0-9a-f]{64}\Z")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
MAX_JSON = 8 * 1024 * 1024
MAX_SOURCE = 16 * 1024 * 1024
MAX_SOURCE_FILES = 64
MAX_TOTAL_SOURCE = 64 * 1024 * 1024


class RoundtripRefused(ValueError):
    """Closed, source-bounded read-only inspection failure."""


def require(test: bool, reason: str) -> None:
    if not test:
        raise RoundtripRefused(reason)


def exact(value: object, keys: set[str], reason: str) -> dict:
    require(type(value) is dict and set(value) == keys, reason)
    return value


def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    values = {}
    for key, value in pairs:
        require(key not in values, "duplicate_json_key")
        values[key] = value
    return values


def reject_constant(value: str) -> None:
    raise RoundtripRefused("nonfinite_json_value")


def safe_path(root: Path, name: object, *, directory: bool = False) -> Path:
    require(isinstance(name, str) and 0 < len(name) <= 320
            and "\\" not in name and ":" not in name and "\x00" not in name,
            "unsafe_relative_path")
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and all(part not in ("", ".", "..") for part in name.split("/")),
            "unsafe_relative_path")
    path = root
    for part in relative.parts:
        path = path / part
        require(path.exists(), "missing_evidence_path")
        entry = path.lstat()
        require(not stat.S_ISLNK(entry.st_mode)
                and not (getattr(entry, "st_file_attributes", 0) & 0x400),
                "unsafe_reparse_or_symlink")
    require(path.is_dir() if directory else path.is_file(), "evidence_path_type")
    return path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bounded_file(root: Path, binding: object, *, maximum: int = MAX_JSON) -> bytes:
    binding = exact(binding, {"path", "bytes", "sha256"}, "invalid_file_witness")
    claimed = binding["bytes"]
    require(type(claimed) is int and 0 < claimed <= maximum, "file_budget_or_type")
    require(type(binding["sha256"]) is str and HEX.fullmatch(binding["sha256"]) is not None,
            "file_digest_format")
    path = safe_path(root, binding["path"])
    stat_before = path.stat()
    require(stat_before.st_size == claimed, "physical_source_size_changed")
    with path.open("rb") as handle:
        body = handle.read(claimed + 1)
    stat_after = path.stat()
    require(len(body) == claimed and stat_after.st_size == claimed
            and stat_before.st_ino == stat_after.st_ino
            and stat_before.st_mtime_ns == stat_after.st_mtime_ns
            and stat_before.st_ctime_ns == stat_after.st_ctime_ns
            and digest(body) == binding["sha256"], "physical_source_bytes_changed")
    safe_path(root, binding["path"])
    return body


def checked_json(data: bytes) -> dict:
    require(0 < len(data) <= MAX_JSON, "json_budget")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs,
                           parse_constant=reject_constant)
    except RoundtripRefused:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise RoundtripRefused("malformed_source_json") from error
    require(type(value) is dict, "expected_json_object")
    return value


def uuid(value: object) -> str:
    require(type(value) is str and UUID.fullmatch(value) is not None, "invalid_source_uuid")
    return value


def project_node(document: dict, scene_id: str, node_id: str) -> dict:
    scenes = document.get("scenes")
    require(type(scenes) is list and 0 < len(scenes) <= 200, "invalid_source_scenes")
    selected = [scene for scene in scenes if type(scene) is dict and scene.get("id") == scene_id]
    require(len(selected) == 1, "source_scene_not_unique")
    nodes = selected[0].get("nodes")
    require(type(nodes) is list and 0 < len(nodes) <= 1000, "missing_source_nodes")
    selected_nodes = [node for node in nodes if type(node) is dict and node.get("id") == node_id]
    require(len(selected_nodes) == 1, "source_node_not_unique")
    return selected_nodes[0]


def assert_source_edit(original: dict, edited: dict, requested: dict) -> dict:
    scene_id, node_id = uuid(requested["scene_id"]), uuid(requested["node_id"])
    old_fill, new_fill = requested["from"], requested["to"]
    require(requested["property"] == "style.fill" and type(old_fill) is str
            and type(new_fill) is str and old_fill != new_fill
            and re.fullmatch(r"#[0-9A-F]{6}", old_fill) is not None
            and re.fullmatch(r"#[0-9A-F]{6}", new_fill) is not None,
            "unadmitted_source_edit")
    for field in ("id", "generation"):
        require(uuid(original.get(field)) == uuid(edited.get(field)),
                "source_identity_or_generation_changed")
    before, after = original.get("revision"), edited.get("revision")
    require(type(before) is int and type(after) is int and before >= 0
            and after == before + 1, "source_revision_did_not_advance_once")
    old_node = project_node(original, scene_id, node_id)
    new_node = project_node(edited, scene_id, node_id)
    old_style = old_node.get("style")
    new_style = new_node.get("style")
    require(type(old_style) is dict and type(new_style) is dict
            and old_style.get("fill") == old_fill and new_style.get("fill") == new_fill,
            "source_edit_property_not_persisted")
    # Only one node property and the canonical revision/time bookkeeping may
    # change. This preserves the entire unknown rights/asset/source graph.
    expected = copy.deepcopy(original)
    expected["revision"] = after
    if "updated_at" in edited and "updated_at" in original:
        expected["updated_at"] = edited["updated_at"]
    expected_node = project_node(expected, scene_id, node_id)
    expected_node["style"]["fill"] = new_fill
    require(expected == edited, "source_edit_changed_unrelated_intent_or_rights")
    return {"project_id": original["id"], "generation": original["generation"],
            "previous_revision": before, "edited_revision": after,
            "scene_id": scene_id, "node_id": node_id, "property": requested["property"],
            "from": old_fill, "to": new_fill}


def source_files(root: Path, files: object) -> list[dict]:
    require(type(files) is list and 1 <= len(files) <= MAX_SOURCE_FILES, "source_inventory_required")
    inventory = []
    total = 0
    names = set()
    for row in files:
        row = exact(row, {"role", "path", "bytes", "sha256"}, "invalid_source_inventory_row")
        role = row["role"]
        require(type(role) is str and 1 <= len(role) <= 160
                and chr(92) not in role and ":" not in role
                and all(part not in ("", ".", "..") for part in role.split("/")),
                "invalid_source_role")
        binding = {key: row[key] for key in ("path", "bytes", "sha256")}
        data = bounded_file(root, binding, maximum=MAX_SOURCE)
        require(role not in names, "duplicate_source_role")
        names.add(role)
        total += len(data)
        require(total <= MAX_TOTAL_SOURCE, "source_inventory_budget")
        inventory.append({"name": role, "bytes": len(data), "sha256": digest(data)})
    return sorted(inventory, key=lambda row: row["name"])


def validate_png_crc_sequence(frames_path: Path, count: int,
                              width: int, height: int) -> None:
    """Validate PNG transport CRCs; FFmpeg remains the sole pixel decoder."""
    signature = b"\x89PNG\r\n\x1a\n"
    for index in range(count):
        frame = safe_path(frames_path, f"frames/{index:06d}.png")
        before = frame.stat()
        require(len(signature) < before.st_size <= 16 * 1024 * 1024,
                "png_frame_budget")
        with frame.open("rb") as stream:
            data = stream.read(before.st_size + 1)
        after = frame.stat()
        require(len(data) == before.st_size and after.st_ino == before.st_ino
                and after.st_size == before.st_size
                and after.st_mtime_ns == before.st_mtime_ns,
                "png_changed_during_crc")
        require(data.startswith(signature), "invalid_png_signature")
        position = len(signature)
        saw_ihdr, saw_idat, saw_iend = False, False, False
        chunk_count = 0
        while position < len(data):
            require(position + 12 <= len(data), "truncated_png_chunk")
            length = struct.unpack_from(">I", data, position)[0]
            require(length <= 16 * 1024 * 1024 and position + 12 + length <= len(data),
                    "png_chunk_byte_budget")
            kind = data[position + 4:position + 8]
            require(all(65 <= x <= 90 or 97 <= x <= 122 for x in kind),
                    "invalid_png_chunk_name")
            payload_end = position + 8 + length
            expected_crc = struct.unpack_from(">I", data, payload_end)[0]
            actual_crc = zlib.crc32(data[position + 4:payload_end]) & 0xffffffff
            require(actual_crc == expected_crc, "corrupt_png_crc")
            if kind == b"IHDR":
                require(not saw_ihdr and chunk_count == 0 and length == 13,
                        "invalid_png_ihdr")
                x, y = struct.unpack_from(">II", data, position + 8)
                require((x, y) == (width, height), "png_geometry_mismatch")
                saw_ihdr = True
            elif kind == b"IDAT":
                require(saw_ihdr and not saw_iend, "invalid_png_idat")
                saw_idat = True
            elif kind == b"IEND":
                require(saw_ihdr and saw_idat and length == 0
                        and payload_end + 4 == len(data), "invalid_png_end")
                saw_iend = True
            require(not saw_iend or payload_end + 4 == len(data),
                    "png_bytes_after_iend")
            chunk_count += 1
            require(chunk_count <= 4096, "png_chunk_count_budget")
            position = payload_end + 4
        require(saw_ihdr and saw_idat and saw_iend, "incomplete_png_structure")


def managed_node(project: dict, scene_id: str, node_id: str) -> dict:
    scenes = project.get("scenes")
    require(type(scenes) is list, "invalid_managed_scenes")
    match = [scene for scene in scenes
             if type(scene) is dict and scene.get("id") == scene_id]
    require(len(match) == 1, "managed_scene_not_unique")
    nodes = match[0].get("nodes")
    require(type(nodes) is list, "invalid_managed_nodes")
    selected = [node for node in nodes
                if type(node) is dict and node.get("id") == node_id]
    require(len(selected) == 1, "managed_node_not_unique")
    return selected[0]


def managed_snapshot(root: Path, record: object, source_inventory: list[dict],
                     count: int, width: int, height: int, film: dict) -> dict:
    snapshot = checked_json(bounded_file(root, record))
    snapshot = exact(snapshot, {"project", "fingerprint", "refs", "generated"},
                     "invalid_managed_inspection")
    require(type(snapshot["fingerprint"]) is str and HEX.fullmatch(snapshot["fingerprint"]) is not None,
            "invalid_native_fingerprint")
    require(type(snapshot["refs"]) is list, "missing_native_object_refs")
    managed = snapshot["project"]
    require(type(managed) is dict
            and managed.get("schema_version") == 1 and type(managed.get("schema_version")) is int
            and managed.get("component_version") == 1 and type(managed.get("component_version")) is int
            and type(managed.get("revision")) is int and managed["revision"] >= 1
            and type(managed.get("id")) is str and 1 <= len(managed["id"]) <= 128
            and type(managed.get("generation")) is str
            and re.fullmatch(r"[0-9a-f]{32}", managed["generation"]) is not None,
            "invalid_managed_project_identity")
    settings = managed.get("settings")
    require(type(settings) is dict
            and type(settings.get("width")) is int and type(settings.get("height")) is int
            and type(settings.get("fps")) is int
            and type(settings.get("fps_denominator", 1)) is int
            and (settings["width"], settings["height"], settings["fps"],
                 settings.get("fps_denominator", 1)) == (width, height, 30, 1),
            "managed_renderer_profile_differs_from_film")
    # The exact pinned Semwright Motion Canvas authoring binding retains the
    # original Film and digests. Matching generated files alone is insufficient:
    # a valid unrelated managed project would otherwise be admitted.
    authoring = managed.get("authoring")
    authoring = exact(authoring, {
        "schema_version", "intent", "intent_digest", "realization",
        "realization_digest", "projection_digest"}, "managed_authoring_binding_missing")
    require(type(authoring["schema_version"]) is int and authoring["schema_version"] == 1,
            "managed_authoring_version_mismatch")
    require(type(authoring["intent"]) is dict and authoring["intent"] == film,
            "managed_authoring_intent_not_bound_to_film")
    require(type(authoring["realization"]) is dict,
            "managed_authoring_realization_missing")
    for name in ("intent_digest", "realization_digest", "projection_digest"):
        require(type(authoring[name]) is str and HEX.fullmatch(authoring[name]) is not None,
                "managed_authoring_digest_malformed")
    # Semantic validity of these typed digests is the pinned Host verifier's
    # job. This reader checks shape and Film equality, not implementation
    # of semwright_semantic_composition::canonical_digest.
    source_rows = snapshot["generated"]
    require(type(source_rows) is list and 1 <= len(source_rows) <= MAX_SOURCE_FILES,
            "managed_source_generated_inventory_required")
    generated = []
    for item in source_rows:
        exact(item, {"path", "sha256", "bytes"}, "invalid_generated_file_record")
        require(type(item["path"]) is str
                and type(item["sha256"]) is str and HEX.fullmatch(item["sha256"]) is not None
                and type(item["bytes"]) is int and item["bytes"] > 0,
                "invalid_generated_file_record")
        generated.append({"name": item["path"], "sha256": item["sha256"],
                          "bytes": item["bytes"]})
    require(sorted(generated, key=lambda row: row["name"]) == source_inventory,
            "native_generated_source_does_not_match_retained_files")
    return snapshot


def assert_managed_edit(original: dict, edited: dict, request: dict) -> None:
    require(request["native_property"] == "fill"
            and type(request["native_scene_id"]) is str
            and type(request["native_node_id"]) is str,
            "unadmitted_managed_edit")
    before, after = original["project"], edited["project"]
    require(before["id"] == after["id"]
            and before["generation"] == after["generation"]
            and type(before["revision"]) is int and type(after["revision"]) is int
            and after["revision"] == before["revision"] + 1,
            "managed_edit_identity_or_revision_changed")
    before_node = managed_node(before, request["native_scene_id"], request["native_node_id"])
    after_node = managed_node(after, request["native_scene_id"], request["native_node_id"])
    old_props, new_props = before_node.get("properties"), after_node.get("properties")
    require(type(old_props) is dict and type(new_props) is dict
            and old_props.get("fill") == request["from"]
            and new_props.get("fill") == request["to"],
            "managed_node_property_not_persisted")
    expected = copy.deepcopy(before)
    expected["revision"] = after["revision"]
    managed_node(expected, request["native_scene_id"], request["native_node_id"])["properties"]["fill"] = request["to"]
    # ManagedBinding.intent/realization/digests change when the existing
    # authorized compositor applies the edited canonical Film. Their exact
    # recomputation must be verified by the native Host, not this Python gate.
    expected["authoring"] = copy.deepcopy(after["authoring"])
    require(expected == after, "managed_edit_changed_unrelated_native_state")
    require(original["fingerprint"] != edited["fingerprint"],
            "managed_source_fingerprint_not_changed")


def assert_only_film_source_color_changed(original: dict, edited: dict,
                                          from_fill: str, to_fill: str) -> int:
    """One predeclared fill edit must never change unrelated Film semantics."""
    expected = copy.deepcopy(original)
    def update(value: object) -> int:
        if type(value) is dict:
            total = 0
            for key, child in value.items():
                if type(child) is str and child == from_fill:
                    value[key] = to_fill
                    total += 1
                else:
                    total += update(child)
            return total
        if type(value) is list:
            total = 0
            for index, child in enumerate(value):
                if type(child) is str and child == from_fill:
                    value[index] = to_fill
                    total += 1
                else:
                    total += update(child)
            return total
        return 0

    changed = update(expected)
    require(changed > 0, "canonical_film_color_property_not_found")
    require(expected == edited, "unrelated_film_semantics_or_clock_changed")
    return changed


def one_stage(root: Path, stage: object, expected_count: int,
              width: int, height: int) -> dict:
    stage = exact(stage, {"session", "project", "film", "source_files",
                          "managed_inspection", "frames_dir", "native_job_ref"},
                  "invalid_stage_fields")
    require(type(stage["session"]) is str and 1 <= len(stage["session"]) <= 128
            and type(stage["native_job_ref"]) is str and 1 <= len(stage["native_job_ref"]) <= 128,
            "missing_stage_identity")
    project = checked_json(bounded_file(root, stage["project"]))
    film = checked_json(bounded_file(root, stage["film"]))
    output = film.get("output")
    require(type(output) is dict and type(output.get("width")) is int
            and type(output.get("height")) is int
            and (output["width"], output["height"]) == (width, height),
            "film_output_geometry_changed")
    native_clock = output.get("frame_rate")
    require(type(native_clock) is dict and set(native_clock) == {"num", "den"}
            and type(native_clock["num"]) is int and type(native_clock["den"]) is int
            and (native_clock["num"], native_clock["den"]) == (30, 1),
            "film_source_frame_rate_mismatch")
    inventory = source_files(root, stage["source_files"])
    native_snapshot = managed_snapshot(
        root, stage["managed_inspection"], inventory, expected_count, width, height, film)
    frames_path = safe_path(root, stage["frames_dir"], directory=True)
    rendered_frames = frames_path / "frames"
    require(rendered_frames.is_dir() and not rendered_frames.is_symlink()
            and len(list(rendered_frames.iterdir())) == expected_count,
            "extra_or_missing_native_frame")
    # Existing independent proof: verify every source manifest entry against
    # its actual PNG bytes, then decode every one of those PNGs to raw RGBA.
    validate_png_crc_sequence(frames_path, expected_count, width, height)
    try:
        frame_bytes = native_png_hashes(frames_path, expected_count)
        pixels = decoded_rgba_frame_hashes(frames_path, expected_count, width, height)
    except (DirectParityError, OSError, ValueError) as error:
        raise RoundtripRefused("native_frame_integrity_or_decode_failed") from error
    require(len(frame_bytes) == len(pixels) == expected_count,
            "incomplete_native_frame_decode")
    return {
        "source_project": project, "film": film, "source_inventory": inventory,
        "native_project_inspection": native_snapshot,
        "frame_file_digests": frame_bytes, "rgba_frame_digests": pixels,
        "project_sha256": stage["project"]["sha256"],
        "film_sha256": stage["film"]["sha256"],
        "session": stage["session"], "native_job_ref": stage["native_job_ref"],
        "frame_count": expected_count,
    }


def inspect(root: Path, witness: dict, *, _unit_fixture: bool = False) -> dict:
    witness = exact(witness, {"schema", "profile", "edit", "stages"}, "invalid_roundtrip_witness")
    require(witness["schema"] == SCHEMA, "wrong_roundtrip_schema")
    profile = exact(witness["profile"], {"frames", "width", "height", "fps"},
                    "invalid_roundtrip_profile")
    count, width, height = profile["frames"], profile["width"], profile["height"]
    require(all(type(x) is int for x in (count, width, height)) and 1 <= count <= 120
            and 1 <= width <= 3840 and 1 <= height <= 3840
            and profile["fps"] == "30/1", "unadmitted_frame_clock_or_geometry")
    if not _unit_fixture:
        require((count, width, height) == (60, 1920, 1080),
                "required_native_60frame_profile")
    edit = exact(witness["edit"],
                 {"scene_id", "node_id", "property", "native_scene_id",
                  "native_node_id", "native_property", "from", "to"},
                 "invalid_edit_contract")
    stages = exact(witness["stages"], set(STAGES), "incomplete_roundtrip_stages")
    seen = {key: one_stage(root, stages[key], count, width, height) for key in STAGES}
    sessions = [seen[key]["session"] for key in STAGES]
    jobs = [seen[key]["native_job_ref"] for key in STAGES]
    require(len(set(sessions)) == len(sessions), "reopen_did_not_use_fresh_session")
    require(len(set(jobs)) == len(jobs), "native_job_identity_reused")
    original, reopened = seen["original"], seen["reopened"]
    edited, edited_reopened = seen["edited"], seen["edited_reopened"]
    for first, second, label in (
        (original, reopened, "original_reopen"),
        (edited, edited_reopened, "edited_reopen"),
    ):
        require(first["source_project"] == second["source_project"],
                label + "_project_changed")
        require(first["film"] == second["film"], label + "_film_changed")
        require(first["source_inventory"] == second["source_inventory"],
                label + "_native_source_changed")
        require(first["native_project_inspection"] == second["native_project_inspection"],
                label + "_managed_project_changed")
        require(first["rgba_frame_digests"] == second["rgba_frame_digests"],
                label + "_visible_pixels_changed")
    change = assert_source_edit(original["source_project"], edited["source_project"], edit)
    assert_managed_edit(original["native_project_inspection"],
                        edited["native_project_inspection"], edit)
    require(original["film"] != edited["film"], "source_edit_not_realized_in_film")
    authorized_film_color_edits = assert_only_film_source_color_changed(
        original["film"], edited["film"], edit["from"], edit["to"])
    require(original["source_inventory"] != edited["source_inventory"],
            "persisted_native_source_did_not_change")
    # This finite fill-edit profile changes every visible frame. It cannot be
    # certified by one thumbnail or by one changed end frame.
    for at, (before, after) in enumerate(zip(original["rgba_frame_digests"],
                                             edited["rgba_frame_digests"], strict=True)):
        require(before != after, "edited_pixels_unchanged_at_frame_" + str(at))
    # Re-validate all witnesses after every decode. A malicious/simultaneous
    # mutation must not replace stage files between observation and receipt.
    for name in STAGES:
        row = stages[name]
        bounded_file(root, row["project"])
        bounded_file(root, row["film"])
        bounded_file(root, row["managed_inspection"])
        for source in row["source_files"]:
            bounded_file(root, {field: source[field]
                         for field in ("path", "bytes", "sha256")}, maximum=MAX_SOURCE)
        try:
            current_frames = safe_path(root, row["frames_dir"], directory=True)
            validate_png_crc_sequence(current_frames, count, width, height)
            observed = native_png_hashes(current_frames, count)
        except (DirectParityError, OSError, ValueError) as error:
            raise RoundtripRefused("native_frame_bytes_changed_after_decode") from error
        require(observed == seen[name]["frame_file_digests"],
                "native_frame_bytes_changed_after_decode")
    return {
        "schema": SCHEMA,
        "inspection": "UNIT_FIXTURE_INSPECTOR_ONLY" if _unit_fixture
                      else "REQUIRED_60FRAME_SOURCE_AND_DECODED_FRAMES_ONLY",
        "embedded_native_png_pts_verified": False,
        "actual_provider_session_and_native_grants_verified": False,
        "no_automatic_native_dispatch": True,
        "managed_authoring_intent_equal_to_canonical_film": True,
        "managed_authoring_digest_computation_independently_verified": False,
        "provider_authority_verified": False,
        "permissions_verified": False,
        "rights_verified": False,
        "human_visual_approval": False,
        "profile": profile, "edit": change,
        "authorized_film_color_changes": authorized_film_color_edits,
        "unrelated_film_semantics_unchanged": True,
        "all_frames_before_after_and_both_reopens_checked": count,
        "all_frame_rgba_identical_original_vs_reopen": True,
        "all_frame_rgba_identical_edit_vs_second_reopen": True,
        "all_frames_changed_after_declared_fill_edit": True,
        "retained_native_source_files_each_stage": {
            name: seen[name]["source_inventory"] for name in STAGES
        },
        "project_sha256_by_stage": {name: seen[name]["project_sha256"] for name in STAGES},
        "managed_source_fingerprints_by_stage": {
            name: seen[name]["native_project_inspection"]["fingerprint"]
            for name in STAGES
        },
        "managed_generated_inventory_fully_bound": True,
        "film_sha256_by_stage": {name: seen[name]["film_sha256"] for name in STAGES},
        "decoded_rgba_digest_chain_by_stage": {
            name: digest("".join(seen[name]["rgba_frame_digests"]).encode("ascii"))
            for name in STAGES
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--witness", type=str, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    require(root.is_dir() and not root.is_symlink(), "invalid_custody_root")
    require(not args.output.exists() and args.output.parent.is_dir(),
            "new_report_destination_required")
    witness_path = safe_path(root, args.witness)
    stat_before = witness_path.stat()
    size = stat_before.st_size
    require(0 < size <= MAX_JSON, "witness_budget")
    with witness_path.open("rb") as stream:
        evidence_bytes = stream.read(size + 1)
    stat_after = witness_path.stat()
    require(len(evidence_bytes) == size and stat_after.st_size == size
            and stat_before.st_ino == stat_after.st_ino
            and stat_before.st_mtime_ns == stat_after.st_mtime_ns,
            "witness_changed_during_read")
    witness = checked_json(evidence_bytes)
    # Inspect only. A new output cannot replace any prior native job or receipt.
    report = inspect(root, witness)
    require(not args.output.exists() and args.output.parent.is_dir(),
            "new_report_destination_required")
    with args.output.open("xb") as stream:
        stream.write((json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
