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

    def test_successful_link_then_metadata_error_is_commit_uncertain(self):
        """The public report may exist even if its post-link stat fails.

        Exercise actual os.link on real synthetic filesystem files, then
        simulate a single failed metadata read from the just-created public
        name. The original effect must not be repeated or overwritten.
        """
        original_link = audit.os.link
        original_stat = audit.os.stat
        original_lstat = Path.lstat
        linked = False
        failed_reads = 0

        def traced_link(*args, **kwargs):
            nonlocal linked
            result = original_link(*args, **kwargs)
            linked = True
            return result

        def failed_stat(path, *args, **kwargs):
            nonlocal failed_reads
            if linked and path == self.destination.name and "dir_fd" in kwargs:
                failed_reads += 1
                raise OSError(errno.EIO, "synthetic stat after successful link")
            return original_stat(path, *args, **kwargs)

        def failed_lstat(path, *args, **kwargs):
            nonlocal failed_reads
            if linked and Path(path) == self.destination:
                failed_reads += 1
                raise OSError(errno.EIO, "synthetic lstat after successful link")
            return original_lstat(path, *args, **kwargs)

        with patch.object(audit.os, "link", side_effect=traced_link):
            if os.name == "posix":
                inspector = patch.object(audit.os, "stat", side_effect=failed_stat)
            else:
                inspector = patch.object(Path, "lstat", new=failed_lstat)
            with inspector, self.assertRaises(audit.AuditError) as caught:
                audit.write_new_receipt(self.destination, self.value)

        self.assertTrue(linked)
        self.assertEqual(failed_reads, 1)
        # The durable source bytes exist before checking the exception label:
        # no claim of a safe-to-retry prepublication failure is defensible.
        self.assertEqual(json.loads(self.destination.read_bytes()), self.value)
        self.assertEqual(set(self.root.iterdir()), {self.destination})
        self.assertEqual(
            caught.exception.code, "receipt_commit_uncertain",
            "published bytes cannot be labeled safe-to-retry after a failed stat",
        )

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


if __name__ == "__main__":
    unittest.main()
