#!/usr/bin/env python3
"""Exact-SHA Motionwright -> Semwright -> Motion Canvas native render acceptance.

CI-only by design. The test creates application-owned Motionwright state, provisions
one narrowly scoped Motion Canvas driver through Semwright's Broker/Driver Host,
then renders and verifies the same project revision through ProductionCoordinator.
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
from native_av_failure_guard import NativeCommandFailure, run_sanitized_json

from native_provider_lifecycle import ProviderLifecycleSampler, summarize_render_receipts
from native_provider_live_watch import PassiveLiveBrokerWatch, public_lifecycle_kind_counts
from native_render_stage_cost import assemble_native_stage_report, elapsed_us
from native_caption_pixels import compare_caption_overlay
from native_direct_mediated_parity import (
    DirectParityError, checked_direct_artifact, compare_native_pngs,
    run_direct_native_film,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = json.loads((ROOT / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
PIN = SOURCE_LOCK["dependencies"]["semwright"]["revision"]
SEMWRIGHT = Path(os.environ.get("SEMWRIGHT_CHECKOUT", ROOT / "_semwright")).resolve()
SW_BINS = SEMWRIGHT / "target" / "debug"
MW_BIN = ROOT / "target" / "debug" / "examples" / "native-render-e2e"
RUNTIME = SEMWRIGHT / "integrations" / "motion-canvas" / "runtime"
GITHUB_SHA = os.environ.get("GITHUB_SHA", "unknown")
# A repeated CI-owned full render gets a distinct durable evidence directory,
# preserving both immutable receipts without overwriting the original run.
# This is deliberately not an arbitrary user-supplied filesystem suffix.
CAPTION_E2E = os.environ.get("MOTIONWRIGHT_NATIVE_CAPTION_E2E", "0")
if CAPTION_E2E not in {"0", "1"}:
    raise SystemExit("Unknown native caption E2E mode")
RUN_LABEL = os.environ.get("MOTIONWRIGHT_NATIVE_RUN_LABEL", "")
if RUN_LABEL not in {"", "repeat-02"}:
    raise SystemExit("Unrecognized CI-only native render evidence label")
EVIDENCE = ROOT / "verification" / "native-render-e2e" / (
    GITHUB_SHA if not RUN_LABEL else GITHUB_SHA + "-" + RUN_LABEL
)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def native_opacity_tile_rgb(frame: Path) -> bytes:
    """Decode only the source-authored 180x90 opacity-tile ROI from native PNG.

    FFmpeg is used for acceptance *inspection* of immutable output frames,
    never as an alternative renderer or image-generation backend.
    """
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(frame),
        "-vf", "crop=180:90:1150:750",
        "-frames:v", "1", "-pix_fmt", "rgb24",
        "-f", "rawvideo", "pipe:1",
    ]
    try:
        process = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AssertionError(
            "native opacity region decoder is unavailable or timed out"
        ) from error
    if process.returncode != 0 or len(process.stdout) != 180 * 90 * 3:
        raise AssertionError(
            "native opacity region could not be decoded into exact RGB24 bytes"
        )
    return process.stdout


def source_bound_cubic_roi(frame: Path, *, kind: str) -> bytes:
    """Sample an isolated source-authored native cubic-out tile via FFmpeg.

    The exact regions correspond to the two added Rust CanvasNode fixtures.
    This is inspection of original native PNGs, never a fabricated renderer.
    """
    regions = {
        "position": (1450, 775, 12, 16),
        "opacity": (1540, 912, 30, 24),
    }
    if kind not in regions:
        raise AssertionError("unrecognized source-bound cubic ROI")
    x, y, width, height = regions[kind]
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(frame),
        "-vf", f"crop={width}:{height}:{x}:{y}",
        "-frames:v", "1", "-pix_fmt", "rgb24",
        "-f", "rawvideo", "pipe:1",
    ]
    try:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AssertionError("native cubic ROI decoder unavailable") from error
    if result.returncode != 0 or len(result.stdout) != width * height * 3:
        raise AssertionError("native cubic ROI invalid or missing frame pixels")
    return result.stdout


def verify_source_cubic_frames(
    position: tuple[bytes, bytes, bytes, bytes],
    opacity: tuple[bytes, bytes, bytes],
) -> dict:
    """Reject a static or linear approximation of the two native curves."""
    p0, p2, p8, p30 = position
    o0, o15, o30 = opacity
    position_delta = mean_byte_distance(p0, p30)
    near_start = mean_byte_distance(p0, p2)
    near_end = mean_byte_distance(p8, p30)
    opacity_delta = mean_byte_distance(o0, o30)
    opacity_half_remaining = mean_byte_distance(o15, o30)
    if not (
        position_delta >= 20.0
        and near_start < position_delta * 0.4
        and near_end < position_delta * 0.4
    ):
        raise AssertionError(
            "native cubic-out position ROI is static, linear, or not aligned with original source at frames 0/2/8/30"
        )
    if not (
        opacity_delta >= 18.0
        and 0.5 <= opacity_half_remaining < opacity_delta * 0.45
    ):
        raise AssertionError(
            "native cubic-out opacity ROI is not the original rapid-out fade at frames 0/15/30"
        )
    return {
        "native_original_pixels_verified": True,
        "interpolation": "ease_out_cubic",
        "native_motion_easing": "out_cubic",
        "position_sampled_frames": [0, 2, 8, 30],
        "opacity_sampled_frames": [0, 15, 30],
        "position_full_mean_rgb_distance": round(position_delta, 3),
        "position_early_to_start_mean_rgb_distance": round(near_start, 3),
        "position_quarter_to_final_mean_rgb_distance": round(near_end, 3),
        "opacity_full_mean_rgb_distance": round(opacity_delta, 3),
        "opacity_half_to_final_mean_rgb_distance": round(opacity_half_remaining, 3),
        "pixel_difference_interpreted_as_visual_approval": False,
    }


def mean_byte_distance(left: bytes, right: bytes) -> float:
    if len(left) != len(right) or not left:
        raise AssertionError("decoded native ROI byte dimensions differ")
    return sum(abs(a - b) for a, b in zip(left, right)) / len(left)


def write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def run_json(argv: list[str], *, env: dict[str, str], timeout: int = 30) -> dict:
    return run_sanitized_json(argv, env=env, timeout=timeout)


def exact_inputs() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("native render E2E is GitHub-Actions-only")
    observed = subprocess.check_output(
        ["git", "-C", str(SEMWRIGHT), "rev-parse", "HEAD"], text=True
    ).strip()
    if observed != PIN:
        raise SystemExit(f"Semwright checkout mismatch: expected {PIN}, got {observed}")
    for path in [
        SW_BINS / "semwright",
        SW_BINS / "semwrightd",
        SW_BINS / "semwright-sandbox",
        SW_BINS / "semwright-motion-canvas-driver",
        MW_BIN,
        RUNTIME / "package-lock.json",
    ]:
        if not path.exists():
            raise SystemExit(f"missing exact acceptance input: {path}")
    if not shutil.which("bwrap"):
        raise SystemExit("bubblewrap is required; no unsandboxed fallback is accepted")


def semwright_version() -> str:
    with (SEMWRIGHT / "Cargo.toml").open("rb") as stream:
        return tomllib.load(stream)["workspace"]["package"]["version"]


def preserve_native_failure_evidence(sandbox_root: Path) -> None:
    """Retain bounded, driver-produced evidence before the temporary workspace is removed."""
    candidates = sorted(sandbox_root.rglob("native-observations.ndjson"))
    if len(candidates) != 1:
        write_private_json(
            EVIDENCE / "native-failure-files.json",
            {
                "observation_candidates": [
                    str(path.relative_to(sandbox_root)) for path in candidates[:16]
                ],
                "files": [
                    {
                        "path": str(path.relative_to(sandbox_root)),
                        "bytes": path.stat().st_size,
                    }
                    for path in sorted(sandbox_root.rglob("*"))
                    if path.is_file()
                ][:256],
            },
        )
        return
    observations = candidates[0]
    if observations.stat().st_size > 8 * 1024 * 1024:
        return
    shutil.copyfile(observations, EVIDENCE / "native-observations.ndjson")
    for name in ["native-observations-receipt.json", "artifact-manifest.json"]:
        source = observations.parent / name
        if source.is_file() and source.stat().st_size <= 1024 * 1024:
            shutil.copyfile(source, EVIDENCE / name)


def wait_for_socket(
    process: subprocess.Popen[bytes],
    socket: Path,
    log: Path,
    *,
    startup_timeout_seconds: int = 30,
) -> None:
    """Check local Broker readiness without publishing its private daemon log.

    The default 30-second window is unchanged. A test harness may explicitly
    allow a larger bounded read-only startup observation without reissuing a
    render, restart or effect grant. No arbitrary user timeout is accepted.
    This helper never opens the supplied daemon-log path or copies its bytes
    into an exception. Artifact redaction and retention are separate gates.
    """
    if type(startup_timeout_seconds) is not int or not 1 <= startup_timeout_seconds <= 120:
        raise AssertionError("invalid bounded native Broker startup observation window")
    deadline = time.monotonic() + startup_timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(
                "semwrightd exited before owner socket readiness; native_bootstrap=NOT_VERIFIED"
            )
        if socket.is_socket():
            return
        time.sleep(0.05)
    raise AssertionError("semwrightd did not expose the owner socket")


def main() -> None:
    exact_inputs()
    EVIDENCE.mkdir(parents=True, exist_ok=False)

    with tempfile.TemporaryDirectory(prefix="motionwright-native-render-") as raw:
        root = Path(raw).resolve()
        root.chmod(0o700)
        paths: dict[str, Path] = {}
        for name in [
            "runtime",
            "state",
            "home",
            "config",
            "bin",
            "project",
            "media",
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
        seeded = run_json([str(MW_BIN), "seed", str(database)], env=env)
        if (seeded["width"], seeded["height"]) != (1920, 1080):
            raise AssertionError(f"unexpected master profile: {seeded}")
        # Non-mutating app-owned Film export, exactly the same source fixture
        # that ProductionCoordinator projects during the mediated render.
        film_source = EVIDENCE / "motionwright-direct-parity-film.json"
        canonical_film = run_json(
            [str(MW_BIN), "film", str(database), str(film_source)], env=env,
        )
        if (
            canonical_film.get("direct_parity_source") != "PASS"
            or canonical_film.get("project_id") != seeded["project_id"]
            or canonical_film.get("generation") != seeded["generation"]
            or canonical_film.get("revision") != seeded["revision"]
            or canonical_film.get("frame_count") != 60
        ):
            raise AssertionError("app-owned direct parity Film is not the pinned project revision")

        driver = paths["bin"] / "semwright-motion-canvas-driver"
        node_tool = paths["bin"] / "semwright-motion-node"
        shutil.copyfile(SW_BINS / "semwright-motion-canvas-driver", driver)
        shutil.copyfile(Path(shutil.which("node") or ""), node_tool)
        driver.chmod(0o700)
        node_tool.chmod(0o700)

        manifest = {
            "manifest_version": 1,
            "protocol": 7,
            "id": "motion-canvas",
            "version": semwright_version(),
            "publisher": "semwright",
            "executable": str(driver),
            "sha256": digest(driver),
            "application": {
                "desktop_id": None,
                "process_names": ["node"],
                "supported_versions": ["Motion Canvas 3.17.2", "Node 22.22.0"],
            },
            "transport": "stdio_v1",
            "mounts": [
                {"root": "project", "read_only": False},
                {"root": "media", "read_only": True},
                {"root": "output", "read_only": False},
                {"root": "runtime", "read_only": True, "execute": True},
                {"root": "fontconfig", "read_only": True},
            ],
            "system_config": [],
            "secrets": [],
            "tools": [
                {
                    "root": "motion-node-tool",
                    "name": "motion-node",
                    "sha256": digest(node_tool),
                    "mounts": ["project", "output", "runtime", "fontconfig"],
                }
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
                "progress": True,
                "artifacts": True,
                "native_refs": False,
                "host_tools": True,
            },
        }
        manifest_path = paths["config"] / "motion-canvas-driver.json"
        write_private_json(manifest_path, manifest)

        grants = [
            ("project", paths["project"], True),
            ("media", paths["media"], False),
            ("output", paths["output"], True),
            ("runtime", RUNTIME, False),
            ("fontconfig", Path("/etc/fonts"), False),
            ("motion-node-tool", node_tool, False),
        ]
        config_lines = [
            f"drivers = [{json.dumps(str(manifest_path))}]",
            "driver_network = false",
            "",
            "[policy]",
            'profile = "observe"',
            'allow = ["driver:motion-canvas"]',
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
        # Passive, bounded lifecycle evidence on disposable CI only. The
        # sampler never inspects argv, env, sessions or rendered media and
        # cannot relaunch provider processes or jobs.
        lifecycle = ProviderLifecycleSampler(
            daemon, EVIDENCE / "native-provider-lifecycle.json", interval=2.0
        )
        lifecycle.start()
        # A post-render read can miss lifecycle transitions when semwrightd
        # dies mid-render. This one read-only owner-session subscription starts
        # BEFORE the original native render and is stopped before teardown.
        # Capture is bounded; it cannot grant effects or retry native jobs.
        live_broker_watch = (
            PassiveLiveBrokerWatch(SW_BINS / "semwright", socket, session, env=env)
            if os.environ.get("MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY") == "1"
            else None
        )

        try:
            wait_for_socket(daemon, socket, daemon_log)
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
                    "motion-canvas",
                    "--provider",
                    "driver:motion-canvas",
                ],
                env=env,
            )
            capabilities = discovered.get("data", {}).get("capabilities", [])
            if not capabilities:
                raise AssertionError("pinned Motion Canvas provider exposed no capabilities")
            if not session.is_file() or (session.stat().st_mode & 0o777) != 0o600:
                raise AssertionError("Semwright did not create a private persistent session ticket")
            if live_broker_watch is not None:
                # No second render.start, effect grant, or provider restart.
                live_broker_watch.start()

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

            mediated_started_ns = time.perf_counter_ns()
            try:
                result = run_json(
                    [
                        str(MW_BIN),
                        "render",
                        str(database),
                        str(connection_path),
                        str(EVIDENCE / "motionwright-render-evidence.json"),
                    ],
                    env=env,
                    timeout=420,
                )
            except (AssertionError, subprocess.TimeoutExpired) as error:
                if isinstance(error, NativeCommandFailure):
                    # Preserve only the existing closed, redacted CLI-failure
                    # receipt privately. The public publisher extracts an
                    # independently checked, allowlisted subset; never upload
                    # this original payload, stdout/stderr or provider refs.
                    write_private_json(
                        EVIDENCE / "native-render-failure-diagnostic.json",
                        {
                            **error.diagnostic,
                            "motionwright_source_sha": GITHUB_SHA,
                            "semwright_source_pin": PIN,
                        },
                    )
                preserve_native_failure_evidence(root)
                raise
            mediated_render_micros = elapsed_us(mediated_started_ns, time.perf_counter_ns())
            mediated_readback_started_ns = time.perf_counter_ns()
            if result.get("native_render_e2e") != "PASS" or result.get("frame_count") != 60:
                raise AssertionError(f"Motionwright render did not pass: {result}")

            artifact = result["artifact"]
            directory = artifact.get("directory")
            if not isinstance(directory, str) or not directory:
                raise AssertionError(f"render artifact has no directory: {artifact}")
            artifact_root = paths["output"] / directory
            manifest_file = artifact_root / "artifact-manifest.json"
            frames = sorted((artifact_root / "frames").glob("*.png"))
            if not manifest_file.is_file() or len(frames) != 60:
                raise AssertionError(
                    f"native artifact is incomplete: manifest={manifest_file.is_file()} "
                    f"frames={len(frames)}"
                )
            manifest_digest = digest(manifest_file)
            reported_manifest = artifact.get("manifest_sha256")
            if reported_manifest is not None and reported_manifest != manifest_digest:
                raise AssertionError(
                    f"artifact manifest digest mismatch: {reported_manifest} != {manifest_digest}"
                )

            # Independent readback of the exact SHA-256 and frame index contract
            # consumed by the bounded desktop native PNG frame preview.
            manifest_json = json.loads(manifest_file.read_text())
            indexed_frames = manifest_json.get("frames", [])
            render_plan = manifest_json.get("plan", {})
            if len(indexed_frames) != 60 or render_plan.get("frame_count") != 60:
                raise AssertionError("native preview manifest does not enumerate all frames")
            if render_plan.get("first_frame") != 0 or render_plan.get("end_frame_exclusive") != 60:
                raise AssertionError("native preview manifest has unsupported frame origin")
            for frame_index, row in enumerate(indexed_frames):
                expected = f"frames/{frame_index:06}.png"
                frame = artifact_root / expected
                if row.get("file") != expected or row.get("index") != frame_index:
                    raise AssertionError(f"native frame manifest index mismatch at {frame_index}")
                if frame.is_symlink() or not frame.is_file():
                    raise AssertionError(f"native PNG missing or replaced by link at {frame_index}")
                if row.get("bytes") != frame.stat().st_size or row.get("sha256") != digest(frame):
                    raise AssertionError(f"native frame digest/size mismatch at {frame_index}")

            # The seeded X/Y tile starts at x=640 and finishes at its
            # unchanged base x=790 on native frame 30. A completely static
            # or silently flattened render would yield identical PNG bytes.
            first_digest = digest(frames[0])
            middle_digest = digest(frames[30])
            if first_digest == middle_digest:
                raise AssertionError(
                    "native frame 0 and frame 30 are identical: the authored linear position motion may have been lost"
                )

            # Independent visual proof of the same-source linear opacity
            # entrance: an isolated ROI is measured across three native
            # frame-indexed PNGs. No generated preview or synthetic pixels.
            fade_start = native_opacity_tile_rgb(frames[0])
            fade_half = native_opacity_tile_rgb(frames[15])
            fade_end = native_opacity_tile_rgb(frames[30])
            fade_full_distance = mean_byte_distance(fade_start, fade_end)
            fade_half_distance = mean_byte_distance(fade_half, fade_end)
            fade_initial_to_half = mean_byte_distance(fade_start, fade_half)
            if not (
                fade_full_distance >= 18.0
                and fade_initial_to_half >= 8.0
                and 5.0 <= fade_half_distance < fade_full_distance * 0.85
            ):
                raise AssertionError(
                    "native opacity tile ROI does not show a bounded progressive 0-to-1 fade across native frames 0/15/30"
                )

            # Two MORE source-authored tiles prove the native cubic-out
            # path itself, not merely that the Film serialized an easing tag.
            # The old linear tile/ROI checks above remain unchanged.
            cubic_evidence = verify_source_cubic_frames(
                tuple(source_bound_cubic_roi(frames[i], kind="position")
                      for i in (0, 2, 8, 30)),
                tuple(source_bound_cubic_roi(frames[i], kind="opacity")
                      for i in (0, 15, 30)),
            )
            mediated_readback_micros = elapsed_us(
                mediated_readback_started_ns, time.perf_counter_ns()
            )

            # A SECOND, DISTINCT, explicitly requested CI-only render follows
            # the direct Semwright Broker/Driver Host path, using precisely the
            # Film exported from the same Motionwright project/generation/
            # revision. This is a separate conformance run, never an automatic
            # retry of the original mediated render. Any uncertain observation
            # fails closed and preserves the original job identity.
            direct = run_direct_native_film(
                film_file=film_source,
                executable=SW_BINS / "semwright",
                socket=socket,
                session=session,
                env=env,
                mediated_job_ref=result["job_ref"],
                expected_frames=60,
            )
            direct_root = checked_direct_artifact(paths["output"], direct["artifact"])
            if direct_root == artifact_root:
                raise AssertionError("direct native conformance reused the mediated artifact")
            direct_manifest_sha = digest(direct_root / "artifact-manifest.json")
            claimed_direct_sha = direct["artifact"].get("manifest_sha256")
            if claimed_direct_sha is not None and claimed_direct_sha != direct_manifest_sha:
                raise AssertionError("direct native manifest digest was not source-bound")
            # Preserve a few separately inspectable, manifest-bound native
            # frame pairs even if the *pixel* gate fails. They are genuine
            # same-fixture source PNGs, not generated diagnostics.
            for frame_index in [0, 15, 30, 59]:
                frame_file = f"{frame_index:06}.png"
                shutil.copyfile(
                    artifact_root / "frames" / frame_file,
                    EVIDENCE / f"mediated-native-frame-{frame_index:06}.png",
                )
                shutil.copyfile(
                    direct_root / "frames" / frame_file,
                    EVIDENCE / f"direct-native-frame-{frame_index:06}.png",
                )
            parity_started_ns = time.perf_counter_ns()
            try:
                parity = compare_native_pngs(artifact_root, direct_root, expected_frames=60)
            except DirectParityError as error:
                write_private_json(
                    EVIDENCE / "native-direct-mediated-diagnostic.json",
                    {
                        "schema": "motionwright.native-direct-mediated-diagnostic/1",
                        "result": "FAIL",
                        "source_sha": GITHUB_SHA,
                        "semwright_sha": PIN,
                        "film_sha256": direct["film_sha256"],
                        "native_manifest_sha256": {
                            "mediated": manifest_digest,
                            "direct": direct_manifest_sha,
                        },
                        "reason": str(error),
                        "native_job_retry": "FORBIDDEN",
                        "source_pixel_equivalence": "NOT_PROVEN",
                        "human_visual_acceptance": "NOT_RUN",
                    },
                )
                raise
            parity_micros = elapsed_us(parity_started_ns, time.perf_counter_ns())
            write_private_json(
                EVIDENCE / "native-render-stage-cost.json",
                assemble_native_stage_report(
                    source_sha=GITHUB_SHA,
                    semwright_sha=PIN,
                    film_sha256=direct["film_sha256"],
                    mediated_job=result["job_ref"],
                    direct_is_separate_job=direct["independent_job_confirmed"],
                    mediated_micros=mediated_render_micros,
                    manifest_review_micros=mediated_readback_micros,
                    direct_micros=direct["measured_direct_total_micros"],
                    parity_micros=parity_micros,
                    direct_stage_micros=direct["measured_direct_substages_micros"],
                    frame_comparison=parity,
                ),
            )
            write_private_json(
                EVIDENCE / "native-direct-mediated-parity.json",
                {
                    "schema": "motionwright-native-direct-mediated-parity/1",
                    "motionwright_sha": GITHUB_SHA,
                    "semwright_sha": PIN,
                    "project_generation": seeded["generation"],
                    "project_revision": seeded["revision"],
                    "source_film_sha256": direct["film_sha256"],
                    "direct_mode": direct["scope"],
                    "mediated_mode": "motionwright_studio_service_native_sdk_coordinator",
                    "direct_job_distinct_from_mediated": direct["independent_job_confirmed"],
                    "direct_native_validation_checks": direct["native_validation_checks"],
                    "direct_status_read_count": direct["status_reads"],
                    "native_manifest_sha256": {
                        "direct": direct_manifest_sha,
                        "mediated": manifest_digest,
                    },
                    "compared_properties": [
                        "exact_linear_xy_position",
                        "exact_linear_opacity_fade",
                        "exact_out_cubic_xy_position",
                        "exact_out_cubic_opacity_fade",
                        "static_geometry_and_native_text",
                    ],
                    "pixels": parity,
                    "human_visual_acceptance": "NOT_RUN",
                    "arbitrary_renderer_property_parity": "NOT_PROVEN",
                },
            )

            shutil.copyfile(manifest_file, EVIDENCE / "artifact-manifest.json")
            for source, name in [
                (frames[0], "frame-first.png"),
                (frames[len(frames) // 2], "frame-middle.png"),
                (frames[-1], "frame-last.png"),
            ]:
                shutil.copyfile(source, EVIDENCE / name)

            write_private_json(
                EVIDENCE / "result.json",
                {
                    "native_render_e2e": "PASS",
                    "motionwright_sha": GITHUB_SHA,
                    "semwright_sha": PIN,
                    "semwright_version": semwright_version(),
                    "project": seeded,
                    "driver_sha256": digest(driver),
                    "node_sha256": digest(node_tool),
                    "artifact_directory": directory,
                    "artifact_manifest_sha256": manifest_digest,
                    "frame_count": len(frames),
                    "linear_position_motion_sampled": True,
                    "linear_opacity_motion_sampled": True,
                    "native_cubic_out_region": cubic_evidence,
                    "direct_mediated_native_frame_parity": "PASS",
                    "direct_mediated_same_film_sha256": direct["film_sha256"],
                    "native_linear_opacity_region": {
                        "scope": "actual_pinned_motion_canvas_png_rgb24_region_only",
                        "rect": [1150, 750, 180, 90],
                        "frames": [0, 15, 30],
                        "rgb24_sha256": [
                            hashlib.sha256(roi).hexdigest()
                            for roi in [fade_start, fade_half, fade_end]
                        ],
                        "mean_byte_distance": {
                            "start_to_end": round(fade_full_distance, 6),
                            "middle_to_end": round(fade_half_distance, 6),
                            "start_to_middle": round(fade_initial_to_half, 6),
                        },
                    },
                    "native_motion_frame_sha256": {
                        "start": first_digest,
                        "end_of_linear_tween": middle_digest,
                    },
                    "review_frames": {
                        "first_sha256": digest(EVIDENCE / "frame-first.png"),
                        "middle_sha256": digest(EVIDENCE / "frame-middle.png"),
                        "last_sha256": digest(EVIDENCE / "frame-last.png"),
                    },
                },
            )

            if CAPTION_E2E == "1":
                # A second, explicitly authored NEW revision of the same
                # canonical owner project; never a retry of the baseline job.
                source = run_json(
                    [str(MW_BIN), "caption-prepare", str(database),
                     str(paths["motionwright-data"] / "source-captions-tone.wav")],
                    env=env, timeout=35,
                )
                if (
                    source.get("caption_source") != "PASS"
                    or source.get("project_id") != seeded["project_id"]
                    or source.get("generation") != seeded["generation"]
                    or source.get("revision", 0) <= seeded["revision"]
                    or source.get("native_cue_count") != 2
                    or source.get("render_count") != 60
                    or len(source.get("voice_sha256", "")) != 64
                ):
                    raise AssertionError("second caption revision is not source-bound")
                try:
                    captioned = run_json(
                        [str(MW_BIN), "render", str(database), str(connection_path),
                         str(EVIDENCE / "caption-motionwright-render-evidence.json")],
                        env=env, timeout=420,
                    )
                except NativeCommandFailure as failure:
                    # Print ONLY fields from the existing closed-enum,
                    # secret-redacting classifier. Do not report provider
                    # error messages, receipts, ticket paths or raw stdout.
                    safe = {
                        key: failure.diagnostic[key]
                        for key in (
                            "reason", "reported_error_code", "source_outcome",
                            "cli_exit_category", "provider_generation_inactive_observed",
                            "original_render_start_receipts_observed",
                        )
                    }
                    write_private_json(EVIDENCE / "caption-native-failure-summary.json", {
                        "schema": "motionwright-native-caption-failure/1",
                        "outcome": "NOT_VERIFIED", "retry": "FORBIDDEN",
                        "source_sha": GITHUB_SHA, "semwright_pin": PIN,
                        "closed_diagnostic": safe,
                    })
                    print("caption-native-failure-class=" + json.dumps(
                        safe, sort_keys=True
                    ), flush=True)
                    raise AssertionError(
                        "Native caption provider outcome NOT_VERIFIED; no retry dispatched"
                    ) from None
                except subprocess.TimeoutExpired:
                    write_private_json(EVIDENCE / "caption-native-failure-summary.json", {
                        "schema": "motionwright-native-caption-failure/1",
                        "outcome": "UNKNOWN", "retry": "FORBIDDEN",
                        "source_sha": GITHUB_SHA, "semwright_pin": PIN,
                    })
                    raise AssertionError(
                        "Native caption subprocess deadline UNKNOWN; no retry dispatched"
                    ) from None
                if (
                    captioned.get("native_render_e2e") != "PASS"
                    or captioned.get("frame_count") != 60
                    or captioned.get("revision") != source["revision"]
                    or captioned.get("generation") != source["generation"]
                    or captioned.get("project_id") != source["project_id"]
                    or captioned.get("job_ref") == result.get("job_ref")
                ):
                    raise AssertionError(
                        "caption render failed exact source revision or independent-job binding"
                    )
                caption_artifact = captioned.get("artifact", {})
                caption_dir = caption_artifact.get("directory")
                if not isinstance(caption_dir, str) or not caption_dir:
                    raise AssertionError("native caption artifact is missing")
                # Same owner output root; bound manifest/frames from original
                # provider are read independently by FFmpeg (not an overlay).
                caption_path = paths["output"] / caption_dir
                checked = compare_caption_overlay(
                    baseline_root=artifact_root,
                    captioned_root=caption_path,
                    expected_cue_count=source["native_cue_count"],
                )
                checked.update({
                    "motionwright_source_sha": GITHUB_SHA,
                    "semwright_source_sha": PIN,
                    "project_id": source["project_id"],
                    "generation": source["generation"],
                    "source_revision": source["revision"],
                    "caption_job_separate_from_baseline": True,
                    "effect_grant_or_retry_inferred": False,
                    "speech_identity_or_content_verified": False,
                })
                write_private_json(
                    EVIDENCE / "caption-native-decoded-pixel-evidence.json", checked
                )
                # No owner session, private receipt, WAV, transcript or original
                # pixel bytes ever enter the public workflow artifact.
                print(
                    "caption-native-pixels=PASS source-sha=" + GITHUB_SHA
                    + " frames=0,22,42,59 separate-native-owner-revision=1"
                )
        finally:
            # Preserve only source-bound error classes and status timestamps.
            # Even malformed optional diagnostic input cannot prevent proper
            # cleanup or replace the original render error.
            try:
                diagnostic = EVIDENCE / "motionwright-render-diagnostic.json"
                if diagnostic.is_file() and 0 < diagnostic.stat().st_size <= 2 * 1024 * 1024:
                    try:
                        incident = json.loads(diagnostic.read_text(encoding="utf-8"))
                        summary = summarize_render_receipts(incident.get("receipts"))
                        write_private_json(
                            EVIDENCE / "provider-status-reconciliation.json", summary
                        )
                    except (OSError, ValueError, TypeError, UnicodeError) as error:
                        write_private_json(EVIDENCE / "provider-status-reconciliation.json", {
                            "schema": "motionwright-ci-render-status-reconciliation/1",
                            "render_outcome": "not_proven",
                            "diagnostic_parse_error_class": type(error).__name__,
                        })
                if live_broker_watch is not None:
                    try:
                        live_events = live_broker_watch.finish()
                        live_events["motionwright_source_sha"] = GITHUB_SHA
                        live_events["semwright_source_pin"] = PIN
                        write_private_json(
                            EVIDENCE / "native-provider-live-broker-events.json",
                            live_events,
                        )
                        # Only closed-enum event COUNTS survive in public CI
                        # logs; native refs, generations and raw watch output
                        # remain inside ephemeral private CI evidence.
                        safe_counts = public_lifecycle_kind_counts(live_events)
                        print(
                            "v01-provider-live-native-render-events="
                            + live_events["observation"]
                            + " counts=" + json.dumps(safe_counts, sort_keys=True)
                            + " early_exit="
                            + str(live_events["subscriber_exited_before_finalize"])
                        )
                    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
                        print(
                            "v01-provider-live-native-render-events="
                            "UNVERIFIED_OPTIONAL_OBSERVATION"
                        )
                lifecycle.stop()
            finally:
                try:
                    if daemon.poll() is None:
                        pid = daemon.pid
                        daemon.terminate()
                        try:
                            daemon.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(pid, signal.SIGKILL)
                            daemon.wait(timeout=5)
                finally:
                    log_stream.close()

    print(
        json.dumps(
            {
                "native_render_e2e": "PASS",
                "motionwright_sha": GITHUB_SHA,
                "semwright_sha": PIN,
                "evidence": str(EVIDENCE.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
