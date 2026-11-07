"""Source-bound inert Graph executable inputs, prepared before R74.

Reuses the physically exercised GN1 copier. Build/tool originals keep their
bytes, modes and identities; only four exclusive private copies become 0500.
The original producer allocation charges these live copies even though their
already retained executable bytes are not duplicated in the encrypted carrier.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from ci_graph_dev_native_startup import executable_metadata, stage_executable_input

SCHEMA = "motionwright.graph-native-executable-inputs/1"
ROLES = {"native_provider": "motionwright-native-provider", "manim_runner": "motionwright-manim-runner",
         "python3": "python3", "ffmpeg": "ffmpeg"}
# The provider's SDK ceiling is 64 MiB; the tools' SDK ceiling is 256 MiB.
# This closed Graph preparation retains GN1's stricter 64 MiB tool ceiling.
PER_FILE_BYTES = 64 * 1024 * 1024
TOTAL_BYTES = 104 * 1024 * 1024
LIMITS = {"individual_bytes": PER_FILE_BYTES, "total_bytes": TOTAL_BYTES, "files": 4,
          "charged_to_producer_allocation": True, "duplicate_executable_bytes_archived": False}
META = {"device", "inode", "bytes", "mode", "uid", "gid", "links", "mtime_ns", "ctime_ns"}


def record_metadata(value):
    # Nanoseconds exceed JSON's interoperable integer range in the JS guard.
    return {key: str(value[key]) if key in ("mtime_ns", "ctime_ns") else value[key] for key in META}


def need(ok, code):
    if not ok:
        raise ValueError(code)


def source_reference(value):
    need(type(value) is dict and set(value) == {"path", "sha256", "size_bytes"}
         and type(value["path"]) is str and Path(value["path"]).is_absolute()
         and type(value["size_bytes"]) is int and 0 < value["size_bytes"] <= PER_FILE_BYTES
         and type(value["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]),
         "Graph native input reference")


def directory_identity(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True) == path
         and not any(item.is_symlink() for item in (path, *path.parents)), "Graph native input directory alias")
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
         and info.st_uid == os.getuid(), "Graph native input directory ownership")
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino,
            "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}


def input_total(binaries):
    for role in ROLES:
        source_reference(binaries[role])
    total = sum(binaries[role]["size_bytes"] for role in ROLES)
    need(total <= TOTAL_BYTES, "Graph native input total")
    return total


def prepare(directory, binaries, source, retain_intent):
    directory = Path(directory)
    total = input_total(binaries)
    need(not directory.exists() and not directory.is_symlink(), "Graph native inputs already attempted")
    # This durable declaration precedes directory/file creation; an uncertain
    # or partial preparation never retries into an existing destination.
    retain_intent({"schema": SCHEMA, "source": source, "inputs": {role: binaries[role] for role in ROLES},
                   "directory": str(directory), "limits": LIMITS, "copy_bytes": total,
                   "source_files_modified": False, "automatic_retry": False})
    directory.mkdir(mode=0o700)
    original_directory = directory_identity(directory)
    inputs = {}
    for role, name in ROLES.items():
        reference = binaries[role]
        _, observation = stage_executable_input({"path": reference["path"], "sha256": reference["sha256"],
                                                "bytes": reference["size_bytes"]}, directory, name)
        copy = observation["private_copy"]
        inputs[role] = {"source": dict(reference),
            "prepared": {"path": copy["path"], "sha256": copy["sha256"], "size_bytes": copy["bytes"]},
            "source_metadata": record_metadata(observation["source_metadata"]),
            "prepared_metadata": record_metadata(copy),
            "source_modified": observation["source_modified"], "automatic_retry": False}
        need(directory_identity(directory) == original_directory, "Graph native input directory changed")
    record = {"schema": SCHEMA, "source": source, "directory": original_directory,
              "limits": LIMITS, "copy_bytes": total, "inputs": inputs,
              "source_files_modified": False, "automatic_retry": False}
    validate_live(record, binaries, source, directory)
    return record


def validate_contract(record, binaries, source, directory):
    need(type(record) is dict and set(record) == {"schema", "source", "directory", "limits", "copy_bytes", "inputs",
         "source_files_modified", "automatic_retry"} and record["schema"] == SCHEMA and record["source"] == source
         and record["limits"] == LIMITS and record["source_files_modified"] is False
         and record["automatic_retry"] is False and set(record["inputs"]) == set(ROLES), "Graph native input contract")
    expected_dir = record["directory"]
    need(set(expected_dir) == {"path", "device", "inode", "mode", "uid", "gid"}
         and expected_dir["path"] == str(directory) and expected_dir["mode"] == 0o700
         and all(type(expected_dir[key]) is int and expected_dir[key] >= 0 for key in ("device", "inode", "uid", "gid")),
         "Graph native input directory contract")
    need(record["copy_bytes"] == input_total(binaries), "Graph native input allocation")
    for role, name in ROLES.items():
        row = record["inputs"][role]
        need(set(row) == {"source", "prepared", "source_metadata", "prepared_metadata", "source_modified", "automatic_retry"}
             and row["source"] == binaries[role] and row["source_modified"] is False and row["automatic_retry"] is False,
             "Graph native input original binding")
        source_reference(row["prepared"])
        need(row["prepared"] == {**binaries[role], "path": str(Path(directory) / name)}
             and row["prepared"]["path"] != row["source"]["path"], "Graph native input copy binding")
        before, copied = row["source_metadata"], row["prepared_metadata"]
        need(set(before) == set(copied) == META
             and all(type(row[key]) is int and row[key] >= 0 for row in (before, copied)
                     for key in META - {"mtime_ns", "ctime_ns"})
             and all(type(row[key]) is str and re.fullmatch(r"[0-9]+", row[key]) for row in (before, copied)
                     for key in ("mtime_ns", "ctime_ns"))
             and before["bytes"] == copied["bytes"] == binaries[role]["size_bytes"]
             and before["mode"] & 0o111 and before["uid"] in (0, expected_dir["uid"])
             and copied["mode"] == 0o500 and copied["uid"] == expected_dir["uid"] and copied["links"] == 1
             and (before["device"], before["inode"]) != (copied["device"], copied["inode"]),
             "Graph native input physical identity")
    return {role: dict(row["prepared"]) for role, row in record["inputs"].items()}


def _unchanged(reference, expected):
    path = Path(reference["path"])
    need(path.resolve(strict=True) == path and not any(item.is_symlink() for item in (path, *path.parents)),
         "Graph native input path changed")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and record_metadata(executable_metadata(before)) == expected
             and record_metadata(executable_metadata(path.lstat())) == expected, "Graph native input identity changed")
        hashed, count, prefix = hashlib.sha256(), 0, b""
        while raw := os.read(fd, 1024 * 1024):
            if not prefix:
                prefix = raw[:4]
            count += len(raw)
            need(count <= reference["size_bytes"], "Graph native input grew")
            hashed.update(raw)
        need(count == reference["size_bytes"] and hashed.hexdigest() == reference["sha256"] and prefix == b"\x7fELF"
             and record_metadata(executable_metadata(os.fstat(fd))) == expected
             and record_metadata(executable_metadata(path.lstat())) == expected,
             "Graph native input bytes changed")
    finally:
        os.close(fd)


def validate_live(record, binaries, source, directory):
    prepared = validate_contract(record, binaries, source, directory)
    need(directory_identity(directory) == record["directory"], "Graph native input live directory")
    for row in record["inputs"].values():
        _unchanged(row["source"], row["source_metadata"])
        _unchanged(row["prepared"], row["prepared_metadata"])
    need(directory_identity(directory) == record["directory"], "Graph native input live directory changed")
    return prepared
