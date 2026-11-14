#!/usr/bin/env python3
"""Bounded read-only X11 proof for the closed Kdenlive modal route.

This does not alter AT-SPI states, send input, or certify an import/save. Native
process identity remains the caller's guard; X11 properties are joined to it.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import selectors
import subprocess
import sys
import time

PROPERTY_NAMES = (
    "_NET_WM_PID", "_NET_WM_NAME", "_NET_WM_WINDOW_TYPE",
    "_NET_WM_STATE", "WM_TRANSIENT_FOR", "WM_CLIENT_LEADER",
)
PROPERTY_KEYS = ("pid", "name", "types", "states", "transient_for", "client_leader")
PROPERTY_TYPES = ("CARDINAL", "UTF8_STRING", "ATOM", "ATOM", "WINDOW", "WINDOW")
QUERY_BYTES = 16 * 1024
TOTAL_BYTES = 64 * 1024
MAX_QUERIES = 24
QUERY_SECONDS = 5.0
REAP_RESERVE_SECONDS = 0.5
LINUX = sys.platform.startswith("linux")
NORMAL = "_NET_WM_WINDOW_TYPE_NORMAL"
DIALOG = "_NET_WM_WINDOW_TYPE_DIALOG"
MODAL = "_NET_WM_STATE_MODAL"


class X11Rejected(ValueError):
    """The bounded original observation cannot prove the requested relation."""


def _require(condition, reason):
    if not condition:
        raise X11Rejected("native_gui_X11_" + reason)


def _uint32(value, *, positive=False):
    return type(value) is int and (1 if positive else 0) <= value <= 0xFFFFFFFF


def _name(value):
    return (type(value) is str and bool(value) and len(value.encode("utf-8")) < 1024
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def parse_properties(raw: bytes) -> dict:
    """Parse all six explicitly requested xprop properties, with their types."""
    _require(type(raw) is bytes and 0 < len(raw) <= QUERY_BYTES, "property_bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise X11Rejected("native_gui_X11_property_utf8") from error
    _require(text.endswith("\n") and "\r" not in text and "\x00" not in text,
             "property_lines")
    lines = text[:-1].split("\n")
    _require(len(lines) == len(PROPERTY_NAMES), "property_count")
    result = {}
    for line in lines:
        absent = re.fullmatch(
            r"([A-Z_][A-Z0-9_]*): {1,2}(not found\.|no such atom on any window\.)", line)
        present = re.fullmatch(
            r"([A-Z_][A-Z0-9_]*)\(([A-Z0-9_]+)\)( = |: window id # )(.*)", line)
        _require(absent is not None or present is not None, "property_syntax")
        prop = (absent or present).group(1)
        _require(prop in PROPERTY_NAMES, "property_unknown")
        index = PROPERTY_NAMES.index(prop)
        key = PROPERTY_KEYS[index]
        _require(key not in result, "property_duplicate")
        if absent:
            result[key] = None
            continue
        _, kind, separator, value = present.groups()
        _require(kind == PROPERTY_TYPES[index], "property_type")
        _require(len(value.encode("utf-8")) < 1024, "property_truncated")
        if kind == "WINDOW":
            _require(separator == ": window id # " and
                     re.fullmatch(r"0x[0-9a-fA-F]{1,8}", value) is not None,
                     "property_window")
            parsed = int(value, 16)
        else:
            _require(separator == " = ", "property_separator")
            if kind == "CARDINAL":
                _require(re.fullmatch(r"0|[1-9][0-9]{0,9}", value) is not None,
                         "property_cardinal")
                parsed = int(value)
                _require(_uint32(parsed), "property_cardinal_range")
            elif kind == "UTF8_STRING":
                try:
                    parsed = json.loads(value)
                except (ValueError, TypeError) as error:
                    raise X11Rejected("native_gui_X11_property_string") from error
                _require(type(parsed) is str and len(parsed.encode("utf-8")) < 1024
                         and not any(ord(c) < 32 or ord(c) == 127 for c in parsed),
                         "property_string")
            else:
                parsed = tuple(value.split(", ")) if value else ()
                _require(len(parsed) <= 32 and len(set(parsed)) == len(parsed)
                         and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", a)
                                 for a in parsed), "property_atoms")
        result[key] = parsed
    _require(set(result) == set(PROPERTY_KEYS), "property_missing")
    return result


def _checked_properties(props):
    _require(type(props) is dict and set(props) == set(PROPERTY_KEYS), "properties_shape")
    for key in ("pid", "transient_for", "client_leader"):
        _require(props[key] is None or _uint32(props[key]), "properties_integer")
    _require(props["name"] is None or
             (type(props["name"]) is str and len(props["name"].encode("utf-8")) < 1024
              and not any(ord(c) < 32 or ord(c) == 127 for c in props["name"])),
             "properties_name")
    for key in ("types", "states"):
        value = props[key]
        _require(value is None or (type(value) in (tuple, list) and len(value) <= 32
                 and all(type(a) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", a)
                         for a in value) and len(set(value)) == len(value)),
                 "properties_atoms")


def _owned(props, expected_pid, name):
    _checked_properties(props)
    _require(props["pid"] == expected_pid and type(props["pid"]) is int, "pid_changed")
    _require(props["name"] == name, "name_changed")


def verify_dialog(props, parent_props, *, expected_pid, dialog_name, parent_name,
                  parent_window, group=False, leader_props=None) -> str:
    _require(_uint32(expected_pid, positive=True)
             and _uint32(parent_window, positive=True)
             and _name(dialog_name) and _name(parent_name) and type(group) is bool,
             "dialog_arguments")
    _owned(props, expected_pid, dialog_name)
    _owned(parent_props, expected_pid, parent_name)
    _require(DIALOG in (props["types"] or ()) and MODAL in (props["states"] or ()),
             "dialog_not_modal")
    _require(bool(parent_props["types"]) and
             set(parent_props["types"]).issubset({NORMAL, DIALOG}), "parent_type")
    if not group:
        _require(props["transient_for"] == parent_window, "transient_parent")
        _require(leader_props is None, "unexpected_leader")
        return "direct_parent"
    # Qt's parentless Delayed Rendering input dialog uses the client leader.
    # This closed exception is not a general group/root-window fallback.
    _require(dialog_name in ("Delayed Rendering", "Delayed Rendering \u2014 Kdenlive")
             and parent_name in ("Rendering", "Rendering \u2014 Kdenlive"),
             "group_profile")
    leader = props["client_leader"]
    _require(_uint32(leader, positive=True) and leader != parent_window
             and props["transient_for"] == leader
             and parent_props["client_leader"] == leader, "group_relation")
    _checked_properties(leader_props)
    _require(leader_props["client_leader"] == leader, "leader_not_self")
    # QXcbConnection publishes no _NET_WM_PID on the client leader. Absence is
    # retained; a contradictory PID cannot support the owned group's proof.
    _require(leader_props["pid"] is None or leader_props["pid"] == expected_pid,
             "leader_pid")
    return "qt_client_leader"


def _boundary(guard, deadline):
    _require(callable(guard) and type(deadline) in (int, float)
             and math.isfinite(deadline), "deadline_arguments")
    _require(time.monotonic() < deadline, "deadline")
    guard()
    _require(time.monotonic() < deadline, "deadline")


def _window(raw):
    _require(type(raw) is bytes and
             re.fullmatch(rb"[1-9][0-9]{0,9}\n", raw) is not None, "window_id")
    value = int(raw[:-1])
    _require(_uint32(value, positive=True), "window_id")
    return value


def _property_argv(window):
    _require(_uint32(window, positive=True), "window_id")
    return ["xprop", "-len", "1024", "-id", str(window), *PROPERTY_NAMES]


def _readonly_argv(argv):
    _require(type(argv) is list and all(type(v) is str for v in argv), "query_argv")
    if argv in (["xdotool", "getactivewindow"], ["xdotool", "getwindowfocus"]):
        return
    _require(len(argv) == 5 + len(PROPERTY_NAMES) and
             argv[:4] == ["xprop", "-len", "1024", "-id"] and
             argv[5:] == list(PROPERTY_NAMES) and
             re.fullmatch(r"[1-9][0-9]{0,9}", argv[4]) is not None
             and _uint32(int(argv[4]), positive=True), "query_not_readonly")


def _bytes(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "base64": base64.b64encode(raw).decode("ascii")}


def _query(argv, guard, deadline, records) -> bytes:
    """One owned child, bounded concurrent streams, and no input or retry."""
    _readonly_argv(argv)
    _require(type(records) is list and len(records) < MAX_QUERIES, "query_count")
    used = 0
    for previous in records:
        count = previous.get("captured_bytes") if type(previous) is dict else None
        _require(type(count) is int and 0 <= count <= QUERY_BYTES, "query_records")
        used += count
    _require(used < TOTAL_BYTES, "total_bytes")
    record = {"argv": list(argv), "pid": None, "returncode": None,
              "state": "INTENT", "captured_bytes": 0, "observed_bytes": 0,
              "truncated": False, "stdout_eof": False, "stderr_eof": False,
              "termination_requested": False, "reaped": False, "retried": False,
              "stdout": _bytes(b""), "stderr": _bytes(b"")}
    records.append(record)
    process = None
    selector = None
    error = None
    transport_phase = False
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    started = time.monotonic()
    hard_deadline = min(deadline, started + QUERY_SECONDS) if type(deadline) in (int, float) else started
    io_deadline = hard_deadline - REAP_RESERVE_SECONDS
    allowance = min(QUERY_BYTES, TOTAL_BYTES - used)
    try:
        _boundary(guard, io_deadline)
        _require(LINUX, "transport_requires_linux")
        env = os.environ.copy()
        env["LC_ALL"] = "C.UTF-8"
        env["LANG"] = "C.UTF-8"
        env.pop("XPROPFORMATS", None)
        transport_phase = True
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, close_fds=True, env=env)
        _require(_uint32(process.pid, positive=True), "child_pid")
        record["pid"] = process.pid
        selector = selectors.DefaultSelector()
        for label in ("stdout", "stderr"):
            stream = getattr(process, label)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while True:
            remaining = io_deadline - time.monotonic()
            _require(remaining > 0, "query_timeout")
            code = process.poll()
            if not selector.get_map() and code is not None:
                record["returncode"] = code
                record["reaped"] = True
                break
            events = selector.select(min(0.05, remaining)) if selector.get_map() else ()
            if not events and not selector.get_map():
                time.sleep(min(0.01, remaining))
            for key, _ in events:
                kept = sum(len(data) for data in streams.values())
                try:
                    data = os.read(key.fileobj.fileno(), min(4096, allowance - kept + 1))
                except BlockingIOError:
                    continue
                if not data:
                    record[key.data + "_eof"] = True
                    selector.unregister(key.fileobj)
                    continue
                record["observed_bytes"] += len(data)
                room = allowance - kept
                streams[key.data].extend(data[:room])
                if len(data) > room:
                    record["truncated"] = True
                    raise X11Rejected("native_gui_X11_query_bytes")
        _require(record["returncode"] == 0, "query_exit")
        _require(not streams["stderr"], "query_stderr")
        transport_phase = False
        _boundary(guard, hard_deadline)
    except BaseException as failure:
        if transport_phase and isinstance(failure, Exception) and not isinstance(failure, X11Rejected):
            error = X11Rejected("native_gui_X11_query_transport:" + type(failure).__name__)
            error.__cause__ = failure
        else:
            error = failure
    finally:
        # Only the Popen object created above may be terminated. No groups,
        # discovered PIDs, xdotool actions, or second execution are admitted.
        cleanup_errors = []
        if process is not None:
            try:
                if process.poll() is None:
                    record["termination_requested"] = True
                    process.kill()
                timeout = max(0.0, min(REAP_RESERVE_SECONDS, hard_deadline - time.monotonic()))
                record["returncode"] = process.wait(timeout=timeout)
                record["reaped"] = True
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            for label in ("stdout", "stderr"):
                try:
                    stream = getattr(process, label, None)
                    if stream is not None:
                        stream.close()
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
        if selector is not None:
            try:
                selector.close()
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if error is not None and process is not None:
            try:
                _boundary(guard, hard_deadline)
            except BaseException as after_error:
                cleanup_errors.append(after_error)
        if cleanup_errors:
            record["cleanup_errors"] = [{"type": type(item).__name__, "message": str(item)}
                                        for item in cleanup_errors]
        for cleanup_error in cleanup_errors:
            if error is None:
                error = X11Rejected("native_gui_X11_query_cleanup_failed:" + type(cleanup_error).__name__)
                error.__cause__ = cleanup_error
            else:
                error.add_note("native_gui_X11_query_cleanup_failed:" + type(cleanup_error).__name__)
        for label, data in streams.items():
            record[label] = _bytes(bytes(data))
        record["captured_bytes"] = sum(len(data) for data in streams.values())
        record["elapsed_ms"] = max(0, int((time.monotonic() - started) * 1000))
        record["state"] = "COMPLETE" if error is None else "REJECTED"
        if error is not None:
            record["error"] = {"type": type(error).__name__, "message": str(error)}
    if error is not None:
        raise error
    return bytes(streams["stdout"])


def _start_evidence(evidence, schema, expected_pid):
    _require(type(evidence) is dict and not evidence, "evidence_not_empty")
    evidence.update({"schema": schema, "complete": False, "expected_pid": expected_pid,
                     "records": [], "native_operation_retried": False,
                     "native_save_or_import_verified": False})
    _require(_uint32(expected_pid, positive=True), "expected_pid")
    return evidence["records"]


def _reject_evidence(evidence, error):
    evidence["complete"] = False
    evidence["error"] = {"type": type(error).__name__, "message": str(error)}


def observe_parent(*, expected_pid, parent_name, parent_type, guard, deadline,
                   evidence=None) -> dict:
    evidence = {} if evidence is None else evidence
    records = _start_evidence(evidence, "motionwright.native.x11-modal-parent/1", expected_pid)
    evidence["name"] = parent_name
    try:
        _require(_name(parent_name) and type(parent_type) is tuple and bool(parent_type)
                 and len(set(parent_type)) == len(parent_type)
                 and set(parent_type).issubset({NORMAL, DIALOG}), "parent_arguments")
        evidence["allowed_types"] = list(parent_type)
        _boundary(guard, deadline)
        window = _window(_query(["xdotool", "getactivewindow"], guard, deadline, records))
        evidence["window"] = window
        first = _query(_property_argv(window), guard, deadline, records)
        props = parse_properties(first)
        evidence["properties"] = props
        second = _query(_property_argv(window), guard, deadline, records)
        last = _window(_query(["xdotool", "getactivewindow"], guard, deadline, records))
        evidence["active_after"] = last
        _require(window == last and first == second, "parent_unstable")
        _owned(props, expected_pid, parent_name)
        _require(bool(props["types"]) and set(props["types"]).issubset(set(parent_type)),
                 "parent_type")
        _boundary(guard, deadline)
        evidence["complete"] = True
        return evidence
    except BaseException as error:
        _reject_evidence(evidence, error)
        raise


def observe_modal(*, expected_pid, dialog_name, parent_observation, group,
                  guard, deadline, evidence) -> dict:
    records = _start_evidence(evidence, "motionwright.native.x11-modal-observation/1", expected_pid)
    try:
        _require(_name(dialog_name) and type(group) is bool, "modal_arguments")
        _require(type(parent_observation) is dict and
                 parent_observation.get("schema") == "motionwright.native.x11-modal-parent/1"
                 and parent_observation.get("complete") is True and
                 parent_observation.get("expected_pid") == expected_pid
                 and type(parent_observation.get("records")) is list,
                 "parent_not_observed")
        parent = parent_observation.get("window")
        parent_name = parent_observation.get("name")
        _require(_uint32(parent, positive=True) and _name(parent_name), "parent_not_observed")
        if group:
            _require(dialog_name in ("Delayed Rendering", "Delayed Rendering \u2014 Kdenlive")
                     and parent_name in ("Rendering", "Rendering \u2014 Kdenlive"),
                     "group_profile")
        preprops = parent_observation.get("properties")
        _owned(preprops, expected_pid, parent_name)
        # The pre-opener originals share the same byte/query budget.
        records.extend(copy.deepcopy(parent_observation["records"]))
        _require(0 < len(records) <= MAX_QUERIES, "parent_records")
        evidence.update({"parent_observation": copy.deepcopy(parent_observation),
                         "name": dialog_name, "parent_window": parent,
                         "parent_name": parent_name, "group": group})
        _boundary(guard, deadline)
        window = _window(_query(["xdotool", "getwindowfocus"], guard, deadline, records))
        evidence["window"] = window
        _require(window != parent, "focus_is_parent")
        dialog_raw = _query(_property_argv(window), guard, deadline, records)
        props = parse_properties(dialog_raw)
        evidence["properties"] = props
        parent_raw = _query(_property_argv(parent), guard, deadline, records)
        parent_props = parse_properties(parent_raw)
        evidence["parent_properties"] = parent_props
        leader = None
        leader_raw = None
        leader_props = None
        if group:
            leader = props["client_leader"]
            _require(_uint32(leader, positive=True) and leader not in (window, parent)
                     and preprops["client_leader"] == leader
                     and parent_props["client_leader"] == leader, "leader_identity")
            evidence["leader_window"] = leader
            leader_raw = _query(_property_argv(leader), guard, deadline, records)
            leader_props = parse_properties(leader_raw)
            evidence["leader_properties"] = leader_props
        dialog_again = _query(_property_argv(window), guard, deadline, records)
        parent_again = _query(_property_argv(parent), guard, deadline, records)
        leader_again = (_query(_property_argv(leader), guard, deadline, records)
                        if group else None)
        last = _window(_query(["xdotool", "getwindowfocus"], guard, deadline, records))
        evidence["focus_after"] = last
        _require(window == last and dialog_raw == dialog_again and
                 parent_raw == parent_again and leader_raw == leader_again, "modal_unstable")
        # FOCUSED may legitimately disappear when the new modal opens. Every
        # other field joins the exact parent; both original state lists remain.
        _require(all(parent_props[key] == preprops[key]
                     for key in PROPERTY_KEYS if key != "states"), "parent_identity_changed")
        evidence["relation"] = verify_dialog(
            props, parent_props, expected_pid=expected_pid,
            dialog_name=dialog_name, parent_name=parent_name, parent_window=parent,
            group=group, leader_props=leader_props)
        _boundary(guard, deadline)
        evidence["complete"] = True
        return evidence
    except BaseException as error:
        _reject_evidence(evidence, error)
        raise
