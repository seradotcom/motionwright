"""v0.1 regression: Broker startup errors must not print owner daemon logs."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_render_e2e as native  # noqa: E402


class NativeOwnerSocketRedactionTests(unittest.TestCase):
    def test_exited_daemon_never_includes_raw_log_or_private_owner_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            log = root / "semwrightd.log"
            secret = "session-ticket=/private/owner/data/never-publish"
            log.write_text("ERROR: " + secret, encoding="utf-8")

            class Exited:
                polls = 0

                def poll(self):
                    self.polls += 1
                    return 101

            process = Exited()
            with patch.object(Path, "read_text",
                              side_effect=AssertionError("raw log accessed")):
                with self.assertRaises(AssertionError) as raised:
                    native.wait_for_socket(process, root / "missing.sock", log)
            message = str(raised.exception)
            self.assertIn("native_bootstrap=NOT_VERIFIED", message)
            self.assertEqual(process.polls, 1)
            self.assertNotIn(secret, message)
            self.assertNotIn(str(root), message)
            self.assertNotIn("raw log accessed", message)

    def test_timeout_exposes_only_static_failure_without_reading_log(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            log = root / "sensitive-daemon-output.log"
            log.write_text("private=/owner/session/opaque", encoding="utf-8")

            class Alive:
                def poll(self):
                    return None

            with patch.object(native.time, "monotonic", side_effect=[0.0, 31.0]):
                with patch.object(Path, "read_text",
                                  side_effect=AssertionError("raw log accessed")):
                    with self.assertRaises(AssertionError) as raised:
                        native.wait_for_socket(Alive(), root / "absent.sock", log)
            self.assertEqual(str(raised.exception),
                             "semwrightd did not expose the owner socket")
            self.assertNotIn(str(root), str(raised.exception))

    def test_ready_socket_is_read_only_and_never_restarts_provider(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)

            class Alive:
                polls = 0

                def poll(self):
                    self.polls += 1
                    return None

            process = Alive()
            with patch.object(native.time, "monotonic", side_effect=[0.0, 1.0]):
                with patch.object(Path, "is_socket", return_value=True):
                    self.assertIsNone(native.wait_for_socket(
                        process, root / "fixture.sock", root / "private.log"
                    ))
            self.assertEqual(process.polls, 1)


if __name__ == "__main__":
    unittest.main()
