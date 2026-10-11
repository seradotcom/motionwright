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

    def test_existing_window_only_smoke_retains_its_original_contract(self):
        contexts = self.mock_launch(clock=[0.0, 0.1, 0.2],
                                    stdout='0x00400004 "Motionwright": ("motionwright")')
        env, popen, xwin, clock, sleep, kill, proc = contexts
        with env, popen, xwin as native_probe, clock, sleep as sleeper, kill as killer:
            result = gui.run_probe(self.app, self.root / "isolated", seconds=2)
        self.assertEqual(result["status"], "WINDOW_OBSERVED")
        self.assertEqual(native_probe.call_count, 1)
        sleeper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
