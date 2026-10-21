#!/usr/bin/env python3
"""Run four frozen Store.apply process-termination cases with original phase custody.

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

SOURCE_SHA = "5c2d4962291f31fccff8533520c01083f17c225f"
SOURCE_TREE = "894d53385f2cc3002b425c6e71aebd19be65068e"
REVIEWED = {
    "crates/storage/src/apply_crash_tests.rs": "3198697fd965b471d1187559d82bf487fe71b15a76aeb7af3333cf945e29bbc5",
    "crates/storage/src/lib.rs": "77feec4804de9d84cce9b10414d2b584fe1865c486f07384e4b8e649d6d9b3e7",
    "crates/storage/Cargo.toml": "f60ec852c01b3dd28ded6bf1560645f43655d4e13bc46feef990b566b3e0d263",
    "Cargo.toml": "455492e78f913df8979a470f2538d0a2bcf5abd293b4937a6e1342233ee305e0",
    "Cargo.lock": "8ac6fad0b1c9282dbee571f79a127dbe45d13f88505868597ef3da04e9a22475",
    "SOURCE_LOCK.json": "f0e42c064c71a2307b4043b8b345ff54b36f8df2692db5809ce1e85635fdc1c1",
    "docs/reliability/storage-apply-process-recovery.md": "063323aa4c3fd58ae84b70d6f356cff6983973ee0399d549757c4d84d5d25cd7"
}
WORKFLOW = ".github/workflows/storage-apply-process-recovery.yml"
GATE_FILES = (WORKFLOW, "tooling/ci_storage_apply_recovery.py",
              "tooling/tests/test_ci_storage_apply_recovery.py")
PREFIX = "apply_crash_tests::real_apply_exit_"
CHECKPOINTS = {
    "apply_crash_tests::real_apply_exit_after_project_update_rolls_back_document_and_eviction_outbox": "after_project_update",
    "apply_crash_tests::real_apply_exit_after_event_insert_rolls_back_the_full_mutation": "after_event_insert",
    "apply_crash_tests::real_apply_exit_before_commit_rolls_back_receipt_and_journal": "before_commit",
    "apply_crash_tests::real_apply_exit_after_commit_recovers_outbox_and_replays_original_result": "after_commit"
}
CASES = {platform: tuple(CHECKPOINTS) for platform in ("Windows", "macOS")}
EVIDENCE_ROOT = "MOTIONWRIGHT_TEST_APPLY_EVIDENCE_ROOT"
PHASES = ("before", "interrupted", "cleaned", "after_retry", "final")

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
    require(env.get("GITHUB_JOB") == "storage-apply-recovery", "wrong logical job")
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
            "Store.apply roster changed or a required case is missing")
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
            "evidence_kind": "real Store.apply process termination, SQLite recovery and source-bound assertions"}


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "duplicate original JSON key")
            value[key] = item
        return value
    def constant(value):
        raise VerificationError("nonfinite original JSON value: " + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def original_json(path, root):
    original = fingerprint(path, root, 16 * 1024 * 1024)
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == original["sha256"]
            and fingerprint(path, root, 16 * 1024 * 1024) == original,
            "original JSON changed during admission")
    value = strict_json(raw)
    require(isinstance(value, dict), "original JSON must be an object")
    return value, original


def phase_snapshot_shape(value):
    """Check retained representation; the unchanged Rust assertions own semantics."""
    fields = {"project", "events", "receipts", "registrations", "evictions",
              "manifest", "immutable_blob"}
    require(set(value) == fields, "phase snapshot fields differ")
    require(isinstance(value["project"], list) and len(value["project"]) == 4
            and all(isinstance(x, str) for x in value["project"]), "phase project representation differs")
    for field, width in (("events", 4), ("receipts", 4), ("registrations", 6), ("evictions", 6)):
        require(isinstance(value[field], list) and all(isinstance(row, list)
                and len(row) == width and all(isinstance(x, str) for x in row) for row in value[field]),
                "phase SQL representation differs")
    for field in ("manifest", "immutable_blob"):
        data = value[field]
        if field == "manifest" and data is None:
            continue
        require(isinstance(data, list) and all(type(x) is int and 0 <= x <= 255 for x in data),
                "phase byte representation differs")
    require(bool(value["immutable_blob"]), "phase immutable blob is empty")


def admit_phase_custody(container, name):
    """Admit the actual five phase files and three child originals, without replay."""
    require(name in CHECKPOINTS, "unselected phase case")
    checkpoint = CHECKPOINTS[name]
    directory = container / checkpoint
    short_name = name.split("::", 1)[1]
    custody, custody_identity = original_json(directory / "custody.json", container)
    require(custody.get("schema") == "motionwright.apply-phase-custody/1"
            and custody.get("test_name") == short_name
            and custody.get("checkpoint") == checkpoint, "phase custody identity differs")
    require(custody.get("status") == "complete" and custody.get("parent_unwinding") is False
            and custody.get("errors") == [] and custody.get("case_identity") == "case.json",
            "phase custody incomplete or parent failed")
    require(custody.get("phases") == [{"phase": phase, "status": "captured", "reason": None}
                                      for phase in PHASES], "missing, duplicate or unavailable phase")
    require(custody.get("physical_power_loss") == "NOT_RUN", "phase scope changed")
    entries = custody.get("files")
    require(isinstance(entries, list) and len(entries) == 15, "phase file inventory must contain15 originals")
    files = {}
    for item in entries:
        require(isinstance(item, dict) and set(item) == {"file", "bytes", "sha256"},
                "unavailable or malformed original entry")
        filename = item["file"]
        require(isinstance(filename, str) and filename not in files and filename not in {"", ".", "..", "custody.json"}
                and "/" not in filename and "\\" not in filename and ":" not in filename,
                "duplicate or unsafe phase filename")
        require(type(item["bytes"]) is int and item["bytes"] >= 0
                and isinstance(item["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]),
                "invalid original byte count or digest")
        observed = fingerprint(directory / filename, container, 16 * 1024 * 1024)
        require(observed["bytes"] == item["bytes"] and observed["sha256"] == item["sha256"],
                "phase original hash or size differs")
        files[filename] = observed
    fixed = {"case.json", *(phase + ".json" for phase in PHASES)}
    require(fixed.issubset(files), "phase or case original missing")
    require({x.name for x in directory.iterdir()} == set(files) | {"custody.json"},
            "unlisted phase file or directory")
    case, identity = original_json(directory / "case.json", container)
    require(identity == files["case.json"], "case changed during admission")
    request_id = case.get("request_id")
    require(isinstance(request_id, str) and re.fullmatch(r"process-termination-[0-9a-f-]{36}", request_id),
            "phase request identity missing")
    require(isinstance(case.get("original"), dict)
            and all(isinstance(case.get(x), str) and case[x] for x in ("database", "manifest", "blob")),
            "case representation differs")
    # The referenced temporary paths have been deleted by the successful Rust
    # parent. Never reopen them or infer retained physical SQLite/CAS custody.
    for phase in PHASES:
        value, identity = original_json(directory / (phase + ".json"), container)
        require(identity == files[phase + ".json"], "phase changed during admission")
        phase_snapshot_shape(value)
    process_files = sorted(x for x in files if x.endswith(".process.json"))
    require(len(process_files) == 3, "expected one writer and two observers")
    processes, consumed = [], set(fixed)
    for filename in process_files:
        prefix = filename.removesuffix(".process.json")
        require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", prefix),
                "child call identity malformed")
        process, identity = original_json(directory / filename, container)
        require(identity == files[filename], "child original changed during admission")
        mode = process.get("mode")
        require(mode in {"writer", "observer"} and process.get("test_name") == short_name
                and process.get("checkpoint") == checkpoint and process.get("request_id") == request_id,
                "child case, checkpoint or request differs")
        require(type(process.get("exit_code")) is int
                and process["exit_code"] == (86 if mode == "writer" else 0)
                and process.get("success") is (mode == "observer")
                and process.get("timed_out") is False and process.get("error") is None
                and isinstance(process.get("exit_status"), str) and process["exit_status"],
                "child exit, timeout or error is not admissible")
        require(process.get("stdout") == prefix + ".stdout"
                and process.get("stderr") == prefix + ".stderr", "child streams differ")
        stdout, stderr = process["stdout"], process["stderr"]
        require(stdout in files and stderr in files and files[stderr]["bytes"] == 0,
                "child streams missing or stderr not empty")
        raw = (directory / stdout).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == files[stdout]["sha256"], "child stdout changed")
        marker = ("APPLY_CHECKPOINT:" if mode == "writer" else "APPLY_OBSERVED:") + request_id + ":" + checkpoint
        text = raw.decode().replace("\r\n", "\n")
        # libtest can print the test prefix before the fixture's println!.
        require(text.count(marker) == 1 and re.search(r"(?m)(?:^|\.\.\. )" + re.escape(marker) + r"$", text),
                "matching child checkpoint or observer marker absent or repeated")
        consumed.update((filename, stdout, stderr))
        processes.append({"call_id": prefix, "mode": mode, "exit_code": process["exit_code"],
                          "request_id": request_id, "checkpoint": checkpoint,
                          "process": files[filename], "stdout": files[stdout], "stderr": files[stderr]})
    require(sorted(x["mode"] for x in processes) == ["observer", "observer", "writer"]
            and consumed == set(files), "foreign or missing child originals")
    require(original_json(directory / "custody.json", container)[1] == custody_identity,
            "custody manifest changed during admission")
    for filename, identity in files.items():
        require(fingerprint(directory / filename, container, 16 * 1024 * 1024) == identity,
                "phase original changed after admission")
    return {"status": "COMPLETE_OBSERVED_PHASE_CUSTODY", "test_name": name,
            "checkpoint": checkpoint, "request_id": request_id, "directory": checkpoint,
            "custody_manifest": custody_identity, "files": files, "phases": list(PHASES), "processes": processes,
            "observer_to_phase_binding": "Source-bound Rust control flow; process metadata does not independently identify snapshot output hashes or PIDs",
            "raw_database_or_full_cas_retained": False}


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
                    "--nocapture", "--test-threads=1"]
                and x.get("phase_custody", {}).get("status") == "COMPLETE_OBSERVED_PHASE_CUSTODY"
                and x["phase_custody"].get("test_name") == x["name"]
                and x["phase_custody"].get("checkpoint") == CHECKPOINTS[x["name"]] for x in rows),
            "missing, foreign or unsuccessful case receipt")
    require(len({x["phase_custody"]["request_id"] for x in rows}) == 4, "phase request reused across cases")
    require({x for x in enumerated if x.startswith(PREFIX)} == set(wanted), "roster is incomplete")
    return {"status": "PASS_EXACT_SOURCE_SELECTED_G2", "required": len(wanted),
            "executed": len(rows), "passed": len(rows), "failed": 0, "ignored": 0,
            "phase_artifact_custody": "COMPLETE_FIVE_OBSERVED_PHASES_PER_CASE",
            "phase_jsons_retained": 20, "child_processes_observed": 12,
            "raw_sqlite_snapshots_retained": False, "full_cas_archive_retained": False,
            "fixture_custody": "The temporary database/CAS paths are deleted by Rust. Original Snapshot JSON includes SQL fields, manifest bytes and immutable blob bytes at the observed phases.",
            "observer_phase_binding": "Unchanged Rust control flow; no independent PID or observer-output-hash mapping is claimed",
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


def isolated_build_environment(environment):
    env = dict(environment, CARGO_INCREMENTAL="0", CARGO_PROFILE_TEST_DEBUG="0", CARGO_PROFILE_DEV_DEBUG="0")
    blocked = [k for k in env if k in {"RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC_WRAPPER",
               "RUSTC_WORKSPACE_WRAPPER", "RUSTC_BOOTSTRAP", "CARGO_BUILD_RUSTFLAGS"}
               or (k.startswith("CARGO_TARGET_") and k.endswith("RUSTFLAGS"))
               or k.startswith("MOTIONWRIGHT_TEST_")]
    require(not blocked, "inherited compiler or test overrides: " + ",".join(sorted(blocked)))
    return env


def run(repo, output, source_sha):
    gate = Path(__file__).resolve().parents[1]
    repo, output = repo.resolve(strict=True), output.resolve()
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    require(repo != gate and repo.is_relative_to(workspace) and gate.is_relative_to(workspace)
            and output.is_relative_to(workspace) and not output.is_relative_to(repo)
            and not output.is_relative_to(gate), "product, gate and evidence must be separate owned paths")
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": "motionwright.storage-apply-os-authority/1", "status": "FAIL",
               "product_source_sha": source_sha, "phase": "source-preflight",
               "case_processes_started": 0, "results": [],
               "formal_acceptance": False, "human_acceptance": False, "release_authorized": False}
    write_new(output / "started.json", {**receipt, "started_utc": now()})
    try:
        context = execution_context(dict(os.environ), source_sha)
        receipt["context"] = context
        receipt["required_cases"] = list(CASES[context["RUNNER_OS"]])
        product_inputs = tuple(REVIEWED)
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
        env = isolated_build_environment(os.environ)
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
        phase_root = output / "phases"
        phase_root.mkdir(exist_ok=False)
        require(phase_root.is_absolute() and phase_root.resolve(strict=True) == phase_root
                and not phase_root.is_relative_to(repo) and not phase_root.is_relative_to(gate),
                "phase root must be canonical and outside both checkouts")
        receipt["phase_container"] = "phases"
        for number, name in enumerate(CASES[context["RUNNER_OS"]], 1):
            tmp = output.parent / "x" / str(number)
            tmp.mkdir(parents=True, exist_ok=False)
            case_env = dict(env, TMP=str(tmp), TEMP=str(tmp), TMPDIR=str(tmp),
                            MOTIONWRIGHT_TEST_APPLY_EVIDENCE_ROOT=str(phase_root))
            label = f"case-{number:02d}"
            require(not (phase_root / CHECKPOINTS[name]).exists(), "phase checkpoint already exists; no reuse")
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
            assertion = None
            try:
                assertion = admit_case(name, read_original(output, command, "stdout") + b"\n" +
                                 read_original(output, command, "stderr"), command, binary,
                                 after_binary, enumerated, context)
                row = {**assertion, "phase_custody": admit_phase_custody(phase_root, name)}
            except (OSError, VerificationError, UnicodeError, ValueError) as error:
                row = {"name": name, "status": "UNKNOWN" if unknown else "FAIL",
                       "command": command, "error": str(error),
                       "original_assertion_observation": assertion,
                       "phase_custody_status": "UNSEALED_UNKNOWN" if unknown else "NOT_ADMITTED",
                       "original_phase_directory": "phases/" + CHECKPOINTS[name]}
            receipt["results"].append(row)
            write_new(output / (label + ".receipt.json"), row)
            if unknown or command["observation"] == "NOT_STARTED_LAUNCH_ERROR":
                break
        receipt["phase"] = "postcheck"
        after = snapshot()
        receipt["source_after"] = after
        write_new(output / "source-after.json", after)
        for row in receipt["results"]:
            if row.get("status") == "PASS":
                require(admit_phase_custody(phase_root, row["name"]) == row["phase_custody"],
                        "phase originals changed after their case")
        if len(receipt["results"]) == 4:
            require({p.name for p in phase_root.iterdir()} == set(CHECKPOINTS.values()),
                    "phase checkpoint inventory incomplete or unexpected")
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
