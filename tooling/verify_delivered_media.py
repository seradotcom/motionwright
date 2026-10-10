#!/usr/bin/env python3
"""Verify a Motionwright MP4 + unsigned local SHA-256 integrity descriptor.

This checks content consistency and the reported source metadata format.
It does NOT authenticate the creator, prove any Semwright invocation happened,
or certify H.264/AAC decoding, audio/video quality, or human acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import uuid

SCHEMA = "motionwright-media-integrity-v1"
SCOPE = "sha256-content-consistency-not-signed-attestation"
MAX_DESCRIPTOR_BYTES = 12 * 1024
MAX_MP4_BYTES = 1024 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")


class IntegrityError(ValueError):
    """The claimed media content or origin descriptor cannot be verified."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


def uuid_value(value: object, name: str) -> None:
    require(isinstance(value, str), f"{name} must be a canonical UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise IntegrityError(f"{name} must be a canonical UUID string") from exc
    require(str(parsed) == value, f"{name} must be canonical lowercase UUID")


def plain_local_file(path: Path) -> Path:
    absolute = path.absolute()
    require(".." not in absolute.parts, "Local integrity path must not contain parent traversal")
    parents = list(reversed(absolute.parents))
    for item in parents:
        require(not item.is_symlink(), "Local integrity path must not cross a symlink")
        require(item.is_dir(), "Local integrity parent must be a directory")
    require(not absolute.is_symlink(), "Local integrity file cannot be a symlink")
    try:
        metadata = absolute.stat()
    except OSError as exc:
        raise IntegrityError("Local integrity file does not exist") from exc
    require(stat.S_ISREG(metadata.st_mode), "Integrity input must be a regular local file")
    return absolute


def validated_descriptor(path: Path) -> tuple[dict, Path]:
    file = plain_local_file(path)
    require(1 <= file.stat().st_size <= MAX_DESCRIPTOR_BYTES,
            "Integrity descriptor is empty or larger than 12 KiB")
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("Integrity descriptor is not valid UTF-8 JSON") from exc
    require(isinstance(document, dict) and set(document) == {
        "schema", "scope", "media", "origin",
    }, "Unknown or incomplete Motionwright proof schema")
    require(document["schema"] == SCHEMA and document["scope"] == SCOPE,
            "Manifest does not use the unsigned Motionwright integrity v1 contract")

    media = document["media"]
    origin = document["origin"]
    require(isinstance(media, dict) and set(media) == {
        "filename", "size_bytes", "sha256",
    }, "Media content descriptor is incomplete or has unexpected fields")
    require(isinstance(origin, dict) and set(origin) == {
        "project_id", "generation", "revision", "deliverable_id", "native_profile",
        "frame_count", "frame_rate", "semwright_native_sdk_revision",
    }, "Source provenance descriptor is incomplete or has unexpected fields")
    filename = media["filename"]
    require(isinstance(filename, str) and 1 <= len(filename.encode("utf-8")) <= 255
            and filename not in {".", ".."}
            and "/" not in filename and "\\" not in filename
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in filename)
            and filename.lower().endswith(".mp4"),
            "Media filename must be one plain relative .mp4 basename")
    digest = media["sha256"]
    require(isinstance(digest, str) and HEX64.fullmatch(digest) is not None,
            "Media SHA-256 is not lowercase 64-character hex")
    size = media["size_bytes"]
    require(isinstance(size, int) and not isinstance(size, bool)
            and 12 <= size <= MAX_MP4_BYTES,
            "Media byte count is outside its bounded range")
    for field in ("project_id", "generation", "deliverable_id"):
        uuid_value(origin[field], field)
    rev = origin["revision"]
    require(isinstance(rev, int) and not isinstance(rev, bool) and rev >= 0,
            "Source revision must be a nonnegative integer")
    frames = origin["frame_count"]
    require(isinstance(frames, int) and not isinstance(frames, bool)
            and 1 <= frames <= 36_000, "Native frame count is outside its bounded budget")
    fps = origin["frame_rate"]
    require(isinstance(fps, dict) and set(fps) == {"num", "den"}
            and all(isinstance(fps[k], int) and not isinstance(fps[k], bool)
                    and 1 <= fps[k] <= 120_000 for k in ("num", "den")),
            "Native frame-rate rational must contain positive bounded integers")
    require(origin["native_profile"] == "h264-aac-mp4",
            "Native media profile is not supported by this proof contract")
    sdk = origin["semwright_native_sdk_revision"]
    require(isinstance(sdk, str) and HEX40.fullmatch(sdk) is not None,
            "Semwright Native SDK source must be an exact SHA")
    media_file = plain_local_file(file.parent / filename)
    return document, media_file


def verify_delivery(manifest_path: Path, trusted_sha256: str | None = None) -> dict:
    document, media_file = validated_descriptor(manifest_path)
    media = document["media"]
    if trusted_sha256 is not None:
        require(HEX64.fullmatch(trusted_sha256) is not None,
                "Trusted SHA-256 anchor must be 64 lowercase hex characters")
        require(trusted_sha256 == media["sha256"],
                "Trusted SHA-256 anchor does not match the supplied manifest")
    file_size = media_file.stat().st_size
    require(file_size == media["size_bytes"], "MP4 byte count differs from delivery manifest")
    require(file_size <= MAX_MP4_BYTES, "MP4 exceeds the 1 GiB verification budget")
    digest = hashlib.sha256()
    count = 0
    with media_file.open("rb") as source:
        first = source.read(12)
        require(len(first) == 12 and first[4:8] == b"ftyp",
                "Exported video has no MP4 ftyp signature")
        digest.update(first)
        count += len(first)
        while payload := source.read(1024 * 1024):
            count += len(payload)
            require(count <= MAX_MP4_BYTES, "MP4 grew past the verification budget")
            digest.update(payload)
    require(count == file_size, "MP4 length changed during verification")
    actual_sha = digest.hexdigest()
    require(actual_sha == media["sha256"], "MP4 SHA-256 differs from delivery manifest")
    return {
        "status": "sha256-content-verified",
        "filename": media["filename"],
        "size_bytes": count,
        "sha256": actual_sha,
        "source_revision": document["origin"]["revision"],
        "signed_authenticity": False,
        "human_acceptance": False,
        "trusted_anchor_matched": trusted_sha256 is not None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="Path to the .mp4.motionwright-integrity.json receipt")
    parser.add_argument("--trusted-sha256", default=None,
                        help="Optional externally trusted SHA-256 of the MP4, not from this manifest")
    args = parser.parse_args(argv)
    try:
        result = verify_delivery(args.manifest, args.trusted_sha256)
    except (IntegrityError, OSError) as exc:
        print(f"Motionwright integrity check FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
