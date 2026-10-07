#!/usr/bin/env python3
"""Exact-SHA Motionwright -> Semwright -> Blender semantic round-trip acceptance.

CI-only by design. The test creates application-owned Motionwright state, provisions
Semwright's pinned Blender Driver through the real Broker/Driver Host sandbox,
realizes the bounded typed contribution, and independently parses the exported GLB.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import struct
import subprocess
import tempfile
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


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def run_json(argv: list[str], *, env: dict[str, str], timeout: int = 60) -> dict:
    result = subprocess.run(
        argv,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {argv!r}\n"
            f"stdout={result.stdout[:16384]!r}\nstderr={result.stderr[:16384]!r}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"command returned non-JSON: {argv!r}\n"
            f"stdout={result.stdout[:16384]!r}\nstderr={result.stderr[:16384]!r}"
        ) from error


def exact_inputs() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("native Blender E2E is GitHub-Actions-only")
    observed = subprocess.check_output(
        ["git", "-C", str(SEMWRIGHT), "rev-parse", "HEAD"], text=True
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
    first = subprocess.check_output([str(BLENDER_BIN), "--version"], text=True).splitlines()[0]
    if first != "Blender 4.5.14 LTS":
        raise SystemExit(f"unexpected Blender runtime: {first}")


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
    data = path.read_bytes()
    if len(data) <= 20:
        raise AssertionError("GLB is too small")
    magic, version, total = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or total != len(data):
        raise AssertionError("GLB header is invalid")
    chunk_length, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON":
        raise AssertionError("GLB first chunk is not JSON")
    end = 20 + chunk_length
    if end > len(data):
        raise AssertionError("GLB JSON chunk exceeds file bounds")
    model = json.loads(data[20:end].rstrip(b" \t\r\n\x00").decode("utf-8"))
    if str(model.get("asset", {}).get("version")) != "2.0":
        raise AssertionError("GLB asset version is not 2.0")
    if len(model.get("nodes", [])) < 2:
        raise AssertionError("GLB has fewer than two nodes")
    if len(model.get("meshes", [])) < 2:
        raise AssertionError("GLB has fewer than two meshes")
    if len(model.get("materials", [])) < 2:
        raise AssertionError("GLB has fewer than two materials")
    return model


def main() -> None:
    exact_inputs()
    EVIDENCE.mkdir(parents=True, exist_ok=False)

    with tempfile.TemporaryDirectory(prefix="motionwright-native-blender-") as raw:
        root = Path(raw).resolve()
        root.chmod(0o700)
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
            path = root / name
            path.mkdir(mode=0o700)
            paths[name] = path

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

        database = paths["motionwright-data"] / "motionwright.sqlite3"
        seeded = run_json([str(MW_BIN), "seed", str(database)], env=env)
        if seeded.get("mesh_count") != 2:
            raise AssertionError(f"unexpected Blender seed: {seeded}")

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

        socket = paths["runtime"] / "broker.sock"
        session = paths["runtime"] / "motionwright.session"
        daemon_log = EVIDENCE / "semwrightd.log"
        log_stream = daemon_log.open("wb")
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
            stdout=log_stream,
            stderr=log_stream,
            start_new_session=True,
        )

        try:
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
            required = {
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
            )
            if result.get("native_blender_e2e") != "PASS":
                raise AssertionError(f"Motionwright Blender realization did not pass: {result}")

            evidence = json.loads(
                (EVIDENCE / "motionwright-blender-evidence.json").read_text(encoding="utf-8")
            )
            artifact = evidence["result"]["artifact"]
            relative = artifact["path"]
            artifact_path = paths["workspace"] / relative
            if not artifact_path.is_file():
                raise AssertionError(f"verified GLB artifact is missing: {artifact_path}")
            observed_digest = digest(artifact_path)
            if observed_digest != artifact["sha256"]:
                raise AssertionError(
                    f"independent GLB digest mismatch: {observed_digest} != {artifact['sha256']}"
                )
            model = parse_glb(artifact_path)
            names = {
                item.get("name")
                for section in ("nodes", "meshes", "materials")
                for item in model.get(section, [])
                if isinstance(item, dict)
            }
            if not any(name and "Motionwright" in name for name in names):
                raise AssertionError("GLB readback lost Motionwright-authored semantic names")

            review_glb = EVIDENCE / "motionwright-contribution.glb"
            shutil.copyfile(artifact_path, review_glb)
            write_private_json(
                EVIDENCE / "result.json",
                {
                    "native_blender_e2e": "PASS",
                    "motionwright_sha": GITHUB_SHA,
                    "semwright_sha": PIN,
                    "semwright_version": semwright_version(),
                    "blender_version": "4.5.14 LTS",
                    "blender_sha256": digest(blender_bin),
                    "driver_sha256": digest(driver),
                    "session_runner_sha256": digest(session_runner),
                    "project": seeded,
                    "glb": {
                        "path": relative,
                        "sha256": observed_digest,
                        "bytes": artifact_path.stat().st_size,
                        "nodes": len(model.get("nodes", [])),
                        "meshes": len(model.get("meshes", [])),
                        "materials": len(model.get("materials", [])),
                    },
                    "capabilities_checked": sorted(required),
                },
            )
        finally:
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
            log_stream.close()

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
