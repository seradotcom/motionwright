#!/usr/bin/env python3
"""Independent bounded reader for the fixed Manim static-source native fixtures.

Reads the entire real media file with FFprobe and a separate FFmpeg RGB decode.
Does not import/execute Manim or scene.py, compile a scene, mix audio, or launch a
replacement render. Thresholds come from the pre-render fixture specification.
A passing report is technical fixture evidence; it is not execution authority,
a full Manim capability claim, an audio master, or human aesthetic acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import shutil
import struct
import subprocess
import threading
import time
import unittest
import zipfile
import zlib
from fractions import Fraction
from pathlib import Path
from uuid import UUID

PROFILE = "mw-manim-static-shapes-v2"
COMPILER = "motionwright.manim.rust.static_shapes.v2.1"
MIME = "application/vnd.motionwright.manim-source+zip"
WIDTH, HEIGHT = 640, 360
FRAME_BYTES = WIDTH * HEIGHT * 3
COLORS = ((0, 0, 0), (32, 64, 224), (224, 32, 64), (32, 204, 64))
IDS = [f"10000000-0000-4000-8000-{n:012d}" for n in (1, 2, 3)]
MAX_VIDEO_BYTES = 512 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_ENTRY_BYTES = 256 * 1024
MAX_REPORT_INPUT = 4 * 1024 * 1024
MAX_CHANNEL_ERROR = 12
BOUNDARY_MARGIN = 2
MIN_CORE_FRACTION = 0.995
MIN_BACKGROUND_FRACTION = 0.999
BBOX_EDGE_ERROR = 2
MEMBERS = {"plan.json", "scene.py", "render-config.json", "assets.lock.json", "manifest.json"}
BINDING_FIELDS = {"project_id", "generation", "revision", "scene_id", "deliverable_id"}
CLOSURE_FIELDS = {"plan_schema_version", "compiler_contract_id", "plan_sha256", "source_sha256",
                  "config_sha256", "assets_lock_sha256", "manifest_sha256", "entries"}
ORIGIN_FIELDS = {"carrier_asset_id", "carrier_sha256", "origin_binding", "current_binding",
                 "prepared_source", "prepared_bundle_sha256", "native_execution_performed",
                 "imported_receipts_used_as_authority"}


class ReadbackError(ValueError):
    pass


class BoundedProcessExitError(ReadbackError):
    """Internal final-exit classification; never a supervision failure."""
    pass


def require(condition, reason):
    if not condition:
        raise ReadbackError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate_json_key:{key}")
        result[key] = value
    return result


def parse_json(data):
    try:
        return json.loads(data, object_pairs_hook=strict_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ReadbackError("nonfinite_json")))
    except (ValueError, UnicodeError) as error:
        raise ReadbackError("invalid_json") from error


def read_regular(path, limit):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), "input_not_regular")
    size = path.stat().st_size
    require(0 < size <= limit, "input_size_out_of_bounds")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) == size and path.stat().st_size == size, "input_changed_while_reading")
    return data


def closed_object(value, fields, reason):
    require(isinstance(value, dict) and set(value) == fields, reason)
    return value


def uint(value, maximum, reason, minimum=0):
    require(type(value) is int and minimum <= value <= maximum, reason)
    return value


def digest_text(value, reason):
    require(type(value) is str and len(value) == 64
            and all(character in "0123456789abcdef" for character in value), reason)
    return value


def uuid_text(value, reason):
    require(type(value) is str, reason)
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ReadbackError(reason) from error
    require(str(parsed) == value and parsed.int != 0, reason)
    return value


def source_binding(value, reason):
    closed_object(value, BINDING_FIELDS, reason)
    for key in BINDING_FIELDS - {"revision"}:
        uuid_text(value[key], reason)
    uint(value["revision"], 2**64-1, reason)
    return value


def finite_vector3(value, reason):
    require(type(value) is list and len(value) == 3, reason)
    for number in value:
        require(type(number) in (int, float), reason)
        try:
            require(math.isfinite(number), reason)
        except OverflowError as error:
            raise ReadbackError(reason) from error
    return value


def source_entries(bundle):
    require(type(bundle) is bytes and 0 < len(bundle) <= MAX_SOURCE_BYTES, "source_archive_size")
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        entries = archive.infolist()
        require(len(entries) == 5 and {entry.filename for entry in entries} == MEMBERS and not archive.comment,
                "source_archive_entry_set")
        data = {}
        for entry in entries:
            require(entry.compress_type == zipfile.ZIP_STORED and not entry.is_dir() and not entry.flag_bits & 1
                    and 0 < entry.file_size <= MAX_ENTRY_BYTES and not entry.comment, "source_archive_entry_invalid")
            require(entry.filename not in data, "source_archive_duplicate_entry")
            data[entry.filename] = archive.read(entry)
    return data


def source_closure(value, data, reason):
    closed_object(value, CLOSURE_FIELDS, reason)
    require(type(value["plan_schema_version"]) is int and value["plan_schema_version"] == 2
            and value["compiler_contract_id"] == COMPILER, "source_compiler_contract")
    digest_fields = {"plan_sha256": "plan.json", "source_sha256": "scene.py",
                     "config_sha256": "render-config.json", "assets_lock_sha256": "assets.lock.json",
                     "manifest_sha256": "manifest.json"}
    for field, path in digest_fields.items():
        require(digest_text(value[field], reason) == sha(data[path]), "source_closure_digest_join")
    require(type(value["entries"]) is list and len(value["entries"]) == 5, "source_closure_entry_set")
    rows = {}
    for row in value["entries"]:
        closed_object(row, {"path", "bytes", "sha256"}, reason)
        require(type(row["path"]) is str and row["path"] in MEMBERS and row["path"] not in rows,
                "source_closure_entry_set")
        uint(row["bytes"], MAX_ENTRY_BYTES, reason, 1)
        digest_text(row["sha256"], reason)
        require(row["bytes"] == len(data[row["path"]]) and row["sha256"] == sha(data[row["path"]]),
                "source_closure_content_mismatch")
        rows[row["path"]] = row
    require(set(rows) == MEMBERS, "source_closure_entry_set")
    return value


def source_artifact(value, content, media_type, reason):
    closed_object(value, {"relative_path", "sha256", "bytes", "media_type"}, reason)
    path = value["relative_path"]
    require(type(path) is str and 0 < len(path) <= 250 and "/" not in path and "\\" not in path
            and path not in {".", ".."}, reason)
    digest_text(value["sha256"], reason)
    uint(value["bytes"], MAX_SOURCE_BYTES, reason, 1)
    require(value["sha256"] == sha(content) and value["bytes"] == len(content)
            and value["media_type"] == media_type, reason)
    return value


def source_origin_links(producer, source, plan, closure, bundle, data, origin_bundle):
    """Close declared joins; historical context is independent only with its bytes."""
    origin = closed_object(producer["source_origin"], ORIGIN_FIELDS, "source_origin_schema")
    uuid_text(origin["carrier_asset_id"], "source_origin_asset_id")
    digest_text(origin["carrier_sha256"], "source_origin_carrier_digest")
    historical = source_binding(origin["origin_binding"], "source_origin_binding_schema")
    current = source_binding(origin["current_binding"], "source_current_binding_schema")
    require(current == source_binding(plan["binding"], "source_plan_binding_schema"), "source_origin_current_join")
    require(all(historical[key] == current[key] for key in ("project_id", "scene_id", "deliverable_id")),
            "source_origin_identity_join")
    source_closure(origin["prepared_source"], data, "prepared_source_schema")
    require(origin["prepared_source"] == closure, "prepared_source_closure_join")
    digest_text(origin["prepared_bundle_sha256"], "prepared_bundle_digest_schema")
    require(origin["prepared_bundle_sha256"] == sha(bundle) == source["source_artifact"]["sha256"],
            "prepared_bundle_digest_join")
    require(origin["native_execution_performed"] is False
            and origin["imported_receipts_used_as_authority"] is False, "source_origin_authority_flags")
    require(producer["result"].get("source_carrier_origin") == origin, "source_origin_result_join")
    project = producer["project"]
    for key in ("id", "generation"):
        uuid_text(project[key], "source_project_stamp_schema")
    uint(project["revision"], 2**64-1, "source_project_stamp_schema")
    require((current["project_id"], current["generation"], current["revision"])
            == (project["id"], project["generation"], project["revision"]), "source_current_project_binding")
    assets = project.get("assets")
    require(type(assets) is list, "source_origin_asset_ledger")
    carriers = [asset for asset in assets if isinstance(asset, dict) and asset.get("id") == origin["carrier_asset_id"]]
    require(len(carriers) == 1 and carriers[0].get("content_sha256") == origin["carrier_sha256"]
            and carriers[0].get("media_type") == MIME, "source_origin_asset_join")
    require(isinstance(project.get("production_design"), dict), "source_origin_capsule_ledger")
    capsules = project["production_design"].get("capsules")
    require(type(capsules) is list and any(isinstance(capsule, dict)
            and capsule.get("scene_id") == historical["scene_id"]
            and capsule.get("source_asset_id") == origin["carrier_asset_id"]
            and capsule.get("source_sha256") == origin["carrier_sha256"] for capsule in capsules),
            "source_origin_capsule_join")
    profiles = project.get("deliverables")
    require(type(profiles) is list, "source_deliverable_ledger")
    profiles = [profile for profile in profiles if isinstance(profile, dict) and profile.get("id") == current["deliverable_id"]]
    require(len(profiles) == 1, "source_current_deliverable_join")
    uint(profiles[0].get("width"), 2**32-1, "source_current_deliverable_schema", 1)
    uint(profiles[0].get("height"), 2**32-1, "source_current_deliverable_schema", 1)
    require(profiles[0].get("width") == plan["render_profile"]["width"]
            and profiles[0].get("height") == plan["render_profile"]["height"]
            and rational(profiles[0].get("frame_rate")) == plan["render_profile"]["frame_rate"],
            "source_current_deliverable_join")
    if producer["phase"] in {"reopen", "edited-reopen"}:
        require(historical["generation"] != current["generation"], "source_reopen_context_not_rotated")
    independent = origin_bundle is not None
    if independent:
        require(sha(origin_bundle) == origin["carrier_sha256"], "origin_carrier_physical_digest")
        original_data = source_entries(origin_bundle)
        original_plan = parse_json(original_data["plan.json"])
        require(source_binding(original_plan["binding"], "physical_origin_binding_schema") == historical,
                "origin_binding_physical_join")
        rebound = dict(original_plan)
        rebound["binding"] = {**historical, "generation": current["generation"], "revision": current["revision"]}
        require(rebound == plan, "origin_current_semantic_rebind")
        for path in ("scene.py", "render-config.json", "assets.lock.json"):
            require(original_data[path] == data[path], "origin_current_source_content_join")
    return {"origin_binding": historical, "current_binding": current,
            "origin_carrier_asset_id": origin["carrier_asset_id"],
            "origin_carrier_sha256": origin["carrier_sha256"],
            "origin_carrier_bytes_independently_read": independent,
            "origin_binding_evidence": ("PHYSICAL_ORIGIN_BYTES_AND_REBIND_CHECKED" if independent else
                                        "PRODUCER_REFERENCE_BOUND_ORIGINAL_BYTES_NOT_READ"),
            "origin_reference_requires_retained_original_carrier": not independent}


def rational(value):
    require(isinstance(value, dict) and set(value) == {"num", "den"}, "rational_schema")
    require(type(value["num"]) is str and type(value["den"]) is str, "rational_wire_must_be_strings")
    n, d = int(value["num"]), int(value["den"])
    require(str(n) == value["num"] and str(d) == value["den"] and d > 0,
            "rational_wire_not_canonical")
    require(-(2**63) <= n < 2**63 and d < 2**63 and math.gcd(n, d) == 1, "rational_not_reduced")
    return Fraction(n, d)


def run_bounded(argv, max_stdout, timeout_seconds=120, *, input_bytes=None, capture=None):
    """Supervise one read-only decoder; stdin and capture are opt-in."""
    require(type(max_stdout) is int and max_stdout > 0, "readonly_decoder_stdout_budget")
    require(type(timeout_seconds) in (int, float)
            and math.isfinite(timeout_seconds) and timeout_seconds > 0,
            "readonly_decoder_timeout_budget")
    require(input_bytes is None or (type(input_bytes) is bytes
            and 0 < len(input_bytes) <= 32 * 1024 * 1024), "readonly_decoder_stdin_budget")
    require(capture is None or (type(capture) is dict and not capture),
            "readonly_decoder_capture_must_be_new")
    started = time.monotonic()
    deadline = started + timeout_seconds
    outputs = [bytearray(), bytearray()]
    overflow = threading.Event()
    errors = []
    stdin_complete = input_bytes is None
    if capture is not None:
        capture.update({"state": "RESERVED", "stdout": b"", "stderr": b"",
                        "stdin_bytes": 0 if input_bytes is None else len(input_bytes),
                        "stdin_sha256": None if input_bytes is None else sha(input_bytes)})
    try:
        process = subprocess.Popen(
            [str(arg) for arg in argv],
            stdin=subprocess.DEVNULL if input_bytes is None else subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    except BaseException as error:
        if capture is not None:
            capture.update({"state": "NOT_STARTED",
                            "startup_error_type": type(error).__name__,
                            "terminal": {"exit_code": None,
                                         "elapsed_seconds": time.monotonic() - started,
                                         "whole_process_completed": False}})
        raise

    def stop():
        try:
            if process.poll() is None:
                process.kill()
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append("kill:" + type(error).__name__)

    def drain(stream, index, limit):
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                room = max(0, limit + 1 - len(outputs[index]))
                outputs[index].extend(chunk[:room])
                if len(outputs[index]) > limit:
                    overflow.set()
                    stop()
                    break
        except OSError as error:
            errors.append("read:" + type(error).__name__)
            stop()
        finally:
            try:
                stream.close()
            except OSError as error:
                errors.append("close:" + type(error).__name__)

    def feed():
        nonlocal stdin_complete
        try:
            view = memoryview(input_bytes)
            while view:
                written = process.stdin.write(view)
                if written is None or written <= 0:
                    raise OSError("decoder_stdin_write_incomplete")
                view = view[written:]
            process.stdin.flush()
            stdin_complete = True
        except OSError as error:
            errors.append("write:" + type(error).__name__)
            stop()
        finally:
            try:
                process.stdin.close()
            except OSError as error:
                errors.append("close_stdin:" + type(error).__name__)

    threads = [threading.Thread(target=drain, args=(process.stdout, 0, max_stdout), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, 1, 65536), daemon=True)]
    if input_bytes is not None:
        threads.append(threading.Thread(target=feed, daemon=True))
    active = []
    timed_out = False
    original_error = None
    try:
        for thread in threads:
            thread.start()
            active.append(thread)
        process.wait(timeout=max(0, deadline - time.monotonic()))
        for thread in active:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in active):
            timed_out = True
            stop()
    except subprocess.TimeoutExpired:
        timed_out = True
        stop()
    except BaseException as error:
        original_error = error
        stop()
    finally:
        # One cleanup allowance, shared by process and every pipe thread.
        cleanup_deadline = time.monotonic() + 10
        if process.poll() is None:
            stop()
            try:
                process.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                errors.append("cleanup:TimeoutExpired")
            except OSError as error:
                errors.append("cleanup:" + type(error).__name__)
        for thread in active:
            thread.join(timeout=max(0, cleanup_deadline - time.monotonic()))
        pipes_closed = not any(thread.is_alive() for thread in active)
        terminal = {"exit_code": process.returncode,
                    "elapsed_seconds": time.monotonic() - started,
                    "stdout_bytes": len(outputs[0]), "stderr_bytes": len(outputs[1]),
                    "stderr_sha256": sha(outputs[1]),
                    "whole_process_completed": process.returncode is not None and pipes_closed,
                    "timed_out": timed_out, "output_budget_exceeded": overflow.is_set(),
                    "pipes_closed": pipes_closed, "stdin_complete": stdin_complete,
                    "pipe_errors": list(errors)}
        if capture is not None:
            capture.update({"state": "TERMINAL_OBSERVED", "terminal": terminal,
                            "stdout": bytes(outputs[0]), "stderr": bytes(outputs[1])})
    # Retain exact bounded streams and terminal before interpreting success.
    if original_error is not None:
        raise original_error
    require(not timed_out, "readonly_decoder_timeout")
    require(not overflow.is_set(), "readonly_decoder_output_budget")
    require(pipes_closed, "decoder_pipe_not_closed")
    require(not errors and stdin_complete, "readonly_decoder_pipe_error")
    if process.returncode != 0:
        raise BoundedProcessExitError(
            f"readonly_decoder_failed:{process.returncode}:{bytes(outputs[1]).decode('utf-8', 'replace')[:2000]}")
    return bytes(outputs[0]), {"exit_code": process.returncode,
                              "elapsed_seconds": terminal["elapsed_seconds"],
                              "stdout_bytes": len(outputs[0]), "stderr_bytes": len(outputs[1]),
                              "stderr_sha256": sha(outputs[1]), "whole_process_completed": True}


def expected_fixture(phase):
    require(phase in {"original", "reopen", "edit", "edited-reopen", "edge25"}, "unknown_fixture_phase")
    changed = phase in {"edit", "edited-reopen"}
    fps, count = (25, 29) if phase == "edge25" else (12, 24)
    return changed, fps, count


def shape_distances(x, y, changed):
    def rectangle(x0, y0, x1, y1):
        if x0 <= x <= x1 and y0 <= y <= y1:
            return min(x - x0, x1 - x, y - y0, y1 - y)
        return -math.hypot(max(x0-x, 0, x-x1), max(y0-y, 0, y-y1))
    red_left = 110 if changed else 80
    return (rectangle(460, 40, 560, 90), rectangle(red_left, 60, red_left + 200, 180),
            40 - math.hypot(x-290, y-170))


def expected_masks(changed):
    labels = bytearray(WIDTH * HEIGHT)
    cores = bytearray(WIDTH * HEIGHT)
    allowed = bytearray(WIDTH * HEIGHT)
    counts = [0, 0, 0, 0]
    for y in range(HEIGHT):
        for x in range(WIDTH):
            index = y * WIDTH + x
            distances = shape_distances(x + .5, y + .5, changed)
            label = 0
            for shape, distance in enumerate(distances, start=1):
                if distance >= 0:
                    label = shape
                if distance >= -BOUNDARY_MARGIN:
                    allowed[index] |= 1 << shape
            labels[index] = label
            if all(abs(distance) > BOUNDARY_MARGIN for distance in distances):
                cores[index] = 1
                counts[label] += 1
    return labels, cores, allowed, counts


def check_pixels(frame, masks, changed):
    require(len(frame) == FRAME_BYTES, "decoded_frame_size")
    labels, cores, allowed, counts = masks
    matched = [0, 0, 0, 0]
    boxes = [[WIDTH, HEIGHT, -1, -1] for _ in range(4)]
    stray = [0, 0, 0, 0]
    for index, label in enumerate(labels):
        offset = index * 3
        rgb = frame[offset:offset+3]
        expected = COLORS[label]
        if cores[index] and all(abs(rgb[channel]-expected[channel]) <= MAX_CHANNEL_ERROR for channel in range(3)):
            matched[label] += 1
        observed = None
        for shape in (1, 2, 3):
            color = COLORS[shape]
            if all(abs(rgb[channel]-color[channel]) <= MAX_CHANNEL_ERROR for channel in range(3)):
                observed = shape
                break
        if observed is not None:
            y, x = divmod(index, WIDTH)
            box = boxes[observed]
            box[0] = min(box[0], x); box[1] = min(box[1], y)
            box[2] = max(box[2], x+1); box[3] = max(box[3], y+1)
            if not (allowed[index] & (1 << observed)):
                stray[observed] += 1
    fractions = [matched[i]/counts[i] for i in range(4)]
    require(fractions[0] >= MIN_BACKGROUND_FRACTION, f"background_content_mismatch:{fractions[0]}")
    for i in (1, 2, 3):
        require(fractions[i] >= MIN_CORE_FRACTION, f"shape_{i}_content_mismatch:{fractions[i]}")
        require(stray[i] == 0, f"shape_{i}_outside_expected_geometry:{stray[i]}")
    left = 110 if changed else 80
    expected_boxes = [None, [460,40,560,90], [left,60,left+200,180], [250,130,330,210]]
    for i in (1, 2, 3):
        require(all(abs(a-b) <= BBOX_EDGE_ERROR for a,b in zip(boxes[i],expected_boxes[i])),
                f"shape_{i}_bbox_mismatch:{boxes[i]}")
    for x,y,label in [(120,90,2),(271,163,3),(500,60,1),(400,280,0)]:
        for yy in range(y-3,y+4):
            for xx in range(x-3,x+4):
                offset=(yy*WIDTH+xx)*3
                require(all(abs(frame[offset+c]-COLORS[label][c]) <= MAX_CHANNEL_ERROR for c in range(3)),
                        f"mandatory_neighborhood_mismatch:{x}:{y}:{label}")
    if changed:
        for x0,y0,x1,y1,label in [(83,80,107,120,0),(283,80,307,120,2)]:
            for y in range(y0,y1):
                for x in range(x0,x1):
                    offset=(y*WIDTH+x)*3
                    require(all(abs(frame[offset+c]-COLORS[label][c]) <= MAX_CHANNEL_ERROR for c in range(3)),
                            "edited_source_difference_not_visible")
    return {"matching_core_fractions": fractions, "observed_visible_bounds": boxes[1:],
            "stray_saturated_pixels": stray[1:]}


def inspect_sources(producer, bundle, native_bytes, phase, *, origin_bundle=None):
    changed, fps, count = expected_fixture(phase)
    closed_object(producer, {"schema", "phase", "project", "request_id", "source_origin", "result",
                            "native_source_getters_verified", "decoded_media_fidelity_verified",
                            "human_acceptance"}, "producer_schema")
    require(producer.get("schema") == "motionwright.manim.native-source-producer/2" and producer.get("phase") == phase,
            "producer_schema_or_phase")
    require(type(producer["request_id"]) is str and 0 < len(producer["request_id"]) <= 250
            and isinstance(producer["result"], dict) and isinstance(producer["project"], dict), "producer_field_schema")
    require(producer["native_source_getters_verified"] is True
            and producer["decoded_media_fidelity_verified"] is False
            and producer["human_acceptance"] is False, "producer_evidence_scope")
    source = producer["result"]["source"]
    closed_object(source, {"source_plan", "source_closure", "source_artifact", "native_readback_artifact",
                           "native_readback", "native_getter_properties_verified", "decoded_media_fidelity_verified",
                           "native_edit_import_applied", "audio_and_captions_included"}, "source_observation_schema")
    require(source["native_getter_properties_verified"] is True
            and source["decoded_media_fidelity_verified"] is False
            and source["native_edit_import_applied"] is False
            and source["audio_and_captions_included"] is False, "source_evidence_scope")
    source_artifact(source["source_artifact"], bundle, MIME, "source_carrier_digest_mismatch")
    source_artifact(source["native_readback_artifact"], native_bytes, "application/json", "native_readback_digest_mismatch")
    data = source_entries(bundle)
    closure = source_closure(source["source_closure"], data, "source_closure_schema")
    plan = parse_json(data["plan.json"])
    require(plan == source["source_plan"] and closure["plan_sha256"] == sha(data["plan.json"]), "source_plan_digest_mismatch")
    require(plan["schema_version"] == 2 and plan["profile_id"] == PROFILE, "source_plan_schema")
    require(plan["render_profile"] == {"width":WIDTH,"height":HEIGHT,"frame_rate":fps}, "source_render_profile")
    origin = source_origin_links(producer, source, plan, closure, bundle, data, origin_bundle)
    clock = plan["clock"]
    require(rational(clock["timeline_start"]) == 0 and rational(clock["duration"]) == Fraction(count,fps)
            and rational(clock["frame_rate"]) == fps and clock["local_start_frame"] == 0
            and clock["frame_count"] == count and clock["project_start_frame"] == 0
            and clock["project_end_frame_exclusive"] == count, "source_exact_clock")
    project = producer["project"]
    require(plan["binding"]["project_id"] == project["id"] and plan["binding"]["generation"] == project["generation"]
            and plan["binding"]["revision"] == project["revision"], "source_current_project_binding")
    scenes = [scene for scene in project["scenes"] if scene["id"] == plan["binding"]["scene_id"]]
    require(len(scenes) == 1 and len(scenes[0]["nodes"]) == 3, "source_current_scene")
    require([node["node_id"] for node in plan["nodes"]] == IDS, "source_full_uuid_order")
    expected = [(750,390,240,240,"#20CC40",20,"circle"),
                (330 if changed else 240,180,600,360,"#E02040",10,"rectangle"),
                (1380,120,300,150,"#2040E0",0,"rectangle")]
    for ordinal, (node, canonical, row) in enumerate(zip(plan["nodes"], scenes[0]["nodes"], expected)):
        x,y,w,h,fill,z,kind = row
        require((node["x_px"],node["y_px"],node["width_px"],node["height_px"],node["fill_hex"],node["z_index"],node["source_kind"]) == row,
                "source_plan_geometry_or_style")
        require(node["canonical_ordinal"] == ordinal and node["display_name"] == canonical["name"], "source_identity_projection")
        require((canonical["id"],canonical["x"],canonical["y"],canonical["width"],canonical["height"],canonical["style"]["fill"],canonical["z_index"],canonical["kind"])
                == (node["node_id"],x,y,w,h,fill,z,kind), "source_canonical_content")
        require(canonical["opacity"] == 1 and canonical["rotation_deg"] == 0 and not canonical["keyframes"]
                and canonical["parent_id"] is None and not canonical["relations"] and canonical["text"] is None
                and canonical["style"]["stroke"] is None and canonical["style"]["stroke_width"] == 0, "unrepresented_canonical_control")
    observed = parse_json(native_bytes)
    require(observed == source["native_readback"], "native_getter_record_changed")
    require(observed["schema_version"] == 1 and observed["profile_id"] == PROFILE and observed["scene_class"] == "MotionwrightScene",
            "native_getter_record_schema")
    require([item["name"] for item in observed["objects"]] == ["mw_"+IDS[index].replace("-", "") for index in (2,1,0)],
            "native_identity_or_z_order")
    for actual, index in zip(observed["objects"], (2,1,0)):
        x,y,w,h,fill,z,kind = expected[index]
        center = [(x+w/2-960)/135, (540-y-h/2)/135, 0]
        actual_center = finite_vector3(actual["center"], "native_getter_center_schema")
        require(all(abs(a-b) <= 1e-9 for a,b in zip(actual_center,center))
                and abs(actual["width"]-w/135) <= 1e-9 and abs(actual["height"]-h/135) <= 1e-9
                and actual["primitive"].lower() == kind and actual["z_index"] == z
                and actual["fill_hex"].upper() == fill and actual["fill_opacity"] == 1
                and actual["stroke_width"] == 0 and actual["stroke_opacity"] == 0, "native_getter_content")
    return {"source_bundle_sha256":sha(bundle),"source_plan_sha256":sha(data["plan.json"]),
            "native_readback_sha256":sha(native_bytes), **origin,
            "source_entry_count":5,"source_projection_executed_by_this_reader":False}


def inspect_probe(probe, fps, count):
    streams = probe.get("streams", [])
    require(len(streams) == 1 and streams[0].get("codec_type") == "video", "contribution_has_audio_or_other_streams")
    video = streams[0]
    require(video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p"
            and (video.get("width"),video.get("height")) == (WIDTH,HEIGHT), "native_video_codec_or_dimensions")
    require(Fraction(video["r_frame_rate"]) == fps and Fraction(video["avg_frame_rate"]) == fps, "native_video_frame_rate")
    base = Fraction(video["time_base"])
    require(base > 0 and int(video.get("start_pts", -1)) == 0
            and int(video["duration_ts"])*base == Fraction(count,fps), "native_stream_interval")
    frames = probe.get("frames", [])
    require(len(frames) == count, f"native_frame_count:{len(frames)}:{count}")
    for index, frame in enumerate(frames):
        require(frame.get("media_type") == "video" and frame.get("stream_index") == video["index"], "native_foreign_frame")
        require(int(frame["best_effort_timestamp"])*base == Fraction(index,fps), f"native_frame_pts:{index}")
        if "pts" in frame:
            require(int(frame["pts"])*base == Fraction(index,fps), f"native_frame_exact_pts:{index}")
    return {"frame_count":len(frames),"frame_rate":f"{fps}/1","time_base":str(base),
            "first_pts":"0/1","end_exclusive":str(Fraction(count,fps)),"audio_stream_count":0,
            "codec":video["codec_name"],"pixel_format":video["pix_fmt"],"all_frame_timestamps_verified":True}


def png(rgb):
    def chunk(kind, content):
        return struct.pack(">I",len(content))+kind+content+struct.pack(">I",zlib.crc32(kind+content)&0xffffffff)
    scan=b"".join(b"\0"+rgb[y*WIDTH*3:(y+1)*WIDTH*3] for y in range(HEIGHT))
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",WIDTH,HEIGHT,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(scan,9))+chunk(b"IEND",b"")


def inspect(args):
    changed,fps,count=expected_fixture(args.phase)
    evidence_bytes=read_regular(args.producer,MAX_REPORT_INPUT)
    producer=parse_json(evidence_bytes)
    bundle=read_regular(args.source,MAX_SOURCE_BYTES)
    native=read_regular(args.native_readback,MAX_ENTRY_BYTES)
    origin_bundle=read_regular(args.origin_source,MAX_SOURCE_BYTES) if args.origin_source is not None else None
    source=inspect_sources(producer,bundle,native,args.phase,origin_bundle=origin_bundle)
    video=Path(args.video).resolve(strict=True)
    data=read_regular(video,MAX_VIDEO_BYTES)
    digest=sha(data)
    expected=producer["result"]["artifact"]
    require(expected["sha256"]==digest and expected["bytes"]==len(data),"native_video_not_bound_to_producer")
    del data
    ffprobe=Path(shutil.which(args.ffprobe) or args.ffprobe).resolve(strict=True)
    ffmpeg=Path(shutil.which(args.ffmpeg) or args.ffmpeg).resolve(strict=True)
    versions={}
    for name,tool in [("ffprobe",ffprobe),("ffmpeg",ffmpeg)]:
        text,run=run_bounded([tool,"-version"],65536,15)
        versions[name]={"sha256":sha(read_regular(tool,MAX_VIDEO_BYTES)),"version":text.decode("utf-8","replace").splitlines()[0],"process":run}
    probe_bytes,probe_run=run_bounded([ffprobe,"-v","error","-show_streams","-show_format","-show_frames","-of","json",video],8*1024*1024)
    media=inspect_probe(parse_json(probe_bytes),fps,count)
    raw,decode_run=run_bounded([ffmpeg,"-v","error","-xerror","-nostdin","-i",video,
                               "-map","0:v:0","-vsync","0","-pix_fmt","rgb24","-f","rawvideo","pipe:1"],FRAME_BYTES*(count+1))
    require(len(raw)==FRAME_BYTES*count,"full_decode_frame_count_mismatch")
    masks=expected_masks(changed)
    measurements=[]
    for index in range(count):
        try:
            frame=raw[index*FRAME_BYTES:(index+1)*FRAME_BYTES]
            measurement=check_pixels(frame,masks,changed)
            measurement["index"]=index;measurement["rgb24_sha256"]=sha(frame);measurements.append(measurement)
        except ReadbackError as error:
            raise ReadbackError(f"decoded_frame_{index}:{error}") from error
    require(sha(read_regular(video,MAX_VIDEO_BYTES))==digest,"native_video_changed_during_readback")
    images=[]
    if args.frames:
        destination=Path(args.frames)
        require(destination.is_dir() and not destination.is_symlink(),"frame_output_directory")
        for index in sorted({0,count-1}):
            name=f"manim-{args.phase}-frame-{index:04d}.png"
            content=png(raw[index*FRAME_BYTES:(index+1)*FRAME_BYTES])
            with (destination/name).open("xb") as stream:stream.write(content)
            images.append({"name":name,"bytes":len(content),"sha256":sha(content)})
    return {"schema":"motionwright.manim.static-source-readback/1","status":"PASS","phase":args.phase,
            "classification":"REAL_DECODED_NATIVE_FIXTURE_CONTENT_READBACK_NOT_RELEASE_ACCEPTANCE",
            "producer_record_sha256":sha(evidence_bytes),"video_sha256":digest,"video_bytes":expected["bytes"],
            "source":source,"media":media,"decoder_identity":versions,"probe_process":probe_run,"decode_process":decode_run,
            "decoded_rgb24_bytes":len(raw),"decoded_rgb24_sha256":sha(raw),"frames":measurements,"images":images,
            "thresholds":{"rgb_channel_error":MAX_CHANNEL_ERROR,"boundary_margin":BOUNDARY_MARGIN,
                          "min_shape_core_fraction":MIN_CORE_FRACTION,"min_background_fraction":MIN_BACKGROUND_FRACTION,"bbox_edge_error":BBOX_EDGE_ERROR},
            "losses":["static shapes only","opaque black matte","no audio or captions","binary64 geometry projection",
                      "static identical-frame permutations are not observable animation evidence"],
            "human_acceptance":False,"external_rights_certified":False,"native_job_retried":False}


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.masks=expected_masks(False)
        cls.frame=b"".join(bytes(COLORS[label]) for label in cls.masks[0])

    @staticmethod
    def source_unit_fixture(phase="original"):
        """In-memory parser controls only: no compiler, provider, video or receipt.

        Getter values below are deliberately authored unit-test data. This helper
        never calls inspect(), writes evidence, or claims to be a native render.
        scene.py is an inert marker, not a replacement Manim source compiler.
        """
        changed, fps, count = expected_fixture(phase)
        clone = lambda value: parse_json(json.dumps(value))
        wire = lambda n, d=1: {"num": str(Fraction(n, d).numerator), "den": str(Fraction(n, d).denominator)}
        current = {"project_id": "20000000-0000-4000-8000-000000000001",
                   "generation": "20000000-0000-4000-8000-000000000002", "revision": 7,
                   "scene_id": "20000000-0000-4000-8000-000000000003",
                   "deliverable_id": "20000000-0000-4000-8000-000000000004"}
        historical = {**current, "revision": 5}
        if phase in {"reopen", "edited-reopen"}:
            historical["generation"] = "20000000-0000-4000-8000-000000000005"
        geometry = [(750,390,240,240,"#20CC40",20,"circle"),
                    (330 if changed else 240,180,600,360,"#E02040",10,"rectangle"),
                    (1380,120,300,150,"#2040E0",0,"rectangle")]
        nodes, canonical, objects = [], [], []
        for ordinal, (identity, (x,y,w,h,fill,z,kind)) in enumerate(zip(IDS, geometry)):
            nodes.append({"node_id": identity, "display_name": "UNIT CONTROL", "source_kind": kind,
                          "canonical_ordinal": ordinal, "z_index": z, "x_px": x, "y_px": y,
                          "width_px": w, "height_px": h, "fill_hex": fill})
            canonical.append({"id": identity, "name": "UNIT CONTROL", "kind": kind, "x": x, "y": y,
                              "width": w, "height": h, "z_index": z, "opacity": 1, "rotation_deg": 0,
                              "keyframes": [], "parent_id": None, "relations": [], "text": None,
                              "style": {"fill": fill, "stroke": None, "stroke_width": 0}})
            objects.append({"name": "mw_"+identity.replace("-", ""), "primitive": kind.title(),
                            "center": [(x+w/2-960)/135, (540-y-h/2)/135, 0.0], "width": w/135,
                            "height": h/135, "z_index": z, "fill_hex": fill, "fill_opacity": 1.0,
                            "stroke_width": 0.0, "stroke_opacity": 0.0})
        plan = {"schema_version": 2, "profile_id": PROFILE, "output_role": "scene_video_contribution_without_audio_or_captions",
                "binding": current, "render_profile": {"width": WIDTH, "height": HEIGHT, "frame_rate": fps},
                "canvas": {"width": 1920, "height": 1080, "coordinate_space": "project_pixels"},
                "background": {"kind": "solid", "color": "#000000", "opacity": 1}, "nodes": nodes,
                "clock": {"timeline_start": wire(0), "duration": wire(count, fps), "frame_rate": wire(fps),
                          "local_start_frame": 0, "frame_count": count, "project_start_frame": 0,
                          "project_end_frame_exclusive": count}}

        def package(value):
            entries = {"plan.json": json.dumps(value, sort_keys=True).encode(),
                       "scene.py": b"# UNIT PARSER CONTROL; NEVER EXECUTED\n",
                       "render-config.json": b'{"classification":"UNIT_PARSER_CONTROL"}',
                       "assets.lock.json": b'{"assets":[],"classification":"UNIT_PARSER_CONTROL"}'}
            entries["manifest.json"] = json.dumps({"classification": "UNIT_PARSER_CONTROL",
                "entries": [{"path": path, "bytes": len(data), "sha256": sha(data)}
                            for path, data in sorted(entries.items())]}, sort_keys=True).encode()
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
                for path, data in sorted(entries.items()):
                    archive.writestr(zipfile.ZipInfo(path), data)
            closure = {"plan_schema_version": 2, "compiler_contract_id": COMPILER,
                       **{field: sha(entries[path]) for field, path in {
                           "plan_sha256": "plan.json", "source_sha256": "scene.py", "config_sha256": "render-config.json",
                           "assets_lock_sha256": "assets.lock.json", "manifest_sha256": "manifest.json"}.items()},
                       "entries": [{"path": path, "bytes": len(data), "sha256": sha(data)}
                                   for path, data in sorted(entries.items())]}
            return buffer.getvalue(), closure

        bundle, closure = package(plan)
        original_plan = clone(plan); original_plan["binding"] = historical
        origin_bundle, _ = package(original_plan)
        asset_id = "20000000-0000-4000-8000-000000000006"
        origin = {"carrier_asset_id": asset_id, "carrier_sha256": sha(origin_bundle),
                  "origin_binding": historical, "current_binding": current, "prepared_source": closure,
                  "prepared_bundle_sha256": sha(bundle), "native_execution_performed": False,
                  "imported_receipts_used_as_authority": False}
        observed = {"schema_version": 1, "profile_id": PROFILE, "scene_class": "MotionwrightScene",
                    "camera": {"pixel_width": WIDTH, "pixel_height": HEIGHT, "frame_rate": fps,
                               "frame_width": 128/9, "frame_height": 8.0, "frame_center": [0.0,0.0,0.0],
                               "background_hex": "#000000", "background_opacity": 1.0},
                    "objects": sorted(objects, key=lambda item: item["z_index"])}
        native = json.dumps(observed, sort_keys=True).encode()
        artifact = lambda content, path, mime: {"relative_path": path, "sha256": sha(content),
                                               "bytes": len(content), "media_type": mime}
        producer = {"schema": "motionwright.manim.native-source-producer/2", "phase": phase,
            "request_id": "UNIT_PARSER_CONTROL_NO_NATIVE_JOB", "source_origin": origin,
            "native_source_getters_verified": True, "decoded_media_fidelity_verified": False, "human_acceptance": False,
            "project": {"id": current["project_id"], "generation": current["generation"], "revision": current["revision"],
                "scenes": [{"id": current["scene_id"], "nodes": canonical}],
                "deliverables": [{"id": current["deliverable_id"], "width": WIDTH, "height": HEIGHT, "frame_rate": wire(fps)}],
                "assets": [{"id": asset_id, "content_sha256": sha(origin_bundle), "media_type": MIME}],
                "production_design": {"capsules": [{"scene_id": current["scene_id"], "source_asset_id": asset_id,
                                                    "source_sha256": sha(origin_bundle)}]}},
            "result": {"source_carrier_origin": origin, "source": {"source_plan": plan, "source_closure": closure,
                "source_artifact": artifact(bundle, "unit-current.mwmsrc.zip", MIME),
                "native_readback_artifact": artifact(native, "unit.objects.json", "application/json"),
                "native_readback": observed, "native_getter_properties_verified": True,
                "decoded_media_fidelity_verified": False, "native_edit_import_applied": False,
                "audio_and_captions_included": False}}}
        # Serialization breaks all aliases just as separate physical JSON does.
        return clone(producer), bundle, native, origin_bundle

    @staticmethod
    def coherent_unit_center(producer, center):
        observed = producer["result"]["source"]["native_readback"]
        observed["objects"][0]["center"] = center
        native = json.dumps(observed, sort_keys=True).encode()
        artifact = producer["result"]["source"]["native_readback_artifact"]
        artifact.update(sha256=sha(native), bytes=len(native))
        return native

    def test_source_controls_join_all_phases_without_claiming_origin_bytes(self):
        for phase in ("original", "reopen", "edit", "edited-reopen", "edge25"):
            with self.subTest(phase=phase):
                producer, bundle, native, _ = self.source_unit_fixture(phase)
                report = inspect_sources(producer, bundle, native, phase)
                self.assertFalse(report["origin_carrier_bytes_independently_read"])
                self.assertEqual(report["origin_binding_evidence"], "PRODUCER_REFERENCE_BOUND_ORIGINAL_BYTES_NOT_READ")
                self.assertTrue(report["origin_reference_requires_retained_original_carrier"])

    def test_origin_physical_read_is_separate_from_current_invocation_zip(self):
        for phase in ("original", "reopen", "edit", "edited-reopen", "edge25"):
            with self.subTest(phase=phase):
                producer, bundle, native, origin = self.source_unit_fixture(phase)
                self.assertNotEqual(sha(origin), sha(bundle))
                report = inspect_sources(producer, bundle, native, phase, origin_bundle=origin)
                self.assertTrue(report["origin_carrier_bytes_independently_read"])
                self.assertEqual(report["origin_binding_evidence"], "PHYSICAL_ORIGIN_BYTES_AND_REBIND_CHECKED")
                with self.assertRaisesRegex(ReadbackError, "origin_carrier_physical_digest"):
                    inspect_sources(producer, bundle, native, phase, origin_bundle=bundle)

    def test_foreign_historical_stable_identity_rejected(self):
        for field in ("project_id", "scene_id", "deliverable_id"):
            with self.subTest(field=field):
                producer, bundle, native, _ = self.source_unit_fixture()
                producer["source_origin"]["origin_binding"][field] = "90000000-0000-4000-8000-000000000001"
                producer["result"]["source_carrier_origin"] = producer["source_origin"]
                with self.assertRaisesRegex(ReadbackError, "source_origin_identity_join"):
                    inspect_sources(producer, bundle, native, "original")

    def test_foreign_current_binding_rejected(self):
        for field, value in [("generation", "90000000-0000-4000-8000-000000000001"), ("revision", 9)]:
            with self.subTest(field=field):
                producer, bundle, native, _ = self.source_unit_fixture()
                producer["source_origin"]["current_binding"][field] = value
                producer["result"]["source_carrier_origin"] = producer["source_origin"]
                with self.assertRaisesRegex(ReadbackError, "source_origin_current_join"):
                    inspect_sources(producer, bundle, native, "original")

    def test_origin_binding_schema_rejects_unknown_keys_bad_uuid_bool_revision(self):
        for field, value in [("extra", "unknown"), ("project_id", "invalid"), ("revision", True),
                             ("revision", -1), ("revision", 2**64)]:
            with self.subTest(field=field, value=value):
                producer, bundle, native, _ = self.source_unit_fixture()
                producer["source_origin"]["origin_binding"][field] = value
                producer["result"]["source_carrier_origin"] = producer["source_origin"]
                with self.assertRaisesRegex(ReadbackError, "source_origin_binding_schema"):
                    inspect_sources(producer, bundle, native, "original")

    def test_origin_historical_context_requires_physical_carrier_for_independence(self):
        producer, bundle, native, origin = self.source_unit_fixture("reopen")
        producer["source_origin"]["origin_binding"]["revision"] = 99
        producer["result"]["source_carrier_origin"] = producer["source_origin"]
        reference = inspect_sources(producer, bundle, native, "reopen")
        self.assertFalse(reference["origin_carrier_bytes_independently_read"])
        with self.assertRaisesRegex(ReadbackError, "origin_binding_physical_join"):
            inspect_sources(producer, bundle, native, "reopen", origin_bundle=origin)

    def test_physical_origin_cannot_change_semantics_behind_coherent_reference_digests(self):
        producer, bundle, native, _ = self.source_unit_fixture("original")
        _, _, _, foreign = self.source_unit_fixture("edit")
        producer["source_origin"]["carrier_sha256"] = sha(foreign)
        producer["result"]["source_carrier_origin"] = producer["source_origin"]
        producer["project"]["assets"][0]["content_sha256"] = sha(foreign)
        producer["project"]["production_design"]["capsules"][0]["source_sha256"] = sha(foreign)
        with self.assertRaisesRegex(ReadbackError, "origin_current_semantic_rebind"):
            inspect_sources(producer, bundle, native, "original", origin_bundle=foreign)

    def test_prepared_bundle_digest_must_match_current_physical_zip(self):
        producer, bundle, native, origin = self.source_unit_fixture()
        producer["source_origin"]["prepared_bundle_sha256"] = sha(origin)
        producer["result"]["source_carrier_origin"] = producer["source_origin"]
        with self.assertRaisesRegex(ReadbackError, "prepared_bundle_digest_join"):
            inspect_sources(producer, bundle, native, "original")

    def test_each_prepared_source_digest_must_match_physical_entries(self):
        for field in ("plan_sha256", "source_sha256", "config_sha256", "assets_lock_sha256", "manifest_sha256"):
            with self.subTest(field=field):
                producer, bundle, native, _ = self.source_unit_fixture()
                producer["source_origin"]["prepared_source"][field] = "0"*64
                producer["result"]["source_carrier_origin"] = producer["source_origin"]
                with self.assertRaisesRegex(ReadbackError, "source_closure_digest_join"):
                    inspect_sources(producer, bundle, native, "original")

    def test_source_closure_header_digests_cannot_disagree_with_valid_entry_rows(self):
        for field in ("source_sha256", "config_sha256", "assets_lock_sha256", "manifest_sha256"):
            with self.subTest(field=field):
                producer, bundle, native, _ = self.source_unit_fixture()
                producer["result"]["source"]["source_closure"][field] = "0"*64
                with self.assertRaisesRegex(ReadbackError, "source_closure_digest_join"):
                    inspect_sources(producer, bundle, native, "original")

    def test_prepared_closure_cannot_add_unknown_fields_or_duplicate_rows(self):
        for mode in ("unknown", "duplicate", "bool_bytes", "string_version"):
            with self.subTest(mode=mode):
                producer, bundle, native, _ = self.source_unit_fixture()
                closure = producer["source_origin"]["prepared_source"]
                if mode == "unknown": closure["extra"] = True
                elif mode == "duplicate": closure["entries"][1] = closure["entries"][0]
                elif mode == "bool_bytes": closure["entries"][0]["bytes"] = True
                else: closure["plan_schema_version"] = "2"
                producer["result"]["source_carrier_origin"] = producer["source_origin"]
                with self.assertRaises(ReadbackError): inspect_sources(producer, bundle, native, "original")

    def test_origin_asset_capsule_and_deliverable_must_resolve_in_current_project(self):
        for field in ("assets", "capsules", "deliverables"):
            with self.subTest(field=field):
                producer, bundle, native, _ = self.source_unit_fixture()
                if field == "capsules": producer["project"]["production_design"]["capsules"][0]["source_sha256"] = "0"*64
                elif field == "assets": producer["project"]["assets"][0]["content_sha256"] = "0"*64
                else: producer["project"]["deliverables"][0]["frame_rate"] = {"num": "25", "den": "1"}
                with self.assertRaises(ReadbackError): inspect_sources(producer, bundle, native, "original")

    def test_origin_and_coordinator_inspection_must_agree(self):
        producer, bundle, native, _ = self.source_unit_fixture()
        producer["result"]["source_carrier_origin"]["carrier_sha256"] = "0"*64
        with self.assertRaisesRegex(ReadbackError, "source_origin_result_join"):
            inspect_sources(producer, bundle, native, "original")

    def test_origin_schema_and_authority_flags_are_closed(self):
        for field, value in [("extra", False), ("native_execution_performed", True),
                             ("imported_receipts_used_as_authority", 0)]:
            with self.subTest(field=field):
                producer, bundle, native, _ = self.source_unit_fixture()
                producer["source_origin"][field] = value
                producer["result"]["source_carrier_origin"] = producer["source_origin"]
                with self.assertRaises(ReadbackError): inspect_sources(producer, bundle, native, "original")

    def test_reopen_requires_a_new_generation(self):
        producer, bundle, native, _ = self.source_unit_fixture("reopen")
        producer["source_origin"]["origin_binding"]["generation"] = producer["project"]["generation"]
        producer["result"]["source_carrier_origin"] = producer["source_origin"]
        with self.assertRaisesRegex(ReadbackError, "source_reopen_context_not_rotated"):
            inspect_sources(producer, bundle, native, "reopen")

    def test_center_requires_exactly_three_coordinates_with_coherent_getter_digest(self):
        for length in (0, 1, 2, 4):
            with self.subTest(length=length):
                producer, bundle, _, _ = self.source_unit_fixture()
                center = producer["result"]["source"]["native_readback"]["objects"][0]["center"]
                native = self.coherent_unit_center(producer, (center+[0])[:length])
                with self.assertRaisesRegex(ReadbackError, "native_getter_center_schema"):
                    inspect_sources(producer, bundle, native, "original")

    def test_center_rejects_wrong_type_bool_and_nonfinite_with_coherent_getters(self):
        for center in (None, "123", {"x": 1}, [False, 0, 0], ["1", 0, 0],
                       [float("nan"), 0, 0], [float("inf"), 0, 0], [10**400, 0, 0]):
            with self.subTest(center=repr(center)):
                producer, bundle, _, _ = self.source_unit_fixture()
                native = self.coherent_unit_center(producer, center)
                with self.assertRaises(ReadbackError): inspect_sources(producer, bundle, native, "original")

    def test_center_rejects_wrong_numeric_value_after_schema_validation(self):
        producer, bundle, _, _ = self.source_unit_fixture()
        native = self.coherent_unit_center(producer, [0.0, 0.0, 0.0])
        with self.assertRaisesRegex(ReadbackError, "native_getter_content"):
            inspect_sources(producer, bundle, native, "original")

    def test_center_finite_guard_rejects_nonfinite_and_bool_directly(self):
        for center in ([float("nan"), 0, 0], [float("-inf"), 0, 0], [0, False, 0], [10**400, 0, 0]):
            with self.assertRaisesRegex(ReadbackError, "center_test"):
                finite_vector3(center, "center_test")

    def test_complete_analytic_control_and_all_black_rejection(self):
        check_pixels(self.frame,self.masks,False)
        with self.assertRaises(ReadbackError):check_pixels(bytes(FRAME_BYTES),self.masks,False)

    def test_wrong_z_and_wrong_color_cannot_pass(self):
        for label in (0,2):
            frame=bytearray(self.frame)
            for y in range(150,180):
                for x in range(255,278):
                    offset=(y*WIDTH+x)*3;frame[offset:offset+3]=bytes(COLORS[label])
            with self.assertRaises(ReadbackError):check_pixels(frame,self.masks,False)

    def test_unedited_source_does_not_pass_edit_oracle(self):
        with self.assertRaises(ReadbackError):check_pixels(self.frame,expected_masks(True),True)

    def test_rational_wire_rejects_numbers_and_duplicate_json(self):
        self.assertEqual(rational({"num":"29","den":"25"}),Fraction(29,25))
        for value in ({"num":29,"den":25},{"num":"2","den":"2"},{"num":"01","den":"1"}):
            with self.assertRaises(ReadbackError):rational(value)
        with self.assertRaises(ReadbackError):parse_json(b'{"a":1,"a":2}')

    def test_missing_audio_and_pts_negative_cases_are_explicit(self):
        video={"index":0,"codec_type":"video","codec_name":"h264","pix_fmt":"yuv420p","width":640,"height":360,
               "r_frame_rate":"12/1","avg_frame_rate":"12/1","time_base":"1/12","start_pts":0,"duration_ts":24}
        frames=[{"media_type":"video","stream_index":0,"best_effort_timestamp":i,"pts":i} for i in range(24)]
        control={"streams":[video],"frames":frames};inspect_probe(control,12,24)
        with self.assertRaises(ReadbackError):inspect_probe({"streams":[video,{"codec_type":"audio"}],"frames":frames},12,24)
        with self.assertRaises(ReadbackError):inspect_probe({"streams":[video],"frames":frames[:-1]},12,24)
        bad=parse_json(json.dumps(control));bad["frames"][12]["pts"]=11
        with self.assertRaises(ReadbackError):inspect_probe(bad,12,24)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--phase",choices=["original","reopen","edit","edited-reopen","edge25"])
    for name in ("video","producer","source","native-readback","report","frames"):
        parser.add_argument("--"+name,type=Path)
    parser.add_argument("--origin-source", type=Path,
                        help="Optional original selected CAS carrier; without it origin is producer/reference bound, not independently read")
    parser.add_argument("--ffmpeg",default="ffmpeg");parser.add_argument("--ffprobe",default="ffprobe")
    args=parser.parse_args(argv)
    if args.self_test:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReaderTests))
        print(json.dumps({"classification":"SELF_TEST_NO_NATIVE_PROVIDER","passed":result.testsRun-len(result.failures)-len(result.errors),
                          "failed":len(result.failures)+len(result.errors),"native_execution_performed":False}))
        return 0 if result.wasSuccessful() else 1
    require(all(getattr(args,name) is not None for name in ("phase","video","producer","source","native_readback","report")), "missing_reader_arguments")
    require(args.report.parent.is_dir() and not args.report.exists(), "report_destination_must_be_new")
    try:
        report=inspect(args);code=0
    except (ReadbackError,OSError,KeyError,TypeError,ValueError,zipfile.BadZipFile) as error:
        report={"schema":"motionwright.manim.static-source-readback/1","status":"FAIL","phase":args.phase,
                "error":str(error),"native_job_retried":False,"human_acceptance":False};code=1
    with args.report.open("x",encoding="utf-8") as stream:json.dump(report,stream,indent=2,allow_nan=False);stream.write("\n")
    print(json.dumps({"status":report["status"],"report":str(args.report),"native_job_retried":False}))
    return code


if __name__=="__main__":
    raise SystemExit(main())
