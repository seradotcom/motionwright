import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICY = ROOT / "tooling" / "security_advisory_policy.py"


def audit_payload(*, changed_class=False, vulnerable=False,
                  path_crate_warning_reported=True, missing_unmaintained=False,
                  new_unknown=False):
    warnings = {
        "unsound": ([{"advisory": {"id": "RUSTSEC-2024-0429"}}]
                    if path_crate_warning_reported else []),
        "unmaintained": ([] if missing_unmaintained
                         else [{"advisory": {"id": "RUSTSEC-2024-0370"}}]),
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
        self.assertEqual(evidence["open_advisories_absent_from_registry_audit"], [])
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
                         ["RUSTSEC-2024-0429"])
        self.assertEqual({row["id"] for row in evidence["known_warnings"]},
                         {"RUSTSEC-2024-0370", "RUSTSEC-2024-0429"})
        self.assertTrue(all(row["status"].startswith("OPEN_")
                            for row in evidence["known_warnings"]))
        self.assertFalse(evidence["glib_owner_chain"]["glib_vulnerability_fixed"])
        self.assertIn("path dependency", evidence["statement"])
        self.assertIn("glib_remediation=NOT_PROVEN", process.stdout)

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
