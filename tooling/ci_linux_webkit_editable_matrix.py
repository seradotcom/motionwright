#!/usr/bin/env python3
"""One disposable WebKitGTK/GTK3 AT-SPI interface matrix, with no product effect.

Compares real accessibility capabilities exposed by ordinary HTML controls
within the same Ubuntu WebKitGTK/AT-SPI stack used by the installed candidate.
Does not launch Motionwright or send any input/focus/Edit/Create/Save action.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from linux_first_use_accessible import AtspiBridge, ProbeError, walk

EXPECTED = ("Plain input", "Typed input", "Plain textarea", "Editable div", "GTK native entry")
KNOWN = frozenset(("Accessible", "Action", "Application", "Collection", "Component",
                   "Document", "EditableText", "Hyperlink", "Hypertext", "Text",
                   "Selection", "Value"))
HTML = """<!doctype html><html lang="en"><meta charset="utf-8"><title>A11Y matrix</title>
<body><label for="plain">Plain input</label>
<input id="plain" type="text" aria-label="Plain input">
<label for="typed">Typed input</label>
<input id="typed" type="text" role="textbox" aria-readonly="false" aria-label="Typed input">
<label for="multi">Plain textarea</label>
<textarea id="multi" aria-label="Plain textarea" rows="1"></textarea>
<div contenteditable="true" role="textbox" aria-multiline="false"
     aria-label="Editable div" tabindex="0"></div>
</body></html>"""


def record(path: Path, contents: dict):
    if path.exists() or path.is_symlink():
        raise ProbeError("MATRIX_ORIGINAL_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(contents, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def serve(ready: Path):
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2
    Gtk.init(None)
    window = Gtk.Window(title="Motionwright AT-SPI Widget Matrix")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    native = Gtk.Entry()
    native.get_accessible().set_name("GTK native entry")
    box.pack_start(native, False, False, 0)
    web = WebKit2.WebView()
    box.pack_start(web, True, True, 0)
    window.add(box)
    window.set_default_size(740, 520)
    window.connect("destroy", Gtk.main_quit)
    def loaded(view, event):
        if event == WebKit2.LoadEvent.FINISHED and not ready.exists():
            record(ready, {"schema": "motionwright.webkit-a11y-fixture-ready/1",
                           "pid": os.getpid(), "fixture": "HTML_INPUT_MATRIX_NO_PRODUCT"})
    web.connect("load-changed", loaded)
    window.show_all()
    web.load_html(HTML, "about:blank")
    Gtk.main()


def enable_session():
    if (sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true"
            or not os.environ.get("DISPLAY") or not os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
        raise ProbeError("MATRIX_DISPOSABLE_SESSION_REQUIRED")
    commands = [
        ["gdbus", "call", "--session", "--dest", "org.a11y.Bus",
         "--object-path", "/org/a11y/bus", "--method", "org.a11y.Bus.GetAddress"],
        ["gdbus", "call", "--session", "--dest", "org.a11y.Bus",
         "--object-path", "/org/a11y/bus", "--method",
         "org.freedesktop.DBus.Properties.Set", "org.a11y.Status", "IsEnabled", "<true>"],
        ["gdbus", "call", "--session", "--dest", "org.a11y.Bus",
         "--object-path", "/org/a11y/bus", "--method",
         "org.freedesktop.DBus.Properties.Get", "org.a11y.Status", "IsEnabled"],
    ]
    for index, cmd in enumerate(commands):
        p = subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=6)
        if p.returncode or len(p.stdout) > 8192 or (index == 2 and "true" not in p.stdout.lower()):
            raise ProbeError("MATRIX_ACCESSIBILITY_SESSION_UNAVAILABLE")


def inspect(pid: int):
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib
    if Atspi.init() not in (0, 1):
        raise ProbeError("MATRIX_ATSPI_INITIALIZATION_FAILED")
    Atspi.set_timeout(800, 2500)
    deadline = time.monotonic() + 25
    bridge = AtspiBridge(Atspi, deadline=deadline)
    observed = {}
    snapshots = []
    for attempt in range(12):
        applications = bridge.applications()
        matches = [a for a in applications if bridge.pid(a) == pid]
        if len(matches) > 1:
            raise ProbeError("MATRIX_AMBIGUOUS_APPLICATION")
        if len(matches) == 1:
            tree = walk(bridge, matches[0])
            latest = {}
            for view in tree:
                if view.name not in EXPECTED:
                    continue
                raw = bridge.call(view.node.get_interfaces)
                values = tuple(raw or ())
                if len(values) > 64:
                    raise ProbeError("MATRIX_INTERFACE_COUNT_LIMIT")
                interfaces = sorted(KNOWN.intersection(values))
                editable = bridge.call(view.node.get_editable_text_iface) is not None
                state = bridge.call(view.node.get_state_set)
                is_editable = state.contains(Atspi.StateType.EDITABLE)
                latest[view.name] = {"role": view.role, "interfaces": interfaces,
                                     "editable_state": bool(is_editable),
                                     "editable_proxy": editable}
            snapshots.append({"iteration": attempt, "matched_names": sorted(latest)})
            observed = latest
            if len(observed) == len(EXPECTED):
                break
        context = GLib.MainContext.default()
        for _ in range(16):
            if not context.pending():
                break
            context.iteration(False)
        time.sleep(0.6)
    return {"targets": observed, "found": len(observed), "expected": len(EXPECTED),
            "snapshots": snapshots[-3:], "product_editor_effects": 0,
            "no_UI_keypresses_or_creations": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.serve:
        enable_session()
        serve(args.ready)
        return 0
    if args.output is None or args.output.exists() or args.ready.exists():
        raise ProbeError("MATRIX_EXCLUSIVE_OUTPUT_REQUIRED")
    enable_session()
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                               text=True, timeout=7).strip() != os.environ.get("GITHUB_SHA"):
        raise ProbeError("MATRIX_SOURCE_IDENTITY_MISMATCH")
    result = {"schema": "motionwright.webkitgtk-editable-matrix/1",
              "source_sha": os.environ["GITHUB_SHA"],
              "status": "NOT_OBSERVED", "no_native_or_product_effects": True,
              "observed_utc": datetime.now(timezone.utc).isoformat()}
    with (args.output.parent / "webkit-fixture-server.stdout").open("xb") as log:
        proc = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                 "--serve", "--ready", str(args.ready)],
                                cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            for _ in range(50):
                if args.ready.is_file():
                    break
                if proc.poll() is not None:
                    raise ProbeError("MATRIX_GUI_PROCESS_EXITED")
                time.sleep(0.2)
            if not args.ready.is_file():
                raise ProbeError("MATRIX_GUI_NOT_READY")
            ready = json.loads(args.ready.read_bytes())
            if ready.get("pid") != proc.pid:
                raise ProbeError("MATRIX_GUI_PID_CHANGED")
            result.update(inspect(proc.pid))
            result["status"] = "PASS_MATRIX" if result["found"] == result["expected"] else "PARTIAL_MATRIX"
        except Exception as err:
            result["status"] = "BLOCKED"
            result["failure"] = str(err)[:150] if isinstance(err, ProbeError) else type(err).__name__
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=4)
    record(args.output, result)
    print("webkit-input-matrix=" + result["status"] + " found=" + str(result.get("found", 0)))
    return 0 if result["status"] == "PASS_MATRIX" else 2


if __name__ == "__main__":
    raise SystemExit(main())
