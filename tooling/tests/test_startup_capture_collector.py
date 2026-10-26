"""Portable state controls and narrowly scoped Linux writer controls."""
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
CORE = HERE / "startup_capture_core.py"
if not CORE.exists():
    CORE = HERE.parent / "v3" / "startup_capture_core.py"
if not CORE.exists():
    CORE = HERE.parent / "startup_capture_core.py"
SPEC = importlib.util.spec_from_file_location("startup_capture_core", CORE)
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)
MODULE = HERE / "startup_capture_collector.py"
if not MODULE.exists():
    MODULE = HERE.parent / "startup_capture_collector.py"
SPEC = importlib.util.spec_from_file_location("startup_capture_collector", MODULE)
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


def frame(value):
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def close_owned_inputs_once(fds):
    errors = []
    for fd in sorted(tuple(fds)):
        fds.remove(fd)
        try:
            os.close(fd)
        except OSError as exc:
            errors.append({"fd": fd, "status": "UNKNOWN_DESCRIPTOR_CLOSE",
                           "error": type(exc).__name__})
    return errors


class FakePump:
    def __init__(self, fds, budget):
        self.budget = budget
        self.started_ns = time.monotonic_ns()
        self.ended_ns = None
        self.streams = {fd: core.BoundedStream(limit) for fd, limit in fds.items()}
        self.pending = set(fds)
        self.steps = 0

    def step(self):
        self.steps += 1

    def finish(self, fd, raw=b"x"):
        self.streams[fd].feed(raw)
        self.streams[fd].finish()
        self.pending.remove(fd)
        if not self.pending:
            self.ended_ns = time.monotonic_ns()

    def require_complete(self):
        if self.pending or self.ended_ns is None:
            raise core.CaptureError("EOF missing")
        for stream in self.streams.values():
            stream.require_complete()


class PortableCollectorTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_000_000_000_000
        self.clock = mock.patch.object(time, "monotonic_ns", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.budget = core.Budget(self.now)
        self.fds = {name: fd for fd, name in enumerate(collector.ORIGINAL_LIMITS, 10)}
        self.fds["phase"] = 13
        with mock.patch.object(collector, "_input_descriptors", return_value=self.fds), \
             mock.patch.object(core, "CapturePump", FakePump):
            self.capture = collector.PassiveCollector({}, HERE / "unused-private-capture", self.budget)
        self.handle = {"pid": 700, "pidfd": 701, "result_fd": 702}

    def complete_inputs(self):
        for fd in sorted(self.capture.original_pump.pending):
            self.capture.original_pump.finish(fd)

    def receipt(self):
        return frame({"complete": True, "files": {
            name: {"bytes": 1, "sha256": hashlib.sha256(b"x").hexdigest()}
            for name in collector.ORIGINAL_LIMITS}})

    @contextmanager
    def runtime(self, *, reads=None, wait=(700, 0), exited=True, start_error=None):
        with ExitStack() as stack:
            start = stack.enter_context(mock.patch.object(core, "start_writer",
                return_value=self.handle, side_effect=start_error))
            stack.enter_context(mock.patch.object(os, "set_blocking", create=True))
            stack.enter_context(mock.patch.object(os, "WNOHANG", 1, create=True))
            reader = stack.enter_context(mock.patch.object(os, "read",
                side_effect=reads if reads is not None else BlockingIOError()))
            waiter = stack.enter_context(mock.patch.object(os, "waitpid", create=True,
                return_value=wait))
            stack.enter_context(mock.patch.object(os, "waitstatus_to_exitcode", create=True,
                side_effect=lambda status: status >> 8))
            stack.enter_context(mock.patch.object(collector, "_pidfd_exited", return_value=exited))
            stack.enter_context(mock.patch.object(signal, "SIGKILL", 9, create=True))
            send = stack.enter_context(mock.patch.object(signal, "pidfd_send_signal", create=True))
            kill = stack.enter_context(mock.patch.object(os, "kill", create=True))
            yield start, reader, waiter, send, kill

    def test_all_three_eofs_required_before_writer(self):
        pending = sorted(self.capture.original_pump.pending)
        self.capture.original_pump.finish(pending[0])
        self.capture.original_pump.finish(pending[1])
        with self.runtime() as (start, _, _, _, _):
            self.assertEqual(self.capture.step_originals(), "CAPTURING")
            start.assert_not_called()

    def test_persisted_result_available_without_phase_eof(self):
        self.complete_inputs()
        with self.runtime(reads=[self.receipt(), b""]) as (start, _, waiter, _, _):
            self.capture.step_originals()
            self.assertEqual(self.capture.step_originals(), "PERSISTED")
            start.assert_called_once()
            waiter.assert_called_once_with(700, 1)
        self.assertEqual(self.capture.phase_pump.steps, 0)
        self.assertEqual(self.capture.phase_metadata()["status"], "IN_PROGRESS")
        self.assertFalse(self.capture.phase_metadata()["durable"])
        self.assertFalse(self.capture.snapshot()["process_tree_closed"])
        self.assertLessEqual(self.capture.completed_ns, self.budget.join_ns)

    def test_persisted_state_stays_fixed_while_phase_continues_after_join(self):
        self.complete_inputs()
        with self.runtime(reads=[self.receipt(), b""]):
            self.capture.step_originals()
            self.capture.step_originals()
            completed = self.capture.completed_ns
            self.now = self.budget.join_ns + 1
            self.capture.phase_pump.streams[13].feed(b"phase-original")
            self.assertEqual(self.capture.step_originals(), "PERSISTED")
            self.assertEqual(self.capture.step_phase(), "IN_PROGRESS")
            self.assertEqual(self.capture.completed_ns, completed)
            self.assertEqual(self.capture.phase_original(), b"phase-original")

    def test_receipt_without_original_wait_is_unknown_at_join(self):
        self.complete_inputs()
        with self.runtime(reads=[self.receipt(), b""], wait=(0, 0)):
            self.capture.step_originals()
            self.capture.step_originals()
            self.assertEqual(self.capture.originals_status, "WRITER_PENDING")
            self.now = self.budget.join_ns
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")
            with mock.patch.object(os, "close") as close:
                self.assertFalse(self.capture.close_writer_descriptors())
                close.assert_not_called()
            self.assertIsNone(self.capture.writer_wait)
            self.assertEqual(self.capture.writer["pidfd"], 701)

    def test_actual_wait_without_receipt_is_unknown(self):
        self.complete_inputs()
        with self.runtime():
            self.capture.step_originals()
            self.now = self.budget.join_ns
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")
        self.assertEqual(self.capture.writer_wait["exit_code"], 0)
        self.assertFalse(self.capture.writer_result.eof)

    def test_nonzero_writer_wait_rejects_favorable_receipt(self):
        self.complete_inputs()
        with self.runtime(reads=[self.receipt(), b""], wait=(700, 125 << 8)):
            self.capture.step_originals()
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")
        self.assertEqual(self.capture.writer_wait["exit_code"], 125)

    def test_readback_digest_mismatch_is_unknown(self):
        self.complete_inputs()
        raw = self.receipt().replace(hashlib.sha256(b"x").hexdigest().encode(), b"0" * 64)
        with self.runtime(reads=[raw, b""]):
            self.capture.step_originals()
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")

    def test_duplicate_result_frames_rejected(self):
        self.complete_inputs()
        with self.runtime(reads=[self.receipt() * 2, b""]):
            self.capture.step_originals()
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")

    def test_partial_result_without_eof_never_promotes(self):
        self.complete_inputs()
        with self.runtime(reads=[self.receipt(), BlockingIOError(), BlockingIOError()]):
            self.capture.step_originals()
            self.assertEqual(self.capture.step_originals(), "WRITER_PENDING")
            self.now = self.budget.join_ns
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")

    def test_writer_result_byte_cap_is_physical_memory_bound(self):
        self.complete_inputs()
        with self.runtime(reads=[b"x" * (collector.RESULT_LIMIT + 1)]):
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")
        self.assertEqual(len(self.capture.writer_result.data), collector.RESULT_LIMIT)
        self.assertTrue(self.capture.writer_result.truncated)

    def test_truncated_observer_original_prevents_writer(self):
        fd = self.fds["cli.stdout.original"]
        self.capture.original_pump.streams[fd].feed(b"x" * (core.STDOUT_LIMIT + 1))
        with self.runtime() as (start, _, _, _, _):
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")
            start.assert_not_called()

    def test_late_original_eof_does_not_start_writer(self):
        self.complete_inputs()
        self.now = self.budget.join_ns
        with self.runtime() as (start, _, _, _, _):
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")
            start.assert_not_called()

    def test_retained_start_error_keeps_handles_and_never_retries(self):
        self.complete_inputs()
        error = core.WriterStartError("fixture", 700, 702, None)
        with self.runtime(reads=[self.receipt(), b""], start_error=error) as (start, _, waiter, _, _):
            self.capture.step_originals()
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")
            start.assert_called_once()
            waiter.assert_called_once()
        self.assertEqual(self.capture.writer["pid"], 700)
        self.assertIsNone(self.capture.writer["pidfd"])
        self.assertEqual(self.capture.writer_wait["exit_code"], 0)

    def test_cleanup_uses_retained_pidfd_once_without_pid_signal(self):
        self.complete_inputs()
        with self.runtime(wait=(0, 0), exited=False) as (_, _, _, send, kill):
            self.capture.step_originals()
            self.now = self.budget.join_ns
            self.capture.step_originals()
            self.capture.step_originals()
            send.assert_called_once_with(701, 9, None, 0)
            kill.assert_not_called()
        self.assertIsNone(self.capture.writer_wait)
        self.assertEqual(self.capture.originals_status, "UNKNOWN")

    def test_phase_original_retained_and_incomplete_at_fixed_cap(self):
        self.capture.phase_pump.streams[13].feed(b"exact\x00phase\n")
        self.now = self.budget.cleanup_ns
        self.assertEqual(self.capture.step_phase(), "INCOMPLETE")
        self.assertEqual(self.capture.phase_original(), b"exact\x00phase\n")
        self.assertFalse(self.capture.phase_metadata()["durable"])

    def test_phase_overflow_does_not_claim_complete_original(self):
        self.capture.phase_pump.streams[13].feed(b"p" * (collector.PHASE_LIMIT + 1))
        self.assertEqual(self.capture.step_phase(), "INCOMPLETE")
        self.assertEqual(len(self.capture.phase_original()), collector.PHASE_LIMIT)
        self.assertTrue(self.capture.phase_metadata()["truncated"])

    def test_late_writer_completion_never_becomes_persisted(self):
        self.complete_inputs()
        with self.runtime(reads=[self.receipt(), b""]):
            self.capture.step_originals()
            self.now = self.budget.join_ns + 1
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")
            self.assertIsNone(self.capture.completed_ns)

    def test_unknown_descriptor_close_is_not_repeated(self):
        self.complete_inputs()
        with self.runtime(reads=[self.receipt(), b""]):
            self.capture.step_originals()
            self.capture.step_originals()
        with mock.patch.object(os, "close", side_effect=OSError("uncertain")) as close:
            self.assertFalse(self.capture.close_writer_descriptors())
            self.assertFalse(self.capture.close_writer_descriptors())
            self.assertEqual(close.call_args_list, [mock.call(702), mock.call(701)])
        self.assertEqual(self.capture.writer_wait["exit_code"], 0)

    def test_nonblocking_setup_failure_does_not_drop_original_wait(self):
        self.complete_inputs()
        with self.runtime() as (_, _, waiter, _, _):
            with mock.patch.object(os, "set_blocking", create=True, side_effect=OSError("fixture")):
                self.assertEqual(self.capture.step_originals(), "UNKNOWN")
            waiter.assert_called_once()
        self.assertEqual(self.capture.writer["pidfd"], 701)

    def test_boolean_byte_count_rejected_even_when_equal_to_one(self):
        self.complete_inputs()
        raw = self.receipt().replace(b'"bytes":1', b'"bytes":true')
        with self.runtime(reads=[raw, b""]):
            self.capture.step_originals()
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")

    def test_wait_without_pidfd_exit_crosscheck_never_promotes(self):
        self.complete_inputs()
        with self.runtime(reads=[self.receipt(), b""], exited=False):
            self.capture.step_originals()
            self.assertEqual(self.capture.step_originals(), "WRITER_PENDING")
            self.now = self.budget.join_ns
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")

    def test_cleanup_cap_rechecked_after_result_read(self):
        self.complete_inputs()

        def read_then_pause(*args):
            self.now = self.budget.cleanup_ns + 1
            return b""

        with self.runtime() as (_, reader, waiter, send, _):
            reader.side_effect = read_then_pause
            self.assertEqual(self.capture.step_originals(), "UNKNOWN")
            waiter.assert_not_called()
            send.assert_not_called()

    def test_input_close_error_does_not_skip_other_inputs_or_retry(self):
        fds = {17, 18, 19}
        with mock.patch.object(os, "close", side_effect=[OSError("uncertain"), None, None]) as close:
            errors = close_owned_inputs_once(fds)
            self.assertEqual(close_owned_inputs_once(fds), [])
            self.assertEqual(close.call_args_list, [mock.call(17), mock.call(18), mock.call(19)])
        self.assertEqual(errors, [{"fd": 17, "status": "UNKNOWN_DESCRIPTOR_CLOSE", "error": "OSError"}])
        self.assertEqual(fds, set())


_UNRESOLVED_LINUX_CUSTODY = []


@unittest.skipUnless(sys.platform.startswith("linux"), "real Linux descriptor/pidfd controls")
class LinuxCollectorTests(unittest.TestCase):
    @contextmanager
    def capture_case(self, elapsed=22):
        fds, channels, writers = set(), {}, {}
        current = None
        temporary = tempfile.TemporaryDirectory(prefix="capture-collector-")
        try:
            for name in (*collector.ORIGINAL_LIMITS, "phase"):
                read_fd, write_fd = os.pipe()
                fds.update((read_fd, write_fd))
                info = os.fstat(read_fd)
                channels[name] = {"fd": read_fd,
                    "identity": [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]}
                writers[name] = write_fd
            budget = core.Budget(time.monotonic_ns() - elapsed * 1_000_000_000)
            current = collector.PassiveCollector(channels, temporary.name, budget)
            yield current, writers, Path(temporary.name), fds
        finally:
            unresolved = False
            cleanup_errors = []
            if current is not None and current.writer is not None:
                try:
                    while current.writer_wait is None and time.monotonic_ns() < current.budget.cleanup_ns:
                        current.step_originals()
                        time.sleep(.005)
                    if current.writer_wait is None:
                        unresolved = True
                        cleanup_errors.append({"status": "UNKNOWN_WRITER_TERMINAL"})
                    elif not current.close_writer_descriptors():
                        unresolved = True
                        cleanup_errors.append({"status": "UNKNOWN_WRITER_DESCRIPTOR_CLOSE"})
                except BaseException as exc:
                    unresolved = True
                    cleanup_errors.append({"status": "UNKNOWN_CUSTODY_CLEANUP", "error": type(exc).__name__})
            cleanup_errors.extend(close_owned_inputs_once(fds))
            unresolved |= bool(cleanup_errors)
            if unresolved:
                _UNRESOLVED_LINUX_CUSTODY.append((current, temporary, cleanup_errors))
                print(json.dumps({"status": "UNKNOWN", "cleanup_errors": cleanup_errors,
                    "writer": None if current is None else current.snapshot()}, sort_keys=True))
            else:
                temporary.cleanup()
            if unresolved:
                self.fail("custody cleanup unresolved; handles or uncertain-close records retained, UNKNOWN")

    def close_input(self, writers, fds, name, raw=b"x"):
        fd = writers.pop(name)
        try:
            if raw:
                self.assertEqual(os.write(fd, raw), len(raw))
        finally:
            fds.remove(fd)
            os.close(fd)

    def finish_original_inputs(self, writers, fds):
        for name in collector.ORIGINAL_LIMITS:
            self.close_input(writers, fds, name)

    def drive(self, current):
        while time.monotonic_ns() < current.budget.join_ns:
            state = current.step_originals()
            if state in ("PERSISTED", "UNKNOWN"):
                return state
            time.sleep(.002)
        return current.step_originals()

    def test_real_writer_readback_and_wait_finish_before_open_phase(self):
        with self.capture_case() as (current, writers, directory, fds):
            os.write(writers["phase"], b"phase-start\n")
            self.finish_original_inputs(writers, fds)
            self.assertEqual(self.drive(current), "PERSISTED")
            self.assertEqual(current.phase_metadata()["status"], "IN_PROGRESS")
            self.assertEqual(current.writer_wait["exit_code"], 0)
            self.assertTrue(current.writer_pidfd_exit)
            self.assertLessEqual(current.completed_ns, current.budget.join_ns)
            for name in collector.ORIGINAL_LIMITS:
                self.assertEqual((directory / name).read_bytes(), b"x")
            self.close_input(writers, fds, "phase", b"phase-end\n")
            while current.phase_pump.pending and time.monotonic_ns() < current.budget.cleanup_ns:
                current.step_phase()
            self.assertEqual(current.phase_original(), b"phase-start\nphase-end\n")
            self.assertEqual(current.phase_metadata()["status"], "EOF_OBSERVED")
            self.assertFalse(current.phase_metadata()["durable"])

    def test_real_blocked_writer_does_not_block_fixed_watchdog(self):
        def blocked(*args):
            time.sleep(60)

        with self.capture_case(elapsed=25) as (current, writers, _, fds):
            self.finish_original_inputs(writers, fds)
            with mock.patch.object(core, "_persist", side_effect=blocked):
                self.assertEqual(self.drive(current), "UNKNOWN")
                while current.writer_wait is None and time.monotonic_ns() < current.budget.cleanup_ns:
                    current.step_originals()
                    current.step_phase()
                    time.sleep(.005)
            self.assertIsNotNone(current.writer_wait)
            self.assertEqual(current.writer_wait["exit_code"], -signal.SIGKILL)
            self.assertEqual(current.writer_signal["transport"], "RETAINED_PIDFD")
            self.assertLess(current.writer_wait["observed_monotonic_ns"], current.budget.cleanup_ns)
            self.assertFalse(current.snapshot()["process_tree_closed"])

    def test_real_postfork_pidfd_error_retains_and_reaps_writer_without_retry(self):
        original_open = os.pidfd_open
        parent = os.getpid()

        def fail_parent(pid, *args):
            if os.getpid() == parent and pid != parent:
                raise OSError("injected parent pidfd failure")
            return original_open(pid, *args)

        with self.capture_case() as (current, writers, _, fds):
            self.finish_original_inputs(writers, fds)
            with mock.patch.object(core.os, "pidfd_open", side_effect=fail_parent), \
                 mock.patch.object(core, "start_writer", wraps=core.start_writer) as start:
                self.assertEqual(self.drive(current), "UNKNOWN")
                while current.writer_wait is None and time.monotonic_ns() < current.budget.cleanup_ns:
                    current.step_originals()
                    time.sleep(.002)
                start.assert_called_once()
            self.assertEqual(current.writer_wait["exit_code"], 0)
            self.assertIsNone(current.writer["pidfd"])
            self.assertEqual(current.writer["retained_start_error"], "WriterStartError")
            self.assertEqual(current.originals_status, "UNKNOWN")

    def test_real_writer_error_without_receipt_is_not_success(self):
        def fail(*args):
            os._exit(125)

        with self.capture_case() as (current, writers, _, fds):
            self.finish_original_inputs(writers, fds)
            with mock.patch.object(core, "_persist", side_effect=fail):
                self.assertEqual(self.drive(current), "UNKNOWN")
                while current.writer_wait is None and time.monotonic_ns() < current.budget.cleanup_ns:
                    current.step_originals()
                    time.sleep(.002)
            self.assertEqual(current.writer_wait["exit_code"], 125)
            self.assertIsNone(current.writer_result_value)

    def test_real_descriptor_alias_and_write_end_rejected_before_writer(self):
        with self.capture_case() as (current, writers, _, _):
            channels = {}
            for name, fd in current.fds.items():
                info = os.fstat(fd)
                channels[name] = {"fd": fd,
                    "identity": [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]}
            names = list(collector.ORIGINAL_LIMITS)
            original = channels[names[1]]
            channels[names[1]] = dict(channels[names[0]])
            with mock.patch.object(core, "start_writer") as start:
                with self.assertRaisesRegex(core.CaptureError, "alias"):
                    collector._input_descriptors(channels)
                channels[names[1]] = original
                channels["phase"] = {**channels["phase"], "fd": writers["phase"]}
                with self.assertRaisesRegex(core.CaptureError, "read-only"):
                    collector._input_descriptors(channels)
                start.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
