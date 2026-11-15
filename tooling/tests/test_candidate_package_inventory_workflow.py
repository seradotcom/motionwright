"""Workflow contracts for detached package observations; no package effects."""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import unittest

from tooling.tests.test_ci_candidate_macos_scope import job_source

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/package-candidate.yml"


def step(body, title):
    selected = body.split("      - name: " + title + "\n", 1)[1]
    return selected.split("\n      - ", 1)[0]


class PackageInventoryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.installed = job_source(cls.workflow, "linux-first-use-installed")
        cls.linux = job_source(cls.workflow, "linux")
        cls.windows = job_source(cls.workflow, "windows")

    def test_source_filters_cover_every_new_owner_reader_and_control(self):
        paths = ("tooling/candidate_package_inventory.py", "tooling/candidate_package_extraction.py",
                 "tooling/startup_capture_core.py", "tooling/tests/test_candidate_package_inventory.py",
                 "tooling/tests/test_candidate_package_extraction.py",
                 "tooling/tests/test_candidate_package_inventory_workflow.py")
        for event, boundary in (("pull_request", "\n  push:\n"), ("push", "\npermissions:\n")):
            with self.subTest(event=event):
                body = self.workflow.split("\n  " + event + ":\n", 1)[1].split(boundary, 1)[0]
                patterns = [line.strip()[3:-1] for line in body.splitlines()
                            if line.startswith('      - "') and line.endswith('"')]
                self.assertEqual(len(patterns), len(set(patterns)))
                self.assertEqual([p for p in paths if not any(fnmatchcase(p, q) for q in patterns)], [])

    def test_complete_linux_kernel_controls_gate_any_new_package_extraction(self):
        policy = job_source(self.workflow, "receipt-policy")
        source = step(policy, "Require complete package inventory and Linux ownership source controls")
        for name in ("test_candidate_package_extraction.py", "test_candidate_package_inventory.py",
                     "test_candidate_package_inventory_workflow.py"):
            self.assertIn("--pattern " + name, source)
        self.assertIn("--mode discovery --require-complete --timeout 60", source)
        self.assertIn('(12,33,8)', source)
        self.assertIn('counts["tests_run"]==counts["passed"]==expected', source)
        self.assertIn('"skipped","expected_failures","unexpected_successes"', source)
        self.assertIn("extraction_effects=0 full_inventory=NOT_VERIFIED G3=STOP", source)
        self.assertNotIn("|| true", source)
        for body in (self.installed, self.linux):
            self.assertIn("receipt-policy", body.split("    steps:\n", 1)[0])

    def test_installed_linux_replaces_each_extraction_once_and_keeps_control_members(self):
        body = step(self.installed, "One accessible first-use attempt and a read-only cold relaunch")
        self.assertEqual(body.count('package_inventory deb "$deb"'), 1)
        self.assertEqual(body.count('package_inventory appimage "$appimage"'), 1)
        self.assertEqual(body.count("tooling/candidate_package_inventory.py extract"), 1)
        self.assertIn('--work "$work/$kind-inventory"', body)
        self.assertIn('deb_payload="$work/deb-inventory/payload"', body)
        self.assertIn('extracted="$work/appimage-inventory/squashfs-root"', body)
        self.assertNotIn("dpkg-deb --extract", body)
        self.assertNotIn("--appimage-extract", body)
        self.assertLess(body.index('package_inventory appimage "$appimage"'),
                        body.index("tooling/ci_linux_first_use.py installed"))

    def test_generic_linux_keeps_legacy_flow_outside_its_existing_admitted_scope(self):
        body = step(self.linux, "Inspect extracted Linux packages and launch a rootless AppImage window")
        admission = 'if [[ "$GITHUB_EVENT_NAME" == \'workflow_dispatch\' && "$MW_LINUX_CANDIDATE_SCOPE" == \'linux_bundle\' ]]; then'
        self.assertEqual(body.count(admission), 2)
        deb = body.split(admission, 1)[1].split("          fi\n", 1)[0]
        admitted, legacy = deb.split("          else\n", 1)
        self.assertEqual(admitted.count('package_inventory deb "$deb"'), 1)
        self.assertNotIn("dpkg-deb", admitted)
        self.assertEqual(legacy.count('dpkg-deb --extract "$deb" "$work/deb"'), 1)
        image = body.split(admission, 2)[2]
        admitted, legacy = image.split("          else\n", 1)
        self.assertEqual(admitted.count('package_inventory appimage "$appimage"'), 1)
        self.assertNotIn("--appimage-extract", admitted)
        self.assertEqual(legacy.count("--appimage-extract"), 1)

    def test_consumer_uses_original_producer_seal_and_live_roots_without_resealing(self):
        for body, producer, consumer in (
                (self.installed, "first_use_package_inventory",
                 "Verify both extracted installed packages against the original Cargo capture"),
                (self.linux, "linux_package_inventory",
                 "Verify both extracted Linux packages against the original Cargo capture")):
            with self.subTest(producer=producer):
                readback = step(body, consumer)
                self.assertIn("steps." + producer + ".outputs.deb_inventory_sha256", readback)
                self.assertIn("steps." + producer + ".outputs.appimage_inventory_sha256", readback)
                self.assertIn('prefix+".producer.json"', readback)
                self.assertIn('seal["sha256"]==expected', readback)
                self.assertIn('expected_sha256=expected,payload=work/(kind+"-inventory")/leaf', readback)
                self.assertIn('prefix+".consumer.json"', readback)
                self.assertNotIn("hashlib", readback)
                self.assertNotIn("Get-FileHash", readback)
                self.assertNotIn('capture.sha(', readback)
                self.assertIn("set -o noclobber", body)
                self.assertIn('> "$output.producer.json"', body)

    def test_public_inventory_uploads_keep_every_original_and_no_payload_glob(self):
        suffixes = ("input.json", "inventory.json", "extract.request.json", "extract.completion.json",
                    "extract.stdout", "extract.stderr", "extract.subtree.original",
                    "extract.binding.json", "extract.closure.json")
        expected = set()
        for kind in ("deb", "appimage"):
            prefix = "ci-evidence/linux-" + kind + "-package-inventory"
            expected.update((prefix + ".producer.json", prefix + ".consumer.json"))
            expected.update(prefix + "/" + name for name in suffixes)
        for body, title in (
                (self.installed, "Preserve original installed Linux package inventories"),
                (self.linux, "Preserve original isolated Linux package inventories")):
            upload = step(body, title)
            selected = upload.split("          path: |\n", 1)[1].split("          if-no-files-found:", 1)[0]
            actual = {line.strip() for line in selected.splitlines() if line.strip()}
            self.assertEqual(actual, expected)
            self.assertNotIn("*", selected)
            self.assertIn("always()", upload)
        from tooling import candidate_attestation_input as attestation
        common = self.linux.split("          name: motionwright-linux-candidate\n", 1)[1]
        common = common.split("          if-no-files-found:", 1)[0]
        evidence = {Path(line.strip()).name for line in common.splitlines()
                    if line.strip().startswith("ci-evidence/")}
        self.assertEqual(evidence, attestation.KNOWN_EVIDENCE_FILES["linux"])

    def test_macos_keeps_existing_flow_without_activating_unproved_inventory_extraction(self):
        macos = job_source(self.workflow, "macos")
        self.assertNotIn("candidate_package_inventory", macos)
        self.assertNotIn("candidate_package_extraction", macos)
        self.assertEqual(macos.count('hdiutil attach -readonly -nobrowse'), 1)
        self.assertEqual(macos.count('ditto "$app" "$work/installed/Motionwright.app"'), 1)

    def test_nsis_only_observes_after_existing_install_and_file_ownership(self):
        body = self.windows
        titles = ("Silent current-user NSIS install and inspect installed Windows executable",
                  "Verify isolated installed Windows application inventory",
                  "Observe detached NSIS members after existing ownership checks",
                  "Consume the original NSIS member observation with its retained producer seal",
                  "Cold launch and reopen the real NSIS-installed application")
        self.assertEqual([body.index(t) for t in titles], sorted(body.index(t) for t in titles))
        observe = step(body, titles[2])
        consume = step(body, titles[3])
        for block in (observe, consume):
            self.assertIn("inputs.verification_scope == 'windows_installed'", block)
            self.assertIn('gate.internal_source(os.environ)', block)
            self.assertIn('expected_inventory_sha256=os.environ["MW_WINDOWS_APPLICATION_INVENTORY_SHA256"]', block)
            self.assertEqual(block.count("gate.package_inventory("), 1)
            self.assertNotIn("Start-Process", block)
            self.assertNotIn("7z", block)
            self.assertNotIn("--appimage-extract", block)
        self.assertIn("installer_process_closure=NOT_OBSERVED full_inventory=NOT_VERIFIED", observe)
        self.assertIn("steps.windows_package_inventory.outputs.package_inventory_sha256", consume)
        self.assertIn('expected_package_inventory_sha256=expected', consume)
        self.assertIn('reference["custody"]=="NOT_OBSERVED"', consume)
        self.assertEqual(body.count('Start-Process -FilePath $installer'), 1)


if __name__ == "__main__":
    unittest.main()
