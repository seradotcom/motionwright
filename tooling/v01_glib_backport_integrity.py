#!/usr/bin/env python3
"""Exact upstream glib 0.18.5 crate and two-token safety backport conformance.

Research/candidate code only. This does NOT declare the RustSec advisory fixed,
prove installed Linux binary provenance, or authorize a Motionwright v0.1 release.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import stat
import tarfile

UPSTREAM_SHA256 = "233daaf6e83ae6a12a52055f568f9d7cf4671dabb78ff9560ab6da230ce00ee5"
VERSION = "0.18.5"
PREFIX = "glib-0.18.5/"
AFFECTED = "src/variant_iter.rs"
OLD_DECL = b"let p: *mut libc::c_char = std::ptr::null_mut();"
NEW_DECL = b"let mut p: *mut libc::c_char = std::ptr::null_mut();"
OLD_CALL = b"\n                &p,\n"
NEW_CALL = b"\n                &mut p,\n"
MAX_ARCHIVE_BYTES = 2_000_000
MAX_EXPANDED_BYTES = 10_000_000


class BackportError(ValueError):
    pass


def require(test: bool, message: str) -> None:
    if not test:
        raise BackportError(message)


def checked_backport(original: bytes) -> bytes:
    """Only the two FFI out-parameter mutability edits are authorized."""
    require(original.count(OLD_DECL) == 1, "upstream_mutability_declaration_changed")
    require(original.count(OLD_CALL) == 1, "upstream_mutability_call_changed")
    require(NEW_DECL not in original and NEW_CALL not in original,
            "upstream_already_patched_or_unexpected")
    expected = original.replace(OLD_DECL, NEW_DECL, 1).replace(OLD_CALL, NEW_CALL, 1)
    require(expected != original and b"ffi::g_variant_get_child(" in expected,
            "backport_context_unrecognized")
    return expected


def read_upstream(crate: Path) -> dict[str, bytes]:
    require(crate.is_file() and not crate.is_symlink(), "upstream_archive_not_regular")
    size = crate.stat().st_size
    require(0 < size <= MAX_ARCHIVE_BYTES, "upstream_archive_size_invalid")
    digest = sha256(crate.read_bytes()).hexdigest()
    require(digest == UPSTREAM_SHA256, "upstream_crate_checksum_mismatch")
    contents: dict[str, bytes] = {}
    total = 0
    with tarfile.open(crate, "r:gz") as archive:
        for member in archive:
            require(member.isfile() or member.isdir(), "unexpected_archive_member_type")
            require(member.name.startswith(PREFIX), "upstream_archive_prefix_invalid")
            subpath = member.name[len(PREFIX):]
            if not subpath:
                continue
            parts = PurePosixPath(subpath).parts
            require(parts and all(part not in ("", ".", "..") for part in parts)
                    and not subpath.startswith("/"), "unsafe_archive_member_path")
            if member.isdir():
                continue
            require(subpath not in contents, "duplicate_archive_member")
            total += member.size
            require(total <= MAX_EXPANDED_BYTES, "upstream_archive_unbounded")
            stream = archive.extractfile(member)
            require(stream is not None, "upstream_archive_member_unreadable")
            raw = stream.read(member.size + 1)
            require(len(raw) == member.size, "upstream_archive_member_length_changed")
            contents[subpath] = raw
    require(AFFECTED in contents and "Cargo.toml" in contents
            and "LICENSE" in contents and len(contents) >= 100,
            "upstream_archive_layout_unrecognized")
    return contents


def materialize_candidate(original: dict[str, bytes], root: Path) -> None:
    """Generate a disposable source candidate without committing upstream code.

    Only CI should opt in. An existing folder is never overwritten, and no
    parent path escapes the verified crate's safe POSIX member names.
    """
    require(not root.exists() and not root.is_symlink(),
            "candidate_destination_already_exists")
    require(root.parent.is_dir() and not root.parent.is_symlink(),
            "candidate_parent_unavailable")
    root.mkdir()
    for name, source in original.items():
        target = root.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = checked_backport(source) if name == AFFECTED else source
        with target.open("xb") as handle:
            handle.write(data)


def verify_vendor(original: dict[str, bytes], root: Path) -> dict:
    require(root.is_dir() and not root.is_symlink(), "vendor_directory_unavailable")
    actual: dict[str, bytes] = {}
    for file in root.rglob("*"):
        require(not file.is_symlink(), "vendor_symlink_refused")
        if file.is_dir():
            continue
        info = file.lstat()
        require(stat.S_ISREG(info.st_mode), "vendor_special_file_refused")
        relative = file.relative_to(root).as_posix()
        require(relative in original, "vendor_unreviewed_extra_file")
        actual[relative] = file.read_bytes()
    require(set(actual) == set(original), "vendored_files_differ_from_original_crate")
    expected = checked_backport(original[AFFECTED])
    for name, data in original.items():
        require(actual[name] == (expected if name == AFFECTED else data),
                "vendor_non_backport_modification")
    return {
        "schema": "motionwright.v01-glib-backport-candidate-evidence/1",
        "glib_version": VERSION,
        "source_crate_sha256": UPSTREAM_SHA256,
        "vendor_file_count": len(actual),
        "patched_file": AFFECTED,
        "upstream_patched_file_sha256": sha256(original[AFFECTED]).hexdigest(),
        "candidate_patched_file_sha256": sha256(actual[AFFECTED]).hexdigest(),
        "only_two_mutability_edits": True,
        "linux_binary_linkage_verified": False,
        "installed_linux_gui_verified": False,
        "upstream_rustsec_advisory_closed": False,
        "motionwright_v01_release_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-crate", type=Path, required=True)
    parser.add_argument("--vendor-root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--create-candidate", action="store_true",
                        help="Generate only into a new disposable CI directory")
    args = parser.parse_args()
    original = read_upstream(args.upstream_crate)
    if args.create_candidate:
        materialize_candidate(original, args.vendor_root)
    report = verify_vendor(original, args.vendor_root)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as out:
            json.dump(report, out, sort_keys=True, indent=2)
            out.write("\n")
    print("glib-v01-backport-candidate=SOURCE_EXACT_ONLY files="
          + str(report["vendor_file_count"])
          + " rustsec=OPEN linux_installed=NOT_RUN release=BLOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
