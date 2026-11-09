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
    "tooling/native_manim_resume_host.py",
    "tooling/native_manim_frozen_runtime_host.py",
    "tooling/tests/test_native_manim_resume.py",
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

    def test_manim_selection_does_not_watch_unrelated_documents(self):
        patterns = pr_paths()
        for path in ("README.md", "docs/roadmap.md",
                     ".github/workflows/native-blender-e2e.yml",
                     "tooling/native_graph_browser_owner.py"):
            with self.subTest(path=path):
                self.assertFalse(any(fnmatchcase(path, glob) for glob in patterns))


if __name__ == "__main__":
    unittest.main()
