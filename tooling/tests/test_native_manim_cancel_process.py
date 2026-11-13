"""Actual Linux process controls for witness selection; no Native render or admission."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import unittest

from tooling import native_manim_cancel as owner


@unittest.skipUnless(sys.platform == "linux" and hasattr(os, "pidfd_open"), "Linux pidfd process controls")
class OriginalProcessSelectionTests(unittest.TestCase):
    def own_identity(self):
        return owner.process_stat(os.getpid())

    def executable_digest(self, pid):
        return hashlib.sha256((Path("/proc") / str(pid) / "exe").read_bytes()).hexdigest()

    def test_original_kernel_executable_is_pinned(self):
        original = owner.pinned_process(self.own_identity(), self.executable_digest(os.getpid()),
                                        argv_check=lambda argv: bool(argv))
        try:
            self.assertEqual(original["pid"], os.getpid())
            self.assertEqual(original["start_ticks"], self.own_identity()["start_ticks"])
            self.assertTrue(owner.alive(original["pidfd"]))
            status = owner.parse_kernel_status(
                (Path("/proc") / str(os.getpid()) / "status").read_bytes(), os.getpid())
            self.assertEqual(status["nspid"][0], os.getpid())
            self.assertEqual(status["parent"], self.own_identity()["parent"])
            # A real process in the observer's namespace is never an admitted init.
            before = len(list(Path("/proc/self/fd").iterdir()))
            with self.assertRaisesRegex(ValueError, "cancel_namespace_init_not_isolated"):
                owner.pin_namespace_init(original, os.getpid())
            self.assertEqual(len(list(Path("/proc/self/fd").iterdir())), before)
        finally:
            os.close(original["pidfd"])

    def test_forwarding_argv_cannot_select_another_executable_or_leak_pidfd(self):
        before = len(list(Path("/proc/self/fd").iterdir()))
        with self.assertRaises(owner.ExecutableNotSelected):
            owner.pinned_process(self.own_identity(), "0" * 64,
                                 argv_check=lambda argv: bool(argv), selecting=True)
        self.assertEqual(len(list(Path("/proc/self/fd").iterdir())), before)

    def test_selected_executable_mismatch_remains_fatal(self):
        with self.assertRaisesRegex(ValueError, "cancel_kernel_executable_digest"):
            owner.pinned_process(self.own_identity(), "0" * 64,
                                 argv_check=lambda argv: bool(argv))

    def test_pidfd_observes_original_child_exit_without_a_signal(self):
        code = "import sys; sys.stdin.buffer.read()"
        child = subprocess.Popen([sys.executable, "-I", "-c", code],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        original = None
        try:
            original = owner.pinned_process(owner.process_stat(child.pid),
                        self.executable_digest(child.pid),
                        argv_check=lambda argv: len(argv) == 4 and argv[1:] == [b"-I", b"-c", code.encode()])
            self.assertTrue(owner.alive(original["pidfd"]))
            child.stdin.close()
            self.assertEqual(child.wait(timeout=5), 0)
            self.assertFalse(owner.alive(original["pidfd"]))
            terminal = owner.pidfd_observation(original["pidfd"])
            self.assertEqual(terminal["state"], "REAPED")
            self.assertTrue(terminal["mask"] & owner.select.POLLHUP)
            invalid = os.dup(original["pidfd"])
            os.close(invalid)
            with self.assertRaisesRegex(ValueError, "cancel_pidfd_poll_invalid"):
                owner.pidfd_observation(invalid)
        finally:
            if child.stdin is not None and not child.stdin.closed:
                child.stdin.close()
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            if original is not None:
                os.close(original["pidfd"])


if __name__ == "__main__":
    unittest.main()
