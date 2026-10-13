"""Synthetic source-only v0.1 release-blocker regressions.

They explicitly do NOT simulate real human review or accept GitHub CI
as proof that a candidate is safe for stable publication.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import v01_release_readiness as gate  # noqa: E402


SHA = "a" * 40
PIN = "b" * 40


class ReadinessGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-v01-gate-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.root.joinpath("apps/desktop/src-tauri").mkdir(parents=True)
        self.root.joinpath("docs/acceptance").mkdir(parents=True)
        self.root.joinpath("docs/security").mkdir(parents=True)
        self.workspace = {
            "workspace": {
                "package": {
                    "version": "0.1.0",
                    "license": "AGPL-3.0-or-later",
                    "repository": "https://github.com/seradotcom/motionwright",
                },
                "dependencies": {
                    "semwright-native-sdk": {
                        "git": "https://github.com/seradotcom/semwright",
                        "rev": PIN,
                        "package": "semwright-native-sdk",
                    }
                },
            },
        }
        self.desktop = {
            "version": "0.1.0",
            "identifier": "com.seradotcom.motionwright",
        }
        self.lock = {
            "dependencies": {
                "semwright": {
                    "version": "1.0.0", "revision": PIN,
                    "repository": "https://github.com/seradotcom/semwright",
                }
            }
        }
        self.requirements = {
            "requirements": [
                {
                    "id": name,
                    "implementation_assessment": "PARTIAL_OR_GAP",
                    "verification_status": "CI_PARTIAL",
                    "evidence": [],
                }
                for name in sorted(gate.REQUIREMENT_IDS)
            ]
        }
        self.acceptance = {
            "cases": [
                {
                    "id": name, "status": "NOT_RUN",
                    "independent": False, "evidence": [],
                }
                for name in sorted(gate.ACCEPTANCE_IDS)
            ]
        }
        self.security = {
            "schema": "motionwright.security-known-advisories.v1",
            "advisories": [
                {"id": "RUSTSEC-2024-0429", "status": "OPEN_UPSTREAM_BLOCKED"},
                {"id": "RUSTSEC-2024-0370", "status": "OPEN_UPSTREAM_TRANSITIVE"},
            ],
        }
        self.store()

    def store(self):
        # Preserve real TOML semantics while using only synthetic fixtures.
        lines = [
            "[workspace.package]",
            f'version = "{self.workspace["workspace"]["package"]["version"]}"',
            f'license = "{self.workspace["workspace"]["package"]["license"]}"',
            f'repository = "{self.workspace["workspace"]["package"]["repository"]}"',
            "[workspace.dependencies]",
        ]
        dep = self.workspace["workspace"]["dependencies"]["semwright-native-sdk"]
        values = ", ".join(f'{k} = "{v}"' for k, v in dep.items())
        lines.append("semwright-native-sdk = { " + values + " }")
        (self.root / "Cargo.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        files = {
            "apps/desktop/src-tauri/tauri.conf.json": self.desktop,
            "SOURCE_LOCK.json": self.lock,
            "docs/acceptance/requirements-status.json": self.requirements,
            "docs/acceptance/acceptance-status.json": self.acceptance,
            "docs/security/known-rustsec-advisories.json": self.security,
        }
        for name, value in files.items():
            (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def committed_checkout(self) -> str:
        """Use actual Git objects rather than stubbing the source-binding check."""
        def git(*args: str) -> str:
            proc = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True, text=True, encoding="utf-8", check=True,
            )
            return proc.stdout.strip()

        git("init", "-q")
        git("config", "core.autocrlf", "false")
        (self.root / "README.md").write_text("original tracked source\n")
        git("add", "--", "Cargo.toml", "apps/desktop/src-tauri/tauri.conf.json",
            "SOURCE_LOCK.json", "docs/acceptance/requirements-status.json",
            "docs/acceptance/acceptance-status.json",
            "docs/security/known-rustsec-advisories.json", "README.md")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-qm", "v0.1 fixture")
        return git("rev-parse", "HEAD")

    def summary(self) -> dict:
        return gate.summarize(self.root, SHA)

    def rejects(self, code: str):
        with self.assertRaises(gate.ReadinessError) as raised:
            self.summary()
        self.assertEqual(raised.exception.code, code)

    def test_realistic_blocked_report_never_claims_release(self):
        report = self.summary()
        self.assertEqual(report["schema"], "motionwright.v01-release-readiness/1")
        self.assertEqual(report["status"], "BLOCKED_PENDING_EXTERNAL_EVIDENCE")
        self.assertEqual(report["source_sha"], SHA)
        self.assertEqual(report["semwright_native_sdk_source_pin"], PIN)
        self.assertEqual(report["requirements"]["total"], 208)
        self.assertEqual(report["requirements"]["fully_verified"], 0)
        self.assertEqual(report["private_acceptance"]["total"], 60)
        self.assertEqual(report["private_acceptance"]["status_counts"], {"NOT_RUN": 60})
        self.assertEqual(len(report["reviewed_dependency_warnings"]), 2)
        self.assertFalse(report["claims"]["stable_release_authorized"])
        self.assertFalse(report["claims"]["automatic_publish"])
        self.assertIn("private_acceptance_not_complete", report["blockers"])

    def test_tampered_requirement_count_rejected(self):
        self.requirements["requirements"].pop()
        self.store()
        self.rejects("requirements_invalid")

    def test_repeated_requirement_rejected(self):
        self.requirements["requirements"][0]["id"] = self.requirements["requirements"][1]["id"]
        self.store()
        self.rejects("requirements_invalid")

    def test_fake_complete_requirement_without_evidence_rejected(self):
        self.requirements["requirements"][0]["implementation_assessment"] = "VERIFIED"
        self.requirements["requirements"][0]["verification_status"] = "VERIFIED"
        self.store()
        self.rejects("unsupported_requirement_verified")

    def test_invalid_requirement_evidence_type_rejected(self):
        self.requirements["requirements"][0]["evidence"] = "fake pointer"
        self.store()
        self.rejects("requirement_evidence_invalid")

    def test_missing_acceptance_case_rejected(self):
        self.acceptance["cases"].pop()
        self.store()
        self.rejects("acceptance_invalid")

    def test_forged_product_pass_without_review_rejected(self):
        self.acceptance["cases"][0]["status"] = "PASS"
        self.store()
        self.rejects("unreviewed_acceptance_pass")

    def test_claimed_independent_pass_without_evidence_rejected(self):
        case = self.acceptance["cases"][0]
        case.update(status="PASS", independent=True)
        self.store()
        self.rejects("unreviewed_acceptance_pass")

    def test_all_synthetic_passes_still_require_external_attestation(self):
        for entry in self.acceptance["cases"]:
            entry.update(status="PASS", independent=True,
                         evidence=[{"fake": "not authenticated by this tool"}])
        for entry in self.requirements["requirements"]:
            entry.update(implementation_assessment="VERIFIED",
                         verification_status="VERIFIED",
                         evidence=["ci-evidence/placeholder.json"])
        self.security["advisories"] = []
        self.store()
        report = self.summary()
        self.assertEqual(report["requirements"]["fully_verified"], 208)
        self.assertEqual(report["private_acceptance"]["status_counts"], {"PASS": 60})
        self.assertEqual(report["reviewed_dependency_warnings"], [])
        self.assertFalse(report["claims"]["stable_release_authorized"])
        self.assertIn("release_authorization_requires_independent_manual_review",
                      report["blockers"])

    def test_open_security_warning_remains_a_release_blocker(self):
        self.assertIn("reviewed_upstream_security_warnings_open", self.summary()["blockers"])

    def test_malformed_security_schema_rejected(self):
        self.security["schema"] = "unknown"
        self.store()
        self.rejects("security_policy_invalid")

    def test_closed_advisory_without_proven_update_rejected(self):
        self.security["advisories"][0]["status"] = "RESOLVED"
        self.store()
        self.rejects("security_policy_invalid")

    def test_duplicate_security_advisory_rejected(self):
        self.security["advisories"][0]["id"] = self.security["advisories"][1]["id"]
        self.store()
        self.rejects("security_policy_invalid")


    def test_mismatched_semwright_sdk_source_pin_rejected(self):
        self.workspace["workspace"]["dependencies"]["semwright-native-sdk"]["rev"] = "c" * 40
        self.store()
        self.rejects("native_sdk_pin_mismatch")

    def test_bad_source_identifier_rejected(self):
        with self.assertRaises(gate.ReadinessError) as raised:
            gate.summarize(self.root, "main")
        self.assertEqual(raised.exception.code, "source_sha_invalid")

    def test_release_version_mismatch_rejected(self):
        self.desktop["version"] = "0.2.0"
        self.store()
        self.rejects("version_mismatch")

    def test_repo_identity_or_license_drift_rejected(self):
        self.workspace["workspace"]["package"]["license"] = "MIT"
        self.store()
        self.rejects("workspace_identity_invalid")

    def test_duplicate_security_json_keys_rejected(self):
        (self.root / "docs/security/known-rustsec-advisories.json").write_text(
            '{"schema":"motionwright.security-known-advisories.v1",'
            '"schema":"fraud","advisories":[]}', encoding="utf-8"
        )
        self.rejects("duplicate_json_field")

    def test_empty_or_unexpected_case_status_fails_closed(self):
        self.acceptance["cases"][0]["status"] = "AUTO_PASS"
        self.store()
        self.rejects("acceptance_status_invalid")

    def test_report_contains_only_aggregate_counts_and_public_source(self):
        report = self.summary()
        printed = json.dumps(report)
        self.assertNotIn(str(self.root), printed)
        self.assertNotIn("ACC-001", printed)
        self.assertNotIn("CR-FOUND-01", printed)
        self.assertNotIn("owner_token", printed)
        self.assertEqual(report["license"], "AGPL-3.0-or-later")

    def test_created_receipt_cannot_overwrite_prior_evidence(self):
        report = self.summary()
        target = self.root / "release-check.json"
        gate.write_receipt(target, report)
        before = target.read_bytes()
        with self.assertRaises(gate.ReadinessError) as raised:
            gate.write_receipt(target, report)
        self.assertEqual(raised.exception.code, "receipt_write_refused")
        self.assertEqual(before, target.read_bytes())

    def test_cli_is_quiet_and_source_bound_without_output(self):
        stdout = io.StringIO()
        with patch.object(gate, "ROOT", self.root):
            with contextlib.redirect_stdout(stdout):
                result = gate.main(["--source-sha", SHA, "--expect-blocked"])
        self.assertEqual(result, 0)
        self.assertIn("v01-release-readiness=BLOCKED_PENDING_EXTERNAL_EVIDENCE",
                      stdout.getvalue())
        self.assertNotIn(str(self.root), stdout.getvalue())

    def test_cli_report_output_is_explicit_and_non_destructive(self):
        output = self.root / "ci-readiness.json"
        with patch.object(gate, "ROOT", self.root):
            with contextlib.redirect_stdout(io.StringIO()):
                result = gate.main([
                    "--source-sha", SHA,
                    "--output", str(output),
                    "--expect-blocked",
                ])
        self.assertEqual(result, 0)
        self.assertFalse(json.loads(output.read_text())["claims"]["stable_release_authorized"])
        self.assertEqual(self.summary()["status"], "BLOCKED_PENDING_EXTERNAL_EVIDENCE")

    def test_verified_checkout_exact_head_and_unrelated_receipts(self):
        sha = self.committed_checkout()
        (self.root / "ci-evidence").mkdir()
        (self.root / "ci-evidence" / "untracked.json").write_text("{}")
        target = self.root / "ci-evidence" / "report.json"
        with patch.object(gate, "ROOT", self.root):
            with contextlib.redirect_stdout(io.StringIO()):
                result = gate.main(["--source-sha", sha, "--verify-checkout",
                                    "--expect-blocked", "--output", str(target)])
        self.assertEqual(result, 0)
        report = json.loads(target.read_text())
        self.assertEqual(report["source_sha"], sha)
        self.assertTrue(report["checkout_binding"]
                        ["local_git_head_and_tracked_inputs_checked"])
        self.assertFalse(report["checkout_binding"]["remote_commit_attestation"])
        self.assertFalse(report["claims"]["stable_release_authorized"])

    def test_verified_checkout_rejects_forged_commit(self):
        self.committed_checkout()
        with self.assertRaises(gate.ReadinessError) as raised:
            gate.verify_local_checkout(self.root, SHA)
        self.assertEqual(raised.exception.code, "source_checkout_sha_mismatch")

    def test_verified_checkout_rejects_dirty_source_metadata(self):
        sha = self.committed_checkout()
        self.requirements["requirements"][0]["verification_status"] = "VERIFIED"
        self.store()
        with self.assertRaises(gate.ReadinessError) as raised:
            gate.verify_local_checkout(self.root, sha)
        self.assertEqual(raised.exception.code, "source_metadata_dirty")

    def test_verified_checkout_rejects_unrelated_tracked_changes(self):
        sha = self.committed_checkout()
        (self.root / "README.md").write_text("new tracked source\\n")
        with self.assertRaises(gate.ReadinessError) as raised:
            gate.verify_local_checkout(self.root, sha)
        self.assertEqual(raised.exception.code, "source_metadata_dirty")

    def test_verified_checkout_rejects_linked_source_file(self):
        sha = self.committed_checkout()
        target = self.root / "SOURCE_LOCK.json"
        actual_is_symlink = Path.is_symlink

        def pretend_linked(path: Path) -> bool:
            return path == target or actual_is_symlink(path)

        # No OS-specific privilege to create symlinks is needed: simulate a
        # committed metadata link whose target bytes are outside Git's control.
        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=pretend_linked):
            with self.assertRaises(gate.ReadinessError) as raised:
                gate.verify_local_checkout(self.root, sha)
        self.assertEqual(raised.exception.code, "source_metadata_link_refused")

    def test_verified_checkout_rejects_linked_parent_directory(self):
        sha = self.committed_checkout()
        target = self.root / "docs" / "acceptance"
        actual_is_symlink = Path.is_symlink

        def pretend_linked(path: Path) -> bool:
            return path == target or actual_is_symlink(path)

        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=pretend_linked):
            with self.assertRaises(gate.ReadinessError) as raised:
                gate.verify_local_checkout(self.root, sha)
        self.assertEqual(raised.exception.code, "source_metadata_link_refused")

    def test_verified_checkout_rejects_windows_reparse_parent(self):
        sha = self.committed_checkout()
        target = self.root / "docs" / "acceptance"
        actual_lstat = Path.lstat

        def synthetic_junction(path: Path):
            result = actual_lstat(path)
            if path == target:
                # Inject NTFS reparse attributes without requiring privileges
                # or a Windows-specific Python runtime in hosted CI.
                from types import SimpleNamespace
                return SimpleNamespace(
                    st_mode=result.st_mode, st_file_attributes=0x400
                )
            return result

        with patch.object(Path, "lstat", autospec=True,
                          side_effect=synthetic_junction):
            with self.assertRaises(gate.ReadinessError) as raised:
                gate.verify_local_checkout(self.root, sha)
        self.assertEqual(raised.exception.code, "source_metadata_link_refused")

    def test_verified_checkout_rejects_missing_regular_source(self):
        sha = self.committed_checkout()
        (self.root / "SOURCE_LOCK.json").unlink()
        with self.assertRaises(gate.ReadinessError) as raised:
            gate.verify_local_checkout(self.root, sha)
        self.assertEqual(raised.exception.code, "source_metadata_not_regular_file")

    def test_verified_checkout_rejects_untracked_metadata(self):
        sha = self.committed_checkout()
        subprocess.run(
            ["git", "-C", str(self.root), "rm", "-q", "--cached",
             "SOURCE_LOCK.json"], capture_output=True, check=True,
        )
        with self.assertRaises(gate.ReadinessError) as raised:
            gate.verify_local_checkout(self.root, sha)
        self.assertEqual(raised.exception.code, "source_metadata_not_tracked")

    def test_verified_checkout_rejects_parent_git_repository(self):
        sha = self.committed_checkout()
        nested = self.root / "nested"
        nested.mkdir()
        with self.assertRaises(gate.ReadinessError) as raised:
            gate.verify_local_checkout(nested, sha)
        self.assertEqual(raised.exception.code, "source_checkout_root_mismatch")

    def test_verified_checkout_fails_closed_without_git(self):
        with patch.object(gate.subprocess, "run",
                          side_effect=FileNotFoundError("private path")):
            with self.assertRaises(gate.ReadinessError) as raised:
                gate.verify_local_checkout(self.root, SHA)
        self.assertEqual(raised.exception.code, "source_checkout_unavailable")

    def test_verified_checkout_rechecks_after_source_read(self):
        sha = self.committed_checkout()
        target = self.root / "forbidden-output.json"
        old_summarize = gate.summarize

        def mutate_after_source_read(root: Path, tested_sha: str) -> dict:
            report = old_summarize(root, tested_sha)
            (root / "SOURCE_LOCK.json").write_text("{}")
            return report

        with patch.object(gate, "ROOT", self.root), patch.object(
            gate, "summarize", side_effect=mutate_after_source_read
        ):
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                result = gate.main(["--source-sha", sha, "--verify-checkout",
                                    "--output", str(target)])
        self.assertEqual(result, 2)
        self.assertIn("source_metadata_dirty", stderr.getvalue())
        self.assertNotIn(str(self.root), stderr.getvalue())
        self.assertFalse(target.exists())

    def test_cli_bad_input_emits_stable_code_never_private_json(self):
        stderr = io.StringIO()
        with patch.object(gate, "ROOT", self.root):
            with contextlib.redirect_stderr(stderr):
                code = gate.main(["--source-sha", "not-a-commit"])
        self.assertEqual(code, 2)
        self.assertIn("source_sha_invalid", stderr.getvalue())
        self.assertNotIn(str(self.root), stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
