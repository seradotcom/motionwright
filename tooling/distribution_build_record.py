#!/usr/bin/env python3
"""Observe retained source/Cargo bytes and one distribution payload.

Version 1 records observations made by this tool. It does not prove that the
payload was built from the checkout, admit a runtime, authenticate a reviewer,
or grant distribution rights. No input field is executed, no payload is copied,
and no output file, environment setting, subprocess, or network call is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType

SCHEMA = "motionwright.distribution-build/1"
_REFUSAL = "distribution_build_record_rejected"


class DistributionBuildRecordError(ValueError):
    """A static refusal without input bytes or private paths."""


def _load_sibling(stem: str) -> ModuleType:
    """Load only the fixed trusted siblings below, without bytecode writes."""
    path = Path(__file__).resolve(strict=True).with_name(stem + ".py")
    module = ModuleType("_motionwright_distribution_build_" + stem)
    module.__file__ = str(path)
    module.__package__ = ""
    code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
    exec(code, module.__dict__)
    return module


try:
    admission = _load_sibling("source_lock_admission")
    owned_files = _load_sibling("nsis_owned_files")
except OSError:
    # Preserve a static CLI refusal if an installed sibling is unavailable.
    # Syntax and other programming errors deliberately remain visible.
    admission = None
    owned_files = None


def distribution_build_record(
    root: Path, *, arch: str, payload: Path,
) -> dict:
    """Observe a payload through one retained descriptor and matching sources.

    Windows retains the helper's exclusion of writers and deleters. POSIX
    revalidates the descriptor, path and change time; it does not impose a
    mandatory lock on unrelated writers or bind a later compiler invocation.
    """
    if admission is None or owned_files is None:
        raise DistributionBuildRecordError(_REFUSAL)
    if not isinstance(root, Path) or not isinstance(payload, Path):
        raise DistributionBuildRecordError(_REFUSAL)

    try:
        scope = admission.distribution_scope(arch)
        root = owned_files._validated_path(root)
        payload = owned_files._validated_path(payload)
        owned_files._require_plain_parents(root)
        source = admission.distribution_source_context(root)

        owned_files._require_plain_parents(payload)
        before = payload.lstat()
        owned_files._require_regular(before, "payload")
        size = before.st_size
        if (type(size) is not int
                or not 0 < size <= admission.MAX_DISTRIBUTION_PAYLOAD_BYTES):
            raise DistributionBuildRecordError(_REFUSAL)

        fd = owned_files._open_publication_reader(payload, "payload")
        try:
            owned_files._require_plain_parents(payload)
            change_time = owned_files._source_change_time(fd)
            opened = os.fstat(fd)
            owned_files._require_path_descriptor_match(opened, before, "payload")
            owned_files._require_unchanged(payload.lstat(), before, "payload")

            digest = hashlib.sha256()
            count = 0
            remaining = size + 1
            while remaining:
                block = os.read(fd, min(owned_files.COPY_CHUNK_BYTES, remaining))
                if not block:
                    break
                count += len(block)
                remaining -= len(block)
                if count > size:
                    raise DistributionBuildRecordError(_REFUSAL)
                digest.update(block)
            if count != size:
                raise DistributionBuildRecordError(_REFUSAL)

            # Keep the payload handle alive while checking the source again.
            owned_files._require_plain_parents(root)
            if admission.distribution_source_context(root) != source:
                raise DistributionBuildRecordError(_REFUSAL)

            owned_files._require_plain_parents(payload)
            final_descriptor = os.fstat(fd)
            final_path = payload.lstat()
            # Compare each stat domain to itself; the helper separately joins
            # comparable path/descriptor fields on Windows.
            owned_files._require_unchanged(final_descriptor, opened, "payload")
            owned_files._require_unchanged(final_path, before, "payload")
            owned_files._require_path_descriptor_match(
                final_descriptor, final_path, "payload",
            )
            if owned_files._source_change_time(fd) != change_time:
                raise DistributionBuildRecordError(_REFUSAL)

            return {
                "schema": SCHEMA,
                "source": source,
                "scope": scope,
                "payload": {"bytes": count, "sha256": digest.hexdigest()},
            }
        finally:
            os.close(fd)
    except (admission.SourceAdmissionError, owned_files.CaptureError, OSError):
        raise DistributionBuildRecordError(_REFUSAL) from None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise DistributionBuildRecordError(_REFUSAL)

    def print_help(self, file=None) -> None:
        super().print_help(file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="distribution_build_record.py",
        description="Observe source and payload bytes without granting admission.",
        allow_abbrev=False,
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--arch", required=True, choices=("x64", "arm64", "x86"))
    parser.add_argument("--payload", required=True)
    try:
        args = parser.parse_args(argv)
        record = distribution_build_record(
            Path(args.repo_root), arch=args.arch, payload=Path(args.payload),
        )
        encoded = (
            json.dumps(record, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except (DistributionBuildRecordError, OSError):
        sys.stderr.write(_REFUSAL + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
