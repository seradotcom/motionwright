"""Source-only outer controls; Linux actors are fixtures, never A2 or an app.

The Linux controls use the actual core, five pipes, pidfds and original writer.
Only the observer semantic parser is replaced with a deliberately small fixture
parser. Its successful receipt is not evidence of an A2 or installed-product join.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
import uuid


HERE = Path(__file__).resolve().parent
OUTER_FILE = HERE.parent / "startup_capture_outer.py"
if not OUTER_FILE.exists():
    OUTER_FILE = HERE / "startup_capture_outer.py"
name = "_test_exact_startup_capture_outer"
outer = types.ModuleType(name)
outer.__file__ = str(OUTER_FILE)
sys.modules[name] = outer
exec(compile(OUTER_FILE.read_bytes(), str(OUTER_FILE), "exec"), outer.__dict__)
core = outer.core


def frame(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def stamp(pid, parent, *, start=None, group=None, session=None):
    return {"pid": pid, "parent_pid": parent, "start_ticks": start or pid * 10,
            "process_group_id": group or pid, "session_id": session or pid,
            "uid": 1000, "state": "R"}


class FakeCollector:
    def __init__(self, now, originals):
        self.originals = originals
        self.originals_status = "PERSISTED"
        self.errors, self.phase_errors = [], []
        self.phase = b""
        self.phase_eof = False
        self.now = now

    def snapshot(self):
        return {"capture_started_monotonic_ns": self.now - 19,
                "capture_ended_monotonic_ns": self.now - 4}

    def phase_metadata(self):
        return {"eof": self.phase_eof, "status": "EOF_OBSERVED" if self.phase_eof else "IN_PROGRESS"}

    def phase_original(self):
        return self.phase


class PortableOuterTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_000_000_000_020
        self.clock = mock.patch.object(time, "monotonic_ns", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(os, "getuid", return_value=1000, create=True))
        self.stack.enter_context(mock.patch.object(os, "waitstatus_to_exitcode", side_effect=lambda status: status >> 8, create=True))
        self.stack.enter_context(mock.patch.object(os, "WNOHANG", 1, create=True))
        self.stack.enter_context(mock.patch.object(signal, "SIGKILL", 9, create=True))

    def assembler(self):
        obj = outer.OuterAssembler.__new__(outer.OuterAssembler)
        obj.budget = core.Budget(self.now - 20)
        obj.directory = HERE / "source-only-capture"
        obj.owner = stamp(100, 90)
        obj.operation_id = "b3d1a2dc-b0fd-462f-8a9c-fb063f128bdf"
        obj.retention_reference = "fixture-retention"
        obj.source_sha256 = "a" * 64
        obj.errors, obj.extra_owned, obj.processes = [], {}, {}
        parents = {"consumer": obj.owner, "supervisor": stamp(200, 100),
                   "cli": stamp(300, 200, group=200, session=200), "worker": stamp(400, 300)}
        items = {"consumer": stamp(200, 100), "supervisor": stamp(300, 200, group=200, session=200),
                 "cli": stamp(400, 300), "worker": stamp(500, 400)}
        for index, role in enumerate(("consumer", "supervisor", "cli", "worker")):
            item = outer.RetainedProcess(role, items[role], parents[role], 70 + index)
            item.authenticated = True
            if role != "consumer":
                item.exit_ns = self.now - 3
            obj.processes[role] = item
        obj.expected_supervisor = {key: items["supervisor"][key] for key in
                                   ("pid", "parent_pid", "start_ticks", "session_id", "process_group_id")}
        obj.expected_supervisor["source_sha256"] = outer.CORE_SHA256
        obj.expected_cli = outer._core_binding(items["cli"], items["supervisor"])
        launch = {"schema": core.SCHEMA, "sequence": 1, "kind": "LAUNCH_BOUND",
                  "supervisor": obj.expected_supervisor, "cli": obj.expected_cli,
                  "observed_monotonic_ns": self.now - 10}
        reap = {"schema": core.SCHEMA, "sequence": 2, "kind": "REAP",
                "identity": {key: value for key, value in obj.expected_cli.items() if key != "parent_start_ticks"},
                "exit_code": 0, "observed_monotonic_ns": self.now - 6}
        terminal = {"schema": core.SCHEMA, "sequence": 3, "kind": "TERMINAL",
                    "process_tree_closed": True, "closure_mechanism": "PR_SET_CHILD_SUBREAPER+waitid_ECHILD",
                    "errors": [], "cli_exit_code": 0, "completed_monotonic_ns": self.now - 5}
        obj.supervisor_context = {"observer_sha256": outer.OBSERVER_SHA256, "admission_sha256": "b" * 64}
        worker_binding = outer._a2_binding(items["worker"], items["cli"])
        parsed = {"pending_io": False, "worker_bindings": {"500": {"binding": worker_binding,
                  "roles": ["PARENT_OBSERVED_WORKER", "WORKER_SELF"],
                  "deadline_monotonic_ns": obj.budget.operation_ns}},
                  "processes": {"400": {"result": {"status": "OBSERVED_TARGET_SNAPSHOT"}},
                                "500": {"result": None}}}
        obj.observer_api = types.SimpleNamespace(MAX_SUPERVISOR_LINE=1024,
            parse_supervisor=mock.Mock(return_value=parsed))
        originals = {"cli.stdout.original": b'{"fixture":true}\n',
            "supervisor.stderr.original": frame({"context": obj.supervisor_context,
                "mono_ns": self.now - 7, "event": "FIXTURE_ONLY"}),
            "subtree.status.original": frame(launch) + frame(reap) + frame(terminal)}
        obj.collector = FakeCollector(self.now, originals)
        obj.launch_phase_ns = self.now - 11
        obj.supervisor_wait = {"supervisor_pid": 300, "supervisor_start_ticks": 3000,
            "actual_wait_status": 0, "actual_exit_code": 0, "observed_monotonic_ns": self.now - 3,
            "phase_observed_monotonic_ns": self.now - 2}
        endpoint = outer.OwnedDescriptor(80, "capture_result:write")
        obj.pipes = types.SimpleNamespace(parent={"capture_result": endpoint},
            channels={"capture_result": {"retention_reference": "fixture-retention:capture_result"}},
            descriptors=[endpoint])
        obj.child = types.SimpleNamespace(pid=200, returncode=None)
        obj.child_wait, obj.child_wait_lost = None, False
        obj.phase_offset, obj.status_offset, obj.stderr_offset = 0, 0, 0
        obj.phase_sequence, obj.phase_last_ns = 0, obj.budget.started_ns
        obj.parsed_supervisor, obj.parsed_subtree = None, None
        obj.receipt, obj.receipt_bytes, obj.receipt_attempted = None, None, False
        obj.receipt_sent_ns, obj.receipt_status = None, "NOT_ATTEMPTED"
        obj.closure_ns, obj.cleanup_started, obj.finalized = None, False, False
        obj.launch_attempted = True
        return obj

    @contextmanager
    def join_runtime(self, obj):
        with mock.patch.object(outer, "_pidfd_exited", return_value=False), \
             mock.patch.object(obj, "_groups_clear"), \
             mock.patch.object(os, "fpathconf", return_value=4096, create=True):
            yield

    def test_uncertain_close_is_consumed_before_retry(self):
        item = outer.OwnedDescriptor(33, "owned")
        with mock.patch.object(os, "close", side_effect=OSError(5, "uncertain")) as close:
            self.assertFalse(item.close())
            self.assertFalse(item.close())
        close.assert_called_once_with(33)
        self.assertIsNone(item.fd)
        self.assertEqual(item.metadata()["close_error"]["errno"], 5)

    def test_reused_integer_has_independent_ownership_generation(self):
        first = outer.OwnedDescriptor(33, "old")
        with mock.patch.object(os, "close") as close:
            first.close()
            second = outer.OwnedDescriptor(33, "new")
            first.close()
            self.assertEqual(second.fd, 33)
            second.close()
        self.assertEqual(close.call_args_list, [mock.call(33), mock.call(33)])

    def test_pipe_standard_fd_rejection_closes_both_owned_ends(self):
        with mock.patch.object(os, "pipe2", return_value=(0, 10), create=True), \
             mock.patch.object(os, "O_CLOEXEC", 0, create=True), \
             mock.patch.object(os, "O_NONBLOCK", 0, create=True), \
             mock.patch.object(os, "close") as close:
            with self.assertRaisesRegex(outer.OuterError, "standard"):
                outer.FivePipes("fixture-retention")
        self.assertEqual(close.call_args_list, [mock.call(0), mock.call(10)])

    def test_pipe_alias_rejection_keeps_all_created_custody(self):
        replies = [([1, 22, stat.S_IFIFO], os.O_RDONLY), ([1, 22, stat.S_IFIFO], os.O_WRONLY)] * 2
        with mock.patch.object(os, "pipe2", side_effect=[(10, 11), (12, 13)], create=True), \
             mock.patch.object(os, "O_CLOEXEC", 0, create=True), \
             mock.patch.object(os, "O_NONBLOCK", 0, create=True), \
             mock.patch.object(outer, "_pipe_identity", side_effect=replies), \
             mock.patch.object(os, "close") as close:
            with self.assertRaisesRegex(outer.OuterError, "distinct"):
                outer.FivePipes("fixture-retention")
        self.assertEqual([call.args[0] for call in close.call_args_list], [10, 11, 12, 13])

    def test_wrong_pipe_direction_rejected(self):
        with mock.patch.object(os, "pipe2", return_value=(10, 11), create=True), \
             mock.patch.object(os, "O_CLOEXEC", 0, create=True), \
             mock.patch.object(os, "O_NONBLOCK", 0, create=True), \
             mock.patch.object(outer, "_pipe_identity", return_value=([1, 22, stat.S_IFIFO], os.O_RDONLY)), \
             mock.patch.object(os, "close") as close:
            with self.assertRaises(outer.OuterError):
                outer.FivePipes("fixture-retention")
        self.assertEqual(close.call_count, 2)

    def test_foreign_plan_hash_cannot_self_admit_a_bootstrap(self):
        plan = {"status": "A1_ADMITTED_INDEPENDENT_STARTUP", "observer_status": "ADMITTED_NEW_READONLY_OBSERVATION",
                "sources": {"bootstrap": {"path": str(HERE / "forged.py"), "sha256": "d" * 64}}}
        with mock.patch.object(outer, "_source_bytes") as reader:
            with self.assertRaisesRegex(outer.OuterError, "exact reviewed A2"):
                outer.require_owner_admission(plan, "a" * 64)
        reader.assert_not_called()

    def test_missing_launch_digest_is_rejected_before_any_process(self):
        obj = self.assembler()
        obj.launch_attempted = False
        with mock.patch.object(subprocess, "Popen") as start, \
             mock.patch.object(outer, "_source_bytes") as read:
            with self.assertRaisesRegex(outer.OuterError, "exact source digest"):
                obj.launch(["unused-python", "-I", "-B", "unused-source"], source_sha256=None)
        start.assert_not_called()
        read.assert_not_called()

    def test_exact_owner_empty_allowlist_does_not_admit(self):
        plan = {"status": "A1_ADMITTED_INDEPENDENT_STARTUP", "observer_status": "ADMITTED_NEW_READONLY_OBSERVATION",
                "sources": {"bootstrap": {"path": str(HERE / "owner.py"), "sha256": outer.A2_CONSUMER_SHA256}}}
        with mock.patch.object(outer, "_source_bytes", return_value=b"REVIEWED_OUTER_SOURCES = frozenset()\n"):
            with self.assertRaisesRegex(outer.OuterError, "not been admitted"):
                outer.require_owner_admission(plan, "a" * 64)

    def test_allowlist_expression_is_not_executed(self):
        with self.assertRaises(outer.OuterError):
            outer._reviewed_outer_sources(b"REVIEWED_OUTER_SOURCES = dangerous()\n")

    def test_pid1_can_be_in_group_inventory_but_never_a_target(self):
        obj = self.assembler()
        with mock.patch.object(Path, "iterdir", return_value=[Path("/proc/1"), Path("/proc/777")]), \
             mock.patch.object(outer, "_stamp", side_effect=[stamp(1, 0), stamp(777, 1)]):
            obj._groups_clear()
        with mock.patch.object(os, "pidfd_open", create=True) as opened:
            with self.assertRaisesRegex(outer.OuterError, "PID1"):
                obj._retain("foreign", 1, obj.owner)
        opened.assert_not_called()

    def test_kernel_zero_session_identity_is_readable_but_not_retainable(self):
        raw = b"2 (kthreadd) " + b" ".join([b"S"] + [b"0"] * 19) + b"\n"
        with mock.patch.object(core, "_read_proc_bound", return_value=raw), \
             mock.patch.object(Path, "stat", return_value=types.SimpleNamespace(st_uid=0)):
            observed = outer._stamp(2)
        self.assertEqual((observed["parent_pid"], observed["session_id"], observed["process_group_id"]), (0, 0, 0))
        obj = self.assembler()
        with mock.patch.object(outer, "_stamp", return_value=observed), \
             mock.patch.object(os, "pidfd_open", create=True) as opened:
            with self.assertRaisesRegex(outer.OuterError, "userspace"):
                obj._retain("kernel", 2, obj.owner)
        opened.assert_not_called()

    def test_pid_reuse_postcheck_never_authorizes_signal(self):
        obj = self.assembler()
        first = stamp(600, 100)
        changed = stamp(600, 100, start=99999)
        with mock.patch.object(outer, "_stamp", side_effect=[first, changed, obj.owner]), \
             mock.patch.object(os, "pidfd_open", return_value=91, create=True), \
             mock.patch.object(os, "close") as close, \
             mock.patch.object(signal, "pidfd_send_signal", create=True) as send:
            with self.assertRaisesRegex(outer.OuterError, "identity changed"):
                obj._retain("reused", 600, obj.owner)
            obj.processes["reused"].signal_once(signal.SIGKILL)
        close.assert_called_once_with(91)
        send.assert_not_called()
        self.assertFalse(obj.processes["reused"].authenticated)
        self.assertIsNotNone(obj.processes["reused"].authentication_error)

    def test_changed_parent_postcheck_does_not_authorize_signal(self):
        obj = self.assembler()
        child = stamp(600, 100)
        with mock.patch.object(outer, "_stamp", side_effect=[child, child, stamp(100, 90, start=99999)]), \
             mock.patch.object(os, "pidfd_open", return_value=91, create=True), \
             mock.patch.object(os, "close"), \
             mock.patch.object(signal, "pidfd_send_signal", create=True) as send:
            with self.assertRaises(outer.OuterError):
                obj._retain("changed-parent", 600, obj.owner)
            obj.processes["changed-parent"].signal_once(signal.SIGKILL)
        send.assert_not_called()

    def test_application_pid_reuse_handle_is_kept_out_of_signals(self):
        obj = self.assembler()
        application = outer.RetainedProcess("application", stamp(1000, 200), obj.processes["consumer"].stamp, 90)
        application.authenticated = True
        obj.processes["application"] = application
        child = stamp(1100, 1000)
        with mock.patch.object(core, "_read_proc_bound", return_value=b"1100"), \
             mock.patch.object(outer, "_stamp", side_effect=[child, stamp(1100, 1000, start=99999), application.stamp]), \
             mock.patch.object(outer, "_pidfd_exited", return_value=False), \
             mock.patch.object(os, "pidfd_open", return_value=91, create=True), \
             mock.patch.object(os, "close") as close, \
             mock.patch.object(signal, "pidfd_send_signal", create=True) as send:
            with self.assertRaisesRegex(outer.OuterError, "identity changed"):
                obj._sample_application_children()
            obj.extra_owned[1100].signal_once(signal.SIGKILL)
        close.assert_called_once_with(91)
        send.assert_not_called()
        self.assertFalse(obj.extra_owned[1100].authenticated)

    def test_expired_identity_budget_does_not_open_pidfd(self):
        obj = self.assembler()
        self.now = obj.budget.cleanup_ns
        with mock.patch.object(os, "pidfd_open", create=True) as opened:
            with self.assertRaisesRegex(outer.OuterError, "deadline"):
                obj._retain("late", 600, obj.owner)
        opened.assert_not_called()

    def test_complete_receipt_does_not_wait_for_phase_eof(self):
        obj = self.assembler()
        self.assertFalse(obj.collector.phase_metadata()["eof"])
        with self.join_runtime(obj):
            receipt = obj._make_receipt()
        self.assertTrue(receipt["complete"])
        self.assertEqual(set(receipt), {"schema", "operation_id", "observer_sha256", "admission_sha256",
            "collector", "cli", "capture_started_monotonic_ns", "capture_ended_monotonic_ns", "streams",
            "closure", "retention_reference", "complete", "errors"})
        self.assertEqual(set(receipt["streams"]), {"cli_stdout", "supervisor", "subtree_status"})
        self.assertIs(type(receipt["cli"]["start_ticks"]), str)
        self.assertIs(type(obj.expected_cli["start_ticks"]), int)

    def test_three_eofs_without_durable_writer_are_insufficient(self):
        obj = self.assembler()
        obj.collector.originals_status = "WRITER_PENDING"
        with self.join_runtime(obj), self.assertRaisesRegex(outer.OuterError, "durable originals"):
            obj._make_receipt()

    def test_missing_original_supervisor_wait_rejected(self):
        obj = self.assembler()
        obj.supervisor_wait = None
        with self.join_runtime(obj), self.assertRaisesRegex(outer.OuterError, "original wait"):
            obj._make_receipt()

    def test_wait_status_and_exit_code_must_agree(self):
        obj = self.assembler()
        obj.supervisor_wait["actual_wait_status"] = 256
        with self.join_runtime(obj), self.assertRaisesRegex(outer.OuterError, "original wait"):
            obj._make_receipt()

    def test_wait_after_fixed_join_rejected(self):
        obj = self.assembler()
        obj.supervisor_wait["observed_monotonic_ns"] = obj.budget.join_ns + 1
        with self.join_runtime(obj), self.assertRaisesRegex(outer.OuterError, "outside original join"):
            obj._make_receipt()

    def test_missing_worker_role_rejected(self):
        obj = self.assembler()
        obj.observer_api.parse_supervisor.return_value["worker_bindings"]["500"]["roles"].pop()
        with self.join_runtime(obj), self.assertRaisesRegex(outer.OuterError, "roles"):
            obj._make_receipt()

    def test_extended_worker_deadline_rejected(self):
        obj = self.assembler()
        obj.observer_api.parse_supervisor.return_value["worker_bindings"]["500"]["deadline_monotonic_ns"] += 1
        with self.join_runtime(obj), self.assertRaisesRegex(outer.OuterError, "extended"):
            obj._make_receipt()

    def test_future_or_prelaunch_supervisor_frame_rejected(self):
        for when in (self.now + 25 * outer.NS, self.now - 12):
            with self.subTest(when=when):
                obj = self.assembler()
                obj.collector.originals["supervisor.stderr.original"] = frame({"mono_ns": when})
                with self.join_runtime(obj), self.assertRaisesRegex(outer.OuterError, "operation budget"):
                    obj._make_receipt()

    def test_worker_cannot_emit_a_second_parent_result(self):
        obj = self.assembler()
        obj.observer_api.parse_supervisor.return_value["processes"]["500"]["result"] = {"status": "UNKNOWN"}
        with self.join_runtime(obj), self.assertRaisesRegex(outer.OuterError, "second parent result"):
            obj._make_receipt()

    def test_kernel_tree_closure_cannot_be_inferred_from_eof(self):
        obj = self.assembler()
        lines = obj.collector.originals["subtree.status.original"].splitlines()
        terminal = json.loads(lines[-1])
        terminal["process_tree_closed"] = False
        obj.collector.originals["subtree.status.original"] = b"\n".join(lines[:-1]) + b"\n" + frame(terminal)
        with self.join_runtime(obj), self.assertRaisesRegex(core.CaptureError, "kernel tree closure"):
            obj._make_receipt()

    def test_binding_bool_does_not_equal_integer_identity(self):
        expected = {"pid": 1, "start_ticks": "10"}
        self.assertFalse(outer._typed_equal({"pid": True, "start_ticks": "10"}, expected))

    def test_return_pipe_backpressure_is_never_retried(self):
        obj = self.assembler()
        with self.join_runtime(obj), \
             mock.patch.object(os, "write", side_effect=BlockingIOError()) as write, \
             mock.patch.object(os, "close") as close:
            obj._send_once()
            with self.assertRaises(outer.OuterError):
                obj._send_once()
        write.assert_called_once()
        close.assert_called_once_with(80)
        self.assertEqual(obj.receipt_status, "UNKNOWN")
        self.assertTrue(obj.receipt_attempted)
        self.assertIsNotNone(obj.receipt_bytes)

    def test_partial_return_write_is_unknown_and_not_repeated(self):
        obj = self.assembler()
        with self.join_runtime(obj), mock.patch.object(os, "write", return_value=1) as write, \
             mock.patch.object(os, "close"):
            obj._send_once()
        write.assert_called_once()
        self.assertEqual(obj.receipt_status, "UNKNOWN")

    def test_uncertain_return_eof_cannot_confirm_delivery(self):
        obj = self.assembler()
        with self.join_runtime(obj), mock.patch.object(os, "write", side_effect=lambda fd, raw: len(raw)), \
             mock.patch.object(os, "close", side_effect=OSError(5, "uncertain")) as close:
            obj._send_once()
            obj.pipes.parent["capture_result"].close()
        close.assert_called_once_with(80)
        self.assertEqual(obj.receipt_status, "UNKNOWN")

    def test_return_write_cannot_reopen_join_budget(self):
        obj = self.assembler()
        def late_write(fd, raw):
            self.now = obj.budget.join_ns + 1
            return len(raw)
        with self.join_runtime(obj), mock.patch.object(os, "write", side_effect=late_write) as write, \
             mock.patch.object(os, "close") as close:
            obj._send_once()
        write.assert_called_once()
        close.assert_called_once_with(80)
        self.assertEqual(obj.receipt_status, "UNKNOWN")

    def test_return_eof_after_join_is_not_delivery_success(self):
        obj = self.assembler()
        def late_close(fd):
            self.now = obj.budget.join_ns + 1
        with self.join_runtime(obj), mock.patch.object(os, "write", side_effect=lambda fd, raw: len(raw)), \
             mock.patch.object(os, "close", side_effect=late_close):
            obj._send_once()
        self.assertEqual(obj.receipt_status, "UNKNOWN")
        self.assertTrue(any("after original join" in error for error in obj.errors))

    def test_phase_partial_tail_at_eof_becomes_unknown(self):
        obj = self.assembler()
        obj.collector.phase = b'{"partial":'
        obj.collector.phase_eof = True
        with self.assertRaisesRegex(outer.OuterError, "partial final record"):
            obj._read_phase()
        self.assertEqual(obj.collector.phase_original(), b'{"partial":')

    def test_missing_direct_child_wait_is_not_fabricated_or_retried(self):
        obj = self.assembler()
        with mock.patch.object(os, "waitpid", side_effect=ChildProcessError(), create=True) as wait:
            obj._poll_child_wait()
            obj._poll_child_wait()
        wait.assert_called_once_with(200, os.WNOHANG)
        self.assertTrue(obj.child_wait_lost)
        self.assertIsNone(obj.child_wait)

    def test_watchdog_does_not_fabricate_failure_after_all_exits(self):
        obj = self.assembler()
        for item in obj.processes.values():
            item.exit_ns = self.now
        self.now = obj.budget.cleanup_ns - outer.NS
        with mock.patch.object(signal, "pidfd_send_signal", create=True) as send:
            obj._watchdog()
        send.assert_not_called()
        self.assertEqual(obj.errors, [])

    def test_watchdog_uses_each_authenticated_handle_once_per_signal(self):
        obj = self.assembler()
        for item in obj.processes.values():
            item.exit_ns = None
        obj.processes["worker"].authenticated = False
        self.now = obj.budget.cleanup_ns - outer.NS
        with mock.patch.object(outer, "_pidfd_exited", return_value=False), \
             mock.patch.object(signal, "pidfd_send_signal", create=True) as send, \
             mock.patch.object(os, "kill", create=True) as numeric:
            obj._watchdog()
            obj._watchdog()
        self.assertEqual({call.args[0] for call in send.call_args_list}, {70, 71, 72})
        self.assertEqual(send.call_count, 3)
        numeric.assert_not_called()


class FixtureParser:
    """Test-only role parser, explicitly not the private Observer65 consumer."""
    MAX_SUPERVISOR_LINE = 1024

    @staticmethod
    def parse_supervisor(raw, expected):
        assert raw and raw.endswith(b"\n")
        processes, workers = {}, {}
        for line in raw.splitlines():
            item = json.loads(line)
            assert item["context"] == expected
            current = processes.setdefault(str(item["pid"]), {"seq": 0, "result": None})
            assert type(item["seq"]) is int and item["seq"] == current["seq"] + 1
            current["seq"] = item["seq"]
            if item["event"] == "PROCESS_BOUND":
                key = str(item["binding"]["pid"])
                previous = workers.setdefault(key, {"binding": item["binding"], "roles": [],
                    "deadline_monotonic_ns": item["deadline_monotonic_ns"]})
                assert previous["binding"] == item["binding"] and item["role"] not in previous["roles"]
                previous["roles"].append(item["role"])
            elif item["event"] == "RESULT":
                current["result"] = {"status": item["status"]}
            else:
                raise AssertionError("fixture protocol changed")
        return {"worker_bindings": workers, "processes": processes, "pending_io": False}


def persist_fixture(path, raw):
    with Path(path).open("xb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def fixture_wait(marker, budget, *, cap=None):
    while time.monotonic_ns() < (budget.operation_ns if cap is None else cap):
        if marker.exists():
            return
        if (marker.parent / "stop").exists():
            raise RuntimeError("fixture stop requested")
        time.sleep(.005)
    raise RuntimeError("fixture inherited deadline expired")


def fixture_binding(pid, parent):
    child = core.proc_identity(pid)
    parent_identity = core.proc_identity(parent)
    return {"pid": pid, "start_ticks": str(child["start_ticks"]), "parent_pid": parent,
            "parent_start_ticks": str(parent_identity["start_ticks"]),
            "session_id": child["session_id"], "process_group_id": child["process_group_id"]}


def fixture_worker(plan):
    budget = core.Budget(plan["start_mono_ns"])
    binding = fixture_binding(os.getpid(), os.getppid())
    if plan["mode"] != "missing-worker-role":
        os.write(2, frame({"schema": "source-fixture-supervisor/1", "pid": os.getpid(), "seq": 1,
            "mono_ns": time.monotonic_ns(), "event": "PROCESS_BOUND", "context": plan["context"],
            "role": "WORKER_SELF", "binding": binding, "deadline_monotonic_ns": budget.operation_ns}))
    fixture_wait(Path(plan["root"]) / "allow-worker-exit", budget)
    return 0


def fixture_cli(plan, plan_path):
    budget = core.Budget(plan["start_mono_ns"])
    worker = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
        "--fixture-worker", str(plan_path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=2, close_fds=True, start_new_session=True)
    binding = fixture_binding(worker.pid, os.getpid())
    os.write(2, frame({"schema": "source-fixture-supervisor/1", "pid": os.getpid(), "seq": 1,
        "mono_ns": time.monotonic_ns(), "event": "PROCESS_BOUND", "context": plan["context"],
        "role": "PARENT_OBSERVED_WORKER", "binding": binding, "deadline_monotonic_ns": budget.operation_ns}))
    observed, status = os.waitpid(worker.pid, 0)
    assert observed == worker.pid
    worker.returncode = os.waitstatus_to_exitcode(status)
    assert worker.returncode == 0
    os.write(2, frame({"schema": "source-fixture-supervisor/1", "pid": os.getpid(), "seq": 2,
        "mono_ns": time.monotonic_ns(), "event": "RESULT", "context": plan["context"],
        "status": "OBSERVED_TARGET_SNAPSHOT"}))
    os.write(1, b'{"scope":"SOURCE_FIXTURE_ONLY","worker_exit_code":0}\n')
    return 0


def fixture_consumer(plan, plan_path):
    budget = core.Budget(plan["start_mono_ns"])
    root = Path(plan["root"])
    endpoints = {name: outer.OwnedDescriptor(item["fd"], "fixture:" + name)
                 for name, item in plan["channels"].items()}
    sequence, handle, waited, cli_pidfd = 0, None, None, None
    wait_lost, status_readable, fixture_errors = False, True, []
    raw, eof, launch = bytearray(), False, None

    def emit(event, phase, **detail):
        nonlocal sequence
        sequence += 1
        packet = frame({"schema": outer.PHASE_SCHEMA, "operation_id": plan["operation_id"],
            "pid": os.getpid(), "seq": sequence, "mono_ns": time.monotonic_ns(),
            "event": event, "phase": phase, "detail": detail})
        assert os.write(endpoints["phase"].fd, packet) == len(packet)

    def status_step():
        nonlocal eof, launch, waited, status_readable, wait_lost
        if not eof and status_readable:
            try:
                chunk = os.read(handle.status_fd, 65536)
                if chunk:
                    raw.extend(chunk)
                    if len(raw) > core.STATUS_LIMIT:
                        status_readable = False
                        fixture_errors.append("supervisor status exceeds finite budget")
                else:
                    eof = True
            except BlockingIOError:
                pass
            except OSError as error:
                status_readable = False
                fixture_errors.append("status read:" + type(error).__name__)
        if status_readable and launch is None and b"\n" in raw:
            try:
                launch = core.decode_frame(bytes(raw[:raw.index(b"\n")+1]), eof=True, limit=core.FRAME_LIMIT)
            except core.CaptureError as error:
                status_readable = False
                fixture_errors.append("invalid supervisor launch frame:" + type(error).__name__)
        if waited is None and not wait_lost:
            try:
                pid, status = os.waitpid(handle.pid, os.WNOHANG)
            except OSError as error:
                wait_lost = True
                fixture_errors.append("original supervisor wait unavailable:" + type(error).__name__)
                return
            if pid:
                if pid != handle.pid:
                    wait_lost = True
                    fixture_errors.append("foreign supervisor wait")
                    return
                waited = {"pid": pid, "status": status, "code": os.waitstatus_to_exitcode(status),
                          "mono_ns": time.monotonic_ns()}

    try:
        emit("ENTER", "observer-supervisor-start", fixture_only=True)
        try:
            handle = core.start_supervisor([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                "--fixture-cli", str(plan_path)], dict(os.environ), endpoints["observer_stdout"].fd,
                endpoints["observer_stderr"].fd, budget, mirror_fd=endpoints["observer_subtree_status"].fd,
                protected_fds=(endpoints["phase"].fd, endpoints["capture_result"].fd))
        except core.RetainedChildError as error:
            handle = types.SimpleNamespace(pid=error.pid, start_ticks=None, status_fd=error.status_fd,
                pidfd=error.pidfd, ack_fd=-1, abort=lambda: None)
            fixture_errors.append("retained supervisor start error:" + type(error).__name__)
            try:
                os.set_blocking(handle.status_fd, False)
            except (OSError, TypeError):
                status_readable = False
            raise
        for name in ("observer_stdout", "observer_stderr", "observer_subtree_status"):
            if name != "observer_subtree_status" or plan["mode"] != "missing-fifth-eof":
                assert endpoints[name].close()
        emit("RETURN", "observer-supervisor-start", returned_pid=handle.pid)
        while launch is None and time.monotonic_ns() < budget.operation_ns:
            status_step()
            time.sleep(.005)
        assert launch is not None and launch["kind"] == "LAUNCH_BOUND"
        cli = core.proc_identity(launch["cli"]["pid"])
        assert cli["parent_pid"] == handle.pid
        cli_pidfd = outer.OwnedDescriptor(os.pidfd_open(cli["pid"], 0), "fixture-cli:pidfd")
        assert core.proc_identity(cli["pid"]) == cli and not outer._pidfd_exited(cli_pidfd.fd)
        # These two markers belong only to fixtures. A2 has no outer pre-G ACK.
        fixture_wait(root / "allow-exec", budget)
        handle.admit_exec()
        while (waited is None or not eof) and time.monotonic_ns() < budget.join_ns:
            status_step()
            if (root / "stop").exists():
                handle.abort()
            time.sleep(.005)
        assert waited is not None and eof and waited["code"] == 0
        core.validate_transcript(bytes(raw), eof=True, expected_supervisor=launch["supervisor"],
            expected_cli=launch["cli"], deadline_ns=budget.join_ns)
        if plan["mode"] == "closed-result-reader":
            assert endpoints["capture_result"].close()
        emit("RETURN", "observer-supervisor-wait", supervisor_pid=handle.pid,
             supervisor_start_ticks=handle.start_ticks, actual_wait_status=waited["status"],
             actual_exit_code=1 if plan["mode"] == "forged-wait" else waited["code"],
             observed_monotonic_ns=waited["mono_ns"])
        if plan["mode"] == "closed-result-reader":
            fixture_wait(root / "allow-phase-close", budget, cap=budget.cleanup_ns)
            return 2
        received, ended = bytearray(), False
        while time.monotonic_ns() < budget.join_ns and not ended and not (root / "stop").exists():
            try:
                data = os.read(endpoints["capture_result"].fd, 16384)
                if data:
                    received.extend(data)
                    assert len(received) <= 131072
                else:
                    ended = True
            except BlockingIOError:
                pass
            time.sleep(.005)
        persist_fixture(root / "capture-result.received.original", bytes(received))
        if not ended or not received:
            return 2
        value = core.decode_frame(bytes(received), eof=True, limit=131072)
        assert value["complete"] is True and value["operation_id"] == plan["operation_id"]
        emit("RETURN", "fixture-capture-result", original_sha256=outer.sha256(received), eof_observed=True)
        fixture_wait(root / "allow-phase-close", budget, cap=budget.cleanup_ns)
        return 0
    finally:
        if handle is not None:
            try:
                handle.abort()
            except OSError as error:
                fixture_errors.append("ACK close uncertain:" + type(error).__name__)
            while waited is None and not wait_lost and time.monotonic_ns() < budget.cleanup_ns:
                try:
                    status_step()
                except (OSError, core.CaptureError) as error:
                    fixture_errors.append("supervisor cleanup:" + type(error).__name__)
                    status_readable = False
                time.sleep(.005)
            if waited is not None:
                for fd in (handle.status_fd, handle.pidfd):
                    if fd is not None and fd >= 0:
                        if not outer.OwnedDescriptor(fd, "fixture-supervisor-owned").close():
                            fixture_errors.append("supervisor descriptor close uncertain")
                if cli_pidfd is not None:
                    if not cli_pidfd.close():
                        fixture_errors.append("CLI descriptor close uncertain")
            else:
                fixture_errors.append("supervisor terminal unresolved at original C")
        for item in endpoints.values():
            if not item.close():
                fixture_errors.append("input descriptor close uncertain:" + item.label)
        persist_fixture(root / "fixture-owner-cleanup.json", frame({"wait": waited,
            "status_eof": eof, "wait_lost": wait_lost, "errors": fixture_errors,
            "handle": None if handle is None else {"pid": handle.pid, "pidfd": handle.pidfd,
                "status_fd": handle.status_fd}, "scope": "SOURCE_FIXTURE_ONLY"}))
        if fixture_errors:
            raise RuntimeError("fixture cleanup uncertain:" + repr(fixture_errors))


LINUX_READY = sys.platform.startswith("linux") and hasattr(os, "fork") and hasattr(os, "pidfd_open")


@unittest.skipUnless(LINUX_READY, "real Linux process and pipe fixture")
class LinuxOuterTests(unittest.TestCase):
    def run_fixture(self, mode):
        root = Path(tempfile.mkdtemp(prefix="startup-outer-fixture-"))
        root.chmod(0o700)
        capture = root / "capture"
        capture.mkdir(mode=0o700)
        # The EOF negative deliberately inherits a partly consumed origin; no
        # deadline is reopened. It retains 4s operation, 6s join and 12s cleanup.
        started = time.monotonic_ns() - (20 * outer.NS if mode == "missing-fifth-eof" else 0)
        budget = core.Budget(started)
        operation_id = str(uuid.uuid4())
        with mock.patch.object(outer, "_load_observer_parser", return_value=FixtureParser):
            obj = outer.OuterAssembler(capture, operation_id, "source-fixture-" + operation_id,
                budget, observer_source=Path(__file__).resolve())
        plan = {"mode": mode, "root": str(root), "start_mono_ns": started,
                "operation_id": operation_id, "channels": obj.pipes.channels,
                "context": {"observer_sha256": outer.OBSERVER_SHA256, "admission_sha256": "f" * 64}}
        plan_path = root / "fixture-plan.json"
        actual_source = Path(__file__).resolve()
        stdio = []
        observed_phase_open = False
        report = None
        try:
            persist_fixture(plan_path, frame(plan))
            for name in ("stdout", "stderr"):
                fd = os.open(root / ("consumer." + name + ".original"),
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                stdio.append(outer.OwnedDescriptor(fd, "fixture-consumer-log:" + name))
            obj.launch([sys.executable, "-I", "-B", str(actual_source), "--fixture-consumer", str(plan_path)],
                source_sha256=hashlib.sha256(actual_source.read_bytes()).hexdigest(),
                stdout=stdio[0].fd, stderr=stdio[1].fd, env=dict(os.environ))
            while time.monotonic_ns() < budget.cleanup_ns:
                obj.step()
                if all(role in obj.processes and obj.processes[role].authenticated for role in ("supervisor", "cli")):
                    if not (root / "allow-exec").exists():
                        persist_fixture(root / "allow-exec", b"fixture-only barrier\n")
                if "worker" in obj.processes and obj.processes["worker"].authenticated:
                    if not (root / "allow-worker-exit").exists():
                        persist_fixture(root / "allow-worker-exit", b"fixture-only barrier\n")
                if obj.receipt_status == "WRITTEN_AND_EOF_CLOSED":
                    observed_phase_open |= not obj.collector.phase_metadata()["eof"]
                    if not (root / "allow-phase-close").exists():
                        persist_fixture(root / "allow-phase-close", b"receipt already delivered\n")
                if obj.errors:
                    for name in ("stop", "allow-phase-close"):
                        if not (root / name).exists():
                            persist_fixture(root / name, b"owned fixture cleanup only\n")
                if obj.child_wait is not None:
                    break
                time.sleep(.005)
        finally:
            if obj.child is not None and obj.child_wait is None:
                for name in ("stop", "allow-phase-close"):
                    if not (root / name).exists():
                        persist_fixture(root / name, b"owned fixture cleanup only\n")
            try:
                report = obj.finish()
            finally:
                for stream in stdio:
                    if not stream.close():
                        obj._error("fixture log descriptor close uncertain:" + stream.label)
            report = obj.snapshot()
            persist_fixture(root / "phase.original", obj.collector.phase_original())
            for original, channel in outer.ORIGINAL_CHANNELS.items():
                path = root / ("partial-" + original)
                persist_fixture(path, obj._bytes(channel))
            report["source_only_fixture"] = True
            report["owner_semantic_parser_exercised"] = False
            report["fixture_barriers_are_not_A2_protocol"] = True
            report["phase_open_at_receipt_delivery"] = observed_phase_open
            persist_fixture(root / "result.json", frame(report))
        self.assertIsNotNone(obj.child_wait, str(root))
        self.assertIsNotNone(report["consumer_original_wait"], str(root))
        self.assertTrue(all(not item["pidfd"]["retained"] for item in report["retained_processes"]
                            if item["identity_authenticated"]), str(root))
        return root, obj, report

    def test_five_pipes_actual_writer_wait_and_receipt_before_phase_eof(self):
        root, obj, report = self.run_fixture("complete")
        self.assertEqual(report["status"], "CAPTURE_CUSTODY_COMPLETE", str(root) + repr(report["errors"]))
        self.assertEqual(report["consumer_original_wait"]["exit_code"], 0)
        self.assertTrue(report["phase_open_at_receipt_delivery"])
        self.assertEqual((root / "capture-result.received.original").read_bytes(), obj.receipt_bytes)
        self.assertEqual(report["collector"]["writer_wait"]["exit_code"], 0)
        for name, raw in obj.collector.originals.items():
            self.assertEqual((root / "capture" / name).read_bytes(), raw)
        self.assertFalse(report["outer_pre_G_retention_claimed"])
        self.assertEqual(report["installed_result"], "NOT_RUN")
        self.assertEqual(json.loads((root / "fixture-owner-cleanup.json").read_bytes())["errors"], [])

    def test_closed_actual_return_reader_does_not_retry_delivery(self):
        root, obj, report = self.run_fixture("closed-result-reader")
        self.assertEqual(report["status"], "UNKNOWN", str(root))
        self.assertTrue(report["capture_result_attempted"])
        self.assertEqual(report["capture_result_status"], "UNKNOWN")
        self.assertTrue(any("write unresolved" in error for error in report["errors"]))

    def test_actual_fifth_proof_does_not_override_forged_wait_phase(self):
        root, obj, report = self.run_fixture("forged-wait")
        self.assertEqual(report["status"], "UNKNOWN", str(root))
        self.assertFalse(report["capture_result_attempted"])
        self.assertTrue(any("original wait" in error for error in report["errors"]))
        self.assertIn(b'"actual_exit_code":1', (root / "phase.original").read_bytes())

    def test_actual_worker_without_both_roles_does_not_produce_complete_receipt(self):
        root, obj, report = self.run_fixture("missing-worker-role")
        self.assertEqual(report["status"], "UNKNOWN", str(root))
        self.assertFalse(report["capture_result_attempted"])
        self.assertTrue(any("roles" in error for error in report["errors"]))

    def test_missing_fifth_eof_uses_original_deadline_without_starting_writer(self):
        root, obj, report = self.run_fixture("missing-fifth-eof")
        self.assertEqual(report["status"], "UNKNOWN", str(root))
        self.assertFalse(report["collector"]["writer_attempted"])
        self.assertFalse(report["capture_result_attempted"])
        self.assertEqual(report["join_deadline_ns"] - report["start_monotonic_ns"], 26 * outer.NS)
        terminal = json.loads(obj._bytes("observer_subtree_status").splitlines()[-1])
        self.assertEqual(terminal["errors"], [], str(root))
        self.assertTrue(terminal["process_tree_closed"], str(root))
        self.assertTrue(any("deadline" in error for error in report["collector"]["errors"]), str(root))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1].startswith("--fixture-"):
        fixture_plan_path = Path(sys.argv[2])
        fixture_plan = json.loads(fixture_plan_path.read_bytes())
        function = {"--fixture-consumer": lambda: fixture_consumer(fixture_plan, fixture_plan_path),
                    "--fixture-cli": lambda: fixture_cli(fixture_plan, fixture_plan_path),
                    "--fixture-worker": lambda: fixture_worker(fixture_plan)}[sys.argv[1]]
        raise SystemExit(function())
    unittest.main()
