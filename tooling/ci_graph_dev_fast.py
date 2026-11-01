#!/usr/bin/env python3
"""Fast, source-only Graph diagnosis; never substitutes an R74/G2 native case.

Run locally with an explicit comparison base.  Only source and inert fixture
contracts run.  Actual graph-source-review requires a separately authorized,
same-run R74 producer/reviewer and the real Native Host/Store workflow.
"""
from __future__ import annotations

import argparse
import ast
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


SCHEMA = "motionwright.graph-fast-development/1"
SDK_ROLES = ("semwright", "semwrightd", "semwright_sandbox")
MANDATORY_PYTHON = (
    "test_graph_owner_phase_literal_from_nested_playwright_aggregate_only",
    "test_graph_owner_phase_nested_malformed_or_excess_depth_fails_closed",
    "test_graph_owner_phase_nested_global_error_quota_fails_closed",
    "test_graph_owner_phase_diagnostic_denies_unknown_and_suffix_codes",
)
OPTIONAL_PYTHON = (
    "test_graph_owner_runtime_stage_is_closed_source_only_and_never_leaks_private_details",
    "test_graph_runtime_stage_labels_match_owner_contract_and_exact_source_markers",
)
NODE_PORTABLE_PATTERN = (
    "^(reviewed source shape never grants a Native callback|"
    "bounded Owner runtime stage preserves a closed diagnostic without private data|"
    "caller approval, cap override or replay cannot amend a source plan)$"
)
UI_SPECS = (
    "src/semanticGraph.test.ts",
    "src/semanticGraphRequestIdentity.test.ts",
    "src/semanticGraphRecovery.test.ts",
)
SOURCE_FILE = "tooling/native_graph_browser_owner.py"
PRODUCER_FILE = "tooling/ci_canvas_service.py"
OWNER_NODE_TEST = "apps/studio/tooling/graph-owner/producer_guard.test.mjs"


class GraphFastError(RuntimeError):
    """A closed preflight reason, not an underlying owner error or token."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise GraphFastError(code)


def git(repo: Path, *args: str, timeout: int = 20) -> bytes:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"}
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, env=env, timeout=timeout,
        check=False,
    )
    require(completed.returncode == 0, "GRAPH_DEV_GIT_SOURCE_UNAVAILABLE")
    return completed.stdout.strip()


def current_source(repo: Path, base: str, expected: str = "") -> dict:
    repo = repo.resolve(strict=True)
    top = Path(os.fsdecode(git(repo, "rev-parse", "--show-toplevel"))).resolve(strict=True)
    require(top == repo, "GRAPH_DEV_REPO_ROOT_MISMATCH")
    head = git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii")
    tree = git(repo, "rev-parse", "HEAD^{tree}").decode("ascii")
    require(re.fullmatch(r"[0-9a-f]{40}", head) is not None, "GRAPH_DEV_HEAD_INVALID")
    require(not expected or head == expected, "GRAPH_DEV_EXPECTED_SOURCE_MISMATCH")
    base_sha = git(repo, "rev-parse", "--verify", base + "^{commit}").decode("ascii")
    git(repo, "merge-base", "--is-ancestor", base_sha, head)
    dirty = bool(git(repo, "status", "--porcelain=v1", "--untracked-files=no"))
    return {"git_head": head, "git_tree": tree, "base_commit": base_sha,
            "tracked_clean": not dirty,
            "source_kind": "EXACT_HEAD" if not dirty else "WORKTREE_WIP_NOT_HEAD_ATTESTATION"}


def _paths(raw: bytes) -> set[str]:
    result = set()
    for item in raw.split(b"\0"):
        if not item:
            continue
        path = item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        chunks = path.split("/")
        require(bool(path) and not path.startswith("/") and ".." not in chunks
                and not any(chunk == "" for chunk in chunks),
                "GRAPH_DEV_UNSAFE_CHANGED_PATH")
        result.add(path)
    return result


def changed_files(repo: Path, base_sha: str) -> list[str]:
    changed = _paths(git(repo, "diff", "--name-only", "--diff-filter=ACMRT", "-z",
                         base_sha, "HEAD"))
    changed |= _paths(git(repo, "diff", "--name-only", "--diff-filter=ACMRT", "-z", "HEAD"))
    changed |= _paths(git(repo, "ls-files", "--others", "--exclude-standard", "-z"))
    require(len(changed) <= 20000, "GRAPH_DEV_CHANGED_SCOPE_TOO_LARGE")
    return sorted(changed)


def impacted_packages(paths: list[str]) -> list[str]:
    """Include the direct consumer closure, not every workspace package."""
    changed = set(paths)
    foundation = any(p.startswith(("Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json",
                                    "rust-toolchain.toml")) for p in changed)
    domain = any(p.startswith("crates/domain/") for p in changed)
    storage = any(p.startswith("crates/storage/") for p in changed)
    service = any(p.startswith("crates/service/") for p in changed)
    native = any(p.startswith("crates/native/") for p in changed)
    if foundation:
        return ["motionwright-domain", "motionwright-storage",
                "motionwright-service", "motionwright-native"]
    if domain or storage:
        return (["motionwright-domain"] if domain else []) + (
            ["motionwright-storage"] if storage else []) + [
                "motionwright-service", "motionwright-native"]
    if service:
        return ["motionwright-service", "motionwright-native"]
    if native:
        return ["motionwright-native"]
    return []


def impacted_ui(paths: list[str]) -> bool:
    return any(p.startswith("apps/studio/src/") or
               p.startswith("apps/studio/tests/semantic-graph-") for p in paths)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == name]
    require(len(matches) == 1, "GRAPH_DEV_REQUIRED_FUNCTION_ABSENT")
    return matches[0]


def probe_capture_roster_source(owner: bytes, producer: bytes) -> dict:
    """Execute only the reviewed inert equality predicate in restricted globals.

    No subprocess, native owner, SDK executable or filesystem receipt is opened
    by the predicate.  The synthetic test roots and fake hashes are not grants.
    """
    owner_tree = ast.parse(owner.decode("utf-8"))
    producer_tree = ast.parse(producer.decode("utf-8"))
    capture = _function(producer_tree, "graph_capture_cargo_executable")
    values = {n.value for n in ast.walk(capture)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    require("mw-r74-captured" in values, "GRAPH_DEV_PRODUCER_CAPTURE_ROOT_DRIFT")
    role_copy = any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
                    and isinstance(n.left, ast.Name) and n.left.id == "directory"
                    and isinstance(n.right, ast.Name) and n.right.id == "role"
                    for n in ast.walk(capture))
    require(role_copy, "GRAPH_DEV_PRODUCER_ROLE_CAPTURE_DRIFT")
    predicate = _function(owner_tree, "verify_graph_sdk_capture_roster")
    loading = _function(owner_tree, "load_shared")
    invokes = [n for n in ast.walk(loading) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Name)
               and n.func.id == predicate.name]
    require(len(invokes) == 1, "GRAPH_DEV_OWNER_CAPTURE_JOIN_MISSING")
    require(not any(isinstance(n, ast.Attribute) and n.attr == "SW_BINS"
                    for n in ast.walk(loading)), "GRAPH_DEV_MUTABLE_SDK_ALIAS_PRESENT")

    forbidden = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal,
                 ast.AsyncFunctionDef, ast.Yield, ast.YieldFrom, ast.Await,
                 ast.Try, ast.With, ast.AsyncWith, ast.Lambda)
    require(not any(isinstance(n, forbidden) for n in ast.walk(predicate)),
            "GRAPH_DEV_PREDICATE_NOT_INERT")
    for node in ast.walk(predicate):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                require(node.func.id in {"require", "type", "Path", "set"},
                        "GRAPH_DEV_PREDICATE_CALL_UNREVIEWED")
            elif isinstance(node.func, ast.Attribute):
                require(node.func.attr in {"get", "fullmatch"},
                        "GRAPH_DEV_PREDICATE_METHOD_UNREVIEWED")
            else:
                raise GraphFastError("GRAPH_DEV_PREDICATE_DYNAMIC_CALL")

    builtins = {"type": type, "dict": dict, "str": str, "int": int, "set": set}
    namespace = {
        "__builtins__": builtins, "Path": Path,
        "DIGEST": re.compile(r"[0-9a-f]{64}\Z"), "require": require,
    }
    expression = ast.fix_missing_locations(ast.Module(body=[predicate], type_ignores=[]))
    exec(compile(expression, "<inert-sdk-capture-predicate>", "exec"), namespace)
    verify = namespace[predicate.name]
    sdk = Path("/motionwright-fast-dev-fixture/sdk")
    source = {"sdk_root": str(sdk), "sha": "1" * 40}
    captured = {
        role: {"path": str(sdk / "target" / "mw-r74-captured" / role),
               "sha256": chr(97 + i) * 64, "size_bytes": 1000 + i}
        for i, role in enumerate(SDK_ROLES)
    }
    spec = {"source": source, "binaries": captured}
    producer_index = copy.deepcopy(spec)
    require(verify(copy.deepcopy(spec), copy.deepcopy(producer_index)) is True,
            "GRAPH_DEV_CAPTURE_VALID_INERT_REJECTED")
    negatives = []

    def deny(name: str, mutate) -> None:
        left, right = copy.deepcopy(spec), copy.deepcopy(producer_index)
        mutate(left, right)
        try:
            verify(left, right)
        except GraphFastError:
            negatives.append(name)
        else:
            raise GraphFastError("GRAPH_DEV_CAPTURE_NEGATIVE_ACCEPTED_" + name)

    def change_both(role: str, **fields):
        def apply(left, right):
            for specimen in (left, right):
                specimen["binaries"][role].update(fields)
        return apply

    deny("mutable_debug_alias", change_both(
        "semwright_sandbox", path=str(sdk / "target" / "debug" / "semwright-sandbox")))
    deny("foreign_role_path", change_both(
        "semwright_sandbox", path=str(sdk / "target" / "mw-r74-captured" / "semwrightd")))
    deny("swapped_producer_digest", lambda a, b: b["binaries"]["semwrightd"].update(sha256="f" * 64))
    deny("wrong_sdk_source", lambda a, b: b["source"].update(sdk_root="/foreign-sdk"))
    deny("unknown_role_field", lambda a, b: a["binaries"]["semwright"].update(injected="unsupported"))
    deny("zero_binary_bytes", change_both("semwright", size_bytes=0))
    deny("wrong_captured_root", change_both("semwright", path="/different/semwright"))
    deny("source_sha_drift", lambda a, b: b["source"].update(sha="2" * 40))
    require(len(negatives) == 8, "GRAPH_DEV_NEGATIVE_SET_INCOMPLETE")
    return {"status": "PASS_INERT_ONLY", "positive": 1, "negative": len(negatives),
            "negative_names": negatives, "first_case": "graph-source-review SOURCE_INTAKE ONLY",
            "actual_native_processes": 0, "R74_technical_admissions": 0}


def selected_python_cases(repo: Path) -> tuple[list[str], list[str]]:
    path = repo / "tooling/tests/test_ci_canvas_service.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    classes = [n for n in module.body if isinstance(n, ast.ClassDef)
               and n.name == "GraphR74JoinTests"]
    require(len(classes) == 1, "GRAPH_DEV_R74_TEST_CLASS_MISSING")
    available = {n.name for n in classes[0].body if isinstance(n, ast.FunctionDef)}
    require(set(MANDATORY_PYTHON) <= available, "GRAPH_DEV_R74_CORE_TEST_MISSING")
    selected = [*MANDATORY_PYTHON, *(name for name in OPTIONAL_PYTHON if name in available)]
    dotted = ["tooling.tests.test_ci_canvas_service.GraphR74JoinTests." + name
              for name in selected]
    return dotted, [name for name in OPTIONAL_PYTHON if name not in available]


def _run(repo: Path, key: str, argv: list[str], timeout: int) -> dict:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
           "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"}
    start = time.monotonic()
    try:
        completed = subprocess.run(argv, cwd=repo, env=env, capture_output=True,
                                   timeout=timeout, check=False)
        code = completed.returncode
        output = completed.stdout
        error = completed.stderr
        status = "PASS" if code == 0 else "FAIL"
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        code = None
        output = b""
        error = b""
        status = "TIMEOUT" if isinstance(exc, subprocess.TimeoutExpired) else "TOOL_UNAVAILABLE"
    return {"check": key, "status": status, "exit_code": code,
            "elapsed_ms": int((time.monotonic() - start) * 1000),
            "stdout_sha256": hashlib.sha256(output).hexdigest(),
            "stderr_sha256": hashlib.sha256(error).hexdigest(),
            "stdout_bytes": len(output), "stderr_bytes": len(error),
            "sensitive_output_exposed": False}


def _check_node(repo: Path, timeout: int) -> list[dict]:
    return [
        _run(repo, "graph_owner_js_syntax",
             ["node", "--check", "apps/studio/tests/support/graph-owner/factory.mjs"], timeout),
        _run(repo, "graph_owner_error_contract_syntax",
             ["node", "--check", "apps/studio/tests/support/graph-owner/contracts.mjs"], timeout),
        _run(repo, "graph_owner_inert_selected",
             ["node", "--test", "--test-name-pattern=" + NODE_PORTABLE_PATTERN,
              OWNER_NODE_TEST], timeout),
    ]


def _check_ui(repo: Path, timeout: int) -> dict:
    app = repo / "apps/studio"
    vitest = app / "node_modules/vitest/vitest.mjs"
    if not vitest.is_file():
        return {"check": "graph_ui_changed", "status": "HOLD_NO_PREINSTALLED_VITEST",
                "exit_code": None, "elapsed_ms": 0}
    result = _run(app, "graph_ui_changed",
                  ["node", str(vitest), "run", *UI_SPECS], timeout)
    return result


def _check_cargo(repo: Path, packages: list[str], target: Path | None,
                 timeout: int) -> dict:
    if target is None:
        return {"check": "graph_rust_impact", "status": "HOLD_REQUIRE_EXISTING_CARGO_TARGET",
                "packages": packages, "exit_code": None, "elapsed_ms": 0}
    try:
        resolved = target.resolve(strict=True)
    except OSError:
        resolved = None
    if (resolved is None or not resolved.is_dir() or target.is_symlink()
            or (getattr(target.stat(), "st_file_attributes", 0) & 0x400)):
        return {"check": "graph_rust_impact", "status": "HOLD_UNSAFE_OR_MISSING_CARGO_TARGET",
                "packages": packages, "exit_code": None, "elapsed_ms": 0}
    env = {**os.environ, "CARGO_TARGET_DIR": str(resolved),
           "CARGO_INCREMENTAL": "0", "GIT_OPTIONAL_LOCKS": "0",
           "CARGO_NET_OFFLINE": "true"}
    argv = ["cargo", "check", "--locked", "--offline"]
    for pkg in packages:
        argv.extend(["-p", pkg])
    start = time.monotonic()
    try:
        p = subprocess.run(argv, cwd=repo, env=env, capture_output=True,
                           timeout=timeout, check=False)
        return {"check": "graph_rust_impact", "status": "PASS" if p.returncode == 0 else "FAIL",
                "packages": packages, "exit_code": p.returncode,
                "elapsed_ms": int((time.monotonic() - start) * 1000),
                "stdout_sha256": hashlib.sha256(p.stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(p.stderr).hexdigest(),
                "existing_target_reused": True}
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return {"check": "graph_rust_impact",
                "status": "TIMEOUT" if isinstance(error, subprocess.TimeoutExpired)
                else "TOOL_UNAVAILABLE",
                "packages": packages, "exit_code": None,
                "elapsed_ms": int((time.monotonic() - start) * 1000),
                "existing_target_reused": True}


def execute(repo: Path, base: str, mode: str, *, expected_source: str = "",
            cargo_target: Path | None = None, timeout: int = 60) -> dict:
    source = current_source(repo, base, expected=expected_source)
    paths = changed_files(repo, source["base_commit"])
    pkgs = impacted_packages(paths)
    ui = impacted_ui(paths)
    report = {
        "schema": SCHEMA,
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "selection": mode, "source": source,
        "impact": {"changed_paths": paths, "rust_consumer_packages": pkgs,
                   "studio_ui_impacted": ui},
        "first_case": {"id": "graph-source-review",
                       "dev_scope": "INERT_SOURCE_INTAKE_ONLY",
                       "real_service_and_store": "NOT_EXECUTED",
                       "actual_native_graph": "NOT_EXECUTED"},
        "checks": [],
        "first_failure": None,
        "R74_review": "NOT_EXECUTED",
        "Native_Host_Store_G2": "NOT_EXECUTED",
        "Graph6_cases_accepted": 0,
        "F": "NOT_SELECTED", "G3": "STOP",
        "formal_acceptance": False,
    }
    if mode == "plan":
        report["status"] = "PLAN_ONLY_NO_COMMANDS"
        return report
    try:
        capture = probe_capture_roster_source(
            (repo / SOURCE_FILE).read_bytes(), (repo / PRODUCER_FILE).read_bytes())
        report["first_case"]["inert_capture_roster"] = capture
    except (GraphFastError, UnicodeError, SyntaxError, OSError, ValueError) as error:
        report["status"] = "FAIL_FIRST_CASE_SOURCE_INTAKE"
        report["first_failure"] = {
            "check": "graph-source-review SOURCE_INTAKE inert producer/owner join",
            "code": error.args[0] if isinstance(error, GraphFastError)
            else "GRAPH_DEV_SOURCE_PRECHECK_UNAVAILABLE",
        }
        return report

    tasks = _check_node(repo, timeout)
    for result in tasks:
        report["checks"].append(result)
        if result["status"] != "PASS":
            report["status"] = "FAIL_SELECTED_SOURCE_CHECK"
            report["first_failure"] = {"check": result["check"], "code": result["status"]}
            return report

    names, absent = selected_python_cases(repo)
    report["impact"]["optional_python_checks_not_present"] = absent
    result = _run(repo, "graph_python_r74_source_subset",
                  [sys.executable, "-B", "-m", "unittest", *names, "-q"], timeout)
    result["selected_test_names"] = [n.rsplit(".", 1)[-1] for n in names]
    report["checks"].append(result)
    if result["status"] != "PASS":
        report["status"] = "FAIL_SELECTED_SOURCE_CHECK"
        report["first_failure"] = {"check": result["check"], "code": result["status"]}
        return report

    if mode == "impact" and ui:
        result = _check_ui(repo, timeout)
        report["checks"].append(result)
        if result["status"] != "PASS":
            report["status"] = "HOLD_IMPACTED_STUDIO_CHECK"
            report["first_failure"] = {"check": result["check"], "code": result["status"]}
            return report
    if mode == "impact" and pkgs:
        result = _check_cargo(repo, pkgs, cargo_target, timeout)
        report["checks"].append(result)
        if result["status"] != "PASS":
            report["status"] = "HOLD_OR_FAIL_IMPACTED_RUST_CHECK"
            report["first_failure"] = {"check": result["check"], "code": result["status"]}
            return report

    report["status"] = "PASS_SOURCE_ONLY" if source["tracked_clean"] else "PASS_DEV_WIP_SOURCE_ONLY"
    report["impact"]["rust_G1"] = ("PASS_EXISTING_TARGET_REUSED" if mode == "impact" and pkgs
                                    else "NOT_REQUIRED" if not pkgs
                                    else "NOT_EXECUTED_FAST_MODE")
    report["impact"]["studio_UI_G1"] = ("PASS_SELECTED" if mode == "impact" and ui
                                         else "NOT_REQUIRED" if not ui
                                         else "NOT_EXECUTED_FAST_MODE")
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--base", default="HEAD^", help="local ancestor commit, never fetched implicitly")
    p.add_argument("--expected-source", default="", help="optional immutable current HEAD guard")
    p.add_argument("--mode", choices=("plan", "fast", "impact"), default="fast")
    p.add_argument("--cargo-target-dir", type=Path,
                   help="pre-existing shared Cargo target (never created here)")
    p.add_argument("--timeout-seconds", type=int, default=60)
    p.add_argument("--report", type=Path, help="optional exclusive JSON receipt outside tracked source")
    args = p.parse_args(argv)
    require(5 <= args.timeout_seconds <= 180, "GRAPH_DEV_TIMEOUT_RANGE")
    repo = args.repo.resolve(strict=True)
    try:
        result = execute(repo, args.base, args.mode, expected_source=args.expected_source,
                         cargo_target=args.cargo_target_dir, timeout=args.timeout_seconds)
    except (GraphFastError, subprocess.TimeoutExpired, OSError, SyntaxError, ValueError) as error:
        result = {"schema": SCHEMA, "selection": args.mode,
                  "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "status": "FAIL_CLOSED_PRECHECK",
                  "first_failure": {"code": (str(error) if isinstance(error, GraphFastError)
                                            else "GRAPH_DEV_PRECHECK_UNAVAILABLE")},
                  "R74_review": "NOT_EXECUTED", "Native_Host_Store_G2": "NOT_EXECUTED",
                  "Graph6_cases_accepted": 0, "formal_acceptance": False,
                  "F": "NOT_SELECTED", "G3": "STOP"}
    if args.report is not None:
        target = args.report.resolve(strict=False)
        if target.is_relative_to(repo):
            require(target.relative_to(repo).parts[0] in ("knowledge",),
                    "GRAPH_DEV_RECEIPT_MUST_BE_PRIVATE_OR_OUTSIDE_SOURCE")
        require(target.parent.is_dir(), "GRAPH_DEV_RECEIPT_PARENT_MISSING")
        with target.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(result, output, sort_keys=True, indent=2)
            output.write("\n")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] in (
        "PLAN_ONLY_NO_COMMANDS", "PASS_SOURCE_ONLY", "PASS_DEV_WIP_SOURCE_ONLY") else 1


if __name__ == "__main__":
    raise SystemExit(main())