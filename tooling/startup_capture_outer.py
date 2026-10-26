"""Five-pipe outer custody for the existing bounded startup capture interfaces.

The stable launcher authenticates the workflow-to-manifest source binding and
supplies the original clock and child environment. This module retains the
five-channel owner interface; it does not create installed-run admission or a
product or human verdict. An old operation is never replayed here.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import time
import types
import uuid


CORE_SHA256 = "509d7fc1756064764ab1970806ad63ef70d4b161ea1343bedd711e1a4d3f3468"
COLLECTOR_SHA256 = "9db88b0342a445074490c07a94baf40bdb34483cfe9cc8712a818ceee0eb7875"
OBSERVER_SHA256 = "0ab7b0ceb993eac9a62e459382dbd5453f7582f28e0ebf9c2a67e76ec83093d0"
LAUNCHER_SHA256 = "57e0ffbd7ec02bd2dc0fcb590e7ea286074e661f3c09f37f871cdcb536b9906b"
CAPTURE_SCHEMA = "motionwright.private.a2-observer-capture-proposal/1"
PHASE_SCHEMA = "motionwright.private.independent-startup-phase/1"
CHANNEL_NAMES = ("phase", "observer_stdout", "observer_stderr", "capture_result",
                 "observer_subtree_status")
ORIGINAL_CHANNELS = {"cli.stdout.original": "observer_stdout",
                     "supervisor.stderr.original": "observer_stderr",
                     "subtree.status.original": "observer_subtree_status"}
MAX_PROCESSES = 128
MAX_PROC_ENTRIES = 4096
NS = 1_000_000_000
OUTPUT_LIMIT = 2 * 1024 * 1024


class OuterError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise OuterError(message)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _source_bytes(path, expected=None, limit=131072):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path,
            "canonical source path required")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit,
                "bounded regular source required")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        require(len(raw) <= limit and signature(before) == signature(after),
                "source changed during read")
        require(expected is None or sha256(raw) == expected, "exact source digest required")
        return raw
    finally:
        os.close(fd)


def _load_exact(name, path, expected, *, limit=131072):
    # Compile the authenticated bytes. -B alone does not forbid reading pyc.
    raw = _source_bytes(path, expected, limit)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), "exec"), module.__dict__)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module


HERE = Path(__file__).resolve().parent
core = _load_exact("_a6_outer_core_" + CORE_SHA256,
                   HERE / "startup_capture_core.py", CORE_SHA256)
_previous_core = sys.modules.get("startup_capture_core")
sys.modules["startup_capture_core"] = core
try:
    collector_api = _load_exact("_a6_outer_collector_" + COLLECTOR_SHA256,
                               HERE / "startup_capture_collector.py", COLLECTOR_SHA256)
finally:
    if _previous_core is None:
        sys.modules.pop("startup_capture_core", None)
    else:
        sys.modules["startup_capture_core"] = _previous_core

launcher_api = _load_exact("_a6_outer_launcher_" + LAUNCHER_SHA256,
                          HERE / "startup_capture_launcher.py", LAUNCHER_SHA256,
                          limit=262144)


def _child_environment(environment):
    require(type(environment) is dict, "explicit allowlisted child environment required")
    try:
        return launcher_api.allowlisted_environment(environment)
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as error:
        raise OuterError("child environment refused:" + str(error)[:160]) from error


def _load_observer_parser(path):
    module = _load_exact("_a6_outer_observer65_" + OBSERVER_SHA256,
                         Path(path), OBSERVER_SHA256, limit=98304)
    require(module.SCHEMA == "motionwright.private.readonly-atspi-admission/2"
            and callable(module.parse_supervisor), "observer parser interface mismatch")
    return module


class OwnedDescriptor:
    """One ownership generation; a reused integer is a different descriptor."""
    def __init__(self, fd, label):
        require(type(fd) is int and fd >= 0, "owned descriptor required")
        self.fd, self.original_fd, self.label = fd, fd, label
        self.close_attempted, self.close_error = False, None

    def close(self):
        if self.fd is None:
            return self.close_error is None
        fd, self.fd = self.fd, None
        self.close_attempted = True
        try:
            os.close(fd)
        except OSError as error:
            self.close_error = {"type": type(error).__name__, "errno": error.errno}
            return False
        return True

    def metadata(self):
        return {"label": self.label, "fd": self.original_fd, "retained": self.fd is not None,
                "close_attempted": self.close_attempted, "close_error": self.close_error}


def _pipe_identity(fd):
    import fcntl
    info = os.fstat(fd)
    require(stat.S_ISFIFO(info.st_mode), "actual pipe required")
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)], fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE


class FivePipes:
    def __init__(self, retention_reference):
        require(type(retention_reference) is str and
                re.fullmatch(r"[A-Za-z0-9_.:-]{8,100}", retention_reference),
                "bounded retention reference required")
        self.parent, self.child, self.descriptors, self.channels = {}, {}, [], {}
        identities = set()
        try:
            for name in CHANNEL_NAMES:
                read, write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
                r = OwnedDescriptor(read, name + ":read")
                self.descriptors.append(r)
                w = OwnedDescriptor(write, name + ":write")
                self.descriptors.append(w)
                require(read >= 3 and write >= 3, "capture channels must not replace standard descriptors")
                identity, read_access = _pipe_identity(read)
                write_identity, write_access = _pipe_identity(write)
                require(identity == write_identity and read_access == os.O_RDONLY and
                        write_access == os.O_WRONLY and tuple(identity) not in identities,
                        "distinct directional pipe identity required")
                identities.add(tuple(identity))
                self.parent[name], self.child[name] = (w, r) if name == "capture_result" else (r, w)
                self.channels[name] = {"fd": self.child[name].fd, "identity": identity,
                    "retention_reference": retention_reference + ":" + name}
        except BaseException as error:
            self.close_all()
            error.owned_descriptors = [item.metadata() for item in self.descriptors]
            raise

    def close_child_copies(self):
        return [item.metadata() for item in self.child.values() if not item.close()]

    def close_all(self):
        return [item.metadata() for item in self.descriptors if not item.close()]

    def collector_channels(self):
        result = {}
        for name, channel in {**ORIGINAL_CHANNELS, "phase": "phase"}.items():
            result[name] = {"fd": self.parent[channel].fd,
                           "identity": list(self.channels[channel]["identity"])}
        return result


def _stamp(pid):
    require(type(pid) is int and pid >= 1, "typed PID required")
    raw = core._read_proc_bound(f"/proc/{pid}/stat", 4096)
    value = core._parse_proc_stat(raw, pid)
    value["uid"] = Path(f"/proc/{pid}").stat().st_uid
    value["state"] = raw[raw.rfind(b")") + 2:].split()[0].decode("ascii")
    require(type(value["pid"]) is int and value["pid"] > 0 and
            all(type(value[key]) is int and value[key] >= 0 for key in
                ("start_ticks", "parent_pid", "session_id", "process_group_id")),
            "invalid kernel process identity")
    return value


def _identity_key(item):
    return tuple(item[key] for key in ("pid", "start_ticks", "uid", "session_id", "process_group_id"))


def _pidfd_exited(fd):
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    events = poller.poll(0)
    require(not any(mask & (select.POLLERR | select.POLLNVAL) for _, mask in events),
            "retained pidfd unavailable")
    return any(mask & (select.POLLIN | select.POLLHUP) for _, mask in events)


def _a2_binding(item, parent):
    return {"pid": item["pid"], "start_ticks": str(item["start_ticks"]),
            "parent_pid": parent["pid"], "parent_start_ticks": str(parent["start_ticks"]),
            "session_id": item["session_id"], "process_group_id": item["process_group_id"]}


def _core_binding(item, parent):
    result = _a2_binding(item, parent)
    result["start_ticks"] = int(result["start_ticks"])
    result["parent_start_ticks"] = int(result["parent_start_ticks"])
    return result


def _typed_equal(left, right):
    return (type(left) is dict and set(left) == set(right) and
            all(type(left[key]) is type(value) and left[key] == value
                for key, value in right.items()))


class RetainedProcess:
    def __init__(self, role, item, parent, fd):
        self.role, self.stamp, self.parent = role, item, parent
        self.pidfd = OwnedDescriptor(fd, role + ":pidfd")
        self.bound_ns = time.monotonic_ns()
        self.authenticated, self.authentication_error = False, None
        self.exit_ns, self.wait, self.wait_lost = None, None, False
        self.signals = []

    def poll_exit(self):
        require(self.authenticated, "unverified process handle cannot prove an exit")
        if self.exit_ns is not None:
            return True
        require(self.pidfd.fd is not None, "released process handle cannot prove a new exit")
        if self.exit_ns is None and _pidfd_exited(self.pidfd.fd):
            self.exit_ns = time.monotonic_ns()
        return self.exit_ns is not None

    def signal_once(self, sig):
        if not self.authenticated or self.pidfd.fd is None:
            return
        if any(record["signal"] == int(sig) for record in self.signals) or self.poll_exit():
            return
        record = {"signal": int(sig), "mono_ns": time.monotonic_ns(),
                  "transport": "RETAINED_PIDFD", "outcome": "UNKNOWN"}
        self.signals.append(record)
        try:
            signal.pidfd_send_signal(self.pidfd.fd, sig, None, 0)
            record["outcome"] = "SENT"
        except ProcessLookupError:
            record["outcome"] = "ALREADY_EXITED_WAIT_REQUIRED"
        except OSError as error:
            record["error"] = type(error).__name__

    def metadata(self):
        return {"role": self.role, "identity": dict(self.stamp),
                "binding": _a2_binding(self.stamp, self.parent),
                "bound_monotonic_ns": self.bound_ns, "pidfd_exit_monotonic_ns": self.exit_ns,
                "identity_authenticated": self.authenticated, "authentication_error": self.authentication_error,
                "original_wait": self.wait, "wait_lost": self.wait_lost,
                "pidfd": self.pidfd.metadata(), "signals": list(self.signals)}


class OuterAssembler:
    """Own five channels and independently assemble the existing A2 receipt.

    Construct before launching the consumer. ``launch`` starts exactly one
    source-bound direct child; ``step`` never renews the inherited budget.
    This class does not grant source or installed-operation admission.
    """
    def __init__(self, directory, operation_id, retention_reference, budget, *, observer_source):
        require(isinstance(budget, core.Budget), "inherited immutable Budget required")
        require(str(uuid.UUID(operation_id)) == operation_id, "canonical operation ID required")
        core._linux()
        require(time.monotonic_ns() < budget.operation_ns, "operation deadline already expired")
        self.budget, self.directory = budget, Path(directory)
        info = self.directory.stat()
        require(self.directory.is_absolute() and self.directory.resolve(strict=True) == self.directory
                and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o700, "private canonical capture directory required")
        self.operation_id, self.retention_reference = operation_id, retention_reference
        self.observer_api = _load_observer_parser(observer_source)
        self.source_sha256 = sha256(_source_bytes(Path(__file__).resolve()))
        self.owner = _stamp(os.getpid())
        self.pipes = FivePipes(retention_reference)
        try:
            self.collector = collector_api.PassiveCollector(
                self.pipes.collector_channels(), self.directory, budget)
        except BaseException as error:
            self.pipes.close_all()
            error.owned_descriptors = [item.metadata() for item in self.pipes.descriptors]
            raise
        self.processes, self.extra_owned, self.errors = {}, {}, []
        self.log_descriptors = []
        self.child, self.child_wait, self.child_wait_lost = None, None, False
        self.launch_attempted = False
        self.phase_offset, self.status_offset, self.stderr_offset = 0, 0, 0
        self.phase_sequence, self.phase_last_ns = 0, budget.started_ns
        self.launch_phase_ns, self.supervisor_wait = None, None
        self.expected_supervisor, self.expected_cli, self.supervisor_context = None, None, None
        self.parsed_supervisor, self.parsed_subtree = None, None
        self.receipt, self.receipt_bytes, self.receipt_attempted = None, None, False
        self.receipt_sent_ns, self.receipt_status = None, "NOT_ATTEMPTED"
        self.closure_ns, self.cleanup_started = None, False
        self.finalized = False

    def _error(self, code):
        if code not in self.errors:
            self.errors.append(code)

    def _retain(self, role, pid, parent, *, live=True):
        require(time.monotonic_ns() < self.budget.cleanup_ns, "identity after cleanup deadline")
        require(type(pid) is int and pid > 1 and pid != self.owner["pid"],
                "PID1 and the outer are never retainable child targets")
        require(role not in self.processes and len(self.processes) + len(self.extra_owned) < MAX_PROCESSES,
                "duplicate or excessive process retention")
        require(pid not in self.extra_owned and all(item.stamp["pid"] != pid for item in self.processes.values()),
                "one process cannot occupy multiple ownership roles")
        first = _stamp(pid)
        require(first["start_ticks"] > 0 and first["session_id"] > 0 and first["process_group_id"] > 0,
                "retainable userspace identity required")
        require(first["uid"] == parent["uid"] == os.getuid() and
                first["parent_pid"] == parent["pid"] and
                first["start_ticks"] >= parent["start_ticks"], "process is not an owned child")
        require(not live or first["state"] != "Z", "live identity was not retained")
        fd = os.pidfd_open(pid, 0)
        retained = RetainedProcess(role, first, parent, fd)
        # Retain the handle before any post-open validation which can fail.
        self.processes[role] = retained
        try:
            second = _stamp(pid)
            actual_parent = _stamp(parent["pid"])
            require(_identity_key(first) == _identity_key(second) and
                    _identity_key(parent) == _identity_key(actual_parent) and
                    second["parent_pid"] == parent["pid"] and
                    (not live or (second["state"] != "Z" and not _pidfd_exited(fd))),
                    "process identity changed while retaining pidfd")
        except BaseException as error:
            retained.authentication_error = type(error).__name__ + ":" + str(error)[:160]
            # This descriptor is ours, but its target has not been admitted.
            # Closing our copy is safe; signaling the target would not be.
            retained.pidfd.close()
            raise
        retained.authenticated = True
        return retained

    def launch(self, command, *, source_sha256, env=None, stdout=None, stderr=None):
        require(not self.launch_attempted, "consumer launch already consumed")
        require(type(command) is list and len(command) >= 4 and command[1:3] == ["-I", "-B"]
                and all(type(value) is str and "\0" not in value for value in command),
                "isolated Python source command required")
        require(type(source_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", source_sha256),
                "launch requires an exact source digest")
        child_env = _child_environment(env)
        require(Path(command[0]).resolve(strict=True) == Path(sys.executable).resolve(strict=True),
                "current interpreter required")
        _source_bytes(Path(command[3]), source_sha256)
        require(time.monotonic_ns() < self.budget.operation_ns, "no launch after original D")
        self.launch_attempted = True
        try:
            self.child = subprocess.Popen(command, env=child_env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL if stdout is None else stdout,
                stderr=subprocess.DEVNULL if stderr is None else stderr,
                close_fds=True, pass_fds=tuple(item.fd for item in self.pipes.child.values()),
                start_new_session=True)
            self._retain("consumer", self.child.pid, self.owner, live=False)
        except BaseException as error:
            self._error("consumer launch/binding unresolved:" + type(error).__name__)
            # The actual Popen handle, if any, remains in self.child for wait.
        finally:
            if self.pipes.close_child_copies():
                self._error("consumer channel-copy close uncertain")
        return self.child

    def _bytes(self, name):
        if name == "phase":
            return self.collector.phase_original()
        return bytes(self.collector.original_pump.streams[
            self.pipes.parent[name].original_fd].data)

    def _frames(self, raw, offset, limit):
        output = []
        while True:
            end = raw.find(b"\n", offset)
            if end < 0:
                require(len(raw) - offset <= limit, "partial record exceeds frame budget")
                return output, offset
            require(end + 1 - offset <= limit, "record exceeds frame budget")
            output.append((core.decode_frame(raw[offset:end+1], eof=True, limit=limit), end + 1))
            offset = end + 1

    def _read_subtree(self):
        raw = self._bytes("observer_subtree_status")
        frames, self.status_offset = self._frames(raw, self.status_offset, core.FRAME_LIMIT)
        for frame, _ in frames:
            if self.expected_supervisor is not None:
                continue  # Full core transcript validation follows actual EOF.
            require(frame.get("schema") == core.SCHEMA and frame.get("kind") == "LAUNCH_BOUND"
                    and type(frame.get("sequence")) is int and frame["sequence"] == 1,
                    "first fifth-pipe frame must bind the launch")
            consumer = self.processes.get("consumer")
            require(consumer is not None and not consumer.poll_exit(), "live consumer identity missing")
            declared = frame.get("supervisor", {})
            supervisor = self._retain("supervisor", declared.get("pid"), consumer.stamp)
            expected = {key: supervisor.stamp[key] for key in
                        ("pid", "start_ticks", "parent_pid", "session_id", "process_group_id")}
            expected["source_sha256"] = CORE_SHA256
            require(_typed_equal(declared, expected), "fifth-pipe supervisor differs from retained identity")
            cli_declared = frame.get("cli", {})
            cli = self._retain("cli", cli_declared.get("pid"), supervisor.stamp)
            require(cli.stamp["pid"] == cli.stamp["session_id"] == cli.stamp["process_group_id"],
                    "CLI independent session/group required")
            expected_cli = _core_binding(cli.stamp, supervisor.stamp)
            require(_typed_equal(cli_declared, expected_cli), "fifth-pipe CLI differs from retained identity")
            when = frame.get("observed_monotonic_ns")
            require(type(when) is int and self.budget.started_ns <= when <= time.monotonic_ns()
                    < self.budget.join_ns, "fifth-pipe launch outside inherited budget")
            self.expected_supervisor, self.expected_cli = expected, expected_cli

    def _read_phase(self):
        raw = self._bytes("phase")
        frames, self.phase_offset = self._frames(raw, self.phase_offset, 2048)
        if self.collector.phase_metadata()["eof"]:
            require(self.phase_offset == len(raw), "phase EOF contains a partial final record")
        for frame, _ in frames:
            require(set(frame) == {"schema", "operation_id", "pid", "seq", "mono_ns", "event", "phase", "detail"}
                    and frame["schema"] == PHASE_SCHEMA and frame["operation_id"] == self.operation_id
                    and self.child is not None and type(frame["pid"]) is int and frame["pid"] == self.child.pid
                    and type(frame["seq"]) is int and frame["seq"] == self.phase_sequence + 1
                    and type(frame["mono_ns"]) is int and
                    self.phase_last_ns <= frame["mono_ns"] <= time.monotonic_ns() <= self.budget.cleanup_ns
                    and type(frame["detail"]) is dict, "phase source/sequence/clock mismatch")
            self.phase_sequence, self.phase_last_ns = frame["seq"], frame["mono_ns"]
            if frame["event"] == "ENTER" and frame["phase"] == "observer-supervisor-start":
                require(self.launch_phase_ns is None, "duplicate observer launch phase")
                self.launch_phase_ns = frame["mono_ns"]
            if frame["event"] == "RETURN" and frame["phase"] == "observer-supervisor-wait":
                require(self.supervisor_wait is None, "duplicate supervisor original wait")
                self.supervisor_wait = dict(frame["detail"])
                self.supervisor_wait["phase_observed_monotonic_ns"] = time.monotonic_ns()
            if frame["event"] == "RETURN" and frame["phase"] == "app-Popen":
                consumer = self.processes.get("consumer")
                require(consumer is not None, "consumer identity missing before application binding")
                application = self._retain("application", frame["detail"].get("returned_pid"), consumer.stamp)
                require(application.stamp["pid"] == application.stamp["session_id"]
                        == application.stamp["process_group_id"], "application launcher group mismatch")

    def _read_worker(self):
        raw = self._bytes("observer_stderr")
        frames, self.stderr_offset = self._frames(raw, self.stderr_offset, self.observer_api.MAX_SUPERVISOR_LINE)
        for frame, end in frames:
            context = frame.get("context")
            require(type(context) is dict and set(context) == {"observer_sha256", "admission_sha256"}
                    and context["observer_sha256"] == OBSERVER_SHA256 and
                    type(context["admission_sha256"]) is str and
                    re.fullmatch(r"[0-9a-f]{64}", context["admission_sha256"]), "observer context missing")
            require(self.supervisor_context is None or context == self.supervisor_context,
                    "observer admission context changed")
            self.supervisor_context = context
            require(frame.get("outcome") != "ERROR", "observer custody reported an error")
            if frame.get("event") != "PROCESS_BOUND":
                continue
            parsed = self.observer_api.parse_supervisor(raw[:end], self.supervisor_context)
            require(len(parsed["worker_bindings"]) == 1, "exactly one observer worker required")
            declared = next(iter(parsed["worker_bindings"].values()))["binding"]
            cli = self.processes.get("cli")
            require(cli is not None, "CLI identity missing before worker binding")
            if "worker" not in self.processes:
                self._retain("worker", declared.get("pid"), cli.stamp)
            worker = self.processes["worker"]
            require(_typed_equal(declared, _a2_binding(worker.stamp, cli.stamp)),
                    "worker differs from independently retained identity")

    def _poll_identities(self):
        for role, item in self.processes.items():
            if not item.authenticated or item.pidfd.fd is None:
                continue
            if item.poll_exit():
                continue
            try:
                current = _stamp(item.stamp["pid"])
            except (FileNotFoundError, ProcessLookupError):
                require(item.poll_exit(), "identity disappeared without retained exit")
                continue
            allowed_parent = {item.parent["pid"]}
            if role == "worker" and "supervisor" in self.processes:
                allowed_parent.add(self.processes["supervisor"].stamp["pid"])
            require(_identity_key(current) == _identity_key(item.stamp) and
                    current["parent_pid"] in allowed_parent, "retained identity or group escaped")

    def _sample_application_children(self):
        # Only descendants of the already bound application are sampled. The
        # observer's transitional pre-exec forks are bound via the fifth pipe.
        root = self.processes.get("application")
        if root is None or not root.authenticated:
            return
        queue = [root, *self.extra_owned.values()]
        seen = set()
        while queue:
            require(time.monotonic_ns() < self.budget.cleanup_ns, "application cleanup inventory deadline")
            parent = queue.pop(0)
            if not parent.authenticated or parent.pidfd.fd is None:
                continue
            pid = parent.stamp["pid"]
            if pid in seen or parent.poll_exit():
                continue
            seen.add(pid)
            raw = core._read_proc_bound(f"/proc/{pid}/task/{pid}/children", 65536)
            values = raw.split()
            require(len(values) <= MAX_PROCESSES and all(value.isdigit() for value in values),
                    "application child inventory limit")
            for value in values:
                child_pid = int(value)
                if child_pid in self.extra_owned or any(
                        item.stamp["pid"] == child_pid for item in self.processes.values()):
                    continue
                require(len(self.processes) + len(self.extra_owned) < MAX_PROCESSES,
                        "application descendant limit")
                first = _stamp(child_pid)
                require(first["parent_pid"] == pid and first["uid"] == self.owner["uid"]
                        and first["start_ticks"] >= parent.stamp["start_ticks"],
                        "foreign application descendant")
                fd = os.pidfd_open(child_pid, 0)
                child = RetainedProcess("application-descendant", first, parent.stamp, fd)
                self.extra_owned[child_pid] = child
                try:
                    after = _stamp(child_pid)
                    current_parent = _stamp(pid)
                    require(_identity_key(first) == _identity_key(after) and after["parent_pid"] == pid
                            and _identity_key(current_parent) == _identity_key(parent.stamp),
                            "application descendant identity changed while binding")
                except BaseException as error:
                    child.authentication_error = type(error).__name__ + ":" + str(error)[:160]
                    child.pidfd.close()
                    raise
                child.authenticated = True
                queue.append(child)

    def _poll_child_wait(self):
        if self.child is None or self.child_wait is not None or self.child_wait_lost:
            return
        try:
            pid, status = os.waitpid(self.child.pid, os.WNOHANG)
            if not pid:
                return
            require(pid == self.child.pid, "foreign direct-child wait")
            code = os.waitstatus_to_exitcode(status)
            self.child.returncode = code
            self.child_wait = {"pid": pid, "wait_status": status, "exit_code": code,
                               "observed_monotonic_ns": time.monotonic_ns()}
            if "consumer" in self.processes:
                self.processes["consumer"].wait = self.child_wait
                require(self.processes["consumer"].authenticated and self.processes["consumer"].poll_exit(),
                        "consumer wait/pidfd disagreement")
        except (OSError, OuterError) as error:
            self.child_wait_lost = True
            self._error("consumer original wait unavailable:" + type(error).__name__)

    def _groups_clear(self):
        groups = {(self.processes[role].stamp["session_id"], self.processes[role].stamp["process_group_id"])
                  for role in ("cli", "worker")}
        count = 0
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            count += 1
            require(count <= MAX_PROC_ENTRIES and time.monotonic_ns() < self.budget.join_ns,
                    "bounded group inventory unavailable")
            try:
                item = _stamp(int(path.name))
            except (FileNotFoundError, ProcessLookupError):
                continue
            if (item["session_id"], item["process_group_id"]) in groups and item["state"] != "Z":
                raise OuterError("live or unidentified observer group member")

    def _validate_join(self):
        require(not self.errors and self.collector.originals_status == "PERSISTED", "durable originals not complete")
        require(all(role in self.processes for role in ("consumer", "supervisor", "cli", "worker")),
                "independently retained identities missing")
        require(all(self.processes[role].authenticated for role in ("consumer", "supervisor", "cli", "worker")),
                "unverified handle cannot support capture closure")
        require(not self.processes["consumer"].poll_exit(), "consumer exited before capture-result delivery")
        require(all(self.processes[role].poll_exit() for role in ("supervisor", "cli", "worker")),
                "owned observer exits not observed")
        wait = self.supervisor_wait
        supervisor = self.processes["supervisor"]
        require(type(wait) is dict and set(wait) == {"supervisor_pid", "supervisor_start_ticks",
                "actual_wait_status", "actual_exit_code", "observed_monotonic_ns", "phase_observed_monotonic_ns"}
                and type(wait["supervisor_pid"]) is int and wait["supervisor_pid"] == supervisor.stamp["pid"]
                and type(wait["supervisor_start_ticks"]) is int
                and wait["supervisor_start_ticks"] == supervisor.stamp["start_ticks"]
                and type(wait["actual_wait_status"]) is int and type(wait["actual_exit_code"]) is int
                and wait["actual_exit_code"] == os.waitstatus_to_exitcode(wait["actual_wait_status"]) == 0,
                "actual supervisor original wait missing or changed")
        require(type(wait["observed_monotonic_ns"]) is int and
                self.budget.started_ns <= wait["observed_monotonic_ns"] <= wait["phase_observed_monotonic_ns"]
                <= time.monotonic_ns() <= self.budget.join_ns, "supervisor wait outside original join")
        snapshot = self.collector.snapshot()
        require(type(self.launch_phase_ns) is int and
                snapshot["capture_started_monotonic_ns"] <= self.launch_phase_ns,
                "capture did not start before observer launch")
        originals = self.collector.originals
        subtree = core.validate_transcript(originals["subtree.status.original"], eof=True,
            expected_supervisor=self.expected_supervisor, expected_cli=self.expected_cli,
            deadline_ns=self.budget.join_ns)
        require(self.launch_phase_ns <= subtree["launch"]["observed_monotonic_ns"] and
                subtree["terminal"]["completed_monotonic_ns"] <= snapshot["capture_ended_monotonic_ns"],
                "subtree terminal/capture order mismatch")
        parsed = self.observer_api.parse_supervisor(originals["supervisor.stderr.original"], self.supervisor_context)
        require(not parsed["pending_io"] and len(parsed["worker_bindings"]) == 1,
                "observer originals incomplete")
        worker_report = next(iter(parsed["worker_bindings"].values()))
        require(set(worker_report["roles"]) == {"WORKER_SELF", "PARENT_OBSERVED_WORKER"}
                and _typed_equal(worker_report["binding"], _a2_binding(self.processes["worker"].stamp,
                    self.processes["cli"].stamp)), "worker roles or final binding incomplete")
        require(type(worker_report["deadline_monotonic_ns"]) is int and
                self.budget.started_ns < worker_report["deadline_monotonic_ns"] <= self.budget.operation_ns,
                "observer worker extended the inherited operation deadline")
        require(set(parsed["processes"]) == {str(self.processes["cli"].stamp["pid"]),
                                           str(self.processes["worker"].stamp["pid"])},
                "foreign supervisor emitter")
        result = parsed["processes"][str(self.processes["cli"].stamp["pid"])]["result"]
        require(result is not None and result["status"] != "UNKNOWN", "provisional observer result")
        require(parsed["processes"][str(self.processes["worker"].stamp["pid"])]["result"] is None,
                "worker must not emit a second parent result")
        for line in originals["supervisor.stderr.original"].splitlines(keepends=True):
            frame = core.decode_frame(line, eof=True, limit=self.observer_api.MAX_SUPERVISOR_LINE)
            when = frame.get("mono_ns")
            require(type(when) is int and subtree["launch"]["observed_monotonic_ns"] <= when
                    <= self.budget.operation_ns, "observer frame outside inherited operation budget")
        self._groups_clear()
        self.parsed_subtree, self.parsed_supervisor = subtree, parsed
        self.closure_ns = time.monotonic_ns()
        require(snapshot["capture_ended_monotonic_ns"] <= self.closure_ns <= self.budget.join_ns,
                "closure after original join deadline")

    def _make_receipt(self):
        self._validate_join()
        cli = _a2_binding(self.processes["cli"].stamp, self.processes["supervisor"].stamp)
        worker = _a2_binding(self.processes["worker"].stamp, self.processes["cli"].stamp)
        snapshot = self.collector.snapshot()
        names = {"cli_stdout": "cli.stdout.original", "supervisor": "supervisor.stderr.original",
                 "subtree_status": "subtree.status.original"}
        streams = {key: {"path": str(self.directory / name), "bytes": len(self.collector.originals[name]),
                        "sha256": sha256(self.collector.originals[name]), "eof": True, "truncated": False}
                   for key, name in names.items()}
        return {"schema": CAPTURE_SCHEMA, "operation_id": self.operation_id,
            "observer_sha256": OBSERVER_SHA256, "admission_sha256": self.supervisor_context["admission_sha256"],
            "collector": {"pid": self.owner["pid"], "start_ticks": str(self.owner["start_ticks"]),
                          "source_sha256": self.source_sha256}, "cli": cli,
            "capture_started_monotonic_ns": snapshot["capture_started_monotonic_ns"],
            "capture_ended_monotonic_ns": snapshot["capture_ended_monotonic_ns"],
            "streams": streams, "closure": {"cli": cli, "worker": worker,
                "group_ids": sorted({cli["process_group_id"], worker["process_group_id"]}),
                "groups_closed": True, "process_tree_closed": True, "escaped": [],
                "unidentified": [], "survivors": [], "completed_monotonic_ns": self.closure_ns},
            "retention_reference": self.pipes.channels["capture_result"]["retention_reference"],
            "complete": True, "errors": []}

    def _send_once(self):
        require(not self.receipt_attempted and not self.errors, "capture-result attempt already consumed or refused")
        self.receipt = self._make_receipt()
        self.receipt_bytes = (json.dumps(self.receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
        endpoint = self.pipes.parent["capture_result"]
        require(endpoint.fd is not None and len(self.receipt_bytes) <= min(core.FRAME_LIMIT,
                os.fpathconf(endpoint.fd, "PC_PIPE_BUF")), "capture result exceeds atomic pipe budget")
        require(time.monotonic_ns() < self.budget.join_ns, "no result write after original join")
        self.receipt_attempted, self.receipt_status = True, "UNKNOWN"
        try:
            written = os.write(endpoint.fd, self.receipt_bytes)
            require(written == len(self.receipt_bytes), "partial capture-result write")
            require(time.monotonic_ns() <= self.budget.join_ns, "capture-result write finished late")
            self.receipt_sent_ns = time.monotonic_ns()
        except (OSError, OuterError) as error:
            self._error("capture-result write unresolved:" + type(error).__name__)
        finally:
            if not endpoint.close():
                self._error("capture-result EOF close uncertain")
        if not self.errors and time.monotonic_ns() <= self.budget.join_ns:
            self.receipt_status = "WRITTEN_AND_EOF_CLOSED"
        elif not self.errors:
            self._error("capture-result EOF finished after original join")

    def _watchdog(self):
        # Signals are addressed only to retained pidfds. No numeric PID/group
        # signal is used, and an uncertain signal is never retried.
        now = time.monotonic_ns()
        if now < self.budget.cleanup_ns - 2 * NS or now >= self.budget.cleanup_ns:
            return
        candidates = [item for item in (*self.extra_owned.values(), *self.processes.values())
                      if item.authenticated and item.pidfd.fd is not None and not item.poll_exit()]
        if not candidates:
            return
        sig = signal.SIGTERM if now < self.budget.cleanup_ns - NS else signal.SIGKILL
        self.cleanup_started = True
        self._error("consumer exceeded reserved cleanup window")
        for item in reversed(tuple(self.extra_owned.values())):
            if item.pidfd.fd is not None:
                item.signal_once(sig)
        for role in ("application", "worker", "cli", "supervisor", "consumer"):
            item = self.processes.get(role)
            if item is not None and item.pidfd.fd is not None:
                item.signal_once(sig)

    def step(self):
        if self.finalized:
            return self.snapshot()
        if time.monotonic_ns() >= self.budget.cleanup_ns:
            self._error("fixed cleanup cap reached")
            return self.snapshot()
        try:
            self.collector.step_originals()
            self.collector.step_phase()
            self._read_subtree()
            self._read_phase()
            self._read_worker()
            self._poll_identities()
            self._sample_application_children()
        except (OSError, ValueError, KeyError, TypeError, OuterError, core.CaptureError) as error:
            self._error("capture/identity verification:" + type(error).__name__ + ":" + str(error)[:160])
        self._poll_child_wait()
        if self.child_wait is not None and self.receipt_status != "WRITTEN_AND_EOF_CLOSED":
            self._error("consumer exited without complete capture-result exchange")
        if self.collector.errors:
            self._error("original custody unresolved")
        if self.collector.phase_errors:
            self._error("phase custody unresolved")
        if (not self.errors and not self.receipt_attempted and self.collector.originals_status == "PERSISTED"
                and self.supervisor_wait is not None):
            try:
                self._send_once()
            except (OSError, ValueError, KeyError, TypeError, OuterError, core.CaptureError) as error:
                self._error("capture join refused:" + type(error).__name__ + ":" + str(error)[:160])
        if self.errors and not self.receipt_attempted:
            if not self.pipes.parent["capture_result"].close():
                self._error("failed-result EOF close uncertain")
            self.receipt_status = "NO_COMPLETE_RESULT"
        try:
            self._watchdog()
        except (OSError, OuterError) as error:
            self._error("owned cleanup unresolved:" + type(error).__name__)
        return self.snapshot()

    def finish(self):
        """Wait only to the inherited C; unresolved handles remain retained."""
        if self.finalized:
            return self.snapshot()
        if self.child is None and self.collector.writer is None:
            self._error("consumer launch has no retained direct-child handle")
            if self.pipes.close_all():
                self._error("prelaunch descriptor close uncertain")
            self.finalized = True
            return self.snapshot()
        while time.monotonic_ns() < self.budget.cleanup_ns:
            self.step()
            try:
                all_exited = all(not item.authenticated or item.poll_exit() for item in
                    (*self.processes.values(), *self.extra_owned.values()))
            except (OSError, OuterError) as error:
                all_exited = False
                self._error("retained terminal check unresolved:" + type(error).__name__)
            if (self.child_wait is not None and all_exited and self.collector.phase_metadata()["eof"]
                    and self.collector.originals_status in ("PERSISTED", "UNKNOWN")
                    and (self.collector.writer is None or self.collector.writer_wait is not None)):
                break
            time.sleep(min(.005, max(0, (self.budget.cleanup_ns - time.monotonic_ns()) / NS)))
        if self.child_wait is None:
            self._error("original consumer terminal unavailable at fixed C")
        if time.monotonic_ns() >= self.budget.cleanup_ns:
            self._error("outer finalization reached the original cleanup cap")
        if self.collector.phase_metadata()["status"] != "EOF_OBSERVED":
            self._error("phase original did not reach a complete EOF")
        if not self.collector.close_writer_descriptors():
            self._error("writer descriptors unresolved")
        for role, item in (*self.processes.items(),
                           *(("application-descendant", item) for item in self.extra_owned.values())):
            if not item.authenticated:
                item.pidfd.close()
                self._error("process identity was never authenticated:" + role)
                continue
            try:
                closed = item.poll_exit()
            except (OSError, OuterError):
                closed = False
            if closed and (role != "consumer" or self.child_wait is not None):
                if not item.pidfd.close():
                    self._error("retained process descriptor close uncertain")
            else:
                self._error("process retained without terminal exit:" + role)
        if self.child_wait is not None or not self.launch_attempted:
            if self.pipes.close_all():
                self._error("channel descriptor close uncertain")
        self.finalized = True
        return self.snapshot()

    def snapshot(self):
        return {"schema": "motionwright.startup-capture-outer/1", "scope": "CAPTURE_INFRASTRUCTURE_ONLY",
            "operation_id": self.operation_id, "status": "CAPTURE_CUSTODY_COMPLETE" if
                self.finalized and not self.errors and self.receipt_status == "WRITTEN_AND_EOF_CLOSED" else "UNKNOWN",
            "errors": list(self.errors), "start_monotonic_ns": self.budget.started_ns,
            "operation_deadline_ns": self.budget.operation_ns, "join_deadline_ns": self.budget.join_ns,
            "cleanup_deadline_ns": self.budget.cleanup_ns, "launch_attempted": self.launch_attempted,
            "consumer_original_wait": self.child_wait, "consumer_wait_lost": self.child_wait_lost,
            "capture_result_status": self.receipt_status, "capture_result_attempted": self.receipt_attempted,
            "capture_result_sent_monotonic_ns": self.receipt_sent_ns,
            "capture_result_sha256": None if self.receipt_bytes is None else sha256(self.receipt_bytes),
            "capture_result_original_base64": None if self.receipt_bytes is None else
                base64.b64encode(self.receipt_bytes).decode("ascii"),
            "capture_result": self.receipt, "collector": self.collector.snapshot(),
            "retained_processes": [item.metadata() for item in self.processes.values()],
            "application_descendants": [item.metadata() for item in self.extra_owned.values()],
            "descriptors": [item.metadata() for item in self.pipes.descriptors],
            "log_descriptors": [item.metadata() for item in getattr(self, "log_descriptors", ())],
            "outer_pre_G_retention_claimed": False, "application_tree_closed": False,
            "installed_result": "NOT_RUN", "automatic_retry": False,
            "formal_acceptance": False, "release_authorized": False}


def _reviewed_outer_sources(raw):
    tree = ast.parse(raw)
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "REVIEWED_OUTER_SOURCES"
                      for target in node.targets)]
    require(len(values) == 1, "owner outer allowlist declaration missing")
    value = values[0]
    require(isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset" and not value.keywords and len(value.args) <= 1,
            "owner outer allowlist must be literal")
    entries = () if not value.args else ast.literal_eval(value.args[0])
    require(type(entries) in (tuple, list, set) and all(type(item) is str and
            re.fullmatch(r"[0-9a-f]{64}", item) for item in entries), "invalid reviewed outer digest set")
    return frozenset(entries)


def _owner_admission(plan, outer_sha, launch_context):
    require(launch_context is not None, "verified inherited launch context required")
    require(type(plan) is dict, "exact owner plan object required")
    require(plan.get("status") == "A1_ADMITTED_INDEPENDENT_STARTUP" and
            plan.get("observer_status") == "ADMITTED_NEW_READONLY_OBSERVATION",
            "A1 exact operation admission required")
    try:
        verified = launcher_api.verify_child_launch_context(
            launch_context, outer_sha, plan, expected_launcher_sha256=LAUNCHER_SHA256)
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as error:
        raise OuterError("inherited launcher admission refused:" + str(error)[:200]) from error
    manifest = verified["manifest"]
    require(all(manifest["sources"][role]["sha256"] == expected for role, expected in
                (("launcher", LAUNCHER_SHA256), ("outer", outer_sha),
                 ("core", CORE_SHA256), ("collector", COLLECTOR_SHA256),
                 ("observer", OBSERVER_SHA256))),
            "verified manifest differs from loaded capture sources")
    require(all(Path(verified["sources"][role]["path"]) == HERE / filename and
                verified["sources"][role]["sha256"] == manifest["sources"][role]["sha256"]
                for role, filename in
                (("launcher", "startup_capture_launcher.py"),
                 ("outer", "startup_capture_outer.py"),
                 ("core", "startup_capture_core.py"),
                 ("collector", "startup_capture_collector.py"))),
            "verified capture source path differs from loaded source")
    item = plan["sources"]["bootstrap"]
    expected_bootstrap = manifest["sources"]["bootstrap"]["sha256"]
    require(item.get("sha256") == expected_bootstrap and
            verified["sources"]["bootstrap"]["sha256"] == expected_bootstrap and
            Path(item["path"]) == Path(verified["sources"]["bootstrap"]["path"]),
            "exact reviewed A2 consumer required")
    raw = _source_bytes(Path(item["path"]), expected_bootstrap)
    require(outer_sha in _reviewed_outer_sources(raw), "outer source has not been admitted by the owner")
    require(plan["sources"]["observer"]["sha256"] == OBSERVER_SHA256 and
            plan["sources"]["observer_subtree_core"]["sha256"] == CORE_SHA256,
            "exact owner parser/core required")
    return raw, verified


def require_owner_admission(plan, outer_sha, *, launch_context=None):
    """Read the admitted consumer only after the inherited launch is verified.

    The caller's plan cannot choose its own source identity. The manifest is
    authenticated through the stable launcher and original workflow binding;
    the consumer still must contain this exact outer in its literal allowlist.
    """
    return _owner_admission(plan, outer_sha, launch_context)[0]


def _inherited_budget(verified):
    started = verified["started_ns"]
    require(type(started) is int and 0 < started <= time.monotonic_ns(),
            "mandatory original monotonic clock required")
    budget = core.Budget(started)
    expected = {"operation_ns": budget.operation_ns, "join_ns": budget.join_ns,
                "cleanup_ns": budget.cleanup_ns, "custody_ns": started + 40 * NS}
    require(all(type(verified[key]) is int and verified[key] == value
                for key, value in expected.items()), "original D/J/C/H cannot change")
    require(time.monotonic_ns() < budget.operation_ns, "original operation clock expired before effects")
    return budget, expected["custody_ns"]


def _before_operation(budget):
    require(time.monotonic_ns() < budget.operation_ns, "original operation deadline before next effect")


def _write_new(path, raw):
    with Path(path).open("xb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def run_admitted(plan, *, launch_context=None):
    """Consume an externally authenticated launch without renewing its clock."""
    require(launch_context is not None, "verified inherited launch context required")
    source_path = Path(__file__).resolve()
    source_raw = _source_bytes(source_path)
    _, verified = _owner_admission(plan, sha256(source_raw), launch_context)
    budget, custody_ns = _inherited_budget(verified)
    child_environment = _child_environment(verified["environment"])
    argv_raw = core._read_proc_bound("/proc/self/cmdline", 65536)
    argv = argv_raw.rstrip(b"\0").split(b"\0")
    require(len(argv) >= 4 and argv[1:4] == [b"-I", b"-B", os.fsencode(source_path)],
            "actual outer script invocation required")
    parent = Path(plan["parent"])
    info = parent.stat()
    require(parent.is_absolute() and parent.resolve(strict=True) == parent and
            info.st_uid == os.getuid() and not info.st_mode & 0o022, "private admitted parent required")
    operation_id = plan["operation_id"]
    require(str(uuid.UUID(operation_id)) == operation_id, "canonical operation ID required")
    capture = parent / ("a2-capture-" + operation_id)
    _before_operation(budget)
    capture.mkdir(mode=0o700)
    outer_root = parent / ("a6-outer-" + operation_id)
    _before_operation(budget)
    outer_root.mkdir(mode=0o700)
    _before_operation(budget)
    assembler = OuterAssembler(capture, operation_id, "a6-outer-" + operation_id, budget,
        observer_source=plan["sources"]["observer"]["path"])
    try:
        _launch_prepared_owner(assembler, plan, source_path, argv_raw, outer_root,
                               child_environment=child_environment)
    except BaseException as error:
        assembler._error("owner assembly/launch unresolved:" + type(error).__name__ + ":" + str(error)[:160])
    if assembler.child is not None:
        result = assembler.finish()
    else:
        if assembler.pipes.close_all():
            assembler._error("prelaunch descriptor close uncertain")
        assembler.finalized = True
        result = assembler.snapshot()
    result["launcher_provenance"] = verified["provenance"]
    result["admission_scope"] = verified["manifest"]["scope"]
    result["custody_deadline_ns"] = custody_ns
    result["phase_durable"] = False
    return result, assembler.collector.phase_original()


def _launch_prepared_owner(assembler, plan, source_path, argv_raw, outer_root, *, child_environment):
    budget, capture, operation_id = assembler.budget, assembler.directory, assembler.operation_id
    child_environment = _child_environment(child_environment)
    _before_operation(budget)
    spec = dict(plan)
    spec.update(start_mono_ns=budget.started_ns, channels=assembler.pipes.channels, capture_directory=str(capture))
    executable = Path(os.readlink("/proc/self/exe"))
    executable_raw = _source_bytes(executable, limit=536870912)
    receipt = {"operation_id": operation_id, "deadline_mono_ns": budget.cleanup_ns,
        "source_sha256": assembler.source_sha256, "launcher_pid": assembler.owner["pid"],
        "launcher_start_ticks": str(assembler.owner["start_ticks"]),
        "channels": spec["channels"], "session_capture_reference": spec["session_capture_reference"],
        "capture_directory": str(capture), "capture_schema": CAPTURE_SCHEMA,
        "observer_subtree_core": spec["sources"]["observer_subtree_core"]}
    receipt_raw = (json.dumps(receipt, sort_keys=True) + "\n").encode()
    receipt_path = outer_root / "outer-intent.json"
    _before_operation(budget)
    _write_new(receipt_path, receipt_raw)
    spec["external_parent"] = {"status": "A1_EXTERNAL_BOOTSTRAP_WATCHDOG_ADMITTED",
        "source": {"path": str(source_path), "sha256": assembler.source_sha256},
        "pid": assembler.owner["pid"], "start_ticks": str(assembler.owner["start_ticks"]),
        "deadline_mono_ns": budget.cleanup_ns,
        "executable": {"path": str(executable), "sha256": sha256(executable_raw), "bytes": len(executable_raw)},
        "cmdline_sha256": sha256(argv_raw), "receipt": {"path": str(receipt_path), "sha256": sha256(receipt_raw)}}
    spec_path = outer_root / "consumer-plan.json"
    _before_operation(budget)
    _write_new(spec_path, (json.dumps(spec, sort_keys=True) + "\n").encode())
    logs = []
    try:
        for name in ("stdout", "stderr"):
            _before_operation(budget)
            fd = os.open(outer_root / ("consumer." + name + ".original"),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            logs.append(OwnedDescriptor(fd, "consumer-log:" + name))
            assembler.log_descriptors.append(logs[-1])
        assembler.launch([str(executable), "-I", "-B", spec["sources"]["bootstrap"]["path"],
                          "--plan", str(spec_path)], source_sha256=spec["sources"]["bootstrap"]["sha256"],
                         env=child_environment, stdout=logs[0].fd, stderr=logs[1].fd)
    finally:
        for item in logs:
            if not item.close():
                assembler._error("consumer log descriptor close uncertain")


def _emit_bounded_document(document, *, custody_ns, fd=1):
    """Transport one report without filesystem work or a renewed deadline.

    External launcher custody, including actual wait/EOF and independent
    persistence/readback, is still required. A written prefix is not durability.
    """
    require(type(custody_ns) is int and time.monotonic_ns() < custody_ns,
            "original external custody deadline expired")
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    require(len(raw) <= OUTPUT_LIMIT, "outer result exceeds bounded transport")
    _, access = _pipe_identity(fd)
    require(access == os.O_WRONLY, "outer result requires the launcher's writable pipe")
    os.set_blocking(fd, False)
    offset = 0
    while offset < len(raw):
        require(time.monotonic_ns() < custody_ns, "outer result transport reached original H")
        try:
            written = os.write(fd, raw[offset:offset + 65536])
        except BlockingIOError:
            remaining = max(0, (custody_ns - time.monotonic_ns()) / NS)
            select.select([], [fd], [], min(.01, remaining))
            continue
        require(type(written) is int and 0 < written <= min(65536, len(raw) - offset),
                "outer result write incomplete")
        offset += written
        require(time.monotonic_ns() < custody_ns, "outer result completed after original H")
    return {"bytes": len(raw), "sha256": sha256(raw), "durable": False}


def _best_effort_error(error):
    """One nonblocking diagnostic attempt; never a flush or a replay."""
    raw = (json.dumps({"status": "REFUSED_OR_UNKNOWN", "reason": str(error)[:256],
        "automatic_retry": False, "installed_result": "NOT_RUN",
        "formal_acceptance": False}, sort_keys=True) + "\n").encode()
    try:
        _, access = _pipe_identity(2)
        if access != os.O_WRONLY:
            return
        os.set_blocking(2, False)
        os.write(2, raw)
    except (RuntimeError, OSError, ValueError, ImportError):
        pass


class _InvocationParser(argparse.ArgumentParser):
    def error(self, message):
        raise OuterError("invalid inherited invocation:" + str(message)[:200])


def main():
    # Direct/help invocations have no authenticated clock or output custody;
    # argparse must not introduce a blocking print/exit path for them.
    parser = _InvocationParser(description=__doc__, add_help=False)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--launch-context-fd", type=int, required=True)
    parser.add_argument("--started-ns", type=int, required=True)
    args = parser.parse_args()
    # The stable reader validates the mandatory original clock before touching
    # the inherited descriptor, and consumes its ownership exactly once.
    context = launcher_api.read_launch_context(args.launch_context_fd, started_ns=args.started_ns)
    plan = core.decode_frame(_source_bytes(args.plan.resolve(), limit=131072), eof=True, limit=131072)
    result, phase = run_admitted(plan, launch_context=context)
    result["phase_original_base64"] = base64.b64encode(phase).decode("ascii")
    result["phase_durable"] = False
    _emit_bounded_document(result, custody_ns=result["custody_deadline_ns"])
    return 0 if result["status"] == "CAPTURE_CUSTODY_COMPLETE" else 2


if __name__ == "__main__":
    try:
        exit_code = main()
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as error:
        _best_effort_error(error)
        exit_code = 2
    # All report bytes use the explicit bounded transport. Interpreter shutdown
    # must not introduce a buffered flush after the fixed external deadline.
    os._exit(exit_code)
