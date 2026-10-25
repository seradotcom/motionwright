"""Portable custody controls and real Linux observer-subtree kernel tests."""
import copy
import hashlib
import importlib.util
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

MODULE = Path(__file__).resolve().parent.parent / "startup_capture_core.py"
if not MODULE.exists():
    MODULE = Path(__file__).with_name("startup_capture_core.py")
SPEC = importlib.util.spec_from_file_location("startup_capture_core", MODULE)
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


def frame(value):
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


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
    def run_observer(self, source, *, acknowledge=True, elapsed=0, check_barrier=False, mirror=False):
        budget = core.Budget(time.monotonic_ns() - int(elapsed * 1e9))
        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        mirror_r, mirror_w = os.pipe() if mirror else (None, None)
        handle = core.start_supervisor([sys.executable, "-u", "-B", "-c", source],
                                       dict(os.environ), out_w, err_w, budget, mirror_fd=mirror_w)
        os.close(out_w)
        os.close(err_w)
        if mirror_w is not None:
            os.close(mirror_w)
        capture_fds = {out_r: core.STDOUT_LIMIT, err_r: core.SUPERVISOR_LIMIT,
                       handle.status_fd: core.STATUS_LIMIT}
        if mirror_r is not None:
            capture_fds[mirror_r] = core.STATUS_LIMIT
        pump = core.CapturePump(capture_fds, budget)
        retained_cli = None
        wait_status = None
        try:
            # Obtain immutable identity while observer code remains behind gate.
            while b"\n" not in pump.streams[handle.status_fd].data:
                self.assertTrue(pump.step())
            raw = bytes(pump.streams[handle.status_fd].data).splitlines()[0] + b"\n"
            binding = core.decode_frame(raw, eof=True)
            self.assertEqual(binding["kind"], "LAUNCH_BOUND")
            cli = binding["cli"]
            retained_cli = os.pidfd_open(cli["pid"])
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
                os.close(handle.ack_fd)
                handle.ack_fd = -1
            while pump.step():
                pass
            pump.require_complete()
            # Actual exact child exit, not a supplied exit-code field.
            self.assertTrue(select.select([handle.pidfd], [], [], .5)[0])
            reaped, wait_status = os.waitpid(handle.pid, 0)
            self.assertEqual(reaped, handle.pid)
            self.assertTrue(select.select([retained_cli], [], [], 0)[0])
            return (bytes(pump.streams[out_r].data), bytes(pump.streams[err_r].data),
                    bytes(pump.streams[handle.status_fd].data),
                    os.waitstatus_to_exitcode(wait_status), handle, budget, cli,
                    bytes(pump.streams[mirror_r].data) if mirror_r is not None else None)
        finally:
            if wait_status is None:
                # Preserve the subreaper while it performs bounded cleanup.
                handle.abort()
                until = budget.join_ns + 1_000_000_000
                while time.monotonic_ns() < until:
                    try:
                        pump.step()
                    except core.CaptureError:
                        pass
                    if select.select([handle.pidfd], [], [], .01)[0]:
                        os.waitpid(handle.pid, 0)
                        break
            if handle.ack_fd >= 0:
                os.close(handle.ack_fd)
            for fd in (out_r, err_r, handle.status_fd, handle.pidfd, retained_cli, mirror_r):
                if fd is not None:
                    os.close(fd)

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
        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        mirror_r, mirror_w = os.pipe()
        prefilled = 0
        if failure == "EPIPE":
            os.close(mirror_r)
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
        handle = core.start_supervisor(
            [sys.executable, "-u", "-B", "-c", "print('MUST_NOT_EXECUTE')"],
            dict(os.environ), out_w, err_w, budget, mirror_fd=mirror_w)
        os.close(out_w)
        os.close(err_w)
        os.close(mirror_w)
        pump = core.CapturePump({out_r: core.STDOUT_LIMIT, err_r: core.SUPERVISOR_LIMIT,
                                 handle.status_fd: core.STATUS_LIMIT}, budget)
        waited = False
        try:
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
            waited = True
            self.assertEqual(reaped, handle.pid)
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
        finally:
            handle.abort()
            if not waited:
                until = budget.join_ns + 1_000_000_000
                while time.monotonic_ns() < until:
                    try:
                        pump.step()
                    except core.CaptureError:
                        pass
                    if select.select([handle.pidfd], [], [], .01)[0]:
                        os.waitpid(handle.pid, 0)
                        break
            for fd in (out_r, err_r, mirror_r, handle.status_fd, handle.pidfd):
                if fd >= 0:
                    os.close(fd)

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
