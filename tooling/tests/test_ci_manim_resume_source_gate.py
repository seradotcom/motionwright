"""Guard Manim resume source coverage on PR without granting Native/G2.

The resumable native scenarios remain manual and require physical originals.
The ordinary pull-request source job must still exercise the inert, synthetic
resume predicates when that source changes.
"""
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/native-manim-e2e.yml"
SOURCE_LABEL = "  source-contracts:"
NEXT_JOB = "  source-recovery-tool:"
RESUME_LABEL = "  native-manim-resume:"
TEST_MODULE = "tooling.tests.test_native_manim_resume"


def _source_job():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    if workflow.count(SOURCE_LABEL) != 1 or workflow.count(NEXT_JOB) != 1:
        raise AssertionError("Missing/duplicate Manim source job boundary")
    return workflow.split(SOURCE_LABEL, 1)[1].split(NEXT_JOB, 1)[0]


class ManimResumeSourcePRCoverage(unittest.TestCase):
    def test_pull_request_watches_resumable_source_and_source_guard(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        pr = workflow.split("  pull_request:", 1)[1].split("\npermissions:", 1)[0]
        for glob in ("tooling/native_manim_resume*.py",
                     "tooling/tests/test_native_manim_resume.py",
                     "tooling/tests/test_ci_manim_resume_source_gate.py"):
            with self.subTest(glob=glob):
                self.assertIn("      - '" + glob + "'", pr)

    def test_source_job_runs_pinned_synthetic_resume_predicates_once(self):
        source = _source_job()
        marker = "Admit source-only Manim resume regressions on pull requests"
        self.assertEqual(source.count(marker), 1)
        step = source.split("      - name: " + marker, 1)[1].split("      - name: ", 1)[0]
        command = ('MANIM_NATIVE_SCENARIO=editable-source-resume '
                   '"$RUNNER_TEMP/manim-custody-venv/bin/python" -B -m unittest '
                   + TEST_MODULE + " -v")
        self.assertEqual(step.count(command), 1)
        self.assertIn("set -euo pipefail", step)
        self.assertIn("tooling.tests.test_ci_manim_resume_source_gate -v", step)
        self.assertNotIn("cargo ", step)
        self.assertNotIn("native-manim-resume --", step)
        self.assertNotIn("workflow_dispatch", step)

    def test_no_existing_manual_effect_scenario_is_admitted_to_source_job(self):
        source = _source_job()
        self.assertIn("inputs.scenario != 'editable-source-resume'", source[:300])
        self.assertIn("inputs.scenario != 'recovery-runtime-cycle'", source[:300])
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(workflow.count(RESUME_LABEL), 1)
        resume_job = workflow.split(RESUME_LABEL, 1)[1]
        self.assertIn("github.event_name == 'workflow_dispatch'", resume_job[:350])


if __name__ == "__main__":
    unittest.main()
