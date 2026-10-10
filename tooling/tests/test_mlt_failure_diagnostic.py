"""Adversarial tests for the public-safe Motion Canvas observation classifier."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "mlt_failure_diagnostic.py"
SPEC = importlib.util.spec_from_file_location("mlt_failure_diagnostic", SCRIPT)
assert SPEC and SPEC.loader
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


class MltRenderDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.owned = tempfile.TemporaryDirectory()
        self.addCleanup(self.owned.cleanup)
        self.root = Path(self.owned.name)
        self.job = self.root / ("render-" + "a" * 32)
        self.job.mkdir()
        self.receipt = self.job / "native-failure-receipt.json"

    def write(self, *, stack="Error: native observation count incomplete", digest="b" * 64,
              failure_class="observation", version=2):
        body = {
            "version": version, "ok": False, "render_input_digest": digest,
            "error_class": failure_class, "runtime_module": None,
            "exception_name": "Error", "exception_code": None,
            "local_stack_only": stack,
        }
        self.receipt.write_text(json.dumps(body), encoding="utf-8")

    def test_bounded_observation_reports_class_without_paths_or_raw_stack(self):
        private = "/home/customer/private/paid-project/credentials/secret.json"
        self.write(stack=f"Error: native observation count incomplete {private}\n at {private}")
        observed = diagnostic.safe_observation_diagnostic(self.root)
        self.assertEqual(observed["status"], "identified")
        self.assertEqual(observed["reason"], "observation_count_incomplete")
        self.assertEqual(observed["receipts_examined"], 1)
        self.assertFalse(observed["raw_driver_text_exposed"])
        self.assertFalse(observed["source_mutated"])
        self.assertNotIn(private, json.dumps(observed))
        self.assertTrue(self.receipt.exists())

    def test_count_only_render_diagnostics_expose_no_names_or_media(self):
        private = "/home/customer/private/secret-video.mp4"
        self.write(stack="Error: native observation count incomplete " + private)
        frame_root = self.job / "frames"
        frame_root.mkdir()
        (frame_root / "000000.png").write_bytes(b"PRIVATE PNG CONTENT")
        (frame_root / "000002.png").write_bytes(b"MORE PRIVATE PNG CONTENT")
        (self.job / "native-observations.ndjson").write_bytes(
            b'{"private":"/home/customer/private"}\n{"private":"second"}\n'
        )
        observed = diagnostic.safe_observation_diagnostic(self.root)
        self.assertEqual(observed["exported_png_count"], 2)
        self.assertEqual(observed["observation_line_count"], 2)
        self.assertEqual((observed["first_png_frame"], observed["last_png_frame"]), (0, 2))
        self.assertNotIn(private, json.dumps(observed))
        self.assertNotIn("PRIVATE", json.dumps(observed))
        self.assertTrue(self.receipt.exists())
        # Never follow an observation-file symlink outside the owner directory.
        linked = self.job / "native-observations.ndjson"
        linked.rename(self.job / "old-observations.ndjson")
        linked.symlink_to(self.root / "missing-external")
        self.assertIsNone(diagnostic.safe_observation_diagnostic(self.root)["observation_line_count"])

    def test_other_supported_reason_stays_finite_and_safe(self):
        for source, expected in diagnostic.REASON_MARKERS.items():
            with self.subTest(reason=expected):
                self.write(stack=f"Error: {source}\n at /private/very-sensitive/file")
                result = diagnostic.safe_observation_diagnostic(self.root)
                self.assertEqual(result["status"], "identified")
                self.assertEqual(result["reason"], expected)

    def test_malformed_and_untrusted_receipts_never_reveal_content(self):
        self.write(stack="Error: owner screenshot /secret/path", digest="x" * 64)
        result = diagnostic.safe_observation_diagnostic(self.root)
        self.assertEqual(result["status"], "unclassified")
        self.assertEqual(result["reason"], "unknown")
        self.write(stack="Error: native observation count incomplete", version=1)
        self.assertEqual(diagnostic.safe_observation_diagnostic(self.root)["reason"], "unknown")
        self.write(failure_class="../../../debug/malicious")
        self.assertEqual(diagnostic.safe_observation_diagnostic(self.root)["status"], "unclassified")
        self.receipt.write_text("not-json", encoding="utf-8")
        self.assertEqual(diagnostic.safe_observation_diagnostic(self.root)["reason"], "unknown")
        self.assertTrue(self.receipt.exists())

    def test_missing_oversized_or_symlinked_receipt_is_never_followed(self):
        self.assertEqual(diagnostic.safe_observation_diagnostic(self.root)["status"], "unclassified")
        self.write()
        self.receipt.write_bytes(b"x" * (diagnostic.MAX_RECEIPT_BYTES + 1))
        self.assertEqual(diagnostic.safe_observation_diagnostic(self.root)["reason"], "unknown")
        self.receipt.unlink()  # Only delete this test-created temporary receipt.
        outside = self.root / "outside"
        outside.write_text("Error: private", encoding="utf-8")
        self.receipt.symlink_to(outside)
        self.assertEqual(diagnostic.safe_observation_diagnostic(self.root)["reason"], "unknown")

    def test_noncanonical_output_directory_and_symlink_root_fail_closed(self):
        self.write()
        other = self.root / "wrong-prefix"
        self.job.rename(other)
        self.assertEqual(diagnostic.safe_observation_diagnostic(self.root)["reason"], "unknown")
        with tempfile.TemporaryDirectory() as external:
            alias = Path(external) / "unsafe"
            alias.symlink_to(self.root, target_is_directory=True)
            self.assertEqual(diagnostic.safe_observation_diagnostic(alias)["status"], "unclassified")


if __name__ == "__main__":
    unittest.main()
