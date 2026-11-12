#!/usr/bin/env python3
"""GN1: one genuine disposable Ubuntu Graph Native owner startup, not R74/G2.

The source-bound CLI and daemon, real Store actor and restricted Graph manifest
are first-party binaries. All effects stay in one fresh RUNNER_TEMP owner.
Private originals are RSA3072/AES-GCM sealed before any artifact publication.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time

SCHEMA = "motionwright.graph-native-dev-startup/1"
STAGES = ("SOURCE_BIND", "BUILD_INPUTS", "STORE_INIT", "PROFILE_PATHS",
          "BOOTSTRAP_SOCKET", "BOOTSTRAP_PROJECT", "BOOTSTRAP_CLOSE",
          "RESTRICTED_MANIFEST", "RESTRICTED_SOCKET", "PROVIDER_HANDSHAKE",
          "FIRST_GRAPH_SAVE", "OWNER_CLOSE", "PRIVATE_SEAL")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
PROJECT = re.compile(r"prj_[0-9a-f]{32}\Z")
MAX_PRIVATE_BYTES = 32 * 1024 * 1024
MAX_PRIVATE_FILES = 256
MAX_PRIVATE_SINGLE = 8 * 1024 * 1024
MAX_DAEMON_LOG_BYTES = 2 * 1024 * 1024
MAX_NATIVE_STAGING_BYTES = 64 * 1024 * 1024


class GN1Failure(Exception):
    def __init__(self, code: str, unknown: bool = False):
        self.code, self.unknown = code, unknown
        super().__init__(code)


def need(value, code):
    if not value:
        raise GN1Failure(code)


def git(repo: Path, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       timeout=15, check=False,
                       env={**os.environ, "GIT_OPTIONAL_LOCKS": "0",
                            "GIT_NO_LAZY_FETCH": "1"})
    need(p.returncode == 0, "GN1_GIT_SOURCE_UNAVAILABLE")
    return p.stdout.strip().decode("utf-8")


def source_identity(repo: Path, sdk: Path, expected: str):
    """Reject remote fetches, dirty sources, SDK substitution and fake identity."""
    need(SHA40.fullmatch(expected) is not None, "GN1_EXPECTED_SOURCE_INVALID")
    need(repo.is_dir() and sdk.is_dir() and repo.resolve() == repo
         and sdk.resolve() == sdk, "GN1_SOURCE_ROOT_NOT_CANONICAL")
    need(Path(git(repo, "rev-parse", "--show-toplevel")).resolve() == repo,
         "GN1_REPO_ROOT_MISMATCH")
    need(git(repo, "rev-parse", "HEAD") == expected, "GN1_HEAD_MISMATCH")
    need(not git(repo, "status", "--porcelain=v1", "--untracked-files=no"),
         "GN1_TRACKED_SOURCE_DIRTY")
    lock = json.loads((repo / "SOURCE_LOCK.json").read_bytes())
    pin = lock["dependencies"]["semwright"]["revision"]
    need(SHA40.fullmatch(pin) is not None, "GN1_SDK_PIN_INVALID")
    need(git(sdk, "rev-parse", "HEAD") == pin, "GN1_SDK_HEAD_MISMATCH")
    need(not git(sdk, "status", "--porcelain=v1", "--untracked-files=no"),
         "GN1_SDK_TRACKED_DIRTY")
    return {"source_sha": expected, "tree": git(repo, "rev-parse", "HEAD^{tree}"),
            "sdk_sha": pin,
            "source_lock_sha256": hashlib.sha256((repo / "SOURCE_LOCK.json").read_bytes()).hexdigest()}


def require_runner():
    need(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
         and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
         and os.environ.get("GITHUB_REPOSITORY") == "seradotcom/motionwright",
         "GN1_DISPOSABLE_MANUAL_RUNNER_REQUIRED")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    need(re.fullmatch(r"[1-9][0-9]*", run_id) is not None, "GN1_RUN_ID_INVALID")
    return run_id


def require_owner_root(root: Path, run_id: str):
    temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    need(root.is_absolute() and root.parent.resolve(strict=True) == temp
         and root.name == "gn1-" + run_id and not root.exists()
         and not root.is_symlink(), "GN1_OWNER_NOT_FRESH_DISPOSABLE")
    return root


def mkdir(parent: Path, name: str):
    path = parent / name
    path.mkdir(mode=0o700)
    # Windows is used only for pure fixture tests; real GN1 effects are
    # Linux-runner-only and retain strict 0700 ownership admission there.
    need(path.resolve() == path
         and (sys.platform != "linux" or stat.S_IMODE(path.stat().st_mode) == 0o700),
         "GN1_PRIVATE_DIRECTORY_INVALID")
    return path


def new_bytes(path: Path, data: bytes, *, max_bytes=MAX_PRIVATE_SINGLE):
    need(type(data) is bytes and type(max_bytes) is int
         and 0 < len(data) <= max_bytes and not path.exists(),
         "GN1_PRIVATE_ORIGINAL_SIZE_OR_REPLAY")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    need(path.stat().st_size == len(data), "GN1_ORIGINAL_WRITE_FAILED")


def new_json(path: Path, row):
    new_bytes(path, (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode())


def file_identity(path: Path):
    need(path.is_absolute() and path.resolve(strict=True) == path
         and path.is_file() and not path.is_symlink(),
         "GN1_EXECUTABLE_IDENTITY_INVALID")
    info = path.stat()
    need(0 < info.st_size <= 256 * 1024 * 1024
         and info.st_mode & 0o111, "GN1_EXECUTABLE_MISSING_OR_UNBOUNDED")
    with path.open("rb") as f:
        sha = hashlib.file_digest(f, "sha256").hexdigest()
    need(info.st_size == path.stat().st_size, "GN1_EXECUTABLE_CHANGED")
    return {"path": str(path), "sha256": sha, "bytes": info.st_size}


def signed_binaries(repo: Path, sdk: Path, *, first_save=False):
    """Actual same-run locked Cargo products; no copied R74 source receipts."""
    sw = sdk / "target" / "debug"
    mw = repo / "target" / "debug"
    paths = {
        "semwright": sw / "semwright",
        "semwrightd": sw / "semwrightd",
        "semwright_sandbox": sw / "semwright-sandbox",
        "native_provider": mw / "motionwright-native-provider",
        "shared_state_actor": mw / "examples" / "shared-state-actor",
        "manim_runner": mw / "motionwright-manim-runner",
    }
    if first_save:
        # These are genuine compiled Rust examples for the optional single
        # Graph Store seed and Native JSONL Save, never invented authorities.
        paths.update({
            "graph_fixture": mw / "examples" / "native-graph-owner-fixture",
            "native_graph_session": mw / "examples" / "native-graph-review-session",
        })
    identities = {}
    for name, path in paths.items():
        need(not path.is_symlink() and path.resolve(strict=True) == path,
             "GN1_CARGO_EXECUTABLE_ALIAS_FORBIDDEN")
        identities[name] = file_identity(path)
    return identities


def simple_public(source, run_id):
    return {"schema": SCHEMA, "source_sha": source["source_sha"],
            "sdk_sha": source["sdk_sha"], "run_id": run_id,
            "stage": "SOURCE_BIND", "status": "UNKNOWN",
            "code": "GN1_NOT_STARTED",
            "physical_owner_startup": "NOT_EXECUTED",
            "Graph6_cases_accepted": 0, "R74_statuses_issued": 0,
            "G2": "NOT_GRANTED", "G3": "STOP",
            "formal_acceptance": False, "automatic_retry": False}


def daemon_staging_plan(binaries: dict):
    """Admit the four actual staged inputs before creating any owner state.

    The trusted daemon copies these executables before the driver sandbox is
    started. Its file budget is distinct from the unchanged driver 4 MiB
    limit and the original-evidence 8 MiB/32 MiB capture limits.
    """
    inputs = {
        "native_provider": binaries["native_provider"],
        "manim_runner": binaries["manim_runner"],
        "python": file_identity(Path(sys.executable).resolve(strict=True)),
        "ffmpeg": file_identity(Path("/usr/bin/ffmpeg").resolve(strict=True)),
    }
    need(all(type(row.get("bytes")) is int
             and 0 < row["bytes"] <= MAX_NATIVE_STAGING_BYTES
             and type(row.get("sha256")) is str
             and SHA256.fullmatch(row["sha256"]) is not None
             for row in inputs.values()), "GN1_STAGING_INPUT_BUDGET")
    budget = max(MAX_DAEMON_LOG_BYTES, *(row["bytes"] for row in inputs.values()))
    import resource
    hard = resource.getrlimit(resource.RLIMIT_FSIZE)[1]
    need(hard == resource.RLIM_INFINITY or hard >= budget,
         "GN1_STAGING_HARD_LIMIT_INSUFFICIENT")
    return {"inputs": inputs, "file_size_limit_bytes": budget,
            "daemon_log_limit_bytes": MAX_DAEMON_LOG_BYTES,
            "driver_file_size_limit_unchanged": True}


def executable_metadata(info):
    """Stable input identity; reading may change atime, never these fields."""
    return {"device": info.st_dev, "inode": info.st_ino, "bytes": info.st_size,
            "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid,
            "gid": info.st_gid, "links": info.st_nlink,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def stage_executable_input(expected: dict, directory: Path, name: str):
    """Prepare one immutable private copy without chmod on a build/tool input.

    Source-bound Cargo and toolchain inputs can retain group-writable modes.
    The SDK correctly rejects those modes. Copy only the already admitted
    bytes to an exclusive owner-only file, then verify the unchanged source
    and completed destination before making the copy executable. The source
    metadata, including its original writable bits, remains explicit evidence.
    """
    need(name in ("motionwright-native-provider", "motionwright-manim-runner",
                  "python3", "ffmpeg"), "GN1_EXECUTABLE_INPUT_ROLE")
    source = Path(expected["path"])
    need(source.is_absolute() and source.resolve(strict=True) == source
         and not any(path.is_symlink() for path in (source, *source.parents)),
         "GN1_EXECUTABLE_INPUT_ALIAS")
    need(type(expected.get("bytes")) is int
         and 0 < expected["bytes"] <= MAX_NATIVE_STAGING_BYTES
         and type(expected.get("sha256")) is str
         and SHA256.fullmatch(expected["sha256"]) is not None,
         "GN1_EXECUTABLE_INPUT_BOUND")
    need(directory.resolve(strict=True) == directory and directory.is_dir()
         and stat.S_IMODE(directory.stat().st_mode) == 0o700
         and directory.stat().st_uid == os.getuid(),
         "GN1_EXECUTABLE_INPUT_DIRECTORY")
    destination = directory / name
    read_fd = write_fd = None
    try:
        read_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(read_fd)
        need(stat.S_ISREG(before.st_mode) and before.st_size == expected["bytes"]
             and before.st_mode & 0o111 and before.st_uid in (0, os.getuid()),
             "GN1_EXECUTABLE_INPUT_SOURCE_IDENTITY")
        original = executable_metadata(before)
        need(original == executable_metadata(source.lstat()),
             "GN1_EXECUTABLE_INPUT_CHANGED")
        write_fd = os.open(destination, os.O_RDWR | os.O_CREAT | os.O_EXCL
                           | os.O_NOFOLLOW, 0o600)
        digest = hashlib.sha256()
        remaining = expected["bytes"]
        first_chunk = True
        while remaining:
            raw = os.read(read_fd, min(1024 * 1024, remaining))
            need(bool(raw), "GN1_EXECUTABLE_INPUT_SHORT_READ")
            if first_chunk:
                need(raw.startswith(b"\x7fELF"), "GN1_EXECUTABLE_INPUT_NOT_ELF")
                first_chunk = False
            digest.update(raw)
            remaining -= len(raw)
            pending = memoryview(raw)
            while pending:
                written = os.write(write_fd, pending)
                need(written > 0, "GN1_EXECUTABLE_INPUT_SHORT_WRITE")
                pending = pending[written:]
        need(not os.read(read_fd, 1)
             and original == executable_metadata(os.fstat(read_fd))
             and original == executable_metadata(source.lstat()),
             "GN1_EXECUTABLE_INPUT_CHANGED")
        need(digest.hexdigest() == expected["sha256"],
             "GN1_EXECUTABLE_INPUT_DIGEST")
        os.fsync(write_fd)
        os.lseek(write_fd, 0, os.SEEK_SET)
        copied_digest = hashlib.sha256()
        remaining = expected["bytes"]
        while remaining:
            raw = os.read(write_fd, min(1024 * 1024, remaining))
            need(bool(raw), "GN1_EXECUTABLE_COPY_SHORT_READ")
            copied_digest.update(raw)
            remaining -= len(raw)
        need(not os.read(write_fd, 1)
             and copied_digest.hexdigest() == expected["sha256"],
             "GN1_EXECUTABLE_COPY_DIGEST")
        # Only this newly created private descriptor changes mode. Original
        # toolchain files, their ACLs and SDK admission checks stay unchanged.
        os.fchmod(write_fd, 0o500)
        os.fsync(write_fd)
        completed = os.fstat(write_fd)
        need(stat.S_ISREG(completed.st_mode) and completed.st_nlink == 1
             and completed.st_uid == os.getuid()
             and stat.S_IMODE(completed.st_mode) == 0o500
             and completed.st_size == expected["bytes"]
             and executable_metadata(completed) == executable_metadata(destination.lstat()),
             "GN1_EXECUTABLE_COPY_IDENTITY")
        return {"path": str(destination), "sha256": expected["sha256"],
                "bytes": expected["bytes"]}, {
            "source": dict(expected), "source_metadata": original,
            "source_group_or_other_writable": bool(original["mode"] & 0o022),
            "private_copy": {"path": str(destination), "sha256": expected["sha256"],
                             **executable_metadata(completed)},
            "source_modified": False, "automatic_retry": False,
        }
    finally:
        # Preserve a failed/partial destination and never reuse it. A later
        # attempt must not relabel or overwrite this preparation's outcome.
        for descriptor in (write_fd, read_fd):
            if descriptor is not None:
                os.close(descriptor)


def prepare_executable_inputs(root: Path, private: Path, staging: dict):
    names = {"native_provider": "motionwright-native-provider",
             "manim_runner": "motionwright-manim-runner",
             "python": "python3", "ffmpeg": "ffmpeg"}
    inputs = staging["inputs"]
    need(type(inputs) is dict and set(inputs) == set(names),
         "GN1_EXECUTABLE_INPUT_ROSTER")
    need(all(type(row.get("bytes")) is int
             and 0 < row["bytes"] <= MAX_NATIVE_STAGING_BYTES
             for row in inputs.values()), "GN1_EXECUTABLE_INPUT_BOUND")
    total = sum(row["bytes"] for row in inputs.values())
    need(total <= 4 * MAX_NATIVE_STAGING_BYTES, "GN1_EXECUTABLE_INPUT_TOTAL")
    new_json(private / "executable-inputs.intent.original.json", {
        "schema": "motionwright.gn1-executable-inputs/1", "inputs": inputs,
        "copy_files": 4, "copy_bytes": total, "source_files_modified": False,
        "derived_executable_bytes_archived": False, "automatic_retry": False,
    })
    directory = mkdir(root, "executable-inputs")
    prepared, originals = {}, {}
    for role, name in names.items():
        prepared[role], originals[role] = stage_executable_input(inputs[role], directory, name)
        new_json(private / ("executable-input-" + role + ".original.json"), originals[role])
    return prepared


def _limits(file_size_limit=MAX_DAEMON_LOG_BYTES):
    import resource
    need(type(file_size_limit) is int
         and MAX_DAEMON_LOG_BYTES <= file_size_limit <= MAX_NATIVE_STAGING_BYTES,
         "GN1_DAEMON_FILE_LIMIT_INVALID")
    for key, bound in (("RLIMIT_CPU", 120), ("RLIMIT_FSIZE", file_size_limit),
                       ("RLIMIT_AS", 8 * 1024 * 1024 * 1024),
                       ("RLIMIT_NOFILE", 1024)):
        hard = getattr(resource, key)
        current, maximum = resource.getrlimit(hard)
        restricted = min(bound, maximum) if maximum != resource.RLIM_INFINITY else bound
        resource.setrlimit(hard, (restricted, maximum))


class DaemonLogCapture:
    """Bound the log independently of executable staging; retain an honest prefix."""
    def __init__(self, process, fd):
        self.process, self.fd = process, fd
        self.observed = self.written = 0
        self.eof = self.overflow = False
        self.error = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _stop_original(self):
        if self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as error:
            self.error = type(error).__name__

    def _read(self):
        try:
            pipe = self.process.stdout.fileno()
            os.set_blocking(pipe, False)
            while not self.stop.is_set():
                if not select.select([pipe], [], [], 0.05)[0]:
                    continue
                chunk = os.read(pipe, 65536)
                if not chunk:
                    self.eof = True
                    break
                self.observed += len(chunk)
                room = MAX_DAEMON_LOG_BYTES - self.written
                retained = memoryview(chunk)[:room]
                while retained:
                    count = os.write(self.fd, retained)
                    self.written += count
                    retained = retained[count:]
                if len(chunk) > room:
                    self.overflow = True
                    # Stop only the original owned group, never retry an effect.
                    self._stop_original()
                    break
        except BaseException as error:
            self.error = type(error).__name__
            self._stop_original()
        finally:
            try:
                os.fsync(self.fd)
            except OSError as error:
                self.error = type(error).__name__
            try:
                os.close(self.fd)
                self.process.stdout.close()
            except OSError as error:
                self.error = type(error).__name__

    def finish(self):
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            self.stop.set()
            self.thread.join(timeout=0.2)
        return {"limit_bytes": MAX_DAEMON_LOG_BYTES,
                "observed_stream_bytes": self.observed,
                "retained_prefix_bytes": self.written,
                "complete": (self.eof and not self.overflow and not self.error
                             and not self.thread.is_alive()),
                "overflow": self.overflow, "capture_error_type": self.error,
                "automatic_retry": False}


def _stop_group(child: subprocess.Popen, label: str, private: Path):
    """Close this single original attempt before any further Native effect."""
    if child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            new_json(private / (label + ".quiescence-failed.original.json"),
                     {"status": "UNKNOWN", "pid": child.pid, "automatic_retry": False})
            raise GN1Failure("GN1_OWNER_GROUP_NOT_QUIESCENT", unknown=True)
    try:
        os.killpg(child.pid, 0)
    except ProcessLookupError:
        return child.returncode
    except PermissionError:
        raise GN1Failure("GN1_GROUP_IDENTITY_UNKNOWN", unknown=True) from None
    raise GN1Failure("GN1_OWNER_GROUP_STILL_ALIVE", unknown=True)


@contextlib.contextmanager
def real_daemon(label: str, executable: str, config: Path, socket: Path,
                env: dict, private: Path, extra_args=(),
                file_size_limit=MAX_DAEMON_LOG_BYTES):
    need(label in ("bootstrap", "restricted") and socket.is_absolute()
         and not socket.exists(), "GN1_DAEMON_SOCKET_SOURCE_OR_REPLAY")
    log = private / (label + ".daemon.original.log")
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    proc = None
    capture = None
    terminal = None
    try:
        proc = subprocess.Popen(
            [executable, "--config", str(config), "--socket", str(socket),
             *extra_args],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=lambda: _limits(file_size_limit),
        )
        capture = DaemonLogCapture(proc, fd)
        new_json(private / (label + ".start.original.json"),
                 {"pid": proc.pid, "config_sha256":
                  hashlib.sha256(config.read_bytes()).hexdigest(),
                  "automatic_retry": False, "source_built": True})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if capture.overflow or capture.error:
                raise GN1Failure("GN1_DAEMON_LOG_CAPTURE_INCOMPLETE", unknown=True)
            terminal = proc.poll()
            if terminal is not None:
                raise GN1Failure("GN1_" + label.upper() + "_EARLY_EXIT")
            if socket.is_socket():
                yield proc
                return
            need(not socket.exists(), "GN1_" + label.upper() + "_FOREIGN_SOCKET")
            time.sleep(0.02)
        raise GN1Failure("GN1_" + label.upper() + "_SOCKET_UNKNOWN", unknown=True)
    finally:
        if capture is None:
            os.close(fd)
        if proc is not None:
            close_error = None
            try:
                observed = _stop_group(proc, label, private)
            except BaseException as error:
                close_error, observed = error, proc.poll()
            log_capture = capture.finish() if capture is not None else {
                "complete": False, "capture_error_type": "CAPTURE_NOT_STARTED"}
            new_json(private / (label + ".log-capture.original.json"), log_capture)
            new_json(private / (label + ".terminal.original.json"),
                     {"pid": proc.pid, "exit_code": observed,
                      "process_group_closed": close_error is None, "automatic_retry": False,
                      "socket_was_ready": socket.is_socket()})
            if close_error is not None:
                raise close_error
            if not log_capture["complete"]:
                raise GN1Failure("GN1_DAEMON_LOG_CAPTURE_INCOMPLETE", unknown=True)


def _env(root: Path, runtime: Path, state: Path, home: Path, sdk: Path):
    # First-party Linux toolchain locations and locale are deterministic;
    # no arbitrary runner variables, executable search path, or credential
    # can cross into semwrightd, CLI, or DriverProvider subprocesses.
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "HOME": str(home),
        "XDG_RUNTIME_DIR": str(runtime),
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_CACHE_HOME": str(root / "manim-work"),
        "TMPDIR": str(root / "manim-work"),
        "SEMWRIGHT_CHECKOUT": str(sdk),
        "RUST_BACKTRACE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def actual_startup(repo: Path, sdk: Path, root: Path, first: dict,
                   private: Path, marks: list[dict], staging: dict,
                   *, one_original_save=False):
    # Graph's original source owns the manifest/config rules. GN1 does not
    # invent a new broker policy, grant or fake project identity.
    sys.path.insert(0, str(repo / "tooling"))
    import native_graph_browser_owner as graph
    import native_host_shared_state as shared
    binaries = signed_binaries(repo, sdk, first_save=one_original_save)
    need(all(binaries[name] == staging["inputs"][name]
             for name in ("native_provider", "manim_runner")),
         "GN1_STAGING_SOURCE_CHANGED")
    new_json(private / "source-built-binaries.original.json", binaries)

    def mark(stage):
        need(stage in STAGES, "GN1_UNREVIEWED_STAGE")
        marks.append({"stage": stage, "elapsed_ms": time.monotonic_ns() // 1_000_000})
        new_json(private / ("%02d-" % len(marks) + stage + ".original.json"),
                 {"stage": stage, "source": first["source_sha"],
                  "automatic_retry": False})

    mark("BUILD_INPUTS")
    prepared_inputs = prepare_executable_inputs(root, private, staging)
    locations = {}
    for name in ("home", "state", "runtime", "config", "clients",
                 "motionwright-data", "manim-runtime", "manim-work"):
        locations[name] = mkdir(root, name)
    owner_env = _env(root, locations["runtime"], locations["state"],
                     locations["home"], sdk)
    store_path = locations["motionwright-data"] / "motionwright.sqlite3"

    mark("STORE_INIT")
    if one_original_save:
        # The actual Native fixture writes a real edited scene to a fresh
        # SQLite Store. It retains the authored Graph as UNSAVED source.
        if __package__:
            from .ci_graph_dev_native_firstsave import FirstSaveFailure, seed_original
        else:
            from ci_graph_dev_native_firstsave import FirstSaveFailure, seed_original
        try:
            seeded = seed_original(Path(binaries["graph_fixture"]["path"]),
                                   store_path, private, owner_env)
        except FirstSaveFailure as error:
            raise GN1Failure(error.code, unknown=error.unknown) from None
    else:
        seeded = shared.actor(owner_env, store_path, "init", "GN1 isolated actual Store")
    need(type(seeded) is dict and type(seeded.get("id")) is str
         and type(seeded.get("resource")) is str
         and seeded["resource"].startswith("project:")
         and store_path.is_file(), "GN1_ACTOR_REAL_STORE_INIT_FAILED")
    original = graph.file_identity(store_path)
    new_json(private / "physical-store.original.json",
             {"path": original["path"], "device": original["device"],
              "inode": original["inode"], "sha256": original["sha256"]})

    mark("PROFILE_PATHS")
    returned = shared.run_json(
        [binaries["semwright"]["path"], "--json", "config", "paths"], env=owner_env)
    need(type(returned) is dict and type(returned.get("state")) is str
         and Path(returned["state"]).resolve().is_relative_to(locations["state"]),
         "GN1_CLI_OWNER_STATE_ESCAPED")
    new_json(private / "profile-paths.original.json", returned)

    bootstrap_toml = locations["config"] / "owner-bootstrap.toml"
    new_bytes(bootstrap_toml, shared.owner_config(
        locations["motionwright-data"]).encode())
    bootstrap_socket = locations["runtime"] / "bootstrap.sock"
    calls = []

    mark("BOOTSTRAP_SOCKET")
    with real_daemon("bootstrap", binaries["semwrightd"]["path"],
                     bootstrap_toml, bootstrap_socket, owner_env, private):
        mark("BOOTSTRAP_PROJECT")
        result = shared.invoke_cli(
            owner_env, bootstrap_socket,
            locations["clients"] / "bootstrap.session",
            "project.create", {"root": "motionwright-data"}, calls,
            evidence_root=private)["envelope"]
        core_project = result.get("data", {}).get("project")
        need(isinstance(core_project, str) and PROJECT.fullmatch(core_project),
             "GN1_CORE_DID_NOT_ISSUE_REAL_PROJECT")
    mark("BOOTSTRAP_CLOSE")

    provider = prepared_inputs["native_provider"]
    manim_runner = prepared_inputs["manim_runner"]
    source_inputs = {"binaries": {
        "motionwright-native-provider": dict(provider),
        "motionwright-manim-runner": dict(manim_runner),
    }}
    runtime = {
        "runtime": locations["manim-runtime"],
        "fontconfig": Path("/etc/fonts").resolve(strict=True),
        "python": prepared_inputs["python"],
        "ffmpeg": prepared_inputs["ffmpeg"],
    }

    mark("RESTRICTED_MANIFEST")
    # graph_manifest uses only this bounded max_processes for its limits.
    previous_spec = graph._OWNER_SPEC
    graph._OWNER_SPEC = {"limits": {"max_processes": 32}}
    try:
        manifest = graph.graph_manifest(repo, source_inputs, runtime,
                                        locations["manim-work"])
    finally:
        graph._OWNER_SPEC = previous_spec
    manifest_file = locations["config"] / "motionwright-driver.json"
    new_json(manifest_file, manifest)

    selected = graph.graph_owner_config(
        shared, store_path, manifest_file, seeded["resource"], core_project,
        runtime, source_inputs, locations["manim-work"])
    restricted_file = locations["config"] / "owner-restricted.toml"
    new_bytes(restricted_file, selected.encode("utf-8"))
    restricted_socket = locations["runtime"] / "broker.sock"

    # This switches on the audited first-party private driver stderr receipt
    # under its normal ownership contract; no grants or fallback are changed.
    capture_args = shared.driver_startup_stderr_args(
        root, "shared-state", selected=True)
    mark("RESTRICTED_SOCKET")
    with real_daemon("restricted", binaries["semwrightd"]["path"],
                     restricted_file, restricted_socket, owner_env, private,
                     extra_args=capture_args,
                     file_size_limit=staging["file_size_limit_bytes"]):
        mark("PROVIDER_HANDSHAKE")
        mounted = shared.invoke_cli(
            owner_env, restricted_socket,
            locations["clients"] / "restricted.session",
            "providers.mount.observe",
            {"provider": "driver:motionwright", "root": "motionwright-data"},
            calls, evidence_root=private)["envelope"]["data"]
        need(type(mounted) is dict
             and mounted.get("provider") == "driver:motionwright"
             and mounted.get("mount", {}).get("root") == "motionwright-data",
             "GN1_RESTRICTED_REAL_PROVIDER_HANDSHAKE_FAILED")
        if one_original_save:
            # One real scoped service grant and one actual Native Graph Save.
            # No R74, no Graph6 G2; the original Store is disposable.
            if __package__:
                from .ci_graph_dev_native_firstsave import FirstSaveFailure, real_first_save
            else:
                from ci_graph_dev_native_firstsave import FirstSaveFailure, real_first_save
            mark("FIRST_GRAPH_SAVE")
            # Seal any Native output together with the private operation
            # originals, not as a separate uncollected runner directory.
            output = mkdir(private, "gn1-first-save-output")
            session_file = locations["clients"] / "restricted.session"
            release_used = False

            def release_one_bound_provider():
                nonlocal release_used
                need(not release_used, "GN1_FIRST_SAVE_PROVIDER_RELEASE_REPLAY")
                release_used = True  # Even an UNKNOWN CLI call cannot be retried.
                need(session_file.is_file() and restricted_socket.is_socket(),
                     "GN1_FIRST_SAVE_SESSION_OR_BROKER_CLOSED")
                fresh_entry = shared.invoke_cli(
                    owner_env, restricted_socket, session_file,
                    "providers.mount.observe",
                    {"provider": mounted["provider"], "root": "motionwright-data"},
                    calls, evidence_root=private)
                fresh = fresh_entry["envelope"]["data"]
                need(type(fresh) is dict
                     and type(fresh.get("binding_ref")) is str
                     and bool(fresh["binding_ref"])
                     and fresh.get("provider") == mounted["provider"]
                     and fresh.get("connection") == mounted["connection"]
                     and fresh.get("provider_generation") == mounted["provider_generation"]
                     and fresh.get("mount") == mounted["mount"],
                     "GN1_FIRST_SAVE_PROVIDER_GENERATION_OR_MOUNT_CHANGED")
                release_entry = shared.invoke_cli(
                    owner_env, restricted_socket, session_file,
                    "providers.release_bound",
                    {"provider": fresh["provider"], "root": "motionwright-data",
                     "binding_ref": fresh["binding_ref"]},
                    calls, evidence_root=private)
                released = release_entry["envelope"]["data"]
                need(type(released) is dict
                     and released.get("provider") == mounted["provider"]
                     and released.get("connection") == mounted["connection"]
                     and released.get("provider_generation") == mounted["provider_generation"]
                     and released.get("binding_ref") == fresh["binding_ref"]
                     and released.get("lease_shutdown_completed") is True
                     and released.get("native_process_closure_inferred") is False
                     and restricted_socket.is_socket(),
                     "GN1_FIRST_SAVE_PROVIDER_RELEASE_NOT_DRAINED")
                new_json(private / "first-save-provider-release.original.json",
                         {"schema": "motionwright.gn1-provider-release/1",
                          "original_provider": mounted["provider"],
                          "connection_generation_unchanged": True,
                          "same_original_mount": True,
                          "lease_shutdown_completed": True,
                          "native_process_closure_inferred": False,
                          "broker_remained_live": True,
                          "automatic_retry": False})
                return released

            try:
                observed_save = real_first_save(
                    fixture=Path(binaries["graph_fixture"]["path"]),
                    native_session=Path(binaries["native_graph_session"]["path"]),
                    source=seeded, store_path=store_path,
                    client={"session_file": session_file, "socket": restricted_socket},
                    mounted=mounted, binaries=binaries,
                    private=private, output=output, env=owner_env,
                    release_provider=release_one_bound_provider)
            except FirstSaveFailure as error:
                raise GN1Failure(error.code, unknown=error.unknown) from None
            new_json(private / "first-save-result.original.json", observed_save)
    mark("OWNER_CLOSE")
    if one_original_save:
        # Independent read-only process opens the same SQLite after the
        # writer's WAL->DELETE and the restricted broker have closed. The
        # Native graph factory is never invoked in this read-only branch.
        if __package__:
            from .ci_graph_dev_native_firstsave import (
                FirstSaveFailure, NativeSession, digest,
                require_same_opened_sqlite_witness,
            )
        else:
            from ci_graph_dev_native_firstsave import (
                FirstSaveFailure, NativeSession, digest,
                require_same_opened_sqlite_witness,
            )
        need(observed_save["provider_lease_shutdown_completed"] is True
             and observed_save["one_native_graph_save_and_reopen"] is True,
             "GN1_FIRST_SAVE_NO_AUTHENTIC_WRITER_CLOSURE")
        before_reader = graph.file_identity(store_path)
        with NativeSession(
                Path(binaries["native_graph_session"]["path"]),
                store_path, private / "graph-connection.original.json",
                private, owner_env, read_only=True) as reader:
            reopened = reader.request(
                "independent-readonly-project", "get_project",
                {"project_id": observed_save["saved_project_id"]})
            need(type(reopened) is dict and reopened.get("ok") is True
                 and type(reopened.get("result")) is dict,
                 "GN1_FIRST_SAVE_INDEPENDENT_READONLY_NOT_READY")
            read_project = reopened["result"]
            if not (
                    read_project.get("id") == observed_save["saved_project_id"]
                    and read_project.get("revision") == observed_save["saved_revision"]
                    and digest(json.dumps(read_project, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False).encode())
                        == observed_save["saved_project_sha256"]):
                raise GN1Failure("GN1_FIRST_SAVE_INDEPENDENT_READONLY_CHANGED",
                                 unknown=True)
            # This is a SECOND process with a distinct held SQLite pager,
            # not a path/inode string supplied by the original writer. Both
            # responses must originate from their own real JSONL processes.
            read_pager = reader.request(
                "independent-opened-sqlite",
                "opened_store_identity_witness", None)
            try:
                require_same_opened_sqlite_witness(
                    observed_save.get("writer_opened_store_identity_witness"),
                    read_pager)
            except FirstSaveFailure as error:
                raise GN1Failure(error.code, unknown=error.unknown) from None
        after_reader = graph.file_identity(store_path)
        need((before_reader["device"], before_reader["inode"])
             == (after_reader["device"], after_reader["inode"]),
             "GN1_FIRST_SAVE_READONLY_PHYSICAL_STORE_CHANGED")
        new_json(private / "independent-readonly-reopen.original.json",
                 {"schema": "motionwright.gn1-independent-readonly/1",
                  "source_built_executable_sha256": binaries["native_graph_session"]["sha256"],
                  "original_store": before_reader,
                  "after_readonly": after_reader,
                  "saved_project_sha256": observed_save["saved_project_sha256"],
                  "same_store": True,
                  "opened_sqlite_identity_witness_match": True,
                  "read_only_process_exit_zero": True,
                  "native_authoring_in_reader": False,
                  "Graph6_accepted": 0, "formal_G2_granted": False,
                  "automatic_retry": False})
    observed_source = source_identity(repo, sdk, first["source_sha"])
    need(observed_source == first, "GN1_SOURCE_CHANGED_AFTER_REAL_OWNER")
    updated = graph.file_identity(store_path)
    need((updated["device"], updated["inode"]) ==
         (original["device"], original["inode"]),
         "GN1_PHYSICAL_STORE_IDENTITY_CHANGED")
    new_json(private / "end-state.original.json",
             {"same_physical_store": True, "source": first["source_sha"],
              "project": core_project,
              "real_native_daemon": True, "Graph6_accepted": 0,
              "R74_statuses_issued": 0, "automatic_retry": False})
    return {"real_store_original": original, "private_call_count": len(calls),
            "one_native_graph_save": one_original_save,
            "Graph6_accepted": 0, "formal_G2_granted": False}


def private_snapshot(root: Path, *, preserve_partial=False):
    """Only owned originals, never sockets/symlinks, public unsealed bytes."""
    if preserve_partial:
        return partial_private_snapshot(root)
    entries = []
    total = 0
    for name in ("private", "config", "motionwright-data", "state",
                 "startup-stderr"):
        directory = root / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise GN1Failure("GN1_PRIVATE_SYMLINK_NOT_SEALED", unknown=True)
            if path.is_dir():
                continue
            need(path.is_file() and not path.is_socket()
                 and stat.S_ISREG(path.lstat().st_mode),
                 "GN1_PRIVATE_SPECIAL_FILE",)
            length = path.stat().st_size
            need(0 <= length <= MAX_PRIVATE_SINGLE,
                 "GN1_ORIGINAL_OVERSIZE",)
            total += length
            need(total <= MAX_PRIVATE_BYTES
                 and len(entries) < MAX_PRIVATE_FILES,
                 "GN1_PRIVATE_SNAPSHOT_LIMIT")
            relative = path.relative_to(root).as_posix()
            need(not any(chunk in ("", ".", "..") for chunk in relative.split("/")),
                 "GN1_PRIVATE_SNAPSHOT_ESCAPE")
            entries.append((relative, path))
    need(bool(entries), "GN1_EMPTY_PRIVATE_ORIGINALS")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, file in entries:
            raw = file.read_bytes()
            need(len(raw) == file.stat().st_size, "GN1_ORIGINAL_CHANGED")
            item = tarfile.TarInfo(name)
            item.size, item.mode, item.mtime = len(raw), 0o600, 0
            archive.addfile(item, io.BytesIO(raw))
    raw = buffer.getvalue()
    need(len(raw) <= MAX_PRIVATE_BYTES + 2 * 1024 * 1024,
         "GN1_SNAPSHOT_ARCHIVE_OVERSIZE")
    return raw, len(entries)


def partial_private_snapshot(root: Path):
    """Seal bounded diagnostics after failure without hiding a missing original.

    An oversized staged executable remains untouched. Its observed metadata
    and the explicit omission are encrypted alongside readable originals;
    this is incomplete custody and cannot produce a passing public outcome.
    The strict snapshot API and all original byte/count limits stay intact.
    """
    entries, omitted, total, inspected = [], [], 0, 0

    def metadata(info):
        return {"device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode,
                "bytes": info.st_size, "links": info.st_nlink,
                "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}

    def missing(path, code, info=None):
        row = {"path": path.relative_to(root).as_posix(), "code": code,
               "original_not_deleted": True, "full_bytes_captured": False}
        if info is not None:
            row["observed_metadata"] = metadata(info)
        omitted.append(row)

    for name in ("private", "config", "motionwright-data", "state", "startup-stderr"):
        directory = root / name
        if not directory.exists() and not directory.is_symlink():
            continue
        if directory.is_symlink() or not directory.is_dir():
            missing(directory, "GN1_PRIVATE_DIRECTORY_NOT_CAPTURED")
            continue
        for path in directory.rglob("*"):
            inspected += 1
            if inspected > MAX_PRIVATE_FILES * 4:
                missing(directory, "GN1_PRIVATE_NAMESPACE_LIMIT")
                break
            info = None
            try:
                info = path.lstat()
                need(not stat.S_ISLNK(info.st_mode), "GN1_PRIVATE_SYMLINK_NOT_SEALED")
                if stat.S_ISDIR(info.st_mode):
                    continue
                need(stat.S_ISREG(info.st_mode), "GN1_PRIVATE_SPECIAL_FILE")
                need(info.st_nlink == 1 and path.resolve(strict=True) == path,
                     "GN1_PRIVATE_FILE_ALIAS")
                need(0 <= info.st_size <= MAX_PRIVATE_SINGLE, "GN1_ORIGINAL_OVERSIZE")
                need(total + info.st_size <= MAX_PRIVATE_BYTES
                     and len(entries) < MAX_PRIVATE_FILES - 1,
                     "GN1_PRIVATE_SNAPSHOT_LIMIT")  # Reserve one slot for coverage metadata.
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(fd, "rb") as file:
                    opened = os.fstat(file.fileno())
                    need(stat.S_ISREG(opened.st_mode), "GN1_PRIVATE_SPECIAL_FILE")
                    need(metadata(opened) == metadata(info),
                         "GN1_ORIGINAL_CHANGED")
                    raw = file.read(info.st_size + 1)
                    need(len(raw) == info.st_size
                         and metadata(os.fstat(file.fileno())) == metadata(info)
                         and metadata(path.lstat()) == metadata(info),
                         "GN1_ORIGINAL_CHANGED")
                entries.append((path.relative_to(root).as_posix(), raw))
                total += len(raw)
            except Exception as error:
                missing(path, error.code if isinstance(error, GN1Failure)
                        else "GN1_PRIVATE_READ_FAILED", info)
        if inspected > MAX_PRIVATE_FILES * 4:
            break

    # Account for the coverage record and tar framing without enlarging the
    # 32 MiB original/2 MiB framing allowance. Removing an archive entry here
    # records an omission; it never deletes or modifies the original file.
    while True:
        coverage = {"schema": "motionwright.gn1-private-capture/1",
                    "complete": not omitted, "captured_originals": len(entries),
                    "captured_original_bytes": total, "omitted_originals": omitted,
                    "automatic_retry": False, "original_files_deleted": 0}
        description = (json.dumps(coverage, sort_keys=True, separators=(",", ":")) + "\n").encode()
        rows = [*sorted(entries), ("gn1-private-capture.json", description)]
        required = 10240
        for name, data in rows:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            required += len(item.tobuf(format=tarfile.PAX_FORMAT)) + ((len(data) + 511) // 512) * 512
        if required <= MAX_PRIVATE_BYTES + 2 * 1024 * 1024:
            break
        need(bool(entries), "GN1_CAPTURE_METADATA_LIMIT")
        name, data = entries.pop()
        total -= len(data)
        missing(root / name, "GN1_SNAPSHOT_ARCHIVE_OVERSIZE")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, raw in rows:
            item = tarfile.TarInfo(name)
            item.size, item.mode, item.mtime = len(raw), 0o600, 0
            archive.addfile(item, io.BytesIO(raw))
    raw = buffer.getvalue()
    need(len(raw) <= MAX_PRIVATE_BYTES + 2 * 1024 * 1024,
         "GN1_SNAPSHOT_ARCHIVE_OVERSIZE")
    return raw, len(entries), coverage


def original_owner_start_observation(private: Path, marks: list[dict]):
    """A stage marker precedes Popen and cannot prove that a daemon ran.

    Only a closed, original post-Popen receipt proves an observed launch.
    Absence after the stage was reached remains UNKNOWN, never NOT_EXECUTED.
    """
    recorded = 0
    unobserved = False
    for label, stage in (("bootstrap", "BOOTSTRAP_SOCKET"),
                         ("restricted", "RESTRICTED_SOCKET")):
        reached = any(row.get("stage") == stage for row in marks)
        path = private / (label + ".start.original.json")
        need(not path.is_symlink(), "GN1_START_RECEIPT_SYMLINK")
        if path.exists():
            need(reached and path.is_file() and 0 < path.stat().st_size <= 8192,
                 "GN1_START_RECEIPT_INVALID")
            receipt = json.loads(path.read_bytes())
            need(type(receipt) is dict and type(receipt.get("pid")) is int
                 and receipt["pid"] > 0 and receipt.get("automatic_retry") is False,
                 "GN1_START_RECEIPT_INVALID")
            recorded += 1
        elif reached:
            unobserved = True
    state = ("UNKNOWN" if unobserved else
             "RECORDED_REAL_START_NOT_G2" if recorded else "NOT_EXECUTED")
    return {"physical_owner_startup": state,
            "original_start_receipts": recorded,
            "unobserved_launch_possible": unobserved}


def run_once(args):
    """Single selected real Native startup. One new Store, never replay."""
    run_id = require_runner()
    repo = args.repo.resolve(strict=True)
    sdk = args.sdk.resolve(strict=True)
    need(os.environ.get("GITHUB_SHA") == args.expected_source,
         "GN1_GITHUB_SOURCE_MISMATCH")
    first = source_identity(repo, sdk, args.expected_source)
    root = require_owner_root(args.root, run_id)

    # Validate the reviewer public key BEFORE any physical Store/daemon effect.
    # The review private key never enters the runner.
    sys.path.insert(0, str(repo / "tooling"))
    import ci_private_evidence as custody
    raw_recipient = os.environ.get("GN1_RECIPIENT_JSON", "")
    need(0 < len(raw_recipient) <= 4096, "GN1_RECIPIENT_REQUIRED")
    recipient = json.loads(raw_recipient)
    custody.public_key(recipient)
    if __package__:
        from .ci_graph_dev_native_firstsave import selected as first_save_selected
    else:
        from ci_graph_dev_native_firstsave import selected as first_save_selected
    # A second explicit permit is required. Existing GN1 workflows default to
    # startup-only, and a Save cannot piggyback on their owner approval.
    need(os.environ.get("GN1_ONE_ORIGINAL_OWNER") == "allow-one-original",
         "GN1_EXPLICIT_OWNER_SELECTION_REQUIRED")
    one_original_save = first_save_selected(os.environ.get("GN1_FIRST_SAVE", "deny"))
    binaries = signed_binaries(repo, sdk, first_save=one_original_save)
    staging = daemon_staging_plan(binaries)  # Before Store/daemon/owner effects.

    root.mkdir(mode=0o700)
    need(stat.S_IMODE(root.stat().st_mode) == 0o700, "GN1_OWNER_ROOT_MODE")
    private = mkdir(root, "private")
    transport = mkdir(root, "transport")
    marks = []
    public = simple_public(first, run_id)
    public["stage"] = "SOURCE_BIND"
    status = "UNKNOWN"
    code = "GN1_UNREVIEWED_FAILURE"
    try:
        marks.append({"stage": "SOURCE_BIND", "source": first["source_sha"]})
        new_json(private / "source-binding.original.json", first)
        new_json(private / "daemon-staging-budget.original.json", staging)
        actual_startup(repo, sdk, root, first, private, marks, staging,
                       one_original_save=one_original_save)
        status, code = (("PASS_DEV_NATIVE_FIRST_SAVE_ONLY", "GN1_REAL_FIRST_SAVE_REOPENED")
                        if one_original_save else
                        ("PASS_DEV_NATIVE_STARTUP_ONLY", "GN1_REAL_OWNER_READY_CLOSED"))
    except GN1Failure as e:
        status, code = ("UNKNOWN" if e.unknown else "FAIL"), e.code
        new_json(private / "failure.original.json",
                 {"code": e.code, "exception_type": type(e).__name__,
                  "raw_error": str(e), "unknown": e.unknown,
                  "automatic_retry": False})
    except BaseException as e:
        status, code = "UNKNOWN", "GN1_INTERNAL_UNKNOWN"
        new_json(private / "failure.original.json",
                 {"code": code, "exception_type": type(e).__name__,
                  "raw_error": str(e), "automatic_retry": False})

    launch = original_owner_start_observation(private, marks)
    if status in ("PASS_DEV_NATIVE_STARTUP_ONLY",
                   "PASS_DEV_NATIVE_FIRST_SAVE_ONLY") and (
            launch["original_start_receipts"] != 2
            or launch["unobserved_launch_possible"]):
        status, code = "UNKNOWN", "GN1_OWNER_START_ORIGINAL_INCOMPLETE"
    public["stage"] = marks[-1]["stage"]
    public["status"] = status
    public["code"] = code
    public.update(launch)
    public["private_originals_published"] = False
    public["Graph6_cases_accepted"] = 0
    public["one_graph_save_development_only"] = (
        status == "PASS_DEV_NATIVE_FIRST_SAVE_ONLY")
    public["formal_G2_granted"] = False

    # The public safe stage alone never establishes first private causal error.
    marks.append({"stage": "PRIVATE_SEAL"})
    new_json(private / "result.original.json",
             {"source": first, "status": status, "code": code,
              "marks": marks, "automatic_retry": False,
              "formal_acceptance": False, "Graph6_cases_accepted": 0,
              "private_capture_evaluated": False})
    archive, original_count, coverage = private_snapshot(root, preserve_partial=True)
    public["private_capture_complete"] = coverage["complete"]
    public["private_capture_issue_count"] = len(coverage["omitted_originals"])
    if not coverage["complete"]:
        status, code = "UNKNOWN", "GN1_PRIVATE_CAPTURE_INCOMPLETE"
        public["status"], public["code"] = status, code
    context = {"source_sha": first["source_sha"],
               "semwright_sha": first["sdk_sha"],
               "run_id": run_id, "run_attempt": "1"}
    sealed = custody.encrypt_snapshot(archive, recipient, context)
    new_bytes(transport / "gn1-private.mwseal", sealed,
              max_bytes=MAX_PRIVATE_BYTES + 2 * 1024 * 1024 + 8192)
    public["private_sealed"] = True
    public["sealed_original_count"] = original_count
    public["sealed_ciphertext_sha256"] = hashlib.sha256(sealed).hexdigest()
    public["recipient_spki_sha256"] = recipient["sha256"]
    new_json(transport / "gn1-public.json", public)

    # Never expose private owner logs, native envelope, paths or stdout/stderr.
    print(json.dumps(public, sort_keys=True, separators=(",", ":")))
    return 0 if status in ("PASS_DEV_NATIVE_STARTUP_ONLY",
                           "PASS_DEV_NATIVE_FIRST_SAVE_ONLY") else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "run"), required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "plan":
        result = {"schema": SCHEMA, "mode": "PLAN_ONLY_NO_EFFECTS",
                  "stages": STAGES, "real_driver_required": True,
                  "SDK_PIN_REQUIRED": True, "one_new_physical_store": True,
                  "automatic_retry": False, "R74_statuses_issued": 0,
                  "Graph6_cases_accepted": 0, "G2": "NOT_GRANTED",
                  "F": False, "G3": "STOP"}
        print(json.dumps(result, sort_keys=True))
        return 0
    try:
        return run_once(args)
    except BaseException:
        # An error before encrypted evidence is sealed cannot be advertised
        # as a usable Native test. Never leak the exception to Actions logs.
        print(json.dumps({"schema": SCHEMA, "status": "UNKNOWN",
                          "code": "GN1_UNSEALED_OR_UNADMITTED",
                          "Graph6_cases_accepted": 0,
                          "R74_statuses_issued": 0, "automatic_retry": False,
                          "G2": "NOT_GRANTED", "G3": "STOP"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
