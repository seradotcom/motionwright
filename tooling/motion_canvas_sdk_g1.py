#!/usr/bin/env python3
"""Source-bound G0 and focused SDK Rust/Driver Host verification.

The CLI requires isolated Python in a disposable Linux Actions job. Importing
the module exposes only data readers and process-custody helpers for synthetic
tests. SDK source, verifier source, build preparation and executed cases remain
separate in every receipt.
"""
import sys

if __name__ == "__main__" and (not sys.flags.isolated or not sys.flags.no_site):
    raise SystemExit("invoke with python -B -I -S")

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import threading
import time

SDK_SHA = "54d3459a051adbce4cae9f60c0dea8f60c824be6"
SDK_TREE = "984066e477dd71e0f40dde6809bf431a2705aa4e"
SDK_LOCK = "aff7a06615d18d9ae155c4afa9182c22210340435afba9bea72f4c4b493b895e"
MW_LOCK = "f0e42c064c71a2307b4043b8b345ff54b36f8df2692db5809ce1e85635fdc1c1"
POLICY_PATH = "scripts/dev/ci-driver-bwrap-profile.sh"
POLICY_SHA = "0d232c376ca00679c051c240465db41117a8e908ba91853c758b000e95095a8c"
WORKFLOW = ".github/workflows/native-sdk.yml"
CALLEE = ".github/workflows/motion-canvas-sdk-contracts.yml"
SELF_PATH = "tooling/motion_canvas_sdk_g1.py"
TEST_PATH = "tooling/tests/test_motion_canvas_sdk_g1.py"
READONLY_PATH = "tooling/motion_canvas_host_readonly.rs"
READONLY_CASE_ID = "readonly_host_queries_preserve_original_fixtures"
READONLY_SDK_LIBRARIES = {
    "semwright_backend_api": "backend-api",
    "semwright_driver_host": "driver-host",
    "semwright_driver_sdk": "driver-sdk",
    "semwright_driver_motion_canvas": "driver-motion-canvas",
    "semwright_policy": "policy",
    "semwright_types": "types",
}
READONLY_REGISTRY_LIBRARIES = {
    "serde_json": ("serde_json", "1.0.151"),
    "tokio": ("tokio", "1.53.1"),
    "tokio_util": ("tokio-util", "0.7.19"),
    "sha2": ("sha2", "0.11.0"),
    "hex": ("hex", "0.4.3"),
}
READONLY_CATALOG_DIGEST = "d7a1bc53c1cc25bfb839edbb99b81c03bf720441f050e86144fd6c9aa672a5a5"
READONLY_INPUTS = (
    {
        "label": "semantic",
        "path": "fixtures/motion-canvas/semantic-complete/semwright-motion.json",
        "bytes": 9643,
        "sha256": "3e98c414762f8187463b3dd0bc4ebed13fdea8f2879e63d0db015ec6efad2059",
        "project_id": "semantic-complete",
        "generation": "11111111111111111111111111111111",
    },
    {
        "label": "cues",
        "path": "fixtures/motion-canvas/audio-cues/semwright-motion.json",
        "bytes": 1760,
        "sha256": "3815bf836331dd8f5aae01d4f463d699329b20568310cd5005fa16138e944cc5",
        "project_id": "audio-cues",
        "generation": "617564696f2d63756573000000000000",
    },
)
READONLY_CALLS = (
    ("semantic", "driver.motion-canvas.doctor"),
    ("semantic", "driver.motion-canvas.semantic.types"),
    ("semantic", "driver.motion-canvas.semantic.describe"),
    ("semantic", "driver.motion-canvas.node.list"),
    ("semantic", "driver.motion-canvas.node.property.get"),
    ("semantic", "driver.motion-canvas.render.plan"),
    ("semantic", "driver.motion-canvas.doctor"),
    ("cues", "driver.motion-canvas.doctor"),
    ("cues", "driver.motion-canvas.scene.list"),
    ("cues", "driver.motion-canvas.cue.list"),
    ("cues", "driver.motion-canvas.doctor"),
)
READONLY_SCOPE = {
    "api_values_are_original_responses": True, "raw_json_rpc_wire_bytes": False,
    "render_calls": 0, "node_tool_calls": 0, "mutation_capability_calls": 0,
    "pixel_or_audio_verification": False, "seek_runtime_verification": False,
    "four_phase_production": False, "installed_product": False,
    "human_evaluation": False, "formal_acceptance": False, "release_acceptance": False,
    "full_runtime_dependency_attestation": False,
    "admission_requires_harness_exit_zero_and_caller_source_binding": True,
    "readonly_fixture_complete": True,
}

RUST_VERSION = "1.98.1"
UNIT_IDS = (
    "driver::tests::capability_catalog_and_manifest_are_stable_goldens",
    "driver::driver_tests::protocol_v7_host_managed_render_contract_is_explicit",
    "semantic::coverage_tests::managed_node_registry_is_unique_and_complete",
    "driver::driver_tests::semantic_introspection_and_property_mutation_are_revision_bound",
    "tests::render_range_is_half_open",
    "tests::rounding_boundaries_are_integer_based",
    "tests::render_profile_is_bounded",
    "tests::authoring_render_range_requires_replay_from_frame_zero",
)
HOST_IDS = (
    "motion_driver_runs_through_real_driver_host_without_network",
    "manifest_requesting_network_is_denied_without_owner_opt_in",
)
QA_METHODS = (
    "test_rust_footer_and_exact_id_positive",
    "test_rust_zero_tests_is_rejected",
    "test_rust_ignored_or_extra_ids_rejected",
    "test_rust_failed_or_duplicate_footer_rejected",
    "test_host_environment_requires_explicit_enable_and_exact_helper",
    "test_context_requires_positive_attempt_and_exact_source_job",
    "test_source_tree_rejects_duplicate_unsafe_or_link_entries",
    "test_compiler_artifacts_bind_names_and_executable_roots",
    "test_source_lock_exact_hash_and_sdk_revision",
    "test_bounded_capture_records_intent_before_launch",
    "test_output_overflow_keeps_uncertainty_and_partial_logs",
    "test_timeout_is_unknown_and_never_replayed",
    "test_late_capture_mutation_is_rejected",
    "test_existing_journal_is_never_overwritten",
    "test_source_and_tool_mutation_prevents_pass",
    "test_build_seal_uses_final_test_profile_driver",
    "test_direct_harness_preserves_seals_and_package_directory",
    "test_readonly_link_selects_exact_versions_and_seals",
    "test_readonly_link_rejects_unbound_or_incomplete_inputs",
    "test_readonly_observation_requires_complete_original_custody",
    "test_unittest_zero_skip_and_exact_method_inventory",
)
CONTEXT_KEYS = (
    "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_WORKFLOW_SHA",
    "GITHUB_WORKFLOW_REF", "GITHUB_EVENT_NAME", "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "RUNNER_OS", "RUNNER_ENVIRONMENT",
)
STREAM_CAP = 16 * 1024 * 1024
PHASE_CAP = 256 * 1024 * 1024
SOURCE_FILE_CAP = 16 * 1024 * 1024
SOURCE_TOTAL_CAP = 256 * 1024 * 1024
RUST_FOOTER = re.compile(
    r"(?m)^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; "
    r"(\d+) ignored; (\d+) measured; (\d+) filtered out; "
    r"finished in [^\r\n]+$"
)
RUST_CASE = re.compile(r"(?m)^test ([A-Za-z0-9_:]+) \.\.\. (ok|FAILED|ignored)$")


class Refused(ValueError):
    pass


class Incomplete(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def token(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns)


def regular(info):
    return stat.S_ISREG(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def plain(path, *, directory=False):
    path = Path(path)
    require(path.is_absolute(), "path must be absolute")
    require(".." not in path.parts, "parent traversal is forbidden")
    current = path
    while True:
        info = current.lstat()
        require(not stat.S_ISLNK(info.st_mode), "symlink path is forbidden")
        require(not (getattr(info, "st_file_attributes", 0) & 0x400),
                "reparse path is forbidden")
        if current == current.parent:
            break
        current = current.parent
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) if directory else regular(info),
            "unexpected filesystem object")
    return path


def file_record(path, cap=512 * 1024 * 1024):
    path = plain(path)
    before = path.lstat()
    require(0 <= before.st_size <= cap, "file byte budget exceeded")
    digest = hashlib.sha256()
    blob = hashlib.sha1(b"blob " + str(before.st_size).encode("ascii") + b"\0")
    count = 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb", buffering=0) as stream:
        require(token(os.fstat(stream.fileno())) == token(before),
                "file changed before opening")
        while True:
            chunk = stream.read(min(1024 * 1024, before.st_size - count + 1))
            if not chunk:
                break
            count += len(chunk)
            require(count <= before.st_size and count <= cap,
                    "file grew beyond its original byte budget")
            digest.update(chunk)
            blob.update(chunk)
        require(token(os.fstat(stream.fileno())) == token(before),
                "file identity changed while reading")
    require(count == before.st_size and token(path.lstat()) == token(before),
            "file changed after reading")
    return {"path": str(path), "bytes": count, "sha256": digest.hexdigest(),
            "git_blob": blob.hexdigest(), "identity": list(token(before))}


def same_file(before, after, label):
    require(before == after, label + " changed")


def read_recorded(record, cap=SOURCE_FILE_CAP):
    path = Path(record["path"])
    before = file_record(path, cap)
    same_file(record, before, "recorded input")
    with path.open("rb") as stream:
        data = stream.read(min(cap, before["bytes"]) + 1)
    require(len(data) == before["bytes"], "recorded input grew or truncated")
    same_file(before, file_record(path, cap), "recorded input")
    return data


def write_json_new(path, value):
    path = Path(path)
    payload = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)
               + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    record = file_record(path, PHASE_CAP)
    require(record["sha256"] == hashlib.sha256(payload).hexdigest(),
            "JSON readback differs")
    return record


def read_json_record(path, cap=SOURCE_FILE_CAP):
    record = file_record(path, cap)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON member")
            result[key] = value
        return result
    value = json.loads(read_recorded(record, cap).decode("utf-8"),
                       object_pairs_hook=pairs,
                       parse_constant=lambda value: (_ for _ in ()).throw(
                           Refused("nonfinite JSON number")))
    return record, value


def validate_context(env, expected):
    require(re.fullmatch(r"[0-9a-f]{40}", expected) is not None,
            "expected source must be a full lowercase SHA")
    require(env.get("GITHUB_ACTIONS") == "true"
            and env.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "a disposable GitHub-hosted runner is required")
    require(env.get("GITHUB_REPOSITORY") == "seradotcom/motionwright",
            "wrong repository")
    require(env.get("GITHUB_SHA") == expected
            and env.get("GITHUB_WORKFLOW_SHA") == expected,
            "verifier source differs from workflow context")
    require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
            "only the manual SDK lane may run this command")
    require(re.fullmatch(r"[1-9][0-9]*",
                         env.get("GITHUB_RUN_ATTEMPT", "")) is not None,
            "run attempt must be a positive decimal integer")
    require(re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ID", "")) is not None,
            "run identity missing")
    require(env.get("GITHUB_JOB") == "sdk-contracts"
            and env.get("RUNNER_OS") == "Linux",
            "wrong job or platform")
    prefix = "seradotcom/motionwright/" + WORKFLOW + "@refs/heads/"
    require(env.get("GITHUB_WORKFLOW_REF", "").startswith(prefix)
            and len(env["GITHUB_WORKFLOW_REF"]) > len(prefix),
            "wrong workflow ref")
    context = {key: env.get(key, "") for key in CONTEXT_KEYS}
    context["GITHUB_RUN_ATTEMPT"] = int(env["GITHUB_RUN_ATTEMPT"])
    return context


def validate_host_environment(env, helper):
    require(env.get("SEMWRIGHT_TEST_MOTION_CANVAS") == "1",
            "Host tests need explicit enable=1; ignored alone can return early")
    require(Path(env.get("SEMWRIGHT_TEST_SANDBOX_HELPER", "")) == helper
            and helper.is_absolute(),
            "Host tests must use the exact built sandbox helper")
    forbidden = ("SEMWRIGHT_TEST_MOTION_CANVAS_RENDER",
                 "SEMWRIGHT_TEST_MOTION_RUNTIME", "SEMWRIGHT_TEST_MOTION_NODE")
    require(not any(env.get(key) for key in forbidden),
            "render runtime is outside the SDK G1 selection")


def parse_tree(data):
    require(data.endswith(b"\0") and 0 < len(data) <= 4 * 1024 * 1024,
            "incomplete or oversized Git tree")
    result = {}
    for row in data[:-1].split(b"\0"):
        meta, separator, name = row.partition(b"\t")
        require(separator == b"\t", "Git tree record has no path")
        fields = meta.decode("ascii").split(" ")
        require(len(fields) == 3 and fields[0] in ("100644", "100755")
                and fields[1] == "blob"
                and re.fullmatch(r"[0-9a-f]{40}", fields[2]) is not None,
                "source must contain regular Git blobs")
        path = name.decode("utf-8")
        parts = PurePosixPath(path).parts
        require(path and not path.startswith("/") and "\\" not in path
                and ":" not in path and "\x00" not in path
                and all(p not in ("", ".", "..", ".git") for p in parts)
                and "/".join(parts) == path,
                "unsafe Git source path")
        require(path not in result, "duplicate Git source path")
        result[path] = {"mode": fields[0], "git_blob": fields[2]}
    require(0 < len(result) <= 10000, "Git source inventory outside budget")
    return result


def parse_rust_result(stdout, expected, *, host=False):
    text = stdout.decode("utf-8", "strict").replace("\r\n", "\n")
    footer = RUST_FOOTER.findall(text)
    require(len(footer) == 1, "expected exactly one original Rust footer")
    state, passed, failed, ignored, measured, filtered = footer[0]
    counts = dict(zip(("passed", "failed", "ignored", "measured", "filtered"),
                      map(int, (passed, failed, ignored, measured, filtered))))
    rows = RUST_CASE.findall(text)
    ids = [name for name, _ in rows]
    running = re.findall(r"(?m)^running (\d+) tests?$", text)
    require(len(running) == 1 and int(running[0]) == len(expected)
            and len(expected) > 0, "zero or unexpected Rust selection")
    require(state == "ok" and counts["passed"] == len(expected)
            and counts["failed"] == counts["ignored"] == counts["measured"] == 0,
            "Rust failure or omission is not a complete selected result")
    require(len(ids) == len(set(ids)) == len(expected)
            and set(ids) == set(expected) and all(s == "ok" for _, s in rows),
            "Rust case IDs differ from the original closed selection")
    require(not host or counts["filtered"] == 0,
            "Host target has omitted cases")
    return {"selected_ids": ids, "counts": counts,
            "filtered_are_unselected_library_cases": not host,
            "physical_native_render": False, "human_acceptance": False}


def parse_unittest_result(stderr, module_name):
    text = stderr.decode("utf-8", "strict").replace("\r\n", "\n")
    runs = re.findall(r"(?m)^Ran (\d+) tests? in [^\r\n]+$", text)
    endings = re.findall(r"(?m)^(OK|FAILED)(?: \(([^\r\n]*)\))?$", text)
    require(runs == [str(len(QA_METHODS))] and endings == [("OK", "")],
            "synthetic test count, result or omissions differ")
    rows = re.findall(r"(?m)^(test_[A-Za-z0-9_]+) "
                      r"\(([A-Za-z0-9_.]+)\) \.\.\. ok$", text)
    require(len(rows) == len(QA_METHODS)
            and {name for name, _ in rows} == set(QA_METHODS),
            "synthetic method inventory differs")
    require(all(owner == module_name + ".SdkG1Controls." + name
                for name, owner in rows), "synthetic module identity differs")
    return {"tests_run": len(rows), "passed": len(rows), "skipped": 0,
            "failures": 0, "errors": 0, "expected_failures": 0,
            "unexpected_successes": 0, "method_ids": [owner for _, owner in rows],
            "scope": "Synthetic verification infrastructure only"}


def compiler_artifacts(data, target_root, expected, source_root):
    records = {}
    finished = []
    for line in data.decode("utf-8", "strict").splitlines():
        if not line:
            continue
        obj = json.loads(line)
        require(isinstance(obj, dict), "Cargo JSON message is not an object")
        reason = obj.get("reason")
        if reason == "build-finished":
            finished.append(obj.get("success"))
        if reason != "compiler-artifact" or not obj.get("executable"):
            continue
        target = obj.get("target", {})
        is_test = obj.get("profile", {}).get("test")
        key = (target.get("name"), is_test)
        if key not in expected:
            continue
        require(type(is_test) is bool, "Cargo test flag must be a boolean")
        require(key not in records, "duplicate selected Cargo artifact")
        executable = Path(obj["executable"])
        require(executable.is_absolute() and executable.is_relative_to(target_root),
                "Cargo executable is outside the isolated target")
        source = Path(target.get("src_path", ""))
        require(source.is_absolute() and source == source_root / expected[key],
                "Cargo executable belongs to another source target")
        records[key] = file_record(executable)
    require(len(finished) == 1 and finished[0] is True
            and len(records) == len(expected),
            "Cargo did not finish every selected build target")
    return records


def readonly_link_inputs(data, target_root, sdk_root, cargo_home):
    """Select this build's exact direct libraries; never glob an old executable."""
    target_root = plain(target_root, directory=True)
    sdk_root = plain(sdk_root, directory=True)
    cargo_home = plain(cargo_home, directory=True)
    expected = set(READONLY_SDK_LIBRARIES) | set(READONLY_REGISTRY_LIBRARIES)
    registry = "registry+https://github.com/rust-lang/crates.io-index#"
    records = {}
    native = []
    finished = []
    for line in data.decode("utf-8", "strict").splitlines():
        if not line:
            continue
        obj = json.loads(line)
        require(isinstance(obj, dict), "Cargo link input is not an object")
        reason = obj.get("reason")
        if reason == "build-finished":
            finished.append(obj.get("success"))
        if reason == "build-script-executed" and (
                obj.get("linked_paths") or obj.get("linked_libs")):
            require(obj.get("package_id") == registry + "libsqlite3-sys@0.38.2",
                    "unselected native dependency appeared in Cargo link inputs")
            directory = Path(obj.get("out_dir", ""))
            require(directory.is_absolute()
                    and directory.is_relative_to(target_root / "debug" / "build")
                    and directory.name == "out",
                    "native library is outside this build")
            plain(directory, directory=True)
            require(obj.get("linked_paths") == ["native=" + str(directory)]
                    and obj.get("linked_libs") == ["static=sqlite3"],
                    "readonly fixture requires the original static SQLite link")
            native.append({
                "package_id": obj["package_id"], "directory": str(directory),
                "archive": file_record(directory / "libsqlite3.a"),
            })
        if reason != "compiler-artifact":
            continue
        target = obj.get("target", {})
        profile = obj.get("profile", {})
        require(isinstance(target, dict) and isinstance(profile, dict),
                "Cargo target or profile is not an object")
        name = target.get("name")
        if name not in expected or profile.get("test") is not False:
            continue
        if name in READONLY_SDK_LIBRARIES:
            folder = sdk_root / "crates" / READONLY_SDK_LIBRARIES[name]
            package_id = ("path+" + folder.as_uri() + "#"
                          + name.replace("_", "-") + "@1.0.0")
            source = folder / "src" / "lib.rs"
        else:
            crate, version = READONLY_REGISTRY_LIBRARIES[name]
            package_id = registry + crate + "@" + version
            source = Path(target.get("src_path", ""))
            if obj.get("package_id") != package_id:
                # SDK54 also builds sha2 0.10; it is not this fixture's dependency.
                continue
            registry_root = cargo_home / "registry" / "src"
            require(source.is_absolute() and source.is_relative_to(registry_root),
                    "registry entrypoint is outside the isolated Cargo home")
            relative = source.relative_to(registry_root)
            require(len(relative.parts) == 4
                    and relative.parts[1:] == (crate + "-" + version, "src", "lib.rs"),
                    "registry entrypoint is outside this isolated package")
        require(obj.get("package_id") == package_id,
                "readonly SDK library package identity differs")
        require(target.get("kind") == ["lib"]
                and target.get("crate_types") == ["lib"]
                and target.get("src_path") == str(source)
                and obj.get("executable") is None,
                "readonly dependency is not the original library target")
        require(name not in records, "duplicate selected readonly Cargo library")
        filenames = obj.get("filenames")
        require(isinstance(filenames, list)
                and all(isinstance(value, str) for value in filenames),
                "Cargo library filenames are malformed")
        libraries = [Path(value) for value in filenames if value.endswith(".rlib")]
        require(len(libraries) == 1, "Cargo did not identify exactly one direct rlib")
        library = libraries[0]
        require(library.is_absolute()
                and library.parent == target_root / "debug" / "deps"
                and library.name.startswith("lib" + name + "-"),
                "direct rlib is outside this build or belongs to another target")
        records[name] = {
            "package_id": package_id, "entrypoint": file_record(source, SOURCE_FILE_CAP),
            "artifact": file_record(library), "features": obj.get("features"),
            "profile": profile, "fresh": obj.get("fresh"),
        }
    require(len(finished) == 1 and finished[0] is True
            and set(records) == expected and len(native) == 1,
            "Cargo did not complete all eleven direct libraries and static SQLite")
    return {
        "cargo_json_sha256": hashlib.sha256(data).hexdigest(),
        "externs": records, "native": native[0],
        "direct_library_count": len(records),
        "library_bytes_retained": False,
        "binary_hashes_are_observations_not_independent_authentication": True,
    }


def readonly_dependency_snapshot(target_root):
    """Seal all files exposed to rustc through its dependency search directory."""
    directory = plain(target_root / "debug" / "deps", directory=True)
    entries = sorted(directory.iterdir())
    require(0 < len(entries) <= 4096, "dependency directory exceeds entry budget")
    records = {}
    total = 0
    for path in entries:
        record = file_record(path)
        total += record["bytes"]
        require(total <= 4 * 1024 * 1024 * 1024,
                "dependency directory exceeds byte budget")
        records[path.name] = record
    return records


def check_readonly_link_seals(inputs, dependency_records):
    for name, value in inputs["externs"].items():
        for key in ("artifact", "entrypoint"):
            record = value[key]
            same_file(record, file_record(Path(record["path"])),
                      "readonly " + name + " " + key)
    archive = inputs["native"]["archive"]
    same_file(archive, file_record(Path(archive["path"])), "readonly static SQLite")
    target_root = Path(next(iter(dependency_records.values()))["path"]).parents[2]
    require(readonly_dependency_snapshot(target_root) == dependency_records,
            "readonly dependency search files changed")


def stop_process(process):
    if process is None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.poll() is None:
            process.terminate()
    except (ProcessLookupError, OSError):
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


class CommandRunner:
    def __init__(self, root, context, env, *, stream_cap=STREAM_CAP,
                 total_cap=PHASE_CAP):
        self.root = plain(Path(root), directory=True)
        self.context = dict(context)
        self.env = dict(env)
        self.stream_cap = stream_cap
        self.total_cap = total_cap
        self.bytes_written = 0
        self.lock = threading.Lock()
        self.index = 0
        self.commands = []
        self.launches = 0
        self.active = None
        self.uncertain = False

    def command(self, label, argv, cwd, timeout, env=None):
        require(not self.uncertain, "an uncertain command prevents later commands")
        require(re.fullmatch(r"[a-z0-9-]+", label) is not None, "unsafe command label")
        require(math.isfinite(timeout) and timeout > 0, "invalid command timeout")
        require(argv and all(isinstance(a, str) and "\0" not in a for a in argv),
                "invalid command arguments")
        executable = plain(Path(argv[0]))
        tool_before = file_record(executable)
        cwd = plain(Path(cwd), directory=True)
        folder = self.root / f"{self.index:03d}-{label}"
        self.index += 1
        folder.mkdir()
        intent = {
            "schema": "motionwright.sdk-g1.command-intent/1",
            "state": "UNKNOWN_UNTIL_OUTCOME", "timestamp": utc(),
            "label": label, "argv": argv, "cwd": str(cwd),
            "timeout_seconds": timeout, "stream_byte_cap": self.stream_cap,
            "phase_byte_cap": self.total_cap,
            "context": self.context, "executable_before": tool_before,
            "sdk_source_sha_expected": SDK_SHA,
            "required_host_environment": {
                key: (self.env if env is None else env).get(key, "")
                for key in ("SEMWRIGHT_TEST_MOTION_CANVAS",
                            "SEMWRIGHT_TEST_SANDBOX_HELPER")},
        }
        intent_record = write_json_new(folder / "intent.json", intent)
        paths = {name: folder / (name + ".original") for name in ("stdout", "stderr")}
        files = {}
        threads = []
        errors = []
        overflow = threading.Event()
        done = {}
        process = None
        exit_code = None
        timed_out = False
        interruption = None
        outcome_record = None
        started_at = utc()
        begin = time.monotonic()
        child_env = self.env if env is None else env

        def drain(name, pipe, destination):
            count = 0
            try:
                while True:
                    chunk = pipe.read(min(65536, self.stream_cap - count + 1))
                    if not chunk:
                        break
                    with self.lock:
                        allowed = max(0, min(len(chunk), self.stream_cap - count,
                                             self.total_cap - self.bytes_written))
                        if allowed:
                            destination.write(chunk[:allowed])
                            destination.flush()
                            count += allowed
                            self.bytes_written += allowed
                        if allowed != len(chunk):
                            errors.append(name + " exceeded capture byte budget")
                            overflow.set()
                            break
                destination.flush()
                os.fsync(destination.fileno())
            except (OSError, ValueError) as exc:
                errors.append(name + " capture error: " + type(exc).__name__)
                overflow.set()
            finally:
                done[name] = count
                try:
                    pipe.close()
                except OSError:
                    pass

        try:
            for name, path in paths.items():
                files[name] = path.open("xb")
            process = subprocess.Popen(
                argv, cwd=cwd, env=child_env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=(os.name == "posix"),
            )
            self.launches += 1
            self.active = process
            for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                worker = threading.Thread(target=drain, args=(name, pipe, files[name]),
                                          daemon=True)
                worker.start()
                threads.append(worker)
            while process.poll() is None:
                if overflow.is_set():
                    interruption = "capture budget or pipe failure"
                    break
                if time.monotonic() - begin > timeout:
                    timed_out = True
                    interruption = "command timeout"
                    break
                time.sleep(0.02)
            if interruption is not None:
                stop_process(process)
            exit_code = process.poll()
        except BaseException as exc:
            interruption = type(exc).__name__ + ": " + str(exc)[:500]
            stop_process(process)
            exit_code = None if process is None else process.poll()
        finally:
            # Persist return/timeout before hashing logs or interpreting test output.
            outcome = {
                "schema": "motionwright.sdk-g1.process-outcome/1",
                "timestamp": utc(), "started_at": started_at,
                "process_started": process is not None,
                "pid": None if process is None else process.pid,
                "exit_code": exit_code, "timed_out": timed_out,
                "interruption": interruption,
                "duration_seconds": round(time.monotonic() - begin, 6),
                "context": self.context, "intent": intent_record,
                "stream_paths": {k: str(v) for k, v in paths.items()},
                "drain_and_test_validation": "PENDING",
            }
            try:
                outcome_record = write_json_new(folder / "outcome.json", outcome)
            except (OSError, ValueError):
                errors.append("original outcome journal could not be completed")
            for worker in threads:
                worker.join(timeout=2)
            if any(worker.is_alive() for worker in threads):
                stop_process(process)
                for worker in threads:
                    worker.join(timeout=2)
            alive = any(worker.is_alive() for worker in threads)
            if alive:
                errors.append("capture writer remains alive")
            else:
                for stream in files.values():
                    stream.close()
            self.active = None
        logs = {}
        if not alive:
            try:
                logs = {name: file_record(path, self.stream_cap)
                        for name, path in paths.items()}
            except (OSError, ValueError) as exc:
                errors.append("log readback failed: " + type(exc).__name__)
        try:
            tool_after = file_record(executable)
            same_file(tool_before, tool_after, "command executable")
        except (OSError, ValueError) as exc:
            tool_after = None
            errors.append("tool identity check failed: " + type(exc).__name__)
        uncertain = bool(interruption or timed_out or errors or alive
                         or outcome_record is None or type(exit_code) is not int)
        status = "UNKNOWN" if uncertain else "COMPLETE" if exit_code == 0 else "FAIL"
        result = {
            "schema": "motionwright.sdk-g1.command-capture/1",
            "label": label, "status": status, "context": self.context,
            "intent": intent_record, "outcome": outcome_record,
            "exit_code": exit_code, "timed_out": timed_out,
            "interruption": interruption, "errors": errors,
            "writers_alive": alive, "logs": logs,
            "executable_before": tool_before, "executable_after": tool_after,
            "argv": argv, "cwd": str(cwd),
        }
        try:
            result["capture_record"] = write_json_new(folder / "capture.json", result)
        except (OSError, ValueError) as exc:
            self.uncertain = True
            raise Incomplete("capture receipt could not be completed") from exc
        self.commands.append(result)
        if status == "UNKNOWN":
            self.uncertain = True
            raise Incomplete("command is uncertain: " + label)
        require(status == "COMPLETE", "command failed: " + label)
        return result

    def bytes(self, command, stream="stdout"):
        require(command["status"] == "COMPLETE", "incomplete command cannot be read")
        return read_recorded(command["logs"][stream], self.stream_cap)


def safe_environment(original):
    env = {k: v for k, v in original.items()
           if not k.startswith(("GIT_", "CARGO_", "RUST", "PYTHON", "SEMWRIGHT_"))
           and not k.endswith(("_TOKEN", "_SECRET", "_PASSWORD"))
           and k not in ("LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "BASH_ENV", "ENV",
                        "CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS")}
    for key in ("CARGO_HOME", "CARGO_TARGET_DIR", "RUSTUP_HOME",
                "TMPDIR", "TMP", "TEMP"):
        value = original.get(key, "")
        require(value and Path(value).is_absolute(), key + " must be isolated")
        env[key] = value
    env.update(
        GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
        GIT_NO_LAZY_FETCH="1", GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0",
        PYTHONDONTWRITEBYTECODE="1", CARGO_INCREMENTAL="0",
        CARGO_PROFILE_DEV_DEBUG="0", CARGO_PROFILE_TEST_DEBUG="0",
        CARGO_TERM_COLOR="never", CARGO_BUILD_JOBS="2",
        RUSTUP_TOOLCHAIN=RUST_VERSION, LANG="C.UTF-8", LC_ALL="C.UTF-8",
    )
    return env


def git_bytes(runner, git_exe, root, label, *args):
    argv = [str(git_exe), "--no-replace-objects", "-c", "core.fsmonitor=false",
            "-c", "advice.graftFileDeprecated=false", *args]
    result = runner.command(label, argv, root, 30)
    return runner.bytes(result)


def source_snapshot(runner, git_exe, root, expected, label, expected_tree=None):
    head = git_bytes(runner, git_exe, root, label + "-head",
                     "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    require(head == expected, "source checkout differs from expected SHA")
    commit = git_bytes(runner, git_exe, root, label + "-commit",
                       "cat-file", "commit", expected)
    object_id = hashlib.sha1(b"commit " + str(len(commit)).encode("ascii")
                            + b"\0" + commit).hexdigest()
    require(object_id == expected, "raw source commit identity differs")
    match = re.match(rb"tree ([0-9a-f]{40})\n", commit)
    require(match is not None, "raw commit has no canonical tree")
    tree = match[1].decode("ascii")
    require(expected_tree is None or tree == expected_tree, "SDK tree differs")
    for suffix, argv in (
        ("status", ("status", "--porcelain=v1", "-z", "--untracked-files=all")),
        ("ignored", ("ls-files", "--others", "--ignored", "--exclude-standard", "-z")),
    ):
        require(git_bytes(runner, git_exe, root, label + "-" + suffix, *argv) == b"",
                "tracked, untracked or ignored source input is not clean")
    listing = git_bytes(runner, git_exe, root, label + "-tree",
                        "ls-tree", "-r", "-z", expected)
    entries = parse_tree(listing)
    files = {}
    count = 0
    for relative, entry in sorted(entries.items()):
        record = file_record(root / relative, SOURCE_FILE_CAP)
        require(record["git_blob"] == entry["git_blob"],
                "working source bytes differ from raw Git: " + relative)
        count += record["bytes"]
        require(count <= SOURCE_TOTAL_CAP, "source inventory exceeds byte budget")
        files[relative] = {"bytes": record["bytes"], "sha256": record["sha256"],
                           "git_blob": entry["git_blob"], "mode": entry["mode"]}
    return {"source_sha": head, "tree": tree, "root": str(root),
            "tracked_files": files, "tracked_bytes": count,
            "listing_sha256": hashlib.sha256(listing).hexdigest(),
            "ignored_and_untracked_empty": True,
            "source_claim": "Observed raw Git and all checkout file bytes"}


def verify_source_lock(mw, sdk=None):
    record, value = read_json_record(mw / "SOURCE_LOCK.json")
    require(record["sha256"] == MW_LOCK, "Motionwright SOURCE_LOCK changed")
    require(value["dependencies"]["semwright"]["revision"] == SDK_SHA,
            "Motionwright no longer selects the reviewed SDK")
    if sdk is not None:
        require(file_record(sdk / "Cargo.lock")["sha256"] == SDK_LOCK,
                "SDK Cargo.lock differs from the original reviewed bytes")
        require(file_record(sdk / POLICY_PATH)["sha256"] == POLICY_SHA,
                "original scoped sandbox script differs")
    return record


def test_catalog(path):
    record = file_record(path)
    module = ast.parse(read_recorded(record).decode("utf-8"))
    classes = [node for node in module.body
               if isinstance(node, ast.ClassDef) and node.name == "SdkG1Controls"]
    require(len(classes) == 1, "synthetic test class differs")
    names = [node.name for node in classes[0].body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name.startswith("test_")]
    require(len(names) == len(set(names)) == len(QA_METHODS)
            and set(names) == set(QA_METHODS), "synthetic test source inventory differs")
    require(not any(node.decorator_list for node in classes[0].body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_")),
            "test decorators require another explicit review")
    return record


def require_same_snapshot(before, after):
    require(before == after, "source snapshot changed during this phase")


def find_tool(name):
    found = shutil.which(name)
    require(found is not None, "required tool is missing: " + name)
    return plain(Path(found).resolve(strict=True))


def build_records_json(records):
    return [{"target": name, "test_harness": is_test, "file": record}
            for (name, is_test), record in sorted(records.items())]


def record_binaries(records):
    for (name, _), record in records.items():
        same_file(record, file_record(Path(record["path"])),
                  "built executable " + name)


def prepare_original_executables(runner, cargo, sdk):
    # Integration-test preparation also builds the driver binary. Seal that
    # final test-profile artifact, never the earlier development-profile file.
    sandbox = runner.command(
        "build-original-sandbox",
        [cargo, "build", "--locked", "--message-format=json",
         "-p", "semwright-plugin-host", "--bin", "semwright-sandbox"],
        sdk, 1200,
    )
    target = Path(runner.env["CARGO_TARGET_DIR"])
    records = compiler_artifacts(
        runner.bytes(sandbox), target,
        {("semwright-sandbox", False):
             "crates/plugin-host/src/semwright-sandbox.rs"}, sdk,
    )
    preparation = runner.command(
        "prepare-original-driver-and-two-test-harnesses",
        [cargo, "test", "--locked", "-p", "semwright-driver-motion-canvas",
         "--no-run", "--lib", "--test", "host_conformance",
         "--message-format=json"],
        sdk, 900,
    )
    prepared = compiler_artifacts(
        runner.bytes(preparation), target,
        {("semwright_driver_motion_canvas", True):
             "crates/driver-motion-canvas/src/lib.rs",
         ("host_conformance", True):
             "crates/driver-motion-canvas/tests/host_conformance.rs",
         ("semwright-motion-canvas-driver", False):
             "crates/driver-motion-canvas/src/main.rs"},
        sdk,
    )
    records.update(prepared)
    record_binaries(records)
    return records, {
        "sandbox_build": sandbox, "test_harness_preparation": preparation,
        "compiled_executables": build_records_json(records),
        "preparation_executed_test_cases": 0,
        "execution_uses_prepared_harnesses_without_cargo": True,
        "binary_hashes_are_observed_not_independently_authenticated": True,
        "compiled_executable_bytes_retained": False,
    }


def execute_original_harness(runner, label, records, target, arguments, sdk,
                             *, env=None):
    require(target in (("semwright_driver_motion_canvas", True),
                       ("host_conformance", True)) and target in records,
            "an original prepared SDK test harness is required")
    record_binaries(records)
    # Match Cargo's test working directory. Compile-time Cargo paths remain
    # embedded in these original harnesses; execution never invokes a compiler.
    result = runner.command(
        label, [records[target]["path"], *arguments],
        sdk / "crates/driver-motion-canvas", 180, env=env,
    )
    record_binaries(records)
    return result


def readonly_json(text):
    require(type(text) is str, "original serialized JSON must be UTF8 text")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate original JSON member")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(
                          Refused("nonfinite original JSON number")))


def readonly_same_json(left, right, label):
    # Compare JSON value types as well as contents (Python True == 1 is unsafe).
    encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      allow_nan=False, separators=(",", ":"))
    require(encode(left) == encode(right), label + " differs")


def readonly_serialized(record, value_key, prefix):
    raw = record[prefix + "_utf8"].encode("utf-8")
    require(0 < len(raw) <= 4 * 1024 * 1024
            and type(record[prefix + "_bytes"]) is int
            and len(raw) == record[prefix + "_bytes"]
            and hashlib.sha256(raw).hexdigest() == record[prefix + "_sha256"],
            "original serialized API value bytes or digest differ")
    readonly_same_json(readonly_json(raw.decode("utf-8")), record[value_key],
                       "serialized API value")
    return record[value_key]


def readonly_file_join(record, expected):
    require(set(record) == {"path", "bytes", "sha256", "identity"},
            "readonly file observation has an unexpected shape")
    require(record["path"] == expected["path"]
            and type(record["bytes"]) is int
            and record["bytes"] == expected["bytes"]
            and record["sha256"] == expected["sha256"],
            "readonly file is not the externally sealed input")
    identity = record["identity"]
    require(set(identity) == {
        "device", "inode", "size", "mode", "uid", "gid", "nlink",
        "mtime_seconds", "mtime_nanoseconds", "ctime_seconds", "ctime_nanoseconds"},
        "incomplete readonly file identity")
    require(all(type(value) is int for value in identity.values())
            and identity["size"] == record["bytes"]
            and identity["nlink"] > 0 and stat.S_ISREG(identity["mode"])
            and 0 <= identity["mtime_nanoseconds"] < 1000000000
            and 0 <= identity["ctime_nanoseconds"] < 1000000000,
            "invalid readonly regular-file identity")
    observed = [
        identity["device"], identity["inode"], identity["size"],
        identity["mtime_seconds"] * 1000000000 + identity["mtime_nanoseconds"],
        identity["ctime_seconds"] * 1000000000 + identity["ctime_nanoseconds"],
    ]
    require(observed == expected["identity"],
            "readonly input identity differs from the caller's seal")


def _validate_readonly_observation(report, sdk_root, output, private_work,
                                   expected_files, original_stdout, report_record):
    require(type(report) is dict and set(report) == {
        "schema", "started_unix_ms", "terminal", "sdk_source_expected",
        "fixture_source_role", "output_directory", "inputs", "hosts", "calls",
        "events", "input_postcheck", "scope", "private_work_root",
        "private_work_upload_allowed", "observed_files", "source_root",
        "counts_expected", "ended_unix_ms", "elapsed_millis", "counts"},
        "readonly report is incomplete or has an unreviewed field")
    require(report["schema"] == "motionwright.motion-canvas-readonly-host.v1"
            and report["terminal"] == "PASSED"
            and report["fixture_source_role"] == "NEW_SEPARATE_LIBTEST",
            "readonly fixture did not reach its required terminal")
    readonly_same_json(report["sdk_source_expected"], {
        "commit": SDK_SHA, "tree": SDK_TREE,
        "complete_checkout_binding": "REQUIRED_FROM_CALLER"}, "SDK source identity")
    require(report["source_root"] == str(sdk_root)
            and report["output_directory"] == str(output)
            and report["private_work_root"] == str(private_work)
            and report["private_work_upload_allowed"] is False,
            "readonly resource roots or upload boundary differ")
    for root in (sdk_root, output):
        require(not private_work.is_relative_to(root)
                and not root.is_relative_to(private_work),
                "readonly private binaries overlap source or evidence")
    readonly_same_json(report["scope"], READONLY_SCOPE, "readonly scope")
    readonly_same_json(report["counts_expected"], {
        "provider_connections": 2, "provider_shutdowns": 2, "execute_calls": 11,
        "libtest_methods": 1, "original_fixture_files": 2}, "declared fixture counts")
    readonly_same_json(report["counts"], {
        "provider_connections_attempted": 2, "provider_connections_completed": 2,
        "provider_shutdowns_completed": 2, "execute_calls_attempted": 11,
        "execute_calls_completed": 11}, "actual fixture counts")
    labels = [item["label"] for item in READONLY_INPUTS]
    observed_keys = {"sdk_lock", "driver", "sandbox",
                     "new_fixture_source", "new_fixture_harness"}
    require(set(expected_files) == observed_keys | set(labels)
            and set(report["observed_files"]) == observed_keys,
            "readonly external input set is not closed")
    for label, record in report["observed_files"].items():
        readonly_file_join(record, expected_files[label])
    require(expected_files["sdk_lock"]["sha256"] == SDK_LOCK,
            "readonly SDK lock has a different source")
    require(type(report["inputs"]) is list and len(report["inputs"]) == 2,
            "original readonly inputs are omitted or duplicated")
    watched = dict(report["observed_files"])
    for item, pin in zip(report["inputs"], READONLY_INPUTS):
        label = pin["label"]
        require(set(item) == {"label", "relative_source_path", "record",
                              "original_utf8", "project_identity_before"}
                and item["label"] == label and item["relative_source_path"] == pin["path"],
                "readonly source fixture order or path differs")
        expected = expected_files[label]
        require(expected["path"] == str(sdk_root / pin["path"])
                and expected["sha256"] == pin["sha256"]
                and expected["bytes"] == pin["bytes"], "readonly fixture pin differs")
        readonly_file_join(item["record"], expected)
        raw = item["original_utf8"].encode("utf-8")
        require(len(raw) == pin["bytes"]
                and hashlib.sha256(raw).hexdigest() == pin["sha256"],
                "original fixture UTF8 was changed or omitted")
        identity = {"id": pin["project_id"], "generation": pin["generation"], "revision": 1}
        readonly_same_json(item["project_identity_before"], identity,
                           "original project identity")
        project = readonly_json(item["original_utf8"])
        readonly_same_json({key: project[key] for key in identity}, identity,
                           "original project bytes and identity")
        require(project["assets"] == [] and project["audio"] == [],
                "readonly fixture unexpectedly includes media")
        watched[label] = item["record"]
    post = report["input_postcheck"]
    require(type(post) is list and len(post) == 7
            and len({row["path"] for row in post}) == 7,
            "readonly watched input observations are missing or duplicated")
    by_path = {row["path"]: row for row in watched.values()}
    require(set(by_path) == {row["path"] for row in post}, "readonly postcheck set differs")
    for row in post:
        require(set(row) == {"path", "before", "after", "unchanged"}
                and row["unchanged"] is True, "readonly input mutation or unknown result")
        readonly_same_json(row["before"], by_path[row["path"]], "watched original")
        readonly_same_json(row["after"], row["before"], "watched after-state")

    hosts = report["hosts"]
    require(type(hosts) is list and len(hosts) == 2
            and [row["label"] for row in hosts] == labels, "readonly Host set differs")
    host_by_label = {}
    for host, pin in zip(hosts, READONLY_INPUTS):
        require(set(host) == {
            "label", "state", "manifest", "grants", "network_opt_in",
            "project_before", "driver_before", "host_state_directory", "shutdown",
            "identity_before", "identity_after", "catalog_before", "catalog_after",
            "project_after", "driver_after", "project_identity_after"}
            and host["state"] == "COMPLETED" and host["network_opt_in"] is False,
            "readonly Host lifecycle is incomplete")
        root = private_work / host["label"]
        require(host["host_state_directory"] == str(root / "host-state"),
                "Host state escaped its private root")
        readonly_same_json(host["grants"], [{
            "name": "project", "path": str(root / "project"), "read": True, "write": False}],
            "readonly grants")
        readonly_same_json(host["shutdown"], {
            "state": "COMPLETED", "closed_token_cancelled": True}, "Host shutdown")
        readonly_same_json(host["identity_before"], {
            "id": "driver:motion-canvas", "kind": "driver", "version": "1.0.0",
            "namespace": "driver.motion-canvas.", "application": None,
            "origin": "driver-manifest:motionwright-ci-fixture"}, "original provider identity")
        readonly_same_json(host["identity_after"], host["identity_before"], "Host identity")
        before = host["project_before"]
        require(set(before) == {"directory", "directory_identity", "file"}
                and before["directory"] == str(root / "project")
                and before["file"]["path"] == str(root / "project/semwright-motion.json")
                and before["file"]["sha256"] == pin["sha256"]
                and before["file"]["bytes"] == pin["bytes"]
                and before["file"]["identity"]["nlink"] == 1
                and before["directory_identity"]["mode"] & 0o077 == 0,
                "private project copy identity or content differs")
        readonly_same_json(host["project_after"], before, "private project after-state")
        readonly_same_json(host["project_identity_after"], {
            "id": pin["project_id"], "generation": pin["generation"], "revision": 1,
            "fingerprint": pin["sha256"], "derived_from_unchanged_original_bytes": True},
            "private project revision")
        driver = host["driver_before"]
        require(driver["path"] == str(root / "bin/semwright-motion-canvas-driver")
                and driver["sha256"] == expected_files["driver"]["sha256"]
                and driver["bytes"] == expected_files["driver"]["bytes"]
                and driver["identity"]["nlink"] == 1
                and driver["identity"]["mode"] & 0o077 == 0,
                "private executable differs from the sealed original driver")
        readonly_same_json(host["driver_after"], driver, "private executable after-state")
        manifest = host["manifest"]
        require(manifest["executable"] == driver["path"]
                and manifest["sha256"] == driver["sha256"]
                and manifest["network"] is False
                and manifest["tools"] == [] and manifest["secrets"] == [],
                "readonly Host manifest changes authority or executable")
        readonly_same_json(manifest["mounts"], [
            {"root": "project", "read_only": True, "execute": False}],
            "readonly mount authority")
        catalog = host["catalog_before"]
        require(catalog["original_sdk_digest"] == READONLY_CATALOG_DIGEST,
                "original SDK catalog digest differs")
        capabilities = readonly_serialized(catalog, "capabilities", "serialized_value")
        require(type(capabilities) is list and len(capabilities) == 33
                and len({row["descriptor"]["name"] for row in capabilities}) == 33,
                "original SDK capability set is missing or ambiguous")
        readonly_same_json(host["catalog_after"], catalog, "Host capability after-state")
        host_by_label[host["label"]] = host

    calls = report["calls"]
    require(type(calls) is list and len(calls) == 11
            and len({row["request_id"] for row in calls}) == 11,
            "readonly query outcomes are missing, duplicated or replayed")
    for index, (call, (label, name)) in enumerate(zip(calls, READONLY_CALLS), 1):
        require(set(call) == {
            "ordinal", "host", "command", "args", "session", "request_id",
            "descriptor", "descriptor_digest", "state", "response",
            "response_value_sha256", "response_value_bytes", "response_value_utf8"}
            and type(call["ordinal"]) is int and call["ordinal"] == index
            and (call["host"], call["command"]) == (label, name)
            and call["session"] == "motionwright-sdk-readonly-" + label
            and type(call["request_id"]) is str and call["request_id"]
            and call["state"] == "RETURNED", "readonly query identity or terminal differs")
        caps = host_by_label[label]["catalog_before"]["capabilities"]
        selected = [row["descriptor"] for row in caps if row["descriptor"]["name"] == name]
        require(len(selected) == 1, "readonly descriptor is not in the original catalog")
        readonly_same_json(call["descriptor"], selected[0], "executed original descriptor")
        require(call["descriptor"]["risk"] == "read_only"
                and call["descriptor"]["idempotency"] == "read_only"
                and re.fullmatch(r"[0-9a-f]{64}", call["descriptor_digest"]) is not None,
                "query is not a digest-bound readonly descriptor")
        require(type(readonly_serialized(call, "response", "response_value")) is dict,
                "readonly response Value is missing")
    node = [row for row in calls[3]["response"]["items"] if row["id"] == "polygon"]
    scene = [row for row in calls[8]["response"]["items"] if row["id"] == "cue-scene"]
    require(len(node) == len(scene) == 1, "readonly original selection is ambiguous")
    args = [{}, {}, {"kind": "polygon"}, {},
            {"node_ref": node[0]["reference"], "property": "sides"},
            {"profile": {"first_frame": 0, "end_frame_exclusive": 2, "scale": "full",
                         "transparent": False, "timeout_ms": 10000}},
            {}, {}, {}, {"scene_ref": scene[0]["reference"]}, {}]
    for call, wanted in zip(calls, args):
        readonly_same_json(call["args"], wanted, "executed readonly arguments")

    expected_events = [("fixture_preflight_complete", {
        "state": "SOURCE_INPUTS_PINNED", "sdk_source_expected": SDK_SHA,
        "complete_checkout_binding": "REQUIRED_FROM_CALLER",
        "original_inputs": report["inputs"], "observed_files": report["observed_files"]})]
    for host in hosts:
        label = host["label"]
        expected_events.extend([
            ("connect_intent", {"host": label, "state": "UNKNOWN_UNTIL_OUTCOME",
                                "manifest": host["manifest"], "grants": host["grants"]}),
            ("connect_outcome", {"host": label, "state": "CONNECTED",
                                 "identity": host["identity_before"]}),
            ("catalog_intent", {"host": label, "state": "UNKNOWN_UNTIL_OUTCOME"}),
            ("catalog_outcome", {"host": label, "state": "RETURNED", "count": 33,
                                 "original_sdk_digest": READONLY_CATALOG_DIGEST}),
        ])
        for call in [row for row in calls if row["host"] == label]:
            intent = {key: value for key, value in call.items()
                      if key != "response" and not key.startswith("response_value_")}
            intent["state"] = "UNKNOWN_UNTIL_OUTCOME"
            expected_events.extend([("execute_intent", intent), ("execute_outcome", call)])
        expected_events.extend([
            ("catalog_intent", {"host": label, "state": "UNKNOWN_UNTIL_OUTCOME"}),
            ("catalog_outcome", {"host": label, "state": "RETURNED", "count": 33,
                                 "original_sdk_digest": READONLY_CATALOG_DIGEST}),
            ("shutdown_intent", {"host": label, "state": "UNKNOWN_UNTIL_OUTCOME",
                                 "cleanup_after_error": False}),
            ("shutdown_outcome", {"host": label, "state": "COMPLETED"}),
        ])
    events = report["events"]
    require(type(events) is list and len(events) == len(expected_events) == 39,
            "readonly attempted/completed event sequence is incomplete")
    require(all(type(report[key]) is int and report[key] >= 0 for key in (
        "started_unix_ms", "ended_unix_ms", "elapsed_millis"))
        and report["ended_unix_ms"] >= report["started_unix_ms"],
        "readonly observation timing is invalid")
    elapsed = 0
    for event, (kind, data) in zip(events, expected_events):
        require(set(event) == {"kind", "unix_ms", "elapsed_millis", "data"}
                and event["kind"] == kind
                and type(event["unix_ms"]) is int
                and type(event["elapsed_millis"]) is int
                and elapsed <= event["elapsed_millis"] <= report["elapsed_millis"],
                "readonly event order or timing differs")
        readonly_same_json(event["data"], data, "readonly intent/outcome data")
        elapsed = event["elapsed_millis"]
    require(type(original_stdout) is bytes, "original libtest stdout is required")
    lines = original_stdout.decode("utf-8").splitlines()
    captured = [readonly_json(line[len("MW_SDK_READONLY_EVENT "):])
                for line in lines if line.startswith("MW_SDK_READONLY_EVENT ")]
    readonly_same_json(captured, events, "original captured lifecycle events")
    terminals = [readonly_json(line[len("MW_SDK_READONLY_TERMINAL "):])
                 for line in lines if line.startswith("MW_SDK_READONLY_TERMINAL ")]
    require(len(terminals) == 1, "original readonly terminal is missing or duplicated")
    terminal = terminals[0]
    require(set(terminal) == {
        "state", "observation", "readonly_fixture_complete", "native_render_calls",
        "node_tool_calls", "formal_acceptance"}, "readonly captured terminal shape differs")
    readonly_same_json({key: terminal[key] for key in terminal if key != "observation"}, {
        "state": "PASSED", "readonly_fixture_complete": True, "native_render_calls": 0,
        "node_tool_calls": 0, "formal_acceptance": False}, "captured terminal scope")
    readonly_file_join(terminal["observation"], report_record)
    return {
        "state": "READONLY_HOST_FIXTURE_COMPLETE", "provider_lifecycles": 2,
        "execute_calls": 11, "selected_libtest_methods": 1, "watched_input_files": 7,
        "captured_intent_outcome_events": 39, "original_serialized_values_retained": True,
        "raw_json_rpc_wire_bytes": False, "physical_native_render": False,
        "semantic_oracles": "Exact compiled Rust fixture assertions; caller also requires exit0",
        "human_acceptance": False, "formal_criteria_closed": 0, "release_ready": False,
    }


def validate_readonly_observation(report, sdk_root, output, private_work,
                                  expected_files, original_stdout, report_record):
    try:
        return _validate_readonly_observation(
            report, sdk_root, output, private_work, expected_files,
            original_stdout, report_record)
    except (KeyError, TypeError, AttributeError, UnicodeError, ValueError) as exc:
        if isinstance(exc, Refused):
            raise
        raise Refused("malformed readonly observation: " + str(exc)) from exc


def execute_readonly_host_fixture(args, runner, rust_tools, executables, build):
    source = file_record(args.motionwright / READONLY_PATH, SOURCE_FILE_CAP)
    target = Path(runner.env["CARGO_TARGET_DIR"])
    links = readonly_link_inputs(
        runner.bytes(build["test_harness_preparation"]), target, args.sdk,
        Path(runner.env["CARGO_HOME"]),
    )
    dependencies = readonly_dependency_snapshot(target)
    original_driver = executables[("semwright-motion-canvas-driver", False)]
    original_sandbox = executables[("semwright-sandbox", False)]
    build_inputs = {
        "kind": "new_a6_readonly_host_fixture_preparation",
        "verifier_source_sha": args.source_sha, "sdk_source_sha": SDK_SHA,
        "fixture_source": source, "link_inputs": links,
        "dependency_search_files": dependencies,
        "original_driver": original_driver, "original_sandbox": original_sandbox,
        "prepared_test_cases_executed": 0,
    }
    write_json_new(runner.root / "readonly-fixture-build-inputs.json", build_inputs)
    formatted = runner.command(
        "fmt-readonly-fixture",
        [rust_tools["rustfmt"]["path"], "--edition", "2024", "--check", source["path"]],
        args.work, 60,
    )
    binary_path = args.work / "readonly-host-fixture"
    require(not binary_path.exists(), "readonly executable path already exists")
    argv = [
        rust_tools["rustc"]["path"], "--edition=2024", "--test",
        "--crate-name", "motion_canvas_host_readonly", source["path"],
        "-C", "debuginfo=0", "-L", "dependency=" + str(target / "debug" / "deps"),
        "-L", "native=" + links["native"]["directory"],
    ]
    for name, record in sorted(links["externs"].items()):
        argv.extend(("--extern", name + "=" + record["artifact"]["path"]))
    argv.extend(("-o", str(binary_path)))
    record_binaries(executables)
    check_readonly_link_seals(links, dependencies)
    compiled = runner.command("compile-readonly-fixture", argv, args.work, 180)
    record_binaries(executables)
    check_readonly_link_seals(links, dependencies)
    same_file(source, file_record(args.motionwright / READONLY_PATH),
              "readonly fixture source")
    binary = file_record(binary_path)
    build_result = {
        **build_inputs, "format_command": formatted, "compile_command": compiled,
        "compiled_executable": binary, "original_and_link_inputs_unchanged": True,
        "compiled_executable_bytes_retained": False,
        "binary_hash_is_observed_not_independently_authenticated": True,
    }
    write_json_new(runner.root / "readonly-fixture-build.json", build_result)
    output = runner.root / "readonly-host"
    output.mkdir()
    private_work = args.work / "readonly-host.private-work"
    assert_outside_roots(private_work, (args.output, args.motionwright, args.sdk))
    require(not private_work.exists(), "readonly private work path already exists")
    expected_files = {
        "sdk_lock": file_record(args.sdk / "Cargo.lock"),
        "driver": original_driver, "sandbox": original_sandbox,
        "new_fixture_source": source, "new_fixture_harness": binary,
        **{pin["label"]: file_record(args.sdk / pin["path"]) for pin in READONLY_INPUTS},
    }
    environment = dict(runner.env)
    environment.update(
        MW_SDK_READONLY_SOURCE=str(args.sdk),
        MW_SDK_READONLY_DRIVER=original_driver["path"],
        MW_SDK_READONLY_SANDBOX=original_sandbox["path"],
        MW_SDK_READONLY_OUTPUT=str(output),
        MW_SDK_READONLY_WORK=str(private_work),
    )
    record_binaries(executables)
    same_file(binary, file_record(binary_path), "readonly executable")
    executed = runner.command(
        "readonly-host-single",
        [str(binary_path), READONLY_CASE_ID, "--exact", "--nocapture"],
        args.work, 180, env=environment,
    )
    parsed = parse_rust_result(runner.bytes(executed), (READONLY_CASE_ID,), host=True)
    report_record, report = read_json_record(
        output / "readonly-host-observation.json", 64 * 1024 * 1024)
    checked = validate_readonly_observation(
        report, args.sdk, output, private_work, expected_files,
        runner.bytes(executed), report_record)
    record_binaries(executables)
    check_readonly_link_seals(links, dependencies)
    same_file(binary, file_record(binary_path), "readonly executable")
    same_file(source, file_record(args.motionwright / READONLY_PATH),
              "readonly fixture source")
    same_file(report_record, file_record(Path(report_record["path"])),
              "readonly fixture observation")
    observation = {
        "kind": "new_a6_real_sdk_host_readonly_fixture", "sdk_source_sha": SDK_SHA,
        "verifier_source_sha": args.source_sha, "build": build_result,
        "command": executed, "result": parsed, "original_observation": report_record,
        "validated_observation": checked,
        "required_environment": {
            key: environment[key] for key in (
                "MW_SDK_READONLY_SOURCE", "MW_SDK_READONLY_DRIVER",
                "MW_SDK_READONLY_SANDBOX", "MW_SDK_READONLY_OUTPUT",
                "MW_SDK_READONLY_WORK")},
        "original_sdk8_and_host2_are_separate_results": True,
        "broker": False, "node": False, "physical_native_render": False,
        "native_four_phase": False, "human_acceptance": False,
        "formal_criteria_closed": 0, "release_ready": False,
    }
    write_json_new(runner.root / "readonly-host-single.json", observation)
    return observation


def assert_outside_roots(path, roots):
    require(path.is_absolute(), "owned resource path must be absolute")
    for root in roots:
        require(path != root and not path.is_relative_to(root)
                and not root.is_relative_to(path),
                "owned resources and source trees must be disjoint")


def phase_g0(args, runner, python, git_exe, observations):
    before = source_snapshot(runner, git_exe, args.motionwright,
                             args.source_sha, "mw-before")
    observations["motionwright_before"] = before
    same_file(file_record(args.motionwright / SELF_PATH),
              file_record(Path(__file__).absolute()), "executed verifier")
    verify_source_lock(args.motionwright)
    test_file = test_catalog(args.motionwright / TEST_PATH)
    observations["test_source"] = test_file
    observations["readonly_fixture_source"] = file_record(
        args.motionwright / READONLY_PATH, SOURCE_FILE_CAP)
    observations["source_lock"] = file_record(args.motionwright / "SOURCE_LOCK.json")
    observations["g0_checks"] = []
    for name, relative in (("source-policy", "tooling/source_policy.py"),
                           ("acceptance-policy", "tooling/acceptance_policy.py"),
                           ("creative-status-policy", "tooling/creative_status_policy.py")):
        # These original scripts resolve their own first-party tooling imports.
        # The entire clean source was measured before allowing that import path.
        result = runner.command(
            name, [str(python), "-B", "-E", "-s",
                   str(args.motionwright / relative)],
            args.motionwright, 120,
        )
        observations["g0_checks"].append(
            {"name": name, "command": result, "exit_code": result["exit_code"]})
    synthetic = []
    for mode, module_name, tail in (
        ("discovery", "test_motion_canvas_sdk_g1",
         ["discover", "-s", "tooling/tests",
          "-p", "test_motion_canvas_sdk_g1.py", "-v"]),
        ("dotted", "tooling.tests.test_motion_canvas_sdk_g1",
         ["-v", "tooling.tests.test_motion_canvas_sdk_g1"]),
    ):
        result = runner.command(
            "synthetic-" + mode,
            [str(python), "-B", "-E", "-s", "-m", "unittest", *tail],
            args.motionwright, 180,
        )
        parsed = parse_unittest_result(runner.bytes(result, "stderr"), module_name)
        synthetic.append({"mode": mode, "command": result, "result": parsed})
        write_json_new(runner.root / ("synthetic-" + mode + ".json"), synthetic[-1])
    observations["synthetic_checks"] = synthetic
    after = source_snapshot(runner, git_exe, args.motionwright,
                            args.source_sha, "mw-after")
    require_same_snapshot(before, after)
    same_file(test_file, file_record(args.motionwright / TEST_PATH),
              "synthetic test source")
    observations["motionwright_after"] = after
    observations["source_unchanged"] = True
    return "G0_SOURCE_AND_SYNTHETIC_COMPLETE"


def phase_g1(args, runner, python, git_exe, observations):
    require(args.sdk is not None, "the complete original SDK checkout is required")
    prior_record, prior = read_json_record(args.output / "g0" / "terminal.json",
                                          PHASE_CAP)
    require(prior.get("status") == "G0_SOURCE_AND_SYNTHETIC_COMPLETE"
            and prior.get("context") == runner.context
            and prior.get("source_sha") == args.source_sha
            and prior.get("source_unchanged") is True
            and prior.get("physical_native_render") is False,
            "G0 for this exact workflow source and attempt is required")
    require(type(prior.get("exit_code")) is int and prior["exit_code"] == 0,
            "G0 did not complete successfully")
    prior_observations = prior.get("observations", {})
    observations["same_run_g0"] = prior_record
    mw_before = source_snapshot(runner, git_exe, args.motionwright,
                                args.source_sha, "mw-before")
    same_file(file_record(args.motionwright / SELF_PATH),
              file_record(Path(__file__).absolute()), "executed verifier")
    require_same_snapshot(prior_observations["motionwright_after"], mw_before)
    sdk_before = source_snapshot(runner, git_exe, args.sdk,
                                 SDK_SHA, "sdk-before", SDK_TREE)
    verify_source_lock(args.motionwright, args.sdk)
    observations["motionwright_before"] = mw_before
    observations["sdk_before"] = sdk_before

    rustup = find_tool("rustup")
    observations["rustup"] = file_record(rustup)
    rust_tools = {}
    for name in ("cargo", "rustc", "rustfmt"):
        result = runner.command(
            "which-" + name,
            [str(rustup), "which", "--toolchain", RUST_VERSION, name],
            args.work, 30,
        )
        value = runner.bytes(result).decode("utf-8", "strict").strip()
        require(value and "\n" not in value and "\r" not in value,
                "rustup returned an ambiguous executable path")
        path = plain(Path(value))
        require(path.is_relative_to(Path(runner.env["RUSTUP_HOME"])),
                "resolved Rust tool is outside this job's isolated toolchain")
        rust_tools[name] = file_record(path)
    tool_dir = str(Path(rust_tools["rustc"]["path"]).parent)
    runner.env.update(RUSTC=rust_tools["rustc"]["path"],
                      RUSTFMT=rust_tools["rustfmt"]["path"],
                      PATH=tool_dir + os.pathsep + runner.env["PATH"])
    for name in ("cargo", "rustc", "rustfmt"):
        result = runner.command(
            "version-" + name, [rust_tools[name]["path"], "--version"],
            args.work, 30,
        )
        original = runner.bytes(result).decode("utf-8", "strict").strip()
        require(original.startswith(name + " "), "wrong executable version output")
        if name in ("cargo", "rustc"):
            require(original.split()[1] == RUST_VERSION, "wrong Rust toolchain")
        observations[name + "_version"] = {
            "file": rust_tools[name], "original_stdout": result["logs"]["stdout"],
            "observed_version": original,
            "independent_binary_authentication": False,
        }
    cargo = rust_tools["cargo"]["path"]

    # Formatting and the original scoped policy setup precede compilation.
    observations["format_checks"] = []
    for label, source in (("motionwright", args.motionwright), ("sdk", args.sdk)):
        result = runner.command(
            "fmt-" + label, [cargo, "fmt", "--all", "--", "--check"], source, 120)
        observations["format_checks"].append(result)
    policy_before = file_record(args.sdk / POLICY_PATH)
    require(policy_before["sha256"] == POLICY_SHA, "original sandbox policy changed")
    profile_path = runner.root / "sandbox-profile.original.txt"
    bash = find_tool("bash")
    policy_result = runner.command(
        "original-scoped-sandbox-policy",
        [str(bash), str(args.sdk / POLICY_PATH), str(profile_path)],
        args.sdk, 300,
    )
    same_file(policy_before, file_record(args.sdk / POLICY_PATH),
              "original sandbox policy script")
    profile = file_record(profile_path, 65536)
    profile_text = read_recorded(profile, 65536).decode("utf-8", "strict")
    profile_rows = profile_text.splitlines()
    require(len(profile_rows) == len(set(profile_rows)) == 5
            and "sandbox_setup=scoped-apparmor-driver-chain" in profile_rows
            and "ci_provider=github-hosted-certification" in profile_rows
            and "certification_eligible=true" in profile_rows,
            "original scoped sandbox setup receipt is incomplete")
    observed_kernel = dict(line.split("=", 1) for line in profile_rows)
    require(observed_kernel.get("kernel.apparmor_restrict_unprivileged_userns")
            not in (None, "", "0"), "restricted AppArmor policy was not retained")
    observations["sandbox_setup"] = {
        "original_script": policy_before, "command": policy_result,
        "original_receipt": profile,
        "script_certification_label_is_not_formal_acceptance": True,
    }
    sandbox_files = {
        name: file_record(find_tool(name))
        for name in ("bwrap", "setpriv", "sh", "true", "apparmor_parser", "python3")
    }
    sandbox_files["installed_profile"] = file_record(
        Path("/etc/apparmor.d/bwrap-userns-restrict"), 1024 * 1024)
    observations["sandbox_runtime_files_observed"] = sandbox_files
    all_executables, observations["build"] = prepare_original_executables(
        runner, cargo, args.sdk,
    )
    write_json_new(runner.root / "build-executables.json", observations["build"])
    observations["selected_sdk_results"] = []
    for index, case_id in enumerate(UNIT_IDS):
        result = execute_original_harness(
            runner, f"rust-library-{index + 1:02d}", all_executables,
            ("semwright_driver_motion_canvas", True),
            [case_id, "--exact", "--nocapture"], args.sdk,
        )
        parsed = parse_rust_result(runner.bytes(result), (case_id,))
        observation = {"kind": "original_sdk_rust_unit", "sdk_source_sha": SDK_SHA,
                       "command": result, "result": parsed}
        observations["selected_sdk_results"].append(observation)
        write_json_new(runner.root / f"rust-library-{index + 1:02d}.json", observation)
    helper = Path(all_executables[("semwright-sandbox", False)]["path"])
    host_env = dict(runner.env)
    host_env.update(SEMWRIGHT_TEST_MOTION_CANVAS="1",
                    SEMWRIGHT_TEST_SANDBOX_HELPER=str(helper))
    validate_host_environment(host_env, helper)
    host = execute_original_harness(
        runner, "original-driver-host-two", all_executables,
        ("host_conformance", True), ["--ignored", "--nocapture"],
        args.sdk, env=host_env,
    )
    parsed = parse_rust_result(runner.bytes(host), HOST_IDS, host=True)
    observations["host_result"] = {
        "kind": "original_real_driver_host_no_render",
        "sdk_source_sha": SDK_SHA, "command": host, "result": parsed,
        "required_environment": {
            "SEMWRIGHT_TEST_MOTION_CANVAS": "1",
            "SEMWRIGHT_TEST_SANDBOX_HELPER": str(helper)},
        "temporary_project_payloads_retained": False,
        "scope": "Original Host assertions and logs; no media production",
    }
    write_json_new(runner.root / "original-driver-host-two.json",
                   observations["host_result"])
    observations["readonly_host_fixture"] = execute_readonly_host_fixture(
        args, runner, rust_tools, all_executables, observations["build"])
    mw_after = source_snapshot(runner, git_exe, args.motionwright,
                               args.source_sha, "mw-after")
    sdk_after = source_snapshot(runner, git_exe, args.sdk,
                                SDK_SHA, "sdk-after", SDK_TREE)
    require_same_snapshot(mw_before, mw_after)
    require_same_snapshot(sdk_before, sdk_after)
    verify_source_lock(args.motionwright, args.sdk)
    for name, record in rust_tools.items():
        same_file(record, file_record(Path(record["path"])), "Rust tool " + name)
    same_file(observations["rustup"], file_record(rustup), "rustup executable")
    for name, record in sandbox_files.items():
        same_file(record, file_record(Path(record["path"])),
                  "observed sandbox runtime file " + name)
    same_file(prior_record, file_record(args.output / "g0" / "terminal.json",
                                       PHASE_CAP), "same-run G0 receipt")
    observations["motionwright_after"] = mw_after
    observations["sdk_after"] = sdk_after
    observations["source_unchanged"] = True
    observations["selected_counts"] = {
        "rust_library_cases": 8, "original_host_cases": 2, "failed": 0,
        "new_readonly_host_cases": 1, "readonly_provider_connections": 2,
        "readonly_user_queries": 11,
        "ignored": 0, "measured": 0, "host_filtered": 0,
        "nonselected_library_filtered_counts": [
            row["result"]["counts"]["filtered"]
            for row in observations["selected_sdk_results"]],
        "previous_node_cases_executed_again": 0,
    }
    return "SCOPED_SDK_G1_COMPLETE"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("g0", "g1"), required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--motionwright", type=Path, required=True)
    parser.add_argument("--sdk", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.motionwright = plain(args.motionwright, directory=True)
    args.work = plain(args.work, directory=True)
    args.output = plain(args.output, directory=True)
    if args.sdk is not None:
        args.sdk = plain(args.sdk, directory=True)
    roots = [args.motionwright] + ([] if args.sdk is None else [args.sdk])
    for path in (args.work, args.output):
        assert_outside_roots(path, roots)
    if args.sdk is not None:
        assert_outside_roots(args.sdk, [args.motionwright])
    folder = args.output / args.phase
    folder.mkdir()
    observed_context = {key: os.environ.get(key, "") for key in CONTEXT_KEYS}
    start = {
        "schema": "motionwright.sdk-g1.phase-start/1", "timestamp": utc(),
        "phase": args.phase, "state": "UNKNOWN_UNTIL_TERMINAL",
        "source_sha_requested": args.source_sha,
        "sdk_source_sha_fixed": SDK_SHA, "observed_context": observed_context,
        "processes_attempted_at_start": 0,
        "physical_native_render": False, "human_acceptance": False,
    }
    write_json_new(folder / "start.json", start)
    observations = {}
    runner = None
    status = "FAIL"
    error = None
    exit_code = 1
    began = time.monotonic()
    original_sigterm = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, _frame):
        raise Incomplete("verification interrupted by signal " + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    try:
        context = validate_context(os.environ, args.source_sha)
        observed_context = context
        event_record, event = read_json_record(
            Path(os.environ.get("GITHUB_EVENT_PATH", "")), 2 * 1024 * 1024)
        require(event.get("inputs", {}).get("scope") == "mc_driver_contracts"
                and event.get("repository", {}).get("full_name")
                    == "seradotcom/motionwright",
                "original manual event did not select SDK contracts")
        observations["event_selection"] = {
            "original_file": event_record, "scope": "mc_driver_contracts",
            "full_event_payload_retained": False,
            "execution_context_not_independent_API_authentication": True,
        }
        env = safe_environment(os.environ)
        for key in ("CARGO_HOME", "CARGO_TARGET_DIR", "RUSTUP_HOME",
                    "TMPDIR", "TMP", "TEMP"):
            resource = plain(Path(env[key]), directory=True)
            assert_outside_roots(resource, roots)
        runner = CommandRunner(folder, context, env)
        python = plain(Path(sys.executable).resolve(strict=True))
        git_exe = find_tool("git")
        observations["python"] = file_record(python)
        observations["git"] = file_record(git_exe)
        require(sys.version_info[:2] == (3, 13), "this lane requires Python 3.13")
        observations["python_version"] = sys.version
        if args.phase == "g0":
            status = phase_g0(args, runner, python, git_exe, observations)
        else:
            status = phase_g1(args, runner, python, git_exe, observations)
        same_file(observations["python"], file_record(python), "Python executable")
        same_file(observations["git"], file_record(git_exe), "Git executable")
        exit_code = 0
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        uncertain = isinstance(exc, (Incomplete, KeyboardInterrupt, SystemExit))
        status = "UNKNOWN" if uncertain or (runner and runner.uncertain) else "FAIL"
        exit_code = 1
    finally:
        signal.signal(signal.SIGTERM, original_sigterm)
        terminal = {
            "schema": "motionwright.sdk-g1.phase-terminal/1",
            "timestamp": utc(), "phase": args.phase, "status": status,
            "exit_code": exit_code, "error": error, "source_sha": args.source_sha,
            "sdk_source_sha": SDK_SHA, "sdk_source_tree": SDK_TREE,
            "context": observed_context, "observations": observations,
            "source_unchanged": observations.get("source_unchanged") is True,
            "command_count": 0 if runner is None else len(runner.commands),
            "child_launches": 0 if runner is None else runner.launches,
            "duration_seconds": round(time.monotonic() - began, 6),
            "missing_completion_or_capture_is_not_success": True,
            "sdk_g1_complete": status == "SCOPED_SDK_G1_COMPLETE",
            "physical_native_render": False, "native_four_phase": False,
            "motionwright_product_execution": False, "installed_app": False,
            "human_acceptance": False, "formal_criteria_closed": 0,
            "release_ready": False,
            "binary_hashes_are_observations_not_independent_authentication": True,
            "source_provenance_custody_requires_external_originals_review": True,
        }
        try:
            record = write_json_new(folder / "terminal.json", terminal)
            print(json.dumps({"status": status, "exit_code": exit_code,
                              "terminal": record}, sort_keys=True))
        except (OSError, ValueError) as exc:
            print("terminal journal incomplete: " + type(exc).__name__,
                  file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
