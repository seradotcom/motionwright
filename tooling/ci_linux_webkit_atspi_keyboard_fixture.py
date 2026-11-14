#!/usr/bin/env python3
"""One bounded AT-SPI-registry keyboard event into a throwaway HTML fixture.

This test never starts Motionwright, invokes its Tauri/JS code or creates
a project. It deliberately tests the standards-based AT-SPI keyboard method
when WebKitGTK exposes an editable HTML entry without EditableText.
It sends at most ONE fixture string on a source- and PID-bound fresh window.
No XTest/xdotool, no direct DOM edit, and no retry after any possible effect.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from ci_linux_webkit_editable_matrix import enable_session, record
from linux_first_use_accessible import AtspiBridge, ProbeError, USABLE, walk

VALUE = "MotionwrightInputFixtureOnce"


def admission(output: Path, ready: Path):
    if (sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
            or not os.environ.get("DISPLAY") or not os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
        raise ProbeError("AT_SPI_FIXTURE_ORIGINAL_LINUX_REQUIRED")
    source = os.environ.get("GITHUB_SHA", "")
    if len(source) != 40 or any(c not in "0123456789abcdef" for c in source):
        raise ProbeError("AT_SPI_FIXTURE_SOURCE_REQUIRED")
    root = Path(__file__).resolve().parents[1]
    observed = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                       text=True, timeout=10).strip()
    if source != observed:
        raise ProbeError("AT_SPI_FIXTURE_SOURCE_DRIFT")
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    if (output.exists() or ready.exists() or output.is_symlink() or ready.is_symlink()
            or output.parent != ready.parent
            or output.parent == runner or not output.parent.is_relative_to(runner)):
        raise ProbeError("AT_SPI_FIXTURE_FRESH_NAMESPACE_REQUIRED")
    return root, source


def drain(glib):
    context = glib.MainContext.default()
    for _ in range(16):
        if not context.pending():
            break
        context.iteration(False)


def locate_title(bridge, glib, pid: int):
    selected = None
    for attempt in range(12):
        apps = bridge.applications()
        if len(apps) > 128:
            raise ProbeError("AT_SPI_FIXTURE_APPLICATION_LIMIT")
        owners = [node for node in apps if bridge.pid(node) == pid]
        if len(owners) > 1:
            raise ProbeError("AT_SPI_FIXTURE_AMBIGUOUS_APPLICATION")
        if len(owners) == 1:
            controls = [v for v in walk(bridge, owners[0])
                        if v.role == "entry" and v.name == "Plain input"
                        and USABLE | {"editable"} <= v.states and "defunct" not in v.states]
            if len(controls) > 1:
                raise ProbeError("AT_SPI_FIXTURE_AMBIGUOUS_FIELD")
            if len(controls) == 1:
                selected = (owners[0], controls[0])
                break
        drain(glib)
        time.sleep(0.5)
    if selected is None:
        raise ProbeError("AT_SPI_FIXTURE_MISSING_TARGET")
    app, field = selected
    if bridge.pid(app) != pid or bridge.owner_pid(field.node) != pid:
        raise ProbeError("AT_SPI_FIXTURE_OWNER_CHANGED")
    # The reusable production bridge only populates View.interfaces for
    # product-labelled fields ("Project title" / "Create project"). This
    # throwaway fixture has its own exact "Plain input" label, so consult the
    # native interface list *on the already uniquely PID-bound node*.
    raw_interfaces = bridge.call(field.node.get_interfaces)
    if raw_interfaces is None or len(raw_interfaces) > 64:
        raise ProbeError("AT_SPI_FIXTURE_INTERFACES_INVALID")
    interface_names = frozenset(raw_interfaces)
    if not {"Component", "Text"} <= interface_names:
        raise ProbeError("AT_SPI_FIXTURE_READABLE_FOCUSABLE_REQUIRED")
    if "EditableText" in interface_names:
        raise ProbeError("AT_SPI_FIXTURE_UNEXPECTED_NATIVE_EDITABLE_INTERFACE")
    if bridge.read_text(field.node) != "":
        raise ProbeError("AT_SPI_FIXTURE_INITIAL_NOT_EMPTY")
    return app, field


def single_input(bridge, atspi, glib, app, title, pid: int, records: Path):
    """All effects are journaled and never repeated under any failure."""
    def mark(name, payload):
        record(records / (name + ".json"), {
            "schema": "motionwright.atspi-keyboard-fixture-event/1",
            "step": name, "product_effects": False, **payload})
    if bridge.pid(app) != pid or bridge.owner_pid(title.node) != pid:
        raise ProbeError("AT_SPI_FIXTURE_OWNER_CHANGED")
    mark("focus-intent", {"pid": pid, "target": "Plain input"})
    # Genuine AT-SPI Component focus effect. It is NOT a product Save.
    focused = bridge.call(atspi.Component.grab_focus, title.node)
    mark("focus-return", {"acknowledged": focused is True})
    if focused is not True:
        raise ProbeError("AT_SPI_FIXTURE_FOCUS_NOT_ACKNOWLEDGED")
    focus_state = False
    refreshed = False
    for index in range(6):
        state = bridge.call(title.node.get_state_set)
        if state.contains(atspi.StateType.FOCUSED):
            focus_state = True
            break
        # AT-SPI may cache FOCUSED from before grab_focus. Refresh only this
        # already PID-verified node once; never discard the application tree,
        # enumerate other windows, invoke another focus, or send input before
        # the subsequent fresh state returns positively.
        if index == 1:
            mark("focus-cache-refresh-intent",
                 {"scope": "single-target-only", "input_sent": False})
            bridge.call(title.node.clear_cache_single)
            refreshed = True
            mark("focus-cache-refresh-return", {"finished": True})
        drain(glib)
        time.sleep(0.2)
    mark("focus-state", {"focused": focus_state, "single_target_cache_refreshed": refreshed})
    if not focus_state or bridge.pid(app) != pid or bridge.owner_pid(title.node) != pid:
        raise ProbeError("AT_SPI_FIXTURE_FOCUS_STATE_UNVERIFIED")
    if bridge.read_text(title.node) != "":
        raise ProbeError("AT_SPI_FIXTURE_VALUE_CHANGED_BEFORE_INPUT")
    digest = hashlib.sha256(VALUE.encode("utf8")).hexdigest()
    mark("type-intent", {"value_sha256": digest, "mode": "AT_SPI_KEY_STRING", "one_attempt": True})
    # One registry-delivered input method string, not an EditableText API
    # bypass, not XTest, not JS evaluation. From here, UNKNOWN forbids replay.
    accepted = bridge.call(atspi.generate_keyboard_event, 0, VALUE, atspi.KeySynthType.STRING)
    mark("type-return", {"acknowledged": accepted is True})
    if accepted is not True:
        raise ProbeError("AT_SPI_FIXTURE_TYPE_RETURN_UNCONFIRMED")
    observed = False
    for _ in range(12):
        read = bridge.read_text(title.node)
        if read == VALUE:
            observed = True
            break
        if read not in ("", VALUE):
            raise ProbeError("AT_SPI_FIXTURE_UNEXPECTED_EDITED_CONTENT")
        drain(glib)
        time.sleep(0.3)
    mark("readback", {"matches_expected": observed, "value_sha256": digest})
    if not observed:
        raise ProbeError("AT_SPI_FIXTURE_TYPE_EFFECT_UNCONFIRMED")
    return {"status": "TEXT_VERIFIED_NO_CREATE", "focus_acknowledged": True,
            "one_registry_input_event": True, "readback_sha256": digest,
            "editor_actions": 1, "product_creates": 0,
            "Motionwright_product_executed": False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--ready",required=True,type=Path)
    args=parser.parse_args()
    repo, source = admission(args.output,args.ready)
    scope=args.output.parent
    record(scope/"original-intent.json", {
        "schema":"motionwright.atspi-keyboard-fixture-intent/1",
        "source_sha":source,"input_attempts_max":1,"project_or_native_effects":False,
        "retry":"FORBIDDEN","observed_utc":datetime.now(timezone.utc).isoformat()})
    result={"schema":"motionwright.atspi-keyboard-fixture/1","source_sha":source,
            "status":"NOT_SENT","original_attempt_replayed":False,
            "native_render_or_save":False,"formal_G2":"NOT_GRANTED","formal_G3":"STOP"}
    # The same Ubuntu system source must expose accessibility BEFORE GTK
    # starts, or WebKit may never advertise the registered entry.
    enable_session()
    fixture_script=repo/"tooling/ci_linux_webkit_editable_matrix.py"
    with (scope/"fixture-server.stdout").open("xb") as log:
        child=subprocess.Popen([sys.executable,"-B",str(fixture_script),
                                "--serve","--ready",str(args.ready)],cwd=repo,
                               stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            for _ in range(60):
                if args.ready.is_file():break
                if child.poll() is not None:raise ProbeError("AT_SPI_FIXTURE_SERVER_EXITED")
                time.sleep(0.2)
            if not args.ready.is_file():
                raise ProbeError("AT_SPI_FIXTURE_SERVER_NOT_READY")
            ready=json.loads(args.ready.read_bytes())
            if ready.get("pid") != child.pid:
                raise ProbeError("AT_SPI_FIXTURE_PROCESS_IDENTITY_CHANGED")
            import gi
            gi.require_version("Atspi","2.0")
            from gi.repository import Atspi, GLib
            if Atspi.init() not in (0, 1):
                raise ProbeError("AT_SPI_FIXTURE_INITIALIZATION_FAILED")
            Atspi.set_timeout(800,2500)
            bridge=AtspiBridge(Atspi,deadline=time.monotonic()+25)
            app,field=locate_title(bridge,GLib,child.pid)
            result["status"]="FOCUS_UNKNOWN"
            result.update(single_input(bridge,Atspi,GLib,app,field,child.pid,scope/"stages"))
        except Exception as error:
            if isinstance(error,ProbeError):
                result["failure"]=str(error)[:150]
            else:
                result["failure_type"]=type(error).__name__
            if (scope/"stages/type-intent.json").exists():
                result["status"]="TYPE_EFFECT_UNKNOWN"
        finally:
            child.terminate()
            try:child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                child.kill();child.wait(timeout=4)
    record(args.output,result)
    print("atspi-registered-keyboard-fixture="+result["status"])
    return 0 if result["status"]=="TEXT_VERIFIED_NO_CREATE" else 2


if __name__=="__main__":
    raise SystemExit(main())
