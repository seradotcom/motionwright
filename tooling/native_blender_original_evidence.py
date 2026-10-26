"""Custody and read-only checks for the canonical Blender source phases.

The caller uses the existing Broker/Driver Host producer once per explicit
phase. These checks neither launch an editor nor grant interactive consent.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable

import blender_original_scene_contract as contract
from native_mlt_loaded_module import _verified_binary, parse_process_starttime

HEX = re.compile(r"[0-9a-f]{64}\Z")
PHASES = ("create_original", "edit_original", "verify_edited")
LABELS = {phase: phase.replace("_", "-") for phase in PHASES}
MAX_JSON = 64 * 1024 * 1024
MAX_EXE = 768 * 1024 * 1024
MAX_PROC = 64 * 1024
COMMANDS = frozenset("driver.blender." + tail for tail in (
    "scene.inspect", "semantic.objects", "semantic.relations", "semantic.rename",
    "semantic.custom.list", "semantic.custom.set", "semantic.object.create",
    "semantic.datablock.create", "semantic.relation.set", "semantic.relation.unlink",
    "semantic.property.get", "semantic.property.set", "file.save", "file.open",
))
READ_COMMANDS = frozenset("driver.blender." + tail for tail in (
    "scene.inspect", "semantic.objects", "semantic.relations",
    "semantic.custom.list", "semantic.property.get",
))
GEOMETRY_COMMANDS = frozenset("driver.blender." + tail for tail in (
    "collection.create", "object.create", "object.transform", "collection.link",
    "material.create", "material.assign", "export.glb",
))


class OriginalSceneUnverified(ValueError):
    pass


def require(condition: object, message: str) -> None:
    if not condition:
        raise OriginalSceneUnverified(message)


def unique_json(data: bytes) -> dict:
    require(type(data) is bytes and 0 < len(data) <= MAX_JSON,
            "original_scene_JSON_missing_or_unbounded")
    def unique(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, "original_scene_JSON_duplicate_member")
            result[key] = value
        return result
    def invalid_constant(_value: str) -> None:
        raise OriginalSceneUnverified("original_scene_JSON_nonfinite")
    try:
        result = json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise OriginalSceneUnverified("original_scene_JSON_malformed") from error
    require(type(result) is dict, "original_scene_JSON_not_object")
    return result


def canonical_bytes(value: object) -> bytes:
    # For private Python records only. Project/plan source is serialized by Rust.
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def new_bytes(path: Path, body: bytes) -> dict:
    require(type(body) is bytes and len(body) <= 256 * 1024 * 1024,
            "original_scene_output_exceeds_shared_writer_profile")
    from ci_private_evidence import new_bytes as write_retained
    write_retained(path, body)
    return {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}

def new_json(path: Path, value: object) -> dict:
    return new_bytes(path, canonical_bytes(value))


def captured_json(
    argv: list[str], *, env: dict[str, str], prefix: Path,
    record: dict, timeout: int = 900,
) -> dict:
    """One process invocation; retain its terminal before flushing its streams."""
    require("invocation_reserved" not in record, "original_scene_command_already_attempted")
    record["invocation_reserved"] = True
    record["argv"] = argv
    record["terminal"] = {"status": "NOT_STARTED"}
    stdout = stderr = None
    original: BaseException | None = None
    value = None
    secondary: list[str] = []
    try:
        stdout = prefix.with_suffix(".stdout").open("xb")
        os.chmod(prefix.with_suffix(".stdout"), 0o600)
        stderr = prefix.with_suffix(".stderr").open("xb")
        os.chmod(prefix.with_suffix(".stderr"), 0o600)
        try:
            result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            record["terminal"] = {"status": "PROCESS_TIMEOUT", "outcome_known": False}
            raise
        except OSError:
            record["terminal"] = {"status": "PROCESS_CALL_ERROR", "outcome_known": False}
            raise
        record["terminal"] = {"status": "PROCESS_EXIT", "returncode": result.returncode}
        if result.returncode != 0:
            raise OriginalSceneUnverified("original_scene_process_returned_nonzero")
        stdout.flush()
        from mlt_official_runtime_intake import capture_regular
        body, _binding = capture_regular(prefix.with_suffix(".stdout"), MAX_JSON)
        value = unique_json(body)
        record["returned_json"] = value
    except BaseException as error:
        original = error
        record["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        for name, stream in (("stdout", stdout), ("stderr", stderr)):
            if stream is None:
                continue
            for operation, action in (
                ("flush", stream.flush), ("sync", lambda stream=stream: os.fsync(stream.fileno())),
                ("close", stream.close),
            ):
                try:
                    action()
                except BaseException as error:
                    secondary.append(name + ":" + operation + ":" + type(error).__name__ + ":" + str(error))
        record["stream_custody_errors"] = secondary
        try:
            new_json(prefix.with_suffix(".command.json"), record)
        except BaseException as error:
            secondary.append("command_record:" + type(error).__name__ + ":" + str(error))
        if original is not None:
            try:
                original.original_scene_command = record
            except BaseException:
                pass
    if original is not None:
        raise original
    require(not secondary, "original_scene_command_custody_incomplete")
    require(type(value) is dict, "original_scene_command_has_no_retained_JSON")
    return value


def _identity_table(readback: dict, plan: dict, calls: list[dict]) -> dict[tuple[str, str], str]:
    generation = readback.get("semantic_generation")
    bound = contract.check_identity_readback(
        plan, generation, readback.get("inventories", {}), readback.get("custom_lists", {})
    )
    def original_payload(command: str, args: dict, data: dict) -> None:
        matches = [call for call in calls if call["command"] == command
                   and all(call["args"].get(key) == value for key, value in args.items())
                   and call["data"] == data]
        require(bool(matches), "original_scene_readback_not_in_original_calls")
    for root, pages in readback["inventories"].items():
        for page in pages:
            original_payload("driver.blender.semantic.objects",
                             {"root": root, "offset": page["offset"]}, page)
            require(any(call["command"] == "driver.blender.semantic.objects"
                        and call["args"].get("root") == root and "query" not in call["args"]
                        and call["data"] == page for call in calls),
                    "original_scene_inventory_used_filtered_lookup")
    for reference, pages in readback["custom_lists"].items():
        for page in pages:
            original_payload("driver.blender.semantic.custom.list",
                             {"ref": reference, "offset": page["offset"]}, page)
    for reference, getters in readback.get("property_getters", {}).items():
        for data in getters:
            original_payload("driver.blender.semantic.property.get",
                             {"ref": reference, "property": data.get("property")}, data)
    original_payload("driver.blender.scene.inspect", {}, readback.get("active_scene", {}))
    return {(item["root"], item["name"]): item["ref"] for item in bound["entities"]}


def verify_readback(plan: dict, readback: dict, calls: list[dict], edit: dict | None) -> dict:
    require(type(readback) is dict and readback.get("edited") is (edit is not None)
            and readback.get("geometry_content_verified") is False,
            "original_scene_readback_scope_or_edit_differs")
    refs = _identity_table(readback, plan, calls)
    generation = readback["semantic_generation"]
    identity_table = contract.expected_identity_table(plan)
    def properties(reference: str, expected: dict) -> None:
        contract.check_property_getters(reference, expected, readback["property_getters"].get(reference))
    def relation(reference: str, property_name: str) -> list[dict]:
        pages = [{"command": call["command"], "args": call["args"], "data": call["data"]}
                 for call in calls
                 if call["command"] == "driver.blender.semantic.relations"
                 and call["args"].get("ref") == reference
                 and call["args"].get("property") == property_name
                 and call["data"].get("generation") == generation]
        items = contract._pages([page["data"] for page in pages], generation)
        required = {item.get("ref") for item in items}
        contract.check_relation_pages(pages, reference, property_name, generation, required, exact=True)
        return items
    def sole(reference: str, property_name: str, rna_type: str) -> dict:
        items = relation(reference, property_name)
        require(len(items) == 1 and items[0].get("rna_type") == rna_type,
                "original_scene_pointer_type_or_count_differs")
        return items[0]
    def identity(reference: str, root: str, name: str) -> None:
        payloads = [call["data"] for call in calls
                    if call["command"] == "driver.blender.semantic.custom.list"
                    and call["args"].get("ref") == reference
                    and call["data"].get("generation") == generation]
        observed = {}
        keys = set()
        for item in contract._pages(payloads, generation, reference=reference):
            key = item.get("key")
            require(type(key) is str and key not in keys,
                    "original_scene_relation_identity_duplicate_or_missing")
            keys.add(key)
            if key.startswith(contract.PREFIX):
                require(item.get("status") == "managed",
                        "original_scene_relation_identity_unreadable")
                observed[key] = item.get("value")
        require(observed == identity_table.get((root, name)),
                "original_scene_relation_target_UUID_differs")
    def membership(reference: str, property_name: str, root: str, names: set[str]) -> None:
        items = relation(reference, property_name)
        require(len(items) == len(names) and {item.get("name") for item in items} == names,
                "original_scene_membership_differs")
        for item in items:
            identity(item["ref"], root, item["name"])
    objects = plan["contribution"]["objects"]
    for obj in objects:
        location = list(obj["location"])
        if edit and obj["node_id"] == edit["node_id"]:
            location[0] += edit["translation_x_delta"]
        object_ref = refs[("objects", obj["name"])]
        properties(object_ref, {"location": location, "rotation_mode": "XYZ",
                                "rotation_euler": obj["rotation"], "scale": obj["scale"],
                                "hide_render": False})
        material_ref = refs[("materials", obj["material_name"])]
        properties(material_ref, {"diffuse_color": obj["color_rgba"], "roughness": 0.45,
                                  "metallic": 0.0, "use_nodes": True})
        mesh = sole(object_ref, "data", "Mesh")
        material = sole(mesh["ref"], "materials", "Material")
        require(material.get("name") == obj["material_name"],
                "original_scene_material_relation_name_differs")
        identity(material["ref"], "materials", obj["material_name"])
    camera = plan["camera"]
    expected_camera = contract.expected_camera_properties(plan)
    camera_object = refs[("objects", camera["object_name"])]
    camera_data = refs[("cameras", camera["data_name"])]
    properties(camera_object, expected_camera["camera_object"])
    properties(camera_data, expected_camera["camera_data"])
    linked_camera_data = sole(camera_object, "data", "Camera")
    identity(linked_camera_data["ref"], "cameras", camera["data_name"])
    scene_ref = refs[("scenes", plan["native_scene_name"])]
    selected_camera = sole(scene_ref, "camera", "Object")
    identity(selected_camera["ref"], "objects", camera["object_name"])
    render = sole(scene_ref, "render", "RenderSettings")
    properties(render["ref"], expected_camera["render_settings"])
    geometry_names = {obj["name"] for obj in objects}
    membership(scene_ref, "objects", "objects", geometry_names | {camera["object_name"]})
    collection_name = plan["contribution"]["collection_name"]
    membership(refs[("collections", collection_name)], "objects", "objects", geometry_names)
    master = sole(scene_ref, "collection", "Collection")
    membership(master["ref"], "children", "collections", {collection_name})
    membership(master["ref"], "objects", "objects", {camera["object_name"]})
    active = readback["active_scene"]
    require(active.get("name") == plan["native_scene_name"]
            and active.get("camera") == camera["object_name"]
            and type(active.get("object_count")) is int
            and active["object_count"] == len(objects) + 1,
            "original_scene_active_Scene_or_camera_differs")
    return {"source_UUIDs_bound": True, "native_camera_getters_bound": True,
            "scene_membership_bound": True, "edited": edit is not None,
            "geometry_content_verified": False, "rendered_camera_or_human_acceptance": False}


def _require_empty_private_append_custody(persisted: dict) -> None:
    """A private in-memory copy never substitutes for completed SQLite receipts."""
    custody = persisted.get("coordinator_append_custody")
    keys = {
        "schema", "durable", "mutex_poisoned", "reserved_slots",
        "reserved_serialized_bytes", "slot_limit", "serialized_byte_limit",
        "pending_without_terminal", "entries",
    }
    require(type(custody) is dict and set(custody) == keys,
            "original_scene_private_append_custody_shape")
    require(custody["schema"] == "motionwright.production.private-receipt-custody/1"
            and custody["durable"] is False and custody["mutex_poisoned"] is False,
            "original_scene_private_append_custody_authority")
    require(all(type(custody[key]) is int and custody[key] == 0 for key in (
        "reserved_slots", "reserved_serialized_bytes", "pending_without_terminal",
    )) and custody["entries"] == [],
            "original_scene_private_append_custody_unconfirmed")
    require(type(custody["slot_limit"]) is int and custody["slot_limit"] == 8
            and type(custody["serialized_byte_limit"]) is int
            and custody["serialized_byte_limit"] == 32 * 1024 * 1024,
            "original_scene_private_append_custody_budget")


def verify_phase(
    plan: dict, source_bytes: bytes, attempt: dict, returned: dict,
    persisted: dict, input_before: dict | None, input_after: dict | None,
    saved_output: dict | None,
) -> dict:
    contract.check_project_source(plan, source_bytes)
    phase = attempt.get("phase")
    require(phase in PHASES, "original_scene_phase_invalid")
    authority = plan["contribution"]
    stamp = attempt.get("expected", {})
    require(attempt.get("project_id") == authority["project_id"]
            and attempt.get("scene_id") == authority["scene_id"]
            and type(stamp) is dict and set(stamp) == {"resource", "generation", "revision"}
            and type(stamp.get("resource")) is str and bool(stamp["resource"])
            and stamp.get("generation") == authority["generation"]
            and type(stamp.get("revision")) is int and stamp["revision"] == authority["revision"],
            "original_scene_attempt_source_authority_differs")
    _require_empty_private_append_custody(persisted)
    expected_return = dict(persisted)
    del expected_return["coordinator_append_custody"]
    expected_return["phase_contract_verified"] = True
    require(persisted.get("phase_contract_verified") is False and returned == expected_return,
            "original_scene_publication_ack_not_bound_to_original_snapshot")
    require(returned.get("schema") == "motionwright.blender.original-scene-phase-evidence/1"
            and returned.get("request") == attempt
            and returned.get("custody_errors") == []
            and returned.get("geometry_content_verified") is False
            and returned.get("human_acceptance") is False,
            "original_scene_phase_envelope_or_custody_differs")
    terminal = returned.get("native_terminal", {})
    require(terminal.get("status") == "ok" and type(terminal.get("value")) is dict,
            "original_scene_native_terminal_not_success")
    result = terminal["value"]
    require(result.get("schema") == "motionwright.blender.original-scene-attempt/1"
            and result.get("phase") == phase and result.get("source") == attempt.get("source")
            and result.get("edit") == attempt.get("edit")
            and result.get("project_json_sha256") == plan["project_json_sha256"]
            and result.get("coordinator_outcomes_retained") is True
            and result.get("broker_terminal_custody_requires_completed_receipts") is True
            and result.get("geometry_content_verified") is False
            and result.get("visual_acceptance") is False,
            "original_scene_coordinator_result_scope_differs")
    custody = returned.get("custody", {})
    require(custody.get("sqlite_reopened") is True
            and custody.get("project_unchanged") is True
            and custody.get("project") == unique_json(source_bytes),
            "original_scene_SQLite_reopen_did_not_preserve_Project")
    production = custody.get("production", {})
    receipts = production.get("receipts")
    watermark = production.get("watermark", {})
    require(production.get("complete") is True and type(receipts) is list
            and type(watermark.get("count")) is int and watermark["count"] == len(receipts)
            and 0 < len(receipts) <= 20_000,
            "original_scene_receipt_snapshot_incomplete")
    by_id = {}
    for receipt in receipts:
        require(type(receipt) is dict and type(receipt.get("id")) is str
                and receipt["id"] not in by_id, "original_scene_receipt_id_missing_or_duplicate")
        by_id[receipt["id"]] = receipt
    bound_provider_generation = None
    broker_ids = set()
    source = plan["contribution"]
    def broker_reply(value: dict, command: str, request_id: str) -> dict:
        nonlocal bound_provider_generation
        require(type(value) is dict and value.get("replayed") is False,
                "original_scene_reused_a_prior_context_reply")
        receipt = value.get("receipt", {})
        require(by_id.get(receipt.get("id")) == receipt
                and receipt.get("project_id") == source["project_id"]
                and receipt.get("generation") == source["generation"]
                and receipt.get("revision") == source["revision"]
                and receipt.get("request_id") == request_id
                and receipt.get("command") == command
                and receipt.get("stage") == "completed"
                and type(receipt.get("request_sha256")) is str
                and HEX.fullmatch(receipt["request_sha256"]),
                "original_scene_Broker_reply_not_bound_to_original_receipt")
        payload = receipt.get("payload", {})
        broker_id = payload.get("broker_request_id")
        require(type(broker_id) is str and broker_id and broker_id not in broker_ids,
                "original_scene_Broker_request_identity_missing_or_duplicate")
        broker_ids.add(broker_id)
        response = value.get("result")
        require(type(response) is dict and response == payload.get("result")
                and response.get("provenance_checked") is True,
                "original_scene_Broker_result_differs_from_receipt")
        execution = response.get("execution", {})
        provenance = execution.get("provenance", {})
        generation = provenance.get("provider_generation")
        require(type(execution.get("backend")) is str and bool(execution["backend"])
                and execution.get("dry_run") is False
                and provenance.get("provider") == "driver:blender"
                and provenance.get("source") == "driver"
                and provenance.get("execution_provider") == "driver:blender"
                and provenance.get("untrusted_metadata") is False
                and type(provenance.get("descriptor_sha256")) is str
                and HEX.fullmatch(provenance["descriptor_sha256"])
                and type(generation) is int and generation > 0,
                "original_scene_original_native_authority_missing")
        if bound_provider_generation is None:
            bound_provider_generation = generation
        require(generation == bound_provider_generation,
                "original_scene_provider_generation_changed")
        data = response.get("data")
        require(type(data) is dict, "original_scene_Broker_data_missing")
        return data
    journal = returned.get("journal", {})
    require(journal.get("schema") == "motionwright.blender.original-scene-attempt/1"
            and journal.get("phase") == LABELS[phase] and journal.get("plan") == plan
            and journal.get("geometry_error") is None
            and type(journal.get("calls")) is list and 0 < len(journal["calls"]) <= 8192,
            "original_scene_original_journal_missing_or_foreign")
    calls = []
    for sequence, call in enumerate(journal["calls"], 1):
        command = call.get("command")
        request_id = attempt["request_id"] + ":" + LABELS[phase] + ":" + f"{sequence:06}"
        require(command in COMMANDS and type(call.get("sequence")) is int
                 and call["sequence"] == sequence
                and call.get("request_id") == request_id and type(call.get("args")) is dict
                and call.get("mutation") is (command not in READ_COMMANDS) and call.get("native_error") is None,
                "original_scene_journal_call_order_or_identity_differs")
        data = broker_reply(call.get("native_result"), command, request_id)
        calls.append({"command": command, "args": call["args"], "data": data})
    geometry = journal.get("geometry_result")
    if phase == "create_original":
        require(type(geometry) is dict and geometry.get("renderer") == "blender"
                and geometry.get("native_driver") == "driver:blender"
                and geometry.get("scene_id") == source["scene_id"]
                and geometry.get("project_revision") == source["revision"],
                "original_scene_canonical_geometry_result_missing")
        prefix = attempt["request_id"] + ":geometry:"
        broker_reply(geometry.get("collection"), "driver.blender.collection.create", prefix + "collection")
        require(type(geometry.get("objects")) is list and len(geometry["objects"]) == len(source["objects"]),
                "original_scene_geometry_object_count_differs")
        for index, (native, expected) in enumerate(zip(geometry["objects"], source["objects"])):
            require(native.get("node_id") == expected["node_id"],
                    "original_scene_geometry_source_node_differs")
            for key, command, suffix in (
                ("object", "object.create", "create"), ("transform", "object.transform", "transform"),
                ("collection_link", "collection.link", "link"), ("material", "material.create", "material"),
                ("material_assignment", "material.assign", "material-assign"),
            ):
                broker_reply(native.get(key), "driver.blender." + command,
                             prefix + "object:" + str(index) + ":" + suffix)
        broker_reply(geometry.get("export"), "driver.blender.export.glb", prefix + "export")
        require(attempt.get("source") is None and attempt.get("edit") is None
                and result.get("before") is None and input_before is None and input_after is None,
                "original_scene_creation_consumed_an_implicit_source")
    else:
        require(geometry is None, "original_scene_reopen_recreated_geometry")
        edit = attempt.get("edit")
        require(type(edit) is dict and set(edit) == {"node_id", "translation_x_delta"}
                and edit["node_id"] in {obj["node_id"] for obj in source["objects"]}
                and type(edit["translation_x_delta"]) in (int, float)
                and not isinstance(edit["translation_x_delta"], bool)
                and 0 < abs(edit["translation_x_delta"]) <= 1.0,
                "original_scene_edit_target_or_delta_not_explicit")
        opens = [call for call in calls if call["command"] == "driver.blender.file.open"]
        require(len(opens) == 1 and calls[0] == opens[0]
                and opens[0]["args"] == {"path": attempt["source"]["relative_path"]}
                and input_before == input_after == attempt["source"],
                "original_scene_reopen_not_bound_to_exact_retained_source")
        contract.check_file_open_response(attempt["source"]["relative_path"],
                                          opens[0]["data"], input_before, input_after)
        verify_readback(plan, result.get("before"), calls, edit if phase == "verify_edited" else None)
    opened = [call for call in calls if call["command"] == "driver.blender.file.open"]
    saves = [call for call in calls if call["command"] == "driver.blender.file.save"]
    if phase == "create_original":
        require(not opened, "original_scene_create_opened_a_foreign_file")
    if phase == "verify_edited":
        require(not saves and saved_output is None and result.get("output") is None,
                "original_scene_verify_performed_a_save")
    else:
        name = "blender-original.blend" if phase == "create_original" else "blender-edited.blend"
        require(len(saves) == 1 and saves[0]["args"] == {"path": name}
                and saves[0]["data"] == {"changed": True, "path": name, "copy": True}
                and saved_output == result.get("output")
                and saved_output.get("relative_path") == name,
                "original_scene_saved_file_not_bound_to_native_save")
    checked = verify_readback(plan, result.get("readback"), calls,
                              attempt.get("edit") if phase != "create_original" else None)
    return {**checked, "phase": phase, "original_calls": len(calls),
            "provider_generation": bound_provider_generation,
            "complete_receipt_count": len(receipts), "saved_output": saved_output,
            "coordinator_outcomes_retained": True,
            "unpersisted_Broker_terminal_not_claimed": True}

def bounded_proc(path: Path, limit: int = MAX_PROC) -> bytes:
    with path.open("rb") as stream:
        body = stream.read(limit + 1)
    require(0 < len(body) <= limit, "original_scene_live_process_record_unbounded")
    return body


def process_identity(pid: int, proc_root: Path = Path("/proc")) -> dict:
    require(type(pid) is int and 1 < pid < 2**30, "original_scene_live_PID_invalid")
    raw = bounded_proc(proc_root / str(pid) / "stat", 8192)
    try:
        prefix, tail = raw.decode("ascii").strip().rsplit(") ", 1)
        pid_part, comm = prefix.split(" (", 1)
        words = tail.split()
        require(pid_part == str(pid) and comm and len(words) >= 20
                and words[0] not in ("Z", "X"), "original_scene_process_not_live")
        ppid = int(words[1])
        ticks = parse_process_starttime(raw)
    except (UnicodeError, ValueError, IndexError) as error:
        raise OriginalSceneUnverified("original_scene_process_stat_malformed") from error
    return {"pid": pid, "parent_pid": ppid, "start_ticks": ticks, "comm": comm}


def ancestry(pid: int, daemon: dict, proc_root: Path = Path("/proc")) -> list[dict]:
    observed = []
    visited = set()
    current = pid
    for _ in range(64):
        require(current not in visited, "original_scene_process_ancestry_cycle")
        visited.add(current)
        identity = process_identity(current, proc_root)
        observed.append(identity)
        if current == daemon["pid"]:
            require(identity["start_ticks"] == daemon["start_ticks"],
                    "original_scene_Broker_PID_was_reused")
            return observed
        current = identity["parent_pid"]
        require(current > 1, "original_scene_process_not_descended_from_original_Broker")
    raise OriginalSceneUnverified("original_scene_process_ancestry_exceeded_bound")


def check_native_argv(body: bytes) -> tuple[list[str], str]:
    require(0 < len(body) <= 16_384 and body.endswith(b"\0"),
            "original_scene_native_argv_unbounded")
    try:
        argv = [part.decode("utf-8") for part in body[:-1].split(b"\0")]
    except UnicodeError as error:
        raise OriginalSceneUnverified("original_scene_native_argv_not_UTF8") from error
    require(len(argv) == 16 and Path(argv[0]).name == "blender"
            and argv[1:7] == ["--background", "--factory-startup", "--disable-autoexec",
                              "--offline-mode", "-q", "--log-file"]
            and argv[8:11] == ["--python-exit-code", "1", "--python"]
            and argv[12] == "--", "original_scene_native_launch_profile_differs")
    require(all(value.startswith("/") and len(value) <= 4096
                and not any(part in (".", "..") for part in Path(value).parts)
                and all(ord(char) >= 32 and ord(char) != 127 for char in value)
                for value in (argv[7], argv[11], argv[13], argv[14], argv[15])),
            "original_scene_native_session_arguments_missing")
    return argv, argv[14]


def inspect_live_blender(
    pid: int, daemon: dict, binary: Path, expected_sha: str, workspace: Path,
    *, proc_root: Path = Path("/proc"),
) -> dict:
    require(sys.platform == "linux" and os.geteuid() != 0,
            "original_scene_native_observation_requires_unprivileged_Linux")
    proc = proc_root / str(pid)
    chain = ancestry(pid, daemon, proc_root)
    require(chain[0]["comm"] == "blender", "original_scene_live_process_not_Blender")
    with _verified_binary(binary, expected_sha, MAX_EXE) as source:
        actual_exe = (proc / "exe").stat()
        original_exe = source["stat"]
        require((actual_exe.st_dev, actual_exe.st_ino)
                == (original_exe.st_dev, original_exe.st_ino),
                "original_scene_process_executable_inode_differs")
        status = bounded_proc(proc / "status").decode("ascii")
        uids = [line.split()[1:] for line in status.splitlines() if line.startswith("Uid:")]
        require(len(uids) == 1 and len(uids[0]) == 4
                and all(int(value) == os.geteuid() for value in uids[0]),
                "original_scene_native_process_owner_differs")
        argv, mapped_workspace = check_native_argv(bounded_proc(proc / "cmdline", 16_384))
        original_workspace = workspace.stat()
        native_workspace = (proc / "root" / mapped_workspace.lstrip("/")).stat()
        require(stat.S_ISDIR(original_workspace.st_mode)
                and (original_workspace.st_dev, original_workspace.st_ino)
                == (native_workspace.st_dev, native_workspace.st_ino),
                "original_scene_native_workspace_is_not_owner_workspace")
        after_chain = ancestry(pid, daemon, proc_root)
        after_exe = (proc / "exe").stat()
        require(after_chain == chain and (after_exe.st_dev, after_exe.st_ino)
                == (original_exe.st_dev, original_exe.st_ino),
                "original_scene_process_identity_changed_during_observation")
        return {
            "schema": "motionwright.blender.original-live-process/1",
            "pid": pid, "start_ticks": chain[0]["start_ticks"],
            "daemon_pid": daemon["pid"], "daemon_start_ticks": daemon["start_ticks"],
            "ancestry": chain, "blender_sha256": source["sha256"],
            "executable_device": original_exe.st_dev, "executable_inode": original_exe.st_ino,
            "workspace_device": original_workspace.st_dev,
            "workspace_inode": original_workspace.st_ino,
            "native_argv": argv, "native_uid": os.geteuid(),
            "native_file_open_or_save_proven_by_this_snapshot": False,
            "human_acceptance": False,
        }


class LiveBlenderObserver:
    """Read /proc while the existing producer runs; never starts/retries it."""
    def __init__(self, daemon_pid: int, binary: Path, expected_sha: str, workspace: Path):
        require(sys.platform == "linux" and os.geteuid() != 0,
                "original_scene_live_observer_requires_unprivileged_Linux")
        self.daemon = process_identity(daemon_pid)
        self.binary, self.expected_sha, self.workspace = binary, expected_sha, workspace
        self.stop_event, self.ready = threading.Event(), threading.Event()
        self.thread = None
        self.first = self.last = None
        self.errors: list[dict] = []
        self.error_count = 0
        self.scans = 0

    def _error(self, error: BaseException) -> None:
        self.error_count += 1
        if len(self.errors) < 16:
            self.errors.append({"type": type(error).__name__, "message": str(error)})

    def _scan(self) -> None:
        require(process_identity(self.daemon["pid"])["start_ticks"] == self.daemon["start_ticks"],
                "original_scene_Broker_identity_changed")
        entries = list(Path("/proc").iterdir())
        require(len(entries) <= 131_072, "original_scene_process_inventory_exceeded_bound")
        selected = []
        for entry in entries:
            if not entry.name.isdecimal() or int(entry.name) <= 1:
                continue
            try:
                identity = process_identity(int(entry.name))
                if identity["comm"] != "blender":
                    continue
                chain = ancestry(identity["pid"], self.daemon)
            except (FileNotFoundError, ProcessLookupError, PermissionError, OriginalSceneUnverified):
                continue
            selected.append(chain[0])
        require(len(selected) <= 1, "original_scene_multiple_native_processes_in_one_context")
        self.scans += 1
        if not selected:
            if self.first is not None:
                raise OriginalSceneUnverified("original_scene_native_process_disappeared")
            return
        selected_identity = (selected[0]["pid"], selected[0]["start_ticks"])
        if self.first is None:
            self.first = inspect_live_blender(selected[0]["pid"], self.daemon,
                                              self.binary, self.expected_sha, self.workspace)
        else:
            require(selected_identity == (self.first["pid"], self.first["start_ticks"]),
                    "original_scene_native_process_was_replaced")
        self.last = {"pid": selected[0]["pid"], "start_ticks": selected[0]["start_ticks"],
                     "scan": self.scans}

    def start(self) -> None:
        require(self.thread is None, "original_scene_observer_already_started")
        def run() -> None:
            self.ready.set()
            while not self.stop_event.is_set():
                try:
                    self._scan()
                except BaseException as error:
                    self._error(error)
                    return
                self.stop_event.wait(0.1)
        self.thread = threading.Thread(target=run, name="blender-source-observer", daemon=True)
        self.thread.start()
        require(self.ready.wait(2), "original_scene_observer_did_not_start")

    def finish(self) -> dict:
        require(self.thread is not None, "original_scene_observer_was_not_started")
        self.stop_event.set()
        self.thread.join(timeout=5)
        require(not self.thread.is_alive(), "original_scene_observer_did_not_stop")
        if self.first is not None:
            try:
                final = inspect_live_blender(self.first["pid"], self.daemon,
                                              self.binary, self.expected_sha, self.workspace)
                require(final == self.first, "original_scene_live_identity_changed_at_terminal")
            except BaseException as error:
                self._error(error)
        return {"watcher_started_before_phase_invocation": True, "scans": self.scans,
                "first": self.first, "last": self.last, "errors": self.errors,
                "error_count": self.error_count,
                "complete": self.first is not None and self.last is not None and self.error_count == 0}



@contextmanager
def live_observation_scope(observer: Any, phase_record: dict,
                           custody: Any, phase: str):
    """Finish the one observer before the enclosing native context is closed."""
    attempted = False
    def finish() -> dict:
        nonlocal attempted
        require(not attempted, "original_scene_observer_finish_already_attempted")
        attempted = True
        observation = observer.finish()
        phase_record["observer"] = observation
        return observation
    try:
        observer.start()
        yield finish
    finally:
        original = sys.exc_info()[1]
        if not attempted:
            try:
                finish()
            except BaseException as error:
                custody.secondary(phase + ":live-observation", error)
                if original is None:
                    raise


class OriginalCustody:
    """Keep original results before all later checks and sealed publication."""
    def __init__(self, owner: Path, public: Path, recipient: dict, context: dict,
                 *, profile: str = "original-scene"):
        require(profile in ("original-scene", "product-scene"), "original_scene_profile")
        self.profile = profile
        from ci_private_evidence import public_key, validate_context
        public_key(recipient)
        validate_context(context)
        require(owner.is_absolute() and public.is_absolute()
                and owner != public and owner not in public.parents and public not in owner.parents,
                "original_scene_custody_roots_overlap")
        self.owner, self.public, self.recipient, self.context = owner, public, recipient, context
        self.record = {
            "schema": "motionwright.blender.original-scene-custody/1",
            "context": context, "attempts": {}, "secondary_errors": [],
            "scope": "static-Scene-UUID-camera-save-edit-reopen",
            "mesh_content_or_rendered_camera_verified": False, "human_acceptance": False,
        }
        if profile == "product-scene":
            self.record.update(schema="motionwright.blender.product-scene-custody/1",
                               scope=PRODUCT_SCOPE, source_profile=PRODUCT_PROFILE)
        self.sealed = None

    def once(self, phase: str, effect: Callable[[], dict], verify: Callable[[dict], dict]) -> dict:
        require(phase in PHASES and phase not in self.record["attempts"],
                "original_scene_phase_already_attempted")
        item = {"invocation_reserved": True, "native_result": None, "native_error": None,
                "verification": None}
        self.record["attempts"][phase] = item
        try:
            result = effect()
        except BaseException as error:
            item["native_error"] = {"type": type(error).__name__, "message": str(error)}
            raise
        item["native_result"] = result
        item["verification"] = verify(result)
        return item["verification"]

    def secondary(self, stage: str, error: BaseException) -> None:
        self.record["secondary_errors"].append({
            "stage": stage, "type": type(error).__name__, "message": str(error),
            "automatic_retry": False,
        })

    def seal(self) -> dict:
        from ci_private_evidence import encrypt_snapshot, gather_snapshot
        require(self.sealed is None, "original_scene_sealing_already_attempted")
        self.sealed = {"status": "ATTEMPTED"}
        complete = True
        try:
            new_json(self.owner / "original-custody.json", self.record)
        except BaseException as error:
            self.secondary("controller-record", error)
            complete = False
        try:
            snapshot, captured = gather_snapshot({"owner": self.owner}, preserve_partial=True)
            complete = complete and captured
        except BaseException as error:
            self.secondary("snapshot", error)
            complete = False
            import io
            import tarfile
            empty = io.BytesIO()
            with tarfile.open(fileobj=empty, mode="w:"):
                pass
            snapshot = empty.getvalue()
        snapshot = append_memory_record(snapshot, self.record)
        sealed = encrypt_snapshot(snapshot, self.recipient, self.context)
        binding = new_bytes(self.public / "private-evidence.mwseal", sealed)
        self.sealed = {"status": "SEALED", **binding, "selected_originals_complete": complete}
        return self.sealed

    def public_summary(self, original_error: BaseException | None) -> dict:
        if self.profile == "product-scene":
            return product_public_summary(self, original_error)
        verified = (original_error is None and not self.record["secondary_errors"]
                    and set(self.record["attempts"]) == set(PHASES)
                    and all(item["verification"] is not None for item in self.record["attempts"].values())
                    and self.sealed is not None and self.sealed.get("status") == "SEALED"
                    and self.sealed.get("selected_originals_complete") is True)
        return {
            "schema": "motionwright.blender.original-scene-public/1",
            "status": "PASS" if verified else "UNVERIFIED",
            "scope": "static-Scene-UUID-camera-save-edit-reopen",
            "source_sha": self.context["source_sha"], "semwright_sha": self.context["semwright_sha"],
            "run_id": self.context["run_id"], "run_attempt": self.context["run_attempt"],
            "phases_attempted": len(self.record["attempts"]),
            "phases_verified": sum(item["verification"] is not None for item in self.record["attempts"].values()),
            "sealed_originals": self.sealed if self.sealed and self.sealed.get("status") == "SEALED" else None,
            "geometry_content_verified": False, "rendered_camera_verified": False,
            "human_acceptance": False, "automatic_native_retry": False,
        }

def append_memory_record(snapshot: bytes, value: dict) -> bytes:
    """Add controller memory without rereading or retrying any original file."""
    import io
    import tarfile
    body = canonical_bytes(value)
    require(len(body) <= MAX_JSON, "original_scene_memory_record_exceeded_bound")
    original = io.BytesIO(snapshot)
    output = io.BytesIO()
    with tarfile.open(fileobj=original, mode="r:") as source, \
            tarfile.open(fileobj=output, mode="w:") as target:
        members = source.getmembers()
        require(len(members) <= 4097 and all(member.isfile() for member in members)
                and all(member.name != "controller-memory.json" for member in members),
                "original_scene_shared_snapshot_contract_differs")
        for member in members:
            stream = source.extractfile(member)
            require(stream is not None, "original_scene_snapshot_member_unreadable")
            target.addfile(member, stream)
        member = tarfile.TarInfo("controller-memory.json")
        member.mode, member.mtime, member.size = 0o600, 0, len(body)
        target.addfile(member, io.BytesIO(body))
    data = output.getvalue()
    require(len(data) <= 256 * 1024 * 1024 - 16_384,
            "original_scene_snapshot_exceeds_shared_sealed_output_profile")
    return data


def same_original_chain(observations: list[dict]) -> dict:
    require(len(observations) == 3, "original_scene_three_fresh_contexts_not_observed")
    native = [item.get("first") for item in observations]
    require(all(item.get("complete") is True for item in observations)
            and all(type(item) is dict for item in native),
            "original_scene_live_context_observation_incomplete")
    identities = [(item["pid"], item["start_ticks"]) for item in native]
    brokers = [(item["daemon_pid"], item["daemon_start_ticks"]) for item in native]
    require(len(set(identities)) == 3 and len(set(brokers)) == 3
            and native[0]["start_ticks"] < native[1]["start_ticks"] < native[2]["start_ticks"],
            "original_scene_reopen_did_not_use_newer_distinct_native_contexts")
    require(len({item["blender_sha256"] for item in native}) == 1
            and len({(item["workspace_device"], item["workspace_inode"]) for item in native}) == 3,
            "original_scene_reopen_binary_or_workspace_binding_differs")
    return {"three_distinct_live_native_contexts": True,
            "same_native_binary": True, "source_file_custody_checked_separately": True,
            "human_acceptance": False}


def _plain_owned_directory(path: Path) -> None:
    require(path.is_absolute() and path.resolve(strict=True) == path
            and not any(parent.is_symlink() for parent in path.parents),
            "original_scene_owner_directory_not_canonical")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700,
            "original_scene_owner_directory_not_private")


def new_original_runtime_paths(owner: Path) -> dict[str, Path]:
    require(owner.is_absolute() and owner.resolve(strict=True) == owner and owner.is_dir(),
            "original_scene_runtime_owner_not_canonical")
    runtime_root = owner / "r"
    runtime_root.mkdir(mode=0o700, exist_ok=False)
    # Seed plus the existing three native phases; these paths are not adopted.
    return {phase: runtime_root / str(index)
            for index, phase in enumerate(("seed-context", *PHASES))}


def run_original(app: Any) -> None:
    """Use three fresh contexts of the one canonical producer, without replay."""
    from mlt_official_runtime_intake import capture_regular
    import traceback
    require(sys.platform == "linux" and os.geteuid() != 0
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("CI") == "true" and os.environ.get("RUNNER_OS") == "Linux",
            "original_scene_requires_selected_unprivileged_Linux_GHA")
    context = {"source_sha": app.GITHUB_SHA, "semwright_sha": app.PIN,
               "run_id": os.environ.get("GITHUB_RUN_ID", ""),
               "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "")}
    require(os.environ.get("MOTIONWRIGHT_EXPECTED_SOURCE_SHA") == app.GITHUB_SHA
            and context["run_attempt"] == "1",
            "original_scene_requires_exact_source_and_a_new_explicit_dispatch")
    recipient_raw = os.environ.get("MOTIONWRIGHT_PRIVATE_EVIDENCE_RECIPIENT", "")
    require(0 < len(recipient_raw) <= 4096, "original_scene_recovery_recipient_missing")
    recipient = unique_json(recipient_raw.encode("utf-8"))
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    owner = runner_temp / ("motionwright-blender-original-" + context["run_id"])
    public = app.ROOT / "verification" / "native-blender-original-public"
    public.parent.mkdir(parents=True, exist_ok=True)
    owner.mkdir(mode=0o700, exist_ok=False)
    public.mkdir(mode=0o700, exist_ok=False)
    _plain_owned_directory(owner)
    _plain_owned_directory(public)
    custody = OriginalCustody(owner, public, recipient, context)
    original_error = None
    try:
        observed_source = subprocess.check_output(["git", "-C", str(app.ROOT), "rev-parse", "HEAD"],
                                                  text=True).strip()
        require(observed_source == app.GITHUB_SHA, "original_scene_checkout_differs")
        custody.record["preflight"] = app.exact_inputs()
        lock_bytes, lock_binding = capture_regular(app.ROOT / "SOURCE_LOCK.json", 1024 * 1024)
        new_bytes(owner / "SOURCE_LOCK.json", lock_bytes)
        custody.record["source_lock"] = lock_binding
        custody.record["example_binary"] = {
            "sha256": app.digest(app.MW_BIN), "path": str(app.MW_BIN),
            "compiler_execution_requires_original_GHA_evidence": True,
        }
        runtime_paths = new_original_runtime_paths(owner)
        seed_root = owner / "seed-context"
        seed_root.mkdir(mode=0o700)
        seed_paths = app.new_owner_paths(seed_root, runtime_path=runtime_paths["seed-context"])
        seed_env = app.owned_environment(seed_paths)
        seed_output = owner / "seed"
        seed_output.mkdir(mode=0o700)
        database = owner / "motionwright.sqlite3"
        seed_command: dict = {}
        custody.record["seed_command"] = seed_command
        seeded = captured_json(
            [str(app.MW_BIN), "seed-original", str(database), str(seed_output)],
            env=seed_env, prefix=owner / "seed", record=seed_command, timeout=60,
        )
        seeded_bytes, _ = capture_regular(seed_output / "seed.json", MAX_JSON)
        source_bytes, source_binding = capture_regular(seed_output / "source-project.json", MAX_JSON)
        plan_bytes, plan_binding = capture_regular(seed_output / "source-plan.json", MAX_JSON)
        plan = unique_json(plan_bytes)
        require(unique_json(seeded_bytes) == seeded and seeded.get("fixture_kind") == "synthetic"
                and seeded.get("native_execution") == "NOT_RUN"
                and seeded.get("mesh_count") == 2 and seeded.get("sqlite_reopened") is True
                and seeded.get("project_json", {}).get("sha256") == source_binding["sha256"],
                "original_scene_seed_not_bound_to_reopened_canonical_source")
        contract.check_project_source(plan, source_bytes)
        custody.record["source_inputs"] = {"project": source_binding, "plan": plan_binding,
                                           "seeded": seeded}
        phase_contexts: list[dict] = []
        custody.record["phase_contexts"] = phase_contexts
        previous_output = None
        observations = []
        for phase in PHASES:
            phase_root = owner / phase
            phase_root.mkdir(mode=0o700)
            root = phase_root / "context"
            root.mkdir(mode=0o700)
            paths = app.new_owner_paths(root, runtime_path=runtime_paths[phase])
            env = app.owned_environment(paths)
            evidence = phase_root / "evidence"
            evidence.mkdir(mode=0o700)
            request = {
                "project_id": seeded["project_id"], "expected": seeded["expected"],
                "scene_id": seeded["scene_id"],
                "request_id": "blender-source-" + context["run_id"] + "-" + context["run_attempt"],
                "phase": phase, "source": None, "edit": None,
            }
            input_before = None
            if phase != "create_original":
                require(previous_output is not None, "original_scene_prior_source_not_verified")
                prior_workspace, binding = previous_output
                observed = contract.inspect_source_file(
                    prior_workspace, binding["relative_path"],
                    expected_sha256=binding["sha256"], expected_bytes=binding["bytes"],
                )
                copied, _ = capture_regular(prior_workspace / binding["relative_path"], 256 * 1024 * 1024)
                require(hashlib.sha256(copied).hexdigest() == observed["sha256"]
                        and len(copied) == observed["bytes"],
                        "original_scene_retained_source_changed_before_copy")
                new_bytes(paths["workspace"] / binding["relative_path"], copied)
                input_before = contract.inspect_source_file(
                    paths["workspace"], binding["relative_path"],
                    expected_sha256=binding["sha256"], expected_bytes=binding["bytes"],
                )
                request["source"] = input_before
                request["edit"] = {"node_id": seeded["edit_node_id"], "translation_x_delta": 0.25}
            request_path = evidence / "attempt.json"
            new_json(request_path, request)
            phase_record: dict = {"phase": phase, "attempt": request, "command": {},
                                  "input_before": input_before, "observer": None,
                                  "context_cleanup": None}
            phase_contexts.append(phase_record)
            native_context = None
            observer = None
            original_phase_error = None
            try:
                with app.blender_context(paths, env, evidence, seeded,
                                         required=set(COMMANDS | GEOMETRY_COMMANDS)) as native_context:
                    phase_record["runtime_bindings"] = native_context["runtime_bindings"]
                    observer = LiveBlenderObserver(
                        native_context["daemon"].pid, app.BLENDER_BIN.resolve(strict=True),
                        native_context["runtime_bindings"]["blender_sha256"], paths["workspace"],
                    )
                    with live_observation_scope(observer, phase_record, custody, phase) as finish_observation:
                        def effect() -> dict:
                            return captured_json(
                                [str(app.MW_BIN), "original", str(database),
                                 str(native_context["connection"]), str(request_path),
                                 str(evidence / "phase-evidence.json")],
                                env=env, prefix=evidence / "native-phase",
                                record=phase_record["command"], timeout=900,
                            )
                        def verify(returned: dict) -> dict:
                            observation = finish_observation()
                            phase_record["observer"] = observation
                            require(observation.get("complete") is True,
                                    "original_scene_live_native_observation_incomplete")
                            observations.append(observation)
                            persisted_bytes, evidence_binding = capture_regular(
                                evidence / "phase-evidence.json", MAX_JSON)
                            persisted = unique_json(persisted_bytes)
                            phase_record["evidence_binding"] = evidence_binding
                            input_after = None
                            if request["source"] is not None:
                                source = request["source"]
                                input_after = contract.inspect_source_file(
                                    paths["workspace"], source["relative_path"],
                                    expected_sha256=source["sha256"], expected_bytes=source["bytes"],
                                )
                            output = None
                            if phase != "verify_edited":
                                descriptor = returned["native_terminal"]["value"]["output"]
                                output = contract.inspect_source_file(
                                    paths["workspace"], descriptor["relative_path"],
                                    expected_sha256=descriptor["sha256"], expected_bytes=descriptor["bytes"],
                                )
                            phase_record["input_after"], phase_record["saved_output"] = input_after, output
                            checked = verify_phase(plan, source_bytes, request, returned, persisted,
                                                   input_before, input_after, output)
                            return checked
                        custody.once(phase, effect, verify)
            except BaseException as error:
                original_phase_error = error
                raise
            finally:
                if native_context is not None:
                    phase_record["context_cleanup"] = {
                        "errors": native_context["cleanup_errors"],
                        "daemon_returncode": native_context.get("daemon_returncode"),
                    }
                if original_phase_error is not None:
                    phase_record["original_error"] = {
                        "type": type(original_phase_error).__name__, "message": str(original_phase_error)
                    }
            require(not phase_record["context_cleanup"]["errors"],
                    "original_scene_context_cleanup_incomplete")
            if phase != "verify_edited":
                previous_output = (paths["workspace"], phase_record["saved_output"])
        custody.record["distinct_context_chain"] = same_original_chain(observations)
    except BaseException as error:
        original_error = error
        custody.record["original_error"] = {
            "type": type(error).__name__, "message": str(error),
            "traceback": traceback.format_exc(), "automatic_retry": False,
        }
    try:
        custody.seal()
    except BaseException as error:
        custody.secondary("seal", error)
    summary = custody.public_summary(original_error)
    try:
        new_json(public / "public-evidence.json", summary)
    except BaseException as error:
        custody.secondary("public-projection", error)
        raise SystemExit("Original Blender source evidence remains unverified; public projection failed") from None
    if summary["status"] != "PASS":
        raise SystemExit("Original Blender source evidence remains unverified; inspect retained sealed originals") from None
    print(json.dumps(summary, sort_keys=True))

# This profile extends the existing custody owner and staging path. Its closed
# public projection does not authenticate Broker responses or restore authority.
PRODUCT_SCOPE = "product-vessel-source-save-edit-reopen-cuts"
PRODUCT_PROFILE = "blender-product-vessel-v1"
PRODUCT_PUBLIC_KEYS = frozenset({
    "schema", "status", "scope", "source_sha", "semwright_sha", "run_id", "run_attempt",
    "source_profile", "source_native_frame_count", "required_native_probe_frames",
    "native_cycle_terminal", "native_contexts_provisioned", "native_contexts_observed",
    "native_source_cycle_observed", "decoded_surface_difference_observed",
    "sealed_originals", "recipient_spki_sha256", "full_1200_frame_render",
    "visual_acceptance", "human_acceptance", "automatic_native_retry",
})
PRODUCT_SOURCE_PATHS = (
    "SOURCE_LOCK.json", "Cargo.lock", "Cargo.toml",
    "crates/native/Cargo.toml", "crates/native/examples/native-blender-e2e.rs",
    "crates/native/src/blender_product_scene.rs",
    "crates/native/src/production.rs",
    "crates/native/src/production/blender_product_cycle.rs",
    "crates/native/src/production/blender_product_consumer.rs",
    "crates/native/src/production/blender_product_lineage.rs",
    "crates/native/src/production/receipt_custody.rs",
    "tooling/native_blender_e2e.py",
    "tooling/native_blender_original_evidence.py",
    "tooling/native_blender_product_contexts.py",
    "tooling/native_blender_product_pixels.py",
    "tooling/native_direct_mediated_parity.py",
    "tooling/manim_native_readback.py",
    "tooling/mlt_official_runtime_intake.py",
    "tooling/native_mlt_loaded_module.py",
    "tooling/ci_private_evidence.py",
    "tooling/native_evidence_projection.py",
    ".github/workflows/native-blender-e2e.yml",
)


def product_public_summary(custody: OriginalCustody,
                           original_error: BaseException | None) -> dict:
    def mapping(value):
        return value if type(value) is dict else {}
    record = custody.record
    product = mapping(record.get("product"))
    rows = product.get("contexts")
    rows = rows if type(rows) is list else []
    contexts = [row for row in rows if type(row) is dict and row.get("phase") in PHASES]
    provisioned = sum(type(row.get("runtime_bindings")) is dict
                      and type(row.get("capabilities_checked")) is list for row in contexts)
    observed = sum(mapping(row.get("observer")).get("complete") is True for row in contexts)
    provisioned, observed = min(provisioned, 3), min(observed, 3)
    command = mapping(product.get("cycle_command"))
    command_terminal = mapping(command.get("terminal"))
    returned = mapping(product.get("native_returned"))
    native_terminal = mapping(returned.get("native_terminal"))
    cycle_terminal = "NOT_STARTED"
    if command.get("invocation_reserved") is True and command_terminal.get("status") != "NOT_STARTED":
        cycle_terminal = "ERROR_OR_UNKNOWN"
    completed = (
        command_terminal.get("status") == "PROCESS_EXIT"
        and type(command_terminal.get("returncode")) is int
        and command_terminal["returncode"] == 0
        and returned.get("schema") == "motionwright.blender.product-cycle-evidence/1"
        and native_terminal.get("status") == "ok"
        and mapping(native_terminal.get("value")).get("schema")
            == "motionwright.blender.product-cycle-caller/2"
    )
    if completed:
        cycle_terminal = "COMPLETED"
    lineage = mapping(returned.get("custody"))
    chain = mapping(product.get("distinct_context_chain"))
    source_observed = (
        completed and product.get("seed_original_digest_verified") is True
        and product.get("caller_publication_bound") is True
        and returned.get("phase_contract_verified") is True
        and lineage.get("sqlite_reopened") is True
        and lineage.get("saved_source_and_lineage_verified") is True
        and mapping(lineage.get("production")).get("complete") is True
        and chain.get("three_distinct_live_native_contexts") is True
        and chain.get("same_native_binary") is True
        and len(contexts) == 3 and {row["phase"] for row in contexts} == set(PHASES)
        and provisioned == observed == 3
    )
    pixels = mapping(product.get("independent_surface_pixels"))
    decoded = (
        source_observed
        and pixels.get("schema") == "motionwright.blender.product-decoded-surface-difference/1"
        and pixels.get("decoded_pixel_difference_verified") is True
        and pixels.get("native_terminal_modified") is False
        and pixels.get("human_acceptance") is False
    )
    clean = (
        len(contexts) == 3
        and all(mapping(row.get("context_cleanup")).get("errors") == []
                and type(mapping(row.get("context_cleanup")).get("daemon_returncode")) is int
                for row in contexts)
        and product.get("secondary_errors") == []
    )
    before, after = mapping(record.get("source_before")), mapping(record.get("source_after"))
    stable = (before.get("source_bytes_bound") is True
              and before.get("source_sha") == custody.context["source_sha"] and before == after)
    sealed = custody.sealed if (type(custody.sealed) is dict
                               and custody.sealed.get("status") == "SEALED") else None
    verified = (original_error is None and not record["secondary_errors"]
                and source_observed and decoded and clean and stable and sealed is not None
                and sealed.get("selected_originals_complete") is True)
    return {
        "schema": "motionwright.blender.product-scene-public/1",
        "status": "PASS" if verified else "UNVERIFIED",
        "scope": PRODUCT_SCOPE, **custody.context,
        "source_profile": PRODUCT_PROFILE, "source_native_frame_count": 1200,
        "required_native_probe_frames": [600, 601],
        "native_cycle_terminal": cycle_terminal,
        "native_contexts_provisioned": provisioned, "native_contexts_observed": observed,
        "native_source_cycle_observed": bool(source_observed),
        "decoded_surface_difference_observed": bool(decoded),
        "sealed_originals": sealed, "recipient_spki_sha256": custody.recipient["sha256"],
        "full_1200_frame_render": False, "visual_acceptance": False,
        "human_acceptance": False, "automatic_native_retry": False,
    }


def check_product_public_summary(value: dict, context: dict,
                                 expected_recipient_sha256: str | None) -> None:
    require(type(value) is dict and set(value) == PRODUCT_PUBLIC_KEYS
            and value["schema"] == "motionwright.blender.product-scene-public/1"
            and value["scope"] == PRODUCT_SCOPE
            and value["source_profile"] == PRODUCT_PROFILE
            and value["status"] in ("PASS", "UNVERIFIED"),
            "product_public_schema")
    require(all(value[key] == context[key] for key in
                ("source_sha", "semwright_sha", "run_id", "run_attempt")),
            "product_public_source_context")
    require(type(expected_recipient_sha256) is str
            and HEX.fullmatch(expected_recipient_sha256) is not None
            and value["recipient_spki_sha256"] == expected_recipient_sha256,
            "product_public_recipient")
    require(type(value["source_native_frame_count"]) is int
            and value["source_native_frame_count"] == 1200
            and type(value["required_native_probe_frames"]) is list
            and all(type(frame) is int for frame in value["required_native_probe_frames"])
            and value["required_native_probe_frames"] == [600, 601]
            and all(value[key] is False for key in
                    ("full_1200_frame_render", "visual_acceptance",
                     "human_acceptance", "automatic_native_retry")),
            "product_public_scope_exceeded")
    require(value["native_cycle_terminal"] in
                ("NOT_STARTED", "ERROR_OR_UNKNOWN", "COMPLETED")
            and all(type(value[key]) is int and 0 <= value[key] <= 3 for key in
                    ("native_contexts_provisioned", "native_contexts_observed"))
            and value["native_contexts_observed"] <= value["native_contexts_provisioned"]
            and all(type(value[key]) is bool for key in
                    ("native_source_cycle_observed", "decoded_surface_difference_observed")),
            "product_public_observation_types")
    source, decoded = value["native_source_cycle_observed"], value["decoded_surface_difference_observed"]
    require(not decoded or source, "product_public_decode_without_source")
    require(not source or (value["native_cycle_terminal"] == "COMPLETED"
                          and value["native_contexts_observed"] == 3),
            "product_public_source_without_original_cycle")
    sealed = value["sealed_originals"]
    if sealed is not None:
        require(type(sealed) is dict and set(sealed) == {
                    "status", "sha256", "bytes", "selected_originals_complete"}
                and sealed["status"] == "SEALED" and type(sealed["sha256"]) is str
                and HEX.fullmatch(sealed["sha256"]) is not None
                and type(sealed["bytes"]) is int and 0 < sealed["bytes"] <= 256 * 1024 * 1024
                and type(sealed["selected_originals_complete"]) is bool,
                "product_public_seal_binding")
    if value["status"] == "PASS":
        require(source and decoded and sealed is not None
                and sealed["selected_originals_complete"] is True,
                "product_public_success_incomplete")


def run_product(app: Any) -> None:
    """One selected existing product helper; capture and seal every terminal."""
    import shutil
    import traceback
    from mlt_official_runtime_intake import capture_regular
    from native_blender_product_contexts import run_product_contexts
    from native_evidence_projection import verify_source_bindings

    require(sys.platform == "linux" and os.geteuid() != 0
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("CI") == "true" and os.environ.get("RUNNER_OS") == "Linux",
            "product_requires_selected_unprivileged_Linux_GHA")
    context = {"source_sha": app.GITHUB_SHA, "semwright_sha": app.PIN,
               "run_id": os.environ.get("GITHUB_RUN_ID", ""),
               "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "")}
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("MOTIONWRIGHT_BLENDER_SCOPE") == "product-scene"
            and os.environ.get("MOTIONWRIGHT_EXPECTED_SOURCE_SHA") == app.GITHUB_SHA
            and context["run_attempt"] == "1",
            "product_requires_exact_source_and_new_explicit_dispatch")
    raw_recipient = os.environ.get("MOTIONWRIGHT_PRIVATE_EVIDENCE_RECIPIENT", "")
    require(0 < len(raw_recipient.encode("utf-8")) <= 4096, "product_recipient_budget")
    recipient = unique_json(raw_recipient.encode("utf-8"))
    namespace = os.environ.get("MOTIONWRIGHT_PRODUCT_NAMESPACE", "")
    curve = os.environ.get("MOTIONWRIGHT_PRODUCT_CAMERA_CURVE", "")
    annotation = os.environ.get("MOTIONWRIGHT_PRODUCT_ANNOTATION_AFTER", "")
    # Rust's existing public model owns semantic admission. These are only
    # finite original argv budgets and the explicit selector.
    require(0 < len(namespace.encode("utf-8")) <= 128
            and curve in ("linear", "bezier_auto_clamped")
            and 0 < len(annotation.encode("utf-8")) <= 256,
            "product_original_inputs_missing_or_unbounded")
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    owner = runner_temp / ("mw-product-" + context["run_id"])
    public = app.ROOT / "verification" / "native-blender-product-public"
    public.parent.mkdir(parents=True, exist_ok=True)
    owner.mkdir(mode=0o700, exist_ok=False)
    public.mkdir(mode=0o700, exist_ok=False)
    _plain_owned_directory(owner)
    _plain_owned_directory(public)
    custody = OriginalCustody(owner, public, recipient, context, profile="product-scene")
    custody.record["original_inputs"] = {
        "namespace": namespace, "camera_curve": curve, "annotation_after": annotation,
        "source_constructor": "ProductScene::vessel",
    }
    original_error = None
    source_bindings = None
    try:
        source_bindings = [{"path": path, "sha256": app.digest(app.ROOT / path)}
                           for path in PRODUCT_SOURCE_PATHS]
        custody.record["source_before"] = verify_source_bindings(
            app.ROOT, app.GITHUB_SHA, source_bindings)
        custody.record["preflight"] = app.exact_inputs()
        lock_bytes, lock_binding = capture_regular(app.ROOT / "SOURCE_LOCK.json", 1024 * 1024)
        new_bytes(owner / "SOURCE_LOCK.json", lock_bytes)
        custody.record["source_lock"] = lock_binding
        caller_bytes, caller_binding = capture_regular(app.MW_BIN, MAX_EXE)
        require(caller_bytes.startswith(b"\x7fELF"), "product_caller_not_native_ELF")
        custody.record["caller_binary"] = caller_binding
        del caller_bytes
        selected = shutil.which("ffmpeg")
        require(selected is not None, "product_decoder_missing")
        decoder = Path(selected).resolve(strict=True)
        decoder_bytes, decoder_binding = capture_regular(decoder, MAX_EXE)
        require(decoder_bytes.startswith(b"\x7fELF"), "product_decoder_not_native_ELF")
        decoder_sha = hashlib.sha256(decoder_bytes).hexdigest()
        custody.record["decoder_binary"] = {
            "path": str(decoder), "sha256": decoder_sha, "capture": decoder_binding,
        }
        del decoder_bytes
        original_recipe = owner / "original-product-recipe.json"
        recipe_record = {}
        custody.record["recipe_command"] = recipe_record
        recipe = captured_json(
            [str(app.MW_BIN), "recipe-product", str(original_recipe), namespace, curve, annotation],
            env=dict(os.environ), prefix=owner / "recipe-command",
            record=recipe_record, timeout=60,
        )
        raw, raw_binding = capture_regular(original_recipe, 64 * 1024)
        recipe_sha = hashlib.sha256(raw).hexdigest()
        require(recipe.get("schema") == "motionwright.blender.product-recipe-original/1"
                and recipe.get("source_constructor") == "ProductScene::vessel"
                and recipe.get("profile") == PRODUCT_PROFILE
                and recipe.get("native_execution") == "NOT_RUN"
                and recipe.get("sqlite_opened") is False
                and recipe.get("human_acceptance") is False
                and recipe.get("annotation_after") == annotation
                and recipe.get("product_scene") == unique_json(raw)
                and recipe["product_scene"].get("namespace") == namespace
                and recipe["product_scene"].get("camera_curve") == curve
                and recipe.get("recipe") == {
                    "path": str(original_recipe), "bytes": len(raw), "sha256": recipe_sha},
                "product_original_recipe_not_bound_to_public_constructor")
        custody.record["recipe_original"] = {"receipt": recipe, "capture": raw_binding,
                                             "sha256": recipe_sha, "bytes": len(raw)}
        product = {}
        custody.record["product"] = product
        run_product_contexts(
            app, owner=owner, recipe_path=original_recipe, request_id="product-" + context["run_id"],
            annotation_body=annotation, record=product,
            ffmpeg=decoder, ffmpeg_sha256=decoder_sha, expected_recipe_sha256=recipe_sha,
        )
    except BaseException as error:
        original_error = error
        custody.record["original_error"] = {
            "type": type(error).__name__, "message": str(error),
            "traceback": traceback.format_exc(), "automatic_retry": False,
        }
    if source_bindings is not None:
        try:
            custody.record["source_after"] = verify_source_bindings(
                app.ROOT, app.GITHUB_SHA, source_bindings)
            require(custody.record["source_after"] == custody.record.get("source_before"),
                    "product_source_changed_during_original_attempt")
        except BaseException as error:
            custody.secondary("source-after", error)
    try:
        custody.seal()
    except BaseException as error:
        custody.secondary("seal", error)
    summary = custody.public_summary(original_error)
    try:
        check_public_summary(summary, context, profile="product-scene",
                             expected_recipient_sha256=recipient["sha256"])
        new_json(public / "public-evidence.json", summary)
    except BaseException as error:
        custody.secondary("public-projection", error)
        raise SystemExit("Product source evidence remains unverified; public projection failed") from None
    if summary["status"] != "PASS":
        raise SystemExit("Product source evidence remains unverified; inspect retained sealed originals") from None
    print(json.dumps(summary, sort_keys=True))

PUBLIC_KEYS = frozenset({
    "schema", "status", "scope", "source_sha", "semwright_sha", "run_id", "run_attempt",
    "phases_attempted", "phases_verified", "sealed_originals", "geometry_content_verified",
    "rendered_camera_verified", "human_acceptance", "automatic_native_retry",
})


def check_public_summary(value: dict, context: dict, *, profile: str = "original-scene",
                         expected_recipient_sha256: str | None = None) -> None:
    if profile == "product-scene":
        check_product_public_summary(value, context, expected_recipient_sha256)
        return
    require(profile == "original-scene", "original_scene_public_profile")
    require(type(value) is dict and set(value) == PUBLIC_KEYS
            and value["schema"] == "motionwright.blender.original-scene-public/1"
            and value["scope"] == "static-Scene-UUID-camera-save-edit-reopen"
            and value["status"] in ("PASS", "UNVERIFIED"),
            "original_scene_public_schema")
    require(all(value[key] == context[key] for key in
                ("source_sha", "semwright_sha", "run_id", "run_attempt")),
            "original_scene_public_source_context")
    require(all(value[key] is False for key in
                ("geometry_content_verified", "rendered_camera_verified",
                 "human_acceptance", "automatic_native_retry")),
            "original_scene_public_scope_exceeded")
    attempted, verified = value["phases_attempted"], value["phases_verified"]
    require(type(attempted) is int and type(verified) is int
            and 0 <= verified <= attempted <= 3, "original_scene_public_phase_counts")
    sealed = value["sealed_originals"]
    if sealed is not None:
        require(type(sealed) is dict and set(sealed) == {
                    "status", "sha256", "bytes", "selected_originals_complete"}
                and sealed["status"] == "SEALED" and type(sealed["sha256"]) is str
                and HEX.fullmatch(sealed["sha256"]) is not None
                and type(sealed["bytes"]) is int and 0 < sealed["bytes"] <= 256 * 1024 * 1024
                and type(sealed["selected_originals_complete"]) is bool,
                "original_scene_public_seal_binding")
    if value["status"] == "PASS":
        require(attempted == verified == 3 and sealed is not None
                and sealed["selected_originals_complete"] is True,
                "original_scene_public_success_incomplete")


def stage_public(source: Path, output: Path, context: dict, job_status: str,
                 *, profile: str = "original-scene",
                 expected_recipient_sha256: str | None = None) -> dict:
    """Publish a closed projection and opaque seal only; keep raw receipts private."""
    from ci_private_evidence import MAGIC, SEAL_SCHEMA, strict, validate_context
    from mlt_official_runtime_intake import capture_regular
    validate_context(context)
    require(profile in ("original-scene", "product-scene"), "original_scene_stage_profile")
    if profile == "product-scene":
        require(type(expected_recipient_sha256) is str
                and HEX.fullmatch(expected_recipient_sha256) is not None,
                "product_stage_recipient_missing")
    require(job_status in ("success", "failure", "cancelled", "skipped"),
            "original_scene_public_job_status")
    require(source.is_absolute() and output.is_absolute()
            and source.resolve() != output.resolve()
            and source not in output.parents and output not in source.parents,
            "original_scene_public_output_overlaps_source")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    value = None
    reasons = []
    try:
        raw, _ = capture_regular(source / "public-evidence.json", 64 * 1024)
        value = unique_json(raw)
        check_public_summary(value, context, profile=profile,
                             expected_recipient_sha256=expected_recipient_sha256)
    except Exception:
        value = None
        reasons.append("producer_projection_unverified")
    binding = None
    try:
        cipher, _ = capture_regular(source / "private-evidence.mwseal", 256 * 1024 * 1024)
        require(cipher.startswith(MAGIC) and len(cipher) > len(MAGIC) + 4,
                "original_scene_public_cipher_magic")
        offset = len(MAGIC)
        length = int.from_bytes(cipher[offset:offset + 4], "big")
        require(0 < length <= 8192 and len(cipher) > offset + 4 + length + 16,
                "original_scene_public_cipher_header_size")
        header = strict(cipher[offset + 4:offset + 4 + length])
        require(header.get("schema") == SEAL_SCHEMA and header.get("context") == context,
                "original_scene_public_cipher_context")
        if profile == "product-scene":
            require(header.get("recipient_spki_sha256") == expected_recipient_sha256,
                    "product_stage_cipher_recipient")
        binding = new_bytes(output / "private-evidence.mwseal", cipher)
    except Exception:
        reasons.append("sealed_originals_unavailable")
    producer_seal = value["sealed_originals"] if value is not None else None
    bound = (binding is not None and producer_seal is not None
             and producer_seal["sha256"] == binding["sha256"]
             and producer_seal["bytes"] == binding["bytes"])
    complete = bound and producer_seal["selected_originals_complete"] is True
    passed = (value is not None and value["status"] == "PASS" and complete
              and job_status == "success" and not reasons)
    if profile == "original-scene":
        projection = {
            "schema": "motionwright.blender.original-scene-published/1",
            **context, "status": "PASS" if passed else "UNVERIFIED", "job_status": job_status,
            "scope": "static-Scene-UUID-camera-save-edit-reopen",
            "phases_attempted": value["phases_attempted"] if value is not None else 0,
            "phases_verified": value["phases_verified"] if value is not None else 0,
            "sealed_originals": binding, "selected_originals_complete": bool(complete),
            "producer_projection_bound": bool(bound), "projection_limitations": reasons,
            "geometry_content_verified": False, "rendered_camera_verified": False,
            "human_acceptance": False, "automatic_native_retry": False,
        }
    if profile == "product-scene":
        projection = {
            "schema": "motionwright.blender.product-scene-published/1",
            **context, "status": "PASS" if passed else "UNVERIFIED", "job_status": job_status,
            "scope": PRODUCT_SCOPE, "source_profile": PRODUCT_PROFILE,
            "source_native_frame_count": 1200, "required_native_probe_frames": [600, 601],
            "native_cycle_terminal": value["native_cycle_terminal"] if value is not None else "NOT_OBSERVED",
            "native_contexts_provisioned": value["native_contexts_provisioned"] if value is not None else 0,
            "native_contexts_observed": value["native_contexts_observed"] if value is not None else 0,
            "native_source_cycle_observed": value["native_source_cycle_observed"] if bound else False,
            "decoded_surface_difference_observed": value["decoded_surface_difference_observed"] if bound else False,
            "sealed_originals": binding, "selected_originals_complete": bool(complete),
            "recipient_spki_sha256": expected_recipient_sha256,
            "producer_projection_bound": bool(bound), "projection_limitations": reasons,
            "full_1200_frame_render": False, "visual_acceptance": False,
            "human_acceptance": False, "automatic_native_retry": False,
        }
    new_json(output / "public-evidence.json", projection)
    return projection


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Stage only closed original-Scene metadata and sealed originals.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-semwright", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--job-status", required=True)
    parser.add_argument("--profile", choices=("original-scene", "product-scene"), default="original-scene")
    parser.add_argument("--expected-recipient-sha256")
    args = parser.parse_args()
    result = stage_public(args.source.resolve(), args.output.resolve(), {
        "source_sha": args.expected_source, "semwright_sha": args.expected_semwright,
        "run_id": args.run_id, "run_attempt": args.run_attempt,
    }, args.job_status, profile=args.profile,
       expected_recipient_sha256=args.expected_recipient_sha256)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
