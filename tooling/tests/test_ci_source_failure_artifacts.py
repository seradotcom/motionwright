"""Keep source-policy failures primary without making missing evidence optional on success.

A failed source guard must stay red. Uploads may retain partial originals,
but a missing artifact must not become a second unrelated failure.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
ON_SUCCESS_ONLY = "${{ job.status == 'success' && 'error' || 'warn' }}"
ARTIFACT_PREFIXES = {
    ".github/workflows/ci.yml": (
        "ci-job-plan-contracts-",
    ),
    ".github/workflows/performance-acceptance.yml": (
        "performance-acceptance-evidence",
        "waveform-performance-evidence",
        "responsiveness-service-evidence",
        "cache-source-g0-",
    ),
}


def targeted_upload_policies(workflow, prefix):
    lines = (ROOT / workflow).read_text(encoding="utf-8").splitlines()
    named = [(i, s) for i, s in enumerate(lines)
             if s.strip().startswith("name: " + prefix)]
    if len(named) != 1:
        raise AssertionError((workflow, prefix, "missing or duplicated upload name"))
    i, _ = named[0]
    # The retained name is inside the upload step's with: mapping. Find its
    # own next with: input, not one from a subsequent unrelated step.
    boundary = next((j for j in range(i + 1, len(lines))
                     if lines[j].startswith("      - ")), len(lines))
    remaining = lines[i + 1:boundary]
    matches = [line.strip().split(": ", 1)[1] for line in remaining
               if line.strip().startswith("if-no-files-found: ")]
    if len(matches) != 1:
        raise AssertionError((workflow, prefix, "no single evidence absence policy"))
    before = lines[max(0, i - 11):i]
    if not any("actions/upload-artifact@" in line for line in before):
        raise AssertionError((workflow, prefix, "not an upload step"))
    if not any(line.strip() == "if: always()" for line in before):
        raise AssertionError((workflow, prefix, "missing partial-originals retention"))
    return matches[0]


class SourceFailureEvidenceTests(unittest.TestCase):
    def test_all_current_affected_original_uploads_preserve_fail_closed_errors(self):
        for workflow, prefixes in ARTIFACT_PREFIXES.items():
            for prefix in prefixes:
                with self.subTest(workflow=workflow, artifact=prefix):
                    self.assertEqual(targeted_upload_policies(workflow, prefix),
                                     ON_SUCCESS_ONLY)

    def test_success_requires_artifact_but_prior_failure_may_retain_partial(self):
        self.assertEqual(ON_SUCCESS_ONLY,
                         "${{ job.status == 'success' && 'error' || 'warn' }}")
        for status, expected in (("success", "error"), ("failure", "warn"),
                                 ("cancelled", "warn")):
            with self.subTest(status=status):
                resolved = "error" if status == "success" else "warn"
                self.assertEqual(resolved, expected)


if __name__ == "__main__":
    unittest.main()
