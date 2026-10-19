"""One explicit UI performance observation; no general browser or release verdict.

The original test owns the measurement. This tool retains its one process and
checks its original report and attachment without modifying the test or samples.
An unfinished process is UNKNOWN and never permission to run it again.
"""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site
                                  and sys.flags.dont_write_bytecode):
    raise SystemExit("Run this evidence tool with Python -I -B -S")

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import types


SELF = "tooling/ui_performance_evidence.py"
TESTS = "tooling/tests/test_ui_performance_evidence.py"
PROVIDER = "tooling/ci_browser_evidence.py"
PROVIDER_SHA256 = "f7c10b3ecc6ee3d6653ef65e7033c88b31214c5aa8e446f747b3f24d306088e4"
WORKFLOW = ".github/workflows/performance-acceptance.yml"
SPEC = "apps/studio/tests/performance.spec.ts"
CONFIG = "apps/studio/playwright.config.ts"
PACKAGE = "apps/studio/package.json"
LOCK = "apps/studio/package-lock.json"
REVIEWED_INPUTS = {
    SPEC: "59f23f7f4a2beb94af74408e2886a54bff0b03fa7f0e39042e79620efcad9f0e",
    CONFIG: "facf22ed7a17d6150f0bf78a9eeb4e86daed7bd746b7de59dfb1b84ea057d38c",
    PACKAGE: "d018bb850cea3d21f368bff5a014899df9bae43f3f4f21eb13b8ac7e59c50336",
    PROVIDER: PROVIDER_SHA256,
}
SOURCE_INPUTS = (SELF, TESTS, PROVIDER, WORKFLOW, SPEC, CONFIG, PACKAGE, LOCK,
                 "tooling/ci_test_runner.py", "Cargo.lock")
CASE = {
    "file": "performance.spec.ts", "line": 25, "column": 1,
    "id": "b15d6361b2fc38288977-47483b7dbbeab9302429",
    "title": "S-profile scene selection updates the inspector within the interactive budget",
}
SURFACE = "project rail scene selection -> inspector scene name -> next animation frame"
PROFILE = {"schema": "motionwright.ui-performance-profile/1", "name": "single-ui-selection",
           "case": CASE, "project": "chromium", "samples": 60, "scenes": 12,
           "p95_rank_one_based": 57, "budget_ms": 100, "reviewed_inputs": REVIEWED_INPUTS}
CONTEXT_KEYS = ("GITHUB_REPOSITORY", "GITHUB_EVENT_NAME", "GITHUB_SHA", "GITHUB_REF",
                "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID",
                "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_CUSTODY_BYTES = 256 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 180
CLAIMS = {"service_runner_requested": False, "sqlite_integration": False,
          "native_execution": False, "human_evaluation": False, "formal_acceptance": False,
          "candidate_eligible": False, "g3_release_verdict": False,
          "full_browser_suite_verdict": False, "same_run_reuse_eligible": False,
          "retry_attempted": False}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON member")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("Nonfinite JSON number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def safe_path(path, *, missing=False):
    path = Path(path).absolute()
    require(".." not in path.parts, "Traversal is not an evidence path")
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            require(missing, "Missing evidence input")
            continue
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400, "Linked evidence path")
        if part != path:
            require(stat.S_ISDIR(info.st_mode), "Non-directory evidence ancestor")
    return path


def read(path, limit=MAX_SOURCE_BYTES):
    path = safe_path(path)
    before = path.stat()
    require(type(limit) is int and 0 <= limit <= MAX_CUSTODY_BYTES
            and stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= limit, "Unbounded input")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(before.st_size + 1)
        closed = os.fstat(stream.fileno())
    signature = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    require(signature(before) == signature(opened) == signature(closed) == signature(path.stat())
            and len(raw) == before.st_size, "Input changed while read")
    return raw


def save(path, value):
    path = safe_path(path, missing=True)
    raw = encoded(value)
    with path.open("xb") as stream:
        require(stream.write(raw) == len(raw), "Short evidence write")
        stream.flush()
        os.fsync(stream.fileno())
    require(read(path, MAX_REPORT_BYTES) == raw, "Evidence write readback differs")
    return identity(raw)


def actual_context():
    context = {name: os.environ.get(name, "") for name in CONTEXT_KEYS}
    require(context["GITHUB_REPOSITORY"] == "seradotcom/motionwright", "Unexpected repository")
    require(context["GITHUB_EVENT_NAME"] == "workflow_dispatch"
            and context["GITHUB_JOB"] == "ui-selection"
            and os.environ.get("MW_UI_PERFORMANCE_SCOPE") == "ui", "Only the explicit UI scope is supported")
    sha, ref = context["GITHUB_SHA"], context["GITHUB_REF"]
    require(re.fullmatch(r"[0-9a-f]{40}", sha) and context["GITHUB_WORKFLOW_SHA"] == sha,
            "Source and workflow must be the same exact commit")
    require(ref.startswith(("refs/heads/", "refs/tags/"))
            and context["GITHUB_WORKFLOW_REF"] == context["GITHUB_REPOSITORY"] + "/" + WORKFLOW + "@" + ref,
            "Wrong performance workflow reference")
    require(context["GITHUB_RUN_ID"].isdecimal() and int(context["GITHUB_RUN_ID"]) > 0
            and context["GITHUB_RUN_ATTEMPT"] == "1", "Repeated attempts require separate review")
    require(not os.environ.get("MOTIONWRIGHT_SERVICE_TEST_BIN"), "UI scope has no service runner")
    for name in ("NODE_OPTIONS", "NODE_PATH", "PWDEBUG", "PW_TEST_SOURCE_TRANSFORM",
                 "PW_TEST_SOURCE_TRANSFORM_SCOPE", "PW_TEST_CONNECT_WS_ENDPOINT",
                 "PLAYWRIGHT_TEST_BASE_URL", "MOTIONWRIGHT_PLAYWRIGHT_PORT"):
        require(not os.environ.get(name), "Unsupported runtime override: " + name)
    return context


def git(repo, *args, input_bytes=None):
    environment = {name: value for name, value in os.environ.items() if not name.upper().startswith("GIT_")}
    environment.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull, GIT_NO_LAZY_FETCH="1",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
                            input=input_bytes, env=environment, capture_output=True, timeout=45)
    require(result.returncode == 0, "Original Git source read failed")
    return result.stdout


def source(repo, context):
    sha = context["GITHUB_SHA"]
    require(git(repo, "rev-parse", "HEAD").decode().strip() == sha, "Checkout SHA differs")
    commit = git(repo, "cat-file", "commit", sha)
    require(hashlib.sha1(b"commit " + str(len(commit)).encode() + b"\0" + commit).hexdigest() == sha,
            "Original commit identity differs")
    trees = [line[5:].decode() for line in commit.split(b"\n\n", 1)[0].splitlines() if line.startswith(b"tree ")]
    require(len(trees) == 1 and re.fullmatch(r"[0-9a-f]{40}", trees[0]), "Invalid original source tree")
    dirty = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching")
    require(all(row == b"!! apps/studio/node_modules/" for row in dirty.split(b"\0") if row),
            "Source checkout has unbound changes or ignored inputs")
    listing = {}
    for row in git(repo, "ls-tree", "-rz", sha, "--", *SOURCE_INPUTS).split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            require(mode in ("100644", "100755") and kind == "blob", "Nonregular source input")
            listing[name.decode()] = oid
    require(set(listing) == set(SOURCE_INPUTS), "Missing exact source input")
    originals, inputs = {}, {}
    for name, oid in listing.items():
        raw = read(repo / name)
        require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == oid,
                "Worktree bytes differ from the original Git blob: " + name)
        if name in REVIEWED_INPUTS:
            require(identity(raw)["sha256"] == REVIEWED_INPUTS[name], "Input is outside the reviewed UI profile: " + name)
        inputs[name] = {"git_blob": oid, **identity(raw)}
        originals[name] = raw
    require(read(Path(__file__)) == originals[SELF], "Executing verifier differs from the checkout")
    return {"source_sha": sha, "source_tree": trees[0], "source_commit": identity(commit), "inputs": inputs}, originals


def load_provider(raw):
    require(identity(raw)["sha256"] == PROVIDER_SHA256, "Unreviewed custody utility source")
    module = types.ModuleType("_ui_performance_original_custody_utilities")
    module.__file__ = PROVIDER
    exec(compile(raw, PROVIDER, "exec"), module.__dict__)
    return module


def runtime(provider, repo, original_lock, executable_paths):
    lock = strict_json(original_lock)
    require(isinstance(lock, dict) and type(lock.get("lockfileVersion")) is int
            and lock["lockfileVersion"] == 3 and isinstance(lock.get("packages"), dict), "Invalid runtime lock")
    inputs = {}
    for package, expected in provider.REVIEWED_RUNTIME_PACKAGES.items():
        entry = lock["packages"].get("node_modules/" + package)
        require(isinstance(entry, dict) and not entry.get("link")
                and all(entry.get(key) == expected[key] for key in ("version", "resolved", "integrity", "dependencies")),
                "Runtime lock differs from the reviewed two-package profile")
        inputs[package] = provider.package_inventory(repo / "apps/studio/node_modules" / package, expected)
    shim = repo / "apps/studio/node_modules/.bin/playwright"
    safe_path(shim.parent)
    require(shim.is_symlink(), "Playwright launcher is not the installed npm symlink")
    target = os.readlink(shim)
    require(target in ("../@playwright/test/cli.js", "../playwright/cli.js"), "Unreviewed Playwright launcher target")
    resolved = shim.resolve(strict=True)
    require(resolved in (repo / "apps/studio/node_modules/@playwright/test/cli.js",
                         repo / "apps/studio/node_modules/playwright/cli.js"), "Playwright launcher escaped its reviewed package")
    return {"packages": inputs, "profile": provider.REVIEWED_RUNTIME_PACKAGES,
            "cli_link": {"target": target, "resolved_path": str(resolved), **provider.file_record(resolved)},
            "archive_sri_recomputed_during_execution": False, "entire_dependency_graph_authenticated": False,
            "executables": {name: {"path": str(path), **provider.file_record(path)}
                            for name, path in executable_paths.items()}}


def command_for(npx, output):
    return [str(npx), "playwright", "test", "tests/performance.spec.ts", "--project=chromium", "--workers=1",
            "--retries=0", "--reporter=dot,json", "--output=" + str(output / "test-results")]


def custody(provider, output):
    rows, errors, pending, seen, size = {}, [], [], set(), 0
    for name in ("stdout.log", "stderr.log", "playwright-report.json", "ui-selection.json", "test-results"):
        path = output / name
        if path.exists() or path.is_symlink():
            pending.append(path)
    while pending:
        path = pending.pop()
        relative = path.relative_to(output).as_posix()
        try:
            require(relative.casefold() not in seen and len(seen) < 128, "Colliding or excessive custody members")
            seen.add(relative.casefold())
            info = safe_path(path).lstat()
            if stat.S_ISDIR(info.st_mode):
                for member in path.iterdir():
                    require(member.name.isascii() and not any(char in member.name for char in "\\:\0"),
                            "Unsafe custody member")
                    require(len(seen) + len(pending) < 128, "Excessive custody members")
                    pending.append(member)
            else:
                record = provider.file_record(path, max_bytes=MAX_CUSTODY_BYTES - size)
                size += record["bytes"]
                rows[relative] = record
        except (OSError, ValueError) as error:
            errors.append({"path": relative, "error": type(error).__name__ + ": " + str(error)})
    return {"files": dict(sorted(rows.items())), "errors": errors,
            "state": "ORIGINALS_HASHED" if not errors else "INCOMPLETE",
            "child_tree_quiescence": "NOT_PROVED", "test_case_results_admitted": False}


def capture(repo, output):
    repo, output = safe_path(repo), safe_path(output, missing=True)
    require(repo not in (output, *output.parents), "Output must be outside source checkout")
    output.mkdir(parents=True, exist_ok=False)
    context = {name: os.environ.get(name, "") for name in CONTEXT_KEYS}
    receipt = {"schema": "motionwright.ui-performance-command/1", "execution_context": context,
               "profile": PROFILE, "profile_sha256": identity(encoded(PROFILE))["sha256"],
               "state": "NOT_STARTED", "original_exit_code": None, "started_at": now(),
               "source_before": None, "source_after": None, "runtime_before": None, "runtime_after": None,
               "custody": None, "errors": [], "test_case_results_admitted": False, "claims": CLAIMS}
    process, code, provider, outcome_attempted = None, None, None, False
    try:
        context = actual_context()
        receipt["execution_context"] = context
        observed, originals = source(repo, context)
        provider = load_provider(originals[PROVIDER])
        executables = {name: Path(shutil.which(name) or "").resolve(strict=True) for name in ("node", "npx")}
        executables["python"] = Path(sys.executable).resolve(strict=True)
        receipt["source_before"] = observed
        receipt["runtime_before"] = runtime(provider, repo, originals[LOCK], executables)
        command = command_for(executables["npx"], output)
        receipt["command"] = command
        environment = dict(os.environ, MOTIONWRIGHT_PERF_ACCEPTANCE="1",
                           MOTIONWRIGHT_UI_PERF_REPORT=str(output / "ui-selection.json"),
                           PLAYWRIGHT_JSON_OUTPUT_FILE=str(output / "playwright-report.json"))
        receipt["intent"] = save(output / "intent.json", {
            "schema": "motionwright.ui-performance-intent/1", "execution_context": context,
            "profile_sha256": receipt["profile_sha256"], "source_before": observed,
            "runtime_before": receipt["runtime_before"], "command": command, "cwd": str(repo / "apps/studio"),
            "measurement_env": {key: environment[key] for key in ("MOTIONWRIGHT_PERF_ACCEPTANCE",
                                 "MOTIONWRIGHT_UI_PERF_REPORT", "PLAYWRIGHT_JSON_OUTPUT_FILE")},
            "state": "UNKNOWN_UNTIL_OUTCOME", "timeout_seconds": COMMAND_TIMEOUT_SECONDS, "retry_attempted": False})
        with (output / "stdout.log").open("xb") as stdout, (output / "stderr.log").open("xb") as stderr:
            process = subprocess.Popen(command, cwd=repo / "apps/studio", env=environment, stdout=stdout, stderr=stderr)
            receipt["state"] = "UNKNOWN"
            save(output / "process-start.json", {"execution_context": context, "pid": process.pid,
                                                  "started_at": now(), "state": "UNKNOWN_UNTIL_OUTCOME"})
            code = process.wait(timeout=COMMAND_TIMEOUT_SECONDS)
            require(type(code) is int, "Invalid original process exit")
            receipt["state"] = "COMPLETED"
            receipt["original_exit_code"] = code
        outcome_attempted = True
        save(output / "process-outcome.json", {"execution_context": context, "state": "COMPLETED",
             "original_exit_code": code, "finished_at": now(), "retry_attempted": False,
             "child_tree_quiescence": "NOT_PROVED"})
        receipt["source_after"], after_originals = source(repo, context)
        receipt["runtime_after"] = runtime(provider, repo, after_originals[LOCK], executables)
        require(receipt["source_before"] == receipt["source_after"]
                and receipt["runtime_before"] == receipt["runtime_after"], "Source or runtime changed during observation")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        receipt["errors"].append(type(error).__name__ + ": " + str(error))
    finally:
        # A started process without a known exit remains UNKNOWN. No poll, kill,
        # wait-again, replacement process or attempt replay is performed here.
        receipt["state"] = "UNKNOWN" if process is not None and code is None else receipt["state"]
        receipt["original_exit_code"] = code
        if not outcome_attempted:
            save(output / "process-outcome.json", {"execution_context": context, "state": receipt["state"],
                 "original_exit_code": code, "finished_at": now(), "retry_attempted": False,
                 "child_tree_quiescence": "NOT_PROVED"})
        if provider is not None:
            receipt["custody"] = custody(provider, output)
        receipt["finished_at"] = now()
        save(output / "receipt.json", receipt)
    if process is not None and code is None:
        return 124
    if type(code) is int and code != 0:
        return code if code > 0 else 128 - code
    return 0 if code == 0 and not receipt["errors"] and receipt["custody"]["state"] == "ORIGINALS_HASHED" else 2


def validate_reports(report, measurement, original_ui_bytes, context, repo, output, command):
    require(isinstance(report, dict) and report.get("errors") == [], "Missing or failed Playwright report")
    stats = report.get("stats", {})
    require(all(type(stats.get(key)) is int and stats[key] == value
                for key, value in {"expected": 1, "unexpected": 0, "skipped": 0, "flaky": 0}.items()),
            "Exactly one case without omissions or flaky outcomes is required")
    config = report.get("config", {})
    root = str(repo / "apps/studio/tests")
    require(config.get("version") == "1.63.0" and config.get("rootDir") == root
            and config.get("configFile") == str(repo / CONFIG)
            and type(config.get("workers")) is int and config["workers"] == 1
            and config.get("shard") is None and config.get("reporter") == [["dot"], ["json"]],
            "Reported configuration differs from the one-case profile")
    argv = config.get("argv")
    require(isinstance(argv, list) and len(argv) == len(command) and argv[2:] == command[2:]
            and Path(argv[1]) == repo / "apps/studio/node_modules/.bin/playwright", "Reported CLI differs")
    projects = config.get("projects")
    require(isinstance(projects, list) and len(projects) == 1, "Exactly one browser project is required")
    project = projects[0]
    require(project.get("name") == project.get("id") == "chromium" and project.get("testDir") == root
            and project.get("outputDir") == str(output / "test-results")
            and type(project.get("retries")) is int and project["retries"] == 0
            and type(project.get("repeatEach")) is int and project["repeatEach"] == 1,
            "Project selection, repetitions or retries differ")
    require(config.get("metadata", {}).get("ci", {}).get("commitHash") == context["GITHUB_SHA"],
            "Report source differs")
    pending, cases = report.get("suites"), []
    require(isinstance(pending, list), "Missing report suites")
    pending, visited = list(pending), 0
    while pending:
        visited += 1
        require(visited <= 8, "Excessive report suite nesting")
        suite = pending.pop()
        require(isinstance(suite, dict) and isinstance(suite.get("specs"), list), "Invalid report suite")
        cases.extend(suite["specs"])
        children = suite.get("suites", [])
        require(isinstance(children, list) and len(cases) <= 1 and len(pending) + len(children) <= 8,
                "Additional selected cases or excessive nesting")
        pending.extend(children)
    require(len(cases) == 1, "Missing selected case")
    case = cases[0]
    require(all(type(case.get(key)) is type(value) and case[key] == value for key, value in CASE.items())
            and case.get("ok") is True, "Wrong original UI case")
    tests = case.get("tests")
    require(isinstance(tests, list) and len(tests) == 1, "Additional or missing case instances")
    test = tests[0]
    require(test.get("expectedStatus") == "passed" and test.get("status") == "expected"
            and test.get("projectId") == test.get("projectName") == "chromium"
            and test.get("annotations") == [], "Skipped, xfailed or changed case")
    results = test.get("results")
    require(isinstance(results, list) and len(results) == 1, "Missing or repeated execution")
    result = results[0]
    require(result.get("status") == "passed" and type(result.get("retry")) is int and result["retry"] == 0
            and result.get("errors") == [] and not result.get("error") and result.get("annotations") == [],
            "Original execution failed, was omitted or retried")
    attachments = result.get("attachments")
    require(isinstance(attachments, list) and len(attachments) == 1, "Missing or additional successful-case attachment")
    attachment = attachments[0]
    require(attachment.get("name") == "ui-selection-evidence" and attachment.get("contentType") == "application/json"
            and not attachment.get("path") and isinstance(attachment.get("body"), str), "Wrong UI attachment")
    attached = base64.b64decode(attachment["body"], validate=True)
    require(original_ui_bytes == attached + b"\n", "Original file and attachment bytes differ")
    require(isinstance(measurement, dict) and set(measurement) == {
        "schema", "source_sha", "dataset", "budget_ms", "p95_ms", "max_ms", "samples_ms"}, "Wrong measurement structure")
    require(measurement["schema"] == "motionwright.ui-selection.v1" and measurement["source_sha"] == context["GITHUB_SHA"],
            "Measurement source differs")
    dataset = measurement["dataset"]
    require(isinstance(dataset, dict) and set(dataset) == {"scenes", "samples", "surface"}
            and type(dataset["scenes"]) is int and dataset["scenes"] == 12
            and type(dataset["samples"]) is int and dataset["samples"] == 60 and dataset["surface"] == SURFACE
            and type(measurement["budget_ms"]) is int and measurement["budget_ms"] == 100, "Measurement contract differs")
    samples = measurement["samples_ms"]
    require(isinstance(samples, list) and len(samples) == 60
            and all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in samples),
            "Missing, nonfinite or invalid original samples")
    percentile, maximum = sorted(samples)[56], max(samples)
    require(all(type(measurement[key]) in (int, float) and math.isfinite(measurement[key])
                and measurement[key] == expected for key, expected in (("p95_ms", percentile), ("max_ms", maximum))),
            "Reported percentiles differ from the original samples")
    require(percentile < 100, "Original interactive budget was not met")
    return {"case": CASE, "result_count": 1, "scenes": 12, "samples": 60, "p95_rank_one_based": 57,
            "p95_ms": percentile, "max_ms": maximum, "budget_ms": 100, "attachment": identity(attached),
            "browser_ui_measurement_only": True}


def verify(repo, output, step_status, job_status):
    repo, output = safe_path(repo), safe_path(output)
    result = {"schema": "motionwright.ui-performance-admission/1", "state": "NOT_ADMITTED",
              "claims": CLAIMS, "test_case_results_admitted": False, "error": None}
    try:
        context = actual_context()
        result["execution_context"] = context
        receipt_raw = read(output / "receipt.json", MAX_REPORT_BYTES)
        receipt = strict_json(receipt_raw)
        result["original_receipt"] = identity(receipt_raw)
        require(step_status == job_status == "success", "The original command and prior job steps must succeed")
        require(receipt["execution_context"] == context and receipt["schema"] == "motionwright.ui-performance-command/1"
                and receipt["profile"] == PROFILE and receipt["profile_sha256"] == identity(encoded(PROFILE))["sha256"]
                and receipt["claims"] == CLAIMS, "Stale context or different scope")
        require(receipt["state"] == "COMPLETED" and type(receipt["original_exit_code"]) is int
                and receipt["original_exit_code"] == 0 and receipt["errors"] == [], "No successful original command")
        current, originals = source(repo, context)
        require(current == receipt["source_before"] == receipt["source_after"], "Source identity differs")
        provider = load_provider(originals[PROVIDER])
        paths = {name: Path(value["path"]) for name, value in receipt["runtime_before"]["executables"].items()}
        require(set(paths) == {"node", "npx", "python"} and paths["python"] == Path(sys.executable).resolve(strict=True),
                "Runtime executable set differs")
        require(runtime(provider, repo, originals[LOCK], paths) == receipt["runtime_before"] == receipt["runtime_after"],
                "Runtime inputs differ")
        require(receipt["command"] == command_for(paths["npx"], output), "Wrong command scope")
        intent_raw = read(output / "intent.json", MAX_REPORT_BYTES)
        intent = strict_json(intent_raw)
        require(identity(intent_raw) == receipt["intent"] and intent["execution_context"] == context
                and intent["source_before"] == current and intent["runtime_before"] == receipt["runtime_before"]
                and intent["command"] == receipt["command"], "Original intention differs")
        outcome = strict_json(read(output / "process-outcome.json"))
        start = strict_json(read(output / "process-start.json"))
        require(outcome["execution_context"] == start["execution_context"] == context
                and outcome["state"] == "COMPLETED" and type(outcome["original_exit_code"]) is int
                and outcome["original_exit_code"] == 0 and outcome["retry_attempted"] is False
                and type(start["pid"]) is int and start["pid"] > 0, "Missing or uncertain process journal")
        current_custody = custody(provider, output)
        require(current_custody == receipt["custody"] and current_custody["state"] == "ORIGINALS_HASHED", "Original files changed")
        report_raw = read(output / "playwright-report.json", MAX_REPORT_BYTES)
        ui_raw = read(output / "ui-selection.json", MAX_SOURCE_BYTES)
        require(identity(report_raw) == current_custody["files"]["playwright-report.json"]
                and identity(ui_raw) == current_custody["files"]["ui-selection.json"], "Report differs from original custody")
        summary = validate_reports(strict_json(report_raw), strict_json(ui_raw), ui_raw, context, repo, output, receipt["command"])
        require(Path(strict_json(report_raw)["config"]["argv"][0]).resolve(strict=True) == paths["node"], "Reported Node executable differs")
        result.update(state="SCOPED_UI_PERFORMANCE_OBSERVED", test_case_results_admitted=True, measurement=summary)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = type(error).__name__ + ": " + str(error)
    save(output / "admission.json", result)
    return 0 if result["state"] == "SCOPED_UI_PERFORMANCE_OBSERVED" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "verify"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--command-step-status", default="")
    parser.add_argument("--job-status", default="")
    args = parser.parse_args()
    require(sys.platform == "linux", "This execution profile is the Ubuntu Chromium job")
    if args.mode == "capture":
        return capture(args.repo, args.output)
    return verify(args.repo, args.output, args.command_step_status, args.job_status)


if __name__ == "__main__":
    raise SystemExit(main())
