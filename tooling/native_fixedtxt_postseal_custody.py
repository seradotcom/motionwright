#!/usr/bin/env python3
"""Retain an already closed FixedTxt seal guard; never enter or retry Native.

The original session capsule cannot contain its own future guard terminal.
This later, bounded custody observation retains only that closed guard and
binds it to the unchanged session ciphertext. Its own completion is not
claimed by the snapshot it is still producing.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

import native_owner_resources as resources

MIB = 1024 * 1024
MAX_JSON = 65536
MAX_MANIFEST = 512 * 1024
MAX_SESSION_CIPHER = 16 * MIB
MAX_POSTSEAL = 2 * MIB
ACTION = "seal-session"
PREFIX = "owner_resources/commands/" + ACTION + "/"
RESOURCE_NAMES = ("owner_resources/origin.original.json",
                  *(PREFIX + name for name in resources.CLOSED_FILES))
STREAM_NAMES = (PREFIX + "stdout.original", PREFIX + "stderr.original")
SESSION_LIMITS = {
    "session/session-clock.original.json": MAX_JSON,
    "session/session-template.original.json": 49152,
    "session/runtime-bootstrap.original.json": 16384,
    "session/resource-session-manifest.original.json": MAX_MANIFEST,
    "session/session-public.json": MAX_JSON,
}
LIMITS = {**dict.fromkeys((*RESOURCE_NAMES, *STREAM_NAMES), MAX_JSON), **SESSION_LIMITS}
SNAPSHOT_SCHEMA = "motionwright.native.fixedtxt-postseal-custody/1"
PUBLIC_SCHEMA = "motionwright.native.fixedtxt-postseal-public/1"
SELF = "tooling/native_fixedtxt_postseal_custody.py"


class PostsealRejected(ValueError):
    pass


def need(value, code):
    if not value:
        raise PostsealRejected(code)


def descriptor(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode()


def strict(raw, maximum=MAX_JSON):
    from native_fixedtxt_g2_admission import strict as checked
    return checked(raw, maximum)


def check_time(now, start, deadline):
    need(all(type(n) in (int, float) and math.isfinite(n) for n in (now, start, deadline))
         and 0 <= start <= now < deadline, "postseal_original_deadline")


def validate_originals(originals, session_ciphertext, *, current, recipient_sha256,
                       helper_sha256, observed_monotonic, boot_id):
    """Pure contract over literal retained bytes, not a Native success oracle."""
    from ci_private_evidence import MAGIC, SEAL_SCHEMA, validate_context
    from native_fixedtxt_session import ROSTER, SESSION_SCHEMA
    validate_context(current)
    need(type(originals) is dict and set(originals) == set(LIMITS)
         and all(type(raw) is bytes and (0 <= len(raw) if name in STREAM_NAMES else 0 < len(raw))
                 and len(raw) <= LIMITS[name] for name, raw in originals.items()),
         "postseal_exact_original_roster")
    held = {name.removeprefix("owner_resources/"): originals[name] for name in RESOURCE_NAMES}
    guard = resources.validate_resource_originals(held, scope="fixedtxt", current=current,
        action=ACTION, case=None, completed=True, helper_sha256=helper_sha256)
    origin = strict(originals["owner_resources/origin.original.json"])
    need(origin["job"] == "fixedtxt-session-owner", "postseal_session_owner_only")
    intent = strict(originals[PREFIX + "intent.original.json"])
    terminal = strict(originals[PREFIX + "terminal.original.json"])
    expected_argv = [origin["base_temp"] + "/mw-fixedtxt-custody-python-" + current["run_id"] + "-1/bin/python",
                     "-B", "-E", "-s", origin["source_root"] + "/tooling/native_fixedtxt_session.py",
                     "--seal-session"]
    need(intent["argv"] == expected_argv, "postseal_closed_guard_command")
    need(intent["stream_bytes_each"] == MAX_JSON, "postseal_unchanged_stream_limit")
    for name in ("stdout", "stderr"):
        raw = originals[PREFIX + name + ".original"]
        row = terminal["streams"][name]
        need(row["retained_bytes"] == row["observed_bytes"] == len(raw)
             and row["sha256"] == descriptor(raw)["sha256"] and row["eof"] is True,
             "postseal_original_stream_join")
    session = strict(originals["session/session-clock.original.json"])
    template_raw = originals["session/session-template.original.json"]
    template = strict(template_raw, SESSION_LIMITS["session/session-template.original.json"])
    clock = origin["clock"]
    need(set(session) == {"schema", "context", "boot_id", "started_monotonic",
                         "deadline_monotonic", "template", "roster"}
         and session["schema"] == SESSION_SCHEMA and session["context"] == current
         and session["template"] == descriptor(template_raw) == clock["request"]
         and session["roster"] == list(ROSTER)
         and session["boot_id"] == boot_id == clock["boot_id"]
         and session["started_monotonic"] == clock["started_monotonic"]
         and session["deadline_monotonic"] == clock["deadline_monotonic"]
         and template.get("source_sha") == current["source_sha"]
         and template.get("semwright_sha") == current["semwright_sha"]
         and template.get("owner_resources") == resources.OWNER_RESOURCE_POLICY,
         "postseal_original_session_clock")
    start, deadline = session["started_monotonic"], session["deadline_monotonic"]
    check_time(observed_monotonic, start, deadline)
    need(type(template.get("limits", {}).get("attempt_seconds")) is int
         and 0 < template["limits"]["attempt_seconds"] <= 3900
         and deadline == start + template["limits"]["attempt_seconds"]
         and intent.get("deadline") == {"initial": clock, "selected": clock,
                                       "effective_deadline_monotonic": deadline},
         "postseal_original_guard_deadline")
    manifest = strict(originals["session/resource-session-manifest.original.json"], MAX_MANIFEST)
    need(manifest.get("schema") == "motionwright.native.fixedtxt-resource-custody-manifest/1"
         and manifest.get("context") == current and manifest.get("case_index") is None
         and manifest.get("active_action") == ACTION
         and manifest.get("active_guard_completion_claimed") is False
         and manifest.get("origin") == descriptor(originals["owner_resources/origin.original.json"])
         and manifest.get("failures") == [] and manifest.get("Native_outcome") == "NOT_INFERRED"
         and manifest.get("automatic_retry") is False and type(manifest.get("originals")) is list,
         "postseal_original_session_resource_manifest")
    rows = manifest["originals"]
    need(all(type(row) is dict and set(row) == {"path", "bytes", "sha256"} for row in rows)
         and len({row["path"] for row in rows}) == len(rows), "postseal_manifest_unique_originals")
    by_name = {row["path"]: {key: row[key] for key in ("bytes", "sha256")} for row in rows}
    for name in resources.PREFIX_FILES:
        path = "commands/" + ACTION + "/" + name
        need(by_name.get(path) == descriptor(originals["owner_resources/" + path]),
             "postseal_original_prefix_changed")
    public_raw = originals["session/session-public.json"]
    public = strict(public_raw)
    need(type(session_ciphertext) is bytes and 0 < len(session_ciphertext) <= MAX_SESSION_CIPHER
         and set(public) == {"schema", "context", "sealed_originals", "recipient_sha256",
                             "original_files", "native_acceptance", "G2_accepted", "F_accepted", "automatic_retry"}
         and canonical(public) == public_raw
         and public["schema"] == "motionwright.native.fixedtxt-session-public/1"
         and public["context"] == current and public["recipient_sha256"] == recipient_sha256
         and public["sealed_originals"] == descriptor(session_ciphertext)
         and type(public["original_files"]) is int and 0 < public["original_files"] <= 512
         and all(public[k] is False for k in ("native_acceptance", "G2_accepted", "F_accepted", "automatic_retry")),
         "postseal_original_cipher_public_binding")
    need(session_ciphertext.startswith(MAGIC) and len(session_ciphertext) > len(MAGIC) + 4,
         "postseal_original_cipher_header")
    offset = len(MAGIC)
    size = int.from_bytes(session_ciphertext[offset:offset + 4], "big")
    need(0 < size <= 8192 and offset + 4 + size + 16 < len(session_ciphertext),
         "postseal_original_cipher_header")
    aad = session_ciphertext[offset + 4:offset + 4 + size]
    header = strict(aad, 8192)
    need(set(header) == {"schema", "algorithm", "recipient_spki_sha256", "context", "nonce",
                         "wrapped_key", "plaintext_sha256", "plaintext_bytes"}
         and canonical(header) == aad and header["schema"] == SEAL_SCHEMA
         and header["algorithm"] == "RSA-OAEP-SHA256+AES-256-GCM"
         and header["context"] == current and header["recipient_spki_sha256"] == recipient_sha256
         and type(header["plaintext_bytes"]) is int and 0 < header["plaintext_bytes"] < MAX_SESSION_CIPHER
         and len(session_ciphertext) == offset + 4 + size + header["plaintext_bytes"] + 16,
         "postseal_original_cipher_header_binding")
    need(type(header["plaintext_sha256"]) is str
         and re.fullmatch(r"[0-9a-f]{64}", header["plaintext_sha256"]),
         "postseal_original_cipher_plaintext_digest")
    for name, length in (("nonce", 12), ("wrapped_key", 384)):
        value = header[name]
        need(type(value) is str and len(value) <= 512, "postseal_original_cipher_encoding")
        decoded = base64.b64decode(value, validate=True)
        need(len(decoded) == length and base64.b64encode(decoded).decode() == value,
             "postseal_original_cipher_encoding")
    return {"guard": guard, "session_ciphertext": descriptor(session_ciphertext),
            "session_public": descriptor(public_raw),
            "session_clock": descriptor(originals["session/session-clock.original.json"]),
            "session_resource_manifest": descriptor(originals["session/resource-session-manifest.original.json"]),
            "deadline_monotonic": deadline, "seal_session_guard_completion_observed": True,
            "session_ciphertext_AEAD_verified_by_observer": False,
            "all_session_cases_verified_by_observer": False,
            "observer_completion_claimed": False, "Native_outcome": "NOT_INFERRED",
            "native_acceptance": False, "G2_accepted": False, "F_accepted": False, "automatic_retry": False}


def pack_snapshot(originals, verification, observation):
    from native_fixedtxt_g2_admission import _pack_originals
    need(set(originals) == set(LIMITS), "postseal_pack_roster")
    manifest = canonical({"schema": SNAPSHOT_SCHEMA, "context": verification["guard"]["context"],
        "files": [{"path": name, **descriptor(raw)} for name, raw in sorted(originals.items())],
        "verification": verification, "observer": observation,
        "native_acceptance": False, "G2_accepted": False, "F_accepted": False, "automatic_retry": False})
    need(len(manifest) <= MAX_MANIFEST, "postseal_manifest_bound")
    plaintext = _pack_originals(originals, manifest)
    need(0 < len(plaintext) <= MAX_POSTSEAL - 8192 - MAX_JSON - 64,
         "postseal_existing_final_reserve")
    return plaintext


def source_bindings(handles, current, deadline):
    from native_fixedtxt_g2_admission import source_paths
    names = sorted(set(source_paths()) | {SELF, ".github/workflows/native-mlt-sequence-e2e.yml"})
    repo = handles.paths["source"]
    environment = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_GRAFT_FILE": os.devnull, "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    command = ["/usr/bin/git", "--no-replace-objects", "-C", repo,
               "-c", "core.fsmonitor=false", "-c", "safe.directory=" + repo]
    def git(*args):
        check_time(time.monotonic(), 0, deadline)
        value = subprocess.run([*command, *args], env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=min(15, deadline - time.monotonic()), check=False)
        need(value.returncode == 0 and len(value.stdout) <= MAX_JSON and len(value.stderr) <= MAX_JSON,
             "postseal_source_git_identity")
        return value.stdout
    need(git("rev-parse", "HEAD").decode().strip() == current["source_sha"],
         "postseal_source_head")
    blobs = {}
    for row in git("ls-tree", "-z", current["source_sha"], "--", *names).split(b"\0"):
        if not row:
            continue
        meta, name = row.decode("utf-8").split("\t", 1)
        mode, kind, oid = meta.split(" ")
        need(name not in blobs and kind == "blob" and mode in ("100644", "100755")
             and re.fullmatch(r"[0-9a-f]{40}", oid), "postseal_source_blob")
        blobs[name] = oid
    need(set(blobs) == set(names), "postseal_source_closed_roster")
    result, marks = {}, {}
    for name in names:
        raw, mark = handles.read("source", tuple(name.split("/")), 4 * MIB)
        need(mark[3] == os.geteuid() and stat.S_IMODE(mark[2]) & 0o022 == 0,
             "postseal_source_owner_mode")
        oid = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        need(oid == blobs[name], "postseal_source_original_blob_changed")
        result[name], marks[("source", name)] = descriptor(raw), mark
    return result, marks


def close_preserving_primary(reader):
    primary = sys.exc_info()[1]
    try:
        reader.close()
    except BaseException as error:
        if primary is None:
            raise
        primary.add_note("Postseal read-handle closure failed: " + type(error).__name__)


def unchanged(handles, marks):
    from native_evidence_projection import RootHandles, identity
    need(handles.stable_roots(), "postseal_original_roots_changed")
    fresh = RootHandles(handles.paths)
    try:
        need(all(identity(fresh.initial[name]) == identity(handles.initial[name])
                 for name in handles.paths), "postseal_original_root_rebound")
    finally:
        close_preserving_primary(fresh)
    for (root, name), mark in marks.items():
        need(handles.stat_member(root, tuple(name.split("/"))) == mark,
             "postseal_original_changed_after_capture")


def publish_closed_envelope(directory, envelope, public, *, uid, gid):
    """Hold the exclusive output directory through literal encrypted readback."""
    from ci_private_evidence import new_bytes
    from native_evidence_projection import RootHandles, identity
    need(type(envelope) is bytes and type(public) is bytes
         and 0 < len(envelope) and 0 < len(public) <= MAX_JSON
         and len(envelope) + len(public) <= MAX_POSTSEAL, "postseal_output_bound")
    directory.mkdir(mode=0o700, exist_ok=False)
    resources.private_directory(directory, uid)
    held = RootHandles({"sealed": str(directory)})
    primary = None
    try:
        values = {"sealed-postseal-originals.bin": envelope, "postseal-public.json": public}
        for name, raw in values.items():
            new_bytes(directory / name, raw)
        mark = identity(os.fstat(held.fds["sealed"]))
        for name, raw in values.items():
            actual, observed = held.read("sealed", (name,), MAX_POSTSEAL)
            need(actual == raw and observed[3:5] == (uid, gid)
                 and stat.S_IMODE(observed[2]) == 0o600, "postseal_output_literal_readback")
        fresh = RootHandles({"sealed": str(directory)})
        try:
            need(identity(fresh.initial["sealed"]) == mark
                 == identity(os.fstat(held.fds["sealed"])), "postseal_output_root_rebound")
        finally:
            close_preserving_primary(fresh)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            held.close()
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note("Postseal output-handle closure failed: " + type(error).__name__)


def capture_postseal():
    import resource
    from ci_private_evidence import encrypt_snapshot
    from native_evidence_projection import RootHandles
    import native_mlt_g2_custody as custody
    import native_fixedtxt_session as serial
    need(sys.platform == "linux" and len(sys.argv) == 1, "postseal_closed_linux_entry")
    origin, group, journal, origin_ref = resources.admitted("fixedtxt")
    need(origin["job"] == "fixedtxt-session-owner", "postseal_session_owner_only")
    current, uid, gid = origin["context"], origin["uid"], origin["gid"]
    temp, repo = Path(origin["base_temp"]), Path(origin["source_root"])
    root = temp / ("mw-fixedtxt-session-" + current["run_id"] + "-1")
    sealed = temp / ("mw-fixedtxt-session-custody-" + current["run_id"] + "-1")
    need(os.environ.get("MW_FIXEDTXT_SESSION_ROOT") == str(root)
         and os.environ.get("RUNNER_TEMP") == str(temp), "postseal_original_namespace")
    python = temp / ("mw-fixedtxt-custody-python-" + current["run_id"] + "-1") / "bin/python"
    need(os.environ.get("CUSTODY_PYTHON") == str(python)
         and Path(sys.executable) == python
         and python.resolve(strict=True) == Path("/usr/bin/python3.12").resolve(strict=True),
         "postseal_original_custody_python")
    deadline = origin["clock"]["deadline_monotonic"]
    started = time.monotonic()
    check_time(started, origin["clock"]["started_monotonic"], deadline)
    for kind, maximum in ((resource.RLIMIT_AS, resources.LIMIT), (resource.RLIMIT_FSIZE, MAX_POSTSEAL)):
        soft, hard = resource.getrlimit(kind)
        cap = min([maximum, *(n for n in (soft, hard) if n != resource.RLIM_INFINITY)])
        resource.setrlimit(kind, (cap, cap))
    def expired(_signal, _frame):
        raise PostsealRejected("postseal_original_deadline")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, deadline - time.monotonic())
    handles, primary = None, None
    try:
        need(resources.available_memory() >= resources.RESERVE, "postseal_physical_reserve")
        handles = RootHandles({"source": str(repo), "resources": str(journal),
                               "session": str(root), "sealed": str(sealed)})
        bindings, marks = source_bindings(handles, current, deadline)
        originals = {}
        locations = {}
        for name in LIMITS:
            if name.startswith("owner_resources/"):
                relative = name.removeprefix("owner_resources/").removeprefix("commands/")
                locations[name] = ("resources", relative)
            elif name == "session/session-public.json":
                locations[name] = ("sealed", "session-public.json")
            else:
                locations[name] = ("session", name.removeprefix("session/"))
        for name, (label, relative) in locations.items():
            raw, mark = handles.read(label, tuple(relative.split("/")), LIMITS[name])
            need(mark[3:5] == (uid, gid) and stat.S_IMODE(mark[2]) == 0o600,
                 "postseal_original_owner_mode")
            originals[name], marks[(label, relative)] = raw, mark
        need(descriptor(originals["owner_resources/origin.original.json"]) == origin_ref,
             "postseal_original_origin_changed")
        cipher, mark = handles.read("sealed", ("sealed-session-originals.bin",), MAX_SESSION_CIPHER)
        need(mark[3:5] == (uid, gid) and stat.S_IMODE(mark[2]) == 0o600,
             "postseal_original_cipher_owner_mode")
        marks[("sealed", "sealed-session-originals.bin")] = mark
        bootstrap = custody.runtime_preparation_dispatch(
            originals["session/runtime-bootstrap.original.json"], current, scenario="fixedtxt-v4-one-fixture")
        verification = validate_originals(originals, cipher, current=current,
            recipient_sha256=bootstrap["recipient"]["sha256"],
            helper_sha256=bindings["tooling/native_owner_resources.py"]["sha256"],
            observed_monotonic=time.monotonic(),
            boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        del cipher
        observed = resources.observe_tree(group, origin)
        resources.validate_kernel_snapshot(observed, origin)
        need(all(not row["populated"] and row["members"] == [] for row in observed["leaves"].values()),
             "postseal_observed_owned_tree_not_empty")
        observation = {"schema": "motionwright.native.fixedtxt-postseal-observation/1",
            "pid": os.getpid(), "uid": uid, "gid": gid, "started_monotonic": started,
            "observed_monotonic": time.monotonic(), "original_deadline_monotonic": deadline,
            "resource_tree_observation": observed, "source_bindings": bindings,
            "original_identities": {label + "/" + name: list(mark) for (label, name), mark in sorted(marks.items())},
            "observer_inside_native_resource_tree_claimed": False,
            "observer_completion_claimed": False, "Native_authority_granted": False, "automatic_retry": False}
        unchanged(handles, marks)
        plaintext = pack_snapshot(originals, verification, observation)
        envelope = encrypt_snapshot(plaintext, bootstrap["recipient"], current)
        del plaintext
        public = canonical({"schema": PUBLIC_SCHEMA, "context": current,
            "sealed_originals": descriptor(envelope), "recipient_sha256": bootstrap["recipient"]["sha256"],
            "original_files": len(originals), "session_ciphertext": verification["session_ciphertext"],
            "session_public": verification["session_public"],
            "seal_session_guard_completion_observed": True, "observer_completion_claimed": False,
            "native_acceptance": False, "G2_accepted": False, "F_accepted": False, "automatic_retry": False})
        need(len(public) <= MAX_JSON and len(envelope) + len(public) <= MAX_POSTSEAL,
             "postseal_existing_final_reserve")
        unchanged(handles, marks)
        need(resources.available_memory() >= resources.RESERVE, "postseal_physical_reserve")
        check_time(time.monotonic(), started, deadline)
        directory = temp / ("mw-fixedtxt-postseal-custody-" + current["run_id"] + "-1")
        publish_closed_envelope(directory, envelope, public, uid=uid, gid=gid)
        unchanged(handles, marks)
        check_time(time.monotonic(), started, deadline)
        serial.output(directory=str(directory))
        return {"status": "POSTSEAL_ORIGINALS_RETAINED", "context": current,
                "sealed_originals": descriptor(envelope), "native_calls": 0,
                "Native_outcome": "NOT_INFERRED", "G2_accepted": False, "automatic_retry": False}
    except BaseException as error:
        primary = error
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if handles is not None:
            try:
                handles.close()
            except BaseException as error:
                if primary is None:
                    raise
                primary.add_note("Postseal read-handle closure failed: " + type(error).__name__)


def main():
    try:
        print(json.dumps(capture_postseal(), sort_keys=True))
        return 0
    except BaseException as error:
        candidate = str(error)
        code = candidate if re.fullmatch(r"[a-z0-9_-]{1,120}", candidate) else "postseal_failed_closed"
        print(json.dumps({"status": "POSTSEAL_CUSTODY_INCOMPLETE", "error_code": code,
            "error_type": type(error).__name__, "seal_session_guard_completion_claimed": False,
            "native_calls": 0, "G2_accepted": False, "automatic_retry": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
