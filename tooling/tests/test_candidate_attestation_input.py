"""v0.1 synthetic candidate attestation input verifier regression corpus.

No Sigstore signing, native rendering, user sessions, installation or external
network access occurs. Verified provenance requires a separate GitHub job.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate_attestation_input as verify
import package_receipt as package


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True,
        timeout=12, check=True,
    )
    return result.stdout.strip()


class CandidateAttestationInputs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mw-v01-gh-provenance-")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.root = self.home / "checkout"
        self.root.mkdir()
        rows = {
            "Cargo.toml": '[workspace.package]\nrepository="https://example.invalid/mw"\nversion="0.1.0"\nlicense="AGPL-3.0-or-later"\n',
            "Cargo.lock": "# fixture",
            "SOURCE_LOCK.json": json.dumps({"dependencies": {"semwright": {
                "repository": "https://example.invalid/sw",
                "revision": "b"*40, "native_sdk_package": "semwright-client",
                "license_observed": "AGPL-3.0",
                "features_planned": ["native-sdk"],
            }}}),
            "LICENSE": "synthetic test license",
            "THIRD_PARTY_NOTICES.md": "test only",
            "apps/studio/package-lock.json": '{"lockfileVersion":3}',
            "apps/desktop/src-tauri/tauri.conf.json": "{}",
            "docs/acceptance/requirements-status.json": "{}",
            "docs/acceptance/acceptance-status.json": "{}",
            "docs/security/known-rustsec-advisories.json": "{}",
        }
        for name, value in rows.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf8")
        git(self.root, "init", "-q")
        git(self.root, "add", ".")
        git(self.root, "-c", "user.name=fixture", "-c",
            "user.email=test@example.invalid", "commit", "-qm", "baseline")
        self.sha = git(self.root, "rev-parse", "HEAD")

        self.download = self.home / "candidates"
        self.download.mkdir()
        self.out = self.home / "generated"
        self.out.mkdir()

        for platform in verify.PLATFORMS:
            folder = self.download / platform
            (folder / "ci-evidence").mkdir(parents=True)
            names = {
                "linux": ["Motionwright_0.1.0_amd64.AppImage",
                          "Motionwright_0.1.0_amd64.deb"],
                "windows": ["Motionwright_0.1.0_x64-setup.exe"],
                "macos": ["Motionwright_0.1.0_aarch64.dmg"],
            }[platform]
            files = []
            for name in names:
                p = folder / "target" / "release" / "bundle" / platform / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(("synthetic byte stream "+name).encode())
                files.append(p)
            receipt = package.make_receipt(
                self.root, self.sha, platform,
                "aarch64" if platform == "macos" else "x86_64", files,
            )
            (folder / "ci-evidence" / verify.RECEIPTS[platform]).write_text(
                json.dumps(receipt), encoding="utf-8"
            )

    def report(self, sha=None):
        return verify.verify_candidates(self.root, sha or self.sha, self.download)

    def receipt(self, platform: str) -> Path:
        return self.download / platform / "ci-evidence" / verify.RECEIPTS[platform]

    def update(self, platform: str, patch):
        p = self.receipt(platform)
        original = json.loads(p.read_text())
        patch(original)
        p.write_text(json.dumps(original), encoding="utf8")

    def test_four_exact_packages_with_source_bound_receipts(self):
        report = self.report()
        self.assertEqual(report["schema"], verify.SCHEMA)
        self.assertEqual(report["motionwright_source_sha"], self.sha)
        self.assertEqual(report["semwright_source_pin"], "b"*40)
        self.assertEqual(len(report["subjects"]), 4)
        self.assertEqual({r["bundle_kind"] for r in report["subjects"]},
                         {"appimage", "deb", "nsis", "dmg"})
        for row in report["subjects"]:
            actual = self.download / row["path"]
            self.assertEqual(hashlib.sha256(actual.read_bytes()).hexdigest(), row["sha256"])
        self.assertFalse(report["checks"]["gh_attestation_signature_verified"])
        self.assertFalse(report["checks"]["release_ready"])
        self.assertEqual(report["checks"]["owner_installed_acceptance"], "NOT_RUN")

    def test_unsigned_report_never_contains_owner_paths_or_secrets(self):
        report = json.dumps(self.report())
        self.assertNotIn(str(self.root), report)
        self.assertNotIn(str(self.home), report)
        self.assertNotIn("session_file", report)
        self.assertNotIn("private", report)

    def test_forged_source_sha_rejected(self):
        with self.assertRaises(verify.AttestationInputError):
            self.report("a"*40)

    def test_dirty_source_refuses_even_if_package_hashes_match(self):
        (self.root / "Cargo.lock").write_text("tampered fixture")
        with self.assertRaises(verify.AttestationInputError):
            self.report()

    def test_mismatch_candidate_receipt_source_rejected(self):
        self.update("windows",
                    lambda row: row["candidate"].update(source_revision="a"*40))
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_candidate_source_mismatch"
        ):
            self.report()

    def test_mismatch_semwright_pin_rejected(self):
        self.update("macos",
                    lambda row: row["candidate"].update(semwright_revision="f"*40))
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_candidate_source_mismatch"
        ):
            self.report()

    def test_unsigned_source_cannot_claim_distributor_signature(self):
        self.update("linux",
                    lambda row: row["claims"].update(
                        distribution_signature="SIGNED"
                    ))
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_release_claims_not_unsigned"
        ):
            self.report()

    def test_tampered_installer_bytes_refused_by_independent_hash(self):
        binary = next((self.download/"linux").rglob("*.AppImage"))
        binary.write_bytes(binary.read_bytes()+b"tampered")
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_candidate_digest_mismatch"
        ):
            self.report()

    def test_missing_artifact_refused(self):
        binary = next((self.download/"macos").rglob("*.dmg"))
        binary.unlink()
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_bundle_path_ambiguous"
        ):
            self.report()

    def test_unexpected_owner_receipt_outside_candidate_allowlist_refused(self):
        hidden = self.download/"linux"/"owner-native-provider-receipt.json"
        hidden.write_bytes(b"synthetic private payload never signed")
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_unexpected_downloaded_content"
        ):
            self.report()

    def test_unlisted_extra_installer_refused(self):
        additional = self.download/"windows"/"other-setup.exe"
        additional.write_bytes(b"not the original candidate")
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_extra_bundle_not_in_receipt"
        ):
            self.report()

    def test_duplicate_receipt_json_authority_refused(self):
        p = self.receipt("linux")
        raw = p.read_text()
        p.write_text(raw.replace('"schema_version":', '"schema_version": 8, "schema_version":', 1))
        with self.assertRaises(verify.AttestationInputError):
            self.report()

    def test_subject_list_created_exclusively_and_stays_untrusted(self):
        report = self.report()
        verify.write_input_files(self.out, report)
        filelist = (self.out/"verified-subject-paths.txt").read_text().splitlines()
        self.assertEqual(len(filelist), 4)
        self.assertTrue(all(row.startswith("candidates/") for row in filelist))
        self.assertTrue(all((self.home/row).is_file() for row in filelist))
        payload = json.loads((self.out/"verified-candidate-inputs.json").read_text())
        self.assertFalse(payload["checks"]["gh_attestation_signature_verified"])
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_output_exists"
        ):
            verify.write_input_files(self.out, report)

    def test_missing_platform_receipt_refused(self):
        self.receipt("macos").unlink()
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_receipt_count_invalid"
        ):
            self.report()

    def test_filename_with_path_separator_rejected(self):
        self.update("windows",
                    lambda row: row["artifacts"][0].update(filename="../evil.exe"))
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_bundle_filename_invalid"
        ):
            self.report()

    def test_trusted_attestation_job_is_separate_from_pr_build_permissions(self):
        yml = (Path(__file__).resolve().parents[2]
               / ".github/workflows/package-candidate.yml").read_text()
        self.assertIn("attest-candidates:", yml)
        self.assertIn("if: github.event_name == 'workflow_dispatch'", yml)
        self.assertIn("needs: [linux, windows, macos]", yml)
        self.assertIn("id-token: write", yml)
        self.assertIn("attestations: write", yml)
        self.assertIn("artifact-metadata: write", yml)
        self.assertIn("actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6", yml)
        self.assertIn("gh attestation verify", yml)
        self.assertIn("verified-subject-paths.txt", yml)
        self.assertIn("test_candidate_attestation_input.py", yml)
        build_part = yml.split("  attest-candidates:", 1)[0]
        self.assertNotIn("id-token: write", build_part)
        self.assertNotIn("attestations: write", build_part)

    def test_linked_downloaded_package_refused(self):
        original = next((self.download/"windows").rglob("*-setup.exe"))
        link = original.parent/"alternate-setup.exe"
        try:
            link.symlink_to(original)
        except (OSError, NotImplementedError):
            self.skipTest("Unprivileged symlink unavailable")
        with self.assertRaisesRegex(
            verify.AttestationInputError, "attest_download_link_refused"
        ):
            self.report()

    def cargo_receipts(self):
        """Exercise the real producer/capture APIs with inert build inputs."""
        def encoded(value):
            return (json.dumps(value, sort_keys=True, indent=2,
                               ensure_ascii=True) + "\n").encode()

        tree = git(self.root, "rev-parse", self.sha + "^{tree}")
        component = ("cargo:path:apps/desktop/src-tauri/Cargo.toml"
                     "#motionwright-desktop@0.1.0")
        for platform in verify.PLATFORMS:
            arch = "aarch64" if platform == "macos" else "x86_64"
            target = package.CARGO_TARGETS[(platform, arch)]
            folder = self.root / "target" / "attestation-inputs" / platform
            folder.mkdir(parents=True)
            binary = folder / "motionwright-desktop"
            binary.write_bytes(b"INERT CAPTURED BUILD INPUT\n" + bytes(range(128)))
            identity = {
                "path": target + "/release/motionwright-desktop",
                "bytes": binary.stat().st_size,
                "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            }
            index = folder / "cargo-index.json"
            index.write_bytes(encoded({
                "schema": "motionwright.cargo-build-notices/1",
                "source": self.sha, "source_tree": tree,
                "target": target, "root_package": "motionwright-desktop",
                "components": [{"component_id": component,
                                "name": "motionwright-desktop"}],
                "artifact_identities": [identity],
                "artifact_identities_sha256":
                    hashlib.sha256(encoded([identity])).hexdigest(),
                "root_binary_observations": [{
                    "package_id": component, "package": "motionwright-desktop",
                    "target": target, "name": "motionwright-desktop",
                    "unit_id": "3" * 64, **identity,
                }],
                "coverage": {"full_package_inventory": "NOT_VERIFIED"},
            }))
            legacy = json.loads(self.receipt(platform).read_text())
            artifacts = [
                next((self.download / platform).rglob(row["filename"]))
                for row in legacy["artifacts"]
            ]
            receipt = package.make_receipt(
                self.root, self.sha, platform, arch, artifacts,
                cargo_index=index, cargo_binary=binary,
                require_cargo_binding=True,
            )
            self.assertEqual(
                receipt["claims"]["package_contents_verification"],
                "SEPARATE_EXTRACTION_GATE",
            )
            self.receipt(platform).write_text(json.dumps(receipt), encoding="utf8")

    def assert_receipt_change_refused(self, platform, change, code):
        path = self.receipt(platform)
        original = path.read_text()
        try:
            self.update(platform, change)
            with self.assertRaisesRegex(verify.AttestationInputError,
                                        "^" + code + "$"):
                self.report()
        finally:
            path.write_text(original, encoding="utf8")

    def test_real_cargo_producer_preserves_legacy_unsigned_subjects(self):
        legacy = self.report()
        self.cargo_receipts()
        try:
            report = self.report()
        except verify.AttestationInputError as exc:
            self.fail("real producer receipt rejected: " + str(exc))
        self.assertEqual(report, legacy)

    def test_cargo_claim_and_artifact_binding_cannot_be_partial(self):
        self.cargo_receipts()
        cases = [
            ("claim_only", "windows",
             lambda row: row["artifacts"][0].pop("cargo_build")),
            ("binding_only", "windows",
             lambda row: row["claims"].pop("package_contents_verification")),
            ("mixed_linux_rows", "linux",
             lambda row: row["artifacts"][1].pop("cargo_build")),
        ]
        for name, platform, change in cases:
            with self.subTest(case=name):
                self.assert_receipt_change_refused(
                    platform, change, "attest_bundle_metadata_invalid")

    def test_cargo_binding_requires_exact_fields_source_and_target(self):
        self.cargo_receipts()
        for field in ("index_sha256", "source_tree", "target",
                      "root_package", "binary_target"):
            with self.subTest(missing=field):
                self.assert_receipt_change_refused(
                    "windows",
                    lambda row, field=field:
                        row["artifacts"][0]["cargo_build"].pop(field),
                    "attest_cargo_build_invalid",
                )
        replacements = [
            ("index_sha256", "not-a-digest"),
            ("index_sha256", False),
            ("source_tree", "f" * 40),
            ("target", "aarch64-apple-darwin"),
            ("root_package", "foreign"),
            ("binary_target", "foreign"),
        ]
        for field, value in replacements:
            with self.subTest(field=field, value=value):
                self.assert_receipt_change_refused(
                    "windows",
                    lambda row, field=field, value=value:
                        row["artifacts"][0]["cargo_build"].update({field: value}),
                    "attest_cargo_build_invalid",
                )
        for name, change in (
            ("extra_field", lambda row:
                row["artifacts"][0]["cargo_build"].update(extra="not-admitted")),
            ("not_object", lambda row:
                row["artifacts"][0].update(cargo_build=None)),
            ("unsupported_arch", lambda row:
                row["candidate"].update(arch="universal")),
        ):
            with self.subTest(case=name):
                self.assert_receipt_change_refused(
                    "windows", change, "attest_cargo_build_invalid")

    def test_linux_packages_cannot_mix_cargo_bindings(self):
        self.cargo_receipts()
        self.assert_receipt_change_refused(
            "linux",
            lambda row: row["artifacts"][1]["cargo_build"].update(
                index_sha256="0" * 64),
            "attest_cargo_build_mismatch",
        )

    def test_cargo_receipts_cannot_upgrade_unsigned_or_acceptance_claims(self):
        self.cargo_receipts()
        for field, value in (
            ("published", True),
            ("published", 0),
            ("distribution_signature", "SIGNED"),
            ("notarization", "VERIFIED"),
            ("human_install_acceptance", "PASS"),
            ("runtime_renderer_acceptance", "PASS"),
            ("package_contents_verification", "VERIFIED"),
            ("unexpected_authority", "APPROVED"),
        ):
            with self.subTest(field=field, value=value):
                self.assert_receipt_change_refused(
                    "windows",
                    lambda row, field=field, value=value:
                        row["claims"].update({field: value}),
                    "attest_release_claims_not_unsigned",
                )

    def test_cargo_receipts_still_require_downloaded_package_digest(self):
        self.cargo_receipts()
        binary = next((self.download / "windows").rglob("*-setup.exe"))
        binary.write_bytes(binary.read_bytes() + b"tampered")
        with self.assertRaisesRegex(
            verify.AttestationInputError, "^attest_candidate_digest_mismatch$"
        ):
            self.report()



if __name__ == "__main__":
    unittest.main()
