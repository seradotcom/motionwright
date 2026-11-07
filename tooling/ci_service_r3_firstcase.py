"""One source-built StudioService first-case R3 diagnostic, never a release gate.

The original Playwright writer and a second real StudioService process must read
the SAME physical managed CAS Store on one Linux runner. The independent R3
reader owns the second transport, exact Project/journal checks and CAS guards.
No synthetic Store, archived-copy substitution, retries or G2 promotion.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

CASE_FILE = "tests/production-design-service.spec.ts"
CASE_TITLE = ("Scene boundary explicit source binding, Draft and Explicit saves preserve "
              "authored objects and assets through SQLite reopen and journalled removal")
WORKFLOW = ".github/workflows/studio-r3-firstcase.yml"
APPROVED_REF = re.compile(r"refs/heads/verify/r3-firstcase-reviewed-[a-z0-9-]{8,64}\Z")
SOURCE_INPUTS = (
    "Cargo.lock", "Cargo.toml", "SOURCE_LOCK.json", "rust-toolchain.toml",
    "crates/service/Cargo.toml", "crates/service/examples/studio_review_session.rs",
    "apps/studio/package.json", "apps/studio/package-lock.json",
    "apps/studio/playwright.config.ts", "apps/studio/vite.config.ts",
    "apps/studio/tests/production-design-service.spec.ts",
    "apps/studio/tests/support/scopedChangeService.ts",
    "tooling/service_readback/service_archive_readback_r3.py",
    "tooling/ci_service_r3_firstcase.py", WORKFLOW,
)
MAX_LOG = 64 * 1024 * 1024


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def new_json(path, value):
    path = Path(path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            timeout=30, check=False)
    require(result.returncode == 0, "Source git query failed: " + " ".join(args))
    return result.stdout.strip()


def source_snapshot(repo, expected):
    require(git(repo, "rev-parse", "HEAD") == expected, "Selected SHA differs from checkout HEAD")
    require(git(repo, "status", "--porcelain=v1", "--untracked-files=no") == "",
            "Tracked source changed or checkout is dirty")
    tracked = set(git(repo, "ls-files").splitlines())
    require(set(SOURCE_INPUTS).issubset(tracked), "Required source-bound input is untracked")
    spec = (repo / "apps/studio/tests/production-design-service.spec.ts").read_text(encoding="utf-8")
    require(spec.count('test("' + CASE_TITLE + '"') == 1, "Original first-case title/definition changed")
    return {"sha": expected, "tree": git(repo, "rev-parse", "HEAD^{tree}"),
            "inputs": {name: digest(repo / name) for name in SOURCE_INPUTS}}


def context(environment, expected):
    require(sys.platform == "linux" and environment.get("RUNNER_OS") == "Linux"
            and environment.get("RUNNER_ARCH") == "X64", "First-case producer requires hosted Linux x64")
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
            and environment.get("GITHUB_EVENT_NAME") == "push"
            and environment.get("GITHUB_JOB") == "firstcase",
            "First-case producer requires its one dedicated GitHub push job")
    require(APPROVED_REF.fullmatch(environment.get("GITHUB_REF", "")) is not None,
            "First-case physical verification requires an explicitly reviewed verification branch")
    require(environment.get("GITHUB_WORKFLOW_REF", "").startswith(
        "seradotcom/motionwright/" + WORKFLOW + "@" + environment["GITHUB_REF"]),
        "First-case workflow source/ref differs")
    require(environment.get("R3_EVENT_BEFORE") == "0" * 40
            and environment.get("GITHUB_RUN_ATTEMPT") == "1",
            "Only the new verification branch's first run is admitted; never replay a push")
    require(re.fullmatch(r"[0-9a-f]{40}", expected) is not None
            and environment.get("GITHUB_SHA") == expected
            and environment.get("GITHUB_WORKFLOW_SHA") == expected,
            "Expected source differs from this workflow and event")
    for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "NODE_OPTIONS",
                 "RUSTFLAGS", "RUSTC_WRAPPER", "CARGO_TARGET_DIR"):
        require(not environment.get(name), "Unapproved execution override: " + name)
    return {key: environment.get(key) for key in
            ("GITHUB_SHA", "GITHUB_REF", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF",
             "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME", "R3_EVENT_BEFORE")}


def playwright_argv(node, cli, output, *, listing):
    expression = "^" + re.escape("chromium production-design-service.spec.ts " + CASE_TITLE) + "$"
    argv = [node, str(cli), "test", CASE_FILE, "--grep", expression,
            "--project=chromium", "--workers=1", "--retries=0", "--repeat-each=1",
            "--forbid-only", "--reporter=json", "--output=" + str(output)]
    return argv + (["--list"] if listing else [])


def playwright_one(document, *, listing, expected_argv):
    require(isinstance(document, dict) and document.get("errors") == [],
            "Playwright original errors are present")
    config = document.get("config", {})
    require(config.get("argv") == expected_argv and config.get("workers") == 1
            and config.get("shard") is None and config.get("fullyParallel") is False
            and config.get("forbidOnly") is True,
            "Playwright original command or isolation differs")
    projects = config.get("projects")
    require(isinstance(projects, list) and len(projects) == 1
            and projects[0].get("id") == projects[0].get("name") == "chromium"
            and projects[0].get("retries") == 0 and projects[0].get("repeatEach") == 1,
            "Playwright project/retries changed")
    stats = document.get("stats", {})
    expected = {"expected": 0 if listing else 1, "unexpected": 0, "flaky": 0,
                "skipped": 1 if listing else 0}
    require(all(type(stats.get(k)) is int and stats[k] == v for k, v in expected.items()),
            "Playwright first-case counts/omissions differ")
    found = []
    def visit(suites):
        require(isinstance(suites, list), "Playwright suite tree malformed")
        for suite in suites:
            for spec in suite.get("specs", []):
                tests = spec.get("tests")
                require(isinstance(tests, list) and len(tests) == 1, "Unexpected extra Playwright project/case")
                test = tests[0]
                require(test.get("projectId") == test.get("projectName") == "chromium"
                        and test.get("expectedStatus") == "passed"
                        and spec.get("title") == CASE_TITLE
                        and str(spec.get("file", "")).replace("\\", "/") in (CASE_FILE, CASE_FILE.split("/")[-1])
                        and isinstance(spec.get("id"), str) and spec["id"],
                        "First-case original identity/title changed")
                results = test.get("results")
                if listing:
                    require(results == [], "Enumeration executed a test")
                else:
                    require(spec.get("ok") is True and test.get("status") == "expected"
                            and isinstance(results, list) and len(results) == 1
                            and results[0].get("status") == "passed"
                            and results[0].get("retry") == 0
                            and results[0].get("errors") == [] and results[0].get("error") is None,
                            "First-case failed, was skipped, retried, or omitted")
                found.append((spec["id"], test["projectId"]))
            visit(suite.get("suites", []))
    visit(document.get("suites", []))
    require(len(found) == 1, "One exact original first-case ID required")
    return found[0]


def example_binary(build_log, target):
    candidates = []
    for line in Path(build_log).read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("reason") != "compiler-artifact":
            continue
        unit = item.get("target", {})
        if unit.get("name") == "studio_review_session" and "example" in unit.get("kind", []):
            candidates.append(item.get("executable"))
    require(len(candidates) == 1 and isinstance(candidates[0], str),
            "Cargo did not produce exactly one real service example executable")
    binary = Path(candidates[0]).resolve(strict=True)
    require(binary.is_relative_to(Path(target).resolve(strict=True))
            and binary.is_file() and os.access(binary, os.X_OK)
            and 0 < binary.stat().st_size <= 400 * 1024 * 1024,
            "Compiled service binary path, mode or byte budget differs")
    return binary


def readback_pass(report, binary_hash, expected_source, test_id):
    require(report.get("status") == "READBACK_VERIFIED" and report.get("errors") == []
            and report.get("expectedCases") == 1
            and report.get("backendSourceShaAssertedByCaller") == expected_source
            and report.get("playwrightExitCodeReportedByCaller") == 0
            and report.get("receipt", {}).get("status") == "WRITTEN",
            "Independent physical readback receipt failed")
    require(report.get("readbackMode") == "original_physical_store"
            and report.get("binary", {}).get("sha256") == binary_hash
            and report.get("transportAttempted") is True
            and report.get("transportExecuted") is True,
            "No authenticated real binary/physical readback transport")
    cases = report.get("cases")
    require(isinstance(cases, list) and len(cases) == 1, "R3 did not retain exactly one case")
    case = cases[0]
    require(case.get("captureStatus") == "VERIFIED"
            and case.get("errors") == []
            and case.get("playwrightIdentity", {}).get("playwrightId") == test_id
            and case.get("playwrightIdentity", {}).get("projectId") == "chromium",
            "R3 case evidence is not bound to original Playwright ID")
    obs = case.get("readback", {})
    before = obs.get("physicalSourceBeforeSha256")
    close = obs.get("close", {})
    require(obs.get("status") == "VERIFIED" and obs.get("errors") == []
            and obs.get("readbackMode") == "original_physical_store"
            and isinstance(before, str) and re.fullmatch(r"[0-9a-f]{64}", before) is not None
            and obs.get("physicalSourceAfterSha256") == before
            and obs.get("transportAttempted") is True and obs.get("transportExecuted") is True
            and type(obs.get("processId")) is int and obs["processId"] > 0
            and type(obs.get("rpcCount")) is int and obs["rpcCount"] >= 5
            and obs.get("primaryFailure") is None and obs.get("projectEqual") is True
            and obs.get("journalEqual") is True and obs.get("receipt", {}).get("status") == "WRITTEN"
            and close.get("clean") is True and close.get("errors") == []
            and close.get("taints") == [] and close.get("returnCode") == 0
            and close.get("processId") == obs["processId"]
            and close.get("rpcCount") == obs["rpcCount"],
            "Independent real-service Project/journal, CAS or clean process custody incomplete")
    journal = report.get("observationJournal", {})
    require(journal.get("status") == "CLOSED"
            and type(journal.get("recordsAttempted")) is int
            and journal["recordsAttempted"] > 0
            and journal.get("recordsConfirmed") == journal["recordsAttempted"],
            "Durable transport journal did not close")
    return {"rpcCount": obs["rpcCount"], "processId": obs["processId"],
            "journalEntries": obs.get("journalEntries"), "physicalStoreSha256": before}


class Recorder:
    def __init__(self, evidence):
        self.evidence = evidence
        self.commands = []
        self.journal = evidence / "commands.jsonl"
        with self.journal.open("x", encoding="utf-8") as file:
            file.flush()
            os.fsync(file.fileno())

    def observe(self, row):
        with self.journal.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, sort_keys=True) + "\n")
            file.flush()
            os.fsync(file.fileno())

    def run(self, label, argv, cwd, env, timeout):
        require(label not in {x["label"] for x in self.commands}, "A command cannot be attempted twice")
        require(all(isinstance(v, str) for v in argv) and Path(cwd).is_dir()
                and timeout > 0, "Unsafe original command specification")
        row = {"label": label, "argv": argv, "cwd": str(cwd), "status": "INTENT",
               "stdout": str(self.evidence / (label + ".stdout.original.log")),
               "stderr": str(self.evidence / (label + ".stderr.original.log"))}
        self.commands.append(row)
        self.observe(row)
        start = time.monotonic()
        process = None
        try:
            with Path(row["stdout"]).open("xb") as stdout, Path(row["stderr"]).open("xb") as stderr:
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                           start_new_session=True)
                row.update(status="STARTED", pid=process.pid)
                self.observe(row)
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    row["status"] = "UNKNOWN_TIMEOUT_NO_RETRY"
                    raise ValueError("Timed-out real command cannot be retried: " + label)
            row.update(exit_code=code, status="CLOSED", elapsed_seconds=round(time.monotonic()-start, 3))
            for name in ("stdout", "stderr"):
                path = Path(row[name])
                require(path.stat().st_size <= MAX_LOG, "Original command output exceeds byte budget")
                row[name + "_sha256"] = digest(path)
            self.observe(row)
            require(code == 0, "Real command failed: " + label + " (exit " + str(code) + ")")
            return row
        finally:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    args = parser.parse_args()
    # Admit only the reviewed, unique branch-creation event before any child.
    identity = context(os.environ, args.expected_source)
    repo = args.repo.resolve(strict=True)
    root = args.root.absolute()
    require(not root.exists() and root.is_absolute()
            and root.parent == Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
            and not root.is_relative_to(repo) and not repo.is_relative_to(root),
            "Output is not a fresh runner-owned evidence namespace")
    require(shutil.disk_usage(root.parent).free >= 8 * 1024**3, "Insufficient bounded compiler disk reserve")
    before = source_snapshot(repo, args.expected_source)
    root.mkdir()
    evidence = root / "evidence"
    evidence.mkdir()
    for name in ("cargo", "target", "npm", "browsers", "tmp"):
        (root / name).mkdir()
    new_json(evidence / "source-before.json", before)
    env = dict(os.environ, CI="true", PYTHONDONTWRITEBYTECODE="1",
               CARGO_BUILD_JOBS="1", CARGO_HOME=str(root / "cargo"),
               CARGO_TARGET_DIR=str(root / "target"), NPM_CONFIG_CACHE=str(root / "npm"),
               PLAYWRIGHT_BROWSERS_PATH=str(root / "browsers"), TMPDIR=str(root / "tmp"),
               MOTIONWRIGHT_PLAYWRIGHT_PORT="14361")
    recorder = Recorder(evidence)
    status = {"schema": "motionwright.r3-firstcase-real-hosted-diagnostic/1",
              "source": before, "github": identity, "status": "STARTED", "commands": recorder.commands,
              "cases": "NOT_RUN", "physicalReadback": "NOT_RUN", "G2": "NOT_GRANTED", "G3": "STOP"}
    result_code = 1
    try:
        studio = repo / "apps/studio"
        node = shutil.which("node")
        cargo = shutil.which("cargo")
        require(node and cargo and shutil.which("npm"), "Missing hosted Node/Cargo/NPM")
        recorder.run("cargo-format", [cargo, "fmt", "--all", "--", "--check"], repo, env, 150)
        recorder.run("locked-cargo-build",
                     [cargo, "build", "--locked", "--jobs", "1", "-p", "motionwright-service",
                      "--example", "studio_review_session", "--message-format=json"], repo, env, 2400)
        binary = example_binary(evidence / "locked-cargo-build.stdout.original.log", root / "target")
        binary_hash = digest(binary)
        status["binary"] = {"path": str(binary), "bytes": binary.stat().st_size, "sha256": binary_hash}
        provenance = {"schema": "motionwright.r3-firstcase-source-built-binary/1",
                      "source": before, "github": identity, "binary": status["binary"],
                      "build": recorder.commands[-1], "humanAcceptance": "NOT_GRANTED"}
        new_json(evidence / "source-build-binary.json", provenance)
        recorder.run("npm-ci", ["npm", "ci", "--no-audit", "--no-fund"], studio, env, 600)
        cli = studio / "node_modules/@playwright/test/cli.js"
        require(cli.resolve(strict=True).is_relative_to(studio / "node_modules"),
                "Playwright CLI escaped admitted installation")
        recorder.run("chromium-install", [node, str(cli), "install", "--with-deps", "chromium"],
                     studio, env, 900)
        pw_env = dict(env, MOTIONWRIGHT_SERVICE_TEST_BIN=str(binary),
                      MOTIONWRIGHT_SERVICE_TEST_TMPDIR=str(root / "tmp"))
        originals = {}
        for listing, label in ((True, "list"), (False, "execution")):
            reporter = evidence / ("playwright-" + label + ".json")
            argv = playwright_argv(node, cli, evidence / "results", listing=listing)
            run_env = dict(pw_env, PLAYWRIGHT_JSON_OUTPUT_FILE=str(reporter))
            recorder.run("playwright-" + label, argv, studio, run_env, 120 if listing else 720)
            require(reporter.is_file() and reporter.stat().st_size <= 12 * 1024 * 1024,
                    "Original Playwright reporter unavailable or too large")
            document = json.loads(reporter.read_bytes())
            require(Path(document["config"]["rootDir"]).resolve(strict=True) == studio / "tests",
                    "Original Playwright test root changed")
            originals[label] = {"path": str(reporter), "sha256": digest(reporter),
                                "id": playwright_one(document, listing=listing, expected_argv=argv)}
        require(originals["list"]["id"] == originals["execution"]["id"],
                "Original independent Playwright discovery/execution IDs differ")
        last_run = evidence / "results/.last-run.json"
        require(json.loads(last_run.read_bytes()) == {"status": "passed", "failedTests": []},
                "Original Playwright test-results terminal marker did not pass")
        status["cases"] = "ONE_ORIGINAL_CHROMIUM_PASS"
        status["playwright"] = originals
        require(source_snapshot(repo, args.expected_source) == before,
                "Source changed between build, first writer and second reader")
        require(digest(binary) == binary_hash, "Compiled service binary changed")
        provenance_path = evidence / "source-build-binary.json"
        reader = repo / "tooling/service_readback/service_archive_readback_r3.py"
        require(digest(reader) == before["inputs"]["tooling/service_readback/service_archive_readback_r3.py"],
                "Independent physical reader changed")
        argv = [sys.executable, "-I", "-B", "-X", "utf8", str(reader),
                "--results-root", str(evidence / "results"), "--output", str(evidence / "readback"),
                "--expected-cases", "1", "--playwright-exit-code", "0",
                "--playwright-json", originals["execution"]["path"],
                "--expected-playwright-sha256", originals["execution"]["sha256"],
                "--expected-roster-json", originals["list"]["path"],
                "--expected-roster-sha256", originals["list"]["sha256"],
                "--provenance-receipt", str(provenance_path),
                "--expected-provenance-sha256", digest(provenance_path),
                "--provenance-reviewed", "--execute-readback", "--physical-original-store",
                "--expected-binary-sha256", binary_hash, "--backend-source-sha", args.expected_source]
        recorder.run("independent-physical-r3", argv, repo, pw_env, 360)
        report = json.loads((evidence / "readback/readback-report.json").read_bytes())
        status["readback"] = readback_pass(report, binary_hash, args.expected_source,
                                          originals["execution"]["id"][0])
        require(source_snapshot(repo, args.expected_source) == before
                and digest(binary) == binary_hash, "Source/binary changed after real readback")
        status["physicalReadback"] = "ONE_REAL_ORIGINAL_PHYSICAL_CAS_STORE_VERIFIED"
        status["status"] = "PASS_ONE_REAL_CASE_DIAGNOSTIC_NOT_G2"
        result_code = 0
    except BaseException as error:
        status["status"] = "FAILED_OR_UNKNOWN_NO_RETRY"
        status["firstCause"] = type(error).__name__ + ": " + str(error)
    status["ended"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    status["commands"] = recorder.commands
    new_json(evidence / "RESULT.json", status)
    print(json.dumps({"status": status["status"], "firstCause": status.get("firstCause"),
                      "receipt": str(evidence / "RESULT.json")}), flush=True)
    return result_code


if __name__ == "__main__":
    sys.exit(main())
