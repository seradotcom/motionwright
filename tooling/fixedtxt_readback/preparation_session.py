#!/usr/bin/env python3
"""Independently review one mandatory FixedTxt context before its sole native call.

No renderer, Native call, workflow dispatch or API mutation is performed.
P1 format validity is necessary but insufficient: original source, clock,
fifteen supervised preparation children and runtime handoff are also checked.
Only a bounded new R28 dispatch may be written after those checks. It permits
one selected candidate24 context, with no A5, G2, F or product acceptance.
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import threading
import time
from types import SimpleNamespace

sys.dont_write_bytecode = True
MIB = 1024 * 1024
RECIPIENT = "dc27fc3142f7279289e36ee1900c64f04e61cf7f6a55823c1de85ecb87080585"
PURPOSE = "candidate24"
CLOCK_STEP = "Retain the one original clock before checkout and preparation"


class ReviewRejected(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise ReviewRejected(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path, expected, name, maximum=2 * MIB):
    with Path(path).open("rb") as stream:
        raw = stream.read(maximum + 1)
    need(len(raw) <= maximum and sha(raw) == expected, "reader_dependency_digest")
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader=None))
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def closed_path(value):
    need(type(value) is str and 0 < len(value) <= 1024 and value.startswith("/")
         and "\\" not in value and all(32 <= ord(c) < 127 for c in value), "original_posix_path")
    result = PurePosixPath(value)
    need(str(result) == value and ".." not in result.parts, "original_posix_path")
    return result


class Originals:
    """Only offsets are retained. Every temporary view is explicitly released."""
    def __init__(self, snapshot, bounded, inventory, base):
        self.snapshot, self.base = snapshot, base
        self.records = {row["archive_path"]: row for row in inventory}
        self.offsets = {}
        with bounded.BorrowedReader(snapshot) as reader, tarfile.open(fileobj=reader, mode="r:") as archive:
            for item in archive.getmembers():
                self.offsets[item.name] = (item.offset_data, item.size)
        need(set(self.offsets) == set(self.records), "original_member_index_changed")

    def view(self, name, maximum):
        need(name in self.records and name in self.offsets, "original_member_absent")
        start, size = self.offsets[name]
        row = self.records[name]
        need(type(maximum) is int and 0 <= size <= maximum and size == row["bytes"], "original_member_budget")
        whole = self.snapshot.view()
        try:
            value = whole[start:start + size]
        finally:
            whole.release()
        try:
            need(sha(value) == row["sha256"], "original_member_binding")
            return value
        except BaseException:
            value.release()
            raise

    def small(self, name, maximum=4 * MIB):
        need(maximum <= 4 * MIB, "json_or_PRQ_buffer_budget")
        value = self.view(name, maximum)
        try:
            return bytes(value)
        finally:
            value.release()

    def json(self, name, maximum=4 * MIB):
        return self.base.strict(self.small(name, maximum), maximum)

    def descriptor(self, name, maximum=256 * MIB):
        value = self.view(name, maximum)
        try:
            return {"bytes": len(value), "sha256": sha(value)}
        finally:
            value.release()


def expected_commands(route, fixed, base, request, context, bootstrap_raw, commands, cycle, runner_temp):
    """Evaluate the original pure command constructor with recorded POSIX roots.

    The only source edit replaces its one bootstrap filesystem read by the
    authenticated original buffer. No command is executed, no pathname opened.
    """
    need(type(commands) is list and len(commands) == 15 and all(type(row) is list for row in commands),
         "preparation_command_count")
    python = str(closed_path(commands[0][0]))
    script = closed_path(commands[0][4])
    need(script.name == "native_fixedtxt_workflow.py" and script.parent.name == "tooling", "preparation_source_script")
    root = script.parent.parent
    helper = closed_path(cycle["runtime"]["render_helper"]["path"])
    need(helper.parts[-4:] == ("integrations", "motion-canvas", "runtime", "render.mjs"), "recorded_SDK_runtime_root")
    sdk = helper.parents[3]
    raw = base.read_stable(Path(route.__file__), 2 * MIB)
    node = next(item for item in ast.parse(raw).body
                if isinstance(item, ast.FunctionDef) and item.name == "preparation_commands")
    source = ast.get_source_segment(raw.decode(), node)
    old = '(owned_layout(current)["control_root"] / "runtime-bootstrap.original.json").read_bytes()'
    need(source.count(old) == 1, "preparation_constructor_io_seam")
    source = source.replace(old, "_original_bootstrap", 1)
    scope = dict(route.__dict__)
    scope.update(Path=PurePosixPath, sys=SimpleNamespace(executable=python),
        os=SimpleNamespace(environ={"GITHUB_WORKSPACE": str(root), "SEMWRIGHT_CHECKOUT": str(sdk),
                                   "RUNNER_TEMP": runner_temp}, getuid=lambda: 1001, getgid=lambda: 1001),
        _original_bootstrap=bootstrap_raw,
        owned_layout=lambda current: {key: PurePosixPath(value) for key, value in
            fixed.owner_layout(current=current, runner_temp=runner_temp).items()})
    # The handoff owner IDs are observed in the authenticated command, then
    # independently bound to the retained handoff receipt below.
    handoff = commands[13]
    need(handoff[-4] == "--uid" and handoff[-2] == "--gid"
         and all(type(handoff[i]) is str and re.fullmatch(r"[1-9][0-9]{0,9}", handoff[i]) for i in (-3, -1)),
         "recorded_handoff_owner_ids")
    uid, gid = int(handoff[-3]), int(handoff[-1])
    scope["os"].getuid, scope["os"].getgid = lambda: uid, lambda: gid
    exec(compile(source, str(route.__file__) + "::offline-command-constructor", "exec"), scope)
    need(scope["preparation_commands"](request, context) == commands, "preparation_source_commands_differ")
    return {"root": str(root), "sdk": str(sdk), "python": python, "uid": uid, "gid": gid,
            "constructor_source_sha256": sha(raw)}


def checked_selection(request, context, *, expected_sdk, expected_sdk_tree, selection):
    arm, fixture, selected_frames = selection
    need(all(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value)
             for value in (expected_sdk, expected_sdk_tree)), "exact_reviewed_sdk_context")
    need((request.get("arm"), request.get("fixture"), request.get("purpose")) == (arm, fixture, PURPOSE)
         and request.get("semwright_sha") == context["semwright_sha"] == expected_sdk
         and request.get("semwright_tree") == expected_sdk_tree,
         "one_mandatory_candidate_selection")


def verify_observer_custody(originals, plan, overlay):
    bundle = {"inputs/" + name: originals.small("inputs/" + name, overlay.MAX_SOURCE_BYTES)
              for name in overlay.bundle_descriptors()}
    try:
        bound = overlay.verify_bundle_originals(bundle, plan=plan)
        need(bound.get("typed_source_bound") is True, "reviewed_observer_custody_required")
        return bound
    finally:
        bundle.clear()


def verify_owner_resource_preparation(originals, plan, fixed, context, runner_temp):
    serial = importlib.import_module("native_fixedtxt_session")
    names = {"request.original.json", "clock.original.json", *serial.SESSION_MEMBERS,
             "reused/control/clock.original.json"}
    metadata = {}
    for row in plan["members"]:
        name = row["relative_path"]
        if row["root"] == "control" and (name in names or name.startswith("owner-resources/")):
            maximum = 49152 if name in ("request.original.json", "session-template.original.json") else 65536
            metadata["control/" + name] = originals.small("control/" + name, maximum)
    return fixed.verify_owner_resource_prefix(metadata, plan=plan, current=context, runner_temp=runner_temp)


def verify_first_preparation(originals, route, fixed, g2, custody, base, context, payload,
                       *, expected_sdk, expected_sdk_tree, selection):
    arm, fixture, selected_frames = selection
    request_raw = originals.small("control/request.original.json", 49152)
    bootstrap_raw = originals.small("control/runtime-bootstrap.original.json", 16384)
    need(request_raw == payload["inputs"]["fixedtxt_request"].encode()
         and bootstrap_raw == payload["inputs"]["g2_admission"].encode(), "predispatch_original_bytes_differ")
    request = route.checked_request(request_raw, current=context)
    checked_selection(request, context, expected_sdk=expected_sdk, expected_sdk_tree=expected_sdk_tree, selection=selection)
    bootstrap = custody.runtime_preparation_dispatch(bootstrap_raw, context, scenario=route.SCOPE)
    need(bootstrap["recipient"]["sha256"] == RECIPIENT, "same_technical_recipient")
    plan_raw = originals.small("control/" + fixed.PLAN_MEMBER, 512 * 1024)
    runtime_raw = originals.small("inputs/" + fixed.RUNTIME_MEMBER, 65536)
    cycle_raw = originals.small("inputs/" + fixed.CYCLE_MEMBER, MIB)
    plan, runtime, cycle = (base.strict(raw) for raw in (plan_raw, runtime_raw, cycle_raw))
    overlay = importlib.import_module("native_fixedtxt_observer_overlay")
    observer_custody = verify_observer_custody(originals, plan, overlay)
    runner_temp = str(closed_path(runtime["fixedtxt_plan"]["path"]).parent.parent)
    owner_resources = verify_owner_resource_preparation(originals, plan, fixed, context, runner_temp)
    clock_raw = originals.small("control/clock.original.json", 65536)
    clock = base.strict(clock_raw)
    need(set(clock) == {"schema", "context", "boot_id", "started_monotonic", "deadline_monotonic", "request_sha256"}
         and clock["schema"] == "motionwright.native.fixedtxt-original-clock/1" and clock["context"] == context
         and type(clock["boot_id"]) is str and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", clock["boot_id"])
         and clock["request_sha256"] == sha(request_raw), "original_clock_binding")
    start, total = clock["started_monotonic"], clock["deadline_monotonic"]
    need(all(type(value) in (int, float) and math.isfinite(value) for value in (start, total))
         and 0 <= start < total and abs(total - start - request["limits"]["attempt_seconds"]) < 0.000001,
         "original_clock_duration")
    intent = originals.json("control/preparation-intent.original.json", MIB)
    need(set(intent) == {"context", "commands", "attempt_started_monotonic", "preparation_deadline_monotonic",
                       "attempt_deadline_monotonic", "stream_limit", "automatic_retry", "native_effects"}
         and intent["context"] == context and intent["automatic_retry"] is False
         and type(intent["native_effects"]) is int and intent["native_effects"] == 0
         and intent["attempt_started_monotonic"] == start and intent["attempt_deadline_monotonic"] == total
         and intent["preparation_deadline_monotonic"] == start + request["preparation_seconds"]
         and intent["stream_limit"] == request["preparation_stream_bytes"], "original_preparation_intent")
    roots = expected_commands(route, fixed, base, request, context, bootstrap_raw, intent["commands"], cycle, runner_temp)
    fields = {"schema", "phase", "argv", "automatic_retry", "process_started", "exit_code", "deadline_exceeded",
        "overflow", "native_success_inferred", "original_complete", "containment_empty", "attempt_started_monotonic",
        "attempt_deadline_monotonic", "stage_deadline_monotonic", "execution_deadline_monotonic"}
    previous, stream_total = start, 0
    for ordinal, command in enumerate(intent["commands"]):
        phase = f"prepare-{ordinal:02d}"
        stem = "control/preparation/" + phase
        declared = originals.json(stem + ".intent.json", 65536)
        spawned = originals.json(stem + ".spawned.json", 65536)
        terminal = originals.json(stem + ".terminal.json", 65536)
        deadline = total - request["upload_seconds"] if ordinal == 13 else start + request["preparation_seconds"]
        need(set(declared) == fields and declared["schema"] == "motionwright.native.fixedtxt-supervised-stage/1"
             and declared["phase"] == phase and declared["argv"] == command
             and declared["attempt_started_monotonic"] == start and declared["attempt_deadline_monotonic"] == total
             and declared["stage_deadline_monotonic"] == deadline and declared["exit_code"] is None
             and declared["containment_empty"] is None
             and all(declared[key] is False for key in ("automatic_retry", "process_started", "deadline_exceeded", "overflow",
                                                       "native_success_inferred", "original_complete")), "original_child_intent")
        execution = declared["execution_deadline_monotonic"]
        need(type(execution) in (int, float) and math.isfinite(execution)
             and start <= execution < deadline and 0 < deadline - execution <= 0.251, "original_child_clock")
        need(set(terminal) == fields | {"pid", "streams", "native_outcome", "terminal_observed_monotonic"}
             and all(terminal[key] == declared[key] for key in fields - {"process_started", "exit_code", "original_complete", "containment_empty"})
             and terminal["process_started"] is True and terminal["original_complete"] is True
             and terminal["containment_empty"] is True and type(terminal["exit_code"]) is int and terminal["exit_code"] == 0
             and terminal["native_outcome"] == "NOT_INFERRED", "original_child_not_complete_success")
        need(set(spawned) == {"pid", "automatic_retry"} and spawned["automatic_retry"] is False
             and type(spawned["pid"]) is int and spawned["pid"] > 0 and spawned["pid"] == terminal["pid"], "original_child_pid")
        finished = terminal["terminal_observed_monotonic"]
        need(type(finished) in (int, float) and math.isfinite(finished) and previous <= finished <= deadline,
             "original_child_sequence_or_deadline")
        previous = finished
        streams = terminal["streams"]
        need(type(streams) is dict and set(streams) == {"stdout", "stderr"}, "original_stream_roster")
        for stream, row in streams.items():
            physical = originals.descriptor(stem + "." + stream, request["preparation_stream_bytes"])
            need(set(row) == {"observed_bytes", "retained_bytes", "eof", "observed_sha256"}
                 and type(row["observed_bytes"]) is int and type(row["retained_bytes"]) is int
                 and row["observed_bytes"] == row["retained_bytes"] == physical["bytes"]
                 and row["eof"] is True and row["observed_sha256"] == physical["sha256"], "original_stream_binding")
            stream_total += physical["bytes"]
        wanted_cwd = (roots["sdk"] + "/integrations/motion-canvas/runtime" if command[0] in ("npm", "npx")
            else roots["sdk"] if command[0] == "cargo" and command[3] == roots["sdk"] + "/Cargo.toml" else roots["root"])
        need(originals.json(stem + ".cwd.json", 65536) == {"cwd": wanted_cwd}, "original_child_cwd")
    need(previous <= start + request["preparation_seconds"], "preparation_final_clock")
    copies = {"original_c_source": "intake/original_c_source", "repaired_c_source": "intake/repaired_c_source",
        "compiled_module": "intake/compiled_module", "compiler_receipt_path": "intake/compiler_receipt_path",
        "official_runtime_receipt_path": "intake/official-runtime-receipt.json",
        "installation_receipt_path": "handoff/installation.json"}
    for role, suffix in copies.items():
        need(originals.descriptor("inputs/runtime-originals/" + role) == originals.descriptor("runtime_custody/" + suffix),
             "original_runtime_copy_differs")
    installation = originals.json("runtime_custody/handoff/installation.json", 65536)
    need(installation.get("schema") == "motionwright.native.mlt-module-installed/1"
         and installation.get("actual_installed_module_sha256") == runtime["compiled_module_sha256"]
         and installation.get("original_module_sha256") == bootstrap["official_runtime"]["known_original_module_sha256"]
         and installation.get("original_backup_sha256") == installation["original_module_sha256"]
         and installation.get("module_replaced") is True and installation.get("new_native_master_issued") is False,
         "original_installation_semantics")
    handoff = originals.json("runtime_custody/handoff/handoff.json", MIB)
    need(handoff.get("schema") == "motionwright.native.mlt-receipt-handoff/1" and handoff.get("context") == context
         and handoff.get("installation_directory_status") == "OBSERVED" and handoff.get("installation_receipt_admitted") is True
         and handoff.get("custody_errors") == [] and handoff.get("recipient_uid") == roots["uid"] and handoff.get("recipient_gid") == roots["gid"]
         and handoff.get("original_permissions_changed") is False and handoff.get("producer_executed_as_root") is False
         and handoff.get("receipt_sha256") == runtime["installation_receipt_sha256"], "original_handoff_semantics")
    backup = originals.descriptor("runtime_custody/handoff/original_system_mlt_avformat.so", g2.admission.MAX_ELF_BYTES)
    need(backup["sha256"] == handoff["backup_sha256"] == installation["original_module_sha256"], "original_backup_binding")
    official = originals.json("inputs/runtime-originals/official_runtime_receipt_path", 512 * 1024)
    g2.verify_official_runtime_bindings(official, bootstrap["official_runtime"]["known_original_module_sha256"],
                                      bootstrap["official_runtime"]["known_melt_sha256"])
    need(plan["selected_frames"] == list(selected_frames) and plan["render_frames"] == 180 and plan["owner_count"] == 1,
         "one_fixture_physical_contract")
    return {"plan_raw": plan_raw, "runtime_raw": runtime_raw, "cycle_raw": cycle_raw,
        "request": request, "bootstrap": bootstrap, "roots": roots, "runner_temp": runner_temp,
        "summary": {"observer_derivation": observer_custody, "owner_resource_prefix": owner_resources, "fifteen_original_children_verified": True, "retained_stream_bytes": stream_total,
                    "native_before_review": False, "runtime_copy_and_handoff_verified": True,
                    "original_clock_sha256": sha(clock_raw), "original_start_monotonic": start,
                    "original_deadline_monotonic": total,
                    "receiver_deadline_monotonic": total - request["limits"]["custody_seconds"] - request["native_seconds"],
                    "preparation_finished_monotonic": previous, "live_clock_recheck_required": True,
                    "source_constructor_sha256": roots["constructor_source_sha256"]}}


def live_deadline(run, jobs, context, request, session_index=None):
    need(run.get("id") == int(context["run_id"]) and run.get("run_attempt") == 1
         and run.get("head_sha") == context["source_sha"] and run.get("event") == "workflow_dispatch"
         and run.get("status") == "in_progress" and run.get("path") == ".github/workflows/native-mlt-sequence-e2e.yml"
         and run.get("repository", {}).get("full_name") == "seradotcom/motionwright"
         and run.get("actor", {}).get("type") == "User", "original_live_run_metadata")
    chosen = [job for job in jobs.get("jobs", []) if job.get("run_id") == int(context["run_id"])
              and job.get("name") == ("fixedtxt-original-owner" if session_index is None else "fixedtxt-session-owner")]
    need(len(chosen) == 1 and chosen[0].get("status") == "in_progress", "original_owner_job_metadata")
    steps = [step for step in chosen[0].get("steps", []) if step.get("name") == CLOCK_STEP]
    need(len(steps) == 1 and steps[0].get("status") == "completed" and steps[0].get("conclusion") == "success",
         "original_clock_step_metadata")
    if session_index is not None:
        expected = "FixedTxt case %02d %s" % (session_index, request["fixture"])
        cases = [step for step in chosen[0].get("steps", []) if step.get("name") == expected]
        need(len(cases) == 1 and cases[0].get("status") == "in_progress" and cases[0].get("conclusion") is None,
             "original_session_case_not_current")
    t0_lower = datetime.fromisoformat(steps[0]["started_at"].replace("Z", "+00:00")).timestamp()
    run_start = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).timestamp()
    job_start = datetime.fromisoformat(chosen[0]["started_at"].replace("Z", "+00:00")).timestamp()
    need(run_start <= job_start <= t0_lower <= time.time() + 5, "original_clock_server_order")
    until = t0_lower + request["limits"]["attempt_seconds"] - request["limits"]["custody_seconds"] - request["native_seconds"]
    need(time.time() + 180 < until, "original_receiver_deadline_insufficient_delivery_reserve")
    return {"job_id": chosen[0]["id"], "clock_step_started_at": steps[0]["started_at"],
        "conservative_receiver_deadline_unix": until,
        "delivery_and_consumption_reserve_seconds": 180, "clock_reset": False}



def matching_source_modules(base, root, render_context):
    """Read current files only after equality with the original render commit.

    Each imported tooling module is checked before execution. HEAD is observed
    separately and never replaces source_sha in any retained run/P/R/Q record.
    """
    class MatchingSourceModules(base.SourceModules):
        def __init__(self):
            self.root, self.loaded = root, {}
            names = base.git(root, 'ls-tree', '-r', '--name-only', render_context['source_sha'], '--', 'tooling')
            need(len(names) <= 1024 * 1024, 'physical_original_tooling_roster_bound')
            self.tracked = set(names.decode('utf-8').splitlines())

        def exec_module(self, module):
            relative = 'tooling/' + module.__name__ + '.py'
            need(relative in self.tracked, 'physical_untracked_original_tooling_import')
            raw = base.read_stable(self.root / relative, 2 * MIB)
            original = base.git(self.root, 'show', render_context['source_sha'] + ':' + relative)
            need(len(original) <= 2 * MIB and raw == original, 'physical_tooling_not_original_relevant_source')
            self.loaded[relative] = {'sha256': sha(raw), 'bytes': len(raw)}
            module.__file__ = str(self.root / relative)
            module.__cached__ = None
            exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    return MatchingSourceModules()

def relevant_source_identity(root, render_context, base, source_paths):
    base.inspect_path(root, directory=True)
    original_sha = render_context['source_sha']
    need(base.git(root, 'rev-parse', '--verify', original_sha + '^{commit}').decode().strip() == original_sha,
         'physical_original_source_commit_unavailable')
    paths = sorted(set(base.FOUR_LOCKS) | set(source_paths))
    need(0 < len(paths) <= 4096 and all(type(path) is str and not path.startswith('/')
         and '\\' not in path and '..' not in path.split('/') for path in paths), 'physical_source_closure_roster')
    records = {}
    for relative in paths:
        original = base.git(root, 'show', original_sha + ':' + relative)
        need(0 < len(original) <= 16 * MIB, 'physical_original_source_blob_bound')
        current = base.read_stable(root / relative, 16 * MIB)
        need(current == original, 'physical_relevant_source_differs:' + relative)
        records[relative] = {'bytes': len(current), 'sha256': sha(current)}
    lock = base.strict(base.read_stable(root / 'SOURCE_LOCK.json', 4 * MIB))
    need(lock['dependencies']['semwright']['revision'] == render_context['semwright_sha'],
         'physical_relevant_source_sdk_differs')
    return {'render_source_sha': original_sha, 'semwright_sha': render_context['semwright_sha'],
            'render_source_tree': base.git(root, 'rev-parse', original_sha + '^{tree}').decode().strip(),
            'read_HEAD': base.git(root, 'rev-parse', '--verify', 'HEAD').decode().strip(),
            'relevant_files': records, 'relevant_files_sha256': sha(base.json_bytes(records)),
            'original_context_rewritten': False}

def same_relevant_source(before, after):
    return all(before[key] == after[key] for key in
        ('render_source_sha', 'semwright_sha', 'render_source_tree', 'relevant_files', 'relevant_files_sha256'))

FIXTURE_FRAMES = {
    'fixed-causal': (0, 45, 120, 179), 'flow-control-base': (45, 120, 179),
    'flow-control-with-fixed': (45, 120, 179), 'flow-text-control': (0, 45, 120, 179),
    'fixed-multirun': (0, 45, 120, 179), 'fixed-multirun-control': (0, 45, 120, 179),
    'fixed-transform-clip': (45, 120, 179), 'fixed-camera-scene': (0, 45, 120, 179),
    'fixed-counter-captions': (0, 29, 30, 45, 119, 120, 179),
    'fixed-layer-behind': (0, 45, 120, 179), 'fixed-layer-equal': (0, 45, 120, 179),
    'fixed-layer-front': (0, 45, 120, 179),
}


def selected_request(payload, selection, session_index, serial):
    arm, fixture, _ = selection
    inputs = payload['inputs']
    template = inputs['fixedtxt_request'].encode()
    if session_index is None:
        need(inputs['fixedtxt_mode'] == 'prepare' and inputs.get('fixedtxt_session_cases', '') == '',
             'original_single_case_mode')
        raw = template
    else:
        need(inputs['fixedtxt_mode'] == 'prepare-session'
             and inputs['fixedtxt_session_cases'] == 'all-twelve-originals'
             and type(session_index) is int and 0 <= session_index < 12
             and serial.ROSTER[session_index] == fixture, 'original_session_selection')
        raw = serial.derive_request(template, session_index)
    parsed = serial.strict(raw, 49152)
    need((parsed['arm'], parsed['fixture'], parsed['purpose']) == (arm, fixture, 'candidate24'),
         'original_closed_case_request')
    return raw


class FirstPreparationOriginals:
    """Alias identical current assets while retaining first paths and commands.

    Each alias must match the first snapshot's retained byte descriptor. No
    first original is fabricated, rewritten or inferred from a current flag.
    """
    def __init__(self, originals, reference, fixed):
        self.originals = originals
        self.base = originals.base
        self.expected = {}
        for row in reference['files']:
            key = row['root'] + '/' + row['relative_path']
            need(key not in self.expected, 'first_preparation_original_duplicate')
            self.expected[key] = {k: row[k] for k in ('bytes', 'sha256')}
        for key, name in (('P', 'control/' + fixed.PLAN_MEMBER), ('R', 'inputs/' + fixed.RUNTIME_MEMBER),
                          ('Q', 'inputs/' + fixed.CYCLE_MEMBER)):
            if name in self.expected:
                need(self.expected[name] == reference[key], 'first_preparation_PRQ_overlap')
            self.expected[name] = reference[key]
        self.records = {key: {'archive_path': key, **row} for key, row in self.expected.items()}

    def target(self, name):
        need(name in self.expected, 'first_preparation_original_not_declared')
        retained = 'control/reused/' + name
        return retained if retained in self.originals.records else name

    def view(self, name, maximum):
        value = self.originals.view(self.target(name), maximum)
        try:
            need({'bytes': len(value), 'sha256': sha(value)} == self.expected[name],
                 'first_preparation_original_bytes_differ')
            return value
        except BaseException:
            value.release()
            raise

    def small(self, name, maximum=4 * MIB):
        need(maximum <= 4 * MIB, 'first_original_small_bound')
        view = self.view(name, maximum)
        try:
            return bytes(view)
        finally:
            view.release()

    def json(self, name, maximum=4 * MIB):
        return self.base.strict(self.small(name, maximum), maximum)

    def descriptor(self, name, maximum=256 * MIB):
        view = self.view(name, maximum)
        try:
            return {'bytes': len(view), 'sha256': sha(view)}
        finally:
            view.release()


def session_from_snapshot(originals, plan, request_raw, clock_raw, serial, context, runner_temp):
    members = {(row['root'], row['relative_path']): row for row in plan['members']}
    raws = {}
    for name, maximum in serial.SESSION_MEMBERS.items():
        row = members.get(('control', name))
        need(row is not None and row['role'] == 'input-original' and type(row['expected_sha256']) is str
             and 0 < row['maximum'] <= maximum, 'review_session_original_not_independently_pinned')
        raw = originals.small('control/' + name, maximum)
        need(sha(raw) == row['expected_sha256'], 'review_session_original_changed')
        raws[name] = raw
    return serial.validate_clock_bundle(raws, request_raw, clock_raw, current=context, runner_temp=runner_temp)


def verify_shared_preparation(originals, reviewed, context, serial):
    identity_raw = originals.small('control/shared-identity.original.json', MIB)
    readonly_raw = originals.small('control/shared-readonly.original.json', MIB)
    identity, readonly = (serial.strict(raw, MIB) for raw in (identity_raw, readonly_raw))
    roots, request = reviewed['roots'], reviewed['request']
    need(set(identity) == {'schema', 'context', 'arm', 'source', 'semwright_tree', 'native_source_sha256', 'binaries', 'readonly_original'}
         and identity['schema'] == 'motionwright.native.fixedtxt-shared-identity/1'
         and identity['context'] == context and identity['arm'] == request['arm']
         and identity['source'] == originals.json('control/source-after.original.json', 4 * MIB)
         and identity['semwright_tree'] == request['semwright_tree']
         and identity['readonly_original'] == {'bytes': len(readonly_raw), 'sha256': sha(readonly_raw)},
         'shared_original_source_identity')
    cycle = serial.strict(reviewed['cycle_raw'], MIB)
    need(identity['native_source_sha256'] == cycle['runtime']['native_source_sha256'][request['arm']],
         'shared_original_observer_arm')
    need(set(readonly) == {'schema', 'context', 'targets', 'observed_monotonic'}
         and readonly['schema'] == 'motionwright.native.fixedtxt-shared-readonly/1' and readonly['context'] == context
         and type(readonly['targets']) is list and len(readonly['targets']) == 2,
         'shared_original_readonly_shape')
    targets = readonly['targets']
    expected_targets = [roots['root'], str(PurePosixPath(roots['python']).parent.parent)]
    need([row['path'] for row in targets] == expected_targets, 'shared_readonly_original_roots')
    for row in targets:
        need(set(row) == {'path', 'identity', 'mounts'} and type(row['identity']) is list
             and len(row['identity']) == 2 and all(type(v) is int and v >= 0 for v in row['identity'])
             and type(row['mounts']) is list and len(row['mounts']) == 1, 'shared_readonly_inode_binding')
        mount = row['mounts'][0]
        need(set(mount) == {'mount_id', 'parent_id', 'device', 'root', 'mountpoint', 'options', 'fs_type', 'source', 'super_options'}
             and mount['mountpoint'] == row['path'] and 'ro' in mount['options'] and 'rw' not in mount['options']
             and type(mount['mount_id']) is str and re.fullmatch('[1-9][0-9]*', mount['mount_id']),
             'shared_original_mount_not_readonly')
    binaries = identity['binaries']
    need(type(binaries) is list and len(binaries) == 11, 'shared_original_binary_roster')
    by_path = {}
    for row in binaries:
        need(type(row) is dict and set(row) == {'path', 'bytes', 'sha256', 'device', 'inode', 'mode'}
             and row['path'] not in by_path and str(closed_path(row['path'])) == row['path']
             and type(row['bytes']) is int and 0 < row['bytes'] <= 512 * MIB
             and re.fullmatch('[0-9a-f]{64}', row['sha256'])
             and all(type(row[k]) is int and row[k] >= 0 for k in ('device', 'inode', 'mode'))
             and row['mode'] & 0o111, 'shared_original_binary_identity')
        by_path[row['path']] = row
    sdkbin = roots['sdk'] + '/target/debug/'
    required = {sdkbin + name for name in ('semwright', 'semwrightd', 'semwright-sandbox',
        'semwright-motion-canvas-driver', 'semwright-mlt-video-driver', 'semwright-mlt-runtime-runner')}
    required |= {sdkbin + 'examples/fixed_text_compile_v4', roots['root'] + '/target/debug/examples/native-film180-e2e',
                 '/usr/bin/python3.12'}
    need(required <= set(by_path) and len(set(by_path) - required) == 2, 'shared_original_exact_built_programs')
    need(by_path[sdkbin + 'semwright-motion-canvas-driver']['sha256'] == cycle['runtime']['driver_sha256']
         and by_path[sdkbin + 'examples/fixed_text_compile_v4']['sha256'] == cycle['compiler_binary_sha256'],
         'shared_original_compiler_driver_Q_binding')
    remaining = [row for path, row in by_path.items() if path not in required]
    need(sorted(row['sha256'] for row in remaining) == sorted((cycle['runtime']['node_sha256'], cycle['decoder_sha256'])),
         'shared_original_node_decoder_Q_binding')
    # Four real mount children use the same bounded cgroup supervisor. They are
    # separate originals; the fifteen original preparation children stay fifteen.
    start = reviewed['summary']['original_start_monotonic']
    total = reviewed['summary']['original_deadline_monotonic']
    deadline = start + request['preparation_seconds']
    previous = reviewed['summary']['preparation_finished_monotonic']
    fields = {'schema', 'phase', 'argv', 'automatic_retry', 'process_started', 'exit_code', 'deadline_exceeded',
        'overflow', 'native_success_inferred', 'original_complete', 'containment_empty', 'attempt_started_monotonic',
        'attempt_deadline_monotonic', 'stage_deadline_monotonic', 'execution_deadline_monotonic'}
    for n in range(4):
        phase = 'prepare-%02d' % (15 + n)
        target = expected_targets[n // 2]
        command = (['sudo', '-n', 'mount', '--bind', target, target] if n % 2 == 0
                   else ['sudo', '-n', 'mount', '-o', 'remount,bind,ro', target])
        stem = 'control/shared-freeze/' + phase
        intent = originals.json(stem + '.intent.json', 65536)
        terminal = originals.json(stem + '.terminal.json', 65536)
        spawned = originals.json(stem + '.spawned.json', 65536)
        need(set(intent) == fields and intent['schema'] == 'motionwright.native.fixedtxt-supervised-stage/1'
             and intent['phase'] == phase and intent['argv'] == command
             and intent['attempt_started_monotonic'] == start and intent['attempt_deadline_monotonic'] == total
             and intent['stage_deadline_monotonic'] == deadline and intent['exit_code'] is None
             and intent['containment_empty'] is None and all(intent[k] is False for k in ('automatic_retry',
                 'process_started', 'deadline_exceeded', 'overflow', 'native_success_inferred', 'original_complete')),
             'shared_original_freeze_intent')
        need(set(terminal) == fields | {'pid', 'streams', 'native_outcome', 'terminal_observed_monotonic'}
             and all(terminal[k] == intent[k] for k in fields - {'process_started', 'exit_code', 'original_complete', 'containment_empty'})
             and terminal['process_started'] is True and terminal['exit_code'] == 0
             and terminal['original_complete'] is True and terminal['containment_empty'] is True
             and terminal['native_outcome'] == 'NOT_INFERRED', 'shared_original_freeze_terminal')
        need(set(spawned) == {'pid', 'automatic_retry'} and type(spawned['pid']) is int and spawned['pid'] > 0
             and spawned['pid'] == terminal['pid'] and spawned['automatic_retry'] is False, 'shared_original_freeze_pid')
        finished, execution = terminal['terminal_observed_monotonic'], terminal['execution_deadline_monotonic']
        need(all(type(v) in (int, float) and math.isfinite(v) for v in (finished, execution))
             and previous <= finished <= deadline and start <= execution < deadline
             and 0 < deadline - execution <= .251, 'shared_original_freeze_clock')
        previous = finished
        need(type(terminal['streams']) is dict and set(terminal['streams']) == {'stdout', 'stderr'}, 'shared_freeze_stream_roster')
        for name, row in terminal['streams'].items():
            actual = originals.descriptor(stem + '.' + name, 65536)
            need(set(row) == {'observed_bytes', 'retained_bytes', 'eof', 'observed_sha256'}
                 and row['observed_bytes'] == row['retained_bytes'] == actual['bytes']
                 and row['eof'] is True and row['observed_sha256'] == actual['sha256'], 'shared_freeze_stream_binding')
    need(type(readonly['observed_monotonic']) in (int, float) and math.isfinite(readonly['observed_monotonic'])
         and previous <= readonly['observed_monotonic'] <= deadline, 'shared_original_mount_observation_clock')
    return {'shared_identity': {'bytes': len(identity_raw), 'sha256': sha(identity_raw)},
            'readonly_original': {'bytes': len(readonly_raw), 'sha256': sha(readonly_raw)},
            'eleven_actual_executables_bound': True, 'four_original_freeze_children_verified': True,
            'first_preparation_reexecuted': False}


def verify_first_physical_receipt(raw, expected_sha256, first_closed, first_reference, context, selection, reader_sha256, first_dispatch_raw, first_dispatch_sha256, fixed):
    need(type(raw) is bytes and 0 < len(raw) <= 4 * MIB and sha(raw) == expected_sha256,
         'first_physical_retained_receipt_digest')
    value = fixed.strict(raw, 4 * MIB)
    arm = selection[0]
    need(value.get('reader_sha256') == reader_sha256
         and value.get('status') == 'AUTHENTICATED_ONE_CANDIDATE_PHYSICAL_READBACK_WITH_FROZEN_MEASUREMENTS'
         and value.get('terminal_phase') == 'complete' and value.get('context') == context
         and value.get('native_calls') == 0 and value.get('automatic_retry') is False
         and value.get('candidate_selection', {}).get('arm') == arm
         and value['candidate_selection'].get('fixture') == 'fixed-multirun-control'
         and value['candidate_selection'].get('request_sha256') == first_reference['request']['sha256']
         and value.get('candidate24_executed_contexts') == 1 and value.get('candidate24_accepted') == 0
         and value.get('G2_accepted') is False and value.get('F_accepted') is False
         and value.get('public_original_sha256') == first_closed['selected_public']['sha256']
         and {k: value.get('envelope', {}).get(k) for k in ('bytes', 'sha256')} == first_closed['selected_envelope'],
         'first_physical_original_context_binding')
    need(type(first_dispatch_raw) is bytes and 0 < len(first_dispatch_raw) <= 16384
         and sha(first_dispatch_raw) == first_dispatch_sha256 == value['external_dispatch_sha256'],
         'first_physical_original_R28_digest')
    first_dispatch = fixed.validate_dispatch(first_dispatch_raw, context)
    commitment = first_dispatch['fixedtxt']
    need((commitment['arm'], commitment['fixture']) == (arm, 'fixed-multirun-control')
         and commitment['plan_original'] == first_reference['P']
         and commitment['runtime_original'] == first_reference['R']
         and commitment['cycle_original'] == first_reference['Q'],
         'first_physical_PRQ_provenance_only')
    physical = value.get('physical_readback', {})
    need(physical.get('mandatory_original_PNGs_checked') == 180
         and physical.get('selected_RGBA_decoded_and_exactly_equal') == 4
         and [row['frame'] for row in physical.get('selected_frames', [])] == [0, 45, 120, 179]
         and physical.get('typed_probe_adapted') is True
         and physical.get('typed_original_adaptation', {}).get('all_selected_geometry_and_font_text_bound') is True,
         'first_physical_required_observations_incomplete')
    return {'bytes': len(raw), 'sha256': sha(raw), 'original_PNGs': 180, 'actual_decodes': 4,
            'numeric_verdicts_preserved': True, 'G2_accepted': False}


def verify_preparation(originals, route, fixed, g2, custody, base, context, payload,
                       *, expected_sdk, expected_sdk_tree, selection, session_index,
                       first_physical_raw=None, first_physical_sha256=None, physical_reader_sha256=None,
                       first_dispatch_raw=None, first_dispatch_sha256=None):
    serial = importlib.import_module('native_fixedtxt_session')
    expected_raw = selected_request(payload, selection, session_index, serial)
    request_raw = originals.small('control/request.original.json', 49152)
    need(request_raw == expected_raw, 'selected_case_original_request_differs')
    if session_index is None:
        need(not any('control/' + name in originals.records for name in serial.SESSION_MEMBERS),
             'single_case_foreign_session_originals')
        return verify_first_preparation(originals, route, fixed, g2, custody, base, context, payload,
            expected_sdk=expected_sdk, expected_sdk_tree=expected_sdk_tree, selection=selection)
    plan_raw = originals.small('control/' + fixed.PLAN_MEMBER, 512 * 1024)
    runtime_raw = originals.small('inputs/' + fixed.RUNTIME_MEMBER, 65536)
    cycle_raw = originals.small('inputs/' + fixed.CYCLE_MEMBER, MIB)
    plan, runtime, cycle = (base.strict(raw) for raw in (plan_raw, runtime_raw, cycle_raw))
    runner_temp = str(closed_path(runtime['fixedtxt_plan']['path']).parent.parent)
    clock_raw = originals.small('control/clock.original.json', 65536)
    session = session_from_snapshot(originals, plan, request_raw, clock_raw, serial, context, runner_temp)
    need(session['binding']['index'] == session_index, 'review_session_index_differs')
    if session_index == 0:
        reviewed = verify_first_preparation(originals, route, fixed, g2, custody, base, context, payload,
            expected_sdk=expected_sdk, expected_sdk_tree=expected_sdk_tree, selection=selection)
        shared = verify_shared_preparation(originals, reviewed, context, serial)
        reviewed['summary'].update(session=session, shared_preparation=shared, new_preparation_children=15,
                                   first_physical_review_required_before_next_R28=True)
        return reviewed
    reference_raw = originals.small('control/first-prepared.original.json', 256 * 1024)
    reference = base.strict(reference_raw, 256 * 1024)
    first = session['plan']['cases'][0]
    need(set(reference) == {'schema','context','request','namespace','P','R','Q','files','new_native_authority'}
         and reference['schema'] == 'motionwright.native.fixedtxt-first-prepared/1'
         and reference['context'] == context and reference['request'] == first['request']
         and reference['namespace'] == first['namespace'] and reference['new_native_authority'] is False,
         'first_prepared_original_context')
    historical = FirstPreparationOriginals(originals, reference, fixed)
    initial = verify_first_preparation(historical, route, fixed, g2, custody, base, context, payload,
        expected_sdk=expected_sdk, expected_sdk_tree=expected_sdk_tree,
        selection=(selection[0], 'fixed-multirun-control', FIXTURE_FRAMES['fixed-multirun-control']))
    shared = verify_shared_preparation(historical, initial, context, serial)
    for name in ('shared-identity.original.json', 'shared-readonly.original.json'):
        need(originals.descriptor('control/' + name, MIB) == historical.descriptor('control/' + name, MIB),
             'reused_shared_original_identity_differs')
    receipt = originals.json('control/preparation-reuse.original.json', MIB)
    need(set(receipt) == {'schema','context','case_request','first_prepared','first_namespace','shared_identity',
        'shared_readonly','retained_original_prefix','new_preparation_children','original_preparation_children',
        'new_native_authority','observed_monotonic','quiescent'}
         and receipt['schema'] == 'motionwright.native.fixedtxt-preparation-reuse/1' and receipt['context'] == context
         and receipt['case_request'] == session['binding']['request']
         and receipt['first_prepared'] == {'bytes': len(reference_raw), 'sha256': sha(reference_raw)}
         and receipt['first_namespace'] == first['namespace'] and receipt['shared_identity'] == shared['shared_identity']
         and receipt['shared_readonly'] == shared['readonly_original']
         and receipt['retained_original_prefix'] == 'control/reused/'
         and receipt['new_preparation_children'] == 0 and receipt['original_preparation_children'] == 15
         and receipt['new_native_authority'] is False, 'original_reuse_provenance')
    need(not any(name.startswith('control/preparation/') for name in originals.records),
         'reused_preparation_must_not_claim_new_fifteen_children')
    def without_paths(value):
        if type(value) is dict:
            return {k: without_paths(v) for k, v in value.items() if k != 'path'}
        if type(value) is list:
            return [without_paths(v) for v in value]
        return value
    need(without_paths(cycle) == without_paths(base.strict(initial['cycle_raw'], MIB)), 'reused_Q_material_inputs_changed')
    clock = base.strict(clock_raw, 65536)
    request = route.checked_request(request_raw, current=context)
    checked_selection(request, context, expected_sdk=expected_sdk, expected_sdk_tree=expected_sdk_tree, selection=selection)
    need(type(receipt['observed_monotonic']) in (int, float) and math.isfinite(receipt['observed_monotonic'])
         and clock['started_monotonic'] <= receipt['observed_monotonic'] < serial.preparation_deadline(clock, request, session),
         'reused_original_preparation_deadline')
    groups = receipt['quiescent']
    need(type(groups) is list and [row.get('phase') for row in groups] == ['preparation','work','custody']
         and all(row.get('populated') is False and type(row.get('device')) is int and type(row.get('inode')) is int
                 for row in groups), 'reused_prior_owner_not_quiescent')
    closed = originals.json('control/first-case-closed.original.json', 65536)
    need(closed.get('schema') == 'motionwright.native.fixedtxt-session-case-closed/1' and closed.get('context') == context
         and closed.get('index') == 0 and closed.get('request') == first['request']
         and closed.get('mechanics_complete') is True and closed.get('native_acceptance') is False
         and closed.get('automatic_retry') is False, 'first_case_closure_original')
    first_physical = verify_first_physical_receipt(first_physical_raw, first_physical_sha256,
        closed, reference, context, selection, physical_reader_sha256, first_dispatch_raw, first_dispatch_sha256, fixed)
    overlay = importlib.import_module('native_fixedtxt_observer_overlay')
    observer = verify_observer_custody(originals, plan, overlay)
    owner_resources = verify_owner_resource_preparation(originals, plan, fixed, context, runner_temp)
    need(plan['selected_frames'] == list(selection[2]) and plan['render_frames'] == 180 and plan['owner_count'] == 1,
         'reused_one_fixture_physical_contract')
    return {'plan_raw': plan_raw, 'runtime_raw': runtime_raw, 'cycle_raw': cycle_raw, 'request': request,
        'bootstrap': initial['bootstrap'], 'roots': initial['roots'], 'runner_temp': runner_temp,
        'summary': {'observer_derivation': observer, 'owner_resource_prefix': owner_resources, 'original_preparation': initial['summary'],
                    'shared_preparation': shared, 'first_physical_readback': first_physical,
                    'fifteen_original_children_verified': True, 'new_preparation_children': 0,
                    'runtime_copy_and_handoff_verified': True, 'native_before_review': False,
                    'session': session, 'original_clock_sha256': sha(clock_raw),
                    'original_start_monotonic': clock['started_monotonic'],
                    'original_deadline_monotonic': clock['deadline_monotonic'],
                    'effective_deadline_monotonic': session['effective_deadline_monotonic'],
                    'receiver_deadline_monotonic': session['effective_deadline_monotonic'] - request['limits']['custody_seconds'] - request['native_seconds'],
                    'preparation_finished_monotonic': receipt['observed_monotonic'], 'live_clock_recheck_required': True}}



class PreparationOutput:
    """Retain a permission only inside this invocation's exclusive directory."""

    NAMES = frozenset({"review.started.json", "dispatch.intent.json",
                       "dispatch.original.json", "review.terminal.json"})

    def __init__(self, base, value, source, disk_options):
        path = base.absolute_e(value, private=True)
        need(not os.path.lexists(path) and not path.is_relative_to(source),
             "new_private_output_required")
        base.inspect_path(path.parent, directory=True)
        base.disk_admission(path.parent, **disk_options)
        path.mkdir(mode=0o700)
        # Constructor failure never gives the error handler another owner's path.
        base.inspect_path(path, directory=True)
        observed = path.lstat()
        self.path, self.base, self.disk_options = path, base, disk_options
        self.identity = (observed.st_dev, observed.st_ino)

    def _guard(self):
        self.base.inspect_path(self.path, directory=True)
        observed = self.path.lstat()
        need((observed.st_dev, observed.st_ino) == self.identity,
             "preparation_output_identity_changed")

    def write_raw(self, name, raw):
        need(name in self.NAMES, "preparation_output_name")
        self._guard()
        self.base.new_bytes(self.path / name, raw, disk_options=self.disk_options)

    def write(self, name, value):
        self.write_raw(name, self.base.json_bytes(value))

    def read_dispatch(self):
        self._guard()
        return self.base.read_stable(self.path / "dispatch.original.json", 16384)


def main(*, configure_private_paths=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("io-adapter", "io-adapter-sha256", "p1-helper", "bounded-helper", "approved-helper", "source-root",
                 "expected-source", "expected-sdk", "expected-sdk-tree", "envelope", "envelope-sha256", "private-key", "run-id", "prepared-payload",
                 "prepared-payload-sha256", "run-original", "jobs-original", "output-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument('--arm', choices=('RED','GREEN'), required=True)
    parser.add_argument('--fixture', choices=tuple(FIXTURE_FRAMES), required=True)
    parser.add_argument('--session-index', type=int)
    parser.add_argument('--first-physical-receipt')
    parser.add_argument('--first-physical-receipt-sha256')
    parser.add_argument('--first-external-dispatch')
    parser.add_argument('--first-external-dispatch-sha256')
    parser.add_argument('--physical-reader')
    parser.add_argument('--physical-reader-sha256')
    args = parser.parse_args()
    arm, fixture = args.arm, args.fixture
    selected_frames = FIXTURE_FRAMES[fixture]
    selection = (arm, fixture, selected_frames)
    first_physical_raw = first_dispatch_raw = None
    snapshot = None
    output = None
    base = None
    phase = "arguments"
    state = {"schema": "motionwright.fixedtxt.bounded-preparation-review/1", "R28_issued": False,
        "private_plaintext_written": False, "native_calls": 0, "native_acceptance": False, "A5_acceptance": False,
        "F_acceptance": False, "automatic_retry": False}
    try:
        seam = load(args.io_adapter, args.io_adapter_sha256, "fixedtxt_bounded_io")
        validate, bounded, p1, io_scope = seam.make_oracle(args.p1_helper, args.bounded_helper)
        base = bounded.load_approved_helper(args.approved_helper)
        if configure_private_paths is not None:
            need(callable(configure_private_paths), "private_path_policy_callback")
            state["authorized_private_path_binding"] = configure_private_paths(base)
        else:
            state["authorized_private_path_binding"] = {"policy": "approved_helper_defaults",
                                                       "additional_roots": False}
        need(os.name == "nt", "windows_required")
        limiter = bounded.ProcessMemoryLimit()
        bounded.require_reserve()
        context = {"source_sha": args.expected_source, "semwright_sha": args.expected_sdk, "run_id": args.run_id, "run_attempt": "1"}
        source = base.absolute_e(args.source_root)
        disk_options = {"storage_mode": "reuse-selected", "max_new_bytes": 128 * MIB, "disk_floor_gib": 10}
        output = PreparationOutput(base, args.output_dir, source, disk_options)
        state.update(context=context, io_scope=io_scope, reader_sha256=sha(base.read_stable(Path(__file__).resolve(), 2 * MIB)))
        output.write("review.started.json", state)
        phase = "source_and_original_payload"
        relevant_source_identity(source, context, base, ())
        loader = matching_source_modules(base, source, context)
        sys.meta_path.insert(0, loader)
        seal = importlib.import_module("ci_private_evidence")
        core = importlib.import_module("native_evidence_projection")
        fixed = importlib.import_module("native_fixedtxt_g2_admission")
        route = importlib.import_module("native_fixedtxt_workflow")
        g2 = importlib.import_module("native_mlt_g2_observation")
        custody = importlib.import_module("native_mlt_g2_custody")
        source_lock = importlib.import_module("source_lock_admission")
        source_lock.verify_source_lock(source, source_lock.load_source_lock(source))
        before = relevant_source_identity(source, context, base, fixed.source_paths())
        source_files = {name: sha(base.read_stable(source / name, 16 * MIB)) for name in fixed.source_paths()}
        tree = before['render_source_tree']
        serial = importlib.import_module('native_fixedtxt_session')
        payload_raw = base.read_stable(base.absolute_e(args.prepared_payload), 65536)
        need(sha(payload_raw) == args.prepared_payload_sha256, "prepared_payload_digest")
        payload = base.strict(payload_raw, 65536)
        selected_request_raw = selected_request(payload, selection, args.session_index, serial)
        request = route.checked_request(selected_request_raw, current=context)
        if args.session_index not in (None, 0):
            need(all(value is not None for value in (args.first_physical_receipt, args.first_physical_receipt_sha256,
                args.first_external_dispatch, args.first_external_dispatch_sha256, args.physical_reader, args.physical_reader_sha256)),
                'first_physical_originals_required_before_reuse_permission')
            first_physical_raw = base.read_stable(base.absolute_e(args.first_physical_receipt, private=True), 4 * MIB)
            first_dispatch_raw = base.read_stable(base.absolute_e(args.first_external_dispatch, private=True), 16384)
            need(sha(base.read_stable(base.absolute_e(args.physical_reader), 2 * MIB)) == args.physical_reader_sha256,
                 'first_physical_reader_original_source')
        else:
            need(all(value is None for value in (args.first_physical_receipt, args.first_physical_receipt_sha256,
                args.first_external_dispatch, args.first_external_dispatch_sha256, args.physical_reader, args.physical_reader_sha256)),
                'first_case_must_not_reuse_a_foreign_execution')
        need(request["source_tree"] == tree, "predispatch_source_tree")
        checked_selection(request, context, expected_sdk=args.expected_sdk, expected_sdk_tree=args.expected_sdk_tree, selection=selection)
        run_raw = base.read_stable(base.absolute_e(args.run_original), 65536)
        jobs_raw = base.read_stable(base.absolute_e(args.jobs_original), 4 * MIB)
        deadline = live_deadline(base.strict(run_raw), base.strict(jobs_raw), context, request, args.session_index)
        phase = "envelope_and_memory_admission"
        envelope = base.absolute_e(args.envelope, private=True)
        key = base.absolute_e(args.private_key, private=True)
        reference = base.envelope_reference(envelope, args.envelope_sha256)
        need(reference["bytes"] <= request["review_capsule_bytes"] <= 256 * MIB, "original_review_capsule_budget")
        prefix = base.read_stable(envelope, base.MAX_ENVELOPE, prefix=len(base.MAGIC) + 4 + 8192)
        header = base.header_from_prefix(prefix, seal, context, RECIPIENT)
        plan = bounded.memory_plan(header["plaintext_bytes"], bounded.available_memory())
        need(plan["admitted"], "bounded_memory_not_admitted")
        state.update(memory_preflight=plan, envelope=reference, original_clock=deadline)
        phase = "authenticated_original_decryption"
        key_raw = base.read_stable(key, 65536)
        private_key = seal.serialization.load_pem_private_key(key_raw, password=None)
        del key_raw
        snapshot = bounded.decrypt_retained(envelope, reference, header, private_key, seal, base)
        del private_key
        phase = "unchanged_P1_offline_oracle"
        inventory, p1_result = validate(snapshot, expected_context=context, expected_source_tree=tree,
                                       expected_source_files=source_files, seal=seal, core=core)
        phase = "independent_preparation_semantics"
        originals = Originals(snapshot, bounded, inventory, base)
        reviewed = verify_preparation(originals, route, fixed, g2, custody, base, context, payload,
            expected_sdk=args.expected_sdk, expected_sdk_tree=args.expected_sdk_tree,
            selection=selection, session_index=args.session_index,
            first_physical_raw=first_physical_raw, first_physical_sha256=args.first_physical_receipt_sha256,
            physical_reader_sha256=args.physical_reader_sha256, first_dispatch_raw=first_dispatch_raw,
            first_dispatch_sha256=args.first_external_dispatch_sha256)
        # Originals owns offsets only. The three <=1.6MiB P/R/Q buffers survive;
        # no view or log payload survives the wiped authenticated snapshot.
        del originals
        snapshot.close()
        snapshot = None
        phase = "final_source_ciphertext_and_clock"
        after = relevant_source_identity(source, context, base, fixed.source_paths())
        need(same_relevant_source(before, after), "relevant_source_changed_during_review")
        loader.verify_unchanged()
        need(base.envelope_reference(envelope, args.envelope_sha256) == reference, "original_ciphertext_changed")
        need(base.read_stable(base.absolute_e(args.prepared_payload), 65536) == payload_raw
             and base.read_stable(base.absolute_e(args.run_original), 65536) == run_raw
             and base.read_stable(base.absolute_e(args.jobs_original), 4 * MIB) == jobs_raw, "review_input_changed")
        if first_physical_raw is not None:
            need(base.read_stable(base.absolute_e(args.first_physical_receipt, private=True), 4 * MIB) == first_physical_raw
                 and base.read_stable(base.absolute_e(args.first_external_dispatch, private=True), 16384) == first_dispatch_raw
                 and sha(base.read_stable(base.absolute_e(args.physical_reader), 2 * MIB)) == args.physical_reader_sha256,
                 "first_physical_originals_changed_during_review")
        bounded.require_reserve()
        need(time.time() + 180 < deadline["conservative_receiver_deadline_unix"], "original_receiver_deadline_elapsed")
        memory = limiter.observe()
        # This new external commitment follows original semantics, never P1-only
        # PASS. The unchanged producer validates it again before its sole owner.
        original_plan = base.strict(reviewed["plan_raw"])
        commitment = {"schema": fixed.COMMITMENT_SCHEMA, "admission_id": request["admission_id"],
            "context": context, "scope": fixed.SCOPE, "arm": arm, "fixture": fixture, "single_owner": 1,
            "plan_original": fixed.descriptor(reviewed["plan_raw"]), "runtime_original": fixed.descriptor(reviewed["runtime_raw"]),
            "cycle_original": fixed.descriptor(reviewed["cycle_raw"]), "limits": request["limits"],
            "wrapper_sha256": next(row["sha256"] for row in original_plan["source_bindings"]
                                   if row["path"] == "tooling/native_film180_original_g2.py")}
        dispatch = {**reviewed["bootstrap"], "schema": fixed.DISPATCH_SCHEMA, "fixedtxt": commitment}
        dispatch_raw = fixed.canonical(dispatch)
        need(len(dispatch_raw) <= 16384, "R28_original_delivery_bound")
        fixed.admit_inputs(dispatch_raw, reviewed["plan_raw"], reviewed["runtime_raw"], reviewed["cycle_raw"],
                           current=context, runner_temp=reviewed["runner_temp"])
        state.update(status="AUTHENTICATED_ONE_CANDIDATE_PREPARATION_REVIEWED", terminal_phase="complete",
            p1_result=p1_result, independent_preparation=reviewed["summary"], os_memory_limit=memory,
            source_identity=before, source_tree=tree, verifier_source_files=loader.loaded,
            verified_original_members=len(inventory), original_inventory_sha256=sha(base.json_bytes(inventory)),
            prepared_payload_sha256=sha(payload_raw), run_original_sha256=sha(run_raw), jobs_original_sha256=sha(jobs_raw),
            selected_request_original=fixed.descriptor(selected_request_raw), session_index=args.session_index,
            read_HEAD_at_completion=after['read_HEAD'],
            selected_profile={"arm": arm, "fixture": fixture, "purpose": PURPOSE, "frames": 180,
                              "width": 1920, "height": 1080, "fps": "30/1", "selected_frames": list(selected_frames)},
            new_R28_dispatch=fixed.descriptor(dispatch_raw),
            reviewer="independent technical preparation review; no historical A5 signature adopted",
            physical_font_color_baseline="PENDING_SELECTED_CANDIDATE_NATIVE_OUTPUT",
            candidate24_executed_contexts=0, candidate24_total_contexts=24, candidate24_accepted=0)
        output.write("dispatch.intent.json", {"context": context,
            "dispatch_original": fixed.descriptor(dispatch_raw), "automatic_retry": False,
            "meaning": "ONE_CANDIDATE_NATIVE_PERMISSION_NO_PRODUCT_ACCEPTANCE"})
        output.write_raw("dispatch.original.json", dispatch_raw)
        need(output.read_dispatch() == dispatch_raw, "R28_original_write_readback")
        state["R28_issued"] = True
        output.write("review.terminal.json", state)
        print(json.dumps({"status": state["status"], "receipt_sha256": sha(base.json_bytes(state)),
            "R28_sha256": sha(dispatch_raw), "R28_bytes": len(dispatch_raw), "scope": "ONE_CANDIDATE_ORIGINAL_" + arm + "_" + fixture,
            "receiver_deadline_unix": deadline["conservative_receiver_deadline_unix"], "native_acceptance": False}, sort_keys=True))
        return 0
    except BaseException as error:
        code = str(error) if isinstance(error, ReviewRejected) or (isinstance(error, ValueError) and re.fullmatch(r"[a-z][a-z0-9_]{0,119}", str(error))) else "failed_closed"
        state.update(status="PREPARATION_REVIEW_INCOMPLETE", terminal_phase=phase,
                     error_code=code, error_type=type(error).__name__)
        if output is not None:
            try:
                output.write("review.terminal.json", state)
            except BaseException:
                pass
        print(json.dumps({"status": state["status"], "phase": phase, "code": state["error_code"], "R28_issued": state["R28_issued"],
                          "native_calls": 0, "automatic_retry": False}, sort_keys=True))
        return 2
    finally:
        if snapshot is not None:
            snapshot.close()


if __name__ == "__main__":
    raise SystemExit(main())
