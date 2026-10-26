"""Portable custody controls and real Linux observer-subtree kernel tests."""
import copy
from contextlib import ExitStack
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock
from types import SimpleNamespace

MODULE = Path(__file__).with_name("startup_capture_core.py")
if not MODULE.exists():
    MODULE = Path(__file__).resolve().parent.parent / "startup_capture_core.py"
SPEC = importlib.util.spec_from_file_location("startup_capture_core", MODULE)
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


def frame(value):
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def proc_snapshot(pid=5, parent=1, group=5, session=5, start=123):
    values = [b"S", str(parent).encode(), str(group).encode(), str(session).encode()]
    values += [b"0"] * 15 + [str(start).encode()]
    return str(pid).encode() + b" (fixture) " + b" ".join(values)


_UNRESOLVED_CUSTODY = []


def _observe_owned_terminal(child, budget, pump=None):
    """Bounded wait of an actual retained direct child; never signal a PID."""
    record = {"status": "UNKNOWN", "pid": child.pid, "wait_status": None,
              "exit_code": None, "process_tree_closed": False,
              "cleanup_deadline_ns": budget.cleanup_ns}
    captured = core.BoundedStream(core.STATUS_LIMIT)
    status_fd = child.status_fd
    status_nonblocking = False
    if status_fd is not None:
        try:
            os.set_blocking(status_fd, False)
            status_nonblocking = True
        except OSError as exc:
            record["status_read_error"] = type(exc).__name__
    while time.monotonic_ns() < budget.cleanup_ns:
        if pump is not None:
            try:
                pump.step()
            except (core.CaptureError, OSError) as exc:
                record["capture_error"] = type(exc).__name__
        elif status_nonblocking and not captured.eof:
            try:
                data = os.read(status_fd, 65536)
                if data:
                    captured.feed(data)
                else:
                    captured.finish()
            except BlockingIOError:
                pass
            except OSError as exc:
                record["status_read_error"] = type(exc).__name__
                status_nonblocking = False
        try:
            reaped, status = os.waitpid(child.pid, os.WNOHANG)
        except ChildProcessError:
            record["reason"] = "original terminal wait unavailable"
            break
        except OSError as exc:
            record["reason"] = f"terminal wait failed:{type(exc).__name__}"
            break
        if reaped == child.pid:
            record.update(status="TERMINAL_WAIT_OBSERVED", wait_status=status,
                          exit_code=os.waitstatus_to_exitcode(status),
                          observed_monotonic_ns=time.monotonic_ns())
            break
        if reaped != 0:
            record["reason"] = "terminal wait returned another identity"
            break
        remaining = max(0, (budget.cleanup_ns - time.monotonic_ns()) / 1e9)
        if remaining:
            if child.pidfd is not None:
                try:
                    select.select([child.pidfd], [], [], min(.01, remaining))
                except OSError as exc:
                    record["reason"] = f"retained handle wait failed:{type(exc).__name__}"
                    break
            else:
                time.sleep(min(.01, remaining))
    if record["wait_status"] is None:
        record.setdefault("reason", "original cleanup deadline exhausted")
    elif record["observed_monotonic_ns"] > budget.cleanup_ns:
        record["status"] = "TERMINAL_WAIT_OBSERVED_AFTER_DEADLINE"
    if pump is not None and status_fd in pump.streams:
        captured = pump.streams[status_fd]
    record["status_bytes"] = len(captured.data)
    record["status_sha256"] = hashlib.sha256(captured.data).hexdigest()
    record["status_eof"] = captured.eof
    record["status_truncated"] = captured.truncated
    child.harness_status_original = bytes(captured.data)
    return record


class _HarnessCustody:
    """Established before pipes/fork, including exceptions with retained children."""
    def __init__(self, budget):
        self.budget = budget
        self.fds = set()
        self.writers = set()
        self.child = None
        self.pump = None
        self.wait_status = None
        self.cleanup_record = None
        self.failure = None
        self.close_errors = []
        self.uncertain_fds = set()

    def pipe(self):
        read_fd, write_fd = os.pipe()
        self.fds.update((read_fd, write_fd))
        self.writers.add(write_fd)
        return read_fd, write_fd

    def own(self, fd):
        if fd is not None and fd >= 0:
            self.fds.add(fd)
        return fd

    def close(self, fd):
        if fd in self.fds:
            self.fds.remove(fd)
            self.writers.discard(fd)
            try:
                os.close(fd)
            except OSError as exc:
                # close may already have released this descriptor. Retrying
                # its number could close an unrelated, subsequently reused FD.
                self.uncertain_fds.add(fd)
                self.close_errors.append({"fd": fd, "error": type(exc).__name__,
                                          "errno": exc.errno})
                raise

    def attach(self, child):
        self.child = child
        self.own(child.pidfd)
        self.own(child.status_fd)
        self.own(getattr(child, "ack_fd", None))

    def finish(self):
        for fd in tuple(self.writers):
            try:
                self.close(fd)
            except OSError:
                pass
        if self.child is not None and self.wait_status is None:
            if isinstance(self.child, core.SupervisorHandle):
                ack_fd = self.child.ack_fd
                if ack_fd in self.uncertain_fds or ack_fd not in self.fds:
                    self.child.ack_fd = -1
                else:
                    try:
                        self.child.abort()
                    except OSError as exc:
                        # Still retain and wait the original child; an abort error
                        # is not permission to lose its process/descriptor custody.
                        self.uncertain_fds.add(ack_fd)
                        self.close_errors.append({"fd": ack_fd, "error": type(exc).__name__,
                                                  "errno": exc.errno})
                        self.child.ack_fd = -1
                self.fds.discard(ack_fd)
            self.cleanup_record = _observe_owned_terminal(self.child, self.budget, self.pump)
            if isinstance(self.child, core.RetainedChildError):
                self.child.harness_custody = self.cleanup_record
            if self.failure is not None:
                self.failure.harness_custody = self.cleanup_record
            self.wait_status = self.cleanup_record["wait_status"]
            if self.wait_status is None:
                # Keep the actual handles and captured buffers reachable. The
                # failing harness does not silently drop an unresolved child.
                _UNRESOLVED_CUSTODY.append(self)
                print("HARNESS_CUSTODY " + json.dumps(self.cleanup_record, sort_keys=True),
                      file=sys.stderr)
                raise AssertionError("UNKNOWN: retained child has no original terminal wait")
        for fd in tuple(self.fds):
            try:
                self.close(fd)
            except OSError:
                pass
        if isinstance(self.child, core.SupervisorHandle):
            self.child.ack_fd = -1
        if self.close_errors:
            record = {"status": "UNKNOWN_DESCRIPTOR_CLOSE", "close_errors": self.close_errors,
                      "pid": self.child.pid if self.child is not None else None,
                      "wait_status": self.wait_status}
            _UNRESOLVED_CUSTODY.append(self)
            if self.failure is not None:
                self.failure.harness_close_errors = self.close_errors
            print("HARNESS_CUSTODY " + json.dumps(record, sort_keys=True), file=sys.stderr)
            raise AssertionError("UNKNOWN: descriptor close failed after original child wait") from self.failure


def transcript():
    supervisor = {"pid": 10, "start_ticks": 20, "source_sha256": "a" * 64}
    cli = {"pid": 11, "start_ticks": 21, "parent_pid": 10,
           "parent_start_ticks": 20, "session_id": 11, "process_group_id": 11}
    records = [
        {"schema": core.SCHEMA, "sequence": 1, "kind": "LAUNCH_BOUND",
         "supervisor": supervisor, "cli": cli, "observed_monotonic_ns": 100},
        {"schema": core.SCHEMA, "sequence": 2, "kind": "REAP",
         "identity": cli, "exit_code": 0, "observed_monotonic_ns": 200},
        {"schema": core.SCHEMA, "sequence": 3, "kind": "TERMINAL",
         "process_tree_closed": True,
         "closure_mechanism": "PR_SET_CHILD_SUBREAPER+waitid_ECHILD",
         "cli_exit_code": 0, "errors": [], "completed_monotonic_ns": 300},
    ]
    return supervisor, records


class PortableCustodyTests(unittest.TestCase):
    def test_abort_close_error_consumes_ack_ownership_without_retry(self):
        handle = core.SupervisorHandle(700, 50, 70, 10, 13)
        original = OSError(errno.EINTR, "injected uncertain ACK close")
        attempts = []

        def close(fd):
            attempts.append(fd)
            if attempts != [13]:
                raise AssertionError("reused ACK descriptor was closed again")
            raise original

        with mock.patch.object(core.os, "close", side_effect=close), \
             mock.patch.object(core.os, "write") as write:
            with self.assertRaises(OSError) as caught:
                handle.abort()
            self.assertIs(caught.exception, original)
            handle.abort()
            self.assertEqual(attempts, [13])
            self.assertEqual(handle.ack_fd, -1)
            self.assertTrue(handle.aborted)
            self.assertEqual((handle.pid, handle.start_ticks, handle.pidfd, handle.status_fd),
                             (700, 50, 70, 10))
            with self.assertRaises(core.CaptureError):
                handle.admit_exec()
            write.assert_not_called()

    def _assert_parent_close_error_retains_child(self, failed_fd):
        budget = core.Budget(time.monotonic_ns())
        original = OSError(errno.EINTR, "injected uncertain parent pipe close")
        attempts = []

        def close(fd):
            attempts.append(fd)
            if attempts.count(fd) > 1:
                raise AssertionError("reused parent pipe descriptor was closed again")
            if fd == failed_fd:
                raise original

        with mock.patch.object(core, "_linux"), \
             mock.patch.object(core.os, "pipe", side_effect=[(10, 11), (12, 13)]), \
             mock.patch.object(core.os, "fork", create=True, return_value=700) as fork, \
             mock.patch.object(core.os, "close", side_effect=close), \
             mock.patch.object(core.os, "set_blocking", create=True) as blocking, \
             mock.patch.object(core.os, "pidfd_open", create=True) as pidfd, \
             mock.patch.object(core, "proc_identity") as identity, \
             mock.patch.object(core, "_run_supervisor") as child, \
             mock.patch.object(core.os, "write") as write:
            with self.assertRaises(core.SupervisorStartError) as caught:
                core.start_supervisor(["never-executed-fixture"], {}, 20, 21, budget)
            self.assertIs(caught.exception.__cause__, original)
            self.assertEqual(caught.exception.pid, 700)
            self.assertEqual(caught.exception.status_fd, 10)
            self.assertIsNone(caught.exception.pidfd)
            self.assertFalse(caught.exception.process_tree_closed)
            self.assertEqual(set(attempts), {11, 12, 13})
            self.assertEqual(len(attempts), 3)
            fork.assert_called_once_with()
            blocking.assert_not_called()
            pidfd.assert_not_called()
            identity.assert_not_called()
            child.assert_not_called()
            write.assert_not_called()

    def test_parent_status_writer_close_error_is_not_retried(self):
        self._assert_parent_close_error_retains_child(11)

    def test_parent_ack_reader_close_error_is_not_retried(self):
        self._assert_parent_close_error_retains_child(12)

    def test_retained_pidfd_reusing_retired_pipe_number_is_not_closed(self):
        budget = core.Budget(time.monotonic_ns())
        original = OSError("injected post-pidfd identity error")
        events = []

        def acquire(pid):
            events.append(("pidfd", pid, 11))
            return 11

        with mock.patch.object(core, "_linux"), \
             mock.patch.object(core.os, "pipe", side_effect=[(10, 11), (12, 13)]), \
             mock.patch.object(core.os, "fork", create=True, return_value=700) as fork, \
             mock.patch.object(core.os, "close", side_effect=lambda fd: events.append(("close", fd))), \
             mock.patch.object(core.os, "set_blocking", create=True), \
             mock.patch.object(core.os, "pidfd_open", create=True, side_effect=acquire), \
             mock.patch.object(core, "proc_identity", side_effect=original), \
             mock.patch.object(core, "_run_supervisor") as child, \
             mock.patch.object(core.os, "write") as write:
            with self.assertRaises(core.SupervisorStartError) as caught:
                core.start_supervisor(["never-executed-fixture"], {}, 20, 21, budget)
            self.assertIs(caught.exception.__cause__, original)
            self.assertEqual((caught.exception.pid, caught.exception.status_fd, caught.exception.pidfd),
                             (700, 10, 11))
            self.assertFalse(caught.exception.process_tree_closed)
            self.assertEqual(events, [("close", 11), ("close", 12),
                                      ("pidfd", 700, 11), ("close", 13)])
            fork.assert_called_once_with()
            child.assert_not_called()
            write.assert_not_called()

    def test_writer_result_writer_close_error_is_not_retried(self):
        original = OSError(errno.EINTR, "injected uncertain writer pipe close")
        attempts = []

        def close(fd):
            attempts.append(fd)
            if attempts != [11]:
                raise AssertionError("reused writer pipe descriptor was closed again")
            raise original

        with mock.patch.object(core, "_linux"), \
             mock.patch.object(core.os, "pipe", return_value=(10, 11)), \
             mock.patch.object(core.os, "fork", create=True, return_value=700) as fork, \
             mock.patch.object(core.os, "close", side_effect=close), \
             mock.patch.object(core.os, "set_blocking", create=True) as blocking, \
             mock.patch.object(core.os, "pidfd_open", create=True) as pidfd, \
             mock.patch.object(core, "_persist") as persist:
            with self.assertRaises(core.WriterStartError) as caught:
                core.start_writer("never-opened-fixture", {name: b"x" for name in core.ORIGINAL_LIMITS})
            self.assertIs(caught.exception.__cause__, original)
            self.assertEqual((caught.exception.pid, caught.exception.status_fd, caught.exception.pidfd),
                             (700, 10, None))
            self.assertFalse(caught.exception.process_tree_closed)
            self.assertEqual(attempts, [11])
            fork.assert_called_once_with()
            blocking.assert_not_called()
            pidfd.assert_not_called()
            persist.assert_not_called()

    def _assert_supervisor_close_error_not_retried(self, failed_fd):
        # Exercise the real launch/cleanup code, stopping before its kernel wait
        # phase. All pipes/forks/closes/writes and identity reads are intercepted.
        class ReachedWaitPhase(Exception):
            pass

        budget = core.Budget(time.monotonic_ns())
        attempts = []
        own = {"pid": 700, "start_ticks": 50, "parent_pid": 1,
               "session_id": 700, "process_group_id": 700}
        cli = {"pid": 701, "start_ticks": 51, "parent_pid": 700,
               "session_id": 701, "process_group_id": 701}

        def close(fd):
            attempts.append(fd)
            if attempts.count(fd) > 1:
                raise AssertionError("reused supervisor pipe descriptor was closed again")
            if fd == failed_fd:
                raise OSError(errno.EINTR, "injected uncertain supervisor close")

        def stop(fd, value):
            if (fd, value) == (31, False):
                return  # Initial nonblocking setup of the synthetic status fd.
            self.assertEqual((fd, value), (30, False))
            raise ReachedWaitPhase()

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(core, "_subreaper"))
            stack.enter_context(mock.patch.object(core.os, "getpid", return_value=700))
            stack.enter_context(mock.patch.object(core, "proc_identity", side_effect=[own, cli]))
            stack.enter_context(mock.patch.object(core.Path, "read_bytes", return_value=b"fixture-source"))
            stack.enter_context(mock.patch.object(core.os, "pipe", side_effect=[(10, 11), (12, 13)]))
            fork = stack.enter_context(mock.patch.object(core.os, "fork", create=True, return_value=701))
            stack.enter_context(mock.patch.object(core.os, "close", side_effect=close))
            stack.enter_context(mock.patch.object(core, "_wait_byte"))
            stack.enter_context(mock.patch.object(core, "_write_record"))
            write = stack.enter_context(mock.patch.object(core.os, "write", return_value=1))
            stack.enter_context(mock.patch.object(core.os, "set_blocking", create=True, side_effect=stop))
            waitid = stack.enter_context(mock.patch.object(core.os, "waitid", create=True))
            waitpid = stack.enter_context(mock.patch.object(core.os, "waitpid", create=True))
            kill = stack.enter_context(mock.patch.object(core.os, "kill", create=True))
            pidfd_signal = stack.enter_context(mock.patch.object(core.signal, "pidfd_send_signal", create=True))
            exit_process = stack.enter_context(mock.patch.object(core.os, "_exit"))
            with self.assertRaises(ReachedWaitPhase):
                core._run_supervisor(["never-executed-fixture"], {}, 20, 21, 31, 30, budget)
            expected = {10, 11, 12, 13} | ({20, 21} if failed_fd in (10, 13) else set())
            self.assertEqual(set(attempts), expected)
            self.assertEqual(len(attempts), len(expected))
            fork.assert_called_once_with()
            waitid.assert_not_called()
            waitpid.assert_not_called()
            kill.assert_not_called()
            pidfd_signal.assert_not_called()
            exit_process.assert_not_called()
            if failed_fd == 13:
                write.assert_called_once_with(13, b"G")
            else:
                write.assert_not_called()

    def test_supervisor_ready_writer_close_error_is_not_retried(self):
        self._assert_supervisor_close_error_not_retried(11)

    def test_supervisor_gate_reader_close_error_is_not_retried(self):
        self._assert_supervisor_close_error_not_retried(12)

    def test_supervisor_ready_reader_close_error_is_not_retried(self):
        self._assert_supervisor_close_error_not_retried(10)

    def test_supervisor_gate_writer_close_error_is_not_retried(self):
        self._assert_supervisor_close_error_not_retried(13)

    def test_procfs_coherence_positive_uses_same_kernel_identity(self):
        raw = proc_snapshot()
        status = b"Pid:\t5\nPPid:\t1\nNSpid:\t5\n"
        identity = core._validate_procfs_snapshot(5, 1, 5, 5, raw, raw, status)
        self.assertEqual(identity["pid"], 5)
        self.assertEqual(identity["start_ticks"], 123)

    def test_procfs_foreign_namespace_is_rejected_without_pid_translation(self):
        foreign = proc_snapshot(pid=7737)
        status = b"Pid:\t7737\nPPid:\t1\nNSpid:\t7737\t5\n"
        with self.assertRaises(core.CaptureError):
            core._validate_procfs_snapshot(5, 1, 5, 5, foreign, foreign, status)

    def test_procfs_numeric_alias_namespace_ancestry_and_relationship_mismatch_fail(self):
        raw = proc_snapshot()
        status = b"Pid:\t5\nPPid:\t1\nNSpid:\t5\n"
        cases = [
            (raw, proc_snapshot(start=999), status),
            (raw, raw, b"Pid:5\nPPid:1\nNSpid:5 5\n"),
            (raw, raw, b"Pid:5\nPPid:1\n"),
            (raw, raw, b"Pid:5\nPid:5\nPPid:1\nNSpid:5\n"),
            (proc_snapshot(group=8), proc_snapshot(group=8), status),
        ]
        for self_stat, numeric_stat, self_status in cases:
            with self.subTest(status=self_status), self.assertRaises(core.CaptureError):
                core._validate_procfs_snapshot(5, 1, 5, 5, self_stat, numeric_stat, self_status)

    def test_procfs_preparation_reads_are_bounded(self):
        with mock.patch.object(core.Path, "open", return_value=io.BytesIO(b"x" * 16)):
            self.assertEqual(core._read_proc_bound("fixture", 16), b"x" * 16)
        with mock.patch.object(core.Path, "open", return_value=io.BytesIO(b"x" * 17)):
            with self.assertRaises(core.CaptureError):
                core._read_proc_bound("fixture", 16)

    def test_incoherent_environment_rejected_before_internal_pipes_or_fork(self):
        budget = core.Budget(time.monotonic_ns())
        raw = proc_snapshot(pid=7737)
        status = b"Pid:7737\nPPid:1\nNSpid:7737 5\n"
        with mock.patch.object(core.sys, "platform", "linux"), \
             mock.patch.object(core.os, "getpid", return_value=5), \
             mock.patch.object(core.os, "getppid", return_value=1), \
             mock.patch.object(core.os, "getpgrp", create=True, return_value=5), \
             mock.patch.object(core.os, "getsid", create=True, return_value=5), \
             mock.patch.object(core.os, "pidfd_open", create=True), \
             mock.patch.object(core.signal, "pidfd_send_signal", create=True), \
             mock.patch.object(core, "_read_proc_bound", side_effect=[raw, raw, status]), \
             mock.patch.object(core.os, "pipe") as pipe, \
             mock.patch.object(core.os, "fork", create=True) as fork:
            with self.assertRaisesRegex(core.CaptureError, "namespace identity"):
                core.start_supervisor(["fixture"], {}, 20, 21, budget)
            pipe.assert_not_called()
            fork.assert_not_called()

    def test_missing_cleanup_terminal_remains_unknown_and_preserves_handles(self):
        custody = _HarnessCustody(SimpleNamespace(cleanup_ns=100))
        child = core.SupervisorStartError("synthetic retained child", pid=7, status_fd=None, pidfd=8)
        custody.attach(child)
        before = len(_UNRESOLVED_CUSTODY)
        try:
            with mock.patch.object(time, "monotonic_ns", return_value=100), \
                 mock.patch.object(os, "waitpid", create=True) as wait, \
                 mock.patch.object(os, "close") as close, \
                 mock.patch("builtins.print"):
                with self.assertRaisesRegex(AssertionError, "UNKNOWN"):
                    custody.finish()
                wait.assert_not_called()
                close.assert_not_called()
            self.assertIs(_UNRESOLVED_CUSTODY[-1], custody)
            self.assertEqual(child.harness_custody["status"], "UNKNOWN")
            self.assertIsNone(child.harness_custody["wait_status"])
            self.assertIn(8, custody.fds)
            self.assertEqual(child.harness_custody["cleanup_deadline_ns"], 100)
        finally:
            del _UNRESOLVED_CUSTODY[before:]

    def test_retained_child_cleanup_uses_original_nonblocking_wait_without_signals(self):
        child = core.SupervisorStartError("synthetic retained child", pid=7, status_fd=None, pidfd=None)
        with mock.patch.object(time, "monotonic_ns", return_value=10), \
             mock.patch.object(os, "WNOHANG", 1, create=True), \
             mock.patch.object(os, "waitpid", create=True, return_value=(7, 124 << 8)) as wait, \
             mock.patch.object(os, "waitstatus_to_exitcode", create=True, return_value=124), \
             mock.patch.object(os, "kill", create=True) as kill, \
             mock.patch.object(signal, "pidfd_send_signal", create=True) as send_signal:
            result = _observe_owned_terminal(child, SimpleNamespace(cleanup_ns=100))
        wait.assert_called_once_with(7, 1)
        kill.assert_not_called()
        send_signal.assert_not_called()
        self.assertEqual(result["status"], "TERMINAL_WAIT_OBSERVED")
        self.assertEqual(result["exit_code"], 124)
        self.assertFalse(result["process_tree_closed"])

    def test_uncertain_writer_close_is_not_retried_and_cannot_skip_child_wait(self):
        custody = _HarnessCustody(SimpleNamespace(cleanup_ns=100))
        child = core.SupervisorStartError("synthetic retained child", pid=7, status_fd=None, pidfd=None)
        custody.attach(child)
        custody.own(9)
        custody.writers.add(9)
        before = len(_UNRESOLVED_CUSTODY)
        try:
            with mock.patch.object(os, "close", side_effect=InterruptedError("fixture close outcome unknown")) as close, \
                 mock.patch.object(time, "monotonic_ns", return_value=10), \
                 mock.patch.object(os, "WNOHANG", 1, create=True), \
                 mock.patch.object(os, "waitpid", create=True, return_value=(7, 124 << 8)) as wait, \
                 mock.patch.object(os, "waitstatus_to_exitcode", create=True, return_value=124), \
                 mock.patch("builtins.print"):
                try:
                    custody.close(9)
                except OSError as original:
                    custody.failure = original
                with self.assertRaisesRegex(AssertionError, "descriptor close failed"):
                    custody.finish()
                close.assert_called_once_with(9)
                wait.assert_called_once_with(7, 1)
            self.assertEqual(child.harness_custody["exit_code"], 124)
            self.assertEqual(custody.cleanup_record["status"], "TERMINAL_WAIT_OBSERVED")
            self.assertIn(9, custody.uncertain_fds)
            self.assertNotIn(9, custody.fds)
        finally:
            del _UNRESOLVED_CUSTODY[before:]

    def test_uncertain_ack_close_is_not_repeated_by_abort(self):
        custody = _HarnessCustody(SimpleNamespace(cleanup_ns=100))
        child = core.SupervisorHandle(pid=7, start_ticks=11, pidfd=None, status_fd=None, ack_fd=9)
        custody.attach(child)
        before = len(_UNRESOLVED_CUSTODY)
        try:
            with mock.patch.object(os, "close", side_effect=InterruptedError("fixture ACK close unknown")) as close, \
                 mock.patch.object(time, "monotonic_ns", return_value=10), \
                 mock.patch.object(os, "WNOHANG", 1, create=True), \
                 mock.patch.object(os, "waitpid", create=True, return_value=(7, 124 << 8)) as wait, \
                 mock.patch.object(os, "waitstatus_to_exitcode", create=True, return_value=124), \
                 mock.patch("builtins.print"):
                try:
                    custody.close(child.ack_fd)
                except OSError as original:
                    custody.failure = original
                with self.assertRaisesRegex(AssertionError, "descriptor close failed"):
                    custody.finish()
                close.assert_called_once_with(9)
                wait.assert_called_once_with(7, 1)
            self.assertEqual(child.ack_fd, -1)
            self.assertEqual(custody.cleanup_record["exit_code"], 124)
            self.assertIn(9, custody.uncertain_fds)
        finally:
            del _UNRESOLVED_CUSTODY[before:]

    def test_budget_never_resets(self):
        value = core.Budget(123)
        self.assertEqual(value.operation_ns, 24_000_000_123)
        self.assertEqual(value.cleanup_ns, 32_000_000_123)
        self.assertEqual(value.join_ns, 26_000_000_123)

    def test_budget_rejects_boolean_or_negative_origin(self):
        for value in (-1, True, 1.0, "1"):
            with self.subTest(value=value), self.assertRaises(core.CaptureError):
                core.Budget(value)

    def test_future_or_wall_clock_origin_cannot_extend_budget(self):
        with mock.patch.object(core.time, "monotonic_ns", return_value=100):
            for value in (101, 1_800_000_000_000_000_000):
                with self.subTest(value=value), self.assertRaises(core.CaptureError):
                    core.Budget(value)

    def test_exact_stream_limit_is_complete(self):
        stream = core.BoundedStream(3)
        stream.feed(b"a")
        stream.feed(b"bc")
        stream.finish()
        stream.require_complete()
        self.assertEqual(stream.metadata("x")["sha256"], hashlib.sha256(b"abc").hexdigest())

    def test_overflow_remains_bounded_and_is_not_success(self):
        stream = core.BoundedStream(3)
        stream.feed(b"abcdef")
        stream.feed(b"gh")
        stream.finish()
        self.assertEqual(bytes(stream.data), b"abc")
        self.assertEqual(stream.seen, 8)
        with self.assertRaises(core.CaptureError):
            stream.require_complete()

    def test_missing_eof_is_not_success(self):
        stream = core.BoundedStream(3)
        stream.feed(b"abc")
        with self.assertRaises(core.CaptureError):
            stream.require_complete()

    def test_duplicate_eof_and_post_eof_data_rejected(self):
        stream = core.BoundedStream(3)
        stream.finish()
        for action in (stream.finish, lambda: stream.feed(b"x")):
            with self.assertRaises(core.CaptureError):
                action()

    def test_invalid_stream_bound_rejected(self):
        for value in (0, -1, True, 1.0):
            with self.subTest(value=value), self.assertRaises(core.CaptureError):
                core.BoundedStream(value)

    def test_one_frame_requires_exact_eof_and_framing(self):
        self.assertEqual(core.decode_frame(b'{"ok":1}\n', eof=True), {"ok": 1})
        for raw, eof in ((b'{"ok":1}\n', False), (b'{"ok":1}', True),
                         (b'{}\n{}\n', True), (b'[]\n', True), (b'\xff\n', True)):
            with self.subTest(raw=raw, eof=eof), self.assertRaises(core.CaptureError):
                core.decode_frame(raw, eof=eof)

    def test_duplicate_json_key_is_not_last_writer_wins(self):
        with self.assertRaises(core.CaptureError):
            core.decode_frame(b'{"complete":false,"complete":true}\n', eof=True)

    def test_nonfinite_json_and_oversize_rejected(self):
        for raw, limit in ((b'{"a":NaN}\n', 100), (b'{"a":1e999}\n', 100), (b'{}\n', 2)):
            with self.subTest(raw=raw), self.assertRaises(core.CaptureError):
                core.decode_frame(raw, eof=True, limit=limit)

    def test_valid_transcript_preserves_observed_cli_exit(self):
        own, records = transcript()
        records[1]["exit_code"] = 7
        records[-1]["cli_exit_code"] = 7
        result = core.validate_transcript(b"".join(map(frame, records)), eof=True,
                                          expected_supervisor=own, expected_cli=transcript()[1][0]["cli"], deadline_ns=300)
        self.assertEqual(result["terminal"]["cli_exit_code"], 7)

    def test_procfs_empty_boolean_cannot_replace_kernel_wait(self):
        own, records = transcript()
        records[-1]["closure_mechanism"] = "empty-procfs-scan"
        with self.assertRaises(core.CaptureError):
            core.validate_transcript(b"".join(map(frame, records)), eof=True,
                                     expected_supervisor=own, expected_cli=transcript()[1][0]["cli"], deadline_ns=300)

    def test_false_closed_errors_and_deadline_fail(self):
        own, original = transcript()
        changes = [("process_tree_closed", False), ("process_tree_closed", 1),
                   ("errors", ["UNKNOWN"]), ("completed_monotonic_ns", 301),
                   ("cli_exit_code", 0.0)]
        for key, value in changes:
            records = copy.deepcopy(original)
            records[-1][key] = value
            with self.subTest(key=key), self.assertRaises(core.CaptureError):
                core.validate_transcript(b"".join(map(frame, records)), eof=True,
                                         expected_supervisor=own, expected_cli=transcript()[1][0]["cli"], deadline_ns=300)

    def test_wrong_source_pid_or_incomplete_retained_identity_fail(self):
        own, records = transcript()
        for bad in ({}, {"pid": 10}, dict(own, pid=12), dict(own, source_sha256="b" * 64)):
            with self.subTest(bad=bad), self.assertRaises(core.CaptureError):
                core.validate_transcript(b"".join(map(frame, records)), eof=True,
                                         expected_supervisor=bad, expected_cli=transcript()[1][0]["cli"], deadline_ns=300)

    def test_missing_reap_duplicate_sequence_or_wrong_exit_fail(self):
        own, original = transcript()
        cases = [original[:1] + original[-1:]]
        bad = copy.deepcopy(original)
        bad[1]["sequence"] = 1
        cases.append(bad)
        bad = copy.deepcopy(original)
        bad[1]["exit_code"] = 1
        cases.append(bad)
        for records in cases:
            with self.subTest(records=records), self.assertRaises(core.CaptureError):
                core.validate_transcript(b"".join(map(frame, records)), eof=True,
                                         expected_supervisor=own, expected_cli=transcript()[1][0]["cli"], deadline_ns=300)

    def test_writer_names_and_lengths_are_closed(self):
        originals = {key: b"" for key in core.ORIGINAL_LIMITS}
        core.validate_originals(originals)
        for bad in ({}, dict(originals, **{"../escape": b""}),
                    dict(originals, **{"cli.stdout.original": b"x" * (core.STDOUT_LIMIT + 1)})):
            with self.subTest(keys=list(bad)), self.assertRaises(core.CaptureError):
                core.validate_originals(bad)

    def test_missing_cli_and_none_equal_none_cannot_pass(self):
        own, records = transcript()
        del records[0]["cli"]
        records[1]["identity"] = {}
        del records[1]["exit_code"]
        del records[-1]["cli_exit_code"]
        with self.assertRaises(core.CaptureError):
            core.validate_transcript(b"".join(map(frame, records)), eof=True,
                expected_supervisor=own, expected_cli=transcript()[1][0]["cli"], deadline_ns=300)

    def test_sequence_bool_and_wrong_parent_and_reap_time_rejected(self):
        own, original = transcript()
        cases = []
        for index, key, value in ((0, "sequence", True),
                                  (1, "observed_monotonic_ns", 99),
                                  (1, "observed_monotonic_ns", 301)):
            records = copy.deepcopy(original)
            records[index][key] = value
            cases.append(records)
        records = copy.deepcopy(original)
        records[1]["identity"]["parent_pid"] = 999
        cases.append(records)
        records = copy.deepcopy(original)
        records[0]["cli"]["parent_start_ticks"] = 999
        cases.append(records)
        for records in cases:
            with self.subTest(records=records), self.assertRaises(core.CaptureError):
                core.validate_transcript(b"".join(map(frame, records)), eof=True,
                    expected_supervisor=own, expected_cli=transcript()[1][0]["cli"], deadline_ns=300)

    def test_ack_after_scheduler_pause_cannot_open_exec_gate(self):
        with mock.patch.object(core.os, "set_blocking", create=True), \
             mock.patch.object(core.os, "read") as read, \
             mock.patch.object(core.select, "select", return_value=([5], [], [])), \
             mock.patch.object(core.time, "monotonic_ns", side_effect=[90, 90, 110]):
            with self.assertRaises(core.CaptureError):
                core._wait_byte(5, b"G", 100)
            read.assert_not_called()

    def test_proc_identity_accepts_opaque_unicode_comm(self):
        fields = [b"S", b"10", b"11", b"11"] + [b"0"] * 15 + [b"123"]
        raw = b"11 (worker-\xc3\xb1) tricky) " + b" ".join(fields)
        with mock.patch.object(core.Path, "read_bytes", return_value=raw):
            identity = core.proc_identity(11)
        self.assertEqual(identity["pid"], 11)
        self.assertEqual(identity["start_ticks"], 123)

    def test_eof_time_is_captured_before_later_scheduler_pause(self):
        budget = core.Budget(0)
        with mock.patch.object(core, "_linux"), \
             mock.patch.object(core.os, "set_blocking", create=True), \
             mock.patch.object(core.os, "read", return_value=b""), \
             mock.patch.object(core.select, "select", return_value=([5], [], [])), \
             mock.patch.object(core.time, "monotonic_ns", side_effect=[10, 20, 20, 30, 40]):
            pump = core.CapturePump({5: 3}, budget)
            self.assertTrue(pump.step())
            self.assertEqual(pump.ended_ns, 40)
        with mock.patch.object(core.time, "monotonic_ns", return_value=budget.join_ns + 1):
            self.assertFalse(pump.step())
            pump.require_complete()

    def test_late_eof_cannot_be_closed_by_earlier_select(self):
        budget = core.Budget(0)
        with mock.patch.object(core, "_linux"), \
             mock.patch.object(core.os, "set_blocking", create=True), \
             mock.patch.object(core.os, "read", return_value=b""), \
             mock.patch.object(core.select, "select", return_value=([5], [], [])), \
             mock.patch.object(core.time, "monotonic_ns", side_effect=[10, 20, 20, 30, budget.join_ns + 1]):
            pump = core.CapturePump({5: 3}, budget)
            with self.assertRaises(core.CaptureError):
                pump.step()
            self.assertIsNone(pump.ended_ns)

    def test_prefork_failure_closes_only_new_pipe_descriptors(self):
        budget = core.Budget(time.monotonic_ns())
        with mock.patch.object(core, "_linux"), \
             mock.patch.object(core.os, "pipe", side_effect=[(10, 11), (12, 13)]), \
             mock.patch.object(core.os, "fork", create=True, side_effect=OSError("fixture fork failure")), \
             mock.patch.object(core.os, "close") as close:
            with self.assertRaises(OSError):
                core.start_supervisor(["fixture"], {}, 20, 21, budget)
            self.assertEqual([call.args[0] for call in close.call_args_list], [10, 11, 12, 13])

    def test_abort_is_idempotent_and_never_writes_into_control_pipe(self):
        handle = core.SupervisorHandle(1, 2, 3, 4, 5)
        with mock.patch.object(core.os, "close") as close, \
             mock.patch.object(core.os, "write") as write:
            handle.abort()
            handle.abort()
            close.assert_called_once_with(5)
            write.assert_not_called()
            with self.assertRaises(core.CaptureError):
                handle.admit_exec()


@unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
                     "actual Linux kernel proof required; not a portable PASS")
class LinuxKernelTests(unittest.TestCase):
    def assert_harness_terminal_custody(self, error):
        record = error.harness_custody
        self.assertEqual(record["status"], "TERMINAL_WAIT_OBSERVED")
        self.assertEqual(record["exit_code"], 124)
        self.assertIsNotNone(record["wait_status"])
        self.assertLessEqual(record["observed_monotonic_ns"], record["cleanup_deadline_ns"])
        self.assertFalse(record["process_tree_closed"])
        with self.assertRaises(ChildProcessError):
            os.waitpid(record["pid"], os.WNOHANG)

    def test_actual_runner_procfs_identity_is_coherent_before_fork(self):
        identity = core._check_procfs_coherence()
        self.assertEqual(identity["pid"], os.getpid())
        self.assertEqual(identity["parent_pid"], os.getppid())
        self.assertEqual(identity["process_group_id"], os.getpgrp())
        self.assertEqual(identity["session_id"], os.getsid(0))

    def test_observer_harness_reaps_retained_postfork_start_error(self):
        parent = os.getpid()
        original_identity = core.proc_identity

        def fail_parent_only(pid):
            if os.getpid() == parent and pid != parent:
                raise OSError("fixture postfork parent binding failure")
            return original_identity(pid)

        with mock.patch.object(core, "proc_identity", side_effect=fail_parent_only):
            with self.assertRaises(core.SupervisorStartError) as caught:
                self.run_observer("print('must-not-run')", elapsed=21)
        self.assert_harness_terminal_custody(caught.exception)

    def test_failed_mirror_harness_reaps_retained_postfork_start_error(self):
        parent = os.getpid()
        original_identity = core.proc_identity

        def fail_parent_only(pid):
            if os.getpid() == parent and pid != parent:
                raise OSError("fixture postfork mirror parent binding failure")
            return original_identity(pid)

        with mock.patch.object(core, "proc_identity", side_effect=fail_parent_only):
            with self.assertRaises(core.SupervisorStartError) as caught:
                self.run_failed_mirror("EPIPE")
        self.assert_harness_terminal_custody(caught.exception)

    def test_harness_reaps_acquired_child_when_capture_setup_raises(self):
        with mock.patch.object(core, "CapturePump", side_effect=core.CaptureError("fixture capture setup")):
            with self.assertRaisesRegex(core.CaptureError, "fixture capture setup") as caught:
                self.run_observer("print('must-not-run')", elapsed=21)
        self.assert_harness_terminal_custody(caught.exception)

    def run_observer(self, source, *, acknowledge=True, elapsed=0, check_barrier=False, mirror=False):
        budget = core.Budget(time.monotonic_ns() - int(elapsed * 1e9))
        custody = _HarnessCustody(budget)
        try:
            out_r, out_w = custody.pipe()
            err_r, err_w = custody.pipe()
            mirror_r, mirror_w = custody.pipe() if mirror else (None, None)
            try:
                handle = core.start_supervisor([sys.executable, "-u", "-B", "-c", source],
                    dict(os.environ), out_w, err_w, budget, mirror_fd=mirror_w)
            except core.RetainedChildError as exc:
                custody.attach(exc)
                raise
            custody.attach(handle)
            custody.close(out_w)
            custody.close(err_w)
            custody.close(mirror_w)
            capture_fds = {out_r: core.STDOUT_LIMIT, err_r: core.SUPERVISOR_LIMIT,
                           handle.status_fd: core.STATUS_LIMIT}
            if mirror_r is not None:
                capture_fds[mirror_r] = core.STATUS_LIMIT
            pump = custody.pump = core.CapturePump(capture_fds, budget)
            # Obtain immutable identity while observer code remains behind gate.
            while b"\n" not in pump.streams[handle.status_fd].data:
                self.assertTrue(pump.step())
            raw = bytes(pump.streams[handle.status_fd].data).splitlines()[0] + b"\n"
            binding = core.decode_frame(raw, eof=True)
            self.assertEqual(binding["kind"], "LAUNCH_BOUND")
            cli = binding["cli"]
            retained_cli = custody.own(os.pidfd_open(cli["pid"]))
            self.assertEqual(core.proc_identity(cli["pid"])["start_ticks"], cli["start_ticks"])
            self.assertEqual(cli["parent_pid"], handle.pid)
            if check_barrier:
                for _ in range(3):
                    pump.step()
                self.assertEqual(bytes(pump.streams[out_r].data), b"")
                self.assertEqual(select.select([retained_cli], [], [], 0)[0], [])
            if acknowledge:
                handle.admit_exec()
            else:
                custody.close(handle.ack_fd)
                handle.ack_fd = -1
            while pump.step():
                pass
            pump.require_complete()
            # Actual exact child exit, not a supplied exit-code field.
            self.assertTrue(select.select([handle.pidfd], [], [], .5)[0])
            reaped, wait_status = os.waitpid(handle.pid, 0)
            self.assertEqual(reaped, handle.pid)
            custody.wait_status = wait_status
            self.assertTrue(select.select([retained_cli], [], [], 0)[0])
            return (bytes(pump.streams[out_r].data), bytes(pump.streams[err_r].data),
                    bytes(pump.streams[handle.status_fd].data),
                    os.waitstatus_to_exitcode(wait_status), handle, budget, cli,
                    bytes(pump.streams[mirror_r].data) if mirror_r is not None else None)
        except BaseException as exc:
            custody.failure = exc
            raise
        finally:
            custody.finish()

    def validate(self, result):
        out, err, raw, exit_code, handle, budget, retained_cli = result[:7]
        self.assertEqual(exit_code, 0)
        expected = {"pid": handle.pid, "start_ticks": handle.start_ticks,
                    "source_sha256": hashlib.sha256(MODULE.read_bytes()).hexdigest()}
        return core.validate_transcript(raw, eof=True, expected_supervisor=expected, expected_cli=retained_cli,
                                        deadline_ns=budget.join_ns)

    def test_exec_barrier_and_actual_early_exit(self):
        result = self.run_observer("print('terminal'); raise SystemExit(7)", check_barrier=True)
        proof = self.validate(result)
        self.assertEqual(result[0], b"terminal\n")
        self.assertEqual(proof["terminal"]["cli_exit_code"], 7)

    def test_double_fork_setsid_descendant_is_reaped_before_echild(self):
        source = """import os,time
pid=os.fork()
if pid:
    os._exit(0)
os.setsid()
pid=os.fork()
if pid:
    os._exit(0)
time.sleep(.08)
os.write(1,b'descendant\\n')
os._exit(0)
"""
        result = self.run_observer(source)
        proof = self.validate(result)
        self.assertEqual(result[0], b"descendant\n")
        self.assertEqual(len(proof["reaps"]), 3)
        self.assertTrue(proof["terminal"]["process_tree_closed"])

    def test_missing_ack_never_executes_and_is_not_accepted(self):
        result = self.run_observer("print('must-not-run')", acknowledge=False)
        self.assertEqual(result[0], b"")
        self.assertEqual(result[3], 124)
        raw = result[2]
        terminal = core.decode_frame(raw.splitlines()[-1] + b"\n", eof=True)
        self.assertTrue(terminal["errors"])
        self.assertTrue(terminal["process_tree_closed"])

    def test_independent_status_mirror_has_identical_original_bytes_and_eof(self):
        result = self.run_observer("print('observer'); raise SystemExit(3)", mirror=True,
                                   check_barrier=True)
        proof = self.validate(result)
        self.assertEqual(result[2], result[7])
        self.assertEqual(proof["terminal"]["cli_exit_code"], 3)
        self.assertTrue(proof["terminal"]["process_tree_closed"])

    def test_mirror_alias_read_end_regular_file_and_protected_channel_rejected_before_fork(self):
        budget = core.Budget(time.monotonic_ns())
        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        other_r, other_w = os.pipe()
        alias = os.dup(out_w)
        try:
            with tempfile.TemporaryFile() as regular:
                cases = [(out_w, ()), (alias, ()), (out_r, ()),
                         (regular.fileno(), ()), (other_w, (other_r,))]
                for mirror_fd, protected in cases:
                    with self.subTest(fd=mirror_fd), mock.patch.object(core.os, "fork") as fork:
                        with self.assertRaises(core.CaptureError):
                            core.start_supervisor(["fixture"], {}, out_w, err_w, budget,
                                                  mirror_fd=mirror_fd, protected_fds=protected)
                        fork.assert_not_called()
        finally:
            for fd in (out_r, out_w, err_r, err_w, other_r, other_w, alias):
                os.close(fd)

    def run_failed_mirror(self, failure):
        budget = core.Budget(time.monotonic_ns() - 21_000_000_000)
        custody = _HarnessCustody(budget)
        try:
            out_r, out_w = custody.pipe()
            err_r, err_w = custody.pipe()
            mirror_r, mirror_w = custody.pipe()
            prefilled = 0
            if failure == "EPIPE":
                custody.close(mirror_r)
                mirror_r = -1
            elif failure == "EAGAIN":
                os.set_blocking(mirror_w, False)
                for _ in range(512):
                    try:
                        written = os.write(mirror_w, b"x" * core.FRAME_LIMIT)
                        self.assertEqual(written, core.FRAME_LIMIT)
                        prefilled += written
                    except BlockingIOError:
                        break
                else:
                    self.fail("fixture pipe did not fill within its finite budget")
                self.assertGreater(prefilled, 0)
            else:
                self.fail("unknown fixture failure")
            try:
                handle = core.start_supervisor(
                    [sys.executable, "-u", "-B", "-c", "print('MUST_NOT_EXECUTE')"],
                    dict(os.environ), out_w, err_w, budget, mirror_fd=mirror_w)
            except core.RetainedChildError as exc:
                custody.attach(exc)
                raise
            custody.attach(handle)
            custody.close(out_w)
            custody.close(err_w)
            custody.close(mirror_w)
            pump = custody.pump = core.CapturePump(
                {out_r: core.STDOUT_LIMIT, err_r: core.SUPERVISOR_LIMIT,
                 handle.status_fd: core.STATUS_LIMIT}, budget)
            # Adversarial prequeued ACK: even this must not override a failed
            # independent capture sink. Normal callers retain CLI identity first.
            try:
                handle.admit_exec()
            except BrokenPipeError:
                pass
            while pump.step():
                pass
            pump.require_complete()
            self.assertTrue(select.select([handle.pidfd], [], [], .5)[0])
            reaped, status = os.waitpid(handle.pid, 0)
            self.assertEqual(reaped, handle.pid)
            custody.wait_status = status
            self.assertEqual(os.waitstatus_to_exitcode(status), 124)
            self.assertEqual(bytes(pump.streams[out_r].data), b"")
            raw = bytes(pump.streams[handle.status_fd].data)
            records = [core.decode_frame(line + b"\n", eof=True) for line in raw.splitlines()]
            self.assertEqual(records[0]["kind"], "LAUNCH_BOUND")
            self.assertEqual(records[-1]["kind"], "TERMINAL")
            self.assertTrue(records[-1]["process_tree_closed"])
            expected_error = "BrokenPipeError" if failure == "EPIPE" else "BlockingIOError"
            self.assertTrue(any(expected_error in error for error in records[-1]["errors"]))
            # No successful mirror bytes can have replaced the original failure.
            captured_mirror = bytearray()
            if mirror_r >= 0:
                os.set_blocking(mirror_r, False)
                while True:
                    chunk = os.read(mirror_r, 65536)
                    if not chunk:
                        break
                    captured_mirror.extend(chunk)
                    self.assertLessEqual(len(captured_mirror), prefilled)
                self.assertEqual(bytes(captured_mirror), b"x" * prefilled)
            own = {"pid": handle.pid, "start_ticks": handle.start_ticks,
                   "source_sha256": hashlib.sha256(MODULE.read_bytes()).hexdigest()}
            with self.assertRaises(core.CaptureError):
                core.validate_transcript(bytes(captured_mirror), eof=mirror_r >= 0,
                    expected_supervisor=own, expected_cli=records[0]["cli"],
                    deadline_ns=budget.join_ns)
            with self.assertRaises(core.CaptureError):
                core.validate_transcript(raw, eof=True, expected_supervisor=own,
                    expected_cli=records[0]["cli"], deadline_ns=budget.join_ns)
        except BaseException as exc:
            custody.failure = exc
            raise
        finally:
            custody.finish()

    def test_closed_independent_reader_is_unknown_without_cli_execution(self):
        self.run_failed_mirror("EPIPE")

    def test_full_independent_pipe_is_unknown_without_retry_or_cli_execution(self):
        self.run_failed_mirror("EAGAIN")

    def test_timeout_kills_adopted_setsid_child_but_remains_unknown(self):
        source = """import os,time,signal
pid=os.fork()
if pid:
    os._exit(0)
os.setsid()
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(60)
"""
        result = self.run_observer(source, elapsed=23)
        self.assertEqual(result[3], 124)
        terminal = core.decode_frame(result[2].splitlines()[-1] + b"\n", eof=True)
        self.assertTrue(terminal["process_tree_closed"])
        self.assertIn("inherited operation deadline expired", terminal["errors"])

    def test_original_writer_custody_and_no_overwrite(self):
        originals = {key: (key + "\n").encode() for key in core.ORIGINAL_LIMITS}
        with tempfile.TemporaryDirectory(prefix="capture-core-") as directory:
            os.chmod(directory, 0o700)
            writer = core.start_writer(directory, originals)
            try:
                self.assertTrue(select.select([writer["pidfd"]], [], [], 3)[0])
                _, status = os.waitpid(writer["pid"], 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                raw = os.read(writer["result_fd"], core.FRAME_LIMIT)
                self.assertEqual(os.read(writer["result_fd"], 1), b"")
                receipt = core.decode_frame(raw, eof=True)
                self.assertTrue(receipt["complete"])
                for name, data in originals.items():
                    self.assertEqual((Path(directory) / name).read_bytes(), data)
                    self.assertEqual(receipt["files"][name]["sha256"], hashlib.sha256(data).hexdigest())
            finally:
                os.close(writer["pidfd"])
                os.close(writer["result_fd"])
            second = core.start_writer(directory, originals)
            try:
                self.assertTrue(select.select([second["pidfd"]], [], [], 3)[0])
                _, status = os.waitpid(second["pid"], 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 125)
                self.assertEqual(os.read(second["result_fd"], 1), b"")
            finally:
                os.close(second["pidfd"])
                os.close(second["result_fd"])

    def test_postfork_identity_failure_retains_child_and_never_executes(self):
        budget = core.Budget(time.monotonic_ns())
        parent = os.getpid()
        real_identity = core.proc_identity

        def fail_parent_only(pid):
            if os.getpid() == parent:
                raise OSError("fixture identity failure after fork")
            return real_identity(pid)

        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        try:
            with mock.patch.object(core, "proc_identity", side_effect=fail_parent_only):
                with self.assertRaises(core.SupervisorStartError) as caught:
                    core.start_supervisor([sys.executable, "-B", "-c", "print('must-not-run')"],
                                          dict(os.environ), out_w, err_w, budget)
            retained = caught.exception
            self.assertFalse(retained.process_tree_closed)
            self.assertGreater(retained.pid, 0)
            os.close(out_w)
            os.close(err_w)
            out_w = err_w = -1
            try:
                self.assertTrue(select.select([retained.pidfd], [], [], 3)[0])
                reaped, status = os.waitpid(retained.pid, 0)
                self.assertEqual(reaped, retained.pid)
                self.assertEqual(os.waitstatus_to_exitcode(status), 124)
                self.assertEqual(os.read(out_r, 1), b"")
            finally:
                os.close(retained.pidfd)
                os.close(retained.status_fd)
        finally:
            for fd in (out_r, out_w, err_r, err_w):
                if fd >= 0:
                    os.close(fd)


if __name__ == "__main__":
    unittest.main()
