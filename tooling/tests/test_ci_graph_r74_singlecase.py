"""Bounded R74 Graph one-case selection: source unit checks, never Native acceptance."""
import copy
from pathlib import Path
import re
import tempfile
import unittest

from tooling import ci_canvas_service as gate

TITLE = next(row[1] for row in gate.GRAPH_CASES if row[0] == "graph-flow")
FILE = "semantic-graph-service.spec.ts"


def original_listing(selected):
    rows = gate.graph_case_rows(selected)
    specs = [
        {"title": title, "id": "SOURCE_ONLY_INDEX_" + str(number), "file": FILE,
         "line": number + 1, "column": 1,
         "tests": [{"projectId": "chromium", "expectedStatus": "passed",
                    "timeout": 120000, "annotations": [], "results": []}]}
        for number, (_, title, _) in enumerate(rows)
    ]
    return {
        "errors": [], "config": {"workers": 1, "forbidOnly": True, "maxFailures": 1,
            "projects": [{"id": "chromium", "name": "chromium", "repeatEach": 1,
                          "retries": 0}]},
        "stats": {"expected": 0, "unexpected": 0, "flaky": 0, "skipped": len(specs)},
        "suites": [{"specs": specs, "suites": []}],
    }


class GraphFlowOriginalSelectionTests(unittest.TestCase):
    def test_default_still_has_all_six_exact_source_cases(self):
        names = [case for case, _, _ in gate.graph_case_rows()]
        self.assertEqual(len(names), 6)
        self.assertEqual(names, [case for case, _, _ in gate.GRAPH_CASES])
        self.assertEqual(gate.graph_selection_plan(), {
            "requested": gate.GRAPH_SCOPE, "required_tests": 6,
            "workers": 1, "retries": 0})

    def test_one_case_exactly_graph_flow_and_cannot_broaden(self):
        self.assertEqual(gate.graph_case_rows("graph-flow"),
                         (next(row for row in gate.GRAPH_CASES if row[0] == "graph-flow"),))
        self.assertEqual(gate.graph_selection_plan("graph-flow"), {
            "requested": gate.GRAPH_SCOPE, "required_tests": 1,
            "workers": 1, "retries": 0, "selected_case": "graph-flow"})
        for invalid in ("", "all", "graph-source-review", "graph-flow;graph-removal",
                        "graph-lost-reply", "../graph-flow", "*", "GRAPH-FLOW", False, 0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                gate.graph_selection_plan(invalid)

    def test_exact_original_playwright_command_is_not_an_env_only_filter(self):
        node, cli, output = "/usr/bin/node", "/original/app/node_modules/cli.js", Path("/original/e/graph-cases")
        whole = gate.graph_playwright_argv(node, cli, output)
        one = gate.graph_playwright_argv(node, cli, output, "graph-flow")
        self.assertEqual(one[:-2], whole)
        self.assertEqual(one[-2], "--grep")
        pattern = re.compile(one[-1])
        self.assertTrue(pattern.fullmatch("chromium " + FILE + " " + TITLE))
        self.assertFalse(pattern.fullmatch("firefox " + FILE + " " + TITLE))
        self.assertFalse(pattern.fullmatch("chromium " + FILE + " " + TITLE + " - malicious"))
        self.assertFalse(pattern.fullmatch("chromium " + FILE + " " + gate.GRAPH_CASES[0][1]))
        self.assertNotIn("--grep", whole)
        # Production must override the Studio 30s default: otherwise real
        # Native/Host startup, editable Graph UI and same-Store close all share
        # an unreviewably short per-test deadline.
        self.assertNotIn("--timeout=30000", whole)
        for flag in ("--workers=1", "--retries=0", "--repeat-each=1", "--max-failures=1",
                     "--forbid-only", "--reporter=json", "--project=chromium",
                     "--timeout=120000"):
            self.assertEqual(whole.count(flag), 1)
            self.assertEqual(one.count(flag), 1)

    def test_single_report_lists_only_original_one_without_executing(self):
        original = original_listing("graph-flow")
        got = gate.graph_report(original, listing=True, selected_case="graph-flow")
        self.assertEqual(got["selected"], 1)
        self.assertEqual(len(got["identities"]), 1)
        self.assertEqual(got["identities"][0]["title"], TITLE)
        self.assertEqual(gate.graph_report(original_listing(None), listing=True)["selected"], 6)

    def test_report_rejects_missing_foreign_extra_and_skipped_omissions(self):
        good = original_listing("graph-flow")
        for kind in ("missing", "extra", "foreign_title", "foreign_file", "skipped",
                     "retries", "test_id", "wrong_worker", "nonzero_expected"):
            report = copy.deepcopy(good)
            spec = report["suites"][0]["specs"][0]
            if kind == "missing":
                report["suites"][0]["specs"] = []
            elif kind == "extra":
                report["suites"][0]["specs"].append(copy.deepcopy(spec))
            elif kind == "foreign_title":
                spec["title"] = "unreviewed Graph case"
            elif kind == "foreign_file":
                spec["file"] = "forged.spec.ts"
            elif kind == "skipped":
                report["stats"]["skipped"] = 0
            elif kind == "retries":
                report["config"]["projects"][0]["retries"] = 1
            elif kind == "test_id":
                spec["id"] = ""
            elif kind == "wrong_worker":
                report["config"]["workers"] = 4
            else:
                report["stats"]["expected"] = 1
            with self.subTest(kind=kind), self.assertRaises((ValueError, KeyError)):
                gate.graph_report(report, listing=True, selected_case="graph-flow")
        with self.assertRaises(ValueError):
            gate.graph_report(good, listing=True)  # The six-case default cannot accept a one-case roster.

    def test_onecase_execution_requires_the_original_pass_without_retry_or_omission(self):
        list_doc = original_listing("graph-flow")
        roster = gate.graph_report(list_doc, listing=True, selected_case="graph-flow")
        good = copy.deepcopy(list_doc)
        good["stats"] = {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0}
        suite = good["suites"][0]
        suite["title"] = FILE
        spec = suite["specs"][0]
        spec["tags"] = []
        spec["ok"] = True
        test = spec["tests"][0]
        test["projectName"] = "chromium"
        test["status"] = "expected"
        test["results"] = [{"status": "passed", "retry": 0, "error": None, "errors": []}]
        accepted = gate.graph_report(good, listing=False, expected=roster, selected_case="graph-flow")
        self.assertEqual(accepted["passed"], 1)
        self.assertEqual(accepted["original_discovery_matched"], True)
        for tamper in ("retry", "skipped", "unexpected", "foreign_source", "identity_mismatch"):
            doc = copy.deepcopy(good)
            if tamper == "retry":
                doc["suites"][0]["specs"][0]["tests"][0]["results"][0]["retry"] = 1
            elif tamper == "skipped":
                doc["stats"]["skipped"] = 1
            elif tamper == "unexpected":
                doc["stats"]["unexpected"] = 1
            elif tamper == "foreign_source":
                doc["suites"][0]["specs"][0]["file"] = "different-graph.spec.ts"
            else:
                doc["suites"][0]["specs"][0]["id"] = "OTHER_SOURCE_TEST_ID"
            with self.subTest(tamper=tamper), self.assertRaises((ValueError, KeyError)):
                gate.graph_report(doc, listing=False, expected=roster, selected_case="graph-flow")
        with self.assertRaises(ValueError):
            gate.graph_report(good, listing=False, expected=roster)

    def test_selected_original_case_root_is_canonical_and_exclusive(self):
        with tempfile.TemporaryDirectory(prefix="r74-one-") as temp:
            cases = Path(temp).resolve()
            original = gate.graph_case_layout(cases, "graph-flow")["private_root"]
            original.mkdir()
            (original / "parent-source-binding.original.json").write_bytes(b"INERT_SOURCE_ROSTER_UNIT_TEST")
            rows = gate.graph_collected_case_roots(cases, selected_case="graph-flow")
            self.assertEqual(rows, [("graph-flow", original)])
            with self.assertRaises(ValueError):
                gate.graph_collected_case_roots(cases)  # Default cannot accept a partial six-case set.
            foreign = gate.graph_case_layout(cases, "graph-source-review")["private_root"]
            foreign.mkdir()
            (foreign / "parent-source-binding.original.json").write_bytes(b"FOREIGN_ORIGINAL_UNIT")
            with self.assertRaises(ValueError):
                gate.graph_collected_case_roots(cases, selected_case="graph-flow")

    def test_workflow_has_explicit_dispatch_choice_and_source_binds_mode(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/studio-service-verification.yml").read_text(encoding="utf-8")
        self.assertIn("graph_case_mode:", workflow)
        self.assertIn("options: [all, graph-flow]", workflow)
        self.assertEqual(workflow.count("MOTIONWRIGHT_GRAPH_CASE_MODE:"), 1)
        self.assertIn("MOTIONWRIGHT_GRAPH_CASE_MODE: ${{ inputs.graph_case_mode }}", workflow)
        self.assertIn("graph produce --repo .", workflow)
        self.assertIn("graph wait --repo .", workflow)
        self.assertIn("graph run --repo .", workflow)
        self.assertNotIn("graph-flow-automatic-approval", workflow)

if __name__ == "__main__":
    unittest.main()
