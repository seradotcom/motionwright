#!/usr/bin/env python3
"""Offline admission of the exact Tauri 2.12.1 IPC diagnostics source patch.

This checks retained source bytes and Cargo manifest/lock selection only. It
does not prove target compilation, runtime sink closure or release approval.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tomllib

SCHEMA = "motionwright.v01-tauri-ipc-desktop-source-admission/1"
VERSION = "2.12.1"
VENDOR = "vendor/tauri-2.12.1"
UPSTREAM = "tooling/upstream-crates/tauri-2.12.1.crate"
UPSTREAM_SHA256 = "ed99ee9694a2deb776d91cae48ac7411ddfc89ecae2f9b5041111d8c88f2ace9"
UPSTREAM_BYTES = 324_974
UPSTREAM_FILE_COUNT = 142
UPSTREAM_CONTENT_BYTES = 1_472_118
GLIB_VENDOR = "vendor/glib-0.18.5"
PATCHED_FILES = {
    "src/webview/mod.rs": {
        "before_bytes": 96_498,
        "before_sha256": "b5319b171c09a8e652d75585be39d92b387df370340ca90b58141b501c17009a",
        "after_bytes": 96_378,
        "after_sha256": "c9b3bde0579a02390a229c47fc1eec237f3d5d3d0320f728ed7e7a73ec52b941",
    },
    "src/ipc/protocol.rs": {
        "before_bytes": 24_671,
        "before_sha256": "e4b16a8800a249011753665eb28a004d8d6712af66c2fedbff094bc4fb99515e",
        "after_bytes": 24_314,
        "after_sha256": "5abef2714df16351b79292f352579dd09c235411aa2b6d633b22ebf3b094e119",
    },
    "src/ipc/channel.rs": {
        "before_bytes": 20_293,
        "before_sha256": "9d20fad897f8377484c7bd7f63b030653501a08b36f03def9cc5123c2a98623e",
        "after_bytes": 20_385,
        "after_sha256": "797331db70ed3391384c8b9636c46909436a6bcf94bf69e364467313c7b0f23a",
    },
}


class TauriSourceAdmissionError(ValueError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise TauriSourceAdmissionError(reason)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _portable_parts(name: str) -> tuple[str, ...]:
    parts = tuple(name.split("/"))
    reserved = {"con", "prn", "aux", "nul"}
    reserved.update(f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10))
    require(bool(name) and not name.startswith("/") and "\\" not in name
            and "\x00" not in name and all(
                part not in ("", ".", "..") and ":" not in part
                and not part.endswith((".", " "))
                and part.split(".", 1)[0].casefold() not in reserved
                for part in parts
            ), "unsafe_source_inventory_path")
    return parts


def _checked_path(root: Path, relative: str, *, directory: bool = False) -> Path:
    parts = _portable_parts(relative)
    current = root
    for index, part in enumerate(parts):
        current = current / part
        info = current.lstat()
        require(not _is_link(info), "source_path_is_link_or_reparse_point")
        require(stat.S_ISDIR(info.st_mode) if index < len(parts) - 1 or directory
                else stat.S_ISREG(info.st_mode), "source_path_not_regular")
    return current


def _read_regular(path: Path, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not _is_link(before),
            "source_file_not_regular")
    require(0 <= before.st_size <= max_bytes, "source_file_budget_exceeded")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode)
                and (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                "source_file_identity_changed")
        data = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    fingerprint = lambda item: (item.st_dev, item.st_ino, item.st_size,
                                item.st_mtime_ns, item.st_ctime_ns)
    require(not _is_link(current) and fingerprint(before) == fingerprint(opened)
            == fingerprint(after) == fingerprint(current)
            and len(data) == after.st_size, "source_file_changed_during_read")
    return data


def _archive_inventory(data: bytes) -> dict[str, bytes]:
    """Read a bounded regular-file inventory without extracting any archive path."""
    files: dict[str, bytes] = {}
    folded: set[str] = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            parts = _portable_parts(member.name)
            require(len(parts) > 1 and parts[0] == "tauri-2.12.1",
                    "unexpected_upstream_archive_root")
            require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE)
                    and not member.linkname, "upstream_archive_member_not_regular")
            name = "/".join(parts[1:])
            require(name.casefold() not in folded, "duplicate_upstream_archive_path")
            require(0 <= member.size <= 2 * 1024 * 1024
                    and len(files) < UPSTREAM_FILE_COUNT, "upstream_archive_budget_exceeded")
            total += member.size
            require(total <= 2 * 1024 * 1024, "upstream_archive_budget_exceeded")
            content = archive.extractfile(member)
            require(content is not None, "upstream_archive_content_absent")
            with content:
                payload = content.read(member.size + 1)
            require(len(payload) == member.size, "upstream_archive_content_size_changed")
            files[name] = payload
            folded.add(name.casefold())
    for name in files:
        require(all(str(parent).casefold() not in folded
                    for parent in PurePosixPath(name).parents if str(parent) != "."),
                "upstream_archive_file_directory_collision")
    return files


def read_upstream(path: Path) -> dict[str, bytes]:
    data = _read_regular(path, max_bytes=UPSTREAM_BYTES)
    require(len(data) == UPSTREAM_BYTES and _sha256(data) == UPSTREAM_SHA256,
            "tauri_upstream_crate_hash_changed")
    original = _archive_inventory(data)
    require(len(original) == UPSTREAM_FILE_COUNT
            and sum(map(len, original.values())) == UPSTREAM_CONTENT_BYTES,
            "tauri_upstream_inventory_changed")
    for name, expected in PATCHED_FILES.items():
        require(name in original and len(original[name]) == expected["before_bytes"]
                and _sha256(original[name]) == expected["before_sha256"],
                "tauri_patch_preimage_changed")
    return original


def _vendor_inventory(root: Path, expected_files: set[str], expected_directories: set[str]) -> tuple[dict[str, bytes], set[str]]:
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    folded: set[str] = set()

    def walk(current: Path, prefix: str) -> None:
        with os.scandir(current) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                name = prefix + entry.name
                _portable_parts(name)
                require(name.casefold() not in folded, "duplicate_vendor_inventory_path")
                folded.add(name.casefold())
                require(name in expected_files or name in expected_directories,
                        "tauri_vendor_inventory_changed")
                info = entry.stat(follow_symlinks=False)
                require(not _is_link(info), "vendor_entry_is_link_or_reparse_point")
                if stat.S_ISDIR(info.st_mode):
                    require(name in expected_directories, "tauri_vendor_inventory_changed")
                    directories.add(name)
                    walk(Path(entry.path), name + "/")
                else:
                    require(name in expected_files and stat.S_ISREG(info.st_mode),
                            "vendor_entry_not_regular")
                    files[name] = _read_regular(Path(entry.path), max_bytes=2 * 1024 * 1024)

    walk(root, "")
    return files, directories


def verify_vendor(original: dict[str, bytes], vendor_root: Path) -> dict:
    info = vendor_root.lstat()
    require(stat.S_ISDIR(info.st_mode) and not _is_link(info), "tauri_vendor_root_not_regular")
    expected_directories = {str(parent) for name in original
                            for parent in PurePosixPath(name).parents if str(parent) != "."}
    files, directories = _vendor_inventory(vendor_root, set(original), expected_directories)
    require(files.keys() == original.keys() and directories == expected_directories,
            "tauri_vendor_inventory_changed")
    for name, upstream in original.items():
        actual = files[name]
        if name in PATCHED_FILES:
            expected = PATCHED_FILES[name]
            require(len(actual) == expected["after_bytes"]
                    and _sha256(actual) == expected["after_sha256"],
                    "tauri_patch_postimage_changed")
        else:
            require(actual == upstream, "tauri_unchanged_source_file_changed")
    package = tomllib.loads(files["Cargo.toml"].decode("utf-8")).get("package", {})
    require(package.get("name") == "tauri" and package.get("version") == VERSION,
            "tauri_vendor_manifest_identity_changed")
    return {"vendor_file_count": len(files), "patched_files": PATCHED_FILES,
            "all_other_files_identical_to_upstream": True}


def admit_patched_tauri(root: Path) -> dict:
    root = root.absolute()
    try:
        root_info = root.lstat()
        require(stat.S_ISDIR(root_info.st_mode) and not _is_link(root_info),
                "repository_root_not_regular")

        def read_toml(relative: str) -> dict:
            return tomllib.loads(_read_regular(_checked_path(root, relative)).decode("utf-8"))

        manifest = read_toml("Cargo.toml")
        require(manifest.get("patch") == {"crates-io": {
            "glib": {"path": GLIB_VENDOR}, "tauri": {"path": VENDOR},
        }} and "replace" not in manifest, "unexpected_cargo_source_override")
        lock = read_toml("Cargo.lock")
        packages = lock.get("package")
        require(isinstance(packages, list) and all(isinstance(row, dict) for row in packages),
                "missing_cargo_lock_packages")
        rows = [row for row in packages if row.get("name") == "tauri"]
        require(len(rows) == 1 and rows[0].get("version") == VERSION,
                "tauri_locked_version_or_identity_changed")
        require("source" not in rows[0] and "checksum" not in rows[0],
                "tauri_lock_did_not_select_local_patch")
        desktop = read_toml("apps/desktop/src-tauri/Cargo.toml")
        dependencies = desktop.get("dependencies")
        require(isinstance(dependencies, dict),
                "desktop_tauri_dependency_not_exact_registry_pin")
        dependency = dependencies.get("tauri")
        require(isinstance(dependency, dict) and dependency.get("version") == "=2.12.1"
                and set(dependency) <= {"version", "features", "default-features", "optional"},
                "desktop_tauri_dependency_not_exact_registry_pin")
        original = read_upstream(_checked_path(root, UPSTREAM))
        custody = verify_vendor(original, _checked_path(root, VENDOR, directory=True))
    except TauriSourceAdmissionError:
        raise
    except (OSError, UnicodeError, ValueError, tarfile.TarError, EOFError) as exc:
        raise TauriSourceAdmissionError("tauri_source_admission_input_invalid") from exc
    return {
        "schema": SCHEMA, "crate": "tauri", "selected_version": VERSION,
        "source_mode": "local-path-exact-three-file-ipc-diagnostics-patch",
        "upstream_sha256": UPSTREAM_SHA256,
        "upstream_file_count": custody["vendor_file_count"],
        "patched_files": custody["patched_files"],
        "local_cargo_manifest_and_lock_selected": True,
        "all_other_files_identical_to_upstream": True,
        "target_compilation_verified": False,
        "runtime_diagnostic_sink_closure_verified": False,
        "hostile_input_confidentiality_verified": False,
        "release_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = admit_patched_tauri(args.root)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write("\n")
    print("v01-tauri-ipc-source=VERIFIED lock=LOCAL_PATCH other_files=EXACT "
          "compile=NOT_VERIFIED runtime_sinks=NOT_VERIFIED release=BLOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
