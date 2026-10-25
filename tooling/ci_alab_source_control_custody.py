#!/usr/bin/env python3
"""Retain only the fixed ALab unittest reports/streams in a closed source artifact.

No product, HTTP, media, ffprobe, decoder, NLE or test process is invoked here.
This transport preserves observed reports; it grants no native/release verdict.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile

if __package__:
    from . import ci_test_runner as runner
else:
    import ci_test_runner as runner

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/v05-otio-real-media.yml"
HELPER = "tooling/ci_alab_source_control_custody.py"
CONTROL = "tooling/tests/test_ci_alab_source_control_custody.py"
RUNNER = "tooling/ci_test_runner.py"
CALLER = "tooling/tests/test_ci_otio_alab_readonly.py"
BRIDGE = "tooling/otio-media/test_bridge.py"
CUSTODY_TEST_COUNT = 18
CALLER_CASES = ((CALLER, 45), (CONTROL, CUSTODY_TEST_COUNT))
PROFILES = {
    "g0": {"job": "verified-native-rust-otio-and-open-timeline-io",
           "reports": (("alab-caller-g0", CALLER_CASES),)},
    "alab": {"job": "admitted-native-kdenlive-gui", "reports": (
        ("alab-caller-source-controls", CALLER_CASES),
        ("alab-owner-source-controls", ((BRIDGE, 94),)))},
}
REPORT_LIMIT, STREAM_LIMIT, TOTAL_LIMIT = 512 * 1024, 1024 * 1024, 8 * 1024 * 1024
OUTPUT_NAMES = ("source-control-originals.tar", "source-control-custody.json")


class Rejected(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise Rejected(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def strict(raw):
    def pairs(items):
        value = {}
        for key, val in items:
            require(key not in value, "duplicate JSON member")
            value[key] = val
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Rejected("non-finite JSON")))


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class FlatRoot:
    """One held canonical directory; accepts only a fixed single-component leaf."""
    def __init__(self, path):
        self.path = Path(path)
        require(os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"), "Linux/POSIX required")
        require(self.path.is_absolute() and self.path.resolve(strict=True) == self.path and
                all(ord(c) >= 32 and ord(c) != 127 for c in str(self.path)), "noncanonical root")
        for parent in (self.path, *self.path.parents):
            require(stat.S_ISDIR(parent.lstat().st_mode), "linked or non-directory parent")
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.identity = os.fstat(self.fd)
        try:
            self.check()
        except BaseException:
            os.close(self.fd)
            raise

    def check(self):
        require(self.path.resolve(strict=True) == self.path and
                (os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino) ==
                (self.path.lstat().st_dev, self.path.lstat().st_ino), "root changed")

    def read(self, leaf, limit):
        require(type(leaf) is str and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", leaf), "invalid fixed leaf")
        self.check()
        before = os.stat(leaf, dir_fd=self.fd, follow_symlinks=False)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= limit,
                "input is not a bounded ordinary single-link file")
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            require(stamp(os.fstat(fd)) == stamp(before), "input changed during open")
            raw = bytearray()
            while block := os.read(fd, min(65536, limit + 1 - len(raw))):
                raw.extend(block)
                require(len(raw) <= limit, "input grew beyond bound")
            require(len(raw) == before.st_size and stamp(os.fstat(fd)) == stamp(before) ==
                    stamp(os.stat(leaf, dir_fd=self.fd, follow_symlinks=False)), "input changed during read")
            self.check()
            return bytes(raw)
        finally:
            os.close(fd)

    def close(self):
        os.close(self.fd)


def expected_names(profile):
    result = {}
    for stem, cases in PROFILES[profile]["reports"]:
        result[stem + ".json"] = REPORT_LIMIT
        for index, _ in enumerate(cases):
            for channel in ("stdout", "stderr"):
                result[f"{stem}-{index:02d}-{channel}.log"] = STREAM_LIMIT
    return result


def context(environment, profile, expected_sha):
    require(profile in PROFILES and type(expected_sha) is str and re.fullmatch(r"[0-9a-f]{40}", expected_sha), "scope or full SHA required")
    for key, expected in (("GITHUB_ACTIONS", "true"), ("RUNNER_OS", "Linux"),
                          ("GITHUB_SHA", expected_sha), ("GITHUB_JOB", PROFILES[profile]["job"])):
        require(environment.get(key) == expected, "workflow context differs:" + key)
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(type(environment.get(key)) is str and re.fullmatch(r"[1-9][0-9]{0,23}", environment[key]), "run identity required")
    if profile == "alab":
        require(environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and environment["GITHUB_RUN_ATTEMPT"] == "1"
                and environment.get("MOTIONWRIGHT_KDENLIVE_GUI_SCOPE") == "external-alab-readonly", "fresh ALab manual selection required")
    return {"source_sha": expected_sha, "run_id": environment["GITHUB_RUN_ID"],
            "run_attempt": environment["GITHUB_RUN_ATTEMPT"], "job": environment["GITHUB_JOB"], "profile": profile,
            "workflow_ref": environment.get("GITHUB_WORKFLOW_REF", "")}


def observed_source(repo, identity):
    source = runner.source_state(repo, identity["source_sha"])
    paths = {WORKFLOW, HELPER, CONTROL, RUNNER}
    paths.update(path for _, cases in PROFILES[identity["profile"]]["reports"] for path, _ in cases)
    hashes, rosters = {}, {}
    expected = {path: count for _, cases in PROFILES[identity["profile"]]["reports"] for path, count in cases}
    for relative in sorted(paths):
        path = repo / relative
        parent = FlatRoot(path.parent)
        try:
            raw = parent.read(path.name, 2 * 1024 * 1024)
        finally:
            parent.close()
        blob = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), "show",
            identity["source_sha"] + ":" + relative], capture_output=True, timeout=20, check=True).stdout
        require(blob == raw, "source is not exact Git bytes:" + relative)
        hashes[relative] = sha(raw)
        if relative in expected:
            names = [Path(relative).stem + "." + cls.name + "." + method.name
                for cls in ast.parse(raw).body if isinstance(cls, ast.ClassDef)
                for method in cls.body if isinstance(method, ast.FunctionDef) and method.name.startswith("test_")]
            require(len(names) == len(set(names)) == expected[relative], "reviewed source-control roster differs")
            rosters[relative] = sorted(names)
    return {**source, "files": hashes, "rosters": rosters}


def verify_report(stem, cases, files, source, identity):
    raw = files[stem + ".json"]
    report = strict(raw)
    require(type(report) is dict and report.get("schema") == "motionwright.unittest-execution/1", "report schema")
    require(report.get("scope") == "Selected unittest entry points; not installed/native/human acceptance", "report scope differs")
    require(report.get("executed_source_sha") == identity["source_sha"] and report.get("tree_sha") == source["tree_sha"], "report SHA/tree differs")
    require(report.get("runner_sha256") == source["files"][RUNNER] and
            report.get("test_definition_sha256") == {path: source["files"][path] for path, _ in cases}, "runner or test source differs")
    require(report.get("require_complete") is True and "source_postcheck_error" not in report, "incomplete source report")
    github = {"GITHUB_SHA": identity["source_sha"], "GITHUB_RUN_ID": identity["run_id"],
              "GITHUB_RUN_ATTEMPT": identity["run_attempt"], "GITHUB_JOB": identity["job"],
              "GITHUB_WORKFLOW_REF": identity["workflow_ref"]}
    require(report.get("github") == github, "report run/job/attempt differs")
    require(type(report.get("steps")) is list and len(report["steps"]) == len(cases), "missing or extra report entry point")
    for index, ((path, total), step) in enumerate(zip(cases, report["steps"])):
        source_path = Path(path)
        expected_command = ["-B", "-E", "-s", "-m", "unittest", "discover", "-s", source_path.parent.as_posix(),
                            "-p", source_path.name, "-v"]
        require(type(step) is dict and step.get("entry_point") == "discovery:" + source_path.name and
                type(step.get("command")) is list and len(step["command"]) == len(expected_command) + 1 and
                type(step["command"][0]) is str and Path(step["command"][0]).is_absolute() and
                step["command"][1:] == expected_command, "unreviewed command or entry point")
        for channel in ("stdout", "stderr"):
            name = f"{stem}-{index:02d}-{channel}.log"
            require(step.get(channel) == {"file": name, "sha256": sha(files[name])}, "missing or changed original stream")
        original_stderr = files[f"{stem}-{index:02d}-stderr.log"].decode("utf-8", "strict").replace("\r\n", "\n")
        nominal = re.findall(r"(?m)^(test_[A-Za-z0-9_]+) \(([A-Za-z0-9_.]+)\) \.\.\. ok$", original_stderr)
        require(len(nominal) == len(set(nominal)) == total and
                sorted(name for _, name in nominal) == source["rosters"][path] and
                all(name.rsplit(".", 1)[-1] == method for method, name in nominal), "nominal source cases missing, duplicate or substituted")
        summary = runner.parse_unittest_summary(original_stderr)
        require(json_bytes(step.get("counts")) == json_bytes(summary), "parsed summary differs")
        require(type(step.get("exit_code")) is int and step["exit_code"] == 0 and step.get("status") == "PASS" and
                summary == {"terminal_status": "OK", "tests_run": total, "passed": total,
                    "failures": 0, "errors": 0, "skipped": 0, "expected_failures": 0, "unexpected_successes": 0},
                "source controls failed, omitted or unknown")
    require(type(report.get("exit_code")) is int and report["exit_code"] == 0 and report.get("status") == "PASS", "report not PASS")
    return {"stem": stem, "source_bound": True, "reported_status": "PASS",
            "tests_run": sum(count for _, count in cases), "execution_credit": "EXISTING_SOURCE_REPORT_ONLY"}


def write_new(parent_fd, leaf, raw):
    fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw); stream.flush(); os.fsync(fd)
        os.fchmod(fd, 0o400)
    finally:
        os.close(fd)


def publish(repo, input_root, output_parent, identity, step_outcome):
    require(step_outcome in ("success", "failure", "cancelled", "skipped"), "step outcome required")
    require(type(identity) is dict and set(identity) == {"source_sha", "run_id", "run_attempt", "job", "profile", "workflow_ref"}
            and identity["profile"] in PROFILES and identity["job"] == PROFILES[identity["profile"]]["job"] and
            type(identity["source_sha"]) is str and re.fullmatch(r"[0-9a-f]{40}", identity["source_sha"]) and
            all(type(identity[k]) is str and re.fullmatch(r"[1-9][0-9]{0,23}", identity[k]) for k in ("run_id", "run_attempt")),
            "closed source-control identity required")
    before = observed_source(repo, identity)
    selected, rejected, total = {}, {}, 0
    held = FlatRoot(input_root)
    try:
        for name, limit in expected_names(identity["profile"]).items():
            try:
                raw = held.read(name, limit)
                require(total + len(raw) <= TOTAL_LIMIT, "total source-control bytes exceeded")
                selected[name], total = raw, total + len(raw)
            except (OSError, ValueError) as error:
                rejected[name] = type(error).__name__
        held.check()
    finally:
        held.close()
    reports = []
    for stem, cases in PROFILES[identity["profile"]]["reports"]:
        # Absence of a receipt is not proof that its test process never started.
        status = "NOT_RUN" if step_outcome == "skipped" else "NOT_OBSERVED"
        try:
            parsed = strict(selected[stem + ".json"])
            require(type(parsed) is dict, "report object required")
            candidate_status = parsed.get("status")
            status = candidate_status if type(candidate_status) is str and candidate_status in (
                "PASS", "PASS_WITH_OMISSIONS", "FAIL", "UNKNOWN") else "INVALID_REPORT"
            require(status != "INVALID_REPORT", "report status invalid")
            row = verify_report(stem, cases, selected, before, identity)
        except (OSError, ValueError, KeyError, TypeError) as error:
            row = {"stem": stem, "source_bound": False, "reported_status": status,
                   "reason": type(error).__name__, "execution_credit": "NONE"}
        reports.append(row)
    source_unchanged = False
    try:
        source_unchanged = observed_source(repo, identity) == before
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    verified = (step_outcome == "success" and not rejected and source_unchanged and all(row["source_bound"] for row in reports))
    original_unknown = step_outcome == "cancelled" or any(row["reported_status"] == "UNKNOWN" for row in reports)
    status = "UNKNOWN" if original_unknown else "SOURCE_REPORTS_BOUND" if verified else "NOT_ADMITTED"
    inventory = {name: {"bytes": len(raw), "sha256": sha(raw)} for name, raw in sorted(selected.items())}
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name, raw in sorted(selected.items()):
            item = tarfile.TarInfo(name)
            item.mode, item.mtime, item.size = 0o600, 0, len(raw)
            archive.addfile(item, io.BytesIO(raw))
    packed = data.getvalue()
    require(len(packed) <= TOTAL_LIMIT + 32768, "bounded archive exceeded")
    manifest = {"schema": "motionwright.alab.source-control-custody/1", "status": status, "source": identity,
        "source_before": before, "source_unchanged": source_unchanged, "source_controls_verified": verified,
        "original_step_outcome": step_outcome, "reports": reports, "originals": inventory, "rejected": rejected,
        "archive": {"file": OUTPUT_NAMES[0], "bytes": len(packed), "sha256": sha(packed)},
        "allowlist": list(expected_names(identity["profile"])), "scope": "Selected unittest source-control originals only",
        "original_execution_repeated": False, "automatic_retry": False, "http_media_ffprobe": "NOT_ACCESSED",
        "native_execution": "NOT_RUN", "candidate_admitted": False, "human_acceptance": False, "release_authorized": False}
    parent = FlatRoot(output_parent)
    folder_name = "alab-source-controls-" + "-".join(identity[k] for k in ("source_sha", "run_id", "run_attempt", "profile"))
    folder = Path(output_parent) / folder_name
    try:
        os.mkdir(folder_name, 0o700, dir_fd=parent.fd)
        output = FlatRoot(folder)
        try:
            write_new(output.fd, OUTPUT_NAMES[0], packed)
            raw_manifest = json_bytes(manifest)
            require(len(raw_manifest) <= REPORT_LIMIT, "manifest size")
            write_new(output.fd, OUTPUT_NAMES[1], raw_manifest)
            require(set(os.listdir(output.fd)) == set(OUTPUT_NAMES), "extra or missing published file")
            require(output.read(OUTPUT_NAMES[0], TOTAL_LIMIT + 32768) == packed and
                    output.read(OUTPUT_NAMES[1], REPORT_LIMIT) == raw_manifest, "published readback differs")
            output.check(); parent.check(); os.fchmod(output.fd, 0o500)
        finally:
            output.close()
    finally:
        parent.close()
    return {"ready": True, "verified": verified, "directory": str(folder), "status": status,
            "manifest_sha256": sha(raw_manifest)}, manifest


def github_output(ready, path):
    parent = FlatRoot(Path(path).parent)
    try:
        leaf = Path(path).name
        info = os.stat(leaf, dir_fd=parent.fd, follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= REPORT_LIMIT, "invalid output channel")
        fd = os.open(leaf, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=parent.fd)
        try:
            require(stamp(os.fstat(fd)) == stamp(info), "output channel changed")
            raw = ("ready=true\nverified=" + str(ready["verified"]).lower() + "\ndirectory=" + ready["directory"] +
                   "\nmanifest_sha256=" + ready["manifest_sha256"] + "\n").encode()
            require(os.write(fd, raw) == len(raw), "short output-channel write")
            os.fsync(fd); parent.check()
        finally:
            os.close(fd)
    finally:
        parent.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--step-outcome", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    identity = context(os.environ, args.profile, args.expected_source)
    root = Path(os.environ["RUNNER_TEMP"])
    ready, _ = publish(ROOT, root, root, identity, args.step_outcome)
    github_output(ready, args.github_output)
    print(json.dumps(ready, sort_keys=True))
    return 0 if ready["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
