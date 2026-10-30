"""Synthetic metadata and temporary Git controls; no installer or release claims."""

from __future__ import annotations

import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

SPEC = importlib.util.spec_from_file_location(
    "release_scope_plan_under_test", Path(__file__).resolve().parents[1] / "release_scope_plan.py")
scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scope)

PATH = ".github/workflows/fixture.yml"
SOURCE = {"repository": "example/fixture", "sha": "a" * 40, "tree": "b" * 40}
WORKFLOW = b"""name: Fixture
on: [workflow_dispatch]
jobs:
  package:
    steps:
      - name: Compile candidate
        run: true
  upgrade:
    if: inputs.scope == 'lifecycle'
    steps:
      - name: Compile fixture
        run: true
      - name: Observe lifecycle
        if: inputs.scope == 'lifecycle'
        run: true
"""


def catalog():
    return {"schema": scope.CATALOG_SCHEMA, "workflows": [{
        "path": PATH, "sha256": scope.digest(WORKFLOW),
        "input_options": {"scope": ["all", "compile", "lifecycle"]},
        "input_dispositions": {"scope": {
            "all": {"covered_by": "packages", "basis": "Synthetic package fixture"},
            "compile": {"covered_by": "lifecycle", "basis": "Compile step is also required in lifecycle"},
            "lifecycle": {"covered_by": "lifecycle", "basis": "Separate guarded step"}}},
        "jobs": {
            "package": {"api_name": "package", "guard": None, "required_steps": [
                {"name": "Compile candidate", "guard": None}]},
            "upgrade": {"api_name": "upgrade", "guard": "inputs.scope == 'lifecycle'", "required_steps": [
                {"name": "Compile fixture", "guard": None},
                {"name": "Observe lifecycle", "guard": "inputs.scope == 'lifecycle'"}]}},
        "cases": [
            {"id": "packages", "inputs": {"scope": "all"}, "required_jobs": ["package"],
             "events": ["workflow_dispatch", "push"], "push_branch": "main",
             "run_title": "Fixture / all / {source_sha}", "purpose": "Synthetic packages"},
            {"id": "lifecycle", "inputs": {"scope": "lifecycle"}, "required_jobs": ["upgrade"],
             "events": ["workflow_dispatch"], "push_branch": None,
             "run_title": "Fixture / lifecycle / {source_sha}", "purpose": "Synthetic lifecycle"}],
        "limitations": ["Synthetic API metadata, never real installation evidence."]}]}


def observation(case_id):
    entry = catalog()["workflows"][0]
    case = next(value for value in entry["cases"] if value["id"] == case_id)
    run_id = 101 if case_id == "packages" else 102
    run = {"id": run_id, "run_attempt": 1, "repository": {"full_name": SOURCE["repository"]},
           "head_sha": SOURCE["sha"], "path": PATH, "event": "workflow_dispatch",
           "display_title": case["run_title"].replace("{source_sha}", SOURCE["sha"]),
           "status": "completed", "conclusion": "success"}
    jobs = []
    for index, job_key in enumerate(case["required_jobs"]):
        job = entry["jobs"][job_key]
        jobs.append({"id": run_id * 100 + index, "run_id": run_id, "run_attempt": 1,
                     "head_sha": SOURCE["sha"], "name": job["api_name"],
                     "status": "completed", "conclusion": "success",
                     "steps": [{"name": step["name"], "number": number,
                                "status": "completed", "conclusion": "success"}
                               for number, step in enumerate(job["required_steps"], 1)]})
    result = {"schema": scope.OBSERVATION_SCHEMA, "workflow": PATH,
              "workflow_sha256": entry["sha256"], "case_id": case_id,
              "source": copy.deepcopy(SOURCE), "inputs": copy.deepcopy(case["inputs"])}
    for kind, body in (("run", run), ("jobs", {"total_count": len(jobs), "jobs": jobs})):
        result[kind + "_json"] = json.dumps(body, indent=2) + "\n"
        result[kind + "_sha256"] = scope.digest(result[kind + "_json"].encode())
    return result


def edit_original(record, kind, edit):
    value = json.loads(record[kind + "_json"])
    edit(value)
    record[kind + "_json"] = json.dumps(value) + "\n"
    record[kind + "_sha256"] = scope.digest(record[kind + "_json"].encode())


class ScopeTests(unittest.TestCase):
    def plan(self, observations=None, *, source=None, workflows=None, review=None):
        return scope.build_plan(source or SOURCE, workflows or {PATH: WORKFLOW},
                                review or catalog(), observations)

    def outcome(self, plan, case_id="lifecycle"):
        return next(row["observation"] for row in plan["obligations"] if row["case_id"] == case_id)

    def assert_rejected(self, record, code):
        plan = self.plan([record])
        self.assertEqual(self.outcome(plan, record["case_id"]), {"status": "REJECTED", "code": code})
        self.assertFalse(plan["scope_observations_complete"])
        self.assertEqual(plan["release_verdict"], "NOT_GRANTED")

    def test_valid_metadata_is_explicitly_unadmitted(self):
        plan = self.plan([observation("packages"), observation("lifecycle")])
        self.assertTrue(plan["scope_plan_complete"])
        self.assertTrue(plan["scope_observations_complete"])
        self.assertEqual(plan["status"], "UNADMITTED_EXECUTION_OBSERVATIONS")
        self.assertEqual(plan["candidate_readiness"], "NOT_ASSESSED")
        self.assertEqual(plan["technical_criteria_verdict"], "NOT_GRANTED")
        self.assertEqual(plan["human_acceptance"], "NOT_RUN")
        self.assertEqual(plan["evidence_admission"], "NOT_PERFORMED")
        self.assertFalse(plan["automatic_dispatch"])
        self.assertFalse(plan["retry_authorized"])
        self.assertEqual(plan["accounting"]["executions_avoided"], 0)
        self.assertEqual(self.outcome(plan)["workflow_definition_sha"], SOURCE["sha"])

    def test_default_all_does_not_cover_guarded_lifecycle(self):
        plan = self.plan([observation("packages")])
        self.assertTrue(plan["scope_plan_complete"])
        self.assertFalse(plan["scope_observations_complete"])
        self.assertEqual(self.outcome(plan), {"status": "MISSING"})

    def test_no_observations_are_not_execution_coverage(self):
        plan = self.plan()
        self.assertFalse(plan["scope_observations_complete"])
        self.assertTrue(all(row["observation"]["status"] == "MISSING" for row in plan["obligations"]))

    def test_required_job_omission_is_rejected(self):
        record = observation("lifecycle")
        edit_original(record, "jobs", lambda value: value.update(total_count=0, jobs=[]))
        self.assert_rejected(record, "jobs_pagination_incomplete")

    def test_required_job_skip_cancel_or_failure_is_rejected(self):
        for conclusion in ("skipped", "cancelled", "failure", None):
            with self.subTest(conclusion=conclusion):
                record = observation("lifecycle")
                edit_original(record, "jobs",
                              lambda value: value["jobs"][0].update(conclusion=conclusion))
                self.assert_rejected(record, "required_job_not_successful")

    def test_green_job_cannot_hide_required_step_skip(self):
        for conclusion in ("skipped", "cancelled", "failure", None):
            with self.subTest(conclusion=conclusion):
                record = observation("lifecycle")
                edit_original(record, "jobs",
                              lambda value: value["jobs"][0]["steps"][-1].update(conclusion=conclusion))
                self.assert_rejected(record, "required_step_not_successful")

    def test_compile_step_is_required_alongside_lifecycle(self):
        record = observation("lifecycle")
        edit_original(record, "jobs", lambda value: value["jobs"][0]["steps"].pop(0))
        self.assert_rejected(record, "required_step_missing")

    def test_original_title_binds_scope_not_caller_label(self):
        record = observation("lifecycle")
        edit_original(record, "run", lambda value: value.update(
            display_title="Fixture / all / " + SOURCE["sha"]))
        self.assert_rejected(record, "run_original_scope_title_mismatch")

    def test_job_source_and_attempt_cannot_be_transferred(self):
        for key, value in (("head_sha", "c" * 40), ("run_id", 999), ("run_attempt", 2),
                           ("run_attempt", True)):
            with self.subTest(key=key, value=value):
                record = observation("lifecycle")
                edit_original(record, "jobs", lambda body: body["jobs"][0].update({key: value}))
                self.assert_rejected(record, "job_source_or_attempt_mismatch")

    def test_run_identity_and_terminal_status_are_exact(self):
        for key, value, code in (
            ("head_sha", "c" * 40, "run_source_or_workflow_mismatch"),
            ("path", ".github/workflows/other.yml", "run_source_or_workflow_mismatch"),
            ("id", True, "run_id"), ("run_attempt", False, "run_attempt"),
            ("event", "pull_request", "run_event_mismatch"),
            ("conclusion", "skipped", "run_not_successful"),
            ("status", "in_progress", "run_not_successful")):
            with self.subTest(key=key):
                record = observation("lifecycle")
                edit_original(record, "run", lambda body: body.update({key: value}))
                self.assert_rejected(record, code)

    def test_same_tree_new_commit_does_not_reuse_observation(self):
        source = dict(SOURCE, sha="d" * 40)
        plan = self.plan([observation("lifecycle")], source=source)
        self.assertEqual(self.outcome(plan)["code"], "observation_source_mismatch")

    def test_workflow_hash_or_scope_input_mismatch_is_rejected(self):
        for key, value, code in (
            ("workflow_sha256", "0" * 64, "observation_workflow_mismatch"),
            ("inputs", {"scope": "compile"}, "observation_scope_mismatch")):
            with self.subTest(key=key):
                record = observation("lifecycle")
                record[key] = value
                self.assert_rejected(record, code)

    def test_original_hashes_and_complete_pagination_are_required(self):
        record = observation("lifecycle")
        record["jobs_json"] += " "
        self.assert_rejected(record, "original_hash_mismatch")
        record = observation("lifecycle")
        edit_original(record, "jobs", lambda value: value.update(total_count=2))
        self.assert_rejected(record, "jobs_pagination_incomplete")

    def test_duplicate_jobs_steps_or_attempt_choices_are_rejected(self):
        record = observation("lifecycle")
        edit_original(record, "jobs", lambda value: value.update(
            total_count=2, jobs=value["jobs"] + value["jobs"]))
        self.assert_rejected(record, "duplicate_job_id")
        record = observation("lifecycle")
        edit_original(record, "jobs", lambda value: value["jobs"][0]["steps"][1].update(number=1))
        self.assert_rejected(record, "duplicate_step_number")
        plan = self.plan([observation("lifecycle"), observation("lifecycle")])
        self.assertEqual(self.outcome(plan)["code"], "select_one_original_run_attempt")
        self.assertFalse(plan["scope_observations_complete"])

    def test_unknown_workflow_or_scope_never_completes(self):
        plan = self.plan([observation("packages"), observation("lifecycle")],
                         workflows={PATH: WORKFLOW, ".github/workflows/new.yml": WORKFLOW})
        self.assertEqual(plan["status"], "NEEDS_REVIEW")
        self.assertFalse(plan["scope_plan_complete"])
        self.assertFalse(plan["scope_observations_complete"])
        record = observation("lifecycle")
        record["case_id"] = "unknown"
        plan = self.plan([record])
        self.assertEqual(plan["observation_errors"][0]["code"], "unreviewed_observation_scope")

    def test_missing_workflow_needs_review(self):
        plan = self.plan(workflows={".github/workflows/unreviewed.yml": WORKFLOW})
        self.assertIn("catalog_workflow_missing", {row["code"] for row in plan["review_gaps"]})

    def test_workflow_bytes_changing_invalidates_review(self):
        plan = self.plan(workflows={PATH: WORKFLOW + b"# changed definition\n"})
        self.assertEqual(plan["review_gaps"][0]["code"], "workflow_definition_changed")
        self.assertFalse(plan["scope_plan_complete"])

    def test_updating_hash_without_guard_review_is_insufficient(self):
        modified = WORKFLOW.replace(b"if: inputs.scope == 'lifecycle'", b"if: false", 1)
        review = catalog()
        review["workflows"][0]["sha256"] = scope.digest(modified)
        plan = self.plan(workflows={PATH: modified}, review=review)
        self.assertEqual(plan["review_gaps"][0]["code"], "workflow_job_guard_changed")
        modified = WORKFLOW.replace(b"        if: inputs.scope == 'lifecycle'", b"        if: false")
        review["workflows"][0]["sha256"] = scope.digest(modified)
        plan = self.plan(workflows={PATH: modified}, review=review)
        self.assertEqual(plan["review_gaps"][0]["code"], "workflow_step_guard_changed")

    def test_catalog_dispositions_and_jobs_must_be_complete(self):
        review = catalog()
        del review["workflows"][0]["input_dispositions"]["scope"]["compile"]
        with self.assertRaisesRegex(scope.ScopeError, "catalog_missing_input_disposition"):
            self.plan(review=review)
        review = catalog()
        review["workflows"][0]["cases"] = review["workflows"][0]["cases"][:1]
        with self.assertRaisesRegex(scope.ScopeError, "catalog_uncovered_job"):
            self.plan(review=review)

    def test_catalog_has_no_waiver_or_auto_acceptance_fields(self):
        review = catalog()
        review["automatic_acceptance"] = True
        with self.assertRaisesRegex(scope.ScopeError, "catalog_fields"):
            self.plan(review=review)
        record = observation("lifecycle")
        record["skip_waiver"] = True
        self.assert_rejected(record, "observation_fields")

    def test_duplicate_json_keys_and_nonfinite_values_are_rejected(self):
        for raw in (b'{"a": 1, "a": 2}', b'{"a": NaN}'):
            with self.subTest(raw=raw), self.assertRaises(scope.ScopeError):
                scope.decode(raw)

    def test_only_main_push_can_observe_packages(self):
        record = observation("packages")
        edit_original(record, "run", lambda value: value.update(event="push", head_branch="main"))
        plan = self.plan([record])
        self.assertEqual(self.outcome(plan, "packages")["status"], "UNADMITTED_EXECUTION_OBSERVATIONS")
        edit_original(record, "run", lambda value: value.update(head_branch="feature"))
        self.assert_rejected(record, "run_push_branch_mismatch")


class GitSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mw-release-scope-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.call("init", "--initial-branch=main")
        self.call("config", "user.name", "Synthetic scope fixture")
        self.call("config", "user.email", "fixture@example.invalid")
        self.call("config", "commit.gpgsign", "false")
        self.call("config", "core.autocrlf", "false")
        hooks = self.repo / ".fixture-empty-hooks"
        hooks.mkdir()
        self.call("config", "core.hooksPath", str(hooks))
        self.write(PATH, WORKFLOW)
        self.initial = self.commit("synthetic initial workflow")

    def call(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
        return subprocess.check_output(["git", "-C", str(self.repo), *args],
                                       env=env, stderr=subprocess.STDOUT).decode().strip()

    def write(self, path, raw):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    def commit(self, message):
        self.call("add", "--all")
        self.call("commit", "--no-gpg-sign", "-m", message)
        return self.call("rev-parse", "HEAD")

    def test_reads_committed_objects_not_dirty_worktree(self):
        self.write(PATH, b"dirty bytes must not become candidate input")
        tree, files = scope.read_source(self.repo, self.initial)
        self.assertEqual(files[PATH], WORKFLOW)
        self.assertEqual(tree, self.call("rev-parse", self.initial + "^{tree}"))

    def test_non_commit_is_refused(self):
        blob = self.call("rev-parse", self.initial + ":" + PATH)
        with self.assertRaisesRegex(scope.ScopeError, "source_not_commit"):
            scope.read_source(self.repo, blob)

    def test_git_replace_cannot_substitute_candidate_source(self):
        self.write(PATH, WORKFLOW + b"# unrelated replacement\n")
        replacement = self.commit("replacement fixture")
        self.call("replace", self.initial, replacement)
        unused_tree, files = scope.read_source(self.repo, self.initial)
        self.assertEqual(files[PATH], WORKFLOW)



REVIEW_PATH = ".github/workflows/reviewed.yml"
REVIEW_PREAMBLE = """name: Reviewed fixture
on:
  workflow_dispatch:
    inputs:
      mode:
        type: choice
        default: full
        options: [full, preflight]
      repeat:
        type: boolean
        default: true
"""
REVIEW_HEADER = """    if: >-
      github.event_name == 'workflow_dispatch'
    strategy:
      matrix:
        os: [windows, macos]
    steps:
"""
REVIEW_STEPS = [
    "    - run: echo compile",
    "    - name: Observe repeat\n      if: inputs.repeat\n      run: echo observed",
    "    - name: Retain originals\n      if: always()\n      uses: actions/upload-artifact@fixture\n",
]
REVIEW_JOB = REVIEW_HEADER + "\n".join(REVIEW_STEPS)
REVIEW_WORKFLOW = (REVIEW_PREAMBLE + "jobs:\n  native:\n" + REVIEW_JOB).encode()


def reviewed_catalog():
    matrices = [{"os": "windows"}, {"os": "macos"}]
    job = {
        "source_name": "native", "guard": ">-\n      github.event_name == 'workflow_dispatch'",
        "runs_on": None, "needs": None, "strategy": {"matrix": {"os": ["windows", "macos"]}},
        "source_sha256": scope.digest(REVIEW_JOB.encode()),
        "instance_sets": {"full": copy.deepcopy(matrices)},
        "steps": [
            {"ordinal": 1, "name": None, "guard": None, "kind": "EXECUTABLE_STEP",
             "source_block_sha256": scope.digest(REVIEW_STEPS[0].encode()),
             "api_binding": "REQUIRES_ORIGINAL_RUN_MAPPING"},
            {"ordinal": 2, "name": "Observe repeat", "guard": "inputs.repeat",
             "kind": "EXECUTABLE_STEP",
             "source_block_sha256": scope.digest(REVIEW_STEPS[1].encode()),
             "api_binding": "EXPLICIT_NAME"},
            {"ordinal": 3, "name": "Retain originals", "guard": "always()",
             "kind": "ARTIFACT_RETENTION",
             "source_block_sha256": scope.digest(REVIEW_STEPS[2].encode()),
             "api_binding": "EXPLICIT_NAME"},
        ],
    }
    entry = {
        "path": REVIEW_PATH, "sha256": scope.digest(REVIEW_WORKFLOW),
        "preamble_sha256": scope.digest(REVIEW_PREAMBLE.encode()),
        "evidence_class": "synthetic_fixture", "purpose": "Synthetic planning fixture only.",
        "inputs": {
            "mode": {"type": "choice", "required": False, "default": "full",
                     "options": ["full", "preflight"]},
            "repeat": {"type": "boolean", "required": False, "default": True, "options": []},
        },
        "input_dispositions": {
            "mode": {value: {"covered_by": ["full"], "basis": "The full synthetic scope includes preflight."}
                     for value in ("full", "preflight")},
            "repeat": {value: {"covered_by": ["full"], "basis": "The selected repeat includes shared setup."}
                       for value in ("true", "false")},
        },
        "jobs": {"native": job},
        "cases": [{
            "id": "full", "inputs": {"mode": "full", "repeat": True},
            "kind": "TECHNICAL_SCOPE", "original_attempt": None, "blocking_reason": None,
            "coverage_basis": "Both synthetic platform instances require compile, repeat and retention.",
            "required_job_instances": [
                {"job_id": "native", "display_name": "native (" + row["os"] + ")",
                 "matrix": row, "name_binding": "proposed_github_default_requires_original_readback",
                 "required_steps": [1, 2, 3]} for row in matrices],
        }],
        "job_dispositions": [],
        "limitations": ["Synthetic source definition; no physical execution."],
    }
    return {"schema": scope.REVIEWED_CATALOG_SCHEMA, "workflows": [],
            "reviewed_scopes": [entry], "limitations": ["Descriptive planning only."]}


class ReviewedScopeTests(unittest.TestCase):
    def plan(self, review=None, observations=None, workflows=None):
        return scope.build_plan(SOURCE, workflows or {REVIEW_PATH: REVIEW_WORKFLOW},
                                review or reviewed_catalog(), observations)

    def test_reviewed_definition_is_not_execution_or_release_evidence(self):
        plan = self.plan()
        self.assertTrue(plan["definition_review_complete"])
        self.assertTrue(plan["scope_plan_complete"])
        self.assertFalse(plan["scope_observations_complete"])
        self.assertEqual(plan["review_gaps"], [])
        self.assertEqual(plan["definitions"],
                         [{"workflow": REVIEW_PATH, "definition_review": "REVIEWED"}])
        self.assertEqual(plan["status"], "READBACK_REQUIRED")
        self.assertEqual(plan["obligations"][0]["observation"]["status"], "READBACK_REQUIRED")
        self.assertEqual(plan["release_verdict"], "NOT_GRANTED")
        self.assertEqual(plan["candidate_readiness"], "NOT_ASSESSED")
        self.assertEqual(plan["authority"]["ownership_or_budget_lease"], "NOT_VALIDATED")
        self.assertFalse(plan["authority"]["authorizes_dispatch"])
        self.assertFalse(plan["automatic_dispatch"])
        self.assertFalse(plan["retry_authorized"])

    def test_api_success_or_skip_cannot_implement_an_unsupported_binding(self):
        for conclusion in ("success", "skipped"):
            record = {"workflow": REVIEW_PATH, "case_id": "full", "status": "success",
                      "source": copy.deepcopy(SOURCE), "steps": [{"conclusion": conclusion}]}
            plan = self.plan(observations=[record])
            result = plan["obligations"][0]["observation"]
            self.assertEqual(result["status"], "READBACK_REQUIRED")
            self.assertEqual(result["supplied_observation_count"], 1)
            self.assertEqual(result["evidence_admission"], "NOT_PERFORMED")
            self.assertFalse(plan["scope_observations_complete"])

    def test_supported_package_observations_remain_strict_in_schema_two(self):
        review = reviewed_catalog()
        review["workflows"] = catalog()["workflows"]
        records = [observation("packages"), observation("lifecycle")]
        plan = self.plan(review, records, {PATH: WORKFLOW, REVIEW_PATH: REVIEW_WORKFLOW})
        supported = [row for row in plan["obligations"] if row["workflow"] == PATH]
        self.assertTrue(all(row["observation"]["status"] == "UNADMITTED_EXECUTION_OBSERVATIONS"
                            for row in supported))
        self.assertFalse(plan["scope_observations_complete"])
        edit_original(records[1], "jobs",
                      lambda value: value["jobs"][0]["steps"][-1].update(conclusion="skipped"))
        plan = self.plan(review, records, {PATH: WORKFLOW, REVIEW_PATH: REVIEW_WORKFLOW})
        failed = next(row for row in plan["obligations"] if row["case_id"] == "lifecycle")
        self.assertEqual(failed["observation"]["code"], "required_step_not_successful")

    def test_boolean_strings_are_not_coerced(self):
        review = reviewed_catalog()
        review["reviewed_scopes"][0]["cases"][0]["inputs"]["repeat"] = "true"
        with self.assertRaisesRegex(scope.ScopeError, "catalog_boolean_input"):
            self.plan(review)

    def test_every_declared_choice_requires_a_disposition(self):
        review = reviewed_catalog()
        del review["reviewed_scopes"][0]["input_dispositions"]["mode"]["preflight"]
        with self.assertRaisesRegex(scope.ScopeError, "reviewed_missing_input_disposition"):
            self.plan(review)

    def test_one_matrix_instance_does_not_cover_both_platforms(self):
        review = reviewed_catalog()
        review["reviewed_scopes"][0]["cases"][0]["required_job_instances"].pop()
        with self.assertRaisesRegex(scope.ScopeError, "reviewed_matrix_member_missing"):
            self.plan(review)

    def test_duplicate_matrix_instance_is_not_additional_coverage(self):
        review = reviewed_catalog()
        rows = review["reviewed_scopes"][0]["cases"][0]["required_job_instances"]
        rows[1] = copy.deepcopy(rows[0])
        with self.assertRaisesRegex(scope.ScopeError, "duplicate_reviewed_instance"):
            self.plan(review)

    def test_unchanged_step_hash_does_not_cover_changed_workflow_bytes(self):
        plan = self.plan(workflows={REVIEW_PATH: REVIEW_WORKFLOW + b"# changed\n"})
        self.assertFalse(plan["definition_review_complete"])
        self.assertEqual(plan["review_gaps"][0]["code"], "workflow_definition_changed")

    def test_updating_workflow_hash_does_not_skip_dispatch_input_review(self):
        changed = REVIEW_WORKFLOW.replace(b"default: full", b"default: preflight")
        review = reviewed_catalog()
        review["reviewed_scopes"][0]["sha256"] = scope.digest(changed)
        plan = self.plan(review, workflows={REVIEW_PATH: changed})
        self.assertEqual(plan["review_gaps"][0]["code"], "workflow_declared_inputs_changed")

    def test_updating_workflow_hash_does_not_skip_matrix_source_review(self):
        changed = REVIEW_WORKFLOW.replace(b"os: [windows, macos]", b"os: [windows]")
        review = reviewed_catalog()
        review["reviewed_scopes"][0]["sha256"] = scope.digest(changed)
        plan = self.plan(review, workflows={REVIEW_PATH: changed})
        self.assertEqual(plan["review_gaps"][0]["code"], "workflow_job_source_changed")

    def test_folded_job_guard_changes_invalidate_definition(self):
        changed = REVIEW_WORKFLOW.replace(b"github.event_name == 'workflow_dispatch'", b"false")
        review = reviewed_catalog()
        review["reviewed_scopes"][0]["sha256"] = scope.digest(changed)
        plan = self.plan(review, workflows={REVIEW_PATH: changed})
        self.assertEqual(plan["review_gaps"][0]["code"], "workflow_job_guard_changed")

    def test_moved_step_ordinal_cannot_keep_another_steps_binding(self):
        changed_job = REVIEW_HEADER + "\n".join(
            [REVIEW_STEPS[1], REVIEW_STEPS[0], REVIEW_STEPS[2]])
        changed = (REVIEW_PREAMBLE + "jobs:\n  native:\n" + changed_job).encode()
        review = reviewed_catalog()
        entry = review["reviewed_scopes"][0]
        entry["sha256"] = scope.digest(changed)
        entry["jobs"]["native"]["source_sha256"] = scope.digest(changed_job.encode())
        plan = self.plan(review, workflows={REVIEW_PATH: changed})
        self.assertEqual(plan["review_gaps"][0]["code"], "workflow_step_guard_changed")

    def test_executable_step_omission_is_not_a_complete_catalog(self):
        review = reviewed_catalog()
        entry = review["reviewed_scopes"][0]
        entry["jobs"]["native"]["steps"].pop(1)
        for instance in entry["cases"][0]["required_job_instances"]:
            instance["required_steps"].remove(2)
        plan = self.plan(review)
        self.assertEqual(plan["review_gaps"][0]["code"], "workflow_executable_step_inventory_changed")

    def test_unknown_workflow_and_duplicate_catalog_path_are_not_reviewed(self):
        plan = self.plan(workflows={REVIEW_PATH: REVIEW_WORKFLOW,
                                   ".github/workflows/new.yml": REVIEW_WORKFLOW})
        self.assertFalse(plan["definition_review_complete"])
        self.assertEqual(plan["review_gaps"][0]["code"], "workflow_not_reviewed")
        review = reviewed_catalog()
        review["reviewed_scopes"].append(copy.deepcopy(review["reviewed_scopes"][0]))
        with self.assertRaisesRegex(scope.ScopeError, "duplicate_catalog_workflow"):
            self.plan(review)

    def test_historical_disposition_blocks_plan_without_erasing_review(self):
        review = reviewed_catalog()
        case = review["reviewed_scopes"][0]["cases"][0]
        case["kind"] = "NEEDS_DISPOSITION"
        case["blocking_reason"] = "Historical precondition requires explicit succession."
        plan = self.plan(review)
        self.assertTrue(plan["definition_review_complete"])
        self.assertFalse(plan["scope_plan_complete"])
        self.assertEqual(plan["status"], "NEEDS_DISPOSITION")
        self.assertEqual(plan["scope_dispositions"][0]["status"], "NEEDS_DISPOSITION")

    def test_pinned_product_is_not_an_execution_of_the_candidate(self):
        review = reviewed_catalog()
        entry = review["reviewed_scopes"][0]
        entry["inputs"]["source_sha"] = {
            "type": "string", "required": True, "default": "c" * 40, "options": []}
        entry["input_dispositions"]["source_sha"] = {}
        case = entry["cases"][0]
        case["inputs"]["source_sha"] = "c" * 40
        case["kind"] = "PINNED_PRODUCT_DIAGNOSTIC"
        case["blocking_reason"] = "Pinned product requires source reconciliation."
        preamble = REVIEW_PREAMBLE + (
            "      source_sha:\n        type: string\n        required: true\n"
            "        default: " + "c" * 40 + "\n")
        changed = (preamble + "jobs:\n  native:\n" + REVIEW_JOB).encode()
        entry["sha256"] = scope.digest(changed)
        entry["preamble_sha256"] = scope.digest(preamble.encode())
        plan = self.plan(review, workflows={REVIEW_PATH: changed})
        self.assertTrue(plan["definition_review_complete"])
        self.assertFalse(plan["scope_plan_complete"])
        self.assertEqual(plan["scope_dispositions"][0]["status"], "NEEDS_SOURCE_RECONCILIATION")
        self.assertEqual(plan["obligations"][0]["execution_identity_role"],
                         "PINNED_PRODUCT_ORIGINALS")
        self.assertEqual(plan["obligations"][0]["inputs"]["source_sha"], "c" * 40)
        self.assertEqual(plan["source"]["sha"], "a" * 40)
        self.assertFalse(plan["scope_observations_complete"])

    def test_component_prerequisite_keeps_its_own_identity_role(self):
        review = reviewed_catalog()
        review["reviewed_scopes"][0]["cases"][0]["kind"] = "COMPONENT_PREREQUISITE"
        plan = self.plan(review)
        self.assertTrue(plan["scope_plan_complete"])
        self.assertEqual(plan["obligations"][0]["execution_identity_role"],
                         "INDEPENDENT_COMPONENT_ORIGINALS")
        self.assertEqual(plan["obligations"][0]["observation"]["status"], "READBACK_REQUIRED")

    def test_prior_sha_and_mixed_attempt_claims_do_not_become_admitted(self):
        records = [{"workflow": REVIEW_PATH, "case_id": "full",
                    "source_sha": "c" * 40, "run_attempt": attempt, "status": "success"}
                   for attempt in (1, 2)]
        plan = self.plan(observations=records)
        result = plan["obligations"][0]["observation"]
        self.assertEqual(result["status"], "READBACK_REQUIRED")
        self.assertEqual(result["supplied_observation_count"], 2)
        self.assertFalse(plan["scope_observations_complete"])

    def test_reviewed_catalog_has_no_execution_or_skip_waiver_switch(self):
        review = reviewed_catalog()
        review["reviewed_scopes"][0]["skip_waiver"] = True
        with self.assertRaisesRegex(scope.ScopeError, "reviewed_scope_fields"):
            self.plan(review)
        review = reviewed_catalog()
        review["execute"] = True
        with self.assertRaisesRegex(scope.ScopeError, "catalog_fields"):
            self.plan(review)


class ObservationFileBoundaryTests(unittest.TestCase):
    def test_reads_complete_small_regular_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.json"
            path.write_bytes(b'[{"run_attempt": 1}]')
            self.assertEqual(scope.read_json_file(path), [{"run_attempt": 1}])

    def test_oversized_original_is_rejected_before_open_or_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.json"
            with path.open("wb") as stream:
                stream.seek(scope.MAX_JSON)
                stream.write(b" ")
            with mock.patch.object(Path, "open",
                                   side_effect=AssertionError("oversized file opened")):
                with self.assertRaisesRegex(scope.ScopeError, "observation_file_size_or_type"):
                    scope.read_json_file(path)

    def test_mutation_between_stat_and_open_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.json"
            path.write_bytes(b"[]")
            original_open = Path.open

            def changing_open(actual, mode="r", *args, **kwargs):
                if actual == path and mode == "rb":
                    with original_open(path, "wb") as stream:
                        stream.write(b'[{"different": true}]')
                return original_open(actual, mode, *args, **kwargs)

            with mock.patch.object(Path, "open", changing_open):
                with self.assertRaisesRegex(scope.ScopeError, "observation_file_changed"):
                    scope.read_json_file(path)


class RetainedTypedReaderTests(unittest.TestCase):
    """Synthetic controls only; production trust pins are never modified on disk."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="typed-reader-synthetic-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.reader_root = self.root / "reader"
        self.execution_root = self.reader_root / "execution"
        self.originals, self.integrity = self.root / "originals", self.root / "integrity"
        for path in (self.execution_root, self.originals, self.integrity):
            path.mkdir(parents=True, exist_ok=True)
        self.product = {"repository": "synthetic/fixture", "sha": "c" * 40, "tree": "d" * 40}
        self.source = {"gate_sha": "a" * 40, "gate_tree": "b" * 40,
                       "product_sha": self.product["sha"], "product_tree": self.product["tree"]}
        self.github = {"repository": "synthetic/fixture", "run_id": 101, "attempt": 1,
                       "event": "workflow_dispatch", "workflow": ".github/workflows/fixture.yml",
                       "jobs": {"g0": 201, "compile": 202}, "artifact_ids": [301, 302]}
        jobs, artifacts, custody = {}, {}, {"artifacts": []}
        for job_id, artifact_id, name in ((201, 301, "g0"), (202, 302, "compile")):
            log_path = self.originals / ("job-" + str(job_id) + ".log.original")
            log = self.write(log_path, b"synthetic retained job log")
            jobs[str(job_id)] = {"name": name, "steps": [{"number": 1, "name": "Synthetic required step"}],
                                 "log": {**log, "path": log_path.name}}
            body = b"synthetic content; never executed"
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("original.bin", body)
            archive_path = self.originals / ("artifact-" + str(artifact_id) + ".zip.original")
            archive_identity = self.write(archive_path, stream.getvalue())
            artifacts[str(artifact_id)] = {**archive_identity, "name": name, "member_count": 1,
                                           "expanded_bytes": len(body)}
            retained = self.integrity / "extract" / ("artifact-" + str(artifact_id)) / "original.bin"
            retained.parent.mkdir(parents=True)
            member = self.write(retained, body)
            custody["artifacts"].append({"id": artifact_id, "members": [{**member,
                "path": "original.bin", "directory": False, "original_crc_checked": True,
                "retained_in_original_zip": True,
                "extracted_path": "extract/artifact-" + str(artifact_id) + "/original.bin"}]})
        self.metadata = {
            "run.json.original": {"id": 101, "run_attempt": 1, "head_sha": "a" * 40},
            "jobs.json.original": {"jobs": [{"id": 201, "steps": [{"name": "Synthetic required step", "conclusion": "success"}]}]},
            "artifacts.json.original": {"artifacts": [301, 302]},
            "archive-index.json": {"artifacts": [301, 302]}, "custody.json": custody}
        metadata_records = {}
        for name, value in self.metadata.items():
            root = self.integrity if name == "custody.json" else self.originals
            metadata_records[name] = self.write(root / name, scope.canonical(value))
        self.pins = {"jobs": jobs, "artifacts": artifacts, "artifact_ids": [301, 302],
                     "metadata": {name: scope.digest(scope.canonical(value))
                                  for name, value in self.metadata.items()}}
        binding_path = self.root / "git-bindings.json"
        git_identity = self.write(binding_path, b'{"synthetic":true}')
        reader_text = "\n".join([
            "PINS = json.loads(" + repr(json.dumps(self.pins)) + ")",
            "GATE = " + repr(self.source["gate_sha"]), "PRODUCT = " + repr(self.source["product_sha"]),
            "TREES = " + repr({"gate": self.source["gate_tree"], "product": self.source["product_tree"]}),
            "RUN, ATTEMPT = 101, 1", "REPO, REPO_ID = 'synthetic/fixture', 999",
            "WORKFLOW = '.github/workflows/fixture.yml'",
            "GIT_BINDING_SHA = " + repr(git_identity["sha256"]),
            "GIT_BINDING_BYTES = " + str(git_identity["bytes"]),
            "raise AssertionError('reader code must never execute')", ""])
        self.reader_path = self.reader_root / "retained_reader.py"
        reader_identity = self.write(self.reader_path, reader_text.encode())
        patch = mock.patch.dict(scope.RETAINED_READERS, {scope.RETAINED_COMPILE_FAMILY: reader_identity})
        patch.start()
        self.addCleanup(patch.stop)
        source_before = [{**reader_identity, "path": str(self.reader_path)}]
        for name in ("reader_controls.py", "stability_controls.py"):
            path = self.reader_root / name
            source_before.append({**self.write(path, b"synthetic control source"), "path": str(path)})
        self.report = {
            "schema": "motionwright.private-nsis-compile-admission/1",
            "status": "EXACT_TWO_VARIANT_COMPILE_OBSERVATIONS_ADMITTED_WITH_LIMITATIONS",
            "reader_implementation": {**reader_identity, "path": str(self.reader_path)},
            "source": copy.deepcopy(self.source), "github": copy.deepcopy(self.github),
            "originals": {"archives": copy.deepcopy(artifacts), "metadata": metadata_records,
                "all_77_zip_members_crc_and_sha256_reread": True,
                "all_77_retained_extraction_members_sha256_reread": True,
                "original_bytes_modified": False},
            "unexecuted_scopes": {"physical_gate_implementation": "NOT_IMPLEMENTED_IN_THIS_GATE",
                "setup_execution": "NOT_RUN", "application_execution": "NOT_RUN", "installation": "NOT_RUN",
                "upgrade": "NOT_RUN", "interruption": "NOT_RUN", "recovery": "NOT_RUN",
                "installed_edit_reopen": "NOT_RUN", "predecessor_profile": "NOT_ADMITTED", "human_acceptance": "NOT_RUN"},
            "decision_limits": {"technical_criteria_verdict": "NOT_ISSUED", "release_readiness": "NOT_EVALUATED",
                "release_publication": False, "other_sha_result_transfer": False,
                "autodispatch_or_retry": False, "product_reexecuted_by_reader": False}}
        self.report_path = self.reader_root / "report.json"
        report_identity = self.write(self.report_path, scope.canonical(self.report))
        self.execution = {"schema": "motionwright.a6.read-only-original-admission-execution/1",
            "status": "READER_EXIT_OBSERVED", "exit_code": 0, "native_retry": False,
            "originals_written": False, "product_execution": False, "source_unchanged": True,
            "source_before": source_before, "source_after": copy.deepcopy(source_before),
            "report": {**report_identity, "path": str(self.report_path)}}
        for key, name, raw in (("stdout", "stdout.log", b"synthetic bounded read"),
                               ("stderr", "stderr.log", b""),
                               ("input_control_receipt", "controls.json", b"{}")):
            path = self.execution_root / name
            self.execution[key] = {**self.write(path, raw), "path": str(path)}
        self.execution_path = self.execution_root / "receipt.json"
        execution_identity = self.write(self.execution_path, scope.canonical(self.execution))
        self.locator = {"family": scope.RETAINED_COMPILE_FAMILY, "reader": str(self.reader_path),
            "report": str(self.report_path), "execution_receipt": str(self.execution_path),
            "originals": str(self.originals), "integrity": str(self.integrity), "git_bindings": str(binding_path)}
        self.binding = {"family": scope.RETAINED_COMPILE_FAMILY,
                        "report": report_identity, "execution_receipt": execution_identity}

    @staticmethod
    def write(path, raw):
        path.write_bytes(raw)
        return {"bytes": len(raw), "sha256": scope.digest(raw)}

    def rebind(self):
        self.binding["report"] = self.write(self.report_path, scope.canonical(self.report))
        self.execution["report"].update(self.binding["report"])
        self.binding["execution_receipt"] = self.write(self.execution_path, scope.canonical(self.execution))

    def plan(self, *, candidate=None, bindings=True, locator=None):
        plan = scope.build_plan(candidate or self.product, {PATH: WORKFLOW}, catalog())
        return scope.attach_typed_readbacks(plan,
            {"schema": scope.TYPED_LOCATORS_SCHEMA, "entries": [locator or self.locator]},
            {"schema": scope.TYPED_BINDINGS_SCHEMA, "entries": [self.binding]} if bindings else None)

    def row(self, **kwargs):
        return self.plan(**kwargs)["typed_evidence"]["readbacks"][0]

    def test_exact_retained_inputs_are_read_without_running_reader_or_product(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("no process authorized")):
            row = self.row()
        self.assertEqual(row["status"], "HISTORICAL_RETAINED_BYTES_VERIFIED")
        self.assertEqual(row["retained_members_rehashed"], 2)
        self.assertEqual(row["reader_execution"], "NOT_REEXECUTED")
        self.assertEqual(row["product_execution"], "NOT_RUN")
        self.assertEqual(row["binding_authentication"], "EXTERNAL_REVIEW_REQUIRED_NOT_AUTHENTICATED_BY_PLANNER")
        self.assertTrue(row["requested_product_identity_matches"])

    def test_compile_even_same_product_never_changes_scope_completeness(self):
        before = scope.build_plan(self.product, {PATH: WORKFLOW}, catalog())
        after = self.plan()
        for key in ("status", "scope_plan_complete", "scope_observations_complete", "evidence_admission",
                    "candidate_readiness", "release_verdict", "human_acceptance", "retry_authorized"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.row()["candidate_disposition"], "READBACK_REQUIRED")
        self.assertEqual(self.row()["unexecuted_scopes"]["installation"], "NOT_RUN")

    def test_foreign_source_is_explicit_historical_not_transferred(self):
        for key in ("sha", "tree", "repository"):
            candidate = dict(self.product)
            candidate[key] = "foreign/repository" if key == "repository" else "f" * 40
            with self.subTest(key=key):
                row = self.row(candidate=candidate)
                self.assertFalse(row["requested_product_identity_matches"])
                self.assertEqual(row["candidate_disposition"], "READBACK_REQUIRED")

    def test_missing_binding_does_not_read_inputs(self):
        with mock.patch.object(scope, "_typed_read", side_effect=AssertionError("no binding")):
            self.assertEqual(self.row(bindings=False)["code"], "typed_reviewed_binding_missing")

    def test_unsupported_family_does_not_read_or_execute(self):
        locator = {"family": "native-video-unreviewed/1", "status": "PASS"}
        with mock.patch.object(scope, "_typed_read", side_effect=AssertionError("unsupported")):
            self.assertEqual(self.row(locator=locator)["code"], "typed_family_unsupported")

    def test_changed_known_reader_is_rejected_before_parsing(self):
        self.reader_path.write_bytes(self.reader_path.read_bytes().replace(b"raise", b"print"))
        with mock.patch.object(scope, "_retained_reader_constants", side_effect=AssertionError("untrusted code parsed")):
            self.assertEqual(self.row()["code"], "typed_original_digest_mismatch")

    def test_unreviewed_report_hash_is_rejected(self):
        self.report["status"] = "PASS"
        self.write(self.report_path, scope.canonical(self.report))
        self.assertIn(self.row()["code"], ("typed_original_size_mismatch", "typed_original_digest_mismatch"))

    def test_self_rebound_pass_json_cannot_claim_supported_scope(self):
        self.report["status"] = "PASS"
        self.rebind()
        self.assertEqual(self.row()["code"], "typed_retained_report_scope")

    def test_report_without_originals_is_never_sufficient(self):
        (self.originals / "run.json.original").unlink()
        self.assertEqual(self.row()["status"], "READBACK_REQUIRED")

    def test_report_identity_swaps_refused_even_with_rebound_report_hash(self):
        original = copy.deepcopy(self.report)
        for section, key, replacement in (
                ("source", "gate_sha", "e" * 40), ("source", "product_sha", "e" * 40),
                ("source", "gate_tree", "e" * 40), ("source", "product_tree", "e" * 40),
                ("github", "repository", "foreign/repository"), ("github", "run_id", 102),
                ("github", "attempt", 2), ("github", "attempt", True),
                ("github", "workflow", ".github/workflows/other.yml"),
                ("github", "jobs", {"g0": 201, "compile": 203}),
                ("github", "artifact_ids", [301, 303])):
            with self.subTest(section=section, key=key, replacement=replacement):
                self.report = copy.deepcopy(original)
                self.report[section][key] = replacement
                self.rebind()
                self.assertEqual(self.row()["code"], "typed_retained_identity_mismatch")

    def test_omitted_skipped_or_changed_step_original_refused_despite_rebinding(self):
        original = copy.deepcopy(self.metadata["jobs.json.original"])
        for steps in ([], [{"name": "Synthetic required step", "conclusion": "skipped"}],
                       [{"name": "Different step", "conclusion": "success"}]):
            with self.subTest(steps=steps):
                value = copy.deepcopy(original)
                value["jobs"][0]["steps"] = steps
                identity = self.write(self.originals / "jobs.json.original", scope.canonical(value))
                self.report["originals"]["metadata"]["jobs.json.original"] = identity
                self.rebind()
                self.assertEqual(self.row()["code"], "typed_reader_original_binding")

    def test_unknown_stays_unknown_when_report_is_missing(self):
        self.execution.update(status="UNKNOWN", exit_code=None)
        self.rebind()
        self.report_path.unlink()
        row = self.row()
        self.assertEqual(row["code"], "typed_execution_unknown")
        self.assertEqual(row["execution_outcome"], "UNKNOWN")
        self.assertFalse(row["retry_authorized"])

    def test_nonzero_boolean_or_omitted_exit_refused(self):
        for value in (1, False, None):
            with self.subTest(exit_code=value):
                self.execution["exit_code"] = value
                self.rebind()
                self.assertEqual(self.row()["status"], "READBACK_REQUIRED")

    def test_changed_source_before_after_refused(self):
        self.execution["source_after"][0]["sha256"] = "f" * 64
        self.rebind()
        self.assertEqual(self.row()["code"], "typed_reader_source_changed")

    def test_effectful_receipt_refused(self):
        for key in ("native_retry", "originals_written", "product_execution"):
            with self.subTest(key=key):
                self.execution[key] = True
                self.rebind()
                self.assertEqual(self.row()["code"], "typed_execution_effect_not_read_only")
                self.execution[key] = False

    def test_compile_report_cannot_promote_installation_or_human_acceptance(self):
        for key in ("installation", "upgrade", "interruption", "recovery", "human_acceptance"):
            with self.subTest(key=key):
                self.report["unexecuted_scopes"][key] = "PASS"
                self.rebind()
                self.assertEqual(self.row()["code"], "typed_compile_scope_promoted")
                self.report["unexecuted_scopes"][key] = "NOT_RUN"

    def test_altered_archive_or_crc_bytes_are_refused_without_extraction(self):
        path = self.originals / "artifact-302.zip.original"
        raw = bytearray(path.read_bytes())
        raw[14] ^= 1
        path.write_bytes(raw)
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("no archive reparsing")):
            self.assertEqual(self.row()["code"], "typed_original_digest_mismatch")

    def test_changed_extracted_payload_is_refused(self):
        path = self.integrity / "extract/artifact-302/original.bin"
        path.write_bytes(path.read_bytes().replace(b"content", b"changed"))
        self.assertEqual(self.row()["code"], "typed_original_digest_mismatch")

    def test_missing_or_additional_extracted_member_is_refused(self):
        root = self.integrity / "extract/artifact-302"
        (root / "unexpected.bin").write_bytes(b"extra")
        self.assertEqual(self.row()["code"], "typed_retained_member_roster")
        (root / "unexpected.bin").unlink()
        (root / "original.bin").unlink()
        self.assertEqual(self.row()["code"], "typed_retained_member_roster")

    def test_unsafe_member_names_are_rejected(self):
        for name in ("../escape", "/absolute", "a//b", "a/./b", "a\\b", "C:drive", "CON.txt", "a.", "a ", "a\0b"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(scope.ScopeError, "typed_unsafe_member"):
                    scope._typed_member(name)

    def test_oversize_or_capture_budget_rejected_before_open(self):
        budget = {"streamed_bytes": 0, "files_read": 0}
        with mock.patch.object(Path, "open", side_effect=AssertionError("oversize opened")):
            with self.assertRaisesRegex(scope.ScopeError, "typed_expected_size"):
                scope._typed_read(self.reader_path, {"bytes": scope.TYPED_MAX_SINGLE + 1, "sha256": "f" * 64}, budget)
            with self.assertRaisesRegex(scope.ScopeError, "typed_capture_size"):
                scope._typed_read(self.reader_path, {"bytes": scope.TYPED_MAX_CAPTURE + 1, "sha256": "f" * 64}, budget, capture=True)

    def test_stream_budget_cannot_be_reset_between_files(self):
        budget = {"streamed_bytes": scope.TYPED_MAX_STREAMED, "files_read": 0}
        with self.assertRaisesRegex(scope.ScopeError, "typed_stream_budget"):
            scope._typed_read(self.reader_path, scope.RETAINED_READERS[scope.RETAINED_COMPILE_FAMILY], budget)

    def test_descriptor_from_other_file_is_refused(self):
        other = self.root / "same-bytes-other-inode.py"
        other.write_bytes(self.reader_path.read_bytes())
        original_open = Path.open
        def redirected(path, *args, **kwargs):
            return original_open(other if path == self.reader_path and args == ("rb",) else path, *args, **kwargs)
        with mock.patch.object(Path, "open", redirected):
            self.assertEqual(self.row()["code"], "typed_opened_identity_mismatch")

    def test_reparse_attribute_is_refused_before_open(self):
        original_lstat = Path.lstat
        def attributes(path, *args, **kwargs):
            info = original_lstat(path, *args, **kwargs)
            values = {key: getattr(info, key) for key in dir(info) if key.startswith("st_")}
            if path == self.reader_path:
                values["st_file_attributes"] = 0x400
            return SimpleNamespace(**values)
        with mock.patch.object(Path, "lstat", attributes):
            self.assertEqual(self.row()["code"], "typed_link_or_reparse")

    def test_atime_changes_do_not_change_content_identity(self):
        info = self.reader_path.stat()
        values = {key: getattr(info, key) for key in dir(info) if key.startswith("st_")}
        values["st_atime_ns"] += 1000000
        self.assertEqual(scope._typed_identity(info), scope._typed_identity(SimpleNamespace(**values)))

    def test_same_size_restored_mtime_change_between_passes_refused(self):
        path, original_open = self.reader_path, Path.open
        before = path.stat()
        original = path.read_bytes()
        changed = False
        @contextlib.contextmanager
        def mutation(candidate, *args, **kwargs):
            nonlocal changed
            with original_open(candidate, *args, **kwargs) as stream:
                yield stream
            if candidate == path and args == ("rb",) and not changed:
                changed = True
                with original_open(path, "wb") as stream:
                    stream.write(original.replace(b"raise", b"print"))
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with mock.patch.object(Path, "open", mutation):
            self.assertEqual(self.row()["status"], "READBACK_REQUIRED")
        self.assertTrue(changed)

    def test_reader_replacement_after_original_reads_is_refused(self):
        original_read = scope._typed_read
        def changing(path, expected, budget, **kwargs):
            result = original_read(path, expected, budget, **kwargs)
            if Path(path).name == "original.bin":
                raw = self.reader_path.read_bytes()
                self.reader_path.write_bytes(raw.replace(b"raise", b"print"))
            return result
        with mock.patch.object(scope, "_typed_read", changing):
            self.assertEqual(self.row()["code"], "typed_original_digest_mismatch")

    def test_ambiguous_bindings_and_locators_are_refused(self):
        plan = scope.build_plan(self.product, {PATH: WORKFLOW}, catalog())
        locators = {"schema": scope.TYPED_LOCATORS_SCHEMA, "entries": [self.locator]}
        bindings = {"schema": scope.TYPED_BINDINGS_SCHEMA, "entries": [self.binding, self.binding]}
        with self.assertRaisesRegex(scope.ScopeError, "typed_binding_family_duplicate"):
            scope.attach_typed_readbacks(plan, locators, bindings)
        locators["entries"].append(self.locator)
        with self.assertRaisesRegex(scope.ScopeError, "typed_locator_family_duplicate"):
            scope.attach_typed_readbacks(plan, locators, None)

    def run_cli(self, *, typed=True, binding=True):
        # This controls CLI exit policy, not GitHub authentication or product execution.
        locator_path, binding_path = self.root / "locators.json", self.root / "reviewed-bindings.json"
        self.write(locator_path, scope.canonical({"schema": scope.TYPED_LOCATORS_SCHEMA, "entries": [self.locator]}))
        self.write(binding_path, scope.canonical({"schema": scope.TYPED_BINDINGS_SCHEMA, "entries": [self.binding]}))
        complete = scope.build_plan(SOURCE, {PATH: WORKFLOW}, catalog(),
                                    [observation("packages"), observation("lifecycle")])
        self.assertTrue(complete["scope_plan_complete"])
        self.assertTrue(complete["scope_observations_complete"])
        files = {PATH: WORKFLOW, scope.CATALOG_PATH: scope.canonical(catalog()),
                 scope.SCRIPT_PATH: Path(scope.__file__).read_bytes()}
        argv = ["--repo", str(self.root), "--source-sha", SOURCE["sha"], "--repository", SOURCE["repository"]]
        if typed:
            argv += ["--typed-evidence-locators", str(locator_path)]
            if binding:
                argv += ["--reviewed-reader-bindings", str(binding_path)]
        with mock.patch.object(scope, "read_source", return_value=(SOURCE["tree"], files)), \
             mock.patch.object(scope, "build_plan", return_value=complete), \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = scope.main(argv)
        return code, json.loads(stdout.getvalue())

    def test_cli_requested_incomplete_or_unknown_readback_cannot_exit_zero(self):
        code, report = self.run_cli(binding=False)
        self.assertEqual(code, 3)
        self.assertTrue(report["scope_observations_complete"])
        self.assertFalse(report["typed_evidence"]["retained_readbacks_complete"])
        self.execution.update(status="UNKNOWN", exit_code=None)
        self.rebind()
        code, report = self.run_cli()
        self.assertEqual(code, 3)
        self.assertEqual(report["typed_evidence"]["readbacks"][0]["execution_outcome"], "UNKNOWN")

    def test_cli_complete_historical_readback_exits_zero_without_admission(self):
        code, report = self.run_cli()
        self.assertEqual(code, 0)
        self.assertTrue(report["typed_evidence"]["retained_readbacks_complete"])
        self.assertEqual(report["typed_evidence"]["readbacks"][0]["candidate_disposition"], "READBACK_REQUIRED")
        self.assertEqual(report["evidence_admission"], "NOT_PERFORMED")
        self.assertEqual(report["release_verdict"], "NOT_GRANTED")

    def test_cli_without_locators_preserves_existing_exit_policy(self):
        with mock.patch.object(scope, "_typed_read", side_effect=AssertionError("no optional read requested")):
            code, report = self.run_cli(typed=False)
        self.assertEqual(code, 0)
        self.assertNotIn("typed_evidence", report)

    def test_regular_exe_named_and_descriptor_modes_bind_to_same_bytes(self):
        path = self.root / "synthetic-retained.exe"
        identity = self.write(path, b"MZsynthetic bytes; this file is never executed")
        budget = {"streamed_bytes": 0, "files_read": 0}
        self.assertEqual(scope._typed_read(path, identity, budget, capture=True), path.read_bytes())
        self.assertEqual(budget["streamed_bytes"], identity["bytes"] * 2)

    def test_windows_exe_exception_rejects_other_modes_types_and_identities(self):
        base = self.reader_path.stat()
        values = {key: getattr(base, key) for key in dir(base) if key.startswith("st_")}
        values["st_mode"] = 0o100666
        opened = SimpleNamespace(**values)
        named_values = {**values, "st_mode": 0o100777}
        named = SimpleNamespace(**named_values)
        path = self.root / "synthetic.exe"
        with mock.patch.object(scope, "os", SimpleNamespace(name="nt")):
            self.assertTrue(scope._typed_descriptor_matches(path, named, opened))
            self.assertFalse(scope._typed_descriptor_matches(path.with_suffix(".bin"), named, opened))
            self.assertFalse(scope._typed_descriptor_matches(path, opened, named))
            for key, value in (("st_mode", 0o100444), ("st_mode", 0o040666),
                               ("st_ino", opened.st_ino + 1), ("st_dev", opened.st_dev + 1),
                               ("st_size", opened.st_size + 1), ("st_mtime_ns", opened.st_mtime_ns + 1),
                               ("st_ctime_ns", opened.st_ctime_ns + 1),
                               ("st_file_attributes", getattr(opened, "st_file_attributes", 0) ^ 0x1)):
                with self.subTest(key=key, value=value):
                    changed = SimpleNamespace(**{**values, key: value})
                    self.assertFalse(scope._typed_descriptor_matches(path, named, changed))
        with mock.patch.object(scope, "os", SimpleNamespace(name="posix")):
            self.assertFalse(scope._typed_descriptor_matches(path, named, opened))

    def test_exe_path_mode_change_during_read_is_not_normalized(self):
        path = self.root / "synthetic-mode-change.exe"
        identity = self.write(path, b"MZsynthetic mode stability control")
        self.addCleanup(lambda: path.chmod(0o666) if path.exists() else None)
        original_open, changed = Path.open, False
        @contextlib.contextmanager
        def mutate(candidate, *args, **kwargs):
            nonlocal changed
            with original_open(candidate, *args, **kwargs) as stream:
                yield stream
            if candidate == path and args == ("rb",) and not changed:
                changed = True
                path.chmod(0o444)
        with mock.patch.object(Path, "open", mutate):
            with self.assertRaisesRegex(scope.ScopeError, "typed_original_changed"):
                scope._typed_read(path, identity, {"streamed_bytes": 0, "files_read": 0})
        self.assertTrue(changed)

    def test_cli_nonobject_report_roots_produce_structured_refusals(self):
        for value in ([{}], None, "PASS"):
            with self.subTest(root=value):
                self.report = value
                self.rebind()
                code, result = self.run_cli()
                self.assertEqual(code, 3)
                row = result["typed_evidence"]["readbacks"][0]
                self.assertEqual(row["code"], "typed_report_object_required")
                self.assertEqual(row["status"], "READBACK_REQUIRED")

    def test_cli_nonobject_execution_roots_produce_structured_refusals(self):
        for value in ([{}], None, "PASS"):
            with self.subTest(root=value):
                self.binding["execution_receipt"] = self.write(self.execution_path, scope.canonical(value))
                code, result = self.run_cli()
                self.assertEqual(code, 3)
                row = result["typed_evidence"]["readbacks"][0]
                self.assertEqual(row["code"], "typed_execution_object_required")
                self.assertEqual(row["status"], "READBACK_REQUIRED")


DIAGNOSTIC_WORKFLOW = b"""name: Evidence fixture
on:
  workflow_dispatch:
jobs:
  verify:
    runs-on: ubuntu-24.04
    steps:
      - name: Execute selected checks
        run: true
      - name: Seal original evidence
        id: archive
        if: success()
        run: true
      - name: Retain sealed originals
        if: always()
        uses: actions/upload-artifact@0123456789012345678901234567890123456789
      - name: Retain unsealed diagnostics
        if: always() && steps.archive.outcome != 'success'
        uses: actions/upload-artifact@0123456789012345678901234567890123456789
"""


def diagnostic_catalog(raw=DIAGNOSTIC_WORKFLOW):
    preamble, jobs = scope._source_outline(raw.decode())
    source_job = jobs["verify"]
    steps = [{"ordinal": step["ordinal"], "name": step["name"],
              "guard": step["guard"], "kind": scope._kind(step),
              "source_block_sha256": scope.digest(step["raw"].encode()),
              "api_binding": "EXPLICIT_NAME"}
             for step in source_job["steps"] if scope._kind(step)]
    entry = {
        "path": PATH, "sha256": scope.digest(raw),
        "preamble_sha256": scope.digest(preamble.encode()),
        "evidence_class": "source_contracts",
        "purpose": "Keep failed custody diagnostics separate from successful sealed originals.",
        "inputs": {}, "input_dispositions": {}, "job_dispositions": [],
        "jobs": {"verify": {"source_name": "verify", "guard": None,
                 "runs_on": "ubuntu-24.04", "needs": None, "strategy": None,
                 "source_sha256": scope.digest(source_job["raw"].encode()),
                 "instance_sets": {"selected": [{}]}, "steps": steps}},
        "cases": [{"id": "selected", "inputs": {}, "kind": "TECHNICAL_SCOPE",
                   "required_job_instances": [{"job_id": "verify", "display_name": "verify",
                     "matrix": {}, "name_binding": "literal_definition", "required_steps": [1, 2, 3]}],
                   "coverage_basis": "Every successful scope requires execution, sealing and sealed retention.",
                   "original_attempt": None, "blocking_reason": None}],
        "limitations": ["Synthetic source-definition fixture, never execution evidence."]}
    return {"schema": scope.REVIEWED_CATALOG_SCHEMA, "workflows": [],
            "reviewed_scopes": [entry], "limitations": ["Descriptive only."]}


class DiagnosticRetentionTests(unittest.TestCase):
    def plan(self, raw=DIAGNOSTIC_WORKFLOW, review=None):
        return scope.build_plan(SOURCE, {PATH: raw}, review or diagnostic_catalog(raw))

    def assert_source_refused(self, raw, code, review=None):
        result = self.plan(raw, review)
        self.assertFalse(result["definition_review_complete"])
        self.assertEqual([row["code"] for row in result["review_gaps"]], [code])
        self.assertEqual(result["release_verdict"], "NOT_GRANTED")
        self.assertFalse(result["automatic_dispatch"])

    def test_conditional_diagnostics_preserve_required_successful_seal(self):
        result = self.plan()
        self.assertTrue(result["definition_review_complete"])
        self.assertTrue(result["scope_plan_complete"])
        required = result["obligations"][0]["required_job_instances"][0]["required_steps"]
        self.assertEqual(required, [1, 2, 3])
        self.assertFalse(result["scope_observations_complete"])
        self.assertEqual(result["status"], "READBACK_REQUIRED")
        self.assertEqual(result["technical_criteria_verdict"], "NOT_GRANTED")

    def test_conditional_diagnostic_cannot_be_a_positive_success_obligation(self):
        review = diagnostic_catalog()
        review["reviewed_scopes"][0]["cases"][0]["required_job_instances"][0]["required_steps"].append(4)
        self.assert_source_refused(DIAGNOSTIC_WORKFLOW,
                                   "diagnostic_retention_positive_obligation", review)

    def test_missing_source_anchor_is_refused(self):
        raw = DIAGNOSTIC_WORKFLOW.replace(b"id: archive", b"id: different")
        self.assert_source_refused(raw, "diagnostic_retention_anchor")

    def test_duplicate_source_anchor_is_refused(self):
        raw = DIAGNOSTIC_WORKFLOW.replace(b"      - name: Execute selected checks\n",
            b"      - name: Execute selected checks\n        id: archive\n")
        self.assert_source_refused(raw, "diagnostic_retention_anchor")

    def test_anchor_must_be_required_in_every_case_and_matrix_member(self):
        review = diagnostic_catalog()
        entry = review["reviewed_scopes"][0]
        other = copy.deepcopy(entry["cases"][0])
        other["id"] = "other"
        other["required_job_instances"][0]["required_steps"] = [1, 3]
        entry["cases"].append(other)
        entry["jobs"]["verify"]["instance_sets"]["other"] = [{}]
        self.assert_source_refused(DIAGNOSTIC_WORKFLOW,
                                   "diagnostic_retention_anchor_not_required", review)

    def test_later_anchor_cannot_explain_prior_diagnostics(self):
        raw = DIAGNOSTIC_WORKFLOW.replace(b"        id: archive\n", b"")
        raw += b"      - name: Later seal\n        id: archive\n        if: success()\n        run: true\n"
        review = diagnostic_catalog(raw)
        review["reviewed_scopes"][0]["cases"][0]["required_job_instances"][0]["required_steps"].append(5)
        self.assert_source_refused(raw, "diagnostic_retention_anchor", review)

    def test_retention_action_cannot_impersonate_executable_seal(self):
        raw = DIAGNOSTIC_WORKFLOW.replace(
            b"        if: success()\n        run: true",
            b"        if: success()\n        uses: actions/upload-artifact@0123456789012345678901234567890123456789")
        self.assert_source_refused(raw, "diagnostic_retention_anchor")

    def test_expression_variants_cannot_create_skip_waivers(self):
        for guard in (b"always() && steps.archive.outcome == 'success'",
                      b"always() && steps.archive.outcome != 'success' || true",
                      b"steps.archive.outcome != 'success'",
                      b"always() && steps.archive.conclusion != 'success'"):
            with self.subTest(guard=guard):
                raw = DIAGNOSTIC_WORKFLOW.replace(
                    b"always() && steps.archive.outcome != 'success'", guard)
                with self.assertRaisesRegex(scope.ScopeError, "reviewed_uncovered_step"):
                    self.plan(raw)

    def test_guarded_executable_check_cannot_be_waived_as_diagnostic(self):
        raw = DIAGNOSTIC_WORKFLOW.replace(
            b"      - name: Retain unsealed diagnostics\n        if: always() && steps.archive.outcome != 'success'\n"
            b"        uses: actions/upload-artifact@0123456789012345678901234567890123456789",
            b"      - name: Retain unsealed diagnostics\n        if: always() && steps.archive.outcome != 'success'\n"
            b"        run: true")
        with self.assertRaisesRegex(scope.ScopeError, "reviewed_uncovered_step"):
            self.plan(raw)

    def test_unconditional_retention_remains_required(self):
        review = diagnostic_catalog()
        review["reviewed_scopes"][0]["cases"][0]["required_job_instances"][0]["required_steps"].remove(3)
        with self.assertRaisesRegex(scope.ScopeError, "reviewed_uncovered_step"):
            self.plan(review=review)

    def test_anchor_requires_explicit_success_guard(self):
        for guard in (b"always()", b"success() || failure()", b"inputs.optional"):
            with self.subTest(guard=guard):
                raw = DIAGNOSTIC_WORKFLOW.replace(b"        if: success()", b"        if: " + guard)
                self.assert_source_refused(raw, "diagnostic_retention_anchor")

    def test_existing_failure_only_retention_remains_descriptive(self):
        raw = DIAGNOSTIC_WORKFLOW.replace(b"always() && steps.archive.outcome != 'success'",
                                          b"failure() || cancelled()")
        result = self.plan(raw)
        self.assertTrue(result["scope_plan_complete"])
        self.assertFalse(result["scope_observations_complete"])
        self.assertEqual(result["release_verdict"], "NOT_GRANTED")





class N83ConditionalFailureTests(unittest.TestCase):
    """Exact conditional rescues cannot turn a failed/skipped path into success."""

    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.raw = (root / ".github/workflows/native-render-e2e.yml").read_bytes()
        catalog = json.loads((root / "tooling/release_scope_catalog.json").read_bytes())
        self.entry = copy.deepcopy(next(item for item in catalog["reviewed_scopes"]
                                        if item["path"] == ".github/workflows/native-render-e2e.yml"))
        self.job_id = "real-native-render"
        _, jobs = scope._source_outline(self.raw.decode("utf-8"))
        self.steps = jobs[self.job_id]["steps"]

    def _review(self, raw=None, entry=None):
        raw = self.raw if raw is None else raw
        entry = self.entry if entry is None else entry
        scope.validate_catalog({"schema": scope.REVIEWED_CATALOG_SCHEMA,
                                "workflows": [], "reviewed_scopes": [entry],
                                "limitations": ["Synthetic source controls; no execution credit."]})
        scope.review_scope_definition(raw, entry)

    def _case(self, name, entry=None):
        return next(case for case in (self.entry if entry is None else entry)["cases"]
                    if case["id"] == name)

    def _step(self, name):
        return next(step for step in self.steps if step["name"] == name or step["id"] == name)

    def _rebind(self, raw):
        # Model an adversary updating every caller-controlled definition hash.
        # Hard-coded reviewed diagnostic/anchor identities must still reject it.
        entry = copy.deepcopy(self.entry)
        preamble, jobs = scope._source_outline(raw.decode("utf-8"))
        job = jobs[self.job_id]
        entry["sha256"] = scope.digest(raw)
        entry["preamble_sha256"] = scope.digest(preamble.encode("utf-8"))
        contract = entry["jobs"][self.job_id]
        contract["source_sha256"] = scope.digest(job["raw"].encode("utf-8"))
        contract["steps"] = [
            {"ordinal": step["ordinal"], "name": step["name"], "guard": step["guard"],
             "kind": scope._kind(step), "source_block_sha256": scope.digest(step["raw"].encode("utf-8")),
             "api_binding": "EXPLICIT_NAME" if step["name"] else "REQUIRES_ORIGINAL_RUN_MAPPING"}
            for step in job["steps"] if scope._kind(step)]
        return entry

    def _changed_step(self, step, replacement):
        before = step["raw"].encode("utf-8")
        self.assertEqual(self.raw.count(before), 1)
        after = replacement.encode("utf-8")
        self.assertNotEqual(before, after)
        return self.raw.replace(before, after, 1)

    def test_standard_and_n83_keep_distinct_positive_selections(self):
        self._review()
        standard = self._case("independent-repeat")
        n83 = self._case("n83-preview")
        self.assertEqual(standard["inputs"]["native_scope"], "standard")
        self.assertIs(standard["inputs"]["v01_provider_repeat"], True)
        self.assertEqual(n83["inputs"]["native_scope"], "n83-preview")
        self.assertIs(n83["inputs"]["v01_provider_repeat"], False)
        anchors = {self._step(name)["ordinal"] for name in
                   ("n83_source_controls", "n83_rust_controls")}
        self.assertTrue(anchors <= set(n83["required_job_instances"][0]["required_steps"]))
        self.assertFalse(anchors & set(standard["required_job_instances"][0]["required_steps"]))

    def test_reviewed_n83_definition_does_not_admit_execution(self):
        catalog = {"schema": scope.REVIEWED_CATALOG_SCHEMA, "workflows": [],
                   "reviewed_scopes": [self.entry], "limitations": ["No runtime evidence."]}
        result = scope.build_plan(SOURCE, {self.entry["path"]: self.raw}, catalog)
        serialized = json.dumps(result, sort_keys=True)
        self.assertIn("READBACK_REQUIRED", serialized)
        self.assertIn("scope_observation_binding_not_implemented", serialized)
        self.assertNotIn('"evidence_admission": "ADMITTED"', serialized)

    def test_changed_rescue_source_cannot_be_waived_by_rebinding_hashes(self):
        step = self._step("Seal failed N83 source controls without any owner or Native attempt")
        raw = self._changed_step(step, step["raw"] + "\n        # unreviewed rescue edit")
        with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_step_source"):
            self._review(raw, self._rebind(raw))

    def test_exact_rescue_cannot_be_reused_in_another_workflow(self):
        entry = copy.deepcopy(self.entry)
        entry["path"] = ".github/workflows/unreviewed-native-render.yml"
        with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_workflow"):
            self._review(entry=entry)

    def test_missing_selected_n83_case_is_refused(self):
        entry = copy.deepcopy(self.entry)
        entry["cases"] = [self._case("independent-repeat", entry)]
        with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_case"):
            scope.review_scope_definition(self.raw, entry)

    def test_selected_n83_case_inputs_and_attempt_cannot_be_substituted(self):
        for key, value in [("v01_provider_repeat", True), ("expected_source_sha", ""),
                           ("recovery_recipient", "")]:
            with self.subTest(input=key):
                entry = copy.deepcopy(self.entry)
                self._case("n83-preview", entry)["inputs"][key] = value
                with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_case_inputs"):
                    self._review(entry=entry)
        entry = copy.deepcopy(self.entry)
        self._case("n83-preview", entry)["original_attempt"] = None
        with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_case_inputs"):
            self._review(entry=entry)

    def test_python_positive_anchor_cannot_be_omitted(self):
        entry = copy.deepcopy(self.entry)
        ordinal = self._step("n83_source_controls")["ordinal"]
        self._case("n83-preview", entry)["required_job_instances"][0]["required_steps"].remove(ordinal)
        with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_success_steps"):
            scope.review_scope_definition(self.raw, entry)

    def test_rust_positive_anchor_cannot_be_omitted(self):
        entry = copy.deepcopy(self.entry)
        ordinal = self._step("n83_rust_controls")["ordinal"]
        self._case("n83-preview", entry)["required_job_instances"][0]["required_steps"].remove(ordinal)
        with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_success_steps"):
            scope.review_scope_definition(self.raw, entry)

    def test_rescue_is_never_a_positive_obligation_in_either_case(self):
        names = ["Seal failed N83 source controls without any owner or Native attempt",
                 "Seal failed N83 Rust3 source controls without any owner or Native attempt"]
        for name in names:
            for case_id in ("independent-repeat", "n83-preview"):
                with self.subTest(rescue=name, case_id=case_id):
                    entry = copy.deepcopy(self.entry)
                    self._case(case_id, entry)["required_job_instances"][0]["required_steps"].append(
                        self._step(name)["ordinal"])
                    with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_positive_obligation"):
                        self._review(entry=entry)

    def test_positive_anchor_source_remains_exact_even_after_hash_rebinding(self):
        step = self._step("n83_source_controls")
        raw = self._changed_step(step, step["raw"] + "\n        # unreviewed anchor edit")
        with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_step_source"):
            self._review(raw, self._rebind(raw))

    def test_missing_or_renamed_positive_anchor_is_refused(self):
        step = self._step("n83_rust_controls")
        raw = self._changed_step(step, step["raw"].replace("id: n83_rust_controls",
                                                        "id: unreviewed_rust_controls"))
        with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_step_source"):
            self._review(raw, self._rebind(raw))

    def test_positive_anchor_guard_cannot_be_relaxed(self):
        step = self._step("n83_source_controls")
        raw = self._changed_step(step, step["raw"].replace(
            "if: ${{ inputs.native_scope == 'n83-preview' }}", "if: ${{ always() }}"))
        with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_step_source"):
            self._review(raw, self._rebind(raw))

    def test_unknown_conditional_rescue_guard_is_not_a_skip_waiver(self):
        step = self._step("Seal failed N83 source controls without any owner or Native attempt")
        raw = self._changed_step(step, step["raw"].replace("outcome == 'failure'",
                                                        "outcome == 'cancelled'"))
        with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_step_source"):
            self._review(raw, self._rebind(raw))



class N83FailureBranchTests(unittest.TestCase):
    """Two observed failure branches never satisfy the successful N83 fixture."""

    setUp = N83ConditionalFailureTests.setUp
    _review = N83ConditionalFailureTests._review
    _case = N83ConditionalFailureTests._case
    _step = N83ConditionalFailureTests._step
    _rebind = N83ConditionalFailureTests._rebind
    _changed_step = N83ConditionalFailureTests._changed_step

    def test_a1_preflight_failure_id_is_retained_for_early_python(self):
        self._review()
        case = self._case("n83-preflight-failure")
        self.assertEqual(case["kind"], "CONDITIONAL_ALTERNATIVE")
        self.assertEqual(case["required_job_instances"][0]["required_steps"], [2, 3, 5, 33])
        self.assertNotIn(4, case["required_job_instances"][0]["required_steps"])
        self.assertNotIn(27, case["required_job_instances"][0]["required_steps"])

    def test_rust_failure_requires_python_success_without_failed_rust_or_native(self):
        self._review()
        case = self._case("n83-rust-controls-failure")
        required = case["required_job_instances"][0]["required_steps"]
        self.assertEqual(required, [2, 3, 4, 6, 8, 9, 10, 14, 15, 16, 17, 18, 21, 33])
        self.assertIn(4, required)
        self.assertNotIn(20, required)
        self.assertNotIn(27, required)
        self.assertNotIn(32, required)

    def test_three_n83_branches_share_one_attempt_without_dispatch_authority(self):
        success = self._case("n83-preview")
        for name in ("n83-preflight-failure", "n83-rust-controls-failure"):
            case = self._case(name)
            self.assertEqual(case["inputs"], success["inputs"])
            self.assertEqual(case["original_attempt"], 1)
            self.assertEqual(case["kind"], "CONDITIONAL_ALTERNATIVE")
        catalog = {"schema": scope.REVIEWED_CATALOG_SCHEMA, "workflows": [],
                   "reviewed_scopes": [self.entry], "limitations": ["Descriptive only."]}
        result = scope.build_plan(SOURCE, {self.entry["path"]: self.raw}, catalog)
        self.assertFalse(result["automatic_dispatch"])
        self.assertFalse(result["retry_authorized"])
        self.assertEqual(result["release_verdict"], "NOT_GRANTED")
        self.assertTrue(all(row["observation"]["evidence_admission"] == "NOT_PERFORMED"
                            for row in result["obligations"]))

    def test_both_exact_failure_case_ids_are_required(self):
        for missing in ("n83-preflight-failure", "n83-rust-controls-failure"):
            with self.subTest(missing=missing):
                entry = copy.deepcopy(self.entry)
                entry["cases"] = [case for case in entry["cases"] if case["id"] != missing]
                with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_case"):
                    scope.review_scope_definition(self.raw, entry)

    def test_failure_case_kind_inputs_and_attempt_cannot_be_substituted(self):
        for name in ("n83-preflight-failure", "n83-rust-controls-failure"):
            mutations = [("kind", "TECHNICAL_SCOPE"), ("original_attempt", None),
                         ("expected_source_sha", ""), ("recovery_recipient", ""),
                         ("v01_provider_repeat", True)]
            for field, value in mutations:
                with self.subTest(case=name, field=field):
                    entry = copy.deepcopy(self.entry)
                    case = self._case(name, entry)
                    if field in ("kind", "original_attempt"):
                        case[field] = value
                    else:
                        case["inputs"][field] = value
                    with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_case_inputs"):
                        scope.review_scope_definition(self.raw, entry)

    def test_failure_cannot_require_its_failed_anchor_or_native_success(self):
        for name, failed in (("n83-preflight-failure", 4), ("n83-rust-controls-failure", 20)):
            for forbidden in (failed, 27, 32):
                with self.subTest(case=name, step=forbidden):
                    entry = copy.deepcopy(self.entry)
                    self._case(name, entry)["required_job_instances"][0]["required_steps"].append(forbidden)
                    with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_case_steps"):
                        self._review(entry=entry)

    def test_failure_cannot_omit_successful_setup_or_sealed_upload(self):
        for name, selected in (("n83-preflight-failure", (2, 3, 33)),
                               ("n83-rust-controls-failure", (2, 3, 4, 18, 33))):
            for missing in selected:
                with self.subTest(case=name, step=missing):
                    entry = copy.deepcopy(self.entry)
                    self._case(name, entry)["required_job_instances"][0]["required_steps"].remove(missing)
                    with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_case_steps"):
                        self._review(entry=entry)

    def test_failure_branch_cannot_claim_the_other_rescue(self):
        for name, old, wrong in (("n83-preflight-failure", 5, 21),
                                  ("n83-rust-controls-failure", 21, 5)):
            with self.subTest(case=name):
                entry = copy.deepcopy(self.entry)
                required = self._case(name, entry)["required_job_instances"][0]["required_steps"]
                required[required.index(old)] = wrong
                with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_positive_obligation"):
                    self._review(entry=entry)

    def test_both_covered_rescue_sources_stay_exact_after_hash_rebinding(self):
        for name in ("Seal failed N83 source controls without any owner or Native attempt",
                     "Seal failed N83 Rust3 source controls without any owner or Native attempt"):
            with self.subTest(rescue=name):
                step = self._step(name)
                raw = self._changed_step(step, step["raw"] + "\n        # altered covered rescue")
                entry = self._rebind(raw)
                self.assertTrue(any(step["ordinal"] in instance["required_steps"]
                                    for case in entry["cases"]
                                    for instance in case["required_job_instances"]))
                with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_step_source"):
                    self._review(raw, entry)

    def test_both_covered_rescue_guards_stay_failure_only_after_rebinding(self):
        for name in ("Seal failed N83 source controls without any owner or Native attempt",
                     "Seal failed N83 Rust3 source controls without any owner or Native attempt"):
            with self.subTest(rescue=name):
                step = self._step(name)
                raw = self._changed_step(step, step["raw"].replace(
                    "outcome == 'failure'", "outcome == 'success'"))
                with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_step_source"):
                    self._review(raw, self._rebind(raw))

    def test_diagnostic_prerequisites_and_publisher_stay_exact_after_rebinding(self):
        for name in ("Admit closed native selection before bootstrap",
                     "Install pinned N83 custody and PNG verifier in a private runner venv",
                     "Build exact Semwright Broker, sandbox and Motion Canvas provider",
                     "Publish only sealed N83 originals and closed manifest"):
            with self.subTest(step=name):
                step = self._step(name)
                raw = self._changed_step(step, step["raw"] + "\n        # altered prerequisite")
                with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_step_source"):
                    self._review(raw, self._rebind(raw))

    def test_failure_branch_requires_one_exact_unexpanded_job_instance(self):
        for name in ("n83-preflight-failure", "n83-rust-controls-failure"):
            for mutation in ("matrix", "display", "extra"):
                with self.subTest(case=name, mutation=mutation):
                    entry = copy.deepcopy(self.entry)
                    instances = self._case(name, entry)["required_job_instances"]
                    if mutation == "matrix":
                        instances[0]["matrix"] = {"invented": True}
                    elif mutation == "display":
                        instances[0]["display_name"] = "unreviewed"
                    else:
                        instances.append(copy.deepcopy(instances[0]))
                    with self.assertRaisesRegex(scope.ScopeError, "conditional_diagnostic_case_steps"):
                        scope.review_scope_definition(self.raw, entry)


if __name__ == "__main__":
    unittest.main()
