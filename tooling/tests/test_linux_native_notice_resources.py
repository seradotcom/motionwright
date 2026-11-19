"""Inert seven-notice source, manifest, byte and bundle-membership gates."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_native_notice_resources as notices

SOURCE = "1" * 40


class SevenPinnedNativeNoticeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            prefix="mw-seven-native-notices-", dir=os.environ.get("TEMP"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(strict=True)
        self.repo = self.root / "repo"
        (self.repo / notices.ROOT.parent).mkdir(parents=True)
        self.host = self.root / "system"
        self.info = self.host / "var/lib/dpkg/info"
        self.info.mkdir(parents=True)
        self.origins = []
        self.installed = {}
        self.pins = []
        for i, (pkg, _, _) in enumerate(notices.SPEC):
            data = (f"Original {pkg} notice -- no legal approval, specimen {i}\n").encode()
            name = notices.original_path(pkg)
            path = self.host / name.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            listed = ("/*\n" + name + "\n").encode()
            listed = ("/.\n" + name + "\n").encode()
            (self.info / (pkg + ":amd64.list")).write_bytes(listed)
            self.pins.append((pkg, len(data), hashlib.sha256(data).hexdigest()))
            self.installed[pkg + ":amd64"] = "1.0.0"
            self.origins.append((pkg, name, data))
        self.pins = tuple(self.pins)

    def stage(self, *, specs=None):
        return notices.stage(
            self.repo, SOURCE, system_root=self.host,
            info_root=self.info, installed=self.installed,
            spec=self.pins if specs is None else specs)

    def map(self):
        return notices.mapping(self.repo, SOURCE, spec=self.pins)

    def test_all_seven_exact_sources_and_installed_manifests_are_staged(self):
        original = self.stage()
        self.assertEqual(original["status"],
                         "SEVEN_PINNED_INSTALLED_PACKAGE_NOTICES_STAGED")
        self.assertEqual(original["claims"]["G3"], "STOP")
        self.assertEqual(len(original["notices"]), 7)
        mapping = self.map()
        self.assertEqual(len(mapping), 7)
        self.assertTrue(all(k.startswith(self.repo.as_posix()) for k in mapping))
        self.assertEqual(len(set(mapping.values())), 7)

    def test_different_bytes_same_size_are_refused(self):
        pkg, _, data = self.origins[2]
        target = self.host / notices.original_path(pkg).lstrip("/")
        target.write_bytes(b"X" + data[1:])
        with self.assertRaisesRegex(ValueError, "original_bytes_differ"):
            self.stage()

    def test_list_file_without_declared_copyright_cannot_supply_original(self):
        pkg, _, _ = self.origins[1]
        (self.info / (pkg + ":amd64.list")).write_bytes(
            b"/.\n/usr/share/doc/other-package/copyright\n")
        with self.assertRaisesRegex(ValueError, "not_in_original_package_manifest"):
            self.stage()

    def test_uninstalled_package_cannot_supply_license_identity(self):
        pkg, _, _ = self.origins[0]
        del self.installed[pkg + ":amd64"]
        with self.assertRaisesRegex(ValueError, "installed_package_missing"):
            self.stage()

    def test_staged_notice_content_mutation_refused_before_bundle(self):
        record = self.stage()
        pkg, _, _ = self.origins[0]
        path = self.repo / notices.ROOT / pkg / "copyright"
        data = path.read_bytes()
        path.write_bytes(b"X" + data[1:])
        with self.assertRaisesRegex((ValueError, notices.inventory.capture.CaptureError),
                                   "changed|unstable"):
            self.map()

    def test_original_manifest_mutation_refused_before_bundle(self):
        self.stage()
        pkg, _, _ = self.origins[0]
        path = self.repo / notices.ROOT / pkg / "dpkg-list.original"
        path.write_bytes(b"/usr/share/doc/other/copyright\n")
        with self.assertRaisesRegex(ValueError, "original_staged_bytes_changed"):
            self.map()

    def test_staging_replay_cannot_overwrite_original(self):
        original = self.stage()
        with self.assertRaisesRegex(ValueError, "staging_replay"):
            self.stage()
        self.assertEqual(self.map().__len__(), 7)

    def test_receipt_source_substitution_refused(self):
        self.stage()
        with self.assertRaisesRegex(ValueError, "staging_receipt_changed"):
            notices.mapping(self.repo, "2" * 40, spec=self.pins)

    def test_nonpackage_symlink_manifest_never_validates(self):
        if os.name == "nt":
            self.skipTest("Debian package colon filenames require POSIX paths")
        pkg, _, _ = self.origins[0]
        path = self.info / (pkg + ":amd64.list")
        actual = path.with_name("real-list.txt")
        path.rename(actual)
        try:
            path.symlink_to(actual)
        except OSError as exc:
            self.skipTest("host test symlink privilege unavailable: " + str(exc))
        with self.assertRaisesRegex(ValueError, "dpkg_list_nonregular"):
            self.stage()

    def test_originals_are_not_a_legal_grant(self):
        receipt = self.stage()
        self.assertEqual(receipt["claims"]["legal_review"], "NOT_PERFORMED")
        self.assertEqual(receipt["claims"]["G2"], "NOT_GRANTED")
        self.assertEqual(receipt["claims"]["G3"], "STOP")
        self.assertFalse(receipt["claims"]["release_authorized"])
        self.assertEqual([x["redistribution_license_review"] for x in
                          receipt["notices"]], ["NOT_PERFORMED"] * 7)

    def test_forged_pinned_target_refused(self):
        self.stage()
        path = self.repo / notices.RECEIPT
        receipt = json.loads(path.read_bytes())
        receipt["notices"][0]["destination"] = "licenses/other/forged"
        path.write_bytes(json.dumps(receipt).encode())
        with self.assertRaisesRegex(ValueError, "staged_mapping_changed"):
            self.map()

    def test_extracted_original_must_have_all_seven_identical(self):
        receipt = self.stage()
        inventory = {
            "context": {"kind": "appimage", "source": SOURCE},
            "custody": {"status": "DIRECT_CAPTURED_EXTRACTION"},
            "roots": [{"role": "payload", "members": [
                {"path": row["extracted_appimage_member"], "kind": "file",
                 **row["original"]} for row in receipt["notices"]]}],
        }
        with mock.patch.object(notices.inventory, "validate", return_value=None):
            result = notices.verify_extracted(inventory, receipt, spec=self.pins)
            self.assertEqual(result["count"], 7)
            self.assertEqual(result["status"],
                             "SEVEN_ORIGINAL_NOTICES_EXTRACTED_BYTES_VERIFIED")
            self.assertEqual(result["G3"], "STOP")
            inventory["roots"][0]["members"] = inventory["roots"][0]["members"][:-1]
            with self.assertRaisesRegex(ValueError, "member_not_original"):
                notices.verify_extracted(inventory, receipt, spec=self.pins)

    def sample_inventory(self, kind, receipt):
        return {
            "context": {"kind": kind, "source": SOURCE},
            "custody": {"status": "DIRECT_CAPTURED_EXTRACTION"},
            "roots": [{"role": "payload", "members": [
                {"path": row["extracted_appimage_member"], "kind": "file",
                 **row["original"]} for row in receipt["notices"]]}],
        }

    def test_deb_and_appimage_have_identical_seven_original_notice_members(self):
        receipt = self.stage()
        with mock.patch.object(notices.inventory, "validate", return_value=None):
            app = notices.verify_extracted(
                self.sample_inventory("appimage", receipt),
                receipt, kind="appimage", spec=self.pins)
            deb = notices.verify_extracted(
                self.sample_inventory("deb", receipt),
                receipt, kind="deb", spec=self.pins)
        self.assertEqual(app["notices"], deb["notices"])
        self.assertEqual({app["kind"], deb["kind"]}, {"appimage", "deb"})
        self.assertEqual((app["G3"], deb["G3"]), ("STOP", "STOP"))

    def test_deb_member_with_same_length_wrong_sha_is_refused(self):
        receipt = self.stage()
        original = self.sample_inventory("deb", receipt)
        original["roots"][0]["members"][0]["sha256"] = "f" * 64
        with mock.patch.object(notices.inventory, "validate", return_value=None):
            with self.assertRaisesRegex(ValueError, "member_not_original"):
                notices.verify_extracted(original, receipt, kind="deb", spec=self.pins)

    def test_deb_source_or_kind_substitution_refused_without_credit(self):
        receipt = self.stage()
        original = self.sample_inventory("deb", receipt)
        with mock.patch.object(notices.inventory, "validate", return_value=None):
            with self.assertRaisesRegex(ValueError, "original_context"):
                notices.verify_extracted(original, receipt, kind="appimage", spec=self.pins)
            original["context"]["source"] = "2" * 40
            with self.assertRaisesRegex(ValueError, "original_context"):
                notices.verify_extracted(original, receipt, kind="deb", spec=self.pins)
            original["context"]["source"] = SOURCE
            original["custody"]["status"] = "HISTORICAL_COPY"
            with self.assertRaisesRegex(ValueError, "original_context"):
                notices.verify_extracted(original, receipt, kind="deb", spec=self.pins)


if __name__ == "__main__":
    unittest.main()
