"""Observe one saved-project edit and reopen in an already recovered Windows app.

This oracle never installs, repairs, compiles, retries a save, or grants release
acceptance. Its input is a pinned set of original compile/lifecycle receipts.
Synthetic controls do not constitute an installed application execution.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

import windows_cold_start as cold
import windows_installer_lifecycle as life
from nsis_owned_files import (
    _capture, _check_path_syntax, _require_plain_parents, _require_regular,
    emit_predecessor_include,
)
from release_manifest import write_new_manifest

SCHEMA = "motionwright.windows-updated-application/1"
INPUT_SCHEMA = "motionwright.windows-updated-application-input/1"
UI_SCHEMA = "motionwright.windows-updated-application-ui/1"
FAMILY = ("motionwright.sqlite3", "motionwright.sqlite3-wal", "motionwright.sqlite3-shm")
MAX_ROWS = 4096
MAX_JSON = 16 * 1024 * 1024
SOURCE_PATHS = (
    "tooling/windows_updated_application.py", "tooling/windows_updated_application_ui.ps1",
    "tooling/tests/test_windows_updated_application.py",
    "tooling/tests/test_windows_updated_application_ui_contract.py",
    "docs/reliability/windows-updated-application.md",
    "tooling/windows_cold_start.py", "tooling/windows_installer_lifecycle.py",
    "tooling/nsis_owned_files.py", "tooling/release_manifest.py",
)
PRODUCT_PATHS = (
    "apps/studio/src/App.tsx", "apps/studio/src/CreativeWorkspaces.tsx",
    "apps/studio/src/BriefWorkspace.tsx", "apps/studio/src/briefDraft.ts",
    "apps/studio/src/ProjectLibraryDialog.tsx", "apps/studio/src/projectLibrary.ts",
    "crates/storage/src/lib.rs", "crates/domain/src/lib.rs", "crates/domain/src/history.rs",
    "Cargo.lock", "SOURCE_LOCK.json",
)
INPUT_REFS = (
    "compile_summary", "normal_setup", "opt_in_setup", "current_main",
    "predecessor_profile", "checkpoint", "interruption_context",
    "interruption_terminal", "recovery_terminal", "before_snapshot", "recovered_snapshot",
)


class OracleError(ValueError):
    """A closed oracle contract failed; no result may authorize a replay."""


def require(ok, code):
    if not ok:
        raise OracleError(code)


def exact(value, fields, code):
    require(isinstance(value, dict) and set(value) == set(fields), code)
    return value


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "duplicate_json_key")
            value[key] = item
        return value
    def nonfinite(_):
        raise OracleError("nonfinite_json")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def read_json(root, reference):
    path = life.bound_file(root, reference)
    require(0 < reference["bytes"] <= MAX_JSON, "json_size_bound")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == reference["sha256"]
            and life.file_identity(path) == life.ref_identity(reference), "json_changed")
    return strict_json(raw)


def absolute_plain(value):
    require(isinstance(value, str), "path_not_text")
    _check_path_syntax(value)
    path = Path(value)
    require(path.is_absolute(), "path_not_absolute")
    _require_plain_parents(path)
    return path


def known_context(run, environment):
    life.run_identity(run)
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("RUNNER_OS") == "Windows", "disposable_windows_required")
    require(environment.get("GITHUB_REPOSITORY") == run["repository"]
            and environment.get("GITHUB_RUN_ID") == str(run["run_id"])
            and environment.get("GITHUB_RUN_ATTEMPT") == str(run["run_attempt"]),
            "lifecycle_run_mismatch")


def input_shape(value):
    exact(value, {"schema", "operation_id", "run", "product_sha", "gate_sha",
                  "version", "refs", "registry_keys", "install_root", "database",
                  "executable", "project_id", "generation", "revision",
                  "old_title", "new_title"}, "input_fields")
    require(value["schema"] == INPUT_SCHEMA, "input_schema")
    for key in ("operation_id", "project_id", "generation"):
        life.operation_id(value[key])
    for key in ("product_sha", "gate_sha"):
        life.source_id(value[key])
    life.run_identity(value["run"])
    life.version(value["version"])
    exact(value["refs"], INPUT_REFS, "input_references")
    for reference in value["refs"].values():
        life.ref_identity(reference)
    require(type(value["revision"]) is int and 0 <= value["revision"] < 2**53 - 1,
            "revision_invalid")
    require(isinstance(value["old_title"], str) and 0 < len(value["old_title"].encode()) <= 200,
            "old_title_invalid")
    require(isinstance(value["new_title"], str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,95}", value["new_title"])
            and value["new_title"] == value["new_title"].strip()
            and value["new_title"] != value["old_title"], "new_title_invalid")
    keys = value["registry_keys"]
    require(isinstance(keys, list) and 1 <= len(keys) <= 16
            and len(set(keys)) == len(keys) and all(isinstance(x, str) and x for x in keys),
            "registry_keys_invalid")
    install, database, executable = (absolute_plain(value[k])
                                     for k in ("install_root", "database", "executable"))
    require(database.name == FAMILY[0] and executable.parent == install
            and executable.suffix.lower() == ".exe"
            and not database.is_relative_to(install), "installed_paths_invalid")
    return value


def admit_originals(root, value, gate):
    """Read originals using the canonical gate and historical custody primitives."""
    input_shape(value)
    refs = value["refs"]
    for reference in refs.values():
        life.bound_file(root, reference)
    compiled = read_json(root, refs["compile_summary"])
    require(compiled.get("schema") == "motionwright.nsis-interruption-compile/1"
            and compiled.get("compile") == "TWO_VARIANTS_COMPILED"
            and compiled.get("product_sha") == value["product_sha"]
            and compiled.get("gate_sha") == value["gate_sha"], "current_compile_missing")
    require(compiled.get("source_before") == compiled.get("source_after")
            and compiled["source_before"]["product"]["sha"] == value["product_sha"]
            and compiled["source_before"]["gate"]["sha"] == value["gate_sha"],
            "compile_source_changed")
    main = life.ref_identity(refs["current_main"])
    require(compiled.get("real_application") == main, "compiled_current_main_mismatch")
    variants = compiled.get("variants", [])
    require(isinstance(variants, list) and [x.get("variant") for x in variants] == ["normal", "opt-in"],
            "compile_variants_missing")
    for variant, key in zip(variants, ("normal_setup", "opt_in_setup")):
        require(variant.get("result") == "COMPILE_ONLY"
                and variant.get("setup") == life.ref_identity(refs[key])
                and variant.get("main_capture") == main, "compiled_variant_mismatch")
    require(refs["normal_setup"]["sha256"] != refs["opt_in_setup"]["sha256"],
            "setup_variants_not_distinct")
    checkpoint = gate.parse_checkpoint(life.bound_file(root, refs["checkpoint"]).read_bytes())
    context = read_json(root, refs["interruption_context"])
    interrupted = read_json(root, refs["interruption_terminal"])
    gate.validate_live_context(checkpoint, context)
    gate.validate_termination(checkpoint, context, interrupted)
    require(context["product_sha"] == value["product_sha"]
            and context["gate_sha"] == value["gate_sha"]
            and context["setup_sha256"] == refs["opt_in_setup"]["sha256"]
            and context["predecessor_profile_sha256"] == refs["predecessor_profile"]["sha256"]
            and context["candidate_sha256"] == main["sha256"]
            and context["candidate_size"] == str(main["bytes"]), "interruption_payload_mismatch")
    require(context["run_id"] == str(value["run"]["run_id"])
            and context["run_attempt"] == str(value["run"]["run_attempt"]),
            "interruption_run_mismatch")
    interrupted_id = str(uuid.UUID(context["id"].strip("{}")))
    recovery = read_json(root, refs["recovery_terminal"])
    exact(recovery, {"schema", "phase", "context", "original_terminal"}, "recovery_terminal_fields")
    require(recovery["schema"] == "motionwright.windows-lifecycle-terminal/1"
            and recovery["phase"] == "upgrade", "recovery_terminal_schema")
    binding = exact(recovery["context"], {"operation_id", "process", "executable",
                       "source_sha", "run"}, "recovery_context_fields")
    recovery_id = life.operation_id(binding["operation_id"])
    require(len({value["operation_id"], interrupted_id, recovery_id}) == 3,
            "operation_identity_reused")
    life.validate_terminal(recovery["original_terminal"])
    require(recovery["original_terminal"] == life.observed_terminal(0),
            "recovery_not_known_success")
    exact(binding["process"], {"pid", "creation_filetime"}, "recovery_process_missing")
    require(all(type(n) is int and n > 0 for n in binding["process"].values()),
            "recovery_process_invalid")
    require(binding["process"] != {"pid": int(context["pid"]),
                "creation_filetime": int(context["creation_filetime"])},
            "recovery_reuses_interrupted_process")
    require(binding["source_sha"] == value["product_sha"] and binding["run"] == value["run"]
            and binding["executable"] == life.ref_identity(refs["normal_setup"]),
            "recovery_payload_or_run_mismatch")
    before = life.snapshot_value(root, refs["before_snapshot"], interrupted_id, "before",
                                 value["registry_keys"])
    recovered = life.snapshot_value(root, refs["recovered_snapshot"], recovery_id, "after",
                                    value["registry_keys"])
    for snapshot in (before, recovered):
        require(snapshot["database"] == value["database"]
                and snapshot["install_root"] == value["install_root"],
                "snapshot_profile_path_changed")
        require(snapshot["sqlite_family"][FAMILY[0]] is not None, "preserved_database_missing")
    require(life.family_map(before) == life.family_map(recovered), "installer_changed_project_family")
    filename = Path(value["executable"]).name
    require(filename in recovered["installed"]
            and life.ref_identity(recovered["installed"][filename]) == main,
            "recovery_did_not_capture_current_application")
    require(filename in before["installed"]
            and life.ref_identity(before["installed"][filename]) != main,
            "predecessor_equals_current_application")
    profile = read_json(root, refs["predecessor_profile"])
    require(profile.get("schema") == "motionwright.nsis-predecessor/1"
            and profile.get("target") == {"version": value["version"], "main_sha256": main["sha256"]},
            "predecessor_target_mismatch")
    records = profile.get("files")
    require(isinstance(records, list) and records, "predecessor_inventory_missing")
    old = {x["relative"]: {"bytes": x["bytes"], "sha256": x["sha256"]} for x in records}
    before_files = life.file_map(before)
    require(len(old) == len(records)
            and all(before_files.get(path) == identity for path, identity in old.items()),
            "predecessor_inventory_not_original_snapshot")
    foreign = {path: identity for path, identity in before_files.items() if path not in old}
    recovered_files = life.file_map(recovered)
    require(all(recovered_files.get(path) == identity for path, identity in foreign.items()),
            "foreign_inventory_not_preserved")
    return {"compile": compiled, "interruption_id": interrupted_id, "recovery_id": recovery_id,
            "before": before, "recovered": recovered, "profile": profile, "main": main}


def verify_live_family(database, family, root):
    exact(family, FAMILY, "family_fields")
    require(family[FAMILY[0]] is not None, "database_absent")
    for name in FAMILY:
        path = database.parent / name
        observed = life.optional_regular_stamp(path)
        reference = family[name]
        if reference is None:
            require(observed is None, "unexpected_sqlite_sidecar")
        else:
            require(observed is not None, "sqlite_family_member_missing")
            life.bound_file(root, reference)
            require(life.file_identity(path) == life.ref_identity(reference), "live_family_changed")


def inspect_project(database, project_id, allowed_storage=(2, 3)):
    """Read only an inspection copy; never bypass WAL recovery with immutable=1."""
    _require_plain_parents(database)
    _require_regular(database.lstat(), "inspection database")
    require(0 < database.stat().st_size < 64 * 1024 * 1024, "database_size_bound")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
        db.execute("PRAGMA query_only=ON")
        require(db.execute("PRAGMA quick_check").fetchall() == [("ok",)], "sqlite_integrity")
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        require(schema in allowed_storage, "sqlite_schema_unsupported")
        rows = db.execute("SELECT id,generation,revision,title,document_json FROM projects "
                          "WHERE id=?", (project_id,)).fetchmany(2)
        require(len(rows) == 1, "project_identity_missing_or_duplicate")
        row = rows[0]
        require(isinstance(row[4], str) and len(row[4].encode()) <= MAX_JSON, "project_json_bound")
        document = strict_json(row[4])
        require(isinstance(document, dict) and
                [document.get(k) for k in ("id", "generation", "revision", "title")] == list(row[:4]),
                "project_columns_disagree")
        require(type(document.get("schema_version")) is int
                and document["schema_version"] in (1, 2), "project_schema_unsupported")
        for key in ("id", "generation"):
            life.operation_id(document[key])
        require(type(document["revision"]) is int and document["revision"] >= 0,
                "project_revision_invalid")
        result = {"storage_schema": schema, "document": document, "events": [], "receipts": []}
        for key, query in (
            ("events", "SELECT revision,kind,payload_json,created_at FROM events "
                       "WHERE project_id=? ORDER BY revision"),
            ("receipts", "SELECT request_id,request_sha256,result_json,created_at FROM receipts "
                         "WHERE project_id=? ORDER BY request_id"),
        ):
            values = db.execute(query, (project_id,)).fetchmany(MAX_ROWS + 1)
            require(len(values) <= MAX_ROWS, "journal_or_receipt_bound")
            result[key] = [list(item) for item in values]
    return result


def inspect_retained_family(root, family, directory, project_id, allowed_storage=(2, 3)):
    exact(family, FAMILY, "family_fields")
    require(family[FAMILY[0]] is not None, "database_absent")
    directory.mkdir()
    for name, reference in family.items():
        if reference is not None:
            _capture(life.bound_file(root, reference), directory / name)
    result = inspect_project(directory / FAMILY[0], project_id, allowed_storage)
    for reference in family.values():
        if reference is not None:
            life.bound_file(root, reference)
    return result


def capture_family(output, label, database, project_id):
    directory = output / label
    directory.mkdir()
    originals = directory / "originals"
    originals.mkdir()
    paths = [database.parent / name for name in FAMILY]
    before = {p.name: life.optional_regular_stamp(p) for p in paths}
    require(before[FAMILY[0]] is not None, "database_absent")
    family = {}
    for path in paths:
        if before[path.name] is None:
            family[path.name] = None
        else:
            _capture(path, originals / path.name)
            family[path.name] = life.retained_ref(output, originals / path.name)
    require({p.name: life.optional_regular_stamp(p) for p in paths} == before, "family_changed_during_capture")
    semantic = inspect_retained_family(output, family, directory / "inspection", project_id, (3,))
    value = {"sqlite_family": family, "semantic": semantic}
    write_new_manifest(output, directory / "snapshot.json", value)
    return value


def verify_expected(snapshot, value, edited=False):
    p = snapshot["document"]
    require(p["id"] == value["project_id"] and p["generation"] == value["generation"],
            "preserved_project_identity_changed")
    require(p["revision"] == value["revision"] + int(edited)
            and p["title"] == value["new_title" if edited else "old_title"],
            "expected_project_revision_or_title_changed")


def verify_edit(before, after, reopened, value):
    verify_expected(before, value)
    verify_expected(after, value, True)
    require(after["storage_schema"] == 3 and after["document"]["schema_version"] == 2,
            "updated_application_schema_not_current")
    require(reopened == after, "read_only_reopen_changed_project_or_history")
    old, new = before["document"], after["document"]
    # Existing fields must survive. Schema-1 migration may add fields, never
    # silently rewrite pre-existing content under the guise of this rename.
    def preserved(left, right, path=()):
        if path in {("schema_version",), ("revision",), ("title",), ("updated_at",)}:
            return
        if isinstance(left, dict):
            require(isinstance(right, dict) and set(left) <= set(right)
                    and (old["schema_version"] == 1 or set(left) == set(right)), "project_fields_lost")
            for key, item in left.items():
                preserved(item, right[key], (*path, key))
        elif isinstance(left, list):
            require(isinstance(right, list) and len(left) == len(right), "project_collection_changed")
            for index, item in enumerate(left):
                if path == ("branches",) and isinstance(item, dict):
                    changed = dict(right[index])
                    if item.get("id") == old.get("active_branch"):
                        require(changed.get("head_revision") == value["revision"] + 1,
                                "active_branch_revision_changed")
                        changed["head_revision"] = item.get("head_revision")
                    preserved(item, changed, (*path, str(index)))
                elif path == ("reviews",) and isinstance(item, dict):
                    # commit_revision invalidates prior resolutions on the
                    # active branch. This is the domain's required rename
                    # effect, not permission to alter review text or anchors.
                    anchor = item.get("anchor", {})
                    changed = dict(right[index])
                    if (anchor.get("branch_id") == old.get("active_branch")
                            and type(anchor.get("revision")) is int
                            and anchor["revision"] < value["revision"] + 1
                            and item.get("status") == "resolved"):
                        require(changed.get("status") == "needs_recheck"
                                and changed.get("resolution") is None
                                and changed.get("resolved_at") is None,
                                "review_resolution_not_invalidated")
                        for key in ("status", "resolution", "resolved_at"):
                            changed[key] = item.get(key)
                    preserved(item, changed, (*path, str(index)))
                else:
                    preserved(item, right[index], (*path, str(index)))
        else:
            require(type(left) is type(right) and left == right, "unrelated_project_content_changed")
    preserved(old, new)
    require(after["events"][:-1] == before["events"]
            and len(after["events"]) == len(before["events"]) + 1, "rename_journal_not_one_append")
    event = after["events"][-1]
    require(event[0] == value["revision"] + 1 and event[1] == "change"
            and strict_json(event[2]) == {"type": "rename_project", "title": value["new_title"]},
            "rename_journal_payload_changed")
    added = [row for row in after["receipts"] if row not in before["receipts"]]
    require(len(after["receipts"]) == len(before["receipts"]) + 1 and len(added) == 1
            and all(row in after["receipts"] for row in before["receipts"]), "rename_receipt_not_unique")
    reply = strict_json(added[0][2])
    require(isinstance(added[0][1], str) and life.SHA64.fullmatch(added[0][1])
            and reply.get("project") == new and reply.get("previous_revision") == value["revision"]
            and reply.get("request_id") == added[0][0] and reply.get("replayed") is False,
            "rename_original_receipt_changed")
    return {"revision": new["revision"], "request_id": added[0][0], "one_edit": True,
            "read_only_reopen": True}


def classify_ui(raw, exit_code, timed_out, mode, expected, process):
    """A returned Invoke and matching SQL values are never an original ACK."""
    try:
        require(mode in {"edit", "observe"}, "ui_mode_invalid")
        record = strict_json(raw.decode("utf-8-sig"))
        exact(record, {"schema", "operation_id", "mode", "state", "phase", "project_id",
                       "revision", "title", "ack_marker", "save_attempts",
                       "selection_confirmed", "future_ack_absent", "process"}, "ui_fields")
        require(record["schema"] == UI_SCHEMA and record["operation_id"] == expected["operation_id"]
                and record["mode"] == mode and record["process"] == process, "ui_context_mismatch")
        require(type(record["save_attempts"]) is int and record["save_attempts"] in (0, 1),
                "ui_save_count")
        require(record["project_id"] == expected["project_id"], "ui_project_mismatch")
        if mode == "observe":
            require(not timed_out and exit_code == 0
                    and record["state"] == "OBSERVED" and record["save_attempts"] == 0
                    and record["selection_confirmed"] is True and record["ack_marker"] is None
                    and record["revision"] == expected["revision"] + 1
                    and record["title"] == expected["new_title"], "ui_reopen_incomplete")
        elif record["state"] == "ACK":
            marker = "The original save reply and saved revision r" + str(expected["revision"] + 1) + " agree."
            require(record["save_attempts"] == 1
                    and record["selection_confirmed"] is True and record["future_ack_absent"] is True
                    and record["revision"] == expected["revision"] + 1
                    and record["title"] == expected["new_title"] and record["ack_marker"] == marker,
                    "ui_original_ack_missing")
        elif record["state"] == "NOT_SENT":
            require(exit_code != 0 and record["save_attempts"] == 0, "ui_not_sent_unproved")
        else:
            require(record["state"] == "UNKNOWN", "ui_outcome_unproved")
        return {"state": record["state"],
                "reason": "CONTROLLER_TIMEOUT" if timed_out else record["phase"], "record": record,
                "controller_complete": not timed_out and exit_code == 0}
    except (OracleError, ValueError, UnicodeError, TypeError, KeyError):
        return {"state": "UNKNOWN",
                "reason": "CONTROLLER_TIMEOUT" if timed_out else "CONTROLLER_ORIGINAL_INCOMPLETE",
                "record": None, "controller_complete": False}


def process_identity(process, executable):
    """Read the original Popen handle, never reopen an arbitrary process by PID."""
    require(os.name == "nt" and getattr(process, "_handle", None) is not None,
            "original_windows_process_handle_required")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = wintypes.HANDLE(int(process._handle))
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    require(bool(kernel.GetProcessTimes(handle, *(ctypes.byref(x) for x in values))),
            "original_process_time_unavailable")
    created = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(buffer))
    require(bool(kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length))),
            "original_process_image_unavailable")
    require(Path(buffer.value).resolve(strict=True) == executable.resolve(strict=True)
            and type(process.pid) is int and process.pid > 0 and created > 0,
            "original_process_image_mismatch")
    return {"pid": process.pid, "creation_filetime": created}


def retain_observation(output, name, original, publisher=write_new_manifest):
    """Custody failure preserves the already observed application outcome."""
    copied = strict_json(json.dumps(original, allow_nan=False))
    try:
        publisher(output, output / name, copied)
        return {"original": copied, "custody": "COMPLETE", "errors": []}
    except Exception as error:
        return {"original": copied, "custody": "INCOMPLETE",
                "errors": [life.custody_error("record_publication", error)]}


def run_lifetime(output, label, mode, value, executable, expected_main, environment):
    directory = output / label
    directory.mkdir()
    write_new_manifest(output, directory / "launch-intent.json",
                       {"operation_id": value["operation_id"], "mode": mode,
                        "executable": expected_main, "launch_count": 1})
    result = {"mode": mode, "process": None, "ui": {"state": "NOT_STARTED"},
              "terminal": life.observed_terminal(None), "graceful_close": False,
              "forced_stop": False, "custody_errors": []}
    process = None
    hwnd = None
    try:
        require(life.file_identity(executable) == expected_main, "installed_main_changed_before_launch")
        with (directory / "application.stdout.log").open("xb") as stdout, (
                directory / "application.stderr.log").open("xb") as stderr:
            process = subprocess.Popen([str(executable)], cwd=executable.parent,
                env=environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            original = process_identity(process, executable)
            result["process"] = original
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                require(process.poll() is None, "application_exited_before_owned_window")
                hwnd = cold.windows_motionwright_window(process.pid)
                if hwnd is not None:
                    break
                time.sleep(0.2)
            require(hwnd is not None, "owned_window_deadline")
            require(process_identity(process, executable) == original, "original_process_changed")
            ui_process = {**original, "window_handle": hwnd}
            script = Path(__file__).with_name("windows_updated_application_ui.ps1")
            powershell = Path(environment["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            _require_regular(powershell.lstat(), "PowerShell")
            command = [str(powershell), "-NoProfile", "-NonInteractive", "-File", str(script),
                       "-WindowHandle", str(hwnd), "-ProcessId", str(original["pid"]),
                       "-CreationFiletime", str(original["creation_filetime"]),
                       "-OperationId", value["operation_id"], "-Mode", mode,
                       "-ProjectId", value["project_id"], "-ExpectedRevision", str(value["revision"]),
                       "-OldTitle", value["old_title"], "-NewTitle", value["new_title"]]
            write_new_manifest(output, directory / "ui-intent.json",
                {"operation_id": value["operation_id"], "mode": mode, "process": ui_process,
                 "save_invocation_limit": 1 if mode == "edit" else 0,
                 "script": life.file_identity(script)})
            exit_code, timed_out = None, False
            with (directory / "controller.stdout.log").open("xb") as out, (
                    directory / "controller.stderr.log").open("xb") as err:
                result["ui"] = {"state": "UNKNOWN", "reason": "CONTROLLER_DISPATCH_OUTCOME_UNOBSERVED",
                                "record": None, "controller_complete": False}
                try:
                    terminal = subprocess.run(command, cwd=script.parent, env=environment,
                        stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=90)
                    exit_code = terminal.returncode
                except subprocess.TimeoutExpired:
                    timed_out = True
            raw = (directory / "controller.stdout.log").read_bytes()
            result["ui"] = classify_ui(raw, exit_code, timed_out, mode, value, ui_process)
            result["controller_terminal"] = {"exit_code": exit_code, "timed_out": timed_out}
            saved = retain_observation(output, label + "/ui-original.json", result["ui"])
            result["custody_errors"].extend(saved["errors"])
            require(process.poll() is None and process_identity(process, executable) == original,
                    "application_not_original_live_process")
            cold.graceful_close(hwnd, process)
            result["graceful_close"] = True
            result["terminal"] = life.observed_terminal(process.returncode)
    except Exception as error:
        result["diagnostic"] = type(error).__name__
        if isinstance(error, OracleError):
            result["code"] = str(error)
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    # Cleanup is confined to the retained original handle. It never
                    # authorizes another lifetime or another Save after uncertainty.
                    result["forced_stop"] = True
                    try:
                        process.kill()
                        process.wait(timeout=12)
                    except Exception as error:
                        result["custody_errors"].append(life.custody_error("owned_process_cleanup", error))
                result["terminal"] = life.observed_terminal(process.poll())
            except Exception as error:
                result["custody_errors"].append(life.custody_error("owned_process_observation", error))
        for name in ("application.stdout.log", "application.stderr.log",
                     "controller.stdout.log", "controller.stderr.log"):
            path = directory / name
            try:
                if path.exists():
                    result[name] = life.retained_ref(output, path)
            except Exception as error:
                result["custody_errors"].append(life.custody_error("original_log", error))
    saved = retain_observation(output, label + "/lifetime.json", result)
    result["custody_errors"].extend(saved["errors"])
    return result


def require_lifetime(value, mode):
    require(value.get("mode") == mode and value.get("process") is not None
            and value.get("ui", {}).get("state") == ("ACK" if mode == "edit" else "OBSERVED"),
            "application_ui_outcome_not_confirmed")
    require(value.get("terminal") == life.observed_terminal(0)
            and value.get("graceful_close") is True and value.get("forced_stop") is False
            and value.get("ui", {}).get("controller_complete") is True
            and not value.get("custody_errors"), "application_terminal_or_custody_incomplete")


def load_canonical_gate(repo, source):
    # Reuse the reviewed checkpoint/terminal reader; do not fork its protocol.
    life.source_id(source)
    observed = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, check=True, timeout=15).stdout.decode().strip()
    require(observed == source, "canonical_gate_source_mismatch")
    path = repo / "tooling/windows_nsis_interruption.py"
    original = subprocess.run(["git", "-C", str(repo), "show",
                               source + ":tooling/windows_nsis_interruption.py"],
                              capture_output=True, check=True, timeout=15).stdout
    local = path.read_bytes()
    require(local == original or (b"\r" not in original and local == original.replace(b"\n", b"\r\n")),
            "canonical_gate_bytes_changed")
    spec = importlib.util.spec_from_file_location("updated_app_canonical_nsis_reader", path)
    require(spec is not None and spec.loader is not None, "canonical_gate_import")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observe(args):
    output = absolute_plain(str(args.output))
    root = absolute_plain(str(args.evidence_root))
    product = absolute_plain(str(args.product_repo))
    oracle = Path(__file__).resolve().parents[1]
    gate_root = absolute_plain(str(args.gate_repo))
    for checkout in (product, oracle, gate_root):
        require(not output.is_relative_to(checkout) and not checkout.is_relative_to(output),
                "evidence_overlaps_source")
    require(not output.is_relative_to(root) and not root.is_relative_to(output),
            "output_overlaps_input_evidence")
    output.mkdir()
    report = {"schema": SCHEMA, "status": "INCOMPLETE", "application_result": "NOT_STARTED",
              "operation_id": None, "source_before": None, "source_after": None,
              "lifetimes": [], "custody_errors": [], "installer_execution": "NOT_RUN",
              "human_acceptance": False, "release_authorized": False,
              "scope": "Owned installed-app edit/reopen after externally observed recovery"}
    gate = None
    snapshot = None
    try:
        require(os.name == "nt", "windows_required")
        life.source_id(args.oracle_source)
        life.source_id(args.product_source)
        life.source_id(args.gate_source)
        require(life.SHA64.fullmatch(args.input_sha256), "independent_input_pin_invalid")
        input_path = absolute_plain(str(args.input))
        require(input_path.is_relative_to(root), "input_outside_evidence_root")
        require(life.file_identity(input_path)["sha256"] == args.input_sha256, "independent_input_pin_mismatch")
        reference = life.retained_ref(root, input_path)
        value = input_shape(read_json(root, reference))
        known_context(value["run"], os.environ)
        require(value["product_sha"] == args.product_source
                and value["gate_sha"] == args.gate_source, "requested_source_mismatch")
        report["operation_id"] = value["operation_id"]
        gate = load_canonical_gate(gate_root, args.gate_source)
        snapshot = lambda: {
            "oracle": gate.source_observation(oracle, args.oracle_source, SOURCE_PATHS),
            "gate": gate.source_observation(gate_root, args.gate_source, gate.GATE_PATHS),
            "product": gate.source_observation(product, args.product_source,
                                               tuple(dict.fromkeys((*gate.PRODUCT_PATHS, *PRODUCT_PATHS)))),
        }
        report["source_before"] = snapshot()
        admitted = admit_originals(root, value, gate)
        require(admitted["compile"]["source_before"]["product"]["tree"]
                == report["source_before"]["product"]["tree"], "compiled_product_tree_mismatch")
        executable = absolute_plain(value["executable"])
        database = absolute_plain(value["database"])
        for location in (Path(value["install_root"]), database.parent):
            require(not output.is_relative_to(location) and not location.is_relative_to(output)
                    and not root.is_relative_to(location), "evidence_overlaps_live_profile")
        require(life.file_identity(executable) == admitted["main"], "installed_application_bytes_mismatch")
        pe = life.read_pe_versions(executable)
        life.require_pe_version(pe, value["version"], admitted["main"])
        profile_dir = output / "predecessor-admission"
        profile_dir.mkdir()
        profile = admitted["profile"]
        profile_path = life.bound_file(root, value["refs"]["predecessor_profile"])
        profile_result = emit_predecessor_include(profile_dir / "predecessor.nsh",
            application="Motionwright", bundle_id="com.seradotcom.motionwright", arch="x64",
            target_version=value["version"], target_main_sha256=admitted["main"]["sha256"],
            environment={"MW_NSIS_PREDECESSOR_MANIFEST": str(profile_path),
                         "MW_NSIS_PREDECESSOR_MANIFEST_SHA256": value["refs"]["predecessor_profile"]["sha256"]})
        require(profile_result["enabled"] is True, "predecessor_profile_not_admitted")
        family = admitted["recovered"]["sqlite_family"]
        verify_live_family(database, family, root)
        before = inspect_retained_family(root, family, output / "before-inspection", value["project_id"])
        verify_expected(before, value)
        write_new_manifest(output, output / "admitted-input.json", {
            "input": reference, "input_pin": args.input_sha256, "operation_id": value["operation_id"],
            "run": value["run"], "original_recovery_id": admitted["recovery_id"],
            "profile": profile_result, "installed_pe": pe, "before": before,
            "external_recovery_reexecuted": False, "human_acceptance": False})
        environment = cold.native_windows_launch_environment(dict(os.environ))
        for key in ("GH_TOKEN", "GITHUB_TOKEN"):
            environment.pop(key, None)
        first = run_lifetime(output, "first", "edit", value, executable, admitted["main"], environment)
        report["lifetimes"].append(first)
        report["application_result"] = first["ui"]["state"]
        # Retain a quiescent observation even after an unconfirmed save, but
        # never infer ACK or launch the second lifetime from matching SQL alone.
        if first["terminal"] == life.observed_terminal(0) and first["graceful_close"]:
            report["after_edit"] = capture_family(output, "after-edit", database, value["project_id"])
        require_lifetime(first, "edit")
        verify_expected(report["after_edit"]["semantic"], value, True)
        require(life.file_identity(executable) == admitted["main"], "installed_main_changed_after_edit")
        second = run_lifetime(output, "second", "observe", value, executable, admitted["main"], environment)
        report["lifetimes"].append(second)
        require_lifetime(second, "observe")
        require(first["process"] != second["process"], "application_lifetime_reused")
        report["after_reopen"] = capture_family(output, "after-reopen", database, value["project_id"])
        report["invariants"] = verify_edit(before, report["after_edit"]["semantic"],
                                           report["after_reopen"]["semantic"], value)
        require(life.file_identity(executable) == admitted["main"], "installed_main_changed_after_reopen")
        for observation in (report["after_edit"], report["after_reopen"]):
            for reference in observation["sqlite_family"].values():
                if reference is not None:
                    life.bound_file(output, reference)
        report["source_after"] = snapshot()
        require(report["source_before"] == report["source_after"], "source_changed_during_observation")
        report["status"] = "SOURCE_BOUND_UPDATED_APP_OBSERVATIONS"
    except Exception as error:
        report["diagnostic"] = type(error).__name__
        if isinstance(error, (OracleError, life.LifecycleError)):
            report["code"] = str(error)
    finally:
        if snapshot is not None and report["source_after"] is None:
            try:
                report["source_after"] = snapshot()
            except Exception as error:
                report["custody_errors"].append(life.custody_error("source_after", error))
    saved = retain_observation(output, "receipt.json", report)
    if saved["custody"] != "COMPLETE":
        print(json.dumps({"status": "INCOMPLETE", "application_result": report["application_result"],
                          "custody": saved["custody"], "human_acceptance": False}))
        return 1
    print(json.dumps({key: report[key] for key in ("status", "application_result", "human_acceptance")}))
    return 0 if report["status"] == "SOURCE_BOUND_UPDATED_APP_OBSERVATIONS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("product-repo", "gate-repo", "input", "evidence-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("product-source", "gate-source", "oracle-source", "input-sha256"):
        parser.add_argument("--" + name, required=True)
    return observe(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
