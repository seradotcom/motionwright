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
            + INTEGRATION_REF + ":" + SCOPE + ":1|"
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

    def test_capture_uses_the_same_real_arm64_toolchain_cargo(self) -> None:
        mac = job_source(self.workflow, "macos")
        self.assertIn('test "$(uname -m)" = \'arm64\'', mac)
        self.assertIn('cargo_executable="$(rustup which cargo)"', mac)
        self.assertIn('--expected-source "$GITHUB_SHA" --cargo "$cargo_executable"', mac)
        self.assertIn('-- "$cargo_executable" build --locked --release --bins', mac)
        self.assertIn("--target aarch64-apple-darwin", mac)
        self.assertNotIn("-- cargo build --locked", mac)

    def test_common_upload_keeps_diagnostics_outside_attestation_subject_inputs(self) -> None:
        import sys
        sys.path.insert(0, str(ROOT / "tooling"))
        import candidate_attestation_input as attestation
        self.assertEqual(Path(attestation.__file__).resolve(),
                         (ROOT / "tooling/candidate_attestation_input.py").resolve())
        mac = job_source(self.workflow, "macos")
        upload = mac.split("          name: motionwright-macos-candidate\n", 1)[1]
        upload = upload.split("          if-no-files-found:", 1)[0]
        evidence = {Path(line.strip()).name for line in upload.splitlines()
                    if line.strip().startswith("ci-evidence/")}
        self.assertEqual(evidence, attestation.KNOWN_EVIDENCE_FILES["macos"])
        originals = mac.split("      - name: Preserve original isolated macOS dependency records\n", 1)[1]
        self.assertIn("inputs.verification_scope == 'macos_bundle'", originals)
        self.assertIn("            ci-evidence/macos-cargo-binding.json\n", originals)
        self.assertNotIn("            target/", originals)
        self.assertNotIn("frontend/archives", originals)

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
                "python3 -B -m unittest tooling.tests.test_ci_candidate_macos_scope "
                "tooling.tests.test_ci_candidate_linux_scope -v"
            ), 1,
        )


if __name__ == "__main__":
    unittest.main()
