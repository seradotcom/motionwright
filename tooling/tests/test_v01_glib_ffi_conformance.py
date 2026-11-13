"""Adversarial lock/source/output boundaries for the real GLib FFI workflow."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))

from v01_glib_ffi_conformance import (  # noqa: E402
    FfiEvidenceError, PROBE, PROBE_NAME, REGISTRY, TESTS,
    pairs, verify_resolution, verify_test_output,
)


class GlibFfiConformanceTests(unittest.TestCase):
    def setUp(self):
        self.product = {"package": [
            {"name": "glib", "version": "0.18.5", "dependencies": ["libc"]},
            {"name": "libc", "version": "0.2.186", "source": REGISTRY,
             "checksum": "a" * 64},
        ]}
        self.fixture = copy.deepcopy(self.product)
        self.fixture["package"].append({
            "name": PROBE_NAME, "version": "0.0.0", "dependencies": ["glib"],
        })
        self.metadata = {
            "workspace_members": ["probe"],
            "packages": [
                {"id": "glib", "name": "glib", "version": "0.18.5", "source": None,
                 "manifest_path": str(ROOT / "vendor/glib-0.18.5/Cargo.toml")},
                {"id": "libc", "name": "libc", "version": "0.2.186", "source": REGISTRY,
                 "manifest_path": "/unused-registry/libc/Cargo.toml"},
                {"id": "probe", "name": PROBE_NAME, "version": "0.0.0", "source": None,
                 "manifest_path": str(ROOT / PROBE / "Cargo.toml")},
            ],
            "resolve": {"root": "probe", "nodes": [
                {"id": "probe", "dependencies": ["glib"]},
                {"id": "glib", "dependencies": ["libc"]},
                {"id": "libc", "dependencies": []},
            ]},
        }

    def check(self):
        return verify_resolution(ROOT, self.product, self.fixture, self.metadata)

    def test_current_product_identities_and_closed_fixture_are_bound(self):
        result = self.check()
        self.assertEqual(result["selected_packages"], 3)
        self.assertTrue(result["product_lock_package_identity_and_checksums_exact"])
        self.assertEqual(result["glib_package_id"], "glib")

    def test_same_version_changed_checksum_is_refused(self):
        self.fixture["package"][1]["checksum"] = "b" * 64
        with self.assertRaisesRegex(FfiEvidenceError, "fixture_checksum_drift"):
            self.check()

    def test_resolved_upgrade_outside_product_lock_is_refused(self):
        self.fixture["package"][1]["version"] = "0.2.187"
        with self.assertRaises(FfiEvidenceError):
            self.check()

    def test_other_local_glib_copy_is_refused(self):
        self.metadata["packages"][0]["manifest_path"] = str(ROOT / "other/glib/Cargo.toml")
        with self.assertRaisesRegex(FfiEvidenceError, "local_manifest_path_changed"):
            self.check()

    def test_metadata_registry_substitution_is_refused(self):
        self.metadata["packages"][1]["source"] = "git+https://example.invalid/libc"
        with self.assertRaisesRegex(FfiEvidenceError, "metadata_package_not_locked"):
            self.check()

    def test_unlocked_edge_and_unreachable_package_are_refused(self):
        self.metadata["resolve"]["nodes"][0]["dependencies"].append("libc")
        with self.assertRaisesRegex(FfiEvidenceError, "metadata_dependency_not_locked"):
            self.check()
        self.metadata["resolve"]["nodes"][0]["dependencies"] = ["glib"]
        self.metadata["resolve"]["nodes"][1]["dependencies"] = []
        with self.assertRaisesRegex(FfiEvidenceError, "metadata_unreachable_package"):
            self.check()

    def test_duplicate_lock_or_metadata_identity_is_refused(self):
        self.fixture["package"].append(copy.deepcopy(self.fixture["package"][1]))
        with self.assertRaisesRegex(FfiEvidenceError, "lock_identity_duplicated"):
            self.check()
        self.fixture["package"].pop()
        self.metadata["packages"].append(copy.deepcopy(self.metadata["packages"][0]))
        with self.assertRaisesRegex(FfiEvidenceError, "metadata_id_duplicated_or_missing"):
            self.check()


    def test_cross_platform_entries_in_lock_may_be_absent_from_metadata(self):
        # An original filtered Linux resolution has 48 observed packages and
        # 52 lock entries. Unused platform variants must remain source-locked.
        extra = {"name": "winapi", "version": "0.3.9", "source": REGISTRY,
                 "checksum": "b" * 64}
        self.product["package"].append(copy.deepcopy(extra))
        self.fixture["package"].append(copy.deepcopy(extra))
        self.assertEqual(self.check()["selected_packages"], 3)
        self.fixture["package"][-1]["checksum"] = "c" * 64
        with self.assertRaisesRegex(FfiEvidenceError, "fixture_checksum_drift"):
            self.check()

    def test_filtered_metadata_does_not_hide_unreviewed_lock_injection(self):
        self.fixture["package"].append({
            "name": "unreviewed-extra", "version": "0.1.0",
            "source": REGISTRY, "checksum": "f" * 64,
        })
        with self.assertRaisesRegex(FfiEvidenceError, "fixture_package_not_in_product_lock"):
            self.check()

    def test_duplicate_original_json_field_is_refused(self):
        with self.assertRaisesRegex(FfiEvidenceError, "duplicate_json_field"):
            json.loads('{"resolve": {}, "resolve": {}}', object_pairs_hook=pairs)

    def test_six_named_original_tests_required_without_skip_or_repeat(self):
        log = "\n".join("test tests::" + name + " ... ok" for name in TESTS)
        summary = "\ntest result: ok. 6 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n"
        self.assertEqual(verify_test_output((log + summary).encode())["tests_passed"], 6)
        for changed in (
            log.replace(" ... ok", " ... ignored", 1) + summary,
            log + summary.replace("0 ignored", "1 ignored"),
            log + summary + summary,
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(FfiEvidenceError):
                    verify_test_output(changed.encode())


if __name__ == "__main__":
    unittest.main()
