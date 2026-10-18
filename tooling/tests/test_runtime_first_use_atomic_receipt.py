"""Real filesystem publication invariants for first-use support receipts.

Synthetic only. No owner connection, Broker, render, or platform installer.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime_first_use_audit as audit


class AtomicSupportReceiptTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix="mw-atomic-first-use-")
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name).resolve()
        self.destination = self.root / "first-use-report.json"
        self.value = {
            "schema": audit.REPORT_SCHEMA,
            "application": "Motionwright",
            "status": "FILES_VERIFIED_ONLY",
            "checks": {"no_mutations": True},
        }

    def test_fsync_error_never_exposes_partial_final_receipt(self):
        """The previous direct O_EXCL destination writer leaves bytes on EIO."""
        with patch.object(audit.os, "fsync", side_effect=OSError(errno.EIO, "synthetic failed sync")):
            with self.assertRaises(audit.AuditError) as caught:
                audit.write_new_receipt(self.destination, self.value)
        self.assertEqual(caught.exception.code, "receipt_write_refused")
        self.assertFalse(
            self.destination.exists(),
            "the final pathname must not exist until the whole JSON was staged and synced",
        )
        self.assertEqual(list(self.root.iterdir()), [], "own aborted stage must be removed")

    def test_first_fsync_sees_no_final_public_name(self):
        seen = []
        original = audit.os.fsync

        def check_before_flush(descriptor):
            if not seen:
                seen.append(self.destination.exists())
            return original(descriptor)

        with patch.object(audit.os, "fsync", side_effect=check_before_flush):
            audit.write_new_receipt(self.destination, self.value)
        self.assertEqual(seen, [False], "publish cannot precede the first successful fsync")
        self.assertEqual(
            json.loads(self.destination.read_bytes()), self.value,
            "new destination is a complete JSON document",
        )
        self.assertEqual(set(self.root.iterdir()), {self.destination})

    def test_failed_publication_preserves_a_foreign_destination(self):
        old = b"pre-existing user report bytes"
        self.destination.write_bytes(old)
        with self.assertRaises(audit.AuditError):
            audit.write_new_receipt(self.destination, self.value)
        self.assertEqual(self.destination.read_bytes(), old)
        self.assertEqual(set(self.root.iterdir()), {self.destination})

    def test_stage_link_failure_does_not_publish_or_keep_partial(self):
        """No path-only rename fallback if hardlink publication is refused."""
        with patch.object(audit.os, "link", side_effect=OSError(errno.EPERM, "synthetic link denied")) as link:
            with self.assertRaises(audit.AuditError) as caught:
                audit.write_new_receipt(self.destination, self.value)
        self.assertTrue(link.called)
        self.assertEqual(caught.exception.code, "receipt_write_refused")
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "directory fsync is POSIX-only")
    def test_post_publish_directory_sync_error_keeps_complete_report(self):
        """An uncertain durability result is not a refused prepublication."""
        original = audit.os.fsync
        calls = 0

        def refuse_directory_sync(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.EIO, "synthetic parent directory sync failed")
            return original(descriptor)

        with patch.object(audit.os, "fsync", side_effect=refuse_directory_sync):
            with self.assertRaises(audit.AuditError) as caught:
                audit.write_new_receipt(self.destination, self.value)
        self.assertEqual(caught.exception.code, "receipt_commit_uncertain")
        self.assertEqual(json.loads(self.destination.read_bytes()), self.value)
        self.assertEqual(set(self.root.iterdir()), {self.destination})

    def test_two_simultaneous_writers_have_one_complete_winner(self):
        start = threading.Barrier(3)
        outcomes: list[str] = []
        lock = threading.Lock()

        def worker(index: int):
            payload = {**self.value, "attempt": index}
            start.wait()
            try:
                audit.write_new_receipt(self.destination, payload)
                result = "success"
            except audit.AuditError as exc:
                self.assertEqual(exc.code, "receipt_write_refused")
                result = "conflict"
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(outcomes), ["conflict", "success"])
        saved = json.loads(self.destination.read_bytes())
        self.assertIn(saved["attempt"], (1, 2))
        self.assertEqual(self.value["schema"], saved["schema"])
        self.assertEqual(set(self.root.iterdir()), {self.destination})


    @contextmanager
    def _fail_final_readback_after_successful_link(self):
        """Fail observation only after the real filesystem published the name."""
        actual_link = audit.os.link
        actual_stat = audit.os.stat
        actual_lstat = audit.Path.lstat
        state = {"successful_links": 0, "readback_failures": 0}

        def is_first_final_observation(path):
            if (state["successful_links"] == 1
                    and state["readback_failures"] == 0
                    and os.fspath(path) in (self.destination.name, str(self.destination))):
                state["readback_failures"] += 1
                return True
            return False

        def link_then_observe(*args, **kwargs):
            result = actual_link(*args, **kwargs)
            state["successful_links"] += 1
            return result

        def fail_stat(path, *args, **kwargs):
            if is_first_final_observation(path):
                raise OSError(errno.EIO, "synthetic final readback failed")
            return actual_stat(path, *args, **kwargs)

        def fail_lstat(path, *args, **kwargs):
            if is_first_final_observation(path):
                raise OSError(errno.EIO, "synthetic final readback failed")
            return actual_lstat(path, *args, **kwargs)

        with (patch.object(audit.os, "link", side_effect=link_then_observe),
              patch.object(audit.os, "stat", side_effect=fail_stat),
              patch.object(audit.Path, "lstat", new=fail_lstat)):
            yield state

    def _run_report_cli(self):
        """Exercise the real output path with isolated collection input."""
        stdout = io.StringIO()
        with (patch.object(audit, "inspect_connection", return_value=self.value),
              patch.object(sys, "stdout", stdout)):
            exit_code = audit.main([
                "--connection-file", str(self.root / "fixture-connection.json"),
                "--project-id", "4e20b9ba-b5e5-4c59-9537-394a5091dc63",
                "--generation", "6db9f6b9-3f05-45bf-8457-bbd609948184",
                "--output", str(self.destination),
            ])
        return exit_code, json.loads(stdout.getvalue())

    def test_post_link_readback_failure_preserves_complete_receipt(self):
        with self._fail_final_readback_after_successful_link() as observed:
            with self.assertRaises(audit.AuditError) as caught:
                audit.write_new_receipt(self.destination, self.value)
        self.assertEqual(json.loads(self.destination.read_bytes()), self.value)
        self.assertEqual(set(self.root.iterdir()), {self.destination})
        self.assertEqual(observed, {"successful_links": 1, "readback_failures": 1})
        self.assertEqual(caught.exception.code, "receipt_commit_uncertain")

    def test_cli_preserves_uncertainty_after_real_receipt_publication(self):
        with self._fail_final_readback_after_successful_link() as observed:
            exit_code, output = self._run_report_cli()
        self.assertEqual(json.loads(self.destination.read_bytes()), self.value)
        self.assertEqual(set(self.root.iterdir()), {self.destination})
        self.assertEqual(observed, {"successful_links": 1, "readback_failures": 1})
        self.assertEqual(exit_code, 3)
        self.assertEqual(output, {
            "schema": audit.REPORT_SCHEMA,
            "status": "BLOCKED",
            "failure_code": "receipt_commit_uncertain",
        })

    def test_cli_refusal_keeps_preexisting_report_unchanged(self):
        original = b"existing report owned by another invocation"
        self.destination.write_bytes(original)
        exit_code, output = self._run_report_cli()
        self.assertEqual(exit_code, 3)
        self.assertEqual(self.destination.read_bytes(), original)
        self.assertEqual(set(self.root.iterdir()), {self.destination})
        self.assertEqual(output["failure_code"], "receipt_write_refused")


if __name__ == "__main__":
    unittest.main()
