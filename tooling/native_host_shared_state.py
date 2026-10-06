#!/usr/bin/env python3
"""Exact-SHA Motionwright UI-service <-> Semwright Native Host shared-state acceptance.

This test is intentionally CI-only. It launches the real Semwright daemon/Broker/
Driver Host against Motionwright's Native SDK provider and a separate app-state
actor that uses the same StudioService as the Tauri UI.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
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
MW_BINS = ROOT / "target" / "debug"
PROVIDER_SOURCE = MW_BINS / "motionwright-native-provider"
ACTOR = MW_BINS / "examples" / "shared-state-actor"
GITHUB_SHA = os.environ.get("GITHUB_SHA", "unknown")
EVIDENCE = ROOT / "verification" / "native-sdk" / "github-actions" / "shared-state" / GITHUB_SHA


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_json(argv: list[str], *, env: dict[str, str], timeout: int = 20) -> dict:
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
            f"stdout={result.stdout[:4096]!r}\nstderr={result.stderr[:4096]!r}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"command did not return JSON: {argv!r}\n"
            f"stdout={result.stdout[:4096]!r}\nstderr={result.stderr[:4096]!r}"
        ) from error


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def actor(env: dict[str, str], database: Path, *args: str) -> dict:
    return run_json([str(ACTOR), str(database), *args], env=env)


def require_exact_inputs() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("real Native Host shared-state acceptance is GitHub-Actions-only")
    if not shutil.which("bwrap"):
        raise SystemExit("bubblewrap is required; no trusted fallback is accepted")
    observed = subprocess.check_output(
        ["git", "-C", str(SEMWRIGHT), "rev-parse", "HEAD"], text=True
    ).strip()
    if observed != PIN:
        raise SystemExit(f"Semwright checkout mismatch: expected {PIN}, got {observed}")
    for path in [
        SW_BINS / "semwright",
        SW_BINS / "semwrightd",
        SW_BINS / "semwright-sandbox",
        PROVIDER_SOURCE,
        ACTOR,
    ]:
        if not path.is_file() or not os.access(path, os.X_OK):
            raise SystemExit(f"missing exact acceptance executable: {path}")


def main() -> None:
    require_exact_inputs()
    EVIDENCE.mkdir(parents=True, exist_ok=False)
    calls: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="motionwright-native-host-") as raw:
        root = Path(raw).resolve()
        root.chmod(0o700)
        paths: dict[str, Path] = {}
        for name in ["runtime", "state", "home", "config", "binary", "motionwright-data"]:
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
        )

        provider = paths["binary"] / "motionwright-native-provider"
        shutil.copyfile(PROVIDER_SOURCE, provider)
        provider.chmod(0o700)

        database = paths["motionwright-data"] / "motionwright.sqlite3"
        seeded = actor(env, database, "init", "UI seeded project")
        project_id = seeded["id"]
        resource = seeded["resource"]
        if seeded["revision"] != "0":
            raise AssertionError(f"new project did not start at revision zero: {seeded}")

        version = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))[
            "workspace"
        ]["package"]["version"]
        manifest = {
            "manifest_version": 1,
            "protocol": 4,
            "id": "motionwright",
            "version": version,
            "publisher": "motionwright-project",
            "executable": str(provider),
            "sha256": digest(provider),
            "application": {
                "desktop_id": None,
                "process_names": ["motionwright-native-provider"],
                "supported_versions": [],
            },
            "transport": "stdio_v1",
            "network": False,
            "mounts": [
                {
                    "root": "motionwright-data",
                    "read_only": False,
                    "execute": False,
                }
            ],
            "resources": {
                "open_files": 128,
                "processes": 16,
                "cpu_seconds": 90,
                "operation_cpu_seconds": 0,
                "address_space_bytes": 1_073_741_824,
                "file_size_bytes": 67_108_864,
            },
            "request_timeout_ms": 10_000,
            "interfaces": {
                "dynamic_capabilities": True,
                "cooperative_cancellation": True,
                "events": False,
                "progress": False,
                "artifacts": False,
                "health": True,
                "native_refs": True,
                "host_tools": False,
            },
        }
        manifest_path = paths["config"] / "motionwright-driver.json"
        write_json(manifest_path, manifest)

        config = (
            "drivers = [" + json.dumps(str(manifest_path)) + "]\n"
            "driver_network = false\n"
            "[policy]\n"
            'profile = "observe"\n'
            'allow = ["driver:motionwright"]\n'
            "\n[[policy.filesystem]]\n"
            'name = "motionwright-data"\n'
            "path = " + json.dumps(str(paths["motionwright-data"])) + "\n"
            "read = true\n"
            "write = true\n"
        )
        config_path = paths["config"] / "owner.toml"
        config_path.write_text(config, encoding="utf-8")
        config_path.chmod(0o600)

        socket = paths["runtime"] / "broker.sock"
        session = paths["runtime"] / "client.session"
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

        def invoke(command: str, args: dict, *, ok: bool = True) -> dict:
            result = subprocess.run(
                [
                    str(SW_BINS / "semwright"),
                    "--socket",
                    str(socket),
                    "--session-file",
                    str(session),
                    "--json",
                    "execute",
                    command,
                    "--args-json",
                    json.dumps(args, separators=(",", ":")),
                ],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
            try:
                value = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                raise AssertionError(
                    f"Semwright CLI returned non-JSON for {command}: "
                    f"{result.stdout[:4096]!r} {result.stderr[:4096]!r}"
                ) from error
            calls.append(
                {
                    "command": command,
                    "args": args,
                    "exit_code": result.returncode,
                    "envelope": value,
                }
            )
            if value.get("ok") is not ok or ((result.returncode == 0) is not ok):
                raise AssertionError(json.dumps(value, indent=2))
            if ok and command.startswith("driver.motionwright."):
                provenance = value["execution"]["provenance"]
                if provenance["provider"] != "driver:motionwright":
                    raise AssertionError(f"unexpected provider provenance: {provenance}")
                if provenance["source"] != "driver" or not provenance["descriptor_sha256"]:
                    raise AssertionError(f"incomplete provider provenance: {provenance}")
            return value

        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if daemon.poll() is not None:
                    log_stream.flush()
                    raise AssertionError(daemon_log.read_text(encoding="utf-8"))
                if socket.is_socket():
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("semwrightd did not expose its owner socket")

            observed = invoke(
                "driver.motionwright.observe",
                {"resource": resource, "scope": "summary", "limit": 8},
            )["data"]
            page = observed["page"]
            if page["version"]["generation"] != seeded["generation"]:
                raise AssertionError("Host observation changed application generation")
            if page["version"]["revision"] != seeded["revision"]:
                raise AssertionError("Host observation did not read the UI-seeded revision")
            if page["items"][0]["title"] != seeded["title"]:
                raise AssertionError("Host observation did not read the UI-seeded title")

            sdk_rename = invoke(
                "driver.motionwright.project.rename",
                {"ref": observed["ref"], "title": "SDK renamed project"},
            )["data"]
            after_sdk = actor(env, database, "show", project_id)
            if after_sdk["title"] != "SDK renamed project":
                raise AssertionError("UI-service actor did not observe Native SDK mutation")
            if after_sdk["revision"] != sdk_rename["revision"]:
                raise AssertionError("UI-service and SDK revisions diverged after SDK mutation")

            ui_rename = actor(
                env,
                database,
                "rename",
                project_id,
                "ui-shared-state-rename",
                "UI renamed after SDK",
            )
            if int(ui_rename["revision"]) <= int(after_sdk["revision"]):
                raise AssertionError("UI-service mutation did not advance shared revision")

            stale = invoke(
                "driver.motionwright.project.rename",
                {"ref": observed["ref"], "title": "must not commit from stale ref"},
                ok=False,
            )
            if stale.get("error", {}).get("code") != "StaleReference":
                raise AssertionError(f"stale Native SDK reference was not rejected: {stale}")
            unchanged = actor(env, database, "show", project_id)
            if unchanged["title"] != "UI renamed after SDK":
                raise AssertionError("stale SDK mutation altered app-owned state")

            fresh = invoke(
                "driver.motionwright.observe",
                {"resource": resource, "scope": "summary", "limit": 8},
            )["data"]
            if fresh["page"]["version"]["revision"] != ui_rename["revision"]:
                raise AssertionError("fresh Host observation missed UI-service revision")
            if fresh["page"]["items"][0]["title"] != "UI renamed after SDK":
                raise AssertionError("fresh Host observation missed UI-service mutation")

            sdk_final = invoke(
                "driver.motionwright.project.rename",
                {"ref": fresh["ref"], "title": "SDK final shared state"},
            )["data"]
            final = actor(env, database, "show", project_id)
            if final["title"] != "SDK final shared state":
                raise AssertionError("final SDK write was not visible to app-owned service")
            if final["revision"] != sdk_final["revision"]:
                raise AssertionError("final UI-service and Native SDK revisions differ")

            write_json(
                EVIDENCE / "result.json",
                {
                    "motionwright_sha": GITHUB_SHA,
                    "semwright_sha": PIN,
                    "provider_sha256": digest(provider),
                    "project": {
                        "id": project_id,
                        "resource": resource,
                        "generation": final["generation"],
                        "final_revision": final["revision"],
                        "final_title": final["title"],
                    },
                    "checks": {
                        "ui_seed_visible_through_host": True,
                        "sdk_write_visible_to_ui_service": True,
                        "ui_write_invalidates_stale_sdk_ref": True,
                        "fresh_sdk_observes_ui_write": True,
                        "final_sdk_write_visible_to_ui_service": True,
                    },
                },
            )
        finally:
            if daemon.poll() is None:
                pid = daemon.pid
                daemon.terminate()
                try:
                    daemon.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(pid, signal.SIGKILL)
                    daemon.wait(timeout=5)
            log_stream.close()
            write_json(EVIDENCE / "calls.json", calls)

    print(
        json.dumps(
            {
                "native_host_shared_state": "PASS",
                "motionwright_sha": GITHUB_SHA,
                "semwright_sha": PIN,
                "evidence": str(EVIDENCE.relative_to(ROOT)),
            }
        )
    )


if __name__ == "__main__":
    main()
