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
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
from native_av_failure_guard import run_sanitized_json

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
    return run_sanitized_json(argv, env=env, timeout=timeout)


def checked_private_cli_payload(
    stdout: bytes, stderr: bytes, exit_code: int, expected_ok: bool | None,
) -> dict:
    """Validate native CLI outcomes without printing private streams or args.

    The shared-host test intentionally observes both successful and failed
    typed commands (including CAS losers). An expected native rejection is
    not a Python command failure. All errors here are fixed, bounded strings.
    """
    if len(stdout) > 256_000 or len(stderr) > 256_000:
        raise AssertionError("Native shared-host CLI output exceeded bounded receipt limits")
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise AssertionError("Native shared-host CLI returned malformed JSON; streams suppressed") from None
    if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
        raise AssertionError("Native shared-host CLI returned an invalid typed envelope")
    actual_ok = payload["ok"]
    if type(exit_code) is not int or (exit_code == 0) is not actual_ok:
        raise AssertionError("Native shared-host CLI envelope disagrees with command exit status")
    if expected_ok is not None and actual_ok is not expected_ok:
        raise AssertionError("Native shared-host CLI did not produce the expected typed outcome")
    return payload


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def actor(env: dict[str, str], database: Path, *args: str) -> dict:
    return run_json([str(ACTOR), str(database), *args], env=env)


def stored_content(database: Path) -> tuple:
    """Compare this real SQLite Store in memory, including receipts and events.

    SQLite may checkpoint WAL bytes during ordinary connection lifetime changes;
    compare its complete persisted SQL content and physical file identity instead.
    """
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        sql = hashlib.sha256("\n".join(connection.iterdump()).encode("utf-8")).hexdigest()
    info = database.stat()
    return info.st_dev, info.st_ino, sql


def owner_config(data_root: Path, *, manifest: Path | None = None,
                 selections: list[tuple[str, str]] | None = None) -> str:
    """Select existing Core Graph projects in trusted owner configuration only."""
    allow = ["project.manage"] if manifest is None else [
        "driver:motionwright", "providers.execute_bound",
    ]
    config = (
        "drivers = " + json.dumps([] if manifest is None else [str(manifest)]) + "\n"
        "driver_network = false\n"
        "[policy]\n"
        'profile = "observe"\n'
        "allow = " + json.dumps(allow) + "\n"
        "\n[[policy.filesystem]]\n"
        'name = "motionwright-data"\n'
        "path = " + json.dumps(str(data_root)) + "\n"
        "read = true\n"
        "write = true\n"
    )
    if manifest is not None:
        config += '\n[[policy.native_resource_reads]]\nprovider = "driver:motionwright"\n'
        for resource, project in selections or []:
            if (not project.startswith("prj_") or len(project) != 36
                    or any(char not in "0123456789abcdef" for char in project[4:])):
                raise AssertionError("Core did not issue a canonical Graph project identity")
            config += (
                "\n[[policy.native_resource_reads.resources]]\n"
                "resource = " + json.dumps(resource) + "\n"
                'root = "motionwright-data"\n'
                "project = " + json.dumps(project) + "\n"
            )
    return config


def driver_startup_stderr_args(owner: Path, label: str, *, selected: bool) -> list[str]:
    """Select one private SDK observer; never grant or retry a Native operation."""
    if not selected:
        return []
    if sys.platform != "linux" or label not in {
            "shared-state", "authorized", "reopened", "withdrawn", "restored"}:
        raise AssertionError("PRIVATE_STARTUP_STDERR_SELECTION")
    owner = Path(owner)
    if (not owner.is_absolute() or not owner.is_dir()
            or owner.resolve(strict=True) != owner
            or any(path.is_symlink() for path in (owner, *owner.parents))
            or owner.stat().st_uid != os.getuid() or owner.stat().st_mode & 0o777 != 0o700):
        raise AssertionError("PRIVATE_STARTUP_STDERR_OWNER")
    parent = owner / "startup-stderr"
    if parent.exists() or parent.is_symlink():
        if (parent.is_symlink() or not parent.is_dir() or parent.resolve(strict=True) != parent
                or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o777 != 0o700):
            raise AssertionError("PRIVATE_STARTUP_STDERR_PARENT")
    else:
        parent.mkdir(mode=0o700)
    directory = parent / label
    # SDK requires an existing empty private directory. A prior attempt is not
    # reused, cleared, or overwritten; the enclosing owner is sealed in place.
    directory.mkdir(mode=0o700)
    return ["--driver-startup-stderr-dir", str(directory),
            "--driver-startup-stderr-provider", "driver:motionwright"]


@contextmanager
def running_daemon(config: Path, socket: Path, env: dict[str, str], log: Path):
    """Run the real daemon; no fake backend or sandbox fallback is selected."""
    with log.open("wb") as stream:
        log.chmod(0o600)
        daemon = subprocess.Popen(
            [str(SW_BINS / "semwrightd"), "--config", str(config), "--socket", str(socket)],
            env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if daemon.poll() is not None:
                    raise AssertionError(
                        "Native shared-host Broker exited before owner socket startup; log retained privately"
                    )
                if socket.is_socket():
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("semwrightd did not expose its owner socket")
            yield daemon
        finally:
            if daemon.poll() is None:
                daemon.terminate()
                try:
                    daemon.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(daemon.pid, signal.SIGKILL)
                    daemon.wait(timeout=5)


def invoke_cli(env: dict[str, str], socket: Path, session: Path, command: str,
               args: dict, calls: list[dict], *, ok: bool | None = True,
               evidence_root: Path | None = None) -> dict:
    """Retain the original outer envelope before validating an expected outcome."""
    receipt = uuid.uuid4().hex  # Custody filename only; never a principal or grant.
    evidence = EVIDENCE if evidence_root is None else evidence_root
    entry = {"command": command, "args": args, "session": session.name,
             "receipt": receipt, "automatic_retry": False}
    try:
        result = subprocess.run(
            [str(SW_BINS / "semwright"), "--socket", str(socket),
             "--session-file", str(session), "--json", "execute", command,
             "--args-json", json.dumps(args, separators=(",", ":"))],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        entry["status"] = "UNKNOWN" if isinstance(error, subprocess.TimeoutExpired) else "NOT_RUN"
        calls.append(entry)
        for name in ("stdout", "stderr"):
            raw = getattr(error, name, None)
            if isinstance(raw, bytes):
                path = evidence / f"{receipt}.{name}.original"
                path.write_bytes(raw)
                path.chmod(0o600)
        raise AssertionError(
            "Native shared-host CLI unavailable or timed out; no effect is retried and custody is private"
        ) from None
    for name, raw in (("stdout", result.stdout), ("stderr", result.stderr)):
        path = evidence / f"{receipt}.{name}.original"
        path.write_bytes(raw)
        path.chmod(0o600)
    entry["exit_code"] = result.returncode
    calls.append(entry)
    value = checked_private_cli_payload(result.stdout, result.stderr, result.returncode, None)
    entry["envelope"] = value
    if ok is not None and value["ok"] is not ok:
        raise AssertionError("Native shared-host CLI did not produce the expected typed outcome")
    return entry


def checked_native_result(outer: dict, binding: str) -> dict:
    """Expose the unchanged child for assertions while custody retains both layers."""
    if not outer["ok"]:
        # Core forwards the original child typed error; never rebuild or retry it.
        return outer
    wrapper = outer.get("data")
    if not isinstance(wrapper, dict) or wrapper.get("binding_ref") != binding:
        raise AssertionError("Native shared-host result lost its original mount binding")
    nested = wrapper.get("result")
    if not isinstance(nested, dict) or nested.get("ok") is not True:
        raise AssertionError("Native shared-host result omitted its original successful child")
    execution = nested.get("execution")
    provenance = execution.get("provenance") if isinstance(execution, dict) else None
    if (not isinstance(provenance, dict)
            or provenance.get("provider") != "driver:motionwright"
            or provenance.get("source") != "driver"
            or not isinstance(provenance.get("descriptor_sha256"), str)
            or not provenance["descriptor_sha256"]):
        raise AssertionError("Native shared-host CLI reported incomplete child provider provenance")
    return nested


def require_known_denial(value: dict, code: str, message: str) -> None:
    error = value.get("error")
    if value.get("ok") is not False or not isinstance(error, dict) or error.get("code") != code:
        raise AssertionError(message)
    if error.get("outcome_known") is not True:
        raise AssertionError(
            "Native shared-host retained UNKNOWN; the known-rejection oracle is not established"
        )


def retain_owner_audit(state: Path, label: str, *, evidence_root: Path | None = None) -> None:
    """Keep private child/parent audit correlation before disposable state disappears."""
    audit = state / "audit"
    evidence = EVIDENCE if evidence_root is None else evidence_root
    if not audit.is_dir():
        write_json(evidence / f"{label}.json", {"status": "NOT_RUN", "audit_present": False})
        return
    if audit.is_symlink() or audit.resolve() != audit:
        raise AssertionError("Native shared-host audit escaped the isolated owner state")
    files = []
    for source in audit.rglob("*"):
        if source.is_symlink() or not (source.is_file() or source.is_dir()):
            raise AssertionError("Native shared-host audit contains a nonregular entry")
        if source.is_file():
            files.append((source.relative_to(audit), digest(source)))
    destination = evidence / label
    shutil.copytree(audit, destination)
    destination.chmod(0o700)
    for relative, expected in files:
        copied = destination / relative
        copied.chmod(0o600)
        if digest(copied) != expected:
            raise AssertionError("Native shared-host audit custody changed original bytes")
    write_json(evidence / f"{label}.json", {
        "status": "PASS", "scope": "original audit byte custody only",
        "files": [{"path": str(relative), "sha256": expected} for relative, expected in files],
    })


def require_exact_inputs() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("real Native Host shared-state acceptance is GitHub-Actions-only")
    if not shutil.which("bwrap"):
        raise SystemExit("bubblewrap is required; no trusted fallback is accepted")
    observed = subprocess.check_output(
        ["git", "-C", str(SEMWRIGHT), "rev-parse", "HEAD"], text=True
    ).strip()
    if observed != PIN:
        raise SystemExit("Pinned Semwright source checkout did not match the source lock")
    for path in [
        SW_BINS / "semwright",
        SW_BINS / "semwrightd",
        SW_BINS / "semwright-sandbox",
        PROVIDER_SOURCE,
        ACTOR,
    ]:
        if not path.is_file() or not os.access(path, os.X_OK):
            raise SystemExit("Pinned native shared-host acceptance executable is unavailable")


def main(*, retained_owner: Path | None = None) -> None:
    require_exact_inputs()
    EVIDENCE.mkdir(mode=0o700, parents=True, exist_ok=False)
    calls: list[dict] = []
    transport_calls: list[dict] = []

    workspace = (nullcontext(str(retained_owner)) if retained_owner is not None else
                 tempfile.TemporaryDirectory(prefix="motionwright-native-host-"))
    with workspace as raw:
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
        effective_paths = run_json(
            [str(SW_BINS / "semwright"), "--json", "config", "paths"], env=env,
        )
        owner_state = Path(effective_paths["state"]).resolve()
        if not owner_state.is_relative_to(paths["state"]):
            raise AssertionError("Native shared-host daemon state is outside the isolated owner profile")
        write_json(EVIDENCE / "owner-paths.original.json", effective_paths)

        provider = paths["binary"] / "motionwright-native-provider"
        shutil.copyfile(PROVIDER_SOURCE, provider)
        provider.chmod(0o700)

        database = paths["motionwright-data"] / "motionwright.sqlite3"
        seeded = actor(env, database, "init", "UI seeded project")
        project_id = seeded["id"]
        resource = seeded["resource"]
        if seeded["revision"] != "0":
            raise AssertionError("New shared-state project was not at revision zero")
        # Both resources are genuinely selected so the existing crossed-reference
        # oracle tests reference isolation, independently of default-deny policy.
        other = actor(env, database, "init", "Independent project")

        version = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))[
            "workspace"
        ]["package"]["version"]
        manifest = {
            "manifest_version": 1,
            "protocol": 9,
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
                "dynamic_capabilities": False,
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
        write_json(EVIDENCE / "motionwright-driver.original.json", manifest)

        # The real owner daemon creates canonical Graph projects. A native UUID
        # or fixture document never creates a prj_ identity or ProjectAccess.
        # Restart with the SAME private state and an explicitly restricted policy.
        bootstrap_config = paths["config"] / "owner-bootstrap.toml"
        bootstrap_text = owner_config(paths["motionwright-data"])
        for path in (bootstrap_config, EVIDENCE / "owner-bootstrap.original.toml"):
            path.write_text(bootstrap_text, encoding="utf-8")
            path.chmod(0o600)
        bootstrap_socket = paths["runtime"] / "bootstrap.sock"
        selections: list[tuple[str, str]] = []
        try:
            with running_daemon(bootstrap_config, bootstrap_socket, env,
                                EVIDENCE / "semwrightd-bootstrap.log"):
                for selected_resource in (resource, other["resource"]):
                    created = invoke_cli(
                        env, bootstrap_socket, paths["runtime"] / "owner-bootstrap.session",
                        "project.create", {"root": "motionwright-data"}, transport_calls,
                    )["envelope"]
                    canonical_project = created.get("data", {}).get("project")
                    if not isinstance(canonical_project, str):
                        raise AssertionError("Owner project.create did not return its real Graph identity")
                    selections.append((selected_resource, canonical_project))
        finally:
            write_json(EVIDENCE / "calls.json", calls)
            write_json(EVIDENCE / "transport-calls.original.json", transport_calls)
            retain_owner_audit(owner_state, "owner-bootstrap-audit.original")
        if selections[0][1] == selections[1][1]:
            raise AssertionError("Independent resources did not receive distinct canonical Graph projects")
        config = owner_config(paths["motionwright-data"], manifest=manifest_path,
                              selections=selections)
        config_path = paths["config"] / "owner.toml"
        for path in (config_path, EVIDENCE / "owner-restricted.original.toml"):
            path.write_text(config, encoding="utf-8")
            path.chmod(0o600)

        socket = paths["runtime"] / "broker.sock"
        session = paths["runtime"] / "client.session"
        bindings: dict[Path, str] = {}
        daemon_log = EVIDENCE / "semwrightd.log"
        log_stream = daemon_log.open("wb")
        daemon_log.chmod(0o600)
        daemon = subprocess.Popen(
            [
                str(SW_BINS / "semwrightd"),
                "--config",
                str(config_path),
                "--socket",
                str(socket),
                *driver_startup_stderr_args(root, "shared-state", selected=retained_owner is not None),
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=log_stream,
            start_new_session=True,
        )

        def invoke(
            command: str,
            args: dict,
            *,
            ok: bool | None = True,
            session_path: Path | None = None,
        ) -> dict:
            selected_session = session_path or session
            if selected_session not in bindings:
                mount = invoke_cli(
                    env, socket, selected_session, "providers.mount.observe",
                    {"provider": "driver:motionwright", "root": "motionwright-data"}, transport_calls,
                )["envelope"]["data"]
                binding = mount.get("binding_ref")
                if (not isinstance(binding, str) or not binding
                        or mount.get("mount", {}).get("root") != "motionwright-data"
                        or mount.get("mount", {}).get("write") is not True):
                    raise AssertionError("Native shared-host did not receive its real writable mount")
                bindings[selected_session] = binding
            binding = bindings[selected_session]
            receipt = invoke_cli(
                env, socket, selected_session, "providers.execute_bound",
                {"binding_ref": binding,
                 "request": {"command": command, "args": args, "dry_run": False}},
                transport_calls, ok=ok,
            )
            outer = receipt["envelope"]
            # Preserve the public curator's exact five-key native-operation shape.
            # Each retains its ORIGINAL outer envelope; bootstrap,
            # mounts and physical wrapper calls also have separate private custody.
            calls.append({"command": command, "args": args, "session": selected_session.name,
                          "exit_code": receipt["exit_code"], "envelope": outer})
            return checked_native_result(outer, binding)

        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if daemon.poll() is not None:
                    log_stream.flush()
                    raise AssertionError(
                        "Native shared-host Broker exited before owner socket startup; raw daemon log suppressed"
                    )
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
            require_known_denial(stale, "StaleReference", "Stale Native SDK reference was not rejected")
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

            forged_actor = invoke(
                "driver.motionwright.project.rename",
                {
                    "ref": fresh["ref"],
                    "title": "forged actor must not commit",
                    "actor": "human",
                },
                ok=False,
            )
            require_known_denial(
                forged_actor, "InvalidArgument",
                "Caller-supplied actor field was not rejected by the typed contract",
            )
            if actor(env, database, "show", project_id)["title"] != "UI renamed after SDK":
                raise AssertionError("forged actor payload altered application state")

            cross_project = invoke(
                "driver.motionwright.observe",
                {
                    "ref": fresh["ref"],
                    "resource": other["resource"],
                    "scope": "summary",
                    "limit": 8,
                },
                ok=False,
            )
            require_known_denial(
                cross_project, "PolicyDenied", "Cross-project native reference was incorrectly accepted",
            )
            if actor(env, database, "show", other["id"])["title"] != "Independent project":
                raise AssertionError("cross-project reference changed the independent project")

            human_session = paths["runtime"] / "human.session"
            agent_session = paths["runtime"] / "agent.session"
            human_view = invoke(
                "driver.motionwright.observe",
                {"resource": resource, "scope": "summary", "limit": 8},
                session_path=human_session,
            )["data"]
            agent_view = invoke(
                "driver.motionwright.observe",
                {"resource": resource, "scope": "summary", "limit": 8},
                session_path=agent_session,
            )["data"]
            human_revision = human_view["page"]["version"]["revision"]
            agent_revision = agent_view["page"]["version"]["revision"]
            if human_revision != agent_revision:
                raise AssertionError("independent clients did not start from the same revision")

            # Exercise the new read through the actual Broker/Host twice, using
            # each client's own observed ref. The CAS race below then continues
            # from these SAME refs: preview must neither commit nor stale them.
            before_preview = stored_content(database)
            human_preview = invoke(
                "driver.motionwright.project.preview",
                {"ref": human_view["ref"],
                 "edit": {"operation": "project.rename", "title": "Uncommitted preview title"}},
                session_path=human_session,
            )["data"]
            agent_preview = invoke(
                "driver.motionwright.project.preview",
                {"ref": agent_view["ref"],
                 "edit": {"operation": "scene.add", "name": "Uncommitted preview scene",
                          "objective": "Read-only Host preview", "duration_seconds": 3}},
                session_path=agent_session,
            )["data"]
            for preview in (human_preview, agent_preview):
                if (preview.get("schema") != "motionwright.change-preview/1"
                        or preview.get("status") != "READ_ONLY_DOMAIN_VALIDATION"
                        or preview.get("resource") != resource
                        or preview.get("generation") != seeded["generation"]
                        or preview.get("revision") != human_revision
                        or preview.get("committed") is not False
                        or preview.get("exact_future_ids_proven") is not False
                        or type(preview.get("candidate_scene_count")) is not int):
                    raise AssertionError("Native preview lost its read-only observed-project contract")
            if (human_preview.get("candidate_title") != "Uncommitted preview title"
                    or agent_preview.get("candidate_title") != ui_rename["title"]
                    or agent_preview["candidate_scene_count"] != human_preview["candidate_scene_count"] + 1):
                raise AssertionError("Native preview did not evaluate the requested uncommitted edit")

            redirected_preview = invoke(
                "driver.motionwright.project.preview",
                {"ref": agent_view["ref"], "resource": other["resource"],
                 "edit": {"operation": "project.rename", "title": "must not cross resources"}},
                ok=False,
                session_path=agent_session,
            )
            require_known_denial(
                redirected_preview, "InvalidArgument",
                "Native preview did not retain its typed payload-override error",
            )
            # Only a known typed denial can continue into another native call.
            # Store equality cannot resolve or promote an UNKNOWN outcome.
            if stored_content(database) != before_preview:
                raise AssertionError("Native previews changed physical Store identity or persisted SQLite rows")
            if actor(env, database, "show", project_id) != ui_rename:
                raise AssertionError("Separate UI-service reopen observed a preview mutation")

            with ThreadPoolExecutor(max_workers=2) as pool:
                human_future = pool.submit(
                    invoke,
                    "driver.motionwright.project.rename",
                    {"ref": human_view["ref"], "title": "Human contender"},
                    ok=None,
                    session_path=human_session,
                )
                agent_future = pool.submit(
                    invoke,
                    "driver.motionwright.project.rename",
                    {"ref": agent_view["ref"], "title": "Agent contender"},
                    ok=None,
                    session_path=agent_session,
                )
                contenders = [human_future.result(), agent_future.result()]

            winners = [value for value in contenders if value.get("ok") is True]
            losers = [value for value in contenders if value.get("ok") is False]
            if len(winners) != 1 or len(losers) != 1:
                raise AssertionError(
                    "Concurrent native CAS did not return exactly one winner and one loser"
                )
            require_known_denial(
                losers[0], "StaleReference", "Concurrent losing native CAS was not rejected as stale",
            )
            after_race = actor(env, database, "show", project_id)
            winner_title = winners[0]["data"]["project"]["title"]
            if after_race["title"] != winner_title:
                raise AssertionError("shared state does not match the winning concurrent mutation")
            if int(after_race["revision"]) != int(human_revision) + 1:
                raise AssertionError("concurrent CAS advanced the shared revision more than once")

            before_stale_preview = stored_content(database)
            stale_preview = invoke(
                "driver.motionwright.project.preview",
                {"ref": agent_view["ref"],
                 "edit": {"operation": "project.rename", "title": "stale preview must be rejected"}},
                ok=False,
                session_path=agent_session,
            )
            require_known_denial(
                stale_preview, "StaleReference",
                "Native preview did not retain its typed stale-reference error",
            )
            if stored_content(database) != before_stale_preview:
                raise AssertionError("Rejected stale preview changed the physical Store")

            stale_after_race = invoke(
                "driver.motionwright.project.rename",
                {"ref": fresh["ref"], "title": "old default-session ref must stay stale"},
                ok=False,
            )
            require_known_denial(
                stale_after_race, "StaleReference",
                "pre-race reference remained writable after concurrent edit",
            )

            post_race = invoke(
                "driver.motionwright.observe",
                {"resource": resource, "scope": "summary", "limit": 8},
            )["data"]
            if post_race["page"]["version"]["revision"] != after_race["revision"]:
                raise AssertionError("post-race observation missed the winning revision")

            sdk_final = invoke(
                "driver.motionwright.project.rename",
                {"ref": post_race["ref"], "title": "SDK final shared state"},
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
                        "forged_actor_field_rejected": True,
                        "cross_project_ref_rejected": True,
                        "two_sessions_share_one_base": True,
                        "two_sessions_preview_through_real_host": True,
                        "preview_preserves_store_identity_rows_receipts_and_events": True,
                        "preview_payload_resource_override_error_retained": True,
                        "preview_refs_continue_into_real_cas": True,
                        "stale_preview_error_retained": True,
                        "concurrent_cas_has_exactly_one_winner": True,
                        "concurrent_loser_is_stale": True,
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
            write_json(EVIDENCE / "transport-calls.original.json", transport_calls)
            retain_owner_audit(owner_state, "owner-final-audit.original")

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


# This explicit mode has separate custody and never extends the shared-state
# public curator or its 17-call roster. It performs no render or native edit.
CAPSULE_FIXTURES = {
    "authored.glb": (1972, "a7b485ccb2afc368cf0ea2421d2df098f39ed5e10d632dcc7733bd76ba4f69b1"),
    "edited.glb": (1992, "61955fda30b99a883f8cd5908b8fc7f9e1dee1168a3460e005c3baf1b753a55c"),
}
CAPSULE_STAGES = ("seed", "bootstrap", "authorized-edit", "reopen", "withdrawn", "restored")
CAPSULE_LOCKS = ("Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml", "apps/studio/package-lock.json")


def capsule_require(value: bool, code: str) -> None:
    if not value:
        raise AssertionError(code)


def capsule_source_identity() -> dict:
    def git(base: Path, *args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(base), *args], stderr=subprocess.PIPE, timeout=20)
    capsule_require(re.fullmatch(r"[0-9a-f]{40}", GITHUB_SHA) is not None, "E11_SOURCE_SHA")
    capsule_require(os.environ.get("MW_NATIVE_EXPECTED_SOURCE_SHA") == GITHUB_SHA, "E11_EXPLICIT_SOURCE_ADMISSION")
    capsule_require(git(ROOT, "rev-parse", "HEAD").decode().strip() == GITHUB_SHA, "E11_SOURCE_HEAD")
    capsule_require(not git(ROOT, "status", "--porcelain", "--untracked-files=all"), "E11_SOURCE_NOT_CLEAN")
    capsule_require(git(SEMWRIGHT, "rev-parse", "HEAD").decode().strip() == PIN, "E11_SDK_HEAD")
    capsule_require(not git(SEMWRIGHT, "status", "--porcelain", "--untracked-files=all"), "E11_SDK_NOT_CLEAN")
    locks = {}
    for relative in CAPSULE_LOCKS:
        raw = (ROOT / relative).read_bytes()
        capsule_require(raw == git(ROOT, "show", f"{GITHUB_SHA}:{relative}"), "E11_LOCK_CHANGED")
        locks[relative] = hashlib.sha256(raw).hexdigest()
    revisions = re.findall(rb'source = "git\+https://github\.com/seradotcom/semwright[^"\n]*#([0-9a-f]{40})"',
                           (ROOT / "Cargo.lock").read_bytes())
    capsule_require(bool(revisions) and all(value.decode() == PIN for value in revisions), "E11_LOCK_SDK_MISMATCH")
    return {"motionwright_sha": GITHUB_SHA, "semwright_sha": PIN, "locks": locks,
            "locks_sha256": hashlib.sha256(json.dumps(locks, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def capsule_stop_group(process) -> bool:
    # Only groups started by this mode with start_new_session=True. A failed
    # closure remains UNKNOWN; it cannot authorize a reopen or a second call.
    if process is None:
        return True
    for sig, seconds in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            process.wait(timeout=5)
            return True
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                process.wait(timeout=5)
                return True
            time.sleep(0.05)
    return False


def capsule_pid_identity(pid: int):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return {"pid": pid, "parent": int(fields[1]), "start_ticks": int(fields[19]), "state": fields[0]}


def capsule_track_descendants(admitted: dict) -> None:
    tracked = admitted.get("active_daemon")
    if tracked is None:
        return
    current = capsule_pid_identity(tracked["process"].pid)
    capsule_require(current is not None and current["start_ticks"] == tracked["identity"]["start_ticks"],
                    "E11_DAEMON_IDENTITY_NOT_LIVE")
    entries = [path for path in Path("/proc").iterdir() if path.name.isdecimal()]
    capsule_require(len(entries) <= 4096, "E11_PROCESS_INVENTORY_BUDGET")
    table = {int(path.name): capsule_pid_identity(int(path.name)) for path in entries}
    descendants = {current["pid"]}
    while True:
        added = {pid for pid, value in table.items() if value is not None and value["parent"] in descendants}
        expanded = descendants | added
        if expanded == descendants:
            break
        descendants = expanded
    for pid in descendants:
        value = table.get(pid)
        if value is not None and pid != current["pid"]:
            tracked["descendants"][(pid, value["start_ticks"])] = value


def capsule_stop_descendants(tracked: dict) -> bool:
    # Linux pidfds bind the observed process, so a recycled numeric PID cannot
    # redirect cleanup. Only actual descendants of our original daemon qualify.
    def live(identity):
        current = capsule_pid_identity(identity["pid"])
        return current is not None and current["start_ticks"] == identity["start_ticks"] and current["state"] != "Z"
    for sig, seconds in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        remaining = [value for value in tracked["descendants"].values() if live(value)]
        if not remaining:
            return True
        for identity in remaining:
            try:
                fd = os.pidfd_open(identity["pid"])
            except ProcessLookupError:
                continue
            try:
                if live(identity):
                    signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                pass
            finally:
                os.close(fd)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not any(live(value) for value in remaining):
                return True
            time.sleep(0.05)
    return False


def capsule_process(admitted: dict, label: str, argv: list[str], env: dict[str, str]) -> dict:
    custody, evidence = admitted["custody"], admitted["evidence"]
    custody.new_json(evidence / f"{label}.started.json", {
        "argv": argv, "source": admitted["source"], "automatic_retry": False, "started_ns": time.time_ns(),
    })
    process, tracked, failure, quiescent = None, None, None, False
    stdout_path, stderr_path = (evidence / f"{label}.{name}.original" for name in ("stdout", "stderr"))
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            stdout_path.chmod(0o600)
            stderr_path.chmod(0o600)
            process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=stdout,
                                       stderr=stderr, start_new_session=True)
            tracked = {"process": process, "quiescent": False, "descendants": {}}
            admitted["processes"].append(tracked)
            custody.new_json(evidence / f"{label}.launch.original.json", {"pid": process.pid})
            process.wait(timeout=20)
    except BaseException as error:
        failure = type(error).__name__
    finally:
        try:
            quiescent = capsule_stop_group(process)
        except BaseException as error:
            failure = type(error).__name__
        if tracked is not None:
            tracked["quiescent"] = quiescent
        code = None if process is None else process.poll()
        custody.new_json(evidence / f"{label}.exit.original.json", {
            "exit_code": code, "failure_type": failure, "process_group_quiescent": quiescent,
            "automatic_retry": False, "effects_absent_asserted": False, "finished_ns": time.time_ns(),
        })
    if failure is not None or code != 0 or not quiescent:
        admitted["unknown"] = True
        raise AssertionError("E11_OWNER_PROCESS_NOT_COMPLETED_OR_CLOSED")
    stdout = custody.regular(stdout_path, 256_000)[0]
    custody.regular(stderr_path, 256_000)
    try:
        value = custody.strict(stdout)
    except Exception:
        admitted["unknown"] = True
        raise AssertionError("E11_OWNER_PROCESS_RESULT_MALFORMED") from None
    return value


@contextmanager
def capsule_stage(admitted: dict, name: str):
    custody, evidence = admitted["custody"], admitted["evidence"]
    capsule_require(name == CAPSULE_STAGES[len(admitted["completed_stages"])], "E11_STAGE_ORDER")
    custody.new_json(evidence / f"{name}.started.json", {
        "source": admitted["source"], "stage": name, "automatic_retry": False,
        "started_ns": time.time_ns(), "effects_absent_asserted": False,
    })
    try:
        yield
    except BaseException as error:
        admitted["failed"] = True
        custody.new_json(evidence / f"{name}.terminal.json", {
            "stage": name, "status": "UNKNOWN" if admitted["unknown"] else "FAIL",
            "exception_type": type(error).__name__, "error": str(error), "automatic_retry": False,
            "effects_absent_asserted": False, "source": admitted["source"],
        })
        raise
    else:
        admitted["completed_stages"].append(name)
        custody.new_json(evidence / f"{name}.terminal.json", {
            "stage": name, "status": "COMPLETED", "source": admitted["source"], "automatic_retry": False,
        })


@contextmanager
def capsule_daemon(admitted: dict, label: str, config: Path, env: dict[str, str]):
    custody, evidence, owner = admitted["custody"], admitted["evidence"], admitted["owner"]
    socket = owner / "runtime" / f"{label}.sock"
    log = evidence / f"{label}.daemon.log.original"
    process, tracked, failure, quiescent = None, None, None, False
    with log.open("xb") as stream:
        log.chmod(0o600)
        try:
            process = subprocess.Popen([str(SW_BINS / "semwrightd"), "--config", str(config),
                                        "--socket", str(socket),
                                        *driver_startup_stderr_args(owner, label, selected=label != "bootstrap")],
                                       env=env, stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=stream, start_new_session=True)
            tracked = {"process": process, "quiescent": False, "descendants": {}, "identity": None}
            admitted["processes"].append(tracked)
            admitted["active_daemon"] = tracked
            tracked["identity"] = capsule_pid_identity(process.pid)
            capsule_require(tracked["identity"] is not None, "E11_DAEMON_START_IDENTITY")
            custody.new_json(evidence / f"{label}.daemon.launch.original.json", {
                "pid": process.pid, "config_sha256": digest(config), "source": admitted["source"],
            })
            deadline = time.monotonic() + 20
            while not socket.is_socket():
                capsule_require(process.poll() is None and time.monotonic() < deadline, "E11_OWNER_SOCKET_UNAVAILABLE")
                time.sleep(0.02)
            capsule_track_descendants(admitted)
            yield socket
        except BaseException as error:
            failure = {"exception_type": type(error).__name__, "error": str(error)}
            raise
        finally:
            inventory_known = True
            try:
                capsule_track_descendants(admitted)
            except BaseException:
                inventory_known = False
            try:
                group_closed = capsule_stop_group(process)
                descendants_closed = tracked is not None and capsule_stop_descendants(tracked)
                quiescent = inventory_known and group_closed and descendants_closed
            except BaseException:
                pass
            admitted["active_daemon"] = None
            admitted["shutdown"].append(quiescent)
            if tracked is not None:
                tracked["quiescent"] = quiescent
            if not quiescent:
                admitted["unknown"] = True
            custody.new_json(evidence / f"{label}.daemon.exit.original.json", {
                "pid": None if process is None else process.pid,
                "exit_code": None if process is None else process.poll(),
                "process_group_quiescent": quiescent, "automatic_retry": False,
                "broker_revoke_session_claimed": False,
                "original_failure": failure,
                "descendant_inventory_known": inventory_known,
                "observed_descendants": [] if tracked is None else list(tracked["descendants"].values()),
            })
            capsule_require(quiescent, "E11_DAEMON_GROUP_CLOSURE_UNKNOWN")


def capsule_cli(admitted: dict, env: dict[str, str], socket: Path, session: Path,
                command: str, args: dict, *, ok: bool = True) -> dict:
    try:
        entry = invoke_cli(env, socket, session, command, args, admitted["transport_calls"],
                           ok=None, evidence_root=admitted["evidence"])
    except BaseException:
        # A malformed/timeout original is never converted to an expected denial.
        admitted["unknown"] = True
        raise
    capsule_track_descendants(admitted)
    value = entry["envelope"]
    if value.get("ok") is False:
        error = value.get("error")
        if not isinstance(error, dict) or error.get("outcome_known") is not True:
            admitted["unknown"] = True
            raise AssertionError("E11_NATIVE_OUTCOME_UNKNOWN_RETAINED")
    capsule_require(value.get("ok") is ok, "E11_UNEXPECTED_TYPED_OUTCOME")
    return value


def capsule_observer(admitted: dict, env: dict[str, str], socket: Path, label: str):
    session = admitted["owner"] / "runtime" / f"{label}.session"
    mount = capsule_cli(admitted, env, socket, session, "providers.mount.observe",
                        {"provider": "driver:motionwright", "root": "motionwright-data"})["data"]
    binding = mount.get("binding_ref")
    capsule_require(isinstance(binding, str) and bool(binding)
                    and mount.get("mount", {}).get("root") == "motionwright-data"
                    and mount.get("mount", {}).get("write") is True, "E11_REAL_MOUNT_MISSING")
    admitted["bindings"].append(binding)

    def observe(args: dict, *, ok: bool = True) -> dict:
        outer = capsule_cli(admitted, env, socket, session, "providers.execute_bound", {
            "binding_ref": binding, "request": {"command": "driver.motionwright.observe",
                                                   "args": args, "dry_run": False},
        }, ok=ok)
        try:
            child = checked_native_result(outer, binding)
        except BaseException:
            admitted["unknown"] = True
            raise
        if not ok:
            capsule_require(outer.get("data") is None and child.get("data") is None,
                            "E11_DENIAL_RELEASED_NATIVE_DATA")
        return child
    return observe


def capsule_fixture(value: dict, expected_name: str) -> dict:
    expected_bytes, expected_sha = CAPSULE_FIXTURES[expected_name]
    for key in ("id", "generation", "capsule_id", "scene_id", "source_asset_id"):
        capsule_require(isinstance(value.get(key), str) and str(uuid.UUID(value[key])) == value[key], "E11_FIXTURE_ID")
    capsule_require(value.get("resource") == f"project:{value['id']}"
                    and re.fullmatch(r"0|[1-9][0-9]*", value.get("revision", "")) is not None
                    and value.get("source_sha256") == expected_sha and value.get("source_bytes") == expected_bytes
                    and value.get("media_type") == "model/gltf-binary"
                    and value.get("owner_local_fixture_only") is True and value.get("native_authority") is False,
                    "E11_FIXTURE_BINDING")
    fidelity = value.get("capsule_fidelity", {})
    capsule_require(fidelity.get("renderer") == "source-import" and fidelity.get("renderer_version") == "unverified"
                    and fidelity.get("evidence_sha256") is None, "E11_SOURCE_UNKNOWN_FIDELITY_CHANGED")
    return value


def capsule_inspection(value: dict, fixture: dict, original: bytes, reference: str) -> dict:
    data = value.get("data", {})
    page = data.get("page", {})
    version = {key: fixture[key] for key in ("resource", "generation", "revision")}
    scope = "native-capsule-source-inspection:" + fixture["capsule_id"]
    capsule_require(value.get("ok") is True and page.get("version") == version and page.get("scope") == scope
                    and page.get("complete") is True and page.get("next") is None
                    and len(page.get("items", [])) == 1 and isinstance(data.get("ref"), str)
                    and bool(data["ref"]) and bool(reference), "E11_BOUNDED_INSPECTION_PAGE")
    item = page["items"][0]
    for key in ("capsule_id", "scene_id", "source_asset_id", "source_sha256", "source_bytes"):
        capsule_require(item.get(key) == fixture[key], "E11_INSPECTION_BINDING")
    capsule_require(item.get("schema") == "motionwright.attached-native-source-observation/1"
                    and item.get("declared_media_type") == "model/gltf-binary"
                    and item.get("source_format") == "glb_2_0_structured_attach"
                    and item.get("original_binary_preserved") is True
                    and item.get("unknown_extensions_preserved") is True, "E11_INSPECTION_SOURCE_FORMAT")
    for field in ("runtime_or_imported_code_executed", "native_renderer_fidelity_observed",
                  "project_snapshot_was_modified", "semantically_editable_by_motionwright",
                  "human_creative_approval", "external_rights_or_install_granted"):
        capsule_require(item.get(field) is False, "E11_INSPECTION_UPGRADED_AUTHORITY_OR_FIDELITY")
    capsule_require(not set(item).intersection({"bytes", "raw_bytes", "source_path", "cas_path", "source_base64"}),
                    "E11_INSPECTION_RELEASED_SOURCE")
    # Independent original-byte oracle: compare every preserved chunk to the
    # retained real GLB. No renderer, imported code or semantic edit is executed.
    expected, position = [], 12
    while position < len(original):
        length = int.from_bytes(original[position:position + 4], "little")
        kind = original[position + 4:position + 8]
        position += 8
        capsule_require(length > 0 and position + length <= len(original), "E11_ORIGINAL_GLB_CHUNK")
        expected.append({"source_offset": position, "bytes": length,
                         "chunk_type": {b"JSON": "JSON", b"BIN\x00": "BIN"}.get(kind, f"0x{int.from_bytes(kind, 'little'):08x}"),
                         "sha256": hashlib.sha256(original[position:position + length]).hexdigest(),
                         "semantic_editability": "opaque_preserved"})
        position += length
    capsule_require(page["items"][0].get("glb_chunks") == expected and position == len(original), "E11_ORIGINAL_CHUNKS_DIFFER")
    return data


def capsule_read(admitted: dict, observe, fixture: dict, original: bytes, label: str) -> dict:
    summary = observe({"resource": fixture["resource"], "scope": "summary", "limit": 1})["data"]
    capsule_require(summary.get("page", {}).get("version") == {
        key: fixture[key] for key in ("resource", "generation", "revision")}, "E11_REAL_EXPECTED_STAMP")
    reference = summary.get("ref")
    capsule_require(isinstance(reference, str) and bool(reference), "E11_ORIGINAL_NATIVE_REF_MISSING")
    value = observe({"resource": fixture["resource"], "scope": "native-capsule-source-inspection:" + fixture["capsule_id"],
                     "limit": 1, "ref": reference})
    result = capsule_inspection(value, fixture, original, reference)
    admitted["custody"].new_json(admitted["evidence"] / f"{label}.inspection.original.json", {
        "request_reference": reference, "result": result, "fixture": fixture,
    })
    return result


def capsule_store(admitted: dict, label: str) -> tuple:
    database = admitted["database"]
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        original = "\n".join(connection.iterdump()).encode("utf-8")
    capsule_require(len(original) <= 16 * 1024 * 1024, "E11_SQL_SNAPSHOT_BUDGET")
    admitted["custody"].new_bytes(admitted["evidence"] / f"{label}.sqlite-content.original.sql", original)
    info = database.stat()
    return info.st_dev, info.st_ino, hashlib.sha256(original).hexdigest()


def capsule_manifest(provider: Path) -> dict:
    version = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))["workspace"]["package"]["version"]
    return {
        "manifest_version": 1, "protocol": 9, "id": "motionwright", "version": version,
        "publisher": "motionwright-project", "executable": str(provider), "sha256": digest(provider),
        "application": {"desktop_id": None, "process_names": ["motionwright-native-provider"], "supported_versions": []},
        "transport": "stdio_v1", "network": False,
        "mounts": [{"root": "motionwright-data", "read_only": False, "execute": False}],
        "resources": {"open_files": 128, "processes": 16, "cpu_seconds": 90, "operation_cpu_seconds": 0,
                      "address_space_bytes": 1_073_741_824, "file_size_bytes": 67_108_864},
        "request_timeout_ms": 10_000,
        "interfaces": {"dynamic_capabilities": False, "cooperative_cancellation": True, "events": False,
                       "progress": False, "artifacts": False, "health": True, "native_refs": True, "host_tools": False},
    }


def capsule_run(admitted: dict) -> None:
    custody, owner, evidence = admitted["custody"], admitted["owner"], admitted["evidence"]
    paths = {name: owner / name for name in ("runtime", "state", "home", "config", "binary", "motionwright-data", "inputs")}
    for path in paths.values():
        path.mkdir(mode=0o700)
    env = {key: value for key, value in os.environ.items()
           if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "MOTIONWRIGHT_E11_RECOVERY_RECIPIENT"}}
    env.update(HOME=str(paths["home"]), XDG_RUNTIME_DIR=str(paths["runtime"]), XDG_STATE_HOME=str(paths["state"]), RUST_BACKTRACE="1")
    admitted["database"] = paths["motionwright-data"] / "motionwright.sqlite3"
    database = admitted["database"]
    effective = capsule_process(admitted, "config-paths", [str(SW_BINS / "semwright"), "--json", "config", "paths"], env)
    owner_state = Path(effective["state"]).resolve()
    capsule_require(owner_state.is_relative_to(paths["state"]), "E11_OWNER_STATE_ESCAPE")
    provider = paths["binary"] / "motionwright-native-provider"
    custody.new_bytes(provider, custody.regular(PROVIDER_SOURCE.resolve())[0], mode=0o700)
    manifest = paths["config"] / "motionwright-driver.json"
    custody.new_json(manifest, capsule_manifest(provider))
    originals = {}
    for name, (size, sha256) in CAPSULE_FIXTURES.items():
        source = ROOT / "crates/native/tests/fixtures/attached_native_source" / name
        raw = custody.regular(source.resolve(), 4096)[0]
        capsule_require(len(raw) == size and hashlib.sha256(raw).hexdigest() == sha256, "E11_ORIGINAL_FIXTURE_CHANGED")
        custody.new_bytes(paths["inputs"] / name, raw)
        originals[name] = raw

    def source_actor(label: str, *args: str) -> dict:
        return capsule_process(admitted, label, [str(ACTOR), str(database), *args], env)

    def config(label: str, selections, *, bootstrap: bool = False) -> Path:
        target = paths["config"] / f"{label}.toml"
        text = owner_config(paths["motionwright-data"], manifest=None if bootstrap else manifest,
                            selections=selections)
        custody.new_bytes(target, text.encode())
        return target

    with capsule_stage(admitted, "seed"):
        initial = capsule_fixture(source_actor("seed-primary", "capsule-seed", "Retained E11 original GLB",
                                              str(paths["inputs"] / "authored.glb")), "authored.glb")
        foreign = capsule_fixture(source_actor("seed-foreign", "capsule-seed", "Independent E11 foreign source",
                                              str(paths["inputs"] / "edited.glb")), "edited.glb")
        capsule_require(initial["id"] != foreign["id"] and initial["capsule_id"] != foreign["capsule_id"], "E11_FOREIGN_NOT_INDEPENDENT")
        capsule_store(admitted, "seeded")

    with capsule_stage(admitted, "bootstrap"):
        mappings = []
        bootstrap = config("bootstrap", [], bootstrap=True)
        with capsule_daemon(admitted, "bootstrap", bootstrap, env) as socket:
            for fixture in (initial, foreign):
                created = capsule_cli(admitted, env, socket, paths["runtime"] / "bootstrap.session",
                                      "project.create", {"root": "motionwright-data"})
                mappings.append((fixture["resource"], created.get("data", {}).get("project")))
        capsule_require(all(isinstance(row[1], str) for row in mappings) and mappings[0][1] != mappings[1][1], "E11_REAL_CORE_PROJECTS")
        # owner_config validates the Core-issued prj_ identities. UUID fixture
        # fields never manufacture project authority or a native read mapping.
        permitted = config("permitted", [mappings[0]])
        withdrawn = config("withdrawn", [])
        restored = config("restored", [mappings[0]])
        retain_owner_audit(owner_state, "bootstrap-audit.original", evidence_root=evidence)

    with capsule_stage(admitted, "authorized-edit"):
        with capsule_daemon(admitted, "authorized", permitted, env) as socket:
            observe = capsule_observer(admitted, env, socket, "authorized")
            before = capsule_store(admitted, "before-original-observations")
            original_read = capsule_read(admitted, observe, initial, originals["authored.glb"], "original")
            denied = observe({"resource": foreign["resource"], "scope": "native-capsule-source-inspection:" + foreign["capsule_id"],
                              "limit": 1}, ok=False)
            require_known_denial(denied, "PolicyDenied", "E11_FOREIGN_RESOURCE_NOT_DENIED")
            capsule_require(capsule_store(admitted, "after-original-observations") == before, "E11_SOURCE_READ_MUTATED_STORE")
            edited = capsule_fixture(source_actor("edit-source", "capsule-import", initial["id"], initial["generation"],
                                                  initial["revision"], initial["capsule_id"], initial["scene_id"],
                                                  "host-e11-edited-source", str(paths["inputs"] / "edited.glb")), "edited.glb")
            capsule_require(all(edited[key] == initial[key] for key in ("id", "resource", "generation", "capsule_id", "scene_id"))
                            and int(edited["revision"]) == int(initial["revision"]) + 1
                            and edited["source_asset_id"] != initial["source_asset_id"]
                            and edited["capsule_fidelity"] == initial["capsule_fidelity"], "E11_EDIT_REBOUND_OR_PROMOTED_SOURCE")
            expected_assets = sorted([{"asset_id": fixture["source_asset_id"], "sha256": fixture["source_sha256"],
                                       "bytes": fixture["source_bytes"]} for fixture in (initial, edited)], key=lambda row: row["asset_id"])
            capsule_require(sorted(edited.get("retained_sources", []), key=lambda row: row["asset_id"]) == expected_assets,
                            "E11_ORIGINAL_SOURCE_NOT_RETAINED")
            before_edited = capsule_store(admitted, "before-edited-observations")
            stale = observe({"resource": edited["resource"], "scope": "native-capsule-source-inspection:" + edited["capsule_id"],
                             "limit": 1, "ref": original_read["ref"]}, ok=False)
            require_known_denial(stale, "StaleReference", "E11_OLD_SOURCE_REFERENCE_NOT_STALE")
            edited_read = capsule_read(admitted, observe, edited, originals["edited.glb"], "edited")
            capsule_require(capsule_store(admitted, "after-edited-observations") == before_edited, "E11_EDITED_READ_MUTATED_STORE")
        retain_owner_audit(owner_state, "authorized-audit.original", evidence_root=evidence)

    baseline = capsule_store(admitted, "before-independent-reopen")
    with capsule_stage(admitted, "reopen"):
        reopened = capsule_fixture(source_actor("reopened-actor", "capsule-show", edited["id"], edited["capsule_id"]), "edited.glb")
        capsule_require(reopened == edited, "E11_INDEPENDENT_ACTOR_REOPEN_DIFFERED")
        with capsule_daemon(admitted, "reopened", permitted, env) as socket:
            observe = capsule_observer(admitted, env, socket, "reopened")
            reopened_read = capsule_read(admitted, observe, reopened, originals["edited.glb"], "reopened")
            capsule_require(reopened_read["page"] == edited_read["page"], "E11_HOST_REOPEN_DIFFERED")
        capsule_require(capsule_store(admitted, "after-independent-reopen") == baseline, "E11_REOPEN_MUTATED_STORE")

    with capsule_stage(admitted, "withdrawn"):
        with capsule_daemon(admitted, "withdrawn", withdrawn, env) as socket:
            observe = capsule_observer(admitted, env, socket, "withdrawn")
            denied = observe({"resource": edited["resource"], "scope": "native-capsule-source-inspection:" + edited["capsule_id"],
                              "limit": 1}, ok=False)
            require_known_denial(denied, "PolicyDenied", "E11_OWNER_SELECTION_WITHDRAWAL_NOT_DENIED")
        capsule_require(capsule_store(admitted, "after-owner-withdrawal") == baseline, "E11_WITHDRAWAL_CHANGED_SOURCE")
        retain_owner_audit(owner_state, "withdrawn-audit.original", evidence_root=evidence)

    with capsule_stage(admitted, "restored"):
        with capsule_daemon(admitted, "restored", restored, env) as socket:
            observe = capsule_observer(admitted, env, socket, "restored")
            restored_read = capsule_read(admitted, observe, edited, originals["edited.glb"], "restored")
            capsule_require(restored_read["page"] == edited_read["page"]
                            and restored_read["ref"] != original_read["ref"]
                            and len(set(admitted["bindings"])) == len(admitted["bindings"]), "E11_RESTORED_ORIGINAL_OR_NEW_GRANT_MISSING")
        final = capsule_fixture(source_actor("final-actor", "capsule-show", edited["id"], edited["capsule_id"]), "edited.glb")
        capsule_require(final == edited and capsule_store(admitted, "final") == baseline, "E11_FINAL_SOURCE_OR_STORE_DIFFERED")
        retain_owner_audit(owner_state, "final-audit.original", evidence_root=evidence)
        custody.new_json(evidence / "result.private.json", {
            "status": "SCOPED_COMPLETED", "source": admitted["source"], "initial": initial, "edited": edited,
            "checks": {"host_original_glb_read_with_expected_reference": True, "foreign_resource_denied": True,
                       "original_and_edited_cas_bytes_retained": True, "old_native_reference_stale_after_owner_import": True,
                       "independent_actor_and_host_reopen": True, "owner_read_selection_withdrawn_after_restart": True,
                       "restored_selection_observed_with_new_session_and_binding": True, "source_unknown_fidelity_preserved": True,
                       "native_reads_did_not_mutate_sqlite": True},
            "core_inflight_revocation_e11": "NOT_RUN", "existing_core_seven_read_scope_tests": "SEPARATE_COVERAGE",
            "native_render_performed": False, "native_source_edit_performed": False, "formal_acceptance": False,
            "release_authorized": False, "automatic_retry": False,
        })


def capsule_preserve_final(admitted: dict, terminal: dict) -> list[dict]:
    """A failed receipt write cannot suppress the one partial sealing attempt."""
    failures = []
    custody, evidence = admitted["custody"], admitted["evidence"]
    for name, value in (("transport-calls.original.json", admitted["transport_calls"]),
                        ("runner.terminal.json", terminal)):
        if failures and value is terminal:
            terminal.update(status="UNKNOWN", persistence_failures=list(failures))
        try:
            custody.new_json(evidence / name, value)
        except BaseException as error:
            failures.append({"file": name, "exception_type": type(error).__name__, "error": str(error),
                             "automatic_retry": False})
    if failures:
        admitted["unknown"] = True
        admitted["failed"] = True
        # This is a separate diagnostic, never an overwrite or retry of either
        # original receipt. It too may fail; readable originals still get sealed.
        name = "runner.persistence-failures.original.json"
        try:
            custody.new_json(evidence / name, {"status": "UNKNOWN", "failures": failures, "automatic_retry": False})
        except BaseException as error:
            failures.append({"file": name, "exception_type": type(error).__name__, "error": str(error),
                             "automatic_retry": False})
    return failures


def capsule_main() -> None:
    require_exact_inputs()
    import ci_private_evidence as custody
    from importlib.metadata import version
    capsule_require(sys.platform == "linux" and hasattr(os, "pidfd_open")
                    and hasattr(signal, "pidfd_send_signal"), "E11_LINUX_PROCESS_IDENTITY_REQUIRED")
    capsule_require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                    and os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "E11_MANUAL_FIRST_ATTEMPT_REQUIRED")
    context = custody.validate_context({"source_sha": GITHUB_SHA, "semwright_sha": PIN,
                                       "run_id": os.environ.get("GITHUB_RUN_ID", ""), "run_attempt": "1"})
    recipient = custody.strict(os.environ.get("MOTIONWRIGHT_E11_RECOVERY_RECIPIENT", "").encode())
    custody.public_key(recipient)  # Existing admitted public recipient only; no key generation.
    versions = {name: version(name) for name in ("cryptography", "cffi", "pycparser")}
    capsule_require(versions == {"cryptography": "50.0.2", "cffi": "2.1.1", "pycparser": "2.23"},
                    "E11_EXISTING_PRIVATE_CUSTODY_PROFILE")
    source = capsule_source_identity()
    temporary = Path(os.environ.get("RUNNER_TEMP", ""))
    capsule_require(temporary.is_absolute() and temporary.is_dir()
                    and temporary.resolve(strict=True) == temporary
                    and not any(path.is_symlink() for path in (temporary, *temporary.parents)), "E11_RUNNER_TEMP")
    # Unix owner sockets have a short platform limit. This exclusive persistent
    # runner directory is sealed with evidence and is never a TemporaryDirectory.
    owner = temporary / ("mw-e11-" + context["run_id"])
    capsule_require(len(os.fsencode(owner / "runtime" / "authorized.sock")) < 104, "E11_OWNER_SOCKET_PATH_BUDGET")
    private = ROOT / "verification/native-sdk/github-actions/capsule-source" / GITHUB_SHA
    public = ROOT / "verification/native-sdk/capsule-source-sealed" / GITHUB_SHA
    private.mkdir(mode=0o700, parents=True, exist_ok=False)
    public.mkdir(mode=0o700, parents=True, exist_ok=False)
    evidence = private / "evidence"
    owner.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    source_copies = evidence / "source-originals"
    source_copies.mkdir(mode=0o700)
    source_descriptors = []
    for relative in (*CAPSULE_LOCKS, "tooling/native_host_shared_state.py", "tooling/ci_private_evidence.py",
                     "tooling/mlt_official_runtime_intake.py", "crates/native/examples/shared-state-actor.rs",
                     "crates/native/src/lib.rs", "crates/native/src/attached_source_inspection.rs",
                     "crates/service/src/native_capsule_source.rs", "crates/domain/src/native_capsule_source.rs"):
        raw = custody.regular((ROOT / relative).resolve(), 16 * 1024 * 1024)[0]
        target = source_copies / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        custody.new_bytes(target, raw)
        source_descriptors.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    admitted = {"custody": custody, "source": source, "context": context, "owner": owner, "evidence": evidence,
                "completed_stages": [], "transport_calls": [], "bindings": [], "processes": [],
                "shutdown": [], "unknown": False, "failed": False}
    custody.new_json(evidence / "runner.started.json", {
        "source": source, "context": context, "provider_sha256": digest(PROVIDER_SOURCE), "actor_sha256": digest(ACTOR),
        "source_originals": source_descriptors, "custody_versions": versions,
        "recipient_spki_sha256": recipient["sha256"], "automatic_retry": False,
        "private_owner_retained_in_place": True, "owner_root": str(owner), "core_inflight_revocation_e11": "NOT_RUN",
    })
    failure = None
    try:
        capsule_run(admitted)
    except BaseException as error:
        admitted["failed"] = True
        failure = {"exception_type": type(error).__name__, "error": str(error)}
    finally:
        closure = []
        for tracked in admitted["processes"]:
            try:
                # Never re-signal a previously closed group whose PID might
                # since have been recycled by the operating system.
                if tracked["quiescent"]:
                    closed = True
                else:
                    group_closed = capsule_stop_group(tracked["process"])
                    descendants_closed = capsule_stop_descendants(tracked)
                    closed = group_closed and descendants_closed
                tracked["quiescent"] = closed
                closure.append(closed)
            except BaseException:
                closure.append(False)
        shutdown_complete = all(closure) and all(admitted["shutdown"])
        unchanged = False
        try:
            unchanged = capsule_source_identity() == source
        except BaseException:
            pass
        complete_run = (admitted["completed_stages"] == list(CAPSULE_STAGES) and not admitted["failed"]
                        and not admitted["unknown"] and shutdown_complete and unchanged)
        terminal = {
            "status": "SCOPED_COMPLETED" if complete_run else "UNKNOWN" if admitted["unknown"] or not shutdown_complete else "FAIL",
            "source": source, "context": context, "completed_stages": admitted["completed_stages"], "failure": failure,
            "process_groups_quiescent": shutdown_complete, "source_unchanged": unchanged,
            "automatic_retry": False, "effects_absent_asserted": False, "formal_acceptance": False,
            "core_inflight_revocation_e11": "NOT_RUN", "release_authorized": False,
        }
        persistence_failures = capsule_preserve_final(admitted, terminal)
        if persistence_failures:
            complete_run = False
        # Preserve even uncertain partial originals once. No deletion or replay;
        # only the existing authenticated ciphertext format may leave the runner.
        plaintext, captured = custody.gather_snapshot({"owner": owner, "evidence": evidence}, preserve_partial=True)
        ciphertext = custody.encrypt_snapshot(plaintext, recipient, context)
        custody.new_bytes(public / "sealed-originals.bin", ciphertext)
        custody.new_json(private / "seal-receipt.json", {
            "context": context, "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(), "ciphertext_bytes": len(ciphertext),
            "selected_originals_complete": captured, "process_groups_quiescent": shutdown_complete,
            "required_terminal_receipts_persisted": not persistence_failures,
            "persistence_failures": persistence_failures,
            "automatic_retry": False, "originals_retained_in_place": True,
        })
    capsule_require(complete_run and captured, "E11_NOT_VERIFIED_OR_PARTIAL_PRIVATE_CUSTODY")
    print(json.dumps({"native_host_capsule_source": "SCOPED_COMPLETED", "motionwright_sha": GITHUB_SHA,
                      "semwright_sha": PIN, "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
                      "core_inflight_revocation_e11": "NOT_RUN", "formal_acceptance": False}))


def shared_state_custody_main() -> None:
    """Retain the unchanged standard case when the source cycle is selected.

    A failed standard Broker startup must retain its own original diagnostic;
    it never becomes a source-cycle attempt or authorizes skipping that case.
    """
    require_exact_inputs()
    import ci_private_evidence as custody
    from importlib.metadata import version
    capsule_require(sys.platform == "linux"
                    and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                    and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
                    "SHARED_CUSTODY_MANUAL_FIRST_ATTEMPT_REQUIRED")
    context = custody.validate_context({"source_sha": GITHUB_SHA, "semwright_sha": PIN,
                                       "run_id": os.environ.get("GITHUB_RUN_ID", ""), "run_attempt": "1"})
    recipient = custody.strict(os.environ.get("MOTIONWRIGHT_E11_RECOVERY_RECIPIENT", "").encode())
    custody.public_key(recipient)
    versions = {name: version(name) for name in ("cryptography", "cffi", "pycparser")}
    capsule_require(versions == {"cryptography": "50.0.2", "cffi": "2.1.1", "pycparser": "2.23"},
                    "SHARED_CUSTODY_PROFILE")
    source = capsule_source_identity()
    temporary = Path(os.environ.get("RUNNER_TEMP", ""))
    capsule_require(temporary.is_absolute() and temporary.is_dir()
                    and temporary.resolve(strict=True) == temporary
                    and not any(path.is_symlink() for path in (temporary, *temporary.parents)),
                    "SHARED_CUSTODY_RUNNER_TEMP")
    owner = temporary / ("mw-shared-" + context["run_id"])
    capsule_require(len(os.fsencode(owner / "runtime" / "bootstrap.sock")) < 104,
                    "SHARED_CUSTODY_SOCKET_PATH_BUDGET")
    private = ROOT / "verification/native-sdk/github-actions/shared-state-custody" / GITHUB_SHA
    public = ROOT / "verification/native-sdk/shared-state-sealed" / GITHUB_SHA
    for path in (owner, EVIDENCE, private, public):
        capsule_require(not path.exists() and not path.is_symlink(), "SHARED_CUSTODY_ORIGINAL_PATH_EXISTS")
    private.mkdir(mode=0o700, parents=True)
    public.mkdir(mode=0o700, parents=True)
    owner.mkdir(mode=0o700)
    custody.new_json(private / "runner.started.original.json", {
        "scope": "existing-standard-shared-state-only", "source": source, "context": context,
        "custody_versions": versions, "recipient_spki_sha256": recipient["sha256"],
        "owner_retained_in_place": True, "owner_root": str(owner),
        "automatic_retry": False, "capsule_source_cycle_started_by_this_entry": False,
    })
    returned = False
    failure = None
    try:
        originals = private / "source-originals"
        originals.mkdir(mode=0o700)
        for relative in (*CAPSULE_LOCKS, "tooling/native_host_shared_state.py",
                         "tooling/ci_private_evidence.py", "tooling/mlt_official_runtime_intake.py",
                         ".github/workflows/native-sdk.yml"):
            raw = custody.regular((ROOT / relative).resolve(), 16 * 1024 * 1024)[0]
            target = originals / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            custody.new_bytes(target, raw)
        # Same standard oracle, calls, curator inputs and cleanup. The exclusive
        # owner directory survives cleanup so its config and raw log can be sealed.
        main(retained_owner=owner)
        returned = True
    except BaseException as error:
        failure = {"exception_type": type(error).__name__, "error": str(error),
                   "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))}
    finally:
        unchanged = False
        try:
            unchanged = capsule_source_identity() == source
        except BaseException:
            pass
        terminal = {
            "scope": "existing-standard-shared-state-only", "source": source, "context": context,
            "standard_returned_success": returned, "failure": failure, "source_unchanged": unchanged,
            "process_groups_quiescence_verified": False, "atomic_sqlite_snapshot_claimed": False,
            "owner_retained_in_place": True, "automatic_retry": False,
            "capsule_source_cycle_started_by_this_entry": False, "effects_absent_asserted": False,
            "formal_acceptance": False, "release_authorized": False,
        }
        persistence_failures = []
        for name, value in (("runner.terminal.original.json", terminal),
                            ("failure.original.json", failure)):
            try:
                custody.new_json(private / name, value)
            except BaseException as error:
                persistence_failures.append({"file": name, "exception_type": type(error).__name__})
        # One partial-preserving capture even if terminal persistence failed.
        # This is diagnostic custody, not proof that every descendant stopped.
        plaintext, complete = custody.gather_snapshot(
            {"owner": owner, "evidence": EVIDENCE, "custody": private}, preserve_partial=True)
        ciphertext = custody.encrypt_snapshot(plaintext, recipient, context)
        custody.new_bytes(public / "sealed-originals.bin", ciphertext)
        custody.new_json(private / "seal-receipt.json", {
            "context": context, "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "ciphertext_bytes": len(ciphertext), "selected_originals_complete": complete,
            "required_terminal_receipts_persisted": not persistence_failures,
            "persistence_failures": persistence_failures, "automatic_retry": False,
            "process_groups_quiescence_verified": False, "atomic_sqlite_snapshot_claimed": False,
            "originals_retained_in_place": True,
        })
    capsule_require(returned and unchanged and complete and not persistence_failures,
                    "SHARED_STATE_FAILED_OR_PRIVATE_CUSTODY_PARTIAL")


if __name__ == "__main__":
    if sys.argv[1:] == ["--capsule-source"]:
        try:
            capsule_main()
        except BaseException:
            # Native originals and actor errors remain in private custody.
            print("E11_NOT_VERIFIED: inspect the retained encrypted originals; no automatic retry", file=sys.stderr)
            raise SystemExit(1) from None
    elif sys.argv[1:] == ["--shared-state-custody"]:
        try:
            shared_state_custody_main()
        except BaseException:
            print("SHARED_STATE_NOT_VERIFIED: inspect the retained encrypted originals; no automatic retry", file=sys.stderr)
            raise SystemExit(1) from None
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("usage: native_host_shared_state.py [--capsule-source|--shared-state-custody]")
