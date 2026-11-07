"""Offline RED/GREEN guards against partial Semwright Cargo/source pin promotion.

Inert fixture mutations prove a mixed pin cannot be accepted. The live case
inspects only existing retained metadata; no Git fetch, Cargo resolution,
Semwright binary, Native G2, or Graph6 runtime is executed.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import semwright_pin_atomicity as pins

OLD = "a" * 40
NEW = "b" * 40


def material():
    workspace = {"workspace": {
        "members": ["crates/driver"],
        "dependencies": {
            "semwright-native-sdk": {
                "git": pins.REPO, "rev": OLD, "package": "semwright-native-sdk",
            }
        },
    }}
    driver = {"dependencies": {
        "semwright-driver-sdk": {
            "git": pins.REPO, "rev": OLD, "package": "semwright-driver-sdk",
        }
    }}
    lock = {"package": [
        {"name": "semwright-native-sdk", "version": "1.0.0",
         "source": pins.LOCK_PREFIX + OLD + "#" + OLD},
        {"name": "semwright-driver-sdk", "version": "1.0.0",
         "source": pins.LOCK_PREFIX + OLD + "#" + OLD},
    ]}
    return {"Cargo.toml": workspace, "crates/driver/Cargo.toml": driver}, lock


class AtomicPinSourceTests(unittest.TestCase):
    def setUp(self):
        self.manifests, self.lock = material()

    def reject(self, code):
        with self.assertRaisesRegex(pins.PinConsistencyError, code):
            pins.check_material(OLD, self.manifests, self.lock)

    def test_full_atomic_manifest_and_lock_roster_is_valid(self):
        result = pins.check_material(OLD, self.manifests, self.lock)
        self.assertEqual(result["source_pin"], OLD)
        self.assertEqual(result["workspace_manifest_count"], 2)
        self.assertEqual(result["direct_git_dependency_count"], 2)
        self.assertEqual(result["locked_upstream_package_count"], 2)
        self.assertEqual(result["status"], "CONSISTENT_ONLY_NOT_RELEASE_APPROVED")

    def test_workspace_pin_advanced_without_nested_dependency_is_rejected(self):
        self.manifests["Cargo.toml"]["workspace"]["dependencies"][
            "semwright-native-sdk"]["rev"] = NEW
        self.reject("cargo_git_revision")

    def test_nested_driver_pin_advanced_without_source_lock_is_rejected(self):
        self.manifests["crates/driver/Cargo.toml"]["dependencies"][
            "semwright-driver-sdk"]["rev"] = NEW
        self.reject("cargo_git_revision")

    def test_cargo_lock_stale_source_fails_even_with_consistent_manifests(self):
        self.lock["package"][0]["source"] = pins.LOCK_PREFIX + NEW + "#" + NEW
        self.reject("cargo_lock_revision")

    def test_cargo_lock_changed_git_fragment_without_rev_is_rejected(self):
        self.lock["package"][1]["source"] = pins.LOCK_PREFIX + OLD + "#" + NEW
        self.reject("cargo_lock_revision")

    def test_registry_or_unlocked_semwright_package_is_rejected(self):
        self.lock["package"][0].pop("source")
        self.reject("cargo_lock_revision")

    def test_mixed_origin_or_branch_dependency_is_rejected(self):
        self.manifests["crates/driver/Cargo.toml"]["dependencies"][
            "semwright-driver-sdk"]["git"] = "https://github.com/attacker/semwright"
        self.reject("cargo_git_origin")
        self.manifests, self.lock = material()
        self.manifests["Cargo.toml"]["workspace"]["dependencies"][
            "semwright-native-sdk"]["branch"] = "main"
        self.reject("cargo_git_selector")

    def test_registry_dependency_cannot_replace_a_pinned_git_dependency(self):
        # A string-valued Cargo dependency is not a table and used to
        # evade the original Git-only recursive walker entirely.
        self.manifests["crates/driver/Cargo.toml"]["dependencies"][
            "semwright-driver-sdk"] = "1.0.0"
        self.reject("cargo_direct_source")

    def test_registry_alias_cannot_replace_a_pinned_git_dependency(self):
        self.manifests["crates/driver/Cargo.toml"]["dependencies"][
            "driver_alias"] = {"package": "semwright-driver-sdk", "version": "1.0"}
        del self.manifests["crates/driver/Cargo.toml"]["dependencies"]["semwright-driver-sdk"]
        self.reject("cargo_direct_source")

    def test_pinned_cargo_dependency_alias_resolves_exact_package(self):
        entry = self.manifests["crates/driver/Cargo.toml"]["dependencies"].pop(
            "semwright-driver-sdk")
        self.manifests["crates/driver/Cargo.toml"]["dependencies"]["driver_alias"] = entry
        result = pins.check_material(OLD, self.manifests, self.lock)
        self.assertEqual(result["direct_git_dependency_count"], 2)

    def test_direct_crate_missing_from_lock_is_rejected(self):
        self.lock["package"].pop()
        self.reject("direct_dependency_not_locked")

    def test_duplicate_git_package_record_is_rejected(self):
        self.lock["package"].append(copy.deepcopy(self.lock["package"][0]))
        self.reject("resolved_package_duplicate")

    def test_unsafe_or_missing_workspace_members_are_rejected(self):
        self.manifests["Cargo.toml"]["workspace"]["members"] = ["../foreign"]
        self.reject("workspace_member_path")
        self.manifests, self.lock = material()
        self.manifests.pop("crates/driver/Cargo.toml")
        self.reject("workspace_roster_mismatch")

    def test_live_repository_retained_source_lock_is_coherent_without_writes(self):
        current = pins.audit(Path(__file__).resolve().parents[2])
        self.assertGreaterEqual(current["direct_git_dependency_count"], 2)
        self.assertGreaterEqual(current["locked_upstream_package_count"], 2)
        self.assertEqual(current["retained_source_evidence"], "VERIFIED_OFFLINE")
        self.assertFalse(current["promotion_authorized"])


class CandidateRetentionTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="mw-a2-upstream-source-pin-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        current = {"dependencies": {"semwright": {
            "revision": OLD,
            "verification": {
                "evidence": {
                    "path": "evidence.json",
                    "sha256": "0" * 64,
                }
            }
        }}}
        self.current = current
        paths = ["Cargo.toml", "LICENSE-MIT", "LICENSE-APACHE"]
        self.original = {
            "Cargo.toml": b"[workspace.package]\nversion=\"1.0.0\"\n",
            "LICENSE-MIT": b"synthetic MIT license bytes",
            "LICENSE-APACHE": b"synthetic Apache license bytes",
        }
        records = []
        for i, path in enumerate(paths):
            retained = self.root / ("retained-" + str(i))
            data = self.original[path]
            retained.write_bytes(data)
            records.append({
                "upstream_path": path,
                "path": retained.name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "git_blob_sha1": hashlib.sha1(
                    b"blob " + str(len(data)).encode() + b"\0" + data,
                    usedforsecurity=False,
                ).hexdigest(),
            })
        (self.root / "evidence.json").write_text(
            json.dumps({"schema": "motionwright.source-evidence/1",
                        "repository": pins.REPO, "revision": OLD,
                        "version": "1.0.0", "license_expression": "MIT OR Apache-2.0",
                        "files": records}),
            encoding="utf-8")
        self.before = {
            path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()
        }
        self.checks = [b"commit", *[self.original[p] for p in paths]]

    def run_candidate(self, checks):
        with patch.object(pins, "load_source_lock", return_value=self.current), \
             patch.object(pins, "verify_source_lock", return_value=None), \
             patch.object(pins.subprocess, "check_output", side_effect=checks):
            return pins.candidate_evidence(self.root, self.root, NEW)

    def test_unchanged_upstream_blobs_reuse_original_retained_bytes_without_writes(self):
        result = self.run_candidate(self.checks)
        self.assertEqual(result["candidate_sha"], NEW)
        self.assertFalse(result["copy_source_metadata_required"])
        self.assertTrue(result["cargo_resolution_regeneration_required"])
        self.assertFalse(result["source_pin_promotion_authorized"])
        self.assertEqual(
            result["future_evidence_path"],
            "third_party/semwright-source-evidence/" + NEW + "/evidence.json",
        )
        after = {
            path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()
        }
        self.assertEqual(self.before, after)

    def test_upstream_metadata_changed_requires_new_reviewed_retention(self):
        changed = [b"commit", b"unreviewed upstream Cargo metadata",
                   self.original["LICENSE-MIT"], self.original["LICENSE-APACHE"]]
        with self.assertRaisesRegex(pins.PinConsistencyError,
                                    "candidate_new_metadata_requires_review"):
            self.run_candidate(changed)
        self.assertEqual(
            self.before,
            {path.name: path.read_bytes() for path in self.root.iterdir()
             if path.is_file()},
        )

    def test_branch_reference_does_not_masquerade_as_commit(self):
        with self.assertRaisesRegex(pins.PinConsistencyError, "candidate_commit_invalid"):
            pins.candidate_evidence(self.root, self.root, "main")
        with self.assertRaisesRegex(pins.PinConsistencyError, "candidate_not_commit"):
            self.run_candidate([b"tag"])


if __name__ == "__main__":
    unittest.main()
