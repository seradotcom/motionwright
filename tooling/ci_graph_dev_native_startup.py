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
import signal
import stat
import subprocess
import sys
import tarfile
import time

SCHEMA = "motionwright.graph-native-dev-startup/1"
STAGES = ("SOURCE_BIND", "BUILD_INPUTS", "STORE_INIT", "PROFILE_PATHS",
          "BOOTSTRAP_SOCKET", "BOOTSTRAP_PROJECT", "BOOTSTRAP_CLOSE",
          "RESTRICTED_MANIFEST", "RESTRICTED_SOCKET", "PROVIDER_HANDSHAKE",
          "OWNER_CLOSE", "PRIVATE_SEAL")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
PROJECT = re.compile(r"prj_[0-9a-f]{32}\Z")
MAX_PRIVATE_BYTES = 32 * 1024 * 1024
MAX_PRIVATE_FILES = 256
MAX_PRIVATE_SINGLE = 8 * 1024 * 1024


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


def new_bytes(path: Path, data: bytes):
    need(len(data) <= MAX_PRIVATE_SINGLE and not path.exists(),
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


def signed_binaries(repo: Path, sdk: Path):
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


def _limits():
    import resource
    for key, bound in (("RLIMIT_CPU", 120), ("RLIMIT_FSIZE", 2 * 1024 * 1024),
                       ("RLIMIT_AS", 8 * 1024 * 1024 * 1024),
                       ("RLIMIT_NOFILE", 1024)):
        hard = getattr(resource, key)
        current, maximum = resource.getrlimit(hard)
        restricted = min(bound, maximum) if maximum != resource.RLIM_INFINITY else bound
        resource.setrlimit(hard, (restricted, maximum))


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
                env: dict, private: Path, extra_args=()):
    need(label in ("bootstrap", "restricted") and socket.is_absolute()
         and not socket.exists(), "GN1_DAEMON_SOCKET_SOURCE_OR_REPLAY")
    log = private / (label + ".daemon.original.log")
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    proc = None
    terminal = None
    try:
        with os.fdopen(fd, "wb") as out:
            proc = subprocess.Popen(
                [executable, "--config", str(config), "--socket", str(socket),
                 *extra_args],
                env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                start_new_session=True, preexec_fn=_limits,
            )
        new_json(private / (label + ".start.original.json"),
                 {"pid": proc.pid, "config_sha256":
                  hashlib.sha256(config.read_bytes()).hexdigest(),
                  "automatic_retry": False, "source_built": True})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
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
        if proc is not None:
            observed = _stop_group(proc, label, private)
            new_json(private / (label + ".terminal.original.json"),
                     {"pid": proc.pid, "exit_code": observed,
                      "process_group_closed": True, "automatic_retry": False,
                      "socket_was_ready": socket.is_socket()})


def _env(root: Path, runtime: Path, state: Path, home: Path, sdk: Path):
    clean = {key: value for key, value in os.environ.items()
             if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}}
    clean.update(HOME=str(home), XDG_RUNTIME_DIR=str(runtime),
                 XDG_STATE_HOME=str(state), SEMWRIGHT_CHECKOUT=str(sdk),
                 RUST_BACKTRACE="1", PYTHONNOUSERSITE="1")
    return clean


def actual_startup(repo: Path, sdk: Path, root: Path, first: dict,
                   private: Path, marks: list[dict]):
    # Graph's original source owns the manifest/config rules. GN1 does not
    # invent a new broker policy, grant or fake project identity.
    sys.path.insert(0, str(repo / "tooling"))
    import native_graph_browser_owner as graph
    import native_host_shared_state as shared
    binaries = signed_binaries(repo, sdk)
    new_json(private / "source-built-binaries.original.json", binaries)

    def mark(stage):
        need(stage in STAGES, "GN1_UNREVIEWED_STAGE")
        marks.append({"stage": stage, "elapsed_ms": time.monotonic_ns() // 1_000_000})
        new_json(private / ("%02d-" % len(marks) + stage + ".original.json"),
                 {"stage": stage, "source": first["source_sha"],
                  "automatic_retry": False})

    mark("BUILD_INPUTS")
    locations = {}
    for name in ("home", "state", "runtime", "config", "clients",
                 "motionwright-data", "manim-runtime", "manim-work"):
        locations[name] = mkdir(root, name)
    owner_env = _env(root, locations["runtime"], locations["state"],
                     locations["home"], sdk)
    store_path = locations["motionwright-data"] / "motionwright.sqlite3"

    mark("STORE_INIT")
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

    provider = binaries["native_provider"]
    manim_runner = binaries["manim_runner"]
    source_inputs = {"binaries": {
        "motionwright-native-provider": dict(provider),
        "motionwright-manim-runner": dict(manim_runner),
    }}
    python_bin = Path(sys.executable).resolve(strict=True)
    ffmpeg = Path("/usr/bin/ffmpeg").resolve(strict=True)
    need(ffmpeg.is_file(), "GN1_FFMPEG_RUNTIME_UNAVAILABLE")
    runtime = {
        "runtime": locations["manim-runtime"],
        "fontconfig": Path("/etc/fonts").resolve(strict=True),
        "python": file_identity(python_bin),
        "ffmpeg": file_identity(ffmpeg),
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
                     extra_args=capture_args):
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
    mark("OWNER_CLOSE")
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
    return {"real_store_original": original, "private_call_count": len(calls)}


def private_snapshot(root: Path):
    """Only owned originals, never sockets/symlinks, public unsealed bytes."""
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
    assert signed_binaries(repo, sdk)  # All original-build binaries must exist.

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
        actual_startup(repo, sdk, root, first, private, marks)
        status, code = "PASS_DEV_NATIVE_STARTUP_ONLY", "GN1_REAL_OWNER_READY_CLOSED"
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

    public["stage"] = marks[-1]["stage"]
    public["status"] = status
    public["code"] = code
    public["physical_owner_startup"] = (
        "REAL_OWNER_LAUNCHED_NOT_G2" if any(row["stage"] == "BOOTSTRAP_SOCKET"
                                           for row in marks)
        else "NOT_EXECUTED")
    public["actual_original_process_attempts"] = sum(
        row["stage"] in ("BOOTSTRAP_SOCKET", "RESTRICTED_SOCKET") for row in marks)
    public["private_originals_published"] = False
    public["Graph6_cases_accepted"] = 0

    # The public safe stage alone never establishes first private causal error.
    marks.append({"stage": "PRIVATE_SEAL"})
    new_json(private / "result.original.json",
             {"source": first, "status": status, "code": code,
              "marks": marks, "automatic_retry": False,
              "formal_acceptance": False, "Graph6_cases_accepted": 0})
    archive, original_count = private_snapshot(root)
    context = {"source_sha": first["source_sha"],
               "semwright_sha": first["sdk_sha"],
               "run_id": run_id, "run_attempt": "1"}
    sealed = custody.encrypt_snapshot(archive, recipient, context)
    new_bytes(transport / "gn1-private.mwseal", sealed)
    public["private_sealed"] = True
    public["sealed_original_count"] = original_count
    public["sealed_ciphertext_sha256"] = hashlib.sha256(sealed).hexdigest()
    public["recipient_spki_sha256"] = recipient["sha256"]
    new_json(transport / "gn1-public.json", public)

    # Never expose private owner logs, native envelope, paths or stdout/stderr.
    print(json.dumps(public, sort_keys=True, separators=(",", ":")))
    return 0 if status == "PASS_DEV_NATIVE_STARTUP_ONLY" else 1


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
