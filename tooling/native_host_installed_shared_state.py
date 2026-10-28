#!/usr/bin/env python3
"""One private Linux installed-Desktop -> genuine Native Host -> Desktop scenario.

This is separate from the unchanged two-identical-snapshot first-use gate.
The actual installed UI creates one project; the real owner daemon provisions
its Graph selection; one retained Native SDK rename touches that same database;
the same AppRun/profile then reads the title through AT-SPI. There is no JSON
actor, database copy, synthetic grant, effect retry, or HR claim from cleanup.

All receipts are private. A successful run only proves the stated sequential
installed shared-state route; revocation and hostile IPC remain separate gates.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib

import candidate_smoke as candidate
import linux_first_use_custody as custody
import linux_cold_start as cold
import linux_first_use_accessible as accessible
import source_checkout_observation as source
import storage_schema_contract as storage
from linux_gui_probe import run_probe
from native_provider_lifecycle import tree_snapshot

ROOT = Path(__file__).resolve().parents[1]
TITLE = "motionwright installed host shared state"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SCHEMA = "motionwright.ci.installed-native-shared-state/1"
MAX_INPUT_BYTES = 4 * 1024 * 1024 * 1024
SOURCE_PATHS = (
    "SOURCE_LOCK.json", "Cargo.toml", "Cargo.lock",
    "tooling/native_host_installed_shared_state.py",
    "tooling/native_host_shared_state.py", "tooling/native_av_failure_guard.py",
    "tooling/linux_gui_probe.py", "tooling/linux_cold_start.py",
    "tooling/linux_first_use_accessible.py", "tooling/source_checkout_observation.py",
    "tooling/storage_schema_contract.py", "tooling/native_provider_lifecycle.py",
    "tooling/candidate_smoke.py", "tooling/linux_first_use_custody.py",
    "tooling/runtime_first_use_audit.py", "scripts/candidate_dependency_capture.py",
    ".github/workflows/package-candidate.yml",
    "apps/desktop/src-tauri/src/main.rs", "apps/desktop/src-tauri/src/desktop_owner.rs",
    "apps/desktop/src-tauri/capabilities/desktop-owner.json",
    "apps/studio/src/App.tsx", "apps/studio/src/ProjectLibraryDialog.tsx",
    "crates/native/src/lib.rs", "crates/native/src/bin/motionwright-native-provider.rs",
    "crates/storage/src/lib.rs",
)
SDK_PATHS = (
    "Cargo.toml", "Cargo.lock", "crates/daemon/src/semwrightd.rs",
    "crates/daemon/src/server.rs", "crates/daemon/src/config.rs",
    "crates/core/src/lib.rs", "crates/core/src/provider_mounts.rs",
    "crates/core/src/operation_permits.rs", "crates/policy/src/lib.rs",
    "crates/native-sdk/src/lib.rs", "crates/native-sdk/src/driver.rs",
    "crates/driver-sdk/src/lib.rs", "crates/cli/src/lib.rs",
)


class ScenarioError(RuntimeError):
    """A bounded code; original streams remain in the private namespace."""


def need(value, code):
    if not value:
        raise ScenarioError(code)


def publish(path: Path, value) -> None:
    """Exclusive durable custody: a previous attempt is never overwritten."""
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_json(path: Path, maximum: int = 4 * 1024 * 1024):
    before = source.regular_file(path, maximum)
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            need(key not in value, "DUPLICATE_JSON_KEY")
            value[key] = item
        return value
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
    except (UnicodeError, ValueError, RecursionError):
        raise ScenarioError("INVALID_PRIVATE_JSON") from None
    need(source.regular_file(path, maximum) == before, "PRIVATE_JSON_CHANGED")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("repo", "semwright-checkout", "artifact", "candidate-receipt",
                 "provider", "namespace"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--semwright-sha", required=True)
    parser.add_argument("--provider-sha256", required=True)
    parser.add_argument("--provider-kind", choices=("source-built", "instrumented"),
                        default="source-built")
    parser.add_argument("--seconds", type=int, default=24)
    args = parser.parse_args(argv)
    for name in ("source_sha", "semwright_sha"):
        if not SHA40.fullmatch(getattr(args, name)):
            parser.error("source and SDK revisions must be full lowercase Git SHAs")
    if not SHA256.fullmatch(args.provider_sha256):
        parser.error("provider hash must be a full lowercase SHA256")
    if not 8 <= args.seconds <= 45:
        parser.error("installed scenario launch bounds are 8 through 45 seconds")
    return args


def limitations():
    return {
        "installed_desktop_shared_state": "NOT_RUN",
        "simultaneous_live_window_refresh": "NOT_RUN",
        "desktop_ipc_negative_matrix": "NOT_RUN",
        "restricted_other_client_and_resource_matrix": "NOT_RUN",
        "pending_revocation_HR": "NOT_RUN",
        "human_install_acceptance": "NOT_RUN",
        "renderer_acceptance": "NOT_RUN",
        "provider_installation": "NOT_CLAIMED",
        "process_cleanup_is_revocation_evidence": False,
    }


def admit_environment(args):
    need(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true",
         "DISPOSABLE_LINUX_CI_REQUIRED")
    need(Path(sys.executable).resolve() == Path("/usr/bin/python3").resolve(),
         "SYSTEM_PYTHON_WITH_GI_REQUIRED")
    need(os.environ.get("DISPLAY") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
         "LIVE_X11_AND_DBUS_REQUIRED")
    need(os.environ.get("GITHUB_SHA") == args.source_sha, "CI_SOURCE_MISMATCH")
    need(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "ORIGINAL_MANUAL_ATTEMPT_REQUIRED")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    need(re.fullmatch(r"[1-9][0-9]*", run_id)
         and os.environ.get("GITHUB_WORKFLOW_REF"), "CI_RUN_IDENTITY_REQUIRED")
    need(shutil.which("bwrap") == "/usr/bin/bwrap", "REAL_BUBBLEWRAP_REQUIRED")
    temporary = Path(os.environ.get("RUNNER_TEMP", "")).absolute()
    need(temporary == temporary.resolve(strict=True) and temporary.is_dir(),
         "CANONICAL_RUNNER_TEMP_REQUIRED")
    for path in (args.repo, args.semwright_checkout):
        need(path.is_absolute() and path == path.resolve(strict=True), "CANONICAL_SOURCE_ROOT_REQUIRED")
    need(args.repo == ROOT.resolve(strict=True), "EXECUTING_SOURCE_CHECKOUT_MISMATCH")
    expected = temporary / ("native-host-installed-" + run_id + "-1-" + args.source_sha)
    need(args.namespace == expected and not expected.is_relative_to(args.repo),
         "EXACT_NEW_SCENARIO_NAMESPACE_REQUIRED")
    need(not expected.exists() and not expected.is_symlink(), "SCENARIO_ALREADY_ATTEMPTED_NO_RETRY")
    return {"run_id": run_id, "run_attempt": "1",
            "workflow_ref": os.environ["GITHUB_WORKFLOW_REF"],
            "event_name": "workflow_dispatch"}


def source_inputs(args):
    own = source.source_observation(args.repo, args.source_sha, SOURCE_PATHS)
    sdk = source.source_observation(args.semwright_checkout, args.semwright_sha, SDK_PATHS)
    lock = read_json(args.repo / "SOURCE_LOCK.json")
    need(lock["dependencies"]["semwright"]["revision"] == args.semwright_sha,
         "LOCKED_SDK_MISMATCH")
    cohort = storage.admit_checkout(args.repo, args.source_sha)
    storage.validate_cohort(cohort, source_sha=args.source_sha, source_tree=own["tree"])
    return {"motionwright": own, "semwright": sdk, "storage_cohort": cohort}


def extract_installed_bundle(args):
    """Observe the canonical AppImage extraction once in a fresh private directory."""
    need(args.artifact.is_absolute() and args.candidate_receipt.is_absolute(),
         "ABSOLUTE_CANDIDATE_INPUTS_REQUIRED")
    artifact_before = source.regular_file(args.artifact, MAX_INPUT_BYTES)
    receipt_before = source.regular_file(args.candidate_receipt, 4 * 1024 * 1024)
    verified = candidate.verify_installer(read_json(args.candidate_receipt), "appimage", args.artifact)
    need(verified["candidate"]["source_revision"] == args.source_sha
         and verified["candidate"]["semwright_revision"] == args.semwright_sha,
         "CANDIDATE_RECEIPT_SOURCE")
    need(os.access(args.artifact, os.X_OK), "APPIMAGE_NOT_EXECUTABLE")
    work = args.namespace / "appimage"
    work.mkdir(mode=0o700)
    command = [str(args.artifact), "--appimage-extract"]
    publish(args.namespace / "appimage-extraction-intent.json", {
        "command": command, "cwd": str(work), "artifact": artifact_before,
        "candidate_receipt": receipt_before, "automatic_retry": False,
    })
    terminal = {"status": "FAIL", "exit_code": None, "automatic_retry": False}
    try:
        with owned_process_scope(args.namespace / "appimage-extraction-closure.json"):
            with (args.namespace / "appimage-extraction.stdout.original").open("xb") as stdout, (
                args.namespace / "appimage-extraction.stderr.original"
            ).open("xb") as stderr:
                completed = subprocess.run(command, cwd=work, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, timeout=90, check=False)
                terminal["exit_code"] = completed.returncode
                for stream in (stdout, stderr):
                    stream.flush()
                    os.fsync(stream.fileno())
        # Descendants may inherit the log descriptors. Sync the final originals
        # only after the complete extraction cohort has closed.
        terminal["streams"] = {}
        for name in ("appimage-extraction.stdout.original", "appimage-extraction.stderr.original"):
            path = args.namespace / name
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            terminal["streams"][name] = source.regular_file(path, custody.MAX_FILE)
        need(terminal["exit_code"] == 0, "APPIMAGE_EXTRACTION_FAILED_NO_RETRY")
        need(source.regular_file(args.artifact, MAX_INPUT_BYTES) == artifact_before
             and source.regular_file(args.candidate_receipt, 4 * 1024 * 1024) == receipt_before,
             "CANDIDATE_CHANGED_DURING_EXTRACTION")
        extracted = work / "squashfs-root"
        need(extracted.is_dir() and extracted == extracted.resolve(strict=True),
             "EXTRACTED_BUNDLE_ROOT_INVALID")
        app_run = extracted / "AppRun"
        need(app_run.resolve(strict=True).is_relative_to(extracted) and os.access(app_run, os.X_OK),
             "EXTRACTED_APP_RUN_INVALID")
        bin_root = extracted / "usr" / "bin"
        need(bin_root == bin_root.resolve(strict=True), "EXTRACTED_BINARY_DIRECTORY_INVALID")
        binaries = [path for path in bin_root.iterdir()
                    if "motionwright" in path.name.lower()
                    and stat.S_ISREG(path.lstat().st_mode) and os.access(path, os.X_OK)]
        desktop_entries = list(extracted.glob("*.desktop"))
        need(len(binaries) == 1 and len(desktop_entries) == 1,
             "EXTRACTED_PACKAGE_MEMBERS_AMBIGUOUS")
        args.app_run, args.installed_binary = app_run, binaries[0]
        smoke = candidate.build_receipt(
            candidate_receipt=args.candidate_receipt, artifact=args.artifact,
            installed_binary=args.installed_binary, kind="appimage", method="appimage_extract",
            linux_desktop_entry=desktop_entries[0], linux_gui_probe=None,
        )
        publish(args.namespace / "appimage-candidate-smoke.original.json", smoke)
        terminal.update(
            status="PASS", app_run=str(args.app_run), installed_binary=str(args.installed_binary),
            app_run_target=source.regular_file(app_run.resolve(strict=True), MAX_INPUT_BYTES),
            binary=smoke["automated_inspection"]["binary"],
        )
        return terminal
    finally:
        publish(args.namespace / "appimage-extraction-terminal.json", terminal)


def closed_profile_identity(data):
    """Hash every closed profile member, retaining the original tree in place."""
    files, proofs = custody.tree(data)
    need(data in proofs and files, "CLOSED_PROFILE_MISSING")
    need(len(files) <= custody.MAX_MEMBERS
         and all(0 <= info.st_size <= custody.MAX_FILE for info in files.values())
         and sum(info.st_size for info in files.values()) <= custody.MAX_TOTAL,
         "CLOSED_PROFILE_CUSTODY_BOUND")
    members = {
        path.relative_to(data).as_posix():
            custody.copy_original(path, custody.stamp(info), custody.MAX_FILE)
        for path, info in sorted(files.items())
    }
    _, after = custody.tree(data)
    need(proofs == after, "CLOSED_PROFILE_CHANGED_DURING_CUSTODY")
    return {"root": str(data), "originals_retained_in_place": True,
            "scope": "ALL_REGULAR_FILES_INCLUDING_SQLITE_SIDECARS_AND_CAS",
            "members": members}


def binary_inputs(args):
    app_dir = args.app_run.parent.resolve(strict=True)
    need(args.app_run.name == "AppRun" and args.app_run.parent == app_dir,
         "EXACT_APP_RUN_REQUIRED")
    target = args.app_run.resolve(strict=True)
    need(target.is_relative_to(app_dir) and args.installed_binary.is_relative_to(app_dir),
         "INSTALLED_EXECUTABLE_ESCAPES_BUNDLE")
    paths = {
        "app_run_target": target, "installed_binary": args.installed_binary,
        "artifact": args.artifact, "candidate_receipt": args.candidate_receipt,
        "provider": args.provider,
        **{name: args.semwright_checkout / "target" / "debug" / name
           for name in ("semwright", "semwrightd", "semwright-sandbox")},
    }
    records = {name: {"path": str(path), **source.regular_file(path, MAX_INPUT_BYTES)}
               for name, path in paths.items()}
    for name in ("app_run_target", "installed_binary", "provider", "semwright",
                 "semwrightd", "semwright-sandbox"):
        need(os.access(paths[name], os.X_OK), "EXECUTABLE_UNAVAILABLE")
    need(records["provider"]["sha256"] == args.provider_sha256, "PROVIDER_HASH_MISMATCH")
    info = args.app_run.lstat()
    records["app_run_entry"] = {
        "path": str(args.app_run), "device": info.st_dev, "inode": info.st_ino,
        "mode": info.st_mode, "mtime_ns": info.st_mtime_ns,
        "symlink_target": os.readlink(args.app_run) if stat.S_ISLNK(info.st_mode) else None,
    }
    validate_candidate(read_json(args.candidate_receipt), args, records["artifact"])
    return records


def validate_candidate(value, args, artifact):
    need(isinstance(value, dict) and value.get("schema_version") == 1,
         "CANDIDATE_RECEIPT_SCHEMA")
    candidate = value.get("candidate")
    need(isinstance(candidate, dict)
         and candidate.get("application") == "Motionwright"
         and candidate.get("platform") == "linux"
         and candidate.get("source_revision") == args.source_sha
         and candidate.get("semwright_revision") == args.semwright_sha,
         "CANDIDATE_RECEIPT_SOURCE")
    artifacts = value.get("artifacts")
    need(isinstance(artifacts, list) and 1 <= len(artifacts) <= 2, "CANDIDATE_ARTIFACTS")
    matching = [item for item in artifacts if isinstance(item, dict)
                and item.get("filename") == args.artifact.name]
    need(len(matching) == 1 and matching[0].get("bundle_kind") == "appimage"
         and matching[0].get("bytes") == artifact["bytes"]
         and matching[0].get("sha256") == artifact["sha256"], "CANDIDATE_ARTIFACT_MISMATCH")


def select_reopened_view(bridge, pid, revision, title=TITLE):
    """Read actual visible editor text, with ownership checked on every witness."""
    apps = bridge.applications()
    need(len(apps) <= 128, "UI_APPLICATION_LIMIT")
    app = accessible.unique([item for item in apps if bridge.pid(item) == pid], "APPLICATION")
    views = accessible.walk(bridge, app)
    visible = [view for view in views
               if accessible.VISIBLE <= view.states and "defunct" not in view.states]
    if any(view.role == "dialog" and view.name == "Project library" for view in visible):
        raise accessible.NotReady("EDITOR_STILL_IN_LIBRARY")
    witnesses = {}
    for label, name, button in (("title", title, False), ("revision", "r" + str(revision), False),
                                 ("library", "Open project library", True),
                                 ("workspace", "Timeline", True)):
        found = [view for view in visible if view.name == name
                 and (view.role == "button" if button else view.role != "entry")
                 and "editable" not in view.states and "EditableText" not in view.interfaces]
        if not found:
            raise accessible.NotReady("MISSING_REOPENED_" + label.upper())
        need(len(found) <= 8, "REOPENED_TEXT_AMBIGUITY_LIMIT")
        for view in found:
            need(bridge.owner_pid(view.node) == pid, "UI_WITNESS_OWNER_CHANGED")
            if not button and "Text" in view.interfaces:
                need(bridge.read_text(view.node) == name, "UI_NAME_AND_TEXT_DISAGREE")
        witnesses[label] = {"name": name, "visible_matches": len(found)}
    return witnesses


def read_ui_worker(argv):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--deadline", type=float, required=True)
    args = parser.parse_args(argv)
    need(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true",
         "DISPOSABLE_LINUX_CI_REQUIRED")
    need(math.isfinite(args.deadline) and 0 < args.deadline - time.monotonic() <= 45,
         "READ_UI_DEADLINE")
    intent = read_json(args.directory / "intent.json", 16384)
    binding, revision, title = intent["binding"], intent["revision"], intent["title"]
    need(type(revision) is int and revision >= 0
         and title in (cold.EXPECTED_INITIAL_TITLE, TITLE),
         "READ_UI_INTENT_INVALID")
    result = {"status": "FAIL", "binding": binding, "mutations": 0}
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        need(Atspi.init() in (0, 1), "ATSPI_INIT")
        Atspi.set_timeout(800, 2500)
        bridge = accessible.AtspiBridge(Atspi, deadline=args.deadline)
        witnesses = observe_editor_until(bridge, binding, revision, title, args.deadline)
        result.update(status="PASS", witnesses=witnesses)
    except Exception as error:
        result["failure_type"] = type(error).__name__
    finally:
        publish(args.directory / "result.json", result)
    return 0 if result["status"] == "PASS" else 1


def observe_editor_until(bridge, binding, revision, title, deadline):
    while time.monotonic() < deadline:
        try:
            need(accessible.x11_identity(deadline=deadline) == binding, "UI_PROCESS_CHANGED")
            witnesses = select_reopened_view(bridge, binding["pid"], revision, title)
            need(accessible.x11_identity(deadline=deadline) == binding, "UI_PROCESS_CHANGED")
        except accessible.NotReady:
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            continue
        return witnesses
    raise ScenarioError("READ_UI_DEADLINE")


def invoke_read_ui(root, binding, revision, seconds, *, phase="reopened", title=TITLE):
    need(phase in ("created", "reopened"), "UI_PHASE_INVALID")
    directory = root / (phase + "-atspi")
    directory.mkdir(mode=0o700)
    publish(directory / "intent.json", {"binding": binding, "revision": revision,
                                       "title": title, "mutations": 0})
    with (directory / "worker.stdout").open("xb") as out, (directory / "worker.stderr").open("xb") as err:
        try:
            process = subprocess.run(
                [sys.executable, "-B", "-E", "-s", str(Path(__file__).resolve()),
                 "--read-ui-worker", "--directory", str(directory),
                 "--deadline", repr(time.monotonic() + seconds - 1)],
                stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=seconds, check=False,
            )
        except subprocess.TimeoutExpired:
            publish(directory / "parent-timeout.json", {"status": "UNKNOWN", "retry": "FORBIDDEN"})
            raise ScenarioError("READ_UI_WORKER_TIMEOUT") from None
    result = read_json(directory / "result.json", 16384)
    need(process.returncode == 0 and result.get("status") == "PASS"
         and result.get("binding") == binding and result.get("mutations") == 0,
         "REOPENED_UI_NOT_OBSERVED")
    return result


def gui_launch(args, profile, cohort, *, phase, snapshot=None):
    """One UI create or one readonly reopen; preserve the overwritten probe log."""
    deadline = time.monotonic() + args.seconds
    state = {"phase": phase, "interaction_started": False}

    def budget():
        remaining = deadline - time.monotonic() - 2
        need(remaining >= 2, "NO_UI_WORKER_BUDGET")
        return remaining

    def interact(root):
        need(not state["interaction_started"], "UI_CALLBACK_REENTRY_FORBIDDEN")
        if phase == "created" and not cold._empty_library_ready(root, storage_cohort=cohort):
            return False
        try:
            binding = accessible.x11_identity(deadline=deadline)
        except accessible.NotReady:
            return False
        state["executing_binary"] = executing_desktop(binding, args.installed_binary)
        need(accessible.x11_identity(deadline=deadline) == binding, "UI_PROCESS_CHANGED")
        state["binding"] = binding
        state["interaction_started"] = True
        if phase == "created":
            state["create_action"] = accessible.invoke_worker(
                root, seconds=budget(), source_sha=args.source_sha,
                semwright_sha=args.semwright_sha, binding=binding,
            )
        return True

    def ready(root):
        # This is the pre-existing readonly readiness contract, not the final
        # closed-process snapshot used for acceptance.
        if phase == "created":
            try:
                current = cold.read_snapshot(root / "data", storage_cohort=cohort)
            except cold.ColdStartError:
                return False
            title, revision = current.title, current.revision
        else:
            title, revision = TITLE, snapshot.revision
        if "ui_readback" not in state:
            state["ui_readback"] = invoke_read_ui(
                root, state["binding"], revision, budget(), phase=phase, title=title,
            )
        return True

    publish(profile.parent / ("ui-" + phase + "-intent.json"), {
        "app_run": str(args.app_run), "profile": str(profile), "phase": phase,
        "source_sha": args.source_sha, "automatic_retry": False,
        "mutations": 1 if phase == "created" else 0,
    })
    try:
        state["window"] = run_probe(args.app_run, profile, seconds=args.seconds,
                                    ready_check=ready, window_interaction=interact)
        need(state["window"].get("status") == "WINDOW_OBSERVED", "INSTALLED_WINDOW_NOT_OBSERVED")
        return state
    finally:
        publish(profile.parent / ("ui-" + phase + "-observation.json"), state)


def closed_gui_launch(args, profile, cohort, *, phase, snapshot=None):
    """Retain a phase log only after every process that could write it is closed."""
    closure = profile.parent / ("ui-" + phase + "-closure.json")
    try:
        with owned_process_scope(closure):
            return gui_launch(args, profile, cohort, phase=phase, snapshot=snapshot)
    finally:
        if closure.exists() and read_json(closure).get("status") == "PASS":
            log = profile / "app-run.log"
            if log.exists():
                retained_path = profile.parent / ("app-run-" + phase + ".log")
                # Only the closed log is copied. The actual database stays in place.
                with log.open("rb") as original, retained_path.open("xb") as retained:
                    shutil.copyfileobj(original, retained)
                    retained.flush()
                    os.fsync(retained.fileno())
                retained_path.chmod(0o600)


def executing_desktop(binding, binary):
    """Bind the observed window to an actual descendant executing this binary."""
    tree = tree_snapshot(os.getpid())
    need(tree["root_observed"] and not tree["truncated"]
         and any(item["pid"] == binding["pid"] and item["pid"] != os.getpid()
                 for item in tree["processes"]), "UI_WINDOW_NOT_OUR_LAUNCHED_DESCENDANT")
    expected = source.regular_file(binary, MAX_INPUT_BYTES)
    identity = binary.stat()
    # /proc/<owned child>/exe is a kernel link, admitted only after the real
    # parentage check above. Open it to bind the executing inode, not its label.
    with (Path("/proc") / str(binding["pid"]) / "exe").open("rb") as stream:
        observed = os.fstat(stream.fileno())
        need((observed.st_dev, observed.st_ino) == (identity.st_dev, identity.st_ino),
             "UI_EXECUTABLE_NOT_THE_INSTALLED_BINARY")
        count, digest = 0, hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            count += len(block)
            need(count <= expected["bytes"], "EXECUTING_BINARY_SIZE_CHANGED")
            digest.update(block)
    need({"bytes": count, "sha256": digest.hexdigest()} == expected,
         "EXECUTING_BINARY_HASH_CHANGED")
    need(source.regular_file(binary, MAX_INPUT_BYTES) == expected, "INSTALLED_BINARY_CHANGED")
    return {"pid": binding["pid"], "device": observed.st_dev, "inode": observed.st_ino, **expected}


def provider_manifest(provider, version):
    """The same protocol-9 descriptor shape as the repaired Host gate."""
    return {
        "manifest_version": 1, "protocol": 9, "id": "motionwright", "version": version,
        "publisher": "motionwright-project", "executable": str(provider),
        "sha256": source.regular_file(provider, MAX_INPUT_BYTES)["sha256"],
        "application": {"desktop_id": None, "process_names": ["motionwright-native-provider"],
                        "supported_versions": []},
        "transport": "stdio_v1", "network": False,
        "mounts": [{"root": "motionwright-data", "read_only": False, "execute": False}],
        "resources": {"open_files": 128, "processes": 16, "cpu_seconds": 90,
                      "operation_cpu_seconds": 0, "address_space_bytes": 1073741824,
                      "file_size_bytes": 67108864},
        "request_timeout_ms": 10000,
        "interfaces": {"dynamic_capabilities": False, "cooperative_cancellation": True,
                       "events": False, "progress": False, "artifacts": False,
                       "health": True, "native_refs": True, "host_tools": False},
    }


def write_config(path, text):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def native_call(host, env, socket, session, binding, command, arguments, calls):
    receipt = host.invoke_cli(
        env, socket, session, "providers.execute_bound",
        {"binding_ref": binding, "request": {"command": command, "args": arguments,
                                              "dry_run": False}}, calls, ok=None,
    )
    return host.checked_native_result(receipt["envelope"], binding)


def observe_matches(value, initial, resource):
    need(value.get("ok") is True and isinstance(value.get("data"), dict), "NATIVE_OBSERVE_NOT_SUCCESS")
    data = value["data"]
    page = data.get("page", {})
    version = page.get("version", {})
    need(version.get("resource") == resource and version.get("generation") == initial.generation
         and version.get("revision") == str(initial.revision), "NATIVE_OBSERVE_SCOPE_MISMATCH")
    items = page.get("items")
    need(isinstance(items, list) and len(items) == 1 and items[0].get("title") == initial.title,
         "NATIVE_OBSERVE_TITLE_MISMATCH")
    need(isinstance(data.get("ref"), str) and data["ref"], "NATIVE_REF_NOT_ISSUED")
    return data["ref"]


def dispatch_rename_once(directory, dispatch, intent):
    """The retained intent is the one-attempt latch, even if the reply is lost."""
    publish(directory / "native-rename-intent.json", {**intent, "automatic_retry": False})
    result = {"status": "UNKNOWN", "automatic_retry": False}
    try:
        original = dispatch()
        result["original_child"] = original  # never reconstruct Error/outcome_known
        if original.get("ok") is True:
            result["status"] = "SUCCESS"
        elif (original.get("ok") is False and isinstance(original.get("error"), dict)
              and original["error"].get("outcome_known") is True):
            result["status"] = "KNOWN_FAILURE"
        return original
    finally:
        # On any parser, transport or timeout error, do not infer a no-op or
        # dispatch a new key. Originals retained by invoke_cli remain decisive.
        publish(directory / "native-rename-outcome.json", result)


def run_native_phase(args, database, initial, *, daemon_scope):
    """Genuine owner bootstrap and one bound operation, using the frozen Host API.

    daemon_scope must retain its complete-process closure proof on context exit;
    this phase never treats a successful leader wait as that proof.
    """
    import native_host_shared_state as host
    root = args.namespace / "host"
    root.mkdir(mode=0o700)
    folders = {}
    for name in ("state", "home", "config", "cache", "data", "binary", "evidence"):
        folders[name] = root / name
        folders[name].mkdir(mode=0o700)
    # sockaddr_un has a small fixed path budget. Keep the actual socket and
    # ticket in a short, private, retained directory, never move the UI Store.
    folders["runtime"] = Path(tempfile.mkdtemp(prefix="mwi-", dir="/tmp"))
    publish(root / "runtime-custody.json", {"path": str(folders["runtime"]),
                                          "owner_uid": os.getuid(), "retained": True})
    host.SW_BINS = args.semwright_checkout / "target" / "debug"
    host.EVIDENCE = folders["evidence"]
    env = {key: value for key, value in os.environ.items()
           if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}
           and not key.startswith(("MOTIONWRIGHT_", "SEMWRIGHT_"))}
    env.update(HOME=str(folders["home"]), XDG_RUNTIME_DIR=str(folders["runtime"]),
               XDG_STATE_HOME=str(folders["state"]), XDG_CONFIG_HOME=str(folders["config"]),
               XDG_CACHE_HOME=str(folders["cache"]), XDG_DATA_HOME=str(folders["data"]),
               RUST_BACKTRACE="1")
    effective = host.run_json([str(host.SW_BINS / "semwright"), "--json", "config", "paths"], env=env)
    state = Path(effective["state"]).resolve()
    need(state.is_relative_to(folders["state"]), "OWNER_STATE_OUTSIDE_PRIVATE_PROFILE")
    publish(root / "owner-paths.original.json", effective)
    provider = folders["binary"] / "motionwright-native-provider"
    with args.provider.open("rb") as original, provider.open("xb") as copied:
        shutil.copyfileobj(original, copied)
    provider.chmod(0o700)
    need(source.regular_file(provider, MAX_INPUT_BYTES)["sha256"] == args.provider_sha256,
         "COPIED_PROVIDER_HASH_MISMATCH")
    version = tomllib.loads((args.repo / "Cargo.toml").read_text())["workspace"]["package"]["version"]
    manifest = folders["config"] / "motionwright-driver.json"
    publish(manifest, provider_manifest(provider, version))
    resource = "project:" + initial.id  # the UUID came from the real UI's Store
    calls = []
    bootstrap = folders["config"] / "owner-bootstrap.toml"
    write_config(bootstrap, host.owner_config(database.parent))
    bootstrap_socket = folders["runtime"] / "bootstrap.sock"
    publish(root / "graph-bootstrap-intent.json", {"command": "project.create",
            "root": "motionwright-data", "automatic_retry": False})
    try:
        with daemon_scope(host, bootstrap, bootstrap_socket, env,
                          folders["evidence"] / "semwrightd-bootstrap.log", root / "bootstrap-closure.json"):
            created = host.invoke_cli(
                env, bootstrap_socket, folders["runtime"] / "bootstrap.session",
                "project.create", {"root": "motionwright-data"}, calls,
            )["envelope"]
            canonical = created.get("data", {}).get("project")
            need(isinstance(canonical, str), "CORE_GRAPH_PROJECT_NOT_ISSUED")
        host.retain_owner_audit(state, "bootstrap-audit.original")
        # owner_config validates the canonical ID but does not manufacture it.
        config = folders["config"] / "owner-restricted.toml"
        write_config(config, host.owner_config(database.parent, manifest=manifest,
                                               selections=[(resource, canonical)]))
        socket = folders["runtime"] / "broker.sock"
        session = folders["runtime"] / "native-client.session"
        with daemon_scope(host, config, socket, env, folders["evidence"] / "semwrightd.log",
                          root / "provider-closure.json"):
            mount = host.invoke_cli(env, socket, session, "providers.mount.observe",
                                    {"provider": "driver:motionwright", "root": "motionwright-data"},
                                    calls)["envelope"]["data"]
            binding = mount.get("binding_ref")
            need(isinstance(binding, str) and binding and mount.get("mount", {}).get("write") is True
                 and mount.get("mount", {}).get("root") == "motionwright-data",
                 "REAL_WRITABLE_MOUNT_NOT_ISSUED")
            observed = native_call(host, env, socket, session, binding, "driver.motionwright.observe",
                                   {"resource": resource, "scope": "summary", "limit": 8}, calls)
            native_ref = observe_matches(observed, initial, resource)
            renamed = dispatch_rename_once(
                root, lambda: native_call(host, env, socket, session, binding,
                                         "driver.motionwright.project.rename",
                                         {"ref": native_ref, "title": TITLE}, calls),
                {"command": "driver.motionwright.project.rename", "ref": native_ref, "title": TITLE,
                 "binding_ref": binding, "session": str(session), "resource": resource},
            )
            need(renamed.get("ok") is True, "NATIVE_RENAME_NOT_SUCCESS_NO_RETRY")
            saved = renamed.get("data", {})
            need(saved.get("revision") == str(initial.revision + 1)
                 and saved.get("project", {}).get("title") == TITLE,
                 "NATIVE_RENAME_REPLY_MISMATCH_NO_RETRY")
        host.retain_owner_audit(state, "restricted-audit.original")
        return {"resource": resource, "core_project": canonical,
                "native_result": renamed, "actual_database": str(database),
                "provider_kind_declared": args.provider_kind, "provider_installation": "NOT_CLAIMED",
                "effect_attempts": 1, "automatic_retry": False}
    finally:
        publish(root / "transport-calls.original.json", calls)


def database_identity(path):
    need(path == path.resolve(strict=True), "DATABASE_PATH_NOT_CANONICAL")
    value = path.stat()
    need(stat.S_ISREG(value.st_mode), "DATABASE_NOT_REGULAR")
    return {"path": str(path), "device": value.st_dev, "inode": value.st_ino}


def compare_committed(initial, changed):
    need(initial.id == changed.id and initial.generation == changed.generation
         and initial.schema_version == changed.schema_version, "STORE_IDENTITY_OR_GENERATION_CHANGED")
    need(changed.title == TITLE and changed.revision == initial.revision + 1
         and changed.event_count == initial.event_count + 1
         and changed.document_sha256 != initial.document_sha256,
         "ONE_NATIVE_MUTATION_NOT_OBSERVED")


def subreaper(value=None):
    """Linux PR_{GET,SET}_CHILD_SUBREAPER, scoped to this CI process only."""
    need(sys.platform == "linux", "LINUX_CHILD_CUSTODY_REQUIRED")
    libc = ctypes.CDLL(None, use_errno=True)
    if value is not None:
        need(value in (0, 1), "SUBREAPER_VALUE")
        status = libc.prctl(ctypes.c_int(36), ctypes.c_ulong(value),
                            ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0))
        need(status == 0, "SUBREAPER_SET_FAILED")
    current = ctypes.c_int()
    status = libc.prctl(ctypes.c_int(37), ctypes.byref(current),
                        ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0))
    need(status == 0 and current.value in (0, 1), "SUBREAPER_GET_FAILED")
    return current.value


@contextmanager
def supervise_own_processes():
    """Keep orphaned descendants of this scenario under the same kernel parent."""
    need(all(hasattr(os, name) for name in ("pidfd_open", "waitid", "P_PIDFD", "WNOWAIT"))
         and hasattr(signal, "pidfd_send_signal"), "KERNEL_PROCESS_CUSTODY_UNAVAILABLE")
    # The existing observer indexes /proc by PID. A mismatched procfs mount
    # must not make another namespace's process look like our own root.
    proc_pid = (Path("/proc/self/stat").read_text(encoding="ascii").split(" ", 1)[0])
    need(proc_pid == str(os.getpid()), "PROC_PID_NAMESPACE_MISMATCH")
    previous = subreaper()
    subreaper(1)
    try:
        yield
    finally:
        # Do not drop custody while any failed cleanup may still own children.
        observed = tree_snapshot(os.getpid())
        if (observed["root_observed"] and not observed["truncated"]
                and len(observed["processes"]) == 1):
            subreaper(previous)


def close_own_children(*, seconds=20, grace=2):
    """Reap genuine adopted children, and signal only kernel-verified pidfds.

    An exited daemon alone is insufficient. With subreaper enabled before any
    launch, surviving descendants become our children as their parents exit.
    Only direct children pass waitid(P_PIDFD); no unrelated PID can be signalled.
    A complete empty descendant tree is required before any final Store read.
    """
    need(subreaper() == 1 and 0 <= grace < seconds <= 20, "CHILD_CLOSURE_PRECONDITION")
    start = time.monotonic()
    samples, reaped, signals_sent = [], [], []
    while time.monotonic() - start < seconds:
        observed = tree_snapshot(os.getpid())
        samples.append(observed)
        if (observed["root_observed"] and not observed["truncated"]
                and len(observed["processes"]) == 1):
            return {"status": "PASS", "subreaper": True,
                    "complete_empty_descendant_tree": observed, "samples": samples,
                    "reaped": reaped, "signals": signals_sent,
                    "authority": "process_custody_only_not_revocation"}
        for child in observed["processes"]:
            if child["ppid"] != os.getpid() or child["pid"] == os.getpid():
                continue
            try:
                descriptor = os.pidfd_open(child["pid"], 0)
            except ProcessLookupError:
                continue
            try:
                try:
                    ended = os.waitid(os.P_PIDFD, descriptor, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                except ChildProcessError:
                    continue  # PID changed or this is not our kernel child.
                if ended is not None:
                    pid, status = os.waitpid(child["pid"], os.WNOHANG)
                    if pid:
                        reaped.append({"pid": pid, "wait_status": status})
                else:
                    number = signal.SIGKILL if time.monotonic() - start >= grace else signal.SIGTERM
                    try:
                        signal.pidfd_send_signal(descriptor, number)
                        signals_sent.append({"pid": child["pid"], "signal": int(number)})
                    except ProcessLookupError:
                        pass
            finally:
                os.close(descriptor)
        time.sleep(0.1)
    return {"status": "FAIL", "subreaper": True, "reason": "CHILD_CLOSURE_DEADLINE",
            "samples": samples, "reaped": reaped, "signals": signals_sent,
            "authority": "process_custody_only_not_revocation"}


@contextmanager
def owned_process_scope(receipt, *, seconds=20, grace=2):
    before = tree_snapshot(os.getpid())
    need(subreaper() == 1 and before["root_observed"] and not before["truncated"]
         and len(before["processes"]) == 1, "PROCESS_SCOPE_NOT_EMPTY_BEFORE_LAUNCH")
    try:
        yield
    finally:
        result = {"status": "FAIL", "authority": "process_custody_only_not_revocation"}
        try:
            result = close_own_children(seconds=seconds, grace=grace)
        finally:
            publish(receipt, result)
        need(result["status"] == "PASS", "DESCENDANTS_NOT_CLOSED_NO_FINAL_SNAPSHOT")


@contextmanager
def daemon_scope(host, config, socket, env, log, receipt):
    with owned_process_scope(receipt):
        with host.running_daemon(config, socket, env, log) as daemon:
            yield daemon


def run_scenario(args):
    github = admit_environment(args)
    args.namespace.mkdir(mode=0o700)
    publish(args.namespace / "scenario-intent.json", {
        "schema": SCHEMA, "source_sha": args.source_sha, "semwright_sha": args.semwright_sha,
        "github": github, "automatic_retry": False, "first_use_gate": "UNCHANGED_SEPARATE_GATE",
    })
    result = {"schema": SCHEMA, "status": "FAIL", "claims": limitations(), "github": github,
              "automatic_retry": False, "source_sha": args.source_sha, "semwright_sha": args.semwright_sha}
    sources_before = binaries_before = None
    try:
        sources_before = source_inputs(args)
        publish(args.namespace / "source-before.json", sources_before)
        cohort = sources_before["storage_cohort"]
        profile = args.namespace / "isolated-gui"
        profile.mkdir(mode=0o700)
        with supervise_own_processes():
            result["appimage_extraction"] = extract_installed_bundle(args)
            binaries_before = binary_inputs(args)
            publish(args.namespace / "binaries-before.json", binaries_before)
            first_ui = closed_gui_launch(args, profile, cohort, phase="created")
            database = cold._safe_database(profile / "data")
            identity = database_identity(database)
            initial = cold.read_snapshot(profile / "data", storage_cohort=cohort)
            publish(args.namespace / "store-created.json", {
                "snapshot": asdict(initial), "identity": identity,
                "sqlite": source.regular_file(database, cold.MAX_DB_BYTES),
                "profile": closed_profile_identity(profile / "data")})
            result["native"] = run_native_phase(args, database, initial, daemon_scope=daemon_scope)
            need(database_identity(database) == identity, "HOST_REPLACED_THE_UI_DATABASE")
            changed = cold.read_snapshot(profile / "data", storage_cohort=cohort, expected_title=TITLE)
            compare_committed(initial, changed)
            publish(args.namespace / "store-after-native.json", {
                "snapshot": asdict(changed), "identity": identity,
                "sqlite": source.regular_file(database, cold.MAX_DB_BYTES),
                "profile": closed_profile_identity(profile / "data")})
            reopened_ui = closed_gui_launch(
                args, profile, cohort, phase="reopened", snapshot=changed)
            need(first_ui["binding"] != reopened_ui["binding"], "UI_REOPEN_DID_NOT_CREATE_A_NEW_PROCESS")
            need(database_identity(database) == identity, "REOPEN_REPLACED_THE_UI_DATABASE")
            reopened = cold.read_snapshot(profile / "data", storage_cohort=cohort, expected_title=TITLE)
            need(reopened == changed, "UI_REOPEN_MUTATED_NATIVE_COMMIT")
            publish(args.namespace / "store-reopened.json", {
                "snapshot": asdict(reopened), "identity": identity,
                "sqlite": source.regular_file(database, cold.MAX_DB_BYTES),
                "profile": closed_profile_identity(profile / "data")})
        result.update(status="PASS", ui_created=first_ui, ui_reopened=reopened_ui,
                      same_actual_database=identity, effect_attempts=1,
                      first_use_scenario_modified=False)
        result["claims"].update(
            installed_desktop_shared_state="PASS",
            real_cli_daemon_broker_policy_host_native_service="PASS",
            appimage_extraction_provenance="OBSERVED_ONCE_WITH_CANONICAL_PACKAGE_VERIFICATION",
            observed_binaries="HASHED_BEFORE_AND_AFTER",
        )
    except Exception as error:
        result["failure"] = {"type": type(error).__name__, "detail": str(error)}
    finally:
        try:
            if sources_before is not None:
                after = source_inputs(args)
                publish(args.namespace / "source-after.json", after)
                need(after == sources_before, "SOURCE_CHANGED_DURING_SCENARIO")
            if binaries_before is not None:
                after = binary_inputs(args)
                publish(args.namespace / "binaries-after.json", after)
                need(after == binaries_before, "BINARIES_CHANGED_DURING_SCENARIO")
        except Exception as error:
            result["status"] = "FAIL"
            result["custody_failure"] = {"type": type(error).__name__, "detail": str(error)}
        if result["status"] != "PASS":
            result["claims"]["installed_desktop_shared_state"] = "NOT_RUN" if sources_before is None else "FAIL"
            if "real_cli_daemon_broker_policy_host_native_service" in result["claims"]:
                result["claims"]["real_cli_daemon_broker_policy_host_native_service"] = "FAIL"
        publish(args.namespace / "result.private.json", result)
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--read-ui-worker":
        return read_ui_worker(argv[1:])
    args = parse_args(argv)
    previous = os.umask(0o077)
    try:
        result = run_scenario(args)
    finally:
        os.umask(previous)
    print("installed-native-shared-state=" + result["status"]
          + " original_evidence=PRIVATE no_retry=true")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("installed-native-shared-state=FAIL original_evidence=PRIVATE no_retry=true",
              file=sys.stderr)
        raise SystemExit(1) from None
