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
# Constructed from the complete npm archives after independently checking their
# lockfile SHA-512 SRI. Inventory SHA-256 uses sorted {path, bytes, sha256} rows,
# compact JSON with sorted keys and ASCII escaping. No package JavaScript runs
# to establish identity. Playwright 1.63 bundles JSONReporter in lib/runner/index.js.
REVIEWED_RUNTIME_PACKAGES = {
    "@playwright/test": {
        "version": "1.63.0",
        "resolved": "https://registry.npmjs.org/@playwright/test/-/test-1.63.0.tgz",
        "integrity": "sha512-oxMK4vllB9RK5NQ2l1pq1IfOf2AvnEuj/vYGDj0H2nMtmtZpKtCwt/l00GEO6xjGfpBNAvjovvYdCm50dRQkpQ==",
        "dependencies": {"playwright": "1.63.0"},
        "member_count": 11,
        "directory_count": 0,
        "total_bytes": 28544,
        "inventory_sha256": "02b5d21ad25a04bd0158d8204e94586a7439e866d5f1c0ae2aad7c604ab08c2f",
    },
    "playwright": {
        "version": "1.63.0",
        "resolved": "https://registry.npmjs.org/playwright/-/playwright-1.63.0.tgz",
        "integrity": "sha512-+7ziBLidS4NaNCdt57SUDT+wYmmd5fmiQejUic/kb+YsYSCPyOOE9sebzMjNmQrsnNpDJqd4WHvV/8lfKfUDUg==",
        "dependencies": {"playwright-core": "1.63.0"},
        "member_count": 62,
        "directory_count": 12,
        "total_bytes": 5094512,
        "inventory_sha256": "10ff9cf8859188519296f697170a366234a09aedfd6914e4c39618fc4ac5116e",
    },
}
CONTEXT_KEYS = ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
                "GITHUB_JOB", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF")
MAX_HASH_BYTES = 256 * 1024 * 1024
MAX_RUNTIME_PACKAGE_BYTES = 8 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_record(path, *, max_bytes=MAX_HASH_BYTES):
    require(type(max_bytes) is int and 0 <= max_bytes <= MAX_HASH_BYTES, "Invalid evidence hashing budget")
    path = Path(path).absolute()
    for component in (path, *path.parents):
        info = component.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400, "Linked evidence input")
    initial = path.stat()
    require(stat.S_ISREG(initial.st_mode), "Evidence input is not a regular file")
    require(0 <= initial.st_size <= max_bytes, "Evidence input exceeds hashing budget")
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        require((initial.st_dev, initial.st_ino) == (opened.st_dev, opened.st_ino), "Input identity changed")
        # A process with UNKNOWN outcome may still append to its streams. Never
        # follow EOF indefinitely or allocate the full budget for a small input.
        while size <= initial.st_size:
            requested = min(1024 * 1024, initial.st_size + 1 - size)
            block = stream.read(requested)
            sha.update(block)
            size += len(block)
            require(size <= initial.st_size, "Evidence grew during hashing")
            if len(block) < requested:
                break
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


def package_inventory(root, expected):
    """Authenticate one closed package tree, with finite reads even after UNKNOWN."""
    root = Path(root).absolute()
    for component in (root, *root.parents):
        info = component.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400, "Linked runtime package directory")
    count, dirs, budget = (expected[key] for key in ("member_count", "directory_count", "total_bytes"))
    require(type(count) is int and 1 <= count <= 128 and type(dirs) is int and 0 <= dirs <= 64
            and type(budget) is int and 0 < budget <= MAX_RUNTIME_PACKAGE_BYTES, "Invalid runtime inventory budget")
    rows, directories, signatures, seen, pending, total = [], set(), {}, set(), [root], 0
    signature = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_mtime_ns, value.st_ctime_ns)
    while pending:
        folder = pending.pop()
        before = folder.lstat()
        require(stat.S_ISDIR(before.st_mode) and not stat.S_ISLNK(before.st_mode)
                and not getattr(before, "st_file_attributes", 0) & 0x400, "Linked runtime package directory")
        signatures[folder] = signature(before)
        with os.scandir(folder) as entries:
            for entry in entries:
                path = folder / entry.name
                relative = path.relative_to(root).as_posix()
                require(entry.name.isascii() and entry.name not in (".", "..")
                        and not any(char in entry.name for char in "\\/:\0")
                        and relative.casefold() not in seen, "Unsafe or colliding runtime member")
                seen.add(relative.casefold())
                require(len(seen) <= count + dirs, "Runtime package has additional members")
                info = path.lstat()
                require(not stat.S_ISLNK(info.st_mode)
                        and not getattr(info, "st_file_attributes", 0) & 0x400, "Linked runtime package member")
                if stat.S_ISDIR(info.st_mode):
                    directories.add(relative)
                    require(len(directories) <= dirs, "Runtime package has additional directories")
                    pending.append(path)
                else:
                    require(stat.S_ISREG(info.st_mode) and len(rows) < count,
                            "Unexpected runtime package member")
                    identity = file_record(path, max_bytes=budget - total)
                    total += identity["bytes"]
                    rows.append({"path": relative, **identity})
    for folder, original in signatures.items():
        require(signature(folder.lstat()) == original, "Runtime package directory changed during inventory")
    required_dirs = {"/".join(row["path"].split("/")[:i]) for row in rows
                     for i in range(1, len(row["path"].split("/")))}
    require(len(rows) == count and len(directories) == dirs and directories == required_dirs
            and total == budget, "Runtime package inventory is incomplete or changed")
    rows.sort(key=lambda row: row["path"])
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    require(hashlib.sha256(raw).hexdigest() == expected["inventory_sha256"],
            "Runtime package differs from its reviewed npm archive")
    return rows


def runtime_inputs(observer, repo, lock_identity):
    lock_path = repo / "apps/studio/package-lock.json"
    require(lock_identity["bytes"] <= MAX_RUNTIME_PACKAGE_BYTES, "Runtime lock exceeds parsing budget")
    with lock_path.open("rb") as stream:
        raw = stream.read(lock_identity["bytes"] + 1)
    require(len(raw) == lock_identity["bytes"]
            and hashlib.sha256(raw).hexdigest() == lock_identity["sha256"], "Runtime lock changed after hashing")
    lock = observer.strict_json(raw)
    require(isinstance(lock, dict) and type(lock.get("lockfileVersion")) is int
            and lock["lockfileVersion"] == 3 and isinstance(lock.get("packages"), dict), "Unsupported runtime lock")
    inputs = {}
    for package, expected in REVIEWED_RUNTIME_PACKAGES.items():
        entry = lock["packages"].get("node_modules/" + package)
        require(isinstance(entry, dict) and not entry.get("link")
                and all(entry.get(key) == expected[key] for key in ("version", "resolved", "integrity", "dependencies")),
                "Runtime lock is outside the reviewed package profile")
        prefix = "apps/studio/node_modules/" + package + "/"
        for row in package_inventory(repo / prefix, expected):
            inputs[prefix + row["path"]] = {"bytes": row["bytes"], "sha256": row["sha256"]}
    return inputs


def snapshot(observer, repo, context, executable_paths):
    source = observer.source(repo, context)
    inputs = {name: file_record(repo / name) for name in (SELF, *SOURCE_INPUTS)}
    inputs.update(runtime_inputs(observer, repo, inputs["apps/studio/package-lock.json"]))
    return {"source": source, "inputs": inputs,
            "runtime_profile": {"schema": "motionwright.reviewed-playwright-npm-inputs/1",
                                "packages": REVIEWED_RUNTIME_PACKAGES,
                                "installed_inventories_match_reviewed_archives": True,
                                "archive_sri_recomputed_during_execution": False,
                                "entire_dependency_graph_authenticated": False},
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
    with path.open("rb") as stream:
        raw = stream.read(identity["bytes"] + 1)
    require(len(raw) == identity["bytes"], "Report changed after hashing")
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
