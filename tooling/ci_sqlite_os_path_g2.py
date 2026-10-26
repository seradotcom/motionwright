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
import signal
import stat
import subprocess
import sys

# Immutable historical experiment. The reviewed successor does not rewrite its
# storage/lock pins or claim execution of its old source.
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
PARENT = "420864831a2dfa8626cbe1854e747ae03ef8a2db"
WORKFLOW = ".github/workflows/storage-sqlite-os-path-authority.yml"
SCHEMA = "motionwright.A6.sqlite-real-os-path-authority/1"
REVIEWED_RECEIPT_SCHEMA = "motionwright.sqlite-real-os-path-authority/2"
REVIEWED_SCHEMA = "motionwright.sqlite-os-path-reviewed-source/1"
REVIEWED_MANIFEST = "tooling/contracts/sqlite-os-path-g2/reviewed-source.json"
GATE_PATHS = (WORKFLOW, "tooling/ci_sqlite_os_path_g2.py",
              "tooling/tests/test_ci_sqlite_os_path_g2.py", REVIEWED_MANIFEST)
REVIEWED_INPUTS = ("Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml",
                   "crates/storage/Cargo.toml", "crates/domain/Cargo.toml",
                   "crates/storage/src/lib.rs", *TEST_PATH.values())
JOB_NAMES = {"windows": "windows-ntfs-real-g2", "macos": "macos-var-alias-real-g2"}
RUST_TOOLCHAIN = "1.98.1"

# Additive, manually selected storage-library gate. These four ignored Windows
# cases are different from the three NTFS integration tests and two Darwin tests.
CAS_SCOPE = "windows-cas"
CAS_CASES = (
    "blob_hash_tests::apply_refuses_leaf_swapped_for_equal_bytes_symlink",
    "blob_hash_tests::verify_blob_refuses_leaf_swapped_for_equal_bytes_symlink",
    "file_import::source_open_tests::replaced_symlink_source_is_rejected_before_staging",
    "file_import::source_open_tests::replay_source_hash_rejects_replaced_symlink_without_creating_outputs",
)
CAS_TEST_SOURCES = {
    "crates/storage/src/blob_hash_tests.rs":
        "d9f63814bca307ded99f3686f9b5fe6e00c165ae56ee1f591dd731ba53e63a37",
    "crates/storage/src/file_import.rs":
        "05d5aefee2e7f3cf9265a2fbe62779776713caea86b6814ec395200212a4b8b5",
}
REVIEWED_INPUTS = (*REVIEWED_INPUTS, *CAS_TEST_SOURCES,
                   "crates/storage/src/apply_crash_tests.rs")
JOB_NAMES = {**JOB_NAMES, CAS_SCOPE: "windows-cas-symlink-real-g2"}


class VerificationError(ValueError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise VerificationError(message)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify_rust_result(stdout: bytes, stderr: bytes, platform: str, code: int) -> dict:
    require(platform in CASES, "invalid OS scope")
    require(type(code) is int and code == 0, "actual Rust process exit was not zero")
    lines = (stdout + b"\n" + stderr).decode("utf-8", "strict")
    lines = re.sub(r"\x1b\[[0-9;]*m", "", lines).replace("\r\n", "\n").replace("\r", "\n")
    matches = re.findall(r"(?m)^test ([a-zA-Z0-9_]+) \.\.\. (ok|FAILED|ignored)$", lines)
    wanted = CASES[platform]
    headers = re.findall(r"(?m)^running (\d+) tests$", lines)
    require(headers == [str(len(wanted))], "wrong or omitted Rust running test count")
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


def source_identity(repo: Path, sha: str) -> dict:
    require(re.fullmatch("[0-9a-f]{40}", sha) is not None, "expected full Git SHA")
    require(git_query(repo, "rev-parse", "HEAD").decode().strip() == sha,
            "Git checkout SHA differs")
    parents = git_query(repo, "rev-list", "--parents", "-n", "1", "HEAD").decode().strip().split()
    require(parents == [sha, PARENT], "source ancestry changed; new review required")
    require(git_query(repo, "diff", "--name-only", PARENT, sha).decode().splitlines() ==
            [WORKFLOW, "tooling/ci_sqlite_os_path_g2.py",
             "tooling/tests/test_ci_sqlite_os_path_g2.py"],
            "unreviewed files in candidate")
    require(not git_query(repo, "status", "--porcelain", "--untracked-files=all").strip(),
            "checkout differs from Git")
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


def ordinary_bytes(path: Path) -> bytes:
    for part in (path, *path.parents):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "source path contains a link or reparse point")
    require(stat.S_ISREG(path.lstat().st_mode), "source path is not ordinary")
    require(path.stat().st_size < 6_000_000, "source input exceeds bound")
    return path.read_bytes()


def reviewed_manifest(raw: bytes, expected_digest: str) -> dict:
    require(type(raw) is bytes and 0 < len(raw) <= 65536,
            "reviewed manifest size differs")
    require(type(expected_digest) is str and re.fullmatch(r"[0-9a-f]{64}", expected_digest)
            and digest(raw) == expected_digest, "reviewed manifest digest differs")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate manifest field")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(
                           VerificationError("non-finite manifest value")))
    require(type(value) is dict and set(value) == {
        "schema", "scope", "baseline_sha", "baseline_tree", "review", "product_pins"},
        "reviewed manifest fields differ")
    require(value["schema"] == REVIEWED_SCHEMA and value["scope"] == "sqlite-os-path-g2",
            "reviewed manifest scope differs")
    for key in ("baseline_sha", "baseline_tree"):
        require(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{40}", value[key]),
                "reviewed baseline identity invalid")
    review = value["review"]
    require(type(review) is dict and set(review) == {"status", "authority", "reference"}
            and review["status"] == "REVIEWED_SOURCE_BASELINE" and review["authority"] == "A1"
            and type(review["reference"]) is str and 1 <= len(review["reference"]) <= 256
            and all(32 <= ord(c) <= 126 for c in review["reference"]),
            "A1 reviewed baseline required; proposal is not admission")
    pins = value["product_pins"]
    require(type(pins) is dict and set(pins) == set(REVIEWED_INPUTS),
            "reviewed product pin roster differs")
    require(all(type(h) is str and re.fullmatch(r"[0-9a-f]{64}", h) for h in pins.values()),
            "reviewed product digest invalid")
    require(all(pins[path] == SOURCES[path] for path in TEST_PATH.values()),
            "original real OS test definitions changed; separate review required")
    require(all(pins[path] == expected for path, expected in CAS_TEST_SOURCES.items()),
            "reviewed CAS test definitions changed; separate review required")
    return value


def reviewed_source_identity(repo: Path, sha: str, manifest_sha256: str) -> dict:
    require(type(sha) is str and re.fullmatch(r"[0-9a-f]{40}", sha), "expected full Git SHA")
    require(git_query(repo, "rev-parse", "HEAD").decode().strip() == sha,
            "Git checkout SHA differs")
    raw = git_query(repo, "show", sha + ":" + REVIEWED_MANIFEST)
    require(ordinary_bytes(repo / REVIEWED_MANIFEST) == raw,
            "working manifest differs from exact Git source")
    manifest = reviewed_manifest(raw, manifest_sha256)
    baseline = manifest["baseline_sha"]
    require(git_query(repo, "rev-parse", baseline + "^{tree}").decode().strip()
            == manifest["baseline_tree"], "reviewed baseline tree differs")
    # Full fetch is mandatory: an unavailable baseline fails instead of gaining credit.
    git_query(repo, "merge-base", "--is-ancestor", baseline, sha)
    changed_raw = git_query(repo, "diff", "--no-renames", "--name-only", "-z", baseline, sha)
    changed = [part.decode("utf-8", "strict") for part in changed_raw.split(b"\0") if part]
    require(len(changed) == len(set(changed)) and set(changed) <= set(GATE_PATHS),
            "product changed outside reviewed baseline; new A1 review required")
    require(not git_query(repo, "status", "--porcelain", "--untracked-files=all").strip(),
            "checkout differs from Git")
    files = {}
    for rel in (*REVIEWED_INPUTS, *GATE_PATHS):
        entries = [x for x in git_query(repo, "ls-tree", "-z", sha, "--", rel).split(b"\0") if x]
        require(len(entries) == 1, "required source Git entry missing")
        meta, sep, name = entries[0].partition(b"\t")
        mode, kind, oid = meta.split()
        require(sep == b"\t" and name.decode() == rel and kind == b"blob"
                and mode in (b"100644", b"100755"), "source Git metadata differs")
        blob = git_query(repo, "cat-file", "blob", oid.decode())
        require(hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest()
                == oid.decode(), "Git blob oid differs")
        working = ordinary_bytes(repo / rel)
        if working != blob:
            raise VerificationError("working source differs from Git blob: " + json.dumps({
                "path": rel,
                "expected_bytes": len(blob), "expected_sha256": digest(blob),
                "observed_bytes": len(working), "observed_sha256": digest(working),
            }, sort_keys=True))
        if rel in manifest["product_pins"]:
            require(digest(blob) == manifest["product_pins"][rel],
                    "reviewed source pin differs")
            require(git_query(repo, "show", baseline + ":" + rel) == blob,
                    "product input differs from reviewed baseline")
        files[rel] = {"bytes": len(blob), "sha256": digest(blob), "git_blob_oid": oid.decode()}
    require(b'channel = "1.98.1"' in ordinary_bytes(repo / "rust-toolchain.toml"),
            "reviewed Rust toolchain differs")
    return {"source_sha": sha, "tree_sha": git_query(repo, "rev-parse", "HEAD^{tree}").decode().strip(),
            "baseline_sha": baseline, "baseline_tree": manifest["baseline_tree"],
            "review": manifest["review"], "manifest_sha256": manifest_sha256,
            "changed_gate_paths": changed, "files": files, "execution_credit": "NONE"}


def run_context(head: str, platform: str, expected_attempt: int) -> dict:
    require(platform in JOB_NAMES and expected_attempt == 1 and type(expected_attempt) is int,
            "only a new first attempt is allowed")
    require(os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
            "actual manual GitHub dispatch required")
    require(os.environ.get("GITHUB_SHA") == head, "GHA source SHA changed")
    require(os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "rerun is not admitted")
    require(os.environ.get("GITHUB_JOB") == JOB_NAMES[platform], "wrong source job for OS scope")
    require(re.fullmatch(r"[1-9][0-9]{0,23}", os.environ.get("GITHUB_RUN_ID", "")),
            "actual run identity required")
    require(os.environ.get("GITHUB_REPOSITORY") == "seradotcom/motionwright",
            "wrong repository context")
    return {"source_sha": head, "run_id": os.environ["GITHUB_RUN_ID"], "attempt": "1",
            "job_id": os.environ["GITHUB_JOB"], "executor_OS": platform}


def write_receipt(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def rust_command(platform: str) -> list[str]:
    require(platform in CASES, "unsupported platform")
    command = ["cargo", "test", "--locked", "-p", "motionwright-storage", "--test",
               Path(TEST_PATH[platform]).stem, "--"]
    if platform == "windows":
        command.append("--ignored")
    return [*command, "--nocapture", "--test-threads=1", "--color", "never"]


def stop_owned_process(process, evidence_dir: Path) -> dict:
    """Best-effort termination of this one owned launch; uncertainty is retained."""
    result = {"requested": True, "tree_closure": "NOT_PROVEN"}
    try:
        if os.name == "nt":
            paths = (evidence_dir / "original-cleanup.stdout.log", evidence_dir / "original-cleanup.stderr.log")
            with paths[0].open("xb") as stdout, paths[1].open("xb") as stderr:
                cleanup = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                         stdout=stdout, stderr=stderr, timeout=15)
                result["exit_code"] = cleanup.returncode
        else:
            os.killpg(process.pid, signal.SIGKILL)
            result["process_group_signal"] = "SIGKILL"
    except (OSError, subprocess.TimeoutExpired) as error:
        result["error"] = type(error).__name__ + ": " + str(error)
    try:
        process.wait(timeout=5)
        result["parent_reaped"] = True
    except subprocess.TimeoutExpired:
        result["parent_reaped"] = False
    except OSError as error:
        result.update(parent_reaped=False, parent_observation_error=str(error))
    if os.name != "nt" and result.get("parent_reaped"):
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            result["tree_closure"] = "OWNED_PROCESS_GROUP_ABSENT"
        except OSError as error:
            result["group_observation_error"] = str(error)
    try:
        streams = {}
        for name in ("original-cleanup.stdout.log", "original-cleanup.stderr.log"):
            path = evidence_dir / name
            if path.is_file():
                raw = path.read_bytes()
                streams[name] = {"bytes": len(raw), "sha256": digest(raw)}
        result["original_cleanup_streams"] = streams
        write_receipt(evidence_dir / "cleanup.json", result)
    except OSError as error:
        result["cleanup_custody_error"] = str(error)
    return result


def observe_rust(command: list[str], repo: Path, env: dict, stdout_path: Path, stderr_path: Path) -> dict:
    observation = {"state": "NOT_STARTED", "exit_code": None, "automatic_retry": False}
    # Files receive original bytes as the child writes them, including a partial timeout.
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                  else {"start_new_session": True})
        process = subprocess.Popen(command, cwd=repo, env=env, stdout=stdout, stderr=stderr, **kwargs)
        observation["state"] = "UNKNOWN_RUNNING"
        observation["pid"] = process.pid
        try:
            observation["exit_code"] = process.wait(timeout=540)
            observation["state"] = "OBSERVED_EXIT"
        except subprocess.TimeoutExpired:
            observation.update(state="UNKNOWN_TIMEOUT_NO_RETRY", cleanup=stop_owned_process(process, stdout_path.parent))
        except BaseException as error:
            observation.update(state="UNKNOWN_OBSERVER_FAILURE_NO_RETRY",
                               error=type(error).__name__ + ": " + str(error),
                               cleanup=stop_owned_process(process, stdout_path.parent))
        stdout.flush(); stderr.flush()
        os.fsync(stdout.fileno()); os.fsync(stderr.fileno())
    return observation


def run_selected(repo: Path, head: str, platform: str, report: Path,
                 manifest_sha256: str, expected_attempt: int) -> None:
    require(report.is_absolute() and not report.exists() and not report.is_symlink(),
            "receipt must be new and outside source")
    require(report.parent.is_dir() and report.parent.resolve() == report.parent
            and not report.parent.is_relative_to(repo), "fresh external receipt parent required")
    runner_root = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(report.parent.is_relative_to(runner_root), "receipt must stay under disposable runner temp")
    evidence = {"schema": REVIEWED_RECEIPT_SCHEMA, "source_sha": head, "executor_OS": platform,
                "requested_source_sha": head,
                "observed_context": {key: os.environ.get(key) for key in (
                    "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
                    "GITHUB_REPOSITORY", "GITHUB_EVENT_NAME")},
                "status": "SOURCE_OR_ENVIRONMENT_REJECTED", "execution": {"state": "NOT_STARTED"},
                "cases_expected": list(CASES[platform]), "cases_observed": None,
                "real_Rust_SQLite": False, "automatic_retry": False,
                "power_loss_atomicity": "NOT_RUN", "installed_application": "NOT_RUN",
                "human_acceptance": False, "release_authorized": False,
                "at_utc": datetime.now(timezone.utc).isoformat()}
    write_receipt(report.with_name("started.json"), evidence)
    try:
        evidence.update(run_context(head, platform, expected_attempt))
        evidence["runner_scope"] = check_environment(platform, report.parent, repo)
        before = reviewed_source_identity(repo, head, manifest_sha256)
        evidence["source_before"] = before
        write_receipt(report.with_name("source-before.json"), before)
        env = dict(os.environ)
        require(not any(env.get(k) for k in ("RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTFLAGS",
                    "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_RUSTFLAGS", "CARGO_BUILD_TARGET"))
                and not any(k.startswith("CARGO_TARGET_") and k.endswith("_RUNNER") and v
                            for k, v in env.items()), "unreviewed Rust execution override")
        for key in tuple(env):
            if key.startswith("RUST_TEST_"):
                env.pop(key)
        env.update(CARGO_INCREMENTAL="0", CARGO_TERM_COLOR="never", RUSTUP_TOOLCHAIN=RUST_TOOLCHAIN)
        command = rust_command(platform)
        evidence["command"] = command
        stdout_path, stderr_path = report.with_name("original-rust.stdout.log"), report.with_name("original-rust.stderr.log")
        evidence["execution"] = {"state": "UNKNOWN_LAUNCH_OBSERVATION_PENDING", "automatic_retry": False}
        evidence["status"] = "UNKNOWN"
        execution = observe_rust(command, repo, env, stdout_path, stderr_path)
        evidence["execution"] = execution
        # Establish UNKNOWN before any later output/custody operation can fail.
        uncertain = execution["state"].startswith("UNKNOWN")
        evidence["status"] = "UNKNOWN" if uncertain else "FAIL_OR_OMISSION"
        for label, path in (("stdout", stdout_path), ("stderr", stderr_path)):
            raw = path.read_bytes()
            evidence[label] = {"file": path.name, "bytes": len(raw), "sha256": digest(raw),
                               "complete_terminal_stream": not uncertain}
        if not uncertain:
            after = reviewed_source_identity(repo, head, manifest_sha256)
            evidence["source_after"] = after
            write_receipt(report.with_name("source-after.json"), after)
            require(after == before, "source changed during selected execution")
            evidence["cases_observed"] = verify_rust_result(stdout_path.read_bytes(), stderr_path.read_bytes(),
                                                           platform, execution["exit_code"])
            evidence.update(status="PASS_EXACT_REAL_OS_G2", source_after=after,
                            tree_sha=after["tree_sha"], real_Rust_SQLite=True,
                            real_NTFS_fixture=platform == "windows",
                            real_macos_var_system_alias=platform == "macos")
    except Exception as error:
        evidence["error"] = {"type": type(error).__name__, "message": str(error)}
        if evidence.get("execution", {}).get("state", "").startswith("UNKNOWN"):
            evidence["status"] = "UNKNOWN"
    write_receipt(report, evidence)
    print(json.dumps({"source_sha": head, "OS": platform, "status": evidence["status"],
                      "attempt": evidence.get("attempt"), "report": str(report)}), flush=True)
    require(evidence["status"] == "PASS_EXACT_REAL_OS_G2", "selected OS authority G2 did not pass")


def cas_command(case: str | None = None) -> list[str]:
    """Enumerate the compiled library, then execute one exact ignored case once."""
    command = ["cargo", "test", "--locked", "-p", "motionwright-storage", "--lib"]
    if case is None:
        return [*command, "--", "--list", "--format", "terse", "--color", "never"]
    require(case in CAS_CASES, "unreviewed Windows CAS case")
    return [*command, case, "--", "--exact", "--ignored", "--nocapture",
            "--test-threads=1", "--color", "never"]


def cas_compiled_roster(stdout: bytes, stderr: bytes, code: int) -> dict:
    require(type(code) is int and code == 0, "actual compiled CAS enumeration exit was not zero")
    text = stdout.decode("utf-8", "strict").replace("\r\n", "\n")
    errors = stderr.decode("utf-8", "strict").replace("\r\n", "\n")
    require(not re.search(r"(?m)^(?:running \d+ tests?|test |[A-Za-z_][A-Za-z_0-9:]*: (?:test|benchmark))", errors),
            "ambiguous compiled CAS enumeration stderr")
    rows = [line for line in text.splitlines() if line]
    names = []
    totals = []
    for row in rows:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9:]*): test", row)
        total = re.fullmatch(r"(\d+) tests, 0 benchmarks", row)
        require(match is not None or total is not None, "unexpected compiled CAS enumeration line")
        if match:
            names.append(match.group(1))
        else:
            totals.append(int(total.group(1)))
    require(names and len(names) == len(set(names)), "empty or duplicate compiled CAS roster")
    require(not totals or totals == [len(names)], "compiled CAS enumeration total differs")
    require(set(CAS_CASES) <= set(names), "mandatory compiled Windows CAS case missing")
    return {"all": names, "selected": list(CAS_CASES),
            "not_selected": [name for name in names if name not in CAS_CASES],
            "not_selected_credit": "NONE", "compiled_count": len(names)}


def verify_cas_case(stdout: bytes, stderr: bytes, case: str, roster: dict, code: int) -> dict:
    require(case in CAS_CASES and roster["selected"] == list(CAS_CASES)
            and case in roster["all"] and len(roster["all"]) == roster["compiled_count"]
            and len(set(roster["all"])) == len(roster["all"]), "CAS case/compiled roster differs")
    require(type(code) is int and code == 0, "actual Windows CAS process exit was not zero")
    text = (stdout + b"\n" + stderr).decode("utf-8", "strict").replace("\r\n", "\n")
    matches = re.findall(r"(?m)^test ([A-Za-z_][A-Za-z_0-9:]*) \.\.\. (ok|FAILED|ignored)(?:,.*)?$", text)
    require(re.findall(r"(?m)^running (\d+) tests?$", text) == ["1"],
            "CAS exact case running count differs")
    require(matches == [(case, "ok")], "CAS exact case missing, substituted, duplicate, failed or ignored")
    summaries = re.findall(
        r"(?m)^test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; "
        r"(\d+) measured; (\d+) filtered out;", text)
    expected = (1, 0, 0, 0, roster["compiled_count"] - 1)
    require(len(summaries) == 1 and tuple(map(int, summaries[0])) == expected,
            "CAS exact case terminal summary or compiled selection differs")
    require(not re.search(r"(?m)^test result: FAILED", text), "CAS failed terminal status")
    require(len(re.findall(r"(?m)^test (?!result:).*$", text)) == 1,
            "unexpected extra CAS nominal output")
    return {"status": "PASS", "case": case, "summary": list(expected),
            "filtered_out_credit": "NONE"}


def run_windows_cas(repo: Path, head: str, report: Path,
                    manifest_sha256: str, expected_attempt: int) -> None:
    """Run an admitted compiled list and four exact cases; any UNKNOWN stops all follow-ups."""
    require(report.is_absolute() and not report.exists() and not report.is_symlink(),
            "receipt must be new and outside source")
    require(report.parent.is_dir() and report.parent.resolve() == report.parent
            and not report.parent.is_relative_to(repo), "fresh external receipt parent required")
    runner_root = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(report.parent.is_relative_to(runner_root), "receipt must stay under disposable runner temp")
    evidence = {"schema": "motionwright.windows-cas-symlink-real-g2/1", "source_sha": head,
                "requested_source_sha": head, "selection": CAS_SCOPE, "executor_OS": "windows",
                "status": "SOURCE_OR_ENVIRONMENT_REJECTED", "execution": {"state": "NOT_STARTED"},
                "cases_expected": list(CAS_CASES), "cases_observed": [], "observations": [],
                "automatic_retry": False, "real_Rust_SQLite": False,
                "real_NTFS_integration_tests": "NOT_RUN", "real_macos_var_system_alias": "NOT_RUN",
                "power_loss_atomicity": "NOT_RUN", "installed_application": "NOT_RUN",
                "human_acceptance": False, "release_authorized": False,
                "at_utc": datetime.now(timezone.utc).isoformat()}
    write_receipt(report.with_name("started.json"), evidence)
    try:
        evidence.update(run_context(head, CAS_SCOPE, expected_attempt))
        evidence["executor_OS"] = "windows"
        evidence["runner_scope"] = check_environment("windows", report.parent, repo)
        before = reviewed_source_identity(repo, head, manifest_sha256)
        evidence["source_before"] = before
        write_receipt(report.with_name("source-before.json"), before)
        env = dict(os.environ)
        require(not any(env.get(k) for k in ("RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTFLAGS",
                    "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_RUSTFLAGS", "CARGO_BUILD_TARGET"))
                and not any(k.startswith("CARGO_TARGET_") and k.endswith("_RUNNER") and v
                            for k, v in env.items()), "unreviewed Rust execution override")
        for key in tuple(env):
            if key.startswith("RUST_TEST_"):
                env.pop(key)
        env.update(CARGO_INCREMENTAL="0", CARGO_TERM_COLOR="never", RUSTUP_TOOLCHAIN=RUST_TOOLCHAIN)
        roster = None
        for index, case in enumerate((None, *CAS_CASES)):
            folder = report.parent / ("step-" + str(index))
            folder.mkdir(exist_ok=False)
            command = cas_command(case)
            step = {"index": index, "case": case, "command": command,
                    "source_sha": head, "state": "UNKNOWN_LAUNCH_OBSERVATION_PENDING"}
            evidence["observations"].append(step)
            write_receipt(folder / "started.json", step)
            stdout_path, stderr_path = folder / "original-rust.stdout.log", folder / "original-rust.stderr.log"
            evidence["execution"] = {"state": "UNKNOWN_LAUNCH_OBSERVATION_PENDING", "automatic_retry": False}
            evidence["status"] = "UNKNOWN"
            execution = observe_rust(command, repo, env, stdout_path, stderr_path)
            evidence["execution"] = execution
            step["execution"] = execution
            uncertain = execution["state"].startswith("UNKNOWN")
            evidence["status"] = "UNKNOWN" if uncertain else "FAIL_OR_OMISSION"
            step["state"] = execution["state"]
            for label, path in (("stdout", stdout_path), ("stderr", stderr_path)):
                raw = path.read_bytes()
                step[label] = {"file": str(path.relative_to(report.parent)), "bytes": len(raw),
                               "sha256": digest(raw), "complete_terminal_stream": not uncertain}
            write_receipt(folder / "observation.json", step)
            require(not uncertain, "UNKNOWN CAS observation; subsequent cases are forbidden")
            after = reviewed_source_identity(repo, head, manifest_sha256)
            write_receipt(folder / "source-after.json", after)
            require(after == before, "source changed during selected execution")
            if case is None:
                roster = cas_compiled_roster(stdout_path.read_bytes(), stderr_path.read_bytes(), execution["exit_code"])
                evidence["compiled_roster"] = roster
                write_receipt(folder / "compiled-roster.json", roster)
            else:
                observed = verify_cas_case(stdout_path.read_bytes(), stderr_path.read_bytes(), case, roster,
                                           execution["exit_code"])
                evidence["cases_observed"].append(observed)
                write_receipt(folder / "case-result.json", observed)
        require([row["case"] for row in evidence["cases_observed"]] == list(CAS_CASES),
                "complete four-case Windows CAS observation required")
        evidence.update(status="PASS_EXACT_REAL_WINDOWS_CAS_G2", source_after=after,
                        tree_sha=after["tree_sha"], real_Rust_SQLite=True,
                        real_Windows_CAS_symlink_cases=True)
        write_receipt(report.with_name("source-after.json"), after)
    except Exception as error:
        evidence["error"] = {"type": type(error).__name__, "message": str(error)}
        if evidence.get("execution", {}).get("state", "").startswith("UNKNOWN"):
            evidence["status"] = "UNKNOWN"
        elif evidence["status"] == "PASS_EXACT_REAL_WINDOWS_CAS_G2":
            evidence["status"] = "CUSTODY_FAILED"
    write_receipt(report, evidence)
    print(json.dumps({"source_sha": head, "selection": CAS_SCOPE, "status": evidence["status"],
                      "report": str(report)}), flush=True)
    require(evidence["status"] == "PASS_EXACT_REAL_WINDOWS_CAS_G2", "selected Windows CAS G2 did not pass")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--platform", choices=sorted((*CASES, CAS_SCOPE)), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--expected-attempt", type=int, choices=[1], required=True)
    args = parser.parse_args()
    if args.platform == CAS_SCOPE:
        run_windows_cas(args.repo.resolve(strict=True), args.expected_sha,
                        args.report, args.manifest_sha256, args.expected_attempt)
    else:
        run_selected(args.repo.resolve(strict=True), args.expected_sha,
                     args.platform, args.report, args.manifest_sha256, args.expected_attempt)


if __name__ == "__main__":
    main()
