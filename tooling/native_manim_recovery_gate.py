#!/usr/bin/env python3
"""Gate the new retained runtime and eight recovery plus two reference tests."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import urllib.request

import ci_private_evidence as engine
from native_manim_camera_evidence import read, read_object, require, sha
from native_manim_editable_outer import load_deadline, new_bytes, new_json, original
from native_manim_resume_focals import (
    SELECTED, binary_identity, folder, parse_original_tests, parse_named_rust_tests, source_snapshot,
)
from native_manim_resume_transfer import (
    NoRedirect, RECOVERY_SCENARIO, REPOSITORY, hosted_context, selected_scenario,
)

REFERENCE = tuple(sorted("editable_cycle::reference::tests::" + name for name in (
    "retained_reference_source_reopens_without_reapplying_events",
    "retained_reference_readback_rejects_changed_source_shared_store_and_missing_native",
)))
MAX_ARTIFACT = 128 * 1024 * 1024
PUBLIC_GATE_FILES = ("source-gate.json", "source-gate.stdout.log", "source-gate.stderr.log",
                     "reference-gate.stdout.log", "reference-gate.stderr.log", "runtime-receipt.json",
                     "runtime-wheel-lock.json", "runtime-installed-files.json", "runtime-artifact.original.json",
                     "runtime-reuse.json")


def runtime_paths():
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    return (Path(os.environ["SEMWRIGHT_TEST_MANIM_RUNTIME"]).resolve(strict=True),
            temporary / "manim-frozen-runtime", temporary / "manim-frozen-runtime-artifact/runtime-wheels.tar")


def artifact_name(current):
    return "manim-frozen-runtime-" + current["source_sha"] + "-" + current["run_id"] + "-1"


def file_identity(path, budget):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink() and 0 < info.st_size <= budget,
            "recovery_runtime_artifact_regular_bound")
    count, digest = 0, hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    signature = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns, row.st_mode)
    try:
        require(signature(os.fstat(descriptor)) == signature(info), "recovery_runtime_artifact_original_open")
        while chunk := os.read(descriptor, 65536):
            count += len(chunk)
            require(count <= budget, "recovery_runtime_artifact_stream_bound")
            digest.update(chunk)
        require(signature(os.fstat(descriptor)) == signature(info), "recovery_runtime_artifact_original_fstat")
    finally:
        os.close(descriptor)
    after = path.lstat()
    require(signature(info) == signature(after)
            and count == info.st_size, "recovery_runtime_artifact_changed_during_read")
    return {"bytes": count, "sha256": digest.hexdigest()}


def validate_artifact(value, current, receipt_sha):
    row = value.get("api")
    raw = value.get("api_original_utf8")
    require(type(raw) is str and 0 < len(raw.encode()) <= 128 * 1024
            and sha(raw.encode()) == value.get("api_original_sha256")
            and engine.strict(raw.encode()) == row, "recovery_runtime_artifact_api_original_bytes")
    require(value.get("schema") == "motionwright.manim.recovery-runtime.artifact-readback/1"
            and value.get("context") == current and value.get("runtime_receipt_sha256") == receipt_sha
            and value.get("native_effect_invocations") == 0 and value.get("automatic_retry") is False
            and type(row) is dict and type(row.get("id")) is int and row["id"] > 0
            and row.get("id") == value.get("upload_action_artifact_id")
            and row.get("name") == artifact_name(current) and row.get("expired") is False
            and type(row.get("size_in_bytes")) is int and 0 < row["size_in_bytes"] <= MAX_ARTIFACT
            and row.get("workflow_run", {}).get("id") == int(current["run_id"])
            and row.get("workflow_run", {}).get("head_sha") == current["source_sha"]
            and row.get("digest") == "sha256:" + value.get("upload_action_artifact_sha256", "")
            and re.fullmatch(r"sha256:[0-9a-f]{64}", row.get("digest", ""))
            and type(value.get("local_tar", {}).get("bytes")) is int
            and 0 < value["local_tar"]["bytes"] <= MAX_ARTIFACT
            and re.fullmatch(r"[0-9a-f]{64}", value.get("local_tar", {}).get("sha256", "")),
            "recovery_runtime_artifact_original_id_digest_required")
    return {"artifact_id": row["id"], "artifact_zip_sha256": row["digest"][7:],
            "artifact_zip_bytes": row["size_in_bytes"], "tar": value["local_tar"]}


def metadata_runtime(current, root):
    from native_manim_frozen_runtime_restore import validate_reuse
    root = Path(root)
    frozen = validate_reuse(root / "runtime-receipt.json", root / "runtime-installed-files.json",
                            root / "runtime-wheel-lock.json", root / "runtime-reuse.json", current)
    receipt_raw = read(root / "runtime-receipt.json", 2 * 1024 * 1024)
    origin = frozen.get("reuse", {}).get("origin_context")
    require(frozen.get("reuse", {}).get("context") == current
            and read_object(root / "runtime-receipt.json").get("context") == origin
            and frozen["reuse"]["origin_receipt_sha256"] == sha(receipt_raw),
            "recovery_runtime_freeze_original_context")
    artifact = validate_artifact(read_object(root / "runtime-artifact.original.json"), origin, sha(receipt_raw))
    require(frozen["reuse"].get("origin_artifact") == artifact,
            "recovery_runtime_reuse_original_artifact_binding")
    checked_archive_binding(frozen, artifact)
    return {"schema": "motionwright.manim.recovery-runtime.binding/1", "context": current,
            "frozen": frozen, "retained_artifact": artifact, "equivalence_to_old_runtime": False}


def checked_archive_binding(frozen, artifact):
    archive = frozen.get("archive")
    require(type(archive) is dict and type(archive.get("bytes")) is int and 0 < archive["bytes"] <= MAX_ARTIFACT
            and re.fullmatch(r"[0-9a-f]{64}", archive.get("sha256", ""))
            and archive == artifact.get("tar"),
            "recovery_runtime_retained_tar_must_equal_freeze_archive")


def check_runtime(current):
    from native_manim_frozen_runtime_restore import check
    runtime, work, archive = runtime_paths()
    observed = check(runtime, work / "receipt.json", work / "installed-files.json",
                      work / "wheel-lock.json", work / "reuse.json", current)
    binding = metadata_runtime(current, folder())
    require(binding["frozen"] == observed, "recovery_runtime_physical_files_changed")
    inventory = read(Path(os.environ["GITHUB_WORKSPACE"]) / "verification/native-manim-e2e/runtime-inventory.json")
    value = engine.strict(inventory)
    require(value.get("schema") == "motionwright.manim.runtime-inventory/2"
            and value.get("context") == current and value.get("manim_version") == "0.21.0"
            and value.get("pyav_version") == "15.0.0" and value.get("native_render_performed") is False,
            "recovery_runtime_original_native_inventory")
    binding["runtime_inventory_sha256"] = sha(inventory)
    return binding


def record_reuse():
    from native_manim_frozen_runtime_restore import check
    current, deadline = hosted_context(), load_deadline()
    require(selected_scenario() == RECOVERY_SCENARIO, "recovery_runtime_reuse_explicit_selection")
    deadline.admit_work()
    runtime, work, _archive = runtime_paths()
    observed = check(runtime, work / "receipt.json", work / "installed-files.json",
                      work / "wheel-lock.json", work / "reuse.json", current)
    output = folder()
    output.mkdir(mode=0o700, exist_ok=False)
    for source, name in (("receipt.json", "runtime-receipt.json"), ("wheel-lock.json", "runtime-wheel-lock.json"),
                         ("installed-files.json", "runtime-installed-files.json"), ("reuse.json", "runtime-reuse.json"),
                         ("runtime-origin-artifact.original.json", "runtime-artifact.original.json")):
        new_bytes(output / name, original(work / source, 2 * 1024 * 1024))
    require(sum(path.stat().st_size for path in output.iterdir()) <= 1536 * 1024,
            "recovery_runtime_public_metadata_budget")
    binding = metadata_runtime(current, output)
    require(binding["frozen"] == observed, "recovery_runtime_recorded_reuse_changed")
    print(json.dumps({"status": "ORIGINAL_RUNTIME_REUSED_WITHOUT_REBUILD_OR_UPLOAD",
                      "artifact_id": binding["retained_artifact"]["artifact_id"], "context": current,
                      "source_build_invocations": 0, "native_effect_invocations": 0}))


def record_artifact():
    from native_manim_frozen_runtime import check
    current, deadline = hosted_context(), load_deadline()
    require(selected_scenario() == RECOVERY_SCENARIO, "recovery_runtime_artifact_explicit_selection")
    deadline.admit_work()
    runtime, work, archive = runtime_paths()
    check(runtime, work / "receipt.json", work / "installed-files.json")
    output = folder()
    output.mkdir(mode=0o700, exist_ok=False)
    for source, name in (("receipt.json", "runtime-receipt.json"), ("wheel-lock.json", "runtime-wheel-lock.json"),
                         ("installed-files.json", "runtime-installed-files.json")):
        new_bytes(output / name, original(work / source, 2 * 1024 * 1024))
    require(sum(path.stat().st_size for path in output.iterdir()) <= 1536 * 1024,
            "recovery_runtime_public_metadata_budget")
    identifier = os.environ.get("MANIM_RUNTIME_ARTIFACT_ID", "")
    digest = os.environ.get("MANIM_RUNTIME_ARTIFACT_SHA256", "")
    require(re.fullmatch(r"[1-9][0-9]*", identifier) and re.fullmatch(r"[0-9a-f]{64}", digest),
            "recovery_runtime_original_upload_outputs")
    endpoint = "https://api.github.com/repos/" + REPOSITORY + "/actions/artifacts/" + identifier
    request = urllib.request.Request(endpoint, headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Motionwright-Manim-runtime-readback"})
    new_json(output / "runtime-artifact-read-intent.json", {"context": current, "method": "GET", "url": endpoint,
        "maximum_bytes": 128 * 1024, "automatic_retry": False, "native_effect_invocations": 0})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=min(30, deadline.remaining("work"))) as response:
        raw = response.read(128 * 1024 + 1)
    new_bytes(output / "runtime-artifact-api.original.json", raw[:128 * 1024])
    require(len(raw) <= 128 * 1024, "recovery_runtime_artifact_api_budget")
    value = {"schema": "motionwright.manim.recovery-runtime.artifact-readback/1", "context": current,
             "upload_action_artifact_id": int(identifier), "upload_action_artifact_sha256": digest,
             "api": engine.strict(raw), "api_original_utf8": raw.decode("utf-8"), "api_original_sha256": sha(raw),
             "runtime_receipt_sha256": sha(read(work / "receipt.json")),
             "local_tar": file_identity(archive, MAX_ARTIFACT),
             "native_effect_invocations": 0, "automatic_retry": False}
    new_json(output / "runtime-artifact.original.json", value)
    validate_artifact(value, current, value["runtime_receipt_sha256"])
    require(check(runtime, work / "receipt.json", work / "installed-files.json").get("archive") == value["local_tar"],
            "recovery_runtime_uploaded_tar_changed_from_original_freeze")
    print(json.dumps({"status": "RUNTIME_ARTIFACT_ID_DIGEST_RETAINED", "artifact_id": int(identifier),
                      "context": current, "native_effect_invocations": 0}))


def parse_reference_tests(stdout):
    return parse_named_rust_tests(stdout, REFERENCE, "recovery_runtime_two_actual_reference_tests_required")


def check_source_gate(current, root=None):
    root = folder() if root is None else Path(root)
    raw = original(root / "source-gate.json", 128 * 1024)
    value = engine.strict(raw)
    counts = {}
    for label, prefix, parser in (("recovery", "source-gate", parse_original_tests),
                                  ("reference", "reference-gate", parse_reference_tests)):
        stdout, stderr = (original(root / (prefix + suffix), 1024 * 1024)
                          for suffix in (".stdout.log", ".stderr.log"))
        counts[label] = parser(stdout)
        row = value.get("tests", {}).get(label, {})
        require(row.get("counts") == counts[label] and row.get("original_cargo_exit") == 0
                and row.get("stdout") == {"bytes": len(stdout), "sha256": sha(stdout)}
                and row.get("stderr") == {"bytes": len(stderr), "sha256": sha(stderr)}
                and row.get("resources", {}).get("memory_limit_bytes") == 1536 * 1024 * 1024
                and row.get("resources", {}).get("physical_peak_budget_bytes") == 1536 * 1024 * 1024
                and row.get("resources", {}).get("cgroup_max_bytes") == 1472 * 1024 * 1024
                and row.get("resources", {}).get("cgroup_high_bytes") == 1408 * 1024 * 1024
                and row.get("resources", {}).get("physical_reserve_bytes") == 512 * 1024 * 1024
                and type(row.get("resources", {}).get("peak_bytes")) is int
                and 0 <= row["resources"]["peak_bytes"] <= 1536 * 1024 * 1024
                and row.get("resources", {}).get("minimum_physical_available_bytes", 0) >= 512 * 1024 * 1024
                and row.get("resources", {}).get("cgroup_quiescent") is True,
                "recovery_runtime_actual_focal_source_originals")
    source, binary = value.get("source_before", {}), value.get("compiled_binary_before", {})
    runtime = metadata_runtime(current, root)
    inventory_sha = value.get("runtime", {}).get("runtime_inventory_sha256", "")
    require(re.fullmatch(r"[0-9a-f]{64}", inventory_sha), "recovery_runtime_native_inventory_binding")
    runtime["runtime_inventory_sha256"] = inventory_sha
    require(sum((root / name).stat().st_size for name in PUBLIC_GATE_FILES) <= 2 * 1024 * 1024 - 65536,
            "recovery_runtime_recipient_metadata_expansion_budget")
    require(value.get("schema") == "motionwright.manim.recovery-runtime.source-gate/1"
            and value.get("status") == "TEN_REAL_RUST_TESTS_AND_RETAINED_RUNTIME_READY"
            and value.get("context") == current and value.get("runtime") == runtime
            and source == value.get("source_after") and binary == value.get("compiled_binary_after")
            and type(source.get("files")) is int and 0 < source["files"] <= 8192
            and type(source.get("bytes")) is int and 0 < source["bytes"] <= 256 * 1024 * 1024
            and re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", ""))
            and source.get("old_renderer_and_original_pixel_predicates_unchanged") is True
            and source.get("entire_rust_closure_equal_to_old_41_claimed") is False
            and type(binary.get("bytes")) is int and 0 < binary["bytes"] <= 128 * 1024 * 1024
            and re.fullmatch(r"[0-9a-f]{64}", binary.get("sha256", ""))
            and type(binary.get("uid")) is int and binary["uid"] > 0
            and value.get("native_effect_invocations") == 0 and value.get("project_changes_applied") == 0
            and value.get("private_source_originals_opened") is False
            and value.get("equivalence_to_old_runtime") is False and value.get("automatic_retry") is False,
            "recovery_runtime_new_gate_cannot_inherit_old_runtime_equivalence")
    return value, sha(raw)


def run_tests():
    from native_manim_resume_resources import run_bounded
    current, deadline = hosted_context(), load_deadline()
    require(selected_scenario() == RECOVERY_SCENARIO, "recovery_runtime_source_gate_explicit_selection")
    repo = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    output = folder()
    require(output.is_dir() and not (output / "source-gate.json").exists(), "recovery_runtime_no_repeated_source_gate")
    require(os.environ.get("CARGO_PROFILE_DEV_DEBUG") == "0" and os.environ.get("CARGO_PROFILE_TEST_DEBUG") == "0"
            and os.environ.get("CARGO_INCREMENTAL") == "0", "recovery_runtime_same_cached_compiler_profile")
    value = {"schema": "motionwright.manim.recovery-runtime.source-gate/1", "context": current,
             "status": "NOT_COMPLETED", "tests": {}, "native_effect_invocations": 0,
             "project_changes_applied": 0, "private_source_originals_opened": False,
             "equivalence_to_old_runtime": False, "formal_G2_granted": False, "automatic_retry": False}
    try:
        value["runtime"] = check_runtime(current)
        value["source_before"] = source_snapshot(repo, current)
        value["compiled_binary_before"] = binary_identity(repo / "target/debug/examples/native-manim-e2e")
        for label, prefix, selector, step, parser in (
            ("recovery", "source-gate", "recovery", "rust-oracles", parse_original_tests),
            ("reference", "reference-gate", "reference", "reference-oracles", parse_reference_tests),
        ):
            deadline.admit_work()
            terminal = run_bounded(deadline, step, ["cargo", "test", "--locked", "--jobs", "1", "-p", "motionwright-native",
                "--example", "native-manim-e2e", "editable_cycle::" + selector + "::tests::",
                "--", "--nocapture", "--test-threads=1"], repo)
            resource_raw = original(deadline.root / "resources" / step / "terminal.json")
            resource = engine.strict(resource_raw)
            logs = deadline.root / "work" / step
            stdout, stderr = (original(logs / suffix, 1024 * 1024) for suffix in ("stdout.log", "stderr.log"))
            new_bytes(output / (prefix + ".stdout.log"), stdout)
            new_bytes(output / (prefix + ".stderr.log"), stderr)
            row = value["tests"][label] = {"original_cargo_exit": terminal["original_exit"],
                "stdout": {"bytes": len(stdout), "sha256": sha(stdout)},
                "stderr": {"bytes": len(stderr), "sha256": sha(stderr)}}
            require(resource.get("status") == "EXITED_WITHIN_LIMITS"
                    and resource.get("original_child_exit_known") is True
                    and resource.get("original_child_exit") == 0 and resource.get("cgroup_quiescent") is True,
                    "recovery_runtime_original_test_resources")
            require(resource.get("physical_peak_budget_bytes") == 1536 * 1024 * 1024
                    and resource.get("cgroup_max_bytes") == 1472 * 1024 * 1024
                    and resource.get("cgroup_high_bytes") == 1408 * 1024 * 1024
                    and resource.get("resource_before", {}).get("limit_bytes")
                        == resource.get("resource_after", {}).get("limit_bytes") == 1472 * 1024 * 1024
                    and resource.get("resource_before", {}).get("high_bytes")
                        == resource.get("resource_after", {}).get("high_bytes") == 1408 * 1024 * 1024,
                    "recovery_runtime_original_test_headroom_policy")
            row["resources"] = {"receipt_sha256": sha(resource_raw), "memory_limit_bytes": resource["memory_limit_bytes"],
                "physical_reserve_bytes": resource["physical_reserve_bytes"], "peak_bytes": resource["resource_after"]["peak_bytes"],
                "physical_peak_budget_bytes": resource["physical_peak_budget_bytes"],
                "cgroup_max_bytes": resource["cgroup_max_bytes"], "cgroup_high_bytes": resource["cgroup_high_bytes"],
                "minimum_physical_available_bytes": resource["minimum_physical_available_bytes"], "cgroup_quiescent": True}
            row["counts"] = parser(stdout)
        value["source_after"] = source_snapshot(repo, current)
        value["compiled_binary_after"] = binary_identity(repo / "target/debug/examples/native-manim-e2e")
        require(value["source_before"] == value["source_after"]
                and value["compiled_binary_before"] == value["compiled_binary_after"]
                and value["runtime"] == check_runtime(current), "recovery_runtime_gate_inputs_changed")
        value["status"] = "TEN_REAL_RUST_TESTS_AND_RETAINED_RUNTIME_READY"
    except BaseException as error:
        value.update(first_error_type=type(error).__name__, first_error=str(error)[:4096])
        new_json(output / "source-gate.json", value)
        raise
    new_json(output / "source-gate.json", value)
    check_source_gate(current)
    print(json.dumps({"status": value["status"], "actual_recovery_tests": 8, "actual_reference_tests": 2,
                      "native_effect_invocations": 0, "context": current}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("record-artifact", "record-reuse", "test"))
    args = parser.parse_args()
    {"record-artifact": record_artifact, "record-reuse": record_reuse, "test": run_tests}[args.command]()


if __name__ == "__main__":
    from native_manim_editable_outer import workflow_cancelled
    signal.signal(signal.SIGTERM, workflow_cancelled)
    signal.signal(signal.SIGINT, workflow_cancelled)
    main()
