#!/usr/bin/env python3
"""Exact-SHA Motionwright -> Semwright -> Manim Community round-trip acceptance.

CI-only by design. The test creates application-owned Motionwright state, provisions
Motionwright's bounded Manim Community Driver through the real Semwright Broker/Driver
Host sandbox, renders a real MP4 with Manim Community 0.21.0, and independently probes
and hashes the artifact.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
import math
import re
import selectors
import sqlite3
import stat
import sys
import traceback
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

from native_scene_public_artifact import (
    PublicationError, SOURCE_PHASES, SOURCE_SCHEMA, SOURCE_MIME, SOURCE_MAX_FILE, SOURCE_MAX_JSON,
    SOURCE_EVIDENCE_PATHS, source_json, source_clock, source_bytes, source_file_record,
    emitted_session_tokens, snapshot_live_inventory, copy_source_snapshot, source_reopened_project,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = json.loads((ROOT / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
PIN = SOURCE_LOCK["dependencies"]["semwright"]["revision"]
SEMWRIGHT = Path(os.environ.get("SEMWRIGHT_CHECKOUT", ROOT / "_semwright")).resolve()
SW_BINS = SEMWRIGHT / "target" / "debug"
MW_EXAMPLE = ROOT / "target" / "debug" / "examples" / "native-manim-e2e"
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
    path = Path(path)
    return hashlib.sha256(source_bytes(path.parent, path.name, 256 * 1024 * 1024)).hexdigest()


def write_private_json(path: Path, value: object) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


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
    if runtime_receipt != {"schema": 1, "manim_version": MANIM_VERSION}:
        raise SystemExit(f"unexpected Manim runtime receipt: {runtime_receipt!r}")

    probe_env = os.environ.copy()
    probe_env.update(
        PYTHONPATH=str(MANIM_RUNTIME / "site-packages"),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    observed_manim = subprocess.check_output(
        [
            str(PYTHON),
            "-c",
            "import manim,sys;sys.stdout.write(str(manim.__version__))",
        ],
        env=probe_env,
        text=True,
    ).strip()
    if observed_manim != MANIM_VERSION:
        raise SystemExit(
            f"unexpected Manim runtime: expected {MANIM_VERSION}, got {observed_manim}"
        )


def semwright_version() -> str:
    with (SEMWRIGHT / "Cargo.toml").open("rb") as stream:
        return tomllib.load(stream)["workspace"]["package"]["version"]


def wait_for_socket(process: subprocess.Popen[bytes], socket: Path, log: Path) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            if SOURCE_MODE:
                raise AssertionError("original_daemon_exited_before_owner_socket")
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



SOURCE_MODE = False
COMMAND_NUMBER = 0
INSPECTION_NUMBER = 0
SOURCE_ATTEMPT = None
SOURCE_PHASE = None
SOURCE_BEFORE = None


class CaseCommandError(RuntimeError):
    def __init__(self, row):
        self.row = row
        super().__init__("Manim case command: " + row["label"] + " / " + row["state"])


@contextmanager
def preserved_attempt_directory():
    # Intentionally no TemporaryDirectory cleanup: original evidence survives
    # success, native failure, exceptions and readback failure on this runner.
    root = (EVIDENCE / "attempt").resolve()
    root.mkdir(mode=0o700)
    yield str(root)


def command_capture(argv, env, timeout, label, mutation=False):
    global COMMAND_NUMBER
    COMMAND_NUMBER += 1
    directory = EVIDENCE / "commands" / f"{COMMAND_NUMBER:04d}"
    directory.mkdir(parents=True, mode=0o700)
    row = {"schema": "motionwright.manim.command/1", "label": label,
           "mutation": mutation, "state": "NOT_STARTED", "exit_code": None,
           "cleanup_exit_code": None, "reason": None, "streams": {},
           "argv_sha256": hashlib.sha256(json.dumps(argv, ensure_ascii=True,
                                  separators=(",", ":")).encode()).hexdigest(),
           "native_mutation_retried": False}
    # This private record is excluded from publication, including on failure.
    write_private_json(directory / "argv-private.json", argv)
    process = None
    selector = selectors.DefaultSelector()
    streams = {}
    failure = None
    budget = 4 * 1024 * 1024
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        # The finally below already owns the process at the first post-start
        # persistence. A failed receipt write cannot bypass cleanup/deadline.
        row["state"] = "STARTED"
        row["pid"] = process.pid
        write_private_json(directory / "command.json", row)
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            if pipe is None:
                raise OSError("missing_command_pipe")
            os.set_blocking(pipe.fileno(), False)
            target = (directory / (name + ".bin")).open("xb")
            streams[name] = {"pipe": pipe, "target": target, "count": 0, "truncated": False}
            selector.register(pipe, selectors.EVENT_READ, name)
        deadline = started + timeout
        while selector.get_map() or process.poll() is None:
            if time.monotonic() >= deadline:
                row["reason"] = "deadline"
                raise TimeoutError("command_deadline")
            for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                item = streams[key.data]
                chunk = os.read(key.fileobj.fileno(), min(65536, budget + 1 - item["count"]))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                item["target"].write(chunk)
                item["count"] += len(chunk)
                if item["count"] > budget:
                    item["truncated"] = True
                    row["reason"] = "output_budget"
                    raise OSError("command_output_budget")
        row["exit_code"] = process.wait(timeout=1)
        row["state"] = "EXITED"
    except BaseException as error:
        failure = error
        row["state"] = "UNKNOWN" if process is not None else "NOT_STARTED"
        row["reason"] = row["reason"] or type(error).__name__
    finally:
        if process is not None:
            if process.poll() is None or failure is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    row["cleanup_signal_error"] = True
                try:
                    row["cleanup_exit_code"] = process.wait(timeout=5)
                except BaseException:
                    row["cleanup_reap_unverified"] = True
            else:
                row["cleanup_exit_code"] = process.returncode
        finalization_errors = []
        try:
            selector.close()
        except BaseException as error:
            finalization_errors.append(type(error).__name__)
        for name, item in streams.items():
            try:
                item["pipe"].close()
                item["target"].flush()
                os.fsync(item["target"].fileno())
                item["target"].close()
                data = source_bytes(directory, name + ".bin", budget + 1, empty=True)
                row["streams"][name] = {"bytes_retained": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                        "complete": row["state"] == "EXITED" and not item["truncated"]}
            except BaseException as error:
                finalization_errors.append(name + ":" + type(error).__name__)
                try:
                    item["target"].close()
                except BaseException:
                    pass
        if process is not None:
            for pipe in (process.stdout, process.stderr):
                if pipe is not None and not pipe.closed:
                    try:
                        pipe.close()
                    except BaseException as error:
                        finalization_errors.append("pipe:" + type(error).__name__)
        row["elapsed_seconds"] = time.monotonic() - started
        row["finalization_errors"] = finalization_errors
        try:
            write_private_json(directory / "command.json", row)
        except BaseException as error:
            finalization_errors.append("receipt:" + type(error).__name__)
        # Do not replace the primary observation by an exit caused by cleanup,
        # or by an independent failure to retain one of the streams.
    if failure is not None or row["finalization_errors"]:
        raise CaseCommandError(row) from failure
    return row, source_bytes(directory, "stdout.bin", budget, empty=True)


def run_json(argv, *, env, timeout=60, label="capability-catalog", mutation=False):
    row, stdout = command_capture(argv, env, timeout, label, mutation)
    if type(row["exit_code"]) is not int or row["exit_code"] != 0:
        raise CaseCommandError(row)
    try:
        value = source_json(stdout)
        if type(value) is not dict:
            raise ValueError("command_json_object")
        return value
    except (ValueError, UnicodeError, PublicationError) as error:
        row["reason"] = "invalid_json"
        raise CaseCommandError(row) from error


def inspect_saved_database(database):
    # SQLite may create/read SHM while opening WAL. Validate only a separate
    # physical copy; the original DB/WAL/SHM are neither opened nor rewritten.
    global INSPECTION_NUMBER
    INSPECTION_NUMBER += 1
    destination = EVIDENCE / "attempt" / "config" / f"inspect-{INSPECTION_NUMBER:04d}"
    destination.mkdir(mode=0o700)
    records = []
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(database) + suffix)
        try:
            source.lstat()
        except FileNotFoundError:
            if not suffix:
                raise
            continue
        data = source_bytes(source.parent, source.name, empty=bool(suffix))
        with (destination / ("case.sqlite3" + suffix)).open("xb") as stream:
            stream.write(data)
        records.append({"suffix": suffix, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        if source_bytes(source.parent, source.name, empty=bool(suffix)) != data:
            raise AssertionError("database_changed_during_physical_copy")
    target = destination / "case.sqlite3"
    with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=3)) as connection:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise AssertionError("copied_database_quick_check")
        projects = connection.execute("SELECT document_json FROM projects LIMIT 2").fetchall()
        if len(projects) != 1:
            raise AssertionError("copied_database_project_count")
        project = source_json(projects[0][0])
        rows = connection.execute(
            "SELECT receipt_id,project_id,generation,revision,request_id,request_sha256,"
            "command,stage,payload_json FROM production_receipts ORDER BY receipt_id LIMIT 1025"
        ).fetchall()
        if len(rows) > 1024:
            raise AssertionError("copied_native_receipt_budget")
    receipts = [{"id": row[0], "project_id": row[1], "generation": row[2], "revision": row[3],
                 "request_id": row[4], "request_sha256": row[5], "command": row[6],
                 "stage": row[7], "payload": source_json(row[8])} for row in rows]
    return {"project": project, "original_database_files": records, "receipts": receipts}


def fixture_project(project, phase):
    clock = source_clock(phase)
    expected_ids = [f"10000000-0000-4000-8000-{n:012d}" for n in (1, 2, 3)]
    if project.get("title") != "Native Manim editable static source":
        raise AssertionError("unexpected_synthetic_project")
    scenes = project.get("scenes")
    if type(scenes) is not list or len(scenes) != 1:
        raise AssertionError("unexpected_synthetic_scene_count")
    nodes = scenes[0].get("nodes")
    if type(nodes) is not list or [node.get("id") for node in nodes] != expected_ids:
        raise AssertionError("unexpected_synthetic_node_identity")
    expected = [(750, 390, 240, 240, "#20CC40", 20, "circle"),
                (330 if phase in {"edit", "edited-reopen"} else 240, 180, 600, 360, "#E02040", 10, "rectangle"),
                (1380, 120, 300, 150, "#2040E0", 0, "rectangle")]
    for node, row in zip(nodes, expected):
        if (type(node.get("z_index")) is not int
                or any(type(node.get(key)) not in {int, float} or not math.isfinite(node[key])
                       for key in ("x", "y", "width", "height"))):
            raise AssertionError("synthetic_geometry_types")
        observed = (node["x"], node["y"], node["width"], node["height"],
                    node["style"]["fill"], node["z_index"], node["kind"])
        if observed != row or node["name"] != "Original static source shape":
            raise AssertionError("unexpected_synthetic_geometry")
    if scenes[0].get("start") != {"num": "0", "den": "1"} or scenes[0].get("duration") != (
            {"num": "29", "den": "25"} if phase == "edge25" else {"num": "2", "den": "1"}):
        raise AssertionError("unexpected_synthetic_scene_clock")
    profiles = project.get("deliverables")
    if (type(profiles) is not list or not 1 <= len(profiles) <= 32
            or any(type(item) is not dict for item in profiles)
            or len({item.get("id") for item in profiles}) != len(profiles)):
        raise AssertionError("unexpected_synthetic_deliverable_ledger")
    # The existing seed edits only the first profile. Other saved profiles
    # remain part of the complete Project equality checked across this case.
    profile = profiles[0]
    if (profile["width"], profile["height"], profile["frame_rate"]) != (
            640, 360, {"num": str(clock["fps"]), "den": "1"}):
        raise AssertionError("unexpected_synthetic_clock")
    assets = project.get("assets")
    if type(assets) is not list or len(assets) != (2 if phase in {"edit", "edited-reopen"} else 1):
        raise AssertionError("unexpected_synthetic_assets")
    if any(asset.get("media_type") != SOURCE_MIME for asset in assets):
        raise AssertionError("unexpected_synthetic_asset_type")
    capsules = project["production_design"]["capsules"]
    if len(capsules) != 1 or capsules[0]["scene_id"] != scenes[0]["id"]:
        raise AssertionError("unexpected_synthetic_capsule")
    matches = [asset for asset in assets if asset["id"] == capsules[0]["source_asset_id"]
               and asset.get("content_sha256") == capsules[0]["source_sha256"]]
    if len(matches) != 1:
        raise AssertionError("synthetic_capsule_asset_binding")
    return matches[0], scenes[0], profile


def source_native_receipts(snapshot, project, phase, *, completed):
    prefix = "native-manim-source-v2-" + phase + ":"
    rows = [row for row in snapshot["receipts"] if row["request_id"].startswith(prefix)]
    commands = {"driver.manim-community.render.start", "driver.manim-community.render.status",
                "driver.manim-community.render.result", "driver.manim-community.render.cancel"}
    for row in rows:
        if (row["project_id"], row["generation"], row["revision"]) != (
                project["id"], project["generation"], project["revision"]) or row["command"] not in commands:
            raise AssertionError("native_receipt_phase_binding")
    if completed:
        starts = [row for row in rows if row["command"].endswith(".start") and row["stage"] == "dispatching"]
        done = [row for row in rows if row["stage"] == "completed"]
        if len(starts) != 1 or any(row["stage"] == "outcome_unknown" for row in rows):
            raise AssertionError("native_phase_has_unresolved_or_duplicate_start")
        for failed in (item for item in rows if item["stage"] == "failed_known"):
            payload = failed["payload"]
            error = payload.get("error", {})
            if (failed["command"] != "driver.manim-community.render.status"
                    or payload.get("mutation") is not False
                    or error.get("code") != "Timeout" or error.get("outcome_known") is not True):
                raise AssertionError("native_phase_has_nonretryable_known_failure")
        for command in ("start", "status", "result"):
            matches = [row for row in done if row["command"].endswith("." + command)]
            if not matches or (command != "status" and len(matches) != 1):
                raise AssertionError("missing_original_native_phase_receipt")
    return rows


SOURCE_CODE = SOURCE_EVIDENCE_PATHS


def source_identity():
    def git(*args, cwd=ROOT):
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                                check=True, timeout=15)
        if len(result.stdout) > 1024 * 1024 or result.stderr:
            raise AssertionError("source_identity_git_output")
        return result.stdout.decode("ascii").strip()
    current = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    if not re.fullmatch(r"[0-9a-f]{40}", current) or current != GITHUB_SHA:
        raise AssertionError("actual_checkout_does_not_match_github_sha")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise AssertionError("source_checkout_is_modified")
    if git("rev-parse", "HEAD", cwd=SEMWRIGHT) != PIN:
        raise AssertionError("source_sdk_checkout_mismatch")
    sources = [source_file_record(ROOT, name, 4 * 1024 * 1024) for name in SOURCE_CODE]
    tools = {
        "example": MW_EXAMPLE, "driver": MW_DRIVER, "runner": MW_RUNNER,
        "semwright": SW_BINS / "semwright", "semwrightd": SW_BINS / "semwrightd",
        "sandbox": SW_BINS / "semwright-sandbox", "python": PYTHON,
        "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
    }
    binaries = {}
    for label, path in tools.items():
        record = source_file_record(path.parent, path.name, 256 * 1024 * 1024)
        binaries[label] = {"bytes": record["bytes"], "sha256": record["sha256"]}
    if SOURCE_PATHS is not None and SOURCE_BEFORE is not None:
        for label, filename in (("driver", "motionwright-manim-community-driver"),
                                ("runner", "motionwright-manim-runner")):
            record = source_file_record(SOURCE_PATHS["bin"], filename, 256 * 1024 * 1024)
            if {"bytes": record["bytes"], "sha256": record["sha256"]} != binaries[label]:
                raise AssertionError("executed_private_binary_differs_from_built_source")
    runtime_rows, runtime_total = [], 0
    packages = MANIM_RUNTIME / "site-packages"
    for path in sorted(packages.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise AssertionError("linked_manim_runtime_input")
        if stat.S_ISREG(metadata.st_mode):
            name = path.relative_to(MANIM_RUNTIME).as_posix()
            runtime_rows.append(source_file_record(MANIM_RUNTIME, name, 128 * 1024 * 1024))
            runtime_total += runtime_rows[-1]["bytes"]
            if len(runtime_rows) > 50000 or runtime_total > 1024 * 1024 * 1024:
                raise AssertionError("manim_runtime_inventory_budget")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise AssertionError("nonregular_manim_runtime_input")
    raw_runtime = json.dumps(runtime_rows, sort_keys=True, separators=(",", ":")).encode()
    return {"C": current, "T": tree, "semwright_sha": PIN,
            "semwright_version": semwright_version(), "manim_version": MANIM_VERSION,
            "sources": sources, "binaries": binaries,
            "runtime": {"files": len(runtime_rows), "bytes": runtime_total,
                        "inventory_sha256": hashlib.sha256(raw_runtime).hexdigest()}}


def save_source_attempt():
    if SOURCE_ATTEMPT is not None:
        write_private_json(EVIDENCE / "source-roundtrip.json", SOURCE_ATTEMPT)


def snapshot_source_phase(paths, row, session):
    # The example has exited; the Broker never owns a Motionwright DB handle.
    # A failed/unknown example is collected only after daemon cleanup below.
    roots = {"data": paths["motionwright-data"], "bundles": paths["bundles"],
             "work": paths["work"], "output": paths["output"]}
    destination = EVIDENCE / "custody" / row["phase"]
    tokens = emitted_session_tokens(session) if session.exists() else ()
    before = snapshot_live_inventory(roots)
    row["custody_status"] = "NOT_VERIFIED"
    save_source_attempt()
    copied = copy_source_snapshot(roots, destination, tokens)
    after = snapshot_live_inventory(roots)
    if before != copied or copied != after:
        raise AssertionError("original_manim_state_changed_during_snapshot")
    row["custody_inventory"] = copied
    row["custody_status"] = "RETAINED_ON_RUNNER"
    row["custody_scope"] = "ORIGINAL_SYNTHETIC_CASE_FILES_NOT_UNIVERSAL_SECRET_CERTIFICATION"
    save_source_attempt()


def phase_record_file(directory, name, data):
    destination = directory / name
    with destination.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    destination.chmod(0o600)
    if source_bytes(directory, name) != data:
        raise AssertionError("phase_record_copy_mismatch")
    return {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def initialize_source_attempt():
    global SOURCE_ATTEMPT, SOURCE_PHASE, SOURCE_BEFORE
    SOURCE_BEFORE = source_identity()
    SOURCE_ATTEMPT = {
        "schema": SOURCE_SCHEMA, "status": "NOT_VERIFIED",
        "motionwright_sha": GITHUB_SHA, "semwright_sha": PIN,
        "inputs_before": SOURCE_BEFORE, "inputs_after": None,
        "phases": [{"phase": phase, "native_invocations": 0, "native_outcome": "NOT_STARTED",
                    "reader_status": "NOT_RUN", "custody_status": "NOT_RUN", "files": {}}
                   for phase in SOURCE_PHASES],
        "native_job_retried": False, "human_acceptance": False,
        "original_runner_files_deleted": False,
        "public_artifact_custody": "NOT_VERIFIED",
    }
    SOURCE_PHASE = SOURCE_ATTEMPT["phases"][0]
    save_source_attempt()


def source_roundtrip(paths, env, original_database, original_seed, session, socket):
    global SOURCE_PHASE
    if SOURCE_ATTEMPT is None:
        raise AssertionError("source_attempt_not_initialized")
    data_root = paths["motionwright-data"]
    bundle_root = paths["bundles"]
    databases = {"original": original_database}
    resources = {"original": original_seed["resource"]}
    original_carrier = None
    edited_carrier = None
    saved_projects = {}
    for row in SOURCE_ATTEMPT["phases"]:
        phase = row["phase"]
        SOURCE_PHASE = row
        directory = EVIDENCE / "phases" / phase
        directory.mkdir(parents=True, mode=0o700)
        if phase == "original":
            prepared = original_seed
        elif phase == "reopen":
            run_json([str(MW_EXAMPLE), "export-source", str(databases["original"]),
                      str(bundle_root / "original")], env=env, label="export-original")
            database = data_root / "reopen" / "motionwright.sqlite3"
            database.parent.mkdir(mode=0o700)
            prepared = run_json([str(MW_EXAMPLE), "import-source", str(database),
                                 str(bundle_root / "original")], env=env, label="import-reopen", mutation=True)
            databases["reopen"] = database
            resources["reopen"] = prepared["resource"]
        elif phase == "edit":
            # The completed reopen snapshot exists before this canonical edit.
            if SOURCE_ATTEMPT["phases"][1]["custody_status"] != "RETAINED_ON_RUNNER":
                raise AssertionError("reopen_original_state_not_retained_before_edit")
            database = databases["reopen"]
            prepared = run_json([str(MW_EXAMPLE), "edit-source", str(database)], env=env,
                                label="edit-source", mutation=True)
            if prepared.get("canonical_transform_import_applied") is not True:
                raise AssertionError("canonical_source_edit_not_confirmed")
        elif phase == "edited-reopen":
            run_json([str(MW_EXAMPLE), "export-source", str(databases["reopen"]),
                      str(bundle_root / "edited")], env=env, label="export-edited")
            database = data_root / "edited-reopen" / "motionwright.sqlite3"
            database.parent.mkdir(mode=0o700)
            prepared = run_json([str(MW_EXAMPLE), "import-source", str(database),
                                 str(bundle_root / "edited")], env=env,
                                label="import-edited-reopen", mutation=True)
            databases["edited-reopen"] = database
            resources["edited-reopen"] = prepared["resource"]
        else:
            database = data_root / "edge25" / "motionwright.sqlite3"
            database.parent.mkdir(mode=0o700)
            prepared = run_json([str(MW_EXAMPLE), "seed-source-edge25", str(database)], env=env,
                                label="seed-edge25", mutation=True)
            databases["edge25"] = database
            resources["edge25"] = prepared["resource"]
        database = databases["original"] if phase == "original" else (
            databases["reopen"] if phase in {"reopen", "edit"} else databases[phase])
        before = inspect_saved_database(database)
        project = before["project"]
        asset, scene, profile = fixture_project(project, phase)
        if phase in {"reopen", "edited-reopen"}:
            previous = "original" if phase == "reopen" else "edit"
            row["project_preservation"] = source_reopened_project(saved_projects[previous], project)
            save_source_attempt()
        saved_projects[phase] = project
        if any(prepared.get(key) != project[field] for key, field in
               (("project_id", "id"), ("generation", "generation"), ("revision", "revision"))):
            raise AssertionError("phase_preparation_stamp_mismatch")
        resource_key = "reopen" if phase == "edit" else phase
        resource = resources[resource_key]
        if type(resource) is not str or not resource or len(resource) > 256:
            raise AssertionError("phase_preparation_resource_invalid")
        if source_native_receipts(before, project, phase, completed=False):
            raise AssertionError("phase_already_has_native_receipts_no_replay")
        if phase in {"reopen", "edited-reopen"} and before["receipts"]:
            raise AssertionError("portable_import_retained_native_authority")
        if prepared.get("native_execution_performed") is not False:
            raise AssertionError("phase_preparation_did_not_exclude_native_execution")
        digest_value = asset["content_sha256"]
        if type(digest_value) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest_value):
            raise AssertionError("source_asset_digest_format")
        carrier_path = database.parent / "blobs" / "sha256" / digest_value[:2] / digest_value
        carrier = source_bytes(carrier_path.parent, carrier_path.name, 2 * 1024 * 1024)
        if hashlib.sha256(carrier).hexdigest() != digest_value:
            raise AssertionError("original_source_cas_bytes_mismatch")
        if phase == "original":
            original_carrier = carrier
        elif phase == "reopen" and carrier != original_carrier:
            raise AssertionError("original_carrier_changed_after_portable_reopen")
        elif phase == "edit":
            if carrier == original_carrier:
                raise AssertionError("edited_carrier_did_not_change")
            edited_carrier = carrier
            origin_sha = hashlib.sha256(original_carrier).hexdigest()
            original_path = database.parent / "blobs" / "sha256" / origin_sha[:2] / origin_sha
            if source_bytes(original_path.parent, original_path.name, 2 * 1024 * 1024) != original_carrier:
                raise AssertionError("source_edit_lost_original_carrier")
        elif phase == "edited-reopen":
            if carrier != edited_carrier:
                raise AssertionError("edited_carrier_changed_after_portable_reopen")
            origin_sha = hashlib.sha256(original_carrier).hexdigest()
            original_path = database.parent / "blobs" / "sha256" / origin_sha[:2] / origin_sha
            if source_bytes(original_path.parent, original_path.name, 2 * 1024 * 1024) != original_carrier:
                raise AssertionError("edited_portable_reopen_lost_original_carrier")
        row["files"]["origin"] = phase_record_file(directory, "origin.mwmsrc.zip", carrier)
        row["project"] = {"id": project["id"], "generation": project["generation"],
                          "revision": project["revision"], "scene_id": scene["id"],
                          "deliverable_id": profile["id"]}
        row["original_database_before"] = before["original_database_files"]
        row["inputs_before"] = source_identity()
        if row["inputs_before"] != SOURCE_BEFORE:
            raise AssertionError("phase_inputs_changed_before_native_start")
        connection = paths["config"] / (phase + "-connection.json")
        write_private_json(connection, {
            "schema": "motionwright-semwright-connection/1",
            "executable": str((SW_BINS / "semwright").resolve()),
            "executable_sha256": digest(SW_BINS / "semwright"),
            "socket": str(socket), "session_file": str(session),
            "output_root": str(paths["output"]), "resource": resource,
        })
        producer_path = directory / "producer.json"
        row["native_invocations"] = 1
        row["native_outcome"] = "UNKNOWN"
        save_source_attempt()
        completed = run_json([str(MW_EXAMPLE), "realize-source", str(database),
                              str(connection), str(producer_path), phase], env=env,
                             timeout=420, label="realize-" + phase, mutation=True)
        producer_bytes = source_bytes(directory, "producer.json", SOURCE_MAX_JSON)
        producer = source_json(producer_bytes)
        if (completed.get("source_job_completed") is not True
                or completed.get("phase") != phase or completed.get("project_id") != project["id"]
                or producer.get("schema") != "motionwright.manim.native-source-producer/2"
                or producer.get("phase") != phase or producer.get("project") != project
                or producer.get("request_id") != "native-manim-source-v2-" + phase
                or producer.get("native_source_getters_verified") is not True
                or producer.get("decoded_media_fidelity_verified") is not False
                or producer.get("human_acceptance") is not False):
            raise AssertionError("source_native_completion_not_bound")
        if producer["result"].get("native_driver") != "driver:manim-community":
            raise AssertionError("source_native_driver_binding")
        row["native_outcome"] = "COMPLETED"
        row["original_journal_status"] = "NOT_VERIFIED"
        row["reader_status"] = "NOT_VERIFIED"
        row["files"]["producer"] = source_file_record(directory, "producer.json", SOURCE_MAX_JSON)
        save_source_attempt()
        # This records the original validated application's completion response.
        # A failed database-copy inspection remains an independent observation.
        after_native = inspect_saved_database(database)
        if after_native["project"] != project:
            raise AssertionError("native_phase_changed_saved_project")
        receipts = source_native_receipts(after_native, project, phase, completed=True)
        row["original_receipts"] = len(receipts)
        row["original_journal_status"] = "VERIFIED_FROM_SEPARATE_DATABASE_COPY"
        save_source_attempt()
        # Custody or decoder failure below does not rewrite native completion.
        result = producer["result"]
        origin = producer["source_origin"]
        if origin.get("carrier_sha256") != digest_value or origin.get("carrier_asset_id") != asset["id"]:
            raise AssertionError("producer_original_carrier_binding")
        if result.get("native_driver") != "driver:manim-community":
            raise AssertionError("source_native_driver_binding")
        source = result["source"]
        for role, key, output_name, limit in (
                ("prepared", "source_artifact", "prepared.mwmsrc.zip", 2 * 1024 * 1024),
                ("getter", "native_readback_artifact", "native.objects.json", 256 * 1024)):
            metadata = source[key]
            data = source_bytes(paths["output"], metadata["relative_path"], limit)
            if type(metadata.get("bytes")) is not int or len(data) != metadata["bytes"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
                raise AssertionError("native_source_artifact_bytes_mismatch")
            row["files"][role] = phase_record_file(directory, output_name, data)
        video = result["artifact"]
        video_bytes = source_bytes(paths["output"], video["relative_path"], SOURCE_MAX_FILE)
        if type(video.get("bytes")) is not int or len(video_bytes) != video["bytes"] or hashlib.sha256(video_bytes).hexdigest() != video["sha256"]:
            raise AssertionError("native_mp4_bytes_mismatch")
        row["files"]["video"] = phase_record_file(directory, "native.mp4", video_bytes)
        row["files"]["producer"] = source_file_record(directory, "producer.json", SOURCE_MAX_JSON)
        frames = directory / "frames"
        frames.mkdir(mode=0o700)
        readback_path = directory / "readback.json"
        run_json([str(PYTHON), "-B", str(ROOT / "tooling" / "manim_native_readback.py"),
                  "--phase", phase, "--video", str(directory / "native.mp4"),
                  "--producer", str(producer_path), "--source", str(directory / "prepared.mwmsrc.zip"),
                  "--origin-source", str(directory / "origin.mwmsrc.zip"),
                  "--native-readback", str(directory / "native.objects.json"),
                  "--report", str(readback_path), "--frames", str(frames),
                  "--ffmpeg", str(FFMPEG), "--ffprobe", str(FFPROBE)],
                 env=env, timeout=180, label="readback-" + phase)
        report = source_json(source_bytes(directory, "readback.json", SOURCE_MAX_JSON))
        if (report.get("status") != "PASS" or report.get("phase") != phase
                or report.get("producer_record_sha256") != hashlib.sha256(producer_bytes).hexdigest()
                or report.get("video_sha256") != video["sha256"] or report.get("video_bytes") != len(video_bytes)
                or report.get("native_job_retried") is not False or report.get("human_acceptance") is not False
                or report.get("source", {}).get("origin_carrier_bytes_independently_read") is not True
                or report.get("source", {}).get("origin_binding_evidence") !=
                   "PHYSICAL_ORIGIN_BYTES_AND_REBIND_CHECKED"
                or report["source"].get("origin_carrier_sha256") != digest_value
                or report["source"].get("origin_carrier_asset_id") != asset["id"]
                or report["source"].get("source_bundle_sha256") != row["files"]["prepared"]["sha256"]
                or report["source"].get("native_readback_sha256") != row["files"]["getter"]["sha256"]):
            raise AssertionError("independent_readback_not_bound_to_original_native_artifacts")
        for item in row["files"].values():
            if source_file_record(directory, item["path"], SOURCE_MAX_FILE) != item:
                raise AssertionError("phase_input_changed_during_reader")
        if source_bytes(carrier_path.parent, carrier_path.name, 2 * 1024 * 1024) != carrier:
            raise AssertionError("original_cas_carrier_changed_during_reader")
        row["files"]["readback"] = source_file_record(directory, "readback.json", SOURCE_MAX_JSON)
        row["reader_status"] = "PASS"
        row["inputs_after"] = source_identity()
        if row["inputs_after"] != row["inputs_before"]:
            raise AssertionError("phase_inputs_changed_during_native_or_reader")
        after_reader = inspect_saved_database(database)
        if after_reader["project"] != project or after_reader["receipts"] != after_native["receipts"]:
            raise AssertionError("independent_reader_changed_original_saved_state")
        snapshot_source_phase(paths, row, session)
    SOURCE_ATTEMPT["inputs_after"] = source_identity()
    if SOURCE_ATTEMPT["inputs_after"] != SOURCE_BEFORE:
        raise AssertionError("source_inputs_changed_after_roundtrip")
    SOURCE_ATTEMPT["status"] = "PASS"
    save_source_attempt()



def finish_source_attempt(paths, session, original_error):
    """Observe original files after daemon shutdown; never start a replacement job."""
    if SOURCE_ATTEMPT is None:
        return ["source_attempt_not_initialized"]
    errors = []
    if original_error is not None:
        SOURCE_ATTEMPT["status"] = "NOT_VERIFIED"
        SOURCE_ATTEMPT["failure_class"] = type(original_error).__name__
        try:
            diagnostic = "".join(traceback.format_exception(original_error)).encode("utf-8")
            with (EVIDENCE / "failure-private.log").open("xb") as stream:
                stream.write(diagnostic[:SOURCE_MAX_JSON])
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException as error:
            errors.append("private_failure_record:" + type(error).__name__)
    row = SOURCE_PHASE
    if row is not None:
        # A known terminal disposition is admitted only from the original
        # producer diagnostic and matching stamp. Absence never means no effect.
        if row["native_outcome"] == "UNKNOWN" and row.get("project"):
            directory = EVIDENCE / "phases" / row["phase"]
            diagnostic_path = directory / "producer.diagnostic.json"
            if diagnostic_path.exists():
                try:
                    raw = source_bytes(directory, diagnostic_path.name, SOURCE_MAX_JSON)
                    diagnostic = source_json(raw)
                    stamp = row["project"]
                    saved = diagnostic["project"]
                    if (diagnostic.get("schema") != "motionwright.manim.source-attempt/2"
                            or diagnostic.get("phase") != row["phase"]
                            or diagnostic.get("request_id") != "native-manim-source-v2-" + row["phase"]
                            or diagnostic.get("native_job_retry_performed") is not False
                            or any(saved.get(key) != stamp[key] for key in ("id", "generation", "revision"))):
                        raise AssertionError("original_failure_diagnostic_binding")
                    row["diagnostic_sha256"] = hashlib.sha256(raw).hexdigest()
                    row["diagnostic_bytes"] = len(raw)
                    rows = diagnostic["original_receipts"]
                    if type(rows) is not list or len(rows) > 256:
                        raise AssertionError("original_failure_diagnostic_receipts")
                    if (diagnostic["error"].get("outcome_known") is True
                            and not any(item.get("stage") == "outcome_unknown" for item in rows)):
                        row["native_outcome"] = "FAILED_KNOWN"
                    row["diagnostic_error_code"] = diagnostic["error"].get("code")
                except BaseException as error:
                    errors.append("original_diagnostic:" + type(error).__name__)
        if row["custody_status"] != "RETAINED_ON_RUNNER":
            try:
                # A partially written snapshot is preserved; it is not replaced.
                snapshot_source_phase(paths, row, session)
            except BaseException as error:
                row["custody_status"] = "NOT_VERIFIED"
                errors.append("original_custody:" + type(error).__name__)
    try:
        SOURCE_ATTEMPT["inputs_after"] = source_identity()
        if SOURCE_ATTEMPT["inputs_after"] != SOURCE_BEFORE:
            raise AssertionError("source_final_identity_changed")
    except BaseException as error:
        errors.append("source_final_identity:" + type(error).__name__)
    try:
        if (EVIDENCE / "semwrightd.log").exists():
            SOURCE_ATTEMPT["daemon_log"] = source_file_record(EVIDENCE, "semwrightd.log",
                                                              16 * 1024 * 1024, empty=True)
    except BaseException as error:
        errors.append("daemon_log_custody:" + type(error).__name__)
    if errors or original_error is not None:
        SOURCE_ATTEMPT["status"] = "NOT_VERIFIED"
    SOURCE_ATTEMPT["finalization_errors"] = errors
    try:
        save_source_attempt()
    except BaseException as error:
        errors.append("source_final_receipt:" + type(error).__name__)
    return errors


SOURCE_PATHS = None
SOURCE_SESSION = None
SOURCE_DAEMON_ERRORS = []


def run_case() -> None:
    global SOURCE_PATHS, SOURCE_SESSION
    exact_inputs()
    EVIDENCE.mkdir(parents=True, exist_ok=False)

    context = preserved_attempt_directory() if SOURCE_MODE else tempfile.TemporaryDirectory(prefix="motionwright-native-manim-")
    with context as raw:
        root = Path(raw).resolve()
        root.chmod(0o700)
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
        ] + (["bundles"] if SOURCE_MODE else []):
            path = root / name
            path.mkdir(mode=0o700)
            paths[name] = path
        if SOURCE_MODE:
            SOURCE_PATHS = paths
            SOURCE_SESSION = paths["runtime"] / "motionwright.session"
            initialize_source_attempt()

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

        data_directory = paths["motionwright-data"]
        if SOURCE_MODE:
            data_directory = data_directory / "original"
            data_directory.mkdir(mode=0o700)
        database = data_directory / "motionwright.sqlite3"
        seeded = run_json([str(MW_EXAMPLE), "seed-source" if SOURCE_MODE else "seed", str(database)],
                          env=env, label="seed-original" if SOURCE_MODE else "capability-catalog",
                          mutation=True)
        if not SOURCE_MODE and seeded.get("primitive_count") != 3:
            raise AssertionError(f"unexpected Manim seed: {seeded}")

        driver = paths["bin"] / "motionwright-manim-community-driver"
        runner = paths["bin"] / "motionwright-manim-runner"
        shutil.copyfile(MW_DRIVER, driver)
        shutil.copyfile(MW_RUNNER, runner)
        driver.chmod(0o700)
        runner.chmod(0o700)
        if digest(driver) != digest(MW_DRIVER) or digest(runner) != digest(MW_RUNNER):
            raise AssertionError("private_provider_copy_differs_from_built_binary")

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
        session = paths["runtime"] / "motionwright.session"
        daemon_log = EVIDENCE / "semwrightd.log"
        log_stream = daemon_log.open("wb")
        daemon = None
        operation_error = None
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

            if SOURCE_MODE:
                source_roundtrip(paths, env, database, seeded, session, socket)
                return

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

            result = run_json(
                [
                    str(MW_EXAMPLE),
                    "realize",
                    str(database),
                    str(connection_path),
                    str(EVIDENCE / "motionwright-manim-evidence.json"),
                ],
                env=env,
                timeout=420,
            )
            if result.get("native_manim_e2e") != "PASS":
                raise AssertionError(f"Motionwright Manim realization did not pass: {result}")

            evidence = json.loads(
                (EVIDENCE / "motionwright-manim-evidence.json").read_text(encoding="utf-8")
            )
            artifact = evidence["result"]["artifact"]
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

            review_mp4 = EVIDENCE / "motionwright-manim-community.mp4"
            shutil.copyfile(artifact_path, review_mp4)
            write_private_json(
                EVIDENCE / "result.json",
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
        except BaseException as error:
            operation_error = error
            raise
        finally:
            cleanup_error = None
            try:
                if daemon is not None and daemon.poll() is None:
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
            except BaseException as error:
                cleanup_error = error
            finally:
                try:
                    log_stream.close()
                except BaseException as error:
                    cleanup_error = cleanup_error or error
            if cleanup_error is not None:
                if SOURCE_MODE:
                    SOURCE_DAEMON_ERRORS.append(type(cleanup_error).__name__)
                if operation_error is None:
                    raise cleanup_error

    print(
        json.dumps(
            {
                "native_manim_e2e": "PASS",
                "motionwright_sha": GITHUB_SHA,
                "semwright_sha": PIN,
                "evidence": str(EVIDENCE),
            },
            sort_keys=True,
        )
    )



def main(argv=None):
    global SOURCE_MODE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-roundtrip", action="store_true",
                        help="Retain and inspect five original static-source phases with the existing native provider")
    args = parser.parse_args(argv)
    SOURCE_MODE = args.source_roundtrip
    original_error = None
    try:
        run_case()
    except BaseException as error:
        original_error = error
    final_errors = []
    if SOURCE_MODE and SOURCE_PATHS is not None:
        final_errors = finish_source_attempt(SOURCE_PATHS, SOURCE_SESSION, original_error)
        if SOURCE_DAEMON_ERRORS:
            final_errors.extend("daemon_cleanup:" + item for item in SOURCE_DAEMON_ERRORS)
            if SOURCE_ATTEMPT is not None:
                SOURCE_ATTEMPT["status"] = "NOT_VERIFIED"
                SOURCE_ATTEMPT["finalization_errors"] = final_errors
                try:
                    save_source_attempt()
                except BaseException:
                    pass
    if original_error is not None:
        raise original_error
    if final_errors:
        raise AssertionError("original_source_custody_finalization_not_verified")
    if SOURCE_MODE:
        if SOURCE_ATTEMPT is None or SOURCE_ATTEMPT.get("status") != "PASS":
            raise AssertionError("source_roundtrip_not_verified")
        print(json.dumps({"native_manim_source_roundtrip": "PASS", "motionwright_sha": GITHUB_SHA,
                          "semwright_sha": PIN, "phases": list(SOURCE_PHASES),
                          "native_job_retried": False, "human_acceptance": False}, sort_keys=True))


if __name__ == "__main__":
    main()
