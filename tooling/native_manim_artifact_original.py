"""Read only the authenticated GitHub ZIP original; never duplicate its ciphers on disk.

The whole original ZIP remains privately retained by the existing GH-API
stdout custody. This reader validates and serves its selected ciphertexts
from memory, with exact member identities and a final physical ZIP readback.
It is not a decryptor, a renderer, an acceptance gate, or a release approval.
"""
from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile


def identity(raw: bytes) -> dict:
    if type(raw) is not bytes:
        raise ValueError("manim_archive_original_bytes_required")
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


class OriginalCiphertextArchive:
    """Single retained ZIP, selected members, no extracted duplicate files."""

    MODE = "original-artifact-zip-members/1"

    def __init__(self, original: bytes, expected: dict, selected: set[str],
                 *, reread_original, max_archive: int, max_capsule: int):
        if (type(original) is not bytes or type(expected) is not dict
                or type(selected) is not set or not (2 <= len(selected) <= 8)
                or not callable(reread_original) or type(max_archive) is not int
                or type(max_capsule) is not int or max_archive <= 0 or max_capsule <= 0
                or len(original) > max_archive or identity(original) != expected):
            raise ValueError("manim_archive_original_zip_identity_or_contract")
        if any(not re.fullmatch(
                r"(?:original-reference|reopen-original-reference|edited|reopen-edited|resume-outer)"
                r"\.mwseal|resume-outer\.public\.json", name) for name in selected):
            raise ValueError("manim_archive_original_selected_names")
        self.original_identity = expected
        self.selected = frozenset(selected)
        self.reread_original = reread_original
        self.max_capsule = max_capsule
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(original))
            members = self.archive.infolist()
            if (self.archive.comment or len(members) != len(selected)
                    or len({row.filename for row in members}) != len(members)
                    or {row.filename for row in members} != selected
                    or sum(row.file_size for row in members) > max_archive
                    or any(row.is_dir() or row.flag_bits & 1
                           or stat.S_ISLNK(row.external_attr >> 16)
                           or row.file_size <= 0
                           or row.file_size > (
                               65536 if row.filename.endswith(".json") else max_capsule
                           )
                           for row in members)):
                raise ValueError("manim_archive_original_closed_member_roster")
        except BaseException:
            if hasattr(self, "archive"):
                self.archive.close()
            raise
        self.entries = {row.filename: row for row in members}

    def member(self, name: str, expected: dict | None = None) -> bytes:
        if name not in self.selected:
            raise ValueError("manim_archive_original_member_not_selected")
        raw = self.archive.read(name)
        info = self.entries[name]
        if (len(raw) != info.file_size
                or len(raw) > (65536 if name.endswith(".json") else self.max_capsule)):
            raise ValueError("manim_archive_original_member_length")
        if expected is not None and identity(raw) != expected:
            raise ValueError("manim_archive_original_member_digest")
        return raw

    def finish(self) -> None:
        try:
            if identity(self.reread_original()) != self.original_identity:
                raise ValueError("manim_archive_original_zip_changed")
        finally:
            self.archive.close()

    def abort(self) -> None:
        """Close after an earlier failure without masking its first cause."""
        self.archive.close()
