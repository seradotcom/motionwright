#!/usr/bin/env python3
"""One accessible Linux first-use attempt, followed by the unchanged cold oracle.

Run with Ubuntu's /usr/bin/python3 inside the disposable Xvfb/DBus CI session.
Offline tests are selection/latch tests, never GTK/WebKit or install acceptance.
No JavaScript invocation, XTest fallback, SQLite writes, or automatic UI retry.
Use --storage-source-repo to bind current storage to the exact source checkout;
without explicit admission, the shared observers retain their historical contract.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import partial
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from linux_cold_start import (
    ColdStartError, EXPECTED_INITIAL_TITLE, SHA40, _bootstrap_committed,
    _empty_library_ready, attest_persistence, read_snapshot,
)
from linux_gui_probe import run_probe
import storage_schema_contract as storage

MAX_NODES = 2048
MAX_DEPTH = 32
JOURNAL_NAME = "first-use-atspi"
VISIBLE = frozenset(("visible", "showing"))
USABLE = VISIBLE | {"enabled", "sensitive"}


class ProbeError(RuntimeError):
    """A closed failure; the durable intent is never removed."""


class NotReady(ProbeError):
    """Read-only preconditions may be observed again in the same worker."""


@dataclass(frozen=True)
class View:
    node: object
    role: str
    name: str
    states: frozenset
    interfaces: frozenset


def unique(items, label):
    if not items:
        raise NotReady("MISSING_" + label)
    if len(items) != 1:
        raise ProbeError("AMBIGUOUS_" + label)
    return items[0]


def walk(bridge, root):
    pending, result = [(root, 0)], []
    while pending:
        node, depth = pending.pop()
        if depth > MAX_DEPTH or len(result) >= MAX_NODES:
            raise ProbeError("ACCESSIBLE_TREE_LIMIT")
        view = bridge.view(node)
        result.append(view)
        if "defunct" in view.states:
            continue
        children = bridge.children(node)
        if len(result) + len(pending) + len(children) > MAX_NODES:
            raise ProbeError("ACCESSIBLE_TREE_LIMIT")
        pending.extend((child, depth + 1) for child in reversed(children))
    return result


def select_controls(bridge, pid):
    apps = bridge.applications()
    if len(apps) > 128:
        raise ProbeError("APPLICATION_LIMIT")
    app = unique([a for a in apps if bridge.pid(a) == pid], "APPLICATION")
    dialog = unique([
        v for v in walk(bridge, app)
        if v.role == "dialog" and v.name == "Project library"
        and VISIBLE <= v.states and "defunct" not in v.states
    ], "DIALOG")
    children = walk(bridge, dialog.node)
    entry = unique([
        v for v in children
        if v.role in ("entry", "text") and v.name == "Project title"
        and VISIBLE <= v.states and "defunct" not in v.states
    ], "TITLE")
    button = unique([
        v for v in children
        if v.role == "button" and v.name == "Create project"
        and VISIBLE <= v.states and "defunct" not in v.states
    ], "CREATE")
    # A WebKit bridge that cannot establish this identity is a blocked probe,
    # not permission to match another application by its name.
    for view in (dialog, entry, button):
        if bridge.owner_pid(view.node) != pid:
            raise ProbeError("CONTROL_APPLICATION_PID_MISMATCH")
    return app, dialog, entry, button


class Attempt:
    """Pure state machine. Backend mutations occur only after a durable latch."""

    def __init__(self, bridge, identity, journal, binding, deadline=None):
        self.bridge, self.identity, self.journal = bridge, identity, journal
        self.binding = binding
        self.deadline = deadline
        self.phase = "WAITING"
        self.target = None
        self.busy = False
        self.last_condition = "NOT_OBSERVED"
        self.edit_intended = False
        self.create_intended = False
        self.finished = False
        self.journal.put("worker-start", {"binding": binding})

    def check_identity(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ProbeError("DEADLINE")
        if self.identity() != self.binding:
            raise ProbeError("WINDOW_PROCESS_IDENTITY_CHANGED")

    def step(self):
        if self.finished:
            return True
        if self.busy:
            return False
        self.busy = True
        try:
            if self.phase not in ("WAITING", "EDIT_ACKNOWLEDGED"):
                raise ProbeError("MUTATION_ALREADY_ATTEMPTED")
            self.check_identity()
            controls = select_controls(self.bridge, self.binding["pid"])
            app, dialog, entry, button = controls
            if self.target is not None and any(
                old != new for old, new in zip(self.target,
                    (app, dialog.node, entry.node, button.node))
            ):
                raise ProbeError("CONTROLS_REPLACED_AFTER_EDIT")
            if not (USABLE | {"editable"}) <= entry.states:
                raise NotReady("TITLE_NOT_EDITABLE")
            if not {"EditableText", "Text"} <= entry.interfaces:
                raise ProbeError("TITLE_INTERFACES_UNAVAILABLE")
            if "Action" not in button.interfaces:
                raise ProbeError("CREATE_ACTION_INTERFACE_UNAVAILABLE")
            if self.phase == "WAITING":
                if self.bridge.read_text(entry.node) != "":
                    raise ProbeError("INITIAL_TITLE_NOT_EMPTY")
                self.target = (app, dialog.node, entry.node, button.node)
                self.check_identity()
                if self.bridge.pid(app) != self.binding["pid"] or (
                    self.bridge.owner_pid(entry.node) != self.binding["pid"]
                ):
                    raise ProbeError("TITLE_OWNER_CHANGED")
                self.phase = "EDIT_JOURNAL_PENDING"
                self.journal.put("edit-intent", {
                    "binding": self.binding,
                    "title_sha256": hashlib.sha256(
                        EXPECTED_INITIAL_TITLE.encode()).hexdigest(),
                })
                self.edit_intended = True
                self.phase = "EDIT_UNKNOWN"
                returned = self.bridge.set_text(entry.node, EXPECTED_INITIAL_TITLE)
                self.journal.put("edit-return", {"returned": returned is True})
                if returned is not True:
                    raise ProbeError("EDIT_RETURNED_FALSE_UNCONFIRMED")
                self.phase = "EDIT_ACKNOWLEDGED"
                # Fresh state is required after the edit, never the pre-edit
                # disabled button/cache snapshot.
                return False
            if self.phase != "EDIT_ACKNOWLEDGED":
                raise ProbeError("MUTATION_ALREADY_ATTEMPTED")
            if self.bridge.read_text(entry.node) != EXPECTED_INITIAL_TITLE:
                raise NotReady("TITLE_NOT_VERIFIED")
            if not USABLE <= button.states:
                raise NotReady("CREATE_NOT_ENABLED")
            names = self.bridge.actions(button.node)
            actions = [i for i, name in enumerate(names) if name in ("click", "press")]
            if len(actions) != 1:
                raise ProbeError("CREATE_ACTION_NOT_UNIQUE")
            self.check_identity()
            if self.bridge.pid(app) != self.binding["pid"]:
                raise ProbeError("APPLICATION_PID_CHANGED")
            if self.bridge.owner_pid(entry.node) != self.binding["pid"]:
                raise ProbeError("TITLE_OWNER_CHANGED")
            if self.bridge.owner_pid(button.node) != self.binding["pid"]:
                raise ProbeError("CREATE_OWNER_CHANGED")
            # All later failures, including a false return, are unconfirmed.
            # No path from this phase can send another button action.
            self.phase = "CREATE_JOURNAL_PENDING"
            self.journal.put("create-intent", {
                "binding": self.binding, "action": names[actions[0]],
                "title_verified": True, "button_enabled": True,
            })
            self.create_intended = True
            self.phase = "CREATE_UNKNOWN"
            returned = self.bridge.invoke(button.node, actions[0])
            self.journal.put("create-return", {"returned": returned is True})
            if returned is not True:
                raise ProbeError("CREATE_RETURNED_FALSE_UNCONFIRMED")
            self.phase = "ACTION_ACCEPTED"
            self.finished = True
            return True
        finally:
            self.busy = False

    def report(self, reason):
        return {
            "schema": "motionwright.ci.accessible-action/1",
            "status": "ACTION_ACCEPTED" if self.finished else (
                "UNKNOWN" if self.edit_intended or self.create_intended
                else "NOT_SENT"),
            "phase": self.phase, "reason": reason,
            "last_condition": self.last_condition,
            "edit_intended": self.edit_intended,
            "create_intended": self.create_intended,
            "binding": self.binding,
            "project_commit": "NOT_VERIFIED_BY_ACTION",
        }


class DiskJournal:
    """Linux dirfd custody; entries are create-once and fsynced before effects."""

    def __init__(self, directory):
        self.fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def put(self, name, value):
        if not re.fullmatch(r"[a-z-]+", name):
            raise ProbeError("INVALID_JOURNAL_ENTRY")
        raw = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
        if len(raw) > 16384:
            raise ProbeError("JOURNAL_ENTRY_LIMIT")
        fd = os.open(name + ".json", os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.fsync(self.fd)

    def read(self, name):
        if not re.fullmatch(r"[a-z-]+", name):
            raise ProbeError("INVALID_JOURNAL_ENTRY")
        fd = os.open(name + ".json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, "rb") as inp:
            raw = inp.read(16385)
        if len(raw) > 16384:
            raise ProbeError("JOURNAL_ENTRY_LIMIT")
        return json.loads(raw)

    def close(self):
        os.close(self.fd)


def x11_identity():
    def output(argv):
        result = subprocess.run(
            argv, text=True, capture_output=True, timeout=3, check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        if result.returncode or len(result.stdout) > 262144:
            raise NotReady("X11_QUERY_UNAVAILABLE")
        return result.stdout

    windows = re.findall(r'^\s*(0x[0-9a-fA-F]+)\s+"Motionwright":',
                         output(["xwininfo", "-root", "-tree"]), re.MULTILINE)
    window = unique(windows, "X11_WINDOW")
    prop = output(["xprop", "-id", window, "_NET_WM_PID"])
    match = re.fullmatch(r"_NET_WM_PID\(CARDINAL\) = ([1-9][0-9]*)\s*", prop)
    if not match:
        raise NotReady("X11_PID_UNAVAILABLE")
    pid = int(match.group(1))
    proc = Path("/proc") / str(pid)
    if proc.stat().st_uid != os.getuid():
        raise ProbeError("PROCESS_OWNER_MISMATCH")
    raw = (proc / "stat").read_text(encoding="ascii")
    if len(raw) > 8192:
        raise ProbeError("PROCESS_STAT_LIMIT")
    fields = raw[raw.rfind(")") + 2:].split()
    if len(fields) < 20 or not fields[19].isdigit():
        raise ProbeError("PROCESS_START_TIME_UNAVAILABLE")
    return {"window": window.lower(), "pid": pid, "start_time": fields[19]}


class AtspiBridge:
    def __init__(self, api, *, deadline=None):
        self.api = api
        self.deadline = deadline
        self.desktop = self.call(api.get_desktop, 0)
        if self.desktop is None:
            raise ProbeError("ATSPI_DESKTOP_UNAVAILABLE")
        self.call(self.desktop.set_cache_mask, api.Cache.NONE)
        self.roles = {
            api.Role.DIALOG: "dialog", api.Role.ENTRY: "entry",
            api.Role.TEXT: "text", api.Role.PUSH_BUTTON: "button",
        }
        self.states = {
            key.lower(): getattr(api.StateType, key)
            for key in ("VISIBLE", "SHOWING", "ENABLED", "SENSITIVE",
                        "EDITABLE", "DEFUNCT")
        }

    def call(self, function, *arguments):
        # A D-Bus call already in flight may still time out in the parent. Never
        # start another read or effect after the one inherited budget expires.
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ProbeError("DEADLINE")
        return function(*arguments)

    def children(self, node):
        count = self.call(node.get_child_count)
        if count < 0 or count > MAX_NODES:
            raise ProbeError("ACCESSIBLE_CHILD_LIMIT")
        result = [self.call(node.get_child_at_index, i) for i in range(count)]
        if any(child is None for child in result):
            raise NotReady("ACCESSIBLE_TREE_INCOMPLETE")
        return result

    def applications(self):
        self.call(self.desktop.clear_cache)
        return self.children(self.desktop)

    def pid(self, node):
        pid = self.call(node.get_process_id)
        if not isinstance(pid, int) or pid <= 0:
            raise ProbeError("ATSPI_PROCESS_ID_UNAVAILABLE")
        return pid

    def owner_pid(self, node):
        app = self.call(node.get_application)
        if app is None:
            raise ProbeError("ATSPI_APPLICATION_UNAVAILABLE")
        return self.pid(app)

    def view(self, node):
        state = self.call(node.get_state_set)
        return View(node, self.roles.get(self.call(node.get_role), "other"),
                    self.call(node.get_name),
                    frozenset(k for k, v in self.states.items() if state.contains(v)),
                    frozenset(self.call(node.get_interfaces)))

    def read_text(self, node):
        count = self.call(self.api.Text.get_character_count, node)
        if not 0 <= count <= 256:
            raise ProbeError("TITLE_LENGTH_LIMIT")
        return self.call(self.api.Text.get_text, node, 0, count)

    def set_text(self, node, value):
        return self.call(self.api.EditableText.set_text_contents, node, value)

    def actions(self, node):
        count = self.call(self.api.Action.get_n_actions, node)
        if not 0 <= count <= 16:
            raise ProbeError("ACTION_LIMIT")
        return [self.call(self.api.Action.get_action_name, node, i) for i in range(count)]

    def invoke(self, node, index):
        return self.call(self.api.Action.do_action, node, index)


def worker(scratch, seconds, *, parent_deadline=None):
    if not 1 <= seconds <= 44:
        raise ProbeError("WORKER_DEADLINE_OUT_OF_RANGE")
    deadline = time.monotonic() + seconds
    if parent_deadline is not None:
        if not math.isfinite(parent_deadline) or parent_deadline <= 0:
            raise ProbeError("WORKER_PARENT_DEADLINE_INVALID")
        deadline = min(deadline, parent_deadline)

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise ProbeError("DEADLINE")
        return budget
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise ProbeError("DISPOSABLE_LINUX_CI_REQUIRED")
    journal = DiskJournal(scratch / JOURNAL_NAME)
    attempt = None
    listener = None
    events = ("object:children-changed", "object:state-changed",
              "object:property-change", "object:text-changed", "window")
    result = None
    try:
        # Setup, listener registration and traversal consume the same budget
        # as the parent. No child phase can restart the clock.
        remaining()
        # GI imports stay in the child; offline tests need no desktop packages.
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi, GLib
        remaining()
        if Atspi.init() not in (0, 1):
            raise ProbeError("ATSPI_INITIALIZATION_FAILED")
        remaining()
        Atspi.set_timeout(800, 2500)

        launch = journal.read("launch-intent")
        if launch["data_root"] != str((scratch / "data").resolve()):
            raise ProbeError("JOURNAL_DATA_ROOT_MISMATCH")
        binding = launch["binding"]
        remaining()
        bridge = AtspiBridge(Atspi, deadline=deadline)
        attempt = Attempt(bridge, x11_identity, journal, binding,
                          deadline=deadline)
        loop = GLib.MainLoop()
        queued = False
        busy = False

        def finish(reason):
            nonlocal result
            if result is None:
                result = attempt.report(reason)
                loop.quit()

        def advance():
            nonlocal queued, busy
            queued = False
            if result is not None or busy:
                return False
            busy = True
            try:
                if attempt.step():
                    finish("ACTION_RETURNED_TRUE")
                elif attempt.phase == "EDIT_ACKNOWLEDGED":
                    schedule()
            except NotReady as exc:
                attempt.last_condition = str(exc)
                if attempt.phase in ("EDIT_UNKNOWN", "CREATE_UNKNOWN"):
                    finish("EFFECT_UNCONFIRMED")
            except ProbeError as exc:
                finish(str(exc))
            except Exception as exc:
                finish("ATSPI_ERROR_" + type(exc).__name__)
            finally:
                busy = False
            return False

        def schedule(*_):
            nonlocal queued
            if result is None and not queued:
                queued = True
                GLib.idle_add(advance)

        remaining()
        listener = Atspi.EventListener.new(schedule, None)
        for event in events:
            remaining()
            if not listener.register(event):
                raise ProbeError("ATSPI_EVENT_REGISTRATION_FAILED")
        GLib.timeout_add(max(1, int(remaining() * 1000)),
                         lambda: (finish("DEADLINE"), False)[1])
        schedule()
        loop.run()
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ProbeError) else "SETUP_" + type(exc).__name__
        result = attempt.report(reason) if attempt is not None else {
            "schema": "motionwright.ci.accessible-action/1",
            "status": "NOT_SENT", "phase": "SETUP", "reason": reason,
            "project_commit": "NOT_VERIFIED_BY_ACTION",
        }
    finally:
        try:
            journal.put("result", result)
        finally:
            if listener is not None:
                for event in events:
                    try:
                        listener.deregister(event)
                    except Exception:
                        pass
            journal.close()
    return result


def invoke_worker(scratch, *, seconds, source_sha, semwright_sha, binding):
    directory = scratch / JOURNAL_NAME
    directory.mkdir(mode=0o700)  # No exist_ok: re-entering this data root is refused.
    journal = DiskJournal(directory)
    try:
        parent_fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        journal.put("launch-intent", {
            "source_sha": source_sha, "semwright_sha": semwright_sha,
            "scope": "one_attempt_same_isolated_data_root",
            "binding": binding,
            "data_root": str((scratch / "data").resolve()),
        })
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("MOTIONWRIGHT_SEMWRIGHT_CONNECTION", None)
        for key, folder in (("XDG_CONFIG_HOME", "config"),
                            ("XDG_DATA_HOME", "data"),
                            ("XDG_CACHE_HOME", "cache")):
            env[key] = str(scratch / folder)
        with (directory / "worker.stdout").open("xb") as out, (
            directory / "worker.stderr").open("xb") as err:
            try:
                # Linux's monotonic clock is shared by parent and child.
                # Reserve one second for the child's durable result custody.
                worker_deadline = time.monotonic() + max(1, seconds - 1)
                process = subprocess.run(
                    [sys.executable, "-B", str(Path(__file__).resolve()),
                     "--worker", "--scratch", str(scratch),
                     "--seconds", str(max(1, seconds - 1)),
                     "--worker-deadline", repr(worker_deadline)],
                    env=env, stdout=out, stderr=err, timeout=seconds, check=False,
                )
            except subprocess.TimeoutExpired:
                journal.put("parent-timeout", {"status": "UNKNOWN", "retry": "FORBIDDEN"})
                raise ProbeError("ACCESSIBLE_WORKER_TIMEOUT_UNKNOWN") from None
        path = directory / "result.json"
        if not path.is_file() or path.is_symlink():
            journal.put("parent-failure", {
                "status": "UNKNOWN", "exit_code": process.returncode, "retry": "FORBIDDEN",
            })
            raise ProbeError("ACCESSIBLE_WORKER_FAILED_NO_RETRY")
        if path.stat().st_size > 16384:
            raise ProbeError("WORKER_RESULT_LIMIT")
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("schema") != "motionwright.ci.accessible-action/1":
            raise ProbeError("WORKER_RESULT_SCHEMA")
        if result.get("status") != "ACTION_ACCEPTED":
            raise ProbeError("ACCESSIBLE_" + str(result.get("reason", "UNCONFIRMED")))
        if process.returncode or result.get("binding") != binding:
            raise ProbeError("WORKER_RESULT_IDENTITY_OR_EXIT")
        return result
    finally:
        journal.close()


def probe_accessible_cold_relaunch(*, app_run, scratch, source_sha,
                                  semwright_sha, seconds=24, storage_cohort=None):
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise ColdStartError("Disposable Linux CI required")
    if not os.environ.get("DISPLAY") or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise ColdStartError("Live X11 and session DBus required")
    if not SHA40.fullmatch(source_sha) or not SHA40.fullmatch(semwright_sha):
        raise ColdStartError("Exact source identities required")
    if not 4 <= seconds <= 45:
        raise ColdStartError("Launch deadline must be 4-45 seconds")
    options = {}
    ready = _bootstrap_committed
    if storage_cohort is not None:
        storage_cohort = storage.validate_cohort(storage_cohort, source_sha=source_sha)
        options = {"storage_cohort": storage_cohort}
        ready = partial(_bootstrap_committed, **options)
    if scratch.exists() or scratch.is_symlink():
        raise ColdStartError("First-use scratch must be new; existing evidence is retained")
    scratch = scratch.resolve()
    runner_temp = os.environ.get("RUNNER_TEMP")
    if not runner_temp:
        raise ColdStartError("Disposable RUNNER_TEMP root required")
    admitted = Path(runner_temp).resolve(strict=True)
    if scratch == admitted or not scratch.is_relative_to(admitted):
        raise ColdStartError("Scratch must be below disposable RUNNER_TEMP")
    scratch.mkdir(mode=0o700, parents=True, exist_ok=False)
    action = None
    started = False
    deadline = time.monotonic() + seconds

    def interact(root):
        nonlocal action, started
        if started:
            raise ProbeError("ACCESSIBLE_CALLBACK_REENTRY_FORBIDDEN")
        if not _empty_library_ready(root, **options):
            return False
        try:
            binding = x11_identity()
        except NotReady:
            return False
        budget = deadline - time.monotonic() - 2
        if budget < 2:
            raise ProbeError("NO_ACCESSIBLE_ATTEMPT_BUDGET")
        started = True
        action = invoke_worker(root, seconds=budget, source_sha=source_sha,
                               semwright_sha=semwright_sha, binding=binding)
        return True

    first = run_probe(app_run, scratch, seconds=seconds,
                      ready_check=ready, window_interaction=interact)
    if first.get("status") != "WINDOW_OBSERVED":
        raise ColdStartError("First installed window not observed")
    shutil.copyfile(scratch / "app-run.log", scratch / "app-run-first.log")
    initial = read_snapshot(scratch / "data", **options)
    second = run_probe(app_run, scratch, seconds=seconds, ready_check=ready)
    if second.get("status") != "WINDOW_OBSERVED":
        raise ColdStartError("Reopened installed window not observed")
    shutil.copyfile(scratch / "app-run.log", scratch / "app-run-second.log")
    reopened = read_snapshot(scratch / "data", **options)
    return {
        "schema": ("motionwright.ci.installed-cold-relaunch/1" if storage_cohort is None
                   else "motionwright.ci.installed-cold-relaunch/2"),
        **({"storage_cohort": storage_cohort} if storage_cohort is not None else {}),
        "status": "WINDOW_OBSERVED", "window_title": "Motionwright",
        "motionwright_sha": source_sha, "semwright_sha": semwright_sha,
        "scope": "extracted_unsigned_linux_appimage_rootless_xvfb",
        "launch_count": 2, "first_launch": first, "reopened_launch": second,
        "first_use_action": action,
        "persistence": attest_persistence(initial, reopened),
        "claims": {
            "semwright_production_runtime": "NOT_CONFIGURED",
            "native_renderer_execution": "NOT_RUN", "human_install_acceptance": "NOT_RUN",
            "human_creative_acceptance": "NOT_RUN", "fresh_physical_device": "NOT_PROVEN",
            "signed_distribution": "NOT_CLAIMED",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-deadline", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=24)
    parser.add_argument("--app-run", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--semwright-sha")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--storage-source-repo", type=Path)
    args = parser.parse_args()
    if args.worker:
        result = worker(args.scratch, args.seconds, parent_deadline=args.worker_deadline)
        raise SystemExit(0 if result["status"] == "ACTION_ACCEPTED" else 2)
    if not all((args.app_run, args.source_sha, args.semwright_sha, args.output)):
        parser.error("--app-run, --source-sha, --semwright-sha and --output are required")
    cohort = (storage.admit_checkout(args.storage_source_repo, args.source_sha)
              if args.storage_source_repo is not None else None)
    result = probe_accessible_cold_relaunch(
        app_run=args.app_run, scratch=args.scratch,
        source_sha=args.source_sha, semwright_sha=args.semwright_sha, seconds=args.seconds,
        storage_cohort=cohort,
    )
    if cohort is not None and storage.admit_checkout(args.storage_source_repo, args.source_sha) != cohort:
        raise ColdStartError("Storage source changed during installed cold relaunch")
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, sort_keys=True)
        out.write("\n")


if __name__ == "__main__":
    main()
