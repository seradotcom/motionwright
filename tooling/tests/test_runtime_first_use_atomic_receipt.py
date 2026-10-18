"""Real filesystem publication invariants for first-use support receipts.

Synthetic only. No owner connection, Broker, render, or platform installer.
"""
from __future__ import annotations

import errno
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


if __name__ == "__main__":
    unittest.main()
