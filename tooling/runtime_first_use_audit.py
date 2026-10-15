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


def read_json(path: Path, *, code: str, limit: int = MAX_JSON) -> dict:
    safe_path(str(path), kind="regular", code=code)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        handle = os.open(path, flags)
        with os.fdopen(handle, "rb") as source:
            metadata = os.fstat(source.fileno())
            require(stat.S_ISREG(metadata.st_mode) and 0 < metadata.st_size <= limit, code)
            contents = source.read(limit + 1)
    except OSError as exc:
        raise AuditError(code) from exc
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
    safe_path(str(path), kind="regular", code="executable_invalid")
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        handle = os.open(path, flags)
        with os.fdopen(handle, "rb") as source:
            before = os.fstat(source.fileno())
            require(stat.S_ISREG(before.st_mode)
                    and 0 < before.st_size <= MAX_EXECUTABLE, "executable_invalid")
            consumed = 0
            for block in iter(lambda: source.read(128 * 1024), b""):
                consumed += len(block)
                require(consumed <= MAX_EXECUTABLE, "executable_invalid")
                digest.update(block)
            after = os.fstat(source.fileno())
            require(before.st_size == consumed == after.st_size
                    and before.st_mtime_ns == after.st_mtime_ns, "executable_changed")
    except OSError as exc:
        raise AuditError("executable_invalid") from exc
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
    session = safe_path(connection["session_file"], kind="regular",
                        code="session_invalid")
    safe_path(connection["socket"], kind="any", code="socket_unavailable")
    safe_path(connection["output_root"], kind="directory",
              code="output_root_invalid")
    require(0 < session.stat().st_size <= MAX_JSON, "session_invalid")
    if os.name == "posix":
        require(session.stat().st_mode & 0o077 == 0, "session_not_private")
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
    """Creates a NEW receipt; never overwrites an old support/evidence file."""
    require(destination.is_absolute(), "receipt_destination_invalid")
    safe_path(str(destination.parent), kind="directory",
              code="receipt_destination_invalid")
    data = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise AuditError("receipt_write_refused") from exc


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
