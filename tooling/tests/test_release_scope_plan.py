"""Synthetic metadata and temporary Git controls; no installer or release claims."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
