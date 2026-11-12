"""Explicit metadata-reader routing and real-file controls; no Native effects.

The source-gate log fixtures below are intentionally incomplete and rejected.
They are never evidence of Rust execution or a runtime acceptance result.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import native_manim_frozen_runtime as frozen
import native_manim_frozen_runtime_restore as reuse
import native_manim_resume_host as host
import native_manim_resume_transfer as transfer


class ExplicitMetadataReaderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()

    def original(self, name, raw):
        path = self.root / name
        path.write_bytes(b"previous bytes")
        with path.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        return path

    def test_reader_preserves_actual_metadata_bytes_identity_and_closes_handle(self):
        raw = b'{"retained":"original metadata"}\n'
        path = self.original("metadata.json", raw)
        identity, observed = frozen.metadata_original(path, len(raw), reader=host.bounded)
        self.assertEqual(observed, raw)
        self.assertEqual(identity, {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        self.assertEqual(frozen.object_file(path, reader=host.bounded),
                         {"retained": "original metadata"})
        path.rename(self.root / "closed.json")

    def test_same_strict_json_parser_rejects_duplicate_keys(self):
        raw = b'{"source_sha":"first","source_sha":"second"}\n'
        path = self.original("duplicate.json", raw)
        with self.assertRaisesRegex(ValueError, "frozen_runtime_duplicate_json_key"):
            frozen.object_file(path, reader=host.bounded)
        self.assertEqual(path.read_bytes(), raw)
        path.rename(self.root / "rejected.json")

    def test_reader_result_cannot_expand_metadata_budget(self):
        path = self.original("oversized.original", b"retained bytes")
        # This deliberately incomplete reader supplies actual file bytes while
        # ignoring its limit. The metadata adapter must still refuse them.
        def oversized_reader(source, maximum):
            return source.read_bytes()
        with self.assertRaisesRegex(ValueError, "frozen_runtime_metadata_reader_bytes"):
            frozen.metadata_original(path, 3, reader=oversized_reader)
        self.assertEqual(path.read_bytes(), b"retained bytes")

    def test_both_scenarios_use_reader_before_rejecting_incomplete_original_logs(self):
        self.original("source-gate.json", b"{}\n")
        self.original("source-gate.stdout.log", b"")
        self.original("source-gate.stderr.log", b"")
        previous = os.environ.get("MANIM_NATIVE_SCENARIO")
        try:
            for scenario, limit in ((transfer.RESUME_SCENARIO, 4 * 1024 * 1024),
                                    (transfer.RECOVERY_SCENARIO, 1024 * 1024)):
                with self.subTest(scenario=scenario):
                    os.environ["MANIM_NATIVE_SCENARIO"] = scenario
                    reads = []
                    def custodied(path, maximum):
                        reads.append((path.name, maximum))
                        return host.bounded(path, maximum)
                    with self.assertRaisesRegex(ValueError, "resume_eight_actual_recovery_tests_required"):
                        transfer.selected_source_gate({}, self.root, reader=custodied)
                    self.assertEqual(reads, [("source-gate.json", 128 * 1024),
                                             ("source-gate.stdout.log", limit),
                                             ("source-gate.stderr.log", limit)])
        finally:
            if previous is None:
                os.environ.pop("MANIM_NATIVE_SCENARIO", None)
            else:
                os.environ["MANIM_NATIVE_SCENARIO"] = previous

    def test_nested_origin_metadata_reads_both_pinned_originals_through_custodian(self):
        reads = []
        def custodied(path, maximum):
            reads.append(path)
            return host.bounded(path, maximum)
        receipt, artifact = reuse.origin_metadata(reader=custodied)
        self.assertEqual(reads, [reuse.RECEIPT_PATH, reuse.ARTIFACT_PATH])
        self.assertEqual(receipt["context"], reuse.ORIGIN)
        self.assertEqual(receipt["archive"], artifact["tar"])
        self.assertFalse(receipt["old_runtime_equivalence_claimed"])

    def test_current_sdk_source_lock_is_read_through_custodian(self):
        current = {**reuse.ORIGIN, "source_sha": "a" * 40, "run_id": "1",
                   "semwright_sha": reuse.CURRENT_SDK}
        reads = []
        def custodied(path, maximum):
            reads.append(path)
            return host.bounded(path, maximum)
        relation = reuse.sdk_relation(current, reader=custodied)
        self.assertEqual(reads, [reuse.SOURCE_LOCK_PATH])
        self.assertEqual(relation["source_lock_sha256"], reuse.CURRENT_SOURCE_LOCK_SHA)
        self.assertFalse(relation["native_effects_attested"])


if __name__ == "__main__":
    unittest.main()
