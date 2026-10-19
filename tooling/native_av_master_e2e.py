#!/usr/bin/env python3
"""Exact-SHA Motionwright -> Semwright -> Motion Canvas -> MLT AV acceptance.

CI-only by design. The test creates application-owned Motionwright state, provisions
the exact Motion Canvas and MLT drivers through Semwright's Broker/Driver Host,
renders the revision, encodes its verified frames to FFV1 and closes an H.264/AAC
master through the bounded MLT AV operations. No product shell-out is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from contextlib import contextmanager

from native_av_media_probe import probe_native_mp4, write_two_channel_tone_wav
from native_provider_lifecycle import ProviderLifecycleSampler, summarize_render_receipts
from native_provider_loss_correlation import inspect_provider_lifetime
from native_provider_broker_events import read_owner_broker_event_history
from native_provider_live_watch import PassiveLiveBrokerWatch, public_lifecycle_kind_counts
from native_av_failure_guard import (
    NativeCommandFailure, run_sanitized_json,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = json.loads((ROOT / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
PIN = SOURCE_LOCK["dependencies"]["semwright"]["revision"]
SEMWRIGHT = Path(os.environ.get("SEMWRIGHT_CHECKOUT", ROOT / "_semwright")).resolve()
SW_BINS = SEMWRIGHT / "target" / "debug"
MW_BIN = ROOT / "target" / "debug" / "examples" / "native-render-e2e"
RUNTIME = SEMWRIGHT / "integrations" / "motion-canvas" / "runtime"
GITHUB_SHA = os.environ.get("GITHUB_SHA", "unknown")
FIXTURE = os.environ.get("MOTIONWRIGHT_E2E_FIXTURE", "baseline")
HERO_ASPECTS = {"landscape": (1920, 1080), "portrait": (1080, 1920), "square": (1080, 1080),
                "split-landscape": (1920, 1080), "split-portrait": (1080, 1920),
                "split-square": (1080, 1080),
                "metric-landscape": (1920, 1080), "metric-portrait": (1080, 1920),
                "metric-square": (1080, 1080),
                "step-landscape": (1920, 1080), "step-portrait": (1080, 1920),
                "step-square": (1080, 1080),
                "contrast-landscape": (1920, 1080),
                "contrast-portrait": (1080, 1920),
                "contrast-square": (1080, 1080),
                "quote-landscape": (1920, 1080),
                "quote-portrait": (1080, 1920),
                "quote-square": (1080, 1080)}
STAGE_MODES = {"stage-browser", "stage-handset"}
PROCEDURAL_MODES = {"procedural-static", "procedural-motion", "procedural-orbit"}
IS_STAGE = FIXTURE in STAGE_MODES
IS_CAMERA = FIXTURE == "camera-reframe"
IS_MEASURED = FIXTURE == "measured-data"
IS_COMPOSITION = FIXTURE == "composition-stack"
IS_PROCEDURAL = FIXTURE in PROCEDURAL_MODES
IS_HERO = FIXTURE in HERO_ASPECTS
if FIXTURE not in {"baseline", "measured-data", "camera-reframe", "composition-stack", *HERO_ASPECTS, *STAGE_MODES, *PROCEDURAL_MODES}:
    raise SystemExit("unknown bounded E2E fixture")
EXPECTED_FRAMES = 540 if IS_COMPOSITION else 180 if IS_HERO or IS_STAGE or IS_MEASURED or IS_PROCEDURAL or IS_CAMERA else 60
EXPECTED_SIZE = HERO_ASPECTS[FIXTURE] if IS_HERO else (1920, 1080)
EVIDENCE = ROOT / "verification" / (
    "composition-native-e2e" if IS_COMPOSITION else
    "camera-reframe-e2e" if IS_CAMERA else
    "product-stage-e2e" if IS_STAGE else
    "measured-data-native-e2e" if IS_MEASURED else
    "procedural-field-e2e" if IS_PROCEDURAL else
    "product-hero-e2e" if IS_HERO else "native-av-master-e2e"
) / GITHUB_SHA
if IS_HERO or IS_STAGE or IS_MEASURED or IS_PROCEDURAL or IS_CAMERA or IS_COMPOSITION:
    EVIDENCE = EVIDENCE / FIXTURE


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
            # The daemon log may contain connection paths or owner tickets;
            # never paste its bytes into a public GitHub CI assertion.
            raise AssertionError(
                "Pinned owner Broker exited before exposing its socket; "
                "private daemon log suppressed"
            )
        if socket.is_socket():
            return
        time.sleep(0.05)
    raise AssertionError("semwrightd did not expose the owner socket")



class NativeObservationFailure(AssertionError):
    """A required CI observation failed; never expose its private exception text."""


def _observation_error(phase: str, error: BaseException) -> dict:
    phases = {
        "render_diagnostic", "failure_files", "receipt_reconciliation", "broker_live_watch",
        "broker_history", "provider_lifecycle", "provider_correlation", "broker_cleanup", "workspace_cleanup",
    }
    phase = phase if phase in phases else "observation"
    known = (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.TimeoutExpired)
    category = next((kind.__name__ for kind in known if isinstance(error, kind)), "Exception")
    return {"phase": phase, "error_class": category, "status": "NOT_CAPTURED"}


def _report_observation_failure(label: str, failures) -> None:
    # A broken diagnostic sink is secondary to the original native exception.
    try:
        print(label + "=" + json.dumps(failures, sort_keys=True))
    except BaseException:
        pass


def finalize_native_observation(*, primary_error, finalizers, cleanup, cleanup_phase="broker_cleanup") -> list[dict]:
    """Attempt each registered finalizer and cleanup once without replacing a primary error."""
    failures = []
    try:
        for phase, finalize in finalizers:
            try:
                finalize()
            except BaseException as error:
                failures.append(_observation_error(phase, error))
    finally:
        try:
            cleanup()
        except BaseException as error:
            failures.append(_observation_error(cleanup_phase, error))
    if failures:
        # Closed phase/class categories only; no stderr, argv or opaque native identity.
        _report_observation_failure("native-observation-finalization", failures)
        if primary_error is None:
            raise NativeObservationFailure("Native observation finalization did not complete") from None
    return failures


@contextmanager
def _native_workspace():
    temporary = tempfile.TemporaryDirectory(prefix="motionwright-native-av-")
    primary_error = None
    try:
        yield temporary.name
    except BaseException as error:
        primary_error = error
        raise
    finally:
        finalize_native_observation(
            primary_error=primary_error, finalizers=[], cleanup=temporary.cleanup,
            cleanup_phase="workspace_cleanup",
        )


def _file_identity(value) -> dict:
    return {
        "device": value.st_dev, "inode": value.st_ino, "bytes": value.st_size,
        "mtime_ns": value.st_mtime_ns, "ctime_ns": value.st_ctime_ns,
    }


def _no_link(value) -> bool:
    return not stat.S_ISLNK(value.st_mode) and not (
        getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _contained_file_identity(root: Path, source: Path) -> tuple[dict, list[tuple[int, int]]]:
    root = root.absolute()
    source = source.absolute()
    relative = source.relative_to(root)
    if ".." in relative.parts:
        raise ValueError("OUTSIDE_ROOT")
    parents = [root]
    for part in relative.parts[:-1]:
        parents.append(parents[-1] / part)
    identity = []
    for parent in parents:
        value = parent.lstat()
        if not _no_link(value) or not stat.S_ISDIR(value.st_mode):
            raise ValueError("SYMLINK_OR_REPARSE_POINT")
        identity.append((value.st_dev, value.st_ino))
    value = source.lstat()
    if not _no_link(value) or value.st_nlink != 1:
        raise ValueError("SYMLINK_REPARSE_OR_HARDLINK")
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("NOT_REGULAR")
    return _file_identity(value), identity


def copy_native_failure_file(root: Path, source: Path, destination: Path, *, byte_limit: int) -> dict:
    """Copy bounded regular bytes with handle/path identity and two source reads; no decoding."""
    result = {"capture_status": "NOT_CAPTURED", "omission_reason": None}
    created_identity = None
    try:
        relative = source.absolute().relative_to(root.absolute())
        if ".." in relative.parts:
            raise ValueError("OUTSIDE_ROOT")
        result["source_relative_path"] = relative.as_posix()
        before, parents = _contained_file_identity(root, source)
        if not 0 < before["bytes"] <= byte_limit:
            raise ValueError("EMPTY_OR_SIZE_LIMIT")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(source, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not _no_link(opened) or not stat.S_ISREG(opened.st_mode) or _file_identity(opened) != before:
                raise ValueError("SOURCE_CHANGED")
            data = stream.read(byte_limit + 1)
            if len(data) != before["bytes"]:
                raise ValueError("SOURCE_CHANGED")
            stream.seek(0)
            if stream.read(byte_limit + 1) != data or _file_identity(os.fstat(stream.fileno())) != before:
                raise ValueError("SOURCE_CHANGED")
            after, after_parents = _contained_file_identity(root, source)
            if after != before or after_parents != parents:
                raise ValueError("SOURCE_CHANGED")
        # The caller owns this private directory. Exclusive creation never overwrites evidence.
        destination_parent = destination.parent
        parent_value = destination_parent.lstat()
        if not _no_link(parent_value) or not stat.S_ISDIR(parent_value.st_mode):
            raise ValueError("DESTINATION_NOT_PRIVATE_DIRECTORY")
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            created_identity = (os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        copied, copied_parents = _contained_file_identity(destination_parent, destination)
        if (copied["device"], copied["inode"]) != created_identity or copied_parents != [(parent_value.st_dev, parent_value.st_ino)]:
            raise ValueError("DESTINATION_CHANGED")
        # Verify the copy through a no-follow descriptor as well.
        with os.fdopen(os.open(destination, flags), "rb") as stream:
            if _file_identity(os.fstat(stream.fileno())) != copied or stream.read(byte_limit + 1) != data:
                raise ValueError("DESTINATION_CHANGED")
        if _contained_file_identity(destination_parent, destination) != (copied, copied_parents):
            raise ValueError("DESTINATION_CHANGED")
        final, final_parents = _contained_file_identity(root, source)
        if final != before or final_parents != parents:
            raise ValueError("SOURCE_CHANGED")
        result.update(
            capture_status="CAPTURED_UNVERIFIED", source_identity=before,
            source_identity_after=final, source_sha256=hashlib.sha256(data).hexdigest(),
            copy_sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
            destination_name=destination.name, copy_identity=copied,
        )
    except FileNotFoundError:
        result["omission_reason"] = "MISSING"
    except FileExistsError:
        result["omission_reason"] = "DESTINATION_EXISTS"
    except ValueError as error:
        allowed = {
            "OUTSIDE_ROOT", "SYMLINK_OR_REPARSE_POINT", "SYMLINK_REPARSE_OR_HARDLINK",
            "NOT_REGULAR", "EMPTY_OR_SIZE_LIMIT", "SOURCE_CHANGED", "DESTINATION_CHANGED",
            "DESTINATION_NOT_PRIVATE_DIRECTORY",
        }
        result["omission_reason"] = str(error) if str(error) in allowed else "OUTSIDE_ROOT"
    except OSError:
        result["omission_reason"] = "IO_ERROR"
    if result["capture_status"] != "CAPTURED_UNVERIFIED" and created_identity is not None:
        # Remove only the same file this call created; never delete a raced replacement.
        try:
            value = destination.lstat()
            if _no_link(value) and (value.st_dev, value.st_ino) == created_identity:
                destination.unlink()
        except OSError:
            pass
    return result


def preserve_native_failure_evidence(output_root: Path, *, native_outcome: str) -> dict:
    """Retain bounded private originals before sandbox cleanup, including baseline/composition."""
    names = {
        "native-observations.ndjson": 8 * 1024 * 1024,
        "native-observations-receipt.json": 1024 * 1024,
        "artifact-manifest.json": 1024 * 1024,
    }
    receipt = {
        "schema": "motionwright.ci-native-failure-custody/1",
        "motionwright_source_sha": GITHUB_SHA, "semwright_source_pin": PIN, "fixture": FIXTURE,
        "native_outcome": native_outcome if native_outcome in {"UNKNOWN", "FAILED_KNOWN"} else "NOT_PROVEN",
        "capture_phase": "BEFORE_TEMPORARY_OUTPUT_CLEANUP",
        "retention_scope": "RUNNER_LOCAL_ONLY", "driver_host_exit_evidence": "NOT_OBSERVED",
        "media_decoded": False, "terminal_native_result_verified": False,
        "original_render_retry_permitted": False, "files": [], "discovery_omissions": [],
        "limits": {"directory_depth": 6, "directory_entries": 36064, "files": 32, "total_bytes": 96 * 1024 * 1024},
    }
    known_files, frames, pending = [], [], [(output_root, 0)]
    seen = 0
    while pending:
        directory, depth = pending.pop()
        try:
            value = directory.lstat()
            if not _no_link(value) or not stat.S_ISDIR(value.st_mode):
                receipt["discovery_omissions"].append("SYMLINK_REPARSE_OR_NON_DIRECTORY")
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    seen += 1
                    if seen > receipt["limits"]["directory_entries"]:
                        raise OverflowError
                    path = Path(entry.path)
                    if entry.name in names:
                        known_files.append(path)
                    elif path.parent.name == "frames" and path.suffix == ".png":
                        frames.append(path)
                    elif entry.is_dir(follow_symlinks=False):
                        if depth < receipt["limits"]["directory_depth"]:
                            pending.append((path, depth + 1))
                        else:
                            receipt["discovery_omissions"].append("DIRECTORY_DEPTH_LIMIT")
                    elif entry.is_symlink():
                        receipt["discovery_omissions"].append("SYMLINK_NOT_TRAVERSED")
        except OverflowError:
            receipt["discovery_omissions"].append("DIRECTORY_ENTRY_LIMIT")
            break
        except OSError:
            receipt["discovery_omissions"].append("DIRECTORY_UNAVAILABLE")
    destination_root = EVIDENCE / "native-failure-originals"
    destination_root.mkdir(mode=0o700, exist_ok=False)
    selected = [
        (path, destination_root / f"{index:03d}-{path.name}", names[path.name])
        for index, path in enumerate(sorted(known_files))
    ]
    selected.extend(
        (path, EVIDENCE / f"UNVERIFIED-native-frame-{index:06d}.png", 16 * 1024 * 1024)
        for index, path in enumerate(sorted(frames)) if index in (0, 20, 44, 60, 179)
    )
    remaining = receipt["limits"]["total_bytes"]
    for index, (source, destination, limit) in enumerate(selected):
        if index >= receipt["limits"]["files"] or remaining <= 0:
            receipt["files"].append({"capture_status": "NOT_CAPTURED", "omission_reason": "FILE_OR_TOTAL_BYTE_LIMIT"})
            break
        item = copy_native_failure_file(output_root, source, destination, byte_limit=min(limit, remaining))
        receipt["files"].append(item)
        remaining -= item.get("bytes", 0)
    for name in names:
        if not any(path.name == name for path in known_files):
            receipt["files"].append({"source_name": name, "capture_status": "NOT_CAPTURED", "omission_reason": "NOT_FOUND"})
    if not frames:
        receipt["files"].append({"source_kind": "diagnostic_frame", "capture_status": "NOT_CAPTURED", "omission_reason": "NOT_FOUND"})
    receipt["discovery_omissions"] = sorted(set(receipt["discovery_omissions"]))
    statuses = [item["capture_status"] for item in receipt["files"]]
    receipt["capture_status"] = (
        "CAPTURED_UNVERIFIED" if statuses and all(value == "CAPTURED_UNVERIFIED" for value in statuses)
        and not receipt["discovery_omissions"] else
        "PARTIAL_UNVERIFIED" if "CAPTURED_UNVERIFIED" in statuses else "NOT_CAPTURED"
    )
    write_private_json(EVIDENCE / "native-failure-files.json", receipt)
    return receipt


def retain_render_failure(error: BaseException, output_root: Path) -> None:
    """Optional custody must preserve the same primary exception and must not issue commands."""
    outcome = error.diagnostic.get("source_outcome") if isinstance(error, NativeCommandFailure) else (
        "UNKNOWN" if isinstance(error, subprocess.TimeoutExpired) else "NOT_PROVEN"
    )
    if isinstance(error, NativeCommandFailure):
        try:
            write_private_json(EVIDENCE / "native-render-failure-diagnostic.json", {
                **error.diagnostic, "motionwright_source_sha": GITHUB_SHA,
                "semwright_source_pin": PIN, "fixture": FIXTURE,
            })
        except BaseException as secondary:
            _report_observation_failure("native-render-failure-diagnostic", _observation_error("render_diagnostic", secondary))
    try:
        preserve_native_failure_evidence(output_root, native_outcome=outcome)
    except BaseException as secondary:
        _report_observation_failure("native-failure-files", _observation_error("failure_files", secondary))


def _finalize_native_session(*, primary_error, lifecycle, live_broker_watch, live_watch_started,
                             daemon, socket, session, env, log_stream) -> None:
    reconciliation = None
    completed_lifecycle = None

    def reconcile_receipts():
        nonlocal reconciliation
        diagnostic = EVIDENCE / "motionwright-render-diagnostic.json"
        if diagnostic.is_file() and 0 < diagnostic.stat().st_size <= 2 * 1024 * 1024:
            try:
                incident = json.loads(diagnostic.read_text(encoding="utf-8"))
                reconciliation = summarize_render_receipts(incident.get("receipts"))
                write_private_json(EVIDENCE / "provider-status-reconciliation.json", reconciliation)
            except (OSError, ValueError, TypeError, UnicodeError) as error:
                write_private_json(EVIDENCE / "provider-status-reconciliation.json", {
                    "schema": "motionwright-ci-render-status-reconciliation/1",
                    "render_outcome": "not_proven",
                    "diagnostic_parse_error_class": _observation_error("receipt_reconciliation", error)["error_class"],
                })
                raise

    def finish_live_watch():
        if live_broker_watch is not None and live_watch_started:
            live_events = live_broker_watch.finish()
            live_events["motionwright_source_sha"] = GITHUB_SHA
            live_events["semwright_source_pin"] = PIN
            write_private_json(EVIDENCE / "native-provider-live-broker-events.json", live_events)
            safe_counts = public_lifecycle_kind_counts(live_events)
            print(
                "v01-provider-live-broker-events=" + live_events["observation"]
                + " counts=" + json.dumps(safe_counts, sort_keys=True)
                + " early_exit=" + str(live_events["subscriber_exited_before_finalize"])
            )

    def retain_broker_history():
        if FIXTURE == "baseline" and os.environ.get("MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY") == "1":
            if daemon is not None and daemon.poll() is None and socket.is_socket():
                broker_events = read_owner_broker_event_history(SW_BINS / "semwright", socket, session, env=env)
            else:
                broker_events = {
                    "schema": "motionwright-ci-native-provider-broker-events/1",
                    "observation": "BROKER_UNAVAILABLE_BEFORE_READ_ONLY_REPLAY",
                    "trusted_lifecycle_event_count": 0, "events": [],
                    "provider_generation_root_cause": "NOT_PROVEN",
                    "original_render_retry_permitted": False,
                    "terminal_native_result_verified": False, "read_only_broker_replay": False,
                    "installed_owner_recovery": "NOT_RUN", "public_owner_payloads_copied": False,
                }
            broker_events["motionwright_source_sha"] = GITHUB_SHA
            broker_events["semwright_source_pin"] = PIN
            write_private_json(EVIDENCE / "native-provider-broker-events.json", broker_events)
            kinds = [
                "provider.connected", "provider.disconnected", "provider.capabilities.changed",
                "provider.generation.invalidated", "provider.removed",
            ]
            safe_counts = {kind: sum(item["kind"] == kind for item in broker_events["events"]) for kind in kinds}
            print("v01-provider-broker-events=" + broker_events["observation"] + " counts=" + json.dumps(safe_counts, sort_keys=True))

    def finish_lifecycle():
        nonlocal completed_lifecycle
        if lifecycle is not None:
            completed_lifecycle = lifecycle.stop()

    def correlate_lifetime():
        if completed_lifecycle is not None:
            correlation = inspect_provider_lifetime(completed_lifecycle, reconciliation)
            correlation["motionwright_source_sha"] = GITHUB_SHA
            correlation["semwright_source_pin"] = PIN
            write_private_json(EVIDENCE / "native-provider-loss-correlation.json", correlation)

    def cleanup_broker():
        try:
            if daemon is not None and daemon.poll() is None:
                pid = daemon.pid
                daemon.terminate()
                try:
                    daemon.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(pid, signal.SIGKILL)
                    daemon.wait(timeout=5)
        finally:
            log_stream.close()

    # Every observer is attempted before intentional shutdown, even if an earlier
    # optional observation failed. No render.start, effect grant or retry occurs here.
    finalize_native_observation(
        primary_error=primary_error,
        finalizers=[
            ("receipt_reconciliation", reconcile_receipts),
            ("broker_live_watch", finish_live_watch),
            ("broker_history", retain_broker_history),
            ("provider_lifecycle", finish_lifecycle),
            ("provider_correlation", correlate_lifetime),
        ],
        cleanup=cleanup_broker,
    )

def main() -> None:
    exact_inputs()
    EVIDENCE.mkdir(parents=True, exist_ok=False)

    with _native_workspace() as raw:
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
        seed_command = [str(MW_BIN), "seed", str(database)]
        if IS_HERO:
            seed_command = [str(MW_BIN), "seed-hero", str(database), FIXTURE, str(EVIDENCE / "editable-project.json")]
        elif IS_STAGE:
            seed_command = [str(MW_BIN), "seed-stage", str(database), FIXTURE.removeprefix("stage-"), str(EVIDENCE / "editable-project.json")]
        elif IS_CAMERA:
            seed_command = [str(MW_BIN), "seed-stage", str(database), "camera", str(EVIDENCE / "editable-project.json")]
        elif IS_MEASURED:
            seed_command = [str(MW_BIN), "seed-measured", str(database), str(EVIDENCE / "editable-project.json")]
        elif IS_PROCEDURAL:
            mode = FIXTURE.removeprefix("procedural-")
            seed_command = [str(MW_BIN), "seed-procedural", str(database), mode, str(EVIDENCE / "editable-project.json")]
        elif IS_COMPOSITION:
            seed_command = [str(MW_BIN), "seed-composition", str(database), str(EVIDENCE / "editable-project.json")]
        seeded = run_json(seed_command, env=env)
        if (seeded["width"], seeded["height"]) != EXPECTED_SIZE:
            raise AssertionError(f"unexpected master profile: {seeded}")
        expected_family = ("contrast-comparison/1" if FIXTURE.startswith("contrast-") else
                           "editorial-quote/1" if FIXTURE.startswith("quote-") else
                           "metric-evidence/1" if FIXTURE.startswith("metric-") else
                           "split-explanation/1" if FIXTURE.startswith("split-") else
                           "step-sequence/1" if FIXTURE.startswith("step-") else
                           "product-hero-reveal/1")
        if IS_HERO and seeded.get("fixture") != expected_family:
            raise AssertionError("Native composition fixture does not match the requested semantic family")
        if IS_STAGE and (seeded.get("fixture") != "product-stage-original/1"
                         or seeded.get("stage_frame") != FIXTURE.removeprefix("stage-")
                         or seeded.get("real_product_capture") is not False):
            raise AssertionError("ProductStage native fixture must be an original generic graphic, not a capture")
        if IS_CAMERA and (seeded.get("fixture") != "camera-reframe/1"
                          or seeded.get("real_product_capture") is not False
                          or seeded.get("camera_reframe_id") is None):
            raise AssertionError("Camera fixture did not record a bounded, original 2D camera source")
        if IS_MEASURED and seeded.get("fixture") != "measured-data/1":
            raise AssertionError("Native measured fixture did not bind a versioned source table")
        if IS_PROCEDURAL and seeded.get("fixture") != f"procedural-field-{mode}/1":
            raise AssertionError("Native procedural fixture does not match the requested authored field")
        if IS_COMPOSITION and (seeded.get("fixture") != "composition-stack/1"
                               or seeded.get("original_ui_not_real_capture") is not True
                               or seeded.get("data_truth") != "synthetic_fixture_not_real_world_metric"
                               or len(seeded.get("scene_ids", [])) != 3):
            raise AssertionError("Native composition fixture is not an original source-bound three-scene project")

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
        lifecycle = None
        live_broker_watch = None
        live_watch_started = False
        primary_error = None
        daemon = None
        try:
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
            # CI-only, passive process / memory sampling while the real pinned
            # providers run. Never reads argv/env/secrets and never retries jobs.
            lifecycle = ProviderLifecycleSampler(
                daemon, EVIDENCE / "native-provider-lifecycle.json", interval=2.0
            )
            lifecycle.start()
            # Capture typed owner-session Broker transitions WHILE the original
            # render is running. A post-render replay alone cannot observe them
            # if the Broker exits before the finally block.
            live_broker_watch = (
                PassiveLiveBrokerWatch(
                    SW_BINS / "semwright", socket, session, env=env,
                )
                if FIXTURE == "baseline"
                and os.environ.get("MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY") == "1"
                else None
            )

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
                "driver.mlt-video.av.mux",
                "driver.mlt-video.sync.probe",
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
            if live_broker_watch is not None:
                # Bounded read-only CLI; no native render.start or effect grant.
                live_watch_started = True
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
                    timeout=1500 if IS_COMPOSITION else 420,
                )
            except Exception as error:
                retain_render_failure(error, paths["output"])
                raise
            if result.get("native_render_e2e") != "PASS" or result.get("frame_count") != EXPECTED_FRAMES:
                raise AssertionError(f"Motionwright render did not pass: {result}")

            artifact = result["artifact"]
            directory = artifact.get("directory")
            if not isinstance(directory, str) or not directory:
                raise AssertionError(f"render artifact has no directory: {artifact}")
            artifact_root = paths["output"] / directory
            manifest_file = artifact_root / "artifact-manifest.json"
            frames = sorted((artifact_root / "frames").glob("*.png"))
            if not manifest_file.is_file() or len(frames) != EXPECTED_FRAMES:
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

            shutil.copyfile(manifest_file, EVIDENCE / "artifact-manifest.json")
            for source, name in [
                (frames[0], "frame-first.png"),
                (frames[len(frames) // 2], "frame-middle.png"),
                (frames[-1], "frame-last.png"),
            ]:
                shutil.copyfile(source, EVIDENCE / name)

            if IS_HERO:
                from creative_frame_evidence import inspect_native_frames
                inspect_native_frames(frames, EVIDENCE, EXPECTED_SIZE, project=seeded, source_sha=GITHUB_SHA, provider_sha=PIN)
            if IS_STAGE:
                from product_stage_native_evidence import inspect_native_stage
                inspect_native_stage(frames, EVIDENCE, seeded, source_sha=GITHUB_SHA, semwright_sha=PIN)
            if IS_CAMERA:
                from camera_reframe_native_evidence import inspect_native_camera_frames
                inspect_native_camera_frames(frames, EVIDENCE, seeded, source_sha=GITHUB_SHA, semwright_sha=PIN)
            if IS_MEASURED:
                from measured_native_evidence import inspect_measured_native_frames
                inspect_measured_native_frames(frames, EVIDENCE, seeded, source_sha=GITHUB_SHA, semwright_sha=PIN)
            if IS_PROCEDURAL:
                from procedural_native_evidence import inspect_procedural_frames
                inspect_procedural_frames(frames, EVIDENCE, seeded, source_sha=GITHUB_SHA, provider_sha=PIN)
            if IS_COMPOSITION:
                from composition_native_evidence import inspect_composition_frames
                inspect_composition_frames(frames, EVIDENCE, seeded, source_sha=GITHUB_SHA, provider_sha=PIN)

            audio_relative = "acceptance-audio.wav"
            audio_path = paths["output"] / audio_relative
            # Distinct non-silent L440Hz/R660Hz synthetic tones, not sound
            # design. Use exact 2s baseline, 6s scene or 18s three-scene duration;
            # independently decode and check actual samples for each closed profile.
            write_two_channel_tone_wav(audio_path, seconds=EXPECTED_FRAMES / 30)
            audio_path.chmod(0o600)
            audio_sha256 = digest(audio_path)

            master_result = run_json(
                [
                    str(MW_BIN),
                    "master",
                    str(database),
                    str(connection_path),
                    str(EVIDENCE / "motionwright-render-evidence.json"),
                    audio_relative,
                    audio_sha256,
                    str(EVIDENCE / "motionwright-av-master-evidence.json"),
                ],
                env=env,
                timeout=1500 if IS_COMPOSITION else 420,
            )
            if master_result.get("native_av_master_e2e") != "PASS":
                raise AssertionError(f"Motionwright AV master did not pass: {master_result}")
            master_artifact = master_result.get("artifact")
            if not isinstance(master_artifact, dict):
                raise AssertionError(f"AV master returned no artifact: {master_result}")
            master_relative = master_artifact.get("path")
            master_sha256 = master_artifact.get("sha256")
            if not isinstance(master_relative, str) or not isinstance(master_sha256, str):
                raise AssertionError(f"AV master artifact identity is incomplete: {master_artifact}")
            master_path = paths["output"] / master_relative
            if not master_path.is_file() or digest(master_path) != master_sha256:
                raise AssertionError("AV master bytes do not match the reported artifact digest")
            shutil.copyfile(master_path, EVIDENCE / "motionwright-master.mp4")

            # Canonical receipts prove bytes; independent ffmpeg/ffprobe
            # decodes the exact 60-, 180- or 540-frame bounded profiles.
            # All require measured non-silent, distinct stereo channel tones
            # and native pixel movement. No decoder runs in the shipped app.
            decoded = probe_native_mp4(
                master_path, audio_path, ffmpeg, ffprobe,
                expected_frames=EXPECTED_FRAMES, expected_size=EXPECTED_SIZE,
                expect_movement=FIXTURE != "procedural-static",
            )
            if decoded["master_sha256"] != master_sha256:
                raise AssertionError("Decoded-media sample came from a different MP4 digest")
            if decoded["source_wav_sha256"] != audio_sha256:
                raise AssertionError("Decoded-media probe used a different WAV source")
            write_private_json(EVIDENCE / "decoded-media-proof.json", decoded)
            if IS_STAGE:
                from product_stage_native_evidence import inspect_stage_mp4
                inspect_stage_mp4(master_path, EVIDENCE, seeded, ffmpeg)
            if IS_CAMERA:
                from camera_reframe_native_evidence import inspect_camera_master
                inspect_camera_master(master_path, EVIDENCE, ffmpeg)
            if IS_MEASURED:
                from measured_native_evidence import inspect_measured_master
                inspect_measured_master(master_path, EVIDENCE, seeded, ffmpeg)
            if IS_PROCEDURAL:
                from procedural_native_evidence import inspect_procedural_mp4
                inspect_procedural_mp4(master_path, EVIDENCE, seeded, ffmpeg)
            if IS_COMPOSITION:
                from composition_native_evidence import inspect_composition_mp4
                inspect_composition_mp4(master_path, EVIDENCE, seeded, ffmpeg)

            write_private_json(
                EVIDENCE / "result.json",
                {
                    "native_av_master_e2e": "PASS",
                    "motionwright_sha": GITHUB_SHA,
                    "semwright_sha": PIN,
                    "semwright_version": semwright_version(),
                    "project": seeded,
                    "motion_canvas_driver_sha256": digest(driver),
                    "motion_node_sha256": digest(node_tool),
                    "mlt_driver_sha256": digest(mlt_driver),
                    "mlt_runner_sha256": digest(mlt_runner),
                    "melt_sha256": digest(melt),
                    "ffprobe_sha256": digest(ffprobe),
                    "ffmpeg_sha256": digest(ffmpeg),
                    "artifact_directory": directory,
                    "artifact_manifest_sha256": manifest_digest,
                    "frame_count": len(frames),
                    "fixture": FIXTURE,
                    "creative_approval": "required" if IS_HERO or IS_STAGE or IS_MEASURED or IS_PROCEDURAL or IS_CAMERA or IS_COMPOSITION else "not_assessed",
                    "camera_native_pixel_evidence_sha256": (
                        digest(EVIDENCE / "camera-native-inspection.json") if IS_CAMERA else None
                    ),
                    "original_product_stage_evidence_sha256": (
                        digest(EVIDENCE / "stage-native-inspection.json") if IS_STAGE else None
                    ),
                    "measured_pixel_evidence_sha256": (
                        digest(EVIDENCE / "measured-native-inspection.json") if IS_MEASURED else None
                    ),
                    "procedural_native_pixel_evidence": (
                        digest(EVIDENCE / "procedural-native-inspection.json")
                        if IS_PROCEDURAL else None
                    ),
                    "joint_native_pixel_evidence_sha256": (
                        digest(EVIDENCE / "composition-native-inspection.json") if IS_COMPOSITION else None
                    ),
                    "audio_kind": "synthetic_distinct_stereo_tones_for_transport_acceptance_not_sound_design",
                    "audio_sha256": audio_sha256,
                    "master_path": master_relative,
                    "master_sha256": master_sha256,
                    "decoded_content_check": decoded["evidence_scope"],
                    "decoded_stereo_channel_identity": decoded["stereo_channel_identity"],
                    "decoded_moving_frame_channels": decoded["changed_channel_bytes"],
                    "decoded_video_frames": decoded["video_frames_decoded"],
                    "decoded_pcm_frames": decoded["decoded_pcm_frames"],
                    "review_frames": {
                        "first_sha256": digest(EVIDENCE / "frame-first.png"),
                        "middle_sha256": digest(EVIDENCE / "frame-middle.png"),
                        "last_sha256": digest(EVIDENCE / "frame-last.png"),
                    },
                },
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            _finalize_native_session(
                primary_error=primary_error, lifecycle=lifecycle,
                live_broker_watch=live_broker_watch, live_watch_started=live_watch_started,
                daemon=daemon, socket=socket, session=session, env=env, log_stream=log_stream,
            )

    print(
        json.dumps(
            {
                "native_av_master_e2e": "PASS",
                "motionwright_sha": GITHUB_SHA,
                "semwright_sha": PIN,
                "evidence": str(EVIDENCE.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
