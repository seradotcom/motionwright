#!/usr/bin/env python3
"""Capture an owned build input and publish a complete NSIS include.

Existing outputs are never overwritten. Failed files remain for inspection.
This is build tooling, not an application runtime or execution capability.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unicodedata

MAX_SOURCE_BYTES = 2_147_483_647
COPY_CHUNK_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400
_SYMBOL = re.compile(r"MW_[A-Z0-9_]{1,48}", re.ASCII)
_DOS_DEVICE = re.compile(
    r"(?:CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³]+)",
    re.IGNORECASE | re.ASCII,
)


class CaptureError(ValueError):
    """The input or the exclusive capture contract was not satisfied."""


def _check_path_syntax(raw: str) -> None:
    if not raw:
        raise CaptureError("paths must be nonempty text")
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
           for c in raw):
        raise CaptureError("control or nonliteral Unicode in path")
    portable = raw.replace("\\", "/")
    if portable.startswith("//") or portable.casefold().startswith(
        ("/??/", "/device/", "/globalroot/")
    ):
        raise CaptureError("UNC and device namespaces are not build paths")
    drive, tail = ntpath.splitdrive(raw)
    if drive and (
        re.fullmatch(r"[A-Za-z]:", drive) is None
        or not tail.startswith(("/", "\\"))
    ):
        raise CaptureError("drive-relative or device path is not permitted")
    if ":" in tail:
        raise CaptureError("alternate data streams are not permitted")
    if "*" in raw or "?" in raw:
        raise CaptureError("NSIS wildcard paths are not permitted")
    if os.name == "nt" and not drive and tail.startswith(("/", "\\")):
        raise CaptureError("Windows root-relative paths are not permitted")
    if os.name != "nt" and "\\" in raw:
        raise CaptureError("backslashes are not POSIX build path separators")
    for part in re.split(r"[\\/]+", tail):
        if not part or part == ".":
            continue
        if part == ".." or part.endswith((" ", ".")):
            raise CaptureError("ambiguous path components are not permitted")
        stem = part.split(".", 1)[0].rstrip(" ")
        if _DOS_DEVICE.fullmatch(stem):
            raise CaptureError("reserved DOS device name in path")


def _validated_path(value: str | os.PathLike[str]) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str):
        raise CaptureError("paths must be text")
    _check_path_syntax(raw)
    path = Path(os.path.abspath(raw))
    _check_path_syntax(str(path))
    return path


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _require_regular(info: os.stat_result, label: str) -> None:
    if not stat.S_ISREG(info.st_mode) or _is_link_or_reparse(info):
        raise CaptureError(f"{label} must be an ordinary non-reparse file")
    if not info.st_ino:
        raise CaptureError(f"{label} has no usable file identity")


def _require_plain_parents(path: Path) -> None:
    for parent in reversed(path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_link_or_reparse(info):
            raise CaptureError("path parents must be ordinary directories")


def _object_identity(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_dev, info.st_ino, info.st_size


def _signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (*_object_identity(info), info.st_mtime_ns, info.st_ctime_ns)


def _require_same_object(observed, original, label: str) -> None:
    _require_regular(observed, label)
    if _object_identity(observed) != _object_identity(original):
        raise CaptureError(f"{label} identity or size changed")


def _require_unchanged(observed, original, label: str) -> None:
    _require_regular(observed, label)
    if _signature(observed) != _signature(original):
        raise CaptureError(f"{label} changed during capture")


def _require_path_descriptor_match(descriptor, path_info, label: str) -> None:
    # Win32 path and descriptor ctime may name different timestamps. Keep each
    # domain's full signature; join the observations only by comparable fields.
    _require_regular(path_info, label)
    _require_same_object(descriptor, path_info, label)
    for field in ("st_mtime_ns", "st_birthtime_ns", "st_birthtime"):
        if getattr(descriptor, field, None) != getattr(path_info, field, None):
            raise CaptureError(f"{label} path and descriptor timestamps differ")
    if os.name != "nt":
        _require_unchanged(descriptor, path_info, label)


def _source_change_time(fd: int) -> int:
    """Read change time from the same source handle, without reopening a path."""
    if os.name != "nt":
        return os.fstat(fd).st_ctime_ns

    import ctypes
    from ctypes import wintypes
    import msvcrt

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        ]

    # Windows stat ctime may be creation time. FileBasicInfo exposes the
    # metadata ChangeTime separately; compare its raw value only to itself.
    # The CRT descriptor retains ownership of this handle throughout capture.
    information = FileBasicInfo()
    if ctypes.sizeof(information) != 40:
        raise CaptureError("unsupported Windows file-information layout")
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel.GetFileInformationByHandleEx
        query.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        )
        query.restype = wintypes.BOOL
        handle = msvcrt.get_osfhandle(fd)
        if handle in (0, -1):
            raise CaptureError("source has no usable Windows handle")
        ok = query(handle, 0, ctypes.byref(information), ctypes.sizeof(information))
    except (AttributeError, OSError, ValueError) as error:
        raise CaptureError("Windows source change time is unavailable") from error
    if not ok:
        raise CaptureError(
            f"Windows source change-time query failed: {ctypes.get_last_error()}"
        )
    if information.ChangeTime <= 0:
        raise CaptureError("Windows source change time is not available")
    return information.ChangeTime


def _require_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise FileExistsError(f"output already exists: {path}")


def _read_flags() -> int:
    return (os.O_RDONLY | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0))


def _create_flags() -> int:
    return (os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))


def _nsis_escape(value: str) -> str:
    # Escape existing dollars before introducing the quote escape.
    return value.replace("$", "$$").replace('"', r'$\"')


def _capture(source: Path, capture: Path, expected_payload: dict | None = None):
    initial = source.lstat()
    _require_regular(initial, "source")
    if not 0 <= initial.st_size <= MAX_SOURCE_BYTES:
        raise CaptureError("source exceeds the NSIS file-size limit")
    digest = hashlib.sha256()
    copied = 0
    remaining = initial.st_size + 1
    source_fd = os.open(source, _read_flags())
    with os.fdopen(source_fd, "rb", buffering=0) as source_file:
        source_change_time = _source_change_time(source_file.fileno())
        opened_source = os.fstat(source_file.fileno())
        _require_path_descriptor_match(opened_source, initial, "source")
        _require_unchanged(source.lstat(), initial, "source")
        if expected_payload is not None:
            if (not isinstance(expected_payload, dict)
                    or set(expected_payload) != {"bytes", "sha256"}
                    or type(expected_payload["bytes"]) is not int
                    or not 0 < expected_payload["bytes"] <= MAX_SOURCE_BYTES
                    or not isinstance(expected_payload["sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", expected_payload["sha256"]) is None):
                raise CaptureError("distribution_admission_payload_invalid")
            expected_payload = dict(expected_payload)
            if initial.st_size != expected_payload["bytes"]:
                raise CaptureError("distribution_admission_payload_mismatch")
            # Inspect the same retained descriptor before creating an output.
            # Rewinding does not reopen a path or freeze concurrent writers.
            reviewed_digest = hashlib.sha256()
            reviewed_size = 0
            allowance = initial.st_size + 1
            while allowance:
                block = os.read(source_file.fileno(), min(COPY_CHUNK_BYTES, allowance))
                if not block:
                    break
                allowance -= len(block)
                reviewed_size += len(block)
                reviewed_digest.update(block)
            _require_plain_parents(source)
            _require_unchanged(os.fstat(source_file.fileno()), opened_source, "source")
            if _source_change_time(source_file.fileno()) != source_change_time:
                raise CaptureError("source change time changed before capture")
            _require_unchanged(source.lstat(), initial, "source")
            if (reviewed_size != expected_payload["bytes"]
                    or reviewed_digest.hexdigest() != expected_payload["sha256"]):
                raise CaptureError("distribution_admission_payload_mismatch")
            os.lseek(source_file.fileno(), 0, os.SEEK_SET)
        _require_plain_parents(capture)
        capture_fd = os.open(capture, _create_flags(), 0o600)
        with os.fdopen(capture_fd, "wb") as output:
            while remaining:
                block = os.read(source_file.fileno(), min(COPY_CHUNK_BYTES, remaining))
                if not block:
                    break
                remaining -= len(block)
                if copied + len(block) > initial.st_size:
                    raise CaptureError("source grew during capture")
                written = output.write(block)
                if written != len(block):
                    raise OSError("short write to owned capture")
                digest.update(block)
                copied += written
            if copied != initial.st_size:
                raise CaptureError("source ended before its initial size")
            if expected_payload is not None and (
                copied != expected_payload["bytes"]
                or digest.hexdigest() != expected_payload["sha256"]
            ):
                # An already-created failed capture stays private for inspection;
                # no include is published for bytes that differ from admission.
                raise CaptureError("distribution_admission_payload_changed")
            output.flush()
            os.fsync(output.fileno())
            _require_plain_parents(source)
            _require_unchanged(os.fstat(source_file.fileno()), opened_source, "source")
            if _source_change_time(source_file.fileno()) != source_change_time:
                raise CaptureError("source change time changed during capture")
            _require_unchanged(source.lstat(), initial, "source")
            open_capture = os.fstat(output.fileno())
            _require_regular(open_capture, "capture")
            if open_capture.st_size != copied:
                raise CaptureError("capture size differs from written bytes")
            _require_same_object(capture.lstat(), open_capture, "capture")
        # Win32 may finalize last-write time when the writing handle closes.
        # Preserve identity/size across close, then snapshot the final times.
        captured = capture.lstat()
        _require_same_object(captured, open_capture, "capture")
        _require_unchanged(os.fstat(source_file.fileno()), opened_source, "source")
        if _source_change_time(source_file.fileno()) != source_change_time:
            raise CaptureError("source change time changed during capture")
        _require_unchanged(source.lstat(), initial, "source")
    _require_plain_parents(capture)
    _require_unchanged(capture.lstat(), captured, "capture")
    return digest.hexdigest(), copied, captured



def _open_publication_reader(path: Path, label: str) -> int:
    """Retain the admitted file; Windows denies writers/deleters until close."""
    if os.name != "nt":
        return os.open(path, _read_flags() | getattr(os, "O_NONBLOCK", 0))

    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # OPEN_EXISTING, GENERIC_READ, FILE_SHARE_READ only. Never fall back to
    # sharing writes/deletion: an existing writer must also fail admission.
    handle = create(str(path), 0x80000000, 0x00000001, None, 3,
                    0x00200000 | 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        raise CaptureError(
            f"{label} cannot be held for publication: Windows error {error}"
        ) from ctypes.WinError(error)
    try:
        fd = msvcrt.open_osfhandle(
            handle, os.O_RDONLY | os.O_BINARY | getattr(os, "O_NOINHERIT", 0)
        )
    except BaseException:
        close(handle)
        raise
    # The CRT descriptor owns the Win32 handle after successful conversion.
    try:
        os.set_inheritable(fd, False)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _verify_publication_bytes(reader, expected_digest: str, size: int, label: str) -> None:
    """Hash at most the admitted length plus one byte from the retained handle."""
    if type(size) is not int or not 0 <= size <= MAX_SOURCE_BYTES:
        raise CaptureError(f"{label} has an invalid publication size")
    reader.seek(0)
    digest = hashlib.sha256()
    count, remaining = 0, size + 1
    while remaining:
        block = reader.read(min(COPY_CHUNK_BYTES, remaining))
        if not block:
            break
        count += len(block)
        remaining -= len(block)
        if count > size:
            raise CaptureError(f"{label} bytes differ from the admitted capture")
        digest.update(block)
    if count != size or digest.hexdigest() != expected_digest:
        raise CaptureError(f"{label} bytes differ from the admitted capture")


@contextmanager
def _publication_guard(path: Path, expected, digest: str, size: int, label: str):
    """Bind expected bytes and identity through publication.

    Windows sharing excludes concurrent writes and deletion while this context
    lives. POSIX retains and revalidates a descriptor; it does not claim a
    mandatory lock against unrelated writers. Neither guard extends to a later
    invocation of the NSIS compiler.
    """
    _require_plain_parents(path)
    try:
        fd = _open_publication_reader(path, label)
    except OSError as error:
        raise CaptureError(f"{label} cannot be held for publication") from error
    try:
        reader = os.fdopen(fd, "rb", buffering=0)
    except BaseException:
        os.close(fd)
        raise
    with reader:
        change_time = _source_change_time(reader.fileno())
        opened = os.fstat(reader.fileno())
        _require_path_descriptor_match(opened, expected, label)
        _require_unchanged(path.lstat(), expected, label)

        def check():
            _require_plain_parents(path)
            _require_unchanged(os.fstat(reader.fileno()), opened, label)
            _require_unchanged(path.lstat(), expected, label)
            if _source_change_time(reader.fileno()) != change_time:
                raise CaptureError(f"{label} changed during publication")

        _verify_publication_bytes(reader, digest, size, label)
        check()
        yield reader, check


def _publish_include(
    include: Path, payload: bytes, capture: Path | None, captured=None,
    *, capture_check=None,
) -> None:
    _require_plain_parents(include)
    _require_absent(include)
    fd, name = tempfile.mkstemp(
        prefix=".mw-nsis-include-", suffix=".pending", dir=include.parent
    )
    pending = Path(name)
    # Failure retains the capture and pending include for inspection.
    with os.fdopen(fd, "wb") as output:
        if output.write(payload) != len(payload):
            raise OSError("short write to pending NSIS include")
        output.flush()
        os.fsync(output.fileno())
        open_pending = os.fstat(output.fileno())
        if open_pending.st_size != len(payload):
            raise CaptureError("pending include size differs from payload")
    pending_info = pending.lstat()
    _require_same_object(pending_info, open_pending, "pending include")
    digest = hashlib.sha256(payload).hexdigest()
    with _publication_guard(
        pending, pending_info, digest, len(payload), "pending include"
    ) as (reader, pending_check):
        if capture is not None:
            _require_plain_parents(capture)
            _require_unchanged(capture.lstat(), captured, "capture")
            if capture_check is None:
                raise CaptureError("capture publication requires its retained guard")
            capture_check()
        _require_plain_parents(include)
        pending_check()
        # Both guards remain alive during atomic exclusive publication.
        # No replace/rename fallback is allowed.
        os.link(pending, include, follow_symlinks=False)
        _require_same_object(include.lstat(), pending_info, "published include")
        # Our hardlink can legitimately change ctime/nlink. Take a fresh
        # descriptor snapshot, then verify bytes without reusing old ctime.
        linked = os.fstat(reader.fileno())
        linked_change_time = _source_change_time(reader.fileno())
        _verify_publication_bytes(reader, digest, len(payload), "published include")
        _require_unchanged(os.fstat(reader.fileno()), linked, "published include")
        if _source_change_time(reader.fileno()) != linked_change_time:
            raise CaptureError("published include changed during verification")
        _require_same_object(include.lstat(), linked, "published include")
        if capture_check is not None:
            capture_check()
    # Close the pending guard before removing its redundant owned name.
    try:
        _require_plain_parents(pending)
        _require_same_object(pending.lstat(), pending_info, "pending include")
        pending.unlink()
    except (OSError, CaptureError):
        # A redundant name is harmless; never delete a known replacement.
        pass



def capture_owned_file(
    source: str | os.PathLike[str],
    capture: str | os.PathLike[str],
    include: str | os.PathLike[str],
    symbol: str,
    *,
    expected_payload: dict | None = None,
) -> dict[str, str | int]:
    if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
        raise CaptureError("symbol must match MW_[A-Z0-9_]{1,48}")
    source_path = _validated_path(source)
    capture_path = _validated_path(capture)
    include_path = _validated_path(include)
    keys = [os.path.normcase(str(p)) for p in (source_path, capture_path, include_path)]
    if len(set(keys)) != 3:
        raise CaptureError("source, capture and include must differ")
    for path in (source_path, capture_path, include_path):
        _require_plain_parents(path)
    # Preflight both outputs before creating either. O_EXCL and os.link
    # additionally enforce exclusivity if a destination appears later.
    _require_absent(capture_path)
    _require_absent(include_path)
    sha256, size, captured = _capture(source_path, capture_path, expected_payload)
    definitions = (
        f'!define {symbol}_SOURCE "{_nsis_escape(str(capture_path))}"\n'
        f'!define {symbol}_SHA256 "{sha256}"\n'
        f'!define {symbol}_SIZE {size}\n'
    )
    with _publication_guard(
        capture_path, captured, sha256, size, "capture"
    ) as (_, capture_check):
        _publish_include(
            include_path, definitions.encode("utf-8-sig"), capture_path, captured,
            capture_check=capture_check,
        )
    return {
        "capture": str(capture_path),
        "include": str(include_path),
        "sha256": sha256,
        "size": size,
    }



PREDECESSOR_ENV = "MW_NSIS_PREDECESSOR_MANIFEST"
PREDECESSOR_SHA_ENV = "MW_NSIS_PREDECESSOR_MANIFEST_SHA256"
MAX_PREDECESSOR_MANIFEST_BYTES = 1_048_576
MAX_PREDECESSOR_FILES = 4096
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_HEX_GIT = re.compile(r"[0-9a-f]{40}", re.ASCII)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CaptureError("duplicate JSON object key in predecessor manifest")
        result[key] = value
    return result


def _exact_keys(value, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise CaptureError(f"{label} has missing or unsupported fields")
    return value


def _digest(value, label: str) -> str:
    if not isinstance(value, str) or _HEX_SHA256.fullmatch(value) is None:
        raise CaptureError(f"{label} must be a lowercase SHA256")
    return value


def _literal(value, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise CaptureError(f"{label} must be bounded nonempty text")
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for c in value):
        raise CaptureError(f"{label} contains control or nonliteral text")
    return value


def _relative(value, label: str) -> str:
    raw = _literal(value, label)
    _check_path_syntax(raw)
    if any(c in raw for c in '<>|"'):
        raise CaptureError(f"{label} contains an unsupported Windows path character")
    drive, tail = ntpath.splitdrive(raw)
    parts = raw.replace("\\", "/").split("/")
    if drive or tail.startswith(("/", "\\")) or any(p in {"", ".", ".."} for p in parts):
        raise CaptureError(f"{label} must be a canonical relative path")
    if unicodedata.normalize("NFC", raw) != raw:
        raise CaptureError(f"{label} must use NFC text")
    return "/".join(parts)


def _bounded_bytes(value, label: str, maximum: int = MAX_SOURCE_BYTES) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise CaptureError(f"{label} is not an admitted byte count")
    return value


def _admitted_input(root: Path, record: dict, label: str) -> tuple[Path, str, int]:
    _exact_keys(record, {"path", "sha256", "bytes"}, label)
    relative = _relative(record["path"], label + ".path")
    path = _validated_path(root.joinpath(*relative.split("/")))
    _require_plain_parents(path)
    info = path.lstat()
    _require_regular(info, label)
    digest = _digest(record["sha256"], label + ".sha256")
    size = _bounded_bytes(record["bytes"], label + ".bytes")
    if info.st_size != size:
        raise CaptureError(f"{label} size differs from its pinned input")
    return path, digest, size


def _predecessor_include(document: dict | None, manifest_sha256: str = "") -> bytes:
    files = [] if document is None else document["files"]
    version = "" if document is None else document["version"]
    package = "" if document is None else document["package"]["sha256"]
    lines = [
        "; Generated from a hash-pinned build input; never from installed registry data.",
        f"!define MWPREV_ENABLED {int(document is not None)}",
        f'!define MWPREV_MANIFEST_SHA256 "{manifest_sha256}"',
        f'!define MWPREV_PACKAGE_SHA256 "{package}"',
        f'!define MWPREV_VERSION "{_nsis_escape(version)}"',
        "!macro MWPREV_LOOKUP Relative",
        "  StrCpy $MWINST_PrevFound 0",
        '  StrCpy $MWINST_PrevSha ""',
        '  StrCpy $MWINST_PrevSize ""',
    ]
    for index, record in enumerate(files):
        relative = _nsis_escape(record["relative"].replace("/", "\\"))
        condition = "If" if index == 0 else "ElseIf"
        lines.extend([
            f'  ${{{condition}}} "${{Relative}}" == "{relative}"',
            "    StrCpy $MWINST_PrevFound 1",
            f'    StrCpy $MWINST_PrevSha "{record["sha256"]}"',
            f'    StrCpy $MWINST_PrevSize "{record["bytes"]}"',
        ])
    if files:
        lines.append("  ${EndIf}")
    lines.extend(["!macroend", "!macro MWPREV_FOR_EACH Action"])
    for record in files:
        relative = _nsis_escape(record["relative"].replace("/", "\\"))
        lines.append(
            f'  !insertmacro ${{Action}} "{relative}" "{record["sha256"]}" "{record["bytes"]}"'
        )
    lines.extend(["!macroend", ""])
    return "\n".join(lines).encode("utf-8-sig")


def emit_predecessor_include(
    include: str | os.PathLike[str],
    *,
    application: str,
    bundle_id: str,
    arch: str,
    target_version: str,
    target_main_sha256: str,
    environment=None,
) -> dict:
    """Admit one predecessor profile as a build input, with exclusive captures.

    The expected manifest digest is supplied independently by the build owner.
    It is not read from that manifest or from the installed application's files.
    A digest proves integrity; the build owner must also review the package-to-
    ownership capture. This helper does not infer that authority or acceptance.
    """
    values = os.environ if environment is None else environment
    output = _validated_path(include)
    _require_plain_parents(output)
    _require_absent(output)
    expected_identity = {
        "application": _literal(application, "application"),
        "bundle_id": _literal(bundle_id, "bundle_id"),
        "arch": _literal(arch, "arch"),
    }
    expected_target = {
        "version": _literal(target_version, "target_version"),
        "main_sha256": _digest(target_main_sha256, "target_main_sha256"),
    }
    manifest_raw = values.get(PREDECESSOR_ENV, "")
    expected_manifest = values.get(PREDECESSOR_SHA_ENV, "")
    if not manifest_raw and not expected_manifest:
        _publish_include(output, _predecessor_include(None), None)
        return {"enabled": False, "files": 0, "manifest_sha256": None, "package_sha256": None}
    if not manifest_raw or not expected_manifest:
        raise CaptureError("a predecessor manifest and its independent digest are both required")
    expected_manifest = _digest(expected_manifest, PREDECESSOR_SHA_ENV)
    manifest = _validated_path(manifest_raw)
    _require_plain_parents(manifest)
    initial = manifest.lstat()
    _require_regular(initial, "predecessor manifest")
    if not 0 < initial.st_size <= MAX_PREDECESSOR_MANIFEST_BYTES:
        raise CaptureError("predecessor manifest exceeds its bounded size")
    manifest_capture = output.parent / "mw-predecessor-manifest.capture.json"
    _require_absent(manifest_capture)
    # Every input stays admitted until the final include is published.
    with ExitStack() as guards:
        actual, size, captured = _capture(manifest, manifest_capture)
        if actual != expected_manifest:
            raise CaptureError("predecessor manifest digest differs from the independent pin")
        manifest_reader, manifest_check = guards.enter_context(_publication_guard(
            manifest_capture, captured, expected_manifest, size, "manifest capture"
        ))
        manifest_reader.seek(0)
        raw = manifest_reader.read(MAX_PREDECESSOR_MANIFEST_BYTES + 1)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != expected_manifest:
            raise CaptureError("predecessor manifest capture changed before parsing")
        _require_unchanged(manifest_capture.lstat(), captured, "manifest capture")
        try:
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeError, ValueError) as error:
            raise CaptureError("predecessor manifest is not strict UTF-8 JSON") from error
        _exact_keys(document, {
            "schema", "identity", "version", "source_sha", "source_tree",
            "target", "package", "capture_receipt", "files",
        }, "predecessor manifest")
        if document["schema"] != "motionwright.nsis-predecessor/1":
            raise CaptureError("unsupported predecessor manifest schema")
        _exact_keys(document["identity"], set(expected_identity), "predecessor identity")
        _exact_keys(document["target"], set(expected_target), "predecessor target")
        if document["identity"] != expected_identity or document["target"] != expected_target:
            raise CaptureError("predecessor profile is not bound to this target application and payload")
        _literal(document["version"], "predecessor version")
        for field in ("source_sha", "source_tree"):
            if not isinstance(document[field], str) or _HEX_GIT.fullmatch(document[field]) is None:
                raise CaptureError(f"{field} must identify the predecessor Git object")
        records = document["files"]
        if not isinstance(records, list) or not 2 <= len(records) <= MAX_PREDECESSOR_FILES:
            raise CaptureError("predecessor ownership inventory must be complete and bounded")
        entries = []
        names = set()
        kinds = []
        for index, record in enumerate(records):
            _exact_keys(record, {"relative", "source", "sha256", "bytes", "kind"}, "owned file")
            relative = _relative(record["relative"], "owned relative")
            if relative != record["relative"]:
                raise CaptureError("owned paths must use canonical forward slashes")
            key = ntpath.normcase(relative.replace("/", "\\")).casefold()
            if key in names:
                raise CaptureError("duplicate or case-aliased owned destination")
            names.add(key)
            kind = record["kind"]
            if not isinstance(kind, str) or kind not in {"main", "resource", "external_binary", "uninstaller"}:
                raise CaptureError("unknown owned file kind")
            if kind == "uninstaller" and relative != "uninstall.exe":
                raise CaptureError("the predecessor self must be exactly uninstall.exe")
            if kind != "uninstaller" and key == "uninstall.exe":
                raise CaptureError("uninstaller destination has another role")
            if kind == "main" and ("/" in relative or not relative.casefold().endswith(".exe")):
                raise CaptureError("the predecessor main must be a root executable")
            if kind in {"main", "uninstaller"} and _bounded_bytes(record["bytes"], "executable bytes") == 0:
                raise CaptureError("predecessor executables must be nonempty")
            kinds.append(kind)
            item = {"path": record["source"], "sha256": record["sha256"], "bytes": record["bytes"]}
            entries.append(_admitted_input(manifest.parent, item, f"owned file {index}"))
        if kinds.count("main") != 1 or kinds.count("uninstaller") != 1:
            raise CaptureError("exactly one main and one independently captured uninstaller are required")
        inputs = [
            ("package", *_admitted_input(manifest.parent, document["package"], "package")),
            ("receipt", *_admitted_input(manifest.parent, document["capture_receipt"], "capture receipt")),
        ]
        if any(size == 0 for _, _, _, size in inputs):
            raise CaptureError("package and capture receipt must be nonempty")
        inputs += [(f"file-{index:04d}", *item) for index, item in enumerate(entries)]
        # Check all output names before the next capture. Every _capture still
        # creates exclusively and binds reads through the existing source helper.
        targets = [output.parent / f"mw-predecessor-{name}.capture.bin" for name, *_ in inputs]
        for target in targets:
            _require_absent(target)
        captured_checks = [manifest_check]
        for (label, source, digest, expected_size), target in zip(inputs, targets):
            found, count, observed = _capture(source, target)
            if found != digest or count != expected_size:
                raise CaptureError(f"{label} differs from the pinned predecessor bytes")
            _, check = guards.enter_context(_publication_guard(
                target, observed, digest, expected_size, "predecessor capture"
            ))
            captured_checks.append(check)

        def check_all_captures():
            for check in captured_checks:
                check()

        _publish_include(
            output, _predecessor_include(document, expected_manifest), manifest_capture, captured,
            capture_check=check_all_captures,
        )
        return {
            "enabled": True, "files": len(records), "manifest_sha256": expected_manifest,
            "package_sha256": document["package"]["sha256"],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source")
    parser.add_argument("--capture")
    parser.add_argument("--include", required=True)
    parser.add_argument("--symbol")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--emit-predecessor", action="store_true")
    modes.add_argument("--prepare-distribution", action="store_true")
    parser.add_argument("--application")
    parser.add_argument("--bundle-id")
    parser.add_argument("--arch")
    parser.add_argument("--target-version")
    parser.add_argument("--target-main-sha256")
    args = parser.parse_args(argv)
    try:
        if args.emit_predecessor:
            if any(value is not None for value in (args.source, args.capture, args.symbol)):
                raise CaptureError("predecessor and single-file capture arguments cannot be mixed")
            result = emit_predecessor_include(
                args.include, application=args.application, bundle_id=args.bundle_id,
                arch=args.arch, target_version=args.target_version,
                target_main_sha256=args.target_main_sha256,
            )
            print(json.dumps(result, sort_keys=True))
        else:
            if not all(value is not None for value in (args.source, args.capture, args.symbol)):
                raise CaptureError("source, capture and symbol are required for a single-file capture")
            if args.prepare_distribution:
                if args.symbol != "MW_MAIN" or args.arch is None:
                    raise CaptureError("distribution_admission_main_and_arch_required")
                if any(value is not None for value in (
                    args.application, args.bundle_id, args.target_version,
                    args.target_main_sha256,
                )):
                    raise CaptureError("predecessor identity requires --emit-predecessor")
                from source_lock_admission import verify_distribution_admission

                # The real template selects this mode literally. Read caller-pinned
                # environment inputs directly; never interpolate them into NSIS.
                helper = Path(__file__).absolute()
                _require_plain_parents(helper)
                _require_regular(helper.lstat(), "helper")
                payload = verify_distribution_admission(
                    helper.resolve(strict=True).parents[1],
                    arch=args.arch,
                    record_path=os.environ.get("MW_NSIS_DISTRIBUTION_ADMISSION"),
                    record_sha256=os.environ.get("MW_NSIS_DISTRIBUTION_ADMISSION_SHA256"),
                )
                capture_owned_file(
                    args.source, args.capture, args.include, args.symbol,
                    expected_payload=payload,
                )
            else:
                if args.symbol == "MW_MAIN":
                    raise CaptureError("distribution_admission_mode_required")
                if any(value is not None for value in (
                    args.application, args.bundle_id, args.arch,
                    args.target_version, args.target_main_sha256,
                )):
                    raise CaptureError("predecessor identity requires --emit-predecessor")
                capture_owned_file(args.source, args.capture, args.include, args.symbol)
    except (OSError, ValueError) as error:
        print(f"nsis_owned_files: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
