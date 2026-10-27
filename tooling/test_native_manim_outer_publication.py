"""Original-Popen publication controls: inert local Python only, no native credit."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import native_manim_editable_outer as outer


@unittest.skipUnless(sys.platform == "linux", "original process-group controls require Linux")
class OuterPublicationTests(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get("MANIM_OUTER_PUBLICATION_TEST_ROOT")
        if retained:
            self.root = Path(retained).resolve(strict=True) / self._testMethodName
            self.root.mkdir(mode=0o700, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(prefix="manim-outer-publication-")
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name).resolve(strict=True)
        self.repo = self.root / "source-fixture"
        self.repo.mkdir(mode=0o700)
        self.control = self.root / "control"
        self.control.mkdir(mode=0o700)
        (self.control / "work").mkdir(mode=0o700)
        (self.control / "closing").mkdir(mode=0o700)
        context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                   "run_id": "17", "run_attempt": "1"}
        started = max(1, time.monotonic_ns() - (outer.WORK_SECONDS - 10) * outer.NANOSECOND)
        record = outer.origin_record(context, started, outer.boot_identity())
        origin_sha = outer.new_json(self.control / "origin.json", record)
        self.deadline = outer.OuterDeadline(record, context, record["boot_id"],
            origin_sha256=origin_sha, root=self.control)
        self.processes = []
        self.signals = []
        for name, value in (("_OUTER_LAUNCH_CRITICAL", 0), ("_OUTER_CANCEL_SIGNAL", None),
                            ("_OUTER_CANCEL_DELIVERED", False)):
            guard = patch.object(outer, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous = signal.signal(signum, outer.workflow_cancelled)
            self.addCleanup(signal.signal, signum, previous)
        outer.new_json(self.root / "SOURCE_CONTROL.json", {
            "kind": "REAL_INERT_PYTHON_PUBLICATION_CONTROL_NOT_MANIM_EVIDENCE",
            "synthetic_source_context": context, "product_renderer_executed": False,
            "network_used": False, "cargo_used": False,
        })

    def tearDown(self):
        # Safety cleanup is recorded separately and uses only retained Popen
        # objects. It cannot count as the product owner's cleanup observation.
        safety = []
        for process in self.processes:
            needed = process.poll() is None
            if needed:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            safety.append({"pid": process.pid, "test_safety_cleanup_needed": needed,
                           "observed_returncode": process.returncode})
        outer.new_json(self.root / "TEST_SAFETY_CLEANUP.json", safety)

    def argv(self, *, exit_code=None):
        ready = self.root / "child-ready.json"
        code = (
            "import json,os,sys,time\nfrom pathlib import Path\n"
            "print('INERT_ORIGINAL_STDOUT',flush=True)\n"
            "print('INERT_ORIGINAL_STDERR',file=sys.stderr,flush=True)\n"
            "Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'sid':os.getsid(0)}))\n"
        )
        code += "time.sleep(10)\n" if exit_code is None else f"raise SystemExit({exit_code})\n"
        return [sys.executable, "-B", "-u", "-c", code, str(ready)]

    def spawn(self, argv, **kwargs):
        process = subprocess.Popen(argv, **kwargs)
        self.processes.append(process)
        ready = self.root / "child-ready.json"
        end = time.monotonic() + 3
        while not ready.is_file() and time.monotonic() < end:
            time.sleep(0.005)
        self.assertTrue(ready.is_file(), "inert original did not publish readiness")
        identity = json.loads(ready.read_bytes())
        self.assertEqual(identity, {"pid": process.pid, "sid": process.pid})
        return process

    def signal_original(self, pid, signum):
        self.signals.append((pid, int(signum)))
        os.killpg(pid, signum)

    def run_original(self, *, spawn=None, killpg=None, exit_code=None):
        return outer.run_command(self.deadline, "native-wrapper", self.argv(exit_code=exit_code),
            self.repo, popen=spawn or self.spawn, killpg=killpg or self.signal_original)

    def terminal(self, failure):
        receipt = failure.receipt
        path = self.control / "work/native-wrapper/terminal.json"
        self.assertEqual(json.loads(path.read_bytes()), receipt)
        self.assertFalse(receipt["automatic_retry"])
        for name, suffix in (("stdout", ".log"), ("stderr", ".stderr.log")):
            raw = (self.root / ("manim-editable-cycle-wrapper" + suffix)).read_bytes()
            self.assertEqual(receipt["streams"][name], {
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        return receipt

    def test_cancel_before_attempt_prevents_every_new_launch(self):
        with self.assertRaises(InterruptedError):
            os.kill(os.getpid(), signal.SIGTERM)
        with patch.object(outer.subprocess, "Popen") as forbidden:
            for label in ("native-wrapper", "build-product"):
                with self.assertRaisesRegex(InterruptedError, "signal " + str(signal.SIGTERM)):
                    outer.run_command(self.deadline, label, ["forbidden"], self.repo)
            forbidden.assert_not_called()
        self.assertFalse(any((self.control / "work").iterdir()))
        self.assertFalse((self.root / "manim-editable-cycle-wrapper.log").exists())

    def test_signal_before_spawn_is_rechecked_inside_publication_guard(self):
        original_check = outer.raise_deferred_workflow_cancellation
        injected = []
        def checked():
            if outer._OUTER_LAUNCH_CRITICAL and not injected:
                injected.append(True)
                os.kill(os.getpid(), signal.SIGTERM)
            return original_check()
        with patch.object(outer, "raise_deferred_workflow_cancellation", side_effect=checked), \
                patch.object(outer.subprocess, "Popen") as forbidden, \
                self.assertRaises(outer.OriginalCommandError) as raised:
            self.run_original()
        forbidden.assert_not_called()
        self.assertEqual(injected, [True])
        receipt = self.terminal(raised.exception)
        self.assertEqual((receipt["status"], receipt["pid"], receipt["original_exit"]),
                         ("UNKNOWN", None, None))
        self.assertFalse(receipt["termination_requested"])
        self.assertEqual(outer._OUTER_LAUNCH_CRITICAL, 0)

    def test_signal_during_return_publication_retains_handle_and_cleanup_once(self):
        def spawn(argv, **kwargs):
            process = self.spawn(argv, **kwargs)
            os.kill(os.getpid(), signal.SIGTERM)
            return process
        def killpg(pid, signum):
            # Repetition during cleanup must not replace the first delivered
            # cancellation or prevent the owner's one termination observation.
            os.kill(os.getpid(), signal.SIGINT)
            self.signal_original(pid, signum)
        with self.assertRaises(outer.OriginalCommandError) as raised:
            self.run_original(spawn=spawn, killpg=killpg)
        receipt = self.terminal(raised.exception)
        self.assertEqual(len(self.processes), 1)
        process = self.processes[0]
        self.assertEqual((receipt["status"], receipt["pid"], receipt["original_exit"]),
                         ("UNKNOWN", process.pid, -signal.SIGTERM))
        self.assertTrue(receipt["process_exit_known"])
        self.assertEqual(self.signals, [(process.pid, int(signal.SIGTERM))])
        self.assertEqual(outer._OUTER_CANCEL_SIGNAL, signal.SIGTERM)
        self.assertIsInstance(raised.exception.__cause__, InterruptedError)
        self.assertIn("signal " + str(signal.SIGTERM), str(raised.exception.__cause__))
        with patch.object(outer.subprocess, "Popen") as forbidden, \
                self.assertRaises(InterruptedError):
            outer.run_command(self.deadline, "build-product", ["forbidden"], self.repo)
        forbidden.assert_not_called()
        self.assertIsNotNone(process.returncode)

    def test_signal_after_handle_publication_keeps_start_and_terminal(self):
        original_write = outer.new_json
        injected = []
        def write(path, value):
            result = original_write(path, value)
            if path.name == "start.json" and not injected:
                injected.append(True)
                os.kill(os.getpid(), signal.SIGTERM)
            return result
        with patch.object(outer, "new_json", side_effect=write), \
                self.assertRaises(outer.OriginalCommandError) as raised:
            self.run_original()
        receipt = self.terminal(raised.exception)
        start = json.loads((self.control / "work/native-wrapper/start.json").read_bytes())
        self.assertEqual((len(self.processes), receipt["pid"], start["pid"]),
                         (1, self.processes[0].pid, self.processes[0].pid))
        self.assertEqual(receipt["original_exit"], -signal.SIGTERM)
        self.assertEqual(self.signals, [(self.processes[0].pid, int(signal.SIGTERM))])
        self.assertEqual(receipt["status"], "UNKNOWN")

    def test_spawn_error_precedes_latched_cancel_without_handle_or_replay(self):
        primary = OSError("INERT_ORIGINAL_SPAWN_FAILURE")
        attempts = []
        def spawn(*_args, **_kwargs):
            attempts.append(True)
            os.kill(os.getpid(), signal.SIGTERM)
            raise primary
        original_write = outer.new_json
        def write(path, value):
            if path.name == "terminal.json":
                os.kill(os.getpid(), signal.SIGINT)
            return original_write(path, value)
        with patch.object(outer, "new_json", side_effect=write), \
                self.assertRaises(outer.OriginalCommandError) as raised:
            self.run_original(spawn=spawn)
        receipt = self.terminal(raised.exception)
        self.assertIs(raised.exception.__cause__, primary)
        self.assertEqual(receipt["original_error"], "OSError")
        self.assertEqual((receipt["status"], receipt["pid"], receipt["original_exit"]),
                         ("UNKNOWN", None, None))
        self.assertFalse(receipt["process_exit_known"])
        self.assertFalse(receipt["termination_requested"])
        self.assertEqual((attempts, self.signals, self.processes), ([True], [], []))
        self.assertEqual(outer._OUTER_CANCEL_SIGNAL, signal.SIGTERM)
        with self.assertRaises(InterruptedError):
            self.run_original(spawn=spawn)
        self.assertEqual(attempts, [True])

    def spawn_unwind_signal(self, *, already_latched):
        """Inject after guard exit, before the outer except records the error."""
        primary = OSError("INERT_PRIMARY_SPAWN_UNWIND_FAILURE")
        attempts, seen_error, injected = [], [], []
        def spawn(*_args, **_kwargs):
            attempts.append(True)
            if already_latched:
                os.kill(os.getpid(), signal.SIGTERM)
            raise primary
        def trace(frame, event, argument):
            if frame.f_code is outer.run_command.__code__:
                if event == "exception" and argument[1] is primary:
                    seen_error.append(True)
                if event == "line" and seen_error and not injected \
                        and outer._OUTER_LAUNCH_CRITICAL == 0:
                    # Trace observes the actual source unwind. No production
                    # helper or exception handler is replaced for this seam.
                    self.assertIsNone(frame.f_locals.get("caught"))
                    injected.append(True)
                    os.kill(os.getpid(), signal.SIGINT if already_latched else signal.SIGTERM)
            return trace
        previous = sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(outer.OriginalCommandError) as raised:
                self.run_original(spawn=spawn)
        finally:
            sys.settrace(previous)
        receipt = self.terminal(raised.exception)
        self.assertEqual(injected, [True])
        self.assertIs(raised.exception.__cause__, primary)
        self.assertEqual(receipt["original_error"], "OSError")
        self.assertEqual((receipt["status"], receipt["pid"], receipt["original_exit"]),
                         ("UNKNOWN", None, None))
        self.assertFalse(receipt["process_exit_known"])
        self.assertFalse(receipt["termination_requested"])
        self.assertEqual((attempts, self.processes, self.signals), ([True], [], []))
        self.assertEqual(outer._OUTER_CANCEL_SIGNAL, signal.SIGTERM)
        self.assertEqual(outer._OUTER_LAUNCH_CRITICAL, 0)
        with self.assertRaises(InterruptedError):
            self.run_original(spawn=spawn)
        self.assertEqual(attempts, [True])

    def test_repeat_signal_during_spawn_unwind_keeps_original_error(self):
        self.spawn_unwind_signal(already_latched=True)

    def test_first_signal_during_spawn_unwind_keeps_original_error(self):
        self.spawn_unwind_signal(already_latched=False)

    def test_late_signal_after_observed_exit_never_resignals_original(self):
        waited, injected = [], []
        def spawn(argv, **kwargs):
            process = self.spawn(argv, **kwargs)
            original_wait = process.wait
            def wait(*, timeout):
                result = original_wait(timeout=timeout)
                waited.append(result)
                return result
            process.wait = wait
            return process
        remaining = self.deadline.remaining
        def budget(stage="work", cap=None):
            if waited and not injected:
                injected.append(True)
                os.kill(os.getpid(), signal.SIGTERM)
            return remaining(stage, cap)
        with patch.object(self.deadline, "remaining", side_effect=budget), \
                self.assertRaises(outer.OriginalCommandError) as raised:
            self.run_original(spawn=spawn, exit_code=23)
        receipt = self.terminal(raised.exception)
        self.assertEqual((waited, self.signals), ([23], []))
        self.assertEqual((receipt["status"], receipt["original_exit"]), ("UNKNOWN", 23))
        self.assertTrue(receipt["process_exit_known"])
        self.assertFalse(receipt["termination_requested"])
        self.assertEqual(receipt["native_result_claim"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
