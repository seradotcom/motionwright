"""Real Git/filesystem contract tests with explicitly inert package records.

No Cargo compiler, patchelf, package installer, application or Native runs here.
"""
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
import ci_linux_dependency_capture as gate
import test_candidate_cargo_producer as fixtures
from test_candidate_receipt_source import git


class LinuxDependencyCaptureTests(unittest.TestCase):
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
            "commit", "-qm", "Linux resource fixture")
        self.sha = git(self.root, "rev-parse", "HEAD")
        fixtures.CandidateCargoProducerTests.cargo_fixture(self, gate.TARGET)
        binary = self.root / gate.BINARY
        binary.parent.mkdir(parents=True)
        self.cargo_binary.replace(binary)
        self.cargo_binary = binary
        notice = b"Inert notice fixture; not a production license.\n"
        digest = gate.capture.sha(notice)
        self.cargo_index_data["components"][0]["notices"] = [{"sha256": digest}]
        self.cargo_index = self.root / gate.BASE / "resources/cargo/index.json"
        self.cargo_index.parent.mkdir(parents=True)
        self.notice_path = self.cargo_index.parent / "texts" / (digest + ".txt")
        self.notice_path.parent.mkdir()
        self.notice_path.write_bytes(notice)
        self.custody = {
            "profile": {"source": self.sha, "target": gate.TARGET, "schema": gate.PROFILE},
            "fixture_only": "No compiler or generator executed",
        }
        self.custody_path = self.root / gate.BASE / "state/tauri-generated-schemas.json"
        gate.capture.write_new(self.custody_path, gate.capture.encoded(self.custody))
        digest = gate.capture.sha(self.custody_path.read_bytes())
        self.cargo_index_data["capture"] = {"generated_metadata_sha256": digest}
        self.save_cargo_index()
        self.complete = {
            "source": self.sha, "cargo_index_sha256": gate.capture.sha(self.cargo_index.read_bytes()),
            "artifact_identities_sha256": self.cargo_index_data["artifact_identities_sha256"],
            "full_package_inventory": "NOT_VERIFIED", "generated_metadata_sha256": digest,
        }
        self.complete_path = self.root / gate.BASE / "state/capture-complete.json"
        gate.capture.write_new(self.complete_path, gate.capture.encoded(self.complete))
        self.output = self.root / "ci-evidence/linux-cargo-binding.json"

    save_cargo_index = fixtures.CandidateCargoProducerTests.save_cargo_index

    def package_fixture(self):
        from candidate_smoke import _verify_cargo_notice_subtree
        from package_receipt import make_receipt

        gate.stage_resources(self.root, self.sha)
        paths = []
        for kind, suffix in (("deb", "deb"), ("appimage", "AppImage")):
            path = self.root / gate.BINARY.parent / "bundle" / kind / ("Motionwright." + suffix)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"INERT PACKAGE FIXTURE " + kind.encode())
            paths.append(path)
        self.candidate = make_receipt(
            self.root, self.sha, "linux", "x86_64", paths,
            cargo_index=self.cargo_index, cargo_binary=self.cargo_binary, require_cargo_binding=True)
        gate.capture.write_new(self.root / "ci-evidence/linux-candidate.json",
                               gate.capture.encoded(self.candidate))
        _, _, _, observed = gate.cargo_binding(self.root, self.sha)
        notices = _verify_cargo_notice_subtree(
            capture=gate.capture, index_raw=self.cargo_index.read_bytes(),
            anchor=self.root / gate.BASE / "resources", relative=Path("cargo"))
        self.smokes = {}
        for row in self.candidate["artifacts"]:
            kind = row["bundle_kind"]
            smoke = {
                "schema_version": 1, "candidate": self.candidate["candidate"],
                "bundle_kind": kind,
                "candidate_archive": {key: row[key] for key in ("filename", "bytes", "sha256")},
                "automated_inspection": {
                    "status": "PASS", "method": kind + "_extract",
                    "binary": {"binary_bytes": observed["bytes"], "binary_sha256": observed["sha256"]},
                    "cargo_build_binding": {**observed, "notice_resources": notices},
                },
                "claims": {"human_install_acceptance": "NOT_RUN",
                           "runtime_renderer_acceptance": "SEPARATE_GATE", "signing": "NOT_CLAIMED",
                           "notarization": "NOT_CLAIMED", "release_published": False},
            }
            self.smokes[kind] = smoke
            gate.capture.write_new(self.smoke_path(kind), gate.capture.encoded(smoke))
        self.frontend = {
            "source_sha": self.sha, "source_tree": observed["source_tree"],
            "observed_npm_source_and_notice_authentication": "VERIFIED",
            "source_binding": "EXACT_HEAD_TREE_INDEX_AND_TRACKED_BYTES_REVALIDATED",
            "frontend_index_sha256": "4" * 64,
        }
        for phase in ("before-build", "after-bundle"):
            gate.capture.write_new(self.root / gate.BASE / "frontend" / (phase + ".json"),
                                   gate.capture.encoded(self.frontend))

    def smoke_path(self, kind):
        return self.root / ("ci-evidence/linux-" + kind + "-smoke.json")

    def test_linux_layout_does_not_mutate_windows_defaults(self):
        defaults = (gate.shared.BASE, gate.shared.TARGET, gate.shared.BINARY)
        binding = gate.stage_resources(self.root, self.sha)
        self.assertEqual(binding["target"], gate.TARGET)
        self.assertEqual(defaults, (gate.shared.BASE, gate.shared.TARGET, gate.shared.BINARY))
        self.assertFalse((self.root / gate.shared.BASE / "tauri-resources.json").exists())
        mapped = json.loads((self.root / gate.BASE / "tauri-resources.json").read_bytes())
        self.assertEqual(len(mapped["bundle"]["resources"]), 6)
        self.assertIn("licenses/cargo/index.json", mapped["bundle"]["resources"].values())

    def test_both_package_observations_bind_exact_bytes_without_full_inventory_credit(self):
        self.package_fixture()
        result = gate.verify_packages(self.root, self.sha)
        self.assertEqual(set(result["packages"]), {"deb", "appimage"})
        for row in result["packages"].values():
            self.assertEqual(row["binary_binding"], "CAPTURED_ROOT_BINARY_BYTE_EXACT")
            self.assertTrue(row["cargo_notices"]["subtree_closed"])
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        self.assertEqual(result["redistribution_permission"], "NOT_INFERRED")
        self.assertFalse(result["published"])
        original = self.output.read_bytes()
        with self.assertRaises(FileExistsError):
            gate.verify_packages(self.root, self.sha)
        self.assertEqual(self.output.read_bytes(), original)

    def test_wrong_capture_links_or_crossed_generator_profile_refuse_staging(self):
        for field, value in (("cargo_index_sha256", "0" * 64),
                             ("artifact_identities_sha256", "1" * 64),
                             ("generated_metadata_sha256", "2" * 64), ("source", "3" * 40)):
            self.complete_path.write_bytes(gate.capture.encoded({**self.complete, field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.stage_resources(self.root, self.sha)
            self.assertFalse((self.root / gate.BASE / "tauri-resources.json").exists())
        self.complete_path.write_bytes(gate.capture.encoded(self.complete))
        crossed = copy.deepcopy(self.custody)
        crossed["profile"]["schema"] = "motionwright.windows-cargo-generated-metadata/2"
        self.custody_path.write_bytes(gate.capture.encoded(crossed))
        digest = gate.capture.sha(self.custody_path.read_bytes())
        self.cargo_index_data["capture"]["generated_metadata_sha256"] = digest
        self.save_cargo_index()
        self.complete_path.write_bytes(gate.capture.encoded({
            **self.complete, "generated_metadata_sha256": digest,
            "cargo_index_sha256": gate.capture.sha(self.cargo_index.read_bytes())}))
        with self.assertRaisesRegex(ValueError, "cargo_generated_custody_profile_mismatch"):
            gate.stage_resources(self.root, self.sha)

    def test_overlay_or_notice_extras_cannot_pass_package_readback(self):
        self.package_fixture()
        overlay = self.root / gate.BASE / "tauri-resources.json"
        original = overlay.read_bytes()
        altered = json.loads(original)
        altered["bundle"]["resources"]["foreign"] = "foreign"
        overlay.write_bytes(gate.capture.encoded(altered))
        with self.assertRaisesRegex(ValueError, "linux_cargo_overlay_changed"):
            gate.verify_packages(self.root, self.sha)
        overlay.write_bytes(original)
        (self.notice_path.parent / "unexpected.txt").write_bytes(b"Unlisted notice")
        with self.assertRaisesRegex(ValueError, "candidate_cargo_"):
            gate.verify_packages(self.root, self.sha)
        self.assertFalse(self.output.exists())

    def test_either_package_cannot_substitute_legacy_or_wrong_binary_binding(self):
        self.package_fixture()
        for kind in ("deb", "appimage"):
            original = copy.deepcopy(self.smokes[kind])
            for field, value in (("status", "NOT_VERIFIED"), ("sha256", "0" * 64),
                                 ("cargo_index_sha256", "1" * 64), ("source_tree", "2" * 40)):
                changed = copy.deepcopy(original)
                changed["automated_inspection"]["cargo_build_binding"][field] = value
                self.smoke_path(kind).write_bytes(gate.capture.encoded(changed))
                with self.subTest(kind=kind, field=field), self.assertRaisesRegex(
                        ValueError, "linux_package_cargo_binding_mismatch"):
                    gate.verify_packages(self.root, self.sha)
                self.assertFalse(self.output.exists())
            self.smoke_path(kind).write_bytes(gate.capture.encoded(original))

    def test_wrong_archive_or_changed_frontend_cannot_receive_report(self):
        self.package_fixture()
        original = self.smokes["appimage"]
        changed = copy.deepcopy(original)
        changed["candidate_archive"]["sha256"] = "0" * 64
        self.smoke_path("appimage").write_bytes(gate.capture.encoded(changed))
        with self.assertRaisesRegex(ValueError, "linux_package_observation_mismatch"):
            gate.verify_packages(self.root, self.sha)
        self.smoke_path("appimage").write_bytes(gate.capture.encoded(original))
        path = self.root / gate.BASE / "frontend/after-bundle.json"
        path.write_bytes(gate.capture.encoded({**self.frontend, "frontend_index_sha256": "5" * 64}))
        with self.assertRaisesRegex(ValueError, "linux_frontend_authentication_required"):
            gate.verify_packages(self.root, self.sha)
        self.assertFalse(self.output.exists())

    def test_changed_capture_binary_rejected_before_bundle_process(self):
        gate.stage_resources(self.root, self.sha)
        self.cargo_binary.write_bytes(b"changed captured binary")
        environment = {"RUSTFLAGS": gate.RUSTFLAGS, "PATCHELF": gate.PATCHELF, "NO_STRIP": "1"}
        with mock.patch.object(gate.capture, "run_captured") as process:
            with self.assertRaisesRegex(gate.capture.CaptureError,
                                        "installed_binary_differs_from_cargo_capture"):
                gate.bundle_packages(self.root, self.sha, environment)
            process.assert_not_called()


class LinuxScopeTests(unittest.TestCase):
    def test_scope_and_link_override_refusals_precede_effects(self):
        env = {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "seradotcom/motionwright",
            "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
            "MW_LINUX_CANDIDATE_SCOPE": "linux_bundle", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "1" * 40, "GITHUB_RUN_ID": "123",
        }
        self.assertEqual(gate.linux_source(env), "1" * 40)
        for key, value in (("GITHUB_EVENT_NAME", "push"), ("GITHUB_REPOSITORY", "other/repo"),
                           ("GITHUB_REF", "refs/heads/main"), ("GITHUB_RUN_ATTEMPT", "2"),
                           ("MW_LINUX_CANDIDATE_SCOPE", "all"), ("GITHUB_SHA", "1" * 8),
                           ("GITHUB_RUN_ID", "0")):
            with self.subTest(key=key), mock.patch.dict(os.environ, {**env, key: value}, clear=True), \
                 mock.patch.object(gate.shared, "authenticate_frontend") as frontend, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(gate.main(["frontend"]), 2)
                frontend.assert_not_called()
        selected = {"RUSTFLAGS": gate.RUSTFLAGS, "PATCHELF": gate.PATCHELF, "NO_STRIP": "1"}
        self.assertEqual(gate.link_environment(selected), selected)
        for key, value in (("RUSTFLAGS", ""), ("PATCHELF", "/foreign/patchelf"),
                           ("NO_STRIP", ""), ("CARGO_ENCODED_RUSTFLAGS", "override"),
                           ("CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS", "override")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.link_environment({**selected, key: value})


class LinuxFrontendLayoutTests(unittest.TestCase):
    def test_explicit_linux_base_rechecks_real_archives_without_mutating_windows_default(self):
        from test_ci_windows_dependency_capture import FrontendInputCustodyTests
        FrontendInputCustodyTests.setUp(self)
        before_base = gate.shared.BASE
        self.directory = self.f.repo / gate.BASE / "frontend"
        self.f.public_copy()
        prior = FrontendInputCustodyTests.prepare_recheck(self)
        result = gate.shared.authenticate_frontend(self.f.repo, self.f.head,
                                                   recheck=True, base=gate.BASE)
        self.assertEqual(result, prior)
        self.assertEqual(gate.shared.BASE, before_base)
        self.assertTrue((self.directory / "after-bundle.json").is_file())
        self.assertFalse((self.f.repo / before_base / "frontend/after-bundle.json").exists())


if __name__ == "__main__":
    unittest.main()
