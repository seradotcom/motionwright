#!/usr/bin/env python3
"""Run ONE new, authorized Film180 native E2E in ephemeral GitHub Actions.

This is an orchestration wrapper, NOT another renderer/SDK/runtime. It binds
the existing pinned Semwright Motion Canvas and MLT drivers, saved StudioService
source, Broker, original Film result, native MLT master and decoded media. It
does not silently retry a failed, timed-out or UNKNOWN native effect.

Separate A1/A6 authorization is required before calling main(), including
installation of a source-locked repaired MLT module in the ephemeral runner.
The live loaded-module process witness remains an independent A6 gate.
All created state is first-party synthetic, never the private r38 project.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib

from native_av_failure_guard import NativeCommandFailure, run_sanitized_json

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


def checked_plan() -> dict:
    require(sys.platform == "linux"
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("CI") == "true"
            and os.environ.get("RUNNER_OS") == "Linux"
            and os.environ.get("MOTIONWRIGHT_APPROVED_FILM180_G2") == "1",
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
            "A1_integrated_original_Film180_independent_reader_missing")
    require((ROOT / "tooling" / "native_mlt_loaded_module.py").is_file(),
            "A1_integrated_original_repaired_MLT_loaded_module_reader_missing")
    require(MOTION_RUNTIME.is_dir()
            and (MOTION_RUNTIME / "package-lock.json").is_file(),
            "original_pinned_Motion_Canvas_runtime_unavailable")
    binaries = {name: shutil.which(name) for name in (
        "melt", "ffprobe", "ffmpeg", "bwrap", "node",
    )}
    require(all(binaries.values()), "original_native_Broker_runtime_tool_missing")
    module_sha = os.environ.get("MOTIONWRIGHT_MLT_G2_MODULE_SHA256", "")
    receipt_path = Path(os.environ.get("MOTIONWRIGHT_MLT_G2_INSTALL_RECEIPT", ""))
    require(HEX64.fullmatch(module_sha) is not None
            and receipt_path.is_absolute()
            and receipt_path.is_file()
            and not receipt_path.is_symlink()
            and TRUSTED_MLT.is_file()
            and not TRUSTED_MLT.is_symlink()
            and digest(TRUSTED_MLT) == module_sha,
            "MLT_repaired_installed_ELF_not_independently_admitted")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    require(receipt.get("schema") == "motionwright.native.mlt-module-installed/1"
            and receipt.get("module_replaced") is True
            and receipt.get("actual_installed_module_sha256") == module_sha
            and receipt.get("repaired_MLT_source_sha256") ==
            "246e84b02d25d0fe5ea08d59d5abb03c6dfd42d5d15dabeb464c8051f8ed8de0"
            and receipt.get("new_native_master_issued") is False,
            "original_MLT_runtime_admission_receipt_not_bound_to_G1")
    original_module_install_digest = digest(receipt_path)
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
    melt = Path(shutil.which("melt") or "").resolve(strict=True)
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
                 seed: dict):
    manifest = closed_owner_setup(paths)
    socket = paths["runtime"] / "broker.sock"
    session = paths["runtime"] / "motionwright.session"
    daemonlog = (evidence / "semwrightd.original.private.log").open("xb")
    daemon = subprocess.Popen(
        [
            str(SW_BIN / "semwrightd"), "--config", str(manifest["owner"]),
            "--socket", str(socket),
        ],
        env=env, stdin=subprocess.DEVNULL, stdout=daemonlog,
        stderr=daemonlog, start_new_session=True,
    )
    try:
        wait_socket(daemon, socket)
        arguments = [
            str(SW_BIN / "semwright"), "--socket", str(socket),
            "--session-file", str(session), "--json",
        ]
        discovered = run_sanitized_json(
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
            response = run_sanitized_json(
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
        yield connection, manifest
    finally:
        if daemon.poll() is None:
            try:
                os.killpg(daemon.pid, signal.SIGTERM)
                daemon.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                os.killpg(daemon.pid, signal.SIGKILL)
                daemon.wait(timeout=5)
        daemonlog.close()


def source_command(args: list[str], env: dict[str, str],
                   *, timeout: int = 40) -> dict:
    return run_sanitized_json([str(FILM_BIN), *args], env=env, timeout=timeout)


def fresh_source_readback(database: Path, receipt: Path, env: dict[str, str]) -> None:
    require(not receipt.exists() and database.is_file(),
            "original_saved_Film180_readback_not_fresh")
    result = subprocess.run(
        [str(FILM_BIN), "readback", str(database), str(receipt)],
        env=env, capture_output=True, timeout=40,
    )
    require(result.returncode == 0
            and receipt.is_file() and receipt.stat().st_size < 1024 * 1024,
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
    require(change >= 250, "edited_Film180_final_MP4_did_not_retain_human_canvas_change")
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


class OriginalMeltModuleWatcher:
    """Passive exact-original /proc witness, with no possibility of dispatch.

    A6 has a separately controlled native G2 auth grant; this observer never
    invokes the driver, writes system modules or starts/retries original jobs.
    """

    def __init__(self, project_id: str, revision: int, stage: str,
                 melt_executable: Path, repaired_module_sha: str) -> None:
        require(sys.platform == "linux",
                "original_live_melt_proc_observation_linux_only")
        self.output_name = original_native_master_name(project_id, revision, stage)
        self.module_sha = repaired_module_sha
        self.melt = melt_executable.resolve(strict=True)
        self.melt_sha = digest(self.melt)
        self.receipt = None
        self.attempts = 0
        self._finished = threading.Event()
        self._worker = threading.Thread(
            target=self._observe, name="film180-original-mlt-module-witness",
            daemon=True,
        )

    def start(self) -> None:
        self._worker.start()

    def _observe(self) -> None:
        from native_mlt_loaded_module import inspect_live_original
        while not self._finished.is_set() and self.receipt is None:
            for process in Path("/proc").iterdir():
                if not process.name.isdecimal() or not (process / "comm").is_file():
                    continue
                try:
                    if (process / "comm").read_text(encoding="utf-8").strip() != "melt":
                        continue
                    self.attempts += 1
                    receipt = inspect_live_original(
                        int(process.name),
                        self.melt, self.melt_sha,
                        TRUSTED_MLT, self.module_sha, self.output_name,
                    )
                except (FileNotFoundError, ProcessLookupError,
                        PermissionError, OSError, ValueError):
                    # The process may have exited or be from another owner.
                    # Never adopt a foreign PID or treat absence as success.
                    continue
                if (receipt.get("original_runtime_loaded_module_attested") is True
                        and receipt.get("original_repaired_avformat_module_sha256")
                        == self.module_sha
                        and receipt.get("source_bound_native_consumer_argv", {})
                        .get("selected_original_output_basename") == self.output_name):
                    self.receipt = receipt
                    break
            self._finished.wait(0.035)

    def stop_and_observe(self) -> dict:
        self._finished.set()
        self._worker.join(timeout=5)
        require(not self._worker.is_alive(),
                "original_melt_loaded_module_witness_did_not_terminate")
        require(self.receipt is not None,
                "original_native_melt_never_loaded_the_source_bound_repaired_MLT_ELF")
        return {
            "schema": "motionwright.native.film180.original-Mlt-mapped-witness/1",
            "original_MLT_command_basename": self.output_name,
            "original_live_pid_observer_receipt": self.receipt,
            "actual_mapped_original_MLT_module_sha256": self.module_sha,
            "original_melt_process_samples": self.attempts,
            "native_melt_effect_started_by_witness": False,
            "original_aac_terminal_right_samples_verified_by_witness": False,
        }


def stage_and_decode(paths: dict[str, Path], connection: Path,
                     database: Path, project_file: Path, source_audio: Path,
                     stage: str, env: dict[str, str], evidence: Path) -> dict:
    from native_film180_media_readback import verified_source_readback
    source_sha = digest(source_audio)
    render_path = evidence / f"{stage}-native-Film180-evidence.json"
    native = source_command(
        ["render", str(database), str(connection), str(render_path)],
        env, timeout=STAGE_TIMEOUT,
    )
    require(native.get("native_film180_host") == "SUCCEEDED_REPORTED"
            and native.get("frame_count") == 180,
            "original_native_Film180_Broker_did_not_complete")
    film = json.loads(render_path.read_text(encoding="utf-8"))
    pixels = original_png_index(paths["output"], film)
    staged = evidence / f"{stage}-staged-PCM.json"
    if stage == "original":
        source_command(
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
    watcher = OriginalMeltModuleWatcher(
        saved["id"], saved["revision"], stage,
        Path(shutil.which("melt") or ""),
        os.environ["MOTIONWRIGHT_MLT_G2_MODULE_SHA256"],
    )
    watcher.start()
    try:
        native_master = source_command(
            ["master", str(database), str(connection), str(render_path),
             ORIGINAL_AUDIO_OWNER_NAME, source_sha, str(master_evidence)],
            env, timeout=STAGE_TIMEOUT,
        )
    except BaseException:
        # Original native result is uncertain: stop the passive watcher, but
        # retain the *primary* original failure and never issue the job again.
        watcher._finished.set()
        watcher._worker.join(timeout=5)
        raise
    loaded_module = watcher.stop_and_observe()
    write_private_json(
        evidence / f"{stage}-original-live-MLT-loaded-module.json",
        loaded_module,
    )
    require(native_master.get("frame_count") == 180
            and native_master.get("native_result_reported") == "SUCCEEDED",
            "original_Film180_H264_AAC_master_not_completed")
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
    require(relative == loaded_module["original_MLT_command_basename"],
            "actual_loaded_original_MLT_output_does_not_match_returned_master")
    receipt = verified_source_readback(
        project_file, digest(project_file), source_audio, source_sha,
        render_path, digest(render_path), master_evidence, digest(master_evidence),
        original_mp4, video_sha,
    )
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


def execute() -> None:
    plan = checked_plan()
    temp = Path(os.environ["RUNNER_TEMP"])
    evidence = temp / ("mw-film180-g2-" + RUN_SHA[:12] + "-"
                       + RUN_ID + "-" + RUN_ATTEMPT)
    require(not evidence.exists(), "original_Film180_job_evidence_already_exists")
    evidence.mkdir(mode=0o700)
    write_private_json(evidence / "source-plan.json", plan)
    data = evidence / "owner-runtime"
    data.mkdir(mode=0o700)
    paths = closed_paths(data)
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}
    }
    env.update(
        HOME=str(paths["home"]),
        XDG_RUNTIME_DIR=str(paths["runtime"]),
        XDG_STATE_HOME=str(paths["state"]),
        RUST_BACKTRACE="1",
        SEMWRIGHT_TEST_DRIVER_SANDBOX="1",
        SEMWRIGHT_TEST_SANDBOX_HELPER=str(SW_BIN / "semwright-sandbox"),
    )
    database = paths["motionwright-data"] / "original-film180.sqlite3"
    wave = paths["motionwright-data"] / "firstparty-film180.wav"
    first_project = evidence / "original-project.json"
    source = source_command(
        ["seed", str(database), str(wave), str(first_project)], env,
    )
    require(source.get("frame_count") == 180
            and source.get("saved_reopen") is True
            and source.get("native_render_performed") is False,
            "original_CAS_authoring_source_not_persisted")
    fresh_source_readback(database, evidence / "original-reopened-before-render.json", env)
    with bound_broker(paths, evidence, env, {
        **source, "resource": "project:" + source["project_id"],
    }) as (connection, manifest):
        original = stage_and_decode(
            paths, connection, database, first_project, wave,
            "original", env, evidence,
        )
        edited_path = evidence / "edited-project.json"
        edited = source_command(
            ["edit", str(database), str(original["Film_receipt"]), str(edited_path)],
            env,
        )
        require(edited.get("frame_count") == 180
                and edited.get("revision") == source["revision"] + 1
                and edited.get("first_native_result_source_bound") is True,
                "original_CAS_edit_after_real_native_result_not_persisted")
        fresh_source_readback(database, evidence / "edited-reopened-before-render.json", env)
        later = stage_and_decode(
            paths, connection, database, edited_path, wave,
            "edited", env, evidence,
        )
        edit_pixels = original_frame_difference(
            "ffmpeg", original["master_file"], later["master_file"],
        )
        # A separate A6 observer MUST independently attest live /proc maps
        # and module identity DURING the new melt process. An installed module
        # hash alone is not a loaded-module receipt, nor a G2 release claim.
        write_private_json(evidence / "original-stage-status.json", {
            "schema": "motionwright.private.native-film180-G2-result/1",
            "product_source_sha": RUN_SHA,
            "semwright_source_pin": PIN,
            "original": {k: v for k, v in original.items() if k != "master_file"
                         and k != "Film_receipt"},
            "edited": {k: v for k, v in later.items() if k != "master_file"
                       and k != "Film_receipt"},
            "original_edited_pixel_change": edit_pixels,
            "MLT_module_installed_SHA": manifest["mlt_installed_module_sha256"],
            "actual_loaded_MLT_process_observed_here": True,
            "native_media_content_probe_completed": True,
            "all180_decoded_RGB24_hashes_source_bound": True,
            "H264_perceptual_pixel_fidelity_claim": False,
            "private_original_r38_project_rendered": False,
            "human_review": False, "release_authorized": False,
        })
    # This runner deliberately publishes no media or private session; A6
    # owns safe-artifact selection and the actual loaded-module observation.
    print(json.dumps({
        "source_sha": RUN_SHA, "synthetic_Film180_original_and_edited": "PASS",
        "independent_decoded_H264_AAC_content": "PASS",
        "native_MLT_module_loaded_attestation": "PASS_FROM_LIVE_MAPPED_ORIGINAL",
        "original_private_r38": "NOT_RUN",
        "human_acceptance": False, "release_authorized": False,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        execute()
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
