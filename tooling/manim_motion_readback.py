#!/usr/bin/env python3
"""Independent source/getter/all-frame oracle for the closed Manim v3 fixture.

This reader never imports the supplied scene.py and never starts Manim. Decoder
supervision is shared with the existing readback. Original provider permissions,
job custody and canonical edits remain separate native coordinator obligations.
"""
from __future__ import annotations

import argparse
import ast
import copy
import io
import json
import math
import shutil
import stat
import sys
import unittest
import zipfile
from fractions import Fraction
from pathlib import Path

import manim_native_readback as base

ReadbackError = base.ReadbackError
require = base.require
parse_json = base.parse_json
sha = base.sha
closed = base.closed_object
uint = base.uint
rational = base.rational

PROFILE = "mw-manim-planar-motion-v3"
COMPILER = "motionwright.manim.rust.planar_motion.v3.1"
SAMPLER = "manim-0.21-cairo-midpoint-play-v1"
# Exact constant program emitted by planar_motion.rs, excluding the data line.
# Updating this hash requires the source/runtime contract review, not a render retry.
PROGRAM_SHA256 = "f1f5c1d7e5db6efeffb245485d923487245230ca2accbf5d22c75d8273a3a020"
MEMBERS = {"plan.json", "scene.py", "render-config.json", "assets.lock.json", "manifest.json"}
MAX_SOURCE = 2 * 1024 * 1024
MAX_ENTRY = 256 * 1024
MAX_TRACE = 8 * 1024 * 1024
MAX_VIDEO = 512 * 1024 * 1024
WIDTH, HEIGHT = 640, 360
FRAME_BYTES = WIDTH * HEIGHT * 3
CHANNEL_ERROR = 16
MEAN_CHANNEL_ERROR = 4.0
BAD_PIXEL_FRACTION = 0.003
BOUNDARY_MARGIN = 2.0
RED = "20000000-0000-4000-8000-000000000001"
GREEN = "20000000-0000-4000-8000-000000000002"
PHASES = ("original", "reopened", "edited", "edited-reopened", "edge25", "camera", "camera-reopened")
PROPERTIES = ("x", "y", "width", "height", "rotation_deg", "opacity")
EASINGS = {"hold", "linear", "ease_out_cubic", "ease_in_out"}
LOSSES = [
    "binary64_geometry_after_rational_interval_selection",
    "opaque_black_matte_no_output_alpha", "no_text_graph_masks_stroke_or_dynamic_camera",
    "no_audio_or_captions", "safe_margin_preserved_editorial_guide",
    "requested_rec709_master_requires_independent_colorimetry_gate",
    "generated_python_edits_outside_plan_not_importable",
    "midpoint_play_sampler_requires_all_frame_runtime_gate",
]


def number(value, reason):
    require(type(value) in (int, float), reason)
    try:
        require(math.isfinite(value), reason)
    except OverflowError as error:
        raise ReadbackError(reason) from error
    return float(value)


def near(actual, expected, reason, tolerance=1e-7):
    require(abs(number(actual, reason) - expected) <= tolerance, reason)


def same_json(actual, expected):
    # bool is an int subclass in Python; typed source flags must remain bools.
    if type(actual) is bool or type(expected) is bool:
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, dict):
        return type(actual) is dict and set(actual) == set(expected) and all(
            same_json(actual[k], value) for k, value in expected.items())
    if isinstance(expected, list):
        return type(actual) is list and len(actual) == len(expected) and all(
            same_json(a, e) for a, e in zip(actual, expected))
    return actual == expected


def color(value):
    require(type(value) is str and len(value) == 7 and value.startswith("#")
            and all(c in "0123456789abcdefABCDEF" for c in value[1:]), "motion_fill")
    return tuple(int(value[i:i+2], 16) for i in (1, 3, 5))


def pose(value):
    closed(value, set(PROPERTIES), "motion_pose_fields")
    for key in PROPERTIES:
        number(value[key], "motion_pose_finite")
    require(0 < value["width"] <= 17280 and 0 < value["height"] <= 17280
            and 0 <= value["opacity"] <= 1 and abs(value["rotation_deg"]) <= 36000,
            "motion_pose_bounds")
    require(abs((value["x"] + value["width"] / 2 - 960) / 135) <= 128
            and abs((540 - value["y"] - value["height"] / 2) / 135) <= 128,
            "motion_pose_center")
    return value


def validate_plan(value):
    closed(value, {"schema_version", "profile_id", "output_role", "binding", "clock",
                   "render_profile", "canvas", "background", "camera", "nodes"}, "motion_plan_fields")
    require(type(value["schema_version"]) is int and value["schema_version"] == 3
            and value["profile_id"] == PROFILE
            and value["output_role"] == "scene_video_contribution_without_audio_or_captions", "motion_plan_kind")
    base.source_binding(value["binding"], "motion_plan_binding")
    require(same_json(value["canvas"], {"width": 1920, "height": 1080, "coordinate_space": "project_pixels"})
            and same_json(value["background"], {"kind": "solid", "color": "#000000", "opacity": 1}), "motion_canvas_matte")
    c = value["clock"]
    closed(c, {"timeline_start", "duration", "frame_rate", "project_start_frame",
               "project_end_frame_exclusive", "local_start_frame", "frame_count"}, "motion_clock_fields")
    start, duration, rate = (rational(c[k]) for k in ("timeline_start", "duration", "frame_rate"))
    require(start >= 0 and duration > 0 and rate.denominator == 1 and 1 <= rate <= 120, "motion_clock_values")
    count = uint(c["frame_count"], 3600, "motion_clock_count", 2)
    for key in ("project_start_frame", "project_end_frame_exclusive", "local_start_frame"):
        uint(c[key], 2**64-1, "motion_clock_integer")
    require(start * rate == c["project_start_frame"] and duration * rate == count
            and c["project_end_frame_exclusive"] == c["project_start_frame"] + count
            and c["local_start_frame"] == 0, "motion_clock_exact_interval")
    p = value["render_profile"]
    closed(p, {"width", "height", "frame_rate"}, "motion_profile_fields")
    uint(p["width"], 7680, "motion_render_width", 320)
    uint(p["height"], 4320, "motion_render_height", 240)
    uint(p["frame_rate"], 120, "motion_render_rate", 1)
    require(p["width"] * 9 == p["height"] * 16 and p["width"] % 2 == 0
            and p["height"] % 2 == 0 and p["frame_rate"] == rate
            and p["width"] * p["height"] <= 33177600, "motion_render_profile")
    cam = value["camera"]
    closed(cam, {"center_x", "center_y", "zoom", "rotation_deg", "safe_margin"}, "motion_camera_fields")
    for val in cam.values():
        number(val, "motion_camera_finite")
    require(cam["rotation_deg"] == 0 and 0.01 <= cam["zoom"] <= 100
            and 0 <= cam["safe_margin"] <= 0.49
            and abs((cam["center_x"] - 960) / 135) <= 128
            and abs((540 - cam["center_y"]) / 135) <= 128, "motion_camera_unsupported")
    nodes = value["nodes"]
    require(type(nodes) is list and 1 <= len(nodes) <= 512 and len(nodes) * count <= 32768
            and 2048 + count * 1024 + len(nodes) * count * 1024 <= min(MAX_TRACE, MAX_ENTRY), "motion_observation_budget")
    identities = set()
    for ordinal, node in enumerate(nodes):
        closed(node, {"node_id", "display_name", "source_kind", "canonical_ordinal", "z_index",
                      "base", "fill_hex", "keyframes"}, "motion_node_fields")
        identity = base.uuid_text(node["node_id"], "motion_node_uuid")
        require(identity not in identities, "motion_duplicate_node")
        identities.add(identity)
        require(type(node["display_name"]) is str and node["display_name"].strip()
                and len(node["display_name"].encode()) <= 160 and "\0" not in node["display_name"], "motion_name")
        require(node["source_kind"] in ("shape", "rectangle", "circle")
                and type(node["canonical_ordinal"]) is int and node["canonical_ordinal"] == ordinal
                and type(node["z_index"]) is int and -(2**31) <= node["z_index"] < 2**31, "motion_node_kind_order")
        pose(node["base"])
        color(node["fill_hex"])
        keys = node["keyframes"]
        require(type(keys) is list and len(keys) <= 128, "motion_key_budget")
        seen = set()
        for key in keys:
            closed(key, {"at", "property", "value", "interpolation"}, "motion_key_fields")
            at = rational(key["at"])
            require(0 <= at < duration and key["property"] in PROPERTIES and key["interpolation"] in EASINGS,
                    "motion_key_domain")
            identity = (key["property"], at)
            require(identity not in seen, "motion_key_duplicate")
            seen.add(identity)
            endpoint = dict(node["base"])
            endpoint[key["property"]] = key["value"]
            pose(endpoint)
        if node["source_kind"] == "circle":
            track = lambda prop: sorted((rational(k["at"]), k["value"], k["interpolation"])
                                        for k in keys if k["property"] == prop)
            require(node["base"]["width"] == node["base"]["height"]
                    and track("width") == track("height"), "motion_circle_tracks")
    return value


def sampled_pose(node, at):
    """Independent Fraction interval selection and canonical destination easing."""
    output = dict(node["base"])
    for prop in PROPERTIES:
        previous_time, previous_value = Fraction(0), output[prop]
        for key in sorted((k for k in node["keyframes"] if k["property"] == prop), key=lambda k: rational(k["at"])):
            next_time = rational(key["at"])
            if at < next_time:
                progress = float((at - previous_time) / (next_time - previous_time))
                require(0 <= progress < 1, "motion_fraction_not_representable")
                ease = key["interpolation"]
                amount = {"hold": lambda: 0.0, "linear": lambda: progress,
                          "ease_out_cubic": lambda: 1 - (1 - progress)**3,
                          "ease_in_out": lambda: progress * progress * (3 - 2 * progress)}[ease]()
                output[prop] = previous_value + (key["value"] - previous_value) * amount
                break
            previous_time, previous_value = next_time, key["value"]
            output[prop] = previous_value
    return pose(output)


def sampling(count, fps):
    seconds = (count - 0.5) / fps
    require((count - 1) / fps < seconds < count / fps, "motion_midpoint_not_representable")
    fraction = Fraction(2 * count - 1, 2 * fps)
    return {"sampler_id": SAMPLER,
            "internal_play_duration": {"num": str(fraction.numerator), "den": str(fraction.denominator)},
            "seconds_hex": seconds.hex(), "predicted_frame_count": count, "runtime_all_frames_verified": False}


def compare_pose(actual, expected):
    closed(actual, set(PROPERTIES), "motion_compiled_pose_fields")
    for key in PROPERTIES:
        near(actual[key], expected[key], f"motion_compiled_pose:{key}")


def source_closure(value, data):
    fields = {"plan_schema_version", "compiler_contract_id", "plan_sha256", "source_sha256",
              "config_sha256", "assets_lock_sha256", "manifest_sha256", "entries"}
    closed(value, fields, "motion_closure_fields")
    require(type(value["plan_schema_version"]) is int and value["plan_schema_version"] == 3
            and value["compiler_contract_id"] == COMPILER, "motion_closure_compiler")
    for field, name in {"plan_sha256": "plan.json", "source_sha256": "scene.py", "config_sha256": "render-config.json",
                        "assets_lock_sha256": "assets.lock.json", "manifest_sha256": "manifest.json"}.items():
        require(base.digest_text(value[field], "motion_closure_digest") == sha(data[name]), "motion_closure_bytes")
    expected = [{"path": name, "bytes": len(data[name]), "sha256": sha(data[name])} for name in sorted(MEMBERS)]
    require(value["entries"] == expected, "motion_closure_entry_set")
    return value


def inspect_source(bundle):
    # Reuse the memory-only bounded ZIP reader. Additional closure restrictions
    # below do not extract a file or add another renderer/runtime.
    data = base.source_entries(bundle)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        for entry in archive.infolist():
            require(not stat.S_ISLNK(entry.external_attr >> 16), "motion_source_symlink")
    plan = validate_plan(parse_json(data["plan.json"]))
    count, fps = plan["clock"]["frame_count"], plan["render_profile"]["frame_rate"]
    encoded = data["scene.py"].decode("utf-8")
    lines = encoded.split("\n", 3)
    require(len(lines) == 4, "motion_generated_program")
    head, tail = lines[:3], lines[3]
    require(head[:2] == [f"# Generated by {COMPILER}",
                        "# Imported source is data; only this compiler emits executable code."]
            and head[2].startswith("_DATA = ") and sha(tail.encode()) == PROGRAM_SHA256, "motion_generated_program")
    literal = parse_json(head[2][len("_DATA = "):])
    require(type(literal) is str, "motion_source_data_literal")
    compiled = parse_json(literal)
    closed(compiled, {"plan_sha256", "binding", "profile", "camera", "nodes", "samples", "end", "sampling"},
           "motion_compiled_fields")
    require(compiled["plan_sha256"] == sha(data["plan.json"]) and compiled["binding"] == plan["binding"]
            and compiled["profile"] == plan["render_profile"] and compiled["camera"] == plan["camera"]
            and same_json(compiled["nodes"], plan["nodes"])
            and same_json(compiled["sampling"], sampling(count, fps)), "motion_compiled_source_binding")
    require(type(compiled["samples"]) is list and len(compiled["samples"]) == count
            and type(compiled["end"]) is list and len(compiled["end"]) == len(plan["nodes"]), "motion_compiled_sample_count")
    for index, row in enumerate(compiled["samples"]):
        require(type(row) is list and len(row) == len(plan["nodes"]), "motion_compiled_node_count")
        for node, actual in zip(plan["nodes"], row):
            compare_pose(actual, sampled_pose(node, Fraction(index, fps)))
    for node, actual in zip(plan["nodes"], compiled["end"]):
        compare_pose(actual, sampled_pose(node, rational(plan["clock"]["duration"])))
    config = parse_json(data["render-config.json"])
    require(same_json(config, {"schema_version": 1, "profile_id": PROFILE, "compiler_id": COMPILER,
                       "manim_version_requirement": "0.21.0", "renderer": "cairo", "scene_class": "MotionwrightScene",
                       "render_profile": plan["render_profile"], "format": "mp4", "seed": 0,
                       "disable_caching": True, "background": plan["background"], "camera": plan["camera"],
                       "sampler": sampling(count, fps), "source_entry_byte_limit": MAX_ENTRY,
                       "readback_byte_limit_hypothesis": MAX_TRACE, "native_frame_readback_required": True,
                       "inline_readback_byte_limit": MAX_ENTRY,
                       "runtime_binary_attested": False}), "motion_config_contract")
    require(same_json(parse_json(data["assets.lock.json"]), {"schema_version": 1, "profile_id": PROFILE, "assets": [],
                "external_asset_usage": "none", "external_rights_certified": False}), "motion_assets_lock")
    manifest = parse_json(data["manifest.json"])
    require(same_json(manifest, {"schema_version": 1, "bundle_kind": "motionwright.manim.editable_planar_motion_source",
                "compiler_id": COMPILER, "encoding_id": "serde-json-pretty-rational-strings-zip9-stored-v1",
                "plan_sha256": sha(data["plan.json"]), "source_sha256": sha(data["scene.py"]),
                "config_sha256": sha(data["render-config.json"]),
                "entries": [{"path": p, "bytes": len(data[p]), "sha256": sha(data[p])} for p in sorted(MEMBERS - {"manifest.json"})],
                "losses": LOSSES, "native_render_verified": False, "native_reopen_verified": False,
                "canonical_import_applied": False}), "motion_manifest_contract")
    return plan, data


def verify_fixture(plan, phase):
    require(phase in PHASES, "motion_fixture_phase")
    count, fps = (29, 25) if phase == "edge25" else (24, 12)
    require(plan["render_profile"] == {"width": WIDTH, "height": HEIGHT, "frame_rate": fps}
            and plan["clock"]["frame_count"] == count and rational(plan["clock"]["timeline_start"]) == 0,
            "motion_fixture_clock")
    nodes = plan["nodes"]
    require(len(nodes) == 2 and [n["node_id"] for n in nodes] == [GREEN, RED], "motion_fixture_identity_order")
    for node, kind, z, fill, baseline in zip(nodes, ("circle", "rectangle"), (20, 10), ("#20CC40", "#E02040"),
          ((750, 390, 240, 240), (240, 180, 600, 360))):
        require(node["source_kind"] == kind and node["display_name"] == f"Original {kind}"
                and node["z_index"] == z and node["fill_hex"] == fill, "motion_fixture_style_identity")
        expected = dict(zip(PROPERTIES, (*baseline, 0, 1)))
        require(node["base"] == expected, "motion_fixture_base")
    require(nodes[0]["keyframes"] == [], "motion_fixture_circle_keys")
    target = 720 if phase in ("edited", "edited-reopened") else 600
    expected = {(prop, Fraction(1)): (value, ease) for prop, value, ease in [
        ("x", target, "linear"), ("y", 300, "hold"), ("width", 840, "ease_out_cubic"),
        ("height", 480, "ease_in_out"), ("rotation_deg", 90, "ease_in_out"), ("opacity", 0.25, "linear")]}
    observed = {(k["property"], rational(k["at"])): (k["value"], k["interpolation"]) for k in nodes[1]["keyframes"]}
    require(observed == expected, "motion_fixture_keys")
    camera = {"center_x": 1000, "center_y": 510, "zoom": 0.8, "rotation_deg": 0, "safe_margin": 0.1} if phase in (
        "camera", "camera-reopened") else {"center_x": 960, "center_y": 540, "zoom": 1, "rotation_deg": 0, "safe_margin": 0.05}
    require(plan["camera"] == camera, "motion_fixture_camera")
    return fps, count


def inspect_preparation(prepared, bundle, origin_bundle, phase):
    closed(prepared, {"schema_version", "kind", "phase", "project", "source", "native_execution_performed",
                      "native_readback_verified", "provider_job_custody_verified"}, "motion_preparation_fields")
    require(type(prepared["schema_version"]) is int and prepared["schema_version"] == 1
            and prepared["kind"] == "motionwright.manim.planar_motion.preparation"
            and prepared["phase"] == phase and prepared["native_execution_performed"] is False
            and prepared["native_readback_verified"] is False and prepared["provider_job_custody_verified"] is False,
            "motion_preparation_scope")
    plan, data = inspect_source(bundle)
    verify_fixture(plan, phase)
    source = prepared["source"]
    closed(source, {"carrier_asset_id", "carrier_sha256", "origin_binding", "current_binding", "prepared_source",
                   "prepared_bundle_sha256", "native_execution_performed", "imported_receipts_used_as_authority"},
           "motion_origin_fields")
    binding = base.source_binding(source["current_binding"], "motion_current_binding")
    origin_binding = base.source_binding(source["origin_binding"], "motion_origin_binding")
    project = prepared["project"]
    require(type(project) is dict and binding == plan["binding"]
            and all(project[k] == binding[k] for k in ("generation", "revision"))
            and project["id"] == binding["project_id"] and source["prepared_bundle_sha256"] == sha(bundle)
            and source["native_execution_performed"] is False and source["imported_receipts_used_as_authority"] is False,
            "motion_current_project_join")
    inspect_project_controls(project, plan)
    source_closure(source["prepared_source"], data)
    asset_id = base.uuid_text(source["carrier_asset_id"], "motion_carrier_id")
    digest = base.digest_text(source["carrier_sha256"], "motion_carrier_sha")
    assets = [a for a in project["assets"] if a.get("id") == asset_id]
    require(len(assets) == 1 and assets[0].get("content_sha256") == digest
            and assets[0].get("media_type") == "application/vnd.motionwright.manim-source+zip", "motion_carrier_asset_join")
    capsules = [c for c in project["production_design"]["capsules"] if c.get("source_asset_id") == asset_id]
    require(len(capsules) == 1 and capsules[0].get("source_sha256") == digest
            and capsules[0].get("scene_id") == binding["scene_id"], "motion_carrier_capsule_join")
    require(sha(origin_bundle) == digest, "motion_original_carrier_bytes")
    origin, _ = inspect_source(origin_bundle)
    require(origin["binding"] == origin_binding, "motion_original_carrier_binding")
    rebound = copy.deepcopy(origin)
    rebound["binding"]["generation"] = binding["generation"]
    rebound["binding"]["revision"] = binding["revision"]
    require(rebound == plan, "motion_semantic_rebind")
    # This is a read-only consistency check of a retained source preparation,
    # not an imported Project mutation or an authentication of arbitrary JSON.
    return plan, {"prepared_bundle_sha256": sha(bundle), "original_carrier_sha256": digest,
                  "plan_sha256": sha(data["plan.json"]), "program_tail_sha256": PROGRAM_SHA256,
                  "binding": binding, "origin_binding": origin_binding, "source_closure": source["prepared_source"],
                  "all_compiled_pose_samples_checked": True, "original_carrier_bytes_verified": True,
                  "canonical_import_applied_by_reader": False, "provider_job_custody_verified": False}


def inspect_project_controls(project, plan):
    """Check only projected controls, without replacing Domain validation."""
    binding = plan["binding"]
    scenes = [s for s in project["scenes"] if s.get("id") == binding["scene_id"]]
    profiles = [p for p in project["deliverables"] if p.get("id") == binding["deliverable_id"]]
    require(len(scenes) == 1 and len(profiles) == 1, "motion_project_selection")
    scene, delivery = scenes[0], profiles[0]
    require(scene["renderer"] == "manim-community" and scene["beats"] == []
            and scene["camera"] == plan["camera"] and scene["start"] == plan["clock"]["timeline_start"]
            and scene["duration"] == plan["clock"]["duration"], "motion_project_scene_controls")
    require(all(delivery[k] == plan["render_profile"][k] for k in ("width", "height"))
            and delivery["frame_rate"] == plan["clock"]["frame_rate"]
            and (not delivery["included_scene_ids"] or binding["scene_id"] in delivery["included_scene_ids"])
            and delivery["container"] == "mp4" and delivery["video_codec"] == "h264"
            and delivery["color_space"] == "rec709", "motion_project_delivery_controls")
    palette = project["visual_language"]["palette"]
    background = next((p for p in palette if p.get("name") == "background"), None)
    if background is None:
        background = next((p for p in palette if p.get("name") == "surface"), None)
    require(background is not None and background["value"] == "#000000", "motion_project_matte")
    require(project["audio"]["sfx_cues"] == [], "motion_project_sfx_obligation")
    start, end = rational(scene["start"]), rational(scene["start"]) + rational(scene["duration"])
    require(not any(start <= rational(c["at"]) < end for c in project["audio"]["cues"]), "motion_project_cue_obligation")
    for key in ("camera_reframes", "measured_bindings", "heroes", "stages", "procedural_fields"):
        require(not any(x.get("scene_id") == binding["scene_id"] for x in project["production_design"][key]),
                f"motion_project_unsupported:{key}")
    require(len(scene["nodes"]) == len(plan["nodes"]), "motion_project_node_count")
    for ordinal, (node, source) in enumerate(zip(scene["nodes"], plan["nodes"])):
        require(node["id"] == source["node_id"] and node["name"] == source["display_name"]
                and node["kind"] == source["source_kind"] and ordinal == source["canonical_ordinal"]
                and node["z_index"] == source["z_index"] and node["keyframes"] == source["keyframes"]
                and node["coordinate_space"] == "project_pixels" and node["parent_id"] is None
                and node["relations"] == [] and node["text"] is None, "motion_project_node_controls")
        compare_pose({k: node[k] for k in PROPERTIES}, source["base"])
        style = node["style"]
        require(style["fill"] == source["fill_hex"] and style["blend_mode"] == "normal"
                and style["stroke"] is None and style["stroke_width"] == 0
                and all(style[k] is None for k in ("font_family", "font_size", "font_weight", "line_height")),
                "motion_project_style_obligation")


def vertices(p):
    angle = -math.radians(p["rotation_deg"])
    sine, cosine = math.sin(angle), math.cos(angle)
    cx, cy = (p["x"] + p["width"] / 2 - 960) / 135, (540 - p["y"] - p["height"] / 2) / 135
    return [[cx + cosine * x * p["width"] / 270 - sine * y * p["height"] / 270,
             cy + sine * x * p["width"] / 270 + cosine * y * p["height"] / 270, 0]
            for x, y in ((1, 1), (-1, 1), (-1, -1), (1, -1))]


def point(actual, expected, reason):
    base.finite_vector3(actual, reason)
    for a, e in zip(actual, expected):
        near(a, e, reason)


def inspect_trace(plan, native, plan_sha256):
    require(type(native) is bytes and 0 < len(native) <= MAX_TRACE, "motion_trace_budget")
    trace = parse_json(native)
    closed(trace, {"schema_version", "profile_id", "scene_class", "plan_sha256", "binding", "sampler_id",
                   "sample_count", "callback_alphas", "samples", "decoded_video_verified", "canonical_import_applied"},
           "motion_trace_fields")
    count, fps = plan["clock"]["frame_count"], plan["render_profile"]["frame_rate"]
    require(type(trace["schema_version"]) is int and trace["schema_version"] == 1 and trace["profile_id"] == PROFILE
            and trace["scene_class"] == "MotionwrightScene" and trace["plan_sha256"] == plan_sha256
            and trace["binding"] == plan["binding"] and trace["sampler_id"] == SAMPLER
            and type(trace["sample_count"]) is int and trace["sample_count"] == count
            and trace["decoded_video_verified"] is False and trace["canonical_import_applied"] is False,
            "motion_trace_identity")
    require(type(trace["samples"]) is list and len(trace["samples"]) == count
            and type(trace["callback_alphas"]) is list and len(trace["callback_alphas"]) == count, "motion_trace_count")
    order = sorted(plan["nodes"], key=lambda n: (n["z_index"], n["canonical_ordinal"]))
    for index, (alpha, frame) in enumerate(zip(trace["callback_alphas"], trace["samples"])):
        require(0 <= number(alpha, "motion_alpha_finite") < 1, "motion_alpha_interval")
        near(alpha * (count - 0.5), index, "motion_alpha_grid", 1e-6)
        closed(frame, {"index", "camera", "objects"}, "motion_frame_fields")
        require(type(frame["index"]) is int and frame["index"] == index, "motion_frame_sequence")
        camera = frame["camera"]
        closed(camera, {"pixel_width", "pixel_height", "frame_rate", "frame_width", "frame_height",
                        "frame_center", "background_hex", "background_opacity"}, "motion_native_camera_fields")
        require(type(camera["pixel_width"]) is int and type(camera["pixel_height"]) is int
                and camera["pixel_width"] == plan["render_profile"]["width"]
                and camera["pixel_height"] == plan["render_profile"]["height"], "motion_camera_pixels")
        cam = plan["camera"]
        for key, expected in {"frame_rate": fps, "frame_width": 128 / (9 * cam["zoom"]),
                              "frame_height": 8 / cam["zoom"], "background_opacity": 1}.items():
            near(camera[key], expected, f"motion_camera:{key}")
        point(camera["frame_center"], [(cam["center_x"] - 960) / 135, (540 - cam["center_y"]) / 135, 0], "motion_camera_center")
        require(camera["background_hex"].upper() == "#000000", "motion_camera_matte")
        require(type(frame["objects"]) is list and len(frame["objects"]) == len(order), "motion_frame_objects")
        for node, obj in zip(order, frame["objects"]):
            closed(obj, {"name", "primitive", "center", "width", "height", "z_index", "fill_hex", "fill_opacity",
                         "stroke_width", "stroke_opacity", "vertices", "curve_start", "curve_mid"}, "motion_object_fields")
            p = sampled_pose(node, Fraction(index, fps))
            center = [(p["x"] + p["width"] / 2 - 960) / 135, (540 - p["y"] - p["height"] / 2) / 135, 0]
            circle = node["source_kind"] == "circle"
            require(obj["name"] == "mw_" + node["node_id"].replace("-", "")
                    and obj["primitive"] == ("Circle" if circle else "Rectangle")
                    and obj["fill_hex"].upper() == node["fill_hex"].upper(), "motion_object_identity")
            point(obj["center"], center, "motion_object_center")
            for key, expected in {"z_index": node["z_index"], "fill_opacity": p["opacity"],
                                  "stroke_width": 0, "stroke_opacity": 0}.items():
                near(obj[key], expected, f"motion_object:{key}")
            if circle:
                radius = p["width"] / 270
                angle = -math.radians(p["rotation_deg"])
                delta = [radius * math.cos(angle), radius * math.sin(angle), 0]
                require(obj["vertices"] == [], "motion_circle_vertices")
                point(obj["curve_start"], [a+b for a, b in zip(center, delta)], "motion_circle_start")
                point(obj["curve_mid"], [a-b for a, b in zip(center, delta)], "motion_circle_mid")
                require(all(1.99 * radius <= number(obj[k], "motion_circle_extent") <= 2.1 * radius
                            for k in ("width", "height")), "motion_circle_extent")
            else:
                expected_vertices = vertices(p)
                require(type(obj["vertices"]) is list and len(obj["vertices"]) == 4, "motion_rectangle_vertices")
                for actual, expected in zip(obj["vertices"], expected_vertices):
                    point(actual, expected, "motion_rectangle_vertices")
                point(obj["curve_start"], expected_vertices[0], "motion_rectangle_start")
                point(obj["curve_mid"], expected_vertices[2], "motion_rectangle_mid")
                s, c = abs(math.sin(math.radians(p["rotation_deg"]))), abs(math.cos(math.radians(p["rotation_deg"])))
                near(obj["width"], (p["width"] * c + p["height"] * s) / 135, "motion_rectangle_width")
                near(obj["height"], (p["width"] * s + p["height"] * c) / 135, "motion_rectangle_height")
    return {"sha256": sha(native), "bytes": len(native), "getter_sample_count": count,
            "all_getter_samples_verified": True, "encoded_frames_inferred_from_callbacks": False}


def projected_shapes(plan, index, width=WIDTH):
    factor = width / 1920 * plan["camera"]["zoom"]
    result = []
    for node in sorted(plan["nodes"], key=lambda n: (n["z_index"], n["canonical_ordinal"])):
        p = sampled_pose(node, Fraction(index, plan["render_profile"]["frame_rate"]))
        cx = width / 2 + factor * (p["x"] + p["width"] / 2 - plan["camera"]["center_x"])
        cy = width * 9 / 32 + factor * (p["y"] + p["height"] / 2 - plan["camera"]["center_y"])
        angle = math.radians(p["rotation_deg"])
        result.append((node["node_id"], node["source_kind"], cx, cy, p["width"] * factor / 2,
                       p["height"] * factor / 2, math.cos(angle), math.sin(angle), p["opacity"], color(node["fill_hex"])))
    return result


def expected_pixel(shapes, x, y, boundary=BOUNDARY_MARGIN):
    rgb, region = [0.0, 0.0, 0.0], []
    for identity, kind, cx, cy, half_w, half_h, cosine, sine, alpha, fill in shapes:
        dx, dy = x + 0.5 - cx, y + 0.5 - cy
        if kind == "circle":
            distance = half_w - math.hypot(dx, dy)
        else:
            local_x, local_y = cosine * dx + sine * dy, -sine * dx + cosine * dy
            distance = min(half_w - abs(local_x), half_h - abs(local_y))
        if abs(distance) <= boundary:
            return None
        if distance > 0 and alpha > 0:
            rgb = [c * alpha + previous * (1 - alpha) for c, previous in zip(fill, rgb)]
            region.append(identity)
    return tuple(rgb), "+".join(region) or "background"


def check_pixels(frame, plan, index, *, width=WIDTH, height=HEIGHT):
    require(type(frame) is bytes and len(frame) == width * height * 3, "motion_decoded_frame_size")
    shapes = projected_shapes(plan, index, width)
    groups, ignored = {}, 0
    for y in range(height):
        for x in range(width):
            expected = expected_pixel(shapes, x, y)
            if expected is None:
                ignored += 1
                continue
            rgb, region = expected
            offset = (y * width + x) * 3
            errors = [abs(frame[offset+c] - rgb[c]) for c in range(3)]
            values = groups.setdefault(region, [0, 0, 0.0, 0.0])
            values[0] += 1
            values[1] += int(max(errors) > CHANNEL_ERROR)
            values[2] += sum(errors)
            values[3] = max(values[3], max(errors))
    require(groups.get("background", [0])[0] >= width * height / 10, "motion_background_core_missing")
    for identity in (RED, GREEN):
        require(sum(v[0] for key, v in groups.items() if identity in key) >= width * height / 400,
                f"motion_shape_core_missing:{identity}")
    for region, (pixels, bad, total, _) in groups.items():
        require(bad / pixels <= BAD_PIXEL_FRACTION and total / (pixels * 3) <= MEAN_CHANNEL_ERROR,
                f"motion_pixel_content:{index}:{region}")
    return {"index": index, "rgb24_sha256": sha(frame), "boundary_pixels_excluded": ignored,
            "regions": {key: {"core_pixels": n, "bad_pixels": b, "mean_channel_error": error / (n * 3),
                              "max_channel_error": maximum} for key, (n, b, error, maximum) in groups.items()}}


def inspect(args):
    prepared_bytes = base.read_regular(args.prepared, 4 * 1024 * 1024)
    bundle = base.read_regular(args.source, MAX_SOURCE)
    origin = base.read_regular(args.origin_source, MAX_SOURCE)
    plan, source = inspect_preparation(parse_json(prepared_bytes), bundle, origin, args.phase)
    native = base.read_regular(args.native_readback, MAX_TRACE)
    trace = inspect_trace(plan, native, source["plan_sha256"])
    fps, count = verify_fixture(plan, args.phase)
    # An original result can be joined by the existing coordinator. This command
    # observes only the explicitly supplied media and never authenticates a job.
    video = Path(args.video)
    video_bytes = base.read_regular(video, MAX_VIDEO)
    digest, size = sha(video_bytes), len(video_bytes)
    del video_bytes
    tools = [(name, Path(shutil.which(value) or value).resolve(strict=True))
             for name, value in (("ffprobe", args.ffprobe), ("ffmpeg", args.ffmpeg))]
    versions = {}
    for name, tool in tools:
        output, process = base.run_bounded([tool, "-version"], 65536, 15)
        versions[name] = {"sha256": sha(base.read_regular(tool, MAX_VIDEO)),
                          "version": output.decode("utf-8", "replace").splitlines()[0], "process": process}
    ffprobe, ffmpeg = (t for _, t in tools)
    probe, probe_process = base.run_bounded([ffprobe, "-v", "error", "-show_streams", "-show_format", "-show_frames", "-of", "json", video], MAX_TRACE)
    probe_document = parse_json(probe)
    media = base.inspect_probe(probe_document, fps, count)
    color_tags = {key: probe_document["streams"][0].get(key) for key in ("color_space", "color_transfer", "color_primaries")}
    media["colorimetry_tags"] = color_tags
    media["requested_rec709_tags_observed"] = all(value == "bt709" for value in color_tags.values())
    raw, decode_process = base.run_bounded([ffmpeg, "-v", "error", "-xerror", "-nostdin", "-i", video,
        "-map", "0:v:0", "-vsync", "0", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], FRAME_BYTES * (count + 1))
    require(len(raw) == FRAME_BYTES * count, "motion_decoded_count")
    frames = [check_pixels(raw[i*FRAME_BYTES:(i+1)*FRAME_BYTES], plan, i) for i in range(count)]
    require(frames[0]["rgb24_sha256"] != frames[fps//2]["rgb24_sha256"] != frames[fps]["rgb24_sha256"],
            "motion_distinct_moving_frames_missing")
    require(sha(base.read_regular(video, MAX_VIDEO)) == digest, "motion_video_changed_during_readback")
    images = []
    if args.frames:
        folder = Path(args.frames)
        require(folder.is_dir() and not folder.is_symlink(), "motion_frame_output_directory")
        for i in sorted({0, fps//2, fps, count-1}):
            content = base.png(raw[i*FRAME_BYTES:(i+1)*FRAME_BYTES])
            name = f"manim-motion-{args.phase}-{i:04d}.png"
            with (folder/name).open("xb") as stream:
                stream.write(content)
            images.append({"name": name, "bytes": len(content), "sha256": sha(content)})
    return {"schema": "motionwright.manim.planar-motion.readback/1", "status": "PASS", "phase": args.phase,
            "classification": "DECODED_FIXTURE_CONTENT_AND_SOURCE_READBACK_NOT_RELEASE_ACCEPTANCE",
            "prepared_record_sha256": sha(prepared_bytes), "video_sha256": digest, "video_bytes": size,
            "source": source, "native_getters": trace, "media": media, "frames": frames, "images": images,
            "decoder_identity": versions, "probe_process": probe_process, "decode_process": decode_process,
            "decoded_rgb24_sha256": sha(raw), "decoded_rgb24_bytes": len(raw),
            "thresholds": {"rgb_channel_error": CHANNEL_ERROR, "mean_channel_error": MEAN_CHANNEL_ERROR,
                           "max_bad_core_fraction": BAD_PIXEL_FRACTION, "boundary_margin_pixels": BOUNDARY_MARGIN,
                           "calibration": "proposed_unmeasured_until_original_native_lane_review"},
            "provider_job_custody_verified": False, "permissions_verified_by_reader": False,
            "canonical_import_applied_by_reader": False, "runtime_execution_performed_by_reader": False,
            "human_acceptance": False, "external_rights_certified": False, "native_job_retried": False,
            "losses": LOSSES + ["640x360_closed_fixture_oracle_only", "AA_and_codec_boundary_band_excluded",
                "identical_hold_tail_frame_permutations_are_unobservable", "opaque_H264_contribution_is_not_an_AV_master"]}


class ReaderTests(unittest.TestCase):
    """Synthetic protocol/mathematics regressions; never native evidence."""

    def node(self):
        return {"base": {"x": 240.0, "y": 180.0, "width": 600.0, "height": 360.0,
                         "rotation_deg": 0.0, "opacity": 1.0}, "keyframes": [
                {"at": {"num": "1", "den": "1"}, "property": p, "value": v, "interpolation": e}
                for p, v, e in (("x", 600, "linear"), ("y", 300, "hold"), ("width", 840, "ease_out_cubic"),
                                ("height", 480, "ease_in_out"), ("rotation_deg", 90, "ease_in_out"), ("opacity", .25, "linear"))]}

    def plan(self):
        red = {"node_id": RED, "display_name": "Original rectangle", "source_kind": "rectangle",
               "canonical_ordinal": 1, "z_index": 10, "fill_hex": "#E02040", **self.node()}
        green = {"node_id": GREEN, "display_name": "Original circle", "source_kind": "circle",
                 "canonical_ordinal": 0, "z_index": 20, "fill_hex": "#20CC40", "keyframes": [],
                 "base": dict(zip(PROPERTIES, (750, 390, 240, 240, 0, 1)))}
        return {"schema_version": 3, "profile_id": PROFILE,
                "output_role": "scene_video_contribution_without_audio_or_captions",
                "binding": {"project_id": "30000000-0000-4000-8000-000000000001",
                    "generation": "30000000-0000-4000-8000-000000000002", "revision": 17,
                    "scene_id": "30000000-0000-4000-8000-000000000003",
                    "deliverable_id": "30000000-0000-4000-8000-000000000004"},
                "clock": {"timeline_start": {"num": "0", "den": "1"}, "duration": {"num": "2", "den": "1"},
                    "frame_rate": {"num": "12", "den": "1"}, "project_start_frame": 0,
                    "project_end_frame_exclusive": 24, "local_start_frame": 0, "frame_count": 24},
                "render_profile": {"width": WIDTH, "height": HEIGHT, "frame_rate": 12},
                "canvas": {"width": 1920, "height": 1080, "coordinate_space": "project_pixels"},
                "background": {"kind": "solid", "color": "#000000", "opacity": 1},
                "camera": {"center_x": 960, "center_y": 540, "zoom": 1, "rotation_deg": 0, "safe_margin": 0.05},
                "nodes": [green, red]}

    def bundle_fixture(self, plan, mutate=None):
        # This constructs adversarial protocol fixtures, not native output or a
        # substitute for Rust's exact canonical ZIP/recompilation test.
        dump = lambda value: (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
        path = Path(__file__).resolve().parents[1] / "crates/manim-profile/src/planar_motion.rs"
        program = path.read_text().split('const MOTION_PROGRAM_V3: &str = r##"', 1)[1].split('"##;', 1)[0]
        self.assertEqual(sha(program.encode()), PROGRAM_SHA256)
        data = {"plan.json": dump(plan)}
        payload = {"plan_sha256": sha(data["plan.json"]), "binding": plan["binding"],
                   "profile": plan["render_profile"], "camera": plan["camera"], "nodes": plan["nodes"],
                   "samples": [[sampled_pose(n, Fraction(i, 12)) for n in plan["nodes"]] for i in range(24)],
                   "end": [sampled_pose(n, Fraction(2)) for n in plan["nodes"]], "sampling": sampling(24, 12)}
        if mutate is not None:
            program, payload = mutate(program, payload)
        source = (f"# Generated by {COMPILER}\n# Imported source is data; only this compiler emits executable code.\n_DATA = "
                  + json.dumps(json.dumps(payload, separators=(",", ":"))) + "\n" + program)
        data["scene.py"] = source.encode()
        data["render-config.json"] = dump({"schema_version": 1, "profile_id": PROFILE, "compiler_id": COMPILER,
            "manim_version_requirement": "0.21.0", "renderer": "cairo", "scene_class": "MotionwrightScene",
            "render_profile": plan["render_profile"], "format": "mp4", "seed": 0, "disable_caching": True,
            "background": plan["background"], "camera": plan["camera"], "sampler": sampling(24, 12),
            "source_entry_byte_limit": MAX_ENTRY, "readback_byte_limit_hypothesis": MAX_TRACE,
            "inline_readback_byte_limit": MAX_ENTRY,
            "native_frame_readback_required": True, "runtime_binary_attested": False})
        data["assets.lock.json"] = dump({"schema_version": 1, "profile_id": PROFILE, "assets": [],
            "external_asset_usage": "none", "external_rights_certified": False})
        data["manifest.json"] = dump({"schema_version": 1, "bundle_kind": "motionwright.manim.editable_planar_motion_source",
            "compiler_id": COMPILER, "encoding_id": "serde-json-pretty-rational-strings-zip9-stored-v1",
            "plan_sha256": sha(data["plan.json"]), "source_sha256": sha(data["scene.py"]),
            "config_sha256": sha(data["render-config.json"]),
            "entries": [{"path": name, "bytes": len(data[name]), "sha256": sha(data[name])} for name in sorted(data)],
            "losses": LOSSES, "native_render_verified": False, "native_reopen_verified": False, "canonical_import_applied": False})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for name in sorted(data):
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(info, data[name])
        return buffer.getvalue(), data

    def trace(self, plan):
        samples = []
        for index in range(24):
            objects = []
            for n in sorted(plan["nodes"], key=lambda n: n["z_index"]):
                p = sampled_pose(n, Fraction(index, 12))
                center = [(p["x"] + p["width"] / 2 - 960) / 135, (540 - p["y"] - p["height"] / 2) / 135, 0]
                circle = n["source_kind"] == "circle"
                angle = math.radians(p["rotation_deg"])
                v = [] if circle else vertices(p)
                obj = {"name": "mw_" + n["node_id"].replace("-", ""), "primitive": "Circle" if circle else "Rectangle",
                       "center": center, "z_index": n["z_index"], "fill_hex": n["fill_hex"], "fill_opacity": p["opacity"],
                       "stroke_width": 0, "stroke_opacity": 0, "vertices": v}
                if circle:
                    delta = [p["width"] / 270 * math.cos(angle), -p["width"] / 270 * math.sin(angle), 0]
                    obj.update(width=p["width"] / 135, height=p["height"] / 135,
                               curve_start=[a+b for a, b in zip(center, delta)], curve_mid=[a-b for a, b in zip(center, delta)])
                else:
                    obj.update(width=(p["width"] * abs(math.cos(angle)) + p["height"] * abs(math.sin(angle))) / 135,
                               height=(p["width"] * abs(math.sin(angle)) + p["height"] * abs(math.cos(angle))) / 135,
                               curve_start=v[0], curve_mid=v[2])
                objects.append(obj)
            samples.append({"index": index, "objects": objects, "camera": {"pixel_width": WIDTH, "pixel_height": HEIGHT,
                "frame_rate": 12.0, "frame_width": 128/9, "frame_height": 8.0, "frame_center": [0, 0, 0],
                "background_hex": "#000000", "background_opacity": 1.0}})
        return {"schema_version": 1, "profile_id": PROFILE, "scene_class": "MotionwrightScene", "plan_sha256": "0"*64,
                "binding": plan["binding"], "sampler_id": SAMPLER, "sample_count": 24,
                "callback_alphas": [i/23.5 for i in range(24)], "samples": samples,
                "decoded_video_verified": False, "canonical_import_applied": False}

    def preparation_fixture(self):
        origin_plan = self.plan()
        origin, _ = self.bundle_fixture(origin_plan)
        current = copy.deepcopy(origin_plan)
        current["binding"]["generation"] = "30000000-0000-4000-8000-000000000005"
        current["binding"]["revision"] += 2
        bundle, entries = self.bundle_fixture(current)
        binding = current["binding"]
        asset_id = "30000000-0000-4000-8000-000000000006"
        capsule = {"id": "30000000-0000-4000-8000-000000000007", "scene_id": binding["scene_id"],
                   "source_asset_id": asset_id, "source_sha256": sha(origin)}
        nodes = []
        for n in current["nodes"]:
            nodes.append({"id": n["node_id"], "name": n["display_name"], "kind": n["source_kind"],
                "parent_id": None, "text": None, "coordinate_space": "project_pixels", "z_index": n["z_index"],
                "relations": [], "property_locks": [], "keyframes": n["keyframes"], **n["base"],
                "style": {"fill": n["fill_hex"], "stroke": None, "stroke_width": 0, "font_family": None,
                          "font_size": None, "font_weight": None, "line_height": None, "blend_mode": "normal"}})
        project = {"id": binding["project_id"], "generation": binding["generation"], "revision": binding["revision"],
            "assets": [{"id": asset_id, "content_sha256": sha(origin), "media_type": "application/vnd.motionwright.manim-source+zip"}],
            "scenes": [{"id": binding["scene_id"], "renderer": "manim-community", "start": current["clock"]["timeline_start"],
                "duration": current["clock"]["duration"], "camera": current["camera"], "beats": [], "nodes": nodes}],
            "deliverables": [{"id": binding["deliverable_id"], "width": WIDTH, "height": HEIGHT,
                "frame_rate": current["clock"]["frame_rate"], "included_scene_ids": [], "container": "mp4",
                "video_codec": "h264", "color_space": "rec709"}],
            "visual_language": {"palette": [{"name": "surface", "value": "#000000"}]},
            "audio": {"cues": [], "sfx_cues": []},
            "production_design": {"capsules": [capsule], "heroes": [], "stages": [], "procedural_fields": [],
                                  "camera_reframes": [], "measured_bindings": []}}
        closure = {"plan_schema_version": 3, "compiler_contract_id": COMPILER,
                   "entries": [{"path": name, "bytes": len(entries[name]), "sha256": sha(entries[name])} for name in sorted(entries)]}
        for key, name in (("plan", "plan.json"), ("source", "scene.py"), ("config", "render-config.json"),
                          ("assets_lock", "assets.lock.json"), ("manifest", "manifest.json")):
            closure[key + "_sha256"] = sha(entries[name])
        return {"schema_version": 1, "kind": "motionwright.manim.planar_motion.preparation", "phase": "reopened",
                "project": project, "source": {"carrier_asset_id": asset_id, "carrier_sha256": sha(origin),
                    "origin_binding": origin_plan["binding"], "current_binding": binding, "prepared_source": closure,
                    "prepared_bundle_sha256": sha(bundle), "native_execution_performed": False,
                    "imported_receipts_used_as_authority": False}, "native_execution_performed": False,
                "native_readback_verified": False, "provider_job_custody_verified": False}, bundle, origin

    def test_source_rebind_requires_original_carrier_and_every_project_join(self):
        prepared, bundle, origin = self.preparation_fixture()
        plan, result = inspect_preparation(prepared, bundle, origin, "reopened")
        self.assertNotEqual(plan["binding"]["generation"], prepared["source"]["origin_binding"]["generation"])
        self.assertTrue(result["original_carrier_bytes_verified"])
        self.assertFalse(result["provider_job_custody_verified"])
        for case in range(9):
            with self.subTest(case=case):
                value = copy.deepcopy(prepared)
                if case == 0:
                    value["source"]["prepared_bundle_sha256"] = "0"*64
                elif case == 1:
                    value["source"]["origin_binding"]["revision"] += 1
                elif case == 2:
                    value["project"]["generation"] = value["source"]["origin_binding"]["generation"]
                elif case == 3:
                    value["project"]["assets"][0]["content_sha256"] = "0"*64
                elif case == 4:
                    value["project"]["production_design"]["capsules"][0]["source_sha256"] = "0"*64
                elif case == 5:
                    value["project"]["scenes"][0]["nodes"][1]["style"]["font_family"] = "Unresolved"
                elif case == 6:
                    value["project"]["scenes"][0]["camera"]["zoom"] = 0.5
                elif case == 7:
                    value["project"]["scenes"][0]["nodes"][1]["keyframes"][0]["value"] += 1
                else:
                    value["source"]["imported_receipts_used_as_authority"] = True
                with self.assertRaises(ReadbackError):
                    inspect_preparation(value, bundle, origin, "reopened")
        with self.assertRaises(ReadbackError):
            inspect_preparation(prepared, bundle, bundle, "reopened")

    def test_typed_false_flags_do_not_accept_json_zero(self):
        self.assertFalse(same_json({"verified": 0}, {"verified": False}))
        source, _ = self.bundle_fixture(self.plan(), lambda program, data: (
            program, {**data, "sampling": {**data["sampling"], "runtime_all_frames_verified": 0}}))
        with self.assertRaisesRegex(ReadbackError, "motion_compiled_source_binding"):
            inspect_source(source)

    def test_plan_rejects_silent_controls_identity_and_clock_losses(self):
        validate_plan(self.plan())
        for case in range(8):
            with self.subTest(case=case):
                p = self.plan()
                if case == 0:
                    p["nodes"][0]["node_id"] = RED
                elif case == 1:
                    p["clock"]["frame_count"] = 23
                elif case == 2:
                    p["camera"]["rotation_deg"] = 1
                elif case == 3:
                    p["nodes"][1]["keyframes"].append(copy.deepcopy(p["nodes"][1]["keyframes"][0]))
                elif case == 4:
                    p["nodes"][1]["keyframes"][0]["at"] = {"num": "2", "den": "1"}
                elif case == 5:
                    p["nodes"][0]["base"]["width"] = 241
                elif case == 6:
                    p["nodes"][1]["rna"] = "arbitrary"
                else:
                    p["background"]["opacity"] = True
                with self.assertRaises(ReadbackError):
                    validate_plan(p)

    def test_inline_transport_budget_is_checked_before_large_native_output(self):
        p = self.plan()
        p["nodes"] = [p["nodes"][1]]
        p["nodes"][0]["canonical_ordinal"] = 0
        for count in (127, 128):
            duration = Fraction(count, 12)
            p["clock"].update(frame_count=count, project_end_frame_exclusive=count,
                              duration={"num": str(duration.numerator), "den": str(duration.denominator)})
            if count == 127:
                validate_plan(p)
            else:
                with self.assertRaisesRegex(ReadbackError, "motion_observation_budget"):
                    validate_plan(p)

    def test_rehashed_source_cannot_substitute_code_or_pose_samples(self):
        source, _ = self.bundle_fixture(self.plan())
        observed, _ = inspect_source(source)
        self.assertEqual(observed, self.plan())
        def alter_pose(program, data):
            data["samples"][6][1]["x"] = 421
            return program, data
        for edit, reason in ((alter_pose, "motion_compiled_pose:x"),
                             (lambda p, d: (p + "\nraise RuntimeError('foreign program')\n", d), "motion_generated_program")):
            source, _ = self.bundle_fixture(self.plan(), edit)
            with self.assertRaisesRegex(ReadbackError, reason):
                inspect_source(source)

    def test_all_native_getter_rows_binding_sequence_and_controls_are_required(self):
        p = self.plan()
        trace = self.trace(p)
        inspect_trace(p, json.dumps(trace).encode(), "0"*64)
        for case in range(9):
            with self.subTest(case=case):
                t = copy.deepcopy(trace)
                if case == 0:
                    t["binding"]["generation"] = "40000000-0000-4000-8000-000000000001"
                elif case == 1:
                    t["samples"].pop()
                elif case == 2:
                    t["callback_alphas"][6] = 1
                elif case == 3:
                    t["samples"][6] = copy.deepcopy(t["samples"][5])
                elif case == 4:
                    t["samples"][6]["objects"][0]["fill_opacity"] = 1
                elif case == 5:
                    t["samples"][6]["objects"].reverse()
                elif case == 6:
                    t["samples"][6]["camera"]["frame_center"] = [1, 0, 0]
                elif case == 7:
                    t["samples"][6]["objects"][0]["vertices"][0][0] += 0.1
                else:
                    del t["samples"][6]["objects"][0]["curve_start"]
                with self.assertRaises(ReadbackError):
                    inspect_trace(p, json.dumps(t).encode(), "0"*64)

    def test_pixel_oracle_rejects_blank_and_previous_moving_frame(self):
        p, width, height = self.plan(), 160, 90
        shapes = projected_shapes(p, 6, width)
        frame = bytearray(width * height * 3)
        for y in range(height):
            for x in range(width):
                expected = expected_pixel(shapes, x, y, boundary=-1)
                rgb = expected[0]
                offset = (y * width + x) * 3
                frame[offset:offset+3] = bytes(round(c) for c in rgb)
        check_pixels(bytes(frame), p, 6, width=width, height=height)
        with self.assertRaisesRegex(ReadbackError, "motion_pixel_content"):
            check_pixels(bytes(frame), p, 0, width=width, height=height)
        with self.assertRaisesRegex(ReadbackError, "motion_pixel_content"):
            check_pixels(bytes(width * height * 3), p, 6, width=width, height=height)

    def test_exact_half_frame_values_and_arrival(self):
        n = self.node()
        self.assertEqual(sampled_pose(n, Fraction(1, 2)), {"x": 420, "y": 180, "width": 810,
                         "height": 420, "rotation_deg": 45, "opacity": .625})
        self.assertEqual(sampled_pose(n, Fraction(1))["y"], 300)
        self.assertEqual(sampled_pose(n, Fraction(23, 12)), sampled_pose(n, Fraction(1)))

    def test_destination_easing_is_not_cosine(self):
        self.assertEqual(sampled_pose(self.node(), Fraction(1, 4))["height"], 378.75)
        self.assertEqual(sampled_pose(self.node(), Fraction(1, 4))["rotation_deg"], 14.0625)

    def test_zero_key_and_fractional_key(self):
        n = self.node()
        n["keyframes"].append({"at": {"num": "0", "den": "1"}, "property": "x", "value": 400, "interpolation": "hold"})
        self.assertEqual(sampled_pose(n, Fraction(0))["x"], 400)
        self.assertEqual(sampled_pose(n, Fraction(1, 2))["x"], 500)
        n["keyframes"][0]["at"] = {"num": "1", "den": "7"}
        self.assertEqual(sampled_pose(n, Fraction(1, 7))["x"], 600)

    def test_midpoint_interval_does_not_change_canonical_29_over_25(self):
        s = sampling(29, 25)
        self.assertEqual(rational(s["internal_play_duration"]), Fraction(57, 50))
        self.assertFalse(s["runtime_all_frames_verified"])
        self.assertLess(28/25, float.fromhex(s["seconds_hex"]))
        self.assertLess(float.fromhex(s["seconds_hex"]), 29/25)

    def test_nonfinite_and_bool_coordinates_rejected(self):
        for value in (True, float("inf"), float("nan"), "42"):
            with self.assertRaises(ReadbackError):
                number(value, "test")

    def test_centered_clockwise_rotation_has_expected_vertex(self):
        p = self.node()["base"]
        p["rotation_deg"] = 90
        expected = vertices(p)[0]
        self.assertAlmostEqual(expected[0], -240/135)
        self.assertAlmostEqual(expected[1], -120/135)

    def test_opacity_and_z_composition_at_known_interior(self):
        shapes = [(RED, "rectangle", 10, 10, 8, 8, 1, 0, .5, (224, 32, 64)),
                  (GREEN, "circle", 10, 10, 5, 5, 1, 0, .5, (32, 204, 64))]
        rgb, region = expected_pixel(shapes, 9, 9)
        self.assertEqual(rgb, (72, 110, 48))
        self.assertEqual(region, RED + "+" + GREEN)
        self.assertNotEqual(expected_pixel(list(reversed(shapes)), 9, 9)[0], rgb)

    def test_boundary_is_explicitly_excluded_not_scored_as_success(self):
        shape = [(RED, "rectangle", 10, 10, 5, 5, 1, 0, 1, (224, 32, 64))]
        self.assertIsNone(expected_pixel(shape, 14, 9))
        self.assertEqual(expected_pixel(shape, 18, 9), ((0.0, 0.0, 0.0), "background"))

    def test_duplicate_and_nonfinite_json_rejected(self):
        for source in ('{"x":1,"x":2}', '{"x":NaN}'):
            with self.assertRaises(ReadbackError):
                parse_json(source)

    def test_false_success_trace_rejected_before_frames(self):
        p = {"clock": {"frame_count": 24}, "render_profile": {"frame_rate": 12}, "binding": {}}
        for field in ("decoded_video_verified", "canonical_import_applied"):
            trace = {"schema_version": 1, "profile_id": PROFILE, "scene_class": "MotionwrightScene",
                     "plan_sha256": "0"*64, "binding": {}, "sampler_id": SAMPLER, "sample_count": 24,
                     "callback_alphas": [], "samples": [], "decoded_video_verified": False, "canonical_import_applied": False}
            trace[field] = True
            with self.assertRaisesRegex(ReadbackError, "motion_trace_identity"):
                inspect_trace(p, json.dumps(trace).encode(), "0"*64)

    def test_source_program_text_is_never_executed(self):
        tree = ast.parse(Path(__file__).read_text())
        forbidden = {"eval", "exec", "compile", "__import__"}
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                             and n.func.id in forbidden for n in ast.walk(tree)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--origin-source", type=Path)
    parser.add_argument("--native-readback", type=Path)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--frames", type=Path)
    args = parser.parse_args(argv)
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReaderTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if any(getattr(args, name) is None for name in ("prepared", "source", "origin_source", "native_readback", "video", "phase")):
        parser.error("required: --prepared --source --origin-source --native-readback --video --phase")
    try:
        report = inspect(args)
    except (ReadbackError, OSError, ValueError, KeyError, TypeError, IndexError, zipfile.BadZipFile) as error:
        print(json.dumps({"schema": "motionwright.manim.planar-motion.readback/1", "status": "FAIL",
                          "error": str(error), "native_job_retried": False}, separators=(",", ":")))
        return 1
    print(json.dumps(report, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
