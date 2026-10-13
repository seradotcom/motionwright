#!/usr/bin/env python3
"""Allowlist public v0.1 cross-app CI evidence; never upload the raw owner folder.

Reads completed, already-verified disposable CI evidence, not native runtime
authority. No failed render, user acceptance or release is ever invented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

SCHEMA = "motionwright.v01-cross-app-public-artifact/1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
# Deliberately fail closed when review markup/manifest carries owner metadata.
PRIVATE_TEXT = re.compile(
    r"(?i)(?:/home/|/tmp/|/private/|[A-Z]:\\\\Users\\\\|"
    r"session[_-]?file|session[_-]?ticket|owner[_-]?connection|"
    r"native[_-]?job[_-]?ref|job_ref|opaque[_-]?ticket|"
    r"bearer[ ]|password|secret[_-]?key|api[_-]?key)"
)
COPY = {
    "recovery-demo.html": 2_000_000,
    "reused-blender.glb": 64_000_000,
    "artifact-manifest.json": 4_000_000,
    "bounded-native-failure-reasons.json": 250_000,
    "first-frame.png": 16_000_000,
    "middle-frame.png": 16_000_000,
    "last-frame.png": 16_000_000,
}
TEXT_FILES = {
    "recovery-demo.html", "artifact-manifest.json",
    "bounded-native-failure-reasons.json",
}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class PublicationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def require(condition: bool, code: str) -> None:
    if not condition:
        raise PublicationError(code)


def strict_object(blob: bytes) -> dict:
    def no_duplicate(pairs: list[tuple[str, object]]) -> dict:
        value = {}
        for k, v in pairs:
            require(k not in value, "duplicate_evidence_json_key")
            value[k] = v
        return value
    try:
        data = json.loads(blob.decode("utf-8"), object_pairs_hook=no_duplicate)
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, PublicationError):
            raise
        raise PublicationError("malformed_evidence_json") from exc
    require(isinstance(data, dict), "non_object_evidence_json")
    return data


def read_bounded(root: Path, name: str, limit: int) -> bytes:
    path = root / name
    require(path.is_file() and not path.is_symlink(), "missing_or_linked_public_source")
    try:
        size = path.stat().st_size
        require(0 < size <= limit, "oversized_public_source")
        data = path.read_bytes()
    except OSError as exc:
        raise PublicationError("unreadable_public_source") from exc
    require(len(data) == size, "source_changed_during_publication")
    if name.endswith(".png"):
        require(data.startswith(PNG_MAGIC), "invalid_public_png")
    if name.endswith(".glb"):
        require(data.startswith(b"glTF"), "invalid_public_glb")
    if name in TEXT_FILES:
        try:
            content = data.decode("utf-8")
        except UnicodeError as exc:
            raise PublicationError("invalid_public_utf8") from exc
        require(PRIVATE_TEXT.search(content) is None, "private_metadata_in_public_source")
        if name.endswith(".json"):
            strict_object(data)
    return data


def publish(root: Path, output: Path, source_sha: str, semwright_sha: str,
            ci_status: str) -> dict:
    require(bool(SHA.fullmatch(source_sha))
            and bool(SHA.fullmatch(semwright_sha)), "source_identity_invalid")
    require(ci_status in {"success", "failure", "cancelled", "skipped"},
            "ci_result_invalid")
    require(not output.exists(), "public_artifact_already_exists")
    source_real = root.resolve()
    output_real = output.resolve()
    require(output_real != source_real
            and output_real not in source_real.parents
            and source_real not in output_real.parents,
            "public_root_overlaps_private_source")

    copies: dict[str, bytes] = {}
    if ci_status == "success":
        require(root.is_dir() and not root.is_symlink(),
                "missing_or_linked_private_evidence_root")
        result = strict_object(read_bounded(root, "result.json", 128_000))
        require(result.get("native_cross_app_recovery_e2e") == "PASS"
                and result.get("source_motionwright") == source_sha
                and result.get("source_semwright") == semwright_sha
                and type(result.get("motion_canvas_frame_count")) is int
                and result["motion_canvas_frame_count"] == 60
                and type(result.get("blender_native_driver_reexecutions")) is int
                and result["blender_native_driver_reexecutions"] == 0
                and type(result.get("native_motion_canvas_successful_attempts")) is int
                and result["native_motion_canvas_successful_attempts"] == 1,
                "unverified_public_success")
        for name, limit in COPY.items():
            copies[name] = read_bounded(root, name, limit)
        require(
            result.get("blender_glb_sha256")
            == hashlib.sha256(copies["reused-blender.glb"]).hexdigest()
            and result.get("motion_canvas_manifest_sha256")
            == hashlib.sha256(copies["artifact-manifest.json"]).hexdigest(),
            "native_source_hash_mismatch",
        )

    file_receipts = [
        {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(copies.items())
    ]
    receipt = {
        "schema": SCHEMA,
        "motionwright_source_sha": source_sha,
        "semwright_source_pin": semwright_sha,
        "ci_evidence": "PASS" if ci_status == "success" else "NOT_VERIFIED",
        "included_files": file_receipts,
        "raw_daemon_log_uploaded": False,
        "original_native_receipts_uploaded": False,
        "native_mutation_replayed_by_publisher": False,
        "installed_owner_acceptance": "NOT_RUN",
        "v01_release_authorized": False,
    }
    # The root must be a new publication namespace; no source path is copied.
    output.mkdir(parents=True, exist_ok=False)
    for name, data in copies.items():
        with (output / name).open("xb") as stream:
            stream.write(data)
    with (output / "public-evidence.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args(argv)
    try:
        lock = strict_object((Path(__file__).resolve().parents[1] / "SOURCE_LOCK.json").read_bytes())
        pin = lock["dependencies"]["semwright"]["revision"]
        result = publish(args.evidence_root, args.output_root,
                         args.source_sha, pin, args.job_status)
    except (PublicationError, OSError, KeyError, TypeError) as exc:
        code = exc.code if isinstance(exc, PublicationError) else "public_artifact_refused"
        print("cross-app-public-artifact=refused reason=" + code, file=sys.stderr)
        return 2
    print("cross-app-public-artifact=" + result["ci_evidence"]
          + " files=" + str(len(result["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
