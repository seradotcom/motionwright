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
import selectors
import signal
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
    terminal_failure = any(counts[k] for k in ("failures", "errors", "unexpected successes"))
    if (state == "OK" and terminal_failure) or (state == "FAILED" and not terminal_failure):
        raise ValueError("inconsistent unittest result counts")
    # FAILED counts may include several subtest outcomes for one test case.
    # The footer preserves those counts but cannot determine successful cases.
    passed = None
    if state == "OK":
        passed = total - counts["skipped"] - counts["expected failures"]
        if passed < 0:
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


INSTALLED_STREAM_BYTES = 1024 * 1024
INSTALLED_PIPE_BYTES = 65536
INSTALLED_OVERFLOW_BYTES = 2 * INSTALLED_PIPE_BYTES
INSTALLED_RECEIPT_BYTES = 1024 * 1024


def capture_installed_entry(command, *, root, env, report, index, timeout):
    """Opt-in pipe capture for the installed suite; never limit child files.

    The caller reserves both streams, both overflow originals and the receipt
    before this selection. Only these four parent-written log files are capped.
    No RLIMIT_FSIZE reaches unittest, its 4 MiB inert build fixture or Cargo.
    """
    import fcntl
    paths = {name: report.with_name(report.stem + f"-{index:02d}-{name}.log")
             for name in ("stdout", "stderr", "stdout-overflow", "stderr-overflow")}
    retained = {name: 0 for name in paths}
    observed = {name: 0 for name in ("stdout", "stderr")}
    detail = {"status": "UNKNOWN", "stream_limit_bytes": INSTALLED_STREAM_BYTES,
              "overflow_limit_per_stream_bytes": INSTALLED_OVERFLOW_BYTES,
              "eof": {name: False for name in observed}, "overflow": False,
              "retention_exhausted": False, "deadline_exceeded": False,
              "child_file_size_limit_changed": False}
    files, writes, selector = {}, [], selectors.DefaultSelector()
    process = None
    def retain(name, data):
        observed[name] += len(data)
        normal = min(len(data), max(0, INSTALLED_STREAM_BYTES - retained[name]))
        if normal:
            files[name].write(data[:normal]); retained[name] += normal
        remainder = data[normal:]
        if remainder:
            detail["overflow"] = True
            target = name + "-overflow"
            if retained[target] + len(remainder) > INSTALLED_OVERFLOW_BYTES:
                detail["retention_exhausted"] = True
                return False
            files[target].write(remainder); retained[target] += len(remainder)
        return True
    try:
        for name, path in paths.items():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            files[name] = os.fdopen(fd, "wb")
        for name in observed:
            reader, writer = os.pipe2(os.O_CLOEXEC)
            writes.append(writer)
            try:
                if fcntl.fcntl(reader, fcntl.F_GETPIPE_SZ) > INSTALLED_PIPE_BYTES:
                    raise ValueError("installed capture pipe exceeds its prior overflow reservation")
                os.set_blocking(reader, False)
                selector.register(reader, selectors.EVENT_READ, name)
            except BaseException:
                os.close(reader)
                raise
        until = time.monotonic() + timeout
        process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                   stdout=writes[0], stderr=writes[1], close_fds=True,
                                   start_new_session=True)
        for writer in writes:
            os.close(writer)
        writes.clear()
        try:
            while selector.get_map():
                if time.monotonic() >= until:
                    detail["deadline_exceeded"] = True
                    break
                for key, _ in selector.select(min(0.05, max(0, until - time.monotonic()))):
                    data = os.read(key.fd, INSTALLED_PIPE_BYTES)
                    if not data:
                        detail["eof"][key.data] = True
                        selector.unregister(key.fd); os.close(key.fd)
                    else:
                        retain(key.data, data)
                    if detail["overflow"] or detail["retention_exhausted"]:
                        break
                if detail["overflow"] or detail["retention_exhausted"]:
                    break
            if not detail["overflow"] and not detail["deadline_exceeded"]:
                try:
                    process.wait(timeout=max(0.001, until - time.monotonic()))
                except subprocess.TimeoutExpired:
                    detail["deadline_exceeded"] = True
        finally:
            # This child has a distinct session created above. The surrounding
            # installed producer independently closes its complete descendant
            # tree; this local group kill is not evidence of that closure.
            if process.poll() is None or not all(detail["eof"].values()):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                detail["deadline_exceeded"] = True
        drain_until = time.monotonic() + 1
        while selector.get_map() and time.monotonic() < drain_until:
            for key, _ in selector.select(min(0.05, max(0, drain_until - time.monotonic()))):
                data = os.read(key.fd, INSTALLED_PIPE_BYTES)
                if not data:
                    detail["eof"][key.data] = True
                    selector.unregister(key.fd); os.close(key.fd)
                elif not retain(key.data, data):
                    selector.unregister(key.fd); os.close(key.fd)
        for output in files.values():
            output.flush(); os.fsync(output.fileno())
        detail.update(observed_stream_bytes=observed, retained_stream_bytes=retained,
                      overflow_originals={name: {"file": paths[name].name, "sha256": sha256(paths[name])}
                                          for name in ("stdout-overflow", "stderr-overflow")})
        if (process.poll() is not None and all(detail["eof"].values()) and not detail["overflow"]
                and not detail["retention_exhausted"] and not detail["deadline_exceeded"]):
            detail["status"] = "COMPLETE"
        return (paths["stdout"].read_bytes(), paths["stderr"].read_bytes(),
                process.returncode if process.poll() is not None else 124, detail)
    finally:
        # Retention errors must not leave the explicitly created child session
        # running while this reader unwinds. The outer owner still determines
        # complete descendant closure independently.
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        for writer in writes:
            os.close(writer)
        for key in list(selector.get_map().values()):
            os.close(key.fd)
        selector.close()
        for output in files.values():
            try:
                output.flush(); os.fsync(output.fileno())
            finally:
                output.close()


def run_suite(root: Path, directory: str, patterns: list[str], mode: str,
              report: Path, expected_source: str | None, require_complete: bool,
              timeout: float, *, bounded_installed_capture: bool = False) -> tuple[int, dict]:
    root = root.resolve(strict=True)
    report = report.resolve()
    if report.exists():
        raise ValueError("existing result receipt will not be overwritten")
    if report.is_relative_to(root):
        raise ValueError("result output must be outside the tested checkout")
    if bounded_installed_capture and not (
            sys.platform == "linux" and directory == "tooling/tests"
            and patterns == ["test_native_host_installed_shared_state.py"]
            and mode == "discovery" and require_complete
            and math.isfinite(timeout) and 0 < timeout <= 60):
        raise ValueError("bounded installed capture requires its one complete source selection")
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
            if bounded_installed_capture:
                stdout, stderr, code, captured = capture_installed_entry(
                    command, root=root, env=env, report=report, index=index, timeout=timeout)
                entry["bounded_capture"] = captured
                uncertain = captured["status"] != "COMPLETE"
            else:
                process = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                         timeout=timeout)
                stdout, stderr, code = process.stdout, process.stderr, process.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", 124
            uncertain = True
        entry.update(exit_code=code, duration_seconds=round(time.monotonic() - start, 6))
        for stream, data in (("stdout", stdout), ("stderr", stderr)):
            log = report.with_name(report.stem + f"-{index:02d}-{stream}.log")
            if not bounded_installed_capture:
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
        failed = uncertain or code != 0 or summary is None or (require_complete and incomplete)
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
    if bounded_installed_capture:
        encoded = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
        if len(encoded) > INSTALLED_RECEIPT_BYTES:
            raise ValueError("installed receipt exceeds its prior reservation; original streams retained")
        fd = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
    else:
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
    parser.add_argument("--bounded-installed-capture", action="store_true")
    args = parser.parse_args(argv)
    try:
        code, _ = run_suite(args.repo, args.test_directory, args.pattern, args.mode,
                            args.report, args.expected_source, args.require_complete, args.timeout,
                            bounded_installed_capture=args.bounded_installed_capture)
        return code
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print("Test execution refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
