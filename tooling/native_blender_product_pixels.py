"""Independent decoded-pixel observation for the fixed Blender product source.

This consumes already retained native terminals. It neither executes Blender
nor restores a source witness, execution grant, or formal acceptance.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from mlt_official_runtime_intake import capture_regular
from native_blender_original_evidence import HEX, _plain_owned_directory, new_bytes, require
from native_direct_mediated_parity import decoded_rgba_png_capture
from native_motion_canvas_roundtrip import validate_png_crc_bytes

PROFILE = "blender-product-vessel-v1"
WIDTH, HEIGHT = 1920, 1080
SURFACE_FRAME = 601
SURFACE_PNG = "blender-product-frame601.png"
MAX_PNG = 32 * 1024 * 1024


def _object(value: object, code: str) -> dict:
    require(type(value) is dict, code)
    return value


def _stamp(value: object) -> dict:
    value = _object(value, "product_pixels_stamp")
    require(set(value) == {"resource", "generation", "revision"}
            and type(value["resource"]) is str and bool(value["resource"])
            and type(value["generation"]) is str and bool(value["generation"])
            and type(value["revision"]) is int and 0 <= value["revision"] < 2**64,
            "product_pixels_stamp")
    return value


def _digest(value: object) -> str:
    require(type(value) is str and HEX.fullmatch(value) is not None,
            "product_pixels_digest")
    return value


def _native_file(value: object) -> dict:
    value = _object(value, "product_pixels_native_source")
    _digest(value.get("sha256"))
    require(type(value.get("bytes")) is int and value["bytes"] > 0
            and type(value.get("relative_path")) is str and bool(value["relative_path"]),
            "product_pixels_native_source")
    return value


def bound_surface_pair(seed: dict, native: dict) -> tuple[dict, dict, dict]:
    """Join one trusted caller result; this is not Broker reauthentication."""
    seed = _object(seed, "product_pixels_seed")
    native = _object(native, "product_pixels_native")
    require(seed.get("schema") == "motionwright.blender.product-scene-seed/1"
            and native.get("schema") == "motionwright.blender.product-cycle-evidence/1"
            and native.get("phase_contract_verified") is True,
            "product_pixels_caller_publication")
    terminal = _object(native.get("native_terminal"), "product_pixels_terminal")
    require(terminal.get("status") == "ok", "product_pixels_native_not_completed")
    value = _object(terminal.get("value"), "product_pixels_cycle_value")
    require(value.get("schema") == "motionwright.blender.product-cycle-caller/2",
            "product_pixels_cycle_schema")
    request = _object(native.get("request"), "product_pixels_request")
    create = _object(value.get("create"), "product_pixels_create")
    edit = _object(value.get("edit"), "product_pixels_edit")
    verify = _object(value.get("verify"), "product_pixels_verify")
    require(create.get("schema") == "motionwright.blender.product-create-probe-save/1"
            and edit.get("schema") == verify.get("schema")
            == "motionwright.blender.product-editable-cycle/1",
            "product_pixels_phase_schema")
    for key in ("project_id", "scene_id", "capsule_id"):
        require(type(seed.get(key)) is str and bool(seed[key])
                and seed[key] == request.get(key) == value.get(key) == create.get(key),
                "product_pixels_scope_join")
    require(edit.get("project_id") == verify.get("project_id") == seed["project_id"],
            "product_pixels_phase_project")
    expected = _stamp(seed.get("expected"))
    require(expected == _stamp(request.get("expected")) == _stamp(create.get("expected"))
            == _stamp(value.get("expected_before")) == _stamp(native.get("expected_before")),
            "product_pixels_before_stamp_join")
    require(_stamp(verify.get("expected")) == _stamp(value.get("expected_after")),
            "product_pixels_after_stamp_join")
    original_attached = _stamp(edit.get("expected"))
    edited_attached = _stamp(value.get("expected_after"))
    require(expected["revision"] <= 2**64 - 3
            and original_attached["resource"] == edited_attached["resource"] == expected["resource"]
            and original_attached["generation"] == edited_attached["generation"] == expected["generation"]
            and original_attached["revision"] == expected["revision"] + 1
            and edited_attached["revision"] == expected["revision"] + 2,
            "product_pixels_atomic_revision_chain")
    recipe_sha = _digest(seed.get("source_sha256"))
    require(recipe_sha == value.get("recipe_sha256") == create.get("recipe_source_sha256")
            and type(seed.get("source_asset_id")) is str and bool(seed["source_asset_id"])
            and seed["source_asset_id"] == value.get("recipe_asset_id")
            == create.get("recipe_source_asset_id"), "product_pixels_recipe_join")
    journal = _object(native.get("journal"), "product_pixels_journal")
    create_journal = _object(journal.get("create"), "product_pixels_create_journal")
    source = _object(create_journal.get("source_binding"), "product_pixels_source_binding")
    for key in ("project_id", "scene_id", "capsule_id"):
        require(source.get(key) == seed[key], "product_pixels_source_scope")
    require(source.get("source_asset_id") == seed["source_asset_id"]
            and source.get("source_sha256") == recipe_sha
            and _stamp(source.get("expected")) == expected, "product_pixels_source_recipe")
    # RationalTime serializes decimal strings; this journal's rate uses integers.
    rate = _object(source.get("rate"), "product_pixels_native_rate")
    require(source.get("canonical_scene_duration") == {"num": "40", "den": "1"}
            and type(source.get("native_frame_origin")) is int
            and source["native_frame_origin"] == 1
            and type(source.get("native_frame_count")) is int
            and source["native_frame_count"] == 1200
            and set(rate) == {"num", "den"}
            and type(rate["num"]) is int and type(rate["den"]) is int
            and rate == {"num": 30, "den": 1}
            and source.get("internal_cuts") == [
                {"camera": "camera_form", "first_native_frame": 1},
                {"camera": "camera_surface", "first_native_frame": SURFACE_FRAME}],
            "product_pixels_fixed_source_profile")
    require(all(type(cut) is dict and type(cut.get("first_native_frame")) is int
                for cut in source["internal_cuts"]), "product_pixels_internal_cut_types")
    recipe = _object(seed.get("product_scene"), "product_pixels_recipe")
    before, after = recipe.get("annotation"), request.get("annotation_body")
    require(type(recipe.get("version")) is int and recipe["version"] == 1
            and type(before) is str and type(after) is str and before != after,
            "product_pixels_annotation_edit")
    require(edit.get("edit") == {"parameter": "product.annotation.body",
                                "before": before, "after": after}
            and edit.get("one_field_edit_readback_verified") is True
            and verify.get("annotation") == {"original": before, "persisted": after}
            and verify.get("edited_source_fresh_reopen_performed") is True
            and value.get("same_capsule_repointed") is True
            and value.get("native_sources_persisted_in_cas") is True,
            "product_pixels_edit_lineage")
    original_source = _native_file(value.get("original_source"))
    edited_source = _native_file(value.get("edited_source"))
    require(original_source == create.get("output") == edit.get("input_source")
            and edited_source == edit.get("output") == verify.get("reopened_source")
            and original_source["sha256"] != edited_source["sha256"],
            "product_pixels_saved_source_chain")
    cuts = create.get("cut_probes")
    require(type(cuts) is list and len(cuts) == 2
            and all(type(cut) is dict and type(cut.get("frame")) is int for cut in cuts)
            and [cut["frame"] for cut in cuts] == [600, SURFACE_FRAME],
            "product_pixels_original_cut_pair")
    original = cuts[1]
    edited = _object(verify.get("surface_probe"), "product_pixels_edited_surface")
    camera = original.get("expected_camera")
    require(type(camera) is str and bool(camera) and edited.get("expected_camera") == camera,
            "product_pixels_camera_join")
    artifacts = []
    scene_names = []
    for cut in (original, edited):
        scene = _object(cut.get("scene_after_render"), "product_pixels_scene_readback")
        ack = _object(cut.get("render_ack"), "product_pixels_render_ack")
        artifact = _object(cut.get("artifact"), "product_pixels_artifact")
        require(type(cut.get("frame")) is int and cut["frame"] == SURFACE_FRAME
                and cut.get("annotation_hidden_after_render") is False
                and type(scene.get("frame")) is int and scene["frame"] == SURFACE_FRAME
                and scene.get("camera") == camera and scene.get("engine") == "CYCLES"
                and type(scene.get("resolution")) is list
                and all(type(size) is int for size in scene["resolution"])
                and scene["resolution"] == [WIDTH, HEIGHT]
                and type(scene.get("name")) is str and bool(scene["name"]),
                "product_pixels_native_render_state")
        # Relevant ACK fields are strict; additional legitimate SDK fields are allowed.
        require(ack.get("changed") is True and ack.get("path") == SURFACE_PNG
                and ack.get("format") == "png", "product_pixels_render_ack")
        require(artifact.get("relative_path") == SURFACE_PNG
                and type(artifact.get("width")) is int and artifact["width"] == WIDTH
                and type(artifact.get("height")) is int and artifact["height"] == HEIGHT
                and type(artifact.get("bytes")) is int and 33 <= artifact["bytes"] <= MAX_PNG
                and artifact.get("png_header_checked") is True,
                "product_pixels_artifact_profile")
        _digest(artifact.get("sha256"))
        artifacts.append(artifact)
        scene_names.append(scene["name"])
    require(scene_names[0] == scene_names[1]
            and verify.get("original_surface_png_sha256") == artifacts[0]["sha256"],
            "product_pixels_original_surface_binding")
    require(type(request.get("request_id")) is str and 1 <= len(request["request_id"]) <= 48
            and request["request_id"].isascii()
            and all(char.isalnum() or char in "-_" for char in request["request_id"]),
            "product_pixels_request_identity")
    return artifacts[0], artifacts[1], {
        "profile": PROFILE, "source_scope": "fixed_40_second_editable_product",
        "project_id": seed["project_id"], "scene_id": seed["scene_id"],
        "capsule_id": seed["capsule_id"], "request_id": request["request_id"],
        "recipe_asset_id": seed["source_asset_id"], "recipe_sha256": recipe_sha,
        "expected_before": expected, "original_attached": original_attached,
        "expected_after": edited_attached,
        "original_source": original_source, "edited_source": edited_source,
        "annotation_edit": edit["edit"], "native_frame": SURFACE_FRAME,
        "source_local_frame_zero_based": SURFACE_FRAME - 1,
        "native_frame_count": 1200, "native_rate": rate,
        "canonical_scene_duration": source["canonical_scene_duration"],
        "observed_camera": camera, "observed_scene": scene_names[0],
        "geometry_or_camera_recertified_by_pixel_reader": False,
        "execution_authority_recovered": False,
    }


def checked_png(png: bytes, width: int = WIDTH, height: int = HEIGHT) -> dict:
    require(type(width) is int and type(height) is int and width > 0 and height > 0
            and width <= 3840 and height <= 3840
            and width * height <= WIDTH * HEIGHT, "product_pixels_geometry_budget")
    header = validate_png_crc_bytes(png, width, height, max_bytes=MAX_PNG)
    require(tuple(header[key] for key in
                  ("bit_depth", "color_type", "compression", "filter", "interlace"))
            == (8, 6, 0, 0, 0) and header["animated"] is False,
            "product_pixels_static_rgba8_required")
    return header


def rgba_difference(original: bytes, edited: bytes, width: int, height: int) -> dict:
    require(type(width) is int and type(height) is int and width > 0 and height > 0
            and width <= 3840 and height <= 3840
            and width * height <= WIDTH * HEIGHT, "product_pixels_geometry_budget")
    require(type(original) is bytes and type(edited) is bytes
            and len(original) == len(edited) == width * height * 4,
            "product_pixels_decoded_size")
    ranges = [[min(data[c::4]), max(data[c::4])]
              for data in (original, edited) for c in range(4)]
    require(ranges[3] == ranges[7] == [255, 255], "product_pixels_must_be_opaque")
    changed = total_error = maximum = 0
    for offset in range(0, len(original), 4):
        deltas = [abs(original[offset + channel] - edited[offset + channel])
                  for channel in range(3)]
        changed += any(deltas)
        total_error += sum(deltas)
        maximum = max(maximum, *deltas)
    return {
        "width": width, "height": height, "pixel_format": "rgba", "bit_depth": 8,
        "original_rgba_sha256": hashlib.sha256(original).hexdigest(),
        "edited_rgba_sha256": hashlib.sha256(edited).hexdigest(),
        "original_channel_ranges_rgba": ranges[:4], "edited_channel_ranges_rgba": ranges[4:],
        "changed_rgb_pixel_count": changed, "rgb_absolute_error_sum": total_error,
        "maximum_rgb_channel_difference": maximum,
        "decoded_pixel_difference_verified": changed > 0,
        "claim_scope": "Exact decoded RGB samples differ at the bound surface frame.",
        "constant_background_verified": False, "exclusive_edit_attribution_verified": False,
        "glyph_legibility_verified": False, "full_fidelity_verified": False,
        "human_acceptance": False,
    }


def retained_decode(png: bytes, *, ffmpeg: Path, executable_sha256: str,
                    prefix: Path, record: dict) -> bytes:
    require(type(record) is dict and not record, "product_pixels_decode_record_must_be_new")
    capture = {}
    decoded = None
    original_error = None
    custody_errors = []
    try:
        decoded = decoded_rgba_png_capture(
            png, WIDTH, HEIGHT, ffmpeg=ffmpeg,
            executable_sha256=executable_sha256, capture=capture)
    except BaseException as error:
        original_error = error
    finally:
        record["process_state"] = capture.get("state", "NOT_STARTED")
        record["terminal"] = capture.get("terminal")
        record["decoder"] = capture.get("decoder")
        record["stdin_bytes"] = capture.get("stdin_bytes")
        record["stdin_sha256"] = capture.get("stdin_sha256")
        record["streams"] = {}
        for name, suffix in (("stdout", ".rgba"), ("stderr", ".stderr")):
            body = capture.get(name)
            if type(body) is not bytes:
                continue
            path = prefix.with_suffix(suffix)
            item = {"state": "RESERVED", "file": path.name,
                    "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            record["streams"][name] = item
            try:
                retained = new_bytes(path, body)
                require(retained["bytes"] == item["bytes"]
                        and retained["sha256"] == item["sha256"],
                        "product_pixels_retained_stream_mismatch")
                item["state"] = "RETAINED"
            except BaseException as error:
                item["state"] = "FAILED"
                custody_errors.append({"stream": name, "type": type(error).__name__,
                                       "message": str(error)})
        record["stream_custody_errors"] = custody_errors
        if original_error is not None:
            record["decoder_error"] = {"type": type(original_error).__name__,
                                       "message": str(original_error)}
    if original_error is not None:
        raise original_error
    require(not custody_errors, "product_pixels_decoder_stream_custody_incomplete")
    require(type(decoded) is bytes, "product_pixels_decoder_has_no_output")
    return decoded


def observe_product_surface_pixels(
    seed: dict, native: dict, *, create_root: Path, verify_root: Path,
    evidence_root: Path, native_evidence_sha256: str, ffmpeg: Path,
    executable_sha256: str, record: dict,
) -> dict:
    require(type(record) is dict and not record, "product_pixels_record_must_be_new")
    record.update({
        "schema": "motionwright.blender.product-decoded-surface-difference/1",
        "native_terminal_modified": False, "automatic_native_retry": False,
        "decoded_pixel_difference_verified": False, "human_acceptance": False,
    })
    original_artifact, edited_artifact, source = bound_surface_pair(seed, native)
    record["source_binding"] = source
    record["native_evidence_sha256"] = _digest(native_evidence_sha256)
    for root in (create_root, verify_root, evidence_root):
        _plain_owned_directory(root)
    require(create_root.resolve() != verify_root.resolve(), "product_pixels_distinct_roots")
    decoded = []
    record["native_pngs"] = {}
    record["decoders"] = {}
    for phase, root, artifact in (
        ("original", create_root, original_artifact), ("edited", verify_root, edited_artifact),
    ):
        path = root / SURFACE_PNG
        png, binding = capture_regular(path, MAX_PNG)
        require(len(png) == artifact["bytes"]
                and hashlib.sha256(png).hexdigest() == artifact["sha256"],
                "product_pixels_artifact_bytes_changed")
        record["native_pngs"][phase] = {"artifact": artifact, "capture": binding}
        record["native_pngs"][phase]["png_header"] = checked_png(png)
        decode_record = {}
        record["decoders"][phase] = decode_record
        pixels = retained_decode(png, ffmpeg=ffmpeg, executable_sha256=executable_sha256,
                                 prefix=evidence_root / phase, record=decode_record)
        again, after = capture_regular(path, MAX_PNG)
        require(again == png and after["before"] == binding["before"],
                "product_pixels_native_file_changed_during_decode")
        record["native_pngs"][phase]["capture_after_decode"] = after
        decoded.append(pixels)
    record["comparison"] = rgba_difference(decoded[0], decoded[1], WIDTH, HEIGHT)
    record["decoded_pixel_difference_verified"] = record["comparison"]["decoded_pixel_difference_verified"]
    return record
