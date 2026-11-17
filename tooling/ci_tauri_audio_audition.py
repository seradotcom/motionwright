#!/usr/bin/env python3
"""Closed manual admission and one authenticated seal for Tauri audio development.

This module neither creates an OwnerInvocation nor dispatches an audition.
The ordinary product authorization and the real desktop harness remain separate.
Only the public recipient enters CI. No development receipt grants G2/G3.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

SCHEMA = "motionwright.ci.tauri-audio-development/1"
REPOSITORY = "seradotcom/motionwright"
WORKFLOW = REPOSITORY + "/.github/workflows/v05-original-native-mix.yml@refs/heads/"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_MEMBER = 8 * 1024 * 1024
MAX_TOTAL = 48 * 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_FILES = 256

class Rejected(RuntimeError):
    pass

def need(value, code):
    if not value:
        raise Rejected(code)

def strict(raw):
    need(type(raw) is bytes and 0 < len(raw) <= 512 * 1024, "JSON_BYTE_BOUND")
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(Rejected("JSON_CONSTANT")))
    need(type(value) is dict, "JSON_OBJECT_REQUIRED")
    return value

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def private_directory(path):
    metadata = path.lstat()
    need(path.is_absolute() and path.resolve(strict=True) == path and stat.S_ISDIR(metadata.st_mode)
         and not path.is_symlink() and metadata.st_uid == os.geteuid()
         and metadata.st_mode & 0o077 == 0, "PRIVATE_DIRECTORY_REQUIRED")
    return metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid, metadata.st_gid

def regular(path, maximum=MAX_MEMBER):
    need(path.is_absolute() and path.resolve(strict=True) == path
         and not any(parent.is_symlink() for parent in path.parents), "CANONICAL_FILE_REQUIRED")
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
         and before.st_mode & 0o077 == 0 and before.st_size <= maximum, "PRIVATE_REGULAR_FILE_REQUIRED")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    def key(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    try:
        need(key(os.fstat(fd)) == key(before), "FILE_CHANGED_AT_OPEN")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        need(len(raw) == before.st_size and key(os.fstat(fd)) == key(before)
             and key(path.lstat()) == key(before), "FILE_CHANGED_DURING_READ")
    finally:
        os.close(fd)
    return raw

def publish(path, value):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    publish_bytes(path, raw)

def publish_bytes(path, raw):
    identity = private_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    need(private_directory(path.parent) == identity and regular(path, MAX_ARCHIVE + 16384) == raw,
         "EXCLUSIVE_PUBLICATION_CHANGED")

def validate_environment(environ, expected_source):
    need(sys.platform == "linux", "LINUX_CI_REQUIRED")
    need(environ.get("CI") == "true" and environ.get("GITHUB_ACTIONS") == "true"
         and environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and environ.get("GITHUB_REPOSITORY") == REPOSITORY
         and environ.get("GITHUB_WORKFLOW_REF", "").startswith(WORKFLOW)
         and environ.get("MW_AUDIO_DIAGNOSTIC_SELECTION") == "tauri-audition",
         "CLOSED_MANUAL_AUDIO_SELECTION_REQUIRED")
    source = environ.get("GITHUB_SHA", "")
    need(SHA40.fullmatch(source) and source == expected_source, "SOURCE_SELECTION_MISMATCH")
    run = environ.get("GITHUB_RUN_ID", "")
    attempt = environ.get("GITHUB_RUN_ATTEMPT", "")
    need(re.fullmatch(r"[1-9][0-9]{0,19}", run) and attempt == "1", "FIRST_RUN_ATTEMPT_REQUIRED")
    return {"source_sha": source, "run_id": run, "run_attempt": attempt}

def git(repo, *arguments):
    result = subprocess.run(["git", "-C", str(repo), *arguments], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=20, check=False)
    need(result.returncode == 0 and len(result.stdout) <= 1024 * 1024, "SOURCE_GIT_OBSERVATION_FAILED")
    return result.stdout

def context(repo, expected_source):
    value = validate_environment(os.environ, expected_source)
    need(repo.is_absolute() and repo.resolve(strict=True) == repo, "SOURCE_ROOT_NOT_CANONICAL")
    need(git(repo, "rev-parse", "HEAD").decode().strip() == value["source_sha"], "CHECKOUT_HEAD_CHANGED")
    need(not git(repo, "status", "--porcelain=v1", "--untracked-files=no"), "TRACKED_SOURCE_CHANGED")
    lock = strict((repo / "SOURCE_LOCK.json").read_bytes())
    sdk = lock["dependencies"]["semwright"]["revision"]
    need(type(sdk) is str and SHA40.fullmatch(sdk), "SDK_PIN_INVALID")
    value["semwright_sha"] = sdk
    return value

def locations(environ):
    runner = Path(environ["RUNNER_TEMP"])
    need(runner.is_absolute() and runner.resolve(strict=True) == runner, "RUNNER_TEMP_NOT_CANONICAL")
    run = environ["GITHUB_RUN_ID"]
    return runner / "tauri-audition-ci", runner / f"motionwright-tauri-audition-{run}-1"

def resources(path):
    need(shutil.disk_usage(path).free >= 2 * 1024**3, "SEAL_DISK_RESERVE_UNAVAILABLE")
    values = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        key, _, value = line.partition(":")
        values[key] = value.strip().split()
    available = int(values["MemAvailable"][0]) * 1024
    need(available >= 1536 * 1024**2, "SEAL_MEMORY_RESERVE_UNAVAILABLE")
    return {"available_memory_bytes": available, "minimum_available_memory_bytes": 1536 * 1024**2,
            "available_disk_bytes": shutil.disk_usage(path).free, "minimum_disk_bytes": 2 * 1024**3,
            "maximum_selected_bytes": MAX_TOTAL, "maximum_archive_bytes": MAX_ARCHIVE,
            "maximum_member_bytes": MAX_MEMBER, "maximum_files": MAX_FILES}

def admit(repo, expected_source):
    current = context(repo, expected_source)
    output, private_root = locations(os.environ)
    need(not output.exists() and not private_root.exists(), "PRIOR_ATTEMPT_EXISTS")
    # Public-key validation occurs before creating any diagnostic namespace.
    import ci_private_evidence as custody
    recipient = strict(os.environ.get("MW_TAURI_AUDIO_PUBLIC_RECIPIENT_JSON", "").encode())
    custody.public_key(recipient)
    need(recipient["sha256"] == os.environ.get("MW_TAURI_AUDIO_RECIPIENT_SHA256"),
         "RECIPIENT_FINGERPRINT_MISMATCH")
    resource = resources(output.parent)
    output.mkdir(mode=0o700)
    publish(output / "recipient.public.json", recipient)
    publish(output / "admission.original.json", {
        "schema": SCHEMA, "context": current, "selection": "tauri-audition",
        "recipient_sha256": recipient["sha256"], "private_root": str(private_root),
        "source_lock_sha256": sha((repo / "SOURCE_LOCK.json").read_bytes()),
        "resources": resource, "automatic_retry": False, "native_calls": 0,
        "formal_G2": False, "formal_G3": False, "authorization": "development custody only",
    })
    print(json.dumps({"admitted": True, "selection": "tauri-audition",
                      "source_sha": current["source_sha"], "native_calls": 0}))

def inventory(root, *, owner_metadata=False):
    identity = private_directory(root)
    files, total = 0, 0
    for parent, directories, names in os.walk(root, followlinks=False):
        parent_path = Path(parent)
        # Product CAS may keep ordinary read/traverse bits under its private
        # root. No selected directory may permit another writer or be a link.
        meta = parent_path.lstat()
        need(stat.S_ISDIR(meta.st_mode) and not parent_path.is_symlink()
             and meta.st_uid == os.geteuid() and meta.st_mode & 0o022 == 0, "SNAPSHOT_DIRECTORY_POLICY")
        for name in directories:
            child = parent_path / name
            need(not child.is_symlink() or owner_metadata, "SNAPSHOT_DIRECTORY_LINK")
        for name in names + [name for name in directories if (parent_path / name).is_symlink()]:
            path = parent_path / name
            observed = path.lstat()
            if not stat.S_ISREG(observed.st_mode):
                # Closed GUI runtimes may retain a socket or lock symlink.
                # The generic owner snapshot records only lstat/readlink
                # metadata, never follows it, opens it or grants authority.
                need(owner_metadata and observed.st_uid == os.geteuid()
                     and (stat.S_ISSOCK(observed.st_mode) or stat.S_ISFIFO(observed.st_mode)
                          or stat.S_ISLNK(observed.st_mode)) and observed.st_size <= 4096,
                     "SNAPSHOT_NONREGULAR_NOT_ADMITTED")
                size = 4096
            else:
                size = len(regular(path))
            files += 1
            total += size
            need(files <= MAX_FILES and total <= MAX_TOTAL, "SNAPSHOT_SELECTION_BOUND")
    need(private_directory(root) == identity, "SNAPSHOT_ROOT_CHANGED")
    return files, total

def closed_writer_observed(closure):
    supervisor = closure.get("supervisor_pid")
    tree = closure.get("complete_empty_descendant_tree", {})
    processes = tree.get("processes")
    return (type(supervisor) is int and supervisor > 0 and closure.get("status") == "PASS"
            and tree.get("root_observed") is True and tree.get("truncated") is False
            and type(processes) is list and len(processes) == 1
            and type(processes[0]) is dict and type(processes[0].get("pid")) is int
            and processes[0]["pid"] == supervisor)

def seal(repo, expected_source):
    current = context(repo, expected_source)
    output, private_root = locations(os.environ)
    admission = strict(regular(output / "admission.original.json", 65536))
    recipient = strict(regular(output / "recipient.public.json", 4096))
    need(admission["context"] == current and admission["recipient_sha256"] == recipient["sha256"]
         and admission["private_root"] == str(private_root), "SEALED_CONTEXT_CHANGED")
    resource = resources(output)
    # The durable marker is consumed before one snapshot/AEAD attempt.
    publish(output / "seal-intent.original.json", {
        "schema": SCHEMA, "context": current, "resources": resource, "automatic_retry": False,
    })
    closed = False
    if private_root.exists() and (private_root / "process-closure.private.json").exists():
        closure = strict(regular(private_root / "process-closure.private.json"))
        closed = closed_writer_observed(closure)
    wrapper_ok = False
    wrapper_path = output / "window-manager" / "completion.original.json"
    wrapper_closure_path = output / "window-manager" / "wrapper-process-closure.original.json"
    if wrapper_path.exists() and wrapper_closure_path.exists():
        wrapper = strict(regular(wrapper_path, 65536))
        wrapper_closure = strict(regular(wrapper_closure_path))
        wrapper_ok = (wrapper.get("schema") == "motionwright.tauri-audio-window-manager/1"
                      and wrapper.get("context") == current and wrapper.get("status") == "PASS"
                      and type(wrapper.get("harness_exit_code")) is int and wrapper["harness_exit_code"] == 0
                      and wrapper.get("owned_window_manager_closed") is True
                      and wrapper.get("forced_kill_requested") is False
                      and wrapper_closure.get("supervisor_pid") == wrapper.get("supervisor_pid")
                      and closed_writer_observed(wrapper_closure))
    roots = {"ci": output}
    result = None
    if closed:
        roots["owner"] = private_root
        if (private_root / "result.private.json").exists():
            result = strict(regular(private_root / "result.private.json"))
    counts = [inventory(root, owner_metadata=(label == "owner"))
              for label, root in roots.items()]
    need(sum(item[0] for item in counts) <= MAX_FILES and sum(item[1] for item in counts) <= MAX_TOTAL,
         "COMBINED_SNAPSHOT_SELECTION_BOUND")
    import ci_private_evidence as custody
    raw, selected_complete = custody.gather_snapshot(
        roots, preserve_partial=True, max_archive_bytes=MAX_ARCHIVE)
    envelope = custody.encrypt_snapshot(raw, recipient, current)
    publish_bytes(output / "sealed-originals.bin", envelope)
    observed = bool(closed and wrapper_ok and selected_complete and result and result.get("schema") == "motionwright.tauri-audio-development/1"
                    and result.get("source_sha") == current["source_sha"]
                    and result.get("sdk_sha") == current["semwright_sha"]
                    and str(result.get("run_id")) == current["run_id"]
                    and str(result.get("run_attempt")) == current["run_attempt"]
                    and result.get("status") == "PASS"
                    and all(result.get(key) is True for key in (
                        "writer_process_closed", "ui_observed", "independent_readback", "original_wav_verified"))
                    and result.get("formal_G2") is False and result.get("formal_G3") is False)
    public = {
        "schema": SCHEMA, "context": current, "recipient_sha256": recipient["sha256"],
        "status": "DEVELOPMENT_TAURI_AUDITION_OBSERVED" if observed else "FAILED_OR_UNKNOWN",
        "ciphertext_bytes": len(envelope), "ciphertext_sha256": sha(envelope),
        "selected_originals_bytes": len(raw), "selected_originals_sha256": sha(raw),
        "private_case_selected": closed, "window_manager_wrapper_verified": wrapper_ok,
        "selected_originals_complete": selected_complete,
        "case_omitted_if_writer_closure_unconfirmed": not closed,
        "automatic_retry": False, "formal_G2": False, "formal_G3": False,
        "human_listening_acceptance": False, "release_authorized": False,
    }
    publish(output / "public-evidence.json", public)
    print(json.dumps(public, sort_keys=True))
    return 0 if observed else 1

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("admit", "seal"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == "admit":
            admit(args.repo, args.expected_source)
            return 0
        return seal(args.repo, args.expected_source)
    except (Rejected, ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        code = str(error) if isinstance(error, Rejected) else type(error).__name__
        print(json.dumps({"status": "REJECTED", "code": code, "automatic_retry": False,
                          "formal_G2": False, "formal_G3": False}))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
