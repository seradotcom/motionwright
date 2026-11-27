"""Read the one durable hybrid MP4 receipt from an installed AAC31 scenario."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import uuid

from source_checkout_observation import regular_file

COMMAND = "driver.mlt-video.av.mux"
CONTEXT = "motionwright.original-hybrid-media-reopen/1"
MAX_MASTER_BYTES = 32 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Source:
    project: str
    generation: str
    revision: int
    deliverable: str


@dataclass(frozen=True)
class Master:
    id: str
    request_id: str
    sha256: str
    size: int
    path: Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical_uuid(text: str) -> str:
    parsed = uuid.UUID(text)
    require(parsed.int != 0 and str(parsed) == text, "invalid installed AAC31 identity")
    return text


def rows(database: Path, source: Source) -> list[sqlite3.Row]:
    canonical_uuid(source.project)
    canonical_uuid(source.generation)
    canonical_uuid(source.deliverable)
    require(type(source.revision) is int and source.revision >= 0, "invalid saved revision")
    require(database.is_absolute() and database == database.resolve(strict=True)
            and database.is_file(), "expected the installed profile database")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db.execute(
            "SELECT receipt_id, request_id, request_sha256, stage, payload_json "
            "FROM production_receipts WHERE project_id = ? AND generation = ? "
            "AND revision = ? AND command = ? LIMIT 3",
            (source.project, source.generation, source.revision, COMMAND)
        ).fetchall()


def original_master(database: Path, source: Source, output: Path) -> Master:
    """Bind one dispatch and its completed original to the file on disk."""
    records = rows(database, source)
    dispatch = [row for row in records if row["stage"] == "dispatching"]
    completed = [row for row in records if row["stage"] == "completed"]
    require(len(records) == 2 and len(dispatch) == len(completed) == 1,
            "expected one original completed AAC31 mux")
    begin, end = dispatch[0], completed[0]
    require(begin["request_id"] == end["request_id"]
            and begin["request_sha256"] == end["request_sha256"]
            and SHA256.fullmatch(end["request_sha256"]) is not None,
            "AAC31 dispatch and terminal identity differ")
    provenance = json.loads(end["payload_json"])
    original = provenance["original_media_reopen"]
    require(original == json.loads(begin["payload_json"])["original_media_reopen"]
            and original["schema"] == CONTEXT
            and original["deliverable_id"] == source.deliverable,
            "AAC31 receipt refers to another saved delivery")
    args = original["args"]
    require((args["frame_count"], args["fps_num"], args["fps_den"],
             args["sample_rate"], args["channels"]) == (31, 30, 1, 48_000, 2),
            "AAC31 mux receipt has the wrong clock")
    result = provenance["result"]
    execution = result["execution"]
    require(result["provenance_checked"] is True and execution["dry_run"] is False
            and execution["provenance"]["provider"] == "driver:mlt-video",
            "AAC31 completion does not prove real MLT execution")
    artifact = result["artifact"]
    rel = PurePosixPath(artifact["path"])
    require(str(rel) == artifact["path"] == args["output_path"]
            and not rel.is_absolute() and ".." not in rel.parts
            and not any(character in str(rel) for character in (":", "\\"))
            and rel.suffix == ".mp4" and artifact["root"] == "output",
            "AAC31 receipt points outside its scoped output")
    parent = output.resolve(strict=True)
    path = parent.joinpath(*rel.parts)
    require(output == parent and path == path.resolve(strict=True)
            and path.is_relative_to(parent), "AAC31 original path changed")
    observed = regular_file(path, MAX_MASTER_BYTES)
    digest = observed["sha256"]
    require(digest == artifact["sha256"] and observed["bytes"] == artifact["bytes"],
            "AAC31 MP4 differs from the retained owner original")
    return Master(canonical_uuid(end["receipt_id"]), end["request_id"],
                  digest, observed["bytes"], path)
