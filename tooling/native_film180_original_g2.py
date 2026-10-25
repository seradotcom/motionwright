#!/usr/bin/env python3
"""Observe one explicit original and edited 180-frame native Film.

The canonical producer creates a six-second StudioService source and renders
through the pinned Semwright drivers. This wrapper retains original results,
uses the shared MLT process witness and seals private evidence. It never retries
an uncertain operation or grants audiovisual, human or release acceptance.
"""
from __future__ import annotations

from contextlib import contextmanager
import argparse
import io
import traceback
import wave
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tomllib

from native_av_failure_guard import NativeCommandFailure, run_sanitized_json
import native_mlt_g2_observation as observed
import native_mlt_g2_custody as custody
from ci_private_evidence import new_bytes, regular

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = json.loads((ROOT / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
PIN = SOURCE_LOCK["dependencies"]["semwright"]["revision"]
SEM = Path(os.environ.get("SEMWRIGHT_CHECKOUT", ROOT / "_semwright"))
SW_BIN = SEM / "target" / "debug"
FILM_BIN = ROOT / "target" / "debug" / "examples" / "native-film180-e2e"
MOTION_RUNTIME = SEM / "integrations" / "motion-canvas" / "runtime"
TRUSTED_MLT = Path("/usr/lib/x86_64-linux-gnu/mlt-7/libmltavformat.so")
RUN_SHA = os.environ.get("GITHUB_SHA", "")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
RUN_ATTEMPT = os.environ.get("GITHUB_RUN_ATTEMPT", "")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
STAGE_TIMEOUT = 1500
MEDIA_TIMEOUT = 180
ORIGINAL_AUDIO_OWNER_NAME = "firstparty-film180-source.wav"
OPENING = "Original synthetic two-shot native Film"


class Film180G2Rejected(ValueError):
    """Not an admitted new G2 native effect, without any user secret output."""


def require(ok: object, reason: str) -> None:
    if not ok:
        raise Film180G2Rejected(reason)


def digest(path: Path) -> str:
    hash_ = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hash_.update(chunk)
    return hash_.hexdigest()


def write_private_json(path: Path, payload: dict) -> None:
    require(path.is_absolute() and path.parent.is_dir()
            and not path.is_symlink() and not path.exists(),
            "original_g2_receipt_path_not_fresh")
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    require(len(data) < 1024 * 1024, "original_g2_receipt_outside_closed_budget")
    with path.open("xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    path.chmod(0o600)


def checked_plan(attempt=None) -> dict:
    require(sys.platform == "linux"
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("CI") == "true"
            and os.environ.get("RUNNER_OS") == "Linux"
            and os.environ.get("MOTIONWRIGHT_APPROVED_FILM180_G2") == "1"
            and os.environ.get("MW_MLT_SCOPE") == custody.FILM_SCOPE
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and RUN_ATTEMPT == "1",
            "original_Film180_native_production_requires_approved_Linux_GHA")
    require(HEX40.fullmatch(RUN_SHA) is not None
            and RUN_ID.isdecimal() and RUN_ATTEMPT.isdecimal(),
            "original_GitHub_run_identity_not_frozen")
    require(HEX40.fullmatch(PIN) is not None,
            "pinned_Semwright_source_lock_invalid")
    require(SEM.is_absolute() and SEM.is_dir()
            and subprocess.check_output(
                ["git", "-C", str(SEM), "rev-parse", "HEAD"],
                timeout=20, text=True,
            ).strip() == PIN,
            "actual_Semwright_checkout_not_the_pinned_source")
    executable_names = (
        "semwright", "semwrightd", "semwright-sandbox",
        "semwright-motion-canvas-driver",
        "semwright-mlt-video-driver", "semwright-mlt-runtime-runner",
    )
    for name in executable_names:
        require((SW_BIN / name).is_file(),
                "selected_original_Broker_or_Driver_Host_binary_missing")
    require(FILM_BIN.is_file(), "selected_original_Film180_example_not_built")
    require((ROOT / "tooling" / "native_film180_media_readback.py").is_file(),
            "integrated_original_Film180_independent_reader_missing")
    require((ROOT / "tooling" / "native_mlt_loaded_module.py").is_file(),
            "integrated_original_repaired_MLT_loaded_module_reader_missing")
    require(MOTION_RUNTIME.is_dir()
            and (MOTION_RUNTIME / "package-lock.json").is_file(),
            "original_pinned_Motion_Canvas_runtime_unavailable")
    binaries = {name: shutil.which(name) for name in (
        "melt", "ffprobe", "ffmpeg", "bwrap", "node",
    )}
    require(all(binaries.values()), "original_native_Broker_runtime_tool_missing")
    require(attempt is not None, "Film180_runtime_inputs_not_admitted")
    module_sha = attempt.inputs["compiled_module_sha256"]
    original_module_install_digest = attempt.inputs["installation_receipt_sha256"]
    runner = Path(os.environ.get("RUNNER_TEMP", ""))
    require(runner.is_absolute() and runner.is_dir() and not runner.is_symlink(),
            "ephemeral_GHA_runner_private_tmp_unavailable")
    return {
        "schema": "motionwright.native.film180.g2-closed-inputs/1",
        "motionwright_SHA": RUN_SHA,
        "Semwright_source_SHA": PIN,
        "repaired_MLT_ELF_sha256": module_sha,
        "MLT_original_installer_receipt_sha256": original_module_install_digest,
        "actual_native_source_project_id": "created_later_by_one_new_CAS_source",
        "native_effects_started": False,
        "human_acceptance": False,
    }


def pin_version() -> str:
    with (SEM / "Cargo.toml").open("rb") as file:
        return tomllib.load(file)["workspace"]["package"]["version"]


def make_manifest(identifier: str, executable: Path, tools: list[dict],
                  mounts: list[dict], *, app_process: str,
                  cooperative_cancellation: bool, artifacts: bool,
                  progress: bool) -> dict:
    """Use the existing pinned Semwright protocol 7; no new runtime behavior."""
    return {
        "manifest_version": 1, "protocol": 7,
        "id": identifier, "version": pin_version(), "publisher": "semwright",
        "executable": str(executable), "sha256": digest(executable),
        "application": {
            "desktop_id": None, "process_names": [app_process],
            "supported_versions": (
                ["Motion Canvas 3.17.2", "Node 22.22.0"]
                if identifier == "motion-canvas" else []
            ),
        },
        "transport": "stdio_v1", "mounts": mounts,
        "system_config": [], "secrets": [], "tools": tools,
        "network": False, "loopback_port": None,
        "resources": {
            "open_files": 512, "processes": 256, "cpu_seconds": 300,
            "operation_cpu_seconds": 0, "address_space_bytes": 4_294_967_296,
            "file_size_bytes": 1_073_741_824,
        },
        "request_timeout_ms": 300_000,
        "interfaces": {
            "dynamic_capabilities": False,
            "cooperative_cancellation": cooperative_cancellation,
            "events": False, "health": True, "progress": progress,
            "artifacts": artifacts, "native_refs": False, "host_tools": True,
        },
    }


def closed_owner_setup(paths: dict[str, Path]) -> dict:
    """Stage only pinned provider binaries, hashes and closed existing mounts."""
    dst = paths["bin"]
    sources = {
        "semwright-motion-canvas-driver": SW_BIN / "semwright-motion-canvas-driver",
        "semwright-motion-node": Path(shutil.which("node") or ""),
        "semwright-mlt-video-driver": SW_BIN / "semwright-mlt-video-driver",
        "semwright-mlt-runtime-runner": SW_BIN / "semwright-mlt-runtime-runner",
    }
    copied = {}
    for name, origin in sources.items():
        require(origin.is_file(), "registered_original_Driver_Host_source_missing")
        target = dst / name
        shutil.copyfile(origin, target)
        target.chmod(0o700)
        require(digest(target) == digest(origin),
                "copied_original_Driver_Host_binary_changed")
        copied[name] = target
    melt = observed.MELT
    ffmpeg = Path(shutil.which("ffmpeg") or "").resolve(strict=True)
    ffprobe = Path(shutil.which("ffprobe") or "").resolve(strict=True)
    mlt_runtime = melt.parent.parent.resolve(strict=True)
    motion_driver = copied["semwright-motion-canvas-driver"]
    mlt_driver = copied["semwright-mlt-video-driver"]
    node = copied["semwright-motion-node"]
    runner = copied["semwright-mlt-runtime-runner"]
    motion_manifest = make_manifest(
        "motion-canvas", motion_driver,
        [{
            "root": "motion-node-tool", "name": "motion-node",
            "sha256": digest(node),
            "mounts": ["project", "output", "runtime", "fontconfig"],
        }],
        [
            {"root": "project", "read_only": False},
            {"root": "media", "read_only": True},
            {"root": "output", "read_only": False},
            {"root": "runtime", "read_only": True, "execute": True},
            {"root": "fontconfig", "read_only": True},
        ], app_process="node", cooperative_cancellation=True,
        artifacts=True, progress=True,
    )
    tools = [{
        "root": "mlt-runner-root", "name": "mlt-runner",
        "sha256": digest(runner),
        "mounts": ["mlt-runtime", "scratch", "project", "media", "output"],
        "dependencies": ["melt", "ffprobe", "ffmpeg"],
    }]
    tools.extend({
        "root": name + "-root", "name": name,
        "sha256": digest(actual),
        "mounts": [], "dependencies": [],
    } for name, actual in (
        ("melt", melt), ("ffprobe", ffprobe), ("ffmpeg", ffmpeg),
    ))
    mlt_manifest = make_manifest(
        "mlt-video", mlt_driver, tools,
        [
            {"root": "project", "read_only": True},
            {"root": "media", "read_only": True},
            {"root": "output", "read_only": False},
            {"root": "mlt-runtime", "read_only": True, "execute": True},
            {"root": "scratch", "read_only": False},
        ], app_process="melt", cooperative_cancellation=False,
        artifacts=False, progress=False,
    )
    config = paths["config"]
    first = config / "motion-canvas-driver.json"
    second = config / "mlt-video-driver.json"
    write_private_json(first, motion_manifest)
    write_private_json(second, mlt_manifest)
    grants = [
        ("project", paths["project"], True),
        ("media", paths["media"], False),
        ("output", paths["output"], True),
        ("runtime", MOTION_RUNTIME, False),
        ("fontconfig", Path("/etc/fonts"), False),
        ("motion-node-tool", node, False),
        ("mlt-runtime", mlt_runtime, False),
        ("scratch", paths["scratch"], True),
        ("mlt-runner-root", runner, False),
        ("melt-root", melt, False),
        ("ffprobe-root", ffprobe, False),
        ("ffmpeg-root", ffmpeg, False),
    ]
    lines = [
        "drivers = [" + ", ".join(json.dumps(str(x)) for x in [first, second]) + "]",
        "driver_network = false", "", "[policy]", 'profile = "observe"',
        'allow = ["driver:motion-canvas", "driver:mlt-video"]',
    ]
    for name, location, writable in grants:
        lines += [
            "", "[[policy.filesystem]]",
            f"name = {json.dumps(name)}",
            f"path = {json.dumps(str(location.resolve(strict=True)))}",
            "read = true", f"write = {'true' if writable else 'false'}",
        ]
    owner = config / "owner.toml"
    owner.write_text("\n".join(lines) + "\n", encoding="utf-8")
    owner.chmod(0o600)
    return {
        "owner": owner, "motion_driver_sha256": digest(motion_driver),
        "mlt_driver_sha256": digest(mlt_driver),
        "registered_node_sha256": digest(node),
        "mlt_installed_module_sha256": digest(TRUSTED_MLT),
    }


def closed_paths(root: Path) -> dict[str, Path]:
    paths = {}
    for name in [
        "runtime", "state", "home", "config", "bin", "project",
        "media", "output", "scratch", "motionwright-data",
    ]:
        location = root / name
        location.mkdir(mode=0o700)
        paths[name] = location
    return paths


def wait_socket(daemon: subprocess.Popen, target: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        require(daemon.poll() is None,
                "registered_native_Broker_died_before_available")
        if target.is_socket():
            return
        time.sleep(0.05)
    raise Film180G2Rejected("registered_native_Broker_socket_not_found")


@contextmanager
def bound_broker(paths: dict[str, Path], evidence: Path, env: dict[str, str],
                 seed: dict, attempt):
    manifest = closed_owner_setup(paths)
    socket = paths["runtime"] / "broker.sock"
    session = paths["runtime"] / "motionwright.session"
    daemonlog = (evidence / "semwrightd.original.private.log").open("xb")
    write_private_json(evidence / "broker-start.intent.json", {
        "executable": str(SW_BIN / "semwrightd"), "owner": str(manifest["owner"]),
        "socket": str(socket), "automatic_retry": False,
    })
    daemon = subprocess.Popen(
        [
            str(SW_BIN / "semwrightd"), "--config", str(manifest["owner"]),
            "--socket", str(socket),
        ],
        env=env, stdin=subprocess.DEVNULL, stdout=daemonlog,
        stderr=daemonlog, start_new_session=True,
    )
    try:
        attempt.daemon_identity = observed.process_identity(daemon.pid)
        write_private_json(evidence / "broker-original-identity.json", {
            "pid": attempt.daemon_identity[0], "start_ticks": attempt.daemon_identity[1],
        })
        wait_socket(daemon, socket)
        arguments = [
            str(SW_BIN / "semwright"), "--socket", str(socket),
            "--session-file", str(session), "--json",
        ]
        discovered = attempt.run_argv("broker.motion-canvas-discovery",
            [*arguments, "capabilities", "search", "motion-canvas",
             "--provider", "driver:motion-canvas"], env=env,
        )
        require(discovered.get("data", {}).get("capabilities"),
                "original_pinned_Motion_Canvas_provider_missing")
        for command in (
            "driver.mlt-video.frames.encode",
            "driver.mlt-video.av.mux",
            "driver.mlt-video.sync.probe",
        ):
            response = attempt.run_argv("broker." + command.rsplit(".", 2)[-2] + "-" + command.rsplit(".", 1)[-1],
                [*arguments, "capabilities", "describe", command], env=env,
            )
            require(
                response.get("data", {}).get("capability", {}).get("name") == command
                and response.get("data", {}).get("provenance", {}).get("provider")
                == "driver:mlt-video",
                "registered_original_MLT_provenance_mismatch",
            )
        require(session.is_file()
                and session.stat().st_mode & 0o777 == 0o600,
                "native_owner_session_ticket_not_private")
        connection = paths["config"] / "motionwright-connection.json"
        write_private_json(connection, {
            "schema": "motionwright-semwright-connection/1",
            "executable": str((SW_BIN / "semwright").resolve(strict=True)),
            "executable_sha256": digest(SW_BIN / "semwright"),
            "socket": str(socket), "session_file": str(session),
            "output_root": str(paths["output"]), "resource": seed["resource"],
        })
        yield connection, manifest, daemon.pid
    finally:
        # Keep the original owner and journals until the common seal completes.
        # Runner teardown follows custody; no uncertain provider is killed or retried.
        try:
            write_private_json(evidence / "broker-end-observation.json", {
                "pid": daemon.pid, "observed_exit_code": daemon.poll(),
                "termination_requested": False, "provider_cleanup_verified": False,
            })
            daemonlog.flush()
            os.fsync(daemonlog.fileno())
        except BaseException:
            attempt.secondary("broker-end-custody")
        daemonlog.close()


def source_command(args: list[str], env: dict[str, str],
                   *, timeout: int = 40, capture=None) -> dict:
    if capture is None:
        return run_sanitized_json([str(FILM_BIN), *args], env=env, timeout=timeout)
    return run_sanitized_json([str(FILM_BIN), *args], env=env, timeout=timeout, capture=capture)


def fresh_source_readback(database: Path, receipt: Path, env: dict[str, str],
                          *, attempt, stage: str) -> None:
    require(not receipt.exists() and database.is_file(),
            "original_saved_Film180_readback_not_fresh")
    label = stage + ".source-reopen"
    capture = attempt.capture(label)
    write_private_json(attempt.evidence_dir / (label + ".intent.json"), {
        "argv": [str(FILM_BIN), "readback", str(database), str(receipt)],
        "automatic_retry": False, "native_renderer_invoked": False,
    })
    try:
        result = subprocess.run(
            [str(FILM_BIN), "readback", str(database), str(receipt)],
            env=env, capture_output=True, timeout=40,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        try:
            capture({"stdout": getattr(error, "stdout", None) or b"",
                     "stderr": getattr(error, "stderr", None) or b"",
                     "exit_code": None, "deadline_exceeded": isinstance(error, subprocess.TimeoutExpired),
                     "error_class": type(error).__name__, "error": str(error), "automatic_retry": False})
        except BaseException:
            attempt.secondary(label + ".capture")
        raise
    capture({"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode,
             "deadline_exceeded": False, "automatic_retry": False})
    require(result.returncode == 0 and receipt.is_file() and receipt.stat().st_size < 1024 * 1024,
            "original_saved_Film180_project_reopen_failed")


def original_png_index(output_root: Path, film: dict, *, frames: int = 180) -> dict:
    """Check every manifest source index and digest, no image guess/fallback."""
    segments = film.get("segments")
    require(isinstance(segments, list) and len(segments) == 1,
            "original_Film180_native_segment_missing")
    artifact = segments[0].get("artifact")
    require(isinstance(artifact, dict), "native_Film_artifact_metadata_missing")
    directory = artifact.get("directory")
    require(type(directory) is str and 0 < len(directory) <= 200
            and not directory.startswith("/")
            and "/" not in directory and "\\" not in directory
            and directory not in (".", ".."),
            "native_Film_artifact_directory_not_closed")
    root = output_root / directory
    frame_root = root / "frames"
    manifest_file = root / "artifact-manifest.json"
    require(output_root.is_absolute()
            and output_root.is_dir() and not output_root.is_symlink()
            and root.is_dir() and not root.is_symlink()
            and frame_root.is_dir() and not frame_root.is_symlink()
            and manifest_file.is_file() and not manifest_file.is_symlink(),
            "native_Film_artifact_manifest_or_frames_symlink_not_admitted")
    require(0 < manifest_file.stat().st_size <= 1024 * 1024,
            "native_Film_manifest_outside_closed_byte_budget")
    require(digest(manifest_file) == artifact.get("manifest_sha256"),
            "native_Film_manifest_digest_not_original")

    def reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, "native_Film_manifest_duplicate_JSON_field")
            result[key] = value
        return result

    manifest = json.loads(
        manifest_file.read_bytes(), object_pairs_hook=reject_duplicate_json_keys,
        parse_constant=lambda _: require(False, "native_Film_manifest_nonfinite_json"),
    )
    require(type(manifest) is dict,
            "native_Film_manifest_root_must_be_object")
    indexed = manifest.get("frames")
    plan = manifest.get("plan", {})
    require(type(indexed) is list and len(indexed) == frames
            and plan.get("frame_count") == frames
            and plan.get("first_frame") == 0
            and plan.get("end_frame_exclusive") == frames,
            "original_native_Film_frame_index_not_contiguous")
    for index, row in enumerate(indexed):
        relative = f"frames/{index:06}.png"
        file = root / "frames" / f"{index:06}.png"
        require(
            row.get("file") == relative and row.get("index") == index
            and file.is_file() and not file.is_symlink()
            and 8 <= file.stat().st_size <= 16 * 1024 * 1024
            and row.get("bytes") == file.stat().st_size
            and row.get("sha256") == digest(file),
            "native_Film_frame_digest_or_sequence_does_not_match",
        )
        with file.open("rb") as source:
            require(source.read(8) == b"\x89PNG\r\n\x1a\n",
                    "original_Film_frame_does_not_have_a_PNG_signature")
    return {
        "frame_count": frames,
        "original_manifest_sha256": digest(manifest_file),
        "first_original_png_sha256": digest(root / "frames" / "000000.png"),
        "last_original_png_sha256": digest(root / "frames" / f"{frames-1:06}.png"),
        "every_original_frame_SHA_matched": True,
    }


def parse_original_180_framemd5(source: bytes) -> dict:
    """Require one SHA-256 of every decoded RGB24 pixel frame at exactly 30fps.

    These independent lossy-master hashes are evidence of complete decoding,
    NOT equality to the original editable PNG frames (H264 is lossy).
    """
    require(0 < len(source) <= 128 * 1024, "all180_decoded_RGB_hash_receipt_budget")
    try:
        content = source.decode("ascii")
    except UnicodeError as exc:
        raise Film180G2Rejected("all180_original_RGB_hash_receipt_not_ASCII") from exc
    header = {}
    records = []
    for line in content.splitlines():
        if line.startswith("#"):
            match = re.fullmatch(r"#(hash|tb 0|dimensions 0|codec_id 0):\s*(.+)", line)
            if match:
                require(match.group(1) not in header,
                        "all180_duplicate_native_decoder_header")
                header[match.group(1)] = match.group(2)
        elif line.strip():
            fields = [token.strip() for token in line.split(",")]
            require(len(fields) == 6, "all180_decoded_RGB_frame_record_malformed")
            records.append(fields)
    require(header.get("hash", "").upper() == "SHA256"
            and header.get("dimensions 0") == "1920x1080"
            and header.get("codec_id 0") == "rawvideo",
            "original_RGB24_video_decoder_format_or_hash_not_admitted")
    timing = re.fullmatch(r"(\d+)/(\d+)", header.get("tb 0", ""))
    require(timing is not None, "all180_decoded_RGB_timebase_not_observed")
    num, den = (int(timing.group(1)), int(timing.group(2)))
    require(num > 0 and den > 0 and den % (30 * num) == 0,
            "all180_decoded_RGB_timebase_is_not_exact_30fps")
    step = den // (30 * num)
    require(len(records) == 180,
            "all180_native_master_frames_not_actually_decoded")
    hashes = []
    for index, frame in enumerate(records):
        try:
            stream, dts, pts, duration, size = [int(value) for value in frame[:5]]
        except ValueError as exc:
            raise Film180G2Rejected("all180_native_RGB_numeric_frame_malformed") from exc
        require(stream == 0 and dts == pts == index * step
                and duration == step and size == 1920 * 1080 * 3
                and re.fullmatch(r"[0-9a-fA-F]{64}", frame[5]) is not None,
                "all180_native_RGB_index_pts_size_or_SHA_mismatch")
        hashes.append(frame[5].lower())
    return {
        "schema": "motionwright.native.film180.decoded-all180-RGB24/1",
        "source_frame_count": 180,
        "observed_decode_timebase": f"{num}/{den}",
        "frame_rate": "30/1",
        "decoded_format": "RGB24",
        "decoded_pixel_size": [1920, 1080],
        "bytes_per_decoded_frame": 1920 * 1080 * 3,
        "all_original_frame_sha256": hashes,
        "unique_decoded_RGB_frames": len(set(hashes)),
        "every_original_decoded_RGB_frame_observed": True,
        "original_PNG_pixel_equality_claim": False,
        "H264_perceptual_fidelity_claim": False,
        "human_acceptance": False,
    }


def actual_all180_original_RGB_digests(video: Path, ffmpeg: str) -> dict:
    """FFmpeg decodes all frames yet writes only bounded SHA manifests, no 1GB RGB."""
    require(video.is_absolute() and video.is_file() and not video.is_symlink(),
            "all180_original_native_MP4_source_not_regular_absolute")
    saved_sha = digest(video)
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-i", str(video), "-map", "0:v:0",
         "-an", "-fps_mode", "passthrough", "-pix_fmt", "rgb24",
         "-f", "framemd5", "-hash", "sha256", "pipe:1"],
        capture_output=True, timeout=MEDIA_TIMEOUT, check=True,
    )
    original = parse_original_180_framemd5(result.stdout)
    require(saved_sha == digest(video),
            "original_AV_master_mutated_during_full_RGB_decode")
    original["source_master_sha256"] = saved_sha
    return original


def parse_rgb_psnr_observations(raw: bytes, *, count: int = 180) -> dict:
    """Observe exactly all source PNG vs H264 RGB frames without a PASS floor.

    FFmpeg's psnr filter compares decoded RGB (gbrp) after dropping PNG
    alpha. Neither pixel-perfect equality nor any arbitrary lossy-video
    quality threshold is inferred from this descriptive metrology.
    """
    require(0 < len(raw) <= 128 * 1024,
            "original_Film180_RGB_PSN_metrics_receipt_outside_budget")
    try:
        source = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise Film180G2Rejected("original_Film180_RGB_PSN_stats_not_ASCII") from exc
    mse, psnr, infinite = [], [], 0
    for expected_index, line in enumerate(source.splitlines(), start=1):
        require(expected_index <= count and line.strip(),
                "original_Film180_RGB_PSN_stats_duplicate_or_extra_frames")
        row = {}
        for pair in line.split():
            require(":" in pair, "native_RGB_PSN_stats_field_malformed")
            key, value = pair.split(":", 1)
            require(key and key not in row,
                    "native_RGB_PSN_stats_duplicate_metric")
            row[key] = value
        require(set(("n", "mse_avg", "psnr_avg")).issubset(row),
                "native_RGB_PSN_metrics_missing")
        try:
            index = int(row["n"])
            mse_value = float(row["mse_avg"])
            psnr_value = float(row["psnr_avg"])
        except ValueError as exc:
            raise Film180G2Rejected("native_RGB_PSN_metric_non_numeric") from exc
        require(index == expected_index
                and math.isfinite(mse_value) and mse_value >= 0
                and not math.isnan(psnr_value) and psnr_value >= 0,
                "native_RGB_PSN_index_or_quality_metric_invalid")
        if math.isinf(psnr_value):
            infinite += 1
        else:
            psnr.append(psnr_value)
        mse.append(mse_value)
    require(len(mse) == count,
            "original_Film180_native_RGB_PSN_missing_or_extra_frames")
    return {
        "schema": "motionwright.native.film180.RGB-native-to-H264-PSNR/1",
        "observed_frame_count": count,
        "observed_RGB_mse_min": min(mse),
        "observed_RGB_mse_max": max(mse),
        "observed_RGB_mse_mean": round(sum(mse) / count, 6),
        "observed_RGB_psnr_min_finite": min(psnr) if psnr else None,
        "observed_RGB_psnr_mean_finite":
            round(sum(psnr) / len(psnr), 6) if psnr else None,
        "observed_identical_RGB_frame_count": infinite,
        "all_frame_comparisons_performed": True,
        "native_PNG_alpha_channel_compared": False,
        "H264_lossy_quality_accepted_by_this_observer": False,
        "pixel_perfect_or_visual_human_acceptance": False,
    }


def original_png_color_types(frame_dir: Path) -> dict:
    """Record native PNG alpha presence without equating it to H264 RGB."""
    require(frame_dir.is_absolute() and frame_dir.is_dir()
            and not frame_dir.is_symlink(),
            "native_original_PNG_source_frames_not_an_absolute_directory")
    counts = {}
    for index in range(180):
        path = frame_dir / f"{index:06}.png"
        require(path.is_file() and not path.is_symlink(),
                "original_native_PNG_color_type_frame_missing")
        with path.open("rb") as source:
            header = source.read(26)
        require(len(header) == 26
                and header[:8] == b"\x89PNG\r\n\x1a\n"
                and header[12:16] == b"IHDR"
                and int.from_bytes(header[16:20], "big") == 1920
                and int.from_bytes(header[20:24], "big") == 1080
                and header[25] in (0, 2, 3, 4, 6),
                "native_original_PNG_IHDR_type_or_geometry_unverified")
        key = str(header[25])
        counts[key] = counts.get(key, 0) + 1
    return {
        "native_source_PNG_color_type_counts": counts,
        "native_RGBA_or_greyscale_alpha_PNG_frames":
            sum(counts.get(x, 0) for x in ("4", "6")),
        "all180_PNG_IHDR_geometry_verified": True,
        "native_alpha_preserved_in_H264": False,
    }


def native_png_vs_h264_observations(
    output_root: Path, motion: dict, master_path: Path,
    owner_evidence: Path, stage: str, ffmpeg: str = "ffmpeg",
) -> dict:
    """Read only original PNG+MP4, write one new bounded FFmpeg stats file.

    No 1.1 GiB raw RGB stream is stored. No quality threshold is invented.
    This observation follows independently verified hashes for both inputs.
    """
    require(stage in {"original", "edited"}, "PSN_observation_source_stage_not_admitted")
    require(output_root.is_absolute() and output_root.is_dir()
            and not output_root.is_symlink()
            and master_path.is_absolute() and master_path.is_file()
            and not master_path.is_symlink()
            and owner_evidence.is_absolute()
            and owner_evidence.is_dir()
            and not owner_evidence.is_symlink(),
            "original_RGB_PSN_input_or_evidence_not_owner_bounded")
    segments = motion.get("segments")
    require(type(segments) is list and len(segments) == 1,
            "RGB_PSN_original_single_Film_segment_missing")
    artifact = segments[0].get("artifact", {})
    dirname = artifact.get("directory")
    require(type(dirname) is str
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", dirname) is not None
            and dirname not in (".", ".."),
            "RGB_PSN_frame_root_not_original_owner_directory")
    frames = output_root / dirname / "frames"
    colors = original_png_color_types(frames)
    stats = owner_evidence / f"{stage}-film180-allframe-RGB-PSNR.stats"
    require(not stats.exists() and not stats.is_symlink()
            and re.fullmatch(r"/[A-Za-z0-9_./-]{1,190}", str(stats)) is not None,
            "RGB_PSN_original_stats_owner_path_not_fresh")
    filter_code = (
        "[0:v]format=gbrp[reference];"
        "[1:v]format=gbrp[encoded];"
        "[reference][encoded]psnr="
        f"stats_file={stats}:stats_version=1[observed]"
    )
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error",
         "-framerate", "30", "-start_number", "0",
         "-i", str(frames / "%06d.png"),
         "-i", str(master_path),
         "-filter_complex", filter_code, "-map", "[observed]",
         "-an", "-f", "null", "-"],
        capture_output=True, timeout=2 * MEDIA_TIMEOUT, check=True,
    )
    require(len(result.stdout) <= 4096 and len(result.stderr) <= 8192
            and stats.is_file() and stats.stat().st_size <= 128 * 1024,
            "RGB_PSN_FFmpeg_source_comparison_not_bounded")
    report = parse_rgb_psnr_observations(stats.read_bytes())
    report.update(colors)
    report["original_native_source_Film_manifest_sha256"] = digest(
        output_root / dirname / "artifact-manifest.json")
    report["H264_master_sha256"] = digest(master_path)
    report["full_RGB_PSN_metrics_sha256"] = digest(stats)
    report["no_H264_quality_floor_assumed"] = True
    return report


def original_frame_difference(ffmpeg: str, first: Path, second: Path,
                              *, number: int = 110) -> dict:
    from native_film180_media_readback import WIDTH, HEIGHT, difference_count
    def at(media: Path) -> bytes:
        result = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-i", str(media),
             "-an", "-vf", f"select=eq(n\\,{number})",
             "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
            capture_output=True, timeout=MEDIA_TIMEOUT, check=True,
        )
        require(len(result.stdout) == WIDTH * HEIGHT * 3,
                "edited_native_mp4_frame_not_decodable")
        return result.stdout
    change = difference_count(at(first), at(second), WIDTH, HEIGHT,
                              bottom_only=False)
    require(change >= 250, "edited_Film180_final_MP4_did_not_retain_authored_canvas_change")
    return {
        "original_edited_comparison_frame": number,
        "changed_decoded_pixels": change,
        "source_content_preserved": True,
        "human_visual_acceptance": False,
    }


def original_native_master_name(project_id: str, revision: int,
                                stage: str) -> str:
    """Compute the *existing* ProductionCoordinator MLT output name.

    Source: crates/native/src/production.rs: sha256(request_id)[:16],
    "mw-" + uuid.simple() + "-r{revision}" + "-master.mp4".
    No independent output naming policy or fallback is introduced.
    """
    require(stage in {"original", "edited"},
            "unrecognized_original_Film180_render_stage")
    require(type(project_id) is str and
            re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                         project_id) is not None
            and type(revision) is int and revision >= 1,
            "original_Film180_project_identity_invalid")
    request = "film180-" + stage + "-av-master-v1"
    token = hashlib.sha256(request.encode("utf-8")).hexdigest()[:16]
    return f"mw-{project_id.replace('-', '')}-r{revision}-{token}-master.mp4"


class FilmAttempt:
    """One original/edited source attempt, using common runtime validation and custody."""

    def __init__(self, inputs: dict, evidence_dir: Path):
        self.inputs = inputs
        self.evidence_dir = evidence_dir
        self.secondary_failures = []
        self.native_commands = {}
        self.command_labels = set()
        self.daemon_identity = None
        self.wave_sha = None
        self.melt_alias_observation = None
        self.completed_stages = {}
        self.completed = False
        self.error = None

    @classmethod
    def prepare(cls, path: Path):
        require(os.environ.get("MOTIONWRIGHT_APPROVED_FILM180_G2") == "1"
                and os.environ.get("MW_MLT_SCOPE") == custody.FILM_SCOPE,
                "Film180_explicit_manual_selection_required")
        runner = observed.admission.disposable_linux_runner()
        data = observed.strict_object(observed.admission.checked_bytes(path, 64 * 1024))
        require(type(data) is dict
                and set(data) == custody.RUNTIME_INPUT_KEYS | {"schema", "private_evidence_dir"}
                and data["schema"] == custody.FILM_INPUT_SCHEMA, "Film180_inputs_schema")
        closure, alias = observed.verify_runtime_inputs(
            {key: data[key] for key in custody.RUNTIME_INPUT_KEYS},
            source_sha=RUN_SHA, semwright_sha=PIN)
        private = Path(data["private_evidence_dir"])
        require(private.is_absolute() and private.parent == runner
                and not private.exists() and not private.is_symlink(),
                "Film180_private_directory_not_fresh_runner_child")
        private.mkdir(mode=0o700)
        result = cls(data, private)
        result.melt_alias_observation = alias
        write_private_json(private / "runtime-input-custody.json", {
            "schema": custody.FILM_INPUT_SCHEMA, "motionwright_sha": RUN_SHA, "semwright_sha": PIN,
            "source_compiler": closure, "installed_melt_alias": alias,
            "known_melt_sha256": data["known_melt_sha256"],
            "official_runtime_receipt_sha256": data["official_runtime_receipt_sha256"],
            "installation_receipt_sha256": data["installation_receipt_sha256"],
            "historical_reference_WAV_reused": False, "own_source_wave_created": False,
            "native_effect_started": False,
        })
        return result

    def secondary(self, label):
        self.secondary_failures.append(label)
        observed.warn_secondary()

    def capture(self, label):
        require(re.fullmatch(r"[a-z0-9.-]{1,64}", label) is not None,
                "Film180_private_capture_label")
        def retain(payload):
            try:
                stdout, stderr = payload["stdout"], payload["stderr"]
                require(type(stdout) is bytes and type(stderr) is bytes,
                        "Film180_original_stream_type")
                new_bytes(self.evidence_dir / (label + ".stdout"), stdout)
                new_bytes(self.evidence_dir / (label + ".stderr"), stderr)
                write_private_json(self.evidence_dir / (label + ".terminal.json"), {
                    **{key: value for key, value in payload.items() if key not in {"stdout", "stderr"}},
                    "stdout_sha256": hashlib.sha256(stdout).hexdigest(), "stdout_bytes": len(stdout),
                    "stderr_sha256": hashlib.sha256(stderr).hexdigest(), "stderr_bytes": len(stderr),
                    "native_terminal_inferred_from_exit": False,
                })
            except BaseException:
                self.secondary(label + ".capture")
                raise
        return retain


    def capture_original_pcm(self, stage, master_sha):
        """Retain the original decode payload once, before content inspection.

        Raw stdout/stderr retain their existing private custody. The dedicated
        s16le original is copied directly from that same callback payload; no
        readback of excluded logs, decoder replay, trimming or padding occurs.
        """
        require(stage in {"original", "edited"} and HEX64.fullmatch(master_sha),
                "Film180_PCM_capture_identity")
        retain_raw = self.capture(stage + ".original-pcm-decode")
        used = False
        def retain(payload):
            nonlocal used
            require(not used, "Film180_PCM_capture_replay_refused")
            used = True
            retain_raw(payload)
            if type(payload.get("exit_code")) is not int or payload["exit_code"] != 0 or payload.get("deadline_exceeded") is not False:
                return
            pcm = payload.get("stdout")
            require(type(pcm) is bytes and 0 < len(pcm) <= 2 * 1024 * 1024
                    and len(pcm) % 4 == 0 and payload.get("automatic_retry") is False,
                    "Film180_PCM_capture_original_shape")
            new_bytes(self.evidence_dir / (stage + "-original-master-decoded.s16le"), pcm)
            write_private_json(self.evidence_dir / (stage + "-original-pcm-capture.json"), {
                "schema": "motionwright.native.film180.original-pcm-capture/2", "phase": stage,
                "master_sha256": master_sha, "decoded_pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                "decoded_pcm_bytes": len(pcm), "decoded_stereo_frames": len(pcm) // 4,
                "channels": 2, "sample_rate": 48000, "sample_width_bytes": 2,
                "decoder_exit_code": 0, "deadline_exceeded": False,
                "captured_from_original_decoder_stdout": True, "decoder_reinvoked_by_custody": False,
                "source_start_offset_frames_assumed": 0, "packet_PTS_and_AAC_priming_verified": False,
                "human_acceptance": False,
            })
        return retain

    def run_argv(self, label, argv, *, env, timeout=30):
        require(label not in self.command_labels, "Film180_command_replay_refused")
        self.command_labels.add(label)
        write_private_json(self.evidence_dir / (label + ".intent.json"), {
            "argv": argv, "timeout_seconds": timeout, "automatic_retry": False,
        })
        return run_sanitized_json(argv, env=env, timeout=timeout, capture=self.capture(label))

    def command(self, label, args, env, *, timeout=40, native=False):
        require(label not in self.command_labels, "Film180_command_replay_refused")
        self.command_labels.add(label)
        write_private_json(self.evidence_dir / (label + ".intent.json"), {
            "argv": [str(FILM_BIN), *args], "timeout_seconds": timeout,
            "automatic_retry": False, "native_renderer_command": native,
        })
        if native:
            self.native_commands[label] = "ERROR_OR_UNKNOWN"
        return source_command(args, env, timeout=timeout, capture=self.capture(label))

    def confirm_native(self, label):
        require(self.native_commands.get(label) == "ERROR_OR_UNKNOWN",
                "Film180_native_terminal_without_original_start")
        self.native_commands[label] = "SUCCESS_REPORTED"

    def retain_wave(self, path):
        raw = regular(path, 2 * 1024 * 1024)[0]
        with wave.open(io.BytesIO(raw), "rb") as reader:
            require(reader.getnchannels() == 2 and reader.getsampwidth() == 2
                    and reader.getframerate() == 48000 and reader.getnframes() == 288000
                    and reader.getcomptype() == "NONE"
                    and len(reader.readframes(288001)) == 288000 * 4,
                    "Film180_source_must_be_own_six_second_stereo_PCM")
        self.wave_sha = hashlib.sha256(raw).hexdigest()
        require(self.wave_sha != observed.REFERENCE_WAV_SHA256,
                "Film180_historical_reference_WAV_not_own_source")
        new_bytes(self.evidence_dir / "firstparty-source.wav", raw)
        write_private_json(self.evidence_dir / "firstparty-source-custody.json", {
            "origin": "CURRENT_CANONICAL_FILM180_SOURCE",
            "sha256": self.wave_sha, "bytes": len(raw), "pcm_frames": 288000,
            "sample_rate": 48000, "channels": 2, "sample_width": 2,
            "historical_reference_WAV_reused": False, "native_effect_started": False,
        })

    def retain_error(self, error):
        self.error = error
        if isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
            try:
                self.capture("original.failed-subprocess")({
                    "stdout": error.stdout if error.stdout is not None else b"",
                    "stderr": error.stderr if error.stderr is not None else b"",
                    "argv": error.cmd,
                    "exit_code": getattr(error, "returncode", None),
                    "deadline_exceeded": isinstance(error, subprocess.TimeoutExpired),
                    "error_class": type(error).__name__, "error": str(error),
                    "automatic_retry": False,
                })
            except BaseException:
                self.secondary("original-subprocess-custody")
        try:
            write_private_json(self.evidence_dir / "original-attempt-error.json", {
                "error_class": type(error).__name__, "error": str(error),
                "traceback": traceback.format_exc(), "automatic_retry": False,
                "native_command_terminals": self.native_commands,
            })
        except BaseException:
            self.secondary("original-error-custody")

    def finish(self):
        required = {"original.render", "original.master", "edited.render", "edited.master"}
        all_reported = (set(self.native_commands) == required and
                        set(self.native_commands.values()) == {"SUCCESS_REPORTED"})
        if all_reported:
            terminal = "SUCCESS_REPORTED"
        elif any(value == "ERROR_OR_UNKNOWN" for value in self.native_commands.values()):
            terminal = "ERROR_OR_UNKNOWN"
        elif self.native_commands:
            terminal = "RESULT_REPORTED_UNVERIFIED"
        else:
            terminal = "NOT_STARTED"
        verified = (self.completed and all_reported and self.error is None
                    and not self.secondary_failures and set(self.completed_stages) == {"original", "edited"})
        write_private_json(self.evidence_dir / "final-custody.json", {
            "schema": custody.FILM_FINAL_SCHEMA, "terminal_scope": custody.FILM_TERMINAL_SCOPE,
            "native_terminal": terminal, "native_command_terminals": self.native_commands,
            "native_operation_retried": False, "source_content_and_live_module_verified": verified,
            "secondary_failures": self.secondary_failures, "completed_stages": self.completed_stages,
            "own_six_second_WAV_sha256": self.wave_sha, "historical_reference_WAV_reused": False,
            "packet_PTS_and_AAC_priming_verified": False, "physical_AV_master_acceptance": False,
            "human_acceptance": False, "release_authorized": False,
        })


@contextmanager
def retained_live_observer(attempt: FilmAttempt, stage: str,
                           daemon_pid: int, output_root: Path):
    """Retain the same original observer before closing its read-only descriptors."""
    require(stage in {"original", "edited"}, "Film180_live_observer_stage")
    directory = attempt.evidence_dir / (stage + "-live-observer")
    directory.mkdir(mode=0o700)
    watcher = None
    stopped = False
    primary = None
    errors = []
    custody_record = {}

    def secondary(label, error):
        errors.append(error)
        attempt.secondary(stage + "." + label)
        try:
            observed.new_json(directory / (label + "-error.json"), {
                "stage": label, "error": observed.bounded_error(error),
                "native_command_terminals": attempt.native_commands,
                "native_operation_retried": False,
            })
        except BaseException:
            attempt.secondary(stage + "." + label + "-error-custody")

    def stop_and_retain():
        nonlocal stopped
        if stopped or watcher is None:
            return
        stopped = True
        for label, operation in (
            ("observer-stop", watcher.stop),
            ("observer-intermediate", lambda: watcher.retain_intermediate(directory)),
        ):
            try:
                operation()
            except BaseException as error:
                secondary(label, error)
        try:
            diagnostic = watcher.diagnostic()
            packed = (json.dumps(diagnostic, sort_keys=True, indent=2) + "\n").encode()
            require(len(packed) <= 3 * 1024 * 1024, "Film180_observer_diagnostic_budget")
            observed.new_bytes(directory / "observer-diagnostic.json", packed)
            custody_record["diagnostic_sha256"] = observed.sha(packed)
            require(diagnostic["bounded_journal_complete"] is True,
                    "Film180_observer_diagnostic_incomplete")
        except BaseException as error:
            secondary("observer-diagnostic", error)
        if errors:
            raise errors[0]

    try:
        watcher = observed.LiveObserver(daemon_pid, output_root, attempt.inputs["known_melt_sha256"])
        watcher.start()
        yield watcher, stop_and_retain, custody_record
    except BaseException as error:
        primary = (error, error.__traceback__)
    finally:
        try:
            stop_and_retain()
        except BaseException as error:
            if primary is None:
                primary = (error, error.__traceback__)
        if watcher is not None:
            try:
                watcher.close()
            except BaseException as error:
                secondary("observer-close", error)
                if primary is None:
                    primary = (error, error.__traceback__)
        if primary is not None:
            try:
                observed.new_json(directory / "original-observation-error.json", {
                    "error": observed.bounded_error(primary[0]),
                    "native_command_terminals": attempt.native_commands,
                    "diagnostic_sha256": custody_record.get("diagnostic_sha256"),
                    "native_operation_retried": False,
                })
            except BaseException:
                attempt.secondary(stage + ".original-observation-error-custody")
            raise primary[0].with_traceback(primary[1])



def retain_original_audio_packets(attempt, stage, master, master_sha, ffprobe="ffprobe"):
    """One bounded read-only probe of this phase's published original MP4.

    Packet times and skip metadata remain observations; this does not approve
    AAC priming, source alignment, renderer success or a native re-dispatch.
    """
    import tempfile
    require(stage in {"original", "edited"} and HEX64.fullmatch(master_sha),
            "Film180_packet_original_identity")
    label = stage + ".original-packet-probe"
    require(label not in attempt.command_labels, "Film180_packet_probe_replay_refused")
    # Latch before any original-file read or process launch, including failures.
    attempt.command_labels.add(label)
    require(digest(master) == master_sha, "Film180_packet_original_identity")
    argv = [
        ffprobe, "-v", "error", "-select_streams", "a:0", "-show_streams",
        "-show_packets", "-of", "json", str(master),
    ]
    # Keep the redirected stdout file alive until this single invocation's
    # available originals and terminal have been retained, including failures.
    result = primary = primary_traceback = None
    capture_errors = []
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                argv, stdout=output, stderr=subprocess.PIPE, timeout=60, check=False,
            )
        except BaseException as error:
            primary, primary_traceback = error, error.__traceback__
        raw = None
        stdout_size = None
        try:
            output.seek(0, 2)
            stdout_size = output.tell()
            if type(stdout_size) is int and 0 <= stdout_size <= 1024 * 1024:
                output.seek(0)
                raw = output.read(1024 * 1024 + 1)
                require(type(raw) is bytes and len(raw) == stdout_size,
                        "Film180_packet_original_stream_changed")
        except BaseException as error:
            raw = None
            capture_errors.append(("stdout-read", error))
        stderr = getattr(primary if primary is not None else result, "stderr", None)
        # A retained stream is never a truncated prefix. On timeout, stderr
        # means exactly the available bytes carried by TimeoutExpired, not EOF.
        stdout_complete = type(raw) is bytes
        stderr_complete = type(stderr) is bytes and len(stderr) <= 64 * 1024
        terminal = {
            "schema": "motionwright.native.film180.original-packet-probe/1",
            "phase": stage, "master_sha256": master_sha,
            "argv": argv, "timeout_seconds": 60,
            "exit_code": getattr(result, "returncode", None),
            "deadline_exceeded": isinstance(primary, subprocess.TimeoutExpired),
            "error_class": type(primary).__name__ if primary is not None else None,
            "stdout_observed_bytes": stdout_size,
            "stderr_observed_bytes": len(stderr) if type(stderr) is bytes else None,
            "stdout_sha256": hashlib.sha256(raw).hexdigest() if stdout_complete else None,
            "stderr_sha256": hashlib.sha256(stderr).hexdigest() if stderr_complete else None,
            "stdout_original_included": False, "stderr_original_included": False,
            "stdout_within_retention_bound": stdout_complete,
            "stderr_within_retention_bound": stderr_complete,
            "automatic_retry": False, "native_effect_invoked": False,
            "native_terminal_inferred_from_exit": False,
            "terminal_captured_before_json_parse": True,
        }
        for stream, payload, complete in (
                ("stdout", raw, stdout_complete), ("stderr", stderr, stderr_complete)):
            if not complete:
                continue
            try:
                new_bytes(attempt.evidence_dir / (label + "." + stream), payload)
                terminal[stream + "_original_included"] = True
            except BaseException as error:
                capture_errors.append((stream + "-retain", error))
        terminal["custody_failures"] = [kind for kind, _ in capture_errors]
        try:
            write_private_json(attempt.evidence_dir / (label + ".terminal.json"), terminal)
        except BaseException as error:
            capture_errors.append(("terminal-retain", error))
        if capture_errors:
            try:
                attempt.secondary(label + ".capture")
            except BaseException:
                pass
        if primary is not None:
            raise primary.with_traceback(primary_traceback)
        require(type(getattr(result, "returncode", None)) is int
                and result.returncode == 0 and type(stdout_size) is int
                and 0 < stdout_size <= 1024 * 1024,
                "Film180_packet_original_bounded_probe")
        if capture_errors:
            raise capture_errors[0][1]
        require(stderr_complete, "Film180_packet_original_stderr_bounded_probe")
    from native_evidence_projection import strict, project
    from native_mlt_evidence_selection import PACKETS
    require(digest(master) == master_sha, "Film180_master_changed_during_packet_probe")
    new_bytes(attempt.evidence_dir / (stage + "-original-master-packets.json"), raw)
    value = project(strict(raw), PACKETS)
    write_private_json(attempt.evidence_dir / (stage + "-original-packet-capture.json"), {
        "schema": "motionwright.native.film180.original-packets/2", "phase": stage,
        "master_sha256": master_sha, "original_packet_document_sha256": hashlib.sha256(raw).hexdigest(),
        "original_packet_document_bytes": len(raw), "packet_count": len(value["packets"]),
        "native_effect_invoked": False, "automatic_retry": False,
        "packet_PTS_and_AAC_priming_verified": False,
    })

def stage_and_decode(paths: dict[str, Path], connection: Path,
                     database: Path, project_file: Path, source_audio: Path,
                     stage: str, env: dict[str, str], evidence: Path,
                     *, attempt: FilmAttempt, daemon_pid: int) -> dict:
    from native_film180_media_readback import verified_source_readback
    source_sha = digest(source_audio)
    require(source_sha == attempt.wave_sha, "Film180_source_WAV_changed_between_stages")
    render_path = evidence / f"{stage}-native-Film180-evidence.json"
    native = attempt.command(stage + ".render",
        ["render", str(database), str(connection), str(render_path)],
        env, timeout=STAGE_TIMEOUT, native=True,
    )
    require(native.get("native_film180_host") == "SUCCEEDED_REPORTED"
            and native.get("frame_count") == 180,
            "original_native_Film180_Broker_did_not_complete")
    attempt.confirm_native(stage + ".render")
    film = json.loads(render_path.read_text(encoding="utf-8"))
    pixels = original_png_index(paths["output"], film)
    staged = evidence / f"{stage}-staged-PCM.json"
    if stage == "original":
        attempt.command(stage + ".stage-audio",
            ["stage-audio", str(database), str(connection), str(source_audio),
             ORIGINAL_AUDIO_OWNER_NAME, str(staged)],
            env,
        )
    else:
        require((paths["output"] / ORIGINAL_AUDIO_OWNER_NAME).is_file()
                and digest(paths["output"] / ORIGINAL_AUDIO_OWNER_NAME) == source_sha,
                "edited_Film180_original_owner_audio_changed_between_revisions")
    master_evidence = evidence / f"{stage}-H264-AAC-native-master.json"
    saved = json.loads(project_file.read_text(encoding="utf-8"))
    with retained_live_observer(
            attempt, stage, daemon_pid, paths["output"]) as (watcher, stop_and_retain, witness_custody):
        native_master = attempt.command(stage + ".master",
            ["master", str(database), str(connection), str(render_path),
             ORIGINAL_AUDIO_OWNER_NAME, source_sha, str(master_evidence)],
            env, timeout=STAGE_TIMEOUT, native=True,
        )
        require(native_master.get("frame_count") == 180
                and native_master.get("native_result_reported") == "SUCCEEDED",
                "original_Film180_H264_AAC_master_not_completed")
        attempt.confirm_native(stage + ".master")
        stop_and_retain()
        master = json.loads(master_evidence.read_text(encoding="utf-8"))
        relative = master.get("master", {}).get("artifact", {}).get("path")
        require(type(relative) is str and relative.endswith(".mp4")
                and ".." not in Path(relative).parts
                and not Path(relative).is_absolute(),
                "original_Film180_master_path_not_owner_bounded")
        original_mp4 = paths["output"] / relative
        require(original_mp4.is_file() and not original_mp4.is_symlink(),
                "original_Film180_owner_master_missing")
        video_sha = digest(original_mp4)
        require(relative == original_native_master_name(saved["id"], saved["revision"], stage),
                "actual_loaded_original_MLT_output_does_not_match_returned_master")
        live_proof = watcher.require_one(original_mp4)
        artifact = master["master"]["artifact"]
        require(type(artifact) is dict and artifact.get("root") == "output"
                and type(artifact.get("sha256")) is str
                and artifact["sha256"] == video_sha
                and type(artifact.get("bytes")) is int
                and artifact["bytes"] == original_mp4.stat().st_size
                and type(live_proof) is dict
                and type(live_proof.get("published_output_sha256")) is str
                and live_proof["published_output_sha256"] == artifact["sha256"]
                and type(live_proof.get("published_output_bytes")) is int
                and live_proof["published_output_bytes"] == artifact["bytes"],
                "Film180_original_consumer_differs_from_native_result_ack")
        loaded_module = {
            "original_MLT_command_basename": relative,
            "actual_mapped_original_MLT_module_sha256": observed.REPAIRED_MODULE_SHA256,
            "original_live_pid_observer_receipt": live_proof,
            "observer_diagnostic_sha256": witness_custody["diagnostic_sha256"],
            "native_melt_effect_started_by_witness": False,
        }
        write_private_json(evidence / f"{stage}-original-live-MLT-loaded-module.json", loaded_module)
    try:
        retain_original_audio_packets(attempt, stage, original_mp4, video_sha)
        receipt = verified_source_readback(
            project_file, digest(project_file), source_audio, source_sha,
            render_path, digest(render_path), master_evidence, digest(master_evidence),
            original_mp4, video_sha, pcm_capture=attempt.capture_original_pcm(stage, video_sha),
        )
    finally:
        try:
            from native_aac_clock_custody import retain_clock_diagnostic
            probe = shutil.which("ffprobe")
            require(probe is not None, "Film180_clock_probe_unavailable")
            clock_capture = retain_clock_diagnostic(
                evidence_dir=evidence, phase=stage, scenario="film180-g2",
                master=original_mp4, master_sha256=video_sha,
                ffprobe=Path(probe).resolve(strict=True), env=env,
                source_document=project_file, original_wav=evidence / "firstparty-source.wav",
                packets=evidence / (stage + "-original-master-packets.json"),
                decoded_pcm=evidence / (stage + "-original-master-decoded.s16le"))
            if clock_capture["state"] != "CAPTURED":
                attempt.secondary(stage + ".clock-custody-incomplete")
        except Exception:
            attempt.secondary(stage + ".clock-custody-failed")
    readback = evidence / f"{stage}-independent-AV-content.json"
    write_private_json(readback, receipt)
    all_frames = actual_all180_original_RGB_digests(original_mp4, "ffmpeg")
    all_frames_path = evidence / f"{stage}-all180-decoded-original-RGB24.json"
    write_private_json(all_frames_path, all_frames)
    lossy_rgb = native_png_vs_h264_observations(
        paths["output"], film, original_mp4, evidence, stage,
    )
    color_comparison = evidence / f"{stage}-native-PNG-versus-H264-RGB-observation.json"
    write_private_json(color_comparison, lossy_rgb)
    require(digest(source_audio) == attempt.wave_sha, "Film180_source_WAV_changed_during_stage")
    return {
        "stage": stage, "source_project_SHA": digest(project_file),
        "original_audio_SHA": source_sha,
        "Film_manifest": pixels,
        "master_sha": video_sha,
        "independent_content_sha": digest(readback),
        "decoded_all180_RGB_manifest_sha256": digest(all_frames_path),
        "all_original_180_video_frames_decoded": all_frames["source_frame_count"] == 180,
        "all180_native_PNG_vs_H264_RGB_observation_sha256": digest(color_comparison),
        "H264_human_perceptual_quality_acceptance": False,
        "original_H264_png_pixel_equality_claim": False,
        "actual_loaded_repaired_MLT_module": loaded_module["actual_mapped_original_MLT_module_sha256"],
        "physical_native_media_witness": "PASS",
        "master_file": original_mp4,
        "Film_receipt": render_path,
    }


def execute(input_path: Path) -> None:
    attempt = FilmAttempt.prepare(input_path)
    original_error = None
    try:
        plan = checked_plan(attempt)
        write_private_json(attempt.evidence_dir / "source-plan.json", plan)
        from native_mlt_sequence_e2e import native_owner_workspace
        with native_owner_workspace(attempt) as temporary:
            paths = closed_paths(Path(temporary))
            env = {key: value for key, value in os.environ.items()
                   if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}}
            env.update(
                XDG_RUNTIME_DIR=str(paths["runtime"]), XDG_STATE_HOME=str(paths["state"]),
                XDG_CONFIG_HOME=str(paths["config"]), XDG_CACHE_HOME=str(paths["scratch"]),
                RUST_BACKTRACE="1", SEMWRIGHT_TEST_DRIVER_SANDBOX="1",
                SEMWRIGHT_TEST_SANDBOX_HELPER=str(SW_BIN / "semwright-sandbox"),
            )
            database = paths["motionwright-data"] / "original-film180.sqlite3"
            source_wave = paths["motionwright-data"] / "firstparty-film180.wav"
            first_project = attempt.evidence_dir / "original-project.json"
            source = attempt.command("source.seed",
                ["seed", str(database), str(source_wave), str(first_project)], env)
            require(source.get("frame_count") == 180 and source.get("saved_reopen") is True
                    and source.get("native_render_performed") is False,
                    "original_CAS_authoring_source_not_persisted")
            attempt.retain_wave(source_wave)
            fresh_source_readback(database, attempt.evidence_dir / "original-reopened-before-render.json",
                                  env, attempt=attempt, stage="original")
            with bound_broker(paths, attempt.evidence_dir, env, {
                **source, "resource": "project:" + source["project_id"],
            }, attempt) as (connection, manifest, daemon_pid):
                original = stage_and_decode(
                    paths, connection, database, first_project, source_wave,
                    "original", env, attempt.evidence_dir, attempt=attempt, daemon_pid=daemon_pid)
                attempt.completed_stages["original"] = {
                    key: str(value) if isinstance(value, Path) else value for key, value in original.items()}
                edited_path = attempt.evidence_dir / "edited-project.json"
                edited = attempt.command("source.edit",
                    ["edit", str(database), str(original["Film_receipt"]), str(edited_path)], env)
                require(edited.get("frame_count") == 180
                        and edited.get("revision") == source["revision"] + 1
                        and edited.get("first_native_result_source_bound") is True,
                        "original_CAS_edit_after_real_native_result_not_persisted")
                fresh_source_readback(database, attempt.evidence_dir / "edited-reopened-before-render.json",
                                      env, attempt=attempt, stage="edited")
                later = stage_and_decode(
                    paths, connection, database, edited_path, source_wave,
                    "edited", env, attempt.evidence_dir, attempt=attempt, daemon_pid=daemon_pid)
                attempt.completed_stages["edited"] = {
                    key: str(value) if isinstance(value, Path) else value for key, value in later.items()}
                edit_pixels = original_frame_difference("ffmpeg", original["master_file"], later["master_file"])
                write_private_json(attempt.evidence_dir / "original-stage-status.json", {
                    "schema": "motionwright.native.film180-original-and-edited/1",
                    "product_source_sha": RUN_SHA, "semwright_source_pin": PIN,
                    "original": attempt.completed_stages["original"],
                    "edited": attempt.completed_stages["edited"],
                    "original_edited_pixel_change": edit_pixels,
                    "MLT_module_installed_SHA": manifest["mlt_installed_module_sha256"],
                    "actual_loaded_MLT_process_observed_here": True,
                    "native_media_content_probe_completed": True,
                    "all180_decoded_RGB24_hashes_source_bound": True,
                    "H264_perceptual_pixel_fidelity_claim": False,
                    "human_review": False, "release_authorized": False,
                })
                require(observed.verified_installed_melt_alias(attempt.inputs["known_melt_sha256"])
                        == attempt.melt_alias_observation, "Film180_melt_alias_changed_after_native_commands")
                attempt.completed = True
    except BaseException as error:
        original_error = (error, error.__traceback__)
        attempt.retain_error(error)
    try:
        attempt.finish()
    except BaseException:
        attempt.secondary("final-custody")
        if original_error is None:
            raise
    if original_error is not None:
        raise original_error[0].with_traceback(original_error[1])
    require(attempt.completed and not attempt.secondary_failures, "Film180_original_custody_incomplete")
    print(json.dumps({
        "source_sha": RUN_SHA, "native_terminal": "SUCCESS_REPORTED",
        "original_and_edited_content_observed": True,
        "private_evidence_seal": "PENDING_WORKFLOW_CUSTODY",
        "physical_AV_master_acceptance": False, "human_acceptance": False,
        "release_authorized": False,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--g2-inputs", type=Path, required=True)
        execute(parser.parse_args().g2_inputs)
    except NativeCommandFailure as error:
        # Closed vocabulary only, never stderr, tickets, paths or input media.
        print(json.dumps({
            "g2": "FAILED_NO_RETRY",
            "code": error.diagnostic.get("reported_error_code", "unclassified"),
            "source_outcome": error.diagnostic.get("source_outcome", "UNKNOWN"),
            "human_acceptance": False,
        }, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from None
    except (Film180G2Rejected, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, OSError, ValueError, KeyError) as error:
        print(json.dumps({
            "g2": "FAILED_NO_RETRY",
            "failure_class": type(error).__name__,
            "human_acceptance": False,
        }, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from None
