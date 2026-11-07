"""Retain one source-recovery executable from the already built CI target.

This copies public compiled code and its identity only. It does not execute the
binary, read private evidence, include credentials, or authorize Native work.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys

MAX_BINARY = 64 * 1024 * 1024
LOCKS = ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json", "rust-toolchain.toml")


def need(value):
    if not value:
        raise ValueError("source_recovery_tool_custody_rejected")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(root, *args):
    result = subprocess.run(["git", "--no-pager", "--no-replace-objects", "-C", str(root), *args],
                            capture_output=True, timeout=15,
                            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"})
    need(result.returncode == 0 and len(result.stdout) <= 16 * 1024 * 1024)
    return result.stdout


def regular(path, maximum, *, single_link=False, private_mode=True):
    need(path.is_absolute() and path.resolve(strict=True) == path
         and not any(parent.is_symlink() for parent in path.parents))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink > 0
             and (not single_link or before.st_nlink == 1)
             and before.st_uid == os.geteuid()
             and (not private_mode or not before.st_mode & 0o022)
             and 0 < before.st_size <= maximum)
        chunks, total = [], 0
        while True:
            raw = os.read(fd, min(65536, maximum + 1 - total))
            if not raw:
                break
            total += len(raw)
            need(total <= maximum)
            chunks.append(raw)
        after, namespace = os.fstat(fd), path.lstat()
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mode,
                                   value.st_uid, value.st_nlink, value.st_mtime_ns, value.st_ctime_ns)
        need(identity(before) == identity(after) == identity(namespace) and total == before.st_size)
        return b"".join(chunks)
    finally:
        os.close(fd)


def exclusive(path, raw, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            need(stream.write(raw) == len(raw))
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    need(regular(path, max(len(raw), 1), single_link=True) == raw)


def main():
    need(sys.platform == "linux" and platform.machine() == "x86_64"
         and os.environ.get("GITHUB_ACTIONS") == "true"
         and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and os.environ.get("GITHUB_JOB") == "source-contracts"
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
         and os.environ.get("MANIM_REQUESTED_SCENARIO") == "source-contracts")
    root = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    head = git(root, "rev-parse", "HEAD").decode().strip()
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    need(head == os.environ["GITHUB_SHA"] == os.environ["GITHUB_WORKFLOW_SHA"]
         and not git(root, "status", "--porcelain=v2", "--untracked-files=no"))
    # Checkout/Cargo originals can be group-writable. Observe stable bytes and
    # original identity without changing their mode; only new copies are private.
    locks, source_lock = {}, None
    for path in LOCKS:
        raw = regular(root / path, 4 * 1024 * 1024, private_mode=False)
        need(raw == git(root, "show", head + ":" + path))
        locks[path] = sha(raw)
        if path == "SOURCE_LOCK.json":
            source_lock = json.loads(raw)
    binary = regular(root / "target/debug/examples/native-manim-e2e", MAX_BINARY, private_mode=False)
    need(binary.startswith(b"\x7fELF"))
    destination = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True) / "manim-source-recovery-tool"
    destination.mkdir(mode=0o700, exist_ok=False)
    exclusive(destination / "native-manim-e2e", binary, 0o500)
    manifest = {
        "schema": "motionwright.manim.source-recovery-tool/1",
        "source_sha": head, "tree_sha": tree,
        "semwright_sha": source_lock["dependencies"]["semwright"]["revision"],
        "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": "1",
        "platform": "linux-x86_64", "source_locks": locks,
        "binary": {"name": "native-manim-e2e", "bytes": len(binary), "sha256": sha(binary)},
        "purpose": "SOURCE_RECOVERY_ONLY", "selected_command": "source-cycle-recover-edit",
        "native_execution_performed": False, "private_evidence_included": False,
        "serialized_identity_grants_native_authority": False,
    }
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    exclusive(destination / "tool-identity.json", raw, 0o600)
    need(head == git(root, "rev-parse", "HEAD").decode().strip()
         and not git(root, "status", "--porcelain=v2", "--untracked-files=no"))
    print(json.dumps({"source_sha": head, "binary_bytes": len(binary), "binary_sha256": sha(binary),
                      "tool_identity_sha256": sha(raw), "private_evidence_included": False}, sort_keys=True))


if __name__ == "__main__":
    main()
