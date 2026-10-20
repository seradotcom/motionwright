#!/usr/bin/env python3
"""Capture an owned build input and publish a complete NSIS include.

Existing outputs are never overwritten. Failed files remain for inspection.
This is build tooling, not an application runtime or execution capability.
"""
from __future__ import annotations

import argparse
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


def _capture(source: Path, capture: Path):
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


def _publish_include(include: Path, payload: bytes, capture: Path | None, captured=None) -> None:
    _require_plain_parents(include)
    _require_absent(include)
    fd, name = tempfile.mkstemp(
        prefix=".mw-nsis-include-", suffix=".pending", dir=include.parent
    )
    pending = Path(name)
    # Failure deliberately retains this file and the capture. The final
    # include name does not appear before the complete file is synced.
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
    if capture is not None:
        _require_plain_parents(capture)
        _require_unchanged(capture.lstat(), captured, "capture")
    _require_plain_parents(include)
    _require_unchanged(pending.lstat(), pending_info, "pending include")
    # Atomic exclusive publication on the same filesystem. There is no
    # replace/rename-overwrite fallback if hardlinks are unsupported.
    os.link(pending, include, follow_symlinks=False)
    try:
        pending.unlink()
    except OSError:
        # A redundant owned name is harmless after successful publication.
        pass


def capture_owned_file(
    source: str | os.PathLike[str],
    capture: str | os.PathLike[str],
    include: str | os.PathLike[str],
    symbol: str,
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
    sha256, size, captured = _capture(source_path, capture_path)
    definitions = (
        f'!define {symbol}_SOURCE "{_nsis_escape(str(capture_path))}"\n'
        f'!define {symbol}_SHA256 "{sha256}"\n'
        f'!define {symbol}_SIZE {size}\n'
    )
    _publish_include(include_path, definitions.encode("utf-8-sig"), capture_path, captured)
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
    actual, size, captured = _capture(manifest, manifest_capture)
    if actual != expected_manifest:
        raise CaptureError("predecessor manifest digest differs from the independent pin")
    with manifest_capture.open("rb") as stream:
        raw = stream.read(MAX_PREDECESSOR_MANIFEST_BYTES + 1)
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
    captured_inputs = []
    for (label, source, digest, expected_size), target in zip(inputs, targets):
        found, count, observed = _capture(source, target)
        if found != digest or count != expected_size:
            raise CaptureError(f"{label} differs from the pinned predecessor bytes")
        captured_inputs.append((target, observed))
    for target, observed in captured_inputs:
        _require_plain_parents(target)
        _require_unchanged(target.lstat(), observed, "predecessor capture")
    _publish_include(output, _predecessor_include(document, expected_manifest), manifest_capture, captured)
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
    parser.add_argument("--emit-predecessor", action="store_true")
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
