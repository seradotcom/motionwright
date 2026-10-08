import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICY = ROOT / "tooling" / "security_advisory_policy.py"


def audit_payload(*, changed_class=False, vulnerable=False):
    warnings = {
        "unsound": [{"advisory": {"id": "RUSTSEC-2024-0429"}}],
        "unmaintained": [{"advisory": {"id": "RUSTSEC-2024-0370"}}],
    }
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
        self.assertIn("remain open", evidence["statement"])

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
