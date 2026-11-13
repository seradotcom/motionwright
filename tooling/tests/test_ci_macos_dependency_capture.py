"""Inert DMG records and real local Git/filesystem checks; no macOS execution."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_macos_dependency_capture as gate
import test_candidate_cargo_producer as fixtures
from test_candidate_receipt_source import git


class MacOSDependencyCaptureTests(unittest.TestCase):
    def setUp(self):
        fixtures.CandidateCargoProducerTests.setUp(self)
        config = self.root / "apps/desktop/src-tauri/tauri.conf.json"
        config.write_bytes((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_bytes())
        from candidate_smoke import NOTICE_FILES
        for original, _ in NOTICE_FILES:
            path = self.root / original
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / original).read_bytes())
        git(self.root, "add", "apps/desktop/src-tauri/tauri.conf.json",
            *(original for original, _ in NOTICE_FILES))
        git(self.root, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "macOS resource fixture")
        self.sha = git(self.root, "rev-parse", "HEAD")
        fixtures.CandidateCargoProducerTests.cargo_fixture(self, gate.TARGET)
        binary = self.root / gate.BINARY
        binary.parent.mkdir(parents=True)
        self.cargo_binary.replace(binary); self.cargo_binary = binary
        notice = b"Inert notice fixture; not a production license.\n"
        digest = gate.capture.sha(notice)
        self.cargo_index_data["components"][0]["notices"] = [{"sha256": digest}]
        self.cargo_index = self.root / gate.BASE / "resources/cargo/index.json"
        self.cargo_index.parent.mkdir(parents=True)
        self.notice = self.cargo_index.parent / "texts" / (digest + ".txt")
        self.notice.parent.mkdir(); self.notice.write_bytes(notice)
        custody = gate.capture.encoded({
            "profile": {"source": self.sha, "target": gate.TARGET, "schema": gate.PROFILE},
            "fixture_only": "No compiler or generator executed"})
        self.custody = self.root / gate.BASE / "state/tauri-generated-schemas.json"
        gate.capture.write_new(self.custody, custody)
        self.cargo_index_data["capture"] = {"generated_metadata_sha256": gate.capture.sha(custody)}
        self.save_cargo_index()
        self.complete = {
            "source": self.sha, "cargo_index_sha256": gate.capture.sha(self.cargo_index.read_bytes()),
            "artifact_identities_sha256": self.cargo_index_data["artifact_identities_sha256"],
            "full_package_inventory": "NOT_VERIFIED", "generated_metadata_sha256": gate.capture.sha(custody)}
        gate.capture.write_new(self.root / gate.BASE / "state/capture-complete.json",
                               gate.capture.encoded(self.complete))
        self.output = self.root / "ci-evidence/macos-cargo-binding.json"

    save_cargo_index = fixtures.CandidateCargoProducerTests.save_cargo_index

    def package_fixture(self):
        from candidate_smoke import _verify_cargo_notice_subtree
        from package_receipt import make_receipt

        gate.stage_resources(self.root, self.sha)
        self.dmg = self.root / gate.BINARY.parent / "bundle/dmg/Motionwright.dmg"
        self.dmg.parent.mkdir(parents=True)
        self.dmg.write_bytes(b"INERT DMG FIXTURE; no mount or application")
        self.candidate = make_receipt(
            self.root, self.sha, "macos", "arm64", [self.dmg],
            cargo_index=self.cargo_index, cargo_binary=self.cargo_binary, require_cargo_binding=True)
        gate.capture.write_new(self.root / "ci-evidence/macos-candidate.json",
                               gate.capture.encoded(self.candidate))
        _, _, _, observed = gate.cargo_binding(self.root, self.sha)
        notices = _verify_cargo_notice_subtree(
            capture=gate.capture, index_raw=self.cargo_index.read_bytes(),
            anchor=self.root / gate.BASE / "resources", relative=Path("cargo"))
        row = self.candidate["artifacts"][0]
        self.smoke = {
            "schema_version": 1, "candidate": self.candidate["candidate"], "bundle_kind": "dmg",
            "candidate_archive": {key: row[key] for key in ("filename", "bytes", "sha256")},
            "automated_inspection": {"status": "PASS", "method": "dmg_readonly_mount_copy",
                "binary": {"binary_bytes": observed["bytes"], "binary_sha256": observed["sha256"]},
                "cargo_build_binding": {**observed, "notice_resources": notices}},
            "claims": {"human_install_acceptance": "NOT_RUN",
                       "runtime_renderer_acceptance": "SEPARATE_GATE", "signing": "NOT_CLAIMED",
                       "notarization": "NOT_CLAIMED", "release_published": False}}
        self.smoke_path = self.root / "ci-evidence/macos-dmg-smoke.json"
        gate.capture.write_new(self.smoke_path, gate.capture.encoded(self.smoke))
        self.frontend = {
            "source_sha": self.sha, "source_tree": observed["source_tree"],
            "observed_npm_source_and_notice_authentication": "VERIFIED",
            "source_binding": "EXACT_HEAD_TREE_INDEX_AND_TRACKED_BYTES_REVALIDATED",
            "frontend_index_sha256": "4" * 64}
        for phase in ("before-build", "after-bundle"):
            gate.capture.write_new(self.root / gate.BASE / "frontend" / (phase + ".json"),
                                   gate.capture.encoded(self.frontend))

    def test_mac_layout_and_report_preserve_windows_defaults_and_acceptance_limits(self):
        defaults = (gate.shared.BASE, gate.shared.TARGET, gate.shared.BINARY)
        self.package_fixture()
        result = gate.verify_dmg(self.root, self.sha)
        self.assertEqual(defaults, (gate.shared.BASE, gate.shared.TARGET, gate.shared.BINARY))
        self.assertEqual(result["cargo_binding"], "CAPTURED_ROOT_BINARY_BYTE_EXACT")
        self.assertTrue(result["cargo_notices"]["subtree_closed"])
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        self.assertEqual(result["notarization"], "NOT_CLAIMED")
        self.assertEqual(result["human_install_acceptance"], "NOT_RUN")
        self.assertFalse(result["published"])
        original = self.output.read_bytes()
        with self.assertRaises(FileExistsError):
            gate.verify_dmg(self.root, self.sha)
        self.assertEqual(self.output.read_bytes(), original)

    def test_dmg_cannot_substitute_legacy_wrong_binary_or_release_claim(self):
        self.package_fixture()
        for field, value in (("status", "NOT_VERIFIED"), ("sha256", "0" * 64),
                             ("source_tree", "1" * 40), ("target", "x86_64-apple-darwin")):
            changed = copy.deepcopy(self.smoke)
            changed["automated_inspection"]["cargo_build_binding"][field] = value
            self.smoke_path.write_bytes(gate.capture.encoded(changed))
            with self.subTest(field=field), self.assertRaisesRegex(
                    ValueError, "macos_package_cargo_binding_mismatch"):
                gate.verify_dmg(self.root, self.sha)
            self.assertFalse(self.output.exists())
        changed = copy.deepcopy(self.smoke); changed["claims"]["release_published"] = 0
        self.smoke_path.write_bytes(gate.capture.encoded(changed))
        with self.assertRaisesRegex(ValueError, "macos_package_claims_changed"):
            gate.verify_dmg(self.root, self.sha)

    def test_mutated_archive_frontend_or_notice_set_prevents_report(self):
        self.package_fixture()
        original = self.dmg.read_bytes(); self.dmg.write_bytes(original + b"x")
        with self.assertRaisesRegex(ValueError, "macos_candidate_changed"):
            gate.verify_dmg(self.root, self.sha)
        self.dmg.write_bytes(original)
        after = self.root / gate.BASE / "frontend/after-bundle.json"
        after.write_bytes(gate.capture.encoded({**self.frontend, "frontend_index_sha256": "5" * 64}))
        with self.assertRaisesRegex(ValueError, "macos_frontend_authentication_required"):
            gate.verify_dmg(self.root, self.sha)
        after.write_bytes(gate.capture.encoded(self.frontend))
        (self.notice.parent / "unlisted.txt").write_bytes(b"extra")
        with self.assertRaisesRegex(ValueError, "candidate_cargo_"):
            gate.verify_dmg(self.root, self.sha)
        self.assertFalse(self.output.exists())

    def test_changed_overlay_refuses_before_starting_any_bundle_process(self):
        gate.stage_resources(self.root, self.sha)
        path = self.root / gate.BASE / "tauri-resources.json"
        value = json.loads(path.read_bytes()); value["bundle"]["resources"]["foreign"] = "foreign"
        path.write_bytes(gate.capture.encoded(value))
        with mock.patch.object(gate.capture, "run_captured") as process:
            with self.assertRaisesRegex(ValueError, "macos_cargo_overlay_changed"):
                gate.bundle_dmg(self.root, self.sha, {})
            process.assert_not_called()


class MacOSScopeTests(unittest.TestCase):
    def test_only_exact_arm64_development_scope_is_admitted_before_effects(self):
        env = {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "seradotcom/motionwright",
            "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
            "MW_MACOS_CANDIDATE_SCOPE": "macos_bundle", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "1" * 40, "GITHUB_RUN_ID": "123",
            "RUNNER_OS": "macOS", "RUNNER_ARCH": "ARM64"}
        self.assertEqual(gate.macos_source(env), "1" * 40)
        for key, value in (("GITHUB_EVENT_NAME", "push"), ("GITHUB_REPOSITORY", "foreign/repo"),
                           ("GITHUB_REF", "refs/heads/main"), ("GITHUB_RUN_ATTEMPT", "2"),
                           ("MW_MACOS_CANDIDATE_SCOPE", "all"), ("RUNNER_OS", "Linux"),
                           ("RUNNER_ARCH", "X64"), ("GITHUB_SHA", "1" * 8), ("GITHUB_RUN_ID", "0")):
            with self.subTest(key=key), mock.patch.dict(os.environ, {**env, key: value}, clear=True), \
                 mock.patch.object(gate.shared, "authenticate_frontend") as frontend, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(gate.main(["frontend"]), 2)
                frontend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
