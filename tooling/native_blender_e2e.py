#!/usr/bin/env python3
"""Exact-SHA Motionwright -> Semwright -> Blender semantic round-trip acceptance.

CI-only by design. The test creates application-owned Motionwright state, provisions
Semwright's pinned Blender Driver through the real Broker/Driver Host sandbox,
realizes the bounded typed contribution, and independently parses the exported GLB.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = json.loads((ROOT / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
PIN = SOURCE_LOCK["dependencies"]["semwright"]["revision"]
SEMWRIGHT = Path(os.environ.get("SEMWRIGHT_CHECKOUT", ROOT / "_semwright")).resolve()
SW_BINS = SEMWRIGHT / "target" / "debug"
MW_BIN = ROOT / "target" / "debug" / "examples" / "native-blender-e2e"
BLENDER_ROOT = Path(
    os.environ.get("SEMWRIGHT_TEST_BLENDER_ROOT", "/usr/local/blender-4.5.14-linux-x64")
).resolve()
BLENDER_BIN = BLENDER_ROOT / "blender"
GITHUB_SHA = os.environ.get("GITHUB_SHA", "unknown")
EVIDENCE = ROOT / "verification" / "native-blender-e2e" / GITHUB_SHA

STATIC_STAGE_BYTES = 64 * 1024 * 1024
STATIC_DATABASE_BYTES = 32 * 1024 * 1024
STATIC_SIDECAR_BYTES = 16 * 1024 * 1024
STATIC_STREAM_BYTES = 4 * 1024 * 1024
STATIC_JSON_BYTES = 8 * 1024 * 1024


def static_original_budget():
    # Installed only by the source-pinned workflow after reading the immutable
    # Runner.Worker clock proof. Absence is denial, never a new local deadline.
    budget = globals().get("_STATIC_ORIGINAL_JOB_BUDGET")
    if budget is None:
        raise AssertionError("static original job deadline is required before native effects")
    return budget


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def write_static_json(path: Path, value: object) -> None:
    """Bound the complete derived JSON before creating its private file."""
    body = bytearray()
    for part in json.JSONEncoder(indent=2, sort_keys=True).iterencode(value):
        raw = part.encode("utf-8")
        if len(body) + len(raw) + 1 > STATIC_JSON_BYTES:
            raise ValueError("static private JSON exceeds its admitted byte budget")
        body.extend(raw)
    body.extend(b"\n")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(body)
        output.flush()
        os.fsync(output.fileno())


def start_static_daemon_log(daemon, log_stream):
    """One owned Blender broker pipe; no native dispatch or retry is added."""
    stop = threading.Event()
    state = {"observed_pipe_bytes": 0, "overflow": False, "eof_observed": False,
             "errors": [], "byte_budget": STATIC_STREAM_BYTES}

    def terminate_owned():
        try:
            if daemon.poll() is None:
                os.killpg(daemon.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as error:
            state["errors"].append("terminate:" + type(error).__name__)

    def drain():
        selector = selectors.DefaultSelector()
        try:
            descriptor = daemon.stdout.fileno()
            os.set_blocking(descriptor, False)
            selector.register(descriptor, selectors.EVENT_READ)
            while not stop.is_set():
                if not selector.select(0.05):
                    continue
                # The one byte that proves overflow is itself retained. No
                # already-written original is truncated and no larger read is
                # performed whose tail could be silently discarded.
                remaining = STATIC_STREAM_BYTES + 1 - state["observed_pipe_bytes"]
                try:
                    data = os.read(descriptor, min(65536, remaining))
                except BlockingIOError:
                    continue
                if not data:
                    state["eof_observed"] = True
                    break
                state["observed_pipe_bytes"] += len(data)
                log_stream.write(data)
                log_stream.flush()
                if state["observed_pipe_bytes"] > STATIC_STREAM_BYTES:
                    state["overflow"] = True
                    terminate_owned()
                    break
        except BaseException as error:
            state["errors"].append("capture:" + type(error).__name__)
            terminate_owned()
        finally:
            try:
                selector.close()
                daemon.stdout.close()
            except BaseException as error:
                state["errors"].append("pipe-close:" + type(error).__name__)

    thread = threading.Thread(target=drain, name="static-blender-broker-log", daemon=True)
    thread.start()
    return {"thread": thread, "stop": stop, "state": state}


def finish_static_daemon_log(capture, daemon, log_stream, log_path, evidence):
    # The existing context has already closed/waited for its owned daemon.
    capture["thread"].join(timeout=2)
    if capture["thread"].is_alive():
        capture["stop"].set()
        capture["thread"].join(timeout=2)
    state = dict(capture["state"])
    state["thread_terminal_observed"] = not capture["thread"].is_alive()
    log_stream.flush()
    os.fsync(log_stream.fileno())
    state["retained_file_bytes"] = log_path.stat().st_size
    state["retained_file_sha256"] = digest(log_path)
    state["owned_daemon_exit_code"] = daemon.returncode
    state["capture_complete"] = (state["thread_terminal_observed"] and state["eof_observed"]
                                 and not state["overflow"] and not state["errors"]
                                 and state["retained_file_bytes"] == state["observed_pipe_bytes"])
    state["native_operation_result"] = ("NO_NATIVE_ACCEPTANCE_FROM_LOG" if state["capture_complete"]
                                        else "UNKNOWN_NO_RETRY")
    state["automatic_retry"] = False
    state["whole_process_tree_completion_claimed"] = False
    write_static_json(evidence / "semwrightd-capture.json", state)
    if not state["capture_complete"]:
        raise AssertionError("static Blender daemon log is incomplete; UNKNOWN_NO_RETRY")


def static_terminal_originals(label: str, database: Path, glb: Path | None,
                             result: subprocess.CompletedProcess) -> None:
    """Copy closed-process bytes without opening SQLite or executing a native app."""
    if label not in {"seed", "realize"}:
        raise AssertionError("unexpected static Blender custody stage")
    destination = EVIDENCE / ("static-" + label + "-originals")
    destination.mkdir(mode=0o700, exist_ok=False)
    receipt = {"schema": "motionwright.blender.static-terminal-originals/1",
               "stage": label, "native_process_returncode": result.returncode,
               "native_process_terminal_observed": True,
               "stage_limit_bytes": STATIC_STAGE_BYTES, "files": [],
               "absent_optional_files": [], "custody_errors": [],
               "sqlite_reopen_or_native_retry_executed": False}
    total = 0

    def keep(name: str, source: Path | None, data: bytes | None, limit: int):
        nonlocal total
        target = destination / name
        source_fd = None
        try:
            if source is not None:
                try:
                    # Reject a substituted FIFO at fstat without waiting for a
                    # writer. This never opens SQLite or retries a native call.
                    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                except FileNotFoundError:
                    if name != "motionwright.sqlite3":
                        receipt["absent_optional_files"].append(name)
                        return
                    raise
                before = os.fstat(source_fd)
                if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                    raise AssertionError("source is not a bounded regular file")
                size = before.st_size
            else:
                size = len(data)
            if size > limit or total + size > STATIC_STAGE_BYTES:
                raise AssertionError("static original exceeds its byte budget")
            total += size
            target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            actual = 0
            hasher = hashlib.sha256()
            with os.fdopen(target_fd, "wb") as output:
                if source_fd is None:
                    output.write(data)
                    actual = len(data)
                    hasher.update(data)
                else:
                    while True:
                        chunk = os.read(source_fd, min(1024 * 1024, size - actual + 1))
                        if not chunk:
                            break
                        if actual + len(chunk) > size:
                            raise AssertionError("source grew during byte preservation")
                        output.write(chunk)
                        hasher.update(chunk)
                        actual += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if actual != size:
                raise AssertionError("source shrank during byte preservation")
            if source_fd is not None:
                after = os.fstat(source_fd)
                current = source.lstat()
                identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
                if identity(before) != identity(after) or identity(after) != identity(current):
                    raise AssertionError("source identity changed during preservation")
            sha256 = hasher.hexdigest()
            if digest(target) != sha256:
                raise AssertionError("preserved original readback differs")
            receipt["files"].append({"name": name, "bytes": size, "sha256": sha256,
                                     "copy_readback_verified": True})
        except Exception as error:
            receipt["custody_errors"].append({"name": name, "error": type(error).__name__,
                                               "detail": str(error)})
        finally:
            if source_fd is not None:
                os.close(source_fd)

    keep("stdout.original", None, result.stdout, STATIC_STREAM_BYTES)
    keep("stderr.original", None, result.stderr, STATIC_STREAM_BYTES)
    keep("motionwright.sqlite3", database, None, STATIC_DATABASE_BYTES)
    for suffix in ("-wal", "-shm", "-journal"):
        keep("motionwright.sqlite3" + suffix, Path(str(database) + suffix), None,
             1024 * 1024 if suffix == "-shm" else STATIC_SIDECAR_BYTES)
    if glb is not None:
        keep("motionwright-contribution.glb", glb, None, 8 * 1024 * 1024)
    receipt["copied_bytes"] = sum(row["bytes"] for row in receipt["files"])
    receipt["custody_complete"] = not receipt["custody_errors"]
    write_static_json(destination / "receipt.json", receipt)
    if receipt["custody_errors"]:
        raise AssertionError("static Blender original custody is incomplete")


@contextmanager
def retained_static_owner():
    # Do not remove the sole owner tree on parse/oracle/custody failure or an
    # operation without an observed terminal result. It contains no cleanup
    # authorization; selected source copies, not session tickets, are evidence.
    root = Path(tempfile.mkdtemp(prefix="motionwright-native-blender-")).resolve()
    root.chmod(0o700)
    try:
        yield root
    finally:
        primary = sys.exc_info()[1]
        try:
            write_static_json(EVIDENCE / "static-owner-retention.private.json", {
                "schema": "motionwright.blender.static-owner-retention/1",
                "owner_original_path": str(root), "owner_removed": False,
                "native_retry_executed": False,
                "retention_on_disposable_runner_is_not_durable_archive": True,
            })
        except BaseException as retention_error:
            if primary is None:
                raise
            try:
                primary.static_owner_retention_error = retention_error
            except BaseException:
                pass


def run_json(argv: list[str], *, env: dict[str, str], timeout: int = 60,
             static_capture: tuple[str, Path, Path | None] | None = None) -> dict:
    if env.get("MOTIONWRIGHT_BLENDER_SCOPE") == "static-glb":
        budget = static_original_budget()
        timeout = (budget.realize(timeout) if static_capture is not None and static_capture[0] == "realize"
                   else budget.setup(timeout))
    try:
        result = subprocess.run(
            argv,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        if static_capture is not None:
            try:
                label = static_capture[0]
                if label not in {"seed", "realize"}:
                    raise AssertionError("unexpected static Blender custody stage")
                destination = EVIDENCE / ("static-" + label + "-unconfirmed")
                destination.mkdir(mode=0o700, exist_ok=False)
                rows = []
                for name, data in (("stdout.partial.original", error.stdout),
                                   ("stderr.partial.original", error.stderr)):
                    if data is None:
                        continue
                    if type(data) is not bytes or len(data) > STATIC_STREAM_BYTES:
                        raise AssertionError("unconfirmed stream exceeds custody profile")
                    path = destination / name
                    with path.open("xb") as output:
                        output.write(data)
                    path.chmod(0o600)
                    rows.append({"name": name, "bytes": len(data), "sha256": digest(path)})
                write_static_json(destination / "receipt.json", {
                    "stage": label, "native_operation_result": "UNKNOWN",
                    "native_process_terminal_observed": False,
                    "streams_are_partial": True, "files": rows,
                    "sqlite_reopen_or_native_retry_executed": False,
                })
            except Exception as custody_error:
                error.native_custody_error = custody_error
        raise
    custody_error = None
    if static_capture is not None:
        try:
            static_terminal_originals(*static_capture, result)
        except Exception as error:
            custody_error = error
    if result.returncode != 0:
        error = AssertionError(
            f"command failed ({result.returncode}): {argv!r}\n"
            f"stdout={result.stdout[:16384]!r}\nstderr={result.stderr[:16384]!r}"
        )
        if custody_error is not None:
            error.native_custody_error = custody_error
        raise error
    if custody_error is not None:
        raise custody_error
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"command returned non-JSON: {argv!r}\n"
            f"stdout={result.stdout[:16384]!r}\nstderr={result.stderr[:16384]!r}"
        ) from error


def exact_inputs(*, bounded_static: bool = False) -> dict:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("native Blender E2E is GitHub-Actions-only")
    observed = subprocess.check_output(
        ["git", "-C", str(SEMWRIGHT), "rev-parse", "HEAD"], text=True,
        **({"timeout": static_original_budget().setup(30)} if bounded_static else {}),
    ).strip()
    if observed != PIN:
        raise SystemExit(f"Semwright checkout mismatch: expected {PIN}, got {observed}")
    for path in [
        SW_BINS / "semwright",
        SW_BINS / "semwrightd",
        SW_BINS / "semwright-sandbox",
        SW_BINS / "semwright-blender-driver",
        SW_BINS / "semwright-blender-session-runner",
        MW_BIN,
        BLENDER_BIN,
    ]:
        if not path.exists():
            raise SystemExit(f"missing exact acceptance input: {path}")
    if not shutil.which("bwrap"):
        raise SystemExit("bubblewrap is required; no unsandboxed fallback is accepted")
    version_output = subprocess.check_output(
        [str(BLENDER_BIN), "--version"], text=True,
        **({"timeout": static_original_budget().setup(30)} if bounded_static else {}),
    )
    first = version_output.splitlines()[0]
    if first != "Blender 4.5.14 LTS":
        raise SystemExit(f"unexpected Blender runtime: {first}")
    return {"semwright_revision": observed, "blender_version_output": version_output,
            "blender_version": first}


def semwright_version() -> str:
    with (SEMWRIGHT / "Cargo.toml").open("rb") as stream:
        return tomllib.load(stream)["workspace"]["package"]["version"]


def wait_for_socket(process: subprocess.Popen[bytes], socket: Path, log: Path) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(log.read_text(encoding="utf-8", errors="replace"))
        if socket.is_socket():
            return
        time.sleep(0.05)
    raise AssertionError("semwrightd did not expose the owner socket")


def parse_glb(path: Path) -> dict:
    # This is structural readback of the embedded static mesh container.
    # Source geometry, native editability and Host authority need separate gates.
    maximum = 8 * 1024 * 1024
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if not 28 <= len(data) <= maximum:
        raise AssertionError("GLB byte count is outside the static mesh profile")
    magic, version, total = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or total != len(data):
        raise AssertionError("GLB header is invalid")
    chunks: list[tuple[bytes, bytes]] = []
    offset = 12
    while offset < len(data):
        if offset + 8 > len(data):
            raise AssertionError("GLB chunk header is truncated")
        length, kind = struct.unpack_from("<I4s", data, offset)
        offset += 8
        end = offset + length
        if length == 0 or length % 4 != 0 or end > len(data):
            raise AssertionError("GLB chunk length is invalid")
        chunks.append((kind, data[offset:end]))
        offset = end
    if len(chunks) != 2 or [kind for kind, _ in chunks] != [b"JSON", b"BIN\x00"]:
        raise AssertionError("GLB requires one JSON and one embedded BIN chunk")

    def unique_object(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise AssertionError("GLB JSON contains a duplicate object key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise AssertionError(f"GLB JSON contains a nonfinite number: {value}")

    try:
        model = json.loads(chunks[0][1].decode("utf-8"),
                           object_pairs_hook=unique_object,
                           parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssertionError("GLB JSON is invalid") from error
    if (type(model) is not dict or type(model.get("asset")) is not dict
            or model["asset"].get("version") != "2.0"):
        raise AssertionError("GLB asset version is not 2.0")
    buffers = model.get("buffers")
    if (type(buffers) is not list or len(buffers) != 1
            or type(buffers[0]) is not dict or "uri" in buffers[0]):
        raise AssertionError("GLB requires one embedded buffer without a URI")
    logical = buffers[0].get("byteLength")
    binary = chunks[1][1]
    if type(logical) is not int or logical <= 0 or not 0 <= len(binary) - logical <= 3:
        raise AssertionError("GLB embedded buffer length differs from BIN")
    if any(binary[logical:]):
        raise AssertionError("GLB BIN padding must contain zero bytes")
    for section in ("nodes", "meshes", "materials"):
        rows = model.get(section)
        if type(rows) is not list or len(rows) < 2 or not all(type(row) is dict for row in rows):
            raise AssertionError(f"GLB has fewer than two valid {section}")
    return model


def new_owner_paths(root: Path, *, runtime_path: Path | None = None) -> dict[str, Path]:
    runtime_path = root / "runtime" if runtime_path is None else runtime_path
    if (not runtime_path.is_absolute()
            or runtime_path.parent.resolve(strict=True) != runtime_path.parent):
        raise AssertionError("Blender runtime parent must be canonical and absolute")
    paths: dict[str, Path] = {}
    for name in [
        "runtime",
        "state",
        "home",
        "config",
        "bin",
        "workspace",
        "scratch",
        "motionwright-data",
    ]:
        path = runtime_path if name == "runtime" else root / name
        path.mkdir(mode=0o700, exist_ok=False)
        paths[name] = path

    return paths


def owned_environment(paths: dict[str, Path]) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}
    }
    env.update(
        HOME=str(paths["home"]),
        XDG_RUNTIME_DIR=str(paths["runtime"]),
        XDG_STATE_HOME=str(paths["state"]),
        RUST_BACKTRACE="1",
        SEMWRIGHT_TEST_DRIVER_SANDBOX="1",
        SEMWRIGHT_TEST_SANDBOX_HELPER=str(SW_BINS / "semwright-sandbox"),
        SEMWRIGHT_TEST_BLENDER_ROOT=str(BLENDER_ROOT),
    )

    return env


def broker_socket_path(runtime: Path) -> Path:
    path = runtime / "broker.sock"
    if not path.is_absolute() or "\0" in str(path):
        raise AssertionError("Blender broker socket must be an absolute non-NUL path")
    if sys.platform == "linux" and len(str(path).encode("utf-8")) > 107:
        raise AssertionError("Blender broker socket exceeds 107 UTF-8 bytes")
    return path


@contextmanager
def blender_context(paths: dict[str, Path], env: dict[str, str], evidence: Path,
                    seeded: dict, *, required: set[str] | None = None,
                    bounded_static: bool = False):
    socket = broker_socket_path(paths["runtime"])
    if socket.exists() or socket.is_symlink():
        raise FileExistsError("Existing Blender broker socket path cannot be adopted")
    driver = paths["bin"] / "semwright-blender-driver"
    shutil.copyfile(SW_BINS / "semwright-blender-driver", driver)
    driver.chmod(0o700)
    session_runner = (SW_BINS / "semwright-blender-session-runner").resolve()
    blender_bin = BLENDER_BIN.resolve()
    font_config = Path("/etc/fonts").resolve()

    manifest = {
        "manifest_version": 1,
        "protocol": 8,
        "id": "blender",
        "version": semwright_version(),
        "publisher": "semwright",
        "executable": str(driver),
        "sha256": digest(driver),
        "application": {
            "desktop_id": "org.blender.Blender",
            "process_names": ["blender"],
            "supported_versions": ["4.5.14"],
        },
        "transport": "stdio_v1",
        "mounts": [
            {"root": "workspace", "read_only": False},
            {"root": "blender-runtime", "read_only": True},
            {"root": "scratch", "read_only": False},
            {"root": "font-config", "read_only": True},
        ],
        "system_config": [],
        "secrets": [],
        "tools": [
            {
                "root": "blender-session-runner",
                "name": "blender-session-runner",
                "sha256": digest(session_runner),
                "mounts": ["workspace", "blender-runtime", "scratch", "font-config"],
                "dependencies": ["blender"],
            },
            {
                "root": "blender-executable",
                "name": "blender",
                "sha256": digest(blender_bin),
                "mounts": [],
                "dependencies": [],
            },
        ],
        "network": False,
        "loopback_port": None,
        "resources": {
            "open_files": 256,
            "processes": 64,
            "cpu_seconds": 300,
            "operation_cpu_seconds": 0,
            "address_space_bytes": 4_294_967_296,
            "file_size_bytes": 1_073_741_824,
        },
        "request_timeout_ms": 300_000,
        "interfaces": {
            "dynamic_capabilities": False,
            "cooperative_cancellation": True,
            "events": False,
            "health": True,
            "progress": True,
            "artifacts": False,
            "native_refs": False,
            "host_tools": True,
        },
    }
    manifest_path = paths["config"] / "blender-driver.json"
    write_private_json(manifest_path, manifest)

    grants = [
        ("workspace", paths["workspace"], True),
        ("font-config", font_config, False),
        ("blender-runtime", BLENDER_ROOT, False),
        ("scratch", paths["scratch"], True),
        ("blender-session-runner", session_runner, False),
        ("blender-executable", blender_bin, False),
    ]
    config_lines = [
        f"drivers = [{json.dumps(str(manifest_path))}]",
        "driver_network = false",
        "",
        "[policy]",
        'profile = "workspace"',
        'allow = ["driver:blender"]',
    ]
    for name, path, write in grants:
        config_lines.extend(
            [
                "",
                "[[policy.filesystem]]",
                f"name = {json.dumps(name)}",
                f"path = {json.dumps(str(path.resolve()))}",
                "read = true",
                f"write = {'true' if write else 'false'}",
            ]
        )
    config_path = paths["config"] / "owner.toml"
    config_path.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    config_path.chmod(0o600)

    session = paths["runtime"] / "motionwright.session"
    daemon_log = evidence / "semwrightd.log"
    log_stream = daemon_log.open("xb")
    daemon_log.chmod(0o600)
    daemon = None
    static_log = None
    state = {"cleanup_errors": []}
    try:
        if bounded_static:
            static_original_budget().setup(45)
        daemon = subprocess.Popen(
            [
                str(SW_BINS / "semwrightd"),
                "--config",
                str(config_path),
                "--socket",
                str(socket),
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if bounded_static else log_stream,
            stderr=subprocess.STDOUT if bounded_static else log_stream,
            start_new_session=True,
        )
        if bounded_static:
            static_log = start_static_daemon_log(daemon, log_stream)
        wait_for_socket(daemon, socket, daemon_log)
        capabilities: list[dict] = []
        offset = 0
        catalog_revision = None
        while True:
            discovered = run_json(
                [
                    str(SW_BINS / "semwright"),
                    "--socket",
                    str(socket),
                    "--session-file",
                    str(session),
                    "--json",
                    "capabilities",
                    "search",
                    "",
                    "--provider",
                    "driver:blender",
                    "--limit",
                    "100",
                    "--offset",
                    str(offset),
                ],
                env=env,
            )
            data = discovered.get("data", {})
            revision = data.get("revision")
            if catalog_revision is None:
                catalog_revision = revision
            elif revision != catalog_revision:
                raise AssertionError("Blender capability catalog changed during discovery")
            page = data.get("capabilities", [])
            if not isinstance(page, list):
                raise AssertionError("Blender capability discovery returned a malformed page")
            capabilities.extend(page)
            next_offset = data.get("next_offset")
            if next_offset is None:
                break
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise AssertionError("Blender capability discovery returned an invalid cursor")
            offset = next_offset
        ids = {item.get("id") for item in capabilities}
        required = required if required is not None else {
            "driver.blender.collection.create",
            "driver.blender.object.create",
            "driver.blender.object.transform",
            "driver.blender.collection.link",
            "driver.blender.material.create",
            "driver.blender.material.assign",
            "driver.blender.export.glb",
        }
        if not required <= ids:
            raise AssertionError(
                f"pinned Blender provider is missing Motionwright capabilities: {sorted(required - ids)}"
            )
        if not session.is_file() or (session.stat().st_mode & 0o777) != 0o600:
            raise AssertionError("Semwright did not create a private persistent session ticket")

        connection_path = paths["config"] / "motionwright-connection.json"
        write_private_json(
            connection_path,
            {
                "schema": "motionwright-semwright-connection/1",
                "executable": str((SW_BINS / "semwright").resolve()),
                "executable_sha256": digest(SW_BINS / "semwright"),
                "socket": str(socket),
                "session_file": str(session),
                "output_root": str(paths["workspace"]),
                "resource": seeded["resource"],
            },
        )

        state.update({
            "daemon": daemon, "connection": connection_path, "paths": paths,
            "capabilities_checked": sorted(required),
            "runtime_bindings": {
                "blender_sha256": digest(blender_bin),
                "driver_sha256": digest(driver),
                "session_runner_sha256": digest(session_runner),
                "semwright_sha256": digest(SW_BINS / "semwright"),
                "semwrightd_sha256": digest(SW_BINS / "semwrightd"),
            },
        })
        yield state
    finally:
        original = sys.exc_info()[1]
        failures = state["cleanup_errors"]
        if daemon is not None:
            try:
                if daemon.poll() is None:
                    try:
                        os.killpg(daemon.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        daemon.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(daemon.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        daemon.wait(timeout=5)
                state["daemon_returncode"] = daemon.returncode
            except BaseException as error:
                failures.append("daemon:" + type(error).__name__ + ":" + str(error))
        if static_log is not None:
            try:
                finish_static_daemon_log(static_log, daemon, log_stream, daemon_log, evidence)
            except BaseException as error:
                failures.append("static-log:" + type(error).__name__ + ":" + str(error))
        for operation, action in (("flush", log_stream.flush), ("close", log_stream.close)):
            try:
                action()
            except BaseException as error:
                failures.append("log:" + operation + ":" + type(error).__name__ + ":" + str(error))
        if failures:
            if original is not None:
                try:
                    original.native_context_cleanup = list(failures)
                except BaseException:
                    pass
            else:
                raise AssertionError("native Blender context cleanup was incomplete")

def static_glb_source_nodes(seeded: dict, evidence: dict) -> list[dict]:
    """Bind independently saved/loaded raw CanvasNodes before GLB admission."""
    from native_blender_glb_content import canvas_source_nodes
    for key in ("project_id", "resource", "generation", "revision", "scene_id"):
        if (key not in seeded or key not in evidence
                or type(seeded[key]) is not type(evidence[key])
                or seeded[key] != evidence[key]):
            raise AssertionError("Blender original source identity changed: " + key)
    if (type(seeded["revision"]) is not int or seeded["revision"] < 0
            or any(type(seeded[key]) is not str or not seeded[key]
                   for key in ("project_id", "resource", "generation", "scene_id"))):
        raise AssertionError("Blender original source identity is malformed")
    if ("source_nodes" not in seeded or "source_nodes" not in evidence
            or seeded["source_nodes"] != evidence["source_nodes"]):
        raise AssertionError("Blender original CanvasNode body changed")
    source = canvas_source_nodes(seeded["source_nodes"])
    objects = evidence.get("result", {}).get("objects")
    if (type(seeded.get("mesh_count")) is not int or seeded["mesh_count"] != len(source)
            or type(objects) is not list or len(objects) != len(source)
            or any(type(row) is not dict or type(row.get("node_id")) is not str
                   for row in objects)
            or {row["node_id"] for row in objects} != {row["node_id"] for row in source}):
        raise AssertionError("Blender realized nodes differ from the original source")
    return source


def check_static_glb_content(path: Path, artifact: dict, seeded: dict, evidence: dict) -> dict:
    from native_blender_glb_content import MAX_GLB_BYTES, check_glb_source_geometry
    source = static_glb_source_nodes(seeded, evidence)
    with path.open("rb") as stream:
        original = stream.read(MAX_GLB_BYTES + 1)
    content = check_glb_source_geometry(
        original, original_GLB_SHA256=artifact["sha256"], source_nodes=source
    )
    if not content["source_Project_UUIDs_explicitly_checked"]:
        raise AssertionError("Blender GLB does not retain every complete source UUID")
    return content


def main() -> None:
    mode = os.environ.get("MOTIONWRIGHT_BLENDER_SCOPE", "static-glb")
    if mode == "original-scene":
        from native_blender_original_evidence import run_original
        run_original(sys.modules[__name__])
        return
    if mode == "product-scene":
        from native_blender_original_evidence import run_product
        run_product(sys.modules[__name__])
        return
    if mode != "static-glb":
        raise SystemExit("unsupported native Blender verification scope")
    static_original_budget().setup(30)
    exact_inputs(bounded_static=True)
    EVIDENCE.mkdir(parents=True, exist_ok=False)

    with retained_static_owner() as raw:
        root = Path(raw).resolve()
        root.chmod(0o700)
        paths = new_owner_paths(root)
        env = owned_environment(paths)
        database = paths["motionwright-data"] / "motionwright.sqlite3"
        seeded = run_json([str(MW_BIN), "seed", str(database)], env=env,
                          static_capture=("seed", database, None))
        if seeded.get("mesh_count") != 2:
            raise AssertionError(f"unexpected Blender seed: {seeded}")
        from native_blender_glb_content import canvas_source_nodes
        canvas_source_nodes(seeded.get("source_nodes"))
        write_static_json(EVIDENCE / "motionwright-blender-source.original.json", seeded)

        with blender_context(paths, env, EVIDENCE, seeded, bounded_static=True) as context:
            connection_path = context["connection"]
            driver = paths["bin"] / "semwright-blender-driver"
            blender_bin = BLENDER_BIN.resolve()
            session_runner = (SW_BINS / "semwright-blender-session-runner").resolve()
            required = context["capabilities_checked"]
            result = run_json(
                [
                    str(MW_BIN),
                    "realize",
                    str(database),
                    str(connection_path),
                    str(EVIDENCE / "motionwright-blender-evidence.json"),
                ],
                env=env,
                timeout=420,
                static_capture=("realize", database, paths["workspace"] /
                                ("motionwright-blender-scene-" +
                                 seeded["scene_id"].replace("-", "") + ".glb")),
            )
            if result.get("native_blender_e2e") != "PASS":
                raise AssertionError(f"Motionwright Blender realization did not pass: {result}")

            evidence = json.loads(
                (EVIDENCE / "motionwright-blender-evidence.json").read_text(encoding="utf-8")
            )
            artifact = evidence["result"]["artifact"]
            relative = artifact["path"]
            expected_name = ("motionwright-blender-scene-" +
                             seeded["scene_id"].replace("-", "") + ".glb")
            if relative != expected_name:
                raise AssertionError("GLB path differs from the selected source scene")
            artifact_path = paths["workspace"] / relative
            if not artifact_path.is_file():
                raise AssertionError(f"verified GLB artifact is missing: {artifact_path}")
            observed_digest = digest(artifact_path)
            if observed_digest != artifact["sha256"]:
                raise AssertionError(
                    f"independent GLB digest mismatch: {observed_digest} != {artifact['sha256']}"
                )
            # Preserve even a rejected original before parsing or admitting it.
            review_glb = EVIDENCE / "motionwright-contribution.glb"
            captured_glb = EVIDENCE / "static-realize-originals" / "motionwright-contribution.glb"
            with captured_glb.open("rb") as source:
                original_bytes = source.read(8 * 1024 * 1024 + 1)
            if len(original_bytes) > 8 * 1024 * 1024:
                raise AssertionError("preserved GLB exceeds the static mesh profile")
            with review_glb.open("xb") as target:
                target.write(original_bytes)
            review_glb.chmod(0o600)
            if digest(review_glb) != observed_digest:
                raise AssertionError("preserved GLB digest differs from the original")
            model = parse_glb(review_glb)
            content = check_static_glb_content(review_glb, artifact, seeded, evidence)
            write_static_json(EVIDENCE / "motionwright-blender-glb-content.json", content)
            names = {
                item.get("name")
                for section in ("nodes", "meshes", "materials")
                for item in model.get(section, [])
                if isinstance(item, dict)
            }
            if not any(name and "Motionwright" in name for name in names):
                raise AssertionError("GLB readback lost Motionwright-authored semantic names")

            public_result = {
                    "native_blender_e2e": "PASS",
                    "motionwright_sha": GITHUB_SHA,
                    "semwright_sha": PIN,
                    "semwright_version": semwright_version(),
                    "blender_version": "4.5.14 LTS",
                    "blender_sha256": digest(blender_bin),
                    "driver_sha256": digest(driver),
                    "session_runner_sha256": digest(session_runner),
                    "project": {key: value for key, value in seeded.items() if key != "source_nodes"},
                    "glb": {
                        "path": relative,
                        "sha256": observed_digest,
                        "bytes": artifact_path.stat().st_size,
                        "nodes": len(model.get("nodes", [])),
                        "meshes": len(model.get("meshes", [])),
                        "materials": len(model.get("materials", [])),
                    },
                    "capabilities_checked": sorted(required),
                }

    write_static_json(EVIDENCE / "result.json", public_result)
    print(
        json.dumps(
            {
                "native_blender_e2e": "PASS",
                "motionwright_sha": GITHUB_SHA,
                "semwright_sha": PIN,
                "evidence": str(EVIDENCE),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
