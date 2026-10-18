#!/usr/bin/env python3
"""Offline, owner-invoked Semwright connection preflight for Motionwright v0.1.

This standalone inspector NEVER connects to a Broker, reads ticket contents,
requests a capability, starts a render, or changes the saved runtime selection.
--probe-version opts into executing the digest-verified CLI with --version only.
The JSON report deliberately excludes paths, session data, and raw diagnostics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONNECTION_SCHEMA = "motionwright-semwright-connection/1"
REPORT_SCHEMA = "motionwright.runtime-first-use-audit/1"
MAX_JSON = 8 * 1024
MAX_EXECUTABLE = 2 * 1024 * 1024 * 1024
MAX_VERSION = 512
VERSION_DEADLINE = 5.0
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_FIELDS = frozenset({
    "schema", "executable", "executable_sha256", "socket",
    "session_file", "output_root", "resource",
})
REGISTRY_FIELDS = frozenset({
    "schema_version", "project_id", "generation", "connection_file",
})


class AuditError(ValueError):
    """Only stable public reason codes are returned, never OS/path details."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AuditError(code)


def canonical_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise AuditError("scope_invalid") from exc
    require(str(parsed) == value, "scope_invalid")
    return value


def strict_object(pairs: list[tuple[str, object]]) -> dict:
    output: dict = {}
    for name, value in pairs:
        if name in output:
            raise AuditError("duplicate_json_field")
        output[name] = value
    return output


def safe_path(value: str, *, kind: str, code: str) -> Path:
    require(isinstance(value, str) and value and "\x00" not in value, code)
    path = Path(value)
    require(path.is_absolute(), code)
    for part in (path, *path.parents):
        try:
            metadata = part.lstat()
            # Windows junctions and many NTFS reparse points are NOT ordinary
            # symbolic links, including under Python 3.11. A saved owner
            # reference through one could escape the inspected file tree.
            # The attribute is returned by os.stat_result on Windows; the
            # fallback bit is the Win32 FILE_ATTRIBUTE_REPARSE_POINT (0x400).
            reparse = bool(
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            # Python 3.12+ provides an explicit junction probe. Keep the
            # attribute guard for earlier installed Windows Python versions.
            is_junction = getattr(part, "is_junction", None)
            junction = bool(is_junction()) if callable(is_junction) else False
        except OSError as exc:
            raise AuditError(code) from exc
        require(
            not stat.S_ISLNK(metadata.st_mode)
            and not part.is_symlink()
            and not reparse
            and not junction,
            code,
        )
    try:
        info = path.stat()
    except OSError as exc:
        raise AuditError(code) from exc
    if kind == "regular":
        require(stat.S_ISREG(info.st_mode), code)
    elif kind == "directory":
        require(stat.S_ISDIR(info.st_mode), code)
    else:
        require(info.st_mode != 0, code)
    if os.name == "posix":
        require(info.st_mode & 0o022 == 0, "unsafe_file_permissions")
    return path


def _same_opened_file(left: os.stat_result, right: os.stat_result) -> bool:
    """Compare actual file identities, never content-equivalent pathnames."""
    return (
        left.st_ino != 0
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and stat.S_ISREG(left.st_mode)
        and stat.S_ISREG(right.st_mode)
    )


def _check_opened_path(path: Path, opened: os.stat_result, code: str) -> None:
    """Recheck all path components and the inode while its fd remains open.

    This is a bounded single-inspection guarantee, NOT a durable OS lease on
    an external caller that opens the path later. Never infer Broker authority.
    """
    safe_path(str(path), kind="regular", code=code)
    try:
        observed = path.lstat()
    except OSError as exc:
        raise AuditError(code) from exc
    require(_same_opened_file(opened, observed), code)


def open_checked_regular(path: Path, *, code: str,
                         changed_code: str | None = None):
    """Open the same ordinary file that the preflight actually validated.

    A hardlink/rename substitution after safe_path but before os.open could
    otherwise supply identical-looking JSON or executable bytes from another
    inode. The fd and pathname identities must agree before any use.
    """
    path = safe_path(str(path), kind="regular", code=code)
    identity_code = changed_code or code
    try:
        preopen = path.lstat()
        require(stat.S_ISREG(preopen.st_mode), code)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        handle = os.open(path, flags)
    except OSError as exc:
        raise AuditError(code) from exc
    try:
        source = os.fdopen(handle, "rb")
    except (OSError, ValueError) as exc:
        os.close(handle)
        raise AuditError(code) from exc
    try:
        opened = os.fstat(source.fileno())
        require(_same_opened_file(preopen, opened), identity_code)
        _check_opened_path(path, opened, identity_code)
        if os.name == "posix":
            require(opened.st_mode & 0o022 == 0, "unsafe_file_permissions")
        return source
    except BaseException:
        source.close()
        raise


def read_json(path: Path, *, code: str, limit: int = MAX_JSON) -> dict:
    with open_checked_regular(path, code=code) as source:
        before = os.fstat(source.fileno())
        require(0 < before.st_size <= limit, code)
        contents = source.read(limit + 1)
        after = os.fstat(source.fileno())
        require(
            _same_opened_file(before, after)
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns,
            code,
        )
        _check_opened_path(path, after, code)
    require(0 < len(contents) <= limit, code)
    try:
        result = json.loads(contents, object_pairs_hook=strict_object)
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, AuditError):
            raise
        raise AuditError(code) from exc
    require(isinstance(result, dict), code)
    return result


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open_checked_regular(path, code="executable_invalid",
                              changed_code="executable_changed") as source:
        before = os.fstat(source.fileno())
        require(stat.S_ISREG(before.st_mode)
                and 0 < before.st_size <= MAX_EXECUTABLE, "executable_invalid")
        consumed = 0
        for block in iter(lambda: source.read(128 * 1024), b""):
            consumed += len(block)
            require(consumed <= MAX_EXECUTABLE, "executable_invalid")
            digest.update(block)
        after = os.fstat(source.fileno())
        require(
            _same_opened_file(before, after)
            and before.st_size == consumed == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns,
            "executable_changed",
        )
        _check_opened_path(path, after, "executable_changed")
    return digest.hexdigest()


def probe_cli_version(executable: Path) -> str:
    """Opt-in digest-checked process: --version only, 5s, 513-byte max read."""
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
                              "LANG", "LC_ALL", "LC_CTYPE"}}
    try:
        child = subprocess.Popen(
            [str(executable), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except OSError as exc:
        raise AuditError("cli_unavailable") from exc
    answer: list[bytes] = []
    failure: list[BaseException] = []

    def bounded_read() -> None:
        try:
            assert child.stdout is not None
            answer.append(child.stdout.read(MAX_VERSION + 1))
        except OSError as exc:
            failure.append(exc)

    reader = threading.Thread(target=bounded_read, daemon=True)
    reader.start()
    reader.join(VERSION_DEADLINE)
    if reader.is_alive() or failure or not answer or len(answer[0]) > MAX_VERSION:
        child.kill()
        child.wait()
        raise AuditError("cli_version_unavailable")
    try:
        status = child.wait(timeout=1.0)
    except subprocess.TimeoutExpired as exc:
        child.kill()
        child.wait()
        raise AuditError("cli_version_timeout") from exc
    require(status == 0, "cli_version_unavailable")
    try:
        value = answer[0].decode("utf-8").strip()
    except UnicodeError as exc:
        raise AuditError("cli_version_invalid") from exc
    match = re.fullmatch(r"semwright ([0-9A-Za-z.+-]{1,64})", value)
    require(match is not None, "cli_version_invalid")
    return match.group(1)


def inspect_connection(
    *,
    project_id: str,
    generation: str,
    connection_file: Path | None = None,
    registry_root: Path | None = None,
    probe_version: bool = False,
    version_probe=probe_cli_version,
) -> dict:
    """Pure local evidence collection except optional read-only CLI --version."""
    project_id = canonical_uuid(project_id)
    generation = canonical_uuid(generation)
    require((connection_file is None) != (registry_root is None),
            "selection_ambiguous")
    lock = read_json(ROOT / "SOURCE_LOCK.json", code="source_lock_invalid")
    semwright = lock.get("dependencies", {}).get("semwright")
    require(isinstance(semwright, dict), "source_lock_invalid")
    source_sha = semwright.get("revision")
    expected_version = semwright.get("version")
    require(isinstance(source_sha, str)
            and re.fullmatch(r"[0-9a-f]{40}", source_sha) is not None,
            "source_lock_invalid")
    require(isinstance(expected_version, str) and expected_version == "1.0.0",
            "source_lock_invalid")

    origin = "explicit_file"
    if registry_root is not None:
        root = safe_path(str(registry_root), kind="directory", code="registry_invalid")
        reference_file = root / (uuid.UUID(project_id).hex + ".json")
        selected = read_json(reference_file, code="registry_invalid")
        require(set(selected) == REGISTRY_FIELDS
                and type(selected.get("schema_version")) is int
                and selected["schema_version"] == 1,
                "registry_invalid")
        require(selected.get("project_id") == project_id
                and selected.get("generation") == generation,
                "registry_scope_mismatch")
        require(isinstance(selected.get("connection_file"), str),
                "registry_invalid")
        connection_file = Path(selected["connection_file"])
        origin = "saved_reference"
    assert connection_file is not None
    connection = read_json(connection_file, code="connection_invalid")
    require(set(connection) == EXPECTED_FIELDS
            and connection.get("schema") == CONNECTION_SCHEMA,
            "connection_invalid")
    require(all(isinstance(connection[name], str) for name in EXPECTED_FIELDS),
            "connection_invalid")
    expected_resource = "project:" + project_id
    require(connection["resource"] == expected_resource, "resource_mismatch")
    claimed_sha = connection["executable_sha256"]
    require(SHA256.fullmatch(claimed_sha) is not None, "digest_invalid")

    cli = safe_path(connection["executable"], kind="regular",
                    code="executable_invalid")
    session = Path(connection["session_file"])
    safe_path(connection["socket"], kind="any", code="socket_unavailable")
    safe_path(connection["output_root"], kind="directory",
              code="output_root_invalid")
    # A session token is never read or exported. Its size, owner-only mode,
    # pathname and inode must all agree on the SAME held file descriptor.
    with open_checked_regular(session, code="session_invalid") as session_file:
        session_info = os.fstat(session_file.fileno())
        require(0 < session_info.st_size <= MAX_JSON, "session_invalid")
        if os.name == "posix":
            require(session_info.st_mode & 0o077 == 0, "session_not_private")
        _check_opened_path(session, session_info, "session_invalid")
    actual_sha = hash_file(cli)
    require(actual_sha == claimed_sha, "executable_digest_mismatch")

    observed_version = None
    if probe_version:
        observed_version = version_probe(cli)
        require(observed_version == expected_version, "cli_version_incompatible")
    # No process-independent Broker health or provider success is inferred.
    return {
        "schema": REPORT_SCHEMA,
        "application": "Motionwright",
        "status": "CLI_VERSION_COMPATIBLE" if probe_version else "FILES_VERIFIED_ONLY",
        "connection_origin": origin,
        "resource_sha256": hashlib.sha256(expected_resource.encode()).hexdigest(),
        "executable_sha256": actual_sha,
        "semwright_expected_version": expected_version,
        "semwright_observed_version": observed_version,
        "semwright_native_sdk_source_pin": source_sha,
        "checks": {
            "schema_and_project_scope": True,
            "registry_generation": origin == "saved_reference",
            "regular_files_and_safe_paths": True,
            "cli_digest_matched": True,
            "cli_version_checked": probe_version,
            "broker_dispatch_tested": False,
        },
        "claims": {
            "broker_ready": "NOT_TESTED",
            "render_success": "NOT_RUN",
            "human_acceptance": "NOT_RUN",
            "source_attestation": "NOT_CLAIMED",
            "release_ready": False,
        },
    }


def write_new_receipt(destination: Path, report: dict) -> None:
    """Atomically publish a fully synced new report; never replace another.

    A failed write/fsync must leave the public destination absent. A collision
    must preserve the previous report byte-for-byte. No pathname-only fallback
    may rewrite the final file after hardlink publication has failed.
    """
    require(destination.is_absolute(), "receipt_destination_invalid")
    parent = safe_path(str(destination.parent), kind="directory",
                       code="receipt_destination_invalid")
    data = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    stage_name = f".{destination.name}.{uuid.uuid4().hex}.stage"
    stage_path = parent / stage_name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)

    directory_fd: int | None = None
    temporary_created = False
    published = False
    staged_identity = None
    inspected_parent = None
    try:
        inspected_parent = parent.lstat()
        require(stat.S_ISDIR(inspected_parent.st_mode),
                "receipt_destination_invalid")
        # On POSIX, keep the inspected directory inode open for both atomic
        # hardlink and cleanup. On Windows, CreateHardLinkW is used through
        # os.link on one same-volume temporary path. Never replace a target.
        if os.name == "posix":
            folder_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            directory_fd = os.open(parent, folder_flags)
            stat_dir = os.fstat(directory_fd)
            require(
                stat.S_ISDIR(stat_dir.st_mode)
                and stat_dir.st_dev == inspected_parent.st_dev
                and stat_dir.st_ino == inspected_parent.st_ino,
                "receipt_destination_invalid",
            )
            fd = os.open(stage_name, flags, 0o600, dir_fd=directory_fd)
        else:
            fd = os.open(stage_path, flags, 0o600)
        temporary_created = True
        with os.fdopen(fd, "wb") as stream:
            staged_identity = os.fstat(stream.fileno())
            require(stat.S_ISREG(staged_identity.st_mode), "receipt_write_refused")
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            complete = os.fstat(stream.fileno())
            require(
                complete.st_dev == staged_identity.st_dev
                and complete.st_ino == staged_identity.st_ino
                and complete.st_size == len(data),
                "receipt_write_refused",
            )
            if directory_fd is not None:
                os.link(stage_name, destination.name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False)
                committed = os.stat(destination.name,
                                    dir_fd=directory_fd, follow_symlinks=False)
            else:
                os.link(stage_path, destination)
                committed = destination.lstat()
            published = True
            require(
                stat.S_ISREG(committed.st_mode)
                and committed.st_dev == staged_identity.st_dev
                and committed.st_ino == staged_identity.st_ino
                and committed.st_size == len(data),
                "receipt_commit_uncertain",
            )
        # Remove only the name created by this call, not a previous report.
        if directory_fd is not None:
            os.unlink(stage_name, dir_fd=directory_fd)
            temporary_created = False
            os.fsync(directory_fd)
        else:
            os.unlink(stage_path)
            temporary_created = False
        safe_path(str(parent), kind="directory",
                  code="receipt_commit_uncertain")
        observed_parent = parent.lstat()
        require(
            stat.S_ISDIR(observed_parent.st_mode)
            and inspected_parent is not None
            and observed_parent.st_dev == inspected_parent.st_dev
            and observed_parent.st_ino == inspected_parent.st_ino,
            "receipt_commit_uncertain",
        )
    except OSError as exc:
        raise AuditError(
            "receipt_commit_uncertain" if published else "receipt_write_refused"
        ) from exc
    finally:
        if temporary_created and staged_identity is not None:
            try:
                current = (
                    os.stat(stage_name, dir_fd=directory_fd, follow_symlinks=False)
                    if directory_fd is not None else stage_path.lstat()
                )
                if (current.st_dev == staged_identity.st_dev
                        and current.st_ino == staged_identity.st_ino):
                    if directory_fd is not None:
                        os.unlink(stage_name, dir_fd=directory_fd)
                    else:
                        os.unlink(stage_path)
            except OSError:
                # Never delete a new occupant or the published destination.
                pass
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except OSError as exc:
                raise AuditError(
                    "receipt_commit_uncertain" if published else "receipt_write_refused"
                ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--connection-file", type=Path,
                        help="Absolute existing owner connection JSON")
    source.add_argument("--registry-root", type=Path,
                        help="Absolute installed app owner-native-connections directory")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--probe-version", action="store_true",
                        help="Explicit consent to run digest-matched CLI --version only")
    parser.add_argument("--output", type=Path,
                        help="New report path only; existing reports are never overwritten")
    args = parser.parse_args(argv)
    exit_code = 0
    try:
        receipt = inspect_connection(
            project_id=args.project_id,
            generation=args.generation,
            connection_file=args.connection_file,
            registry_root=args.registry_root,
            probe_version=args.probe_version,
        )
    except AuditError as exc:
        exit_code = 2
        receipt = {
            "schema": REPORT_SCHEMA,
            "application": "Motionwright",
            "status": "BLOCKED",
            "failure_code": exc.code,
            "claims": {"broker_ready": "NOT_TESTED", "render_success": "NOT_RUN",
                       "human_acceptance": "NOT_RUN",
                       "source_attestation": "NOT_CLAIMED",
                       "release_ready": False},
        }
    except Exception:
        # Never echo OS diagnostics, secrets, absolute paths, or raw CLI output.
        exit_code = 2
        receipt = {
            "schema": REPORT_SCHEMA, "application": "Motionwright",
            "status": "BLOCKED", "failure_code": "internal_preflight_error",
            "claims": {"broker_ready": "NOT_TESTED", "render_success": "NOT_RUN",
                       "human_acceptance": "NOT_RUN",
                       "source_attestation": "NOT_CLAIMED",
                       "release_ready": False},
        }
    if args.output is not None:
        try:
            write_new_receipt(args.output, receipt)
        except AuditError:
            print(json.dumps({"schema": REPORT_SCHEMA, "status": "BLOCKED",
                              "failure_code": "receipt_write_refused"}),
                  file=sys.stdout)
            return 3
    else:
        print(json.dumps(receipt, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
