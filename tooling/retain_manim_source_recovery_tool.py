"""Retain public compiled source-recovery code with bounded original custody.

No private evidence or Native work. The focused lane reuses the original Rust
contracts only when its complete Rust/Cargo/SDK input closure is unchanged.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import subprocess
import sys

# Same existing public source-executable budget as ci_saved_original_mix.py.
# This is not a change to any SDK/Native executable or evidence limit.
MAX_BINARY = 128 * 1024 * 1024
MAX_INPUT = 16 * 1024 * 1024
MAX_CLOSURE = 256 * 1024 * 1024
LOCKS = ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json", "rust-toolchain.toml")
BASELINE = "d5b33f746b2d1bfee496b6261f4ba8bb20c9b872"
REUSED_TESTS = {
    "source_sha": BASELINE, "run_id": "42345728982", "run_attempt": "1",
    "artifact_id": 13563865758,
    "artifact_zip_sha256": "f1122852e17f185185e80372e8909c2a414a61da2d93df3bbcad504a1e001e38",
    "receipt_sha256": "d1060bdf70f2b471ebaca5af70670f28713e6cbac6cf6de53df26dd91e003a44",
    "original_rust_tests": 41, "tests_reexecuted": 0, "native_execution_performed": False,
}
STAGE = "SCOPE"
OBSERVED_BINARY = None


class CustodyRejected(ValueError):
    def __init__(self, code, metadata=None):
        super().__init__(code)
        self.code = code
        self.metadata = metadata


def need(value, code="SOURCE_RECOVERY_TOOL_CUSTODY_REJECTED", metadata=None):
    if not value:
        raise CustodyRejected(code, metadata)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(root, *args):
    result = subprocess.run(["git", "--no-pager", "--no-replace-objects", "-C", str(root), *args],
                            capture_output=True, timeout=20,
                            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"})
    need(result.returncode == 0 and len(result.stdout) <= MAX_INPUT, "GIT_SOURCE_OBSERVATION_REJECTED")
    return result.stdout


def identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mode,
            value.st_uid, value.st_nlink, value.st_mtime_ns, value.st_ctime_ns)


def metadata(value, maximum):
    return {"regular": stat.S_ISREG(value.st_mode), "links": value.st_nlink,
            "owner_matches": value.st_uid == os.geteuid(),
            "mode": oct(stat.S_IMODE(value.st_mode)), "bytes": value.st_size,
            "maximum_bytes": maximum, "stat_mode": value.st_mode,
            "uid": value.st_uid, "device": value.st_dev, "inode": value.st_ino,
            "mtime_ns": value.st_mtime_ns, "ctime_ns": value.st_ctime_ns}


def admit(value, maximum, *, single_link=False, private_mode=True, allow_empty=False):
    observed = metadata(value, maximum)
    need(observed["regular"], "INPUT_NOT_REGULAR", observed)
    need(value.st_nlink > 0 and (not single_link or value.st_nlink == 1), "INPUT_LINK_COUNT_REJECTED", observed)
    need(observed["owner_matches"], "INPUT_OWNER_REJECTED", observed)
    need(not private_mode or not value.st_mode & 0o022, "PRIVATE_COPY_MODE_REJECTED", observed)
    need((0 if allow_empty else 1) <= value.st_size <= maximum, "INPUT_BYTE_BUDGET_REJECTED", observed)


def open_original(path):
    need(path.is_absolute() and path.resolve(strict=True) == path
         and not any(parent.is_symlink() for parent in path.parents), "INPUT_NAMESPACE_REJECTED")
    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)


def regular(path, maximum, *, single_link=False, private_mode=True, allow_empty=False):
    fd = open_original(path)
    try:
        before = os.fstat(fd)
        admit(before, maximum, single_link=single_link, private_mode=private_mode, allow_empty=allow_empty)
        chunks, total = [], 0
        while True:
            raw = os.read(fd, min(65536, maximum + 1 - total))
            if not raw:
                break
            total += len(raw)
            need(total <= maximum, "INPUT_GREW_PAST_BUDGET")
            chunks.append(raw)
        need(identity(before) == identity(os.fstat(fd)) == identity(path.lstat())
             and total == before.st_size, "INPUT_IDENTITY_CHANGED")
        return b"".join(chunks)
    finally:
        os.close(fd)


def exclusive(path, raw, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            need(stream.write(raw) == len(raw), "PRIVATE_COPY_SHORT_WRITE")
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    need(regular(path, max(len(raw), 1), single_link=True) == raw, "PRIVATE_COPY_READBACK_CHANGED")


def fingerprint_fd(fd, maximum):
    digest, total = hashlib.sha256(), 0
    while True:
        raw = os.read(fd, 65536)
        if not raw:
            break
        total += len(raw)
        need(total <= maximum, "PRIVATE_COPY_GREW_PAST_BUDGET")
        digest.update(raw)
    return total, digest.hexdigest()


def retain_binary(source, destination, maximum=MAX_BINARY):
    """Stream one untouched Cargo original; memory stays below two small chunks."""
    global OBSERVED_BINARY
    fd = open_original(source)
    try:
        before = os.fstat(fd)
        OBSERVED_BINARY = metadata(before, maximum)
        # This safe stat is retained even if the original cannot be admitted.
        admit(before, maximum, private_mode=False)
        first = os.read(fd, 4)
        need(first == b"\x7fELF", "COMPILED_BINARY_NOT_ELF")
        output = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o500)
        digest, total = hashlib.sha256(), 0
        try:
            with os.fdopen(output, "wb", closefd=False) as writer:
                raw = first
                while raw:
                    total += len(raw)
                    need(total <= maximum, "COMPILED_BINARY_GREW_PAST_BUDGET")
                    need(writer.write(raw) == len(raw), "COMPILED_BINARY_SHORT_WRITE")
                    digest.update(raw)
                    raw = os.read(fd, 65536)
                writer.flush()
                os.fsync(writer.fileno())
            os.fchmod(output, 0o500)
            copied = os.fstat(output)
            admit(copied, maximum, single_link=True)
        finally:
            os.close(output)
        need(identity(before) == identity(os.fstat(fd)) == identity(source.lstat())
             and total == before.st_size, "COMPILED_BINARY_ORIGINAL_IDENTITY_CHANGED")
        readback = open_original(destination)
        try:
            initial = os.fstat(readback)
            admit(initial, maximum, single_link=True)
            need(identity(copied) == identity(initial), "COMPILED_BINARY_COPY_IDENTITY_CHANGED")
            read_size, read_sha = fingerprint_fd(readback, maximum)
            need(identity(initial) == identity(os.fstat(readback)) == identity(destination.lstat()),
                 "COMPILED_BINARY_READBACK_IDENTITY_CHANGED")
        finally:
            os.close(readback)
        need(read_size == total and read_sha == digest.hexdigest(), "COMPILED_BINARY_READBACK_CHANGED")
        return {"name": destination.name, "bytes": total, "sha256": digest.hexdigest()}
    finally:
        os.close(fd)


def compiler_input(path):
    return (path.startswith(("crates/", "vendor/", "apps/desktop/src-tauri/", ".cargo/",
                             "integrations/", "third_party/semwright-source-evidence/"))
            or path in LOCKS or path in {"Cargo.toml", "rust-toolchain", ".gitmodules"}
            or PurePosixPath(path).name in {"Cargo.toml", "Cargo.lock", "build.rs"}
            or path.endswith(".rs"))


def source_tree(root, revision):
    selected = {}
    raw = git(root, "ls-tree", "-r", "-z", "--full-tree", revision)
    need(raw.endswith(b"\x00"), "COMPILED_INPUT_TREE_TRUNCATED")
    for record in raw[:-1].split(b"\x00"):
        meta, path = record.split(b"\t", 1)
        name = path.decode("utf-8", "strict")
        mode, kind, digest = meta.decode("ascii").split(" ")
        if compiler_input(name):
            need(mode in {"100644", "100755"} and kind == "blob" and len(digest) == 40,
                 "COMPILED_INPUT_TYPE_REJECTED")
            need(name not in selected, "COMPILED_INPUT_DUPLICATED")
            selected[name] = {"mode": mode, "blob": digest}
    need(0 < len(selected) <= 8192, "COMPILED_INPUT_COUNT_REJECTED")
    return selected


def source_reuse(root, head):
    baseline = source_tree(root, BASELINE)
    need(source_tree(root, head) == baseline, "COMPILED_INPUT_CLOSURE_CHANGED_NO_REUSE")
    closure, total = {}, 0
    for name, row in sorted(baseline.items()):
        raw = regular(root / name, MAX_INPUT, private_mode=False, allow_empty=True)
        total += len(raw)
        need(total <= MAX_CLOSURE, "COMPILED_INPUT_CLOSURE_BUDGET_REJECTED")
        git_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
        need(git_blob == row["blob"], "COMPILED_INPUT_PHYSICAL_BYTES_CHANGED")
        closure[name] = {**row, "bytes": len(raw), "sha256": sha(raw)}
    encoded = json.dumps(closure, sort_keys=True, separators=(",", ":")).encode()
    return {**REUSED_TESTS, "compiled_input_files": len(closure), "compiled_input_bytes": total,
            "compiled_input_closure_sha256": sha(encoded), "actual_checkout_sha": head,
            "same_compiled_input_closure": True, "original_job_packaging_failed": True}


def main(*, verify_only=False):
    global STAGE
    scenario = os.environ.get("MANIM_REQUESTED_SCENARIO")
    need(sys.platform == "linux" and platform.machine() == "x86_64"
         and os.environ.get("GITHUB_ACTIONS") == "true"
         and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
         and (scenario, os.environ.get("GITHUB_JOB")) in {
             ("source-contracts", "source-contracts"),
             ("source-recovery-tool-only", "source-recovery-tool")}, "WORKFLOW_SCOPE_REJECTED")
    root = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    head = git(root, "rev-parse", "HEAD").decode().strip()
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    need(head == os.environ["GITHUB_SHA"] == os.environ["GITHUB_WORKFLOW_SHA"]
         and not git(root, "status", "--porcelain=v2", "--untracked-files=no"), "CURRENT_SOURCE_BINDING_REJECTED")
    reuse = None
    if scenario == "source-recovery-tool-only":
        need(os.environ.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
             and os.environ.get("GITHUB_REF") == "refs/heads/fix/a4-manim-recovery-minimal-20261107"
             and os.environ.get("MANIM_EXPECTED_SOURCE_SHA") == head
             and os.environ.get("GITHUB_WORKFLOW_REF") == (
                 "seradotcom/motionwright/.github/workflows/native-manim-e2e.yml@"
                 "refs/heads/fix/a4-manim-recovery-minimal-20261107")
             and os.environ.get("CARGO_INCREMENTAL") == "0"
             and os.environ.get("CARGO_PROFILE_DEV_DEBUG") == "0"
             and os.environ.get("CARGO_PROFILE_TEST_DEBUG") == "0"
             and not os.environ.get("RUSTFLAGS") and not os.environ.get("CARGO_ENCODED_RUSTFLAGS")
             and not os.environ.get("CARGO_BUILD_TARGET")
             and not os.environ.get("CARGO_TARGET_DIR")
             and os.environ.get("MANIM_AFFECTED_RUST_DIAGNOSTICS") == "false"
             and os.environ.get("MW_MANIM_DEVELOPMENT_NATIVE", "false") == "false",
             "FOCUSED_SOURCE_PROFILE_REJECTED")
        STAGE = "REUSED_RUST_INPUT_CLOSURE"
        reuse = source_reuse(root, head)
    if verify_only:
        need(reuse is not None, "REUSE_ONLY_REQUIRES_FOCUSED_LANE")
        return {"status": "UNCHANGED_RUST_SOURCE_EVIDENCE_REFERENCED", "reuse": reuse,
                "build_performed": False, "native_execution_performed": False}
    STAGE = "SOURCE_LOCKS"
    locks, source_lock = {}, None
    for path in LOCKS:
        raw = regular(root / path, 4 * 1024 * 1024, private_mode=False)
        need(raw == git(root, "show", head + ":" + path), "PHYSICAL_LOCK_BYTES_CHANGED")
        locks[path] = sha(raw)
        if path == "SOURCE_LOCK.json":
            source_lock = json.loads(raw)
    destination = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True) / "manim-source-recovery-tool"
    destination.mkdir(mode=0o700, exist_ok=False)
    STAGE = "COMPILED_BINARY_CUSTODY"
    binary = retain_binary(root / "target/debug/examples/native-manim-e2e", destination / "native-manim-e2e")
    manifest = {
        "schema": "motionwright.manim.source-recovery-tool/1",
        "source_sha": head, "tree_sha": tree,
        "semwright_sha": source_lock["dependencies"]["semwright"]["revision"],
        "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": "1",
        "platform": "linux-x86_64", "source_locks": locks,
        "binary": binary, "original_binary_metadata": OBSERVED_BINARY,
        "purpose": "SOURCE_RECOVERY_ONLY", "selected_command": "source-cycle-recover-edit",
        "referenced_rust_source_evidence": reuse,
        "native_execution_performed": False, "private_evidence_included": False,
        "serialized_identity_grants_native_authority": False,
    }
    STAGE = "MANIFEST_CUSTODY"
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    exclusive(destination / "tool-identity.json", raw, 0o600)
    need(head == git(root, "rev-parse", "HEAD").decode().strip()
         and not git(root, "status", "--porcelain=v2", "--untracked-files=no"), "FINAL_SOURCE_BINDING_CHANGED")
    if reuse is not None:
        need(source_reuse(root, head) == reuse, "COMPILED_INPUT_CLOSURE_CHANGED_DURING_RETENTION")
    return {"status": "PUBLIC_SOURCE_TOOL_RETAINED", "source_sha": head, "binary": binary,
            "original_binary_metadata": OBSERVED_BINARY, "tool_identity_sha256": sha(raw),
            "private_evidence_included": False, "native_execution_performed": False}


if __name__ == "__main__":
    verify_only = sys.argv[1:] == ["--verify-reuse-only"]
    ok = False
    try:
        need(not sys.argv[1:] or verify_only, "UNSUPPORTED_PACKAGING_ARGUMENT")
        result = main(verify_only=verify_only)
        ok = True
    except Exception as error:
        result = {"status": "ARTIFACT_CUSTODY_FAILURE", "stage": STAGE,
                  "code": error.code if isinstance(error, CustodyRejected) else "IO_OR_RUNTIME_FAILURE",
                  "metadata": error.metadata if isinstance(error, CustodyRejected) else None,
                  "original_binary_metadata": OBSERVED_BINARY,
                  "native_execution_performed": False, "automatic_retry": False}
    try:
        name = ("manim-source-recovery-reuse-check.json" if verify_only
                else "manim-source-recovery-tool-attempt.json")
        destination = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True) / name
        exclusive(destination, (json.dumps(result, sort_keys=True, indent=2) + "\n").encode(), 0o600)
    except Exception:
        result["diagnostic_retention_failed"] = True
        ok = False
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if ok else 2)
