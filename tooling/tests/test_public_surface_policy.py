"""Public source hygiene rejects only development chatter, not product APIs."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from public_surface_policy import (
    scan_paths, scan_text, verify_public_surface,
)


class PublicSurfacePolicyTests(unittest.TestCase):
    def test_current_tracked_repository_is_clean(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(verify_public_surface(root), [])

    def test_private_tool_and_local_workspace_are_not_tracked(self):
        for folder in (".agents/", ".codex/", "knowledge/", "worktrees/",
                       ".worktrees/", "verification/"):
            with self.subTest(folder=folder):
                self.assertTrue(scan_paths([folder + "some-file.md"]))
        self.assertTrue(scan_paths(["AGENTS.md"]))
        self.assertTrue(scan_paths(["MASTER_PROMPT.md"]))

    def test_real_product_agents_are_not_forbidden(self):
        legitimate = (
            "A human and an agent edit the same revision-bound project.\n"
            "An external_agent provider has an explicit permission boundary.\n"
            "No agent may bypass an owner effect grant."
        )
        self.assertEqual(scan_text("docs/native-sdk.md", legitimate), [])
        self.assertEqual(scan_text("crates/native/src/lib.rs", legitimate), [])

    def test_development_speech_is_rejected_without_copying_its_body(self):
        for phrase in (
            "Another agent may be implementing this.",
            "human review not performed by the implementation agent",
            "silent modifications to other agents' work",
            "Delegated by the user:",
            "impeccable:product-schema",
            "Use this master prompt",
            "The next agent should edit this module",
        ):
            with self.subTest(phrase=phrase):
                outcome = scan_text("docs/spec.md", phrase)
                self.assertEqual(len(outcome), 1)
                self.assertNotIn(phrase, outcome[0])

    def test_unrelated_untracked_text_is_not_publicly_scanned(self):
        self.assertEqual(scan_text("knowledge/internal.md", "Another agent may be working"), [])
        self.assertEqual(scan_text("tests/private-notes.txt", "Agent handoff"), [])

    def test_allowed_first_party_paths_are_not_blocked(self):
        self.assertEqual(
            scan_paths(["README.md", "PRODUCT.md", "Cargo.toml",
                        "apps/studio/src/ModelContextInspector.tsx",
                        "docs/native-sdk.md"]), [],
        )


if __name__ == "__main__":
    unittest.main()
