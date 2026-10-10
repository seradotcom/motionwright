#!/usr/bin/env python3
"""Exact-SHA source-bound two-segment Motion Canvas / MLT FFV1 video E2E.

CI only. Provisions a 33-scene application-owned project and pinned Semwright
Broker/Driver Host, renders 32+1 real Motion Canvas segments, and assembles
FFV1/PCM Matroska lossless intermediary through semantic MLT, not an AAC master.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from mlt_failure_diagnostic import safe_observation_diagnostic

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = json.loads((ROOT / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
PIN = SOURCE_LOCK["dependencies"]["semwright"]["revision"]
SEMWRIGHT = Path(os.environ.get("SEMWRIGHT_CHECKOUT", ROOT / "_semwright")).resolve()
SW_BINS = SEMWRIGHT / "target" / "debug"
MW_BIN = ROOT / "target" / "debug" / "examples" / "native-mlt-sequence-e2e"
RUNTIME = SEMWRIGHT / "integrations" / "motion-canvas" / "runtime"
GITHUB_SHA = os.environ.get("GITHUB_SHA", "unknown")
EVIDENCE = ROOT / "verification" / "native-mlt-sequence-e2e" / GITHUB_SHA
MODE = os.environ.get("MOTIONWRIGHT_MLT_MODE", "pcm")
if MODE not in {"pcm", "video-only", "final-mp4"}:
    raise SystemExit("Only closed native MLT smoke modes are accepted")
VIDEO_ONLY = MODE in {"video-only", "final-mp4"}
FINAL_MASTER = MODE == "final-mp4"
PROFILE = "lossless-video-only" if VIDEO_ONLY else "lossless"
EXPECTED_SCOPE = (
    "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master"
    if VIDEO_ONLY else "actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master"
)
VIDEO_FILE = "native-two-segment-video-only.mkv" if VIDEO_ONLY else "native-two-segment-lossless.mkv"


def inspect_decoded_ci_stereo(pcm: bytes) -> dict:
    """Independent decoded-content evidence for the actual 33-frame CI
    source. The 48 kHz stereo WAV authored by the Rust E2E contains distinct
    400 Hz left and 600 Hz right sawtooth fundamentals. AAC stream headers,
    MP4 byte hashes or a nonempty decoded WAV alone do NOT prove the actual
    speaker content was retained and not swapped, duplicated or muted.
    """
    if len(pcm) % 4:
        raise AssertionError("Decoded H.264/AAC master has partial stereo PCM sample")
    frames = len(pcm) // 4
    if not 52_800 <= frames <= 53_824:
        raise AssertionError("Decoded AAC samples exceed one bounded access unit")
    start = 4_800
    end = min(48_000, frames - 2_048)
    if end - start < 24_000:
        raise AssertionError("Decoded AAC lacks a stable measured content window")

    def fundamental(channel: int, frequency: int) -> float:
        cosine = sine = 0.0
        count = 0
        for frame in range(start, end, 8):
            sample = struct.unpack_from("<hh", pcm, frame * 4)[channel] / 32_768.0
            phase = 2.0 * math.pi * frequency * frame / 48_000
            cosine += sample * math.cos(phase)
            sine += sample * math.sin(phase)
            count += 1
        return 2.0 * math.hypot(cosine, sine) / count

    left_400 = fundamental(0, 400)
    left_600 = fundamental(0, 600)
    right_400 = fundamental(1, 400)
    right_600 = fundamental(1, 600)
    if (left_400 < 0.08 or right_600 < 0.06
        or left_400 < 4 * max(left_600, 1e-7)
        or right_600 < 4 * max(right_400, 1e-7)):
        raise AssertionError("Actual decoded AAC lost distinct authored stereo CI signals")
    return {
        "decoded_master_pcm_frames": frames,
        "decoded_stereo_content": "distinct_source_400hz_left_600hz_right",
        "left_400_amplitude": round(left_400, 5),
        "right_600_amplitude": round(right_600, 5),
    }


def inspect_native_master_decoded_content(master: Path, ffmpeg: Path, env: dict) -> dict:
    """Decode the real signed-off owner MP4 using fixed CI ffmpeg arguments;
    no application runtime, local backend or user-controlled filters.
    Temp disk output ensures even a hostile decoder cannot grow Python RAM.
    """
    with tempfile.TemporaryFile() as output:
        decoded = subprocess.run(
            [str(ffmpeg), "-nostdin", "-v", "error", "-i", str(master),
             "-map", "0:a:0", "-acodec", "pcm_s16le", "-ac", "2",
             "-ar", "48000", "-f", "s16le", "-"],
            env=env, stdout=output, stderr=subprocess.PIPE,
            timeout=45, check=False,
        )
        if decoded.returncode != 0:
            raise AssertionError("Independent bounded CI AAC-to-PCM decoder failed")
        output.seek(0, 2)
        size = output.tell()
        if not (52_800 * 4 <= size <= 53_824 * 4):
            raise AssertionError("Decoded source speaker PCM exceeded the AV clock budget")
        output.seek(0)
        pcm = output.read(53_824 * 4 + 1)
        if len(pcm) != size:
            raise AssertionError("Decoded AV speaker bytes changed during bounded read")
        return inspect_decoded_ci_stereo(pcm)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def run_json(argv: list[str], *, env: dict[str, str], timeout: int = 30) -> dict:
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
            f"stdout={result.stdout[:8192]!r}\nstderr={result.stderr[:8192]!r}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"command returned non-JSON: {argv!r}\n"
            f"stdout={result.stdout[:8192]!r}\nstderr={result.stderr[:8192]!r}"
        ) from error


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
        SW_BINS / "semwright-mlt-video-driver",
        SW_BINS / "semwright-mlt-runtime-runner",
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


def wait_for_socket(process: subprocess.Popen[bytes], socket: Path, log: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(log.read_text(encoding="utf-8", errors="replace"))
        if socket.is_socket():
            return
        time.sleep(0.05)
    raise AssertionError("semwrightd did not expose the owner socket")


def main() -> None:
    exact_inputs()
    EVIDENCE.mkdir(parents=True, exist_ok=False)

    with tempfile.TemporaryDirectory(prefix="motionwright-native-av-") as raw:
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
        )

        database = paths["motionwright-data"] / "motionwright.sqlite3"
        seeded = run_json([str(MW_BIN), "seed", str(database)], env=env)
        if ((seeded["width"], seeded["height"]) != (1280, 720)
            or (seeded["fps_num"], seeded["fps_den"]) != (30, 1)
            or seeded["scene_count"] != 33
            or seeded["deliverable_count"] != 3):
            raise AssertionError(f"unexpected real default profiles or source scenes: {seeded}")

        driver = paths["bin"] / "semwright-motion-canvas-driver"
        node_tool = paths["bin"] / "semwright-motion-node"
        mlt_driver = paths["bin"] / "semwright-mlt-video-driver"
        mlt_runner = paths["bin"] / "semwright-mlt-runtime-runner"
        shutil.copyfile(SW_BINS / "semwright-motion-canvas-driver", driver)
        shutil.copyfile(Path(shutil.which("node") or ""), node_tool)
        shutil.copyfile(SW_BINS / "semwright-mlt-video-driver", mlt_driver)
        shutil.copyfile(SW_BINS / "semwright-mlt-runtime-runner", mlt_runner)
        for executable in [driver, node_tool, mlt_driver, mlt_runner]:
            executable.chmod(0o700)

        melt = Path(shutil.which("melt") or "").resolve()
        ffprobe = Path(shutil.which("ffprobe") or "").resolve()
        ffmpeg = Path(shutil.which("ffmpeg") or "").resolve()
        for executable in [melt, ffprobe, ffmpeg]:
            if not executable.is_file():
                raise SystemExit(f"missing pinned MLT runtime tool: {executable}")
        mlt_runtime = melt.parent.parent.resolve()

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

        mlt_manifest = {
            "manifest_version": 1,
            "protocol": 7,
            "id": "mlt-video",
            "version": semwright_version(),
            "publisher": "semwright",
            "executable": str(mlt_driver),
            "sha256": digest(mlt_driver),
            "application": {
                "desktop_id": None,
                "process_names": ["melt"],
                "supported_versions": [],
            },
            "transport": "stdio_v1",
            "mounts": [
                {"root": "project", "read_only": True},
                {"root": "media", "read_only": True},
                {"root": "output", "read_only": False},
                {"root": "mlt-runtime", "read_only": True, "execute": True},
                {"root": "scratch", "read_only": False},
            ],
            "system_config": [],
            "secrets": [],
            "tools": [
                {
                    "root": "mlt-runner-root",
                    "name": "mlt-runner",
                    "sha256": digest(mlt_runner),
                    "mounts": ["mlt-runtime", "scratch", "project", "media", "output"],
                    "dependencies": ["melt", "ffprobe", "ffmpeg"],
                },
                {
                    "root": "melt-root",
                    "name": "melt",
                    "sha256": digest(melt),
                    "mounts": [],
                    "dependencies": [],
                },
                {
                    "root": "ffprobe-root",
                    "name": "ffprobe",
                    "sha256": digest(ffprobe),
                    "mounts": [],
                    "dependencies": [],
                },
                {
                    "root": "ffmpeg-root",
                    "name": "ffmpeg",
                    "sha256": digest(ffmpeg),
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
                "cooperative_cancellation": False,
                "events": False,
                "health": True,
                "progress": False,
                "artifacts": False,
                "native_refs": False,
                "host_tools": True,
            },
        }
        mlt_manifest_path = paths["config"] / "mlt-video-driver.json"
        write_private_json(mlt_manifest_path, mlt_manifest)

        grants = [
            ("project", paths["project"], True),
            ("media", paths["media"], False),
            ("output", paths["output"], True),
            ("runtime", RUNTIME, False),
            ("fontconfig", Path("/etc/fonts"), False),
            ("motion-node-tool", node_tool, False),
            ("mlt-runtime", mlt_runtime, False),
            ("scratch", paths["scratch"], True),
            ("mlt-runner-root", mlt_runner, False),
            ("melt-root", melt, False),
            ("ffprobe-root", ffprobe, False),
            ("ffmpeg-root", ffmpeg, False),
        ]
        config_lines = [
            "drivers = [" + ", ".join(
                json.dumps(str(path)) for path in [manifest_path, mlt_manifest_path]
            ) + "]",
            "driver_network = false",
            "",
            "[policy]",
            'profile = "observe"',
            'allow = ["driver:motion-canvas", "driver:mlt-video"]',
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
            required_mlt = (
                "driver.mlt-video.frames.encode",
                "driver.mlt-video.project.create",
                "driver.mlt-video.sequence.create",
                "driver.mlt-video.sequence.list",
                "driver.mlt-video.track.create",
                "driver.mlt-video.track.list",
                "driver.mlt-video.asset.import",
                "driver.mlt-video.clip.insert",
                "driver.mlt-video.sequence.duration",
                "driver.mlt-video.render.plan",
                "driver.mlt-video.render.start",
                "driver.mlt-video.render.status",
                "driver.mlt-video.render.result",
                "driver.mlt-video.project.close",
            )
            for capability_name in required_mlt:
                described = run_json(
                    [
                        str(SW_BINS / "semwright"),
                        "--socket",
                        str(socket),
                        "--session-file",
                        str(session),
                        "--json",
                        "capabilities",
                        "describe",
                        capability_name,
                    ],
                    env=env,
                )
                payload = described.get("data", {})
                capability = payload.get("capability", {})
                provenance = payload.get("provenance", {})
                if capability.get("name") != capability_name:
                    raise AssertionError(
                        f"pinned MLT capability describe mismatch: {capability_name}: {capability}"
                    )
                if provenance.get("provider") != "driver:mlt-video":
                    raise AssertionError(
                        f"pinned MLT capability has unexpected provider: "
                        f"{capability_name}: {provenance}"
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
                    "output_root": str(paths["output"]),
                    "resource": seeded["resource"],
                },
            )

            try:
                result = run_json(
                    [
                        str(MW_BIN),
                        "assemble",
                        str(database),
                        str(connection_path),
                        str(EVIDENCE / "native-multisegment-timeline-evidence.json"),
                    ],
                    env=env,
                    timeout=1050,
                )
            except (AssertionError, subprocess.TimeoutExpired):
                # The pinned renderer's raw failure receipt contains a local
                # stack and owner paths: NEVER print or upload that receipt.
                # Persist only a fixed vocabulary derived before the owner's
                # temporary, private output root is destroyed.
                safe = safe_observation_diagnostic(paths["output"])
                try:
                    write_private_json(EVIDENCE / "safe-observation-failure.json", safe)
                except OSError:
                    pass  # Never replace the original native failure.
                print(json.dumps(safe, sort_keys=True), file=sys.stderr)
                raise
            if result.get("native_mlt_multisegment_e2e") != "PASS":
                raise AssertionError("Real source-bound MLT timeline was not successful: " + repr(result))
            if result.get("frame_count") != 33 or result.get("segment_count") != 2:
                raise AssertionError("Native timeline lost the 32+1 source partition")
            if result.get("evidence_scope") != EXPECTED_SCOPE or result.get("native_render_profile") != PROFILE:
                raise AssertionError("MLT result falsely claimed final H.264/AAC master")

            evidence_path = EVIDENCE / "native-multisegment-timeline-evidence.json"
            evidence = json.loads(evidence_path.read_text())
            if (evidence.get("provider_project_cleanup") != "not_requested_requires_foreground_broker_consent"
                or result.get("provider_project_cleanup") != "not_requested_requires_foreground_broker_consent"):
                raise AssertionError("Native MLT receipt did not disclose pending foreground-only cleanup")
            if evidence.get("evidence_scope") != EXPECTED_SCOPE or evidence.get("native_render_profile") != PROFILE:
                raise AssertionError("MLT output identity and exact closed profile diverged")
            if evidence.get("transport_pcm_audio") is VIDEO_ONLY:
                raise AssertionError("Native MLT claimed wrong audio presence")
            if VIDEO_ONLY and evidence.get("native_frame_count_observed") != 33:
                raise AssertionError("New native video-only profile lacks actual decoded frame count")
            if evidence.get("source", {}).get("total_frames") != 33:
                raise AssertionError("Real FFV1 intermediate frame total was altered")
            records = evidence.get("source", {}).get("verified_video_segments", [])
            if len(records) != 2 or [item["frame_count"] for item in records] != [32, 1]:
                raise AssertionError("Real native source FFV1s are not the exact two source segments")
            if [item["output_start_frame"] for item in records] != [0, 32]:
                raise AssertionError("Native output clip start frames were not preserved")
            for record in records:
                artifact = paths["output"] / record["artifact_path"]
                if (not artifact.is_file() or artifact.stat().st_size != record["artifact_bytes"]
                    or digest(artifact) != record["artifact_sha256"]):
                    raise AssertionError("Native source FFV1 bytes changed since Semwright verification")

            video_path = paths["output"] / result["artifact_path"]
            if not video_path.is_file() or video_path.stat().st_size != result["artifact_bytes"]:
                raise AssertionError("Native MLT timeline artifact does not exist with measured size")
            if digest(video_path) != result["artifact_sha256"]:
                raise AssertionError("Native MLT timeline SHA-256 mismatch")
            meta_result = subprocess.run(
                [
                    str(ffprobe), "-v", "error", "-count_frames",
                    "-show_entries", "stream=codec_name,codec_type,width,height,nb_read_frames,r_frame_rate",
                    "-of", "json", str(video_path),
                ],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=30, check=True,
            )
            streams = json.loads(meta_result.stdout)["streams"]
            videos = [item for item in streams if item.get("codec_type") == "video"]
            audios = [item for item in streams if item.get("codec_type") == "audio"]
            if len(videos) != 1 or len(audios) != (0 if VIDEO_ONLY else 1):
                raise AssertionError("MLT native render audio presence differs from exact closed profile")
            if not VIDEO_ONLY and audios[0].get("codec_name") != "pcm_s16le":
                raise AssertionError("Native MLT PCM transport audio differs")
            if result.get("transport_pcm_audio") is VIDEO_ONLY:
                raise AssertionError("MLT result contradicted source-bound profile")
            if evidence.get("native_frame_count_observed") not in (None, 33):
                raise AssertionError("MLT native frame count observation contradicted source")
            video = videos[0]
            if (video.get("codec_name") != "ffv1"
                or (video.get("width"), video.get("height")) != (1280, 720)
                or video.get("r_frame_rate") != "30/1"
                or video.get("nb_read_frames") != "33"):
                raise AssertionError("Native actual MLT video codec/frame provenance failed: " + repr(video))

            shutil.copyfile(video_path, EVIDENCE / VIDEO_FILE)
            master_check = {}
            if FINAL_MASTER:
                av_receipt = json.loads(
                    (EVIDENCE / "native-multisegment-av-master-evidence.json").read_text()
                )
                if (av_receipt["frame_count"] != 33
                    or av_receipt["measured_voice"]["audio_pcm_frames"] != 52_800
                    or av_receipt["evidence_scope"]
                       != "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved"):
                    raise AssertionError("Native final AV master source/voice provenance differs")
                relative = result.get("master_artifact_path")
                if not isinstance(relative, str) or not relative.endswith(".mp4"):
                    raise AssertionError("Native master has no bounded MP4 output")
                master = paths["output"] / relative
                if not master.is_file():
                    raise AssertionError("Native final AV mux did not publish a real MP4")
                if (master.stat().st_size != result.get("master_artifact_bytes")
                    or digest(master) != result.get("master_artifact_sha256")
                    or digest(master) != av_receipt["master"]["artifact"]["sha256"]):
                    raise AssertionError("Actual MP4 bytes do not match both native receipts")
                mux_probe = subprocess.run(
                    [
                        str(ffprobe), "-v", "error", "-count_frames",
                        "-show_entries",
                        "stream=codec_type,codec_name,nb_read_frames,width,height,r_frame_rate,sample_rate,channels",
                        "-of", "json", str(master),
                    ],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=45, check=True,
                )
                av_streams = json.loads(mux_probe.stdout)["streams"]
                if len(av_streams) != 2:
                    raise AssertionError("Final MP4 must have exactly H264 video and AAC stereo audio")
                v = [stream for stream in av_streams if stream.get("codec_type") == "video"]
                a = [stream for stream in av_streams if stream.get("codec_type") == "audio"]
                if (len(v) != 1 or len(a) != 1 or v[0].get("codec_name") != "h264"
                    or v[0].get("nb_read_frames") != "33"
                    or (v[0].get("width"),v[0].get("height")) != (1280,720)
                    or v[0].get("r_frame_rate") != "30/1"
                    or a[0].get("codec_name") != "aac"
                    or a[0].get("sample_rate") != "48000"
                    or a[0].get("channels") != 2):
                    raise AssertionError("Independent MP4 decoder did not verify exact AV output")
                decoded = av_receipt["decoded_audio"]
                wav_path = paths["output"] / decoded["path"]
                if not wav_path.is_file() or digest(wav_path) != decoded["sha256"]:
                    raise AssertionError("MLT native decoded PCM audit is missing or changed")
                # The actual MP4, not the receipt or source WAV, must retain
                # the authored, distinct 400/600 Hz left/right test signals.
                decoded_content = inspect_native_master_decoded_content(master, ffmpeg, env)
                write_private_json(EVIDENCE / "decoded-stereo-content-proof.json", decoded_content)
                shutil.copyfile(master, EVIDENCE / "native-multisegment-master.mp4")
                master_check = {
                    "final_master_frames":33,
                    "final_master_video_codec":"h264",
                    "final_master_audio_codec":"aac",
                    "final_master_audio_rate":48000,
                    "final_master_audio_channels":2,
                    "final_master_sha256":digest(master),
                    "final_master_bytes":master.stat().st_size,
                    "measured_voice_pcm_frames":52_800,
                    "decoded_audio_sha256":decoded["sha256"],
                    "final_evidence_scope":av_receipt["evidence_scope"],
                    **decoded_content,
                }
            write_private_json(
                EVIDENCE / "result.json",
                {
                    "native_mlt_multisegment_e2e": "PASS",
                    "motionwright_sha": GITHUB_SHA,
                    "semwright_sha": PIN,
                    "semwright_version": semwright_version(),
                    "project": seeded,
                    "motion_canvas_driver_sha256": digest(driver),
                    "mlt_driver_sha256": digest(mlt_driver),
                    "mlt_runner_sha256": digest(mlt_runner),
                    "melt_sha256": digest(melt),
                    "ffprobe_sha256": digest(ffprobe),
                    "rendered_source_frames": [32, 1],
                    "assembled_video_frames": 33,
                    "mode": MODE,
                    "evidence_scope": EXPECTED_SCOPE,
                    "native_render_profile": PROFILE,
                    "provider_project_cleanup": evidence["provider_project_cleanup"],
                    "assembled_video_codec": "ffv1",
                    "assembled_video_has_audio": not VIDEO_ONLY,
                    "transport_audio_codec": None if VIDEO_ONLY else "pcm_s16le",
                    "video_bytes": video_path.stat().st_size,
                    "video_sha256": digest(video_path),
                    **master_check,
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

    print(
        json.dumps(
            {
                "native_mlt_multisegment_e2e": "PASS",
                "motionwright_sha": GITHUB_SHA,
                "semwright_sha": PIN,
                "evidence": str(EVIDENCE.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
