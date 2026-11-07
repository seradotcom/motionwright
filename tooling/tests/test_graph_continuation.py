"""Closed continuation controls; no Service, Native, browser or network calls."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("graph_continuation_controller", ROOT / "tooling/ci_canvas_service.py")
graph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph)


class GraphContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gc-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = {"repository_root": str(self.root / "g"), "sha": "a" * 40,
                       "run_id": "42359999999", "run_attempt": 1}
        self.deadline = {
            "graph_continuation_selector": graph.GRAPH_CONTINUATION,
            "binding": {"RUNNER_NAME": "GitHub Actions 2000000001"},
            "jobs": {"jobs": [{"name": "Real SQLite and Studio / " + graph.GRAPH_SCOPE,
                "id": 128400000001, "runner_id": 2000000001, "runner_name": "GitHub Actions 2000000001",
                "run_id": int(self.source["run_id"]), "run_attempt": 1, "head_sha": self.source["sha"],
                "status": "in_progress", "conclusion": None}]}}
        self.cases_root = graph.graph_cases_root(self.root / "v", self.source["run_id"], True)
        self.record = graph.graph_continuation_record(self.source, self.deadline, self.cases_root)
        self.deadline_ref = graph.graph_put_json(self.root / "deadline.original.json", self.deadline)
        self.index = {"source": self.source, "contract": {"execution": {
            "cases_root": str(self.cases_root), "deadline_original": self.deadline_ref,
            "continuation": self.record}}}

    def test_six_case_contract_and_callback_caps_remain_unchanged(self):
        self.assertEqual(len(graph.GRAPH_CASES), 6)
        self.assertEqual(graph.GRAPH_CONTINUATION_CASES, graph.GRAPH_CASES[1:])
        self.assertEqual(graph.GRAPH_CASES[0][0], "graph-source-review")
        self.assertEqual([row[2] for row in graph.GRAPH_CASES],
                         [(5, 0), (2, 0), (5, 0), (2, 2, 0), (5, 2, 0), (5, 2, 0)])
        self.assertEqual(graph.graph_selected_cases(self.index), graph.GRAPH_CONTINUATION_CASES)

    def test_new_run_runner_and_namespace_are_all_required(self):
        for field in ("id", "runner_id", "runner_name"):
            wrong = copy.deepcopy(self.deadline)
            wrong["jobs"]["jobs"][0][field] = graph.GRAPH_CONTINUATION_ORIGIN[
                "job_id" if field == "id" else field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                graph.graph_continuation_record(self.source, wrong, self.cases_root)
        for root in (graph.GRAPH_CONTINUATION_ORIGIN["cases_root"], self.root / "v/e/graph-cases"):
            with self.subTest(root=root), self.assertRaises(ValueError):
                graph.graph_continuation_record(self.source, self.deadline, root)
        old = {**self.source, "run_id": graph.GRAPH_CONTINUATION_ORIGIN["run_id"]}
        with self.assertRaises(ValueError):
            graph.graph_continuation_record(old, self.deadline,
                graph.graph_cases_root(self.root / "v", old["run_id"], True))

    def test_unknown_and_resource_guarantees_cannot_be_changed(self):
        for field, value in (("selected_cases", [row[0] for row in graph.GRAPH_CASES]),
                             ("historical_cleanup_claimed", True),
                             ("historical_owner_credentials_or_tickets_reused", True),
                             ("simultaneous_owner_limit", 2), ("formal_acceptance", True)):
            wrong = copy.deepcopy(self.index)
            wrong["contract"]["execution"]["continuation"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                graph.graph_selected_cases(wrong)
        wrong = copy.deepcopy(self.index)
        wrong["contract"]["execution"]["continuation"]["origin"]["excluded_case"]["effects"] = "NONE"
        with self.assertRaises(ValueError):
            graph.graph_selected_cases(wrong)

    def test_bootstrap_selector_and_deadline_original_are_required(self):
        for value in (None, "none", "all", "unstarted-after-42356210002"):
            wrong = copy.deepcopy(self.deadline)
            wrong["graph_continuation_selector"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                graph.graph_selected_cases(self.index, wrong)
        self.assertFalse(graph.graph_requested_continuation({}))
        self.assertTrue(graph.graph_requested_continuation({graph.GRAPH_CONTINUATION_ENV: graph.GRAPH_CONTINUATION}))
        with self.assertRaises(ValueError):
            graph.graph_requested_continuation({graph.GRAPH_CONTINUATION_ENV: "all"})

    def test_grep_selects_exact_five_and_never_unknown_or_near_title(self):
        arguments = graph.graph_playwright_selection(graph.GRAPH_CONTINUATION_CASES)
        self.assertEqual(arguments[0], "--grep")
        pattern = re.compile(arguments[1])
        for case, title, _ in graph.GRAPH_CASES:
            self.assertEqual(bool(pattern.search("chromium semantic-graph-service.spec.ts " + title)),
                             case != "graph-source-review")
        self.assertIsNone(pattern.search(graph.GRAPH_CASES[1][1] + " extra"))
        self.assertEqual(graph.graph_playwright_selection(graph.GRAPH_CASES), [])
        with self.assertRaises(ValueError):
            graph.graph_playwright_selection(graph.GRAPH_CASES[:5])

    def report(self, rows):
        return {"errors": [], "config": {"workers": 1, "forbidOnly": True, "maxFailures": 1,
            "projects": [{"id": "chromium", "name": "chromium", "repeatEach": 1, "retries": 0}]},
            "stats": {"expected": 0, "unexpected": 0, "flaky": 0, "skipped": len(rows)},
            "suites": [{"specs": [{"id": case, "title": title, "file": "semantic-graph-service.spec.ts",
                "line": n + 1, "column": 1, "tests": [{"expectedStatus": "passed", "annotations": [],
                "timeout": 120000, "projectId": "chromium", "results": []}]}
                for n, (case, title, _) in enumerate(rows)], "suites": []}]}

    def test_actual_report_validator_requires_five_and_rejects_six(self):
        result = graph.graph_report(self.report(graph.GRAPH_CONTINUATION_CASES), listing=True,
                                    selected=graph.GRAPH_CONTINUATION_CASES)
        self.assertEqual(result["selected"], 5)
        self.assertEqual(result["status"], "LISTED_NOT_EXECUTED")
        with self.assertRaises(ValueError):
            graph.graph_report(self.report(graph.GRAPH_CASES), listing=True,
                               selected=graph.GRAPH_CONTINUATION_CASES)
        wrong = self.report(graph.GRAPH_CONTINUATION_CASES)
        wrong["suites"][0]["specs"][0]["tests"][0]["results"] = [{"status": "passed"}]
        with self.assertRaises(ValueError):
            graph.graph_report(wrong, listing=True, selected=graph.GRAPH_CONTINUATION_CASES)

    def test_five_closure_cannot_be_presented_as_original_six_closure(self):
        self.assertEqual(graph.graph_closed_status(self.index), "CLOSED_GRAPH_CONTINUATION_OBSERVATIONS")
        plan = graph.graph_selection_plan(self.index)
        self.assertEqual(plan["required_tests"], 5)
        self.assertEqual(plan["original_contract_tests"], 6)
        self.assertIs(plan["original_contract_complete"], False)
        self.assertEqual(plan["historical_effects"], "UNKNOWN")
        self.assertFalse(self.record["historical_cleanup_claimed"])
        self.assertFalse(self.record["formal_acceptance"])

    def test_physical_five_roots_reject_excluded_owner_and_aliases(self):
        self.cases_root.mkdir(parents=True)
        for case, _, _ in graph.GRAPH_CONTINUATION_CASES:
            root = graph.graph_case_layout(self.cases_root, case)["private_root"]
            root.mkdir()
            (root / "parent-source-binding.original.json").write_bytes(b"{}\n")
        observed = graph.graph_collected_case_roots(self.cases_root, graph.GRAPH_CONTINUATION_CASES)
        self.assertEqual(len(observed), 5)
        excluded = graph.graph_case_layout(self.cases_root, "graph-source-review")["private_root"]
        excluded.mkdir()
        with self.assertRaisesRegex(ValueError, "excluded historical case"):
            graph.graph_collected_case_roots(self.cases_root, graph.GRAPH_CONTINUATION_CASES)
        excluded.rmdir()
        alias = self.root / "alias"
        alias.symlink_to(self.cases_root, target_is_directory=True)
        with self.assertRaises(ValueError):
            graph.graph_collected_case_roots(alias, graph.GRAPH_CONTINUATION_CASES)

    def test_public_upload_whitelist_uses_only_exact_namespace_from_admitted_index(self):
        names = graph.graph_public_evidence_namespaces(self.root / "v", self.index)
        self.assertEqual(names, frozenset(("graph-producer", "gc42359999999-1")))
        for name in ("e/gc42359999999-1/g1/owner/owner-final.original.json",
                     "e/graph-producer/producer-index.original.json", "e/graph-cases.stdout.log"):
            graph.graph_public_evidence_path(name, names)
        for other in ("graph-cases", "gc42356210001-1", "gc42359999998-1", "gc42359999999-1-extra"):
            with self.subTest(namespace=other), self.assertRaisesRegex(ValueError, "unselected original"):
                graph.graph_public_evidence_path("e/" + other + "/g1/owner/owner-final.original.json", names)
        wrong = copy.deepcopy(self.index)
        wrong["contract"]["execution"]["cases_root"] = str(self.root / "v/e/gc42359999998-1")
        with self.assertRaises(ValueError):
            graph.graph_public_evidence_namespaces(self.root / "v", wrong)
        original = copy.deepcopy(self.index)
        del original["contract"]["execution"]["continuation"]
        original["contract"]["execution"]["cases_root"] = str(self.root / "v/e/graph-cases")
        normal = graph.graph_public_evidence_namespaces(self.root / "v", original)
        self.assertEqual(normal, frozenset(("graph-producer", "graph-cases")))
        graph.graph_public_evidence_path("e/graph-cases/g6/owner/owner-final.original.json", normal)
        with self.assertRaises(ValueError):
            graph.graph_public_evidence_path("e/gc42359999999-1/g1/owner/owner-final.original.json", normal)


if __name__ == "__main__":
    unittest.main()
