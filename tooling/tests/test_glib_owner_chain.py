"""Adversarial v0.1 RustSec glib owner-chain review: no warning is waived."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
from glib_owner_chain import OwnerChainError, verify_glib_owner_chain, lock_reverse_edges  # noqa: E402


class GlibOwnerChainTests(unittest.TestCase):
    def setUp(self):
        self.lock = tomllib.loads((ROOT / "Cargo.lock").read_text(encoding="utf-8"))
        self.policy = json.loads(
            (ROOT / "docs/security/known-rustsec-advisories.json").read_text()
        )["advisories"][0]
        self.assertEqual(self.policy["id"], "RUSTSEC-2024-0429")

    def package(self, name: str):
        matches = [pkg for pkg in self.lock["package"] if pkg["name"] == name]
        self.assertEqual(len(matches), 1, name)
        return matches[0]

    def assert_rejected(self, expected):
        with self.assertRaisesRegex(OwnerChainError, expected):
            verify_glib_owner_chain(self.lock, self.policy)

    def test_actual_locked_gtk3_tauri_owner_chain_remains_explicitly_open(self):
        result = verify_glib_owner_chain(self.lock, self.policy)
        self.assertEqual(result["schema"], "motionwright.v01-glib-owner-chain/1")
        self.assertEqual(result["advisory_status"], "OPEN_UPSTREAM_BLOCKED")
        self.assertEqual(result["locked_version"], "0.18.5")
        self.assertEqual(len(result["reviewed_direct_owners"]), 12)
        self.assertEqual(len(result["reviewed_transitive_owners"]), 22)
        self.assertEqual(result["observed_first_party_owners"], ["motionwright-desktop"])
        self.assertEqual(result["terminal_source_owner"], "motionwright-desktop@0.1.0")
        self.assertFalse(result["glib_vulnerability_fixed"])
        self.assertFalse(result["release_authorized"])
        forbidden = self.policy["forbidden_first_party_tokens"]
        self.assertIn("VariantStrIter", forbidden)
        self.assertIn("array_iter_str", forbidden)
        self.assertFalse(result["linux_target_reachability_proven"])
        self.assertFalse(result["affected_iterator_api_unreachable_proven"])

    def test_new_direct_rust_glib_client_is_refused(self):
        self.lock["package"].append({
            "name": "unknown-glib-consumer", "version": "0.1.0",
            "source": "registry+https://github.com/rust-lang/crates.io-index",
            "checksum": "a" * 64, "dependencies": ["glib"],
        })
        self.assert_rejected("glib_direct_owner_chain_drifted")

    def test_first_party_core_cannot_add_transitive_glib(self):
        self.package("motionwright-native")["dependencies"].append("glib")
        self.assert_rejected("glib_direct_owner_chain_drifted")

    def test_new_indirect_client_cannot_hide_behind_reviewed_gtk(self):
        self.lock["package"].append({
            "name": "unreviewed-gtk-client", "version": "0.1.0",
            "source": "registry+https://github.com/rust-lang/crates.io-index",
            "checksum": "a" * 64, "dependencies": ["gtk"],
        })
        self.assert_rejected("glib_indirect_owner_chain_drifted")

    def test_first_party_service_cannot_depend_on_existing_reviewed_gtk_owner(self):
        self.package("motionwright-service")["dependencies"].append("gtk")
        self.assert_rejected("glib_indirect_owner_chain_drifted")

    def test_known_owner_replaced_by_git_source_is_rejected(self):
        pkg = self.package("wry")
        pkg["source"] = "git+https://attacker.example/wry?rev=deadbeef"
        self.assert_rejected("glib_ancestor_source_changed")

    def test_known_owner_missing_checksum_is_rejected(self):
        self.package("gtk").pop("checksum", None)
        self.assert_rejected("glib_ancestor_checksum_missing")

    def test_glib_source_substitution_is_rejected(self):
        self.package("glib")["source"] = "git+https://attacker.example/glib"
        self.assert_rejected("glib_ancestor_source_changed")

    def test_missing_original_direct_glib_owner_is_not_silently_accepted(self):
        owner = self.package("gtk")
        owner["dependencies"] = [dep for dep in owner["dependencies"]
                                 if dep.split()[0] != "glib"]
        self.assert_rejected("glib_direct_owner_chain_drifted")

    def test_indirect_tauri_version_drift_refused(self):
        self.package("tauri")["version"] = "2.999.0"
        self.assert_rejected("glib_indirect_owner_chain_drifted")

    def test_missing_direct_owner_review_never_passes(self):
        del self.policy["reviewed_direct_owners"]
        self.assert_rejected("glib_reviewed_owners_invalid")

    def test_missing_indirect_owner_review_never_passes(self):
        del self.policy["reviewed_transitive_owners"]
        self.assert_rejected("glib_reviewed_transitive_owner_policy_invalid")

    def test_policy_cannot_claim_upstream_fixed(self):
        self.policy["status"] = "FIXED"
        self.assert_rejected("glib_advisory_policy_not_open")

    def test_duplicate_lock_identity_is_rejected(self):
        self.lock["package"].append(copy.deepcopy(self.package("glib")))
        self.assert_rejected("duplicated_lock_package_identity")

    def test_forged_missing_or_ambiguous_dependency_is_rejected(self):
        self.package("gtk")["dependencies"].append("missing-gtk-helper")
        self.assert_rejected("missing_lock_dependency_package")
        self.package("gtk")["dependencies"].pop()
        self.package("gtk")["dependencies"].append("glib 0.20.0")
        self.assert_rejected("ambiguous_or_mismatched_lock_dependency_version")

    def test_output_never_includes_paths_tokens_or_release_approval(self):
        data = verify_glib_owner_chain(self.lock, self.policy)
        output = json.dumps(data)
        self.assertNotIn("session_file", output)
        self.assertNotIn("NativeJob", output)
        self.assertNotIn("/home/", output)
        self.assertNotIn("PRIVATE_OWNER", output)
        self.assertIn("OPEN_UPSTREAM_BLOCKED", output)


if __name__ == "__main__":
    unittest.main()
