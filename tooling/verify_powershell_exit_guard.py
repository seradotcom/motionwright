"""Observe the existing Windows CI failure guard with real PowerShell children.

The two controls execute only Python exit codes. They do not launch Motionwright,
build a package, install software, or establish installed application acceptance.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone


WORKFLOW = ".github/workflows/package-candidate.yml"
SELF = "tooling/verify_powershell_exit_guard.py"
GUARD = (
    "if ($LASTEXITCODE -ne 0) { throw 'Windows unit contracts failed or were "
    "incomplete; NSIS build is blocked' }"
)
CONTEXT_KEYS = (
    "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF",
    "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
)
INPUTS = (
    WORKFLOW, SELF, "tooling/ci_test_runner.py",
    "tooling/tests/test_windows_cold_start.py", "tooling/windows_cold_start.py",
    "tooling/windows_first_project_ui.ps1",
)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def file_record(path, limit=32 * 1024 * 1024):
    path = Path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink()
            and not getattr(before, "st_file_attributes", 0) & 0x400,
            "Expected a regular, unlinked file: " + str(path))
    require(before.st_size <= limit, "File exceeds the custody bound")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(before.st_size + 1)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    require(identity(before) == identity(opened) == identity(after)
            == identity(path.stat()) and len(raw) == before.st_size,
            "File changed during the observation")
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}, raw


def write_new(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return file_record(path)[0]


def git(root, *args):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_NO_LAZY_FETCH="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0",
               GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor",
               GIT_CONFIG_VALUE_0="false")
    p = subprocess.run(["git", "--no-replace-objects", "-C", str(root), *args],
                       env=env, capture_output=True, timeout=30)
    require(p.returncode == 0, "Original Git source read failed")
    return p.stdout


def source_snapshot(root, expected):
    require(git(root, "rev-parse", "HEAD").decode().strip() == expected,
            "Checkout differs from the requested source")
    require(not git(root, "status", "--porcelain", "--untracked-files=no"),
            "Tracked source is dirty")
    commit = git(root, "cat-file", "commit", expected)
    require(hashlib.sha1(b"commit " + str(len(commit)).encode() + b"\0" + commit).hexdigest()
            == expected, "Commit identity differs")
    tree = commit.split(b"\n", 1)[0].removeprefix(b"tree ").decode()
    require(re.fullmatch("[0-9a-f]{40}", tree) is not None, "Invalid original tree")
    records = {}
    for relative in INPUTS:
        record, raw = file_record(root / relative)
        original = git(root, "show", expected + ":" + relative)
        require(raw == original, "Working source bytes differ: " + relative)
        records[relative] = record
    return {"source_sha": expected, "source_tree": tree, "inputs": records}


def execute(out, name, argv, env):
    intent = {"schema": "motionwright.powershell-exit-control-command/1",
              "argv": argv, "started_utc": now(), "state": "NOT_COMPLETED",
              "retry_attempted": False}
    write_new(out / (name + "-intent.json"), intent)
    process, code, error = None, None, None
    try:
        with (out / (name + "-stdout.log")).open("xb") as stdout, \
                (out / (name + "-stderr.log")).open("xb") as stderr:
            process = subprocess.Popen(argv, env=env, stdout=stdout, stderr=stderr)
            code = process.wait(timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        error = type(exc).__name__ + ": " + str(exc)
    state = "COMPLETED" if type(code) is int else ("UNKNOWN" if process else "NOT_STARTED")
    result = {"schema": intent["schema"], "argv": argv, "completed_utc": now(),
              "state": state, "exit_code": code, "error": error,
              "pid": process.pid if process else None, "retry_attempted": False}
    write_new(out / (name + "-outcome.json"), result)
    if state == "COMPLETED":
        result["streams"] = {
            stream: {"file": name + "-" + stream + ".log",
                     **file_record(out / (name + "-" + stream + ".log"))[0]}
            for stream in ("stdout", "stderr")
        }
        write_new(out / (name + "-custody.json"), result)
    return result


def observe(root, out, expected, powershell, ownership):
    require(sys.platform == "win32" and sys.flags.isolated and sys.dont_write_bytecode,
            "Use the real Windows Python interpreter with -B -I")
    require(re.fullmatch("[0-9a-f]{40}", expected) is not None, "Missing exact source SHA")
    require(not out.is_relative_to(root), "Evidence must be outside the checked-out source")
    require(powershell.name.lower() in ("powershell.exe", "pwsh.exe"),
            "Expected an explicitly selected PowerShell executable")
    source = source_snapshot(root, expected)
    text = (root / WORKFLOW).read_bytes().decode("utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip() == GUARD]
    require(lines == [GUARD], "Expected exactly one original Windows unit failure guard")
    context = {name: os.environ.get(name, "") for name in CONTEXT_KEYS}
    if context["GITHUB_RUN_ID"]:
        require(context["GITHUB_SHA"] == context["GITHUB_WORKFLOW_SHA"] == expected
                and context["GITHUB_JOB"] == "windows-contracts"
                and context["GITHUB_REPOSITORY"] == "seradotcom/motionwright"
                and context["GITHUB_RUN_ID"].isdigit()
                and context["GITHUB_RUN_ATTEMPT"].isdigit()
                and context["GITHUB_WORKFLOW_REF"].startswith(
                    "seradotcom/motionwright/" + WORKFLOW + "@"),
                "Unexpected GitHub execution identity")
    runtime = {
        "python": {"path": sys.executable, **file_record(Path(sys.executable))[0],
                   "version": sys.version},
        "powershell": {"path": str(powershell), **file_record(powershell)[0]},
    }
    out.mkdir(parents=True, exist_ok=False)
    ownership["created"] = True
    write_new(out / "intent.json", {
        "schema": "motionwright.powershell-exit-guard/1", "started_utc": now(),
        "source": source, "github": context, "runtime": runtime,
        "guard": GUARD, "guard_sha256": hashlib.sha256(GUARD.encode()).hexdigest(),
        "control_exit_codes": [0, 23],
        "scope": "Real PowerShell executes the original guard; Python exits are synthetic.",
        "native_automatic_error_preference": False, "installed_application": False,
        "retry_attempted": False,
    })
    script = (
        "param([string] $Interpreter, [int] $Code, [string] $ExitPath, [string] $ReachedPath, [string] $ErrorPath)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$PSNativeCommandUseErrorActionPreference = $false\n"
        "& $Interpreter -B -I -S -c \"import sys; sys.exit($Code)\"\n"
        "[System.IO.File]::WriteAllText($ExitPath, [string] $LASTEXITCODE)\n"
        + "try {\n    " + GUARD + "\n"
        "} catch {\n"
        "    $observedError = $_\n"
        "    $guardRecord = @{\n"
        "        exception_message = $observedError.Exception.Message\n"
        "        exception_type = $observedError.Exception.GetType().FullName\n"
        "        source_line = $observedError.InvocationInfo.Line\n"
        "        script_line_number = $observedError.InvocationInfo.ScriptLineNumber\n"
        "        script_path = $observedError.InvocationInfo.ScriptName\n"
        "        fully_qualified_error_id = $observedError.FullyQualifiedErrorId\n"
        "        last_exit_code = $LASTEXITCODE\n"
        "    }\n"
        "    [System.IO.File]::WriteAllText($ErrorPath, ($guardRecord | ConvertTo-Json -Compress))\n"
        "    throw\n"
        "}\n"
        "[System.IO.File]::WriteAllText($ReachedPath, 'GUARD_COMPLETED')\n"
    )
    script_path = out / "original-guard-control.ps1"
    with script_path.open("xb") as stream:
        stream.write(script.encode())
    script_identity = file_record(script_path)[0]
    guard_line = next(index for index, line in enumerate(script.splitlines(), 1)
                      if line.strip() == GUARD)
    env = dict(os.environ)
    env.update(TEMP=str(out), TMP=str(out), PYTHONDONTWRITEBYTECODE="1")
    version = execute(out, "powershell-version", [
        str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
        "$PSVersionTable.PSVersion.ToString()"], env)
    require(version["state"] == "COMPLETED" and version["exit_code"] == 0,
            "PowerShell version could not be observed; no controls attempted")
    observations = []
    for expected_exit in (0, 23):
        name = "exit-" + str(expected_exit)
        exit_file, reached_file = out / (name + ".txt"), out / (name + "-reached.txt")
        error_file, error_evidence = out / (name + "-error-record.json"), None
        result = execute(out, name, [
            str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(script_path), str(Path(sys.executable)),
            str(expected_exit), str(exit_file), str(reached_file), str(error_file)], env)
        require(result["state"] == "COMPLETED",
                "Control outcome is uncertain or not started; no next control or retry")
        require(exit_file.is_file(), "The original native exit code was not retained")
        native_exit = int(file_record(exit_file)[1].decode("utf-8").strip())
        require(native_exit == expected_exit, "Actual Python exit differs")
        if expected_exit == 0:
            require(result["exit_code"] == 0 and reached_file.read_bytes() == b"GUARD_COMPLETED"
                    and not error_file.exists(),
                    "The positive control did not reach the post-guard marker")
        else:
            require(type(result["exit_code"]) is int and result["exit_code"] != 0
                    and not reached_file.exists(),
                    "PowerShell masked the failing child or continued past the guard")
            error_identity, error_raw = file_record(error_file)
            error_record = json.loads(error_raw)
            require(error_record["exception_message"]
                    == GUARD.split("throw '", 1)[1].removesuffix("' }")
                    and error_record["source_line"].strip() == GUARD
                    and type(error_record["script_line_number"]) is int
                    and error_record["script_line_number"] == guard_line
                    and Path(error_record["script_path"]).resolve(strict=True) == script_path
                    and type(error_record["last_exit_code"]) is int
                    and error_record["last_exit_code"] == expected_exit,
                    "The original PowerShell ErrorRecord does not identify the exact guard failure")
            error_evidence = {"file": error_file.name, **error_identity, "observed": error_record}
        observations.append({
            "child_exit_code": native_exit, "powershell_exit_code": result["exit_code"],
            "post_guard_marker_reached": reached_file.exists(),
            "outcome": file_record(out / (name + "-outcome.json"))[0],
            "native_exit_original": file_record(exit_file)[0],
            "original_error_record": error_evidence,
            "streams": result["streams"],
        })
    require(file_record(script_path)[0] == script_identity, "Original guard script changed")
    require(source_snapshot(root, expected) == source, "Source changed during controls")
    require(file_record(powershell)[0] == {k: runtime["powershell"][k] for k in ("bytes", "sha256")}
            and file_record(Path(sys.executable))[0]
            == {k: runtime["python"][k] for k in ("bytes", "sha256")},
            "Runtime changed during controls")
    result = {
        "schema": "motionwright.powershell-exit-guard/1",
        "state": "OBSERVED_POSITIVE_AND_NEGATIVE_POWERSHELL_GUARD",
        "completed_utc": now(), "source": source, "github": context, "runtime": runtime,
        "original_guard": GUARD, "script": script_identity,
        "guard_line_one_based": guard_line,
        "failure_attribution": "Original PowerShell ErrorRecord; stderr presentation is not parsed",
        "controls": observations, "control_count": 2,
        "negative_control_child_exit_preserved": 23,
        "negative_control_powershell_failure_observed": True,
        "native_automatic_error_preference": False,
        "source_and_runtime_unchanged": True, "retry_attempted": False,
        "installed_application": False, "native_renderer": False,
        "unknown_native_operation_replayed": False,
        "formal_acceptance": False, "release_verdict": False,
        "scope": "Two controlled Python exits through the exact workflow guard. "
                 "No full Windows job failure, installer, app UI or recovery claim.",
    }
    write_new(out / "observation.json", result)
    print(json.dumps({"state": result["state"], "source_sha": expected, "controls": observations}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--powershell", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.repo.resolve(strict=True), args.output.resolve()
    ownership = {"created": False}
    try:
        observe(root, output, args.expected_source, args.powershell.resolve(strict=True), ownership)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        failure = {"schema": "motionwright.powershell-exit-guard/1",
                   "state": "NOT_ADMITTED", "completed_utc": now(),
                   "source_sha_requested": args.expected_source,
                   "error": type(error).__name__ + ": " + str(error),
                   "retry_attempted": False, "formal_acceptance": False}
        if ownership["created"] and not (output / "not-admitted.json").exists():
            write_new(output / "not-admitted.json", failure)
        print(json.dumps(failure), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
