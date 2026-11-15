import json
import pathlib
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
from security_advisory_policy import verify_ttf_parser_review, verify_rustix_0486_patched
POLICY = ROOT / "tooling" / "security_advisory_policy.py"


def audit_payload(*, changed_class=False, vulnerable=False,
                  path_crate_warning_reported=True, missing_unmaintained=False,
                  new_unknown=False, missing_ttf_parser=True):
    warnings = {
        "unsound": ([{"advisory": {"id": "RUSTSEC-2024-0429"}}]
                    if path_crate_warning_reported else []),
        "unmaintained": ([] if missing_unmaintained
                         else [{"advisory": {"id": "RUSTSEC-2024-0370"}}])
                        + ([] if missing_ttf_parser
                           else [{"advisory": {"id": "RUSTSEC-2026-0192"}}]),
    }
    if new_unknown:
        warnings["unmaintained"].append({"advisory": {"id": "RUSTSEC-2099-9999"}})
    if changed_class:
        warnings["notice"] = warnings.pop("unsound")
    return {
        "vulnerabilities": {
            "found": vulnerable,
            "count": 1 if vulnerable else 0,
            "list": (
                [{"advisory": {"id": "RUSTSEC-2099-0001"}}]
                if vulnerable
                else []
            ),
        },
        "warnings": warnings,
    }


class SecurityAdvisoryPolicyTests(unittest.TestCase):
    def run_policy(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "audit.json"
            output = pathlib.Path(directory) / "evidence.json"
            path.write_text(json.dumps(payload))
            process = subprocess.run(
                [
                    sys.executable,
                    str(POLICY),
                    "--audit-json",
                    str(path),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            evidence = json.loads(output.read_text()) if output.exists() else None
            return process, evidence

    def test_rustix_0486_is_patched_without_a_new_warning_waiver(self):
        from copy import deepcopy
        lock = tomllib.loads((ROOT / "Cargo.lock").read_text())
        selected = [item for item in lock["package"] if item["name"] == "rustix"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["version"], "1.1.6")
        self.assertEqual(
            selected[0]["checksum"],
            "e5e76742e5294153d4dde1207dbec5bdd81eef6fabdc0d2118babadabfd858aa",
        )
        self.assertIn(
            'rustix = { version = "=1.1.6", features = ["fs"] }',
            (ROOT / "crates/storage/Cargo.toml").read_text(),
        )
        self.assertIsNone(verify_rustix_0486_patched(lock))
        for mutation in ("old-version", "bad-checksum", "missing", "duplicate", "source"):
            with self.subTest(mutation=mutation):
                altered = deepcopy(lock)
                target = [item for item in altered["package"] if item.get("name") == "rustix"][0]
                if mutation == "old-version":
                    target["version"] = "1.1.5"
                elif mutation == "bad-checksum":
                    target["checksum"] = "0" * 64
                elif mutation == "missing":
                    altered["package"].remove(target)
                elif mutation == "duplicate":
                    altered["package"].append(deepcopy(target))
                else:
                    target["source"] = "unreviewed"
                with self.assertRaises(SystemExit):
                    verify_rustix_0486_patched(altered)

    def test_exact_reviewed_warning_set_is_evidenced_without_claiming_resolution(self):
        process, evidence = self.run_policy(audit_payload())
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(evidence["audit_checked"])
        self.assertEqual(evidence["vulnerabilities_allowed"], 0)
        self.assertEqual(
            evidence["actual_warning_ids"],
            ["RUSTSEC-2024-0370", "RUSTSEC-2024-0429"],
        )
        self.assertIn("remain OPEN", evidence["statement"])
        self.assertFalse(evidence["upstream_glib_advisory_resolved"])
        self.assertFalse(evidence["local_path_crate_audit_coverage_complete"])
        self.assertEqual(evidence["open_advisories_absent_from_registry_audit"], ["RUSTSEC-2026-0192"])
        self.assertEqual(evidence["verified_source_removed_advisories"], ["RUSTSEC-2026-0192"])
        self.assertEqual(evidence["registry_audit_coverage_gaps"], ["RUSTSEC-2024-0429"])
        chain = evidence["glib_owner_chain"]
        self.assertEqual(chain["schema"], "motionwright.v01-glib-owner-chain/1")
        self.assertEqual(chain["advisory_status"], "OPEN_UPSTREAM_BLOCKED")
        self.assertEqual(len(chain["reviewed_direct_owners"]), 12)
        self.assertEqual(len(chain["reviewed_transitive_owners"]), 22)
        self.assertEqual(chain["observed_first_party_owners"], ["motionwright-desktop"])
        self.assertFalse(chain["glib_vulnerability_fixed"])
        self.assertFalse(chain["linux_target_reachability_proven"])
        self.assertFalse(chain["affected_iterator_api_unreachable_proven"])
        self.assertFalse(chain["release_authorized"])

    def test_real_cargo_audit_omits_path_glib_but_not_security_obligation(self):
        # This is the exact structural outcome from real PR #237 audit:
        # the local glib source has no registry advisory entry; proc macro
        # unmaintained warning remains visible.
        process, evidence = self.run_policy(
            audit_payload(path_crate_warning_reported=False)
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(evidence["actual_warning_ids"], ["RUSTSEC-2024-0370"])
        self.assertEqual(evidence["open_advisories_absent_from_registry_audit"],
                         ["RUSTSEC-2024-0429", "RUSTSEC-2026-0192"])
        self.assertEqual({row["id"] for row in evidence["known_warnings"]},
                         {"RUSTSEC-2024-0370", "RUSTSEC-2024-0429", "RUSTSEC-2026-0192"})
        self.assertTrue(all(row["status"].startswith("OPEN_")
                            for row in evidence["known_warnings"]))
        self.assertFalse(evidence["glib_owner_chain"]["glib_vulnerability_fixed"])
        self.assertIn("path dependency", evidence["statement"])
        self.assertIn("glib_remediation=NOT_PROVEN", process.stdout)

    def test_verified_fontations_source_removal_keeps_runtime_risk_open(self):
        process, evidence = self.run_policy(
            audit_payload(path_crate_warning_reported=False)
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        row = evidence["ttf_parser_unmaintained_source"]
        self.assertEqual(row["advisory_id"], "RUSTSEC-2026-0192")
        self.assertEqual(row["status"], "OPEN_SOURCE_REMOVED_RUNTIME_UNVERIFIED")
        self.assertTrue(row["source_dependency_absent"])
        self.assertTrue(row["replacement_source_verified"])
        self.assertEqual(row["replacement"]["name"], "read-fonts")
        self.assertEqual(row["replacement"]["version"], "0.47.0")
        self.assertFalse(row["maintenance_risk_closed"])
        self.assertFalse(row["native_runtime_or_distribution_certified"])
        self.assertEqual(row["direct_owners"], [
            "motionwright-manim-profile@0.1.0",
            "semwright-driver-motion-canvas@1.0.0",
        ])
        self.assertEqual(len(row["transitive_owners"]), 6)

    def test_fontations_registry_checksum_drift_fails_closed(self):
        lock = tomllib.loads((ROOT / "Cargo.lock").read_text())
        policy = json.loads((ROOT / "docs/security/known-rustsec-advisories.json").read_text())
        reviewed = next(row for row in policy["advisories"]
                        if row["id"] == "RUSTSEC-2026-0192")
        package = next(row for row in lock["package"]
                       if row["name"] == "read-fonts")
        package["checksum"] = "0" * 64
        with self.assertRaises(SystemExit):
            verify_ttf_parser_review(lock, reviewed)

    def test_fontations_direct_owner_drift_fails_closed(self):
        lock = tomllib.loads((ROOT / "Cargo.lock").read_text())
        policy = json.loads((ROOT / "docs/security/known-rustsec-advisories.json").read_text())
        reviewed = next(row for row in policy["advisories"]
                        if row["id"] == "RUSTSEC-2026-0192")
        native = next(row for row in lock["package"]
                      if row["name"] == "motionwright-native")
        native["dependencies"].append("read-fonts")
        with self.assertRaises(SystemExit):
            verify_ttf_parser_review(lock, reviewed)

    def test_fontations_locked_version_drift_fails_closed(self):
        lock = tomllib.loads((ROOT / "Cargo.lock").read_text())
        policy = json.loads((ROOT / "docs/security/known-rustsec-advisories.json").read_text())
        reviewed = next(row for row in policy["advisories"]
                        if row["id"] == "RUSTSEC-2026-0192")
        package = next(row for row in lock["package"] if row["name"] == "read-fonts")
        package["version"] = "0.47.1"
        with self.assertRaises(SystemExit):
            verify_ttf_parser_review(lock, reviewed)

    def test_ttf_parser_pinned_semwright_revision_drift_fails_closed(self):
        lock = tomllib.loads((ROOT / "Cargo.lock").read_text())
        policy = json.loads((ROOT / "docs/security/known-rustsec-advisories.json").read_text())
        reviewed = next(row for row in policy["advisories"]
                        if row["id"] == "RUSTSEC-2026-0192")
        driver = next(row for row in lock["package"]
                      if row["name"] == "semwright-driver-motion-canvas")
        driver["source"] = "git+https://github.com/seradotcom/semwright#changed"
        with self.assertRaises(SystemExit):
            verify_ttf_parser_review(lock, reviewed)

    def test_ttf_parser_indirect_owner_drift_fails_closed(self):
        lock = tomllib.loads((ROOT / "Cargo.lock").read_text())
        policy = json.loads((ROOT / "docs/security/known-rustsec-advisories.json").read_text())
        reviewed = next(row for row in policy["advisories"]
                        if row["id"] == "RUSTSEC-2026-0192")
        domain = next(row for row in lock["package"]
                      if row["name"] == "motionwright-domain")
        domain["dependencies"].append("motionwright-manim-profile")
        with self.assertRaises(SystemExit):
            verify_ttf_parser_review(lock, reviewed)

    def test_reappearing_ttf_parser_warning_cannot_be_hidden_in_another_class(self):
        payload = audit_payload(path_crate_warning_reported=False)
        payload["warnings"]["unmaintained"] = [
            entry for entry in payload["warnings"]["unmaintained"]
            if entry["advisory"]["id"] != "RUSTSEC-2026-0192"
        ]
        payload["warnings"]["notice"] = [
            {"advisory": {"id": "RUSTSEC-2026-0192"}}
        ]
        process, evidence = self.run_policy(payload)
        self.assertNotEqual(process.returncode, 0)
        self.assertIsNone(evidence)
        self.assertIn("warning reappeared after verified source removal", process.stderr)

    def test_ttf_parser_warning_must_be_absent_after_verified_source_removal(self):
        process, evidence = self.run_policy(
            audit_payload(path_crate_warning_reported=False,
                          missing_ttf_parser=False)
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIsNone(evidence)
        self.assertIn("warning reappeared after verified source removal", process.stderr)

    def reviewed_parser_inputs(self):
        lock = tomllib.loads((ROOT / "Cargo.lock").read_text())
        policy = json.loads((ROOT / "docs/security/known-rustsec-advisories.json").read_text())
        return lock, next(row for row in policy["advisories"]
                          if row["id"] == "RUSTSEC-2026-0192")

    def test_any_ttf_parser_package_reintroduction_fails_closed(self):
        lock, reviewed = self.reviewed_parser_inputs()
        lock["package"].append({
            "name": "ttf-parser", "version": reviewed["locked_version"],
            "source": "registry+https://github.com/rust-lang/crates.io-index",
            "checksum": reviewed["registry_checksum"],
        })
        with self.assertRaises(SystemExit):
            verify_ttf_parser_review(lock, reviewed)

    def test_replacement_policy_and_first_party_bindings_cannot_drift(self):
        from copy import deepcopy
        lock, reviewed = self.reviewed_parser_inputs()
        for mutation in ("replacement-source", "source-hash", "closed-status", "tokens"):
            changed = deepcopy(reviewed)
            if mutation == "replacement-source":
                changed["source_replacement"]["source"] = "unreviewed"
            elif mutation == "source-hash":
                changed["reviewed_first_party_sources"]["crates/manim-profile/src/font_tables.rs"] = "0" * 64
            elif mutation == "closed-status":
                changed["status"] = "CLOSED"
            else:
                changed["forbidden_first_party_tokens"] = []
            with self.subTest(mutation=mutation), self.assertRaises(SystemExit):
                verify_ttf_parser_review(lock, changed)

    def test_duplicate_or_substituted_fontations_source_fails_closed(self):
        from copy import deepcopy
        lock, reviewed = self.reviewed_parser_inputs()
        for mutation in ("duplicate", "substituted"):
            changed = deepcopy(lock)
            selected = next(row for row in changed["package"] if row["name"] == "read-fonts")
            if mutation == "duplicate":
                changed["package"].append(deepcopy(selected))
            else:
                selected["source"] = "unreviewed"
            with self.subTest(mutation=mutation), self.assertRaises(SystemExit):
                verify_ttf_parser_review(changed, reviewed)

    def test_selected_font_owner_sources_cannot_be_substituted(self):
        from copy import deepcopy
        lock, reviewed = self.reviewed_parser_inputs()
        for mutation in ("manim-registry", "manim-checksum", "driver-checksum"):
            changed = deepcopy(lock)
            name = ("semwright-driver-motion-canvas" if mutation == "driver-checksum"
                    else "motionwright-manim-profile")
            owner = next(row for row in changed["package"] if row["name"] == name)
            if mutation == "manim-registry":
                owner["source"] = "registry+https://github.com/rust-lang/crates.io-index"
            else:
                owner["checksum"] = "0" * 64
            with self.subTest(mutation=mutation), self.assertRaises(SystemExit):
                verify_ttf_parser_review(changed, reviewed)

    def test_font_dependency_version_and_source_tokens_resolve_exactly(self):
        from copy import deepcopy
        lock, reviewed = self.reviewed_parser_inputs()
        correct = "read-fonts 0.47.0 (registry+https://github.com/rust-lang/crates.io-index)"
        for dependency in (correct, "read-fonts 9.9.9", "read-fonts not-a-version",
                           "read-fonts 0.47.0 (unreviewed)", correct + " trailing",
                           "read-fonts  0.47.0"):
            changed = deepcopy(lock)
            owner = next(row for row in changed["package"] if row["name"] == "motionwright-manim-profile")
            owner["dependencies"][owner["dependencies"].index("read-fonts")] = dependency
            with self.subTest(dependency=dependency):
                if dependency == correct:
                    self.assertTrue(verify_ttf_parser_review(changed, reviewed)["source_dependency_absent"])
                else:
                    with self.assertRaises(SystemExit):
                        verify_ttf_parser_review(changed, reviewed)

    def test_missing_other_reviewed_warning_still_fails(self):
        process, evidence = self.run_policy(
            audit_payload(path_crate_warning_reported=False,
                          missing_unmaintained=True)
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIsNone(evidence)
        self.assertIn("missing=['RUSTSEC-2024-0370']", process.stderr)

    def test_new_unknown_warning_still_fails(self):
        process, evidence = self.run_policy(
            audit_payload(path_crate_warning_reported=False, new_unknown=True)
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIsNone(evidence)
        self.assertIn("unexpected=['RUSTSEC-2099-9999']", process.stderr)

    def test_changed_warning_class_requires_new_review(self):
        process, evidence = self.run_policy(audit_payload(changed_class=True))
        self.assertNotEqual(process.returncode, 0)
        self.assertIsNone(evidence)
        self.assertIn("class changed", process.stderr)

    def test_vulnerability_is_never_accepted_by_warning_policy(self):
        process, evidence = self.run_policy(audit_payload(vulnerable=True))
        self.assertNotEqual(process.returncode, 0)
        self.assertIsNone(evidence)
        self.assertIn("security vulnerabilities", process.stderr)


if __name__ == "__main__":
    unittest.main()
