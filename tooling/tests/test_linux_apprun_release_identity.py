"""Inert exact-origin policy for pinned upstream AppRun helper bytes."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_apprun_release_identity as pin


SOURCE = "1" * 40


def row(data):
    return {
        "path": "AppRun.wrapped", "kind": "file", "native_format": "ELF",
        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()
    }


class PinnedAppRunControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            dir=os.environ.get("TEMP"), prefix="mw-apprun-pinned-original-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(strict=True)
        self.raw = b"\x7fELF" + b"synthetic fixture, not the actual vendor binary\n" * 3
        self.file = self.root / "AppRun.wrapped"
        self.file.write_bytes(self.raw)
        self.app = {
            "context": {
                "source": SOURCE, "source_tree": "2" * 40,
                "run_id": "42", "run_attempt": 1,
                "kind": "appimage", "platform": "linux", "arch": "x86_64",
            },
            "custody": {"status": "DIRECT_CAPTURED_EXTRACTION"},
            "roots": [{"role": "payload", "members": [row(self.raw)]}],
            "gaps": [{"code": "MEMBER_ORIGIN_UNRESOLVED",
                      "members": ["payload:AppRun.wrapped"]}],
            "claims": {
                "formal_g2": "NOT_GRANTED", "formal_g3": "NOT_GRANTED",
                "redistribution_permission": "NOT_INFERRED",
            },
        }

    def observe(self):
        return pin.observe_original(
            self.app, self.root, trusted_bytes=len(self.raw),
            trusted_sha256=hashlib.sha256(self.raw).hexdigest())

    def test_stable_original_bytes_pass_with_scoped_synthetic_reference(self):
        observed = self.observe()
        self.assertEqual(observed["status"],
                         "IDENTICAL_TO_PINNED_RELEASE_ASSET_BYTES")
        self.assertEqual(observed["stable_live_read"],
                         "SAME_AS_ORIGINAL_CAPTURE")
        # The public report path deliberately uses definition-time pinned
        # defaults, so monkeypatching the constants cannot make a test
        # fixture pass as a genuine vendor executable.
        with mock.patch.object(pin, "PROVIDER_SHA256",
                               hashlib.sha256(self.raw).hexdigest()):
            with self.assertRaisesRegex(ValueError, "original_differs"):
                pin.report(self.app, self.root, SOURCE)

    def test_same_length_changed_file_does_not_promote(self):
        self.file.write_bytes(b"\x7fELF" + b"X" + self.raw[5:])
        with self.assertRaisesRegex(ValueError, "live_original_differs"):
            self.observe()

    def test_inventory_sha_changed_no_source_credit(self):
        self.app["roots"][0]["members"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "original_differs"):
            self.observe()

    def test_missing_root_member_refused(self):
        self.app["roots"][0]["members"] = []
        with self.assertRaisesRegex(ValueError, "member_roster"):
            self.observe()

    def test_duplicate_root_member_refused(self):
        self.app["roots"][0]["members"].append(row(self.raw))
        with self.assertRaisesRegex(ValueError, "member_roster"):
            self.observe()

    def test_non_elf_member_refused(self):
        self.app["roots"][0]["members"][0]["native_format"] = "NOT_IDENTIFIED"
        with self.assertRaisesRegex(ValueError, "original_differs"):
            self.observe()

    def test_original_gap_must_stay_unresolved_for_formal_acceptance(self):
        self.app["gaps"][0]["members"] = []
        with self.assertRaisesRegex(ValueError, "original_gap_changed"):
            self.observe()

    def test_wrong_custody_refused(self):
        self.app["custody"]["status"] = "HISTORICAL_COPY"
        with self.assertRaisesRegex(ValueError, "original_context"):
            self.observe()

    def test_provider_pin_is_not_overridable_by_cli_report(self):
        # The public 'report' path always uses the real upstream hardcoded pin.
        with self.assertRaisesRegex(ValueError, "original_differs"):
            pin.report(self.app, self.root, SOURCE)
        self.assertEqual(pin.PROVIDER_BYTES, 31552)
        self.assertEqual(pin.PROVIDER_SHA256,
                         "f30140a43a0a59e46db21bdefdf749b9e9f2c6946e92afabbacf98b8ae73fb4f")
        self.assertEqual(pin.PROVIDER_ASSET, 274691722)

    def test_symlinked_payload_cannot_satisfy_pin(self):
        actual = self.root / "real-original"
        self.file.rename(actual)
        try:
            self.file.symlink_to(actual)
        except OSError as exc:
            self.skipTest("symlink privilege unavailable: " + str(exc))
        with self.assertRaises((ValueError, pin.inventory.capture.CaptureError, OSError)):
            self.observe()

    def test_source_mismatch_and_fake_permissions_cannot_grant(self):
        self.app["claims"]["formal_g3"] = "PASS"
        with self.assertRaisesRegex(ValueError, "unearned_claim"):
            pin.report(self.app, self.root, SOURCE)
        self.app["claims"]["formal_g3"] = "NOT_GRANTED"
        with self.assertRaisesRegex(ValueError, "source_or_attempt"):
            pin.report(self.app, self.root, "3" * 40)

    def test_same_size_empty_non_elf_bytes_are_rejected(self):
        self.file.write_bytes(b"NOT!" + self.raw[4:])
        with self.assertRaisesRegex(ValueError, "live_original_differs"):
            self.observe()


if __name__ == "__main__":
    unittest.main()
