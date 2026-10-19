"""Negative controls for source-bound composition of the MLT planar repair."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve()
CANDIDATES = (
    HERE.parent.parent / "mlt_repair_verification.py",
    HERE.parent / "mlt_repair_verification.py",
)
MODULE_PATH = next(path for path in CANDIDATES if path.is_file())
SPEC = importlib.util.spec_from_file_location("mlt_repair_verification", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


class SourceCompositionTests(unittest.TestCase):
    def test_disjoint_repair_and_observer_changes_are_both_retained(self):
        original = b"header\nleft\nmiddle\nright\nfooter\n"
        repaired = b"header\nfixed left\nmiddle\nright\nfooter\n"
        observed = b"tap\nheader\nleft\nmiddle\nobserved right\nfooter\n"
        merged, proof = verify.merge_disjoint(original, repaired, observed)
        self.assertEqual(merged, b"tap\nheader\nfixed left\nmiddle\nobserved right\nfooter\n")
        self.assertEqual(proof["overlapping_edits"], 0)

    def test_two_replacements_of_the_same_source_line_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            verify.merge_disjoint(b"a\nb\nc\n", b"a\nrepair\nc\n", b"a\nobserve\nc\n")

    def test_insertion_inside_the_other_deleted_region_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            verify.merge_disjoint(b"a\nb\nc\nd\n", b"a\nd\n", b"a\nb\ntap\nc\nd\n")

    def test_competing_insertions_at_the_same_boundary_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            verify.merge_disjoint(b"a\nb\n", b"a\nrepair\nb\n", b"a\ntap\nb\n")

    def test_insertion_at_the_other_replacement_boundary_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            verify.merge_disjoint(b"a\nb\nc\n", b"a\nnew\nc\n", b"a\ntap\nb\nc\n")

    def test_repeated_text_does_not_select_a_different_source_occurrence(self):
        original = b"head\nsame\nmiddle\nsame\ntail\n"
        repaired = b"head\nchanged\nmiddle\nsame\ntail\n"
        observed = b"head\nsame\nmiddle\nobserved\ntail\n"
        result, _ = verify.merge_disjoint(original, repaired, observed)
        self.assertEqual(result, b"head\nchanged\nmiddle\nobserved\ntail\n")

    def test_line_endings_are_not_normalized_by_composition(self):
        original = b"a\r\nb\r\nc\r\n"
        repaired = b"a\r\nB\r\nc\r\n"
        observed = b"a\r\nb\r\nC\r\n"
        result, _ = verify.merge_disjoint(original, repaired, observed)
        self.assertEqual(result, b"a\r\nB\r\nC\r\n")

    def test_unpinned_original_is_rejected_before_composition(self):
        with self.assertRaisesRegex(ValueError, "original consumer digest"):
            verify.compose_pinned(b"foreign\n", b"repair\n", b"tap\n")


class SourceBindingTests(unittest.TestCase):
    def valid_fixture(self):
        sources = {"tooling/example.py": b"source\n"}
        receipt = {
            "schema": "motionwright.mlt-observer-source-inputs/1",
            "source_sha": "a" * 40,
            "tree": "b" * 40,
            "sources": {name: verify.sha(data) for name, data in sources.items()},
            "source_blob_oids": {name: verify.git_blob(data) for name, data in sources.items()},
        }
        return sources, receipt

    def test_exact_source_content_and_git_blob_are_required_together(self):
        sources, receipt = self.valid_fixture()
        verify.validate_binding(receipt, sources, "a" * 40)
        receipt["source_blob_oids"]["tooling/example.py"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "blob differs"):
            verify.validate_binding(receipt, sources, "a" * 40)

    def test_another_commit_cannot_borrow_the_source_receipt(self):
        sources, receipt = self.valid_fixture()
        with self.assertRaisesRegex(ValueError, "SHA differs"):
            verify.validate_binding(receipt, sources, "c" * 40)

    def test_unrecorded_source_or_digest_is_not_omitted(self):
        sources, receipt = self.valid_fixture()
        sources["tooling/additional.py"] = b"new\n"
        with self.assertRaisesRegex(ValueError, "inventory is not closed"):
            verify.validate_binding(receipt, sources, "a" * 40)

    def test_wrong_digest_is_rejected_even_when_blob_matches(self):
        sources, receipt = self.valid_fixture()
        receipt["sources"]["tooling/example.py"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "digest differs"):
            verify.validate_binding(receipt, sources, "a" * 40)

    def test_truncated_or_nonsha_source_identity_is_rejected(self):
        sources, receipt = self.valid_fixture()
        with self.assertRaisesRegex(ValueError, "invalid expected"):
            verify.validate_binding(receipt, sources, "a" * 39)
        receipt["tree"] = "invented"
        with self.assertRaisesRegex(ValueError, "invalid source tree"):
            verify.validate_binding(receipt, sources, "a" * 40)

    def test_duplicate_json_fields_cannot_replace_a_source_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "receipt.json"
            path.write_bytes(b'{"source_sha":"old","source_sha":"new"}')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                verify.read_json(path)

    def test_read_limits_prevent_unbounded_source_material(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source"
            path.write_bytes(b"12345")
            with self.assertRaisesRegex(ValueError, "bounded size"):
                verify.read_regular(path, limit=4)

    def test_existing_output_is_not_overwritten_or_replayed(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            target = directory / "source"
            target.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                verify.write_new(target, b"changed")
            self.assertEqual(target.read_bytes(), b"original")

    def test_existing_preparation_refuses_before_loading_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with self.assertRaisesRegex(ValueError, "already exists"):
                verify.prepare(directory, directory, directory / "missing.json",
                               directory / "missing.c", directory, "a" * 40)

    def test_missing_prepared_outputs_are_rejected_without_a_success_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "output set is not closed"):
                verify.verify_prepared(Path(folder), {})

    def test_source_preparation_never_claims_a_native_result(self):
        sources, receipt = self.valid_fixture()
        prepared = verify.receipt_for_outputs(
            receipt, json.dumps(receipt).encode(), sources, {"overlapping_edits": 0}
        )
        for name in verify.FALSE_CLAIMS:
            self.assertIs(prepared[name], False, name)
        self.assertEqual(prepared["C_layout_checks"], "NOT_RUN")
        self.assertEqual(prepared["full_consumer_compile"], "NOT_RUN")
        self.assertEqual(prepared["physical_runtime"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
