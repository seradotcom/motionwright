"""Job applicability controls; fixtures execute Git and Python, never the product."""

from __future__ import annotations

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


class ClosedInputProcessTests(unittest.TestCase):
    """Exercise the actual pinned provider and job CLI in disposable Git fixtures."""

    setUp = GitSourceBoundary.setUp
    git = GitSourceBoundary.git
    commit = GitSourceBoundary.commit
    invoke = GitSourceBoundary.invoke

    def prepare_reviewed_fixture(self):
        import ast

        for relative, raw in {
            "CONTRIBUTING.md": b"Synthetic contribution prose.\n",
            "tooling/conformance_history_guard.py": b"VALUE = 1\n",
            ".github/workflows/verification-plan.yml": b"name: Required G0 fixture\n",
            ".github/workflows/history-archive-conformance.yml": b"name: History source fixture\n",
            ".github/workflows/security-delivery.yml": b"name: Broad discovery fixture\n",
        }.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.git("add", ".")
        self.git("commit", "-qm", "reviewable synthetic consumers")
        provider_path = self.repo / ci.PROVIDER
        raw = provider_path.read_text()
        scope = {"__file__": str(provider_path), "__name__": "synthetic_provider_fixture"}
        exec(compile(raw, str(provider_path), "exec"), scope)
        review = copy.deepcopy(scope["REVIEWED_NONPRODUCT_INPUTS"])
        excluded = scope["NONPRODUCT_DOCUMENTS"] | scope["NONPRODUCT_PLANNERS"] | {scope["NONPRODUCT_HISTORY"]}
        inventory = []
        for row in self.git("ls-tree", "-r", "HEAD").splitlines():
            metadata, name = row.split("\t", 1)
            mode, _, oid = metadata.split()
            if name not in excluded:
                inventory.append([name, [mode, oid]])
        review["consumer_inventory_sha256"] = hashlib.sha256(ci.canonical(sorted(inventory))).hexdigest()
        review["python_asts"][scope["NONPRODUCT_HISTORY"]] = scope["nonproduct_python_ast"](b"VALUE = 1\n")
        node = next(node for node in ast.parse(raw).body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "REVIEWED_NONPRODUCT_INPUTS"
                            for target in node.targets))
        lines = raw.splitlines(keepends=True)
        lines[node.lineno - 1:node.end_lineno] = ["REVIEWED_NONPRODUCT_INPUTS = " + repr(review) + "\n"]
        provider_path.write_text("".join(lines))
        pinned = hashlib.sha256(provider_path.read_bytes()).hexdigest()
        planner = self.repo / ci.SELF
        replacement, count = ci.re.subn(r'REVIEWED_PROVIDER_SHA256 = "[0-9a-f]{64}"',
                                       'REVIEWED_PROVIDER_SHA256 = "' + pinned + '"', planner.read_text())
        self.assertEqual(count, 1)
        planner.write_text(replacement)
        self.git("add", ".")
        self.git("commit", "-qm", "bind synthetic review and provider pin")
        self.base = self.git("rev-parse", "HEAD").strip()

    def feature_plan(self, base, head):
        process, path, output = self.invoke(
            observed=execution(head, event="push", ref="refs/heads/feat/input-fixture"),
            event={"before": base, "after": head})
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        return json.loads(path.read_bytes()), output.read_text()

    def test_actual_source_bound_exclusions_are_not_executed_product_passes(self):
        self.prepare_reviewed_fixture()
        head = self.commit("README.md", b"Only reviewed root prose changed.\n", "prose")
        plan, outputs = self.feature_plan(self.base, head)
        self.assertEqual(plan["neutral_review_status"], "closed-nonproduct-input-review-applied")
        self.assertTrue(all(plan["jobs"][job]["selected"] for job in ci.ALWAYS))
        self.assertTrue(all(not plan["jobs"][job]["selected"] for job in ci.SELECTABLE))
        self.assertIn("rust_tests=false\n", outputs)
        self.assertIn("studio_tests=false\n", outputs)
        self.assertIn("browser_acceptance=false\n", outputs)
        self.assertEqual(plan["source"]["checkout_sha"], head)
        self.assertIs(plan["test_case_results_admitted"], False)
        self.assertIs(plan["release_verdict"], False)

    def test_actual_history_comment_excludes_product_but_keeps_source_consumers(self):
        self.prepare_reviewed_fixture()
        head = self.commit("tooling/conformance_history_guard.py", b"# Explanation only.\nVALUE = 1\n", "comment")
        plan, _ = self.feature_plan(self.base, head)
        self.assertTrue(all(not plan["jobs"][job]["selected"] for job in ci.SELECTABLE))
        self.assertEqual(plan["nonproduct_input_review"]["source_consumers"],
                         [".github/workflows/history-archive-conformance.yml", ".github/workflows/security-delivery.yml"])

    def test_actual_helper_logic_or_new_consumer_never_reuses_review_as_exclusion(self):
        self.prepare_reviewed_fixture()
        for variant in ("logic", "dynamic-consumer-before-base", "main", "candidate"):
            with self.subTest(variant=variant):
                self.git("reset", "--hard", self.base)
                before = self.base
                if variant == "logic":
                    head = self.commit("tooling/conformance_history_guard.py", b"VALUE = 2\n", "logic change")
                else:
                    if variant == "dynamic-consumer-before-base":
                        before = self.commit("crates/domain/src/lib.rs", b"pub fn new_runtime_input_reader() {}\n", "new consumer")
                    head = self.commit("README.md", b"Only prose in the final diff.\n", "prose")
                if variant in ("main", "candidate"):
                    observed = execution(head, event="push" if variant == "main" else "workflow_dispatch",
                                         ref="refs/heads/main")
                    process, path, _ = self.invoke(observed=observed,
                        event={"before": before, "after": head} if variant == "main" else {})
                    self.assertEqual(process.returncode, 0, process.stderr.decode())
                    plan = json.loads(path.read_bytes())
                else:
                    plan, _ = self.feature_plan(before, head)
                self.assertTrue(all(row["selected"] for row in plan["jobs"].values()))



class StudioSecurityG0(unittest.TestCase):
    """Synthetic admission/process custody controls, never installed Vite execution."""

    def inputs(self):
        return {"files": {path: {"bytes": 17, "sha256": format(index + 1, "064x")}
                          for index, path in enumerate((*ci.STUDIO_SECURITY_FILES,
                                                       ci.STUDIO_VITE_PACKAGE, ci.STUDIO_VITE_BUNDLE))},
                "locked_vite_version": "0.0.0-synthetic",
                "bundle": str(Path.cwd() / "synthetic-bundle.js")}

    def report(self):
        inputs = self.inputs()
        return {"schema": "motionwright.vite-editor-admission/1",
                "source_sha_from_ci_environment": execution()["GITHUB_SHA"],
                "ci_run_id": "11", "ci_run_attempt": "1",
                "probe_sha256": inputs["files"][ci.STUDIO_SECURITY_PROBE]["sha256"],
                "studio_package_sha256": inputs["files"]["apps/studio/package.json"]["sha256"],
                "studio_lock_sha256": inputs["files"]["apps/studio/package-lock.json"]["sha256"],
                "bundle_sha256": inputs["files"][ci.STUDIO_VITE_BUNDLE]["sha256"],
                "module_regions_sha256": "f" * 64,
                "locked_vite_version": inputs["locked_vite_version"],
                "installed_vite_version": inputs["locked_vite_version"],
                "bundle": inputs["bundle"], "bundle_mode": "installed_locked_dependency",
                "actual_host_platform": sys.platform, "simulated_platforms": ["win32", "linux-wsl"],
                "actual_os_processes_started": 0, "status": "PASS", "passed": 13, "failed": 0,
                "results": [{"name": name, "status": "PASS", "observation": {}}
                            for name in ci.STUDIO_SECURITY_NAMES]}

    def admit(self, report, *, stdout=None):
        raw = json.dumps(report).encode()
        return ci.admit_studio_security_report(raw if stdout is None else stdout,
                                               raw, self.inputs(), execution())

    def assert_required_workflow(self, raw):
        def job(name):
            match = ci.re.search(r"^  " + name + r":\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
                                 raw, ci.re.M | ci.re.S)
            self.assertIsNotNone(match, name)
            return match.group(1)
        studio, rust_core, units = job("studio"), job("rust-core"), job("studio-tests")
        self.assertIn("studio", ci.ALWAYS)
        self.assertIn("rust-core", ci.ALWAYS)
        self.assertNotRegex(studio, r"(?m)^    if:")
        self.assertNotRegex(rust_core, r"(?m)^    if:")
        self.assertIn("cargo fmt --all -- --check", rust_core)
        self.assertIn("cargo clippy --locked", rust_core)
        self.assertIn("--all-targets -- -D warnings", rust_core)
        self.assertIn("run: npm test", units)
        step = "      - name: G0 installed Vite editor security\n"
        upload = "      - name: Retain G0 editor security originals\n"
        self.assertEqual(studio.count(step), 1)
        self.assertEqual(studio.count(upload), 1)
        self.assertLess(studio.index("run: npm ci --no-audit --no-fund"), studio.index(step))
        self.assertLess(studio.index(step), studio.index(upload))
        self.assertLess(studio.index(upload), studio.index("run: npm run build"))
        body = studio.split(step, 1)[1].split("      - ", 1)[0]
        self.assertNotRegex(body, r"(?m)^        (if|continue-on-error):")
        self.assertIn("        working-directory: .\n", body)
        self.assertIn("python3 -I -B tooling/ci_job_plan.py studio-security", body)
        self.assertIn('--repo "$GITHUB_WORKSPACE" --plan-id "$CI_PLAN_ID"', body)
        self.assertIn('--output "$RUNNER_TEMP/g0-studio-security"', body)
        self.assertIn("CI_PLAN_ID: ${{ needs.plan.outputs.plan_id }}", body)
        retention = studio.split(upload, 1)[1].split("      - ", 1)[0]
        self.assertIn("        if: always()\n", retention)
        self.assertIn("uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", retention)
        self.assertIn("name: g0-studio-security-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}", retention)
        self.assertIn("path: ${{ runner.temp }}/g0-studio-security", retention)
        self.assertIn("if-no-files-found: error", retention)
        self.assertNotIn("continue-on-error:", retention)

    def test_actual_workflow_keeps_format_lint_typecheck_and_security_in_required_jobs(self):
        self.assert_required_workflow((TOOLING.parent / ci.WORKFLOW).read_text(encoding="utf-8"))

    def test_manual_entrypoint_requires_exact_workflow_owner_event_and_explicit_true(self):
        observed = execution(event="workflow_dispatch", ref="refs/heads/feat/manual-probe")
        observed["GITHUB_WORKFLOW_REF"] = (observed["GITHUB_REPOSITORY"] + "/"
            + ci.STUDIO_SECURITY_SHADOW_WORKFLOW + "@" + observed["GITHUB_REF"])
        for flag in (True, "true"):
            self.assertEqual(ci.studio_security_workflow(observed, "plan-and-contracts",
                {"studio_security": flag}), ci.STUDIO_SECURITY_SHADOW_WORKFLOW)
        for flag in (None, False, 0, 1, "false", "TRUE", "1", [], {}):
            with self.subTest(flag=flag):
                with self.assertRaises(ci.PlanError):
                    ci.studio_security_workflow(observed, "plan-and-contracts", {"studio_security": flag})
        for event in ("push", "pull_request"):
            with self.subTest(event=event), self.assertRaises(ci.PlanError):
                ci.studio_security_workflow(dict(observed, GITHUB_EVENT_NAME=event),
                                             "plan-and-contracts", {"studio_security": True})
        for emitter in ("studio-tests", "windows-manifest-contracts", "g0-complete", None):
            with self.subTest(emitter=emitter), self.assertRaises(ci.PlanError):
                ci.studio_security_workflow(observed, emitter, {"studio_security": True})
        with self.assertRaises(ci.PlanError):
            ci.studio_security_workflow(execution(event="workflow_dispatch"),
                                       "plan-and-contracts", {"studio_security": True})
        self.assertEqual(ci.studio_security_workflow(execution(), "studio", {"studio_security": False}),
                         ci.WORKFLOW)

    def test_actual_manual_workflow_selects_probe_after_clean_planning_without_product_jobs(self):
        from types import SimpleNamespace

        text = (TOOLING.parent / ci.STUDIO_SECURITY_SHADOW_WORKFLOW).read_text(encoding="utf-8")
        self.assertIn("      studio_security:\n", text)
        field = text.split("      studio_security:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("        type: boolean\n", field)
        self.assertIn("        default: false\n", field)
        self.assertEqual(ci.re.findall(r"(?m)^  ([a-z][a-z0-9-]*):$", text.split("jobs:\n", 1)[1]),
                         ["plan-and-contracts", "windows-manifest-contracts", "g0-complete"])
        planning = text.index("      - name: Compute full-source shadow selection without dispatching jobs\n")
        setup = text.index("      - name: Set up Node for requested Studio security probe\n")
        install = text.index("      - name: Install locked Studio dependencies for requested security probe\n")
        probe = text.index("      - name: Execute requested G0 installed Vite editor security\n")
        upload = text.index("      - name: Preserve original planning and guardrail evidence\n")
        self.assertLess(planning, setup)
        self.assertLess(setup, install)
        self.assertLess(install, probe)
        self.assertLess(probe, upload)
        steps = text[setup:upload]
        conditions = ci.re.findall(r"(?m)^        if: \$\{\{ (.+) \}\}$", steps)
        self.assertEqual(conditions, ["github.event_name == 'workflow_dispatch' && inputs.studio_security"] * 3)
        for event in ("push", "pull_request", "workflow_dispatch"):
            for flag in (False, True):
                for condition in conditions:
                    observed = eval(condition.replace("&&", " and "), {"__builtins__": {},
                        "github": SimpleNamespace(event_name=event),
                        "inputs": SimpleNamespace(studio_security=flag)})
                    self.assertIs(observed, event == "workflow_dispatch" and flag)
        self.assertIn("actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020", steps)
        self.assertIn('          node-version: "22"\n', steps)
        self.assertIn("cache-dependency-path: apps/studio/package-lock.json", steps)
        self.assertIn("working-directory: apps/studio", steps)
        self.assertIn("run: npm ci --no-audit --no-fund", steps)
        self.assertIn("python3 -I -B tooling/ci_job_plan.py studio-security", steps)
        self.assertIn("CI_PLAN_ID: ${{ steps.shadow_plan.outputs.plan_id }}", steps)
        self.assertIn('--output "$RUNNER_TEMP/verification/studio-security"', steps)
        for forbidden in ("npm test", "npm run build", "cargo "):
            self.assertNotIn(forbidden, steps)
        self.assertIn("        id: shadow_plan\n", text[planning:setup])
        self.assertIn('with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:', text[planning:setup])
        retained = text[upload:text.index("  windows-manifest-contracts:\n")]
        self.assertIn("if: always()", retained)
        self.assertIn("path: ${{ runner.temp }}/verification", retained)
        final = text[text.index("  g0-complete:\n"):]
        self.assertIn("needs: [plan-and-contracts, windows-manifest-contracts]", final)
        self.assertIn("if: always()", final)
        self.assertIn('if any(result != "success" for result in results.values()):', final)

    def test_workflow_guard_rejects_conditional_probe_lost_originals_and_skipped_lint(self):
        raw = (TOOLING.parent / ci.WORKFLOW).read_text(encoding="utf-8")
        mutations = (
            ("  studio:\n", "  studio:\n    if: false\n"),
            ("  rust-core:\n", "  rust-core:\n    if: false\n"),
            ("cargo fmt --all -- --check", "cargo fmt --all"),
            ("--all-targets -- -D warnings", "--all-targets"),
            ("      - name: G0 installed Vite editor security\n",
             "      - name: G0 installed Vite editor security\n        if: false\n"),
            ("      - name: G0 installed Vite editor security\n",
             "      - name: G0 installed Vite editor security\n        continue-on-error: true\n"),
            ("      - name: Retain G0 editor security originals\n        if: always()",
             "      - name: Retain G0 editor security originals\n        if: success()"),
            ('--output "$RUNNER_TEMP/g0-studio-security"', '--output "inside-checkout"'),
            ("path: ${{ runner.temp }}/g0-studio-security", "path: absent-originals"),
            ("name: g0-studio-security-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}",
             "name: unbound-security-result"),
        )
        for before, after in mutations:
            with self.subTest(before=before):
                self.assertIn(before, raw)
                with self.assertRaises(AssertionError):
                    self.assert_required_workflow(raw.replace(before, after, 1))

    def test_exact_existing_thirteen_controls_are_admitted_with_bound_installed_inputs(self):
        result = self.admit(self.report())
        self.assertEqual(result["controls"], 13)
        self.assertEqual(result["executed_names"], list(ci.STUDIO_SECURITY_NAMES))
        self.assertEqual(result["scope"], "installed-bundle-with-intercepted-effects")

    def test_counts_alone_wrong_names_duplicate_missing_skip_and_unknown_never_pass(self):
        for mutation in ("wrong-name", "duplicate", "missing", "extra", "skip", "unknown", "failed",
                         "counts-only", "boolean-count", "undeclared-result-field"):
            with self.subTest(mutation=mutation):
                report = self.report()
                if mutation == "wrong-name":
                    report["results"][0]["name"] = "some_other_control"
                elif mutation == "duplicate":
                    report["results"][1] = copy.deepcopy(report["results"][0])
                elif mutation == "missing":
                    report["results"].pop()
                elif mutation == "extra":
                    report["results"].append(copy.deepcopy(report["results"][0]))
                elif mutation in ("skip", "unknown", "failed"):
                    report["results"][0]["status"] = mutation.upper()
                elif mutation == "counts-only":
                    del report["results"]
                elif mutation == "boolean-count":
                    report["failed"] = False
                else:
                    report["results"][0]["reason"] = "was not actually executed"
                with self.assertRaises(ci.PlanError):
                    self.admit(report)

    def test_foreign_source_run_attempt_bundle_hash_or_external_fixture_is_not_admitted(self):
        for field, value in (("source_sha_from_ci_environment", "e" * 40), ("ci_run_id", "12"),
                             ("ci_run_attempt", "2"), ("probe_sha256", "e" * 64),
                             ("studio_package_sha256", "e" * 64), ("studio_lock_sha256", "e" * 64),
                             ("bundle_sha256", "e" * 64), ("bundle", "different-bundle.js"),
                             ("installed_vite_version", "different"),
                             ("bundle_mode", "external_bundle_fixture"),
                             ("actual_os_processes_started", 1), ("status", "UNKNOWN")):
            with self.subTest(field=field):
                report = self.report()
                report[field] = value
                with self.assertRaises(ci.PlanError):
                    self.admit(report)
        with self.assertRaises(ci.PlanError):
            self.admit(self.report(), stdout=b"different original stdout")
        raw = json.dumps(self.report()).encode()
        with self.assertRaises(ci.PlanError):
            ci.admit_studio_security_report(raw[:-1] + b',"status":"PASS"}',
                raw[:-1] + b',"status":"PASS"}', self.inputs(), execution())

    def capture(self, variant="pass", *, manual=False):
        from unittest import mock

        temp = tempfile.TemporaryDirectory(prefix="ci-studio-security-", dir=os.environ.get("MW_CI_JOB_TEST_TMP"))
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        repo = root / "repo"
        repo.mkdir()
        observed = execution()
        workflow = ci.STUDIO_SECURITY_SHADOW_WORKFLOW if manual else ci.WORKFLOW
        emitter = "plan-and-contracts" if manual else "studio"
        if manual:
            observed = execution(event="workflow_dispatch", ref="refs/heads/feat/manual-probe")
            observed["GITHUB_WORKFLOW_REF"] = observed["GITHUB_REPOSITORY"] + "/" + workflow + "@" + observed["GITHUB_REF"]
        workflow_file = repo / workflow
        workflow_file.parent.mkdir(parents=True)
        workflow_file.write_bytes(b"synthetic invoking workflow\n")
        event_path = root / "event.json"
        event_path.write_bytes(b'{"inputs":{"studio_security":"true"}}\n')
        output = root / "originals"
        before, after = source_observation(), source_observation()
        inputs_before, inputs_after = self.inputs(), self.inputs()
        if variant == "source-changed":
            after["tree_sha"] = "e" * 40
        if variant == "installed-changed":
            inputs_after["files"][ci.STUDIO_VITE_BUNDLE]["sha256"] = "e" * 64

        def fake_process(command, **kwargs):
            self.assertEqual(command, ["node", str(repo / ci.STUDIO_SECURITY_PROBE),
                                       "--report", str(output / "probe.json")])
            self.assertNotIn("--bundle", command)
            self.assertEqual(kwargs["timeout"], 60)
            self.assertEqual(kwargs["cwd"], repo)
            self.assertNotIn("NODE_OPTIONS", kwargs["env"])
            self.assertNotIn("NODE_PATH", kwargs["env"])
            self.assertEqual(kwargs["env"]["GITHUB_SHA"], execution()["GITHUB_SHA"])
            if variant == "timeout":
                kwargs["stdout"].write(b'{"partial":')
                kwargs["stderr"].write(b"original timeout stderr\n")
                raise subprocess.TimeoutExpired(command, 60)
            if variant == "spawn-failure":
                raise FileNotFoundError("synthetic missing node")
            raw = json.dumps(self.report()).encode() + b"\n"
            kwargs["stdout"].write(raw)
            kwargs["stderr"].write(b"original diagnostic stderr\n")
            if variant != "missing-report":
                (output / "probe.json").write_bytes(raw)
            return subprocess.CompletedProcess(command, 1 if variant == "nonzero" else 0)

        with mock.patch.dict(os.environ, {"GITHUB_JOB": emitter, "NODE_OPTIONS": "--require untrusted",
                                          "NODE_PATH": "untrusted", "GITHUB_EVENT_PATH": str(event_path)}), \
                mock.patch.object(ci, "source", side_effect=[before, after]), \
                mock.patch.object(ci, "studio_security_inputs", side_effect=[inputs_before, inputs_after]), \
                mock.patch.object(ci, "blob", return_value=workflow_file.read_bytes()), \
                mock.patch.object(ci.subprocess, "run", side_effect=fake_process) as process:
            result = ci.studio_security(repo, output, observed, "f" * 64)
            process.assert_called_once()
            with self.assertRaises(ci.PlanError):
                ci.studio_security(repo, output, observed, "f" * 64)
            process.assert_called_once()
        retained = json.loads((output / "receipt.json").read_bytes())
        self.assertEqual(result, retained)
        self.assertEqual(retained["source_before"], before)
        self.assertEqual(retained["execution_context"], observed)
        self.assertEqual(retained["emitter_job"], emitter)
        self.assertEqual(retained["invoking_workflow"],
                         {"path": workflow, "sha256": hashlib.sha256(workflow_file.read_bytes()).hexdigest()})
        self.assertEqual(retained["manual_event_sha256"],
                         hashlib.sha256(event_path.read_bytes()).hexdigest() if manual else None)
        self.assertEqual(retained["plan_id"], "f" * 64)
        for name, identity in retained["originals"].items():
            if identity is None:
                self.assertFalse((output / name).exists())
            else:
                raw = (output / name).read_bytes()
                self.assertEqual(identity, {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        self.assertIs(retained["formal_acceptance"], False)
        self.assertIs(retained["release_verdict"], False)
        return result

    def test_required_probe_captures_originals_identity_and_exact_command_once(self):
        result = self.capture()
        self.assertEqual((result["status"], result["exit_code"], result["process_exit_code"]), ("PASS", 0, 0))
        self.assertEqual(result["admission"]["controls"], 13)
        self.assertEqual(result["source_before"], result["source_after"])
        self.assertEqual(result["inputs_before"], result["inputs_after"])

    def test_manual_probe_captures_invoking_workflow_and_explicit_original_input(self):
        result = self.capture(manual=True)
        self.assertEqual((result["status"], result["exit_code"]), ("PASS", 0))
        self.assertEqual(result["emitter_job"], "plan-and-contracts")
        self.assertEqual(result["admission"]["controls"], 13)

    def test_timeout_retains_partial_originals_as_unknown_without_replay(self):
        result = self.capture("timeout")
        self.assertEqual((result["status"], result["exit_code"]), ("UNKNOWN", 124))
        self.assertIsNone(result["process_exit_code"])
        self.assertIsNone(result["originals"]["probe.json"])
        self.assertNotIn("admission", result)

    def test_nonzero_missing_report_changed_source_and_installed_bundle_fail_closed(self):
        for variant in ("nonzero", "missing-report", "source-changed", "installed-changed", "spawn-failure"):
            with self.subTest(variant=variant):
                result = self.capture(variant)
                self.assertEqual(result["status"], "NOT_RUN" if variant == "spawn-failure" else "FAIL")
                self.assertNotEqual(result["exit_code"], 0)
                self.assertNotIn("admission", result)



if __name__ == "__main__":
    unittest.main()
