#!/usr/bin/env python3
"""Real, SHA-bound SQLite OS-path authority checks. No power-loss/G3 claim."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

SOURCES = {
    "crates/storage/tests/sqlite_windows_reparse_authority.rs":
        "4e9ee16caf5f80176a69e134779b35989d76ae65256b18ff34f1c866801eb685",
    "crates/storage/tests/sqlite_darwin_system_alias.rs":
        "68bbc8cb6b3aeb6d775a81400a4b140ebce1fb5e994fbb3aa8ae48fa8be1fccd",
    "crates/storage/src/lib.rs":
        "5833e7c185e8ba5aa2fa3b05ddda8dc23b20fac8ec9ea551c903f521cea7cd83",
    "Cargo.lock":
        "583f9276782966778e544ca1facd9d7a9af5a7c216e2dee4da598c00da118a84",
    "SOURCE_LOCK.json":
        "f0e42c064c71a2307b4043b8b345ff54b36f8df2692db5809ce1e85635fdc1c1",
}
CASES = {
    "windows": (
        "windows_junction_does_not_admit_another_owners_existing_sqlite",
        "windows_junction_cannot_create_sqlite_or_sidecars_in_unowned_directory",
        "windows_reparse_file_leaf_is_rejected_without_touching_target_bytes",
    ),
    "macos": (
        "built_in_var_alias_admits_real_sqlite_creation_and_reopen_with_nofollow",
        "built_in_var_alias_does_not_exempt_foreign_nested_symlink",
    ),
}
TEST_PATH = {
    "windows": "crates/storage/tests/sqlite_windows_reparse_authority.rs",
    "macos": "crates/storage/tests/sqlite_darwin_system_alias.rs",
}
SOURCE_BASE = "420864831a2dfa8626cbe1854e747ae03ef8a2db"
PARENT = "46d50a0efd979ebf5908350de6b7e3c34f461da0"
WORKFLOW = ".github/workflows/storage-sqlite-os-path-authority.yml"
SCHEMA = "motionwright.A6.sqlite-real-os-path-authority/1"


class VerificationError(ValueError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise VerificationError(message)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify_rust_result(stdout: bytes, stderr: bytes, platform: str, code: int) -> dict:
    require(platform in CASES, "invalid OS scope")
    lines = (stdout + b"\n" + stderr).decode("utf-8", "replace")
    lines = re.sub(r"\x1b\[[0-9;]*m", "", lines).replace("\r\n", "\n").replace("\r", "\n")
    matches = re.findall(r"(?m)^test ([a-zA-Z0-9_]+) \.\.\. (ok|FAILED|ignored)$", lines)
    wanted = CASES[platform]
    require(code == 0, "actual Rust process exit was not zero")
    require(len(matches) == len(wanted), "wrong number of observed Rust test cases")
    require(len(set(name for name, _ in matches)) == len(matches), "duplicate Rust case")
    require(set(matches) == {(name, "ok") for name in wanted},
            "wrong or omitted Rust tests (all must be ok)")
    summaries = re.findall(
        r"(?m)^test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; "
        r"(\d+) measured; (\d+) filtered out;", lines)
    require(len(summaries) == 1, "missing/ambiguous terminal Rust summary")
    require(tuple(int(x) for x in summaries[0]) == (len(wanted), 0, 0, 0, 0),
            "Rust terminal summary has failure, omission or filtering")
    require(not re.search(r"(?m)^test result: FAILED", lines), "failed terminal status")
    return {"status": "PASS", "observed": [name for name, _ in matches],
            "summary": list(map(int, summaries[0])), "expected": list(wanted)}


def git_query(repo: Path, *parts: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
               GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *parts],
                            capture_output=True, timeout=35, env=env)
    require(result.returncode == 0 and len(result.stdout) < 6_000_000,
            "Git source lookup failed or oversized")
    return result.stdout


def verify_checkout_history(repo: Path, sha: str) -> None:
    require(re.fullmatch("[0-9a-f]{40}", sha) is not None, "expected full Git SHA")
    require(git_query(repo, "rev-parse", "HEAD").decode().strip() == sha,
            "Git checkout SHA differs")
    parents = git_query(repo, "rev-list", "--parents", "-n", "1", "HEAD").decode().strip().split()
    require(parents == [sha, PARENT], "source ancestry changed; new review required")
    require(git_query(repo, "diff", "--name-only", SOURCE_BASE, sha).decode().splitlines() ==
            [WORKFLOW, "tooling/ci_sqlite_os_path_g2.py",
             "tooling/tests/test_ci_sqlite_os_path_g2.py"],
            "unreviewed files in candidate")
    require(not git_query(repo, "status", "--porcelain", "--untracked-files=all").strip(),
            "checkout differs from Git")


def source_identity(repo: Path, sha: str) -> dict:
    verify_checkout_history(repo, sha)
    evidence = {}
    for rel, expected in SOURCES.items():
        local = repo / rel
        require(local.is_file() and not local.is_symlink() and stat.S_ISREG(local.lstat().st_mode),
                "source path is not an ordinary file")
        entries = [x for x in git_query(repo, "ls-tree", "-z", sha, "--", rel).split(b"\0") if x]
        require(len(entries) == 1, "source Git entry missing")
        meta, sep, stored = entries[0].partition(b"\t")
        mode, kind, oid = meta.split()
        require(sep == b"\t" and stored.decode() == rel and kind == b"blob"
                and mode in (b"100644", b"100755"), "unexpected Git source metadata")
        blob = git_query(repo, "cat-file", "blob", oid.decode())
        require(local.read_bytes() == blob and digest(blob) == expected,
                "source bytes do not match reviewed Git blob")
        require(hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest() ==
                oid.decode(), "Git blob oid differs")
        evidence[rel] = {"sha256": expected, "git_blob_oid": oid.decode()}
    for platform, rel in TEST_PATH.items():
        src = (repo / rel).read_text(encoding="utf-8")
        require(src.startswith("//!") and
                ('#![cfg(windows)]' if platform == "windows"
                 else '#![cfg(target_os = "macos")]') in src,
                "OS-specific Rust source lost its platform constraint")
        tests = re.findall(r"(?m)^fn ([a-zA-Z_][a-zA-Z0-9_]*)\(\) \{", src)
        require(set(tests) == set(CASES[platform]) and len(tests) == len(CASES[platform]),
                "OS authority test names changed")
        if platform == "windows":
            require(src.count('#[ignore = ') == 3,
                    "Windows live fixtures are no longer explicit ignored-by-default tests")
        else:
            require("#[ignore" not in src, "macOS system alias tests unexpectedly ignored")
    return evidence


def check_environment(platform: str, evidence: Path, repo: Path) -> dict:
    require(platform == ("windows" if sys.platform == "win32" else
                         "macos" if sys.platform == "darwin" else ""),
            "selected OS differs from current runner")
    require(os.environ.get("GITHUB_ACTIONS") == "true", "requires actual GitHub runner")
    require(os.environ.get("RUNNER_OS") == ("Windows" if platform == "windows" else "macOS"),
            "RUNNER_OS not consistent")
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(evidence.is_absolute() and evidence.is_dir() and not evidence.is_symlink(),
            "invalid evidence directory")
    require(evidence.resolve().is_relative_to(runner) and
            not evidence.resolve().is_relative_to(repo.resolve()),
            "evidence not isolated under runner temp")
    target = Path(os.environ["CARGO_TARGET_DIR"]).resolve(strict=True)
    require(target.is_dir() and target.is_relative_to(runner) and
            not target.is_relative_to(repo.resolve()), "cargo target not isolated")
    result = {"real_runner_temp": str(runner), "real_target": str(target)}
    if platform == "windows":
        fixture = Path(os.environ["MW_SQLITE_NTFS_FIXTURE_ROOT"]).resolve(strict=True)
        require(fixture.is_dir() and fixture.is_relative_to(runner),
                "fixture is not under disposable Windows runner temp")
        for name in ("owner", "junction"):
            require((fixture / name).exists(), "NTFS junction fixture absent")
        require((fixture / "file-target.bin").is_file() and
                (fixture / "file-reparse.bin").exists(), "NTFS file symlink fixture absent")
        # The actual Rust test independently checks FILE_ATTRIBUTE_REPARSE_POINT
        # and the resolved target before attempting Store::open.
        result["fixture"] = str(fixture)
    return result


def run_selected(repo: Path, head: str, platform: str, report: Path) -> None:
    require(platform in CASES, "unsupported platform")
    require(os.environ.get("GITHUB_SHA") == head, "GHA source SHA changed")
    require(report.is_absolute() and not report.exists()
            and not report.is_symlink(), "receipt must be new and outside source")
    e = check_environment(platform, report.parent, repo)
    try:
        names = source_identity(repo, head)
    except VerificationError as err:
        rejection = {
            "schema": SCHEMA, "status": "SOURCE_PREFLIGHT_REJECTED",
            "expected_source_sha": head, "source_verified": False,
            "run_id": os.environ["GITHUB_RUN_ID"],
            "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
            "job_id": os.environ["GITHUB_JOB"], "executor_OS": platform,
            "cases_expected": list(CASES[platform]), "tests_executed": 0,
            "test_status": "NOT_RUN", "rejection": str(err),
            "runner_scope": e, "human_acceptance": False,
            "release_authorized": False,
            "at_utc": datetime.now(timezone.utc).isoformat(),
        }
        with report.open("x", encoding="utf-8") as stream:
            json.dump(rejection, stream, indent=2)
            stream.write("\n")
        raise
    cmd = ["cargo", "test", "--locked", "-p", "motionwright-storage",
           "--test", ("sqlite_windows_reparse_authority" if platform == "windows"
                      else "sqlite_darwin_system_alias"), "--"]
    if platform == "windows":
        cmd += ["--ignored"]
    cmd += ["--nocapture", "--test-threads=1"]
    env = dict(os.environ, CARGO_INCREMENTAL="0")
    result = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, timeout=540)
    stdout_file = report.with_name("original-rust.stdout.log")
    stderr_file = report.with_name("original-rust.stderr.log")
    with stdout_file.open("xb") as f:
        f.write(result.stdout)
    with stderr_file.open("xb") as f:
        f.write(result.stderr)
    status = "FAIL_OR_OMISSION"
    observation = {"error": "unexamined"}
    try:
        observation = verify_rust_result(result.stdout, result.stderr,
                                         platform, result.returncode)
        status = "PASS_EXACT_REAL_OS_G2"
    except (VerificationError, UnicodeError) as err:
        observation = {"rejection": str(err)}
    evidence = {
        "schema": SCHEMA, "source_sha": head,
        "tree_sha": git_query(repo, "rev-parse", "HEAD^{tree}").decode().strip(),
        "run_id": os.environ["GITHUB_RUN_ID"],
        "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "job_id": os.environ["GITHUB_JOB"], "executor_OS": platform,
        "reviewed_sources": names,
        "status": status, "cases_expected": list(CASES[platform]),
        "cases_observed": observation, "command": cmd, "exit_code": result.returncode,
        "stdout_sha256": digest(result.stdout), "stderr_sha256": digest(result.stderr),
        "stdout_file": stdout_file.name, "stderr_file": stderr_file.name,
        "runner_scope": e,
        "real_NTFS_fixture": platform == "windows",
        "real_macos_var_system_alias": platform == "macos",
        "real_Rust_SQLite": True,
        "power_loss_atomicity": "NOT_RUN",
        "installed_application": "NOT_RUN",
        "human_acceptance": False, "release_authorized": False,
        "at_utc": datetime.now(timezone.utc).isoformat(),
    }
    with report.open("x", encoding="utf-8") as f:
        json.dump(evidence, f, indent=2)
        f.write("\n")
    print(json.dumps({"source_sha": head, "OS": platform,
                      "status": status, "attempt": evidence["attempt"],
                      "case_count": len(CASES[platform])}), flush=True)
    require(status == "PASS_EXACT_REAL_OS_G2", "selected OS authority G2 did not pass")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--platform", choices=sorted(CASES), required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    run_selected(args.repo.resolve(strict=True), args.expected_sha,
                 args.platform, args.report)


if __name__ == "__main__":
    main()
