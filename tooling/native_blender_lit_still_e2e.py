#!/usr/bin/env python3
"""Four original lit-still phases through the existing canonical Blender Host.

A source-bound CI producer, not an alternative Blender launcher. It preserves
each native terminal, uses new Host contexts, and stops after any failed or
uncertain phase. Its private output is input to the existing selected-evidence
collector; it never publishes an owner tree or a session ticket.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

import native_blender_original_evidence as retained
import native_blender_lit_still_oracle as pixels
from mlt_official_runtime_intake import capture_regular

SCHEMA = "motionwright.blender.lit-still-producer/1"
PROFILE = "planar-project-cycles-lit-still-v1"
PHASES = ("create", "edit", "reopen", "light_control")
LABELS = dict(zip(PHASES, ("lit-create", "lit-edit", "lit-reopen", "lit-light-control")))
ROLES = dict(zip(PHASES, ("original", "edited", "reopened", "light_off")))
NAMES = dict(zip(PHASES, ("lit-original.png", "lit-edited.png", "lit-reopened.png", "lit-light-off.png")))
SAVED = {"create": "lit-original.blend", "edit": "lit-edited.blend"}
HEX = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
COMMANDS = frozenset("driver.blender." + tail for tail in (
    "composition.apply", "composition.inspect", "composition.plan", "composition.verify",
    "file.open", "file.save", "render", "render.settings", "scene.inspect",
    "semantic.custom.list", "semantic.custom.set", "semantic.datablock.create",
    "semantic.objects", "semantic.property.get", "semantic.property.set",
    "semantic.relation.set", "semantic.relation.unlink", "semantic.relations", "semantic.rename",
))
READ_COMMANDS = frozenset("driver.blender." + tail for tail in (
    "composition.inspect", "composition.plan", "composition.verify", "scene.inspect",
    "semantic.custom.list", "semantic.objects", "semantic.property.get", "semantic.relations",
))
SOURCE_PATHS = (
    "SOURCE_LOCK.json", "Cargo.lock", "crates/native/Cargo.toml",
    "crates/native/examples/native-blender-lit-still-e2e.rs",
    "crates/native/src/production.rs",
    "crates/native/src/production/blender_render.rs",
    "crates/native/src/production/blender_original.rs",
    "crates/native/src/multi_renderer.rs",
    "tooling/native_blender_e2e.py",
    "tooling/native_blender_original_evidence.py",
    "tooling/blender_original_scene_contract.py",
    "tooling/native_blender_lit_still_e2e.py",
    "tooling/native_blender_lit_still_oracle.py",
    "tooling/native_mlt_loaded_module.py",
    "tooling/mlt_official_runtime_intake.py",
    "tooling/ci_private_evidence.py",
    "tooling/native_evidence_projection.py",
)
MAX_JSON = 64 * 1024 * 1024
MAX_BLEND = 256 * 1024 * 1024  # Existing selected-evidence individual-file bound.
MAX_EXE = 256 * 1024 * 1024


class ProducerError(ValueError):
    pass


def require(condition: object, code: str) -> None:
    if not condition:
        raise ProducerError(code)


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def binding(body: bytes) -> dict:
    return {"bytes": len(body), "sha256": sha(body)}


def plain_binding(value: object) -> bool:
    return (type(value) is dict and set(value) == {"bytes", "sha256"}
            and type(value["bytes"]) is int and value["bytes"] > 0
            and type(value["sha256"]) is str and HEX.fullmatch(value["sha256"]) is not None)


def check_ack(ack: dict, persisted_bytes: bytes, phase: str) -> dict:
    require(phase in PHASES, "unknown-phase")
    require(type(ack) is dict and set(ack) == {
        "schema", "phase", "phase_contract_verified", "evidence",
        "decoded_pixel_verification", "formal_acceptance"}, "phase-ack-fields")
    require(ack["schema"] == "motionwright.blender.lit-still-phase-ack/1"
            and ack["phase"] == phase and ack["phase_contract_verified"] is True
            and ack["decoded_pixel_verification"] is False
            and ack["formal_acceptance"] is False
            and plain_binding(ack["evidence"])
            and ack["evidence"] == binding(persisted_bytes), "phase-ack-not-bound-to-original")
    persisted = retained.unique_json(persisted_bytes)
    require(persisted.get("schema") == "motionwright.blender.lit-still-phase-evidence/1"
            and persisted.get("fixture_contract_sha256") == pixels.FIXTURE_CONTRACT_SHA256
            and persisted.get("phase_contract_verified") is False
            and persisted.get("custody_errors") == []
            and persisted.get("decoded_pixel_verification") is False
            and persisted.get("human_acceptance") is False, "phase-evidence-contract")
    terminal = persisted.get("native_terminal")
    require(type(terminal) is dict and set(terminal) == {"status", "value"}
            and terminal["status"] == "ok" and type(terminal["value"]) is dict,
            "original-native-terminal-not-success")
    retained._require_empty_private_append_custody(persisted)
    return persisted


def check_seed(seeded: dict, seed_bytes: bytes, source_bytes: bytes, plan_bytes: bytes) -> dict:
    require(retained.unique_json(seed_bytes) == seeded
            and seeded.get("schema") == "motionwright.blender.lit-still-seed/1"
            and seeded.get("fixture_contract_sha256") == pixels.FIXTURE_CONTRACT_SHA256
            and seeded.get("fixture_kind") == "first_party_synthetic"
            and seeded.get("sqlite_reopened") is True
            and seeded.get("native_execution") == "NOT_RUN"
            and seeded.get("human_acceptance") is False, "seed-custody")
    require(seeded.get("project_json") == {
        "path": "source-project.json", **binding(source_bytes)}, "seed-project-bytes")
    source, plan = retained.unique_json(source_bytes), retained.unique_json(plan_bytes)
    roles = seeded.get("role_node_ids")
    require(type(roles) is dict and set(roles) == {
        "edited_gold_card", "blue_anchor", "rose_disk"}
        and all(type(v) is str and v for v in roles.values())
        and len(set(roles.values())) == 3, "seed-role-roster")
    projected = plan.get("source", {})
    contribution = projected.get("contribution", {})
    objects = contribution.get("objects")
    require(plan.get("profile") == PROFILE
            and projected.get("project_json_sha256") == sha(source_bytes)
            and projected.get("camera", {}).get("resolution") == [1920, 1080]
            and type(objects) is list and len(objects) == 3
            and {obj.get("node_id") for obj in objects} == set(roles.values())
            and contribution.get("project_id") == seeded.get("project_id") == source.get("id")
            and contribution.get("scene_id") == seeded.get("scene_id")
            and contribution.get("generation") == seeded.get("generation") == source.get("generation")
            and contribution.get("revision") == seeded.get("revision") == source.get("revision"),
            "seed-project-projection")
    return plan


def receipt_calls(persisted: dict, request: dict, plan: dict, source_bytes: bytes) -> tuple[list[dict], dict]:
    custody = persisted.get("custody", {})
    require(custody.get("sqlite_reopened") is True and custody.get("project_unchanged") is True
            and custody.get("project") == retained.unique_json(source_bytes), "sqlite-source-changed")
    production = custody.get("production", {})
    rows, watermark = production.get("receipts"), production.get("watermark", {})
    require(production.get("complete") is True and type(rows) is list
            and 0 < len(rows) <= 20_000 and type(watermark.get("count")) is int
            and watermark["count"] == len(rows), "receipt-snapshot-incomplete")
    by_id = {}
    for row in rows:
        require(type(row) is dict and type(row.get("id")) is str
                and row["id"] not in by_id, "duplicate-or-missing-receipt")
        by_id[row["id"]] = row
    source = plan["source"]["contribution"]
    prefix = request["request_id"] + ":" + LABELS[request["phase"]] + ":"
    # Inspect the complete history, not the latest receipt or a 256-row page.
    # A later completed row cannot replace an original uncertain terminal.
    for row in rows:
        if str(row.get("request_id", "")).startswith(prefix):
            require(row.get("stage") in {"dispatching", "completed"},
                    "original-phase-has-unresolved-or-failed-history")
    journal = persisted.get("journal", {})
    require(persisted.get("request") == request
            and journal.get("schema") == "motionwright.blender.lit-still/1"
            and journal.get("phase") == LABELS[request["phase"]]
            and journal.get("plan") == plan
            and type(journal.get("calls")) is list and 0 < len(journal["calls"]) <= 8192,
            "journal-source-or-request-mismatch")
    calls, ids, brokers, generations = [], set(), set(), set()
    for sequence, call in enumerate(journal["calls"], 1):
        require(type(call) is dict, "journal-call-shape")
        command, request_id = call.get("command"), prefix + f"{sequence:06}"
        require(command in COMMANDS and type(call.get("sequence")) is int
                and call["sequence"] == sequence and call.get("request_id") == request_id
                and type(call.get("args")) is dict and call.get("native_error") is None
                and call.get("mutation") is (command not in READ_COMMANDS),
                "journal-command-authority-or-order")
        reply = call.get("native_result")
        require(type(reply) is dict and reply.get("replayed") is False, "replayed-native-call")
        receipt = reply.get("receipt", {})
        require(type(receipt) is dict and receipt.get("id") not in ids
                and by_id.get(receipt.get("id")) == receipt
                and all(receipt.get(key) == source[key] for key in ("project_id", "generation", "revision"))
                and receipt.get("request_id") == request_id and receipt.get("command") == command
                and receipt.get("stage") == "completed"
                and type(receipt.get("request_sha256")) is str
                and HEX.fullmatch(receipt["request_sha256"]), "call-not-bound-to-completed-original")
        ids.add(receipt["id"])
        payload = receipt.get("payload", {})
        broker = payload.get("broker_request_id")
        require(type(broker) is str and broker and broker not in brokers,
                "duplicate-or-missing-broker-request")
        brokers.add(broker)
        response = reply.get("result")
        require(type(response) is dict and response == payload.get("result")
                and response.get("provenance_checked") is True, "native-response-custody-mismatch")
        execution = response.get("execution", {})
        provenance = execution.get("provenance", {})
        generation = provenance.get("provider_generation")
        require(type(execution.get("backend")) is str and execution["backend"]
                and execution.get("dry_run") is False
                and provenance.get("provider") == "driver:blender"
                and provenance.get("source") == "driver"
                and provenance.get("execution_provider") == "driver:blender"
                and provenance.get("untrusted_metadata") is False
                and type(provenance.get("descriptor_sha256")) is str
                and HEX.fullmatch(provenance["descriptor_sha256"])
                and type(generation) is int and generation > 0
                and type(response.get("data")) is dict, "native-provider-authority")
        generations.add(generation)
        calls.append({"command": command, "args": call["args"], "data": response["data"]})
    require(len(generations) == 1, "provider-generation-changed")
    # No completed phase operation can be omitted from the original journal.
    completed = {row["id"] for row in rows if str(row.get("request_id", "")).startswith(prefix)
                 and row.get("stage") == "completed"}
    require(completed == ids, "phase-completed-roster-differs-from-journal")
    requests = {prefix + f"{i:06}" for i in range(1, len(calls) + 1)}
    phase_rows = [row for row in rows if str(row.get("request_id", "")).startswith(prefix)]
    require({row["request_id"] for row in phase_rows} == requests,
            "phase-history-has-unjournaled-operation")
    for request_id in requests:
        history = [row for row in phase_rows if row["request_id"] == request_id]
        require(len(history) == 2 and {row["stage"] for row in history} == {"dispatching", "completed"}
                and all(history[0].get(key) == history[1].get(key) for key in (
                    "project_id", "generation", "revision", "request_id", "request_sha256", "command")),
                "original-dispatch-completion-pair-differs")
    return calls, {"complete_receipt_count": len(rows), "original_call_count": len(calls),
                   "provider_generation": next(iter(generations))}


def check_readback(value: dict, calls: list[dict], native_session: str,
                   light_control: bool, plan: dict) -> None:
    require(type(value) is dict and value.get("light_control") is light_control
            and value.get("pixel_fidelity_verified") is False, "native-readback-shape")
    snapshot = value.get("snapshot", {})
    require(snapshot.get("native_session") == native_session
            and type(snapshot.get("fingerprint")) is str and HEX.fullmatch(snapshot["fingerprint"])
            and any(c["command"] == "driver.blender.composition.inspect" and c["data"] == snapshot
                    for c in calls), "native-snapshot-not-in-original-calls")
    props = value.get("properties")
    require(type(props) is list and props
            and all(any(c["command"] == "driver.blender.semantic.property.get" and c["data"] == item
                        for c in calls) for item in props), "native-properties-not-in-original-calls")
    scene = value.get("active_scene", {})
    require(scene.get("name") == plan["scene_name"] and scene.get("object_count") == 5
            and scene.get("resolution") == [1920, 1080]
            and any(c["command"] == "driver.blender.scene.inspect" and c["data"] == scene for c in calls),
            "active-scene-not-in-original-calls")


def verify_phase(ack: dict, persisted_bytes: bytes, request: dict, plan: dict,
                 source_bytes: bytes) -> tuple[dict, dict]:
    phase = request["phase"]
    persisted = check_ack(ack, persisted_bytes, phase)
    calls, receipt_summary = receipt_calls(persisted, request, plan, source_bytes)
    result = persisted["native_terminal"]["value"]
    require(result.get("schema") == "motionwright.blender.lit-still/1"
            and result.get("profile") == PROFILE and result.get("phase") == phase
            and result.get("project_json_sha256") == sha(source_bytes)
            and result.get("source") == request["source"]
            and result.get("edit_node_id") == request["edit_node_id"]
            and result.get("native_render_invoked") is True
            and result.get("native_render_file_measured") is True
            and result.get("broker_terminal_custody_requires_completed_receipts") is True
            and result.get("decoded_pixel_verification") is False
            and result.get("visual_acceptance") is False, "original-native-result-contract")
    native_session = result.get("native_session")
    require(type(native_session) is str and 0 < len(native_session) <= 256,
            "native-session-missing")
    check_readback(result.get("before_render"), calls, native_session, phase == "light_control", plan)
    check_readback(result.get("after_render"), calls, native_session, phase == "light_control", plan)
    require(result["before_render"]["snapshot"] == result["after_render"]["snapshot"],
            "source-changed-across-render")
    opens = [c for c in calls if c["command"] == "driver.blender.file.open"]
    renders = [c for c in calls if c["command"] == "driver.blender.render"]
    saves = [c for c in calls if c["command"] == "driver.blender.file.save"]
    require(len(renders) == 1 and renders[0]["args"] == {"path": NAMES[phase]}
            and renders[0]["data"] == {"changed": True, "path": NAMES[phase], "format": "png"},
            "native-render-ack-differs")
    if phase == "create":
        require(not opens and request["source"] is None and result.get("before") is None
                and request["edit_node_id"] is None, "create-consumed-foreign-source")
    else:
        source = request["source"]
        require(type(source) is dict and native_session != source["origin_native_session"]
                and len(opens) == 1 and opens[0]["args"] == {"path": source["file"]["relative_path"]}
                and opens[0]["data"] == {"changed": True, "path": source["file"]["relative_path"],
                                        "use_scripts": False}, "fresh-reopen-source-binding")
        check_readback(result.get("before"), calls, native_session, False, plan)
        require(result["before"]["snapshot"]["fingerprint"] == source["composition_fingerprint"],
                "opened-source-fingerprint-differs")
    if phase in SAVED:
        saved = result.get("saved_source")
        require(type(saved) is dict and set(saved) == {
            "file", "island", "composition_fingerprint", "origin_native_session", "edited_node_id"}
            and len(saves) == 1 and saves[0]["args"] == {"path": SAVED[phase]}
            and saves[0]["data"] == {"changed": True, "path": SAVED[phase], "copy": True}
            and saved["file"].get("relative_path") == SAVED[phase]
            and saved["origin_native_session"] == native_session
            and saved["edited_node_id"] == request["edit_node_id"]
            and saved["composition_fingerprint"] == result["after_render"]["snapshot"]["fingerprint"],
            "native-saved-source-binding")
        require(calls.index(saves[0]) > calls.index(renders[0]), "source-saved-before-original-render")
    else:
        require(not saves and result.get("saved_source") is None, "readback-phase-saved-over-source")
    if phase in ("edit", "light_control"):
        require(result["before"]["snapshot"]["fingerprint"]
                != result["before_render"]["snapshot"]["fingerprint"], "requested-native-change-not-observed")
    return result, {"phase": phase, **receipt_summary, "native_session": native_session,
                    "original_evidence": binding(persisted_bytes),
                    "canonical_full_scene_readback_bound": True,
                    "independent_pixel_verification": "SEPARATE", "formal_acceptance": False}


def measured_file(workspace: Path, descriptor: dict, expected_name: str, maximum: int) -> tuple[bytes, dict]:
    require(type(descriptor) is dict and set(descriptor) == {"relative_path", "bytes", "sha256"}
            and descriptor["relative_path"] == expected_name
            and plain_binding({key: descriptor[key] for key in ("bytes", "sha256")}),
            "native-file-descriptor")
    body, _ = capture_regular(workspace / expected_name, maximum)
    require(binding(body) == {key: descriptor[key] for key in ("bytes", "sha256")},
            "original-native-file-changed")
    return body, descriptor


def distinct_contexts(observations: list[dict], sessions: list[str]) -> dict:
    require(len(observations) == len(sessions) == 4 and len(set(sessions)) == 4
            and all(o.get("complete") is True and type(o.get("first")) is dict for o in observations),
            "four-native-contexts-not-complete")
    native = [o["first"] for o in observations]
    require(len({(x["pid"], x["start_ticks"]) for x in native}) == 4
            and len({(x["daemon_pid"], x["daemon_start_ticks"]) for x in native}) == 4
            and len({(x["workspace_device"], x["workspace_inode"]) for x in native}) == 4
            and len({x["blender_sha256"] for x in native}) == 1
            and all(native[i]["start_ticks"] < native[i + 1]["start_ticks"] for i in range(3)),
            "native-context-reused-or-runtime-changed")
    return {"four_distinct_native_contexts": True, "four_distinct_brokers": True,
            "four_distinct_workspaces": True, "same_measured_blender": True}


class Record:
    def __init__(self, value: dict):
        self.value = value

    def secondary(self, stage: str, error: BaseException) -> None:
        self.value["secondary_errors"].append({"stage": stage, "type": type(error).__name__})


def observe_decoder_version(decoder: Path, expected_sha: str, destination: Path,
                            environment: dict[str, str]) -> dict:
    """Bound the original read-only version command and execute its measured ELF FD."""
    import resource
    import subprocess
    intent = destination / "decoder-version-intent.json"
    retained.new_json(intent, {"decoder_sha256": expected_sha, "timeout_seconds": 10,
                               "maximum_stream_bytes": 65536, "automatic_retry": False})
    observation = {"state": "INCOMPLETE", "decoder_sha256": expected_sha}
    try:
        with pixels._regular(decoder, MAX_EXE) as executable:
            identity = pixels._identity(executable)
            require(sha(executable.read(MAX_EXE + 1)) == expected_sha, "decoder-version-ELF-changed")
            stdout_path, stderr_path = destination / "ffmpeg-version.txt", destination / "ffmpeg-version-stderr"
            def limits():
                resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
                resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                os.chmod(stdout_path, 0o600)
                os.chmod(stderr_path, 0o600)
                result = subprocess.run([str(decoder), "-version"],
                    executable=f"/proc/self/fd/{executable.fileno()}", env=environment,
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    pass_fds=(executable.fileno(),), preexec_fn=limits, timeout=10, check=False)
                observation["exit_code"] = result.returncode
                stdout.flush()
                os.fsync(stdout.fileno())
                stderr.flush()
                os.fsync(stderr.fileno())
            body, _ = capture_regular(stdout_path, 65536)
            require(result.returncode == 0 and body.startswith(b"ffmpeg version ")
                    and pixels._identity(executable) == identity, "decoder-version-not-observed")
            observation.update(state="COMPLETE", version_output=binding(body))
            return observation
    finally:
        retained.new_json(destination / "decoder-version-receipt.json", observation)


# The fixed producer contract below is consumed by the already reviewed /2
# collector. It selects this first-party case only; it does not enumerate a
# directory, grant upload permission, or export private diagnostics or tickets.
def selection_contract():
    import native_evidence_projection as selection
    F, O, A = selection.Field, selection.Object, selection.Array
    S, N, L, Q = selection.String, selection.Number, selection.Literal, selection.Nullable
    integer = N(0, 2**53 - 1, integer=True)
    signed = N(-(2**53 - 1), 2**53 - 1, integer=True)
    digest = S(64, r"[0-9a-f]{64}")
    ident = S(64, r"[A-Za-z0-9_-]{1,64}")
    uuid = S(36, r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
    dimensions = A(integer, 2, 2)
    vector3 = A(N(-1e12, 1e12), 3, 3)
    file_binding = O({"bytes": F(integer), "sha256": F(digest)})
    artifact = O({"relative_path": F(S(64, r"lit-(?:original|edited|reopened|light-off)\.(?:blend|png)")),
                  "bytes": F(integer), "sha256": F(digest)})
    saved = O({"file": F(artifact), "island": F(ident),
               "composition_fingerprint": F(digest), "origin_native_session": F(S(256)),
               "edited_node_id": F(Q(uuid))})
    resource = S(44, r"project:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
    stamp = O({"resource": F(resource), "generation": F(uuid), "revision": F(integer)})
    context_rule = O({"source_sha": F(S(40, r"[0-9a-f]{40}")),
                      "semwright_sha": F(S(40, r"[0-9a-f]{40}")),
                      "run_id": F(S(20, r"[1-9][0-9]{0,19}")), "run_attempt": F(L("1"))})
    seed_rule = O({
        "schema": F(L("motionwright.blender.lit-still-seed/1")),
        "fixture_kind": F(L("first_party_synthetic")),
        "fixture_contract_sha256": F(L(pixels.FIXTURE_CONTRACT_SHA256)),
        "project_id": F(uuid), "resource": F(resource), "generation": F(uuid),
        "revision": F(integer), "expected": F(stamp), "scene_id": F(uuid),
        "role_node_ids": F(O({name: F(uuid) for name in ("edited_gold_card", "blue_anchor", "rose_disk")})),
        "project_json": F(O({"path": F(L("source-project.json")), "bytes": F(integer), "sha256": F(digest)})),
        "plan_path": F(L("source-plan.json")), "sqlite_reopened": F(L(True)),
        "native_execution": F(L("NOT_RUN")), "human_acceptance": F(L(False)),
    })
    obj = O({"node_id": F(uuid), "name": F(S(128)), "material_name": F(S(128)),
             "primitive": F(S(16, values=("plane", "cylinder"))),
             "color_rgba": F(A(N(0, 1), 4, 4)),
             "location": F(vector3), "rotation": F(vector3), "scale": F(vector3)})
    contribution = O({"project_id": F(uuid), "generation": F(uuid), "revision": F(integer),
                      "scene_id": F(uuid), "collection_name": F(S(128)), "objects": F(A(obj, 3, 3))})
    camera = O({"object_name": F(S(128)), "data_name": F(S(128)), "location": F(vector3),
                "rotation_euler": F(vector3), "projection": F(S(32)), "sensor_fit": F(S(32)),
                "ortho_scale": F(N(0, 1e12)), "clip_start": F(N(0, 1e12)),
                "clip_end": F(N(0, 1e12)), "resolution": F(dimensions),
                "pixel_aspect": F(A(N(0, 1000), 2, 2))})
    plan_rule = O({
        "schema": F(L("motionwright.blender.lit-still/1")), "profile": F(L(PROFILE)),
        "source": F(O({"project_json_sha256": F(digest), "contribution": F(contribution),
                       "camera": F(camera)})), "scene_name": F(S(128)), "world_name": F(S(128)),
        "camera_entity": F(ident), "light_entity": F(ident),
        "light_translation": F(vector3), "light_rotation": F(vector3),
    })
    request_rule = O({"project_id": F(uuid), "expected": F(stamp), "scene_id": F(uuid),
                      "request_id": F(S(48, r"[A-Za-z0-9_-]+")),
                      "phase": F(S(20, values=PHASES)),
                      "source": F(Q(saved)), "edit_node_id": F(Q(uuid))})
    value_rule = O({
        "schema": F(L("motionwright.blender.lit-still/1")), "profile": F(L(PROFILE)),
        "phase": F(S(20, values=PHASES)), "project_json_sha256": F(digest),
        "source": F(Q(saved)), "edit_node_id": F(Q(uuid)), "native_session": F(S(256)),
        "image": F(artifact), "saved_source": F(Q(saved)),
        "native_render_invoked": F(bool), "native_render_file_measured": F(bool),
        "source_only_composition": F(bool), "general_product_3d_authoring": F(L(False)),
        "flat_canvas_pixel_equivalence": F(L(False)), "decoded_pixel_verification": F(L(False)),
        "native_audio": F(L(False)), "atomic_no_clobber": F(L(False)),
        "broker_terminal_custody_requires_completed_receipts": F(bool), "visual_acceptance": F(L(False)),
    })
    error_rule = O({"code": F(S(64, r"[A-Za-z_][A-Za-z0-9_]*")), "outcome_known": F(bool)})
    receipt_rule = O({
        "id": F(uuid), "project_id": F(uuid), "generation": F(uuid), "revision": F(integer),
        "request_id": F(S(256)), "request_sha256": F(digest),
        "command": F(S(128, r"driver\.blender\.[a-z.]+")),
        "stage": F(S(32, values=("dispatching", "completed", "failed_known", "outcome_unknown"))),
    })
    phase_rule = O({
        "schema": F(L("motionwright.blender.lit-still-phase-evidence/1")),
        "fixture_contract_sha256": F(L(pixels.FIXTURE_CONTRACT_SHA256)),
        "request": F(request_rule), "phase_contract_verified": F(L(False)),
        "decoded_pixel_verification": F(L(False)), "human_acceptance": F(L(False)),
        "native_terminal": F(O({"status": F(S(8, values=("ok", "error"))),
                               "value": F(value_rule, required=False),
                               "error": F(error_rule, required=False)})),
        "custody_errors": F(A(O({"stage": F(S(16, values=("capture", "publish")))}), 2)),
        "custody": F(O({"sqlite_reopened": F(bool), "project_unchanged": F(bool),
            "production": F(O({"complete": F(bool),
                "watermark": F(O({"latest_id": F(Q(uuid)), "count": F(integer)})),
                "receipts": F(A(receipt_rule, 20_000))}))}), required=False),
    })
    verification_rule = O({"phase": F(S(20, values=PHASES)), "complete_receipt_count": F(integer),
        "original_call_count": F(integer), "provider_generation": F(integer),
        "native_session": F(S(256)), "original_evidence": F(file_binding),
        "canonical_full_scene_readback_bound": F(bool),
        "independent_pixel_verification": F(L("SEPARATE")), "formal_acceptance": F(L(False))})
    native_identity = O({
        "pid": F(integer), "start_ticks": F(integer), "daemon_pid": F(integer),
        "daemon_start_ticks": F(integer), "workspace_device": F(integer),
        "workspace_inode": F(integer), "blender_sha256": F(digest),
    })
    observer = O({"complete": F(bool), "scans": F(integer), "error_count": F(integer),
                  "first": F(Q(native_identity))})
    phase_record = O({"phase": F(S(20, values=PHASES)),
        "state": F(S(16, values=("ATTEMPTED", "VERIFIED"))),
        "observer": F(Q(observer)),
        "command": F(O({"terminal": F(O({"status": F(S(32, values=(
            "NOT_STARTED", "PROCESS_EXIT", "PROCESS_TIMEOUT", "PROCESS_CALL_ERROR"))),
            "returncode": F(signed, required=False), "outcome_known": F(bool, required=False)}),
            required=False)})),
        "verification": F(Q(verification_rule))})
    record_rule = O({
        "schema": F(L(SCHEMA)), "fixture_contract_sha256": F(L(pixels.FIXTURE_CONTRACT_SHA256)),
        "context": F(context_rule), "state": F(S(32, values=("INCOMPLETE", "PHYSICAL_ORACLES_OBSERVED"))),
        "phases": F(A(phase_record, 4)), "formal_acceptance": F(L(False)),
        "human_acceptance": F(L(False)), "public_upload_performed": F(L(False)),
        "automatic_retry": F(L(False)), "compiler_execution_requires_original_GHA_evidence": F(L(True)),
        "elapsed_seconds": F(N(0, 86400)), "caller_binary": F(file_binding, required=False),
        "decoder": F(O({"sha256": F(digest)}), required=False),
        "original_error": F(O({"type": F(S(96, r"[A-Za-z_][A-Za-z0-9_]*"))}), required=False),
        "source_inputs": F(O({name: F(file_binding) for name in ("seed", "project", "plan")}), required=False),
    })
    fraction = O({"num": F(signed), "den": F(N(1, 2**53 - 1, integer=True))})
    pixel_rule = O({
        "schema": F(L(pixels.PIXEL_OBSERVATION_SCHEMA)),
        "fixture_contract_sha256": F(L(pixels.FIXTURE_CONTRACT_SHA256)),
        "width": F(L(1920)), "height": F(L(1080)), "rgba_bytes_per_image": F(L(pixels.RGBA_BYTES)),
        "complete_pixels_per_image": F(L(1920 * 1080)), "opaque_every_pixel": F(bool),
        "decoded_sha256": F(O({role: F(digest) for role in pixels.ROLES})),
        "checks": F(O({key: F(bool) for key in (
            "edited_reopened_exact", "light_off_black", "original_not_black",
            "gold_old_strip_removed", "gold_new_strip_gained", "gold_displacement_centroid",
            "unedited_interiors_visible")})),
        "all_pixel_oracles_observed": F(bool),
        "gold_displacement_exact": F(Q(O({"x": F(fraction), "y": F(fraction)}))),
        "formal_acceptance": F(L(False)), "human_acceptance": F(L(False)),
    })
    decode_rule = O({
        "schema": F(L("motionwright.blender.lit-still-decode-receipt/1")),
        "role": F(S(16, values=pixels.ROLES)), "intent_sha256": F(digest),
        "decoder_sha256": F(digest), "native_render": F(L(False)),
        "state": F(S(16, values=("INCOMPLETE", "COMPLETE"))), "formal_acceptance": F(L(False)),
        "pid": F(integer, required=False), "exit_code": F(signed, required=False),
        "timeout": F(bool, required=False), "error_type": F(S(96), required=False),
        "raw_bytes": F(integer, required=False), "raw_sha256": F(digest, required=False),
        "all_pixels_decoded": F(bool, required=False), "original_fd_stable": F(bool, required=False),
        "decoder_fd_stable": F(bool, required=False), "seconds": F(N(0, 86400)),
    })
    decode_intent_rule = O({
        "schema": F(L("motionwright.blender.lit-still-decode-intent/1")),
        "role": F(S(16, values=pixels.ROLES)), "original": F(file_binding),
        "decoder_sha256": F(digest), "timeout_seconds": F(L(30)),
        "maximum_raw_bytes": F(L(pixels.RGBA_BYTES)), "native_render": F(L(False)), "retry": F(L(False)),
    })
    policies = {
        "lit-seed-v1": seed_rule, "lit-project-identity-v1": O({
            "id": F(uuid), "generation": F(uuid), "revision": F(integer)}),
        "lit-plan-v1": plan_rule, "lit-phase-v1": phase_rule,
        "lit-verification-v1": verification_rule, "lit-producer-v1": record_rule,
        "lit-pixels-v2": pixel_rule, "lit-decode-v1": decode_rule,
        "lit-decode-intent-v1": decode_intent_rule,
    }
    phase_map = {"create": "original", "edit": "edited", "reopen": "comparison", "light_control": "comparison"}
    entries = [
        ("seed/seed.json", "single", "origin", "seed", "json-original", "lit-seed-v1"),
        ("seed/source-project.json", "single", "origin", "project-identity", "json-projection", "lit-project-identity-v1"),
        ("seed/source-plan.json", "single", "origin", "plan", "json-projection", "lit-plan-v1"),
        ("motionwright.sqlite3", "single", "persistence", "closed-project-store", "original", None),
        ("producer-record.json", "single", "attempt-custody", "producer-terminal", "json-projection", "lit-producer-v1"),
        ("pixel-oracle.json", "comparison", "readback", "full-pixel-oracle", "json-projection", "lit-pixels-v2"),
        ("decoded/ffmpeg-version.txt", "single", "origin", "decoder-version", "original", None),
    ]
    for phase in PHASES:
        selected_phase, role = phase_map[phase], ROLES[phase].replace("_", "-")
        entries.extend((
            (f"{phase}/context/workspace/{NAMES[phase]}", selected_phase, "readback", role + "-png", "original", None),
            (f"{phase}/evidence/phase-evidence.json", selected_phase, "attempt-custody", role + "-terminal", "json-projection", "lit-phase-v1"),
            (f"{phase}/evidence/verified-phase.json", selected_phase, "readback", role + "-verification", "json-original", "lit-verification-v1"),
            (f"decoded/{ROLES[phase]}.rgba", selected_phase, "readback", role + "-rgba", "original", None),
            (f"decoded/{ROLES[phase]}.decode-receipt.json", selected_phase, "readback", role + "-decode", "json-projection", "lit-decode-v1"),
            (f"decoded/{ROLES[phase]}.decode-intent.json", selected_phase, "origin", role + "-decode-intent", "json-original", "lit-decode-intent-v1"),
        ))
        if phase in SAVED:
            entries.append((f"{phase}/context/workspace/{SAVED[phase]}", selected_phase,
                            "persistence", role + "-blend", "original", None))
    roles = {(phase, layer, role): (1, 1) for _, phase, layer, role, _, _ in entries}
    for suffix in ("wal", "shm", "journal"):
        roles[("single", "persistence", "absent-sqlite-" + suffix)] = (0, 0)
    require(len(entries) == 33 and len({row[0] for row in entries}) == 33,
            "lit-selection-fixed-roster")
    return policies, roles, entries


def build_selection_spec(owner: Path, source_root: Path, record: dict,
                         source_bindings: list[dict]) -> dict:
    import native_evidence_projection as selection
    _, _, entries = selection_contract()
    # Reuse producer-observed identities for native artifacts. Do not bless
    # replacement pixels or .blend bytes by taking a new expected digest.
    known = {row["path"]: {"bytes": row["bytes"], "sha256": row["sha256"]}
             for row in record.get("selected_original_files", [])}
    inputs = record.get("source_inputs", {})
    known.update({path: inputs[key] for path, key in (
        ("seed/seed.json", "seed"), ("seed/source-project.json", "project"),
        ("seed/source-plan.json", "plan")) if key in inputs})
    for phase in record["phases"]:
        verification = phase.get("verification")
        if verification is not None:
            known[f'{phase["phase"]}/evidence/phase-evidence.json'] = verification["original_evidence"]
    members = []
    for path, phase, layer, role, kind, policy in entries:
        expected = known.get(path)
        media = path.endswith((".blend", ".png", ".rgba"))
        if expected is None and not media:
            try:
                body, _ = capture_regular(owner / path, min(selection.MAX_FILE,
                                          selection.MAX_JSON if kind != "original" else selection.MAX_FILE))
                expected = binding(body)
            except (OSError, ValueError, AssertionError):
                expected = None
        if path.endswith(".rgba"):
            native_role = Path(path).stem
            decode = record.get("decodes", {}).get(native_role, {})
            if decode.get("state") == "COMPLETE":
                expected = {"sha256": decode["raw_sha256"], "bytes": decode["raw_bytes"]}
        members.append({"root": "case", "relative_path": path, "role": role, "layer": layer,
                        "phase": phase, "kind": kind, "required": True,
                        "expected_sha256": expected["sha256"] if expected else None,
                        "bytes": expected["bytes"] if expected else None, "policy": policy})
    exclusions = [{"root": "case", "relative_path": f"{phase}/{folder}" if phase == "seed-context" else f"{phase}/context/{folder}",
                   "reason": "outside-reviewed-scope"}
                  for phase in ("seed-context", *PHASES)
                  for folder in ("config", "home", "state", "scratch", "bin")]
    exclusions.extend({"root": "case", "relative_path": f"{phase}/evidence/{name}",
                       "reason": "outside-reviewed-scope"}
                      for phase in PHASES for name in ("native-phase.stdout", "native-phase.stderr", "semwrightd.log"))
    guards = [{"root": "case", "relative_path": "motionwright.sqlite3-" + suffix,
               "phase": "single", "layer": "persistence", "role": "absent-sqlite-" + suffix}
              for suffix in ("wal", "shm", "journal")]
    return {"schema": selection.SPEC_SCHEMA,
        "context": {**record["context"], "scenario": "blender-lit-still"},
        "roots": {"source": str(source_root), "case": str(owner)}, "members": members,
        "exclusions": exclusions, "absence_guards": guards, "source_bindings": source_bindings,
        "origin": {"kind": "generated-first-party-fixture", "fixture_id": "blender-lit-still",
                   "producer_paths": ["crates/native/examples/native-blender-lit-still-e2e.rs",
                                      "crates/native/src/production/blender_render.rs",
                                      "tooling/native_blender_lit_still_e2e.py",
                                      "tooling/native_blender_lit_still_oracle.py"],
                   "private_data": True, "upload_authorization": "NOT_GRANTED_BY_COLLECTOR"}}


def validate_lit_manifest(manifest: dict, expected_context: dict) -> dict:
    """Producer-specific fixed paths and WAL guards supplement the shared reader."""
    import native_evidence_projection as selection
    _, roles, entries = selection_contract()
    selection.validate_selected_manifest(manifest, expected_context=expected_context, role_contract=roles)
    expected = {(path, phase, layer, role, kind, policy) for path, phase, layer, role, kind, policy in entries}
    rows = manifest["members"]
    require(len(rows) == len(expected) and {
        (r["relative_path"], r["phase"], r["layer"], r["role"], r["kind"], r["policy"])
        for r in rows} == expected and all(r["root"] == "case" and r["required"] is True for r in rows),
        "lit-selected-role-path-substitution")
    guards = manifest.get("absence_guards", [])
    expected_guards = {("motionwright.sqlite3-" + suffix, "absent-sqlite-" + suffix)
                       for suffix in ("wal", "shm", "journal")}
    require(len(guards) == 3 and {(r["relative_path"], r["role"]) for r in guards} == expected_guards
            and all(r["root"] == "case" and r["phase"] == "single" and r["layer"] == "persistence"
                    and r["opened"] is False and r["hashed"] is False for r in guards),
            "lit-selected-store-sidecar-guard-substitution")
    closed = all(r["absent_before"] is True and r["absent_after"] is True for r in guards)
    return {"closed_store_sidecars_absent": closed,
            "complete": closed and manifest["required_layers_complete"]
                        and manifest["source_bytes_bound"] and manifest["selected_namespace_stable"],
            "formal_acceptance": False}


def capture_lit_evidence(owner: Path, source_root: Path, record: dict,
                         source_bindings: list[dict]) -> dict:
    import native_evidence_projection as selection
    # No native effect or retry occurs here, including on an incomplete attempt.
    retained.new_json(owner / "selection-intent.json", {
        "schema": "motionwright.blender.lit-still-selection-intent/1",
        "context": record["context"], "automatic_retry": False, "native_effects": 0})
    policies, roles, _ = selection_contract()
    spec = build_selection_spec(owner, source_root, record, source_bindings)
    retained.new_json(owner / "selection-spec.json", spec)
    tar, manifest = selection.capture_selected_originals(spec, policies=policies, role_contract=roles)
    require(len(tar) <= 256 * 1024 * 1024, "lit-selected-archive-budget")
    # Retain the collector's original result before interpreting completeness.
    # A failed/UNKNOWN native phase must not lose its selected attempt custody.
    tar_binding = retained.new_bytes(owner / "selected-evidence.tar", tar)
    manifest_binding = retained.new_json(owner / "selected-evidence-manifest.json", manifest)
    checked = {"complete": False, "closed_store_sidecars_absent": False}
    validation_error = None
    try:
        checked = validate_lit_manifest(manifest, spec["context"])
    except (ValueError, KeyError, TypeError) as error:
        validation_error = {"type": type(error).__name__}
    return {"tar": {"relative_path": "selected-evidence.tar", **tar_binding},
            "manifest": {"relative_path": "selected-evidence-manifest.json", **manifest_binding},
            "required_layers_complete": checked["complete"],
            "closed_store_sidecars_absent": checked["closed_store_sidecars_absent"],
            "source_bytes_bound": manifest.get("source_bytes_bound") is True,
            "selection_validation_error": validation_error,
            "selected_originals_and_closed_projections": True,
            "encrypted_or_uploaded": False, "formal_acceptance": False}


def run() -> int:
    import native_blender_e2e as app
    from native_evidence_projection import verify_source_bindings

    require(sys.platform == "linux" and os.geteuid() != 0
            and os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("CI") == "true"
            and os.environ.get("RUNNER_OS") == "Linux", "selected-unprivileged-Linux-GHA-required")
    source_sha = os.environ.get("GITHUB_SHA", "")
    run_id, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    require(COMMIT.fullmatch(source_sha) and run_id.isdecimal() and attempt == "1"
            and os.environ.get("MOTIONWRIGHT_EXPECTED_SOURCE_SHA") == source_sha,
            "exact-source-and-new-dispatch-required")
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    owner = runner_temp / ("mw-lit-" + run_id)
    owner.mkdir(mode=0o700, exist_ok=False)
    retained._plain_owned_directory(owner)
    record = {"schema": SCHEMA, "fixture_contract_sha256": pixels.FIXTURE_CONTRACT_SHA256,
              "context": {"source_sha": source_sha, "semwright_sha": app.PIN,
                          "run_id": run_id, "run_attempt": attempt},
              "state": "INCOMPLETE", "phases": [], "secondary_errors": [],
              "formal_acceptance": False, "human_acceptance": False,
              "compiler_execution_requires_original_GHA_evidence": True,
              "public_upload_performed": False, "automatic_retry": False}
    custody = Record(record)
    original_error = None
    source_bindings = None
    started = time.monotonic()
    try:
        retained.new_json(owner / "producer-intent.json", {
            "schema": SCHEMA, "context": record["context"], "state": "ATTEMPTED",
            "fixture_contract_sha256": pixels.FIXTURE_CONTRACT_SHA256,
            "phase_order": list(PHASES), "per_phase_timeout_seconds": 330,
            "automatic_retry": False, "public_upload_authorized_by_producer": False})
        source_bindings = [{"path": path, "sha256": app.digest(app.ROOT / path)}
                           for path in SOURCE_PATHS]
        record["source_before"] = verify_source_bindings(app.ROOT, source_sha, source_bindings)
        record["preflight"] = app.exact_inputs()
        executable = app.ROOT / "target/debug/examples/native-blender-lit-still-e2e"
        exe_bytes, _ = capture_regular(executable, MAX_EXE)
        require(exe_bytes.startswith(b"\x7fELF"), "caller-is-not-native-ELF")
        record["caller_binary"] = binding(exe_bytes)
        del exe_bytes
        decoder_name = shutil.which("ffmpeg")
        require(decoder_name is not None, "original-PNG-decoder-missing")
        decoder = Path(decoder_name).resolve(strict=True)
        decoder_bytes, _ = capture_regular(decoder, MAX_EXE)
        require(decoder_bytes.startswith(b"\x7fELF"), "decoder-is-not-native-ELF")
        decoder_sha = sha(decoder_bytes)
        del decoder_bytes
        record["decoder"] = {"sha256": decoder_sha, "path": str(decoder)}
        runtime = owner / "r"
        runtime.mkdir(mode=0o700)
        seed_root = owner / "seed-context"
        seed_root.mkdir(mode=0o700)
        seed_paths = app.new_owner_paths(seed_root, runtime_path=runtime / "0")
        seed_env = app.owned_environment(seed_paths)
        seed_output = owner / "seed"
        seed_output.mkdir(mode=0o700)
        database = owner / "motionwright.sqlite3"
        seed_command = {}
        record["seed_command"] = seed_command
        seeded = retained.captured_json([str(executable), "seed", str(database), str(seed_output)],
            env=seed_env, prefix=owner / "seed-command", record=seed_command, timeout=60)
        seeded_bytes, _ = capture_regular(seed_output / "seed.json", MAX_JSON)
        source_bytes, _ = capture_regular(seed_output / "source-project.json", MAX_JSON)
        plan_bytes, _ = capture_regular(seed_output / "source-plan.json", MAX_JSON)
        plan = check_seed(seeded, seeded_bytes, source_bytes, plan_bytes)
        record["source_inputs"] = {"seed": binding(seeded_bytes), "project": binding(source_bytes),
                                  "plan": binding(plan_bytes)}
        record["selected_original_files"] = [
            {"role": "source-project", "path": "seed/source-project.json", **binding(source_bytes)},
            {"role": "source-plan", "path": "seed/source-plan.json", **binding(plan_bytes)}]

        sources, images, observations, sessions = {}, {}, [], []
        for index, phase in enumerate(PHASES, 1):
            phase_root = owner / phase
            phase_root.mkdir(mode=0o700)
            context_root = phase_root / "context"
            context_root.mkdir(mode=0o700)
            paths = app.new_owner_paths(context_root, runtime_path=runtime / str(index))
            env = app.owned_environment(paths)
            evidence = phase_root / "evidence"
            evidence.mkdir(mode=0o700)
            request = {"project_id": seeded["project_id"], "expected": seeded["expected"],
                       "scene_id": seeded["scene_id"], "request_id": "blender-lit-" + run_id,
                       "phase": phase, "source": None, "edit_node_id": None}
            if phase != "create":
                prior = "edit" if phase == "reopen" else "create"
                prior_workspace, source = sources[prior]
                body, _ = measured_file(prior_workspace, source["file"], SAVED[prior], MAX_BLEND)
                retained.new_bytes(paths["workspace"] / SAVED[prior], body)
                request["source"] = source
                if phase in ("edit", "reopen"):
                    request["edit_node_id"] = seeded["role_node_ids"]["edited_gold_card"]
            request_path = evidence / "attempt.json"
            retained.new_json(request_path, request)
            item = {"phase": phase, "command": {}, "state": "ATTEMPTED",
                    "observer": None, "verification": None}
            record["phases"].append(item)
            retained.new_json(evidence / "controller-intent.json",
                              {"phase": phase, "request": request, "automatic_retry": False})
            context = None
            try:
                with app.blender_context(paths, env, evidence, seeded,
                                         required=set(COMMANDS)) as context:
                    item["runtime_bindings"] = context["runtime_bindings"]
                    observer = retained.LiveBlenderObserver(context["daemon"].pid,
                        app.BLENDER_BIN.resolve(strict=True),
                        context["runtime_bindings"]["blender_sha256"], paths["workspace"])
                    with retained.live_observation_scope(observer, item, custody, phase) as finish_observation:
                        # Freeze the exact executable identity before every invocation.
                        require(app.digest(executable) == record["caller_binary"]["sha256"],
                                "caller-changed-before-phase")
                        ack = retained.captured_json([str(executable), "phase", str(database),
                            str(context["connection"]), str(request_path),
                            str(evidence / "phase-evidence.json")],
                            env=env, prefix=evidence / "native-phase", record=item["command"], timeout=330)
                        observation = finish_observation()
                        require(observation.get("complete") is True, "live-native-observation-incomplete")
                        observations.append(observation)
                        persisted_bytes, _ = capture_regular(evidence / "phase-evidence.json", MAX_JSON)
                        result, verified = verify_phase(ack, persisted_bytes, request, plan, source_bytes)
                        image_bytes, image_descriptor = measured_file(
                            paths["workspace"], result["image"], NAMES[phase], pixels.PNG_LIMIT)
                        pixels.inspect_png(image_bytes)
                        record["selected_original_files"].append({
                            "role": ROLES[phase] + "-png",
                            "path": (paths["workspace"] / NAMES[phase]).relative_to(owner).as_posix(),
                            **{k: image_descriptor[k] for k in ("bytes", "sha256")}})

                        if request["source"] is not None:
                            measured_file(paths["workspace"], request["source"]["file"],
                                          request["source"]["file"]["relative_path"], MAX_BLEND)
                        if phase in SAVED:
                            measured_file(paths["workspace"], result["saved_source"]["file"],
                                          SAVED[phase], MAX_BLEND)
                            sources[phase] = (paths["workspace"], result["saved_source"])
                            record["selected_original_files"].append({
                                "role": phase + "-blend",
                                "path": (paths["workspace"] / SAVED[phase]).relative_to(owner).as_posix(),
                                **{k: result["saved_source"]["file"][k] for k in ("bytes", "sha256")}})

                        sessions.append(result["native_session"])
                        images[ROLES[phase]] = (paths["workspace"] / NAMES[phase], image_descriptor)
                        item.update(state="VERIFIED", verification=verified)
                        retained.new_json(evidence / "verified-phase.json", verified)
            finally:
                if context is not None:
                    item["context_cleanup"] = context["cleanup_errors"]
        record["native_contexts"] = distinct_contexts(observations, sessions)
        decoded = owner / "decoded"
        decoded.mkdir(mode=0o700)
        record["decoder"]["version"] = observe_decoder_version(decoder, decoder_sha, decoded, seed_env)
        rgba, decode_receipts = {}, {}
        record["decodes"] = decode_receipts
        for role in pixels.ROLES:
            path, descriptor = images[role]
            rgba[role], decode_receipts[role] = pixels.decode_original_png(
                role, path, {key: descriptor[key] for key in ("bytes", "sha256")},
                decoder, decoder_sha, decoded, environment=seed_env)
        record["decodes"] = decode_receipts
        oracle = pixels.observe_rgba(rgba)
        retained.new_json(owner / "pixel-oracle.json", oracle)
        record["pixel_oracle"] = oracle
        require(oracle["all_pixel_oracles_observed"] is True, "predeclared-pixel-oracle-failed")
        record["source_after"] = verify_source_bindings(app.ROOT, source_sha, source_bindings)
        require(app.digest(executable) == record["caller_binary"]["sha256"], "caller-changed-after-phases")
        record["public_selection_contract"] = {
            "collector": "tooling/native_evidence_projection.py", "version": 2,
            "raw_owner_export": False, "raw_diagnostics_export": False,
            "four_phase_typed_projection_required": True,
            "original_build_and_source_admission_required": True,
            "formal_or_human_acceptance_granted": False}
        record["state"] = "PHYSICAL_ORACLES_OBSERVED"
    except BaseException as error:
        original_error = error
        record["original_error"] = {"type": type(error).__name__, "message": str(error)}
        record["state"] = "INCOMPLETE"
    finally:
        record["elapsed_seconds"] = round(time.monotonic() - started, 6)
        try:
            retained.new_json(owner / "producer-record.json", record)
        except BaseException as error:
            custody.secondary("producer-record", error)
            if original_error is None:
                original_error = error
        selection_result = None
        selection_error = None
        if source_bindings is not None:
            try:
                selection_result = capture_lit_evidence(owner, app.ROOT, record, source_bindings)
                require(selection_result["required_layers_complete"] is True,
                        "selected-original-evidence-incomplete")
            except BaseException as error:
                selection_error = {"type": type(error).__name__}
                if original_error is None:
                    original_error = error
        else:
            selection_error = {"type": "SourceAdmissionNotCompleted"}
        try:
            retained.new_json(owner / "selection-terminal.json", {
                "schema": "motionwright.blender.lit-still-selection-terminal/1",
                "selection": selection_result, "error": selection_error,
                "original_native_terminal_replaced": False, "automatic_retry": False,
                "public_upload_performed": False, "formal_acceptance": False})
        except BaseException as error:
            if original_error is None:
                original_error = error
        print(json.dumps({"schema": SCHEMA, "state": record["state"] if original_error is None else "INCOMPLETE",
                          "private_owner": str(owner), "selected_evidence": selection_result, "completed_phases": sum(
                              x["state"] == "VERIFIED" for x in record["phases"]),
                          "public_upload_performed": False, "formal_acceptance": False}), flush=True)
    return 0 if original_error is None else 1


if __name__ == "__main__":
    raise SystemExit(run())
