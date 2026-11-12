#!/usr/bin/env python3
"""Eight real Rust recovery regressions, before a resume recipient may exist."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat

from native_manim_editable_outer import load_deadline, new_bytes, new_json, original
from native_manim_resume_transfer import ORIGIN, hosted_context
from retain_manim_source_recovery_tool import git, source_tree

SELECTED = tuple(sorted("editable_cycle::recovery::tests::" + name for name in (
    "committed_edit_recovery_exports_saved_source_without_another_commit",
    "committed_edit_recovery_missing_apply_stays_unresolved_without_a_retry",
    "committed_edit_recovery_missing_receipt_never_becomes_known",
    "committed_edit_recovery_rejects_changed_original_bundle",
    "committed_edit_recovery_never_overwrites_a_retained_report",
    "committed_source_resume_default_cas_reopens_in_fresh_store",
    "committed_source_resume_existing_or_shared_root_is_not_reinitialized",
    "committed_source_resume_readback_rejects_changed_committed_receipt",
)))
RENDERER_PATHS = ("crates/manim-profile", "crates/driver-manim-community",
                  "tooling/native_manim_editable_pixels.py", "tooling/native_manim_camera_pixels.py")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(value, code):
    if not value:
        raise ValueError(code)


def folder():
    return Path(os.environ["RUNNER_TEMP"]).resolve(strict=True) / "manim-resume-source-gate"


def source_snapshot(repo, current):
    require(git(repo, "rev-parse", "HEAD").decode().strip() == current["source_sha"]
            and not git(repo, "status", "--porcelain=v2", "--untracked-files=no"),
            "resume_source_must_be_exact_clean_checkout")
    selected, total = {}, 0
    for name, row in sorted(source_tree(repo, current["source_sha"]).items()):
        raw = original(repo / name, 16 * 1024 * 1024)
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        require(blob == row["blob"], "resume_compiled_file_changed")
        total += len(raw)
        require(total <= 256 * 1024 * 1024, "resume_compiled_source_budget")
        selected[name] = {**row, "bytes": len(raw), "sha256": sha(raw)}
    renderer_old = git(repo, "ls-tree", "-r", "-z", ORIGIN["source_sha"], "--", *RENDERER_PATHS)
    renderer_new = git(repo, "ls-tree", "-r", "-z", current["source_sha"], "--", *RENDERER_PATHS)
    require(renderer_old == renderer_new and renderer_old,
            "resume_original_renderer_or_pixel_predicates_changed")
    require(git(repo / "_semwright", "rev-parse", "HEAD").decode().strip() == current["semwright_sha"],
            "resume_exact_sdk_checkout")
    encoded = json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
    return {"files": len(selected), "bytes": total, "sha256": sha(encoded),
            "renderer_origin_sha": ORIGIN["source_sha"], "renderer_tree_sha256": sha(renderer_new),
            "old_renderer_and_original_pixel_predicates_unchanged": True,
            "entire_rust_closure_equal_to_old_41_claimed": False}


def binary_identity(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and not before.st_mode & 0o022 and before.st_mode & 0o111
                and 0 < before.st_size <= 128 * 1024 * 1024,
                "resume_compiled_executable_admission")
        digest, count, first = hashlib.sha256(), 0, None
        while block := os.read(fd, 65536):
            if first is None:
                first = block[:20]
            count += len(block)
            require(count <= 128 * 1024 * 1024, "resume_compiled_executable_budget")
            digest.update(block)
        after = os.fstat(fd)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mode,
                                  value.st_uid, value.st_nlink, value.st_mtime_ns, value.st_ctime_ns)
        require(identity(before) == identity(after) and count == before.st_size
                and first[:6] == b"\x7fELF\x02\x01" and first[18:20] == b"\x3e\x00",
                "resume_compiled_linux_x64_original_changed")
        return {"bytes": count, "sha256": digest.hexdigest(), "mode": stat.S_IMODE(before.st_mode),
                "nlink": before.st_nlink, "uid": before.st_uid}
    finally:
        os.close(fd)


def parse_named_rust_tests(stdout, selected, code):
    """Read sequential libtest records, including stdout emitted by --nocapture."""
    expected = set(selected)
    observed, pending, headers, footer = [], None, [], None
    require(len(expected) == len(selected) and expected, code)
    for line in stdout.splitlines():
        if line.startswith(b"test result:"):
            match = re.fullmatch(rb"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; "
                                 rb"(\d+) measured; (\d+) filtered out; finished in \d+(?:\.\d+)?s", line)
            require(pending is None and footer is None and match is not None, code)
            footer = tuple(int(value) for value in match.groups())
        elif line.startswith(b"test "):
            match = re.fullmatch(rb"test ([A-Za-z0-9_:]+) \.\.\.(?: (.*))?", line)
            require(footer is None and pending is None and match is not None, code)
            name, tail = match.group(1).decode("ascii"), match.group(2) or b""
            require(name in expected and name not in observed, code)
            require(not tail.startswith((b"FAILED", b"ignored")), code)
            if tail == b"ok":
                observed.append(name)
            else:
                pending = name
        elif line == b"ok":
            require(footer is None and pending is not None, code)
            observed.append(pending)
            pending = None
        elif line.startswith((b"FAILED", b"ignored")):
            require(False, code)
        elif line.startswith(b"running "):
            match = re.fullmatch(rb"running (\d+) tests?", line)
            require(footer is None and pending is None and not observed and match is not None, code)
            headers.append(int(match.group(1)))
        elif line:
            # Only an open test may own output; stray completion/footer text
            # cannot substitute for a missing named result.
            require(footer is None and pending is not None, code)
    require(pending is None and headers == [len(selected)] and footer is not None
            and footer[:4] == (len(selected), 0, 0, 0)
            and sorted(observed) == sorted(selected), code)
    return {"passed": len(selected), "failed": 0, "ignored": 0, "measured": 0,
            "filtered": footer[4], "selected_ids": sorted(observed)}


def parse_original_tests(stdout):
    return parse_named_rust_tests(stdout, SELECTED, "resume_eight_actual_recovery_tests_required")


def check_source_gate(current, root=None, *, reader=None):
    from native_manim_resume_runtime import ORIGINAL_INVENTORY_SHA, ORIGINAL_PROJECTION_SHA
    root = folder() if root is None else Path(root)
    read_original = original if reader is None else reader
    raw = read_original(root / "source-gate.json", 128 * 1024)
    value = json.loads(raw)
    stdout = read_original(root / "source-gate.stdout.log", 4 * 1024 * 1024)
    stderr = read_original(root / "source-gate.stderr.log", 4 * 1024 * 1024)
    counts = parse_original_tests(stdout)
    source, binary = value.get("source_before"), value.get("compiled_binary_before")
    require(type(source) is dict and type(source.get("files")) is int and 0 < source["files"] <= 8192
            and type(source.get("bytes")) is int and 0 < source["bytes"] <= 256 * 1024 * 1024
            and re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", ""))
            and source.get("old_renderer_and_original_pixel_predicates_unchanged") is True
            and source.get("entire_rust_closure_equal_to_old_41_claimed") is False
            and type(binary) is dict and type(binary.get("bytes")) is int and 0 < binary["bytes"] <= 128 * 1024 * 1024
            and re.fullmatch(r"[0-9a-f]{64}", binary.get("sha256", ""))
            and type(binary.get("uid")) is int and binary["uid"] > 0,
            "resume_actual_source_and_linux_executable_required")
    require(value.get("schema") == "motionwright.manim.resume.source-gate/1"
            and value.get("status") == "EIGHT_REAL_RUST_TESTS_AND_EXECUTABLE_READY"
            and value.get("context") == current and value.get("counts") == counts
            and value.get("source_before") == value.get("source_after")
            and value.get("compiled_binary_before") == value.get("compiled_binary_after")
            and value.get("stdout") == {"bytes": len(stdout), "sha256": sha(stdout)}
            and value.get("stderr") == {"bytes": len(stderr), "sha256": sha(stderr)}
            and value.get("original_cargo_exit") == 0
            and value.get("resources", {}).get("memory_limit_bytes") == 1536 * 1024 * 1024
            and value.get("resources", {}).get("physical_reserve_bytes") == 512 * 1024 * 1024
            and type(value.get("resources", {}).get("peak_bytes")) is int
            and value["resources"]["peak_bytes"] <= 1536 * 1024 * 1024
            and value.get("resources", {}).get("minimum_physical_available_bytes", 0) >= 512 * 1024 * 1024
            and value.get("resources", {}).get("cgroup_quiescent") is True
            and value.get("runtime", {}).get("original_inventory_sha256") == ORIGINAL_INVENTORY_SHA
            and value.get("runtime", {}).get("projection_sha256") == ORIGINAL_PROJECTION_SHA
            and value.get("runtime", {}).get("whole_package_tree_equal") is True
            and re.fullmatch(r"[0-9a-f]{64}", value.get("runtime", {}).get("receipt_sha256", ""))
            and re.fullmatch(r"[0-9a-f]{64}", value.get("runtime", {}).get("runtime_inventory_sha256", ""))
            and value.get("native_effect_invocations") == 0
            and value.get("private_source_originals_opened") is False
            and value.get("automatic_retry") is False, "resume_original_source_gate_incomplete")
    return value, sha(raw)


def main():
    from native_manim_resume_resources import run_bounded
    from native_manim_resume_runtime import check as check_runtime
    current, deadline = hosted_context(), load_deadline()
    repo = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    deadline.admit_work()
    require(os.environ.get("CARGO_PROFILE_DEV_DEBUG") == "0"
            and os.environ.get("CARGO_PROFILE_TEST_DEBUG") == "0"
            and os.environ.get("CARGO_INCREMENTAL") == "0", "resume_same_compiler_profile")
    out = folder()
    out.mkdir(mode=0o700, exist_ok=False)
    value = {"schema": "motionwright.manim.resume.source-gate/1", "context": current,
             "status": "NOT_COMPLETED", "selected_ids": list(SELECTED),
             "native_effect_invocations": 0, "private_source_originals_opened": False,
             "formal_G2_granted": False, "automatic_retry": False}
    try:
        value["runtime"] = check_runtime(current)
        value["source_before"] = source_snapshot(repo, current)
        value["compiled_binary_before"] = binary_identity(repo / "target/debug/examples/native-manim-e2e")
        command = ["cargo", "test", "--locked", "-p", "motionwright-native", "--example", "native-manim-e2e",
                   "editable_cycle::recovery::tests::", "--", "--nocapture", "--test-threads=1"]
        terminal = run_bounded(deadline, "rust-oracles", command, repo)
        value["original_cargo_exit"] = terminal["original_exit"]
        resource_raw = original(deadline.root / "resources/rust-oracles/terminal.json")
        resource = json.loads(resource_raw)
        require(resource.get("status") == "EXITED_WITHIN_LIMITS"
                and resource.get("original_child_exit_known") is True
                and resource.get("original_child_exit") == 0 and resource.get("cgroup_quiescent") is True,
                "resume_original_test_resources_incomplete")
        value["resources"] = {"receipt_sha256": sha(resource_raw), "memory_limit_bytes": resource["memory_limit_bytes"],
                              "physical_reserve_bytes": resource["physical_reserve_bytes"],
                              "peak_bytes": resource["resource_after"]["peak_bytes"],
                              "minimum_physical_available_bytes": resource["minimum_physical_available_bytes"],
                              "cgroup_quiescent": True}
        value["source_after"] = source_snapshot(repo, current)
        value["compiled_binary_after"] = binary_identity(repo / "target/debug/examples/native-manim-e2e")
        logs = deadline.root / "work/rust-oracles"
        stdout, stderr = original(logs / "stdout.log", 4 * 1024 * 1024), original(logs / "stderr.log", 4 * 1024 * 1024)
        new_bytes(out / "source-gate.stdout.log", stdout)
        new_bytes(out / "source-gate.stderr.log", stderr)
        value.update(counts=parse_original_tests(stdout),
                     stdout={"bytes": len(stdout), "sha256": sha(stdout)},
                     stderr={"bytes": len(stderr), "sha256": sha(stderr)})
        require(value["source_before"] == value["source_after"]
                and value["compiled_binary_before"] == value["compiled_binary_after"],
                "resume_compiled_inputs_changed_during_focals")
        value["status"] = "EIGHT_REAL_RUST_TESTS_AND_EXECUTABLE_READY"
    except BaseException as error:
        value["first_error_type"] = type(error).__name__
        value["first_error"] = str(error)[:4096]
        new_json(out / "source-gate.json", value)
        raise
    new_json(out / "source-gate.json", value)
    check_source_gate(current)
    print(json.dumps({"status": value["status"], "selected_actual_tests": 8,
                      "native_effect_invocations": 0, "context": current}))


if __name__ == "__main__":
    from native_manim_editable_outer import workflow_cancelled
    signal.signal(signal.SIGTERM, workflow_cancelled)
    signal.signal(signal.SIGINT, workflow_cancelled)
    main()
