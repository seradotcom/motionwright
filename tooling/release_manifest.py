#!/usr/bin/env python3
"""Create bounded v0.1 delivery metadata for the *actual* clean Git checkout.

This is a local source-integrity receipt, NOT an installed binary attestation,
signing certificate, independent acceptance verdict or publication authority.
Never execute native effects and never read owner session material.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib
import uuid

from v01_release_readiness import (
    ReadinessError, _git_read, verify_local_checkout,
)

ROOT = Path(__file__).resolve().parents[1]
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
MAX_SOURCE_BYTES = 16 * 1024 * 1024
INPUTS = (
    "Cargo.toml",
    "Cargo.lock",
    "SOURCE_LOCK.json",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "apps/studio/package-lock.json",
)
LOCKED_INPUTS = tuple(name for name in INPUTS if name != "Cargo.toml")


class ManifestError(ValueError):
    """Constant failure codes: no Git stderr, machine path or secret content."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _require(valid: bool, code: str) -> None:
    if not valid:
        raise ManifestError(code)


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        _require(key not in result, "manifest_input_duplicate_json_key")
        result[key] = value
    return result


def _path_has_reparse(path: Path, info: os.stat_result) -> bool:
    attrs = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    check_junction = getattr(path, "is_junction", None)
    return bool(attrs & flag) or (
        bool(check_junction()) if callable(check_junction) else False
    )


def _regular_tracked_input(root: Path, relative: str) -> Path:
    _require(relative in INPUTS and ".." not in Path(relative).parts, "manifest_input_unexpected")
    current = root
    for component in Path(relative).parts:
        current = current / component
        try:
            info = current.lstat()
            _require(
                not stat.S_ISLNK(info.st_mode)
                and not _path_has_reparse(current, info)
                and not current.is_symlink(),
                "manifest_input_link_refused",
            )
        except OSError as exc:
            raise ManifestError("manifest_input_unavailable") from exc
    _require(stat.S_ISREG(info.st_mode), "manifest_input_not_regular")
    return current


def _stable_read(root: Path, relative: str) -> bytes:
    path = _regular_tracked_input(root, relative)
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        with os.fdopen(os.open(path, flags), "rb") as reader:
            first = os.fstat(reader.fileno())
            _require(stat.S_ISREG(first.st_mode) and
                     0 < first.st_size <= MAX_SOURCE_BYTES,
                     "manifest_input_bounds")
            contents = bytearray()
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                contents.extend(chunk)
                _require(len(contents) <= MAX_SOURCE_BYTES, "manifest_input_bounds")
            last = os.fstat(reader.fileno())
        final = path.stat()
    except OSError as exc:
        raise ManifestError("manifest_input_unavailable") from exc
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink)
    _require(identity(first) == identity(last) == identity(final)
             and len(contents) == first.st_size, "manifest_input_changed")
    return bytes(contents)


def _checkout(root: Path, expected_sha: str) -> None:
    _require(isinstance(expected_sha, str) and
             SHA40.fullmatch(expected_sha) is not None, "manifest_sha_invalid")
    try:
        verify_local_checkout(root, expected_sha)
    except ReadinessError as exc:
        raise ManifestError(exc.code) from exc


def _inputs_are_tracked(root: Path) -> None:
    try:
        _git_read(root, "manifest_input_not_tracked", "ls-files",
                  "--error-unmatch", "--", *INPUTS)
    except ReadinessError as exc:
        raise ManifestError(exc.code) from exc


def build_manifest(root: Path, expected_sha: str) -> dict:
    """Bind every digest to unchanged, tracked bytes at the exact HEAD."""
    _checkout(root, expected_sha)
    _inputs_are_tracked(root)
    captured = {relative: _stable_read(root, relative) for relative in INPUTS}
    try:
        workspace = tomllib.loads(captured["Cargo.toml"].decode("utf-8"))["workspace"]["package"]
        lock = json.loads(captured["SOURCE_LOCK.json"].decode("utf-8"),
                          object_pairs_hook=_strict_object)
        semwright = lock["dependencies"]["semwright"]
        _require(isinstance(semwright, dict) and
                 isinstance(semwright.get("revision"), str) and
                 SHA40.fullmatch(semwright["revision"]) is not None,
                 "manifest_semwright_pin_invalid")
        for field in ("repository", "native_sdk_package", "license_observed"):
            _require(isinstance(semwright.get(field), str) and bool(semwright[field]),
                     "manifest_semwright_lock_invalid")
        _require(isinstance(semwright.get("features_planned"), list),
                 "manifest_semwright_lock_invalid")
        for field in ("repository", "version", "license"):
            _require(isinstance(workspace.get(field), str) and bool(workspace[field]),
                     "manifest_workspace_invalid")
    except (ValueError, UnicodeError, TypeError, KeyError, tomllib.TOMLDecodeError) as exc:
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError("manifest_source_parse_invalid") from exc
    _checkout(root, expected_sha)
    return {
        "schema_version": 1,
        "motionwright": {
            "name": "Motionwright",
            "repository": workspace["repository"],
            "version": workspace["version"],
            "license": workspace["license"],
            "source_revision": expected_sha,
        },
        "semwright": {
            "repository": semwright["repository"],
            "revision": semwright["revision"],
            "native_sdk_package": semwright["native_sdk_package"],
            "license_observed": semwright["license_observed"],
            "features_planned": semwright["features_planned"],
        },
        "locked_inputs": {
            relative: {"sha256": sha256(captured[relative]).hexdigest()}
            for relative in LOCKED_INPUTS
        },
        "delivery_boundary": {
            "automatic_publish": False,
            "hidden_runtime_downloads": False,
            "project_receipts_exported": False,
            "secrets_expected_in_project_bundle": False,
            "portable_project_format": "motionwright-directory-bundle",
        },
    }


def write_new_manifest(root: Path, output: Path, manifest: dict) -> None:
    """Publish synced, closed manifest bytes with an exclusive same-directory link.

    Known failures before publication leave no new final path. Other failures
    during or after a link attempt report manifest_output_commit_uncertain;
    callers must preserve the final path and must not retry automatically.
    A preexisting target is a known collision and is never replaced.
    """
    target = output if output.is_absolute() else root / output
    _require(".." not in target.parts, "manifest_output_invalid")
    parent = target.parent
    try:
        _require(parent.is_dir(), "manifest_output_parent_unavailable")
        for part in (parent, *parent.parents):
            info = part.lstat()
            _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                     and not _path_has_reparse(part, info)
                     and not part.is_symlink(), "manifest_output_link_refused")
        _require(not target.is_symlink(), "manifest_output_exists")
        content = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        stage_name = f".motionwright-manifest.{uuid.uuid4().hex}.stage"
        stage_path = parent / stage_name
    except OSError as exc:
        raise ManifestError("manifest_output_refused") from exc

    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    directory_fd: int | None = None
    raw_fd: int | None = None
    staged_identity: os.stat_result | None = None
    publication_attempted = False
    failure: Exception | None = None

    def same_stage(info: os.stat_result) -> bool:
        return (staged_identity is not None and staged_identity.st_ino != 0
                and info.st_ino != 0 and stat.S_ISREG(info.st_mode)
                and info.st_dev == staged_identity.st_dev
                and info.st_ino == staged_identity.st_ino)

    try:
        inspected_parent = parent.lstat()
        _require(stat.S_ISDIR(inspected_parent.st_mode)
                 and not _path_has_reparse(parent, inspected_parent)
                 and not parent.is_symlink(), "manifest_output_refused")
        if os.name == "posix":
            directory_fd = os.open(
                parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
            opened_parent = os.fstat(directory_fd)
            _require(stat.S_ISDIR(opened_parent.st_mode)
                     and opened_parent.st_dev == inspected_parent.st_dev
                     and opened_parent.st_ino == inspected_parent.st_ino,
                     "manifest_output_refused")
            raw_fd = os.open(stage_name, flags, 0o600, dir_fd=directory_fd)
        else:
            raw_fd = os.open(stage_path, flags, 0o600)
        staged_identity = os.fstat(raw_fd)
        _require(stat.S_ISREG(staged_identity.st_mode) and staged_identity.st_ino != 0,
                 "manifest_output_refused")
        # Retain raw_fd until fdopen succeeds; close either owner once.
        stream = os.fdopen(raw_fd, "wb")
        raw_fd = None
        try:
            _require(stream.write(content) == len(content), "manifest_output_refused")
            stream.flush()
            os.fsync(stream.fileno())
            complete = os.fstat(stream.fileno())
            _require(same_stage(complete) and complete.st_size == len(content),
                     "manifest_output_refused")
        finally:
            # A close failure is still before publication. Never link an open
            # stream, nor retry a close that may already have closed its fd.
            stream.close()
        ready = (os.stat(stage_name, dir_fd=directory_fd, follow_symlinks=False)
                 if directory_fd is not None else stage_path.lstat())
        _require(same_stage(ready) and ready.st_size == len(content)
                 and not _path_has_reparse(stage_path, ready),
                 "manifest_output_refused")

        # Any error from an attempted link can follow a committed publication.
        # EEXIST is the sole known collision; never retry via another primitive.
        publication_attempted = True
        try:
            if directory_fd is not None:
                os.link(stage_name, target.name, src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd, follow_symlinks=False)
            else:
                os.link(stage_path, target)
        except FileExistsError:
            publication_attempted = False
            raise
        committed = (os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
                     if directory_fd is not None else target.lstat())
        _require(same_stage(committed) and committed.st_size == len(content)
                 and not _path_has_reparse(target, committed),
                 "manifest_output_commit_uncertain")
        for part in (parent, *parent.parents):
            info = part.lstat()
            _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                     and not _path_has_reparse(part, info)
                     and not part.is_symlink(), "manifest_output_commit_uncertain")
        observed_parent = parent.lstat()
        _require(observed_parent.st_dev == inspected_parent.st_dev
                 and observed_parent.st_ino == inspected_parent.st_ino,
                 "manifest_output_commit_uncertain")
    except Exception as exc:
        failure = exc
    finally:
        if raw_fd is not None:
            try:
                os.close(raw_fd)
            except Exception as exc:
                if failure is None:
                    failure = exc
        if staged_identity is not None:
            try:
                current = (os.stat(stage_name, dir_fd=directory_fd, follow_symlinks=False)
                           if directory_fd is not None else stage_path.lstat())
                _require(same_stage(current), "manifest_output_refused")
                # Only this invocation's inode, one unlink attempt, never target.
                if directory_fd is not None:
                    os.unlink(stage_name, dir_fd=directory_fd)
                else:
                    os.unlink(stage_path)
            except Exception as exc:
                if failure is None:
                    failure = exc
        if directory_fd is not None:
            try:
                if publication_attempted and failure is None:
                    os.fsync(directory_fd)
            except Exception as exc:
                if failure is None:
                    failure = exc
            finally:
                try:
                    os.close(directory_fd)
                except Exception as exc:
                    if failure is None:
                        failure = exc
    if failure is not None:
        raise ManifestError(
            "manifest_output_commit_uncertain" if publication_attempted
            else "manifest_output_refused"
        ) from failure


def _discard_new_output_on_source_change(target: Path, manifest: dict) -> None:
    """Keep the committed output when a later source check fails.

    A path may already refer to a different file after publication. Checking
    its metadata or contents cannot make deletion safe, so do not inspect or
    remove it. Receipt consumers keep this call signature and must propagate
    the uncertain outcome without retrying publication.
    """
    raise ManifestError("manifest_output_commit_uncertain")

def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(root, args.git_sha)
        write_new_manifest(root, args.output, manifest)
        # Post-write check also prevents a success claim if tracked inputs
        # changed while the output bytes were committed to disk.
        try:
            _checkout(root, args.git_sha)
        except ManifestError:
            target = args.output if args.output.is_absolute() else root / args.output
            _discard_new_output_on_source_change(target, manifest)
            raise
    except ManifestError as exc:
        print("delivery-manifest=REFUSED code=" + exc.code, file=sys.stderr)
        return 2
    print("delivery-manifest=CREATED source=" + args.git_sha
          + " semwright=" + manifest["semwright"]["revision"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
