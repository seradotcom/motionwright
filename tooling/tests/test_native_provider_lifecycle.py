"""Adversarial tests for passive, privacy-bounded provider lifecycle evidence."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

FILE = Path(__file__).resolve().parents[1] / "native_provider_lifecycle.py"
SPEC = importlib.util.spec_from_file_location("native_provider_lifecycle", FILE)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def status(role: str, ppid: int, rss: int = 2048) -> str:
    return (
        f"Name:\t{role}\nState:\tS (sleeping)\nPPid:\t{ppid}\n"
        f"VmRSS:\t{rss} kB\nVmHWM:\t{rss * 2} kB\n"
        "Arguments: --session-file=/private/token-do-not-export\n"
    )


class ProviderDiagnosticTests(unittest.TestCase):
    @staticmethod
    def _native_event(command, stage, second, job, *, code=None, known=None):
        return {
            "command": "driver.motion-canvas.render." + command,
            "stage": stage,
            "created_at": f"2026-10-13T01:00:{second:02d}Z",
            "payload": {
                "job_ref": job,
                "error": {
                    "code": code, "outcome_known": known,
                    "message": "/private/session-never-export",
                },
            },
        }

    def test_interleaved_different_jobs_never_claim_same_job_generation_loss(self):
        a = "opaque-A-credential"
        b = "opaque-B-credential"
        rows = [
            self._native_event("start", "completed", 0, a),
            self._native_event("start", "completed", 1, b),
            self._native_event("status", "outcome_unknown", 2, a,
                               code="Timeout", known=False),
            self._native_event("status", "failed_known", 3, b,
                               code="Unavailable", known=True),
        ]
        report = mod.summarize_render_receipts(rows)
        self.assertEqual(report["render_outcome"], "unconfirmed")
        self.assertFalse(report["timeout_then_provider_unavailable_observed"])
        self.assertFalse(report["same_acknowledged_job_status_chain_verified"])
        self.assertNotIn(a, str(report))
        self.assertNotIn(b, str(report))
        self.assertNotIn("/private", str(report))

    def test_same_acknowledged_job_loss_is_source_scoped_and_redacted(self):
        job = "private-job-ref"
        rows = [
            self._native_event("start", "completed", 0, job),
            self._native_event("status", "outcome_unknown", 1, job,
                               code="Timeout", known=False),
            self._native_event("status", "failed_known", 2, job,
                               code="Unavailable", known=True),
        ]
        report = mod.summarize_render_receipts(rows)
        self.assertTrue(report["timeout_then_provider_unavailable_observed"])
        self.assertTrue(report["same_acknowledged_job_status_chain_verified"])
        self.assertEqual(report["render_outcome"], "unconfirmed")
        self.assertNotIn(job, str(report))

    def test_recovered_loss_does_not_leak_into_unrelated_unknown_job(self):
        a, b = "private-recovered-job", "private-unconfirmed-other-job"
        rows = [
            self._native_event("start", "completed", 0, a),
            self._native_event("status", "outcome_unknown", 1, a,
                               code="Timeout", known=False),
            self._native_event("status", "failed_known", 2, a,
                               code="Unavailable", known=True),
            self._native_event("result", "completed", 3, a),
            self._native_event("start", "completed", 4, b),
            self._native_event("status", "outcome_unknown", 5, b,
                               code="Timeout", known=False),
        ]
        rows[3]["payload"]["result"] = {
            "data": {"job_ref": a, "state": "succeeded",
                     "artifact": {"sha256": "a" * 64}},
        }
        report = mod.summarize_render_receipts(rows)
        self.assertEqual(report["render_outcome"], "unconfirmed")
        # Keep the real historical incident, but do not attribute it to B.
        self.assertTrue(report["timeout_then_provider_unavailable_observed"])
        self.assertTrue(report["same_acknowledged_job_status_chain_verified"])
        self.assertFalse(report["unresolved_same_job_status_loss_verified"])
        self.assertNotIn(a, str(report))
        self.assertNotIn(b, str(report))

    def test_unresolved_same_job_loss_remains_diagnosable_after_other_recovery(self):
        a, b = "private-recovered-job", "private-still-lost-job"
        rows = [
            self._native_event("start", "completed", 0, a),
            self._native_event("status", "outcome_unknown", 1, a,
                               code="Timeout", known=False),
            self._native_event("status", "failed_known", 2, a,
                               code="Unavailable", known=True),
            self._native_event("result", "completed", 3, a),
            self._native_event("start", "completed", 4, b),
            self._native_event("status", "outcome_unknown", 5, b,
                               code="Timeout", known=False),
            self._native_event("status", "failed_known", 6, b,
                               code="Unavailable", known=True),
        ]
        rows[3]["payload"]["result"] = {
            "data": {"job_ref": a, "state": "succeeded",
                     "artifact": {"sha256": "a" * 64}},
        }
        report = mod.summarize_render_receipts(rows)
        self.assertEqual(report["render_outcome"], "unconfirmed")
        self.assertTrue(report["unresolved_same_job_status_loss_verified"])
        self.assertNotIn(a, str(report))
        self.assertNotIn(b, str(report))

    def test_unacknowledged_or_mistyped_loss_is_not_native_generation_evidence(self):
        job = "private-job-ref"
        start = self._native_event("start", "dispatching", 0, job)
        timeout = self._native_event("status", "outcome_unknown", 1, job,
                                     code="Timeout", known=False)
        unavailable = self._native_event("status", "failed_known", 2, job,
                                         code="Unavailable", known=True)
        for events in (
            [start, timeout, unavailable],
            [self._native_event("start", "completed", 0, job), timeout,
             self._native_event("status", "completed", 2, job,
                                code="Unavailable", known=True)],
            [self._native_event("start", "completed", 0, job), timeout,
             self._native_event("status", "failed_known", 2, job,
                                code="Unavailable", known=False)],
            [self._native_event("start", "completed", 0, job),
             self._native_event("status", "outcome_unknown", 1, job,
                                code="Timeout", known=True), unavailable],
            [self._native_event("start", "completed", 0, job), unavailable,
             self._native_event("status", "outcome_unknown", 3, job,
                                code="Timeout", known=False)],
        ):
            report = mod.summarize_render_receipts(events)
            self.assertFalse(report["timeout_then_provider_unavailable_observed"])
            self.assertFalse(report["same_acknowledged_job_status_chain_verified"])

    def test_conflicting_terminal_identity_cannot_clear_unknown_original_job(self):
        a = "private-original-job"
        b = "private-other-job"
        start = self._native_event("start", "completed", 0, a)
        timeout = self._native_event("status", "outcome_unknown", 1, a,
                                     code="Timeout", known=False)
        forged = self._native_event("result", "completed", 2, a)
        forged["payload"]["result"] = {
            "data": {"job_ref": b, "state": "succeeded",
                     "artifact": {"sha256": "c" * 64}},
        }
        report = mod.summarize_render_receipts([start, timeout, forged])
        self.assertEqual(report["render_outcome"], "unconfirmed")
        self.assertFalse(report["result_observed_completed"])
        self.assertNotIn(a, str(report))
        self.assertNotIn(b, str(report))

    def test_only_read_safe_structured_proc_fields_no_secrets(self):
        parsed = mod.parse_status(status("unknown-private-application", 75))
        self.assertEqual(parsed["role"], "other")
        self.assertEqual(parsed["ppid"], 75)
        self.assertEqual(parsed["rss_kib"], 2048)
        self.assertEqual(parsed["peak_rss_kib"], 4096)
        self.assertNotIn("token-do-not-export", str(parsed))
        self.assertNotIn("unknown-private-application", str(parsed))

    def test_nested_driver_descendants_and_outsiders_filtered(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            for pid, name, parent in [
                (100, "semwrightd", 1),
                (120, "semwright-sandb", 100),
                (140, "node", 120),
                (155, "firefox", 140),
                (900, "other-persons-process", 1),
            ]:
                folder = proc / str(pid)
                folder.mkdir()
                (folder / "status").write_text(status(name, parent), encoding="ascii")
                leader = folder / "task" / str(pid)
                leader.mkdir(parents=True)
                leader_children = {120: "140", 140: "155"}.get(pid, "")
                (leader / "children").write_text(leader_children, encoding="ascii")
            # A multi-threaded daemon may launch a child from a worker TID.
            # Sampling only /proc/<pid>/task/<pid>/children misses that job.
            worker = proc / "100" / "task" / "101"
            worker.mkdir()
            (worker / "children").write_text("120", encoding="ascii")
            observed = mod.tree_snapshot(100, proc)
            self.assertTrue(observed["root_observed"])
            self.assertFalse(observed["truncated"])
            self.assertEqual([p["pid"] for p in observed["processes"]], [100, 120, 140, 155])
            self.assertEqual(observed["processes"][1]["role"], "semwright-sandb")
            self.assertNotIn("900", str(observed))
            self.assertNotIn("private/token", str(observed))
            self.assertFalse(mod.tree_snapshot(9000, proc)["root_observed"])

    def test_child_pid_reuse_cannot_be_admitted_or_traversed(self):
        # /task/<tid>/children was read while PID 120 was a real child,
        # but /proc/120/status now belongs to a different process.
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            for pid, name, parent, children in (
                (100, "semwrightd", 1, "120"),
                (120, "unrelated-private-process", 900, "135"),
                (135, "node", 120, ""),
            ):
                folder = proc / str(pid)
                folder.mkdir()
                (folder / "status").write_text(status(name, parent), encoding="ascii")
                leader = folder / "task" / str(pid)
                leader.mkdir(parents=True)
                (leader / "children").write_text(children, encoding="ascii")
            observed = mod.tree_snapshot(100, proc)
            self.assertTrue(observed["root_observed"])
            self.assertTrue(observed["truncated"])
            self.assertEqual([row["pid"] for row in observed["processes"]], [100])
            self.assertNotIn("unrelated-private-process", str(observed))
            self.assertNotIn("135", str(observed))

    def test_missing_listed_child_never_looks_like_complete_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            root = proc / "100"
            root.mkdir()
            (root / "status").write_text(status("semwrightd", 1), encoding="ascii")
            task = root / "task" / "100"
            task.mkdir(parents=True)
            (task / "children").write_text("120", encoding="ascii")
            observed = mod.tree_snapshot(100, proc)
            self.assertTrue(observed["root_observed"])
            self.assertTrue(observed["truncated"])
            self.assertEqual(len(observed["processes"]), 1)

            # A vanished worker thread's children file must also abstain.
            (task / "children").unlink()
            observed = mod.tree_snapshot(100, proc)
            self.assertTrue(observed["truncated"])

    def test_conflicting_parent_reports_cannot_yield_complete_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            for pid, parent, children in (
                (100, 1, "120 130"),
                (120, 100, "130"),
                (130, 120, ""),
            ):
                folder = proc / str(pid)
                folder.mkdir()
                name = "semwrightd" if pid == 100 else "node"
                (folder / "status").write_text(status(name, parent), encoding="ascii")
                task = folder / "task" / str(pid)
                task.mkdir(parents=True)
                (task / "children").write_text(children, encoding="ascii")
            observed = mod.tree_snapshot(100, proc)
            self.assertTrue(observed["truncated"])
            self.assertTrue(observed["root_observed"])

    def test_malformed_kernel_child_list_is_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            root = proc / "100"
            root.mkdir()
            (root / "status").write_text(status("semwrightd", 1), encoding="ascii")
            task = root / "task" / "100"
            task.mkdir(parents=True)
            (task / "children").write_text("untrusted-value", encoding="ascii")
            observed = mod.tree_snapshot(100, proc)
            self.assertTrue(observed["root_observed"])
            self.assertTrue(observed["truncated"])
            self.assertEqual(len(observed["processes"]), 1)

    def test_missing_state_is_not_a_valid_linux_process_state(self):
        parsed = mod.parse_status("Name:\tnode\nPPid:\t100\n")
        self.assertEqual(parsed["state"], "?")

    def test_cgroup_oom_counter_is_numeric_and_no_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "memory.current").write_text("1250\n")
            (root / "memory.peak").write_text("9999\n")
            (root / "memory.max").write_text("max\n")
            (root / "memory.events").write_text("low 1\nhigh 2\noom 3\noom_kill 1\n")
            events = mod.memory_events(root)
            self.assertEqual(events["current_bytes"], 1250)
            self.assertEqual(events["peak_bytes"], 9999)
            self.assertIsNone(events["limit_bytes"])
            self.assertEqual(events["oom"], 3)
            self.assertEqual(events["oom_kill"], 1)

    def test_realistic_status_loss_yields_redacted_unconfirmed_incident(self):
        def receipt(command, stage, timestamp, code=None, known=None):
            return {
                "command": "driver.motion-canvas.render." + command,
                "stage": stage,
                "created_at": "2026-10-09T18:15:0" + str(timestamp) + "Z",
                "request_id": "private-high-entropy-request",
                "payload": {
                    "job_ref": "private-native-job-ref",
                    "error": {
                        "code": code,
                        "outcome_known": known,
                        "message": "/private/session/never-publish",
                    },
                    "result": {"data": {"source": "/private/sensitive-video.mp4"}},
                },
            }
        records = [
            receipt("start", "dispatching", 0),
            receipt("start", "completed", 1),
            receipt("status", "completed", 2),
            receipt("status", "outcome_unknown", 3, "Timeout", False),
            receipt("status", "failed_known", 4, "Unavailable", True),
        ]
        result = mod.summarize_render_receipts(records)
        self.assertEqual(result["render_outcome"], "unconfirmed")
        self.assertTrue(result["timeout_then_provider_unavailable_observed"])
        self.assertEqual(result["render_start_dispatch_count"], 1)
        self.assertFalse(result["result_observed_completed"])
        self.assertFalse(result["restart_identity_verified"])
        output = str(result)
        for secret in ["private-high-entropy-request", "/private", "private-native-job-ref"]:
            self.assertNotIn(secret, output)
        self.assertEqual(
            [row["error_code"] for row in result["status_events"]],
            ["none", "Timeout", "Unavailable"],
        )

        terminal = receipt("result", "completed", 5)
        terminal["payload"]["result"]["data"].update({
            "job_ref": "private-native-job-ref",
            "state": "succeeded",
            "artifact": {"manifest": "/private/do-not-export"},
        })
        confirmed = records + [terminal]
        receipt_status = mod.summarize_render_receipts(confirmed)
        self.assertEqual(receipt_status["render_outcome"], "confirmed_result_receipt")
        self.assertTrue(receipt_status["result_observed_completed"])
        self.assertNotIn("/private", str(receipt_status))

        # A verified result for ANOTHER native job cannot resolve this one.
        foreign = {**terminal, "payload": {
            **terminal["payload"], "job_ref": "different-native-job",
        }}
        foreign["payload"]["result"] = {"data": {
            "job_ref": "different-native-job",
            "state": "succeeded", "artifact": {"manifest": "not-this-source"},
        }}
        unrelated = mod.summarize_render_receipts(records + [foreign])
        self.assertEqual(unrelated["render_outcome"], "unconfirmed")
        self.assertFalse(unrelated["result_observed_completed"])

        reversed_failure = records[:3] + [records[4], records[3]]
        reversed_failure[3] = {**reversed_failure[3], "created_at": "2026-10-09T18:15:03Z"}
        reversed_failure[4] = {**reversed_failure[4], "created_at": "2026-10-09T18:15:04Z"}
        self.assertFalse(
            mod.summarize_render_receipts(reversed_failure)["timeout_then_provider_unavailable_observed"]
        )

    def test_sampler_captures_only_structured_info(self):
        class ExitedDaemon:
            pid = 2**30
            @staticmethod
            def poll():
                return -9

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "health.json"
            sampler = mod.ProviderLifecycleSampler(ExitedDaemon(), destination, interval=0.25)
            sampler.start()
            result = sampler.stop()
            self.assertTrue(destination.exists())
            self.assertEqual(result["daemon_exit_before_owner_cleanup"], -9)
            self.assertEqual(result["daemon_exit_signal"], 9)
            self.assertEqual(result["authority"], "passive-ci-process-status-not-driver-evidence")
            self.assertNotIn("session-file", destination.read_text())
            self.assertNotIn("private", destination.read_text())
            with self.assertRaises(RuntimeError):
                sampler.stop()


if __name__ == "__main__":
    unittest.main()
