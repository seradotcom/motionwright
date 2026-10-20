#!/usr/bin/env python3
"""Run the Canvas browser contracts against the freshly built SQLite service."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

CANVAS_TITLES = (
    "structural removal denial is accessible and preserves authored hierarchy through repeated revisions and reopen",
    "an empty library reaches thirteen authored revisions while object, scene and workspace drafts survive a process restart",
    "an independent peer wins overlapping copy while disjoint geometry and locked drafts are preserved",
    "a late real transform acknowledgement preserves newer invalid raw input without replay after reload",
    "another tab restores foreign input explicitly and changed recovery bytes cannot be overwritten or discarded from an older view",
    "lost reply after real Canvas commit cannot replay the already saved operation",
    "Canvas pointer readback is required before real-service commit and explicit reload restores draft",
    "authored Canvas style persists across two real revisions and reopen without losing uncommitted typography",
    "Canvas hierarchy retains parent and raw Z through two real revisions and a service restart",
    "Canvas relation add and remove preserve stable IDs and source intent through reopen",
    "Canvas removal isolates the real children denial and permits explicit repair after reopen",
    "Canvas removal isolates the real incoming relations denial and permits explicit repair after reopen",
    "Canvas removal stays pending through selection and remount before the real commit response",
    "Canvas removal stays pending through selection and remount after the real commit response",
    "Canvas removal committed with a lost reply stays unknown until real SQLite reopen and never replays",
    "Canvas removal sends nothing when its record cannot be retained and verified",
    "Canvas removal sends nothing when its pointer cannot be retained and verified",
)
INK_TITLE = "a saved ink token changes the actual SVG text paint across revisions and reopen"
SELECTIONS = (
    ("canvas", "canvas-draft-service.spec.ts", CANVAS_TITLES),
    ("ink", "visual-language-draft-service.spec.ts", (INK_TITLE,)),
)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def validate_report(document, filename, titles):
    """Reject empty, omitted, repeated, retried or expected-failure selections."""
    require(isinstance(document, dict), "report must be an object")
    require(document.get("errors") == [], "report has missing or nonempty global errors")
    stats = document.get("stats", {})
    require(stats.get("expected") == len(titles), "selected success count differs")
    for name in ("skipped", "unexpected", "flaky"):
        require(stats.get(name) == 0, "selected " + name + " count is not zero")
    found = []

    def visit(suites):
        require(isinstance(suites, list), "suite list is missing")
        for suite in suites:
            require(isinstance(suite, dict), "suite must be an object")
            for spec in suite.get("specs", []):
                require(spec.get("file") == filename, "unexpected test file")
                require(spec.get("ok") is True, "test specification did not pass")
                title = spec.get("title")
                require(title in titles and title not in found, "unknown or duplicate test title")
                found.append(title)
                tests = spec.get("tests", [])
                require(len(tests) == 1, "each selected case must execute once")
                test = tests[0]
                require(test.get("projectId") == "chromium", "unexpected browser project")
                require(test.get("expectedStatus") == "passed", "expected failure or skip is forbidden")
                require(test.get("status") == "expected", "test is not an ordinary pass")
                results = test.get("results", [])
                require(len(results) == 1, "retry or missing original result")
                result = results[0]
                require(result.get("status") == "passed" and result.get("retry") == 0,
                        "original result must pass without retry")
                require(not result.get("error") and not result.get("errors"),
                        "original result contains an error")
            visit(suite.get("suites", []))

    visit(document.get("suites"))
    require(set(found) == set(titles) and len(found) == len(titles), "selected inventory differs")
    return {"passed": len(found), "skipped": 0, "retries": 0, "titles": found}


def digest(path):
    require(path.is_file() and not path.is_symlink(), "regular file required: " + str(path))
    with path.open("rb") as stream:
        value = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"bytes": path.stat().st_size, "sha256": value}


def write_new(path, value):
    body = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        require(stream.write(body) == len(body), "short evidence write")
        stream.flush()
        os.fsync(stream.fileno())


def source_observation(repo):
    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=repo, timeout=30).decode().strip()

    head = git("rev-parse", "HEAD")
    require(re.fullmatch("[0-9a-f]{40}", head) is not None, "invalid checkout SHA")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        require(head == os.environ.get("GITHUB_SHA"), "checkout differs from the selected CI source")
    require(git("diff", "--name-only", "HEAD", "--") == "", "tracked source has changed")
    paths = (
        ".github/workflows/ci.yml", "Cargo.lock", "apps/studio/package-lock.json",
        "crates/service/examples/studio_review_session.rs",
        "apps/studio/playwright.config.ts",
        "apps/studio/tests/support/scopedChangeService.ts",
        "apps/studio/tests/canvas-draft-service.spec.ts",
        "apps/studio/tests/visual-language-draft-service.spec.ts",
        "tooling/ci_canvas_service.py",
    )
    files = {}
    for name in paths:
        original = subprocess.check_output(["git", "show", head + ":" + name], cwd=repo, timeout=30)
        observed = digest(repo / name)
        require(observed == {"bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()},
                "source differs from Git: " + name)
        files[name] = observed
    return {"checkout": head, "tree": git("rev-parse", "HEAD^{tree}"), "files": files}


def execute(argv, cwd, env, output, label, timeout):
    stdout = output / (label + ".stdout.log")
    stderr = output / (label + ".stderr.log")
    command = {"argv": argv, "cwd": str(cwd), "started_utc": datetime.now(timezone.utc).isoformat()}
    with stdout.open("xb") as out, stderr.open("xb") as err:
        try:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=out, stderr=err,
                                    timeout=timeout, check=False)
            command["exit_code"] = result.returncode
            command["diagnostic_custody"] = "DIRECT_PROCESS_EXIT_OBSERVED"
        except subprocess.TimeoutExpired:
            command["exit_code"] = None
            command["observation"] = "TIMED_OUT_NO_RETRY"
            # subprocess.run observes and kills its direct process only. The
            # original descendants may still hold these streams. Retain this
            # bounded snapshot without claiming sealed diagnostic custody.
            command["diagnostic_custody"] = "UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED"
            command["timeout_seconds"] = timeout
    command["finished_utc"] = datetime.now(timezone.utc).isoformat()
    command["stdout"] = digest(stdout)
    command["stderr"] = digest(stderr)
    write_new(output / (label + ".command.json"), command)
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    repo, output, target = (p.resolve() for p in (args.repo, args.output, args.target))
    require(repo.is_dir(), "repository missing")
    require(not output.exists() and not target.exists(), "fresh evidence and build directories required")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "motionwright.canvas-service-ci/1", "status": "FAIL",
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "selections": [],
              "installed_tauri": "NOT_RUN", "native_render": "NOT_RUN", "human_acceptance": "NOT_RUN"}
    try:
        before = source_observation(repo)
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        cargo, node = shutil.which("cargo"), shutil.which("node")
        require(cargo is not None and node is not None, "cargo and node are required")
        env = dict(os.environ)
        env["CARGO_TARGET_DIR"] = str(target)
        build = execute([cargo, "build", "--locked", "-p", "motionwright-service",
                         "--example", "studio_review_session"], repo, env, output, "build", 1200)
        report["build"] = build
        require(build["exit_code"] == 0, "real service build failed")
        binary = target / "debug/examples/studio_review_session"
        if os.name == "nt":
            binary = binary.with_suffix(".exe")
        report["service_binary"] = {"path": str(binary), **digest(binary)}
        require(source_observation(repo) == before, "source changed during service compilation")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        report["playwright_cli"] = digest(cli)
        report["node"] = {"path": node, **digest(Path(node))}
        for label, filename, titles in SELECTIONS:
            case_env = dict(env)
            stores = output / (label + "-stores")
            stores.mkdir()
            case_env["MOTIONWRIGHT_SERVICE_TEST_BIN"] = str(binary)
            case_env["MOTIONWRIGHT_SERVICE_TEST_TMPDIR"] = str(stores)
            case_env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / (label + ".json"))
            argv = [node, str(cli), "test", "tests/" + filename, "--project=chromium",
                    "--workers=1", "--retries=0", "--reporter=json",
                    "--output=" + str(output / (label + "-test-results"))]
            if label == "ink":
                argv += ["--grep", re.escape(INK_TITLE) + "$"]
            command = execute(argv, repo / "apps/studio", case_env, output, label,
                              1800 if label == "canvas" else 180)
            selection = {"label": label, "expected_tests": len(titles), "command": command, "status": "FAIL"}
            report["selections"].append(selection)
            try:
                result_path = output / (label + ".json")
                selection["original_report"] = digest(result_path)
                require(result_path.stat().st_size <= 64 * 1024 * 1024, "report exceeds bound")
                document = json.loads(result_path.read_bytes())
                selection["verified"] = validate_report(document, filename, titles)
                require(command["exit_code"] == 0, "selected process did not exit zero")
                selection["status"] = "PASS"
            except Exception as error:
                selection["error"] = str(error)
            if command["exit_code"] is None:
                break
        report["source_after"] = source_observation(repo)
        require(report["source_after"] == before, "source changed during browser execution")
        require(digest(binary) == {k: report["service_binary"][k] for k in ("bytes", "sha256")},
                "service executable changed")
        require(digest(cli) == report["playwright_cli"], "Playwright CLI changed")
        require(len(report["selections"]) == len(SELECTIONS)
                and all(x["status"] == "PASS" for x in report["selections"]), "required selection failed")
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = str(error)
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"],
                      "selected_tests": sum(x["expected_tests"] for x in report["selections"]),
                      "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
