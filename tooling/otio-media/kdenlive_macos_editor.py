"""Import and save the same-job synthetic cut in installed macOS Kdenlive."""
from __future__ import annotations

import argparse
import ctypes as C
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from kdenlive_native_distribution import JOB, ROOT, publish, selection
from kdenlive_native_source import MAX_METADATA_BYTES, installed_editor
from kdenlive_readback import MAX_XML_BYTES, inspect_saved_kdenlive, read_capsule
from source_checkout_observation import git, regular_file, require


UTF8 = 0x08000100
MAX_AX_CHILDREN = 128
CANNOT_COMPLETE = -25204


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
        self.type_id = bind(cf, "CFGetTypeID", C.c_ulong, pointer)
        self.array_type = bind(cf, "CFArrayGetTypeID", C.c_ulong)()
        self.boolean_type = bind(cf, "CFBooleanGetTypeID", C.c_ulong)()
        self.string_type = bind(cf, "CFStringGetTypeID", C.c_ulong)()
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

    def owns(self, element):
        pid = C.c_int()
        return (element and self.type_id(element) == self.element_type
                and self.get_pid(element, C.byref(pid)) == 0 and pid.value == self.pid)

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

    def enabled(self, element):
        value = self.value(element, "AXEnabled")
        return bool(value and self.type_id(value) == self.boolean_type and self.boolean_value(value))

    def action(self, element, allowed):
        output = C.c_void_p()
        actions = self.capture(self.copy_actions(element, C.byref(output)), output)
        observed = {self.string(name) for name in self.array(actions)}
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

    def descend(self, roots, *, limit=160):
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
            if depth < 7:
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
        ax = self.ax
        menu_bar = ax.value(ax.app, "AXMenuBar")
        window = self.top_window()
        return next((element for element in self.descend((menu_bar, window))
                     if self.title(element) == "file"
                     and ax.text(element, "AXRole") in {"AXMenu", "AXMenuBarItem", "AXMenuItem"}
                     and ax.enabled(element)), None)

    def menu_item(self, title, evidence, stage):
        ax = self.ax
        target = label(title)

        def lookup():
            menu = self.file_menu()
            if menu:
                return next((node for node in self.descend((menu,), limit=100)
                             if self.title(node) == target
                             and ax.text(node, "AXRole") == "AXMenuItem"
                             and ax.enabled(node) and ax.action(node, ("AXPress", "AXPick"))), None)
            return None

        self.ax.next_snapshot()
        item = lookup()
        if item:
            return item
        menu = self.await_target(self.file_menu, "File menu")
        evidence.press(stage + "-file-menu", menu)
        return self.await_target(lookup, title)

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

    def press(self, stage, target):
        self.guard()
        ax = self.ax
        allowed = ("AXPress", "AXPick") if ax.text(target, "AXRole").startswith("AXMenu") else ("AXPress",)
        action = ax.action(target, allowed)
        require(ax.owns(target) and ax.enabled(target) and action,
                f"macOS {stage} target is outside the editor, disabled or has no exact action")
        self.write(stage + "-intent", {
            "pid": ax.pid, "action": action, "title": ax.text(target, "AXTitle"),
        })
        status = ax.perform(target, ax.name(action))
        self.write(stage + "-terminal", {"pid": ax.pid, "ax_error": status})
        # A modal callback may leave AX's response uncertain. Only the following
        # dialog/project can acknowledge it. Never send the action again.
        require(status in (0, CANNOT_COMPLETE), f"macOS {stage} AX action failed ({status})")


def import_and_save(ui, evidence, original, initial, check_source):
    item = ui.menu_item("OpenTimelineIO Import", evidence, "import")
    evidence.press("open-import", item)
    field, button = ui.file_panel({"opentimelineio import"}, "Open")
    ui.set_text(field, str(original))
    check_source()
    evidence.press("import-otio", button)
    ui.after_import()

    item = ui.menu_item("Save As", evidence, "save")
    evidence.press("open-save-as", item)
    field, button = ui.file_panel({"save as", "save file", "save", ""}, "Save")
    ui.set_text(field, str(initial))
    require(not initial.exists() and not initial.is_symlink(),
            "macOS initial project path was occupied before Save")
    evidence.press("save-initial", button)


def exact_source(owner, context, distribution):
    receipt = owner / "otio-source-ready.json"
    receipt_identity = regular_file(receipt, MAX_METADATA_BYTES)
    with receipt.open("rb") as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    require(sha256(raw).hexdigest() == receipt_identity["sha256"],
            "macOS source-ready receipt changed during read")
    source = json.loads(raw)
    editor, binary = installed_editor(owner, context, distribution)
    require(source.get("schema") == "motionwright.kdenlive-native-source/1"
            and source.get("context") == context
            and source.get("editor_path") == str(editor)
            and source.get("editor_sha256") == binary["sha256"],
            "macOS editor source-ready receipt differs from this installation")
    capsule = owner / "otio-original"
    for role, filename in (("timeline", "timeline.otio"), ("manifest", "bridge-manifest.json")):
        path = capsule / filename
        require(source.get(role) == {"path": str(path), **regular_file(path, MAX_METADATA_BYTES)},
                f"macOS editor source {role} differs from its original")
    read_capsule(capsule, source["manifest"]["sha256"])
    return editor, binary, source


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


def run(owner, context, distribution, observe_editor):
    editor, binary, source = exact_source(owner, context, distribution)
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
                        evidence.write("accessibility-tree", {"pid": process.pid, "nodes": ui.diagnostic()})
                        return {"status": "EDITOR_OBSERVED", "pid": process.pid, "editor_actions": 0}

                    def check_source():
                        require(exact_source(owner, context, distribution) == (editor, binary, source),
                                "original editor or source changed")

                    import_and_save(ui, evidence, original, initial, check_source)
                    saved = wait_project(initial, guard)
                    evidence.write("project-saved", {"pid": process.pid, "path": str(initial), **saved})
                    check_source()
                    readback = inspect_saved_kdenlive(initial, saved["sha256"], original.parent,
                                                     source["manifest"]["sha256"])
                    evidence.write("initial-readback", readback)
                    clips = readback["observed_bound_editorial_clip_semantics"]
                    require([(clip["original_inpoint_frame"], clip["duration_frames"]) for clip in clips]
                            == [(0, 30), (30, 30), (60, 30)],
                            "saved Kdenlive project does not preserve the three complete input intervals")
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
    parser.add_argument("--observe-editor", action="store_true")
    options = parser.parse_args()
    context, distribution = selection(os.environ, sys.platform, platform.machine())
    require(context["runner_os"] == "macOS", "macOS editor qualification is restricted to macOS")
    require(git(ROOT, "rev-parse", "HEAD").decode("ascii").strip() == context["source_sha"],
            "macOS Kdenlive checkout differs from the workflow")
    runner = Path(os.environ["RUNNER_TEMP"])
    require(runner.is_absolute() and runner.resolve(strict=True) == runner,
            "macOS Kdenlive runner root is not canonical")
    print(json.dumps(run(runner / f"{JOB}-{context['run_id']}-1", context, distribution,
                         options.observe_editor), sort_keys=True))


if __name__ == "__main__":
    main()
