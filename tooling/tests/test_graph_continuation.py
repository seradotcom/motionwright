"""Closed continuation controls; no Service, Native, browser or network calls."""
import ast
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


    def four_index(self):
        deadline = copy.deepcopy(self.deadline)
        deadline["graph_continuation_selector"] = graph.GRAPH_FOUR_CONTINUATION
        index = copy.deepcopy(self.index)
        index["contract"]["execution"]["deadline_original"] = graph.graph_put_json(
            self.root / "four-deadline.original.json", deadline)
        record = graph.graph_continuation_record(self.source, deadline, self.cases_root)
        index["contract"]["execution"]["continuation"] = record
        return index, deadline, record

    def test_four_selects_only_never_started_titles_and_requires_four_report_rows(self):
        index, deadline, record = self.four_index()
        selected = graph.graph_selected_cases(index)
        self.assertEqual(selected, graph.GRAPH_CASES[2:])
        pattern = re.compile(graph.graph_playwright_selection(selected)[1])
        for case, title, _ in graph.GRAPH_CASES:
            self.assertEqual(bool(pattern.search("chromium semantic-graph-service.spec.ts " + title)),
                             case not in ("graph-source-review", "graph-flow"))
        self.assertEqual(graph.graph_report(self.report(selected), listing=True, selected=selected)["selected"], 4)
        for wrong in (graph.GRAPH_CASES, graph.GRAPH_CONTINUATION_CASES):
            with self.subTest(count=len(wrong)), self.assertRaises(ValueError):
                graph.graph_report(self.report(wrong), listing=True, selected=selected)
        plan = graph.graph_selection_plan(index)
        self.assertEqual((plan["required_tests"], plan["original_contract_tests"]), (4, 6))
        self.assertEqual(plan["historical_excluded_cases"], ["graph-source-review", "graph-flow"])
        self.assertIs(plan["original_contract_complete"], False)
        self.assertEqual(record["schema"], "motionwright.graph-unstarted-continuation/2")
        self.assertEqual(graph.graph_closed_status(index), "CLOSED_GRAPH_CONTINUATION_OBSERVATIONS")
        self.assertTrue(graph.graph_requested_continuation({graph.GRAPH_CONTINUATION_ENV: graph.GRAPH_FOUR_CONTINUATION}))

    def test_four_rejects_each_historical_runner_and_each_changed_unknown(self):
        index, deadline, record = self.four_index()
        for origin in (graph.GRAPH_CONTINUATION_ORIGIN, graph.GRAPH_FOUR_CONTINUATION_ORIGIN):
            for field in ("id", "runner_id", "runner_name"):
                wrong = copy.deepcopy(deadline)
                wrong["jobs"]["jobs"][0][field] = origin["job_id" if field == "id" else field]
                with self.subTest(run=origin["run_id"], field=field), self.assertRaises(ValueError):
                    graph.graph_continuation_record(self.source, wrong, self.cases_root)
            old = {**self.source, "run_id": origin["run_id"]}
            with self.assertRaises(ValueError):
                graph.graph_continuation_record(old, deadline,
                    graph.graph_cases_root(self.root / "v", old["run_id"], True))
        for name in ("origin", "prior_origins"):
            wrong = copy.deepcopy(index)
            origin = wrong["contract"]["execution"]["continuation"][name]
            if isinstance(origin, list):
                origin = origin[0]
            origin["excluded_case"]["effects"] = "NONE"
            with self.subTest(origin=name), self.assertRaises(ValueError):
                graph.graph_selected_cases(wrong)
        for key, value in (("prior_origins", []), ("required_current_cases", 5),
                           ("historical_cleanup_claimed", True),
                           ("historical_owner_credentials_or_tickets_reused", True),
                           ("simultaneous_owner_limit", 2), ("formal_acceptance", True)):
            wrong = copy.deepcopy(index)
            wrong["contract"]["execution"]["continuation"][key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                graph.graph_selected_cases(wrong)
        wrong = copy.deepcopy(deadline)
        wrong["graph_continuation_selector"] = graph.GRAPH_CONTINUATION
        with self.assertRaises(ValueError):
            graph.graph_selected_cases(index, wrong)

    def test_physical_four_roots_reject_both_excluded_owners_and_old_upload_namespace(self):
        index, deadline, record = self.four_index()
        self.cases_root.mkdir(parents=True)
        for case, _, _ in graph.GRAPH_FOUR_CONTINUATION_CASES:
            root = graph.graph_case_layout(self.cases_root, case)["private_root"]
            root.mkdir()
            (root / "parent-source-binding.original.json").write_bytes(b"{}\n")
        observed = graph.graph_collected_case_roots(self.cases_root, graph.GRAPH_FOUR_CONTINUATION_CASES)
        self.assertEqual([root.name for _, root in observed], ["g2", "g3", "g4", "g5"])
        for case in ("graph-source-review", "graph-flow"):
            excluded = graph.graph_case_layout(self.cases_root, case)["private_root"]
            excluded.mkdir()
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, "excluded historical case"):
                graph.graph_collected_case_roots(self.cases_root, graph.GRAPH_FOUR_CONTINUATION_CASES)
            excluded.rmdir()
        names = graph.graph_public_evidence_namespaces(self.root / "v", index)
        graph.graph_public_evidence_path("e/gc42359999999-1/g4/owner/owner-final.original.json", names)
        for old in ("gc42356210001-1", "gc42372165893-1", "graph-cases"):
            with self.subTest(namespace=old), self.assertRaises(ValueError):
                graph.graph_public_evidence_path("e/" + old + "/g4/owner/owner-final.original.json", names)

    def test_input_upload_metadata_is_admitted_only_in_the_four_case_clock(self):
        index, _, _ = self.four_index()
        filename = "tools/graph-origin-recipient-upload-admission.original.json"
        graph.graph_public_tool_path(filename, index)
        graph.graph_public_tool_path("tools/graph-phase-origin-inputs.terminal.original.json", index)
        self.assertEqual(sum(graph.graph_phase_plan(graph.GRAPH_FOUR_CONTINUATION).values()), 8430)
        for value in (self.index, {"contract": {"execution": {}}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                graph.graph_public_tool_path(filename, value)
        for name in ("tools/read-ticket.original.json", "tools/recipient.pkcs8.pem",
                     "tools/graph-origin-other-upload-admission.original.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                graph.graph_public_tool_path(name, index)



class GraphInputWindowBudgetTests(unittest.TestCase):
    def test_input_window_fits_original_job_and_preserves_future_reserves(self):
        plan = graph.graph_phase_plan(graph.GRAPH_FOUR_CONTINUATION)
        self.assertEqual(sum(plan.values()), 8430)
        self.assertEqual(10800 - sum(plan.values()), 2370)
        # Shared Native, runtime, review and custody reservations are unchanged.
        for name in set(plan) & set(graph.GRAPH_PHASES):
            with self.subTest(phase=name):
                self.assertEqual(plan[name], graph.GRAPH_PHASES[name])
        source = ast.parse((ROOT / "tooling/native_graph_build_transfer.py").read_text())
        constants = {}
        for node in source.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in {"POLL_SECONDS", "DOWNLOAD_SECONDS", "RECIPIENT_SECONDS"}):
                constants[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(plan["origin-inputs"], constants["POLL_SECONDS"])
        self.assertEqual(constants["DOWNLOAD_SECONDS"], 180)
        self.assertEqual(constants["RECIPIENT_SECONDS"], 2190)
        self.assertEqual(constants["POLL_SECONDS"] - constants["DOWNLOAD_SECONDS"] - 30, 1140)
        workflow = (ROOT / ".github/workflows/studio-service-verification.yml").read_text()
        branch = re.search(r'(?m)^          if continuation == "unstarted-after-42372165893":\n'
                           r'              phases = (\{[^}]+\})', workflow)
        self.assertIsNotNone(branch)
        bootstrap = ast.literal_eval(branch.group(1))
        self.assertEqual(bootstrap, plan)
        step = workflow.split("name: Receive one encrypted original build capsule with read-only Actions permission", 1)[1]
        minutes = int(re.search(r"timeout-minutes: ([0-9]+)", step).group(1))
        self.assertEqual(minutes, (plan["origin-inputs"] + 59) // 60)
        self.assertLess(minutes * 60 - plan["origin-inputs"], 60)


class GraphCurrentCustodyDependencyTests(unittest.TestCase):
    # Projection of the retained, authenticated current deadline from run42400480547:
    # 28710 bytes, sha256 79ab57bf94faa4d5e694f438d0e626d750764a0655cc00b0819adf79a80f86ac.
    # These are constructor controls only, not process, clock, Native or G2 evidence.
    ORIGINAL = {
        "binding": {"GITHUB_SHA": "deb4ce258e6efb99fca984b104f6f699fa6c7a5e",
                    "GITHUB_RUN_ID": "42400480547", "GITHUB_RUN_ATTEMPT": "1"},
        "jobs": {"jobs": [{"name": "Real SQLite and Studio / phase1-native-graph",
                           "id": 128487111120}]},
    }
    ORIGINAL_REF = {
        "path": "/home/runner/work/motionwright/motionwright/v/tools/graph-deadline.original.json",
        "size_bytes": 28710,
        "sha256": "79ab57bf94faa4d5e694f438d0e626d750764a0655cc00b0819adf79a80f86ac",
    }

    def setUp(self):
        self.original = copy.deepcopy(self.ORIGINAL)
        self.reference = copy.deepcopy(self.ORIGINAL_REF)
        self.repo = Path("/current-source")
        self.expected = {
            "source_sha": "deb4ce258e6efb99fca984b104f6f699fa6c7a5e",
            "run_id": "42400480547", "run_attempt": 1, "job_id": 128487111120,
            "deadline_original": copy.deepcopy(self.reference),
        }
        self.command = {
            "argv": [graph.sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                     "--only-binary=:all:", "--require-hashes", "-r",
                     str(self.repo / "tooling/requirements/mlt-g2-custody.txt")],
            "graph_job": copy.deepcopy(self.expected),
        }

    def check(self, command):
        return graph.graph_validate_current_custody_dependency(
            command, self.repo, self.original, self.reference)

    def test_current_writer_selects_clock_metadata_without_inventing_runtime_or_authority(self):
        environment = {**self.original["binding"], graph.GRAPH_TOKEN_ENV: "unit-only-unused-token",
                       "MOTIONWRIGHT_GRAPH_PRODUCER_RUN": "0", "CARGO_INCREMENTAL": "0"}
        before = copy.deepcopy(environment)
        result = graph.graph_input_environment(environment)
        self.assertEqual(result.pop("MOTIONWRIGHT_GRAPH_PRODUCER_RUN"), "1")
        self.assertEqual(result.pop("PYTHONNOUSERSITE"), "1")
        self.assertEqual(result.pop("PYTHONDONTWRITEBYTECODE"), "1")
        self.assertNotIn(graph.GRAPH_TOKEN_ENV, result)
        expected = {key: value for key, value in before.items()
                    if key not in (graph.GRAPH_TOKEN_ENV, "MOTIONWRIGHT_GRAPH_PRODUCER_RUN")}
        self.assertEqual(result, expected)
        self.assertEqual(environment, before)
        self.assertEqual(graph.graph_current_job_binding(self.original, self.reference), self.expected)

    def test_exact_current_dependency_is_accepted_without_changing_originals(self):
        before = copy.deepcopy((self.original, self.reference, self.command))
        self.check(self.command)
        self.assertEqual((self.original, self.reference, self.command), before)

    def test_missing_or_different_current_job_fails_closed_without_injecting_fields(self):
        cases = []
        missing = copy.deepcopy(self.command)
        del missing["graph_job"]
        cases.append(missing)
        for field, value in (("source_sha", "52d149ea0dd5550fbded28ad0ea337353f826210"),
                             ("run_id", "42356210001"), ("run_attempt", 2),
                             ("run_attempt", True), ("job_id", 128354875162),
                             ("job_id", 128487111120.0)):
            wrong = copy.deepcopy(self.command)
            wrong["graph_job"][field] = value
            cases.append(wrong)
        for field, value in (("sha256", "0" * 64), ("path", "/old/deadline.original.json"),
                             ("size_bytes", 1)):
            wrong = copy.deepcopy(self.command)
            wrong["graph_job"]["deadline_original"][field] = value
            cases.append(wrong)
        for command in cases:
            before = copy.deepcopy(command)
            with self.subTest(command=command), self.assertRaisesRegex(
                    ValueError, "Graph current custody dependency original"):
                self.check(command)
            self.assertEqual(command, before)

    def test_command_shape_and_ambiguous_current_job_are_rejected(self):
        wrong = copy.deepcopy(self.command)
        wrong["argv"][wrong["argv"].index("--require-hashes")] = "--no-deps"
        with self.assertRaisesRegex(ValueError, "Graph current custody dependency original"):
            self.check(wrong)
        actual = next(row for row in self.original["jobs"]["jobs"]
                      if row["name"] == "Real SQLite and Studio / " + graph.GRAPH_SCOPE)
        self.original["jobs"]["jobs"].append(copy.deepcopy(actual))
        with self.assertRaisesRegex(ValueError, "Graph current job is absent or ambiguous"):
            graph.graph_current_job_binding(self.original, self.reference)


if __name__ == "__main__":
    unittest.main()
