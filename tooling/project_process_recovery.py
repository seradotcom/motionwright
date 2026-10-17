#!/usr/bin/env python3
"""
Portable process-termination wrapper for the Motionwright project fixture.

Child contract:
    <binary> hold <new-case-root>
    <binary> verify <same-case-root>

All wrapper-created files remain below case-root.
No shell, network, process-tree termination or mutation retries.

The wrapper verifies the actual Git checkout and executable digest. Build logs
must separately bind the compiled binary to that checkout. Runner metadata is
recorded as caller-supplied rather than platform attestation.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import queue
import re
import signal
import sqlite3
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid


SCHEMA = "motionwright.project-process-recovery/1"
DATABASE_RELATIVE_PATH = "project/state.sqlite3"
CONTROL_FILES = (
    "original-intent.json",
    "race-intents.json",
    "checkpoint.json",
)
SOURCE_FILES = (
    "inputs/original.wav",
    "inputs/cas-left.wav",
    "inputs/cas-right.wav",
)
ORIGINAL_REQUEST_ID = "project-process/original-import/1"
LEFT_REQUEST_ID = "project-process/cas-left/1"
RIGHT_REQUEST_ID = "project-process/cas-right/1"
PREPARATION_REQUEST_IDS = (
    "project-process/scene/1",
    "project-process/main-marker/1",
    ORIGINAL_REQUEST_ID,
    "project-process/create-branch/1",
    "project-process/checkout-alternate/1",
    "project-process/alternate-marker/1",
    "project-process/checkout-main/1",
)
RACE_SPECS = {
    LEFT_REQUEST_ID: ("CAS left source", 200),
    RIGHT_REQUEST_ID: ("CAS right source", 300),
}
READ_CHUNK_BYTES = 4096
QUEUE_ITEMS = 64
MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_CONTROL_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_JSON_BYTES = 16 * 1024 * 1024
MAX_BINARY_BYTES = 1024 * 1024 * 1024
MAX_CASE_ENTRIES = 2048
MAX_CASE_DEPTH = 32
CLEANUP_SECONDS = 5.0
HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


class HarnessError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise HarnessError(message)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def short_error(error):
    return "{}: {}".format(type(error).__name__, str(error))[:4096]


def require_object(value, label):
    require(type(value) is dict, "{} must be a JSON object".format(label))
    return value


def require_list(value, label):
    require(type(value) is list, "{} must be a JSON array".format(label))
    return value


def require_integer(value, expected, label):
    require(
        type(value) is int and value == expected,
        "{} must equal integer {}".format(label, expected),
    )


def require_positive_integer(value, label):
    require(type(value) is int and value > 0, "{} must be a positive integer".format(label))


def require_string(value, label):
    require(type(value) is str and len(value) > 0, "{} must be a nonempty string".format(label))
    return value


def require_uuid(value, label):
    require_string(value, label)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise HarnessError("{} is not a UUID".format(label)) from error
    require(str(parsed) == value, "{} must use canonical UUID text".format(label))
    return value


def require_sha256(value, label):
    require(
        type(value) is str and HEX64.fullmatch(value) is not None,
        "{} must be a lowercase SHA-256 hex string".format(label),
    )
    return value


def require_time(value, numerator, denominator, label):
    value = require_object(value, label)
    require_integer(value.get("num"), numerator, label + ".num")
    require_integer(value.get("den"), denominator, label + ".den")


def strict_json(text, label):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "{} contains a duplicate JSON key".format(label))
            result[key] = value
        return result

    def reject_constant(_value):
        raise HarnessError("{} contains a non-finite JSON number".format(label))

    try:
        return json.loads(text, object_pairs_hook=object_pairs, parse_constant=reject_constant)
    except HarnessError:
        raise
    except (ValueError, UnicodeError, RecursionError) as error:
        raise HarnessError("{} is not valid bounded JSON".format(label)) from error


class Deadline:
    def __init__(self, seconds):
        self.started = time.monotonic()
        self.ends = self.started + seconds
        self.seconds = seconds

    def remaining(self, phase):
        remaining = self.ends - time.monotonic()
        require(remaining > 0, "timeout during {}".format(phase))
        return remaining

    def check(self, phase):
        self.remaining(phase)

    def wait_slice(self, phase):
        return min(0.05, self.remaining(phase))

    def elapsed(self):
        return time.monotonic() - self.started


def absolute_path(value, label):
    path = Path(value)
    require(path.is_absolute(), "{} must be absolute".format(label))
    require(".." not in path.parts, "{} must not contain '..'".format(label))
    require(
        not str(path).startswith(("\\\\?\\", "\\\\.\\")),
        "{} must not use a Windows device path".format(label),
    )
    return path


def is_reparse_or_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def check_kind(info, directory, label):
    require(not is_reparse_or_link(info), "{} is a symlink or reparse point".format(label))
    if directory:
        require(stat.S_ISDIR(info.st_mode), "{} is not a directory".format(label))
    else:
        require(stat.S_ISREG(info.st_mode), "{} is not a regular file".format(label))


def guard_absolute(path, directory):
    """Check the observed path components without resolving through aliases."""
    require(path.is_absolute(), "internal path must be absolute")
    current = Path(path.anchor)
    check_kind(os.lstat(current), True, str(current))
    components = path.parts[1:]
    for index, component in enumerate(components):
        current = current / component
        is_last = index == len(components) - 1
        check_kind(os.lstat(current), directory if is_last else True, str(current))
    if not components:
        require(directory, "filesystem root is not an ordinary file")


def stat_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def open_existing_regular(path):
    guard_absolute(path, False)
    observed = os.lstat(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        check_kind(opened, False, str(path))
        require(
            stat_identity(observed) == stat_identity(opened),
            "file changed while opening {}".format(path),
        )
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def read_bounded_file(path, limit, deadline, phase):
    deadline.check(phase)
    with open_existing_regular(path) as handle:
        before = os.fstat(handle.fileno())
        require(before.st_size <= limit, "{} exceeds its byte limit".format(path))
        result = bytearray()
        while True:
            deadline.check(phase)
            chunk = handle.read(min(1024 * 1024, limit + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
            require(len(result) <= limit, "{} exceeds its byte limit".format(path))
        after = os.fstat(handle.fileno())
        require(
            stat_identity(before) == stat_identity(after),
            "file changed while reading {}".format(path),
        )
        require(len(result) == before.st_size, "file length changed while reading {}".format(path))
        return bytes(result)


def hash_regular_file(path, limit, deadline, phase):
    deadline.check(phase)
    digest = hashlib.sha256()
    total = 0
    with open_existing_regular(path) as handle:
        before = os.fstat(handle.fileno())
        require(before.st_size <= limit, "{} exceeds its byte limit".format(path))
        while True:
            deadline.check(phase)
            chunk = handle.read(min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            require(total <= limit, "{} exceeds its byte limit".format(path))
            digest.update(chunk)
        after = os.fstat(handle.fileno())
        require(
            stat_identity(before) == stat_identity(after) and total == before.st_size,
            "file changed while hashing {}".format(path),
        )
    return {"sha256": digest.hexdigest(), "size_bytes": total}


class Case:
    def __init__(self, root):
        self.root = root
        self.created = False
        self.evidence_created = False

    def create(self):
        guard_absolute(self.root.parent, True)
        require(
            not os.path.lexists(self.root),
            "case-root must be new; refusing to reuse an existing path",
        )
        os.mkdir(self.root, 0o700)
        self.created = True
        if os.name == "posix":
            os.chmod(self.root, 0o700)
        guard_absolute(self.root, True)
        self.make_directory("evidence")
        self.evidence_created = True
        self.make_directory("evidence/temporary")

    def relative_parts(self, relative):
        require(type(relative) is str, "artifact path must be a string")
        require("\x00" not in relative, "artifact path contains NUL")
        require(
            not PureWindowsPath(relative).drive,
            "artifact path must not have a drive",
        )
        normalized = relative.replace("\\", "/")
        require(not normalized.startswith("/"), "artifact path must be relative")
        parts = normalized.split("/")
        require(
            all(
                part not in ("", ".", "..")
                and SAFE_COMPONENT.fullmatch(part) is not None
                for part in parts
            ),
            "artifact path contains an unsafe component",
        )
        return tuple(parts)

    def path(self, relative):
        parts = self.relative_parts(relative)
        path = self.root.joinpath(*parts)
        guard_absolute(path.parent, True)
        return path

    def existing_file(self, relative):
        path = self.path(relative)
        guard_absolute(path, False)
        return path

    def make_directory(self, relative):
        path = self.path(relative)
        os.mkdir(path, 0o700)
        if os.name == "posix":
            os.chmod(path, 0o700)
        guard_absolute(path, True)
        return path

    def open_new_file(self, relative):
        path = self.path(relative)
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags, 0o600)
        return os.fdopen(descriptor, "wb")

    def write_json(self, relative, value):
        data = (
            json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8")
        require(
            len(data) <= MAX_EVIDENCE_JSON_BYTES,
            "{} exceeds evidence JSON limit".format(relative),
        )
        with self.open_new_file(relative) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return {
            "path": relative,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def guard_tree(self, deadline):
        guard_absolute(self.root, True)
        pending = [(self.root, 0)]
        count = 0
        while pending:
            directory, depth = pending.pop()
            deadline.check("case artifact inspection")
            require(depth <= MAX_CASE_DEPTH, "case artifact tree is too deep")
            guard_absolute(directory, True)
            with os.scandir(directory) as entries:
                for entry in entries:
                    deadline.check("case artifact inspection")
                    count += 1
                    require(count <= MAX_CASE_ENTRIES, "too many case artifacts")
                    info = entry.stat(follow_symlinks=False)
                    require(
                        not is_reparse_or_link(info),
                        "case contains a symlink or reparse point: {}".format(entry.path),
                    )
                    if stat.S_ISDIR(info.st_mode):
                        pending.append((Path(entry.path), depth + 1))
                    else:
                        require(
                            stat.S_ISREG(info.st_mode),
                            "case contains a non-regular artifact: {}".format(entry.path),
                        )


class CapturedProcess:
    """Only reader threads touch the child's pipes; the supervisor reads a queue."""

    def __init__(self, name, case):
        self.name = name
        self.case = case
        self.process = None
        self.events = queue.Queue(maxsize=QUEUE_ITEMS)
        self.stop_readers = threading.Event()
        self.threads = []
        self.logs = {}
        self.log_hashes = {"stdout": hashlib.sha256(), "stderr": hashlib.sha256()}
        self.observed_bytes = {"stdout": 0, "stderr": 0}
        self.saved_bytes = {"stdout": 0, "stderr": 0}
        self.tails = {"stdout": b"", "stderr": b""}
        self.eof = set()
        self.messages = []
        self.errors = []
        self.cleanup_errors = []
        self.cleanup_kill_attempted = False
        self.started_utc = None
        self.exited_utc = None

    def flag(self, message):
        if len(self.errors) < 8:
            self.errors.append(str(message)[:1024])

    def put_event(self, item):
        while not self.stop_readers.is_set():
            try:
                self.events.put(item, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def reader(self, stream_name, pipe):
        try:
            while not self.stop_readers.is_set():
                chunk = os.read(pipe.fileno(), READ_CHUNK_BYTES)
                if not chunk:
                    break
                if not self.put_event(("data", stream_name, chunk)):
                    return
        except Exception as error:
            self.put_event(("error", stream_name, short_error(error)))
        finally:
            try:
                pipe.close()
            except Exception as error:
                self.put_event(("error", stream_name, short_error(error)))
            self.put_event(("eof", stream_name, None))

    def start(self, binary, mode):
        for stream_name in ("stdout", "stderr"):
            relative = "evidence/{}.{}.log".format(self.name, stream_name)
            self.logs[stream_name] = self.case.open_new_file(relative)
        child_environment = os.environ.copy()
        temporary = str(self.case.path("evidence/temporary"))
        child_environment["TMPDIR"] = temporary
        child_environment["TMP"] = temporary
        child_environment["TEMP"] = temporary
        self.started_utc = utc_now()
        self.process = subprocess.Popen(
            [str(binary), mode, str(self.case.root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.case.root),
            env=child_environment,
            shell=False,
            close_fds=True,
            bufsize=0,
        )
        for stream_name, pipe in (
            ("stdout", self.process.stdout),
            ("stderr", self.process.stderr),
        ):
            thread = threading.Thread(
                target=self.reader,
                args=(stream_name, pipe),
                daemon=True,
                name="fixture-{}-{}".format(self.name, stream_name),
            )
            self.threads.append(thread)
            thread.start()

    def consume_data(self, stream_name, chunk):
        self.observed_bytes[stream_name] += len(chunk)
        available = MAX_STREAM_BYTES - self.saved_bytes[stream_name]
        saved = chunk[:max(0, available)]
        if saved:
            written = self.logs[stream_name].write(saved)
            require(written == len(saved), "short write to captured process log")
            self.saved_bytes[stream_name] += len(saved)
            self.log_hashes[stream_name].update(saved)
        if self.observed_bytes[stream_name] > MAX_STREAM_BYTES:
            self.flag("{} exceeded its stream byte limit".format(stream_name))
            self.tails[stream_name] = b""
            return
        combined = self.tails[stream_name] + chunk
        lines = combined.split(b"\n")
        self.tails[stream_name] = lines.pop()
        for line in lines:
            if len(line) > MAX_LINE_BYTES:
                self.flag("{} exceeded its line byte limit".format(stream_name))
                continue
            if stream_name == "stdout":
                if self.messages:
                    self.flag("stdout contained more than one JSONL message")
                    continue
                if line.endswith(b"\r"):
                    line = line[:-1]
                try:
                    message = strict_json(
                        line.decode("utf-8"), "{} stdout JSONL".format(self.name)
                    )
                    require_object(message, "stdout protocol message")
                    self.messages.append(message)
                except Exception as error:
                    self.flag(short_error(error))
        if len(self.tails[stream_name]) > MAX_LINE_BYTES:
            self.flag("{} exceeded its line byte limit".format(stream_name))
            self.tails[stream_name] = b""

    def consume_event(self, item):
        kind, stream_name, payload = item
        if kind == "data":
            self.consume_data(stream_name, payload)
        elif kind == "error":
            self.flag("{} reader failed: {}".format(stream_name, payload))
        elif kind == "eof":
            self.eof.add(stream_name)
            if stream_name == "stdout" and self.tails[stream_name]:
                self.flag("stdout ended without the required JSONL newline")
            self.tails[stream_name] = b""
        else:
            self.flag("internal unknown capture event")

    def raise_errors(self):
        require(
            not self.errors,
            "{} capture failed: {}".format(self.name, "; ".join(self.errors)),
        )

    def pump(self, timeout, strict=True):
        try:
            item = self.events.get(timeout=timeout)
        except queue.Empty:
            item = None
        if item is not None:
            self.consume_event(item)
        if strict:
            self.raise_errors()

    def drain_available(self, strict=True):
        for _ in range(QUEUE_ITEMS):
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                break
            self.consume_event(item)
        if strict:
            self.raise_errors()

    def assert_alive(self):
        self.drain_available()
        require(self.process is not None, "{} was not started".format(self.name))
        require(
            self.process.poll() is None,
            "{} exited before the required termination point".format(self.name),
        )

    def wait_ready(self, deadline):
        while True:
            deadline.check("hold READY")
            self.assert_alive()
            if self.messages:
                return self.messages[0]
            self.pump(deadline.wait_slice("hold READY"))

    def wait_done(self, deadline, phase):
        require(self.process is not None, "{} was not started".format(self.name))
        while True:
            deadline.check(phase)
            self.drain_available()
            if self.process.poll() is not None and self.eof == {"stdout", "stderr"}:
                return_code = self.process.wait(timeout=min(0.1, deadline.remaining(phase)))
                self.exited_utc = utc_now()
                self.raise_errors()
                require(
                    len(self.messages) == 1,
                    "{} did not emit exactly one JSONL message".format(self.name),
                )
                return return_code
            self.pump(deadline.wait_slice(phase))

    def force_terminate(self, deadline):
        self.assert_alive()
        pid = self.process.pid
        record = {
            "pid": pid,
            "alive_immediately_before_call": True,
            "call_utc": utc_now(),
            "method": (
                "subprocess.Popen.kill / TerminateProcess"
                if os.name == "nt"
                else "subprocess.Popen.kill / SIGKILL"
            ),
        }
        self.process.kill()
        return_code = self.wait_done(deadline, "forced process termination")
        record["exit_utc"] = self.exited_utc
        record["return_code"] = return_code
        if os.name == "nt":
            require(
                return_code == 1,
                "Windows fixture did not report the TerminateProcess exit code 1",
            )
        else:
            require(return_code == -signal.SIGKILL, "Unix fixture did not exit from SIGKILL")
        return record

    def shutdown(self, cleanup_end):
        """Best-effort bounded cleanup of this Popen only; never a process tree."""
        try:
            if self.process is not None and self.process.poll() is None:
                self.cleanup_kill_attempted = True
                try:
                    self.process.kill()
                except Exception as error:
                    if self.process.poll() is None:
                        self.cleanup_errors.append(short_error(error))
            while self.process is not None and time.monotonic() < cleanup_end:
                self.drain_available(strict=False)
                if self.process.poll() is not None and self.eof == {"stdout", "stderr"}:
                    self.process.wait(timeout=0)
                    if self.exited_utc is None:
                        self.exited_utc = utc_now()
                    break
                self.pump(
                    min(0.05, max(0.001, cleanup_end - time.monotonic())), strict=False
                )
            if self.process is not None:
                if self.process.poll() is None:
                    self.cleanup_errors.append("owned process remained alive at cleanup deadline")
                if self.eof != {"stdout", "stderr"}:
                    self.cleanup_errors.append("pipe EOF was not observed before cleanup deadline")
        except Exception as error:
            self.cleanup_errors.append(short_error(error))
        finally:
            self.stop_readers.set()
            for thread in self.threads:
                remaining = cleanup_end - time.monotonic()
                if remaining > 0:
                    thread.join(timeout=min(0.2, remaining))
                if thread.is_alive():
                    self.cleanup_errors.append("reader thread did not exit before cleanup deadline")
            for handle in self.logs.values():
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                except Exception as error:
                    self.cleanup_errors.append(short_error(error))
                finally:
                    try:
                        handle.close()
                    except Exception as error:
                        self.cleanup_errors.append(short_error(error))

    def describe(self):
        return {
            "name": self.name,
            "pid": None if self.process is None else self.process.pid,
            "return_code": None if self.process is None else self.process.poll(),
            "started_utc": self.started_utc,
            "exited_utc": self.exited_utc,
            "protocol_message": self.messages[0] if self.messages else None,
            "capture_errors": self.errors,
            "cleanup_errors": self.cleanup_errors,
            "cleanup_kill_attempted": self.cleanup_kill_attempted,
            "streams": {
                name: {
                    "path": "evidence/{}.{}.log".format(self.name, name),
                    "observed_bytes": self.observed_bytes[name],
                    "saved_bytes": self.saved_bytes[name],
                    "captured_sha256": self.log_hashes[name].hexdigest(),
                    "truncated": self.observed_bytes[name] > self.saved_bytes[name],
                    "eof_observed": name in self.eof,
                }
                for name in ("stdout", "stderr")
            },
        }


def read_controls(case, deadline):
    documents = {}
    fingerprints = {}
    for relative in CONTROL_FILES:
        data = read_bounded_file(
            case.existing_file(relative), MAX_CONTROL_BYTES, deadline,
            "reading fixture control files",
        )
        documents[relative] = require_object(strict_json(data.decode("utf-8"), relative), relative)
        fingerprints[relative] = {
            "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)
        }
    return documents, fingerprints


def assert_controls_unchanged(case, expected, deadline):
    actual = {}
    for relative in CONTROL_FILES:
        actual[relative] = hash_regular_file(
            case.existing_file(relative), MAX_CONTROL_BYTES, deadline,
            "checking immutable fixture control files",
        )
    require(actual == expected, "fixture intent or checkpoint bytes changed")


def synthetic_wav(sample_value):
    require(
        type(sample_value) is int and -32768 <= sample_value <= 32767,
        "WAV sample must be an i16",
    )
    pcm = struct.pack("<h", sample_value) * 4800
    return (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 48000, 96000, 2, 16)
        + b"data" + struct.pack("<I", len(pcm)) + pcm
    )


def asset_intent(name):
    return {"kind": "asset", "name": name, "media_type": "audio/wav"}


def check_asset(asset, asset_id, name, digest, label):
    asset = require_object(asset, label)
    require_uuid(asset.get("id"), label + ".id")
    require(asset["id"] == asset_id, label + " has an unexpected asset ID")
    require(asset.get("name") == name, label + " has an unexpected name")
    require(asset.get("media_type") == "audio/wav", label + " has an unexpected MIME")
    require(asset.get("content_sha256") == digest, label + " has an unexpected content digest")
    require(asset.get("source_revision") == "local-import", label + " is not a local-import asset")


def check_scene(scene, scene_id, label):
    scene = require_object(scene, label)
    require(scene.get("id") == scene_id, label + " has an unexpected scene ID")
    require(scene.get("name") == "Persistent scene", label + " has an unexpected name")
    require(
        scene.get("objective") == "Committed scene survives process termination",
        label + " has an unexpected objective",
    )
    require_time(scene.get("start"), 0, 1, label + ".start")
    require_time(scene.get("duration"), 4, 1, label + ".duration")


def check_marker(marker, marker_id, label_text, second, label):
    marker = require_object(marker, label)
    require(marker.get("id") == marker_id, label + " has an unexpected marker ID")
    require(marker.get("label") == label_text, label + " has an unexpected label")
    require_time(marker.get("at"), second, 1, label + ".at")


def indexed_objects(values, key, label):
    values = require_list(values, label)
    result = {}
    for item in values:
        item = require_object(item, label + " item")
        identifier = require_string(item.get(key), label + " item." + key)
        require(identifier not in result, label + " contains duplicate identifiers")
        result[identifier] = item
    return result


def validate_project_content(project, checkpoint):
    main_id = checkpoint["main_branch_id"]
    alternate_id = checkpoint["alternate_branch_id"]
    scene_id = checkpoint["shared_scene_id"]
    main_marker_id = checkpoint["main_marker_id"]
    alternate_marker_id = checkpoint["alternate_marker_id"]
    require(main_id != alternate_id, "branch IDs must be distinct")
    require(main_marker_id != alternate_marker_id, "marker IDs must be distinct")
    require(project.get("active_branch") == main_id, "active branch must remain main")
    branches = indexed_objects(project.get("branches"), "id", "project.branches")
    require(set(branches) == {main_id, alternate_id}, "project must have exactly two branches")
    main = branches[main_id]
    alternate = branches[alternate_id]
    require(
        "parent_branch" in main and main["parent_branch"] is None,
        "main branch must have no parent",
    )
    require_integer(main.get("base_revision"), 0, "main.base_revision")
    require_integer(main.get("head_revision"), 8, "main.head_revision")
    require(alternate.get("parent_branch") == main_id, "alternate branch must descend from main")
    require_integer(alternate.get("base_revision"), 3, "alternate.base_revision")
    require_integer(alternate.get("head_revision"), 6, "alternate.head_revision")
    require(
        alternate.get("name") == "Persistence alternate",
        "alternate branch has an unexpected name",
    )
    workspaces = indexed_objects(
        project.get("branch_workspaces"), "branch_id", "project.branch_workspaces"
    )
    require(
        set(workspaces) == {main_id, alternate_id},
        "project must have exactly two branch workspaces",
    )
    main_state = require_object(workspaces[main_id].get("current_state"), "main workspace current_state")
    alternate_state = require_object(
        workspaces[alternate_id].get("current_state"), "alternate workspace current_state"
    )
    alternate_base = require_object(
        workspaces[alternate_id].get("base_state"), "alternate workspace base_state"
    )
    require(
        alternate_base == main_state,
        "alternate base state must equal the preserved main workspace state",
    )
    document_scenes = require_list(project.get("scenes"), "project.scenes")
    require(len(document_scenes) == 1, "active document must contain exactly one scene")
    check_scene(document_scenes[0], scene_id, "active scene")
    for label, branch_state in (
        ("main current state", main_state),
        ("alternate current state", alternate_state),
        ("alternate base state", alternate_base),
    ):
        scenes = require_list(branch_state.get("scenes"), label + ".scenes")
        require(len(scenes) == 1, label + " must contain exactly one scene")
        check_scene(scenes[0], scene_id, label + " scene")
        require(scenes == document_scenes, label + " changed the shared scene")
    document_markers = indexed_objects(project.get("markers"), "id", "project.markers")
    main_markers = indexed_objects(main_state.get("markers"), "id", "main markers")
    alternate_markers = indexed_objects(alternate_state.get("markers"), "id", "alternate markers")
    require(set(document_markers) == {main_marker_id}, "active document has unexpected markers")
    require(set(main_markers) == {main_marker_id}, "main workspace has unexpected markers")
    require(
        set(alternate_markers) == {main_marker_id, alternate_marker_id},
        "alternate workspace must retain both committed markers",
    )
    check_marker(document_markers[main_marker_id], main_marker_id, "Main baseline", 1, "main marker")
    require(
        document_markers[main_marker_id]
        == main_markers[main_marker_id]
        == alternate_markers[main_marker_id],
        "shared marker differs between document and branch states",
    )
    check_marker(
        alternate_markers[alternate_marker_id], alternate_marker_id,
        "Alternate committed marker", 2, "alternate marker",
    )


def check_outcome(outcome, project_id, generation, request_id, previous_revision, revision, label):
    outcome = require_object(outcome, label)
    require(outcome.get("request_id") == request_id, label + " has the wrong request ID")
    require_integer(outcome.get("previous_revision"), previous_revision, label + ".previous_revision")
    require(outcome.get("replayed") is False, label + " must be the original non-replayed outcome")
    project = require_object(outcome.get("project"), label + ".project")
    require(project.get("id") == project_id, label + " has the wrong project ID")
    require(project.get("generation") == generation, label + " has the wrong generation")
    require_integer(project.get("revision"), revision, label + ".project.revision")
    return project


def validate_manifests(documents, process_pid, case):
    original = documents["original-intent.json"]
    race = documents["race-intents.json"]
    checkpoint = documents["checkpoint.json"]
    for name, document in documents.items():
        require(document.get("schema") == SCHEMA, name + " has an unexpected schema")
    require_integer(checkpoint.get("pid"), process_pid, "checkpoint.pid")
    require(
        checkpoint.get("database_relative_path") == DATABASE_RELATIVE_PATH,
        "checkpoint names an unexpected database path",
    )
    require(
        checkpoint.get("source_relative_paths") == list(SOURCE_FILES),
        "checkpoint names unexpected original source paths",
    )
    for field in (
        "main_branch_id", "alternate_branch_id", "shared_scene_id",
        "main_marker_id", "alternate_marker_id",
    ):
        require_uuid(checkpoint.get(field), "checkpoint." + field)
    backup = require_object(checkpoint.get("expected_export"), "expected_export")
    require_positive_integer(backup.get("format_version"), "backup.format_version")
    require_positive_integer(backup.get("source_storage_schema"), "backup.source_storage_schema")
    require_string(backup.get("exported_at"), "backup.exported_at")
    require_sha256(backup.get("payload_sha256"), "backup.payload_sha256")
    project = require_object(backup.get("project"), "backup.project")
    project_id = require_uuid(project.get("id"), "project.id")
    generation = require_uuid(project.get("generation"), "project.generation")
    require_integer(project.get("revision"), 8, "project.revision")
    require_string(project.get("title"), "project.title")
    require_string(project.get("updated_at"), "project.updated_at")
    require(original.get("project_id") == project_id, "original intent has the wrong project")
    require(race.get("project_id") == project_id, "race intents have the wrong project")
    require(
        original.get("expected")
        == {"resource": "project:" + project_id, "generation": generation, "revision": 2},
        "original import must retain its pre-dispatch revision-2 stamp",
    )
    require(
        race.get("expected")
        == {"resource": "project:" + project_id, "generation": generation, "revision": 7},
        "both race attempts must retain the same revision-7 stamp",
    )
    require(original.get("request_id") == ORIGINAL_REQUEST_ID, "original import has an unexpected request ID")
    require(original.get("intent") == asset_intent("Original source"), "original import intent changed")

    blobs = require_list(checkpoint.get("blobs"), "checkpoint.blobs")
    require(len(blobs) == 3, "checkpoint must describe exactly three CAS blobs")
    blobs_by_sample = {}
    seen_paths = set()
    seen_digests = set()
    for blob in blobs:
        blob = require_object(blob, "CAS expectation")
        sample = blob.get("sample_value")
        require(
            type(sample) is int and sample in (100, 200, 300),
            "unexpected synthetic WAV sample value",
        )
        require(sample not in blobs_by_sample, "duplicate synthetic WAV sample value")
        expected_bytes = synthetic_wav(sample)
        expected_sha256 = hashlib.sha256(expected_bytes).hexdigest()
        require_sha256(blob.get("sha256"), "CAS expectation.sha256")
        require(blob["sha256"] == expected_sha256, "CAS digest does not match independently reconstructed WAV")
        require_integer(blob.get("size_bytes"), len(expected_bytes), "CAS expectation.size_bytes")
        parts = case.relative_parts(blob.get("relative_path"))
        require(
            len(parts) >= 3 and parts[:2] == ("project", "blobs"),
            "CAS expectation must remain under the actual project blob root",
        )
        normalized_path = "/".join(parts)
        require(normalized_path not in seen_paths, "duplicate CAS expectation path")
        require(blob["sha256"] not in seen_digests, "duplicate CAS expectation digest")
        seen_paths.add(normalized_path)
        seen_digests.add(blob["sha256"])
        blobs_by_sample[sample] = blob
    require(set(blobs_by_sample) == {100, 200, 300}, "synthetic CAS sample set is incomplete")
    attempts = require_list(race.get("attempts"), "race.attempts")
    require(len(attempts) == 2, "race must contain two independent attempts")
    attempts_by_request = {}
    for attempt in attempts:
        attempt = require_object(attempt, "race attempt")
        request_id = attempt.get("request_id")
        require(request_id in RACE_SPECS, "race attempt has an unexpected request ID")
        require(request_id not in attempts_by_request, "race request IDs must be distinct")
        name, sample = RACE_SPECS[request_id]
        require(
            attempt.get("intent") == asset_intent(name),
            "race file-import intent does not match its independent candidate",
        )
        change = require_object(attempt.get("change"), "race attempt.change")
        require(change.get("type") == "add_asset", "race change must be add_asset")
        asset = require_object(change.get("asset"), "race change.asset")
        asset_id = require_uuid(asset.get("id"), "race asset.id")
        check_asset(asset, asset_id, name, blobs_by_sample[sample]["sha256"], "race asset")
        attempts_by_request[request_id] = attempt
    require(
        set(attempts_by_request) == set(RACE_SPECS),
        "race must use the two distinct declared request IDs",
    )
    left_asset = attempts_by_request[LEFT_REQUEST_ID]["change"]["asset"]
    right_asset = attempts_by_request[RIGHT_REQUEST_ID]["change"]["asset"]
    require(left_asset["id"] != right_asset["id"], "race asset IDs must differ")
    require(left_asset["content_sha256"] != right_asset["content_sha256"], "race source bytes must differ")
    winner_request_id = checkpoint.get("winner_request_id")
    loser_request_id = checkpoint.get("loser_request_id")
    require(winner_request_id in RACE_SPECS, "checkpoint names an unknown winner")
    require(loser_request_id in RACE_SPECS, "checkpoint names an unknown loser")
    require(winner_request_id != loser_request_id, "winner and loser must differ")
    require_integer(checkpoint.get("loser_expected_revision"), 7, "loser expected revision")
    require_integer(checkpoint.get("loser_actual_revision"), 8, "loser actual revision")
    committed_requests = list(PREPARATION_REQUEST_IDS) + [winner_request_id]
    require(
        checkpoint.get("committed_request_ids") == committed_requests,
        "checkpoint must contain exactly seven preparation requests and one winner",
    )
    original_project = check_outcome(
        checkpoint.get("original_outcome"), project_id, generation,
        ORIGINAL_REQUEST_ID, 2, 3, "original outcome",
    )
    winner_project = check_outcome(
        checkpoint.get("winner_outcome"), project_id, generation,
        winner_request_id, 7, 8, "winner outcome",
    )
    require(winner_project == project, "winner outcome must equal the committed revision-8 document")
    original_assets = require_list(original_project.get("assets"), "original outcome assets")
    require(len(original_assets) == 1, "revision-3 original outcome must contain one asset")
    original_asset = require_object(original_assets[0], "original asset")
    original_asset_id = require_uuid(original_asset.get("id"), "original asset.id")
    check_asset(
        original_asset, original_asset_id, "Original source",
        blobs_by_sample[100]["sha256"], "original asset",
    )
    require(
        original_asset_id not in (left_asset["id"], right_asset["id"]),
        "original and race asset IDs must differ",
    )
    current_assets = indexed_objects(project.get("assets"), "id", "current project assets")
    winner_asset = attempts_by_request[winner_request_id]["change"]["asset"]
    loser_asset = attempts_by_request[loser_request_id]["change"]["asset"]
    require(
        set(current_assets) == {original_asset_id, winner_asset["id"]},
        "current document must associate only the original and winning assets",
    )
    require(current_assets[original_asset_id] == original_asset, "original asset association changed")
    require(current_assets[winner_asset["id"]] == winner_asset, "winning asset association changed")
    require(loser_asset["id"] not in current_assets, "losing asset was partially associated")
    validate_project_content(project, checkpoint)
    expected_changes = [
        {
            "type": "add_scene", "name": "Persistent scene",
            "objective": "Committed scene survives process termination",
            "duration_seconds": 4,
        },
        {"type": "add_marker", "at": {"num": 1, "den": 1}, "label": "Main baseline"},
        {"type": "add_asset", "asset": original_asset},
        {"type": "create_branch", "name": "Persistence alternate"},
        {"type": "checkout_branch", "branch_id": checkpoint["alternate_branch_id"]},
        {
            "type": "add_marker", "at": {"num": 2, "den": 1},
            "label": "Alternate committed marker",
        },
        {"type": "checkout_branch", "branch_id": checkpoint["main_branch_id"]},
        attempts_by_request[winner_request_id]["change"],
    ]
    backup_events = require_list(backup.get("events"), "backup.events")
    require(len(backup_events) == 8, "backup must contain exactly eight events")
    for revision, (event, expected_change) in enumerate(zip(backup_events, expected_changes), start=1):
        event = require_object(event, "backup event")
        require_integer(event.get("revision"), revision, "backup event revision")
        require(event.get("change") == expected_change, "backup contains an unexpected event change")
        require_string(event.get("created_at"), "backup event created_at")
    return {
        "project_id": project_id,
        "generation": generation,
        "project": project,
        "backup": backup,
        "checkpoint": checkpoint,
        "committed_requests": committed_requests,
        "winner_request_id": winner_request_id,
        "loser_request_id": loser_request_id,
        "blobs_by_sample": blobs_by_sample,
    }


def verify_cas_and_deleted_sources(case, state, deadline):
    observed = []
    for sample in (100, 200, 300):
        expectation = state["blobs_by_sample"][sample]
        expected_bytes = synthetic_wav(sample)
        path = case.existing_file(expectation["relative_path"])
        actual_bytes = read_bounded_file(
            path, len(expected_bytes) + 1, deadline, "independent CAS byte verification"
        )
        require(actual_bytes == expected_bytes, "published CAS bytes differ from the synthetic source")
        actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
        require(actual_sha256 == expectation["sha256"], "published CAS digest changed")
        observed.append({
            "sample_value": sample,
            "relative_path": expectation["relative_path"],
            "sha256": actual_sha256,
            "size_bytes": len(actual_bytes),
        })
    for relative in SOURCE_FILES:
        path = case.path(relative)
        require(
            not os.path.lexists(path),
            "original input source unexpectedly exists: {}".format(relative),
        )
    return observed


def snapshot_sqlite(case, state, deadline):
    """Open and close an independent readonly SQLite connection."""
    deadline.check("readonly SQLite snapshot")
    database_path = case.existing_file(DATABASE_RELATIVE_PATH)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = database_path.with_name(database_path.name + suffix)
        if os.path.lexists(sidecar):
            guard_absolute(sidecar, False)
    uri = database_path.as_uri() + "?mode=ro"
    timeout = min(1.0, deadline.remaining("readonly SQLite open"))
    with closing(sqlite3.connect(uri, uri=True, timeout=timeout)) as connection:
        connection.set_progress_handler(
            lambda: 1 if time.monotonic() >= deadline.ends else 0, 1000
        )
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        quick_check = [list(row) for row in connection.execute("PRAGMA quick_check")]
        foreign_key_check = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        counts = {
            table: connection.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
            for table in ("projects", "events", "receipts")
        }
        project_rows = [
            list(row) for row in connection.execute(
                "SELECT id, generation, revision, title, document_json, updated_at "
                "FROM projects WHERE id = ?", (state["project_id"],),
            )
        ]
        events = [
            list(row) for row in connection.execute(
                "SELECT revision, kind, payload_json, created_at "
                "FROM events WHERE project_id = ? ORDER BY revision", (state["project_id"],),
            )
        ]
        receipts = [
            list(row) for row in connection.execute(
                "SELECT request_id, request_sha256, result_json, created_at "
                "FROM receipts WHERE project_id = ? ORDER BY request_id", (state["project_id"],),
            )
        ]
        deadline.check("readonly SQLite snapshot")
    return {
        "quick_check": quick_check,
        "foreign_key_check": foreign_key_check,
        "counts": counts,
        "project_rows": project_rows,
        "events": events,
        "receipts": receipts,
    }


def utc_timestamp_text(value):
    require_string(value, "UTC timestamp")
    return value[:-6] + "Z" if value.endswith("+00:00") else value


def validate_snapshot(snapshot, state):
    require(snapshot["quick_check"] == [["ok"]], "SQLite quick_check failed")
    require(snapshot["foreign_key_check"] == [], "SQLite foreign_key_check failed")
    require(
        snapshot["counts"] == {"projects": 1, "events": 8, "receipts": 8},
        "SQLite must contain exactly 1 project, 8 events and 8 receipts globally",
    )
    require(len(snapshot["project_rows"]) == 1, "expected project row is absent or ambiguous")
    row = snapshot["project_rows"][0]
    require(len(row) == 6, "unexpected project snapshot column count")
    require(row[0] == state["project_id"], "project row ID changed")
    require(row[1] == state["generation"], "project row generation changed")
    require_integer(row[2], 8, "project row revision")
    document = require_object(strict_json(row[4], "projects.document_json"), "stored project")
    require(document == state["project"], "stored project differs from the complete committed document")
    require(row[3] == document.get("title"), "project title column and document disagree")
    require(
        utc_timestamp_text(row[5]) == utc_timestamp_text(document.get("updated_at")),
        "project updated_at column and document disagree",
    )
    events = snapshot["events"]
    require(len(events) == 8, "expected project must own all eight events")
    for revision, (row, expected_event) in enumerate(zip(events, state["backup"]["events"]), start=1):
        require(len(row) == 4, "unexpected event snapshot column count")
        require_integer(row[0], revision, "stored event revision")
        require_string(row[1], "stored event kind")
        payload = strict_json(row[2], "events.payload_json")
        require(payload == expected_event["change"], "stored event payload differs from the real export")
        require(row[3] == expected_event["created_at"], "stored event timestamp differs from the real export")
    receipts = snapshot["receipts"]
    require(len(receipts) == 8, "expected project must own all eight receipts")
    expected_revisions = {
        request_id: revision
        for revision, request_id in enumerate(state["committed_requests"], start=1)
    }
    seen_requests = set()
    for row in receipts:
        require(len(row) == 4, "unexpected receipt snapshot column count")
        request_id, request_sha256, result_json, created_at = row
        require(request_id in expected_revisions, "unexpected or losing receipt was committed")
        require(request_id not in seen_requests, "duplicate receipt request ID")
        seen_requests.add(request_id)
        require_sha256(request_sha256, "receipt request_sha256")
        require_string(created_at, "receipt created_at")
        outcome = strict_json(result_json, "receipts.result_json")
        revision = expected_revisions[request_id]
        check_outcome(
            outcome, state["project_id"], state["generation"], request_id,
            revision - 1, revision, "stored receipt outcome",
        )
        if request_id == ORIGINAL_REQUEST_ID:
            require(
                outcome == state["checkpoint"]["original_outcome"],
                "stored original receipt lost its complete historical outcome",
            )
        if request_id == state["winner_request_id"]:
            require(
                outcome == state["checkpoint"]["winner_outcome"],
                "stored winning receipt differs from its original outcome",
            )
    require(seen_requests == set(expected_revisions), "committed receipt set is incomplete")
    require(state["loser_request_id"] not in seen_requests, "losing receipt was committed")


def validate_ready(message, process_pid):
    require(
        set(message) == {"schema", "phase", "pid", "checkpoint"},
        "READY message has unexpected fields",
    )
    require(message.get("schema") == SCHEMA, "READY schema mismatch")
    require(message.get("phase") == "ready", "hold did not announce READY")
    require_integer(message.get("pid"), process_pid, "READY PID")
    require(message.get("checkpoint") == "checkpoint.json", "READY checkpoint path mismatch")


def validate_verified(message, process_pid, state):
    require(
        set(message) == {
            "schema", "phase", "pid", "project_id", "revision", "original_revision",
            "winner_revision", "missing_request_id", "read_only_recovery",
        },
        "verified message has unexpected fields",
    )
    require(message.get("schema") == SCHEMA, "verified schema mismatch")
    require(message.get("phase") == "verified", "verify did not announce success")
    require_integer(message.get("pid"), process_pid, "verify PID")
    require(message.get("project_id") == state["project_id"], "verify project ID mismatch")
    require_integer(message.get("revision"), 8, "verify current revision")
    require_integer(message.get("original_revision"), 3, "verify original revision")
    require_integer(message.get("winner_revision"), 8, "verify winner revision")
    require(
        message.get("missing_request_id") == state["loser_request_id"],
        "verify did not retain the original losing request identity",
    )
    require(
        message.get("read_only_recovery") is True,
        "verify did not report the declared readonly recovery contract",
    )


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--case-root", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--actual-source-sha", required=True)
    parser.add_argument("--expected-binary-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--runner-os", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    arguments = parser.parse_args()
    require(
        HEX40.fullmatch(arguments.expected_source_sha) is not None,
        "--expected-source-sha must contain 40 hex characters",
    )
    require(
        HEX40.fullmatch(arguments.actual_source_sha) is not None,
        "--actual-source-sha must contain 40 hex characters",
    )
    require(
        re.fullmatch(r"[0-9a-fA-F]{64}", arguments.expected_binary_sha256) is not None,
        "--expected-binary-sha256 must contain 64 hex characters",
    )
    require(
        math.isfinite(arguments.timeout_seconds) and 0 < arguments.timeout_seconds <= 120,
        "--timeout-seconds must be finite, greater than zero and at most 120",
    )
    for name in ("run_id", "job_name", "runner_os"):
        value = getattr(arguments, name)
        require(
            0 < len(value) <= 256 and all(ord(character) >= 32 for character in value),
            "--{} must contain 1..256 printable characters".format(name.replace("_", "-")),
        )
    require(os.name in ("posix", "nt"), "unsupported operating-system process API")
    arguments.expected_source_sha = arguments.expected_source_sha.lower()
    arguments.actual_source_sha = arguments.actual_source_sha.lower()
    arguments.expected_binary_sha256 = arguments.expected_binary_sha256.lower()
    # Resolve initial runner-owned aliases once (for example macOS /var).
    # New fixture descendants remain subject to strict link/reparse checks.
    arguments.source_root = absolute_path(arguments.source_root, "--source-root").resolve(strict=True)
    guard_absolute(arguments.source_root, True)
    binary = absolute_path(arguments.binary, "--binary")
    check_kind(os.lstat(binary), False, str(binary))
    arguments.binary = binary.parent.resolve(strict=True) / binary.name
    guard_absolute(arguments.binary, False)
    case_root = absolute_path(arguments.case_root, "--case-root")
    arguments.case_root = case_root.parent.resolve(strict=True) / case_root.name
    guard_absolute(arguments.case_root.parent, True)
    return arguments


def observe_source(arguments, deadline):
    def git(*command):
        environment = os.environ.copy()
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        completed = subprocess.run(
            ["git", "-C", str(arguments.source_root), *command],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=min(10.0, deadline.remaining("source checkout verification")),
            env=environment, shell=False, check=False,
        )
        require(completed.returncode == 0, "Git checkout observation failed")
        require(
            len(completed.stdout) <= 1024 * 1024 and len(completed.stderr) <= 64 * 1024,
            "Git observation output exceeds its expected limit",
        )
        return completed.stdout.decode("utf-8").strip()

    actual_root = Path(git("rev-parse", "--show-toplevel")).resolve(strict=True)
    require(actual_root == arguments.source_root, "--source-root must be the actual checkout root")
    actual_sha = git("rev-parse", "HEAD")
    require(
        actual_sha == arguments.expected_source_sha == arguments.actual_source_sha,
        "actual Git HEAD does not match the selected source SHA",
    )
    require(
        git("status", "--porcelain=v1", "--untracked-files=no") == "",
        "tracked checkout must be clean",
    )
    return {
        "observed_git_head": actual_sha,
        "observed_checkout_root": str(actual_root),
        "tracked_checkout_clean": True,
    }


def run(arguments):
    case = Case(arguments.case_root)
    deadline = Deadline(arguments.timeout_seconds)
    captures = []
    exit_code = 1
    result_file_written = False
    result = {
        "schema": SCHEMA,
        "status": "failed",
        "started_utc": utc_now(),
        "case_root": str(case.root),
        "source_provenance": {
            "expected_source_sha": arguments.expected_source_sha,
            "runner_reported_actual_source_sha": arguments.actual_source_sha,
            "values_match": arguments.expected_source_sha == arguments.actual_source_sha,
            "source_root": str(arguments.source_root),
            "verification_scope": (
                "Git HEAD and tracked checkout cleanliness are observed directly. "
                "Executable hashing does not replace source-bound runner build logs."
            ),
        },
        "runner_reported": {
            "run_id": arguments.run_id,
            "job_name": arguments.job_name,
            "runner_os": arguments.runner_os,
        },
        "observed_runtime": {
            "os_name": os.name,
            "sys_platform": sys.platform,
            "python_version": sys.version,
            "sqlite_version": sqlite3.sqlite_version,
        },
        "binary": {
            "path": str(arguments.binary),
            "expected_sha256": arguments.expected_binary_sha256,
        },
        "limits": {
            "supervision_timeout_seconds": arguments.timeout_seconds,
            "cleanup_timeout_seconds": CLEANUP_SECONDS,
            "stream_byte_limit": MAX_STREAM_BYTES,
            "line_byte_limit": MAX_LINE_BYTES,
            "queue_items": QUEUE_ITEMS,
            "queue_chunk_bytes": READ_CHUNK_BYTES,
        },
        "snapshots": {},
        "cas_checks": {},
        "claims": {
            "process_termination_recovery": False,
            "one_distinct_change_cas_winner": False,
            "logical_sqlite_state_preserved": False,
            "synthetic_cas_bytes_preserved": False,
            "readonly_recovery_retained_original_intents": False,
        },
        "scope_limits": [
            "Process termination is exercised; power-loss durability is not asserted.",
            "The fixture uses synthetic local assets and does not establish actor authorization or consent.",
            "No external Host side-effect counter or remote UNKNOWN reconciliation is exercised.",
            "No replay retention horizon, restored-backup replay policy, installer signature or platform signing is asserted.",
            "Filesystem guards inspect paths but do not provide a sandbox against an adversarial concurrent filesystem mutator.",
        ],
    }
    try:
        case.create()
        initial_source = observe_source(arguments, deadline)
        result["source_provenance"]["initial_observed"] = initial_source
        result["invocation_artifact"] = case.write_json(
            "evidence/invocation.json",
            {
                "schema": SCHEMA,
                "started_utc": result["started_utc"],
                "binary": result["binary"],
                "source_provenance": result["source_provenance"],
                "runner_reported": result["runner_reported"],
                "observed_runtime": result["observed_runtime"],
                "limits": result["limits"],
            },
        )
        require(
            arguments.actual_source_sha == arguments.expected_source_sha,
            "runner-reported source SHA does not match the expected source SHA",
        )
        initial_binary = hash_regular_file(
            arguments.binary, MAX_BINARY_BYTES, deadline, "initial fixture binary hashing"
        )
        require(
            initial_binary["sha256"] == arguments.expected_binary_sha256,
            "actual binary SHA-256 does not match the expected binary SHA-256",
        )
        result["binary"]["initial_observed"] = initial_binary
        hold = CapturedProcess("hold", case)
        captures.append(hold)
        hold.start(arguments.binary, "hold")
        ready = hold.wait_ready(deadline)
        validate_ready(ready, hold.process.pid)
        case.guard_tree(deadline)
        documents, controls = read_controls(case, deadline)
        state = validate_manifests(documents, hold.process.pid, case)
        result["control_files"] = controls
        result["project_id"] = state["project_id"]
        result["generation"] = state["generation"]
        result["winner_request_id"] = state["winner_request_id"]
        result["loser_request_id"] = state["loser_request_id"]
        result["cas_checks"]["before_termination"] = verify_cas_and_deleted_sources(case, state, deadline)
        before = snapshot_sqlite(case, state, deadline)
        result["snapshots"]["before_termination"] = case.write_json(
            "evidence/sqlite-before-termination.json", before
        )
        validate_snapshot(before, state)
        assert_controls_unchanged(case, controls, deadline)

        # snapshot_sqlite's closing() has already closed its independent reader.
        hold.assert_alive()
        result["termination"] = hold.force_terminate(deadline)

        # First database reader after forced termination, before any product opener.
        after_kill = snapshot_sqlite(case, state, deadline)
        result["snapshots"]["after_termination"] = case.write_json(
            "evidence/sqlite-after-termination.json", after_kill
        )
        validate_snapshot(after_kill, state)
        require(after_kill == before, "logical SQLite rows changed across forced process termination")
        assert_controls_unchanged(case, controls, deadline)
        result["cas_checks"]["after_termination"] = verify_cas_and_deleted_sources(case, state, deadline)
        case.guard_tree(deadline)
        before_verify_binary = hash_regular_file(
            arguments.binary, MAX_BINARY_BYTES, deadline,
            "fixture binary hashing before readonly verification",
        )
        require(before_verify_binary == initial_binary, "fixture binary changed before readonly verification")
        result["binary"]["before_verify_observed"] = before_verify_binary
        verify = CapturedProcess("verify", case)
        captures.append(verify)
        verify.start(arguments.binary, "verify")
        verify_return_code = verify.wait_done(deadline, "readonly fixture verification")
        require(verify_return_code == 0, "readonly fixture verification exited unsuccessfully")
        validate_verified(verify.messages[0], verify.process.pid, state)
        after_verify = snapshot_sqlite(case, state, deadline)
        result["snapshots"]["after_readonly_verification"] = case.write_json(
            "evidence/sqlite-after-readonly-verification.json", after_verify
        )
        validate_snapshot(after_verify, state)
        require(after_verify == before, "readonly verification changed logical SQLite rows")
        assert_controls_unchanged(case, controls, deadline)
        result["cas_checks"]["after_readonly_verification"] = verify_cas_and_deleted_sources(
            case, state, deadline
        )
        case.guard_tree(deadline)
        final_binary = hash_regular_file(
            arguments.binary, MAX_BINARY_BYTES, deadline, "final fixture binary hashing"
        )
        require(final_binary == initial_binary, "fixture binary changed during the case")
        result["binary"]["final_observed"] = final_binary
        final_source = observe_source(arguments, deadline)
        require(final_source == initial_source, "source checkout changed during the case")
        result["source_provenance"]["final_observed"] = final_source
        result["claims"] = {
            "process_termination_recovery": True,
            "one_distinct_change_cas_winner": True,
            "logical_sqlite_state_preserved": True,
            "synthetic_cas_bytes_preserved": True,
            "readonly_recovery_retained_original_intents": True,
        }
        result["status"] = "passed"
        exit_code = 0
    except KeyboardInterrupt as error:
        result["error"] = short_error(error)
        result["status"] = "interrupted"
        exit_code = 130
    except Exception as error:
        result["error"] = short_error(error)
        result["status"] = "failed"
        exit_code = 1
    finally:
        cleanup_end = time.monotonic() + CLEANUP_SECONDS
        for capture in reversed(captures):
            capture.shutdown(cleanup_end)
        result["processes"] = [capture.describe() for capture in captures]
        cleanup_errors = [
            "{}: {}".format(capture.name, error)
            for capture in captures for error in capture.cleanup_errors
        ]
        if cleanup_errors:
            result["cleanup_errors"] = cleanup_errors
            result["status"] = "failed"
            exit_code = 1
        result["finished_utc"] = utc_now()
        result["elapsed_seconds"] = deadline.elapsed()
        if case.evidence_created:
            try:
                case.write_json("evidence/result.json", result)
                result_file_written = True
            except Exception as error:
                result["status"] = "failed"
                result["evidence_write_error"] = short_error(error)
                exit_code = 1
        summary = {
            "schema": SCHEMA,
            "status": result["status"],
            "result_path": str(case.root / "evidence" / "result.json") if result_file_written else None,
            "error": result.get("error"),
            "evidence_write_error": result.get("evidence_write_error"),
            "cleanup_errors": result.get("cleanup_errors", []),
        }
        print(json.dumps(summary, sort_keys=True, allow_nan=False), flush=True)
    return exit_code


def main():
    try:
        arguments = parse_arguments()
    except (HarnessError, OSError) as error:
        print(
            json.dumps({"schema": SCHEMA, "status": "argument-error", "error": short_error(error)}, sort_keys=True),
            file=sys.stderr, flush=True,
        )
        return 2
    return run(arguments)


if __name__ == "__main__":
    sys.exit(main())
