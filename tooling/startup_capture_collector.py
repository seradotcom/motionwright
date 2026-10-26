"""Passive, bounded custody of inherited capture descriptors.

This module launches only the existing isolated original-file writer. It does
not launch an application or observer, issue admission, prove process-tree
closure, or construct a product acceptance receipt. Input descriptors remain
owned by the caller. The returned writer handles require continued custody on
UNKNOWN; starting the writer is never retried.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import select
import signal
import stat
import time
from types import MappingProxyType, SimpleNamespace

import startup_capture_core as core


PHASE_LIMIT = 1_048_576
RESULT_LIMIT = core.FRAME_LIMIT
ORIGINAL_LIMITS = dict(core.ORIGINAL_LIMITS)


def _input_descriptors(channels):
    """Verify caller-bound distinct readable pipes; grant no launch authority."""
    import fcntl

    expected = set(ORIGINAL_LIMITS) | {"phase"}
    if type(channels) is not dict or set(channels) != expected:
        raise core.CaptureError("exact capture descriptor set required")
    result, identities = {}, set()
    for name, item in channels.items():
        if type(item) is not dict or set(item) != {"fd", "identity"}:
            raise core.CaptureError("bound input descriptor required")
        fd = item["fd"]
        if type(fd) is not int or fd < 3:
            raise core.CaptureError("invalid input descriptor")
        info = os.fstat(fd)
        actual = [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]
        supplied = item["identity"]
        if (type(supplied) is not list or len(supplied) != 3
                or any(type(value) is not int for value in supplied)
                or actual != supplied or not stat.S_ISFIFO(info.st_mode)):
            raise core.CaptureError("input pipe identity mismatch")
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
            raise core.CaptureError("capture input must be read-only")
        identity = tuple(actual)
        if fd in result.values() or identity in identities:
            raise core.CaptureError("capture descriptors alias")
        identities.add(identity)
        result[name] = fd
    return result


def _pidfd_exited(fd):
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    events = poller.poll(0)
    if any(mask & (select.POLLERR | select.POLLNVAL) for _, mask in events):
        raise core.CaptureError("writer pidfd poll failed")
    return any(mask & (select.POLLIN | select.POLLHUP) for _, mask in events)


class PassiveCollector:
    """Incremental original custody, with phase progress independent of join.

    Call ``step_originals`` until ``originals_status`` becomes PERSISTED or
    UNKNOWN. PERSISTED is available immediately after the three stream EOFs,
    writer readback receipt and original writer wait have all been observed
    before the inherited join deadline. It never waits for phase EOF.

    Call ``step_phase`` separately while the producer is alive, up to the
    original cleanup deadline. ``phase_original`` returns an exact bounded
    snapshot, including partial bytes. No phase durability claim is made.
    ``step`` is a convenience for one finite step of each, never a full wait.
    """

    def __init__(self, channels, directory, budget):
        if not isinstance(budget, core.Budget):
            raise core.CaptureError("original immutable Budget required")
        if time.monotonic_ns() >= budget.join_ns:
            raise core.CaptureError("capture setup after inherited join deadline")
        self.budget, self.directory = budget, Path(directory)
        if not self.directory.is_absolute() or ".." in self.directory.parts:
            raise core.CaptureError("absolute bound capture directory required")
        self.fds = _input_descriptors(channels)
        self.original_pump = core.CapturePump(
            {self.fds[name]: limit for name, limit in ORIGINAL_LIMITS.items()}, budget)
        # A phase channel may remain open while its producer waits for the
        # originals. This uses C from the same origin, never a renewed clock.
        self.phase_pump = core.CapturePump(
            {self.fds["phase"]: PHASE_LIMIT}, SimpleNamespace(join_ns=budget.cleanup_ns))
        self.originals = None
        self.originals_status = "CAPTURING"
        self.errors, self.phase_errors = [], []
        self.writer_attempted = False
        self.writer = None
        self.writer_result = core.BoundedStream(RESULT_LIMIT)
        self.writer_result_readable = False
        self.writer_result_value = None
        self.writer_result_observed_ns = None
        self.writer_wait = None
        self.writer_wait_lost = False
        self.writer_pidfd_exit = False
        self.writer_signal_attempted = False
        self.writer_signal = None
        self.completed_ns = None
        self.closed_writer_fds, self.uncertain_writer_fds = set(), set()

    def _error(self, reason):
        if reason not in self.errors:
            self.errors.append(reason)
        self.originals_status = "UNKNOWN"

    def _retain_writer(self, handle):
        # Called even after a post-fork failure; no error path discards a PID
        # or handle. These are actual handles from the existing writer API.
        self.writer = dict(handle)
        fd = self.writer.get("result_fd")
        if fd is not None:
            try:
                os.set_blocking(fd, False)
                self.writer_result_readable = True
            except OSError as exc:
                self._error("writer result nonblocking setup:" + type(exc).__name__)

    def _start_writer_once(self):
        self.writer_attempted = True  # Before a possibly effectful fork.
        try:
            self._retain_writer(core.start_writer(self.directory, self.originals))
        except core.RetainedChildError as exc:
            self._retain_writer({"pid": exc.pid, "pidfd": exc.pidfd,
                                 "result_fd": exc.status_fd,
                                 "retained_start_error": type(exc).__name__})
            self._error("writer start retained child:" + type(exc).__name__)
        except BaseException as exc:
            self._error("writer start unresolved:" + type(exc).__name__)
            # Unexpected failure has no fabricated not-started verdict or retry.

    def _poll_writer(self):
        if self.writer is None or time.monotonic_ns() >= self.budget.cleanup_ns:
            return
        fd = self.writer.get("result_fd")
        if (self.writer_result_readable and not self.writer_result.eof
                and fd not in self.closed_writer_fds and fd not in self.uncertain_writer_fds):
            try:
                raw = os.read(fd, RESULT_LIMIT + 1)
                if raw:
                    self.writer_result.feed(raw)
                    if self.writer_result.truncated:
                        self._error("writer result exceeds byte budget")
                else:
                    self.writer_result.finish()
                    self.writer_result_observed_ns = time.monotonic_ns()
            except BlockingIOError:
                pass
            except OSError as exc:
                self.writer_result_readable = False
                self._error("writer result read:" + type(exc).__name__)
        pidfd = self.writer.get("pidfd")
        if time.monotonic_ns() >= self.budget.cleanup_ns:
            return
        if (pidfd is not None and not self.writer_pidfd_exit
                and pidfd not in self.closed_writer_fds and pidfd not in self.uncertain_writer_fds):
            try:
                self.writer_pidfd_exit = _pidfd_exited(pidfd)
            except (OSError, core.CaptureError) as exc:
                self._error("writer pidfd:" + type(exc).__name__)
        if time.monotonic_ns() >= self.budget.cleanup_ns:
            return
        if self.writer_wait is None and not self.writer_wait_lost:
            try:
                pid, status = os.waitpid(self.writer["pid"], os.WNOHANG)
                if pid:
                    if pid != self.writer["pid"]:
                        raise core.CaptureError("foreign writer terminal")
                    self.writer_wait = {"pid": pid, "wait_status": status,
                        "exit_code": os.waitstatus_to_exitcode(status),
                        "observed_monotonic_ns": time.monotonic_ns()}
            except (OSError, core.CaptureError) as exc:
                self.writer_wait_lost = True
                self._error("writer original wait unavailable:" + type(exc).__name__)
        if self.writer_wait is not None and self.writer_wait["exit_code"] != 0:
            self._error("writer exited without successful persistence")

    def _validate_writer_result(self):
        if self.writer_result_value is not None or not self.writer_result.eof:
            return
        try:
            self.writer_result.require_complete()
            value = core.decode_frame(bytes(self.writer_result.data), eof=True, limit=RESULT_LIMIT)
            expected = {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                        for name, raw in self.originals.items()}
            if (set(value) != {"complete", "files"} or value["complete"] is not True
                    or type(value["files"]) is not dict or value["files"] != expected):
                raise core.CaptureError("writer readback mismatch")
            # Python dict equality alone treats true as 1. Retain exact types.
            for descriptor in value["files"].values():
                if (set(descriptor) != {"bytes", "sha256"}
                        or type(descriptor["bytes"]) is not int
                        or type(descriptor["sha256"]) is not str):
                    raise core.CaptureError("writer descriptor types")
            self.writer_result_value = value
        except (core.CaptureError, TypeError, ValueError, KeyError) as exc:
            self._error("writer receipt invalid:" + type(exc).__name__)

    def _cleanup_writer_after_join(self):
        now = time.monotonic_ns()
        if (self.writer is None or self.writer_wait is not None
                or self.writer_wait_lost or self.writer_signal_attempted
                or not self.budget.join_ns <= now < self.budget.cleanup_ns):
            return
        pidfd = self.writer.get("pidfd")
        if pidfd is None:
            self._error("writer cleanup lacks retained pidfd")
            return
        self.writer_signal_attempted = True
        self.writer_signal = {"signal": int(signal.SIGKILL), "mono_ns": now,
                              "transport": "RETAINED_PIDFD", "outcome": "UNKNOWN"}
        try:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
            self.writer_signal["outcome"] = "SENT"
        except ProcessLookupError:
            self.writer_signal["outcome"] = "ALREADY_EXITED_WAIT_REQUIRED"
        except OSError as exc:
            self.writer_signal["error"] = type(exc).__name__
        # A failed or uncertain signal is never repeated, and is never closure.

    def step_originals(self):
        """One bounded progress step; never wait for phase or perform disk IO."""
        if self.originals_status == "PERSISTED":
            return self.originals_status
        now = time.monotonic_ns()
        if self.originals is None and not self.errors:
            if now >= self.budget.join_ns:
                self._error("observer original capture deadline")
            else:
                try:
                    self.original_pump.step()
                    if any(s.truncated for s in self.original_pump.streams.values()):
                        raise core.CaptureError("observer stream truncated")
                    if not self.original_pump.pending:
                        self.original_pump.require_complete()
                        self.originals = MappingProxyType({
                            name: bytes(self.original_pump.streams[fd].data)
                            for name, fd in self.fds.items() if name != "phase"})
                except (OSError, core.CaptureError) as exc:
                    self._error("observer capture:" + type(exc).__name__)
        if self.originals is not None and not self.writer_attempted and not self.errors:
            if time.monotonic_ns() >= self.budget.join_ns:
                self._error("no writer start after original join deadline")
            else:
                self.originals_status = "WRITER_PENDING"
                self._start_writer_once()
        self._poll_writer()
        if self.originals is not None:
            self._validate_writer_result()
        now = time.monotonic_ns()
        if (not self.errors and self.writer_result_value is not None
                and self.writer_wait is not None and self.writer_pidfd_exit):
            if (self.writer_wait["exit_code"] == 0
                    and self.writer_wait["observed_monotonic_ns"] <= self.budget.join_ns
                    and self.writer_result_observed_ns <= self.budget.join_ns
                    and now <= self.budget.join_ns):
                self.originals_status = "PERSISTED"
                if self.completed_ns is None:
                    self.completed_ns = now
            else:
                self._error("writer completion outside inherited join deadline")
        if now >= self.budget.join_ns and self.originals_status != "PERSISTED":
            self._error("original custody incomplete at join deadline")
        self._cleanup_writer_after_join()
        return self.originals_status

    def step_phase(self):
        """Continue independent phase capture up to original C, never join on it."""
        stream = self.phase_pump.streams[self.fds["phase"]]
        if self.phase_errors or stream.eof:
            return self.phase_metadata()["status"]
        if time.monotonic_ns() >= self.budget.cleanup_ns:
            self.phase_errors.append("phase EOF absent at fixed cleanup cap")
        else:
            try:
                self.phase_pump.step()
                if stream.truncated:
                    self.phase_errors.append("phase stream exceeded byte budget")
            except (OSError, core.CaptureError) as exc:
                self.phase_errors.append("phase capture:" + type(exc).__name__)
        return self.phase_metadata()["status"]

    def step(self):
        self.step_originals()
        self.step_phase()
        return self.originals_status

    def phase_original(self):
        return bytes(self.phase_pump.streams[self.fds["phase"]].data)

    def phase_metadata(self):
        stream = self.phase_pump.streams[self.fds["phase"]]
        return {"status": "INCOMPLETE" if self.phase_errors else
                    ("EOF_OBSERVED" if stream.eof else "IN_PROGRESS"),
                "bytes": len(stream.data), "seen_bytes": stream.seen,
                "sha256": hashlib.sha256(stream.data).hexdigest(),
                "eof": stream.eof, "truncated": stream.truncated,
                "errors": list(self.phase_errors), "durable": False,
                "capture_started_monotonic_ns": self.phase_pump.started_ns,
                "capture_ended_monotonic_ns": self.phase_pump.ended_ns,
                "cleanup_deadline_ns": self.budget.cleanup_ns,
                "semantic_validation": "NOT_PERFORMED"}

    def snapshot(self):
        """No IO; exact captured originals remain available on this instance."""
        return {"scope": "PASSIVE_CAPTURE_CUSTODY_ONLY",
                "originals_status": self.originals_status, "errors": list(self.errors),
                "capture_started_monotonic_ns": self.original_pump.started_ns,
                "capture_ended_monotonic_ns": self.original_pump.ended_ns,
                "completed_monotonic_ns": self.completed_ns,
                "join_deadline_ns": self.budget.join_ns,
                "cleanup_deadline_ns": self.budget.cleanup_ns,
                "writer_attempted": self.writer_attempted,
                "writer_handles": None if self.writer is None else dict(self.writer),
                "writer_wait": self.writer_wait,
                "writer_pidfd_exit_observed": self.writer_pidfd_exit,
                "writer_result_sha256": hashlib.sha256(self.writer_result.data).hexdigest(),
                "writer_result_eof": self.writer_result.eof,
                "writer_readback": self.writer_result_value,
                "writer_signal": self.writer_signal,
                "uncertain_writer_fds": sorted(self.uncertain_writer_fds),
                "phase": self.phase_metadata(), "process_tree_closed": False,
                "installed_result": "NOT_RUN", "formal_acceptance": False}

    def close_writer_descriptors(self):
        """Release own descriptors only after an original wait; inputs stay owned.

        An unresolved child retains its handles. A descriptor-close error is
        recorded before returning and never retried, since its result is unknown.
        """
        if self.writer is None:
            return True
        if self.writer_wait is None:
            self._error("retained writer has no original terminal wait")
            return False
        for name in ("result_fd", "pidfd"):
            fd = self.writer.get(name)
            if fd is None or fd in self.closed_writer_fds or fd in self.uncertain_writer_fds:
                continue
            self.closed_writer_fds.add(fd)
            try:
                os.close(fd)
            except OSError as exc:
                self.uncertain_writer_fds.add(fd)
                self._error("writer descriptor close unknown:" + type(exc).__name__)
        return not self.uncertain_writer_fds
