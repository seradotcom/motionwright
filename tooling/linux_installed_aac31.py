"""Drive the installed AppImage's AAC31 render, playback and cold reopen.

Host preparation owns bundle/source admission, the genuine registered provider
connection, the pre-imported fixture and sealing. This lane never supplies a
new authorization via IPC, changes the profile or redispatches a render.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import faulthandler
from hashlib import sha256
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time

import linux_first_use_accessible as ui
from linux_gui_probe import run_probe
from linux_hybrid_clock_proof import observe_clock
from linux_hybrid_original_receipt import Source, original_master, require, rows
from linux_hybrid_playback_frames import PlaybackFrame, observe_playback
from native_host_installed_shared_state import (
    executing_desktop, owned_process_scope, publish, supervise_own_processes,
)
from source_checkout_observation import regular_file

PLAYER = "Verified native MP4 review player"
METADATA = "1920 × 1080 · 1.03 s decoded metadata"
ACTION_READY = frozenset({"visible", "enabled", "sensitive"})


class ReviewBridge(ui.AtspiBridge):
    """Read exact labels from the target WebKit tree and invoke standard actions."""

    def __init__(self, api, *, deadline, glib):
        super().__init__(api, deadline=deadline, glib=glib)
        self.roles[api.Role.TOGGLE_BUTTON] = "button"
        self.roles[api.Role.LIST_ITEM] = "list item"
        for symbol, kind in (("STATIC", "text"), ("LABEL", "text"), ("HEADING", "heading"),
                             ("STATUS_BAR", "status"), ("SECTION", "section"), ("LANDMARK", "section"),
                             ("VIDEO", "video"), ("PANEL", "section"), ("GROUPING", "section")):
            role = getattr(api.Role, symbol, None)
            if role is not None:
                self.roles[role] = kind
        self._button_names_only = False

    def view(self, node):
        role = self.roles.get(self.call(node.get_role), "other")
        state = self.call(node.get_state_set)
        states = frozenset(name for name, value in self.states.items() if state.contains(value))
        named = role == "button" or (not self._button_names_only and role != "other")
        name = self.call(node.get_name) if named and "visible" in states and "defunct" not in states else ""
        return ui.View(node, role, name or "", states, frozenset())

    def refresh_view(self, node):
        self.call(node.clear_cache_single)
        return self.view(node)

    def pump(self):
        context = self.glib.MainContext.default()
        for _ in range(64):
            if not self.call(context.pending):
                return
            self.call(context.iteration, False)

    def tree(self, binding, *, buttons_only=False):
        self.pump()
        owner = ui.unique(
            [app for app in self.applications() if self.pid(app) == binding["pid"]],
            "AAC31_APPLICATION",
        )
        self._button_names_only = buttons_only
        try:
            return ui.walk(self, owner)
        finally:
            self._button_names_only = False


def wait_for(bridge, operation, *, seconds=None):
    inherited = bridge.deadline
    if seconds is not None:
        bridge.deadline = min(inherited, time.monotonic() + seconds)
    last = None
    try:
        while time.monotonic() < bridge.deadline:
            try:
                return operation()
            except ui.NotReady as missing:
                last = missing
                time.sleep(0.15)
        raise ui.ProbeError("AAC31_UI_DEADLINE") from last
    finally:
        bridge.deadline = inherited


def exact(bridge, binding, label, *, button=False, enabled=False, within=None):
    views = bridge.tree(binding, buttons_only=button) if within is None else ui.walk(bridge, within)
    found = ui.unique(
        [view for view in views if view.name == label
         and "visible" in view.states and "defunct" not in view.states
         and (not button or view.role == "button")],
        "EXACT_AAC31_CONTROL",
    )
    if enabled and not {"enabled", "sensitive"} <= found.states:
        raise ui.NotReady("AAC31_CONTROL_NOT_ENABLED")
    return found


def same_window(bridge, binding, view):
    require(ui.x11_identity(deadline=bridge.deadline) == binding
            and bridge.owner_pid(view.node) == binding["pid"],
            "AAC31 installed window or control owner changed")


def bring_into_view(bridge, binding, view):
    same_window(bridge, binding, view)
    if "showing" not in view.states:
        require(bridge.call(bridge.api.Component.scroll_to, view.node,
                            bridge.api.ScrollType.ANYWHERE) is True,
                "AAC31 control could not be scrolled into its own window")

        def observed():
            bridge.pump()
            current = bridge.refresh_view(view.node)
            require((current.name, current.role) == (view.name, view.role),
                    "AAC31 control changed while scrolling")
            if not ui.VISIBLE <= current.states or "defunct" in current.states:
                raise ui.NotReady("AAC31_CONTROL_NOT_SHOWING")
            return current

        view = wait_for(bridge, observed)
        same_window(bridge, binding, view)
    return view


def prepare_action(bridge, binding, view):
    # AT-SPI resolves the action by accessible object. Screen coordinates
    # and the visible viewport are required separately for video readback.
    same_window(bridge, binding, view)
    actions = bridge.actions(view.node)
    allowed = [index for index, name in enumerate(actions)
               if name.casefold() in {"click", "press", "activate", "play"}]
    require(len(allowed) == 1, "AAC31 control has no unique standard action")
    current = bridge.refresh_view(view.node)
    require((current.name, current.role) == (view.name, "button")
            and ACTION_READY <= current.states
            and "defunct" not in current.states, "AAC31 action target changed before dispatch")
    same_window(bridge, binding, current)
    return current, allowed[0]


def invoke(bridge, binding, view, journal, label):
    view, index = prepare_action(bridge, binding, view)
    journal.put(label + "-intent", {"control": view.name, "binding": binding, "automatic_retry": False})
    started = time.monotonic_ns()
    require(bridge.invoke(view.node, index) is True, "AAC31 action is unconfirmed")
    journal.put(label + "-acknowledged", {"control": view.name, "observed_click": True})
    return started


def wait_for_button(bridge, binding, label, journal, stage, *, seconds=30):
    try:
        view = wait_for(
            bridge, lambda: exact(bridge, binding, label, button=True, enabled=True), seconds=seconds,
        )
    except Exception as error:
        try:
            journal.put(stage + "-unavailable", {
                "control": label, "failure": type(error).__name__, "detail": str(error.__cause__ or error)[:160],
            })
        except OSError as custody_error:
            error.add_note("Could not retain the unavailable-control diagnostic: " + str(custody_error))
        raise
    return view


def press(bridge, binding, label, journal, stage, *, seconds=30):
    view = wait_for_button(bridge, binding, label, journal, stage, seconds=seconds)
    return invoke(bridge, binding, view, journal, stage)


def saved_review_control(bridge, binding, master):
    label = "Review saved original " + master.id
    matches = []
    for row in bridge.tree(binding):
        if row.role != "list item" or "visible" not in row.states or "defunct" in row.states:
            continue
        try:
            control = exact(bridge, binding, label, button=True, enabled=True, within=row.node)
        except ui.NotReady:
            continue
        matches.append((row, control))
    row, control = ui.unique(matches, "AAC31_SAVED_RECEIPT_ROW")
    exact(bridge, binding, master.sha256, within=row.node)
    return control


def player_proof(bridge, binding, player, journal, gdk, evidence):
    # The review panel lives below the delivery controls. Resolve the visible
    # play control first so later scrolling cannot invalidate the capture area.
    player = bring_into_view(bridge, binding, player)

    def play_control():
        expected = {"play", "play video"}
        play = [
            view for view in ui.walk(bridge, player.node)
            if view.name.casefold() in expected and view.role == "button"
            and ACTION_READY <= view.states and "defunct" not in view.states
        ]
        return ui.unique(play, "AAC31_VIDEO_PLAY")

    play = wait_for(bridge, play_control)

    def screen_bounds():
        bounds = bridge.call(bridge.api.Component.get_extents, player.node, bridge.api.CoordType.SCREEN)
        return bounds.x, bounds.y, bounds.width, bounds.height

    initial_bounds = screen_bounds()
    left, top, width, height = initial_bounds
    panel = gdk.get_default_root_window()
    w = min(width, height * 16 // 9)
    h = w * 9 // 16
    x = left + (width - w) // 2
    y = top + (height - h) // 2
    require(panel is not None and w >= 160 and h >= 90 and x >= 0 and y >= 0
            and x + w <= panel.get_width() and y + h <= panel.get_height(),
            "AAC31 WebView video content is outside the owned X11 display")

    def capture():
        picture = gdk.pixbuf_get_from_window(panel, x, y, w, h)
        require(picture is not None and picture.get_width() == w and picture.get_height() == h,
                "AAC31 video viewport capture failed")
        return PlaybackFrame(w, h, picture.get_rowstride(), picture.get_n_channels(),
                             bytes(picture.get_pixels()))

    began = invoke(bridge, binding, play, journal, "play-original")
    observed = observe_playback(
        capture, play_intent_ns=began,
        deadline_ns=min(began + 4_000_000_000, int(bridge.deadline * 1e9)),
    )
    same_window(bridge, binding, player)
    require(screen_bounds() == initial_bounds, "AAC31 player moved during pixel observation")
    result = []
    for scene, (frame, timestamp) in zip(("motion-canvas", "manim"), observed, strict=True):
        filename = scene + "-viewport.original.rgb"
        with (evidence / filename).open("xb") as original:
            original.write(frame.pixels)
            original.flush()
            os.fsync(original.fileno())
        result.append({
            "scene": scene, "observed_ns": timestamp, "original": filename,
            "sha256": sha256(frame.pixels).hexdigest(), "bytes": len(frame.pixels),
            "width": frame.width, "height": frame.height, "row_stride": frame.row_stride,
            "channels": frame.channels,
        })
    journal.put("played-two-authored-scenes", result)
    return result


def capture_delivery_window(evidence, binding, deadline, gdk, gdk_x11):
    require(ui.x11_identity(deadline=deadline) == binding, "AAC31 diagnostic window changed")
    display = gdk.Display.open(os.environ["DISPLAY"])
    require(display is not None, "AAC31 diagnostic display is unavailable")
    window = gdk_x11.X11Window.foreign_new_for_display(display, int(binding["window"], 16))
    require(window is not None, "AAC31 diagnostic window is unavailable")
    width, height = window.get_width(), window.get_height()
    require(320 <= width <= 1920 and 180 <= height <= 1080, "AAC31 diagnostic window is outside bounds")
    image = gdk.pixbuf_get_from_window(window, 0, 0, width, height)
    require(image is not None and ui.x11_identity(deadline=deadline) == binding,
            "AAC31 diagnostic capture lost its window")
    channels, stride = image.get_n_channels(), image.get_rowstride()
    pixels = bytes(image.get_pixels())
    require(channels in {3, 4} and width * channels <= stride <= width * channels + 64
            and stride * (height - 1) + width * channels <= len(pixels) <= stride * height,
            "AAC31 diagnostic pixel layout is invalid")
    with (evidence / "delivery-window.original.rgb").open("xb") as stream:
        stream.write(pixels)
        stream.flush()
        os.fsync(stream.fileno())
    publish(evidence / "delivery-window.json", {"binding": binding, "width": width, "height": height,
                                               "channels": channels, "row_stride": stride,
                                               "sha256": sha256(pixels).hexdigest()})



def worker(request, phase, binding, seconds):
    import gi
    gi.require_version("Atspi", "2.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Atspi, Gdk, GLib

    deadline = time.monotonic() + seconds
    # A libatspi call can outlive its advertised IPC timeout. Retain the
    # original stack before the external supervisor stops a stalled worker.
    faulthandler.dump_traceback_later(10, file=sys.stderr)
    if phase in {"inspect-delivery", "capture-window"}:
        gi.require_version("GdkX11", "3.0")
        from gi.repository import GdkX11

        capture_delivery_window(Path(request["evidence"]), binding, deadline, Gdk, GdkX11)
        if phase == "capture-window":
            return {"phase": phase, "binding": binding}
    require(Atspi.init() in (0, 1), "AAC31_ATSPI_INIT")
    Atspi.set_timeout(800, 2500)

    database = Path(request["database"])
    output = Path(request["output"])
    source = Source(**request["source"])
    bridge = ReviewBridge(Atspi, deadline=deadline, glib=GLib)
    journal = ui.DiskJournal(Path(request["evidence"]) / (phase + "-ui"))
    try:
        if phase.startswith("open-project-"):
            open_project_window(request, binding, bridge, journal)
        elif phase.startswith("navigate-"):
            press(bridge, binding, "Deliver", journal, "open-deliver")
        elif phase == "check-source":
            journal.put("before-check-source", {"binding": binding})
            press(bridge, binding, "Check hybrid sources", journal, "check-source")
        elif phase == "inspect-delivery":
            journal.put("before-check-source", {"binding": binding})
            view = wait_for_button(bridge, binding, "Check hybrid sources", journal, "check-source")
            journal.put("located-check-source", {"control": view.name})
            prepare_action(bridge, binding, view)
            state = {"phase": phase, "binding": binding, "source_check_enabled": True}
            journal.put("delivery-controls", state)
            return state
        elif phase == "create":
            require(not rows(database, source), "AAC31 mux was already attempted for this saved source")
            press(bridge, binding, "Create hybrid master", journal, "create-one-master", seconds=90)
        else:
            return operate_window(request, phase, binding, bridge, journal, Gdk,
                                  database, output, source)
        return {"phase": phase, "binding": binding}
    finally:
        faulthandler.cancel_dump_traceback_later()
        journal.close()


def open_project_window(request, binding, bridge, journal):
    def project_or_editor():
        try:
            return exact(bridge, binding, "Deliver", button=True, enabled=True)
        except ui.NotReady:
            return exact(bridge, binding, "Open " + request["project_title"],
                         button=True, enabled=True)

    initial = wait_for(bridge, project_or_editor, seconds=30)
    if initial.name != "Deliver":
        invoke(bridge, binding, initial, journal, "open-saved-project")


def await_original(database, source, output, deadline):
    while time.monotonic() < deadline:
        status = rows(database, source)
        if len(status) >= 2:
            return original_master(database, source, output)
        require(all(item["stage"] == "dispatching" for item in status), "original AAC31 mux outcome is unconfirmed")
        time.sleep(0.15)
    raise ui.ProbeError("AAC31_ORIGINAL_COMPLETION_DEADLINE")


def operate_window(request, phase, binding, bridge, journal, gdk, database, output, source):
    require(phase in {"render", "reopen"}, "invalid installed AAC31 review phase")
    master = original_master(database, source, output)
    if phase == "render":
        completed = wait_for(bridge, lambda: exact(bridge, binding, "Completed original hybrid master"))
        wait_for(bridge, lambda: exact(bridge, binding, master.sha256, within=completed.node))

    press(bridge, binding, "Find saved MP4s", journal, "find-saved")
    review = wait_for(bridge, lambda: saved_review_control(bridge, binding, master))
    invoke(bridge, binding, review, journal, "select-original")
    player = wait_for(bridge, lambda: exact(bridge, binding, PLAYER))
    wait_for(bridge, lambda: exact(bridge, binding, METADATA))
    same_window(bridge, binding, player)
    played = None
    if phase == "render":
        played = player_proof(bridge, binding, player, journal, gdk, Path(request["evidence"]))
    durable = original_master(database, source, output)
    require(durable == master, "AAC31 original changed while the WebView was open")
    record = asdict(master)
    record["path"] = str(master.path)
    return {"phase": phase, "binding": binding, "master": record,
            "decoded_metadata": METADATA, "playback": played}


def admitted_owner_connection(request, source, output):
    path, digest = request.get("connection"), request.get("connection_sha256")
    if path is None and digest is None:
        return None
    require(isinstance(path, str) and isinstance(digest, str)
            and re.fullmatch(r"[a-f0-9]{64}", digest) is not None,
            "incomplete explicit AAC31 owner connection identity")
    connection = Path(path)
    require(connection == output.parent / "config" / "motionwright-connection.json"
            and connection == connection.resolve(strict=True),
            "AAC31 connection is not in the canonical broker layout")
    observed = regular_file(connection, 64 * 1024)
    require(observed["sha256"] == digest, "AAC31 original owner connection changed")
    document = json.loads(connection.read_bytes())
    require(document.get("schema") == "motionwright-semwright-connection/1"
            and document.get("resource") == "project:" + source.project
            and document.get("output_root") == str(output)
            and document.get("socket") == str(output.parent / "runtime" / "broker.sock")
            and regular_file(connection, 64 * 1024) == observed,
            "AAC31 original owner connection does not bind this source")
    return connection


def observe_stage(request_path, evidence, phase, binding, deadline, *, limit=None):
    remaining = int(deadline - time.monotonic())
    if limit is not None:
        remaining = min(remaining, limit)
    require(remaining > 0, "AAC31 window has no remaining budget")
    (evidence / (phase + "-ui")).mkdir(mode=0o700)
    argv = [sys.executable, "-B", "-E", "-s", __file__, "--worker", phase,
            "--request", str(request_path), "--binding", json.dumps(binding),
            "--seconds", str(remaining)]
    result_path = evidence / (phase + "-ui.stdout")
    # Each bounded process keeps its originals even when an IPC call never returns.
    with result_path.open("xb") as stdout, (evidence / (phase + "-ui.stderr")).open("xb") as stderr:
        child = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=stdout,
                               stderr=stderr, timeout=remaining, check=False)
    require(child.returncode == 0, "installed AAC31 UI observation failed: " + phase)
    regular_file(result_path, 64 * 1024)
    result = json.loads(result_path.read_bytes())
    require(result["binding"] == binding and result["phase"] == phase,
            "AAC31 UI report changed its launch identity")
    return result


def observe_window(request_path, evidence, phase, binding, deadline, database, source, output,
                   *, inspect_delivery=False):
    try:
        observe_stage(request_path, evidence, "open-project-" + phase, binding, deadline, limit=25)
        observe_stage(request_path, evidence, "navigate-" + phase, binding, deadline, limit=25)
        if inspect_delivery:
            return observe_stage(request_path, evidence, "inspect-delivery", binding, deadline, limit=25)
    except Exception as error:
        if inspect_delivery and not (evidence / "delivery-window.original.rgb").exists():
            try:
                observe_stage(request_path, evidence, "capture-window", binding, deadline, limit=10)
            except Exception as capture_error:
                error.add_note("AAC31 diagnostic capture failed: " + str(capture_error))
        raise
    if phase == "render":
        observe_stage(request_path, evidence, "check-source", binding, deadline, limit=30)
        observe_stage(request_path, evidence, "create", binding, deadline, limit=90)
        await_original(database, source, output, deadline)
    return observe_stage(request_path, evidence, phase, binding, deadline)



def run_installed(request_path: Path, *, inspect_delivery=False) -> dict:
    """One owned AppRun render/play session and one fresh AppRun reopen."""
    request = json.loads(request_path.read_bytes())
    source = Source(**request["source"])
    evidence = Path(request["evidence"])
    database = Path(request["database"])
    output = Path(request["output"])
    binary = Path(request["binary"])
    profile = Path(request["profile"])
    app_run = Path(request["app_run"])
    seconds = request["seconds"]
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and os.environ.get("GITHUB_SHA") == request["source_sha"],
            "expected the admitted original Linux Actions execution")
    lock_path = Path(__file__).resolve().parents[1] / "SOURCE_LOCK.json"
    semwright = json.loads(lock_path.read_text(encoding="utf-8"))["dependencies"]["semwright"]
    require(re.fullmatch(r"[a-f0-9]{40}", request["source_sha"]) is not None
            and re.fullmatch(r"[a-f0-9]{40}", request["semwright_sha"]) is not None
            and request["semwright_sha"] == semwright["revision"], "AAC31 source or SDK pin differs")
    require(type(seconds) is int and 45 <= seconds <= 600, "invalid AAC31 window deadline")
    require(evidence == evidence.resolve(strict=True) and not rows(database, source)
            and profile == profile.resolve(strict=True) and profile.is_dir()
            and database.is_relative_to(profile / "data"), "expected a new installed AAC31 case")
    screens = {}

    for phase in (("render",) if inspect_delivery else ("render", "reopen")):
        started = time.monotonic()
        deadline = started + (seconds if phase == "render" else 45)
        observation = {}

        def interaction(_profile):
            # Refuse a second invocation even if the observer cannot determine
            # whether the first child delivered its render action.
            require(not observation, "AAC31 interaction already attempted")
            try:
                binding = ui.x11_identity(deadline=deadline)
            except ui.NotReady:
                return False
            observed = executing_desktop(binding, binary)
            observation["binding"] = binding
            observation["executing_binary"] = observed
            publish(evidence / (phase + "-window-intent.json"), observation)
            screens[phase] = observe_window(request_path, evidence, phase, binding, deadline,
                                            database, source, output, inspect_delivery=inspect_delivery)
            return True

        owner_connection = admitted_owner_connection(request, source, output)
        try:
            with supervise_own_processes(), owned_process_scope(
                    evidence / (phase + "-process-closure.json")):
                run_probe(app_run, profile, seconds=int(deadline - started),
                          window_interaction=interaction, owner_connection=owner_connection)
        finally:
            if (profile / "app-run.log").is_file():
                log = profile / "app-run.log"
                regular_file(log, 4 * 1024 * 1024)
                with (evidence / (phase + "-app-run.log")).open("xb") as retained:
                    retained.write(log.read_bytes())

        require(admitted_owner_connection(request, source, output) == owner_connection,
                "AAC31 owner connection changed after AppRun")
        if inspect_delivery:
            require(not rows(database, source), "AAC31 diagnostic must precede any original dispatch")
            publish(evidence / "delivery-observation.json", screens[phase])
            return screens[phase]
        master = original_master(database, source, output)
        require(screens[phase]["master"]["id"] == master.id
                and screens[phase]["master"]["sha256"] == master.sha256,
                "AAC31 original changed after the installed process closed")
        if phase == "render":
            proof = observe_clock(master, Path(request["wave"]), request["wave_sha256"],
                                  ffprobe=Path(request["ffprobe"]), ffprobe_sha=request["ffprobe_sha256"],
                                  ffmpeg=Path(request["ffmpeg"]), ffmpeg_sha=request["ffmpeg_sha256"])
            publish(evidence / "original-mp4-clock.json", proof)

    require(screens["render"]["master"] == screens["reopen"]["master"]
            and screens["render"]["binding"] != screens["reopen"]["binding"],
            "AAC31 cold reopen did not observe the same original in a fresh instance")
    report = {"schema": "motionwright.installed-aac31/1", "source_sha": request["source_sha"],
              "semwright_sha": request["semwright_sha"], "original_sha256": master.sha256,
              "mux_receipt_id": master.id, "screens": screens, "verified_clock": proof}
    publish(evidence / "installed-aac31.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--inspect-delivery", action="store_true")
    parser.add_argument("--worker", choices=("open-project-render", "open-project-reopen",
                                             "navigate-render", "navigate-reopen", "check-source",
                                             "inspect-delivery", "capture-window", "create", "render", "reopen"))
    parser.add_argument("--binding")
    parser.add_argument("--seconds", type=int)
    args = parser.parse_args()
    if args.worker:
        require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true",
                "expected an owned Linux Actions window")
        result = worker(json.loads(args.request.read_bytes()), args.worker,
                        json.loads(args.binding), args.seconds)
    else:
        result = run_installed(args.request, inspect_delivery=args.inspect_delivery)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
