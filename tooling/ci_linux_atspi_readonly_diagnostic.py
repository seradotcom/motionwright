#!/usr/bin/env python3
"""One source-bound, read-only AT-SPI trace using a previously built AppImage.

The original installed attempt is never resumed or retried. This diagnostic
opens a fresh, isolated application only to inspect its accessibility tree.
No editor commands, click, Create, Host request or user effect are sent.
The disposable session accessibility switch is enabled before launching WebKit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from linux_first_use_accessible import (
    AtspiBridge, ProbeError, VISIBLE, unique, x11_identity,
)
from linux_gui_probe import run_probe

SHA = re.compile(r"[0-9a-f]{40}\Z")
STAGE = re.compile(r"[a-z]+(?:-[a-z]+)*\Z")


def stage(directory: Path, name: str, payload: dict | None = None):
    """Create and fsync once, before each potentially blocking native query."""
    if not STAGE.fullmatch(name):
        raise ProbeError("READ_ONLY_INVALID_STAGE")
    directory.mkdir(parents=True, exist_ok=True)
    record = {"schema": "motionwright.linux-atspi-readonly-stage/1",
              "stage": name, "observed_utc": datetime.now(timezone.utc).isoformat(),
              "no_mutation": True, **(payload or {})}
    with (directory / (name + ".json")).open("x", encoding="utf-8") as out:
        json.dump(record, out, sort_keys=True)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())


def admit_source(repo: Path, source: str, binary_source: str):
    if not SHA.fullmatch(source) or not SHA.fullmatch(binary_source):
        raise ProbeError("READ_ONLY_SOURCE_INVALID")
    if (sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_SHA") != source
            or not os.environ.get("DISPLAY") or not os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
        raise ProbeError("READ_ONLY_DISPOSABLE_LINUX_REQUIRED")
    def git(*argv):
        return subprocess.check_output(["git", "-C", str(repo), *argv],
                                       text=True, timeout=7).strip()
    if git("rev-parse", "HEAD") != source:
        raise ProbeError("READ_ONLY_CHECKOUT_SOURCE_CHANGED")
    # The checkout on GitHub is intentionally shallow. Independently pinned
    # Git blob identities from the original built source are checked without
    # fetching a historical commit or weakening source/bytes equivalence.
    for path, environment_key in (
            ("tooling/linux_first_use_accessible.py", "ORIGINAL_ATSPI_CLIENT_BLOB"),
            ("tooling/linux_gui_probe.py", "ORIGINAL_GUI_PROBE_BLOB")):
        original_blob = os.environ.get(environment_key, "")
        if not SHA.fullmatch(original_blob) or git("rev-parse", "HEAD:" + path) != original_blob:
            raise ProbeError("READ_ONLY_PROBE_SOURCE_DIFFERS_FROM_BINARY")


def read_shallow_roles(bridge, app, records: Path):
    """Bounded structural AT-SPI reads, never a whole-WebKit tree query.

    Inspect only the app's first eight children and at most 16 descendants
    across those children. Return role *types* and counts only, never names,
    document text, user data, or effect-capable objects.
    """
    stage(records, "shallow-root-count-start")
    count = bridge.call(app.get_child_count)
    if type(count) is not int or count < 0:
        raise ProbeError("READ_ONLY_SHALLOW_ROOT_COUNT_INVALID")
    stage(records, "shallow-root-count-return", {"children": count})
    selected = []
    first_roles = []
    labels = ("one", "two", "three", "four", "five", "six", "seven", "eight")
    for index in range(min(count, len(labels))):
        label = labels[index]
        stage(records, "shallow-child-" + label + "-start")
        child = bridge.call(app.get_child_at_index, index)
        if child is None:
            raise ProbeError("READ_ONLY_SHALLOW_MISSING_CHILD")
        role = bridge.call(child.get_role)
        children = bridge.call(child.get_child_count)
        if type(children) is not int or children < 0:
            raise ProbeError("READ_ONLY_SHALLOW_CHILD_COUNT_INVALID")
        # AT-SPI role is a finite public enum; do not capture accessible names.
        role_number = int(role)
        first_roles.append(role_number)
        selected.append((child, children))
        stage(records, "shallow-child-" + label + "-return",
              {"role_enum": role_number, "children": children})
    stage(records, "shallow-grandchildren-start")
    second_roles = []
    examined = 0
    for parent, children in selected:
        for index in range(min(children, 16 - examined)):
            node = bridge.call(parent.get_child_at_index, index)
            if node is None:
                raise ProbeError("READ_ONLY_SHALLOW_MISSING_GRANDCHILD")
            second_roles.append(int(bridge.call(node.get_role)))
            examined += 1
        if examined == 16:
            break
    stage(records, "shallow-grandchildren-return",
          {"examined": examined, "total_possible": sum(x[1] for x in selected)})
    return {
        "status": "SHALLOW_ROLE_SNAPSHOT_ONLY",
        "root_child_count": count,
        "first_roles": first_roles,
        "second_role_counts": {str(role): second_roles.count(role) for role in sorted(set(second_roles))},
        "second_examined": examined,
        "truncated": count > len(labels) or sum(x[1] for x in selected) > examined,
        "full_dialog_or_controls_verified": False,
        "editor_actions": 0,
    }


def enable_session_accessibility(records: Path):
    """Enable ordinary AT-SPI visibility only inside a disposable CI session.

    This is a desktop-session setting, not Motionwright project/Native
    authority. It MUST be set before launching WebKitGTK; all actions remain
    read-only afterwards and the session is destroyed at the end of the job.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true" or not (
            os.environ.get("DISPLAY") and os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
        raise ProbeError("READ_ONLY_DISPOSABLE_ACCESSIBILITY_SESSION_REQUIRED")
    commands = [
        ["gdbus", "call", "--session", "--dest", "org.a11y.Bus",
         "--object-path", "/org/a11y/bus", "--method", "org.a11y.Bus.GetAddress"],
        ["gdbus", "call", "--session", "--dest", "org.a11y.Bus",
         "--object-path", "/org/a11y/bus", "--method", "org.freedesktop.DBus.Properties.Set",
         "org.a11y.Status", "IsEnabled", "<true>"],
        ["gdbus", "call", "--session", "--dest", "org.a11y.Bus",
         "--object-path", "/org/a11y/bus", "--method", "org.freedesktop.DBus.Properties.Get",
         "org.a11y.Status", "IsEnabled"],
    ]
    names = ("session-bus-activation", "session-accessibility-enable",
             "session-accessibility-verify")
    for name, command in zip(names, commands):
        stage(records, name + "-start")
        try:
            reply = subprocess.run(command, text=True, capture_output=True,
                                   timeout=6, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ProbeError("READ_ONLY_ACCESSIBILITY_SESSION_COMMAND_FAILURE") from e
        if reply.returncode != 0 or len(reply.stdout) > 8192:
            raise ProbeError("READ_ONLY_ACCESSIBILITY_SESSION_REJECTED")
        if name == "session-accessibility-verify" and "true" not in reply.stdout.lower():
            raise ProbeError("READ_ONLY_ACCESSIBILITY_SESSION_NOT_ENABLED")
        # Preserve only a categorical success; do not retain bus addresses.
        stage(records, name + "-return", {"verified": True})
    return True


def trace_controls(scratch: Path, records: Path):
    stage(records, "callback-start")
    deadline = time.monotonic() + 26
    stage(records, "identity-start")
    binding = x11_identity(deadline=deadline)
    stage(records, "identity-return", {"bound_pid": binding["pid"]})
    stage(records, "gi-import-start")
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    stage(records, "gi-import-return")
    # Unlike the full installed harness, this early read-only probe has not
    # started the AT-SPI registry by exercising application admission. Ask
    # the ordinary session bus to activate its published org.a11y.Bus service
    # before calling Atspi.init(), with one bounded read-only request.
    stage(records, "accessibility-bus-start")
    try:
        bus = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.a11y.Bus",
             "--object-path", "/org/a11y/bus", "--method", "org.a11y.Bus.GetAddress"],
            text=True, capture_output=True, timeout=6, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProbeError("READ_ONLY_ACCESSIBILITY_BUS_UNAVAILABLE") from error
    if bus.returncode != 0 or len(bus.stdout) > 8192:
        raise ProbeError("READ_ONLY_ACCESSIBILITY_BUS_UNAVAILABLE")
    stage(records, "accessibility-bus-return")
    if Atspi.init() not in (0, 1):
        raise ProbeError("READ_ONLY_ATSPI_INIT_FAILED")
    Atspi.set_timeout(800, 2500)
    stage(records, "bridge-init-start")
    bridge = AtspiBridge(Atspi, deadline=deadline)
    stage(records, "bridge-init-return")
    stage(records, "applications-start")
    apps = bridge.applications()
    stage(records, "applications-return", {"application_count": len(apps)})
    if len(apps) > 128:
        raise ProbeError("READ_ONLY_APPLICATION_LIMIT")
    stage(records, "application-pid-filter-start")
    app = unique([node for node in apps if bridge.pid(node) == binding["pid"]], "APPLICATION")
    stage(records, "application-pid-filter-return")
    # Global Collection.GetMatches on the full WebKit app subtree has been
    # observed blocking past the original 65 s external watchdog. Never call
    # it again in this diagnostic. Read only two shallow, explicitly bounded
    # levels; these types identify the next safe inspection boundary.
    return {"bound_pid": binding["pid"],
            **read_shallow_roles(bridge, app, records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-run", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--original-binary-source", required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    admit_source(repo, args.source, args.original_binary_source)
    if args.scratch.exists() or args.output.exists():
        raise ProbeError("READ_ONLY_FRESH_NAMESPACE_REQUIRED")
    records = args.scratch / "read-only-atspi"
    result = {"schema": "motionwright.linux-atspi-readonly-diagnostic/1",
              "source_sha": args.source,
              "prebuilt_binary_source_sha": args.original_binary_source,
              "scope": "read-only-fresh-appimage-no-editor-effect",
              "status": "NOT_OBSERVED", "editor_actions": 0,
              "native_render_or_save_commands": 0, "original_attempt_replayed": False,
              "formal_G2": "NOT_GRANTED", "formal_G3": "STOP"}
    def inspect(_):
        stage(records, "one-callback-intent")
        try:
            result.update(trace_controls(args.scratch, records))
        except Exception as err:
            result["status"] = "READ_ONLY_BLOCKED"
            result["failure_class"] = type(err).__name__
            result["failure_type"] = (str(err) if isinstance(err, ProbeError)
                                      else "NATIVE_READ_" + type(err).__name__)[:130]
            stage(records, "terminal-blocked", {"failure_class": type(err).__name__})
        return True
    try:
        # The source job launches under its own dbus-run-session. Enable the
        # ephemeral accessibility switch *before* run_probe spawns the
        # prebuilt AppImage; no source or product files are changed.
        session_records = args.output.parent / "read-only-session-a11y"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result["session_accessibility_enabled"] = enable_session_accessibility(session_records)
        result["window"] = run_probe(args.app_run, args.scratch,
                                     seconds=35, window_interaction=inspect)
    except Exception as err:
        result["status"] = "READ_ONLY_BLOCKED"
        result["window_error_class"] = type(err).__name__
        # Never copy process logs, current user variables or UI text to stdout.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, sort_keys=True, indent=2)
        out.write("\n")
    print("read-only-ats=" + result["status"]
          + " source=" + args.original_binary_source, flush=True)
    return 0 if result["status"] == "SHALLOW_ROLE_SNAPSHOT_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
