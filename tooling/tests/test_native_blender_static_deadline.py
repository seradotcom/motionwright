"""Original-job clock and admission controls; no renderer, Cargo or GitHub run.

Linux process controls use only explicitly synthetic children. Runner identity,
job times and every native response are synthetic and cannot grant production.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import native_blender_e2e as wrapper


def wiring_module():
    source = (ROOT / ".github/workflows/native-blender-e2e.yml").read_text()
    marker = '        cat > "$RUNNER_TEMP/blender-static-custody.py" <<\'PY\'\n'
    if source.count(marker) != 1:
        raise AssertionError("one source-pinned static helper is required")
    body = textwrap.dedent(source.split(marker, 1)[1].split("        PY\n", 1)[0])
    module = types.ModuleType("_static_original_deadline_unit")
    exec(compile(body, "native-blender-e2e.yml:deadline", "exec"), module.__dict__)
    return module, body


class StaticOriginalDeadlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wiring, cls.body = wiring_module()
        cls.context = {"GITHUB_REPOSITORY": "seradotcom/motionwright",
                       "GITHUB_JOB": "native-blender-roundtrip", "GITHUB_SHA": "1" * 40,
                       "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                       "GITHUB_REF": "refs/heads/synthetic-source-only",
                       "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/.github/workflows/native-blender-e2e.yml@refs/heads/synthetic-source-only"}

    def observation(self, *, before=200, after=201):
        worker = {"pid": 100, "parent_pid": 99, "start_ticks": 10000,
                  "uid": [1001] * 4, "comm": "Runner.Worker", "executable": "/opt/runner/bin/Runner.Worker"}
        shell = {"pid": 200, "parent_pid": 100, "start_ticks": 11000,
                 "uid": [1001] * 4, "comm": "bash", "executable": "/usr/bin/bash"}
        python = {"pid": 300, "parent_pid": 200, "start_ticks": 12000,
                  "uid": [1001] * 4, "comm": "python3", "executable": "/usr/bin/python3.12"}
        return {"clock": "CLOCK_BOOTTIME", "clock_ticks_per_second": 100,
                "boot_id": "11111111-2222-3333-4444-555555555555",
                "observed_before_ns": before * 1_000_000_000,
                "observed_after_ns": after * 1_000_000_000,
                "worker": worker, "ancestors": [python, shell, worker]}

    def proof(self):
        return {"schema": "motionwright.blender.static-original-deadline/1",
                "status": "ORIGINAL_ANCHOR_OBSERVED", "context": dict(self.context),
                "budget_seconds": self.wiring.budget_seconds(),
                "wiring_sha256": hashlib.sha256(self.body.encode()).hexdigest(),
                "observation": self.observation(), "worker_start_boottime_ns": 100_000_000_000,
                "original_deadline_boottime_ns": 2_800_000_000_000}

    def test_ancestor_start_anchors_deadline_before_all_setup_and_is_not_now_plus_45_minutes(self):
        wiring = self.wiring
        observed = self.observation()
        rows = {row["pid"]: row for row in observed["ancestors"]}
        with patch.object(wiring, "clock_ns", side_effect=[200_000_000_000, 201_000_000_000]), \
             patch.object(wiring.os, "getpid", return_value=300), \
             patch.object(wiring.os, "sysconf", return_value=100), \
             patch.object(wiring, "bounded_original", return_value=observed["boot_id"].encode() + b"\n"), \
             patch.object(wiring, "process_identity", side_effect=lambda pid: copy.deepcopy(rows[pid])) as read:
            actual = wiring.observe_worker()
        self.assertEqual(actual, observed)
        self.assertEqual([call.args[0] for call in read.call_args_list], [300, 200, 100])
        proof = self.proof()
        self.assertEqual(proof["original_deadline_boottime_ns"],
                         observed["worker"]["start_ticks"] * 10_000_000 + 2700 * 1_000_000_000)
        self.assertNotEqual(proof["original_deadline_boottime_ns"], observed["observed_after_ns"] + 2700 * 1_000_000_000)

    def test_missing_worker_parent_cycle_and_future_parent_clock_are_denied_without_fallback(self):
        wiring = self.wiring
        for mutation, code in (("missing", "not-observed"), ("cycle", "cycle"), ("future", "clock-incoherent")):
            with self.subTest(mutation=mutation):
                observed = self.observation()
                rows = {row["pid"]: row for row in observed["ancestors"]}
                if mutation == "missing":
                    rows[100].update(comm="untrusted", executable="/tmp/untrusted", parent_pid=0)
                elif mutation == "cycle":
                    rows[200]["parent_pid"] = 300
                else:
                    rows[100]["start_ticks"] = 30000
                with patch.object(wiring, "clock_ns", return_value=200_000_000_000), \
                     patch.object(wiring.os, "getpid", return_value=300), \
                     patch.object(wiring.os, "sysconf", return_value=100), \
                     patch.object(wiring, "bounded_original", return_value=observed["boot_id"].encode()), \
                     patch.object(wiring, "process_identity", side_effect=lambda pid: copy.deepcopy(rows[pid])):
                    with self.assertRaisesRegex(wiring.StaticDeadlineDenied, code):
                        wiring.observe_worker()

    def test_proc_identity_accepts_changing_counters_but_rejects_uid_and_pid_reuse(self):
        wiring = self.wiring
        def stat_line(start, cpu="0"):
            fields = ["S", "200"] + ["0"] * 17 + [str(start)]
            fields[11] = cpu
            return ("300 (python3) " + " ".join(fields) + "\n").encode()
        status = b"Name:\tpython3\nUid:\t1001\t1001\t1001\t1001\n"
        def check(second, uid_status=status):
            with patch.object(wiring, "bounded_original", side_effect=[stat_line(12000), uid_status, second]), \
                 patch.object(wiring.os, "geteuid", return_value=1001), \
                 patch.object(wiring.os, "readlink", return_value="/usr/bin/python3.12"):
                return wiring.process_identity(300)
        self.assertEqual(check(stat_line(12000, "100"))["start_ticks"], 12000)
        with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "identity-changed"):
            check(stat_line(12001))
        with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "uid-mismatch"):
            check(stat_line(12000), status.replace(b"1001", b"1002"))

    def test_proof_cannot_move_to_another_boot_worker_uid_or_run(self):
        wiring = self.wiring
        for mutation in ("boot", "start", "pid", "uid", "run", "wiring"):
            proof = self.proof()
            observed = self.observation(before=300, after=301)
            context = dict(self.context)
            digest = proof["wiring_sha256"]
            if mutation == "boot": observed["boot_id"] = "different-boot"
            elif mutation == "start": observed["worker"]["start_ticks"] += 1
            elif mutation == "pid": observed["worker"]["pid"] += 1
            elif mutation == "uid": observed["worker"]["uid"] = [1002] * 4
            elif mutation == "run": context["GITHUB_RUN_ID"] = "124"
            else: digest = "0" * 64
            with self.subTest(mutation=mutation), self.assertRaises(wiring.StaticDeadlineDenied):
                wiring.validate_deadline_proof(proof, observed, context, digest)

    def test_original_deadline_cannot_be_renewed_and_clock_cannot_regress(self):
        wiring = self.wiring
        proof = self.proof()
        later = self.observation(before=1500, after=1501)
        self.assertIs(wiring.validate_deadline_proof(proof, later, self.context, proof["wiring_sha256"]), proof)
        self.assertEqual(proof["original_deadline_boottime_ns"], 2_800_000_000_000)
        for mutation in ("deadline", "start", "regress", "tickrate"):
            altered = copy.deepcopy(proof)
            observed = copy.deepcopy(later)
            if mutation == "deadline": altered["original_deadline_boottime_ns"] += 1
            elif mutation == "start": altered["worker_start_boottime_ns"] += 1
            elif mutation == "regress": observed["observed_before_ns"] = 1
            else: observed["clock_ticks_per_second"] = 1000
            with self.subTest(mutation=mutation), self.assertRaises(wiring.StaticDeadlineDenied):
                wiring.validate_deadline_proof(altered, observed, self.context, proof["wiring_sha256"])

    def test_bootstrap_retains_exact_script_and_original_denial_without_overwrite(self):
        wiring = self.wiring
        with tempfile.TemporaryDirectory(prefix="static-clock-denied-") as directory:
            root = Path(directory)
            script = root / "selected.py"
            script.write_bytes(self.body.encode())
            env = {**self.context, "RUNNER_TEMP": str(root)}
            primary = wiring.StaticDeadlineDenied("synthetic worker absent")
            with patch.object(wiring, "__file__", str(script), create=True), \
                 patch.object(wiring, "observe_worker", side_effect=primary):
                with self.assertRaises(wiring.StaticDeadlineDenied) as caught:
                    wiring.deadline_bootstrap(env)
                self.assertIs(caught.exception, primary)
                control = wiring.deadline_control(env)
                original = (control / "deadline.original.json").read_bytes()
                receipt = json.loads(original)
                self.assertEqual(receipt["status"], "DENY")
                self.assertFalse(receipt["native_effects"])
                self.assertEqual((control / "wiring.original.py").read_bytes(), self.body.encode())
                with self.assertRaises(FileExistsError):
                    wiring.deadline_bootstrap(env)
                self.assertEqual((control / "deadline.original.json").read_bytes(), original)

    def test_bootstrap_denies_elapsed_original_job_and_preserves_observed_worker(self):
        wiring = self.wiring
        with tempfile.TemporaryDirectory(prefix="static-clock-late-") as directory:
            root = Path(directory)
            script = root / "selected.py"
            script.write_bytes(self.body.encode())
            env = {**self.context, "RUNNER_TEMP": str(root)}
            observed = self.observation(before=1500, after=1501)
            with patch.object(wiring, "__file__", str(script), create=True), \
                 patch.object(wiring, "observe_worker", return_value=observed):
                with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "already-exhausted"):
                    wiring.deadline_bootstrap(env)
            proof = json.loads((wiring.deadline_control(env) / "deadline.original.json").read_bytes())
            self.assertEqual(proof["observation"], observed)
            self.assertEqual(proof["original_deadline_boottime_ns"], 2_800_000_000_000)
            self.assertEqual(proof["status"], "DENY")

    def test_bootstrap_custody_error_is_secondary_to_original_identity_denial(self):
        wiring = self.wiring
        with tempfile.TemporaryDirectory(prefix="static-clock-primary-") as directory:
            root = Path(directory)
            script = root / "selected.py"
            script.write_bytes(self.body.encode())
            primary = wiring.StaticDeadlineDenied("synthetic original identity denial")
            secondary = OSError("synthetic proof write failure")
            original_writer = wiring.exclusive_original
            def write(path, raw, maximum):
                if path.name == "deadline.original.json":
                    raise secondary
                return original_writer(path, raw, maximum)
            with patch.object(wiring, "__file__", str(script), create=True), \
                 patch.object(wiring, "observe_worker", side_effect=primary), \
                 patch.object(wiring, "exclusive_original", side_effect=write):
                with self.assertRaises(wiring.StaticDeadlineDenied) as caught:
                    wiring.deadline_bootstrap({**self.context, "RUNNER_TEMP": str(root)})
            self.assertIs(caught.exception, primary)
            self.assertIs(primary.static_deadline_retention_error, secondary)
            self.assertEqual((root / "blender-static-custody-123-1/wiring.original.py").read_bytes(), self.body.encode())

    def test_oversized_observation_has_bounded_denial_receipt_not_a_complete_clock_claim(self):
        wiring = self.wiring
        with tempfile.TemporaryDirectory(prefix="static-clock-proof-budget-") as directory:
            root = Path(directory)
            script = root / "selected.py"
            script.write_bytes(self.body.encode())
            observed = self.observation()
            observed["synthetic_oversized_derived_diagnostic"] = "x" * 8192
            with patch.object(wiring, "__file__", str(script), create=True), \
                 patch.object(wiring, "observe_worker", return_value=observed):
                with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "observation-budget"):
                    wiring.deadline_bootstrap({**self.context, "RUNNER_TEMP": str(root)})
            raw = (root / "blender-static-custody-123-1/deadline.original.json").read_bytes()
            self.assertLessEqual(len(raw), 8192)
            receipt = json.loads(raw)
            self.assertEqual(receipt["status"], "DENY")
            self.assertEqual(receipt["observation_retention"], "INCOMPLETE_BEFORE_ANY_NATIVE_EFFECT")
            self.assertGreater(receipt["rejected_observation_bytes"], 8192)
            self.assertFalse(receipt["native_effects"])

    def test_preparation_and_actions_cannot_consume_original_native_custody_reserve(self):
        wiring = self.wiring
        proof = self.proof()
        # 2,800 - (900 + 180 + 300 + 60) = 1,360 seconds from boot.
        self.assertEqual(wiring.prepare_remaining(proof, 1_360_000_000_000), 0)
        with patch.object(wiring, "original_deadline", return_value=proof):
            with patch.object(wiring, "clock_ns", return_value=1_175_000_000_000):
                self.assertEqual(wiring.prepare_action({}), 0)
            with patch.object(wiring, "clock_ns", return_value=1_175_000_000_001):
                with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "custody-reserve"):
                    wiring.prepare_action({})
        with tempfile.TemporaryDirectory(prefix="static-prep-denied-") as directory:
            script = Path(directory) / "original.sh"
            script.write_text("exit 0\n")
            with patch.object(wiring, "original_deadline", return_value=proof), \
                 patch.object(wiring, "clock_ns", return_value=1_360_000_000_000), \
                 patch.object(wiring.subprocess, "Popen", side_effect=AssertionError("must not spawn")) as launch:
                with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "preparation-deadline"):
                    wiring.prepare_script({"RUNNER_TEMP": directory}, str(script))
                launch.assert_not_called()

    def test_native_admission_requires_full_1440_seconds_and_never_reduces_420(self):
        wiring = self.wiring
        proof = self.proof()
        with patch.object(wiring, "clock_ns", return_value=1_360_000_000_000):
            budget = wiring.StaticNativeBudget(proof)
        with patch.object(wiring, "clock_ns", return_value=1_360_000_000_001):
            with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "native-and-custody"):
                wiring.StaticNativeBudget(proof)
        with patch.object(wiring, "clock_ns", return_value=1_500_000_000_000):
            self.assertEqual(budget.realize(420), 420)
            with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "attempt-changed"):
                budget.realize(420)
        self.assertEqual(proof["original_deadline_boottime_ns"], 2_800_000_000_000)

    def test_setup_loop_and_late_source_checks_cannot_obtain_a_fresh_native_window(self):
        wiring = self.wiring
        with patch.object(wiring, "clock_ns", return_value=1_000_000_000_000):
            budget = wiring.StaticNativeBudget(self.proof(), step_started_ns=900_000_000_000)
        with patch.object(wiring, "clock_ns", return_value=1_150_000_000_000):
            with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "realize-reserve"):
                budget.setup(60)
            self.assertEqual(budget.setup(45), 45)
        with patch.object(wiring, "clock_ns", return_value=1_200_000_000_001):
            with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "full-original-realize"):
                budget.realize(420)
        with patch.object(wiring, "clock_ns", return_value=1_000_000_000_000):
            stale = wiring.StaticNativeBudget(self.proof(), step_started_ns=600_000_000_000)
            with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "realize-reserve"):
                stale.setup(30)

    def test_native_step_clock_comes_from_observed_shell_not_post_setup_now(self):
        wiring = self.wiring
        observed = self.observation()
        self.assertEqual(wiring.native_step_start(observed), 110_000_000_000)
        altered = copy.deepcopy(observed)
        altered["ancestors"][-2]["comm"] = "untrusted-shell"
        with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "shell-identity"):
            wiring.native_step_start(altered)

    def test_real_original_readback_rejects_changed_script_without_new_clock(self):
        wiring = self.wiring
        with tempfile.TemporaryDirectory(prefix="static-clock-readback-") as directory:
            root = Path(directory)
            script = root / "selected.py"
            script.write_bytes(self.body.encode())
            env = {**self.context, "RUNNER_TEMP": str(root)}
            with patch.object(wiring, "__file__", str(script), create=True), \
                 patch.object(wiring, "observe_worker", return_value=self.observation()):
                proof = wiring.deadline_bootstrap(env)
            with patch.object(wiring, "__file__", str(script), create=True), \
                 patch.object(wiring, "observe_worker", return_value=self.observation(before=300, after=301)):
                read = wiring.original_deadline(env)
                self.assertEqual(read, proof)
                script.write_bytes(self.body.encode() + b"# different current helper\n")
                with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "wiring-changed"):
                    wiring.original_deadline(env)
            retained = json.loads((wiring.deadline_control(env) / "deadline.original.json").read_bytes())
            self.assertEqual(retained, proof)

    def retain_process_control(self, directory, receipt):
        destination = os.environ.get("MOTIONWRIGHT_STATIC_DEADLINE_TEST_ORIGINALS")
        if destination is None:
            return
        target = Path(destination) / self._testMethodName
        target.mkdir(parents=True, exist_ok=False)
        rows = []
        for path in sorted(Path(directory).iterdir()):
            if not path.is_file():
                continue
            raw = path.read_bytes()
            self.assertLessEqual(len(raw), 8192)
            (target / path.name).write_bytes(raw)
            rows.append({"file": path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        (target / "RECEIPT.json").write_text(json.dumps({**receipt, "originals": rows,
                "fixture_kind": "SYNTHETIC_SETUP_PROCESSES_ONLY", "native_effects": False,
                "formal_acceptance": False}, indent=2, sort_keys=True) + "\n")

    def test_real_nonzero_setup_parent_cannot_leave_live_same_group_child(self):
        wiring = self.wiring
        with tempfile.TemporaryDirectory(prefix="static-orphan-fixture-") as directory:
            root = Path(directory)
            child_code = ("import signal,time; from pathlib import Path; "
                          "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                          f"Path({str(root / 'child-ready.original')!r}).write_text('ready'); time.sleep(30)")
            parent_code = "\n".join([
                "import os,subprocess,sys,time", "from pathlib import Path",
                f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)",
                f"Path({str(root / 'child-pid.original')!r}).write_text(str(child.pid))",
                "stop=time.monotonic()+2",
                f"while not Path({str(root / 'child-ready.original')!r}).exists() and time.monotonic()<stop: time.sleep(.01)",
                "os._exit(7)",
            ])
            (root / "parent.py").write_text(parent_code + "\n")
            script = root / "original.sh"
            script.write_text("#!/bin/bash\n" + sys.executable + " " + str(root / "parent.py") + "\n")
            start = time.monotonic()
            with patch.object(wiring, "original_deadline", return_value=self.proof()), \
                 patch.object(wiring, "clock_ns", return_value=1_000_000_000_000):
                code = wiring.prepare_script({**os.environ, "RUNNER_TEMP": directory}, str(script))
            elapsed = time.monotonic() - start
            self.assertEqual(code, 7)
            self.assertLess(elapsed, 8)
            child_pid = int((root / "child-pid.original").read_text())
            # PID syscalls and mounted /proc can belong to different namespaces
            # in this control environment. Observe the exact child PID returned
            # by Popen through the same syscall namespace; ABSENT in /proc is
            # not evidence that this child ended.
            stop = time.monotonic() + 2
            probes = 0
            while True:
                probes += 1
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError as error:
                    child_terminal = {"method": "os.kill(child_pid, 0)", "child_pid": child_pid,
                                      "result": "ESRCH", "errno": error.errno,
                                      "probes": probes, "proc_path_used": False}
                    break
                if time.monotonic() >= stop:
                    self.fail("synthetic child terminal was not observed through its actual PID namespace")
                time.sleep(.02)
            (root / "child-terminal-observation.original").write_text(
                json.dumps(child_terminal, indent=2, sort_keys=True) + "\n")
            self.retain_process_control(root, {"observed_parent_exit": code, "elapsed_seconds": elapsed,
                    "same_group_child_live_after_return": False, "terminal_tree_completeness_claimed": False,
                    "retry_executed": False})

    def test_real_setup_timeout_stays_primary_after_group_cleanup(self):
        wiring = self.wiring
        with tempfile.TemporaryDirectory(prefix="static-timeout-fixture-") as directory:
            root = Path(directory)
            script = root / "original.sh"
            script.write_text("#!/bin/bash\nprintf 'synthetic bounded setup fixture\\n'\nsleep 30\n")
            proof = self.proof()
            now = 1_000_000_000_000
            proof["original_deadline_boottime_ns"] = now + int((1440 + 7.15) * 1_000_000_000)
            start = time.monotonic()
            with patch.object(wiring, "original_deadline", return_value=proof), \
                 patch.object(wiring, "clock_ns", return_value=now):
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    wiring.prepare_script({**os.environ, "RUNNER_TEMP": directory}, str(script))
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 8)
            self.assertGreater(caught.exception.timeout, 0)
            self.assertLess(caught.exception.timeout, .2)
            self.retain_process_control(root, {"primary_exception": type(caught.exception).__name__,
                    "timeout_seconds": caught.exception.timeout, "elapsed_seconds": elapsed,
                    "secondary_cleanup_error": type(getattr(caught.exception, "static_preparation_cleanup_error", None)).__name__,
                    "retry_executed": False})

    def test_static_main_denies_missing_clock_before_any_native_input_probe(self):
        with patch.dict(wrapper.__dict__, {"_STATIC_ORIGINAL_JOB_BUDGET": None}), \
             patch.dict(os.environ, {"MOTIONWRIGHT_BLENDER_SCOPE": "static-glb"}), \
             patch.object(wrapper, "exact_inputs", side_effect=AssertionError("native must not start")) as native:
            with self.assertRaisesRegex(AssertionError, "original job deadline is required"):
                wrapper.main()
            native.assert_not_called()

    def test_expired_static_budget_denies_before_subprocess_and_preserves_420_argument(self):
        wiring = self.wiring
        with patch.object(wiring, "clock_ns", return_value=1_000_000_000_000):
            budget = wiring.StaticNativeBudget(self.proof())
        with patch.object(wiring, "clock_ns", return_value=1_300_000_000_001), \
             patch.dict(wrapper.__dict__, {"_STATIC_ORIGINAL_JOB_BUDGET": budget}), \
             patch.object(wrapper.subprocess, "run", side_effect=AssertionError("native must not start")) as native:
            with self.assertRaisesRegex(wiring.StaticDeadlineDenied, "full-original-realize"):
                wrapper.run_json(["synthetic-no-native"], env={"MOTIONWRIGHT_BLENDER_SCOPE": "static-glb"},
                                 timeout=420, static_capture=("realize", Path("unused"), None))
            native.assert_not_called()

    def test_original_timeout_keeps_partial_bytes_unknown_identity_and_no_retry(self):
        wiring = self.wiring
        with tempfile.TemporaryDirectory(prefix="static-clock-unknown-") as directory:
            evidence = Path(directory)
            with patch.object(wiring, "clock_ns", return_value=1_000_000_000_000):
                budget = wiring.StaticNativeBudget(self.proof())
            primary = subprocess.TimeoutExpired(["synthetic-no-native"], 420,
                                                output=b"partial original\x00\xff", stderr=b"original stderr\n")
            with patch.object(wiring, "clock_ns", return_value=1_010_000_000_000), \
                 patch.dict(wrapper.__dict__, {"_STATIC_ORIGINAL_JOB_BUDGET": budget}), \
                 patch.object(wrapper, "EVIDENCE", evidence), \
                 patch.object(wrapper.subprocess, "run", side_effect=primary) as native:
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    wrapper.run_json(["synthetic-no-native"], env={"MOTIONWRIGHT_BLENDER_SCOPE": "static-glb"},
                                     timeout=420, static_capture=("realize", evidence / "unopened.sqlite3", None))
                self.assertIs(caught.exception, primary)
                self.assertEqual(native.call_count, 1)
                self.assertEqual(native.call_args.kwargs["timeout"], 420)
            capture = evidence / "static-realize-unconfirmed"
            self.assertEqual((capture / "stdout.partial.original").read_bytes(), primary.stdout)
            self.assertEqual((capture / "stderr.partial.original").read_bytes(), primary.stderr)
            receipt = json.loads((capture / "receipt.json").read_bytes())
            self.assertEqual(receipt["native_operation_result"], "UNKNOWN")
            self.assertFalse(receipt["native_process_terminal_observed"])
            self.assertFalse(receipt["sqlite_reopen_or_native_retry_executed"])

    def test_workflow_starts_clock_first_bounds_actions_and_seals_before_other_work(self):
        source = (ROOT / ".github/workflows/native-blender-e2e.yml").read_text()
        job = source.split("  native-blender-roundtrip:\n", 1)[1].split("  native-blender-lit-still:\n", 1)[0]
        steps = re.split(r"(?=^    - (?:name|uses):)", job, flags=re.M)[1:]
        self.assertTrue(steps[0].startswith("    - name: Capture the original static job clock before checkout or setup\n"))
        self.assertEqual(job.count('"$RUNNER_TEMP/blender-static-custody.py" init'), 1)
        self.assertIn("|| 45 }}", job.split("    steps:\n", 1)[0])
        self.assertEqual(job.count("Admit static setup action "), 4)
        self.assertEqual(job.count("inputs.verification_scope == 'static-glb' && 3 || 360"), 4)
        native = next(index for index, step in enumerate(steps) if "      id: static_native\n" in step)
        self.assertIn("      timeout-minutes: 15\n", steps[native])
        self.assertIn("      id: static_seal\n", steps[native + 1])
        self.assertIn("      timeout-minutes: 3\n", steps[native + 1])
        self.assertIn("      timeout-minutes: 5\n", steps[native + 2])
        self.assertIn("always() && steps.static_seal.outputs.sealed == 'true'", steps[native + 2])
        self.assertNotIn("steps.static_native.outcome == 'success'", steps[native + 1] + steps[native + 2])
        unbounded = []
        for step in steps[1:native]:
            if "      run: |\n" not in step:
                continue
            if any(name in step for name in ("Verify affected lit producer contracts", "Require 29 exact lit Rust", "Bind the explicit original Scene")):
                continue
            if "prepare-script" not in step:
                unbounded.append(step.splitlines()[0])
        self.assertEqual(unbounded, [])


if __name__ == "__main__":
    unittest.main()
