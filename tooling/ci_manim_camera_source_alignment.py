#!/usr/bin/env python3
"""Reject stale source pins and test expectations before native camera CI.

This is a source-only G0 check. It does not run or accept a native production
attempt, installed package, physical media, or any human-review obligation.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import textwrap
from typing import Callable

RUNNER = "tooling/native_manim_camera120_e2e.py"
PIXEL_TEST = "tooling/tests/test_native_manim_camera_pixels.py"
WORKFLOW = ".github/workflows/native-manim-camera120.yml"
G1_MARKER = "      - name: G1 unchanged original camera pixel controls in both entrypoints"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_BLOB_BYTES = 8 * 1024 * 1024


class AlignmentError(ValueError):
    """The source-only G1 contract is missing, ambiguous, or inconsistent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AlignmentError(message)


def valid_relative(value: str) -> bool:
    if not isinstance(value, str) or "\\" in value or value.startswith("-"):
        return False
    p = PurePosixPath(value)
    return bool(value) and not p.is_absolute() and ".." not in p.parts and str(p) == value


def git(repo: Path, *args: str) -> bytes:
    cmd = ["git", "--no-replace-objects", "-C", str(repo), *args]
    result = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
    if result.returncode or len(result.stdout) > MAX_BLOB_BYTES:
        raise AlignmentError("Git source lookup failed or exceeded its size budget")
    return result.stdout


def committed_blob(repo: Path, sha: str, relative: str) -> bytes:
    require(SHA40.fullmatch(sha) is not None, "full lowercase source SHA required")
    require(valid_relative(relative), "invalid tracked source path")
    identity = git(repo, "ls-tree", "-z", sha, "--", relative)
    rows = [r for r in identity.split(b"\x00") if r]
    require(len(rows) == 1, "source path missing or ambiguous")
    head, sep, path = rows[0].partition(b"\t")
    require(sep == b"\t" and path.decode("utf-8") == relative, "source path differs from Git")
    require(head.split(b" ", 2)[0:2] in
            ([b"100644", b"blob"], [b"100755", b"blob"]),
            "source must be a regular tracked blob, not a link")
    result = git(repo, "show", f"{sha}:{relative}")
    require(len(result) <= MAX_BLOB_BYTES, "source blob exceeds byte budget")
    return result


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def literal_assignment(source: bytes, name: str):
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (UnicodeError, SyntaxError) as error:
        raise AlignmentError("runner source cannot be parsed") from error
    nodes = [n.value for n in tree.body
             if isinstance(n, (ast.Assign, ast.AnnAssign))
             and ((isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
                  or (isinstance(n, ast.AnnAssign)
                      and isinstance(n.target, ast.Name) and n.target.id == name))]
    require(len(nodes) == 1, f"expected one literal {name} assignment")
    value = nodes[0]
    if isinstance(value, ast.Dict):
        try:
            keys = [ast.literal_eval(k) for k in value.keys]
        except (TypeError, ValueError, SyntaxError) as error:
            raise AlignmentError("dynamic source pin keys are forbidden") from error
        require(len(set(keys)) == len(keys), "duplicate source pin keys are forbidden")
    try:
        return ast.literal_eval(value)
    except (TypeError, ValueError, SyntaxError) as error:
        raise AlignmentError("source pin assignment must be literal") from error


def source_pin_map(runner: bytes) -> dict[str, str]:
    pins = literal_assignment(runner, "SOURCE_INPUTS")
    require(type(pins) is dict and len(pins) >= 10, "source pin table is incomplete")
    for path, pinned in pins.items():
        require(valid_relative(path) and type(pinned) is str
                and SHA256.fullmatch(pinned) is not None,
                "malformed camera source pin")
    require(PIXEL_TEST in pins and
            "tooling/native_manim_camera_pixels.py" in pins and
            "SOURCE_LOCK.json" in pins and "Cargo.lock" in pins,
            "required G1 inputs are not pinned")
    own = literal_assignment(runner, "OWN_INPUTS")
    require(type(own) is tuple and RUNNER in own and WORKFLOW in own,
            "G1 runner or workflow source is not included in identity")
    return pins


def static_case_names(test_source: bytes) -> list[str]:
    try:
        tree = ast.parse(test_source.decode("utf-8"))
    except (UnicodeError, SyntaxError) as error:
        raise AlignmentError("G1 test definition cannot be parsed") from error
    require(not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == "load_tests" for n in ast.walk(tree)),
            "dynamic unittest loading is not admitted by this static gate")
    cases = []
    for item in tree.body:
        if not isinstance(item, ast.ClassDef):
            continue
        if not any(isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                   and base.value.id == "unittest" and base.attr == "TestCase"
                   for base in item.bases):
            continue
        cases.extend(n.name for n in item.body
                     if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"))
    require(bool(cases) and len(set(cases)) == len(cases),
            "static G1 test inventory is empty or duplicated")
    return sorted(cases)


def workflow_expected_contract(workflow: bytes) -> tuple[str, int]:
    try:
        source = workflow.decode("utf-8")
    except UnicodeError as error:
        raise AlignmentError("workflow must be UTF-8") from error
    require(source.count(G1_MARKER) == 1, "one exact G1 job step is required")
    body = source.split(G1_MARKER, 1)[1].split("\n      - ", 1)[0]
    for invariant in (
        "--pattern test_native_manim_camera_pixels.py",
        "--mode both --require-complete --expected-source \"$MW_SOURCE_SHA\"",
        "value[\"executed_source_sha\"] == os.environ[\"MW_SOURCE_SHA\"]",
        "value[\"require_complete\"] is True and len(value[\"steps\"]) == 2",
        "\"discovery:test_native_manim_camera_pixels.py\"",
        "\"dotted:tooling.tests.test_native_manim_camera_pixels\"",
        "(\"errors\", \"failures\", \"skipped\", \"expected_failures\", \"unexpected_successes\")",
        "hashlib.sha256((p.parent / row[key][\"file\"]).read_bytes()).hexdigest() == row[key][\"sha256\"]",
    ):
        require(invariant in body, f"source-bound G1 guard missing: {invariant}")
    match = re.search(r"python -B -I -S - <<'PY'\n(.*?)\n          PY", body, re.DOTALL)
    require(match is not None, "G1 inline verification is missing")
    try:
        ast.parse(textwrap.dedent(match.group(1)))
    except SyntaxError as error:
        raise AlignmentError("G1 inline Python is not valid") from error
    pattern = (r'assert value\["test_definition_sha256"\] == \{"'
               + re.escape(PIXEL_TEST) + r'": "([0-9a-f]{64})"\}')
    hashes = re.findall(pattern, body)
    counts = re.findall(r'row\["counts"\]\["tests_run"\] == (\d+)', body)
    passed = re.findall(r'row\["counts"\]\["passed"\] == (\d+)', body)
    require(len(hashes) == len(counts) == len(passed) == 1,
            "G1 test source hash and exact counts must each be bound once")
    case_count = int(counts[0])
    require(case_count > 0 and int(passed[0]) == case_count,
            "G1 passed count differs from executed test count")
    return hashes[0], case_count


def required_read(read: Callable[[str], bytes], relative: str) -> bytes:
    try:
        data = read(relative)
    except (KeyError, FileNotFoundError) as error:
        raise AlignmentError("required source input is absent: " + relative) from error
    require(type(data) is bytes and len(data) <= MAX_BLOB_BYTES,
            "source input is not a bounded immutable blob: " + relative)
    return data


def assess(read: Callable[[str], bytes]) -> dict:
    runner = required_read(read, RUNNER)
    workflow = required_read(read, WORKFLOW)
    pins = source_pin_map(runner)
    originals = {}
    for path, trusted in sorted(pins.items()):
        data = required_read(read, path)
        actual = digest(data)
        require(actual == trusted, "stale source pin: " + path)
        originals[path] = {"bytes": len(data), "sha256": actual}
    test = required_read(read, PIXEL_TEST)
    expected_hash, expected_count = workflow_expected_contract(workflow)
    actual_hash = digest(test)
    require(expected_hash == pins[PIXEL_TEST] == actual_hash,
            "G1 workflow refers to an obsolete test definition")
    names = static_case_names(test)
    require(len(names) == expected_count,
            "G1 workflow test count differs from exact committed definitions")
    return {
        "status": "ALIGNED_SOURCE_ONLY",
        "source_inputs": originals,
        "G1_test_definition_sha256": actual_hash,
        "expected_test_count_per_entrypoint": expected_count,
        "static_case_names": names,
        "workflow_sha256": digest(workflow),
        "native_execution": False,
        "physical_media_readback": False,
        "human_acceptance": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        require(SHA40.fullmatch(args.expected_source) is not None,
                "full expected source SHA required")
        repo = args.repo.resolve(strict=True)
        require(repo.is_dir() and not repo.is_symlink(), "checkout must be a real directory")
        require(git(repo, "rev-parse", "HEAD").decode().strip() == args.expected_source,
                "checkout SHA differs from requested source")
        require(not git(repo, "status", "--porcelain=v1",
                        "--untracked-files=all", "--ignored=matching").strip(),
                "source checkout has modified, extra or ignored files")
        tree = git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
        require(SHA40.fullmatch(tree) is not None, "invalid source tree SHA")
        result = assess(lambda name: committed_blob(repo, args.expected_source, name))
        result.update({
            "schema": "motionwright.camera120.G0-source-alignment/1",
            "source_sha": args.expected_source,
            "tree_sha": tree,
            "at_utc": datetime.now(timezone.utc).isoformat(),
        })
        payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            target = args.output
            require(target.is_absolute() and target.parent.is_dir()
                    and not target.is_symlink() and not target.exists()
                    and not target.resolve().is_relative_to(repo),
                    "source-bound report must be new and outside tested checkout")
            with target.open("x", encoding="utf-8") as stream:
                stream.write(payload)
        else:
            print(payload, end="")
        return 0
    except (AlignmentError, OSError, subprocess.TimeoutExpired,
            UnicodeError, ValueError, TypeError) as error:
        print(f"camera source alignment rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
