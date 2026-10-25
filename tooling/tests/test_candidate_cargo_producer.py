"""Candidate build-input binding using real Git and inert package fixtures."""
from __future__ import annotations
import contextlib
import copy
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_candidate_receipt_source as source_fixture
candidate = source_fixture.candidate
git = source_fixture.git


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


class CandidateCargoProducerTests(unittest.TestCase):
    setUp = source_fixture.CandidateReceiptSourceTests.setUp

    def invoke(self, *, extra_args=(), artifacts=None, platform="windows"):
        stdout, stderr = io.StringIO(), io.StringIO()
        args = ["--git-sha", self.sha, "--platform", platform, "--arch", "x86_64"]
        for path in artifacts or [self.bundle]:
            args += ["--artifact", str(path)]
        args += ["--output", str(self.receipt), *extra_args]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = candidate.main(args, root=self.root)
        return code, stdout.getvalue(), stderr.getvalue()

    def cargo_fixture(self, target="x86_64-pc-windows-msvc"):
        self.cargo_binary = self.root / "target/captured/motionwright-desktop"
        self.cargo_binary.parent.mkdir(parents=True)
        self.cargo_original = b"INERT CAPTURED BUILD INPUT\n" + bytes(range(128))
        self.cargo_binary.write_bytes(self.cargo_original)
        public_id = ("cargo:path:apps/desktop/src-tauri/Cargo.toml"
                     "#motionwright-desktop@0.1.0")
        identity = {"path": target + "/release/motionwright-desktop",
                    "bytes": len(self.cargo_original),
                    "sha256": sha256(self.cargo_original).hexdigest()}
        self.cargo_index_data = {
            "schema": "motionwright.cargo-build-notices/1", "source": self.sha,
            "source_tree": git(self.root, "rev-parse", "HEAD^{tree}"),
            "target": target, "root_package": "motionwright-desktop",
            "components": [{"component_id": public_id, "name": "motionwright-desktop"}],
            "artifact_identities": [identity],
            "artifact_identities_sha256": sha256(encoded([identity])).hexdigest(),
            "root_binary_observations": [{
                "package_id": public_id, "package": "motionwright-desktop",
                "target": target, "name": "motionwright-desktop",
                "unit_id": "3" * 64, **identity}],
            "coverage": {"full_package_inventory": "NOT_VERIFIED"},
        }
        self.cargo_index = self.root / "target/capture/cargo-index.json"
        self.cargo_index.parent.mkdir(parents=True)
        self.save_cargo_index()

    def save_cargo_index(self):
        raw = encoded(self.cargo_index_data)
        self.cargo_index.write_bytes(raw)
        return raw

    def cargo_arguments(self, *, index=None, binary=None):
        return ["--cargo-index", str(index or self.cargo_index),
                "--cargo-binary", str(binary or self.cargo_binary),
                "--require-cargo-binding"]

    def assert_refused(self, result):
        code, output, errors = result
        self.assertEqual((code, output), (2, ""))
        self.assertFalse(self.receipt.exists())
        self.assertNotIn(str(self.root), errors)
        return errors

    def test_cargo_binding_requires_complete_pair_in_api_and_cli(self):
        self.cargo_fixture()
        for options in ({"cargo_index": self.cargo_index},
                        {"cargo_binary": self.cargo_binary},
                        {"require_cargo_binding": True}):
            with self.subTest(options=tuple(options)), self.assertRaises(candidate.CandidateError):
                candidate.make_receipt(self.root, self.sha, "windows", "x86_64",
                                       [self.bundle], **options)
        for args in (["--cargo-index", str(self.cargo_index)],
                     ["--cargo-binary", str(self.cargo_binary)],
                     ["--require-cargo-binding"]):
            with self.subTest(argument=args[0]):
                self.assert_refused(self.invoke(extra_args=args))

    def test_cargo_binding_fixes_exact_index_real_tree_and_preserves_legacy_limits(self):
        self.cargo_fixture()
        legacy = candidate.make_receipt(self.root, self.sha, "windows", "x86_64", [self.bundle])
        self.assertNotIn("cargo_build", legacy["artifacts"][0])
        self.assertNotIn("package_contents_verification", legacy["claims"])
        raw = self.cargo_index.read_bytes()
        code, _, errors = self.invoke(extra_args=self.cargo_arguments())
        self.assertEqual((code, errors), (0, ""))
        data = json.loads(self.receipt.read_bytes())
        binding = data["artifacts"][0]["cargo_build"]
        self.assertEqual(set(binding), {"index_sha256", "source_tree", "target",
                                       "root_package", "binary_target"})
        self.assertEqual(binding["index_sha256"], sha256(raw).hexdigest())
        self.assertEqual(binding["source_tree"], git(self.root, "rev-parse", "HEAD^{tree}"))
        self.assertEqual(data["claims"]["package_contents_verification"], "SEPARATE_EXTRACTION_GATE")
        self.assertFalse(data["claims"]["published"])
        self.assertEqual(data["claims"]["distribution_signature"], "NOT_CLAIMED")
        self.assertEqual(data["claims"]["human_install_acceptance"], "NOT_RUN")
        self.assertNotIn(str(self.root), json.dumps(data))

    def test_two_linux_packages_fix_the_same_build_input_without_claiming_extraction(self):
        self.cargo_fixture("x86_64-unknown-linux-gnu")
        paths = [self.bundle.parent / "Motionwright.deb",
                 self.bundle.parent / "Motionwright.AppImage"]
        for number, path in enumerate(paths):
            path.write_bytes(b"INERT PACKAGE " + str(number).encode())
        code, _, errors = self.invoke(platform="linux", artifacts=paths,
                                      extra_args=self.cargo_arguments())
        self.assertEqual((code, errors), (0, ""))
        data = json.loads(self.receipt.read_bytes())
        records = data["artifacts"]
        self.assertEqual({row["bundle_kind"] for row in records}, {"deb", "appimage"})
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["cargo_build"], records[1]["cargo_build"])
        self.assertEqual(len(records[0]["cargo_build"]), 5)
        self.assertEqual({row["sha256"] for row in records},
                         {sha256(path.read_bytes()).hexdigest() for path in paths})
        self.assertEqual(data["claims"]["package_contents_verification"], "SEPARATE_EXTRACTION_GATE")

    def test_cargo_source_tree_target_and_component_expectations_are_independent(self):
        self.cargo_fixture()
        original = copy.deepcopy(self.cargo_index_data)
        for field, value in (("source", "4"*40), ("source_tree", "5"*40),
                             ("target", "aarch64-apple-darwin"),
                             ("root_package", "foreign"),
                             ("binary_name", "foreign"),
                             ("component_path", "foreign/Cargo.toml")):
            with self.subTest(field=field):
                self.cargo_index_data = copy.deepcopy(original)
                if field == "binary_name":
                    self.cargo_index_data["root_binary_observations"][0]["name"] = value
                elif field == "component_path":
                    changed = original["components"][0]["component_id"].replace(
                        "apps/desktop/src-tauri/Cargo.toml", value)
                    self.cargo_index_data["components"][0]["component_id"] = changed
                    self.cargo_index_data["root_binary_observations"][0]["package_id"] = changed
                else:
                    self.cargo_index_data[field] = value
                self.save_cargo_index()
                errors = self.assert_refused(self.invoke(extra_args=self.cargo_arguments()))
                if field == "component_path":
                    self.assertIn("candidate_cargo_component_mismatch", errors)

    def test_same_size_build_input_substitution_is_rejected(self):
        self.cargo_fixture()
        changed = bytearray(self.cargo_original); changed[-1] ^= 1
        self.cargo_binary.write_bytes(changed)
        errors = self.assert_refused(self.invoke(extra_args=self.cargo_arguments()))
        self.assertIn("candidate_cargo_capture_invalid", errors)

    def test_equal_bytes_outside_checkout_cannot_supply_a_new_evidence_anchor(self):
        self.cargo_fixture()
        outside_index = self.root.parent / "outside-index.json"
        outside_binary = self.root.parent / "outside-binary"
        outside_index.write_bytes(self.cargo_index.read_bytes())
        outside_binary.write_bytes(self.cargo_original)
        for args in (self.cargo_arguments(index=outside_index),
                     self.cargo_arguments(binary=outside_binary)):
            with self.subTest(argument=args[0]):
                self.assert_refused(self.invoke(extra_args=args))

    def test_changed_or_staged_non_metadata_source_cannot_keep_the_capture(self):
        source = self.root / "ordinary-source.rs"
        source.write_bytes(b"original tracked source\n")
        git(self.root, "add", "ordinary-source.rs")
        git(self.root, "-c", "user.name=fixture", "-c",
            "user.email=fixture@example.invalid", "commit", "-qm", "additional source")
        self.sha = git(self.root, "rev-parse", "HEAD")
        self.cargo_fixture()
        source.write_bytes(b"modified tracked source\n")
        self.assert_refused(self.invoke(extra_args=self.cargo_arguments()))
        git(self.root, "add", "ordinary-source.rs")
        self.assert_refused(self.invoke(extra_args=self.cargo_arguments()))

    def test_existing_receipt_is_unchanged_with_binding_enabled(self):
        self.cargo_fixture()
        self.receipt.parent.mkdir()
        original = b"previous independently retained receipt"
        self.receipt.write_bytes(original)
        code, output, _ = self.invoke(extra_args=self.cargo_arguments())
        self.assertEqual((code, output), (2, ""))
        self.assertEqual(self.receipt.read_bytes(), original)

    def test_index_change_after_publication_preserves_uncertain_receipt(self):
        self.cargo_fixture()
        original_publish = candidate.write_new_manifest
        retained = []

        def publish_then_change(*args, **kwargs):
            result = original_publish(*args, **kwargs)
            retained.append(self.receipt.read_bytes())
            self.cargo_index.write_bytes(self.cargo_index.read_bytes() + b"\n")
            return result

        with patch.object(candidate, "write_new_manifest", side_effect=publish_then_change) as publish, \
             patch.object(candidate, "_discard_new_output_on_source_change",
                          wraps=candidate._discard_new_output_on_source_change) as uncertain:
            code, output, errors = self.invoke(extra_args=self.cargo_arguments())
        self.assertEqual((code, output), (2, ""))
        self.assertEqual(errors, "candidate-receipt=REFUSED code=manifest_output_commit_uncertain\n")
        publish.assert_called_once(); uncertain.assert_called_once()
        self.assertEqual(len(retained), 1)
        self.assertEqual(self.receipt.read_bytes(), retained[0])

    def test_build_input_change_during_package_hashing_prevents_publication(self):
        self.cargo_fixture()
        original_hash = candidate.verified_artifact

        def hash_then_change(*args, **kwargs):
            result = original_hash(*args, **kwargs)
            changed = bytearray(self.cargo_original); changed[-1] ^= 1
            self.cargo_binary.write_bytes(changed)
            return result

        with patch.object(candidate, "verified_artifact", side_effect=hash_then_change), \
             patch.object(candidate, "write_new_manifest", wraps=candidate.write_new_manifest) as publish:
            errors = self.assert_refused(self.invoke(extra_args=self.cargo_arguments()))
        self.assertIn("candidate_cargo_capture_invalid", errors)
        publish.assert_not_called()



    def prepare_producer_consumer_fixture(self):
        import importlib.util
        import struct
        import tomllib

        root = Path(candidate.__file__).resolve().parents[1]
        specification = importlib.util.spec_from_file_location(
            "candidate_smoke_producer_integration", root / "tooling/candidate_smoke.py",
        )
        assert specification and specification.loader
        self.consumer = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(self.consumer)
        for name in ("Cargo.toml", "SOURCE_LOCK.json",
                     "apps/desktop/src-tauri/tauri.conf.json"):
            (self.root / name).write_bytes((root / name).read_bytes())
        git(self.root, "add", "Cargo.toml", "SOURCE_LOCK.json",
            "apps/desktop/src-tauri/tauri.conf.json")
        git(self.root, "-c", "user.name=fixture", "-c",
            "user.email=fixture@example.invalid", "commit", "-qm",
            "bind inert fixture metadata to the consumer source")
        self.sha = git(self.root, "rev-parse", "HEAD")
        self.cargo_fixture()
        original = bytearray(512)
        original[:2] = b"MZ"
        struct.pack_into("<I", original, 0x3c, 0x80)
        original[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", original, 0x84, 0x8664)
        self.cargo_original = bytes(original)
        self.cargo_binary.write_bytes(self.cargo_original)
        version = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf8"))[
            "workspace"]["package"]["version"]
        identity = ("cargo:path:apps/desktop/src-tauri/Cargo.toml"
                    "#motionwright-desktop@" + version)
        self.cargo_index_data["components"][0]["component_id"] = identity
        artifact = self.cargo_index_data["artifact_identities"][0]
        artifact.update(path="x86_64-pc-windows-msvc/release/motionwright-desktop.exe",
                        bytes=len(self.cargo_original),
                        sha256=sha256(self.cargo_original).hexdigest())
        self.cargo_index_data["artifact_identities_sha256"] = sha256(
            encoded([artifact])).hexdigest()
        self.cargo_index_data["root_binary_observations"][0].update(
            package_id=identity, **artifact)
        cargo_notice = (root / "LICENSE").read_bytes()
        cargo_notice_sha = sha256(cargo_notice).hexdigest()
        self.cargo_index_data["components"][0]["notices"] = [
            {"sha256": cargo_notice_sha},
        ]
        source_text = self.cargo_index.parent / "texts" / (cargo_notice_sha + ".txt")
        source_text.parent.mkdir(parents=True, exist_ok=True)
        source_text.write_bytes(cargo_notice)
        self.save_cargo_index()
        self.extracted_binary = self.root / "inert-extraction/Motionwright.exe"
        self.extracted_binary.parent.mkdir()
        self.extracted_binary.write_bytes(self.cargo_original)
        for source, target in self.consumer.NOTICE_FILES:
            path = self.extracted_binary.parent / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((root / source).read_bytes())
        extracted_index = self.extracted_binary.parent / "licenses/cargo/index.json"
        extracted_index.parent.mkdir(parents=True)
        extracted_index.write_bytes(self.cargo_index.read_bytes())
        extracted_text = extracted_index.parent / "texts" / source_text.name
        extracted_text.parent.mkdir()
        extracted_text.write_bytes(source_text.read_bytes())
        code, _, errors = self.invoke(extra_args=self.cargo_arguments())
        self.assertEqual((code, errors), (0, ""))

    def inspect_producer_consumer_fixture(self):
        return self.consumer.build_receipt(
            candidate_receipt=self.receipt, artifact=self.bundle,
            installed_binary=self.extracted_binary, kind="nsis",
            method="nsis_silent_current_user",
        )

    def test_produced_binding_is_consumed_by_the_real_package_inspector(self):
        self.prepare_producer_consumer_fixture()
        receipt = self.inspect_producer_consumer_fixture()
        binding = receipt["automated_inspection"]["cargo_build_binding"]
        self.assertEqual(binding["status"], "CAPTURED_ROOT_BINARY_BYTE_EXACT")
        self.assertEqual(binding["sha256"], sha256(self.cargo_original).hexdigest())
        notices = binding["notice_resources"]
        self.assertEqual(notices["status"], "DECLARED_CARGO_TEXTS_PRESENT_BYTE_EXACT")
        self.assertEqual(notices["unique_texts"], 1)
        self.assertTrue(notices["subtree_closed"])
        self.assertEqual(notices["full_package_inventory"], "NOT_VERIFIED")
        self.assertEqual(receipt["candidate"]["source_revision"], self.sha)
        self.assertEqual(receipt["claims"]["human_install_acceptance"], "NOT_RUN")
        self.assertEqual(receipt["claims"]["signing"], "NOT_CLAIMED")
        self.assertFalse(receipt["claims"]["release_published"])

    def test_produced_binding_rejects_same_size_extracted_body_substitution(self):
        self.prepare_producer_consumer_fixture()
        changed = bytearray(self.cargo_original)
        changed[400] ^= 1
        self.extracted_binary.write_bytes(changed)
        with self.assertRaisesRegex(
                self.consumer.CandidateError, "installed_binary_differs_from_cargo_capture"):
            self.inspect_producer_consumer_fixture()


if __name__ == "__main__":
    unittest.main(verbosity=2)
