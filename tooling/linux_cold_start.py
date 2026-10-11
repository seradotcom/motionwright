#!/usr/bin/env python3
"""Rootless, twice-launched *installed* AppImage SQLite persistence acceptance.

Uses the existing native Xvfb WINDOW_OBSERVED probe for two distinct
processes sharing only a disposable XDG app-data directory. All SQLite
inspection is query-only after the app is terminated. This is not a
human installation, Semwright renderer, or visual editing acceptance.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from urllib.parse import quote
import uuid

from linux_gui_probe import run_probe

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
MAX_DB_BYTES = 128 * 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_TRAVERSAL_ENTRIES = 1500
EXPECTED_SCHEMA = 2
EXPECTED_INITIAL_TITLE = "Untitled Motion Project"
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


def read_snapshot(data_root: Path) -> ProjectSnapshot:
    """Independently inspect committed SQLite after a terminated GUI process."""
    database = _safe_database(data_root)
    uri = "file:" + quote(str(database.resolve(strict=True)), safe="/") + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=3) as conn:
            conn.execute("PRAGMA query_only=ON")
            if conn.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                raise ColdStartError("Installed project database failed SQLite integrity check")
            schema = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema != EXPECTED_SCHEMA:
                raise ColdStartError("Installed database schema is not the supported native schema")
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


def probe_cold_relaunch(
    *, app_run: Path, scratch: Path, source_sha: str,
    semwright_sha: str, seconds: int = 24,
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
    first_gui = run_probe(app_run, scratch, seconds=seconds)
    if first_gui.get("status") != "WINDOW_OBSERVED":
        raise ColdStartError("First installed run created no observed desktop window")
    first_log = scratch / "app-run.log"
    if first_log.is_file() and not first_log.is_symlink():
        shutil.copyfile(first_log, scratch / "app-run-first.log")
    initial = read_snapshot(scratch / "data")

    second_gui = run_probe(app_run, scratch, seconds=seconds)
    if second_gui.get("status") != "WINDOW_OBSERVED":
        raise ColdStartError("Reopened installed run created no observed desktop window")
    if first_log.is_file() and not first_log.is_symlink():
        shutil.copyfile(first_log, scratch / "app-run-second.log")
    reopened = read_snapshot(scratch / "data")

    return {
        "schema": "motionwright.ci.installed-cold-relaunch/1",
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
    args = parser.parse_args()
    source = json.loads(args.source_lock.read_text(encoding="utf-8"))
    pin = source["dependencies"]["semwright"]["revision"]
    report = probe_cold_relaunch(
        app_run=args.app_run, scratch=args.scratch, source_sha=args.source_sha,
        semwright_sha=pin, seconds=args.seconds,
    )
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
