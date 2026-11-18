"""File-lifetime tests only; these do not qualify cryptography or Native."""
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import stable_envelope as envelope
from stable_file_read import StableReadRejected


class Snapshot:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class StableEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "retained.bin"
        self.raw = b"file-lifetime-fixture" * 8000
        self.path.write_bytes(self.raw)
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.reference = envelope.envelope_reference(
            self.path, self.digest, maximum=len(self.raw) + 1)

    def call(self, consumer, *, reference=None):
        reserve = lambda: None
        bounded = SimpleNamespace(decrypt_stream=consumer, require_reserve=reserve)
        self.header, self.key, self.seal = object(), object(), object()
        return envelope.decrypt_retained(
            self.path, self.reference if reference is None else reference,
            self.header, self.key, self.seal, bounded=bounded,
            maximum=len(self.raw) + 1)

    def test_reference_keeps_exact_bytes_digest_and_no_copy(self):
        self.assertEqual(self.reference["path"], str(self.path))
        self.assertEqual(self.reference["bytes"], len(self.raw))
        self.assertEqual(self.reference["sha256"], self.digest)
        self.assertEqual(self.reference["namespace_identity"][4], len(self.raw))
        self.assertIs(self.reference["copied"], False)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_wrong_digest_and_whole_file_budget_reject(self):
        with self.assertRaisesRegex(envelope.StableEnvelopeRejected,
                                    "envelope_digest_differs"):
            envelope.envelope_reference(self.path, "0" * 64, maximum=len(self.raw))
        with self.assertRaisesRegex(StableReadRejected, "regular_file_budget"):
            envelope.envelope_reference(self.path, self.digest,
                                        maximum=len(self.raw) - 1)

    def test_foreign_or_malformed_reference_never_calls_consumer(self):
        variants = [
            {**self.reference, "path": str(self.root / "foreign.bin")},
            {**self.reference, "bytes": True},
            {**self.reference, "copied": True},
            {**self.reference, "sha256": "0" * 63},
            {**self.reference, "unexpected": 1},
        ]
        for reference in variants:
            with self.subTest(reference=reference):
                with self.assertRaisesRegex(envelope.StableEnvelopeRejected,
                                            "stream_reference_binding"):
                    self.call(lambda *a, **k: self.fail("Consumer called"),
                              reference=reference)

    def test_replaced_original_rejects_before_consumer(self):
        self.path.unlink()
        self.path.write_bytes(b"changed" + self.raw[7:])
        with self.assertRaisesRegex(envelope.StableEnvelopeRejected,
                                    "stream_namespace_changed"):
            self.call(lambda *a, **k: self.fail("Consumer called"))

    def test_unrelated_sibling_change_does_not_discard_snapshot(self):
        snapshot, handles = Snapshot(), []
        def consume(reader, size, digest, header, key, seal, *, reserve_guard):
            handles.append(reader)
            self.assertEqual((size, digest), (len(self.raw), self.digest))
            self.assertIs(header, self.header)
            self.assertIs(key, self.key)
            self.assertIs(seal, self.seal)
            self.assertTrue(callable(reserve_guard))
            (self.root / "unrelated.txt").write_bytes(b"directory content")
            self.assertEqual(reader.read(), self.raw)
            return snapshot
        self.assertIs(self.call(consume), snapshot)
        self.assertFalse(snapshot.closed)
        self.assertTrue(handles[0].closed)
        snapshot.close()

    def test_original_mutation_after_consumer_return_wipes_snapshot(self):
        snapshot, handles = Snapshot(), []
        def consume(reader, *args, **kwargs):
            handles.append(reader)
            with self.path.open("ab") as writer:
                writer.write(b"x")
                writer.flush()
                os.fsync(writer.fileno())
            return snapshot
        with self.assertRaisesRegex(StableReadRejected,
                                    "input_changed_during_read"):
            self.call(consume)
        self.assertTrue(snapshot.closed)
        self.assertTrue(handles[0].closed)

    def test_consumer_failure_is_preserved_and_file_closed(self):
        original = RuntimeError("consumer rejection")
        handles = []
        def consume(reader, *args, **kwargs):
            handles.append(reader)
            raise original
        with self.assertRaises(RuntimeError) as caught:
            self.call(consume)
        self.assertIs(caught.exception, original)
        self.assertTrue(handles[0].closed)

    def test_closed_descriptor_after_consumer_return_wipes_snapshot(self):
        snapshot = Snapshot()
        def consume(reader, *args, **kwargs):
            os.close(reader.fileno())
            return snapshot
        with self.assertRaises(OSError):
            self.call(consume)
        self.assertTrue(snapshot.closed)


if __name__ == "__main__":
    unittest.main()
