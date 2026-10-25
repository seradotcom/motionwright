"""Assertion and service-transport definitions extracted verbatim from the retained executed probe.
Deployment preflight and command-line handling are retained with the original private execution.
This module performs no product execution when imported.
"""
import sys

sys.dont_write_bytecode = True

import argparse

import copy

import datetime

import hashlib

import json

import os

import platform

from pathlib import Path, PureWindowsPath

import queue

import subprocess

import threading

import traceback

import uuid

SOURCE_SHA = "f0805ade6df0aa4fdf593400c386a97ea54c37e3"

SOURCE_TREE = "ec4e51f2e4aea8a439ada43fc7d4dd1a2c577959"

CARGO_LOCK_SHA = "3d93b2ed700a67cbda781601b11d6bc3053e5697558775a68e699526a941e685"

SOURCE_LOCK_SHA = "2d830383f3df0731effb531cf713020be1ce4ced69ec66465fae86f94c2d740d"

TITLE = "Synthetic narrative persistence exercise"

PREMISE = "A synthetic three-part explanation; no factual or creative acceptance."

BRIEF = {
    "objective": "Exercise persisted authored references through two scene moves.",
    "audience": "Synthetic test audience",
    "constraints": ["Keep authored references", "Keep exact scene durations"],
    "exclusions": ["No human review", "No factual certification"],
}

INITIAL_CLAIMS = [
    {"text": "Synthetic observation A", "source": {"kind": "manual_note", "label": "Synthetic source A version one"}, "context": "Authored fixture only", "source_revision": "fixture-A-v1"},
    {"text": "Synthetic observation B", "source": {"kind": "manual_note", "label": "Synthetic source B version one"}, "context": "Authored fixture only", "source_revision": "fixture-B-v1"},
]

FINAL_CLAIMS = copy.deepcopy(INITIAL_CLAIMS)

FINAL_CLAIMS[0]["source"] = {"kind": "manual_note", "label": "Synthetic source A version two"}

FINAL_CLAIMS[0]["source_revision"] = "fixture-A-v2"

PARTS = [
    ("A", "Orient", "Explain synthetic observation A", 2, [0]),
    ("B", "Compare", "Explain synthetic observation B", 3, [1]),
    ("C", "Connect", "Connect the two synthetic observations", 5, [0, 1]),
]

LIMITATIONS = [
    "Service JSONL and real SQLite only; no installed Tauri or native rendering.",
    "Authored synthetic data; no truth, creative quality, rights or human approval.",
    "Narrative and scene-local beats have distinct identities; no direct identity link is invented.",
    "No closure of concept/representation, cue alignment, beat-reorder impact, shared-state continuity or UI conflict presentation.",
    "A successful probe requires separate original-clause sufficiency and independent acceptance review.",
]

class ProbeFailure(RuntimeError):
    pass

def require(condition, message):
    if not condition:
        raise ProbeFailure(message)

def json_equal(left, right):
    """JSON types stay significant: true, 1 and 1.0 must not be conflated."""
    try:
        options = {"sort_keys": True, "separators": (",", ":"), "allow_nan": False, "ensure_ascii": False}
        return json.dumps(left, **options) == json.dumps(right, **options)
    except (TypeError, ValueError):
        return False

def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()

def file_ref(path):
    data = Path(path).read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": sha_bytes(data)}

def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def rational(seconds):
    return {"num": str(seconds), "den": "1"}

def beat_input(key, claim_ids):
    _, label, objective, duration, refs = next(part for part in PARTS if part[0] == key)
    return {"label": label, "objective": objective, "audience_takeaway": f"Synthetic takeaway {key}", "claim_ids": [claim_ids[i] for i in refs], "preferred_duration": rational(duration)}

def local_beat_input(key):
    _, label, objective, duration, _ = next(part for part in PARTS if part[0] == key)
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"motionwright:synthetic:scene-local-beat:{key}:v1")), "label": f"Local {label}", "objective": objective, "start": rational(0), "duration": rational(duration)}

def plan_input(claim_ids, scene_ids):
    return {
        "objective": BRIEF["objective"], "audience": BRIEF["audience"],
        "concept": "Synthetic source persistence exercise",
        "reference_constraints": ["Preserve the declared claim references"],
        "exclusions": ["No factual or creative certification"],
        "shots": [{"scene_id": scene_ids[key], "purpose": objective, "claim_ids": [claim_ids[i] for i in refs], "asset_ids": [], "evidence_kind": "synthetic_labeled"} for key, _, objective, _, refs in PARTS],
        "clock": {"kind": "timeline"}, "approval": None,
    }

class CaseOracle:
    """Expected content comes from authored inputs; only generated IDs are registered."""
    def __init__(self, identity, claim_ids, beat_ids, scene_ids):
        self.identity = tuple(identity)
        self.claim_ids = list(claim_ids)
        self.beat_ids = dict(beat_ids)
        self.scene_ids = dict(scene_ids)

    def assert_project(self, project, revision, order, starts):
        require((project["id"], project["generation"]) == self.identity, "Project identity or generation changed")
        require(type(project["revision"]) is int and project["revision"] == revision, "Wrong project revision")
        require(project["title"] == TITLE, "Title changed")
        expected_brief = {**BRIEF, "claims": [{"id": self.claim_ids[i], **FINAL_CLAIMS[i]} for i in range(2)]}
        require(json_equal(project["brief"], expected_brief), "Brief, claim source, version or identity changed")
        expected_narrative = {"premise": PREMISE, "beats": [{"id": self.beat_ids[key], **beat_input(key, self.claim_ids)} for key, *_ in PARTS], "protected_sections": []}
        require(json_equal(project["narrative"], expected_narrative), "Narrative identity, content, order or reference changed")
        require(json_equal(project["production_design"]["plan"], plan_input(self.claim_ids, self.scene_ids)), "Production dependency or purpose changed")
        require(project["assets"] == [], "Unexpected media source")
        require(len(project["scenes"]) == len(order) == len(starts) == 3, "Wrong scene count")
        require([s["id"] for s in project["scenes"]] == [self.scene_ids[key] for key in order], "Wrong scene order or identity")
        for scene, key, start in zip(project["scenes"], order, starts):
            _, _, objective, duration, _ = next(part for part in PARTS if part[0] == key)
            expected = {"id": self.scene_ids[key], "name": f"Synthetic scene {key}", "objective": objective, "start": rational(start), "duration": rational(duration), "beats": [local_beat_input(key)]}
            require(json_equal({field: scene[field] for field in expected}, expected), f"Scene {key} source, timing or local beat changed")

def assert_journal(events, expected_changes):
    require(isinstance(events, list) and len(events) == len(expected_changes), "Wrong journal length")
    require(json_equal([event["revision"] for event in events], list(range(1, len(expected_changes) + 1))), "Non-contiguous journal revisions")
    require(json_equal([event["change"] for event in events], expected_changes), "Journal differs from exact dispatched changes")

def assert_unchanged(before, after, before_events, after_events):
    require(json_equal(after, before), "Rejected change mutated Project")
    require(json_equal(after_events, before_events), "Rejected change mutated journal")

class Transport:
    def __init__(self, executable, database, directory, phase, env, on_started=None):
        self.counter = 0
        self.replies = queue.Queue()
        self.closed = False
        self.pump_error = None
        self.requests = self.stdout = self.stderr = None
        self.process = None
        try:
            self.requests = (directory / f"{phase}.requests.original.jsonl").open("xb")
            self.stdout = (directory / f"{phase}.stdout.original.jsonl").open("xb")
            self.stderr = (directory / f"{phase}.stderr.original.log").open("xb")
            self.process = subprocess.Popen([str(executable), "--database", str(database)], cwd=str(directory), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr)
            if on_started is not None:
                on_started()
        except BaseException:
            if self.process is not None:
                self.process.kill()
                self.process.wait(timeout=15)
                self.process.stdin.close(); self.process.stdout.close()
            for handle in (self.requests, self.stdout, self.stderr):
                if handle is not None:
                    handle.close()
            raise

        def pump():
            try:
                while True:
                    line = self.process.stdout.readline(8 * 1024 * 1024 + 1)
                    if not line:
                        break
                    self.stdout.write(line); self.stdout.flush()
                    require(len(line) <= 8 * 1024 * 1024 and line.endswith(b"\n"), "Oversized or unterminated response")
                    self.replies.put(line)
            except BaseException as error:
                self.pump_error = error
            finally:
                self.replies.put(None)
        self.thread = threading.Thread(target=pump, daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.process.kill()
            self.process.wait(timeout=15)
            for handle in (self.process.stdin, self.process.stdout, self.requests, self.stdout, self.stderr):
                handle.close()
            raise

    def call(self, command, request=None, expect_ok=True, timeout=30):
        require(not self.closed, "Transport is closed")
        self.counter += 1
        wire = {"id": self.counter, "command": command, "args": {} if request is None else {"request": request}}
        raw = (json.dumps(wire, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        self.requests.write(raw); self.requests.flush()
        self.process.stdin.write(raw); self.process.stdin.flush()
        try:
            response_raw = self.replies.get(timeout=timeout)
        except queue.Empty as error:
            raise ProbeFailure(f"Timeout during {command}") from error
        require(response_raw is not None, f"Transport ended during {command}; pump error={self.pump_error}")
        response = json.loads(response_raw)
        require(isinstance(response, dict), "Non-object response")
        require(type(response.get("id")) is int and response["id"] == self.counter, "Response identity mismatch")
        require(response.get("ok") is expect_ok, f"Unexpected {command} response: {response}")
        field = "result" if expect_ok else "error"
        require(field in response, "Missing response payload")
        return response[field]

    def close(self):
        if self.closed:
            return
        self.closed = True
        errors = []
        try:
            if self.process.poll() is None:
                self.process.stdin.close()
                try:
                    code = self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=15)
                    raise ProbeFailure("Transport did not exit after stdin closed")
            else:
                code = self.process.returncode
            require(type(code) is int and code == 0, f"Transport exit code {code}")
        except BaseException as error:
            errors.append(error)
        try:
            self.thread.join(timeout=5)
            require(not self.thread.is_alive(), "Stdout reader did not finish")
            require(self.pump_error is None, f"Stdout reader failed: {self.pump_error}")
        except BaseException as error:
            errors.append(error)
        for handle in (self.process.stdin, self.process.stdout, self.requests, self.stdout, self.stderr):
            try:
                handle.close()
            except BaseException as error:
                errors.append(error)
        if errors:
            raise ProbeFailure("; ".join(str(error) for error in errors)) from errors[0]

def close_transports(transports, prior_failure, secondary_errors):
    failures = []
    for transport in list(transports):
        try:
            transport.close()
        except BaseException as error:
            failures.append(error)
            secondary_errors.append(f"{type(error).__name__}: {error}")
    if failures and not prior_failure:
        raise failures[0]

def run_probe(executable, directory, env, execution):
    database = directory / "narrative.sqlite3"
    require(not database.exists(), "Fresh database required")
    expected_changes, ids_seen, transports = [], set(), []
    current, identity = None, None

    def register_id(value):
        require(uuid.UUID(value).int != 0 and value not in ids_seen, "Nil or duplicate identity")
        ids_seen.add(value)
        return value

    def open_transport(phase):
        execution["spawn_attempted"] = True
        transport = Transport(executable, database, directory, phase, env, on_started=lambda: execution.update(product_started=True))
        transports.append(transport)
        return transport

    t = open_transport("first-process")

    def scope():
        return {"project_id": identity[0], "generation": identity[1], "revision": len(expected_changes)}

    def issue(request_id):
        receipt = t.call("issue_effect_grant", {"effect": "project_edit", **scope(), "subject": request_id})
        require(receipt["effect"] == "project_edit" and receipt["one_time"] is True, "Wrong grant kind")
        require(type(receipt["revision"]) is int and json_equal({key: receipt[key] for key in scope()}, scope()), "Wrong grant scope")
        return receipt["token"]

    def apply(change):
        nonlocal current
        require(type(current["revision"]) is int and current["revision"] == len(expected_changes), "Unexpected pre-change revision")
        request_id = f"synthetic-narrative-change-{len(expected_changes) + 1:02d}"
        token = issue(request_id)
        current = t.call("apply_change", {**scope(), "request_id": request_id, "effect_grant": token, "change": change})
        expected_changes.append(copy.deepcopy(change))
        require((current["id"], current["generation"]) == identity, "Change altered identity")
        require(type(current["revision"]) is int and current["revision"] == len(expected_changes), "Change did not commit one revision")

    def history():
        events = t.call("project_history", {"project_id": identity[0], "after_revision": 0, "limit": 100})
        assert_journal(events, expected_changes)
        return events

    try:
        bootstrap = t.call("bootstrap")
        require(bootstrap["test_transport"] == "service-test-jsonl" and bootstrap["project"] is None, "Wrong transport or nonempty database")
        intent = {"request_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "motionwright:synthetic:narrative-reorder:c4:v1")), "title": TITLE}
        creation_grant = t.call("issue_project_creation_grant", intent)
        creation = t.call("create_project", {"intent": intent, "effect_grant": creation_grant["token"]})
        require(creation["request_id"] == intent["request_id"] and creation["replayed"] is False, "Wrong creation receipt")
        current = creation["project"]
        identity = (register_id(current["id"]), register_id(current["generation"]))
        require(type(current["revision"]) is int and current["revision"] == 0 and current["title"] == TITLE and current["scenes"] == [] and current["brief"]["claims"] == [], "Wrong initial document")
        apply({"type": "set_brief", **BRIEF})
        claim_ids = []
        for index, fields in enumerate(INITIAL_CLAIMS):
            apply({"type": "add_claim", **fields})
            require(len(current["brief"]["claims"]) == index + 1, "AddClaim count mismatch")
            claim = current["brief"]["claims"][index]
            claim_id = register_id(claim["id"])
            require(json_equal(claim, {"id": claim_id, **fields}), "Claim differs from input")
            claim_ids.append(claim_id)
        apply({"type": "update_claim", "claim_id": claim_ids[0], "expected": INITIAL_CLAIMS[0], "replacement": FINAL_CLAIMS[0]})
        apply({"type": "set_narrative_premise", "premise": PREMISE})
        beat_ids, scene_ids = {}, {}
        for key, *_ in PARTS:
            fields = beat_input(key, claim_ids)
            apply({"type": "add_narrative_beat", **fields})
            beat = current["narrative"]["beats"][-1]
            beat_ids[key] = register_id(beat["id"])
            require(json_equal(beat, {"id": beat_ids[key], **fields}), "Narrative beat differs from input")
        for key, _, objective, duration, _ in PARTS:
            apply({"type": "add_scene", "name": f"Synthetic scene {key}", "objective": objective, "duration_seconds": duration})
            scene_ids[key] = register_id(current["scenes"][-1]["id"])
        for key, *_ in PARTS:
            beat = local_beat_input(key)
            register_id(beat["id"])
            apply({"type": "upsert_scene_beat", "scene_id": scene_ids[key], "beat": beat})
        plan = plan_input(claim_ids, scene_ids)
        apply({"type": "set_production_plan", "plan": plan})
        require(len(expected_changes) == 15, "Wrong preparation count")
        oracle = CaseOracle(identity, claim_ids, beat_ids, scene_ids)
        oracle.assert_project(current, 15, ["A", "B", "C"], [0, 2, 5])
        save_json(directory / "before-reorder.project.json", current)
        apply({"type": "move_scene", "scene_id": scene_ids["C"], "to_index": 0})
        oracle.assert_project(current, 16, ["C", "A", "B"], [0, 5, 7])
        apply({"type": "move_scene", "scene_id": scene_ids["A"], "to_index": 2})
        oracle.assert_project(current, 17, ["C", "B", "A"], [0, 5, 8])
        saved_events = history()
        save_json(directory / "expected-changes.json", expected_changes)
        save_json(directory / "after-reorder.project.json", current)
        save_json(directory / "after-reorder.history.json", saved_events)

        def reject_unchanged(label, change, diagnostic):
            before, before_events = copy.deepcopy(current), history()
            token = issue(label)
            error = t.call("apply_change", {**scope(), "request_id": label, "effect_grant": token, "change": change}, expect_ok=False)
            require(isinstance(error, str) and diagnostic in error, f"Wrong negative result: {error}")
            observed = t.call("get_project", {"project_id": identity[0]})
            assert_unchanged(before, observed, before_events, history())

        unknown = str(uuid.uuid5(uuid.NAMESPACE_URL, "motionwright:synthetic:unknown-claim:v1"))
        require(unknown not in claim_ids, "Negative identity collision")
        reject_unchanged("synthetic-negative-narrative-reference", {"type": "add_narrative_beat", "label": "Rejected synthetic beat", "objective": "Test an unknown reference", "audience_takeaway": "", "claim_ids": [unknown], "preferred_duration": rational(1)}, "narrative beat references an unknown claim")
        invalid_plan = copy.deepcopy(plan)
        invalid_plan["shots"][0]["claim_ids"] = [unknown]
        reject_unchanged("synthetic-negative-shot-reference", {"type": "set_production_plan", "plan": invalid_plan}, "production shot has duplicate or unknown evidence references")
        reopened = t.call("reopen", {"project_id": identity[0]})
        assert_unchanged(current, reopened, saved_events, history())
        oracle.assert_project(reopened, 17, ["C", "B", "A"], [0, 5, 8])
        save_json(directory / "reopened.project.json", reopened)
        t.close(); transports.remove(t)
        t = open_transport("second-process")
        bootstrap = t.call("bootstrap", {"project_id": identity[0]})
        require(bootstrap["test_transport"] == "service-test-jsonl", "Wrong second transport")
        assert_unchanged(current, bootstrap["project"], saved_events, history())
        oracle.assert_project(bootstrap["project"], 17, ["C", "B", "A"], [0, 5, 8])
        save_json(directory / "second-process.project.json", bootstrap["project"])
        return {"probe_status": "PASS", "committed_changes": 17, "negative_reference_controls": 2, "project_id": identity[0], "generation": identity[1], "final_revision": 17, "connection_reopen_verified": True, "second_process_reopen_verified": True}
    finally:
        close_transports(transports, sys.exc_info()[0] is not None, execution["secondary_errors"])
