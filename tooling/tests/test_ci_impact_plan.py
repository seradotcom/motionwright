"""Adversarial source-identity/selection fixtures; no native media claims."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "ci_impact_plan.py"
SPEC = importlib.util.spec_from_file_location("ci_impact_plan_under_test", MODULE)
impact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(impact)


class Repo:
    def __init__(self, root: Path):
        self.root = root
        self.call("init", "--initial-branch=main")
        self.call("config", "user.email", "fixture@example.invalid")
        self.call("config", "user.name", "Verification Fixture")
        self.call("config", "commit.gpgsign", "false")
        self.call("config", "core.autocrlf", "false")
        hooks = root / ".empty-hooks"
        hooks.mkdir()
        self.call("config", "core.hooksPath", str(hooks))
        members = ["crates/domain", "crates/storage", "crates/service", "crates/native",
                   "crates/manim-profile", "crates/driver-manim-community", "apps/desktop/src-tauri"]
        self.write("Cargo.toml", "[workspace]\nmembers = " + json.dumps(members) + "\n"
                   "[workspace.dependencies]\nshared-domain = {path = 'crates/domain'}\n")
        deps = {
            "crates/domain": "",
            "crates/storage": "[dependencies]\nrenamed-domain = {path = '../domain'}\n",
            "crates/service": "[dependencies]\nshared-domain = {workspace = true}\n"
                              "storage = {path = '../storage'}\n",
            "crates/manim-profile": "",
            "crates/driver-manim-community": "[dependencies]\nprofile = {path = '../manim-profile'}\n",
            "crates/native": "[dependencies]\nservice = {path = '../service'}\n"
                             "profile = {path = '../manim-profile', optional = true}\n",
            "apps/desktop/src-tauri": "[target.'cfg(windows)'.build-dependencies]\n"
                                      "native = {path = '../../../crates/native'}\n",
        }
        for root, dependencies in deps.items():
            self.write(root + "/Cargo.toml", "[package]\nname = 'fixture-" + root.split("/")[-1]
                       + "'\nversion = '0.1.0'\n" + dependencies)
            self.write(root + "/src/lib.rs", "pub fn fixture() {}\n")
        workflows = [
            "ci.yml", "verification-plan.yml", "native-production.yml", "native-render-e2e.yml",
            "native-cross-app-recovery.yml", "native-manim-e2e.yml", "native-mlt-sequence-e2e.yml",
            "runtime-first-use.yml", "package-candidate.yml", "project-portability.yml",
            "performance-acceptance.yml", "heavy-verification.yml",
        ]
        for workflow in workflows:
            self.write(".github/workflows/" + workflow, "name: Fixture\non: [push]\njobs:\n"
                       "  verify:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n")
        self.write("README.md", "Fixture repository.\n")
        self.write("tooling/native_manim_e2e.py", "# Recognized native boundary.\n")
        self.write("tooling/tests/test_native_manim_e2e.py", "# Recognized native boundary tests.\n")
        self.baseline = self.commit("baseline")

    def call(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], stderr=subprocess.STDOUT,
            env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1"),
        ).decode().strip()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message):
        self.call("add", "--all")
        self.call("commit", "--no-gpg-sign", "-m", message)
        return self.call("rev-parse", "HEAD")

    def plan(self, **kwargs):
        head = self.call("rev-parse", "HEAD")
        inputs = dict(base=self.baseline, head=head, checkout=head, event="push",
                      repository="example/fixture")
        inputs.update(kwargs)
        return impact.build_plan(self.root, **inputs)


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-ci-impact-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Repo(Path(self.temp.name))

    def selected(self, plan):
        return {x["workflow"] for x in plan["selected_workflows"]}

    def advanced_pr_graph(self, *, remove_workflow=False):
        """Real Git merge with a historical event base and two ordered parents."""
        event_base = self.repo.baseline
        self.repo.call("checkout", "-b", "feature")
        self.repo.write("README.md", "PR-only documentation.\n")
        head = self.repo.commit("feature from event base")
        self.repo.call("checkout", "main")
        self.repo.write("LICENSE", "Base-only fixture notice.\n")
        if remove_workflow:
            self.repo.call("rm", ".github/workflows/native-manim-e2e.yml")
        actual_base = self.repo.commit("base advances after event snapshot")
        self.repo.call("merge", "--no-ff", "--no-edit", "feature")
        checkout = self.repo.call("rev-parse", "HEAD")
        return event_base, actual_base, head, checkout

    def plan_advanced_graph(self, graph, **overrides):
        event_base, _, head, checkout = graph
        arguments = dict(base=event_base, head=head, checkout=checkout,
                         event="pull_request", allow_advanced_pr_base=True)
        arguments.update(overrides)
        return self.repo.plan(**arguments)

    def test_advanced_base_is_rejected_by_default_without_opt_in(self):
        event_base, _, head, checkout = self.advanced_pr_graph()
        with self.assertRaisesRegex(impact.PlanError, "exact ordered base/head merge"):
            self.repo.plan(base=event_base, head=head, checkout=checkout, event="pull_request")

    def test_advanced_base_opt_in_binds_both_bases_and_expands_neutral_changes(self):
        graph = self.advanced_pr_graph()
        event_base, actual_base, head, checkout = graph
        plan = self.plan_advanced_graph(graph)
        binding = plan["source"]["pr_base_reconciliation"]
        self.assertEqual(plan["source"]["base_sha"], event_base)
        self.assertEqual(plan["source"]["checkout_sha"], checkout)
        self.assertEqual(plan["source"]["checkout_parents"], [actual_base, head])
        self.assertEqual(binding["event_base_sha"], event_base)
        self.assertEqual(binding["actual_merge_parent_sha"], actual_base)
        self.assertEqual(binding["actual_merge_parent_tree_sha"],
                         self.repo.call("rev-parse", actual_base + "^{tree}"))
        self.assertEqual(binding["actual_merge_base_sha"], event_base)
        self.assertEqual(binding["policy"], "allow-advanced-merge-parent")
        self.assertEqual(binding["ancestry"], "verified-ancestor")
        self.assertTrue(binding["advanced"])
        self.assertEqual(set(plan["changed_paths"]), {"README.md", "LICENSE"})
        self.assertFalse(plan["unknown_paths"])
        self.assertIn({"kind": "advanced-event-base", "before": event_base,
                       "after": actual_base}, plan["change_ranges"])
        self.assertIn({"kind": "actual-base-to-checkout", "before": actual_base,
                       "after": checkout}, plan["change_ranges"])
        self.assertIn(actual_base, {g["source_sha"] for g in plan["dependency_graphs"]})
        self.assertEqual(plan["expansion_reasons"], ["advanced-pr-base-requires-full-shadow-inventory"])
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))
        self.assertEqual(plan["accounting"]["executions_avoided"], 0)

    def test_cli_advanced_base_requires_explicit_flag(self):
        event_base, _, head, checkout = self.advanced_pr_graph()
        argv = [sys.executable, "-B", str(MODULE), "plan", "--repo", str(self.repo.root),
                "--base", event_base, "--head", head, "--checkout", checkout,
                "--event", "pull_request", "--repository", "example/fixture"]
        strict = subprocess.run(argv, capture_output=True, check=False, timeout=30)
        self.assertEqual(strict.returncode, 2)
        self.assertIn(b"exact ordered base/head merge", strict.stderr)
        allowed = subprocess.run(argv + ["--allow-advanced-pr-base"], capture_output=True,
                                 check=False, timeout=30)
        self.assertEqual(allowed.returncode, 0, allowed.stderr.decode(errors="replace"))
        self.assertTrue(json.loads(allowed.stdout)["source"]["pr_base_reconciliation"]["advanced"])

    def test_opt_in_with_current_event_base_keeps_original_change_range(self):
        graph = self.advanced_pr_graph()
        _, actual_base, _, _ = graph
        plan = self.plan_advanced_graph(graph, base=actual_base)
        self.assertEqual(plan["changed_paths"], ["README.md"])
        self.assertFalse(plan["source"]["pr_base_reconciliation"]["advanced"])
        self.assertEqual(plan["source"]["pr_base_reconciliation"]["ancestry"], "same-commit")
        self.assertEqual(self.selected(plan), {impact.CI, impact.PLANNER})

    def test_opt_in_exact_head_is_still_labeled_as_head_only(self):
        self.repo.call("checkout", "-b", "feature")
        self.repo.write("README.md", "Exact head fixture.\n")
        head = self.repo.commit("head only")
        plan = self.repo.plan(head=head, checkout=head, event="pull_request", allow_advanced_pr_base=True)
        self.assertEqual(plan["source"]["scope"], "source-head")
        self.assertIsNone(plan["source"]["pr_base_reconciliation"]["actual_merge_parent_sha"])
        self.assertFalse(plan["source"]["pr_base_reconciliation"]["advanced"])

    def test_advanced_base_rejects_stale_head_parent(self):
        graph = self.advanced_pr_graph()
        self.repo.call("checkout", "feature")
        self.repo.write("README.md", "Newer feature head.\n")
        newer_head = self.repo.commit("head advances")
        self.repo.call("checkout", "--detach", graph[3])
        with self.assertRaisesRegex(impact.PlanError, "exact ordered base/head merge"):
            self.plan_advanced_graph(graph, head=newer_head)

    def test_advanced_base_rejects_reversed_merge_parents(self):
        graph = self.advanced_pr_graph()
        _, actual_base, head, checkout = graph
        tree = self.repo.call("rev-parse", checkout + "^{tree}")
        reversed_merge = self.repo.call("commit-tree", tree, "-p", head, "-p", actual_base,
                                       "-m", "reversed real commit parents")
        self.repo.call("checkout", "--detach", reversed_merge)
        with self.assertRaisesRegex(impact.PlanError, "exact ordered base/head merge"):
            self.plan_advanced_graph(graph, checkout=reversed_merge)

    def test_advanced_base_rejects_unrelated_event_base(self):
        graph = self.advanced_pr_graph()
        self.repo.call("checkout", "-b", "other-base", self.repo.baseline)
        self.repo.write("CONTRIBUTING.md", "Unrelated base movement.\n")
        other = self.repo.commit("not an ancestor of merge first parent")
        self.repo.call("checkout", "--detach", graph[3])
        with self.assertRaisesRegex(impact.PlanError, "ancestor of the actual first merge parent"):
            self.plan_advanced_graph(graph, base=other)

    def test_local_graft_cannot_fake_advanced_base_ancestry(self):
        graph = self.advanced_pr_graph()
        self.repo.call("checkout", "-b", "grafted-base", self.repo.baseline)
        self.repo.write("CONTRIBUTING.md", "Unrelated source in real commit graph.\n")
        other = self.repo.commit("unrelated real base")
        self.repo.call("checkout", "--detach", graph[3])
        graft = self.repo.root / ".git/info/grafts"
        graft.parent.mkdir(parents=True, exist_ok=True)
        graft.write_text(graph[1] + " " + other + "\n", encoding="ascii")
        with self.assertRaisesRegex(impact.PlanError, "ancestor of the actual first merge parent"):
            self.plan_advanced_graph(graph, base=other)

    def test_advanced_base_rejects_octopus_merge(self):
        graph = self.advanced_pr_graph()
        event_base, actual_base, head, checkout = graph
        tree = self.repo.call("rev-parse", checkout + "^{tree}")
        octopus = self.repo.call("commit-tree", tree, "-p", actual_base, "-p", head,
                                "-p", event_base, "-m", "three real commit parents")
        self.repo.call("checkout", "--detach", octopus)
        with self.assertRaisesRegex(impact.PlanError, "exact ordered base/head merge"):
            self.plan_advanced_graph(graph, checkout=octopus)

    def test_advanced_base_still_rejects_shallow_history(self):
        event_base, _, head, checkout = self.advanced_pr_graph()
        shallow = self.repo.root / "shallow"
        subprocess.check_call(["git", "clone", "--depth", "1", self.repo.root.as_uri(), str(shallow)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.assertRaisesRegex(impact.PlanError, "complete history"):
            impact.build_plan(shallow, base=event_base, head=head, checkout=checkout,
                              event="pull_request", repository="example/fixture", allow_advanced_pr_base=True)

    def test_advanced_base_does_not_allow_a_different_source_checkout(self):
        graph = self.advanced_pr_graph()
        with self.assertRaisesRegex(impact.PlanError, "checkout SHA mismatch"):
            self.plan_advanced_graph(graph, checkout=graph[2])

    def test_advanced_base_keeps_deleted_workflow_disposition_blocking(self):
        graph = self.advanced_pr_graph(remove_workflow=True)
        plan = self.plan_advanced_graph(graph)
        self.assertIn(".github/workflows/native-manim-e2e.yml", plan["changed_paths"])
        self.assertIn("removed-workflow:.github/workflows/native-manim-e2e.yml", plan["blockers"])

    def test_advanced_base_policy_is_explicitly_pr_only_and_boolean(self):
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event=event), self.assertRaisesRegex(impact.PlanError, "only available for pull_request"):
                self.repo.plan(event=event, allow_advanced_pr_base=True)
        for value in (1, "true", None):
            with self.subTest(value=value), self.assertRaisesRegex(impact.PlanError, "must be boolean"):
                self.repo.plan(event="pull_request", allow_advanced_pr_base=value)

    def test_known_document_only_keeps_always_checks_and_zero_savings(self):
        self.repo.write("README.md", "Edited fixture docs.\n")
        head = self.repo.commit("readme")
        plan = self.repo.plan()
        self.assertEqual(self.selected(plan), {impact.CI, impact.PLANNER})
        self.assertEqual(plan["source"]["checkout_sha"], head)
        self.assertEqual(plan["source"]["base_tree_sha"], self.repo.call("rev-parse", self.repo.baseline + "^{tree}"))
        self.assertEqual(plan["accounting"]["executions_avoided"], 0)
        self.assertEqual(plan["accounting"]["minutes_saved"], 0)
        self.assertEqual(plan["mode"], "shadow")

    def test_complete_pr_merge_base_retains_changes_before_latest_commit(self):
        self.repo.call("checkout", "-b", "feature")
        self.repo.write("crates/storage/src/lib.rs", "pub fn changed() {}\n")
        self.repo.commit("earlier storage change")
        self.repo.write("README.md", "Later documentation change.\n")
        head = self.repo.commit("later doc change")
        self.repo.call("checkout", "main")
        self.repo.write("base-only.txt", "Concurrent base movement.\n")
        base = self.repo.commit("base moves")
        self.repo.call("merge", "--no-ff", "--no-edit", "feature")
        checkout = self.repo.call("rev-parse", "HEAD")
        plan = self.repo.plan(base=base, head=head, checkout=checkout, event="pull_request")
        self.assertEqual(plan["source"]["merge_base_sha"], self.repo.baseline)
        self.assertEqual(plan["source"]["scope"], "pr-merge")
        self.assertIn("crates/storage/src/lib.rs", plan["changed_paths"])
        self.assertIn("apps/desktop/src-tauri", plan["affected_package_roots"])
        self.assertNotIn("base-only.txt", plan["changed_paths"])
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))

    def test_renamed_and_deleted_paths_are_both_part_of_impact(self):
        self.repo.call("mv", "tooling/native_manim_e2e.py", "tooling/renamed-reader.py")
        self.repo.call("rm", "crates/storage/src/lib.rs")
        self.repo.commit("rename and delete")
        plan = self.repo.plan()
        for path in ("tooling/native_manim_e2e.py", "tooling/renamed-reader.py", "crates/storage/src/lib.rs"):
            self.assertIn(path, plan["changed_paths"])
        self.assertTrue(any(x["status"].startswith("R") for x in plan["changes"]))
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))

    def test_path_dependency_closure_includes_renames_workspace_optional_and_targets(self):
        self.repo.write("crates/domain/src/lib.rs", "pub fn contract_changed() {}\n")
        self.repo.commit("change shared domain")
        plan = self.repo.plan()
        self.assertEqual(set(plan["affected_package_roots"]), {
            "crates/domain", "crates/storage", "crates/service", "crates/native", "apps/desktop/src-tauri",
        })
        self.repo.write("crates/manim-profile/src/lib.rs", "pub fn changed_profile() {}\n")
        base = self.repo.call("rev-parse", "HEAD")
        self.repo.commit("change optional profile")
        plan = self.repo.plan(base=base)
        self.assertEqual(set(plan["affected_package_roots"]), {
            "crates/manim-profile", "crates/driver-manim-community", "crates/native", "apps/desktop/src-tauri",
        })

    def test_deleted_dependency_edge_still_selects_original_consumer(self):
        self.repo.write("crates/native/Cargo.toml", "[package]\nname = 'fixture-native'\nversion = '0.1.0'\n")
        self.repo.write("crates/manim-profile/src/lib.rs", "pub fn changed_profile() {}\n")
        self.repo.commit("remove edge and alter former dependency")
        plan = self.repo.plan()
        self.assertIn("crates/native", plan["affected_package_roots"])
        self.assertIn("apps/desktop/src-tauri", plan["affected_package_roots"])

    def test_missing_or_invalid_manifest_expands_instead_of_dropping_dependencies(self):
        self.repo.write("crates/native/Cargo.toml", "not valid TOML !!!\n")
        self.repo.commit("bad manifest")
        plan = self.repo.plan()
        self.assertIn("incomplete-dependency-graph", plan["expansion_reasons"])
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))

    def test_unknown_path_expands_to_current_workflow_inventory(self):
        self.repo.write("unmapped/brand_new_plugin.py", "print('source')\n")
        self.repo.commit("unknown source")
        plan = self.repo.plan()
        self.assertEqual(plan["unknown_paths"], ["unmapped/brand_new_plugin.py"])
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))

    def test_known_helper_test_selects_real_production_consumer(self):
        self.repo.write("tooling/tests/test_native_manim_e2e.py", "# Revised negative fixture.\n")
        self.repo.commit("reader test changed")
        plan = self.repo.plan()
        self.assertIn(".github/workflows/native-manim-e2e.yml", self.selected(plan))
        self.assertNotIn(".github/workflows/package-candidate.yml", self.selected(plan))
        self.assertFalse(plan["unknown_paths"])

    def test_manifest_lock_and_workflow_changes_expand(self):
        for path in ("Cargo.lock", "apps/studio/package-lock.json", ".github/workflows/extra.yml"):
            with self.subTest(path=path):
                base = self.repo.call("rev-parse", "HEAD")
                self.repo.write(path, "fixture\n")
                self.repo.commit("boundary changed")
                plan = self.repo.plan(base=base)
                self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))

    def test_candidate_includes_newly_observed_workflow(self):
        self.repo.write(".github/workflows/future-renderer.yml", "name: Future\n")
        self.repo.commit("new workflow")
        plan = self.repo.plan(base=self.repo.call("rev-parse", "HEAD"), candidate=True)
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))
        self.assertTrue(all("G3" in w["stages"] for w in plan["selected_workflows"]))

    def test_removed_workflow_has_blocking_disposition(self):
        self.repo.call("rm", ".github/workflows/native-manim-e2e.yml")
        self.repo.commit("remove native workflow")
        plan = self.repo.plan()
        self.assertIn("removed-workflow:.github/workflows/native-manim-e2e.yml", plan["blockers"])

    def test_no_previous_push_commit_expands(self):
        plan = self.repo.plan(base=impact.ZERO)
        self.assertIn("no-previous-commit", plan["expansion_reasons"])
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))

    def test_nonancestor_push_expands(self):
        self.repo.call("checkout", "-b", "other")
        self.repo.write("README.md", "Other branch.\n")
        other = self.repo.commit("other branch")
        self.repo.call("checkout", "main")
        self.repo.write("README.md", "Current branch.\n")
        self.repo.commit("main")
        plan = self.repo.plan(base=other)
        self.assertIn("base-is-not-head-ancestor", plan["expansion_reasons"])
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))

    def test_rejects_dirty_source_and_wrong_checkout(self):
        self.repo.write("README.md", "Uncommitted source.\n")
        with self.assertRaisesRegex(impact.PlanError, "tracked working tree"):
            self.repo.plan()
        current = self.repo.commit("commit source")
        with self.assertRaisesRegex(impact.PlanError, "checkout SHA mismatch"):
            self.repo.plan(head=current, checkout=self.repo.baseline)

    def test_untracked_and_ignored_inputs_cannot_hide_under_checkout_sha(self):
        self.repo.write(".gitignore", "ignored-input.py\n")
        self.repo.commit("ignore fixture")
        for path in ("untracked-input.py", "ignored-input.py"):
            with self.subTest(path=path):
                self.repo.write(path, "# Not part of the commit.\n")
                with self.assertRaisesRegex(impact.PlanError, "untracked/ignored"):
                    self.repo.plan()
                (self.repo.root / path).unlink()

    def test_rejects_unrelated_pr_checkout(self):
        self.repo.write("README.md", "New commit.\n")
        newer = self.repo.commit("newer")
        with self.assertRaisesRegex(impact.PlanError, "exact ordered base/head merge"):
            self.repo.plan(head=self.repo.baseline, checkout=newer, event="pull_request")

    def test_rejects_shallow_history_before_selecting(self):
        shallow = Path(self.temp.name) / "clone"
        subprocess.check_call(["git", "clone", "--depth", "1", self.repo.root.as_uri(), str(shallow)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.assertRaisesRegex(impact.PlanError, "complete history"):
            impact.build_plan(shallow, base=self.repo.baseline, head=self.repo.baseline,
                              checkout=self.repo.baseline, event="push", repository="example/fixture")

    def test_rejects_revision_injection_and_unsafe_path_variants(self):
        for sha in ("HEAD", "main", "a" * 39, "A" * 40, "--help", "a" * 40 + "^{tree}"):
            with self.subTest(sha=sha), self.assertRaises(impact.PlanError):
                self.repo.plan(head=sha)
        for path in ("../file", "/etc/file", "E:/file", "x\\file", "x//file", "x/./file", "x/../file", "x\nfile", "x\0file"):
            with self.subTest(path=repr(path)), self.assertRaises(impact.PlanError):
                impact.safe_path(path)

    def test_cargo_symlink_is_not_followed_or_treated_as_complete(self):
        # Git-mode fixture: no OS symlink creation, privilege or target access.
        # core.symlinks=false retains the 120000 Git entry as a literal file.
        self.repo.call("config", "core.symlinks", "false")
        path = "crates/native/Cargo.toml"
        self.repo.write(path, "/must-not-be-opened\n")
        blob = self.repo.call("hash-object", "-w", path)
        self.repo.call("update-index", "--cacheinfo", "120000," + blob + "," + path)
        self.repo.call("commit", "--no-gpg-sign", "-m", "symlink manifest")
        plan = self.repo.plan()
        self.assertIn("incomplete-dependency-graph", plan["expansion_reasons"])
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))

    def test_path_dependency_outside_repository_expands(self):
        self.repo.write("crates/native/Cargo.toml", "[package]\nname = 'fixture-native'\nversion = '0.1.0'\n"
                        "[dependencies]\nexternal = {path = '../../../outside'}\n")
        self.repo.commit("escaping dependency")
        plan = self.repo.plan()
        self.assertIn("incomplete-dependency-graph", plan["expansion_reasons"])


def receipt_fixture():
    source = {"repository": "example/fixture", "checkout_sha": "a" * 40,
              "tree_sha": "b" * 40, "head_sha": "c" * 40}
    plan = {
        "schema_version": 1, "kind": "motionwright-ci-impact-plan", "mode": "shadow",
        "source": source, "selected_workflows": [{"workflow": impact.CI}],
        "workflow_inventory_sha256": {impact.CI: "d" * 64}, "blockers": [],
    }
    plan["plan_id"] = impact.digest(plan)
    wanted = {
        "workflow": impact.CI, "run_id": "101", "run_attempt": 2,
        "event_sha": "e" * 40, "workflow_sha": "f" * 40, "workflow_sha256": "d" * 64,
        "jobs": [{"name": "source-policy", "checks": ["positive-roundtrip", "reject-stale-receipt"]}],
    }
    got = {
        **copy.deepcopy(wanted), "status": "completed", "conclusion": "success",
        "jobs": [{
            "name": "source-policy", "job_id": "202", "run_id": "101", "run_attempt": 2,
            "status": "completed", "conclusion": "success",
            "source": {"checkout_sha": "a" * 40, "tree_sha": "b" * 40, "plan_id": plan["plan_id"]},
            "original_log_sha256": "9" * 64,
            "checks": [{"id": "positive-roundtrip", "outcome": "passed"},
                       {"id": "reject-stale-receipt", "outcome": "passed"}],
        }],
    }
    envelope = {"schema_version": 1, "plan_id": plan["plan_id"], "repository": "example/fixture"}
    return plan, {**envelope, "runs": [wanted]}, {**envelope, "runs": [got]}


class WorkflowReviewTests(unittest.TestCase):
    """Exact Git fixtures for the optional trust-input route, not product evidence."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-workflow-review-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Repo(Path(self.temp.name))

    def modify(self, *names):
        for name in names:
            path = self.repo.root / name
            self.repo.write(name, path.read_text(encoding="utf-8") + "# revised definition\n")
        return self.repo.commit("workflow definition fixture")

    def review(self, **kwargs):
        plan = self.repo.plan(**kwargs)
        views = []
        for graph in plan["dependency_graphs"]:
            tree = impact.Tree(self.repo.root, graph["source_sha"])
            nodes = {}
            for workflow in sorted(tree.workflows()):
                raw = tree.read(workflow)
                nodes[workflow] = {
                    "sha256": impact.hashlib.sha256(raw).hexdigest(),
                    "depends_on": sorted(other for other in tree.workflows()
                                         if other != workflow and other.encode() in raw),
                    "basis": "OFFLINE FIXTURE: controlled source, no external execution or evidence",
                }
            views.append({"source_sha": tree.sha, "tree_sha": tree.tree_sha,
                          "workflows": nodes, "unresolved_dependencies": []})
        return {
            "schema_version": 1, "kind": "motionwright-reviewed-workflow-impact",
            "mode": "shadow", "status": "REVIEWED",
            "review_id": "OFFLINE_UNIT_FIXTURE_NOT_PRODUCT_EVIDENCE",
            "source": {key: plan["source"][key] for key in
                       ("repository", "event", "base_sha", "head_sha", "checkout_sha")},
            "changed_paths": plan["changed_paths"], "workflow_graphs": views,
        }

    def selected(self, plan):
        return {item["workflow"] for item in plan["selected_workflows"]}

    def assert_broad(self, review, **kwargs):
        plan = self.repo.plan(workflow_impact_review=review, **kwargs)
        self.assertEqual(plan["workflow_impact_review"]["status"], "NOT_APPLIED")
        self.assertIn("workflow-impact-review-not-applicable", plan["expansion_reasons"])
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))
        self.assertEqual(plan["accounting"]["executions_avoided"], 0)
        return plan

    def test_exact_review_narrows_two_workflows_and_keeps_g0(self):
        first_use = ".github/workflows/runtime-first-use.yml"
        self.modify(first_use, impact.PLANNER)
        plan = self.repo.plan(workflow_impact_review=self.review())
        self.assertEqual(self.selected(plan), {impact.CI, impact.PLANNER, first_use})
        self.assertEqual(plan["workflow_impact_review"]["status"], "APPLIED")
        self.assertFalse(plan["expansion_reasons"])
        self.assertFalse(plan["blockers"])
        self.assertEqual(plan["accounting"]["executions_avoided"], 0)
        self.assertEqual(plan["evidence_claim"], "selection-only-no-product-test-or-release-verdict")
        for item in plan["selected_workflows"]:
            if item["workflow"] in (impact.CI, impact.PLANNER):
                self.assertIn("G0", item["stages"])

    def test_no_review_preserves_original_broad_workflow_rule(self):
        self.modify(".github/workflows/runtime-first-use.yml")
        plan = self.repo.plan()
        self.assertEqual(plan["workflow_impact_review"], {"status": "NOT_SUPPLIED"})
        self.assertEqual(self.selected(plan), set(plan["workflow_inventory_sha256"]))
        self.assertIn("dependency-or-workflow-boundary:.github/workflows/runtime-first-use.yml",
                      plan["expansion_reasons"])

    def test_private_review_identity_and_narrative_do_not_enter_public_plan(self):
        self.modify(".github/workflows/runtime-first-use.yml")
        review = self.review()
        review["review_id"] = "OFFLINE_PRIVATE_COORDINATION_ID_CANARY"
        for view in review["workflow_graphs"]:
            for node in view["workflows"].values():
                node["basis"] = "OFFLINE PRIVATE NARRATIVE CANARY; synthetic review content"
        plan = self.repo.plan(workflow_impact_review=review)
        encoded = json.dumps(plan)
        self.assertNotIn(review["review_id"], encoded)
        self.assertNotIn("OFFLINE PRIVATE NARRATIVE CANARY", encoded)
        self.assertEqual(plan["workflow_impact_review"]["review_sha256"], impact.digest(review))
        self.assertEqual(plan["workflow_impact_review"]["review_id_sha256"],
                         impact.hashlib.sha256(review["review_id"].encode()).hexdigest())
        self.assertTrue(plan["workflow_impact_review"]["workflow_graphs"])

    def test_reverse_consumers_include_removed_old_edges_and_transitive_edges(self):
        producer = ".github/workflows/runtime-first-use.yml"
        consumer = ".github/workflows/package-candidate.yml"
        transitive = ".github/workflows/native-production.yml"
        self.repo.write(consumer, f"name: Consumer\n# reads {producer}\n")
        self.repo.write(transitive, f"name: Downstream\n# reads {consumer}\n")
        self.repo.baseline = self.repo.commit("old explicit dependency fixture")
        self.repo.write(consumer, "name: Consumer\n# input removed in new definition\n")
        self.modify(producer)
        plan = self.repo.plan(workflow_impact_review=self.review())
        self.assertEqual(self.selected(plan), {impact.CI, impact.PLANNER, producer, consumer, transitive})
        self.assertEqual(plan["workflow_impact_review"]["status"], "APPLIED")

    def test_stale_commit_with_identical_tree_is_not_a_review_of_new_commit(self):
        self.modify(".github/workflows/runtime-first-use.yml")
        review = self.review()
        before_tree = self.repo.call("rev-parse", "HEAD^{tree}")
        self.repo.call("commit", "--allow-empty", "-m", "new source identity")
        self.assertEqual(self.repo.call("rev-parse", "HEAD^{tree}"), before_tree)
        plan = self.assert_broad(review)
        self.assertIn("stale or unrelated", plan["workflow_impact_review"]["reason"])

    def test_old_reviewed_semantic_consumer_cannot_disappear_from_union(self):
        producer = ".github/workflows/runtime-first-use.yml"
        consumer = ".github/workflows/package-candidate.yml"
        transitive = ".github/workflows/native-production.yml"
        self.modify(producer)
        review = self.review()
        for view in review["workflow_graphs"]:
            if view["source_sha"] == self.repo.baseline:
                view["workflows"][consumer]["depends_on"] = [producer]
                view["workflows"][transitive]["depends_on"] = [consumer]
        plan = self.repo.plan(workflow_impact_review=review)
        self.assertEqual(plan["changed_paths"], [producer])
        self.assertEqual(self.selected(plan), {impact.CI, impact.PLANNER, producer, consumer, transitive})

    def test_review_source_fields_are_exact_and_complete(self):
        self.modify(".github/workflows/runtime-first-use.yml")
        review = self.review()
        for key in review["source"]:
            with self.subTest(key=key):
                broken = copy.deepcopy(review)
                broken["source"][key] = "unrelated"
                self.assert_broad(broken)
        broken = copy.deepcopy(review)
        broken["source"]["claimed_same_tree_reuse"] = True
        self.assert_broad(broken)

    def test_draft_missing_and_unsupported_review_fields_cannot_narrow(self):
        self.modify(".github/workflows/runtime-first-use.yml")
        review = self.review()
        edits = (("status", "PROPOSED"), ("schema_version", True), ("mode", "enforcing"),
                 ("review_id", ""), ("review_id", "unsafe\nlabel"))
        for key, value in edits:
            with self.subTest(key=key, value=value):
                broken = copy.deepcopy(review)
                broken[key] = value
                self.assert_broad(broken)
        broken = copy.deepcopy(review)
        del broken["workflow_graphs"]
        self.assert_broad(broken)
        broken = copy.deepcopy(review)
        broken["automatic_acceptance"] = True
        self.assert_broad(broken)

    def test_all_source_views_definitions_and_hashes_are_required(self):
        self.modify(".github/workflows/runtime-first-use.yml")
        review = self.review()
        variants = []
        broken = copy.deepcopy(review)
        broken["workflow_graphs"].pop()
        variants.append(broken)
        broken = copy.deepcopy(review)
        broken["workflow_graphs"].append(copy.deepcopy(broken["workflow_graphs"][0]))
        variants.append(broken)
        broken = copy.deepcopy(review)
        broken["workflow_graphs"][0]["tree_sha"] = "a" * 40
        variants.append(broken)
        broken = copy.deepcopy(review)
        del broken["workflow_graphs"][0]["workflows"][impact.CI]
        variants.append(broken)
        broken = copy.deepcopy(review)
        broken["workflow_graphs"][0]["workflows"][impact.CI]["sha256"] = "b" * 64
        variants.append(broken)
        for index, broken in enumerate(variants):
            with self.subTest(index=index):
                self.assert_broad(broken)

    def test_dependency_omissions_and_unresolved_edges_broaden(self):
        producer = ".github/workflows/runtime-first-use.yml"
        consumer = ".github/workflows/package-candidate.yml"
        self.repo.write(consumer, f"name: Consumer\n# refers to {producer}\n")
        self.repo.baseline = self.repo.commit("consumer fixture")
        self.modify(producer)
        review = self.review()
        for view in review["workflow_graphs"]:
            view["workflows"][consumer]["depends_on"] = []
        plan = self.assert_broad(review)
        self.assertIn("omits a literal", plan["workflow_impact_review"]["reason"])
        review = self.review()
        review["workflow_graphs"][0]["unresolved_dependencies"] = ["indirect artifact source"]
        self.assert_broad(review)

    def test_unsafe_duplicate_or_unreviewed_dependency_records_broaden(self):
        self.modify(".github/workflows/runtime-first-use.yml")
        review = self.review()
        for dependencies in (["../escape.yml"], [impact.PLANNER, impact.PLANNER],
                             [impact.CI], [True], "not a list"):
            with self.subTest(dependencies=dependencies):
                broken = copy.deepcopy(review)
                broken["workflow_graphs"][0]["workflows"][impact.CI]["depends_on"] = dependencies
                self.assert_broad(broken)
        broken = copy.deepcopy(review)
        broken["workflow_graphs"][0]["workflows"][impact.CI]["basis"] = ""
        self.assert_broad(broken)

    def test_unreviewed_activation_and_reusable_contracts_stay_broad(self):
        path = ".github/workflows/native-production.yml"
        for relationship in ("workflow_run", "workflow_call", "repository_dispatch"):
            with self.subTest(relationship=relationship):
                self.repo.write(path, f"name: Fixture\non: [{relationship}]\n")
                self.repo.commit("relationship needs a wider event contract")
                plan = self.assert_broad(self.review())
                self.assertIn("wider contract", plan["workflow_impact_review"]["reason"])

    def test_mixed_product_shared_action_lock_or_unlisted_path_never_narrows(self):
        workflow = ".github/workflows/runtime-first-use.yml"
        start = self.modify(workflow)
        for path in ("Cargo.lock", ".github/actions/shared/action.yml", "tooling/unknown.py",
                     "crates/domain/src/lib.rs"):
            with self.subTest(path=path):
                self.repo.write(path, "changed shared or unreviewed input\n")
                self.repo.commit("non-workflow input")
                review = self.review()
                self.assert_broad(review)
                review["changed_paths"] = [workflow]
                self.assert_broad(review)
                self.repo.call("reset", "--hard", start)

    def test_new_renamed_deleted_workflows_keep_full_disposition(self):
        path = ".github/workflows/native-manim-e2e.yml"
        self.repo.write(".github/workflows/new.yml", "name: New\n")
        self.repo.commit("new workflow")
        self.assert_broad(self.review())
        self.repo.call("mv", path, ".github/workflows/renamed.yml")
        self.repo.commit("rename workflow")
        plan = self.assert_broad(self.review())
        self.assertIn("removed-workflow:" + path, plan["blockers"])
        self.repo.call("rm", ".github/workflows/runtime-first-use.yml")
        self.repo.commit("remove workflow")
        plan = self.assert_broad(self.review())
        self.assertIn("removed-workflow:.github/workflows/runtime-first-use.yml", plan["blockers"])

    def test_candidate_and_incomplete_graph_take_precedence(self):
        self.modify(".github/workflows/runtime-first-use.yml")
        plan = self.assert_broad(self.review(), candidate=True)
        self.assertTrue(all("G3" in item["stages"] for item in plan["selected_workflows"]))
        self.repo.write("Cargo.toml", "[workspace]\nmembers=['crates/missing']\n")
        self.repo.commit("incomplete dependency graph")
        plan = self.assert_broad(self.review())
        self.assertIn("incomplete-dependency-graph", plan["expansion_reasons"])

    def test_complete_pr_comparison_and_exact_ordered_merge_are_preserved(self):
        base = self.repo.baseline
        self.repo.call("checkout", "-b", "topic")
        self.modify(".github/workflows/runtime-first-use.yml")
        head = self.modify(impact.PLANNER)
        self.repo.call("checkout", "main")
        self.repo.call("merge", "--no-ff", "topic", "-m", "exact ordered merge")
        checkout = self.repo.call("rev-parse", "HEAD")
        inputs = dict(base=base, head=head, checkout=checkout, event="pull_request")
        review = self.review(**inputs)
        self.assertEqual(len(review["workflow_graphs"]), 3)
        plan = self.repo.plan(**inputs, workflow_impact_review=review)
        self.assertEqual(plan["source"]["checkout_parents"], [base, head])
        self.assertEqual(self.selected(plan), {impact.CI, impact.PLANNER,
                                             ".github/workflows/runtime-first-use.yml"})
        self.assertEqual(plan["source"]["scope"], "pr-merge")
        self.assertEqual(plan["change_ranges"][0]["before"], base)
        with self.assertRaises(impact.PlanError):
            self.repo.plan(**dict(inputs, head=base), workflow_impact_review=review)

    def test_advanced_base_cannot_be_narrowed_by_a_matching_review(self):
        event_base = self.repo.baseline
        self.repo.call("checkout", "-b", "topic")
        head = self.modify(".github/workflows/runtime-first-use.yml")
        self.repo.call("checkout", "main")
        self.modify(impact.PLANNER)
        self.repo.call("merge", "--no-ff", "topic", "-m", "advanced base merge")
        checkout = self.repo.call("rev-parse", "HEAD")
        inputs = dict(base=event_base, head=head, checkout=checkout, event="pull_request",
                      allow_advanced_pr_base=True)
        plan = self.assert_broad(self.review(**inputs), **inputs)
        self.assertIn("advanced-pr-base-requires-full-shadow-inventory", plan["expansion_reasons"])
        self.assertTrue(plan["source"]["pr_base_reconciliation"]["advanced"])

    def test_cli_accepts_only_external_review_and_preserves_original_exit(self):
        self.modify(".github/workflows/runtime-first-use.yml")
        review = self.review()
        with tempfile.TemporaryDirectory(prefix="mw-external-workflow-review-") as external:
            review_path = Path(external) / "review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            command = [sys.executable, "-B", str(MODULE), "plan", "--repo", str(self.repo.root)]
            for name, value in review["source"].items():
                command.extend(["--" + name.removesuffix("_sha"), value])
            result = subprocess.run(command + ["--reviewed-workflow-impact", str(review_path)],
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["workflow_impact_review"]["status"], "APPLIED")
            internal = self.repo.root / "review.json"
            internal.write_text(json.dumps(review), encoding="utf-8")
            result = subprocess.run(command + ["--reviewed-workflow-impact", str(internal)],
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"outside source checkout", result.stderr)


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.expected, self.observed = receipt_fixture()

    def check(self):
        return impact.aggregate(self.plan, self.expected, self.observed)

    def test_matching_receipts_only_accept_consistency_never_release(self):
        result = self.check()
        self.assertEqual(result["result"], "RECEIPT_CONSISTENCY_ACCEPTED")
        self.assertFalse(result["authenticity_verified"])
        self.assertFalse(result["release_ready"])
        self.assertFalse(result["contractual_acceptance"])

    def test_missing_selected_workflow_is_rejected(self):
        self.observed["runs"] = []
        with self.assertRaises(impact.PlanError):
            self.check()

    def test_missing_expected_workflow_is_rejected(self):
        self.expected["runs"] = []
        with self.assertRaises(impact.PlanError):
            self.check()

    def test_failed_cancelled_skipped_neutral_or_pending_workflow_rejected(self):
        for conclusion in ("failure", "cancelled", "skipped", "neutral", "timed_out", "action_required", None):
            with self.subTest(conclusion=conclusion):
                self.observed["runs"][0]["conclusion"] = conclusion
                with self.assertRaises(impact.PlanError):
                    self.check()
        self.observed["runs"][0]["conclusion"] = "success"
        self.observed["runs"][0]["status"] = "in_progress"
        with self.assertRaises(impact.PlanError):
            self.check()

    def test_skipped_missing_or_incomplete_selected_job_rejected(self):
        for change in ("skipped", "missing", "in_progress"):
            with self.subTest(change=change):
                self.plan, self.expected, self.observed = receipt_fixture()
                if change == "missing":
                    self.observed["runs"][0]["jobs"] = []
                elif change == "skipped":
                    self.observed["runs"][0]["jobs"][0]["conclusion"] = "skipped"
                else:
                    self.observed["runs"][0]["jobs"][0]["status"] = "in_progress"
                with self.assertRaises(impact.PlanError):
                    self.check()

    def test_stale_source_rejected_even_if_tree_is_identical(self):
        self.observed["runs"][0]["jobs"][0]["source"]["checkout_sha"] = "8" * 40
        with self.assertRaisesRegex(impact.PlanError, "checkout_sha"):
            self.check()

    def test_wrong_tree_rejected_even_if_commit_claim_matches(self):
        self.observed["runs"][0]["jobs"][0]["source"]["tree_sha"] = "7" * 40
        with self.assertRaisesRegex(impact.PlanError, "tree_sha"):
            self.check()

    def test_old_attempt_or_other_run_is_rejected_at_workflow_and_job(self):
        for node, field, value in (("run", "run_attempt", 1), ("run", "run_id", "999"),
                                   ("job", "run_attempt", 1), ("job", "run_id", "999")):
            with self.subTest(node=node, field=field):
                self.plan, self.expected, self.observed = receipt_fixture()
                target = self.observed["runs"][0]
                if node == "job":
                    target = target["jobs"][0]
                target[field] = value
                with self.assertRaises(impact.PlanError):
                    self.check()

    def test_wrong_event_workflow_or_definition_rejected(self):
        for field, value in (("event_sha", "1" * 40), ("workflow_sha", "2" * 40),
                             ("workflow_sha256", "3" * 64)):
            with self.subTest(field=field):
                self.plan, self.expected, self.observed = receipt_fixture()
                self.observed["runs"][0][field] = value
                with self.assertRaises(impact.PlanError):
                    self.check()

    def test_skipped_missing_or_unknown_required_check_rejected(self):
        for outcome in ("skipped", "failed", "unknown", "not_run", "missing"):
            with self.subTest(outcome=outcome):
                self.plan, self.expected, self.observed = receipt_fixture()
                checks = self.observed["runs"][0]["jobs"][0]["checks"]
                if outcome == "missing":
                    checks.pop()
                else:
                    checks[0]["outcome"] = outcome
                with self.assertRaises(impact.PlanError):
                    self.check()

    def test_zero_checks_cannot_pass_on_success_exit_alone(self):
        self.expected["runs"][0]["jobs"][0]["checks"] = []
        self.observed["runs"][0]["jobs"][0]["checks"] = []
        with self.assertRaisesRegex(impact.PlanError, "nonempty check inventory"):
            self.check()

    def test_duplicate_workflow_job_or_check_identity_rejected(self):
        for field in ("runs", "jobs", "checks"):
            with self.subTest(field=field):
                self.plan, self.expected, self.observed = receipt_fixture()
                items = self.observed["runs"]
                if field in ("jobs", "checks"):
                    items = items[0]["jobs"]
                if field == "checks":
                    items = items[0]["checks"]
                items.append(copy.deepcopy(items[0]))
                with self.assertRaisesRegex(impact.PlanError, "duplicate"):
                    self.check()

    def test_swapped_plan_repository_or_missing_original_log_rejected(self):
        mutations = (lambda: self.observed.update(plan_id="1" * 64),
                     lambda: self.observed.update(repository="another/repository"),
                     lambda: self.observed["runs"][0]["jobs"][0].pop("original_log_sha256"))
        for mutate in mutations:
            self.plan, self.expected, self.observed = receipt_fixture()
            mutate()
            with self.assertRaises(impact.PlanError):
                self.check()

    def test_plan_tampering_and_unresolved_workflow_removal_rejected(self):
        self.plan["source"]["tree_sha"] = "1" * 40
        with self.assertRaisesRegex(impact.PlanError, "digest mismatch"):
            self.check()
        self.plan, self.expected, self.observed = receipt_fixture()
        self.plan["blockers"] = ["removed-workflow:required.yml"]
        self.plan["plan_id"] = impact.digest({k: v for k, v in self.plan.items() if k != "plan_id"})
        with self.assertRaisesRegex(impact.PlanError, "unresolved"):
            self.check()

    def test_boolean_attempt_does_not_equal_numeric_identity(self):
        self.expected["runs"][0]["run_attempt"] = 1
        self.observed["runs"][0]["run_attempt"] = True
        with self.assertRaises(impact.PlanError):
            self.check()

    def test_duplicate_json_keys_and_nonfinite_numbers_rejected(self):
        with tempfile.TemporaryDirectory(prefix="mw-ci-json-") as tmp:
            path = Path(tmp) / "bad.json"
            for text in ('{"schema_version":1,"schema_version":2}', '{"number":NaN}'):
                with self.subTest(text=text):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(impact.PlanError):
                        impact.read_json(path)


class ClosedNonproductInputTests(unittest.TestCase):
    """Actual Git source comparisons; no application or renderer execution."""

    def setUp(self):
        from unittest import mock

        self.temp = tempfile.TemporaryDirectory(prefix="mw-closed-inputs-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Repo(Path(self.temp.name))
        self.repo.write("CONTRIBUTING.md", "Human contribution guidance.\n")
        self.repo.write(impact.NONPRODUCT_HISTORY, "VALUE = 1\n")
        for workflow in impact.NONPRODUCT_CONSUMERS:
            self.repo.write(workflow, "name: Source fixture\non: [push]\njobs:\n"
                            "  guard:\n    runs-on: ubuntu-latest\n    steps:\n"
                            "      - run: python -m unittest discover -s tooling/tests -p 'test_*.py'\n")
        self.repo.baseline = self.repo.commit("closed fixture source")
        tree = impact.Tree(self.repo.root, self.repo.baseline)
        subjects = impact.NONPRODUCT_DOCUMENTS | {impact.NONPRODUCT_HISTORY}
        inventory = [[path, list(metadata)] for path, metadata in sorted(tree.entries.items())
                     if path not in subjects | impact.NONPRODUCT_PLANNERS]
        review = copy.deepcopy(impact.REVIEWED_NONPRODUCT_INPUTS)
        review["consumer_inventory_sha256"] = impact.digest(inventory)
        review["python_asts"][impact.NONPRODUCT_HISTORY] = impact.nonproduct_python_ast(b"VALUE = 1\n")
        patch = mock.patch.object(impact, "REVIEWED_NONPRODUCT_INPUTS", review)
        patch.start()
        self.addCleanup(patch.stop)

    def assert_full(self, plan):
        self.assertEqual(plan["nonproduct_input_review"]["status"], "NOT_APPLIED")
        self.assertEqual({row["workflow"] for row in plan["selected_workflows"]},
                         impact.Tree(self.repo.root, self.repo.call("rev-parse", "HEAD")).workflows())

    def test_reviewed_root_prose_keeps_g0_and_binds_every_compared_tree(self):
        self.repo.write("README.md", "Revised human documentation.\n")
        self.repo.write("CONTRIBUTING.md", "Revised contribution guidance.\n")
        head = self.repo.commit("only reviewed prose")
        plan = self.repo.plan()
        proof = plan["nonproduct_input_review"]
        self.assertEqual(proof["status"], "APPLIED")
        self.assertEqual(proof["reviewed_paths"], ["CONTRIBUTING.md", "README.md"])
        self.assertEqual({view["source_sha"] for view in proof["source_views"]},
                         {self.repo.baseline, head})
        self.assertEqual({row["workflow"] for row in plan["selected_workflows"]},
                         {impact.CI, impact.PLANNER})
        self.assertEqual(plan["accounting"]["executions_avoided"], 0)
        self.assertEqual(plan["accounting"]["minutes_saved"], 0)

    def test_identical_history_ast_preserves_history_and_broad_security_discovery(self):
        self.repo.write(impact.NONPRODUCT_HISTORY, "# Source-only explanation.\nVALUE = (1)\n")
        self.repo.commit("comments and equivalent syntax")
        plan = self.repo.plan()
        self.assertEqual(plan["nonproduct_input_review"]["status"], "APPLIED")
        self.assertEqual(plan["unknown_paths"], [])
        self.assertEqual({row["workflow"] for row in plan["selected_workflows"]},
                         {impact.CI, impact.PLANNER, *impact.NONPRODUCT_CONSUMERS})

    def test_logic_import_encoding_and_docstring_changes_are_not_ast_equivalence(self):
        variants = ["VALUE = 2\n", "import subprocess\nVALUE = 1\n",
                    "# coding: latin-1\nVALUE = 1\n", '"new runtime docstring"\nVALUE = 1\n']
        for source in variants:
            with self.subTest(source=source):
                self.repo.call("reset", "--hard", self.repo.baseline)
                self.repo.write(impact.NONPRODUCT_HISTORY, source)
                self.repo.commit("unreviewed helper semantics")
                plan = self.repo.plan()
                self.assert_full(plan)
                self.assertIn(impact.NONPRODUCT_HISTORY, plan["unknown_paths"])

    def test_preexisting_new_dynamic_reader_invalidates_a_later_comment_only_diff(self):
        self.repo.write("crates/native/src/lib.rs", 'pub fn read_input() {\n'
                        ' let _ = std::fs::read_to_string("tooling/conformance_history_guard.py");\n}\n')
        self.repo.baseline = self.repo.commit("new consumer before comparison base")
        self.repo.write(impact.NONPRODUCT_HISTORY, "# Only a comment in this diff.\nVALUE = 1\n")
        self.repo.commit("later comment")
        plan = self.repo.plan()
        self.assertEqual(plan["changed_paths"], [impact.NONPRODUCT_HISTORY])
        self.assert_full(plan)
        self.assertIn("consumer inventory changed", plan["nonproduct_input_review"]["reason"])

    def test_missing_g0_changed_broad_discovery_and_mixed_product_changes_remain_full(self):
        for name, content in [(impact.PLANNER, "name: no G0\n"),
                              (".github/workflows/security-delivery.yml", "name: narrower discovery\n"),
                              ("crates/domain/src/lib.rs", "pub fn changed_contract() {}\n"),
                              ("unknown/input.dat", "unreviewed runtime input\n")]:
            with self.subTest(path=name):
                self.repo.call("reset", "--hard", self.repo.baseline)
                self.repo.write("README.md", "Prose change mixed with another input.\n")
                self.repo.write(name, content)
                self.repo.commit("mixed input change")
                self.assert_full(self.repo.plan())

    def test_candidate_and_dependency_boundaries_take_precedence_over_proof(self):
        self.repo.write("README.md", "Prose candidate.\n")
        self.repo.commit("prose")
        self.assert_full(self.repo.plan(candidate=True))
        self.repo.write("Cargo.lock", "# Synthetic dependency change\n")
        self.repo.commit("dependency boundary")
        self.assert_full(self.repo.plan())

    def test_added_renamed_deleted_and_executable_subjects_do_not_gain_exclusions(self):
        for action in ("delete", "rename", "mode"):
            with self.subTest(action=action):
                self.repo.call("reset", "--hard", self.repo.baseline)
                if action == "delete":
                    self.repo.call("rm", impact.NONPRODUCT_HISTORY)
                elif action == "rename":
                    self.repo.call("mv", impact.NONPRODUCT_HISTORY, "tooling/other_history.py")
                else:
                    (self.repo.root / impact.NONPRODUCT_HISTORY).chmod(0o755)
                    self.repo.call("update-index", "--chmod=+x", impact.NONPRODUCT_HISTORY)
                    self.repo.call("commit", "--no-gpg-sign", "-m", "mode change")
                    self.assert_full(self.repo.plan())
                    continue
                self.repo.commit("non-modification boundary")
                self.assert_full(self.repo.plan())

    def test_stale_review_cannot_ignore_an_old_compared_consumer_tree(self):
        original = self.repo.baseline
        self.repo.write("crates/native/src/lib.rs", "pub fn new_input_reader() {}\n")
        changed_consumer = self.repo.commit("different compared tree")
        self.repo.call("reset", "--hard", original)
        self.repo.write(impact.NONPRODUCT_HISTORY, "# Same AST.\nVALUE = 1\n")
        head = self.repo.commit("reviewed helper comment")
        trees = {sha: impact.Tree(self.repo.root, sha) for sha in (original, changed_consumer, head)}
        with self.assertRaisesRegex(impact.PlanError, "consumer inventory changed"):
            impact.reviewed_nonproduct_closure(trees=trees, paths={impact.NONPRODUCT_HISTORY},
                changes=[{"status": "M", "paths": [impact.NONPRODUCT_HISTORY]}])

    def test_unrelated_conformance_policy_changes_keep_transitive_consumer_scope_broad(self):
        self.repo.write("tooling/conformance_policy.py", "# Consumed by acceptance and creative policies.\n")
        self.repo.commit("unreviewed transitive policy input")
        self.assert_full(self.repo.plan())



if __name__ == "__main__":
    unittest.main()
