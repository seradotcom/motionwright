#!/usr/bin/env python3
"""One source-bound synthetic camera attempt through the original Broker/Host.

Run only in a disposable Linux Actions job. Source export never confers authority.
The original camera producer and pixel oracle are invoked unchanged. Receipts
distinguish this 120-frame master from editing/reopening four native revisions.
"""
import sys

if not sys.flags.isolated or not sys.flags.no_site:
    raise SystemExit("invoke with python -B -I -S")

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
import tomllib


MIB = 1024 * 1024
MANIM_VERSION = "0.21.0"
SEMWRIGHT_PIN = "54d3459a051adbce4cae9f60c0dea8f60c824be6"
SOURCE_INPUTS = {
    "crates/native/examples/native-manim-camera-seed.rs": "cb61f7e548e1a0cc8180bd092050c09a341d428f6a1681098cfc8d77052b64e6",
    "tooling/native_manim_camera_pixels.py": "d88387a025f611d963581d3ef790d890885ff4545ce05163c58a1a91a950e9e7",
    "tooling/tests/test_native_manim_camera_pixels.py": "541da5525837fec38943629d4dd11b27f6abd0c11b8284ecf8a8fe5ccb505639",
    "crates/driver-manim-community/src/main.rs": "9b6a14be9c0b7aa8548b26b90423d4da1ae6b4561fef2e33954b1491d25dd130",
    "crates/manim-profile/src/planar_camera.rs": "2afd5550f5573a8517cad8c73ffddac06ab62a3f18cd14316b72c5fe837995f3",
    "crates/native/examples/native-manim-e2e.rs": "4eb013102d0a9d0a94da89a66eeada1ddf6d2199b64a5dba2d1b0ddce53f4fba",
    "crates/native/src/manim_camera_projection.rs": "dbf92e3f62255bc32a201f7c2823057c9ae6b2513fce76e6fd86e41cff57129b",
    "crates/native/src/production.rs": "e902b15653742ae973b58195d86bc3c76ac626916bf00757d261fe1034c40f84",
    "crates/service/src/jobs.rs": "86f197b10f504b2b1625c1869baeca9d1c9beb57b43dc19dd3ac1f2bad731540",
    "crates/service/tests/production_jobs_identity.rs": "981033a019b721d728ed09fab4e6a15bb4fe10215841972477437707f91071db",
    "Cargo.lock": "583f9276782966778e544ca1facd9d7a9af5a7c216e2dee4da598c00da118a84",
    "SOURCE_LOCK.json": "f0e42c064c71a2307b4043b8b345ff54b36f8df2692db5809ce1e85635fdc1c1",
}
OWN_INPUTS = (
    "crates/native/examples/ci-manim-camera-plan.rs",
    "tooling/native_manim_camera120_e2e.py",
    ".github/workflows/native-manim-camera120.yml",
    "tooling/ci_test_runner.py",
)
PROFILE = {"width": 640, "height": 360, "frame_rate": 30}
LIMITATIONS = {
    "four_phase_edit_and_reopen_verified": False,
    "registered_native_source_reopen_verified": False,
    "all_frame_pixel_fidelity_verified": False,
    "both_axes_pixel_motion_independently_verified": False,
    "strict_point_two_frame_timing_verified": False,
    "voice_caption_mixing_verified": False,
    "independent_rights_acceptance": False,
    "human_acceptance": False,
    "formal_acceptance": False,
    "release_readiness": False,
}


class Refused(RuntimeError):
    pass


class AttemptError(Refused):
    def __init__(self, message, *, unknown=False):
        super().__init__(message)
        self.unknown = unknown


def require(condition, message):
    if not condition:
        raise Refused(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def plain(path, *, missing=False):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "absolute nontraversing path required")
    for index, part in enumerate((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            require(missing and index == 0, "missing path ancestor")
            continue
        require(not stat.S_ISLNK(info.st_mode), "symbolic link input refused")
        require(index == 0 or stat.S_ISDIR(info.st_mode), "non-directory ancestor")
    return path


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def file_record(path, maximum, *, empty=False):
    path = plain(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and (empty or before.st_size > 0)
            and before.st_size <= maximum, "nonregular/empty/oversized evidence")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        require(identity(os.fstat(stream.fileno())) == identity(before), "input replaced before open")
        while chunk := stream.read(min(1024 * 1024, maximum - count + 1)):
            count += len(chunk)
            require(count <= maximum, "input grew beyond byte budget")
            digest.update(chunk)
        require(identity(os.fstat(stream.fileno())) == identity(before), "input changed during read")
    require(count == before.st_size and identity(path.lstat()) == identity(before), "input identity changed")
    return {"path": str(path), "bytes": count, "sha256": digest.hexdigest()}


def read_bytes(path, maximum, *, empty=False):
    before = file_record(path, maximum, empty=empty)
    with path.open("rb") as stream:
        raw = stream.read(min(maximum + 1, before["bytes"] + 1))
    require(len(raw) == before["bytes"] and sha256(raw) == before["sha256"]
            and file_record(path, maximum, empty=empty) == before, "evidence changed before parsing")
    return raw


def read_json(path, maximum=32 * MIB):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    return json.loads(read_bytes(path, maximum), object_pairs_hook=pairs)


def write_bytes(path, raw):
    plain(path, missing=True)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        require(stream.write(raw) == len(raw), "short evidence write")
        stream.flush()
        os.fsync(stream.fileno())


def write_json(path, value):
    write_bytes(path, (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())


def mkdir(path):
    plain(path, missing=True)
    path.mkdir(mode=0o700)
    return path


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_GRAFT_FILE=os.devnull, GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
    return env


def git(repo, *args):
    command = [shutil.which("git"), "--no-replace-objects", "-c", "core.fsmonitor=false",
               "-C", str(repo), *args]
    result = subprocess.run(command, env=git_env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    require(result.returncode == 0 and len(result.stdout) <= 16 * MIB
            and len(result.stderr) <= MIB, "original Git source query failed")
    return result.stdout


def source_identity(repo, expected):
    require(re.fullmatch(r"[0-9a-f]{40}", expected) is not None, "full source SHA required")
    require(git(repo, "rev-parse", "HEAD").decode().strip() == expected, "actual source differs from requested SHA")
    require(not git(repo, "status", "--porcelain", "--untracked-files=all"), "source checkout is dirty")
    require(not git(repo, "ls-files", "--others", "--ignored", "--exclude-standard"), "ignored source inputs refused")
    tree = git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    inputs = {}
    for relative in (*SOURCE_INPUTS, *OWN_INPUTS):
        record = file_record(repo / relative, 8 * MIB)
        raw = git(repo, "show", f"{expected}:{relative}")
        require(sha256(raw) == record["sha256"], "physical source differs from original Git blob: " + relative)
        if relative in SOURCE_INPUTS:
            require(record["sha256"] == SOURCE_INPUTS[relative], "unreviewed camera source composition: " + relative)
        inputs[relative] = {"bytes": record["bytes"], "sha256": record["sha256"]}
    lock = read_json(repo / "SOURCE_LOCK.json")
    require(lock["dependencies"]["semwright"]["revision"] == SEMWRIGHT_PIN, "Semwright source pin changed")
    return {"source_sha": expected, "tree_sha": tree, "source_inputs": inputs}


def context():
    require(os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "linux", "disposable Linux Actions job required")
    keys = ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
            "GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF")
    row = {key: os.environ.get(key, "") for key in keys}
    require(all(row.values()), "incomplete execution context")
    require(row["GITHUB_REPOSITORY"] == "seradotcom/motionwright", "unexpected repository")
    require(row["GITHUB_RUN_ID"].isdigit() and row["GITHUB_RUN_ATTEMPT"].isdigit(), "invalid run identity")
    return row


class Capture:
    """Bound both stream writes; retain intent even if the caller is interrupted.

    A forced stop of the native client is UNKNOWN. Stopping a client or the
    disposable Broker never means its native side effect was safely cancelled.
    """
    def __init__(self, folder, argv, env, timeout, *, native=False,
                 stdout_limit=32 * MIB, stderr_limit=2 * MIB, binding=None):
        self.folder = mkdir(folder)
        self.argv = [str(x) for x in argv]
        self.native = native
        self.timeout = timeout
        self.started = time.monotonic()
        self.overflow = threading.Event()
        self.errors = []
        self.counts = [0, 0]
        self.limits = [stdout_limit, stderr_limit]
        self.threads = []
        self.process = None
        self.outcome = None
        self.streams = [(self.folder / name).open("xb") for name in ("stdout.log", "stderr.log")]
        for name in ("stdout.log", "stderr.log"):
            os.chmod(self.folder / name, 0o600)
        write_json(self.folder / "process-intent.json", {
            "timestamp": utc(), "state": "UNKNOWN_UNTIL_OUTCOME", "argv": self.argv,
            "native_effect_attempt": native, "timeout_seconds": timeout,
            "stream_byte_limits": self.limits, "binding": binding,
            "retry_performed": False, "process_started": None,
        })
        try:
            self.process = subprocess.Popen(self.argv, cwd=folder, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, start_new_session=True)
            write_json(self.folder / "process-started.json", {"timestamp": utc(), "pid": self.process.pid,
                       "state": "RUNNING_OUTCOME_NOT_ESTABLISHED", "native_effect_attempt": native})
            for index, source in enumerate((self.process.stdout, self.process.stderr)):
                thread = threading.Thread(target=self._drain, args=(source, index), daemon=True)
                self.threads.append(thread)
                thread.start()
        except BaseException:
            self.stop(signal.SIGKILL)
            for stream in self.streams:
                stream.close()
            raise

    def stop(self, sig=signal.SIGKILL):
        if self.process is not None:
            try:
                os.killpg(self.process.pid, sig)
            except ProcessLookupError:
                pass

    def _drain(self, source, index):
        target, maximum = self.streams[index], self.limits[index]
        try:
            while chunk := source.read(min(65536, maximum - self.counts[index] + 1)):
                accepted = min(len(chunk), maximum - self.counts[index])
                require(target.write(chunk[:accepted]) == accepted, "short process log write")
                self.counts[index] += accepted
                if accepted != len(chunk):
                    self.overflow.set()
                    self.stop()
                    break
        except BaseException as error:
            self.errors.append(type(error).__name__)
            self.stop()
        finally:
            source.close()
            try:
                target.flush()
            except OSError as error:
                self.errors.append(type(error).__name__)
                self.stop()

    def wait(self, *, owner_shutdown=False):
        timed_out = False
        try:
            self.process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.stop()
        # This journal precedes pipe joining, byte hashing and semantic checks.
        write_json(self.folder / "process-outcome.json", {
            "timestamp": utc(), "original_exit_code": self.process.poll(), "timed_out": timed_out,
            "native_effect_attempt": self.native, "owner_shutdown": owner_shutdown,
            "semantic_result": "NOT_ASSESSED", "streams_finalized": False,
            "native_outcome": "UNKNOWN" if self.native and timed_out else "NOT_ASSESSED",
        })
        cleanup_incomplete = False
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cleanup_incomplete = True
        for thread in self.threads:
            thread.join(timeout=5)
        cleanup_incomplete |= any(thread.is_alive() for thread in self.threads)
        row = {"exit_code": self.process.poll(), "timed_out": timed_out,
               "overflow": self.overflow.is_set(), "pipe_errors": self.errors,
               "cleanup_incomplete": cleanup_incomplete, "captured_bytes": self.counts,
               "prefix_only": self.overflow.is_set(), "elapsed_seconds": time.monotonic() - self.started,
               "owner_shutdown": owner_shutdown, "logs": None}
        if not cleanup_incomplete:
            for stream in self.streams:
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
            row["logs"] = [file_record(self.folder / name, maximum, empty=True)
                           for name, maximum in zip(("stdout.log", "stderr.log"), self.limits)]
        row["capture_complete"] = not (timed_out or row["overflow"] or self.errors or cleanup_incomplete)
        row["command_succeeded"] = row["capture_complete"] and type(row["exit_code"]) is int and row["exit_code"] == 0
        write_json(self.folder / "capture.json", row)
        self.outcome = row
        return row


def command(folder, argv, env, timeout=60, *, native=False, binding=None,
            stdout_limit=32 * MIB, stderr_limit=2 * MIB):
    process = Capture(folder, argv, env, timeout, native=native, binding=binding,
                      stdout_limit=stdout_limit, stderr_limit=stderr_limit)
    row = process.wait()
    if not row["command_succeeded"]:
        raise AttemptError("command did not complete: " + folder.name,
                           unknown=native or not row["capture_complete"])
    return read_bytes(folder / "stdout.log", stdout_limit, empty=True)


def runtime_inputs(args):
    require(git(args.semwright, "rev-parse", "HEAD").decode().strip() == SEMWRIGHT_PIN, "Semwright checkout is not pinned")
    require(not git(args.semwright, "status", "--porcelain", "--untracked-files=all"), "Semwright source dirty")
    require(not git(args.semwright, "ls-files", "--others", "--ignored", "--exclude-standard"), "ignored Semwright input")
    result = {}
    for name, path in {
        "seed": args.motionwright_bins / "examples/native-manim-camera-seed",
        "export": args.motionwright_bins / "examples/ci-manim-camera-plan",
        "realize": args.motionwright_bins / "examples/native-manim-e2e",
        "driver": args.motionwright_bins / "motionwright-manim-community-driver",
        "runner": args.motionwright_bins / "motionwright-manim-runner",
        "broker": args.semwright_bins / "semwrightd", "cli": args.semwright_bins / "semwright",
        "sandbox": args.semwright_bins / "semwright-sandbox",
        "python": args.python, "ffmpeg": args.ffmpeg, "ffprobe": args.ffprobe,
        "bwrap": Path(shutil.which("bwrap") or "/missing-bwrap").resolve(strict=True),
        "collector_python": Path(sys.executable).resolve(strict=True),
    }.items():
        record = file_record(path, 512 * MIB)
        require(os.access(path, os.X_OK), "nonexecutable runtime input: " + name)
        result[name] = record
    require(read_json(args.runtime / "runtime.json") == {"schema": 1, "manim_version": MANIM_VERSION}, "runtime version marker changed")
    plain(args.runtime / "site-packages")
    require((args.runtime / "site-packages").is_dir(), "runtime site-packages absent")
    return result


def inventory(root, maximum=2048 * MIB, *, maximum_files=30000):
    rows, total = {}, 0
    for directory, names, files in os.walk(root, followlinks=False):
        for name in names:
            require(not (Path(directory) / name).is_symlink(), "inventory directory link")
        for name in sorted(files):
            path = Path(directory) / name
            record = file_record(path, maximum, empty=True)
            total += record["bytes"]
            require(total <= maximum and len(rows) < maximum_files, "inventory byte/file budget")
            rows[path.relative_to(root).as_posix()] = {k: record[k] for k in ("bytes", "sha256")}
    return rows


def prepare_owner(args, work, tools, env):
    paths = {name: mkdir(work / name) for name in
             ("runtime", "state", "home", "config", "bin", "work", "output", "motionwright-data", "tmp")}
    driver = paths["bin"] / "motionwright-manim-community-driver"
    runner = paths["bin"] / "motionwright-manim-runner"
    for key, destination in (("driver", driver), ("runner", runner)):
        write_bytes(destination, read_bytes(Path(tools[key]["path"]), 512 * MIB))
        destination.chmod(0o700)
        require(file_record(destination, 512 * MIB)["sha256"] == tools[key]["sha256"], "copied executable identity")
    grants = [("manim-runtime", args.runtime, False), ("manim-work", paths["work"], True),
              ("manim-output", paths["output"], True), ("fontconfig", Path("/etc/fonts"), False),
              ("manim-runner-root", runner, False), ("python-root", args.python, False),
              ("ffmpeg-root", args.ffmpeg, False)]
    mounts = [name for name, _, _ in grants[:4]]
    manifest = {
        "manifest_version": 1, "protocol": 8, "id": "manim-community", "version": "0.1.0",
        "publisher": "motionwright", "executable": str(driver), "sha256": tools["driver"]["sha256"],
        "application": {"desktop_id": None, "process_names": ["motionwright-manim-runner", "python3", "ffmpeg"],
                        "supported_versions": ["Manim Community " + MANIM_VERSION]},
        "transport": "stdio_v1", "mounts": [{"root": n, "read_only": not w} for n, _, w in grants[:4]],
        "system_config": [], "secrets": [], "network": False, "loopback_port": None,
        "tools": [{"root": "manim-runner-root", "name": "manim-runner", "sha256": tools["runner"]["sha256"],
                   "mounts": mounts, "dependencies": ["python3", "ffmpeg"]},
                  {"root": "python-root", "name": "python3", "sha256": tools["python"]["sha256"], "mounts": [], "dependencies": []},
                  {"root": "ffmpeg-root", "name": "ffmpeg", "sha256": tools["ffmpeg"]["sha256"], "mounts": [], "dependencies": []}],
        "resources": {"open_files": 512, "processes": 256, "cpu_seconds": 300, "operation_cpu_seconds": 0,
                      "address_space_bytes": 4294967296, "file_size_bytes": 1073741824},
        "request_timeout_ms": 300000,
        "interfaces": {"dynamic_capabilities": False, "cooperative_cancellation": True, "events": False,
                       "health": True, "progress": False, "artifacts": False, "native_refs": False, "host_tools": True},
    }
    manifest_path = paths["config"] / "manim-driver.json"
    write_json(manifest_path, manifest)
    lines = [f"drivers = [{json.dumps(str(manifest_path))}]", "driver_network = false", "",
             "[policy]", 'profile = "workspace"', 'allow = ["driver:manim-community"]']
    for name, path, writable in grants:
        plain(path)
        lines += ["", "[[policy.filesystem]]", "name = " + json.dumps(name),
                  "path = " + json.dumps(str(path)), "read = true", "write = " + str(writable).lower()]
    write_bytes(paths["config"] / "owner.toml", ("\n".join(lines) + "\n").encode())
    env.update(HOME=str(paths["home"]), XDG_RUNTIME_DIR=str(paths["runtime"]),
               XDG_STATE_HOME=str(paths["state"]), RUST_BACKTRACE="1", SEMWRIGHT_TEST_DRIVER_SANDBOX="1",
               TMPDIR=str(paths["tmp"]), TMP=str(paths["tmp"]), TEMP=str(paths["tmp"]),
               SEMWRIGHT_TEST_SANDBOX_HELPER=tools["sandbox"]["path"])
    return paths


def capabilities(paths, tools, env, processes, binding):
    socket, session = paths["runtime"] / "broker.sock", paths["runtime"] / "motionwright.session"
    offset, revision, rows = 0, None, []
    for page_number in range(8):
        raw = command(processes / f"catalog-{page_number}", [tools["cli"]["path"], "--socket", socket,
            "--session-file", session, "--json", "capabilities", "search", "", "--provider",
            "driver:manim-community", "--limit", "100", "--offset", str(offset)], env, binding=binding)
        data = json.loads(raw)["data"]
        require(type(data) is dict and type(data.get("capabilities")) is list, "invalid capability page")
        if revision is None:
            revision = data.get("revision")
            require(revision is not None, "missing capability revision")
        require(data.get("revision") == revision, "capabilities changed during discovery")
        rows += data["capabilities"]
        next_offset = data.get("next_offset")
        if next_offset is None:
            break
        require(type(next_offset) is int and next_offset > offset, "invalid capability cursor")
        offset = next_offset
    else:
        raise Refused("capability pagination exceeded closed budget")
    ids = {row.get("id") for row in rows}
    required = {"driver.manim-community." + name for name in
                ("doctor", "camera.render.start", "render.status", "render.cancel", "render.result")}
    require(required <= ids, "original camera Host capability missing")
    require(not any(any(token in value.lower() for token in ("python", "shell", "exec"))
                    for value in ids if isinstance(value, str)), "unexpected code execution capability")
    require(stat.S_IMODE(plain(session).stat().st_mode) == 0o600, "session ticket is not private")
    return {"catalog_revision": revision, "required_capabilities": sorted(required), "pages": page_number + 1}


def copy_original(source, destination, maximum):
    before = file_record(source, maximum)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    plain(destination.parent)
    with source.open("rb") as reader, destination.open("xb") as writer:
        os.chmod(destination, 0o600)
        total = 0
        while chunk := reader.read(min(MIB, maximum - total + 1)):
            total += len(chunk)
            require(total <= maximum, "copy source byte budget")
            require(writer.write(chunk) == len(chunk), "short original copy")
        writer.flush()
        os.fsync(writer.fileno())
    after, copied = file_record(source, maximum), file_record(destination, maximum)
    require(before == after and all(copied[k] == before[k] for k in ("bytes", "sha256")), "original changed during custody copy")
    return {"original": before, "retained": copied}


def source_export(args, processes, database, seed_file, destination, env, binding):
    command(processes / destination.name, [args.motionwright_bins / "examples/ci-manim-camera-plan",
            database, seed_file, destination], env, binding=binding)
    report = read_json(destination / "source-export.json")
    require(report["state"] == "ORIGINAL_COMPILER_BYTES_EXPORTED_SOURCE_ONLY", "source export did not complete")
    for key, name in (("plan", "plan.json"), ("compiled_source", "compiled-source.py")):
        physical = file_record(destination / name, 256 * 1024)
        require(all(report[key][field] == physical[field] for field in ("bytes", "sha256")), "compiler original bytes differ")
    require(report["native_dispatch_performed"] is False and report["provider_grant_created"] is False,
            "source export claimed native authority")
    return report


def joined_native(native, exported, seed, request_id):
    require(native.get("native_manim_camera_dispatch") == "PASS", "original coordinator did not retain successful dispatch")
    for name in ("project_id", "generation", "revision", "scene_id", "deliverable_id"):
        require(native.get(name) == seed[name], "seed/coordinator identity mismatch: " + name)
    require(native.get("request_id") == request_id, "original request identity mismatch")
    require(native.get("resource") == exported["canonical_stamp"]["resource"], "original resource identity mismatch")
    result = native["result"]
    require(result["renderer"] == "manim-community" and result["native_driver"] == "driver:manim-community"
            and result["runtime_contract"] == "Manim Community 0.21.0"
            and result["plan_sha256"] == exported["plan"]["sha256"]
            and result["profile"] == PROFILE, "original native source/profile join failed")
    require(re.fullmatch(r"manim-[0-9a-f]{32}", result["job_ref"]) is not None, "unbounded native job reference")
    for key, suffix, command_name in (("original_start_receipt", ":start", "camera.render.start"),
                                      ("original_result_receipt", ":result", "render.result")):
        row = native[key]
        require(row["request_id"] == request_id + suffix and row["stage"] == "completed"
                and row["command"] == "driver.manim-community." + command_name,
                "original start/result receipt lifecycle mismatch")
        for field in ("project_id", "generation", "revision"):
            require(row[field] == seed[field], "receipt source identity mismatch")
    require(native["decoded_frames_verified"] is False, "producer conflated physical reader result")
    return result


def physical_readback(args, result, exported, before, paths, evidence, processes, env, binding):
    token = result["job_ref"][6:]
    relative = result["artifact"]["relative_path"]
    expected_relative = f"videos/mw_{token}/360p30/mw_{token}.mp4"
    require(relative == expected_relative and PurePosixPath(relative).as_posix() == relative,
            "artifact is outside the original single-job output path")
    original_media = paths["output"] / relative
    artifact = result["artifact"]
    media_record = file_record(original_media, 512 * MIB)
    require(all(media_record[key] == artifact[key] for key in ("sha256", "bytes")), "original media differs from Broker receipt")
    source = paths["work"] / f"mw_{token}.py"
    getters = paths["work"] / f"mw_{token}.camera.json"
    require(file_record(source, 256 * 1024)["sha256"] == exported["compiled_source"]["sha256"], "Host executed source differs from exported compiler bytes")
    require(file_record(getters, 8 * MIB)["sha256"] == artifact["camera_getters_sha256"], "original getter digest differs from Broker artifact")
    getter_data = read_json(getters, 8 * MIB)
    require(getter_data.get("plan_sha256") == exported["plan"]["sha256"]
            and getter_data.get("binding") == exported["binding"]
            and type(getter_data.get("sample_count")) is int and getter_data["sample_count"] == 120
            and len(getter_data.get("frames", [])) == 120
            and getter_data.get("decoded_video_verified") is False
            and getter_data.get("native_grants_verified_by_this_script") is False,
            "original getter count/binding/scope changed")
    retained = mkdir(evidence / "physical-originals")
    copies = {"media": copy_original(original_media, retained / "master.mp4", 512 * MIB),
              "generated_source": copy_original(source, retained / "generated-source.py", 256 * 1024),
              "getters": copy_original(getters, retained / "camera-getters.json", 8 * MIB)}
    write_json(retained / "custody.json", copies)
    media, plan = retained / "master.mp4", before / "plan.json"
    stable = {"media": file_record(media, 512 * MIB), "plan": file_record(plan, 256 * 1024)}
    # This unselected stream query closes the original reader's v:0-only gap,
    # and retains all 120 original presentation timestamps before its oracle.
    raw = command(processes / "physical-full-probe", [args.ffprobe, "-v", "error", "-show_streams",
                  "-show_frames", "-show_format", "-of", "json", media], env, timeout=60,
                  stdout_limit=8 * MIB, binding=binding)
    probe = json.loads(raw)
    require(type(probe.get("streams")) is list and len(probe["streams"]) == 1
            and probe["streams"][0].get("codec_type") == "video", "master must have exactly one total stream")
    require(probe["streams"][0].get("codec_name") == "h264", "master codec differs")
    # The original reader remains byte-for-byte unchanged. A fresh isolated process
    # bounds memory for its pipe capture; errors or limits cannot become PASS.
    bootstrap = ("import resource,runpy,sys\n"
                 "resource.setrlimit(resource.RLIMIT_AS,(1610612736,1610612736))\n"
                 "resource.setrlimit(resource.RLIMIT_FSIZE,(8388608,8388608))\n"
                 "resource.setrlimit(resource.RLIMIT_CPU,(180,180))\n"
                 "sys.argv=sys.argv[1:]\nrunpy.run_path(sys.argv[0],run_name='__main__')\n")
    reader_receipt = retained / "original-pixel-reader.json"
    command(processes / "original-pixel-reader", [sys.executable, "-B", "-I", "-S", "-c", bootstrap,
        args.repo / "tooling/native_manim_camera_pixels.py", "--plan", plan,
        "--plan-sha256", exported["plan"]["sha256"], "--media", media,
        "--media-sha256", artifact["sha256"], "--ffprobe", args.ffprobe,
        "--ffmpeg", args.ffmpeg, "--receipt", reader_receipt], env, timeout=210, binding=binding)
    reader = read_json(reader_receipt)
    require(reader["source_bound_video_observation"] == "PASS"
            and reader["compiled_plan_sha256"] == exported["plan"]["sha256"]
            and reader["trusted_media_sha256"] == artifact["sha256"]
            and reader["binding"] == exported["binding"], "original pixel result lost source binding")
    require(reader["video"]["frame_count"] == 120 and reader["video"]["frame_rate"] == "30/1", "physical frame clock differs")
    # A second *read-only decode*, not a second render, retains the exact three
    # samples needed to reproduce the original centroid observation.
    command(processes / "retain-three-rgb-frames", [args.ffmpeg, "-nostdin", "-v", "error", "-i", media,
        "-map", "0:v:0", "-an", "-vf", "select=eq(n\\,0)+eq(n\\,60)+eq(n\\,119)",
        "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        env, timeout=120, stdout_limit=3 * 640 * 360 * 3, binding=binding)
    raw_frames = processes / "retain-three-rgb-frames/stdout.log"
    require(file_record(raw_frames, 3 * 640 * 360 * 3)["bytes"] == 3 * 640 * 360 * 3,
            "physical RGB samples incomplete")
    require(stable == {"media": file_record(media, 512 * MIB), "plan": file_record(plan, 256 * 1024)},
            "readback inputs changed across original oracle")
    require(file_record(original_media, 512 * MIB) == media_record, "original master changed during readback")
    return {"original_pixel_reader": file_record(reader_receipt, MIB),
            "all_pts_original_log": file_record(processes / "physical-full-probe/stdout.log", 8 * MIB),
            "rgb_samples": file_record(raw_frames, 3 * 640 * 360 * 3),
            "sample_frame_indices": [0, 60, 119], "rgb24_bytes_per_frame": 640 * 360 * 3,
            "oracle": "original centroid ±3 px/color ±38, horizontal sample motion; unchanged",
            "physical_frame_count": 120, "fresh_native_render_attempts": 1, **LIMITATIONS}


def retain_attempt(paths, evidence):
    """Copy only technical synthetic inputs/output; never config/state/session.

    Incomplete/unstable originals are identified individually. Nothing in this
    failure custody path opens SQLite, executes scene Python or resumes a job.
    """
    destination = mkdir(evidence / "attempt-originals")
    rows, failures, ignored, total = [], [], [], 0
    for key, root, allowed, limit in (
        ("database", paths["motionwright-data"], lambda rel: rel in {
            "motionwright.sqlite3", "motionwright.sqlite3-wal", "motionwright.sqlite3-shm"}, 64 * MIB),
        ("work", paths["work"], lambda rel: re.fullmatch(r"mw_[0-9a-f]{32}\.(py|camera\.json)", rel) is not None, 8 * MIB),
        ("output", paths["output"], lambda rel: rel.endswith(".mp4") and rel.startswith("videos/mw_"), 512 * MIB),
    ):
        for folder, names, files in os.walk(root, followlinks=False):
            for name in list(names):
                if (Path(folder) / name).is_symlink():
                    names.remove(name)
                    failures.append({"root": key, "path": name, "reason": "directory_symlink"})
            for name in sorted(files):
                path = Path(folder) / name
                rel = path.relative_to(root).as_posix()
                if not allowed(rel):
                    ignored.append({"root": key, "path": rel, "reason": "outside_closed_technical_allowlist"})
                    continue
                try:
                    info = file_record(path, limit)
                    total += info["bytes"]
                    require(total <= 1024 * MIB and len(rows) < 256, "technical custody budget exceeded")
                    rows.append(copy_original(path, destination / key / rel, limit))
                except (OSError, Refused) as error:
                    failures.append({"root": key, "path": rel, "reason": str(error)})
    receipt = {"schema": "motionwright.camera120.attempt-custody/1", "originals": rows,
               "failures": failures, "excluded": ignored, "complete_for_selected_existing_files": not failures,
               "whole_runner_custody": False, "config_state_session_socket_excluded": True,
               "unknown_job_replayed": False, "sqlite_opened_by_custody": False, **LIMITATIONS}
    write_json(destination / "custody.json", receipt)
    return receipt


def run(args):
    ctx = context()
    require(ctx["GITHUB_RUN_ATTEMPT"] == "1", "native reruns are forbidden; inspect the original attempt")
    require(ctx["GITHUB_SHA"] == args.expected_source, "event SHA differs from composed source")
    require(ctx["GITHUB_WORKFLOW_SHA"] == args.expected_source, "workflow/source must be the same composed commit")
    binding = {**source_identity(args.repo, args.expected_source), "execution_context": ctx}
    require(not args.output.is_relative_to(args.work) and not args.work.is_relative_to(args.output)
            and not args.output.is_relative_to(args.repo) and not args.work.is_relative_to(args.repo), "outputs must be separated outside source")
    evidence, private = mkdir(args.output), mkdir(args.work)
    write_json(evidence / "start.json", {"timestamp": utc(), "state": "NOT_STARTED", **binding,
               "native_attempts": 0, "synthetic_fixture": "saved one rectangle, 120 frames, 30 fps", **LIMITATIONS})
    processes = mkdir(evidence / "processes")
    # Pass no CI authentication material to Broker, Host or native executables.
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "LANG", "LC_ALL", "TZ", "LD_LIBRARY_PATH"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", RUST_BACKTRACE="1",
               GITHUB_ACTIONS="true", CI="true")
    status, native_outcome, error, paths, daemon = "FAIL", "NOT_RUN", None, None, None
    tools = None
    try:
        tools = runtime_inputs(args)
        runtime_before = inventory(args.runtime)
        write_json(evidence / "tools-and-runtime.json", {"tools": tools, "runtime": runtime_before,
                   "semwright_sha": SEMWRIGHT_PIN, "manim_version": MANIM_VERSION})
        paths = prepare_owner(args, private, tools, env)
        version_env = dict(env, PYTHONPATH=str(args.runtime / "site-packages"))
        version = command(processes / "manim-version", [args.python, "-B", "-s", "-c",
            "import manim,sys;sys.stdout.write(str(manim.__version__))"], version_env,
            timeout=60, stdout_limit=1024, binding=binding)
        require(version.decode() == MANIM_VERSION, "actual Manim runtime version differs")
        for name in ("ffmpeg", "ffprobe"):
            command(processes / (name + "-version"), [tools[name]["path"], "-version"], env,
                    timeout=15, stdout_limit=64 * 1024, binding=binding)
        database = paths["motionwright-data"] / "motionwright.sqlite3"
        seed_file = evidence / "seed.original.json"
        seed_raw = command(processes / "seed", [tools["seed"]["path"], database], env, binding=binding)
        write_bytes(seed_file, seed_raw)
        seed = json.loads(seed_raw)
        before = evidence / "source-before"
        exported = source_export(args, processes, database, seed_file, before, env, binding)
        copy_original(database, evidence / "database-before.sqlite3", 64 * MIB)
        socket = paths["runtime"] / "broker.sock"
        daemon = Capture(processes / "broker", [tools["broker"]["path"], "--config",
                         paths["config"] / "owner.toml", "--socket", socket], env, 20,
                         stdout_limit=8 * MIB, stderr_limit=8 * MIB, binding=binding)
        deadline = time.monotonic() + 45
        while not socket.is_socket():
            require(daemon.process.poll() is None and not daemon.overflow.is_set(), "Broker exited before owner socket")
            require(time.monotonic() < deadline, "Broker owner socket timeout")
            time.sleep(0.05)
        catalog = capabilities(paths, tools, env, processes, binding)
        write_json(evidence / "capabilities-checked.json", catalog)
        connection = paths["config"] / "motionwright-connection.json"
        write_json(connection, {"schema": "motionwright-semwright-connection/1",
            "executable": tools["cli"]["path"], "executable_sha256": tools["cli"]["sha256"],
            "socket": str(socket), "session_file": str(paths["runtime"] / "motionwright.session"),
            "output_root": str(paths["output"]), "resource": exported["canonical_stamp"]["resource"]})
        request_id = "camera120-" + ctx["GITHUB_RUN_ID"] + "-" + ctx["GITHUB_RUN_ATTEMPT"]
        native_path = evidence / "coordinator.original.json"
        native_outcome = "UNKNOWN"
        command(processes / "native-attempt", [tools["realize"]["path"], "realize-camera", database,
            connection, native_path, seed["project_id"], seed["scene_id"], seed["deliverable_id"], request_id],
            env, timeout=420, native=True, binding={**binding, "request_id": request_id,
            "original_plan_sha256": exported["plan"]["sha256"]})
        native = read_json(native_path)
        result = joined_native(native, exported, seed, request_id)
        native_outcome = "SUCCEEDED_ORIGINAL_COORDINATOR_RECEIPT"
        after = source_export(args, processes, database, seed_file, evidence / "source-after", env, binding)
        for field in ("canonical_stamp", "binding", "source_project_sha256", "source_plan_compact_sha256", "plan", "compiled_source"):
            require(after[field] == exported[field], "saved source changed across native attempt: " + field)
        physical = physical_readback(args, result, exported, before, paths, evidence, processes, env, binding)
        write_json(evidence / "physical-observation.json", physical)
        require(runtime_inputs(args) == tools and inventory(args.runtime) == runtime_before, "runtime changed during attempt")
        require(source_identity(args.repo, args.expected_source) == {k: binding[k] for k in ("source_sha", "tree_sha", "source_inputs")},
                "source changed during attempt")
        status = "PASS_SCOPED_CAMERA120_OBSERVATION"
    except BaseException as exception:
        error = {"type": type(exception).__name__, "message": str(exception)}
        status = "UNKNOWN" if native_outcome == "UNKNOWN" else "FAIL"
        diagnostic = evidence / "coordinator.original.diagnostic.json"
        if diagnostic.is_file():
            try:
                original = read_json(diagnostic)
                if original.get("native_manim_camera_dispatch") == "FAIL" and original.get("error", {}).get("outcome_known") is True:
                    native_outcome, status = "FAILED_KNOWN_ORIGINAL_DIAGNOSTIC", "FAIL"
            except (OSError, ValueError, Refused):
                pass
    finally:
        daemon_report = None
        if daemon is not None:
            try:
                exited_before_shutdown = daemon.process.poll()
                daemon.stop(signal.SIGTERM)
                daemon_report = daemon.wait(owner_shutdown=True)
                daemon_report["exit_observed_before_owner_shutdown"] = exited_before_shutdown
                if exited_before_shutdown is not None:
                    daemon_report["unexpected_broker_exit"] = True
                    status = "UNKNOWN" if native_outcome == "UNKNOWN" else "FAIL"
            except BaseException as cleanup_error:
                daemon_report = {"capture_complete": False, "cleanup_error": type(cleanup_error).__name__}
            if not daemon_report["capture_complete"]:
                status = "UNKNOWN" if native_outcome == "UNKNOWN" else "FAIL"
        try:
            custody = retain_attempt(paths, evidence) if paths else None
        except BaseException as custody_error:
            custody = {"complete_for_selected_existing_files": False,
                       "failure": {"type": type(custody_error).__name__, "message": str(custody_error)}}
            write_json(evidence / "custody-error.json", custody)
        if custody and not custody["complete_for_selected_existing_files"]:
            status = "UNKNOWN" if native_outcome == "UNKNOWN" else "FAIL"
        write_json(evidence / "terminal.json", {"timestamp": utc(), "state": status,
            "native_outcome": native_outcome, "error": error, "binding": binding,
            "fresh_native_render_attempts": int(native_outcome != "NOT_RUN"),
            "retry_performed": False, "broker_cleanup": daemon_report,
            "technical_custody_complete": custody is not None and custody["complete_for_selected_existing_files"],
            "source_before_and_after_recorded": (evidence / "source-after/source-export.json").is_file(),
            "physical_receipt_present": (evidence / "physical-observation.json").is_file(), **LIMITATIONS})
    return 0 if status == "PASS_SCOPED_CAMERA120_OBSERVATION" else 2 if status == "UNKNOWN" else 1


def ticket_patterns(owner_work):
    """Detect original ticket bytes/credential values without writing them out."""
    session = owner_work / "runtime/motionwright.session"
    if not session.exists():
        return []
    raw = read_bytes(session, 64 * 1024)
    patterns = {raw, raw.strip()}
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        value = None
    def visit(node, key=""):
        if type(node) is dict:
            for child_key, child in node.items():
                visit(child, child_key)
        elif type(node) is list:
            for child in node:
                visit(child, key)
        elif type(node) is str and any(part in key.lower() for part in ("token", "secret", "ticket", "credential", "bearer", "private_key")):
            patterns.add(node.encode())
    visit(value)
    return [item for item in patterns if len(item) >= 16]


def contains_ticket(path, patterns):
    if not patterns:
        return False
    overlap = max(map(len, patterns)) - 1
    previous = b""
    with path.open("rb") as stream:
        while block := stream.read(MIB):
            combined = previous + block
            if any(pattern in combined for pattern in patterns):
                return True
            previous = combined[-overlap:]
    return False


def finalize(args):
    """Stage exact technical bytes even after a failed attempt; never promote it."""
    require(not args.output.is_relative_to(args.evidence)
            and not args.evidence.is_relative_to(args.output)
            and not args.output.is_relative_to(args.owner_work)
            and not args.owner_work.is_relative_to(args.output), "technical publication roots must be separated")
    destination = mkdir(args.output)
    quarantine = mkdir(args.output.with_name(args.output.name + "-quarantine"))
    rows, exclusions = [], []
    total = 0
    patterns = ticket_patterns(args.owner_work)
    # The sole input is the technical evidence directory created by run(), not
    # the separate owner work root. Config, tickets, sockets and state are absent.
    if args.evidence.exists():
        for folder, names, files in os.walk(plain(args.evidence), followlinks=False):
            for name in names:
                require(not (Path(folder) / name).is_symlink(), "technical directory symlink")
            for name in sorted(files):
                path = Path(folder) / name
                relative = path.relative_to(args.evidence)
                dest = destination / "originals" / relative
                try:
                    record = file_record(path, 512 * MIB, empty=True)
                    total += record["bytes"]
                    require(total <= 1536 * MIB and len(rows) < 2048, "staging byte/file budget")
                    candidate = quarantine / relative
                    candidate.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if record["bytes"] == 0:
                        write_bytes(candidate, b"")
                    else:
                        staged = copy_original(path, candidate, 512 * MIB)
                        require(staged["original"] == record, "original_changed_before_private_staging")
                    # Scan the exact private copy, never an earlier version of
                    # the original. Quarantine is outside the always-upload root.
                    scanned = file_record(candidate, 512 * MIB, empty=True)
                    require(not contains_ticket(candidate, patterns), "original_ticket_material_detected_no_redaction")
                    require(file_record(candidate, 512 * MIB, empty=True) == scanned, "private_candidate_changed_during_scan")
                    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if scanned["bytes"] == 0:
                        write_bytes(dest, b"")
                        require(file_record(candidate, 0, empty=True) == scanned, "scanned_candidate_changed_before_publication")
                        published = file_record(dest, 0, empty=True)
                    else:
                        publication = copy_original(candidate, dest, 512 * MIB)
                        require(publication["original"] == scanned, "scanned_candidate_changed_before_publication")
                        published = publication["retained"]
                    require(all(published[k] == scanned[k] for k in ("bytes", "sha256")), "published_copy_differs_from_scanned_bytes")
                    rows.append({"original": record, "private_scanned_copy": scanned, "retained": published})
                except (OSError, Refused) as error:
                    # This is only an unadmitted copy. Keep original/quarantine
                    # bytes; remove any partial destination from the upload tree.
                    if dest.exists() or dest.is_symlink():
                        dest.unlink()
                    exclusions.append({"relative_path": relative.as_posix(), "reason": str(error)})
    write_json(destination / "artifact-custody.json", {"schema": "motionwright.camera120.artifact-custody/1",
               "timestamp": utc(), "execution_context": context(), "originals": rows, "exclusions": exclusions,
               "complete_for_technical_directory": args.evidence.exists() and not exclusions,
               "whole_runner_custody": False, "product_verdict": "NOT_GRANTED_BY_STAGING", **LIMITATIONS})
    return 1 if exclusions or not rows else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("source-preflight")
    preflight.add_argument("--repo", type=Path, required=True)
    preflight.add_argument("--expected-source", required=True)
    preflight.add_argument("--output", type=Path, required=True)
    execute = commands.add_parser("run")
    for name in ("repo", "semwright", "motionwright-bins", "semwright-bins", "runtime", "python", "ffmpeg", "ffprobe", "work", "output"):
        execute.add_argument("--" + name, type=Path, required=True)
    execute.add_argument("--expected-source", required=True)
    stage = commands.add_parser("finalize")
    stage.add_argument("--evidence", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)
    stage.add_argument("--owner-work", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "source-preflight":
        binding = source_identity(plain(args.repo), args.expected_source)
        observed = context()
        require(observed["GITHUB_SHA"] == args.expected_source
                and observed["GITHUB_WORKFLOW_SHA"] == args.expected_source, "composed workflow/source identity required")
        write_json(args.output, {"timestamp": utc(), **binding, "execution_context": observed, **LIMITATIONS})
        return 0
    if args.command == "finalize":
        return finalize(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
