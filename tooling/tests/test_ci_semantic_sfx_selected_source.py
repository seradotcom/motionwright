"""Selected source verification for native semantic SFX signed PCM samples."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/semantic-sfx-selected-source.yml"
NEW_CASE = "semantic_sfx_admits_exact_negative_pcm16_floor_but_rejects_real_underflow"


class SemanticSfxSelectedSourceRoutingTests(unittest.TestCase):
    def source(self):
        self.assertTrue(WORKFLOW.is_file(), "The selected source-only SFX route is missing")
        return WORKFLOW.read_text(encoding="utf-8")

    def test_push_only_current_integration_branch_and_native_source(self):
        text = self.source()
        head = text.split("permissions:", 1)[0]
        self.assertIn("push:", head)
        self.assertIn("integrate/phase1-final-composition-20261027", head)
        self.assertIn("'crates/native/src/sfx_mix.rs'", head)
        self.assertIn("'crates/native/tests/semantic_sfx_mix.rs'", head)
        self.assertNotIn("workflow_dispatch:", head)
        self.assertNotIn("pull_request:", head)
        self.assertNotIn("crates/native/src/original_mix.rs", head)

    def test_selected_case_presence_is_required_before_compilation(self):
        text = self.source()
        self.assertIn("id: source_case", text)
        self.assertIn("grep -Fq 'fn " + NEW_CASE + "()'", text)
        self.assertIn('selected=true', text)
        self.assertIn("if: steps.source_case.outputs.selected == 'true'", text)

    def test_single_exact_rust_case_no_full_suite_or_graph(self):
        text = self.source()
        command = "cargo test --locked -p motionwright-native --test semantic_sfx_mix " + NEW_CASE
        self.assertEqual(text.count(command), 1)
        self.assertIn("-- --exact", text)
        self.assertNotIn("cargo test --workspace", text)
        self.assertNotIn("cargo test --all", text)
        self.assertNotIn("graph-flow", text)
        self.assertNotIn("Service47", text)

    def test_genuine_original_case_output_is_verified_and_hashed(self):
        text = self.source()
        self.assertIn("running 1 test", text)
        self.assertIn("1 passed; 0 failed; 0 ignored; 0 measured;", text)
        self.assertIn("sha256sum", text)
        self.assertIn("roster.original.log", text)
        self.assertIn("roster.original.log.sha256", text)
        self.assertIn("source-sha.original", text)

    def test_absent_case_explicitly_not_run(self):
        text = self.source()
        self.assertIn("selected=false", text)
        self.assertIn("NOT_RUN", text)
        self.assertIn("source_case.outputs.selected", text)
        self.assertIn("if: always()", text)

    def test_retain_bounded_original_with_no_privileged_credentials(self):
        text = self.source()
        self.assertIn("contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("retention-days: 7", text)
        self.assertIn("if-no-files-found: warn", text)
        self.assertIn("actions/upload-artifact@", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("workflow_run:", text)

    def test_embedded_original_summary_python_compiles(self):
        import ast
        import textwrap

        text = self.source()
        start = text.index("<<'PY'\n") + len("<<'PY'\n")
        end = text.index("\n          PY", start)
        script = textwrap.dedent(text[start:end])
        ast.parse(script, filename="semantic-sfx-selected-summary.py")

    def test_preserves_existing_full_native_sfx_workflow(self):
        self.source()
        full = (ROOT / ".github/workflows/semantic-sfx-e2e.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('branches: ["main"]', full)
        self.assertIn("cargo test --locked -p motionwright-native --test semantic_sfx_mix", full)


if __name__ == "__main__":
    unittest.main()
