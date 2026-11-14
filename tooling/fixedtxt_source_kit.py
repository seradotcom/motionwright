#!/usr/bin/env python3
"""Prepare one real managed Project in the existing manual source-contract job.

This is a partial source input artifact, never P/R/Q, R28 or Native admission.
The existing command-custody helper owns child observation and no-retry rules.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import tomllib
import zipfile

import ci_storage_apply_recovery as custody

ROOT = Path(__file__).resolve().parents[1]
MARKER = "managed-project-source-kit"
SDK = "6a28fbfe023faf260f432dee1b40bb803dd85691"
SDK_SOURCE = "git+https://github.com/seradotcom/semwright?rev=" + SDK + "#" + SDK
FACTORY = "tooling/fixedtxt/prepare_fixedtxt_managed_project.rs"
FACTORY_SHA = "18ff099a773471a059d0ac7a6b97efdee0d27e29eb9cc29a11853bed6051b3ad"
TEMPLATE_SHA = "5b1f9e7befb11c3ef6354a9b3b292d105647f444cac4a13d6eb8d27bf7c10b97"
WORKFLOW = ".github/workflows/native-mlt-sequence-e2e.yml"
SOURCE_PATHS = (WORKFLOW, FACTORY, "tooling/fixedtxt_source_kit.py",
                "tooling/ci_storage_apply_recovery.py", "tooling/source_policy.py",
                "Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml",
                "apps/studio/package-lock.json")
EXTERNS = ("semwright_driver_motion_canvas", "semwright_motion_authoring",
           "semwright_semantic_composition", "serde_json", "sha2", "hex")
MAX_LOG = 16 * 1024 * 1024


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict(raw):
    def pairs(items):
        result = {}
        for name, value in items:
            need(name not in result, "duplicate JSON member")
            result[name] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def write_new(path, raw):
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def json_new(path, value):
    write_new(path, (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())


def original(path, root, limit):
    before = custody.fingerprint(path, root, limit)
    raw = path.read_bytes()
    need(len(raw) == before["bytes"] and sha(raw) == before["sha256"]
         and custody.fingerprint(path, root, limit) == before, "original changed during read")
    return raw


def context():
    env = os.environ
    expected = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "seradotcom/motionwright",
                "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_JOB": "source-contracts", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
                "MW_FIXEDTXT_SOURCE_SCOPE": "source", "MW_FIXEDTXT_SOURCE_MODE": "prepare",
                "MW_FIXEDTXT_SOURCE_REQUEST": MARKER, "MW_FIXEDTXT_SOURCE_G2": "",
                "MW_FIXEDTXT_SOURCE_DELIVERY": ""}
    need(sys.platform == "linux" and all(env.get(k) == v for k, v in expected.items()),
         "requires the exact manual source-only selection; no Native authority")
    head = env.get("GITHUB_SHA", "")
    need(re.fullmatch(r"[0-9a-f]{40}", head)
         and env.get("EXPECTED_SOURCE_SHA") == head == env.get("GITHUB_WORKFLOW_SHA"),
         "exact source and workflow SHA required")
    run = env.get("GITHUB_RUN_ID", "")
    need(re.fullmatch(r"[1-9][0-9]*", run), "real run ID required")
    need(env.get("GITHUB_WORKFLOW_REF", "").startswith(
        "seradotcom/motionwright/" + WORKFLOW + "@"), "wrong workflow")
    return {"source_sha": head, "semwright_sha": SDK, "run_id": run, "run_attempt": "1"}


def layout(current):
    temp = Path(os.environ["RUNNER_TEMP"])
    need(temp.is_absolute() and temp.is_dir() and temp.resolve(strict=True) == temp
         and str(temp).startswith("/home/runner/work/_temp")
         and all(not p.is_symlink() for p in (temp, *temp.parents)), "canonical runner temp required")
    owner = temp / ("mw-fixedtxt-source-kit-" + current["run_id"] + "-1")
    return owner, owner / "originals"


def selected_pins():
    lock = strict(original(ROOT / "SOURCE_LOCK.json", ROOT, 2 * 1024 * 1024))
    row = lock["dependencies"]["semwright"]
    need(row["revision"] == SDK and row["repository"] == "https://github.com/seradotcom/semwright",
         "this reviewed factory requires the exact selected SDK")
    evidence = row["verification"]["evidence"]
    relative = evidence["path"]
    need(type(relative) is str and not Path(relative).is_absolute() and ".." not in Path(relative).parts,
         "SDK evidence must be a relative committed input")
    need(row["verification"]["status"] == "verified" and row["verification"]["revision"] == SDK
         and sha(original(ROOT / relative, ROOT, 2 * 1024 * 1024)) == evidence["sha256"],
         "selected SDK source evidence differs")
    cargo = tomllib.loads(original(ROOT / "Cargo.toml", ROOT, 2 * 1024 * 1024).decode())
    pins = [v for v in cargo["workspace"]["dependencies"].values()
            if isinstance(v, dict) and "semwright" in v.get("git", "")]
    # Match the exact reviewed desktop overrides admitted by source_policy.
    # No SDK source override or Cargo replacement is permitted.
    need(pins and all(v.get("git") == row["repository"] and v.get("rev") == SDK for v in pins)
         and cargo.get("patch") == {"crates-io": {
             "glib": {"path": "vendor/glib-0.18.5"},
             "tauri": {"path": "vendor/tauri-2.12.1"},
         }} and "replace" not in cargo, "SDK direct pins or overrides changed")
    packages = tomllib.loads(original(ROOT / "Cargo.lock", ROOT, 2 * 1024 * 1024).decode())["package"]
    locked = [p for p in packages if "semwright" in p.get("source", "")]
    need(locked and all(p["source"] == SDK_SOURCE for p in locked)
         and sum(p["name"] == "semwright-driver-motion-canvas" for p in locked) == 1,
         "SDK locked packages differ")
    return relative


def source_matches(snapshot):
    need(custody.git(ROOT, "rev-parse", "HEAD").decode().strip() == snapshot["sha"], "source HEAD changed")
    need(not custody.git(ROOT, "status", "--porcelain=v1", "--untracked-files=no"), "tracked source changed")
    for name, row in snapshot["files"].items():
        raw = original(ROOT / name, ROOT, 16 * 1024 * 1024)
        need(len(raw) == row["bytes"] and sha(raw) == row["sha256"]
             and custody.git(ROOT, "show", snapshot["sha"] + ":" + name) == raw,
             "committed preparation input changed:" + name)


def start():
    current = context()
    evidence_path = selected_pins()
    snapshot = custody.source_snapshot(ROOT, current["source_sha"], (*SOURCE_PATHS, evidence_path))
    need(snapshot["files"][FACTORY]["sha256"] == FACTORY_SHA, "reviewed factory source differs")
    owner, originals = layout(current)
    owner.mkdir(mode=0o700, exist_ok=False)
    originals.mkdir(mode=0o700)
    for name in ("source", "commands", "binary"):
        (originals / name).mkdir(mode=0o700)
    for name in ("target", "tmp"):
        (owner / name).mkdir(mode=0o700)
    for name in snapshot["files"]:
        path = originals / "source" / name
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        write_new(path, original(ROOT / name, ROOT, 16 * 1024 * 1024))
    now = time.monotonic()
    json_new(originals / "STARTED.original.json", {
        "kind": "FIXEDTXT_MANAGED_PROJECT_SOURCE_INPUT_PREPARATION", "context": current,
        "state": "WAITING_FOR_EXISTING_SOURCE_CONTRACTS", "source": snapshot,
        "started_monotonic": now, "deadline_monotonic": now + 28 * 60,
        "existing_job_minutes": 30, "upload_reserve_seconds": 120,
        "factory_invoked": False, "Native_calls": 0, "automatic_retry": False,
        "g2_source_bundle_complete": False, "formal_acceptance": False})


def cargo_libraries(metadata, messages, target):
    """Select actual metadata IDs and emitted rlibs, never a filename glob."""
    packages = {p["id"]: p for p in metadata["packages"]}
    driver = [p for p in packages.values() if p["name"] == "semwright-driver-motion-canvas"
              and p.get("source") == SDK_SOURCE]
    need(len(driver) == 1, "exact driver package missing or ambiguous")
    driver = driver[0]
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    selected = {EXTERNS[0]: driver["id"]}
    for name in EXTERNS[1:]:
        ids = [d["pkg"] for d in nodes[driver["id"]]["deps"]
               if d["name"].replace("-", "_") == name
               and any(k["kind"] is None and k["target"] is None for k in d["dep_kinds"])]
        need(len(ids) == 1, "normal driver dependency missing or ambiguous:" + name)
        selected[name] = ids[0]
    finished = [m for m in messages if m.get("reason") == "build-finished"]
    need(len(finished) == 1 and finished[0].get("success") is True, "build completion missing")
    result = {}
    for name, package_id in selected.items():
        package = packages[package_id]
        if name.startswith("semwright_"):
            need(package.get("source") == SDK_SOURCE, "linked SDK library has another source")
        rows = [m for m in messages if m.get("reason") == "compiler-artifact"
                and m.get("package_id") == package_id and m.get("target", {}).get("name") == name
                and m["target"].get("kind") == ["lib"] and m.get("profile", {}).get("test") is False]
        need(len(rows) == 1, "actual emitted library missing or ambiguous:" + name)
        row = rows[0]
        need(Path(row["manifest_path"]).resolve(strict=True) == Path(package["manifest_path"]).resolve(strict=True),
             "artifact belongs to another package manifest")
        paths = [Path(p) for p in row["filenames"] if p.endswith(".rlib")]
        # Cargo emits the explicitly selected root library directly in debug;
        # transitive libraries remain in debug/deps. Admit no other root.
        expected_parent = target / ("debug" if name == EXTERNS[0] else "debug/deps")
        need(len(paths) == 1 and paths[0].is_absolute()
             and paths[0].parent == expected_parent, "rlib is not in the one fresh target")
        result[name] = {"package_id": package_id, "path": str(paths[0]),
                        "original": custody.fingerprint(paths[0], target, 256 * 1024 * 1024)}
    linked_paths = set()
    for row in messages:
        if row.get("reason") != "build-script-executed":
            continue
        for value in row.get("linked_paths", []):
            kind, separator, raw = value.partition("=")
            path = Path(raw if separator else value)
            need((not separator or kind in {"native", "dependency", "all"})
                 and path.is_absolute() and path.resolve(strict=True) == path and path.is_relative_to(target),
                 "native library search path is outside the fresh build target")
            linked_paths.add(value)
    return driver, result, sorted(linked_paths)


def prepare():
    current = context()
    owner, originals = layout(current)
    started = strict(original(originals / "STARTED.original.json", owner, 4 * 1024 * 1024))
    need(started["context"] == current, "source preparation belongs to another invocation")
    need(not (originals / "PREPARATION_INTENT.original.json").exists(), "preparation cannot be repeated")
    source_matches(started["source"])
    selected_pins()
    json_new(originals / "PREPARATION_INTENT.original.json", {"context": current,
             "factory_calls_maximum": 1, "Native_calls": 0, "automatic_retry": False})
    env = {k: os.environ[k] for k in ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL",
                                    "TZ", "CARGO_HOME", "RUSTUP_HOME") if k in os.environ}
    env.update(CARGO_TARGET_DIR=str(owner / "target"), TMPDIR=str(owner / "tmp"),
               CARGO_INCREMENTAL="0", CARGO_PROFILE_DEV_DEBUG="0", CARGO_PROFILE_TEST_DEBUG="0",
               CARGO_BUILD_JOBS="1", CARGO_TERM_COLOR="never", GIT_TERMINAL_PROMPT="0")
    env = custody.isolated_build_environment(env)
    commands = []
    attempted = False
    report = {"kind": "PARTIAL_FIXEDTXT_SOURCE_KIT", "context": current,
              "state": "NOT_VERIFIED", "factory_attempted": False,
              "g2_source_bundle_complete": False, "Native_calls": 0, "automatic_retry": False,
              "missing_G2_source_roles": ["caption_source", "red_native", "measurement_protocol"],
              "R28_issued": False, "formal_acceptance": False}

    def command(label, argv, seconds):
        need(time.monotonic() + seconds + 20 < started["deadline_monotonic"],
             "existing source-job budget cannot contain this command and upload reserve")
        row = custody.execute(argv, ROOT, env, originals / "commands", label, seconds, current)
        commands.append(row)
        need(not custody.execution_unknown(row) and row.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
             and row.get("owned_group_after_wait") == "ABSENT" and row.get("exit_code") == 0
             and row.get("log_custody") == "VERIFIED_SNAPSHOT", "command failed or UNKNOWN; no replay:" + label)
        need(all(row[s]["bytes"] <= MAX_LOG for s in ("stdout", "stderr")), "original command stream exceeds bound")
        custody.read_original(originals / "commands", row, "stderr")
        return custody.read_original(originals / "commands", row, "stdout")

    try:
        rustc = shutil.which("rustc", path=env.get("PATH"))
        cargo = shutil.which("cargo", path=env.get("PATH"))
        need(rustc and cargo, "actual Rust tools required")
        version = command("rustc-version", [rustc, "-vV"], 30)
        need(version.startswith(b"rustc 1.98.1 "), "reviewed compiler version required")
        command("cargo-version", [cargo, "-V"], 30)
        metadata = strict(command("cargo-metadata", [cargo, "metadata", "--locked", "--format-version", "1"], 90))
        need(Path(metadata["workspace_root"]).resolve(strict=True) == ROOT, "metadata belongs to another workspace")
        raw = command("cargo-library", [cargo, "build", "--locked", "--jobs", "1", "-p",
                      "semwright-driver-motion-canvas", "--lib", "--message-format=json"], 600)
        messages = [strict(line) for line in raw.splitlines() if line.strip()]
        driver, libraries, native_paths = cargo_libraries(metadata, messages, owner / "target")
        sdk_root = Path(driver["manifest_path"]).resolve(strict=True).parent.parents[1]
        need(custody.git(sdk_root, "rev-parse", "HEAD").decode().strip() == SDK
             and not custody.git(sdk_root, "status", "--porcelain=v1", "--untracked-files=no"),
             "actual Cargo SDK checkout changed")
        template_path = sdk_root / "fixtures/composition/motion/technical.json"
        template = original(template_path, sdk_root, 4 * 1024 * 1024)
        need(sha(template) == TEMPLATE_SHA
             and custody.git(sdk_root, "show", SDK + ":fixtures/composition/motion/technical.json") == template,
             "actual SDK template differs from preserved original")
        write_new(originals / "template.input.original.json", template)
        source_matches(started["source"])
        selected_pins()
        binary = originals / "binary/prepare-fixedtxt-managed-project"
        need(not binary.exists(), "compiled helper already exists")
        argv = [rustc, "--edition=2024", "--crate-name", "prepare_fixedtxt_managed_project",
                "--crate-type", "bin", "-C", "debuginfo=0", str(ROOT / FACTORY),
                "-L", "dependency=" + str(owner / "target/debug/deps")]
        for name in EXTERNS:
            argv += ["--extern", name + "=" + libraries[name]["path"]]
        for value in native_paths:
            argv += ["-L", value]
        argv += ["-o", str(binary)]
        json_new(originals / "LINK_INPUTS.original.json", {"sdk_revision": SDK,
                 "cargo_package_id": driver["id"], "libraries": libraries,
                 "build_script_linked_paths": native_paths, "factory_source_sha256": FACTORY_SHA,
                 "sdk_manifest": custody.fingerprint(Path(driver["manifest_path"]), sdk_root),
                 "sdk_lock": custody.fingerprint(sdk_root / "Cargo.lock", sdk_root)})
        command("rustc-factory", argv, 120)
        compiled = custody.fingerprint(binary, owner, 64 * 1024 * 1024)
        attempted = True
        command("factory-once", [str(binary), str(originals / "template.input.original.json"),
                                  str(originals / "base")], 30)
        receipt = strict(original(originals / "base/PREPARATION_RECEIPT.json", owner, 4 * 1024 * 1024))
        need(receipt["status"] == "GENERATED_BY_REAL_AUTHORING_API_NOT_NATIVE_EVIDENCE"
             and receipt["factory_calls"] == 1 and receipt["binding_revalidated"] is True
             and receipt["native_calls"] == receipt["renders"] == 0
             and receipt["expected_sdk_revision"] == SDK
             and receipt["linked_sdk_revision_verified_by_this_program"] is False,
             "original factory receipt is incomplete")
        available = {}
        for name in ("template_film.original.json", "managed_project.original.json", "realization.original.json"):
            path = originals / "base" / name
            value = custody.fingerprint(path, originals, 4 * 1024 * 1024)
            rows = [r for r in receipt["originals"] if r["member"] == name]
            need(len(rows) == 1 and (rows[0]["bytes"], rows[0]["sha256"]) == (value["bytes"], value["sha256"]),
                 "factory original does not match its receipt")
            available[name] = {"member": value["path"], "bytes": value["bytes"], "sha256": value["sha256"]}
        project = strict(original(originals / "base/managed_project.original.json", owner, 4 * 1024 * 1024))
        need(project["id"] == receipt["project_id"] and project["generation"] == receipt["generation"]
             and project["revision"] == receipt["revision"] == 1 and project["assets"] == []
             and receipt["project_files"] == [] and project.get("authoring") is not None,
             "actual managed Project identity or assets differ")
        need(custody.fingerprint(binary, owner, 64 * 1024 * 1024) == compiled, "factory binary changed")
        source_matches(started["source"])
        selected_pins()
        need(custody.git(sdk_root, "rev-parse", "HEAD").decode().strip() == SDK
             and not custody.git(sdk_root, "status", "--porcelain=v1", "--untracked-files=no"),
             "SDK checkout changed after preparation")
        report.update(state="MANAGED_PROJECT_PREPARED_FULL_G2_KIT_STILL_PARTIAL",
                      actual_factory_calls=1, project_files=[], available_source_members=available,
                      binary=compiled, source_and_locks_rechecked=True)
    except BaseException as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        if isinstance(error, custody.UnknownObservation):
            report["unretained_command_observation"] = error.command
        raise
    finally:
        report.update(factory_attempted=attempted, command_count=len(commands),
                      process_scope="existing observed POSIX group; escaped descendants NOT_OBSERVED")
        json_new(originals / "SOURCE_KIT_RECEIPT.json", report)


def record_upload():
    current = context()
    owner, originals = layout(current)
    artifact_id = os.environ.get("MW_FIXEDTXT_SOURCE_ARTIFACT_ID", "")
    digest = os.environ.get("MW_FIXEDTXT_SOURCE_ARTIFACT_DIGEST", "")
    need(re.fullmatch(r"[1-9][0-9]*", artifact_id) and re.fullmatch(r"[0-9a-f]{64}", digest),
         "actual upload-artifact outputs required")
    receipt_path = originals / "SOURCE_KIT_RECEIPT.json"
    receipt = strict(original(receipt_path, owner, 4 * 1024 * 1024)) if receipt_path.exists() else None
    json_new(owner / "UPLOAD_BINDING.original.json", {
        "kind": "ACTUAL_SOURCE_KIT_UPLOAD_ACTION_OUTPUTS", "context": current,
        "artifact": {"artifact_id": int(artifact_id), "run_id": int(current["run_id"]),
                     "run_attempt": 1, "source_sha": current["source_sha"], "zip_sha256": digest},
        "artifact_name": "motionwright-fixedtxt-source-kit-" + current["source_sha"] + "-" + current["run_id"] + "-1",
        "preparation_state": receipt["state"] if receipt else "SOURCE_PREPARATION_NOT_COMPLETED",
        "remote_API_and_downloaded_ZIP_verified_here": False,
        "required_next_readback": "After run completion, bind original run/artifact API metadata and downloaded ZIP digest before intake",
        "g2_source_bundle_complete": False, "R28_issued": False, "Native_calls": 0,
        "formal_acceptance": False, "automatic_retry": False})


RETAINED_MARKER = "retained-source-kit"
RETAINED_INPUT = "retained_fixedtxt_source_zip_base85"
RETAINED_BRANCHES = (
    "integrate/phase1-final-composition-20261027",
    "fix/native-production-a1-211feef-20261101-a4",
)
RETAINED_ZIP_BYTES = 49077
RETAINED_ZIP_SHA = "fe035303a8a8aaecdc70163d6ad9f953d6912920b65acf41672bfdf3f2bbf347"
RETAINED_BASE85_CHARS = 61347
RETAINED_EVENT_MAX = 256 * 1024
RETAINED_PAYLOAD_MAX = 65535
RETAINED_MEMBERS = {
    "template_film.original.json": (9584, "5b1f9e7befb11c3ef6354a9b3b292d105647f444cac4a13d6eb8d27bf7c10b97"),
    "managed_project.original.json": (6675, "03a3f03e0d34b3acd764a946b7af85a2e2b28043deb50c2b2792dc70d4495bc7"),
    "caption_source.txt": (35, "8d0254370cfa2cb7ebe8cc4b3b40cfec8e0f149253ffbf6008767b47f99ffa9e"),
    "red_native.ts": (33809, "dfe6c0fc226d7e9017eb718c7e745d942c4067a54f968d4483b80bbdd51b4fa8"),
    "measurement_protocol.zip": (35228, "ff20aaeba671616cbf3025ad62a20663edfa1c8e2b37e3ceffb8bdd3d8c2fe6f"),
}
RETAINED_ORIGIN = {
    "kind": "RETAINED_A1_COMPOSITION_NOT_A_NEW_FACTORY_RESULT",
    "archive_sha256": RETAINED_ZIP_SHA,
    "factory_roles": ["template_film.original.json", "managed_project.original.json"],
    "factory_origin": {
        "run_id": 41258472167, "run_attempt": 1, "artifact_id": 13064212124,
        "source_sha": "ffc307393be5adc3d2028bc79b30602eff43f24e",
        "semwright_sha": "5c2ac3107afbb083afb00d67a28edd0f104f8e15",
        "zip_sha256": "5e872f92a7affade9bc424c033b0b4e3c9bfb6ee1f27fe164665e151b9e87396",
    },
    "independent_roles": {
        "caption_source.txt": {"origin": "original 35-byte caption required by the FixedTxt contract"},
        "red_native.ts": {
            "origin": "A4 R56 retained explicit RED counterfactual overlay; not a physical result",
            "source_sdk": "e64a2d28d1d866c539a2c40824cbb57f86a13c10",
            "package_sha256": "b7c027565a512f483990bb53dfc8cfcdb8becdb090b0805b9dc367edc6bf59f3",
            "member": "arms/RED_NEW_COUNTERFACTUAL/integrations/composition/motion/native.ts",
        },
        "measurement_protocol.zip": {
            "origin": "A5 independent R39 preregistered numeric oracle; physical font baseline uncalibrated",
            "package_sha256": "ff20aaeba671616cbf3025ad62a20663edfa1c8e2b37e3ceffb8bdd3d8c2fe6f",
        },
    },
}


def retained_canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")


def retained_regular(path, maximum):
    """Bound the actual descriptor read; reject links and changes before use."""
    path = Path(path)
    need(path.is_absolute() and all(not p.is_symlink() for p in (path, *path.parents)),
         "retained original path must be absolute and have no symbolic links")
    before = path.lstat()
    def identity(value):
        return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns)
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
         and 0 < before.st_size <= maximum, "retained original must be one bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        need(identity(opened) == identity(before), "retained original changed at open")
        chunks, count = [], 0
        while count <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
        need(count == before.st_size and count <= maximum
             and identity(os.fstat(descriptor)) == identity(before)
             and identity(path.lstat()) == identity(before)
             and all(not p.is_symlink() for p in (path, *path.parents)),
             "retained original changed during bounded read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def retained_context(event, environment):
    """This source-only route has no runtime, factory or Native authority."""
    expected = {"GITHUB_ACTIONS": "true", "CI": "true",
                "GITHUB_REPOSITORY": "seradotcom/motionwright",
                "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_JOB": "fixedtxt-retained-source", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64"}
    need(all(environment.get(key) == value for key, value in expected.items()),
         "retained source requires the exact manual source-only job")
    ref = environment.get("GITHUB_REF", "")
    branches = {"refs/heads/" + branch: branch for branch in RETAINED_BRANCHES}
    need(ref in branches, "retained source branch is not explicitly admitted")
    branch = branches[ref]
    need(environment.get("GITHUB_REF_NAME") == branch
         and environment.get("GITHUB_WORKFLOW_REF") ==
             "seradotcom/motionwright/" + WORKFLOW + "@" + ref,
         "retained source workflow ref differs")
    need(type(event) is dict and event.get("ref") in (branch, ref)
         and type(event.get("repository")) is dict
         and event["repository"].get("full_name") == "seradotcom/motionwright",
         "retained source event origin differs")
    inputs = event.get("inputs")
    required = {"verification_scope", "expected_source_sha", "fixedtxt_mode",
                "fixedtxt_request", RETAINED_INPUT}
    optional_empty = {"g2_admission", "fixedtxt_delivery", "retained_reference_zip_base64"}
    need(type(inputs) is dict, "retained source input roster: not-object")
    need(required <= set(inputs), "retained source input roster: missing-required")
    need(set(inputs) <= required | optional_empty, "retained source input roster: unknown-key")
    need(all(type(inputs[key]) is str for key in required),
         "retained source input roster: required-type")
    need(len(retained_canonical({"ref": branch, "inputs": inputs})) <= RETAINED_PAYLOAD_MAX,
         "retained source dispatch exceeds the complete payload bound")
    # Optional disabled transports may be absent/null in the event. Preserve the
    # original event and bound; never coerce an authority-bearing value to empty.
    need(all(inputs.get(key) is None or (type(inputs.get(key)) is str and inputs[key] == "")
             for key in optional_empty),
         "retained source input roster: optional-authority-nonempty")
    need(inputs["verification_scope"] == "source" and inputs["fixedtxt_mode"] == "prepare"
         and inputs["fixedtxt_request"] == RETAINED_MARKER,
         "retained source cannot carry runtime or delivery authority")
    head = environment.get("GITHUB_SHA", "")
    need(re.fullmatch(r"[0-9a-f]{40}", head)
         and head == environment.get("GITHUB_WORKFLOW_SHA") == inputs["expected_source_sha"],
         "retained source and workflow SHA differ")
    run = environment.get("GITHUB_RUN_ID", "")
    need(re.fullmatch(r"[1-9][0-9]*", run), "retained source needs a real run ID")
    return {"source_sha": head, "semwright_sha": SDK, "run_id": run, "run_attempt": "1"}


def retained_members(archive):
    """The closed five original roles are validated before any output exists."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            entries = bundle.infolist()
            need(len(entries) == len(RETAINED_MEMBERS)
                 and {entry.filename for entry in entries} == set(RETAINED_MEMBERS),
                 "retained source ZIP must contain exactly the five declared members")
            result = {}
            for entry in entries:
                size, digest = RETAINED_MEMBERS[entry.filename]
                need(not entry.is_dir() and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                     and not (entry.flag_bits & 1) and entry.compress_type == zipfile.ZIP_DEFLATED
                     and entry.file_size == size and 0 < entry.compress_size <= RETAINED_ZIP_BYTES,
                     "retained source member type or size differs")
                with bundle.open(entry) as stream:
                    raw = stream.read(size + 1)
                    need(len(raw) == size and stream.read(1) == b"" and sha(raw) == digest,
                         "retained source original member differs")
                result[entry.filename] = raw
            return result
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError):
        raise ValueError("retained source ZIP structure or CRC is invalid") from None


def decode_retained_source(encoded):
    need(type(encoded) is str and len(encoded) == RETAINED_BASE85_CHARS,
         "retained source Base85 length differs")
    try:
        archive = base64.b85decode(encoded.encode("ascii"))
    except (ValueError, UnicodeError):
        raise ValueError("retained source Base85 is invalid") from None
    need(base64.b85encode(archive).decode("ascii") == encoded,
         "retained source Base85 is not canonical")
    need(len(archive) == RETAINED_ZIP_BYTES and sha(archive) == RETAINED_ZIP_SHA,
         "retained source ZIP is not the byte-identical admitted original")
    return archive, retained_members(archive)


def read_retained_event(environment=None):
    environment = os.environ if environment is None else environment
    raw = retained_regular(environment.get("GITHUB_EVENT_PATH", ""), RETAINED_EVENT_MAX)
    event = strict(raw)
    current = retained_context(event, environment)
    archive, members = decode_retained_source(event["inputs"][RETAINED_INPUT])
    return current, archive, members, {
        "event_sha256": sha(raw), "event_bytes": len(raw),
        "dispatch_payload_bytes": len(retained_canonical({
            "ref": environment["GITHUB_REF_NAME"], "inputs": event["inputs"]})),
    }


def retained_layout(current):
    temp = Path(os.environ["RUNNER_TEMP"])
    need(temp.is_absolute() and temp.is_dir() and temp.resolve(strict=True) == temp
         and str(temp).startswith("/home/runner/work/_temp")
         and all(not p.is_symlink() for p in (temp, *temp.parents)), "canonical retained runner temp required")
    owner = temp / ("mw-fixedtxt-retained-source-kit-" + current["run_id"] + "-1")
    return owner, owner / "payload", owner / "custody"


def retained_payload_manifest():
    return {name: {"bytes": size, "sha256": digest}
            for name, (size, digest) in RETAINED_MEMBERS.items()}


def retained_receipt(current, snapshot, event_identity):
    return {
        "schema": "motionwright.fixedtxt-retained-source-transport/1",
        "state": "FIVE_RETAINED_SOURCE_ROLES_ONLY",
        "transport_context": current, "original_source_origin": RETAINED_ORIGIN,
        "retained_archive": {"bytes": RETAINED_ZIP_BYTES, "sha256": RETAINED_ZIP_SHA},
        "payload": retained_payload_manifest(), "source": snapshot, "event": event_identity,
        "source_roles_complete": True, "g2_source_bundle_complete": False,
        "factory_invocations": 0, "SDK_builds": 0, "Native_calls": 0, "R28_issued": False,
        "native_helper_binary_custody": "NOT_RUN", "physical_font_color_baselines": "NOT_RUN",
        "physical_edit_save_reopen": "NOT_RUN", "original_API_revalidated": False,
        "F_selected": False, "formal_G2": "NOT_GRANTED", "G3": "NOT_GRANTED",
        "formal_acceptance": False, "release_authorized": False, "automatic_retry": False,
    }


def retain_original_bundle():
    need(sys.platform == "linux", "retained source requires the real Linux source job")
    current, archive, members, event_identity = read_retained_event()
    evidence = selected_pins()
    snapshot = custody.source_snapshot(ROOT, current["source_sha"], (
        *SOURCE_PATHS, evidence, "tooling/public_surface_policy.py",
        "tooling/tests/test_fixedtxt_retained_source_kit.py"))
    source_matches(snapshot)
    owner, payload, retained = retained_layout(current)
    need(not owner.exists() and not owner.is_symlink(), "retained source owner already exists; no retry")
    # All source, context, archive and closed-member checks precede this first write.
    owner.mkdir(mode=0o700, exist_ok=False)
    payload.mkdir(mode=0o700)
    retained.mkdir(mode=0o700)
    write_new(retained / "source-inputs.original.zip", archive)
    for name, raw in members.items():
        write_new(payload / name, raw)
    json_new(retained / "SOURCE_TRANSPORT.original.json", retained_receipt(current, snapshot, event_identity))
    source_matches(snapshot)


def record_retained_upload():
    need(sys.platform == "linux", "retained upload requires the real Linux source job")
    current, archive, members, _ = read_retained_event()
    selected_pins()
    _, payload, retained = retained_layout(current)
    need(payload.is_dir() and retained.is_dir()
         and all(not p.is_symlink() for p in (payload, retained, *payload.parents)),
         "retained upload directories changed")
    receipt = strict(retained_regular(retained / "SOURCE_TRANSPORT.original.json", 1024 * 1024))
    need(receipt["transport_context"] == current and receipt["original_source_origin"] == RETAINED_ORIGIN
         and receipt["payload"] == retained_payload_manifest()
         and receipt["factory_invocations"] == receipt["SDK_builds"] == receipt["Native_calls"] == 0
         and receipt["R28_issued"] is False and receipt["formal_acceptance"] is False,
         "retained upload provenance changed")
    source_matches(receipt["source"])
    need(retained_regular(retained / "source-inputs.original.zip", RETAINED_ZIP_BYTES) == archive,
         "retained input ZIP changed before upload binding")
    need({path.name for path in payload.iterdir()} == set(RETAINED_MEMBERS),
         "retained upload payload is not exactly five files")
    for name, raw in members.items():
        need(retained_regular(payload / name, RETAINED_MEMBERS[name][0]) == raw,
             "retained payload original changed before upload binding")
    artifact_id = os.environ.get("MW_FIXEDTXT_RETAINED_ARTIFACT_ID", "")
    digest = os.environ.get("MW_FIXEDTXT_RETAINED_ARTIFACT_DIGEST", "")
    need(re.fullmatch(r"[1-9][0-9]*", artifact_id) and re.fullmatch(r"[0-9a-f]{64}", digest),
         "retained upload requires actual artifact action outputs")
    json_new(retained / "UPLOAD_BINDING.original.json", {
        "schema": "motionwright.fixedtxt-retained-upload-binding/1",
        "kind": "ACTUAL_REUPLOAD_ACTION_OUTPUTS_NOT_A_NEW_FACTORY",
        "transport_context": current,
        "artifact": {"artifact_id": int(artifact_id), "run_id": int(current["run_id"]),
                     "run_attempt": 1, "source_sha": current["source_sha"], "zip_sha256": digest},
        "artifact_name": "motionwright-fixedtxt-retained-source-" + current["source_sha"] + "-" + current["run_id"] + "-1",
        "input_archive_sha256": RETAINED_ZIP_SHA, "payload": retained_payload_manifest(),
        "original_source_origin": RETAINED_ORIGIN,
        "remote_API_and_downloaded_ZIP_verified_here": False,
        "required_next_readback": "Completed run and actual artifact API metadata plus downloaded ZIP digest before existing intake",
        "factory_invocations": 0, "SDK_builds": 0, "Native_calls": 0, "R28_issued": False,
        "g2_source_bundle_complete": False, "formal_acceptance": False,
        "F_selected": False, "formal_G2": "NOT_GRANTED", "G3": "NOT_GRANTED",
        "release_authorized": False, "automatic_retry": False,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", action="store_true")
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--record-upload", action="store_true")
    group.add_argument("--retain-original-bundle", action="store_true")
    group.add_argument("--record-retained-upload", action="store_true")
    args = parser.parse_args()
    if args.start:
        start()
    elif args.prepare:
        prepare()
    elif args.record_upload:
        record_upload()
    elif args.retain_original_bundle:
        retain_original_bundle()
    else:
        record_retained_upload()


if __name__ == "__main__":
    main()
