#!/usr/bin/env python3
"""Real two-provider Motionwright/Semwright recovery proof on an exact-SHA CI runner.

Creates one application-owned project and one Broker/Driver Host serving Blender
and Motion Canvas. Stage one uses real Blender then *intentionally stops before
Motion Canvas is dispatched*. Two fresh Motionwright processes resume and then
reuse both verified results. This does not claim a native Motion Canvas crash.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import native_blender_e2e as blender
import native_render_e2e as render
from cross_app_demo_report import make_report

ROOT = Path(__file__).resolve().parents[1]
SW = render.SEMWRIGHT
BINS = render.SW_BINS
PIN = render.PIN
EVIDENCE = ROOT / "verification" / "native-cross-app-recovery" / os.environ.get("GITHUB_SHA", "unknown")
EXAMPLE = ROOT / "target" / "debug" / "examples" / "native-cross-app-recovery-e2e"
BLENDER_BIN = blender.BLENDER_BIN
BLENDER_ROOT = blender.BLENDER_ROOT
RUNTIME = render.RUNTIME
SHA = os.environ.get("GITHUB_SHA", "unknown")


def require_inputs() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("real native cross-app recovery is GitHub-Actions-only")
    if subprocess.check_output(["git", "-C", str(SW), "rev-parse", "HEAD"], text=True).strip() != PIN:
        raise SystemExit("Semwright checkout differs from exact source lock")
    expected = [
        EXAMPLE,
        BINS / "semwright",
        BINS / "semwrightd",
        BINS / "semwright-sandbox",
        BINS / "semwright-motion-canvas-driver",
        BINS / "semwright-blender-driver",
        BINS / "semwright-blender-session-runner",
        BLENDER_BIN,
        RUNTIME / "package-lock.json",
    ]
    if any(not path.is_file() for path in expected):
        raise SystemExit("missing pinned tool/binary required for native cross-app acceptance")
    if shutil.which("bwrap") is None or shutil.which("node") is None:
        raise SystemExit("sandbox helper and Node required for native E2E")
    if subprocess.check_output([str(BLENDER_BIN), "--version"], text=True).splitlines()[0] != "Blender 4.5.14 LTS":
        raise SystemExit("Blender version does not match exact acceptance fixture")


def manifests(paths: dict[str, Path]) -> tuple[list[Path], list[tuple[str, Path, bool]], dict]:
    blender_driver = paths["bin"] / "semwright-blender-driver"
    motion_driver = paths["bin"] / "semwright-motion-canvas-driver"
    node_tool = paths["bin"] / "semwright-motion-node"
    shutil.copyfile(BINS / "semwright-blender-driver", blender_driver)
    shutil.copyfile(BINS / "semwright-motion-canvas-driver", motion_driver)
    shutil.copyfile(Path(shutil.which("node") or ""), node_tool)
    for binary in [blender_driver, motion_driver, node_tool]:
        binary.chmod(0o700)
    session_runner = (BINS / "semwright-blender-session-runner").resolve()
    blender_bin = BLENDER_BIN.resolve()
    version = blender.semwright_version()
    common = {
        "manifest_version": 1,
        "version": version,
        "publisher": "semwright",
        "transport": "stdio_v1",
        "system_config": [],
        "secrets": [],
        "network": False,
        "loopback_port": None,
    }
    blender_manifest = {
        **common,
        "protocol": 8, "id": "blender",
        "executable": str(blender_driver), "sha256": blender.digest(blender_driver),
        "application": {
            "desktop_id": "org.blender.Blender", "process_names": ["blender"],
            "supported_versions": ["4.5.14"],
        },
        "mounts": [
            {"root": "workspace", "read_only": False},
            {"root": "blender-runtime", "read_only": True},
            {"root": "scratch", "read_only": False},
            {"root": "font-config", "read_only": True},
        ],
        "tools": [
            {
                "root": "blender-session-runner", "name": "blender-session-runner",
                "sha256": blender.digest(session_runner),
                "mounts": ["workspace", "blender-runtime", "scratch", "font-config"],
                "dependencies": ["blender"],
            },
            {"root": "blender-executable", "name": "blender",
             "sha256": blender.digest(blender_bin), "mounts": [], "dependencies": []},
        ],
        "resources": {
            "open_files": 256, "processes": 64, "cpu_seconds": 300,
            "operation_cpu_seconds": 0, "address_space_bytes": 4_294_967_296,
            "file_size_bytes": 1_073_741_824,
        },
        "request_timeout_ms": 300_000,
        "interfaces": {
            "dynamic_capabilities": False, "cooperative_cancellation": True,
            "events": False, "health": True, "progress": True,
            "artifacts": False, "native_refs": False, "host_tools": True,
        },
    }
    motion_manifest = {
        **common,
        "protocol": 7, "id": "motion-canvas",
        "executable": str(motion_driver), "sha256": render.digest(motion_driver),
        "application": {
            "desktop_id": None, "process_names": ["node"],
            "supported_versions": ["Motion Canvas 3.17.2", "Node 22.22.0"],
        },
        "mounts": [
            {"root": "project", "read_only": False},
            {"root": "media", "read_only": True},
            {"root": "output", "read_only": False},
            {"root": "runtime", "read_only": True, "execute": True},
            {"root": "fontconfig", "read_only": True},
        ],
        "tools": [
            {"root": "motion-node-tool", "name": "motion-node",
             "sha256": render.digest(node_tool),
             "mounts": ["project", "output", "runtime", "fontconfig"]}
        ],
        "resources": {
            "open_files": 512, "processes": 256, "cpu_seconds": 300,
            "operation_cpu_seconds": 0, "address_space_bytes": 4_294_967_296,
            "file_size_bytes": 1_073_741_824,
        },
        "request_timeout_ms": 300_000,
        "interfaces": {
            "dynamic_capabilities": False, "cooperative_cancellation": True,
            "events": False, "health": True, "progress": True,
            "artifacts": True, "native_refs": False, "host_tools": True,
        },
    }
    bm = paths["config"] / "blender-driver.json"
    mm = paths["config"] / "motion-canvas-driver.json"
    render.write_private_json(bm, blender_manifest)
    render.write_private_json(mm, motion_manifest)
    # Two semantic mount names may refer to the same owner directory. Both
    # drivers publish artifacts into this one tightly authorized root.
    grants = [
        ("workspace", paths["output"], True),
        ("scratch", paths["scratch"], True),
        ("blender-runtime", BLENDER_ROOT, False),
        ("font-config", Path("/etc/fonts"), False),
        ("blender-session-runner", session_runner, False),
        ("blender-executable", blender_bin, False),
        ("project", paths["project"], True),
        ("media", paths["media"], False),
        ("output", paths["output"], True),
        ("runtime", RUNTIME, False),
        ("fontconfig", Path("/etc/fonts"), False),
        ("motion-node-tool", node_tool, False),
    ]
    return [bm, mm], grants, {
        "blender_driver_sha256": blender.digest(blender_driver),
        "motion_driver_sha256": render.digest(motion_driver),
        "node_sha256": render.digest(node_tool),
    }


def save_bounded_native_failure_reason(output_root: Path) -> None:
    """Never publish arbitrary driver stacks, paths or exception messages."""
    records = []
    for path in sorted(output_root.rglob("native-failure-receipt.json"))[:4]:
        try:
            if path.stat().st_size > 65536 or path.stat().st_size == 0:
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                continue
            stack = str(value.get("local_stack_only", "")).lower()
            # Only finite codes, never the untrusted message or pathname.
            causes = {
                "enoent": ("enoent" in stack or "no such file or directory" in stack),
                "missing_module": "cannot find module" in stack,
                "resource_digest": "font resource digest binding changed" in stack,
                "resource_pin": "font resource binding changed" in stack,
                "dependency_lock": "dependency lock binding changed" in stack,
                "missing_font": "pinned font" in stack and (
                    "no woff2" in stack or "file exceeds" in stack),
                "project_root": "invalid project" in stack or "project root" in stack,
                "browser_executable": "firefox executable" in stack,
                "permission": "eacces" in stack or "permission denied" in stack,
            }
            matched = [name for name, found in causes.items() if found]
            records.append({
                "error_class": value.get("error_class") if value.get("error_class") in {
                    "font_evidence", "project_stage", "vite_build", "frame_export",
                    "browser_launch", "render_nonzero", "observation"
                } else "unclassified",
                "exception_name": value.get("exception_name") if value.get("exception_name") in {
                    "Error", "TypeError", "RangeError", "SyntaxError", "AggregateError"
                } else "OtherError",
                "reason_codes": matched[:4] if matched else ["unclassified"],
            })
        except (OSError, ValueError, UnicodeError, TypeError):
            continue
    render.write_private_json(
        EVIDENCE / "bounded-native-failure-reasons.json",
        {"schema": "motionwright-cross-app-failure-reasons/1", "observations": records},
    )


def pinned_firefox_executable(env: dict[str, str]) -> Path:
    """Resolve only the Firefox binary installed inside our pinned runtime.

    Never rename an arbitrary system browser, even on the ephemeral CI host.
    """
    if env.get("PLAYWRIGHT_BROWSERS_PATH") != "0":
        raise AssertionError("native renderer requires its local pinned browser registry")
    result = subprocess.run(
        [
            shutil.which("node") or "node", "--input-type=module", "-e",
            "import {firefox} from 'playwright'; console.log(firefox.executablePath());",
        ],
        cwd=RUNTIME, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True, timeout=25,
    )
    raw = Path(result.stdout.strip())
    # A browser already missing here means test preconditions are broken, not
    # a valid example of a new terminal rendering failure.
    if not raw.is_absolute() or not raw.is_file() or raw.is_symlink():
        raise AssertionError("pinned native Firefox binary is not an ordinary file")
    browser = raw.resolve(strict=True)
    runtime_root = RUNTIME.resolve(strict=True)
    if not browser.is_relative_to(runtime_root) or browser.suffix == ".motionwright-fault":
        raise AssertionError("native fault injection cannot target outside the runtime bundle")
    return browser


def main() -> None:
    require_inputs()
    EVIDENCE.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="motionwright-cross-app-") as tmp:
        root = Path(tmp).resolve()
        root.chmod(0o700)
        paths: dict[str, Path] = {}
        for name in [
            "runtime", "state", "home", "config", "bin", "output",
            "scratch", "project", "media", "motionwright-data",
        ]:
            location = root / name
            location.mkdir(mode=0o700)
            paths[name] = location
        env = {
            key: value for key, value in os.environ.items()
            if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}
        }
        env.update(
            HOME=str(paths["home"]), XDG_RUNTIME_DIR=str(paths["runtime"]),
            XDG_STATE_HOME=str(paths["state"]), RUST_BACKTRACE="1",
            SEMWRIGHT_TEST_DRIVER_SANDBOX="1",
            SEMWRIGHT_TEST_SANDBOX_HELPER=str(BINS / "semwright-sandbox"),
            SEMWRIGHT_TEST_BLENDER_ROOT=str(BLENDER_ROOT),
        )
        database = paths["motionwright-data"] / "motionwright.sqlite3"
        seeded = render.run_json([str(EXAMPLE), "seed", str(database)], env=env)
        manifests_paths, grants, binaries = manifests(paths)
        lines = [
            f"drivers = [{', '.join(json.dumps(str(path)) for path in manifests_paths)}]",
            "driver_network = false",
            "",
            "[policy]",
            'profile = "workspace"',
            'allow = ["driver:blender", "driver:motion-canvas"]',
        ]
        for name, location, write in grants:
            lines.extend([
                "", "[[policy.filesystem]]", f"name = {json.dumps(name)}",
                f"path = {json.dumps(str(location.resolve()))}",
                "read = true", f"write = {'true' if write else 'false'}",
            ])
        config = paths["config"] / "owner.toml"
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")
        config.chmod(0o600)

        socket = paths["runtime"] / "broker.sock"
        session = paths["runtime"] / "motionwright.session"
        daemon_log = EVIDENCE / "semwrightd.log"
        with daemon_log.open("wb") as log:
            daemon = subprocess.Popen(
                [str(BINS / "semwrightd"), "--config", str(config), "--socket", str(socket)],
                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
            )
            try:
                render.wait_for_socket(daemon, socket, daemon_log)
                for provider in ["driver:blender", "driver:motion-canvas"]:
                    discovered = render.run_json([
                        str(BINS / "semwright"), "--socket", str(socket),
                        "--session-file", str(session), "--json", "capabilities", "search",
                        "", "--provider", provider, "--limit", "100", "--offset", "0",
                    ], env=env)
                    if not discovered.get("data", {}).get("capabilities"):
                        raise AssertionError(f"native provider unavailable: {provider}")
                if not session.is_file() or (session.stat().st_mode & 0o777) != 0o600:
                    raise AssertionError("private Semwright Broker session ticket missing")
                connection = paths["config"] / "owner-connection.json"
                render.write_private_json(connection, {
                    "schema": "motionwright-semwright-connection/1",
                    "executable": str((BINS / "semwright").resolve()),
                    "executable_sha256": render.digest(BINS / "semwright"),
                    "socket": str(socket), "session_file": str(session),
                    "output_root": str(paths["output"]),
                    "resource": seeded["resource"],
                })
                # Each call is a FRESH Motionwright process over the SAME SQLite
                # and same native Broker authority. No demo-side fake scheduler.
                stop = render.run_json([
                    str(EXAMPLE), "stop", str(database), str(connection),
                    str(EVIDENCE / "stop.json"),
                ], env=env, timeout=420)
                stopped = json.loads((EVIDENCE / "stop.json").read_text())
                if stop["phase"] != "stop" or stopped["blender"] != "executed":
                    raise AssertionError("native Blender did not complete before interruption")
                bp = stopped["blender_proof"]
                glb = paths["output"] / bp["relative_path"]
                if render.digest(glb) != bp["sha256"] or glb.stat().st_size != bp["bytes"]:
                    raise AssertionError("Blender GLB did not survive the first process exit")
                model = blender.parse_glb(glb)
                shutil.copyfile(glb, EVIDENCE / "reused-blender.glb")

                # Real terminal failure, NOT a scripted provider response:
                # the Semwright Motion Canvas renderer gets as far as its
                # pinned-browser executable check, fails natively, and must
                # durably record a confirmed retryable result. We restore the
                # byte-identical browser before making a fresh app process retry.
                browser = pinned_firefox_executable(env)
                browser_sha = render.digest(browser)
                disabled = browser.with_name(browser.name + ".motionwright-fault")
                if disabled.exists():
                    raise AssertionError("fault injection target already exists")
                browser.rename(disabled)
                try:
                    native_failure = render.run_json([
                        str(EXAMPLE), "fail", str(database), str(connection),
                        str(EVIDENCE / "failure.json"),
                    ], env=env, timeout=440)
                finally:
                    if not disabled.is_file() or browser.exists():
                        raise AssertionError("pinned native Firefox restore invariant failed")
                    disabled.rename(browser)
                if render.digest(browser) != browser_sha:
                    raise AssertionError("native Firefox bytes changed during failure injection")
                recorded_failure = json.loads((EVIDENCE / "failure.json").read_text())
                if (native_failure.get("native_cross_app_recovery_e2e")
                        != "EXPECTED_NATIVE_FAILURE"
                        or recorded_failure.get("native_failure_class") != "font_evidence"
                        or recorded_failure.get("native_status") != "failed"
                        or recorded_failure.get("motion_canvas_attempt") != 1
                        or recorded_failure.get("outcome_known") is not True
                        or recorded_failure.get("retryable") is not True
                        or recorded_failure.get("blender_reexecution_count") != 0):
                    raise AssertionError("real native failed state did not produce a safe checkpoint")
                save_bounded_native_failure_reason(paths["output"])
                classified = json.loads(
                    (EVIDENCE / "bounded-native-failure-reasons.json").read_text()
                )
                if not any(x.get("error_class") == "font_evidence"
                           and "enoent" in x.get("reason_codes", [])
                           for x in classified["observations"]):
                    raise AssertionError("real native failure receipt did not confirm the pinned browser interruption")

                try:
                    resumed = render.run_json([
                        str(EXAMPLE), "resume", str(database), str(connection),
                        str(EVIDENCE / "resume.json"),
                    ], env=env, timeout=440)
                except (AssertionError, subprocess.TimeoutExpired):
                    save_bounded_native_failure_reason(paths["output"])
                    raise
                complete = json.loads((EVIDENCE / "resume.json").read_text())
                if resumed["phase"] != "resume":
                    raise AssertionError("resumed Motionwright process did not finish")
                if complete["recovery"]["blender"] != "reused":
                    raise AssertionError("Blender was not reused")
                if complete["recovery"]["motion_canvas"] != "executed":
                    raise AssertionError("actual Motion Canvas did not execute")
                if complete["blender_reexecution_count"] != 0:
                    raise AssertionError("Blender native commands repeated during resume")
                if complete["recovery"]["blender_proof"]["sha256"] != bp["sha256"]:
                    raise AssertionError("Blender artifact identity changed after restart")

                segment = complete["recovery"]["motion"]["segments"]
                if len(segment) != 1 or segment[0]["frame_count"] != 60:
                    raise AssertionError("native Motion Canvas did not render exactly 60 frames")
                native = segment[0]["artifact"]
                native_dir = paths["output"] / native["directory"]
                manifest = native_dir / "artifact-manifest.json"
                if (native.get("manifest_sha256") is not None
                        and render.digest(manifest) != native["manifest_sha256"]):
                    raise AssertionError("native Motion Canvas manifest digest disagrees with provider")
                frames = sorted((native_dir / "frames").glob("*.png"))
                if len(frames) != 60:
                    raise AssertionError("native Motion Canvas incomplete rendered frames")

                repeated = render.run_json([
                    str(EXAMPLE), "repeat", str(database), str(connection),
                    str(EVIDENCE / "repeat.json"),
                ], env=env, timeout=440)
                repeat = json.loads((EVIDENCE / "repeat.json").read_text())
                if repeated["phase"] != "repeat" or repeat["recovery"]["motion_canvas"] != "reused":
                    raise AssertionError("verified native Motion Canvas did not reuse after restart")
                if repeat["blender_reexecution_count"] != 0:
                    raise AssertionError("Blender reran on the final process")
                for source, name in [
                    (frames[0], "first-frame.png"),
                    (frames[30], "middle-frame.png"),
                    (frames[-1], "last-frame.png"),
                ]:
                    shutil.copyfile(source, EVIDENCE / name)
                shutil.copyfile(manifest, EVIDENCE / "artifact-manifest.json")
                render.write_private_json(EVIDENCE / "result.json", {
                    "native_cross_app_recovery_e2e": "PASS",
                    "source_motionwright": SHA, "source_semwright": PIN,
                    "blender_version": "4.5.14 LTS",
                    "failure_model": "real native terminal render failure from CI-only missing Firefox executable; restored byte-identical pinned browser before retry",
                    "native_motion_canvas_failed_attempts": 1,
                    "native_motion_canvas_successful_attempts": 1,
                    "browser_original_sha256": browser_sha,
                    "app_processes": 4,
                    "blender_native_driver_reexecutions": 0,
                    "blender_glb_sha256": bp["sha256"],
                    "blender_scene_meshes": len(model["meshes"]),
                    "motion_canvas_frame_count": len(frames),
                    "motion_canvas_manifest_sha256": render.digest(manifest),
                    "repeated_motion_canvas": True,
                    "provenance": binaries,
                })
                make_report(EVIDENCE)
            finally:
                if daemon.poll() is None:
                    os.killpg(daemon.pid, signal.SIGTERM)
                    try:
                        daemon.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(daemon.pid, signal.SIGKILL)
                        daemon.wait(timeout=5)
    print(json.dumps({
        "native_cross_app_recovery_e2e": "PASS",
        "motionwright_sha": SHA, "semwright_sha": PIN,
        "evidence": str(EVIDENCE.relative_to(ROOT)),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
