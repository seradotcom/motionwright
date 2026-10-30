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
import selectors
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
_CI_CAPACITY = None
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
    "tooling/ci_private_evidence.py", "tooling/mlt_official_runtime_intake.py",
    "tooling/requirements/mlt-g2-custody.txt", "tooling/ci_test_runner.py",
    "tooling/tests/test_native_host_installed_shared_state.py",
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
    if _CI_CAPACITY is not None:
        _CI_CAPACITY.claim(path, len(raw))
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
            extracted_tree=installed_bundle_inventory(extracted),
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
                if _CI_CAPACITY is not None:
                    ci_copy_original(log, retained_path, custody.MAX_FILE)
                else:
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
    if _CI_CAPACITY is not None:
        _CI_CAPACITY.claim(path, len(text.encode("utf-8")))
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
    if _CI_CAPACITY is not None:
        ci_copy_original(args.provider, provider, MAX_INPUT_BYTES)
    else:
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


def run_scenario(args, *, before_native=None):
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
            if before_native is not None:
                before_native()
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
            if result.get("appimage_extraction", {}).get("extracted_tree") is not None:
                after = installed_bundle_inventory(args.namespace / "appimage/squashfs-root")
                publish(args.namespace / "appimage-tree-after.original.json", after)
                need(after == result["appimage_extraction"]["extracted_tree"],
                     "EXTRACTED_PUBLIC_PAYLOAD_CHANGED_UNIQUE_BYTES_REQUIRE_PRIVATE_CUSTODY")
        except Exception as error:
            result["status"] = "FAIL"
            result["custody_failure"] = {"type": type(error).__name__, "detail": str(error)}
        if result["status"] != "PASS":
            result["claims"]["installed_desktop_shared_state"] = "NOT_RUN" if sources_before is None else "FAIL"
            if "real_cli_daemon_broker_policy_host_native_service" in result["claims"]:
                result["claims"]["real_cli_daemon_broker_policy_host_native_service"] = "FAIL"
        publish(args.namespace / "result.private.json", result)
    return result



# Wiring for the existing linux-first-use-installed job. These receipts are
# observations and capacity checks, never independent product approval.
CI_PRIVATE_BYTES = 240 * 1024 * 1024
CI_PRIVATE_FILES = 4094
CI_FUTURE_PRIVATE_BYTES = 64 * 1024 * 1024
CI_FUTURE_PRIVATE_FILES = 1024
CI_JOIN_SECONDS = 1800
CI_TAIL_SECONDS = 360  # sealing, cipher upload and the existing package upload
CI_EXPECTED_CONTROLS = 37
CI_CAPACITY_JOURNAL_BYTES = 1024 * 1024
CI_PRODUCER_CLOSE_BYTES = 1024 * 1024
CI_PRODUCER_CLOSE_FILES = 8
CI_COMMAND_RECORD_BYTES = 65536
CI_COMMAND_CLOSURE_BYTES = 1024 * 1024
CI_PIPE_BYTES = 65536
CI_OVERFLOW_BYTES = 2 * CI_PIPE_BYTES
# These are predeclared suballocations, not observations of future outputs.
# Every build keeps the existing 8 MiB per-stream ceiling. No reservation is
# refunded, including a command that emits less than its declared allowance.
CI_COMMAND_STREAM_BYTES = {
    "seal-venv": 65536, "seal-dependencies": 512 * 1024,
    "recipient-admission": 65536, "sdk-init": 65536,
    "sdk-fetch": 512 * 1024, "sdk-checkout": 65536,
    "product-toolchain": 65536, "sdk-toolchain": 65536,
    "cargo-toolchain": 65536, "sdk-sandbox-profile": 65536,
    "sdk-daemon-cli": 8 * 1024 * 1024, "sdk-sandbox": 8 * 1024 * 1024,
    "product-provider": 8 * 1024 * 1024, "installed-controls": 256 * 1024,
}


class CiOriginalCapacity:
    """One append-only pre-effect allocation for the existing private selection.

    Build targets and the public AppImage are not this private-original payload.
    Their selected copies must be charged here before they are written. The
    existing scenario's whole private remainder is reserved before any producer
    child, and measured again before the scenario and its Native boundary.
    """
    def __init__(self, evidence, namespace, *, resume_sha256=None):
        self.evidence, self.namespace = Path(evidence), Path(namespace)
        need(self.evidence.resolve(strict=True) == self.evidence
             and self.namespace.is_absolute() and ".." not in self.namespace.parts,
             "CI_CAPACITY_EXACT_ROOTS")
        self.maximum_bytes, self.maximum_files = CI_PRIVATE_BYTES, CI_PRIVATE_FILES
        self.reserved_bytes = CI_CAPACITY_JOURNAL_BYTES + CI_PRODUCER_CLOSE_BYTES + CI_FUTURE_PRIVATE_BYTES
        self.reserved_files = 1 + CI_PRODUCER_CLOSE_FILES + CI_FUTURE_PRIVATE_FILES
        need(self.reserved_bytes <= self.maximum_bytes and self.reserved_files <= self.maximum_files,
             "CI_CAPACITY_INITIAL_RESERVES_DO_NOT_FIT")
        self.entries = {}
        self.scope_used = {"scenario": [0, 0], "close": [0, 0]}
        self.journal_bytes = 0
        self.journal = self.evidence / "private-capacity.original.ndjson"
        self.fd = None
        if resume_sha256 is not None:
            self.resume(resume_sha256)
            return
        self.fd = os.open(self.journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        self.event(self.initial_record())

    def initial_record(self):
        return {"event": "original-allocation", "maximum_bytes": self.maximum_bytes,
            "maximum_files": self.maximum_files, "journal_bytes": CI_CAPACITY_JOURNAL_BYTES,
            "scenario_bytes": CI_FUTURE_PRIVATE_BYTES, "scenario_files": CI_FUTURE_PRIVATE_FILES,
            "close_bytes": CI_PRODUCER_CLOSE_BYTES, "close_files": CI_PRODUCER_CLOSE_FILES,
            "source_sha": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"), "refunds": False}

    def resume(self, expected_sha256):
        """Continue the same early allocation once, without refund or new pool."""
        need(type(expected_sha256) is str and SHA256.fullmatch(expected_sha256),
             "CI_EARLY_CAPACITY_COMMITMENT_REQUIRED")
        before = source.regular_file(self.journal, CI_CAPACITY_JOURNAL_BYTES)
        need(before["sha256"] == expected_sha256, "CI_EARLY_CAPACITY_ORIGINAL_CHANGED")
        identity = custody.stamp(custody.plain(self.journal))
        raw = self.journal.read_bytes()
        need(raw.endswith(b"\n") and source.regular_file(self.journal, CI_CAPACITY_JOURNAL_BYTES) == before,
             "CI_EARLY_CAPACITY_READ_CHANGED")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                need(key not in result, "CI_CAPACITY_DUPLICATE_FIELD")
                result[key] = value
            return result
        rows = [json.loads(line, object_pairs_hook=unique) for line in raw.splitlines()]
        need(2 <= len(rows) <= 2 * CI_PRIVATE_FILES + 2 and rows[0] == self.initial_record(),
             "CI_EARLY_CAPACITY_CONTEXT_OR_LIMIT_CHANGED")
        for row in rows[1:-1]:
            kind = row.get("event")
            need(kind in {"reserve-before-write", "claim-before-open"}, "CI_EARLY_CAPACITY_NOT_FIRST_CLOSED_PHASE")
            path = Path(row["path"])
            need(path.is_absolute() and ".." not in path.parts and path != self.journal
                 and (path.is_relative_to(self.evidence) or path.is_relative_to(self.namespace)),
                 "CI_EARLY_CAPACITY_PATH")
            key = str(path)
            if kind == "reserve-before-write":
                bound, scope = row["maximum"], row["scope"]
                need(key not in self.entries and type(bound) is int and bound >= 0
                     and scope in {"producer", "scenario", "close"}, "CI_EARLY_CAPACITY_RESERVATION")
                if scope == "producer":
                    self.reserved_bytes += bound; self.reserved_files += 1
                else:
                    limits = ((CI_FUTURE_PRIVATE_BYTES, CI_FUTURE_PRIVATE_FILES) if scope == "scenario"
                              else (CI_PRODUCER_CLOSE_BYTES, CI_PRODUCER_CLOSE_FILES))
                    used = self.scope_used[scope]
                    used[0] += bound; used[1] += 1
                    need(used[0] <= limits[0] and used[1] <= limits[1], "CI_EARLY_CAPACITY_SCOPE_EXHAUSTED")
                need(self.reserved_bytes <= self.maximum_bytes and self.reserved_files <= self.maximum_files
                     and row["reserved_bytes"] == self.reserved_bytes and row["reserved_files"] == self.reserved_files,
                     "CI_EARLY_CAPACITY_TOTAL_CHANGED")
                self.entries[key] = {"maximum": bound, "scope": scope, "claimed": False, "written": 0}
            else:
                need(key in self.entries, "CI_EARLY_CAPACITY_CLAIM_WITHOUT_RESERVATION")
                item = self.entries[key]
                need(not item["claimed"] and type(row["initial_bytes"]) is int
                     and 0 <= row["initial_bytes"] <= item["maximum"]
                     and row["maximum"] == item["maximum"] and row["no_refund"] is True,
                     "CI_EARLY_CAPACITY_CLAIM_CHANGED")
                item["claimed"] = True
        need(rows[-1] == {"event": "producer-allocation-closed", **self.snapshot()},
             "CI_EARLY_CAPACITY_NOT_CLOSED_OR_REFUNDED")
        for key, item in self.entries.items():
            path = Path(key)
            if item["claimed"]:
                item["written"] = source.regular_file(path, item["maximum"])["bytes"]
            else:
                need(not path.exists() and not path.is_symlink(), "CI_UNCLAIMED_FUTURE_ORIGINAL_ALREADY_EXISTS")
        need(custody.stamp(custody.plain(self.journal)) == identity, "CI_EARLY_CAPACITY_IDENTITY_CHANGED")
        self.journal_bytes = len(raw)
        self.fd = os.open(self.journal, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            need(custody.stamp(os.fstat(self.fd)) == identity, "CI_EARLY_CAPACITY_OPEN_CHANGED")
            self.event({"event": "producer-allocation-resumed", "original_prefix_sha256": expected_sha256,
                        "original_prefix_bytes": len(raw), **self.snapshot()})
        except BaseException:
            os.close(self.fd); self.fd = None
            raise

    def event(self, value):
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        need(self.journal_bytes + len(raw) <= CI_CAPACITY_JOURNAL_BYTES, "CI_CAPACITY_JOURNAL_EXHAUSTED_BEFORE_EFFECT")
        # The complete journal allowance was charged before its first write.
        self.journal_bytes += len(raw)
        view = memoryview(raw)
        while view:
            written = os.write(self.fd, view)
            need(written > 0, "CI_CAPACITY_JOURNAL_INCOMPLETE")
            view = view[written:]
        os.fsync(self.fd)

    def reserve(self, path, maximum, *, separate=False):
        path = Path(path)
        need(path.is_absolute() and ".." not in path.parts and type(maximum) is int and maximum >= 0,
             "CI_CAPACITY_ORIGINAL_PATH_OR_SIZE")
        need(path.is_relative_to(self.evidence) or path.is_relative_to(self.namespace),
             "CI_CAPACITY_ORIGINAL_OUTSIDE_RETAINED_ROOTS")
        key = str(path)
        need(key not in self.entries and path != self.journal and not path.exists() and not path.is_symlink(),
             "CI_CAPACITY_ORIGINAL_REUSED_NO_RETRY")
        close_names = {"producer-terminal.original.json", "seal-failure.original.json"}
        scope = ("scenario" if path.is_relative_to(self.namespace) and not separate else
                 "close" if path.parent == self.evidence and path.name in close_names else "producer")
        if scope == "producer":
            need(self.reserved_bytes + maximum <= self.maximum_bytes
                 and self.reserved_files + 1 <= self.maximum_files, "CI_PRIVATE_CUSTODY_CAPACITY_BEFORE_WRITE")
            self.reserved_bytes += maximum; self.reserved_files += 1
        else:
            limits = ((CI_FUTURE_PRIVATE_BYTES, CI_FUTURE_PRIVATE_FILES) if scope == "scenario"
                      else (CI_PRODUCER_CLOSE_BYTES, CI_PRODUCER_CLOSE_FILES))
            used = self.scope_used[scope]
            need(used[0] + maximum <= limits[0] and used[1] + 1 <= limits[1],
                 "CI_PRIVATE_SCOPE_CAPACITY_BEFORE_WRITE")
            used[0] += maximum; used[1] += 1
        self.entries[key] = {"maximum": maximum, "scope": scope, "claimed": False, "written": 0}
        self.event({"event": "reserve-before-write", "path": key, "maximum": maximum,
            "scope": scope, "reserved_bytes": self.reserved_bytes, "reserved_files": self.reserved_files})

    def claim(self, path, size):
        key = str(Path(path))
        if key not in self.entries:
            self.reserve(path, size)
        item = self.entries[key]
        need(not item["claimed"] and type(size) is int and 0 <= size <= item["maximum"],
             "CI_ORIGINAL_EXCEEDS_PRIOR_RESERVATION")
        item["claimed"] = True
        self.event({"event": "claim-before-open", "path": key, "initial_bytes": size,
                    "maximum": item["maximum"], "no_refund": True})

    def before_chunk(self, path, size):
        item = self.entries[str(Path(path))]
        need(item["claimed"] and type(size) is int and size >= 0
             and item["written"] + size <= item["maximum"], "CI_ORIGINAL_CHUNK_EXCEEDS_PRIOR_RESERVATION")
        item["written"] += size  # no refund even if the following write fails

    def snapshot(self):
        return {"maximum_plain_original_bytes": self.maximum_bytes, "maximum_original_files": self.maximum_files,
            "reserved_total_bytes": self.reserved_bytes, "reserved_total_files": self.reserved_files,
            "journal_bytes_reserved": CI_CAPACITY_JOURNAL_BYTES, "producer_close_bytes_reserved": CI_PRODUCER_CLOSE_BYTES,
            "scenario_bytes_reserved": CI_FUTURE_PRIVATE_BYTES, "scenario_files_reserved": CI_FUTURE_PRIVATE_FILES,
            "prior_allocations": len(self.entries), "refunds": False, "same_original_capacity": True}

    def close(self):
        if self.fd is not None:
            try:
                self.event({"event": "producer-allocation-closed", **self.snapshot()})
            finally:
                os.close(self.fd); self.fd = None


@contextmanager
def ci_capacity_scope(evidence, namespace, *, resume_sha256=None):
    global _CI_CAPACITY
    need(_CI_CAPACITY is None, "CI_CAPACITY_ALREADY_ACTIVE_NO_RESET")
    capacity = CiOriginalCapacity(evidence, namespace, resume_sha256=resume_sha256)
    _CI_CAPACITY = capacity
    try:
        yield capacity
    finally:
        try:
            capacity.close()
        finally:
            _CI_CAPACITY = None


def ci_copy_original(original, destination, maximum, *, expected=None):
    """Charge the complete measured copy before opening its output, then chunks."""
    original, destination = Path(original), Path(destination)
    before = source.regular_file(original, maximum)
    need(expected is None or before == expected, "CI_SOURCE_COPY_DIFFERS")
    if _CI_CAPACITY is not None:
        _CI_CAPACITY.claim(destination, before["bytes"])
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_fd = os.open(original, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        output_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(source_fd, "rb", closefd=False) as inp, os.fdopen(output_fd, "wb") as out:
            copied = 0
            while True:
                block = inp.read(65536)
                if not block:
                    break
                need(copied + len(block) <= before["bytes"], "CI_ORIGINAL_GREW_BEFORE_COPY_WRITE")
                if _CI_CAPACITY is not None:
                    _CI_CAPACITY.before_chunk(destination, len(block))
                out.write(block); copied += len(block)
            out.flush(); os.fsync(out.fileno())
    finally:
        os.close(source_fd)
    need(source.regular_file(original, maximum) == before
         and source.regular_file(destination, maximum) == before, "CI_SOURCE_COPY_CHANGED")
    return before


def installed_bundle_inventory(root):
    """Read every extracted entry without following links; exclude only atime."""
    root = Path(root)
    need(root.is_dir() and root.resolve(strict=True) == root, "PUBLIC_EXTRACTION_ROOT")
    paths = [root, *sorted(root.rglob("*"))]
    need(len(paths) <= 16384, "PUBLIC_EXTRACTION_METADATA_BOUND")
    rows = []
    for path in paths:
        before = path.lstat()
        relative = path.relative_to(root).as_posix()
        need(not any(part in ("..", "") for part in relative.split("/")), "PUBLIC_EXTRACTION_MEMBER")
        row = {"path": relative, "device": before.st_dev, "inode": before.st_ino,
            "mode": before.st_mode, "uid": before.st_uid, "gid": before.st_gid,
            "nlink": before.st_nlink, "bytes": before.st_size,
            "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns}
        if stat.S_ISREG(before.st_mode):
            row.update(kind="file", sha256=source.regular_file(path, MAX_INPUT_BYTES)["sha256"])
        elif stat.S_ISDIR(before.st_mode):
            row.update(kind="directory")
        elif stat.S_ISLNK(before.st_mode):
            row.update(kind="symlink", link_target=os.readlink(path))
        else:
            raise ScenarioError("PUBLIC_EXTRACTION_SPECIAL_FILE")
        need(custody.stamp(path.lstat()) == custody.stamp(before), "PUBLIC_EXTRACTION_CHANGED_DURING_READ")
        rows.append(row)
    need(paths == [root, *sorted(root.rglob("*"))], "PUBLIC_EXTRACTION_INVENTORY_CHANGED")
    need(len(json.dumps(rows, sort_keys=True).encode()) <= 2 * 1024 * 1024,
         "PUBLIC_EXTRACTION_MANIFEST_BOUND")
    return {"root": str(root), "entries": rows,
            "meaning": "EXACT_OBSERVED_PUBLIC_APPIMAGE_EXTRACTION_NOT_PRIVATE_STATE"}


def ci_paths():
    need(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
         and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
         and os.environ.get("GITHUB_JOB") == "linux-first-use-installed", "CI_INSTALLED_ORIGINAL_JOB")
    sha, run = os.environ.get("GITHUB_SHA", ""), os.environ.get("GITHUB_RUN_ID", "")
    need(SHA40.fullmatch(sha) and re.fullmatch(r"[1-9][0-9]*", run), "CI_INSTALLED_SOURCE_RUN")
    repo = Path(os.environ["GITHUB_WORKSPACE"])
    temporary = Path(os.environ["RUNNER_TEMP"])
    need(repo.resolve(strict=True) == repo == ROOT.resolve(strict=True)
         and temporary.resolve(strict=True) == temporary, "CI_INSTALLED_CANONICAL_PATHS")
    suffix = run + "-1-" + sha
    return repo, temporary / ("native-host-installed-producer-" + suffix), temporary / ("native-host-installed-" + suffix)


def ci_capture_job_deadline(evidence):
    """Authenticate the actual existing job start; do not start a new clock."""
    from datetime import datetime
    from email.utils import parsedate_to_datetime
    from urllib.request import build_opener, Request, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ScenarioError("CI_METADATA_REDIRECT")
    token = os.environ.pop("MW_INSTALLED_METADATA_TOKEN", "")
    need(bool(token), "CI_METADATA_READ_CREDENTIAL_REQUIRED")
    repo = "seradotcom/motionwright"
    run_id, sha = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_SHA"]
    records = []
    def get(suffix, name):
        url = "https://api.github.com/repos/" + repo + suffix
        request = Request(url, headers={"Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "motionwright-installed-original-job", "Cache-Control": "no-cache"})
        if _CI_CAPACITY is not None:
            _CI_CAPACITY.reserve(evidence / name, 2 * 1024 * 1024)
        # Date belongs to this response. Anchoring it after the body/readback
        # would add transfer latency to the original job's permitted duration.
        request_started_mono_ns = time.monotonic_ns()
        with build_opener(NoRedirect).open(request, timeout=12) as response:
            raw = response.read(262145)
            need(response.status == 200 and len(raw) <= 262144 and response.headers.get("Age", "0") == "0",
                 "CI_METADATA_ORIGINAL_RESPONSE")
            date = response.headers.get("Date")
            need(date is not None, "CI_METADATA_DATE_REQUIRED")
        record = {"url": url, "status": 200, "date": date, "body": json.loads(raw),
                  "request_started_mono_ns": request_started_mono_ns,
                  "response_completed_mono_ns": time.monotonic_ns(),
                  "raw_body_hex": raw.hex(), "sha256": hashlib.sha256(raw).hexdigest()}
        publish(evidence / name, record); records.append(record)
        return record["body"], parsedate_to_datetime(date).timestamp(), request_started_mono_ns
    run, _, _ = get("/actions/runs/" + run_id, "job-run.original.json")
    need(str(run.get("id")) == run_id and run.get("run_attempt") == 1 and run.get("head_sha") == sha
         and run.get("event") == "workflow_dispatch" and run.get("status") == "in_progress"
         and run.get("repository", {}).get("full_name") == repo
         and run.get("head_repository", {}).get("full_name") == repo
         and run.get("path", "").split("@", 1)[0] == ".github/workflows/package-candidate.yml",
         "CI_METADATA_EXACT_DISPATCH")
    jobs = []
    for page in range(1, 5):
        data, server_now, server_anchor_mono_ns = get(
            "/actions/runs/" + run_id + "/attempts/1/jobs?per_page=100&page=" + str(page),
            "job-list-" + str(page) + ".original.json")
        need(type(data.get("jobs")) is list and len(data["jobs"]) <= 100, "CI_JOB_PAGE")
        jobs.extend(data["jobs"])
        if len(data["jobs"]) < 100:
            break
    else:
        raise ScenarioError("CI_JOB_PAGINATION_BOUND")
    matches = [row for row in jobs if row.get("name") == "linux-first-use-installed"
               and row.get("status") == "in_progress" and str(row.get("run_id")) == run_id
               and row.get("head_sha") == sha]
    need(len(matches) == 1, "CI_EXACT_ORIGINAL_INSTALLED_JOB")
    job = matches[0]
    started = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00")).timestamp()
    # Date has one-second resolution; subtract two seconds conservatively.
    deadline_mono_ns = server_anchor_mono_ns + int((started + 2700 - server_now - 2) * 1e9)
    remaining = (deadline_mono_ns - time.monotonic_ns()) / 1e9
    need(CI_JOIN_SECONDS <= remaining <= 2700, "CI_JOIN_DOES_NOT_FIT_ORIGINAL_45_MINUTE_JOB")
    record = {"source_sha": sha, "run_id": run_id, "run_attempt": "1", "job_id": job["id"],
        "job_seconds": 2700, "job_started_at": job["started_at"], "observed_server_epoch": server_now,
        "deadline_mono_ns": deadline_mono_ns, "server_anchor_mono_ns": server_anchor_mono_ns,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "automatic_retry": False, "join_minimum_remaining_seconds": CI_JOIN_SECONDS,
        "cipher_upload_reserved_seconds": 120, "package_upload_reserved_seconds": 120}
    publish(evidence / "job-deadline.original.json", record)
    return record


def ci_require_time(evidence, seconds):
    value = read_json(evidence / "job-deadline.original.json")
    need(value["source_sha"] == os.environ["GITHUB_SHA"] and value["run_id"] == os.environ["GITHUB_RUN_ID"]
         and value["run_attempt"] == os.environ["GITHUB_RUN_ATTEMPT"] == "1" and value["job_seconds"] == 2700
         and value["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "CI_JOB_DEADLINE_BINDING")
    need(time.monotonic_ns() + int(seconds * 1e9) <= value["deadline_mono_ns"],
         "CI_COMPLETE_NEXT_EFFECT_AND_CUSTODY_DO_NOT_FIT")
    return value


def ci_reserve_command(evidence, label, limit):
    need(_CI_CAPACITY is not None and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", label), "CI_CLOSED_COMMAND_LABEL")
    need(type(limit) is int and 0 < limit <= 8 * 1024 * 1024, "CI_COMMAND_STREAM_SUBALLOCATION")
    paths = {name: evidence / (label + "." + name + ".original")
             for name in ("stdout", "stderr", "stdout-overflow", "stderr-overflow")}
    metadata = {name: evidence / (label + "." + name + ".original.json")
                for name in ("intent", "closure", "terminal")}
    reservations = [(path, CI_OVERFLOW_BYTES if name.endswith("-overflow") else limit) for name, path in paths.items()]
    reservations += [(path, CI_COMMAND_CLOSURE_BYTES if name == "closure" else CI_COMMAND_RECORD_BYTES)
                     for name, path in metadata.items()]
    existing = [str(path) in _CI_CAPACITY.entries for path, _ in reservations]
    need(not any(existing) or all(existing), "CI_PARTIAL_COMMAND_RESERVATION_NO_RETRY")
    for path, bound in reservations:
        if all(existing):
            item = _CI_CAPACITY.entries[str(path)]
            need(not item["claimed"] and item["maximum"] == bound,
                 "CI_COMMAND_RESERVATION_REUSED_OR_CHANGED")
        else:
            _CI_CAPACITY.reserve(path, bound)
    return paths, metadata


def ci_command(evidence, label, argv, cwd, env, seconds, reserve=CI_TAIL_SECONDS, *, stream_bytes=None,
               sidecar_file_limit=None):
    """Retain bounded pipes; a Cargo child never inherits a file-size limit."""
    import fcntl
    need(_CI_CAPACITY is not None and sys.platform == "linux", "CI_CAPACITY_REQUIRED_BEFORE_COMMAND")
    child_limit = None
    if sidecar_file_limit is not None:
        need(label == "sdk-sandbox-profile" and sidecar_file_limit == 65536
             and len(argv) == 3 and argv[0] == "bash"
             and Path(argv[1]).name == "ci-driver-bwrap-profile.sh"
             and argv[2] == str(evidence / "sandbox-profile.original.txt"),
             "CI_SIDECAR_FILE_LIMIT_ONLY_FOR_SDK_PROFILE")
        def child_limit():
            import resource
            prior = resource.getrlimit(resource.RLIMIT_FSIZE)
            maximum = min([sidecar_file_limit, *(n for n in prior if n != resource.RLIM_INFINITY)])
            need(0 < maximum <= 65536, "CI_PROFILE_EXISTING_FILE_LIMIT")
            resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
    ci_require_time(evidence, seconds + reserve + 22)
    limit = CI_COMMAND_STREAM_BYTES.get(label, 8 * 1024 * 1024) if stream_bytes is None else stream_bytes
    # Every slot and maximum is held before the child exists. Nothing is
    # refunded after success, failure, cancellation, short output or failed open.
    paths, metadata = ci_reserve_command(evidence, label, limit)
    intent = {"argv": argv, "cwd": str(cwd), "source_sha": os.environ["GITHUB_SHA"],
              "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": "1",
              "timeout_seconds": seconds, "automatic_retry": False, "stream_limit_bytes": limit,
              "overflow_reserve_per_stream_bytes": CI_OVERFLOW_BYTES, "cargo_file_size_limit_changed": False}
    if sidecar_file_limit is not None:
        intent["sdk_profile_child_file_limit_bytes"] = sidecar_file_limit
    publish(metadata["intent"], intent)
    terminal = {"status": "UNKNOWN", "exit_code": None, "automatic_retry": False,
                "overflow": False, "deadline_exceeded": False, "retention_exhausted": False,
                "complete_cohort_closed": False, "stream_eof": {name: False for name in ("stdout", "stderr")}}
    outputs, readers, writes = {}, selectors.DefaultSelector(), []
    process, primary = None, None
    counts = {name: 0 for name in paths}
    observed = {name: 0 for name in ("stdout", "stderr")}
    def retain(name, data):
        observed[name] += len(data)
        normal = min(len(data), max(0, limit - counts[name]))
        if normal:
            _CI_CAPACITY.before_chunk(paths[name], normal)
            outputs[name].write(data[:normal]); counts[name] += normal
        remainder = data[normal:]
        if remainder:
            terminal["overflow"] = True
            overflow = name + "-overflow"
            if counts[overflow] + len(remainder) > CI_OVERFLOW_BYTES:
                terminal["retention_exhausted"] = True
                return False
            _CI_CAPACITY.before_chunk(paths[overflow], len(remainder))
            outputs[overflow].write(remainder); counts[overflow] += len(remainder)
        return True
    try:
        for name, path in paths.items():
            _CI_CAPACITY.claim(path, 0)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            outputs[name] = os.fdopen(fd, "wb")
        for name in ("stdout", "stderr"):
            read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
            writes.append(write_fd)
            try:
                need(fcntl.fcntl(read_fd, fcntl.F_GETPIPE_SZ) <= CI_PIPE_BYTES, "CI_PIPE_EXCEEDS_RESERVED_OVERFLOW")
                os.set_blocking(read_fd, False)
                readers.register(read_fd, selectors.EVENT_READ, name)
            except BaseException:
                os.close(read_fd)
                raise
        try:
            with owned_process_scope(metadata["closure"]):
                deadline = time.monotonic() + seconds
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                    stdout=writes[0], stderr=writes[1], close_fds=True, preexec_fn=child_limit)
                for fd in writes:
                    os.close(fd)
                writes.clear()
                try:
                    while readers.get_map():
                        if time.monotonic() >= deadline:
                            terminal["deadline_exceeded"] = True
                            break
                        for key, _ in readers.select(min(0.05, max(0, deadline - time.monotonic()))):
                            data = os.read(key.fd, CI_PIPE_BYTES)
                            if not data:
                                terminal["stream_eof"][key.data] = True
                                readers.unregister(key.fd); os.close(key.fd)
                            else:
                                retain(key.data, data)
                            if terminal["overflow"] or terminal["retention_exhausted"]:
                                break
                        if terminal["overflow"] or terminal["retention_exhausted"]:
                            break
                    if not terminal["overflow"] and not terminal["deadline_exceeded"]:
                        try:
                            process.wait(timeout=max(0.001, deadline - time.monotonic()))
                        except subprocess.TimeoutExpired:
                            terminal["deadline_exceeded"] = True
                finally:
                    if process.poll() is None:
                        process.kill()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                    terminal["exit_code"] = process.poll()
            terminal["complete_cohort_closed"] = True
        except BaseException as error:
            primary = error
        # No readers run while the existing scope closes all descendants. At
        # most one already-read chunk plus each measured pipe can remain. Keep
        # those observed bytes in the precharged overflow originals, never
        # truncate or replace an already-written stream.
        for key in list(readers.get_map().values()):
            while True:
                try:
                    data = os.read(key.fd, CI_PIPE_BYTES)
                except BlockingIOError:
                    break
                if not data:
                    terminal["stream_eof"][key.data] = True
                    break
                if not retain(key.data, data):
                    break
        for stream in outputs.values():
            stream.flush(); os.fsync(stream.fileno())
        terminal["streams"] = {name: source.regular_file(path, CI_OVERFLOW_BYTES if name.endswith("-overflow") else limit)
                               for name, path in paths.items()}
        terminal["observed_stream_bytes"] = observed
        terminal["retained_stream_bytes"] = counts
        if primary is not None:
            raise primary
        need(terminal["complete_cohort_closed"] and all(terminal["stream_eof"].values())
             and not terminal["overflow"] and not terminal["retention_exhausted"]
             and not terminal["deadline_exceeded"], "CI_ORIGINAL_STREAM_OR_CLOSURE_INCOMPLETE:" + label)
        need(terminal["exit_code"] == 0, "CI_ORIGINAL_COMMAND_FAILED:" + label)
        terminal["status"] = "EXIT_ZERO_AND_COMPLETE_COHORT_CLOSED"
        return paths["stdout"].read_bytes()
    finally:
        for fd in writes:
            os.close(fd)
        for key in list(readers.get_map().values()):
            os.close(key.fd)
        readers.close()
        for stream in outputs.values():
            try:
                stream.flush(); os.fsync(stream.fileno())
            finally:
                stream.close()
        publish(metadata["terminal"], terminal)


def ci_capture_sources(evidence, label, root, observation):
    destination = evidence / (label + "-source")
    destination.mkdir(mode=0o700)
    for name, expected in observation["files"].items():
        file = root / name
        target = destination / name
        ci_copy_original(file, target, 8 * 1024 * 1024, expected=expected["checkout"])


def ci_capacity_before_scenario(evidence, provider_bytes):
    if _CI_CAPACITY is not None:
        # The scenario remainder was held before the first producer child. Its
        # second provider copy is now known exactly and must fit in addition.
        _CI_CAPACITY.reserve(_CI_CAPACITY.namespace / "host/binary/motionwright-native-provider",
                             provider_bytes, separate=True)
        _CI_CAPACITY.reserve(evidence / "private-capacity-before-scenario.original.json", 65536)
        _CI_CAPACITY.reserve(evidence / "private-capacity-before-native.original.json", 65536)
    files = [path for path in evidence.rglob("*") if path.is_file()]
    actual = sum(source.regular_file(path, 256 * 1024 * 1024)["bytes"] for path in files)
    # The actual provider is copied again by the existing Host. Reserve those
    # measured bytes plus a finite private profile/log/metadata remainder.
    need(actual + provider_bytes + CI_FUTURE_PRIVATE_BYTES <= CI_PRIVATE_BYTES
         and len(files) + CI_FUTURE_PRIVATE_FILES <= CI_PRIVATE_FILES, "CI_PRIVATE_CUSTODY_CAPACITY_BEFORE_EFFECT")
    result = {"observed_producer_bytes": actual, "observed_producer_files": len(files),
        "reserved_provider_copy_bytes": provider_bytes, "reserved_private_bytes": CI_FUTURE_PRIVATE_BYTES,
        "reserved_private_files": CI_FUTURE_PRIVATE_FILES,
        "maximum_plain_original_bytes": CI_PRIVATE_BYTES, "maximum_original_files": CI_PRIVATE_FILES,
        "public_extraction_excluded_only_after_exact_before_after_match": True}
    if _CI_CAPACITY is not None:
        result.update(_CI_CAPACITY.snapshot())
    return result


def ci_capacity_before_native(evidence, namespace, provider_bytes):
    original = read_json(evidence / "private-capacity-before-scenario.original.json")
    files = [path for root in (evidence, namespace) for path in root.rglob("*")
             if path.is_file() and not path.is_relative_to(namespace / "appimage/squashfs-root")]
    observed = sum(source.regular_file(path, 256 * 1024 * 1024)["bytes"] for path in files)
    need(observed + provider_bytes + 32 * 1024 * 1024 <= original["observed_producer_bytes"] + provider_bytes + CI_FUTURE_PRIVATE_BYTES
         and observed + provider_bytes + 32 * 1024 * 1024 <= CI_PRIVATE_BYTES
         and len(files) + 512 <= original["observed_producer_files"] + CI_FUTURE_PRIVATE_FILES,
         "CI_REMAINING_NATIVE_AND_CLOSURE_CAPACITY_BEFORE_EFFECT")
    ci_require_time(evidence, 180 + CI_TAIL_SECONDS)
    publish(evidence / "private-capacity-before-native.original.json", {
        "observed_original_bytes": observed, "observed_files": len(files),
        "provider_copy_bytes": provider_bytes, "remaining_private_bytes": 32 * 1024 * 1024,
        "remaining_private_files": 512, "same_original_capacity": True})


def ci_controls_paths(evidence):
    return {name: evidence / name for name in ("installed-controls.json",
        "installed-controls-00-stdout.log", "installed-controls-00-stderr.log",
        "installed-controls-00-stdout-overflow.log", "installed-controls-00-stderr-overflow.log")}


def ci_verify_controls_report(repo, evidence):
    """Consume the actual early runner originals, never infer a suite from AST."""
    import ci_test_runner as runner
    need(Path(runner.__file__).resolve() == repo / "tooling/ci_test_runner.py", "CI_CONTROL_RUNNER_ORIGIN")
    paths = ci_controls_paths(evidence)
    for name, path in paths.items():
        source.regular_file(path, CI_OVERFLOW_BYTES if "-overflow" in name else 1024 * 1024)
    report = read_json(paths["installed-controls.json"], 1024 * 1024)
    github = {key: os.environ[key] for key in
              ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")}
    definitions = {"tooling/tests/test_native_host_installed_shared_state.py": source.regular_file(
        repo / "tooling/tests/test_native_host_installed_shared_state.py", 4 * 1024 * 1024)["sha256"]}
    need(report.get("schema") == "motionwright.unittest-execution/1" and report.get("status") == "PASS"
         and type(report.get("exit_code")) is int and report["exit_code"] == 0
         and report.get("executed_source_sha") == os.environ["GITHUB_SHA"]
         and type(report.get("tree_sha")) is str and SHA40.fullmatch(report["tree_sha"])
         and report.get("require_complete") is True and "source_postcheck_error" not in report
         and report.get("github") == github and report.get("test_definition_sha256") == definitions
         and report.get("runner_sha256") == source.regular_file(repo / "tooling/ci_test_runner.py", 1024 * 1024)["sha256"]
         and report.get("python_executable_sha256") == source.regular_file(Path(sys.executable).resolve(), MAX_INPUT_BYTES)["sha256"]
         and type(report.get("steps")) is list and len(report["steps"]) == 1,
         "CI_INSTALLED_EARLY_CONTROLS_SOURCE_OR_IDENTITY")
    locks = {name: source.regular_file(repo / name, 16 * 1024 * 1024)["sha256"] for name in
             ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")}
    need(report.get("source_locks") == locks, "CI_INSTALLED_EARLY_CONTROLS_LOCKS")
    step = report["steps"][0]
    expected_command = [sys.executable, "-B", "-E", "-s", "-m", "unittest", "discover",
        "-s", "tooling/tests", "-p", "test_native_host_installed_shared_state.py", "-v"]
    need(step.get("entry_point") == "discovery:test_native_host_installed_shared_state.py"
         and step.get("command") == expected_command and step.get("status") == "PASS"
         and type(step.get("exit_code")) is int and step["exit_code"] == 0, "CI_INSTALLED_EARLY_CONTROL_ENTRYPOINT")
    counts = runner.parse_unittest_summary(paths["installed-controls-00-stderr.log"].read_bytes().decode("utf-8", "replace"))
    need(step.get("counts") == counts and counts["tests_run"] == counts["passed"] == CI_EXPECTED_CONTROLS
         and all(counts[key] == 0 for key in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes")),
         "CI_INSTALLED_ALL_CONTROLS_WITH_REAL_ORPHAN_REQUIRED")
    capture = step.get("bounded_capture", {})
    need(capture.get("status") == "COMPLETE" and capture.get("stream_limit_bytes") == 1024 * 1024
         and capture.get("overflow_limit_per_stream_bytes") == CI_OVERFLOW_BYTES
         and capture.get("eof") == {"stdout": True, "stderr": True}
         and all(capture.get(key) is False for key in
                 ("overflow", "retention_exhausted", "deadline_exceeded", "child_file_size_limit_changed")),
         "CI_INSTALLED_CONTROLS_CAPTURE_INCOMPLETE")
    for name in ("stdout", "stderr", "stdout-overflow", "stderr-overflow"):
        path = paths["installed-controls-00-" + name + ".log"]
        actual = source.regular_file(path, CI_OVERFLOW_BYTES if name.endswith("-overflow") else 1024 * 1024)
        descriptor = {"file": path.name, "sha256": actual["sha256"]}
        held = capture.get("overflow_originals", {}).get(name) if name.endswith("-overflow") else step.get(name)
        need(held == descriptor and capture.get("retained_stream_bytes", {}).get(name) == actual["bytes"],
             "CI_INSTALLED_EARLY_CONTROL_STREAM_CHANGED")
        if name.endswith("-overflow"):
            need(actual["bytes"] == 0, "CI_INSTALLED_EARLY_CONTROL_OVERFLOW")
        else:
            need(capture.get("observed_stream_bytes", {}).get(name) == actual["bytes"],
                 "CI_INSTALLED_EARLY_CONTROL_OBSERVATION_CHANGED")
    return report


def ci_private_child_environment():
    result = {key: value for key, value in os.environ.items() if key not in {
        "GITHUB_TOKEN", "GH_TOKEN", "MW_INSTALLED_METADATA_TOKEN", "ACTIONS_RUNTIME_TOKEN"}}
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "MW_INSTALLED_METADATA_TOKEN", "ACTIONS_RUNTIME_TOKEN"):
        os.environ.pop(key, None)
    result.update(CARGO_INCREMENTAL="0", CARGO_BUILD_JOBS="1", CARGO_PROFILE_DEV_DEBUG="0",
                  CARGO_PROFILE_TEST_DEBUG="0", PYTHONDONTWRITEBYTECODE="1")
    return result


def ci_prepare_controls():
    """Open the one pool and run controls while the checkout is still pristine."""
    repo, root, namespace = ci_paths()
    need(Path(sys.executable).resolve() == Path("/usr/bin/python3").resolve(), "SYSTEM_PYTHON_WITH_GI_REQUIRED")
    need(not root.exists() and not root.is_symlink() and not namespace.exists(), "CI_ORIGINAL_ATTEMPT_ALREADY_EXISTS")
    root.mkdir(mode=0o700); evidence = root / "evidence"; evidence.mkdir(mode=0o700)
    with ci_capacity_scope(evidence, namespace):
        for label, bound in CI_COMMAND_STREAM_BYTES.items():
            ci_reserve_command(evidence, label, bound)
        for name in ("early-controls-terminal.original.json", "early-controls-binding.original.json",
                     "early-controls-consumed.original.json"):
            _CI_CAPACITY.reserve(evidence / name, 65536)
        for name, path in ci_controls_paths(evidence).items():
            _CI_CAPACITY.reserve(path, CI_OVERFLOW_BYTES if "-overflow" in name else 1024 * 1024)
        terminal = {"status": "UNKNOWN", "native_effects": 0, "automatic_retry": False}
        code = 1
        try:
            ci_capture_job_deadline(evidence)
            recipient = {"spki_der_base64": os.environ["PRIVATE_EVIDENCE_RECIPIENT_SPKI"],
                         "sha256": os.environ["PRIVATE_EVIDENCE_RECIPIENT_SHA256"]}
            publish(evidence / "recipient.original.json", recipient)
            env = ci_private_child_environment()
            with supervise_own_processes():
                venv = root / "seal-venv"
                ci_command(evidence, "seal-venv", [sys.executable, "-m", "venv", "--system-site-packages", str(venv)], repo, env, 30, 1530)
                seal_python = str(venv / "bin/python")
                ci_command(evidence, "seal-dependencies", [seal_python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                    "--only-binary=:all:", "--require-hashes", "-r", str(repo / "tooling/requirements/mlt-g2-custody.txt")], repo, env, 90, 1440)
                ci_command(evidence, "recipient-admission", [seal_python, "-B", "-E", "-s", str(Path(__file__).resolve()), "--ci-recipient"], repo, env, 10, 1400)
                for name, path in ci_controls_paths(evidence).items():
                    _CI_CAPACITY.claim(path, 0)
                ci_command(evidence, "installed-controls", [sys.executable, "-B", "-E", "-s", str(repo / "tooling/ci_test_runner.py"),
                    "--repo", str(repo), "--pattern", "test_native_host_installed_shared_state.py", "--mode", "discovery", "--require-complete",
                    "--timeout", "45", "--bounded-installed-capture", "--expected-source", os.environ["GITHUB_SHA"],
                    "--report", str(evidence / "installed-controls.json")], repo, env, 60, CI_JOIN_SECONDS)
                controls = ci_verify_controls_report(repo, evidence)
                terminal.update(status="COMPLETE_SOURCE_CONTROLS_ONLY", tests_run=CI_EXPECTED_CONTROLS,
                                source_sha=controls["executed_source_sha"], tree_sha=controls["tree_sha"])
                code = 0
        except Exception as error:
            terminal["failure"] = {"type": type(error).__name__, "detail": str(error)}
        finally:
            publish(evidence / "early-controls-terminal.original.json", terminal)
        if code == 0:
            originals = {}
            for name, item in sorted(_CI_CAPACITY.entries.items()):
                if item["claimed"]:
                    path = Path(name)
                    originals[path.relative_to(evidence).as_posix()] = {
                        **source.regular_file(path, item["maximum"]), "identity": list(custody.stamp(custody.plain(path)))}
            publish(evidence / "early-controls-binding.original.json", {
                "github": controls["github"], "source_sha": controls["executed_source_sha"],
                "tree_sha": controls["tree_sha"], "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "originals": originals, "native_effects": 0, "automatic_retry": False})
    if code == 0:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write("controls_binding_sha256=" + source.regular_file(evidence / "early-controls-binding.original.json", 65536)["sha256"] + "\n")
            output.write("capacity_journal_sha256=" + source.regular_file(evidence / "private-capacity.original.ndjson", CI_CAPACITY_JOURNAL_BYTES)["sha256"] + "\n")
    return code


def ci_verify_early_controls(repo, evidence, expected_sha256):
    binding_path = evidence / "early-controls-binding.original.json"
    need(type(expected_sha256) is str and SHA256.fullmatch(expected_sha256)
         and source.regular_file(binding_path, 65536)["sha256"] == expected_sha256,
         "CI_EARLY_CONTROLS_BINDING_CHANGED")
    binding = read_json(binding_path, 65536)
    github = {key: os.environ[key] for key in
              ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")}
    need(binding.get("github") == github and binding.get("source_sha") == os.environ["GITHUB_SHA"]
         and binding.get("boot_id") == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
         and binding.get("native_effects") == 0 and binding.get("automatic_retry") is False,
         "CI_EARLY_CONTROLS_CONTEXT_CHANGED")
    originals = binding.get("originals")
    need(type(originals) is dict and set(ci_controls_paths(evidence)) <= set(originals)
         and 5 <= len(originals) <= CI_PRIVATE_FILES, "CI_EARLY_CONTROLS_ORIGINAL_ROSTER")
    for relative, held in originals.items():
        path = evidence / relative
        need(type(relative) is str and not Path(relative).is_absolute() and ".." not in Path(relative).parts
             and type(held.get("bytes")) is int and 0 <= held["bytes"] <= CI_PRIVATE_BYTES,
             "CI_EARLY_CONTROLS_ORIGINAL_PATH")
        need(source.regular_file(path, held["bytes"]) == {key: held[key] for key in ("bytes", "sha256")}
             and list(custody.stamp(custody.plain(path))) == held["identity"], "CI_EARLY_CONTROL_ORIGINAL_CHANGED")
    controls = ci_verify_controls_report(repo, evidence)
    current = source.source_observation(repo, os.environ["GITHUB_SHA"], SOURCE_PATHS)
    need(current["tree"] == controls["tree_sha"] == binding["tree_sha"], "CI_EARLY_CONTROLS_FINAL_SOURCE_CHANGED")
    return binding


def ci_prepare_and_run():
    repo, root, namespace = ci_paths()
    need(Path(sys.executable).resolve() == Path("/usr/bin/python3").resolve(), "SYSTEM_PYTHON_WITH_GI_REQUIRED")
    need(root.is_dir() and root.resolve(strict=True) == root and not namespace.exists(), "CI_EARLY_CONTROL_ROOT_REQUIRED")
    evidence = root / "evidence"
    binding_sha = os.environ.get("MW_INSTALLED_CONTROLS_BINDING_SHA256")
    ci_verify_early_controls(repo, evidence, binding_sha)
    ci_require_time(evidence, CI_JOIN_SECONDS)
    journal_sha = os.environ.get("MW_INSTALLED_CAPACITY_JOURNAL_SHA256")
    with ci_capacity_scope(evidence, namespace, resume_sha256=journal_sha):
        publish(evidence / "early-controls-consumed.original.json", {
            "controls_binding_sha256": binding_sha, "capacity_original_prefix_sha256": journal_sha,
            "source_sha": os.environ["GITHUB_SHA"], "run_id": os.environ["GITHUB_RUN_ID"],
            "run_attempt": "1", "automatic_retry": False})
        return ci_produce_originals(repo, root, namespace, evidence)


def ci_produce_originals(repo, root, namespace, evidence):
    need(_CI_CAPACITY is not None, "CI_ORIGINAL_CAPACITY_NOT_ADMITTED")
    terminal = {"status": "UNKNOWN", "automatic_retry": False, "native_scenario_started": False}
    try:
        # The early controls opened this same pool and fixed the actual job
        # deadline. Reuse its remaining allocations, never create a new clock.
        ci_require_time(evidence, CI_JOIN_SECONDS)
        lock = read_json(repo / "SOURCE_LOCK.json")["dependencies"]["semwright"]
        need(lock["repository"] == "https://github.com/seradotcom/semwright"
             and SHA40.fullmatch(lock["revision"]), "CI_LOCKED_SDK_ORIGIN")
        env = ci_private_child_environment()
        with supervise_own_processes():
            sdk = root / "sdk"; sdk.mkdir(mode=0o700)
            for label, argv, seconds in (
                ("sdk-init", ["git", "init", str(sdk)], 15),
                ("sdk-fetch", ["git", "-C", str(sdk), "fetch", "--no-tags", "--depth", "1", lock["repository"], lock["revision"]], 90),
                ("sdk-checkout", ["git", "-C", str(sdk), "checkout", "--detach", "FETCH_HEAD"], 15)):
                ci_command(evidence, label, argv, repo, env, seconds, 1270)
            sdk_names = tuple(sorted(set((*SDK_PATHS, "scripts/dev/ci-driver-bwrap-profile.sh"))))
            own = source.source_observation(repo, os.environ["GITHUB_SHA"], SOURCE_PATHS)
            sdk_before = source.source_observation(sdk, lock["revision"], sdk_names)
            publish(evidence / "product-source-before.original.json", own)
            publish(evidence / "sdk-source-before.original.json", sdk_before)
            ci_capture_sources(evidence, "product", repo, own); ci_capture_sources(evidence, "sdk", sdk, sdk_before)
            for label, cwd in (("product-toolchain", repo), ("sdk-toolchain", sdk)):
                raw = ci_command(evidence, label, ["rustc", "-vV"], cwd, env, 15, 1270)
                need(raw.startswith(b"rustc 1.98.1 "), "CI_ACTUAL_TOOLCHAIN_VERSION")
            ci_command(evidence, "cargo-toolchain", ["cargo", "-vV"], repo, env, 15, 1270)
            profile_original = evidence / "sandbox-profile.original.txt"
            _CI_CAPACITY.reserve(profile_original, 65536); _CI_CAPACITY.claim(profile_original, 0)
            ci_command(evidence, "sdk-sandbox-profile", ["bash", str(sdk / "scripts/dev/ci-driver-bwrap-profile.sh"), str(profile_original)],
                       repo, env, 30, 1240, sidecar_file_limit=65536)
            need(source.regular_file(profile_original, 65536)["bytes"] < 65536, "CI_PROFILE_SIDECAR_LIMIT_REACHED")
            binaries = {}
            groups = (("sdk-daemon-cli", sdk, ["-p", "semwright-daemon", "-p", "semwright-cli", "--bins"], ("semwright", "semwrightd"), 480),
                      ("sdk-sandbox", sdk, ["-p", "semwright-plugin-host", "--bin", "semwright-sandbox"], ("semwright-sandbox",), 120),
                      ("product-provider", repo, ["-p", "motionwright-native", "--bin", "motionwright-native-provider"], ("motionwright-native-provider",), 300))
            for group_index, (label, cwd, selected, roles, seconds) in enumerate(groups):
                target = sdk / "target" if cwd == sdk else root / "target"
                build_env = dict(env, CARGO_TARGET_DIR=str(target))
                reserve = sum(row[4] for row in groups[group_index + 1:]) + 60 + 360 + CI_TAIL_SECONDS
                raw = ci_command(evidence, label, ["cargo", "build", "--locked", "--jobs", "1", "--message-format=json",
                    "--config", "profile.dev.debug=0", *selected], cwd, build_env, seconds, reserve)
                messages = [json.loads(line) for line in raw.splitlines() if line]
                need(messages[-1].get("reason") == "build-finished" and messages[-1].get("success") is True, "CI_CARGO_BUILD_TERMINAL")
                for role in roles:
                    matches = [row for row in messages if row.get("reason") == "compiler-artifact"
                               and row.get("target", {}).get("name") == role and row.get("executable")]
                    need(len(matches) == 1 and matches[0].get("fresh") is False
                         and matches[0]["target"]["kind"] == ["bin"]
                         and Path(matches[0]["target"]["src_path"]).is_relative_to(cwd), "CI_ACTUAL_FRESH_EXECUTABLE:" + role)
                    binary = Path(matches[0]["executable"])
                    need(binary == target / "debug" / role and binary.resolve(strict=True) == binary, "CI_EXECUTABLE_LAYOUT")
                    identity = source.regular_file(binary, 256 * 1024 * 1024)
                    copied = evidence / (role + ".binary.original")
                    ci_copy_original(binary, copied, 256 * 1024 * 1024, expected=identity)
                    target_source = Path(matches[0]["target"]["src_path"])
                    original_source = source.regular_file(target_source, 8 * 1024 * 1024)
                    retained_source = evidence / (role + ".source.original")
                    ci_copy_original(target_source, retained_source, 8 * 1024 * 1024, expected=original_source)
                    binaries[role] = {"path": str(binary), **identity, "cargo_artifact": matches[0], "producer": label,
                                      "target_source": {"path": str(target_source), **original_source}}
            after = source.source_observation(repo, os.environ["GITHUB_SHA"], SOURCE_PATHS)
            sdk_after = source.source_observation(sdk, lock["revision"], sdk_names)
            publish(evidence / "product-source-after.original.json", after); publish(evidence / "sdk-source-after.original.json", sdk_after)
            need(own == after and sdk_before == sdk_after, "CI_BUILD_SOURCE_CHANGED")
            publish(evidence / "four-build-originals.json", binaries)
            ci_verify_early_controls(repo, evidence, os.environ.get("MW_INSTALLED_CONTROLS_BINDING_SHA256"))
            artifacts = list((repo / "target/release/bundle/appimage").glob("*.AppImage"))
            need(len(artifacts) == 1, "CI_ONE_CURRENT_APPIMAGE")
            provider = binaries["motionwright-native-provider"]
            args = parse_args(["--repo", str(repo), "--source-sha", os.environ["GITHUB_SHA"], "--semwright-sha", lock["revision"],
                "--semwright-checkout", str(sdk), "--artifact", str(artifacts[0]), "--candidate-receipt", str(repo / "ci-evidence/linux-candidate.json"),
                "--provider", provider["path"], "--provider-sha256", provider["sha256"], "--provider-kind", "source-built", "--namespace", str(namespace), "--seconds", "24"])
            args.artifact.chmod(args.artifact.stat().st_mode | stat.S_IXUSR)
            publish(evidence / "private-capacity-before-scenario.original.json", ci_capacity_before_scenario(evidence, provider["bytes"]))
            ci_require_time(evidence, 360 + CI_TAIL_SECONDS + 60)
            terminal["native_scenario_started"] = True
            # The existing scenario owns the real UI/Host effects and descendant
            # closure. A single outer timer leaves the same original tail.
            previous = signal.getsignal(signal.SIGALRM)
            def expired(*_):
                raise ScenarioError("CI_INSTALLED_SCENARIO_DEADLINE_NO_RETRY")
            signal.signal(signal.SIGALRM, expired); signal.setitimer(signal.ITIMER_REAL, 360)
            try:
                observed = run_scenario(args, before_native=lambda: ci_capacity_before_native(evidence, namespace, provider["bytes"]))
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGALRM, previous)
            terminal["scenario_result"] = {"path": str(namespace / "result.private.json"), **source.regular_file(namespace / "result.private.json", 4 * 1024 * 1024)}
            need(observed["status"] == "PASS", "CI_INSTALLED_SCENARIO_NOT_CLOSED")
            terminal["status"] = "OBSERVED_SEQUENTIAL_INSTALLED_NATIVE_ONLY"
    except Exception as error:
        terminal["failure"] = {"type": type(error).__name__, "detail": str(error)}
    finally:
        publish(evidence / "producer-terminal.original.json", terminal)
    print("installed-native-producer=" + terminal["status"] + " originals=PRIVATE automatic_retry=false")
    return 0 if terminal["status"] == "OBSERVED_SEQUENTIAL_INSTALLED_NATIVE_ONLY" else 1


def ci_public_extraction_proof(namespace):
    before = read_json(namespace / "appimage-extraction-terminal.json")
    after = read_json(namespace / "appimage-tree-after.original.json")
    current = installed_bundle_inventory(namespace / "appimage/squashfs-root")
    need(before["status"] == "PASS" and before["extracted_tree"] == after == current,
         "CI_PUBLIC_EXTRACTION_HAS_UNARCHIVED_UNIQUE_BYTES")
    intent = read_json(namespace / "appimage-extraction-intent.json")
    artifact = Path(intent["command"][0])
    need(intent["command"] == [str(artifact), "--appimage-extract"]
         and source.regular_file(artifact, MAX_INPUT_BYTES) == intent["artifact"], "CI_PUBLIC_APPIMAGE_CHANGED")
    return {"artifact": {"path": str(artifact), **intent["artifact"]}, "extraction": current,
            "required_same_run_package_upload_and_exact_readback": True, "private_bytes_omitted": False}


def ci_stage_private(evidence, namespace, stage):
    """Copy actual private bytes once; retain all entry identities separately.

    Public extracted payload may be referenced only after exhaustive equality.
    Failure preserves readable originals but never claims complete custody.
    """
    stage.mkdir(mode=0o700)
    records, failures, total, count = [], [], 0, 0
    roots = {"producer": evidence, "scenario": namespace}
    public = None
    if namespace.is_dir() and (namespace / "appimage/squashfs-root").is_dir():
        try:
            public = ci_public_extraction_proof(namespace)
        except Exception as error:
            failures.append({"scope": "public-extraction", "type": type(error).__name__, "detail": str(error)})
    runtime = namespace / "host/runtime-custody.json"
    if runtime.is_file():
        observed = read_json(runtime)
        actual = Path(observed["path"])
        need(actual.parent == Path("/tmp") and re.fullmatch(r"mwi-[a-zA-Z0-9_-]+", actual.name)
             and actual.resolve(strict=True) == actual and actual.is_dir()
             and actual.stat().st_uid == observed["owner_uid"] == os.getuid() and observed["retained"] is True,
             "CI_EXACT_SHORT_RUNTIME_CUSTODY")
        roots["runtime"] = actual
    for label, root in roots.items():
        if not root.exists():
            failures.append({"scope": label, "error": "ORIGINAL_ROOT_ABSENT"}); continue
        need(root.resolve(strict=True) == root and not root.is_symlink(), "CI_PRIVATE_ROOT_ALIAS")
        entries = [root, *sorted(root.rglob("*"))]
        need(len(entries) <= 16384, "CI_PRIVATE_NAMESPACE_COUNT")
        # If the public equivalence proof failed, retain private state first.
        # The uncertain extracted bytes remain originals and are then captured
        # within the same bound; any omission is explicitly incomplete custody.
        ordered = sorted(entries, key=lambda path: (path.is_relative_to(namespace / "appimage/squashfs-root"), str(path)))
        for path in ordered:
            if public is not None and (path == namespace / "appimage/squashfs-root"
                                      or path.is_relative_to(namespace / "appimage/squashfs-root")):
                continue
            before = path.lstat()
            record = {"root": label, "path": str(path), "relative": path.relative_to(root).as_posix(),
                "device": before.st_dev, "inode": before.st_ino, "mode": before.st_mode,
                "uid": before.st_uid, "gid": before.st_gid, "nlink": before.st_nlink,
                "bytes": before.st_size, "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns}
            try:
                if stat.S_ISREG(before.st_mode):
                    need(count < CI_PRIVATE_FILES and total + before.st_size <= CI_PRIVATE_BYTES,
                         "CI_PRIVATE_ORIGINAL_CAPACITY_EXHAUSTED")
                    identity = source.regular_file(path, 256 * 1024 * 1024)
                    need(custody.stamp(path.lstat()) == custody.stamp(before)
                         and total + identity["bytes"] <= CI_PRIVATE_BYTES, "CI_PRIVATE_ORIGINAL_CHANGED_BEFORE_COPY")
                    name = ("%04d" % count) + ".original"
                    destination = stage / name
                    # Consume the original slot and full measured allowance
                    # before writing. A failed copy keeps its name and bytes;
                    # independent later originals do not collide or get reset.
                    total += identity["bytes"]; count += 1
                    record.update(kind="file", retained_file=name, **identity)
                    with path.open("rb") as inp, destination.open("xb") as out:
                        copied_bytes = 0
                        while True:
                            block = inp.read(65536)
                            if not block:
                                break
                            need(copied_bytes + len(block) <= identity["bytes"], "CI_PRIVATE_ORIGINAL_GREW_BEFORE_WRITE")
                            out.write(block); copied_bytes += len(block)
                        out.flush(); os.fsync(out.fileno())
                    need(source.regular_file(destination, 256 * 1024 * 1024) == identity
                         and source.regular_file(path, 256 * 1024 * 1024) == identity, "CI_PRIVATE_ORIGINAL_CHANGED")
                elif stat.S_ISDIR(before.st_mode):
                    record["kind"] = "directory"
                else:
                    record.update(kind="metadata-only-nonregular", followed_or_opened=False,
                        link_target=os.readlink(path) if stat.S_ISLNK(before.st_mode) else None)
                need(custody.stamp(path.lstat()) == custody.stamp(before), "CI_PRIVATE_ENTRY_CHANGED")
            except Exception as error:
                record.update(status="CAPTURE_FAILED", error_type=type(error).__name__, detail=str(error))
                failures.append({"path": str(path), "type": type(error).__name__, "detail": str(error)})
            records.append(record)
        need(entries == [root, *sorted(root.rglob("*"))], "CI_PRIVATE_INVENTORY_CHANGED")
    allocation = evidence / "private-capacity-before-scenario.original.json"
    if allocation.is_file():
        original = read_json(allocation)
        allocated_bytes = original.get("reserved_total_bytes", original["observed_producer_bytes"]
            + original["reserved_provider_copy_bytes"] + original["reserved_private_bytes"])
        allocated_files = original.get("reserved_total_files", original["observed_producer_files"] + original["reserved_private_files"])
        if (total > allocated_bytes or count > allocated_files
                or allocated_bytes > CI_PRIVATE_BYTES or allocated_files > CI_PRIVATE_FILES):
            failures.append({"scope": "original-private-allocation", "error": "RESERVED_PRIVATE_CAPACITY_EXCEEDED_NO_ACCEPTANCE"})
        # Unused producer reservations are never reassigned to runtime state.
        # The original 64 MiB/1024 case remainder and measured provider copy
        # remain separate obligations within the same whole-payload ceiling.
        private_rows = [row for row in records if row.get("kind") == "file" and row["root"] != "producer"]
        if (sum(row["bytes"] for row in private_rows) > original["reserved_provider_copy_bytes"] + original["reserved_private_bytes"]
                or len(private_rows) > original["reserved_private_files"]):
            failures.append({"scope": "original-scenario-allocation", "error": "RESERVED_SCENARIO_CAPACITY_EXCEEDED_NO_ACCEPTANCE"})
    manifest = {"original_entries": records, "original_roots": {key: str(value) for key, value in roots.items()},
        "public_duplicate_extraction": public, "failures": failures, "complete": not failures,
        "originals_retained_in_place": True, "automatic_retry": False, "reserved_bytes": total, "reserved_files": count,
        "captured_bytes": sum(row["bytes"] for row in records if row.get("kind") == "file" and row.get("status") != "CAPTURE_FAILED"),
        "captured_files": sum(row.get("kind") == "file" and row.get("status") != "CAPTURE_FAILED" for row in records)}
    need(len(json.dumps(manifest, sort_keys=True).encode()) <= 4 * 1024 * 1024, "CI_PRIVATE_IDENTITY_MANIFEST_BOUND")
    publish(stage / "original-entry-identities.json", manifest)
    return manifest


def ci_seal_private():
    repo, root, namespace = ci_paths(); evidence = root / "evidence"
    output = root / "transport"; output.mkdir(mode=0o700)
    terminal = {"status": "HOLD", "complete_private_custody": False, "automatic_retry": False,
                "source_sha": os.environ["GITHUB_SHA"], "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": "1"}
    try:
        ci_require_time(evidence, CI_TAIL_SECONDS)
        import ci_private_evidence as seal
        need(Path(seal.__file__).resolve() == repo / "tooling/ci_private_evidence.py", "CI_EXISTING_SEALER_ORIGIN")
        recipient = read_json(evidence / "recipient.original.json"); seal.public_key(recipient)
        try:
            manifest = ci_stage_private(evidence, namespace, root / "seal-input")
        except Exception as error:
            stage = root / "seal-input"
            need(stage.is_dir() and stage.resolve(strict=True) == stage, "CI_NO_CAPTURE_TO_SEAL")
            publish(stage / "partial-capture-failure.original.json", {"type": type(error).__name__, "detail": str(error),
                "complete": False, "originals_retained_in_place": True, "automatic_retry": False})
            manifest = {"complete": False, "captured_bytes": None, "captured_files": None,
                        "failures": [{"type": type(error).__name__}]}
        raw, captured = seal.gather_snapshot({"owner": root / "seal-input"}, preserve_partial=True)
        lock = read_json(repo / "SOURCE_LOCK.json")["dependencies"]["semwright"]["revision"]
        encrypted = seal.encrypt_snapshot(raw, recipient, {"source_sha": os.environ["GITHUB_SHA"],
            "semwright_sha": lock, "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": "1"})
        need(len(encrypted) <= seal.MAX_FILE, "CI_EXISTING_CIPHERTEXT_FILE_BOUND")
        header = ci_envelope_header(encrypted)
        destination = output / "installed-private.mwseal"
        seal.new_bytes(destination, encrypted)
        terminal.update(status="SEALED", complete_private_custody=manifest["complete"] and captured,
            cipher=source.regular_file(destination, seal.MAX_FILE),
            recipient_spki_sha256=recipient["sha256"], plaintext_sha256=header["plaintext_sha256"],
            captured_bytes=manifest["captured_bytes"], captured_files=manifest["captured_files"],
            custody_problem_count=len(manifest["failures"]), product_acceptance=False)
    except Exception as error:
        # Private diagnostics stay beside original data. Public upload receives
        # only this bounded type/code, never a traceback containing private data.
        publish(evidence / "seal-failure.original.json", {"type": type(error).__name__, "detail": str(error)})
        terminal["failure_type"] = type(error).__name__
    publish(output / "custody-public.json", terminal)
    print("installed-private-custody=" + terminal["status"])
    return 0 if terminal["status"] == "SEALED" and terminal["complete_private_custody"] else 1


def ci_envelope_header(raw):
    """Inspect the existing sealed format; only the external key can decrypt."""
    import base64
    magic = b"MWCISEAL1\n"
    need(type(raw) is bytes and raw.startswith(magic) and len(raw) <= 256 * 1024 * 1024
         and len(raw) > len(magic) + 4, "CI_UPLOAD_REQUIRES_EXISTING_SEALED_FORMAT")
    width = int.from_bytes(raw[len(magic):len(magic) + 4], "big")
    need(0 < width <= 8192 and len(raw) > len(magic) + 4 + width, "CI_UPLOAD_SEAL_HEADER_BOUND")
    encoded = raw[len(magic) + 4:len(magic) + 4 + width]
    header = json.loads(encoded)
    need(set(header) == {"schema", "algorithm", "recipient_spki_sha256", "context", "nonce", "wrapped_key", "plaintext_sha256", "plaintext_bytes"}
         and header["schema"] == "motionwright.ci.private-evidence-seal/1"
         and header["algorithm"] == "RSA-OAEP-SHA256+AES-256-GCM"
         and len(base64.b64decode(header["nonce"], validate=True)) == 12
         and len(base64.b64decode(header["wrapped_key"], validate=True)) == 384
         and SHA256.fullmatch(header["recipient_spki_sha256"]) and SHA256.fullmatch(header["plaintext_sha256"])
         and type(header["plaintext_bytes"]) is int and header["plaintext_bytes"] > 0
         and len(raw) - len(magic) - 4 - width == header["plaintext_bytes"] + 16,
         "CI_UPLOAD_SEAL_CONTRACT")
    need(encoded == (json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode(),
         "CI_UPLOAD_SEAL_CANONICAL_HEADER")
    return header


def ci_entry(argv):
    if argv == ["--ci-controls"]:
        return ci_prepare_controls()
    if argv == ["--ci-run"]:
        return ci_prepare_and_run()
    repo, root, _ = ci_paths()
    if argv == ["--ci-recipient"]:
        import ci_private_evidence as seal
        need(Path(seal.__file__).resolve() == repo / "tooling/ci_private_evidence.py", "CI_EXISTING_SEALER_ORIGIN")
        seal.public_key(read_json(root / "evidence/recipient.original.json"))
        print("recipient=VALIDATED_PUBLIC_KEY_ONLY"); return 0
    if argv == ["--ci-seal"]:
        return ci_seal_private()
    if argv == ["--ci-upload"]:
        ci_require_time(root / "evidence", 240)
        public = read_json(root / "transport/custody-public.json")
        need(public["status"] == "SEALED" and source.regular_file(root / "transport/installed-private.mwseal", 256 * 1024 * 1024) == public["cipher"],
             "CI_UPLOAD_ONLY_VERIFIED_CIPHERTEXT")
        header = ci_envelope_header((root / "transport/installed-private.mwseal").read_bytes())
        lock = read_json(repo / "SOURCE_LOCK.json")["dependencies"]["semwright"]["revision"]
        need(header["context"] == {"source_sha": os.environ["GITHUB_SHA"], "semwright_sha": lock,
            "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": "1"}
            and header["recipient_spki_sha256"] == read_json(root / "evidence/recipient.original.json")["sha256"]
            and header["recipient_spki_sha256"] == public["recipient_spki_sha256"]
            and header["plaintext_sha256"] == public["plaintext_sha256"], "CI_UPLOAD_EXACT_SEALED_CONTEXT")
        need(set(path.name for path in (root / "transport").iterdir()) == {"installed-private.mwseal", "custody-public.json"}, "CI_CLOSED_PUBLIC_UPLOAD_SET")
        print("ready=true"); return 0
    raise ScenarioError("CI_UNKNOWN_WIRING_MODE")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].startswith("--ci-"):
        return ci_entry(argv)
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
