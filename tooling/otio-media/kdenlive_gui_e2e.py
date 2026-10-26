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
    "tooling/manim_native_readback.py",
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
    except subprocess.TimeoutExpired as original:
        try:
            records.emit({"step": "distribution-extract", "state": "UNKNOWN_STILL_RUNNING",
                          "pid": owned.pid, "starttime": owned.start, "replayed": False})
        except BaseException as recording_error:
            try:
                original.add_note("native_terminal_custody_failed:" +
                                  type(recording_error).__name__)
            except BaseException:
                pass
            raise original from recording_error
        raise
    original = (GuiRejected("native_gui_distribution_extraction_failed")
                if code != 0 else None)
    try:
        records.emit({"step": "distribution-extract", "state": "TERMINAL_OBSERVED",
                      "pid": owned.pid, "starttime": owned.start, "returncode": code})
        for stream in owned.streams:
            stream.close()
    except BaseException as recording_error:
        if original is not None:
            try:
                original.add_note("native_terminal_custody_failed:" +
                                  type(recording_error).__name__)
            except BaseException:
                pass
            raise original from recording_error
        raise
    if original is not None:
        raise original
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


CAPSULE_RELOCATION_SCHEMA = "motionwright.native.kdenlive-capsule-relocation/1"
CAPSULE_METADATA_NAMES = (
    "bridge-manifest.json", "timeline.otio", "source-project.json",
    "firstparty-complete-native-G2-input.json",
)


def relocation_source_path(capsule: Path) -> Path:
    """The sole pre-move location is derived from the selected disposable path."""
    return capsule.with_name(capsule.name + ".before-relocation")


def _frozen_three_scene_capsule_members(capsule: Path) -> dict:
    manifest_sha = digest(capsule / "bridge-manifest.json", MAX_OTIO)
    root, manifest = readback.read_capsule(capsule, manifest_sha)
    require(root == capsule and len(manifest["bound_media"]) == 3
            and len(manifest["scene_order"]) == 3
            and not manifest["missing_scene_ids"],
            "native_gui_relocation_requires_the_closed_complete_three_scene_fixture")
    names = set(CAPSULE_METADATA_NAMES)
    names.update(item["path"] for item in manifest["bound_media"])
    members = {}
    for name in sorted(names):
        maximum = MAX_OTIO if name in CAPSULE_METADATA_NAMES else readback.MAX_MEDIA
        raw, _ = custody.regular(capsule / name, maximum)
        members[name] = {"bytes": len(raw), "sha256": custody.sha(raw)}
    require(members["bridge-manifest.json"]["sha256"] == manifest_sha,
            "native_gui_relocation_manifest_changed_during_observation")
    for item in manifest["bound_media"]:
        require(members[item["path"]]["sha256"] == item["source_sha256"],
                "native_gui_relocation_media_changed_during_observation")
    return members


def relocate_capsule_once(source: Path, capsule: Path, records: Recorder, *, retained_alab=None) -> dict:
    """Move actual originals once; never rewrite OTIO URLs or infer UI relink."""
    require(source == relocation_source_path(capsule)
            and source.is_absolute() and capsule.is_absolute()
            and source.parent == capsule.parent
            and capsule.parent.resolve(strict=True) == capsule.parent
            and not capsule.exists() and not capsule.is_symlink(),
            "native_gui_relocation_dest_not_the_closed_fresh_sibling")
    retained_kwargs = {} if retained_alab is None else {"retained_alab": retained_alab}
    before = frozen_capsule_members(source, **retained_kwargs)
    intent = {
        "schema": CAPSULE_RELOCATION_SCHEMA, "state": "INTENT",
        "source": str(source), "destination": str(capsule), "members": before,
        "mode": "relative_reference_resolution_after_physical_relocation",
        "ui_relink_dialog_used": False, "native_application_launched_here": False,
        "automatic_retry": False,
    }
    records.document("capsule-relocation.intent.json", intent)
    records.emit({"step": "capsule-relocation", "state": "INTENT",
                  "source": str(source), "destination": str(capsule)})
    # Exclusive intent precedes the only move. Any effect/observation error stops
    # the caller before the first native process; UNKNOWN is never retried.
    try:
        require(not capsule.exists() and not capsule.is_symlink(),
                "native_gui_relocation_destination_appeared_before_move")
        source.rename(capsule)
        require(not source.exists() and not source.is_symlink(),
                "native_gui_relocation_old_location_still_present")
        after = frozen_capsule_members(capsule, **retained_kwargs)
        require(after == before, "native_gui_relocation_changed_original_bytes")
    except BaseException as original:
        try:
            records.emit({"step": "capsule-relocation", "state": "ERROR_OR_UNKNOWN",
                          "error_type": type(original).__name__,
                          "original_error": str(original), "automatic_retry": False})
        except BaseException as recording_error:
            try:
                original.add_note("capsule_relocation_terminal_custody_failed:" +
                                  type(recording_error).__name__)
            except BaseException:
                pass
            raise original from recording_error
        raise
    receipt = {
        **intent, "state": "MOVED_AND_BYTES_OBSERVED",
        "original_location_absent": True, "original_bytes_preserved": True,
        "relative_media_members_preserved": True,
        "source_origin_inferred": False, "alab_sources_adopted_here": False,
        "native_editor_acceptance": "NOT_VERIFIED",
        "human_acceptance": False, "release_authorized": False,
    }
    receipt_path = records.root / "capsule-relocation.original.json"
    records.document(receipt_path.name, receipt)
    receipt_sha = digest(receipt_path, MAX_OTIO)
    records.emit({"step": "capsule-relocation", "state": "MOVED_AND_BYTES_OBSERVED",
                  "receipt_sha256": receipt_sha, "automatic_retry": False})
    return {
        "schema": CAPSULE_RELOCATION_SCHEMA,
        "mode": "relative_reference_resolution_after_physical_relocation",
        "receipt_sha256": receipt_sha, "members": before,
        "original_location_absent": True, "original_bytes_preserved": True,
        "relative_media_members_preserved": True, "ui_relink_dialog_used": False,
        "native_application_launched_here": False, "source_origin_inferred": False,
        "alab_sources_adopted_here": False, "automatic_retry": False,
        "human_acceptance": False, "release_authorized": False,
    }


def run_selected_native(scope: str, appdir: Path, binary: Path, binary_sha: str,
                        capsule: Path, records: Recorder, profile: Path,
                        *, alab_inputs: dict | None = None) -> tuple[str, dict]:
    require(scope in ("accessibility-only", "kdenlive-gui"),
            "native_gui_selection_not_closed")
    if alab_inputs is not None:
        require(scope == "kdenlive-gui", "alab_profile_cannot_enter_accessibility_only")
        return run_selected_alab(appdir, binary, binary_sha, capsule, records, profile, alab_inputs)
    if scope == "accessibility-only":
        return ("NATIVE_ACCESSIBILITY_OBSERVED",
                run_accessibility_preflight(appdir, binary, binary_sha, records, profile))
    records.emit({"step": "existing-complete-fixture", "state": "INTENT"})
    source_database = records.root / "canonical-source.sqlite3"
    before_relocation = relocation_source_path(capsule)
    require(not capsule.exists() and not capsule.is_symlink(),
            "native_gui_selected_capsule_must_be_fresh_before_fixture")
    source = fixture.prepare_original(
        before_relocation, evidence_root=records.root / "firstparty-source",
        source_database=source_database)
    records.document("existing-complete-fixture.json", source)
    records.emit({"step": "existing-complete-fixture", "state": "RETURNED"})
    relocation = relocate_capsule_once(before_relocation, capsule, records)
    result = run_route(appdir, binary, binary_sha, capsule, records, profile,
                       source_database=source_database)
    require(result.get("owner_cut_completion", {}).get("sqlite_owner_cut_committed") is True,
            "native_gui_owner_cut_completion_not_observed")
    require(result.get("native_source_cycle", {}).get("native_source_import_committed") is True
            and result["native_source_cycle"].get("portable_readback_in_new_process") is True,
            "native_gui_canonical_source_cycle_not_observed")
    require(not before_relocation.exists() and not before_relocation.is_symlink()
            and frozen_capsule_members(capsule) == relocation["members"],
            "native_gui_relocated_originals_changed_during_native_route")
    result["capsule_relocation"] = relocation
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
        except subprocess.TimeoutExpired as original:
            try:
                self.records.emit({"step": self.phase + ".terminal",
                                   "state": "UNKNOWN_STILL_RUNNING", "pid": self.pid,
                                   "replayed": False})
            except BaseException as recording_error:
                try:
                    original.add_note("native_terminal_custody_failed:" +
                                      type(recording_error).__name__)
                except BaseException:
                    pass
                raise original from recording_error
            raise
        original = (GuiRejected("native_gui_process_nonzero_terminal")
                    if code != 0 else None)
        try:
            self.records.emit({"step": self.phase + ".terminal", "state": "OBSERVED",
                               "pid": self.pid, "starttime": self.start, "returncode": code})
            for stream in self.streams:
                stream.close()
        except BaseException as recording_error:
            if original is not None:
                try:
                    original.add_note("native_terminal_custody_failed:" +
                                      type(recording_error).__name__)
                except BaseException:
                    pass
                raise original from recording_error
            raise
        if original is not None:
            raise original


def run_route(appdir: Path, binary: Path, binary_sha: str,
              capsule: Path, records: Recorder, profile: Path,
              source_database: Path | None = None, *, retained_alab=None,
              alab_source_input: dict | None = None) -> dict:
    require((retained_alab is None) == (alab_source_input is None),
            "alab_route_requires_original_source_and_retained_media_together")
    require(retained_alab is None or source_database is not None,
            "alab_route_requires_the_canonical_source_owner")
    retained_kwargs = {} if retained_alab is None else {"retained_alab": retained_alab}
    check_selected_edit = check_edit if retained_alab is None else check_alab_edit
    original = capsule / "timeline.otio"
    manifest_sha = digest(capsule / "bridge-manifest.json", MAX_OTIO)
    original_sha = digest(original, MAX_OTIO)
    if retained_alab is not None:
        # Validate the two exact MOV/notice bindings before the first native PID.
        readback.read_capsule(capsule, manifest_sha, **retained_kwargs)
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
        initial, initial_sha, capsule, manifest_sha, **retained_kwargs)
    records.document("initial-readback.json", initial_readback)
    first_witness = first.witness(original, original_sha, initial, initial_sha)
    records.document("initial-ui-tree.json", {"rows": first.ui.tree()})
    first.close()
    # The first native file is the exact argv of a new, separately awaited PID.
    second = NativeProcess(appdir, binary, binary_sha, profile, records,
                           "reopen", reopen_input=initial)
    if retained_alab is None:
        second.ui.edit_third_clip()
    else:
        edit_alab_second_clip(second.ui)
    second.ui.save_as("reopen.save", edited)
    second.ui.export_otio("reopen.export", returned)
    edited_sha = digest(edited, MAX_PROJECT)
    require(digest(initial, MAX_PROJECT) == initial_sha,
            "native_gui_original_reopen_input_was_overwritten")
    edited_readback = readback.inspect_saved_kdenlive(
        edited, edited_sha, capsule, manifest_sha, **retained_kwargs)
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
        capsule / "source-project.json", authorized_media_roots=(capsule,), **retained_kwargs)
    records.document("bounded-cut-preview.json", preview)
    check_selected_edit(initial_readback, edited_readback, comparison, preview)
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
        edited, edited_sha, capsule, manifest_sha, **retained_kwargs)
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
        authorized_media_roots=(capsule,), **retained_kwargs)
    records.document("reopened-edited-cut-preview.json", reopened_preview)
    check_selected_edit(initial_readback, final_readback, final_comparison, reopened_preview)
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
    owner_commit, source_cycle, alab_returned = None, None, None
    if retained_alab is not None:
        alab_returned = owner_cut.finish_alab_return_once(capsule, records.root, source_database,
            edited, edited_sha, records.root / "reopened-edited-cut-preview.json",
            alab_source_input, retained_alab)
        records.document("alab-returned-source-completion.json", alab_returned)
    elif source_database is not None:
        owner_commit = owner_cut.commit_once(
            capsule, records.root, source_database,
            records.root / "reopened-edited-cut-preview.json")
        records.document("owner-cut-completion.json", owner_commit)
        source_cycle = owner_cut.source_cycle_once(
            capsule, records.root, source_database, edited, edited_sha, owner_commit)
        records.document("native-source-cycle-completion.json", source_cycle)
    result = {"owner_cut_completion": owner_commit, "native_source_cycle": source_cycle,
            "initial_project_sha256": initial_sha, "edited_project_sha256": edited_sha,
            "original_otio_sha256": original_sha,
            "received_otio_sha256": received_sha,
            "edited_otio_sha256": request["edited_sha256"],
            "reopened_edited_otio_sha256": reopened_request["edited_sha256"],
            "two_distinct_native_processes": True,
            "three_distinct_native_processes": True,
            "edited_native_project_reopened": True,
            "exact_end_trim_frames": 15 if retained_alab is None else 12}
    if retained_alab is not None:
        result["alab_returned_source"] = alab_returned
    return result


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
    if "source_profile" in result:
        return _alab_verified_snapshot_expectations(result, context)
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
    cycle = result.get("native_source_cycle")
    require(type(cycle) is dict
            and all(cycle.get(key) is True for key in (
                "native_source_import_committed", "source_readback_in_new_process",
                "source_blob_reopened_equal", "portable_readback_in_new_process",
                "fresh_generation_observed", "source_fidelity_unavailable"))
            and cycle.get("native_source_sha256") == result["edited_project_sha256"]
            and type(cycle.get("additional_source_revision_increment")) is int
            and cycle["additional_source_revision_increment"] == 1
            and type(cycle.get("production_receipt_count")) is int
            and cycle["production_receipt_count"] == 0
            and type(cycle.get("source_generation")) is str and cycle["source_generation"]
            and type(cycle.get("portable_generation")) is str and cycle["portable_generation"]
            and cycle["portable_generation"] != cycle["source_generation"]
            and type(cycle.get("bundle_inspection_fingerprint")) is str
            and re.fullmatch(r"[0-9a-f]{64}", cycle["bundle_inspection_fingerprint"])
            and all(cycle.get(key) is False for key in (
                "local_production_receipts_inherited", "execution_authority_issued",
                "per_scene_native_semantic_mapping_verified", "native_editor_dispatched_here",
                "alab_sources_adopted_here", "automatic_retry",
                "human_acceptance", "release_authorized")),
            "native_gui_verified_source_cycle_missing_or_promoted")
    for key, name in (
        ("native_source_import_receipt_sha256", "owner/native-source-attach-receipt.json"),
        ("native_source_readback_sha256", "owner/native-source-reopen.json"),
        ("native_source_bundle_receipt_sha256", "owner/native-source-bundle-receipt.json"),
        ("native_source_portable_readback_sha256", "owner/native-source-portable-reopen.json"),
        ("bundle_manifest_sha256", "owner/native-source-portable/manifest.json"),
    ):
        value = cycle.get(key)
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value),
                "native_gui_verified_source_cycle_digest_missing")
        expected[name] = (value, MAX_OTIO)
    relocation = result.get("capsule_relocation")
    require(type(relocation) is dict
            and relocation.get("schema") == CAPSULE_RELOCATION_SCHEMA
            and relocation.get("mode") ==
                "relative_reference_resolution_after_physical_relocation"
            and all(relocation.get(key) is True for key in (
                "original_location_absent", "original_bytes_preserved",
                "relative_media_members_preserved"))
            and all(relocation.get(key) is False for key in (
                "ui_relink_dialog_used", "native_application_launched_here",
                "source_origin_inferred", "alab_sources_adopted_here",
                "automatic_retry", "human_acceptance", "release_authorized")),
            "native_gui_verified_physical_relocation_missing_or_promoted")
    receipt_sha = relocation.get("receipt_sha256")
    require(type(receipt_sha) is str and re.fullmatch(r"[0-9a-f]{64}", receipt_sha),
            "native_gui_verified_relocation_receipt_digest_missing")
    expected["owner/capsule-relocation.original.json"] = (receipt_sha, MAX_OTIO)
    members = relocation.get("members")
    require(type(members) is dict and 5 <= len(members) <= 7
            and set(CAPSULE_METADATA_NAMES) <= set(members),
            "native_gui_verified_relocation_members_missing")
    for name, item in members.items():
        metadata = name in CAPSULE_METADATA_NAMES
        require(type(name) is str and (metadata or re.fullmatch(r"media/[0-9a-f]{64}\.mp4", name))
                and type(item) is dict and set(item) == {"bytes", "sha256"}
                and type(item["bytes"]) is int and item["bytes"] > 0
                and type(item["sha256"]) is str
                and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]),
                "native_gui_verified_relocation_member_not_closed")
        if not metadata:
            require(name == "media/" + item["sha256"] + ".mp4",
                    "native_gui_verified_relocation_media_name_not_digest_bound")
        maximum = MAX_OTIO if metadata else readback.MAX_MEDIA
        require(item["bytes"] <= maximum,
                "native_gui_verified_relocation_member_exceeds_bound")
        member = "capsule/" + name
        if member in expected:
            require(expected[member] == (item["sha256"], maximum),
                    "native_gui_verified_relocation_disagrees_with_native_input")
        expected[member] = (item["sha256"], maximum)
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
    pre_relocation_capsule = None if capsule is None else relocation_source_path(capsule)
    if status == "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED":
        try:
            expected = verified_snapshot_expectations(result, context)
        except Exception:
            status = "CUSTODY_FAILED"
    try:
        roots = {"owner": evidence_root}
        if pre_relocation_capsule is not None:
            require(capsule is not None
                    and pre_relocation_capsule == relocation_source_path(capsule),
                    "native_gui_pre_relocation_custody_path_not_closed")
            if pre_relocation_capsule.exists() or pre_relocation_capsule.is_symlink():
                roots["capsule-before-relocation"] = pre_relocation_capsule
                if expected is not None:
                    status = "CUSTODY_FAILED"
        if capsule is not None:
            roots["capsule"] = capsule
        if distribution_evidence is not None:
            roots["distribution"] = distribution_evidence
        packed, complete = custody.gather_snapshot(roots, preserve_partial=True)
        if expected is not None and pre_relocation_capsule is not None:
            if pre_relocation_capsule.exists() or pre_relocation_capsule.is_symlink():
                status = "CUSTODY_FAILED"
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



ALAB_GUI_INPUT_SCHEMA = "motionwright.native.alab-gui-input/1"
ALAB_CAPSULE_METADATA_NAMES = (
    "bridge-manifest.json", "timeline.otio", "source-project.json",
    "alab-complete-native-G2-input.json", "ALab-HANDOFF-SCOPE.json", "README.md",
    "ALab-LICENSE.original.txt", "ALab-README.original.txt", "ALab-SELECTION-NOTICE.original.txt",
)
ALAB_PORTABLE_ZIP = "portable-cut.zip"
MAX_ALAB_PORTABLE_ZIP = 64 * 1024 * 1024


def load_alab_gui_inputs(path: Path, context: dict, runner_temp: Path) -> tuple[dict, bytes]:
    """Read the finite, run-bound input using the existing original-file reader.

    Rights are explicit caller declarations. Retained metadata never creates a
    media grant or a new native run admission. The ordinary three-scene fixture
    does not enter this opt-in path.
    """
    import bridge as media_bridge
    require(path.is_absolute() and path.is_relative_to(runner_temp)
            and path.resolve(strict=True) == path and not path.is_symlink(),
            "alab_gui_input_not_a_plain_owned_original")
    raw, _ = custody.regular(path, 128 * 1024)
    value = custody.strict(raw)
    require(type(value) is dict and set(value) == {
                "schema", "profile", "source", "originals", "notices", "owner_review", "media_rights"}
            and value["schema"] == ALAB_GUI_INPUT_SCHEMA
            and value["profile"] == media_bridge.ALAB_RETAINED_PROFILE
            and value["source"] == context,
            "alab_gui_input_not_bound_to_the_admitted_source_and_run")
    originals_pins = {role: (size, sha) for role, size, sha in media_bridge._ALAB_RETAINED_ORIGINALS}
    originals_pins.update({"media" + str(index): (row[2], row[3])
                          for index, row in enumerate(media_bridge._ALAB_RETAINED_MEDIA)})
    notice_pins = {role: (size, sha) for role, _, size, sha in owner_cut._ALAB_SOURCE_NOTICES}
    admitted = {}
    for label, pins in (("originals", originals_pins), ("notices", notice_pins)):
        rows = value[label]
        require(type(rows) is dict and set(rows) == set(pins), "alab_gui_input_roles_are_not_closed")
        admitted[label] = {}
        for role, (size, sha) in pins.items():
            item = rows[role]
            require(type(item) is dict and set(item) == {"path", "bytes", "sha256"}
                    and type(item["path"]) is str and type(item["bytes"]) is int
                    and item["bytes"] == size and item["sha256"] == sha,
                    "alab_gui_input_descriptor_differs_from_the_retained_original:" + role)
            original = Path(item["path"])
            require(original.is_absolute() and original.is_relative_to(runner_temp)
                    and original.resolve(strict=True) == original and not original.is_symlink(),
                    "alab_gui_original_path_not_plain_and_owned:" + role)
            # Descriptor guards precede AppImage extraction or any new process.
            readback.read_closed_bytes(original, sha, size)
            admitted[label][role] = original
    review = value["owner_review"]
    rights = value["media_rights"]
    require(type(review) is dict and set(review) == {"reviewer", "synthetic_source_allowed"}
            and type(review["reviewer"]) is str and 1 <= len(review["reviewer"].strip()) <= 200
            and review["synthetic_source_allowed"] is False,
            "alab_gui_original_source_review_missing")
    require(type(rights) is list and len(rights) == 2,
            "alab_gui_requires_two_explicit_source_rights_declarations")
    for row in rights:
        require(type(row) is dict and set(row) == {"owner", "license", "authorized", "redistributable"}
                and row["authorized"] is True and row["redistributable"] is True
                and all(type(row[name]) is str and 1 <= len(row[name].strip()) <= 250
                        for name in ("owner", "license"))
                and "synthetic" not in row["license"].lower(),
                "alab_gui_media_rights_declaration_not_admitted")
    admitted.update({"owner_review": review, "media_rights": rights,
                     "admission_sha256": custody.sha(raw)})
    return admitted, raw


def frozen_capsule_members(capsule: Path, *, retained_alab=None) -> dict:
    if retained_alab is None:
        return _frozen_three_scene_capsule_members(capsule)
    import bridge as media_bridge
    manifest_sha = digest(capsule / "bridge-manifest.json", MAX_OTIO)
    root, manifest = readback.read_capsule(capsule, manifest_sha, retained_alab=retained_alab)
    require(root == capsule and manifest.get("output_profile") == "alab-two-retained-mov-cut-v1"
            and len(manifest["bound_media"]) == len(manifest["scene_order"]) == 2
            and not manifest["missing_scene_ids"],
            "alab_relocation_requires_the_closed_two_original_MOV_profile")
    names = set(ALAB_CAPSULE_METADATA_NAMES) | {ALAB_PORTABLE_ZIP}
    names.update(item["path"] for item in manifest["bound_media"])
    require(len(names) == 12, "alab_relocation_member_roster_changed")
    members = {}
    for name in sorted(names):
        maximum = (MAX_ALAB_PORTABLE_ZIP if name == ALAB_PORTABLE_ZIP
                   else MAX_OTIO if name in ALAB_CAPSULE_METADATA_NAMES else readback.MAX_MEDIA)
        raw, _ = custody.regular(capsule / name, maximum)
        members[name] = {"bytes": len(raw), "sha256": custody.sha(raw)}
    require(members["bridge-manifest.json"]["sha256"] == manifest_sha,
            "alab_relocation_manifest_changed_during_observation")
    for item in manifest["bound_media"]:
        require(item["path"] == "media/" + item["source_sha256"] + ".mov"
                and members[item["path"]] == {"bytes": item["bytes"], "sha256": item["source_sha256"]},
                "alab_relocation_original_MOV_changed")
    for _, name, size, sha in media_bridge._ALAB_SOURCE_NOTICES:
        require(members[name] == {"bytes": size, "sha256": sha},
                "alab_relocation_original_notice_changed")
    scope = custody.strict(custody.regular(capsule / "ALab-HANDOFF-SCOPE.json", MAX_OTIO)[0])
    require(scope.get("schema") == "motionwright.alab-private-selected-handoff/1"
            and scope.get("portable_zip_sha256") == members[ALAB_PORTABLE_ZIP]["sha256"]
            and scope.get("portable_zip_includes_all_original_notices") is True
            and scope.get("retain_all_original_notices_when_redistributing") is True
            and all(scope.get(key) is False for key in ("third_party_audio_selected",
                "music_or_voice_clearance_claimed", "endorsement_claimed", "human_acceptance", "release_authorized")),
            "alab_relocation_standalone_zip_or_original_license_scope_changed")
    return members


def _alab_timecode24(value: str) -> int:
    require(type(value) is str and re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{2}", value),
            "alab_native_duration_field_is_not_a_24fps_timecode")
    hours, minutes, seconds, frames = (int(part) for part in value.split(":"))
    require(minutes < 60 and seconds < 60 and frames < 24,
            "alab_native_duration_field_exceeds_24fps_clock")
    return ((hours * 60 + minutes) * 60 + seconds) * 24 + frames


def edit_alab_second_clip(ui: AtspiSession) -> None:
    """Use the existing AT-SPI controls and journal for the selected 182-to-170 trim."""
    from kdenlive_gui_actions import TIMECODE, labeled_field, normalized, unique
    rows = ui.tree()
    monitors = [row for row in rows if row["visible"]
                and normalized(row["name"]) == "Project Monitor"
                and row["role"] not in ("menu item", "check menu item", "label")]
    fields = {row["key"]: row for row in rows if row["visible"] and row["enabled"]
              and row["editable"] and TIMECODE.fullmatch(row["text"]) and any(
                  row["key"].startswith(parent["key"] + "/") for parent in monitors)}
    leaves = [row for row in fields.values() if not any(
        other["key"].startswith(row["key"] + "/") for other in fields.values() if other is not row)]
    position = unique(leaves, code="alab_project_monitor_timecode_not_unique")
    ui.text("alab.seek100", position, "00:00:04:04")
    ui.key("alab.commit-seek", 0xFF0D)
    ui.key("alab.select-video-track1", 0x0031)
    ui.menu("alab.select-clip", "Timeline", "Selection", "Select Clip")
    ui.menu("alab.duration", "Timeline", "Current clip", "Edit Duration")
    dialog = ui.dialog("alab.duration-dialog", ("Duration",))
    rows = ui.descendants(dialog)
    fields = [labeled_field(rows, (label,)) for label in ("Position:", "Crop start:", "Duration:")]
    require(tuple(_alab_timecode24(row["text"]) for row in fields) == (52, 0, 182),
            "alab_native_selected_clip_is_not_the_original_second_occurrence")
    require(not any(row["visible"] and normalized(row["name"]) == "Apply duration to all items"
                    for row in rows), "alab_native_multiple_clip_duration_refused")
    ui.journal.emit({"step": "alab.duration-precondition", "state": "OBSERVED", "fields": fields,
                     "source_fps": 24, "position_frames": 52, "crop_start_frames": 0,
                     "duration_frames": 182, "new_duration_frames": 170, "automatic_retry": False})
    ui.text("alab.duration170", fields[2], "00:00:07:02")
    ui.key("alab.commit-duration-field", 0xFF0D)
    dialog = ui.dialog("alab.duration-before-accept", ("Duration",))
    rows = ui.descendants(dialog)
    actual = tuple(_alab_timecode24(labeled_field(rows, (label,))["text"])
                   for label in ("Position:", "Crop start:", "Duration:"))
    require(actual == (52, 0, 170), "alab_native_duration_field_readback_changed")
    okay = ui.journal.wait("alab.accept-discover", lambda: ui.find(
        ("OK",), ("push button",), scope=dialog["key"]))
    ui.action("alab.accept-once", okay)
    ui.journal.wait("alab.dialog-closed", lambda: not ui.find(("Duration",), ("dialog",)))


def check_alab_edit(initial: dict, edited: dict, comparison: dict, preview: dict) -> None:
    before = initial["observed_bound_editorial_clip_semantics"]
    after = edited["observed_bound_editorial_clip_semantics"]
    require(len(before) == len(after) == 2
            and [row["scene_id"] for row in before] == [row["scene_id"] for row in after]
            and [row["original_inpoint_frame"] for row in before] == [0, 0]
            and [row["original_inpoint_frame"] for row in after] == [0, 0]
            and [row["duration_frames"] for row in before] == [52, 182]
            and [row["duration_frames"] for row in after] == [52, 170]
            and comparison["bounded_native_end_trims_observed"] == [{"scene_id": before[1]["scene_id"],
                "initial_duration_frames": 182, "reopened_duration_frames": 170}],
            "alab_native_observed_edit_is_not_only_second182to170")
    require(preview.get("nle_receiver_acceptance") == "NOT_VERIFIED"
            and preview.get("edit_imported") is False and preview.get("human_review_required") is True
            and preview.get("publication_approved") is False
            and preview.get("status") == "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED"
            and preview.get("native_change_proposals") == [{"type": "set_scene_duration",
                "scene_id": before[1]["scene_id"], "duration": {"num": "85", "den": "12"}}],
            "alab_existing_preview_changed_selected_trim_or_promoted_native_acceptance")


def run_selected_alab(appdir: Path, binary: Path, binary_sha: str, capsule: Path,
                       records: Recorder, profile: Path, inputs: dict) -> tuple[str, dict]:
    import bridge as media_bridge
    require(type(inputs) is dict and set(inputs) == {
                "originals", "notices", "owner_review", "media_rights", "admission_sha256"}
            and type(inputs["admission_sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", inputs["admission_sha256"]),
            "alab_gui_selected_input_contract_changed")
    require(digest(records.root / "alab-gui-input.original.json", 128 * 1024) == inputs["admission_sha256"],
            "alab_gui_input_not_retained_before_native_dispatch")
    database = records.root / "canonical-source.sqlite3"
    before = relocation_source_path(capsule)
    require(not capsule.exists() and not capsule.is_symlink(), "alab_gui_capsule_not_fresh")
    records.emit({"step": "existing-alab-source", "state": "INTENT"})
    source, retained = owner_cut.prepare_alab_original(before, evidence_root=records.root / "alab-source",
        source_database=database, **{key: inputs[key] for key in ("originals", "notices", "owner_review", "media_rights")})
    records.document("existing-alab-source.json", source)
    records.emit({"step": "existing-alab-source", "state": "RETURNED"})
    relocation = relocate_capsule_once(before, capsule, records, retained_alab=retained)
    result = run_route(appdir, binary, binary_sha, capsule, records, profile,
        source_database=database, retained_alab=retained, alab_source_input=source)
    completion = result.get("alab_returned_source")
    require(type(completion) is dict and completion.get("owner_cut_single_revision_committed") is True
            and completion.get("native_source_additional_revision_committed") is True
            and completion.get("new_process_portable_reopened_equal") is True,
            "alab_gui_returned_cut_source_and_portable_cycle_not_observed")
    require(not before.exists() and not before.is_symlink()
            and frozen_capsule_members(capsule, retained_alab=retained) == relocation["members"],
            "alab_gui_relocated_originals_changed_during_the_native_route")
    result.update({"source_profile": media_bridge.ALAB_RETAINED_PROFILE,
                   "alab_source_adoption": source, "capsule_relocation": relocation,
                   "alab_input_admission_sha256": inputs["admission_sha256"]})
    result["alab_original_bindings"] = observe_alab_original_bindings(records.root, source, completion)
    return "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED", result


def _alab_original_member_limits() -> dict:
    """Only named members of the existing owner evidence root; no workspace scan."""
    limits = {
        "alab-gui-input.original.json": 128 * 1024,
        "alab-source/ALab_mk020_final_edit.h264.otio": MAX_OTIO,
        "alab-source/NATIVE_READBACK.original.json": MAX_OTIO,
        "alab-source/mk020_0010-container.original.json": MAX_OTIO,
        "alab-source/mk020_0020-container.original.json": MAX_OTIO,
        "alab-source/mk020_0010.mov": readback.MAX_MEDIA,
        "alab-source/mk020_0020.mov": readback.MAX_MEDIA,
        "alab-source/alab-retained-origin-derived.json": MAX_OTIO,
        "alab-source/alab-adoption-receipt.json": MAX_OTIO,
        "alab-source/alab-source-reopen.json": MAX_OTIO,
        "alab-source/alab-bundle-receipt.json": MAX_OTIO,
        "alab-source/alab-portable-reopen.json": MAX_OTIO,
        "alab-source/alab-source-portable/manifest.json": MAX_OTIO,
        "alab-source/alab-source-input.original.json": MAX_OTIO,
        "alab-source/alab-portable-request.original.json": MAX_OTIO,
        "alab-owner-cut-preview.json": MAX_OTIO,
        "alab-returned-source-input.original.json": MAX_OTIO,
        "reopened-edited-cut-preview.json": MAX_OTIO,
        "alab-returned-source-receipt.json": MAX_OTIO,
        "alab-returned-source-reopen.json": MAX_OTIO,
        "alab-edited-bundle-receipt.json": MAX_OTIO,
        "alab-edited-portable-reopen.json": MAX_OTIO,
        "alab-edited-source-portable/manifest.json": MAX_OTIO,
    }
    for _, name, _, _ in owner_cut._ALAB_SOURCE_NOTICES:
        limits["alab-source/" + name] = MAX_OTIO
    for stage in range(7):
        limits[f"alab-source/alab-adoption-receipt.stage-{stage:02}.result.original.json"] = MAX_OTIO
    for stage in (7, 8):
        limits[f"alab-returned-source-receipt.stage-{stage:02}.result.original.json"] = MAX_OTIO
    for prefix, _ in _alab_original_calls():
        for suffix, maximum in ((".intent.json", MAX_OTIO), (".terminal.json", MAX_OTIO),
                                (".stdout.bin", fixture.MAX_RUST_STDOUT), (".stderr.bin", 32 * 1024)):
            limits[prefix + suffix] = maximum
    return limits


def _alab_original_calls() -> tuple:
    return (
        ("alab-source/alab-adopt-cli", "alab-adopt"),
        ("alab-source/alab-source-readback-cli", "alab-source-readback"),
        ("alab-source/alab-source-bundle-cli", "alab-source-bundle"),
        ("alab-source/alab-source-portable-readback-cli", "alab-source-portable-readback"),
        ("alab-returned-cut-cli", "alab-returned-cut"),
        ("alab-returned-readback-cli", "alab-returned-readback"),
        ("alab-edited-bundle-cli", "alab-edited-bundle"),
        ("alab-edited-portable-readback-cli", "alab-edited-portable-readback"),
    )


def _alab_expected_original_sha(source: dict, completion: dict) -> dict:
    import bridge as media_bridge
    expected = {}
    roles = {"otio": "ALab_mk020_final_edit.h264.otio", "readback": "NATIVE_READBACK.original.json",
             "container0": "mk020_0010-container.original.json", "container1": "mk020_0020-container.original.json",
             "media0": "mk020_0010.mov", "media1": "mk020_0020.mov"}
    pins = {role: (size, sha) for role, size, sha in media_bridge._ALAB_RETAINED_ORIGINALS}
    pins.update({"media" + str(i): (row[2], row[3]) for i, row in enumerate(media_bridge._ALAB_RETAINED_MEDIA)})
    require(source.get("originals") == {role: {"bytes": size, "sha256": sha} for role, (size, sha) in pins.items()},
            "alab_verified_original_source_descriptor_roster_changed")
    for role, (_, sha) in pins.items():
        expected["alab-source/" + roles[role]] = sha
    for _, name, _, sha in owner_cut._ALAB_SOURCE_NOTICES:
        expected["alab-source/" + name] = sha
    for key, path in (
        ("derived_descriptor_sha256", "alab-retained-origin-derived.json"),
        ("adoption_receipt_sha256", "alab-adoption-receipt.json"),
        ("source_readback_sha256", "alab-source-reopen.json"),
        ("bundle_receipt_sha256", "alab-bundle-receipt.json"),
        ("portable_readback_sha256", "alab-portable-reopen.json"),
        ("bundle_manifest_sha256", "alab-source-portable/manifest.json"),
    ):
        expected["alab-source/" + path] = source.get(key)
    for key, path in (
        ("returned_source_receipt_sha256", "alab-returned-source-receipt.json"),
        ("returned_source_readback_sha256", "alab-returned-source-reopen.json"),
        ("edited_bundle_receipt_sha256", "alab-edited-bundle-receipt.json"),
        ("edited_portable_readback_sha256", "alab-edited-portable-reopen.json"),
        ("bundle_manifest_sha256", "alab-edited-source-portable/manifest.json"),
    ):
        expected[path] = completion.get(key)
    limits = _alab_original_member_limits()
    for container, calls, strip_prefix in ((source, _alab_original_calls()[:4], "alab-source/"),
                                          (completion, _alab_original_calls()[4:], "")):
        originals = container.get("source_call_originals")
        names = {prefix.removeprefix(strip_prefix) + suffix
                 for prefix, _ in calls for suffix in (".intent.json", ".terminal.json", ".stdout.bin", ".stderr.bin")}
        require(type(originals) is dict and set(originals) == names,
                "alab_verified_call_originals_not_frozen_by_the_first_caller")
        for name, row in originals.items():
            member = strip_prefix + name
            require(type(row) is dict and set(row) == {"bytes", "sha256"}
                    and type(row["bytes"]) is int and 0 <= row["bytes"] <= limits[member]
                    and type(row["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]),
                    "alab_verified_first_caller_stream_descriptor_changed")
            expected[member] = row["sha256"]
    require(all(type(sha) is str and re.fullmatch(r"[0-9a-f]{64}", sha) for sha in expected.values()),
            "alab_verified_original_digest_missing")
    return expected


def observe_alab_original_bindings(root: Path, source: dict, completion: dict) -> dict:
    """Freeze exact named originals for the existing single snapshot collector."""
    expected = _alab_expected_original_sha(source, completion)
    members, captured = {}, {}
    for name, maximum in _alab_original_member_limits().items():
        raw, _ = custody.regular(root / name, maximum)
        captured[name] = raw
        members[name] = {"bytes": len(raw), "sha256": custody.sha(raw)}
        if name in expected:
            require(members[name]["sha256"] == expected[name], "alab_verified_original_changed_before_capture")
    adoption = custody.strict(captured["alab-source/alab-adoption-receipt.json"])
    source_stages = adoption.get("stage_receipts")
    returned_stages = completion.get("stage_originals")
    for stages, numbers, prefix, stem in (
        (source_stages, range(7), "alab-source/", "alab-adoption-receipt"),
        (returned_stages, (7, 8), "", "alab-returned-source-receipt"),
    ):
        require(type(stages) is list and len(stages) == len(numbers), "alab_verified_original_stage_roster_missing")
        for number, row in zip(numbers, stages):
            filename = f"{stem}.stage-{number:02}.result.original.json"
            require(type(row) is dict and type(row.get("index")) is int and row["index"] == number
                    and row.get("file") == filename
                    and members[prefix + filename] == {"bytes": row.get("bytes"), "sha256": row.get("sha256")},
                    "alab_verified_stage_original_not_the_returned_typed_outcome")
    for prefix, mode in _alab_original_calls():
        intent = custody.strict(captured[prefix + ".intent.json"])
        terminal = custody.strict(captured[prefix + ".terminal.json"])
        require(intent.get("state") == "INTENT" and type(intent.get("command")) is list
                and len(intent["command"]) >= 2 and intent["command"][:2] == [str(owner_cut.OWNER_EXAMPLE), mode]
                and type(intent.get("timeout")) is int and intent["timeout"] == 90
                and intent.get("invocation_retried") is False
                and terminal.get("schema") == "motionwright.native.firstparty-source-call/1"
                and terminal.get("state") == "TERMINAL_OBSERVED"
                and type(terminal.get("returncode")) is int and terminal["returncode"] == 0
                and terminal.get("invocation_retried") is False
                and terminal.get("timeout_policy") == "existing_subprocess_run"
                and terminal.get("error") is None,
                "alab_verified_original_call_is_not_its_single_known_terminal")
        for stream in ("stdout", "stderr"):
            row = terminal.get("captured_streams", {}).get(stream)
            member = members[prefix + "." + stream + ".bin"]
            require(type(row) is dict and row.get("captured") is True
                    and type(row.get("bytes")) is int and row["bytes"] == member["bytes"]
                    and row.get("sha256") == member["sha256"],
                    "alab_verified_original_call_streams_do_not_match_terminal")
    return {"schema": "motionwright.native.alab-original-bindings/1", "members": members,
            "source_stage_originals": source_stages, "single_existing_collector": True,
            "native_operation_retried": False}


def _alab_verified_snapshot_expectations(result: dict, context: dict) -> dict:
    import bridge as media_bridge
    require(result.get("source_profile") == media_bridge.ALAB_RETAINED_PROFILE
            and result.get("owner_cut_completion") is None and result.get("native_source_cycle") is None
            and result.get("three_distinct_native_processes") is True
            and result.get("edited_native_project_reopened") is True
            and type(result.get("exact_end_trim_frames")) is int and result["exact_end_trim_frames"] == 12,
            "alab_verified_receiver_result_not_the_closed_two_scene_cycle")
    source, completion = result.get("alab_source_adoption"), result.get("alab_returned_source")
    require(type(source) is dict and source.get("schema") == "motionwright.native.alab-complete-source-G2-input/1"
            and source.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
            and type(source.get("source_adoption_history_events")) is int and source["source_adoption_history_events"] == 7
            and source.get("source_scene_frames") == [52, 182] and source.get("source_frame_rate") == 24
            and source.get("expected_later_edited_scene_frames") == [52, 170]
            and all(source.get(key) is True for key in ("canonical_source_adopted", "sqlite_source_reopened_equal",
                "all_source_blobs_reopened_equal", "whole_original_otio_retained", "source_fidelity_unavailable",
                "new_process_source_readback", "new_process_portable_readback", "separate_owner_cas", "fresh_generation_observed"))
            and all(source.get(key) is False for key in ("historical_readback_rewritten", "new_decode", "new_render",
                "edit_imported", "nle_opened", "per_scene_native_editability_verified", "local_production_receipts_inherited",
                "execution_authority_issued", "automatic_retry", "human_acceptance", "release_authorized")),
            "alab_verified_original_source_adoption_missing_or_promoted")
    require(type(completion) is dict and completion.get("schema") == "motionwright.native.alab-returned-source-completion/1"
            and completion.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
            and type(completion.get("additional_history_events")) is int and completion["additional_history_events"] == 2
            and type(completion.get("total_original_history_events")) is int and completion["total_original_history_events"] == 9
            and completion.get("original_second_frames") == 182 and completion.get("edited_second_frames") == 170
            and completion.get("native_source_sha256") == result.get("edited_project_sha256")
            and all(completion.get(key) is True for key in ("owner_cut_single_revision_committed",
                "native_source_additional_revision_committed", "new_process_source_reopened_equal",
                "new_process_portable_reopened_equal", "all_four_source_blobs_reopened_equal",
                "whole_original_otio_retained", "source_fidelity_unavailable"))
            and all(completion.get(key) is False for key in ("local_production_receipts_inherited", "execution_authority_issued",
                "per_scene_native_editability_verified", "native_editor_dispatched_here", "automatic_retry",
                "human_acceptance", "release_authorized"))
            and type(completion.get("source_generation")) is str
            and completion["source_generation"] == source.get("source_generation")
            and type(completion.get("portable_generation")) is str
            and completion["portable_generation"] != completion["source_generation"],
            "alab_verified_returned_source_scope_missing_or_promoted")
    record = {"status": "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED", "source": context, "result": result,
              "recording_errors_before_result": [], "native_operation_retried": False,
              "human_acceptance": False, "release_authorized": False}
    expected = {"owner/original-result.json": (hashlib.sha256(custody.json_bytes(record)).hexdigest(), MAX_OTIO)}
    for key, name, maximum in (
        ("initial_project_sha256", "owner/initial.kdenlive", MAX_PROJECT),
        ("edited_project_sha256", "owner/edited.kdenlive", MAX_PROJECT),
        ("original_otio_sha256", "capsule/timeline.otio", MAX_OTIO),
        ("received_otio_sha256", "owner/received.otio", MAX_OTIO),
        ("edited_otio_sha256", "owner/edited.otio", MAX_OTIO),
        ("reopened_edited_otio_sha256", "owner/reopened-edited.otio", MAX_OTIO),
    ):
        require(type(result.get(key)) is str and re.fullmatch(r"[0-9a-f]{64}", result[key]),
                "alab_verified_native_original_digest_missing")
        expected[name] = (result[key], maximum)
    binding = result.get("alab_original_bindings")
    require(type(binding) is dict and set(binding) == {"schema", "members", "source_stage_originals",
                "single_existing_collector", "native_operation_retried"}
            and binding["schema"] == "motionwright.native.alab-original-bindings/1"
            and binding["single_existing_collector"] is True and binding["native_operation_retried"] is False,
            "alab_verified_original_bindings_contract_changed")
    members, limits = binding["members"], _alab_original_member_limits()
    require(type(members) is dict and set(members) == set(limits), "alab_verified_original_members_not_closed")
    known = _alab_expected_original_sha(source, completion)
    known["alab-gui-input.original.json"] = result.get("alab_input_admission_sha256")
    for name, maximum in limits.items():
        row = members[name]
        require(type(row) is dict and set(row) == {"bytes", "sha256"}
                and type(row["bytes"]) is int and 0 <= row["bytes"] <= maximum
                and type(row["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                and (name not in known or row["sha256"] == known[name]),
                "alab_verified_original_member_is_not_the_observed_bounded_member")
        expected["owner/" + name] = (row["sha256"], maximum)
    for rows, numbers, prefix, stem in (
        (binding["source_stage_originals"], range(7), "alab-source/", "alab-adoption-receipt"),
        (completion.get("stage_originals"), (7, 8), "", "alab-returned-source-receipt"),
    ):
        require(type(rows) is list and len(rows) == len(numbers), "alab_verified_original_stage_roster_changed")
        for number, row in zip(numbers, rows):
            filename = f"{stem}.stage-{number:02}.result.original.json"
            require(type(row) is dict and type(row.get("index")) is int and row["index"] == number
                    and row.get("file") == filename
                    and members[prefix + filename] == {"bytes": row.get("bytes"), "sha256": row.get("sha256")},
                    "alab_verified_original_stage_not_bound_to_its_returned_receipt")
    relocation = result.get("capsule_relocation")
    require(type(relocation) is dict and relocation.get("schema") == CAPSULE_RELOCATION_SCHEMA
            and relocation.get("mode") == "relative_reference_resolution_after_physical_relocation"
            and all(relocation.get(key) is True for key in ("original_location_absent", "original_bytes_preserved",
                "relative_media_members_preserved"))
            and all(relocation.get(key) is False for key in ("ui_relink_dialog_used", "native_application_launched_here",
                "source_origin_inferred", "alab_sources_adopted_here", "automatic_retry", "human_acceptance", "release_authorized"))
            and type(relocation.get("receipt_sha256")) is str
            and re.fullmatch(r"[0-9a-f]{64}", relocation["receipt_sha256"]),
            "alab_verified_relocation_missing_or_promoted")
    expected["owner/capsule-relocation.original.json"] = (relocation["receipt_sha256"], MAX_OTIO)
    moved = relocation.get("members")
    media = {"media/" + row[3] + ".mov": {"bytes": row[2], "sha256": row[3]}
             for row in media_bridge._ALAB_RETAINED_MEDIA}
    names = set(ALAB_CAPSULE_METADATA_NAMES) | {ALAB_PORTABLE_ZIP} | set(media)
    require(type(moved) is dict and set(moved) == names and len(names) == 12,
            "alab_verified_relocated_capsule_roster_changed")
    for name in sorted(names):
        maximum = (MAX_ALAB_PORTABLE_ZIP if name == ALAB_PORTABLE_ZIP
                   else readback.MAX_MEDIA if name in media else MAX_OTIO)
        row = moved[name]
        require(type(row) is dict and set(row) == {"bytes", "sha256"}
                and type(row["bytes"]) is int and 0 < row["bytes"] <= maximum
                and type(row["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                and (name not in media or row == media[name]), "alab_verified_relocated_original_changed")
        member = "capsule/" + name
        require(member not in expected or expected[member] == (row["sha256"], maximum),
                "alab_verified_relocation_disagrees_with_native_input")
        expected[member] = (row["sha256"], maximum)
    for _, name, size, sha in owner_cut._ALAB_SOURCE_NOTICES:
        require(moved[name] == {"bytes": size, "sha256": sha}, "alab_verified_original_notice_not_retained")
    require(moved["bridge-manifest.json"]["sha256"] == source.get("original_portable_manifest_sha256"),
            "alab_verified_relocated_manifest_differs_from_source_adoption")
    return expected


def main(argv=None) -> int:
    global custody
    import ci_private_evidence as custody
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("appimage", "appdir", "evidence-root", "capsule",
                 "public-dir", "recipient", "distribution-evidence"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--alab-inputs", type=Path,
                   help="Explicit run-bound original ALab profile; omitted for the default three-scene fixture")
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
        alab_inputs = None
        if args.alab_inputs is not None:
            require(args.verification_scope == "kdenlive-gui", "alab_profile_requires_the_existing_complete_gui_scope")
            alab_inputs, original_input = load_alab_gui_inputs(args.alab_inputs, context, runner_temp)
            custody.new_bytes(records.root / "alab-gui-input.original.json", original_input)
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
        selected_kwargs = {} if alab_inputs is None else {"alab_inputs": alab_inputs}
        status, result = run_selected_native(
            args.verification_scope, args.appdir, binary, binary_sha,
            args.capsule, records, profile, **selected_kwargs)
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
