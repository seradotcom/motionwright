"""Bound a source-only Linux package build without claiming installed first-use acceptance."""

from __future__ import annotations

from pathlib import Path
import unittest

from tooling.tests.test_ci_candidate_macos_scope import job_source


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/package-candidate.yml"
SOURCE_SCOPE = "linux_bundle"


class IsolatedLinuxBundleScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_choice_is_explicit_and_retains_full_scope(self):
        choices = self.workflow.split("      verification_scope:\n", 1)[1]
        choices = choices.split("      evidence_recipient_spki:\n", 1)[0]
        self.assertEqual(choices.count("          - linux_bundle\n"), 1)
        self.assertIn("          - all\n", choices)

    def test_only_first_exact_development_attempt_defers_formal_ledger(self):
        policy = job_source(self.workflow, "receipt-policy")
        self.assertEqual(policy.count(
            "seradotcom/motionwright:workflow_dispatch:refs/heads/"
            "integrate/phase1-final-composition-20261027:linux_bundle:1)"
        ), 1)
        self.assertIn('"linux_bundle": "dev-linux-bundle-source"', policy)
        self.assertIn('len(required) != 208', policy)
        self.assertIn('row["status"] != "NOT_RUN"', policy)
        self.assertIn('row["independent"] is not False', policy)
        self.assertIn('creative["v05_release_ready"] is not False', policy)
        self.assertIn("python3 tooling/acceptance_policy.py", policy)
        self.assertIn("python3 tooling/creative_status_policy.py", policy)
        self.assertIn("F-G0=NOT_RUN G3=STOP", policy)

    def test_only_linux_unsigned_package_job_is_selected(self):
        for name in ("nsis-source-windows", "windows-contracts", "windows"):
            with self.subTest(name=name):
                self.assertIn("inputs.verification_scope != 'linux_bundle'",
                              job_source(self.workflow, name))
        macos = job_source(self.workflow, "macos")
        self.assertNotIn("inputs.verification_scope == 'linux_bundle'", macos)
        linux = job_source(self.workflow, "linux")
        self.assertNotIn("inputs.verification_scope != 'linux_bundle'", linux)
        self.assertIn("    needs: receipt-policy\n", linux)
        self.assertIn("Build unsigned Linux candidate bundles", linux)
        self.assertIn("Verify actual Linux candidate compiled the reviewed local glib source", linux)
        self.assertIn("Record exact Linux candidate artifacts", linux)
        self.assertIn("Inspect extracted Linux packages and launch a rootless AppImage window", linux)
        installed = job_source(self.workflow, "linux-first-use-installed")
        self.assertIn("inputs.verification_scope == 'linux_first_use'", installed)

    def test_attestation_remains_full_trusted_scope_only(self):
        attestation = job_source(self.workflow, "attest-candidates")
        self.assertNotIn(SOURCE_SCOPE, attestation)
        self.assertIn("inputs.verification_scope == 'all'", attestation)
        self.assertIn("github.ref == 'refs/heads/main'", attestation)

    def test_closed_source_guards_run_before_selected_package(self):
        policy = job_source(self.workflow, "receipt-policy")
        self.assertIn(
            "python3 -B -m unittest tooling.tests.test_ci_candidate_macos_scope "
            "tooling.tests.test_ci_candidate_linux_scope -v", policy
        )
        self.assertIn("python3 tooling/source_policy.py", policy)
        self.assertIn("python3 tooling/public_surface_policy.py", policy)


if __name__ == "__main__":
    unittest.main()
