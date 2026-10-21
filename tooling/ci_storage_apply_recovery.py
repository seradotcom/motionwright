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
from types import MappingProxyType

SOURCE_SHA = "5faedc090aa45b33833b229050c09bf424de938f"
SOURCE_TREE = "b3936a746abf33d3b066b517d8b1493bda2bf372"
REVIEWED = {
    "crates/storage/src/apply_crash_tests.rs": "3198697fd965b471d1187559d82bf487fe71b15a76aeb7af3333cf945e29bbc5",
    "crates/storage/src/lib.rs": "b86b2cf5d86ed8ea8076a468e3810e123666bc2ec092b8aa3e6180643aa3e20e",
    "crates/storage/Cargo.toml": "02d305e94bf37f92d643ec9b7bae0eeab104dffcea08be44029d86f4bd76b8cb",
    "Cargo.toml": "455492e78f913df8979a470f2538d0a2bcf5abd293b4937a6e1342233ee305e0",
    "Cargo.lock": "5a70f296cd65e9fa9c5f9f5265a7f13735d72b74496b05b2d9e4501cbd604cdd",
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


def phase_snapshot_shape(value, cohort="historical5fa"):
    """Check retained representation; the unchanged Rust assertions own semantics."""
    require(cohort in {"historical5fa", "binding4"}, "unknown phase cohort")
    receipt_width = 6 if cohort == "binding4" else 4
    fields = {"project", "events", "receipts", "registrations", "evictions",
              "manifest", "immutable_blob"}
    require(set(value) == fields, "phase snapshot fields differ")
    require(isinstance(value["project"], list) and len(value["project"]) == 4
            and all(isinstance(x, str) for x in value["project"]), "phase project representation differs")
    for field, width in (("events", 4), ("receipts", receipt_width), ("registrations", 6), ("evictions", 6)):
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


def admit_phase_custody(container, name, cohort="historical5fa"):
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
        phase_snapshot_shape(value, cohort)
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



# The historical constants and callable helpers above remain source-frozen.
BINDING4_SOURCE_SHA = "449de7a315a513468610d517e78f9ab87f76ae9d"
BINDING4_SOURCE_TREE = "6193f0f2721914e84bac571986cacd5492f8a7c9"
BINDING4_REVIEWED = MappingProxyType({'Cargo.lock': '5a70f296cd65e9fa9c5f9f5265a7f13735d72b74496b05b2d9e4501cbd604cdd',
 'Cargo.toml': '455492e78f913df8979a470f2538d0a2bcf5abd293b4937a6e1342233ee305e0',
 'SOURCE_LOCK.json': 'f0e42c064c71a2307b4043b8b345ff54b36f8df2692db5809ce1e85635fdc1c1',
 'crates/storage/Cargo.toml': '02d305e94bf37f92d643ec9b7bae0eeab104dffcea08be44029d86f4bd76b8cb',
 'crates/storage/src/apply_crash_tests.rs': '9febb826879b1180ddd8afc84256a7fd6d7b76c3c5769c726ad612c2adc3e974',
 'crates/storage/src/apply_receipt_binding.rs': '9922802f0deaa32c9e2b2dee7d3d0d7fbcfdfa58b2047b506f0432fd9db4d4af',
 'crates/storage/src/file_import.rs': '5304deb69723dda86036ae88d5de5b60479af17262631a5fc234ae62e01dec0b',
 'crates/storage/src/lib.rs': '058b6340a8ffb046e49330997b8c7b5319c5e98998820fd02920efa4363ec903',
 'crates/storage/src/production.rs': '3d106e93c8c4a5fe163f9bcb8443b8d364d88db009f856de6001971461dcafa9',
 'crates/storage/src/project_creation.rs': '8b025a21443ba73ef9f712264e010d3fda605ebf37df71d10c7d79d2063b0dc9',
 'crates/storage/tests/apply_binding_migration.rs': '25500c33cbb4ad19079ad8e5bfa396bcf21afb7fd9595818493c082d11313ace',
 'crates/storage/tests/apply_receipt_binding.rs': 'b74d2e616e880da1b9be515d3dcb5d92332af557f291b88bc9b34245b6c45274',
 'crates/storage/tests/apply_receipt_integrity.rs': 'cc995895ccfae9c8f584e15555e38aeccdca684f3afcc5b64d72b69619428883',
 'crates/storage/tests/schema_migration_atomicity.rs': '0df1d3e87dbd2b1b14687ed58cd1a51fc60e58aa56d3c1467cb5951a8f3b870b',
 'docs/reliability/apply-receipt-binding.md': '7fe6cecacb86b0965ebcb4ab0dd71c581d3be417c8295c3c45bc839d1c74c92c'})
BASELINE_SOURCE_SHA = "c9929498f49bcd03101390c44b5a12f31e43f052"
BASELINE_SOURCE_TREE = "02f2829de924af92e7d146b4e3b87da0da0b12d8"
BASELINE_REVIEWED = MappingProxyType({'crates/storage/src/lib.rs': '11c0a97ca6c20a47b7e61217146a831b2bba8c53581373317c20e714b1a07bf2',
 'crates/storage/src/apply_crash_tests.rs': '3198697fd965b471d1187559d82bf487fe71b15a76aeb7af3333cf945e29bbc5',
 'crates/storage/tests/apply_receipt_binding.rs': 'c7b92ab20966ee08d709da15020978658e3982af9b2d2eb4f62d46ddb81d022a',
 'crates/storage/Cargo.toml': '02d305e94bf37f92d643ec9b7bae0eeab104dffcea08be44029d86f4bd76b8cb',
 'Cargo.toml': '455492e78f913df8979a470f2538d0a2bcf5abd293b4937a6e1342233ee305e0',
 'Cargo.lock': '5a70f296cd65e9fa9c5f9f5265a7f13735d72b74496b05b2d9e4501cbd604cdd',
 'SOURCE_LOCK.json': 'f0e42c064c71a2307b4043b8b345ff54b36f8df2692db5809ce1e85635fdc1c1',
 'docs/reliability/storage-apply-process-recovery.md': '063323aa4c3fd58ae84b70d6f356cff6983973ee0399d549757c4d84d5d25cd7'})
BINDING4_INTEGRATIONS = MappingProxyType({'apply_receipt_integrity': ('known_request_rejects_a_valid_result_from_another_project',
                             'known_request_rejects_a_different_embedded_request_id',
                             'known_request_rejects_a_different_result_generation',
                             'known_request_rejects_a_persisted_replay_flag',
                             'known_request_rejects_an_inconsistent_revision_transition',
                             'known_request_rejects_a_valid_result_ahead_of_current_authority',
                             'known_request_rejects_a_result_that_breaks_domain_invariants',
                             'known_request_rejects_a_result_bound_to_another_valid_event',
                             'known_request_rejects_a_missing_commit_event',
                             'known_request_rejects_a_changed_commit_event_payload',
                             'known_request_rejects_a_changed_commit_event_kind',
                             'known_request_replays_its_original_snapshot_after_later_edit_and_reopen'),
 'apply_receipt_binding': ('versioned_binding_rejects_same_change_from_a_later_execution',
                           'lost_ack_after_later_replay_observes_the_original_commit_after_reopen',
                           'legacy_representation_remains_unverified_without_backfill_or_reapplication',
                           'observer_distinguishes_mismatch_absence_and_authority_without_writes',
                           'unsupported_contract_is_explicit_and_never_becomes_absence_or_legacy',
                           'malformed_or_oversized_binding_never_downgrades_or_repairs',
                           'invalid_sqlite_receipt_types_and_utf8_are_content_failures',
                           'historical_branch_switch_is_not_reconstructed_from_the_current_head',
                           'exact_revisions_survive_above_javascript_integer_range_and_reject_sqlite_overflow',
                           'complete_observation_wire_fits_budget_for_maximum_request_keys',
                           'imported_v1_result_and_event_kind_share_the_canonical_verifier',
                           'imported_v1_observation_and_recovery_need_no_source_or_cas_after_reopen',
                           'typed_creation_rejects_generic_apply_binding_markers_without_repair'),
 'apply_binding_migration': ('schema3_upgrade_preserves_both_legacy_families_and_extensions',
                             'reserved_legacy_columns_are_rejected_before_wal_or_schema_changes',
                             'incomplete_or_misdeclared_schema4_columns_fail_before_wal',
                             'noncanonical_marker_constraints_are_rejected_without_probing_writes',
                             'real_quoted_constraint_and_harmless_extensions_remain_admissible',
                             'future_binding_schema_is_never_adopted_or_downgraded',
                             'schema3_import_recovery_remains_source_free_and_unverified_after_migration')})
BINDING4_UNIT_CASES = ('apply_receipt_binding::tests::observation_succeeds_on_query_only_connection_without_sql_changes',
 'apply_receipt_binding::tests::observation_keeps_one_snapshot_during_real_peer_commit',
 'apply_receipt_binding::tests::resealed_noncanonical_decimal_is_still_invalid',
 'apply_receipt_binding::tests::sqlite_statement_failure_is_not_content_failure_or_absence',
 'apply_receipt_binding::tests::schema_constraint_tokens_cannot_be_supplied_by_literals_or_comments',
 'apply_receipt_binding::tests::schema_inspection_bounds_ddl_tokens_and_nesting',
 'apply_receipt_binding::tests::every_legacy_version_rejects_reserved_hidden_and_case_variant_columns')
BINDING4_PHASE_CASES = tuple(CHECKPOINTS)
BINDING4_DDL_CASE = "apply_crash_tests::binding_migration_ddl_rolls_back_after_abrupt_exit"
BINDING4_BASELINE_CASE = "versioned_binding_rejects_same_change_from_a_later_execution"
BINDING4_CORE_GROUPS = ("integrity12", "binding13", "migration7", "binding-unit7",
                       *(f"phase:{name}" for name in BINDING4_PHASE_CASES), "ddl")
BINDING4_PROCESS_GROUPS = (*BINDING4_CORE_GROUPS[4:],)
# The current product is a separate reviewed source, not a relabelled historical run.
CURRENT_SOURCE_SHA = "be7d0377425f9eec201852450b5a7c53e4a1f403"
CURRENT_SOURCE_TREE = "5f9e92f6640a39c7adddb249501399cc97e958f8"
CURRENT_REVIEWED = MappingProxyType({'Cargo.lock': 'cf5d0fe15f6a1ca3aa63681f9a1d9b5d646efda9bee214243f1b2efa77119f1b',
 'Cargo.toml': '455492e78f913df8979a470f2538d0a2bcf5abd293b4937a6e1342233ee305e0',
 'SOURCE_LOCK.json': 'f0e42c064c71a2307b4043b8b345ff54b36f8df2692db5809ce1e85635fdc1c1',
 'crates/storage/Cargo.toml': '02d305e94bf37f92d643ec9b7bae0eeab104dffcea08be44029d86f4bd76b8cb',
 'crates/storage/src/apply_crash_tests.rs': '9febb826879b1180ddd8afc84256a7fd6d7b76c3c5769c726ad612c2adc3e974',
 'crates/storage/src/apply_receipt_binding.rs': '9922802f0deaa32c9e2b2dee7d3d0d7fbcfdfa58b2047b506f0432fd9db4d4af',
 'crates/storage/src/file_import.rs': '037564e6894a59075f295739ae93967825c6a96429b3f113b53ed87b6d4c9039',
 'crates/storage/src/lib.rs': 'db8976efd0cccbb2a3c36f0a4b21dd78738e76166297cd21cfd33f7f21bcd8a4',
 'crates/storage/src/production.rs': '3d106e93c8c4a5fe163f9bcb8443b8d364d88db009f856de6001971461dcafa9',
 'crates/storage/src/project_creation.rs': '8b025a21443ba73ef9f712264e010d3fda605ebf37df71d10c7d79d2063b0dc9',
 'crates/storage/tests/apply_binding_migration.rs': '25500c33cbb4ad19079ad8e5bfa396bcf21afb7fd9595818493c082d11313ace',
 'crates/storage/tests/apply_receipt_binding.rs': 'b74d2e616e880da1b9be515d3dcb5d92332af557f291b88bc9b34245b6c45274',
 'crates/storage/tests/apply_receipt_integrity.rs': 'cc995895ccfae9c8f584e15555e38aeccdca684f3afcc5b64d72b69619428883',
 'crates/storage/tests/schema_migration_atomicity.rs': '0df1d3e87dbd2b1b14687ed58cd1a51fc60e58aa56d3c1467cb5951a8f3b870b',
 'docs/reliability/apply-receipt-binding.md': '7fe6cecacb86b0965ebcb4ab0dd71c581d3be417c8295c3c45bc839d1c74c92c'})
BINDING4_EXECUTION_MODES = ("binding4-core44", "binding4-process", "current-process")
MODES = ("source", "historical5fa", "binding4-core44", "binding4-process",
         "current-source", "current-process")



def binding4_product_profile(mode):
    """Select an exact reviewed product without changing historical identities."""
    require(mode in BINDING4_EXECUTION_MODES, "unknown binding4 product mode")
    if mode == "current-process":
        return CURRENT_SOURCE_SHA, CURRENT_SOURCE_TREE, CURRENT_REVIEWED
    return BINDING4_SOURCE_SHA, BINDING4_SOURCE_TREE, BINDING4_REVIEWED



def select_mode(mode, source_sha, platforms):
    require(mode in MODES, "unknown mode")
    if mode == "source":
        require(source_sha == SOURCE_SHA and platforms == "both",
                "source mode requires its explicit default selection")
    elif mode == "historical5fa":
        require(source_sha == SOURCE_SHA and platforms in {"both", "windows", "macos"},
                "historical product or platform differs")
    elif mode == "binding4-core44":
        require(source_sha == BINDING4_SOURCE_SHA and platforms == "linux",
                "core44 requires reviewed binding4 source and Linux")
    elif mode == "binding4-process":
        require(source_sha == BINDING4_SOURCE_SHA and platforms in {"both", "windows", "macos"},
                "binding4 process source or platform differs")
    elif mode == "current-source":
        require(source_sha == CURRENT_SOURCE_SHA and platforms == "both",
                "current source controls require the exact reviewed product and both selection")
    else:
        require(source_sha == CURRENT_SOURCE_SHA and platforms in {"both", "windows", "macos"},
                "current process source or platform differs")
    rows = [{"platform": "windows", "runner": "windows-2025"},
            {"platform": "macos", "runner": "macos-15"}]
    matrix = {"include": [row for row in rows if platforms in {"both", row["platform"]}]} \
        if mode in {"historical5fa", "binding4-process", "current-process"} else {"include": []}
    return {"mode": mode, "source_sha": source_sha, "platforms": platforms, "matrix": matrix,
            "tests_executed": 0, "formal_acceptance": False}


def binding4_context(env, source_sha, mode, runtime_platform=None):
    runtime_platform = sys.platform if runtime_platform is None else runtime_platform
    platform = {"linux": ("Linux", "linux"), "win32": ("Windows", "windows"),
                "darwin": ("macOS", "macos")}.get(runtime_platform)
    require(platform is not None, "unsupported binding4 runtime platform")
    select_mode(mode, source_sha, platform[1])
    require(mode in BINDING4_EXECUTION_MODES, "not a binding4 execution mode")
    require(env.get("RUNNER_OS") == platform[0], "binding4 runner OS differs")
    require(env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
            "binding4 execution requires explicit dispatch")
    for key in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"):
        require(SHA.fullmatch(env.get(key, "")), "missing full workflow SHA")
    require(env["GITHUB_SHA"] == env["GITHUB_WORKFLOW_SHA"], "workflow checkout identity differs")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]*", env.get(key, "")), "missing run or attempt")
    job = "storage-apply-binding-core" if mode == "binding4-core44" else "storage-apply-recovery"
    require(env.get("GITHUB_JOB") == job, "wrong logical binding4 job")
    require(bool(env.get("GITHUB_REPOSITORY")) and env.get("GITHUB_WORKFLOW_REF", "").startswith(
        env["GITHUB_REPOSITORY"] + "/" + WORKFLOW + "@"), "wrong workflow identity")
    keys = ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_REPOSITORY",
            "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "RUNNER_OS", "RUNNER_ARCH",
            "RUNNER_NAME", "ImageOS", "ImageVersion")
    return {"product_source_sha": source_sha, "mode": mode, "cohort": "binding4",
            "baseline_source_sha": BASELINE_SOURCE_SHA if mode == "binding4-core44" else None,
            **{key: env.get(key, "") for key in keys}}



def binding4_command_context(context, role):
    require(role in {"product", "baseline"}, "unknown command source role")
    source_sha, _tree, _reviewed = binding4_product_profile(context["mode"])
    require(context.get("product_source_sha") == source_sha
            and context.get("cohort") == "binding4", "command product source differs")
    require(role != "baseline" or context["mode"] == "binding4-core44", "unselected baseline")
    return {**context, "command_source_role": role,
            "command_source_sha": BASELINE_SOURCE_SHA if role == "baseline" else source_sha}



def binding4_source_snapshot(repo, role, *, mode="binding4-process"):
    require(role in {"product", "baseline"}, "unknown snapshot source role")
    if role == "product":
        sha, tree, reviewed = binding4_product_profile(mode)
    else:
        require(mode in {"binding4-core44", "binding4-process"}, "unselected current baseline")
        sha, tree, reviewed = BASELINE_SOURCE_SHA, BASELINE_SOURCE_TREE, BASELINE_REVIEWED
    observed = source_snapshot(repo, sha, tuple(reviewed), tree)
    require(observed["sha"] == sha and observed["tree"] == tree
            and set(observed["files"]) == set(reviewed), "binding4 source inventory differs")
    require(all(observed["files"][name]["sha256"] == value for name, value in reviewed.items()),
            "binding4 reviewed source bytes differ")
    return observed


def select_binding4_binaries(raw, code, repo, target, mode, baseline=False):
    require(mode in BINDING4_EXECUTION_MODES, "unknown binary selection mode")
    require(not baseline or mode == "binding4-core44", "unselected baseline build")
    require(code == 0, "compilation did not succeed")
    messages = [strict_json(line) for line in raw.decode().splitlines() if line.strip()]
    finished = [x for x in messages if x.get("reason") == "build-finished"]
    require(len(finished) == 1 and finished[0].get("success") is True, "missing compile completion")
    targets = ("apply_receipt_binding",) if baseline else (
        ("motionwright_storage", *BINDING4_INTEGRATIONS) if mode == "binding4-core44"
        else ("motionwright_storage",))
    result = {}
    if "motionwright_storage" in targets:
        result["motionwright_storage"] = select_binary(raw, code, repo, target)
    relevant = [x for x in messages if x.get("reason") == "compiler-artifact"
                and x.get("executable") and isinstance(x.get("manifest_path"), str)
                and Path(x["manifest_path"]).absolute() == repo / "crates/storage/Cargo.toml"]
    require({x.get("target", {}).get("name") for x in relevant} == set(targets)
            and len(relevant) == len(targets), "unexpected or duplicate selected test target")
    for name in targets:
        if name == "motionwright_storage":
            continue
        candidates = [x for x in messages if x.get("reason") == "compiler-artifact"
                      and x.get("executable") and x.get("target", {}).get("name") == name]
        require(len(candidates) == 1, "integration binary is missing or ambiguous")
        item = candidates[0]
        require(item["target"].get("kind") == ["test"] and item.get("profile", {}).get("test") is True,
                "not an integration test binary")
        require(Path(item["manifest_path"]).absolute() == repo / "crates/storage/Cargo.toml"
                and Path(item["target"]["src_path"]).absolute() == repo / "crates/storage/tests" / (name + ".rs"),
                "integration binary belongs to another source checkout")
        path = Path(item["executable"]).absolute()
        observed = fingerprint(path, target)
        require(observed["bytes"] > 0, "empty integration binary")
        result[name] = {"absolute_path": str(path), **observed}
    require(len({x["absolute_path"] for x in result.values()}) == len(targets), "test binary path reused")
    return result


def binding4_roster(raw, code, target, baseline=False):
    require(code == 0, "test enumeration did not succeed")
    names = tuple(re.findall(r"(?m)^([^\s]+): test$", raw.decode().replace("\r\n", "\n")))
    require(names and len(names) == len(set(names)), "duplicate or absent enumerated tests")
    if baseline:
        require(target == "apply_receipt_binding" and names == (BINDING4_BASELINE_CASE,),
                "baseline roster is not the original one-case control")
    elif target == "motionwright_storage":
        require({x for x in names if x.startswith("apply_receipt_binding::tests::")}
                == set(BINDING4_UNIT_CASES), "binding unit roster changed")
        require({x for x in names if x.startswith("apply_crash_tests::")}
                == set((*BINDING4_PHASE_CASES, BINDING4_DDL_CASE)), "binding process roster changed")
    else:
        require(target in BINDING4_INTEGRATIONS and set(names) == set(BINDING4_INTEGRATIONS[target]),
                "integration roster changed")
    return names


def binding4_group(group, binary):
    groups = {"integrity12": ("apply_receipt_integrity", 120),
              "binding13": ("apply_receipt_binding", 180),
              "migration7": ("apply_binding_migration", 180)}
    if group in groups:
        target, timeout = groups[group]
        return target, BINDING4_INTEGRATIONS[target], [binary["absolute_path"], "--test-threads=1"], timeout
    if group == "binding-unit7":
        return "motionwright_storage", BINDING4_UNIT_CASES, [
            binary["absolute_path"], "apply_receipt_binding::tests::", "--test-threads=1"], 180
    if group == "baseline":
        return "apply_receipt_binding", (BINDING4_BASELINE_CASE,), [
            binary["absolute_path"], "--exact", BINDING4_BASELINE_CASE, "--nocapture", "--test-threads=1"], 120
    if group == "ddl":
        return "motionwright_storage", (BINDING4_DDL_CASE,), [
            binary["absolute_path"], "--exact", BINDING4_DDL_CASE, "--test-threads=1"], 120
    require(group.startswith("phase:") and group[6:] in BINDING4_PHASE_CASES, "unknown selected test group")
    name = group[6:]
    return "motionwright_storage", (name,), [
        binary["absolute_path"], "--exact", name, "--nocapture", "--test-threads=1"], 120


def admit_binding4_group(group, raw, command, binary, after, enumerated, context):
    target, wanted, argv, _timeout = binding4_group(group, binary)
    baseline = group == "baseline"
    role = "baseline" if baseline else "product"
    source_sha, _tree, _reviewed = binding4_product_profile(context.get("mode"))
    require(context.get("product_source_sha") == source_sha
            and context.get("cohort") == "binding4"
            and context.get("command_source_role") == role
            and context.get("command_source_sha") == (BASELINE_SOURCE_SHA if baseline else source_sha),
            "test source context differs")
    allowed = BINDING4_CORE_GROUPS if context["mode"] == "binding4-core44" else BINDING4_PROCESS_GROUPS
    require(group in allowed or (baseline and context["mode"] == "binding4-core44"), "unselected group")
    binding4_roster("".join(x + ": test\n" for x in enumerated).encode(), 0, target, baseline)
    require(type(command.get("exit_code")) is int and command["exit_code"] == (101 if baseline else 0)
            and command.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED", "test failed or UNKNOWN; no retry")
    require(command.get("log_custody") == "VERIFIED_SNAPSHOT", "original log custody incomplete")
    require(command.get("context") == context and binary == after and command.get("argv") == argv,
            "test command, context or executed binary changed")
    text = ANSI.sub("", raw.decode().replace("\r\n", "\n"))
    results = re.findall(r"(?m)^test (.+) \.\.\. (ok|FAILED|ignored)$", text)
    summaries = re.findall(r"(?m)^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; "
                           r"(\d+) ignored; (\d+) measured; (\d+) filtered out;", text)
    state = "FAILED" if baseline else "ok"
    require(len(results) == len(wanted) and len({x[0] for x in results}) == len(wanted)
            and set(results) == {(name, state) for name in wanted} and len(summaries) == 1,
            "selected results missing, repeated or unsuccessful")
    counts = (0, 1, 0, 0, 0) if baseline else (len(wanted), 0, 0, 0, len(enumerated) - len(wanted))
    require(summaries[0][0] == state and tuple(map(int, summaries[0][1:])) == counts,
            "selected summary differs from actual enumeration")
    if baseline:
        require("accepted corrupt known result: Ok(" in text
                and re.search(r"apply_receipt_binding\.rs:90(?::\d+)?", text),
                "baseline failed for a different cause")
    result = {"group": group, "target": target, "cases": list(wanted), "context": context,
              "status": "EXPECTED_CAUSAL_FAILURE_OBSERVED" if baseline else "PASS",
              "binary_sha256": binary["sha256"], "command": command}
    if group == "ddl":
        result["evidence_kind"] = "source-bound parent assertions for two migration child exits86"
        result["retained_child_originals"] = 0
        result["retained_phase_jsons"] = 0
    return result


def finish_binding4(before, after, binaries, results, rosters, context, baseline_result, baseline_binary=None):
    require(before == after, "source inputs changed during execution")
    mode = context["mode"]
    source_sha, tree, reviewed = binding4_product_profile(mode)
    require(before["product"]["sha"] == context["product_source_sha"] == source_sha
            and before["product"]["tree"] == tree
            and before["gate"]["sha"] == context["GITHUB_SHA"], "current source binding missing")
    require(set(before["product"]["files"]) == set(reviewed)
            and all(before["product"]["files"][p]["sha256"] == h for p, h in reviewed.items()),
            "reviewed product evidence differs")
    groups = BINDING4_CORE_GROUPS if mode == "binding4-core44" else BINDING4_PROCESS_GROUPS
    expected_binaries = {"motionwright_storage", *BINDING4_INTEGRATIONS} if mode == "binding4-core44" else {"motionwright_storage"}
    require(set(binaries) == expected_binaries and set(rosters) == expected_binaries, "selected binary set differs")
    require(all(value.get("bytes", 0) > 0 and re.fullmatch(r"[0-9a-f]{64}", value.get("sha256", ""))
                for value in binaries.values()), "selected binary custody missing")
    require(tuple(x.get("group") for x in results) == groups, "missing, duplicate or foreign selected group")
    command_context = binding4_command_context(context, "product")
    for row in results:
        target, wanted, argv, _timeout = binding4_group(row["group"], binaries[row["target"]])
        binding4_roster("".join(x + ": test\n" for x in rosters[target]).encode(), 0, target)
        require(row.get("status") == "PASS" and row.get("target") == target
                and row.get("cases") == list(wanted) and row.get("context") == command_context
                and row.get("binary_sha256") == binaries[target]["sha256"]
                and row["command"].get("context") == command_context
                and type(row["command"].get("exit_code")) is int and row["command"]["exit_code"] == 0
                and row["command"].get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
                and row["command"].get("log_custody") == "VERIFIED_SNAPSHOT"
                and row["command"].get("argv") == argv
                and row["command"].get("cwd") == before["product"]["checkout"],
                "selected result is not bound to the current attempt and binary")
    phase_rows = [x for x in results if x["group"].startswith("phase:")]
    require(len(phase_rows) == 4 and len({x.get("phase_custody", {}).get("request_id") for x in phase_rows}) == 4,
            "phase request reused or missing")
    for row in phase_rows:
        name = row["group"][6:]
        require(row.get("phase_custody", {}).get("status") == "COMPLETE_OBSERVED_PHASE_CUSTODY"
                and row["phase_custody"].get("test_name") == name
                and row["phase_custody"].get("checkpoint") == CHECKPOINTS[name],
                "current phase originals are incomplete")
    ddl = results[-1]
    require(ddl["group"] == "ddl" and ddl.get("retained_child_originals") == 0
            and ddl.get("retained_phase_jsons") == 0 and "phase_custody" not in ddl,
            "DDL parent must not claim independently retained child originals")
    if mode == "binding4-core44":
        require(before["baseline"]["sha"] == BASELINE_SOURCE_SHA
                and before["baseline"]["tree"] == BASELINE_SOURCE_TREE
                and all(before["baseline"]["files"][p]["sha256"] == h for p, h in BASELINE_REVIEWED.items()),
                "baseline source binding missing")
        require(baseline_result and baseline_result.get("status") == "EXPECTED_CAUSAL_FAILURE_OBSERVED"
                and baseline_result.get("group") == "baseline"
                and baseline_result.get("cases") == [BINDING4_BASELINE_CASE]
                and baseline_result.get("context") == binding4_command_context(context, "baseline")
                and baseline_binary and baseline_binary.get("bytes", 0) > 0
                and baseline_result.get("binary_sha256") == baseline_binary.get("sha256")
                and baseline_result.get("command", {}).get("cwd") == before["baseline"]["checkout"]
                and baseline_result["command"].get("context") == binding4_command_context(context, "baseline")
                and baseline_result["command"].get("argv") == binding4_group("baseline", baseline_binary)[2]
                and baseline_result["command"].get("exit_code") == 101
                and baseline_result["command"].get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
                and baseline_result["command"].get("log_custody") == "VERIFIED_SNAPSHOT",
                "causal baseline was not admitted")
    else:
        require(baseline_result is None and baseline_binary is None and "baseline" not in before, "unselected baseline result")
    wanted = 44 if mode == "binding4-core44" else 5
    require(sum(len(x["cases"]) for x in results) == wanted, "selected case count differs")
    return {"status": "PASS_EXACT_SOURCE_BINDING4_CORE44" if mode == "binding4-core44"
            else "PASS_EXACT_SOURCE_BINDING4_PROCESS5",
            "required": wanted, "executed": wanted, "passed": wanted, "failed": 0, "ignored": 0,
            "baseline_expected_failure_observed": mode == "binding4-core44",
            "phase_cases": 4, "phase_jsons_retained": 20, "child_process_originals_retained": 12,
            "ddl_parent_cases": 1, "ddl_retained_child_originals": 0, "ddl_retained_phase_jsons": 0,
            "receipt_snapshot_fields": 6, "sql_null_distinction_proven_by_phase_strings": False,
            "raw_sqlite_snapshots_retained": False, "full_cas_archive_retained": False,
            "power_loss_atomicity": "NOT_RUN", "installed_application": "NOT_RUN",
            "other_source_or_platform_claims": False, "formal_acceptance": False,
            "human_acceptance": False, "release_authorized": False}


def run_binding4(repo, output, source_sha, mode, baseline_repo=None):
    context = binding4_context(dict(os.environ), source_sha, mode)
    require((baseline_repo is not None) == (mode == "binding4-core44"), "baseline checkout selection differs")
    gate = Path(__file__).resolve().parents[1]
    repo, output = repo.resolve(strict=True), output.resolve()
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    roots = {"product": repo, "gate": gate}
    if baseline_repo is not None:
        roots["baseline"] = baseline_repo.resolve(strict=True)
    require(len(set(roots.values())) == len(roots) and all(p.is_relative_to(workspace) for p in roots.values())
            and output.is_relative_to(workspace)
            and all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in roots.values()),
            "product, baseline, gate and evidence must be separate owned paths")
    require(all(not left.is_relative_to(right) for key, left in roots.items()
                for other, right in roots.items() if key != other), "source checkouts cannot nest")
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": "motionwright.storage-apply-source-authority/2", "status": "FAIL",
               "product_source_sha": source_sha, "context": context, "phase": "source-preflight",
               "case_processes_started": 0, "results": [], "commands": [], "baseline": None,
               "formal_acceptance": False, "human_acceptance": False, "release_authorized": False}
    write_new(output / "started.json", {**receipt, "started_utc": now()})
    def snapshot():
        product = binding4_source_snapshot(repo, "product", mode=mode) \
            if mode == "current-process" else binding4_source_snapshot(repo, "product")
        result = {"product": product,
                  "gate": source_snapshot(gate, context["GITHUB_SHA"], GATE_FILES)}
        if "baseline" in roots:
            result["baseline"] = binding4_source_snapshot(roots["baseline"], "baseline")
        return result
    def invoke(argv, root, env, label, timeout, role, is_case=False):
        cmd = execute(argv, root, env, output, label, timeout, binding4_command_context(context, role))
        receipt["commands"].append(cmd)
        if is_case and cmd["observation"] != "NOT_STARTED_LAUNCH_ERROR":
            receipt["case_processes_started"] += 1
        if cmd["observation"] == "UNKNOWN_TIMEOUT_NO_RETRY":
            receipt["status"] = "UNKNOWN"
        require(cmd["observation"] == "DIRECT_PROCESS_EXIT_OBSERVED", "command failed or UNKNOWN; no retry")
        return cmd
    def retained_binary(binary, target, label):
        path = Path(binary["absolute_path"])
        require({"absolute_path": str(path), **fingerprint(path, target)} == binary, "binary changed before copy")
        destination = output / (label + (".exe" if context["RUNNER_OS"] == "Windows" else ""))
        shutil.copyfile(path, destination)
        observed = fingerprint(destination, output)
        require((observed["bytes"], observed["sha256"]) == (binary["bytes"], binary["sha256"]),
                "retained binary copy differs")
        return observed
    def execute_group(group, binary, enumerated, root, target, env, label, role):
        require({"absolute_path": binary["absolute_path"],
                 **fingerprint(Path(binary["absolute_path"]), target)} == binary, "binary changed before execution")
        _target, _wanted, argv, timeout = binding4_group(group, binary)
        cmd = invoke(argv, root, env, label, timeout, role, True)
        after_binary = {"absolute_path": binary["absolute_path"],
                        **fingerprint(Path(binary["absolute_path"]), target)}
        row = admit_binding4_group(group, read_original(output, cmd, "stdout") + b"\n"
                                   + read_original(output, cmd, "stderr"), cmd, binary, after_binary,
                                   enumerated, binding4_command_context(context, role))
        return row
    try:
        before = snapshot()
        receipt["source_before"] = before
        write_new(output / "source-before.json", before)
        for label, root in roots.items():
            for name, expected in before[label]["files"].items():
                destination = output / "sources" / label / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, destination)
                observed = fingerprint(destination, output)
                require((observed["bytes"], observed["sha256"]) == (expected["bytes"], expected["sha256"]),
                        "retained source copy differs")
        target = output.parent / "t"
        require(Path(os.environ["CARGO_TARGET_DIR"]).resolve() == target.resolve(), "foreign build target")
        target.mkdir(exist_ok=False)
        env = isolated_build_environment(os.environ)
        receipt["build_environment"] = {k: env.get(k, "") for k in
            ("CARGO_HOME", "CARGO_TARGET_DIR", "RUSTUP_TOOLCHAIN", "CARGO_INCREMENTAL",
             "CARGO_PROFILE_TEST_DEBUG", "CARGO_PROFILE_DEV_DEBUG")}
        for label, argv in (("rustc-version", ["rustc", "-Vv"]), ("cargo-version", ["cargo", "-V"])):
            require(invoke(argv, repo, env, label, 45, "product")["exit_code"] == 0, "tool identity failed")
        if "baseline" in roots:
            receipt["phase"] = "baseline"
            base_target = output.parent / "bt"
            base_target.mkdir(exist_ok=False)
            base_env = dict(env, CARGO_TARGET_DIR=str(base_target))
            base = roots["baseline"]
            command = invoke(["cargo", "test", "--locked", "-p", "motionwright-storage", "--test",
                              "apply_receipt_binding", "--no-run", "--message-format=json"],
                             base, base_env, "baseline-compile", 1500, "baseline")
            selected = select_binding4_binaries(read_original(output, command, "stdout"),
                                                command["exit_code"], base, base_target, mode, True)
            base_binary = selected["apply_receipt_binding"]
            receipt["baseline_binary"] = base_binary
            receipt["retained_baseline_binary"] = retained_binary(base_binary, base_target, "baseline-test-binary")
            command = invoke([base_binary["absolute_path"], "--list", "--format", "terse"],
                             base, base_env, "baseline-roster", 45, "baseline")
            base_roster = binding4_roster(read_original(output, command, "stdout"), command["exit_code"],
                                         "apply_receipt_binding", True)
            require(snapshot() == before, "source changed during baseline compilation")
            receipt["baseline"] = execute_group("baseline", base_binary, base_roster, base, base_target,
                                               base_env, "baseline-causal", "baseline")
            write_new(output / "baseline.receipt.json", receipt["baseline"])
        receipt["phase"] = "compile"
        argv = ["cargo", "test", "--locked", "-p", "motionwright-storage", "--lib"]
        if mode == "binding4-core44":
            for name in BINDING4_INTEGRATIONS:
                argv += ["--test", name]
        command = invoke([*argv, "--no-run", "--message-format=json"], repo, env, "compile", 1500, "product")
        binaries = select_binding4_binaries(read_original(output, command, "stdout"),
                                            command["exit_code"], repo, target, mode)
        receipt["binaries"], receipt["retained_binaries"], rosters = binaries, {}, {}
        for name, binary in binaries.items():
            receipt["retained_binaries"][name] = retained_binary(binary, target, "executed-" + name)
            command = invoke([binary["absolute_path"], "--list", "--format", "terse"], repo, env,
                             "roster-" + name, 45, "product")
            rosters[name] = binding4_roster(read_original(output, command, "stdout"), command["exit_code"], name)
        receipt["enumerated_cases"] = rosters
        require(snapshot() == before, "source changed during compilation")
        receipt["phase"] = "cases"
        phase_root = output / "phases"
        phase_root.mkdir(exist_ok=False)
        require(phase_root.resolve(strict=True) == phase_root, "phase root must be canonical")
        groups = BINDING4_CORE_GROUPS if mode == "binding4-core44" else BINDING4_PROCESS_GROUPS
        for number, group in enumerate(groups, 1):
            target_name, _wanted, _argv, _timeout = binding4_group(group, {"absolute_path": "not executed"})
            binary = binaries[target_name]
            temporary = output.parent / "x" / ("binding4-" + str(number))
            temporary.mkdir(parents=True, exist_ok=False)
            case_env = dict(env, TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))
            if group.startswith("phase:"):
                name = group[6:]
                require(not (phase_root / CHECKPOINTS[name]).exists(), "phase checkpoint already exists; no reuse")
                case_env[EVIDENCE_ROOT] = str(phase_root)
            row = execute_group(group, binary, rosters[target_name], repo, target, case_env,
                                f"case-{number:02d}", "product")
            if group.startswith("phase:"):
                row["phase_custody"] = admit_phase_custody(phase_root, group[6:], "binding4")
            receipt["results"].append(row)
            write_new(output / f"case-{number:02d}.receipt.json", row)
        receipt["phase"] = "postcheck"
        after = snapshot()
        receipt["source_after"] = after
        write_new(output / "source-after.json", after)
        for name, binary in binaries.items():
            require({"absolute_path": binary["absolute_path"],
                     **fingerprint(Path(binary["absolute_path"]), target)} == binary, "binary changed after execution")
        if "baseline" in roots:
            require({"absolute_path": base_binary["absolute_path"],
                     **fingerprint(Path(base_binary["absolute_path"]), base_target)} == base_binary,
                    "baseline binary changed after execution")
        for original_command in receipt["commands"]:
            for stream in ("stdout", "stderr"):
                read_original(output, original_command, stream)
        for row in receipt["results"]:
            if row["group"].startswith("phase:"):
                require(admit_phase_custody(phase_root, row["group"][6:], "binding4") == row["phase_custody"],
                        "phase originals changed after their case")
        require({p.name for p in phase_root.iterdir()} == set(CHECKPOINTS.values()),
                "phase checkpoint inventory incomplete or unexpected")
        receipt.update(finish_binding4(before, after, binaries, receipt["results"], rosters, context,
                                       receipt["baseline"], base_binary if "baseline" in roots else None))
        receipt["phase"] = "complete"
    except Exception as error:
        receipt["error"] = type(error).__name__ + ": " + str(error)
    receipt["finished_utc"] = now()
    receipt["operations_retried"] = False
    write_new(output / "receipt.json", receipt)
    print(json.dumps({k: receipt.get(k) for k in
                      ("status", "product_source_sha", "case_processes_started", "passed", "error")}))
    return 0 if receipt["status"] in {"PASS_EXACT_SOURCE_BINDING4_CORE44",
                                     "PASS_EXACT_SOURCE_BINDING4_PROCESS5"} else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--mode", choices=MODES, default="historical5fa")
    parser.add_argument("--baseline-repo", type=Path)
    args = parser.parse_args()
    if args.mode in {"source", "current-source"}:
        parser.error("source modes run complete workflow G0 controls; they are not product executions")
    if args.mode == "historical5fa":
        if args.baseline_repo is not None:
            parser.error("historical mode does not select a baseline checkout")
        return run(args.repo, args.output, args.source_sha)
    return run_binding4(args.repo, args.output, args.source_sha, args.mode, args.baseline_repo)


if __name__ == "__main__":
    raise SystemExit(main())
