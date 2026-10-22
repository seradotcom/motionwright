#!/usr/bin/env python3
"""Exact-SHA Motionwright -> Semwright -> Manim Community round-trip acceptance.

CI-only by design. The test creates application-owned Motionwright state, provisions
Motionwright's bounded Manim Community Driver through the real Semwright Broker/Driver
Host sandbox, renders a real MP4 with Manim Community 0.21.0, and independently probes
and hashes the artifact.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
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
MW_CAMERA_SEED = ROOT / "target" / "debug" / "examples" / "native-manim-camera-seed"
MW_CAMERA_SOURCE = ROOT / "target" / "debug" / "examples" / "native-manim-camera-e2e"
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
    expected_receipt = {
        "schema": 2, "manim_version": MANIM_VERSION, "pyav_version": "15.0.0",
        "source_color_policy": "srgb-rgba8-opaque-v1",
        "color_recipe": "srgb-to-rec709-full12-v2",
    }
    if (type(runtime_receipt) is not dict
            or type(runtime_receipt.get("schema")) is not int
            or runtime_receipt != expected_receipt):
        raise SystemExit(f"unexpected Manim runtime receipt: {runtime_receipt!r}")

    probe_env = os.environ.copy()
    probe_env.update(
        PYTHONPATH=str(MANIM_RUNTIME / "site-packages"),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    observed_versions = json.loads(subprocess.check_output(
        [
            str(PYTHON),
            "-c",
            "import av,json,manim;print(json.dumps({"
            "'manim_version':manim.__version__,'pyav_version':av.__version__}))",
        ],
        env=probe_env,
        text=True,
    ))
    if observed_versions != {"manim_version": MANIM_VERSION, "pyav_version": "15.0.0"}:
        raise SystemExit(f"unexpected Manim/PyAV runtime versions: {observed_versions!r}")


def retain_runtime_inventory(evidence: Path) -> str:
    """Retain the measured runtime identity inside the existing private custody."""
    source = ROOT / "verification/native-manim-e2e/runtime-inventory.json"
    raw = source.read_bytes()
    if not 0 < len(raw) <= 2 * 1024 * 1024:
        raise ValueError("Manim runtime inventory exceeds its bound")
    value = json.loads(raw)
    context = {"source_sha": GITHUB_SHA, "semwright_sha": PIN,
               "run_id": os.environ.get("GITHUB_RUN_ID"),
               "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT")}
    if (type(value) is not dict
            or value.get("schema") != "motionwright.manim.runtime-inventory/2"
            or value.get("context") != context
            or value.get("manim_version") != MANIM_VERSION
            or value.get("pyav_version") != "15.0.0"
            or value.get("runtime_receipt_sha256") != digest(MANIM_RUNTIME / "runtime.json")):
        raise ValueError("Manim runtime inventory context differs from this invocation")
    for name, path in (("python", PYTHON), ("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
        expected = {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}
        if value.get("binaries", {}).get(name) != expected:
            raise ValueError("Manim runtime inventory binary changed: " + name)
    wheel = value.get("pyav_wheel", {})
    wheel_sha = wheel.get("sha256")
    if (type(wheel_sha) is not str or len(wheel_sha) != 64
            or any(c not in "0123456789abcdef" for c in wheel_sha)
            or type(wheel.get("bytes")) is not int or wheel["bytes"] <= 0
            or not str(wheel.get("filename", "")).startswith("av-15.0.0-")
            or not str(wheel.get("filename", "")).endswith(".whl")):
        raise ValueError("Manim runtime inventory lacks an observed PyAV wheel")
    libraries = value.get("libav_versions")
    if (type(libraries) is not dict or not libraries
            or any(type(name) is not str or type(version) is not list
                   or len(version) != 3
                   or any(type(part) is not int or part < 0 for part in version)
                   for name, version in libraries.items())):
        raise ValueError("Manim runtime inventory lacks observed libav versions")
    packages = (MANIM_RUNTIME / "site-packages").resolve(strict=True)
    rows = value.get("pyav_native_files")
    if type(rows) is not list or not 0 < len(rows) <= 512:
        raise ValueError("Manim runtime inventory native-file roster is invalid")
    seen = set()
    for row in rows:
        if type(row) is not dict or type(row.get("path")) is not str:
            raise ValueError("Manim runtime inventory native-file entry is invalid")
        relative = Path(row["path"])
        if (relative.is_absolute() or ".." in relative.parts
                or row["path"] in seen):
            raise ValueError("Manim runtime inventory native-file path is invalid")
        path = packages / relative
        if (path.is_symlink() or not path.resolve(strict=True).is_relative_to(packages)
                or not path.is_file() or path.stat().st_size != row.get("bytes")
                or digest(path) != row.get("sha256")
                or row.get("wheel_sha256") != row.get("sha256")):
            raise ValueError("Manim runtime inventory native-file bytes changed")
        seen.add(row["path"])
    target = evidence / "runtime-inventory.json"
    with target.open("xb") as stream:
        stream.write(raw)
    target.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()


def require_original_native_video(native, capabilities, output_root, work_root, evidence):
    """Check the Rust-admitted original observation; never decode, render or repair it."""
    from native_manim_camera_evidence import (
        JOB, SHA, object_bytes, read, require, sha,
    )
    def private_bytes(path, payload):
        # Exclusive retention only; preserve any partial output on a later failure.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def private_json(path, value):
        private_bytes(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())

    command = "driver.manim-community.render.result"
    result = native.get("result", {})
    returned = result.get("result", {})
    observed = returned.get("result", {})
    receipt = returned.get("receipt", {})
    data = observed.get("data", {})
    provenance = observed.get("execution", {}).get("provenance", {})
    require(returned.get("replayed") is False
            and observed.get("provenance_checked") is True
            and receipt.get("command") == command
            and receipt.get("stage") == "completed"
            and all(receipt.get(key) == native.get(key)
                    for key in ("project_id", "generation", "revision"))
            and receipt.get("payload", {}).get("result") == observed,
            "n3_original_completed_receipt")
    job = result.get("job_ref")
    match = JOB.fullmatch(job) if type(job) is str else None
    require(match is not None and data.get("job_ref") == job
            and receipt.get("payload", {}).get("job_ref") == job
            and data.get("state") == "succeeded", "n3_original_job")
    generation = provenance.get("provider_generation")
    require(type(generation) is int and generation >= 0
            and provenance.get("provider") == "driver:manim-community"
            and provenance.get("source") == "driver"
            and observed.get("execution", {}).get("backend") == "driver:manim-community",
            "n3_original_provider")
    rows = [row for row in capabilities if row.get("id") == command]
    require(len(rows) == 1, "n3_original_catalog")
    metadata = rows[0].get("provenance", {})
    descriptor = provenance.get("descriptor_sha256")
    require(type(descriptor) is str and SHA.fullmatch(descriptor)
            and metadata.get("descriptor_sha256") == descriptor
            and metadata.get("provider") == provenance["provider"]
            and metadata.get("source") == provenance["source"]
            and metadata.get("source_version") == provenance.get("provider_version")
            and rows[0].get("version") == provenance.get("capability_version"),
            "n3_original_descriptor")
    if "original_result_receipt" in native:
        require(native["original_result_receipt"] == receipt,
                "n3_reopened_terminal_receipt")
        start = native.get("original_start_receipt", {}).get("payload", {}).get("result", {})
        require(start.get("data", {}).get("job_ref") == job
                and start.get("execution", {}).get("provenance", {}).get(
                    "provider_generation") == generation, "n3_original_provider_generation")
    artifact = data.get("artifact", {})
    verified = result.get("artifact", {})
    require(all(artifact.get(key) == verified.get(key)
                for key in ("relative_path", "sha256", "bytes"))
            and artifact.get("plan_sha256") == result.get("plan_sha256")
            and "decoded_video_error" not in artifact, "n3_original_artifact")
    profile = result.get("profile", {})
    require(all(type(profile.get(key)) is int and profile[key] > 0
                for key in ("width", "height", "frame_rate")), "n3_original_profile")
    token = match[1]
    relative = (f"videos/mw_{token}/{profile['height']}p{profile['frame_rate']}/"
                f"mw_{token}.mp4")
    require(artifact.get("relative_path") == relative, "n3_original_media_path")
    media = read(output_root / relative, 512 * 1024 * 1024)
    require(len(media) == artifact.get("bytes") and sha(media) == artifact.get("sha256"),
            "n3_original_media_digest")
    descriptor_value = artifact.get("decoded_video")
    require(type(descriptor_value) is dict, "n3_complete_observation_missing")
    report_relative = relative + ".decoded-video.json"
    require(descriptor_value.get("relative_path") == report_relative,
            "n3_original_report_path")
    raw = read(output_root / report_relative)
    require(len(raw) == descriptor_value.get("bytes")
            and sha(raw) == descriptor_value.get("sha256"), "n3_original_report_digest")
    report = object_bytes(raw)
    summary = descriptor_value.get("summary", {})
    source = read(work_root / f"mw_{token}.py", 256 * 1024)
    require(report.get("artifact_sha256") == artifact["sha256"]
            and report.get("artifact_bytes") == artifact["bytes"]
            and report.get("source_sha256") == sha(source)
            and report.get("width") == profile["width"]
            and report.get("height") == profile["height"]
            and report.get("fps_num") == profile["frame_rate"]
            and report.get("fps_den") == 1, "n3_original_report_binding")
    # These are identity comparisons with the actual report, not a second color parser.
    keys = (
        "schema", "artifact_sha256", "artifact_bytes", "source_sha256", "decoder",
        "width", "height", "fps_num", "fps_den", "frame_count", "video_streams",
        "audio_streams", "codec", "pixel_format", "stream_color", "frame_color",
        "alpha_min", "alpha_max", "all_pixels_opaque", "rgba_sequence_sha256",
        "retained_png_bytes",
    )
    require(all(key in report and summary.get(key) == report[key] for key in keys),
            "n3_original_summary_binding")
    frames = report.get("frames")
    count = report.get("frame_count")
    require(type(count) is int and 0 < count <= 9000
            and type(frames) is list and len(frames) == count,
            "n3_original_frame_roster")
    frame_root = output_root / (report_relative + ".frames")
    expected_files = {f"{index:06}.png" for index in range(count)}
    require({path.name for path in frame_root.iterdir()} == expected_files,
            "n3_original_frame_files")
    retained = evidence / "native-video-originals"
    retained.mkdir(mode=0o700, exist_ok=False)
    private_bytes(retained / "decoded-video.json", raw)
    private_bytes(retained / "source.py", source)
    private_json(retained / "result-capability.json", rows[0])
    retained_frames = retained / "decoded-video.json.frames"
    retained_frames.mkdir(mode=0o700, exist_ok=False)
    png_total = 0
    for index, frame in enumerate(frames):
        name = f"{index:06}.png"
        require(frame.get("index") == index and frame.get("file") == name,
                "n3_original_frame_identity")
        png = read(frame_root / name, 32 * 1024 * 1024)
        require(len(png) == frame.get("png_bytes")
                and sha(png) == frame.get("png_sha256"), "n3_original_frame_digest")
        png_total += len(png)
        require(png_total <= 1024 * 1024 * 1024, "n3_original_png_budget")
        private_bytes(retained_frames / name, png)
    require(png_total == report.get("retained_png_bytes"), "n3_original_png_total")
    # Admission is computed by the production Rust parser in the original driver.
    require(summary.get("schema") == "motionwright.native-video-observation/1"
            and summary.get("opaque_rec709_h264_without_audio") is True
            and summary.get("square_progressive_all_frames") is True
            and summary.get("native_metadata_frame_count") == count
            and all(type(frame.get("native_metadata")) is dict for frame in frames),
            "n3_original_rust_admission")
    require(read(output_root / report_relative) == raw
            and read(work_root / f"mw_{token}.py", 256 * 1024) == source
            and digest(output_root / relative) == artifact["sha256"],
            "n3_originals_changed_during_verification")
    admission = {
        "schema": "motionwright.manim.original-native-video-admission/1",
        "status": "PASS", "authority": "original_driver_rust_parser_and_receipt",
        "job_ref": job, "provider_generation": generation,
        "result_descriptor_sha256": descriptor,
        "plan_sha256": result["plan_sha256"], "source_sha256": sha(source),
        "artifact_sha256": artifact["sha256"], "report_sha256": sha(raw),
        "frame_count": count, "retained_png_bytes": png_total,
        "api_frame_color": report["frame_color"],
        "native_metadata_frame_count": summary["native_metadata_frame_count"],
        "native_decode_or_render_invocations": 0,
    }
    private_json(evidence / "native-video-admission.json", admission)
    return admission


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


def captured_json(argv, *, env, evidence, label, timeout=60, camera_custody=None):
    """Capture original process streams in private files before interpreting them."""
    from native_manim_camera_evidence import CameraEvidenceError, object_bytes, read
    streams = []
    process = None
    try:
        for name in ("stdout", "stderr"):
            path = evidence / f"{label}.{name}.log"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            streams.append(os.fdopen(fd, "wb", buffering=0))
        process = subprocess.Popen(
            argv, env=env, stdin=subprocess.DEVNULL,
            stdout=streams[0], stderr=streams[1], start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if camera_custody is not None:
                camera_custody.native_process = {
                    "pid": process.pid, "timeout_seconds": timeout,
                    "process_returncode": None, "native_outcome": "UNKNOWN",
                    "automatic_retry": False,
                }
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            except BaseException as cleanup:
                if camera_custody is not None:
                    camera_custody.secondary("native-client-timeout-cleanup", cleanup)
            raise
        if camera_custody is not None:
            camera_custody.native_process = {
                "pid": process.pid, "process_returncode": code,
                "native_outcome": "NOT_INTERPRETED",
                "automatic_retry": False,
            }
        if code != 0:
            # The original Rust diagnostic and append-only journal are retained.
            # A nonzero wrapper process code cannot classify a native UNKNOWN.
            raise CameraEvidenceError(f"native_example_process_exit_{code}_see_original_private_diagnostic")
        value = object_bytes(read(evidence / f"{label}.stdout.log"))
        if camera_custody is not None:
            camera_custody.native_result = value
        return value
    finally:
        for stream in streams:
            try:
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException as cleanup:
                if camera_custody is not None:
                    camera_custody.secondary("native-process-stream-custody", cleanup)
            try:
                stream.close()
            except BaseException as cleanup:
                if camera_custody is not None:
                    camera_custody.secondary("native-process-stream-close", cleanup)


def _private_options(args, *, editable=False):
    from native_manim_camera_evidence import custody_module, require
    require(sys.platform == "linux" and os.geteuid() != 0,
            "camera_physical_runner_must_be_linux_nonroot")
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
            "camera_requires_one_explicit_original_dispatch")
    require(args.evidence_root is not None and args.sealed_originals is not None,
            "camera_private_custody_paths_required")
    root = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    for path in (args.evidence_root, args.sealed_originals):
        require(path.is_absolute() and path.is_relative_to(root)
                and path.parent.resolve(strict=True) == path.parent
                and not path.exists() and not path.is_symlink(),
                "camera_new_private_output_required")
    observed = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                       text=True).strip()
    require(observed == GITHUB_SHA, "camera_original_checkout_identity")
    if not editable:
        require(MW_CAMERA_SEED.is_file(), "camera_source_seed_binary_absent")
    if getattr(args, "source_cycle_enabled", False):
        require(MW_CAMERA_SOURCE.is_file(), "camera_source_cycle_binary_absent")
    recipient = {
        "spki_der_base64": os.environ.get("PRIVATE_EVIDENCE_RECIPIENT_SPKI", ""),
        "sha256": os.environ.get("PRIVATE_EVIDENCE_RECIPIENT_SHA256", ""),
    }
    current = {
        "source_sha": GITHUB_SHA, "semwright_sha": PIN,
        "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
    }
    engine = custody_module()
    engine.public_key(recipient)
    engine.validate_context(current)
    return recipient, current


def camera_options(args):
    return _private_options(args)


def editable_options(args):
    return _private_options(args, editable=True)


def _run_single(args, *, source_cycle=None, editable_cycle=None):
    camera = args.scenario == "camera120"
    editable = args.scenario == "editable-source-cycle"
    private = camera or editable
    if editable != (editable_cycle is not None):
        raise ValueError("editable phases require the ordered source-cycle coordinator")
    cycle_observation = None
    editable_result = None
    if source_cycle is not None and not camera:
        raise ValueError("camera source cycle requires the camera producer")
    if private:
        from native_manim_camera_evidence import (
            CameraCustody, EVIDENCE_NAME, finish_render, prepare_seed, private_json,
        )
        recipient, current = editable_options(args) if editable else camera_options(args)
        if editable and current != editable_cycle["current"]:
            raise ValueError("editable cycle context changed between native phases")
        if source_cycle is not None and current != source_cycle["current"]:
            raise ValueError("camera cycle context changed between native phases")
    elif args.evidence_root is not None or args.sealed_originals is not None:
        raise ValueError("private camera custody arguments require camera120")
    evidence = args.evidence_root if private else EVIDENCE
    evidence.mkdir(parents=True, exist_ok=False, mode=0o700)
    # Camera originals remain in their private directory until runner disposal,
    # including when sealing fails. They are never automatically removed here.
    shared_owner = (source_cycle["owner"] if source_cycle is not None else
                    editable_cycle["owner"] if editable else None)
    workspace = (
        contextlib.nullcontext(str(shared_owner)) if shared_owner is not None else
        (contextlib.nullcontext(tempfile.mkdtemp(
            prefix="mw-manim-" if editable else "motionwright-native-manim-camera-",
            dir=os.environ["RUNNER_TEMP"]))
         if private else tempfile.TemporaryDirectory(prefix="motionwright-native-manim-"))
    )
    with workspace as raw:
        root = Path(raw).resolve()
        root.chmod(0o700)
        scope = (CameraCustody(
            root, evidence, args.sealed_originals, recipient, current,
            profile="editable" if editable else "camera",
        ) if private else contextlib.nullcontext(None))
        with scope as camera_custody:
            runtime_inventory_sha256 = retain_runtime_inventory(evidence)
            phase_root = root
            if source_cycle is not None:
                phase_root = root / "edited1040"
                phase_root.mkdir(mode=0o700, exist_ok=False)
            if editable:
                phase_root = root / editable_cycle["short_phase"]
                phase_root.mkdir(mode=0o700, exist_ok=False)
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
                if editable and name in ("motionwright-data", "bin"):
                    path = root / name
                    if editable_cycle["history"]:
                        if (not path.is_dir() or path.is_symlink()
                                or path.resolve(strict=True) != path):
                            raise ValueError("editable shared store or pinned binary root changed")
                        if name == "motionwright-data" and editable_cycle["database"].parent != path:
                            raise ValueError("editable phase changed the original persisted store")
                    else:
                        path.mkdir(mode=0o700, exist_ok=False)
                elif source_cycle is not None and name == "motionwright-data":
                    path = source_cycle["database"].parent
                    if path != root / "motionwright-data":
                        raise ValueError("camera cycle must reuse its original persisted store")
                else:
                    path = phase_root / name
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
            if camera:
                if source_cycle is None:
                    seeded = captured_json([str(MW_CAMERA_SEED), str(database)], env=env,
                                           evidence=evidence, label="source-seed")
                    prepared_camera = prepare_seed(seeded, evidence)
                else:
                    from native_manim_camera_cycle import prepare_edited_seed, reject_original_media
                    seeded = prepare_edited_seed(
                        source_cycle, evidence, database, env,
                        source_binary=MW_CAMERA_SOURCE, capture=captured_json,
                    )
                    prepared_camera = prepare_seed(seeded, evidence, fixture="edited1040")
                    reject_original_media(
                        prepared_camera, source_cycle, evidence, str(FFPROBE), str(FFMPEG),
                    )
                # This spelling is the canonical Project.resource_key implementation.
                seeded = {**seeded, "resource": "project:" + seeded["project_id"]}
                private_json(evidence / "native-input-identities.json", {
                    "motionwright_sha": GITHUB_SHA, "semwright_sha": PIN,
                    "runner_uid": os.geteuid(), "manim_version": MANIM_VERSION,
                    "source_lock_sha256": digest(ROOT / "SOURCE_LOCK.json"),
                    "runtime_inventory_sha256": runtime_inventory_sha256,
                    "binaries": {name: digest(path) for name, path in {
                        "source_seed": MW_CAMERA_SEED, "native_example": MW_EXAMPLE,
                        **({"source_cycle_example": MW_CAMERA_SOURCE}
                           if getattr(args, "source_cycle_enabled", False) else {}),
                        "driver": MW_DRIVER, "runner": MW_RUNNER,
                        "broker": SW_BINS / "semwrightd", "client": SW_BINS / "semwright",
                        "sandbox": SW_BINS / "semwright-sandbox",
                        "python": PYTHON, "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
                    }.items()},
                    "compiler_bytes_exported_before_native_dispatch": True,
                    "independent_vendor_binary_authentication": False,
                })
            elif editable:
                from native_manim_editable_cycle import new_phase_inputs
                prepared_editable = new_phase_inputs(
                    editable_cycle, evidence, database, paths["output"], env,
                    capture=captured_json, source_binary=MW_EXAMPLE,
                )
                seeded = prepared_editable["seed"]
                private_json(evidence / "native-input-identities.json", {
                    "motionwright_sha": GITHUB_SHA, "semwright_sha": PIN,
                    "runner_uid": os.geteuid(), "manim_version": MANIM_VERSION,
                    "source_lock_sha256": digest(ROOT / "SOURCE_LOCK.json"),
                    "runtime_inventory_sha256": runtime_inventory_sha256,
                    "binaries": {name: digest(path) for name, path in {
                        "native_example": MW_EXAMPLE, "driver": MW_DRIVER, "runner": MW_RUNNER,
                        "broker": SW_BINS / "semwrightd", "client": SW_BINS / "semwright",
                        "sandbox": SW_BINS / "semwright-sandbox", "python": PYTHON,
                        "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
                    }.items()},
                    "independent_vendor_binary_authentication": False,
                })
            else:
                seeded = run_json([str(MW_EXAMPLE), "seed", str(database)], env=env)
                if seeded.get("primitive_count") != 3:
                    raise AssertionError(f"unexpected Manim seed: {seeded}")

            driver = paths["bin"] / "motionwright-manim-community-driver"
            runner = paths["bin"] / "motionwright-manim-runner"
            if editable and editable_cycle["history"]:
                if digest(driver) != digest(MW_DRIVER) or digest(runner) != digest(MW_RUNNER):
                    raise ValueError("editable original admitted binaries changed")
            else:
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
            if sys.platform == "linux" and len(os.fsencode(socket)) > 107:
                raise ValueError("camera owner socket exceeds Linux address capacity")
            session = paths["runtime"] / "motionwright.session"
            daemon_log = evidence / "semwrightd.log"
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
                if camera:
                    required.add("driver.manim-community.camera.render.start")
                else:
                    required.add("driver.manim-community.editable.render.start")
                if private:
                    private_json(evidence / "provider-capabilities.json", capabilities)
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

                launch = {
                    "broker_pid": daemon.pid, "config_sha256": digest(config_path),
                    "manifest_sha256": digest(manifest_path),
                    "session_sha256": digest(session), "session_bytes": session.stat().st_size,
                    "driver_sha256": digest(driver), "runner_sha256": digest(runner),
                    "broker_sha256": digest(SW_BINS / "semwrightd"),
                    "runtime_inventory_sha256": runtime_inventory_sha256,
                    "fresh_process_launched_by_this_wrapper": True,
                }
                if private:
                    private_json(evidence / "original-host-launch.json", launch)
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

                if camera:
                    request_id = f"camera-{current['run_id']}-{current['run_attempt']}-{GITHUB_SHA[:12]}"
                    if source_cycle is not None:
                        request_id += "-edited1040"
                    native = camera_custody.once(lambda: captured_json(
                        [str(MW_EXAMPLE), "realize-camera", str(database), str(connection_path),
                         str(evidence / EVIDENCE_NAME), seeded["project_id"], seeded["scene_id"],
                         seeded["deliverable_id"], request_id],
                        env=env, evidence=evidence, label="native-example", timeout=420,
                        camera_custody=camera_custody,
                    ))
                    require_original_native_video(
                        native, capabilities, paths["output"], paths["work"], evidence,
                    )
                    finish_render(
                        native, prepared_camera, evidence, paths["output"], paths["work"],
                        GITHUB_SHA, PIN, str(FFPROBE), str(FFMPEG),
                        fixture="original" if source_cycle is None else "edited1040",
                    )
                    if source_cycle is not None:
                        from native_manim_camera_cycle import verify_edited_source_readback
                        cycle_observation = verify_edited_source_readback(
                            source_cycle, prepared_camera, native, evidence, database,
                            paths["output"], env, source_binary=MW_CAMERA_SOURCE,
                            capture=captured_json,
                        )
                elif editable:
                    from native_manim_editable_cycle import (
                        EVIDENCE_NAME as EDITABLE_EVIDENCE_NAME, finish_phase,
                    )
                    request_id = (f"editable-{current['run_id']}-{current['run_attempt']}-"
                                  f"{GITHUB_SHA[:12]}-{editable_cycle['short_phase']}")
                    command = [str(MW_EXAMPLE),
                               "realize-bundle" if prepared_editable["is_reopen"] else "realize",
                               str(database), str(connection_path),
                               str(evidence / EDITABLE_EVIDENCE_NAME)]
                    if prepared_editable["is_reopen"]:
                        command.append(str(prepared_editable["input_path"]))
                    command.append(request_id)
                    native = camera_custody.once(lambda: captured_json(
                        command, env=env, evidence=evidence, label="native-example",
                        timeout=420, camera_custody=camera_custody,
                    ))
                    require_original_native_video(
                        native, capabilities, paths["output"], paths["work"], evidence,
                    )
                    editable_result = finish_phase(
                        editable_cycle, prepared_editable, native, evidence,
                        paths["output"], paths["work"], database, env,
                        capture=captured_json, source_binary=MW_EXAMPLE, python=PYTHON,
                        runtime=MANIM_RUNTIME, tooling=ROOT / "tooling", launch=launch,
                    )
                else:
                    result = run_json(
                        [
                            str(MW_EXAMPLE),
                            "realize",
                            str(database),
                            str(connection_path),
                            str(evidence / "motionwright-manim-evidence.json"),
                        ],
                        env=env,
                        timeout=420,
                    )
                    if result.get("native_manim_e2e") != "PASS":
                        raise AssertionError(f"Motionwright Manim realization did not pass: {result}")

                    original_evidence = json.loads(
                        (evidence / "motionwright-manim-evidence.json").read_text(encoding="utf-8")
                    )
                    artifact = original_evidence["result"]["artifact"]
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

                    review_mp4 = evidence / "motionwright-manim-community.mp4"
                    shutil.copyfile(artifact_path, review_mp4)
                    require_original_native_video(
                        original_evidence, capabilities, paths["output"], paths["work"], evidence,
                    )
                    write_private_json(
                        evidence / "result.json",
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
                try:
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

                except BaseException as cleanup_error:
                    if camera_custody is None:
                        raise
                    camera_custody.secondary("broker-cleanup", cleanup_error)

    if editable:
        return {
            "owner": root, "database": database, "evidence": evidence,
            "sealed_originals": args.sealed_originals, "current": current,
            "prepared": prepared_editable, "seed": seeded, "native": native,
            "native_path": evidence / EDITABLE_EVIDENCE_NAME, "output_root": paths["output"],
            "launch": launch, **editable_result,
        }
    if camera:
        return {
            "owner": root, "database": database, "evidence": evidence,
            "sealed_originals": args.sealed_originals, "current": current,
            "prepared": prepared_camera, "native": native,
            "native_path": evidence / EVIDENCE_NAME, "output_root": paths["output"],
            "cycle_observation": cycle_observation,
        }
    return None


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", choices=("static-scene", "camera120", "camera120-source-cycle", "editable-source-cycle"),
        default="static-scene",
    )
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--sealed-originals", type=Path)
    parser.add_argument("--edited-evidence-root", type=Path)
    parser.add_argument("--sealed-edited", type=Path)
    parser.add_argument("--sealed-cycle-root", type=Path)
    args = parser.parse_args(argv)
    exact_inputs()
    camera = args.scenario != "static-scene"
    if args.scenario == "editable-source-cycle":
        from native_manim_editable_cycle import run_source_cycle
        run_source_cycle(args, run_phase=_run_single, validate_phase=editable_options)
        status_key = "native_manim_editable_source_cycle_e2e"
    elif args.scenario == "camera120-source-cycle":
        if args.sealed_cycle_root is not None:
            raise ValueError("sealed cycle root requires editable-source-cycle")
        from native_manim_camera_cycle import run_source_cycle
        from native_manim_camera_evidence import verified_publication
        run_source_cycle(
            args, run_phase=_run_single, validate_phase=camera_options,
            publish_phase=verified_publication,
        )
        status_key = "native_manim_camera_source_cycle_e2e"
    else:
        if (args.edited_evidence_root is not None or args.sealed_edited is not None
                or args.sealed_cycle_root is not None):
            raise ValueError("cycle custody paths require their selected source cycle")
        _run_single(args)
        status_key = "native_manim_camera_e2e" if camera else "native_manim_e2e"
    print(json.dumps({
        status_key: "PASS", "motionwright_sha": GITHUB_SHA, "semwright_sha": PIN,
        "evidence": "private sealed originals" if camera else str(EVIDENCE),
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        if not any(value in sys.argv for value in ("camera120", "camera120-source-cycle", "editable-source-cycle")):
            raise
        # Original stdout/stderr, Rust diagnostics, journal and wrapper error
        # belong in the private snapshot, never in public Actions logs.
        try:
            sys.stderr.write("Manim native verification did not complete; inspect sealed originals. No replay.\n")
        except BaseException:
            pass
        raise SystemExit(2) from None
