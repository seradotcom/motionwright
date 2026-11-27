"""Deterministic guardrails for the native X11 boot + durable state predicate.

No real GUI, provider, render or destructive operation is used in these tests.
"""
from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_gui_probe as gui  # noqa: E402


class NativeWindowAndBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-gui-readiness-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.app = self.root / "squashfs-root" / "AppRun"
        self.app.parent.mkdir()
        self.app.write_bytes(b"mock extracted runner; never executed")

    def mock_launch(self, *, clock, stdout):
        proc = Mock()
        proc.pid = 4242
        proc.poll.return_value = None
        proc.wait.return_value = 0
        window = Mock(returncode=0, stdout=stdout)
        return (
            patch.dict(os.environ, {"DISPLAY": ":99"}),
            patch.object(gui.subprocess, "Popen", return_value=proc),
            patch.object(gui.subprocess, "run", return_value=window),
            patch.object(gui.time, "monotonic", side_effect=clock),
            patch.object(gui.time, "sleep", return_value=None),
            patch.object(gui.os, "killpg", create=True),
            proc,
        )

    def test_window_does_not_end_boot_before_project_is_committed(self):
        contexts = self.mock_launch(clock=[10.0, 10.1, 10.3, 10.5, 10.6],
                                    stdout='0x00400004 "Motionwright": ("motionwright")')
        env, popen, xwin, clock, sleep, kill, proc = contexts
        checks = iter([False, False, True])
        calls = []
        def ready(scratch):
            self.assertEqual(scratch, self.root / "isolated")
            result = next(checks)
            calls.append(result)
            return result
        with env, popen, xwin as native_probe, clock, sleep as sleeper, kill as killer:
            outcome = gui.run_probe(self.app, self.root / "isolated",
                                    seconds=5, ready_check=ready)
        self.assertEqual(outcome["status"], "WINDOW_OBSERVED")
        self.assertEqual(calls, [False, False, True])
        self.assertEqual(native_probe.call_count, 3)
        self.assertEqual(sleeper.call_count, 2)
        killer.assert_called_once()

    def test_window_without_committed_project_fails_closed_at_deadline(self):
        contexts = self.mock_launch(clock=[0.0, 0.1, 1.1, 2.1],
                                    stdout='0x00400004 "Motionwright": ("motionwright")')
        env, popen, xwin, clock, sleep, kill, proc = contexts
        with env, popen, xwin as native_probe, clock, sleep as sleeper, kill as killer:
            with self.assertRaisesRegex(RuntimeError, "bootstrap never became ready"):
                gui.run_probe(self.app, self.root / "isolated",
                              seconds=2, ready_check=lambda _: False)
        killer.assert_called_once()
        self.assertEqual(native_probe.call_count, 2)

    def test_real_window_interaction_can_wait_but_dispatches_only_once(self):
        contexts = self.mock_launch(
            clock=[0.0, 0.1, 0.3, 0.5, 0.7, 0.9],
            stdout='0x00400004 "Motionwright": ("motionwright")',
        )
        env, popen, xwin, clock, sleep, kill, proc = contexts
        calls = []
        readiness = []

        def once(scratch):
            self.assertEqual(scratch, self.root / "isolated")
            calls.append("inspected")
            return len(calls) == 2

        def ready(scratch):
            readiness.append(True)
            return True

        with env, popen, xwin as native_probe, clock, sleep, kill as killer:
            result = gui.run_probe(
                self.app, self.root / "isolated", seconds=3,
                window_interaction=once, ready_check=ready,
            )
        self.assertEqual(result["status"], "WINDOW_OBSERVED")
        self.assertEqual(calls, ["inspected", "inspected"])
        self.assertEqual(readiness, [True])
        self.assertEqual(native_probe.call_count, 3)
        killer.assert_called_once()

    def test_native_create_is_not_claimed_before_empty_store_authority(self):
        contexts = self.mock_launch(clock=[0, 0.1, 1.1, 2.1],
                                    stdout='0x00400004 "Motionwright": ("motionwright")')
        env, popen, xwin, clock, sleep, kill, proc = contexts
        calls = []
        def not_yet_ready(scratch):
            calls.append(scratch)
            return False
        with env, popen, xwin, clock, sleep, kill:
            with self.assertRaisesRegex(RuntimeError, "empty-store authority never permitted"):
                gui.run_probe(self.app, self.root / "isolated", seconds=2,
                              ready_check=lambda _: False,
                              window_interaction=not_yet_ready)
        self.assertEqual(len(calls), 2)

    def test_sent_native_create_without_sqlite_ack_remains_failure_without_retry(self):
        contexts = self.mock_launch(clock=[0, 0.1, 0.5, 1.1, 2.1],
                                    stdout='0x00400004 "Motionwright": ("motionwright")')
        env, popen, xwin, clock, sleep, kill, proc = contexts
        calls = []
        def dispatched_once(scratch):
            calls.append(scratch)
            return True
        with env, popen, xwin, clock, sleep, kill:
            with self.assertRaisesRegex(RuntimeError, "One original native Create action was sent"):
                gui.run_probe(self.app, self.root / "isolated", seconds=2,
                              ready_check=lambda _: False,
                              window_interaction=dispatched_once)
        self.assertEqual(len(calls), 1)

    def test_existing_window_only_smoke_retains_its_original_contract(self):
        self.assertTrue(gui.check_window_output(
            '0x00400004 "Motionwright": ("motionwright")'))
        for original in (
            '0x00400004 "Motionwright loading": ("motionwright")',
            '0x00400004 "Other": ("Motionwright")',
            'logger said Motionwright but this is not an X11 client',
            '0x00400004 "Motionwright":\n0x00400005 "Motionwright":\n',
            '0x00400004 "motionwright": ("motionwright")',
        ):
            with self.subTest(original=original):
                self.assertFalse(gui.check_window_output(original))
        contexts = self.mock_launch(clock=[0.0, 0.1, 0.2],
                                    stdout='0x00400004 "Motionwright": ("motionwright")')
        env, popen, xwin, clock, sleep, kill, proc = contexts
        with env, popen, xwin as native_probe, clock, sleep as sleeper, kill as killer:
            result = gui.run_probe(self.app, self.root / "isolated", seconds=2)
        self.assertEqual(result["status"], "WINDOW_OBSERVED")
        self.assertEqual(native_probe.call_count, 1)
        sleeper.assert_not_called()


    def test_only_explicit_ci_owner_connection_reaches_the_app(self):
        connection = self.root / "motionwright-connection.json"
        connection.write_text("{}", encoding="utf-8")
        window = '0x00400004 "Motionwright": ("motionwright")'
        for enabled in (False, True):
            with self.subTest(explicit_connection=enabled):
                env, popen, xwin, clock, sleep, kill, _ = self.mock_launch(
                    clock=[0.0, 0.1, 0.2], stdout=window)
                with env, patch.dict(os.environ, {
                    "GITHUB_ACTIONS": "true", "GITHUB_RUN_ATTEMPT": "1",
                    "MOTIONWRIGHT_SEMWRIGHT_CONNECTION": "/unrelated/inherited.json",
                }), popen as launch, xwin, clock, sleep, kill:
                    gui.run_probe(self.app, self.root / "isolated", seconds=2,
                                  owner_connection=connection if enabled else None)
                forwarded = launch.call_args.kwargs["env"]
                self.assertEqual(
                    forwarded.get("MOTIONWRIGHT_SEMWRIGHT_CONNECTION"),
                    str(connection) if enabled else None,
                )

    def test_loader_observation_is_sampled_once_at_window_readiness(self):
        contexts = self.mock_launch(
            clock=[0.0, 0.1, 0.2],
            stdout='0x00400004 "Motionwright": ("motionwright")',
        )
        env, popen, xwin, clock, sleep, kill, proc = contexts
        observer = Mock(return_value={
            "status": "NOT_OBSERVED", "claims": {"G3": "STOP"},
        })
        with env, popen, xwin, clock, sleep, kill:
            report = gui.run_probe(
                self.app, self.root / "isolated", seconds=2,
                loader_observer=observer,
            )
        observer.assert_called_once_with(4242, self.app.parent.resolve())
        self.assertEqual(report["loader_observation"]["status"], "NOT_OBSERVED")

    def test_observer_cannot_promote_g3_or_replay_a_gui_action(self):
        contexts = self.mock_launch(
            clock=[0.0, 0.1, 0.2],
            stdout='0x00400004 "Motionwright": ("motionwright")',
        )
        env, popen, xwin, clock, sleep, kill, proc = contexts
        observer = Mock(return_value={"claims": {"G3": "PASS"}})
        with env, popen, xwin, clock, sleep, kill:
            with self.assertRaisesRegex(RuntimeError, "cannot grant G3"):
                gui.run_probe(self.app, self.root / "isolated", seconds=2,
                              loader_observer=observer)
        observer.assert_called_once()


class LiveLoaderScopeTests(unittest.TestCase):
    """Synthetic proc fixtures, never a fabricated real installed observation."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="mw-live-loader-", dir=os.environ.get("TEMP"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.app = self.root / "squashfs-root"
        self.bin = self.app / "usr/bin/motionwright-desktop"
        self.bin.parent.mkdir(parents=True)
        self.bin.write_bytes(b"inert fixture, not an ELF")
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.launcher = 3214
        self.pid = 3215
        self.child = self.proc / str(self.pid)
        self.child.mkdir()
        self.stat(group=self.launcher)
        self.maps()

    def stat(self, *, group):
        # Linux /proc/stat starts fields after "(comm)" with state and ppid.
        fields = ["S", "1", str(group), *(["0"] * 16), "123456", "0"]
        (self.child / "stat").write_text(
            f"{self.pid} (motionwright-desktop) " + " ".join(fields) + "\n")

    def maps(self, extra=""):
        (self.child / "maps").write_text(
            f"0000-1000 r-xp 00000000 08:01 31 {self.bin}\n"
            "1000-2000 r-xp 00000000 08:01 39 "
            "/usr/lib/x86_64-linux-gnu/libgtk-3.so.0\n"
            "2000-3000 rw-p 00000000 00:00 0 [heap]\n"
            + extra)

    def observe(self, link=None):
        return gui.observe_runtime_loader(
            self.launcher, self.app, proc_root=self.proc,
            link_reader=(link or (lambda _: str(self.bin))),
        )

    def test_installed_executable_and_system_library_maps_are_scoped(self):
        report = self.observe()
        self.assertEqual(report["status"], "LIVE_EXECUTABLE_MAPPINGS_OBSERVED")
        self.assertEqual(report["process_starttime_ticks"], 123456)
        self.assertEqual(len(report["mapped_files"]), 2)
        self.assertIn("usr/bin/motionwright-desktop",
                      [m["path"] for m in report["mapped_files"]])
        self.assertIn("/usr/lib/x86_64-linux-gnu/libgtk-3.so.0",
                      [m["path"] for m in report["mapped_files"]])
        self.assertEqual(report["claims"]["G3"], "STOP")
        self.assertEqual(report["claims"]["mapped_file_bytes"], "NOT_AUTHENTICATED")
        self.assertEqual(report["claims"]["host_package_ownership"], "NOT_ATTESTED")

    def test_foreign_executable_cannot_supply_loader_evidence(self):
        result = self.observe(link=lambda _: "/usr/bin/foreign-editor")
        self.assertEqual(result["status"], "NOT_OBSERVED")
        self.assertEqual(result["reason"], "AUTHENTICATED_PROCESS_ABSENT_OR_AMBIGUOUS")

    def test_foreign_process_group_cannot_supply_loader_evidence(self):
        self.stat(group=self.launcher + 99)
        self.assertEqual(self.observe()["status"], "NOT_OBSERVED")

    def test_unclassified_private_paths_are_never_exposed(self):
        secret = "/home/runner/user-session-secret/private.so"
        self.maps(extra=f"3000-4000 r-xp 00000000 08:01 55 {secret}\n")
        report = self.observe()
        self.assertEqual(report["status"], "LIVE_EXECUTABLE_MAPPINGS_OBSERVED")
        self.assertNotIn(secret, str(report))
        self.assertEqual(report["unclassified_executable_segments"], 1)

    def test_missing_expected_binary_cannot_be_claimed(self):
        self.bin.unlink()
        report = self.observe()
        self.assertEqual(report["reason"], "INSTALLED_EXECUTABLE_MISSING")
        self.assertEqual(report["claims"]["G3"], "STOP")

    def test_malformed_stat_never_promotes_loader(self):
        (self.child / "stat").write_bytes(b"bad proc stat")
        report = self.observe()
        self.assertEqual(report["status"], "NOT_OBSERVED")
        self.assertEqual(report["claims"]["G3"], "STOP")


if __name__ == "__main__":
    unittest.main()
