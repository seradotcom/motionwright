"""Bounded read-only GNU ELF build-id evidence, never binary execution.

Matching build-id is only a scoped identity clue; it cannot attest that the
packager copied a particular original or retained its redistribution license.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import struct

import candidate_package_inventory as inventory

MAX_BINARY = 64 * 1024 * 1024
MAX_NOTE_SEGMENT = 1024 * 1024
MAX_PROGRAM_HEADERS = 256
ELF64_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
ELF64_PHDR = struct.Struct("<IIQQQQQQ")
NOTE = struct.Struct("<III")
ARCH_X86_64 = 62
PT_NOTE = 4
NT_GNU_BUILD_ID = 3
BUILD_ID_MIN = 16
BUILD_ID_MAX = 64
HEX = re.compile(r"[0-9a-f]{32,128}\Z")


def require(ok, code):
    if not ok:
        raise ValueError(code)


def align4(value):
    return (value + 3) & ~3


def gnu_build_id(raw):
    """Parse PT_NOTE from ELF64 little-endian x86_64, no external parser/exec."""
    require(isinstance(raw, bytes) and len(raw) <= MAX_BINARY, "elf_input_budget")
    if raw[:4] != b"\x7fELF":
        return None
    require(len(raw) >= ELF64_HEADER.size, "elf_header_truncated")
    (ident, kind, machine, version, entry, phoff, shoff, flags,
     ehsize, phentsize, phnum, shentsize, shnum, shstrndx) = ELF64_HEADER.unpack_from(raw)
    if ident[4] != 2 or ident[5] != 1 or ident[6] != 1 or machine != ARCH_X86_64:
        return None
    require(kind in (2, 3) and version == 1 and
            ehsize == ELF64_HEADER.size and
            phentsize == ELF64_PHDR.size and
            0 < phnum <= MAX_PROGRAM_HEADERS and
            phoff >= ELF64_HEADER.size and
            phoff + phnum * phentsize <= len(raw),
            "elf_program_header_invalid")
    observed = []
    for i in range(phnum):
        (p_type, p_flags, off, vaddr, paddr, size, memsz, align) = \
            ELF64_PHDR.unpack_from(raw, phoff + i * phentsize)
        if p_type != PT_NOTE:
            continue
        require(size <= MAX_NOTE_SEGMENT and off + size <= len(raw),
                "elf_note_bounds")
        at, end = off, off + size
        while at < end:
            require(at + NOTE.size <= end, "elf_note_truncated")
            namesz, descsz, note_kind = NOTE.unpack_from(raw, at)
            at += NOTE.size
            name_bytes, description_bytes = align4(namesz), align4(descsz)
            require(name_bytes <= end - at and
                    description_bytes <= end - at - name_bytes,
                    "elf_note_length_invalid")
            name = raw[at:at + namesz]
            at += name_bytes
            desc = raw[at:at + descsz]
            at += description_bytes
            if name == b"GNU\x00" and note_kind == NT_GNU_BUILD_ID:
                require(BUILD_ID_MIN <= len(desc) <= BUILD_ID_MAX,
                        "elf_gnu_build_id_length")
                observed.append(desc.hex())
    require(len(observed) <= 1, "elf_ambiguous_build_id")
    if observed:
        require(HEX.fullmatch(observed[0]) is not None, "elf_build_id_format")
    return observed[0] if observed else None


def _path(root, absolute):
    require(isinstance(absolute, str) and absolute.startswith("/")
            and not absolute.startswith("//") and "\x00" not in absolute,
            "elf_host_path_invalid")
    parts = absolute.lstrip("/").split("/")
    require(all(part not in ("", ".", "..") for part in parts)
            and (parts[:2] == ["usr", "lib"] or parts[0] == "lib"),
            "elf_host_path_outside_library_roots")
    root = Path(root).resolve(strict=True)
    alias = root.joinpath(*parts)
    return root, alias


def _alias_stat(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def observed_library(root, declared_path, *, limit=MAX_BINARY):
    """Retain exact current bytes for one installed manifest-declared source.

    A symlink is permitted only if both the declared alias and its resolved
    regular target stay within the same library roots. The alias/target is
    rechecked after a stable, no-follow read of the resolved original bytes.
    Symlinks by themselves never supply an ELF identity.
    """
    require(type(limit) is int and 1 <= limit <= MAX_BINARY, "elf_read_budget")
    root, alias = _path(root, declared_path)
    try:
        before = alias.lstat()
        resolved = alias.resolve(strict=True)
        relative = resolved.relative_to(root)
        rel = "/" + relative.as_posix()
        _path(root, rel)
        target_stat = resolved.lstat()
        if not stat.S_ISREG(target_stat.st_mode) or target_stat.st_size > limit:
            return None
        raw = inventory.capture.stable_read(root, resolved, limit)
        build_id = gnu_build_id(raw)
        after = alias.lstat()
        require(_alias_stat(before) == _alias_stat(after)
                and resolved == alias.resolve(strict=True),
                "elf_manifest_alias_changed")
        return {
            "declared_path": declared_path,
            "resolved_path": rel,
            "alias_symlink": stat.S_ISLNK(before.st_mode),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "gnu_build_id": build_id,
            "status": "STABLE_READ_INSTALLED_LIBRARY",
        }
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        # Absent or inaccessible library is no identity proof.
        return None
