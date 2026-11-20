"""Read-only, enum-only projection of one failed FixedTxt prepare-10 stderr.

This never executes an original command or echoes raw stderr. It is selected
AFTER the original owner has failed and returned, not in place of the owner.
All claimed phase/error words are exact literals from the checked-out original
G2 custody module. Missing or untrusted observations remain UNAVAILABLE.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

MAX_STDERR = 1024 * 1024
SAFE_CLASS = frozenset({
    "CustodyRejected", "TimeoutExpired", "CalledProcessError",
    "HTTPError", "URLError", "ConnectionError", "FileNotFoundError",
    "PermissionError", "OSError", "ValueError", "KeyError",
    "JSONDecodeError", "RuntimeError", "OTHER",
})
PATTERN = re.compile(
    rb"MLT G2 custody gate failed; no automatic retry\. "
    rb"(\{[^\r\n]{1,2048}\})\Z"
)
SHA = re.compile(r"[0-9a-f]{40}\Z")
RUN = re.compile(r"[1-9][0-9]*\Z")


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def literal_frozenset(source, label):
    """Accept only static source-defined frozenset({literal,...}) values."""
    tree = ast.parse(source)
    matches = [
        stmt.value for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == label
    ]
    need(len(matches) == 1, "source_enum_roster")
    node = matches[0]
    need(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
         and node.func.id == "frozenset"
         and len(node.args) == 1 and not node.keywords
         and isinstance(node.args[0], ast.Set), "source_enum_not_literal")
    try:
        values = ast.literal_eval(node.args[0])
    except (ValueError, TypeError, SyntaxError, MemoryError) as exc:
        raise ValueError("source_enum_values_invalid") from exc
    need(type(values) is set and 1 <= len(values) <= 160
         and all(type(v) is str
                 and re.fullmatch(r"[A-Za-z0-9_-]{3,110}", v)
                 for v in values), "source_enum_values_invalid")
    return frozenset(values)


def safe_record(stderr, phases, codes):
    """Only parse the exact final failure line and the closed five-key object."""
    need(type(stderr) is bytes and 0 < len(stderr) <= MAX_STDERR,
         "not_bounded_original_stderr")
    need(stderr.endswith(b"\n") and b"\x00" not in stderr,
         "original_stderr_noncanonical")
    rows = stderr.removesuffix(b"\n").split(b"\n")
    need(1 <= len(rows) <= 10000, "stderr_line_budget")
    raw_line = rows[-1].removesuffix(b"\r")
    match = PATTERN.fullmatch(raw_line)
    need(match is not None
         and sum(b"MLT G2 custody gate failed; no automatic retry."
                 in row for row in rows) == 1,
         "no_unique_final_safe_diagnostic")
    def pairs(entries):
        d = {}
        for key, value in entries:
            need(key not in d, "duplicate_safe_projection_field")
            d[key] = value
        return d
    try:
        value = json.loads(match.group(1), object_pairs_hook=pairs,
                           parse_constant=lambda _: need(False, "unsafe_constant"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("safe_projection_not_json") from exc
    need(type(value) is dict
         and set(value) == {
             "phase", "error_class", "error_code",
             "automatic_retry", "originals_disclosed"}
         and type(value["phase"]) is str
         and value["phase"] in phases
         and type(value["error_class"]) is str
         and value["error_class"] in SAFE_CLASS
         and type(value["error_code"]) is str
         and (value["error_code"] in codes
              or value["error_code"] == "WITHHELD")
         and value["automatic_retry"] is False
         and value["originals_disclosed"] is False,
         "safe_projection_enum_or_contract_invalid")
    return {
        "status": "SOURCE_BOUND_SAFE_PREPARE_FAILURE",
        "phase": value["phase"],
        "error_class": value["error_class"],
        "error_code": value["error_code"],
        "native_outcome": "NOT_INFERRED",
        "automatic_retry": False,
        "originals_disclosed": False,
    }


def original_stderr(path, *, parent, uid):
    parent = Path(parent)
    path = Path(path)
    need(parent.is_absolute() and parent.resolve(strict=True) == parent
         and path.parent.resolve(strict=True) == path.parent
         and path.parent.is_relative_to(parent), "stderr_original_parent")
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
         and info.st_uid == uid and stat.S_IMODE(info.st_mode) & 0o022 == 0
         and 0 < info.st_size <= MAX_STDERR,
         "stderr_original_not_regular")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need((before.st_ino, before.st_dev, before.st_size) ==
             (info.st_ino, info.st_dev, info.st_size),
             "stderr_original_open_changed")
        raw = os.read(fd, MAX_STDERR + 1)
        after = os.fstat(fd)
        need(len(raw) == info.st_size
             and (after.st_ino, after.st_dev, after.st_size) ==
             (info.st_ino, info.st_dev, info.st_size)
             and path.lstat().st_size == info.st_size,
             "stderr_original_read_changed")
        return raw
    finally:
        os.close(fd)


def main():
    need(sys.argv[1:] == ["--failed-prepare-ten"],
         "closed_read_only_projection_action")
    env = os.environ
    need(sys.platform == "linux"
         and all(env.get(key) == value for key, value in {
             "CI": "true", "GITHUB_ACTIONS": "true",
             "GITHUB_EVENT_NAME": "workflow_dispatch",
             "GITHUB_REPOSITORY": "seradotcom/motionwright",
             "GITHUB_RUN_ATTEMPT": "1",
             "GITHUB_JOB": "fixedtxt-original-owner",
             "MW_MLT_SCOPE": "fixedtxt-v4-one-fixture",
         }.items())
         and SHA.fullmatch(env.get("GITHUB_SHA", "")) is not None
         and RUN.fullmatch(env.get("GITHUB_RUN_ID", "")) is not None,
         "wrong_original_G2_job")
    repo = Path(__file__).resolve().parents[1]
    need(Path(env["GITHUB_WORKSPACE"]).resolve(strict=True) == repo,
         "wrong_original_source_checkout")
    # Validate source bytes against the exact checked-out HEAD, not an
    # environment-supplied list of safe codes or a dynamically imported module.
    module = repo / "tooling/native_mlt_g2_custody.py"
    raw = module.read_bytes()
    need(len(raw) <= 2 * 1024 * 1024, "source_policy_file_budget")
    git_env = {
        "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
    }
    cmd = ["/usr/bin/git", "-C", str(repo),
           "-c", "safe.directory=" + str(repo),
           "show", env["GITHUB_SHA"] + ":tooling/native_mlt_g2_custody.py"]
    result = subprocess.run(cmd, env=git_env, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=12,
                            check=False)
    need(result.returncode == 0 and result.stdout == raw,
         "original_custody_source_bytes_changed")
    phases = literal_frozenset(raw, "_PREPARE_PHASES")
    codes = literal_frozenset(raw, "_PREPARE_SAFE_CODES")
    base = Path(env["RUNNER_TEMP"]).resolve(strict=True)
    need(str(base) == "/home/runner/work/_temp",
         "unexpected_runner_temp")
    run = env["GITHUB_RUN_ID"]
    stderr = (base / ("mw-fixedtxt-g2-control-" + run + "-1")
              / "preparation/prepare-10.stderr")
    try:
        original = original_stderr(stderr, parent=base, uid=os.getuid())
        record = safe_record(original, phases, codes)
    except (OSError, ValueError):
        record = {
            "status": "SAFE_PREPARE_FAILURE_UNAVAILABLE",
            "phase": "NOT_OBSERVED", "error_class": "NOT_OBSERVED",
            "error_code": "WITHHELD", "native_outcome": "NOT_INFERRED",
            "automatic_retry": False, "originals_disclosed": False,
        }
    print("G2_PREPARE_10_SAFE_DIAGNOSTIC " +
          json.dumps(record, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
