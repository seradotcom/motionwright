#!/usr/bin/env python3
"""Rootless, twice-launched *installed* AppImage SQLite persistence acceptance.

Uses the existing native Xvfb WINDOW_OBSERVED probe for two distinct
processes sharing only a disposable XDG app-data directory. The launch
stays alive until a read-only SQLite query verifies the committed first
project; final snapshots are rechecked after process exit. This is not a
human installation, Semwright renderer, or visual editing acceptance.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
from dataclasses import dataclass
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import shutil
import sqlite3
from urllib.parse import quote
import uuid

from linux_gui_probe import run_probe
import storage_schema_contract as storage

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
MAX_DB_BYTES = 128 * 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_TRAVERSAL_ENTRIES = 1500
EXPECTED_SCHEMA = 3
EXPECTED_INITIAL_TITLE = "motionwright ci first use"
EXPECTED_DATABASE_NAME = "motionwright.sqlite3"


class ColdStartError(ValueError):
    """CI probe failure without leaking user media, paths or database JSON."""


@dataclass(frozen=True)
class ProjectSnapshot:
    id: str
    generation: str
    revision: int
    title: str
    document_sha256: str
    schema_version: int
    event_count: int


def _safe_database(data_root: Path) -> Path:
    if data_root.is_symlink() or not data_root.is_dir():
        raise ColdStartError("Fresh Linux XDG app-data root is missing or symlinked")
    canonical_root = data_root.resolve(strict=True)
    found: list[Path] = []
    inspected = 0
    for current, folders, files in os.walk(data_root, topdown=True, followlinks=False):
        inspected += len(folders) + len(files)
        if inspected > MAX_TRAVERSAL_ENTRIES:
            raise ColdStartError("Fresh app-data discovery exceeded a bounded file budget")
        for folder in folders:
            if (Path(current) / folder).is_symlink():
                raise ColdStartError("Fresh app-data contains an unsafe directory symlink")
        for name in files:
            candidate = Path(current) / name
            if candidate.is_symlink():
                raise ColdStartError("Fresh app-data contains an unsafe file symlink")
            if name == EXPECTED_DATABASE_NAME:
                if not candidate.resolve(strict=True).is_relative_to(canonical_root):
                    raise ColdStartError("Project database escaped its disposable XDG root")
                found.append(candidate)
    if len(found) != 1:
        raise ColdStartError("Installed app did not create exactly one local project database")
    candidate = found[0]
    metadata = candidate.stat()
    if not candidate.is_file() or not 0 < metadata.st_size <= MAX_DB_BYTES:
        raise ColdStartError("Installed project database is absent or exceeds the byte budget")
    return candidate


def read_snapshot(data_root: Path, *, storage_cohort=None) -> ProjectSnapshot:
    """Independently inspect committed SQLite after a terminated GUI process."""
    expected_schema = storage.expected_schema(storage_cohort, historical_default=EXPECTED_SCHEMA)
    database = _safe_database(data_root)
    uri = "file:" + quote(str(database.resolve(strict=True)), safe="/") + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=3)) as conn:
            conn.execute("PRAGMA query_only=ON")
            if conn.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                raise ColdStartError("Installed project database failed SQLite integrity check")
            schema = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema != expected_schema:
                raise ColdStartError("Installed database schema is not the supported native schema")
            if storage_cohort is not None:
                storage.validate_receipt_columns(
                    conn.execute("PRAGMA main.table_xinfo(\'receipts\')").fetchall(), storage_cohort)
            rows = conn.execute(
                "SELECT id,generation,revision,title,document_json "
                "FROM projects ORDER BY id LIMIT 2"
            ).fetchall()
            if len(rows) != 1:
                raise ColdStartError("First boot did not persist exactly one project")
            project_id, generation, revision, title, document = rows[0]
            if title != EXPECTED_INITIAL_TITLE:
                raise ColdStartError("First boot created an unexpected default project")
            try:
                uuid.UUID(project_id)
                uuid.UUID(generation)
            except (ValueError, AttributeError, TypeError) as error:
                raise ColdStartError("Persisted project has malformed native identity") from error
            if type(revision) is not int or revision < 0:
                raise ColdStartError("Persisted project revision is invalid")
            if not isinstance(document, str) or not 0 < len(document.encode()) <= MAX_DOCUMENT_BYTES:
                raise ColdStartError("Persisted creative project document is invalid or too large")
            decoded = json.loads(document)
            if storage_cohort is not None:
                storage.validate_document(decoded, storage_cohort)
            if (
                not isinstance(decoded, dict)
                or decoded.get("id") != project_id
                or decoded.get("generation") != generation
                or decoded.get("revision") != revision
                or decoded.get("title") != title
            ):
                raise ColdStartError("SQLite project columns disagree with the stored domain project")
            event_count = conn.execute("SELECT count(*) FROM events").fetchone()[0]
            receipt_count = conn.execute(
                "SELECT count(*) FROM production_receipts"
            ).fetchone()[0]
            if receipt_count:
                raise ColdStartError("Fresh offline project unexpectedly contains native render receipts")
            if type(event_count) is not int or event_count < 0:
                raise ColdStartError("Initial creative event count is invalid")
            return ProjectSnapshot(
                id=project_id,
                generation=generation,
                revision=revision,
                title=title,
                document_sha256=hashlib.sha256(document.encode("utf-8")).hexdigest(),
                schema_version=schema,
                event_count=event_count,
            )
    except storage.StorageContractError as error:
        raise ColdStartError(str(error)) from error
    except sqlite3.DatabaseError as error:
        raise ColdStartError("Installed project database could not be inspected read-only") from error


def attest_persistence(first: ProjectSnapshot, second: ProjectSnapshot) -> dict:
    """The second launch must not replace/edit the project on its own."""
    if first != second:
        raise ColdStartError(
            "Second installed-app launch replaced or mutated the persisted project"
        )
    return {
        "status": "PASS",
        "one_project_after_both_launches": True,
        "same_project_id": True,
        "same_generation": True,
        "same_revision": True,
        "same_domain_document_sha256": True,
        "same_event_count": True,
        "sqlite_integrity_checked_twice": True,
        "schema_version": first.schema_version,
        "project_identity_sha256": hashlib.sha256(first.id.encode()).hexdigest(),
        "generation_sha256": hashlib.sha256(first.generation.encode()).hexdigest(),
        "document_sha256": first.document_sha256,
        "revision": first.revision,
        "event_count": first.event_count,
        "native_receipts": 0,
    }


def _bootstrap_committed(scratch: Path, *, storage_cohort=None) -> bool:
    """Read-only readiness check while the observed native window is alive.

    Window creation can precede Tauri setup and its SQLite transaction.
    Do not terminate the first launch merely because X11 has a title; demand
    the complete committed project, schema and receipt-free offline store.
    The final closed-process snapshot is still independently rechecked.
    """
    try:
        read_snapshot(scratch / "data", **({"storage_cohort": storage_cohort} if storage_cohort is not None else {}))
    except ColdStartError:
        return False
    return True


def _empty_library_ready(scratch: Path, *, storage_cohort=None) -> bool:
    """Observe schema and zero projects before the one real UI mutation."""
    expected_schema = storage.expected_schema(storage_cohort, historical_default=EXPECTED_SCHEMA)
    try:
        database = _safe_database(scratch / "data")
        uri = "file:" + quote(str(database.resolve(strict=True)), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=3)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            if storage_cohort is not None:
                storage.validate_receipt_columns(
                    connection.execute("PRAGMA main.table_xinfo(\'receipts\')").fetchall(), storage_cohort)
            return (
                connection.execute("PRAGMA quick_check(1)").fetchone() == ("ok",)
                and connection.execute("PRAGMA user_version").fetchone() == (expected_schema,)
                and connection.execute("SELECT COUNT(*) FROM projects").fetchone() == (0,)
            )
    except (ColdStartError, storage.StorageContractError, sqlite3.Error, OSError):
        return False


def _send_native_x11_create() -> None:
    """Send a single real keyboard activation to the accessible modal.

    ProjectLibraryDialog focuses its heading and traps Shift+Tab to its last
    enabled control, the labeled title input (Create is disabled until title).
    The subsequent Tab/Return uses the native WebKit/Xvfb event path.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("DISPLAY"):
        raise ColdStartError("Native first-use keyboard requires disposable Xvfb CI")
    observed = subprocess.run(
        ["xwininfo", "-root", "-tree"], capture_output=True, text=True,
        timeout=5, check=False,
    )
    if observed.returncode:
        raise ColdStartError("Could not inspect native installed X11 window")
    ids = re.findall(r'^\s*(0x[0-9A-Fa-f]+)\s+"Motionwright":',
                     observed.stdout, re.MULTILINE)
    if len(ids) != 1:
        raise ColdStartError("Expected exactly one original Motionwright X11 window")
    x11_name = ctypes.util.find_library("X11") or "libX11.so.6"
    xtst_name = ctypes.util.find_library("Xtst") or "libXtst.so.6"
    try:
        x11 = ctypes.CDLL(x11_name)
        xtst = ctypes.CDLL(xtst_name)
    except OSError as error:
        raise ColdStartError("X11 XTest runtime unavailable on disposable runner") from error
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XRaiseWindow.restype = ctypes.c_int
    x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    x11.XSetInputFocus.restype = ctypes.c_int
    x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x11.XStringToKeysym.restype = ctypes.c_ulong
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_ubyte
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    xtst.XTestFakeKeyEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong,
    ]
    xtst.XTestFakeKeyEvent.restype = ctypes.c_int
    display = x11.XOpenDisplay(None)
    if not display:
        raise ColdStartError("Xvfb display could not be opened for the native GUI action")
    try:
        window_id = int(ids[0], 16)
        x11.XRaiseWindow(display, window_id)
        x11.XSetInputFocus(display, window_id, 1, 0)
        x11.XSync(display, 0)
        time.sleep(1.2)
        def key(name: str, pressed: bool) -> None:
            symbol = x11.XStringToKeysym(name.encode("ascii"))
            code = x11.XKeysymToKeycode(display, symbol)
            if not code or not xtst.XTestFakeKeyEvent(display, code, int(pressed), 0):
                raise ColdStartError("Native X11 project-creation key event was rejected")
            x11.XSync(display, 0)
        def stroke(name: str) -> None:
            key(name, True)
            key(name, False)
            time.sleep(0.035)
        # The dialog focuses its h2. First Shift+Tab wraps to the footer
        # Close button; the second reaches the labeled Project title input
        # because the Create button is disabled before a title is entered.
        # A single Shift+Tab would type into the footer and never create.
        key("Shift_L", True)
        stroke("Tab")
        stroke("Tab")
        key("Shift_L", False)
        for char in EXPECTED_INITIAL_TITLE:
            stroke("space" if char == " " else char)
        stroke("Tab")
        stroke("Return")
    finally:
        x11.XCloseDisplay(display)


def _first_project_window_interaction(scratch: Path, *, storage_cohort=None) -> bool:
    options = {"storage_cohort": storage_cohort} if storage_cohort is not None else {}
    if not _empty_library_ready(scratch, **options):
        return False
    # No retry follows this point even if acknowledgement is lost. The
    # installed application and SQLite journal remain authoritative.
    _send_native_x11_create()
    return True


def probe_cold_relaunch(
    *, app_run: Path, scratch: Path, source_sha: str,
    semwright_sha: str, seconds: int = 24, storage_cohort=None,
) -> dict:
    """Run the *same* extracted AppImage twice, never repoint its XDG data."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ColdStartError("Packaged cold-start acceptance runs on disposable CI only")
    if not SHA40.fullmatch(source_sha) or not SHA40.fullmatch(semwright_sha):
        raise ColdStartError("Cold-start probe requires exact repository source identities")
    if not 2 <= seconds <= 45:
        raise ColdStartError("Installed GUI probe requires a bounded launch deadline")
    if scratch.exists() and any(scratch.iterdir()):
        raise ColdStartError("Cold-start scratch must be empty before the first launch")
    options = {}
    ready, interaction = _bootstrap_committed, _first_project_window_interaction
    if storage_cohort is not None:
        from functools import partial
        storage_cohort = storage.validate_cohort(storage_cohort, source_sha=source_sha)
        options = {"storage_cohort": storage_cohort}
        ready = partial(_bootstrap_committed, **options)
        interaction = partial(_first_project_window_interaction, **options)
    first_gui = run_probe(
        app_run, scratch, seconds=seconds, ready_check=ready,
        window_interaction=interaction,
    )
    if first_gui.get("status") != "WINDOW_OBSERVED":
        raise ColdStartError("First installed run created no observed desktop window")
    first_log = scratch / "app-run.log"
    if first_log.is_file() and not first_log.is_symlink():
        shutil.copyfile(first_log, scratch / "app-run-first.log")
    initial = read_snapshot(scratch / "data", **options)

    second_gui = run_probe(
        app_run, scratch, seconds=seconds, ready_check=ready,
    )
    if second_gui.get("status") != "WINDOW_OBSERVED":
        raise ColdStartError("Reopened installed run created no observed desktop window")
    if first_log.is_file() and not first_log.is_symlink():
        shutil.copyfile(first_log, scratch / "app-run-second.log")
    reopened = read_snapshot(scratch / "data", **options)

    return {
        "schema": ("motionwright.ci.installed-cold-relaunch/1" if storage_cohort is None
                   else "motionwright.ci.installed-cold-relaunch/2"),
        **({"storage_cohort": storage_cohort} if storage_cohort is not None else {}),
        "status": "WINDOW_OBSERVED",
        "window_title": "Motionwright",
        "motionwright_sha": source_sha,
        "semwright_sha": semwright_sha,
        "scope": "extracted_unsigned_linux_appimage_rootless_xvfb",
        "launch_count": 2,
        "first_launch": first_gui,
        "reopened_launch": second_gui,
        "persistence": attest_persistence(initial, reopened),
        "claims": {
            "semwright_production_runtime": "NOT_CONFIGURED",
            "native_renderer_execution": "NOT_RUN",
            "human_install_acceptance": "NOT_RUN",
            "human_creative_acceptance": "NOT_RUN",
            "fresh_physical_device": "NOT_PROVEN",
            "signed_distribution": "NOT_CLAIMED",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-run", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-lock", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=24)
    parser.add_argument("--storage-source-repo", type=Path)
    args = parser.parse_args()
    source = json.loads(args.source_lock.read_text(encoding="utf-8"))
    pin = source["dependencies"]["semwright"]["revision"]
    cohort = (storage.admit_checkout(args.storage_source_repo, args.source_sha)
              if args.storage_source_repo is not None else None)
    report = probe_cold_relaunch(
        app_run=args.app_run, scratch=args.scratch, source_sha=args.source_sha,
        semwright_sha=pin, seconds=args.seconds, storage_cohort=cohort,
    )
    if cohort is not None and storage.admit_checkout(args.storage_source_repo, args.source_sha) != cohort:
        raise ColdStartError("Storage source changed during installed cold relaunch")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, indent=2, sort_keys=True)
        out.write("\n")
    print(
        "candidate-first-use=" + report["persistence"]["status"]
        + " launches=2 fresh-offline-project=retained"
        + " human-acceptance=NOT_RUN"
    )


if __name__ == "__main__":
    main()
