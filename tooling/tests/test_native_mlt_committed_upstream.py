"""FixedTxt source-bound original MLT tarball: offline, fail-closed tests.

No additional workflow, network request, Native process, fake permission,
compiler execution or acceptance. The upstream original is not regenerated.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_g2_custody as custody
import native_fixedtxt_g2_admission as fixedtxt

REAL_ROOT = Path(__file__).resolve().parents[2]
REAL_ORIGINAL = REAL_ROOT / custody.PINNED_UPSTREAM_SOURCE


class CommittedUpstreamOriginalTests(unittest.TestCase):
    def setUp(self):
        # Use a caller-supplied E: temp location. Never fall back to C:.
        root = Path(os.environ["TEMP"])
        if os.name == "nt":
            self.assertEqual(root.drive.upper(), "E:")
        self.assertTrue(root.is_absolute())
        self.sandbox = tempfile.TemporaryDirectory(prefix="g2-pinned-original-", dir=root)
        self.addCleanup(self.sandbox.cleanup)
        self.dir = Path(self.sandbox.name)
        self.output = self.dir / "intake"
        self.output.mkdir()

    def test_committed_full_archive_has_the_frozen_digest_and_is_selected(self):
        original = REAL_ORIGINAL.read_bytes()
        self.assertEqual(len(original), 1659954)
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         custody.UPSTREAM_ARCHIVE_SHA256)
        self.assertIn(custody.PINNED_UPSTREAM_SOURCE, fixedtxt.source_paths())
        self.assertEqual(custody.original_from_upstream(original).__len__(), 96690)

    def test_exact_committed_original_is_retained_without_HTTP(self):
        with mock.patch.object(custody.urllib.request, "build_opener",
                               side_effect=AssertionError("HTTP must not run")):
            raw = custody.committed_upstream_original(REAL_ROOT, self.output)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         custody.UPSTREAM_ARCHIVE_SHA256)
        self.assertEqual((self.output / "upstream-source.tar.gz").read_bytes(), raw)
        intent = json.loads((self.output / "upstream-download.intent.json").read_bytes())
        terminal = json.loads((self.output / "upstream-download.terminal.json").read_bytes())
        self.assertFalse(intent["http_request_performed"])
        self.assertEqual(intent["git_source_path"], custody.PINNED_UPSTREAM_SOURCE)
        self.assertEqual(terminal["status"], "VERIFIED_COMMITTED_PINNED_BYTES")
        self.assertFalse(terminal["automatic_retry"])
        self.assertFalse(terminal["http_request_performed"])

    def test_wrong_bytes_are_rejected_without_second_source(self):
        fake_repo = self.dir / "invalid"
        path = fake_repo / custody.PINNED_UPSTREAM_SOURCE
        path.parent.mkdir(parents=True)
        path.write_bytes(b"forged source")
        output = self.dir / "wrong"
        output.mkdir()
        with mock.patch.object(custody.urllib.request, "build_opener",
                               side_effect=AssertionError("HTTP fallback forbidden")):
            with self.assertRaisesRegex(custody.CustodyRejected,
                                        "committed_upstream_source_digest"):
                custody.committed_upstream_original(fake_repo, output)
        self.assertFalse((output / "upstream-source.tar.gz").exists())
        self.assertFalse((output / "upstream-download.terminal.json").exists())

    def test_missing_original_is_not_recovered_from_network(self):
        missing = self.dir / "missing"
        missing.mkdir()
        output = self.dir / "absent"
        output.mkdir()
        with mock.patch.object(custody.urllib.request, "build_opener",
                               side_effect=AssertionError("HTTP fallback forbidden")):
            with self.assertRaises((FileNotFoundError, custody.CustodyRejected)):
                custody.committed_upstream_original(missing, output)
        self.assertFalse((output / "upstream-source.tar.gz").exists())

    def test_reconstruction_reports_true_committed_origin(self):
        archive = REAL_ORIGINAL.read_bytes()
        with mock.patch.object(custody.urllib.request, "build_opener",
                               side_effect=AssertionError("no HTTP")), \
             mock.patch.object(custody.subprocess, "run",
                               side_effect=AssertionError("no Native or subprocess")):
            custody.reconstruct_sources(
                archive, REAL_ROOT, self.output, origin=custody.PINNED_UPSTREAM_ORIGIN)
        record = json.loads((self.output / "source-reconstruction.json").read_bytes())
        self.assertEqual(record["original_upstream_transport"],
                         custody.PINNED_UPSTREAM_ORIGIN)
        self.assertEqual(record["source_checkout_original"],
                         custody.PINNED_UPSTREAM_SOURCE)
        self.assertFalse(record["upstream_http_download_performed"])
        self.assertEqual(record["original_c_source"]["origin"],
                         custody.PINNED_UPSTREAM_ORIGIN)
        self.assertEqual(hashlib.sha256((self.output / "original_c_source").read_bytes()).hexdigest(),
                         custody.admission.ORIGINAL_SHA)
        self.assertEqual(hashlib.sha256((self.output / "repaired_c_source").read_bytes()).hexdigest(),
                         custody.admission.REPAIRED_SHA)

    def test_fixedtxt_compiler_intake_selects_one_offline_origin_only(self):
        compiled = b"\x7fELF exact compiled module"
        receipt = b'{"schema":"historical original compiler receipt"}'
        member_names = {"compiled_module": "only-libmltavformat.so",
                        "compiler_receipt_path": "only-compiler-receipt.json"}
        artifact_data = io.BytesIO()
        with zipfile.ZipFile(artifact_data, "w") as packed:
            packed.writestr(member_names["compiled_module"], compiled)
            packed.writestr(member_names["compiler_receipt_path"], receipt)
        selected = {
            "compiler": {
                "artifact": {"artifact_id": 1, "run_id": 2, "run_attempt": 1,
                             "source_sha": "a" * 40, "zip_sha256": "b" * 64},
                "members": member_names, "source_mode": custody.SOURCE_MODE
            }
        }
        with mock.patch.object(custody, "download_artifact",
                               return_value=artifact_data.getvalue()) as archive, \
             mock.patch.object(custody.g2, "REPAIRED_MODULE_SHA256",
                               hashlib.sha256(compiled).hexdigest()), \
             mock.patch.object(custody.g2, "COMPILER_RECEIPT_SHA256",
                               hashlib.sha256(receipt).hexdigest()), \
             mock.patch.object(custody, "committed_upstream_original",
                               return_value=b"exact upstream tar") as committed, \
             mock.patch.object(custody, "retrieve_upstream") as forbidden_http, \
             mock.patch.object(custody, "reconstruct_sources") as reconstruct, \
             mock.patch.object(custody.admission, "source_compiler_closure") as closure:
            custody.intake_compiler(selected, REAL_ROOT, self.output,
                                    scenario="fixedtxt-v4-one-fixture")
        archive.assert_called_once_with(selected["compiler"]["artifact"],
                                        "compiler", self.output)
        committed.assert_called_once_with(REAL_ROOT, self.output)
        forbidden_http.assert_not_called()
        reconstruct.assert_called_once_with(
            b"exact upstream tar", REAL_ROOT, self.output,
            origin=custody.PINNED_UPSTREAM_ORIGIN)
        closure.assert_called_once()
        self.assertEqual((self.output / "compiled_module").read_bytes(), compiled)
        self.assertEqual((self.output / "compiler_receipt_path").read_bytes(), receipt)

    def test_unknown_origin_is_not_mislabeled(self):
        with self.assertRaisesRegex(custody.CustodyRejected,
                                    "upstream_source_provenance"):
            custody.reconstruct_sources(
                b"not an original", REAL_ROOT, self.output, origin="made-up-review")


if __name__ == "__main__":
    unittest.main()
