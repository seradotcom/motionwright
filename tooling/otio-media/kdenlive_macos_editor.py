"""Import and save the same-job synthetic cut in installed macOS Kdenlive."""
from __future__ import annotations

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

from kdenlive_native_distribution import JOB, ROOT, publish, selection
from kdenlive_native_source import admitted_source, inspect_initial_save, installed_editor
from kdenlive_macos_kxmlgui import prepare_profile
from kdenlive_readback import MAX_XML_BYTES
from source_checkout_observation import git, regular_file, require


UTF8 = 0x08000100
MAX_AX_CHILDREN = 128
CANNOT_COMPLETE = -25204
TIMECODE = re.compile(r"[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{2}")
DURATION_FIELDS = ("m_pos", "m_cropStart", "m_dur")
CLOCK_ROLES = frozenset({"AXTextField", "AXIncrementor", "AXSpinButton"})


class CFRange(C.Structure):
    _fields_ = [("location", C.c_long), ("length", C.c_long)]


def clock_frames(text):
    require(TIMECODE.fullmatch(text), "macOS editor clock field is not exact")
    hours, minutes, seconds, frames = map(int, text.split(":"))
    require(minutes < 60 and seconds < 60 and frames < 30,
            "macOS editor clock field differs from the 30 fps source clock")
    return 30 * (3600 * hours + 60 * minutes + seconds) + frames


def bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype, function.argtypes = result, arguments
    return function


class Accessibility:
    def __init__(self):
        ax = C.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        pointer, integer, index = C.c_void_p, C.c_int, C.c_long
        out_pointer = C.POINTER(pointer)
        self.release = bind(cf, "CFRelease", None, pointer)
        self.equal = bind(cf, "CFEqual", C.c_ubyte, pointer, pointer)
        self.type_id = bind(cf, "CFGetTypeID", C.c_ulong, pointer)
        self.array_type = bind(cf, "CFArrayGetTypeID", C.c_ulong)()
        self.boolean_type = bind(cf, "CFBooleanGetTypeID", C.c_ulong)()
        self.string_type = bind(cf, "CFStringGetTypeID", C.c_ulong)()
        self.number_type = bind(cf, "CFNumberGetTypeID", C.c_ulong)()
        self.get_number = bind(cf, "CFNumberGetValue", C.c_ubyte, pointer, C.c_long, pointer)
        self.element_type = bind(ax, "AXUIElementGetTypeID", C.c_ulong)()
        self.array_count = bind(cf, "CFArrayGetCount", index, pointer)
        self.array_item = bind(cf, "CFArrayGetValueAtIndex", pointer, pointer, index)
        self.boolean_value = bind(cf, "CFBooleanGetValue", C.c_ubyte, pointer)
        self.make_string = bind(cf, "CFStringCreateWithCString", pointer, pointer, C.c_char_p, C.c_uint32)
        self.string_value = bind(cf, "CFStringGetCString", C.c_ubyte, pointer, pointer, index,
                                 C.c_uint32)
        self.create_app = bind(ax, "AXUIElementCreateApplication", pointer, integer)
        self.create_system = bind(ax, "AXUIElementCreateSystemWide", pointer)
        self.trusted = bind(ax, "AXIsProcessTrusted", C.c_ubyte)
        self.messaging_timeout = bind(ax, "AXUIElementSetMessagingTimeout", integer, pointer, C.c_float)
        self.get_pid = bind(ax, "AXUIElementGetPid", integer, pointer, C.POINTER(integer))
        self.copy_value = bind(ax, "AXUIElementCopyAttributeValue", integer, pointer, pointer,
                               out_pointer)
        self.copy_parameterized_value = bind(ax, "AXUIElementCopyParameterizedAttributeValue", integer,
                                             pointer, pointer, pointer, out_pointer)
        self.create_ax_value = bind(ax, "AXValueCreate", pointer, integer, pointer)
        self.copy_actions = bind(ax, "AXUIElementCopyActionNames", integer, pointer, out_pointer)
        self.set_value = bind(ax, "AXUIElementSetAttributeValue", integer, pointer, pointer, pointer)
        self.perform = bind(ax, "AXUIElementPerformAction", integer, pointer, pointer)
        self.names, self.values = {}, []
        require(self.trusted(), "macOS editor requires an already trusted accessibility session")
        self.system = self.create_system()
        require(self.system and self.messaging_timeout(self.system, 0.5) == 0,
                "macOS accessibility messaging timeout could not be established")
        self.app = None
        self.pid = None

    def attach(self, pid):
        self.app = self.create_app(pid)
        self.pid = pid
        require(self.app is not None and self.owns(self.app), "macOS accessibility PID differs")

    def next_snapshot(self):
        for value in reversed(self.values):
            self.release(value)
        self.values.clear()

    def close(self):
        self.next_snapshot()
        for value in (*self.names.values(), self.app, self.system):
            if value:
                self.release(value)

    def name(self, value):
        if value not in self.names:
            string = self.make_string(None, value.encode("utf-8"), UTF8)
            require(string is not None, "could not create an accessibility attribute name")
            self.names[value] = string
        return self.names[value]

    def element_pid(self, element):
        pid = C.c_int()
        if (element and self.type_id(element) == self.element_type
                and self.get_pid(element, C.byref(pid)) == 0):
            return pid.value
        return None

    def owns(self, element):
        return self.pid is not None and self.element_pid(element) == self.pid


    def capture(self, status, output):
        if output.value:
            self.values.append(output.value)
        return output.value if status == 0 else None

    def value(self, element, name):
        output = C.c_void_p()
        return self.capture(self.copy_value(element, self.name(name), C.byref(output)), output)

    def array(self, value):
        if not value or self.type_id(value) != self.array_type:
            return ()
        count = self.array_count(value)
        if count > MAX_AX_CHILDREN:
            return ()
        return tuple(self.array_item(value, position) for position in range(count))


    def children(self, element, name="AXChildren"):
        return tuple(child for child in self.array(self.value(element, name))
                     if self.type_id(child) == self.element_type)

    def string(self, value):
        if not value or self.type_id(value) != self.string_type:
            return ""
        buffer = C.create_string_buffer(4096)
        return buffer.value.decode("utf-8") if self.string_value(value, buffer, len(buffer), UTF8) else ""

    def text(self, element, name):
        return self.string(self.value(element, name))

    def number(self, original):
        if not original or self.type_id(original) != self.number_type:
            return None
        converted = C.c_longlong()
        return converted.value if self.get_number(original, 4, C.byref(converted)) else None

    def control_text(self, element):
        # Qt Cocoa exposes text separately from QAccessibleValueInterface.
        count = self.number(self.value(element, "AXNumberOfCharacters"))
        if count is None or not 1 <= count <= 16:
            return ""
        span = CFRange(0, count)
        parameter = self.create_ax_value(4, C.byref(span))  # kAXValueCFRangeType
        if not parameter:
            return ""
        try:
            output = C.c_void_p()
            rc = self.copy_parameterized_value(element, self.name("AXStringForRange"),
                                               parameter, C.byref(output))
            text = self.string(self.capture(rc, output))
            return text if len(text) == count else ""
        finally:
            self.release(parameter)

    def duration_value(self, element):
        original = self.value(element, "AXValue")
        frames = self.number(original)
        if frames is not None:
            return frames if 0 <= frames < 10_800_000 else None
        text = self.string(original) or self.control_text(element)
        if TIMECODE.fullmatch(text):
            return clock_frames(text)
        if re.fullmatch(r"[0-9]{1,8}", text) and int(text) < 10_800_000:
            return int(text)
        return None

    def flag(self, element, name):
        value = self.value(element, name)
        return bool(value and self.type_id(value) == self.boolean_type and self.boolean_value(value))

    def enabled(self, element):
        return self.flag(element, "AXEnabled")


    def actions(self, element):
        output = C.c_void_p()
        actions = self.capture(self.copy_actions(element, C.byref(output)), output)
        return sorted({self.string(name) for name in self.array(actions)} - {""})

    def action(self, element, allowed):
        observed = self.actions(element)
        return next((name for name in allowed if name in observed), None)

    def set_text(self, element, value):
        original = self.make_string(None, value.encode("utf-8"), UTF8)
        require(original is not None, "could not represent the exact macOS project path")
        try:
            require(self.owns(element) and self.enabled(element)
                    and self.set_value(element, self.name("AXValue"), original) == 0
                    and self.text(element, "AXValue") == value,
                    "macOS file dialog did not acknowledge the exact selected path")
        finally:
            self.release(original)


def label(text):
    return " ".join(text.replace("&", "").removesuffix("…").removesuffix("...").split()).casefold()


class EditorUI:
    def __init__(self, ax, guard):
        self.ax, self.guard = ax, guard

    def descend(self, roots, *, limit=160, depth_limit=7):
        ax = self.ax
        queue = [(root, 0) for root in roots if root]
        seen, deadline = set(), time.monotonic() + 4
        for element, depth in queue:
            if len(seen) >= limit or time.monotonic() >= deadline:
                return
            if element in seen or not ax.owns(element):
                continue
            seen.add(element)
            yield element
            if depth < depth_limit:
                queue.extend((child, depth + 1) for child in ax.children(element))

    def await_target(self, lookup, description, *, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.guard()
            self.ax.next_snapshot()
            value = lookup()
            if value:
                return value
            time.sleep(0.2)
        raise TimeoutError(f"macOS accessibility target unavailable: {description}")

    def top_window(self):
        return self.ax.value(self.ax.app, "AXMainWindow")


    def title(self, element):
        return label(self.ax.text(element, "AXTitle"))

    def file_menu(self):
        return self.top_menu("File")

    def top_menu(self, section):
        ax = self.ax
        native_bar = ax.value(ax.app, "AXMenuBar")
        widget_bar = next((node for node in self.descend((self.top_window(),), limit=40)
                           if ax.text(node, "AXRole") == "AXMenuBar"
                           and ax.text(node, "AXIdentifier").endswith(".QMenuBar")), None)
        for bar in (widget_bar, native_bar):
            if bar and ax.owns(bar):
                files = [node for node in ax.children(bar) if ax.owns(node) and self.title(node) == label(section)
                         and ax.text(node, "AXRole") in {"AXMenu", "AXMenuBarItem", "AXMenuItem"}
                         and ax.enabled(node)]
                if len(files) == 1:
                    return files[0]
        return None

    def menu_item(self, title, evidence, stage, *, section="File", submenu=None):
        ax = self.ax
        unavailable = {}

        def menu_node(parent, expected, wanted):
            if parent:
                for node in self.descend(ax.children(parent), limit=100):
                    if self.title(node) != label(expected) or ax.text(node, "AXRole") != "AXMenuItem":
                        continue
                    enabled, actions = ax.enabled(node), ax.actions(node)
                    if enabled and any(action in actions for action in wanted):
                        return node
                    unavailable[expected] = {
                        "identifier": ax.text(node, "AXIdentifier"),
                        "enabled": enabled, "actions": actions,
                    }
            return None

        def nested():
            return menu_node(self.top_menu(section), submenu, ("AXShowMenu", "AXPress", "AXPick"))

        def lookup():
            parent = nested() if submenu else self.top_menu(section)
            return menu_node(parent, title, ("AXPress", "AXPick"))

        self.ax.next_snapshot()
        item = lookup()
        if item:
            return item
        if submenu and nested():
            menu = self.await_target(nested, submenu)
            evidence.press(stage + "-submenu", menu, opens=submenu)
        else:
            menu = self.await_target(lambda: self.top_menu(section), section + " menu")
            section_stage = stage + "-" + section.casefold() + "-menu"
            evidence.write(section_stage + "-observed", {
                "pid": ax.pid, "role": ax.text(menu, "AXRole"), "title": ax.text(menu, "AXTitle"),
                "identifier": ax.text(menu, "AXIdentifier"), "actions": ax.actions(menu),
            })
            evidence.press(section_stage, menu, opens=section)
            if submenu:
                self.ax.next_snapshot()
                item = lookup()
                if item:
                    return item
                submenu_node = self.await_target(nested, submenu)
                evidence.press(stage + "-submenu", submenu_node, opens=submenu)
        try:
            return self.await_target(lookup, title)
        except TimeoutError:
            evidence.write(stage + "-unavailable", {
                "pid": ax.pid, "section": section, "submenu": submenu,
                "title": title, "observed_unavailable_items": unavailable,
            })
            raise

    def panels(self):
        ax = self.ax
        windows = ax.children(ax.app, "AXWindows")
        return (*windows, *(sheet for window in windows
                           for sheet in ax.children(window, "AXSheets")))

    def file_panel(self, names, confirm):
        ax = self.ax

        def lookup():
            for panel in self.panels():
                if self.title(panel) not in names:
                    continue
                fields, buttons = [], []
                for element in self.descend((panel,)):
                    role = ax.text(element, "AXRole")
                    if role == "AXTextField":
                        identifier = ax.text(element, "AXIdentifier")
                        if (identifier.rsplit(".", 1)[-1] == "fileNameEdit"
                                or self.title(element).rstrip(":") == "file name"):
                            fields.append(element)
                    elif role == "AXButton" and self.title(element) == label(confirm):
                        buttons.append(element)
                if len(fields) == len(buttons) == 1:
                    return fields[0], buttons[0]
            return None

        return self.await_target(lookup, f"{confirm} file dialog and its file name field")


    def video_track_toolbar_button(self):
        ax = self.ax
        matches = [node for node in self.descend((self.top_window(),), limit=200, depth_limit=10)
                   if ax.text(node, "AXRole") == "AXButton"
                   and self.title(node) == "select video track 1"
                   and ".timelineToolBar." in ax.text(node, "AXIdentifier")
                   and ax.enabled(node) and ax.action(node, ("AXPress",))]
        return matches[0] if len(matches) == 1 else None


    def set_text(self, element, value):
        self.guard()
        self.ax.set_text(element, value)

    def after_import(self):
        def lookup():
            panels = self.panels()
            return self.file_menu() and not any(self.title(panel) in {
                "opentimelineio import", "importing opentimelineio",
                "error importing opentimelineio file",
            } for panel in panels)

        self.await_target(lookup, "editor after importing the original OTIO")


    def project_monitor(self):
        ax = self.ax
        groups = []
        for node in self.descend((self.top_window(),), limit=160, depth_limit=12):
            if (self.title(node) == "project monitor" and ax.text(node, "AXRole") == "AXTabGroup"
                    and ax.enabled(node) and not any(ax.equal(node, known) for known in groups)):
                groups.append(node)
        return groups[0] if len(groups) == 1 else None

    def project_monitor_tab(self):
        ax = self.ax
        group = self.project_monitor()
        if not group:
            return None
        tabs = []
        for node in ax.children(group):
            if (ax.owns(node) and self.title(node) == "project monitor"
                    and ax.text(node, "AXRole") == "AXRadioButton"
                    and ax.enabled(node) and ax.action(node, ("AXPress",))):
                parent = ax.value(node, "AXParent")
                if ax.owns(parent) and ax.equal(group, parent):
                    tabs.append(node)
        return tabs[0] if len(tabs) == 1 else None

    def seek_and_select_third_clip(self, evidence, stage=None):
        def step(name):
            return f"{stage}-{name}" if stage else name

        tab = self.await_target(self.project_monitor_tab, "owned Project Monitor tab")
        evidence.press(step("activate-project-monitor"), tab)
        self.await_target(self.project_monitor, "selected Project Monitor tab group")
        # The pinned Kdenlive action activates the project monitor itself before
        # opening the inspector. The dock tab alone cannot select the monitor.
        activate = self.menu_item("Sequence effects", evidence, step("focus-project-monitor"),
                                  section="Sequence", submenu="Tracks")
        evidence.press(step("focus-project-monitor"), activate)
        for name, phase, submenu in (
            ("Go to Project End", "project-end", "Go To"),
            ("Rewind 1 Frame", "rewind-one-frame", "Seek"),
        ):
            item = self.menu_item(name, evidence, step(phase), section="Monitor", submenu=submenu)
            evidence.press(step(phase), item)
        v1 = self.await_target(self.video_track_toolbar_button, "exact V1 button in the isolated toolbar")
        require(evidence.press(step("select-video-track-one"), v1) == 0,
                "native V1 toolbar did not acknowledge selection")
        item = self.menu_item("Select Clip", evidence, step("select-third"),
                              section="Sequence", submenu="Selection")
        evidence.press(step("select-third"), item)

    def duration_panels(self):
        return [node for node in self.panels() if self.title(node) in {"duration", "duration - kdenlive"}]

    def resize_selected_end(self, stage, forward_frames, evidence):
        require(forward_frames in (0, 2), "unexpected navigation for the original one-frame change")
        # Go to Clip End seeks to the last inclusive frame of the selected clip.
        # Resize Item End uses the current frame as its exclusive end.
        item = self.menu_item("Go to Clip End", evidence, stage + "-clip-end",
                              section="Monitor", submenu="Go To")
        require(evidence.press(stage + "-clip-end", item) == 0,
                "the selected clip's end was not acknowledged")
        for index in range(1, forward_frames + 1):
            phase = f"{stage}-forward-{index}"
            item = self.menu_item("Forward 1 Frame", evidence, phase, section="Monitor", submenu="Seek")
            require(evidence.press(phase, item) == 0, "the forward frame navigation was not acknowledged")
        item = self.menu_item("Resize Item End", evidence, stage + "-resize-end",
                              section="Sequence", submenu="Current Item")
        require(evidence.press(stage + "-resize-end", item) == 0,
                "resize of the selected clip was not acknowledged")

    def duration_controls(self):
        ax = self.ax
        panels = self.duration_panels()
        if len(panels) != 1:
            return None
        candidates = {name: [] for name in DURATION_FIELDS}
        cancel = []
        for node in self.descend(panels, limit=100, depth_limit=12):
            role, title = ax.text(node, "AXRole"), self.title(node)
            require("resizing in ripple mode" not in title
                    and title != "apply duration to all items",
                    "macOS editor duration must apply to exactly one clip without ripple")
            if role in CLOCK_ROLES:
                components = ax.text(node, "AXIdentifier").split(".")
                value = ax.duration_value(node)
                for name in DURATION_FIELDS:
                    if name in components and value is not None:
                        candidates[name].append((role, value))
            elif role == "AXButton" and title == "cancel" and ax.enabled(node):
                cancel.append(node)
        fields = [([value for role, value in records if role == "AXTextField"]
                   or [value for _, value in records]) for records in candidates.values()]
        if len(cancel) == 1 and all(len(values) == 1 for values in fields):
            return cancel[0], tuple(values[0] for values in fields)
        return None

    def duration_dialog(self, stage, evidence):
        item = self.menu_item("Edit Duration", evidence, stage,
                              section="Sequence", submenu="Current Item")
        evidence.press(stage + "-open", item)
        return self.await_target(self.duration_controls, "single-clip Duration fields")

    def duration_closed(self):
        self.await_target(lambda: self.top_window() and not self.duration_panels(),
                          "closure of the owned Duration dialog")

    def edit_diagnostic(self):
        ax = self.ax
        ax.next_snapshot()
        rows = []
        for element in self.descend((*self.panels(), self.top_window()), limit=320, depth_limit=17):
            role = ax.text(element, "AXRole")
            if role not in CLOCK_ROLES:
                continue
            identifier = ax.text(element, "AXIdentifier")
            parts = identifier.split(".")
            if "projectmonitor" in parts or any(name in parts for name in DURATION_FIELDS):
                value = ax.text(element, "AXValue") or ax.control_text(element)
                rows.append({"role": role, "identifier": identifier,
                             "title": ax.text(element, "AXTitle"),
                             "clock": value if TIMECODE.fullmatch(value) else None,
                             "frames": ax.duration_value(element), "actions": ax.actions(element)})
            if len(rows) >= 20:
                break
        return rows

    def project_monitor_diagnostic(self):
        ax = self.ax

        def row(node):
            if not ax.owns(node):
                return None
            role = ax.text(node, "AXRole")
            raw = ax.value(node, "AXValue")
            value = ax.string(raw)
            return {
                "role": role, "id": ax.text(node, "AXIdentifier"),
                "title": ax.text(node, "AXTitle"), "clock": value if TIMECODE.fullmatch(value) else None,
                "value_type": ax.type_id(raw) if raw else None, "focused": ax.flag(node, "AXFocused"),
                "enabled": ax.enabled(node), "actions": ax.actions(node),
            }

        tab = self.await_target(self.project_monitor, "empty project's Project Monitor tab group")
        parent = ax.value(tab, "AXParent")
        require(ax.owns(parent), "observed Project Monitor has no parent in the editor PID")
        focused = ax.value(ax.app, "AXFocusedUIElement")
        observed = []
        for element in self.descend((parent,), limit=90, depth_limit=9):
            data = row(element)
            if data:
                origin = ax.value(element, "AXParent")
                data["parent_id"] = ax.text(origin, "AXIdentifier") if ax.owns(origin) else None
                observed.append(data)
        return {"tab_group": row(tab), "parent": row(parent), "focused_element": row(focused),
                "rows": observed, "editor_actions": 0}

    def diagnostic(self):
        self.ax.next_snapshot()
        roots = (self.ax.value(self.ax.app, "AXMenuBar"), *self.panels(), self.top_window())
        return [{"role": self.ax.text(element, "AXRole"),
                 "title": self.ax.text(element, "AXTitle"),
                 "identifier": self.ax.text(element, "AXIdentifier")}
                for element in self.descend(roots, limit=80)]


class Evidence:
    def __init__(self, root, ax, guard):
        self.root, self.ax, self.guard = root, ax, guard

    def write(self, name, value):
        publish(self.root / (name + ".json"), value)

    def press(self, stage, target, *, opens=None):
        self.guard()
        ax = self.ax
        role, title = ax.text(target, "AXRole"), ax.text(target, "AXTitle")
        allowed = ("AXPress",)
        if role.startswith("AXMenu"):
            allowed = ("AXPress", "AXPick")
            if opens is None and stage in {"import-file-menu", "save-file-menu"}:
                opens = "File"
            if opens is not None and label(title) == label(opens):
                allowed = ("AXShowMenu", *allowed)
        action = ax.action(target, allowed)
        require(ax.owns(target) and ax.enabled(target) and action,
                f"macOS {stage} target is outside the editor, disabled or has no exact action")
        self.write(stage + "-intent", {
            "pid": ax.pid, "action": action, "title": title, "role": role,
        })
        status = ax.perform(target, ax.name(action))
        self.write(stage + "-terminal", {"pid": ax.pid, "ax_error": status})
        # A modal callback may leave AX's response uncertain. Only the following
        # dialog/project can acknowledge it. Never send the action again.
        require(status in (0, CANNOT_COMPLETE), f"macOS {stage} AX action failed ({status})")
        return status


def refresh_third_playlist(ui, evidence):
    tool = ui.menu_item("Selection Tool", evidence, "selection-tool", section="Tool")
    require(evidence.press("selection-tool", tool) == 0, "the non-ripple selection tool was not acknowledged")
    for stage, expected in (("shorten", 30), ("restore", 29), ("verify-restoration", 30)):
        ui.seek_and_select_third_clip(evidence, stage)
        cancel, values = ui.duration_dialog(stage, evidence)
        evidence.write(stage + "-model", {"position_crop_duration_frames": values})
        require(values == (60, 60, expected), f"macOS {stage} model does not match the original third clip")
        evidence.press(stage + "-close", cancel)
        ui.duration_closed()
        if stage != "verify-restoration":
            # The original end is frame 89. The shortened clip ends at 88;
            # seeking two frames past 88 restores its exclusive end at 90.
            ui.resize_selected_end(stage, 0 if stage == "shorten" else 2, evidence)


def import_and_save(ui, evidence, original, initial, check_source):
    ui.await_target(ui.video_track_toolbar_button, "verified native V1 button before importing")
    item = ui.menu_item("OpenTimelineIO Import", evidence, "import")
    evidence.press("open-import", item)
    field, button = ui.file_panel({"opentimelineio import"}, "Open")
    ui.set_text(field, str(original))
    check_source()
    evidence.press("import-otio", button)
    ui.after_import()
    refresh_third_playlist(ui, evidence)

    item = ui.menu_item("Save As", evidence, "save")
    evidence.press("open-save-as", item)
    field, button = ui.file_panel({"save as", "save file", "save", ""}, "Save")
    ui.set_text(field, str(initial))
    require(not initial.exists() and not initial.is_symlink(),
            "macOS initial project path was occupied before Save")
    check_source()
    evidence.press("save-initial", button)


def editor_pid_executable(pid):
    library = C.CDLL("/usr/lib/libproc.dylib")
    proc_pidpath = bind(library, "proc_pidpath", C.c_int, C.c_int, C.c_void_p, C.c_uint32)
    buffer = C.create_string_buffer(4096)
    require(proc_pidpath(pid, buffer, len(buffer)) > 0, "owned Kdenlive PID has no executable path")
    return Path(os.fsdecode(buffer.value))


def wait_project(initial, guard):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        guard()
        if initial.exists():
            record = regular_file(initial, MAX_XML_BYTES)
            time.sleep(0.2)
            if record["bytes"] > 0 and regular_file(initial, MAX_XML_BYTES) == record:
                return record
        time.sleep(0.2)
    raise TimeoutError("macOS Kdenlive did not produce the initial native project")


def run(owner, context, distribution, observe_editor, *, observe_toolbar_override=False):
    if observe_editor:
        editor, binary = installed_editor(owner, context, distribution)
        source = None
    else:
        editor, binary, source = admitted_source(owner, context, distribution)
    ax = Accessibility()
    try:
        output = owner / ("macos-initial-observe" if observe_editor else "macos-initial")
        output.mkdir(mode=0o700)
        initial = output / "initial.kdenlive"
        original = owner / "otio-original" / "timeline.otio"
        command = [str(editor), "--no-welcome", "--config", str(output / "kdenliverc"),
                   "-platformtheme", "generic"]
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("DYLD_", "QT_", "QML", "MLT_"))}
        environment.update({"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "QT_ACCESSIBILITY": "1"})
        override = None
        if not observe_editor or observe_toolbar_override:
            environment, override = prepare_profile(output, environment)
        publish(output / "launch-intent.json", {
            "context": context, "argv": command, "original_executable": binary,
            "source": source, "observe_editor": observe_editor,
        })
        with (output / "editor.stdout").open("xb") as stdout, (output / "editor.stderr").open("xb") as stderr:
            try:
                process = subprocess.Popen(command, cwd=output, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr)
            except OSError as error:
                publish(output / "editor-terminal.json", {"pid": None, "launch_error": str(error)})
                raise
            saved = None
            try:
                def guard():
                    require(process.poll() is None and editor_pid_executable(process.pid) == editor,
                            "owned macOS Kdenlive process ended or executable path changed")

                guard()
                ax.attach(process.pid)
                ui = EditorUI(ax, guard)
                evidence = Evidence(output, ax, guard)
                evidence.write("editor-process", {"pid": process.pid, "executable": str(editor), **binary})
                try:
                    ui.await_target(ui.file_menu, "owned Kdenlive File menu", timeout=45)
                    if observe_editor:
                        if observe_toolbar_override:
                            button = ui.await_target(ui.video_track_toolbar_button,
                                                     "V1 button from the isolated KXMLGUI override")
                            evidence.write("toolbar-v1-button", {
                                "pid": process.pid, "title": ax.text(button, "AXTitle"),
                                "identifier": ax.text(button, "AXIdentifier"),
                                "actions": ax.actions(button),
                            })
                            require(evidence.press("empty-editor-v1-toolbar", button) == 0,
                                    "isolated V1 toolbar did not return an acknowledged AX action")
                            require(regular_file(Path(override["path"]), 64 * 1024) == override["override"],
                                    "isolated KXMLGUI override changed after native use")
                            return {"status": "KXMLGUI_TOOLBAR_VERIFIED", "pid": process.pid,
                                    "imports": 0, "saves": 0, "timeline_actions": 1}
                        evidence.write("accessibility-tree", {"pid": process.pid, "nodes": ui.diagnostic()})
                        evidence.write("project-monitor-ax", ui.project_monitor_diagnostic())
                        return {"status": "EDITOR_OBSERVED", "pid": process.pid, "editor_actions": 0}

                    def check_source():
                        require(admitted_source(owner, context, distribution) == (editor, binary, source)
                                and regular_file(Path(override["path"]), 64 * 1024) == override["override"],
                                "original editor, profile or source changed")

                    import_and_save(ui, evidence, original, initial, check_source)
                    saved = wait_project(initial, guard)
                    evidence.write("project-saved", {"pid": process.pid, "path": str(initial), **saved})
                    check_source()
                    readback = inspect_initial_save(initial, saved["sha256"],
                                                    source["manifest"]["sha256"], original.parent)
                    evidence.write("initial-readback", readback)
                    report = {
                        "schema": "motionwright.kdenlive-macos-initial-project/1", "context": context,
                        "status": "INITIAL_PROJECT_VERIFIED", "pid": process.pid,
                        "editor": {"path": str(editor), **binary},
                        "source": source["timeline"], "project": {"path": str(initial), **saved},
                        "import_confirmations": 1, "save_confirmations": 1, "render_dispatches": 0,
                    }
                    status = report
                except Exception as error:
                    diagnosis = {"pid": process.pid, "type": type(error).__name__, "message": str(error)}
                    if process.poll() is None:
                        try:
                            diagnosis["nodes"] = ui.diagnostic()
                            diagnosis["edit_controls"] = ui.edit_diagnostic()
                        except Exception as diagnostic_error:
                            diagnosis["diagnostic_error"] = str(diagnostic_error)
                    evidence.write("editor-diagnostic", diagnosis)
                    raise
            finally:
                cleanup = "already_exited"
                try:
                    if process.poll() is None:
                        cleanup = "terminated_owned_process"
                        process.terminate()
                        try:
                            process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            cleanup = "killed_owned_process_after_timeout"
                            process.kill()
                            process.wait(timeout=5)
                finally:
                    publish(output / "editor-terminal.json", {
                        "pid": process.pid, "exit_code": process.poll(), "cleanup": cleanup,
                    })
                if saved is not None:
                    require(regular_file(initial, MAX_XML_BYTES) == saved,
                            "original macOS Kdenlive project changed after the editor closed")
            evidence.write("initial-project", status)
            return status
    finally:
        ax.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    observation = parser.add_mutually_exclusive_group()
    observation.add_argument("--observe-editor", action="store_true")
    observation.add_argument("--observe-editor-kxmlgui-v1", action="store_true")
    options = parser.parse_args()
    context, distribution = selection(os.environ, sys.platform, platform.machine())
    require(context["runner_os"] == "macOS", "macOS editor qualification is restricted to macOS")
    require(git(ROOT, "rev-parse", "HEAD").decode("ascii").strip() == context["source_sha"],
            "macOS Kdenlive checkout differs from the workflow")
    runner = Path(os.environ["RUNNER_TEMP"])
    require(runner.is_absolute() and runner.resolve(strict=True) == runner,
            "macOS Kdenlive runner root is not canonical")
    print(json.dumps(run(runner / f"{JOB}-{context['run_id']}-1", context, distribution,
                         options.observe_editor or options.observe_editor_kxmlgui_v1,
                         observe_toolbar_override=options.observe_editor_kxmlgui_v1), sort_keys=True))


if __name__ == "__main__":
    main()
