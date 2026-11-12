#!/usr/bin/env python3
"""Restore the exact retained runtime, preserving its original provenance.

The original receipt, wheel lock, installed manifest and 55-member archive are
never rewritten or assigned a new context. A separate reuse receipt records
one artifact GET, one installation from the retained wheels and one fresh
import observation. No sdist build, dependency download or Native invocation
is permitted. The outer resource guard owns the Linux cgroup and deadline.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import time

import native_manim_frozen_runtime as frozen
import native_manim_frozen_runtime_host as archive_io

MIB = 1024 * 1024
MAX_SECONDS = 900
ORIGIN = {"source_sha": "54c74f5366513a72f6f22d4f6641091362534a09",
          "semwright_sha": "acc0a089e2f0e3e9936632fb2a4aa0c2fab56199",
          "run_id": "42361964881", "run_attempt": "1"}
# This relation concerns only the retained Python wheels. It does not claim
# that an original acc0 Native/Host execution is valid on the newer SDK.
CURRENT_SDK = "0b2406bee57fc15280abbdf25f1b89527bd8987c"
CURRENT_SOURCE_LOCK_SHA = "8683f913816abee3fd0cea8123ca1972bdd704a6fba4d5b6750de652119d55a5"
SOURCE_LOCK_PATH = Path(__file__).resolve().parents[1] / "SOURCE_LOCK.json"
ORIGIN_RECEIPT_SHA = "d54d3cc4e49fb0cda44d7da0106ad02532e06263b1e7a0f7083e18cafe8fe6dc"
ORIGIN_ARTIFACT_SHA = "3f6540d9fb32483277867df66e8113effba386890253c7e3656f805213c4ab88"
OBSERVER_SHA = "709be28b4e451482c4e2bdd03f2faaa8c79d12ee29af229801b22d105d81ce42"
REQUIREMENTS = Path(__file__).with_name("requirements")
RECEIPT_PATH = REQUIREMENTS / "manim-frozen-runtime-42361964881-receipt.json"
ARTIFACT_PATH = REQUIREMENTS / "manim-frozen-runtime-42361964881-artifact.json"
SCHEMA = "motionwright.manim.frozen-runtime-reuse/1"
STATUS = "EXACT_RETAINED_WHEELS_REINSTALLED_AND_OBSERVED"
COMMAND_LABELS = ("inspect-python", "install-wheels", "observe-native")
PYTHON_INSPECTION = (
    "import importlib.metadata,json,os,sys; "
    "print(json.dumps({'python_executable':sys.executable,'python_version':sys.version,"
    "'pip_version':importlib.metadata.version('pip'),'uid':os.getuid(),'euid':os.geteuid()},sort_keys=True))"
)
need = frozen.need


def absolute(value):
    return (type(value) is str and PurePosixPath(value).is_absolute()
            and str(PurePosixPath(value)) == value and ".." not in PurePosixPath(value).parts
            and len(value) <= 4096)


def sdk_relation(current, *, reader=None):
    """Pin an explicit new wheel-only reuse context without rewriting ORIGIN.

    Original SDK custody is historical. Reuse on the reviewed current SDK
    remains a Python wheel installation observation, NOT a current SDK binary
    or Native renderer compatibility/acceptance certificate.
    """
    sdk = current["semwright_sha"]
    if sdk == ORIGIN["semwright_sha"]:
        return {"scope": "historical-python-wheel-runtime-only",
                "origin_sdk_sha": ORIGIN["semwright_sha"],
                "current_sdk_sha": sdk, "source_lock_sha256": None,
                "native_effects_attested": False}
    need(sdk == CURRENT_SDK, "runtime_reuse_unreviewed_sdk")
    original = (SOURCE_LOCK_PATH.read_bytes() if reader is None else
                frozen.metadata_original(SOURCE_LOCK_PATH, frozen.MAX_METADATA, reader=reader)[1])
    need(0 < len(original) <= frozen.MAX_METADATA
         and hashlib.sha256(original).hexdigest() == CURRENT_SOURCE_LOCK_SHA,
         "runtime_reuse_source_lock_changed")
    locked = frozen.strict(original)
    selected = locked["dependencies"]["semwright"]
    need(locked["schema_version"] == 1
         and selected["repository"] == "https://github.com/seradotcom/semwright"
         and selected["revision"] == sdk
         and selected["verification"]["status"] == "verified"
         and selected["verification"]["revision"] == sdk
         and selected["verification"]["repository"] == selected["repository"],
         "runtime_reuse_current_sdk_source_verification")
    return {"scope": "reviewed-current-sdk-python-wheel-runtime-only",
            "origin_sdk_sha": ORIGIN["semwright_sha"], "current_sdk_sha": sdk,
            "source_lock_sha256": CURRENT_SOURCE_LOCK_SHA,
            "native_effects_attested": False}


def context(current, *, hosted=False, reader=None):
    need(type(current) is dict and set(current) == set(ORIGIN)
         and all(type(current[key]) is str and re.fullmatch(r"[0-9a-f]{40}", current[key])
                 for key in ("source_sha", "semwright_sha"))
         and type(current["run_id"]) is str and re.fullmatch(r"[1-9][0-9]*", current["run_id"])
         and current["run_id"] != ORIGIN["run_id"] and current["run_attempt"] == "1",
         "runtime_reuse_new_explicit_context")
    relation = sdk_relation(current, reader=reader)
    if hosted:
        # A historical acc0 request cannot describe this current 0b job.
        # GitHub source identity is checked independently from its SDK pin.
        need(relation["current_sdk_sha"] == CURRENT_SDK
             and current["source_sha"] == os.environ.get("GITHUB_SHA")
             and current["run_id"] == os.environ.get("GITHUB_RUN_ID")
             and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
             "runtime_reuse_exact_hosted_context")
    return current


def origin_metadata(*, reader=None):
    from native_manim_recovery_gate import validate_artifact
    receipt_identity, receipt_raw = frozen.metadata_original(RECEIPT_PATH, frozen.MAX_METADATA, reader=reader)
    artifact_identity, artifact_raw = frozen.metadata_original(ARTIFACT_PATH, frozen.MAX_METADATA, reader=reader)
    need(receipt_identity["sha256"] == ORIGIN_RECEIPT_SHA
         and artifact_identity["sha256"] == ORIGIN_ARTIFACT_SHA, "runtime_reuse_reviewed_origin_changed")
    receipt = frozen.strict(receipt_raw)
    need(receipt.get("context") == ORIGIN and receipt.get("input_lock_sha256") == frozen.LOCK_SHA
         and len(receipt.get("archive_members", {})) == 55, "runtime_reuse_origin_context")
    artifact = validate_artifact(frozen.strict(artifact_raw), ORIGIN, ORIGIN_RECEIPT_SHA)
    need(artifact["artifact_id"] == 13570292418 and artifact["tar"] == receipt["archive"],
         "runtime_reuse_origin_artifact_binding")
    return receipt, artifact


def install_argv(python, runtime, work):
    return [python, "-B", "-m", "pip", "install", "--disable-pip-version-check",
            "--no-cache-dir", "--no-index", "--no-deps", "--no-compile", "--require-hashes",
            "--find-links", str(PurePosixPath(str(work)) / "wheels"),
            "--target", str(PurePosixPath(str(runtime)) / "site-packages"),
            "--report", str(PurePosixPath(str(work)) / "installed-pip-report.json"),
            "-r", str(PurePosixPath(str(work)) / "wheel-lock.requirements.txt")]


def observe_argv(python, runtime, work, observer):
    return [python, "-B", observer, "_observe", "--runtime", runtime,
            "--output", str(PurePosixPath(str(work)) / "native-observation.json")]


def inspect_argv(python):
    return [python, "-B", "-c", PYTHON_INSPECTION]


def validate_python_inspection(row, origin, python):
    value = inline_json(row, 65536)
    need(set(value) == {"python_executable", "python_version", "pip_version", "uid", "euid"}
         and value["python_executable"] == python and value["python_version"] == origin["python_version"]
         and value["pip_version"] == "24.0" and type(value["uid"]) is int and value["uid"] > 0
         and type(value["euid"]) is int and value["euid"] == value["uid"],
         "runtime_reuse_actual_installer_python")
    return value


def recorded_json(path, maximum=frozen.MAX_METADATA):
    identity, raw = frozen.regular(path, maximum, collect=True)
    frozen.strict(raw)
    return {**identity, "original_utf8": raw.decode("utf-8")}


def inline_json(row, maximum=frozen.MAX_METADATA):
    need(type(row) is dict and set(row) == {"bytes", "sha256", "original_utf8"}
         and type(row["original_utf8"]) is str, "runtime_reuse_original_json_shape")
    raw = row["original_utf8"].encode("utf-8")
    need(0 < len(raw) == row["bytes"] <= maximum and frozen.digest(raw) == row["sha256"],
         "runtime_reuse_original_json_identity")
    return frozen.strict(raw)


def command_originals(work, label):
    return {**{name: recorded_json(work / (label + "." + name + ".json"))
               for name in ("intent", "spawn", "terminal")},
            **{name: frozen.regular(work / (label + "." + name + ".log"), 4 * MIB)
               for name in ("stdout", "stderr")}}


def validate_command(row, expected_argv, seconds):
    need(type(row) is dict and set(row) == {"intent", "spawn", "terminal", "stdout", "stderr"},
         "runtime_reuse_new_command_roster")
    intent, spawn, terminal = (inline_json(row[name], 65536) for name in ("intent", "spawn", "terminal"))
    need(intent.get("argv") == expected_argv and intent.get("maximum_seconds") == seconds
         and intent.get("stream_bytes_each") == 4 * MIB and intent.get("owned_process_group") is True
         and intent.get("preparation_watchdog_bytes") == frozen.MAX_PREPARATION_BYTES
         and intent.get("preparation_watchdog_files") == frozen.MAX_PREPARATION_FILES
         and intent.get("preparation_hard_disk_quota_claimed") is False
         and intent.get("automatic_retry") is False, "runtime_reuse_current_command_intent")
    need(type(spawn.get("pid")) is int and spawn["pid"] > 0 and spawn.get("process_group") == spawn["pid"]
         and spawn.get("automatic_retry") is False, "runtime_reuse_current_command_spawn")
    need(terminal.get("spawned") is True and terminal.get("exit_code") == 0
         and all(terminal.get(key) is False for key in ("terminated", "process_group_term_sent",
             "process_group_kill_sent", "group_survived_parent_exit", "stream_limit_exceeded",
             "preparation_hard_disk_quota_claimed", "automatic_retry"))
         and terminal.get("streams_settled") is True and terminal.get("stream_errors") == []
         and terminal.get("first_error_type") is None, "runtime_reuse_current_command_not_complete")
    peak = terminal.get("preparation_observed_peak", {})
    need(type(peak.get("bytes")) is int and 0 <= peak["bytes"] <= frozen.MAX_PREPARATION_BYTES
         and type(peak.get("files")) is int and 0 <= peak["files"] <= frozen.MAX_PREPARATION_FILES,
         "runtime_reuse_command_disk_observation")
    for name in ("stdout", "stderr"):
        item = row[name]
        need(type(item) is dict and set(item) == {"bytes", "sha256"}
             and type(item["bytes"]) is int and 0 <= item["bytes"] <= 4 * MIB
             and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), "runtime_reuse_current_command_stream")


def validate_reuse(receipt_path, manifest_path, wheel_lock_path, reuse_path, current, *, reader=None):
    """Validate origin and reuse metadata without pretending to read the TAR."""
    context(current, reader=reader)
    origin, artifact = origin_metadata(reader=reader)
    binding = frozen.validate_receipt(receipt_path, manifest_path, wheel_lock_path, reader=reader)
    need(binding["receipt_sha256"] == ORIGIN_RECEIPT_SHA
         and frozen.object_file(receipt_path, reader=reader) == origin, "runtime_reuse_original_receipt_required")
    value = frozen.object_file(reuse_path, reader=reader)
    need(value.get("schema") == SCHEMA and value.get("status") == STATUS
         and value.get("context") == current and value.get("origin_context") == ORIGIN
         and value.get("sdk_relation") == sdk_relation(current, reader=reader)
         and value.get("origin_receipt_sha256") == ORIGIN_RECEIPT_SHA
         and value.get("origin_artifact") == artifact and value.get("archive") == origin["archive"]
         and value.get("input_lock_sha256") == frozen.LOCK_SHA
         and value.get("installed_manifest") == origin["installed_manifest"]
         and value.get("wheel_lock") == origin["wheel_lock"]
         and value.get("native_observation") == origin["native_observation"]
         and value.get("python") == origin["python"] and value.get("python_version") == origin["python_version"]
         and value.get("runtime_path") == origin["runtime_path"]
         and value.get("archive_member_count") == 55 and value.get("wheel_count") == 32
         and value.get("archive_physical_readback") is True
         and value.get("installed_files_equal_to_origin") is True
         and value.get("imported_native_files_equal_to_origin") is True
         and all(type(value.get(key)) is int and value[key] == number for key, number in (
             ("source_build_invocations", 0), ("dependency_download_invocations", 0),
             ("artifact_download_invocations", 1), ("installation_invocations", 1),
             ("python_inspection_invocations", 1),
             ("native_observation_invocations", 1), ("native_effect_invocations", 0)))
         and all(value.get(key) is False for key in ("project_inputs_read", "automatic_retry",
             "old_runtime_equivalence_claimed", "sdist_rebuild_byte_reproducible_claimed")),
         "runtime_reuse_origin_and_current_observations")
    need(absolute(value.get("work_path")) and absolute(value.get("python_executable"))
         and absolute(value.get("observer_script", {}).get("path"))
         and value["observer_script"].get("sha256") == OBSERVER_SHA
         and value["observer_script"].get("bytes") == 40652,
         "runtime_reuse_current_installation_paths")
    original_install = inline_json(value.get("origin_install_intent"), 65536)
    need({key: value["origin_install_intent"][key] for key in ("bytes", "sha256")}
         == origin["archive_members"]["metadata/install-wheels.intent.json"]
         and original_install.get("argv", [None])[0] == value["python_executable"],
         "runtime_reuse_original_python_invocation")
    need(set(value.get("commands", {})) == set(COMMAND_LABELS), "runtime_reuse_only_three_new_commands")
    validate_command(value["commands"]["inspect-python"], inspect_argv(value["python_executable"]), 30)
    validate_python_inspection(value.get("python_inspection_original"), origin, value["python_executable"])
    need({key: value["python_inspection_original"][key] for key in ("bytes", "sha256")}
         == value["commands"]["inspect-python"]["stdout"], "runtime_reuse_python_inspection_original_stream")
    validate_command(value["commands"]["install-wheels"],
                     install_argv(value["python_executable"], value["runtime_path"], value["work_path"]), 300)
    validate_command(value["commands"]["observe-native"], observe_argv(value["python_executable"],
                     value["runtime_path"], value["work_path"], value["observer_script"]["path"]), 120)
    imported = inline_json(value.get("native_observation_original"))
    need(imported == origin["native_observation"]
         and value["native_observation_original"]["sha256"] == binding["native_observation_sha256"],
         "runtime_reuse_fresh_import_observation")
    download = inline_json(value.get("download_terminal"), 65536)
    need(download.get("schema") == "motionwright.manim.frozen-runtime.host-download/1"
         and download.get("spawned") is True and download.get("exit_code") == 0
         and download.get("termination_requested") is False and download.get("streams_settled") is True
         and download.get("automatic_retry") is False and download.get("first_error") is None
         and download.get("secondary_errors") == []
         and type(download.get("pid")) is int and download["pid"] > 0
         and download.get("owned_process_group") == download["pid"]
         and download.get("process_group_kill_sent") is False
         and download.get("group_survived_parent_exit") is False
         and download.get("final_process_group_state") == "absent",
         "runtime_reuse_artifact_get_not_complete")
    for name, maximum in (("stdout", artifact["artifact_zip_bytes"]), ("stderr", archive_io.MAX_STDERR)):
        row = download.get("streams", {}).get(name, {})
        need(row.get("complete") is True and row.get("error") is None
             and type(row.get("written_bytes")) is int
             and 0 <= row["written_bytes"] == row.get("observed_bytes") <= maximum
             and re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")), "runtime_reuse_artifact_get_stream")
    stdout = download["streams"]["stdout"]
    need(stdout["written_bytes"] == artifact["artifact_zip_bytes"]
         and stdout["sha256"] == artifact["artifact_zip_sha256"], "runtime_reuse_downloaded_original_zip")
    binding["reuse"] = {"receipt_sha256": frozen.metadata_original(reuse_path, frozen.MAX_METADATA, reader=reader)[0]["sha256"],
        "context": current, "origin_context": ORIGIN, "sdk_relation": sdk_relation(current, reader=reader),
        "origin_receipt_sha256": ORIGIN_RECEIPT_SHA,
        "origin_artifact": artifact, "source_build_invocations": 0,
        "exact_installed_files_reused": True, "exact_imported_native_files_reused": True}
    return binding


def check(runtime, receipt_path, manifest_path, wheel_lock_path, reuse_path, current):
    binding = validate_reuse(receipt_path, manifest_path, wheel_lock_path, reuse_path, current)
    actual = frozen.check(runtime, receipt_path, manifest_path)
    need(actual == {key: value for key, value in binding.items() if key != "reuse"},
         "runtime_reuse_physical_installation_changed")
    return binding


class Watch:
    def __init__(self, roots):
        self.roots, self.started, self.next_usage = tuple(roots), time.monotonic(), 0

    def __call__(self):
        current = time.monotonic()
        need(current - self.started <= MAX_SECONDS, "runtime_reuse_original_deadline")
        if current >= self.next_usage:
            frozen.preparation_usage(self.roots)
            need(shutil.disk_usage(self.roots[0]).free >= 512 * MIB, "runtime_reuse_disk_reserve")
            self.next_usage = current + 0.5


def extract_originals(archive, expected, members, work, guard):
    """Extract only the already authenticated closed roster to exclusive files."""
    need(len(members) == 55 and set(members) == {
        *["metadata/" + name for name in frozen.RUNTIME_METADATA],
        *[name for name in members if name.startswith("wheels/") and name.endswith(".whl")]},
        "runtime_reuse_extraction_roster")
    observed = {}
    with archive_io.stable_reader(archive, frozen.MAX_TAR) as (stream, _size):
        with tarfile.open(fileobj=stream, mode="r|", errorlevel=2) as source:
            for member in source:
                guard()
                name = member.name
                need(name in members and name not in observed and member.type == tarfile.REGTYPE
                     and not member.sparse and member.linkname == "" and member.mode == 0o600
                     and member.uid == member.gid == member.mtime == 0
                     and member.pax_headers in ({}, {"path": name})
                     and member.size == members[name]["bytes"], "runtime_reuse_extraction_original_member")
                parts = name.split("/")
                need(len(parts) == 2 and parts[1] == frozen.relative_name(parts[1]),
                     "runtime_reuse_extraction_name")
                destination = work / ("wheels" if parts[0] == "wheels" else "origin") / parts[1]
                content = source.extractfile(member)
                need(content is not None, "runtime_reuse_extraction_content")
                count, hashed = 0, hashlib.sha256()
                with content, archive_io.exclusive_writer(destination) as output:
                    while chunk := content.read(MIB):
                        guard()
                        count += len(chunk)
                        need(count <= member.size, "runtime_reuse_extraction_member_overflow")
                        output.write(chunk)
                        hashed.update(chunk)
                observed[name] = {"bytes": count, "sha256": hashed.hexdigest()}
                need(observed[name] == members[name]
                     and frozen.regular(destination, max(frozen.MAX_INPUT, 4 * MIB)) == members[name],
                     "runtime_reuse_extracted_original_changed")
    need(observed == members and frozen.regular(archive, frozen.MAX_TAR) == expected,
         "runtime_reuse_extraction_complete")
    return observed


def download_original(work, gh, artifact, guard):
    """The bounded GET reader owns cleanup from Popen, including publication failure."""
    return archive_io.download_once(work, gh, artifact, guard)


def restore(runtime, work, output, current, gh):
    runtime, work, output, gh = map(Path, (runtime, work, output, gh))
    context(current, hosted=True)
    # This controller runs in the already admitted custody venv. Renderer and
    # pip children use the original interpreter recorded in the retained TAR;
    # no custody package directory is added to their environment or imports.
    need(sys.platform == "linux" and os.geteuid() > 0 and sys.version_info[:2] == (3, 12),
         "runtime_reuse_original_install_platform")
    origin, artifact = origin_metadata()
    need(str(runtime) == origin["runtime_path"] and sys.version == origin["python_version"]
         and frozen.regular(Path(sys.executable).resolve(strict=True), frozen.MAX_INPUT) == origin["python"],
         "runtime_reuse_original_python_before_install")
    for path in (runtime, work, output):
        need(path.is_absolute() and path.resolve(strict=False) == path
             and not path.exists() and not path.is_symlink(), "runtime_reuse_existing_destination_no_replay")
    need(runtime != work and runtime not in work.parents and work not in runtime.parents
         and runtime not in output.parents and work not in output.parents
         and shutil.disk_usage(work.parent).free >= 2 * 1024 * MIB, "runtime_reuse_workspace_reserve")
    for path in (runtime.parent, work.parent, output.parent.parent):
        archive_io.namespace(path, directory=True)
    work.mkdir(mode=0o700)
    runtime.mkdir(mode=0o700)
    (runtime / "site-packages").mkdir(mode=0o700)
    for name in ("origin", "wheels", "build-temp", "build-cache"):
        (work / name).mkdir(mode=0o700)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    guard = Watch((work, runtime, output.parent))
    frozen.json_new(work / "reuse-intent.json", {"context": current, "origin_context": ORIGIN,
        "sdk_relation": sdk_relation(current),
        "origin_receipt_sha256": ORIGIN_RECEIPT_SHA, "origin_artifact": artifact,
        "source_build_invocations": 0, "dependency_download_invocations": 0,
        "native_effect_invocations": 0, "project_inputs_read": False, "automatic_retry": False})
    for source, name in ((RECEIPT_PATH, "receipt.json"), (ARTIFACT_PATH, "runtime-origin-artifact.original.json")):
        _identity, raw = frozen.regular(source, frozen.MAX_METADATA, collect=True)
        frozen.write_new(work / name, raw)
    zip_path, _terminal = download_original(work, gh.resolve(strict=True), artifact, guard)
    archive_io.retain_tar(zip_path, output,
        {"bytes": artifact["artifact_zip_bytes"], "sha256": artifact["artifact_zip_sha256"]},
        artifact["tar"], origin["archive_members"], guard)
    extract_originals(output, artifact["tar"], origin["archive_members"], work, guard)
    for name in ("wheel-lock.json", "wheel-lock.requirements.txt", "installed-files.json"):
        _identity, raw = frozen.regular(work / "origin" / name, frozen.MAX_METADATA, collect=True)
        frozen.write_new(work / name, raw)
    frozen.validate_receipt(work / "receipt.json")
    lock, _raw = frozen.input_lock()
    wheels = frozen.object_file(work / "wheel-lock.json")["wheels"]
    for row, expected in zip(wheels, lock["entries"]):
        need(row["name"] == expected["name"], "runtime_reuse_original_wheel_order")
        observed = frozen.wheel_metadata(work / "wheels" / row["filename"], expected)
        need(observed == {key: row[key] for key in ("bytes", "sha256", "expanded_bytes", "expanded_files")},
             "runtime_reuse_retained_wheel_changed")
        guard()
    requirements = "".join(row["name"] + "==" + row["version"] + " --hash=sha256:" + row["sha256"] + "\n" for row in wheels)
    need(frozen.regular(work / "wheel-lock.requirements.txt", frozen.MAX_METADATA, collect=True)[1]
         == requirements.encode(), "runtime_reuse_closed_no_network_requirements")
    old_install = recorded_json(work / "origin/install-wheels.intent.json", 65536)
    python = inline_json(old_install)["argv"][0]
    need(absolute(python) and frozen.regular(Path(python).resolve(strict=True), frozen.MAX_INPUT) == origin["python"],
         "runtime_reuse_original_python_path")
    frozen.command(work, "inspect-python", inspect_argv(python), frozen.clean_environment(work),
                   seconds=30, observed_roots=guard.roots)
    python_inspection = recorded_json(work / "inspect-python.stdout.log", 65536)
    validate_python_inspection(python_inspection, origin, python)
    observer = Path(frozen.__file__).resolve(strict=True)
    observer_identity = frozen.regular(observer, frozen.MAX_METADATA)
    need(observer_identity == {"bytes": 40652, "sha256": OBSERVER_SHA}, "runtime_reuse_observer_source_changed")
    frozen.command(work, "install-wheels", install_argv(python, runtime, work),
                   frozen.clean_environment(work), seconds=300, observed_roots=guard.roots)
    installed = list(importlib.metadata.distributions(path=[str(runtime / "site-packages")]))
    names = {frozen.name_key(value.metadata["Name"]): value.version for value in installed}
    need(len(installed) == len(names) == 32 and names == {row["name"]: row["version"] for row in wheels},
         "runtime_reuse_installed_distribution_roster")
    frozen.write_new(runtime / "runtime.json",
                     frozen.regular(work / "origin/runtime.json", frozen.MAX_METADATA, collect=True)[1])
    installed_observation = frozen.inventory(runtime)
    frozen.json_new(work / "installed-files-observed.json", installed_observation)
    need(installed_observation == frozen.object_file(work / "installed-files.json"),
         "runtime_reuse_installed_files_differ_from_origin")
    frozen.command(work, "observe-native", observe_argv(python, str(runtime), str(work), str(observer)),
                   frozen.clean_environment(work, runtime), seconds=120, observed_roots=guard.roots)
    native = frozen.object_file(work / "native-observation.json")
    need(native == origin["native_observation"], "runtime_reuse_imported_native_files_differ_from_origin")
    frozen.check(runtime, work / "receipt.json")
    guard()
    value = {"schema": SCHEMA, "status": STATUS, "context": current, "origin_context": ORIGIN,
        "sdk_relation": sdk_relation(current),
        "origin_receipt_sha256": ORIGIN_RECEIPT_SHA, "origin_artifact": artifact,
        "input_lock_sha256": frozen.LOCK_SHA, "runtime_path": str(runtime), "work_path": str(work),
        "python_executable": python, "python": origin["python"], "python_version": sys.version,
        "python_inspection_original": python_inspection,
        "observer_script": {"path": str(observer), **observer_identity}, "origin_install_intent": old_install,
        "wheel_lock": origin["wheel_lock"], "installed_manifest": origin["installed_manifest"],
        "native_observation": native, "native_observation_original": recorded_json(work / "native-observation.json"),
        "archive": origin["archive"], "archive_member_count": 55, "wheel_count": 32,
        "archive_physical_readback": True, "installed_files_equal_to_origin": True,
        "imported_native_files_equal_to_origin": True,
        "commands": {label: command_originals(work, label) for label in COMMAND_LABELS},
        "download_terminal": recorded_json(work / "download-terminal.original.json", 65536),
        "source_build_invocations": 0, "dependency_download_invocations": 0, "artifact_download_invocations": 1,
        "installation_invocations": 1, "python_inspection_invocations": 1,
        "native_observation_invocations": 1, "native_effect_invocations": 0,
        "project_inputs_read": False, "automatic_retry": False, "old_runtime_equivalence_claimed": False,
        "sdist_rebuild_byte_reproducible_claimed": False}
    frozen.json_new(work / "reuse.json", value)
    check(runtime, work / "receipt.json", work / "installed-files.json", work / "wheel-lock.json", work / "reuse.json", current)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("restore",))
    for name in ("runtime", "work", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--gh", type=Path, default=Path(shutil.which("gh") or "/usr/bin/gh"))
    args = parser.parse_args()
    try:
        result = restore(args.runtime, args.work, args.output, frozen.strict(args.context), args.gh)
    except BaseException as error:
        if args.work.is_dir() and not (args.work / "reuse-failure.json").exists():
            frozen.json_new(args.work / "reuse-failure.json", {"status": "RUNTIME_REUSE_FAILED_NO_RETRY",
                "first_error_type": type(error).__name__, "first_error": str(error)[:4096],
                "native_effect_invocations": 0, "project_inputs_read": False, "automatic_retry": False})
        raise
    print(json.dumps({"status": result["status"], "origin_context": ORIGIN,
                      "context": result["context"], "source_build_invocations": 0,
                      "native_effect_invocations": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
