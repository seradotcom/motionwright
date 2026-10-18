"""Adversarial, source-bound owner candidate signature verification.

All packages/repos are generated from isolated tiny synthetic local fixtures.
Network, GitHub signing, real owner installation and native effects are NEVER
performed in this unittest suite.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import owner_candidate_provenance as owner
import package_receipt


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        capture_output=True, text=True, timeout=15,
    )
    return result.stdout.strip()


class OwnerCandidateProvenanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-v01-owner-provenance-fixture-")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name).resolve()
        self.root = self.dir / "source"
        self.root.mkdir()
        data = {
            "Cargo.toml": '[workspace.package]\nrepository="https://example.invalid/motionwright"\nversion="0.1.0"\nlicense="AGPL-3.0-or-later"\n',
            "Cargo.lock": "# test-only",
            "SOURCE_LOCK.json": json.dumps({"dependencies": {"semwright": {
                "revision": "b" * 40,
                "repository": "https://example.invalid/semwright",
                "native_sdk_package": "semwright-client",
                "license_observed": "AGPL-3.0", "features_planned": [],
            }}}),
            "LICENSE": "fixture only",
            "THIRD_PARTY_NOTICES.md": "fixture",
            "apps/studio/package-lock.json": '{"lockfileVersion":3}',
            "apps/desktop/src-tauri/tauri.conf.json": "{}",
            "docs/acceptance/requirements-status.json": "{}",
            "docs/acceptance/acceptance-status.json": "{}",
            "docs/security/known-rustsec-advisories.json": "{}",
        }
        for rel, value in data.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        git(self.root, "init", "-q")
        git(self.root, "add", ".")
        git(self.root, "-c", "user.name=fixture",
            "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "fixture")
        self.sha = git(self.root, "rev-parse", "HEAD")

        self.artifact = self.dir / "Motionwright_0.1.0_x64-setup.exe"
        self.artifact.write_bytes(b"synthetic NSIS named bytes without any installer")
        self.receipt = self.dir / "windows-candidate.json"
        manifest = package_receipt.make_receipt(
            self.root, self.sha, "windows", "x86_64", [self.artifact]
        )
        self.receipt.write_text(json.dumps(manifest), encoding="utf-8")
        self.output = self.dir / "owner-evidence.json"
        self.calls: list[tuple[Path, str]] = []

    def fake_attest(self, artifact: Path, source: str) -> bool:
        self.calls.append((artifact, source))
        return True

    def verify(self, **changes):
        params = {
            "root": self.root, "source_sha": self.sha,
            "kind": "nsis", "artifact": self.artifact,
            "receipt_path": self.receipt, "attest": self.fake_attest,
        }
        params.update(changes)
        return owner.validate_owner_candidate(**params)

    def change_receipt(self, edit):
        parsed = json.loads(self.receipt.read_text())
        edit(parsed)
        self.receipt.write_text(json.dumps(parsed), encoding="utf-8")

    def test_exact_installer_sha_and_true_signature_are_both_required(self):
        receipt = self.verify()
        self.assertEqual(self.calls, [(self.artifact, self.sha)])
        self.assertEqual(receipt["schema"], owner.SCHEMA)
        self.assertEqual(receipt["source_sha"], self.sha)
        self.assertEqual(receipt["semwright_source_pin"], "b" * 40)
        self.assertEqual(receipt["github_attestation"]["signer_workflow"],
                         owner.SIGNER_WORKFLOW)
        self.assertEqual(receipt["github_attestation"]["source_digest"], self.sha)
        self.assertEqual(receipt["github_attestation"]["status"],
                         "VERIFIED_SIGSTORE_WORKFLOW_PROVENANCE")
        self.assertEqual(receipt["claims"]["owner_installed_binary"], "NOT_TESTED")
        self.assertEqual(receipt["claims"]["independent_human_acceptance"], "NOT_RUN")
        self.assertFalse(receipt["claims"]["v01_release_authorized"])
        self.assertNotIn(str(self.dir), json.dumps(receipt))

    def test_forged_source_sha_refused_before_signature_call(self):
        with self.assertRaises(owner.OwnerCandidateError):
            self.verify(source_sha="a" * 40)
        self.assertEqual(self.calls, [])

    def test_mismatched_receipt_sha_refused_before_signature_call(self):
        self.change_receipt(lambda row: row["candidate"].update(
            source_revision="a" * 40
        ))
        with self.assertRaisesRegex(owner.OwnerCandidateError,
                                    "owner_receipt_source_mismatch"):
            self.verify()
        self.assertEqual(self.calls, [])

    def test_modified_local_artifact_refused_before_signature(self):
        self.artifact.write_bytes(b"fake tamper")
        with self.assertRaisesRegex(owner.OwnerCandidateError,
                                    "owner_artifact_hash_mismatch"):
            self.verify()
        self.assertEqual(self.calls, [])

    def test_failed_or_missing_github_attestation_never_qualifies(self):
        with self.assertRaisesRegex(owner.OwnerCandidateError,
                                    "owner_gh_attestation_unverified"):
            self.verify(attest=lambda artifact, sha: False)
        self.assertEqual(self.calls, [])

    def test_attestation_backend_exception_is_fail_closed(self):
        def broken(a, s):
            raise RuntimeError("private GH_TOKEN never copy")
        with self.assertRaisesRegex(owner.OwnerCandidateError,
                                    "^owner_gh_attestation_unverified$") as caught:
            self.verify(attest=broken)
        self.assertNotIn("GH_TOKEN", str(caught.exception))

    def test_oversized_receipt_rejected_before_streaming_or_signer(self):
        self.receipt.write_bytes(b"x" * (owner.MAX_RECEIPT_BYTES + 1))
        with self.assertRaisesRegex(owner.OwnerCandidateError,
                                    "owner_receipt_size_invalid"):
            self.verify()
        self.assertEqual(self.calls, [])

    def test_fake_signature_claim_in_receipt_refused(self):
        self.change_receipt(lambda row: row["claims"].update(
            distribution_signature="SIGNED_BY_OWNER"
        ))
        with self.assertRaisesRegex(owner.OwnerCandidateError,
                                    "owner_receipt_release_claims_invalid"):
            self.verify()

    def test_forged_semwright_pin_refused(self):
        self.change_receipt(lambda row: row["candidate"].update(
            semwright_revision="f" * 40
        ))
        with self.assertRaisesRegex(owner.OwnerCandidateError,
                                    "owner_receipt_source_mismatch"):
            self.verify()

    def test_wrong_platform_or_kind_refused(self):
        with self.assertRaises(owner.OwnerCandidateError):
            self.verify(kind="dmg")

    def test_modified_local_checkout_refused_before_signature(self):
        (self.root / "Cargo.lock").write_text("changed")
        with self.assertRaises(owner.OwnerCandidateError):
            self.verify()
        self.assertEqual(self.calls, [])

    def test_duplicate_receipt_json_key_is_rejected(self):
        raw = self.receipt.read_text()
        self.receipt.write_text(raw.replace('"schema_version":',
            '"schema_version": 17, "schema_version":', 1))
        with self.assertRaisesRegex(owner.OwnerCandidateError,
                                    "owner_receipt_unverified"):
            self.verify()
        self.assertEqual(self.calls, [])

    def test_multiple_artifact_rows_invalid_for_windows(self):
        self.change_receipt(lambda row: row["artifacts"].append(
            row["artifacts"][0].copy()
        ))
        with self.assertRaisesRegex(owner.OwnerCandidateError,
                                    "owner_receipt_artifact_count_invalid"):
            self.verify()

    def test_gh_cli_verifier_uses_fixed_repo_signer_source_without_shell(self):
        with patch.object(owner.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0)) as call:
            self.assertTrue(owner.verify_gh_attestation(self.artifact, self.sha))
        argv = call.call_args.args[0]
        self.assertEqual(argv[:3], ["gh", "attestation", "verify"])
        self.assertEqual(argv[3], str(self.artifact))
        self.assertEqual(argv[argv.index("--repo")+1], "seradotcom/motionwright")
        self.assertEqual(argv[argv.index("--signer-workflow")+1],
                         owner.SIGNER_WORKFLOW)
        self.assertEqual(argv[argv.index("--source-digest")+1], self.sha)
        self.assertEqual(call.call_args.kwargs["timeout"], owner.GH_TIMEOUT_SECONDS)
        self.assertFalse(call.call_args.kwargs["check"])
        self.assertEqual(call.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(call.call_args.kwargs["stdout"], subprocess.DEVNULL)

    def test_gh_cli_missing_or_nonzero_returns_false_without_logs(self):
        with patch.object(owner.subprocess, "run",
                          side_effect=FileNotFoundError("private filesystem path")):
            self.assertFalse(owner.verify_gh_attestation(self.artifact, self.sha))
        with patch.object(owner.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 1)):
            self.assertFalse(owner.verify_gh_attestation(self.artifact, self.sha))

    def test_output_created_exclusively_outside_source_without_human_pass(self):
        clean = self.verify()
        with patch.object(owner, "validate_owner_candidate", return_value=clean):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = owner.main([
                    "--source-sha", self.sha, "--bundle-kind", "nsis",
                    "--artifact", str(self.artifact),
                    "--candidate-receipt", str(self.receipt),
                    "--output", str(self.output),
                ], root=self.root)
            self.assertEqual(code, 0)
        result = json.loads(self.output.read_text())
        self.assertFalse(result["claims"]["v01_release_authorized"])
        self.assertEqual(result["claims"]["owner_broker_session"], "NOT_TESTED")
        self.assertNotIn(str(self.artifact), stdout.getvalue())
        previous = self.output.read_bytes()
        with patch.object(owner, "validate_owner_candidate", return_value=clean):
            with contextlib.redirect_stderr(io.StringIO()):
                code = owner.main([
                    "--source-sha", self.sha, "--bundle-kind", "nsis",
                    "--artifact", str(self.artifact),
                    "--candidate-receipt", str(self.receipt),
                    "--output", str(self.output),
                ], root=self.root)
        self.assertEqual(code, 2)
        self.assertEqual(self.output.read_bytes(), previous)

    def test_output_inside_public_git_checkout_refused(self):
        fake = self.verify()
        dest = self.root / "untracked-review-evidence.json"
        with patch.object(owner, "validate_owner_candidate", return_value=fake):
            with contextlib.redirect_stderr(io.StringIO()):
                code = owner.main([
                    "--source-sha", self.sha, "--bundle-kind", "nsis",
                    "--artifact", str(self.artifact),
                    "--candidate-receipt", str(self.receipt),
                    "--output", str(dest),
                ], root=self.root)
        self.assertEqual(code, 2)
        self.assertFalse(dest.exists())

    def test_source_changes_after_gh_verification_fail_closed(self):
        def dirty_after_cryptographic_verification(artifact, sha):
            (self.root / "Cargo.lock").write_text("mutated")
            return True
        with self.assertRaisesRegex(owner.OwnerCandidateError,
                                    "owner_checkout_changed"):
            self.verify(attest=dirty_after_cryptographic_verification)

    def test_package_changed_while_gh_checks_signature_is_rejected(self):
        def changed_bytes_during_attestation(artifact, sha):
            artifact.write_bytes(b"replacement signed candidate")
            return True
        with self.assertRaisesRegex(
            owner.OwnerCandidateError, "owner_input_changed_during_attestation"
        ):
            self.verify(attest=changed_bytes_during_attestation)

    def test_receipt_changed_while_gh_checks_signature_is_rejected(self):
        def replaced_receipt_during_attestation(artifact, sha):
            self.change_receipt(lambda row: row["claims"].update(
                published=True
            ))
            return True
        with self.assertRaisesRegex(
            owner.OwnerCandidateError, "owner_input_changed_during_attestation"
        ):
            self.verify(attest=replaced_receipt_during_attestation)

    def test_newline_in_artifact_filename_rejected_before_attestation(self):
        self.change_receipt(lambda row: row["artifacts"][0].update(
            filename="Motionwright_0.1.0_x64-setup.exe\\n"
        ))
        with self.assertRaisesRegex(
            owner.OwnerCandidateError, "owner_receipt_artifact_name_invalid"
        ):
            self.verify()

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_real_ntfs_junction_as_owner_installer_parent_refused(self):
        folder = self.dir / "junction-real-dir"
        folder.mkdir()
        pkg = folder / self.artifact.name
        pkg.write_bytes(self.artifact.read_bytes())
        junction = self.dir / "alias-to-package"
        try:
            creation = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J",
                 str(junction), str(folder)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("Junction creation not permitted")
        if creation.returncode != 0:
            self.skipTest("Junction creation not permitted")
        try:
            with self.assertRaisesRegex(owner.OwnerCandidateError,
                                        "owner_artifact_unavailable_or_changed"):
                self.verify(artifact=junction / pkg.name)
            self.assertEqual(self.calls, [])
        finally:
            junction.rmdir()

    def invoke(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = owner.main([
                "--source-sha", self.sha,
                "--bundle-kind", "nsis",
                "--artifact", str(self.artifact),
                "--candidate-receipt", str(self.receipt),
                "--output", str(self.output),
            ], root=self.root)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_uncertain_result(self, result):
        code, stdout, stderr = result
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(
            stderr,
            "owner-candidate-provenance=REFUSED "
            "code=manifest_output_commit_uncertain\n",
        )

    def check_failure_after_publication(self, *, replace_output):
        clean = self.verify()
        real_link = os.link
        retained = []
        replacement = self.dir / "external-replacement.json"
        if replace_output:
            replacement.write_bytes(b"independent replacement evidence")

        def link_then_fail(*args, **kwargs):
            real_link(*args, **kwargs)
            if replace_output:
                replacement.replace(self.output)
            info = self.output.stat()
            retained.append((
                self.output.read_bytes(),
                (info.st_dev, info.st_ino),
            ))
            raise OSError("private post-publication diagnostic")

        with (
            patch.object(owner, "validate_owner_candidate",
                         return_value=clean) as validate,
            patch.object(owner, "write_new_manifest",
                         wraps=owner.write_new_manifest) as publish,
            patch.object(owner, "_discard_new_output_on_source_change",
                         wraps=owner._discard_new_output_on_source_change) as discard,
            patch.object(os, "link", side_effect=link_then_fail) as link,
        ):
            result = self.invoke()

        self.assert_uncertain_result(result)
        validate.assert_called_once()
        publish.assert_called_once()
        link.assert_called_once()
        discard.assert_not_called()
        self.assertEqual(len(retained), 1)
        contents, identity = retained[0]
        info = self.output.stat()
        self.assertEqual(self.output.read_bytes(), contents)
        self.assertEqual((info.st_dev, info.st_ino), identity)

    def test_error_after_publication_preserves_output_without_retry(self):
        self.check_failure_after_publication(replace_output=False)

    def test_external_replacement_after_publication_is_untouched(self):
        self.check_failure_after_publication(replace_output=True)

    def test_checkout_change_after_publication_preserves_uncertain_output(self):
        clean = self.verify()
        real_write = owner.write_new_manifest
        retained = []

        def write_then_change_source(root, output, report):
            real_write(root, output, report)
            retained.append(self.output.read_bytes())
            (root / "Cargo.lock").write_text(
                "changed after publication", encoding="utf-8"
            )

        with (
            patch.object(owner, "validate_owner_candidate",
                         return_value=clean) as validate,
            patch.object(owner, "write_new_manifest",
                         side_effect=write_then_change_source) as publish,
            patch.object(owner, "_discard_new_output_on_source_change",
                         wraps=owner._discard_new_output_on_source_change) as discard,
        ):
            result = self.invoke()

        self.assert_uncertain_result(result)
        validate.assert_called_once()
        publish.assert_called_once()
        discard.assert_called_once()
        self.assertEqual(len(retained), 1)
        self.assertEqual(self.output.read_bytes(), retained[0])

    def test_other_manifest_failure_remains_redacted(self):
        clean = self.verify()
        with (
            patch.object(owner, "validate_owner_candidate",
                         return_value=clean),
            patch.object(
                owner, "write_new_manifest",
                side_effect=owner.ManifestError(
                    "private output path or backend diagnostic"
                ),
            ) as publish,
        ):
            code, stdout, stderr = self.invoke()

        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(
            stderr,
            "owner-candidate-provenance=REFUSED code=owner_output_refused\n",
        )
        publish.assert_called_once()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
