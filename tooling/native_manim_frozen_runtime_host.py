#!/usr/bin/env python3
"""Retain one original Manim runtime artifact on the admitted Windows host.

The default admission uses the reviewed public source gate. An explicit
runtime-metadata admission instead binds four authentic runtime originals to a
reviewed receipt digest, without evaluating or granting a SourceGate. Both
admissions require the original artifact ID, ZIP/TAR identities and exact
55-member roster. This reader performs one GET, preserves
the original ZIP and TAR, and hashes every TAR member without extracting wheels
or installing a runtime. An exclusive attempt directory prevents an automatic
second attempt. Failed and partial originals remain in that directory.

Invoke with the exact, already reviewed A4 memory helper. Its existing Windows
Job enforces the 1536 MiB process/aggregate cap; this reader observes the 512 MiB
physical reserve and preserves the same kill-on-job-close ownership boundary.
No historical private key, Project input, Native call or renderer is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time
import types
import zipfile

import ci_private_evidence as engine
from native_manim_frozen_runtime import MAX_INPUT, RUNTIME_METADATA, relative_name, validate_receipt
from native_manim_recovery_gate import (
    PUBLIC_GATE_FILES, check_source_gate, checked_archive_binding, validate_artifact,
)
from native_manim_resume_host import private_root, save
from native_manim_resume_transfer import RECOVERY_SCENARIO, REPOSITORY, SDK

MIB = 1024 * 1024
CHUNK = MIB
MAX_ARCHIVE = 128 * MIB
MAX_GATE = 2 * MIB
MAX_STDERR = 65536
MAX_NEW_BYTES = 2 * MAX_ARCHIVE + 8 * MIB
DISK_RESERVE = 512 * MIB
MEMORY_LIMIT = 1536 * MIB
MEMORY_RESERVE = 512 * MIB
MAX_SECONDS = 240
DOWNLOAD_SECONDS = 180
MEMBER_COUNT = 55
TAR_NAME = "runtime-wheels.tar"
MEMORY_HELPER_SHA = "eca6aaf897a2845894f73ae3bac1483dd840b41a6c2b6ab2c2bbfd1e13dc639c"
RUNTIME_FILES = ("runtime-receipt.json", "runtime-installed-files.json",
                 "runtime-wheel-lock.json", "runtime-artifact.original.json")


class Rejected(ValueError):
    pass


def need(value, code):
    if not value:
        raise Rejected("runtime_host_" + code)


def error_code(error):
    value = str(error)
    return value if re.fullmatch(r"[a-z][a-z0-9_]{0,199}", value) else type(error).__name__


def file_signature(info):
    mode = info.st_mode
    if os.name == "nt" and stat.S_ISREG(mode):
        # Windows stat(path) infers 0111 from .exe; fstat(handle) cannot infer
        # the suffix. The ACL is checked separately. Keep file type, readonly
        # bits, attributes, identity, link count, times and size unchanged.
        mode &= ~0o111
    return (info.st_dev, info.st_ino, mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, getattr(info, "st_file_attributes", 0))


def namespace(path, *, directory=False):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True) == path, "canonical_path_required")
    rows = []
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        need(not stat.S_ISLNK(info.st_mode)
             and not getattr(info, "st_file_attributes", 0) & 0x400, "path_alias_or_reparse_point")
        is_directory = item != path or directory
        need(stat.S_ISDIR(info.st_mode) if is_directory else
             stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "regular_single_link_required")
        # Sibling creation by another owner does not alter a parent's identity.
        rows.append((str(item), info.st_dev, info.st_ino, info.st_mode,
                     getattr(info, "st_file_attributes", 0)) if is_directory else
                    (str(item), *file_signature(info)))
    return rows


@contextmanager
def stable_reader(path, maximum):
    path = Path(path)
    before = namespace(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    try:
        info = os.fstat(descriptor)
        need(file_signature(info) == before[-1][1:] and 0 <= info.st_size <= maximum,
             "file_identity_or_budget")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            yield stream, info.st_size
        need(file_signature(os.fstat(descriptor)) == file_signature(info)
             and namespace(path) == before, "file_changed_during_read")
    finally:
        os.close(descriptor)


def hash_stream(stream, maximum, guard, *, collect=False):
    count, hashed, pieces = 0, hashlib.sha256(), []
    while block := stream.read(CHUNK):
        guard()
        count += len(block)
        need(count <= maximum, "stream_budget")
        hashed.update(block)
        if collect:
            pieces.append(block)
    value = {"bytes": count, "sha256": hashed.hexdigest()}
    return (value, b"".join(pieces)) if collect else value


def file_identity(path, maximum, guard):
    with stable_reader(path, maximum) as (stream, size):
        value = hash_stream(stream, maximum, guard)
        need(value["bytes"] == size, "file_size_changed")
    return value


def small_original(path, maximum, guard):
    need(maximum <= MAX_GATE, "small_original_budget")
    with stable_reader(path, maximum) as (stream, size):
        identity, raw = hash_stream(stream, maximum, guard, collect=True)
        need(identity["bytes"] == size, "small_original_changed")
    return identity, raw


@contextmanager
def exclusive_writer(path):
    path = Path(path)
    parents = namespace(path.parent, directory=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            try:
                yield stream
            finally:
                stream.flush()
                os.fsync(stream.fileno())
        need(namespace(path.parent, directory=True) == parents, "output_parent_changed")
    finally:
        os.close(descriptor)


def retain_small(path, raw, guard):
    need(len(raw) <= MAX_GATE, "retained_metadata_budget")
    with exclusive_writer(path) as stream:
        stream.write(raw)
    expected = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    need(file_identity(path, MAX_GATE, guard) == expected, "retained_metadata_readback")
    return expected


class HostResources:
    """Use the pinned existing A4 Job implementation; never emulate its cap."""
    def __init__(self, helper, destination):
        need(os.name == "nt" and ctypes.sizeof(ctypes.c_void_p) == 8, "windows_64bit_required")
        identity, raw = small_original(helper, MAX_GATE, lambda: None)
        need(identity["sha256"] == MEMORY_HELPER_SHA, "reviewed_memory_helper_changed")
        module = types.ModuleType("motionwright_frozen_runtime_memory")
        module.__file__ = str(helper)
        exec(compile(raw, str(helper), "exec"), module.__dict__)
        need(module.MAXIMUM_MIB * MIB == MEMORY_LIMIT and module.RESERVE_MIB * MIB == MEMORY_RESERVE,
             "reviewed_resource_contract_changed")
        available = module.available_memory()
        need(available >= MEMORY_LIMIT + MEMORY_RESERVE, "initial_physical_reserve")
        self.module, self.limiter = module, module.ProcessMemoryLimit()
        limits = self.limiter.type()
        need(bool(self.limiter.kernel.QueryInformationJobObject(self.limiter.handle, 9,
            ctypes.byref(limits), ctypes.sizeof(limits), None)), "job_query")
        limits.basic.flags |= 0x2000  # Same owned-cohort kill-on-close as the existing host wrapper.
        need(bool(self.limiter.kernel.SetInformationJobObject(self.limiter.handle, 9,
            ctypes.byref(limits), ctypes.sizeof(limits))), "job_kill_on_close")
        self.destination = Path(destination)
        self.started = time.monotonic()
        self.minimum_available = available
        self.available_before = available
        self.free_before = shutil.disk_usage(destination).free
        need(self.free_before >= DISK_RESERVE + MAX_NEW_BYTES, "initial_disk_reserve")
        self.lock = threading.Lock()
        self.observe()

    def __call__(self):
        with self.lock:
            need(time.monotonic() - self.started <= MAX_SECONDS, "original_deadline_exceeded")
            available = self.module.available_memory()
            self.minimum_available = min(self.minimum_available, available)
            need(available >= MEMORY_RESERVE, "physical_reserve_lost")
            free = shutil.disk_usage(self.destination).free
            need(free >= DISK_RESERVE and self.free_before - free <= MAX_NEW_BYTES,
                 "disk_reserve_or_write_budget_lost")

    def observe(self):
        self()
        value = self.limiter.observe()
        limits = self.limiter.type()
        need(bool(self.limiter.kernel.QueryInformationJobObject(self.limiter.handle, 9,
            ctypes.byref(limits), ctypes.sizeof(limits), None))
            and limits.basic.flags & 0x2300 == 0x2300
            and not limits.basic.flags & (0x800 | 0x1000), "job_boundary_changed")
        return {"helper_sha256": MEMORY_HELPER_SHA, "memory_limit_bytes": MEMORY_LIMIT,
                "physical_reserve_bytes": MEMORY_RESERVE, "available_before": self.available_before,
                "minimum_available_observed": self.minimum_available, "kernel": value,
                "cohort_termination_on_job_close": True, "elapsed_seconds": time.monotonic() - self.started,
                "disk_free_before": self.free_before, "disk_free_after": shutil.disk_usage(self.destination).free,
                "disk_reserve_bytes": DISK_RESERVE, "maximum_new_bytes": MAX_NEW_BYTES,
                "disk_watchdog_is_hard_quota": False}

    def only_reader_active(self):
        class Processes(ctypes.Structure):
            _fields_ = [("assigned", ctypes.c_uint32), ("listed", ctypes.c_uint32),
                        ("ids", ctypes.c_size_t * 64)]
        row = Processes()
        need(bool(self.limiter.kernel.QueryInformationJobObject(self.limiter.handle, 3,
            ctypes.byref(row), ctypes.sizeof(row), None))
            and row.assigned == row.listed == 1 and row.ids[0] == os.getpid(),
            "owned_job_has_remaining_processes")
        return {"active_processes": 1, "only_reader_pid": os.getpid()}


def download_once(root, executable, artifact, guard):
    """One authenticated logical GET; stdout is streamed to its original file."""
    limit = artifact["artifact_zip_bytes"]
    need(type(limit) is int and 0 < limit <= MAX_ARCHIVE, "artifact_download_budget")
    executable = Path(executable)
    executable_before = file_identity(executable, MAX_ARCHIVE, guard)
    endpoint = "/repos/" + REPOSITORY + "/actions/artifacts/" + str(artifact["artifact_id"]) + "/zip"
    command = [str(executable), "api", "--hostname", "github.com", "--method", "GET", endpoint]
    save(root, "download-intent.original.json", {"command": command,
        "executable": executable_before, "maximum_stdout_bytes": limit,
        "maximum_stderr_bytes": MAX_STDERR, "maximum_seconds": DOWNLOAD_SECONDS,
        "automatic_retry": False, "attempts_authorized": 1})
    env = {key: value for key, value in os.environ.items() if key not in {"GH_DEBUG", "GH_TRACE"}}
    env.update(GH_HOST="github.com", GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0", GH_PAGER="")
    paths = {"stdout": root / "runtime-artifact.zip.original", "stderr": root / "download-stderr.original"}
    state = {name: {"written_bytes": 0, "observed_bytes": 0, "complete": False,
                    "sha256": None, "error": None} for name in paths}
    failed = threading.Event()
    child, threads, first_error, terminated = None, [], None, False
    started_threads, secondary_errors, settled = [], [], True
    linux_group_state, linux_group_killed, linux_group_survived = "not_spawned", False, False
    started = time.monotonic()

    def drain(source, destination, name, maximum):
        hashed = hashlib.sha256()
        try:
            while block := source.read(CHUNK):
                guard()
                row = state[name]
                row["observed_bytes"] += len(block)
                selected = block[:max(0, maximum - row["written_bytes"])]
                destination.write(selected)
                hashed.update(selected)
                row["written_bytes"] += len(selected)
                need(row["observed_bytes"] <= maximum, name + "_download_overflow")
            state[name]["complete"] = True
        except BaseException as error:
            state[name]["error"] = error_code(error)
            failed.set()
        finally:
            try:
                destination.flush()
                os.fsync(destination.fileno())
            except BaseException as error:
                state[name]["error"] = state[name]["error"] or error_code(error)
                state[name]["complete"] = False
                failed.set()
            state[name]["sha256"] = hashed.hexdigest()
            source.close()

    # Reserve both originals before Popen. All work after spawn is inside the
    # cleanup scope, including publication of the original PID receipt.
    try:
        with exclusive_writer(paths["stdout"]) as stdout, exclusive_writer(paths["stderr"]) as stderr:
            try:
                guard()
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=env, start_new_session=os.name != "nt")
                threads = [threading.Thread(target=drain, args=(pipe, stream, name, maximum), daemon=True)
                    for pipe, stream, name, maximum in ((child.stdout, stdout, "stdout", limit),
                                                        (child.stderr, stderr, "stderr", MAX_STDERR))]
                for thread in threads:
                    thread.start()
                    started_threads.append(thread)
                save(root, "download-spawn.original.json", {"pid": child.pid, "automatic_retry": False})
                while child.poll() is None:
                    guard()
                    need(not failed.is_set(), "download_stream_failed")
                    need(time.monotonic() - started < DOWNLOAD_SECONDS, "download_deadline_exceeded")
                    time.sleep(0.05)
            except BaseException as error:
                first_error = error
            finally:
                if child is not None and os.name != "nt":
                    # Popen owns this new session. Settle its group before
                    # joining drains even if the parent has already exited or
                    # publishing its PID failed. The outer cgroup remains the
                    # owner of any descendant that leaves this process group.
                    try:
                        parent_exited = child.poll() is not None
                        os.killpg(child.pid, 0)
                        linux_group_state = "present"
                        if parent_exited:
                            linux_group_survived = True
                            error = Rejected("runtime_host_download_group_survived_parent_exit")
                            if first_error is None:
                                first_error = error
                            else:
                                secondary_errors.append(error_code(error))
                        os.killpg(child.pid, signal.SIGKILL)
                        linux_group_killed = terminated = True
                    except ProcessLookupError:
                        linux_group_state = "absent"
                    except BaseException as error:
                        linux_group_state = "unknown"
                        if first_error is None:
                            first_error = error
                        else:
                            secondary_errors.append(error_code(error))
                elif child is not None and child.poll() is None:
                    try:
                        child.kill()
                        terminated = True
                    except ProcessLookupError:
                        pass
                    except BaseException as error:
                        first_error = first_error or error
                        secondary_errors.append(error_code(error))
                if child is not None:
                    try:
                        child.wait(timeout=5)
                    except BaseException as error:
                        first_error = first_error or error
                        secondary_errors.append(error_code(error))
                for thread in started_threads:
                    thread.join(timeout=5)
                settled = not any(thread.is_alive() for thread in started_threads)
                if child is not None:
                    for thread, pipe in zip(threads, (child.stdout, child.stderr)):
                        if thread not in started_threads:
                            pipe.close()
                if not settled:
                    first_error = first_error or Rejected("runtime_host_download_streams_unsettled")
                if child is not None and os.name != "nt":
                    try:
                        os.killpg(child.pid, 0)
                    except ProcessLookupError:
                        linux_group_state = "absent"
                    except BaseException as error:
                        linux_group_state = "unknown"
                        if first_error is None:
                            first_error = error
                        else:
                            secondary_errors.append(error_code(error))
                    else:
                        linux_group_state = "present"
                        error = Rejected("runtime_host_download_group_still_observed")
                        if first_error is None:
                            first_error = error
                        else:
                            secondary_errors.append(error_code(error))
    except BaseException as error:
        if first_error is None:
            first_error = error
        else:
            secondary_errors.append(error_code(error))
    terminal = {"schema": "motionwright.manim.frozen-runtime.host-download/1",
        "spawned": child is not None, "pid": child.pid if child else None,
        "exit_code": child.returncode if child else None, "termination_requested": terminated,
        "streams_settled": settled, "streams": state, "automatic_retry": False,
        "first_error": error_code(first_error) if first_error else None,
        "secondary_errors": secondary_errors,
        "elapsed_seconds": time.monotonic() - started}
    if os.name != "nt":
        terminal.update(owned_process_group=child.pid if child else None,
            process_group_kill_sent=linux_group_killed,
            group_survived_parent_exit=linux_group_survived,
            final_process_group_state=linux_group_state)
    try:
        save(root, "download-terminal.original.json", terminal)
    except BaseException as error:
        first_error = first_error or error
    if first_error is not None:
        raise first_error
    need(child is not None and child.returncode == 0 and not terminated and settled
         and all(row["complete"] and row["error"] is None for row in state.values()),
         "download_not_known_complete")
    expected = {"bytes": limit, "sha256": artifact["artifact_zip_sha256"]}
    need({"bytes": state["stdout"]["written_bytes"], "sha256": state["stdout"]["sha256"]} == expected
         and file_identity(paths["stdout"], MAX_ARCHIVE, guard) == expected,
         "download_original_zip_identity")
    need(file_identity(executable, MAX_ARCHIVE, guard) == executable_before, "download_executable_changed")
    return paths["stdout"], terminal


def verify_tar(path, expected, members, guard):
    """Hash only the admitted regular members; never extract their contents."""
    need(type(members) is dict and len(members) == MEMBER_COUNT, "exact_55_tar_members_required")
    need(file_identity(path, MAX_ARCHIVE, guard) == expected, "tar_original_identity")
    need(sum(row["bytes"] for row in members.values()) <= MAX_ARCHIVE, "tar_member_aggregate")
    for name, row in members.items():
        relative_name(name)
        need(set(row) == {"bytes", "sha256"} and type(row["bytes"]) is int
             and 0 <= row["bytes"] <= (MAX_INPUT if name.startswith("wheels/") else 4 * MIB)
             and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "tar_member_contract")
    observed, last_end = {}, 0
    with stable_reader(path, MAX_ARCHIVE) as (stream, size):
        with tarfile.open(fileobj=stream, mode="r|", bufsize=10240, errorlevel=2) as archive:
            for member in archive:
                guard()
                name = member.name
                need(name in members and name not in observed and len(observed) < MEMBER_COUNT
                     and member.type == tarfile.REGTYPE and not member.sparse
                     and member.linkname == "" and member.mode == 0o600
                     and member.uid == member.gid == member.mtime == 0
                     and member.pax_headers in ({}, {"path": name})
                     and member.size == members[name]["bytes"], "tar_original_member_roster")
                source = archive.extractfile(member)
                need(source is not None, "tar_regular_stream_missing")
                with source:
                    row = hash_stream(source, member.size, guard)
                need(row == members[name], "tar_member_original_digest")
                observed[name] = row
                last_end = member.offset_data + ((member.size + 511) // 512) * 512
        need(observed == members and len(observed) == MEMBER_COUNT, "tar_member_roster_incomplete")
        # tarfile stops at its first zero block. Reject hidden appended members
        # or nonzero payload after that marker rather than silently ignoring it.
        need(size == ((last_end + 1024 + 10239) // 10240) * 10240,
             "tar_original_record_padding_size")
        stream.seek(last_end)
        remaining = size - last_end
        need(remaining >= 1024, "tar_original_end_markers_missing")
        while block := stream.read(CHUNK):
            guard()
            need(not any(block), "tar_nonzero_trailing_payload")
            remaining -= len(block)
        need(remaining == 0, "tar_trailer_changed")
    need(file_identity(path, MAX_ARCHIVE, guard) == expected, "tar_changed_after_member_verification")
    return observed


def retain_tar(zip_path, output, zip_expected, tar_expected, members, guard):
    need(file_identity(zip_path, MAX_ARCHIVE, guard) == zip_expected, "zip_original_identity")
    with stable_reader(zip_path, MAX_ARCHIVE) as (stream, zip_size):
        with zipfile.ZipFile(stream, mode="r") as archive:
            rows = archive.infolist()
            need(len(rows) == 1 and not archive.comment, "zip_single_tar_required")
            member = rows[0]
            need(member.filename == TAR_NAME and member.orig_filename == TAR_NAME
                 and not member.is_dir() and not member.flag_bits & 1 and not member.comment
                 and stat.S_IFMT(member.external_attr >> 16) in (0, stat.S_IFREG)
                 and member.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                 and member.header_offset == 0 and 0 < member.compress_size <= zip_size
                 and member.file_size == tar_expected["bytes"]
                 and 0 < member.file_size <= MAX_ARCHIVE, "zip_original_tar_entry")
            count, hashed = 0, hashlib.sha256()
            with archive.open(member, mode="r") as source, exclusive_writer(output) as destination:
                while block := source.read(CHUNK):
                    guard()
                    count += len(block)
                    need(count <= member.file_size, "zip_tar_expansion_overflow")
                    destination.write(block)
                    hashed.update(block)
            need({"bytes": count, "sha256": hashed.hexdigest()} == tar_expected,
                 "zip_tar_original_digest")
    need(file_identity(zip_path, MAX_ARCHIVE, guard) == zip_expected, "zip_changed_during_retention")
    return verify_tar(output, tar_expected, members, guard)


def checked_gate(path, current, expected_sha, guard):
    namespace(path, directory=True)
    originals = {}
    total = 0
    for name in PUBLIC_GATE_FILES:
        identity, raw = small_original(path / name, MAX_GATE, guard)
        total += len(raw)
        need(total <= MAX_GATE, "public_gate_aggregate_budget")
        originals[name] = (identity, raw)
    need(originals["source-gate.json"][0]["sha256"] == expected_sha, "reviewed_source_gate_digest")
    gate, digest = check_source_gate(current, path)
    need(digest == expected_sha and gate.get("context") == current, "reviewed_source_gate_context")
    runtime = gate["runtime"]
    receipt = engine.strict(originals["runtime-receipt.json"][1])
    members = receipt["archive_members"]
    origin = runtime["frozen"].get("reuse", {}).get("origin_context", current)
    need(receipt.get("context") == origin and len(members) == MEMBER_COUNT
         and sum(name.startswith("wheels/") for name in members) == 32
         and {name for name in members if name.startswith("metadata/")}
             == {"metadata/" + name for name in RUNTIME_METADATA}
         and runtime["frozen"]["archive"] == runtime["retained_artifact"]["tar"] == receipt["archive"],
         "reviewed_gate_runtime_archive_binding")
    for name, (identity, _raw) in originals.items():
        need(file_identity(path / name, MAX_GATE, guard) == identity, "public_gate_changed_during_admission")
    return gate, originals, members


def checked_runtime_metadata(path, current, expected_sha, guard):
    """Retain public dependencies even if a later Rust build/gate did not run.

The four inputs must already be authentic originals recovered from custody.
The explicit receipt pin binds the manifest, exact wheel lock, archive and
members. This original four-file admission validates the artifact API/context
and TAR directly; it does not require a later reuse receipt or a source gate.
No source test result is manufactured or inferred from this admission.
"""
    namespace(path, directory=True)
    need(type(expected_sha) is str and re.fullmatch(r"[0-9a-f]{64}", expected_sha),
         "reviewed_runtime_receipt_digest_argument")
    originals, total = {}, 0
    for name in RUNTIME_FILES:
        identity, raw = small_original(path / name, MAX_GATE, guard)
        total += len(raw)
        need(total <= MAX_GATE, "runtime_metadata_aggregate_budget")
        originals[name] = (identity, raw)
    need(originals["runtime-receipt.json"][0]["sha256"] == expected_sha,
         "reviewed_runtime_receipt_digest")
    receipt = engine.strict(originals["runtime-receipt.json"][1])
    need(receipt.get("context") == current, "reviewed_runtime_metadata_context")
    frozen = validate_receipt(path / "runtime-receipt.json",
                              path / "runtime-installed-files.json", path / "runtime-wheel-lock.json")
    artifact = validate_artifact(engine.strict(originals["runtime-artifact.original.json"][1]),
                                 current, expected_sha)
    checked_archive_binding(frozen, artifact)
    runtime = {"schema": "motionwright.manim.recovery-runtime.binding/1", "context": current,
               "frozen": frozen, "retained_artifact": artifact, "equivalence_to_old_runtime": False}
    members = receipt["archive_members"]
    need(receipt.get("context") == current and len(members) == MEMBER_COUNT
         and sum(name.startswith("wheels/") for name in members) == 32
         and {name for name in members if name.startswith("metadata/")}
             == {"metadata/" + name for name in RUNTIME_METADATA}
         and runtime["frozen"]["receipt_sha256"] == expected_sha
         and runtime["frozen"]["archive"] == runtime["retained_artifact"]["tar"] == receipt["archive"],
         "reviewed_runtime_metadata_archive_binding")
    for name, (identity, _raw) in originals.items():
        need(file_identity(path / name, MAX_GATE, guard) == identity,
             "runtime_metadata_changed_during_admission")
    return runtime, originals, members


def checked_admission(path, current, mode, source_gate_sha, receipt_sha, guard):
    need(mode in ("source-gate", "runtime-metadata"), "explicit_archive_admission")
    if mode == "source-gate":
        need(type(source_gate_sha) is str and re.fullmatch(r"[0-9a-f]{64}", source_gate_sha)
             and receipt_sha is None, "source_gate_admission_arguments")
        gate, originals, members = checked_gate(path, current, source_gate_sha, guard)
        runtime = gate["runtime"]
        receipt_sha = runtime["frozen"]["receipt_sha256"]
    else:
        need(source_gate_sha is None, "runtime_metadata_cannot_presuppose_source_gate")
        runtime, originals, members = checked_runtime_metadata(path, current, receipt_sha, guard)
    return {"mode": mode, "context": current, "runtime": runtime,
            "source_gate_sha256": source_gate_sha, "runtime_receipt_sha256": receipt_sha,
            "source_gate_evaluated": mode == "source-gate",
            "source_gate_admission_granted": False, "native_admission_granted": False}, originals, members


def checked_ntmode_continuation(prior, current, admission, originals, guard):
    """One explicit continuation of the reconciled failure before gh was called.

    This closed historical exception does not authorize a second download or a
    general retry of a failed reader. Its original negative and directory stay
    intact; any download intent, spawn or output in that attempt denies it.
    """
    need(current == {"source_sha": "54c74f5366513a72f6f22d4f6641091362534a09", "semwright_sha": SDK,
                     "run_id": "42361964881", "run_attempt": "1"}
         and admission["mode"] == "runtime-metadata"
         and admission["runtime_receipt_sha256"] == "d54d3cc4e49fb0cda44d7da0106ad02532e06263b1e7a0f7083e18cafe8fe6dc"
         and admission["runtime"]["retained_artifact"]["artifact_id"] == 13570292418,
         "ntmode_continuation_closed_original_context")
    namespace(prior, directory=True)
    need({path.name for path in prior.iterdir()} == {
        "retention-failure.original.json", "retention-intent.original.json", "runtime-originals"},
        "ntmode_prior_download_must_not_have_started")
    failure_identity, failure_raw = small_original(prior / "retention-failure.original.json", 65536, guard)
    intent_identity, intent_raw = small_original(prior / "retention-intent.original.json", 65536, guard)
    need(failure_identity == {"bytes": 690, "sha256": "bcc7a6dc96ece3978ab95cdae8c69b2d5a2a7a63e237e9054ccd89a2dd60fc5c"}
         and intent_identity == {"bytes": 3131, "sha256": "09f4228a512b6401e3fd48abbaba81335a95db2237eac863e47060704794297b"},
         "ntmode_original_negative_or_intent_changed")
    failure, intent = engine.strict(failure_raw), engine.strict(intent_raw)
    need(failure.get("context") == current and failure.get("stage") == "artifact-download"
         and failure.get("first_error") == "runtime_host_file_identity_or_budget"
         and failure.get("status") == "INCOMPLETE_OR_REJECTED_ORIGINALS_PRESERVED"
         and intent.get("context") == current and intent.get("admission") == admission,
         "ntmode_original_pre_download_failure_required")
    previous_inputs = prior / "runtime-originals"
    namespace(previous_inputs, directory=True)
    need({path.name for path in previous_inputs.iterdir()} == set(RUNTIME_FILES),
         "ntmode_prior_input_roster_changed")
    for name, (identity, _raw) in originals.items():
        need(file_identity(previous_inputs / name, MAX_GATE, guard) == identity,
             "ntmode_prior_input_identity_changed")
    return {"prior_root": str(prior), "failure": failure_identity, "intent": intent_identity,
            "previous_reader_sha256": "01f0cd6150bf4dbec797c6a9e3e0a9fa9a2e045e5b44129e1d57ee7d7d49449e",
            "previous_reader_tool_pid": 24632, "previous_artifact_GET_invocations": 0,
            "basis": "EXACT_RECONCILED_NT_MODE_FAILURE_BEFORE_GH_INTENT_OR_SPAWN",
            "continuation_explicitly_authorized": True, "automatic_retry": False}


def run(args):
    os.environ["MANIM_NATIVE_SCENARIO"] = RECOVERY_SCENARIO
    current = {"source_sha": args.source_sha, "semwright_sha": SDK,
               "run_id": args.run_id, "run_attempt": "1"}
    engine.validate_context(current)
    need(args.private.is_absolute() and args.private.drive.upper() == "E:", "original_E_drive_destination")
    namespace(args.private, directory=True)
    resources = HostResources(args.memory_helper, args.private)
    private_root(args.private)
    resources.only_reader_active()
    root, created, stage = None, False, "public-originals-admission"
    try:
        admission, originals, members = checked_admission(args.gate, current, args.admission,
            args.source_gate_sha256, args.runtime_receipt_sha256, resources)
        artifact = admission["runtime"]["retained_artifact"]
        root = args.private / ("manim-runtime-" + current["run_id"] + "-" + current["source_sha"][:12]
                               + "-artifact-" + str(artifact["artifact_id"]))
        continuation = None
        if args.attempt_tag == "causalntmode01":
            continuation = checked_ntmode_continuation(root, current, admission, originals, resources)
            root = root.with_name(root.name + "-causalntmode01")
        root.mkdir(mode=0o700, exist_ok=False)
        created = True
        save(root, "retention-intent.original.json", {"schema": "motionwright.manim.frozen-runtime.host-intent/1",
            "context": current, "admission": admission, "artifact": artifact,
            "pre_download_continuation": continuation,
            "members": MEMBER_COUNT, "maximum_archive_bytes": MAX_ARCHIVE,
            "resource_admission": resources.observe(), "automatic_retry": False,
            "authorized_download_attempts": 1, "historical_private_key_read": False,
            "installation_invocations": 0, "reconstruction_invocations": 0, "native_effect_invocations": 0})
        retained_gate = root / ("public-gate-originals" if args.admission == "source-gate" else "runtime-originals")
        retained_gate.mkdir(mode=0o700, exist_ok=False)
        for name, (identity, raw) in originals.items():
            need(retain_small(retained_gate / name, raw, resources) == identity, "gate_copy_identity")
        copied_admission, _copied_originals, copied_members = checked_admission(retained_gate, current,
            args.admission, args.source_gate_sha256, args.runtime_receipt_sha256, resources)
        need(copied_admission == admission and copied_members == members, "retained_admission_semantics_changed")
        stage = "artifact-download"
        zip_path, download = download_once(root, args.gh, artifact, resources)
        resources.only_reader_active()
        stage = "zip-and-tar-readback"
        zip_identity = {"bytes": artifact["artifact_zip_bytes"], "sha256": artifact["artifact_zip_sha256"]}
        observed = retain_tar(zip_path, root / TAR_NAME, zip_identity, artifact["tar"], members, resources)
        stage = "final-originals"
        for name, (identity, _raw) in originals.items():
            need(file_identity(args.gate / name, MAX_GATE, resources) == identity
                 and file_identity(retained_gate / name, MAX_GATE, resources) == identity,
                 "reviewed_gate_originals_changed")
        value = {"schema": "motionwright.manim.frozen-runtime.host-retention/1",
            "status": "ORIGINAL_ZIP_TAR_AND_55_MEMBERS_VERIFIED", "context": current,
            "admission": admission, "artifact_id": artifact["artifact_id"],
            "pre_download_continuation": continuation,
            "zip": {"path": zip_path.name, **zip_identity}, "tar": {"path": TAR_NAME, **artifact["tar"]},
            "archive_members": observed, "archive_member_count": len(observed),
            "admission_originals": {name: identity for name, (identity, _raw) in originals.items()},
            "resources": resources.observe(), "final_owned_processes": resources.only_reader_active(),
            "download_attempts": 1, "download_exit_code": download["exit_code"],
            "historical_private_key_read": False, "project_inputs_read": False,
            "installation_invocations": 0, "reconstruction_invocations": 0,
            "native_effect_invocations": 0, "render_invocations": 0,
            "runtime_restored_or_imported": False, "old_runtime_equivalence_claimed": False,
            "formal_G2_granted": False, "automatic_retry": False}
        digest = save(root, "retention-terminal.original.json", value)
        return {"status": value["status"], "root": str(root), "context": current,
                "admission_mode": args.admission, "source_gate_evaluated": admission["source_gate_evaluated"],
                "source_gate_admission_granted": False, "native_admission_granted": False,
                "artifact_id": artifact["artifact_id"], "terminal_sha256": digest,
                "archive_member_count": len(observed), "native_effect_invocations": 0}
    except BaseException as error:
        if created:
            value = {"schema": "motionwright.manim.frozen-runtime.host-retention/1",
                "status": "INCOMPLETE_OR_REJECTED_ORIGINALS_PRESERVED", "context": current,
                "admission_mode": args.admission, "source_gate_admission_granted": False,
                "native_admission_granted": False,
                "stage": stage, "first_error_type": type(error).__name__, "first_error": error_code(error),
                "automatic_retry": False, "another_download_authorized": False,
                "installation_invocations": 0, "reconstruction_invocations": 0,
                "native_effect_invocations": 0, "historical_private_key_read": False}
            try:
                save(root, "retention-failure.original.json", value)
            except BaseException:
                pass  # Preserve the first failure and every original already created.
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--admission", choices=("source-gate", "runtime-metadata"), default="source-gate")
    parser.add_argument("--attempt-tag", choices=("causalntmode01",))
    parser.add_argument("--source-gate-sha256")
    parser.add_argument("--runtime-receipt-sha256")
    parser.add_argument("--gate", "--public-originals", dest="gate", type=Path, required=True)
    for name in ("private", "memory-helper"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--gh", type=Path, default=Path(r"C:\Program Files\GitHub CLI\gh.exe"))
    args = parser.parse_args()
    try:
        result = run(args)
    except BaseException as error:
        print(json.dumps({"status": "REJECTED_NO_AUTOMATIC_RETRY", "error_type": type(error).__name__,
                          "error": error_code(error), "native_effect_invocations": 0}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
