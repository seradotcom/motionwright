"""Bounded, read-only Cargo source/license material collection.

No fetch, extraction, source execution, filesystem writes, or permission grant.
Git plumbing is invoked only when collect_cargo_licenses is called. It does not
run status/diff/hash-object filters. This checks a controlled source snapshot;
it is not an atomic defense against a hostile concurrent namespace mutator.
An initial caller-admitted root alias may resolve once; inner links/reparse
points are rejected. Returned paths are relative to the declared root kind.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import stat
import subprocess
import tarfile
import threading
import time
import zlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_COMPONENTS = 4096
MAX_LOCK_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 32 * 1024 * 1024
MAX_NOTICE_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_PACKAGE_FILES = 20000
MAX_TOTAL_FILES = 200000
MAX_TOTAL_READ_BYTES = 2 * 1024 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 20
GIT_REAP_SECONDS = 1
_CHUNK = 65536
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_NOTICE = re.compile(r"(?:LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)(?:[._-].*)?\Z", re.I)
_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{stem}{number}" for stem in ("com", "lpt") for number in range(1, 10)
}
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_CARGO_METADATA = {".cargo-ok", ".cargo-checksum.json"}


class _Problem(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def issue(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


def _fail(code: str, detail: str) -> None:
    raise _Problem(code, detail)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _Budget:
    read: int = 0
    files: int = 0

    def consume(self, count: int) -> None:
        self.read += count
        if self.read > MAX_TOTAL_READ_BYTES:
            _fail("collection_byte_budget_exceeded", "The bounded collection read budget was exceeded.")

    def file(self) -> None:
        self.files += 1
        if self.files > MAX_TOTAL_FILES:
            _fail("collection_file_budget_exceeded", "The bounded collection file budget was exceeded.")


def _parts(value: str, *, empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, str) or len(value) > 4096:
        _fail("unsafe_relative_path", "A source path is not a bounded string.")
    if empty and value == "":
        return ()
    if not value or value.startswith("/") or "\\" in value:
        _fail("unsafe_relative_path", "A source path must be relative and use unambiguous separators.")
    result = value.split("/")
    for part in result:
        if (not part or part in {".", ".."} or len(part) > 255
                or part[-1:] in {".", " "}
                or any(ord(ch) < 32 or ord(ch) == 127 or ch in ':<>*?|"'
                       for ch in part)
                or part.split(".")[0].casefold() in _RESERVED):
            _fail("unsafe_relative_path", "A source path has an unsafe or ambiguous component.")
    if len(result) > 64:
        _fail("source_path_depth_exceeded", "A source path exceeds the bounded depth.")
    return tuple(result)


def _linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    # Reading may legitimately update atime; it is not a content/identity oracle.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass
class _Root:
    original: Path
    path: Path
    kind: str

    @classmethod
    def admit(cls, value: Path, kind: str) -> "_Root":
        value = Path(value)
        if not value.is_absolute():
            _fail("root_not_absolute", "An admitted root must be explicit and absolute.")
        try:
            resolved = value.resolve(strict=True)
            info = resolved.lstat()
        except OSError:
            _fail("root_unavailable", "An admitted source root is unavailable.")
        if _linked(info) or not stat.S_ISDIR(info.st_mode):
            _fail("root_not_directory", "An admitted source root is not a regular directory.")
        return cls(value, resolved, kind)

    def relative(self, path: str | Path) -> tuple[str, ...]:
        candidate = Path(path)
        if not candidate.is_absolute():
            return _parts(candidate.as_posix())
        for prefix in (self.original, self.path):
            try:
                relative = candidate.relative_to(prefix)
            except ValueError:
                continue
            return _parts(relative.as_posix())
        _fail("source_outside_admitted_root", "A source path is outside its admitted root.")

    def checked(self, parts: tuple[str, ...], *, directory: bool = False) -> tuple[Path, os.stat_result]:
        current = self.path
        if not parts:
            info = current.lstat()
            if _linked(info) or not stat.S_ISDIR(info.st_mode):
                _fail("source_root_changed", "The admitted root changed type.")
            return current, info
        _parts("/".join(parts))
        for index, part in enumerate(parts):
            current = current / part
            try:
                info = current.lstat()
            except OSError:
                _fail("source_file_missing", "An expected source path is unavailable.")
            if _linked(info):
                _fail("source_link_rejected", "An inner source symlink or reparse point was rejected.")
            wants_dir = index < len(parts) - 1 or directory
            if wants_dir and not stat.S_ISDIR(info.st_mode):
                _fail("source_parent_not_directory", "An inner source parent is not a directory.")
            if not wants_dir and not stat.S_ISREG(info.st_mode):
                _fail("source_not_regular_file", "An expected source file is not regular.")
        return current, info


def _read(root: _Root, parts: tuple[str, ...], limit: int, budget: _Budget) -> bytes:
    path, before = root.checked(parts)
    if before.st_size > limit:
        _fail("source_file_budget_exceeded", "An expected source file exceeds its byte budget.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail("source_file_open_failed", "An expected source file could not be opened safely.")
    try:
        opened = os.fstat(descriptor)
        if _linked(opened) or not stat.S_ISREG(opened.st_mode) or _stamp(opened) != _stamp(before):
            _fail("source_changed_during_capture", "A source file changed before capture.")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(_CHUNK, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            budget.consume(len(chunk))
            if size > limit:
                _fail("source_file_budget_exceeded", "A source file grew beyond its byte budget.")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        _, named_after = root.checked(parts)
        if (_stamp(opened) != _stamp(after) or _stamp(after) != _stamp(named_after)
                or size != after.st_size):
            _fail("source_changed_during_capture", "Source content or identity changed during capture.")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _walk(root: _Root, base: tuple[str, ...], *, git_metadata: bool = False) -> list[tuple[str, ...]]:
    root.checked(base, directory=True)
    pending = [base]
    files: list[tuple[str, ...]] = []
    seen: set[str] = set()
    while pending:
        relative = pending.pop()
        directory, _ = root.checked(relative, directory=True)
        try:
            with os.scandir(directory) as iterator:
                children = []
                for child in iterator:
                    children.append(child)
                    if len(children) > MAX_PACKAGE_FILES:
                        _fail("source_tree_budget_exceeded", "A source directory has too many entries.")
        except OSError:
            _fail("source_tree_unreadable", "The source tree could not be enumerated.")
        if len(children) > MAX_PACKAGE_FILES:
            _fail("source_tree_budget_exceeded", "A source directory has too many entries.")
        for child in sorted(children, key=lambda item: item.name):
            name = _parts(child.name)
            child_parts = relative + name
            _parts("/".join(child_parts))
            try:
                info = child.stat(follow_symlinks=False)
            except OSError:
                _fail("source_tree_changed", "A source entry disappeared during enumeration.")
            if _linked(info):
                _fail("source_link_rejected", "An inner source symlink or reparse point was rejected.")
            if git_metadata and relative == base and child.name == ".git":
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    _fail("git_metadata_not_regular", "Git administrative metadata is not regular.")
                continue
            folded = "/".join(child_parts).casefold()
            if folded in seen:
                _fail("source_path_collision", "Source paths collide under case-insensitive comparison.")
            seen.add(folded)
            if len(seen) > MAX_PACKAGE_FILES:
                _fail("source_tree_budget_exceeded", "A source tree has too many entries.")
            if stat.S_ISDIR(info.st_mode):
                pending.append(child_parts)
            elif stat.S_ISREG(info.st_mode):
                files.append(child_parts)
            else:
                _fail("source_special_file_rejected", "A source tree contains a special file.")
    return sorted(files)


def _git(cwd: Path, *arguments: str, limit: int = MAX_GIT_OUTPUT_BYTES,
         checkout_config: bool = False) -> bytes:
    # Plumbing only; no status/diff/textconv or source filters.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_LITERAL_PATHSPECS": "1",
    })
    if checkout_config:
        for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
                    "GIT_ATTR_NOSYSTEM"):
            if key in os.environ:
                env[key] = os.environ[key]
    else:
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    command = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
               "-c", "core.untrackedCache=false", *arguments]
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        _fail("git_unavailable", "Read-only Git plumbing is unavailable.")
    output: list[bytes] = []
    errors: list[bytes] = []
    overflow = threading.Event()
    read_error = threading.Event()
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS

    def kill() -> None:
        try:
            process.kill()
        except OSError:
            pass

    def reader(stream: Any, sink: list[bytes], cap: int) -> None:
        size = 0
        try:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > cap:
                    overflow.set()
                    kill()
                    break
                sink.append(chunk)
        except OSError:
            read_error.set()
        finally:
            try:
                stream.close()
            except OSError:
                read_error.set()

    workers = [
        threading.Thread(target=reader, args=(process.stdout, output, limit), daemon=True),
        threading.Thread(target=reader, args=(process.stderr, errors, 65536), daemon=True),
    ]
    for worker in workers:
        worker.start()
    timed_out = False
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
        kill()
        try:
            process.wait(timeout=GIT_REAP_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    finally:
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in workers):
            timed_out = True
            kill()
    # A descendant retaining a pipe cannot extend the caller's wait forever.
    # Do not close a BufferedReader from here: its blocked read can own its lock.
    if timed_out:
        _fail("git_timeout", "Read-only Git plumbing or its output drain exceeded its time budget.")
    if overflow.is_set():
        _fail("git_output_budget_exceeded", "Read-only Git plumbing exceeded its output budget.")
    if read_error.is_set() or process.returncode != 0:
        _fail("git_source_unverified", "Read-only Git plumbing could not verify the source.")
    return b"".join(output)


def _git_blob(data: bytes, oid: str) -> str:
    digest = hashlib.sha1() if len(oid) == 40 else hashlib.sha256()
    digest.update(f"blob {len(data)}\0".encode("ascii"))
    digest.update(data)
    return digest.hexdigest()


@dataclass
class _GitRepo:
    root: _Root
    prefix: tuple[str, ...]
    head: str = field(init=False)
    byte_proofs: dict[tuple[str, ...], dict[str, Any]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        cwd, _ = self.root.checked(self.prefix, directory=True)
        raw_top = _git(cwd, "rev-parse", "--show-toplevel", limit=8192)
        try:
            top = Path(raw_top.decode("utf-8").strip())
        except UnicodeError:
            _fail("git_root_encoding_invalid", "Git returned an unsupported root encoding.")
        if os.path.normcase(os.path.abspath(top)) != os.path.normcase(os.path.abspath(cwd)):
            _fail("git_root_mismatch", "The expected source checkout is not the Git worktree root.")
        self.head = self.current_head()

    @property
    def cwd(self) -> Path:
        return self.root.path.joinpath(*self.prefix)

    def current_head(self) -> str:
        raw = _git(self.cwd, "rev-parse", "--verify", "HEAD^{commit}", limit=256)
        try:
            value = raw.decode("ascii").strip()
        except UnicodeError:
            _fail("git_head_invalid", "Git HEAD is not a supported exact object identifier.")
        if not _GIT_OID.fullmatch(value):
            _fail("git_head_invalid", "Git HEAD is not a supported exact object identifier.")
        return value

    def entries(self, scope: tuple[str, ...]) -> dict[tuple[str, ...], tuple[str, str]]:
        arguments = ["ls-tree", "-r", "-z", "--full-tree", self.head, "--"]
        if scope:
            arguments.append("/".join(scope))
        raw = _git(self.cwd, *arguments)
        result: dict[tuple[str, ...], tuple[str, str]] = {}
        folded: set[str] = set()
        for record in raw.split(b"\0"):
            if not record:
                continue
            try:
                header, name = record.split(b"\t", 1)
                mode, kind, oid = header.decode("ascii").split(" ")
                relative = _parts(name.decode("utf-8"))
            except (UnicodeError, ValueError):
                _fail("git_tree_malformed", "The Git tree could not be interpreted unambiguously.")
            if relative[:len(scope)] != scope:
                _fail("git_tree_scope_mismatch", "Git returned a path outside the requested source scope.")
            if mode not in {"100644", "100755"} or kind != "blob" or not _GIT_OID.fullmatch(oid):
                _fail("git_nonregular_source_unverified", "A Git source contains a link, submodule, or unsupported object.")
            path_key = "/".join(relative).casefold()
            if relative in result or path_key in folded:
                _fail("git_tree_path_collision", "The Git source has ambiguous duplicate paths.")
            folded.add(path_key)
            result[relative] = (mode, oid)
            if len(result) > MAX_PACKAGE_FILES:
                _fail("git_tree_budget_exceeded", "The Git source tree exceeds the entry budget.")
        return result

    def check_index(self, scope: tuple[str, ...],
                    expected: dict[tuple[str, ...], tuple[str, str]]) -> None:
        arguments = ["ls-files", "--stage", "-z", "--"]
        if scope:
            arguments.append("/".join(scope))
        raw = _git(self.cwd, *arguments)
        actual: dict[tuple[str, ...], tuple[str, str]] = {}
        for record in raw.split(b"\0"):
            if not record:
                continue
            try:
                header, name = record.split(b"\t", 1)
                mode, oid, stage = header.decode("ascii").split(" ")
                relative = _parts(name.decode("utf-8"))
            except (UnicodeError, ValueError):
                _fail("git_index_malformed", "The Git index could not be interpreted unambiguously.")
            if stage != "0" or relative in actual:
                _fail("git_index_not_clean", "The source index has unresolved or duplicate stages.")
            actual[relative] = (mode, oid)
        if actual != expected:
            _fail("git_index_not_clean", "The source index differs from the exact HEAD tree.")

    def finish(self) -> None:
        if self.current_head() != self.head:
            _fail("git_head_changed", "Git HEAD changed during source collection.")

    def verify_bytes(self, relative: tuple[str, ...], data: bytes, oid: str) -> dict[str, Any]:
        proof: dict[str, Any] = {"git_blob": oid, "git_line_endings": "raw"}
        if _git_blob(data, oid) == oid:
            self.byte_proofs[relative] = proof
            return proof
        canonical = data.replace(b"\r\n", b"\n")
        if (b"\r\n" not in data or b"\0" in data or b"\r" in canonical
                or canonical.replace(b"\n", b"\r\n") != data
                or _git_blob(canonical, oid) != oid):
            _fail("git_worktree_bytes_mismatch", "Source bytes are neither the HEAD blob nor its exact LF-to-CRLF expansion.")
        # Arbitrary environment-injected Git config is outside this narrow
        # checkout-conversion protocol. Never impose autocrlf=true ourselves.
        if (os.environ.get("GIT_CONFIG_PARAMETERS")
                or os.environ.get("GIT_CONFIG_COUNT", "0") not in {"", "0"}):
            _fail("git_crlf_context_unverified", "Environment-injected Git configuration is not authenticated.")
        enabled = _git(self.cwd, "config", "--bool", "--default=false", "--get",
                       "core.autocrlf", limit=32, checkout_config=True).strip()
        if enabled != b"true":
            _fail("git_crlf_policy_unverified", "The actual checkout configuration does not enable autocrlf.")
        path = "/".join(relative)
        names = ("text", "eol", "filter", "ident", "working-tree-encoding", "crlf")
        def attributes(cached: bool) -> dict[str, str]:
            args = ["check-attr", "-z"]
            if cached:
                args.append("--cached")
            raw = _git(self.cwd, *args, *names, "--", path, limit=65536,
                       checkout_config=True)
            fields = raw.split(b"\0")
            if fields[-1:] != [b""] or len(fields) != 3 * len(names) + 1:
                _fail("git_crlf_attributes_unverified", "Git attributes are not an exact bounded result.")
            observed: dict[str, str] = {}
            try:
                for index in range(0, len(fields) - 1, 3):
                    found, name, value = (part.decode("utf-8") for part in fields[index:index + 3])
                    if found != path or name not in names or name in observed:
                        _fail("git_crlf_attributes_unverified", "Git attributes returned an unexpected path or key.")
                    observed[name] = value
            except UnicodeError:
                _fail("git_crlf_attributes_unverified", "Git attribute encoding is unsupported.")
            return observed
        actual = attributes(False)
        if (actual != attributes(True) or actual["text"] not in {"unspecified", "set", "auto"}
                or actual["eol"] not in {"unspecified", "crlf"}
                or actual["crlf"] != "unspecified"
                or any(actual[name] not in {"unspecified", "unset"}
                       for name in ("filter", "ident", "working-tree-encoding"))):
            _fail("git_crlf_policy_unverified", "Tracked and effective attributes do not permit the narrow LF-to-CRLF conversion.")
        raw = _git(self.cwd, "ls-files", "--eol", "-z", "--", path,
                   limit=8192, checkout_config=True)
        try:
            header, found = raw[:-1].split(b"\t", 1)
        except ValueError:
            _fail("git_crlf_eol_unverified", "Git did not return one exact EOL record.")
        if (not raw.endswith(b"\0") or b"\0" in raw[:-1]
                or found != path.encode("utf-8") or header.split()[:2] != [b"i/lf", b"w/crlf"]):
            _fail("git_crlf_eol_unverified", "Git does not classify the index as LF and the worktree as CRLF.")
        proof.update({"git_line_endings": "lf_blob_to_crlf_worktree",
                      "core_autocrlf": True, "git_attributes": actual})
        self.byte_proofs[relative] = proof
        return proof

    def read_tracked(self, relative: tuple[str, ...], limit: int, budget: _Budget) -> bytes:
        expected = self.entries(relative)
        if set(expected) != {relative}:
            _fail("git_required_file_untracked", "A required source file is not a tracked HEAD blob.")
        self.check_index(relative, expected)
        data = _read(self.root, self.prefix + relative, limit, budget)
        self.verify_bytes(relative, data, expected[relative][1])
        self.finish()
        return data


@dataclass
class _Capture:
    root: _Root
    package: tuple[str, ...]
    files: dict[tuple[str, ...], dict[str, Any]]
    authentication: dict[str, Any]
    budget: _Budget
    repo: _GitRepo | None = None
    repo_package: tuple[str, ...] = ()

    def read(self, relative: tuple[str, ...], limit: int) -> bytes:
        expected = self.files.get(relative)
        if expected is None:
            if self.repo is None or relative[:len(self.repo.prefix)] != self.repo.prefix:
                _fail("license_file_not_authenticated", "A requested file is outside the authenticated source tree.")
            inside = relative[len(self.repo.prefix):]
            data = self.repo.read_tracked(inside, limit, self.budget)
            expected = {"sha256": _sha(data), "bytes": len(data), **self.repo.byte_proofs[inside]}
            self.files[relative] = expected
            return data
        data = _read(self.root, relative, limit, self.budget)
        if _sha(data) != expected["sha256"] or len(data) != expected["bytes"]:
            _fail("authenticated_source_changed", "An authenticated source file changed before material collection.")
        return data


def _decompress(crate: bytes, budget: _Budget) -> bytes:
    if not crate.startswith(b"\x1f\x8b"):
        _fail("crate_archive_format_invalid", "The original crate is not a gzip archive.")
    chunks: list[bytes] = []
    size = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(crate), mode="rb") as stream:
            while True:
                chunk = stream.read(min(_CHUNK, MAX_UNPACKED_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                budget.consume(len(chunk))
                if size > MAX_UNPACKED_BYTES:
                    _fail("crate_unpacked_budget_exceeded", "The original crate exceeds the unpacked byte budget.")
                chunks.append(chunk)
    except (OSError, EOFError, zlib.error):
        _fail("crate_archive_malformed", "The original crate gzip stream is malformed.")
    return b"".join(chunks)


def _registry_capture(component: dict[str, Any], cargo: _Root,
                      lock_entry: dict[str, Any], budget: _Budget) -> _Capture:
    manifest = cargo.relative(component["manifest_path"])
    prefix = f'{component["name"]}-{component["version"]}'
    if (len(manifest) != 5 or manifest[:2] != ("registry", "src")
            or manifest[3:] != (prefix, "Cargo.toml")):
        _fail("registry_manifest_layout_unverified", "The manifest is not in the expected Cargo registry source layout.")
    package = manifest[:-1]
    checksum = lock_entry.get("checksum")
    if not isinstance(checksum, str) or not _HEX64.fullmatch(checksum):
        _fail("registry_lock_checksum_missing", "The exact Cargo.lock package lacks a valid SHA-256 checksum.")
    archive_path = ("registry", "cache", manifest[2], prefix + ".crate")
    crate = _read(cargo, archive_path, MAX_ARCHIVE_BYTES, budget)
    if _sha(crate) != checksum:
        _fail("registry_archive_checksum_mismatch", "The original crate does not match the exact Cargo.lock checksum.")
    raw = _decompress(crate, budget)
    expected: dict[tuple[str, ...], dict[str, Any]] = {}
    member_names: set[tuple[str, ...]] = set()
    folded: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for info in archive:
                name = info.name.rstrip("/") if info.isdir() else info.name
                parts = _parts(name)
                if parts[0] != prefix:
                    _fail("crate_member_root_mismatch", "A crate member is outside its package root.")
                if parts in member_names or "/".join(parts).casefold() in folded:
                    _fail("crate_member_duplicate", "A crate archive contains duplicate or colliding member paths.")
                member_names.add(parts)
                folded.add("/".join(parts).casefold())
                if len(member_names) > MAX_PACKAGE_FILES:
                    _fail("crate_member_budget_exceeded", "The crate archive exceeds its member budget.")
                if info.isdir():
                    continue
                if (not info.isfile() or info.issym() or info.islnk()
                        or info.sparse is not None or len(parts) < 2):
                    _fail("crate_member_type_rejected", "A crate member is linked, sparse, special, or outside a regular file scope.")
                if info.size < 0 or info.size > MAX_SOURCE_FILE_BYTES:
                    _fail("crate_member_byte_budget_exceeded", "A crate member exceeds its file byte budget.")
                stream = archive.extractfile(info)
                if stream is None:
                    _fail("crate_member_unreadable", "An original crate member could not be read.")
                with stream:
                    data = stream.read(MAX_SOURCE_FILE_BYTES + 1)
                if len(data) != info.size or len(data) > MAX_SOURCE_FILE_BYTES:
                    _fail("crate_member_size_mismatch", "An original crate member has an inconsistent size.")
                relative = package + parts[1:]
                expected[relative] = {"sha256": _sha(data), "bytes": len(data)}
            if any(raw[archive.offset:]):
                _fail("crate_trailing_payload_rejected", "The crate contains nonzero payload after the tar member sequence.")
    except (tarfile.TarError, OSError, EOFError):
        _fail("crate_archive_malformed", "The original crate tar stream is malformed.")
    if manifest not in expected:
        _fail("crate_manifest_missing", "The original crate does not contain its manifest.")
    actual_paths = _walk(cargo, package)
    excluded: list[str] = []
    actual: set[tuple[str, ...]] = set()
    for relative in actual_paths:
        if (relative not in expected and len(relative) == len(package) + 1
                and relative[-1] in _CARGO_METADATA):
            _read(cargo, relative, MAX_MANIFEST_BYTES, budget)
            excluded.append(relative[-1])
            continue
        actual.add(relative)
    if actual != set(expected):
        _fail("registry_source_members_mismatch", "The local registry source has missing or additional package files.")
    for relative, descriptor in expected.items():
        budget.file()
        data = _read(cargo, relative, MAX_SOURCE_FILE_BYTES, budget)
        if _sha(data) != descriptor["sha256"] or len(data) != descriptor["bytes"]:
            _fail("registry_source_bytes_mismatch", "A local source, manifest, or notice differs from the original crate.")
    return _Capture(cargo, package, expected, {
        "status": "VERIFIED", "kind": "registry_archive",
        "root_kind": "cargo_home", "source_root": "/".join(package),
        "archive_path": "/".join(archive_path), "archive_sha256": checksum,
        "scope": "all_regular_package_files_against_original_crate",
        "excluded_untrusted_cargo_metadata": sorted(excluded),
    }, budget)


def _git_capture(component: dict[str, Any], root: _Root, repo: _GitRepo,
                 package: tuple[str, ...], budget: _Budget, kind: str) -> _Capture:
    if package[:len(repo.prefix)] != repo.prefix:
        _fail("git_package_scope_mismatch", "The package is outside its exact Git checkout.")
    inside = package[len(repo.prefix):]
    expected = repo.entries(inside)
    repo.check_index(inside, expected)
    local = set(_walk(root, package, git_metadata=not inside))
    expected_local = {repo.prefix + relative for relative in expected}
    if local != expected_local:
        _fail("git_worktree_members_mismatch", "The source tree has missing, untracked, or ignored additional files.")
    files: dict[tuple[str, ...], dict[str, Any]] = {}
    for relative, (mode, oid) in expected.items():
        budget.file()
        local_relative = repo.prefix + relative
        data = _read(root, local_relative, MAX_SOURCE_FILE_BYTES, budget)
        proof = repo.verify_bytes(relative, data, oid)
        files[local_relative] = {"sha256": _sha(data), "bytes": len(data),
                                 **proof, "git_mode": mode}
    repo.check_index(inside, expected)
    repo.finish()
    return _Capture(root, package, files, {
        "status": "VERIFIED", "kind": kind, "root_kind": root.kind,
        "source_root": "/".join(package), "git_head": repo.head,
        "scope": "tracked_package_tree_and_index_at_exact_HEAD_with_verified_line_endings",
    }, budget, repo, inside)


def _toml(data: bytes, code: str) -> dict[str, Any]:
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError):
        _fail(code, "A required TOML document is malformed.")
    if not isinstance(parsed, dict):
        _fail(code, "A required TOML document is not a table.")
    return parsed


def _lock(lock_bytes: bytes) -> dict[tuple[str, str, str | None], dict[str, Any]]:
    if not isinstance(lock_bytes, bytes) or len(lock_bytes) > MAX_LOCK_BYTES:
        _fail("lock_byte_budget_exceeded", "Cargo.lock must be bounded captured bytes.")
    document = _toml(lock_bytes, "cargo_lock_malformed")
    packages = document.get("package")
    if not isinstance(packages, list) or len(packages) > MAX_COMPONENTS:
        _fail("cargo_lock_packages_invalid", "Cargo.lock does not contain a bounded package list.")
    result: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for package in packages:
        if (not isinstance(package, dict) or not isinstance(package.get("name"), str)
                or not isinstance(package.get("version"), str)
                or package.get("source") is not None and not isinstance(package.get("source"), str)):
            _fail("cargo_lock_package_invalid", "Cargo.lock contains an invalid package identity.")
        key = (package["name"], package["version"], package.get("source"))
        if key in result:
            _fail("cargo_lock_package_duplicate", "Cargo.lock contains a duplicate exact package identity.")
        result[key] = package
    return result


def _expression(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        _fail("license_expression_malformed", "The license expression is absent, empty, or oversized.")
    tokens = re.findall(r"\(|\)|[A-Za-z0-9][A-Za-z0-9.+:-]*", value)
    if re.sub(r"\s+", "", value) != "".join(tokens):
        _fail("license_expression_malformed", "The license expression has unsupported syntax.")
    position = 0

    def term() -> None:
        nonlocal position
        if position >= len(tokens):
            _fail("license_expression_malformed", "The license expression is incomplete.")
        if tokens[position] == "(":
            position += 1
            disjunction()
            if position >= len(tokens) or tokens[position] != ")":
                _fail("license_expression_malformed", "The license expression has unbalanced parentheses.")
            position += 1
            return
        if tokens[position] in {"AND", "OR", "WITH", ")"}:
            _fail("license_expression_malformed", "The license expression has an invalid operand.")
        position += 1
        if position < len(tokens) and tokens[position] == "WITH":
            position += 1
            if position >= len(tokens) or tokens[position] in {"AND", "OR", "WITH", "(", ")"}:
                _fail("license_expression_malformed", "The license exception is missing.")
            position += 1

    def conjunction() -> None:
        nonlocal position
        term()
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            term()

    def disjunction() -> None:
        nonlocal position
        conjunction()
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            conjunction()

    if len(tokens) > 512 or value.count("(") > 32:
        _fail("license_expression_budget_exceeded", "The license expression exceeds the parser budget.")
    disjunction()
    if position != len(tokens):
        _fail("license_expression_malformed", "The license expression has trailing or misplaced terms.")
    # Preserve the declaration exactly; syntax checking is not an SPDX catalog
    # lookup, a license choice, or a legal coverage assertion.
    return value


def _join_license(base: tuple[str, ...], value: str, *, allow_parent: bool) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or len(value) > 4096:
        _fail("license_file_path_invalid", "The explicit license-file path is not bounded.")
    if value.startswith("/") or "\\" in value or ":" in value:
        _fail("license_file_path_invalid", "The manifest license-file path is not relative.")
    result = list(base)
    for part in value.split("/"):
        if part == "..":
            if not allow_parent or not result:
                _fail("license_file_outside_source", "The license-file path escapes its authenticated source.")
            result.pop()
        elif part == ".":
            if not allow_parent:
                _fail("license_file_path_invalid", "An archive license-file path must be canonical.")
        else:
            result.extend(_parts(part))
    if not result:
        _fail("license_file_path_invalid", "The license-file path does not name a file.")
    return tuple(result)


def _component_manifest(component: dict[str, Any], capture: _Capture,
                        workspace: _Root, workspace_repo: _GitRepo) -> tuple[str | None, tuple[str, ...] | None]:
    manifest = _toml(capture.read(capture.package + ("Cargo.toml",), MAX_MANIFEST_BYTES),
                     "package_manifest_malformed")
    package = manifest.get("package")
    if not isinstance(package, dict) or package.get("name") != component["name"]:
        _fail("component_manifest_identity_mismatch", "The classifier name does not match the authenticated manifest.")
    inherited: dict[str, Any] | None = None

    def workspace_package() -> dict[str, Any]:
        nonlocal inherited
        if inherited is not None:
            return inherited
        if capture.root is not workspace or capture.repo is not workspace_repo:
            _fail("manifest_inheritance_unverified", "Workspace inheritance outside the admitted workspace is unverified.")
        explicit_workspace = package.get("workspace")
        if explicit_workspace is not None:
            if not isinstance(explicit_workspace, str):
                _fail("manifest_inheritance_malformed", "The package workspace reference is malformed.")
            # Normalize a directory reference without resolving an inner link.
            referenced = _join_license(capture.package, explicit_workspace.rstrip("/") + "/Cargo.toml",
                                       allow_parent=True)
            if referenced != ("Cargo.toml",):
                _fail("manifest_inheritance_unverified", "The package selects a different workspace root.")
        else:
            # Never substitute the caller's outer workspace for a nearer one.
            for depth in range(len(capture.package) - 1, 0, -1):
                candidate = capture.package[:depth] + ("Cargo.toml",)
                try:
                    workspace.checked(candidate)
                except _Problem as problem:
                    if problem.code == "source_file_missing":
                        continue
                    raise
                closer = _toml(capture.read(candidate, MAX_MANIFEST_BYTES), "workspace_manifest_malformed")
                if isinstance(closer.get("workspace"), dict):
                    _fail("nested_workspace_inheritance_unverified", "A nearer workspace requires its own inheritance protocol.")
        document = _toml(workspace_repo.read_tracked(("Cargo.toml",), MAX_MANIFEST_BYTES,
                                                    capture.budget),
                         "workspace_manifest_malformed")
        workspace_table = document.get("workspace")
        inherited = workspace_table.get("package") if isinstance(workspace_table, dict) else None
        if not isinstance(inherited, dict):
            _fail("manifest_inheritance_unverified", "The authenticated workspace package table is missing.")
        capture.read(("Cargo.toml",), MAX_MANIFEST_BYTES)
        return inherited

    def field_value(name: str) -> tuple[Any, bool]:
        value = package.get(name)
        if isinstance(value, dict):
            if value != {"workspace": True}:
                _fail("manifest_inheritance_malformed", "A package field has unsupported inheritance syntax.")
            table = workspace_package()
            if name not in table:
                _fail("manifest_inheritance_unverified", "An inherited package field is missing in the workspace.")
            return table[name], True
        return value, False

    version, _ = field_value("version")
    if version != component["version"]:
        _fail("component_manifest_identity_mismatch", "The classifier version does not match the authenticated manifest.")
    declared_license, _ = field_value("license")
    expression = _expression(declared_license)
    if component.get("license") != expression:
        _fail("component_license_metadata_mismatch", "The classifier license differs from the authenticated declaration.")
    license_file, inherited_file = field_value("license-file")
    explicit: tuple[str, ...] | None = None
    if license_file is not None:
        base = () if inherited_file else capture.package
        # Only authenticated workspace inheritance permits a member metadata
        # path containing parents. Registry declarations never permit traversal.
        explicit = _join_license(base, license_file, allow_parent=inherited_file)
        metadata = component.get("license_file")
        if not isinstance(metadata, (str, Path)):
            _fail("component_license_file_metadata_mismatch", "The explicit license-file is absent from classifier metadata.")
        metadata_path = Path(metadata)
        if metadata_path.is_absolute():
            observed = capture.root.relative(metadata_path)
        else:
            # Cargo metadata uses native separators; manifest declarations stay strict.
            observed = _join_license(capture.package, str(metadata).replace(os.sep, "/"),
                                     allow_parent=inherited_file)
        if observed != explicit:
            _fail("component_license_file_metadata_mismatch", "The classifier license-file does not resolve to the authenticated declaration.")
    elif component.get("license_file") is not None:
        _fail("component_license_file_metadata_mismatch", "Classifier metadata introduces an undeclared license-file.")
    if expression is None and explicit is None:
        _fail("license_declaration_missing", "The authenticated manifest declares neither license nor license-file.")
    return expression, explicit


def _is_notice(relative: tuple[str, ...], package: tuple[str, ...]) -> bool:
    if relative[:len(package)] != package:
        return False
    local = relative[len(package):]
    return bool(local and (_NOTICE.fullmatch(local[-1])
                or any(part.casefold() in {"licenses", "licences"} for part in local[:-1])))


def _collect_one(component: dict[str, Any], workspace: _Root, cargo: _Root,
                 workspace_repo: _GitRepo, lock: dict[tuple[str, str, str | None], dict[str, Any]],
                 budget: _Budget) -> tuple[dict[str, Any], dict[str, bytes]]:
    result = {
        "id": component.get("id") if isinstance(component.get("id"), str) else None,
        "name": component.get("name") if isinstance(component.get("name"), str) else None,
        "version": component.get("version") if isinstance(component.get("version"), str) else None,
        "source": component.get("source") if isinstance(component.get("source"), str) else None,
        "roles": [role for role in component.get("roles", []) if isinstance(role, str) and len(role) <= 128][:32]
                 if isinstance(component.get("roles", []), list) else [],
        "license_expression": component.get("license") if isinstance(component.get("license"), str) else None,
        "source_authentication": {"status": "NOT_VERIFIED"},
        "notices": [], "issues": [],
    }
    texts: dict[str, bytes] = {}
    try:
        if (not isinstance(component.get("id"), str) or not component["id"]
                or len(component["id"]) > 8192 or not isinstance(component.get("name"), str)
                or not _NAME.fullmatch(component["name"])
                or not isinstance(component.get("version"), str)
                or not component["version"] or len(component["version"]) > 128
                or not isinstance(component.get("manifest_path"), (str, Path))
                or not isinstance(component.get("roles"), list) or not component["roles"]
                or any(not isinstance(role, str) or not role or len(role) > 128
                       for role in component["roles"])
                or len(component["roles"]) > 32):
            _fail("component_metadata_malformed", "Classifier metadata lacks a bounded identity, manifest, or role set.")
        source = component.get("source")
        if source is not None and (not isinstance(source, str) or len(source) > 8192):
            _fail("component_source_malformed", "The classifier source identifier is invalid.")
        entry = lock.get((component["name"], component["version"], source))
        if entry is None:
            _fail("component_not_in_exact_lock", "The exact name/version/source identity is absent from Cargo.lock.")
        if source is not None and (source.startswith("registry+") or source.startswith("sparse+")):
            capture = _registry_capture(component, cargo, entry, budget)
        elif source is not None and source.startswith("git+"):
            revision = source.rsplit("#", 1)[-1]
            if not _GIT_OID.fullmatch(revision):
                _fail("git_source_revision_unpinned", "The Git source lacks a full exact revision.")
            manifest = cargo.relative(component["manifest_path"])
            if len(manifest) < 5 or manifest[:2] != ("git", "checkouts") or manifest[-1] != "Cargo.toml":
                _fail("git_manifest_layout_unverified", "The Git source is outside an admitted Cargo checkout layout.")
            repo = _GitRepo(cargo, manifest[:4])
            if repo.head != revision:
                _fail("git_source_revision_mismatch", "The checkout HEAD differs from the exact locked Git revision.")
            # Check the entire dependency checkout, not just one nested member.
            capture = _git_capture(component, cargo, repo, repo.prefix, budget, "git_exact_revision")
            capture.package = manifest[:-1]
            capture.repo_package = capture.package[len(repo.prefix):]
            capture.authentication["package_root"] = "/".join(capture.package)
        elif source is None:
            manifest = workspace.relative(component["manifest_path"])
            if manifest[-1:] != ("Cargo.toml",):
                _fail("workspace_manifest_layout_unverified", "The workspace source does not name Cargo.toml.")
            package = manifest[:-1]
            if component["name"] == "glib" and not any(part.casefold() in {"vendor", "vendored"} for part in package):
                _fail("vendored_glib_wrapper_path_unverified", "A source=None glib wrapper requires an explicit tracked vendor path.")
            capture = _git_capture(component, workspace, workspace_repo, package, budget, "workspace_HEAD")
            if component["name"] == "glib":
                capture.authentication["kind"] = "vendored_rust_glib_wrapper"
                capture.authentication["scope"] = "tracked_rust_wrapper_only_not_native_GLib_C"
        else:
            _fail("component_source_kind_unverified", "This Cargo source kind has no implemented authenticity protocol.")
        expression, explicit = _component_manifest(component, capture, workspace, workspace_repo)
        result["license_expression"] = expression
        paths = {path for path in capture.files if _is_notice(path, capture.package)}
        if explicit is not None:
            paths.add(explicit)
        if not paths:
            _fail("license_text_missing", "No original bounded license/notice text is available; no text was synthesized.")
        notices: list[dict[str, Any]] = []
        for path in sorted(paths):
            data = capture.read(path, MAX_NOTICE_BYTES)
            try:
                decoded = data.decode("utf-8-sig")
            except UnicodeError:
                _fail("license_text_encoding_unverified", "An original notice is not supported UTF-8 text.")
            if not decoded.strip() or "\0" in decoded:
                _fail("license_text_malformed", "An original notice is empty or contains NUL characters.")
            digest = _sha(data)
            if digest not in texts and sum(map(len, texts.values())) + len(data) > MAX_TEXT_BYTES:
                _fail("notice_collection_budget_exceeded", "Original notice texts exceed the bounded collection budget.")
            texts[digest] = data
            notices.append({"source_path": "/".join(path), "sha256": digest, "bytes": len(data)})
        capture.authentication["files"] = [
            {"source_path": "/".join(path), **descriptor}
            for path, descriptor in sorted(capture.files.items())
        ]
        capture.authentication["license_expression_check"] = "GRAMMAR_ONLY_NO_ALTERNATIVE_SELECTED"
        result["source_authentication"] = capture.authentication
        result["notices"] = notices
    except _Problem as problem:
        result["issues"].append(problem.issue())
        texts = {}
        result["notices"] = []
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        result["issues"].append({
            "code": "source_collection_failed_closed",
            "detail": "Malformed or changing source data prevented bounded source authentication.",
        })
        texts = {}
        result["notices"] = []
    return result, texts


def collect_cargo_licenses(components: Any, *, workspace_root: Path,
                           cargo_home: Path, lock_bytes: bytes) -> dict[str, Any]:
    """Collect source-authenticated original license material; never legal consent.

    The caller supplies observed components/roles from the build classifier.
    This collector does not turn a dependency lock into a shipping inventory.
    Git bytes match tracked blobs exactly, or their narrowly verified LF-to-CRLF
    checkout expansion. Filters, mixed endings and other transforms remain rejected.
    """
    result: dict[str, Any] = {
        "schema": "motionwright.cargo-license-sources/1",
        "components": [], "texts": {}, "issues": [], "native_content_reviews": [],
        "full_package_inventory": "NOT_VERIFIED",
    }
    budget = _Budget()
    try:
        if not isinstance(components, (list, tuple)) or not components or len(components) > MAX_COMPONENTS:
            _fail("component_list_budget_exceeded", "Components must be a bounded concrete list.")
        lock = _lock(lock_bytes)
        workspace = _Root.admit(workspace_root, "workspace")
        cargo = _Root.admit(cargo_home, "cargo_home")
        workspace_repo = _GitRepo(workspace, ())
        actual_lock = workspace_repo.read_tracked(("Cargo.lock",), MAX_LOCK_BYTES, budget)
        if actual_lock != lock_bytes:
            _fail("lock_input_not_workspace_HEAD", "The captured lock bytes differ from the tracked workspace HEAD lock.")
        seen: set[str] = set()
        total_text_bytes = 0
        for component in components:
            if not isinstance(component, dict):
                result["issues"].append({"code": "component_metadata_malformed",
                                         "detail": "A classifier component is not an object."})
                continue
            identity = component.get("id")
            if isinstance(identity, str) and identity in seen:
                result["issues"].append({"code": "component_identity_duplicate",
                                         "detail": "The classifier repeats a component identity."})
                continue
            if isinstance(identity, str):
                seen.add(identity)
            record, texts = _collect_one(component, workspace, cargo, workspace_repo, lock, budget)
            result["components"].append(record)
            if isinstance(component.get("name"), str) and component["name"] in {
                    "libsqlite3-sys", "glib", "glib-sys", "gobject-sys", "gio-sys"}:
                result["native_content_reviews"].append({
                    "component_id": identity, "code": "native_content_review_required",
                    "detail": ("Rust wrapper material does not establish the included native C library, "
                               "static/dynamic inclusion, native notices, or distribution permission."),
                })
            for issue in record["issues"]:
                result["issues"].append({"component_id": identity, **issue})
            additional = sum(len(data) for digest, data in texts.items() if digest not in result["texts"])
            if total_text_bytes + additional > MAX_TEXT_BYTES:
                issue = {"code": "notice_collection_budget_exceeded",
                         "detail": "Original notice texts exceed the bounded collection budget."}
                record["issues"].append(issue)
                record["notices"] = []
                result["issues"].append({"component_id": identity, **issue})
            else:
                result["texts"].update(texts)
                total_text_bytes += additional
            if budget.read > MAX_TOTAL_READ_BYTES or budget.files > MAX_TOTAL_FILES:
                result["issues"].append({
                    "code": "remaining_components_not_collected",
                    "detail": "Collection stopped after exhausting its global source budget.",
                })
                break
        workspace_repo.finish()
    except _Problem as problem:
        result["issues"].append(problem.issue())
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        result["issues"].append({"code": "source_collection_failed_closed",
                                 "detail": "Malformed or changing input prevented bounded collection."})
    return result
