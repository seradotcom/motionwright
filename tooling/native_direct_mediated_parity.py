"""GitHub-only direct-driver vs Motionwright-mediated native output conformance.

The direct leg still uses the pinned Semwright Broker/Driver Host, never a
second renderer. Both legs receive the *same* application-owned canonical Film.
Only the comparison is reusable outside CI; the direct executable path is
limited to a disposable GitHub Actions fixture.

A direct second render is an explicitly separate comparison operation, NOT
reconciliation or retry of an uncertain previously started native job.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_MANIFEST = 2 * 1024 * 1024
MAX_FRAME_BYTES = 16 * 1024 * 1024
DRIVER = "driver:motion-canvas"
STATES_IN_PROGRESS = frozenset({"queued", "starting", "rendering"})
COMMANDS = frozenset({
    "driver.motion-canvas.composition.plan",
    "driver.motion-canvas.composition.apply",
    "driver.motion-canvas.render.start",
    "driver.motion-canvas.render.status",
    "driver.motion-canvas.render.result",
    "driver.motion-canvas.composition.verify",
})


class DirectParityError(ValueError):
    """Safe failure: do not include raw provider, source, or private job text."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def native_png_hashes(artifact_root: Path, expected_frames: int = 60) -> list[str]:
    """Validate native driver manifests *and actual bytes*, no advertised-only claims."""
    if not (1 <= expected_frames <= 120):
        raise DirectParityError("Native parity frame budget is invalid")
    if artifact_root.is_symlink() or not artifact_root.is_dir():
        raise DirectParityError("Native parity artifact root is missing or symlinked")
    frame_root = artifact_root / "frames"
    if frame_root.is_symlink() or not frame_root.is_dir():
        raise DirectParityError("Native parity frame folder is missing or symlinked")
    manifest = artifact_root / "artifact-manifest.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise DirectParityError("Native parity manifest is missing or symlinked")
    if not 0 < manifest.stat().st_size <= MAX_MANIFEST:
        raise DirectParityError("Native parity manifest violates the size budget")
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise DirectParityError("Native parity manifest is malformed") from error
    if not isinstance(value, dict):
        raise DirectParityError("Native parity manifest must be an object")
    plan = value.get("plan")
    frames = value.get("frames")
    if not isinstance(plan, dict) or not isinstance(frames, list):
        raise DirectParityError("Native parity manifest has no frame plan")
    if (
        plan.get("first_frame") != 0
        or plan.get("end_frame_exclusive") != expected_frames
        or plan.get("frame_count") != expected_frames
        or len(frames) != expected_frames
    ):
        raise DirectParityError("Native parity frame timeline does not match the source")
    hashes: list[str] = []
    for index, row in enumerate(frames):
        relative = f"frames/{index:06}.png"
        if not isinstance(row, dict) or row.get("file") != relative or row.get("index") != index:
            raise DirectParityError("Native parity frame index/path is inconsistent")
        advertised_sha = row.get("sha256")
        bytes_claimed = row.get("bytes")
        if not isinstance(advertised_sha, str) or not HEX_SHA256.fullmatch(advertised_sha):
            raise DirectParityError("Native parity frame digest is malformed")
        if type(bytes_claimed) is not int or not (len(PNG_MAGIC) < bytes_claimed <= MAX_FRAME_BYTES):
            raise DirectParityError("Native parity frame byte budget is invalid")
        path = frame_root / f"{index:06}.png"
        if path.is_symlink() or not path.is_file() or path.stat().st_size != bytes_claimed:
            raise DirectParityError("Native parity PNG byte length or path changed")
        with path.open("rb") as stream:
            if stream.read(len(PNG_MAGIC)) != PNG_MAGIC:
                raise DirectParityError("Native parity frame does not have a PNG header")
        actual_sha = sha256_path(path)
        if actual_sha != advertised_sha:
            raise DirectParityError("Native parity PNG is not bound to its native manifest")
        hashes.append(actual_sha)
    return hashes


def compare_native_pngs(
    mediated: Path, direct: Path, expected_frames: int = 60,
) -> dict[str, Any]:
    mediated_sha = native_png_hashes(mediated, expected_frames)
    direct_sha = native_png_hashes(direct, expected_frames)
    mismatches = [index for index, (a, b) in enumerate(zip(mediated_sha, direct_sha)) if a != b]
    if mismatches:
        # Neither path/filename nor private native job references are disclosed.
        raise DirectParityError(
            f"Direct and mediated native pixels differ at {len(mismatches)} of "
            f"{expected_frames} exact source-bound frames"
        )
    if len(mediated_sha) != expected_frames or len(direct_sha) != expected_frames:
        raise DirectParityError("Native parity frame comparison is incomplete")
    return {
        "frame_count": expected_frames,
        "every_frame_byte_identical": True,
        "ordered_frame_digests_sha256": hashlib.sha256(
            "".join(mediated_sha).encode("ascii")
        ).hexdigest(),
        "checked_frames": [0, 15, 30, expected_frames - 1],
        "checked_frame_sha256": [
            mediated_sha[index] for index in [0, 15, 30, expected_frames - 1]
        ],
    }


def checked_direct_artifact(root: Path, artifact: dict[str, Any]) -> Path:
    """Reject absolute, traversing or symlinked native artifact routes."""
    directory = artifact.get("directory")
    if not isinstance(directory, str) or not 0 < len(directory) <= 512:
        raise DirectParityError("Direct native artifact has no bounded directory")
    relative = Path(directory)
    if relative.is_absolute() or any(part in {"..", "."} for part in relative.parts):
        raise DirectParityError("Direct native artifact escapes its owner output root")
    candidate = root.joinpath(relative)
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise DirectParityError("Direct native artifact is outside the owner output root")
    return candidate


def _execute_direct(
    *, executable: Path, socket: Path, session: Path,
    env: dict[str, str], command: str, args: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Exact allowlisted Semwright CLI/Broker invocation, not direct shell control."""
    if command not in COMMANDS:
        raise DirectParityError("Direct parity attempted an unauthorized native command")
    encoded = json.dumps(args, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
        raise DirectParityError("Direct parity native argument budget exceeded")
    argv = [
        str(executable), "--socket", str(socket),
        "--session-file", str(session), "--json",
        "execute", command, "--args-json", encoded,
    ]
    try:
        result = subprocess.run(
            argv, stdin=subprocess.DEVNULL, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        # A timed-out CLI may leave a mutation outcome UNKNOWN. No retry or
        # replacement job identity is ever generated in this comparison.
        raise DirectParityError(
            "Direct native Broker interaction unavailable or timed out; "
            "operation outcome must be reconciled, not redispatched"
        ) from error
    if result.returncode != 0 or not 0 < len(result.stdout) <= 4 * 1024 * 1024:
        raise DirectParityError(
            "Direct native Broker command did not complete with a bounded response; "
            "no mutation retry is authorized"
        )
    try:
        receipt = json.loads(result.stdout)
    except ValueError as error:
        raise DirectParityError("Direct native Broker envelope is not valid JSON") from error
    if not isinstance(receipt, dict) or receipt.get("ok") is not True:
        raise DirectParityError("Direct native Broker command has no successful receipt")
    if receipt.get("command") != command:
        raise DirectParityError("Direct native Broker receipt command mismatch")
    execution = receipt.get("execution")
    if not isinstance(execution, dict) or execution.get("backend") != DRIVER:
        raise DirectParityError("Direct native execution is not bound to Motion Canvas")
    provenance = execution.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("provider") != DRIVER:
        raise DirectParityError("Direct native execution has no matching provider")
    descriptor_sha = provenance.get("descriptor_sha256")
    generation = provenance.get("provider_generation")
    if (
        provenance.get("source") != "driver"
        or not isinstance(descriptor_sha, str)
        or not HEX_SHA256.fullmatch(descriptor_sha)
        or type(generation) is not int
        or generation <= 0
    ):
        raise DirectParityError("Direct native provider generation/source digest is unverified")
    data = receipt.get("data")
    if not isinstance(data, dict):
        raise DirectParityError("Direct native Broker returned no canonical data")
    return data


def _require_job(data: dict[str, Any], acknowledged_job: str) -> None:
    if data.get("job_ref") != acknowledged_job:
        raise DirectParityError(
            "Direct native status/result does not belong to its acknowledged job"
        )


def run_direct_native_film(
    *, film_file: Path, executable: Path, socket: Path, session: Path,
    env: dict[str, str], mediated_job_ref: str,
    expected_frames: int = 60,
) -> dict[str, Any]:
    """A distinct authorized direct-driver job using the exact mediated Film.

    The second *new* render is a CI comparison operation, never a retry of the
    previously accepted mediated job. A failure is terminal/unknown for this
    test and does not generate another render.start.
    """
    if env.get("GITHUB_ACTIONS") != "true":
        raise DirectParityError("Direct native execution is disabled outside GitHub Actions")
    if expected_frames != 60:
        raise DirectParityError("Direct Film comparison is restricted to 60 source frames")
    if film_file.is_symlink() or not film_file.is_file():
        raise DirectParityError("Direct Film source is absent or symlinked")
    if not 0 < film_file.stat().st_size <= 2 * 1024 * 1024:
        raise DirectParityError("Direct Film source exceeds the controlled fixture budget")
    if not isinstance(mediated_job_ref, str) or not mediated_job_ref:
        raise DirectParityError("No previously acknowledged mediated native job was supplied")
    try:
        film = json.loads(film_file.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise DirectParityError("Direct Film source JSON is invalid") from error
    if not isinstance(film, dict):
        raise DirectParityError("Direct Film source is not an object")

    def execute(command_suffix: str, args: dict[str, Any], timeout_seconds: int = 90):
        return _execute_direct(
            executable=executable, socket=socket, session=session, env=env,
            command=f"driver.motion-canvas.{command_suffix}", args=args,
            timeout_seconds=timeout_seconds,
        )

    planned = execute("composition.plan", {
        "film": film,
        "budget": {
            "max_iterations": 8, "max_operations": 4096,
            "max_findings": 1024, "max_observations": 128,
            "max_elapsed_ms": 120000,
        },
    }, 150)
    plan_ref = planned.get("plan_ref")
    if not isinstance(plan_ref, str) or not plan_ref:
        raise DirectParityError("Direct Film plan has no native reference")
    applied = execute("composition.apply", {"plan_ref": plan_ref, "dry_run": False}, 150)
    fingerprint = applied.get("fingerprint")
    if (
        applied.get("applied") is not True
        or applied.get("execution_status") != "completed"
        or not isinstance(fingerprint, str)
        or not HEX_SHA256.fullmatch(fingerprint)
    ):
        raise DirectParityError("Direct Film apply is not canonically completed")
    started = execute("render.start", {
        "expected_fingerprint": fingerprint,
        "profile": {
            "first_frame": 0, "end_frame_exclusive": expected_frames,
            "scale": "full", "transparent": False, "timeout_ms": 300_000,
        },
    }, 120)
    acknowledged_job = started.get("job_ref")
    if not isinstance(acknowledged_job, str) or not acknowledged_job:
        raise DirectParityError("Direct native render has no acknowledged job reference")
    if acknowledged_job == mediated_job_ref:
        raise DirectParityError("Direct comparison unexpectedly reused the mediated render job")
    deadline = time.monotonic() + 330
    poll_count = 0
    terminal: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        poll_count += 1
        try:
            status = execute("render.status", {"job_ref": acknowledged_job}, 45)
        except DirectParityError as error:
            raise DirectParityError(
                "Direct render status became unobservable; the original job is UNKNOWN; "
                "no new render.start is allowed"
            ) from error
        _require_job(status, acknowledged_job)
        state = status.get("state")
        if state == "succeeded":
            terminal = status
            break
        if state not in STATES_IN_PROGRESS:
            raise DirectParityError(
                "Direct native render has no confirmed successful terminal state"
            )
        time.sleep(1)
    if terminal is None:
        raise DirectParityError(
            "Direct native render deadline expired; job outcome UNKNOWN, no redispatch"
        )
    if not isinstance(terminal.get("artifact"), dict) or terminal["artifact"].get("frame_count") != expected_frames:
        raise DirectParityError("Direct terminal render artifact has mismatched frame count")
    completed = execute("render.result", {"job_ref": acknowledged_job}, 90)
    _require_job(completed, acknowledged_job)
    artifact = completed.get("artifact")
    if (
        completed.get("state") != "succeeded"
        or not isinstance(artifact, dict)
        or artifact.get("frame_count") != expected_frames
    ):
        raise DirectParityError("Direct render.result is not a complete source-bound native artifact")
    verified = execute("composition.verify", {
        "plan_ref": plan_ref, "job_ref": acknowledged_job,
    }, 150)
    report = verified.get("report")
    measurement = verified.get("measurement")
    validation = measurement.get("validation") if isinstance(measurement, dict) else None
    checks = validation.get("checks") if isinstance(validation, dict) else None
    if (
        not isinstance(report, dict)
        or report.get("execution_status") != "completed"
        or report.get("support_level") != "native"
        or not isinstance(measurement, dict)
        or measurement.get("findings") != []
        or not isinstance(checks, list)
        or not checks
        or any(not isinstance(check, dict) or check.get("verdict") != "PASS" for check in checks)
    ):
        raise DirectParityError("Direct Film native verification did not pass")
    return {
        "artifact": artifact,
        "film_sha256": sha256_path(film_file),
        "status_reads": poll_count,
        "independent_job_confirmed": True,
        "native_validation_checks": len(checks),
        "scope": "ci_direct_broker_vs_motionwright_mediated_same_film",
    }
