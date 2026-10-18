"""Run isolated unittest entry points and retain source-bound, nonzero results.

Each entry point gets a fresh interpreter. This preserves unittest discovery's
import behavior instead of accidentally making a broken dotted import work.
The receipt describes these tests only; it is not native or release acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, encoding="utf-8"
    ).strip()


def source_state(root: Path, expected: str | None) -> dict:
    head = git(root, "rev-parse", "HEAD")
    if expected is not None and (
        not re.fullmatch(r"[0-9a-f]{40}", expected) or head != expected
    ):
        raise ValueError("requested source does not match the actual checkout")
    dirty = git(root, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise ValueError("tracked source has uncommitted changes")
    for options in (("--others", "--exclude-standard"),
                    ("--others", "--ignored", "--exclude-standard")):
        if git(root, "ls-files", *options):
            raise ValueError("untracked or ignored checkout inputs are not bound to the source SHA")
    return {"executed_source_sha": head, "tree_sha": git(root, "rev-parse", "HEAD^{tree}")}


def parse_unittest_summary(stderr: str) -> dict:
    # Normalize only this derived parsing view; retained stream bytes are untouched.
    stderr = stderr.replace("\r\n", "\n").replace("\r", "\n")
    runs = re.findall(r"(?m)^Ran (\d+) tests? in [^\r\n]+$", stderr)
    endings = re.findall(r"(?m)^(OK|FAILED)(?: \(([^\r\n]*)\))?$", stderr)
    if len(runs) != 1:
        raise ValueError("expected exactly one unittest result summary")
    total = int(runs[0])
    if total == 0:
        raise ValueError("zero tests is not a successful verification")
    if len(endings) != 1:
        raise ValueError("expected exactly one unittest result summary")
    state, fields = endings[0]
    counts = {"failures": 0, "errors": 0, "skipped": 0,
              "expected failures": 0, "unexpected successes": 0}
    if fields:
        seen = set()
        for part in fields.split(", "):
            key, separator, value = part.partition("=")
            if not separator or key not in counts or key in seen or not value.isdigit():
                raise ValueError("unrecognized unittest result field")
            seen.add(key)
            counts[key] = int(value)
    passed = total - sum(counts.values())
    terminal_failure = any(counts[k] for k in ("failures", "errors", "unexpected successes"))
    if passed < 0 or (state == "OK" and terminal_failure) or (state == "FAILED" and not terminal_failure):
        raise ValueError("inconsistent unittest result counts")
    return {"terminal_status": state, "tests_run": total, "passed": passed,
            **{key.replace(" ", "_"): value for key, value in counts.items()}}


def selected_files(root: Path, directory: str, patterns: list[str]) -> list[Path]:
    relative = Path(directory)
    if relative.is_absolute() or any(part in ("..", ".git") for part in relative.parts):
        raise ValueError("test directory must be a relative source directory")
    folder = (root / relative).resolve(strict=True)
    if not folder.is_relative_to(root):
        raise ValueError("test directory resolves outside the checkout")
    files: set[Path] = set()
    for pattern in patterns:
        if not pattern or any(c in pattern for c in ("/", "\\", "\x00")):
            raise ValueError("test pattern must match a filename, not a path")
        matches = [p for p in folder.iterdir()
                   if p.is_file() and p.suffix == ".py" and fnmatch.fnmatchcase(p.name, pattern)]
        if not matches:
            raise ValueError("test pattern selects no source files: " + pattern)
        for path in matches:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("test source must be a regular in-checkout file")
            relative_path = path.relative_to(root).as_posix()
            git(root, "ls-files", "--error-unmatch", "--", relative_path)
            files.add(path)
    return sorted(files)


def run_suite(root: Path, directory: str, patterns: list[str], mode: str,
              report: Path, expected_source: str | None, require_complete: bool,
              timeout: float) -> tuple[int, dict]:
    root = root.resolve(strict=True)
    report = report.resolve()
    if report.exists():
        raise ValueError("existing result receipt will not be overwritten")
    if report.is_relative_to(root):
        raise ValueError("result output must be outside the tested checkout")
    source = source_state(root, expected_source)
    files = selected_files(root, directory, patterns)
    definitions = {p.relative_to(root).as_posix(): sha256(p) for p in files}
    steps = []
    if mode in ("discovery", "both"):
        for pattern in patterns:
            steps.append(("discovery:" + pattern,
                          [sys.executable, "-B", "-E", "-s", "-m", "unittest", "discover",
                           "-s", directory, "-p", pattern, "-v"]))
    if mode in ("dotted", "both"):
        modules = [".".join(p.relative_to(root).with_suffix("").parts) for p in files]
        if any(not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", m) for m in modules):
            raise ValueError("test source has no valid dotted module name")
        for module in modules:
            steps.append(("dotted:" + module,
                          [sys.executable, "-B", "-E", "-s", "-m", "unittest", module, "-v"]))
    if not steps or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("an entry point and positive timeout are required")
    report.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"schema": "motionwright.unittest-execution/1", **source,
               "started_at_utc": datetime.now(timezone.utc).isoformat(),
               "runner_sha256": sha256(Path(__file__).resolve()),
               "python_executable_sha256": sha256(Path(sys.executable)),
               "python_environment": "-B -E -s: no bytecode writes, inherited Python overrides or user site",
               "log_representation": "Original stdout/stderr bytes; decoding and newline normalization only for summary parsing",
               "test_definition_sha256": definitions,
               "platform": platform.platform(), "python": sys.version,
               "source_locks": {p: sha256(root / p) for p in
                                ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")
                                if (root / p).is_file()},
               "github": {name: os.environ.get(name, "") for name in
                          ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID",
                           "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")},
               "require_complete": require_complete,
               "scope": "Selected unittest entry points; not installed/native/human acceptance",
               "steps": []}
    exit_code = 0
    uncertain = False
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for index, (label, command) in enumerate(steps):
        entry = {"entry_point": label, "command": command}
        if uncertain:
            entry.update(status="NOT_RUN", reason="prior test process timed out; no retry")
            receipt["steps"].append(entry)
            continue
        start = time.monotonic()
        try:
            process = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                     timeout=timeout)
            stdout, stderr, code = process.stdout, process.stderr, process.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", 124
            uncertain = True
        entry.update(exit_code=code, duration_seconds=round(time.monotonic() - start, 6))
        for stream, data in (("stdout", stdout), ("stderr", stderr)):
            log = report.with_name(report.stem + f"-{index:02d}-{stream}.log")
            with log.open("xb") as handle:
                handle.write(data)
            entry[stream] = {"file": log.name, "sha256": sha256(log)}
        summary = None
        try:
            summary = parse_unittest_summary(stderr.decode("utf-8", "replace"))
            entry["counts"] = summary
        except ValueError as exc:
            entry["summary_error"] = str(exc)
        incomplete = summary and (summary["skipped"] or summary["expected_failures"])
        failed = code != 0 or summary is None or (require_complete and incomplete)
        if summary and (summary["terminal_status"] != "OK" or summary["failures"] or
                        summary["errors"] or summary["unexpected_successes"]):
            failed = True
        entry["status"] = ("UNKNOWN" if uncertain else "FAIL" if failed else
                           "PASS_WITH_OMISSIONS" if incomplete else "PASS")
        if failed and not exit_code:
            exit_code = code if 0 < code < 256 else 1
        receipt["steps"].append(entry)
        print(json.dumps(entry, sort_keys=True), flush=True)
    try:
        after = source_state(root, source["executed_source_sha"])
        if after != source or any(sha256(root / p) != digest for p, digest in definitions.items()):
            raise ValueError("test source identity changed during execution")
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        receipt["source_postcheck_error"] = str(exc)
        exit_code = exit_code or 1
    receipt.update(completed_at_utc=datetime.now(timezone.utc).isoformat(), exit_code=exit_code,
                   status="UNKNOWN" if uncertain else "FAIL" if exit_code else
                   "PASS_WITH_OMISSIONS" if any(s["status"] == "PASS_WITH_OMISSIONS"
                                               for s in receipt["steps"]) else "PASS")
    with report.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return exit_code, receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--test-directory", default="tooling/tests")
    parser.add_argument("--pattern", action="append", required=True)
    parser.add_argument("--mode", choices=("discovery", "dotted", "both"), default="both")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    try:
        code, _ = run_suite(args.repo, args.test_directory, args.pattern, args.mode,
                            args.report, args.expected_source, args.require_complete, args.timeout)
        return code
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print("Test execution refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
