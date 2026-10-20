#!/usr/bin/env python3
"""Run the frozen bundle-export OS cases; retain exact-source execution evidence.

The product and this gate use separate checkouts. This is not release admission,
an installed application test, a power-loss test, or permission to retry UNKNOWN.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

SOURCE_SHA = "2c1e41fe14350bd478ea02d9301de75ccd0b29cc"
SOURCE_TREE = "9aa8b4cd7451148a72b5f49ad4790b0f7cbddebe"
REVIEWED = {
    "crates/storage/src/portability.rs": "b3281be45a51d83987bd3e2a6862f38ac3b09aaa9efa730815e87ff63bdc34be",
    "crates/storage/src/lib.rs": "5833e7c185e8ba5aa2fa3b05ddda8dc23b20fac8ec9ea551c903f521cea7cd83",
    "crates/storage/Cargo.toml": "02d305e94bf37f92d643ec9b7bae0eeab104dffcea08be44029d86f4bd76b8cb",
    "Cargo.lock": "c298abcbaa137aa680a74d4eb89cc10d871270a7922381799c9515a260b54cea",
}
WORKFLOW = ".github/workflows/storage-bundle-export-authority.yml"
GATE_FILES = (WORKFLOW, "tooling/ci_bundle_export_authority.py",
              "tooling/tests/test_ci_bundle_export_authority.py")
PREFIX = "portability::tests::bundle_export_"
COMMON = ("rejects_empty_destination_created_at_publication",
          "rejects_file_created_at_publication", "preserves_preexisting_destinations",
          "unclaimed_destination_restores_complete_original_bytes")
CASES = {
    "Windows": tuple(PREFIX + x for x in (*COMMON,
        "windows_long_unicode_destination_remains_operational",
        "windows_nul_paths_fail_before_publication")),
    "macOS": tuple(PREFIX + x for x in (*COMMON,
        "preserves_dangling_symlink_created_at_publication")),
}
SHA = re.compile(r"[0-9a-f]{40}\Z")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class VerificationError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise VerificationError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(path, root, limit=256 * 1024 * 1024):
    """Bounded descriptor read, confined to an owned root, including empty logs."""
    path, root = Path(path).absolute(), Path(root).absolute()
    require(path.is_relative_to(root) and path != root and ".." not in path.parts,
            "artifact escaped its owned root")
    for item in (path, *(x for x in path.parents if x == root or root in x.parents)):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400, "linked artifact path")
    first = path.lstat()
    require(stat.S_ISREG(first.st_mode) and first.st_size <= limit, "unbounded or nonregular artifact")
    stamp = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_nlink)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest, size = hashlib.sha256(), 0
    with os.fdopen(os.open(path, flags), "rb") as stream:
        require(stamp(os.fstat(stream.fileno())) == stamp(first), "artifact replaced before read")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            require(size <= limit, "artifact exceeds bound")
            digest.update(chunk)
        require(stamp(os.fstat(stream.fileno())) == stamp(first), "artifact changed during read")
    require(stamp(path.lstat()) == stamp(first) and size == first.st_size, "artifact replaced after read")
    return {"path": path.relative_to(root).as_posix(), "bytes": size,
            "sha256": digest.hexdigest(), "identity": list(stamp(first))}


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull, GIT_GRAFT_FILE=os.devnull,
               GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                             "-C", str(repo), *args], env=env, capture_output=True, timeout=45)
    require(result.returncode == 0 and len(result.stdout) <= 16 * 1024 * 1024,
            "Git source query failed or exceeded its bound")
    return result.stdout


def execution_context(env, source_sha, runtime_platform=None):
    require(source_sha == SOURCE_SHA, "unreviewed product SHA")
    runtime_platform = sys.platform if runtime_platform is None else runtime_platform
    expected_os = {"win32": "Windows", "darwin": "macOS"}.get(runtime_platform)
    require(expected_os in CASES and env.get("RUNNER_OS") == expected_os,
            "requires selected real Windows or macOS runner")
    require(env.get("GITHUB_ACTIONS") == "true"
            and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "G2 requires explicit dispatch")
    for key in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"):
        require(SHA.fullmatch(env.get(key, "")), "missing full workflow SHA")
    require(env["GITHUB_SHA"] == env["GITHUB_WORKFLOW_SHA"], "workflow checkout identity differs")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]*", env.get(key, "")), "missing run or attempt")
    require(env.get("GITHUB_JOB") == "bundle-export-authority", "wrong logical job")
    require(bool(env.get("GITHUB_REPOSITORY")) and
            env.get("GITHUB_WORKFLOW_REF", "").startswith(env["GITHUB_REPOSITORY"] + "/" + WORKFLOW + "@"),
            "wrong workflow identity")
    keys = ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_REPOSITORY",
            "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "RUNNER_OS", "RUNNER_ARCH",
            "RUNNER_NAME", "ImageOS", "ImageVersion")
    return {"product_source_sha": source_sha,
            **{key: env.get(key, "") for key in keys}}


def source_snapshot(repo, expected_sha, paths, expected_tree=None):
    require(SHA.fullmatch(expected_sha), "full source SHA required")
    require(git(repo, "rev-parse", "HEAD").decode().strip() == expected_sha, "checkout SHA differs")
    raw = git(repo, "cat-file", "commit", expected_sha)
    require(hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == expected_sha,
            "commit object differs")
    headers = raw.split(b"\n\n", 1)[0].decode().splitlines()
    tree = next(x[5:] for x in headers if x.startswith("tree "))
    require(expected_tree is None or tree == expected_tree, "product tree differs")
    require(not git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"),
            "checkout has changed or unbound inputs")
    files = {}
    for path in paths:
        entry = git(repo, "ls-tree", "-z", expected_sha, "--", path).split(b"\0")
        require(len(entry) == 2 and entry[1] == b"", "missing unique tracked input")
        meta, name = entry[0].split(b"\t", 1)
        mode, kind, oid = meta.split()
        require(mode in (b"100644", b"100755") and kind == b"blob" and name.decode() == path,
                "input is not a regular Git blob")
        blob = git(repo, "cat-file", "blob", oid.decode())
        require(hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest() == oid.decode(),
                "Git blob identity differs")
        observed = fingerprint(repo / path, repo, 16 * 1024 * 1024)
        require(observed["bytes"] == len(blob) and observed["sha256"] == hashlib.sha256(blob).hexdigest(),
                "worktree bytes differ from Git: " + path + "; local=" + observed["sha256"] +
                "; Git=" + hashlib.sha256(blob).hexdigest())
        if expected_sha == SOURCE_SHA and path in REVIEWED:
            require(observed["sha256"] == REVIEWED[path], "reviewed product bytes differ")
        files[path] = {**observed, "git_blob_oid": oid.decode()}
    return {"checkout": str(repo), "sha": expected_sha, "tree": tree,
            "parents": [x[7:] for x in headers if x.startswith("parent ")], "files": files}


def select_binary(raw, code, repo, target):
    require(code == 0, "compilation did not succeed")
    messages = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    finished = [x for x in messages if x.get("reason") == "build-finished"]
    require(len(finished) == 1 and finished[0].get("success") is True, "missing compile completion")
    candidates = [x for x in messages if x.get("reason") == "compiler-artifact"
                  and x.get("executable") and x.get("target", {}).get("name") == "motionwright_storage"
                  and x["target"].get("kind") == ["lib"] and x.get("profile", {}).get("test") is True]
    require(len(candidates) == 1, "test binary is missing or ambiguous")
    item = candidates[0]
    require(Path(item["manifest_path"]).absolute() == repo / "crates/storage/Cargo.toml"
            and Path(item["target"]["src_path"]).absolute() == repo / "crates/storage/src/lib.rs",
            "test binary belongs to another source checkout")
    path = Path(item["executable"]).absolute()
    identity = fingerprint(path, target)
    require(identity["bytes"] > 0, "empty test binary")
    return {"absolute_path": str(path), **identity}


def selected_roster(raw, code, platform):
    require(platform in CASES and code == 0, "test enumeration did not succeed")
    names = tuple(re.findall(r"(?m)^([^\s]+): test$", raw.decode().replace("\r\n", "\n")))
    require(len(names) == len(set(names)) and names, "duplicate or absent enumerated tests")
    require({x for x in names if x.startswith(PREFIX)} == set(CASES[platform]),
            "bundle export roster changed or a required case is missing")
    return names


def admit_case(name, raw, command, binary, after, enumerated, context):
    require(name in CASES[context["RUNNER_OS"]], "unselected case")
    require(command["exit_code"] == 0 and command["observation"] == "DIRECT_PROCESS_EXIT_OBSERVED",
            "case failed or is UNKNOWN; no retry")
    require(command.get("log_custody") == "VERIFIED_SNAPSHOT", "original log custody incomplete")
    require(command["argv"] == [binary["absolute_path"], "--exact", name, "--nocapture", "--test-threads=1"],
            "case command differs")
    require(command["context"] == context and binary == after, "case context or executed binary changed")
    text = ANSI.sub("", raw.decode().replace("\r\n", "\n"))
    results = re.findall(r"(?m)^test (.+) \.\.\. (ok|FAILED|ignored)$", text)
    summaries = re.findall(r"(?m)^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; "
                           r"(\d+) ignored; (\d+) measured; (\d+) filtered out;", text)
    require(results == [(name, "ok")] and len(summaries) == 1
            and summaries[0][0] == "ok" and tuple(map(int, summaries[0][1:])) ==
            (1, 0, 0, 0, len(enumerated) - 1), "case missing, omitted or unsuccessful")
    return {"name": name, "status": "PASS", "context": context,
            "binary_sha256": binary["sha256"], "command": command,
            "evidence_kind": "real filesystem plus NUL preflight; OS move not invoked"
            if name.endswith("windows_nul_paths_fail_before_publication")
            else "real filesystem, SQLite and bundle publication assertions"}


def finish(before, after, binary, rows, enumerated, context):
    require(before == after, "source inputs changed during execution")
    require(before["product"]["sha"] == context["product_source_sha"] == SOURCE_SHA
            and before["product"]["tree"] == SOURCE_TREE
            and before["gate"]["sha"] == context["GITHUB_SHA"]
            and set(REVIEWED).issubset(before["product"]["files"])
            and set(GATE_FILES).issubset(before["gate"]["files"]), "source binding missing")
    require(all(before["product"]["files"][path].get("sha256") == digest
                for path, digest in REVIEWED.items()), "reviewed product evidence differs")
    require(binary.get("bytes", 0) > 0 and re.fullmatch(r"[0-9a-f]{64}", binary.get("sha256", "")),
            "executed binary custody missing")
    wanted = CASES[context["RUNNER_OS"]]
    require(tuple(x.get("name") for x in rows) == wanted and
            all(x.get("status") == "PASS" and x.get("context") == context
                and x.get("binary_sha256") == binary["sha256"]
                and x.get("command", {}).get("context") == context
                and x["command"].get("exit_code") == 0
                and x["command"].get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
                and x["command"].get("log_custody") == "VERIFIED_SNAPSHOT"
                and x["command"].get("cwd") == before["product"]["checkout"]
                and x["command"].get("argv") == [binary["absolute_path"], "--exact", x["name"],
                    "--nocapture", "--test-threads=1"] for x in rows),
            "missing, foreign or unsuccessful case receipt")
    require({x for x in enumerated if x.startswith(PREFIX)} == set(wanted), "roster is incomplete")
    return {"status": "PASS_EXACT_SOURCE_SELECTED_G2", "required": len(wanted),
            "executed": len(rows), "passed": len(rows), "failed": 0, "ignored": 0,
            "fixtures_retained": False,
            "fixture_custody": "TempDir fixtures are deleted by the frozen tests; retained originals are logs, sources and the executed binary",
            "power_loss_atomicity": "NOT_RUN", "installed_application": "NOT_RUN",
            "other_source_or_platform_claims": False, "formal_acceptance": False,
            "human_acceptance": False, "release_authorized": False}


def execute(argv, cwd, env, output, label, timeout, context):
    row = {"argv": argv, "cwd": str(cwd), "started_utc": now(), "context": context}
    with (output / (label + ".stdout.log")).open("xb") as out, (output / (label + ".stderr.log")).open("xb") as err:
        try:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=out, stderr=err, timeout=timeout)
            row.update(exit_code=result.returncode, observation="DIRECT_PROCESS_EXIT_OBSERVED")
        except subprocess.TimeoutExpired:
            row.update(exit_code=None, observation="UNKNOWN_TIMEOUT_NO_RETRY",
                       diagnostic_custody="UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED")
        except OSError as error:
            row.update(exit_code=None, observation="NOT_STARTED_LAUNCH_ERROR", error=str(error))
    row["finished_utc"] = now()
    row["log_custody"] = "VERIFIED_SNAPSHOT"
    for stream in ("stdout", "stderr"):
        path = output / (label + "." + stream + ".log")
        try:
            row[stream] = fingerprint(path, output)
        except (OSError, VerificationError) as error:
            row["log_custody"] = "INCOMPLETE"
            row[stream] = {"path": path.name, "custody_error": str(error)}
    write_new(output / (label + ".command.json"), row)
    return row


def read_original(output, command, stream):
    require(command.get("log_custody") == "VERIFIED_SNAPSHOT", "original log custody incomplete")
    expected = command[stream]
    path = output / expected["path"]
    require(fingerprint(path, output) == expected, "original log changed before admission")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected["sha256"]
            and fingerprint(path, output) == expected, "original log changed during admission")
    return raw


def run(repo, output, source_sha):
    gate = Path(__file__).resolve().parents[1]
    repo, output = repo.resolve(strict=True), output.resolve()
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    require(repo != gate and repo.is_relative_to(workspace) and gate.is_relative_to(workspace)
            and output.is_relative_to(workspace) and not output.is_relative_to(repo)
            and not output.is_relative_to(gate), "product, gate and evidence must be separate owned paths")
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": "motionwright.bundle-export-os-authority/1", "status": "FAIL",
               "product_source_sha": source_sha, "phase": "source-preflight",
               "case_processes_started": 0, "results": [],
               "formal_acceptance": False, "human_acceptance": False, "release_authorized": False}
    write_new(output / "started.json", {**receipt, "started_utc": now()})
    try:
        context = execution_context(dict(os.environ), source_sha)
        receipt["context"] = context
        receipt["required_cases"] = list(CASES[context["RUNNER_OS"]])
        product_inputs = (*REVIEWED, "Cargo.toml", "SOURCE_LOCK.json")
        snapshot = lambda: {"product": source_snapshot(repo, SOURCE_SHA, product_inputs, SOURCE_TREE),
                            "gate": source_snapshot(gate, context["GITHUB_SHA"], GATE_FILES)}
        before = snapshot()
        receipt["source_before"] = before
        write_new(output / "source-before.json", before)
        for label, root in (("product", repo), ("gate", gate)):
            for name, expected in before[label]["files"].items():
                dest = output / "sources" / label / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, dest)
                observed = fingerprint(dest, output)
                require((observed["bytes"], observed["sha256"]) == (expected["bytes"], expected["sha256"]),
                        "retained source copy differs")
        target = output.parent / "t"
        require(Path(os.environ["CARGO_TARGET_DIR"]).resolve() == target.resolve(), "foreign build target")
        target.mkdir(exist_ok=False)
        env = dict(os.environ, CARGO_INCREMENTAL="0", CARGO_PROFILE_TEST_DEBUG="0", CARGO_PROFILE_DEV_DEBUG="0")
        blocked = [k for k in env if k in {"RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC_WRAPPER",
                   "RUSTC_WORKSPACE_WRAPPER", "RUSTC_BOOTSTRAP", "CARGO_BUILD_RUSTFLAGS"}
                   or (k.startswith("CARGO_TARGET_") and k.endswith("RUSTFLAGS"))
                   or k.startswith("MOTIONWRIGHT_TEST_")]
        require(not blocked, "inherited compiler or test overrides: " + ",".join(sorted(blocked)))
        receipt["build_environment"] = {k: env.get(k, "") for k in
            ("CARGO_HOME", "CARGO_TARGET_DIR", "RUSTUP_TOOLCHAIN", "CARGO_INCREMENTAL",
             "CARGO_PROFILE_TEST_DEBUG", "CARGO_PROFILE_DEV_DEBUG")}
        receipt["phase"] = "compile"
        for label, argv in (("rustc-version", ["rustc", "-Vv"]), ("cargo-version", ["cargo", "-V"])):
            require(execute(argv, repo, env, output, label, 45, context)["exit_code"] == 0, "tool identity failed")
        build = execute(["cargo", "test", "--locked", "-p", "motionwright-storage", "--lib", "--no-run",
                         "--message-format=json"], repo, env, output, "compile", 1500, context)
        receipt["build"] = build
        if build["observation"] == "UNKNOWN_TIMEOUT_NO_RETRY":
            receipt["status"] = "UNKNOWN"
        binary = select_binary(read_original(output, build, "stdout"), build["exit_code"], repo, target)
        receipt["binary"] = binary
        copy = output / ("executed-test-binary.exe" if context["RUNNER_OS"] == "Windows" else "executed-test-binary")
        shutil.copyfile(binary["absolute_path"], copy)
        retained = fingerprint(copy, output)
        require((retained["bytes"], retained["sha256"]) == (binary["bytes"], binary["sha256"]), "binary copy differs")
        receipt["retained_binary"] = retained
        receipt["phase"] = "roster"
        listing = execute([binary["absolute_path"], "--list", "--format", "terse"], repo, env, output, "roster", 45, context)
        if listing["observation"] == "UNKNOWN_TIMEOUT_NO_RETRY":
            receipt["status"] = "UNKNOWN"
        enumerated = selected_roster(read_original(output, listing, "stdout"), listing["exit_code"], context["RUNNER_OS"])
        receipt["enumerated_cases"] = list(enumerated)
        require(snapshot() == before, "source changed during compilation")
        receipt["phase"] = "cases"
        for number, name in enumerate(CASES[context["RUNNER_OS"]], 1):
            tmp = output.parent / "x" / str(number)
            tmp.mkdir(parents=True, exist_ok=False)
            case_env = dict(env, TMP=str(tmp), TEMP=str(tmp), TMPDIR=str(tmp))
            label = f"case-{number:02d}"
            require(binary == {"absolute_path": binary["absolute_path"],
                    **fingerprint(Path(binary["absolute_path"]), target)}, "binary changed before execution")
            command = execute([binary["absolute_path"], "--exact", name, "--nocapture", "--test-threads=1"],
                              repo, case_env, output, label, 120, context)
            if command["observation"] != "NOT_STARTED_LAUNCH_ERROR":
                receipt["case_processes_started"] += 1
            unknown = command["observation"] == "UNKNOWN_TIMEOUT_NO_RETRY"
            if unknown:
                receipt["status"] = "UNKNOWN"
            after_binary = {"absolute_path": binary["absolute_path"], **fingerprint(Path(binary["absolute_path"]), target)}
            try:
                row = admit_case(name, read_original(output, command, "stdout") + b"\n" +
                                 read_original(output, command, "stderr"), command, binary,
                                 after_binary, enumerated, context)
            except (VerificationError, UnicodeError) as error:
                row = {"name": name, "status": "UNKNOWN" if unknown else "FAIL",
                       "command": command, "error": str(error)}
            receipt["results"].append(row)
            write_new(output / (label + ".receipt.json"), row)
            if unknown or command["observation"] == "NOT_STARTED_LAUNCH_ERROR":
                break
        receipt["phase"] = "postcheck"
        after = snapshot()
        receipt["source_after"] = after
        write_new(output / "source-after.json", after)
        receipt.update(finish(before, after, binary, receipt["results"], enumerated, context))
        receipt["phase"] = "complete"
    except Exception as error:
        receipt["error"] = type(error).__name__ + ": " + str(error)
    receipt["finished_utc"] = now()
    receipt["operations_retried"] = False
    receipt["cases_without_verified_pass"] = [name for name in receipt.get("required_cases", [])
        if name not in {row["name"] for row in receipt["results"] if row.get("status") == "PASS"}]
    write_new(output / "receipt.json", receipt)
    print(json.dumps({k: receipt.get(k) for k in ("status", "product_source_sha", "case_processes_started", "error")}))
    return 0 if receipt["status"] == "PASS_EXACT_SOURCE_SELECTED_G2" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    return run(args.repo, args.output, args.source_sha)


if __name__ == "__main__":
    raise SystemExit(main())
