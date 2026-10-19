#!/usr/bin/env python3
"""Source-bound orchestration for the real StudioService browser test lane.

This receipt concerns a test JSONL transport, SQLite and browser interactions.
It never certifies installed Tauri, native rendering, or human acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time


SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
LOG_LIMIT = 64 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def execution_context():
    return {key: os.environ.get(key) for key in (
        "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
        "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF")}


def require_plain_ancestors(path):
    for parent in (Path(path).absolute(), *Path(path).absolute().parents):
        if not parent.exists() and not parent.is_symlink():
            continue
        info = parent.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                f"Linked or reparse path component: {parent.name}")


def read(path, limit=256 * 1024 * 1024):
    path = Path(path)
    require_plain_ancestors(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not getattr(before, "st_file_attributes", 0) & 0x400,
            f"Non-regular or reparse input: {path.name}")
    require(before.st_size <= limit, f"Input exceeds budget: {path.name}")
    raw = path.read_bytes()
    after = path.lstat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(signature(before) == signature(after) and len(raw) == before.st_size,
            f"Input changed during read: {path.name}")
    return raw


def file_record(path):
    raw = read(path)
    return {"path": str(Path(path).absolute()), "bytes": len(raw), "sha256": digest(raw)}


def save(path, value):
    path = Path(path)
    require_plain_ancestors(path)
    raw = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        require(stream.write(raw) == len(raw), "Short receipt write; original retained")
        stream.flush()
        os.fsync(stream.fileno())
    require(read(path) == raw, "Receipt readback differs")
    return file_record(path)


def load_initial(path):
    value = json.loads(read(path))
    require(value.get("schema") == "motionwright.studio-service-gate/1"
            and value.get("status") == "SOURCE_FROZEN_NO_EXECUTION", "Invalid initial source freeze")
    require(value.get("execution_context") == execution_context(), "Receipt is from a different CI execution")
    require(SHA.fullmatch(value["product_source"]["sha"]), "Invalid frozen source")
    return value


def clean_env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS="0")
    return env


def git(repo, *args):
    result = subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                             "-C", str(repo), *args], env=clean_env(), capture_output=True, timeout=45)
    require(result.returncode == 0, f"Git read failed: {args[0]}")
    return result.stdout


def safe_relative(name):
    require(isinstance(name, str) and name and not any(c in name for c in "\\\x00\r\n:"),
            "Invalid source relative path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and not any(p in ("..", ".git") for p in path.parts)
            and path.as_posix() == name, "Unsafe source relative path")
    return path


def load_contract(path):
    raw = read(path)
    contract = json.loads(raw)
    require(contract.get("schema") == "motionwright.studio-service-selection/1", "Unknown selection schema")
    cases = contract["cases"]
    require(cases and len(cases) == contract["expected_cases"], "Selection count differs")
    keys = [(x["file"], x["title"]) for x in cases]
    require(len(set(keys)) == len(keys), "Duplicate expected browser case")
    for name, expected in contract["source_files"].items():
        safe_relative(name)
        require(DIGEST.fullmatch(expected), "Invalid expected source hash")
    for case in cases:
        safe_relative(case["file"])
        require(isinstance(case["title"], str) and case["title"], "Empty expected test title")
    for selector in contract["selectors"]:
        name, _, line = selector.partition(":")
        safe_relative(name)
        require("apps/studio/" + name in contract["source_files"], "Selector has no bound source")
        require(not line or (line.isdecimal() and int(line) > 0), "Invalid selected source location")
    return contract, file_record(path)


def source_snapshot(repo, expected_sha, contract, *, prepared=False):
    require(SHA.fullmatch(expected_sha), "Source must be an exact commit")
    repo = Path(repo).resolve(strict=True)
    actual = git(repo, "rev-parse", "HEAD").decode().strip()
    require(actual == expected_sha, "Executed checkout differs from requested source")
    raw_commit = git(repo, "cat-file", "commit", actual)
    require(hashlib.sha1(b"commit " + str(len(raw_commit)).encode() + b"\0" + raw_commit).hexdigest() == actual,
            "Raw commit identity differs")
    header = raw_commit.split(b"\n\n", 1)[0].splitlines()
    tree = [x[5:].decode() for x in header if x.startswith(b"tree ")]
    require(len(tree) == 1 and SHA.fullmatch(tree[0]), "Invalid raw commit tree")
    status = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored")
    permitted = []
    for row in status.split(b"\0"):
        if not row:
            continue
        text = row.decode()
        code, name = text[:2], text[3:]
        allowed = prepared and code in ("??", "!!") and name.startswith("apps/studio/node_modules/")
        require(allowed, f"Source checkout contains a changed, extra or ignored input: {name}")
        permitted.append(name)
    inputs = []
    for name, expected in contract["source_files"].items():
        source = git(repo, "show", actual + ":" + name)
        require(digest(source) == expected, f"Selection requires review for changed source: {name}")
        observed = read(repo.joinpath(*safe_relative(name).parts))
        if observed == source:
            representation = "EXACT_GIT_BYTES"
        elif os.name == "nt" and b"\r\n" not in source and observed == source.replace(b"\n", b"\r\n"):
            representation = "EXPLICIT_GIT_LF_TO_WORKTREE_CRLF"
        else:
            raise ValueError(f"Worktree source bytes differ: {name}")
        inputs.append({"path": name, "git_sha256": expected, "worktree_sha256": digest(observed),
                       "worktree_bytes": len(observed), "representation": representation})
    return {"sha": actual, "tree": tree[0], "raw_commit_sha256": digest(raw_commit),
            "parents": [x[7:].decode() for x in header if x.startswith(b"parent ")],
            "source_inputs": inputs, "permitted_generated_paths": permitted}


def report_cases(report):
    require(isinstance(report, dict) and isinstance(report.get("suites"), list), "Missing browser suites")
    found = []

    def visit(suite):
        for spec in suite.get("specs", []):
            filename = spec.get("file", suite.get("file", ""))
            require(isinstance(filename, str) and safe_relative(filename).name == filename,
                    "Reporter case file is outside the selected test root")
            for test in spec.get("tests", []):
                found.append({"file": filename, "title": spec["title"], "line": spec.get("line"),
                              "id": spec.get("id"), "project": test.get("projectName"),
                              "expected_status": test.get("expectedStatus"), "status": test.get("status"),
                              "results": test.get("results", []), "annotations": test.get("annotations", [])})
        for child in suite.get("suites", []):
            visit(child)

    for suite in report["suites"]:
        visit(suite)
    return found


def validate_report(report, contract, *, listing=False):
    rows = report_cases(report)
    expected = Counter((c["file"], c["title"]) for c in contract["cases"])
    observed = Counter((c["file"], c["title"]) for c in rows)
    require(observed == expected, "Browser cases missing, duplicated or outside selected real-service scope")
    require(not report.get("errors"), "Browser reporter contains collection/global errors")
    for row in rows:
        require(row["project"] == "chromium", "Unexpected browser project")
        require(row["expected_status"] == "passed", "Skipped or expected-failure case is not admitted")
        require(not any(a.get("type") in ("skip", "fixme", "fail") for a in row["annotations"]),
                "Case annotation omits or changes the positive expectation")
        if listing:
            require(not row["results"], "Listing contains execution results")
        else:
            results = row["results"]
            require(row["status"] == "expected" and len(results) == 1, "Failed, flaky, retried or absent result")
            require(results[0].get("status") == "passed" and results[0].get("retry", 0) == 0,
                    "Case did not pass its single original attempt")
            require(not results[0].get("errors") and not results[0].get("error"), "Passed result carries errors")
    if not listing:
        stats = report.get("stats", {})
        require(stats.get("expected") == len(rows) and all(stats.get(k) == 0 for k in ("unexpected", "skipped", "flaky")),
                "Browser aggregate contradicts complete original outcomes")
    return {"case_count": len(rows), "cases": [{k: v for k, v in row.items() if k != "results"} for row in rows],
            "scope": "REAL_STUDIO_SERVICE_JSONL_SQLITE", "status": "LISTED" if listing else "PASS"}


def run_command(argv, cwd, record_path, *, initial, purpose, report=None, seconds=900,
                extra_env=None, bindings=None):
    require(argv and seconds > 0, "Missing command or invalid time budget")
    frozen = load_initial(initial)
    require(purpose in ("build", "dependencies", "browser-install", "browser-list", "browser-test"),
            "Unknown execution purpose")
    require(Path(cwd).resolve() in (Path(frozen["repository"]), Path(frozen["repository"]) / "apps/studio"),
            "Execution working directory is outside the frozen product")
    executable = shutil.which(argv[0])
    require(executable is not None, "Executable unavailable")
    executable = str(Path(executable).resolve(strict=True))
    argv = [executable, *argv[1:]]
    record_path = Path(record_path)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    require(not record_path.exists(), "Execution record already exists; do not replay")
    stdout = record_path.with_suffix(".stdout.log")
    stderr = record_path.with_suffix(".stderr.log")
    env = dict(os.environ)
    env.update(extra_env or {})
    if report:
        require_plain_ancestors(report)
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        require(not Path(report).exists(), "Browser report already exists; do not replay")
        env["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(Path(report).absolute())
    freeze_record = file_record(initial)
    lock_path = Path(frozen["repository"]) / "Cargo.lock"
    lock_before = file_record(lock_path) if lock_path.exists() else None
    started = now()
    state, code, reason = "UNKNOWN", None, None
    try:
        with stdout.open("xb") as out, stderr.open("xb") as err:
            child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err)
            deadline = time.monotonic() + seconds
            while child.poll() is None:
                if time.monotonic() >= deadline or max(stdout.stat().st_size, stderr.stat().st_size) > LOG_LIMIT:
                    reason = "time_or_output_budget_exceeded"
                    child.kill()
                    child.wait(timeout=15)
                    break
                time.sleep(0.1)
            if reason is None:
                code = child.returncode
                state = "PASS" if code == 0 else "FAIL"
    except (OSError, subprocess.SubprocessError) as error:
        reason = f"process_transport_error: {error}"
    receipt = {"schema": "motionwright.studio-service-process/1", "argv": argv, "cwd": str(Path(cwd).absolute()),
               "purpose": purpose, "source_freeze": freeze_record, "execution_context": execution_context(),
               "executable": file_record(executable), "bindings": bindings or {},
               "environment": {key: env.get(key) for key in ("CARGO_TARGET_DIR", "MOTIONWRIGHT_SERVICE_TEST_BIN",
                               "MOTIONWRIGHT_SERVICE_TEST_TMPDIR", "PLAYWRIGHT_JSON_OUTPUT_NAME")},
               "dependency_lock_before": lock_before,
               "dependency_lock_after": file_record(lock_path) if lock_path.exists() else None,
               "started_at_utc": started, "finished_at_utc": now(), "process_status": state,
               "exit_code": code, "reason": reason,
               "automatic_retry": False, "child_tree_quiescence_after_budget_kill": "NOT_PROVED" if reason else "NOT_APPLICABLE"}
    for key, path in (("stdout", stdout), ("stderr", stderr), ("browser_report", report)):
        if path is None:
            continue
        try:
            receipt[key] = file_record(path)
        except (OSError, ValueError) as error:
            receipt[key + "_not_admitted"] = str(error)
            receipt["evidence_status"] = "NOT_ADMITTED"
    saved = save(record_path, receipt)
    print(json.dumps({"receipt": saved, "process_status": receipt["process_status"], "exit_code": code}))
    if code is None:
        return 124
    if code:
        return code
    return 2 if receipt.get("evidence_status") == "NOT_ADMITTED" else 0


def validate_stage(path, *, initial, purpose, cwd, expected_report=None, expected_argv=None):
    load_initial(initial)
    receipt = json.loads(read(path))
    require(receipt.get("schema") == "motionwright.studio-service-process/1", "Unexpected execution receipt schema")
    require(receipt.get("source_freeze") == file_record(initial)
            and receipt.get("execution_context") == execution_context(), "Execution/source identity differs")
    require(receipt.get("purpose") == purpose and receipt.get("cwd") == str(Path(cwd).absolute()),
            "Execution purpose or working directory differs")
    require(receipt.get("process_status") == "PASS" and type(receipt.get("exit_code")) is int
            and receipt["exit_code"] == 0 and receipt.get("automatic_retry") is False and receipt.get("reason") is None,
            "Original process did not exit successfully")
    argv = receipt.get("argv")
    require(isinstance(argv, list) and argv and all(isinstance(x, str) for x in argv), "Missing original argv")
    require(receipt.get("executable") == file_record(argv[0]), "Executed tool bytes changed")
    if expected_argv is not None:
        require(argv == expected_argv, "Executed command differs from the selected intent")
    for name in ("stdout", "stderr"):
        expected = receipt[name]
        require(file_record(expected["path"]) == expected, "Original process stream changed")
    if expected_report:
        require(receipt.get("browser_report") == file_record(expected_report), "Process/report identity differs")
    return file_record(path)


def validate_runner_record(record, initial):
    frozen = load_initial(initial)
    require(record.get("schema") == "motionwright.studio-service-runner/1"
            and record.get("source_freeze") == file_record(initial), "Runner/source identity differs")
    build_path = record["build_stage"]["path"]
    require(file_record(build_path) == record["build_stage"], "Original build receipt changed")
    validate_stage(build_path, initial=initial, purpose="build", cwd=Path(frozen["repository"]))
    stage = json.loads(read(build_path))
    require(Path(stage["argv"][0]).stem.lower() == "cargo"
            and stage["argv"][1:] == ["build", "--locked", "-p", "motionwright-service", "--example", "studio_review_session"],
            "Build command does not build the selected real service example")
    binary = record["binary"]
    lock = record["dependency_lock"]
    require(file_record(binary["path"]) == binary and file_record(lock["path"]) == lock, "Runner or dependency lock changed")
    require(stage["dependency_lock_before"] == lock and stage["dependency_lock_after"] == lock,
            "Dependency lock does not match the original locked build")
    target = stage["environment"].get("CARGO_TARGET_DIR")
    require(isinstance(target, str) and Path(target).is_absolute(), "Missing isolated Cargo target")
    expected = Path(target) / "debug/examples" / ("studio_review_session.exe" if os.name == "nt" else "studio_review_session")
    require(Path(binary["path"]) == expected, "Runner is not the selected build output")
    return record


def validate_runner(path, initial):
    return validate_runner_record(json.loads(read(path)), initial)


def browser_argv(repo, contract, output, listing):
    node = shutil.which("node")
    require(node is not None, "Node unavailable")
    node = str(Path(node).resolve(strict=True))
    cli = Path(repo) / "apps/studio/node_modules/@playwright/test/cli.js"
    read(cli)
    return [node, str(cli), "test", *contract["selectors"], "--project=chromium", "--workers=2", "--retries=0",
            "--reporter=json", "--output", str(Path(output).absolute()), *(["--list"] if listing else [])]


def validate_browser_stage(path, *, initial, contract, runner_path, report, listing):
    frozen = load_initial(initial)
    runner = validate_runner(runner_path, initial)
    stage = json.loads(read(path))
    bindings = stage["bindings"]
    require(bindings.get("runner_record") == file_record(runner_path)
            and bindings.get("runner_binary") == runner["binary"], "Browser did not bind the compiled runner")
    env = stage["environment"]
    require(env.get("MOTIONWRIGHT_SERVICE_TEST_BIN") == runner["binary"]["path"]
            and env.get("MOTIONWRIGHT_SERVICE_TEST_TMPDIR") == bindings.get("service_tmpdir")
            and env.get("PLAYWRIGHT_JSON_OUTPUT_NAME") == str(Path(report).absolute()), "Browser environment/runner differs")
    cli = Path(frozen["repository"]) / "apps/studio/node_modules/@playwright/test/cli.js"
    require(bindings.get("playwright_cli") == file_record(cli), "Playwright CLI bytes changed")
    validate_stage(path, initial=initial, purpose="browser-list" if listing else "browser-test",
                   cwd=Path(frozen["repository"]) / "apps/studio", expected_report=report,
                   expected_argv=browser_argv(frozen["repository"], contract, bindings["output_directory"], listing))
    return validate_report(json.loads(read(report)), contract, listing=listing)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ("freeze", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--repo", type=Path, required=True)
        p.add_argument("--source-sha", required=True)
        p.add_argument("--contract", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True)
        if name == "verify":
            p.add_argument("--initial", type=Path, required=True)
            p.add_argument("--runner-record", type=Path, required=True)
            p.add_argument("--report", type=Path, required=True)
            p.add_argument("--stage", type=Path, required=True)
            p.add_argument("--list-report", type=Path, required=True)
            p.add_argument("--list-stage", type=Path, required=True)
    p = sub.add_parser("run")
    p.add_argument("--initial", type=Path, required=True)
    p.add_argument("--purpose", choices=("build", "dependencies", "browser-install"), required=True)
    p.add_argument("--cwd", type=Path, required=True)
    p.add_argument("--record", type=Path, required=True)
    p.add_argument("--report", type=Path)
    p.add_argument("--seconds", type=float, default=900)
    p.add_argument("command", nargs=argparse.REMAINDER)
    p = sub.add_parser("runner")
    p.add_argument("--initial", type=Path, required=True)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--build-stage", type=Path, required=True)
    p.add_argument("--lock", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("report")
    p.add_argument("--initial", type=Path, required=True)
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--stage", type=Path, required=True)
    p.add_argument("--listing", action="store_true")
    p = sub.add_parser("browser")
    p.add_argument("--initial", type=Path, required=True)
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--runner-record", type=Path, required=True)
    p.add_argument("--record", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--service-tmpdir", type=Path, required=True)
    p.add_argument("--listing", action="store_true")
    args = parser.parse_args(argv)
    require(sys.flags.isolated and sys.dont_write_bytecode, "Invoke with python -I -B")
    if args.operation == "run":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        return run_command(command, args.cwd, args.record, initial=args.initial, purpose=args.purpose,
                           report=args.report, seconds=args.seconds)
    if args.operation == "runner":
        original = load_initial(args.initial)
        validate_stage(args.build_stage, initial=args.initial, purpose="build", cwd=Path(original["repository"]))
        raw = read(args.binary)
        require(raw[:2] == b"MZ" if os.name == "nt" else raw[:4] == b"\x7fELF", "Unexpected executable format")
        result = {"schema": "motionwright.studio-service-runner/1", "binary": file_record(args.binary),
                  "build_stage": file_record(args.build_stage), "dependency_lock": file_record(args.lock),
                  "source_freeze": file_record(args.initial),
                  "source_execution_scope": "TEST_JSONL_SERVICE_ONLY"}
        validate_runner_record(result, args.initial)
        saved = save(args.output, result)
        print(json.dumps(saved))
        return 0
    contract, contract_record = load_contract(args.contract)
    if args.operation == "browser":
        original = load_initial(args.initial)
        repo = Path(original["repository"])
        snapshot = source_snapshot(repo, original["product_source"]["sha"], contract, prepared=True)
        require(snapshot["source_inputs"] == original["product_source"]["source_inputs"]
                and original["selection_contract"] == contract_record, "Browser source or selection changed")
        runner = validate_runner(args.runner_record, args.initial)
        for destination in (args.output_dir, args.service_tmpdir):
            require(destination.is_absolute() and repo not in destination.parents, "Test output must be isolated from product sources")
            require_plain_ancestors(destination)
            destination.mkdir(parents=True, exist_ok=False)
        bindings = {"runner_record": file_record(args.runner_record), "runner_binary": runner["binary"],
                    "playwright_cli": file_record(repo / "apps/studio/node_modules/@playwright/test/cli.js"),
                    "output_directory": str(args.output_dir.absolute()), "service_tmpdir": str(args.service_tmpdir.absolute())}
        code = run_command(browser_argv(repo, contract, args.output_dir, args.listing), repo / "apps/studio",
                           args.record, initial=args.initial, purpose="browser-list" if args.listing else "browser-test",
                           report=args.report, seconds=120 if args.listing else 900, bindings=bindings,
                           extra_env={"MOTIONWRIGHT_SERVICE_TEST_BIN": runner["binary"]["path"],
                                      "MOTIONWRIGHT_SERVICE_TEST_TMPDIR": str(args.service_tmpdir.absolute())})
        if code:
            return code
        outcome = validate_browser_stage(args.record, initial=args.initial, contract=contract,
                                         runner_path=args.runner_record, report=args.report, listing=args.listing)
        print(json.dumps(outcome))
        return 0
    if args.operation == "report":
        frozen = load_initial(args.initial)
        validate_stage(args.stage, initial=args.initial, purpose="browser-list" if args.listing else "browser-test",
                       cwd=Path(frozen["repository"]) / "apps/studio", expected_report=args.report)
        print(json.dumps(validate_report(json.loads(read(args.report)), contract, listing=args.listing)))
        return 0
    result = {"schema": "motionwright.studio-service-gate/1", "created_at_utc": now(),
              "repository": str(args.repo.resolve(strict=True)), "execution_context": execution_context(),
              "product_source": source_snapshot(args.repo, args.source_sha, contract, prepared=args.operation == "verify"),
              "selection_contract": contract_record, "verifier_sha": os.environ.get("GITHUB_SHA"),
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job_key": os.environ.get("GITHUB_JOB"), "human_acceptance": False,
              "installed_tauri": False, "native_rendering": False}
    if args.operation == "freeze":
        result["status"] = "SOURCE_FROZEN_NO_EXECUTION"
    else:
        original = load_initial(args.initial)
        require(original["product_source"]["sha"] == args.source_sha
                and original["product_source"]["tree"] == result["product_source"]["tree"]
                and original["product_source"]["source_inputs"] == result["product_source"]["source_inputs"]
                and original["selection_contract"] == contract_record, "Inputs changed after initial freeze")
        runner = validate_runner(args.runner_record, args.initial)
        validate_browser_stage(args.list_stage, initial=args.initial, contract=contract, runner_path=args.runner_record,
                               report=args.list_report, listing=True)
        result.update(validate_browser_stage(args.stage, initial=args.initial, contract=contract,
                                             runner_path=args.runner_record, report=args.report, listing=False))
        result["runner"] = runner
        result["original_report"] = file_record(args.report)
        result["initial_freeze"] = file_record(args.initial)
        result["closed_store_archive_reopen"] = "SEPARATE_GATE_REQUIRED"
    print(json.dumps(save(args.output, result)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "NOT_ADMITTED", "error": str(error), "automatic_retry": False}), file=sys.stderr)
        raise SystemExit(2)
