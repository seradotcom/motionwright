"""Private bounded originals for the three Hero profiles; no Native dispatch.

The archive and encryption stream to exclusive files. At most one selected
regular member (the existing 256 MiB bound) is retained in memory at a time.
The envelope is the existing ci_private_evidence RSA-OAEP/AES-GCM format.
Missing, unstable or unclosed originals remain explicit negative observations.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import time

import native_profiles_workflow as route

MIB = 1024 * 1024
FILE_MAX = 256 * MIB
TAR_OVERHEAD = 16 * MIB
MAX_JSON = 8 * MIB


def mark(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def check_deadline(deadline):
    route.need(deadline is None or time.monotonic() < deadline, "profiles-custody-original-deadline")


def file_digest(path, maximum, *, deadline=None):
    check_deadline(deadline)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        route.need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                   and 0 <= before.st_size <= maximum, "profiles-seal-file-bound")
        count, value = 0, hashlib.sha256()
        while block := os.read(fd, MIB):
            check_deadline(deadline)
            count += len(block)
            route.need(count <= maximum, "profiles-seal-file-growth")
            value.update(block)
        route.need(count == before.st_size and mark(before) == mark(os.fstat(fd)) == mark(Path(path).lstat()),
                   "profiles-seal-file-changed")
        return {"bytes": count, "sha256": value.hexdigest()}
    finally:
        os.close(fd)


def bounded_member(handles, root, relative, maximum, *, deadline=None):
    """Use the existing anchored parent handles; raw diagnostics stay private."""
    from native_evidence_projection import member_path, identity
    parents = handles.parent(root, member_path(relative))
    fd = None
    try:
        before_name = os.stat(Path(relative).name, dir_fd=parents[-1][0], follow_symlinks=False)
        route.need(stat.S_ISREG(before_name.st_mode) and before_name.st_nlink == 1
                   and 0 <= before_name.st_size <= maximum, "profiles-selected-member-bound")
        fd = os.open(Path(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=parents[-1][0])
        before = os.fstat(fd)
        route.need(mark(before) == mark(before_name), "profiles-selected-member-replaced")
        data = bytearray()
        while block := os.read(fd, min(65536, maximum + 1 - len(data))):
            check_deadline(deadline)
            data.extend(block)
            route.need(len(data) <= maximum, "profiles-selected-member-growth")
        after_name = os.stat(Path(relative).name, dir_fd=parents[-1][0], follow_symlinks=False)
        route.need(len(data) == before.st_size and mark(before) == mark(os.fstat(fd)) == mark(after_name)
                   and all(identity(os.fstat(parent)) == expected for parent, expected in parents),
                   "profiles-selected-member-changed")
        return bytes(data), {"device": before.st_dev, "inode": before.st_ino, "mode": stat.S_IMODE(before.st_mode),
                             "uid": before.st_uid, "gid": before.st_gid, "links": before.st_nlink}
    finally:
        if fd is not None:
            os.close(fd)
        handles.close_parents(parents)


def safe_relative(value):
    parts = value.split("/")
    route.need(type(value) is str and 0 < len(value) <= 4096 and "\\" not in value
               and all(part and part not in (".", "..") for part in parts)
               and all(ord(c) >= 32 and ord(c) != 127 for c in value), "profiles-selected-relative-name")
    return value


def selected_paths(handles, label, select, *, maximum=4096, deadline=None):
    """Finite, no-follow inventory; excluded private credentials are never read."""
    todo, files, directories, negative, count = [()], [], [], [], 0
    while todo:
        check_deadline(deadline)
        prefix = todo.pop()
        parents = handles.parent(label, (*prefix, "__inventory_leaf__"))
        try:
            with os.scandir(parents[-1][0]) as entries:
                rows = []
                for row in entries:
                    check_deadline(deadline)
                    route.need(len(rows) < maximum * 4, "profiles-selected-directory-bound")
                    rows.append(row)
            rows.sort(key=lambda row: row.name)
            for row in rows:
                parts = (*prefix, row.name)
                if not select(parts):
                    continue
                count += 1
                route.need(count <= maximum * 4, "profiles-selected-namespace-bound")
                relative = safe_relative("/".join(parts))
                before = os.stat(row.name, dir_fd=parents[-1][0], follow_symlinks=False)
                if stat.S_ISDIR(before.st_mode):
                    directories.append({"path": relative, "device": before.st_dev, "inode": before.st_ino,
                                        "mode": stat.S_IMODE(before.st_mode)})
                    todo.append(parts)
                elif stat.S_ISREG(before.st_mode):
                    route.need(len(files) < maximum, "profiles-selected-file-count")
                    files.append(relative)
                else:
                    negative.append({"path": relative, "status": "METADATA_ONLY_NONREGULAR", "mode": before.st_mode,
                                     "device": before.st_dev, "inode": before.st_ino, "followed_or_opened": False})
        finally:
            handles.close_parents(parents)
    return sorted(files), sorted(directories, key=lambda row: row["path"]), negative


class LimitedWriter:
    def __init__(self, stream, maximum, deadline=None):
        self.stream, self.maximum, self.count, self.deadline = stream, maximum, 0, deadline

    def write(self, data):
        check_deadline(self.deadline)
        route.need(self.count + len(data) <= self.maximum, "profiles-private-TAR-bound")
        result = self.stream.write(data)
        route.need(result == len(data), "profiles-private-TAR-short-write")
        self.count += result
        return result

    def flush(self):
        self.stream.flush()


def capture_tar(destination, roots, selection, *, current, scope, payload_max, files_max, claims, deadline=None):
    from native_evidence_projection import RootHandles
    rows, dirs, total, complete = [], {}, 0, True
    roster = {}
    # Reserve the output before inventory; it is outside every selected branch.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    handles = None
    try:
        handles = RootHandles({key: str(path) for key, path in roots.items()})
        with os.fdopen(fd, "wb", closefd=False) as sink:
            writer = LimitedWriter(sink, payload_max + TAR_OVERHEAD, deadline)
            with tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for label, root in sorted(roots.items()):
                    names, directories, negatives = selected_paths(handles, label, selection[label],
                        maximum=files_max, deadline=deadline)
                    dirs[label] = directories
                    for row in negatives:
                        rows.append({"root": label, **row})
                    for relative in names:
                        name = label + "/" + relative
                        route.need(len(rows) < files_max, "profiles-private-total-file-count")
                        maximum = (route.LIMITS["stream_bytes"] if relative.endswith((".log", ".stdout", ".stderr"))
                                   else MAX_JSON if relative.endswith((".json", ".ndjson")) else FILE_MAX)
                        try:
                            raw, identity = bounded_member(handles, label, relative, min(maximum, payload_max - total),
                                                           deadline=deadline)
                        except Exception as error:
                            complete = False
                            rows.append({"root": label, "path": relative, "status": "CAPTURE_FAILED",
                                         "error_type": type(error).__name__, "original_retained_in_place": True})
                            continue
                        total += len(raw)
                        route.need(total <= payload_max, "profiles-private-total-byte-bound")
                        row = {"root": label, "path": relative, "status": "INCLUDED", **route.descriptor(raw),
                               "identity": identity}
                        rows.append(row)
                        entry = tarfile.TarInfo(name)
                        entry.size, entry.mode, entry.mtime = len(raw), 0o600, 0
                        archive.addfile(entry, io.BytesIO(raw))
                        roster[name] = route.descriptor(raw)
                        del raw
                stable = handles.stable_roots()
                complete = complete and stable
                required_matched = None
                proof = claims.get("physical_proof")
                if proof is not None:
                    captured = {row["path"]: {key: row[key] for key in ("bytes", "sha256")}
                        for row in rows if row["root"] == "case" and row["status"] == "INCLUDED"}
                    required_matched = all(captured.get(name) == identity
                                           for name, identity in proof["required_originals"].items())
                    complete = complete and required_matched
                manifest = {"schema": "motionwright.native.hero-original-snapshot/1", "context": current,
                    "scope": scope, "members": rows, "directories": dirs, "selected_payload_bytes": total,
                    "selected_originals_complete": complete, "roots_stable": stable,
                    "required_physical_originals_matched": required_matched,
                    "complete_owner_state_claimed": False, "claims": claims,
                    "excluded_owner_branches": ["bin", "runtime", "config", "home", "state", "scratch"],
                    "credential_paths_opened": False, "automatic_retry": False, "G2_claimed": False}
                raw = route.canonical(manifest)
                route.need(len(raw) <= MAX_JSON, "profiles-private-manifest-bound")
                entry = tarfile.TarInfo("profile-snapshot-manifest.json")
                entry.size, entry.mode, entry.mtime = len(raw), 0o600, 0
                archive.addfile(entry, io.BytesIO(raw))
                roster[entry.name] = route.descriptor(raw)
            sink.flush()
            os.fsync(sink.fileno())
    finally:
        if handles is not None:
            handles.close()
        os.close(fd)
    actual = file_digest(destination, payload_max + TAR_OVERHEAD, deadline=deadline)
    # Validate the material archive once, streaming; no extraction or re-execution.
    seen = {}
    with tarfile.open(destination, mode="r|") as archive:
        for member in archive:
            check_deadline(deadline)
            route.need(member.name in roster and member.name not in seen and member.isreg()
                       and not member.linkname and not member.sparse
                       and member.pax_headers in ({}, {"path": member.name})
                       and member.size == roster[member.name]["bytes"], "profiles-private-TAR-roster")
            source, value, count = archive.extractfile(member), hashlib.sha256(), 0
            for block in iter(lambda: source.read(MIB), b""):
                check_deadline(deadline)
                count += len(block)
                route.need(count <= member.size, "profiles-private-TAR-member-growth")
                value.update(block)
            seen[member.name] = {"bytes": count, "sha256": value.hexdigest()}
    route.need(seen == roster and file_digest(destination, payload_max + TAR_OVERHEAD, deadline=deadline) == actual,
               "profiles-private-TAR-readback")
    return actual, manifest


def encrypt_tar(source, destination, recipient, current, *, maximum, deadline=None):
    from ci_private_evidence import MAGIC, SEAL_SCHEMA, public_key, validate_context
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    validate_context(current)
    plaintext = file_digest(source, maximum, deadline=deadline)
    key, nonce = os.urandom(32), os.urandom(12)
    wrapped = public_key(recipient).encrypt(key, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=MAGIC))
    header = {"schema": SEAL_SCHEMA, "algorithm": "RSA-OAEP-SHA256+AES-256-GCM",
        "recipient_spki_sha256": recipient["sha256"], "context": current,
        "nonce": base64.b64encode(nonce).decode(), "wrapped_key": base64.b64encode(wrapped).decode(),
        "plaintext_sha256": plaintext["sha256"], "plaintext_bytes": plaintext["bytes"]}
    aad = route.canonical(header)
    route.need(len(aad) <= 8192, "profiles-private-envelope-header")
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as sink, Path(source).open("rb") as original:
            before = os.fstat(original.fileno())
            route.need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                       and before.st_size == plaintext["bytes"], "profiles-private-encryption-input")
            sink.write(MAGIC + len(aad).to_bytes(4, "big") + aad)
            value, count = hashlib.sha256(), 0
            for block in iter(lambda: original.read(MIB), b""):
                check_deadline(deadline)
                count += len(block)
                route.need(count <= plaintext["bytes"], "profiles-private-encryption-growth")
                value.update(block)
                sink.write(encryptor.update(block))
            route.need(count == plaintext["bytes"] and value.hexdigest() == plaintext["sha256"]
                       and mark(before) == mark(os.fstat(original.fileno())) == mark(Path(source).lstat()),
                       "profiles-private-encryption-source-changed")
            sink.write(encryptor.finalize())
            sink.write(encryptor.tag)
            sink.flush()
            os.fsync(sink.fileno())
    finally:
        os.close(fd)
    return file_digest(destination, maximum + 8192 + 64, deadline=deadline), header


def seal(roots, selection, destination, *, current, request, scope, payload_max, claims, public_claims=None, deadline=None):
    check_deadline(deadline)
    destination.mkdir(mode=0o700, exist_ok=False)
    original = destination / "selected-originals.tar"
    archive, manifest = capture_tar(original, roots, selection, current=current, scope=scope,
        payload_max=payload_max, files_max=request["limits"]["files"], claims=claims, deadline=deadline)
    ciphertext, header = encrypt_tar(original, destination / "sealed-originals.bin", request["recipient"],
                                     current, maximum=payload_max + TAR_OVERHEAD, deadline=deadline)
    public = {"schema": "motionwright.native.hero-private-custody/1", "context": current, "scope": scope,
        "recipient_spki_sha256": request["recipient"]["sha256"], "archive": archive, "ciphertext": ciphertext,
        "selected_originals_complete": manifest["selected_originals_complete"],
        "selected_member_count": sum(row["status"] == "INCLUDED" for row in manifest["members"]),
        "partial_or_negative_members": sum(row["status"] == "CAPTURE_FAILED" for row in manifest["members"]),
        "metadata_only_nonregular_members": sum(row["status"] == "METADATA_ONLY_NONREGULAR" for row in manifest["members"]),
        "complete_owner_state_claimed": False, "automatic_retry": False, "G2_claimed": False,
        "release_authorized": False, "claims": claims if public_claims is None else public_claims}
    route.write(destination / "public.json", public)
    return public


def seal_preparation(root, current, request):
    import native_owner_resources as resources
    runtime = Path(os.environ["RUNNER_TEMP"]) / ("mw-mlt-g2-control-" + current["run_id"] + "-1")
    source = Path(os.environ["GITHUB_WORKSPACE"])
    journal = Path(os.environ["MW_NATIVE_RESOURCE_ORIGIN"]).parent / "journal"
    prefixes = {tuple(name.split("/")[:length]) for name in route.SOURCE_PATHS for length in range(1, len(name.split("/")) + 1)}
    result = seal({"preparation": root, "runtime": runtime, "source": source, "resources": journal},
        {"preparation": lambda parts: parts[0] not in {*route.ROSTER, "prepared-sealed", "session-sealed", "permissions"},
         "runtime": lambda parts: True, "source": lambda parts: parts in prefixes,
         "resources": resources.journal_selection("prepare")}, root / "prepared-sealed",
        current=current, request=request, scope="shared-preparation-only", payload_max=240 * MIB,
        claims={"native_calls": 0, "owner_authority_inherited": False, "profiles": list(route.ROSTER),
                "active_guard_completion_claimed": False, "active_guard_capture": "ORIGINAL_PREFIX_ONLY"})
    return result


def case_selection(parts, *, cohort_empty):
    if parts[0] == "control":
        return len(parts) == 1 or not parts[-1].startswith("custody.")
    if not cohort_empty:
        return False
    if parts[0] == "evidence":
        return True
    if parts[0] == "owner":
        return len(parts) == 1 or parts[1] in {"project", "output", "motionwright-data", "motionwright-import"}
    return False


def seal_case(root, profile, current, request):
    case = route.case_path(root, profile)
    route.need(Path("/proc/self/cgroup").read_text().splitlines()
               == ["0::/motionwright-hero-" + current["run_id"] + "-1/custody"], "profiles-custody-owned-cohort")
    observation = route.strict(route.read(case / "control/native-observation.original.json"))
    route.need(observation["context"] == current and observation["profile"] == profile,
               "profiles-custody-native-observation-binding")
    group = route.group(current, "work", initially_empty=False)
    try:
        empty = not group.populated()
    finally:
        group.close()
    # A held/unknown process closure permits only stable control diagnostics.
    # It never authorizes opening an owner Store or live media tree.
    empty = empty and observation.get("cohort_empty") is True
    proof, error_type = None, None
    if empty and observation.get("work_terminal_complete") is True and observation.get("kernel_complete") is True:
        try:
            proof = route.case_proof(case, profile, current)
        except Exception as error:
            error_type = type(error).__name__
    proof_identity = None
    if proof is not None:
        proof_raw = route.canonical(proof)
        route.need(len(proof_raw) <= 256 * 1024, "profiles-physical-proof-budget")
        route.write(case / "control/physical-proof.original.json", proof_raw)
        proof_identity = route.descriptor(proof_raw)
    result = seal({"case": case}, {"case": lambda parts: case_selection(parts, cohort_empty=empty)}, case / "sealed",
        current=current, request=request, scope="profile-" + profile, payload_max=request["limits"]["capsule_bytes"],
        claims={"profile": profile, "cohort_empty_observed": empty, "physical_proof": proof,
                "physical_proof_error_type": error_type, "Native_outcome_inferred_from_exit_code": False},
        public_claims={"profile": profile, "cohort_empty_observed": empty,
                "physical_proof": proof_identity, "independent_review_pending": True,
                "physical_proof_error_type": error_type, "Native_outcome_inferred_from_exit_code": False})
    # An authentic negative can be safely retained and uploaded. It does not
    # permit the next profile; the owner checks work/proof/closure separately.
    return result


def seal_session(root, current, request):
    import native_owner_resources as resources
    # Preserve the final upload minute inside the unchanged original clock.
    _, _, _, _, clock, _, _ = route.load_originals(current)
    deadline = min(clock["deadline_monotonic"] - 60, time.monotonic() + 58)
    check_deadline(deadline)
    outcomes = []
    for profile in route.ROSTER:
        case = route.case_path(root, profile)
        started = (case / "control/owner.started.original.json").is_file()
        final = case / "control/closed.original.json"
        value = route.strict(route.read(final)) if final.is_file() else None
        outcomes.append({"profile": profile, "started": started,
            "outcome": "NOT_STARTED" if not started else value["native_outcome"] if value else "UNKNOWN_NO_RETRY",
            "physical_execution_completed": bool(value and value.get("physical_execution_completed"))})
    def selected(parts):
        if parts[0] in {"prepared-sealed", "session-sealed"}:
            return False
        if parts[0] in route.ROSTER:
            return len(parts) == 1 or parts[1] == "control" or (parts[1] == "sealed"
                and (len(parts) == 2 or parts[2] == "public.json"))
        return True
    journal = Path(os.environ["MW_NATIVE_RESOURCE_ORIGIN"]).parent / "journal"
    roots = {"session": root, "resources": journal}
    selection = {"session": selected, "resources": resources.journal_selection("seal-session")}
    runtime = Path(os.environ["RUNNER_TEMP"]) / ("mw-mlt-g2-control-" + current["run_id"] + "-1")
    if runtime.is_dir():
        roots["runtime"] = runtime
        selection["runtime"] = lambda parts: True
    return seal(roots, selection, root / "session-sealed", current=current,
        request=request, scope="session-originals-and-final-terminals", payload_max=240 * MIB, deadline=deadline,
        claims={"profiles": outcomes, "physical_profiles_complete": sum(row["physical_execution_completed"] for row in outcomes),
                "required_profiles": 3, "historical_Graph_owners_reused": False,
                "source_review_or_flow_UNKNOWN_changed": False,
                "prior_guard_terminals_captured": True, "active_guard_completion_claimed": False,
                "active_guard_capture": "ORIGINAL_PREFIX_ONLY"})
