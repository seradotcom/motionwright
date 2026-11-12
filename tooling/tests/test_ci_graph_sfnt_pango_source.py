"""Lock the selected Graph-fast real Linux Pango source check to non-Native G0.

This is a workflow/source fixture test; do not run Pango or create a Store here.
"""
from __future__ import annotations

from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/graph-development-fast.yml"


class GraphSfntPangoSourceRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_original_linux_runtime_and_font_paths_trigger_source_gate(self):
        watched = self.workflow.split("  push:\n", 1)[1].split("  workflow_dispatch:\n", 1)[0]
        for path in (
            "tooling/manim_graph_font_probe.py",
            "tooling/manim_bundled_font_intake.py",
            "tooling/manim_graph_runtime.py",
            "tooling/native_manim_graph_runtime_intake.py",
            "tooling/derive_manim_native_fonts.py",
            "tooling/tests/test_manim_graph_font_probe.py",
            "tooling/tests/test_native_manim_graph_manifest.py",
            "tooling/tests/test_ci_graph_sfnt_pango_source.py",
            "crates/manim-profile/**",
        ):
            with self.subTest(path=path):
                self.assertEqual(watched.count("      - '" + path + "'\n"), 1)

    def test_g0_always_selects_pango_source_guard_without_native_admission(self):
        self.assertEqual(
            self.workflow.count(
                "tooling.tests.test_ci_graph_sfnt_pango_source \\\n"
            ), 1,
        )
        head = "      - name: Verify original Linux Pango SFNT and source-bound Graph runtime fixtures\n"
        tail = "      - name: Verify original J1 reader, closed context and inspection source on Linux\n"
        self.assertEqual(self.workflow.count(head), 1)
        self.assertEqual(self.workflow.count(tail), 1)
        step = self.workflow.split(head, 1)[1].split(tail, 1)[0]
        for expected in (
            "timeout-minutes: 6",
            'test "$RUNNER_OS" = Linux',
            'test "$GITHUB_SHA" = "$EXPECTED_GRAPH_SOURCE"',
            "NativeFontProvenanceTests.test_real_native_files_bind_distinct_hashes_to_all_retained_sources",
            "NativeSfntFaceTests.test_real_bundled_sfnt_faces_have_exact_ascii_coverage_without_fallback",
            "tooling.tests.test_native_manim_graph_manifest",
            "result.testsRun != 24",
            "result.skipped",
            "result.failures",
            "result.errors",
            "result.unexpectedSuccesses",
            "result.expectedFailures",
            "len(paths) != 7",
            'with dest.open("x"',
            '"Native_Host": "NOT_RUN"',
            '"Graph_Save": "NOT_RUN"',
            '"G2": "NOT_GRANTED"',
            '"G3": "STOP"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, step)
        for forbidden in ("continue-on-error", "cargo build", "render.start", "NativeOwner.acquire"):
            self.assertNotIn(forbidden, step)
        source = step.split("          python3 -B - <<'PY'\n", 1)[1].split("          PY\n", 1)[0]
        script = "\n".join(line[10:] for line in source.splitlines())
        ast.parse(script, filename="graph-fast-real-pango-original.py")

        tree = ast.parse((ROOT / "tooling/tests/test_native_manim_graph_manifest.py").read_text(encoding="utf-8"))
        cases = [method for cls in tree.body if isinstance(cls, ast.ClassDef)
                 for method in cls.body if isinstance(method, ast.FunctionDef)
                 and method.name.startswith("test_")]
        self.assertEqual(len(cases), 22, "Graph POSIX source roster needs review")

    def test_original_g0_artifact_retains_selected_pango_evidence(self):
        self.assertEqual(
            self.workflow.count(
                "            knowledge/runtime/graph-fast-pango-sfnt-source.json\n"
            ), 1,
        )
        self.assertIn(
            "Graph REAL private Linux Pango SFNT source PASS; no Native Host or Graph Save",
            self.workflow,
        )


if __name__ == "__main__":
    unittest.main()
