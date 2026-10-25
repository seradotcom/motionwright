"""Retain bounded Linux first-use originals without interpreting their result.

Only a newly published, verified archive and its custody receipt are upload
inputs. The application data directory is never an upload-artifact glob.
Missing data and rejected custody cannot establish persistence or a GUI pass.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import zipfile

if __package__:
    from . import runtime_first_use_audit as audit
else:
    import runtime_first_use_audit as audit

SCHEMA = "motionwright.linux-first-use-custody/1"
ARCHIVE = "originals.zip"
RECEIPT = "custody.json"
MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_ENTRIES = 1500
MAX_MEMBERS = 192
DATABASE = "motionwright.sqlite3"
FAMILY = frozenset(DATABASE + suffix for suffix in ("", "-wal", "-shm", "-journal"))
NAMESPACE_FILES = (
    "reservation.json", "launch-intent.json", "installed-gate.json", "window.json",
    "helper.stdout", "helper.stderr", "isolated-gui/app-run.log",
    "isolated-gui/app-run-first.log", "isolated-gui/app-run-second.log",
)
REPOSITORY_FILES = (
    "linux-candidate.json", "linux-deb-smoke.json", "linux-appimage-smoke.json",
    "glib-patched-source.json", "glib-compiled-owner-tree.txt",
)
JOURNAL_FILES = frozenset(name + ".json" for name in (
    "launch-intent", "worker-start", "edit-intent", "edit-return",
    "create-intent", "create-return", "result", "parent-timeout", "parent-failure",
    "listeners-start", "listeners-ready", "event-loop-start", "first-step-start",
    "first-identity-start", "first-identity-return",
    "listener-ready-object-children-changed", "listener-ready-object-state-changed",
    "listener-ready-object-property-change", "listener-ready-object-text-changed",
    "listener-ready-window",
)) | {"worker.stdout", "worker.stderr"}


class CustodyError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise CustodyError(code)


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def plain(path, *, directory=False):
    audit.safe_path(str(path), kind="directory" if directory else "regular",
                    code="custody_link_or_nonregular_path")
    info = path.lstat()
    require(directory or info.st_nlink == 1, "custody_hardlink_forbidden")
    return info


def optional(path, *, directory=False):
    # lstat distinguishes a genuinely missing path from a broken symlink.
    # Existing ancestors are checked even when the leaf is absent.
    for parent in reversed(path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            return None
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "custody_link_or_nonregular_parent")
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    return plain(path, directory=directory)


def tree(root):
    """Bounded, descriptor-relative discovery; never descend through links."""
    first = optional(root, directory=True)
    if first is None:
        return {}, {}
    files, proofs = {}, {root: stamp(first)}
    budget = 0

    def visit(directory, descriptor, depth):
        nonlocal budget
        require(depth <= 64, "custody_directory_depth_bound")
        with os.scandir(descriptor) as entries:
            for entry in entries:
                budget += 1
                require(budget <= MAX_ENTRIES, "custody_discovery_entry_bound")
                name = entry.name
                require(name not in (".", "..") and "/" not in name
                        and "\\" not in name and ":" not in name
                        and all(32 <= ord(c) < 127 for c in name), "custody_unsafe_member_name")
                child = directory / name
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                require(not stat.S_ISLNK(info.st_mode), "custody_symlink_forbidden")
                require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode),
                        "custody_nonregular_forbidden")
                require(stat.S_ISDIR(info.st_mode) or info.st_nlink == 1,
                        "custody_hardlink_forbidden")
                proofs[child] = stamp(info)
                if stat.S_ISDIR(info.st_mode):
                    child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptor)
                    try:
                        require(stamp(os.fstat(child_fd)) == stamp(info), "custody_directory_changed")
                        visit(child, child_fd, depth + 1)
                        require(stamp(os.fstat(child_fd)) == stamp(info), "custody_directory_changed")
                    finally:
                        os.close(child_fd)
                else:
                    files[child] = info

    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        require(stamp(os.fstat(descriptor)) == stamp(first), "custody_directory_changed")
        visit(root, descriptor, 0)
        require(stamp(os.fstat(descriptor)) == stamp(first), "custody_directory_changed")
    finally:
        os.close(descriptor)
    return files, proofs


def select(namespace, repo):
    selected, proofs = {}, {}

    def add(path, name, limit, info=None):
        info = optional(path) if info is None else info
        if info is None:
            return
        require(name not in selected and len(selected) < MAX_MEMBERS,
                "custody_member_collision_or_bound")
        require(len(name.encode("utf-8")) <= 1024, "custody_member_name_bound")
        require(0 <= info.st_size <= limit, "custody_file_byte_bound")
        selected[name] = (path, limit, stamp(info))
        proofs[path] = stamp(info)

    for name in NAMESPACE_FILES:
        add(namespace / name, "installed/" + name, 8 * 1024 * 1024)
    for name in REPOSITORY_FILES:
        add(repo / "ci-evidence" / name, "ci-evidence/" + name, 8 * 1024 * 1024)
    journal = namespace / "isolated-gui/first-use-atspi"
    files, observed = tree(journal)
    proofs.update(observed)
    for path, info in files.items():
        name = path.relative_to(journal).as_posix()
        require(name in JOURNAL_FILES, "custody_unknown_journal_member")
        add(path, "installed/isolated-gui/first-use-atspi/" + name,
            16384 if name.endswith(".json") else 8 * 1024 * 1024, info)
    data = namespace / "isolated-gui/data"
    files, observed = tree(data)
    proofs.update(observed)
    databases = [path for path in files if path.name == DATABASE]
    family = [path for path in files if path.name.startswith(DATABASE)]
    require(len(databases) <= 1, "custody_ambiguous_database")
    require(not family or len(databases) == 1, "custody_orphan_database_sidecar")
    for path in family:
        require(path.name in FAMILY and path.parent == databases[0].parent,
                "custody_ambiguous_database_family")
        add(path, "installed/" + path.relative_to(namespace).as_posix(), MAX_FILE, files[path])
    require(sum(item[2][3] for item in selected.values()) <= MAX_TOTAL, "custody_total_byte_bound")
    return selected, proofs, "PRESENT_BYTES_ONLY" if databases else "NOT_PRESENT"


def copy_original(path, expected, limit, target=None):
    """Reuse the first-use audit's inode-bound open and ancestor rechecks."""
    digest, count = hashlib.sha256(), 0
    with audit.open_checked_regular(path, code="custody_original_invalid",
                                    changed_code="custody_original_changed") as source:
        before = os.fstat(source.fileno())
        require(stamp(before) == expected and before.st_nlink == 1
                and 0 <= before.st_size <= limit, "custody_original_changed")
        while block := source.read(min(1024 * 1024, before.st_size - count + 1)):
            count += len(block)
            require(count <= before.st_size, "custody_original_grew")
            digest.update(block)
            if target is not None:
                require(target.write(block) == len(block), "custody_short_write")
        require(count == before.st_size and stamp(os.fstat(source.fileno())) == expected,
                "custody_original_changed")
        audit._check_opened_path(path, before, "custody_original_changed")
        require(stamp(path.lstat()) == expected, "custody_original_changed")
    return {"bytes": count, "sha256": digest.hexdigest()}


def verify_archive(path, inventory):
    require(isinstance(inventory, dict) and len(inventory) <= MAX_MEMBERS,
            "custody_archive_inventory")
    for name, expected in inventory.items():
        require(isinstance(name, str) and 0 < len(name.encode("utf-8")) <= 1024
                and name.split("/")[0] in ("installed", "ci-evidence")
                and all(part not in ("", ".", "..") for part in name.split("/"))
                and "\\" not in name and ":" not in name
                and all(32 <= ord(c) < 127 for c in name)
                and isinstance(expected, dict) and set(expected) == {"bytes", "sha256"}
                and type(expected["bytes"]) is int and 0 <= expected["bytes"] <= MAX_FILE
                and isinstance(expected["sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]), "custody_archive_member")
    require(sum(item["bytes"] for item in inventory.values()) <= MAX_TOTAL,
            "custody_total_byte_bound")
    before = plain(path)
    with audit.open_checked_regular(path, code="custody_archive_invalid") as stream:
        require(stamp(os.fstat(stream.fileno())) == stamp(before), "custody_archive_changed")
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            require(len(infos) == len(inventory) <= MAX_MEMBERS
                    and {info.filename for info in infos} == set(inventory), "custody_archive_inventory")
            for info in infos:
                expected = inventory[info.filename]
                require(info.compress_type == zipfile.ZIP_STORED
                        and info.file_size == info.compress_size == expected["bytes"]
                        and not info.is_dir(), "custody_archive_member")
                digest, count = hashlib.sha256(), 0
                with archive.open(info) as source:
                    while block := source.read(1024 * 1024):
                        count += len(block)
                        require(count <= expected["bytes"], "custody_archive_member_grew")
                        digest.update(block)
                require({"bytes": count, "sha256": digest.hexdigest()} == expected,
                        "custody_archive_member_changed")
        require(stamp(os.fstat(stream.fileno())) == stamp(before), "custody_archive_changed")
        audit._check_opened_path(path, before, "custody_archive_changed")


def retain(namespace, repo, output, github):
    """Publish custody for any installed terminal; never run a GUI or SQLite."""
    plain(output, directory=True)
    require(not any(output.iterdir()), "custody_namespace_already_used")
    receipt = {"schema": SCHEMA, "github": github, "status": "REJECTED",
               "scope": "ORIGINAL_BYTES_ONLY", "installed_result": "NOT_INFERRED",
               "archive": None}
    code = 1
    try:
        selected, proofs, database = select(namespace, repo)
        stage, inventory = output / "originals.incomplete", {}
        descriptor = os.open(stage, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w+b") as destination:
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
                for name, (path, limit, expected) in sorted(selected.items()):
                    with archive.open(name, "w") as member:
                        inventory[name] = copy_original(path, expected, limit, member)
            destination.flush()
            os.fsync(destination.fileno())
        for path, expected in proofs.items():
            info = plain(path, directory=stat.S_ISDIR(expected[2]))
            require(stamp(info) == expected, "custody_original_set_changed")
        verify_archive(stage, inventory)
        archive_identity = copy_original(stage, stamp(plain(stage)), MAX_TOTAL + 1024 * 1024)
        directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.link(stage.name, ARCHIVE, src_dir_fd=directory, dst_dir_fd=directory,
                    follow_symlinks=False)
            os.unlink(stage.name, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)
        require(copy_original(output / ARCHIVE, stamp(plain(output / ARCHIVE)),
                              MAX_TOTAL + 1024 * 1024) == archive_identity, "custody_publication_changed")
        receipt.update(status="RETAINED", archive=archive_identity, members=inventory,
                       database=database)
        code = 0
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        receipt["reason"] = (str(error) if isinstance(error, (CustodyError, audit.AuditError))
                             else type(error).__name__)
    # This existing atomic create-only helper preserves a prior receipt and
    # never makes an incomplete report an upload input.
    audit.write_new_receipt(output / RECEIPT, receipt)
    return code


def admit_upload(output, github):
    plain(output / RECEIPT)
    receipt = audit.read_json(output / RECEIPT, code="custody_receipt_invalid", limit=1024 * 1024)
    require(receipt.get("schema") == SCHEMA and receipt.get("github") == github
            and receipt.get("scope") == "ORIGINAL_BYTES_ONLY"
            and receipt.get("installed_result") == "NOT_INFERRED", "custody_receipt_binding")
    path = output / ARCHIVE
    if receipt.get("status") == "REJECTED":
        require(receipt.get("archive") is None and not path.exists() and not path.is_symlink(),
                "custody_rejected_archive_present")
    else:
        require(receipt.get("status") == "RETAINED", "custody_receipt_status")
        inventory = receipt.get("members")
        require(isinstance(inventory, dict), "custody_receipt_inventory")
        actual = copy_original(path, stamp(plain(path)), MAX_TOTAL + 1024 * 1024)
        require(actual == receipt.get("archive"), "custody_archive_changed")
        verify_archive(path, inventory)
    # The caller prints this only after admission. A rejected source selection
    # may publish its safe refusal receipt, but never the rejected originals.
    return "ready=true"
