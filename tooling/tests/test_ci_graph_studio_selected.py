"""Selected Studio Graph G1 contract; never Graph Native/Host/Store physical acceptance."""

from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[2]
WF = REPO / ".github/workflows/graph-development-fast.yml"

VITEST_TITLES = (
    "removes the original saved UUID",
    "still denies removal without the original source admission",
    "suggests the earliest non-overlapping authored frame",
    "uses the next edge when the first edge is full",
)
PLAYWRIGHT_TITLES = (
    "Graph removal uses the saved source",
    "Graph flow adds sequential exact-frame events",
    "Graph stale waypoint typing after removal cannot overwrite its successor",
    "Vertex removal cannot silently remove edges, and flow exposes actual synthetic event controls",
)


class SelectedGraphStudioG1(unittest.TestCase):
    def setUp(self):
        self.workflow = WF.read_text(encoding="utf-8")
        self.assertNotIn("\r", self.workflow)
        self.assertIn("  graph-source-intake:", self.workflow)

    def selected(self):
        self.assertEqual(self.workflow.count("  studio-graph-selected:\n"), 1)
        return self.workflow.split("  studio-graph-selected:\n", 1)[1]

    def test_manual_studio_choice_does_not_expand_push_criteria(self):
        self.assertIn("verification_scope:", self.workflow)
        self.assertIn("options: [source, studio-selected]", self.workflow)
        self.assertIn("default: source", self.workflow)
        self.assertIn(
            "github.event_name != 'workflow_dispatch' || inputs.verification_scope == 'source'",
            self.workflow,
        )
        job = self.selected()
        self.assertIn("github.event_name == 'workflow_dispatch'", job)
        self.assertIn("github.event_name == 'push'", job)
        self.assertIn("inputs.verification_scope == 'studio-selected'", job)
        self.assertIn("runs-on: ubuntu-24.04", job)
        self.assertNotIn("workflow_dispatch:", job)
        self.assertEqual(self.workflow.count("  push:\n"), 1)

    def test_selected_studio_source_contract_is_self_watched_without_browser_churn(self):
        # An edit to the inert selection test must run its source check,
        # without scheduling the eight component tests for that edit alone.
        from fnmatch import fnmatchcase

        lines = self.workflow.splitlines()
        begin = lines.index("    paths:") + 1
        watches = []
        for line in lines[begin:]:
            if not line.startswith("      - "):
                break
            watches.append(line.strip()[2:].strip("'"))
        file = "tooling/tests/test_ci_graph_studio_selected.py"
        source_lane = self.workflow.split("  graph-source-intake:", 1)[1].split(
            "  studio-graph-selected:", 1
        )[0]
        impact = self.selected().split("          impact = {", 1)[1].split(
            "          changed = ", 1
        )[0]
        with self.subTest(check="self_watch"):
            self.assertTrue(any(fnmatchcase(file, path) for path in watches))
        with self.subTest(check="source_execution"):
            self.assertEqual(
                source_lane.count("tooling.tests.test_ci_graph_studio_selected"), 1
            )
        with self.subTest(check="no_browser_churn"):
            self.assertNotIn(file, impact)
        with self.subTest(check="no_unrelated_docs"):
            self.assertFalse(any(fnmatchcase("docs/roadmap.md", path) for path in watches))

    def test_exact_published_sha_and_eight_source_tests_are_required(self):
        job = self.selected()
        self.assertIn("EXPECTED_GRAPH_SHA", job)
        self.assertIn("GRAPH_SELECTED_BASE", job)
        self.assertIn("NOT_RUN_SOURCE_ROSTER_OR_IMPACT", job)
        self.assertIn("changed & impact", job)
        self.assertIn('test "$EXPECTED_GRAPH_SHA" = "$GITHUB_SHA"', job)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', job)
        self.assertIn('subprocess.run(["python3", "-B", "tooling/source_policy.py"], check=True)', job)
        self.assertIn("selected_titles =", job)
        for title in VITEST_TITLES + PLAYWRIGHT_TITLES:
            self.assertIn(title, job)
        self.assertIn("read_text(encoding=\"utf-8\").count(title) == 1", job)

    def test_run_exact_four_vitest_and_four_chromium_tests(self):
        job = self.selected()
        self.assertEqual(job.count("src/semanticGraphDraft.test.ts"), 3)
        self.assertEqual(job.count("src/semanticGraph.test.ts"), 3)
        self.assertIn("vitest run", job)
        self.assertIn("testNamePattern", job)
        self.assertIn("playwright test", job)
        self.assertIn("tests/semantic-graph-authority.spec.ts", job)
        self.assertIn("--project=chromium --workers=1", job)
        self.assertIn("Tests", job)
        self.assertIn("4 passed", job)
        self.assertIn("source-sha.original.txt", job)
        self.assertEqual(job.count("steps.source_case.outputs.selected == 'true'"), 5)

    def test_job_cannot_run_native_or_claim_physical_g2(self):
        job = self.selected()
        for forbidden in (
            "cargo test",
            "cargo run",
            "graph-flow",
            "status:write",
            "actions: write",
            "studio-service-verification.yml",
            "MOTIONWRIGHT_GRAPH_METADATA_TOKEN",
        ):
            self.assertNotIn(forbidden, job)
        self.assertIn("No native Graph effects", job)
        self.assertIn("if: always()", job)
        self.assertIn("retention-days: 7", job)

    def test_source_lane_is_still_present_and_bounded(self):
        source_lane = self.workflow.split("  graph-source-intake:\n", 1)[1].split(
            "  studio-graph-selected:\n", 1
        )[0]
        self.assertIn("timeout-minutes: 8", source_lane)
        self.assertIn("tooling/ci_graph_dev_fast.py", source_lane)
        self.assertIn("tooling.tests.test_ci_graph_r74_singlecase", source_lane)
        self.assertIn("graph-fast-source-", source_lane)


if __name__ == "__main__":
    unittest.main()
