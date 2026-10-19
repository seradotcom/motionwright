"""Negative controls for Studio CI evidence, using only synthetic test processes.

No Cargo, Node, browser, Studio service or native renderer is executed here.
Disposable Git repositories and Python children test source/receipt boundaries.
"""

from __future__ import annotations

from collections import Counter
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOLING = Path(__file__).resolve().parents[1]
HELPER = TOOLING / "studio_service_ci.py"
CONTRACT = TOOLING / "studio_service_contracts.json"
spec = importlib.util.spec_from_file_location("_studio_service_ci_under_test", HELPER)
ci = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ci
spec.loader.exec_module(ci)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))


def report_fixture(contract, *, listing=False):
    """Reporter-shaped fixture; it is never evidence of a browser execution."""
    suites = {}
    for index, case in enumerate(contract["cases"]):
        suite = suites.setdefault(case["file"], {
            "title": case["file"], "file": case["file"], "specs": [], "suites": [],
        })
        suite["specs"].append({
            "file": case["file"], "line": case["line"], "column": 1,
            "title": case["title"], "id": f"synthetic-test-{index}", "ok": True,
            "tests": [{
                "projectName": "chromium", "expectedStatus": "passed",
                "status": "expected", "annotations": [],
                "results": [] if listing else [{
                    "status": "passed", "retry": 0, "duration": 1,
                    "errors": [], "stdout": [], "stderr": [], "attachments": [],
                }],
            }],
        })
    return {
        "config": {"workers": 2, "projects": [{"name": "chromium", "retries": 0}]},
        "suites": list(suites.values()), "errors": [],
        "stats": {"expected": 0 if listing else len(contract["cases"]),
                  "unexpected": 0, "skipped": 0, "flaky": 0},
    }


def first_test(report):
    return report["suites"][0]["specs"][0]["tests"][0]


class BrowserEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract, _ = ci.load_contract(CONTRACT)

    def test_selection_contains_only_the_reviewed_89_real_service_cases(self):
        expected = {
            "audio-draft-preservation.spec.ts": 9,
            "brief-preservation.spec.ts": 2,
            "brief-service.spec.ts": 7,
            "canvas-draft-service.spec.ts": 4,
            "caption-continuity-service.spec.ts": 2,
            "delivery-profile-preservation.spec.ts": 14,
            "delivery-recipe.spec.ts": 12,
            "file-import-service.spec.ts": 10,
            "project-lifecycle-service.spec.ts": 11,
            "project-mutation.spec.ts": 4,
            "scoped-change-capture.spec.ts": 12,
            "scoped-change-service.spec.ts": 2,
        }
        self.assertEqual(self.contract["expected_cases"], 89)
        self.assertEqual(Counter(row["file"] for row in self.contract["cases"]), expected)
        self.assertEqual(len(self.contract["source_files"]), 21)
        self.assertEqual(self.contract["source_files"]["Cargo.lock"],
                         "583f9276782966778e544ca1facd9d7a9af5a7c216e2dee4da598c00da118a84")
        selectors = self.contract["selectors"]
        self.assertEqual(set(selectors), {
            *("tests/" + name for name in expected if name != "project-mutation.spec.ts"),
            "tests/project-mutation.spec.ts:813",
        })
        mutation = [row for row in self.contract["cases"] if row["file"] == "project-mutation.spec.ts"]
        self.assertTrue(all(row["title"].startswith("real ") for row in mutation))
        self.assertEqual(Counter(row["line"] for row in mutation), {969: 2, 1101: 1, 1166: 1})
        self.assertEqual(self.contract["scope"], "REAL_STUDIO_SERVICE_JSONL_SQLITE")
        self.assertFalse(self.contract["scope_limits"]["mock_transport_fallback_allowed"])
        for row in self.contract["cases"]:
            self.assertIn("apps/studio/tests/" + row["file"], self.contract["source_files"])

    def test_complete_original_attempt_and_listing_share_the_closed_selection(self):
        completed = ci.validate_report(report_fixture(self.contract), self.contract)
        listed = ci.validate_report(report_fixture(self.contract, listing=True), self.contract, listing=True)
        self.assertEqual((completed["case_count"], completed["status"]), (89, "PASS"))
        self.assertEqual((listed["case_count"], listed["status"]), (89, "LISTED"))

    def test_missing_duplicate_and_unrelated_fixture_cases_are_rejected(self):
        missing = report_fixture(self.contract)
        missing["suites"][0]["specs"].pop()
        duplicate = report_fixture(self.contract)
        duplicate["suites"][0]["specs"].append(copy.deepcopy(duplicate["suites"][0]["specs"][0]))
        unrelated = report_fixture(self.contract)
        unrelated["suites"][0]["specs"][0]["title"] = "browser mock with no real service"
        for name, report in [("missing", missing), ("duplicate", duplicate), ("unrelated", unrelated)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                ci.validate_report(report, self.contract)

    def test_skipped_flaky_retried_xfail_timeout_and_absent_results_never_pass(self):
        variants = {}
        row = copy.deepcopy(first_test(report_fixture(self.contract)))
        variants["skip"] = {**row, "expectedStatus": "skipped", "status": "skipped",
                            "results": [{"status": "skipped", "retry": 0}]}
        variants["xfail"] = {**row, "expectedStatus": "failed", "results": [{"status": "failed", "retry": 0}]}
        variants["flaky"] = {**row, "status": "flaky", "results": [
            {"status": "failed", "retry": 0}, {"status": "passed", "retry": 1}]}
        variants["hidden_retry"] = {**row, "results": [{"status": "passed", "retry": 1}]}
        variants["timeout"] = {**row, "status": "unexpected", "results": [{"status": "timedOut", "retry": 0}]}
        variants["absent"] = {**row, "results": []}
        for annotation in ("skip", "fixme", "fail"):
            variants[annotation + "_annotation"] = {**row, "annotations": [{"type": annotation}]}
        for name, changed in variants.items():
            report = report_fixture(self.contract)
            report["suites"][0]["specs"][0]["tests"][0] = changed
            with self.subTest(name=name), self.assertRaises(ValueError):
                ci.validate_report(report, self.contract)

    def test_global_and_passed_result_errors_override_green_aggregate(self):
        global_error = report_fixture(self.contract)
        global_error["errors"] = [{"message": "collection failed"}]
        error_array = report_fixture(self.contract)
        first_test(error_array)["results"][0]["errors"] = [{"message": "transport lost"}]
        error_single = report_fixture(self.contract)
        first_test(error_single)["results"][0]["error"] = {"message": "closed process unavailable"}
        for name, report in [("global", global_error), ("array", error_array), ("single", error_single)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                ci.validate_report(report, self.contract)

    def test_wrong_project_source_path_and_contradictory_counts_are_rejected(self):
        project = report_fixture(self.contract)
        first_test(project)["projectName"] = "firefox"
        counts = report_fixture(self.contract)
        counts["stats"]["expected"] -= 1
        skipped = report_fixture(self.contract)
        skipped["stats"]["skipped"] = 1
        paths = []
        for prefix in ("../other/", "/other/", "other/"):
            report = report_fixture(self.contract)
            spec_row = report["suites"][0]["specs"][0]
            spec_row["file"] = prefix + spec_row["file"]
            paths.append((prefix, report))
        for name, report in [("project", project), ("counts", counts), ("skipped", skipped), *paths]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                ci.validate_report(report, self.contract)

    def test_listing_cannot_smuggle_execution_or_expected_failure(self):
        execution = report_fixture(self.contract, listing=True)
        first_test(execution)["results"] = [{"status": "passed", "retry": 0}]
        xfail = report_fixture(self.contract, listing=True)
        first_test(xfail)["expectedStatus"] = "failed"
        for name, report in [("executed", execution), ("xfail", xfail)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                ci.validate_report(report, self.contract, listing=True)


class IsolatedGitFixture(unittest.TestCase):
    """A small private repo; its source files are data, never compiled or served."""

    def setUp(self):
        fixture_parent = os.environ.get("MOTIONWRIGHT_STUDIO_CI_TEST_ROOT")
        if fixture_parent:
            Path(fixture_parent).mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="motionwright-studio-ci-", dir=fixture_parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.contract = json.loads(CONTRACT.read_bytes())
        for name in self.contract["source_files"]:
            raw = ("Synthetic source-binding fixture only: " + name + "\n").encode()
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.contract["source_files"][name] = hashlib.sha256(raw).hexdigest()
        (self.repo / ".gitignore").write_bytes(b"apps/studio/node_modules/\n")
        self.git("init", "-q")
        self.git("-c", "core.autocrlf=false", "add", "--all")
        self.commit("initial synthetic source")
        self.source_sha = self.git("rev-parse", "HEAD").decode().strip()
        self.contract["reference_source_sha"] = self.source_sha
        self.contract["reference_source_tree"] = self.git("rev-parse", "HEAD^{tree}").decode().strip()
        self.contract_path = self.evidence / "selection.json"
        write_json(self.contract_path, self.contract)
        self.environment = patch.dict(os.environ, {
            "GITHUB_SHA": self.source_sha,
            "GITHUB_RUN_ID": "4242", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "synthetic_studio_ci_tests",
            "GITHUB_WORKFLOW_SHA": "b" * 40,
            "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/.github/workflows/studio-service-verification.yml@refs/heads/fixture",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def git(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_NO_REPLACE_OBJECTS="1")
        result = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.hooksPath=" + os.devnull,
                                 "-C", str(self.repo), *args], env=env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return result.stdout

    def commit(self, message):
        self.git("-c", "user.name=Motionwright CI fixture", "-c", "user.email=ci-fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", "-B", str(HELPER), *map(str, args)],
                              cwd=self.root, env=dict(os.environ), capture_output=True, timeout=30)

    def freeze(self):
        initial = self.evidence / "initial.json"
        result = self.cli("freeze", "--repo", self.repo, "--source-sha", self.source_sha,
                          "--contract", self.contract_path, "--output", initial)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return initial


class SourceIdentityTests(IsolatedGitFixture):
    def test_original_raw_git_source_and_tree_are_bound(self):
        value = ci.source_snapshot(self.repo, self.source_sha, self.contract)
        self.assertEqual(value["sha"], self.source_sha)
        self.assertEqual(value["tree"], self.contract["reference_source_tree"])
        self.assertEqual(len(value["source_inputs"]), 21)

    def test_lf_checkout_matches_isolated_guard_without_accepting_changed_bytes(self):
        # This unselected Cargo file models the source-wide status guard. Git
        # performs the checkout conversion; no product source or process runs.
        name = "tooling/v01-glib-backport-probe/Cargo.toml"
        target = self.repo / name
        original = b'[package]\nname = "synthetic-checkout-fixture"\nversion = "0.0.0"\n'
        target.parent.mkdir(parents=True)
        target.write_bytes(original)
        self.git("add", name)
        self.commit("add synthetic checkout-conversion input")
        actual = self.git("rev-parse", "HEAD").decode().strip()
        self.assertEqual(self.git("show", actual + ":" + name), original)
        global_config = self.evidence / "synthetic-global.gitconfig"
        global_config.write_bytes(b"[core]\n\tautocrlf = true\n")
        checkout_env = {key: value for key, value in os.environ.items()
                        if not key.upper().startswith("GIT_")}
        checkout_env.update(GIT_CONFIG_GLOBAL=str(global_config), GIT_CONFIG_NOSYSTEM="1",
                            GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
                            GIT_OPTIONAL_LOCKS="0")

        def checkout(env):
            # Materialize a missing file, as in a fresh Actions checkout. An
            # already stat-clean file can otherwise be left untouched by Git.
            target.unlink()
            result = subprocess.run(
                ["git", "--no-replace-objects", "-c", "core.hooksPath=" + os.devnull,
                 "-C", str(self.repo), "checkout-index", "--force", "--", name],
                env=env, capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

        checkout(checkout_env)
        self.assertEqual(target.read_bytes(), original.replace(b"\n", b"\r\n"))
        # The guard intentionally drops the global profile. The resulting dirty
        # source must fail, even though a CRLF-capable checkout wrote the bytes.
        with patch.dict(os.environ, checkout_env, clear=True):
            self.assertEqual(ci.clean_env()["GIT_CONFIG_GLOBAL"], os.devnull)
            with self.assertRaisesRegex(ValueError, "changed, extra or ignored input"):
                ci.source_snapshot(self.repo, actual, self.contract)

        # These are the job-level settings, present before either checkout.
        # They override the synthetic global policy only for materialization;
        # the source guard must still discard inherited Git configuration.
        checkout_env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.autocrlf",
                            GIT_CONFIG_VALUE_0="false")
        checkout(checkout_env)
        self.assertEqual(target.read_bytes(), original)
        with patch.dict(os.environ, checkout_env, clear=True):
            self.assertNotIn("GIT_CONFIG_COUNT", ci.clean_env())
            observed = ci.source_snapshot(self.repo, actual, self.contract)
            self.assertEqual(observed["sha"], actual)
            self.assertEqual(observed["permitted_generated_paths"], [])
            self.assertTrue(all(row["representation"] == "EXACT_GIT_BYTES"
                                for row in observed["source_inputs"]))
            target.write_bytes(original + b"# actual unreviewed source mutation\n")
            with self.assertRaisesRegex(ValueError, "changed, extra or ignored input"):
                ci.source_snapshot(self.repo, actual, self.contract)

    def test_prepared_state_requires_the_original_tracked_dependency_lock(self):
        observed = ci.source_snapshot(self.repo, self.source_sha, self.contract, prepared=True)
        self.assertIn("Cargo.lock", {row["path"] for row in observed["source_inputs"]})
        lock = self.repo / "Cargo.lock"
        original = lock.read_bytes()
        lock.write_bytes(original + b"a different dependency resolution\n")
        with self.assertRaises(ValueError):
            ci.source_snapshot(self.repo, self.source_sha, self.contract, prepared=True)
        lock.write_bytes(original)
        # A new ignored lock also cannot enter via the former generated-file exception.
        self.git("rm", "--cached", "Cargo.lock")
        with (self.repo / ".gitignore").open("ab") as stream:
            stream.write(b"Cargo.lock\n")
        self.git("add", ".gitignore")
        self.commit("synthetic old selection with an untracked ignored lock")
        changed_sha = self.git("rev-parse", "HEAD").decode().strip()
        old_selection = copy.deepcopy(self.contract)
        del old_selection["source_files"]["Cargo.lock"]
        with self.assertRaises(ValueError):
            ci.source_snapshot(self.repo, changed_sha, old_selection, prepared=True)

    def test_previous_sha_is_rejected_even_when_selected_files_are_identical(self):
        (self.repo / "unrelated-note.txt").write_bytes(b"new commit, same selected fixture files\n")
        self.git("add", "unrelated-note.txt")
        self.commit("second synthetic commit")
        actual = self.git("rev-parse", "HEAD").decode().strip()
        self.assertNotEqual(actual, self.source_sha)
        with self.assertRaises(ValueError):
            ci.source_snapshot(self.repo, self.source_sha, self.contract)

    def test_changed_source_and_ignored_injected_input_are_rejected(self):
        name = "apps/studio/tests/brief-service.spec.ts"
        original = (self.repo / name).read_bytes()
        (self.repo / name).write_bytes(original + b"changed\n")
        with self.assertRaises(ValueError):
            ci.source_snapshot(self.repo, self.source_sha, self.contract)
        (self.repo / name).write_bytes(original)
        injected = self.repo / "apps/studio/node_modules/injected.js"
        injected.parent.mkdir(parents=True)
        injected.write_bytes(b"unreviewed ignored input\n")
        with self.assertRaises(ValueError):
            ci.source_snapshot(self.repo, self.source_sha, self.contract)

    def test_changed_contract_hash_and_symbolic_source_are_rejected(self):
        wrong = copy.deepcopy(self.contract)
        wrong["source_files"]["apps/studio/tests/brief-service.spec.ts"] = "0" * 64
        with self.assertRaises(ValueError):
            ci.source_snapshot(self.repo, self.source_sha, wrong)
        with self.assertRaises(ValueError):
            ci.source_snapshot(self.repo, "HEAD", self.contract)


class OriginalStageTests(IsolatedGitFixture):
    def setUp(self):
        super().setUp()
        self.initial = self.freeze()

    def run_fixture(self, name, program="print('original output')", *, seconds=5, purpose="dependencies", report=None):
        stage = self.evidence / (name + ".json")
        args = ["run", "--initial", self.initial, "--purpose", purpose,
                "--cwd", self.repo, "--record", stage, "--seconds", str(seconds)]
        if report is not None:
            args += ["--report", report]
        args += ["--", sys.executable, "-I", "-B", "-c", program]
        return stage, self.cli(*args)

    def validate(self, stage, *, initial=None, purpose="dependencies", cwd=None, report=None):
        return ci.validate_stage(stage, initial=initial or self.initial, purpose=purpose,
                                 cwd=cwd or self.repo, expected_report=report)

    def test_original_python_process_receipt_matches_its_frozen_context(self):
        stage, result = self.run_fixture("positive")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        receipt = json.loads(stage.read_bytes())
        self.assertEqual(receipt["source_freeze"], ci.file_record(self.initial))
        self.assertFalse(receipt["automatic_retry"])
        self.validate(stage)

    def test_nonzero_process_exit_is_preserved_and_never_admitted(self):
        stage, result = self.run_fixture("exit7", "raise SystemExit(7)")
        self.assertEqual(result.returncode, 7, result.stderr.decode("utf-8", errors="replace"))
        receipt = json.loads(stage.read_bytes())
        self.assertEqual((receipt["process_status"], receipt["exit_code"]), ("FAIL", 7))
        with self.assertRaises(ValueError):
            self.validate(stage)

    def test_unknown_timeout_is_retained_and_same_operation_is_not_replayed(self):
        marker = self.evidence / "effects.txt"
        program = "from pathlib import Path; import time; Path(" + repr(str(marker)) + ").open('a').write('once\\n'); time.sleep(10)"
        stage, result = self.run_fixture("unknown", program, seconds=1)
        self.assertEqual(result.returncode, 124, result.stderr.decode("utf-8", errors="replace"))
        receipt = json.loads(stage.read_bytes())
        self.assertEqual(receipt["process_status"], "UNKNOWN")
        self.assertIsNone(receipt["exit_code"])
        self.assertEqual(marker.read_text(), "once\n")
        with self.assertRaises(ValueError):
            self.validate(stage)
        before = stage.read_bytes()
        _, replay = self.run_fixture("unknown", program, seconds=1)
        self.assertEqual(replay.returncode, 2)
        self.assertEqual(stage.read_bytes(), before)
        self.assertEqual(marker.read_text(), "once\n")

    def test_false_exit_wrong_schema_and_unknown_cannot_use_green_streams(self):
        stage, result = self.run_fixture("metadata")
        self.assertEqual(result.returncode, 0)
        original = json.loads(stage.read_bytes())
        variants = {
            "boolean-exit": {**original, "exit_code": False},
            "wrong-schema": {**original, "schema": "unrelated.process/1"},
            "unknown-green": {**original, "process_status": "UNKNOWN", "exit_code": 0},
        }
        for name, value in variants.items():
            path = self.evidence / (name + ".json")
            write_json(path, value)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.validate(path)

    def test_changed_logs_or_substituted_browser_report_are_rejected(self):
        stage, result = self.run_fixture("streams")
        self.assertEqual(result.returncode, 0)
        original = json.loads(stage.read_bytes())
        Path(original["stdout"]["path"]).write_bytes(b"replacement stdout\n")
        with self.assertRaises(ValueError):
            self.validate(stage)
        report = self.evidence / "synthetic-report.json"
        program = "import json,os; from pathlib import Path; Path(os.environ['PLAYWRIGHT_JSON_OUTPUT_NAME']).write_text(json.dumps({'synthetic': True}))"
        report_stage, result = self.run_fixture("report-stream", program, report=report)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.validate(report_stage, report=report)
        report.write_bytes(b'{"substitution":true}\n')
        with self.assertRaises(ValueError):
            self.validate(report_stage, report=report)

    def test_old_freeze_other_job_purpose_and_cwd_are_not_reusable(self):
        stage, result = self.run_fixture("bound")
        self.assertEqual(result.returncode, 0)
        copied = self.evidence / "other-freeze.json"
        copied.write_bytes(self.initial.read_bytes())
        with self.assertRaises(ValueError):
            self.validate(stage, initial=copied)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "4243"}):
            with self.assertRaises(ValueError):
                self.validate(stage)
        with self.assertRaises(ValueError):
            self.validate(stage, purpose="build")
        with self.assertRaises(ValueError):
            self.validate(stage, cwd=self.root)

    def test_successful_unrelated_command_cannot_attest_a_runner_build(self):
        stage, result = self.run_fixture("unrelated-build", "print('no cargo build happened')", purpose="build")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        binary = self.evidence / "unused-runner.bin"
        binary.write_bytes((b"MZ" if os.name == "nt" else b"\x7fELF") + b"data only; never executed")
        lock = self.repo / "Cargo.lock"
        output = self.evidence / "runner.json"
        rejected = self.cli("runner", "--initial", self.initial, "--binary", binary,
                            "--build-stage", stage, "--lock", lock, "--output", output)
        self.assertEqual(rejected.returncode, 2, rejected.stdout.decode("utf-8", errors="replace"))
        self.assertFalse(output.exists())
        error = json.loads(rejected.stderr)
        self.assertEqual(error["status"], "NOT_ADMITTED")
        self.assertIn("build", error["error"].lower())


class ValidatorChainTests(IsolatedGitFixture):
    """Declarative unit fixtures only: none of these tool or runner bytes execute."""

    def setUp(self):
        super().setUp()
        self.initial = self.freeze()
        self.target = self.root / "synthetic-target"
        self.binary = self.target / "debug/examples" / (
            "studio_review_session.exe" if os.name == "nt" else "studio_review_session")
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b"Synthetic runner data; never execute.\n")
        self.lock = self.repo / "Cargo.lock"
        self.lock.write_bytes(b"# Declarative unit fixture; not a resolved product lock.\n")
        self.cargo = self.evidence / ("cargo.exe" if os.name == "nt" else "cargo")
        self.node = self.evidence / ("node.exe" if os.name == "nt" else "node")
        self.cargo.write_bytes(b"Synthetic Cargo identity data; never execute.\n")
        self.node.write_bytes(b"Synthetic Node identity data; never execute.\n")
        self.playwright = self.repo / "apps/studio/node_modules/@playwright/test/cli.js"
        self.playwright.parent.mkdir(parents=True)
        self.playwright.write_bytes(b"Synthetic Playwright identity data; never execute.\n")
        self.stdout = self.evidence / "declarative.stdout.log"
        self.stderr = self.evidence / "declarative.stderr.log"
        self.stdout.write_bytes(b"Declarative validator fixture, not an execution log.\n")
        self.stderr.write_bytes(b"")
        self.output_dir = self.evidence / "synthetic-test-output"
        self.service_tmp = self.evidence / "synthetic-service-tmp"
        self.report = self.evidence / "declarative-browser-report.json"
        write_json(self.report, report_fixture(self.contract))
        common = {
            "schema": "motionwright.studio-service-process/1",
            "source_freeze": ci.file_record(self.initial),
            "execution_context": ci.execution_context(),
            "process_status": "PASS", "exit_code": 0, "reason": None,
            "automatic_retry": False,
            "stdout": ci.file_record(self.stdout), "stderr": ci.file_record(self.stderr),
        }
        self.build_stage = self.evidence / "declarative-build.json"
        write_json(self.build_stage, {
            **common, "purpose": "build", "cwd": str(self.repo),
            "argv": [str(self.cargo), "build", "--locked", "-p", "motionwright-service",
                     "--example", "studio_review_session"],
            "executable": ci.file_record(self.cargo),
            "environment": {"CARGO_TARGET_DIR": str(self.target)},
            "dependency_lock_before": ci.file_record(self.lock),
            "dependency_lock_after": ci.file_record(self.lock),
        })
        self.runner = self.evidence / "declarative-runner.json"
        write_json(self.runner, {
            "schema": "motionwright.studio-service-runner/1",
            "source_freeze": ci.file_record(self.initial),
            "binary": ci.file_record(self.binary),
            "build_stage": ci.file_record(self.build_stage),
            "dependency_lock": ci.file_record(self.lock),
            "source_execution_scope": "TEST_JSONL_SERVICE_ONLY",
        })
        self.browser_stage = self.evidence / "declarative-browser-stage.json"
        write_json(self.browser_stage, {
            **common, "purpose": "browser-test", "cwd": str(self.repo / "apps/studio"),
            "argv": [str(self.node), str(self.playwright), "test", *self.contract["selectors"],
                     "--project=chromium", "--workers=2", "--retries=0", "--reporter=json",
                     "--output", str(self.output_dir)],
            "executable": ci.file_record(self.node),
            "browser_report": ci.file_record(self.report),
            "environment": {
                "MOTIONWRIGHT_SERVICE_TEST_BIN": str(self.binary),
                "MOTIONWRIGHT_SERVICE_TEST_TMPDIR": str(self.service_tmp),
                "PLAYWRIGHT_JSON_OUTPUT_NAME": str(self.report),
            },
            "bindings": {
                "runner_record": ci.file_record(self.runner),
                "runner_binary": ci.file_record(self.binary),
                "playwright_cli": ci.file_record(self.playwright),
                "output_directory": str(self.output_dir), "service_tmpdir": str(self.service_tmp),
            },
        })

    def validate_browser_fixture(self, stage=None):
        # Resolving a tool identity is mocked; invoking tools remains forbidden.
        with patch.object(ci.shutil, "which", side_effect=lambda name: str(self.node) if name == "node" else None):
            return ci.validate_browser_stage(
                stage or self.browser_stage, initial=self.initial, contract=self.contract,
                runner_path=self.runner, report=self.report, listing=False)

    def test_declarative_validator_fixture_has_a_complete_chain_without_execution(self):
        value = ci.validate_runner(self.runner, self.initial)
        self.assertEqual(value["binary"], ci.file_record(self.binary))
        result = self.validate_browser_fixture()
        self.assertEqual((result["status"], result["case_count"]), ("PASS", 89))

    def test_changed_runner_lock_or_original_build_receipt_is_rejected(self):
        for path in (self.binary, self.lock, self.build_stage):
            original = path.read_bytes()
            path.write_bytes(original + b"substituted bytes\n")
            try:
                with self.subTest(path=path.name), self.assertRaises(ValueError):
                    ci.validate_runner(self.runner, self.initial)
            finally:
                path.write_bytes(original)

    def test_browser_cannot_substitute_runner_identity_or_its_process_environment(self):
        original = json.loads(self.browser_stage.read_bytes())
        variants = {}
        for name, section, key, value in (
            ("other-runner-record", "bindings", "runner_record",
             {**original["bindings"]["runner_record"], "path": str(self.evidence / "copied-runner.json")}),
            ("other-runner-bytes", "bindings", "runner_binary",
             {**original["bindings"]["runner_binary"], "sha256": "0" * 64}),
            ("other-executed-runner", "environment", "MOTIONWRIGHT_SERVICE_TEST_BIN",
             str(self.evidence / "another-runner")),
            ("other-service-state", "environment", "MOTIONWRIGHT_SERVICE_TEST_TMPDIR",
             str(self.evidence / "other-service-tmp")),
            ("other-original-report", "environment", "PLAYWRIGHT_JSON_OUTPUT_NAME",
             str(self.evidence / "copied-browser-report.json")),
        ):
            changed = copy.deepcopy(original)
            changed[section][key] = value
            variants[name] = changed
        for name, value in variants.items():
            path = self.evidence / (name + ".json")
            write_json(path, value)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.validate_browser_fixture(path)

    def test_changed_browser_tool_or_widened_and_retried_selection_is_rejected(self):
        original_cli = self.playwright.read_bytes()
        self.playwright.write_bytes(b"A different browser test program.\n")
        try:
            with self.assertRaises(ValueError):
                self.validate_browser_fixture()
        finally:
            self.playwright.write_bytes(original_cli)
        original = json.loads(self.browser_stage.read_bytes())
        for name in ("missing-selector", "fixture-added", "retry-enabled"):
            changed = copy.deepcopy(original)
            if name == "missing-selector":
                changed["argv"].remove(self.contract["selectors"][0])
            elif name == "fixture-added":
                changed["argv"].insert(3, "tests/mock-only.spec.ts")
            else:
                changed["argv"][changed["argv"].index("--retries=0")] = "--retries=1"
            path = self.evidence / (name + ".json")
            write_json(path, changed)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.validate_browser_fixture(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
