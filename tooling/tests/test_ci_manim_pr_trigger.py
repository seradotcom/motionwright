"""Ensure Manim original source/custody changes trigger CI on pull requests.

Pure source routing test: no GitHub dispatch, Host/Native effects, or renderer.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/native-manim-e2e.yml"

# Real source, custody, fixture and regression consumers introduced by the
# source-bound recovery/resume work. An isolated PR touching any of them must
# run the Manim source contracts rather than silently skipping that lane.
REQUIRED_PR_PATHS = (
    "tooling/native_manim_retained_reference.py",
    "tooling/requirements/manim-retained-reference-42369864661.json",
    "tooling/retain_manim_source_recovery_tool.py",
    "tooling/tests/fixtures/manim-recovery-42365799758.stdout.txt",
    "tooling/tests/fixtures/manim-sdk-guard-42365799758.json",
    "tooling/tests/test_manim_source_recovery_tool.py",
    "tooling/tests/test_native_manim_ntmode_context.py",
    "tooling/tests/test_native_manim_recovery_lineage.py",
    "tooling/tests/test_native_manim_retained_reference.py",
    "tooling/tests/test_ci_manim_pr_trigger.py",
    "tooling/ci_affected_rust_diagnostics.py",
    # Retain the original native entrypoints and current Rust/SDK source.
    "tooling/native_manim_e2e.py",
    "tooling/native_manim_cancel.py",
    "tooling/native_manim_cancel_publication.py",
    "tooling/tests/test_native_manim_cancel_process.py",
    "crates/native/examples/native_manim_cancel/active.rs",
    "tooling/native_manim_artifact_original.py",
    "tooling/tests/test_native_manim_artifact_zip_original.py",
    "tooling/native_manim_resume_host.py",
    "tooling/native_manim_frozen_runtime_host.py",
    "tooling/tests/test_native_manim_resume.py",
    "tooling/tests/test_native_manim_resume_host_files.py",
    "tooling/tests/test_native_manim_resume_metadata_reader.py",
    "tooling/requirements/manim-windows-held-originals.txt",
    ".github/workflows/native-manim-e2e.yml",
    "crates/driver-manim-community/src/lib.rs",
    "SOURCE_LOCK.json",
)


def pr_paths():
    text = WORKFLOW.read_text(encoding="utf-8")
    if text.count("  pull_request:\n") != 1:
        raise AssertionError("A single Manim pull_request trigger is required")
    block = text.split("  pull_request:\n", 1)[1].split("\npermissions:\n", 1)[0]
    if block.count("    paths:\n") != 1:
        raise AssertionError("A single Manim pull_request.paths list is required")
    block = block.split("    paths:\n", 1)[1]
    patterns = []
    for line in block.splitlines():
        if line.startswith("      - '") and line.endswith("'"):
            patterns.append(line.strip()[3:-1])
        else:
            break
    if not patterns or len(set(patterns)) != len(patterns):
        raise AssertionError("Missing or duplicate Manim pull_request source path")
    return patterns


class NativeManimPullRequestWatchTests(unittest.TestCase):
    def test_managed_original_source_and_tests_all_start_pr_ci(self):
        patterns = pr_paths()
        uncovered = [p for p in REQUIRED_PR_PATHS
                     if not any(fnmatchcase(p, glob) for glob in patterns)]
        self.assertEqual(uncovered, [], "Manim PR source-gate blind spots")

    def test_original_zip_regressions_run_in_source_only_pr_step(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        marker = "      - name: Verify Manim PR source recovery and custody path routing"
        self.assertEqual(workflow.count(marker), 1)
        source_step = workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]
        command = ("python3 -B -m unittest "
                   "tooling.tests.test_native_manim_artifact_zip_original -v")
        self.assertEqual(source_step.count(command), 1)
        self.assertNotIn("workflow_dispatch", source_step)
        self.assertNotIn("cargo ", source_step)

    def test_original_zip_source_ci_requires_ten_executed_no_skips_and_retained_originals(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        marker = "      - name: Verify Manim PR source recovery and custody path routing"
        source_step = workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("tooling.tests.test_native_manim_artifact_zip_original -v", source_step)
        self.assertIn("Ran 10 tests in", source_step)
        self.assertIn("ZIP_SOURCE_TEN_EXACT_UNSKIPPED", source_step)
        self.assertIn("test_native_manim_artifact_zip_original.py", source_step)
        self.assertIn("unittest.stdout.original.log", source_step)
        self.assertIn("unittest.stderr.original.log", source_step)
        self.assertIn("manim-zip-original-source", source_step)
        self.assertIn("${{ runner.temp }}/manim-zip-original-source/", workflow)

    def test_affected_rust_diagnostics_source_is_parsed_without_dispatch(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "      - name: Verify optional affected Rust diagnostics script has valid syntax, without executing it"
        self.assertEqual(text.count(marker), 1)
        source_step = text.split(marker, 1)[1].split(
            "      - name: Quick source and conformance policies", 1
        )[0]
        self.assertIn("python3 -B - <<'PY'", source_step)
        self.assertIn('ast.parse(source.read_text(encoding="utf-8"), filename=str(source))', source_step)
        self.assertNotIn("cargo ", source_step)
        self.assertNotIn("python3 -I -B tooling/ci_affected_rust_diagnostics.py", source_step)
        self.assertNotIn("workflow_dispatch", source_step)

    def test_windows_held_original_custody_has_one_bounded_source_job(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        marker = "  windows-held-originals-source:\n"
        self.assertEqual(workflow.count(marker), 1)
        job = workflow.split(marker, 1)[1].split("\n  source-recovery-tool:", 1)[0]
        self.assertIn("runs-on: windows-latest", job)
        self.assertIn("timeout-minutes: 10", job)
        self.assertIn("if: github.event_name == 'pull_request' || "
                      "(github.event_name == 'workflow_dispatch' "
                      "&& inputs.scenario == 'source-contracts')", job)
        self.assertIn("persist-credentials: false", job)
        self.assertIn("python-version: '3.13'", job)
        self.assertIn("--require-hashes -r tooling/requirements/manim-windows-held-originals.txt", job)
        self.assertIn("cryptography.__version__", job)
        self.assertIn('held = "tooling.tests.test_native_manim_resume_host_files.OriginalHostFileTests."', job)
        self.assertIn('nested = "tooling.tests.test_native_manim_resume_metadata_reader.ExplicitMetadataReaderTests."', job)
        self.assertIn("tests=10 native=NOT_RUN", job)
        self.assertIn("motionwright.manim.windows-held-original-source/2", job)
        self.assertIn("TextTestRunner(stream=sys.stdout, verbosity=2)", job)
        self.assertIn("result.testsRun != len(expected)", job)
        for required in ("result.skipped", "result.errors", "result.failures",
                         "result.unexpectedSuccesses", "result.expectedFailures"):
            self.assertIn(required, job)
        self.assertIn("destination.open(\"x\"", job)
        self.assertIn("manim-windows-held-original-source.json", job)
        self.assertIn('"native_effects": "NOT_RUN"', job)
        self.assertIn('"G2": "NOT_GRANTED"', job)
        self.assertNotIn("cargo build", job)
        self.assertNotIn("native_manim_e2e.py", job)
        self.assertNotIn("continue-on-error", job)

    def test_manim_selection_does_not_watch_unrelated_documents(self):
        patterns = pr_paths()
        for path in ("README.md", "docs/roadmap.md",
                     ".github/workflows/native-blender-e2e.yml",
                     "tooling/native_graph_browser_owner.py"):
            with self.subTest(path=path):
                self.assertFalse(any(fnmatchcase(path, glob) for glob in patterns))


if __name__ == "__main__":
    unittest.main()
