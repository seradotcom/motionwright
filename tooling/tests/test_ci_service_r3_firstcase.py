"""Source-only R3 first-case controls; no Cargo, browser or real RPC is launched."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tooling import ci_service_r3_firstcase as firstcase


def reporter(listing=False):
    args = firstcase.playwright_argv("node", Path("/repo/apps/studio/node_modules/@playwright/test/cli.js"),
                                     Path("/results"), listing=listing)
    return {"errors": [], "config": {"argv": args, "workers": 1, "shard": None,
             "fullyParallel": False, "forbidOnly": True,
             "projects": [{"id": "chromium", "name": "chromium", "retries": 0, "repeatEach": 1}]},
            "stats": {"expected": 0 if listing else 1, "unexpected": 0,
                      "flaky": 0, "skipped": 1 if listing else 0},
            "suites": [{"specs": [{"id": "ORIGINAL-TEST-ID", "ok": True,
                "title": firstcase.CASE_TITLE, "file": "production-design-service.spec.ts",
                "tests": [{"projectId": "chromium", "projectName": "chromium",
                           "expectedStatus": "passed", "status": "skipped" if listing else "expected",
                           "results": [] if listing else [{"status": "passed", "retry": 0,
                                                            "errors": [], "error": None}]}]}]}]}


def readback():
    observation = {"status": "VERIFIED", "errors": [], "readbackMode": "original_physical_store",
                   "physicalSourceBeforeSha256": "a" * 64, "physicalSourceAfterSha256": "a" * 64,
                   "transportAttempted": True, "transportExecuted": True,
                   "processId": 444, "rpcCount": 5, "primaryFailure": None,
                   "projectEqual": True, "journalEqual": True, "journalEntries": 8,
                   "receipt": {"status": "WRITTEN"},
                   "close": {"clean": True, "errors": [], "taints": [], "returnCode": 0,
                             "processId": 444, "rpcCount": 5}}
    return {"status": "READBACK_VERIFIED", "errors": [], "expectedCases": 1,
            "backendSourceShaAssertedByCaller": "b" * 40,
            "playwrightExitCodeReportedByCaller": 0, "readbackMode": "original_physical_store",
            "binary": {"sha256": "c" * 64}, "receipt": {"status": "WRITTEN"},
            "transportAttempted": True, "transportExecuted": True,
            "observationJournal": {"status": "CLOSED", "recordsAttempted": 5,
                                   "recordsConfirmed": 5},
            "cases": [{"captureStatus": "VERIFIED", "errors": [],
                       "playwrightIdentity": {"playwrightId": "ORIGINAL-TEST-ID",
                                             "projectId": "chromium"},
                       "readback": observation}]}


class FirstCaseSourceOnly(unittest.TestCase):
    def test_workflow_does_not_run_on_integration_or_a_pull_request(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / firstcase.WORKFLOW).read_text(encoding="utf-8")
        self.assertIn("branches: ['verify/r3-firstcase-reviewed-*']", workflow)
        self.assertIn("R3_EVENT_BEFORE: ${{ github.event.before }}", workflow)
        self.assertIn('test "$GITHUB_RUN_ATTEMPT" = 1', workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertIn("test_ci_service_r3_firstcase -v", workflow)
        self.assertIn('--expected-source "$GITHUB_SHA"', workflow)
        self.assertIn("if: always()", workflow)
        self.assertNotIn("phase1-service47", workflow)
        self.assertNotIn("studio-service-68", workflow)

    def test_exact_real_source_case_and_reader_inputs(self):
        root = Path(__file__).resolve().parents[2]
        spec = (root / "apps/studio/tests/production-design-service.spec.ts").read_text(encoding="utf-8")
        self.assertEqual(spec.count('test("' + firstcase.CASE_TITLE + '"'), 1)
        self.assertEqual(firstcase.CASE_FILE, "tests/production-design-service.spec.ts")
        self.assertIn("tooling/service_readback/service_archive_readback_r3.py", firstcase.SOURCE_INPUTS)

    def test_playwright_firstcase_cli_is_one_worker_without_retry(self):
        args = firstcase.playwright_argv("node", Path("/test/cli.js"), Path("/results"), listing=False)
        self.assertEqual(args[:4], ["node", str(Path("/test/cli.js")), "test", firstcase.CASE_FILE])
        self.assertEqual(args[4:6], ["--grep", "^" + firstcase.re.escape(
            "chromium production-design-service.spec.ts " + firstcase.CASE_TITLE) + "$"])
        for flag in ("--project=chromium", "--workers=1", "--retries=0", "--repeat-each=1",
                     "--forbid-only", "--reporter=json"):
            self.assertEqual(args.count(flag), 1)
        self.assertNotIn("--list", args)
        self.assertEqual(firstcase.playwright_argv("node", Path("/test/cli.js"), Path("/results"), listing=True),
                         args + ["--list"])

    def test_original_list_and_execution_bind_one_identical_playwright_id(self):
        for listing in (False, True):
            value = reporter(listing)
            self.assertEqual(firstcase.playwright_one(value, listing=listing,
                             expected_argv=value["config"]["argv"]), ("ORIGINAL-TEST-ID", "chromium"))

    def test_extra_cases_changed_ids_skips_and_retries_are_rejected(self):
        for mutation in ("extra", "title", "otherfile", "project", "unexpected", "skip", "retry",
                         "error", "forbidOnly", "argv", "emptyId"):
            value = reporter()
            spec = value["suites"][0]["specs"][0]
            result = spec["tests"][0]["results"][0]
            if mutation == "extra": value["suites"][0]["specs"].append(copy.deepcopy(spec))
            elif mutation == "title": spec["title"] = "another test"
            elif mutation == "otherfile": spec["file"] = "graph-mock.spec.ts"
            elif mutation == "project": spec["tests"][0]["projectId"] = "firefox"
            elif mutation == "unexpected": value["stats"]["unexpected"] = 1
            elif mutation == "skip": value["stats"]["skipped"] = 1
            elif mutation == "retry": result["retry"] = 1
            elif mutation == "error": result["errors"] = [{"message": "failure"}]
            elif mutation == "forbidOnly": value["config"]["forbidOnly"] = False
            elif mutation == "argv": value["config"]["argv"].append("--workers=8")
            else: spec["id"] = ""
            control = reporter()["config"]["argv"]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                firstcase.playwright_one(value, listing=False, expected_argv=control)

    def test_enumeration_cannot_contain_executed_result(self):
        value = reporter(True)
        value["suites"][0]["specs"][0]["tests"][0]["results"] = [{"status": "passed"}]
        with self.assertRaises(ValueError):
            firstcase.playwright_one(value, listing=True, expected_argv=value["config"]["argv"])

    def test_context_requires_first_reviewed_branch_creation(self):
        sha = "d" * 40
        ref = "refs/heads/verify/r3-firstcase-reviewed-20261107-a2a5"
        env = {"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "GITHUB_ACTIONS": "true",
               "GITHUB_REPOSITORY": "seradotcom/motionwright", "GITHUB_EVENT_NAME": "push",
               "GITHUB_JOB": "firstcase", "GITHUB_REF": ref, "GITHUB_SHA": sha,
               "GITHUB_WORKFLOW_SHA": sha,
               "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/" + firstcase.WORKFLOW + "@" + ref,
               "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "11111",
               "R3_EVENT_BEFORE": "0" * 40}
        with mock.patch.object(firstcase.sys, "platform", "linux"):
            self.assertEqual(firstcase.context(env, sha)["GITHUB_SHA"], sha)
            for key, invalid in (("GITHUB_REF", "refs/heads/integrate/phase1-final-composition-20261027"),
                                 ("GITHUB_REF", "refs/heads/verify/r3-firstcase-unreviewed"),
                                 ("GITHUB_RUN_ATTEMPT", "2"), ("R3_EVENT_BEFORE", "1" * 40),
                                 ("GITHUB_SHA", "f" * 40), ("GITHUB_WORKFLOW_SHA", "e" * 40),
                                 ("GITHUB_EVENT_NAME", "workflow_dispatch"),
                                 ("LD_PRELOAD", "/tmp/interpose.so")):
                with self.subTest(key=key, invalid=invalid), self.assertRaises(ValueError):
                    firstcase.context({**env, key: invalid}, sha)

    def test_original_physical_real_reader_receipt_requires_every_boundary(self):
        correct = readback()
        self.assertEqual(firstcase.readback_pass(correct, "c"*64, "b"*40, "ORIGINAL-TEST-ID")["rpcCount"], 5)
        for change in ("copied", "invalid_root", "changed_root", "journal", "process",
                       "binary", "source", "project", "unclean", "receipt", "foreign_id",
                       "short_rpc", "extra_case"):
            value = copy.deepcopy(correct)
            r = value["cases"][0]["readback"]
            if change == "copied": value["readbackMode"] = "archive_extracted_copy"
            elif change == "invalid_root": r["physicalSourceBeforeSha256"] = "not a sha"
            elif change == "changed_root": r["physicalSourceAfterSha256"] = "f"*64
            elif change == "journal": value["observationJournal"]["recordsConfirmed"] = 4
            elif change == "process": r["processId"] = 0
            elif change == "binary": value["binary"]["sha256"] = "f"*64
            elif change == "source": value["backendSourceShaAssertedByCaller"] = "f"*40
            elif change == "project": r["projectEqual"] = False
            elif change == "unclean": r["close"]["clean"] = False
            elif change == "receipt": value["receipt"]["status"] = "PENDING"
            elif change == "foreign_id": value["cases"][0]["playwrightIdentity"]["playwrightId"] = "other"
            elif change == "short_rpc": r["rpcCount"] = 4
            else: value["cases"].append(copy.deepcopy(value["cases"][0]))
            with self.subTest(change=change), self.assertRaises(ValueError):
                firstcase.readback_pass(value, "c"*64, "b"*40, "ORIGINAL-TEST-ID")

    def test_build_output_must_have_exact_one_compiled_example(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exe = root / "target/debug/examples/studio_review_session"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"INERT FIXTURE - NEVER EXECUTED")
            exe.chmod(0o755)
            log = root / "build.stdout"
            artifact = {"reason": "compiler-artifact", "target":
                        {"name": "studio_review_session", "kind": ["example"]}, "executable": str(exe)}
            log.write_text(json.dumps(artifact) + "\n")
            self.assertEqual(firstcase.example_binary(log, root / "target"), exe)
            log.write_text(json.dumps(artifact) + "\n" + json.dumps(artifact) + "\n")
            with self.assertRaises(ValueError):
                firstcase.example_binary(log, root / "target")
            other = root / "other"
            other.write_bytes(b"INERT")
            other.chmod(0o755)
            artifact["executable"] = str(other)
            log.write_text(json.dumps(artifact) + "\n")
            with self.assertRaises(ValueError):
                firstcase.example_binary(log, root / "target")


if __name__ == "__main__":
    unittest.main()
