"""Single-attempt native Kdenlive import, edit, save and edited-project reopen.

Selected only on an admitted disposable Linux CI run. The existing Rust/OTIO
fixture, live-process witness, native project readback and bounded cut preview
remain the sole respective producers and inspectors. All raw evidence is
sealed through ci_private_evidence; only a closed status projection is public.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
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
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))

custody = None  # Loaded only by the explicitly selected native CLI.
import kdenlive_complete_g2_fixture as fixture
import kdenlive_owner_cut as owner_cut
import kdenlive_live_process as live
import kdenlive_readback as readback
from bridge import KDENLIVE_REQUEST_SCHEMA, preview_kdenlive_cut
from kdenlive_gui_actions import ActionJournal, AtspiSession, GuiRejected, require

APPIMAGE_SHA256 = "38ec1bdec007ccd9171f951ba00ded8cc2dd43570c14a438e88fabb4f0a81f4f"
APPIMAGE_VERSION = "25.08.3"
RUNTIME_VARIABLES = (
    "LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH", "QML_IMPORT_PATH", "MLT_REPOSITORY",
    "MLT_DATA", "MLT_PROFILES_PATH",
)
MAX_PROJECT = 5 * 1024 * 1024
MAX_OTIO = 2 * 1024 * 1024
SOURCE_PATHS = (
    "SOURCE_LOCK.json", ".github/workflows/v05-otio-real-media.yml",
    "tooling/ci_private_evidence.py", "tooling/mlt_official_runtime_intake.py",
    "tooling/requirements/mlt-g2-custody.txt",
    "tooling/otio-media/kdenlive_gui_actions.py",
    "tooling/otio-media/kdenlive_gui_e2e.py",
    "tooling/otio-media/test_kdenlive_gui_e2e.py",
    "tooling/otio-media/test_kdenlive_accessibility_diagnostics.py",
    "tooling/otio-media/test_kdenlive_accessibility_preflight.py",
    "tooling/otio-media/kdenlive_complete_g2_fixture.py",
    "tooling/otio-media/kdenlive_live_process.py",
    "tooling/otio-media/test_kdenlive_live_process.py",
    "tooling/otio-media/kdenlive_readback.py",
    "tooling/otio-media/bridge.py",
    "crates/domain/examples/otio_real_media_fixture.rs",
    "crates/native/examples/native_otio_owner_cut_e2e.rs",
    "tooling/otio-media/kdenlive_owner_cut.py",
    "tooling/otio-media/test_kdenlive_owner_cut.py",
)


def source_snapshot(source_sha: str) -> dict:
    actual = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True, timeout=20)
    require(actual.stdout.strip() == source_sha, "native_gui_source_checkout_changed")
    files = {}
    for relative in SOURCE_PATHS:
        expected = subprocess.run(["git", "-C", str(ROOT), "show", source_sha + ":" + relative],
                                  check=True, capture_output=True, timeout=20).stdout
        current, _ = custody.regular(ROOT / relative, 2 * 1024 * 1024)
        require(current == expected, "native_gui_source_differs_from_exact_git_blob:" + relative)
        files[relative] = {"bytes": len(current), "sha256": custody.sha(current)}
    return {"checkout": source_sha, "files": files}



def digest(path: Path, limit: int) -> str:
    data, _ = custody.regular(path, limit)
    return hashlib.sha256(data).hexdigest()


def fresh_directory(path: Path) -> None:
    require(path.is_absolute() and path.parent.resolve(strict=True) == path.parent
            and not path.exists() and not path.is_symlink(),
            "native_gui_directory_not_fresh")
    path.mkdir(mode=0o700)


def validate_dispatch(environment: dict, platform: str, uid: int,
                      *, scope: str = "kdenlive-gui") -> None:
    require(scope in ("accessibility-only", "kdenlive-gui"),
            "native_gui_selection_not_closed")
    require(platform == "linux" and type(uid) is int and uid > 0
            and all(environment.get(name) == value for name, value in {
                "GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
                "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
                "MOTIONWRIGHT_KDENLIVE_GUI_SCOPE": scope,
                "MOTIONWRIGHT_APPROVED_KDENLIVE_G2_SOURCE": "1"}.items()),
            "native_gui_not_selected_disposable_manual_run")


def admitted_context(source_sha: str, recipient: dict,
                     *, scope: str = "kdenlive-gui") -> dict:
    validate_dispatch(os.environ, sys.platform, os.getuid(), scope=scope)
    actual = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True, timeout=20)
    require(actual.stdout.strip() == source_sha == os.environ.get("GITHUB_SHA"),
            "native_gui_source_not_dispatch_sha")
    lock = custody.strict((ROOT / "SOURCE_LOCK.json").read_bytes())
    context = {
        "source_sha": source_sha,
        "semwright_sha": lock["dependencies"]["semwright"]["revision"],
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }
    custody.validate_context(context)
    custody.public_key(recipient)
    return context


def check_edit(initial: dict, edited: dict, comparison: dict, preview: dict) -> None:
    before = initial["observed_bound_editorial_clip_semantics"]
    after = edited["observed_bound_editorial_clip_semantics"]
    require(len(before) == len(after) == 3
            and [r["scene_id"] for r in before] == [r["scene_id"] for r in after]
            and [r["original_inpoint_frame"] for r in before] == [0, 30, 60]
            and [r["original_inpoint_frame"] for r in after] == [0, 30, 60]
            and [r["duration_frames"] for r in before] == [30, 30, 30]
            and [r["duration_frames"] for r in after] == [30, 30, 15],
            "native_gui_observed_edit_not_one_original_end_trim")
    trims = comparison["bounded_native_end_trims_observed"]
    require(trims == [{"scene_id": before[2]["scene_id"],
                       "initial_duration_frames": 30, "reopened_duration_frames": 15}],
            "native_gui_native_readback_edit_different")
    require(preview.get("nle_receiver_acceptance") == "NOT_VERIFIED"
            and preview.get("edit_imported") is False
            and preview.get("human_review_required") is True
            and preview.get("publication_approved") is False
            and preview.get("status") == "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED"
            and preview.get("native_change_proposals") == [{
                "type": "set_scene_duration", "scene_id": before[2]["scene_id"],
                "duration": {"num": "1", "den": "2"}}],
            "native_gui_existing_preview_did_not_preserve_bounded_proposal")


class Recorder:
    def __init__(self, root: Path):
        self.root = root
        self.owned_processes = []
        self.events = root / "action-journal.jsonl"
        self.stream = self.events.open("x", encoding="utf-8")

    def emit(self, value: dict):
        self.stream.write(json.dumps(value, sort_keys=True) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def document(self, name: str, value: dict):
        custody.new_json(self.root / name, value)

    def observe_terminals(self):
        observations = []
        for owned in self.owned_processes:
            observation = {"phase": owned.phase, "pid": owned.pid,
                           "starttime": owned.start, "automatic_retry": False,
                           "termination_requested_here": False}
            try:
                code = owned.process.poll()
                observation["returncode"] = code
                observation["state"] = "TERMINAL_OBSERVED" if code is not None else "STILL_RUNNING"
                if code is None:
                    owned.guard()
                    observation["live_identity_still_bound"] = True
            except BaseException as error:
                observation.update(state="OBSERVATION_ERROR_OR_UNKNOWN",
                                   error_type=type(error).__name__, error=str(error))
            observations.append(observation)
            # Closing our descriptors does not terminate or retry a child.
            for stream in owned.streams:
                stream.close()
        self.document("original-owned-process-terminals.json", {
            "observations": observations, "descendants_not_adopted": True,
            "native_operation_retried": False})
        return observations

    def close(self):
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()


class OwnedProcess:
    def guard(self):
        require(self.process.poll() is None, "native_gui_owned_process_not_running")
        proc = Path("/proc") / str(self.pid)
        require(proc.stat().st_uid == os.getuid()
                and live.process_starttime((proc / "stat").read_bytes(), self.pid) == self.start
                and os.readlink(proc / "exe") == str(self.binary),
                "native_gui_process_identity_changed")



def extract_distribution(appimage: Path, appdir: Path, records: Recorder) -> None:
    """Unpack the hash-admitted distribution once and retain its own terminal."""
    require(appdir.name == "squashfs-root" and not appdir.exists()
            and not appdir.is_symlink(), "native_gui_extract_destination_not_fresh")
    fresh_directory(appdir.parent)
    owned = OwnedProcess()
    owned.phase, owned.records, owned.binary = "distribution-extract", records, appimage
    owned.streams = [(records.root / ("distribution-extract." + name + ".log")).open("xb")
                     for name in ("stdout", "stderr")]
    argv = [str(appimage), "--appimage-extract"]
    records.emit({"step": "distribution-extract", "state": "INTENT", "argv": argv})
    owned.process = subprocess.Popen(argv, cwd=appdir.parent, stdin=subprocess.DEVNULL,
                                     stdout=owned.streams[0], stderr=owned.streams[1])
    owned.pid, owned.start = owned.process.pid, None
    records.owned_processes.append(owned)
    records.emit({"step": "distribution-extract", "state": "RETURNED", "pid": owned.pid})
    if owned.process.poll() is None:
        owned.start = live.process_starttime(
            (Path("/proc") / str(owned.pid) / "stat").read_bytes(), owned.pid)
    try:
        code = owned.process.wait(timeout=180)
    except subprocess.TimeoutExpired:
        records.emit({"step": "distribution-extract", "state": "UNKNOWN_STILL_RUNNING",
                      "pid": owned.pid, "starttime": owned.start, "replayed": False})
        raise
    records.emit({"step": "distribution-extract", "state": "TERMINAL_OBSERVED",
                  "pid": owned.pid, "starttime": owned.start, "returncode": code})
    for stream in owned.streams:
        stream.close()
    require(code == 0, "native_gui_distribution_extraction_failed")
    require(appdir.is_dir() and appdir.resolve(strict=True) == appdir,
            "native_gui_distribution_extraction_output_invalid")


ACCESSIBILITY_ENVIRONMENT_KEYS = frozenset((
    *RUNTIME_VARIABLES, "LD_PRELOAD", "DISPLAY", "WAYLAND_DISPLAY",
    "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS", "AT_SPI_BUS_ADDRESS",
    "DBUS_STARTER_ADDRESS", "DBUS_STARTER_BUS_TYPE", "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
    "XDG_DATA_DIRS", "QT_QPA_PLATFORM", "QT_LINUX_ACCESSIBILITY_ALWAYS_ON",
    "QT_ACCESSIBILITY", "NO_AT_BRIDGE", "LANG", "LC_ALL", "LANGUAGE",
))
LAUNCHER_DIAGNOSTIC_MEMBERS = (
    "apprun-hooks/craft-runenv-hook.sh",
    "apprun-hooks/linuxdeploy-plugin-qt-hook.sh",
    "AppRun.wrapped",
)


def project_accessibility_environment(raw: bytes) -> dict:
    """Retain only the named runtime fields; never publish the raw environment."""
    require(type(raw) is bytes and 0 < len(raw) <= 128 * 1024
            and raw.endswith(b"\0"), "native_accessibility_environment_unbounded")
    fields = raw[:-1].split(b"\0")
    selected = {}
    for item in fields:
        key, separator, value = item.partition(b"=")
        require(separator == b"=", "native_accessibility_environment_malformed")
        name = key.decode("utf-8", "strict")
        if name in ACCESSIBILITY_ENVIRONMENT_KEYS:
            require(name not in selected and len(value) <= 8192,
                    "native_accessibility_environment_duplicate_or_unbounded")
            selected[name] = value.decode("utf-8", "strict")
    return {"schema": "motionwright.native.kdenlive-accessibility-environment/1",
            "original_bytes": len(raw), "original_sha256": hashlib.sha256(raw).hexdigest(),
            "selected_fields": selected, "excluded_field_count": len(fields) - len(selected),
            "raw_environment_retained": False}


def retain_launcher_diagnostics(appdir: Path, records: Recorder) -> None:
    """Read three original members from this fresh hash-admitted distribution."""
    destination = records.root / "launcher-originals"
    fresh_directory(destination)
    observations = []
    for relative in LAUNCHER_DIAGNOSTIC_MEMBERS:
        requested = appdir / relative
        resolved = requested.resolve(strict=True)
        require(resolved.is_relative_to(appdir),
                "native_accessibility_launcher_member_outside_distribution")
        before = requested.lstat()
        link = os.readlink(requested) if stat.S_ISLNK(before.st_mode) else None
        data, observation = custody.regular(resolved, live.MAX_EXE_BYTES)
        after = requested.lstat()
        require((before.st_dev, before.st_ino, before.st_mode, before.st_mtime_ns, before.st_size)
                == (after.st_dev, after.st_ino, after.st_mode, after.st_mtime_ns, after.st_size)
                and requested.resolve(strict=True) == resolved,
                "native_accessibility_launcher_member_changed")
        name = relative.replace("/", "__")
        custody.new_bytes(destination / name, data)
        observations.append({"distribution_relative_path": relative,
                             "resolved_path": str(resolved), "symlink_target": link,
                             "retained_file": "launcher-originals/" + name,
                             "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                             "original_regular_observation": observation})
    records.document("launcher-originals.json", {
        "schema": "motionwright.native.kdenlive-launcher-originals/1",
        "appimage_sha256": APPIMAGE_SHA256, "members": observations,
        "new_distribution_observation": True, "original_unknown_run_replayed": False})


def _bounded_proc_read(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(0 < len(raw) <= limit, "native_accessibility_proc_observation_unbounded")
    return raw


def retain_accessibility_process_context(owned: OwnedProcess) -> None:
    """Observe this exact live process; no signal, wait-for-exit or descendant adoption."""
    owned.guard()
    proc = Path("/proc") / str(owned.pid)
    originals = {
        "stat": _bounded_proc_read(proc / "stat", live.MAX_STAT_BYTES),
        "cmdline": _bounded_proc_read(proc / "cmdline", live.MAX_CMDLINE_BYTES),
        "maps": _bounded_proc_read(proc / "maps", 2 * 1024 * 1024),
    }
    environment = project_accessibility_environment(
        _bounded_proc_read(proc / "environ", 128 * 1024))
    require(live.process_starttime(originals["stat"], owned.pid) == owned.start,
            "native_accessibility_process_birth_changed")
    owned.guard()
    retained = {}
    for name, raw in originals.items():
        filename = owned.phase + ".original-proc-" + name
        custody.new_bytes(owned.records.root / filename, raw)
        retained[name] = {"file": filename, "bytes": len(raw),
                          "sha256": hashlib.sha256(raw).hexdigest()}
    owned.records.document(owned.phase + ".native-process-context.json", {
        "schema": "motionwright.native.kdenlive-accessibility-process/1",
        "pid": owned.pid, "kernel_starttime_ticks": owned.start,
        "actual_executable": str(owned.binary),
        "actual_executable_sha256": owned.binary_sha,
        "originals": retained, "native_environment": environment,
        "gui_action_dispatched_here": False, "termination_requested_here": False})


def initialize_accessibility(ui: AtspiSession, phase: str, records: Recorder,
                             *, accessibility_only: bool) -> list[dict]:
    """A discovery-only selection stops before the first startup acknowledgement."""
    if not accessibility_only:
        rows = ui.wait_ready(phase)
        ui.ready(phase)
        return rows
    require(phase == "accessibility-only", "native_accessibility_phase_not_closed")

    def retain(state: str, rows: list[dict]) -> None:
        records.document("accessibility-discovery.json", {
            "schema": "motionwright.native.kdenlive-accessibility-discovery/1",
            "state": state, "expected_pid": ui.pid,
            "init_returncode": ui.init_returncode,
            "discovery_count": ui.discovery_count,
            "first_discovery": ui.first_discovery,
            "last_discovery": ui.last_discovery, "owned_rows": rows,
            "startup_acknowledgement_attempted": False, "gui_mutation_attempted": False,
            "native_operation_retried": False})
    try:
        rows = ui.wait_ready(phase)
    except BaseException as original:
        try:
            retain("ERROR_OR_UNKNOWN", [])
        except BaseException as recording_error:
            original.add_note("accessibility_preflight_recording_failed:" +
                              type(recording_error).__name__)
        raise
    retain("EXACT_PID_ACCESSIBILITY_OBSERVED", rows)
    return rows


def run_accessibility_preflight(appdir: Path, binary: Path, binary_sha: str,
                                records: Recorder, profile: Path) -> dict:
    # This is a new process in a new manual run. Never resume an earlier GUI.
    retain_launcher_diagnostics(appdir, records)
    process = NativeProcess(appdir, binary, binary_sha, profile, records,
                            "accessibility-only", accessibility_only=True)
    process.guard()
    require(digest(binary, live.MAX_EXE_BYTES) == binary_sha,
            "native_accessibility_executable_changed")
    require(process.accessibility_rows, "native_accessibility_owned_tree_not_observed")
    # Do not call ready, menu, quit, close, keyboard or editable-text operations.
    # Finalization observes the owned terminal once and leaves process disposal
    # to the isolated runner; it does not send a signal or retry this attempt.
    return {"scope": "accessibility-only", "exact_pid_accessibility_observed": True,
            "pid": process.pid, "kernel_starttime_ticks": process.start,
            "fixture_created": False, "otio_import_attempted": False,
            "gui_mutation_attempted": False, "owner_commit_attempted": False,
            "native_roundtrip_observed": False, "termination_requested_here": False,
            "human_acceptance": False, "release_authorized": False}


def run_selected_native(scope: str, appdir: Path, binary: Path, binary_sha: str,
                        capsule: Path, records: Recorder, profile: Path) -> tuple[str, dict]:
    require(scope in ("accessibility-only", "kdenlive-gui"),
            "native_gui_selection_not_closed")
    if scope == "accessibility-only":
        return ("NATIVE_ACCESSIBILITY_OBSERVED",
                run_accessibility_preflight(appdir, binary, binary_sha, records, profile))
    records.emit({"step": "existing-complete-fixture", "state": "INTENT"})
    source_database = records.root / "canonical-source.sqlite3"
    source = fixture.prepare_original(
        capsule, evidence_root=records.root / "firstparty-source",
        source_database=source_database)
    records.document("existing-complete-fixture.json", source)
    records.emit({"step": "existing-complete-fixture", "state": "RETURNED"})
    result = run_route(appdir, binary, binary_sha, capsule, records, profile,
                       source_database=source_database)
    require(result.get("owner_cut_completion", {}).get("sqlite_owner_cut_committed") is True,
            "native_gui_owner_cut_completion_not_observed")
    return "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED", result


class NativeProcess(OwnedProcess):
    def __init__(self, appdir: Path, binary: Path, binary_sha: str,
                 profile: Path, records: Recorder, phase: str,
                 reopen_input: Path | None = None, *, accessibility_only: bool = False):
        require(type(accessibility_only) is bool
                and (phase == "accessibility-only") == accessibility_only
                and (not accessibility_only or reopen_input is None),
                "native_accessibility_selection_cannot_reopen_or_dispatch_gui")
        self.binary, self.binary_sha = binary, binary_sha
        self.phase, self.records, self.appdir = phase, records, appdir
        self.logs = [records.root / (phase + ".stdout.log"),
                     records.root / (phase + ".stderr.log")]
        self.streams = [path.open("xb") for path in self.logs]
        environment = os.environ.copy()
        for name in (*RUNTIME_VARIABLES, "LD_PRELOAD"):
            environment.pop(name, None)
        for name, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                          ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
            directory = profile / sub
            directory.mkdir(exist_ok=True)
            environment[name] = str(directory)
        environment.update({"QT_QPA_PLATFORM": "xcb",
                            "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1",
                            "LC_ALL": "C.UTF-8", "LANGUAGE": "en_US:en"})
        argv = [str(appdir / "AppRun"), "--config", "motionwright-ci.rc"]
        if reopen_input is not None:
            argv.append(str(reopen_input))
        if accessibility_only:
            inherited = b"\0".join(
                (key + "=" + value).encode("utf-8") for key, value in sorted(environment.items())) + b"\0"
            records.document("accessibility-launch-environment.json",
                             project_accessibility_environment(inherited))
        records.emit({"step": phase + ".launch", "state": "INTENT", "argv": argv})
        self.process = subprocess.Popen(
            argv, cwd=profile, env=environment, stdin=subprocess.DEVNULL,
            stdout=self.streams[0], stderr=self.streams[1])
        self.pid = self.process.pid
        self.start = None
        records.owned_processes.append(self)
        records.emit({"step": phase + ".launch", "state": "RETURNED", "pid": self.pid})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            require(self.process.poll() is None, "native_gui_launcher_exited_before_binding")
            proc = Path("/proc") / str(self.pid)
            start = live.process_starttime((proc / "stat").read_bytes(), self.pid)
            if self.start is None:
                self.start = start
            require(start == self.start, "native_gui_launcher_pid_reused")
            if os.readlink(proc / "exe") == str(binary):
                self.guard()
                break
            time.sleep(0.1)
        else:
            raise GuiRejected("native_gui_launcher_did_not_exec_pinned_binary")
        if accessibility_only:
            retain_accessibility_process_context(self)
        self.journal = ActionJournal(records.emit, self.guard)
        self.ui = AtspiSession(self.pid, self.journal)
        self.accessibility_rows = initialize_accessibility(
            self.ui, phase, records, accessibility_only=accessibility_only)

    @contextmanager
    def native_environment(self):
        self.guard()
        raw = (Path("/proc") / str(self.pid) / "environ").read_bytes()
        require(0 < len(raw) <= 128 * 1024 and raw.endswith(b"\0"),
                "native_gui_runtime_environment_unbounded")
        values = dict(item.decode("utf-8").split("=", 1)
                      for item in raw[:-1].split(b"\0") if b"=" in item)
        require(not values.get("LD_PRELOAD"), "native_gui_runtime_preload_not_admitted")
        selected = {}
        for key in RUNTIME_VARIABLES:
            if values.get(key):
                for value in values[key].split(":"):
                    require(value and Path(value).is_absolute()
                            and Path(value).resolve().is_relative_to(self.appdir),
                            "native_gui_runtime_path_outside_distribution")
                selected[key] = values[key]
        previous = {key: os.environ.get(key) for key in RUNTIME_VARIABLES}
        try:
            for key in RUNTIME_VARIABLES:
                if key in selected:
                    os.environ[key] = selected[key]
                else:
                    os.environ.pop(key, None)
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.guard()

    def witness(self, original: Path, original_sha: str, saved: Path,
                saved_sha: str, reopen_input=None, reopen_sha=None) -> dict:
        native_phase = {"import": "import", "reopen": "reopen",
                        "reopen-edited": "reopen"}.get(self.phase)
        require(native_phase is not None, "native_gui_witness_phase_unrecognized")
        with self.native_environment():
            result = live.inspect_live_native_kdenlive(
                self.pid, self.binary, self.binary_sha, original, original_sha,
                saved, saved_sha, phase=native_phase,
                reopen_input=reopen_input, reopen_input_sha256=reopen_sha)
        require(result["original_Kdenlive_application_version"] ==
                live.parse_native_kdenlive_version(f"kdenlive {APPIMAGE_VERSION}".encode("ascii")),
                "native_gui_version_not_exact_distribution")
        self.records.document(self.phase + "-live-process.json", result)
        return result

    def close(self):
        self.ui.quit(self.phase + ".quit")
        try:
            code = self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.records.emit({"step": self.phase + ".terminal",
                               "state": "UNKNOWN_STILL_RUNNING", "pid": self.pid,
                               "replayed": False})
            raise
        self.records.emit({"step": self.phase + ".terminal", "state": "OBSERVED",
                           "pid": self.pid, "starttime": self.start, "returncode": code})
        for stream in self.streams:
            stream.close()
        require(code == 0, "native_gui_process_nonzero_terminal")


def run_route(appdir: Path, binary: Path, binary_sha: str,
              capsule: Path, records: Recorder, profile: Path,
              source_database: Path | None = None) -> dict:
    original = capsule / "timeline.otio"
    manifest_sha = digest(capsule / "bridge-manifest.json", MAX_OTIO)
    original_sha = digest(original, MAX_OTIO)
    initial, edited = records.root / "initial.kdenlive", records.root / "edited.kdenlive"
    received, returned = records.root / "received.otio", records.root / "edited.otio"
    first = NativeProcess(appdir, binary, binary_sha, profile, records, "import")
    first.ui.menu("import.otio-menu", "File", "OpenTimelineIO Import...")
    first.ui.file_dialog("import.otio", ("OpenTimelineIO Import",), original, "Open")
    first.ui.save_as("import.save", initial)
    first.ui.export_otio("import.export", received)
    initial_sha = digest(initial, MAX_PROJECT)
    received_sha = digest(received, MAX_OTIO)
    initial_readback = readback.inspect_saved_kdenlive(
        initial, initial_sha, capsule, manifest_sha)
    records.document("initial-readback.json", initial_readback)
    first_witness = first.witness(original, original_sha, initial, initial_sha)
    records.document("initial-ui-tree.json", {"rows": first.ui.tree()})
    first.close()
    # The first native file is the exact argv of a new, separately awaited PID.
    second = NativeProcess(appdir, binary, binary_sha, profile, records,
                           "reopen", reopen_input=initial)
    second.ui.edit_third_clip()
    second.ui.save_as("reopen.save", edited)
    second.ui.export_otio("reopen.export", returned)
    edited_sha = digest(edited, MAX_PROJECT)
    require(digest(initial, MAX_PROJECT) == initial_sha,
            "native_gui_original_reopen_input_was_overwritten")
    edited_readback = readback.inspect_saved_kdenlive(
        edited, edited_sha, capsule, manifest_sha)
    records.document("edited-readback.json", edited_readback)
    second_witness = second.witness(original, original_sha, edited, edited_sha,
                                    reopen_input=initial, reopen_sha=initial_sha)
    sessions = live.compare_distinct_native_sessions(first_witness, second_witness)
    comparison = readback.compare_two_native_custody_snapshots(
        initial_readback, edited_readback)
    request = {"schema": KDENLIVE_REQUEST_SCHEMA,
               "capsule_manifest_sha256": manifest_sha,
               "received_otio": received.name, "received_sha256": received_sha,
               "edited_otio": returned.name, "edited_sha256": digest(returned, MAX_OTIO)}
    preview = preview_kdenlive_cut(capsule, records.root, request,
        capsule / "source-project.json", authorized_media_roots=(capsule,))
    records.document("bounded-cut-preview.json", preview)
    check_edit(initial_readback, edited_readback, comparison, preview)
    records.document("native-project-comparison.json", comparison)
    records.document("distinct-native-sessions.json", sessions)
    records.document("edited-ui-tree.json", {"rows": second.ui.tree()})
    second.close()
    # Reopen the edited file in a third process and inspect a fresh native export.
    reopened_otio = records.root / "reopened-edited.otio"
    third = NativeProcess(appdir, binary, binary_sha, profile, records,
                          "reopen-edited", reopen_input=edited)
    third.ui.export_otio("reopen-edited.export", reopened_otio)
    require(digest(edited, MAX_PROJECT) == edited_sha,
            "native_gui_edited_project_changed_during_reopen")
    final_readback = readback.inspect_saved_kdenlive(
        edited, edited_sha, capsule, manifest_sha)
    require(final_readback == edited_readback,
            "native_gui_edited_project_readback_changed_on_reopen")
    records.document("reopened-edited-readback.json", final_readback)
    third_witness = third.witness(original, original_sha, edited, edited_sha,
                                  reopen_input=edited, reopen_sha=edited_sha)
    edited_sessions = live.compare_distinct_native_sessions(
        second_witness, third_witness, expected_first_phase="reopen")
    final_comparison = readback.compare_two_native_custody_snapshots(
        initial_readback, final_readback)
    reopened_request = {**request, "edited_otio": reopened_otio.name,
                        "edited_sha256": digest(reopened_otio, MAX_OTIO)}
    reopened_preview = preview_kdenlive_cut(
        capsule, records.root, reopened_request, capsule / "source-project.json",
        authorized_media_roots=(capsule,))
    records.document("reopened-edited-cut-preview.json", reopened_preview)
    check_edit(initial_readback, final_readback, final_comparison, reopened_preview)
    records.document("reopened-edited-comparison.json", final_comparison)
    records.document("edited-project-distinct-native-sessions.json", edited_sessions)
    records.document("reopened-edited-ui-tree.json", {"rows": third.ui.tree()})
    third.close()
    require(digest(initial, MAX_PROJECT) == initial_sha
            and digest(edited, MAX_PROJECT) == edited_sha,
            "native_gui_saved_project_changed_after_reopen")
    require(digest(original, MAX_OTIO) == original_sha
            and digest(capsule / "bridge-manifest.json", MAX_OTIO) == manifest_sha
            and digest(binary, live.MAX_EXE_BYTES) == binary_sha,
            "native_gui_input_or_executable_changed")
    owner_commit = None
    if source_database is not None:
        owner_commit = owner_cut.commit_once(
            capsule, records.root, source_database,
            records.root / "reopened-edited-cut-preview.json")
        records.document("owner-cut-completion.json", owner_commit)
    return {"owner_cut_completion": owner_commit,
            "initial_project_sha256": initial_sha, "edited_project_sha256": edited_sha,
            "original_otio_sha256": original_sha,
            "received_otio_sha256": received_sha,
            "edited_otio_sha256": request["edited_sha256"],
            "reopened_edited_otio_sha256": reopened_request["edited_sha256"],
            "two_distinct_native_processes": True,
            "three_distinct_native_processes": True,
            "edited_native_project_reopened": True, "exact_end_trim_frames": 15}


def finalize_records(records: Recorder, status: str, result: dict, context: dict) -> str:
    # A failure retaining one record does not skip the single sealing attempt.
    errors = []
    try:
        records.observe_terminals()
    except BaseException as error:
        errors.append(type(error).__name__)
    try:
        records.document("original-result.json", {
            "status": status, "source": context, "result": result,
            "recording_errors_before_result": errors,
            "native_operation_retried": False,
            "human_acceptance": False, "release_authorized": False})
    except BaseException as error:
        errors.append(type(error).__name__)
    try:
        records.close()
    except BaseException as error:
        errors.append(type(error).__name__)
    return "LOCAL_RECORDING_FAILED" if errors else status



def verified_snapshot_expectations(result: dict, context: dict) -> dict:
    """Freeze the already-observed route result before collecting any files."""
    require(type(result) is dict, "native_gui_verified_result_unavailable")
    record = {
        "status": "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED",
        "source": context, "result": result,
        "recording_errors_before_result": [],
        "native_operation_retried": False,
        "human_acceptance": False, "release_authorized": False,
    }
    expected = {
        "owner/original-result.json": (
            hashlib.sha256(custody.json_bytes(record)).hexdigest(), MAX_OTIO),
    }
    paths = {
        "initial_project_sha256": ("owner/initial.kdenlive", MAX_PROJECT),
        "edited_project_sha256": ("owner/edited.kdenlive", MAX_PROJECT),
        "original_otio_sha256": ("capsule/timeline.otio", MAX_OTIO),
        "received_otio_sha256": ("owner/received.otio", MAX_OTIO),
        "edited_otio_sha256": ("owner/edited.otio", MAX_OTIO),
        "reopened_edited_otio_sha256": ("owner/reopened-edited.otio", MAX_OTIO),
    }
    for key, (name, limit) in paths.items():
        value = result.get(key)
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value),
                "native_gui_verified_artifact_digest_missing")
        expected[name] = (value, limit)
    owner = result.get("owner_cut_completion")
    require(type(owner) is dict and owner.get("sqlite_owner_cut_committed") is True,
            "native_gui_verified_owner_result_missing")
    for key, name in (
        ("owner_cut_commit_receipt_sha256", "owner/owner-cut-commit-receipt.json"),
        ("owner_cut_reopen_receipt_sha256", "owner/owner-cut-reopen-readback.json"),
    ):
        value = owner.get(key)
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value),
                "native_gui_verified_owner_receipt_digest_missing")
        expected[name] = (value, MAX_OTIO)
    return expected


def require_verified_snapshot(packed: bytes, expected: dict) -> None:
    """Check the captured TAR bytes; never reread or recapture the live files."""
    require(type(packed) is bytes, "native_gui_snapshot_is_not_immutable_bytes")
    seen, matched = set(), set()
    with tarfile.open(fileobj=io.BytesIO(packed), mode="r:") as archive:
        for member in archive:
            require(member.name not in seen and member.isfile()
                    and len(seen) <= custody.MAX_FILES,
                    "native_gui_snapshot_member_not_unique_regular")
            seen.add(member.name)
            if member.name not in expected:
                continue
            digest_, limit = expected[member.name]
            require(0 <= member.size <= limit,
                    "native_gui_verified_snapshot_member_unbounded")
            stream = archive.extractfile(member)
            require(stream is not None, "native_gui_verified_snapshot_member_unreadable")
            with stream:
                raw = stream.read(limit + 1)
            require(len(raw) == member.size
                    and hashlib.sha256(raw).hexdigest() == digest_,
                    "native_gui_verified_snapshot_member_changed")
            matched.add(member.name)
    require(matched == set(expected), "native_gui_verified_snapshot_member_missing")


def seal_and_publish(evidence_root: Path, capsule: Path | None, public_dir: Path,
                     recipient: dict, context: dict, status: str,
                     distribution_evidence: Path | None = None, *,
                     result: dict | None = None) -> dict:
    sealed = None
    complete = False
    expected = None
    if status == "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED":
        try:
            expected = verified_snapshot_expectations(result, context)
        except Exception:
            status = "CUSTODY_FAILED"
    try:
        roots = {"owner": evidence_root}
        if capsule is not None:
            roots["capsule"] = capsule
        if distribution_evidence is not None:
            roots["distribution"] = distribution_evidence
        packed, complete = custody.gather_snapshot(roots, preserve_partial=True)
        if complete and expected is not None:
            try:
                require_verified_snapshot(packed, expected)
            except Exception:
                # Seal these original captured bytes once even on a binding failure.
                # Never recapture files or grant the prior route result to new bytes.
                status = "CUSTODY_FAILED"
        candidate = custody.encrypt_snapshot(packed, recipient, context)
        custody.new_bytes(public_dir / "original-evidence.mwseal", candidate)
        sealed = candidate
    except Exception:
        status = "CUSTODY_FAILED"
    if not complete and status != "CUSTODY_FAILED":
        status = "CUSTODY_INCOMPLETE"
    projection = {
        "schema": "motionwright.native.kdenlive-gui-projection/1",
        "source": context, "status": status,
        "ciphertext_sha256": None if sealed is None else custody.sha(sealed),
        "snapshot_collection_complete": complete,
        "snapshot_is_atomic_SQLite_or_whole_process": False,
        "other_workflow_selections": "NOT_RUN",
        "native_operation_retried": False,
        "human_acceptance": False, "release_authorized": False,
    }
    custody.new_json(public_dir / "projection.json", projection)
    return projection


def main(argv=None) -> int:
    global custody
    import ci_private_evidence as custody
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("appimage", "appdir", "evidence-root", "capsule",
                 "public-dir", "recipient", "distribution-evidence"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--verification-scope", choices=("accessibility-only", "kdenlive-gui"),
                   default="kdenlive-gui")
    args = p.parse_args(argv)
    recipient = custody.strict(custody.regular(args.recipient, 8192)[0])
    context = admitted_context(args.source_sha, recipient, scope=args.verification_scope)
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(all(path.parent == runner_temp for path in
                (args.evidence_root, args.capsule, args.public_dir, args.distribution_evidence))
            and args.appimage.parent == runner_temp
            and args.appdir.parent.parent == runner_temp,
            "native_gui_outputs_not_direct_fresh_runner_children")
    if args.verification_scope == "accessibility-only":
        require(not args.capsule.exists() and not args.capsule.is_symlink(),
                "native_accessibility_unused_capsule_must_be_absent")
    fresh_directory(args.evidence_root)
    fresh_directory(args.public_dir)
    profile = args.evidence_root / "profile"
    profile.mkdir(mode=0o700)
    records = Recorder(args.evidence_root)
    status, result, source_before = "ERROR_OR_UNKNOWN", {}, None
    try:
        source_before = source_snapshot(context["source_sha"])
        records.document("source-before.json", source_before)
        require(digest(args.appimage, 1024 * 1024 * 1024) == APPIMAGE_SHA256,
                "native_gui_appimage_digest_not_admitted")
        extract_distribution(args.appimage, args.appdir, records)
        require(args.appdir.is_absolute() and args.appdir.resolve(strict=True) == args.appdir
                and args.appdir.is_relative_to(runner_temp)
                and (args.appdir / "AppRun").is_file(),
                "native_gui_appdir_not_owned_distribution")
        candidates = [args.appdir / "usr/bin" / name for name in live.APP_NAMES]
        binaries = [path for path in candidates if path.is_file() and not path.is_symlink()
                    and custody.regular(path, live.MAX_EXE_BYTES)[0].startswith(b"\x7fELF")]
        require(len(binaries) == 1, "native_gui_native_ELF_inventory_not_unique")
        launcher = (args.appdir / "AppRun").resolve(strict=True)
        require(launcher.is_relative_to(args.appdir), "native_gui_launcher_outside_distribution")
        launcher_bytes, _ = custody.regular(launcher, live.MAX_EXE_BYTES)
        custody.new_bytes(records.root / "original-AppRun", launcher_bytes)
        binary = binaries[0]
        binary_sha = digest(binary, live.MAX_EXE_BYTES)
        records.document("distribution-binding.json", {
            "appimage_sha256": APPIMAGE_SHA256, "expected_version": APPIMAGE_VERSION,
            "actual_native_binary": str(binary), "actual_native_binary_sha256": binary_sha,
            "AppRun_resolved_path": str(launcher),
            "AppRun_sha256": custody.sha(launcher_bytes),
            "source_checkout": context})
        status, result = run_selected_native(
            args.verification_scope, args.appdir, binary, binary_sha,
            args.capsule, records, profile)
    except BaseException as error:
        try:
            records.document("original-error.json", {
                "type": type(error).__name__, "message": str(error),
                "traceback": traceback.format_exc(), "native_operation_retried": False})
        except BaseException:
            status = "LOCAL_RECORDING_FAILED"
    finally:
        try:
            source_after = source_snapshot(context["source_sha"])
            records.document("source-after.json", source_after)
            if source_before is None or source_after != source_before:
                status = "SOURCE_CHANGED_OR_UNOBSERVED"
        except BaseException as error:
            status = "SOURCE_CHANGED_OR_UNOBSERVED"
            try:
                records.document("source-after-original-error.json", {
                    "type": type(error).__name__, "message": str(error),
                    "source_observation_retried": False})
            except BaseException:
                status = "LOCAL_RECORDING_FAILED"
        status = finalize_records(records, status, result, context)
    selected_capsule = None if args.verification_scope == "accessibility-only" else args.capsule
    projection = seal_and_publish(args.evidence_root, selected_capsule, args.public_dir,
                                  recipient, context, status, args.distribution_evidence,
                                  result=result)
    print(json.dumps(projection, sort_keys=True))
    expected = ("NATIVE_ACCESSIBILITY_OBSERVED" if args.verification_scope == "accessibility-only"
                else "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED")
    return 0 if projection["status"] == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
