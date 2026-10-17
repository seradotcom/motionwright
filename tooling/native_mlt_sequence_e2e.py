#!/usr/bin/env python3
"""Exact-SHA two-segment Motion Canvas / MLT native CI evidence.

The closed modes preserve their own frame, source and output contracts.
The canonical OriginalMix master uses its retained Rust WAV and the existing
independent AAC reader; source custody and listening approval remain separate.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from native_av_failure_guard import run_sanitized_json
from native_mlt_caption_pixels import prove_native_captioned_master
from native_mlt_public_artifact import (
    PublicationError, SOURCE_WAV_NAME, mode_contract, read_synthetic_source_wav,
    verify_source_unchanged, verify_staged_source_receipt, expected_source_proof,
    read_stable_native_file, strict_object, file_sha, exact_values, same_json_value,
    canonical_fixture_ids, original_mix_master_name, original_mix_readback_projection,
    original_mix_provenance_projection, ORIGINAL_MIX_SCOPE, ORIGINAL_MIX_RECEIPT,
    ORIGINAL_MIX_READBACK, ORIGINAL_MIX_PROVENANCE, ORIGINAL_MIX_READER_SHA256,
    ORIGINAL_MIX_SOURCE_PATHS, ORIGINAL_MIX_TOOL_NAMES, ORIGINAL_MIX_VERSION_NAMES,
    ORIGINAL_MIX_AAC_TEST_IDS, ORIGINAL_MIX_READER_TEST_SHA256,
    READBACK_DOCUMENT_LIMIT, LIMIT,
)
from native_mlt_mix_readback import (
    ContentLimits, MediaClock, ReadbackError, bounded_decoder, inspect_master,
)

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
if MODE not in {"pcm", "video-only", "final-mp4", "captioned-mp4", "original-mix-mp4"}:
    raise SystemExit("Only closed native MLT smoke modes are accepted")
CONTRACT = mode_contract(MODE)
VIDEO_ONLY = MODE in {"video-only", "final-mp4", "captioned-mp4", "original-mix-mp4"}
FINAL_MASTER = MODE in {"final-mp4", "captioned-mp4"}
CAPTIONED_MASTER = MODE == "captioned-mp4"
ORIGINAL_MIX_MASTER = MODE == "original-mix-mp4"
PROFILE = "lossless-video-only" if VIDEO_ONLY else "lossless"
EXPECTED_SCOPE = (
    "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master"
    if VIDEO_ONLY else "actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master"
)
VIDEO_FILE = "native-two-segment-video-only.mkv" if VIDEO_ONLY else "native-two-segment-lossless.mkv"


def inspect_decoded_ci_stereo(pcm: bytes, *, mode: str = "final-mp4") -> dict:
    """Independent decoded-content evidence for the actual 33-frame CI
    source. The 48 kHz stereo WAV authored by the Rust E2E contains distinct
    400 Hz left and 600 Hz right sawtooth fundamentals. AAC stream headers,
    MP4 byte hashes or a nonempty decoded WAV alone do NOT prove the actual
    speaker content was retained and not swapped, duplicated or muted.
    """
    if mode not in {"final-mp4", "captioned-mp4"}:
        raise AssertionError("Legacy stereo oracle admits only its measured-voice fixture")
    contract = mode_contract(mode)
    if len(pcm) % 4:
        raise AssertionError("Decoded H.264/AAC master has partial stereo PCM sample")
    frames = len(pcm) // 4
    if not contract.pcm_frames <= frames <= contract.pcm_frames + 1024:
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


def inspect_native_master_decoded_content(master: Path, ffmpeg: Path, env: dict,
                                          *, mode: str = "final-mp4") -> dict:
    """Decode the real signed-off owner MP4 using fixed CI ffmpeg arguments;
    no application runtime, local backend or user-controlled filters.
    Temp disk output ensures even a hostile decoder cannot grow Python RAM.
    """
    if mode not in {"final-mp4", "captioned-mp4"}:
        raise AssertionError("Legacy stereo decoder admits only its measured-voice fixture")
    contract = mode_contract(mode)
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
        if not (contract.pcm_frames * 4 <= size <= (contract.pcm_frames + 1024) * 4):
            raise AssertionError("Decoded source speaker PCM exceeded the AV clock budget")
        output.seek(0)
        pcm = output.read((contract.pcm_frames + 1024) * 4 + 1)
        if len(pcm) != size:
            raise AssertionError("Decoded AV speaker bytes changed during bounded read")
        return inspect_decoded_ci_stereo(pcm, mode=mode)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def retain_admitted_source_file(output_root: Path, evidence_root: Path,
                                relative: str, mode: str) -> dict:
    if (not output_root.is_dir() or output_root.is_symlink()
        or not evidence_root.is_dir() or evidence_root.is_symlink()):
        raise PublicationError("native_mlt_source_wav_root_invalid")
    native_root, retained_root = output_root.resolve(), evidence_root.resolve()
    if (native_root == retained_root or native_root in retained_root.parents
        or retained_root in native_root.parents):
        raise PublicationError("native_mlt_source_wav_roots_overlap")
    # Callers admit a single closed basename before this function opens it.
    if (not isinstance(relative, str) or Path(relative).name != relative
        or "/" in relative or "\\" in relative or ":" in relative):
        raise PublicationError("native_mlt_source_wav_path_invalid")
    source = native_root / relative
    data, proof, token = read_synthetic_source_wav(source, mode)
    destination = retained_root / SOURCE_WAV_NAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(destination, flags, 0o600), "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    copied, copied_proof, _ = read_synthetic_source_wav(destination, mode)
    if copied != data or copied_proof != proof:
        raise PublicationError("native_mlt_source_wav_copy_mismatch")
    verify_source_unchanged(source, token)
    return proof


def retain_native_master_source(output_root: Path, evidence_root: Path,
                                receipt: dict, mode: str,
                                project: dict | None = None) -> dict:
    """Retain the admitted staged bytes; never regenerate or replace a source."""
    try:
        relative = verify_staged_source_receipt(receipt, mode, project)
        return retain_admitted_source_file(output_root, evidence_root, relative, mode)
    except (PublicationError, OSError):
        raise AssertionError("native_master_source_retention_refused") from None


def write_private_new_json(path: Path, value: object) -> str:
    data = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if not 0 < len(data) <= READBACK_DOCUMENT_LIMIT:
        raise AssertionError("native_original_mix_document_budget")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    observed, _ = read_stable_native_file(path, READBACK_DOCUMENT_LIMIT)
    if observed != data:
        raise AssertionError("native_original_mix_document_copy_mismatch")
    return file_sha(data)


def retain_partial_original_mix_source(output_root: Path, evidence_root: Path,
                                       project: dict) -> None:
    """Best-effort private custody only; never replace the primary native failure."""
    outcome = {"scope": "SOURCE_ONLY", "master_readback": "NOT_RUN",
               "source_status": "NOT_OBSERVED"}
    try:
        retained = evidence_root / SOURCE_WAV_NAME
        if retained.exists() or retained.is_symlink():
            _, proof, _ = read_synthetic_source_wav(retained, "original-mix-mp4")
            outcome.update(source_status="ALREADY_RETAINED", retained_source_audio=proof)
        else:
            project_id, generation, revision, profile = canonical_fixture_ids(project)
            source_proof = expected_source_proof("original-mix-mp4")
            pattern = re.compile(
                rf"mw-original-mix-{project_id}-{generation}-r{revision}-p{profile}-"
                rf"[0-9a-f]{{16}}-{source_proof['sha256'][:16]}\.wav\Z"
            )
            if not output_root.is_dir() or output_root.is_symlink():
                raise PublicationError("native_mlt_source_wav_root_invalid")
            names = []
            with os.scandir(output_root) as entries:
                for count, item in enumerate(entries, 1):
                    if count > 4096:
                        raise PublicationError("native_original_mix_custody_scan_budget")
                    if pattern.fullmatch(item.name):
                        names.append(item.name)
            if len(names) > 1:
                raise PublicationError("native_original_mix_custody_ambiguous")
            if names:
                proof = retain_admitted_source_file(
                    output_root, evidence_root, names[0], "original-mix-mp4"
                )
                outcome.update(source_status="RETAINED", retained_source_audio=proof)
    except (PublicationError, OSError, ValueError, TypeError, AssertionError):
        outcome["source_status"] = "REFUSED"
    try:
        write_private_new_json(evidence_root / "native-original-mix-partial-custody.json", outcome)
    except (PublicationError, OSError, ValueError, TypeError, AssertionError):
        pass


def retain_original_mix_master(output_root: Path, evidence_root: Path,
                               project: dict, native_receipt: dict, result: dict) -> Path:
    name = original_mix_master_name(project)
    artifact = native_receipt.get("master", {}).get("artifact")
    exact_values(artifact, {"root": "output", "path": name}, "native_original_mix_master_path")
    exact_values(result, {"master_artifact_path": name}, "native_original_mix_master_path")
    if (not output_root.is_dir() or output_root.is_symlink()
        or not evidence_root.is_dir() or evidence_root.is_symlink()
        or output_root.resolve() == evidence_root.resolve()
        or output_root.resolve() in evidence_root.resolve().parents
        or evidence_root.resolve() in output_root.resolve().parents):
        raise AssertionError("native_original_mix_master_roots")
    source = output_root / name
    data, token = read_stable_native_file(source, LIMIT)
    exact_values(artifact, {"sha256": file_sha(data), "bytes": len(data)},
                 "native_original_mix_master_identity")
    exact_values(result, {"master_artifact_sha256": file_sha(data),
                          "master_artifact_bytes": len(data)},
                 "native_original_mix_master_identity")
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise AssertionError("native_original_mix_master_container")
    destination = evidence_root / "native-multisegment-master.mp4"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(destination, flags, 0o600), "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    copied, _ = read_stable_native_file(destination, LIMIT)
    if copied != data:
        raise AssertionError("native_original_mix_master_copy_mismatch")
    verify_source_unchanged(source, token)
    return destination


def inspect_retained_original_mix(evidence_root: Path, master_sha256: str,
                                  ffmpeg: Path, ffprobe: Path) -> tuple[dict, str]:
    source = evidence_root / SOURCE_WAV_NAME
    master = evidence_root / "native-multisegment-master.mp4"
    _, custody, source_token = read_synthetic_source_wav(source, "original-mix-mp4")
    master_bytes, master_token = read_stable_native_file(master, LIMIT)
    if file_sha(master_bytes) != master_sha256:
        raise AssertionError("native_original_mix_retained_master_changed")
    try:
        # Exactly one decoder/comparison implementation, on the retained original
        # WAV and retained MP4. No repaired, resampled or AAC-derived reference.
        proof = inspect_master(
            source, master, reference_sha256=custody["sha256"],
            master_sha256=master_sha256, clock=MediaClock(60, 1280, 720, 30, 1),
            ffmpeg=str(ffmpeg), ffprobe=str(ffprobe), limits=ContentLimits(),
        )
        verify_source_unchanged(source, source_token)
        verify_source_unchanged(master, master_token)
        original_mix_readback_projection(proof, custody["sha256"], master_sha256)
    except ReadbackError as exc:
        reason = str(exc)
        if re.fullmatch(r"[a-z0-9_]{1,128}", reason) is None:
            reason = "reader_refused"
        try:
            write_private_new_json(evidence_root / "native-original-mix-readback-failure.json", {
                "technical_content_readback": "FAIL", "reason": reason,
                "reference_wav_sha256": custody["sha256"], "final_mp4_sha256": master_sha256,
            })
        except (PublicationError, OSError, ValueError, TypeError, AssertionError):
            pass
        raise AssertionError("native_original_mix_readback_failed") from None
    proof_sha = write_private_new_json(evidence_root / ORIGINAL_MIX_READBACK, proof)
    return proof, proof_sha


def original_mix_input_snapshot() -> dict:
    """Observe the exact checkout, built inputs and decoder versions on this runner."""
    expected_source_proof("original-mix-mp4")
    try:
        identities = {}
        for label, repository, expected in (("motionwright", ROOT, GITHUB_SHA),
                                            ("semwright", SEMWRIGHT, PIN)):
            def git(*arguments):
                return subprocess.check_output(
                    ["git", "-C", str(repository), *arguments], text=True,
                    stderr=subprocess.DEVNULL, timeout=30,
                ).strip()
            if git("rev-parse", "HEAD") != expected:
                raise AssertionError("native_original_mix_checkout_mismatch")
            subprocess.run(["git", "-C", str(repository), "diff", "--quiet", "HEAD", "--"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=True)
            identities[label + "_sha"] = expected
            identities[label + "_tree"] = git("rev-parse", "HEAD^{tree}")
        sources = {}
        for relative in ORIGINAL_MIX_SOURCE_PATHS:
            data, _ = read_stable_native_file(ROOT / relative, READBACK_DOCUMENT_LIMIT)
            sources[relative] = {"bytes": len(data), "sha256": file_sha(data)}
        if sources["tooling/native_mlt_mix_readback.py"]["sha256"] != ORIGINAL_MIX_READER_SHA256:
            raise AssertionError("native_original_mix_reader_source_mismatch")
        paths = {
            "motionwright_example": MW_BIN,
            "semwright_cli": SW_BINS / "semwright",
            "semwright_daemon": SW_BINS / "semwrightd",
            "semwright_sandbox": SW_BINS / "semwright-sandbox",
            "motion_canvas_driver": SW_BINS / "semwright-motion-canvas-driver",
            "mlt_driver": SW_BINS / "semwright-mlt-video-driver",
            "mlt_runner": SW_BINS / "semwright-mlt-runtime-runner",
            **{name: Path(shutil.which(name) or "").resolve()
               for name in ("melt", "ffmpeg", "ffprobe", "bwrap", "node")},
        }
        if set(paths) != set(ORIGINAL_MIX_TOOL_NAMES):
            raise AssertionError("native_original_mix_tool_inventory")
        tools = {}
        for name, path in paths.items():
            path = path.resolve()
            data, _ = read_stable_native_file(path, 512 * 1024 * 1024)
            tools[name] = {"path": str(path), "bytes": len(data), "sha256": file_sha(data)}
        versions = {}
        for name in ORIGINAL_MIX_VERSION_NAMES:
            option = "-version" if name in ("ffmpeg", "ffprobe") else "--version"
            versions[name] = bounded_decoder([tools[name]["path"], option], 65536, timeout=10).decode("utf-8").strip()
        runtime_versions, _ = read_stable_native_file(
            ROOT / "verification/native-mlt-sequence-e2e/native-runtime-versions.txt", 65536
        )
        gate_blob, _ = read_stable_native_file(
            ROOT / "verification/native-mlt-sequence-e2e/native-original-mix-aac-test-gate.json",
            READBACK_DOCUMENT_LIMIT,
        )
        gate = strict_object(gate_blob)
        exact_values(gate, {
            "schema": "motionwright.native-original-mix-real-aac-tests/1",
            "motionwright_sha": GITHUB_SHA, "reader_sha256": ORIGINAL_MIX_READER_SHA256,
            "reader_tests_sha256": ORIGINAL_MIX_READER_TEST_SHA256,
            "test_ids": sorted(ORIGINAL_MIX_AAC_TEST_IDS),
            "tests_run": 4, "skipped": 0, "failures": 0, "errors": 0,
        }, "native_original_mix_real_aac_gate_missing", closed=True)
        return {**identities, "semwright_declared_version": semwright_version(),
                "tracked_worktrees_clean": True, "source_files": sources, "tools": tools,
                "tool_versions": versions, "native_runtime_versions_sha256": file_sha(runtime_versions),
                "real_aac_test_gate": gate}
    except (OSError, ValueError, subprocess.SubprocessError):
        raise AssertionError("native_original_mix_inputs_unverified") from None


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
    source_before = original_mix_input_snapshot() if ORIGINAL_MIX_MASTER else None
    if source_before is not None:
        write_private_new_json(EVIDENCE / "native-original-mix-inputs-before.json", source_before)

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
        if ((seeded["width"], seeded["height"]) != (CONTRACT.width, CONTRACT.height)
            or (seeded["fps_num"], seeded["fps_den"]) != (CONTRACT.fps_num, CONTRACT.fps_den)
            or seeded["scene_count"] != CONTRACT.scene_count
            or seeded["deliverable_count"] != CONTRACT.deliverable_count):
            raise AssertionError(f"unexpected real default profiles or source scenes: {seeded}")
        if ORIGINAL_MIX_MASTER:
            exact_values(seeded, {
                "fixture_mode": "original-mix-mp4", "fixture_frame_count": 60,
                "fixture_audio_sample_frames": 96000,
                "fixture_speech_or_human_approval": False,
            }, "native_original_mix_seed_mismatch")
            canonical_fixture_ids(seeded)

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

            # Inspect the actual source journal produced by Semwright-backed
            # native MLT project.create. This is not a mock allocation count
            # and never claims the provider project was actually closed.
            inventory_path = EVIDENCE / "native-mlt-lifetime-inventory.json"
            if (not inventory_path.is_file() or inventory_path.is_symlink()
                or not 1 <= inventory_path.stat().st_size <= 16_384):
                raise AssertionError("Native MLT lifetime source evidence was missing or oversized")
            inventory_bytes = inventory_path.read_bytes()
            if (result.get("native_mlt_lifetime_inventory_sha256")
                != hashlib.sha256(inventory_bytes).hexdigest()):
                raise AssertionError("Native MLT lifetime receipt bytes differ from Rust source")
            inventory = json.loads(inventory_bytes)
            expected_keys = {
                "schema", "project_id", "generation", "revision",
                "source_receipts_scanned", "confirmed_native_creations_current_revision",
                "confirmed_native_creations_historical",
                "unconfirmed_or_invalid_creation_requests",
                "observed_close_request_identities", "disposition",
                "complete_source_receipt_snapshot",
                "native_provider_liveness_verified",
                "foreground_cleanup_consent_obtained",
                "verified_native_project_cleanup",
                "automatic_destructive_cleanup_allowed",
            }
            if not isinstance(inventory, dict) or set(inventory) != expected_keys:
                raise AssertionError("Native MLT lifetime receipt schema exposes untrusted fields")
            current = inventory.get("confirmed_native_creations_current_revision")
            receipt_total = inventory.get("source_receipts_scanned")
            if (
                inventory["schema"] != "motionwright.v01-mlt-cleanup-inventory/1"
                or inventory["project_id"] != seeded["project_id"]
                or inventory["generation"] != seeded["generation"]
                or inventory["revision"] != seeded["revision"]
                or type(current) is not int or not 1 <= current <= 4
                or type(receipt_total) is not int or receipt_total < current
                or result.get("native_mlt_lifetime_confirmed_current_creations") != current
                or result.get("native_mlt_lifetime_receipts_scanned") != receipt_total
                or result.get("native_mlt_lifetime_close_requests_observed") != 0
                or result.get("native_mlt_cleanup_verified") is not False
                or inventory["unconfirmed_or_invalid_creation_requests"] != 0
                or inventory["observed_close_request_identities"] != 0
                or inventory["disposition"] != "project_lifetime_unverified_foreground_consent_required"
                or inventory["complete_source_receipt_snapshot"] is not True
                or any(inventory[key] is not False for key in (
                    "native_provider_liveness_verified",
                    "foreground_cleanup_consent_obtained",
                    "verified_native_project_cleanup",
                    "automatic_destructive_cleanup_allowed",
                ))
            ):
                raise AssertionError("Real MLT project creation provenance/lifetime is unconfirmed")
            if (result.get("frame_count") != CONTRACT.video_frames
                or result.get("segment_count") != len(CONTRACT.source_segment_frames)):
                raise AssertionError("Native timeline lost its exact source partition")
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
            if VIDEO_ONLY and evidence.get("native_frame_count_observed") != CONTRACT.video_frames:
                raise AssertionError("New native video-only profile lacks actual decoded frame count")
            if evidence.get("source", {}).get("total_frames") != CONTRACT.video_frames:
                raise AssertionError("Real FFV1 intermediate frame total was altered")
            records = evidence.get("source", {}).get("verified_video_segments", [])
            if (len(records) != len(CONTRACT.source_segment_frames)
                or [item["frame_count"] for item in records] != list(CONTRACT.source_segment_frames)):
                raise AssertionError("Real native source FFV1s are not the exact two source segments")
            if [item["output_start_frame"] for item in records] != [
                sum(CONTRACT.source_segment_frames[:index])
                for index in range(len(CONTRACT.source_segment_frames))
            ]:
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
            if evidence.get("native_frame_count_observed") not in (None, CONTRACT.video_frames):
                raise AssertionError("MLT native frame count observation contradicted source")
            video = videos[0]
            if (video.get("codec_name") != "ffv1"
                or (video.get("width"), video.get("height")) != (CONTRACT.width, CONTRACT.height)
                or video.get("r_frame_rate") != f"{CONTRACT.fps_num}/{CONTRACT.fps_den}"
                or video.get("nb_read_frames") != str(CONTRACT.video_frames)):
                raise AssertionError("Native actual MLT video codec/frame provenance failed: " + repr(video))

            shutil.copyfile(video_path, EVIDENCE / VIDEO_FILE)
            master_check = {}
            if FINAL_MASTER:
                av_receipt = json.loads(
                    (EVIDENCE / "native-multisegment-av-master-evidence.json").read_text()
                )
                master_scope = (
                    "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved"
                    if CAPTIONED_MASTER else
                    "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved"
                )
                if (av_receipt["frame_count"] != CONTRACT.video_frames
                    or av_receipt["measured_voice"]["audio_pcm_frames"] != CONTRACT.pcm_frames
                    or av_receipt["measured_voice"].get("caption_cue_count")
                       != (2 if CAPTIONED_MASTER else None)
                    or result.get("native_captioned_master_requested") is not CAPTIONED_MASTER
                    or result.get("source_bound_caption_cue_count")
                       != (2 if CAPTIONED_MASTER else None)
                    or av_receipt["evidence_scope"] != master_scope):
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
                    or v[0].get("nb_read_frames") != str(CONTRACT.video_frames)
                    or (v[0].get("width"),v[0].get("height")) != (CONTRACT.width, CONTRACT.height)
                    or v[0].get("r_frame_rate") != f"{CONTRACT.fps_num}/{CONTRACT.fps_den}"
                    or a[0].get("codec_name") != "aac"
                    or a[0].get("sample_rate") != str(CONTRACT.sample_rate)
                    or a[0].get("channels") != CONTRACT.channels):
                    raise AssertionError("Independent MP4 decoder did not verify exact AV output")
                decoded = av_receipt["decoded_audio"]
                wav_path = paths["output"] / decoded["path"]
                if not wav_path.is_file() or digest(wav_path) != decoded["sha256"]:
                    raise AssertionError("MLT native decoded PCM audit is missing or changed")
                # The actual MP4, not the receipt or source WAV, must retain
                # the authored, distinct 400/600 Hz left/right test signals.
                decoded_content = inspect_native_master_decoded_content(master, ffmpeg, env, mode=MODE)
                write_private_json(EVIDENCE / "decoded-stereo-content-proof.json", decoded_content)
                retained_source = retain_native_master_source(
                    paths["output"], EVIDENCE, av_receipt, MODE,
                )
                shutil.copyfile(master, EVIDENCE / "native-multisegment-master.mp4")
                master_check = {
                    "retained_source_audio": retained_source,
                    "final_master_frames":CONTRACT.video_frames,
                    "final_master_video_codec":"h264",
                    "final_master_audio_codec":"aac",
                    "final_master_audio_rate":CONTRACT.sample_rate,
                    "final_master_audio_channels":CONTRACT.channels,
                    "final_master_sha256":digest(master),
                    "final_master_bytes":master.stat().st_size,
                    "measured_voice_pcm_frames":CONTRACT.pcm_frames,
                    "decoded_audio_sha256":decoded["sha256"],
                    "final_evidence_scope":av_receipt["evidence_scope"],
                    **decoded_content,
                }
                if CAPTIONED_MASTER:
                    # Independent FFmpeg readback of native FFV1 and actual
                    # lossy H264. This comparison neither adds a glyph nor
                    # synthesizes a new render or MLT composition.
                    proof = prove_native_captioned_master(
                        video_path, master, ffmpeg,
                        source_sha=GITHUB_SHA,
                        semwright_sha=PIN,
                        selected_cues=av_receipt["measured_voice"]["caption_cue_count"],
                    )
                    if (proof["original_ffv1_sha256"] != digest(video_path)
                        or proof["actual_master_mp4_sha256"] != digest(master)
                        or proof["captioned_master_decoded_pixels"] != "PASS"
                        or proof["native_cue_count"] != 2
                        or proof["commercial_release_authorized"] is not False):
                        raise AssertionError(
                            "Original native caption glyphs did not survive the actual lossy MLT master"
                        )
                    write_private_json(
                        EVIDENCE / "captioned-master-pixel-evidence.json", proof,
                    )
                    master_check.update({
                        "captioned_master_decoded_pixels": "PASS",
                        "captioned_source_cue_count": 2,
                        "native_caption_mask_pixels": {
                            key: value["native_caption_mask_pixels"]
                            for key, value in proof["active_caption_frames"].items()
                        },
                        "native_captioned_human_legibility": "NOT_RUN",
                    })
            if ORIGINAL_MIX_MASTER:
                native_blob, native_token = read_stable_native_file(
                    EVIDENCE / ORIGINAL_MIX_RECEIPT, READBACK_DOCUMENT_LIMIT
                )
                av_receipt = strict_object(native_blob)
                verify_staged_source_receipt(av_receipt, MODE, seeded)
                source_mix = av_receipt["original_mix"]["original_mix"]
                exact_values(result, {
                    "fixture_mode": MODE, "canonical_original_mix_requested": True,
                    "master_evidence_scope": ORIGINAL_MIX_SCOPE,
                    "master_audio_sha256": av_receipt["staged_audio"]["sha256"],
                    "canonical_mix_source_plan_sha256": source_mix["source_plan_sha256"],
                    "canonical_mix_decoded_source_pcm_sha256": source_mix["rendered_pcm_sha256"],
                    "canonical_dependency": av_receipt["canonical_dependency"],
                    "native_captioned_master_requested": False,
                    "source_bound_caption_cue_count": None,
                    "fixture_speech_or_human_approval": False,
                }, "native_original_mix_result_mismatch")
                exact_values(av_receipt["master"], {"video_sha256": digest(video_path)},
                             "native_original_mix_visual_binding")
                # Retain the original reference and MP4 before any independent
                # decoder can fail or the native TemporaryDirectory can close.
                retained_source = retain_native_master_source(
                    paths["output"], EVIDENCE, av_receipt, MODE, seeded
                )
                retained_master = retain_original_mix_master(
                    paths["output"], EVIDENCE, seeded, av_receipt, result
                )
                master_sha = result["master_artifact_sha256"]
                readback, readback_sha = inspect_retained_original_mix(
                    EVIDENCE, master_sha, ffmpeg, ffprobe
                )
                verify_source_unchanged(EVIDENCE / ORIGINAL_MIX_RECEIPT, native_token)
                source_after = original_mix_input_snapshot()
                if not same_json_value(source_before, source_after):
                    raise AssertionError("native_original_mix_inputs_changed")
                provenance = {
                    "schema": "motionwright.native-original-mix-provenance/1",
                    "run_id": os.environ.get("GITHUB_RUN_ID", ""),
                    "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                    "source_before": source_before, "source_after": source_after,
                    "same_inputs": True,
                }
                original_mix_provenance_projection(provenance, GITHUB_SHA, PIN)
                provenance_sha = write_private_new_json(EVIDENCE / ORIGINAL_MIX_PROVENANCE, provenance)
                master_check = {
                    "retained_source_audio": retained_source,
                    "final_master_frames": readback["clock"]["video_frames"],
                    "final_master_video_codec": "h264", "final_master_audio_codec": "aac",
                    "final_master_audio_rate": CONTRACT.sample_rate,
                    "final_master_audio_channels": CONTRACT.channels,
                    "final_master_sha256": master_sha, "final_master_bytes": retained_master.stat().st_size,
                    "original_mix_pcm_frames": readback["content"]["reference_sample_frames"],
                    "decoded_master_pcm_frames": readback["content"]["decoded_sample_frames"],
                    "final_evidence_scope": av_receipt["evidence_scope"],
                    "canonical_original_mix_requested": True,
                    "canonical_mix_source_plan_sha256": source_mix["source_plan_sha256"],
                    "canonical_mix_decoded_source_pcm_sha256": source_mix["rendered_pcm_sha256"],
                    "canonical_dependency": av_receipt["canonical_dependency"],
                    "original_mix_native_receipt_sha256": file_sha(native_blob),
                    "original_mix_readback_sha256": readback_sha,
                    "original_mix_provenance_sha256": provenance_sha,
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
                    "ffmpeg_sha256": digest(ffmpeg),
                    "rendered_source_frames": list(CONTRACT.source_segment_frames),
                    "assembled_video_frames": CONTRACT.video_frames,
                    "mode": MODE,
                    "evidence_scope": EXPECTED_SCOPE,
                    "native_render_profile": PROFILE,
                    "provider_project_cleanup": evidence["provider_project_cleanup"],
                    "native_mlt_lifetime_inventory_sha256": digest(inventory_path),
                    "native_mlt_lifetime_confirmed_current_creations": current,
                    "native_mlt_lifetime_receipts_scanned": receipt_total,
                    "native_mlt_lifetime_close_requests_observed": 0,
                    "native_mlt_cleanup_verified": False,
                    "assembled_video_codec": "ffv1",
                    "assembled_video_has_audio": not VIDEO_ONLY,
                    "transport_audio_codec": None if VIDEO_ONLY else "pcm_s16le",
                    "video_bytes": video_path.stat().st_size,
                    "video_sha256": digest(video_path),
                    **master_check,
                },
            )
        finally:
            if ORIGINAL_MIX_MASTER:
                retain_partial_original_mix_source(paths["output"], EVIDENCE, seeded)
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
