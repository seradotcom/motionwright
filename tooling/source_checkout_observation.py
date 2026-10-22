"""Observe an exact source checkout without importing packaging or installer code.

Git object bytes and physical checkout bytes are observed independently. Only
exact bytes or an exact LF-to-CRLF checkout are admitted. Callers retain the
observation before and after their work; a digest is not runtime acceptance.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any

SHA40 = re.compile(r"[0-9a-f]{40}\Z")


class ContractError(ValueError):
    pass


def require(value: Any, reason: str) -> None:
    if not value:
        raise ContractError(reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def regular_file(path: Path, maximum: int | None = None) -> dict[str, Any]:
    path = path.absolute()
    for ancestor in (path, *path.parents):
        observed = ancestor.lstat()
        require(not stat.S_ISLNK(observed.st_mode)
                and not getattr(observed, "st_file_attributes", 0) & 0x400,
                "link_or_reparse:" + str(ancestor))
    before = path.stat()
    require(stat.S_ISREG(before.st_mode), "not_regular:" + str(path))
    if maximum is not None:
        require(0 <= before.st_size <= maximum, "file_size_bound:" + str(path))
    sha, observed_bytes = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            observed_bytes += len(block)
            require(observed_bytes <= before.st_size, "file_grew_during_read:" + str(path))
            sha.update(block)
    require(observed_bytes == before.st_size, "file_truncated_during_read:" + str(path))
    after = path.stat()
    require(all(getattr(before, key) == getattr(after, key)
                for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns")),
            "file_changed:" + str(path))
    return {"bytes": before.st_size, "sha256": sha.hexdigest()}


def git(repo: Path, *arguments: str) -> bytes:
    # Match the source isolation used by the conformance and CI readers. A
    # commit label alone does not exclude replacement refs, another worktree,
    # an injected object database, or inherited Git configuration.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0",
               GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                             "-C", str(repo), *arguments], check=True,
                            capture_output=True, timeout=30, env=env)
    return result.stdout


def source_observation(repo: Path, expected: str, paths: tuple[str, ...]) -> dict[str, Any]:
    require(SHA40.fullmatch(expected), "source_sha_not_full")
    actual_root = git(repo, "rev-parse", "--show-toplevel").decode("utf-8", "strict").strip()
    require(Path(actual_root).resolve(strict=True) == repo.resolve(strict=True),
            "source_root_mismatch")
    require(git(repo, "rev-parse", "HEAD").decode().strip() == expected, "source_head_mismatch")
    # Status uses the same deterministic LF/CRLF equivalence admitted below.
    # Do not restore global configuration merely to recognize a Windows
    # checkout; binary/-text attributes and independent source bytes remain
    # authoritative. This read-only query does not rewrite checkout files.
    require(not git(repo, "-c", "core.autocrlf=true", "status",
                    "--porcelain=v1", "--untracked-files=no"), "tracked_source_dirty")
    git(repo, "ls-files", "--error-unmatch", "--", *paths)
    files = {}
    for relative in paths:
        blob = git(repo, "show", expected + ":" + relative)
        path = repo / relative
        identity = regular_file(path)
        checkout = path.read_bytes()
        require(identity == {"bytes": len(checkout), "sha256": digest(checkout)},
                "checkout_changed_during_source_read:" + relative)
        exact = checkout == blob
        crlf_only = b"\r" not in blob and checkout == blob.replace(b"\n", b"\r\n")
        require(exact or crlf_only, "checkout_bytes_not_git_or_exact_crlf:" + relative)
        files[relative] = {"git_bytes": len(blob), "git_sha256": digest(blob), "checkout": identity,
                           "checkout_encoding": "EXACT_GIT_BYTES" if exact else "EXACT_LF_TO_CRLF"}
    return {"sha": expected, "tree": git(repo, "rev-parse", "HEAD^{tree}").decode().strip(),
            "files": files}
