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
import sqlite3
import subprocess
import tempfile
import time
import tomllib
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
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
               args: dict, calls: list[dict], *, ok: bool | None = True) -> dict:
    """Retain the original outer envelope before validating an expected outcome."""
    receipt = uuid.uuid4().hex  # Custody filename only; never a principal or grant.
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
                path = EVIDENCE / f"{receipt}.{name}.original"
                path.write_bytes(raw)
                path.chmod(0o600)
        raise AssertionError(
            "Native shared-host CLI unavailable or timed out; no effect is retried and custody is private"
        ) from None
    for name, raw in (("stdout", result.stdout), ("stderr", result.stderr)):
        path = EVIDENCE / f"{receipt}.{name}.original"
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


def retain_owner_audit(state: Path, label: str) -> None:
    """Keep private child/parent audit correlation before disposable state disappears."""
    audit = state / "audit"
    if not audit.is_dir():
        write_json(EVIDENCE / f"{label}.json", {"status": "NOT_RUN", "audit_present": False})
        return
    if audit.is_symlink() or audit.resolve() != audit:
        raise AssertionError("Native shared-host audit escaped the isolated owner state")
    files = []
    for source in audit.rglob("*"):
        if source.is_symlink() or not (source.is_file() or source.is_dir()):
            raise AssertionError("Native shared-host audit contains a nonregular entry")
        if source.is_file():
            files.append((source.relative_to(audit), digest(source)))
    destination = EVIDENCE / label
    shutil.copytree(audit, destination)
    destination.chmod(0o700)
    for relative, expected in files:
        copied = destination / relative
        copied.chmod(0o600)
        if digest(copied) != expected:
            raise AssertionError("Native shared-host audit custody changed original bytes")
    write_json(EVIDENCE / f"{label}.json", {
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


def main() -> None:
    require_exact_inputs()
    EVIDENCE.mkdir(mode=0o700, parents=True, exist_ok=False)
    calls: list[dict] = []
    transport_calls: list[dict] = []

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


if __name__ == "__main__":
    main()
