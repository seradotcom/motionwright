"""Bounded, read-only transport of one same-run NSIS lifecycle artifact.

This module admits transport identity only. The independent lifecycle/tamper
consumer must validate the retained source, package, project and observations.
It never launches an installer, resets a namespace, retries GH, or grants a gate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import threading
import time
import zipfile

ARTIFACT_NAME = "motionwright-windows-nsis-lifecycle-original"
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 4096
MAX_DIRECTORY_BYTES = 4 * 1024 * 1024
CHUNK = 64 * 1024


class ArtifactError(ValueError):
    """A closed transport refusal; never a product acceptance result."""


def require(condition, code):
    if not condition:
        raise ArtifactError(code)


def _positive(value):
    return type(value) is int and value > 0


def _sha(value, length):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is not None


def _expected(artifact_id, artifact_sha256, expected_source, expected_run):
    require(_positive(artifact_id), "artifact_id_invalid")
    require(_sha(artifact_sha256, 64), "artifact_digest_invalid")
    require(_sha(expected_source, 40), "artifact_source_invalid")
    require(isinstance(expected_run, dict)
            and set(expected_run) == {"repository", "run_id", "run_attempt"}, "artifact_run_fields")
    repository = expected_run["repository"]
    require(isinstance(repository, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}",
                             repository) is not None, "artifact_repository_invalid")
    require(_positive(expected_run["run_id"]) and type(expected_run["run_attempt"]) is int
            and expected_run["run_attempt"] == 1, "artifact_run_invalid")
    return dict(expected_run)


def _plain_directory(path):
    require(path.is_absolute(), "artifact_path_not_absolute")
    for item in (path, *path.parents):
        require(not any(part in {".", ".."} or "~" in part for part in item.parts),
                "artifact_path_alias")
        meta = item.lstat()
        require(stat.S_ISDIR(meta.st_mode) and not stat.S_ISLNK(meta.st_mode)
                and not getattr(meta, "st_file_attributes", 0) & 0x400,
                "artifact_directory_not_plain")


def _new_directory(path):
    _plain_directory(path.parent)
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ArtifactError("artifact_output_already_exists")
    path.mkdir()
    _plain_directory(path)


def _json_new(path, value):
    body = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        require(stream.write(body) == len(body), "artifact_record_short_write")
        stream.flush()
        os.fsync(stream.fileno())


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "artifact_json_duplicate_key")
        result[key] = value
    return result


def _json(path):
    try:
        with path.open("rb") as stream:
            body = stream.read(MAX_METADATA_BYTES + 1)
        require(0 < len(body) <= MAX_METADATA_BYTES, "artifact_metadata_size")
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda unused: (_ for _ in ()).throw(
                               ArtifactError("artifact_json_constant")))
        require(isinstance(value, dict), "artifact_metadata_object")
        return value
    except (UnicodeError, json.JSONDecodeError):
        raise ArtifactError("artifact_metadata_json") from None


def _drain(source, destination, limit, name, errors, failed):
    """Retain an exact bounded prefix on refusal, without printing its contents."""
    try:
        total = 0
        while True:
            block = source.read(min(CHUNK, limit - total + 1))
            if not block:
                break
            require(destination.write(block) == len(block), "gh_capture_short_write")
            total += len(block)
            if total > limit:
                errors[name] = name + "_limit"
                failed.set()
                break
        destination.flush()
        os.fsync(destination.fileno())
    except Exception:
        errors.setdefault(name, name + "_capture_failed")
        failed.set()


def _run_gh(transport, label, route, output_name, maximum, gh_executable, timeout):
    """Exactly one owned GH process. Only that handle may be killed on refusal."""
    output = transport / output_name
    error_output = transport / (label + ".stderr.original.log")
    environment = dict(os.environ)
    for name in list(environment):
        if name.upper().startswith(("GIT_TRACE", "GH_TRACE")) or name.upper() in {
                "GIT_CURL_VERBOSE", "GH_DEBUG", "GH_FORCE_TTY", "GH_PAGER", "PAGER"}:
            environment.pop(name, None)
    environment.update({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
                        "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"})
    command = [str(gh_executable), "api", "--hostname", "github.com", "--method", "GET", route]
    failure = None
    code = None
    timed_out = False
    process = None
    kill_attempted = False
    errors = {}
    failed = threading.Event()
    threads = []
    with output.open("xb") as stdout, error_output.open("xb") as stderr:
        try:
            process = subprocess.Popen(command, cwd=str(transport), env=environment,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, shell=False)
            require(process.stdout is not None and process.stderr is not None, "gh_pipes_unavailable")
            for incoming, retained, cap, name in (
                    (process.stdout, stdout, maximum, "stdout"),
                    (process.stderr, stderr, MAX_STDERR_BYTES, "stderr")):
                worker = threading.Thread(target=_drain,
                    args=(incoming, retained, cap, name, errors, failed), daemon=True)
                worker.start()
                threads.append(worker)
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                remaining = deadline - time.monotonic()
                if failed.is_set() or remaining <= 0:
                    timed_out = remaining <= 0
                    failure = "gh_timeout" if timed_out else "gh_capture_failed"
                    # This is the Popen handle we own, never a PID lookup or another launch.
                    kill_attempted = True
                    process.kill()
                    break
                try:
                    process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    pass
            try:
                code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                failure = failure or "gh_terminal_unknown"
        except Exception:
            failure = failure or ("gh_launch_failed" if process is None else "gh_transport_failed")
            if process is not None and process.poll() is None and not kill_attempted:
                try:
                    kill_attempted = True
                    process.kill()
                    code = process.wait(timeout=5)
                except Exception:
                    failure = "gh_terminal_unknown"
        finally:
            for worker in threads:
                worker.join(timeout=5)
            if any(worker.is_alive() for worker in threads):
                failure = failure or "gh_capture_incomplete"
            if errors:
                failure = failure or "gh_capture_failed"
            if process is not None:
                for incoming in (process.stdout, process.stderr):
                    if incoming is not None and not any(worker.is_alive() for worker in threads):
                        incoming.close()
    _json_new(transport / (label + ".terminal.json"), {
        "schema": "motionwright.windows-tamper-artifact-transport/1",
        "launch_count": 1, "exit_code": code, "timed_out": timed_out,
        "failure": failure, "capture_errors": dict(errors),
        "stdout_complete": failure is None, "stderr_complete": failure is None,
    })
    require(failure is None, failure or "gh_transport_failed")
    require(code == 0, "gh_exit_nonzero")
    return output


def _run_repository(run, expected_source, expected_run):
    require(type(run.get("id")) is int and run["id"] == expected_run["run_id"]
            and run.get("head_sha") == expected_source
            and type(run.get("run_attempt")) is int and run["run_attempt"] == 1,
            "artifact_run_metadata_mismatch")
    repository, head = run.get("repository"), run.get("head_repository")
    require(isinstance(repository, dict) and isinstance(head, dict)
            and repository.get("full_name") == expected_run["repository"]
            and head.get("full_name") == expected_run["repository"]
            and _positive(repository.get("id")) and type(head.get("id")) is int
            and head["id"] == repository["id"], "artifact_repository_metadata_mismatch")
    # The same workflow is still running while these dependent matrix jobs execute.
    # Its global conclusion cannot be required to be success here.
    return repository["id"]


def _metadata(meta, artifact_id, artifact_sha256, expected_source, expected_run, repository_id):
    run = meta.get("workflow_run")
    require(type(meta.get("id")) is int and meta["id"] == artifact_id
            and meta.get("name") == ARTIFACT_NAME and meta.get("expired") is False
            and meta.get("digest") == "sha256:" + artifact_sha256,
            "artifact_metadata_identity_mismatch")
    size = meta.get("size_in_bytes")
    require(_positive(size) and size <= MAX_ARCHIVE_BYTES, "artifact_archive_size")
    require(isinstance(run, dict) and type(run.get("id")) is int
            and run["id"] == expected_run["run_id"] and run.get("head_sha") == expected_source
            and type(run.get("repository_id")) is int and run["repository_id"] == repository_id
            and type(run.get("head_repository_id")) is int
            and run["head_repository_id"] == repository_id, "artifact_metadata_run_mismatch")
    return {"artifact_id": artifact_id, "artifact_name": ARTIFACT_NAME,
            "artifact_bytes": size, "artifact_sha256": artifact_sha256,
            "source_sha": expected_source, "run": dict(expected_run)}


def _stamp(meta):
    return (meta.st_dev, meta.st_ino, meta.st_mode, meta.st_size,
            meta.st_mtime_ns, meta.st_ctime_ns)


def _regular(meta):
    require(stat.S_ISREG(meta.st_mode) and not stat.S_ISLNK(meta.st_mode)
            and not getattr(meta, "st_file_attributes", 0) & 0x400, "artifact_archive_not_regular")


def _hash(stream, size):
    stream.seek(0)
    digest, total = hashlib.sha256(), 0
    while block := stream.read(min(CHUNK, size - total + 1)):
        total += len(block)
        require(total <= size, "artifact_archive_length_mismatch")
        digest.update(block)
    require(total == size, "artifact_archive_length_mismatch")
    return digest.hexdigest()


def _envelope(stream, size):
    """Bound the central directory before zipfile allocates its member objects."""
    require(size >= 22, "artifact_zip_envelope")
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    offset = tail.rfind(b"PK\x05\x06")
    require(offset >= 0 and offset + 22 <= len(tail), "artifact_zip_envelope")
    header = struct.unpack_from("<4s4H2IH", tail, offset)
    unused, disk, central_disk, disk_entries, entries, central_bytes, central_offset, comment = header
    require(offset + 22 + comment == len(tail), "artifact_zip_trailing_bytes")
    require(disk == central_disk == 0 and disk_entries == entries
            and 0 < entries <= MAX_MEMBERS, "artifact_zip_member_bound")
    require(0 < central_bytes <= MAX_DIRECTORY_BYTES
            and central_offset + central_bytes == size - len(tail) + offset,
            "artifact_zip_directory_bound")
    stream.seek(0)
    require(stream.read(4) == b"PK\x03\x04", "artifact_zip_prefix")
    return entries


def _member_name(info):
    name = info.orig_filename
    require(name == info.filename and isinstance(name, str) and 0 < len(name) <= 512
            and name.isascii() and "\\" not in name, "artifact_zip_name")
    directory = name.endswith("/")
    relative = name[:-1] if directory else name
    parts = relative.split("/")
    require(0 < len(parts) <= 32, "artifact_zip_name")
    for part in parts:
        require(0 < len(part) <= 128 and part not in {".", ".."}
                and re.fullmatch(r"[A-Za-z0-9_. -]+", part) is not None
                and part == part.strip(" ."), "artifact_zip_name")
        stem = part.split(".", 1)[0].upper()
        require(stem not in {"CON", "PRN", "AUX", "NUL"}
                and re.fullmatch(r"(COM|LPT)[1-9]", stem) is None, "artifact_zip_device_name")
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    require(kind in {0, stat.S_IFDIR if directory else stat.S_IFREG}
            and not info.external_attr & 0x400
            and not info.external_attr & 0x40
            and (directory or not info.external_attr & 0x10), "artifact_zip_special_entry")
    require(not info.flag_bits & 1 and info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
            "artifact_zip_encoding")
    require(0 <= info.file_size <= MAX_MEMBER_BYTES
            and (not directory or info.file_size == 0), "artifact_zip_member_size")
    return parts, directory


def _plan(archive, declared_count):
    entries = archive.infolist()
    require(len(entries) == declared_count, "artifact_zip_member_count")
    nodes, explicit, plan = {}, set(), []
    total = 0
    for info in entries:
        parts, directory = _member_name(info)
        for index in range(1, len(parts) + 1):
            name = "/".join(parts[:index])
            key = name.casefold()
            kind = "directory" if index < len(parts) or directory else "file"
            require(key not in nodes or nodes[key] == (name, kind), "artifact_zip_alias_or_prefix")
            nodes[key] = (name, kind)
            require(len(nodes) <= MAX_MEMBERS, "artifact_zip_namespace_bound")
        key = "/".join(parts).casefold()
        require(key not in explicit, "artifact_zip_duplicate_member")
        explicit.add(key)
        total += info.file_size
        require(total <= MAX_UNPACKED_BYTES, "artifact_zip_total_size")
        plan.append((info, parts, directory))
    require(any(not directory for unused, unused_parts, directory in plan), "artifact_zip_no_files")
    return plan


def extract_archive(archive_path: Path, destination: Path, *, expected_bytes: int,
                    expected_sha256: str) -> None:
    """Verify the original ZIP and materialize regular files under one fresh root.

    Refusals preserve the archive and any already created output. No extractall,
    metadata application, overwrite, link following, rollback or retry is used.
    """
    archive_path, destination = Path(archive_path), Path(destination)
    require(_positive(expected_bytes) and expected_bytes <= MAX_ARCHIVE_BYTES
            and _sha(expected_sha256, 64), "artifact_archive_expectation")
    _plain_directory(archive_path.parent)
    _plain_directory(destination.parent)
    path_before = archive_path.lstat()
    _regular(path_before)
    require(path_before.st_size == expected_bytes, "artifact_archive_length_mismatch")
    try:
        with archive_path.open("rb") as stream:
            descriptor_before = os.fstat(stream.fileno())
            _regular(descriptor_before)
            # Path and descriptor ctime have different domains on Windows.
            require((path_before.st_dev, path_before.st_ino, path_before.st_size)
                    == (descriptor_before.st_dev, descriptor_before.st_ino, descriptor_before.st_size),
                    "artifact_archive_identity")
            require(_hash(stream, expected_bytes) == expected_sha256, "artifact_archive_digest_mismatch")
            count = _envelope(stream, expected_bytes)
            with zipfile.ZipFile(stream) as archive:
                plan = _plan(archive, count)
                _new_directory(destination)
                total = 0
                for info, parts, directory in plan:
                    target = destination.joinpath(*parts)
                    for parent in reversed(target.parent.relative_to(destination).parents):
                        if parent != Path("."):
                            location = destination / parent
                            if not location.exists():
                                location.mkdir()
                            _plain_directory(location)
                    if not target.parent.exists():
                        target.parent.mkdir()
                    _plain_directory(target.parent)
                    if directory:
                        if not target.exists():
                            target.mkdir()
                        _plain_directory(target)
                        continue
                    written = 0
                    with archive.open(info) as source, target.open("xb") as output:
                        while block := source.read(min(CHUNK, info.file_size - written + 1)):
                            written += len(block)
                            total += len(block)
                            require(written <= info.file_size and total <= MAX_UNPACKED_BYTES,
                                    "artifact_zip_expanded_bound")
                            require(output.write(block) == len(block), "artifact_extract_short_write")
                        require(written == info.file_size, "artifact_zip_member_length")
                        output.flush()
                        os.fsync(output.fileno())
                    _regular(target.lstat())
            require(_hash(stream, expected_bytes) == expected_sha256, "artifact_archive_changed")
            require(_stamp(os.fstat(stream.fileno())) == _stamp(descriptor_before)
                    and _stamp(archive_path.lstat()) == _stamp(path_before), "artifact_archive_changed")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError):
        raise ArtifactError("artifact_zip_invalid") from None


def receive_producer(root: Path, *, artifact_id: int, artifact_sha256: str,
                     expected_source: str, expected_run: dict, gh_executable="gh") -> dict:
    """Receive exactly one externally identified producer artifact, with originals."""
    run = _expected(artifact_id, artifact_sha256, expected_source, expected_run)
    require(isinstance(gh_executable, (str, os.PathLike)) and 0 < len(str(gh_executable)) <= 4096,
            "artifact_gh_executable")
    root = Path(root)
    _new_directory(root)
    transport = root / "transport"
    _new_directory(transport)
    prefix = "repos/" + run["repository"] + "/actions/"
    run_path = _run_gh(transport, "run", prefix + "runs/" + str(run["run_id"]),
                       "run.original.json", MAX_METADATA_BYTES, gh_executable, 60)
    repository_id = _run_repository(_json(run_path), expected_source, run)
    route = prefix + "artifacts/" + str(artifact_id)
    before_path = _run_gh(transport, "artifact-before", route, "artifact-before.original.json",
                          MAX_METADATA_BYTES, gh_executable, 60)
    binding = _metadata(_json(before_path), artifact_id, artifact_sha256,
                        expected_source, run, repository_id)
    archive = _run_gh(transport, "archive", route + "/zip", "archive.original.zip",
                      binding["artifact_bytes"], gh_executable, 180)
    after_path = _run_gh(transport, "artifact-after", route, "artifact-after.original.json",
                         MAX_METADATA_BYTES, gh_executable, 60)
    require(_metadata(_json(after_path), artifact_id, artifact_sha256,
                      expected_source, run, repository_id) == binding, "artifact_metadata_changed")
    extract_archive(archive, root / "producer", expected_bytes=binding["artifact_bytes"],
                    expected_sha256=artifact_sha256)
    _json_new(root / "binding.json", binding)
    return binding
