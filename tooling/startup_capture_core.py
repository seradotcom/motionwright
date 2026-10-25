"""Bounded observer capture primitives with a dedicated Linux subreaper.

This module supplies no installed-application launch authority. Integration must
authenticate a separate control channel, retain the observer pidfd while its
exec barrier is closed, and review the exact source before admitting execution.
Procfs is used for identities and cleanup, never as a tree-closure oracle.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import select
import signal
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

OPERATION_NS = 24_000_000_000
CLEANUP_NS = 32_000_000_000
STDOUT_LIMIT = 16_384
SUPERVISOR_LIMIT = 16_777_216
STATUS_LIMIT = 131_072
FRAME_LIMIT = 4096
SCHEMA = "motionwright.observer-subtree/1"


class CaptureError(RuntimeError):
    pass


class RetainedChildError(CaptureError):
    """Retains an unresolved direct child; never implies that cleanup succeeded."""
    def __init__(self, message, pid, status_fd, pidfd):
        super().__init__(message)
        self.pid = pid
        self.status_fd = status_fd
        self.pidfd = pidfd
        self.process_tree_closed = False


class SupervisorStartError(RetainedChildError):
    pass


class WriterStartError(RetainedChildError):
    pass


@dataclass(frozen=True)
class Budget:
    started_ns: int

    def __post_init__(self):
        if type(self.started_ns) is not int or self.started_ns < 0:
            raise CaptureError("invalid inherited monotonic origin")
        if self.started_ns > time.monotonic_ns():
            raise CaptureError("inherited monotonic origin is in the future")

    @property
    def operation_ns(self):
        return self.started_ns + OPERATION_NS

    @property
    def cleanup_ns(self):
        return self.started_ns + CLEANUP_NS

    @property
    def join_ns(self):
        return min(self.cleanup_ns, self.operation_ns + 2_000_000_000)


@dataclass
class BoundedStream:
    limit: int
    data: bytearray = field(default_factory=bytearray)
    seen: int = 0
    eof: bool = False
    truncated: bool = False

    def __post_init__(self):
        if type(self.limit) is not int or self.limit < 1:
            raise CaptureError("invalid stream bound")

    def feed(self, chunk):
        if not isinstance(chunk, bytes) or self.eof:
            raise CaptureError("invalid bytes or bytes after EOF")
        self.seen += len(chunk)
        left = max(0, self.limit - len(self.data))
        self.data.extend(chunk[:left])
        self.truncated |= len(chunk) > left

    def finish(self):
        if self.eof:
            raise CaptureError("duplicate EOF")
        self.eof = True

    def require_complete(self):
        if not self.eof or self.truncated:
            raise CaptureError("stream is incomplete or truncated")

    def metadata(self, path):
        return {"path": str(path), "bytes": len(self.data),
                "sha256": hashlib.sha256(self.data).hexdigest(),
                "eof": self.eof, "truncated": self.truncated}


def decode_frame(raw, *, eof, limit=STATUS_LIMIT):
    if (not eof or not raw or len(raw) > limit or not raw.endswith(b"\n")
            or raw.count(b"\n") != 1):
        raise CaptureError("invalid framing, EOF, or byte budget")

    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise CaptureError("duplicate JSON key")
            output[key] = value
        return output

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise CaptureError("nonfinite JSON value")
        return result

    try:
        result = json.loads(raw[:-1].decode("utf-8"), object_pairs_hook=pairs,
                            parse_float=finite_float,
                            parse_constant=lambda _: (_ for _ in ()).throw(
                                CaptureError("nonfinite JSON value")))
    except (UnicodeError, ValueError) as exc:
        raise CaptureError("invalid JSON") from exc
    if not isinstance(result, dict):
        raise CaptureError("JSON object required")
    return result


def proc_identity(pid):
    """Identity only; the caller needs a retained pidfd to prevent PID reuse."""
    # comm may contain Unicode, spaces or ')'; it is opaque and irrelevant to
    # identity. Numeric fields after its final ')' remain ASCII kernel fields.
    raw = Path(f"/proc/{pid}/stat").read_bytes()
    right = raw.rfind(b")")
    if right < 0 or int(raw[:raw.find(b" ")]) != pid:
        raise CaptureError("invalid proc identity")
    fields = raw[right + 2:].split()
    if len(fields) < 20:
        raise CaptureError("short proc identity")
    return {"pid": pid, "start_ticks": int(fields[19]),
            "parent_pid": int(fields[1]), "session_id": int(fields[3]),
            "process_group_id": int(fields[2])}


def _linux():
    if (not sys.platform.startswith("linux") or not hasattr(os, "fork")
            or not hasattr(os, "pidfd_open")
            or not hasattr(signal, "pidfd_send_signal")):
        raise CaptureError("Linux fork, pidfd and procfs interfaces required")


def _close_except(keep):
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        if fd > 2 and fd not in keep:
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise


def _record(fd, value):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _write_record(fd, raw)


def _write_record(fd, raw):
    if len(raw) > FRAME_LIMIT or os.write(fd, raw) != len(raw):
        raise CaptureError("non-atomic or excessive status record")


def _mirror_descriptor(fd, protected_fds):
    import fcntl
    if type(fd) is not int or fd < 0:
        raise CaptureError("invalid independent status descriptor")
    info = os.fstat(fd)
    if not stat.S_ISFIFO(info.st_mode):
        raise CaptureError("independent status must be a pipe, not a filesystem sink")
    if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_WRONLY:
        raise CaptureError("independent status descriptor must be write-only")
    if os.fpathconf(fd, "PC_PIPE_BUF") < FRAME_LIMIT:
        raise CaptureError("independent status atomic-write budget is insufficient")
    identity = (info.st_dev, info.st_ino)
    for protected in protected_fds:
        other = os.fstat(protected)
        if protected == fd or identity == (other.st_dev, other.st_ino):
            raise CaptureError("independent status aliases an existing channel")


def _wait_byte(fd, expected, deadline_ns):
    os.set_blocking(fd, False)
    while time.monotonic_ns() < deadline_ns:
        left = max(0, (deadline_ns - time.monotonic_ns()) / 1e9)
        ready, _, _ = select.select([fd], [], [], min(.02, left))
        if ready:
            if time.monotonic_ns() >= deadline_ns:
                raise CaptureError("acknowledgement deadline")
            if os.read(fd, 2) != expected:
                raise CaptureError("invalid or missing acknowledgement")
            if time.monotonic_ns() >= deadline_ns:
                raise CaptureError("acknowledgement deadline")
            return
    raise CaptureError("acknowledgement deadline")


def _subreaper():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    actual = ctypes.c_int(0)
    if libc.prctl(37, ctypes.byref(actual), 0, 0, 0) != 0 or actual.value != 1:
        raise CaptureError("subreaper activation not observed")


def _signal_children(sig, deadline_ns):
    """Only owned direct children; closure is established by kernel wait."""
    if time.monotonic_ns() >= deadline_ns:
        raise CaptureError("cleanup deadline")
    with Path(f"/proc/self/task/{os.getpid()}/children").open("rb") as source:
        raw = source.read(65_537)
    if len(raw) > 65_536:
        raise CaptureError("owned child roster exceeds finite budget")
    children = raw.split()
    for value in children:
        if time.monotonic_ns() >= deadline_ns:
            raise CaptureError("cleanup deadline")
        pid = int(value)
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            continue
        try:
            if proc_identity(pid)["parent_pid"] != os.getpid():
                raise CaptureError("child ownership changed")
            if time.monotonic_ns() >= deadline_ns:
                raise CaptureError("cleanup deadline")
            signal.pidfd_send_signal(fd, sig)
        except (ProcessLookupError, FileNotFoundError):
            pass
        finally:
            os.close(fd)


def _run_supervisor(argv, env, out_fd, err_fd, status_fd, ack_fd, budget, mirror_fd=None):
    # This dedicated process launches exactly one subtree. In particular no
    # application, capture writer, or unrelated helper may be its child.
    os.set_blocking(status_fd, False)
    if mirror_fd is not None:
        os.set_blocking(mirror_fd, False)
    sequence = 0
    cli_pid = None
    cli_exit = None
    errors = []
    opened = set()

    def emit(kind, **fields):
        nonlocal sequence
        sequence += 1
        value = {"schema": SCHEMA, "sequence": sequence, "kind": kind, **fields}
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        # Serialize exactly once. Neither sink is reconstructed from the
        # other. A short/full/closed sink is an error, never retried as proof.
        _write_record(status_fd, raw)
        if mirror_fd is not None:
            _write_record(mirror_fd, raw)

    try:
        _subreaper()
        own = proc_identity(os.getpid())
        own["source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        ready_r, ready_w = os.pipe()
        gate_r, gate_w = os.pipe()
        opened.update((ready_r, ready_w, gate_r, gate_w))
        cli_pid = os.fork()
        if cli_pid == 0:
            try:
                os.setsid()
                null = os.open("/dev/null", os.O_RDONLY)
                os.dup2(null, 0)
                os.dup2(out_fd, 1)
                os.dup2(err_fd, 2)
                _close_except({ready_w, gate_r})
                os.write(ready_w, b"R")
                os.close(ready_w)
                if os.read(gate_r, 2) != b"G":
                    os._exit(125)
                os.close(gate_r)
                if time.monotonic_ns() >= budget.operation_ns:
                    os._exit(125)
                os.execvpe(argv[0], argv, env)
            except BaseException:
                os._exit(126)
        for fd in (ready_w, gate_r):
            os.close(fd)
            opened.remove(fd)
        os.close(out_fd)
        os.close(err_fd)
        _wait_byte(ready_r, b"R", budget.operation_ns)
        os.close(ready_r)
        opened.remove(ready_r)
        cli = proc_identity(cli_pid)
        cli["parent_start_ticks"] = own["start_ticks"]
        if cli["parent_pid"] != own["pid"] or cli["session_id"] != cli_pid:
            raise CaptureError("CLI binding mismatch")
        emit("LAUNCH_BOUND", supervisor=own, cli=cli,
             observed_monotonic_ns=time.monotonic_ns())
        # Until the owner authenticates binding and retains its pidfd the CLI
        # cannot exec, fork or otherwise execute observer code.
        _wait_byte(ack_fd, b"G", budget.operation_ns)
        if time.monotonic_ns() >= budget.operation_ns:
            raise CaptureError("pre-exec inherited deadline expired")
        os.write(gate_w, b"G")
        os.close(gate_w)
        opened.remove(gate_w)
    except BaseException as exc:
        errors.append(f"launch:{type(exc).__name__}:{exc}")
    finally:
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass

    os.set_blocking(ack_fd, False)

    kill_started = None
    closed = False
    while time.monotonic_ns() < budget.join_ns:
        now = time.monotonic_ns()
        try:
            control = os.read(ack_fd, 2)
        except BlockingIOError:
            control = None
        if control is not None:
            if len(errors) < 8:
                errors.append("owner requested abort" if control == b"X" else "owner control channel closed or invalid")
        if now >= budget.operation_ns and "inherited operation deadline expired" not in errors:
            errors.append("inherited operation deadline expired")
        if errors or now >= budget.operation_ns:
            if kill_started is None:
                kill_started = now
            sig = signal.SIGTERM if now - kill_started < 100_000_000 else signal.SIGKILL
            try:
                _signal_children(sig, budget.join_ns)
            except BaseException as exc:
                if len(errors) < 8:
                    errors.append(f"cleanup:{type(exc).__name__}:{exc}")
        try:
            result = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError as exc:
            if exc.errno == errno.ECHILD and cli_pid is not None and cli_exit is not None:
                closed = True
            else:
                errors.append("wait did not establish ECHILD after CLI terminal")
            break
        if result is not None:
            try:
                identity = proc_identity(result.si_pid)
                if identity["parent_pid"] != os.getpid():
                    raise CaptureError("wait identity not owned")
                reaped, status = os.waitpid(result.si_pid, 0)
                if reaped != result.si_pid:
                    raise CaptureError("wait identity mismatch")
                code = os.waitstatus_to_exitcode(status)
                if reaped == cli_pid:
                    cli_exit = code
                emit("REAP", identity=identity, exit_code=code,
                     observed_monotonic_ns=time.monotonic_ns())
            except BaseException as exc:
                if len(errors) < 8:
                    errors.append(f"reap:{type(exc).__name__}:{exc}")
        else:
            time.sleep(.005)
    if not closed:
        errors.append("no bounded terminal ECHILD proof")
    try:
        emit("TERMINAL", process_tree_closed=closed,
             closure_mechanism="PR_SET_CHILD_SUBREAPER+waitid_ECHILD" if closed else None,
             cli_exit_code=cli_exit, errors=errors,
             completed_monotonic_ns=time.monotonic_ns())
    except BaseException:
        os._exit(124)
    os._exit(0 if closed and not errors else 124)


@dataclass
class SupervisorHandle:
    pid: int
    start_ticks: int
    pidfd: int
    status_fd: int
    ack_fd: int
    admitted: bool = False
    aborted: bool = False

    def admit_exec(self):
        if self.ack_fd < 0 or self.admitted or self.aborted:
            raise CaptureError("launch already acknowledged")
        if os.write(self.ack_fd, b"G") != 1:
            raise CaptureError("short launch acknowledgement")
        self.admitted = True

    def abort(self):
        if self.ack_fd < 0 or self.aborted:
            return
        # EOF is an authenticated failure signal for this exclusive pipe.
        # Closing once is idempotent and cannot block behind a full pipe.
        os.close(self.ack_fd)
        self.ack_fd = -1
        self.aborted = True


def start_supervisor(argv, env, out_fd, err_fd, budget, *, mirror_fd=None, protected_fds=()):
    """Start paused; optionally mirror raw status to an independent fifth pipe.

    The caller authenticates mirror_fd and passes phase/result descriptors in
    protected_fds. This verifies direction/type/aliasing, not launch authority.
    The caller owns and closes its inherited mirror writer copy after fork.
    """
    _linux()
    if not argv or any(not isinstance(x, str) or "\0" in x for x in argv):
        raise CaptureError("invalid argv")
    if time.monotonic_ns() >= budget.operation_ns:
        raise CaptureError("inherited operation budget expired")
    if mirror_fd is not None:
        _mirror_descriptor(mirror_fd, (out_fd, err_fd, *protected_fds))
    created = []
    try:
        status_r, status_w = os.pipe()
        created.extend((status_r, status_w))
        ack_r, ack_w = os.pipe()
        created.extend((ack_r, ack_w))
        if mirror_fd is not None:
            _mirror_descriptor(mirror_fd, created)
        pid = os.fork()
    except BaseException:
        for fd in created:
            os.close(fd)
        raise
    if pid == 0:
        try:
            os.close(status_r)
            os.close(ack_w)
            keep = {out_fd, err_fd, status_w, ack_r}
            if mirror_fd is not None:
                keep.add(mirror_fd)
            _close_except(keep)
            _run_supervisor(argv, env, out_fd, err_fd, status_w, ack_r, budget, mirror_fd)
        finally:
            # Never unwind into the cloned caller, even when setup fails.
            os._exit(124)
    parent_owned = set(created)
    pidfd = None
    try:
        os.close(status_w)
        parent_owned.remove(status_w)
        os.close(ack_r)
        parent_owned.remove(ack_r)
        os.set_blocking(status_r, False)
        os.set_blocking(ack_w, False)
        pidfd = os.pidfd_open(pid)
        identity = proc_identity(pid)
        if identity["parent_pid"] != os.getpid():
            raise CaptureError("supervisor binding mismatch")
    except BaseException as exc:
        # No G has ever been written: observer code remains behind the exec
        # barrier. Closing control requests fail-closed cleanup. Preserve the
        # exact direct child and status descriptor for the owner's watchdog;
        # do not lose it behind a generic exception or claim that it exited.
        for fd in (ack_w, ack_r, status_w):
            if fd not in parent_owned:
                continue
            try:
                os.close(fd)
            except OSError:
                pass
            parent_owned.discard(fd)
        raise SupervisorStartError(f"supervisor binding unavailable:{type(exc).__name__}",
                                   pid, status_r, pidfd) from exc
    return SupervisorHandle(pid, identity["start_ticks"], pidfd, status_r, ack_w)


class CapturePump:
    """No filesystem IO; finite buffers and nonblocking descriptor draining."""
    def __init__(self, fds, budget):
        _linux()
        if not fds:
            raise CaptureError("capture descriptor set is empty")
        if len(set(fds)) != len(fds):
            raise CaptureError("capture descriptors must be distinct")
        self.budget = budget
        self.started_ns = time.monotonic_ns()
        self.ended_ns = None
        self.streams = {fd: BoundedStream(limit) for fd, limit in fds.items()}
        self.pending = set(fds)
        for fd in self.pending:
            os.set_blocking(fd, False)

    def step(self):
        if not self.pending:
            return False
        if time.monotonic_ns() >= self.budget.join_ns:
            raise CaptureError("capture deadline")
        left = max(0, (self.budget.join_ns - time.monotonic_ns()) / 1e9)
        ready, _, _ = select.select(sorted(self.pending), [], [], min(.02, left))
        for fd in ready:
            if time.monotonic_ns() >= self.budget.join_ns:
                raise CaptureError("capture deadline")
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if chunk:
                self.streams[fd].feed(chunk)
            else:
                observed_ns = time.monotonic_ns()
                if observed_ns > self.budget.join_ns:
                    raise CaptureError("capture EOF observed after deadline")
                self.streams[fd].finish()
                self.pending.remove(fd)
                if not self.pending:
                    self.ended_ns = observed_ns
        return True

    def require_complete(self):
        if self.pending or self.ended_ns is None:
            raise CaptureError("capture EOF not observed")
        for value in self.streams.values():
            value.require_complete()


def validate_transcript(raw, *, eof, expected_supervisor, expected_cli, deadline_ns):
    """Custody validation for a retained descriptor, never arbitrary JSON proof.

    Caller must additionally observe the exact supervisor's terminal exit using
    its retained pidfd and compare the observer's existing semantic parser.
    """
    if type(deadline_ns) is not int or deadline_ns <= 0:
        raise CaptureError("invalid absolute join deadline")
    if not eof or len(raw) > STATUS_LIMIT or not raw.endswith(b"\n"):
        raise CaptureError("supervisor transcript incomplete")
    records = [decode_frame(line + b"\n", eof=True, limit=FRAME_LIMIT)
               for line in raw.splitlines()]
    if len(records) < 3:
        raise CaptureError("missing launch, reap or terminal")
    for number, record in enumerate(records, 1):
        if (record.get("schema") != SCHEMA or type(record.get("sequence")) is not int
                or record["sequence"] != number):
            raise CaptureError("supervisor sequence/schema mismatch")
    launch, terminal = records[0], records[-1]
    if launch.get("kind") != "LAUNCH_BOUND" or terminal.get("kind") != "TERMINAL":
        raise CaptureError("supervisor order mismatch")
    if not isinstance(expected_supervisor, dict) or not {"pid", "start_ticks", "source_sha256"} <= set(expected_supervisor):
        raise CaptureError("complete independently retained supervisor identity required")
    if any(type(expected_supervisor[k]) is not int or expected_supervisor[k] <= 0
           for k in ("pid", "start_ticks")):
        raise CaptureError("invalid retained supervisor identity")
    source_hash = expected_supervisor["source_sha256"]
    if not isinstance(source_hash, str) or len(source_hash) != 64 or any(c not in "0123456789abcdef" for c in source_hash):
        raise CaptureError("invalid retained source digest")
    supervisor = launch.get("supervisor", {})
    if any(supervisor.get(k) != v or type(supervisor.get(k)) is not type(v)
           for k, v in expected_supervisor.items()):
        raise CaptureError("supervisor identity mismatch")
    if (terminal.get("process_tree_closed") is not True
            or terminal.get("closure_mechanism") != "PR_SET_CHILD_SUBREAPER+waitid_ECHILD"
            or terminal.get("errors") != []):
        raise CaptureError("kernel tree closure absent or errors present")
    end, start = terminal.get("completed_monotonic_ns"), launch.get("observed_monotonic_ns")
    if type(end) is not int or type(start) is not int or not 0 <= start <= end <= deadline_ns:
        raise CaptureError("supervisor timing outside inherited budget")
    cli_fields = {"pid", "start_ticks", "parent_pid", "parent_start_ticks",
                  "session_id", "process_group_id"}
    if not isinstance(expected_cli, dict) or not cli_fields <= set(expected_cli):
        raise CaptureError("independently retained CLI identity required")
    if any(type(expected_cli[k]) is not int or expected_cli[k] <= 0 for k in cli_fields):
        raise CaptureError("invalid retained CLI identity")
    cli = launch.get("cli")
    if not isinstance(cli, dict) or any(cli.get(k) != v or type(cli.get(k)) is not type(v)
                                         for k, v in expected_cli.items()):
        raise CaptureError("CLI identity mismatch")
    if (cli["parent_pid"] != expected_supervisor["pid"]
            or cli["parent_start_ticks"] != expected_supervisor["start_ticks"]
            or cli["session_id"] != cli["pid"] or cli["process_group_id"] != cli["pid"]):
        raise CaptureError("CLI parent/session binding mismatch")
    if type(terminal.get("cli_exit_code")) is not int:
        raise CaptureError("missing typed CLI terminal wait")
    reaps = records[1:-1]
    if any(rec.get("kind") != "REAP" for rec in reaps):
        raise CaptureError("unexpected process event")
    previous_ns = start
    for rec in reaps:
        ident = rec.get("identity")
        if not isinstance(ident, dict) or any(type(ident.get(k)) is not int or ident[k] <= 0
                                             for k in cli_fields - {"parent_start_ticks"}):
            raise CaptureError("unidentified reaped process")
        if ident["parent_pid"] != expected_supervisor["pid"]:
            raise CaptureError("reaped process not owned by supervisor")
        when = rec.get("observed_monotonic_ns")
        if type(when) is not int or not previous_ns <= when <= end:
            raise CaptureError("reap timestamp outside ordered budget")
        if type(rec.get("exit_code")) is not int:
            raise CaptureError("missing typed process terminal wait")
        previous_ns = when
    identities = [(rec.get("identity", {}).get("pid"),
                   rec.get("identity", {}).get("start_ticks")) for rec in reaps]
    if len(set(identities)) != len(identities):
        raise CaptureError("duplicate process reap")
    matching = [rec for rec in reaps if
                rec.get("identity", {}).get("pid") == cli.get("pid") and
                rec.get("identity", {}).get("start_ticks") == cli.get("start_ticks")]
    if len(matching) != 1 or matching[0].get("exit_code") != terminal.get("cli_exit_code"):
        raise CaptureError("CLI terminal wait mismatch")
    if any(matching[0]["identity"].get(k) != cli[k]
           for k in cli_fields - {"parent_start_ticks"}):
        raise CaptureError("CLI terminal identity changed")
    return {"launch": launch, "terminal": terminal, "reaps": reaps}


ORIGINAL_LIMITS = {"cli.stdout.original": STDOUT_LIMIT,
                   "supervisor.stderr.original": SUPERVISOR_LIMIT,
                   "subtree.status.original": STATUS_LIMIT}


def validate_originals(originals):
    if set(originals) != set(ORIGINAL_LIMITS):
        raise CaptureError("complete original set required")
    for name, data in originals.items():
        if not isinstance(data, bytes) or len(data) > ORIGINAL_LIMITS[name]:
            raise CaptureError("invalid or excessive original")


def _persist(directory, originals, result_fd):
    try:
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory_fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise CaptureError("capture directory owner/mode")
            output = {}
            for name, data in originals.items():
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
                try:
                    view = memoryview(data)
                    while view:
                        size = os.write(fd, view)
                        if size < 1:
                            raise CaptureError("short original write")
                        view = view[size:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                try:
                    digest, count = hashlib.sha256(), 0
                    while True:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > len(data):
                            raise CaptureError("original grew during readback")
                        digest.update(chunk)
                    expected = hashlib.sha256(data).hexdigest()
                    if count != len(data) or digest.hexdigest() != expected:
                        raise CaptureError("original readback mismatch")
                    output[name] = {"bytes": count, "sha256": expected}
                finally:
                    os.close(fd)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        _record(result_fd, {"complete": True, "files": output})
        os._exit(0)
    except BaseException:
        os._exit(125)


def start_writer(directory, originals):
    """Separate writer; caller must poll its pidfd under the original deadline.

    Only the outer collector may call this. It must not be a child of the
    observer-only subreaper. This isolation keeps a blocked fsync away from
    descriptor draining and watchdog enforcement. Missing result/exit is UNKNOWN.
    """
    _linux()
    validate_originals(originals)
    result_r, result_w = os.pipe()
    try:
        pid = os.fork()
    except BaseException:
        os.close(result_r)
        os.close(result_w)
        raise
    if pid == 0:
        try:
            os.close(result_r)
            _close_except({result_w})
            os.set_blocking(result_w, False)
            _persist(directory, originals, result_w)
        finally:
            os._exit(125)
    writer_end_open = True
    try:
        os.close(result_w)
        writer_end_open = False
        os.set_blocking(result_r, False)
        pidfd = os.pidfd_open(pid)
    except BaseException as exc:
        if writer_end_open:
            try:
                os.close(result_w)
            except OSError:
                pass
        raise WriterStartError(f"writer handle unavailable:{type(exc).__name__}",
                               pid, result_r, None) from exc
    return {"pid": pid, "pidfd": pidfd, "result_fd": result_r}
