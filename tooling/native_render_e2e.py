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
import re
import sys
from contextlib import contextmanager
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
NATIVE_SCOPE = os.environ.get("MOTIONWRIGHT_NATIVE_SCOPE", "standard")
MW_BIN = ROOT / "target" / "debug" / "examples" / (
    "native-incremental-e2e" if NATIVE_SCOPE == "n83-preview" else "native-render-e2e"
)
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

if NATIVE_SCOPE == "n83-preview":
    EVIDENCE = ROOT / "verification" / "native-n83-private" / GITHUB_SHA
elif NATIVE_SCOPE == "editable-source-cycle":
    EVIDENCE = ROOT / "verification" / "native-editable-private" / GITHUB_SHA


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
    if CYCLE_CONTEXT is not None:
        return cycle_run_json(argv, env=env, timeout=timeout)
    return run_sanitized_json(argv, env=env, timeout=timeout)


def exact_inputs() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("native render E2E is GitHub-Actions-only")
    observed = subprocess.check_output(
        ["git", "-C", str(SEMWRIGHT), "rev-parse", "HEAD"], text=True
    ).strip()
    if observed != PIN:
        raise SystemExit(f"Semwright checkout mismatch: expected {PIN}, got {observed}")
    if NATIVE_SCOPE == "editable-source-cycle":
        changed = subprocess.check_output(["git", "-C", str(SEMWRIGHT), "status",
            "--porcelain=v1", "--untracked-files=no"], text=True, stderr=subprocess.PIPE)
        if changed:
            raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_SDK_TRACKED_SOURCE_CHANGED")
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


class N83RunnerFailure(RuntimeError):
    """Closed public failure; original details remain in private evidence."""


N83_STAGES = (
    "seed", "before", "edit", "dirty", "full-after", "assemble-input",
    "admit-preview", "reopen-preview", "dependency-negative",
)


def n83_custody():
    import ci_private_evidence
    return ci_private_evidence


def n83_read_json(path):
    custody = n83_custody()
    return custody.strict(custody.regular(Path(path), custody.MAX_JSON)[0])


def n83_source_identity():
    expected = os.environ.get("MW_NATIVE_EXPECTED_SOURCE_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", expected) or expected != GITHUB_SHA:
        raise N83RunnerFailure("N83_SOURCE_ADMISSION")
    head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, stderr=subprocess.PIPE).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--porcelain=v1", "--untracked-files=normal"],
        text=True, stderr=subprocess.PIPE,
    )
    if head != expected or dirty:
        raise N83RunnerFailure("N83_SOURCE_NOT_CLEAN_F")
    locks = {}
    for relative in ("Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml", "apps/studio/package-lock.json"):
        raw = (ROOT / relative).read_bytes()
        committed = subprocess.check_output(["git", "-C", str(ROOT), "show", "HEAD:" + relative], stderr=subprocess.PIPE)
        if raw != committed:
            raise N83RunnerFailure("N83_LOCK_CHANGED")
        locks[relative] = hashlib.sha256(raw).hexdigest()
    return {
        "motionwright_sha": head, "sdk_revision": PIN, "locks": locks,
        "locks_sha256": hashlib.sha256(
            json.dumps(locks, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def n83_admission(*, preflight_seal_only=False):
    # Pure admission/readback: no owner directory, process or native effect.
    if (
        NATIVE_SCOPE != "n83-preview"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
        or RUN_LABEL or CAPTION_E2E != "0"
        or os.environ.get("MOTIONWRIGHT_V01_PROVIDER_REPEAT", "false") != "false"
        or os.environ.get("MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY", "0") not in {"", "0"}
    ):
        raise N83RunnerFailure("N83_CLOSED_MANUAL_SCOPE_REQUIRED")
    custody = n83_custody()
    context = custody.validate_context({
        "source_sha": GITHUB_SHA, "semwright_sha": PIN,
        "run_id": os.environ.get("GITHUB_RUN_ID", ""), "run_attempt": "1",
    })
    recipient = custody.strict(os.environ.get("MOTIONWRIGHT_N83_RECOVERY_RECIPIENT", "").encode())
    custody.public_key(recipient)  # RSA3072/SPKI digest validated before effects.
    temp_text = os.environ.get("RUNNER_TEMP", "")
    runner_temp = Path(temp_text)
    if (
        not temp_text or not runner_temp.is_absolute() or not runner_temp.is_dir()
        or runner_temp.resolve(strict=True) != runner_temp
        or any(parent.is_symlink() for parent in (runner_temp, *runner_temp.parents))
    ):
        raise N83RunnerFailure("N83_RUNNER_TEMP_REQUIRED")
    preflight = runner_temp / ("mw-n83-preflight-" + context["run_id"])
    if (
        not preflight.is_dir() or preflight.resolve(strict=True) != preflight
        or preflight.is_symlink() or preflight.stat().st_mode & 0o077
    ):
        raise N83RunnerFailure("N83_PRIVATE_PREFLIGHT_ROOT_REQUIRED")
    from importlib.metadata import version
    versions = {name: version(name) for name in ("cryptography", "cffi", "pycparser", "Pillow")}
    if versions != {"cryptography": "50.0.2", "cffi": "2.1.1", "pycparser": "2.23", "Pillow": "10.2.0"}:
        raise N83RunnerFailure("N83_PYTHON_PROFILE_MISMATCH")
    source, source_error = None, None
    try:
        source = n83_source_identity()
    except Exception as error:
        if not preflight_seal_only:
            raise
        # A failed clean-F source control is evidence to seal, never admission
        # to owner creation or Native. This exception is exclusive to salvage.
        source_error = {"exception_type": type(error).__name__, "error": str(error)}
    return {
        "recipient": recipient, "context": context,
        "source": source, "source_check_error": source_error, "runner_temp": runner_temp,
        "versions": {"python": sys.version, "executable": sys.executable, **versions},
        "preflight_root": preflight,
        "completed_stages": [], "stage_processes": [], "daemon": None, "failed": False,
    }


def n83_seal_preflight_failure():
    """One capture of failed source controls; cannot create or operate an owner."""
    admitted = n83_admission(preflight_seal_only=True)
    owner = admitted["runner_temp"] / ("mw-n83-" + admitted["context"]["run_id"])
    public = ROOT / "verification" / "native-n83-public"
    # Refuse any existing owner/evidence/public path, even a dangling symlink.
    # This path must never replace or supplement a Native attempt's own seal.
    if any(path.exists() or path.is_symlink() for path in (owner, EVIDENCE, public)):
        raise N83RunnerFailure("N83_PREFLIGHT_SALVAGE_REQUIRES_NO_OWNER_ATTEMPT")
    custody = n83_custody()
    custody.new_json(admitted["preflight_root"] / "preflight-seal.started.original.json", {
        "schema": "motionwright.n83.preflight-seal-intent/1",
        "context": admitted["context"], "source_observed": admitted["source"],
        "source_check_error": admitted.get("source_check_error"),
        "versions": admitted["versions"], "automatic_retry": False,
        "native_execution_performed_by_this_entry": False,
        "effects_absent_asserted": False, "formal_acceptance": False,
    })
    plaintext, _ = custody.gather_snapshot(
        {"preflight": admitted["preflight_root"]}, preserve_partial=True,
    )
    envelope = custody.encrypt_snapshot(plaintext, admitted["recipient"], admitted["context"])
    public.mkdir(mode=0o700, parents=True, exist_ok=False)
    custody.new_bytes(public / "sealed-originals.bin", envelope)
    manifest = {
        "schema": "motionwright.n83.sealed-public/1", "status": "NOT_VERIFIED",
        "capture_scope": "preflight-failure-only", "context": admitted["context"],
        "recipient_spki_sha256": admitted["recipient"]["sha256"],
        "ciphertext_file": "sealed-originals.bin",
        "ciphertext_sha256": hashlib.sha256(envelope).hexdigest(),
        "ciphertext_bytes": len(envelope), "selected_originals_complete": False,
        "daemon_shutdown_complete": False, "source_unchanged": False,
        "completed_stages": [], "automatic_retry": False, "master_eligible": False,
        "formal_acceptance": False, "release_authorized": False,
        "originals_retained_in_place": True,
    }
    custody.new_json(public / "public-evidence.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)


def n83_original_terminal(name, admitted, *, paired_pid=None, connection_digest=None):
    terminal = n83_read_json(EVIDENCE / name / "stage.terminal.json")
    started = n83_read_json(EVIDENCE / name / "stage.started.json")
    if (
        terminal.get("schema") != "motionwright.native-incremental-stage-terminal/1"
        or terminal.get("status") != "COMPLETED"
        or terminal.get("source") != admitted["source"]
        or started.get("stage") != name or started.get("source") != admitted["source"]
        or terminal.get("automatic_retry") is not False
        or terminal.get("master_eligible") is not False
        or terminal.get("acceptance_granted") is not False
        or (EVIDENCE / name / "stage.unknown.json").exists()
    ):
        raise N83RunnerFailure("N83_ORIGINAL_TERMINAL_NOT_BOUND")
    if paired_pid is None:
        return None
    receipt = n83_read_json(EVIDENCE / name / "paired-producer.original.json")
    digest = receipt.get("connection_file_sha256")
    if (
        type(paired_pid) is not int or paired_pid <= 0
        or type(started.get("pid")) is not int or started["pid"] != paired_pid
        or receipt.get("schema") != "motionwright.native-incremental-paired-producer/1"
        or receipt.get("operation") != "dirty-and-full-after"
        or receipt.get("logical_stages") != ["dirty", "full-after"]
        or receipt.get("stage") != name
        or type(receipt.get("producer_pid")) is not int or receipt["producer_pid"] != paired_pid
        or receipt.get("same_process_coordinator") is not True
        or receipt.get("source") != admitted["source"]
        or receipt.get("automatic_retry") is not False
        or receipt.get("master_eligible") is not False
        or receipt.get("acceptance_granted") is not False
        or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or (connection_digest is not None and digest != connection_digest)
    ):
        raise N83RunnerFailure("N83_PAIRED_ORIGINAL_NOT_BOUND")
    return digest


def n83_stage(name, arguments, *, env, admitted, timeout=420):
    # Rust CLI stdout is empty; original terminal is the sole completion input.
    paired = name == "dirty-and-full-after"
    logical = ("dirty", "full-after") if paired else (name,)
    if (
        (not paired and name not in N83_STAGES)
        or len(arguments) < 2 or arguments[0] != str(MW_BIN) or arguments[1] != name
        or (paired and len(arguments) != 5)
    ):
        raise N83RunnerFailure("N83_STAGE_SCOPE")
    done = admitted["completed_stages"]
    if (
        tuple(N83_STAGES[len(done):len(done) + len(logical)]) != logical
        or any((EVIDENCE / stage).exists() or (EVIDENCE / stage).is_symlink() for stage in logical)
    ):
        raise N83RunnerFailure("N83_STAGE_REPLAY_FORBIDDEN")
    commands = EVIDENCE / "runner-commands"
    commands.mkdir(mode=0o700, exist_ok=True)
    custody = n83_custody()
    custody.new_json(commands / (name + ".started.json"), {
        "stage": name, "logical_stages": list(logical), "argv": arguments, "source": admitted["source"],
        "automatic_retry": False, "started_ns": time.time_ns(),
    })
    code, timed_out, wait_error, quiescent = None, False, None, False
    with (commands / (name + ".stdout.original")).open("xb") as out, (
        commands / (name + ".stderr.original")
    ).open("xb") as err:
        os.chmod(out.name, 0o600)
        os.chmod(err.name, 0o600)
        process = subprocess.Popen(
            arguments, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
            start_new_session=True,
        )
        # Track immediately: even an exceptional wait is finalized by the owner.
        admitted.setdefault("stage_processes", []).append(process)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        except BaseException as error:
            wait_error = {"exception_type": type(error).__name__, "error": str(error)}
        finally:
            try:
                quiescent = n83_stop_group(process)
            except BaseException as error:
                wait_error = {"exception_type": type(error).__name__, "error": str(error)}
                quiescent = False
            code = process.poll()
    custody.new_json(commands / (name + ".exit.original.json"), {
        "stage": name, "logical_stages": list(logical), "exit_code": code, "timed_out": timed_out,
        "wait_error": wait_error, "process_group_quiescent": quiescent,
        "automatic_retry": False, "effects_absent_asserted": False,
        "finished_ns": time.time_ns(),
    })
    bad_exit = timed_out or wait_error is not None or code != 0 or not quiescent
    stdout_present = (commands / (name + ".stdout.original")).stat().st_size != 0
    if paired:
        # A failure after dirty completed must preserve that exact logical
        # prefix without restarting either native job or asserting full-after.
        observed, terminal_error, digest = [], None, None
        for stage in logical:
            try:
                digest = n83_original_terminal(
                    stage, admitted, paired_pid=process.pid, connection_digest=digest,
                )
            except Exception as error:
                terminal_error = {"exception_type": type(error).__name__, "error": str(error)}
                break
            admitted["completed_stages"].append(stage)
            observed.append(stage)
        custody.new_json(commands / (name + ".original-stage-status.json"), {
            "stage": name, "logical_stages": list(logical),
            "producer_pid": process.pid, "same_process_coordinator": True,
            "observed_completed_stages": observed, "terminal_validation_error": terminal_error,
            "automatic_retry": False, "effects_absent_asserted": False,
        })
        if bad_exit or terminal_error is not None or stdout_present or observed != list(logical):
            raise N83RunnerFailure("N83_PAIRED_STAGE_NOT_COMPLETED")
        return
    if bad_exit:
        raise N83RunnerFailure("N83_STAGE_NOT_COMPLETED")
    if stdout_present:
        raise N83RunnerFailure("N83_EMPTY_STDOUT_CONTRACT")
    n83_original_terminal(name, admitted)
    admitted["completed_stages"].append(name)


def n83_seed(database, env, admitted):
    n83_stage("seed", [str(MW_BIN), "seed", str(database), str(EVIDENCE)],
              env=env, admitted=admitted, timeout=120)
    seeded = n83_read_json(EVIDENCE / "seed" / "fixture.identity.json")
    if (
        seeded.get("width") != 640 or seeded.get("height") != 360
        or seeded.get("frame_rate") != {"num": "30", "den": "1"}
        or seeded.get("total_frames") != 90
        or seeded.get("source_window") != {"start": 30, "end": 60}
        or not isinstance(seeded.get("resource"), str) or not seeded["resource"]
    ):
        raise N83RunnerFailure("N83_SEED_IDENTITY")
    return seeded


def n83_remaining_stages(database, connection_path, env, admitted):
    for name in ("before", "edit", "dirty-and-full-after", "assemble-input",
                 "admit-preview", "reopen-preview", "dependency-negative"):
        arguments = [str(MW_BIN), name, str(database)]
        if name in {"before", "dirty-and-full-after"}:
            arguments.append(str(connection_path))
        elif name == "admit-preview":
            arguments.append(sys.executable)
        arguments.append(str(EVIDENCE))
        n83_stage(name, arguments, env=env, admitted=admitted,
                  timeout=840 if name == "dirty-and-full-after" else 420)
    # Three actual producer invocations only: before90 + dirty60 + full-after90.
    # The final dependency-negative is model/Store only, with no fourth render.


def n83_stop_group(process):
    # Only groups created by this wrapper with start_new_session=True.
    # Normal standard-mode cleanup remains unchanged.
    if process is None:
        return True
    for sig, seconds in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            process.wait(timeout=5)
            return True
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                process.wait(timeout=5)
                return True
            time.sleep(0.05)
    return False


def n83_seal(admitted, owner, *, shutdown_complete):
    completed = admitted["completed_stages"]
    if completed != list(N83_STAGES[:len(completed)]):
        raise N83RunnerFailure("N83_NONCANONICAL_STAGE_ROSTER")
    custody = n83_custody()
    source_unchanged = False
    try:
        source_unchanged = n83_source_identity() == admitted["source"]
    except Exception:
        pass
    custody.new_json(EVIDENCE / "runner-final.original.json", {
        "schema": "motionwright.n83.runner-private/1",
        "source": admitted["source"], "versions": admitted["versions"],
        "completed_stages": admitted["completed_stages"],
        "failed": admitted["failed"], "daemon_shutdown_complete": shutdown_complete,
        "source_unchanged": source_unchanged,
        "automatic_retry": False, "owner_retained_in_place": True,
        "final_project_is_negative_revision_without_current_preview": (
            admitted["completed_stages"] == list(N83_STAGES)
        ),
    })
    # One bounded salvage capture even if shutdown could not be proved.
    # A stable read-time inventory never substitutes for process quiescence.
    plaintext, snapshot_complete = custody.gather_snapshot(
        {"owner": owner, "evidence": EVIDENCE, "preflight": admitted["preflight_root"]},
        preserve_partial=True,
    )
    complete = bool(snapshot_complete and shutdown_complete)
    envelope = custody.encrypt_snapshot(plaintext, admitted["recipient"], admitted["context"])
    public = ROOT / "verification" / "native-n83-public"
    public.mkdir(mode=0o700, parents=True, exist_ok=False)
    ciphertext = public / "sealed-originals.bin"
    custody.new_bytes(ciphertext, envelope)
    success = (
        complete and not admitted["failed"] and source_unchanged
        and admitted["completed_stages"] == list(N83_STAGES)
    )
    # Closed public projection: no owner paths, native IDs, sessions, raw errors,
    # project/generation identities, stdout/stderr, or original frame/audio bytes.
    manifest = {
        "schema": "motionwright.n83.sealed-public/1",
        "status": "SCOPED_COMPLETED" if success else "NOT_VERIFIED",
        "context": admitted["context"],
        "recipient_spki_sha256": admitted["recipient"]["sha256"],
        "ciphertext_file": "sealed-originals.bin",
        "ciphertext_sha256": hashlib.sha256(envelope).hexdigest(),
        "ciphertext_bytes": len(envelope),
        "selected_originals_complete": complete,
        "daemon_shutdown_complete": shutdown_complete,
        "source_unchanged": source_unchanged,
        "completed_stages": list(admitted["completed_stages"]),
        "automatic_retry": False, "master_eligible": False,
        "formal_acceptance": False, "release_authorized": False,
        "originals_retained_in_place": True,
    }
    custody.new_json(public / "public-evidence.json", manifest)
    if not success:
        raise N83RunnerFailure("N83_NOT_VERIFIED")
    print(json.dumps(manifest, sort_keys=True), flush=True)


@contextmanager
def native_workspace(admitted):
    if CYCLE_CONTEXT is not None:
        with cycle_workspace(CYCLE_CONTEXT) as raw:
            yield raw
        return
    if admitted is None:
        with tempfile.TemporaryDirectory(prefix="motionwright-native-render-") as raw:
            yield raw
        return
    # No tempfile fallback. Reusing the run's owner path is rejected.
    owner = admitted["runner_temp"] / ("mw-n83-" + admitted["context"]["run_id"])
    owner.mkdir(mode=0o700, exist_ok=False)
    owner.chmod(0o700)
    try:
        yield str(owner)
    except BaseException as error:
        admitted["failed"] = True
        try:
            n83_custody().new_json(EVIDENCE / "runner-error.original.json", {
                "exception_type": type(error).__name__, "error": str(error),
                "effects_absent_asserted": False, "automatic_retry": False,
            })
        except Exception:
            pass
    finally:
        quiescence = []
        # Do not short-circuit: every started group must receive bounded cleanup.
        for process in [admitted["daemon"], *admitted.get("stage_processes", [])]:
            try:
                quiescence.append(n83_stop_group(process))
            except BaseException:
                quiescence.append(False)
        try:
            n83_seal(admitted, owner, shutdown_complete=all(quiescence))
        except BaseException as error:
            try:
                n83_custody().new_json(EVIDENCE / "seal-error.original.json", {
                    "exception_type": type(error).__name__, "error": str(error),
                    "automatic_retry": False, "originals_retained_in_place": True,
                })
            except Exception:
                pass
            raise N83RunnerFailure("N83_SEAL_OR_OUTCOME_NOT_VERIFIED") from None



# This finite cycle uses the existing fixture, Broker policy and private sealer.
CYCLE_STAGES = ("original", "reopened", "edited", "edited_reopened")
CYCLE_CONTEXT = None


def cycle_admission(*, preflight_only=False):
    if (NATIVE_SCOPE != "editable-source-cycle"
            or os.environ.get("GITHUB_REPOSITORY") != "seradotcom/motionwright"
            or os.environ.get("GITHUB_JOB") != "real-native-render"
            or os.environ.get("GITHUB_WORKFLOW_SHA") != GITHUB_SHA
            or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
            or RUN_LABEL or CAPTION_E2E != "0"
            or os.environ.get("MOTIONWRIGHT_V01_PROVIDER_REPEAT", "false") != "false"
            or os.environ.get("MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY", "0") not in {"", "0"}):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_REQUIRES_CLOSED_MANUAL_SELECTION")
    custody = n83_custody()
    context = custody.validate_context({"source_sha": GITHUB_SHA, "semwright_sha": PIN,
        "run_id": os.environ.get("GITHUB_RUN_ID", ""), "run_attempt": "1"})
    recipient = custody.strict(os.environ.get("MOTIONWRIGHT_N83_RECOVERY_RECIPIENT", "").encode())
    custody.public_key(recipient)
    temporary = Path(os.environ.get("RUNNER_TEMP", ""))
    if (not temporary.is_absolute() or not temporary.is_dir()
            or temporary.resolve(strict=True) != temporary
            or any(p.is_symlink() for p in (temporary, *temporary.parents))):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_PRIVATE_PARENT_REQUIRED")
    preflight = temporary / ("mw-mc-cycle-preflight-" + context["run_id"])
    if (not preflight.is_dir() or preflight.is_symlink()
            or preflight.stat().st_mode & 0o077):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_PRIVATE_PREFLIGHT_REQUIRED")
    from importlib.metadata import version
    versions = {key: version(key) for key in ("cryptography", "cffi", "pycparser", "Pillow")}
    if versions != {"cryptography":"50.0.2", "cffi":"2.1.1", "pycparser":"2.23", "Pillow":"10.2.0"}:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_PYTHON_PROFILE_MISMATCH")
    source = None
    try:
        source = n83_source_identity()
    except Exception:
        if not preflight_only:
            raise
    return {"context":context, "recipient":recipient, "source":source,
        "runner_temp":temporary, "preflight":preflight, "versions":versions,
        "processes":[], "hosts":[], "calls":0, "jobs":[], "completed":[],
        "stages":{}, "failed":False, "verified":False}


def cycle_seal_preflight_failure():
    context = cycle_admission(preflight_only=True)
    owner = context["runner_temp"] / ("mw-mc-cycle-" + context["context"]["run_id"])
    public = ROOT / "verification" / "native-editable-public"
    if any(path.exists() or path.is_symlink() for path in (owner, EVIDENCE, public)):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_PREFLIGHT_CANNOT_REPLACE_OWNER_CAPTURE")
    cycle_record(context["preflight"] / "seal.started.original.json", {
        "context":context["context"],"source_observed":context["source"],
        "capture_scope":"preflight-only","native_execution_performed":False,"automatic_retry":False})
    plaintext, complete = n83_custody().gather_snapshot({"preflight":context["preflight"]},preserve_partial=True)
    envelope = n83_custody().encrypt_snapshot(plaintext,context["recipient"],context["context"])
    public.mkdir(mode=0o700,parents=True,exist_ok=False)
    n83_custody().new_bytes(public / "sealed-originals.bin",envelope)
    cycle_record(public / "public-evidence.json", {"selection":"editable-source-cycle",
        "status":"NOT_VERIFIED","context":context["context"],"capture_scope":"preflight-only",
        "recipient_spki_sha256":context["recipient"]["sha256"],"ciphertext_file":"sealed-originals.bin",
        "ciphertext_bytes":len(envelope),"ciphertext_sha256":hashlib.sha256(envelope).hexdigest(),
        "preflight_originals_complete":complete,"selected_originals_complete":False,
        "automatic_retry":False,"formal_acceptance":False,"release_authorized":False})


def cycle_record(path, value):
    n83_custody().new_json(path, value)


def cycle_observe_host():
    row = CYCLE_CONTEXT.get("active_host") if CYCLE_CONTEXT is not None else None
    if row is None or row.get("quiescent") is True:
        return
    from native_host_shared_state import capsule_track_descendants
    try:
        capsule_track_descendants({"active_daemon":row})
    except BaseException:
        row["inventory_failed"] = True
        raise


def cycle_stop(row):
    if row.get("quiescent") is True:
        return True
    inventory_ok = True
    if row.get("host"):
        from native_host_shared_state import capsule_track_descendants, capsule_stop_descendants
        try:
            capsule_track_descendants({"active_daemon":row})
        except BaseException:
            row["inventory_failed"] = True
        inventory_ok = not row.get("inventory_failed", False)
    group_closed = n83_stop_group(row["process"])
    descendants_closed = True
    if row.get("host"):
        descendants_closed = capsule_stop_descendants(row)
        if not row.get("closure_recorded"):
            row["closure_recorded"] = True
            cycle_record(row["closure_path"], {"host_identity":row.get("identity"),
                "observed_descendants":list(row["descendants"].values()),
                "final_inventory_verified":inventory_ok,"broker_group_closed":group_closed,
                "observed_descendants_closed":descendants_closed,"automatic_retry":False})
    row["quiescent"] = bool(group_closed and descendants_closed and inventory_ok)
    return row["quiescent"]


def cycle_run_json(argv, *, env, timeout=30):
    context = CYCLE_CONTEXT
    if context is None or context["calls"] >= 2048:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_CALL_BUDGET")
    cycle_observe_host()
    number = context["calls"]
    context["calls"] += 1
    folder = EVIDENCE / "calls" / f"{number:04d}"
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    cycle_record(folder / "started.original.json", {"argv":list(argv), "timeout_seconds":timeout,
        "source":context["source"], "automatic_retry":False})
    with (folder / "stdout.original").open("xb") as stdout, (folder / "stderr.original").open("xb") as stderr:
        process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, start_new_session=True)
        row = {"process":process, "quiescent":False}
        context["processes"].append(row)
        timeout_hit = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timeout_hit = True
            code = None
        finally:
            stopped = cycle_stop(row)
        cycle_record(folder / "terminal.original.json", {"pid":process.pid,
            "exit_code":code, "deadline_exceeded":timeout_hit, "group_quiescent":stopped,
            "automatic_retry":False, "outcome_absent_asserted":False})
    cycle_observe_host()
    if code != 0 or timeout_hit or not stopped:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_COMMAND_NOT_VERIFIED")
    raw, _ = n83_custody().regular(folder / "stdout.original", 8 * 1024 * 1024)
    value = n83_custody().strict(raw)
    if not isinstance(value, dict):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_NONOBJECT_RESPONSE")
    return value


def cycle_execute(command, args, *, socket, session, env, timeout=90):
    allowed = {"project.inspect", "project.validate", "composition.inspect",
        "composition.plan", "composition.apply", "composition.verify",
        "render.start", "render.status", "render.result"}
    if command not in allowed:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_COMMAND_OUTSIDE_SELECTION")
    name = "driver.motion-canvas." + command
    encoded = json.dumps(args, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode()) > 2 * 1024 * 1024:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_ARGUMENT_BUDGET")
    envelope = cycle_run_json([str(SW_BINS / "semwright"), "--socket", str(socket),
        "--session-file", str(session), "--json", "execute", name, "--args-json", encoded],
        env=env, timeout=timeout)
    execution = envelope.get("execution", {})
    provenance = execution.get("provenance", {}) if isinstance(execution, dict) else {}
    if (envelope.get("ok") is not True or envelope.get("command") != name
            or execution.get("backend") != "driver:motion-canvas"
            or provenance.get("provider") != "driver:motion-canvas"
            or provenance.get("source") != "driver"
            or not re.fullmatch("[0-9a-f]{64}", str(provenance.get("descriptor_sha256", "")))
            or type(provenance.get("provider_generation")) is not int
            or provenance["provider_generation"] <= 0 or not isinstance(envelope.get("data"), dict)):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_HOST_RECEIPT_MISMATCH")
    return envelope["data"]


def cycle_register_job(job):
    context = CYCLE_CONTEXT
    if not isinstance(job, str) or not job or job in context["jobs"] or len(context["jobs"]) >= 5:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_JOB_REUSED_OR_BUDGET")
    context["jobs"].append(job)


def cycle_diagnostic_acknowledgements(diagnostic, identity, phase):
    """Project selected start receipts without granting completion or replay authority."""
    prefixes = {"original":"native-render-real-driver-host",
        "edited":"native-source-cycle-edited-authorized-render"}
    if phase not in prefixes or type(diagnostic) is not dict or type(identity) is not dict:
        raise N83RunnerFailure("EDITABLE_JOB_OBSERVATION_INPUT")
    stamp = {key:identity.get(key) for key in ("project_id", "generation", "revision")}
    uuid_pattern = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    if (any(type(stamp[key]) is not str or re.fullmatch(uuid_pattern, stamp[key]) is None
            for key in ("project_id", "generation"))
            or type(stamp["revision"]) is not int or not 0 <= stamp["revision"] < 2**64
            or diagnostic.get("native_render_e2e") != "FAIL"
            or type(diagnostic.get("revision")) is not int
            or any(diagnostic.get(key) != value for key, value in stamp.items())):
        raise N83RunnerFailure("EDITABLE_JOB_OBSERVATION_SOURCE_BINDING")
    receipts = diagnostic.get("receipts")
    if type(receipts) is not list or len(receipts) > 128:
        raise N83RunnerFailure("EDITABLE_JOB_OBSERVATION_RECEIPT_BUDGET")
    observed = []
    for index, row in enumerate(receipts):
        if type(row) is not dict:
            raise N83RunnerFailure("EDITABLE_JOB_OBSERVATION_RECEIPT_SHAPE")
        if (row.get("command") != "driver.motion-canvas.render.start"
                or row.get("stage") != "completed"
                or row.get("request_id") != prefixes[phase] + ":motion:0:render-start"):
            continue
        if (any(row.get(key) != value for key, value in stamp.items())
                or type(row.get("revision")) is not int
                or type(row.get("id")) is not str or type(row.get("request_sha256")) is not str
                or re.fullmatch(uuid_pattern, str(row.get("id", ""))) is None
                or re.fullmatch(r"[0-9a-f]{64}", str(row.get("request_sha256", ""))) is None):
            raise N83RunnerFailure("EDITABLE_JOB_OBSERVATION_RECEIPT_BINDING")
        payload = row.get("payload")
        result = payload.get("result") if type(payload) is dict else None
        data = result.get("data") if type(result) is dict else None
        execution = result.get("execution") if type(result) is dict else None
        provenance = execution.get("provenance") if type(execution) is dict else None
        if (type(data) is not dict or type(execution) is not dict or type(provenance) is not dict
                or result.get("provenance_checked") is not True
                or execution.get("backend") != "driver:motion-canvas"
                or execution.get("dry_run") is not False or execution.get("policy_decision") != "allow"
                or provenance.get("provider") != "driver:motion-canvas" or provenance.get("source") != "driver"
                or type(provenance.get("provider_generation")) is not int
                or not 0 < provenance["provider_generation"] < 2**64
                or type(provenance.get("descriptor_sha256")) is not str
                or re.fullmatch(r"[0-9a-f]{64}", str(provenance.get("descriptor_sha256", ""))) is None):
            raise N83RunnerFailure("EDITABLE_JOB_OBSERVATION_PROVIDER_BINDING")
        job = data.get("job_ref")
        if (type(job) is not str or len(job) > 2048
                or re.fullmatch(r"mc1:[^\x00-\x20\x7f]+:render_job:render-[0-9a-f]{32}", job) is None
                or data.get("state") not in {"queued", "starting", "rendering", "succeeded", "failed", "cancelled"}):
            raise N83RunnerFailure("EDITABLE_JOB_OBSERVATION_JOB_BINDING")
        observed.append({"phase":phase,"source_stamp":stamp,"native_job_ref":job,
            "receipt_pointer":f"/receipts/{index}","receipt_id":row["id"],
            "request_id":row["request_id"],"request_sha256":row["request_sha256"],
            "observed_start_state":data["state"],"render_completion_inferred":False})
    return observed


def cycle_job_observation(context):
    """Private sidecar: the wrapper registry may omit a failed mediated start."""
    report = {"source":context["source"],"derived_observation":True,
        "registry":{"refs":list(context["jobs"]),"scope":"wrapper registered job references",
            "mediated_registration":"after successful command return",
            "direct_registration":"after acknowledged render.start",
            "complete_for_verified_selected_cycle":bool(context["verified"]
                and context["completed"] == list(CYCLE_STAGES) and len(context["jobs"]) == 5)},
        "diagnostics":[],"acknowledged_job_refs":[],"complete_inventory_of_effects_claimed":False,
        "absence_of_effects_asserted":False,"render_completion_inferred":False,
        "receipts_rebound_as_authority":False,"automatic_retry":False,"formal_acceptance":False}
    custody = n83_custody()
    terminal = EVIDENCE / "runner.terminal.original.json"
    report["runner_terminal"] = cycle_file(terminal) if terminal.is_file() else {"status":"NOT_RETAINED"}
    for phase, folder in (("original", EVIDENCE), ("edited", EVIDENCE / "cycle" / "edited")):
        path = folder / "motionwright-render-diagnostic.json"
        row = {"phase":phase,"path":path.relative_to(EVIDENCE).as_posix(),"status":"NOT_RETAINED"}
        report["diagnostics"].append(row)
        if not os.path.lexists(path):
            continue
        try:
            raw, _ = custody.regular(path, 2 * 1024 * 1024)
            row["diagnostic"] = {"path":row["path"],"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
            identity_path = EVIDENCE / "cycle" / phase / "source.identity.original.json"
            identity_raw, _ = custody.regular(identity_path, 16 * 1024)
            row["source_identity"] = {"path":identity_path.relative_to(EVIDENCE).as_posix(),
                "bytes":len(identity_raw),"sha256":hashlib.sha256(identity_raw).hexdigest()}
            rows = cycle_diagnostic_acknowledgements(custody.strict(raw), custody.strict(identity_raw), phase)
            row.update(status="ORIGINAL_RECEIPTS_INSPECTED", acknowledgements=rows)
            report["acknowledged_job_refs"].extend(value["native_job_ref"] for value in rows)
        except Exception as error:
            row.update(status="NOT_VERIFIED", error_type=type(error).__name__, error=str(error))
    report["acknowledged_job_refs"] = sorted(set(report["acknowledged_job_refs"]))
    return report


def cycle_native_render(fingerprint, *, socket, session, env):
    started_ns = time.perf_counter_ns()
    started = cycle_execute("render.start", {"expected_fingerprint":fingerprint,
        "profile":{"first_frame":0,"end_frame_exclusive":60,"scale":"full",
                   "transparent":False,"timeout_ms":300000}}, socket=socket, session=session,
        env=env, timeout=120)
    start_us = elapsed_us(started_ns, time.perf_counter_ns())
    job = started.get("job_ref")
    cycle_register_job(job)
    started_ns = time.perf_counter_ns()
    deadline = time.monotonic() + 330
    reads = 0
    while time.monotonic() < deadline:
        status = cycle_execute("render.status", {"job_ref":job}, socket=socket,
            session=session, env=env, timeout=45)
        reads += 1
        if status.get("job_ref") != job:
            raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_JOB_IDENTITY_CHANGED")
        state = status.get("state")
        if state == "succeeded":
            break
        if state not in {"queued", "running", "pending", "starting"}:
            raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_NATIVE_NOT_SUCCEEDED")
        time.sleep(1)
    else:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_NATIVE_OUTCOME_UNKNOWN")
    wait_us = elapsed_us(started_ns, time.perf_counter_ns())
    started_ns = time.perf_counter_ns()
    result = cycle_execute("render.result", {"job_ref":job}, socket=socket,
        session=session, env=env, timeout=90)
    if (result.get("job_ref") != job or result.get("state") != "succeeded"
            or result.get("artifact", {}).get("frame_count") != 60
            or status.get("artifact", {}).get("frame_count") != 60):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_INCOMPLETE_ARTIFACT")
    return result, {"start":start_us, "terminal_wait":wait_us,
        "result":elapsed_us(started_ns, time.perf_counter_ns())}, reads


def cycle_direct_film(*, film_file, socket, session, env, mediated_job_ref, **unused):
    cycle_register_job(mediated_job_ref)
    overall = time.perf_counter_ns()
    stage_start = overall
    film = n83_read_json(film_file)
    plan = cycle_execute("composition.plan", {"film":film,
        "budget":{"max_iterations":8,"max_operations":4096,"max_findings":1024,
                  "max_observations":128,"max_elapsed_ms":120000}},
        socket=socket, session=session, env=env, timeout=150)
    costs = {"plan":elapsed_us(stage_start, time.perf_counter_ns())}
    plan_ref = plan.get("plan_ref")
    if not isinstance(plan_ref, str) or not plan_ref:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_PLAN_MISSING")
    stage_start = time.perf_counter_ns()
    applied = cycle_execute("composition.apply", {"plan_ref":plan_ref,"dry_run":False},
        socket=socket, session=session, env=env, timeout=150)
    costs["apply"] = elapsed_us(stage_start, time.perf_counter_ns())
    fingerprint = applied.get("fingerprint")
    if (applied.get("applied") is not True or applied.get("execution_status") != "completed"
            or not re.fullmatch("[0-9a-f]{64}", str(fingerprint))):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_APPLY_NOT_VERIFIED")
    result, render_costs, reads = cycle_native_render(fingerprint, socket=socket, session=session, env=env)
    costs.update(render_costs)
    stage_start = time.perf_counter_ns()
    verification = cycle_execute("composition.verify", {"plan_ref":plan_ref,"job_ref":result["job_ref"]},
        socket=socket, session=session, env=env, timeout=150)
    costs["verify"] = elapsed_us(stage_start, time.perf_counter_ns())
    checks = verification.get("measurement", {}).get("validation", {}).get("checks")
    if (verification.get("report", {}).get("execution_status") != "completed"
            or verification.get("report", {}).get("support_level") != "native"
            or verification.get("measurement", {}).get("findings") != []
            or not isinstance(checks, list) or not checks
            or any(row.get("verdict") != "PASS" for row in checks)):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_NATIVE_VERIFICATION_FAILED")
    return {"artifact":result["artifact"],"job_ref":result["job_ref"],
        "film_sha256":digest(film_file),"status_reads":reads,
        "measured_direct_total_micros":elapsed_us(overall,time.perf_counter_ns()),
        "measured_direct_substages_micros":costs,"independent_job_confirmed":True,
        "native_validation_checks":len(checks),
        "scope":"ci_direct_broker_vs_motionwright_mediated_same_film"}


def cycle_new_stage(name):
    context = CYCLE_CONTEXT
    if name != CYCLE_STAGES[len(context["stages"])]:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_STAGE_REPLAY")
    path = EVIDENCE / "cycle" / name
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    cycle_record(path / "stage.started.original.json", {"stage":name,"source":context["source"],
        "automatic_retry":False})
    context["stages"][name] = path
    return path


def cycle_copy(source, destination, maximum):
    if any(parent.is_symlink() for parent in (source, *source.parents)):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_SYMLINKED_ORIGINAL")
    raw, _ = n83_custody().regular(source, maximum)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    n83_custody().new_bytes(destination, raw)
    return {"path":destination.relative_to(EVIDENCE).as_posix(),
        "bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}


def cycle_file(path):
    raw, _ = n83_custody().regular(path, 16 * 1024 * 1024)
    return {"path":path.relative_to(EVIDENCE).as_posix(),"bytes":len(raw),
        "sha256":hashlib.sha256(raw).hexdigest()}


def cycle_capture(name, *, paths, socket, session, env, artifact, job):
    folder = CYCLE_CONTEXT["stages"][name]
    inspected = cycle_execute("project.inspect", {}, socket=socket, session=session, env=env)
    valid = cycle_execute("project.validate", {}, socket=socket, session=session, env=env)
    if (valid.get("valid") is not True or valid.get("fingerprint") != inspected.get("fingerprint")
            or valid.get("revision") != inspected.get("project", {}).get("revision")
            or not re.fullmatch("[0-9a-f]{64}", str(valid.get("generated_fingerprint", "")))):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_NATIVE_SOURCE_INVALID")
    original = cycle_copy(paths["project"] / "semwright-motion.json",
        folder / "native" / "semwright-motion.original.json", 16 * 1024 * 1024)
    if (original["sha256"] != inspected["fingerprint"]
            or n83_read_json(EVIDENCE / original["path"]) != inspected["project"]):
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_SAVED_SOURCE_DIFFERS_FROM_HOST")
    cycle_record(folder / "native" / "project-inspect.json", inspected)
    cycle_record(folder / "native" / "project-validate.json", valid)
    binding = cycle_run_json([str(MW_BIN), "cycle-verify-binding",
        str(folder / "film.original.json"), str(folder / "native" / "project-inspect.json")], env=env)
    cycle_record(folder / "native" / "binding-verification.original.json", binding)
    generated = paths["project"] / (".semwright-generated-" + valid["generated_fingerprint"])
    files = []
    inventory = inspected.get("generated")
    if not isinstance(inventory, list) or not 1 <= len(inventory) <= 64:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_SOURCE_INVENTORY_BUDGET")
    for item in inventory:
        relative = item.get("path", "")
        parts = relative.split("/") if isinstance(relative, str) else []
        if (not parts or any(part in {"", ".", ".."} for part in parts)
                or "\\" in relative or ":" in relative):
            raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_SOURCE_PATH")
        record = cycle_copy(generated.joinpath(*parts), folder / "native" / "generated" / relative,
            16 * 1024 * 1024)
        if record["bytes"] != item.get("bytes") or record["sha256"] != item.get("sha256"):
            raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_NATIVE_SOURCE_DIGEST")
        files.append({"role":relative, **record})
    artifact_root = checked_direct_artifact(paths["output"], artifact)
    from native_direct_mediated_parity import native_png_hashes
    native_png_hashes(artifact_root, 60)
    cycle_copy(artifact_root / "artifact-manifest.json", folder / "render" / "artifact-manifest.json", 8*1024*1024)
    for index in range(60):
        relative = f"frames/{index:06d}.png"
        cycle_copy(artifact_root / relative, folder / "render" / relative, 24*1024*1024)
    row = {"session":str(session),"project":cycle_file(folder / "project.original.json"),
        "film":cycle_file(folder / "film.original.json"),"source_files":files,
        "managed_inspection":cycle_file(folder / "native" / "project-inspect.json"),
        "frames_dir":(folder / "render").relative_to(EVIDENCE).as_posix(),"native_job_ref":job}
    cycle_record(folder / "stage.terminal.original.json", {"stage":name,"capture":row,
        "native_saved_source":original,"original_host_pid":CYCLE_CONTEXT["hosts"][-1],
        "native_authority_rebound":False,"automatic_retry":False})
    CYCLE_CONTEXT["completed"].append(name)
    return row


def cycle_start_host(name, paths, config_path, env):
    folder = CYCLE_CONTEXT["stages"][name]
    host = folder / "host"
    host.mkdir(mode=0o700)
    child_env = dict(env)
    for key, leaf in (("HOME","home"),("XDG_STATE_HOME","state"),("XDG_RUNTIME_DIR","runtime")):
        path = host / leaf
        path.mkdir(mode=0o700)
        child_env[key] = str(path)
    socket = host / "runtime" / "broker.sock"
    session = host / "runtime" / "owner.session"
    # Use a short owned socket root. The full retained stage path may exceed sun_path.
    runtime = CYCLE_CONTEXT["owner"] / ("r-" + name.replace("_", "-"))
    runtime.mkdir(mode=0o700, exist_ok=False)
    socket = runtime / "broker.sock"
    session = runtime / "owner.session"
    child_env["XDG_RUNTIME_DIR"] = str(runtime)
    log = folder / "semwrightd.original.log"
    log_stream = log.open("xb")
    process = subprocess.Popen([str(SW_BINS / "semwrightd"), "--config", str(config_path),
        "--socket", str(socket)], env=child_env, stdin=subprocess.DEVNULL,
        stdout=log_stream, stderr=log_stream, start_new_session=True)
    row = {"process":process,"quiescent":False}
    CYCLE_CONTEXT["processes"].append(row)
    from native_host_shared_state import capsule_pid_identity
    row.update(host=True,identity=capsule_pid_identity(process.pid),descendants={},
        closure_path=folder / "host-closure.original.json")
    CYCLE_CONTEXT["active_host"] = row
    if row["identity"] is None:
        row["inventory_failed"] = True
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_HOST_IDENTITY_NOT_OBSERVED")
    if process.pid in CYCLE_CONTEXT["hosts"]:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_HOST_PID_REUSED")
    CYCLE_CONTEXT["hosts"].append(process.pid)
    cycle_record(folder / "host.started.original.json", {"pid":process.pid,
        "config_sha256":digest(config_path),"daemon_sha256":digest(SW_BINS / "semwrightd"),
        "same_saved_native_project":str(paths["project"]),"fresh_runtime":str(runtime)})
    try:
        wait_for_socket(process, socket, log)
        discovered = cycle_run_json([str(SW_BINS / "semwright"),"--socket",str(socket),
            "--session-file",str(session),"--json","capabilities","search","motion-canvas",
            "--provider","driver:motion-canvas"],env=child_env)
        if (not discovered.get("data", {}).get("capabilities") or not session.is_file()
                or session.stat().st_mode & 0o777 != 0o600):
            raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_FRESH_HOST_NOT_ADMITTED")
    except BaseException:
        cycle_stop(row)
        log_stream.close()
        raise
    return row, log_stream, socket, session, child_env, runtime


def cycle_remaining(database, paths, config_path, env, original_row):
    rows = {"original":original_row}
    for name in CYCLE_STAGES[1:]:
        folder = cycle_new_stage(name)
        if name == "edited":
            cycle_run_json([str(MW_BIN),"cycle-edit",str(database),
                str(EVIDENCE / rows["reopened"]["project"]["path"]),str(folder)],env=env)
        else:
            cycle_run_json([str(MW_BIN),"cycle-snapshot",str(database),str(folder)],env=env)
        row, stream, socket, session, child_env, runtime = cycle_start_host(name, paths, config_path, env)
        try:
            if name == "edited":
                connection = folder / "connection.original.json"
                cycle_record(connection, {"schema":"motionwright-semwright-connection/1",
                    "executable":str((SW_BINS / "semwright").resolve()),
                    "executable_sha256":digest(SW_BINS / "semwright"),"socket":str(socket),
                    "session_file":str(session),"output_root":str(paths["output"]),
                    "resource":n83_read_json(folder / "source.identity.original.json")["resource"]})
                result = cycle_run_json([str(MW_BIN),"cycle-render-edited",str(database),
                    str(connection),str(folder / "native-render-evidence.original.json")],
                    env=child_env,timeout=420)
                if result.get("native_render_e2e") != "PASS" or result.get("frame_count") != 60:
                    raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_EDIT_RENDER_FAILED")
                cycle_register_job(result.get("job_ref"))
            else:
                # Inspect the persisted source in the fresh driver. No plan/apply,
                # no inherited plan_ref and no replay of a prior native job.
                inspected = cycle_execute("project.inspect", {},socket=socket,session=session,env=child_env)
                previous = rows["original" if name == "reopened" else "edited"]
                expected = n83_read_json(EVIDENCE / previous["managed_inspection"]["path"])
                if inspected != expected:
                    raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_REOPEN_CHANGED_NATIVE_SOURCE")
                result, _, _ = cycle_native_render(inspected["fingerprint"],socket=socket,
                    session=session,env=child_env)
            rows[name] = cycle_capture(name,paths=paths,socket=socket,session=session,env=child_env,
                artifact=result["artifact"],job=result["job_ref"])
        finally:
            quiescent = cycle_stop(row)
            stream.close()
            cycle_record(folder / "host.terminal.original.json", {"pid":row["process"].pid,
                "exit_code":row["process"].returncode,"group_quiescent":quiescent,
                "automatic_retry":False,"no_absence_of_effects_inferred":True})
            # Preserve session/config originals under the owner before sealing.
            if not quiescent:
                raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_HOST_SHUTDOWN_UNKNOWN")
    original_identity = n83_read_json(CYCLE_CONTEXT["stages"]["original"] / "source.identity.original.json")
    original_native = n83_read_json(EVIDENCE / rows["original"]["managed_inspection"]["path"])["project"]
    # Native identities come from the compiler's actual persisted projection.
    matches = [(scene["id"], node["id"]) for scene in original_native["scenes"] for node in scene["nodes"]
        if node["id"] == original_identity["native_node_id"]
        and scene["id"] == original_identity["native_scene_id"]
        and node.get("properties", {}).get("fill") == "#F5F5F2"]
    if len(matches) != 1:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_NATIVE_SUBJECT_NOT_UNIQUE")
    native_scene, native_node = matches[0]
    witness = {"schema":"motionwright.motion-canvas-source-roundtrip/1",
        "profile":{"frames":60,"width":1920,"height":1080,"fps":"30/1"},
        "edit":{"scene_id":original_identity["scene_id"],"node_id":original_identity["node_id"],
                "property":"style.fill","native_scene_id":native_scene,"native_node_id":native_node,
                "native_property":"fill","from":"#F5F5F2","to":"#E85A35"},"stages":rows}
    cycle_record(EVIDENCE / "roundtrip-witness.original.json", witness)
    import native_motion_canvas_roundtrip as reader
    def verify_binding(film_path, inspection_path):
        return cycle_run_json([str(MW_BIN),"cycle-verify-binding",str(film_path),str(inspection_path)],env=env)
    report = reader.inspect(EVIDENCE, witness, binding_verifier=verify_binding)
    cycle_record(EVIDENCE / "roundtrip-reader.original.json", report)
    if len(CYCLE_CONTEXT["jobs"]) != 5 or len(CYCLE_CONTEXT["hosts"]) != 4:
        raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_OBSERVED_PROCESS_OR_JOB_COUNT")
    CYCLE_CONTEXT["verified"] = True


@contextmanager
def cycle_workspace(context):
    owner = context["runner_temp"] / ("mw-mc-cycle-" + context["context"]["run_id"])
    owner.mkdir(mode=0o700, exist_ok=False)
    context["owner"] = owner
    try:
        context["executables"] = {str(path):digest(path) for path in (MW_BIN, SW_BINS / "semwright",
            SW_BINS / "semwrightd", SW_BINS / "semwright-sandbox", SW_BINS / "semwright-motion-canvas-driver")}
        cycle_record(EVIDENCE / "executables.original.json", context["executables"])
        cycle_record(EVIDENCE / "cycle.started.original.json", {"source":context["source"],
            "selected_stages":list(CYCLE_STAGES),"maximum_native_jobs":5,"maximum_full_frames":300,
            "automatic_retry":False,"formal_acceptance":False})
        yield str(owner)
    except BaseException as error:
        context["failed"] = True
        try:
            cycle_record(EVIDENCE / "runner-error.original.json", {"type":type(error).__name__,
                "error":str(error),"automatic_retry":False,"absence_of_effects_asserted":False})
        except BaseException:
            pass
    finally:
        quiet = []
        for row in context["processes"]:
            try:
                quiet.append(cycle_stop(row))
            except BaseException:
                quiet.append(False)
        unchanged = False
        try:
            unchanged = (n83_source_identity() == context["source"]
                and all(digest(Path(path)) == expected for path, expected in context["executables"].items()))
        except BaseException:
            pass
        persisted = True
        try:
            cycle_record(EVIDENCE / "runner.terminal.original.json", {
                "source":context["source"],"completed_stages":context["completed"],
                "host_pids":context["hosts"],"native_jobs":context["jobs"],
                "comparison_verified":context["verified"],"failed":context["failed"],
                "groups_quiescent":all(quiet),"source_unchanged":unchanged,
                "automatic_retry":False,"formal_acceptance":False})
        except BaseException:
            persisted = False
        try:
            cycle_record(EVIDENCE / "native-job-observations.original.json", cycle_job_observation(context))
        except BaseException as error:
            # An optional projection failure never prevents sealing the original
            # diagnostic, source, existing terminal, or partial native output.
            try:
                cycle_record(EVIDENCE / "native-job-observation-error.original.json", {
                    "error_type":type(error).__name__,"error":str(error),
                    "automatic_retry":False,"absence_of_effects_asserted":False})
            except BaseException:
                pass
        paths = {"owner":owner,"evidence":EVIDENCE,"preflight":context["preflight"]}
        plaintext, complete = n83_custody().gather_snapshot(paths,preserve_partial=True)
        ciphertext = n83_custody().encrypt_snapshot(plaintext,context["recipient"],context["context"])
        public = ROOT / "verification" / "native-editable-public"
        public.mkdir(mode=0o700,parents=True,exist_ok=False)
        n83_custody().new_bytes(public / "sealed-originals.bin",ciphertext)
        success = bool(complete and all(quiet) and persisted and unchanged and not context["failed"]
            and context["verified"] and context["completed"] == list(CYCLE_STAGES))
        cycle_record(public / "public-evidence.json", {"selection":"editable-source-cycle",
            "status":"SCOPED_COMPLETED" if success else "NOT_VERIFIED","context":context["context"],
            "recipient_spki_sha256":context["recipient"]["sha256"],"ciphertext_file":"sealed-originals.bin",
            "ciphertext_bytes":len(ciphertext),"ciphertext_sha256":hashlib.sha256(ciphertext).hexdigest(),
            "selected_originals_complete":bool(complete and all(quiet) and persisted),
            "groups_quiescent":all(quiet),"source_unchanged":unchanged,
            "completed_stages":context["completed"],"observed_native_jobs":len(context["jobs"]),
            "observed_host_processes":len(context["hosts"]),"automatic_retry":False,
            "master_eligible":False,"formal_acceptance":False,"release_authorized":False,
            "absence_of_effects_asserted":False})
        if not success:
            raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_NOT_VERIFIED")


def main() -> None:
    global CYCLE_CONTEXT
    if NATIVE_SCOPE not in {"standard", "n83-preview", "editable-source-cycle"}:
        raise N83RunnerFailure("N83_UNKNOWN_SCOPE")
    if NATIVE_SCOPE == "editable-source-cycle":
        CYCLE_CONTEXT = cycle_admission()
    admitted = n83_admission() if NATIVE_SCOPE == "n83-preview" else None
    exact_inputs()
    EVIDENCE.mkdir(parents=True, exist_ok=False)
    if admitted is not None or CYCLE_CONTEXT is not None:
        EVIDENCE.chmod(0o700)

    with native_workspace(admitted) as raw:
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
        if admitted is not None:
            seeded = n83_seed(database, env, admitted)
        else:
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
            if CYCLE_CONTEXT is not None:
                original_stage = cycle_new_stage("original")
                cycle_run_json([str(MW_BIN), "cycle-snapshot", str(database), str(original_stage)], env=env)

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
        if admitted is not None:
            admitted["daemon"] = daemon
        if CYCLE_CONTEXT is not None:
            first_host = {"process":daemon,"quiescent":False}
            CYCLE_CONTEXT["processes"].append(first_host)
            from native_host_shared_state import capsule_pid_identity
            first_host.update(host=True, identity=capsule_pid_identity(daemon.pid), descendants={},
                closure_path=EVIDENCE / "first-host-closure.original.json")
            CYCLE_CONTEXT["active_host"] = first_host
            if first_host["identity"] is None:
                first_host["inventory_failed"] = True
                raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_FIRST_HOST_IDENTITY_NOT_OBSERVED")
            CYCLE_CONTEXT["hosts"].append(daemon.pid)
            cycle_record(EVIDENCE / "first-host.started.original.json", {"pid":daemon.pid,
                "config_sha256":digest(config_path),"daemon_sha256":digest(SW_BINS / "semwrightd")})
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

            if admitted is not None:
                n83_remaining_stages(database, connection_path, env, admitted)
                return  # owner context seals only after the shared daemon finally

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
            direct = (cycle_direct_film if CYCLE_CONTEXT is not None else run_direct_native_film)(
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

            if CYCLE_CONTEXT is not None:
                original_cycle_row = cycle_capture("original", paths=paths, socket=socket,
                    session=session, env=env, artifact=direct["artifact"], job=direct["job_ref"])

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
                            "reported_caption_stage", "reported_caption_native_class",
                            "reported_caption_verification_gate",
                        )
                        if key in failure.diagnostic
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
                    if CYCLE_CONTEXT is not None:
                        if not cycle_stop(first_host):
                            raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_HOST_CLOSURE_UNKNOWN")
                    elif daemon.poll() is None:
                        pid = daemon.pid
                        daemon.terminate()
                        try:
                            daemon.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(pid, signal.SIGKILL)
                            daemon.wait(timeout=5)
                finally:
                    log_stream.close()

        if CYCLE_CONTEXT is not None:
            if not cycle_stop(first_host):
                raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_FIRST_HOST_SHUTDOWN_UNKNOWN")
            cycle_record(EVIDENCE / "first-host.terminal.original.json", {"pid":daemon.pid,
                "exit_code":daemon.returncode,"group_quiescent":True})
            cycle_remaining(database, paths, config_path, env, original_cycle_row)

    if CYCLE_CONTEXT is not None:
        return
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
    if NATIVE_SCOPE == "editable-source-cycle":
        try:
            if sys.argv[1:] == ["--cycle-admission-only"]:
                cycle_admission()
            elif sys.argv[1:] == ["--cycle-seal-preflight-failure"]:
                cycle_seal_preflight_failure()
            elif sys.argv[1:]:
                raise N83RunnerFailure("EDITABLE_SOURCE_CYCLE_UNKNOWN_ARGUMENT")
            else:
                main()
        except BaseException:
            print(json.dumps({"selection":"editable-source-cycle","status":"NOT_VERIFIED",
                "automatic_retry":False,"raw_diagnostics_public":False}),flush=True)
            raise SystemExit(1) from None
        raise SystemExit(0)
    if NATIVE_SCOPE == "n83-preview":
        try:
            if sys.argv[1:] == ["--n83-admission-only"]:
                n83_admission()
                print(json.dumps({"schema":"motionwright.n83.admission/1","status":"SOURCE_ONLY_ADMITTED","native_execution":False}))
            elif sys.argv[1:] == ["--n83-seal-preflight-failure"]:
                n83_seal_preflight_failure()
            elif sys.argv[1:]:
                raise N83RunnerFailure("N83_UNKNOWN_ARGUMENT")
            else:
                main()
        except BaseException:
            print(json.dumps({"schema":"motionwright.n83.runner/1","status":"NOT_VERIFIED","automatic_retry":False,"raw_diagnostics_public":False}), flush=True)
            raise SystemExit(1) from None
    else:
        main()
