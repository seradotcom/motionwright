"""Workflow contracts for detached package observations; no package effects."""
from __future__ import annotations

import ast
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
        paths = ("tooling/candidate_package_inventory.py", "tooling/linux_package_parity.py",
                 "tooling/tests/test_linux_package_parity.py", "tooling/linux_deb_archive_audit.py",
                 "tooling/tests/test_linux_deb_archive_audit.py", "tooling/linux_appimage_system_origins.py",
                 "tooling/tests/test_linux_appimage_system_origins.py",
                 "tooling/linux_elf_build_id.py", "tooling/tests/test_linux_elf_build_id.py",
                 "tooling/linux_apprun_release_identity.py",
                 "tooling/tests/test_linux_apprun_release_identity.py",
                 "tooling/linux_tauri_bundle_tool_pins.py",
                 "tooling/tests/test_linux_tauri_bundle_tool_pins.py",
                 "tooling/candidate_package_extraction.py",
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
        self.assertIn('(12,39,8)', source)
        self.assertIn('counts["tests_run"]==counts["passed"]==expected', source)
        self.assertIn('"skipped","expected_failures","unexpected_successes"', source)
        self.assertIn("extraction_effects=0 full_inventory=NOT_VERIFIED G3=STOP", source)
        self.assertNotIn("|| true", source)
        original = step(policy, "Authenticate original Debian ar and control source contracts")
        self.assertIn("--pattern test_linux_deb_archive_audit.py", original)
        self.assertIn("--mode discovery --require-complete --expected-source", original)
        self.assertIn("counts[\x27tests_run\x27]==counts[\x27passed\x27]==26", original)
        self.assertIn("Native=NOT_RUN G3=STOP", original)
        self.assertIn("set -euo pipefail", original)
        self.assertNotIn("|| true", original)
        origin_source = step(policy, "Require original AppImage system-origin matching source controls")
        self.assertIn("--pattern test_linux_appimage_system_origins.py", origin_source)
        self.assertIn("--mode discovery --require-complete --expected-source", origin_source)
        self.assertIn("counts['tests_run']==counts['passed']==30", origin_source)
        self.assertIn("counts[k]==0", origin_source)
        self.assertIn("G3=STOP", origin_source)
        elf_source = step(policy, "Authenticate bounded GNU ELF build-id source controls")
        self.assertIn("--pattern test_linux_elf_build_id.py", elf_source)
        self.assertIn("--mode discovery --require-complete --expected-source", elf_source)
        self.assertIn("counts['tests_run']==counts['passed']==16", elf_source)
        self.assertIn("counts[k]==0", elf_source)
        self.assertIn("native_execution=NOT_RUN G3=STOP", elf_source)
        bootstrap_source = step(policy, "Require pinned upstream AppRun release-byte source controls")
        self.assertIn("--pattern test_linux_apprun_release_identity.py", bootstrap_source)
        self.assertIn("--mode discovery --require-complete --expected-source", bootstrap_source)
        self.assertIn("counts['tests_run']==counts['passed']==12", bootstrap_source)
        self.assertIn("counts[k]==0", bootstrap_source)
        self.assertIn("G3=STOP", bootstrap_source)
        tools_source = step(policy, "Require bounded pinned Tauri linuxdeploy source controls")
        self.assertIn("--pattern test_linux_tauri_bundle_tool_pins.py", tools_source)
        self.assertIn("--mode discovery --require-complete --expected-source", tools_source)
        self.assertIn("counts['tests_run']==counts['passed']==15", tools_source)
        self.assertIn("counts[k]==0", tools_source)
        self.assertIn("package_effects=NOT_RUN G3=STOP", tools_source)
        physical = step(self.linux, "Observe exact system-origin bytes in unresolved AppImage members")
        self.assertIn("inputs.verification_scope == 'linux_bundle'", physical)
        self.assertIn("--appimage-sha256", physical)
        self.assertIn("--deb-sha256", physical)
        self.assertIn("--expected-source \"$GITHUB_SHA\"", physical)
        self.assertIn('appimage-inventory/squashfs-root', physical)
        self.assertIn("tooling/linux_appimage_system_origins.py", physical)
        upload = step(self.linux, "Preserve original isolated Linux package inventories")
        self.assertIn("ci-evidence/linux-appimage-system-origin", upload)
        self.assertNotIn("ci-evidence/linux-appimage-system-origin/*", upload)
        archive = step(policy, "Retain original package inventory source control records")
        self.assertIn("deb-original-archive-source-${{ github.run_id }}-${{ github.run_attempt }}/*", archive)
        self.assertIn("elf-build-id-source-${{ github.run_id }}-${{ github.run_attempt }}/*", archive)
        self.assertIn("tauri-tool-pin-source-${{ github.run_id }}-${{ github.run_attempt }}/*", archive)
        retained = step(self.linux, "Preserve original isolated Linux dependency records")
        self.assertIn("ci-evidence/linux-cargo/tauri-tool-pins.pre.json", retained)
        self.assertIn("ci-evidence/linux-cargo/tauri-tool-pins.post.json", retained)
        bundler = (ROOT / "tooling/ci_linux_dependency_capture.py").read_text(encoding="utf-8")
        scoped = bundler.split("def bundle_packages(", 1)[1].split("\ndef verify_packages(", 1)[0]
        self.assertIn("toolpins.stage(cache_home)", scoped)
        self.assertLess(scoped.index("toolpins.verify_cached(cache_home, pinned)"),
                        scoped.index('capture.run_captured(command, repo / "apps/desktop"'))
        self.assertGreater(scoped.index("toolpins.verify_cached(cache_home, pinned, post=True)"),
                           scoped.index('capture.run_captured(command, repo / "apps/desktop"'))
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
        self.assertEqual(body.count(admission), 3)
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
        self.assertIn("tooling/linux_apprun_release_identity.py", body)
        self.assertIn("--producer-seal ci-evidence/linux-appimage-package-inventory.producer.json", body)
        self.assertIn("--appimage-inventory ci-evidence/linux-appimage-package-inventory/inventory.json", body)
        self.assertIn('--live-payload "$extracted"', body)
        self.assertIn("--expected-source \"$GITHUB_SHA\"", body)
        self.assertIn("--output ci-evidence/linux-apprun-release-pin.json", body)
        self.assertLess(body.index("tooling/linux_apprun_release_identity.py"),
                        body.index('if ! test -x "$extracted/AppRun"'))
        self.assertLess(body.index("tooling/linux_apprun_release_identity.py"),
                        body.index("xvfb-run"))

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
        expected.add("ci-evidence/linux-package-parity.json")
        expected.add("ci-evidence/linux-deb-original-archive-audit.json")
        for body, title in (
                (self.installed, "Preserve original installed Linux package inventories"),
                (self.linux, "Preserve original isolated Linux package inventories")):
            upload = step(body, title)
            selected = upload.split("          path: |\n", 1)[1].split("          if-no-files-found:", 1)[0]
            actual = {line.strip() for line in selected.splitlines() if line.strip()}
            expected_for_scope = set(expected)
            if body is self.linux:
                expected_for_scope.update({
                    "ci-evidence/linux-appimage-system-origin",
                    "ci-evidence/linux-apprun-release-pin.json",
                })
            self.assertEqual(actual, expected_for_scope)
            self.assertNotIn("*", selected)
            self.assertIn("always()", upload)
        for body, consumer, parity in (
                (self.installed, "Verify both extracted installed packages against the original Cargo capture",
                 "Reject divergent DEB and AppImage installed application payloads"),
                (self.linux, "Verify both extracted Linux packages against the original Cargo capture",
                 "Require exact shared payload bytes in isolated Linux bundles")):
            with self.subTest(parity=parity):
                self.assertLess(body.index(consumer), body.index(parity))
                upload_title = ("Preserve original installed Linux package inventories"
                                if body is self.installed
                                else "Preserve original isolated Linux package inventories")
                self.assertLess(body.index(parity), body.index(upload_title))
                check = step(body, parity)
                for value in (
                        "--expected-source \"$GITHUB_SHA\"",
                        "--deb ci-evidence/linux-deb-package-inventory/inventory.json",
                        "--appimage ci-evidence/linux-appimage-package-inventory/inventory.json",
                        "--expected-deb-sha256 \"$MW_DEB_PACKAGE_INVENTORY_SHA256\"",
                        "--expected-appimage-sha256 \"$MW_APPIMAGE_PACKAGE_INVENTORY_SHA256\"",
                        "--output ci-evidence/linux-package-parity.json"):
                    self.assertIn(value, check)
                self.assertIn("set -euo pipefail", check)
                self.assertNotIn("|| true", check)
        for body, title, producer in (
                (self.installed, "Authenticate original installed Debian archive control and data",
                 "first_use_package_inventory"),
                (self.linux, "Authenticate original isolated Debian archive control and data",
                 "linux_package_inventory")):
            with self.subTest(audit=title):
                audit = step(body, title)
                preceding = ("Reject divergent DEB and AppImage installed application payloads"
                             if body is self.installed
                             else "Require exact shared payload bytes in isolated Linux bundles")
                upload = ("Preserve original installed Linux package inventories"
                          if body is self.installed
                          else "Preserve original isolated Linux package inventories")
                self.assertLess(body.index(preceding), body.index(title))
                self.assertLess(body.index(title), body.index(upload))
                self.assertIn("steps." + producer + ".outputs.deb_inventory_sha256", audit)
                for value in (
                        "tooling/linux_deb_archive_audit.py",
                        '--deb "${debs[0]}"',
                        "--inventory ci-evidence/linux-deb-package-inventory/inventory.json",
                        '--expected-source "$GITHUB_SHA"',
                        '--expected-inventory-sha256 "$MW_DEB_PACKAGE_INVENTORY_SHA256"',
                        "--output ci-evidence/linux-deb-original-archive-audit.json",
                        'test "${#debs[@]}" -eq 1',
                        "set -euo pipefail"):
                    self.assertIn(value, audit)
                self.assertNotIn("|| true", audit)
        attestation_source = ast.parse(
            (ROOT / "tooling/candidate_attestation_input.py").read_text(encoding="utf-8")
        )
        evidence_rosters = [
            node.value for node in attestation_source.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "KNOWN_EVIDENCE_FILES"
                    for target in node.targets)
        ]
        self.assertEqual(len(evidence_rosters), 1)
        attestation_evidence = ast.literal_eval(evidence_rosters[0])
        common = self.linux.split("          name: motionwright-linux-candidate\n", 1)[1]
        common = common.split("          if-no-files-found:", 1)[0]
        evidence = {Path(line.strip()).name for line in common.splitlines()
                    if line.strip().startswith("ci-evidence/")}
        self.assertEqual(evidence, attestation_evidence["linux"])

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
