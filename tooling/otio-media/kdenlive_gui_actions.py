"""PID-scoped AT-SPI actions for the bounded native Kdenlive CI roundtrip.

Every effect has a durable intent and is attempted once. Read-only discovery
may wait for a control; an uncertain action prevents all subsequent actions.
This module neither creates project XML nor decides editorial acceptance.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import time
from typing import Callable

MAX_NODES = 4096
MAX_DEPTH = 40
MAX_CHILDREN = 512
WAIT_SECONDS = 30.0
TIMECODE = re.compile(r"[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{2}\Z")


class GuiRejected(ValueError):
    pass


class GuiUncertain(RuntimeError):
    pass


def require(ok: object, code: str) -> None:
    if not ok:
        raise GuiRejected(code)


def normalized(value: str) -> str:
    return value.replace("&", "").replace("\u2026", "...").strip()


def unique(rows: list[dict], *, code: str) -> dict:
    require(len(rows) == 1, code)
    return rows[0]


def timecode_frames(value: str, fps: int = 30) -> int:
    require(type(value) is str and TIMECODE.fullmatch(value) is not None,
            "native_gui_timecode_not_exact")
    h, m, s, f = (int(part) for part in value.split(":"))
    require(m < 60 and s < 60 and f < fps, "native_gui_timecode_outside_clock")
    return ((h * 60 + m) * 60 + s) * fps + f


def labeled_field(rows: list[dict], labels: tuple[str, ...]) -> dict:
    """Select the sole editable leaf on the observed label's row.

    Native Qt labels do not all expose a buddy relation. The dialog's actual
    accessibility rectangles locate the corresponding field; no fixed screen
    coordinate or model identifier is invented.
    """
    label = unique([r for r in rows if r["visible"] and
                    normalized(r["name"]) in labels and
                    r["role"] == "label"], code="native_gui_label_not_unique")
    lx, ly, lw, lh = label["rect"]
    require(lw > 0 and lh > 0, "native_gui_label_geometry_missing")
    candidates = []
    for row in rows:
        if not (row["visible"] and row["enabled"] and row["editable"]):
            continue
        x, y, w, h = row["rect"]
        if w > 0 and h > 0 and x >= lx + lw and max(ly, y) < min(ly + lh, y + h):
            candidates.append(row)
    # A spin box and its line edit may both expose EditableText. Keep the
    # editable leaf of the same accessibility ancestry, never a second field.
    leaves = [r for r in candidates if not any(
        s["key"].startswith(r["key"] + "/") for s in candidates if s is not r)]
    return unique(leaves, code="native_gui_labeled_field_not_unique")


def check_duration_fields(position: str, crop: str, duration: str) -> None:
    require(tuple(timecode_frames(v) for v in (position, crop, duration))
            == (60, 60, 30), "native_gui_selected_clip_is_not_original_third_clip")


class ActionJournal:
    def __init__(self, emit: Callable[[dict], None],
                 guard: Callable[[], None]):
        self.emit = emit
        self.guard = guard
        self.used: set[str] = set()
        self.blocked = False

    def _record_failure(self, step: str, state: str,
                        original: BaseException) -> None:
        # Custody failure cannot replace the observation or effect failure.
        self.blocked = True
        try:
            event = {"step": step, "state": state, "replayed": False,
                     "monotonic_ns": time.monotonic_ns()}
            if state != "TIMED_OUT_UNKNOWN":
                event.update(error_type=type(original).__name__, error=str(original))
            self.emit(event)
        except BaseException as recording_error:
            original.add_note("native_gui_journal_recording_failed:" +
                              type(recording_error).__name__)

    def perform(self, step: str, target: dict, effect: Callable[[], object],
                *, guard_after: bool = True):
        require(not self.blocked, "native_gui_previous_action_uncertain")
        require(type(step) is str and step and step not in self.used,
                "native_gui_action_replay_refused")
        self.guard()
        self.used.add(step)
        # Only a successfully retained intent permits the first effect.
        self.blocked = True
        self.emit({"step": step, "state": "INTENT", "target": target,
                   "monotonic_ns": time.monotonic_ns()})
        self.blocked = False
        try:
            result = effect()
            require(result is not False, "native_gui_action_rejected_or_unknown")
            if guard_after:
                self.guard()
        except BaseException as original:
            self._record_failure(step, "ERROR_OR_UNKNOWN", original)
            if not isinstance(original, Exception):
                raise
            raise GuiUncertain(step) from original
        # A returned effect without a retained terminal must block later actions.
        self.blocked = True
        self.emit({"step": step, "state": "REQUEST_RETURNED",
                   "monotonic_ns": time.monotonic_ns(),
                   "semantic_effect_verified_here": False})
        self.blocked = False
        return result

    def wait(self, step: str, query: Callable[[], object],
             *, timeout: float = WAIT_SECONDS):
        require(not self.blocked, "native_gui_previous_action_uncertain")
        require(0 < timeout <= WAIT_SECONDS, "native_gui_wait_budget_invalid")
        deadline = time.monotonic() + timeout

        def timed_out() -> None:
            original = GuiUncertain(step)
            self._record_failure(step, "TIMED_OUT_UNKNOWN", original)
            raise original

        while True:
            if time.monotonic() >= deadline:
                timed_out()
            try:
                self.guard()
                # A slow guard must not start another query after the deadline.
                expired_before_query = time.monotonic() >= deadline
                result = None if expired_before_query else query()
            except BaseException as original:
                self._record_failure(step, "OBSERVATION_ERROR_OR_UNKNOWN", original)
                raise
            # This rejects a late result; it does not cancel synchronous AT-SPI.
            if expired_before_query or time.monotonic() >= deadline:
                timed_out()
            if result:
                return result
            time.sleep(0.1)


class AtspiSession:
    def __init__(self, pid: int, journal: ActionJournal):
        # Keep GI optional at import time for offline contract tests.
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        self.api = Atspi
        self.pid = pid
        self.journal = journal
        self.nodes: dict[str, object] = {}
        self.init_returncode = None
        self.discovery_count = 0
        self.first_discovery = None
        self.last_discovery = None
        code = self.api.init()
        self.init_returncode = code if type(code) is int else None
        if type(code) is not int or code not in (0, 1):
            self.journal.blocked = True
            original = GuiRejected("native_gui_accessibility_init_failed")
            try:
                self._record_discovery_failure("atspi.initialize")
            except Exception as recording_error:
                original.add_note("discovery_diagnostic_recording_failed:" +
                                  type(recording_error).__name__)
            raise original
        self.api.set_timeout(1000, 3000)

    def _record_discovery_failure(self, step: str) -> None:
        # Only captured primitive observations; never query AT-SPI on failure.
        self.journal.emit({
            "step": step + ".discovery-diagnostic",
            "state": "OBSERVATION_ERROR_OR_UNKNOWN",
            "expected_pid": self.pid,
            "init_returncode": self.init_returncode,
            "init_admitted": self.init_returncode in (0, 1),
            "discovery_count": self.discovery_count,
            "first_discovery": self.first_discovery,
            "last_discovery": self.last_discovery,
            "native_operation_retried": False,
        })

    def wait_ready(self, phase: str) -> list[dict]:
        try:
            return self.journal.wait(phase + ".accessibility-ready", self.tree)
        except BaseException as original:
            try:
                self._record_discovery_failure(phase + ".accessibility-ready")
            except Exception as recording_error:
                original.add_note("discovery_diagnostic_recording_failed:" +
                                  type(recording_error).__name__)
            raise

    def tree(self) -> list[dict]:
        self.journal.guard()
        desktop = self.api.get_desktop(0)
        require(desktop is not None, "native_gui_accessibility_desktop_absent")
        count = desktop.get_child_count()
        require(0 <= count <= MAX_CHILDREN, "native_gui_desktop_budget")
        apps = [desktop.get_child_at_index(i) for i in range(count)]
        observed = [(app, app.get_process_id()) for app in apps if app is not None]
        require(all(type(pid) is int and 0 <= pid <= 0xffffffff
                    for _, pid in observed), "native_gui_accessibility_pid_invalid")
        owned = [app for app, pid in observed if pid == self.pid]
        discovery = {
            "desktop_child_count": count,
            "null_child_count": count - len(observed),
            "observed_pids": [pid for _, pid in observed],
            "owned_application_count": len(owned),
        }
        self.discovery_count += 1
        if self.first_discovery is None:
            self.first_discovery = discovery
        self.last_discovery = discovery
        if not owned:
            self.nodes = {}
            return []
        require(len(owned) == 1, "native_gui_owned_application_not_unique")
        queue = [("app", owned[0], 0)]
        rows, nodes = [], {}
        while queue:
            key, node, depth = queue.pop(0)
            require(depth <= MAX_DEPTH and len(rows) < MAX_NODES,
                    "native_gui_accessibility_tree_budget")
            require(node.get_process_id() == self.pid,
                    "native_gui_cross_process_accessible_refused")
            state = node.get_state_set()
            role = node.get_role_name()
            visible = state.contains(self.api.StateType.SHOWING)
            enabled = state.contains(self.api.StateType.ENABLED) and state.contains(
                self.api.StateType.SENSITIVE)
            editable = node.get_editable_text_iface() is not None
            text = ""
            interface = node.get_text_iface()
            if interface is not None:
                size = interface.get_character_count()
                require(0 <= size <= 65536, "native_gui_text_budget")
                text = interface.get_text(0, min(size, 1024))
            rect = [0, 0, 0, 0]
            component = node.get_component_iface()
            if component is not None and visible:
                bounds = component.get_extents(self.api.CoordType.SCREEN)
                rect = [int(bounds.x), int(bounds.y), int(bounds.width), int(bounds.height)]
            row = {"key": key, "name": node.get_name()[:512], "role": role,
                   "text": text, "visible": visible, "enabled": enabled,
                   "editable": editable, "rect": rect,
                   "focused": state.contains(self.api.StateType.FOCUSED),
                   "pid": self.pid}
            rows.append(row)
            nodes[key] = node
            count = node.get_child_count()
            require(0 <= count <= MAX_CHILDREN, "native_gui_child_budget")
            queue.extend((key + "/" + str(i), node.get_child_at_index(i), depth + 1)
                         for i in range(count))
        self.journal.guard()
        self.nodes = nodes
        return rows

    def find(self, names: tuple[str, ...], roles: tuple[str, ...],
             *, scope: str | None = None) -> dict | None:
        rows = self.tree()
        matches = [r for r in rows if r["visible"] and r["enabled"]
                   and normalized(r["name"]) in names and r["role"] in roles
                   and (scope is None or r["key"].startswith(scope + "/")
                        or r["key"] == scope)]
        if not matches:
            return None
        return unique(matches, code="native_gui_control_not_unique")

    def action(self, step: str, row: dict, *, exits: bool = False) -> None:
        node = self.nodes[row["key"]]
        require(node.get_process_id() == self.pid, "native_gui_action_wrong_pid")
        interface = node.get_action_iface()
        require(interface is not None, "native_gui_control_has_no_action")
        count = interface.get_n_actions()
        require(0 < count <= 8, "native_gui_action_inventory_invalid")
        actions = [(i, interface.get_action_name(i).lower()) for i in range(count)]
        preferred = [(i, name) for i, name in actions if name in
                     ("press", "click", "activate", "showmenu", "show menu")]
        require(len(preferred) == 1, "native_gui_action_not_unambiguous")
        index, name = preferred[0]
        self.journal.perform(step, {**row, "action": name},
                             lambda: interface.do_action(index), guard_after=not exits)

    def control(self, step: str, names: tuple[str, ...],
                roles: tuple[str, ...]) -> None:
        row = self.journal.wait(step + ".discover", lambda: self.find(names, roles))
        self.action(step, row)

    def menu(self, step: str, *names: str) -> None:
        for index, name in enumerate(names):
            self.control(step + "." + str(index), (name,),
                         ("menu", "menu item", "check menu item"))

    def dialog(self, step: str, names: tuple[str, ...]) -> dict:
        return self.journal.wait(step, lambda: self.find(names, ("dialog", "frame")))

    def descendants(self, row: dict) -> list[dict]:
        return [r for r in self.tree() if r["key"].startswith(row["key"] + "/")]

    def focus(self, step: str, row: dict) -> None:
        node = self.nodes[row["key"]]
        component = node.get_component_iface()
        require(component is not None, "native_gui_control_cannot_focus")
        self.journal.perform(step, row, component.grab_focus)
        self.journal.wait(step + ".focused", lambda: any(
            r["focused"] and (r["key"] == row["key"] or
            r["key"].startswith(row["key"] + "/")) for r in self.tree()))

    def text(self, step: str, row: dict, value: str) -> None:
        self.focus(step + ".focus", row)
        interface = self.nodes[row["key"]].get_editable_text_iface()
        require(interface is not None, "native_gui_field_is_not_editable")
        self.journal.perform(step, {**row, "requested_text": value},
                             lambda: interface.set_text_contents(value))
        self.journal.wait(step + ".readback", lambda: any(
            r["key"] == row["key"] and r["text"] == value for r in self.tree()))

    def key(self, step: str, symbol: int) -> None:
        keys = {0xFF0D: "Return", 0x0031: "1"}
        require(symbol in keys, "native_gui_key_not_in_closed_route")
        # X11 focus is measured independently of the accessibility cache.
        # Explicit --window prevents a later global focus change from retargeting
        # the key to another application's window.
        focus = subprocess.run(["xdotool", "getwindowfocus"], capture_output=True,
                               check=True, timeout=5, text=True).stdout.strip()
        require(focus.isdecimal() and 0 < int(focus) < 2**64,
                "native_gui_X11_focus_window_invalid")
        def target_pid():
            return subprocess.run(["xdotool", "getwindowpid", focus],
                                  capture_output=True, check=True, timeout=5,
                                  text=True).stdout.strip()
        require(target_pid() == str(self.pid), "native_gui_keyboard_focus_not_owned")
        def send():
            result = subprocess.run(
                ["xdotool", "key", "--window", focus, "--clearmodifiers",
                 "--delay", "0", keys[symbol]],
                capture_output=True, check=False, timeout=5)
            require(result.returncode == 0, "native_gui_key_terminal_not_success")
            require(target_pid() == str(self.pid), "native_gui_X11_target_changed")
            return True
        self.journal.perform(step, {"keysym": symbol, "pid": self.pid,
                                   "X11_window": focus}, send)

    def file_dialog(self, step: str, titles: tuple[str, ...], path: Path,
                    button: str) -> None:
        dialog = self.dialog(step + ".dialog", titles)
        field = labeled_field(self.descendants(dialog), ("File name:", "File name", "Name:", "Name"))
        self.text(step + ".filename", field, str(path))
        # Refresh the real dialog rather than using a widget from a prior tree.
        dialog = self.dialog(step + ".dialog-before-submit", titles)
        row = self.journal.wait(step + ".button", lambda: self.find(
            (button,), ("push button",), scope=dialog["key"]))
        self.action(step + ".submit", row)
        self.journal.wait(step + ".closed", lambda: not any(
            r["visible"] and r["role"] in ("dialog", "frame")
            and normalized(r["name"]) in titles for r in self.tree()))

    def save_as(self, step: str, path: Path) -> None:
        require(not path.exists() and not path.is_symlink(), "native_gui_save_target_exists")
        self.menu(step + ".menu", "File", "Save As...")
        self.file_dialog(step, ("Save As", "Save Project"), path, "Save")

    def export_otio(self, step: str, path: Path) -> None:
        require(not path.exists() and not path.is_symlink(), "native_gui_export_target_exists")
        self.menu(step + ".menu", "File", "OpenTimelineIO Export...")
        self.file_dialog(step, ("OpenTimelineIO Export",), path, "Save")

    def ready(self, phase: str) -> None:
        """Observe native startup; acknowledge its sole first-run page once."""
        def startup():
            rows = self.tree()
            dialogs = [r for r in rows if r["visible"] and
                       r["role"] in ("dialog", "frame") and
                       normalized(r["name"]) == "Welcome to Kdenlive"]
            if dialogs:
                dialog = unique(dialogs, code="native_gui_startup_dialog_not_unique")
                require(phase == "import", "native_gui_first_run_reappeared_on_reopen")
                return {"kind": "wizard", "dialog": dialog, "rows": rows}
            files = [r for r in rows if r["visible"] and r["enabled"] and
                     normalized(r["name"]) == "File" and
                     r["role"] in ("menu", "menu item")]
            if files:
                unique(files, code="native_gui_file_menu_not_unique")
                return {"kind": "main", "rows": rows}
            return None
        observed = self.journal.wait(phase + ".startup", startup)
        self.journal.emit({"step": phase + ".startup", "state": "OBSERVED",
                           "native_first_run": observed["kind"],
                           "rows": observed["rows"]})
        if observed["kind"] == "wizard":
            dialog = observed["dialog"]
            rows = [r for r in observed["rows"] if
                    r["key"].startswith(dialog["key"] + "/")]
            require(any(r["visible"] and normalized(r["name"]) ==
                        "Welcome to Kdenlive 25.08.3" for r in rows),
                    "native_gui_startup_version_title_not_exact")
            okay = unique([r for r in rows if r["visible"] and r["enabled"]
                           and r["role"] == "push button" and
                           normalized(r["name"]) == "OK"],
                          code="native_gui_startup_cannot_finish")
            self.action(phase + ".startup-acknowledge", okay)
            self.journal.wait(phase + ".main-ready", lambda: self.find(
                ("File",), ("menu", "menu item")))
            require(not self.find(("Welcome to Kdenlive",), ("dialog", "frame")),
                    "native_gui_startup_dialog_did_not_close")

    def edit_third_clip(self) -> None:
        rows = self.tree()
        monitor_roots = [r for r in rows if r["visible"] and
                         normalized(r["name"]) == "Project Monitor"
                         and r["role"] not in ("menu item", "check menu item", "label")]
        fields = {r["key"]: r for r in rows if r["visible"] and r["enabled"]
                  and r["editable"] and TIMECODE.fullmatch(r["text"]) and any(
                      r["key"].startswith(p["key"] + "/") for p in monitor_roots)}
        leaves = [r for r in fields.values() if not any(
            s["key"].startswith(r["key"] + "/") for s in fields.values() if s is not r)]
        position = unique(leaves, code="native_gui_project_monitor_timecode_not_unique")
        self.text("edit.seek75", position, "00:00:02:15")
        self.key("edit.commit-seek", 0xFF0D)
        # The version-pinned native action maps the '1' key to Video Track 1.
        self.key("edit.select-video-track1", 0x0031)
        self.menu("edit.select-clip", "Timeline", "Selection", "Select Clip")
        self.menu("edit.duration", "Timeline", "Current clip", "Edit Duration")
        dialog = self.dialog("edit.duration-dialog", ("Duration",))
        rows = self.descendants(dialog)
        fields = [labeled_field(rows, (label,)) for label in
                  ("Position:", "Crop start:", "Duration:")]
        check_duration_fields(*(row["text"] for row in fields))
        require(not any(r["visible"] and normalized(r["name"]) ==
                        "Apply duration to all items" for r in rows),
                "native_gui_multiple_clip_duration_refused")
        self.journal.emit({"step": "edit.duration-precondition",
                           "state": "OBSERVED", "fields": fields})
        self.text("edit.duration15", fields[2], "00:00:00:15")
        self.key("edit.commit-duration-field", 0xFF0D)
        dialog = self.dialog("edit.duration-before-accept", ("Duration",))
        rows = self.descendants(dialog)
        actual = tuple(timecode_frames(labeled_field(rows, (label,))["text"])
                       for label in ("Position:", "Crop start:", "Duration:"))
        require(actual == (60, 60, 15), "native_gui_duration_field_readback_changed")
        okay = self.journal.wait("edit.accept-discover", lambda: self.find(
            ("OK",), ("push button",), scope=dialog["key"]))
        self.action("edit.accept-once", okay)
        self.journal.wait("edit.dialog-closed", lambda: not self.find(
            ("Duration",), ("dialog",)))

    def quit(self, step: str) -> None:
        self.menu(step + ".menu", "File")
        row = self.journal.wait(step + ".discover", lambda: self.find(
            ("Quit",), ("menu item",)))
        # Completion is the separately retained process terminal. A successful
        # quit can remove /proc immediately after do_action returns.
        self.action(step + ".submit", row, exits=True)
