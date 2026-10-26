import copy
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import MagicMock, patch
import native_film180_original_g2 as runner


class FakeDaemon:
    pid = 3123
    def __init__(self, wait_error=None):
        self.returncode = None
        self.terminate_calls = 0
        self.wait_calls = 0
        self.wait_error = wait_error
    def poll(self):
        return self.returncode
    def terminate(self):
        self.terminate_calls += 1
    def wait(self, *, timeout):
        self.wait_calls += 1
        if self.wait_error is not None:
            raise self.wait_error
        self.returncode = 0
        return 0


class FixedTextOwnerCloseContracts(unittest.TestCase):
    identity = (7, 11, 1000, 0o40700)

    def setup_case(self, *, known=True, wait_error=None, change=None):
        directory = MagicMock()
        directory.lstat.return_value = SimpleNamespace(st_dev=7, st_ino=11, st_uid=1000, st_mode=0o40700)
        directory.resolve.return_value = directory
        directory.is_dir.return_value = True
        directory.is_symlink.return_value = False
        daemon = FakeDaemon(wait_error)
        attempt = SimpleNamespace(native_commands={"synthetic.render": "SUCCESS_REPORTED" if known else "ERROR_OR_UNKNOWN"},
                                  daemon_identity=(daemon.pid, 42), owner_shutdown_requested=False, owner_custody_close=None)
        closed = {"schema": "semwright.host-stdin-custody-close/1", "status": "observer_closed", "reason": "owner_shutdown",
                  "capture_records_written": 1, "terminal_records_written": 1, "input_bytes_reserved": 32,
                  "queued_captures_discarded": 0, "queue_disconnection_proven": True,
                  "loss_counters_at_observer_close": {"capture_full": 0, "capture_closed": 0, "capture_oversized": 0, "terminal_receiver_lost": 0},
                  "expected_job_coverage": "caller_must_join_original_job_and_parent_ids",
                  "child_consumption": "not_observed", "native_render_success": "not_inferred"}
        if change:
            change(closed)
        original_close = json.dumps(closed).encode()
        selected = {"selected_capture_join": {"matched": True, "ordinal": 1, "pipe_write_all_completed": True, "capture": {"stdin_bytes": 32}}}
        publications, originals = [], []
        contexts = [
            patch.object(runner.observed, "process_identity", return_value=attempt.daemon_identity),
            patch.object(runner, "regular", side_effect=[(original_close, None), (json.dumps(selected).encode(), None)]),
            patch.object(runner, "write_private_json", side_effect=lambda path, value: publications.append((path.name, copy.deepcopy(value)))),
            patch.object(runner, "new_bytes", side_effect=lambda path, value: originals.append((path.name, value))),
        ]
        return directory, daemon, attempt, original_close, publications, originals, contexts

    def invoke(self, case):
        directory, daemon, attempt, _, _, _, contexts = case
        with contexts[0], contexts[1], contexts[2], contexts[3]:
            runner.fixed_owner_close(daemon, directory, Path("/synthetic-private-evidence"), attempt, self.identity)

    def test_unknown_never_requests_daemon_shutdown_or_waits(self):
        case = self.setup_case(known=False)
        self.invoke(case)
        _, daemon, attempt, _, publications, originals, _ = case
        self.assertEqual((daemon.terminate_calls, daemon.wait_calls), (0, 0))
        self.assertFalse(attempt.owner_custody_close["loss_free_close_observed"])
        self.assertFalse(attempt.owner_custody_close["termination_requested"])
        self.assertEqual(originals, [])
        self.assertEqual([name for name, _ in publications], ["owner-custody-close.observation.json"])

    def test_original_wait_error_is_retained_and_not_replaced(self):
        error = subprocess.TimeoutExpired(["synthetic-daemon-no-process"], 10)
        case = self.setup_case(wait_error=error)
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            self.invoke(case)
        self.assertIs(raised.exception, error)
        _, daemon, _, _, publications, originals, _ = case
        self.assertEqual((daemon.terminate_calls, daemon.wait_calls), (1, 1))
        terminal = next(value for name, value in publications if name == "owner-shutdown.terminal.json")
        self.assertEqual(terminal["wait_error_type"], "TimeoutExpired")
        self.assertIsNone(terminal["observed_exit_code"])
        self.assertFalse(terminal["receiver_close_inferred_from_process_exit"])
        self.assertEqual(originals, [])

    def test_loss_counters_cannot_be_promoted_to_complete_close(self):
        for counter in ("capture_full", "capture_closed", "capture_oversized", "terminal_receiver_lost"):
            with self.subTest(counter=counter):
                case = self.setup_case(change=lambda value: value["loss_counters_at_observer_close"].__setitem__(counter, 1))
                with self.assertRaises(runner.Film180G2Rejected):
                    self.invoke(case)
                self.assertEqual(case[5], [("owner-closed.original.json", case[3])])
                self.assertFalse(case[2].owner_custody_close["loss_free_close_observed"])
                self.assertEqual(case[1].terminate_calls, 1)

    def test_boolean_counts_do_not_satisfy_original_integer_contract(self):
        for field in ("capture_records_written", "terminal_records_written", "input_bytes_reserved"):
            with self.subTest(field=field):
                case = self.setup_case(change=lambda value: value.__setitem__(field, True))
                with self.assertRaises(runner.Film180G2Rejected):
                    self.invoke(case)
                self.assertEqual(case[5], [("owner-closed.original.json", case[3])])
                self.assertFalse(case[2].owner_custody_close["loss_free_close_observed"])

    def test_known_success_requires_original_close_bytes_and_terminal(self):
        case = self.setup_case()
        self.invoke(case)
        _, daemon, attempt, original_close, _, originals, _ = case
        self.assertEqual((daemon.terminate_calls, daemon.wait_calls), (1, 1))
        self.assertEqual(originals, [("owner-closed.original.json", original_close)])
        self.assertTrue(attempt.owner_custody_close["loss_free_close_observed"])
        self.assertEqual(attempt.owner_custody_close["child_consumption"], "not_observed")
        self.assertFalse(attempt.owner_custody_close["formal_acceptance"])


    def v4_attempt(self, daemon, *, known=True):
        attempt = runner.FilmAttempt({}, Path("/synthetic-private-evidence"))
        attempt.fixed_text_v4 = {"arm": "GREEN", "fixtures": ["synthetic"]}
        attempt.native_commands = {
            "synthetic.render": "SUCCESS_REPORTED" if known else "ERROR_OR_UNKNOWN"
        }
        attempt.daemon_identity = (daemon.pid, 42)
        attempt.owner_shutdown_requested = False
        attempt.owner_custody_close = None
        return attempt

    def terminal_publication_failure_case(self, *, wait_error=None):
        case = self.setup_case(wait_error=wait_error)
        directory, daemon, _, original_close, publications, originals, contexts = case
        attempt = self.v4_attempt(daemon)
        publication_error = OSError("synthetic terminal custody failure; no process")

        def publish(path, value):
            if path.name == "owner-shutdown.terminal.json":
                raise publication_error
            publications.append((path.name, copy.deepcopy(value)))

        contexts[1] = patch.object(
            runner, "regular",
            side_effect=AssertionError("close bytes must not be read after an unsettled wait/publication"),
        )
        contexts[2] = patch.object(runner, "write_private_json", side_effect=publish)
        changed = (directory, daemon, attempt, original_close, publications, originals, contexts)
        return changed, publication_error

    def test_wait_timeout_survives_terminal_publication_and_warning_failures(self):
        original = subprocess.TimeoutExpired(["synthetic-daemon-no-process"], 10)
        case, _ = self.terminal_publication_failure_case(wait_error=original)
        with patch.object(runner.observed, "warn_secondary", side_effect=OSError("warning failed")):
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                self.invoke(case)
        self.assertIs(raised.exception, original)
        _, daemon, attempt, _, publications, originals, _ = case
        self.assertEqual((daemon.terminate_calls, daemon.wait_calls), (1, 1))
        self.assertEqual([name for name, _ in publications], ["owner-shutdown.intent.json"])
        self.assertEqual(originals, [])
        self.assertIsNone(attempt.owner_custody_close)
        self.assertIn("owner-shutdown-terminal-custody", attempt.secondary_failures)
        self.assertIn("owner-shutdown-terminal-custody.warning", attempt.secondary_failures)

    def test_successful_wait_keeps_terminal_publication_error_as_primary(self):
        case, original = self.terminal_publication_failure_case()
        with patch.object(runner.observed, "warn_secondary", side_effect=OSError("warning failed")):
            with self.assertRaises(OSError) as raised:
                self.invoke(case)
        self.assertIs(raised.exception, original)
        _, daemon, attempt, _, publications, originals, _ = case
        self.assertEqual((daemon.terminate_calls, daemon.wait_calls), (1, 1))
        self.assertEqual(daemon.returncode, 0)
        self.assertEqual([name for name, _ in publications], ["owner-shutdown.intent.json"])
        self.assertEqual(originals, [])
        self.assertIsNone(attempt.owner_custody_close)
        self.assertIn("owner-shutdown-terminal-custody.warning", attempt.secondary_failures)

    def test_v4_error_retention_cannot_replace_original_unknown_on_warning_failure(self):
        attempt = self.v4_attempt(FakeDaemon(), known=False)
        original = RuntimeError("synthetic original UNKNOWN; no native process")
        before = dict(attempt.native_commands)
        with patch.object(runner, "write_private_json", side_effect=OSError("error custody failed")), \
                patch.object(runner.observed, "warn_secondary", side_effect=OSError("warning failed")):
            attempt.retain_error(original)
        self.assertIs(attempt.error, original)
        self.assertEqual(attempt.native_commands, before)
        self.assertEqual(set(attempt.native_commands.values()), {"ERROR_OR_UNKNOWN"})
        self.assertIn("original-error-custody", attempt.secondary_failures)
        self.assertIn("original-error-custody.warning", attempt.secondary_failures)

    def test_v4_subprocess_error_retention_preserves_original_when_all_custody_fails(self):
        attempt = self.v4_attempt(FakeDaemon(), known=False)
        original = subprocess.TimeoutExpired(["synthetic-no-process"], 10,
                                             output=b"original stdout", stderr=b"original stderr")
        before = dict(attempt.native_commands)
        with patch.object(runner, "new_bytes", side_effect=OSError("stream custody failed")), \
                patch.object(runner, "write_private_json", side_effect=OSError("error custody failed")), \
                patch.object(runner.observed, "warn_secondary", side_effect=OSError("warning failed")):
            attempt.retain_error(original)
        self.assertIs(attempt.error, original)
        self.assertEqual(attempt.native_commands, before)
        self.assertIn("original.failed-subprocess.capture.warning", attempt.secondary_failures)
        self.assertIn("original-subprocess-custody.warning", attempt.secondary_failures)
        self.assertIn("original-error-custody.warning", attempt.secondary_failures)

    def test_ordinary_film_warning_behavior_is_unchanged(self):
        attempt = runner.FilmAttempt({}, Path("/synthetic-private-evidence"))
        self.assertIsNone(attempt.fixed_text_v4)
        original = OSError("original ordinary warning behavior")
        with patch.object(runner.observed, "warn_secondary", side_effect=original):
            with self.assertRaises(OSError) as raised:
                attempt.secondary("ordinary")
        self.assertIs(raised.exception, original)
        self.assertEqual(attempt.secondary_failures, ["ordinary"])


if __name__ == "__main__":
    unittest.main()
