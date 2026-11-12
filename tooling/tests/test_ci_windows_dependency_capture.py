"""Inert records and real Git/filesystem checks for Windows capture composition.

No Cargo build, archive download, installer, or application is executed here.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock

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
        from candidate_smoke import NOTICE_FILES
        for original, _ in NOTICE_FILES:
            path = self.root / original
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / original).read_bytes())
        git(self.root, "add", "apps/desktop/src-tauri/tauri.conf.json",
            *(original for original, _ in NOTICE_FILES))
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

    def application_fixture(self):
        from candidate_smoke import NOTICE_FILES
        from package_receipt import make_receipt

        gate.stage_resources(self.root, self.sha)
        artifact = self.root / gate.BINARY.parent / "bundle/nsis" / self.bundle.name
        artifact.parent.mkdir(parents=True)
        self.bundle.replace(artifact)
        self.bundle = artifact
        candidate = make_receipt(
            self.root, self.sha, "windows", "x86_64", [artifact],
            cargo_index=self.cargo_index, cargo_binary=self.cargo_binary,
            require_cargo_binding=True)
        gate.capture.write_new(self.root / "ci-evidence/windows-candidate.json",
                               gate.capture.encoded(candidate))
        self.install_root = self.root / "runner" / gate.INSTALL_DIRECTORY
        self.install_root.parent.mkdir()
        self.inventory_sha = gate.prepare_application_inventory(
            self.root, self.sha, "123", self.install_root)
        self.install_root.mkdir()
        sources = [("motionwright-desktop.exe", self.cargo_binary),
                   ("licenses/cargo/index.json", self.cargo_index),
                   ("licenses/cargo/texts/" + self.notice_path.name, self.notice_path)]
        sources += [(destination, self.root / original) for original, destination in NOTICE_FILES]
        for relative, source in sources:
            target = self.install_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        (self.install_root / "uninstall.exe").write_bytes(
            b"INERT generated member fixture; no installer ran.\n")
        self.candidate = candidate

    def verify_installed(self):
        return gate.verify_installed(
            self.root, self.sha, run_id="123", install_root=self.install_root,
            expected_inventory_sha256=self.inventory_sha)

    def verify_inventory(self, *, root=None, digest=None):
        return gate.verify_application_inventory(
            self.root, self.sha, "123", root or self.install_root,
            digest or self.inventory_sha)

    def test_original_destination_member_set_rejects_extras_and_nested_main(self):
        self.application_fixture()
        original = (self.root / gate.INVENTORY).read_bytes()
        result = self.verify_inventory()
        self.assertTrue(result["namespace_closed"])
        self.assertEqual(result["application_files"], 7)
        self.assertFalse(result["all_member_bytes_authenticated"])
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        pending = result["generated_members"]["uninstall.exe"]
        self.assertEqual(pending["reference_status"], "NOT_OBSERVED_BEFORE_PUBLICATION")
        self.assertNotIn("uninstall.exe", json.loads(original)["application_files"])
        for name, directory in (("unexpected.dll", False), ("unlisted-cache", True)):
            target = self.install_root / name
            if directory:
                target.mkdir()
            else:
                target.write_bytes(b"Unlisted file")
            with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError, "candidate_application_tree_extra"):
                self.verify_inventory()
            target.rmdir() if directory else target.unlink()
            self.assertEqual((self.root / gate.INVENTORY).read_bytes(), original)
        child = self.install_root / "nested"
        child.mkdir()
        for path in list(self.install_root.iterdir()):
            if path != child:
                path.replace(child / path.name)
        (self.install_root / "outside-extra").write_bytes(b"Not part of the nested payload")
        with self.assertRaisesRegex(ValueError, "candidate_application_tree_extra"):
            self.verify_inventory()
        with self.assertRaisesRegex(ValueError, "windows_inventory_destination_invalid"):
            self.verify_inventory(root=child)

    def test_inventory_keeps_prior_digest_source_and_destination_independent(self):
        self.application_fixture()
        path = self.root / gate.INVENTORY
        original = path.read_bytes()
        self.verify_inventory()
        for field, value in (("source", "0"*40), ("workflow_run_id", "124"),
                             ("full_package_inventory", "VERIFIED")):
            changed = json.loads(original)
            changed[field] = value
            raw = gate.capture.encoded(changed)
            path.write_bytes(raw)
            with self.subTest(field=field), self.assertRaisesRegex(
                    ValueError, "windows_inventory_original_changed"):
                self.verify_inventory()
            with self.subTest(rehashed=field), self.assertRaisesRegex(
                    ValueError, "windows_inventory_expectation_mismatch"):
                self.verify_inventory(digest=gate.capture.sha(raw))
            path.write_bytes(original)
        other = self.root / "other-runner" / gate.INSTALL_DIRECTORY
        other.parent.mkdir()
        shutil.copytree(self.install_root, other)
        with self.assertRaisesRegex(ValueError, "windows_inventory_expectation_mismatch"):
            self.verify_inventory(root=other)
        with self.assertRaisesRegex(ValueError, "windows_inventory_destination_preexisting"):
            gate.prepare_application_inventory(self.root, self.sha, "123", self.install_root)
        self.assertEqual(path.read_bytes(), original)
        overlay = self.root / gate.BASE / "tauri-resources.json"
        data = json.loads(overlay.read_bytes())
        data["bundle"]["resources"]["foreign"] = "foreign"
        overlay.write_bytes(gate.capture.encoded(data))
        with self.assertRaisesRegex(ValueError, "windows_inventory_overlay_changed"):
            self.verify_inventory()

    def test_inventory_rejects_missing_substituted_and_hardlinked_members(self):
        self.application_fixture()
        self.verify_inventory()
        main = self.install_root / "motionwright-desktop.exe"
        raw = main.read_bytes()
        main.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, "candidate_application_bytes_mismatch"):
            self.verify_inventory()
        main.unlink()
        with self.assertRaisesRegex(ValueError, "candidate_application_resource_missing"):
            self.verify_inventory()
        os.link(self.cargo_binary, main)
        with self.assertRaisesRegex(ValueError, "candidate_application_hardlink"):
            self.verify_inventory()

    def test_cargo_resource_sources_are_absolute_plain_checkout_files_for_nsis(self):
        # The Tauri overlay may refer to generated Cargo notices above
        # src-tauri. NSIS must never see a '..' source path; preserve the
        # authenticated source bytes by spelling each as a checkout-anchored
        # absolute path instead of weakening nsis_owned_files validation.
        source_root = self.root / gate.BASE / "resources/cargo"
        expected = {
            (source_root / "index.json").as_posix(): "licenses/cargo/index.json",
            (source_root / "texts" / self.notice_path.name).as_posix():
                "licenses/cargo/texts/" + self.notice_path.name,
        }
        mapped = gate.resource_mapping(self.root, self.cargo_index.read_bytes())
        cargo = {name: dest for name, dest in mapped.items()
                 if dest.startswith("licenses/cargo/")}
        self.assertEqual(cargo, expected)
        self.assertEqual(len(cargo), 2)
        for source in cargo:
            with self.subTest(source=source):
                path = Path(source)
                self.assertTrue(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertNotIn("\\", source)
                self.assertIn(source_root, path.parents)
                self.assertTrue(path.is_file())
        gate.stage_resources(self.root, self.sha)
        overlay = json.loads((self.root / gate.BASE / "tauri-resources.json").read_bytes())
        self.assertEqual(overlay["bundle"]["resources"], mapped)

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
        self.application_fixture()
        _, _, _, observed = gate.cargo_binding(self.root, self.sha)
        from candidate_smoke import _verify_cargo_notice_subtree
        notices = _verify_cargo_notice_subtree(
            capture=gate.capture, index_raw=self.cargo_index.read_bytes(),
            anchor=self.root / gate.BASE / "resources", relative=Path("cargo"))
        smoke = {
            "candidate": self.candidate["candidate"],
            "candidate_archive": {key: self.candidate["artifacts"][0][key]
                                  for key in ("filename", "bytes", "sha256")},
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
        result = self.verify_installed()
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
                self.verify_installed()
            self.assertEqual(output.read_bytes(), original)
        changed = copy.deepcopy(smoke)
        changed["candidate_archive"]["sha256"] = "0" * 64
        smoke_path.write_bytes(gate.capture.encoded(changed))
        with self.assertRaisesRegex(ValueError, "windows_installed_inventory_package_mismatch"):
            self.verify_installed()
        self.assertEqual(output.read_bytes(), original)
        smoke["automated_inspection"]["cargo_build_binding"] = {"status": "NOT_VERIFIED"}
        smoke_path.write_bytes(gate.capture.encoded(smoke))
        with self.assertRaisesRegex(ValueError, "windows_installed_cargo_binding_required"):
            self.verify_installed()

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


class FrontendInputCustodyTests(unittest.TestCase):
    """Real local Git/TAR verification; no network, compiler or application."""

    def setUp(self):
        from test_candidate_frontend_authentication import FrontendAdapterTests
        FrontendAdapterTests.setUp(self)
        for name, value in (("BASE", Path("receipts/windows-capture")), ("FRONTEND", Path("dist")),
                            ("BOOTSTRAP_SOURCE", "public/desktop-bootstrap.html")):
            patch = mock.patch.object(gate, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.directory = self.f.repo / gate.BASE / "frontend"

    def prepare_recheck(self):
        report = self.f.run("prior-authentication.json")
        gate.capture.new_directory(self.f.repo, self.directory)
        archives = gate.capture.new_directory(self.f.repo, self.directory / "archives")
        (archives / "fixture.tgz").write_bytes((self.f.archive_root / "fixture.tgz").read_bytes())
        gate.capture.write_new(self.directory / "archive-map.json",
                               gate.capture.encoded({"node_modules/fixture": "fixture.tgz"}))
        gate.capture.write_new(self.directory / "before-build.json", gate.capture.encoded(report))
        return report

    def observation(self, phase="pre-recheck"):
        return json.loads((self.directory / (phase + "-emitted-set.json")).read_bytes())

    def assert_original(self, raw, phase="pre-recheck"):
        self.assertEqual((self.directory / (phase + "-index.original.json")).read_bytes(), raw)
        record = self.observation(phase)
        self.assertEqual(record["source_sha"], self.f.head)
        self.assertEqual(record["index"]["sha256"], gate.capture.sha(raw))
        self.assertEqual(record["npm_authentication"], "NOT_VERIFIED")
        self.assertEqual(record["full_package_inventory"], "NOT_VERIFIED")
        return record

    def test_declared_public_copy_authenticates_and_each_phase_is_create_only(self):
        self.f.public_copy()
        raw_copy = self.f.assets["desktop-bootstrap.html"]
        before = self.f.before
        original = (self.f.out / "assets/licenses/frontend/index.json").read_bytes()
        prior = self.prepare_recheck()
        gate.retain_frontend_inputs(self.f.repo, before, self.directory, "pre-archive")
        self.assertEqual(gate.authenticate_frontend(self.f.repo, self.f.head, recheck=True), prior)
        record = self.assert_original(original)
        self.assertTrue(record["collection_complete"])
        self.assertEqual(record["files"]["desktop-bootstrap.html"],
                         {"bytes": len(raw_copy), "sha256": gate.capture.sha(raw_copy)})
        self.assert_original(original, "pre-archive")
        retained = {path.name: path.read_bytes() for path in self.directory.iterdir() if path.is_file()}
        with self.assertRaises(FileExistsError):
            gate.authenticate_frontend(self.f.repo, self.f.head, recheck=True)
        self.assertEqual({path.name: path.read_bytes() for path in self.directory.iterdir() if path.is_file()},
                         retained)

    def test_windows_gate_refuses_legacy_index_after_all_copy_markers_are_removed(self):
        row = self.f.public_copy()
        self.f.index["source_binding"].pop("public_directory")
        for key in ("origin", "source_path", "git_blob"):
            row.pop(key)
        self.f.write_index()
        prior = self.prepare_recheck()
        self.assertEqual(prior["public_copy_source_authentication"], "NO_OBSERVED_PUBLIC_COPIES")
        with self.assertRaisesRegex(ValueError, "windows_bootstrap_source_authentication_required"):
            gate.authenticate_frontend(self.f.repo, self.f.head, recheck=True)

    def test_windows_bootstrap_requires_expected_source_even_when_other_source_authenticates(self):
        self.f.public_copy("other-public")
        prior = self.prepare_recheck()
        self.assertEqual(prior["public_copy_source_authentication"],
                         "EXACT_TRACKED_GIT_RAW_SOURCE_BYTES_VERIFIED")
        with self.assertRaisesRegex(ValueError, "windows_bootstrap_source_authentication_required"):
            gate.authenticate_frontend(self.f.repo, self.f.head, recheck=True)

    def test_unexpected_physical_member_is_retained_while_real_authentication_rejects_it(self):
        self.prepare_recheck()
        original = (self.f.out / "assets/licenses/frontend/index.json").read_bytes()
        (self.f.out / "unexpected.bin").write_bytes(b"Undeclared physical bytes.\n")
        (self.f.out / "empty-unexpected-directory").mkdir()
        with self.assertRaisesRegex(ValueError, "emitted_asset_set_mismatch"):
            gate.authenticate_frontend(self.f.repo, self.f.head, recheck=True)
        record = self.assert_original(original)
        self.assertTrue(record["collection_complete"])
        self.assertEqual(record["entries"]["empty-unexpected-directory"], "directory")
        self.assertEqual(record["files"]["unexpected.bin"]["sha256"],
                         gate.capture.sha(b"Undeclared physical bytes.\n"))
        self.assertNotIn("Undeclared physical bytes.", json.dumps(record))
        self.assertFalse((self.directory / "after-bundle.json").exists())

    def test_invalid_index_bytes_survive_before_parsing_without_normalization(self):
        original = b'{  "invalid" : \r\n ORIGINAL-BYTES\t}\r\n'
        (self.f.out / "assets/licenses/frontend/index.json").write_bytes(original)
        with self.assertRaises((ValueError, gate.capture.CaptureError)):
            gate.authenticate_frontend(self.f.repo, self.f.head)
        self.assertTrue(self.assert_original(original, "pre-archive")["collection_complete"])
        self.assertFalse((self.directory / "before-build.json").exists())
        self.assertEqual(list((self.directory / "archives").iterdir()), [])

    def test_originals_survive_real_local_archive_integrity_failure(self):
        self.prepare_recheck()
        original = (self.f.out / "assets/licenses/frontend/index.json").read_bytes()
        archive = self.directory / "archives/fixture.tgz"
        raw = bytearray(archive.read_bytes())
        raw[-1] ^= 1
        archive.write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            gate.authenticate_frontend(self.f.repo, self.f.head, recheck=True)
        self.assertRegex(str(caught.exception).lower(), r"sri|integrity")
        self.assertTrue(self.assert_original(original)["collection_complete"])
        self.assertFalse((self.directory / "after-bundle.json").exists())

    def test_authentication_of_another_valid_index_cannot_replace_retained_original(self):
        self.prepare_recheck()
        index_path = self.f.out / "assets/licenses/frontend/index.json"
        original = index_path.read_bytes()
        authentic = gate.frontend.authenticate_build

        def authenticate_after_real_replacement(**kwargs):
            index_path.write_bytes(b" \r\n" + original)
            return authentic(**kwargs)

        with mock.patch.object(gate.frontend, "authenticate_build",
                               side_effect=authenticate_after_real_replacement), \
             self.assertRaisesRegex(ValueError, "frontend_authenticated_index_differs_from_retained_original"):
            gate.authenticate_frontend(self.f.repo, self.f.head, recheck=True)
        self.assertTrue(self.assert_original(original)["collection_complete"])
        # The separate authenticator's original report survives; the composed gate failed.
        report = json.loads((self.directory / "after-bundle.json").read_bytes())
        self.assertNotEqual(report["frontend_index_sha256"], gate.capture.sha(original))

    def test_secondary_recording_failure_does_not_mask_the_original_collection_rejection(self):
        self.prepare_recheck()
        original = (self.f.out / "assets/licenses/frontend/index.json").read_bytes()
        write_original = gate.capture.write_new

        def refuse_secondary_record(path, data):
            if path.name == "pre-recheck-emitted-set.json":
                raise OSError("Synthetic secondary output refusal")
            return write_original(path, data)

        with mock.patch.object(gate.frontend, "MAX_EMITTED_ENTRIES", 1), \
             mock.patch.object(gate.capture, "write_new", side_effect=refuse_secondary_record), \
             self.assertRaisesRegex(ValueError, "emitted_entry_count_budget") as caught:
            gate.authenticate_frontend(self.f.repo, self.f.head, recheck=True)
        self.assertEqual((self.directory / "pre-recheck-index.original.json").read_bytes(), original)
        self.assertIn("Secondary observation recording failed: OSError", caught.exception.__notes__)
        self.assertFalse((self.directory / "pre-recheck-emitted-set.json").exists())
        self.assertFalse((self.directory / "after-bundle.json").exists())

    def test_collection_budget_failure_preserves_index_and_explicit_incomplete_observation(self):
        self.prepare_recheck()
        original = (self.f.out / "assets/licenses/frontend/index.json").read_bytes()
        with mock.patch.object(gate.frontend, "MAX_EMITTED_ENTRIES", 1), \
             self.assertRaisesRegex(ValueError, "emitted_entry_count_budget"):
            gate.authenticate_frontend(self.f.repo, self.f.head, recheck=True)
        record = self.assert_original(original)
        self.assertFalse(record["collection_complete"])
        self.assertNotIn("entries", record)
        self.assertNotIn("files", record)
        self.assertEqual(record["original_exception"], "PROPAGATED")
        self.assertFalse((self.directory / "after-bundle.json").exists())


if __name__ == "__main__":
    unittest.main()
