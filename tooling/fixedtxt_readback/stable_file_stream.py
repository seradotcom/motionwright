"""Held-descriptor streaming reads with the qualified stable-file policy.

This module preserves leaf and descriptor identity while allowing unrelated
directory-content changes. Root, ACL, digest and read-allocation authority stay
with the caller. It does not provide continuous namespace exclusion.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import sys

import stable_file_read as stable


@dataclass(frozen=True)
class StableInput:
    stream: object
    namespace_identity: tuple
    opened_identity: tuple


@contextmanager
def open_stable(path, limit):
    """Yield one bounded regular input and recheck its identity before closing.

    The caller must bound each read and its aggregate allocations. A partial
    read is permitted. The complete file remains within the initial size limit,
    and no mutation of that file is permitted during the held descriptor.
    """
    if type(limit) is not int or not 0 <= limit < sys.maxsize:
        stable._reject("regular_file_budget", stage="arguments", field="limit")
    try:
        path = Path(path)
    except (TypeError, ValueError):
        stable._reject("path_invalid", stage="arguments")
    if (not path.is_absolute() or ".." in path.parts or len(path.parts) < 2
            or (os.name == "nt" and any(":" in part for part in path.parts[1:]))):
        stable._reject("path_invalid", stage="arguments")
    before = stable._inspect_path(path, stage="namespace_before")
    leaf = len(before) - 1
    if not 0 <= before[-1][4] <= limit:
        stable._reject("regular_file_budget", stage="namespace_before",
                       component=leaf, field="size", expected=limit,
                       observed=before[-1][4])
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise stable.StableReadRejected(
            "input_unavailable", stage="open", component=leaf,
            field="errno", observed=error.errno) from None
    try:
        stream = os.fdopen(descriptor, "rb")
    except BaseException as primary:
        try:
            os.close(descriptor)
        except BaseException as closing:
            primary.add_note("Stable input descriptor close also failed: "
                             + type(closing).__name__)
        raise
    primary = None
    try:
        first = stable._identity(os.fstat(stream.fileno()))
        stable._validate(first, leaf=True, stage="descriptor_before", component=leaf)
        stable._compare(before[-1], first, stable._FIELDS,
                        code="input_changed_before_read", stage="namespace_to_fd",
                        component=leaf, omit_ctime=stable._WINDOWS_STAT)
        try:
            yield StableInput(stream, before[-1], first)
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                last = stable._identity(os.fstat(stream.fileno()))
                stable._validate(last, leaf=True, stage="descriptor_after", component=leaf)
                stable._compare(first, last, stable._FIELDS,
                                code="input_changed_during_read", stage="descriptor",
                                component=leaf)
                after = stable._inspect_path(path, stage="namespace_after")
                stable._compare_namespace(before, after)
            except BaseException as verification:
                if primary is None:
                    raise
                primary.add_note("Stable input exit verification also failed: "
                                 + getattr(verification, "code", type(verification).__name__))
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            stream.close()
        except BaseException as closing:
            if primary is None:
                raise
            primary.add_note("Stable input stream close also failed: "
                             + type(closing).__name__)
