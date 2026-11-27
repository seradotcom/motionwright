"""Drive the installed AppImage's AAC31 render, playback and cold reopen.

Host preparation owns bundle/source admission, the genuine registered provider
connection, the pre-imported fixture and sealing. This lane never supplies a
new authorization via IPC, changes the profile or redispatches a render.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
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


class ReviewBridge(ui.AtspiBridge):
    """Read exact labels from the target WebKit tree and invoke standard actions."""

    def __init__(self, api, *, deadline, glib):
        super().__init__(api, deadline=deadline, glib=glib)
        self.roles[api.Role.TOGGLE_BUTTON] = "button"

    def view(self, node):
        original = super().view(node)
        if "visible" not in original.states or "defunct" in original.states:
            return original
        name = original.name or self.call(node.get_name)
        return ui.View(node, original.role, name or "", original.states, original.interfaces)

    def pump(self):
        context = self.glib.MainContext.default()
        for _ in range(64):
            if not self.call(context.pending):
                return
            self.call(context.iteration, False)

    def tree(self, binding):
        self.pump()
        owner = ui.unique(
            [app for app in self.applications() if self.pid(app) == binding["pid"]],
            "AAC31_APPLICATION",
        )
        return ui.walk(self, owner)


def wait_for(bridge, operation):
    while time.monotonic() < bridge.deadline:
        try:
            return operation()
        except ui.NotReady:
            time.sleep(0.15)
    raise ui.ProbeError("AAC31_UI_DEADLINE")


def exact(bridge, binding, label, *, button=False, enabled=False):
    found = ui.unique(
        [view for view in bridge.tree(binding) if view.name == label
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


def invoke(bridge, binding, view, journal, label):
    same_window(bridge, binding, view)
    if "showing" not in view.states:
        require(bridge.call(bridge.api.Component.scroll_to, view.node,
                            bridge.api.ScrollType.ANYWHERE) is True,
                "AAC31 control could not be scrolled into its own window")
    same_window(bridge, binding, view)
    actions = bridge.actions(view.node)
    allowed = [index for index, name in enumerate(actions)
               if name.casefold() in {"click", "press", "activate", "play"}]
    require(len(allowed) == 1, "AAC31 control has no unique standard action")
    journal.put(label + "-intent", {"control": view.name, "binding": binding, "automatic_retry": False})
    started = time.monotonic_ns()
    require(bridge.invoke(view.node, allowed[0]) is True, "AAC31 action is unconfirmed")
    journal.put(label + "-acknowledged", {"control": view.name, "observed_click": True})
    return started


def press(bridge, binding, label, journal, stage):
    view = wait_for(bridge, lambda: exact(bridge, binding, label, button=True, enabled=True))
    return invoke(bridge, binding, view, journal, stage)


def player_proof(bridge, binding, player, journal, gdk, evidence):
    same_window(bridge, binding, player)
    extents = bridge.call(bridge.api.Component.get_extents, player.node, bridge.api.CoordType.SCREEN)
    panel = gdk.get_default_root_window()
    w = min(extents.width, extents.height * 16 // 9)
    h = w * 9 // 16
    x = extents.x + (extents.width - w) // 2
    y = extents.y + (extents.height - h) // 2
    require(panel is not None and w >= 160 and h >= 90 and x >= 0 and y >= 0
            and x + w <= panel.get_width() and y + h <= panel.get_height(),
            "AAC31 WebView video content is outside the owned X11 display")

    def capture():
        picture = gdk.pixbuf_get_from_window(panel, x, y, w, h)
        require(picture is not None and picture.get_width() == w and picture.get_height() == h,
                "AAC31 video viewport capture failed")
        return PlaybackFrame(w, h, picture.get_rowstride(), picture.get_n_channels(),
                             bytes(picture.get_pixels()))

    def play_control():
        expected = {"play", "play video"}
        play = [
            view for view in ui.walk(bridge, player.node)
            if view.name.casefold() in expected and view.role == "button"
            and {"visible", "enabled", "sensitive"} <= view.states
        ]
        return ui.unique(play, "AAC31_VIDEO_PLAY")

    play = wait_for(bridge, play_control)
    began = invoke(bridge, binding, play, journal, "play-original")
    observed = observe_playback(
        capture, play_intent_ns=began,
        deadline_ns=min(began + 4_000_000_000, int(bridge.deadline * 1e9)),
    )
    same_window(bridge, binding, player)
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


def worker(request, phase, binding, seconds):
    import gi
    gi.require_version("Atspi", "2.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Atspi, Gdk, GLib

    database = Path(request["database"])
    output = Path(request["output"])
    source = Source(**request["source"])
    bridge = ReviewBridge(Atspi, deadline=time.monotonic() + seconds, glib=GLib)
    journal = ui.DiskJournal(Path(request["evidence"]) / (phase + "-ui"))
    try:
        return operate_window(request, phase, binding, bridge, journal, Gdk,
                              database, output, source)
    finally:
        journal.close()


def operate_window(request, phase, binding, bridge, journal, gdk, database, output, source):
    def project_or_editor():
        try:
            return exact(bridge, binding, "Deliver", button=True, enabled=True)
        except ui.NotReady:
            return exact(bridge, binding, "Open " + request["project_title"],
                         button=True, enabled=True)

    initial = wait_for(bridge, project_or_editor)
    if initial.name != "Deliver":
        invoke(bridge, binding, initial, journal, "open-saved-project")
    press(bridge, binding, "Deliver", journal, "open-deliver")

    if phase == "render":
        press(bridge, binding, "Check hybrid sources", journal, "check-source")
        require(not rows(database, source), "AAC31 mux was already attempted for this saved source")
        press(bridge, binding, "Create hybrid master", journal, "create-one-master")

        def original_completed():
            status = rows(database, source)
            if len(status) <= 1:
                if any(item["stage"] != "dispatching" for item in status):
                    raise ValueError("original AAC31 mux outcome is unconfirmed")
                raise ui.NotReady("AAC31_ORIGINAL_STILL_RUNNING")
            return original_master(database, source, output)

        master = wait_for(bridge, original_completed)
        wait_for(bridge, lambda: exact(bridge, binding, "Completed original hybrid master"))
        wait_for(bridge, lambda: exact(bridge, binding, master.sha256))
    else:
        require(phase == "reopen", "invalid installed AAC31 phase")
        master = original_master(database, source, output)

    press(bridge, binding, "Find saved MP4s", journal, "find-saved")
    wait_for(bridge, lambda: exact(bridge, binding, master.sha256))
    pressed = "Review saved original " + master.id
    press(bridge, binding, pressed, journal, "select-original")
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


def run_installed(request_path: Path) -> dict:
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

    for phase in ("render", "reopen"):
        started = time.monotonic()
        deadline = started + (seconds if phase == "render" else 45)
        observation = {}
        journal = evidence / (phase + "-ui")
        journal.mkdir(mode=0o700)

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
            remaining = int(deadline - time.monotonic())
            require(remaining > 0, "AAC31 window has no remaining budget")
            argv = [sys.executable, "-B", "-E", "-s", __file__, "--worker", phase,
                    "--request", str(request_path), "--binding", json.dumps(binding),
                    "--seconds", str(remaining)]
            # The stdout/stderr originals remain exclusive files even on a
            # timeout. No cancellation result is treated as no effect.
            with (evidence / (phase + "-ui.stdout")).open("xb") as stdout, (
                    evidence / (phase + "-ui.stderr")).open("xb") as stderr:
                child = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=stdout,
                                       stderr=stderr, timeout=remaining, check=False)
            require(child.returncode == 0, "installed AAC31 UI observation failed")
            result_path = evidence / (phase + "-ui.stdout")
            regular_file(result_path, 64 * 1024)
            result = json.loads(result_path.read_bytes())
            require(result["binding"] == binding and result["phase"] == phase,
                    "AAC31 UI report changed its launch identity")
            screens[phase] = result
            return True

        try:
            with supervise_own_processes(), owned_process_scope(
                    evidence / (phase + "-process-closure.json")):
                run_probe(app_run, profile, seconds=int(deadline - started),
                          window_interaction=interaction)
        finally:
            if (profile / "app-run.log").is_file():
                log = profile / "app-run.log"
                regular_file(log, 4 * 1024 * 1024)
                with (evidence / (phase + "-app-run.log")).open("xb") as retained:
                    retained.write(log.read_bytes())

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
    parser.add_argument("--worker", choices=("render", "reopen"))
    parser.add_argument("--binding")
    parser.add_argument("--seconds", type=int)
    args = parser.parse_args()
    if args.worker:
        require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true",
                "expected an owned Linux Actions window")
        result = worker(json.loads(args.request.read_bytes()), args.worker,
                        json.loads(args.binding), args.seconds)
    else:
        result = run_installed(args.request)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
