"""Held-input controls; no original archives, keys, or Native effects."""
import os
from pathlib import Path
import tempfile
import unittest

import stable_file_read as stable
from stable_file_stream import open_stable


class StableStreamTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "source.bin"
        self.raw = b"retained-original" * 100
        self.path.write_bytes(self.raw)

    def test_streaming_with_unrelated_directory_content_change_preserves_bytes(self):
        with open_stable(self.path, len(self.raw)) as held:
            first = held.stream.read(31)
            (self.root / "unrelated.txt").write_text("directory content changed")
            rest = held.stream.read(len(self.raw))
            self.assertEqual(held.namespace_identity[4], len(self.raw))
            self.assertEqual(first + rest, self.raw)
        self.assertTrue(held.stream.closed)

    def test_partial_header_read_is_allowed_and_descriptor_is_closed(self):
        with open_stable(self.path, len(self.raw)) as held:
            self.assertEqual(held.stream.read(8), self.raw[:8])
        self.assertTrue(held.stream.closed)

    def test_mutation_of_held_original_is_rejected_and_descriptor_is_closed(self):
        with self.assertRaises(stable.StableReadRejected) as rejected:
            with open_stable(self.path, len(self.raw) + 10) as held:
                self.assertEqual(held.stream.read(5), self.raw[:5])
                with self.path.open("ab") as writer:
                    writer.write(b"changed")
                    writer.flush()
                    os.fsync(writer.fileno())
        self.assertEqual(rejected.exception.code, "input_changed_during_read")
        self.assertEqual(rejected.exception.diagnostic["stage"], "descriptor")
        self.assertTrue(held.stream.closed)

    def test_caller_failure_preserved_and_handle_closed_even_when_exit_check_fails(self):
        for mutate in (False, True):
            with self.subTest(mutate=mutate):
                self.path.write_bytes(self.raw)
                primary = RuntimeError("primary caller failure")
                with self.assertRaises(RuntimeError) as caught:
                    with open_stable(self.path, len(self.raw) + 10) as held:
                        if mutate:
                            with self.path.open("ab") as writer:
                                writer.write(b"changed")
                                writer.flush()
                                os.fsync(writer.fileno())
                        raise primary
                self.assertIs(caught.exception, primary)
                self.assertTrue(held.stream.closed)
                notes = getattr(primary, "__notes__", [])
                self.assertEqual(bool(notes), mutate)
                if mutate:
                    self.assertIn("input_changed_during_read", notes[0])

    def test_entire_file_budget_is_checked_before_yield(self):
        for limit in (len(self.raw) - 1, -1, True):
            with self.subTest(limit=limit):
                yielded = False
                with self.assertRaisesRegex(stable.StableReadRejected, "regular_file_budget"):
                    with open_stable(self.path, limit):
                        yielded = True
                self.assertFalse(yielded)

    def test_raw_descriptor_close_cannot_replace_the_callers_primary_error(self):
        primary = RuntimeError("primary caller failure")
        with self.assertRaises(RuntimeError) as caught:
            with open_stable(self.path, len(self.raw)) as held:
                os.close(held.stream.fileno())
                raise primary
        self.assertIs(caught.exception, primary)
        self.assertTrue(held.stream.closed)
        self.assertGreaterEqual(len(primary.__notes__), 2)

    def test_failed_descriptor_verification_remains_primary_when_close_also_fails(self):
        with self.assertRaises(OSError) as caught:
            with open_stable(self.path, len(self.raw)) as held:
                os.close(held.stream.fileno())
        self.assertTrue(held.stream.closed)
        self.assertTrue(any("stream close also failed" in note
                            for note in caught.exception.__notes__))

    def test_fdopen_failure_survives_a_secondary_descriptor_close_failure(self):
        from unittest.mock import patch
        primary = RuntimeError("fdopen failed")
        real_close = os.close
        closed = []
        def close_then_fail(descriptor):
            real_close(descriptor)
            closed.append(descriptor)
            raise OSError("close diagnostic")
        with patch("stable_file_stream.os.fdopen", side_effect=primary), \
             patch("stable_file_stream.os.close", side_effect=close_then_fail):
            with self.assertRaises(RuntimeError) as caught:
                with open_stable(self.path, len(self.raw)):
                    self.fail("fdopen failure yielded")
        self.assertIs(caught.exception, primary)
        self.assertEqual(len(closed), 1)
        self.assertTrue(any("descriptor close also failed" in note
                            for note in primary.__notes__))

    def test_missing_file_never_yields_a_descriptor(self):
        with self.assertRaisesRegex(stable.StableReadRejected, "input_unavailable"):
            with open_stable(self.root / "absent.bin", 10):
                self.fail("A missing input yielded a descriptor")


if __name__ == "__main__":
    unittest.main()
