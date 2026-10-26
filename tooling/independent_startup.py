from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import select
import stat
import subprocess
import sys
import time
import types
import uuid

SOURCE = "995e91988078488b69a6656028c30af9c8b286e6"
SDK = "54d3459a051adbce4cae9f60c0dea8f60c824be6"
OBSERVER_SHA = "0ab7b0ceb993eac9a62e459382dbd5453f7582f28e0ebf9c2a67e76ec83093d0"
# Only this exact external watchdog source may supply the retained parent
# and original custody receipt. Never select a source from untrusted plan data.
REVIEWED_OUTER_SOURCES = frozenset({
    "a47c5e6920ff9748dfe708bac554be1f6150414b8b186277ba1b5e366265dc37"
})
PINS = {
    "appimage": (91073016, "dcf5a7d59e6ccef19216b4fa4cfcc031683fb1bbf673c9812dd0ad66a4e5a732"),
    "elf": (47348553, "8f55c1d4e9fc21912a8ecf4cbc6128ddc89600a11bc70c04779e4cfb4cb092a6"),
    "apprun": (274, "eb0b254ac0dae6543e6dd7cd02e1baf40a060de95b927313122d6b46323c00aa"),
}
ENV_KEYS = ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR")
BUS_KEYS = ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "AT_SPI_BUS_ADDRESS")
PROC = Path("/proc")  # Never a CLI/environment override.
NS = 1_000_000_000

class Refused(RuntimeError):
    pass

def require(ok, code):
    if not ok:
        raise Refused(code)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "DUPLICATE_JSON_FIELD")
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(Refused("NONFINITE_JSON")))

def signature(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

def checked_file(path, expected_sha, expected_size=None, *, limit=131072, proc_link=False):
    path = Path(path)
    require(path.is_absolute(), "ABSOLUTE_FILE_REQUIRED")
    if not proc_link:
        require(path.resolve(strict=True) == path, "CANONICAL_FILE_REQUIRED")
    flags = os.O_RDONLY | (0 if proc_link else getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "FILE_TYPE_OR_LIMIT")
        size = before.st_size if expected_size is None else expected_size
        require(before.st_size == size, "FILE_SIZE_MISMATCH")
        h, count = hashlib.sha256(), 0
        while count <= size:
            block = os.read(fd, min(1048576, size + 1 - count))
            if not block:
                break
            count += len(block)
            require(count <= size, "FILE_GREW")
            h.update(block)
        require(count == size and signature(os.fstat(fd)) == signature(before), "FILE_CHANGED")
        require(h.hexdigest() == expected_sha, "FILE_SHA_MISMATCH")
        return signature(before)
    finally:
        os.close(fd)

def small_file(path, limit=131072):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, "CANONICAL_INPUT_REQUIRED")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "INPUT_TYPE_OR_LIMIT")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        require(len(raw) == before.st_size and signature(os.fstat(fd)) == signature(before), "INPUT_CHANGED")
        return raw
    finally:
        os.close(fd)

@dataclass(frozen=True)
class ProcessStamp:
    pid: int
    start_ticks: str
    uid: int
    parent: int
    group: int
    session: int
    state: str

    def key(self):
        return (self.pid, self.start_ticks, self.uid, self.group, self.session)

def process_stamp(pid):
    root = PROC / str(pid)
    info = root.stat()
    raw = (root / "stat").read_bytes()
    require(len(raw) <= 8192 and raw.startswith(f"{pid} (".encode()), "PROC_STAT_IDENTITY")
    fields = raw[raw.rfind(b")") + 2:].split()
    require(len(fields) >= 20 and fields[19].isdigit(), "PROC_START_TICKS")
    return ProcessStamp(pid, fields[19].decode("ascii"), info.st_uid,
        int(fields[1]), int(fields[2]), int(fields[3]), fields[0].decode("ascii"))

def same_process(expected, *, live=True):
    try:
        now = process_stamp(expected.pid)
        return now.key() == expected.key() and (not live or now.state != "Z")
    except (OSError, ValueError, Refused):
        return False

def process_table():
    paths = [p for p in PROC.iterdir() if p.name.isdigit()]
    require(len(paths) <= 4096, "PROCESS_INVENTORY_LIMIT")
    out = {}
    for path in paths:
        try:
            item = process_stamp(int(path.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        if item.uid == os.getuid() and item.state != "Z":
            out[item.pid] = item
    return out

def owned_tree(root):
    require(same_process(root), "OWNED_ROOT_IDENTITY_CHANGED")
    table, owned = process_table(), {root.pid: root}
    for _ in range(128):
        added = False
        for pid, item in table.items():
            parent = owned.get(item.parent)
            if pid not in owned and parent is not None and int(item.start_ticks) >= int(parent.start_ticks):
                owned[pid], added = item, True
        require(len(owned) <= 128, "OWNED_PROCESS_LIMIT")
        if not added:
            break
    require(same_process(root), "OWNED_ROOT_CHANGED_DURING_INVENTORY")
    return tuple(owned.values())

class PhaseChannel:
    """Independent A2 channel; never A6's supervisor stream or journal."""
    def __init__(self, fd, operation_id):
        require(type(fd) is int and fd >= 3 and not os.isatty(fd), "PHASE_CHANNEL_REQUIRED")
        require(any(f(os.fstat(fd).st_mode) for f in (stat.S_ISFIFO, stat.S_ISSOCK)), "PHASE_PIPE_OR_SOCKET_REQUIRED")
        os.set_blocking(fd, False)
        self.fd, self.operation_id, self.seq = fd, operation_id, 0

    def emit(self, event, phase, **detail):
        self.seq += 1
        raw = (json.dumps({"schema":"motionwright.private.independent-startup-phase/1",
            "operation_id":self.operation_id, "pid":os.getpid(), "seq":self.seq,
            "mono_ns":time.monotonic_ns(), "event":event, "phase":phase,
            "detail":detail}, separators=(",", ":")) + "\n").encode()
        require(len(raw) <= 2048, "PHASE_RECORD_LIMIT")
        try:
            written = os.write(self.fd, raw)
        except BlockingIOError:
            raise Refused("PHASE_CHANNEL_BACKPRESSURE_NO_RETRY") from None
        require(written == len(raw), "PHASE_CHANNEL_PARTIAL_NO_RETRY")

@dataclass(frozen=True)
class AliveHandoff:
    operation_id: str
    launcher: ProcessStamp
    target: ProcessStamp
    executable: str
    executable_sha256: str
    deadline_mono_ns: int
    cleanup_cap_mono_ns: int
    worker_seconds: int
    watchdog_seconds: int
    profile: str
    process_environment: dict
    bus_environment_sha256: dict
    boot_id: str

def require_pidfd_support():
    require(hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"),
        "PIDFD_API_REQUIRED_BEFORE_LAUNCH")
    fd = None
    try:
        fd = os.pidfd_open(os.getpid(), 0)
        signal.pidfd_send_signal(fd, 0, None, 0)
    except OSError as error:
        raise Refused("PIDFD_KERNEL_OR_POLICY_UNAVAILABLE") from error
    finally:
        if fd is not None:
            os.close(fd)

def bind_pidfd(expected):
    fd = os.pidfd_open(expected.pid, 0)
    try:
        actual = process_stamp(expected.pid)
        require(actual.key() == expected.key(), "PIDFD_POST_OPEN_IDENTITY_MISMATCH")
        return fd
    except BaseException:
        os.close(fd)
        raise

def pidfd_exited(fd):
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    events = poller.poll(0)
    require(not any(mask & (select.POLLERR | select.POLLNVAL) for _, mask in events),
        "PIDFD_POLL_FAILED")
    return any(mask & (select.POLLIN | select.POLLHUP) for _, mask in events)

def verify_outer(spec, cleanup_cap):
    outer = spec.get("external_parent")
    require(type(outer) is dict, "MISSING_REVIEWED_OUTER_WATCHDOG")
    source = outer.get("source", {})
    require(source.get("sha256") in REVIEWED_OUTER_SOURCES, "OUTER_WATCHDOG_SOURCE_NOT_REVIEWED")
    require(outer.get("status") == "A1_EXTERNAL_BOOTSTRAP_WATCHDOG_ADMITTED", "OUTER_ADMISSION_REQUIRED")
    require(outer.get("deadline_mono_ns") == cleanup_cap, "OUTER_DEADLINE_MUST_EQUAL_FIXED_C")
    require(outer.get("pid") == os.getppid(), "OUTER_PARENT_PID_MISMATCH")
    owner = process_stamp(os.getppid())
    require(owner.start_ticks == outer.get("start_ticks") and owner.uid == os.getuid(), "OUTER_START_OR_UID_MISMATCH")
    checked_file(Path(source["path"]), source["sha256"])
    exe = outer["executable"]
    require(os.readlink(PROC / str(owner.pid) / "exe") == exe["path"], "OUTER_EXECUTABLE_PATH_MISMATCH")
    checked_file(PROC / str(owner.pid) / "exe", exe["sha256"], exe["bytes"], limit=536870912, proc_link=True)
    argv_raw = (PROC / str(owner.pid) / "cmdline").read_bytes()
    require(len(argv_raw) <= 65536 and digest(argv_raw) == outer["cmdline_sha256"], "OUTER_COMMAND_CHANGED")
    argv = argv_raw.rstrip(b"\0").split(b"\0")
    require(len(argv) >= 4 and argv[1:4] == [b"-I", b"-B", source["path"].encode()], "OUTER_SOURCE_NOT_ACTUAL_SCRIPT_ARGUMENT")
    require(Path(os.fsdecode(argv[0])).resolve(strict=True) == Path(exe["path"]), "OUTER_INTERPRETER_MISMATCH")
    raw = small_file(Path(outer["receipt"]["path"]))
    require(digest(raw) == outer["receipt"]["sha256"], "OUTER_RECEIPT_CHANGED")
    receipt = strict_json(raw)
    require(receipt.get("operation_id") == spec["operation_id"] and
        receipt.get("deadline_mono_ns") == cleanup_cap and
        receipt.get("source_sha256") == source["sha256"] and
        receipt.get("launcher_pid") == owner.pid and
        receipt.get("launcher_start_ticks") == owner.start_ticks, "OUTER_RECEIPT_BINDING")
    require(receipt.get("channels") == spec.get("channels") and
        receipt.get("session_capture_reference") == spec.get("session_capture_reference"),
        "OUTER_CUSTODY_CHANNEL_RECEIPT_BINDING")
    require(receipt.get("capture_directory") == spec.get("capture_directory") and
        receipt.get("capture_schema") == CAPTURE_SCHEMA, "OUTER_CAPTURE_JOIN_CONTRACT_BINDING")
    require(receipt.get("observer_subtree_core") == spec["sources"].get("observer_subtree_core"),
        "OUTER_SUBTREE_SOURCE_RECEIPT_BINDING")
    require(same_process(owner), "OUTER_CHANGED_DURING_PREFLIGHT")
    return owner

# Read-only observer consumers retain exact original wire bytes, process
# identity and all five bounded channels supplied by the external watchdog.
OBSERVER_BYTES = 57332
CAPTURE_SCHEMA = "motionwright.private.a2-observer-capture-proposal/1"
WORKER_GROUP_CONTRACT = "BIND_PID_START_PPID_START_SID_PGID_AND_CLOSE_SEPARATE_GROUP_NO_RETRY"
CAPTURE_LIMIT = 131072

def load_observer65(spec):
    item = spec["sources"]["observer"]
    raw = small_file(Path(item["path"]), limit=98304)
    require(len(raw) == OBSERVER_BYTES and digest(raw) == OBSERVER_SHA == item["sha256"],
        "EXACT_OBSERVER65_SOURCE_REQUIRED")
    module = types.ModuleType("_a2_held_observer65_parsers")
    module.__file__ = item["path"]
    # Only the actual owner's pure parsers are consumed; __main__ never runs.
    exec(compile(raw, item["path"], "exec"), module.__dict__)
    require(module.SCHEMA == "motionwright.private.readonly-atspi-admission/2" and
        module.WORKER_GROUP_CONTRACT == WORKER_GROUP_CONTRACT, "OBSERVER65_API_MISMATCH")
    return module

def observer_budget(global_deadline, now):
    require(type(global_deadline) is int and type(now) is int, "INTEGER_CLOCK_REQUIRED")
    # A retained whole-second boundary is representable by Observer65's float
    # worker clock without rounding its absolute cap beyond the inherited D.
    deadline = (global_deadline // NS) * NS
    remaining = deadline - now
    worker = min(20, remaining // NS - 2)
    if worker < 2:
        return None
    # external_supervision checks remaining <= this integer upper bound.
    # A floor would reject every nonzero fractional remainder.
    watchdog = (remaining + NS - 1) // NS
    require(worker + 2 <= watchdog <= 60 and deadline <= global_deadline,
        "OBSERVER_BUDGET_BOUNDARY")
    return {"deadline":deadline, "worker":worker, "watchdog":watchdog}

def binding_of(item, parent):
    return {"pid":item.pid, "start_ticks":item.start_ticks,
        "parent_pid":parent.pid, "parent_start_ticks":parent.start_ticks,
        "session_id":item.session, "process_group_id":item.group}

class CaptureReceiptChannel:
    """One compact JSON receipt then EOF from the reviewed outer collector.

    This fd is a distinct, pre-bound input. It does not carry observer stderr,
    cannot authorize a launch and is never opened from a caller-supplied path.
    The future collector must drain original streams independently of its disk IO.
    """
    def __init__(self, fd):
        require(type(fd) is int and fd >= 3 and not os.isatty(fd), "CAPTURE_RESULT_FD_REQUIRED")
        require(any(f(os.fstat(fd).st_mode) for f in (stat.S_ISFIFO, stat.S_ISSOCK)),
            "CAPTURE_RESULT_PIPE_REQUIRED")
        os.set_blocking(fd, False)
        self.fd, self.raw, self.eof = fd, bytearray(), False

    def poll(self):
        if self.eof:
            return
        try:
            raw = os.read(self.fd, min(16384, CAPTURE_LIMIT + 1 - len(self.raw)))
        except BlockingIOError:
            return
        if not raw:
            self.eof = True
            return
        self.raw.extend(raw)
        require(len(self.raw) <= CAPTURE_LIMIT, "CAPTURE_RESULT_LIMIT")

    def value(self):
        require(self.eof and self.raw and self.raw.endswith(b"\n") and
            self.raw.count(b"\n") == 1, "CAPTURE_RESULT_INCOMPLETE")
        return strict_json(bytes(self.raw))

SUBTREE_CORE_SHA = "509d7fc1756064764ab1970806ad63ef70d4b161ea1343bedd711e1a4d3f3468"
CAPTURE_CHANNEL_NAMES = ("phase", "observer_stdout", "observer_stderr", "capture_result", "observer_subtree_status")

def load_subtree_core(spec):
    item = spec["sources"]["observer_subtree_core"]
    raw = small_file(Path(item["path"]), limit=131072)
    require(item.get("sha256") == SUBTREE_CORE_SHA and digest(raw) == SUBTREE_CORE_SHA,
        "EXACT_SUBTREE_CORE_REQUIRED")
    name = "_a2_held_startup_capture_" + SUBTREE_CORE_SHA
    module = types.ModuleType(name)
    module.__file__ = item["path"]
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        exec(compile(raw, item["path"], "exec"), module.__dict__)
        require(module.SCHEMA == "motionwright.observer-subtree/1" and
            module.OPERATION_NS == 24*NS and module.CLEANUP_NS == 32*NS and
            module.STATUS_LIMIT == CAPTURE_LIMIT, "SUBTREE_CORE_API_MISMATCH")
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module

def core_binding(item, parent):
    return {"pid":item.pid, "start_ticks":int(item.start_ticks),
        "parent_pid":parent.pid, "parent_start_ticks":int(parent.start_ticks),
        "session_id":item.session, "process_group_id":item.group}

def require_fork_image(item, parent, spec):
    require(item.parent == parent.pid and item.uid == parent.uid == os.getuid() and
        same_process(parent) and same_process(item), "FORK_PARENT_OR_UID_CHANGED")
    expected = spec["sources"]["python"]
    require(os.readlink(PROC / str(item.pid) / "exe") == expected["path"],
        "FORK_EXECUTABLE_PATH_CHANGED")
    checked_file(PROC / str(item.pid) / "exe", expected["sha256"], expected.get("bytes"),
        limit=536870912, proc_link=True)
    actual_argv = (PROC / str(item.pid) / "cmdline").read_bytes()
    parent_argv = (PROC / str(parent.pid) / "cmdline").read_bytes()
    require(actual_argv and len(actual_argv) <= 65536 and actual_argv == parent_argv,
        "FORK_PREEXEC_ARGV_CHANGED")
    require(same_process(parent) and same_process(item), "FORK_IMAGE_IDENTITY_CHANGED")

class RetainedObserverSubtree:
    def __init__(self, core, handle, parent, spec, began_ns, phase):
        self.core, self.handle, self.parent, self.spec = core, handle, parent, spec
        self.began_ns, self.phase = began_ns, phase
        self.raw, self.eof, self.offset, self.records = bytearray(), False, 0, []
        self.pid, self.returncode, self.exit_seen_ns = None, None, None
        self.owner, self.supervisor, self.cli_pidfd = None, None, None
        self.expected_supervisor, self.expected_cli = None, None
        self.wait_code, self.wait_status, self.wait_seen_ns = None, None, None
        self.abort_attempted, self.admit_attempted = False, False
        self.first_abort_error = None
        self.cleanup_errors = []

    def _pump(self):
        if self.eof:
            return
        try:
            raw = os.read(self.handle.status_fd, min(16384, CAPTURE_LIMIT+1-len(self.raw)))
        except BlockingIOError:
            return
        if not raw:
            self.eof = True
            require(self.offset == len(self.raw), "SUBTREE_PARTIAL_STATUS_RECORD")
            return
        self.raw.extend(raw)
        require(len(self.raw) <= CAPTURE_LIMIT, "SUBTREE_STATUS_LIMIT")
        while True:
            end = self.raw.find(b"\n", self.offset)
            if end < 0:
                require(len(self.raw)-self.offset <= self.core.FRAME_LIMIT,
                    "SUBTREE_STATUS_RECORD_LIMIT")
                break
            frame = self.core.decode_frame(bytes(self.raw[self.offset:end+1]),
                eof=True, limit=self.core.FRAME_LIMIT)
            require(frame.get("schema") == self.core.SCHEMA and
                type(frame.get("sequence")) is int and
                frame["sequence"] == len(self.records)+1,
                "SUBTREE_STATUS_SEQUENCE")
            require(not self.records or self.records[-1].get("kind") != "TERMINAL",
                "SUBTREE_STATUS_AFTER_TERMINAL")
            require(frame.get("kind") == ("LAUNCH_BOUND" if not self.records else
                frame.get("kind")) and frame.get("kind") in {"LAUNCH_BOUND","REAP","TERMINAL"},
                "SUBTREE_STATUS_KIND")
            require(not self.records or frame.get("kind") != "LAUNCH_BOUND",
                "SUBTREE_DUPLICATE_LAUNCH")
            self.records.append(frame)
            self.offset = end+1

    def _wait(self):
        if self.wait_seen_ns is not None:
            return
        try:
            pid, status = os.waitpid(self.handle.pid, os.WNOHANG)
        except ChildProcessError as error:
            raise Refused("SUPERVISOR_TERMINAL_NOT_OWNED") from error
        if pid == 0:
            return
        require(pid == self.handle.pid, "SUPERVISOR_WAIT_IDENTITY")
        self.wait_status = status
        self.wait_code = os.waitstatus_to_exitcode(status)
        self.wait_seen_ns = time.monotonic_ns()
        if self.handle.pidfd is not None:
            require(pidfd_exited(self.handle.pidfd), "SUPERVISOR_WAIT_PIDFD_DISAGREEMENT")
        self.phase("RETURN", "observer-supervisor-wait", supervisor_pid=pid,
            supervisor_start_ticks=getattr(self.handle,"start_ticks",None),
            actual_wait_status=status, actual_exit_code=self.wait_code,
            observed_monotonic_ns=self.wait_seen_ns)

    def bind(self, cap):
        while not self.records and time.monotonic_ns() < cap:
            self._pump()
            self._wait()
            if self.eof or self.wait_seen_ns is not None:
                break
            if not self.records:
                time.sleep(min(.01, max(0,(cap-time.monotonic_ns())/NS)))
        require(time.monotonic_ns() < cap and self.records and
            self.records[0].get("kind") == "LAUNCH_BOUND", "SUBTREE_LAUNCH_BINDING_MISSING")
        launch = self.records[0]
        supervisor = process_stamp(self.handle.pid)
        require(supervisor.parent == self.parent.pid and supervisor.state != "Z" and
            int(supervisor.start_ticks) == self.handle.start_ticks and
            self.handle.pidfd is not None and not pidfd_exited(self.handle.pidfd),
            "SUBTREE_ACTUAL_SUPERVISOR_MISMATCH")
        require_fork_image(supervisor, self.parent, self.spec)
        expected_supervisor = {"pid":supervisor.pid, "start_ticks":int(supervisor.start_ticks),
            "parent_pid":self.parent.pid, "session_id":supervisor.session,
            "process_group_id":supervisor.group, "source_sha256":SUBTREE_CORE_SHA}
        reported = launch.get("supervisor")
        require(type(reported) is dict and set(reported) == set(expected_supervisor) and
            all(type(reported[k]) is int for k in expected_supervisor if k != "source_sha256"),
            "SUBTREE_SUPERVISOR_FIELD_TYPES")
        require(reported == expected_supervisor, "SUBTREE_REPORTED_SUPERVISOR_MISMATCH")
        declared = launch.get("cli")
        require(type(declared) is dict and type(declared.get("pid")) is int and
            declared["pid"] > 0, "SUBTREE_CLI_PID_TYPE")
        owner = process_stamp(declared["pid"])
        require(owner.parent == supervisor.pid and owner.uid == supervisor.uid == os.getuid() and
            owner.state != "Z" and owner.pid == owner.group == owner.session,
            "SUBTREE_ACTUAL_CLI_MISMATCH")
        self.cli_pidfd = bind_pidfd(owner)
        require_fork_image(owner, supervisor, self.spec)
        expected_cli = core_binding(owner, supervisor)
        require(set(declared) == set(expected_cli) and
            all(type(declared[k]) is int for k in expected_cli), "SUBTREE_CLI_FIELD_TYPES")
        require(declared == expected_cli, "SUBTREE_REPORTED_CLI_MISMATCH")
        when = launch.get("observed_monotonic_ns")
        require(type(when) is int and self.began_ns <= when <= time.monotonic_ns() < cap,
            "SUBTREE_LAUNCH_TIME_MISMATCH")
        self.owner, self.supervisor, self.pid = owner, supervisor, owner.pid
        self.expected_cli, self.expected_supervisor = expected_cli, expected_supervisor
        return owner, supervisor

    def poll(self):
        self._pump()
        self._wait()
        if self.expected_cli is None:
            return None
        matches = [r for r in self.records if r.get("kind") == "REAP" and
            r.get("identity",{}).get("pid") == self.pid]
        require(len(matches) <= 1, "SUBTREE_DUPLICATE_CLI_WAIT")
        if matches:
            record = matches[0]
            expected = {k:v for k,v in self.expected_cli.items() if k != "parent_start_ticks"}
            require(record.get("identity") == expected and type(record.get("exit_code")) is int,
                "SUBTREE_CLI_WAIT_CHANGED")
            require(pidfd_exited(self.cli_pidfd), "SUBTREE_CLI_WAIT_PIDFD_DISAGREEMENT")
            if self.returncode is None:
                self.returncode = record["exit_code"]
                self.exit_seen_ns = time.monotonic_ns()
        return self.returncode

    def admit(self, handoff):
        require(self.owner is not None and not self.admit_attempted and not self.abort_attempted,
            "SUBTREE_EXEC_ALREADY_CONSUMED")
        require(same_process(self.parent) and same_process(self.supervisor) and
            same_process(self.owner) and not pidfd_exited(self.cli_pidfd) and
            not pidfd_exited(self.handle.pidfd), "SUBTREE_BINDING_LOST_BEFORE_EXEC")
        source = self.spec["sources"]["observer_subtree_core"]
        checked_file(Path(source["path"]), SUBTREE_CORE_SHA)
        require(time.monotonic_ns()+(handoff.worker_seconds+2)*NS <= handoff.deadline_mono_ns,
            "SUBTREE_ADMISSION_CONSUMED_RESERVED_BUDGET")
        self.admit_attempted = True
        self.handle.admit_exec()

    def abort(self):
        if self.abort_attempted or getattr(self.handle,"ack_fd",-1) < 0:
            return
        self.abort_attempted = True
        try:
            self.handle.abort()
        except OSError as error:
            if self.first_abort_error is None:
                self.first_abort_error = error
            self.cleanup_errors.append("SUPERVISOR_ACK_CLOSE_UNCERTAIN")
        finally:
            self.handle.ack_fd = -1
            self.handle.aborted = True

    def finish(self, cap):
        while time.monotonic_ns() < cap and (self.wait_seen_ns is None or not self.eof):
            self.poll()
            if self.wait_seen_ns is None or not self.eof:
                time.sleep(min(.01, max(0,(cap-time.monotonic_ns())/NS)))
        require(time.monotonic_ns() <= cap and self.eof and self.wait_seen_ns is not None and
            self.wait_code == 0 and not self.cleanup_errors,
            "SUPERVISOR_ACTUAL_TERMINAL_UNCONFIRMED")

    def validate_join(self, independent_raw, cli_binding, cap):
        require(self.wait_seen_ns is not None and self.wait_code == 0 and
            self.wait_seen_ns <= cap and self.eof and not self.cleanup_errors,
            "SUBTREE_ACTUAL_WAIT_REQUIRED")
        require(self.expected_cli is not None and self.supervisor is not None and
            cli_binding == binding_of(self.owner,self.supervisor), "SUBTREE_JOIN_CLI_CHANGED")
        require(pidfd_exited(self.handle.pidfd) and pidfd_exited(self.cli_pidfd),
            "SUBTREE_RETAINED_EXIT_REQUIRED")
        require(independent_raw == bytes(self.raw), "SUBTREE_INDEPENDENT_BYTES_MISMATCH")
        parsed = self.core.validate_transcript(bytes(self.raw), eof=self.eof,
            expected_supervisor=self.expected_supervisor, expected_cli=self.expected_cli,
            deadline_ns=cap)
        require(parsed["launch"]["observed_monotonic_ns"] >= self.began_ns and
            parsed["terminal"]["cli_exit_code"] == self.returncode, "SUBTREE_FINAL_WAIT_CHANGED")
        return parsed

    def settle_failed(self, cap):
        self.abort()
        pump_failed = False
        while time.monotonic_ns() < cap:
            if not pump_failed:
                try:
                    self._pump()
                except Exception:
                    pump_failed = True
                    self.cleanup_errors.append("SUBTREE_STATUS_CUSTODY_FAILED")
            try:
                self._wait()
            except Exception:
                self.cleanup_errors.append("SUPERVISOR_WAIT_UNCONFIRMED")
                break
            if self.wait_seen_ns is not None and (self.eof or pump_failed):
                break
            time.sleep(min(.01, max(0,(cap-time.monotonic_ns())/NS)))
        return {"supervisor_pid":self.handle.pid,
            "supervisor_start_ticks":getattr(self.handle,"start_ticks",None),
            "actual_wait_status":self.wait_status, "actual_exit_code":self.wait_code,
            "wait_observed_monotonic_ns":self.wait_seen_ns,
            "status_bytes":len(self.raw), "status_sha256":digest(bytes(self.raw)),
            "status_eof":self.eof, "errors":list(self.cleanup_errors),
            "abort_error":None if self.first_abort_error is None else {
                "type":type(self.first_abort_error).__name__,
                "errno":self.first_abort_error.errno,
                "descriptor_close_confirmed":False},
            "process_tree_closed":"NOT_ADMITTED_FROM_FAILURE_CLEANUP"}

    def release_after_wait(self):
        if self.wait_seen_ns is None:
            return False
        pairs = [(self,"cli_pidfd"),(self.handle,"pidfd"),(self.handle,"status_fd"),(self.handle,"ack_fd")]
        for obj, key in pairs:
            fd = getattr(obj,key,None)
            if fd is None or fd < 0:
                continue
            setattr(obj,key,-1)
            try:
                os.close(fd)
            except OSError:
                self.cleanup_errors.append("SUBTREE_DESCRIPTOR_CLOSE_UNCERTAIN")
        return not self.cleanup_errors

class OwnedObserverProcesses:
    """Retain actual pidfds before using any observer-reported numeric identity.

    Procfs sampling can miss an escaped short-lived descendant. Therefore these
    controls prove known handles/groups only; an independent reviewed collector
    must additionally prove tree closure before any observation is admitted.
    """
    def __init__(self, child, owner, parent, forbidden):
        require(owner.parent == parent.pid and owner.uid == parent.uid == os.getuid() and
            owner.pid == child.pid == owner.group == owner.session and owner.state != "Z",
            "CLI_PROCESS_OWNERSHIP")
        self.child, self.root, self.parent = child, owner, parent
        self.forbidden, self.records, self.errors = set(forbidden), {}, []
        self._retain(owner, parent)

    def _retain(self, item, parent):
        require(item.pid not in self.forbidden and len(self.records) < 128 and
            item.uid == os.getuid() and item.parent == parent.pid and
            int(item.start_ticks) >= int(parent.start_ticks), "OBSERVED_DESCENDANT_SCOPE")
        current_parent = process_stamp(parent.pid)
        require(current_parent.key() == parent.key() and current_parent.state != "Z",
            "OBSERVED_PARENT_CHANGED")
        fd = bind_pidfd(item)
        self.records[item.pid] = {"stamp":item, "binding":binding_of(item, parent), "fd":fd,
            "signals":[], "exited":False}

    def sample(self, cap):
        if time.monotonic_ns() >= cap:
            return
        table = process_table()
        for item in self.records.values():
            if pidfd_exited(item["fd"]):
                item["exited"] = True
                continue
            current = table.get(item["stamp"].pid)
            if current is None or current.key() != item["stamp"].key():
                if "OWNED_IDENTITY_OR_GROUP_ESCAPE" not in self.errors:
                    self.errors.append("OWNED_IDENTITY_OR_GROUP_ESCAPE")
        for _ in range(128):
            changed = False
            for item in table.values():
                if time.monotonic_ns() >= cap:
                    return
                if item.pid in self.records:
                    continue
                parent = self.records.get(item.parent)
                if parent is None or parent["exited"]:
                    continue
                actual_parent = table.get(item.parent)
                if actual_parent is None or actual_parent.key() != parent["stamp"].key():
                    continue
                self._retain(item, actual_parent)
                changed = True
            if not changed:
                break
        groups = {(r["stamp"].session, r["stamp"].group) for r in self.records.values()}
        if any(p.pid not in self.records and (p.session, p.group) in groups for p in table.values()):
            if "UNIDENTIFIED_OWNED_GROUP_MEMBER" not in self.errors:
                self.errors.append("UNIDENTIFIED_OWNED_GROUP_MEMBER")

    def close(self, cap):
        result = {"status":"STOP_UNCONFIRMED", "errors":list(self.errors),
            "groups_closed":False, "tree_closure":"REQUIRES_INDEPENDENT_COLLECTOR",
            "root_exit_code":self.child.returncode, "records":[], "unknown_group_members":[]}
        try:
            if time.monotonic_ns() >= cap:
                result["errors"].append("FIXED_CLEANUP_CAP_EXPIRED")
                return result
            self.sample(cap)
            for sig in (signal.SIGTERM, signal.SIGKILL):
                if time.monotonic_ns() >= cap:
                    break
                for item in reversed(tuple(self.records.values())):
                    if time.monotonic_ns() >= cap:
                        break
                    if not pidfd_exited(item["fd"]):
                        try:
                            signal.pidfd_send_signal(item["fd"], sig, None, 0)
                            item["signals"].append({"signal":int(sig), "mono_ns":time.monotonic_ns(),
                                "transport":"RETAINED_PIDFD"})
                        except ProcessLookupError:
                            pass
                end = min(cap, time.monotonic_ns() + NS)
                while time.monotonic_ns() < end:
                    self.child.poll()
                    if all(pidfd_exited(r["fd"]) for r in self.records.values()):
                        break
                    self.sample(end)
                    time.sleep(min(.01, max(0, (end-time.monotonic_ns())/NS)))
                if all(pidfd_exited(r["fd"]) for r in self.records.values()):
                    break
            if time.monotonic_ns() >= cap:
                result["errors"].append("FIXED_CLEANUP_CAP_EXPIRED")
                return result
            self.child.poll()
            table = process_table()
            groups = {(r["stamp"].session, r["stamp"].group) for r in self.records.values()}
            live = [p.pid for p in table.values() if (p.session, p.group) in groups]
            result["unknown_group_members"] = live
            result["errors"].extend(e for e in self.errors if e not in result["errors"])
            for item in self.records.values():
                item["exited"] = pidfd_exited(item["fd"])
            result["groups_closed"] = (not live and not result["errors"] and
                self.child.returncode is not None and all(r["exited"] for r in self.records.values()))
            result["status"] = "OWNED_GROUPS_CLOSED" if result["groups_closed"] else "STOP_UNCONFIRMED"
            return result
        except (OSError, Refused, ValueError) as error:
            result["errors"].append(type(error).__name__)
            return result
        finally:
            result["root_exit_code"] = self.child.returncode
            result["records"] = [{"binding":r["binding"], "uid":r["stamp"].uid,
                "exit_observed_on_retained_pidfd":r["exited"], "signals":r["signals"]}
                for r in self.records.values()]
            result["completed_mono_ns"] = time.monotonic_ns()
            for item in self.records.values():
                os.close(item["fd"])

def capture_stream(descriptor, expected_path, limit):
    require(type(descriptor) is dict and set(descriptor) == {"path", "bytes", "sha256", "eof", "truncated"}
        and descriptor["path"] == str(expected_path) and type(descriptor["bytes"]) is int and
        0 <= descriptor["bytes"] <= limit and descriptor["eof"] is True and descriptor["truncated"] is False,
        "INDEPENDENT_STREAM_INCOMPLETE")
    raw = small_file(expected_path, limit=limit)
    require(len(raw) == descriptor["bytes"] and digest(raw) == descriptor["sha256"],
        "INDEPENDENT_STREAM_BYTES_CHANGED")
    return raw

def join_observer65(api, spec, handoff, admission_raw, cli_binding, popen_ns,
                    exit_code, capture, owned_closure, *, now_ns, subtree=None):
    """Join originals, never promote a provisional terminal or CLI exit alone."""
    join_cap = min(handoff.cleanup_cap_mono_ns, spec["start_mono_ns"]+26*NS)
    require(now_ns <= join_cap, "JOIN_AFTER_FIXED_CLEANUP_CAP")
    admission_value = api.admission(admission_raw)
    admission_sha = digest(admission_raw)
    require(admission_value["observer_sha256"] == OBSERVER_SHA and
        admission_value["external_supervision"]["absolute_deadline_monotonic_ns"] == handoff.deadline_mono_ns,
        "JOIN_ADMISSION_OR_DEADLINE_CHANGED")
    require(type(capture) is dict and set(capture) == {"schema", "operation_id", "observer_sha256",
        "admission_sha256", "collector", "cli", "capture_started_monotonic_ns", "capture_ended_monotonic_ns",
        "streams", "closure", "retention_reference", "complete", "errors"}, "CAPTURE_RECEIPT_FIELDS")
    require(capture["schema"] == CAPTURE_SCHEMA and capture["operation_id"] == spec["operation_id"] and
        capture["observer_sha256"] == OBSERVER_SHA and capture["admission_sha256"] == admission_sha and
        capture["cli"] == cli_binding and capture["complete"] is True and capture["errors"] == [],
        "CAPTURE_RECEIPT_CONTEXT_OR_INCOMPLETE")
    outer = spec["external_parent"]
    require(capture["collector"] == {"pid":outer["pid"], "start_ticks":outer["start_ticks"],
        "source_sha256":outer["source"]["sha256"]} and
        outer["source"]["sha256"] in REVIEWED_OUTER_SOURCES, "CAPTURE_NOT_FROM_REVIEWED_OUTER_SOURCE")
    require(capture["retention_reference"] == spec["channels"]["capture_result"]["retention_reference"],
        "CAPTURE_RETENTION_BINDING")
    begun, ended = capture["capture_started_monotonic_ns"], capture["capture_ended_monotonic_ns"]
    require(type(begun) is int and type(ended) is int and
        spec["start_mono_ns"] <= begun <= popen_ns <= ended <= handoff.cleanup_cap_mono_ns,
        "CAPTURE_NOT_COMPLETE_FROM_BEFORE_LAUNCH")
    require(type(capture["streams"]) is dict and set(capture["streams"]) == {"cli_stdout", "supervisor", "subtree_status"},
        "CAPTURE_STREAM_FIELDS")
    capture_root = Path(spec["capture_directory"])
    cli_raw = capture_stream(capture["streams"]["cli_stdout"], capture_root / "cli.stdout.original", 16384)
    supervisor_raw = capture_stream(capture["streams"]["supervisor"], capture_root / "supervisor.stderr.original", 16777216)
    subtree_raw = capture_stream(capture["streams"]["subtree_status"], capture_root / "subtree.status.original", CAPTURE_LIMIT)
    expected = {"observer_sha256":OBSERVER_SHA, "admission_sha256":admission_sha}
    parsed = api.parse_supervisor(supervisor_raw, expected)
    require(not parsed["pending_io"] and len(parsed["worker_bindings"]) == 1,
        "SUPERVISOR_PENDING_OR_WORKER_MISSING")
    worker = next(iter(parsed["worker_bindings"].values()))
    binding = worker["binding"]
    require(set(worker["roles"]) == {"PARENT_OBSERVED_WORKER", "WORKER_SELF"} and
        binding["parent_pid"] == cli_binding["pid"] and
        binding["parent_start_ticks"] == cli_binding["start_ticks"] and
        binding["pid"] not in {handoff.target.pid, handoff.launcher.pid, cli_binding["pid"]} and
        0 < worker["deadline_monotonic_ns"] <= handoff.deadline_mono_ns,
        "WORKER_BINDING_NOT_OWNED_OR_DEADLINE_CHANGED")
    require(set(parsed["processes"]) == {str(cli_binding["pid"]), str(binding["pid"])},
        "FOREIGN_SUPERVISOR_EMITTER")
    parent_result = parsed["processes"][str(cli_binding["pid"])]["result"]
    require(parent_result is not None and parent_result["status"] != "UNKNOWN" and
        parsed["processes"][str(binding["pid"])]["result"] is None,
        "FINAL_SUPERVISOR_UNKNOWN_OR_MISSING")
    for line in supervisor_raw.splitlines():
        frame = api.strict_json(line)
        require(popen_ns <= frame["mono_ns"] <= handoff.deadline_mono_ns,
            "SUPERVISOR_OUTSIDE_OPERATIONAL_DEADLINE")
        require(frame.get("outcome") != "ERROR", "SUPERVISOR_CUSTODY_ERROR")
    folder = Path(admission_value["evidence_parent"]) / admission_value["observation_name"]
    terminal_raw = small_file(folder / "terminal.json", limit=4194304)
    require(digest(terminal_raw) == parent_result["receipt_sha256"], "TERMINAL_RAW_DIGEST_MISMATCH")
    terminal = api.strict_json(terminal_raw)
    require(terminal.get("schema") == api.PROTOCOL and terminal.get("status") == parent_result["status"] and
        terminal.get("reason") == parent_result["reason"] and terminal.get("phase") == parent_result["phase"] and
        terminal.get("worker_binding") == binding and
        terminal.get("absolute_deadline_monotonic_ns") == worker["deadline_monotonic_ns"] and
        terminal.get("terminal_admission") == "REQUIRES_MATCHING_EXTERNAL_SUPERVISOR_RESULT_AND_LAUNCHER_CLOSURE" and
        terminal.get("protocol_error") is None and terminal.get("custody_errors") == [],
        "TERMINAL_PROVISIONAL_OR_INCONSISTENT")
    require(terminal.get("automatic_retry") is False and terminal.get("installed_result") == "NOT_RUN" and
        terminal.get("application_launched") is False and terminal.get("ui_effects_requested") is False and
        terminal.get("effects_absence") == "NOT_PROVEN" and terminal.get("human_acceptance") is False and
        terminal.get("release_authorized") is False, "TERMINAL_SCOPE_CHANGED")
    raw_streams = {}
    require(set(terminal.get("original_streams", {})) == {"stdout", "stderr"}, "WORKER_STREAMS_MISSING")
    for name, limit in (("stdout", api.MAX_STDOUT), ("stderr", api.MAX_STDERR)):
        raw = small_file(folder / ("worker." + name + ".original"), limit=limit)
        require(terminal["original_streams"][name] == {"bytes":len(raw), "sha256":digest(raw)},
            "WORKER_ORIGINAL_STREAM_CHANGED")
        raw_streams[name] = raw
    trace_context = {"target_pid":handoff.target.pid, "admission_sha256":admission_sha,
        "observer_sha256":OBSERVER_SHA, "declared_source_sha":SOURCE}
    trace = api.parse_trace(raw_streams["stdout"], trace_context)
    require(trace["terminal"] is not None and trace["pending_read"] is None and
        trace == terminal.get("trace") and terminal.get("partial_trace") is None and
        trace["terminal"]["state"] == terminal["status"] and
        terminal.get("observation_started") == trace["observation_started"], "TRACE_TERMINAL_JOIN_FAILED")
    expected_exit = {"OBSERVED_TARGET_SNAPSHOT":0, "NOT_OBSERVED":2}[terminal["status"]]
    require(terminal.get("worker_exit_code") == expected_exit and exit_code == expected_exit,
        "ACTUAL_CLI_OR_WORKER_EXIT_MISMATCH")
    require(cli_raw.endswith(b"\n") and cli_raw.count(b"\n") == 1, "FINAL_CLI_STDOUT_INCOMPLETE")
    cli = api.strict_json(cli_raw)
    require(cli == {"status":terminal["status"], "receipt":str(folder / "terminal.json"),
        "installed_result":"NOT_RUN", "automatic_retry":False}, "FINAL_CLI_OVERRIDES_PROVISIONAL_RESULT")
    require(owned_closure.get("status") == "OWNED_GROUPS_CLOSED" and owned_closure.get("groups_closed") is True and
        owned_closure.get("errors") == [] and owned_closure.get("unknown_group_members") == [] and
        owned_closure.get("root_exit_code") == exit_code, "OWNED_CLI_WORKER_CLOSURE_UNCONFIRMED")
    records = owned_closure.get("records", [])
    require(records and all(r.get("exit_observed_on_retained_pidfd") is True and r.get("uid") == os.getuid()
        for r in records), "RETAINED_PROCESS_HANDLES_NOT_CLOSED")
    actual_bindings = [r["binding"] for r in records]
    require(cli_binding in actual_bindings and binding in actual_bindings,
        "WORKER_WAS_NOT_OBSERVED_WITH_ACTUAL_OWNED_HANDLE")
    closure = capture["closure"]
    require(type(closure) is dict and set(closure) == {"cli", "worker", "group_ids", "groups_closed",
        "process_tree_closed", "escaped", "unidentified", "survivors", "completed_monotonic_ns"} and
        closure["cli"] == cli_binding and closure["worker"] == binding and
        closure["group_ids"] == sorted({b["process_group_id"] for b in actual_bindings}) and
        closure["groups_closed"] is True and closure["process_tree_closed"] is True and
        closure["escaped"] == closure["unidentified"] == closure["survivors"] == [] and
        type(closure["completed_monotonic_ns"]) is int and
        ended <= closure["completed_monotonic_ns"] <= now_ns <= handoff.cleanup_cap_mono_ns,
        "INDEPENDENT_TREE_CLOSURE_UNCONFIRMED")
    require(isinstance(subtree, RetainedObserverSubtree), "RETAINED_SUBTREE_REQUIRED")
    require(subtree.began_ns == popen_ns and subtree.returncode == exit_code and
        subtree.exit_seen_ns is not None and subtree.exit_seen_ns <= handoff.deadline_mono_ns,
        "SUBTREE_CLI_EXIT_OUTSIDE_OPERATIONAL_DEADLINE")
    subtree_value = subtree.validate_join(subtree_raw, cli_binding, join_cap)
    supervision = admission_value["external_supervision"]
    require(supervision["launcher_pid"] == subtree.supervisor.pid and
        supervision["launcher_start_ticks"] == subtree.supervisor.start_ticks,
        "ADMISSION_NOT_BOUND_TO_ACTUAL_SUPERVISOR")
    cli_reap = [r for r in subtree_value["reaps"] if r["identity"]["pid"] == cli_binding["pid"]]
    require(len(cli_reap) == 1 and
        cli_reap[0]["observed_monotonic_ns"] <= handoff.deadline_mono_ns and
        subtree_value["terminal"]["completed_monotonic_ns"] <= ended and
        subtree.wait_seen_ns <= now_ns,
        "SUBTREE_CLOSURE_OR_WAIT_TIME_MISMATCH")
    return {"status":"OBSERVATION_JOINED", "observation_status":terminal["status"],
        "reason":terminal["reason"], "exit_code":exit_code, "automatic_retry":False,
        "observer_sha256":OBSERVER_SHA, "admission_sha256":admission_sha,
        "terminal_sha256":digest(terminal_raw), "supervisor_sha256":digest(supervisor_raw),
        "cli_stdout_sha256":digest(cli_raw), "worker_original_streams":terminal["original_streams"],
        "subtree_status_sha256":digest(subtree_raw),
        "subtree_supervisor":subtree.expected_supervisor,
        "subtree_supervisor_actual_exit_code":subtree.wait_code,
        "cli_binding":cli_binding, "worker_binding":binding,
        "observer_deadline_monotonic_ns":handoff.deadline_mono_ns,
        "owned_closure":owned_closure, "independent_closure":closure,
        "installed_result":"NOT_RUN", "effects_absence":"NOT_PROVEN",
        "formal_acceptance":False, "release_authorized":False}

class Bootstrap:
    def __init__(self, spec):
        self.spec = spec
        t0 = spec.get("start_mono_ns")
        require(type(t0) is int and t0 >= 0, "INHERITED_START_REQUIRED")
        self.deadline, self.cleanup_cap = t0 + 24*NS, t0 + 32*NS
        self.root = Path(spec["parent"]) / ("a2-independent-" + spec["operation_id"])
        self.profile = self.root / "application"
        self.receipt = {"operation_id":spec["operation_id"], "status":"INTENDED",
            "original995":"UNKNOWN/FORBIDDEN", "original_reopen":"NOT_RUN",
            "operational_deadline_mono_ns":self.deadline, "cleanup_cap_mono_ns":self.cleanup_cap,
            "operational_seconds":24, "total_cap_seconds":32, "automatic_retry":False,
            "whole_experiment_read_only":False, "effects_absence":"NOT_PROVEN",
            "startup_effects":["SQLite/schema/migrations", "WAL/SHM", "CAS/maintenance",
                "runtime connection settings", "WebKit/GTK caches", "theme/localStorage",
                "isolated HOME/XDG/TMP files", "shutdown/checkpoint/sidecar writes"],
            "installed_result":"NOT_RUN", "human_acceptance":False, "release_authorized":False}
        self.used, self.owner, self.channel = False, None, None
        self.observer_subtrees = []

    def phase(self, event, phase, **detail):
        self.channel.emit(event, phase, **detail)

    def io(self, stage, label, fn):
        self.phase("IO_ENTER", stage, label=label)
        try:
            value = fn()
        except BaseException:
            self.phase("IO_RETURN", stage, label=label, outcome="ERROR")
            raise
        self.phase("IO_RETURN", stage, label=label, outcome="RETURNED")
        return value

    def write_new(self, path, raw, label):
        stream = self.io("open", label, lambda: path.open("xb"))
        try:
            self.io("write", label, lambda: stream.write(raw))
            self.io("flush", label, stream.flush)
            self.io("fsync", label, lambda: os.fsync(stream.fileno()))
        finally:
            self.io("close", label, stream.close)

    def prepare(self, app_run, scratch, seconds):
        require(not self.used, "LIFECYCLE_ALREADY_CONSUMED_NO_RETRY")
        self.used = True
        s = self.spec
        require(sys.platform == "linux", "LINUX_REQUIRED")
        require(s.get("status") == "A1_ADMITTED_INDEPENDENT_STARTUP" and
            s.get("observer_status") == "ADMITTED_NEW_READONLY_OBSERVATION", "A1_FINAL_RUN_ADMISSION_REQUIRED")
        require(s.get("source_sha") == SOURCE and s.get("semwright_sha") == SDK, "EXACT_995_SOURCE_REQUIRED")
        require(str(uuid.UUID(s["operation_id"])) == s["operation_id"], "CANONICAL_OPERATION_ID")
        require(seconds == 24 and s["start_mono_ns"] <= time.monotonic_ns() < self.deadline, "START_DEADLINE_INVALID")
        verify_outer(s, self.cleanup_cap)
        require_pidfd_support()
        self.channel = PhaseChannel(s["channels"]["phase"]["fd"], s["operation_id"])
        identities = []
        require(set(s["channels"]) == set(CAPTURE_CHANNEL_NAMES), "EXACT_CAPTURE_CHANNEL_SET_REQUIRED")
        import fcntl
        for name in CAPTURE_CHANNEL_NAMES:
            item = s["channels"][name]
            fd = item["fd"]
            require(type(fd) is int and fd >= 3, "OWNED_CAPTURE_DESCRIPTOR_REQUIRED")
            info = os.fstat(fd)
            require(not os.isatty(fd) and any(f(info.st_mode) for f in (stat.S_ISFIFO, stat.S_ISSOCK)), "INDEPENDENT_CAPTURE_PIPE_REQUIRED")
            identity = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
            require(list(identity) == item["identity"] and item.get("retention_reference"), "CAPTURE_DESCRIPTOR_BINDING")
            access = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
            require(access == (os.O_RDONLY if name == "capture_result" else os.O_WRONLY),
                "CAPTURE_DESCRIPTOR_DIRECTION")
            if name == "observer_subtree_status":
                require(stat.S_ISFIFO(info.st_mode), "SUBTREE_STATUS_PIPE_REQUIRED")
            identities.append(identity)
        require(len(set(identities)) == len(CAPTURE_CHANNEL_NAMES), "CAPTURE_CHANNELS_MUST_BE_DISTINCT")
        require(s.get("session_capture_reference"), "DBUS_XVFB_SESSION_CAPTURE_REQUIRED")
        for name in ("bootstrap", "probe", "observer", "python", "observer_subtree_core"):
            item = s["sources"][name]
            self.io("read", name, lambda item=item: checked_file(Path(item["path"]), item["sha256"], limit=536870912 if name == "python" else 131072))
        require(Path(s["sources"]["bootstrap"]["path"]) == Path(__file__).resolve(), "BOOTSTRAP_SOURCE_PATH_MISMATCH")
        require(s["sources"]["observer"]["sha256"] == OBSERVER_SHA, "EXACT_A6_OBSERVER_REQUIRED")
        self.observer_api = self.io("read", "observer65-held-parsers", lambda: load_observer65(s))
        self.subtree_core = self.io("read", "observer-subtree-held-source", lambda: load_subtree_core(s))
        capture = Path(s["capture_directory"])
        capture_info = capture.stat()
        require(capture == Path(s["parent"]) / ("a2-capture-" + s["operation_id"]) and
            capture.resolve(strict=True) == capture and capture.is_dir() and
            capture_info.st_uid == os.getuid() and not capture_info.st_mode & 0o022 and
            not capture.is_relative_to(self.profile) and not self.profile.is_relative_to(capture),
            "PRIVATE_SEPARATE_CAPTURE_NAMESPACE_REQUIRED")
        env_receipt = s["environment_receipt"]
        self.io("read", "environment", lambda: checked_file(Path(env_receipt["path"]), env_receipt["sha256"]))
        custody = s["package_custody_receipt"]
        self.io("read", "package-custody", lambda: checked_file(Path(custody["path"]), custody["sha256"]))
        self.bundle = Path(s["app_run"]).parent.resolve(strict=True)
        require(Path(app_run) == Path(s["app_run"]) and Path(scratch) == self.profile, "CALLER_PATH_BINDING")
        for name, (size, sha) in PINS.items():
            path = Path(s["app_run"] if name == "apprun" else s[name]).resolve(strict=True)
            if name != "appimage":
                require(path.is_relative_to(self.bundle), "BUNDLE_ESCAPE")
            sig = self.io("read", name, lambda path=path, size=size, sha=sha: checked_file(path, sha, size, limit=size))
            if name == "elf":
                self.elf_path, self.elf_signature = path, sig
        parent = Path(s["parent"])
        info = parent.stat()
        require(parent.resolve(strict=True) == parent and info.st_uid == os.getuid() and not info.st_mode & 0o022, "PRIVATE_CANONICAL_PARENT_REQUIRED")
        require(time.monotonic_ns() < self.deadline, "STARTUP_PREFLIGHT_DEADLINE")
        self.io("mkdir", "operation", lambda: self.root.mkdir(mode=0o700))
        self.io("mkdir", "application", lambda: self.profile.mkdir(mode=0o700))
        self.boot_id = (PROC / "sys/kernel/random/boot_id").read_text().strip()
        require(str(uuid.UUID(self.boot_id)) == self.boot_id, "BOOT_ID_REQUIRED")

    def environment(self, env):
        env = dict(env)
        for key, folder in (("HOME", "home"), ("TMPDIR", "tmp"), ("XDG_STATE_HOME", "state")):
            path = self.profile / folder
            self.io("mkdir", folder, lambda path=path: path.mkdir(mode=0o700))
            env[key] = str(path)
        env.pop("MOTIONWRIGHT_SEMWRIGHT_CONNECTION", None)
        for key in ENV_KEYS:
            path = Path(env[key])
            require(path.is_dir() and path.resolve(strict=True) == path and path.is_relative_to(self.profile), "EXCLUSIVE_PROFILE_BINDING")
        require(env.get("DISPLAY") and env.get("DBUS_SESSION_BUS_ADDRESS"), "ADMITTED_GUI_SESSION_REQUIRED")
        self.env = env
        return env

    @contextmanager
    def open_log(self, path):
        stream = self.io("open", "app-log", lambda: path.open("xb"))
        try:
            yield stream
        finally:
            self.io("close", "app-log", stream.close)

    def intent(self, env):
        require(time.monotonic_ns() < self.deadline, "NO_LAUNCH_AFTER_DEADLINE")
        verify_outer(self.spec, self.cleanup_cap)
        me = process_stamp(os.getpid())
        self.receipt.update(launcher_pid=me.pid, launcher_start_ticks=me.start_ticks,
            source_sha=SOURCE, semwright_sha=SDK, boot_id=self.boot_id,
            application_namespace=str(self.profile), process_environment={k:env[k] for k in ENV_KEYS},
            bus_environment_sha256={k:digest(env[k].encode()) if k in env else None for k in BUS_KEYS},
            appimage_sha256=PINS["appimage"][1], elf_sha256=PINS["elf"][1], apprun_sha256=PINS["apprun"][1],
            environment_receipt=self.spec["environment_receipt"], sources=self.spec["sources"],
            session_capture_reference=self.spec["session_capture_reference"], channels=self.spec["channels"])
        raw = (json.dumps(self.receipt, sort_keys=True) + "\n").encode()
        self.write_new(self.root / "intent.json", raw, "intent")
        self.intent_sha = digest(raw)
        require(time.monotonic_ns() < self.deadline, "NO_LAUNCH_AFTER_INTENT_IO_DEADLINE")
        self.phase("ENTER", "app-Popen")
    def target(self):
        candidates = []
        for item in owned_tree(self.owner):
            if item.group != self.owner.group or item.session != self.owner.session:
                continue
            path = PROC / str(item.pid)
            try:
                if os.readlink(path / "exe") != str(self.elf_path):
                    continue
                sig = checked_file(path / "exe", PINS["elf"][1], PINS["elf"][0],
                    limit=PINS["elf"][0], proc_link=True)
                require(sig == self.elf_signature, "MAPPED_ELF_IDENTITY_MISMATCH")
                with (path / "environ").open("rb") as stream:
                    raw = stream.read(131073)
                require(len(raw) <= 131072, "TARGET_ENVIRONMENT_LIMIT")
                env = {}
                for pair in raw.rstrip(b"\0").split(b"\0"):
                    key, value = pair.split(b"=", 1)
                    require(key not in env, "TARGET_DUPLICATE_ENVIRONMENT")
                    env[key] = value
                require(b"MOTIONWRIGHT_SEMWRIGHT_CONNECTION" not in env, "INHERITED_SEMWRIGHT_CONNECTION")
                for key in ENV_KEYS + BUS_KEYS:
                    expected = self.env[key].encode() if key in self.env else None
                    require(env.get(key.encode()) == expected, "TARGET_ENVIRONMENT_CHANGED")
                require(same_process(item), "TARGET_CHANGED_DURING_BINDING")
                candidates.append(item)
            except (FileNotFoundError, ProcessLookupError):
                continue
        require(len(candidates) <= 1, "AMBIGUOUS_OWNED_EXACT_ELF")
        return candidates[0] if candidates else None

    def stop_owned(self, child, root, *, cap, term_seconds, kill_seconds):
        def expired():
            return time.monotonic_ns() >= cap
        def unknown(reason):
            return {"status":"STOP_UNCONFIRMED", "reason":reason,
                "root_exit_code":child.returncode, "process_tree_closure":"NOT_PROVEN"}
        if expired():
            return unknown("FIXED_CLEANUP_CAP_EXPIRED")
        if root is None:
            return unknown("NO_VERIFIED_PROCESS_STAMP")
        if child.poll() is not None:
            return {"status":"EXIT_OBSERVED", "root_exit_code":child.returncode,
                "cleanup_action":"ROOT_ALREADY_REAPED_NO_SIGNALS",
                "process_tree_closure":"NOT_PROVEN"}
        handles = []
        try:
            if expired():
                return unknown("FIXED_CLEANUP_CAP_EXPIRED")
            records = owned_tree(root)
            if expired():
                return unknown("FIXED_CLEANUP_CAP_EXPIRED")
            for item in records:
                if expired():
                    return unknown("FIXED_CLEANUP_CAP_EXPIRED")
                try:
                    fd = bind_pidfd(item)
                except ProcessLookupError:
                    continue
                handles.append((item, fd))
            for sig, seconds in ((signal.SIGTERM, term_seconds), (signal.SIGKILL, kill_seconds)):
                if expired():
                    return unknown("FIXED_CLEANUP_CAP_EXPIRED")
                for item, fd in reversed(handles):
                    if expired():
                        return unknown("FIXED_CLEANUP_CAP_EXPIRED")
                    if not pidfd_exited(fd):
                        try:
                            signal.pidfd_send_signal(fd, sig, None, 0)
                        except ProcessLookupError:
                            pass
                end = min(cap, time.monotonic_ns() + seconds*NS)
                while time.monotonic_ns() < end:
                    child.poll()
                    if all(pidfd_exited(fd) for _, fd in handles):
                        break
                    time.sleep(min(0.02, max(0, (end-time.monotonic_ns())/NS)))
                if expired():
                    return unknown("FIXED_CLEANUP_CAP_EXPIRED")
                if all(pidfd_exited(fd) for _, fd in handles):
                    break
            if expired():
                return unknown("FIXED_CLEANUP_CAP_EXPIRED")
            child.poll()
            live = [item.pid for item, fd in handles if not pidfd_exited(fd)]
            return {"status":"EXIT_OBSERVED" if child.returncode is not None and not live else "STOP_UNCONFIRMED",
                "root_exit_code":child.returncode, "live_known_owned_pids":live,
                "process_tree_closure":"NOT_PROVEN"}
        except (OSError, Refused, ValueError) as error:
            return unknown(type(error).__name__ + ":" + str(error)[:120])
        finally:
            for _, fd in handles:
                os.close(fd)

    def write_observer_admission(self, handoff, observer_parent):
        s, observer = self.spec, self.spec["observer"]
        require(time.monotonic_ns() < handoff.deadline_mono_ns <= self.deadline and
            handoff.cleanup_cap_mono_ns == self.cleanup_cap, "NO_ATTACH_AFTER_INHERITED_DEADLINE")
        api = self.observer_api
        me = observer_parent
        target = {"pid":handoff.target.pid, "start_ticks":handoff.target.start_ticks,
            "executable":handoff.executable, "sha256":handoff.executable_sha256, "source_sha":SOURCE}
        binding = {"operation_id":s["operation_id"], "target":target,
            "target_uid":handoff.target.uid, "target_group":handoff.target.group,
            "target_session":handoff.target.session, "boot_id":handoff.boot_id,
            "app_launcher_pid":handoff.launcher.pid,
            "app_launcher_start_ticks":handoff.launcher.start_ticks,
            "launcher_pid":me.pid, "launcher_start_ticks":me.start_ticks,
            "deadline_mono_ns":self.deadline, "observer_deadline_mono_ns":handoff.deadline_mono_ns,
            "cleanup_cap_mono_ns":self.cleanup_cap, "intent_sha256":self.intent_sha,
            "environment_receipt":s["environment_receipt"],
            "whole_experiment_read_only":False, "installed_result":"NOT_RUN"}
        raw_binding = (json.dumps(binding, sort_keys=True) + "\n").encode()
        binding_path = self.root / "live-binding.json"
        self.write_new(binding_path, raw_binding, "live-binding")
        require(observer.get("independent_capture") is True and
            observer.get("worker_group_contract") == WORKER_GROUP_CONTRACT,
            "A1_INDEPENDENT_CAPTURE_AND_GROUP_CONTRACT_REQUIRED")
        admission = {
            "schema":"motionwright.private.readonly-atspi-admission/2",
            "authority":"A1", "status":s["observer_status"],
            "reference":observer["admission_reference"], "observer_sha256":OBSERVER_SHA,
            "target":target, "application_namespace":handoff.profile,
            "environment_receipt":s["environment_receipt"]["path"],
            "environment_sha256":s["environment_receipt"]["sha256"],
            "process_environment":handoff.process_environment,
            "bus_environment_sha256":handoff.bus_environment_sha256,
            "evidence_parent":observer["evidence_parent"],
            "observation_name":observer["observation_name"], "seconds":handoff.worker_seconds,
            "external_supervision":{
                "status":"A1_EXTERNAL_WATCHDOG_AND_CAPTURE_ADMITTED",
                "launcher_pid":me.pid, "launcher_start_ticks":me.start_ticks,
                "watchdog_seconds":handoff.watchdog_seconds,
                "receipt_path":str(binding_path), "receipt_sha256":digest(raw_binding),
                "retention_reference":s["channels"]["observer_stderr"]["retention_reference"],
                "channel":"stderr", "absolute_deadline_monotonic_ns":handoff.deadline_mono_ns,
                "worker_group_contract":WORKER_GROUP_CONTRACT, "independent_capture":True}}
        require(observer.get("external_supervision_status") == admission["external_supervision"]["status"],
            "A1_OBSERVER_EXTERNAL_SUPERVISION_ADMISSION_REQUIRED")
        admission_raw = (json.dumps(admission, sort_keys=True)+"\n").encode()
        api.admission(admission_raw)
        admission_path = self.root / "observer-admission.json"
        self.write_new(admission_path, admission_raw, "observer-admission")
        return admission_raw

    def observe_once(self, handoff):
        s, api, core = self.spec, self.observer_api, self.subtree_core
        require(time.monotonic_ns() < handoff.deadline_mono_ns <= self.deadline and
            handoff.cleanup_cap_mono_ns == self.cleanup_cap, "NO_ATTACH_AFTER_INHERITED_DEADLINE")
        me = process_stamp(os.getpid())
        channel = CaptureReceiptChannel(s["channels"]["capture_result"]["fd"])
        cap = min(self.cleanup_cap, self.deadline + 2*NS)
        closed_copies, child, owned, joined = set(), None, None, False
        result = {"status":"UNKNOWN", "automatic_retry":False, "installed_result":"NOT_RUN",
            "observer_sha256":OBSERVER_SHA, "observer_deadline_monotonic_ns":handoff.deadline_mono_ns}
        def close_copy(name):
            if name not in closed_copies:
                closed_copies.add(name)
                os.close(s["channels"][name]["fd"])
        try:
            if time.monotonic_ns() + (handoff.worker_seconds+2)*NS > handoff.deadline_mono_ns:
                result.update(status="NOT_RUN_NO_BUDGET", reason="PRELAUNCH_IO_CONSUMED_RESERVED_BUDGET")
                return result
            require(same_process(handoff.launcher) and same_process(handoff.target),
                "TARGET_LOST_BEFORE_ATTACH")
            require((PROC / "sys/kernel/random/boot_id").read_text().strip() == self.boot_id,
                "BOOT_CHANGED")
            self.phase("ENTER", "observer-supervisor-start", target_pid=handoff.target.pid,
                target_start_ticks=handoff.target.start_ticks, deadline_mono_ns=handoff.deadline_mono_ns)
            began_ns = time.monotonic_ns()
            if began_ns + (handoff.worker_seconds+2)*NS > handoff.deadline_mono_ns:
                result.update(status="NOT_RUN_NO_BUDGET", reason="PRELAUNCH_PHASE_CONSUMED_RESERVED_BUDGET")
                return result
            command = [s["sources"]["python"]["path"], "-I", "-B",
                s["sources"]["observer"]["path"], "--admission", str(self.root/"observer-admission.json")]
            env = {k:v for k,v in os.environ.items() if k not in
                ("LD_PRELOAD", "PYTHONPATH", "PYTHONHOME", "GI_TYPELIB_PATH", "MOTIONWRIGHT_SEMWRIGHT_CONNECTION")}
            try:
                handle = core.start_supervisor(command, env,
                    s["channels"]["observer_stdout"]["fd"], s["channels"]["observer_stderr"]["fd"],
                    core.Budget(s["start_mono_ns"]),
                    mirror_fd=s["channels"]["observer_subtree_status"]["fd"],
                    protected_fds=(s["channels"]["phase"]["fd"],s["channels"]["capture_result"]["fd"]))
            except core.RetainedChildError as error:
                unresolved = types.SimpleNamespace(pid=error.pid, start_ticks=None,
                    pidfd=error.pidfd, status_fd=error.status_fd, ack_fd=-1, aborted=True)
                child = RetainedObserverSubtree(core, unresolved, me, s, began_ns, self.phase)
                self.observer_subtrees.append(child)
                for name in ("observer_stdout", "observer_stderr", "observer_subtree_status"):
                    close_copy(name)
                raise
            child = RetainedObserverSubtree(core, handle, me, s, began_ns, self.phase)
            self.observer_subtrees.append(child)
            for name in ("observer_stdout", "observer_stderr", "observer_subtree_status"):
                close_copy(name)
            self.phase("RETURN", "observer-supervisor-start", returned_pid=handle.pid)
            owner, supervisor = child.bind(handoff.deadline_mono_ns)
            owned = OwnedObserverProcesses(child, owner, supervisor,
                {handoff.target.pid, handoff.launcher.pid, me.pid, supervisor.pid,
                    s["external_parent"]["pid"]})
            admission_raw = self.write_observer_admission(handoff, supervisor)
            result["admission_sha256"] = digest(admission_raw)
            require(same_process(handoff.launcher) and same_process(handoff.target),
                "TARGET_LOST_BEFORE_ATTACH")
            require((PROC / "sys/kernel/random/boot_id").read_text().strip() == self.boot_id,
                "BOOT_CHANGED")
            child.admit(handoff)
            while time.monotonic_ns() < handoff.deadline_mono_ns:
                owned.sample(handoff.deadline_mono_ns)
                channel.poll()
                code = child.poll()
                if code is not None:
                    result["actual_cli_exit_code"] = code
                    break
                time.sleep(min(.01, max(0,(handoff.deadline_mono_ns-time.monotonic_ns())/NS)))
            require(child.exit_seen_ns is not None and child.exit_seen_ns <= handoff.deadline_mono_ns,
                "EXTERNAL_WHOLE_CLI_DEADLINE")
            result["owned_closure"] = owned.close(cap)
            owned = None
            child.finish(cap)
            while not channel.eof and time.monotonic_ns() < cap:
                channel.poll()
                if not channel.eof:
                    time.sleep(min(.01, max(0,(cap-time.monotonic_ns())/NS)))
            require(time.monotonic_ns() < cap, "CAPTURE_JOIN_FIXED_CAP_EXPIRED")
            capture = channel.value()
            verify_outer(s, self.cleanup_cap)
            result = self.io("read", "observer65-originals-join", lambda:
                join_observer65(api, s, handoff, admission_raw, binding_of(owner,supervisor),
                    began_ns, child.returncode, capture, result["owned_closure"],
                    now_ns=time.monotonic_ns(), subtree=child))
            require(time.monotonic_ns() <= cap, "CAPTURE_JOIN_IO_EXCEEDED_FIXED_CAP")
            joined = True
            return result
        except Exception as error:
            result.update(status="UNKNOWN", reason="OBSERVER_LIFECYCLE_"+type(error).__name__,
                automatic_retry=False, installed_result="NOT_RUN")
            return result
        finally:
            if owned is not None:
                try:
                    result["owned_closure"] = owned.close(cap)
                except Exception as error:
                    result.update(status="UNKNOWN", reason="OWNED_CLOSURE_"+type(error).__name__)
            if child is not None:
                if not joined:
                    result["subtree_failure_cleanup"] = child.settle_failed(cap)
                if child.release_after_wait():
                    self.observer_subtrees.remove(child)
                elif child.wait_seen_ns is not None:
                    result.update(status="UNKNOWN", reason="SUBTREE_DESCRIPTOR_CLOSE_UNCONFIRMED")
            for name in ("observer_stdout", "observer_stderr", "observer_subtree_status", "capture_result"):
                try:
                    close_copy(name)
                except OSError as error:
                    result.update(status="UNKNOWN", reason="CAPTURE_DESCRIPTOR_CLOSE_UNCONFIRMED")
                    result.setdefault("descriptor_close_errors", []).append(type(error).__name__)

    def on_alive(self, child, env):
        try:
            self.phase("RETURN", "app-Popen", returned_pid=child.pid)
            self.owner = self.io("read", "app-process-stamp", lambda: process_stamp(child.pid))
            require(self.owner.uid == os.getuid() and self.owner.group == child.pid and
                self.owner.session == child.pid and self.owner.state != "Z", "APP_PROCESS_OWNERSHIP")
            self.receipt.update(status="PROCESS_LIVE", app_launcher_pid=self.owner.pid,
                app_launcher_start_ticks=self.owner.start_ticks)
            target = None
            while time.monotonic_ns() < self.deadline:
                require(child.poll() is None and same_process(self.owner), "APP_EXITED_DURING_STARTUP")
                target = self.target()
                if target is not None:
                    break
                time.sleep(min(0.02, max(0, (self.deadline-time.monotonic_ns())/NS)))
            if target is None:
                self.receipt.update(status="STARTUP_DEADLINE", observer={"status":"NOT_RUN"})
                return self.receipt
            budget = observer_budget(self.deadline, time.monotonic_ns())
            if budget is None:
                self.receipt.update(status="PROCESS_LIVE", observer={"status":"NOT_RUN_NO_BUDGET"})
                return self.receipt
            handoff = AliveHandoff(self.spec["operation_id"], self.owner, target,
                str(self.elf_path), PINS["elf"][1], budget["deadline"], self.cleanup_cap,
                budget["worker"], budget["watchdog"], str(self.profile), {k:env[k] for k in ENV_KEYS},
                {k:digest(env[k].encode()) if k in env else None for k in BUS_KEYS}, self.boot_id)
            self.receipt["target"] = {"pid":target.pid, "start_ticks":target.start_ticks,
                "executable":str(self.elf_path), "sha256":PINS["elf"][1]}
            self.receipt["observer"] = self.observe_once(handoff)
            self.receipt["target_live_after_observer"] = same_process(target)
            return self.receipt
        except Exception as error:
            self.receipt.update(status="STARTUP_OR_ATTACH_FAILED", error=str(error)[:256])
            return self.receipt

    def finish(self, child):
        try:
            self.phase("ENTER", "app-cleanup", cleanup_cap_mono_ns=self.cleanup_cap)
        except Exception:
            self.receipt["phase_channel_incomplete"] = True
        self.receipt["cleanup"] = self.stop_owned(child, self.owner,
            cap=self.cleanup_cap, term_seconds=4, kill_seconds=4)
        self.receipt["lifecycle_end_mono_ns"] = time.monotonic_ns()
        self.receipt["stable_database_snapshot_claimed"] = False
        try:
            self.phase("RETURN", "app-cleanup", status=self.receipt["cleanup"]["status"])
        except Exception:
            self.receipt["phase_channel_incomplete"] = True


def run(spec):
    require(spec.get("status") == "A1_ADMITTED_INDEPENDENT_STARTUP", "A1_FINAL_RUN_ADMISSION_REQUIRED")
    bootstrap = Bootstrap(spec)
    verify_outer(spec, bootstrap.cleanup_cap)
    item = spec["sources"]["probe"]
    raw = small_file(Path(item["path"]))
    require(digest(raw) == item["sha256"], "PROBE_SOURCE_CHANGED")
    module = types.ModuleType("_a2_bound_probe")
    module.__file__ = item["path"]
    exec(compile(raw, item["path"], "exec"), module.__dict__)
    try:
        result = module.run_probe(Path(spec["app_run"]), bootstrap.profile,
            seconds=24, independent=bootstrap)
        if time.monotonic_ns() < bootstrap.cleanup_cap:
            bootstrap.write_new(bootstrap.root / "terminal.json",
                (json.dumps(result, sort_keys=True)+"\n").encode(), "terminal")
        else:
            result["terminal_persistence"] = "NOT_RUN_NO_BUDGET"
        return result
    except Exception as error:
        bootstrap.receipt.update(status="LIFECYCLE_INCOMPLETE", error=str(error)[:256])
        raise


def main():
    parser = argparse.ArgumentParser(description="Unadmitted independent995 startup proposal; no original installed attempt")
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    spec = strict_json(small_file(args.plan))
    result = run(spec)
    print(json.dumps(result, sort_keys=True))
    observer = result.get("observer", {})
    complete = (result.get("status") == "PROCESS_LIVE" and
        observer.get("status") == "OBSERVATION_JOINED" and
        observer.get("observation_status") == "OBSERVED_TARGET_SNAPSHOT" and observer.get("exit_code") == 0 and
        observer.get("owned_closure", {}).get("status") == "OWNED_GROUPS_CLOSED" and
        observer.get("independent_closure", {}).get("process_tree_closed") is True and
        result.get("cleanup", {}).get("status") == "EXIT_OBSERVED" and
        not result.get("phase_channel_incomplete") and
        time.monotonic_ns() <= result["cleanup_cap_mono_ns"])
    return 0 if complete else 2

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Refused, OSError, ValueError, KeyError) as error:
        print(json.dumps({"status":"REFUSED_OR_INCOMPLETE", "reason":str(error),
            "automatic_retry":False, "installed_result":"NOT_RUN", "release_authorized":False}))
        raise SystemExit(2)
