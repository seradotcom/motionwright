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
    manifest_file = root / "artifact-manifest.json"
    require(root.is_dir() and not root.is_symlink()
            and manifest_file.is_file() and not manifest_file.is_symlink(),
            "native_Film_artifact_manifest_missing")
    require(digest(manifest_file) == artifact.get("manifest_sha256"),
            "native_Film_manifest_digest_not_original")
    manifest = json.loads(manifest_file.read_bytes())
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
    return {
        "stage": stage, "source_project_SHA": digest(project_file),
        "original_audio_SHA": source_sha,
        "Film_manifest": pixels,
        "master_sha": video_sha,
        "independent_content_sha": digest(readback),
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
