"""Admit a reviewed source record only while its retained bytes are verifiable.

This is an offline consistency check, not a license grant, historical approval,
proof of remote Git ancestry, or proof that the complete SDK is available.
No field is executed and this module performs no downloads or writes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tomllib

MAX_EVIDENCE_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 256 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}")
SHA1 = re.compile(r"[0-9a-f]{40}")
PATH_PART = re.compile(r"[A-Za-z0-9_.-]+")
LICENSE_EXPRESSION = "MIT OR Apache-2.0"
REQUIRED_FILES = {"Cargo.toml", "LICENSE-MIT", "LICENSE-APACHE"}
_WINDOWS_STAT = os.name == "nt"
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    prefix + str(index)
    for prefix in ("COM", "LPT") for index in range(1, 10)
}


class SourceAdmissionError(ValueError):
    """A static refusal code; never includes source bytes or private paths."""


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise SourceAdmissionError("source_admission_" + code)


def _object(value: object, keys: set[str], code: str) -> dict:
    _need(isinstance(value, dict) and set(value) == keys, code)
    return value


def _text(value: object, maximum: int = 2048) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and (
        value == value.strip() and all(ord(char) >= 32 for char in value)
    )


def _relative(value: object) -> tuple[str, ...]:
    _need(isinstance(value, str) and 0 < len(value) <= 512,
          "evidence_path_invalid")
    parts = value.split("/")
    _need(all(
        PATH_PART.fullmatch(part) is not None
        and part not in {".", ".."}
        and not part.endswith(".")
        and part.split(".", 1)[0].upper() not in RESERVED_NAMES
        for part in parts
    ), "evidence_path_invalid")
    return tuple(parts)


def _redirect(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _identity(info: os.stat_result) -> tuple:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
        getattr(info, "st_file_attributes", 0),
    )


def _opened_identity_matches(observed: tuple, opened: os.stat_result) -> bool:
    actual = _identity(opened)
    if _WINDOWS_STAT:
        # CPython can report creation time through lstat and change time through
        # fstat. Bind the path to its descriptor using all other identity fields;
        # retain ctime in both complete same-API before/after comparisons below.
        return observed[:6] + observed[7:] == actual[:6] + actual[7:]
    return observed == actual


def _inspect(root: Path, parts: tuple[str, ...]) -> tuple[Path, list[tuple]]:
    path = root
    observations = []
    for index, part in enumerate((None, *parts)):
        if part is not None:
            path = path / part
        info = path.lstat()
        _need(not _redirect(info), "evidence_path_invalid")
        is_leaf = index == len(parts)
        _need(stat.S_ISREG(info.st_mode) if is_leaf else stat.S_ISDIR(info.st_mode),
              "evidence_not_regular")
        if is_leaf:
            _need(info.st_nlink == 1, "evidence_path_invalid")
        observations.append(_identity(info))
    return path, observations


def _read(root: Path, relative: object, maximum: int) -> bytes:
    parts = _relative(relative)
    try:
        path, before = _inspect(root, parts)
        _need(0 < before[-1][4] <= maximum, "evidence_bounds")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as reader:
            first = os.fstat(reader.fileno())
            _need(not _redirect(first) and stat.S_ISREG(first.st_mode),
                  "evidence_not_regular")
            _need(_opened_identity_matches(before[-1], first), "evidence_changed")
            data = reader.read(maximum + 1)
            last = os.fstat(reader.fileno())
        _, after = _inspect(root, parts)
        _need(before == after and _identity(first) == _identity(last)
              and len(data) == first.st_size, "evidence_changed")
        _need(0 < len(data) <= maximum, "evidence_bounds")
        return data
    except (OSError, ValueError) as exc:
        if isinstance(exc, SourceAdmissionError):
            raise
        raise SourceAdmissionError("source_admission_evidence_unavailable") from exc


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, "evidence_json_invalid")
        result[key] = value
    return result


def load_source_lock(root: Path) -> object:
    """Read the bounded authoritative lock without accepting duplicate keys."""
    encoded = _read(Path(root), "SOURCE_LOCK.json", MAX_EVIDENCE_BYTES)
    try:
        return json.loads(encoded.decode("utf-8"), object_pairs_hook=_strict_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, SourceAdmissionError):
            raise
        raise SourceAdmissionError("source_admission_lock_json_invalid") from exc


def verify_source_lock(root: Path, lock: object) -> None:
    """Check the Semwright record and its bounded, retained metadata/license bytes.

    A reviewer must supply the verified record. Matching hashes establish only
    consistency with that record; they do not establish who authorized its use.
    """
    _need(isinstance(lock, dict), "lock_invalid")
    _need(type(lock.get("schema_version")) is int
          and lock["schema_version"] == 1, "lock_invalid")
    dependencies = lock.get("dependencies")
    _need(isinstance(dependencies, dict), "lock_invalid")
    source = dependencies.get("semwright")
    _need(isinstance(source, dict), "lock_invalid")
    repository = source.get("repository")
    _need(repository == "https://github.com/seradotcom/semwright",
          "repository_unsupported")
    revision = source.get("revision")
    version = source.get("version")
    license_expression = source.get("license_observed")
    _need(_text(repository) and isinstance(revision, str)
          and SHA1.fullmatch(revision) is not None and _text(version, 128),
          "lock_invalid")
    _need(license_expression == LICENSE_EXPRESSION, "license_unsupported")
    verification = source.get("verification")
    _need(isinstance(verification, dict)
          and verification.get("status") == "verified", "unverified")
    _object(verification, {"schema", "status", "repository", "revision", "evidence"},
            "verification_invalid")
    _need(verification["schema"] == "motionwright.source-verification/1"
          and verification["repository"] == repository
          and verification["revision"] == revision, "verification_mismatch")
    reference = _object(verification["evidence"], {"path", "sha256"},
                        "evidence_reference_invalid")
    _need(isinstance(reference["sha256"], str)
          and SHA256.fullmatch(reference["sha256"]) is not None,
          "evidence_reference_invalid")
    try:
        supplied_root = Path(root).absolute()
        _need(not _redirect(supplied_root.lstat()), "evidence_path_invalid")
        root = supplied_root.resolve(strict=True)
        _need(root.is_dir(), "evidence_path_invalid")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        if isinstance(exc, SourceAdmissionError):
            raise
        raise SourceAdmissionError("source_admission_evidence_unavailable") from exc
    encoded = _read(root, reference["path"], MAX_EVIDENCE_BYTES)
    _need(hashlib.sha256(encoded).hexdigest() == reference["sha256"],
          "evidence_hash_mismatch")
    try:
        evidence = json.loads(encoded.decode("utf-8"),
                              object_pairs_hook=_strict_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, SourceAdmissionError):
            raise
        raise SourceAdmissionError("source_admission_evidence_json_invalid") from exc
    _object(evidence, {"schema", "repository", "revision", "version",
                      "license_expression", "files"}, "evidence_invalid")
    _need(evidence["schema"] == "motionwright.source-evidence/1"
          and evidence["repository"] == repository
          and evidence["revision"] == revision
          and evidence["version"] == version
          and evidence["license_expression"] == license_expression,
          "evidence_binding_mismatch")
    records = evidence["files"]
    _need(isinstance(records, list) and len(records) == len(REQUIRED_FILES),
          "evidence_files_invalid")
    captured = {}
    local_paths = {"/".join(_relative(reference["path"])).casefold()}
    for record in records:
        record = _object(record, {"upstream_path", "path", "bytes", "sha256",
                                  "git_blob_sha1"}, "evidence_file_invalid")
        upstream = record["upstream_path"]
        _need(isinstance(upstream, str) and upstream in REQUIRED_FILES
              and upstream not in captured, "evidence_files_invalid")
        local = "/".join(_relative(record["path"])).casefold()
        _need(local not in local_paths, "evidence_path_invalid")
        local_paths.add(local)
        _need(type(record["bytes"]) is int
              and 0 < record["bytes"] <= MAX_SOURCE_BYTES,
              "evidence_bounds")
        _need(isinstance(record["sha256"], str)
              and SHA256.fullmatch(record["sha256"]) is not None
              and isinstance(record["git_blob_sha1"], str)
              and SHA1.fullmatch(record["git_blob_sha1"]) is not None,
              "evidence_file_invalid")
        data = _read(root, record["path"], MAX_SOURCE_BYTES)
        _need(len(data) == record["bytes"], "evidence_size_mismatch")
        _need(hashlib.sha256(data).hexdigest() == record["sha256"],
              "evidence_hash_mismatch")
        header = b"blob " + str(len(data)).encode("ascii") + b"\0"
        _need(hashlib.sha1(header + data, usedforsecurity=False).hexdigest()
              == record["git_blob_sha1"], "evidence_git_blob_mismatch")
        captured[upstream] = data
    _need(set(captured) == REQUIRED_FILES, "evidence_files_invalid")
    try:
        metadata = tomllib.loads(captured["Cargo.toml"].decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceAdmissionError("source_admission_metadata_invalid") from exc
    workspace = metadata.get("workspace")
    _need(isinstance(workspace, dict), "metadata_invalid")
    package = workspace.get("package")
    _need(isinstance(package, dict), "metadata_invalid")
    _need(package.get("version") == version
          and package.get("license") == license_expression,
          "metadata_mismatch")


# This caller-pinned disposition is separate from source consistency. A matching
# digest identifies reviewed bytes; neither an issuer string nor an SPDX value
# authenticates a grant. The reviewed build caller supplies the expected digest.
DISTRIBUTION_CARGO_INPUTS = (
    "Cargo.toml", "Cargo.lock", "apps/desktop/src-tauri/Cargo.toml",
    "crates/native/Cargo.toml",
)
MAX_CARGO_INPUT_BYTES = 2 * 1024 * 1024
MAX_DISTRIBUTION_PAYLOAD_BYTES = 2_147_483_647


def _distribution_need(condition: bool, code: str) -> None:
    if not condition:
        raise SourceAdmissionError("distribution_admission_" + code)


def _distribution_object(value: object, keys: set[str], code: str) -> dict:
    _distribution_need(isinstance(value, dict) and set(value) == keys, code)
    return value


def _distribution_json(data: bytes) -> object:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceAdmissionError("distribution_admission_json_invalid") from exc


def distribution_scope(arch: str) -> dict:
    """The selected Windows main executable boundary, not a global copy policy."""
    _distribution_need(isinstance(arch, str) and arch in {"x64", "arm64", "x86"},
                       "arch_invalid")
    return {
        "action": "prepare-distribution",
        "component": "motionwright.desktop.main",
        "destination_class": "windows.nsis.capture-include",
        "arch": arch,
        "policy": "motionwright.nsis-main-distribution/1",
    }


def distribution_source_context(root: Path) -> dict:
    """Bind retained source evidence and the selected Cargo input bytes.

    This does not resolve Cargo, prove a binary was built from these inputs,
    authenticate a reviewer, or grant distribution rights.
    """
    root = Path(root)
    encoded = _read(root, "SOURCE_LOCK.json", MAX_EVIDENCE_BYTES)
    lock = _distribution_json(encoded)
    verify_source_lock(root, lock)
    inputs = {
        relative: hashlib.sha256(
            _read(root, relative, MAX_CARGO_INPUT_BYTES)
        ).hexdigest()
        for relative in DISTRIBUTION_CARGO_INPUTS
    }
    return {
        "source_lock_sha256": hashlib.sha256(encoded).hexdigest(),
        "cargo_inputs": inputs,
    }


def _distribution_reference(root: Path, value: object, seen: set[str]) -> bytes:
    reference = _distribution_object(
        value, {"path", "bytes", "sha256"}, "reference_invalid")
    key = "/".join(_relative(reference["path"])).casefold()
    _distribution_need(key not in seen, "reference_reused")
    seen.add(key)
    _distribution_need(type(reference["bytes"]) is int
                       and 0 < reference["bytes"] <= MAX_EVIDENCE_BYTES
                       and isinstance(reference["sha256"], str)
                       and SHA256.fullmatch(reference["sha256"]) is not None,
                       "reference_invalid")
    encoded = _read(root, reference["path"], MAX_EVIDENCE_BYTES)
    _distribution_need(len(encoded) == reference["bytes"]
                       and hashlib.sha256(encoded).hexdigest() == reference["sha256"],
                       "reference_mismatch")
    return encoded


def _distribution_payload(value: object) -> dict:
    payload = _distribution_object(value, {"bytes", "sha256"}, "payload_invalid")
    _distribution_need(type(payload["bytes"]) is int
                       and 0 < payload["bytes"] <= MAX_DISTRIBUTION_PAYLOAD_BYTES
                       and isinstance(payload["sha256"], str)
                       and SHA256.fullmatch(payload["sha256"]) is not None,
                       "payload_invalid")
    return dict(payload)


def verify_distribution_admission(
    root: Path, *, arch: str, record_path: object, record_sha256: object,
) -> dict:
    """Return the reviewed payload identity only for an applicable disposition.

    Source consistency is checked before deciding disposition. Negative records
    never authorize payload inspection/capture. Positive records additionally
    require reviewed build evidence binding the same inputs, scope and payload.
    All records are bounded, read once by retained descriptor, and never executed.
    """
    root = Path(root)
    scope = distribution_scope(arch)
    context = distribution_source_context(root)
    _distribution_need(isinstance(record_path, str) and bool(record_path)
                       and isinstance(record_sha256, str)
                       and SHA256.fullmatch(record_sha256) is not None,
                       "missing")
    key = "/".join(_relative(record_path)).casefold()
    encoded = _read(root, record_path, MAX_EVIDENCE_BYTES)
    _distribution_need(hashlib.sha256(encoded).hexdigest() == record_sha256,
                       "record_hash_mismatch")
    record = _distribution_object(
        _distribution_json(encoded),
        {"schema", "status", "scope", "source", "review", "payload", "build_provenance"},
        "record_invalid",
    )
    _distribution_need(record["schema"] == "motionwright.distribution-admission/1",
                       "schema_invalid")
    _distribution_need(isinstance(record["status"], str)
                       and record["status"] in {"unresolved", "prohibited", "permitted"},
                       "status_invalid")
    _distribution_need(record["source"] == context, "source_mismatch")
    _distribution_need(record["scope"] == scope, "scope_mismatch")
    review = _distribution_object(
        record["review"], {"issuer", "reference", "basis", "evidence", "obligations"},
        "review_invalid",
    )
    _distribution_need(all(_text(review[field]) for field in ("issuer", "reference", "basis")),
                       "review_invalid")
    references = review["evidence"]
    _distribution_need(isinstance(references, list) and 1 <= len(references) <= 16,
                       "review_invalid")
    obligations = review["obligations"]
    _distribution_need(isinstance(obligations, list) and len(obligations) <= 16
                       and all(_text(item) for item in obligations), "review_invalid")
    seen = {key}
    for reference in references:
        _distribution_reference(root, reference, seen)
    if record["status"] != "permitted":
        _distribution_need(record["payload"] is None and record["build_provenance"] is None,
                           "negative_record_invalid")
        raise SourceAdmissionError("distribution_admission_" + record["status"])
    payload = _distribution_payload(record["payload"])
    provenance = _distribution_object(
        _distribution_json(_distribution_reference(root, record["build_provenance"], seen)),
        {"schema", "source", "scope", "payload"}, "build_invalid",
    )
    _distribution_need(provenance["schema"] == "motionwright.distribution-build/1"
                       and provenance["source"] == context
                       and provenance["scope"] == scope
                       and _distribution_payload(provenance["payload"]) == payload,
                       "build_mismatch")
    return payload
