#!/usr/bin/env python3
"""One encrypted diagnostic selection; UNKNOWN never authorizes Store capture.

This reader performs no process launch, RPC, cleanup, retry, or mutation of an
original. Stable bytes are observations at read time, not evidence of EOF or
quiescence. Only the existing review recipient can recover the selection.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile

import ci_private_evidence as seal

MIB = 1024 * 1024
# A withheld slice of Graph's existing 104 MiB/1232 shared allocation.
RESERVATION = {"bytes": 32 * MIB, "members": 4}
MAX_DATA, MAX_FILE, MAX_FILES, MAX_OBSERVATIONS = 24 * MIB, 4 * MIB, 512, 2048
MAX_MANIFEST = 2 * MIB
SCHEMA = "motionwright.graph-negative-originals/1"
FILE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,240}\Z")
CASE_FIXED = (
    "parent-source-binding.original.json", "parent-close-error.original.json",
    "owner-parent-terminal.original.json", "initializer.original.json",
    "initializer-store-physical.original.json", "owner-spec.original.json",
    "owner-launch.intent.original.json", "cleanup-control.original.jsonl",
    "sdk-ticket-precharge.original.json", "sdk-ticket-roster-binding.original.json",
    "owner-original-allocation.original.json",
    "owner/owner-final.original.json", "owner/owner-error.original.json",
    "owner/owner-complete-close.original.json", "owner/owner-close.json",
    "owner/complete-descendant-scope.original.json", "owner/error-cohort.original.json",
)
CASE_CLOSURE = {
    "graph-source-review": "closed-graph-store",
    "graph-flow": "closed-flow-store",
    "graph-lost-reply": "closed-graph-lost-reply-store",
    "graph-removal": "closed-graph-removal-store",
    "graph-label-causal-diagram": "closed-graph-label-store",
    "graph-label-system-flow": "closed-graph-label-store",
}
CLOSURE_SUFFIXES = ("-service-closure.json", "-outcomes.json", "-outcomes-before-capture.json")
PARENT_RECORD = re.compile(r"(?:reservation-\d+|(?:launch|exit)-[A-Za-z0-9_-]+-\d+|"
    r"client-[A-Za-z0-9_-]+|control-\d+|sdk-ticket-[A-Za-z0-9_-]+(?:\.intent)?)\.original\.(?:json|jsonl)\Z")
OWNER_RECORD = re.compile(r"[A-Za-z0-9_.-]+(?:\.json|\.stdout\.original|\.stderr\.original)\Z")
RAW_RECORD = re.compile(r"[A-Za-z0-9_-]+\.(?:stdout|stderr)\.original\Z")
JOURNAL = re.compile(r"[A-Za-z0-9_-]+\.connection\.json\.graph-originals\Z")
TRACE_DIRECTORY = re.compile(r"semantic-graph-service-[A-Za-z0-9_-]{1,210}\Z")


def require(ok, code):
    if not ok:
        raise seal.CustodyRejected(code)


def identity(info):
    return (*seal.metadata_identity(info), info.st_nlink)


class SelectedTree:
    """Open beneath pinned directory handles, without following any alias."""
    def __init__(self, root):
        self.root = Path(root)
        require(self.root.is_absolute() and self.root.resolve(strict=True) == self.root
                and not any(p.is_symlink() for p in (self.root, *self.root.parents)), "selection_root")

    @contextmanager
    def directory(self, relative=""):
        parts = relative.split("/") if relative else []
        require(all(FILE_NAME.fullmatch(p) and p not in (".", "..") for p in parts), "selection_name")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        handles = [os.open(self.root, flags)]
        try:
            for part in parts:
                handles.append(os.open(part, flags, dir_fd=handles[-1]))
            yield handles[-1]
            require(self.root.resolve(strict=True) == self.root
                    and not any(p.is_symlink() for p in (self.root, *self.root.parents))
                    and (self.root.lstat().st_dev, self.root.lstat().st_ino)
                        == (os.fstat(handles[0]).st_dev, os.fstat(handles[0]).st_ino), "selection_root_changed")
            for index, part in enumerate(parts):
                named = os.stat(part, dir_fd=handles[index], follow_symlinks=False)
                opened = os.fstat(handles[index + 1])
                require(stat.S_ISDIR(named.st_mode) and (named.st_dev, named.st_ino)
                        == (opened.st_dev, opened.st_ino), "selection_parent_changed")
        finally:
            for fd in reversed(handles):
                os.close(fd)

    def names(self, relative, maximum):
        with self.directory(relative) as fd:
            before = identity(os.fstat(fd))
            names = []
            with os.scandir(fd) as stream:
                for entry in stream:
                    require(len(names) < maximum and FILE_NAME.fullmatch(entry.name)
                            and entry.name not in (".", ".."), "selection_directory_bound")
                    names.append(entry.name)
            require(identity(os.fstat(fd)) == before, "selection_directory_changed")
            return sorted(names)

    def info(self, relative):
        parent, _, name = relative.rpartition("/")
        require(FILE_NAME.fullmatch(name) and name not in (".", ".."), "selection_name")
        with self.directory(parent) as fd:
            return os.stat(name, dir_fd=fd, follow_symlinks=False)

    def read(self, relative, maximum=MAX_FILE):
        parent, _, name = relative.rpartition("/")
        require(FILE_NAME.fullmatch(name) and name not in (".", ".."), "selection_name")
        with self.directory(parent) as directory:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                before = os.fstat(fd)
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                        and 0 <= before.st_size <= maximum, "selection_file_bound_or_alias")
                chunks, total = [], 0
                while True:
                    chunk = os.read(fd, min(65536, maximum + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk); total += len(chunk)
                    require(total <= maximum, "selection_file_grew")
                require(total == before.st_size and identity(os.fstat(fd)) == identity(before)
                        == identity(os.stat(name, dir_fd=directory, follow_symlinks=False)), "selection_file_changed")
                data = b"".join(chunks)
                return data, {"bytes": total, "sha256": seal.sha(data), "identity": list(identity(before))}
            finally:
                os.close(fd)


def shared_usage(tree, case_directories, maximum_bytes, maximum_members, case_root="e/graph-cases"):
    """Count the shared namespace; existing producer/case slices stay charged."""
    excluded = {"e/graph-producer", *(case_root + "/" + case for case in case_directories)}
    pending, total, members = ["e", "tools"], 0, 0
    while pending:
        relative = pending.pop()
        observed = tree.info(relative)
        require(not stat.S_ISLNK(observed.st_mode), "shared_alias")
        if relative in excluded:
            require(stat.S_ISDIR(observed.st_mode), "shared_prepaid_root")
            continue
        members += 1
        require(members <= maximum_members, "shared_member_budget")
        if stat.S_ISDIR(observed.st_mode):
            pending.extend(relative + "/" + name for name in tree.names(relative, maximum_members))
            require(members + len(pending) <= maximum_members + len(excluded), "shared_pending_budget")
        else:
            require(stat.S_ISREG(observed.st_mode) and observed.st_nlink == 1, "shared_nonregular")
            total += observed.st_size
            require(total <= maximum_bytes, "shared_byte_budget")
    require(total + RESERVATION["bytes"] <= maximum_bytes
            and members + RESERVATION["members"] <= maximum_members, "negative_shared_reservation")
    return {"bytes": total, "members": members, "withheld": RESERVATION}


def collect(tree, cases, binding, case_root="e/graph-cases"):
    """Fixed diagnostic namespaces only; all misses remain explicit and private."""
    records, originals, seen, total = [], [], set(), 0

    def note(relative, status, **extra):
        if len(records) >= MAX_OBSERVATIONS - 1:
            if len(records) == MAX_OBSERVATIONS - 1:
                records.append({"status": "REMAINING_SELECTION_NOT_CAPTURED_OBSERVATION_BOUND", "automatic_retry": False})
            return
        records.append({"path": relative, "status": status, "automatic_retry": False, **extra})

    def capture(relative, maximum=MAX_FILE):
        nonlocal total
        if relative in seen:
            return
        seen.add(relative)
        if len(records) >= MAX_OBSERVATIONS - 1:
            note(relative, "NOT_CAPTURED_OBSERVATION_BOUND"); return
        try:
            if len(originals) >= MAX_FILES or total >= MAX_DATA:
                note(relative, "NOT_CAPTURED_BUDGET"); return
            data, observation = tree.read(relative, min(maximum, MAX_DATA - total))
            total += len(data); originals.append((relative, data))
            note(relative, "STABLE_AT_READ_TIME_ONLY", observation=observation)
        except FileNotFoundError:
            note(relative, "ABSENT_AT_READ_TIME")
        except (OSError, seal.CustodyRejected) as error:
            # Do not copy exception text, a path, partial bytes, or a live prefix.
            note(relative, "NOT_CAPTURED", error_type=type(error).__name__)

    def listing(relative, pattern, maximum=384):
        if len(records) >= MAX_OBSERVATIONS - 1:
            note(relative, "NOT_CAPTURED_OBSERVATION_BOUND"); return []
        try:
            names = tree.names(relative, maximum)
            note(relative, "DIRECTORY_LIST_OBSERVED", selected=[name for name in names if pattern.fullmatch(name)])
            return [name for name in names if pattern.fullmatch(name)]
        except FileNotFoundError:
            note(relative, "ABSENT_AT_READ_TIME")
        except (OSError, seal.CustodyRejected) as error:
            note(relative, "LIST_NOT_CAPTURED", error_type=type(error).__name__)
        return []

    def journal(relative, depth=0):
        if depth > 6:
            note(relative, "NOT_CAPTURED_DEPTH_BOUND"); return
        for name in listing(relative, FILE_NAME):
            child = relative + "/" + name
            try:
                info = tree.info(child)
                if stat.S_ISDIR(info.st_mode):
                    journal(child, depth + 1)
                else:
                    capture(child)
            except (OSError, seal.CustodyRejected) as error:
                note(child, "NOT_CAPTURED", error_type=type(error).__name__)

    # Preserve the actual first error before optional detail can exhaust space.
    for name in ("receipt.json", "graph-execution.json", "graph-cases.command.json",
                 "graph-cases.stderr.log", "graph-cases.stdout.log", "r74-wait-complete.original.json"):
        capture("e/" + name, MIB if name.endswith(".log") else MAX_FILE)
    for name in ("e/graph-producer/producer-index.original.json", "graph-review/carrier.original.json",
                 "tools/graph-deadline.original.json", "tools/graph-phase-cases.intent.original.json",
                 "tools/graph-phase-cases.terminal.original.json", "tools/graph-phase-closure.intent.original.json",
                 "tools/graph-phase-closure.terminal.original.json"):
        capture(name)
    for case, directory in cases:
        prefix = case_root + "/" + directory
        capture("e/graph-producer/consumption/" + case + ".original.json")
        for name in CASE_FIXED:
            capture(prefix + "/" + name)
        # Only these source-authored JSON siblings are diagnostic inputs. The
        # Store directory with the same basename is never selected or opened.
        if case in CASE_CLOSURE:
            for suffix in CLOSURE_SUFFIXES:
                capture(prefix + "/artifacts/" + CASE_CLOSURE[case] + suffix)
        for name in listing(prefix, PARENT_RECORD, 128):
            capture(prefix + "/" + name)
        for name in listing(prefix + "/raw", RAW_RECORD, 64):
            capture(prefix + "/raw/" + name, MIB)
        for name in listing(prefix + "/owner", OWNER_RECORD, 768):
            capture(prefix + "/owner/" + name, MIB if name.endswith(".original") else MAX_FILE)
    for name in listing(case_root, TRACE_DIRECTORY, 128):
        for leaf in ("error-context.md", "trace.zip", "test-failed-1.png"):
            capture(case_root + "/" + name + "/" + leaf)
    for _case, directory in cases:
        clients = case_root + "/" + directory + "/owner/clients"
        for name in listing(clients, JOURNAL, 128):
            journal(clients + "/" + name)
    manifest = {"schema": SCHEMA, "scope": "SELECTED_NEGATIVE_BYTES_AT_READ_TIME_ONLY",
        "binding": binding, "records": records, "captured_files": len(originals), "captured_bytes": total,
        "originals_modified": False, "automatic_retry": False, "effects": "UNKNOWN",
        "quiescence": "UNKNOWN", "complete_owner_state_claimed": False, "store_capture_permitted": False,
        "formal_acceptance": False, "native_acceptance": False, "public_original_upload": "HOLD"}
    encoded = seal.json_bytes(manifest)
    require(len(encoded) <= MAX_MANIFEST, "negative_manifest_budget")
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in originals + [("negative-manifest.json", encoded)]:
            info = tarfile.TarInfo(name); info.mode = 0o600; info.mtime = 0; info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    raw = stream.getvalue()
    require(len(raw) + 65536 <= RESERVATION["bytes"], "negative_tar_budget")
    return raw


def seal_once(root, cases, binding, recipient, shared_budget, admission, *, case_root="e/graph-cases", progress=None):
    """Reserve exclusively before reading; an interrupted seal cannot be replayed."""
    if progress is not None:
        progress["stage"] = "SEAL_BINDING"
    require(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "negative_linux_handles_required")
    if case_root == "e/graph-cases":
        require(len(cases) == 6 and [directory for _case, directory in cases] == ["g" + str(i) for i in range(1, 7)]
                and len({case for case, _directory in cases}) == 6 and "continuation" not in binding, "negative_case_roster")
    else:
        expected = [("graph-flow", "g1"), ("graph-label-causal-diagram", "g2"),
                    ("graph-label-system-flow", "g3"), ("graph-lost-reply", "g4"), ("graph-removal", "g5")]
        run = binding["context"]["run_id"]
        require(run != "42356210001" and re.fullmatch(r"[1-9][0-9]*", run)
                and case_root == "e/gc" + run + "-1" and cases == expected
                and binding.get("continuation", {}).get("selector") == "unstarted-after-42356210001"
                and binding["continuation"].get("origin", {}).get("run_id") == "42356210001",
                "negative_continuation_case_roster")
    seal.validate_context(binding["context"])
    seal.public_key(recipient)
    require(recipient["sha256"] == binding["recipient_spki_sha256"], "negative_recipient_changed")
    tree = SelectedTree(root)
    if progress is not None:
        progress["stage"] = "SHARED_BEFORE"
    before = shared_usage(tree, [directory for _case, directory in cases], **shared_budget, case_root=case_root)
    destination = Path(root) / "graph-negative"
    if progress is not None:
        progress["stage"] = "ATTEMPT"
    destination.mkdir(mode=0o700)  # O_EXCL directory creation is the one-attempt latch.
    intent = {"schema": SCHEMA, "binding": binding, "admission": admission,
              "shared_before": before, "automatic_retry": False, "formal_acceptance": False}
    seal.new_json(destination / "attempt.original.json", intent)
    if progress is not None:
        progress["stage"] = "COLLECT"
    plaintext = collect(tree, cases, binding, case_root)
    if progress is not None:
        progress["stage"] = "ENCRYPT"
    ciphertext = seal.encrypt_snapshot(plaintext, recipient, binding["context"])
    del plaintext
    require(len(ciphertext) + 32768 <= RESERVATION["bytes"], "negative_ciphertext_budget")
    if progress is not None:
        progress["stage"] = "SHARED_AFTER"
    after = shared_usage(tree, [directory for _case, directory in cases], **shared_budget, case_root=case_root)
    if progress is not None:
        progress["stage"] = "CIPHERTEXT_WRITE"
    seal.new_bytes(destination / "negative-originals.mwseal", ciphertext)
    if progress is not None:
        progress["stage"] = "CARRIER"
    carrier = {"schema": SCHEMA, "meaning": "ENCRYPTED_NEGATIVE_DIAGNOSTIC_ONLY",
        "binding": binding, "envelope": {"filename": "negative-originals.mwseal", "bytes": len(ciphertext),
            "sha256": hashlib.sha256(ciphertext).hexdigest()}, "shared_after": after,
        "formal_acceptance": False, "effects": "UNKNOWN", "quiescence": "UNKNOWN",
        "public_original_upload": "HOLD", "automatic_retry": False}
    require(len(seal.json_bytes(intent)) + len(seal.json_bytes(carrier)) <= 32768, "negative_public_metadata_budget")
    seal.new_json(destination / "carrier.original.json", carrier)
    return carrier
