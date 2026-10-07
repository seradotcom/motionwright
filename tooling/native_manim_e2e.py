#!/usr/bin/env python3
"""Exact-SHA Motionwright -> Semwright -> Manim Community round-trip acceptance.

CI-only by design. The test creates application-owned Motionwright state, provisions
Motionwright's bounded Manim Community Driver through the real Semwright Broker/Driver
Host sandbox, renders a real MP4 with Manim Community 0.21.0, and independently probes
and hashes the artifact.
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
MW_EXAMPLE = ROOT / "target" / "debug" / "examples" / "native-manim-e2e"
MW_DRIVER = ROOT / "target" / "debug" / "motionwright-manim-community-driver"
MW_RUNNER = ROOT / "target" / "debug" / "motionwright-manim-runner"
MANIM_RUNTIME = Path(
    os.environ.get("SEMWRIGHT_TEST_MANIM_RUNTIME", ROOT / ".manim-runtime")
).resolve()
PYTHON = Path(os.environ.get("SEMWRIGHT_TEST_PYTHON", shutil.which("python3") or "")).resolve()
FFMPEG = Path(os.environ.get("SEMWRIGHT_TEST_FFMPEG", shutil.which("ffmpeg") or "")).resolve()
FFPROBE = Path(os.environ.get("SEMWRIGHT_TEST_FFPROBE", shutil.which("ffprobe") or "")).resolve()
FONTCONFIG = Path("/etc/fonts").resolve()
GITHUB_SHA = os.environ.get("GITHUB_SHA", "unknown")
EVIDENCE = ROOT / "verification" / "native-manim-e2e" / GITHUB_SHA
MANIM_VERSION = "0.21.0"


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
            f"stdout={result.stdout[:32768]!r}\nstderr={result.stderr[:32768]!r}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"command returned non-JSON: {argv!r}\n"
            f"stdout={result.stdout[:32768]!r}\nstderr={result.stderr[:32768]!r}"
        ) from error


def exact_inputs() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("native Manim E2E is GitHub-Actions-only")
    observed = subprocess.check_output(
        ["git", "-C", str(SEMWRIGHT), "rev-parse", "HEAD"], text=True
    ).strip()
    if observed != PIN:
        raise SystemExit(f"Semwright checkout mismatch: expected {PIN}, got {observed}")
    for path in [
        SW_BINS / "semwright",
        SW_BINS / "semwrightd",
        SW_BINS / "semwright-sandbox",
        MW_EXAMPLE,
        MW_DRIVER,
        MW_RUNNER,
        PYTHON,
        FFMPEG,
        FFPROBE,
        FONTCONFIG / "fonts.conf",
        MANIM_RUNTIME / "runtime.json",
        MANIM_RUNTIME / "site-packages",
    ]:
        if not path.exists():
            raise SystemExit(f"missing exact acceptance input: {path}")
    if not shutil.which("bwrap"):
        raise SystemExit("bubblewrap is required; no unsandboxed fallback is accepted")

    runtime_receipt = json.loads(
        (MANIM_RUNTIME / "runtime.json").read_text(encoding="utf-8")
    )
    if runtime_receipt != {"schema": 1, "manim_version": MANIM_VERSION}:
        raise SystemExit(f"unexpected Manim runtime receipt: {runtime_receipt!r}")

    probe_env = os.environ.copy()
    probe_env.update(
        PYTHONPATH=str(MANIM_RUNTIME / "site-packages"),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    observed_manim = subprocess.check_output(
        [
            str(PYTHON),
            "-c",
            "import manim,sys;sys.stdout.write(str(manim.__version__))",
        ],
        env=probe_env,
        text=True,
    ).strip()
    if observed_manim != MANIM_VERSION:
        raise SystemExit(
            f"unexpected Manim runtime: expected {MANIM_VERSION}, got {observed_manim}"
        )


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


def probe_video(path: Path) -> dict:
    result = subprocess.run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"ffprobe failed ({result.returncode}): {result.stderr[:16384]!r}"
        )
    media = json.loads(result.stdout)
    videos = [
        stream
        for stream in media.get("streams", [])
        if stream.get("codec_type") == "video"
    ]
    if len(videos) != 1:
        raise AssertionError(f"expected one video stream, got {len(videos)}")
    video = videos[0]
    if video.get("width") != 640 or video.get("height") != 360:
        raise AssertionError(
            f"unexpected Manim dimensions: {video.get('width')}x{video.get('height')}"
        )
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
    if rate not in {"12/1", "24/2", "36/3"}:
        raise AssertionError(f"unexpected Manim frame rate: {rate}")
    duration = float(media.get("format", {}).get("duration") or 0)
    if not 0.05 <= duration <= 30:
        raise AssertionError(f"unexpected Manim duration: {duration}")
    return {
        "codec": video.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "avg_frame_rate": rate,
        "duration_seconds": duration,
        "format_name": media.get("format", {}).get("format_name"),
    }


def capability_catalog(env: dict[str, str], socket: Path, session: Path) -> list[dict]:
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
                "driver:manim-community",
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
            raise AssertionError("Manim capability catalog changed during discovery")
        page = data.get("capabilities", [])
        if not isinstance(page, list):
            raise AssertionError("Manim capability discovery returned a malformed page")
        capabilities.extend(page)
        next_offset = data.get("next_offset")
        if next_offset is None:
            break
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise AssertionError("Manim capability discovery returned an invalid cursor")
        offset = next_offset
    return capabilities


def main() -> None:
    exact_inputs()
    EVIDENCE.mkdir(parents=True, exist_ok=False)

    with tempfile.TemporaryDirectory(prefix="motionwright-native-manim-") as raw:
        root = Path(raw).resolve()
        root.chmod(0o700)
        paths: dict[str, Path] = {}
        for name in [
            "runtime",
            "state",
            "home",
            "config",
            "bin",
            "work",
            "output",
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
        )

        database = paths["motionwright-data"] / "motionwright.sqlite3"
        seeded = run_json([str(MW_EXAMPLE), "seed", str(database)], env=env)
        if seeded.get("primitive_count") != 3:
            raise AssertionError(f"unexpected Manim seed: {seeded}")

        driver = paths["bin"] / "motionwright-manim-community-driver"
        runner = paths["bin"] / "motionwright-manim-runner"
        shutil.copyfile(MW_DRIVER, driver)
        shutil.copyfile(MW_RUNNER, runner)
        driver.chmod(0o700)
        runner.chmod(0o700)

        manifest = {
            "manifest_version": 1,
            "protocol": 8,
            "id": "manim-community",
            "version": "0.1.0",
            "publisher": "motionwright",
            "executable": str(driver),
            "sha256": digest(driver),
            "application": {
                "desktop_id": None,
                "process_names": ["motionwright-manim-runner", "python3", "ffmpeg"],
                "supported_versions": [f"Manim Community {MANIM_VERSION}"],
            },
            "transport": "stdio_v1",
            "mounts": [
                {"root": "manim-runtime", "read_only": True},
                {"root": "manim-work", "read_only": False},
                {"root": "manim-output", "read_only": False},
                {"root": "fontconfig", "read_only": True},
            ],
            "system_config": [],
            "secrets": [],
            "tools": [
                {
                    "root": "manim-runner-root",
                    "name": "manim-runner",
                    "sha256": digest(runner),
                    "mounts": [
                        "manim-runtime",
                        "manim-work",
                        "manim-output",
                        "fontconfig",
                    ],
                    "dependencies": ["python3", "ffmpeg"],
                },
                {
                    "root": "python-root",
                    "name": "python3",
                    "sha256": digest(PYTHON),
                    "mounts": [],
                    "dependencies": [],
                },
                {
                    "root": "ffmpeg-root",
                    "name": "ffmpeg",
                    "sha256": digest(FFMPEG),
                    "mounts": [],
                    "dependencies": [],
                },
            ],
            "network": False,
            "loopback_port": None,
            "resources": {
                "open_files": 512,
                "processes": 256,
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
                "progress": False,
                "artifacts": False,
                "native_refs": False,
                "host_tools": True,
            },
        }
        manifest_path = paths["config"] / "manim-driver.json"
        write_private_json(manifest_path, manifest)

        grants = [
            ("manim-runtime", MANIM_RUNTIME, False),
            ("manim-work", paths["work"], True),
            ("manim-output", paths["output"], True),
            ("fontconfig", FONTCONFIG, False),
            ("manim-runner-root", runner, False),
            ("python-root", PYTHON, False),
            ("ffmpeg-root", FFMPEG, False),
        ]
        config_lines = [
            f"drivers = [{json.dumps(str(manifest_path))}]",
            "driver_network = false",
            "",
            "[policy]",
            'profile = "workspace"',
            'allow = ["driver:manim-community"]',
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
            capabilities = capability_catalog(env, socket, session)
            ids = {item.get("id") for item in capabilities}
            required = {
                "driver.manim-community.doctor",
                "driver.manim-community.render.start",
                "driver.manim-community.render.status",
                "driver.manim-community.render.cancel",
                "driver.manim-community.render.result",
            }
            if not required <= ids:
                raise AssertionError(
                    "Manim provider is missing Motionwright capabilities: "
                    + repr(sorted(required - ids))
                )
            forbidden = {
                item
                for item in ids
                if isinstance(item, str)
                and any(token in item.lower() for token in ("python", "shell", "exec"))
            }
            if forbidden:
                raise AssertionError(f"Manim provider exposed code-execution surface: {forbidden}")
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
                    "output_root": str(paths["output"]),
                    "resource": seeded["resource"],
                },
            )

            result = run_json(
                [
                    str(MW_EXAMPLE),
                    "realize",
                    str(database),
                    str(connection_path),
                    str(EVIDENCE / "motionwright-manim-evidence.json"),
                ],
                env=env,
                timeout=420,
            )
            if result.get("native_manim_e2e") != "PASS":
                raise AssertionError(f"Motionwright Manim realization did not pass: {result}")

            evidence = json.loads(
                (EVIDENCE / "motionwright-manim-evidence.json").read_text(encoding="utf-8")
            )
            artifact = evidence["result"]["artifact"]
            relative = artifact["relative_path"]
            artifact_path = paths["output"] / relative
            if not artifact_path.is_file():
                raise AssertionError(f"verified MP4 artifact is missing: {artifact_path}")
            observed_digest = digest(artifact_path)
            if observed_digest != artifact["sha256"]:
                raise AssertionError(
                    f"independent MP4 digest mismatch: {observed_digest} != {artifact['sha256']}"
                )
            media = probe_video(artifact_path)

            review_mp4 = EVIDENCE / "motionwright-manim-community.mp4"
            shutil.copyfile(artifact_path, review_mp4)
            write_private_json(
                EVIDENCE / "result.json",
                {
                    "native_manim_e2e": "PASS",
                    "motionwright_sha": GITHUB_SHA,
                    "semwright_sha": PIN,
                    "semwright_version": semwright_version(),
                    "manim_version": MANIM_VERSION,
                    "python_sha256": digest(PYTHON),
                    "ffmpeg_sha256": digest(FFMPEG),
                    "driver_sha256": digest(driver),
                    "runner_sha256": digest(runner),
                    "project": seeded,
                    "mp4": {
                        "relative_path": relative,
                        "sha256": observed_digest,
                        "bytes": artifact_path.stat().st_size,
                        **media,
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
                "native_manim_e2e": "PASS",
                "motionwright_sha": GITHUB_SHA,
                "semwright_sha": PIN,
                "evidence": str(EVIDENCE),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
