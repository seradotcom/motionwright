#!/usr/bin/env python3
"""Order the existing Manim Graph source APIs and original owner operations.

The native wrapper owns the only daemon/session provisioning. This module
reuses that owner, the public bound-provider gateway and retained native PNGs.
"""
from __future__ import annotations
import argparse
import copy
import io
import json
import math
import os
from pathlib import Path
import shutil
import sys
import zipfile

from native_manim_camera_evidence import (
    JOB, SHA, object_bytes, private_bytes, private_json, read, read_object, require, sha,
)

PHASES = ("original", "reopen-original", "edited", "reopen-edited")
SHORT_PHASES = ("g1", "g2", "g3", "g4")
EVIDENCE_NAME = "motionwright-manim-graph-evidence.json"
GRAPH_COMMAND = "driver.motionwright.graph.upsert"
SOURCE_MAX = 16 * 1024 * 1024
ORIGINAL_LABEL, EDITED_LABEL = "Authored concept", "Edited concept"


def same(left, right):
    """JSON equality without treating a boolean as a numeric stamp."""
    options = dict(sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    try:
        return json.dumps(left, **options) == json.dumps(right, **options)
    except (ValueError, TypeError):
        return False


def scope(project):
    require(type(project) is dict, "graph_saved_project_missing")
    value = {name: project.get(name) for name in ("id", "generation", "active_branch", "revision")}
    require(all(type(value[name]) is str and value[name]
                for name in ("id", "generation", "active_branch"))
            and type(value["revision"]) is int and 0 <= value["revision"] < 2**64,
            "graph_saved_project_scope")
    return {"project_id": value["id"], "generation": value["generation"],
            "branch_id": value["active_branch"], "revision": value["revision"]}


def checked_pending(value, phase):
    pending = value.get("pending")
    require(type(pending) is dict
            and pending.get("schema") == "motionwright.manim.graph-cycle-pending/1"
            and pending.get("phase") == phase
            and pending.get("native_execution_performed") is False
            and pending.get("graph_write_performed") is False,
            "graph_pending_source_identity")
    project, graph = pending.get("project_before"), pending.get("graph")
    require(type(graph) is dict and type(project) is dict
            and same(graph.get("authorship", {}).get("source_snapshot"), scope(project)),
            "graph_pending_source_current_scope")
    return pending


def checked_saved(value, phase):
    require(value.get("schema") == "motionwright.manim.graph-cycle-saved/1"
            and value.get("phase") == phase and value.get("fresh_sqlite_readback") is True
            and value.get("render_performed") is False
            and value.get("native_source_reopen_verified") is False
            and value.get("human_acceptance") is False and value.get("automatic_retry") is False,
            "graph_saved_source_identity")
    target, plan = value.get("selection"), value.get("prepared_plan")
    require(type(target) is dict and type(plan) is dict
            and target.get("schema") == "motionwright.manim.graph-cycle-selection/1"
            and same(target.get("source_snapshot"), scope(value.get("project")))
            and same(plan.get("execution_snapshot"), target["source_snapshot"])
            and plan.get("source", {}).get("id") == target.get("graph_id")
            and plan["source"].get("scene_id") == target.get("scene_id")
            and plan["source"].get("variant_profile_ref") == target.get("deliverable_id")
            and plan.get("render_profile") == {"width": 640, "height": 360, "frame_rate": 12}
            and plan.get("scene_clock", {}).get("frame_count") == 24,
            "graph_saved_selection_plan")
    return target


def new_phase_inputs(context, evidence, database, output_root, env, *, capture, source_binary):
    phase, history = context["phase"], context["history"]
    if phase == "original":
        pending_path = evidence / "graph-source-pending.json"
        source = capture([str(source_binary), "seed-graph-cycle", str(database), str(pending_path)],
                         env=env, evidence=evidence, label="graph-source-seed", timeout=120)
        require(same(read_object(pending_path), source), "graph_source_seed_original_stdout")
        require(source.get("application") == "motionwright"
                and type(source.get("application_version")) is str
                and source.get("graph_persisted") is False and source.get("native_authority") is False,
                "graph_source_seed_identity")
        pending = checked_pending(source, "initial")
        saved_path, saved = None, None
    elif phase == "edited":
        original_phase = history[0]
        pending_path = evidence / "graph-source-pending.json"
        request = f"graph-source-{context['current']['run_id']}-{context['current']['run_attempt']}"
        source = capture([
            str(source_binary), "graph-cycle-edit-prepare", str(database),
            str(original_phase["native_path"]), str(original_phase["output_root"]),
            str(pending_path), request,
        ], env=env, evidence=evidence, label="graph-source-edit", timeout=120)
        require(same(read_object(pending_path), source), "graph_edit_original_stdout")
        pending = checked_pending(source, "label")
        source["application_version"] = history[0]["prepared"]["application_version"]
        saved_path, saved = None, None
    else:
        source_phase = history[0] if phase == "reopen-original" else history[2]
        source = source_phase["prepared"]
        saved_path, saved = source["saved_path"], source["saved"]
        checked_saved(saved, "initial" if phase == "reopen-original" else "label")
        pending_path, pending = None, None
    if pending is not None:
        project = pending["project_before"]
        application_version = source["application_version"]
    else:
        project = saved["project"]
        application_version = source["application_version"]
    seed = {"project_id": project["id"], "resource": "project:" + project["id"],
            "generation": project["generation"], "revision": project["revision"]}
    return {"seed": seed, "application_version": application_version,
            "pending_path": pending_path, "pending": pending,
            "saved_path": saved_path, "saved": saved, "input_path": None, "input_descriptor": None}


def provider_manifest(prepared, paths, source_binary, *, digest, evidence):
    """Describe the normal provider; a manifest is not an admission issuer."""
    require(source_binary.is_absolute() and source_binary.resolve(strict=True) == source_binary
            and source_binary.is_file() and not source_binary.is_symlink(),
            "graph_normal_provider_original")
    target = paths["bin"] / "motionwright-native-provider"
    original_sha = digest(source_binary)
    if target.exists():
        require(target.is_file() and not target.is_symlink()
                and digest(target) == original_sha, "graph_original_provider_changed")
    else:
        shutil.copyfile(source_binary, target)
        target.chmod(0o500)
    require(digest(target) == digest(source_binary) == original_sha, "graph_provider_copy_changed")
    value = {
        "manifest_version": 1, "protocol": 9, "id": "motionwright",
        "version": prepared["application_version"], "publisher": "motionwright",
        "executable": str(target), "sha256": original_sha,
        "application": {"desktop_id": None, "process_names": [], "supported_versions": []},
        "transport": "stdio_v1",
        "mounts": [{"root": "motionwright-data", "read_only": False, "execute": False}],
        "system_config": [], "secrets": [], "tools": [], "network": False,
        "nvidia_gpu": False, "loopback_port": None,
        "resources": {"open_files": 128, "processes": 16, "cpu_seconds": 60,
                      "operation_cpu_seconds": 0, "address_space_bytes": 536870912,
                      "file_size_bytes": 67108864},
        "request_timeout_ms": 15000,
        "interfaces": {"dynamic_capabilities": False, "cooperative_cancellation": True,
                       "events": False, "progress": False, "artifacts": False, "health": True,
                       "native_refs": True, "host_tools": False},
    }
    path = paths["config"] / "motionwright-driver.json"
    private_json(path, value)
    private_json(evidence / "graph-provider-identity.json", {
        "application": "motionwright", "application_version": prepared["application_version"],
        "binary_sha256": original_sha, "manifest_sha256": sha(read(path)),
        "selected_database": str(paths["motionwright-data"] / "motionwright.sqlite3"),
        "serialized_authority_used": False,
    })
    return path


class OriginalOwnerOperationError(ValueError):
    def __init__(self, envelope, process_error=None):
        super().__init__("original_graph_owner_operation_failed_no_retry")
        self.original_envelope = envelope
        self.original_process_error = process_error


def retain_capture(evidence, label, captured, *, secondary):
    """Retain present buffers and the observed state without fabricating absent keys."""
    errors = []
    metadata = {key: value for key, value in captured.items() if key not in ("stdout", "stderr")}
    for name in ("stdout", "stderr"):
        if name not in captured:
            continue
        raw = captured[name]
        require(type(raw) is bytes, "graph_owner_capture_not_binary")
        metadata[name + "_bytes"] = len(raw)
        metadata[name + "_sha256"] = sha(raw)
        try:
            private_bytes(evidence / f"{label}.{name}.original", raw)
        except BaseException as error:
            errors.append(error)
    try:
        private_json(evidence / f"{label}.process.original.json", metadata)
    except BaseException as error:
        errors.append(error)
    for error in errors:
        try:
            secondary("graph-owner-process-custody", error)
        except BaseException:
            pass
    if errors:
        raise errors[0]


def owner_json(cli, socket, session, command, args, evidence, label, *, secondary):
    from manim_native_readback import BoundedProcessExitError, run_bounded
    require(command in ("providers.mount.observe", "driver.motionwright.observe", "providers.execute_bound"),
            "graph_owner_command_not_selected")
    argument_path = evidence / f"{label}.args.original.json"
    private_json(argument_path, args)
    argument_raw = read(argument_path, 262144)
    argv = [str(cli), "--socket", str(socket), "--session-file", str(session),
            "--json", "execute", command, "--args-file", str(argument_path)]
    private_json(evidence / f"{label}.intent.json", {
        "argv": argv, "args_sha256": sha(argument_raw),
        "mutating": command == "providers.execute_bound",
        "client_environment": "inherited_existing_wrapper_process",
        "automatic_retry": False, "dispatch_invocations": 1,
    })
    captured = {}
    primary = None
    try:
        raw, _ = run_bounded(argv, 4 * 1024 * 1024, 60, capture=captured)
    except BaseException as error:
        primary = error
        terminal = captured.get("terminal")
        # Only the helper's final nonzero-exit rejection admits this SDK
        # envelope. A prior supervision error may leave a complete capture and
        # a killed child; those observations never replace the original error.
        # TERMINAL_OBSERVED alone also permits partial or failed pipe capture.
        complete_error_capture = (
            isinstance(error, BoundedProcessExitError)
            and captured.get("state") == "TERMINAL_OBSERVED"
            and type(terminal) is dict
            and type(terminal.get("exit_code")) is int and terminal["exit_code"] != 0
            and terminal.get("whole_process_completed") is True
            and terminal.get("pipes_closed") is True
            and terminal.get("stdin_complete") is True
            and terminal.get("timed_out") is False
            and terminal.get("output_budget_exceeded") is False
            and terminal.get("pipe_errors") == []
            and type(captured.get("stdout")) is bytes
            and type(captured.get("stderr")) is bytes
        )
        raw = captured["stdout"] if complete_error_capture else None
    custody_error = None
    try:
        retain_capture(evidence, label, captured, secondary=secondary)
    except BaseException as error:
        custody_error = error
    envelope = None
    if raw is not None:
        try:
            envelope = object_bytes(raw)
        except BaseException:
            if primary is None:
                raise
    # Preserve the SDK's whole error before reporting a generic process or
    # downstream custody error. No success or known outcome is inferred.
    if type(envelope) is dict and envelope.get("ok") is False:
        raise OriginalOwnerOperationError(envelope, primary) from primary
    if primary is not None:
        raise primary
    if custody_error is not None:
        raise custody_error
    require(type(envelope) is dict and envelope.get("ok") is True,
            "graph_owner_success_envelope_absent")
    require(read(argument_path, 262144) == argument_raw, "graph_owner_original_args_changed")
    return envelope, evidence / f"{label}.stdout.original"


def provider_result(result, capabilities, command, generation):
    require(type(result) is dict and result.get("ok") is True,
            "graph_original_provider_success_absent")
    execution = result.get("execution", {})
    provenance = execution.get("provenance", {})
    rows = [row for row in capabilities if row.get("id") == command]
    require(len(rows) == 1, "graph_source_capability_identity")
    row = rows[0]
    expected = row.get("provenance", {})
    require(execution.get("backend") == "driver:motionwright"
            and provenance.get("provider") == expected.get("provider") == "driver:motionwright"
            and provenance.get("source") == expected.get("source") == "driver"
            and type(provenance.get("provider_generation")) is int
            and type(generation) is int and generation >= 0
            and provenance["provider_generation"] == generation
            and provenance.get("descriptor_sha256") == expected.get("descriptor_sha256")
            and type(provenance.get("descriptor_sha256")) is str
            and SHA.fullmatch(provenance["descriptor_sha256"]) is not None
            and provenance.get("provider_version") == expected.get("source_version")
            and provenance.get("capability_version") == row.get("version"),
            "graph_original_native_provider_provenance")
    return result.get("data")


def application_result(envelope, capabilities, mounted):
    require(type(envelope) is dict and envelope.get("ok") is True, "graph_owner_success_absent")
    bound = envelope.get("data")
    require(type(bound) is dict and type(mounted) is dict
            and bound.get("admission_current") is True and bound.get("admission_state") == "current"
            and type(bound.get("binding_ref")) is str
            and bound["binding_ref"] == mounted.get("binding_ref"),
            "graph_bound_provider_result_absent")
    result = bound.get("result")
    data = provider_result(result, capabilities, GRAPH_COMMAND, mounted.get("provider_generation"))
    require(type(data) is dict and data.get("replayed") is False
            and type(data.get("request_id")) is str and bool(data["request_id"]),
            "graph_owner_original_application_result")
    return data


def apply_pending(context, prepared, database, output_root, env, evidence, socket, session,
                  capabilities, *, cli, source_binary, capture, secondary):
    if prepared["pending"] is not None:
        require(type(capabilities) is list
                and all(type(row) is dict for row in capabilities)
                and all(sum(row.get("id") == command for row in capabilities) == 1
                        for command in ("driver.motionwright.observe", GRAPH_COMMAND)),
                "graph_required_original_capabilities_absent")
        pending = prepared["pending"]
        mount, _ = owner_json(cli, socket, session, "providers.mount.observe",
                              {"provider": "driver:motionwright", "root": "motionwright-data"},
                              evidence, "graph-mount-observation", secondary=secondary)
        mounted = mount.get("data", {})
        require(type(mounted) is dict and mounted.get("provider") == "driver:motionwright"
                and type(mounted.get("provider_generation")) is int
                and mounted["provider_generation"] >= 0
                and type(mounted.get("connection")) is str and bool(mounted["connection"])
                and mounted.get("mount", {}).get("root") == "motionwright-data"
                and mounted["mount"].get("path") == str(database.parent)
                and mounted["mount"].get("read") is True
                and mounted["mount"].get("write") is True
                and mounted["mount"].get("execute") is False
                and type(mounted.get("binding_ref")) is str and bool(mounted["binding_ref"]),
                "graph_actual_owner_mount")
        observed, _ = owner_json(cli, socket, session, "driver.motionwright.observe", {
            "resource": "project:" + pending["project_before"]["id"], "scope": "summary", "limit": 1,
        }, evidence, "graph-project-observation", secondary=secondary)
        observation = provider_result(observed, capabilities, "driver.motionwright.observe",
                                      mounted["provider_generation"])
        require(type(observation) is dict, "graph_original_project_observation_absent")
        reference = observation.get("ref")
        require(type(reference) is str and reference.startswith("native:"), "graph_native_ref_absent")
        envelope, terminal_path = owner_json(cli, socket, session, "providers.execute_bound", {
            "binding_ref": mounted["binding_ref"],
            "request": {"command": GRAPH_COMMAND,
                        "args": {"ref": reference, "graph": pending["graph"]}, "dry_run": False},
        }, evidence, "graph-source-save", secondary=secondary)
        applied = application_result(envelope, capabilities, mounted)
        require(envelope["data"].get("binding_ref") == mounted["binding_ref"]
                and applied.get("resource") == "project:" + pending["project_before"]["id"]
                and applied.get("generation") == pending["project_before"]["generation"]
                and applied.get("previous_revision") == str(pending["project_before"]["revision"]),
                "graph_bound_save_original_scope")
        saved_path = evidence / "graph-source-saved.json"
        saved = capture([
            str(source_binary), "graph-cycle-save-readback", str(database),
            str(prepared["pending_path"]), str(terminal_path), str(saved_path),
        ], env=env, evidence=evidence, label="graph-source-readback", timeout=120)
        require(same(read_object(saved_path), saved), "graph_saved_original_stdout")
        checked_saved(saved, pending["phase"])
        require(saved["owner_request_id"] == applied["request_id"]
                and saved["owner_terminal_sha256"] == sha(read(terminal_path)),
                "graph_saved_owner_terminal_join")
        prepared.update(saved=saved, saved_path=saved_path)
    saved = prepared["saved"]
    checked_saved(saved, "initial" if context["phase"] in ("original", "reopen-original") else "label")
    prepared["seed"] = {"project_id": saved["project"]["id"],
                        "resource": "project:" + saved["project"]["id"],
                        "generation": saved["project"]["generation"], "revision": saved["project"]["revision"]}
    if context["phase"] != "original":
        if context["phase"] == "edited":
            exported = saved.get("current_export")
            require(type(exported) is dict, "graph_saved_export_absent")
            raw = read(Path(exported["path"]), SOURCE_MAX)
            expected_sha, expected_bytes = exported["sha256"], exported["bytes"]
        else:
            index = 0 if context["phase"] == "reopen-original" else 2
            previous = context["history"][index]["source"]
            raw = read(previous["original_path"], SOURCE_MAX)
            expected_sha, expected_bytes = previous["descriptor"]["sha256"], previous["descriptor"]["bytes"]
        require(sha(raw) == expected_sha and len(raw) == expected_bytes, "graph_original_input_changed")
        relative = "original-input.mwgraph.zip"
        private_bytes(output_root / relative, raw)
        descriptor = {"root": "output", "relative_path": relative,
                      "sha256": sha(raw), "bytes": len(raw)}
        path = evidence / "graph-source-input.json"
        private_json(path, descriptor)
        prepared.update(input_path=path, input_descriptor=descriptor)
    return prepared


def retained_source(native, prepared, evidence, output_root):
    require(native.get("native_manim_graph_e2e") == "PASS"
            and same(native.get("prepared_plan"), prepared["saved"]["prepared_plan"])
            and same(native.get("source_snapshot"), prepared["saved"]["selection"]["source_snapshot"]),
            "graph_original_native_source_scope")
    artifact = native.get("result", {}).get("artifact", {})
    custody = artifact.get("graph_source")
    require(type(custody) is dict and custody.get("source_and_getter_bytes_verified") is True
            and "graph_source_custody_error" not in artifact, "graph_original_source_custody")
    descriptor, closure = custody.get("source"), custody.get("source_closure")
    require(type(descriptor) is dict and type(closure) is dict
            and same(descriptor.get("closure"), closure)
            and closure.get("plan_sha256") == native["result"]["plan_sha256"],
            "graph_original_source_closure")
    job = native["result"]["job_ref"]
    match = JOB.fullmatch(job) if type(job) is str else None
    require(match is not None and descriptor.get("relative_path") == f"mw_{match[1]}.mwgraph.zip",
            "graph_original_source_job")
    raw = read(output_root / descriptor["relative_path"], SOURCE_MAX)
    require(sha(raw) == descriptor.get("sha256") and len(raw) == descriptor.get("bytes"),
            "graph_original_zip_digest")
    original_path = evidence / "graph-source-original.mwgraph.zip"
    private_bytes(original_path, raw)
    rows = closure.get("entries")
    require(type(rows) is list and 1 <= len(rows) <= 128, "graph_original_zip_entry_budget")
    by_path = {row.get("path"): row for row in rows if type(row) is dict}
    require(len(by_path) == len(rows), "graph_original_zip_entry_duplicate")
    entries = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        require(len(infos) == len(by_path) and len({row.filename for row in infos}) == len(infos)
                and {row.filename for row in infos} == set(by_path), "graph_original_zip_roster")
        total = 0
        for info in infos:
            row = by_path[info.filename]
            require(info.compress_type == zipfile.ZIP_STORED and not info.is_dir()
                    and info.file_size == row.get("bytes") and 0 < info.file_size <= SOURCE_MAX,
                    "graph_original_zip_entry")
            total += info.file_size
            require(total <= SOURCE_MAX, "graph_original_zip_expansion_budget")
            blob = archive.read(info)
            require(len(blob) == row["bytes"] and sha(blob) == row.get("sha256"),
                    "graph_original_zip_entry_digest")
            entries[info.filename] = blob
    plan = object_bytes(entries["plan.json"])
    require(same(plan, prepared["saved"]["prepared_plan"])
            and sha(entries["plan.json"]) == closure["plan_sha256"]
            and sha(entries["scene.py"]) == closure.get("source_sha256")
            and sha(entries["manifest.json"]) == closure.get("manifest_sha256"),
            "graph_original_zip_compiled_plan")
    fonts = {name: {"sha256": sha(blob), "bytes": len(blob)} for name, blob in entries.items()
             if name.startswith("graph-profile-v1/fonts/")}
    require(len(fonts) == 9 and sum(name.endswith("/LICENSE") for name in fonts) == 2,
            "graph_original_font_license_roster")
    if prepared["input_descriptor"] is not None:
        require(descriptor["sha256"] == prepared["input_descriptor"]["sha256"]
                and descriptor["bytes"] == prepared["input_descriptor"]["bytes"]
                and same(artifact.get("graph_input_source", {}).get("input"), prepared["input_descriptor"])
                and "graph_input_source_custody_error" not in artifact,
                "graph_native_original_bundle_reopen")
    report = {"schema": "motionwright.manim.graph.original-source-retention/1",
              "source_snapshot": native["source_snapshot"], "graph_id": native["graph_id"],
              "descriptor": descriptor, "closure": closure, "plan": plan, "font_originals": fonts,
              "native_source_getters": custody["readback"], "original_zip_sha256": sha(raw),
              "native_execution_authority": False, "native_pixel_acceptance": False}
    private_json(evidence / "graph-source-retention.json", report)
    return {**report, "original_path": original_path}


def pixels(roots, edit_path):
    # Reuse the existing PNG reader, byte/RGBA digest checks and codec-margin
    # comparator. It creates no renderer and never decodes the MP4 a second time.
    from native_manim_editable_pixels import rgba, load_phase, changed_text
    require(len(roots) == 4 and len(set(roots)) == 4, "graph_four_original_pixel_phases")
    edited = read_object(edit_path)
    target = checked_saved(edited, "label")
    graph = edited["prepared_plan"]["source"]
    vertices = graph.get("content", {}).get("vertices")
    require(type(vertices) is list and len(vertices) == 2
            and vertices[0].get("label") == EDITED_LABEL, "graph_edited_vertex_source")
    box = vertices[0]["box"]
    roi = (max(0, math.floor(box["x"]) - 24), max(0, math.floor(box["y"]) - 24),
           min(640, math.ceil(box["x"] + box["width"]) + 24),
           min(360, math.ceil(box["y"] + box["height"]) + 24))
    require(0 <= roi[0] < roi[2] <= 640 and 0 <= roi[1] < roi[3] <= 360,
            "graph_label_codec_region")
    appearance = vertices[0].get("appearance")
    require(appearance == {"at": {"num": "0", "den": "1"},
                           "duration": {"num": "1", "den": "12"}},
            "graph_first_label_visibility_clock")
    phases = [load_phase(root) for root in roots]
    rows = []
    for index in range(24):
        samples = [rgba(frame_root / f"{index:06}.png", report["frames"][index])
                   for report, frame_root, _, _ in phases]
        require(samples[0] == samples[1], "graph_original_reopen_pixel_difference")
        require(samples[2] == samples[3], "graph_edited_reopen_pixel_difference")
        row = {"index": index, "original_rgba_sha256": sha(samples[0]),
               "edited_rgba_sha256": sha(samples[2]), "both_reopens_equal": True}
        if index >= 1:
            row.update(changed_text(samples[0], samples[2], roi))
            row["fully_visible_label_edit_observed"] = True
        rows.append(row)
    original = rgba(phases[0][1] / "000023.png", phases[0][0]["frames"][23])
    try:
        changed_text(original, original, roi)
    except ValueError as error:
        require(str(error) == "editable_text_change_unobserved", "graph_original_negative_wrong_rejection")
        negative = {"frame": 23, "status": "EXPECTED_PIXEL_REJECTION",
                    "original_rgba_sha256": sha(original), "synthetic_pixels_used": False}
    else:
        raise ValueError("graph_original_pixels_satisfied_edited_label")
    for root, (_, _, _, raw) in zip(roots, phases):
        require(read(root / "native-video-originals/decoded-video.json") == raw,
                "graph_original_pixel_report_changed")
    import PIL
    from PIL import Image, _imaging
    decoder_files = [Path(Image.__file__).resolve(), Path(_imaging.__file__).resolve()]
    return {
        "schema": "motionwright.manim.graph.original-pixel-cycle/1",
        "status": "PASS_REQUIRES_SEALED_CUSTODY", "frame_count_per_phase": 24,
        "original_reopen_all_frames_equal": True, "edited_reopen_all_frames_equal": True,
        "fully_visible_label_frames": list(range(1, 24)),
        "label_change_observed_in_all_fully_visible_frames": True,
        "frame_zero_label_visibility_claimed": False,
        "label_region_with_codec_margin": list(roi),
        "maximum_outside_difference_policy": "outside <= text // 20 + 96",
        "edit_readback_sha256": sha(read(edit_path)), "graph_id": target["graph_id"],
        "original_media_as_edited_negative": negative, "frames": rows,
        "phases": {name: {"report_sha256": sha(row[3]), "artifact_sha256": row[2]["artifact_sha256"],
                          "source_sha256": row[2]["source_sha256"], "job_ref": row[2]["job_ref"]}
                   for name, row in zip(PHASES, phases)},
        "png_decoder": {"implementation": "Pillow", "version": PIL.__version__,
                        "files": [{"name": path.name, "bytes": path.stat().st_size,
                                   "sha256": sha(read(path, 32 * 1024 * 1024))}
                                  for path in decoder_files]},
        "native_video_decode_invocations": 0, "render_invocations": 0,
        "full_manim_fidelity_accepted": False, "human_acceptance": False,
    }


def finish_phase(context, prepared, native, evidence, output_root, work_root, database, env,
                 *, capture, source_binary, python, runtime, tooling, launch):
    require(same(read_object(evidence / EVIDENCE_NAME), native), "graph_native_original_stdout")
    source = retained_source(native, prepared, evidence, output_root)
    if context["history"]:
        require(same(source["font_originals"], context["history"][0]["source"]["font_originals"]),
                "graph_original_fonts_changed_between_phases")
    pixel_report = None
    if context["phase"] == "reopen-edited":
        for previous in context["history"]:
            verify_phase(previous, context["current"])
        path = evidence / "graph-pixel-cycle.json"
        pixel_env = dict(env, PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                         PYTHONPATH=str(runtime / "site-packages") + os.pathsep + str(tooling))
        pixel_report = capture([
            str(python), "-B", str(tooling / "native_manim_graph_cycle.py"), "pixels",
            *(str(row["evidence"]) for row in context["history"]), str(evidence),
            str(prepared["saved_path"]), str(path),
        ], env=pixel_env, evidence=evidence, label="graph-pixels", timeout=120)
        require(same(read_object(path), pixel_report)
                and pixel_report.get("status") == "PASS_REQUIRES_SEALED_CUSTODY"
                and pixel_report.get("frame_count_per_phase") == 24
                and pixel_report.get("original_reopen_all_frames_equal") is True
                and pixel_report.get("edited_reopen_all_frames_equal") is True
                and pixel_report.get("label_change_observed_in_all_fully_visible_frames") is True,
                "graph_original_pixel_cycle_incomplete")
    value = {
        "schema": "motionwright.manim.graph.native-phase/1",
        "status": "PASS_REQUIRES_SEALED_CUSTODY", "phase": context["phase"], "context": context["current"],
        "source_snapshot": native["source_snapshot"],
        "native_evidence_sha256": sha(read(evidence / EVIDENCE_NAME)),
        "source_retention_sha256": sha(read(evidence / "graph-source-retention.json")),
        "native_video_admission_sha256": sha(read(evidence / "native-video-admission.json")),
        "saved_source_readback_sha256": sha(read(prepared["saved_path"])),
        "native_request_id": native["request_id"], "native_job_ref": native["result"]["job_ref"],
        "source_bundle_input": prepared["input_descriptor"], "launch": launch,
        "native_render_invocations": 1, "graph_save_invocations": int(context["phase"] in ("original", "edited")),
        "native_result_original_retained": True,
        "original_pixel_cycle_sha256": sha(read(evidence / "graph-pixel-cycle.json")) if pixel_report else None,
        "human_acceptance": False, "automatic_retry": False,
    }
    private_json(evidence / "result.json", value)
    return {"source": source, "phase_observation": value, "pixel_report": pixel_report}


def verify_phase(phase, current):
    require(same(phase["current"], current), "graph_phase_context_changed")
    result = read_object(phase["evidence"] / "result.json")
    require(same(result, phase["phase_observation"])
            and result.get("status") == "PASS_REQUIRES_SEALED_CUSTODY"
            and result.get("native_render_invocations") == 1
            and result.get("native_result_original_retained") is True
            and result.get("automatic_retry") is False
            and result.get("native_evidence_sha256") == sha(read(phase["native_path"]))
            and result.get("source_retention_sha256") == sha(read(phase["evidence"] / "graph-source-retention.json"))
            and result.get("native_video_admission_sha256") == sha(read(phase["evidence"] / "native-video-admission.json")),
            "graph_phase_original_result_changed")
    custody = read_object(phase["evidence"] / "graph-custody.json")
    require(custody.get("schema") == "motionwright.manim.graph.sealed-custody/1"
            and custody.get("status") == "PASS" and same(custody.get("context"), current)
            and custody.get("selected_originals_complete") is True
            and custody.get("native_result_retained") is True and custody.get("secondary_error_count") == 0,
            "graph_phase_original_custody_incomplete")
    if "sealed_custody" in phase:
        require(same(custody, phase["sealed_custody"]), "graph_original_custody_changed")
    cipher = read(phase["sealed_originals"], 256 * 1024 * 1024)
    require(cipher.startswith(b"MWCISEAL1\n") and len(cipher) == custody.get("ciphertext_bytes")
            and sha(cipher) == custody.get("ciphertext_sha256"), "graph_original_ciphertext_changed")
    return custody


def run_source_cycle(args, *, run_phase, validate_phase):
    evidence_root, sealed_root = args.evidence_root, args.sealed_cycle_root
    require(isinstance(evidence_root, Path) and isinstance(sealed_root, Path)
            and args.sealed_originals is None and args.edited_evidence_root is None
            and args.sealed_edited is None, "graph_cycle_explicit_original_roots")
    probe = argparse.Namespace(**vars(args))
    probe.evidence_root = evidence_root
    probe.sealed_originals = sealed_root
    recipient, current = validate_phase(probe)
    require(evidence_root != sealed_root and evidence_root not in sealed_root.parents
            and sealed_root not in evidence_root.parents, "graph_cycle_roots_overlap")
    evidence_root.mkdir(mode=0o700, exist_ok=False)
    sealed_root.mkdir(mode=0o700, exist_ok=False)
    phase_args = [argparse.Namespace(**{
        **vars(args), "evidence_root": evidence_root / phase,
        "sealed_originals": sealed_root / (phase + ".mwseal"),
    }) for phase in PHASES]
    for item in phase_args:
        observed_recipient, observed_current = validate_phase(item)
        require(same(observed_recipient, recipient) and same(observed_current, current),
                "graph_cycle_preflight_context_changed")
    private_json(evidence_root / "cycle-intent.json", {
        "schema": "motionwright.manim.graph.cycle-intent/1", "context": current, "phases": list(PHASES),
        "native_render_invocations": 4, "bound_graph_save_invocations": 2,
        "automatic_retry": False, "next_phase_after_failure": False,
    })
    history = []
    for phase, short, item in zip(PHASES, SHORT_PHASES, phase_args):
        context = {"phase": phase, "short_phase": short, "history": history, "current": current,
                   "owner": history[0]["owner"] if history else None,
                   "database": history[0]["database"] if history else None}
        observed = run_phase(item, graph_cycle=context)
        observed["sealed_custody"] = verify_phase(observed, current)
        require(observed["phase_observation"]["phase"] == phase, "graph_cycle_phase_identity")
        if history:
            require(observed["owner"] == history[0]["owner"] and observed["database"] == history[0]["database"],
                    "graph_cycle_saved_store_changed")
        require(all(observed["native"]["request_id"] != row["native"]["request_id"]
                    and observed["native"]["result"]["job_ref"] != row["native"]["result"]["job_ref"]
                    and observed["launch"]["session_sha256"] != row["launch"]["session_sha256"]
                    for row in history), "graph_original_job_or_session_reused")
        history.append(observed)
    final = history[-1]["pixel_report"]
    require(type(final) is dict and final.get("status") == "PASS_REQUIRES_SEALED_CUSTODY",
            "graph_original_pixel_cycle_absent")
    saves = [row["prepared"]["saved"]["owner_request_id"] for row in (history[0], history[2])]
    require(len(set(saves)) == 2, "graph_original_bound_save_identity_reused")
    result = {
        "schema": "motionwright.manim.graph.source-cycle-observation/1", "status": "PASS",
        "context": current, "native_render_invocations": 4, "bound_graph_save_invocations": 2,
        "phase_results": [{"phase": row["phase_observation"]["phase"],
                           "result_sha256": sha(read(row["evidence"] / "result.json")),
                           "ciphertext_sha256": sha(read(row["sealed_originals"], 256 * 1024 * 1024))}
                          for row in history],
        "same_saved_store": True, "fresh_process_and_session_per_phase": True,
        "original_bundle_byte_preserving_native_reopen": True,
        "edited_bundle_byte_preserving_native_reopen": True,
        "all_24_decoded_frames_equal_for_both_reopens": True,
        "label_change_observed_in_frames_1_through_23": True,
        "original_fonts_and_licenses_preserved": True,
        "typed_source_edit_saved_through_current_bound_provider": True,
        "pixel_cycle_sha256": sha(read(history[-1]["evidence"] / "graph-pixel-cycle.json")),
        "full_manim_feature_acceptance": False, "human_acceptance": False, "automatic_retry": False,
    }
    private_json(evidence_root / "cycle-result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("pixels",))
    parser.add_argument("phase_roots", type=Path, nargs=4)
    parser.add_argument("edit_readback", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    value = pixels(args.phase_roots, args.edit_readback)
    private_json(args.report, value)
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
