#!/usr/bin/env python3
"""One source-bound, read-only AT-SPI trace using a previously built AppImage.

The original installed attempt is never resumed or retried. This diagnostic
opens a fresh, isolated application only to inspect its accessibility tree.
No editor commands, click, Create, Host request or user effect are sent.
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
    stage(records, "dialog-collection-start")
    dialogs = bridge.matching_roles(app, ("dialog",))
    stage(records, "dialog-collection-return", {"dialog_role_count": len(dialogs)})
    dialog = unique([
        row for row in dialogs if row.name == "Project library"
        and VISIBLE <= row.states and "defunct" not in row.states
    ], "DIALOG")
    if bridge.owner_pid(dialog.node) != binding["pid"]:
        raise ProbeError("READ_ONLY_DIALOG_PROCESS_MISMATCH")
    stage(records, "control-collection-start")
    children = bridge.matching_roles(dialog.node, ("entry", "text", "button"))
    stage(records, "control-collection-return", {"role_count": len(children)})
    entry = unique([r for r in children
                    if r.role in ("entry", "text") and r.name == "Project title"
                    and VISIBLE <= r.states and "defunct" not in r.states], "TITLE")
    button = unique([r for r in children
                     if r.role == "button" and r.name == "Create project"
                     and VISIBLE <= r.states and "defunct" not in r.states], "CREATE")
    if bridge.owner_pid(entry.node) != binding["pid"] or bridge.owner_pid(button.node) != binding["pid"]:
        raise ProbeError("READ_ONLY_CONTROL_PROCESS_MISMATCH")
    stage(records, "controls-verified")
    return {"status": "SELECTION_CONFIRMED_READ_ONLY",
            "bound_pid": binding["pid"],
            "dialog_count": len(dialogs),
            "control_role_count": len(children)}


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
    return 0 if result["status"] == "SELECTION_CONFIRMED_READ_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
