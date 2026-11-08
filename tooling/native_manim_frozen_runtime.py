#!/usr/bin/env python3
"""Build once and retain the wheels used by a new four-phase Native cycle.

This does not reconstruct the lost historical runtime. Its contract is exact
reuse of the retained wheels and observed installation, not deterministic
rebuilding of the three source distributions. No project input is read here.
"""
from __future__ import annotations

import argparse
import email.parser
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile

MIB = 1024 * 1024
MAX_TAR = 128 * MIB
MAX_INPUT = 48 * MIB
MAX_INSTALLED = 512 * MIB
MAX_FILES = 8192
MAX_METADATA = 2 * MIB
MAX_PREPARATION_BYTES = 1536 * MIB
MAX_PREPARATION_FILES = 32768
LOCK_SHA = "023588b32c779906ad4e92d1ccbb861e63a04e4e224eaf96cd91e99b62a51789"
LOCK_PATH = Path(__file__).with_name("requirements") / "manim-runtime-observed-42357390508.json"
SHA = re.compile(r"[0-9a-f]{64}")
RUNTIME_METADATA = (
    "input-lock.json", "wheel-lock.json", "wheel-lock.requirements.txt", "installed-files.json",
    "download-originals.json", "installed-pip-report.json", "build-wheels.intent.json", "build-wheels.spawn.json",
    "build-wheels.terminal.json", "build-wheels.stdout.log", "build-wheels.stderr.log", "install-wheels.intent.json",
    "install-wheels.spawn.json", "install-wheels.terminal.json", "install-wheels.stdout.log", "install-wheels.stderr.log",
    "native-observation.json", "observe-native.intent.json", "observe-native.spawn.json", "observe-native.terminal.json",
    "observe-native.stdout.log", "observe-native.stderr.log", "runtime.json")


def need(value, code):
    if not value:
        raise ValueError(code)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "frozen_runtime_duplicate_json_key")
        result[key] = value
    return result


def strict(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite_json")))


def observation(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def regular(path, maximum, *, collect=False):
    path = Path(path)
    need(not path.is_symlink(), "frozen_runtime_symlink")
    def windows_names():
        if os.name == "nt":
            # NT's POSIX-looking mode bits do not describe the DACL. Host
            # callers first verify the protected private_root ACL; these reads
            # independently reject reparse aliases and unstable file identity.
            need(all(not item.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                     for item in (path, *path.parents)), "frozen_runtime_windows_reparse_alias")
    windows_names()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and (os.name == "nt" or not before.st_mode & 0o022) and 0 <= before.st_size <= maximum,
             "frozen_runtime_regular_file_bound")
        count, hashed, parts = 0, hashlib.sha256(), []
        while block := os.read(fd, MIB):
            count += len(block)
            need(count <= maximum, "frozen_runtime_file_grew")
            hashed.update(block)
            if collect:
                parts.append(block)
        need(count == before.st_size and observation(before) == observation(os.fstat(fd))
             == observation(path.stat(follow_symlinks=False)), "frozen_runtime_file_changed")
        windows_names()
        value = {"bytes": count, "sha256": hashed.hexdigest()}
        return (value, b"".join(parts)) if collect else value
    finally:
        os.close(fd)


def object_file(path, maximum=MAX_METADATA):
    _identity, raw = regular(path, maximum, collect=True)
    return strict(raw)


def write_new(path, raw):
    path = Path(path)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)
    need(regular(path, len(raw)) == {"bytes": len(raw), "sha256": digest(raw)},
         "frozen_runtime_write_readback")


def json_new(path, value):
    raw = encoded(value)
    need(len(raw) <= MAX_METADATA, "frozen_runtime_metadata_budget")
    write_new(path, raw)


def name_key(value):
    need(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value),
         "frozen_runtime_distribution_name")
    return re.sub(r"[-_.]+", "-", value).lower()


def input_lock():
    identity, raw = regular(LOCK_PATH, 128 * 1024, collect=True)
    need(identity["sha256"] == LOCK_SHA, "frozen_runtime_input_lock_changed")
    value = strict(raw)
    rows = value["entries"]
    need(value.get("schema") == "motionwright.manim.observed-runtime-inputs/1"
         and value.get("old_runtime_equivalence_claimed") is False
         and len(rows) == 32 and len({row["name"] for row in rows}) == 32
         and sum(row["kind"] == "wheel" for row in rows) == 29
         and {row["name"] for row in rows if row["kind"] == "sdist"} == {"manimpango", "pycairo", "srt"},
         "frozen_runtime_closed_input_roster")
    return value, raw


def relative_name(raw):
    path = PurePosixPath(raw)
    need(type(raw) is str and 0 < len(raw) <= 1024 and not path.is_absolute()
         and raw == str(path) and not {"", ".", ".."} & set(path.parts)
         and "\\" not in raw and ":" not in raw and "\0" not in raw,
         "frozen_runtime_relative_name")
    return raw


def wheel_metadata(path, expected):
    before = regular(path, MAX_INPUT)
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        need(0 < len(members) <= MAX_FILES and len({row.filename for row in members}) == len(members)
             and not archive.comment and sum(row.file_size for row in members) <= MAX_INSTALLED,
             "frozen_runtime_wheel_roster")
        for row in members:
            relative_name(row.filename.rstrip("/"))
            need(not row.flag_bits & 1 and not stat.S_ISLNK(row.external_attr >> 16)
                 and row.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                 and 0 <= row.file_size <= MAX_INSTALLED, "frozen_runtime_wheel_member")
        metadata = [row for row in members if re.fullmatch(r"[^/]+\.dist-info/METADATA", row.filename)]
        need(len(metadata) == 1 and metadata[0].file_size <= MIB, "frozen_runtime_wheel_metadata")
        raw = archive.read(metadata[0])
        value = email.parser.BytesParser().parsebytes(raw)
        need(name_key(value["Name"]) == expected["name"] and value["Version"] == expected["version"],
             "frozen_runtime_wheel_identity")
    need(regular(path, MAX_INPUT) == before, "frozen_runtime_wheel_changed_during_validation")
    return {**before, "expanded_bytes": sum(row.file_size for row in members),
            "expanded_files": sum(not row.is_dir() for row in members)}


def inventory(root):
    root = Path(root)
    need(root.is_dir() and not root.is_symlink() and root.resolve(strict=True) == root,
         "frozen_runtime_inventory_root")
    rows, total = [], 0
    for directory, directories, filenames in os.walk(root, followlinks=False):
        parent = Path(directory)
        need(all(not (parent / name).is_symlink() for name in directories),
             "frozen_runtime_inventory_linked_directory")
        for name in sorted(filenames):
            path = parent / name
            row = regular(path, MAX_INSTALLED)
            total += row["bytes"]
            need(total <= MAX_INSTALLED and len(rows) < MAX_FILES, "frozen_runtime_installed_budget")
            rows.append({"path": relative_name(path.relative_to(root).as_posix()), **row})
    return {"schema": "motionwright.manim.installed-runtime-files/1",
            "files": sorted(rows, key=lambda row: row["path"]), "file_count": len(rows), "total_bytes": total}


def secure_url(url, hosts):
    parsed = urllib.parse.urlsplit(url)
    need(parsed.scheme == "https" and parsed.hostname in hosts and parsed.port in (None, 443)
         and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
         "frozen_runtime_closed_https_source")


def download(url, destination, expected_sha, maximum):
    secure_url(url, {"files.pythonhosted.org", "pypi.org"})
    started, count, hashed = time.monotonic(), 0, hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Motionwright-runtime-custody/1"}),
                                timeout=20) as response, Path(destination).open("xb") as stream:
        secure_url(response.geturl(), {"files.pythonhosted.org", "pypi.org"})
        need(response.status == 200, "frozen_runtime_download_status")
        size = response.headers.get("Content-Length")
        if size is not None:
            need(size.isdecimal() and int(size) <= maximum, "frozen_runtime_download_declared_budget")
        while block := response.read(MIB):
            count += len(block)
            need(count <= maximum and time.monotonic() - started <= 90, "frozen_runtime_download_budget")
            hashed.update(block)
            stream.write(block)
        stream.flush()
        os.fsync(stream.fileno())
    Path(destination).chmod(0o600)
    need(expected_sha is None or hashed.hexdigest() == expected_sha, "frozen_runtime_download_hash")
    need(regular(destination, maximum) == {"bytes": count, "sha256": hashed.hexdigest()},
         "frozen_runtime_download_readback")
    return {"bytes": count, "sha256": hashed.hexdigest()}


def clean_environment(work, runtime=None):
    env = {key: os.environ[key] for key in ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL") if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PIP_CONFIG_FILE=os.devnull,
               PIP_INDEX_URL="https://pypi.org/simple", PIP_NO_INPUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
               TMPDIR=str(work / "build-temp"), PIP_CACHE_DIR=str(work / "build-cache"))
    if runtime is not None:
        env["PYTHONPATH"] = str(runtime / "site-packages")
    return env


def preparation_usage(roots):
    """Observe owned working files; the outer cgroup supplies the hard RAM bound."""
    total, count = 0, 0
    for root in roots:
        for directory, directories, filenames in os.walk(root, followlinks=False):
            parent = Path(directory)
            # Build backends may replace temporary entries while this watchdog scans.
            for name in [*directories, *filenames]:
                try:
                    value = (parent / name).lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode):
                    total += value.st_size
                    count += 1
                need(total <= MAX_PREPARATION_BYTES and count <= MAX_PREPARATION_FILES,
                     "frozen_runtime_preparation_watchdog_budget")
    return {"bytes": total, "files": count}


def command(work, label, argv, env, *, seconds=600, observed_roots=()):
    """Run once in an owned process group; settle it on every post-spawn exit.

    The disk watchdog is observational, not a filesystem quota. The surrounding
    exclusive cgroup remains responsible for hard RAM/PID limits and for any
    descendant which deliberately leaves this process group.
    """
    work = Path(work)
    need(sys.platform == "linux" and 0 < seconds <= 600, "frozen_runtime_command_platform_or_time")
    json_new(work / (label + ".intent.json"), {"argv": argv, "maximum_seconds": seconds,
             "stream_bytes_each": 4 * MIB, "owned_process_group": True,
             "preparation_watchdog_bytes": MAX_PREPARATION_BYTES,
             "preparation_watchdog_files": MAX_PREPARATION_FILES,
             "preparation_hard_disk_quota_claimed": False, "automatic_retry": False})
    exceeded = threading.Event()
    errors, threads, streams = [], [], []
    process = None
    first_error = None
    started = time.monotonic()
    terminated, term_sent, kill_sent, remaining_group = False, False, False, False
    peak = {"bytes": 0, "files": 0}
    code = None

    def capture(source, stream):
        try:
            count = 0
            while block := source.read(65536):
                count += len(block)
                if count > 4 * MIB:
                    exceeded.set()
                    break
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException as error:
            errors.append(type(error).__name__)
            exceeded.set()
        finally:
            source.close()

    def signal_group(number):
        try:
            os.killpg(process.pid, number)
            return True
        except ProcessLookupError:
            return False

    try:
        # Reserve both stream files before allowing a child to exist.
        for name in ("stdout", "stderr"):
            path = work / (label + "." + name + ".log")
            stream = path.open("xb")
            streams.append(stream)
            path.chmod(0o600)
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env, start_new_session=True, bufsize=0)
        for source, stream in zip((process.stdout, process.stderr), streams):
            thread = threading.Thread(target=capture, args=(source, stream), daemon=True)
            thread.start()
            threads.append(thread)
        json_new(work / (label + ".spawn.json"), {"pid": process.pid, "process_group": process.pid,
                                                 "automatic_retry": False})
        next_usage = started
        while process.poll() is None:
            if exceeded.is_set() or time.monotonic() - started >= seconds:
                terminated = True
                break
            if observed_roots and time.monotonic() >= next_usage:
                usage = preparation_usage(observed_roots)
                peak = {key: max(peak[key], usage[key]) for key in peak}
                next_usage = time.monotonic() + 0.5
            time.sleep(0.05)
    except BaseException as error:
        first_error = error
        terminated = process is not None
    finally:
        if process is not None:
            # A normal parent exit with a surviving group is also a failure.
            # This group was created by Popen; it contains no pre-existing owner.
            try:
                if process.poll() is not None:
                    remaining_group = signal_group(0)
                if terminated or remaining_group or process.poll() is None:
                    terminated = True
                    term_sent = signal_group(signal.SIGTERM)
                try:
                    code = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    kill_sent = signal_group(signal.SIGKILL)
                    code = process.wait(timeout=5)
                # Even if the parent has exited, terminate children which ignore TERM.
                if signal_group(0):
                    kill_sent = signal_group(signal.SIGKILL) or kill_sent
                for thread in threads:
                    thread.join(timeout=5)
            except BaseException as error:
                first_error = first_error or error
                try:
                    signal_group(signal.SIGKILL)
                except OSError:
                    pass
            finally:
                for source in (process.stdout, process.stderr):
                    if source is not None and not source.closed and not any(thread.is_alive() for thread in threads):
                        source.close()
        for stream in streams:
            if not any(thread.is_alive() for thread in threads):
                stream.close()
        terminal = {"spawned": process is not None, "exit_code": code, "terminated": terminated,
                    "process_group_term_sent": term_sent, "process_group_kill_sent": kill_sent,
                    "group_survived_parent_exit": remaining_group,
                    "stream_limit_exceeded": exceeded.is_set(), "stream_errors": errors,
                    "streams_settled": not any(thread.is_alive() for thread in threads),
                    "first_error_type": type(first_error).__name__ if first_error else None,
                    "preparation_observed_peak": peak, "preparation_hard_disk_quota_claimed": False,
                    "automatic_retry": False}
        try:
            json_new(work / (label + ".terminal.json"), terminal)
        except BaseException as error:
            first_error = first_error or error
    if first_error is not None:
        raise first_error
    need(code == 0 and not terminated and not exceeded.is_set() and not errors and terminal["streams_settled"],
         "frozen_runtime_command_failed:" + label)


def tar_originals(output, selected):
    need(len(selected) == len(set(selected)) and 0 < len(selected) <= 96, "frozen_runtime_tar_roster")
    identities = {relative_name(name): regular(path, MAX_INPUT if name.startswith("wheels/") else 4 * MIB)
                  for name, path in selected.items()}
    size = 1024 + sum(4096 + ((row["bytes"] + 511) // 512) * 512 for row in identities.values())
    # tarfile pads the final record to its 10240-byte blocking factor.
    need(((size + 10239) // 10240) * 10240 <= MAX_TAR, "frozen_runtime_tar_budget")
    with Path(output).open("xb") as stream:
        with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            for name, path in sorted(selected.items()):
                row = identities[name]
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode, entry.mtime = row["bytes"], 0o600, 0
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(fd, "rb") as source:
                    archive.addfile(entry, source)
                need(regular(path, MAX_INPUT if name.startswith("wheels/") else 4 * MIB) == row,
                     "frozen_runtime_archive_input_changed")
        stream.flush()
        os.fsync(stream.fileno())
    Path(output).chmod(0o600)
    archive_identity = regular(output, MAX_TAR)
    observed = {}
    with tarfile.open(output, mode="r|") as archive:
        for member in archive:
            need(member.name in identities and member.name not in observed and member.isreg()
                 and member.pax_headers in ({}, {"path": member.name})
                 and member.size == identities[member.name]["bytes"],
                 "frozen_runtime_tar_readback_roster")
            hashed = hashlib.sha256()
            source = archive.extractfile(member)
            for block in iter(lambda: source.read(MIB), b""):
                hashed.update(block)
            observed[member.name] = {"bytes": member.size, "sha256": hashed.hexdigest()}
    need(observed == identities and regular(output, MAX_TAR) == archive_identity,
         "frozen_runtime_tar_physical_readback")
    return archive_identity, identities


def native_observation(runtime, output):
    """Observe imported renderer libraries; this is not a render or all-system inventory."""
    import ctypes
    import platform
    import av
    import cairo
    import manim
    import manimpango
    packages = (Path(runtime) / "site-packages").resolve(strict=True)
    need(sys.platform == "linux" and os.geteuid() > 0
         and all(Path(module.__file__).resolve(strict=True).is_relative_to(packages)
                 for module in (av, cairo, manim, manimpango)), "frozen_runtime_native_imports_escaped")
    need(manim.__version__ == "0.21.0" and manimpango.__version__ == "0.6.1" and av.__version__ == "15.0.0",
         "frozen_runtime_renderer_versions")
    fontconfig = ctypes.CDLL("libfontconfig.so.1")
    fontconfig.FcGetVersion.restype = ctypes.c_int
    fontconfig_version = int(fontconfig.FcGetVersion())
    need(fontconfig_version > 0, "frozen_runtime_fontconfig_version")
    mapped = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or not fields[5].startswith("/"):
            continue
        need(not fields[5].endswith(" (deleted)"), "frozen_runtime_deleted_native_mapping")
        path = Path(fields[5]).resolve(strict=True)
        with path.open("rb") as stream:
            if stream.read(4) == b"\x7fELF":
                mapped.add(path)
    need(0 < len(mapped) <= 256, "frozen_runtime_native_mapping_roster")
    rows = [{"path": str(path), **regular(path, 128 * MIB)} for path in sorted(mapped)]
    need(sum(row["bytes"] for row in rows) <= MAX_INSTALLED, "frozen_runtime_native_mapping_bytes")
    value = {"schema": "motionwright.manim.imported-native-observation/1", "runtime_path": str(Path(runtime)),
             "python_version": sys.version, "machine": platform.machine(), "system": platform.system(),
             "manim_version": manim.__version__, "manimpango_version": manimpango.__version__,
             "pyav_version": av.__version__, "fontconfig_version": fontconfig_version,
             "libav_versions": {key: list(value) for key, value in sorted(av.library_versions.items())},
             "files": rows, "scope": "native_files_mapped_by_preparation_imports",
             "all_system_dependencies_claimed": False, "native_effect_invocations": 0}
    json_new(output, value)


def validate_receipt(receipt_path, manifest_path=None, wheel_lock_path=None):
    """Validate retained metadata bindings; this never claims to read the tar."""
    receipt_path = Path(receipt_path)
    manifest_path = Path(manifest_path) if manifest_path else receipt_path.with_name("installed-files.json")
    wheel_lock_path = Path(wheel_lock_path) if wheel_lock_path else receipt_path.with_name("wheel-lock.json")
    receipt = object_file(receipt_path)
    manifest = object_file(manifest_path)
    wheel_lock = object_file(wheel_lock_path)
    lock, _raw = input_lock()
    need(receipt.get("schema") == "motionwright.manim.frozen-runtime/1"
         and receipt.get("status") == "EXACT_WHEELS_AND_INSTALLED_FILES_RETAINED"
         and receipt.get("input_lock_sha256") == LOCK_SHA
         and receipt.get("old_runtime_equivalence_claimed") is False
         and receipt.get("sdist_rebuild_byte_reproducible_claimed") is False
         and receipt.get("automatic_retry") is False
         and receipt.get("archive_physical_readback") is True
         and receipt.get("native_effect_invocations") == 0 and receipt.get("project_inputs_read") is False
         and receipt.get("installed_manifest") == regular(manifest_path, MAX_METADATA)
         and receipt.get("wheel_lock") == regular(wheel_lock_path, MAX_METADATA), "frozen_runtime_receipt_chain")
    need(wheel_lock.get("schema") == "motionwright.manim.exact-wheel-lock/1"
         and wheel_lock.get("input_lock_sha256") == LOCK_SHA and len(wheel_lock.get("wheels", [])) == 32
         and wheel_lock.get("context") == receipt.get("context"),
         "frozen_runtime_wheel_lock_roster")
    current = receipt.get("context", {})
    need(set(current) == {"source_sha", "semwright_sha", "run_id", "run_attempt"}
         and all(type(current[name]) is str and re.fullmatch(r"[0-9a-f]{40}", current[name])
                 for name in ("source_sha", "semwright_sha"))
         and type(current["run_id"]) is str and current["run_id"].isdecimal()
         and int(current["run_id"]) > 0 and current["run_attempt"] == "1", "frozen_runtime_receipt_context")
    wheels = wheel_lock["wheels"]
    need(len({row["name"] for row in wheels}) == 32 and len({row["filename"] for row in wheels}) == 32,
         "frozen_runtime_duplicate_wheel")
    inputs = {row["name"]: row for row in lock["entries"]}
    need(set(inputs) == {row["name"] for row in wheels}, "frozen_runtime_wheel_name_changed")
    for row in wheels:
        expected = inputs[row["name"]]
        need(row["version"] == expected["version"] and row["input_sha256"] == expected["sha256"]
             and row["input_kind"] == expected["kind"] and SHA.fullmatch(row["sha256"])
             and type(row["bytes"]) is int and 0 < row["bytes"] <= MAX_INPUT
             and type(row["expanded_bytes"]) is int and 0 < row["expanded_bytes"] <= MAX_INSTALLED
             and type(row["expanded_files"]) is int and 0 < row["expanded_files"] <= MAX_FILES
             and Path(row["filename"]).name == row["filename"] and row["filename"].endswith(".whl")
             and (expected["kind"] == "sdist" or (row["sha256"] == expected["sha256"]
                  and row["filename"] == expected["filename"])), "frozen_runtime_wheel_original_identity")
        relative_name(row["filename"])
    need(sum(row["expanded_bytes"] for row in wheels) <= MAX_INSTALLED - 64 * MIB
         and sum(row["expanded_files"] for row in wheels) <= MAX_FILES - 256,
         "frozen_runtime_wheel_aggregate_expansion")
    rows = manifest.get("files", [])
    need(manifest.get("schema") == "motionwright.manim.installed-runtime-files/1"
         and 0 < len(rows) <= MAX_FILES and manifest.get("file_count") == len(rows)
         and len({row["path"] for row in rows}) == len(rows)
         and rows == sorted(rows, key=lambda row: row["path"])
         and manifest.get("total_bytes") == sum(row["bytes"] for row in rows) <= MAX_INSTALLED,
         "frozen_runtime_installed_manifest_roster")
    for row in rows:
        relative_name(row["path"])
        need(type(row["bytes"]) is int and 0 <= row["bytes"] <= MAX_INSTALLED and SHA.fullmatch(row["sha256"]),
             "frozen_runtime_installed_manifest_entry")
    archive = receipt.get("archive", {})
    members = receipt.get("archive_members", {})
    need(type(archive.get("bytes")) is int and 0 < archive["bytes"] <= MAX_TAR
         and SHA.fullmatch(archive.get("sha256", ""))
         and set(members) == {"wheels/" + row["filename"] for row in wheels}
                            | {"metadata/" + name for name in RUNTIME_METADATA}
         and members.get("metadata/wheel-lock.json") == receipt["wheel_lock"]
         and members.get("metadata/installed-files.json") == receipt["installed_manifest"]
         and members.get("metadata/input-lock.json") == {"bytes": len(_raw), "sha256": LOCK_SHA},
         "frozen_runtime_archive_binding")
    for name, row in members.items():
        relative_name(name)
        need(set(row) == {"bytes", "sha256"} and type(row["bytes"]) is int
             and 0 <= row["bytes"] <= (MAX_INPUT if name.startswith("wheels/") else 4 * MIB)
             and SHA.fullmatch(row["sha256"]), "frozen_runtime_archive_member_identity")
    for row in wheels:
        need(members.get("wheels/" + row["filename"]) == {key: row[key] for key in ("bytes", "sha256")},
             "frozen_runtime_archive_wheel_binding")
    native = receipt.get("native_observation", {})
    native_rows = native.get("files", [])
    need(native.get("schema") == "motionwright.manim.imported-native-observation/1"
         and native.get("runtime_path") == receipt.get("runtime_path")
         and native.get("python_version") == receipt.get("python_version")
         and native.get("scope") == "native_files_mapped_by_preparation_imports"
         and native.get("all_system_dependencies_claimed") is False
         and native.get("native_effect_invocations") == 0
         and native.get("system") == "Linux" and native.get("machine") == "x86_64"
         and native.get("manim_version") == "0.21.0" and native.get("manimpango_version") == "0.6.1"
         and native.get("pyav_version") == "15.0.0"
         and type(native.get("fontconfig_version")) is int and native["fontconfig_version"] > 0
         and 0 < len(native_rows) <= 256 and len({row["path"] for row in native_rows}) == len(native_rows)
         and members.get("metadata/native-observation.json") == {"bytes": len(encoded(native)), "sha256": digest(encoded(native))},
         "frozen_runtime_native_observation_binding")
    for row in native_rows:
        need(type(row["path"]) is str and PurePosixPath(row["path"]).is_absolute()
             and type(row["bytes"]) is int and 0 < row["bytes"] <= 128 * MIB
             and SHA.fullmatch(row["sha256"]), "frozen_runtime_native_observation_entry")
    need(sum(row["bytes"] for row in native_rows) <= MAX_INSTALLED, "frozen_runtime_native_observation_total")
    runtime_path = receipt.get("runtime_path")
    need(type(runtime_path) is str and PurePosixPath(runtime_path).is_absolute()
         and str(PurePosixPath(runtime_path)) == runtime_path and ".." not in PurePosixPath(runtime_path).parts
         and set(receipt.get("python", {})) == {"bytes", "sha256"}
         and type(receipt["python"]["bytes"]) is int and 0 < receipt["python"]["bytes"] <= MAX_INPUT
         and SHA.fullmatch(receipt["python"]["sha256"]), "frozen_runtime_host_runtime_identity")
    return {"receipt_sha256": regular(receipt_path, MAX_METADATA)["sha256"],
            "input_lock_sha256": LOCK_SHA, "wheel_lock_sha256": receipt["wheel_lock"]["sha256"],
            "installed_files_sha256": receipt["installed_manifest"]["sha256"],
            "native_observation_sha256": digest(encoded(native)),
            "archive": archive, "files": len(rows), "runtime_bytes": manifest["total_bytes"],
            "old_runtime_equivalence_claimed": False, "archive_content_read_by_binding_validation": False}


def check(runtime, receipt_path, manifest_path=None):
    receipt_path = Path(receipt_path)
    binding = validate_receipt(receipt_path, manifest_path)
    receipt = object_file(receipt_path)
    manifest = object_file(manifest_path or receipt_path.with_name("installed-files.json"))
    need(Path(runtime).resolve(strict=True) == Path(receipt["runtime_path"])
         and inventory(Path(runtime)) == manifest, "frozen_runtime_installed_bytes_changed")
    need(regular(Path(sys.executable).resolve(strict=True), MAX_INPUT) == receipt["python"],
         "frozen_runtime_python_changed")
    for row in receipt["native_observation"]["files"]:
        need(regular(Path(row["path"]), 128 * MIB) == {key: row[key] for key in ("bytes", "sha256")},
             "frozen_runtime_observed_native_file_changed")
    return binding


def build(runtime, work, output, context):
    runtime, work, output = map(Path, (runtime, work, output))
    need(sys.platform == "linux" and os.geteuid() != 0 and sys.version_info[:2] == (3, 12)
         and importlib.metadata.version("pip") == "24.0", "frozen_runtime_original_build_platform")
    need(set(context) == {"source_sha", "semwright_sha", "run_id", "run_attempt"}
         and all(re.fullmatch(r"[0-9a-f]{40}", context[name]) for name in ("source_sha", "semwright_sha"))
         and context["run_id"].isdecimal() and context["run_attempt"] == "1"
         and context["source_sha"] == os.environ.get("GITHUB_SHA")
         and context["run_id"] == os.environ.get("GITHUB_RUN_ID")
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "frozen_runtime_exact_hosted_context")
    for path in (runtime, work, output):
        need(path.is_absolute() and path.resolve(strict=False) == path and not path.exists() and not path.is_symlink(),
             "frozen_runtime_existing_destination_no_replay")
    need(runtime != work and runtime not in work.parents and work not in runtime.parents and runtime not in output.parents
         and shutil.disk_usage(work.parent).free >= 2 * 1024 * MIB, "frozen_runtime_private_workspace_reserve")
    work.mkdir(mode=0o700)
    runtime.mkdir(mode=0o700)
    (runtime / "site-packages").mkdir(mode=0o700)
    for name in ("downloads", "wheels", "build-temp", "build-cache"):
        (work / name).mkdir(mode=0o700)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    json_new(work / "build-intent.json", {"context": context, "input_lock_sha256": LOCK_SHA,
             "native_effect_invocations": 0, "project_inputs_read": False, "automatic_retry": False})
    lock, lock_raw = input_lock()
    write_new(work / "input-lock.json", lock_raw)
    resolved, downloaded = [], []
    for row in lock["entries"]:
        url = row["download_url"]
        if url is None:
            need(row["name"] == "av" and row["kind"] == "wheel", "frozen_runtime_only_observed_pyav_resolution")
            metadata_path = work / "pyav-release.original.json"
            download("https://pypi.org/pypi/av/15.0.0/json", metadata_path, None, MAX_METADATA)
            metadata = object_file(metadata_path)
            choices = [item for item in metadata["urls"] if item.get("filename") == row["filename"]
                       and item.get("digests", {}).get("sha256") == row["sha256"] and item.get("yanked") is False]
            need(len(choices) == 1, "frozen_runtime_exact_original_pyav_wheel")
            url = choices[0]["url"]
        destination = work / ("wheels" if row["kind"] == "wheel" else "downloads") / row["filename"]
        remaining = MAX_TAR - sum(downloaded)
        need(remaining > 0, "frozen_runtime_download_aggregate")
        retained = download(url, destination, row["sha256"], min(MAX_INPUT, remaining))
        downloaded.append(retained["bytes"])
        need(sum(downloaded) <= MAX_TAR, "frozen_runtime_download_aggregate")
        resolved.append({"name": row["name"], "url": url, "filename": row["filename"], **retained})
    json_new(work / "download-originals.json", {"context": context, "inputs": resolved})
    env = clean_environment(work)
    sdists = [str(work / "downloads" / row["filename"]) for row in lock["entries"] if row["kind"] == "sdist"]
    command(work, "build-wheels", [sys.executable, "-B", "-m", "pip", "wheel", "--disable-pip-version-check",
            "--no-cache-dir", "--no-deps", "--wheel-dir", str(work / "wheels"), *sdists], env,
            observed_roots=(work, runtime))
    paths = sorted((work / "wheels").iterdir())
    need(len(paths) == 32 and all(path.suffix == ".whl" for path in paths), "frozen_runtime_exact_32_wheels")
    wheels = []
    for expected in lock["entries"]:
        candidates = [path for path in paths if name_key(path.name.split("-", 1)[0]) == expected["name"]]
        need(len(candidates) == 1, "frozen_runtime_unique_distribution_wheel")
        path = candidates[0]
        observed = wheel_metadata(path, expected)
        if expected["kind"] == "wheel":
            need(path.name == expected["filename"] and observed["sha256"] == expected["sha256"], "frozen_runtime_downloaded_wheel_changed")
        wheels.append({"name": expected["name"], "version": expected["version"], "filename": path.name,
                       "input_kind": expected["kind"], "input_sha256": expected["sha256"], **observed})
    # Leave room for pip's generated records/scripts and the runtime descriptor.
    need(sum(row["expanded_bytes"] for row in wheels) <= MAX_INSTALLED - 64 * MIB
         and sum(row["expanded_files"] for row in wheels) <= MAX_FILES - 256,
         "frozen_runtime_wheel_aggregate_expansion")
    wheel_lock = {"schema": "motionwright.manim.exact-wheel-lock/1", "context": context,
                  "input_lock_sha256": LOCK_SHA, "wheels": wheels, "source_builds": 3,
                  "sdist_rebuild_byte_reproducible_claimed": False}
    json_new(work / "wheel-lock.json", wheel_lock)
    requirements = "".join(row["name"] + "==" + row["version"] + " --hash=sha256:" + row["sha256"] + "\n" for row in wheels)
    write_new(work / "wheel-lock.requirements.txt", requirements.encode())
    command(work, "install-wheels", [sys.executable, "-B", "-m", "pip", "install", "--disable-pip-version-check",
            "--no-cache-dir", "--no-index", "--no-deps", "--no-compile", "--require-hashes",
            "--find-links", str(work / "wheels"), "--target", str(runtime / "site-packages"),
            "--report", str(work / "installed-pip-report.json"), "-r", str(work / "wheel-lock.requirements.txt")], env,
            observed_roots=(work, runtime))
    installed = list(importlib.metadata.distributions(path=[str(runtime / "site-packages")]))
    observed_names = {name_key(value.metadata["Name"]): value.version for value in installed}
    need(len(installed) == len(observed_names) == 32 and observed_names == {row["name"]: row["version"] for row in wheels},
         "frozen_runtime_installed_distribution_roster")
    runtime_value = {"schema": 2, "manim_version": "0.21.0", "pyav_version": "15.0.0",
                     "source_color_policy": "srgb-rgba8-opaque-v1", "color_recipe": "srgb-to-rec709-full12-v2"}
    write_new(runtime / "runtime.json", (json.dumps(runtime_value, sort_keys=True) + "\n").encode())
    inventory(runtime)
    command(work, "observe-native", [sys.executable, "-B", str(Path(__file__).resolve()), "_observe",
            "--runtime", str(runtime), "--output", str(work / "native-observation.json")],
            clean_environment(work, runtime), seconds=120, observed_roots=(work, runtime))
    native = object_file(work / "native-observation.json")
    manifest = inventory(runtime)
    json_new(work / "installed-files.json", manifest)
    selected = {"wheels/" + row["filename"]: work / "wheels" / row["filename"] for row in wheels}
    for name in RUNTIME_METADATA:
        selected["metadata/" + name] = (runtime if name == "runtime.json" else work) / name
    archive, members = tar_originals(output, selected)
    receipt = {"schema": "motionwright.manim.frozen-runtime/1", "status": "EXACT_WHEELS_AND_INSTALLED_FILES_RETAINED",
               "context": context, "runtime_path": str(runtime.resolve(strict=True)), "input_lock_sha256": LOCK_SHA,
               "wheel_lock": regular(work / "wheel-lock.json", MAX_METADATA),
               "installed_manifest": regular(work / "installed-files.json", MAX_METADATA),
               "python": regular(Path(sys.executable).resolve(strict=True), MAX_INPUT), "python_version": sys.version,
               "native_observation": native,
               "archive": archive, "archive_members": members, "archive_physical_readback": True,
               "old_runtime_equivalence_claimed": False, "sdist_rebuild_byte_reproducible_claimed": False,
               "native_effect_invocations": 0, "project_inputs_read": False, "automatic_retry": False}
    json_new(work / "receipt.json", receipt)
    check(runtime, work / "receipt.json")
    return receipt


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("build")
    for name in ("runtime", "work", "output"):
        create.add_argument("--" + name, type=Path, required=True)
    create.add_argument("--context", required=True)
    verify = sub.add_parser("check")
    verify.add_argument("--runtime", type=Path, required=True)
    verify.add_argument("--receipt", type=Path, required=True)
    observe = sub.add_parser("_observe")
    observe.add_argument("--runtime", type=Path, required=True)
    observe.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "_observe":
        native_observation(args.runtime, args.output)
        return
    if args.command == "check":
        print(json.dumps(check(args.runtime, args.receipt), sort_keys=True))
        return
    try:
        result = build(args.runtime, args.work, args.output, strict(args.context))
    except BaseException as error:
        if args.work.is_dir() and not (args.work / "failure.json").exists():
            json_new(args.work / "failure.json", {"status": "FROZEN_RUNTIME_BUILD_FAILED_NO_RETRY",
                     "first_error_type": type(error).__name__, "first_error": str(error)[:4096],
                     "native_effect_invocations": 0, "project_inputs_read": False, "automatic_retry": False})
        raise
    print(json.dumps({"status": result["status"], "archive": result["archive"], "native_effect_invocations": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
