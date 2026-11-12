"""Keep a manual unsigned macOS bundle check separate from acceptance and other OS builds."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/package-candidate.yml"
SCOPE = "macos_bundle"
INTEGRATION_REF = "refs/heads/integrate/phase1-final-composition-20261027"


def job_source(text: str, name: str) -> str:
    match = re.search(r"(?m)^  " + re.escape(name) + r":\n", text)
    if not match:
        raise AssertionError(f"Missing candidate job: {name}")
    next_job = re.search(r"(?m)^  [a-z][a-z0-9-]*:\n", text[match.end():])
    end = match.end() + next_job.start() if next_job else len(text)
    return text[match.start():end]


class IsolatedMacOSBundleScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_scope_is_explicit_manual_choice(self) -> None:
        options = self.workflow.split("      verification_scope:\n", 1)[1]
        options = options.split("      evidence_recipient_spki:\n", 1)[0]
        self.assertEqual(options.count("          - " + SCOPE + "\n"), 1)
        self.assertIn("          - all\n", options)

    def test_only_exact_canonical_first_attempt_uses_non_acceptance_projection(self) -> None:
        policy = job_source(self.workflow, "receipt-policy")
        admission = (
            "seradotcom/motionwright:workflow_dispatch:"
            + INTEGRATION_REF + ":" + SCOPE + ":1)"
        )
        self.assertEqual(policy.count(admission), 1)
        self.assertIn('"macos_bundle": "dev-macos-bundle-source"', policy)
        self.assertIn(
            "any(row[\"status\"] != \"NOT_RUN\" or row[\"independent\"] is not False",
            policy,
        )
        self.assertIn(
            'or creative["v05_release_ready"] is not False', policy
        )
        self.assertIn("python3 tooling/acceptance_policy.py", policy)
        self.assertIn("python3 tooling/creative_status_policy.py", policy)
        self.assertIn("F-G0=NOT_RUN G3=STOP", policy)

    def test_isolated_scope_skips_windows_and_linux_but_keeps_macos_package(self) -> None:
        for name in ("nsis-source-windows", "windows-contracts", "windows", "linux"):
            with self.subTest(job=name):
                body = job_source(self.workflow, name)
                self.assertIn("inputs.verification_scope != '" + SCOPE + "'", body)
        mac = job_source(self.workflow, "macos")
        self.assertIn(
            "github.event_name != 'workflow_dispatch' || "
            "inputs.verification_scope == 'all' || "
            "inputs.verification_scope == 'macos_bundle'",
            mac,
        )
        self.assertIn("    needs: receipt-policy\n", mac)
        self.assertIn("Build unsigned macOS DMG candidate", mac)
        self.assertIn("Read-only DMG mount, copy application and inspect macOS Mach-O", mac)
        self.assertIn("No unsigned application is force-launched", mac)

    def test_only_full_candidate_can_trigger_provenance_job(self) -> None:
        attestation = job_source(self.workflow, "attest-candidates")
        self.assertNotIn(SCOPE, attestation)
        self.assertIn("inputs.verification_scope == 'all'", attestation)
        self.assertIn("github.ref == 'refs/heads/main'", attestation)
        self.assertIn("needs: [linux, windows, macos]", attestation)

    def test_selected_contract_runs_on_every_macos_only_attempt(self) -> None:
        policy = job_source(self.workflow, "receipt-policy")
        self.assertEqual(
            policy.count(
                "python3 -B -m unittest tooling.tests.test_ci_candidate_macos_scope -v"
            ), 1,
        )


if __name__ == "__main__":
    unittest.main()
