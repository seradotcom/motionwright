"""Inert records and real Git/filesystem checks for Windows capture composition.

No Cargo build, archive download, installer, or application is executed here.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_windows_dependency_capture as gate
import test_candidate_cargo_producer as fixtures
from test_candidate_receipt_source import git


class WindowsDependencyCaptureTests(unittest.TestCase):
    def setUp(self):
        fixtures.CandidateCargoProducerTests.setUp(self)
        config = self.root / "apps/desktop/src-tauri/tauri.conf.json"
        config.write_bytes((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_bytes())
        git(self.root, "add", "apps/desktop/src-tauri/tauri.conf.json")
        git(self.root, "-c", "user.name=fixture", "-c",
            "user.email=fixture@example.invalid", "commit", "-qm", "resource fixture")
        self.sha = git(self.root, "rev-parse", "HEAD")
        fixtures.CandidateCargoProducerTests.cargo_fixture(self)
        binary = self.root / gate.BINARY
        binary.parent.mkdir(parents=True)
        self.cargo_binary.replace(binary)
        self.cargo_binary = binary
        raw_notice = b"Inert notice fixture; not a production license.\n"
        digest = gate.capture.sha(raw_notice)
        self.cargo_index_data["components"][0]["notices"] = [{"sha256": digest}]
        self.cargo_index = self.root / gate.BASE / "resources/cargo/index.json"
        self.cargo_index.parent.mkdir(parents=True)
        text = self.cargo_index.parent / "texts" / (digest + ".txt")
        text.parent.mkdir()
        text.write_bytes(raw_notice)
        self.notice_path = text
        custody = gate.capture.encoded({"profile": {"source": self.sha, "target": gate.TARGET},
                                        "fixture_only": "No compiler or generator executed"})
        gate.capture.write_new(self.root / gate.BASE / "state/tauri-generated-schemas.json", custody)
        self.custody_sha = gate.capture.sha(custody)
        self.cargo_index_data["capture"] = {"generated_metadata_sha256": self.custody_sha}
        self.save_cargo_index()
        self.complete = {
            "source": self.sha, "cargo_index_sha256": gate.capture.sha(self.cargo_index.read_bytes()),
            "artifact_identities_sha256": self.cargo_index_data["artifact_identities_sha256"],
            "full_package_inventory": "NOT_VERIFIED",
            "generated_metadata_sha256": self.custody_sha,
        }
        gate.capture.write_new(self.root / gate.BASE / "state/capture-complete.json",
                               gate.capture.encoded(self.complete))

    save_cargo_index = fixtures.CandidateCargoProducerTests.save_cargo_index

    def test_overlay_preserves_originals_and_binds_real_bytes_before_publication(self):
        binding = gate.stage_resources(self.root, self.sha)
        overlay = self.root / gate.BASE / "tauri-resources.json"
        original = overlay.read_bytes()
        resources = json.loads(original)["bundle"]["resources"]
        base = json.loads((self.root / "apps/desktop/src-tauri/tauri.conf.json").read_bytes())
        self.assertTrue(base["bundle"]["resources"].items() <= resources.items())
        self.assertEqual(len(resources), 6)
        self.assertIn("licenses/cargo/index.json", resources.values())
        self.assertEqual(binding["cargo_index_sha256"], self.complete["cargo_index_sha256"])
        self.assertEqual(binding["name"], "motionwright-desktop")
        raw = self.cargo_binary.read_bytes()
        self.cargo_binary.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with self.assertRaisesRegex(gate.capture.CaptureError,
                                     "installed_binary_differs_from_cargo_capture"):
            gate.stage_resources(self.root, self.sha)
        self.assertEqual(overlay.read_bytes(), original)
        self.cargo_binary.write_bytes(raw)
        self.notice_path.write_bytes(b"changed notice")
        with self.assertRaisesRegex(ValueError, "candidate_cargo_notice_digest_mismatch"):
            gate.stage_resources(self.root, self.sha)
        self.assertEqual(overlay.read_bytes(), original)

    def test_wrong_completion_or_source_never_creates_overlay(self):
        path = self.root / gate.BASE / "state/capture-complete.json"
        for key, value in (("cargo_index_sha256", "0"*64),
                           ("artifact_identities_sha256", "1"*64), ("source", "2"*40)):
            path.write_bytes(gate.capture.encoded({**self.complete, key: value}))
            with self.subTest(field=key), self.assertRaises(ValueError):
                gate.stage_resources(self.root, self.sha)
            self.assertFalse((self.root / gate.BASE / "tauri-resources.json").exists())
        path.write_bytes(gate.capture.encoded(self.complete))
        with self.assertRaises(gate.capture.CaptureError):
            gate.stage_resources(self.root, "3"*40)

    def test_installed_readback_requires_all_actual_binding_fields_and_notices(self):
        _, _, _, observed = gate.cargo_binding(self.root, self.sha)
        from candidate_smoke import _verify_cargo_notice_subtree
        notices = _verify_cargo_notice_subtree(
            capture=gate.capture, index_raw=self.cargo_index.read_bytes(),
            anchor=self.root / gate.BASE / "resources", relative=Path("cargo"))
        smoke = {
            "candidate": {"source_revision": self.sha},
            "automated_inspection": {"cargo_build_binding": {**observed, "notice_resources": notices}},
        }
        smoke_path = self.root / "ci-evidence/windows-nsis-smoke.json"
        smoke_path.write_bytes(gate.capture.encoded(smoke))
        frontend = {
            "source_sha": self.sha, "source_tree": observed["source_tree"],
            "observed_npm_source_and_notice_authentication": "VERIFIED",
            "source_binding": "EXACT_HEAD_TREE_INDEX_AND_TRACKED_BYTES_REVALIDATED",
            "frontend_index_sha256": "4"*64,
        }
        gate.capture.write_new(self.root / gate.BASE / "frontend/after-bundle.json",
                               gate.capture.encoded(frontend))
        result = gate.verify_installed(self.root, self.sha)
        self.assertEqual(result["installed_binary"]["sha256"], gate.capture.sha(self.cargo_original))
        self.assertFalse(result["published"])
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        output = self.root / "ci-evidence/windows-cargo-binding.json"
        original = output.read_bytes()
        for field in ("cargo_index_sha256", "name", "unit_id", "source_tree"):
            changed = copy.deepcopy(smoke)
            changed["automated_inspection"]["cargo_build_binding"][field] = "foreign"
            smoke_path.write_bytes(gate.capture.encoded(changed))
            with self.subTest(field=field), self.assertRaisesRegex(
                    ValueError, "windows_installed_cargo_identity_mismatch"):
                gate.verify_installed(self.root, self.sha)
            self.assertEqual(output.read_bytes(), original)
        smoke["automated_inspection"]["cargo_build_binding"] = {"status": "NOT_VERIFIED"}
        smoke_path.write_bytes(gate.capture.encoded(smoke))
        with self.assertRaisesRegex(ValueError, "windows_installed_cargo_binding_required"):
            gate.verify_installed(self.root, self.sha)

    def test_scope_and_archive_origins_are_closed_before_any_effect(self):
        environment = {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "seradotcom/motionwright",
            "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
            "MW_WINDOWS_CANDIDATE_SCOPE": "windows_installed", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": self.sha, "GITHUB_RUN_ID": "123",
        }
        self.assertEqual(gate.internal_source(environment), self.sha)
        for key, value in (("GITHUB_EVENT_NAME", "push"), ("MW_WINDOWS_CANDIDATE_SCOPE", "all"),
                           ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_REPOSITORY", "foreign/repo"),
                           ("GITHUB_REF", "refs/heads/main"), ("GITHUB_RUN_ID", "0")):
            with self.subTest(field=key), self.assertRaises(ValueError):
                gate.internal_source({**environment, key: value})
        allowed = "https://registry.npmjs.org/fixture/-/fixture-1.0.0.tgz"
        self.assertEqual(gate.archive_url(allowed), allowed)
        for url in ("http://registry.npmjs.org/a.tgz", "https://example.invalid/a.tgz",
                    "https://user@registry.npmjs.org/a.tgz", allowed + "?token=foreign",
                    allowed + "#fragment", "https://registry.npmjs.org:444/a.tgz",
                    "file:///foreign.tgz", "https://registry.npmjs.org/a.tgz\n"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                gate.archive_url(url)


if __name__ == "__main__":
    unittest.main()
