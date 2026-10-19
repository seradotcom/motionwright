"""Job applicability controls; fixtures execute Git and Python, never the product."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


TOOLING = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ci_job_plan_under_test", TOOLING / "ci_job_plan.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


def execution(sha="a" * 40, *, event="pull_request", ref="refs/pull/7/merge"):
    return {"GITHUB_REPOSITORY": "example/job-contract", "GITHUB_EVENT_NAME": event,
            "GITHUB_SHA": sha, "GITHUB_REF": ref, "GITHUB_RUN_ID": "11",
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKFLOW_SHA": sha,
            "GITHUB_WORKFLOW_REF": "example/job-contract/.github/workflows/ci.yml@" + ref}


def source_observation():
    return {"checkout_sha": "a" * 40, "tree_sha": "b" * 40, "parents": ["c" * 40, "d" * 40],
            "workflow_sha256": "1" * 64, "planner_git_sha256": "2" * 64,
            "planner_execution_sha256": "2" * 64, "cargo_lock_git_sha256": "3" * 64,
            "cargo_lock_worktree_sha256": "3" * 64}


def result_fixture(*, select_tests=True):
    """A synthetic plan for the aggregation protocol, not an admitted exclusion."""
    plan = {"schema": "motionwright.ci-job-plan/1", "workflow": ci.WORKFLOW,
            "execution_context": execution(), "source": source_observation(),
            "jobs": {job: {"selected": job in ci.ALWAYS or select_tests,
                           "reason": "synthetic-protocol-test"} for job in ci.JOBS}}
    plan["plan_id"] = ci.digest(ci.canonical(plan))
    needs = {"plan": {"result": "success", "outputs": {"plan_id": plan["plan_id"]}}}
    for job, choice in plan["jobs"].items():
        needs[job] = {"result": "success" if choice["selected"] else "skipped",
                      "outputs": {k: str(v) for k, v in plan["source"].items() if k != "parents"}
                      if choice["selected"] else {}}
        if choice["selected"]:
            needs[job]["outputs"].update(plan_id=plan["plan_id"], run_id="11", run_attempt="1", emitter_job=job)
    return plan, needs


class JobOutcomes(unittest.TestCase):
    def aggregate(self, plan, needs):
        return ci.aggregate(plan, needs, execution(), source_observation())

    def test_explicit_unselected_jobs_are_not_executions_or_test_passes(self):
        plan, needs = result_fixture(select_tests=False)
        result = self.aggregate(plan, needs)
        self.assertEqual(result["status"], "JOB_SELECTION_CONSISTENT")
        for job in ci.SELECTABLE:
            self.assertEqual(result["jobs"][job]["job_result"], "skipped")
            self.assertEqual(result["jobs"][job]["execution_claim"], "NOT_EXECUTED")
        for field in ("test_case_results_admitted", "formal_acceptance", "release_verdict"):
            self.assertIs(result[field], False)
        needs["rust-tests"]["result"] = "success"
        self.assertEqual(self.aggregate(plan, needs)["status"], "FAIL")

    def test_selected_failure_cancel_unknown_skip_and_missing_source_never_pass(self):
        plan, original = result_fixture()
        self.assertEqual(self.aggregate(plan, original)["status"], "JOB_SELECTION_CONSISTENT")
        for job in ci.JOBS:
            for state in ("failure", "cancelled", "skipped", "UNKNOWN", None):
                with self.subTest(job=job, state=state):
                    needs = copy.deepcopy(original)
                    needs[job]["result"] = state
                    result = self.aggregate(plan, needs)
                    self.assertEqual(result["status"], "FAIL")
                    self.assertIn(job, result["inconsistent_jobs"])
            with self.subTest(job=job, state="success-without-source"):
                needs = copy.deepcopy(original)
                needs[job]["outputs"] = {}
                self.assertEqual(self.aggregate(plan, needs)["status"], "FAIL")

    def test_other_sha_tree_run_attempt_or_workflow_is_not_transferable(self):
        plan, needs = result_fixture()
        for field, value in (("GITHUB_SHA", "e" * 40), ("GITHUB_RUN_ID", "12"),
                             ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_WORKFLOW_SHA", "f" * 40)):
            with self.subTest(field=field):
                observed = execution()
                observed[field] = value
                with self.assertRaises(ci.PlanError):
                    ci.aggregate(plan, needs, observed, source_observation())
        for field in ("checkout_sha", "tree_sha", "cargo_lock_git_sha256", "planner_execution_sha256"):
            with self.subTest(field=field):
                observed = source_observation()
                observed[field] = "0" * len(observed[field])
                with self.assertRaises(ci.PlanError):
                    ci.aggregate(plan, needs, execution(), observed)
                changed = copy.deepcopy(needs)
                changed["rust-tests"]["outputs"][field] = observed[field]
                self.assertEqual(self.aggregate(plan, changed)["status"], "FAIL")
        for field, value in (("plan_id", "f" * 64), ("run_id", "10"), ("run_attempt", "0"),
                             ("emitter_job", "studio-tests")):
            with self.subTest(job_output=field):
                changed = copy.deepcopy(needs)
                changed["rust-tests"]["outputs"][field] = value
                self.assertEqual(self.aggregate(plan, changed)["status"], "FAIL")

    def test_tampered_plan_incomplete_inventory_and_deselected_g0_are_rejected(self):
        original_plan, original_needs = result_fixture()
        for change in ("tampered-plan", "missing-job", "extra-job", "failed-plan", "deselected-g0"):
            with self.subTest(change=change):
                plan, needs = copy.deepcopy(original_plan), copy.deepcopy(original_needs)
                if change == "tampered-plan":
                    plan["jobs"]["rust-tests"]["selected"] = False
                elif change == "missing-job":
                    del needs["rust-tests"]
                elif change == "extra-job":
                    needs["unspecified"] = {"result": "success"}
                elif change == "failed-plan":
                    needs["plan"]["result"] = "failure"
                else:
                    plan["jobs"]["source-policy"]["selected"] = False
                    plan["plan_id"] = ci.digest(ci.canonical({k: v for k, v in plan.items() if k != "plan_id"}))
                    needs["plan"]["outputs"]["plan_id"] = plan["plan_id"]
                with self.assertRaises(ci.PlanError):
                    self.aggregate(plan, needs)


class SelectionBoundary(unittest.TestCase):
    def test_unknown_cross_module_and_incomplete_inputs_expand_all_tests(self):
        original = {"changed_paths": ["README.md"], "dependency_graphs": [{"issues": []}]}
        cases = [
            {"changed_paths": ["Cargo.lock"]},
            {"changed_paths": ["crates/domain/Cargo.toml"]},
            {"changed_paths": ["SOURCE_LOCK.json"]},
            {"changed_paths": ["apps/studio/src/main.tsx"]},
            {"changed_paths": ["crates/service/src/lib.rs"]},
            {"changed_paths": ["unknown-input.dat"]},
            {"unknown_paths": ["README.md"]},
            {"dependency_graphs": [{"issues": ["unresolved-runtime-input"]}]},
            {"dependency_graphs": []},
            {"changed_paths": []},
            {"candidate": True},
            {"blockers": ["missing-disposition"]},
            {"expansion_reasons": ["advanced-pr-base-requires-full-shadow-inventory"]},
        ]
        for delta in cases:
            with self.subTest(delta=delta):
                jobs = ci.select_jobs(dict(original, **delta), execution(), {"README.md"})
                self.assertTrue(all(row["selected"] for row in jobs.values()))
        for observed in (execution(event="push", ref="refs/heads/main"),
                         execution(event="workflow_dispatch", ref="refs/heads/candidate")):
            with self.subTest(event=observed["GITHUB_EVENT_NAME"]):
                jobs = ci.select_jobs(original, observed, {"README.md"})
                self.assertTrue(all(row["selected"] for row in jobs.values()))

    def test_missing_runtime_consumer_audit_keeps_docs_and_all_other_paths_selected(self):
        # This is deliberately stricter than the shadow provider's neutral names.
        self.assertIsNone(ci.NEUTRAL_INPUT_REVIEW)
        neutral, reason = ci.reviewed_neutral_paths(Path.cwd(), {})
        self.assertEqual(neutral, set())
        self.assertEqual(reason, "no-complete-neutral-input-review")
        for path in ("README.md", "LICENSE", "CONTRIBUTING.md", "tooling/tests/test_guard.py"):
            with self.subTest(path=path):
                jobs = ci.select_jobs({"changed_paths": [path], "dependency_graphs": [{"issues": []}]},
                                      execution(), neutral)
                self.assertTrue(all(row["selected"] for row in jobs.values()))


class GitSourceBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ci-job-plan-", dir=os.environ.get("MW_CI_JOB_TEST_TMP"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for name, raw in {
            ci.SELF: (TOOLING / "ci_job_plan.py").read_bytes(),
            ci.PROVIDER: (TOOLING / "ci_impact_plan.py").read_bytes(),
            ci.WORKFLOW: b"name: CI\non: [push, pull_request, workflow_dispatch]\njobs:\n  guard:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n",
            "Cargo.toml": b'[workspace]\nmembers = ["crates/domain"]\nresolver = "2"\n',
            "Cargo.lock": b"# Synthetic tracked lock; no Cargo command is executed.\nversion = 4\n",
            "crates/domain/Cargo.toml": b'[package]\nname = "motionwright-domain"\nversion = "0.1.0"\nedition = "2021"\n',
            "crates/domain/src/lib.rs": b"pub fn unused_fixture() {}\n",
            "README.md": b"Synthetic protocol fixture\n",
            ".gitignore": b"ignored/\njson.py\n",
        }.items():
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.git("config", "user.name", "CI protocol fixture")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.seq = 0

    def git(self, *args):
        process = subprocess.run(["git", "-C", str(self.repo), *args], env=ci.git_env(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        return process.stdout

    def commit(self, relative, raw, message):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.git("add", relative)
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD").strip()

    def invoke(self, operation="plan", *, observed=None, event=None, extra_env=None):
        self.seq += 1
        folder = self.root / str(self.seq)
        folder.mkdir()
        sha = self.git("rev-parse", "HEAD").strip()
        observed = observed or execution(sha, event="workflow_dispatch", ref="refs/heads/main")
        event_file = folder / "event.json"
        event_file.write_text(json.dumps(event or {}), encoding="utf-8")
        output, github_output = folder / "plan.json", folder / "github-output"
        argv = [sys.executable, "-I", "-B", str(self.repo / ci.SELF), operation,
                "--repo", str(self.repo), "--github-output", str(github_output)]
        if operation == "plan":
            argv += ["--event-path", str(event_file), "--output", str(output)]
        else:
            argv += ["--plan-id", "f" * 64]
        env = dict(os.environ, **observed, **(extra_env or {}))
        env["GITHUB_JOB"] = "source-policy"
        process = subprocess.run(argv, cwd=self.repo, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=45)
        return process, output, github_output

    def test_real_git_plan_and_job_observation_bind_raw_source_without_product_execution(self):
        process, path, outputs = self.invoke()
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        plan = json.loads(path.read_bytes())
        self.assertTrue(all(row["selected"] for row in plan["jobs"].values()))
        self.assertEqual(plan["source"]["checkout_sha"], self.base)
        self.assertEqual(plan["source"]["tree_sha"], self.git("rev-parse", "HEAD^{tree}").strip())
        provider_hash = hashlib.sha256((TOOLING / "ci_impact_plan.py").read_bytes()).hexdigest()
        self.assertEqual(plan["impact_provider_sha256"], provider_hash)
        expected_review = ("no-complete-neutral-input-review" if provider_hash == ci.REVIEWED_PROVIDER_SHA256
                           else "provider-review-required")
        self.assertEqual(plan["neutral_review_status"], expected_review)
        self.assertIs(plan["test_case_results_admitted"], False)
        self.assertIn("rust_tests=true\n", outputs.read_text())
        observed, _, observation_output = self.invoke("observe")
        self.assertEqual(observed.returncode, 0, observed.stderr.decode())
        self.assertIn("cargo_lock_git_sha256=", observation_output.read_text())

    def test_actual_pr_merge_is_distinct_from_trigger_head_and_old_checkout_is_rejected(self):
        self.git("checkout", "-qb", "feature")
        head = self.commit("README.md", b"Feature README\n", "feature")
        self.git("checkout", "-q", "main")
        self.git("merge", "--no-ff", "-qm", "synthetic merge", "feature")
        merge = self.git("rev-parse", "HEAD").strip()
        event = {"pull_request": {"base": {"sha": self.base}, "head": {"sha": head}}}
        process, path, _ = self.invoke(observed=execution(merge), event=event)
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        plan = json.loads(path.read_bytes())
        self.assertEqual(plan["source"]["checkout_sha"], merge)
        self.assertEqual(plan["event_source"]["head_sha"], head)
        self.assertEqual(plan["source"]["parents"], [self.base, head])
        self.assertNotEqual(merge, head)
        stale, path, _ = self.invoke(observed=execution(head), event=event)
        self.assertEqual(stale.returncode, 2)
        self.assertFalse(path.exists())

    def test_changed_lock_ignored_input_and_import_shim_prevent_planning_without_replay(self):
        lock = self.repo / "Cargo.lock"
        original = lock.read_bytes()
        lock.write_bytes(original + b"# regenerated\n")
        changed, path, _ = self.invoke()
        self.assertEqual(changed.returncode, 2)
        self.assertFalse(path.exists())
        lock.write_bytes(original)
        ignored = self.repo / "ignored/input.txt"
        ignored.parent.mkdir()
        ignored.write_text("unexpected prepared input", encoding="utf-8")
        unknown, path, _ = self.invoke()
        self.assertEqual(unknown.returncode, 2)
        self.assertFalse(path.exists())
        shutil.rmtree(ignored.parent)
        marker = self.root / "import-executed"
        (self.repo / "json.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
        shim, path, _ = self.invoke()
        self.assertEqual(shim.returncode, 2)
        self.assertFalse(path.exists())
        self.assertFalse(marker.exists())

    def test_changed_provider_is_not_executed_and_forces_full_scope(self):
        marker = self.root / "provider-executed"
        self.commit(ci.PROVIDER, ("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n").encode(),
                    "unreviewed provider fixture")
        process, path, _ = self.invoke()
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        plan = json.loads(path.read_bytes())
        self.assertTrue(all(row["selected"] for row in plan["jobs"].values()))
        self.assertEqual(plan["neutral_review_status"], "provider-review-required")
        self.assertIsNone(plan["impact_plan_id"])
        self.assertFalse(marker.exists())

    def test_raw_git_replacement_and_graft_do_not_change_source_identity(self):
        head = self.commit("README.md", b"Actual source\n", "actual")
        actual_tree = self.git("rev-parse", "HEAD^{tree}").strip()
        self.git("replace", head, self.base)
        graft = self.root / "grafts"
        graft.write_text(head + "\n", encoding="ascii")
        process, path, _ = self.invoke(extra_env={"GIT_GRAFT_FILE": str(graft), "GIT_DIR": str(self.root / "absent")})
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        plan = json.loads(path.read_bytes())
        self.assertEqual(plan["source"]["checkout_sha"], head)
        self.assertEqual(plan["source"]["tree_sha"], actual_tree)
        self.assertEqual(plan["source"]["parents"], [self.base])

    def test_output_cannot_overwrite_source_or_an_existing_receipt(self):
        source = self.repo / "README.md"
        before = source.read_bytes()
        with self.assertRaises(ci.PlanError):
            ci.write_new(source, {"bad": True}, self.repo)
        self.assertEqual(source.read_bytes(), before)
        output = self.root / "retained.json"
        ci.write_new(output, {"original": True}, self.repo)
        with self.assertRaises(FileExistsError):
            ci.write_new(output, {"replacement": True}, self.repo)
        self.assertEqual(json.loads(output.read_bytes()), {"original": True})
        with self.assertRaises(ci.PlanError):
            ci.write_new(self.root / "absent/../traversal.json", {}, self.repo)
        self.assertFalse((self.root / "absent").exists())


class Css13GateBoundary(unittest.TestCase):
    def test_disabled_and_nonboolean_gates_never_read_a_source_or_admit_a_mask(self):
        for disabled in (False, None, "true", 1):
            with self.subTest(disabled=disabled), mock.patch.object(ci, "CSS13_PILOT_ENABLED", disabled), \
                    mock.patch.object(ci, "git", side_effect=AssertionError("disabled gate read Git")):
                self.assertEqual(ci.reviewed_css13_inputs(Path.cwd(), {}, execution())["status"], "DISABLED")

    def test_malformed_review_is_not_a_truthy_approval(self):
        for change in ({"consumer_inventory_entries": True}, {"consumer_inventory_entries": 0},
                       {"consumer_inventory_sha256": "missing"}, {"reference_source_sha": "stale"},
                       {"schema": "other"}, {"human_accepted": True}):
            review = dict(ci.CSS13_INPUT_REVIEW, repository="example/job-contract", **change)
            with self.subTest(change=change), mock.patch.object(ci, "CSS13_PILOT_ENABLED", True), \
                    mock.patch.object(ci, "CSS13_INPUT_REVIEW", review):
                result = ci.reviewed_css13_inputs(Path.cwd(), {}, execution())
                self.assertEqual(result["status"], "NOT_APPLIED")
                self.assertEqual(result["fallback"], "FULL_CI")

    def test_mixed_mask_keeps_required_studio_browser_desktop_and_run_attempt_binding(self):
        plan, _ = result_fixture()
        plan["jobs"]["rust-tests"] = {"selected": False, "reason": "synthetic-css-mask"}
        plan["plan_id"] = ci.digest(ci.canonical({k: v for k, v in plan.items() if k != "plan_id"}))
        needs = {"plan": {"result": "success", "outputs": {"plan_id": plan["plan_id"]}}}
        for job, choice in plan["jobs"].items():
            outputs = {k: str(v) for k, v in plan["source"].items() if k != "parents"} if choice["selected"] else {}
            if choice["selected"]:
                outputs.update(plan_id=plan["plan_id"], run_id="11", run_attempt="1", emitter_job=job)
            needs[job] = {"result": "success" if choice["selected"] else "skipped", "outputs": outputs}
        result = ci.aggregate(plan, needs, execution(), source_observation())
        self.assertEqual(result["status"], "JOB_SELECTION_CONSISTENT")
        self.assertEqual(result["jobs"]["rust-tests"]["execution_claim"], "NOT_EXECUTED")
        for job in ("source-policy", "rust-core", "studio", "studio-tests", "browser-acceptance", "desktop-linux"):
            for state in ("skipped", "failure", "cancelled"):
                with self.subTest(job=job, state=state):
                    bad = copy.deepcopy(needs)
                    bad[job]["result"] = state
                    self.assertEqual(ci.aggregate(plan, bad, execution(), source_observation())["status"], "FAIL")
        for field, value in (("run_attempt", "2"), ("run_id", "12"), ("plan_id", "f" * 64),
                             ("checkout_sha", "e" * 40), ("emitter_job", "rust-tests")):
            with self.subTest(field=field):
                bad = copy.deepcopy(needs)
                bad["desktop-linux"]["outputs"][field] = value
                self.assertEqual(ci.aggregate(plan, bad, execution(), source_observation())["status"], "FAIL")
        bad = copy.deepcopy(needs)
        bad["rust-tests"]["outputs"] = {"prior_pass": "true"}
        self.assertEqual(ci.aggregate(plan, bad, execution(), source_observation())["status"], "FAIL")
        self.assertIs(result["test_case_results_admitted"], False)
        self.assertIs(result["formal_acceptance"], False)


class Css13GitBoundary(unittest.TestCase):
    # Reuse only fixture operations, not inherited old test methods.
    setUp = GitSourceBoundary.setUp
    git = GitSourceBoundary.git
    commit = GitSourceBoundary.commit
    invoke = GitSourceBoundary.invoke

    def install_css_fixture(self, *, enabled=True):
        """A synthetic repository review, never evidence for reference951."""
        # Authenticate the LF provider copied into this synthetic Git history.
        # Windows checkout conversion must not replace the reviewed blob.
        provider = self.repo / ci.PROVIDER
        normalized = provider.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(ci.digest(normalized), ci.REVIEWED_PROVIDER_SHA256)
        provider.write_bytes(normalized)
        for name in ci.CSS13_PATHS:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(".synthetic-fixture { color: red; }\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "synthetic CSS sources")
        inventory = []
        for row in ci.git(self.repo, "ls-tree", "-r", "-z", "HEAD").split(b"\0"):
            if row:
                metadata, name = row.split(b"\t", 1)
                mode, kind, oid = metadata.decode().split()
                self.assertEqual(kind, "blob")
                if name.decode() not in {*ci.CSS13_PATHS, ci.SELF}:
                    inventory.append([name.decode(), mode, oid])
        self.css_review = dict(ci.CSS13_INPUT_REVIEW, repository="example/job-contract",
                               consumer_inventory_entries=len(inventory),
                               consumer_inventory_sha256=ci.digest(ci.canonical(sorted(inventory))))
        text = (self.repo / ci.SELF).read_text()
        lines = text.splitlines(keepends=True)
        replacements = {"CSS13_PILOT_ENABLED": enabled, "CSS13_INPUT_REVIEW": self.css_review}
        for node in reversed(ast.parse(text).body):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in replacements:
                    lines[node.lineno - 1:node.end_lineno] = [name + " = " + repr(replacements[name]) + "\n"]
        self.base = self.commit(ci.SELF, "".join(lines).encode(), "synthetic private review and feature gate")
        self.git("checkout", "-qb", "feature")

    def css_change(self, raw=b".synthetic-fixture { color: blue; }\n"):
        return self.commit(ci.CSS13_PATHS[0], raw, "synthetic CSS-only change")

    def plan_pr(self, head=None, *, checkout=None, base=None):
        head = head or self.git("rev-parse", "HEAD").strip()
        event = {"pull_request": {"base": {"sha": base or self.base}, "head": {"sha": head}}}
        process, path, output = self.invoke(observed=execution(checkout or head), event=event)
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        return json.loads(path.read_bytes()), output.read_text()

    def assert_full(self, plan):
        self.assertTrue(all(row["selected"] for row in plan["jobs"].values()), plan)
        self.assertNotEqual(plan["css13_review"]["status"], "APPLIED")

    def test_real_git_css_merge_omits_only_rust_tests_after_both_tree_checks(self):
        self.install_css_fixture()
        head = self.css_change()
        self.git("checkout", "-q", "main")
        self.git("merge", "--no-ff", "-qm", "synthetic exact merge", "feature")
        merge = self.git("rev-parse", "HEAD").strip()
        plan, output = self.plan_pr(head, checkout=merge)
        self.assertEqual(plan["css13_review"]["status"], "APPLIED")
        self.assertEqual([job for job, row in plan["jobs"].items() if not row["selected"]], ["rust-tests"])
        self.assertEqual({row["source_sha"] for row in plan["css13_review"]["compared_inputs"]}, {self.base, head, merge})
        self.assertTrue(all(len(row["css_blobs"]) == 13 for row in plan["css13_review"]["compared_inputs"]))
        self.assertIn("rust_tests=false\n", output)
        self.assertIn("studio_tests=true\n", output)
        self.assertIn("browser_acceptance=true\n", output)
        self.assertEqual(plan["source"]["checkout_sha"], merge)
        self.assertNotEqual(merge, head)
        self.assertIs(plan["css13_review"]["execution_result_reused"], False)

    def test_disabled_feature_and_main_manual_keep_full_scope_on_real_git(self):
        self.install_css_fixture(enabled=False)
        head = self.css_change()
        plan, _ = self.plan_pr(head)
        self.assert_full(plan)
        self.assertEqual(plan["css13_review"]["status"], "DISABLED")
        for event, ref, body in (("push", "refs/heads/main", {"before": self.base, "after": head}),
                                 ("workflow_dispatch", "refs/heads/feat/candidate", {})):
            with self.subTest(event=event):
                process, path, _ = self.invoke(observed=execution(head, event=event, ref=ref), event=body)
                self.assertEqual(process.returncode, 0, process.stderr.decode())
                self.assert_full(json.loads(path.read_bytes()))

    def test_equal_unreviewed_consumer_on_base_and_head_and_git_replace_keep_full(self):
        self.install_css_fixture()
        reviewed_base = self.base
        self.base = self.commit("crates/domain/src/lib.rs", b"pub fn new_reader_boundary() {}\n", "unreviewed consumer")
        unsafe_head = self.css_change()
        plan, _ = self.plan_pr(unsafe_head)
        self.assertEqual(plan["changed_paths"], [ci.CSS13_PATHS[0]])
        self.assert_full(plan)
        self.assertIn("consumer inventory changed", plan["css13_review"]["reason"])
        self.git("checkout", "-qb", "safe-lookalike", reviewed_base)
        safe_head = self.css_change()
        self.git("checkout", "-q", unsafe_head)
        self.git("replace", unsafe_head, safe_head)
        replaced, _ = self.plan_pr(unsafe_head)
        self.assert_full(replaced)
        self.assertEqual(replaced["source"]["checkout_sha"], unsafe_head)

    def test_enabled_gate_still_keeps_css_only_main_and_manual_full(self):
        self.install_css_fixture()
        head = self.css_change()
        for event, ref, body in (("push", "refs/heads/main", {"before": self.base, "after": head}),
                                 ("workflow_dispatch", "refs/heads/feat/candidate", {})):
            with self.subTest(event=event):
                process, path, _ = self.invoke(observed=execution(head, event=event, ref=ref), event=body)
                self.assertEqual(process.returncode, 0, process.stderr.decode())
                plan = json.loads(path.read_bytes())
                self.assert_full(plan)
                self.assertEqual(plan["css13_review"]["status"], "NOT_APPLIED")

    def test_changed_locks_workflow_tests_or_new_reader_keep_full(self):
        self.install_css_fixture()
        for name, raw in (("Cargo.lock", b"# changed lock\nversion = 4\n"),
                          (ci.WORKFLOW, b"name: changed CI\non: [push]\njobs: {}\n"),
                          ("tooling/tests/new_reader.py", b"# a new test input needs review\n"),
                          ("apps/studio/src/main.tsx", b"// changed app input\n"),
                          (".cargo/config.toml", b"[build]\njobs = 1\n")):
            with self.subTest(path=name):
                self.git("reset", "--hard", self.base)
                self.commit(name, raw, "changed non-CSS consumer input")
                head = self.css_change()
                plan, _ = self.plan_pr(head)
                self.assert_full(plan)

    def test_added_removed_renamed_or_mode_changed_css_cannot_use_modification_profile(self):
        self.install_css_fixture()
        path = self.repo / ci.CSS13_PATHS[0]
        for status in ("A", "D", "R", "T"):
            with self.subTest(status=status):
                self.git("reset", "--hard", self.base)
                if status == "A":
                    extra = "apps/studio/src/new.css"
                    (self.repo / extra).write_text(".extra { color: red; }\n")
                    self.git("add", extra)
                elif status == "D":
                    self.git("rm", ci.CSS13_PATHS[0])
                elif status == "R":
                    self.git("mv", ci.CSS13_PATHS[0], "apps/studio/src/renamed.css")
                else:
                    path.chmod(0o755)
                    self.git("update-index", "--chmod=+x", ci.CSS13_PATHS[0])
                self.git("commit", "-qm", "out-of-profile CSS source status")
                head = self.git("rev-parse", "HEAD").strip()
                plan, _ = self.plan_pr(head)
                self.assert_full(plan)

    def test_resource_escape_binary_budget_and_missing_unchanged_css_keep_full(self):
        self.install_css_fixture()
        for raw in (b'.x { background: url("external.png"); }\n', b'@import "other.css";\n',
                    b'.x { background: u/**/rl("external.png"); }\n', b'.x { color: \\72 ed; }\n',
                    b'/* unclosed', b'\xff\n', b'x' * 131_073):
            with self.subTest(raw_prefix=raw[:36]):
                self.git("reset", "--hard", self.base)
                head = self.css_change(raw)
                plan, _ = self.plan_pr(head)
                self.assert_full(plan)
        self.git("reset", "--hard", self.base)
        self.git("rm", ci.CSS13_PATHS[1])
        self.git("commit", "-qm", "synthetic missing old CSS source")
        self.base = self.git("rev-parse", "HEAD").strip()
        head = self.css_change()
        plan, _ = self.plan_pr(head)
        self.assertEqual(plan["changed_paths"], [ci.CSS13_PATHS[0]])
        self.assert_full(plan)

    def test_trimmed_graph_range_wrong_context_candidate_and_shallow_cannot_admit(self):
        self.install_css_fixture()
        head = self.css_change()
        provider, _ = ci.load_provider(self.repo, head)
        impact = provider.build_plan(self.repo, base=self.base, head=head, checkout=head,
                                     event="pull_request", repository="example/job-contract", candidate=False,
                                     allow_advanced_pr_base=True)
        variants = []
        for key, value in (("candidate", True), ("unknown_paths", [ci.CSS13_PATHS[0]]),
                           ("expansion_reasons", ["advanced-pr-base-requires-full-shadow-inventory"]),
                           ("change_ranges", []), ("dependency_graphs", impact["dependency_graphs"][:-1]),
                           ("changes", [{"status": "R100", "paths": [ci.CSS13_PATHS[0], ci.CSS13_PATHS[1]]}])):
            variants.append((key, dict(impact, **{key: value}), execution(head)))
        variants.append(("wrong-source", impact, execution("f" * 40)))
        variants.append(("enabled-main", impact, execution(head, event="push", ref="refs/heads/main")))
        variants.append(("enabled-manual", impact, execution(head, event="workflow_dispatch", ref="refs/heads/feat/candidate")))
        advanced = copy.deepcopy(impact)
        advanced["source"]["pr_base_reconciliation"]["advanced"] = True
        variants.append(("advanced-base-without-expansion-label", advanced, execution(head)))
        with mock.patch.object(ci, "CSS13_PILOT_ENABLED", True), mock.patch.object(ci, "CSS13_INPUT_REVIEW", self.css_review):
            self.assertEqual(ci.reviewed_css13_inputs(self.repo, impact, execution(head))["status"], "APPLIED")
            for label, changed, context in variants:
                with self.subTest(label=label):
                    self.assertEqual(ci.reviewed_css13_inputs(self.repo, changed, context)["status"], "NOT_APPLIED")
            (self.repo / ".git/shallow").write_text(self.base + "\n", encoding="ascii")
            result = ci.reviewed_css13_inputs(self.repo, impact, execution(head))
            self.assertEqual(result["status"], "NOT_APPLIED")
            self.assertIn("Complete CSS comparison history", result["reason"])


if __name__ == "__main__":
    unittest.main()
