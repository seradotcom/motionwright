"""Static CI selection tests: GN1 real owner stays manual; inert Rust2 is separate.

These tests have no Native, Host, Store, or GitHub side effects.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/graph-gn1-two-compiled-ci.yml"
NATIVE = ROOT / ".github/workflows/graph-native-startup-dev.yml"
RUST = ROOT / "crates/native/examples/native-graph-review-session.rs"
PYTHON = ROOT / "tooling/tests/test_ci_graph_dev_native_firstsave.py"

EXPECTED_RUST = (
    "smaller_parent_retains_two_sequential_originals_without_expanding_eight_mib",
    "smaller_parent_refuses_unreserved_oversized_result_without_publishing",
)
EXPECTED_NEW_PYTHON = (
    "test_large_stdout_is_bounded_unknown_and_never_replayed",
    "test_large_stderr_does_not_mask_unknown_as_custody_admission",
    "test_timeout_with_no_observed_bytes_preserves_unknown_and_no_fake_newline",
    "test_returned_json_is_bound_and_empty_stderr_is_not_fabricated",
)


class Gn1CompiledSelectedSourceTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.native = NATIVE.read_text(encoding="utf-8")
        self.assertNotIn("\r", self.workflow)

    def test_original_native_owner_stays_strictly_manual(self):
        self.assertIn("\n  workflow_dispatch:", self.native)
        self.assertIn("\n  workflow_call:", self.native)
        self.assertNotIn("\n  push:", self.native)
        self.assertNotIn("\n  pull_request:", self.native)
        self.assertIn(
            "github.event_name == 'workflow_dispatch' && "
            "inputs.one_original_owner == 'allow-one-original'",
            self.native,
        )
        self.assertNotIn("gn1-two-rust-compiled-selected:", self.native)

    def test_separate_ci_triggers_only_selected_source_push(self):
        self.assertEqual(self.workflow.count("  push:\n"), 1)
        self.assertNotIn("  workflow_dispatch:\n", self.workflow)
        self.assertEqual(self.workflow.count("  gn1-source-and-rust-two:\n"), 1)
        self.assertIn(
            "branches: ['integrate/phase1-final-composition-20261027']",
            self.workflow,
        )
        expected_paths = (
            ".github/workflows/graph-gn1-two-compiled-ci.yml",
            "crates/native/examples/native-graph-review-session.rs",
            ".github/workflows/graph-native-startup-dev.yml",
            "tooling/ci_graph_dev_native_firstsave.py",
            "tooling/ci_graph_dev_native_startup.py",
            "tooling/ci_graph_dev_terminal_stdout.py",
            "tooling/tests/test_ci_graph_dev_native_firstsave.py",
            "tooling/tests/test_ci_graph_dev_portable_terminal_stdout.py",
            "tooling/tests/test_ci_graph_gn1_selected.py",
            "Cargo.toml",
            "Cargo.lock",
            "SOURCE_LOCK.json",
        )
        source_paths = re.search(
            r"(?ms)^    paths:\n((?:      - [^\n]+\n)+)", self.workflow
        )
        self.assertIsNotNone(source_paths)
        actual_paths = tuple(
            line.strip()[2:].strip("\'\"")
            for line in source_paths.group(1).splitlines()
        )
        self.assertEqual(actual_paths, expected_paths)
        self.assertIn("github.run_attempt == 1", self.workflow)
        self.assertIn("permissions:\n  contents: read", self.workflow)

    def test_inert_python18_and_exact_rust2_without_native_launch(self):
        roster = re.findall(
            r"(?m)^\s+def (test_[A-Za-z0-9_]+)\s*\(",
            PYTHON.read_text(encoding="utf-8"),
        )
        self.assertEqual(len(roster), len(set(roster)))
        self.assertEqual(len(roster), 18)
        for name in EXPECTED_NEW_PYTHON:
            self.assertEqual(roster.count(name), 1)
        rust = RUST.read_text(encoding="utf-8")
        for name in EXPECTED_RUST:
            self.assertEqual(len(re.findall(r"(?m)^\s*fn\s+" + name + r"\s*\(", rust)), 1)
            self.assertIn(name, self.workflow)
        self.assertEqual(
            self.workflow.count(
                "cargo test --locked --jobs 2 -p motionwright-native"
            ),
            1,
        )
        self.assertIn("--example native-graph-review-session", self.workflow)
        self.assertIn("smaller_parent_ -- --test-threads=1", self.workflow)
        self.assertIn("Ran 18 tests in [0-9.]+s", self.workflow)
        self.assertNotIn("providers.mount", self.workflow)
        self.assertNotIn("one_original_save:", self.workflow)
        self.assertNotIn("GH_TOKEN", self.workflow)

    def test_originals_have_portable_sha_and_no_self_hash(self):
        self.assertEqual(
            self.workflow.count(
                "find . -maxdepth 1 -type f ! -name originals.sha256 -print0"
            ),
            1,
        )
        self.assertIn('cd "$GN1_TWO_EVIDENCE"', self.workflow)
        self.assertIn("LC_ALL=C sort -z | xargs -0 -r sha256sum", self.workflow)
        self.assertIn("if: always()", self.workflow)
        self.assertIn("retention-days: 14", self.workflow)
        for name in ("python.original.log", "rust.original.log",
                     "rust-stderr.original.log", "source-sha.original.txt"):
            self.assertIn(name, self.workflow)

    def test_never_claim_physical_g2_or_launch_graph_effect(self):
        self.assertIn('"Native_owner_startup": "NOT_RUN"', self.workflow)
        self.assertIn('"Graph_Save": "NOT_RUN"', self.workflow)
        self.assertIn('"G2": "NOT_GRANTED"', self.workflow)
        self.assertIn('"F": "HOLD"', self.workflow)
        # A read-only source assertion may mention the manual owner guard.
        # Reject executable Native dispatch, not a quote of that guard.
        for forbidden in ("cargo run ", "--mode run", "providers.release_bound",
                          "status:write", "contents: write",
                          "native-owner-first-start:", "one_original_save:"):
            self.assertNotIn(forbidden, self.workflow)


if __name__ == "__main__":
    unittest.main()
