"""Bounded offline reads with stable ancestors and an unchanged regular leaf.

This standalone derivation retains the source_lock_admission opened-identity
contract: only Windows path-stat versus descriptor-stat ctime may differ.
Both same-API temporal comparisons retain ctime. Ancestors bind dev/ino/type/
attributes; directory size, timestamps, link count and permission bits describe
directory contents or metadata, not the selected file's identity.

The caller still owns root/ACL authorization, resource limits and external
digest/size pins. This module grants no access, decrypts nothing and writes
nothing. Its snapshot checks do not claim continuous namespace exclusion.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

POLICY = "motionwright.fixedtxt-stable-file-read/1"
_FIELDS = ("dev", "ino", "mode", "nlink", "size", "mtime_ns", "ctime_ns",
           "attributes")
_ANCESTOR_FIELDS = ("dev", "ino", "kind", "attributes")
_WINDOWS_STAT = os.name == "nt"
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class StableReadRejected(ValueError):
    """Static refusal plus numeric/component diagnostics, never paths or bytes."""

    def __init__(self, code, *, stage, component=None, field=None,
                 expected=None, observed=None):
        super().__init__(code)
        self.code = code
        self.diagnostic = {
            "policy": POLICY, "code": code, "stage": stage,
            "component": component, "field": field,
            "expected": expected, "observed": observed,
        }


def _reject(code, **details):
    raise StableReadRejected(code, **details) from None


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            getattr(info, "st_file_attributes", 0))


def _validate(row, *, leaf, stage, component):
    if stat.S_ISLNK(row[2]) or row[7] & _REPARSE:
        _reject("reparse_or_symlink_path", stage=stage, component=component,
                field="kind" if stat.S_ISLNK(row[2]) else "attributes",
                observed=stat.S_IFMT(row[2]) if stat.S_ISLNK(row[2]) else row[7])
    if not (stat.S_ISREG(row[2]) if leaf else stat.S_ISDIR(row[2])):
        _reject("path_kind", stage=stage, component=component, field="kind",
                expected=stat.S_IFREG if leaf else stat.S_IFDIR,
                observed=stat.S_IFMT(row[2]))
    if leaf and row[3] != 1:
        _reject("hardlinked_input", stage=stage, component=component,
                field="nlink", expected=1, observed=row[3])


def _inspect_path(path, *, stage):
    current = Path(path.anchor)
    rows = []
    for index, part in enumerate((None, *path.parts[1:])):
        if part is not None:
            current /= part
        try:
            row = _identity(current.lstat())
        except OSError as exc:
            _reject("input_unavailable", stage=stage, component=index,
                    field="errno", observed=exc.errno)
        _validate(row, leaf=index == len(path.parts) - 1,
                  stage=stage, component=index)
        rows.append(row)
    return tuple(rows)


def _compare(expected, observed, fields, *, code, stage, component,
             omit_ctime=False):
    for field, before, after in zip(fields, expected, observed):
        if field == "ctime_ns" and omit_ctime:
            continue
        if before != after:
            _reject(code, stage=stage, component=component, field=field,
                    expected=before, observed=after)


def _ancestor_identity(row):
    return (row[0], row[1], stat.S_IFMT(row[2]), row[7])


def _compare_namespace(before, after):
    for index, (first, last) in enumerate(zip(before, after)):
        leaf = index == len(before) - 1
        _compare(first if leaf else _ancestor_identity(first),
                 last if leaf else _ancestor_identity(last),
                 _FIELDS if leaf else _ANCESTOR_FIELDS,
                 code="input_changed_during_read", stage="namespace",
                 component=index)


def read_stable(path, limit, *, prefix=None):
    """Return bytes from one unchanged, single-link regular file.

    Absolute lexical paths only; no resolution through symlinks or reparse
    points. The complete namespace is checked before and after a held-FD read.
    The limit bounds the whole file, even for a prefix read. Full reads request
    limit + 1 bytes to detect growth; prefix reads request at most limit.
    Errors expose .code and .diagnostic (zero-based component from the anchor).
    Digest and receipt authority remain with the caller.
    """
    if type(limit) is not int or not 0 <= limit < sys.maxsize:
        _reject("regular_file_budget", stage="arguments", field="limit")
    if prefix is not None and (type(prefix) is not int
                               or not 0 <= prefix <= limit):
        _reject("regular_file_budget", stage="arguments", field="prefix")
    try:
        path = Path(path)
    except (TypeError, ValueError):
        _reject("path_invalid", stage="arguments")
    if (not path.is_absolute() or ".." in path.parts
            or len(path.parts) < 2
            or (os.name == "nt" and any(":" in part for part in path.parts[1:]))):
        _reject("path_invalid", stage="arguments")

    before = _inspect_path(path, stage="namespace_before")
    leaf = len(before) - 1
    if not 0 <= before[-1][4] <= limit:
        _reject("regular_file_budget", stage="namespace_before",
                component=leaf, field="size", expected=limit,
                observed=before[-1][4])
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    stage = "open"
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            stage = "descriptor_before"
            first = _identity(os.fstat(stream.fileno()))
            _validate(first, leaf=True, stage=stage, component=leaf)
            _compare(before[-1], first, _FIELDS,
                     code="input_changed_before_read", stage="namespace_to_fd",
                     component=leaf, omit_ctime=_WINDOWS_STAT)
            stage = "read"
            raw = stream.read(limit + 1 if prefix is None else prefix)
            stage = "descriptor_after"
            last = _identity(os.fstat(stream.fileno()))
        _validate(last, leaf=True, stage=stage, component=leaf)
        _compare(first, last, _FIELDS, code="input_changed_during_read",
                 stage="descriptor", component=leaf)
    except OSError as exc:
        raise StableReadRejected("input_unavailable", stage=stage,
                                 component=leaf, field="errno",
                                 observed=exc.errno) from None

    after = _inspect_path(path, stage="namespace_after")
    _compare_namespace(before, after)
    expected = first[4] if prefix is None else min(prefix, first[4])
    if len(raw) != expected:
        _reject("read_size", stage="read", component=leaf, field="size",
                expected=expected, observed=len(raw))
    return raw
