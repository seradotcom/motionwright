#!/usr/bin/env python3
"""Retain one ordinary CI browser command without interpreting test acceptance.

The original exit, streams and JSON report remain distinct from custody errors.
An intent without a completed process record is UNKNOWN, never permission to retry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import types
from datetime import datetime, timezone

SELF = "tooling/ci_browser_evidence.py"
PLANNER = "tooling/ci_job_plan.py"
SOURCE_INPUTS = ("apps/studio/package.json", "apps/studio/package-lock.json",
                 "apps/studio/playwright.config.ts")
RUNTIME_INPUTS = ("apps/studio/node_modules/@playwright/test/package.json",
                  "apps/studio/node_modules/@playwright/test/cli.js",
                  "apps/studio/node_modules/playwright/package.json",
                  "apps/studio/node_modules/playwright/lib/reporters/json.js")
CONTEXT_KEYS = ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
                "GITHUB_JOB", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF")


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_record(path):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        info = component.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400, "Linked evidence input")
    initial = path.stat()
    require(stat.S_ISREG(initial.st_mode), "Evidence input is not a regular file")
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        require((initial.st_dev, initial.st_ino) == (opened.st_dev, opened.st_ino), "Input identity changed")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
            size += len(block)
        final = os.fstat(stream.fileno())
    after = path.stat()
    signature = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    require(signature(initial) == signature(opened) == signature(final) == signature(after)
            and size == initial.st_size, "Evidence changed during hashing")
    return {"bytes": size, "sha256": sha.hexdigest()}


def source_module(repo):
    """Load the existing source observer only after checking both original Git blobs."""
    sha = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Missing actual checkout SHA")
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    environment.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
                       GIT_NO_LAZY_FETCH="1", GIT_CONFIG_NOSYSTEM="1",
                       GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                             "-C", str(repo), "cat-file", "--batch"],
                            input=f"{sha}:{SELF}\n{sha}:{PLANNER}\n".encode(),
                            capture_output=True, env=environment, timeout=45)
    require(result.returncode == 0, "Original source read failed")
    remaining, blobs = result.stdout, {}
    for name in (SELF, PLANNER):
        header, remaining = remaining.split(b"\n", 1)
        oid, kind, length = header.split()
        size = int(length)
        require(kind == b"blob" and 0 <= size <= 1_000_000, "Invalid original tool blob")
        raw, remaining = remaining[:size], remaining[size:]
        require(len(raw) == size and remaining.startswith(b"\n"), "Truncated original tool blob")
        remaining = remaining[1:]
        require(hashlib.sha1(b"blob " + str(size).encode() + b"\0" + raw).hexdigest() == oid.decode(),
                "Original Git blob digest differs")
        identity = file_record(repo / name)
        actual = (repo / name).read_bytes()
        require(hashlib.sha256(actual).hexdigest() == identity["sha256"], "Tool changed after hashing")
        require(actual == raw or (os.name == "nt" and b"\r\n" not in raw
                                 and actual == raw.replace(b"\n", b"\r\n")), "Executed tool differs from Git source")
        if name == SELF:
            executed = file_record(Path(__file__))
            require(executed == identity, "Executing custody tool differs from the checkout tool")
        blobs[name] = raw
    require(not remaining, "Unexpected original source response")
    module = types.ModuleType("_ci_browser_original_source_observer")
    module.__file__ = str(repo / PLANNER)
    exec(compile(blobs[PLANNER], module.__file__, "exec"), module.__dict__)
    return module


def snapshot(observer, repo, context, executable_paths):
    return {"source": observer.source(repo, context),
            "inputs": {name: file_record(repo / name) for name in (SELF, *SOURCE_INPUTS, *RUNTIME_INPUTS)},
            "executables": {name: {"resolved_path": str(Path(path).resolve(strict=True)),
                                    **file_record(Path(path).resolve(strict=True))}
                            for name, path in executable_paths.items()}}


def retained_report(observer, path):
    if not path.exists():
        return {"state": "ABSENT", "test_case_results_admitted": False}
    identity = file_record(path)
    if identity["bytes"] > 64 * 1024 * 1024:
        return {"state": "ORIGINAL_JSON_NOT_PARSED", **identity,
                "error": "Report exceeds parsing budget; original retained", "test_case_results_admitted": False}
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == identity["sha256"], "Report changed after hashing")
    try:
        value = observer.strict_json(raw)
        require(isinstance(value, dict) and isinstance(value.get("suites"), list) and isinstance(value.get("stats"), dict)
                and isinstance(value.get("config"), dict) and isinstance(value.get("errors"), list),
                "Incomplete Playwright report structure")
    except (ValueError, KeyError, TypeError) as error:
        return {"state": "ORIGINAL_JSON_INVALID", **identity, "error": f"{type(error).__name__}: {error}",
                "test_case_results_admitted": False}
    # This is original data retention. No status, retry, xfail or skip is promoted or forbidden here.
    return {"state": "ORIGINAL_JSON_RETAINED", **identity,
            "stats_as_reported": value["stats"], "test_case_results_admitted": False}


def capture(repo, output, plan_id, command, executable_paths):
    repo, output = Path(repo).resolve(strict=True), Path(output).absolute()
    require(repo not in (output, *output.parents) and ".." not in output.parts, "Evidence must be outside checkout")
    for parent in output.parents:
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                    and not getattr(info, "st_file_attributes", 0) & 0x400, "Linked output ancestor")
    output.mkdir(parents=True, exist_ok=False)  # Existing/uncertain operations cannot be replayed.
    started, process, code, observer = now(), None, None, None
    context = {key: os.environ.get(key) for key in CONTEXT_KEYS}
    before, after, process_error, custody_errors, report = None, None, None, [], None
    report_path = output / "playwright-report.json"
    environment = dict(os.environ, PLAYWRIGHT_JSON_OUTPUT_FILE=str(report_path))
    try:
        observer = source_module(repo)
        context = {**observer.context(), "GITHUB_JOB": os.environ.get("GITHUB_JOB")}
        require(context["GITHUB_JOB"] == "browser-acceptance", "Unexpected evidence job")
        require(re.fullmatch(r"[0-9a-f]{64}", plan_id or ""), "Missing original plan identity")
        before = snapshot(observer, repo, context, executable_paths)
        observer.write_new(output / "intent.json", {
            "schema": "motionwright.ci-browser-command-intent/1", "started_at": started,
            "state": "NOT_COMPLETED; UNKNOWN_UNTIL_PROCESS_OUTCOME_EXISTS",
            "execution_context": context, "plan_id": plan_id, "source_before": before,
            "command": command, "cwd": str(repo / "apps/studio"),
            "reporter_override_only": "dot,json", "report_path": str(report_path),
            "retry_attempted": False, "test_case_results_admitted": False,
        }, repo)
        with (output / "stdout.log").open("xb") as stdout, (output / "stderr.log").open("xb") as stderr:
            process = subprocess.Popen(command, cwd=repo / "apps/studio", env=environment,
                                       stdout=stdout, stderr=stderr)
            observer.write_new(output / "process-started.json", {
                "schema": "motionwright.ci-browser-process-started/1", "pid": process.pid,
                "observed_at": now(), "execution_context": context, "plan_id": plan_id,
                "command": command, "retry_attempted": False,
            }, repo)
            code = process.wait()  # Preserve the existing workflow timeout/retry policy.
            stdout.flush()
            stderr.flush()
            os.fsync(stdout.fileno())
            os.fsync(stderr.fileno())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        process_error = f"{type(error).__name__}: {error}"
    process_state = "COMPLETED" if type(code) is int else ("UNKNOWN" if process is not None else "NOT_STARTED")
    outcome = {"schema": "motionwright.ci-browser-original-process/1", "state": process_state,
               "execution_context": context, "plan_id": plan_id, "command": command,
               "started_at": started, "observed_at": now(), "original_exit_code": code,
               "error": process_error, "retry_attempted": False, "test_case_results_admitted": False}
    # Keep the original exit before any later source/report/stream validation can fail.
    raw = (json.dumps(outcome, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    with (output / "process-outcome.json").open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    streams = {}
    if type(code) is int and process_error is not None:
        custody_errors.append(f"process_completion_custody: {process_error}")
    for name in ("stdout.log", "stderr.log"):
        try:
            streams[name] = file_record(output / name)
        except (OSError, ValueError) as error:
            custody_errors.append(f"{name}: {type(error).__name__}: {error}")
    if observer is not None and before is not None:
        try:
            after = snapshot(observer, repo, context, executable_paths)
            require(before == after, "Source, runner or inputs changed during original command")
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            custody_errors.append(f"source_postcheck: {type(error).__name__}: {error}")
        try:
            report = retained_report(observer, report_path)
            if report["state"] in ("ORIGINAL_JSON_INVALID", "ORIGINAL_JSON_NOT_PARSED"):
                custody_errors.append(f"report: {report['error']}")
            if type(code) is int and code == 0:
                require(report["state"] == "ORIGINAL_JSON_RETAINED", "Successful command lacks original JSON report")
        except (OSError, ValueError, KeyError, TypeError) as error:
            custody_errors.append(f"report: {type(error).__name__}: {error}")
    status = ("ORIGINAL_EXECUTION_FAILED" if type(code) is int and code != 0 else
              "ORIGINAL_COMMAND_COMPLETED" if type(code) is int and code == 0 and not custody_errors else
              "CUSTODY_FAILED" if type(code) is int and code == 0 else process_state)
    receipt = {"schema": "motionwright.ci-browser-original-evidence/1", "state": status,
               "execution_context": context, "plan_id": plan_id, "process_outcome": file_record(output / "process-outcome.json"),
               "original_exit_code": code, "source_before": before, "source_after": after,
               "source_and_inputs_unchanged": before is not None and before == after,
               "streams": streams, "report": report, "custody_errors": custody_errors,
               "retry_attempted": False, "test_case_results_admitted": False,
               "same_run_reuse_eligible": False, "formal_acceptance": False,
               "scope": "Original ordinary CI browser command retention; all reported omissions and attempts preserved."}
    raw = (json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    with (output / "receipt.json").open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({"state": status, "original_exit_code": code, "receipt": file_record(output / "receipt.json"),
                      "stats_as_reported": report.get("stats_as_reported") if report else None,
                      "custody_errors": custody_errors, "test_case_results_admitted": False}, sort_keys=True))
    if type(code) is int and code != 0:
        return code if code > 0 else 128 - code
    return 0 if type(code) is int and code == 0 and not custody_errors else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan-id", required=True)
    args = parser.parse_args()
    require(sys.flags.isolated and sys.dont_write_bytecode, "Use python -I -B")
    npm, node = shutil.which("npm"), shutil.which("node")
    require(npm and node, "Existing Node/npm installation is required")
    return capture(args.repo, args.output, args.plan_id,
                   [npm, "run", "test:e2e", "--", "--reporter=dot,json"],
                   {"npm": npm, "node": node, "python": sys.executable,
                    "playwright_cli": args.repo / "apps/studio/node_modules/.bin/playwright"})


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"state": "NOT_ADMITTED", "error": f"{type(error).__name__}: {error}",
                          "test_case_results_admitted": False}), file=sys.stderr)
        raise SystemExit(2)
